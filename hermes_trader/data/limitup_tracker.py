"""涨停基准日的 T+1/T+2 追踪、冻结与补数。

快照使用按基准日分片的 JSONL 追加日志。已经完整冻结的基准池或追踪窗口不再
覆盖；不完整补数只追加覆盖率更高的 observation，避免把“后来拿到的数据”伪装成
当时已经存在的事实。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dt_time
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Callable

from . import free_source, market, stock
from .base import now_cn, trade_dates


_CLOSE_FREEZE_TIME = dt_time(15, 10)
_OPEN_TIME = dt_time(9, 25)
_SCHEMA = "limitup-tracker-v1"


def _float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _iso_ts(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _metric(
    value: float | None,
    state: str,
    *,
    sample: int = 0,
    total: int = 0,
    source: str | None = None,
    frozen_at: str | None = None,
) -> dict[str, Any]:
    labels = {
        "pending": "待收盘",
        "intraday": "盘中快照／未定稿",
        "final": "收盘定稿",
        "missing": "数据暂缺",
        "unavailable": "暂不可用",
        "not_applicable": "无样本",
    }
    return {
        "value": value,
        "state": state,
        "label": labels[state],
        "sample_size": sample,
        "total_size": total,
        "coverage": round(sample / total, 4) if total else None,
        "source": source,
        "frozen_at": frozen_at,
    }


class LimitUpTracker:
    """服务端唯一的交易日推导、业务计算和冻结入口。"""

    def __init__(
        self,
        data_dir: Path,
        *,
        calendar_fn: Callable[[], set[str] | None] = trade_dates,
        now_fn: Callable[[], datetime] = now_cn,
        limit_list_fn: Callable[[str, str], list[dict] | None] = free_source.limit_list,
        current_pool_fn: Callable[[str | None], dict] = market.market_limitup,
        quotes_fn: Callable[[list[str]], dict[str, dict]] = stock.quick_quotes,
        bars_fn: Callable[[str, int], list[dict]] = free_source.daily_bars,
    ) -> None:
        self.root = Path(data_dir) / "limitup-tracker"
        self._calendar_fn = calendar_fn
        self._now_fn = now_fn
        self._limit_list_fn = limit_list_fn
        self._current_pool_fn = current_pool_fn
        self._quotes_fn = quotes_fn
        self._bars_fn = bars_fn
        self._write_lock = threading.Lock()
        self._backfill_lock = threading.Lock()

    # --------------------------------------------------------------- persistence
    def _path(self, baseline_date: str) -> Path:
        return self.root / f"{baseline_date}.jsonl"

    def _events(self, baseline_date: str) -> list[dict[str, Any]]:
        path = self._path(baseline_date)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            try:
                event = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(event, dict) and event.get("schema") == _SCHEMA:
                out.append(event)
        return out

    def _append(self, baseline_date: str, event: dict[str, Any]) -> None:
        payload = {
            "schema": _SCHEMA,
            "baseline_date": baseline_date,
            "recorded_at": _iso_ts(self._now_fn()),
            **event,
        }
        raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._write_lock:
            self.root.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path(baseline_date), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                os.write(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)

    @staticmethod
    def _baseline_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
        return next((event for event in events if event.get("event") == "baseline_frozen"), None)

    @staticmethod
    def _window_event(events: list[dict[str, Any]], stage: str) -> dict[str, Any] | None:
        candidates = [
            event for event in events
            if event.get("event") == "window_observed" and event.get("stage") == stage
        ]
        if not candidates:
            return None
        finals = [event for event in candidates if event.get("final") is True]
        if finals:
            return finals[0]
        return max(candidates, key=lambda event: (int(event.get("available") or 0), event.get("recorded_at") or ""))

    # ---------------------------------------------------------------- calendar
    def _calendar(self) -> list[str]:
        values = self._calendar_fn()
        return sorted(value for value in (values or set()) if isinstance(value, str) and len(value) == 10)

    def _latest_baseline_date(self, calendar: list[str], now: datetime) -> str | None:
        if not calendar:
            return None
        today = now.date().isoformat()
        upper = today if now.time() >= _OPEN_TIME else "9999-99-99"
        if now.time() < _OPEN_TIME:
            candidates = [value for value in calendar if value < today]
        else:
            candidates = [value for value in calendar if value <= upper]
        return candidates[-1] if candidates else None

    @staticmethod
    def _next_dates(calendar: list[str], baseline_date: str) -> tuple[str | None, str | None]:
        following = [value for value in calendar if value > baseline_date]
        return (
            following[0] if following else None,
            following[1] if len(following) > 1 else None,
        )

    def dates(self, limit: int = 20) -> dict[str, Any]:
        now = self._now_fn()
        calendar = self._calendar()
        if not calendar:
            stored = sorted(path.stem for path in self.root.glob("*.jsonl")) if self.root.exists() else []
            recent = stored[-max(1, min(limit, 20)):][::-1]
            return {
                "ok": bool(recent),
                "dates": [self._date_item(value, now) for value in recent],
                "default_date": recent[0] if recent else None,
                "calendar": {"state": "missing", "source": "服务端交易日历不可用"},
                "missing": ["A股交易日历不可用；仅展示已存在的真实快照日期"],
                "generated_at": _iso_ts(now),
            }
        latest = self._latest_baseline_date(calendar, now)
        recent = [value for value in calendar if latest and value <= latest][-max(1, min(limit, 20)):][::-1]
        return {
            "ok": True,
            "dates": [self._date_item(value, now) for value in recent],
            "default_date": recent[0] if recent else None,
            "calendar": {"state": "available", "source": "服务端A股交易日历"},
            "missing": [],
            "generated_at": _iso_ts(now),
        }

    def _date_item(self, baseline_date: str, now: datetime) -> dict[str, Any]:
        events = self._events(baseline_date)
        baseline = self._baseline_event(events)
        calendar = self._calendar()
        t1, t2 = self._next_dates(calendar, baseline_date)
        due = [stage for stage, target in (("t1", t1), ("t2", t2)) if target and target < now.date().isoformat()]
        finals = [stage for stage in due if (self._window_event(events, stage) or {}).get("final") is True]
        if baseline is None:
            state = "intraday" if baseline_date == now.date().isoformat() else "missing"
        elif len(finals) == len(due):
            state = "complete" if t2 and t2 <= now.date().isoformat() else "tracking"
        else:
            state = "tracking"
        return {
            "date": baseline_date,
            "snapshot_state": state,
            "has_snapshot": baseline is not None,
            "missing_label": "快照缺失" if baseline is None and state != "intraday" else None,
        }

    # -------------------------------------------------------------- baseline pool
    @staticmethod
    def _normalize_rows(rows: list[dict], *, source: str) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row in rows:
            code = str(row.get("code") or "").split(".", 1)[0].zfill(6)
            if len(code) != 6 or not code.isdigit():
                continue
            lianban = row.get("lianban")
            try:
                lianban = int(lianban) if lianban is not None else None
            except (TypeError, ValueError):
                lianban = None
            normalized.append({
                "code": code,
                "name": str(row.get("name") or ""),
                "industry": str(row.get("industry") or "") or None,
                "lianban": lianban,
                "lianban_label": (
                    f"{lianban}连板" if lianban and lianban > 1 else "首板" if lianban == 1 else "板位待补"
                ),
                "baseline_change_pct": _float(row.get("change_pct")),
                "first_limit_time": str(row.get("first_time") or "") or None,
                "source": str(row.get("source") or source),
            })
        normalized.sort(key=lambda row: (-(row.get("lianban") or 0), row.get("first_limit_time") or "99:99:99", row["code"]))
        for position, row in enumerate(normalized, 1):
            row["position"] = position
        return normalized

    def _fetch_baseline(self, baseline_date: str) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
        now = self._now_fn()
        if baseline_date == now.date().isoformat() and now.time() < _CLOSE_FREEZE_TIME:
            raw = self._current_pool_fn(baseline_date.replace("-", ""))
            if not isinstance(raw, dict) or raw.get("ok") is False:
                return None, {"source": "当日涨停池", "reason": "当日涨停池接口暂不可用", "intraday": True}
            if str(raw.get("data_date") or "")[:10] != baseline_date:
                return None, {"source": "当日涨停池", "reason": "当日涨停池发生跨日降级，未用于本日快照", "intraday": True}
            rows = self._normalize_rows(list(raw.get("stocks") or []), source="当日涨停池")
            return rows, {
                "source": "当日涨停池盘中截面",
                "market_time": raw.get("ts"),
                "intraday": True,
                "notes": list(raw.get("missing") or []),
            }
        raw_rows = self._limit_list_fn(baseline_date.replace("-", ""), "U")
        if raw_rows is None:
            return None, {"source": "公开涨跌停池", "reason": "指定交易日涨停池未取得", "intraday": False}
        return self._normalize_rows(raw_rows, source="公开涨跌停池"), {
            "source": "公开涨跌停池",
            "market_time": baseline_date,
            "intraday": False,
            "notes": [],
        }

    def _ensure_baseline(self, baseline_date: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        events = self._events(baseline_date)
        frozen = self._baseline_event(events)
        if frozen:
            return frozen, {"frozen": True, "existing": True}
        try:
            rows, meta = self._fetch_baseline(baseline_date)
        except Exception as exc:  # noqa: BLE001 -- 数据边界显式转为 unavailable
            return None, {"frozen": False, "unavailable": True, "reason": str(exc)[:180]}
        if rows is None:
            return None, {"frozen": False, **meta}
        payload = {
            "event": "baseline_frozen" if not meta.get("intraday") else "baseline_intraday",
            "source": meta.get("source"),
            "market_time": meta.get("market_time"),
            "stocks": rows,
            "count": len(rows),
            "notes": meta.get("notes") or [],
        }
        if meta.get("intraday"):
            return payload, {"frozen": False, "intraday": True}
        self._append(baseline_date, payload)
        return {"schema": _SCHEMA, "baseline_date": baseline_date, **payload, "recorded_at": _iso_ts(self._now_fn())}, {"frozen": True, "existing": False}

    # --------------------------------------------------------------- T+ windows
    @staticmethod
    def _promotion(pool: list[dict] | None, baseline_count: int) -> float | None:
        """复用 market._promote_rate 的既有口径，但分母直接使用冻结基准池。"""
        if not pool or baseline_count <= 0:
            return None
        values: list[int] = []
        for row in pool:
            try:
                values.append(int(row.get("lianban")))
            except (TypeError, ValueError):
                continue
        if not values:
            return None
        return round(sum(1 for value in values if value >= 2) / baseline_count * 100, 1)

    @staticmethod
    def _stats(changes: dict[str, dict], total: int) -> tuple[float | None, float | None, int]:
        values = [_float(row.get("change_pct")) for row in changes.values()]
        clean = [value for value in values if value is not None]
        if not clean:
            return None, None, 0
        return round(sum(clean) / len(clean), 2), round(sum(1 for value in clean if value > 0) / len(clean) * 100, 1), len(clean)

    def _observe_today(
        self,
        baseline_date: str,
        baseline: dict[str, Any],
        stage: str,
        target_date: str,
    ) -> tuple[dict[str, Any] | None, str]:
        codes = [row["code"] for row in baseline.get("stocks") or []]
        if not codes:
            return {
                "event": "window_observed", "stage": stage, "target_date": target_date,
                "changes": {}, "available": 0, "total": 0, "average": None, "red_rate": None,
                "promotion_rate": None, "source": "无样本", "final": True,
            }, "final"
        try:
            quotes = self._quotes_fn(codes)
        except Exception:  # noqa: BLE001
            return None, "unavailable"
        changes: dict[str, dict] = {}
        for code in codes:
            quote = quotes.get(code) or {}
            value = _float(quote.get("change_pct"))
            if value is not None:
                changes[code] = {
                    "change_pct": value,
                    "source": quote.get("source") or "批量实时行情",
                    "market_time": quote.get("as_of"),
                }
        average, red_rate, available = self._stats(changes, len(codes))
        now = self._now_fn()
        final_candidate = now.time() >= _CLOSE_FREEZE_TIME
        pool: list[dict] | None = None
        promotion = None
        if stage == "t1":
            try:
                pool = self._limit_list_fn(target_date.replace("-", ""), "U")
            except Exception:  # noqa: BLE001
                pool = None
            promotion = self._promotion(pool, len(codes))
        complete = available == len(codes) and (stage != "t1" or promotion is not None)
        observation = {
            "event": "window_observed",
            "stage": stage,
            "target_date": target_date,
            "changes": changes,
            "available": available,
            "total": len(codes),
            "average": average,
            "red_rate": red_rate,
            "promotion_rate": promotion,
            "source": "批量实时行情" + ("＋公开涨跌停池" if stage == "t1" else ""),
            "market_time": _iso_ts(now),
            "final": bool(final_candidate and complete),
            "missing": [] if complete else [f"{stage.upper()} 行情覆盖 {available}/{len(codes)}"],
        }
        if observation["final"]:
            self._append(baseline_date, observation)
            observation["recorded_at"] = _iso_ts(now)
            return observation, "final"
        return observation, "missing" if final_candidate else "intraday"

    def _window(
        self,
        baseline_date: str,
        baseline: dict[str, Any],
        stage: str,
        target_date: str | None,
    ) -> tuple[dict[str, Any] | None, str]:
        if not target_date:
            return None, "unavailable"
        events = self._events(baseline_date)
        saved = self._window_event(events, stage)
        if saved and saved.get("final") is True:
            return saved, "final"
        today = self._now_fn().date().isoformat()
        if target_date > today or (target_date == today and self._now_fn().time() < _OPEN_TIME):
            return saved, "pending"
        if target_date == today:
            return self._observe_today(baseline_date, baseline, stage, target_date)
        return saved, "missing"

    def _metric_from_window(
        self,
        window: dict[str, Any] | None,
        state: str,
        field: str,
        total: int,
    ) -> dict[str, Any]:
        if total == 0:
            return _metric(None, "not_applicable", total=0, source="冻结基准池无样本")
        if state in {"pending", "unavailable"}:
            return _metric(None, state, total=total)
        available = int((window or {}).get("available") or 0)
        value = _float((window or {}).get(field))
        metric_state = "final" if state == "final" and value is not None else "intraday" if state == "intraday" and value is not None else "missing"
        return _metric(
            value if metric_state in {"final", "intraday"} else None,
            metric_state,
            sample=available,
            total=total,
            source=(window or {}).get("source"),
            frozen_at=(window or {}).get("recorded_at") if metric_state == "final" else None,
        )

    # ---------------------------------------------------------------- response
    def snapshot(self, baseline_date: str) -> dict[str, Any]:
        now = self._now_fn()
        calendar = self._calendar()
        stored = self._path(baseline_date).exists()
        if len(baseline_date) != 10 or (calendar and baseline_date not in calendar) or (not calendar and not stored):
            return {
                "ok": False,
                "selected_date": baseline_date,
                "error": "日期不在服务端交易日历中",
                "missing": ["无法确认该日期为A股交易日"],
            }
        baseline, baseline_meta = self._ensure_baseline(baseline_date)
        t1_date, t2_date = self._next_dates(calendar, baseline_date)
        if not calendar:
            saved_events = self._events(baseline_date)
            t1_date = str((self._window_event(saved_events, "t1") or {}).get("target_date") or "") or None
            t2_date = str((self._window_event(saved_events, "t2") or {}).get("target_date") or "") or None
        if baseline is None:
            state = "unavailable" if baseline_meta.get("unavailable") else "missing"
            empty_metric = _metric(None, state)
            return {
                "ok": True,
                "selected_date": baseline_date,
                "baseline_date": baseline_date,
                "baseline_count": None,
                "tracking_status": "暂不可用" if state == "unavailable" else "快照缺失",
                "t1_date": t1_date,
                "t2_date": t2_date,
                "metrics": {key: dict(empty_metric) for key in ("t1_average", "t1_red_rate", "t1_promotion_rate", "t2_average", "t2_red_rate")},
                "stocks": [],
                "generated_at": _iso_ts(now),
                "market_time": None,
                "data_date": baseline_date,
                "sources": [baseline_meta.get("source") or "涨停池"],
                "is_intraday": False,
                "is_final": False,
                "missing": [baseline_meta.get("reason") or "基准日涨停快照缺失"],
                "degraded_reasons": [],
                "coverage": {"baseline": None, "t1": None, "t2": None},
            }
        stocks = list(baseline.get("stocks") or [])
        total = len(stocks)
        t1, t1_state = self._window(baseline_date, baseline, "t1", t1_date)
        t2, t2_state = self._window(baseline_date, baseline, "t2", t2_date)
        metrics = {
            "t1_average": self._metric_from_window(t1, t1_state, "average", total),
            "t1_red_rate": self._metric_from_window(t1, t1_state, "red_rate", total),
            "t1_promotion_rate": self._metric_from_window(t1, t1_state, "promotion_rate", total),
            "t2_average": self._metric_from_window(t2, t2_state, "average", total),
            "t2_red_rate": self._metric_from_window(t2, t2_state, "red_rate", total),
        }
        merged: list[dict[str, Any]] = []
        for row in stocks:
            code = row["code"]
            merged.append({
                **row,
                "t1": self._row_window((t1 or {}).get("changes", {}).get(code), t1_state, t1_date),
                "t2": self._row_window((t2 or {}).get("changes", {}).get(code), t2_state, t2_date),
            })
        states = [metric["state"] for metric in metrics.values()]
        if "intraday" in states:
            tracking_status = "盘中追踪／未定稿"
        elif all(state in {"final", "not_applicable"} for state in states):
            tracking_status = "追踪结果已定稿"
        elif "missing" in states:
            tracking_status = "部分数据暂缺"
        else:
            tracking_status = "等待后续交易日"
        missing = list(baseline.get("notes") or [])
        for window in (t1, t2):
            missing.extend(list((window or {}).get("missing") or []))
        return {
            "ok": True,
            "selected_date": baseline_date,
            "baseline_date": baseline_date,
            "baseline_count": total,
            "tracking_status": tracking_status,
            "t1_date": t1_date,
            "t2_date": t2_date,
            "metrics": metrics,
            "stocks": merged,
            "generated_at": _iso_ts(now),
            "market_time": baseline.get("market_time"),
            "data_date": baseline_date,
            "sources": list(dict.fromkeys(filter(None, [baseline.get("source"), (t1 or {}).get("source"), (t2 or {}).get("source")]))),
            "is_intraday": bool(baseline_meta.get("intraday") or "intraday" in states),
            "is_final": all(state in {"final", "not_applicable"} for state in states) and not baseline_meta.get("intraday"),
            "missing": list(dict.fromkeys(filter(None, missing))),
            "degraded_reasons": list(dict.fromkeys(filter(None, missing))),
            "coverage": {
                "baseline": {"available": total, "total": total},
                "t1": {"available": int((t1 or {}).get("available") or 0), "total": total},
                "t2": {"available": int((t2 or {}).get("available") or 0), "total": total},
            },
        }

    @staticmethod
    def _row_window(change: dict[str, Any] | None, state: str, target_date: str | None) -> dict[str, Any]:
        value = _float((change or {}).get("change_pct"))
        row_state = "final" if value is not None and state in {"final", "missing"} else "intraday" if state == "intraday" and value is not None else state if state in {"pending", "unavailable"} else "missing"
        labels = {"final": "收盘定稿", "intraday": "盘中", "pending": "待追踪", "missing": "数据暂缺", "unavailable": "暂不可用"}
        return {
            "date": target_date,
            "change_pct": value if row_state in {"final", "intraday"} else None,
            "state": row_state,
            "label": labels[row_state],
            "source": (change or {}).get("source"),
            "market_time": (change or {}).get("market_time"),
        }

    # ---------------------------------------------------------------- backfill
    def _bars_change(self, code: str, target_date: str, previous_date: str) -> dict[str, Any] | None:
        rows = self._bars_fn(code, 80) or []
        by_date = {str(row.get("d") or "")[:10]: row for row in rows}
        target = by_date.get(target_date)
        previous = by_date.get(previous_date)
        close = _float((target or {}).get("c"))
        previous_close = _float((previous or {}).get("c"))
        if close is None or previous_close is None or previous_close <= 0:
            return None
        return {
            "change_pct": round((close / previous_close - 1) * 100, 2),
            "source": (target or {}).get("source") or "腾讯财经·前复权日K",
            "market_time": target_date,
        }

    def _backfill_window(
        self,
        baseline_date: str,
        baseline: dict[str, Any],
        stage: str,
        target_date: str,
        calendar: list[str],
    ) -> dict[str, Any]:
        events = self._events(baseline_date)
        saved = self._window_event(events, stage)
        if saved and saved.get("final") is True:
            return {"status": "noop", "stage": stage, "available": saved.get("available", 0)}
        prior = [value for value in calendar if value < target_date]
        if not prior:
            return {"status": "failed", "stage": stage, "reason": "目标日前一交易日不可确认"}
        previous_date = prior[-1]
        codes = [row["code"] for row in baseline.get("stocks") or []]
        changes: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="limitup-backfill") as pool:
            futures = {pool.submit(self._bars_change, code, target_date, previous_date): code for code in codes}
            for future in as_completed(futures):
                try:
                    value = future.result()
                except Exception:  # noqa: BLE001
                    value = None
                if value is not None:
                    changes[futures[future]] = value
        average, red_rate, available = self._stats(changes, len(codes))
        promotion = None
        if stage == "t1":
            try:
                target_pool = self._limit_list_fn(target_date.replace("-", ""), "U")
            except Exception:  # noqa: BLE001
                target_pool = None
            promotion = self._promotion(target_pool, len(codes))
        complete = available == len(codes) and (stage != "t1" or promotion is not None)
        observation = {
            "event": "window_observed", "stage": stage, "target_date": target_date,
            "changes": changes, "available": available, "total": len(codes),
            "average": average, "red_rate": red_rate, "promotion_rate": promotion,
            "source": "腾讯财经·前复权日K" + ("＋公开涨跌停池" if stage == "t1" else ""),
            "market_time": target_date, "final": complete,
            "missing": [] if complete else [f"{stage.upper()} 历史行情覆盖 {available}/{len(codes)}"],
        }
        previous_available = int((saved or {}).get("available") or -1)
        if complete or available > previous_available:
            self._append(baseline_date, observation)
        return {
            "status": "complete" if complete else "partial" if available else "failed",
            "stage": stage, "available": available, "total": len(codes),
            "reason": None if complete else observation["missing"][0],
        }

    def backfill_recent(self, limit: int = 20) -> dict[str, Any]:
        if not self._backfill_lock.acquire(blocking=False):
            return {"ok": False, "status": "failed", "reason": "已有补数据任务正在执行"}
        try:
            dates_payload = self.dates(limit)
            dates = [item["date"] for item in dates_payload.get("dates") or []]
            if not dates:
                return {"ok": False, "status": "failed", "reason": "服务端交易日历及快照日期均不可用"}
            calendar = self._calendar()
            today = self._now_fn().date().isoformat()
            results: list[dict[str, Any]] = []
            for baseline_date in dates:
                baseline, meta = self._ensure_baseline(baseline_date)
                if baseline is None or meta.get("intraday"):
                    results.append({"date": baseline_date, "scope": "baseline", "status": "failed", "reason": meta.get("reason") or "基准池尚未收盘定稿"})
                    continue
                results.append({
                    "date": baseline_date,
                    "scope": "baseline",
                    "status": "noop" if meta.get("existing") else "complete",
                })
                t1_date, t2_date = self._next_dates(calendar, baseline_date)
                for stage, target in (("t1", t1_date), ("t2", t2_date)):
                    target_due = bool(
                        target and (
                            target < today
                            or (target == today and self._now_fn().time() >= _CLOSE_FREEZE_TIME)
                        )
                    )
                    if not target_due:
                        continue
                    results.append({"date": baseline_date, "scope": stage, **self._backfill_window(baseline_date, baseline, stage, target, calendar)})
            changed = [item for item in results if item.get("status") in {"complete", "partial"}]
            failures = [item for item in results if item.get("status") == "failed"]
            if not changed and not failures:
                status = "noop"
            elif failures:
                status = "partial" if changed else "failed"
            else:
                status = "complete"
            return {
                "ok": status in {"complete", "partial", "noop"},
                "status": status,
                "summary": {
                    "completed": sum(item.get("status") == "complete" for item in results),
                    "partial": sum(item.get("status") == "partial" for item in results),
                    "failed": len(failures),
                    "noop": sum(item.get("status") == "noop" for item in results),
                },
                "results": results,
                "reason": (
                    "没有已到期且可补的数据" if status == "noop" else
                    "部分历史源未返回，缺失保持缺失" if status == "partial" else
                    "历史涨停池或前复权日K未取得" if status == "failed" else None
                ),
                "finished_at": _iso_ts(self._now_fn()),
            }
        finally:
            self._backfill_lock.release()
