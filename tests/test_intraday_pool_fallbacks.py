from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from hermes_trader.analysis import features, mainline_runtime


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
