from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from hermes_trader.analysis import stock_ask_data, stock_ask_profile
from hermes_trader.api import routes


def _card() -> dict:
    return {
        "code": "600519",
        "name": "贵州茅台",
        "decision_generated_at": "2026-08-18T10:30:00+08:00",
        "market_quote": {
            "code": "600519",
            "name": "贵州茅台",
            "price": 1500.0,
            "change_pct": 1.2,
            "volume_ratio": 0.75,
            "pe": 22.0,
            "as_of": "2026-08-18T10:30:00+08:00",
            "source": "test quote",
            "stale": False,
        },
        "technical_analysis": {
            "complete": True,
            "bars": 120,
            "structure": "结构偏强",
            "metrics": {
                "ma5": 1490.0,
                "ma10": 1470.0,
                "ma20": 1450.0,
                "ma20_prev": 1430.0,
                "ma60": 1400.0,
                "rsi14": 62.0,
                "macd_hist": 2.0,
                "macd_hist_prev": 1.0,
                "atr14": 20.0,
                "return5_pct": 3.0,
                "return10_pct": 5.0,
                "volume_5_20": 0.75,
            },
            "levels": {"support": 1450.0, "resistance": 1650.0},
        },
        "board": {"industry": "白酒", "source": "TDX relation"},
        "financial": {
            "roe": 30.0,
            "grossprofit_margin": 90.0,
            "debt_to_assets": 20.0,
            "source": "TDX financial",
        },
        "decision_market_evidence_quality": {"status": "ok", "captured": 8, "requested": 8},
        "missing": [],
    }


def _overview() -> dict:
    return {
        "indexes": [
            {"name": "上证指数", "change_pct": 0.2},
            {"name": "深证成指", "change_pct": 0.4},
        ],
        "up_count": 3200,
        "down_count": 1600,
        "missing": [],
    }


def _profile(monkeypatch: pytest.MonkeyPatch, **overrides) -> dict:
    monkeypatch.setattr(stock_ask_profile, "is_trade_day", lambda value: True)
    payload = {
        "holding_status": "unheld",
        "scenario": "intraday",
        "horizon": "SHORT_TERM",
        **overrides,
    }
    return stock_ask_profile.normalize_profile(
        payload,
        quote={"price": 1500.0},
        at=datetime.fromisoformat("2026-08-18T10:30:00+08:00"),
    )


def test_profile_auto_detects_holding_and_adjusts_non_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stock_ask_profile, "is_trade_day", lambda value: True)
    profile = stock_ask_profile.normalize_profile(
        {"holding_status": "unspecified", "scenario": "intraday"},
        holding={"cost": 1400.0, "weight_pct": 20.0},
        quote={"price": 1500.0},
        at=datetime.fromisoformat("2026-08-18T16:00:00+08:00"),
    )
    assert profile["holding_status"] == "held"
    assert profile["holding_cost"] == 1400.0
    assert profile["position_pct"] == 20.0
    assert profile["effective_scenario"] == "after_close"
    assert profile["scenario_adjusted"] is True


def test_profile_rejects_out_of_range_values() -> None:
    with pytest.raises(stock_ask_profile.StockAskProfileError):
        stock_ask_profile.validate_profile_payload({"position_pct": 101})
    with pytest.raises(stock_ask_profile.StockAskProfileError):
        stock_ask_profile.validate_profile_payload({"horizon": "FOREVER"})


def test_six_bucket_analysis_keeps_unavailable_sources_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _profile(monkeypatch)
    analysis = stock_ask_data.build_analysis(
        _card(),
        profile,
        _overview(),
        {
            "support": 1450.0,
            "resistance": 1650.0,
            "invalidation": 1450.0,
            "atr14": 20.0,
            "entry_trigger": "缩量回踩 1450.00 后企稳",
            "stop": "收盘有效跌破 1450.00",
        },
        at=datetime.fromisoformat("2026-08-18T10:30:00+08:00"),
    )
    assert analysis["rule_version"] == "stock-ask-six-bucket-v2"
    assert analysis["context"]["events"]["state"] == "missing"
    assert analysis["context"]["valuation"]["peer_pe_median"] is None
    assert analysis["buckets"]["QV"]["score"] == 0
    assert analysis["confidence"]["score"] < 100
    assert analysis["model_used"] is False


def test_explicit_manual_price_conflict_caps_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _profile(monkeypatch, current_price=1600.0)
    analysis = stock_ask_data.build_analysis(
        _card(), profile, _overview(), {"support": 1450.0, "resistance": 1700.0, "invalidation": 1450.0}
    )
    assert analysis["confidence"]["manual_price_conflict"] is True
    assert analysis["confidence"]["score"] <= 59


