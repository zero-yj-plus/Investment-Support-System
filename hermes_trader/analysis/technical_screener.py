"""全市场技术选股缓存、筛选、方案和历史命中。

普通查询只读客户隔离 SQLite；逐股 K 线网络采集只允许盘后任务或显式刷新调用。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable

from ..data import free_source
from ..data import tdx_local_daily
from ..data import tencent_source
from ..data.base import now_cn, ts_str
from . import technical_signals

SCHEMA_VERSION = "technical-screener-v1"
CONFIG_SCHEMA_VERSION = 2
HISTORY_DAYS = 120
_db_path: Path | None = None
_refresh_lock = threading.Lock()
_query_cache: dict[str, tuple[float, dict]] = {}
_query_cache_lock = threading.Lock()


def init(data_dir: str | Path, *, reconcile_interrupted: bool = False) -> None:
    global _db_path
    _db_path = Path(data_dir) / "technical_screener.sqlite3"
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS bars(
          code TEXT NOT NULL,date TEXT NOT NULL,open REAL,high REAL,low REAL,close REAL,
          volume REAL,amount REAL,PRIMARY KEY(code,date));
        CREATE TABLE IF NOT EXISTS stocks(
          code TEXT PRIMARY KEY,name TEXT,board TEXT,industry TEXT,last_date TEXT,last_price REAL,
          change_pct REAL,last_amount REAL,avg_amount20 REAL,bar_count INTEGER,paused INTEGER NOT NULL DEFAULT 0,
          adjustment TEXT,source TEXT,updated_at TEXT,bars_hash TEXT,algorithm_ver TEXT);
        CREATE TABLE IF NOT EXISTS benchmark_bars(
          benchmark TEXT NOT NULL,date TEXT NOT NULL,close REAL NOT NULL,source TEXT,
          PRIMARY KEY(benchmark,date));
        CREATE TABLE IF NOT EXISTS signal_matches(
          code TEXT NOT NULL,timeframe TEXT NOT NULL,signal_code TEXT NOT NULL,signal_name TEXT NOT NULL,
          signal_kind TEXT NOT NULL,direction TEXT NOT NULL,event_date TEXT NOT NULL,params_json TEXT NOT NULL,
          metrics_json TEXT NOT NULL,algorithm_ver TEXT NOT NULL,is_confirmed INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(code,timeframe,signal_code,signal_kind,event_date,params_json,algorithm_ver));
        CREATE INDEX IF NOT EXISTS idx_signal_lookup ON signal_matches(signal_code,timeframe,signal_kind,event_date);
        CREATE INDEX IF NOT EXISTS idx_signal_code_date ON signal_matches(code,event_date DESC);
        CREATE TABLE IF NOT EXISTS schemes(
          id TEXT PRIMARY KEY,name TEXT NOT NULL,payload_json TEXT NOT NULL,algorithm_ver TEXT NOT NULL,
          created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
        """)
        stock_columns = {
            str(row[1]) for row in db.execute("PRAGMA table_info(stocks)").fetchall()
        }
        if "bars_hash" not in stock_columns:
            db.execute("ALTER TABLE stocks ADD COLUMN bars_hash TEXT")
        if "algorithm_ver" not in stock_columns:
            db.execute("ALTER TABLE stocks ADD COLUMN algorithm_ver TEXT")
        row = db.execute("SELECT value_json FROM meta WHERE key='refresh_state'").fetchone()
        try:
            refresh_state = json.loads(row[0]) if row else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            refresh_state = {}
        if (
            reconcile_interrupted
            and refresh_state.get("state") == "running"
            and not _refresh_lock.locked()
        ):
            refresh_state.update({
                "state": "interrupted",
                "finished_at": ts_str(),
                "error_code": "refresh_interrupted_by_restart",
                "error": "上次刷新随业务进程中断，已保留上次成功缓存",
            })
            db.execute(
                "UPDATE meta SET value_json=? WHERE key='refresh_state'",
                (_json(refresh_state),),
            )


