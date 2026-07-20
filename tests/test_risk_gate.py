from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError

import pytest

from hermes_trader.decision.risk_gate import (
    CANDIDATE_POSITION_CAP_PCT,
    PortfolioContext,
    RULE_VERSION,
    TOTAL_POSITION_CAP_PCT,
    RiskGateInput,
    evaluate,
    evaluate_risk_gate,
)


def _snapshot(**overrides) -> dict:
    value = {
        "candidate": {
            "action": "buy",
            "symbol": "000001",
            "sector": "银行",
            "confidence": 0.65,
            "position_cap_pct": 4,
            "evidence_ids": ["ev_price", "ev_liquidity", "ev_event"],
            "critical_evidence_complete": True,
        },
        "market": {"risk_level": 2},
        "portfolio": {
            "target_position_pct": 0,
            "sector_position_pct": 10,
            "drawdown_pct": 2,
        },
        "liquidity": {"adequate": True, "participation_pct": 1},
        "event": {"major_event": False},
    }
    for key, nested in overrides.items():
        if nested == {}:
            value[key] = {}
        elif isinstance(nested, dict) and isinstance(value.get(key), dict):
            value[key].update(nested)
        else:
            value[key] = nested
    return value


def test_compliant_candidate_is_allowed_and_contract_is_complete() -> None:
    result = evaluate_risk_gate(_snapshot())

    assert result == {
        "decision": "allow",
        "final_action": "buy",
        "position_cap_pct": 4.0,
        "effective_confidence": 0.65,
        "reason_codes": [],
        "reasons": [],
        "rule_version": RULE_VERSION,
        "input_snapshot_hash": result["input_snapshot_hash"],
    }
    assert len(result["input_snapshot_hash"]) == 64
    int(result["input_snapshot_hash"], 16)


def test_risk_level_five_vetoes_buy_but_does_not_block_exit() -> None:
    buy = evaluate(_snapshot(market={"risk_level": 5}))
    assert buy.decision == "veto"
    assert buy.final_action == "avoid"
    assert buy.position_cap_pct == 0
    assert "market_risk_level_5" in buy.reason_codes

    exit_snapshot = _snapshot(
        candidate={
            "action": "sell",
            "symbol": "000001",
            "position_cap_pct": 0,
        },
        market={"risk_level": 5},
        liquidity={"adequate": False},
        event={"major_event": True},
    )
    exit_result = evaluate(exit_snapshot)
    assert exit_result.decision == "allow"
    assert exit_result.final_action == "sell"
    assert exit_result.position_cap_pct == 0


@pytest.mark.parametrize(
    ("action", "normalised"),
    [("观望", "watch"), ("止损", "exit")],
)
def test_diagnosis_chinese_defensive_actions_never_become_unknown(
    action: str,
    normalised: str,
) -> None:
    result = evaluate(_snapshot(
        candidate={"action": action, "symbol": "000001"},
        market={},
        liquidity={},
        event={},
    ))

    assert result.decision == "allow"
    assert result.final_action == normalised
    assert "action_invalid" not in result.reason_codes


@pytest.mark.parametrize("confidence", [0.35, 92])
def test_missing_critical_evidence_vetoes_entry_at_any_confidence(confidence) -> None:
    result = evaluate(_snapshot(candidate={
        "action": "buy",
        "symbol": "000001",
        "sector": "银行",
        "confidence": confidence,
        "confidence_scale": "0_to_100",
        "position_cap_pct": 8,
        "evidence_ids": [],
        "critical_evidence_complete": False,
    }))

    assert result.decision == "veto"
    assert result.final_action == "avoid"
    assert result.position_cap_pct == 0
    assert "candidate_evidence_missing" in result.reason_codes


def test_missing_target_position_is_vetoed_instead_of_filled_to_rule_cap() -> None:
    result = evaluate(_snapshot(candidate={
        "action": "buy",
        "symbol": "000001",
        "sector": "银行",
        "confidence": 0.65,
        "position_cap_pct": None,
        "evidence_ids": ["ev_price"],
        "critical_evidence_complete": True,
    }))

    assert result.decision == "veto"
    assert result.final_action == "avoid"
    assert result.position_cap_pct == 0
    assert "position_cap_missing" in result.reason_codes


