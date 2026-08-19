from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_trader.api import routes
from hermes_trader.data import stock_search


NAMES = {
    "000001": "平安银行",
    "300750": "宁德时代",
    "600519": "贵州茅台",
    "600036": "招商银行",
    "002310": "*ST东园",
    "601318": "XD中国平安",
}


def _patch_catalog(monkeypatch, *, complete: bool = True, stale: bool = False) -> None:
    monkeypatch.setattr(stock_search.free_source, "cached_stock_universe", lambda: dict(NAMES))
    monkeypatch.setattr(stock_search.free_source, "stock_universe_status", lambda: {
        "schema": "a-share-universe-v3",
        "authority": "tdx_instruments_a",
        "source": "TDX 数据 API 股票列表",
        "as_of": "2026-08-19",
        "complete": complete,
        "stale": stale,
        "refresh_required": not complete or stale,
        "missing_exchanges": [],
    })


def test_stock_search_supports_code_chinese_full_pinyin_and_initials(monkeypatch) -> None:
    _patch_catalog(monkeypatch)
    assert stock_search._PINYIN_AVAILABLE is True

    cases = {
        "6005": "600519",
        "茅台": "600519",
        "maotai": "600519",
        "gzmt": "600519",
        "mt": "600519",
    }
    for query, expected in cases.items():
        result = stock_search.search_stock_catalog(query)
        assert result["state"] == "ready"
        assert result["results"][0] == {"code": expected, "name": NAMES[expected]}


def test_stock_search_ignores_temporary_name_prefixes_for_matching(monkeypatch) -> None:
    _patch_catalog(monkeypatch)

    st = stock_search.search_stock_catalog("dongyuan")
    xd = stock_search.search_stock_catalog("zhongguopingan")

    assert st["results"][0] == {"code": "002310", "name": "*ST东园"}
    assert xd["results"][0] == {"code": "601318", "name": "XD中国平安"}


def test_stock_search_prioritizes_omitted_prefix_initials(monkeypatch) -> None:
    names = {
        "600519": "贵州茅台",
        **{f"600{index:03d}": f"明天股份{index}" for index in range(10)},
    }
    monkeypatch.setattr(stock_search.free_source, "cached_stock_universe", lambda: names)
    monkeypatch.setattr(stock_search.free_source, "stock_universe_status", lambda: {
        "authority": "tdx_instruments_a",
        "source": "TDX 数据 API 股票列表",
        "as_of": "2026-08-19",
        "complete": True,
        "stale": False,
        "refresh_required": False,
        "missing_exchanges": [],
    })

    result = stock_search.search_stock_catalog("mt")

    assert {"code": "600519", "name": "贵州茅台"} in result["results"]


def test_stock_search_caps_results_and_preserves_degraded_catalog_truth(monkeypatch) -> None:
    names = {f"600{index:03d}": f"测试股份{index}" for index in range(20)}
    monkeypatch.setattr(stock_search.free_source, "cached_stock_universe", lambda: names)
    monkeypatch.setattr(stock_search.free_source, "stock_universe_status", lambda: {
        "source": "本地 last-good",
        "as_of": "2026-08-01",
        "complete": False,
        "stale": True,
        "refresh_required": True,
    })
    monkeypatch.setattr(stock_search.free_source, "stock_universe_is_authoritative", lambda _status: False)

    result = stock_search.search_stock_catalog("600", limit=99)

    assert len(result["results"]) == 8
    assert result["state"] == "degraded"
    assert result["universe"]["stale"] is True
    assert any("陈旧快照" in item for item in result["missing"])


def test_stock_search_api_uses_cache_only_search_contract(monkeypatch) -> None:
    seen = {}

    def fake_search(query: str, *, limit: int) -> dict:
        seen.update(query=query, limit=limit)
        return {"ok": True, "state": "ready", "results": [{"code": "600519", "name": "贵州茅台"}]}

    monkeypatch.setattr(routes.stock_search_mod, "search_stock_catalog", fake_search)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)

    response = client.get("/api/stocks/search", params={"q": "maotai", "limit": 100})

    assert response.status_code == 200
    assert response.json()["results"] == [{"code": "600519", "name": "贵州茅台"}]
    assert seen == {"query": "maotai", "limit": 8}
    assert client.get("/api/stocks/search", params={"q": "x" * 65}).status_code == 400
