from __future__ import annotations

import json
import os
import stat

import pytest

from hermes_trader.analysis import advice_proposal as proposal_module
from hermes_trader.analysis.advice_proposal import AdviceProposal, AdviceProposalLedger
from hermes_trader.analysis.engine import Engine
from hermes_trader.knowledge.store import Store


def _proposal(**overrides) -> dict:
    item = {
        "client_request_id": "tail:2026-07-13:report:tail:2026-07-13:row-1",
        "source": "尾盘",
        "source_artifact": {
            "content_id": "report:tail:2026-07-13",
            "content_hash": "a" * 64,
            "section": "尾盘观察机会",
            "row": 1,
        },
        "trade_day": "2026-07-13",
        "symbol": "1",
        "name": "平安银行",
        "action": "watch",
        "entry_condition": "放量站稳 12.50 后再考虑",
        "invalidation": ["跌破 12.20", "板块转弱", "跌破 12.20"],
        "expires_at": "2026-07-14T15:00:00+08:00",
        "reference_price_evidence": {
            "value": 12.34,
            "evidence_id": "quote:000001:20260713T145200",
            "source_id": "tdx_snapshot",
            "observed_at": "2026-07-13T14:52:00+08:00",
        },
        "risk_decision": {"decision": "pending"},
    }
    item.update(overrides)
    return item


def test_proposed_event_preserves_contract_and_can_never_be_score_eligible(tmp_path) -> None:
    ledger = AdviceProposalLedger(tmp_path)
    event = ledger.record({**_proposal(), "score_eligible": True})

    assert event["schema_version"] == "1.0"
    assert event["event_type"] == "advice_proposal_state"
    assert event["proposal_id"].startswith("ap_")
    assert event["event_id"].startswith("ape_")
    assert event["state"] == "proposed"
    assert event["score_eligible"] is False
    assert event["symbol"] == "000001"
    assert event["source"] == "尾盘"
    assert event["source_artifact"]["content_id"] == "report:tail:2026-07-13"
    assert event["invalidation"] == ["跌破 12.20", "板块转弱"]
    assert ledger.latest(event["proposal_id"]) == event


def test_public_append_accepts_an_advice_proposal_value(tmp_path) -> None:
    ledger = AdviceProposalLedger(tmp_path)
    proposal = AdviceProposal.from_input(_proposal())

    event = ledger.append(proposal)

    assert event["event_id"] == proposal.as_dict()["event_id"]
    assert event["proposal_id"] == proposal.as_dict()["proposal_id"]
    assert ledger.events() == [event]


@pytest.mark.parametrize("terminal_state", ["approved", "vetoed", "expired"])
def test_each_terminal_state_appends_a_full_snapshot(tmp_path, terminal_state: str) -> None:
    ledger = AdviceProposalLedger(tmp_path)
    proposed = ledger.record(_proposal())
    terminal = ledger.record({
        "proposal_id": proposed["proposal_id"],
        "client_request_id": f"{proposed['proposal_id']}:{terminal_state}",
        "state": terminal_state,
        "risk_decision": {"decision": terminal_state, "gate_version": "risk-v1"},
    })

    history = ledger.events(proposed["proposal_id"])
    assert [row["state"] for row in history] == ["proposed", terminal_state]
    assert terminal["source_artifact"] == proposed["source_artifact"]
    assert terminal["symbol"] == proposed["symbol"]
    assert terminal["entry_condition"] == proposed["entry_condition"]
    assert terminal["reference_price_evidence"] == proposed["reference_price_evidence"]
    assert terminal["risk_decision"]["decision"] == terminal_state
    assert terminal["score_eligible"] is False


def test_identical_client_request_retry_is_idempotent(tmp_path) -> None:
    ledger = AdviceProposalLedger(tmp_path)
    first = ledger.record(_proposal())
    original_bytes = ledger.path.read_bytes()
    second = ledger.record(_proposal())

    assert second == first
    assert ledger.path.read_bytes() == original_bytes
    assert len(ledger.events()) == 1


def test_terminal_state_retry_is_idempotent(tmp_path) -> None:
    ledger = AdviceProposalLedger(tmp_path)
    proposed = ledger.record(_proposal())
    transition = {
        "proposal_id": proposed["proposal_id"],
        "client_request_id": "tail:2026-07-13:row-1:approved",
        "state": "approved",
        "risk_decision": {"decision": "allow"},
    }
    first = ledger.record(transition)
    original_bytes = ledger.path.read_bytes()
    second = ledger.record(transition)

    assert second == first
    assert ledger.path.read_bytes() == original_bytes
    assert len(ledger.events(proposed["proposal_id"])) == 2


def test_client_request_id_conflict_is_rejected_without_appending(tmp_path) -> None:
    ledger = AdviceProposalLedger(tmp_path)
    ledger.record(_proposal())
    original_bytes = ledger.path.read_bytes()

    with pytest.raises(ValueError, match="client_request_id"):
        ledger.record(_proposal(action="buy"))

    assert ledger.path.read_bytes() == original_bytes
    assert len(ledger.events()) == 1


def test_duplicate_explicit_proposal_id_is_rejected(tmp_path) -> None:
    ledger = AdviceProposalLedger(tmp_path)
    first = ledger.record(_proposal(proposal_id="ap_explicit"))

    with pytest.raises(ValueError, match="proposal_id 已存在"):
        ledger.record(_proposal(
            proposal_id=first["proposal_id"],
            client_request_id="another-business-request",
        ))


