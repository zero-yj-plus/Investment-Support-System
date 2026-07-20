from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_trader.api import routes
from hermes_trader.data.evidence_store import EvidenceStore
from hermes_trader.data.gateway import (
    AdapterResult,
    FieldPolicy,
    MarketDataGateway,
)


NOW = datetime(2026, 7, 13, 2, 0, 10, tzinfo=timezone.utc)


class CountingAdapter:
    source_tier = 2

    def __init__(self, source_id: str, value: float) -> None:
        self.source_id = source_id
        self.value = value
        self.calls = 0
        self.source_meta = {
            "provider": f"{source_id} provider",
            "purpose": "Phase 2 API contract test",
            "refresh_seconds": 3,
            "rate_limit": "10/s",
            "license": "public endpoint; terms unknown",
            "owner": "test",
            "cost": "free; no API key",
            "known_risks": "no SLA",
        }

    def fetch(self, symbol: str, field: str) -> AdapterResult:
        self.calls += 1
        return AdapterResult(
            value=self.value,
            raw_payload={"symbol": symbol, "field": field, "value": self.value},
            event_time="2026-07-13T02:00:05Z",
            available_at="2026-07-13T02:00:06Z",
            fetched_at="2026-07-13T02:00:06Z",
            trading_day="2026-07-13",
            unit="CNY",
            source_id=self.source_id,
            source_tier=self.source_tier,
            source_url=f"https://example.test/{self.source_id}?symbol={symbol}",
        )


def _gateway(tmp_path) -> tuple[MarketDataGateway, CountingAdapter, CountingAdapter]:
    primary = CountingAdapter("primary", 10.0)
    backup = CountingAdapter("backup", 10.01)
    gateway = MarketDataGateway(
        store=EvidenceStore(tmp_path / "evidence.sqlite3"),
        policies=[
            FieldPolicy(
                field="last_price",
                unit="CNY",
                meaning="最近可观察成交价",
                value_semantics="point_in_time_snapshot",
                route=("primary", "backup"),
                conflict_tolerance_pct=0.5,
                min_value=0.01,
            )
        ],
        adapters={"primary": primary, "backup": backup},
        now_fn=lambda: NOW,
    )
    return gateway, primary, backup


def _api_client(monkeypatch, gateway: MarketDataGateway) -> TestClient:
    monkeypatch.setattr(
        routes,
        "build_market_context",
        lambda: {"market_session": "intraday"},
    )
    app = FastAPI()
    app.state.market_gateway = gateway
    app.state.evidence_store = gateway.store
    app.include_router(routes.router)
    return TestClient(app)


def test_create_app_assembles_gateway_store_and_engine(monkeypatch, tmp_path) -> None:
    from hermes_trader import main
    from hermes_trader.data import runtime_gateway

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
client:
  id: phase2-test
  name: Phase 2 Test
server:
  data_dir: ./data
hermes:
  base_url: https://example.invalid/v1
  api_key: test-only
push:
  enabled: false
schedule:
  timezone: Asia/Shanghai
  trading_days_only: false
data_source:
  policy: free_only
