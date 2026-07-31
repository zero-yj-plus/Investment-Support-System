from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from hermes_trader.analysis import features, mainline_runtime
from hermes_trader.data import market


def _run_market_overview_with_limit_down(
    monkeypatch,
    *,
    limit_down_rows,
    scan,
) -> dict:
    now = datetime(2026, 7, 31, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(market, "now_cn", lambda: now)
    monkeypatch.setattr(market, "_resolve_data_day", lambda _date: ("20260731", "2026-07-31", ""))
    monkeypatch.setattr(market, "get_ak", lambda: None)
    monkeypatch.setattr(market, "_relay_api", lambda: None)
    monkeypatch.setattr(
        market.tdxapi,
        "index_quotes",
        lambda symbols: {
            symbol: {"price": 100.0, "change_pct": 1.0, "amount": 10_000_000.0}
            for symbol in symbols
        },
    )
    monkeypatch.setattr(
        market.tdxapi,
        "pricevol_breadth",
        lambda: {"up_count": 3000, "down_count": 2000, "flat_count": 100, "count": 5100},
    )
    monkeypatch.setattr(
        market,
        "_limit_list_today_or_prev",
        lambda _date, kind: (
            ([{"code": "600001", "name": "涨停样本", "lianban": 1, "amount": 1_000_000.0}], None)
            if kind == "U"
            else (None, None)
        ),
    )
    monkeypatch.setattr(market, "_promote_rate", lambda *_args: None)
    monkeypatch.setattr(
        market.tx,
        "limit_list",
        lambda _date, kind="U": limit_down_rows if kind == "D" else None,
    )
    monkeypatch.setattr(market.tc, "market_zt_scan", scan)

    return market.market_overview.__wrapped__("20260731")


def test_market_overview_preserves_confirmed_empty_limit_down_pool_as_zero(monkeypatch) -> None:
    def must_not_scan():
        raise AssertionError("真实空跌停池不应触发腾讯兜底扫描")

    result = _run_market_overview_with_limit_down(
        monkeypatch,
        limit_down_rows=[],
        scan=must_not_scan,
    )

    assert result["limit_down"] == 0
    assert "跌停池" not in result["missing"]


def test_market_overview_uses_tencent_scan_when_limit_down_sources_fail(monkeypatch) -> None:
    result = _run_market_overview_with_limit_down(
        monkeypatch,
        limit_down_rows=None,
        scan=lambda: {
            "asof": "11:00",
            "judged": 5000,
            "limit_up": 20,
            "zhaban": 5,
            "limit_down": 3,
            "zhaban_rate": 20.0,
            "lu_list": [],
            "ld_list": [],
        },
    )

    assert result["limit_down"] == 3
    assert result["zhaban_count"] == 5
    assert result["zhaban_rate"] == 20.0
    assert any("跌停家数来自腾讯全市场同一时点扫描(11:00" in note for note in result["missing"])


def test_mainline_runtime_uses_same_moment_tencent_limit_down_rows(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(mainline_runtime, "now_cn", lambda: datetime(2026, 7, 14, 12, 10, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(mainline_runtime, "_dated_sector_payload", lambda: {"data_date": "2026-07-14", "sectors": []})
    monkeypatch.setattr(mainline_runtime.market, "market_limitup", lambda: {"data_date": "2026-07-14", "stocks": []})
    monkeypatch.setattr(mainline_runtime, "_industry_payload", lambda: {})
    monkeypatch.setattr(mainline_runtime.market, "market_overview", lambda: {})
    monkeypatch.setattr(mainline_runtime.news, "cn_news_aggregated", lambda _n: {})
    monkeypatch.setattr(mainline_runtime.public_data, "limit_list", lambda *_args: None)
    monkeypatch.setattr(
        mainline_runtime.tencent_data,
        "market_zt_scan",
        lambda: {
            "asof": "12:10",
            "ld_list": [{"code": "600001", "name": "跌停样本", "change_pct": -10.0}],
        },
    )

    def fake_build(*_args, **kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(mainline_runtime.mainline_mod, "build_mainline_response", fake_build)
    asyncio.run(mainline_runtime.build_current_response(5))

    assert captured["limit_down_rows"] == [{"code": "600001", "name": "跌停样本", "change_pct": -10.0}]
    assert any("腾讯全市场同一时点扫描(12:10)" in item for item in captured["limit_down_missing"])


def test_leaders_keeps_exact_sealed_names_when_board_height_is_pending(monkeypatch) -> None:
    monkeypatch.setattr(
        features.market,
        "market_limitup",
        lambda: {
            "data_date": "2026-07-14",
            "missing": ["连板层级待昨日涨停池补齐"],
            "stocks": [
                {"code": "600001", "name": "封板甲", "lianban": None, "amount": 300_000_000},
                {"code": "000002", "name": "封板乙", "lianban": None, "amount": 200_000_000},
            ],
        },
    )

    result = features.sentiment_leaders()

    assert result["kongban"] is None
    assert result["ladder"][0]["label"] == "封板池 · 板位待补"
    assert [row["name"] for row in result["ladder"][0]["stocks"]] == ["封板甲", "封板乙"]
    assert result["popularity"] == []
