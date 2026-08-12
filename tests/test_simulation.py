from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from hermes_trader.api import routes
from hermes_trader.jobs import simulation_matcher
from hermes_trader.jobs.simulation_matcher import SimulationMatcher
from hermes_trader.simulation import SimulationError, SimulationStore


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 13, 2, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path, clock: Clock) -> SimulationStore:
    value = SimulationStore(
        tmp_path / "simulation.sqlite3",
        clock=clock,
        max_market_age_seconds=120,
    )
    value.initialize_account(
        client_request_id="req-fixture-account",
        initial_cash=1_000_000,
        commission_rate_pct=0.03,
        minimum_commission=5,
        stamp_duty_rate_pct=0.05,
        transfer_fee_rate_pct=0.002,
        slippage_bps=0,
    )
    return value


def proposal(clock: Clock, request_id: str, **overrides):
    payload = {
        "client_request_id": request_id,
        "symbol": "600519",
        "side": "buy",
        "order_type": "limit",
        "quantity": 100,
        "reference_price": 10.0,
        "limit_price": 10.5,
        "risk_level": 2,
        "data_as_of": clock().isoformat(),
        "data_stale": False,
        "data_missing": [],
        "trade_date": "2026-07-13",
        "valid_trade_date": "2026-07-13",
    }
    payload.update(overrides)
    return payload


def test_submission_queues_order_and_limit_caps_slippage(
    store: SimulationStore, clock: Clock,
) -> None:
    store.update_account_settings(
        client_request_id="req-slip-settings",
        commission_rate_pct=0.03,
        minimum_commission=5,
        stamp_duty_rate_pct=0.05,
        transfer_fee_rate_pct=0.002,
        slippage_bps=100,
    )
    created = store.propose(**proposal(clock, "req-create-001", limit_price=10.05))
    order_id = created["order"]["order_id"]

    assert created["order"]["status"] == "approved"
    assert [event["event_type"] for event in created["order"]["events"]] == ["queued"]
    waiting = store.fill(
        order_id,
        client_request_id="req-fill-waiting",
        market_price=10.1,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
        trade_date="2026-07-13",
        unlock_trade_date="2026-07-14",
    )
    assert waiting["matched"] is False
    assert waiting["order"]["status"] == "approved"
    assert [event["event_type"] for event in waiting["order"]["events"]] == ["queued"]

    clock.advance(1)
    filled = store.fill(
        order_id,
        client_request_id="req-fill-002",
        market_price=10.0,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
        trade_date="2026-07-13",
        unlock_trade_date="2026-07-14",
    )["order"]

    assert filled["status"] == "filled"
    assert filled["market_price"] == pytest.approx(10.05)
    assert filled["market_source"] == "tencent_quote"
    assert filled["slippage_per_share"] == pytest.approx(0.05)
    assert filled["slippage_bps"] == pytest.approx(50.0)
    assert filled["slippage_value"] == pytest.approx(5.0)
    assert [event["event_type"] for event in filled["events"]] == ["queued", "filled"]
    fill_replay = store.fill(
        order_id,
        client_request_id="req-fill-002",
        market_price=10.1,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
        trade_date="2026-07-13",
        unlock_trade_date="2026-07-14",
    )
    assert fill_replay["idempotent_replay"] is True
    assert [event["event_type"] for event in fill_replay["order"]["events"]].count("filled") == 1


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"risk_level": 4}, "risk_level_gte_4"),
        ({"data_stale": True}, "data_stale"),
        ({"data_missing": ["last_price"]}, "data_missing"),
        ({"quantity": 101}, "buy_quantity_not_board_lot_100"),
        ({"quantity": 0}, "invalid_quantity"),
        ({"reference_price": 0}, "invalid_reference_price"),
        ({"limit_price": -1}, "invalid_limit_price"),
        ({"data_as_of": "2026-07-13T01:00:00+00:00"}, "data_stale"),
    ],
)
def test_buy_proposal_is_deterministically_rejected(
    store: SimulationStore,
    clock: Clock,
    override: dict,
    reason: str,
) -> None:
    request_id = "req-block-" + reason[:18].replace("_", "-")
    order = store.propose(**proposal(clock, request_id, **override))["order"]

    assert order["status"] == "rejected"
    assert reason in order["rejection_reason"]
    assert [event["event_type"] for event in order["events"]] == ["submitted", "rejected"]


