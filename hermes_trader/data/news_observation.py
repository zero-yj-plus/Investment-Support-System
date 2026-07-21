"""A 股新闻事件的点时观察账本。

映射和观察严格分层：映射只说明公司/板块关系；观察只记录事件后盘面事实，
不判断利多利空，也不把同步涨跌解释成新闻因果。

账本采用每客户独立的 append-only JSONL：事件与观察均不覆盖。页面/API 只读账本，
只有后台采样任务会追加记录，避免“用户打开页面”改变研究结果。
"""
from __future__ import annotations

from datetime import date, datetime, time
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Iterable

from . import tdx_api_source as tdx
from .base import CN_TZ, now_cn, trade_dates
from .news_mapping import sector_observation


EVENT_SCHEMA = "a-share-news-event-v1"
OBSERVATION_SCHEMA = "a-share-news-observation-v1"
_EVENT_TYPE = "a_share_news_event"
_OBSERVATION_TYPE = "a_share_market_observation"
_LOCK = threading.RLock()
_store: "NewsObservationStore | None" = None
_CLOSE_CAPTURE_AT = time(15, 5)
_LIVE_CAPTURE_AT = time(10, 30)
_MAX_OPEN_EVENTS = 500


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _parse_event_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    match = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)", raw)
    if not match:
        return None
    try:
        return datetime.fromisoformat(f"{match.group(1)} {match.group(2)}").replace(tzinfo=CN_TZ)
    except ValueError:
        return None


