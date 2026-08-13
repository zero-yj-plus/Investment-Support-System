from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse

from hermes_trader.api import routes
from hermes_trader.data import etf
from hermes_trader.knowledge.store import Store


class Request:
    def __init__(self, store: Store, body: dict | None = None):
        self.app = SimpleNamespace(state=SimpleNamespace(store=store))
        self._body = body or {}

    async def json(self):
        return self._body


def response_body(response):
    return json.loads(response.body) if isinstance(response, JSONResponse) else response


def test_etf_store_is_isolated_from_stock_watchlist_and_zones(tmp_path: Path) -> None:
    store = Store(tmp_path)
    first_id = store.add_etf_watch({"code": "510300", "name": "沪深300ETF", "group": "宽基"})
    result = store.import_etf_watch([
        {"code": "510300", "name": "重复"},
        {"code": "513100", "name": "纳指ETF"},
    ])
    store.upsert_etf_watch_snapshot("510300", {"ret20_pct": 2.1, "missing": []})

    assert result == {"added_codes": ["513100"], "duplicate_codes": ["510300"]}
    assert [row["code"] for row in store.etf_watchlist()] == ["510300", "513100"]
    assert store.watchlist() == []
    assert store.watchlist_zones() == {}
    assert store.etf_watch_snapshots()["510300"]["ret20_pct"] == 2.1

    assert store.update_etf_watch(first_id, {"note": "核心观察"}) is True
    assert store.etf_watchlist()[0]["note"] == "核心观察"
    assert store.delete_etf_watch(first_id) is True
    assert store.delete_etf_watch(first_id) is False


def test_etf_store_rejects_non_exchange_fund_codes(tmp_path: Path) -> None:
    store = Store(tmp_path)
    for code in ("600000", "00700", "ABCDEF"):
        with pytest.raises(ValueError, match="ETF代码"):
            store.add_etf_watch({"code": code})


@pytest.mark.asyncio
async def test_etf_watch_get_uses_one_batch_quote_and_reads_saved_profile(monkeypatch, tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.add_etf_watch({"code": "510300", "name": "录入名称"})
    store.add_etf_watch({"code": "513100", "name": ""})
    store.upsert_etf_watch_snapshot("510300", {
        "official_name": "沪深300ETF",
        "ret20_pct": 1.2,
        "history_date": "2026-08-12",
        "missing": [],
    })
    calls: list[list[str]] = []

    def quotes(codes):
        calls.append(list(codes))
        return {
            "510300": {
                "name": "沪深300ETF", "price": 4.8, "prev_close": 4.7,
                "open": 4.71, "high": 4.82, "low": 4.69, "change_pct": 2.13,
                "volume": 10_000, "amount": 48_000, "turnover_rate": 0.2,
                "volume_ratio": 1.1, "total_mv": 100_000_000,
                "source": "TDX API", "as_of": "2026-08-13T10:00:00+08:00", "stale": False,
            }
        }

    monkeypatch.setattr(routes.stock, "quick_quotes", quotes)
    monkeypatch.setattr(
        routes.etf_data, "refresh_etf",
        lambda _code: pytest.fail("GET /etf-watch 不得逐只抓低频公开源"),
    )

    payload = await routes.get_etf_watch(Request(store))

    assert calls == [["510300", "513100"]]
    assert payload["dual_cycle_zones"] is False
    assert payload["low_frequency_refresh"] == "explicit_background"
    assert payload["items"][0]["quote"]["amplitude_pct"] == pytest.approx(2.77)
    assert payload["items"][0]["profile"]["ret20_pct"] == 1.2
    assert payload["items"][0]["state"] == "complete"
    assert payload["items"][1]["quote"] is None
    assert payload["items"][1]["state"] == "missing"
    assert "行情" in payload["items"][1]["missing"]


@pytest.mark.asyncio
async def test_etf_import_requires_quote_and_etf_catalog_verification(monkeypatch, tmp_path: Path) -> None:
    store = Store(tmp_path)
    queued: list[str | None] = []

    monkeypatch.setattr(routes.stock, "quick_quotes", lambda _codes: {
        "510300": {"name": "300ETF", "price": 4.8},
        "501018": {"name": "南方原油LOF", "price": 1.2},
    })
    monkeypatch.setattr(routes.etf_data, "verified_etf_codes", lambda codes, _quotes: {"510300"} & set(codes))
    monkeypatch.setattr(routes, "_queue_etf_watch_refresh", lambda _store, code=None: queued.append(code))

    response = await routes.import_etf_watch(Request(store, {"codes": ["510300", "501018", "600000"]}))
    payload = response_body(response)

    assert payload["ok"] is True
    assert payload["added_codes"] == ["510300"]
    assert payload["rejected_codes"] == ["501018"]
    assert [row["code"] for row in store.etf_watchlist()] == ["510300"]
    assert queued == [None]


def test_etf_history_metrics_use_real_rows_and_require_61_closes(monkeypatch) -> None:
    start = date(2026, 4, 1)
    rows = [{"d": (start + timedelta(days=index)).isoformat(), "c": 1 + index * 0.01} for index in range(61)]
    monkeypatch.setattr(etf, "_history_rows", lambda _code: (rows, "腾讯财经·前复权日K"))

    metrics, missing = etf._history_metrics("510300")

    assert missing == []
    assert metrics["ret5_pct"] == pytest.approx(3.23)
    assert metrics["ret20_pct"] == pytest.approx(14.29)
    assert metrics["ret60_pct"] == pytest.approx(60.0)
    assert metrics["history_date"] == rows[-1]["d"]
    assert metrics["history_adjustment"] == "qfq"

    monkeypatch.setattr(etf, "_history_rows", lambda _code: (rows[:60], "腾讯财经·前复权日K"))
    metrics, missing = etf._history_metrics("510300")
    assert metrics == {}
    assert missing == ["前复权日线不足61根"]


def test_etf_trade_rule_stays_missing_when_source_semantics_are_insufficient() -> None:
    assert etf._trade_rule("513100", "QDII-股票型") == "T+0"
    assert etf._trade_rule("159915", "指数型-股票") == "T+1"
    assert etf._trade_rule("159999", None) is None


def test_etf_asset_scale_parser_preserves_units() -> None:
    assert etf._billions_and_date("948.72亿元（2026年6月30日）") == (948.72, "2026-06-30")
    assert etf._billions_and_date("12,000万元（2026年6月30日）") == (1.2, "2026-06-30")
    assert etf._billions_and_date("120,000,000元（2026年6月30日）") == (1.2, "2026-06-30")


def test_etf_refresh_marks_all_missing_sources_as_missing(monkeypatch) -> None:
    monkeypatch.setattr(etf, "_overview", lambda _code: ({}, ["基金档案"]))
    monkeypatch.setattr(etf, "_nav_snapshot", lambda _code: ({}, ["净值申赎"]))
    monkeypatch.setattr(etf, "_history_metrics", lambda _code: ({}, ["前复权日线不足61根"]))

    payload = etf.refresh_etf("510300")

    assert payload["state"] == "missing"
    assert payload["trade_rule"] is None