def test_a_share_sell_accepts_any_positive_integer_but_buy_requires_board_lot(
    store: SimulationStore, clock: Clock,
) -> None:
    sell = store.propose(**proposal(
        clock,
        "req-sell-001",
        side="sell",
        quantity=1,
        risk_level=5,
        data_as_of=None,
        data_stale=True,
    ))["order"]
    buy = store.propose(**proposal(clock, "req-buy-odd", quantity=1))["order"]

    assert sell["status"] == "approved"
    assert sell["quantity"] == 1
    assert buy["status"] == "rejected"
    assert "buy_quantity_not_board_lot_100" in buy["rejection_reason"]


def test_normal_orders_need_no_manual_approval(
    store: SimulationStore, clock: Clock,
) -> None:
    order = store.propose(**proposal(clock, "req-auto-create"))["order"]
    status = store.status()
    assert order["status"] == "approved"
    assert order["approved_by"] is None
    assert order["approved_at"] is None
    assert status["manual_approval_required"] is False
    assert status["automatic_matching"] is True
    assert status["matching_interval_seconds"] == 5


def test_fill_rejects_stale_future_or_untrusted_market_evidence(
    store: SimulationStore, clock: Clock,
) -> None:
    cases = [
        ("req-stale", (clock() - timedelta(minutes=10)).isoformat(), "tencent_quote", "market_data_stale"),
        ("req-future", (clock() + timedelta(minutes=10)).isoformat(), "tencent_quote", "market_as_of_future"),
        ("req-source", clock().isoformat(), "manual", "real_market_source_required"),
    ]
    for index, (prefix, as_of, source, reason) in enumerate(cases):
        order_id = store.propose(**proposal(clock, f"{prefix}-create"))["order"]["order_id"]
        rejected = store.fill(
            order_id,
            client_request_id=f"{prefix}-fill",
            market_price=10.1 + index * 0.01,
            as_of=as_of,
            market_source=source,
            trade_date="2026-07-13",
            unlock_trade_date="2026-07-14",
        )["order"]
        assert rejected["status"] == "rejected"
        assert reason in rejected["rejection_reason"]


def test_limit_order_waits_until_market_price_reaches_limit(store: SimulationStore, clock: Clock) -> None:
    order_id = store.propose(**proposal(
        clock, "req-limit-create", limit_price=9.9,
    ))["order"]["order_id"]
    waiting = store.fill(
        order_id,
        client_request_id="req-limit-fill",
        market_price=10.0,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
        trade_date="2026-07-13",
        unlock_trade_date="2026-07-14",
    )

    assert waiting["matched"] is False
    assert waiting["order"]["status"] == "approved"
    assert waiting["order"]["rejection_reason"] is None


def test_market_order_can_fill_without_manual_approval(store: SimulationStore, clock: Clock) -> None:
    order_id = store.propose(**proposal(
        clock,
        "req-market-create",
        order_type="market",
        limit_price=None,
    ))["order"]["order_id"]
    order = store.fill(
        order_id,
        client_request_id="req-market-fill",
        market_price=10.02,
        as_of=clock().isoformat(),
        market_source="tdx_api_quote",
        trade_date="2026-07-13",
        unlock_trade_date="2026-07-14",
    )["order"]

    assert order["status"] == "filled"
    assert order["order_type"] == "market"