def test_missing_proposal_and_terminal_rewrite_are_rejected(tmp_path) -> None:
    ledger = AdviceProposalLedger(tmp_path)
    with pytest.raises(ValueError, match="proposal 不存在"):
        ledger.record({
            "proposal_id": "ap_missing", "client_request_id": "missing:approve",
            "state": "approved",
        })

    proposed = ledger.record(_proposal())
    ledger.record({
        "proposal_id": proposed["proposal_id"],
        "client_request_id": "terminal:approve",
        "state": "approved",
    })
    with pytest.raises(ValueError, match="终态 proposal"):
        ledger.record({
            "proposal_id": proposed["proposal_id"],
            "client_request_id": "terminal:rewrite",
            "state": "vetoed",
        })


def test_invalid_state_and_malformed_objects_fail_closed(tmp_path) -> None:
    ledger = AdviceProposalLedger(tmp_path)
    with pytest.raises(ValueError, match="state"):
        ledger.record(_proposal(state="executed"))
    with pytest.raises(ValueError, match="source_artifact"):
        ledger.record(_proposal(source_artifact="report:tail:2026-07-13"))
    with pytest.raises(ValueError, match="trade_day"):
        ledger.record(_proposal(trade_day="2026/07/13"))
    with pytest.raises(ValueError, match="client_request_id"):
        ledger.record(_proposal(client_request_id=""))

    assert ledger.events() == []


def test_ledger_uses_jsonl_mode_0600_and_fsyncs_file_and_directory(
    tmp_path, monkeypatch,
) -> None:
    fsync_calls: list[int] = []
    real_fsync = proposal_module.os.fsync

    def observed_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(proposal_module.os, "fsync", observed_fsync)
    ledger = AdviceProposalLedger(tmp_path)
    event = ledger.record(_proposal())

    mode = stat.S_IMODE(ledger.path.stat().st_mode)
    if os.name == "nt":
        # Windows 只映射只读/可写位，stat 不会保留 POSIX 0600。
        assert mode & stat.S_IWRITE
        assert ledger.path.parent.is_dir()
    else:
        assert mode == 0o600
        assert stat.S_IMODE(ledger.path.parent.stat().st_mode) == 0o700
    # Windows 不能用 os.open 打开目录描述符，只能 fsync 事件文件。
    assert len(fsync_calls) >= (1 if os.name == "nt" else 2)
    line = json.loads(ledger.path.read_text(encoding="utf-8").strip())
    assert line == event


def test_engine_holdings_advice_creates_traceable_proposals_idempotently(tmp_path) -> None:
    engine = Engine.__new__(Engine)
    engine.store = Store(tmp_path)
    enriched = [
        {
            "code": "000001", "name": "平安银行",
            "实时行情": {
                "price": 10.20, "change_pct": -3.2, "source": "TDX API",
                "as_of": "2026-07-13 15:00:03", "stale": False,
            },
        },
        {
            "code": "600000", "name": "浦发银行",
            "实时行情": {
                "price": 9.80, "change_pct": 0.2, "source": "腾讯财经",
                "as_of": "2026-07-13 15:00:04", "stale": True,
            },
        },
    ]
    advice = engine._compute_holdings_advice(enriched)

    first = engine._record_holdings_advice_proposals("2026-07-13", advice)
    original_bytes = AdviceProposalLedger(tmp_path).path.read_bytes()
    changed_advice = [{**row, "close_price": 99.99} for row in advice]
    replay = engine._record_holdings_advice_proposals("2026-07-13", changed_advice)
    events = AdviceProposalLedger(tmp_path).events()

    assert [row["action"] for row in advice] == ["reduce", "hold"]
    assert replay == first
    assert AdviceProposalLedger(tmp_path).path.read_bytes() == original_bytes
    assert len(events) == 2
    assert all(row["state"] == "proposed" for row in events)
    assert all(row["score_eligible"] is False for row in events)
    reduce_event = events[0]
    assert reduce_event["source"] == "持仓"
    assert reduce_event["source_artifact"] == {
        "kind": "holdings_advice",
        "artifact_id": "holdings_advice/2026-07-13/000001",
        "date": "2026-07-13",
        "code": "000001",
        "rule_version": "holdings-tendency-v1",
    }
    assert reduce_event["trade_day"] == "2026-07-13"
    assert reduce_event["reference_price_evidence"] == {
        "value": 10.2,
        "currency": "CNY",
        "field": "last_price",
        "source_id": "TDX API",
        "observed_at": "2026-07-13 15:00:03",
        "quality_status": "ok",
    }
    assert reduce_event["risk_decision"] == {
        "decision": "not_evaluated",
        "reason_codes": ["proposal_only"],
        "rule_version": "holdings-tendency-v1",
    }


def test_engine_missing_holding_quote_stays_watch_proposal_without_fake_price(tmp_path) -> None:
    engine = Engine.__new__(Engine)
    engine.store = Store(tmp_path)
    advice = engine._compute_holdings_advice([{
        "code": "000001", "name": "平安银行", "实时行情": "数据不全",
    }])

    event = engine._record_holdings_advice_proposals("2026-07-13", advice)[0]

    assert event["action"] == "watch"
    assert event["reference_price_evidence"] is None
    assert event["score_eligible"] is False
