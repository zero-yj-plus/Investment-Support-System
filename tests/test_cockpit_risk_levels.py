from datetime import datetime
from zoneinfo import ZoneInfo

from hermes_trader.analysis import cockpit as cockpit_module
from hermes_trader.analysis.cockpit import (
    _DOWNGRADE_TRIGGERS,
    _LEVELS,
    _UPGRADE_TRIGGERS,
    apply_decision_time_guard,
    risk_command,
    risk_level_guide,
)


CN = ZoneInfo("Asia/Shanghai")


def _overview(*, change_pct: float, limit_up: int, limit_down: int) -> dict:
    return {
        "indexes": [{"change_pct": change_pct}],
        "limit_up": limit_up,
        "limit_down": limit_down,
        "zhaban_rate": 20,
        "up_count": 3200,
        "down_count": 1500,
    }


def test_five_risk_levels_have_monotonic_numeric_position_caps() -> None:
    assert [_LEVELS[level]["max_position_pct"] for level in range(1, 6)] == [70, 50, 40, 20, 10]

    guide = risk_level_guide()
    assert [item["level"] for item in guide] == [1, 2, 3, 4, 5]
    assert guide[-1]["position_cap"].startswith("总仓位 0—10%")


def test_every_level_exposes_downgrade_observation_and_level_five_has_no_upgrade() -> None:
    assert all(_DOWNGRADE_TRIGGERS[level] for level in range(1, 6))
    assert _UPGRADE_TRIGGERS[5] == []


def test_risk_command_returns_position_cap_for_normal_and_extreme_markets() -> None:
    normal = risk_command(
        {"score": 75, "phase": "发酵期"},
        _overview(change_pct=0.8, limit_up=65, limit_down=5),
    )
    assert normal["risk_level"] == 1
    assert normal["max_position_pct"] == 70

    extreme = risk_command(
        {"score": 15, "phase": "冰点期"},
        _overview(change_pct=-3.5, limit_up=12, limit_down=80),
    )
    assert extreme["risk_level"] == 5
    assert extreme["max_position_pct"] == 10
    assert extreme["position_cap"].startswith("总仓位 0—10%")


def test_premarket_never_exposes_previous_close_as_buy_instruction() -> None:
    guarded = apply_decision_time_guard(
        {
            "today_action": "可按计划交易，观察池满足条件可买。",
            "trade_state": "强势多头",
            "position_cap": "总仓位可至 70%",
            "forbid": [],
        },
        {"data_date": "2026-07-14"},
        at=datetime(2026, 7, 15, 0, 20, tzinfo=CN),
        trade_day_checker=lambda _day: True,
    )

    assert guarded["decision_tradeable"] is False
    assert guarded["decision_state"] == "premarket_pending"
    assert guarded["action_label"] == "盘前待确认"
    assert guarded["baseline_data_date"] == "2026-07-14"
    assert "不执行买卖" in guarded["today_action"]
    assert guarded["reference_action"].startswith("可按计划交易")


def test_same_day_intraday_decision_remains_executable() -> None:
    guarded = apply_decision_time_guard(
        {"today_action": "仅满足价格条件时执行", "trade_state": "震荡分歧"},
        {"data_date": "2026-07-15"},
        at=datetime(2026, 7, 15, 10, 30, tzinfo=CN),
        trade_day_checker=lambda _day: True,
    )

    assert guarded["decision_tradeable"] is True
    assert guarded["decision_state"] == "executable"
    assert guarded["today_action"] == "仅满足价格条件时执行"


def test_post_close_decision_is_review_only() -> None:
    guarded = apply_decision_time_guard(
        {"today_action": "可买", "trade_state": "强势多头"},
        {"data_date": "2026-07-15"},
        at=datetime(2026, 7, 15, 16, 5, tzinfo=CN),
        trade_day_checker=lambda _day: True,
    )

    assert guarded["decision_tradeable"] is False
    assert guarded["decision_state"] == "post_close_review"
    assert "不再显示可买指令" in guarded["today_action"]


def test_time_guard_is_idempotent_and_preserves_original_reference_action() -> None:
    original = {"today_action": "原始规则动作", "trade_state": "震荡分歧", "forbid": []}
    at = datetime(2026, 7, 15, 0, 20, tzinfo=CN)

    first = apply_decision_time_guard(
        original, {"data_date": "2026-07-14"}, at=at,
        trade_day_checker=lambda _day: True,
    )
    second = apply_decision_time_guard(
        first, {"data_date": "2026-07-14"}, at=at,
        trade_day_checker=lambda _day: True,
    )

    assert first["reference_action"] == "原始规则动作"
    assert second["reference_action"] == "原始规则动作"
    assert second["today_action"] == first["today_action"]
    assert second["trade_state"] == first["trade_state"]
    assert second["forbid"] == first["forbid"]


def test_time_guard_replaces_stale_snapshot_baseline_without_nesting() -> None:
    stale_snapshot = {
        "today_action": "盘前仅做准备：不执行买卖。",
        "reference_action": "原始规则动作",
        "trade_state": "盘前待确认｜强势多头（2026-07-15 收盘基线）",
        "position_cap": "最近风险上限仅作参考，今日开仓额度待确认",
        "forbid": ["不把昨日收盘结论当作今日买入指令", "不追高"],
    }

    guarded = apply_decision_time_guard(
        stale_snapshot, {"data_date": "2026-07-14"},
        at=datetime(2026, 7, 15, 1, 20, tzinfo=CN),
        trade_day_checker=lambda _day: True,
    )

    assert guarded["baseline_data_date"] == "2026-07-14"
    assert guarded["trade_state"] == "盘前待确认｜强势多头（2026-07-14 收盘基线）"
    assert guarded["forbid"] == ["不把昨日收盘结论当作今日买入指令", "不追高"]


def test_cockpit_keeps_business_date_separate_from_market_data_date(monkeypatch) -> None:
    monkeypatch.setattr(
        cockpit_module.market,
        "market_overview",
        lambda: {"ok": True, "data_date": "2026-07-14"},
    )
    monkeypatch.setattr(
        cockpit_module.senti_mod,
        "compute_today",
        lambda _store: {"date": "2026-07-15", "score": 70, "metrics": {}},
    )
    monkeypatch.setattr(
        cockpit_module,
        "now_cn",
        lambda: datetime(2026, 7, 15, 1, 20, tzinfo=CN),
    )
    monkeypatch.setattr(cockpit_module, "is_trade_day", lambda _day: True)

    payload = cockpit_module.cockpit(
        pulse_snap={"command": {"today_action": "原始规则动作", "trade_state": "强势多头"}}
    )

    assert payload["date"] == "2026-07-15"
    assert payload["data_date"] == "2026-07-14"
    assert payload["command"]["baseline_data_date"] == "2026-07-14"


def test_decision_data_date_is_local_and_uses_overview_calendar_rule(monkeypatch) -> None:
    monkeypatch.setattr(
        cockpit_module.market,
        "_resolve_data_day",
        lambda _date=None: ("20260714", "2026-07-14", "盘前数据"),
    )
    assert cockpit_module.market.decision_data_date() == "2026-07-14"