def test_sell_limit_also_caps_adverse_slippage(
    store: SimulationStore, clock: Clock,
) -> None:
    buy_id = store.propose(**proposal(clock, "req-slip-sell-buy"))["order"]["order_id"]
    store.fill(
        buy_id,
        client_request_id="req-slip-sell-buy-fill",
        market_price=10.0,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
        trade_date="2026-07-13",
        unlock_trade_date="2026-07-14",
    )
    clock.advance(24 * 60 * 60)
    store.update_account_settings(
        client_request_id="req-slip-sell-settings",
        commission_rate_pct=0.03,
        minimum_commission=5,
        stamp_duty_rate_pct=0.05,
        transfer_fee_rate_pct=0.002,
        slippage_bps=100,
    )
    sell_id = store.propose(**proposal(
        clock,
        "req-slip-sell-create",
        side="sell",
        reference_price=11.0,
        limit_price=10.95,
        trade_date="2026-07-14",
        valid_trade_date="2026-07-14",
    ))["order"]["order_id"]
    sold = store.fill(
        sell_id,
        client_request_id="req-slip-sell-fill",
        market_price=11.0,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
        trade_date="2026-07-14",
    )["order"]

    assert sold["status"] == "filled"
    assert sold["market_price"] == pytest.approx(10.95)
    assert sold["market_price"] >= sold["limit_price"]
    assert sold["slippage_bps"] == pytest.approx(45.4545)


def test_full_account_applies_fees_t1_fifo_pnl_and_equity_curve(
    store: SimulationStore, clock: Clock,
) -> None:
    buy_id = store.propose(**proposal(clock, "req-account-buy"))["order"]["order_id"]
    bought = store.fill(
        buy_id,
        client_request_id="req-account-buy-fill",
        market_price=10.0,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
        trade_date="2026-07-13",
        unlock_trade_date="2026-07-14",
    )["order"]

    assert bought["total_fee"] == pytest.approx(5.02)
    assert bought["net_cash_change"] == pytest.approx(-1005.02)
    missing_valuation = store.account_view(quotes={}, trade_date="2026-07-13")
    assert missing_valuation["valuation_state"] == "missing"
    assert missing_valuation["total_assets"] is None
    assert missing_valuation["missing_symbols"] == ["600519"]
    same_day_sell = store.propose(**proposal(
        clock, "req-account-sell-t0", side="sell", limit_price=9.5,
    ))["order"]
    locked = store.fill(
        same_day_sell["order_id"],
        client_request_id="req-account-sell-t0-fill",
        market_price=11.0,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
        trade_date="2026-07-13",
    )["order"]
    assert locked["status"] == "rejected"
    assert locked["rejection_reason"] == "t1_position_locked"

    same_day_view = store.account_view(trade_date="2026-07-13")
    assert same_day_view["positions"][0]["available_quantity"] == 0
    assert same_day_view["positions"][0]["t1_locked_quantity"] == 100

    clock.advance(24 * 60 * 60)
    sell_id = store.propose(**proposal(
        clock, "req-account-sell-t1", side="sell", limit_price=10.5,
    ))["order"]["order_id"]
    sold = store.fill(
        sell_id,
        client_request_id="req-account-sell-t1-fill",
        market_price=11.0,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
        trade_date="2026-07-14",
    )["order"]
    assert sold["status"] == "filled"
    assert sold["commission_fee"] == pytest.approx(5.0)
    assert sold["stamp_duty_fee"] == pytest.approx(0.55)
    assert sold["transfer_fee"] == pytest.approx(0.02)
    assert sold["realized_pnl"] == pytest.approx(89.41)

    account = store.account_view(trade_date="2026-07-14")
    assert account["cash"] == pytest.approx(1_000_089.41)
    assert account["total_assets"] == pytest.approx(1_000_089.41)
    assert account["realized_pnl"] == pytest.approx(89.41)
    assert account["total_pnl"] == pytest.approx(89.41)
    assert account["total_fees"] == pytest.approx(10.59)
    assert account["positions"] == []
    assert len(account["equity_curve"]) == 3


