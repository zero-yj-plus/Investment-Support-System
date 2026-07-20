from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
import time

import pytest

from hermes_trader.data.evidence_store import EvidenceStore
from hermes_trader.data.gateway import MarketDataGateway
from hermes_trader.data.gateway import FieldPolicy
from hermes_trader.data import tencent_source
from hermes_trader.data.runtime_gateway import (
    FIELD_SEMANTICS,
    FIELD_UNITS,
    QuoteSnapshot,
    TdxApiQuoteAdapter,
    TencentQuoteAdapter,
    _canonical_symbol,
    _parse_tencent_event_time,
    _parse_tencent_quote,
    create_runtime_gateway,
    runtime_field_policies,
)


NOW = datetime(2026, 7, 13, 2, 0, 10, tzinfo=timezone.utc)  # 10:00:10 CST


class ManualMonotonic:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def complete_quote(price: float = 10.0) -> dict:
    return {
        "price": price,
        "prev_close": 9.8,
        "change_pct": 2.04,
        "open": 9.9,
        "high": 10.2,
        "low": 9.75,
        "amount": 123_450_000.0,
        "volume": 12_345_600,
        "turnover_rate": 2.5,
        "volume_ratio": 1.3,
        "pe": 12.2,
        "pb": 1.4,
        "total_mv": 20_500_000_000.0,
        "float_mv": 12_300_000_000.0,
    }


def test_runtime_policies_cover_all_fields_with_explicit_units_and_routes() -> None:
    policies = {item.field: item for item in runtime_field_policies()}
    assert set(policies) == set(FIELD_UNITS)
    assert {name: item.unit for name, item in policies.items()} == FIELD_UNITS
    assert {name: item.meaning for name, item in policies.items()} == FIELD_SEMANTICS
    assert all(item.batch_scope == "provider_symbol_quote_snapshot" for item in policies.values())

    tdx_api_fields = {
        "last_price", "prev_close", "change_pct", "open", "high", "low",
        "amount", "volume",
    }
    for field in tdx_api_fields:
        assert policies[field].route == ("tdx_api_quote", "tencent_quote")
    assert policies["turnover_rate"].route == ("tencent_quote",)
    for field in set(FIELD_UNITS) - tdx_api_fields - {"turnover_rate"}:
        assert policies[field].route == ("tencent_quote",)

    assert policies["change_pct"].min_value == -31.5
    assert policies["change_pct"].max_value == 31.5
    assert policies["amount"].min_value == 0
    assert policies["total_mv"].min_value == 1


def test_runtime_symbol_canonicalization_rejects_wrong_exchange_suffix() -> None:
    assert _canonical_symbol("000001") == "000001.SZ"
    assert _canonical_symbol("sz000001") == "000001.SZ"
    assert _canonical_symbol("000001.sz") == "000001.SZ"
    assert _canonical_symbol("600000.SH") == "600000.SH"
    assert _canonical_symbol("920001.BJ") == "920001.BJ"
    assert _canonical_symbol("000001.SH") is None
    assert _canonical_symbol("sh000001.sz") is None
    assert _canonical_symbol("not-a-symbol") is None
    assert tencent_source._BASE.startswith("https://")


