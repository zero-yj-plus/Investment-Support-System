from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hermes_trader.data import news


SENTI_VIEW = Path(__file__).parents[1] / "frontend" / "js" / "views" / "senti.js"
UI_VIEW = Path(__file__).parents[1] / "frontend" / "js" / "ui.js"


def test_domestic_news_refresh_contract_is_thirty_seconds() -> None:
    assert news.NEWS_FEED_REFRESH_SECONDS == 30


def test_aggregated_news_keeps_tdx_feed_when_akshare_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(news, "now_cn", lambda: datetime(2026, 7, 13, 10, 20, 30, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(news, "get_ak", lambda: None)
    monkeypatch.setattr(
        news.cls_news,
        "cached_cls_telegraph",
        lambda *_args, **_kwargs: {"ok": False, "state": "missing", "items": []},
    )
    monkeypatch.setattr(
        news.tdx_api,
        "market_news",
        lambda limit=80: {
            "ok": True,
            "items": [{
                "title": "中药板块走强",
                "content": "龙头甲涨停",
                "time": "2026-07-13 10:20:00",
            }],
            "missing": [],
        },
    )

    payload = news.cn_news_aggregated.__wrapped__(40)

    assert payload["sources_tried"] == 2
    assert payload["sources_live"] == 1
    assert payload["missing"] == []
    item = payload["items"][0]
    assert item["title"] == "中药板块走强"
    assert item["content"] == "龙头甲涨停"
    assert item["time"] == "2026-07-13 10:20:00"
    assert item["confirms"] == 1
    assert item["multi"] is False
    assert item["published_at"] == "2026-07-13T10:20:00+08:00"
    assert item["fetched_at"] == "2026-07-13T10:20:30+08:00"
    assert item["evidence_level"] == "tier_1"
    assert len(item["content_hash"]) == 64
    assert item["corroboration"] == {
        "collection_endpoint_count": 1,
        "independent_origin_count": 0,
        "independent_origin_confirmed": False,
        "origin_unknown": True,
    }
    assert item["observations"][0]["source_id"] == "tdx_news_sentiment"
    assert item["observations"][0]["publisher_name"] is None
    assert item["observations"][0]["collection_platform_name"] == "TDX 舆情电讯"
    assert item["observations"][0]["source_url"] is None
    assert "original_url_unavailable" in item["observations"][0]["degraded_reasons"]


def test_aggregated_news_replays_nonempty_last_good_on_transient_total_failure(
    monkeypatch, tmp_path,
) -> None:
    current = datetime(2026, 7, 13, 10, 20, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(news, "_CN_LAST_GOOD_PATH", str(tmp_path / "news" / "cn-last-good.json"))
    monkeypatch.setattr(news, "now_cn", lambda: current)
    monkeypatch.setattr(news, "get_ak", lambda: None)
    monkeypatch.setattr(
        news,
        "_parallel",
        lambda _thunks: [[news._raw_news_item(
            "tdx", "中药板块走强", "龙头甲涨停", "2026-07-13 10:20:00",
            fetched_at="2026-07-13T10:20:30+08:00",
        )], []],
    )
    first = news.cn_news_aggregated.__wrapped__(40)
    assert first["state"] == "live" and first["items"]

    current = datetime(2026, 7, 13, 10, 21, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(news, "_parallel", lambda _thunks: [[], []])
    replay = news.cn_news_aggregated.__wrapped__(40)

    assert replay["state"] == "last_good"
    assert replay["used_last_good"] is True
    assert replay["items"][0]["title"] == "中药板块走强"
    assert replay["sources_live"] == 0
    assert any("沿用" in item for item in replay["missing"])


def test_aggregated_news_merges_near_duplicate_reports_and_counts_live_sources(monkeypatch) -> None:
    fetched_at = "2026-07-13T22:28:00+08:00"
    monkeypatch.setattr(news, "now_cn", lambda: datetime(2026, 7, 13, 22, 28, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(news, "get_ak", lambda: object())
    monkeypatch.setattr(
        news,
        "_parallel",
        lambda thunks: [
            [news._raw_news_item("tdx", "特朗普称美国将恢复针对伊朗的封锁", "", "2026-07-13 22:27:30", fetched_at=fetched_at)],
            [],
            [news._raw_news_item("em", "特朗普称恢复对伊朗的海上封锁", "", "2026-07-13 22:25:10", fetched_at=fetched_at)],
            [],
            [],
            [],
        ],
    )

    payload = news.cn_news_aggregated.__wrapped__(40)

    assert payload["sources_tried"] == 6
    assert payload["sources_live"] == 2
    assert len(payload["items"]) == 1
    assert payload["items"][0]["confirms"] == 2
    assert payload["items"][0]["multi"] is True
    assert payload["items"][0]["corroboration"]["collection_endpoint_count"] == 2
    assert payload["items"][0]["corroboration"]["independent_origin_count"] == 0
    assert payload["items"][0]["corroboration"]["origin_unknown"] is True
    assert {row["source_id"] for row in payload["items"][0]["observations"]} == {
        "tdx_news_sentiment", "eastmoney_public_news",
    }


def test_direct_cls_observation_identifies_one_known_origin_without_claiming_corroboration(monkeypatch) -> None:
    fetched_at = "2026-07-13T22:28:00+08:00"
    monkeypatch.setattr(news, "now_cn", lambda: datetime(2026, 7, 13, 22, 28, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(news, "get_ak", lambda: None)
    monkeypatch.setattr(
        news,
        "_parallel",
        lambda _thunks: [[], [news._raw_news_item(
            "cls", "原油期货上涨", "WTI原油期货持续上涨",
            "2026-07-13T22:27:30+08:00", fetched_at=fetched_at,
            source_url="https://api3.cls.cn/share/article/123",
        )]],
    )

    item = news.cn_news_aggregated.__wrapped__(40)["items"][0]

    assert item["observations"][0]["publisher_name"] == "财联社"
    assert item["observations"][0]["source_relation"] == "original_publisher"
    assert item["corroboration"] == {
        "collection_endpoint_count": 1,
        "independent_origin_count": 1,
        "independent_origin_confirmed": False,
        "origin_unknown": False,
    }


def test_news_observation_marks_time_only_source_date_as_inferred(monkeypatch) -> None:
    monkeypatch.setattr(news, "now_cn", lambda: datetime(2026, 7, 13, 23, 59, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    raw = news._raw_news_item(
        "sina", "时间口径测试", "正文", "23:58:30",
        fetched_at="2026-07-13T23:59:00+08:00",
    )

    observation = news._news_observation(raw)

    assert observation["published_at"] == "2026-07-13T23:58:30+08:00"
    assert "published_date_inferred" in observation["degraded_reasons"]


def test_multi_market_news_exposes_collection_and_event_times(monkeypatch) -> None:
    monkeypatch.setattr(
        news,
        "cn_news_aggregated",
        lambda limit=40: {
            "ok": True,
            "ts": "2026-07-13 11:20:30",
            "latest_event_time": "2026-07-13 11:20:05",
            "refresh_seconds": 30,
            "sources_tried": 5,
            "sources_live": 3,
            "missing": [],
            "items": [{"title": "测试事件", "content": "测试内容", "time": "2026-07-13 11:20:05"}],
        },
    )
    monkeypatch.setattr(
        news,
        "_parallel",
        lambda thunks: [
            {"indexes": [], "tech_stocks": []},
            [],
            {"ok": False, "status": "not_routed", "items": []},
        ],
    )

    payload = news.multi_market_news.__wrapped__()

    assert payload["refresh_seconds"] == 30
    assert payload["cn_fetched_at"] == "2026-07-13 11:20:30"
    assert payload["latest_event_time"] == "2026-07-13 11:20:05"
    assert payload["cn_missing"] == []
    assert payload["cn_sources_tried"] == 5
    assert payload["cn_sources_live"] == 3
    assert payload["cn"][0]["title"] == "测试事件"


def test_news_page_refreshes_after_hours_and_exposes_truthful_a_share_mapping() -> None:
    source = SENTI_VIEW.read_text(encoding="utf-8")

    assert "setInterval(() => load(true), 30000)" in source
    assert "isTradingTime" not in source
    assert "api.get('/news/a-share')" in source
    assert "事件看板 · EVENT BOARD" in source
    assert "A股相关" in source
    assert "公司点名" in source
    assert "行业/主题线索" in source
    assert "最近抓取" in source
    assert "采集端命中" in source
    assert "独立信源待确认" in source
    assert "多源确认" not in source
    assert "可信度更高" not in source
    assert "有效源" not in source
    assert "state_label: lastTs ? '最近抓取'" in source
    assert "renderNewsSourceDetail(n)" in source
    assert "rel=\"noopener noreferrer\"" in source
    assert "先读事件，再看关联 A 股，最后看盘面是否已留痕" in source
    assert "不代表新闻造成涨跌" in source
    assert "来源证据放在事件详情里" in source
    assert "待盘面核对" in source
    assert "items.filter((n) => ['waiting', 'observing'].includes(n?.a_share?.observation?.status)).length" in source
    assert "已留痕" in source
    assert "暂无明确 A 股关联" in source
    assert "刷新未成功，当前沿用上次成功数据" in source
    assert "delay_text: '沿用'" in source
    assert "CLUE_RULES" not in source
    assert ".senti-grid{display:flex;flex-direction:column" in source
    assert "发生了什么" in source
    assert "关联 A 股" in source
    assert "盘面状态" in source
    assert "grid-auto-rows" not in source
    assert 'role="button" tabindex="0"' in source
    assert "e.key !== 'Enter' && e.key !== ' '" in source
    assert 'aria-pressed="${viewFilter === key ? \'true\' : \'false\'}"' in source


def test_shared_modal_manages_keyboard_focus_and_labels() -> None:
    source = UI_VIEW.read_text(encoding="utf-8")

    assert 'aria-labelledby="${titleId}"' in source
    assert "overlay._returnFocus = previousFocus" in source
    assert "if (e.key !== 'Tab') return" in source
    assert "(initial || modal).focus()" in source
    assert "returnFocus.focus()" in source