def test_account_settings_are_prospective_and_initial_cash_is_immutable(
    store: SimulationStore,
) -> None:
    changed = store.update_account_settings(
        client_request_id="req-settings-update",
        commission_rate_pct=0.02,
        minimum_commission=0,
        stamp_duty_rate_pct=0.05,
        transfer_fee_rate_pct=0.002,
        slippage_bps=3,
    )
    replay = store.update_account_settings(
        client_request_id="req-settings-update",
        commission_rate_pct=0.02,
        minimum_commission=0,
        stamp_duty_rate_pct=0.05,
        transfer_fee_rate_pct=0.002,
        slippage_bps=3,
    )
    assert changed["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert store.account_view(trade_date="2026-07-13")["fee_settings"]["slippage_bps"] == 3

    with pytest.raises(SimulationError) as exc_info:
        store.initialize_account(
            client_request_id="req-account-second",
            initial_cash=2_000_000,
            commission_rate_pct=0.03,
            minimum_commission=5,
            stamp_duty_rate_pct=0.05,
            transfer_fee_rate_pct=0.002,
        )
    assert exc_info.value.code == "account_already_initialized"


def test_fill_rejects_insufficient_cash_without_mutating_account(
    store: SimulationStore, clock: Clock,
) -> None:
    order_id = store.propose(**proposal(
        clock,
        "req-cash-create",
        reference_price=20_000,
        limit_price=21_000,
    ))["order"]["order_id"]
    rejected = store.fill(
        order_id,
        client_request_id="req-cash-fill",
        market_price=20_000,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
        trade_date="2026-07-13",
        unlock_trade_date="2026-07-14",
    )["order"]
    assert rejected["status"] == "rejected"
    assert rejected["rejection_reason"] == "insufficient_cash"
    account = store.account_view(trade_date="2026-07-13")
    assert account["cash"] == pytest.approx(1_000_000)
    assert account["positions"] == []
    assert len(account["equity_curve"]) == 1


def test_all_successful_writes_are_idempotent_and_conflicts_are_blocked(
    store: SimulationStore, clock: Clock,
) -> None:
    payload = proposal(clock, "req-idem-create")
    first = store.propose(**payload)
    replay = store.propose(**payload)

    assert replay["idempotent_replay"] is True
    assert replay["order"]["order_id"] == first["order"]["order_id"]
    assert len(replay["order"]["events"]) == 1

    with pytest.raises(SimulationError) as exc_info:
        store.propose(**{**payload, "quantity": 200})
    assert exc_info.value.code == "idempotency_conflict"

    assert first["order"]["status"] == "approved"
    assert [event["event_type"] for event in first["order"]["events"]] == ["queued"]


def test_cancel_is_terminal_and_append_only(store: SimulationStore, clock: Clock) -> None:
    order_id = store.propose(**proposal(clock, "req-cancel-create"))["order"]["order_id"]
    cancelled = store.cancel(
        order_id,
        client_request_id="req-cancel-order",
        reason="operator_changed_plan",
    )["order"]

    assert cancelled["status"] == "cancelled"
    assert cancelled["rejection_reason"] == "operator_changed_plan"
    assert [event["event_type"] for event in cancelled["events"]] == ["queued", "cancelled"]
    cancel_replay = store.cancel(
        order_id,
        client_request_id="req-cancel-order",
        reason="operator_changed_plan",
    )
    assert cancel_replay["idempotent_replay"] is True
    assert [event["event_type"] for event in cancel_replay["order"]["events"]].count("cancelled") == 1
    with pytest.raises(SimulationError) as exc_info:
        store.approve(
            order_id,
            client_request_id="req-cancel-approve",
            approved_by="operator",
        )
    assert exc_info.value.code == "invalid_transition"


def test_emergency_stop_cancels_pending_orders_and_rejects_new_orders(
    store: SimulationStore, clock: Clock,
) -> None:
    proposed_id = store.propose(**proposal(clock, "req-stop-proposed"))["order"]["order_id"]
    approved_id = store.propose(**proposal(clock, "req-stop-approved"))["order"]["order_id"]

    first = store.emergency_stop(
        client_request_id="req-emergency-stop",
        reason="行情时钟异常",
    )
    replay = store.emergency_stop(
        client_request_id="req-emergency-stop",
        reason="行情时钟异常",
    )

    assert first["status"]["emergency_stop"]["active"] is True
    assert first["status"]["broker_connected"] is False
    assert first["status"]["broker_interface_present"] is False
    assert replay["idempotent_replay"] is True
    assert store.get_order(proposed_id)["status"] == "cancelled"
    assert store.get_order(approved_id)["status"] == "cancelled"

    blocked = store.propose(**proposal(clock, "req-after-stop"))["order"]
    assert blocked["status"] == "rejected"
    assert "emergency_stop_active" in blocked["rejection_reason"]


def test_emergency_stop_reset_is_manual_audited_idempotent_and_persistent(
    tmp_path, clock: Clock,
) -> None:
    path = tmp_path / "simulation.sqlite3"
    first = SimulationStore(path, clock=clock)
    first.emergency_stop(
        client_request_id="req-reset-stop",
        reason="上游时钟异常",
    )

    restarted = SimulationStore(path, clock=clock)
    assert restarted.status()["emergency_stop"]["active"] is True
    with pytest.raises(SimulationError) as auto_exc:
        restarted.reset_emergency_stop(
            client_request_id="req-reset-auto",
            approved_by="Hermes",
            reason="自动恢复",
        )
    assert auto_exc.value.code == "manual_approver_required"
    assert restarted.status()["emergency_stop"]["active"] is True

    reset = restarted.reset_emergency_stop(
        client_request_id="req-reset-manual",
        approved_by="risk_operator",
        reason="人工核验行情时钟与数据源均已恢复",
    )
    replay = restarted.reset_emergency_stop(
        client_request_id="req-reset-manual",
        approved_by="risk_operator",
        reason="人工核验行情时钟与数据源均已恢复",
    )
    assert reset["status"]["emergency_stop"]["active"] is False
    assert reset["status"]["latest_event"]["event_type"] == "emergency_stop_reset"
    assert reset["status"]["latest_event"]["detail"]["manual_approval"] is True
    assert reset["status"]["latest_event"]["detail"]["approved_by"] == "risk_operator"
    assert reset["status"]["last_emergency_reset"] == {
        "approved_by": "risk_operator",
        "reason": "人工核验行情时钟与数据源均已恢复",
        "at": clock().isoformat(timespec="milliseconds"),
    }
    assert replay["idempotent_replay"] is True

    with pytest.raises(SimulationError) as conflict_exc:
        restarted.reset_emergency_stop(
            client_request_id="req-reset-manual",
            approved_by="risk_operator",
            reason="不同的恢复理由",
        )
    assert conflict_exc.value.code == "idempotency_conflict"

    after_restart = SimulationStore(path, clock=clock)
    assert after_restart.status()["emergency_stop"]["active"] is False
    assert after_restart.status()["last_emergency_reset"]["approved_by"] == "risk_operator"
    new_order = after_restart.propose(**proposal(clock, "req-after-reset"))["order"]
    assert new_order["status"] == "approved"


def test_emergency_stop_reset_api_requires_manual_actor_and_appends_event(
    store: SimulationStore,
) -> None:
    store.emergency_stop(client_request_id="req-api-stop", reason="人工触发紧停")
    automated = asyncio.run(routes.simulation_emergency_stop_reset(Request(store, {
        "client_request_id": "req-api-reset-auto",
        "approved_by": "bot",
        "reason": "自动恢复",
    })))
    auto_payload = _json_response(automated)
    assert automated.status_code == 400
    assert auto_payload["error_code"] == "manual_approver_required"
    assert store.status()["emergency_stop"]["active"] is True

    manual = asyncio.run(routes.simulation_emergency_stop_reset(Request(store, {
        "client_request_id": "req-api-reset-human",
        "approved_by": "ops_user",
        "reason": "人工确认异常已解除",
    })))
    assert manual["status"]["emergency_stop"]["active"] is False
    assert manual["status"]["latest_event"]["event_type"] == "emergency_stop_reset"


def test_sqlite_uses_wal_and_state_survives_restart(tmp_path, clock: Clock) -> None:
    path = tmp_path / "simulation.sqlite3"
    first = SimulationStore(path, clock=clock)
    order_id = first.propose(**proposal(clock, "req-persist-create"))["order"]["order_id"]

    second = SimulationStore(path, clock=clock)
    assert second.get_order(order_id)["status"] == "approved"
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


class Request:
    def __init__(self, store: SimulationStore, payload: dict | None = None, query: dict | None = None):
        self.app = SimpleNamespace(state=SimpleNamespace(simulation_store=store, store=object()))
        self._payload = payload or {}
        self.query_params = query or {}

    async def json(self):
        return self._payload


def _json_response(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _install_server_authority(monkeypatch, clock: Clock, quote_state: dict, *, risk_level: int = 2):
    async def pulse(_store):
        return {"risk_level": risk_level, "missing": []}

    monkeypatch.setattr(routes, "_pulse_locked", pulse)
    monkeypatch.setattr(
        routes,
        "now_cn",
        lambda: clock().astimezone(timezone(timedelta(hours=8))),
    )
    monkeypatch.setattr(routes, "is_trade_day", lambda value: value.weekday() < 5)
    monkeypatch.setattr(routes, "next_trade_day", lambda _value: "2026-07-14")
    monkeypatch.setattr(routes.stock, "quick_quote", lambda _symbol: {
        "price": quote_state["price"],
        "as_of": quote_state.get("as_of", clock().isoformat()),
        "stale": quote_state.get("stale", False),
    })


def test_simulation_account_api_initializes_updates_and_records_curve(
    tmp_path, clock: Clock, monkeypatch,
) -> None:
    ledger = SimulationStore(tmp_path / "account-api.sqlite3", clock=clock)
    monkeypatch.setattr(
        routes,
        "now_cn",
        lambda: clock().astimezone(timezone(timedelta(hours=8))),
    )
    monkeypatch.setattr(routes, "is_trade_day", lambda value: value.weekday() < 5)

    before = asyncio.run(routes.simulation_account(Request(ledger)))
    assert before["initialized"] is False

    created = asyncio.run(routes.simulation_account_initialize(Request(ledger, {
        "client_request_id": "req-api-account-create",
        "initial_cash": 500_000,
        "commission_rate_pct": 0.03,
        "minimum_commission": 5,
        "stamp_duty_rate_pct": 0.05,
        "transfer_fee_rate_pct": 0.002,
        "slippage_bps": 1,
    })))
    assert created["account"]["cash"] == pytest.approx(500_000)
    assert created["account"]["fee_settings"]["slippage_bps"] == 1

    settings = asyncio.run(routes.simulation_account_settings(Request(ledger, {
        "client_request_id": "req-api-account-settings",
        "commission_rate_pct": 0.02,
        "minimum_commission": 0,
        "stamp_duty_rate_pct": 0.05,
        "transfer_fee_rate_pct": 0.002,
        "slippage_bps": 2,
    })))
    assert settings["account"]["fee_settings"]["commission_rate_pct"] == pytest.approx(0.02)

    valued = asyncio.run(routes.simulation_account_valuation(Request(ledger, {
        "client_request_id": "req-api-account-value",
    })))
    assert valued["account"]["valuation_state"] == "exact"
    assert len(valued["account"]["equity_curve"]) == 2


def test_simulation_api_exposes_lifecycle_without_broker(
    store: SimulationStore, clock: Clock, monkeypatch,
) -> None:
    quote_state = {"price": 10.0, "stale": False}
    _install_server_authority(monkeypatch, clock, quote_state)
    forged_create = proposal(
        clock,
        "req-api-create",
        reference_price=999.0,
        risk_level=5,
        data_as_of="2099-01-01T00:00:00+00:00",
        data_stale=True,
        data_missing=["forged_missing"],
    )
    created = asyncio.run(routes.simulation_propose_order(Request(
        store, forged_create,
    )))
    assert created["ok"] is True
    assert created["simulation_only"] is True
    assert created["order"]["status"] == "approved"
    assert created["order"]["reference_price"] == 10.0
    assert created["order"]["risk_level"] == 2
    assert created["order"]["data_stale"] is False
    assert created["order"]["data_missing"] == []
    order_id = created["order"]["order_id"]

    quote_state["price"] = 10.1
    filled = asyncio.run(routes.simulation_fill_order(
        order_id,
        Request(store, {
            "client_request_id": "req-api-fill",
            "market_price": 0.01,
            "as_of": "2099-01-01T00:00:00+00:00",
            "market_source": "manual",
        }),
    ))
    assert filled["order"]["status"] == "filled"
    assert filled["order"]["market_price"] == 10.1
    assert filled["order"]["market_source"] == "server_stock_quick_quote_live"
    clock.advance(30)
    quote_state.update(price=99.0, stale=True)
    fill_replay = asyncio.run(routes.simulation_fill_order(
        order_id,
        Request(store, {
            "client_request_id": "req-api-fill",
            "market_price": 888.0,
            "as_of": "2099-01-01T00:00:00+00:00",
            "market_source": "forged",
        }),
    ))
    assert fill_replay["idempotent_replay"] is True
    assert fill_replay["order"]["status"] == "filled"
    assert fill_replay["order"]["market_price"] == 10.1

    listed = asyncio.run(routes.simulation_orders(Request(store, query={"limit": "10"})))
    assert listed["count"] == 1
    assert listed["items"][0]["order_id"] == order_id

    status = asyncio.run(routes.simulation_status(Request(store)))
    assert status["simulation_only"] is True
    assert status["broker_connected"] is False


def test_simulation_api_cannot_bypass_server_risk_with_forged_safe_fields(
    store: SimulationStore, clock: Clock, monkeypatch,
) -> None:
    quote_state = {"price": 10.0, "stale": False}
    _install_server_authority(monkeypatch, clock, quote_state, risk_level=4)
    forged_safe = proposal(
        clock,
        "req-forged-safe",
        risk_level=1,
        reference_price=1.0,
        data_stale=False,
        data_missing=[],
    )

    created = asyncio.run(routes.simulation_propose_order(Request(store, forged_safe)))

    assert created["order"]["status"] == "rejected"
    assert created["order"]["risk_level"] == 4
    assert created["order"]["reference_price"] == 10.0
    assert "risk_level_gte_4" in created["order"]["rejection_reason"]


def test_network_retry_replays_first_proposal_when_server_snapshot_changes_but_intent_conflicts(
    store: SimulationStore, clock: Clock, monkeypatch,
) -> None:
    quote_state = {"price": 10.0, "stale": False}
    _install_server_authority(monkeypatch, clock, quote_state, risk_level=2)
    client_intent = proposal(clock, "req-network-retry")
    first = asyncio.run(routes.simulation_propose_order(Request(store, client_intent)))

    async def changed_pulse(_store):
        return {"risk_level": 5, "missing": ["market_breadth"]}

    monkeypatch.setattr(routes, "_pulse_locked", changed_pulse)
    quote_state.update(price=88.0, stale=True)
    clock.advance(45)
    replay = asyncio.run(routes.simulation_propose_order(Request(store, client_intent)))

    assert replay["idempotent_replay"] is True
    assert replay["order"]["order_id"] == first["order"]["order_id"]
    assert replay["order"]["status"] == "approved"
    assert replay["order"]["reference_price"] == 10.0
    assert replay["order"]["risk_level"] == 2

    conflicting = asyncio.run(routes.simulation_propose_order(Request(
        store,
        {**client_intent, "quantity": 200},
    )))
    payload = _json_response(conflicting)
    assert conflicting.status_code == 409
    assert payload["error_code"] == "idempotency_conflict"


def test_simulation_api_accepts_server_verified_latest_close_outside_session(
    store: SimulationStore, clock: Clock, monkeypatch,
) -> None:
    quote_state = {"price": 10.0, "stale": False}
    _install_server_authority(monkeypatch, clock, quote_state)
    created = asyncio.run(routes.simulation_propose_order(Request(
        store, proposal(clock, "req-stale-api-create"),
    )))
    order_id = created["order"]["order_id"]
    quote_state.update(stale=True, as_of="2026-07-10")

    filled = asyncio.run(routes.simulation_fill_order(
        order_id,
        Request(store, {
            "client_request_id": "req-stale-api-fill",
            "market_price": 10.0,
            "as_of": clock().isoformat(),
            "market_source": "tencent_quote",
        }),
    ))

    assert filled["order"]["status"] == "filled"
    assert filled["order"]["price_mode"] == "latest_close"
    assert filled["order"]["trade_date"] == "2026-07-10"
    assert filled["order"]["market_source"] == "server_stock_latest_close"


def test_simulation_api_keeps_untriggered_limit_order_in_matching_pool(
    store: SimulationStore, clock: Clock, monkeypatch,
) -> None:
    quote_state = {"price": 10.0, "stale": False}
    _install_server_authority(monkeypatch, clock, quote_state)
    created = asyncio.run(routes.simulation_propose_order(Request(
        store, proposal(clock, "req-api-noapprove", limit_price=9.9),
    )))
    response = asyncio.run(routes.simulation_fill_order(
        created["order"]["order_id"],
        Request(store, {
            "client_request_id": "req-api-bad-fill",
            "market_price": 10.1,
            "as_of": clock().isoformat(),
            "market_source": "tencent_quote",
        }),
    ))
    assert response["matched"] is False
    assert response["order"]["status"] == "approved"
    assert response["order"]["rejection_reason"] is None


def test_automatic_matcher_fills_triggered_order_and_expires_day_orders(
    store: SimulationStore, clock: Clock, monkeypatch,
) -> None:
    current = datetime(2026, 7, 13, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    price = {"value": 10.2}
    matcher = SimulationMatcher(
        store,
        quote_provider=lambda symbols: {
            symbol: {"price": price["value"], "stale": False, "source": "test_quote"}
            for symbol in symbols
        },
        clock=lambda: current,
    )
    monkeypatch.setattr(simulation_matcher, "is_trade_day", lambda _value: True)
    monkeypatch.setattr(simulation_matcher, "next_trade_day", lambda _value: "2026-07-14")
    order_id = store.propose(**proposal(
        clock, "req-matcher-create", limit_price=10.0,
    ))["order"]["order_id"]

    first = asyncio.run(matcher.run_once())
    assert first["waiting"] == 1
    assert store.get_order(order_id)["status"] == "approved"

    price["value"] = 9.99
    second = asyncio.run(matcher.run_once())
    assert second["matched"] == 1
    assert store.get_order(order_id)["status"] == "filled"

    expiring_id = store.propose(**proposal(
        clock, "req-matcher-expire", limit_price=9.0,
    ))["order"]["order_id"]
    matcher.clock = lambda: datetime(
        2026, 7, 13, 15, 1, tzinfo=timezone(timedelta(hours=8)),
    )
    expired = asyncio.run(matcher.run_once())
    assert expired["expired"] == 1
    order = store.get_order(expiring_id)
    assert order["status"] == "cancelled"
    assert order["rejection_reason"] == "day_order_expired"
    assert order["events"][-1]["event_type"] == "expired"