def test_symbol_snapshot_cache_avoids_one_request_per_field_and_expires() -> None:
    calls: list[str] = []
    clock = ManualMonotonic()

    def loader(code: str) -> QuoteSnapshot:
        calls.append(code)
        return QuoteSnapshot(
            values=complete_quote(),
            raw_payload={"code": code, "raw": True},
            event_time="2026-07-13T10:00:05+08:00",
            trading_day="2026-07-13",
            source_url=f"https://quote.test/{code}",
        )

    adapter = TencentQuoteAdapter(
        loader=loader,
        now_fn=lambda: NOW,
        monotonic_fn=clock,
        trade_day_fn=lambda: "2026-07-13",
        ttl_seconds=3,
    )

    points = [adapter.fetch("000001.SZ", field) for field in FIELD_UNITS]
    assert calls == ["000001"]
    assert all(point is not None for point in points)
    assert {point.unit for point in points if point is not None} == set(FIELD_UNITS.values())
    assert all(point.trading_day == "2026-07-13" for point in points if point)
    assert all(point.source_id == "tencent_quote" for point in points if point)
    assert all(point.batch_status == "complete" for point in points if point)
    assert all(point.batch_expected_count == len(FIELD_UNITS) for point in points if point)
    assert len({point.batch_id for point in points if point}) == 1

    adapter.fetch("600000.SH", "last_price")
    assert calls == ["000001", "600000"]
    clock.value += 3.01
    adapter.fetch("000001", "last_price")
    assert calls == ["000001", "600000", "000001"]


def test_symbol_cache_and_lock_storage_are_strictly_bounded() -> None:
    adapter = TencentQuoteAdapter(
        loader=lambda code: QuoteSnapshot(
            values=complete_quote(),
            raw_payload={"code": code},
            event_time="2026-07-13T10:00:05+08:00",
            trading_day="2026-07-13",
        ),
        now_fn=lambda: NOW,
        monotonic_fn=lambda: 100.0,
        max_cache_entries=3,
        lock_stripes=5,
    )

    for index in range(10):
        assert adapter.fetch(f"{index:06d}", "last_price") is not None

    assert adapter.health()["cache"]["entries"] <= 3
    assert len(adapter._lock_stripes) == 5


def test_same_symbol_concurrent_field_fanout_is_coalesced() -> None:
    calls = 0
    calls_lock = threading.Lock()

    def loader(_code: str) -> QuoteSnapshot:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.03)
        return QuoteSnapshot(
            values=complete_quote(),
            raw_payload={"raw": True},
            event_time="2026-07-13T10:00:05+08:00",
            trading_day="2026-07-13",
        )

    adapter = TencentQuoteAdapter(loader=loader, now_fn=lambda: NOW)
    fields = list(FIELD_UNITS) * 3
    with ThreadPoolExecutor(max_workers=14) as pool:
        results = list(pool.map(lambda field: adapter.fetch("sz000001", field), fields))

    assert calls == 1
    assert all(result is not None for result in results)


def test_public_source_concurrency_is_bounded_process_wide() -> None:
    active = 0
    peak = 0
    guard = threading.Lock()

    def loader(code: str) -> QuoteSnapshot:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.03)
            return QuoteSnapshot(
                values=complete_quote(),
                raw_payload={"code": code},
                event_time="2026-07-13T10:00:05+08:00",
                trading_day="2026-07-13",
            )
        finally:
            with guard:
                active -= 1

    adapter = TencentQuoteAdapter(
        loader=loader,
        now_fn=lambda: NOW,
        max_concurrency=2,
        failure_threshold=20,
    )
    symbols = [f"{index:06d}" for index in range(1, 9)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda symbol: adapter.fetch(symbol, "last_price"), symbols)
        )

    assert all(item is not None for item in results)
    assert peak == 2
    assert adapter.health()["concurrency"] == {
        "limit": 2,
        "in_flight": 0,
        "peak_in_flight": 2,
    }


