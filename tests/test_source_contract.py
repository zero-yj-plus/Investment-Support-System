from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from hermes_trader.api import routes
from hermes_trader.data import sources


def test_source_catalog_reports_actual_field_routes(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_DEPLOYMENT_PROFILE", "cn_home")
    monkeypatch.setenv("HERMES_DEPLOYMENT_PROFILE_CONFIGURED", "auto")
    monkeypatch.delenv("DATA_RELAY_API", raising=False)
    monkeypatch.delenv("DATA_RELAY_PROXY", raising=False)
    monkeypatch.setattr(sources, "source_metrics", lambda: {
        "TDX API 实时行情": {
            "last_ok": True, "last_latency_ms": 18, "last_ts": "2026-07-13 09:35:02",
            "last_epoch": 13, "last_mode": "tdx_api", "calls": 1, "fails": 0,
        },
        "腾讯行情": {
            "last_ok": True, "last_latency_ms": 21, "last_ts": "2026-07-13 09:35:01",
            "last_epoch": 10, "last_mode": "tencent", "calls": 2, "fails": 0,
        },
        "涨跌停池(U,公开源)": {
            "last_ok": True, "last_latency_ms": 82, "last_ts": "2026-07-13 15:31:02",
            "last_epoch": 11, "last_mode": "direct", "calls": 1, "fails": 0,
        },
    })

    report = sources.build_report({"relay": {"enabled": False}}, {
        "source": "沪深京交易所官网公开列表", "count": 5231,
        "complete": True, "stale": False, "refresh_required": False,
        "fetched_at": "2026-07-13T09:34:59+08:00", "missing_exchanges": [],
    })
    by_key = {row["key"]: row for row in report["sources"]}

    assert report["source_contract"] == "field-route-v2"
    assert report["active_policy"] == "free_only"
    assert report["deployment"]["profile"] == "cn_home"
    assert report["deployment"]["china_public_route"] == "direct_low_concurrency"
    assert report["deployment"]["quote_primary"] == "tdx_api"
    assert report["deployment"]["quote_fallback"] == "tencent"
    assert by_key["tdx_api"]["status"] == "ok"
    assert by_key["tdx_api"]["channel"] == "TDX API"
    assert "秒级" in by_key["tdx_api"]["feeds"]
    assert by_key["tencent"]["status"] == "ok"
    assert by_key["tencent"]["provider"] == "qt.gtimg.cn"
    assert "tushare" not in by_key
    assert by_key["public_structure"]["status"] == "ok"
    assert "免费" in by_key["public_structure"]["role"]
    assert "tdx" not in by_key
    assert by_key["tdx_api"]["provider"] == "客户授权 TDX API v2.8"
    assert "免费" in by_key["eastmoney"]["role"]
    assert "备用" in by_key["eastmoney"]["role"]
    assert "财联社" in by_key["news"]["provider"]
    assert "多端命中不等于独立互证" in by_key["news"]["role"]
    assert by_key["cls_news"]["status"] == "idle"
    assert by_key["cls_news"]["provider"] == "财联社官网页面数据"
    assert "无 API SLA" in by_key["cls_news"]["role"]
    assert "非深证信正式 Web API" in by_key["cninfo_disclosure"]["role"]
    assert by_key["sse_disclosure"]["status"] == "idle"
    assert by_key["szse_disclosure"]["status"] == "idle"


def test_overseas_deployment_reports_missing_relay_as_an_explicit_gap(monkeypatch) -> None:
    monkeypatch.setattr(sources, "source_metrics", lambda: {})
    monkeypatch.setenv("HERMES_DEPLOYMENT_PROFILE", "overseas_server")
    monkeypatch.setenv("HERMES_DEPLOYMENT_PROFILE_CONFIGURED", "overseas_server")
    monkeypatch.delenv("DATA_RELAY_API", raising=False)
    monkeypatch.delenv("DATA_RELAY_PROXY", raising=False)

    report = sources.build_report({}, {
        "source": "last-good", "count": 5200, "complete": True, "stale": False,
    })

    assert report["deployment"]["china_public_route"] == "disabled"
    assert report["deployment"]["relay_configured"] is False
    assert "未配置境内中继" in report["deployment"]["public_note"]


def test_universe_row_uses_canonical_result_and_keeps_fallback_failure_as_diagnostic(monkeypatch) -> None:
    monkeypatch.setattr(sources, "source_metrics", lambda: {
        "股票代码表(TDX API)": {
            "last_ok": False, "last_latency_ms": 6800,
            "last_ts": "2026-07-13 09:35:07", "last_epoch": 20,
            "last_mode": "tdx_api", "last_error": "total wall timeout after 6.8s",
            "calls": 1, "fails": 1,
        },
    })
    canonical = {
        "source": "沪深京交易所官网公开列表", "count": 5231,
        "complete": True, "stale": False, "refresh_required": False,
        "fetched_at": "2026-07-13T09:35:01+08:00", "missing_exchanges": [],
    }

    report = sources.build_report({}, canonical)
    row = next(item for item in report["sources"] if item["key"] == "universe")

    assert row["status"] == "ok"
    assert row["channel"] == "沪深京交易所官网公开列表"
    assert row["diagnostics"] == [{
        "source": "股票代码表(TDX API)",
        "status": "down",
        "last_ts": "2026-07-13 09:35:07",
        "note": "total wall timeout after 6.8s",
    }]
    assert "未影响当前股票池" in row["note"]


def test_universe_row_maps_partial_missing_and_unobserved_states(monkeypatch) -> None:
    monkeypatch.setattr(sources, "source_metrics", lambda: {})

    partial = sources.build_report({}, {
        "source": "本地last-good", "count": 4904, "complete": False,
        "stale": True, "refresh_required": True, "missing_exchanges": ["bse"],
    })
    partial_row = next(item for item in partial["sources"] if item["key"] == "universe")
    assert partial_row["status"] == "degraded"
    assert "缺BSE" in partial_row["note"]

    idle = sources.build_report({}, {
        "source": "unavailable", "count": 0, "stale": True, "refresh_required": True,
    })
    idle_row = next(item for item in idle["sources"] if item["key"] == "universe")
    assert idle_row["status"] == "idle"

    unavailable = sources.build_report({}, {
        "status": "unavailable", "source": "unavailable", "count": 0,
    })
    unavailable_row = next(item for item in unavailable["sources"] if item["key"] == "universe")
    assert unavailable_row["status"] == "down"


class _Hermes:
    async def check_connection(self) -> bool:
        return True


def test_datasource_endpoint_never_returns_relay_credentials(monkeypatch) -> None:
    monkeypatch.setenv("DATA_RELAY_API", "https://relay.example/api?token=top-secret")
    monkeypatch.setattr(routes, "relay_proxy", lambda: "socks5://user:proxy-secret@relay.example:1080")
    monkeypatch.setattr(routes.market, "last_relay_meta", lambda: {})
    monkeypatch.setattr(routes.tdx_api, "service_health", lambda: {"configured": True, "connected": True})
    canonical = {
        "source": "沪深京交易所官网公开列表", "count": 5231,
        "complete": True, "stale": False, "refresh_required": False,
    }
    seen = {}

    def build_report(infra, stock_universe):
        seen["stock_universe"] = stock_universe
        return {"ok": True, "infra": infra, "sources": []}

    monkeypatch.setattr(routes.public_data, "stock_universe_status", lambda: canonical)
    monkeypatch.setattr(routes.sources, "build_report", build_report)

    config = SimpleNamespace(
        hermes_base_url="http://127.0.0.1:8742/v1",
        hermes_model="test-model",
        feishu_webhook="",
        feishu_app_id="",
        feishu_app_secret="",
    )

    class EvidenceStore:
        @staticmethod
        def list_sources():
            return [
                {"source_id": "tdx_quote"},
                {"source_id": "opentdx"},
                {"source_id": "tdx_api_quote"},
            ]

        @staticmethod
        def stats():
            return {"sources": 3}

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        config=config, hermes=_Hermes(), store=None,
        evidence_store=EvidenceStore(), market_gateway=None,
    )))

    response = asyncio.run(routes.datasources(request))
    encoded = json.dumps(response, ensure_ascii=False)

    assert response["infra"]["relay"] == {
        "enabled": True,
        "transport": "api",
        "api_configured": True,
        "proxy_configured": True,
        "spot": None,
    }
    assert "top-secret" not in encoded
    assert "proxy-secret" not in encoded
    assert "relay.example" not in encoded
    assert response["infra"]["tdx_api"] == {"configured": True, "connected": True}
    assert seen["stock_universe"] is canonical
    assert response["universe"] is canonical
    assert response["evidence_registry"] == [{"source_id": "tdx_api_quote"}]
    assert response["retired_evidence_sources"] == ["opentdx", "tdx_quote"]
    assert "TDXAPI_KEY" not in encoded
