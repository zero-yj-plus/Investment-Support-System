"""Official SSE/SZSE abnormal-trading disclosure (``龙虎榜``) adapter.

This module deliberately stays below the API/MCP layer.  It normalizes the
official exchange website responses without pretending that the website
endpoints are documented OpenAPI products.  The public result distinguishes
five availability states:

``complete``
    Both exchanges returned complete responses for the requested trading day.
``degraded``
    At least one official exchange returned usable data, but another source,
    page, or seat-detail request failed.
``missing``
    The disclosure is due, but neither official source produced usable data.
``not_due``
    The date is in the future/non-trading, or today's post-close publication
    window has not started.  This is not a data-source failure.
``settling``
    Today's publication window has started but the two exchanges have not yet
    produced a complete, non-empty result.  Empty data must not be presented as
    a confirmed "no stocks on the list" before the internal settlement cutoff.

The settlement times below are product-side observation policy, not an
exchange SLA.  Callers must keep this distinction visible in product copy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time as time_module
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from .base import is_trade_day, ttl_cache
from .evidence_contract import build_evidence_contract
from .official_disclosure import DisclosureFetchError, Transport, _request_bytes


SSE_LIST_URL = "https://query.sse.com.cn/marketdata/tradedata/queryAllTradeOpenDate.do"
SZSE_REPORT_URL = "https://www.szse.cn/api/report/ShowReport/data"

SSE_REFERER = "https://www.sse.com.cn/disclosure/diclosure/public/dailydata/"
SZSE_REFERER = "https://www.szse.cn/disclosure/deal/public/index.html"

SCHEMA_VERSION = "official-longhu-v1"
PARSER_VERSION = "official-longhu-parser-v1"
_BEIJING = ZoneInfo("Asia/Shanghai")
_PUBLICATION_START = time(15, 30)
_SETTLEMENT_CUTOFF = time(21, 0)
_STATE_VALUES = frozenset({"complete", "degraded", "missing", "not_due", "settling"})
_DATA_ROOT: Path | None = None


def init(data_dir: str | Path) -> None:
    """配置客户隔离的官方龙虎榜快照目录。"""
    global _DATA_ROOT  # noqa: PLW0603 - 进程只加载一个客户
    _DATA_ROOT = Path(data_dir) / "official-longhu"


def _snapshot_path(trade_day: str) -> Path | None:
    return (_DATA_ROOT / f"{trade_day}.json") if _DATA_ROOT is not None else None


def save_snapshot(payload: dict[str, Any]) -> Path | None:
    """原子保存已进入发布窗口的盘后快照；失败/未到期不覆盖缓存。"""
    trade_day = str(payload.get("trade_date") or "")
    path = _snapshot_path(trade_day)
    if (
        path is None
        or payload.get("state") not in {"complete", "degraded", "settling"}
        or not _iso_day(trade_day)
    ):
        return None
    # 不让"今日结算中/空"覆盖已落盘的更完整证据：complete 不被非 complete 覆盖，
    # 非空 events 不被空 events 覆盖（对齐"最近完整快照不能被今日空数据覆盖"门禁）。
    existing = load_snapshot(trade_day)
    if existing is not None:
        new_events = len(payload.get("events") or [])
        old_events = len(existing.get("events") or [])
        if (existing.get("state") == "complete" and payload.get("state") != "complete") or (
            old_events > 0 and new_events == 0
        ):
            return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            tmp_path = Path(handle.name)
        tmp_path.chmod(0o600)
        tmp_path.replace(path)
        path.chmod(0o600)
        return path
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def load_snapshot(trade_day: str) -> dict[str, Any] | None:
    """读取精确交易日快照，不拿其他日期冒充。"""
    normalized = _iso_day(trade_day)
    path = _snapshot_path(normalized or "")
    if path is None or normalized is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("trade_date") != normalized:
        return None
    if payload.get("state") not in _STATE_VALUES or not isinstance(payload.get("events"), list):
        return None
    return payload


def load_latest_complete_snapshot(before_date: str | None = None) -> dict[str, Any] | None:
    """恢复策略：回退最近一份【完整证据】官方快照（complete/degraded 且 events 非空），

    跳过 settling/空/损坏快照（发现损坏空快照时不拿来冒充"当日无榜"）。返回的 payload 自带
    其真实 trade_date，调用方据此显眼标注"上一完整交易日(日期)"。before_date 限定 ≤ 该日。
    """
    normalized = _iso_day(before_date) if before_date else None
    if _DATA_ROOT is None or not _DATA_ROOT.is_dir():
        return None
    for path in sorted(_DATA_ROOT.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        events = payload.get("events")
        if payload.get("state") not in {"complete", "degraded"} or not (
            isinstance(events, list) and events
        ):
            continue
        td = _iso_day(payload.get("trade_date"))
        if td is None or (normalized and td > normalized):
            continue
        return payload
    return None


@dataclass
class SourceResult:
    """One exchange's bounded fetch result."""

    provider_family: str
    api_product: str
    source_url: str
    state: str
    events: list[dict[str, Any]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    expected_records: int | None = None
    received_records: int = 0
    excluded_non_a_share: int = 0
    pages_expected: int | None = None
    pages_fetched: int = 0
    detail_requests_expected: int = 0
    detail_requests_completed: int = 0

    def __post_init__(self) -> None:
        if self.state not in {"complete", "degraded", "missing"}:
            raise ValueError("source state must be complete, degraded, or missing")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_family": self.provider_family,
            "api_product": self.api_product,
            "source_url": self.source_url,
            "state": self.state,
            "event_count": len(self.events),
            "seat_count": sum(len(event.get("seats") or []) for event in self.events),
            "expected_records": self.expected_records,
            "received_records": self.received_records,
            "excluded_non_a_share": self.excluded_non_a_share,
            "pages_expected": self.pages_expected,
            "pages_fetched": self.pages_fetched,
            "detail_requests_expected": self.detail_requests_expected,
            "detail_requests_completed": self.detail_requests_completed,
            "issues": list(self.issues),
        }


