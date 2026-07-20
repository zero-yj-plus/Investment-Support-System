from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from hermes_trader.analysis import advice_proposal as proposal_module
from hermes_trader.analysis import recommend as recommend_module
from hermes_trader.analysis.advice_proposal import AdviceProposalLedger
from hermes_trader.analysis.recommend import record_recommendation
from hermes_trader.knowledge import store as store_module
from hermes_trader.knowledge.store import Store


TRADE_DAY = "2026-07-13"


def _fixed_now() -> datetime:
    return datetime(2026, 7, 13, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _proposal_payload(**overrides) -> dict:
    payload = {
        "client_request_id": "archive:2026-07-13:000001",
        "source": "尾盘",
        "source_artifact": {
            "content_id": "report:tail:2026-07-13",
            "content_hash": "a" * 64,
        },
        "trade_day": TRADE_DAY,
        "symbol": "000001",
        "name": "平安银行",
        "action": "buy",
        "entry_condition": "放量站稳 12.50",
        "reference_price_evidence": {
            "value": 12.34,
            "evidence_id": "quote:000001:20260713T145200",
            "source_id": "tdx_snapshot",
            "observed_at": "2026-07-13T14:52:00+08:00",
        },
        "risk_decision": {"decision": "pending"},
    }
    payload.update(overrides)
    return payload


def test_archive_freezes_candidate_formal_execution_and_review_ledgers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(proposal_module, "now_cn", _fixed_now)
    monkeypatch.setattr(recommend_module, "now_cn", _fixed_now)
    monkeypatch.setattr(store_module, "now_cn", _fixed_now)
    store = Store(tmp_path)

    ledger = AdviceProposalLedger(tmp_path)
    proposed = ledger.record(_proposal_payload())
    approved = ledger.record({
        "proposal_id": proposed["proposal_id"],
        "client_request_id": "archive:2026-07-13:000001:approved",
        "state": "approved",
        "risk_decision": {"decision": "allow", "position_cap_pct": 3},
    })
    recommendation = record_recommendation(store, {
        "client_request_id": "archive:recommendation:2026-07-13:000001",
        "proposal_id": proposed["proposal_id"],
        "trade_day": TRADE_DAY,
        "source": "尾盘",
        "source_artifact": {"content_id": "report:tail:2026-07-13"},
        "code": "000001",
        "name": "平安银行",
        "reference_price": 12.34,
        "direction": "buy",
        "entry_condition": "放量站稳 12.50",
        "entry_trigger": {
            "state": "triggered",
            "time": "2026-07-13T14:52:00+08:00",
            "price": 12.50,
            "evidence_id": "quote:000001:20260713T145200",
        },
        "reference_price_evidence": {
            "value": 12.34,
            "evidence_id": "quote:000001:20260713T145200",
            "source_id": "tdx_snapshot",
            "observed_at": "2026-07-13T14:52:00+08:00",
        },
        "risk_decision": {"decision": "allow", "position_cap_pct": 3},
        "evidence_ids": ["quote:000001:20260713T145200"],
        "_server_validated": True,
        "_score_eligible": True,
    })
    store.save_rec_review(recommendation["rec_id"], {
        "recommendation_event_id": recommendation["event_id"],
        "review_time": "2026-07-13 15:30:00",
        "window": "T0",
        "evaluation_state": "evaluated",
        "hit_status": "hit",
        "final_return": 1.62,
        "terminal": True,
    })
    store.save_report(
        "tail",
        "尾盘决策定稿",
        "# 存档测试",
        date=TRADE_DAY,
        advice_governance={
            "proposal_only": True,
            "proposal_ids": [proposed["proposal_id"]],
        },
        input_lineage={"evidence_ids": ["quote:000001:20260713T145200"]},
    )

    bundle = store.archive_day(date=TRADE_DAY, sentiment={"phase": "修复", "score": 62})
    advice = bundle["advice_review"]

    assert bundle["schema_version"] == "daily-archive-v2"
    assert bundle["reports"]["tail"]["advice_governance"]["proposal_ids"] == [
        proposed["proposal_id"]
    ]
    assert advice["historical_read_only"] is True
    assert advice["realtime_recalculation"] is False
    assert advice["execution_scope"] == "entry_trigger_only_no_broker_execution"
    assert len(advice["candidates"]) == 1
    assert advice["candidates"][0]["event_id"] == approved["event_id"]
    assert advice["candidates"][0]["archive_status"] == "approved"
    assert len(advice["formal_recommendations"]) == 1
    assert advice["formal_recommendations"][0]["event_id"] == recommendation["event_id"]
    assert advice["formal_recommendations"][0]["proposal_id"] == proposed["proposal_id"]
    assert advice["executions"] == [{
        "archive_stage": "execution",
        "archive_status": "triggered",
        "execution_basis": "entry_trigger_snapshot",
        "broker_receipt_bound": False,
        "recommendation_event_id": recommendation["event_id"],
        "rec_id": recommendation["rec_id"],
        "proposal_id": proposed["proposal_id"],
        "symbol": "000001",
        "name": "平安银行",
        "direction": "buy",
        "entry_condition": "放量站稳 12.50",
        "trigger": {
            "state": "triggered",
            "time": "2026-07-13T14:52:00+08:00",
            "price": 12.5,
            "evidence_id": "quote:000001:20260713T145200",
            "rule": None,
            "reason": None,
        },
    }]
    assert advice["reviews"][0]["archive_status"] == "hit"
    assert advice["reviews"][0]["symbol"] == "000001"
    assert advice["summary"]["candidate"] == {
        "total": 1, "by_status": {"approved": 1},
    }
    assert advice["summary"]["review"] == {"total": 1, "by_status": {"hit": 1}}


def test_existing_old_archive_is_returned_unchanged_and_never_rebuilt_from_live_ledgers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(proposal_module, "now_cn", _fixed_now)
    monkeypatch.setattr(store_module, "now_cn", _fixed_now)
    store = Store(tmp_path)
    archive_path = tmp_path / "archive" / f"{TRADE_DAY}.json"
    old_bundle = {
        "ok": True,
        "date": TRADE_DAY,
        "reports": {},
        "holdings": [],
        "knowledge_today": [],
        "sentiment": {},
        "archived_at": "2026-07-13 22:00:00",
    }
    archive_path.write_text(json.dumps(old_bundle, ensure_ascii=False), encoding="utf-8")
    original_bytes = archive_path.read_bytes()

    AdviceProposalLedger(tmp_path).record(_proposal_payload())
    returned = store.archive_day(date=TRADE_DAY, sentiment={"phase": "高潮"})

    assert returned == old_bundle
    assert "advice_review" not in returned
    assert archive_path.read_bytes() == original_bytes
    assert store.get_archive(TRADE_DAY) == old_bundle


def test_corrupt_historical_archive_fails_closed_instead_of_recomputing(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    archive_path = tmp_path / "archive" / f"{TRADE_DAY}.json"
    archive_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="拒绝.*重算覆写"):
        store.archive_day(date=TRADE_DAY)

    assert archive_path.read_text(encoding="utf-8") == "{broken"