def test_total_and_single_name_caps_are_separate_policy_dimensions() -> None:
    assert CANDIDATE_POSITION_CAP_PCT == {
        1: 10.0, 2: 8.0, 3: 5.0, 4: 2.0, 5: 0.0,
    }
    assert TOTAL_POSITION_CAP_PCT == {
        1: 70.0, 2: 50.0, 3: 40.0, 4: 20.0, 5: 10.0,
    }


@pytest.mark.parametrize(
    ("risk_level", "total_cap"),
    sorted(TOTAL_POSITION_CAP_PCT.items()),
)
def test_current_total_position_at_each_market_cap_vetoes_entry(
    risk_level: int,
    total_cap: float,
) -> None:
    result = evaluate(_snapshot(
        market={"risk_level": risk_level},
        portfolio={"current_total_position_pct": total_cap},
    ))

    assert result.decision == "veto"
    assert result.position_cap_pct == 0
    assert "total_position_limit_reached" in result.reason_codes


def test_projected_total_position_at_cap_vetoes_entry() -> None:
    result = evaluate(_snapshot(
        market={"risk_level": 4},
        candidate={"position_cap_pct": 1},
        portfolio={
            "current_total_position_pct": 19,
            "target_position_pct": 0,
            "sector_position_pct": 0,
        },
    ))

    assert result.decision == "veto"
    assert result.position_cap_pct == 0
    assert "projected_total_position_limit" in result.reason_codes


def test_explicit_projected_total_position_alias_is_enforced() -> None:
    result = evaluate(_snapshot(
        market={"risk_level": 2},
        candidate={"position_cap_pct": 1},
        portfolio={
            "current_total_position_pct": 10,
            "post_trade_total_position_pct": 50,
        },
    ))

    assert result.decision == "veto"
    assert "projected_total_position_limit" in result.reason_codes


def test_portfolio_v1_positional_constructor_order_remains_compatible() -> None:
    portfolio = PortfolioContext((), 1, 2, 3, 4, 0.5, 6)

    assert portfolio.max_correlation == 0.5
    assert portfolio.drawdown_pct == 6
    assert portfolio.current_total_position_pct is None


def test_total_position_is_derived_from_complete_holding_weights() -> None:
    result = evaluate(_snapshot(
        market={"risk_level": 4},
        candidate={"symbol": "600000", "sector": "银行", "position_cap_pct": 1},
        portfolio={
            "holdings": [
                {"code": f"00000{i}", "industry": "银行", "weight_pct": 5}
                for i in range(1, 5)
            ],
        },
    ))

    assert result.decision == "veto"
    assert "total_position_limit_reached" in result.reason_codes


def test_unknown_liquidity_and_event_fail_closed_for_any_entry_confidence() -> None:
    result = evaluate(_snapshot(
        candidate={
            "action": "buy",
            "symbol": "000001",
            "sector": "银行",
            "confidence": 0.9,
            "position_cap_pct": 4,
            "evidence_ids": ["ev_price"],
            "critical_evidence_complete": True,
        },
        liquidity={},
        event={},
    ))

    assert result.decision == "veto"
    assert result.final_action == "avoid"
    assert "liquidity_check_missing" in result.reason_codes
    assert "event_check_missing" in result.reason_codes


def test_single_and_sector_concentration_leave_only_computable_headroom() -> None:
    result = evaluate(_snapshot(
        candidate={"position_cap_pct": 8},
        market={"risk_level": 1},
        portfolio={
            "target_position_pct": 4,
            "sector_position_pct": 28,
            "projected_target_pct": 12,
            "drawdown_pct": 2,
        },
    ))

    # Projected single-name excess removes 2 points, then the sector has only
    # two points of headroom above the existing 4% target: final target 6%.
    assert result.decision == "reduce"
    assert result.position_cap_pct == 6.0
    assert "projected_single_name_cap" in result.reason_codes
    assert "sector_concentration_cap" not in result.reason_codes


def test_add_is_vetoed_when_industry_has_no_incremental_headroom() -> None:
    result = evaluate(_snapshot(
        candidate={"action": "add", "position_cap_pct": 6},
        market={"risk_level": 1},
        portfolio={"target_position_pct": 4, "sector_position_pct": 30},
    ))

    assert result.decision == "veto"
    assert result.position_cap_pct == 0
    assert "sector_concentration_cap" in result.reason_codes
    assert "no_concentration_headroom" in result.reason_codes


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"liquidity": {"adequate": False}}, "liquidity_insufficient"),
        ({"liquidity": {"adequate": True, "participation_pct": 12}}, "liquidity_insufficient"),
        ({"event": {"major_event": True, "severity": "critical"}}, "major_event"),
    ],
)
def test_insufficient_liquidity_or_major_event_is_a_hard_veto(
    override: dict,
    reason: str,
) -> None:
    result = evaluate(_snapshot(**override))
    assert result.decision == "veto"
    assert result.final_action == "avoid"
    assert result.position_cap_pct == 0
    assert reason in result.reason_codes


