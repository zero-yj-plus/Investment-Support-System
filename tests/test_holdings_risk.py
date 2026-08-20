from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from hermes_trader.analysis.holdings_risk import (
    evaluate_holding,
    push_test_message,
    wilder_atr,
)
from hermes_trader.api import routes
from hermes_trader.knowledge.store import Store


CN = ZoneInfo("Asia/Shanghai")


class Request:
    def __init__(self, state, body: dict | None = None):
        self.app = SimpleNamespace(state=state)
        self._body = body or {}

    async def json(self):
        return self._body


def test_holdings_risk_push_test_message_is_explicitly_non_actionable() -> None:
    message = push_test_message()

    assert "持仓仓位管理·链路测试" in message
    assert "建议将该持仓降低25%" in message
    assert "未读取真实触发条件" in message
    assert "不代表任何真实持仓结论" in message
    assert "卖出" not in message


@pytest.mark.asyncio
async def test_holdings_risk_push_test_uses_current_notifier_without_real_data() -> None:
    class Notifier:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def push_text(self, message: str) -> bool:
            self.messages.append(message)
            return True

    notifier = Notifier()
    result = await routes.push_test(Request(
        SimpleNamespace(feishu=notifier),
        {"kind": "holdings_risk"},
    ))

    assert result == {"ok": True, "msg": "持仓提示测试消息已推送"}
    assert notifier.messages == [push_test_message()]


def _daily(count: int = 40, *, last_close: float | None = None) -> list[dict]:
    start = datetime(2026, 6, 22)
    rows: list[dict] = []
    day = start
    while len(rows) < count:
        if day.weekday() < 5:
            index = len(rows)
            close = 10 + index * 0.01
            rows.append({
                "d": day.date().isoformat(), "o": close - 0.03,
                "h": close + 0.12, "l": close - 0.12, "c": close,
                "v_shares": 1_000_000 + index * 1_000, "amount": (1_000_000 + index * 1_000) * close,
            })
        day += timedelta(days=1)
    if last_close is not None:
        rows[-1].update({"o": last_close + 0.1, "h": last_close + 0.2, "l": last_close - 0.2, "c": last_close})
    return rows


def _minutes(period: int = 60, count: int = 40, *, last_close: float | None = None) -> list[dict]:
    rows = []
    stamp = datetime(2026, 8, 10, 9, 30)
    for index in range(count):
        close = 10.2 + index * 0.005
        rows.append({
            "day": stamp.strftime("%Y-%m-%d %H:%M:%S"),
            "open": close - 0.02, "high": close + 0.08, "low": close - 0.08, "close": close,
            "volume": 100_000 + index * 100, "amount": (100_000 + index * 100) * close,
        })
        stamp += timedelta(minutes=period)
    if last_close is not None:
        rows[-1].update({"open": last_close + 0.1, "high": last_close + 0.2, "low": last_close - 0.2, "close": last_close})
    return rows


def _holding(**plan_updates) -> dict:
    plan = {
        "version": 1, "period": "short", "buy_date": "2026-08-11",
        "customer_risk_price": None, "customer_target_price": None,
        "target_mode": "reminder", "confirmed_reduction_pct": 0,
    }
    plan.update(plan_updates)
    return {"code": "000001", "name": "平安银行", "cost": 10, "shares": 100, "risk_plan": plan}


def _quote(price: float = 10.5) -> dict:
    return {"code": "000001", "name": "平安银行", "price": price, "as_of": "2026-08-17 10:05:00", "source": "测试行情", "stale": False}


def test_wilder_atr_uses_recursive_wilder_smoothing() -> None:
    rows = [{"high": 11 + i * 0.1, "low": 9 + i * 0.1, "close": 10 + i * 0.1} for i in range(20)]
    assert wilder_atr(rows) == 2


def test_short_plan_expires_on_fifth_trading_day_and_maps_to_25_percent() -> None:
    state = evaluate_holding(
        _holding(), _quote(), _daily(), _minutes(),
        calculated_at=datetime(2026, 8, 17, 10, 5, tzinfo=CN),
    )
    assert state["time"]["expired"] is True
    assert state["time"]["trading_days_held"] >= 5
    assert "time_expired" in state["active_trigger_codes"]
    assert state["target_reduction_pct"] == 25
    assert "降低25%" in state["action"]


def test_first_evaluation_does_not_raise_dynamic_line_above_initial_line() -> None:
    state = evaluate_holding(
        _holding(buy_date="2026-08-17"), _quote(10.5), _daily(), _minutes(),
        calculated_at=datetime(2026, 8, 17, 10, 5, tzinfo=CN),
    )
    assert state["current_risk_line"] == state["initial_risk_line"]