def _connect() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("technical_screener_not_initialized")
    db = sqlite3.connect(_db_path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    return db


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _meta_get(key: str, default=None):
    with _connect() as db:
        row = db.execute("SELECT value_json FROM meta WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _meta_set(key: str, value) -> None:
    with _connect() as db:
        db.execute("INSERT INTO meta(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (key, _json(value)))


def status(*, include_counts: bool = True) -> dict:
    state = _meta_get("refresh_state", {}) or {}
    cached_counts = _meta_get("cache_counts", {}) or {}
    with _connect() as db:
        stocks = int(db.execute("SELECT COUNT(*) FROM stocks").fetchone()[0])
        # 大表精确数量只允许由独立 worker 在刷新完成时统计并物化；交互请求绝不
        # 扫描数百万行。旧库尚无物化值时返回 null，不能用猜测或 0 冒充。
        bars = cached_counts.get("bars") if include_counts else None
        matches = cached_counts.get("signal_matches") if include_counts else None
        last_date = db.execute("SELECT MAX(last_date) FROM stocks").fetchone()[0]
        benchmark_bars = int(db.execute("SELECT COUNT(*) FROM benchmark_bars WHERE benchmark='000300.SH'").fetchone()[0])
    refresh_state = state.get("state")
    current = (
        refresh_state
        if refresh_state in {"ready", "partial", "running"}
        else ("ready" if stocks else "missing")
    )
    return {
        "ok": current in {"ready", "partial", "running"}, "schema_version": SCHEMA_VERSION,
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "algorithm_ver": technical_signals.ALGORITHM_VERSION, "state": current,
        "stocks": stocks, "bars": bars, "signal_matches": matches, "data_date": last_date,
        "adjustment": "qfq", "history_days": HISTORY_DAYS, "benchmark": "000300.SH", "benchmark_bars": benchmark_bars,
        "level2_available": False,
        "level2_reason": "当前没有接入可核验的逐笔/Level-2 DDX 历史数据",
        "refresh": state,
    }


def refresh_running() -> bool:
    """只反映当前进程是否真的持有刷新锁，不受重启前遗留状态影响。"""
    return _refresh_lock.locked()


def catalog() -> dict:
    signals=technical_signals.signal_catalog(level2_available=False)
    benchmark_available=False
    try:
        with _connect() as db: benchmark_available=bool(db.execute("SELECT 1 FROM benchmark_bars WHERE benchmark='000300.SH' LIMIT 1").fetchone())
    except (RuntimeError,sqlite3.Error): pass
    for item in signals:
        if item["signal_code"]=="WIN_MARKET_N":
            item["available"]=benchmark_available
            item["unavailable_reason"]=None if benchmark_available else "沪深300基准历史尚未进入本地缓存"
    return {
        "ok": True, "algorithm_ver": technical_signals.ALGORITHM_VERSION,
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "timeframes": [{"code": "D", "name": "日线"}, {"code": "W", "name": "周线"}, {"code": "M", "name": "月线"}],
        "occurrences": [
            {"code": "TODAY", "name": "今日刚发生"}, {"code": "RECENT", "name": "近N个交易周期内发生"},
            {"code": "CURRENT", "name": "当前仍然成立"},
        ],
        "signals": signals,
    }


def _board(code: str) -> str:
    if code.startswith("30"): return "创业板"
    if code.startswith("68"): return "科创板"
    if code.startswith(("4", "8", "92")): return "北交所"
    return "主板"


def _bars_fingerprint(bars: list[dict]) -> str:
    digest = hashlib.sha256()
    for row in bars:
        digest.update(_json({
            "d": row.get("d") or row.get("date"),
            "o": row.get("o", row.get("open")),
            "h": row.get("h", row.get("high")),
            "l": row.get("l", row.get("low")),
            "c": row.get("c", row.get("close")),
            "v": row.get("v_shares", row.get("volume")),
            "a": row.get("amount"),
            "adjustment": row.get("adjustment"),
        }).encode("utf-8"))
    return digest.hexdigest()


def _cached_stock_state(code: str, bars_hash: str) -> tuple[bool, bool]:
    """返回（K线相同，K线及算法均为当前版本）。"""
    try:
        with _connect() as db:
            row = db.execute(
                "SELECT bars_hash,algorithm_ver FROM stocks WHERE code=?", (code,)
            ).fetchone()
    except (RuntimeError, sqlite3.Error):
        return False, False
    if row is None:
        return False, False
    bars_same = bool(bars_hash and str(row["bars_hash"] or "") == bars_hash)
    return bars_same, bars_same and str(row["algorithm_ver"] or "") == technical_signals.ALGORITHM_VERSION


def _store_stock(
    code: str,
    name: str,
    bars: list[dict],
    matches: list[dict],
    *,
    bars_hash: str,
    replace_bars: bool = True,
) -> None:
    frame = technical_signals.normalized_frame(bars)
    if frame.empty:
        return
    last = frame.iloc[-1]
    previous = frame.iloc[-2].close if len(frame) > 1 else None
    change = (last.close / previous - 1) * 100 if previous not in (None, 0) else None
    adjustment = str((bars[-1] or {}).get("adjustment") or "qfq")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if replace_bars:
            db.execute("DELETE FROM bars WHERE code=?", (code,))
            db.executemany(
                "INSERT INTO bars(code,date,open,high,low,close,volume,amount) VALUES(?,?,?,?,?,?,?,?)",
                [(code, row.date.date().isoformat(), row.open, row.high, row.low, row.close,
                  None if row.volume != row.volume else row.volume, None if row.amount != row.amount else row.amount)
                 for row in frame.itertuples(index=False)],
            )
        db.execute("DELETE FROM signal_matches WHERE code=? AND signal_kind='state' AND algorithm_ver=?", (code, technical_signals.ALGORITHM_VERSION))
        db.executemany(
            "INSERT OR IGNORE INTO signal_matches(code,timeframe,signal_code,signal_name,signal_kind,direction,event_date,params_json,metrics_json,algorithm_ver,is_confirmed) VALUES(?,?,?,?,?,?,?,?,?,?,1)",
            [(code, row["timeframe"], row["signal_code"], row["signal_name"], row["signal_kind"], row["direction"], row["date"], _json(row["params"]), _json(row["metrics"]), row["algorithm_ver"]) for row in matches],
        )
        db.execute("""INSERT INTO stocks(code,name,board,industry,last_date,last_price,change_pct,last_amount,avg_amount20,bar_count,paused,adjustment,source,updated_at,bars_hash,algorithm_ver)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,board=excluded.board,last_date=excluded.last_date,last_price=excluded.last_price,change_pct=excluded.change_pct,last_amount=excluded.last_amount,avg_amount20=excluded.avg_amount20,bar_count=excluded.bar_count,paused=excluded.paused,adjustment=excluded.adjustment,source=excluded.source,updated_at=excluded.updated_at,bars_hash=excluded.bars_hash,algorithm_ver=excluded.algorithm_ver""",
          (code, name or None, _board(code), None, last.date.date().isoformat(), float(last.close), change,
           None if last.amount != last.amount else float(last.amount),
           float(frame.amount.tail(20).mean()) if frame.amount.tail(20).notna().any() else None,
           len(frame), int((last.volume or 0) <= 0), adjustment, str((bars[-1] or {}).get("source") or "腾讯财经·前复权日K"), ts_str(),
           bars_hash, technical_signals.ALGORITHM_VERSION))
        db.commit()


def _fetch_compute(
    item: tuple[str, str], bars: list[dict] | None = None,
) -> tuple[str, str, list[dict], list[dict], str | None, str, str, bool, bool]:
    code, name = item
    try:
        source = "tdx_day"
        if not bars or len(bars) < 20:
            fallback = tencent_source.stock_daily_bars_uncached(code, 520, adjust="qfq")
            if fallback:
                bars = fallback
                source = "tencent_fallback"
        bars = bars or []
        if len(bars) < 20:
            return code, name, bars, [], f"有效日K不足20根（{len(bars)}）", source, "", False, False
        bars_hash = _bars_fingerprint(bars)
        bars_same, fully_current = _cached_stock_state(code, bars_hash)
        if fully_current:
            return code, name, bars, [], None, source, bars_hash, bars_same, True
        daily = technical_signals.normalized_frame(bars)
        matches = []
        for tf in ("D", "W", "M"):
            frame = technical_signals.resample_frame(daily, tf, include_partial=False)
            matches.extend(technical_signals.default_signal_matches(frame, tf, history_days=HISTORY_DAYS))
        return code, name, bars, matches, None, source, bars_hash, bars_same, False
    except Exception as exc:  # noqa: BLE001 - 单股失败必须降级且继续
        return code, name, [], [], str(exc)[:160], source, "", False, False


def _refresh_benchmark() -> int:
    local = tdx_local_daily.daily_bars_batch(["000300.SH"], 520)
    rows = local.get("000300") or []
    if not rows:
        rows = tencent_source.stock_daily_bars_uncached("sh000300", 520, adjust="none") or []
    clean=[]
    for row in rows:
        try: clean.append(("000300.SH",str(row.get("d") or row.get("date"))[:10],float(row.get("c",row.get("close"))),str(row.get("source") or "腾讯沪深300日K")))
        except (TypeError,ValueError): continue
    if clean:
        with _connect() as db:
            db.execute("DELETE FROM benchmark_bars WHERE benchmark=?",("000300.SH",)); db.executemany("INSERT INTO benchmark_bars(benchmark,date,close,source) VALUES(?,?,?,?)",clean)
    return len(clean)


def refresh_all(
    *,
    max_symbols: int | None = None,
    workers: int = 4,
    resume: dict[str, Any] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict:
    if not _refresh_lock.acquire(blocking=False):
        return {"ok": False, "state": "running", "error_code": "refresh_already_running"}
    started = time.monotonic()
    resume = dict(resume or {})
    started_at = str(resume.get("started_at") or ts_str())
    try:
        processed = max(0, int(resume.get("processed") or 0))
        succeeded = max(0, int(resume.get("succeeded") or 0))
        failed = max(0, int(resume.get("failed") or 0))
        skipped = max(0, int(resume.get("skipped") or 0))
        cursor = str(resume.get("cursor") or "")
        source_counts = dict(resume.get("source_counts") or {"tdx_day": 0, "tencent_fallback": 0})
        previous_stages = resume.get("stage_seconds") if isinstance(resume.get("stage_seconds"), dict) else {}
        stage_seconds = {
            key: float(previous_stages.get(key) or 0.0)
            for key in ("benchmark", "universe", "local_bars", "compute_write", "final_counts")
        }
        _meta_set("refresh_state", {
            "state": "running", "started_at": started_at, "processed": processed,
            "succeeded": succeeded, "failed": failed, "skipped": skipped,
            "cursor": cursor or None, "resumed": bool(cursor), "source_counts": source_counts,
            "stage_seconds": stage_seconds,
        })
        stage_started = time.monotonic()
        benchmark_bars = _refresh_benchmark()
        stage_seconds["benchmark"] += time.monotonic() - stage_started
        stage_started = time.monotonic()
        universe = free_source.stock_universe() or {}
        items = sorted((str(code).zfill(6), str(name or "")) for code, name in universe.items())
        stage_seconds["universe"] += time.monotonic() - stage_started
        if max_symbols is not None:
            items = items[:max(1, int(max_symbols))]
        expected = len(items)
        if cursor:
            items = [item for item in items if item[0] > cursor]
        samples = []
        worker_count = max(1, min(int(workers), 12))
        max_pending = worker_count * 2
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for offset in range(0, len(items), max_pending):
                batch = items[offset : offset + max_pending]
                stage_started = time.monotonic()
                try:
                    local_bars = tdx_local_daily.daily_bars_batch(
                        [code for code, _name in batch], 520,
                    )
                except Exception:  # noqa: BLE001 - 本地日线失败时逐股走无缓存降级
                    local_bars = {}
                stage_seconds["local_bars"] += time.monotonic() - stage_started
                stage_started = time.monotonic()
                futures = {
                    executor.submit(_fetch_compute, item, local_bars.get(item[0])): item[0]
                    for item in batch
                }
                for future in as_completed(futures):
                    code, name, bars, matches, error, source, bars_hash, bars_same, unchanged = future.result()
                    source_counts[source] = source_counts.get(source, 0) + 1
                    if bars:
                        if unchanged:
                            skipped += 1
                        else:
                            _store_stock(
                                code, name, bars, matches, bars_hash=bars_hash,
                                replace_bars=not bars_same,
                            )
                        succeeded += 1
                    else:
                        failed += 1
                    processed += 1
                    if error and len(samples) < 20: samples.append({"code": code, "reason": error})
                stage_seconds["compute_write"] += time.monotonic() - stage_started
                cursor = batch[-1][0]
                progress = {
                    "state": "running", "started_at": started_at, "processed": processed,
                    "total": expected, "succeeded": succeeded, "failed": failed,
                    "skipped": skipped, "cursor": cursor, "source_counts": source_counts,
                    "stage_seconds": {
                        key: round(value, 2) for key, value in stage_seconds.items()
                    },
                }
                _meta_set("refresh_state", progress)
                if progress_callback is not None:
                    try:
                        progress_callback(dict(progress))
                    except Exception:
                        # 队列心跳暂时失败不应破坏已提交的技术缓存；租约协程会继续续期。
                        pass
        ratio = succeeded / expected if expected else 0
        state = "ready" if ratio >= .8 else ("partial" if succeeded else "missing")
        stage_started = time.monotonic()
        with _connect() as db:
            cache_counts = {
                "bars": int(db.execute("SELECT COUNT(*) FROM bars").fetchone()[0]),
                "signal_matches": int(db.execute("SELECT COUNT(*) FROM signal_matches").fetchone()[0]),
                "counted_at": ts_str(),
            }
        stage_seconds["final_counts"] += time.monotonic() - stage_started
        _meta_set("cache_counts", cache_counts)
        result = {
            "ok": state == "ready", "state": state, "started_at": started_at,
            "finished_at": ts_str(), "processed": processed, "total": expected,
            "succeeded": succeeded, "failed": failed, "skipped": skipped,
            "cursor": cursor or None, "coverage_ratio": round(ratio, 4),
            "benchmark": "000300.SH", "benchmark_bars": benchmark_bars,
            "source_counts": source_counts, "cache_counts": cache_counts,
            "stage_seconds": {
                key: round(value, 2) for key, value in stage_seconds.items()
            },
            "max_pending": max_pending, "failure_samples": samples,
            "duration_seconds": round(time.monotonic()-started, 2),
        }
        _meta_set("refresh_state", result)
        with _query_cache_lock: _query_cache.clear()
        return result
    except Exception as exc:  # noqa: BLE001 - 刷新失败必须结束 running 状态并保留 last-good
        try:
            with _connect() as db: cached=int(db.execute("SELECT COUNT(*) FROM stocks").fetchone()[0])
        except Exception: cached=0
        result={"ok":False,"state":"partial" if cached else "missing","started_at":started_at,"finished_at":ts_str(),"error_code":"technical_refresh_failed","error":"全市场技术信号刷新失败，已保留上次成功缓存","cached_stocks":cached,"duration_seconds":round(time.monotonic()-started,2)}
        _meta_set("refresh_state",result)
        return result
    finally:
        _refresh_lock.release()


def _flatten_conditions(items: list, group: str, errors: list[dict], *, depth: int = 0):
    for index,node in enumerate(items):
        if not isinstance(node,dict):
            errors.append({"code":"invalid_condition","group":group,"index":index,"message":"条件必须是对象"}); continue
        if isinstance(node.get("conditions"),list):
            if depth>=1:
                errors.append({"code":"nested_group_too_deep","group":group,"index":index,"message":"条件组最多支持两层"}); continue
            operator=str(node.get("operator") or "AND").upper()
            if operator not in {"AND","OR"}: errors.append({"code":"invalid_operator","group":group,"index":index,"message":"子组运算符必须为AND或OR"})
            if not node["conditions"]: errors.append({"code":"empty_nested_group","group":group,"index":index,"message":"子条件组不能为空"})
            yield from _flatten_conditions(node["conditions"],f"{group}.{index}",errors,depth=depth+1)
        else:
            yield index,node


def _is_v2(payload: dict) -> bool:
    return payload.get("schemaVersion") == CONFIG_SCHEMA_VERSION or "satisfy" in payload


def _condition_copy(condition: dict, *, keep_id: bool = False) -> dict:
    """只复制会影响筛选语义的字段；编辑器 ID 不进入缓存键。"""
    fields = (
        "signal_code", "timeframe", "occurrence", "recent_periods",
        "include_partial_period", "params",
    )
    clean = {key: condition[key] for key in fields if key in condition}
    if keep_id and condition.get("id"):
        clean["id"] = str(condition["id"])
    return clean


def _new_migrated_group(prefix: str, index: int, operator: str, conditions: list[dict]) -> dict:
    condition_match = {"AND": "ALL", "OR": "ANY"}.get(str(operator).upper(), str(operator).upper())
    return {
        "id": f"{prefix}_{index}",
        "conditionMatch": condition_match,
        "conditions": [
            {"id": f"{prefix}_{index}_c_{child_index}", **_condition_copy(condition)}
            for child_index, condition in enumerate(conditions, 1)
        ],
    }


def migrate_legacy_config(payload: dict) -> dict:
    """把可表达的 v1 编辑配置无损迁移到固定两层结构。

    不可表达时返回原配置的只读标记；调用方仍可用旧执行器语义筛选。
    """
    if _is_v2(payload):
        return {"ok": True, "config": payload, "migrated": False}
    must_items = payload.get("must_all") or []
    any_items = payload.get("any_of") or []
    exclude_items = payload.get("exclude") or []
    if not all(isinstance(items, list) for items in (must_items, any_items, exclude_items)):
        return {"ok": False, "reason": "旧方案条件入口不是数组"}

    satisfy_groups: list[dict] = []
    must_leaves = [item for item in must_items if isinstance(item, dict) and not isinstance(item.get("conditions"), list)]
    must_nodes = [item for item in must_items if isinstance(item, dict) and isinstance(item.get("conditions"), list)]
    if len(must_leaves) + len(must_nodes) != len(must_items):
        return {"ok": False, "reason": "旧方案包含无效条件节点"}
    if must_leaves:
        satisfy_groups.append(_new_migrated_group("sg", len(satisfy_groups) + 1, "ALL", must_leaves))
    for node in must_nodes:
        operator = str(node.get("operator") or "AND").upper()
        conditions = node.get("conditions") or []
        if operator not in {"AND", "OR"} or not conditions or any(isinstance(item.get("conditions"), list) for item in conditions if isinstance(item, dict)):
            return {"ok": False, "reason": "旧方案包含超出两层的必须条件组"}
        satisfy_groups.append(_new_migrated_group("sg", len(satisfy_groups) + 1, operator, conditions))

    group_match = "ALL"
    if any_items:
        any_leaves = [item for item in any_items if isinstance(item, dict) and not isinstance(item.get("conditions"), list)]
        any_nodes = [item for item in any_items if isinstance(item, dict) and isinstance(item.get("conditions"), list)]
        if len(any_leaves) + len(any_nodes) != len(any_items):
            return {"ok": False, "reason": "旧方案包含无效任选条件节点"}
        flattenable = all(str(node.get("operator") or "AND").upper() == "OR" for node in any_nodes)
        if not must_items:
            group_match = "ANY"
            for leaf in any_leaves:
                satisfy_groups.append(_new_migrated_group("sg", len(satisfy_groups) + 1, "ALL", [leaf]))
            for node in any_nodes:
                conditions = node.get("conditions") or []
                if not conditions or any(isinstance(item.get("conditions"), list) for item in conditions if isinstance(item, dict)):
                    return {"ok": False, "reason": "旧方案包含超出两层的任选条件组"}
                satisfy_groups.append(_new_migrated_group("sg", len(satisfy_groups) + 1, str(node.get("operator") or "AND").upper(), conditions))
        elif not any_nodes or flattenable:
            conditions = list(any_leaves)
            for node in any_nodes:
                conditions.extend(node.get("conditions") or [])
            satisfy_groups.append(_new_migrated_group("sg", len(satisfy_groups) + 1, "ANY", conditions))
        elif len(any_items) == 1 and any_nodes:
            node = any_nodes[0]
            conditions = node.get("conditions") or []
            if not conditions or any(isinstance(item.get("conditions"), list) for item in conditions if isinstance(item, dict)):
                return {"ok": False, "reason": "旧方案包含超出两层的任选条件组"}
            satisfy_groups.append(_new_migrated_group("sg", len(satisfy_groups) + 1, str(node.get("operator") or "AND").upper(), conditions))
        else:
            return {"ok": False, "reason": "必须条件与多个复合任选组无法无损压缩到固定两层"}

    exclude_groups: list[dict] = []
    exclude_leaves = [item for item in exclude_items if isinstance(item, dict) and not isinstance(item.get("conditions"), list)]
    exclude_nodes = [item for item in exclude_items if isinstance(item, dict) and isinstance(item.get("conditions"), list)]
    if len(exclude_leaves) + len(exclude_nodes) != len(exclude_items):
        return {"ok": False, "reason": "旧方案包含无效排除条件节点"}
    if exclude_leaves:
        exclude_groups.append(_new_migrated_group("eg", len(exclude_groups) + 1, "ANY", exclude_leaves))
    for node in exclude_nodes:
        operator = str(node.get("operator") or "AND").upper()
        conditions = node.get("conditions") or []
        if operator not in {"AND", "OR"} or not conditions or any(isinstance(item.get("conditions"), list) for item in conditions if isinstance(item, dict)):
            return {"ok": False, "reason": "旧方案包含超出两层的排除条件组"}
        exclude_groups.append(_new_migrated_group("eg", len(exclude_groups) + 1, operator, conditions))

    passthrough = {
        key: value for key, value in payload.items()
        if key not in {"must_all", "any_of", "exclude", "schemaVersion", "editor_config", "editable", "migration_state", "migration_reason"}
    }
    config = {
        **passthrough,
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "satisfy": {"groupMatch": group_match, "groups": satisfy_groups},
        "exclude": {"groups": exclude_groups},
    }
    return {"ok": True, "config": config, "migrated": True}


def _validate_legacy_request(payload: dict) -> dict:
    errors=[]; groups=("must_all","any_of","exclude")
    for group in groups:
        value=payload.get(group,[])
        if not isinstance(value,list):
            errors.append({"code":"invalid_group","group":group,"message":"条件组必须是数组"}); continue
        seen=set()
        for index,condition in _flatten_conditions(value,group,errors):
            code=str((condition or {}).get("signal_code") or "")
            definition=technical_signals.DEFINITIONS.get(code)
            if not definition: errors.append({"code":"unknown_signal","group":group,"index":index,"message":f"未知信号：{code}"}); continue
            if definition.requires=="level2": errors.append({"code":"data_source_unavailable","group":group,"index":index,"message":f"{definition.signal_name}需要Level-2数据源"})
            if definition.requires=="benchmark":
                try:
                    with _connect() as db: available=bool(db.execute("SELECT 1 FROM benchmark_bars WHERE benchmark='000300.SH' LIMIT 1").fetchone())
                except (RuntimeError,sqlite3.Error): available=False
                if not available: errors.append({"code":"data_source_unavailable","group":group,"index":index,"message":"沪深300基准历史尚未进入本地缓存"})
            tf=str(condition.get("timeframe") or payload.get("default_timeframe") or "D")
            if tf not in {"D","W","M"}: errors.append({"code":"invalid_timeframe","group":group,"index":index,"message":"周期必须为D/W/M"})
            occurrence=str(condition.get("occurrence") or ("CURRENT" if definition.signal_kind=="state" else "TODAY"))
            if occurrence=="CURRENT" and definition.signal_kind!="state": errors.append({"code":"event_state_mismatch","group":group,"index":index,"message":"事件型信号不能解释为当前持续成立"})
            if occurrence!="CURRENT" and definition.signal_kind=="state": errors.append({"code":"event_state_mismatch","group":group,"index":index,"message":"状态型信号必须使用当前成立"})
            if occurrence=="RECENT":
                try: recent=int(condition.get("recent_periods") or 5)
                except (TypeError,ValueError): recent=0
                if not 1<=recent<=60: errors.append({"code":"invalid_recent_periods","group":group,"index":index,"message":"近N周期必须在1—60之间"})
            params=condition.get("params") or {}
            if not isinstance(params,dict): errors.append({"code":"invalid_params","group":group,"index":index,"message":"params必须是对象"}); params={}
            allowed_params=set(dict(definition.defaults))
            positive={"period","volume_period","multiplier","days","min_history","fast","slow","signal","ratio","body_pct","range_pct","change_pct","adx_threshold","rsi_period","stoch_period","smooth_k","smooth_d","trix_period","signal_period","short_period","long_period","channel_period","average_period","smooth_period","stddev","step","maximum"}
            integer_params={"period","volume_period","days","min_history","fast","slow","signal","rsi_period","stoch_period","smooth_k","smooth_d","trix_period","signal_period","short_period","long_period","channel_period","average_period","smooth_period"}
            for key,value in params.items():
                if key not in allowed_params:
                    errors.append({"code":"unknown_param","group":group,"index":index,"field":str(key),"message":f"{definition.signal_name}不支持参数{key}"}); continue
                try: number=float(value)
                except (TypeError,ValueError): number=float("nan")
                if not math.isfinite(number) or (key in positive and number<=0): errors.append({"code":"invalid_param","group":group,"index":index,"field":str(key),"message":f"参数{key}必须是有效正数"})
                elif key in integer_params and not number.is_integer(): errors.append({"code":"invalid_param","group":group,"index":index,"field":str(key),"message":f"参数{key}必须是整数"})
            if all(key in params for key in ("fast","slow")):
                try: invalid_order=float(params["fast"])>=float(params["slow"])
                except (TypeError,ValueError): invalid_order=False
                if invalid_order: errors.append({"code":"invalid_param","group":group,"index":index,"field":"fast","message":"MACD快线必须小于慢线"})
            try: invalid_dma=all(key in params for key in ("short_period","long_period")) and float(params["short_period"])>=float(params["long_period"])
            except (TypeError,ValueError): invalid_dma=False
            if invalid_dma: errors.append({"code":"invalid_param","group":group,"index":index,"field":"short_period","message":"短周期必须小于长周期"})
            try: invalid_sar=all(key in params for key in ("step","maximum")) and float(params["step"])>float(params["maximum"])
            except (TypeError,ValueError): invalid_sar=False
            if invalid_sar: errors.append({"code":"invalid_param","group":group,"index":index,"field":"step","message":"SAR步长不能大于最大加速因子"})
            key=(code,tf)
            if key in seen: errors.append({"code":"duplicate_condition","group":group,"index":index,"message":"同组内存在重复条件"})
            seen.add(key)
    must_items = payload.get("must_all", [])
    exclude_items = payload.get("exclude", [])
    must={(c.get("signal_code"),c.get("timeframe","D")) for _,c in _flatten_conditions(must_items if isinstance(must_items,list) else [],"must_all",[])}
    excluded={(c.get("signal_code"),c.get("timeframe","D")) for _,c in _flatten_conditions(exclude_items if isinstance(exclude_items,list) else [],"exclude",[])}
    for conflict in sorted(must&excluded): errors.append({"code":"contradictory_condition","message":f"同一条件同时要求并排除：{conflict[0]}"})
    opposites=[("MACD_GOLDEN_CROSS","MACD_DEAD_CROSS"),("LONG_MA_ARRAY","SHORT_MA_ARRAY")]
    must_codes={item[0] for item in must}
    for a,b in opposites:
        if a in must_codes and b in must_codes: errors.append({"code":"contradictory_condition","message":f"条件互相矛盾：{a} / {b}"})
    return {"ok":not errors,"errors":errors}


def _validate_v2_request(payload: dict) -> dict:
    errors: list[dict] = []
    if payload.get("schemaVersion") != CONFIG_SCHEMA_VERSION:
        errors.append({"code": "invalid_schema_version", "message": "规则配置版本必须为2"})
    satisfy = payload.get("satisfy")
    exclude = payload.get("exclude")
    if not isinstance(satisfy, dict):
        errors.append({"code": "invalid_satisfy", "group": "satisfy", "message": "满足规则必须是对象"})
        satisfy = {}
    if not isinstance(exclude, dict):
        errors.append({"code": "invalid_exclude", "group": "exclude", "message": "排除规则必须是对象"})
        exclude = {}
    group_match = str(satisfy.get("groupMatch") or "ALL").upper()
    if group_match not in {"ALL", "ANY"}:
        errors.append({"code": "invalid_group_match", "group": "satisfy", "message": "满足规则组关系必须为ALL或ANY"})
    satisfy_groups = satisfy.get("groups", [])
    exclude_groups = exclude.get("groups", [])
    if not isinstance(satisfy_groups, list):
        errors.append({"code": "invalid_group", "group": "satisfy", "message": "满足规则组必须是数组"})
        satisfy_groups = []
    if not isinstance(exclude_groups, list):
        errors.append({"code": "invalid_group", "group": "exclude", "message": "排除规则组必须是数组"})
        exclude_groups = []
    if not satisfy_groups:
        errors.append({"code": "missing_satisfy_group", "group": "satisfy", "message": "请至少添加一组满足规则"})

    validated_groups: list[tuple[str, int, dict]] = []
    for kind, groups in (("satisfy", satisfy_groups), ("exclude", exclude_groups)):
        seen_group_ids: set[str] = set()
        for index, group in enumerate(groups):
            path = f"{kind}.groups.{index}"
            if not isinstance(group, dict):
                errors.append({"code": "invalid_rule_group", "group": path, "message": "规则组必须是对象"})
                continue
            group_id = str(group.get("id") or "")
            if group_id and group_id in seen_group_ids:
                errors.append({"code": "duplicate_group_id", "group": path, "message": "规则组ID不能重复"})
            if group_id:
                seen_group_ids.add(group_id)
            condition_match = str(group.get("conditionMatch") or ("ANY" if kind == "exclude" else "ALL")).upper()
            if condition_match not in {"ALL", "ANY"}:
                errors.append({"code": "invalid_condition_match", "group": path, "message": "组内关系必须为ALL或ANY"})
            conditions = group.get("conditions")
            if not isinstance(conditions, list):
                errors.append({"code": "invalid_condition_group", "group": path, "message": "组内条件必须是数组"})
                continue
            if not conditions:
                errors.append({"code": "empty_rule_group", "group": path, "message": "每个规则组至少需要一个技术信号"})
                continue
            if any(isinstance(condition, dict) and isinstance(condition.get("conditions"), list) for condition in conditions):
                errors.append({"code": "nested_group_too_deep", "group": path, "message": "规则组内不能继续嵌套规则组"})
                continue
            validated_groups.append((kind, index, group))
            legacy_key = "exclude" if kind == "exclude" else "must_all"
            checked = _validate_legacy_request({
                legacy_key: conditions,
                "default_timeframe": payload.get("default_timeframe"),
            })
            for error in checked["errors"]:
                remapped = dict(error)
                remapped["group"] = path
                errors.append(remapped)

    satisfy_conditions = [
        condition for kind, _, group in validated_groups if kind == "satisfy"
        for condition in group.get("conditions", []) if isinstance(condition, dict)
    ]
    exclude_conditions = [
        condition for kind, _, group in validated_groups if kind == "exclude"
        for condition in group.get("conditions", []) if isinstance(condition, dict)
    ]
    required = {(row.get("signal_code"), row.get("timeframe") or payload.get("default_timeframe") or "D") for row in satisfy_conditions}
    excluded = {(row.get("signal_code"), row.get("timeframe") or payload.get("default_timeframe") or "D") for row in exclude_conditions}
    for signal_code, _ in sorted(required & excluded):
        errors.append({"code": "contradictory_condition", "message": f"同一条件同时用于满足和排除：{signal_code}"})
    return {"ok": not errors, "errors": errors}


def validate_request(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"ok": False, "errors": [{"code": "invalid_payload", "message": "筛选请求必须是对象"}]}
    return _validate_v2_request(payload) if _is_v2(payload) else _validate_legacy_request(payload)


def _params(condition: dict) -> dict:
    definition=technical_signals.DEFINITIONS[str(condition.get("signal_code") or "")]
    defaults=dict(definition.defaults); out=dict(defaults); out.update(condition.get("params") or {})
    for key,default in defaults.items():
        value=out.get(key)
        try:
            if isinstance(default,int) and not isinstance(default,bool) and float(value).is_integer(): out[key]=int(float(value))
            elif isinstance(default,float): out[key]=float(value)
        except (TypeError,ValueError): pass
    return out


def _is_default(condition: dict) -> bool:
    definition=technical_signals.DEFINITIONS[str(condition.get("signal_code") or "")]
    return definition.requires != "benchmark" and not bool(condition.get("include_partial_period")) and _params(condition)==dict(definition.defaults)


def _default_matches(condition: dict) -> tuple[set[str], dict[str, dict]]:
    code=str(condition["signal_code"]); tf=str(condition.get("timeframe") or "D"); definition=technical_signals.DEFINITIONS[code]
    occurrence=str(condition.get("occurrence") or ("CURRENT" if definition.signal_kind=="state" else "TODAY")); params_json=_json(_params(condition))
    sql="SELECT code,event_date,metrics_json FROM signal_matches WHERE signal_code=? AND timeframe=? AND signal_kind=? AND params_json=? AND algorithm_ver=?"; args=[code,tf,definition.signal_kind,params_json,technical_signals.ALGORITHM_VERSION]
    with _connect() as db:
        if occurrence=="TODAY":
            latest=db.execute(
                "SELECT MAX(event_date) FROM signal_matches WHERE signal_code=? AND timeframe=? AND signal_kind=? AND params_json=? AND algorithm_ver=?",
                (code,tf,definition.signal_kind,params_json,technical_signals.ALGORITHM_VERSION),
            ).fetchone()[0]
            if not latest:return set(),{}
            sql+=" AND event_date=?"; args.append(latest)
        elif occurrence=="RECENT":
            n=max(1,min(int(condition.get("recent_periods") or 5),60))
            raw_dates=[str(r[0]) for r in db.execute("SELECT DISTINCT date FROM bars ORDER BY date DESC LIMIT 500").fetchall()]
            if not raw_dates:return set(),{}
            if tf=="D": period_dates=raw_dates[:n]
            else:
                keys=[]; period_dates=[]
                for value in raw_dates:
                    stamp=date.fromisoformat(value)
                    key=(stamp.isocalendar().year,stamp.isocalendar().week) if tf=="W" else (stamp.year,stamp.month)
                    if key not in keys: keys.append(key)
                    if len(keys)>n: break
                    period_dates.append(value)
            cutoff=min(period_dates) if period_dates else raw_dates[0]
            sql+=" AND event_date>=?"; args.append(cutoff)
        rows=db.execute(sql,args).fetchall()
    reasons={}
    for row in rows:
        try: metrics=json.loads(row["metrics_json"])
        except (TypeError,ValueError,json.JSONDecodeError): metrics={}
        reasons[str(row["code"]) ]={"signal_code":code,"signal_name":definition.signal_name,"timeframe":tf,"date":row["event_date"],"metrics":metrics,"is_confirmed":True}
    return set(reasons),reasons


def _custom_matches(condition: dict, eligible: set[str]) -> tuple[set[str], dict[str, dict]]:
    definition=technical_signals.DEFINITIONS[str(condition["signal_code"])]
    tf=str(condition.get("timeframe") or "D"); occurrence=str(condition.get("occurrence") or ("CURRENT" if definition.signal_kind=="state" else "TODAY")); n=max(1,min(int(condition.get("recent_periods") or 5),60)); matched=set(); reasons={}
    codes=sorted(eligible)
    with _connect() as db:
        benchmark={row["date"]:float(row["close"]) for row in db.execute("SELECT date,close FROM benchmark_bars WHERE benchmark='000300.SH' ORDER BY date").fetchall()} if condition["signal_code"]=="WIN_MARKET_N" else {}
        for start in range(0,len(codes),800):
            chunk=codes[start:start+800]; rows=db.execute(f"SELECT * FROM bars WHERE code IN ({','.join('?' for _ in chunk)}) ORDER BY code,date",chunk).fetchall(); by={}
            for row in rows: by.setdefault(row["code"],[]).append(dict(row))
            for code,bars in by.items():
                partial=bool(condition.get("include_partial_period")) and tf in {"W","M"}
                frame=technical_signals.resample_frame(technical_signals.normalized_frame(bars),tf,include_partial=partial)
                if condition["signal_code"]=="WIN_MARKET_N":
                    period=int(_params(condition).get("period",20)); stock_ret=(frame.close/frame.close.shift(period)-1)*100; bench_close=frame.date.map(lambda stamp:benchmark.get(stamp.date().isoformat())); bench_ret=(bench_close/bench_close.shift(period)-1)*100; outperform=stock_ret-bench_ret
                    result={"mask":outperform>0,"metrics":{"stock_return_pct":stock_ret,"benchmark_return_pct":bench_ret,"outperform_pct":outperform}}
                else:
                    result=technical_signals.evaluate_condition(frame,{"signal_code":condition["signal_code"],"params":_params(condition)})
                mask=result["mask"]
                indexes=([len(frame)-1] if len(frame) and bool(mask.iloc[-1]) else []) if occurrence in {"TODAY","CURRENT"} else list(frame.index[mask].intersection(frame.index[-n:]))
                if indexes:
                    i=indexes[-1]; metrics={}
                    for key,series in result["metrics"].items():
                        try:
                            value=series.iloc[i]
                            if value==value: metrics[key]=round(float(value),6)
                        except (AttributeError,IndexError,TypeError,ValueError): pass
                    matched.add(code); reasons[code]={"signal_code":condition["signal_code"],"signal_name":definition.signal_name,"timeframe":tf,"date":frame.date.iloc[i].date().isoformat(),"metrics":metrics,"is_confirmed":not partial}
    return matched,reasons


def _eligible_stocks(universe: dict) -> dict[str, dict]:
    markets=set(universe.get("markets") or ["MAIN","CHINEXT","STAR"]); board_map={"主板":"MAIN","创业板":"CHINEXT","科创板":"STAR","北交所":"BSE"}; out={}
    with _connect() as db: rows=db.execute("SELECT * FROM stocks").fetchall()
    for row in rows:
        item=dict(row); name=str(item.get("name") or "")
        if board_map.get(item.get("board")) not in markets: continue
        if universe.get("exclude_st",True) and ("ST" in name.upper() or "退" in name): continue
        if universe.get("exclude_suspended",True) and item.get("paused"): continue
        if int(item.get("bar_count") or 0)<int(universe.get("min_listing_days") or 0): continue
        if universe.get("min_amount_ma20") is not None and (item.get("avg_amount20") is None or item["avg_amount20"]<float(universe["min_amount_ma20"])): continue
        out[item["code"]]=item
    return out


def _condition_ast(condition: dict) -> dict:
    return {"type": "condition", "condition": _condition_copy(condition)}


def _ast_operator(value: str, default: str = "AND") -> str:
    normalized = str(value or default).upper()
    return {"ALL": "AND", "ANY": "OR"}.get(normalized, normalized)


def _legacy_node_ast(node: dict) -> dict:
    if isinstance(node, dict) and isinstance(node.get("conditions"), list):
        return {
            "type": "group",
            "operator": _ast_operator(node.get("operator"), "AND"),
            "children": [_legacy_node_ast(child) for child in node["conditions"]],
        }
    return _condition_ast(node)


def compile_rule_config(payload: dict) -> dict:
    """编译为不含编辑器 ID/名称的稳定内部规则树。"""
    if _is_v2(payload):
        satisfy = payload.get("satisfy") or {}
        exclude = payload.get("exclude") or {}
        satisfy_children = []
        for group in satisfy.get("groups") or []:
            satisfy_children.append({
                "type": "group",
                "operator": _ast_operator(group.get("conditionMatch"), "ALL"),
                "children": [_condition_ast(condition) for condition in group.get("conditions") or []],
            })
        exclude_children = []
        for group in exclude.get("groups") or []:
            exclude_children.append({
                "type": "group",
                "operator": _ast_operator(group.get("conditionMatch"), "ANY"),
                "children": [_condition_ast(condition) for condition in group.get("conditions") or []],
            })
        return {
            "satisfy": {"type": "group", "operator": _ast_operator(satisfy.get("groupMatch"), "ALL"), "children": satisfy_children},
            "exclude": {"type": "group", "operator": "OR", "children": exclude_children},
        }

    must_nodes = [_legacy_node_ast(node) for node in payload.get("must_all") or []]
    if payload.get("any_of"):
        must_nodes.append({
            "type": "group", "operator": "OR",
            "children": [_legacy_node_ast(node) for node in payload["any_of"]],
        })
    return {
        "satisfy": {"type": "group", "operator": "AND", "children": must_nodes},
        "exclude": {
            "type": "group", "operator": "OR",
            "children": [_legacy_node_ast(node) for node in payload.get("exclude") or []],
        },
    }


def _semantic_cache_payload(payload: dict, compiled: dict) -> dict:
    return {
        "universe": payload.get("universe") or {},
        "default_timeframe": payload.get("default_timeframe") or "D",
        "rules": compiled,
        "sort": payload.get("sort") or [{"field": "signal_score", "direction": "desc"}],
        "limit": max(1, min(int(payload.get("limit") or 50), 300)),
        "offset": max(0, int(payload.get("offset") or 0)),
        "algorithm_ver": payload.get("algorithm_ver") or technical_signals.ALGORITHM_VERSION,
        "adjustment": payload.get("adjustment") or "qfq",
        "benchmark": payload.get("benchmark") or "000300.SH",
    }


def screen(payload: dict) -> dict:
    checked=validate_request(payload)
    if not checked["ok"]: return {"ok":False,"state":"invalid","errors":checked["errors"],"stocks":[]}
    cache_status=status()
    if not cache_status["stocks"]: return {"ok":False,"state":"missing","error_code":"technical_cache_missing","missing":["尚未生成全市场技术信号缓存，请先执行盘后刷新"],"stocks":[],"status":cache_status}
    compiled = compile_rule_config(payload)
    cache_key=hashlib.sha256(_json(_semantic_cache_payload(payload, compiled)).encode()).hexdigest()
    with _query_cache_lock:
        hit=_query_cache.get(cache_key)
        if hit and time.monotonic()-hit[0]<300:return dict(hit[1])
    stocks=_eligible_stocks(payload.get("universe") or {}); eligible=set(stocks); strongest=[]
    def resolve(condition): return _default_matches(condition) if _is_default(condition) else _custom_matches(condition,eligible)
    def resolve_node(node):
        if node.get("type") == "condition":
            condition = node["condition"]
            codes, one_detail = resolve(condition)
            return codes, {stock_code: [reason] for stock_code, reason in one_detail.items()}
        operator = str(node.get("operator") or "AND").upper()
        combined = set(eligible) if operator == "AND" else set()
        child_details = []
        for child in node.get("children") or []:
            child_codes, child_detail = resolve_node(child)
            combined = combined & child_codes if operator == "AND" else combined | child_codes
            child_details.append(child_detail)
        detail: dict[str, list[dict]] = {}
        for child_detail in child_details:
            for stock_code, reason_rows in child_detail.items():
                if stock_code in combined:
                    detail.setdefault(stock_code, []).extend(reason_rows)
        return combined, detail

    for index, unit in enumerate(compiled["satisfy"].get("children") or [], 1):
        unit_codes, _ = resolve_node(unit)
        strongest.append((f"满足规则 {index}", len(unit_codes)))
    selected, reasons = resolve_node(compiled["satisfy"])
    excluded, _ = resolve_node(compiled["exclude"])
    selected -= excluded
    rows=[]
    for code in selected:
        item=dict(stocks[code]); item["signal_score"]=len(reasons.get(code,[])); item["matched_reasons"]=reasons.get(code,[]); rows.append(item)
    sort=(payload.get("sort") or [{"field":"signal_score","direction":"desc"}])[0]; field=str(sort.get("field") or "signal_score"); desc=str(sort.get("direction") or "desc")=="desc"; allowed={"signal_score","change_pct","last_amount","avg_amount20","last_price"}; field=field if field in allowed else "signal_score"
    rows.sort(key=lambda row:(row.get(field) is None,-row[field] if desc and row.get(field) is not None else row.get(field) or 0))
    matched=len(rows); limit=max(1,min(int(payload.get("limit") or 50),300)); offset=max(0,int(payload.get("offset") or 0)); page=rows[offset:offset+limit]
    try:
        from . import screener as base_screener
        base_screener._enrich_candidate_industries(page)  # 候选低频补全，沿用12只上限
    except Exception: pass
    result={"ok":True,"state":cache_status["state"],"schema_version":SCHEMA_VERSION,"config_schema_version":CONFIG_SCHEMA_VERSION,"algorithm_ver":technical_signals.ALGORITHM_VERSION,"data_date":cache_status["data_date"],"adjustment":"qfq","matched":matched,"returned":len(page),"offset":offset,"limit":limit,"stocks":page,"coverage":{"cached":cache_status["stocks"],"eligible":len(eligible)},"strongest_condition":min(strongest,key=lambda x:x[1])[0] if matched==0 and strongest else None,"missing":([cache_status["level2_reason"]] if cache_status["level2_available"] is False else [])}
    with _query_cache_lock:_query_cache[cache_key]=(time.monotonic(),result)
    return result


def history(*, code: str | None = None, signal_code: str | None = None, timeframe: str | None = None, limit: int = 200) -> dict:
    sql="SELECT * FROM signal_matches WHERE signal_kind='event'"; args=[]
    for field,value in (("code",code),("signal_code",signal_code),("timeframe",timeframe)):
        if value: sql+=f" AND {field}=?"; args.append(str(value))
    sql+=" ORDER BY event_date DESC,code LIMIT ?"; args.append(max(1,min(int(limit),1000)))
    with _connect() as db: rows=[dict(row) for row in db.execute(sql,args).fetchall()]
    for row in rows:
        row["params"]=json.loads(row.pop("params_json")); row["metrics"]=json.loads(row.pop("metrics_json")); row["is_confirmed"]=bool(row["is_confirmed"])
    return {"ok":True,"events":rows,"returned":len(rows),"algorithm_ver":technical_signals.ALGORITHM_VERSION}


def list_schemes() -> list[dict]:
    with _connect() as db: rows=db.execute("SELECT * FROM schemes ORDER BY updated_at DESC").fetchall()
    schemes = []
    for row in rows:
        raw = json.loads(row["payload_json"])
        migration = migrate_legacy_config(raw)
        if migration["ok"]:
            item = dict(migration["config"])
            if migration["migrated"]:
                item["migration_state"] = "migrated"
        else:
            item = dict(raw)
            item.update({
                "schemaVersion": 1,
                "editable": False,
                "migration_state": "read_only_incompatible",
                "migration_reason": migration["reason"],
            })
        item.update({
            "id": row["id"], "name": row["name"], "algorithm_ver": row["algorithm_ver"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })
        schemes.append(item)
    return schemes


def save_scheme(payload: dict, scheme_id: str | None = None) -> dict:
    name=str(payload.get("name") or "未命名方案").strip()[:80]
    migration = migrate_legacy_config(payload)
    if not migration["ok"]:
        return {"ok": False, "errors": [{"code": "legacy_migration_failed", "message": migration["reason"]}]}
    config = migration["config"]
    checked=validate_request(config)
    if not checked["ok"]: return {"ok":False,"errors":checked["errors"]}
    sid=str(scheme_id or payload.get("id") or uuid.uuid4().hex); now=ts_str(); clean={k:v for k,v in config.items() if k not in {"id","created_at","updated_at","editable","migration_state","migration_reason"}}; clean["name"]=name; clean["algorithm_ver"]=str(payload.get("algorithm_ver") or technical_signals.ALGORITHM_VERSION)
    with _connect() as db:
        existing=db.execute("SELECT created_at FROM schemes WHERE id=?",(sid,)).fetchone(); created=existing[0] if existing else now
        db.execute("INSERT INTO schemes(id,name,payload_json,algorithm_ver,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,payload_json=excluded.payload_json,algorithm_ver=excluded.algorithm_ver,updated_at=excluded.updated_at",(sid,name,_json(clean),clean["algorithm_ver"],created,now))
    return {**clean,"ok":True,"id":sid,"created_at":created,"updated_at":now}


def delete_scheme(scheme_id: str) -> dict:
    with _connect() as db: cursor=db.execute("DELETE FROM schemes WHERE id=?",(str(scheme_id),))
    return {"ok":cursor.rowcount>0,"id":str(scheme_id)}
