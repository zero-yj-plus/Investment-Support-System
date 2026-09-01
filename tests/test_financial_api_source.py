from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_trader.api import routes
from hermes_trader.data import financial_api_source as source


class _Response:
    def __init__(self, payload: dict, *, status: int = 200) -> None:
        self.body = json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = {"Content-Length": str(len(self.body))}

    def read(self, limit: int = -1) -> bytes:
        return self.body if limit < 0 else self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class _Opener:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout: float):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


def _success(data: dict, request_id: str = "req-test") -> _Response:
    return _Response({"code": 0, "message": "ok", "request_id": request_id, "data": data})


def test_unconfigured_source_is_explicit_and_does_not_make_network_call(monkeypatch) -> None:
    monkeypatch.delenv("HITHINK_FINANCE_API_KEY", raising=False)
    monkeypatch.setattr(
        source._OPENER,
        "open",
        lambda *_args, **_kwargs: pytest.fail("未配置 API Key 时不得请求网络"),
    )

    result = source.valuation_snapshot.__wrapped__(("600519",))

    assert result["ok"] is False
    assert result["state"] == "unconfigured"
    assert result["data"] is None
    assert "HITHINK_FINANCE_API_KEY" in result["upstream_message"]


def test_request_uses_official_endpoint_and_never_returns_key(monkeypatch) -> None:
    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "secret-never-returned")
    opener = _Opener([_success({"timestamp": 123, "total": 0, "item": []})])
    monkeypatch.setattr(source, "_OPENER", opener)

    result = source.auction_snapshot.__wrapped__(("600519",), "final")

    assert result["ok"] is True
    request, timeout = opener.requests[0]
    assert request.full_url.startswith(
        "https://fuyao.aicubes.cn/api/a-share/auction/snapshot?"
    )
    assert request.get_header("X-api-key") == "secret-never-returned"
    assert timeout == source._TIMEOUT
    assert "secret-never-returned" not in json.dumps(result, ensure_ascii=False)


def test_business_not_ready_preserves_request_id_and_does_not_zero_fill(monkeypatch) -> None:
    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "test-key")
    opener = _Opener([
        _Response({"code": 3002, "message": "data not ready", "request_id": "req-3002", "data": None})
    ])
    monkeypatch.setattr(source, "_OPENER", opener)

    result = source.auction_snapshot.__wrapped__(("000001",), "live")

    assert result == {
        "ok": False,
        "state": "not_ready",
        "source": "同花顺金融数据服务",
        "fetched_at": result["fetched_at"],
        "request_id": "req-3002",
        "upstream_code": 3002,
        "upstream_message": "data not ready",
        "http_status": 200,
        "data": None,
        "missing_semantics": "null_or_missing_means_unavailable; never_zero_fill",
    }
    assert len(opener.requests) == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600519", "600519.SH"),
        ("000001", "000001.SZ"),
        ("920001", "920001.BJ"),
        ("600519.sh", "600519.SH"),
    ],
)
def test_a_share_code_normalization(raw: str, expected: str) -> None:
    assert source.to_a_share_thscode(raw) == expected


def test_auction_preserves_null_and_documents_units(monkeypatch) -> None:
    monkeypatch.setattr(
        source,
        "_request_data",
        lambda *_args, **_kwargs: {
            "ok": True,
            "state": "complete",
            "source": "同花顺金融数据服务",
            "fetched_at": "2026-08-27T01:25:00Z",
            "request_id": "auction-1",
            "upstream_code": 0,
            "data": {
                "timestamp": 1000,
                "auction_phase": "final",
                "data_status": "final",
                "total": 1,
                "item": [{"ticker": "600519", "auction_price": None, "auction_volume": 25}],
            },
            "missing_semantics": "upstream null means undisclosed or unavailable; never_zero_fill",
        },
    )

    result = source.auction_snapshot.__wrapped__(("600519",), "final")

    assert result["item"][0]["auction_price"] is None
    assert result["field_units"]["auction_volume"] == "lot"
    assert result["data_timestamp_ms"] == 1000
    assert result["timestamp_semantics"].startswith("response_assembly_time")


