from __future__ import annotations

from pathlib import Path

import pytest

from hermes_trader.analysis import engine as engine_module
from hermes_trader.analysis.advice_proposal import proposal_ledger_for
from hermes_trader.analysis.engine import (
    Engine,
    _sanitize_unbound_report_price_lines,
    validate_report_advice_governance,
)
from hermes_trader.knowledge.store import Store


def _snapshot(level: int) -> dict:
    return {
        "command": {"risk_level": level, "rule_version": "test-risk-v1"},
        "as_of": "2026-07-13 14:52:00",
    }


def test_report_governance_blocks_direct_order_language_at_any_level() -> None:
    result = validate_report_advice_governance("建议立即买入 000001", _snapshot(1))

    assert result["ok"] is False
    assert "direct_order_language" in result["errors"]


def test_report_governance_blocks_any_positive_entry_at_risk_five() -> None:
    result = validate_report_advice_governance("000001 观察后可介入", _snapshot(5))

    assert result["ok"] is False
    assert "risk_level_5_entry_proposal" in result["errors"]


def test_report_governance_allows_negated_or_proposal_language() -> None:
    defensive = validate_report_advice_governance("风险高，不买入，不加仓。", _snapshot(5))
    proposal = validate_report_advice_governance(
        "000001 候选观察，待触发条件满足后再走风险闸。", _snapshot(2),
    )

    assert defensive["ok"] is True
    assert proposal["ok"] is True


def test_unbound_price_line_is_removed_before_revalidation() -> None:
    content = "# 收盘复盘\n- 回踩 10.20 元后观察\n- 保持低仓位"
    sanitized, meta = _sanitize_unbound_report_price_lines(
        content,
        {
            "ok": False,
            "claims": [{
                "line": 2,
                "status": "blocked",
                "reason": "price_claim_not_bound_to_stock",
                "value": 10.20,
            }],
        },
    )

    assert "10.20" not in sanitized
    assert "价格数据不足，暂不给点位" in sanitized
    assert "保持低仓位" in sanitized
    assert meta == {
        "applied": True,
        "reason": "unbound_stock_price_lines_removed",
        "line_count": 1,
        "lines": [2],
        "original_blocked_count": 1,
    }


@pytest.mark.parametrize(
    "reason",
    [
        "stock_not_in_injected_context",
        "price_claim_bound_to_multiple_stocks",
        "stock_has_no_price_evidence",
        "price_outside_stock_evidence_range",
    ],
)
def test_price_sanitizer_never_relaxes_other_claim_failures(reason: str) -> None:
    content = "- 000001 平安银行回踩 99.99 元后买入"

    sanitized, meta = _sanitize_unbound_report_price_lines(
        content,
        {"claims": [{"line": 1, "status": "blocked", "reason": reason}]},
    )

    assert sanitized == content
    assert meta is None


class _Config:
    client_id = "report-governance-test"

    @staticmethod
    def section(_name: str) -> dict:
        return {}


class _Hermes:
    def __init__(self, content: str):
        self.content = content

    async def chat_stream(self, *_args, **_kwargs):
        yield self.content


async def _collect(stream) -> list[dict]:
    return [event async for event in stream]


@pytest.mark.asyncio
async def test_report_does_not_leak_blocked_model_text_before_validation(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    engine = Engine(_Config(), store, _Hermes("建议立即买入 000001"))
    monkeypatch.setattr(
        engine, "_context_for_report",
        lambda _rtype: _async_value({"今日日期": "2026-07-13"}),
    )
    monkeypatch.setattr(engine_module, "is_trade_day", lambda: False)
    monkeypatch.setattr(
        engine_module, "validate_report_finalization",
        lambda *_args, **_kwargs: {"ok": True, "status": "passed"},
    )

    events = await _collect(engine.generate_report_stream("morning"))

    assert not any(event.get("delta") for event in events)
    assert any("已阻止保存" in event.get("error", "") for event in events)
    assert store.get_report("morning", "2026-07-13") is None


@pytest.mark.asyncio
async def test_validated_report_records_proposals_before_releasing_content(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    content = "趋势观察候选 000001，待触发后再走风险闸。"
    engine = Engine(_Config(), store, _Hermes(content))
    monkeypatch.setattr(
        engine, "_context_for_report",
        lambda _rtype: _async_value({"今日日期": "2026-07-13"}),
    )
    monkeypatch.setattr(engine_module, "is_trade_day", lambda: False)
    monkeypatch.setattr(
        engine_module, "validate_report_finalization",
        lambda *_args, **_kwargs: {"ok": True, "status": "passed"},
    )

    events = await _collect(engine.generate_report_stream("trend"))
    report = store.get_report("trend", "2026-07-13")

    assert report is not None
    assert report["advice_governance"]["proposal_count"] == 1
    assert report["advice_governance"]["proposal_ids"]
    proposals = proposal_ledger_for(store).events()
    assert len(proposals) == 1
    assert proposals[0]["source_artifact"]["content_hash"] == report["content_hash"]
    deltas = [event for event in events if event.get("delta")]
    assert deltas and all(event.get("validated") is True for event in deltas)
    assert events[-1]["done"] is True


@pytest.mark.asyncio
async def test_report_degrades_only_unbound_price_line_and_saves_audited_result(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    content = "# 收盘复盘\n- 回踩 10.20 元后观察\n- 风险偏高，保持低仓位。"
    engine = Engine(_Config(), store, _Hermes(content))
    monkeypatch.setattr(
        engine, "_context_for_report",
        lambda _rtype: _async_value({"今日日期": "2026-07-13"}),
    )
    monkeypatch.setattr(engine_module, "is_trade_day", lambda: False)

    events = await _collect(engine.generate_report_stream("close"))
    report = store.get_report("close", "2026-07-13")

    assert events[-1]["done"] is True
    assert report is not None
    assert "10.20" not in report["content_md"]
    assert "价格数据不足，暂不给点位" in report["content_md"]
    assert report["claim_validation"]["status"] == "passed"
    assert report["claim_validation"]["sanitation"]["line_count"] == 1


async def _async_value(value):
    return value