def test_customer_risk_line_or_confirmed_algorithm_break_is_decisive_100_percent() -> None:
    customer = evaluate_holding(
        _holding(customer_risk_price=10.6, buy_date="2026-08-17"), _quote(10.5), _daily(), _minutes(),
        calculated_at=datetime(2026, 8, 17, 10, 5, tzinfo=CN),
    )
    assert customer["target_reduction_pct"] == 100
    assert "customer_risk_line" in customer["active_trigger_codes"]

    confirmed = evaluate_holding(
        _holding(buy_date="2026-08-17"), _quote(7.0), _daily(), _minutes(last_close=7.0),
        calculated_at=datetime(2026, 8, 17, 18, 0, tzinfo=CN),
    )
    assert confirmed["target_reduction_pct"] == 100
    assert set(confirmed["active_trigger_codes"]) & {"confirmed_algorithm_break", "extreme_break"}


def test_plan_target_never_falls_until_replan() -> None:
    previous = {
        "plan_version": 1, "target_reduction_pct": 67, "current_risk_line": 8.0,
        "initial_risk_line": 8.0, "highest_price": 10.5, "active_trigger_codes": [],
    }
    state = evaluate_holding(
        _holding(buy_date="2026-08-17"), _quote(10.5), _daily(), _minutes(), previous=previous,
        calculated_at=datetime(2026, 8, 17, 10, 5, tzinfo=CN),
    )
    assert state["target_reduction_pct"] == 67
    assert state["retain_pct"] == 33


def test_stale_refresh_retains_last_good_with_original_price_conclusion() -> None:
    previous = {
        "plan_version": 1, "target_reduction_pct": 33, "current_risk_line": 9.1,
        "initial_risk_line": 8.8, "price": 9.5, "evaluated_at": "2026-08-14T14:00:00+08:00",
        "active_trigger_codes": ["time_expired"], "sent_notification_keys": [],
    }
    state = evaluate_holding(
        _holding(), {**_quote(), "stale": True}, _daily(), _minutes(), previous=previous,
        calculated_at=datetime(2026, 8, 17, 10, 5, tzinfo=CN),
    )
    assert state["stale"] is True
    assert state["price"] == 9.5
    assert state["target_reduction_pct"] == 33
    assert "保留最近有效价格结论" in state["data_note"]


def test_explicit_off_hours_closing_price_is_valid_completed_bar_evidence() -> None:
    quote = {
        **_quote(), "stale": True, "as_of": "2026-08-17",
        "quote_note": "当前非交易时段，显示为今日收盘价，非实时",
    }
    state = evaluate_holding(
        _holding(buy_date="2026-08-17"), quote, _daily(), _minutes(),
        calculated_at=datetime(2026, 8, 17, 18, 0, tzinfo=CN),
    )
    assert state["state"] != "data_insufficient"
    assert state["price_context"] == "official_close"


def test_store_preserves_plan_and_reset_then_confirm_100_ends_holding(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save_holdings([{
        "code": "000001", "name": "平安银行", "cost": 10, "shares": 100,
        "risk_plan": {
            "period": "short", "buy_date": "2026-08-11", "target_mode": "reminder",
        },
    }])
    assert store.holdings()[0]["risk_plan"]["period"] == "short"

    # 旧客户端未回传 risk_plan 时，服务端保留已确认计划，不能被整体 PUT 静默抹掉。
    store.save_holdings([{"code": "000001", "name": "平安银行", "cost": 10, "shares": 100}])
    assert store.holdings()[0]["risk_plan"]["buy_date"] == "2026-08-11"

    reset = store.reset_holding_risk_plan("000001", {
        "period": "swing", "buy_date": "2026-08-17", "target_mode": "ordinary_trigger",
    }, cost=10.2, shares=120, reason="supplement")
    assert reset["risk_plan"]["version"] == 2
    assert reset["cost"] == 10.2
    assert store.confirm_holding_reduction("000001", 100)["ended"] is True
    assert store.holdings() == []


@pytest.mark.asyncio
async def test_risk_plan_and_adjustment_api_keep_server_authority(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save_holdings([{"code": "000001", "name": "平安银行", "cost": 10, "shares": 100}])

    class Service:
        def snapshots(self):
            return store.holdings_risk_states()

        async def refresh_all(self, force: bool = False):
            return {"ok": True, "states": self.snapshots()}

    state = SimpleNamespace(store=store, holding_risk=Service())
    reset = await routes.reset_holding_risk_plan("000001", Request(state, {
        "cost": 10.1, "shares": 100, "buy_date": "2026-08-17", "period": "short",
        "customer_risk_price": 9.2, "customer_target_price": 12.0,
        "target_mode": "reminder", "reason": "manual_replan",
    }))
    assert reset["ok"] is True
    assert reset["holding"]["risk_plan"]["version"] == 1

    result = await routes.confirm_holding_adjustment("000001", Request(state, {"reduction_pct": 100}))
    assert result["ok"] is True
    assert result["ended"] is True
    assert result["holdings"] == []