def test_circuit_breaker_stops_calls_and_half_open_success_recovers() -> None:
    clock = ManualMonotonic()
    calls: list[str] = []

    def loader(code: str):
        calls.append(code)
        if len(calls) <= 2:
            raise ConnectionError("transport unavailable")
        return QuoteSnapshot(
            values=complete_quote(),
            raw_payload={"code": code},
            event_time="2026-07-13T10:00:05+08:00",
            trading_day="2026-07-13",
        )

    adapter = TencentQuoteAdapter(
        loader=loader,
        now_fn=lambda: NOW,
        monotonic_fn=clock,
        missing_ttl_seconds=0.1,
        failure_threshold=2,
        cooldown_seconds=5,
    )
    assert adapter.fetch("000001", "last_price") is None
    clock.value += 0.11
    assert adapter.fetch("000002", "last_price") is None
    assert adapter.health()["breaker"]["state"] == "open"

    # New symbols cannot bypass the source-wide breaker.
    clock.value += 0.11
    assert adapter.fetch("000003", "last_price") is None
    assert adapter.fetch("000004", "last_price") is None
    assert calls == ["000001", "000002"]
    assert adapter.health()["breaker"]["rejected_calls"] == 2

    # Once cooling ends exactly one half-open probe may hit the source.  A
    # successful probe closes the breaker and resets consecutive failures.
    clock.value += 5.01
    recovered = adapter.fetch("000005", "last_price")
    assert recovered is not None and recovered.value == pytest.approx(10.0)
    assert calls == ["000001", "000002", "000005"]
    breaker = adapter.health()["breaker"]
    assert breaker["state"] == "closed"
    assert breaker["consecutive_failures"] == 0


def test_not_found_symbols_do_not_open_global_breaker() -> None:
    adapter = TencentQuoteAdapter(
        loader=lambda _code: None,
        now_fn=lambda: NOW,
        missing_ttl_seconds=0.1,
        failure_threshold=3,
    )

    for code in ("123456", "123457", "123458"):
        assert adapter.fetch(code, "last_price") is None

    health = adapter.health()
    assert health["breaker"]["state"] == "closed"
    assert health["breaker"]["consecutive_failures"] == 0
    assert health["requests"] == {
        "attempted": 3, "succeeded": 3, "failed": 0, "no_data": 3,
        "last_result": "no_data",
        "last_success_at": "2026-07-13T02:00:10+00:00",
        "last_failure_at": None,
    }


def test_fetched_at_is_response_availability_time_not_request_start() -> None:
    wall = {"now": datetime(2026, 7, 13, 2, 0, 0, tzinfo=timezone.utc)}

    def loader(_code: str) -> QuoteSnapshot:
        wall["now"] = datetime(2026, 7, 13, 2, 0, 2, tzinfo=timezone.utc)
        return QuoteSnapshot(
            values=complete_quote(),
            raw_payload={"raw": True},
            event_time="2026-07-13T10:00:01+08:00",
            trading_day="2026-07-13",
        )

    adapter = TencentQuoteAdapter(loader=loader, now_fn=lambda: wall["now"])
    point = adapter.fetch("000001", "last_price")
    assert point is not None
    assert point.fetched_at == datetime(
        2026, 7, 13, 2, 0, 2, tzinfo=timezone.utc
    )
    assert point.available_at == point.fetched_at


def test_tencent_raw_row_keeps_exchange_timestamp_and_normalizes_units() -> None:
    fields = [""] * 50
    fields[1] = "平安银行"
    fields[2] = "000001"
    fields[3] = "10.00"
    fields[4] = "9.80"
    fields[5] = "9.90"
    fields[6] = "123456"  # hands outside STAR Market
    fields[30] = "20260713100005"
    fields[32] = "2.04"
    fields[33] = "10.20"
    fields[34] = "9.75"
    fields[37] = "12345"  # ten-thousand CNY
    fields[38] = "2.50"
    fields[39] = "12.20"
    fields[44] = "123.00"  # hundred-million CNY
    fields[45] = "205.00"
    fields[46] = "1.40"
    fields[49] = "1.30"

    parsed = _parse_tencent_quote(fields)
    event = _parse_tencent_event_time(fields)
    assert parsed is not None
    assert parsed["volume"] == 12_345_600
    assert parsed["amount"] == pytest.approx(123_450_000)
    assert parsed["float_mv"] == pytest.approx(12_300_000_000)
    assert parsed["total_mv"] == pytest.approx(20_500_000_000)
    assert event is not None
    assert event.isoformat() == "2026-07-13T10:00:05+08:00"

    fields[30] = "20260713161421"
    assert _parse_tencent_event_time(fields).isoformat() == (
        "2026-07-13T15:00:00+08:00"
    )
    fields[30] = "20260713121500"
    assert _parse_tencent_event_time(fields).isoformat() == (
        "2026-07-13T11:30:00+08:00"
    )

    # STAR Market reports the raw volume in shares, not hands.
    fields[2] = "688981"
    assert _parse_tencent_quote(fields)["volume"] == 123_456
    # Beijing shares may legitimately move by 30%; other boards may not.
    fields[2], fields[32] = "920001", "29.9"
    assert _parse_tencent_quote(fields) is not None
    fields[2] = "000001"
    assert _parse_tencent_quote(fields) is None