def test_default_risk_budget_is_not_a_position_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    trade_plan = {"support": 1450.0, "resistance": 1650.0, "invalidation": 1450.0}
    hinted = stock_ask_data.build_analysis(_card(), _profile(monkeypatch), _overview(), trade_plan)
    explicit = stock_ask_data.build_analysis(
        _card(),
        _profile(monkeypatch, risk_budget_pct=1.5, risk_budget_explicit=True),
        _overview(),
        trade_plan,
    )
    assert hinted["risk_reward"]["position_max_pct"] is None
    assert explicit["risk_reward"]["position_max_pct"] is not None


def test_risk_veto_only_blocks_new_risk(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _profile(monkeypatch)
    analysis = {
        "profile": profile,
        "action_code": "CONDITIONAL_BUY",
        "action": "满足条件后买入",
    }
    result = stock_ask_data.apply_risk_veto(
        analysis,
        {"decision": "veto", "reason_codes": ["evidence_incomplete"]},
    )
    assert result["action_code"] == "OBSERVE"
    assert result["action"] == "观察，不追高"


def test_two_completed_daily_closes_below_support_confirm_structure_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = _card()
    card["material_payloads"] = {
        "technical": {
            "daily": [
                {"d": "2026-08-14", "o": 1460.0, "h": 1470.0, "l": 1430.0, "c": 1440.0, "v": 1000},
                {"d": "2026-08-17", "o": 1440.0, "h": 1450.0, "l": 1420.0, "c": 1430.0, "v": 1200},
            ],
        },
    }
    analysis = stock_ask_data.build_analysis(
        card,
        _profile(monkeypatch, holding_status="held"),
        _overview(),
        {"support": 1450.0, "resistance": 1650.0},
        at=datetime.fromisoformat("2026-08-18T10:30:00+08:00"),
    )
    assert analysis["execution"]["support_break_confirmed"] is True
    assert analysis["decision_grade"] == "D"
    assert analysis["action"] == "建议将该持仓降低100%"


def test_deterministic_markdown_contains_frozen_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _profile(monkeypatch)
    analysis = stock_ask_data.build_analysis(
        _card(),
        profile,
        _overview(),
        {"support": 1450.0, "resistance": 1650.0, "invalidation": 1450.0, "stop": "收盘有效跌破 1450.00"},
    )
    text = stock_ask_data.render_markdown({**_card(), "stock_ask_analysis": analysis})
    assert "六桶综合分" in text
    assert "## 执行计划" in text
    assert "## 数据说明" in text
    assert "不构成投资建议" in text
    assert "模型分析全文" not in text


@pytest.mark.asyncio
async def test_governed_stock_ask_route_uses_six_bucket_without_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    card = _card()
    card.update({
        "recognized": True,
        "trade_plan": {
            "support": 1450.0,
            "resistance": 1650.0,
            "invalidation": 1450.0,
            "entry_trigger": "缩量回踩 1450.00 后企稳",
            "stop": "收盘有效跌破 1450.00",
        },
        "market_evidence_quality": {"status": "ok", "captured": 8, "requested": 8},
    })

    async def fake_diagnosis(*_args, **_kwargs):
        return dict(card)

    async def fake_risk(*_args, **_kwargs):
        return {"decision": "permit", "reason_codes": [], "reasons": []}

    async def fake_record(*_args, **_kwargs):
        return {"ok": True, "blocked": False, "recommendation_created": False}

    monkeypatch.setattr(routes, "governed_diagnosis_card", fake_diagnosis)
    monkeypatch.setattr(routes, "_server_risk_decision", fake_risk)
    monkeypatch.setattr(routes, "_authorize_and_record_advice", fake_record)
    monkeypatch.setattr(routes.market, "market_overview", _overview)
    monkeypatch.setattr(stock_ask_profile, "is_trade_day", lambda value: True)
    st = SimpleNamespace(store=SimpleNamespace(holdings=lambda: []))

    result = await routes._governed_and_record_diagnosis(
        st,
        "600519",
        stock_ask=True,
        stock_ask_profile={
            "holding_status": "unheld",
            "scenario": "intraday",
            "horizon": "SHORT_TERM",
        },
    )

    assert result["decision_authority"] == "stock_ask_six_bucket+risk_gate"
    assert result["stock_ask_analysis"]["rule_version"] == "stock-ask-six-bucket-v2"
    assert result["stock_ask_analysis"]["model_used"] is False
    assert result["model_explanation_status"] == "not_requested"