def test_correlation_and_drawdown_reduce_position_deterministically() -> None:
    correlated = evaluate(_snapshot(
        market={"risk_level": 1},
        candidate={"position_cap_pct": 8},
        portfolio={"max_pair_correlation": 0.85, "drawdown_pct": 2},
    ))
    assert correlated.decision == "reduce"
    assert correlated.position_cap_pct == 4
    assert correlated.reason_codes == ("high_correlation",)

    drawdown = evaluate(_snapshot(
        market={"risk_level": 1},
        candidate={"position_cap_pct": 8},
        portfolio={"drawdown_pct": None, "mdd": -0.10},
    ))
    assert drawdown.decision == "reduce"
    assert drawdown.position_cap_pct == 4
    assert "portfolio_drawdown" in drawdown.reason_codes

    severe = evaluate(_snapshot(
        market={"risk_level": 1},
        candidate={"position_cap_pct": 8},
        portfolio={"drawdown_pct": 18},
    ))
    assert severe.position_cap_pct == 1
    assert "portfolio_drawdown_severe" in severe.reason_codes


def test_current_store_holding_fields_are_normalised_without_mutation() -> None:
    raw = _snapshot(
        candidate={"action": "add", "code": "000001", "industry": "银行"},
        market={"risk_level": 1},
        portfolio={
            "target_position_pct": None,
            "sector_position_pct": None,
            "drawdown_pct": None,
            "holdings": [
                {"code": "000001", "industry": "银行", "cost": 10, "shares": 10},
                {"code": "000002", "industry": "银行", "cost": 10, "shares": 30},
            ],
        },
    )
    before = copy.deepcopy(raw)
    snapshot = RiskGateInput.from_mapping(raw)
    result = evaluate(snapshot)

    assert raw == before
    assert snapshot.portfolio.target_position_pct is None
    assert snapshot.portfolio.sector_position_pct is None
    assert result.decision == "veto"
    assert "portfolio_weight_missing" in result.reason_codes
    with pytest.raises(FrozenInstanceError):
        snapshot.market.risk_level = 3  # type: ignore[misc]


def test_unknown_industry_in_existing_holdings_fails_closed() -> None:
    result = evaluate(_snapshot(
        candidate={"action": "buy", "symbol": "000001", "sector": "银行"},
        market={"risk_level": 1},
        portfolio={
            "holdings": [
                {"code": f"{i:06d}", "weight_pct": 5, "cost": 10, "shares": 100}
                for i in range(1, 21)
            ],
        },
    ))

    assert result.decision == "veto"
    assert "portfolio_sector_missing" in result.reason_codes


def test_hash_uses_normalised_immutable_snapshot_not_mapping_order() -> None:
    first = _snapshot()
    second = {
        "event": {"major_event": False},
        "liquidity": {"participation_pct": 1, "adequate": True},
        "portfolio": {
            "drawdown_pct": 2,
            "sector_position_pct": 10,
            "target_position_pct": 0,
        },
        "market": {"risk_level": 2},
        "candidate": {
            "critical_evidence_complete": True,
            "evidence_ids": ["ev_price", "ev_liquidity", "ev_event"],
            "position_cap_pct": 4,
            "confidence": 0.65,
            "sector": "银行",
            "symbol": "000001",
            "action": "buy",
        },
    }
    first_result = evaluate(first)
    second_result = evaluate(second)
    assert first_result == second_result

    # The frozen snapshot owns tuples/primitives; later caller mutation cannot
    # change either the decision or its audit hash.
    frozen = RiskGateInput.from_mapping(first)
    original = evaluate(frozen)
    first["candidate"]["evidence_ids"].append("ev_late")
    assert evaluate(frozen) == original


@pytest.mark.parametrize("bad_position", [float("nan"), float("inf"), "many", -5, 0])
def test_invalid_position_fails_closed(bad_position) -> None:
    result = evaluate(_snapshot(candidate={"position_cap_pct": bad_position}))
    assert result.decision == "veto"
    assert result.position_cap_pct == 0
    assert {"position_invalid", "position_not_positive"} & set(result.reason_codes)