def _json_payload(raw: Any, label: str) -> dict[str, Any] | list[Any]:
    if isinstance(raw, (dict, list)):
        payload = raw
    else:
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            payload = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DisclosureFetchError(f"{label} returned non-JSON content") from exc
    if not isinstance(payload, (dict, list)):
        raise DisclosureFetchError(f"{label} returned an invalid JSON root")
    return payload


def _number(value: object, *, scale: float = 1.0) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text or text in {"-", "--", "null", "None"}:
        return None
    try:
        number = float(text) * scale
    except ValueError:
        return None
    return number if number == number else None


def _integer(value: object, *, scale: float = 1.0) -> int | None:
    number = _number(value, scale=scale)
    return int(round(number)) if number is not None else None


def _iso_day(value: object) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    else:
        text = text[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _trade_date(value: str | date | None, today: date) -> date:
    if value is None or str(value).strip() == "":
        return today
    if isinstance(value, date):
        return value
    normalized = _iso_day(value)
    if normalized is None:
        raise ValueError("trade_date must be YYYY-MM-DD or YYYYMMDD")
    return date.fromisoformat(normalized)


def _stable_id(*parts: object) -> str:
    raw = "\x1f".join(str(part or "") for part in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _a_share(exchange: str, code: str, security_type: str = "") -> bool:
    if exchange == "sse":
        return security_type.upper() == "A" and code.startswith("6")
    return code.startswith(("0", "3"))


def _seat(
    *,
    event_id: str,
    exchange: str,
    side: str,
    rank: int,
    branch_name: object,
    ranked_amount_yuan: float | None,
    buy_amount_yuan: float | None,
    sell_amount_yuan: float | None,
    currency: str,
) -> dict[str, Any] | None:
    branch = re.sub(r"\s+", " ", str(branch_name or "")).strip()[:180]
    if not branch or side not in {"buy", "sell"} or rank < 1:
        return None
    net_amount = None
    if buy_amount_yuan is not None and sell_amount_yuan is not None:
        net_amount = buy_amount_yuan - sell_amount_yuan
    return {
        "seat_id": _stable_id(event_id, side, rank, branch),
        "exchange": exchange,
        "ranked_side": side,
        "rank": rank,
        "branch_name": branch,
        "ranked_amount_yuan": ranked_amount_yuan,
        "buy_amount_yuan": buy_amount_yuan,
        "sell_amount_yuan": sell_amount_yuan,
        "net_amount_yuan": net_amount,
        "currency": currency,
        "is_institution": "机构专用" in branch,
    }


def _merge_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for event in events:
        event_id = str(event["event_id"])
        if event_id not in merged:
            merged[event_id] = dict(event)
            merged[event_id]["seats"] = list(event.get("seats") or [])
            continue
        current = merged[event_id]
        known = {
            (seat["ranked_side"], seat["rank"], seat["branch_name"])
            for seat in current.get("seats") or []
        }
        for seat in event.get("seats") or []:
            key = (seat["ranked_side"], seat["rank"], seat["branch_name"])
            if key not in known:
                current.setdefault("seats", []).append(seat)
                known.add(key)
        states = {current.get("seat_detail_state"), event.get("seat_detail_state")}
        if "partial" in states:
            current["seat_detail_state"] = "partial"
        elif "complete" in states:
            current["seat_detail_state"] = "complete"
    return sorted(
        merged.values(),
        key=lambda event: (
            event["trade_date"], event["exchange"], event["code"],
            event.get("reason_code") or "",
        ),
    )


def _sse_seats(
    row: dict[str, Any], event_id: str, currency: str,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    seats: list[dict[str, Any]] = []
    issues: list[str] = []
    saw_fields = False
    for side, names_key, amounts_key in (
        ("buy", "branchNameB", "branchTxAmtB"),
        ("sell", "branchNameS", "branchTxAmtS"),
    ):
        names_raw = str(row.get(names_key) or "").strip()
        amounts_raw = str(row.get(amounts_key) or "").strip()
        if not names_raw and not amounts_raw:
            continue
        saw_fields = True
        names = [name.strip() for name in names_raw.split(",") if name.strip()]
        amount_tokens = [amount.strip() for amount in amounts_raw.split(",") if amount.strip()]
        if len(names) != len(amount_tokens):
            issues.append(
                f"SSE {event_id} {side} seat names/amounts length mismatch "
                f"({len(names)}/{len(amount_tokens)})"
            )
        for rank, (name, amount_text) in enumerate(zip(names, amount_tokens), start=1):
            amount = _number(amount_text)
            if amount is None:
                issues.append(f"SSE {event_id} {side}{rank} amount is invalid")
                continue
            normalized = _seat(
                event_id=event_id,
                exchange="sse",
                side=side,
                rank=rank,
                branch_name=name,
                ranked_amount_yuan=amount,
                buy_amount_yuan=amount if side == "buy" else None,
                sell_amount_yuan=amount if side == "sell" else None,
                currency=currency,
            )
            if normalized:
                seats.append(normalized)
    if issues:
        state = "partial"
    elif saw_fields:
        state = "complete"
    else:
        state = "not_published"
    return seats, state, issues


def parse_sse_summary(
    payload: dict[str, Any], requested_date: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    """Normalize SSE summary rows and their embedded buy/sell seat arrays."""
    page_help = payload.get("pageHelp")
    if not isinstance(page_help, dict) or not isinstance(page_help.get("data"), list):
        raise DisclosureFetchError("SSE pageHelp.data is not a list")
    rows = page_help["data"]
    events: list[dict[str, Any]] = []
    issues: list[str] = []
    excluded = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(f"SSE row {index} is not an object")
            continue
        day = _iso_day(row.get("tradeDate"))
        code = str(row.get("secCode") or "").strip().zfill(6)
        security_type = str(row.get("secType") or "").strip().upper()
        if day != requested_date:
            issues.append(f"SSE row {index} returned unexpected trade date {day or 'invalid'}")
            continue
        if not re.fullmatch(r"\d{6}", code):
            issues.append(f"SSE row {index} has invalid security code")
            continue
        if not _a_share("sse", code, security_type):
            excluded += 1
            continue
        reason_code = str(row.get("refType") or "").strip() or None
        event_id = _stable_id(
            "sse", day, code, reason_code,
            row.get("abnormalStart"), row.get("abnormalEnd"),
        )
        currency = "USD" if security_type == "B" else "CNY"
        seats, seat_state, seat_issues = _sse_seats(row, event_id, currency)
        issues.extend(seat_issues)
        events.append({
            "event_id": event_id,
            "provider_family": "sse",
            "api_product": "queryAllTradeOpenDate",
            "source_url": SSE_LIST_URL,
            "exchange": "sse",
            "trade_date": day,
            "code": code,
            "name": str(row.get("secAbbr") or "").strip()[:80],
            "security_type": security_type or "A",
            "reason_code": reason_code,
            # The official endpoint publishes a code.  Chinese reason text is
            # versioned in page JavaScript and must not be guessed here.
            "reason_text": None,
            "abnormal_period_start": _iso_day(row.get("abnormalStart")),
            "abnormal_period_end": _iso_day(row.get("abnormalEnd")),
            "turnover_amount": _number(row.get("secTxAmount")),
            "turnover_amount_unit": "yuan",
            "traded_volume": _integer(row.get("secTxVolume")),
            "traded_volume_unit": "shares_or_units",
            "currency": currency,
            "seat_detail_state": seat_state,
            "seats": seats,
        })
    return _merge_events(events), issues, {
        "raw_row_count": len(rows),
        "excluded_non_a_share": excluded,
    }


def _szse_report(payload: list[Any] | dict[str, Any], catalog_id: str) -> dict[str, Any]:
    candidates = payload if isinstance(payload, list) else [payload]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") or {}
        if str(metadata.get("catalogid") or "") == catalog_id:
            return item
    raise DisclosureFetchError(f"SZSE response omitted catalog {catalog_id}")


def _szse_reason_code(row: dict[str, Any]) -> str | None:
    match = re.search(r"(?:[?&]|&amp;)ZBDM=([A-Za-z0-9_-]+)", str(row.get("bz") or ""))
    return match.group(1) if match else None


def parse_szse_summary(
    payload: list[Any] | dict[str, Any], requested_date: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, int | None]]:
    """Normalize one SZSE public-information report page.

    The official table labels turnover as ``亿元`` and volume as ``万股/万份``;
    normalized values are converted to yuan and shares-or-units exactly once.
    """
    report = _szse_report(payload, "1842_xxpl_after")
    metadata = report.get("metadata")
    rows = report.get("data")
    if not isinstance(metadata, dict) or not isinstance(rows, list):
        raise DisclosureFetchError("SZSE summary metadata/data is invalid")
    events: list[dict[str, Any]] = []
    issues: list[str] = []
    excluded = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(f"SZSE row {index} is not an object")
            continue
        day = _iso_day(row.get("dqrq"))
        code = str(row.get("zqdm") or "").strip().zfill(6)
        if day != requested_date:
            issues.append(f"SZSE row {index} returned unexpected trade date {day or 'invalid'}")
            continue
        if not re.fullmatch(r"\d{6}", code):
            issues.append(f"SZSE row {index} has invalid security code")
            continue
        if not _a_share("szse", code):
            excluded += 1
            continue
        reason_code = _szse_reason_code(row)
        if not reason_code:
            issues.append(f"SZSE {day}/{code} omitted disclosure reason code")
        event_id = _stable_id("szse", day, code, reason_code, row.get("plyy"))
        amount = _number(row.get("cjje"), scale=100_000_000)
        volume = _integer(row.get("cjsl"), scale=10_000)
        if row.get("cjje") not in (None, "") and amount is None:
            issues.append(f"SZSE {event_id} turnover amount is invalid")
        if row.get("cjsl") not in (None, "") and volume is None:
            issues.append(f"SZSE {event_id} traded volume is invalid")
        events.append({
            "event_id": event_id,
            "provider_family": "szse",
            "api_product": "ShowReport-1842",
            "source_url": SZSE_REPORT_URL,
            "exchange": "szse",
            "trade_date": day,
            "code": code,
            "name": str(row.get("zqjc") or "").strip()[:80],
            "security_type": "A",
            "reason_code": reason_code,
            "reason_text": re.sub(r"\s+", " ", str(row.get("plyy") or "")).strip()[:240] or None,
            "abnormal_period_start": None,
            "abnormal_period_end": None,
            "turnover_amount": amount,
            "turnover_amount_unit": "yuan",
            "traded_volume": volume,
            "traded_volume_unit": "shares_or_units",
            "currency": "CNY",
            "seat_detail_state": "pending",
            "source_detail_key": {
                "trade_date": day,
                "code": code,
                "reason_code": reason_code,
            },
            "seats": [],
        })
    return events, issues, {
        "raw_row_count": len(rows),
        "excluded_non_a_share": excluded,
        "record_count": _integer(metadata.get("recordcount")),
        "page_count": _integer(metadata.get("pagecount")),
        "page_no": _integer(metadata.get("pageno")),
        "page_size": _integer(metadata.get("pagesize")),
    }


def parse_szse_detail(
    payload: list[Any] | dict[str, Any], event: dict[str, Any],
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Normalize the optional SZSE tab2 seat report for one abnormal event."""
    candidates = payload if isinstance(payload, list) else [payload]
    seat_report = None
    for item in candidates:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") or {}
        cols = metadata.get("cols") or {}
        if str(metadata.get("tabkey") or "") == "tab2" or (
            isinstance(cols, dict) and {"mmlb", "zsmc"}.issubset(cols)
        ):
            seat_report = item
            break
    if seat_report is None:
        return [], "not_published", []
    rows = seat_report.get("data")
    if not isinstance(rows, list):
        raise DisclosureFetchError("SZSE seat detail data is not a list")
    seats: list[dict[str, Any]] = []
    issues: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(f"SZSE seat row {index} is not an object")
            continue
        label = str(row.get("mmlb") or "").strip()
        side = "buy" if label.startswith("买") else "sell" if label.startswith("卖") else ""
        rank_match = re.search(r"(\d+)", label)
        rank = int(rank_match.group(1)) if rank_match else 0
        buy_amount = _number(row.get("mrje"))
        sell_amount = _number(row.get("mcje"))
        ranked_amount = buy_amount if side == "buy" else sell_amount
        normalized = _seat(
            event_id=str(event["event_id"]),
            exchange="szse",
            side=side,
            rank=rank,
            branch_name=row.get("zsmc"),
            ranked_amount_yuan=ranked_amount,
            buy_amount_yuan=buy_amount,
            sell_amount_yuan=sell_amount,
            currency="CNY",
        )
        if normalized is None or ranked_amount is None:
            issues.append(f"SZSE seat row {index} is incomplete")
            continue
        seats.append(normalized)
    deduped = []
    seen = set()
    for seat in seats:
        key = (seat["ranked_side"], seat["rank"], seat["branch_name"])
        if key not in seen:
            seen.add(key)
            deduped.append(seat)
    if issues:
        state = "partial"
    elif deduped:
        state = "complete"
    else:
        state = "not_published"
    return deduped, state, issues


def _fetch_sse(
    trade_day: str, request: Transport, timeout: float,
) -> SourceResult:
    raw = request(
        SSE_LIST_URL,
        label="上交所官方龙虎榜",
        timeout=timeout,
        referer=SSE_REFERER,
        params={"token": "QUERY", "tradeDate": trade_day.replace("-", ""), "flag": "1"},
    )
    payload = _json_payload(raw, "SSE public trading information")
    if not isinstance(payload, dict):
        raise DisclosureFetchError("SSE public trading information root is not an object")
    events, issues, meta = parse_sse_summary(payload, trade_day)
    return SourceResult(
        provider_family="sse",
        api_product="queryAllTradeOpenDate",
        source_url=SSE_LIST_URL,
        state="degraded" if issues else "complete",
        events=events,
        issues=issues,
        expected_records=meta["raw_row_count"],
        received_records=meta["raw_row_count"],
        excluded_non_a_share=meta["excluded_non_a_share"],
        pages_expected=1,
        pages_fetched=1,
        detail_requests_expected=0,
        detail_requests_completed=0,
    )


def _szse_summary_params(trade_day: str, page_no: int) -> dict[str, str]:
    return {
        "SHOWTYPE": "JSON",
        "CATALOGID": "1842_xxpl_after",
        "TABKEY": "tab1",
        "PAGENO": str(page_no),
        "txtDMorJC": "",
        "txtStart": trade_day,
        "txtEnd": trade_day,
        "selectModule": "",
    }


def _szse_detail_params(event: dict[str, Any]) -> dict[str, str]:
    key = event.get("source_detail_key") or {}
    return {
        "SHOWTYPE": "JSON",
        "CATALOGID": "1842_detal",
        "TABKEY": "tab1,tab2",
        "DQRQ": str(key.get("trade_date") or ""),
        "ZQDM": str(key.get("code") or ""),
        "ZBDM": str(key.get("reason_code") or ""),
    }


def _fetch_szse(
    trade_day: str,
    request: Transport,
    timeout: float,
    *,
    max_pages: int,
    max_detail_requests: int,
    request_gap_seconds: float,
) -> SourceResult:
    issues: list[str] = []
    events: list[dict[str, Any]] = []
    expected_records: int | None = None
    pages_expected: int | None = None
    pages_fetched = 0
    received_records = 0
    excluded = 0
    page_no = 1
    last_request_started: float | None = None

    def paced_request(**kwargs) -> bytes:
        """Keep requests to the same official host sequential and spaced."""
        nonlocal last_request_started
        if request_gap_seconds > 0 and last_request_started is not None:
            remaining = request_gap_seconds - (time_module.monotonic() - last_request_started)
            if remaining > 0:
                time_module.sleep(remaining)
        last_request_started = time_module.monotonic()
        return request(SZSE_REPORT_URL, **kwargs)

    while True:
        try:
            raw = paced_request(
                label="深交所官方龙虎榜",
                timeout=timeout,
                referer=SZSE_REFERER,
                params=_szse_summary_params(trade_day, page_no),
            )
            payload = _json_payload(raw, f"SZSE public trading information page {page_no}")
            page_events, page_issues, meta = parse_szse_summary(payload, trade_day)
        except Exception as exc:  # noqa: BLE001 - retain earlier valid pages
            if page_no == 1:
                raise
            issues.append(f"SZSE summary page {page_no} unavailable: {type(exc).__name__}")
            break
        pages_fetched += 1
        events.extend(page_events)
        issues.extend(page_issues)
        received_records += int(meta["raw_row_count"] or 0)
        excluded += int(meta["excluded_non_a_share"] or 0)
        current_record_count = meta.get("record_count")
        current_page_count = meta.get("page_count")
        if expected_records is None:
            expected_records = int(current_record_count or 0)
            pages_expected = max(1, int(current_page_count or 1))
        elif current_record_count is not None and int(current_record_count) != expected_records:
            issues.append("SZSE recordcount changed while paging")
        if meta.get("page_no") not in (None, page_no):
            issues.append(
                f"SZSE requested page {page_no} but response identified page {meta.get('page_no')}"
            )
        target_pages = pages_expected or 1
        if page_no >= target_pages:
            break
        if page_no >= max_pages:
            issues.append(f"SZSE page count {target_pages} exceeds safety cap {max_pages}")
            break
        page_no += 1

    events = _merge_events(events)
    if expected_records is not None and received_records != expected_records:
        issues.append(
            f"SZSE pagination incomplete: received {received_records}/{expected_records} rows"
        )

    detail_expected = sum(bool((event.get("source_detail_key") or {}).get("reason_code")) for event in events)
    detail_completed = 0
    detail_attempts = 0
    for event in events:
        detail_key = event.get("source_detail_key") or {}
        if not detail_key.get("reason_code"):
            event["seat_detail_state"] = "unavailable"
            continue
        if detail_attempts >= max_detail_requests:
            event["seat_detail_state"] = "skipped_by_safety_cap"
            continue
        detail_attempts += 1
        try:
            raw = paced_request(
                label="深交所龙虎榜席位明细",
                timeout=timeout,
                referer=SZSE_REFERER,
                params=_szse_detail_params(event),
            )
            payload = _json_payload(raw, f"SZSE seat detail {event['event_id']}")
            seats, detail_state, detail_issues = parse_szse_detail(payload, event)
            event["seats"] = seats
            event["seat_detail_state"] = detail_state
            detail_completed += 1
            issues.extend(f"{event['event_id']}: {issue}" for issue in detail_issues)
        except Exception as exc:  # noqa: BLE001 - keep the official summary row
            event["seat_detail_state"] = "unavailable"
            issues.append(
                f"SZSE seat detail {event['event_id']} unavailable: {type(exc).__name__}"
            )
    if detail_expected > max_detail_requests:
        issues.append(
            f"SZSE seat detail safety cap reached: {max_detail_requests}/{detail_expected}"
        )
    for event in events:
        # Keep the stable official keys needed for audit while avoiding an
        # implementation-private nested object in the normalized contract.
        event.pop("source_detail_key", None)

    return SourceResult(
        provider_family="szse",
        api_product="ShowReport-1842",
        source_url=SZSE_REPORT_URL,
        state="degraded" if issues else "complete",
        events=events,
        issues=issues,
        expected_records=expected_records,
        received_records=received_records,
        excluded_non_a_share=excluded,
        pages_expected=pages_expected,
        pages_fetched=pages_fetched,
        detail_requests_expected=detail_expected,
        detail_requests_completed=detail_completed,
    )


def _missing_source(provider: str, exc: Exception) -> SourceResult:
    return SourceResult(
        provider_family=provider,
        api_product=("queryAllTradeOpenDate" if provider == "sse" else "ShowReport-1842"),
        source_url=SSE_LIST_URL if provider == "sse" else SZSE_REPORT_URL,
        state="missing",
        issues=[f"{provider} official source unavailable: {type(exc).__name__}"],
    )


def _fetched_at(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _not_due_result(target: date, now: datetime, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "provider_family": "sse+szse",
        "api_product": "official-longhu-aggregate",
        "evidence_level": "tier_1",
        "ok": True,
        "state": "not_due",
        "trade_date": target.isoformat(),
        "fetched_at": _fetched_at(now),
        "events": [],
        "event_count": 0,
        "seat_count": 0,
        "empty_confirmed": False,
        "sources": [],
        "completeness": {
            "expected_exchanges": ["sse", "szse"],
            "complete_exchanges": [],
            "live_exchanges": [],
        },
        "availability_reason": reason,
        "degraded_reasons": [],
        "missing": [],
        "content_hash": None,
        "evidence": None,
    }


def fetch_official_longhu(
    trade_date: str | date | None = None,
    *,
    timeout: float = 8.0,
    max_pages: int = 20,
    max_detail_requests: int = 100,
    request_gap_seconds: float = 1.0,
    transport: Transport | None = None,
    now_fn: Callable[[], datetime] | None = None,
    trade_day_fn: Callable[[date], bool] | None = None,
) -> dict[str, Any]:
    """Fetch and normalize one trading day's official SSE/SZSE list.

    Network requests are sequential per exchange.  SZSE seat detail is an N+1
    website flow and is therefore hard-capped; hitting the cap is explicit
    degradation, never silent completeness.
    """
    clock = now_fn or (lambda: datetime.now(_BEIJING))
    now = clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now_fn must return a timezone-aware datetime")
    now_cn = now.astimezone(_BEIJING)
    target = _trade_date(trade_date, now_cn.date())
    calendar = trade_day_fn or is_trade_day
    bounded_pages = max(1, min(int(max_pages), 50))
    bounded_details = max(0, min(int(max_detail_requests), 200))
    bounded_gap = max(0.0, min(float(request_gap_seconds), 5.0))

    if target > now_cn.date():
        return _not_due_result(target, now, "目标日期尚未到达")
    if not calendar(target):
        return _not_due_result(target, now, "目标日期为非交易日，不应有当日龙虎榜")
    if target == now_cn.date() and now_cn.time() < _PUBLICATION_START:
        return _not_due_result(target, now, "当日盘后公开信息发布窗口尚未开始")

    request = transport or _request_bytes
    trade_day = target.isoformat()
    sources: list[SourceResult] = []
    try:
        sources.append(_fetch_sse(trade_day, request, timeout))
    except Exception as exc:  # noqa: BLE001 - preserve the other exchange
        sources.append(_missing_source("sse", exc))
    try:
        sources.append(
            _fetch_szse(
                trade_day,
                request,
                timeout,
                max_pages=bounded_pages,
                max_detail_requests=bounded_details,
                # Injected transports are offline tests or caller-managed
                # gateways; the built-in website transport owns pacing.
                request_gap_seconds=bounded_gap if transport is None else 0.0,
            )
        )
    except Exception as exc:  # noqa: BLE001 - preserve the other exchange
        sources.append(_missing_source("szse", exc))

    events = _merge_events(event for source in sources for event in source.events)
    complete_sources = [source.provider_family for source in sources if source.state == "complete"]
    live_sources = [source.provider_family for source in sources if source.state != "missing"]
    issues = [issue for source in sources for issue in source.issues]
    before_cutoff = target == now_cn.date() and now_cn.time() < _SETTLEMENT_CUTOFF

    if before_cutoff and (len(complete_sources) < 2 or not events):
        state = "settling"
        availability_reason = "当日盘后公开信息仍在交易所发布与同步窗口内"
    elif len(complete_sources) == 2:
        state = "complete"
        availability_reason = "沪深两所官网响应完整"
    elif live_sources:
        state = "degraded"
        availability_reason = "仅部分官方交易所数据或席位明细完整"
    else:
        state = "missing"
        availability_reason = "龙虎榜已应发布，但沪深两所官方链路均不可用"
    if state not in _STATE_VALUES:  # defensive guard for future edits
        raise AssertionError(f"invalid official longhu state: {state}")

    completed_at = clock()
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise ValueError("now_fn must return a timezone-aware datetime")
    evidence_state = "degraded" if state == "settling" else state
    evidence_reason = None if evidence_state == "complete" else (
        "；".join(issues) or availability_reason
    )
    contract = build_evidence_contract(
        content=events if evidence_state != "missing" else None,
        provider_family="sse+szse",
        api_product="official-longhu-aggregate",
        data_as_of=trade_day if evidence_state != "missing" else None,
        fetched_at=completed_at,
        parser_version=PARSER_VERSION,
        source_url=None,
        evidence_level="tier_1",
        data_state=evidence_state,
        degraded_reason=evidence_reason,
    )
    content_hash = contract.content_hash
    result = {
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "provider_family": "sse+szse",
        "api_product": "official-longhu-aggregate",
        "evidence_level": "tier_1",
        "ok": state != "missing",
        "state": state,
        "trade_date": trade_day,
        "data_as_of": trade_day if state != "missing" else None,
        "fetched_at": contract.fetched_at,
        "events": events,
        "event_count": len(events),
        "seat_count": sum(len(event.get("seats") or []) for event in events),
        "empty_confirmed": state == "complete" and not events,
        "sources": [source.to_dict() for source in sources],
        "completeness": {
            "expected_exchanges": ["sse", "szse"],
            "complete_exchanges": complete_sources,
            "live_exchanges": live_sources,
        },
        "availability_reason": availability_reason,
        "degraded_reasons": issues if state in {"degraded", "missing", "settling"} else [],
        "missing": ["官方龙虎榜"] if state == "missing" else [],
        "content_hash": content_hash,
        "evidence": contract.to_dict(),
    }
    return result


@ttl_cache(300, fail_seconds=60)
def cached_official_longhu(
    trade_date: str = "",
    max_pages: int = 20,
    max_detail_requests: int = 100,
) -> dict[str, Any]:
    """Five-minute single-flight cache for future API/MCP integration."""
    return fetch_official_longhu(
        trade_date or None,
        max_pages=max_pages,
        max_detail_requests=max_detail_requests,
    )
