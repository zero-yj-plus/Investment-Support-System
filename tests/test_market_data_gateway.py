from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
import sqlite3

import pytest

from hermes_trader.data.evidence_store import (
    EvidenceStore,
    canonical_hash,
    sanitize_url,
)
from hermes_trader.data.gateway import (
    AdapterResult,
    DataPoint,
    FieldPolicy,
    MarketDataGateway,
)


NOW = datetime(2026, 7, 13, 2, 0, 10, tzinfo=timezone.utc)


class FakeAdapter:
    def __init__(
        self,
        source_id: str,
        result: AdapterResult | dict | None,
        *,
        tier: int = 2,
    ):
        self.source_id = source_id
        self.source_tier = tier
        self.result = result
        self.calls = 0
        self.source_meta = {
            "provider": f"{source_id} provider",
            "purpose": "test market data",
            "refresh_seconds": 3,
            "rate_limit": "10 requests/second",
            "license": "public endpoint; terms unknown",
            "owner": "data-team",
            "cost": "free",
            "known_risks": "no SLA",
        }

    def fetch(self, symbol: str, field: str):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class UnsafeHealthAdapter(FakeAdapter):
    def health(self):
        return {
            "breaker": {
                "state": "token=secret-state",
                "failure_threshold": 3,
                "token": "secret-token",
            },
            "concurrency": {
                "limit": 2,
                "in_flight": "password=secret-password",
                "password": "secret-password",
            },
            "source_url": "https://u:p@example.test/q?api_key=secret",
        }


def result(
    value,
    *,
    source_id: str,
    event_time: str = "2026-07-13T02:00:05Z",
    fetched_at: str = "2026-07-13T02:00:06Z",
    unit: str = "CNY",
    source_url: str | None = None,
    batch_id: str | None = None,
    batch_status: str = "complete",
    batch_expected_count: int = 8,
    batch_present_count: int = 8,
) -> AdapterResult:
    return AdapterResult(
        value=value,
        raw_payload={"value": value, "symbol": "000001.SZ"},
        event_time=event_time,
        available_at=fetched_at,
        fetched_at=fetched_at,
        trading_day="2026-07-13",
        unit=unit,
        source_id=source_id,
        source_url=source_url or f"https://example.test/{source_id}",
        batch_id=batch_id or f"quote-batch/{source_id}/test",
        batch_status=batch_status,
        batch_expected_count=batch_expected_count,
        batch_present_count=batch_present_count,
    )


def policy(**overrides) -> FieldPolicy:
    data = {
        "field": "last_price",
        "unit": "CNY",
        "meaning": "最近可观察成交价",
        "value_semantics": "point_in_time_snapshot",
        "route": ("primary", "backup"),
        "conflict_tolerance_pct": 0.5,
        "min_value": 0.01,
        "max_value": 100_000,
    }
    data.update(overrides)
    return FieldPolicy(**data)


def point(
    *,
    value: float,
    available_at: str,
    fetched_at: str,
    evidence_id: str | None = None,
) -> DataPoint:
    return DataPoint(
        symbol="000001.SZ",
        market="CN-A",
        field="last_price",
        value=value,
        unit="CNY",
        source_id="primary",
        source_tier=2,
        source_url="https://example.test/quote",
        event_time="2026-07-13T01:00:00Z",
        available_at=available_at,
        fetched_at=fetched_at,
        trading_day="2026-07-13",
        freshness="live",
        fallback_level=0,
        quality_status="ok",
        raw_payload_hash=None,
        route_version="v1",
        evidence_id=evidence_id,
    )


def register_primary(store: EvidenceStore) -> None:
    store.register_source(
        "primary",
        tier=2,
        provider="Primary",
        purpose="quote",
        fields=["last_price"],
        refresh_seconds=3,
        rate_limit="10/s",
        license="public terms unknown",
        owner="data-team",
        cost="free",
        known_risks="no SLA",
    )


def test_canonical_hash_and_url_sanitization_are_deterministic() -> None:
    assert canonical_hash({"b": 2, "a": [1, {"z": True}]}) == canonical_hash(
        {"a": [1, {"z": True}], "b": 2}
    )
    assert canonical_hash({"value": 10}) != canonical_hash({"value": 11})

    clean = sanitize_url(
        "https://user:pass@example.test:8443/q?symbol=000001&token=secret"
        "&api_key=also-secret#private"
    )
    parts = urlsplit(clean)
    query = parse_qs(parts.query)
    assert parts.netloc == "example.test:8443"
    assert parts.fragment == ""
    assert query["symbol"] == ["000001"]
    assert query["token"] == ["[REDACTED]"]
    assert query["api_key"] == ["[REDACTED]"]
    assert "user" not in clean and "secret" not in clean
    assert sanitize_url("https://example.test:invalid/q") is None