def _frame_date(frame: dict | None) -> date | None:
    raw = str((frame or {}).get("data_date") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _strict_target_trade_day(event_time: datetime) -> tuple[str | None, str]:
    """严格使用交易日集合；日历不可用时不退化成“工作日=T+1”。"""
    dates = trade_dates()
    if not dates:
        return None, "calendar_unavailable"
    event_day = event_time.date().isoformat()
    same_session = event_day in dates and event_time.time() < time(15, 0)
    if same_session:
        return event_day, "same_session"
    candidates = sorted(day for day in dates if day > event_day)
    return (candidates[0], "next_session") if candidates else (None, "calendar_out_of_range")


def _safe_matches(item: dict) -> list[dict]:
    mapping = item.get("a_share") if isinstance(item.get("a_share"), dict) else {}
    rows = mapping.get("matches") if isinstance(mapping.get("matches"), list) else []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("kind") not in {"stock", "industry", "concept"}:
            continue
        out.append({
            "kind": str(row.get("kind") or ""),
            "code": str(row.get("code") or "")[:24],
            "name": str(row.get("name") or "")[:80],
            "match_by": str(row.get("match_by") or "")[:48],
            "name_consistency": str(row.get("name_consistency") or "")[:24],
        })
    return out


def _event_record(item: dict, observed_at: datetime) -> dict | None:
    mapping = item.get("a_share") if isinstance(item.get("a_share"), dict) else {}
    if mapping.get("state") != "mapped" or not item.get("event_id"):
        return None
    event_time = _parse_event_time(item.get("time"))
    if event_time is None:
        target, basis, reason = None, "unknown", "event_time_incomplete"
    else:
        target, basis = _strict_target_trade_day(event_time)
        reason = "" if target else basis
        if target:
            target_close = datetime.combine(date.fromisoformat(target), _CLOSE_CAPTURE_AT, tzinfo=CN_TZ)
            if observed_at > target_close:
                reason = "discovered_after_observation_window"
    return {
        "schema_version": EVENT_SCHEMA,
        "event_type": _EVENT_TYPE,
        "event_id": str(item.get("event_id"))[:80],
        "title": str(item.get("title") or "")[:500],
        "event_time": event_time.isoformat() if event_time else None,
        "first_seen_at": observed_at.isoformat(),
        "target_trade_date": target,
        "observation_basis": basis,
        "initial_reason_code": reason or None,
        "mapping_level": str(mapping.get("level") or "")[:48],
        "matches": _safe_matches(item),
        "score_weight": 0,
    }


def _checkpoint_id(event_id: str, checkpoint: str, suffix: str = "recorded") -> str:
    raw = f"{event_id}\x1f{checkpoint}\x1f{suffix}"
    return "no_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _scheduled_for(event: dict, checkpoint: str) -> datetime | None:
    if checkpoint == "first_seen":
        return _parse_event_time(event.get("first_seen_at"))
    raw = str(event.get("target_trade_date") or "")
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return None
    at = _LIVE_CAPTURE_AT if checkpoint == "target_1030" else _CLOSE_CAPTURE_AT
    return datetime.combine(day, at, tzinfo=CN_TZ)


def _snapshot(
    event: dict,
    frame: dict | None,
    members_by_sector: dict[str, Iterable[str]],
    names: dict[str, str],
    baseline: dict | None = None,
    use_prev_close: bool = False,
) -> tuple[dict, list[str]]:
    frame_stocks = dict((frame or {}).get("stocks") or {})
    stocks: list[dict] = []
    blocks: list[dict] = []
    for match in event.get("matches") or []:
        if match.get("kind") == "stock":
            quote = frame_stocks.get(str(match.get("code") or ""))
            selected_price = quote.get("prev_close") if isinstance(quote, dict) and use_prev_close else (
                quote.get("price") if isinstance(quote, dict) else None
            )
            if isinstance(quote, dict) and selected_price is not None:
                row = {
                    "code": match.get("code"), "name": match.get("name"),
                    "price": selected_price,
                    "change_pct": None if use_prev_close else quote.get("change_pct"),
                }
                base_rows = {str(x.get("code") or ""): x for x in (baseline or {}).get("stocks") or []}
                base_price = (base_rows.get(str(match.get("code") or "")) or {}).get("price")
                if base_price not in (None, 0):
                    row["since_baseline_pct"] = round((float(row["price"]) / float(base_price) - 1) * 100, 2)
                stocks.append(row)
        elif match.get("kind") in {"industry", "concept"} and match.get("code"):
            blocks.append(match)
    sectors = [] if use_prev_close else [
        observation for block in blocks
        if (observation := sector_observation(block, members_by_sector, frame_stocks, names))
    ]
    expected = len([m for m in event.get("matches") or [] if m.get("kind") == "stock"]) + len(blocks)
    complete = len(stocks) + len(sectors)
    missing: list[str] = []
    if not frame:
        missing.append("pricevol 全市场完整帧不可用")
    elif complete < expected:
        missing.append(f"{expected - complete}个映射对象缺少可比行情或成分覆盖")
    return {
        "data_date": (frame or {}).get("data_date"),
        "market_as_of": (frame or {}).get("as_of"),
        "source_as_of_precision": "date",
        "stocks": stocks,
        "sectors": sectors,
        "coverage": {"complete": complete, "total": expected},
    }, missing


class NewsObservationStore:
    """事件/观察分离的只追加 JSONL 账本。"""

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir) / "news_events"
        self.events_path = self.root / "events.v1.jsonl"
        self.observations_path = self.root / "observations.v1.jsonl"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    @staticmethod
    def _read(path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows: list[dict] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows

    @staticmethod
    def _append(path: Path, record: dict) -> dict:
        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("新闻事件账本追加失败")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return record

    def events(self) -> list[dict]:
        with _LOCK:
            return self._read(self.events_path)

    def observations(self) -> list[dict]:
        with _LOCK:
            return self._read(self.observations_path)

    def append_event(self, record: dict) -> dict:
        if record.get("schema_version") != EVENT_SCHEMA or record.get("event_type") != _EVENT_TYPE:
            raise ValueError("新闻事件 schema 不受支持")
        event_id = str(record.get("event_id") or "")
        if not event_id:
            raise ValueError("新闻事件缺少 event_id")
        with _LOCK:
            for existing in self._read(self.events_path):
                if existing.get("event_id") == event_id:
                    return existing
            return self._append(self.events_path, record)

    def append_observation(self, record: dict) -> dict:
        if record.get("schema_version") != OBSERVATION_SCHEMA or record.get("event_type") != _OBSERVATION_TYPE:
            raise ValueError("新闻观察 schema 不受支持")
        observation_id = str(record.get("observation_id") or "")
        if not observation_id:
            raise ValueError("新闻观察缺少 observation_id")
        with _LOCK:
            for existing in self._read(self.observations_path):
                if existing.get("observation_id") != observation_id:
                    continue
                if existing != record:
                    raise ValueError("observation_id 已存在且内容不一致")
                return existing
            return self._append(self.observations_path, record)


def init(data_dir: str | Path) -> NewsObservationStore:
    global _store
    _store = NewsObservationStore(data_dir)
    return _store


def available() -> bool:
    return _store is not None


def _checkpoint_success(observations: list[dict], event_id: str, checkpoint: str) -> dict | None:
    rows = [
        row for row in observations
        if row.get("event_id") == event_id and row.get("checkpoint") == checkpoint
        and row.get("status") == "recorded"
    ]
    return rows[-1] if rows else None


def _checkpoint_terminal_missing(
    observations: list[dict], event_id: str, checkpoint: str,
) -> dict | None:
    """返回已经不可能靠后续重试补齐的缺失检查点。

    过去对“目标交易日已错过”的几百条事件每 10 分钟重复追加 missing，
    任务表面成功但日志持续出现数百缺口。临时上游失败仍允许重试；只有观察
    窗口已经永久错过时才终止重复写入。
    """
    terminal_reasons = {"target_frame_missed", "baseline_frame_missed"}
    rows = [
        row for row in observations
        if row.get("event_id") == event_id
        and row.get("checkpoint") == checkpoint
        and row.get("status") == "missing"
        and row.get("reason_code") in terminal_reasons
    ]
    return rows[-1] if rows else None


def _append_checkpoint(
    store: NewsObservationStore,
    event: dict,
    checkpoint: str,
    observed_at: datetime,
    frame: dict | None,
    members_by_sector: dict[str, Iterable[str]],
    names: dict[str, str],
    prior_observations: list[dict],
) -> dict:
    scheduled = _scheduled_for(event, checkpoint) or observed_at
    baseline_row = _checkpoint_success(prior_observations, str(event["event_id"]), "first_seen")
    baseline = (baseline_row or {}).get("snapshot") if baseline_row else None
    frame_day = _frame_date(frame)
    target_day = _frame_date({"data_date": event.get("target_trade_date")})
    use_prev_close = (
        checkpoint == "first_seen"
        and event.get("observation_basis") == "next_session"
        and frame_day is not None and target_day is not None and frame_day == target_day
    )
    snapshot, missing = _snapshot(
        event, frame, members_by_sector, names, baseline, use_prev_close=use_prev_close,
    )
    expected_day = (
        target_day
        if checkpoint != "first_seen" else frame_day
    )
    status = "recorded"
    reason_code = None
    if not frame:
        status, reason_code = "missing", "market_frame_unavailable"
    elif checkpoint != "first_seen" and frame_day != expected_day:
        status = "missing"
        reason_code = "target_frame_not_available" if frame_day and expected_day and frame_day < expected_day else "target_frame_missed"
        missing.append("当前完整行情帧不属于目标交易日")
    if checkpoint == "first_seen" and frame_day and target_day and frame_day > target_day:
        status, reason_code = "missing", "baseline_frame_missed"
        missing.append("首次采集时目标观察窗口已经结束")
    suffix = "recorded" if status == "recorded" else f"{reason_code}:{observed_at.strftime('%Y%m%d%H%M')}"
    record = {
        "schema_version": OBSERVATION_SCHEMA,
        "event_type": _OBSERVATION_TYPE,
        "observation_id": _checkpoint_id(str(event["event_id"]), checkpoint, suffix),
        "event_id": event["event_id"],
        "checkpoint": checkpoint,
        "scheduled_for": scheduled.isoformat(),
        "observed_at": observed_at.isoformat(),
        "lateness_seconds": max(0, int((observed_at - scheduled).total_seconds())),
        "status": status,
        "reason_code": reason_code,
        "snapshot": snapshot if status == "recorded" else None,
        "coverage": snapshot.get("coverage") or {"complete": 0, "total": 0},
        "missing": list(dict.fromkeys(missing)),
        "causality": "not_inferred",
        "direction": "unknown",
        "score_weight": 0,
    }
    return store.append_observation(record)


def capture_mapped_items(
    items: list[dict],
    *,
    observed_at: datetime | None = None,
    frame: dict | None = None,
    members_by_sector: dict[str, Iterable[str]] | None = None,
    names: dict[str, str] | None = None,
) -> dict:
    """后台采样入口。即使事件已滚出当前新闻列表，也继续补齐到期检查点。"""
    if _store is None:
        return {"ok": False, "error_code": "observation_store_not_initialized"}
    now = observed_at or now_cn()
    if now.tzinfo is None:
        now = now.replace(tzinfo=CN_TZ)
    store = _store
    existing = {str(row.get("event_id") or ""): row for row in store.events()}
    created = 0
    for item in items:
        event_id = str(item.get("event_id") or "")
        if not event_id or event_id in existing:
            continue
        record = _event_record(item, now)
        if record:
            existing[event_id] = store.append_event(record)
            created += 1

    frame = frame if frame is not None else tdx.pricevol_snapshot()
    names = names if names is not None else (tdx.stock_universe() or {})
    events = list(existing.values())[-_MAX_OPEN_EVENTS:]
    observations = store.observations()
    open_events = [
        event for event in events
        if not _checkpoint_success(observations, str(event.get("event_id") or ""), "target_close")
    ]
    block_codes = {
        str(match.get("code") or "") for event in open_events for match in event.get("matches") or []
        if match.get("kind") in {"industry", "concept"} and match.get("code")
    }
    if members_by_sector is None:
        memberships = tdx.sector_members_for_codes(sorted(block_codes)) or {}
        members_by_sector = dict(memberships.get("by_sector") or {})
    captured = 0
    missing = 0
    for event in open_events:
        event_id = str(event.get("event_id") or "")
        if (
            not _checkpoint_success(observations, event_id, "first_seen")
            and not _checkpoint_terminal_missing(observations, event_id, "first_seen")
        ):
            row = _append_checkpoint(store, event, "first_seen", now, frame, members_by_sector, names, observations)
            observations.append(row)
            captured += row.get("status") == "recorded"
            missing += row.get("status") != "recorded"
        target_raw = str(event.get("target_trade_date") or "")
        try:
            target = date.fromisoformat(target_raw)
        except ValueError:
            continue
        if now.date() < target:
            continue
        if (
            now.date() == target and _LIVE_CAPTURE_AT <= now.time() < _CLOSE_CAPTURE_AT
            and not _checkpoint_success(observations, event_id, "target_1030")
        ):
            row = _append_checkpoint(store, event, "target_1030", now, frame, members_by_sector, names, observations)
            observations.append(row)
            captured += row.get("status") == "recorded"
            missing += row.get("status") != "recorded"
        if (
            (now.date() > target or now.time() >= _CLOSE_CAPTURE_AT)
            and not _checkpoint_success(observations, event_id, "target_close")
            and not _checkpoint_terminal_missing(observations, event_id, "target_close")
        ):
            row = _append_checkpoint(store, event, "target_close", now, frame, members_by_sector, names, observations)
            observations.append(row)
            captured += row.get("status") == "recorded"
            missing += row.get("status") != "recorded"
    summary = observation_summary(events=store.events(), observations=store.observations(), now=now, frame=frame)
    return {"ok": True, "created": created, "captured": captured, "missing": missing, "summary": summary}


def _contract(event: dict, observations: list[dict], now: datetime, frame: dict | None) -> dict:
    event_id = str(event.get("event_id") or "")
    rows = [row for row in observations if row.get("event_id") == event_id]
    successes = {key: _checkpoint_success(rows, event_id, key) for key in ("first_seen", "target_1030", "target_close")}
    latest = next((row for row in reversed(rows) if row.get("status") == "recorded"), None)
    target_raw = str(event.get("target_trade_date") or "")
    try:
        target = date.fromisoformat(target_raw)
    except ValueError:
        target = None
    close_missing = next((row for row in reversed(rows) if row.get("checkpoint") == "target_close" and row.get("status") == "missing"), None)
    if event.get("initial_reason_code"):
        status, label, reason = "incomparable", "不可比 · 交易日或事件时间不可用", event.get("initial_reason_code")
    elif successes["target_close"] and successes["first_seen"]:
        status, label, reason = "observed", "已观察 · 截至目标日收盘", None
    elif successes["target_close"] and not successes["first_seen"]:
        status, label, reason = "incomparable", "不可比 · 首次基准缺失", "baseline_missing"
    elif close_missing and close_missing.get("reason_code") == "target_frame_missed":
        status, label, reason = "incomparable", "不可比 · 已错过目标交易日完整帧", "target_frame_missed"
    elif target and now.date() >= target and (successes["target_1030"] or (now.date() == target and now.time() >= _LIVE_CAPTURE_AT)):
        status, label, reason = "observing", "观察中", None
    else:
        status, label, reason = "waiting", "待观察", "awaiting_target_trade_date"
    checkpoints = []
    for key, name in (("first_seen", "首次基准"), ("target_1030", "目标日10:30"), ("target_close", "目标日收盘")):
        success = successes[key]
        latest_attempt = next(
            (row for row in reversed(rows) if row.get("checkpoint") == key),
            None,
        )
        checkpoint_status = (
            "complete" if success
            else "missing" if latest_attempt and latest_attempt.get("status") == "missing"
            else "not_due" if (_scheduled_for(event, key) or now) > now
            else "pending"
        )
        checkpoints.append({
            "key": key, "name": name,
            "status": checkpoint_status,
            "scheduled_for": (_scheduled_for(event, key) or now).isoformat(),
            "observed_at": (success or latest_attempt or {}).get("observed_at"),
            "reason_code": None if success else (latest_attempt or {}).get("reason_code"),
            "lateness_seconds": (success or latest_attempt or {}).get("lateness_seconds"),
            "market_as_of": (
                ((success or {}).get("snapshot") or {}).get("market_as_of")
                if success else None
            ),
            "as_of_precision": (
                ((success or {}).get("snapshot") or {}).get("source_as_of_precision")
                if success else None
            ),
        })
    complete = sum(1 for item in checkpoints if item["status"] == "complete")
    result = successes["target_close"]
    latest_snapshot = (latest or {}).get("snapshot") if latest else None
    result_snapshot = (result or {}).get("snapshot") if result else None
    coverage = (result or latest or {}).get("coverage") if (result or latest) else {"complete": 0, "total": len(event.get("matches") or [])}
    return {
        "status": status,
        "label": label,
        "reason_code": reason,
        "target_trade_date": target_raw or None,
        "progress": {"complete": complete, "total": 3},
        "checkpoints": checkpoints,
        "baseline": (successes["first_seen"] or {}).get("snapshot"),
        "latest": latest_snapshot,
        "result": result_snapshot,
        "coverage": coverage,
        "missing": list(dict.fromkeys([
            *list((result or latest or {}).get("missing") or []),
            *[
                str(row.get("reason_code"))
                for row in checkpoints
                if row.get("status") == "missing" and row.get("reason_code")
            ],
        ])),
        "causality": "not_inferred",
        "direction": "unknown",
        "score_weight": 0,
    }


def observation_summary(
    *, events: list[dict] | None = None, observations: list[dict] | None = None,
    now: datetime | None = None, frame: dict | None = None,
) -> dict:
    if _store is None:
        return {"enabled": False, "state": "disabled", "tracked": 0}
    events = events if events is not None else _store.events()
    observations = observations if observations is not None else _store.observations()
    current = now or now_cn()
    counts = {"waiting": 0, "observing": 0, "observed": 0, "incomparable": 0}
    contracts = []
    for event in events:
        contract = _contract(event, observations, current, frame)
        counts[contract["status"]] += 1
        contracts.append(contract)
    last_capture = max((str(row.get("observed_at") or "") for row in observations), default="") or None
    checkpoint_keys = {
        (str(row.get("event_id") or ""), str(row.get("checkpoint") or ""))
        for row in observations if row.get("event_id") and row.get("checkpoint")
    }
    recorded_keys = {
        (str(row.get("event_id") or ""), str(row.get("checkpoint") or ""))
        for row in observations if row.get("status") == "recorded"
    }
    terminal_missing_keys = {
        (str(row.get("event_id") or ""), str(row.get("checkpoint") or ""))
        for row in observations
        if row.get("status") == "missing"
        and row.get("reason_code") in {"target_frame_missed", "baseline_frame_missed"}
    }
    return {
        "enabled": True, "state": "active", "tracked": len(events), **counts,
        "last_capture_at": last_capture,
        "checkpoint_health": {
            "attempted_unique": len(checkpoint_keys),
            "recorded_unique": len(recorded_keys),
            "terminal_missing_unique": len(terminal_missing_keys),
        },
        "score_weight": 0,
    }


def enrich_items(items: list[dict], *, now: datetime | None = None, frame: dict | None = None) -> dict:
    """只读：把账本状态附回当前新闻，绝不在 API 访问路径写盘。"""
    if _store is None:
        return {"items": items, "summary": observation_summary()}
    events = _store.events()
    observations = _store.observations()
    by_id = {str(event.get("event_id") or ""): event for event in events}
    current = now or now_cn()
    enriched: list[dict] = []
    for item in items:
        copied = _json_copy(item)
        event = by_id.get(str(item.get("event_id") or ""))
        mapping = copied.get("a_share") if isinstance(copied.get("a_share"), dict) else {}
        if event and mapping.get("state") == "mapped":
            mapping["observation"] = _contract(event, observations, current, frame)
        elif mapping.get("state") == "mapped":
            event_time = _parse_event_time(item.get("time"))
            target, reason = _strict_target_trade_day(event_time) if event_time else (None, "event_time_incomplete")
            mapping["observation"] = {
                "status": "waiting" if target else "incomparable",
                "label": "待后台采样" if target else "不可比 · 事件时间或交易日历不可用",
                "reason_code": "awaiting_scheduler" if target else reason,
                "target_trade_date": target,
                "progress": {"complete": 0, "total": 3},
                "checkpoints": [], "baseline": None, "latest": None, "result": None,
                "coverage": {"complete": 0, "total": len(mapping.get("matches") or [])},
                "missing": [], "causality": "not_inferred", "direction": "unknown", "score_weight": 0,
            }
        copied["a_share"] = mapping
        enriched.append(copied)
    return {
        "items": enriched,
        "summary": observation_summary(events=events, observations=observations, now=current, frame=frame),
    }


def history(limit: int = 100) -> dict:
    if _store is None:
        return {"ok": False, "error_code": "observation_store_not_initialized", "items": []}
    events = _store.events()
    observations = _store.observations()
    current = now_cn()
    rows = [
        {**event, "observation": _contract(event, observations, current, None)}
        for event in reversed(events[-max(1, min(int(limit), 500)):])
    ]
    return {"ok": True, "items": rows, "summary": observation_summary(events=events, observations=observations, now=current)}


def _snapshot_facts(snapshot: dict | None) -> dict:
    """只提取可重算的盘面事实，不把涨跌翻译成新闻利多/利空。"""
    if not isinstance(snapshot, dict):
        return {"stocks": [], "sectors": []}
    stocks = []
    for row in snapshot.get("stocks") or []:
        if not isinstance(row, dict):
            continue
        stocks.append({
            "code": row.get("code"),
            "name": row.get("name"),
            "price": row.get("price"),
            "change_pct": row.get("change_pct"),
            "since_baseline_pct": row.get("since_baseline_pct"),
        })
    sectors = []
    for row in snapshot.get("sectors") or []:
        if not isinstance(row, dict):
            continue
        sectors.append({
            key: row.get(key)
            for key in (
                "code", "name", "change_pct", "since_baseline_pct", "up", "down",
                "flat", "coverage",
            )
            if key in row
        })
    return {"stocks": stocks, "sectors": sectors}


def _pct_between(current: Any, baseline: Any) -> float | None:
    try:
        current_value = float(current)
        baseline_value = float(baseline)
    except (TypeError, ValueError):
        return None
    if baseline_value == 0:
        return None
    return round((current_value / baseline_value - 1) * 100, 2)


def _fact_differences(contract: dict) -> dict:
    """只计算同代码的点对点变化；缺价格就返回 null，绝不插值。"""
    baseline = contract.get("baseline") if isinstance(contract.get("baseline"), dict) else {}
    result = contract.get("result") if isinstance(contract.get("result"), dict) else {}
    latest = contract.get("latest") if isinstance(contract.get("latest"), dict) else {}
    baseline_rows = {
        str(row.get("code") or ""): row
        for row in baseline.get("stocks") or [] if isinstance(row, dict)
    }
    latest_rows = {
        str(row.get("code") or ""): row
        for row in latest.get("stocks") or [] if isinstance(row, dict)
    }
    close_rows = {
        str(row.get("code") or ""): row
        for row in result.get("stocks") or [] if isinstance(row, dict)
    }
    stock_codes = sorted(set(baseline_rows) | set(latest_rows) | set(close_rows))
    stocks = []
    for code in stock_codes:
        first = baseline_rows.get(code) or {}
        current = latest_rows.get(code) or {}
        close = close_rows.get(code) or {}
        stocks.append({
            "code": code,
            "name": close.get("name") or current.get("name") or first.get("name"),
            "baseline_price": first.get("price"),
            "latest_price": current.get("price"),
            "close_price": close.get("price"),
            "latest_vs_baseline_pct": _pct_between(current.get("price"), first.get("price")),
            "close_vs_baseline_pct": _pct_between(close.get("price"), first.get("price")),
            "close_vs_latest_pct": _pct_between(close.get("price"), current.get("price")),
        })
    return {"stocks": stocks, "sectors": [], "causality": "not_inferred"}


def daily_reconciliation(trade_day: str, limit: int = 100) -> dict:
    """
    返回收盘报告可用的当日事件观察冻结视图。

    lifecycle 只回答“观察到哪一步”；verdict 回答“早先冻结的
    可验证主张是否成立”。当前新闻映射没有冻结方向假设和触发条件，
    因此 verdict 必须为“证据不足”，即使收盘快照已完成也不得判命中/证伪。
    """
    if _store is None:
        return {
            "ok": False,
            "trade_day": str(trade_day),
            "lifecycle": "incomparable",
            "verdict": "证据不足",
            "reason_code": "observation_store_not_initialized",
            "items": [],
            "score_weight": 0,
            "causality": "not_inferred",
        }
    events = [
        row for row in _store.events()
        if str(row.get("target_trade_date") or "") == str(trade_day)
    ]
    observations = _store.observations()
    current = now_cn()
    rows = []
    counts = {"waiting": 0, "observing": 0, "observed": 0, "incomparable": 0}
    for event in sorted(events, key=lambda row: (str(row.get("event_time") or ""), str(row.get("event_id") or ""))):
        contract = _contract(event, observations, current, None)
        lifecycle = str(contract.get("status") or "incomparable")
        counts[lifecycle if lifecycle in counts else "incomparable"] += 1
        snapshot = contract.get("result") or contract.get("latest")
        rows.append({
            "event_id": event.get("event_id"),
            "event_time": event.get("event_time"),
            "title": event.get("title"),
            "mapping_level": event.get("mapping_level"),
            "matches": event.get("matches") or [],
            "lifecycle": lifecycle,
            "lifecycle_label": contract.get("label"),
            "verdict": "证据不足",
            "verdict_reason": "no_frozen_claim",
            "coverage": contract.get("coverage"),
            "facts": _snapshot_facts(snapshot),
            "fact_differences": _fact_differences(contract),
            "checkpoints": contract.get("checkpoints") or [],
            "missing": contract.get("missing") or [],
            "causality": "not_inferred",
            "score_weight": 0,
        })
    bounded = rows[-max(1, min(int(limit), 500)):]
    state = (
        "observed" if bounded and all(row["lifecycle"] == "observed" for row in bounded)
        else "observing" if any(row["lifecycle"] in {"observing", "observed"} for row in bounded)
        else "waiting" if bounded
        else "incomparable"
    )
    payload = {
        "ok": True,
        "trade_day": str(trade_day),
        "lifecycle": state,
        "lifecycle_counts": counts,
        "verdict": "证据不足",
        "verdict_reason": "no_frozen_claim",
        "verdict_contract": ["命中", "部分命中", "证伪", "未触发", "证据不足"],
        "items": bounded,
        "score_weight": 0,
        "causality": "not_inferred",
        "note": "只记录事件后盘面事实；未冻结方向假设，不推断新闻因果。",
    }
    payload["content_hash"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return payload