def test_sector_history_validates_window_before_network(monkeypatch) -> None:
    monkeypatch.setattr(
        source,
        "_request_data",
        lambda *_args, **_kwargs: pytest.fail("非法窗口不得请求网络"),
    )
    with pytest.raises(source.FinancialAPIInputError, match="不能超过 10 年"):
        source.sector_history.__wrapped__("886042.TI", 1, 11 * 366 * 24 * 3600 * 1000)


def test_financial_statements_use_three_fixed_calls_and_keep_partial_state(monkeypatch) -> None:
    calls: list[str] = []

    def statement(path: str, _params: dict, _label: str, _units: dict) -> dict:
        calls.append(path)
        return {"ok": not path.endswith("cash-flow-statements"), "item": []}

    monkeypatch.setattr(source, "_statement", statement)

    result = source.financial_statements.__wrapped__("600519", "annual", 4)

    assert set(calls) == {
        "/api/a-share/financials/income-statements",
        "/api/a-share/financials/balance-sheets",
        "/api/a-share/financials/cash-flow-statements",
    }
    assert result["state"] == "partial"
    assert result["missing"] == ["cash_flow"]
    assert result["query"] == {"period": "annual", "limit": 4}


def test_financial_range_mode_never_sends_limit(monkeypatch) -> None:
    seen: list[dict] = []

    def statement(_path: str, params: dict, _label: str, _units: dict) -> dict:
        seen.append(dict(params))
        return {"ok": True, "item": []}

    monkeypatch.setattr(source, "_statement", statement)
    source.financial_statements.__wrapped__("000001", "quarterly", 4, 1000, 2000)

    assert len(seen) == 3
    assert all("limit" not in params for params in seen)
    assert all(params["start"] == 1000 and params["end"] == 2000 for params in seen)


def test_valuation_keeps_negative_and_null_values(monkeypatch) -> None:
    monkeypatch.setattr(
        source,
        "_request_data",
        lambda _path, params, **_kwargs: {
            "ok": True,
            "state": "complete",
            "source": "同花顺金融数据服务",
            "fetched_at": "2026-08-27T01:25:00Z",
            "request_id": "valuation-1",
            "upstream_code": 0,
            "data": {
                "timestamp": 1000,
                "total": 1,
                "item": [{"ticker": "600519", "pe_ttm": -3.2, "pcf_ttm": None}],
                "requested": params["thscodes"],
            },
            "missing_semantics": "upstream null means undisclosed or unavailable; never_zero_fill",
        },
    )

    result = source.valuation_snapshot.__wrapped__(("600519",))

    assert result["item"][0]["pe_ttm"] == -3.2
    assert result["item"][0]["pcf_ttm"] is None
    assert result["requested"] == "600519.SH"


def test_indicator_report_format_is_strict() -> None:
    with pytest.raises(source.FinancialAPIInputError, match=r"YYYY-\[1-4\]"):
        source.financial_indicators.__wrapped__("600519", "2025-12-31")


def test_original_batch_limit_is_checked_before_deduplication() -> None:
    with pytest.raises(source.FinancialAPIInputError, match="1 至 100"):
        source.valuation_snapshot.__wrapped__(tuple("600519" for _ in range(101)))


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_market_auction_route_passes_original_batch_and_stage(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(
        routes.financial_api,
        "auction_snapshot",
        lambda codes, stage: seen.append((codes, stage)) or {"ok": True, "item": []},
    )

    response = _client().get("/api/market/auction?codes=600519,000001.SZ&stage=live")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen == [(('600519', '000001.SZ'), 'live')]


def test_financial_route_keeps_range_arguments_separate(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(
        routes.financial_api,
        "financial_statements",
        lambda *args: seen.append(args) or {"ok": True, "state": "complete"},
    )

    response = _client().get(
        "/api/stock/600519/financials?period=quarterly&limit=8&start_ms=1000&end_ms=2000"
    )

    assert response.status_code == 200
    assert seen == [("600519", "quarterly", 8, 1000, 2000)]


def test_new_routes_return_local_parameter_errors_as_http_400() -> None:
    response = _client().get("/api/market/sector-catalog?tag=unknown")

    assert response.status_code == 400
    assert response.json()["state"] == "invalid_request"
