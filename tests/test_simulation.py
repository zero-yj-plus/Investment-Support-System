from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from hermes_trader.api import routes
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
    return SimulationStore(
        tmp_path / "simulation.sqlite3",
        clock=clock,
        max_market_age_seconds=120,
    )


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
    }
    payload.update(overrides)
    return payload


def test_manual_approval_is_required_before_fill_and_slippage_is_recorded(
    store: SimulationStore, clock: Clock,
) -> None:
    created = store.propose(**proposal(clock, "req-create-001"))
    order_id = created["order"]["order_id"]

    assert created["order"]["status"] == "proposed"
    with pytest.raises(SimulationError, match="人工批准") as exc_info:
        store.fill(
            order_id,
            client_request_id="req-fill-001",
            market_price=10.1,
            as_of=clock().isoformat(),
            market_source="tencent_quote",
        )
    assert exc_info.value.code == "manual_approval_required"

    approved = store.approve(
        order_id,
        client_request_id="req-approve-001",
        approved_by="risk_operator",
    )
    assert approved["order"]["status"] == "approved"
    assert approved["order"]["approved_by"] == "risk_operator"

    clock.advance(1)
    filled = store.fill(
        order_id,
        client_request_id="req-fill-002",
        market_price=10.1,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
    )["order"]

    assert filled["status"] == "filled"
    assert filled["market_price"] == pytest.approx(10.1)
    assert filled["market_source"] == "tencent_quote"
    assert filled["slippage_per_share"] == pytest.approx(0.1)
    assert filled["slippage_bps"] == pytest.approx(100.0)
    assert filled["slippage_value"] == pytest.approx(10.0)
    assert [event["event_type"] for event in filled["events"]] == [
        "proposed", "approved", "filled",
    ]
    fill_replay = store.fill(
        order_id,
        client_request_id="req-fill-002",
        market_price=10.1,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
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
    assert [event["event_type"] for event in order["events"]] == ["proposed", "rejected"]


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

    assert sell["status"] == "proposed"
    assert sell["quantity"] == 1
    assert buy["status"] == "rejected"
    assert "buy_quantity_not_board_lot_100" in buy["rejection_reason"]


def test_automated_actor_cannot_satisfy_manual_approval(
    store: SimulationStore, clock: Clock,
) -> None:
    order_id = store.propose(**proposal(clock, "req-auto-create"))["order"]["order_id"]

    with pytest.raises(SimulationError) as exc_info:
        store.approve(
            order_id,
            client_request_id="req-auto-approve",
            approved_by="Hermes",
        )

    assert exc_info.value.code == "manual_approver_required"
    assert store.get_order(order_id)["status"] == "proposed"


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
        store.approve(
            order_id,
            client_request_id=f"{prefix}-approve",
            approved_by="operator",
        )
        rejected = store.fill(
            order_id,
            client_request_id=f"{prefix}-fill",
            market_price=10.1 + index * 0.01,
            as_of=as_of,
            market_source=source,
        )["order"]
        assert rejected["status"] == "rejected"
        assert reason in rejected["rejection_reason"]


def test_limit_price_must_be_marketable(store: SimulationStore, clock: Clock) -> None:
    order_id = store.propose(**proposal(
        clock, "req-limit-create", limit_price=9.9,
    ))["order"]["order_id"]
    store.approve(
        order_id,
        client_request_id="req-limit-approve",
        approved_by="operator",
    )
    rejected = store.fill(
        order_id,
        client_request_id="req-limit-fill",
        market_price=10.0,
        as_of=clock().isoformat(),
        market_source="tencent_quote",
    )["order"]

    assert rejected["status"] == "rejected"
    assert rejected["rejection_reason"] == "buy_limit_not_marketable"


def test_market_order_can_fill_after_manual_approval(store: SimulationStore, clock: Clock) -> None:
    order_id = store.propose(**proposal(
        clock,
        "req-market-create",
        order_type="market",
        limit_price=None,
    ))["order"]["order_id"]
    store.approve(
        order_id,
        client_request_id="req-market-approve",
        approved_by="operator",
    )
    order = store.fill(
        order_id,
        client_request_id="req-market-fill",
        market_price=10.02,
        as_of=clock().isoformat(),
        market_source="tdx_api_quote",
    )["order"]

    assert order["status"] == "filled"
    assert order["order_type"] == "market"


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

    order_id = first["order"]["order_id"]
    approved = store.approve(
        order_id,
        client_request_id="req-idem-approve",
        approved_by="operator",
    )
    approved_replay = store.approve(
        order_id,
        client_request_id="req-idem-approve",
        approved_by="operator",
    )
    assert approved["order"]["status"] == "approved"
    assert approved_replay["idempotent_replay"] is True
    assert [event["event_type"] for event in approved_replay["order"]["events"]].count("approved") == 1


def test_cancel_is_terminal_and_append_only(store: SimulationStore, clock: Clock) -> None:
    order_id = store.propose(**proposal(clock, "req-cancel-create"))["order"]["order_id"]
    cancelled = store.cancel(
        order_id,
        client_request_id="req-cancel-order",
        reason="operator_changed_plan",
    )["order"]

    assert cancelled["status"] == "cancelled"
    assert cancelled["rejection_reason"] == "operator_changed_plan"
    assert [event["event_type"] for event in cancelled["events"]] == ["proposed", "cancelled"]
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
    store.approve(
        approved_id,
        client_request_id="req-stop-approve",
        approved_by="operator",
    )

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
    assert new_order["status"] == "proposed"


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
    assert second.get_order(order_id)["status"] == "proposed"
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
    monkeypatch.setattr(routes.stock, "quick_quote", lambda _symbol: {
        "price": quote_state["price"],
        "as_of": clock().isoformat(),
        "stale": quote_state.get("stale", False),
    })


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
    assert created["order"]["status"] == "proposed"
    assert created["order"]["reference_price"] == 10.0
    assert created["order"]["risk_level"] == 2
    assert created["order"]["data_stale"] is False
    assert created["order"]["data_missing"] == []
    order_id = created["order"]["order_id"]

    approved = asyncio.run(routes.simulation_approve_order(
        order_id,
        Request(store, {"client_request_id": "req-api-approve", "approved_by": "operator"}),
    ))
    assert approved["order"]["status"] == "approved"

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
    assert filled["order"]["market_source"] == "server_stock_quick_quote"
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
    assert replay["order"]["status"] == "proposed"
    assert replay["order"]["reference_price"] == 10.0
    assert replay["order"]["risk_level"] == 2

    conflicting = asyncio.run(routes.simulation_propose_order(Request(
        store,
        {**client_intent, "quantity": 200},
    )))
    payload = _json_response(conflicting)
    assert conflicting.status_code == 409
    assert payload["error_code"] == "idempotency_conflict"


def test_simulation_api_fill_fails_closed_on_server_stale_quote(
    store: SimulationStore, clock: Clock, monkeypatch,
) -> None:
    quote_state = {"price": 10.0, "stale": False}
    _install_server_authority(monkeypatch, clock, quote_state)
    created = asyncio.run(routes.simulation_propose_order(Request(
        store, proposal(clock, "req-stale-api-create"),
    )))
    order_id = created["order"]["order_id"]
    asyncio.run(routes.simulation_approve_order(
        order_id,
        Request(store, {"client_request_id": "req-stale-api-approve", "approved_by": "operator"}),
    ))
    quote_state["stale"] = True

    filled = asyncio.run(routes.simulation_fill_order(
        order_id,
        Request(store, {
            "client_request_id": "req-stale-api-fill",
            "market_price": 10.0,
            "as_of": clock().isoformat(),
            "market_source": "tencent_quote",
        }),
    ))

    assert filled["order"]["status"] == "rejected"
    assert "market_data_stale" in filled["order"]["rejection_reason"]


def test_simulation_api_returns_stable_manual_approval_error(
    store: SimulationStore, clock: Clock, monkeypatch,
) -> None:
    quote_state = {"price": 10.0, "stale": False}
    _install_server_authority(monkeypatch, clock, quote_state)
    created = asyncio.run(routes.simulation_propose_order(Request(
        store, proposal(clock, "req-api-noapprove"),
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
    payload = _json_response(response)

    assert response.status_code == 409
    assert payload == {
        "ok": False,
        "error": "只有人工批准后的 approved 订单可模拟成交，当前为 proposed",
        "error_code": "manual_approval_required",
        "simulation_only": True,
    }
