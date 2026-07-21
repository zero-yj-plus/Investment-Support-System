"""Append-only ledger for advice proposals before they become recommendations.

An advice proposal is deliberately *not* a recommendation.  It can preserve a
candidate emitted by a report, diagnosis, quant job, or holdings rule while it
waits for approval, a central risk decision, or expiry.  Proposal events never
participate in recommendation performance statistics.

The ledger stores one complete immutable snapshot for every state change.  A
proposal starts in ``proposed`` and may move once to ``approved``, ``vetoed``,
or ``expired``.  There are no update or delete operations.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..data.base import now_cn


ADVICE_PROPOSAL_SCHEMA_VERSION = "1.0"
ADVICE_PROPOSAL_EVENT_TYPE = "advice_proposal_state"
PROPOSAL_STATES = frozenset({"proposed", "approved", "vetoed", "expired"})
TERMINAL_STATES = frozenset({"approved", "vetoed", "expired"})

_APPEND_LOCK = threading.RLock()


def _text(value, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def _string_list(value, *, limit: int = 500) -> list[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for raw in values:
        text = _text(raw, limit)
        if text and text not in result:
            result.append(text)
    return result


def _json_object(value, field: str) -> dict | None:
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field} 应为对象")
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 不能 JSON 序列化") from exc


def _trade_day(value) -> str | None:
    text = _text(value, 10)
    if text is None:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("trade_day 应为 YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise ValueError("trade_day 应为 YYYY-MM-DD")
    return text


def _normal_symbol(value) -> str:
    symbol = str(value or "").strip()
    if not symbol:
        raise ValueError("缺少股票代码")
    return symbol.zfill(6) if symbol.isdigit() else symbol[:32]


def _stable_id(prefix: str, namespace: str, request_id: str | None) -> str:
    if request_id:
        return prefix + uuid.uuid5(uuid.NAMESPACE_URL, f"{namespace}:{request_id}").hex
    return prefix + uuid.uuid4().hex


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        # The event file itself has already been fsynced.  Some filesystems do
        # not support syncing directory descriptors, so directory fsync is
        # best-effort rather than a reason to report a false write failure.
        pass
    finally:
        os.close(fd)


@dataclass(frozen=True)
class AdviceProposal:
    """One immutable proposal-state event."""

    payload: dict

    @classmethod
    def from_input(cls, item: dict, *, previous: dict | None = None) -> "AdviceProposal":
        if not isinstance(item, dict):
            raise ValueError("proposal 应为对象")

        state = str(item.get("state") or "proposed").strip().lower()
        if state not in PROPOSAL_STATES:
            raise ValueError("state 必须是 proposed/approved/vetoed/expired")

        client_request_id = _text(item.get("client_request_id"), 160)
        if not client_request_id:
            raise ValueError("缺少 client_request_id，无法保证幂等追加")
        supplied_proposal_id = _text(item.get("proposal_id"), 120)
        if previous is None:
            if state != "proposed":
                raise ValueError("非 proposed 事件必须引用已有 proposal")
            proposal_id = supplied_proposal_id or _stable_id(
                "ap_", "hermes-advice-proposal", client_request_id,
            )
            source = _text(item.get("source"), 80)
            if not source:
                raise ValueError("缺少 proposal 来源")
            symbol = _normal_symbol(item.get("symbol") or item.get("code"))
            action = _text(item.get("action"), 40)
            if not action:
                raise ValueError("缺少 proposal 动作")
            immutable = {
                "source": source,
                "source_artifact": _json_object(item.get("source_artifact"), "source_artifact"),
                "trade_day": _trade_day(item.get("trade_day")),
                "symbol": symbol,
                "name": _text(item.get("name"), 60),
                "action": action,
                "entry_condition": _text(item.get("entry_condition"), 500),
                "invalidation": _string_list(item.get("invalidation")),
                "expires_at": _text(item.get("expires_at"), 40),
            }
            reference_price_evidence = _json_object(
                item.get("reference_price_evidence"), "reference_price_evidence",
            )
            risk_decision = _json_object(item.get("risk_decision"), "risk_decision")
        else:
            if state == "proposed":
                raise ValueError("已有 proposal 不能再追加 proposed 状态")
            proposal_id = supplied_proposal_id
            if not proposal_id or proposal_id != previous.get("proposal_id"):
                raise ValueError("状态事件 proposal_id 不匹配")
            immutable = {
                field: json.loads(json.dumps(previous.get(field), ensure_ascii=False))
                for field in (
                    "source", "source_artifact", "trade_day", "symbol", "name", "action",
                    "entry_condition", "invalidation", "expires_at",
                )
            }
            reference_price_evidence = (
                _json_object(item.get("reference_price_evidence"), "reference_price_evidence")
                if "reference_price_evidence" in item
                else _json_object(previous.get("reference_price_evidence"), "reference_price_evidence")
            )
            risk_decision = (
                _json_object(item.get("risk_decision"), "risk_decision")
                if "risk_decision" in item
                else _json_object(previous.get("risk_decision"), "risk_decision")
            )

        created_at = now_cn().isoformat(timespec="seconds")
        event_id = _stable_id(
            "ape_", "hermes-advice-proposal-event", client_request_id,
        )
        payload = {
            "schema_version": ADVICE_PROPOSAL_SCHEMA_VERSION,
            "event_type": ADVICE_PROPOSAL_EVENT_TYPE,
            "event_id": event_id,
            "proposal_id": proposal_id,
            "client_request_id": client_request_id,
            "created_at": created_at,
            **immutable,
            "reference_price_evidence": reference_price_evidence,
            "risk_decision": risk_decision,
            "state": state,
            # This field is intentionally non-configurable.  Only a promoted
            # RecommendationEvent may become eligible for performance stats.
            "score_eligible": False,
        }
        return cls(payload)

    def as_dict(self) -> dict:
        return json.loads(json.dumps(self.payload, ensure_ascii=False))


class AdviceProposalLedger:
    """Append-only JSONL ledger for proposal lifecycle events."""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "advice_proposals" / "events.v1.jsonl"

    def events(self, proposal_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        rows: list[dict] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            if proposal_id is None or row.get("proposal_id") == proposal_id:
                rows.append(row)
        return rows

    def latest(self, proposal_id: str) -> dict | None:
        rows = self.events(str(proposal_id or "").strip())
        return rows[-1] if rows else None

    def append(self, proposal: AdviceProposal | dict) -> dict:
        """Public append API; lifecycle validation still goes through ``record``."""

        item = proposal.as_dict() if isinstance(proposal, AdviceProposal) else proposal
        return self.record(item)

    @staticmethod
    def _same_event(left: dict, right: dict) -> bool:
        # created_at can differ between retries; every business field and ID
        # must still match.  Returning the first event preserves original time.
        ignored = {"created_at"}
        return (
            {k: v for k, v in left.items() if k not in ignored}
            == {k: v for k, v in right.items() if k not in ignored}
        )

    def record(self, item: dict) -> dict:
        """Validate and append one state event, with idempotent retries."""

        if not isinstance(item, dict):
            raise ValueError("proposal 应为对象")
        requested_state = str(item.get("state") or "proposed").strip().lower()
        if requested_state not in PROPOSAL_STATES:
            raise ValueError("state 必须是 proposed/approved/vetoed/expired")
        requested_proposal_id = _text(item.get("proposal_id"), 120)

        with _APPEND_LOCK:
            all_events = self.events()
            previous = None
            if requested_state != "proposed":
                if not requested_proposal_id:
                    raise ValueError("状态事件缺少 proposal_id")
                history = [
                    row for row in all_events
                    if row.get("proposal_id") == requested_proposal_id
                ]
                if not history:
                    raise ValueError("proposal 不存在")
                previous = history[-1]

            event = AdviceProposal.from_input(item, previous=previous).as_dict()
            for existing in all_events:
                if existing.get("event_id") != event["event_id"]:
                    continue
                if not self._same_event(existing, event):
                    raise ValueError("client_request_id 已用于不同 proposal 事件")
                return existing

            if requested_state == "proposed":
                same_proposal = [
                    row for row in all_events
                    if row.get("proposal_id") == event["proposal_id"]
                ]
                if same_proposal:
                    raise ValueError("proposal_id 已存在")
            elif previous and previous.get("state") in TERMINAL_STATES:
                raise ValueError("终态 proposal 不可再变更")

            self._append(event)
            return event

    def _append(self, event: dict) -> None:
        if event.get("schema_version") != ADVICE_PROPOSAL_SCHEMA_VERSION:
            raise ValueError("proposal schema_version 不受支持")
        if event.get("event_type") != ADVICE_PROPOSAL_EVENT_TYPE:
            raise ValueError("proposal event_type 不受支持")
        if event.get("state") not in PROPOSAL_STATES:
            raise ValueError("proposal state 不受支持")
        if event.get("score_eligible") is not False:
            raise ValueError("proposal 不得进入胜率统计")

        encoded = json.dumps(
            event, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ) + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            # Windows 没有 os.fchmod；权限收紧是 best-effort，不能让
            # append-only 候选留痕因此失败并阻断整份报告。
            fchmod = getattr(os, "fchmod", None)
            try:
                if fchmod is not None:
                    fchmod(fd, 0o600)
                else:
                    os.chmod(self.path, 0o600)
            except OSError:
                pass
            remaining = memoryview(encoded.encode("utf-8"))
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("proposal 事件追加失败")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_dir(self.path.parent)


# Store is a compatibility-friendly name for callers that use event_store_for
# style naming elsewhere in the analysis package.
AdviceProposalStore = AdviceProposalLedger


def proposal_ledger_for(store_or_path) -> AdviceProposalLedger:
    """Build a ledger from a Store-like object or a data-directory path."""

    root = getattr(store_or_path, "root", store_or_path)
    if root in (None, ""):
        raise ValueError("proposal 存储缺少 root 目录")
    return AdviceProposalLedger(root)


proposal_store_for = proposal_ledger_for