""".strip(),
        encoding="utf-8",
    )
    fake_store = object()
    fake_gateway = SimpleNamespace(store=fake_store)
    calls = []

    def fake_factory(data_dir):
        calls.append(data_dir)
        return fake_gateway

    monkeypatch.setattr(runtime_gateway, "create_runtime_gateway", fake_factory)

    app = main.create_app(str(config_path))

    assert calls == [(tmp_path / "data").resolve()]
    assert app.state.market_gateway is fake_gateway
    assert app.state.evidence_store is fake_store
    assert app.state.engine.market_gateway is fake_gateway


def test_market_data_api_returns_datapoint_rejects_unknown_field_and_pit_is_offline(
    monkeypatch, tmp_path,
) -> None:
    gateway, primary, backup = _gateway(tmp_path)
    client = _api_client(monkeypatch, gateway)

    live = client.get("/api/market-data/000001.SZ/last_price")
    assert live.status_code == 200
    payload = live.json()
    assert payload["ok"] is True
    assert payload["data"]["field"] == "last_price"
    assert payload["data"]["value"] == 10.0
    assert payload["data"]["source_id"] == "primary"
    assert payload["data"]["field_meaning"] == "最近可观察成交价"
    assert payload["data"]["contract_version"] == "field-contract-v2"
    assert payload["data"]["evidence_id"]
    assert (primary.calls, backup.calls) == (1, 1)

    historical = client.get(
        "/api/market-data/000001.SZ/last_price",
        params={"as_of": "2026-07-13T02:00:10Z"},
    )
    assert historical.status_code == 200
    assert historical.json()["data"]["value"] == 10.0
    assert historical.json()["data"]["source_id"] == "primary"
    # PIT contract: once as_of is supplied, the gateway must only read evidence storage.
    assert (primary.calls, backup.calls) == (1, 1)

    monkeypatch.setattr(
        routes,
        "build_market_context",
        lambda: (_ for _ in ()).throw(AssertionError("PIT must stay offline")),
    )
    offline_historical = client.get(
        "/api/market-data/000001.SZ/last_price",
        params={"as_of": "2026-07-13T02:00:10Z"},
    )
    assert offline_historical.status_code == 200
    assert offline_historical.json()["data"]["value"] == 10.0

    naive = client.get(
        "/api/market-data/000001.SZ/last_price",
        params={"as_of": "2026-07-13T02:00:10"},
    )
    assert naive.status_code == 400
    assert "Z 或 UTC offset" in naive.json()["error"]

    unknown = client.get("/api/market-data/000001.SZ/not_a_field")
    assert unknown.status_code == 404
    assert unknown.json() == {"ok": False, "error": "未知标准字段: not_a_field"}

    contract = client.get("/api/market-data/contract")
    assert contract.status_code == 200
    assert contract.json()["schema_version"] == "field-contract-v2"
    assert contract.json()["fields"]["last_price"]["unit"] == "CNY"
    assert contract.json()["fields"]["last_price"]["meaning"] == "最近可观察成交价"


def test_evidence_registry_stats_points_and_detail_endpoints(monkeypatch, tmp_path) -> None:
    gateway, _primary, _backup = _gateway(tmp_path)
    client = _api_client(monkeypatch, gateway)
    selected = client.get("/api/market-data/000001.SZ/last_price").json()["data"]

    sources = client.get("/api/evidence/sources")
    assert sources.status_code == 200
    assert sources.json()["ok"] is True
    assert sources.json()["count"] == 2
    assert {row["source_id"] for row in sources.json()["sources"]} == {
        "primary", "backup",
    }
    assert all(row["cost"] == "free; no API key" for row in sources.json()["sources"])

    stats = client.get("/api/evidence/stats")
    assert stats.status_code == 200
    assert stats.json()["ok"] is True
    assert stats.json()["sources"] == 2
    assert stats.json()["evidence_points"] == 2
    assert stats.json()["lineage_edges"] == 1

    points = client.get(
        "/api/evidence/points",
        params={"symbol": "000001.SZ", "field": "last_price", "limit": 1},
    )
    assert points.status_code == 200
    assert points.json()["ok"] is True
    assert points.json()["count"] == 1
    assert points.json()["points"][0]["symbol"] == "000001.SZ"

    denied = client.get(
        f"/api/evidence/{selected['evidence_id']}",
        params={"include_raw": "true"},
    )
    assert denied.status_code == 403
    assert "显式开启" in denied.json()["error"]

    monkeypatch.setenv("HERMES_ALLOW_RAW_EVIDENCE", "1")
    detail = client.get(
        f"/api/evidence/{selected['evidence_id']}",
        params={"include_raw": "true"},
    )
    body = detail.json()
    assert detail.status_code == 200
    assert body["ok"] is True
    assert body["point"]["value"] == 10.0
    assert body["source"]["source_id"] == "primary"
    assert body["lineage"][0]["role"] == "cross_check"
    assert body["lineage"][0]["point"]["source_id"] == "backup"
    assert body["raw_payload"]["payload"]["value"] == 10.0

    missing = client.get("/api/evidence/evidence-does-not-exist")
    assert missing.status_code == 404
    assert missing.json() == {"ok": False, "error": "证据不存在"}