def test_evidence_store_wal_registry_raw_dedup_lineage_and_pit(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    register_primary(store)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

    source = store.get_source("primary")
    assert source is not None
    assert source["fields"] == ["last_price"]
    for key in (
        "tier", "provider", "purpose", "refresh_seconds", "rate_limit",
        "license", "owner", "cost", "known_risks",
    ):
        assert key in source

    first_hash = store.put_raw_payload(
        "primary",
        {"b": 2, "a": 1},
        fetched_at="2026-07-13T01:00:00Z",
        source_url="https://u:p@example.test/q?token=secret&symbol=1",
    )
    second_hash = store.put_raw_payload(
        "primary",
        {"a": 1, "b": 2},
        fetched_at="2026-07-13T01:01:00Z",
        source_url="https://example.test/q",
    )
    assert first_hash == second_hash
    stored_raw = store.get_raw_payload(first_hash)
    assert stored_raw is not None
    assert stored_raw["payload"] == {"a": 1, "b": 2}
    assert "secret" not in (stored_raw["source_url"] or "")

    old_id = store.save_point(
        point(
            value=10.0,
            available_at="2026-07-13T01:05:00Z",
            fetched_at="2026-07-13T01:05:00Z",
        )
    )
    revised_id = store.save_point(
        point(
            value=10.8,
            available_at="2026-07-13T03:00:00Z",
            fetched_at="2026-07-13T03:00:00Z",
        ),
        parents=[(old_id, "revision_of")],
    )
    middle = store.query_as_of(
        "000001.SZ", "last_price", "2026-07-13T02:00:00Z"
    )
    assert middle is not None and middle["value"] == pytest.approx(10.0)
    latest = store.query_as_of(
        "000001.SZ", "last_price", "2026-07-13T04:00:00Z"
    )
    assert latest is not None and latest["value"] == pytest.approx(10.8)
    assert store.get_lineage(revised_id) == [
        {"parent_id": old_id, "role": "revision_of"}
    ]

    listed = store.list_points(symbol="000001.SZ", field="last_price", limit=1)
    assert len(listed) == 1 and listed[0]["evidence_id"] == revised_id
    stats = store.stats()
    assert stats["sources"] == 1
    assert stats["raw_payloads"] == 1
    assert stats["evidence_points"] == 2
    assert stats["lineage_edges"] == 1
    assert stats["by_quality"] == {"ok": 2}
    assert stats["latest_fetched_at"] == "2026-07-13T03:00:00.000000Z"

    same_again = store.save_point(
        point(
            value=10.8,
            available_at="2026-07-13T03:00:00Z",
            fetched_at="2026-07-13T03:00:00Z",
        )
    )
    assert same_again == revised_id
    assert store.stats()["evidence_points"] == 2


def test_raw_payload_dedup_is_scoped_to_source(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "source-scoped-raw.sqlite3")
    register_primary(store)
    store.register_source(
        "backup",
        tier=3,
        provider="Backup",
        purpose="quote cross-check",
        fields=["last_price"],
    )

    primary_hash = store.put_raw_payload("primary", {"value": 10.0})
    backup_hash = store.put_raw_payload("backup", {"value": 10.0})

    assert primary_hash != backup_hash
    assert store.get_raw_payload(primary_hash)["source_id"] == "primary"
    assert store.get_raw_payload(backup_hash)["source_id"] == "backup"


def test_gateway_selects_primary_persists_cross_check_and_reports_live(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "gateway.sqlite3")
    primary = FakeAdapter(
        "primary",
        result(
            10.0,
            source_id="primary",
            source_url="https://user:pass@primary.test/q?token=secret&symbol=1",
        ),
    )
    backup = FakeAdapter(
        "backup",
        result(
            10.01,
            source_id="backup",
            event_time="2026-07-13T02:00:06Z",
            fetched_at="2026-07-13T02:00:07Z",
        ),
    )
    gateway = MarketDataGateway(
        store=store,
        policies=[policy()],
        adapters={"primary": primary, "backup": backup},
        now_fn=lambda: NOW,
    )

    selected = gateway.get("000001.SZ", "last_price", session="intraday")

    assert selected.value == pytest.approx(10.0)
    assert selected.source_id == "primary"
    assert selected.fallback_level == 0
    assert selected.freshness == "live"
    assert selected.quality_status == "ok"
    assert selected.field_meaning == "最近可观察成交价"
    assert selected.value_semantics == "point_in_time_snapshot"
    assert selected.batch_id == "quote-batch/primary/test"
    assert selected.batch_status == "complete"
    assert selected.batch_expected_count == 8
    assert selected.batch_present_count == 8
    assert selected.degraded_reason is None
    assert selected.raw_payload_hash
    assert selected.evidence_id
    assert "secret" not in (selected.source_url or "")
    stored = store.get_point(selected.evidence_id)
    assert stored["value"] == pytest.approx(10.0)
    assert stored["observation_role"] == "selected"
    assert stored["field_meaning"] == "最近可观察成交价"
    assert stored["batch_status"] == "complete"
    assert stored["contract_version"] == "field-contract-v2"
    lineage = store.get_lineage(selected.evidence_id)
    assert len(lineage) == 1 and lineage[0]["role"] == "cross_check"
    cross = store.get_point(lineage[0]["parent_id"])
    assert cross is not None and cross["observation_role"] == "cross_check"
    assert store.get_source("primary")["fields"] == ["last_price"]
    assert primary.calls == backup.calls == 1
    contract = gateway.contract()
    assert contract["schema_version"] == "field-contract-v2"
    assert contract["fields"]["last_price"] == {
        "meaning": "最近可观察成交价",
        "unit": "CNY",
        "value_semantics": "point_in_time_snapshot",
        "route": ["primary", "backup"],
        "route_version": "v1",
        "batch_scope": "symbol_snapshot",
        "live_max_age_seconds": 15.0,
        "delayed_max_age_seconds": 300.0,
        "live_sessions": ["intraday", "tail"],
    }

    # PIT 必须回放当时真正 selected 的主源；即使备源更晚
    # available/fetched，也不能因 ORDER BY 时间把 cross-check 翻成决策值。
    historical = gateway.get(
        "000001.SZ", "last_price", as_of="2026-07-13T02:00:10Z"
    )
    assert historical.value == pytest.approx(10.0)
    assert historical.source_id == "primary"
    assert primary.calls == backup.calls == 1


def test_evidence_prune_removes_old_points_lineage_and_orphan_raw(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "prune.sqlite3")
    register_primary(store)
    raw_hash = store.put_raw_payload("primary", {"value": 10.0})
    parent = store.save_point(point(
        value=10.0,
        available_at="2026-01-01T01:00:00Z",
        fetched_at="2026-01-01T01:00:00Z",
    ))
    child = store.save_point(
        point(
            value=10.1,
            available_at="2026-01-01T01:01:00Z",
            fetched_at="2026-01-01T01:01:00Z",
        ),
        parents=[(parent, "revision_of")],
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE evidence_point SET raw_payload_hash=?,created_at=?",
            (raw_hash, "2020-01-01T00:00:00.000000Z"),
        )

    removed = store.prune()

    assert removed["evidence_points"] == 2
    assert store.get_point(parent) is None and store.get_point(child) is None
    assert store.get_raw_payload(raw_hash) is None


def test_gateway_fallback_conflict_and_validation_contracts(tmp_path) -> None:
    fallback_store = EvidenceStore(tmp_path / "fallback.sqlite3")
    primary = FakeAdapter("primary", None)
    backup = FakeAdapter("backup", result(10.2, source_id="backup"))
    fallback_gateway = MarketDataGateway(
        store=fallback_store,
        policies=[policy()],
        adapters={"primary": primary, "backup": backup},
        now_fn=lambda: NOW,
    )
    fallback = fallback_gateway.get("000001.SZ", "last_price")
    assert fallback.source_id == "backup"
    assert fallback.fallback_level == 1
    assert fallback.quality_status == "degraded"
    assert fallback.quality_reasons == ("fallback_source",)
    assert fallback.degraded_reason == "主源不可用，当前字段使用备用源"

    conflict_store = EvidenceStore(tmp_path / "conflict.sqlite3")
    conflict_gateway = MarketDataGateway(
        store=conflict_store,
        policies=[policy()],
        adapters={
            "primary": FakeAdapter("primary", result(10.0, source_id="primary")),
            "backup": FakeAdapter("backup", result(11.0, source_id="backup")),
        },
        now_fn=lambda: NOW,
    )
    conflicted = conflict_gateway.get("000001.SZ", "last_price")
    assert conflicted.value == pytest.approx(10.0)
    assert conflicted.quality_status == "conflict"

    unit_store = EvidenceStore(tmp_path / "unit.sqlite3")
    unit_gateway = MarketDataGateway(
        store=unit_store,
        policies=[policy()],
        adapters={
            "primary": FakeAdapter(
                "primary", result(10.0, source_id="primary", unit="USD")
            ),
            "backup": FakeAdapter("backup", result(10.1, source_id="backup")),
        },
        now_fn=lambda: NOW,
    )
    unit_fallback = unit_gateway.get("000001.SZ", "last_price")
    assert unit_fallback.source_id == "backup"
    assert unit_fallback.quality_status == "degraded"


def test_pit_as_of_never_calls_live_adapters_and_does_not_leak_revisions(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "pit.sqlite3")
    register_primary(store)
    store.save_point(
        point(
            value=9.8,
            available_at="2026-07-13T01:00:00Z",
            fetched_at="2026-07-13T01:00:00Z",
        )
    )
    store.save_point(
        point(
            value=12.0,
            available_at="2026-07-13T03:00:00Z",
            fetched_at="2026-07-13T03:00:00Z",
        )
    )
    primary = FakeAdapter("primary", result(99.0, source_id="primary"))
    backup = FakeAdapter("backup", result(98.0, source_id="backup"))
    gateway = MarketDataGateway(
        store=store,
        policies=[policy()],
        adapters={"primary": primary, "backup": backup},
        now_fn=lambda: NOW,
    )

    historical = gateway.get(
        "000001.SZ", "last_price", as_of="2026-07-13T02:00:00Z"
    )
    assert historical.value == pytest.approx(9.8)
    assert primary.calls == backup.calls == 0

    absent = gateway.get(
        "000001.SZ", "last_price", as_of="2026-07-12T23:00:00Z"
    )
    assert absent.value is None
    assert absent.freshness == "missing"
    assert absent.quality_status == "missing"
    assert "point-in-time" in (absent.missing_reason or "")
    assert primary.calls == backup.calls == 0


def test_gateway_freshness_does_not_call_postmarket_data_live(tmp_path) -> None:
    delayed_gateway = MarketDataGateway(
        store=EvidenceStore(tmp_path / "delayed.sqlite3"),
        policies=[policy()],
        adapters={
            "primary": FakeAdapter("primary", result(10.0, source_id="primary")),
            "backup": FakeAdapter("backup", None),
        },
        now_fn=lambda: NOW,
    )
    delayed = delayed_gateway.get(
        "000001.SZ", "last_price", session="postmarket"
    )
    assert delayed.freshness == "delayed"
    assert delayed.quality_status == "ok"

    stale_gateway = MarketDataGateway(
        store=EvidenceStore(tmp_path / "stale.sqlite3"),
        policies=[policy(delayed_max_age_seconds=60)],
        adapters={
            "primary": FakeAdapter(
                "primary",
                result(
                    10.0,
                    source_id="primary",
                    event_time="2026-07-13T01:00:00Z",
                    fetched_at="2026-07-13T01:00:05Z",
                ),
            ),
            "backup": FakeAdapter("backup", None),
        },
        now_fn=lambda: NOW,
    )
    stale = stale_gateway.get("000001.SZ", "last_price")
    assert stale.freshness == "stale"
    assert stale.quality_status == "degraded"


def test_gateway_all_sources_missing_returns_explicit_missing_without_evidence(tmp_path) -> None:
    gateway = MarketDataGateway(
        store=EvidenceStore(tmp_path / "missing.sqlite3"),
        policies=[policy()],
        adapters={
            "primary": FakeAdapter("primary", RuntimeError("down")),
            "backup": FakeAdapter("backup", None),
        },
        now_fn=lambda: NOW,
    )
    missing = gateway.get("000001.SZ", "last_price")
    assert missing.value is None
    assert missing.freshness == "missing"
    assert missing.quality_status == "missing"
    assert missing.batch_status == "missing"
    assert missing.degraded_reason == "all configured sources unavailable or invalid"
    assert missing.evidence_id is None
    assert missing.raw_payload_hash is None


def test_gateway_health_allowlists_adapter_metadata(tmp_path) -> None:
    gateway = MarketDataGateway(
        store=EvidenceStore(tmp_path / "health-allowlist.sqlite3"),
        policies=[policy(route=("primary",))],
        adapters={
            "primary": UnsafeHealthAdapter(
                "primary", result(10.0, source_id="primary")
            )
        },
        now_fn=lambda: NOW,
    )
    health = gateway.health()
    assert health["sources"]["primary"] == {
        "breaker": {"state": "unknown", "failure_threshold": 3},
        "concurrency": {"limit": 2},
    }
    assert "secret" not in repr(health)
