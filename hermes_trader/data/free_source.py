"""A 股免费数据兼容层。

该模块把原先散落在 Tushare 付费积分接口中的常用能力，改由无需商业订阅、
无需 API Key 的公开源组合提供。调用方只依赖这里的稳定契约，不直接感知上游：

* 行情/估值/涨跌停价：腾讯财经；
* 代码表、实时价量、行业关系：客户授权 TDX 数据 API；
* 财务指标、涨跌停池、业绩预告：AkShare 包装的新浪/同花顺/东财公开接口；
* 个股日度资金流：新浪公开接口；
* 涨停原因：同花顺公开接口。

"免费"不等于有 SLA 或可无限抓取。所有调用均短超时、缓存、失败返回空并带数据源
埋点；东财仍只作为公开备用源，不能成为关键字段的唯一事实来源。
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zlib
from zoneinfo import ZoneInfo

from .base import (
    _record_source,
    direct_https_opener,
    get_ak,
    is_trade_day,
    log,
    now_cn,
    prev_trade_day,
    safe,
    to_float,
    to_int,
    ttl_cache,
)
from . import tencent_source as tc
from . import akshare_isolated
from . import bootstrap_universe
from . import official_universe
from . import tdx_api_source as _tdx_research

_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/126 Safari/537.36"
    )
}
_DIRECT = direct_https_opener()
_UNIVERSE_CACHE_FRESH_AGE = 30 * 24 * 3600
_UNIVERSE_CACHE_MAX_STALE_AGE = 180 * 24 * 3600
_UNIVERSE_MIN_COVERAGE = 3000
_UNIVERSE_RACE_TIMEOUT = 7.0
_UNIVERSE_TDX_GRACE = 6.0
_universe_status_lock = threading.Lock()
_universe_status: dict = {
    "source": "unavailable",
    "count": 0,
    "stale": True,
    "refresh_required": True,
}


def _code6(code: object) -> str:
    return str(code or "").split(".")[0].strip().zfill(6)


def _market_prefix(code: str) -> str:
    code = _code6(code)
    if code.startswith(("4", "8", "92")):
        return "bj"
    if code.startswith(("5", "6", "9")):
        return "sh"
    return "sz"


def _iso_from_compact(value: object) -> str:
    s = str(value or "")[:10]
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _compact_from_iso(value: object) -> str:
    return str(value or "").replace("-", "")[:8]


def _fmt_hhmmss(value: object) -> str:
    s = str(value or "").strip()
    if s.isdigit():
        s = s.zfill(6)[-6:]
        return f"{s[:2]}:{s[2:4]}:{s[4:6]}"
    return s


def _universe_cache_path() -> Path:
    root = Path(os.environ.get("HERMES_DATA_DIR") or Path.home() / ".cache" / "hermes-trader")
    return root / "market" / "a-share-universe.json"


def _set_universe_status(status: dict) -> None:
    global _universe_status
    with _universe_status_lock:
        _universe_status = dict(status)


def stock_universe_status() -> dict:
    """最近一次股票池的来源/时效/覆盖状态，供运行时诚实展示。

    冷启动时先读本客户的 last-good 元数据，不发网络请求。否则即使磁盘已有
    当天完整快照，设置页和上线门禁也会在第一次选股前误报 ``unavailable``。
    """
    with _universe_status_lock:
        status = dict(_universe_status)
    if status.get("source") == "unavailable" or not status.get("count"):
        _load_universe_cache()
        with _universe_status_lock:
            status = dict(_universe_status)
    return status


@ttl_cache(5 * 60, fail_seconds=30)
def cached_stock_universe() -> dict[str, str]:
    """只读本地 last-good/内置官方快照，不发起网络请求。

    交互接口可用它区分“已知股票但当前报价未返回”和“在完整名录中不存在”；
    不得为了一次问股同步刷新全 A 名录。
    """
    return _load_universe_cache() or _load_bootstrap_universe() or {}


def cached_stock_identity(code: str) -> dict:
    """返回本地股票识别结果；名录不完整时绝不把未命中说成不存在。"""
    normalized = _code6(code)
    names = cached_stock_universe()
    status = stock_universe_status()
    name = str(names.get(normalized) or "").strip()
    complete = bool(status.get("complete")) and not bool(status.get("missing_exchanges"))
    return {
        "code": normalized,
        "known": bool(name),
        "name": name,
        "universe_complete": complete,
        "universe_as_of": status.get("as_of"),
    }


def _load_universe_cache() -> dict[str, str] | None:
    path = _universe_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        saved_at = float(payload.get("saved_at_epoch") or 0)
        names = payload.get("names") if isinstance(payload, dict) else None
        age = max(0.0, time.time() - saved_at)
        if age > _UNIVERSE_CACHE_MAX_STALE_AGE or not isinstance(names, dict):
            _record_source(
                "股票代码表(本地last-good)", 0, False, "cache-expired", "快照已超最长保留期"
            )
            return None
        clean = {
            _code6(code): str(name or "")
            for code, name in names.items()
            if _code6(code).isdigit()
        }
        if len(clean) < _UNIVERSE_MIN_COVERAGE:
            return None
        stale = age > _UNIVERSE_CACHE_FRESH_AGE
        status = {
            "source": str(payload.get("source") or "本地last-good"),
            "sources": list(payload.get("sources") or []),
            "as_of": payload.get("as_of") or payload.get("saved_at"),
            "fetched_at": payload.get("saved_at"),
            "count": len(clean),
            "coverage": payload.get("coverage") or {"total": len(clean)},
            "stale": stale,
            "refresh_required": stale or bool(payload.get("refresh_required")),
            "complete": bool(payload.get("complete", not payload.get("refresh_required"))),
            "missing_exchanges": list(payload.get("missing_exchanges") or []),
            "source_attempts": dict(payload.get("source_attempts") or {}),
            "age_seconds": round(age),
            "cache": "last-good",
        }
        _set_universe_status(status)
        _record_source(
            "股票代码表(本地last-good)", 0, True,
            "cache-stale" if stale else ("cache-partial" if status["refresh_required"] else "cache"),
            (
                f"refresh_required; missing={','.join(status['missing_exchanges']) or 'unknown'}"
                if status["refresh_required"] else None
            ),
        )
        return clean
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _save_universe_cache(names: dict[str, str], metadata: dict | None = None) -> None:
    if len(names) < _UNIVERSE_MIN_COVERAGE:
        return
    path = _universe_cache_path()
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        saved_at = now_cn().isoformat(timespec="seconds")
        meta = dict(metadata or {})
        payload = {
            "schema": "a-share-universe-v2",
            "saved_at": saved_at,
            "saved_at_epoch": time.time(),
            "as_of": meta.get("as_of") or now_cn().date().isoformat(),
            "source": meta.get("source") or "未标注免费源",
            "sources": list(meta.get("sources") or []),
            "coverage": meta.get("coverage") or {"total": len(names)},
            "refresh_required": bool(meta.get("refresh_required")),
            "complete": bool(meta.get("complete", not meta.get("refresh_required"))),
            "missing_exchanges": list(meta.get("missing_exchanges") or []),
            "source_attempts": dict(meta.get("source_attempts") or {}),
            "names": names,
        }
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.info("股票代码表快照写入失败: %s", str(exc)[:120])
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _load_bootstrap_universe() -> dict[str, str] | None:
    """首次启动且全部网络源不可达时，读取官方列表生成的内置快照。

    它不是 last-good，永远标记 stale/refresh_required；一年后硬失效。
    """
    try:
        as_of = datetime.fromisoformat(bootstrap_universe.AS_OF).date()
        age_days = max(0, (now_cn().date() - as_of).days)
        if age_days > 365:
            _record_source(
                "股票代码表(内置官方快照)", 0, False,
                "bootstrap-expired", f"as_of={bootstrap_universe.AS_OF}; age={age_days}d",
            )
            return None
        names = {
            _code6(code): str(name or "").strip()
            for code, name in bootstrap_universe.load().items()
            if _code6(code).isdigit() and str(name or "").strip()
        }
        if len(names) < _UNIVERSE_MIN_COVERAGE:
            return None
        status = {
            "source": "交易所官网公开列表内置首启快照",
            "sources": list(bootstrap_universe.SOURCES),
            "as_of": bootstrap_universe.AS_OF,
            "fetched_at": bootstrap_universe.GENERATED_AT,
            "count": len(names),
            "coverage": dict(bootstrap_universe.COVERAGE),
            "stale": True,
            "refresh_required": True,
            "complete": False,
            "missing_exchanges": list(bootstrap_universe.COVERAGE.get("missing") or []),
            "age_seconds": age_days * 86400,
            "cache": "bundled-bootstrap",
        }
        _set_universe_status(status)
        _record_source(
            "股票代码表(内置官方快照)", 0, True,
            "bootstrap-stale", f"as_of={bootstrap_universe.AS_OF}; refresh_required",
        )
        return names
    except (TypeError, ValueError, OSError, json.JSONDecodeError, zlib.error) as exc:
        log.info("股票代码表内置快照不可用: %s", str(exc)[:120])
        return None


def quote_data_date() -> str:
    """当前免费报价实际归属日（ISO）。盘前/休市不得盖成自然日。"""
    now = now_cn()
    hm = now.hour * 60 + now.minute
    if is_trade_day(now.date()) and hm >= 9 * 60 + 15:
        return now.date().isoformat()
    return prev_trade_day(now.date()) or now.date().isoformat()


def _direct_json(url: str, *, label: str, headers: dict | None = None, timeout: int = 10):
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={**_UA, **(headers or {})})
        raw = _DIRECT.open(req, timeout=timeout).read().decode("utf-8", "ignore")
        data = json.loads(raw)
        _record_source(label, (time.monotonic() - t0) * 1000, True, "direct", None)
        return data
    except Exception as exc:  # noqa: BLE001 - 外部公开源必须优雅降级
        _record_source(label, (time.monotonic() - t0) * 1000, False, "direct", str(exc)[:140])
        log.info("免费源 %s 不可用: %s", label, str(exc)[:140])
        return None


@ttl_cache(45, fail_seconds=90)
def minute_bars(symbol: str, period: str = "1", limit: int = 1970) -> list[dict]:
    """新浪公开 JSONP 分时，不经过 AkShare 的隐藏 MiniRacer 日线探测。

    ``stock_zh_a_minute`` 即使 ``adjust=''`` 也会先调用 V8 版日线；这里只取
    它真正需要的 JSONP 段，归一为 day/open/high/low/close/volume/amount。
    """
    symbol = str(symbol or "").strip()
    if not re.fullmatch(r"(?:sh|sz|bj)\d{6}", symbol):
        return []
    scale = str(period or "1")
    if scale not in {"1", "5", "15", "30", "60"}:
        return []
    query = urllib.parse.urlencode({
        "symbol": symbol,
        "scale": scale,
        "ma": "no",
        "datalen": max(1, min(int(limit or 1970), 1970)),
    })
    url = (
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/"
        f"CN_MarketDataService.getKLineData?{query}"
    )
    started = time.monotonic()
    try:
        req = urllib.request.Request(url, headers=_UA)
        raw = _DIRECT.open(req, timeout=8).read().decode("utf-8", "ignore")
        left, right = raw.find("=("), raw.rfind(");")
        if left < 0 or right <= left:
            raise ValueError("invalid Sina JSONP")
        payload = json.loads(raw[left + 2:right])
        out = []
        for row in payload if isinstance(payload, list) else []:
            if not isinstance(row, dict) or not row.get("day"):
                continue
            out.append({
                "day": str(row.get("day")),
                "open": to_float(row.get("open")),
                "high": to_float(row.get("high")),
                "low": to_float(row.get("low")),
                "close": to_float(row.get("close")),
                "volume": to_float(row.get("volume")),
                "amount": to_float(row.get("amount")),
                "source": "新浪公开分时",
            })
        _record_source(
            "分时(新浪JSONP)",
            (time.monotonic() - started) * 1000,
            bool(out),
            "direct",
            None if out else "空响应",
        )
        return out
    except Exception as exc:  # noqa: BLE001
        _record_source(
            "分时(新浪JSONP)",
            (time.monotonic() - started) * 1000,
            False,
            "direct",
            str(exc)[:140],
        )
        log.info("免费源 分时(新浪JSONP) 不可用: %s", str(exc)[:140])
        return []


def available() -> bool:
    """本地免费数据运行时是否就绪；不主动打网络探针。"""
    return get_ak() is not None


def _bounded_tdx_api_universe(timeout: float) -> dict[str, str] | None:
    """TDX HTTP API 名录在隔离子进程内读取，超时可硬终止。"""
    started = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "hermes_trader.data.universe_worker", "tdx_api"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={
                key: os.environ[key]
                for key in (
                    "HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TZ",
                    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
                    "TDXAPI_BASE", "TDXAPI_KEY",
                )
                if os.environ.get(key)
            } | {"PYTHONIOENCODING": "utf-8"},
            timeout=max(0.2, timeout),
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or f"exit={result.returncode}")[-140:])
        payload = json.loads(result.stdout or "{}")
        names = payload if isinstance(payload, dict) else {}
        ok = len(names) >= _UNIVERSE_MIN_COVERAGE
        _record_source(
            "股票代码表(TDX API)", (time.monotonic() - started) * 1000,
            ok, "tdx_api", None if ok else "覆盖不足",
        )
        return names if ok else None
    except subprocess.TimeoutExpired:
        _record_source(
            "股票代码表(TDX API)", (time.monotonic() - started) * 1000,
            False, "tdx_api", f"total wall timeout after {timeout:.1f}s",
        )
        return None
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        _record_source(
            "股票代码表(TDX API)", (time.monotonic() - started) * 1000,
            False, "tdx_api", str(exc)[:140],
        )
        return None


def _tdx_exchange_coverage(names: dict[str, str]) -> tuple[dict[str, int], list[str]]:
    coverage = {
        "sse": sum(code.startswith("6") for code in names),
        "szse": sum(code.startswith(("0", "3")) for code in names),
        "bse": sum(code.startswith(("4", "8", "92")) for code in names),
    }
    missing = [
        exchange
        for exchange, minimum in {"sse": 1500, "szse": 2000, "bse": 50}.items()
        if coverage[exchange] < minimum
    ]
    return coverage, missing


def _active_a_share_names(names: dict[str, str]) -> dict[str, str]:
    """Normalize the canonical universe and exclude explicit delisting names."""
    out: dict[str, str] = {}
    for raw_code, raw_name in (names or {}).items():
        code = _code6(raw_code)
        name = str(raw_name or "").strip()
        market_ok = code.startswith(("0", "3", "4", "6", "8", "92"))
        delisted = name.startswith("退市") or name.endswith("退")
        if re.fullmatch(r"\d{6}", code) and market_ok and name and not delisted:
            out[code] = name
    return out


@ttl_cache(30 * 60, fail_seconds=60)
def stock_universe() -> dict[str, str] | None:
    """全 A 股 ``code -> name``：三家交易所官网独立并发，并与 TDX API 竞速。

    不再调用 AkShare 的串行聚合函数。交易所结果合并后达到 3000 只即可
    作为真实分母（允许单个交易所故障）；低于门槛绝不落盘。全链失败时
    读取 last-good，30 天后明确标记 stale/refresh_required，180 天后拒绝使用。
    """
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hermes-universe")
    future_kind = {
        executor.submit(_bounded_tdx_api_universe, _UNIVERSE_RACE_TIMEOUT - 0.25): "tdx_api"
    }
    for exchange in ("sse", "szse", "bse"):
        future_kind[executor.submit(
            official_universe.fetch_exchange, exchange, _UNIVERSE_RACE_TIMEOUT - 0.5
        )] = exchange
    pending = set(future_kind)
    deadline = time.monotonic() + _UNIVERSE_RACE_TIMEOUT
    official_parts: dict[str, official_universe.ExchangeUniverse] = {}
    tdx_candidate: dict[str, str] | None = None
    official_candidate: tuple[dict[str, str], dict] | None = None
    candidate_ready_at: float | None = None
    try:
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if candidate_ready_at is not None:
                remaining = min(remaining, max(0.0, _UNIVERSE_TDX_GRACE - (time.monotonic() - candidate_ready_at)))
                if remaining <= 0:
                    break
            done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                kind = future_kind[future]
                try:
                    result = future.result()
                except Exception:  # noqa: BLE001 - 任一分支失败不影响另三支
                    result = None
                if kind == "tdx_api":
                    names = result if isinstance(result, dict) else {}
                    if len(names) >= _UNIVERSE_MIN_COVERAGE:
                        tdx_candidate = names
                        _coverage, _missing = _tdx_exchange_coverage(names)
                        if not _missing:
                            candidate_ready_at = candidate_ready_at or time.monotonic()
                elif isinstance(result, official_universe.ExchangeUniverse):
                    official_parts[kind] = result

            official_names: dict[str, str] = {}
            for exchange in ("sse", "szse", "bse"):
                part = official_parts.get(exchange)
                if part:
                    official_names.update(part.names)
            official_names = _active_a_share_names(official_names)
            if len(official_names) >= _UNIVERSE_MIN_COVERAGE:
                coverage = {
                    exchange: {
                        "count": len(part.names), "complete": part.complete, "error": part.error,
                    }
                    for exchange, part in official_parts.items()
                }
                coverage["total"] = len(official_names)
                missing_exchanges = [
                    exchange for exchange in ("sse", "szse", "bse")
                    if exchange not in official_parts or not official_parts[exchange].complete
                ]
                metadata = {
                    "source": "沪深京交易所官网公开列表",
                    "sources": [part.source_url for part in official_parts.values() if part.names],
                    "as_of": now_cn().date().isoformat(),
                    "coverage": coverage,
                    "refresh_required": bool(missing_exchanges),
                    "complete": not missing_exchanges,
                    "missing_exchanges": missing_exchanges,
                }
                if metadata["complete"]:
                    # 官网的 complete 只表示三个端点各自返回，不代表集合没有漏股。
                    # 给 TDX 名录一个很短的 grace，两者做并集后再落盘。
                    official_candidate = (official_names, metadata)
                    candidate_ready_at = candidate_ready_at or time.monotonic()
                    if tdx_candidate is not None:
                        break
                    continue
                # 官网部分达标只作候选，给仍在途的 BSE/完整 TDX 短暂 grace。
                official_candidate = (official_names, metadata)

        tdx_choice: tuple[dict[str, str], dict] | None = None
        source_attempts = {
            exchange: {
                "status": (
                    "ok" if official_parts.get(exchange) and official_parts[exchange].complete
                    else ("degraded" if official_parts.get(exchange) else "timeout")
                ),
                "count": len(official_parts[exchange].names) if official_parts.get(exchange) else 0,
                "error": (official_parts[exchange].error if official_parts.get(exchange) else "bounded_fetch_timeout"),
            }
            for exchange in ("sse", "szse", "bse")
        }
        source_attempts["tdx_api"] = {
            "status": "ok" if tdx_candidate is not None else "unavailable",
            "count": len(tdx_candidate or {}),
            "error": None if tdx_candidate is not None else "instruments_a_unavailable",
        }
        if official_candidate is not None:
            official_candidate[1]["source_attempts"] = source_attempts
        if tdx_candidate is not None:
            tdx_candidate = _active_a_share_names(tdx_candidate)
            tdx_coverage, missing_exchanges = _tdx_exchange_coverage(tdx_candidate)
            metadata = {
                "source": "TDX 数据 API 股票列表",
                "sources": ["TDX 数据 API / instruments_a"],
                "as_of": now_cn().date().isoformat(),
                "coverage": {
                    **tdx_coverage, "tdx": len(tdx_candidate), "total": len(tdx_candidate),
                },
                "refresh_required": bool(missing_exchanges),
                "complete": not missing_exchanges,
                "missing_exchanges": missing_exchanges,
                "source_attempts": source_attempts,
            }
            tdx_choice = (tdx_candidate, metadata)

        choices = [choice for choice in (official_candidate, tdx_choice) if choice is not None]
        if official_candidate is not None and tdx_choice is not None:
            official_names, official_meta = official_candidate
            tdx_names, tdx_meta = tdx_choice
            merged_names = dict(tdx_names)
            merged_names.update(official_names)  # 同代码以交易所名称为准
            merged_names = _active_a_share_names(merged_names)
            merged_coverage, merged_missing = _tdx_exchange_coverage(merged_names)
            choices.append((merged_names, {
                "source": "沪深京交易所官网 + TDX 数据 API 交叉并集",
                "sources": list(dict.fromkeys(
                    list(official_meta.get("sources") or []) + list(tdx_meta.get("sources") or [])
                )),
                "as_of": now_cn().date().isoformat(),
                "coverage": {
                    **merged_coverage,
                    "official": len(official_names),
                    "tdx": len(tdx_names),
                    "tdx_only": len(set(tdx_names) - set(official_names)),
                    "official_only": len(set(official_names) - set(tdx_names)),
                    "total": len(merged_names),
                },
                "refresh_required": bool(merged_missing),
                "complete": not merged_missing,
                "missing_exchanges": merged_missing,
                "source_attempts": source_attempts,
            }))
        if choices:
            # 优先完整、其次缺所更少、再比覆盖数；不因先返回
            # 一份 3000+ partial 就丢弃几百毫秒后的完整免费股票池。
            selected_names, selected_meta = max(
                choices,
                key=lambda item: (
                    bool(item[1].get("complete")),
                    -len(item[1].get("missing_exchanges") or []),
                    len(item[0]),
                    2 if "交叉并集" in str(item[1].get("source") or "") else (
                        1 if item[1].get("source") == "沪深京交易所官网公开列表" else 0
                    ),
                ),
            )
            _save_universe_cache(selected_names, selected_meta)
            _set_universe_status({
                **selected_meta, "count": len(selected_names), "stale": False,
                "fetched_at": now_cn().isoformat(timespec="seconds"),
            })
            source_name = str(selected_meta.get("source") or "")
            if "交叉并集" in source_name:
                label, mode = "股票代码表(官网+TDX API)", "tdx_api"
            elif source_name == "沪深京交易所官网公开列表":
                label, mode = "股票代码表(交易所官网)", "official-direct"
            else:
                label, mode = "股票代码表(TDX API)", "tdx_api"
            _record_source(label, 0, True, mode, None)
            for other in pending:
                other.cancel()
            return selected_names
        cached = _load_universe_cache()
        return cached if cached is not None else _load_bootstrap_universe()
    finally:
        # 不等待失去竞速的慢分支；页面墙钟最长 7s。
        executor.shutdown(wait=False, cancel_futures=True)


def _name_map() -> dict[str, str]:
    """兼容原 Tushare 调用名。"""
    return stock_universe() or {}


@ttl_cache(90)
def daily_basic(code: str) -> dict | None:
    """实时估值/市值/换手，腾讯公开行情无需 token。"""
    q = tc.quote(_code6(code))
    if not q:
        return None
    return {
        "trade_date": quote_data_date().replace("-", ""),
        "turnover_rate": q.get("turnover_rate"),
        "volume_ratio": q.get("volume_ratio"),
        "pe": q.get("pe"),
        "pe_ttm": q.get("pe"),
        "pb": q.get("pb"),
        "total_mv": q.get("total_mv"),
        "circ_mv": q.get("float_mv"),
        "source": "腾讯财经",
    }


@ttl_cache(300)
def daily_bars(code: str, days: int = 120) -> list[dict]:
    """免费前复权日线；只有上游明确返回 ``qfqday`` 才可用。

    TDX API 日 K 是未复权口径，除权日会产生假跌幅并污染均线/收益，
    因此不能作为这个前复权契约的隐式回退。腾讯失败时显式缺失。
    """
    c6 = _code6(code)
    out = tc.stock_daily_bars(c6, days, adjust="qfq")
    return out[-max(1, int(days)):] if out else []


@ttl_cache(3600)
def fina_indicator(code: str) -> dict | None:
    """最近基础财务：TDX ``stock_info`` 主源，新浪公开源备用。"""
    primary = _tdx_research.basic_financial(_code6(code))
    if primary:
        return primary
    ak = get_ak()
    fn = getattr(ak, "stock_financial_analysis_indicator", None) if ak else None
    if fn is None:
        return None
    df = safe(
        fn,
        symbol=_code6(code),
        start_year=str(now_cn().year - 4),
        label="财务指标(新浪)",
        timeout=25,
    )
    if df is None or getattr(df, "empty", True):
        return None
    try:
        if "日期" in df.columns:
            df = df.sort_values("日期", ascending=False)
        row = df.iloc[0]
    except Exception:
        return None
    gross_margin = to_float(row.get("销售毛利率(%)"))
    if gross_margin is None:
        gross_margin = to_float(row.get("主营业务利润率(%)"))
    out = {
        "end_date": _compact_from_iso(row.get("日期")),
        "roe": to_float(row.get("净资产收益率(%)")),
        "grossprofit_margin": gross_margin,
        "netprofit_yoy": to_float(row.get("净利润增长率(%)")),
        "or_yoy": to_float(row.get("主营业务收入增长率(%)")),
        "debt_to_assets": to_float(row.get("资产负债率(%)")),
        "source": "新浪财务分析",
    }
    if all(v is None for k, v in out.items() if k not in {"end_date", "source"}):
        return None
    return out


@ttl_cache(24 * 3600)
def industry(code: str) -> str | None:
    """个股行业；只使用客户 TDX API 的 relation 主数据。

    旧的公开 F10 备用经常返回 HTML/空内容，已从母版删除。relation
    缺失时如实返回 ``None``，不得用慢源拖住交互请求或伪造行业。
    """
    relation = _tdx_research.relations_for_codes([_code6(code)]).get(_code6(code)) or {}
    mapped = relation.get("industry")
    if isinstance(mapped, dict):
        value = str(mapped.get("name") or "").strip()
    else:
        value = str(mapped or "").strip()
    return value or None


@ttl_cache(24 * 3600, fail_seconds=300)
def verified_industry(code: str) -> str | None:
    """兼容旧调用名；行业主数据只认远程 TDX relation。"""
    return industry(code)


@ttl_cache(300)
def moneyflow(code: str) -> dict | None:
    """新浪日度四档单资金流，作为东财资金流的独立免费备源。"""
    c6 = _code6(code)
    sym = _market_prefix(c6) + c6
    query = urllib.parse.urlencode(
        {"page": 1, "num": 20, "sort": "opendate", "asc": 0, "daima": sym}
    )
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"MoneyFlow.ssl_qsfx_zjlrqs?{query}"
    )
    data = _direct_json(
        url,
        label="个股资金流(新浪)",
        headers={"Referer": "https://finance.sina.com.cn/"},
    )
    if not isinstance(data, list) or not data:
        return None
    row = data[0]

    def num(*keys):
        for key in keys:
            value = to_float(row.get(key))
            if value is not None:
                return value
        return None

    # 新浪该端点公开的 r0_net 是其“大单/主力”口径，netamount 为全口径净额。
    # 不能把 netamount 冒充旧契约的“主力=超大+大单”，也不能把 r0 伪拆成四档。
    main = num("r0_net", "main_net")
    net_all = num("netamount")
    if main is None and net_all is None:
        return None
    return {
        "trade_date": _compact_from_iso(row.get("opendate") or row.get("date")),
        "main_net": main,
        "super_net": None,
        "large_net": None,
        "medium_net": None,
        "small_net": None,
        "net_all": net_all,
        "derived": True,
        "source": "新浪资金流估算口径",
    }


def _fund_flow_rank_frame():
    ak = get_ak()
    fn = getattr(ak, "stock_individual_fund_flow_rank", None) if ak else None
    if fn is None:
        return None
    return safe(fn, indicator="今日", label="个股资金流排名(公开源)", timeout=4)


def _isolated_market_flow_rows() -> list[dict]:
    """同花顺净额排序首屏的双端结果，统一成系统的元口径。"""
    result = akshare_isolated.market_fund_flow(timeout=12, trip_breaker=True)
    latency_ms = to_float(result.get("latency_ms")) or 0.0
    if result.get("ok") is not True:
        reason = str(result.get("error") or "隔离子进程失败")[:140]
        _record_source("个股资金流排名(同花顺隔离)", latency_ms, False,
                       "isolated-subprocess", reason)
        log.warning("个股资金流隔离子进程降级: %s", reason)
        return []
    raw_rows = result.get("rows")
    if not isinstance(raw_rows, list):
        _record_source("个股资金流排名(同花顺隔离)", latency_ms, False,
                       "isolated-subprocess", "rows 非列表")
        return []
    data_date = quote_data_date()
    out: list[dict] = []
    seen: set[str] = set()
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        code = _code6(raw.get("code"))
        name = str(raw.get("name") or "").strip()
        main_net = to_float(raw.get("net_raw"))
        if (
            len(code) != 6 or not code.isdigit() or not name
            or main_net is None or not math.isfinite(main_net)
            or abs(main_net) > 1_000_000_000_000
        ):
            continue
        key = f"{code}:{raw.get('direction')}"
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "code": code,
            "name": name,
            "main_net": main_net,
            "change_pct": to_float(raw.get("change_pct")),
            "reason": "同花顺公开个股资金净额榜",
            "rnk": 0,
            "trade_date": data_date,
            "source": "同花顺·个股资金流",
        })
    out.sort(key=lambda row: row["main_net"], reverse=True)
    for rank, row in enumerate(out, 1):
        row["rnk"] = rank
    _record_source("个股资金流排名(同花顺隔离)", latency_ms, bool(out),
                   "isolated-subprocess", None if out else "子进程返回空个股榜")
    return out


@ttl_cache(120, offhours_seconds=6 * 3600)
def moneyflow_market(top: int | None = 20) -> list[dict]:
    """公开资金流双端榜；同花顺单页优先，东财全表仅作备用。"""
    isolated = _isolated_market_flow_rows()
    if isolated:
        if top is None:
            return isolated
        return isolated[:max(1, int(top))]
    df = _fund_flow_rank_frame()
    if df is None or getattr(df, "empty", True):
        return []
    net_col = next((c for c in df.columns if "主力净流入-净额" in str(c)), None)
    pct_col = next((c for c in df.columns if "涨跌幅" in str(c)), None)
    if not net_col:
        return []
    try:
        import pandas as pd  # noqa: PLC0415

        work = df.assign(_net=pd.to_numeric(df[net_col], errors="coerce")).dropna(subset=["_net"])
        work = work.sort_values("_net", ascending=False)
        if top is not None:
            work = work.head(max(1, int(top)))
    except Exception:
        return []
    data_date = quote_data_date()
    return [
        {
            "code": _code6(row.get("代码")),
            "name": str(row.get("名称") or ""),
            "main_net": to_float(row.get("_net")),
            "change_pct": to_float(row.get(pct_col)) if pct_col else None,
            "reason": "公开主力净流入榜",
            "rnk": i,
            "trade_date": data_date,
        }
        for i, (_, row) in enumerate(work.iterrows(), 1)
    ]


@ttl_cache(120, offhours_seconds=6 * 3600)
def moneyflow_market_both(n: int = 6) -> dict:
    # 先保留全表再分别取头尾；若先截 top，全 A 股数超过截断值时
    # 会恰好丢掉净流出最多的尾部股票。
    rows = moneyflow_market(None)
    if not rows:
        return {"inflow": [], "outflow": [], "data_date": None}
    ordered = sorted(rows, key=lambda x: x.get("main_net") or 0, reverse=True)
    outflow = sorted(
        (r for r in ordered if (r.get("main_net") or 0) < 0),
        key=lambda x: x.get("main_net") or 0,
    )[: max(1, int(n))]
    return {
        "inflow": ordered[: max(1, int(n))],
        "outflow": outflow,
        "data_date": ordered[0].get("trade_date"),
    }


def _numeric_amount_to_yuan(value: object, *, numeric_unit: str) -> float | None:
    """将金额转成元，防止带单位字符串被二次放大。

    公开接口的数值列通常是裸 ``float``（此处单位为亿元），但上游版本也可能
    返回 ``5.23亿`` / ``-3100万``。``to_float`` 已识别字符串单位，这种情况不再乘一次。
    """
    if isinstance(value, str) and value.strip().endswith(("万", "亿")):
        return to_float(value)
    number = to_float(value)
    if number is None:
        return None
    return number * (1e8 if numeric_unit == "yi" else 1.0)


@ttl_cache(120, offhours_seconds=6 * 3600)
def moneyflow_ind_dc(
    date_compact: str | None = None,
    *,
    timeout: float = 4.0,
    trip_breaker: bool = False,
) -> list[dict]:
    """同花顺行业即时资金流（AkShare/V8 隔离子进程）。

    这是有 120s TTL 的唯一主进程入口。子进程崩溃/超时返空并
    写入数据源指标；不会在主进程内绕过 V8 安全门禁。
    """
    if date_compact:
        requested = _iso_from_compact(date_compact)
        if requested and requested != quote_data_date():
            return []  # 即时源不能回答历史日，绝不把当前快照贴成历史
    # 交互调用默认只等 4 秒且不改变全局熔断；后台资金快照显式传入
    # 25 秒完整预算并允许熔断。这样页面不会被慢源拖住，也不会因一次
    # 短预算超时把之后 5 分钟的后台采集一并误杀。
    result = akshare_isolated.industry_fund_flow(
        timeout=max(0.1, float(timeout)),
        trip_breaker=bool(trip_breaker),
    )
    latency_ms = to_float(result.get("latency_ms")) or 0.0
    if result.get("ok") is not True:
        reason = str(result.get("error") or "隔离子进程失败")[:140]
        _record_source(
            "行业资金流(同花顺隔离)",
            latency_ms,
            False,
            "isolated-subprocess",
            reason,
        )
        log.warning("行业资金流隔离子进程降级: %s", reason)
        return []
    raw_rows = result.get("rows")
    if not isinstance(raw_rows, list):
        _record_source(
            "行业资金流(同花顺隔离)", latency_ms, False,
            "isolated-subprocess", "rows 非列表",
        )
        return []
    # 1.18.x 此列的纯数值单位为亿元。若未来上游改成“元”却仍给纯数字，
    # 原值会跃升到 1e8 级；在乘 1e8 前整批拒绝，不静默放大 1e8 倍。
    numeric_raw = [
        float(row.get("net_raw"))
        for row in raw_rows
        if isinstance(row, dict) and isinstance(row.get("net_raw"), (int, float))
    ]
    if any(not math.isfinite(value) or abs(value) > 10_000 for value in numeric_raw):
        _record_source(
            "行业资金流(同花顺隔离)", latency_ms, False,
            "isolated-subprocess", "净额单位/量级校验失败",
        )
        return []
    out = []
    fetched_at = now_cn().isoformat(timespec="seconds")
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        net = _numeric_amount_to_yuan(row.get("net_raw"), numeric_unit="yi")
        if net is None:
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "main_net": net,
                "change_pct": to_float(row.get("change_pct")),
                "source": "同花顺·公开行业资金流(AkShare隔离)",
                "fetched_at": fetched_at,
                "unit": "CNY",
            }
        )
    out.sort(key=lambda x: x.get("main_net") or 0, reverse=True)
    for i, row in enumerate(out, 1):
        row["rnk"] = i
    _record_source(
        "行业资金流(同花顺隔离)",
        latency_ms,
        bool(out),
        "isolated-subprocess",
        None if out else "子进程返回空行业列表",
    )
    return out


@ttl_cache(60)
def auction(code: str) -> dict | None:
    """当前 TDX HTTP API 不提供可验证的竞价匹配量/未匹配量。

    不得用普通实时成交量冒充集合竞价；未接入独立竞价源前诚实返回 None。
    """
    return None


@ttl_cache(300)
def auction_oc(code: str) -> dict | None:
    """当前 API 无开/收盘竞价专用数据集，不从普通 K 线倒推。"""
    return None


def _ak_limit_frame(date_compact: str, kind: str):
    ak = get_ak()
    if ak is None:
        return None
    name = {
        "U": "stock_zt_pool_em",
        "D": "stock_zt_pool_dtgc_em",
        "Z": "stock_zt_pool_zbgc_em",
    }.get(kind, "stock_zt_pool_em")
    fn = getattr(ak, name, None)
    return safe(fn, date=date_compact, label=f"涨跌停池({kind},公开源)", timeout=4) if fn else None


@ttl_cache(60, offhours_seconds=6 * 3600)
def limit_list(date_compact: str, kind: str = "U") -> list[dict] | None:
    df = _ak_limit_frame(date_compact, kind)
    if df is None:
        return None
    if getattr(df, "empty", True):
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        lianban = to_int(row.get("连板数"))
        if lianban is None:
            lianban = to_int(row.get("连续跌停"))
        if kind == "U" and not lianban:
            lianban = 1
        open_times = to_int(row.get("炸板次数"))
        if open_times is None:
            open_times = to_int(row.get("开板次数"))
        seal_amount = to_float(row.get("封板资金"))
        if seal_amount is None:
            seal_amount = to_float(row.get("封单资金"))
        out.append(
            {
                "code": _code6(row.get("代码")),
                "name": str(row.get("名称") or ""),
                "price": to_float(row.get("最新价")),
                "change_pct": to_float(row.get("涨跌幅")),
                "lianban": lianban,
                "open_times": open_times,
                "seal_amount": seal_amount,
                "amount": to_float(row.get("成交额")),
                "turnover_rate": to_float(row.get("换手率")),
                "first_time": _fmt_hhmmss(row.get("首次封板时间")),
                "last_time": _fmt_hhmmss(row.get("最后封板时间")),
                "industry": str(row.get("所属行业") or ""),
                "source": "公开涨跌停池",
            }
        )
    return out


@ttl_cache(1800)
def forecast_list(period: str) -> list[dict] | None:
    """业绩预告公开汇总。区间缺失时只给公开中值，不伪造上下限。"""
    ak = get_ak()
    fn = getattr(ak, "stock_yjyg_em", None) if ak else None
    if fn is None:
        return None
    df = safe(fn, date=period, label="业绩预告(公开源)", timeout=30)
    if df is None:
        return None
    if getattr(df, "empty", True):
        return []
    by_code: dict[str, dict] = {}
    for _, row in df.iterrows():
        metric = str(row.get("预测指标") or "")
        # 同一公司常同时出现归母与扣非两行；系统的基数陷阱/利润区间口径只接受归母净利润。
        if metric and ("归属于上市公司股东的净利润" not in metric or "扣除" in metric):
            continue
        text = str(row.get("业绩变动") or "")
        # 利润区间只取“同比/较上年”之前的预测子句。全文抓前两个金额会把
        # “同比减少1.48亿”误当成净利润上限。
        profit_clause = re.split(r"同比|较上年|比上年", text, maxsplit=1)[0]
        profit = to_float(row.get("预测数值"))
        last_net = to_float(row.get("上年同期值"))
        money_vals: list[float] = []
        for raw, unit in re.findall(r"(-?\d[\d,]*(?:\.\d+)?)\s*(万|亿)?元", profit_clause):
            value = to_float(raw)
            if value is None:
                continue
            money_vals.append(value * (1e8 if unit == "亿" else 1e4 if unit == "万" else 1.0))
        loss_words = ("亏损", "首亏", "续亏", "增亏", "减亏")
        is_loss = any(word in profit_clause or word in str(row.get("预告类型") or "") for word in loss_words)
        if is_loss:
            money_vals = [-abs(value) for value in money_vals]
        elif profit is not None and profit < 0:
            money_vals = [-abs(value) for value in money_vals]
        selected_money = money_vals[:2]
        net_min = min(selected_money) if selected_money else profit
        net_max = max(selected_money) if selected_money else profit

        pct_text = text[len(profit_clause):]
        pct_vals = [to_float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?)%", pct_text)]
        pct_vals = [x for x in pct_vals if x is not None]
        if pct_vals:
            if any(word in pct_text for word in ("下降", "降低", "减少", "下滑")):
                pct_vals = [-abs(value) for value in pct_vals]
            elif any(word in pct_text for word in ("增长", "上升", "增加")):
                pct_vals = [abs(value) for value in pct_vals]
        pct_fallback = to_float(row.get("业绩变动幅度"))
        record = {
                "code": _code6(row.get("股票代码")),
                "name": str(row.get("股票简称") or ""),
                "ann_date": _compact_from_iso(row.get("公告日期")),
                "type": str(row.get("预告类型") or ""),
                "p_chg_min": min(pct_vals) if pct_vals else pct_fallback,
                "p_chg_max": max(pct_vals) if pct_vals else pct_fallback,
                "net_min": net_min,
                "net_max": net_max,
                "last_net": last_net,
                "summary": text[:160],
                "reason": str(row.get("业绩变动原因") or "")[:240],
                "source": "公开业绩预告",
            }
        previous = by_code.get(record["code"])
        if previous is None or record["ann_date"] >= previous.get("ann_date", ""):
            by_code[record["code"]] = record
    return sorted(by_code.values(), key=lambda x: x.get("ann_date") or "", reverse=True)


@ttl_cache(120, fail_seconds=600, offhours_seconds=6 * 3600)
def ths_limit_up_pool(date_compact: str) -> dict:
    """同花顺公开涨停池的独立明细口径。

    该端点用于“涨停题材”页面，保留题材、板型、几天几板、封板成功率、回封、
    首封时间和封单额。它不把这些数字回填到 ``market_limitup`` 的东财/腾讯主口径，
    避免不同源的连板与封单数字被混成一套事实。
    """
    dc = str(date_compact or "").strip()
    out = {
        "ok": True,
        "date": dc,
        "data_date": _iso_from_compact(dc),
        "ts": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "missing": [],
        "stocks": [],
    }
    if not (len(dc) == 8 and dc.isdigit()):
        out["ok"] = False
        out["missing"].append("日期格式应为 YYYYMMDD")
        return out
    params = urllib.parse.urlencode(
        {
            "page": 1,
            # 同花顺公开端点硬上限为 200；300 会返回 status_code=-1。
            "limit": 200,
            "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004",
            "filter": "HS,GEM2STAR",
            "order_field": "330324",
            "order_type": "0",
            "date": date_compact,
        }
    )
    # 该公开端点的 HTTP 返回稳定；部分部署机 Python 对其 HTTPS 证书链会报
    # CERTIFICATE_VERIFY_FAILED，因此使用已经验证的 HTTP 路径。
    payload = _direct_json(
        f"http://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?{params}",
        label="涨停题材明细(同花顺)",
        headers={
            "Referer": "http://data.10jqka.com.cn/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        },
        timeout=8,
    )
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    info = (data or {}).get("info") if isinstance(data, dict) else None
    if not isinstance(info, list):
        out["ok"] = False
        out["missing"].append("同花顺公开涨停池当前不可达")
        return out
    total = to_int(((data or {}).get("page") or {}).get("total"))
    if total is not None and total > len(info):
        out["missing"].append(
            f"当日共 {total} 只，本次返回前 {len(info)} 只（公开接口单页上限 200）"
        )

    def _unix_time(value: object) -> str:
        try:
            return datetime.fromtimestamp(
                int(str(value or "").strip()), ZoneInfo("Asia/Shanghai")
            ).strftime("%H:%M")
        except (TypeError, ValueError, OSError, OverflowError):
            return ""

    for item in info:
        if not isinstance(item, dict):
            continue
        code = _code6(item.get("code"))
        if not code.isdigit() or code == "000000":
            continue
        success = to_float(item.get("limit_up_suc_rate"))
        success_pct = None
        if success is not None:
            success_pct = round(success * 100 if 0 <= success <= 1 else success, 1)
        out["stocks"].append(
            {
                "code": code,
                "name": str(item.get("name") or "").strip(),
                "price": to_float(item.get("latest")),
                "change_pct": to_float(item.get("change_rate")),
                "reason_type": str(item.get("reason_type") or "").strip(),
                "limit_up_type": str(item.get("limit_up_type") or "").strip(),
                "high_days": str(item.get("high_days") or "").strip(),
                "is_again": bool(to_int(item.get("is_again_limit"), 0)),
                "suc_rate_pct": success_pct,
                "open_num_ths": to_int(item.get("open_num")),
                "first_time": _unix_time(item.get("first_limit_up_time")),
                "last_time": _unix_time(item.get("last_limit_up_time")),
                "seal_amount": to_float(item.get("order_amount")),
                "seal_volume": to_float(item.get("order_volume")),
                "turnover_rate": to_float(item.get("turnover_rate")),
                "circ_mv": to_float(item.get("currency_value")),
            }
        )
    if not out["stocks"]:
        out["missing"].append("当日公开涨停池为空（非交易日、尚未开盘或当日无涨停）")
    out["notes"] = ["同花顺公开池口径：沪深、创业板、科创板，不含北交所"]
    return out


def _ths_limit_reasons(date_compact: str) -> dict[str, dict]:
    pool = ths_limit_up_pool(date_compact)
    out: dict[str, dict] = {}
    for item in pool.get("stocks") or []:
        out[item["code"]] = {
            "reason": item.get("reason_type") or "",
            "theme": item.get("reason_type") or "",
            "status": item.get("high_days") or "",
            "first_time": item.get("first_time") or "",
            "board_type": item.get("limit_up_type") or "",
        }
    return out


@ttl_cache(120, offhours_seconds=6 * 3600)
def kpl_list(date_compact: str, tag: str = "涨停") -> list[dict] | None:
    kind = {"涨停": "U", "炸板": "Z", "跌停": "D"}.get(tag, "U")
    rows = limit_list(date_compact, kind)
    if rows is None:
        return None
    reasons = _ths_limit_reasons(date_compact) if kind == "U" else {}
    out = []
    for row in rows:
        extra = reasons.get(row["code"], {})
        status = extra.get("status") or (f"{row.get('lianban')}连板" if row.get("lianban") else "")
        out.append(
            {
                **row,
                "lu_time": extra.get("first_time") or row.get("first_time") or "",
                "open_time": "",
                "last_time": row.get("last_time") or "",
                "reason": extra.get("reason") or row.get("industry") or "",
                "theme": extra.get("theme") or row.get("industry") or "",
                "status": status,
                "limit_order": row.get("seal_amount"),
                "net_change": None,
            }
        )
    return out
