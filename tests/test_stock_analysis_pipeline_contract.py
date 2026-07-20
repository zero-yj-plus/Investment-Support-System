from __future__ import annotations

from pathlib import Path

from hermes_trader.analysis import screener, stock_analysis_contract


ROOT = Path(__file__).resolve().parents[1]


def test_condition_screener_industry_filter_uses_only_tdx_sector_members(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        screener.tdx_api,
        "sector_catalog",
        lambda: {
            "881001.SZ": {
                "code": "881001.SZ",
                "name": "银行",
                "kind": "industry",
            },
            "880001.SZ": {
                "code": "880001.SZ",
                "name": "银行概念",
                "kind": "concept",
            },
        },
    )
    calls: list[list[str]] = []

    def members(codes):
        calls.append(list(codes))
        return {
            "by_sector": {
                "881001.SZ": ["000001", "600000"],
            },
            "data_date": "2026-07-18",
        }

    monkeypatch.setattr(screener.tdx_api, "sector_members_for_codes", members)
    codes, mapping = screener._industry_members.__wrapped__("银行")

    assert calls == [["881001.SZ"]]
    assert codes == {"000001", "600000"}
    assert mapping == {"000001": "银行", "600000": "银行"}


def test_shared_stock_analysis_contract_freezes_ai_as_explanation_only() -> None:
    contract = stock_analysis_contract.build(
        entry="quant_rank",
        current_stage="deterministic_candidate_selection",
        analysis_executed=True,
        deterministic_method="cross_sectional_zscore",
        deterministic_rule_version="quant-factor-rank-v2",
        data_status="partial",
        data_as_of="2026-07-18",
        expected=300,
        present=270,
        missing=["30 只缺行情"],
        decision_authority="candidate_only",
    )

    assert contract["version"] == "stock-analysis-pipeline-v1"
    assert contract["analysis_executed"] is True
    assert contract["real_data"]["completeness"] == 0.9
    assert contract["ai"] == {
        "role": "explanation_only",
        "status": "not_requested",
        "may_change_deterministic_action": False,
        "may_invent_prices_or_missing_data": False,
    }
    assert contract["next_step"] == "governed_stock_diagnosis"


def test_quant_and_screener_clicks_execute_diagnosis_before_ai_explanation() -> None:
    quant = (ROOT / "frontend/js/views/quant.js").read_text(encoding="utf-8")
    screener_source = (
        ROOT / "frontend/js/views/screener.js"
    ).read_text(encoding="utf-8")
    diagnosis = (
        ROOT / "frontend/js/views/diagnosis.js"
    ).read_text(encoding="utf-8")
    chat = (ROOT / "frontend/js/views/chat.js").read_text(encoding="utf-8")

    assert "hermes.diagnosis.code" in quant
    assert "hermes.diagnosis.code" in screener_source
    assert "让 AI 解释这张确定性诊断卡" in diagnosis
    assert "hermes.chat.prompt" in diagnosis
    assert "hermes.chat.prompt" in chat