def test_tdx_api_adapter_uses_reported_age_as_evidence_time() -> None:
    adapter = TdxApiQuoteAdapter(
        loader=lambda _code: QuoteSnapshot(
            values=complete_quote(),
            raw_payload={"provider": "tdx-api"},
            event_time="2026-07-13T09:59:58+08:00",
            trading_day="2026-07-13",
            source_url="tdx-api://realtime/quote/000001",
        ),
        now_fn=lambda: NOW,
        monotonic_fn=lambda: 1.0,
        trade_day_fn=lambda: "2026-07-13",
    )

    price = adapter.fetch("000001.SZ", "last_price")

    assert price is not None
    assert price.source_id == "tdx_api_quote"
    assert price.event_time == "2026-07-13T09:59:58+08:00"
    assert price.event_time_inferred is False
    assert adapter.fetch("000001.SZ", "turnover_rate") is None


def test_gateway_health_exposes_breaker_concurrency_and_cache_without_secrets(tmp_path) -> None:
    clock = ManualMonotonic()

    def unsafe_loader(_code: str):
        raise RuntimeError("token=do-not-leak&password=also-secret")

    adapter = TencentQuoteAdapter(
        loader=unsafe_loader,
        now_fn=lambda: NOW,
        monotonic_fn=clock,
        failure_threshold=1,
        cooldown_seconds=9,
    )
    gateway = MarketDataGateway(
        store=EvidenceStore(tmp_path / "health.sqlite3"),
        policies=[
            FieldPolicy(
                field="last_price", unit="CNY", route=("tencent_quote",)
            )
        ],
        adapters={"tencent_quote": adapter},
        now_fn=lambda: NOW,
    )
    assert gateway.get("000001", "last_price").quality_status == "missing"

    health = gateway.health()
    source = health["sources"]["tencent_quote"]
    assert health["schema_version"] == "market-data-health-v1"
    assert source["breaker"]["state"] == "open"
    assert source["breaker"]["cooldown_remaining_seconds"] == 9
    assert source["concurrency"]["limit"] == 4
    assert source["cache"]["negative_entries"] == 1
    assert source["requests"] == {
        "attempted": 1, "succeeded": 0, "failed": 1, "no_data": 0,
        "last_result": "failure",
        "last_success_at": None,
        "last_failure_at": "2026-07-13T02:00:10+00:00",
    }
    assert "do-not-leak" not in repr(health)
    assert "also-secret" not in repr(health)


def test_factory_creates_durable_store_and_registers_all_quote_sources(tmp_path) -> None:
    gateway = create_runtime_gateway(tmp_path)
    assert gateway.store.path == tmp_path / "evidence" / "market_data.sqlite3"
    assert gateway.store.path.exists()
    assert set(gateway.adapters) == {"tdx_api_quote", "tencent_quote"}
    sources = {item["source_id"]: item for item in gateway.store.list_sources()}
    assert set(sources) == {"tdx_api_quote", "tencent_quote"}
    assert sources["tdx_api_quote"]["cost"] == "customer-provided access; no per-call points"
    assert sources["tencent_quote"]["cost"] == "free; no API key"
    assert set(gateway.policies) == set(FIELD_UNITS)
    assert gateway.symbol_normalizer("000001") == "000001.SZ"
