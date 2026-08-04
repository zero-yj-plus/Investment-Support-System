from __future__ import annotations

from datetime import timedelta

from hermes_trader.data import news, news_mapping


def _install_sources(monkeypatch, *, catalog=None, relations=None, frame=None, official=None, tdx_names=None,
                     industry_members=None, concept_members=None) -> None:
    monkeypatch.setattr(news_mapping.tdx, "stock_universe", lambda: tdx_names or {"000001": "平安银行"})
    monkeypatch.setattr(news_mapping.free_source, "stock_universe", lambda: official or {"000001": "平安银行"})
    monkeypatch.setattr(
        news_mapping.free_source,
        "stock_universe_status",
        lambda: {"complete": True, "stale": False, "source": "交易所官网"},
    )
    monkeypatch.setattr(news_mapping.tdx, "sector_catalog", lambda: catalog or {
        "881157.SH": {"code": "881157.SH", "name": "全国性银行", "kind": "industry"},
    })
    monkeypatch.setattr(news_mapping.tdx, "relations_for_codes", lambda _codes: relations or {})
    monkeypatch.setattr(news_mapping.tdx, "sector_memberships", lambda: {
        "by_sector": industry_members or {}, "data_date": "2026-07-13",
    })
    monkeypatch.setattr(news_mapping.tdx, "sector_members_for_codes", lambda _codes: {
        "by_sector": concept_members or {}, "data_date": "2026-07-13",
    })
    monkeypatch.setattr(news_mapping.tdx, "pricevol_snapshot", lambda: frame)


def test_direct_stock_mapping_uses_official_and_tdx_then_waits_for_next_session(monkeypatch) -> None:
    _install_sources(
        monkeypatch,
        tdx_names={"605028": "世茂能源", "600900": "长江电力"},
        official={"605028": "世茂能源", "600900": "长江电力"},
        catalog={
            "881459.SH": {"code": "881459.SH", "name": "电力", "kind": "industry"},
            "880753.SH": {"code": "880753.SH", "name": "绿色电力", "kind": "concept"},
        },
        relations={
            "605028": {
                "industry": {"name": "电力", "code": "881459.SH"},
                "concepts": [
                    {"name": "绿色电力", "code": "880753.SH"},
                    {"name": "次新股", "code": "880529.SH"},
                ],
            }
        },
        frame={
            "data_date": "2026-07-13", "as_of": "2026-07-13 15:00:00", "count": 2,
            "stocks": {
                "605028": {"code": "605028", "change_pct": 4.2},
                "600900": {"code": "600900", "change_pct": 1.1},
            },
        },
        industry_members={"881459.SH": ("605028", "600900")},
        concept_members={"880753.SH": ("605028", "600900")},
    )

    result = news_mapping.map_news_items([{
        "title": "世茂能源：控股股东拟协议转让21.88%公司股份",
        "content": "世茂能源（605028.SH）公告",
        "time": "2026-07-13 22:58:43",
        "confirms": 1,
        "multi": False,
    }])

    mapped = result["items"][0]["a_share"]
    assert mapped["state"] == "mapped"
    assert mapped["level"] == "direct_stock"
    assert [(item["kind"], item["name"]) for item in mapped["matches"]] == [
        ("stock", "世茂能源"), ("industry", "电力"), ("concept", "绿色电力"),
    ]
    assert mapped["market_check"]["state"] == "pending_next_session"
    assert mapped["market_check"]["stocks"] == []
    assert "收盘后" in mapped["coverage"]["missing"][0]
    assert result["summary"]["mapped"] == 1


def test_direct_industry_mapping_only_reports_same_day_observation(monkeypatch) -> None:
    _install_sources(
        monkeypatch,
        catalog={
            "881319.SH": {"code": "881319.SH", "name": "半导体", "kind": "industry"},
        },
        frame={
            "data_date": "2026-07-13", "as_of": "2026-07-13 14:30:00", "count": 3,
            "stocks": {
                "000001": {"code": "000001", "change_pct": 2.0},
                "000002": {"code": "000002", "change_pct": 1.0},
                "000003": {"code": "000003", "change_pct": -1.0},
            },
        },
        industry_members={"881319.SH": ("000001", "000002", "000003")},
        tdx_names={"000001": "甲公司", "000002": "乙公司", "000003": "丙公司"},
        official={"000001": "甲公司", "000002": "乙公司", "000003": "丙公司"},
    )

    result = news_mapping.map_news_items([{
        "title": "半导体指数盘中走强",
        "content": "半导体指数盘中走强",
        "time": "2026-07-13 10:15:00",
    }])

    mapped = result["items"][0]["a_share"]
    assert mapped["state"] == "mapped"
    assert mapped["level"] == "direct_sector"
    assert mapped["hypothesis"]["direction"] == "unknown"
    assert mapped["market_check"]["state"] == "observation_only"
    assert mapped["market_check"]["label"] == "同日盘面观察（非因果）"
    sector = mapped["market_check"]["sectors"][0]
    assert sector["up_count"] == 2
    assert sector["down_count"] == 1
    assert sector["up_ratio_pct"] == 66.7
    assert sector["median_change_pct"] == 1.0
    assert mapped["market_check"]["counter_evidence"] == []


def test_exact_concept_is_candidate_and_short_generic_industry_does_not_false_match(monkeypatch) -> None:
    _install_sources(
        monkeypatch,
        catalog={
            "880655.SH": {"code": "880655.SH", "name": "钙钛矿电池", "kind": "concept"},
            "881155.SH": {"code": "881155.SH", "name": "银行", "kind": "industry"},
        },
        frame={"data_date": "2026-07-13", "as_of": "2026-07-13 15:00:00", "count": 1, "stocks": {}},
    )

    result = news_mapping.map_news_items([
        {"title": "钙钛矿电池研究取得新突破", "content": "研究取得新突破", "time": "2026-07-13 22:00:00"},
        {"title": "美国银行调查：基金经理看空日元", "content": "美国银行调查", "time": "2026-07-13 22:01:00"},
    ])

    first, second = [item["a_share"] for item in result["items"]]
    assert first["state"] == "candidate"
    assert first["level"] == "topic_candidate"
    assert first["topic_candidates"][0]["name"] == "钙钛矿电池"
    assert second["state"] == "none"
    assert result["summary"]["mapped"] == 0
    assert result["summary"]["candidates"] == 1


def test_market_style_label_is_not_an_a_share_topic_candidate(monkeypatch) -> None:
    _install_sources(
        monkeypatch,
        catalog={
            "880640.SH": {"code": "880640.SH", "name": "历史新高", "kind": "concept"},
        },
        frame={"data_date": "2026-07-13", "as_of": "2026-07-13 15:00:00", "count": 1, "stocks": {}},
    )

    result = news_mapping.map_news_items([{
        "title": "意大利银行板块创收盘历史新高",
        "content": "",
        "time": "2026-07-13 22:00:00",
    }])

    assert result["items"][0]["a_share"]["state"] == "none"
    assert result["items"][0]["a_share"]["topic_candidates"] == []


def test_stock_name_is_not_mapped_when_official_validator_lacks_the_code(monkeypatch) -> None:
    _install_sources(
        monkeypatch,
        tdx_names={"300024": "机器人", "000001": "平安银行"},
        official={"000001": "平安银行"},
        frame={"data_date": "2026-07-13", "as_of": "2026-07-13 14:00:00", "count": 1, "stocks": {}},
    )

    result = news_mapping.map_news_items([{
        "title": "机器人产业大会召开",
        "content": "机器人产业大会召开",
        "time": "2026-07-13 10:00:00",
    }])

    assert result["items"][0]["a_share"]["state"] == "none"
    assert not any(item.get("kind") == "stock" for item in result["items"][0]["a_share"]["matches"])


def test_plain_six_digit_number_is_not_treated_as_stock_code(monkeypatch) -> None:
    _install_sources(
        monkeypatch,
        tdx_names={"600000": "浦发银行"},
        official={"600000": "浦发银行"},
        frame={"data_date": "2026-07-13", "as_of": "20260713", "count": 1, "stocks": {}},
    )

    result = news_mapping.map_news_items([{
        "title": "工厂今年计划交付600000台设备",
        "content": "销量目标为600000台",
        "time": "2026-07-13 10:00:00",
    }])

    assert result["items"][0]["a_share"]["state"] == "none"


def test_suffixed_stock_code_remains_strong_evidence(monkeypatch) -> None:
    _install_sources(
        monkeypatch,
        tdx_names={"600000": "浦发银行"},
        official={"600000": "浦发银行"},
        frame={"data_date": "2026-07-13", "as_of": "20260713", "count": 1, "stocks": {}},
    )

    result = news_mapping.map_news_items([{
        "title": "600000.SH发布公告",
        "content": "证券代码600000",
        "time": "2026-07-13 10:00:00",
    }])

    stock = result["items"][0]["a_share"]["matches"][0]
    assert stock["code"] == "600000"
    assert stock["name_consistency"] == "matched"


def test_a_share_short_name_inside_overseas_subsidiary_is_not_bound(monkeypatch) -> None:
    _install_sources(
        monkeypatch,
        tdx_names={"600999": "招商证券", "000001": "平安银行"},
        official={"600999": "招商证券", "000001": "平安银行"},
        frame={"data_date": "2026-07-13", "as_of": "2026-07-13 15:00:00", "count": 1, "stocks": {}},
    )

    result = news_mapping.map_news_items([{
        "title": "港股IPO公司递表港交所",
        "content": "独家保荐人为招商证券国际。",
        "time": "2026-07-13 22:57:39",
    }])

    assert result["items"][0]["a_share"]["state"] == "none"


def test_a_share_news_endpoint_contract_keeps_feed_and_mapping_metadata(monkeypatch) -> None:
    observations = [{
        "source_id": "tdx_news_sentiment", "publisher_name": None, "collection_platform_name": "TDX 舆情电讯",
        "published_at": "2026-07-13T22:58:43+08:00", "fetched_at": "2026-07-13T22:58:50+08:00",
        "evidence_level": "tier_1", "content_hash": "a" * 64,
    }]
    monkeypatch.setattr(news, "cn_news_aggregated", lambda limit=40: {
        "ts": "2026-07-13 22:58:50",
        "fetched_at": "2026-07-13T22:58:50+08:00",
        "latest_event_time": "2026-07-13 22:58:43",
        "sources_tried": 5,
        "sources_live": 4,
        "missing": [],
        "items": [{
            "title": "世茂能源公告", "content": "正文", "time": "2026-07-13 22:58:43",
            "published_at": "2026-07-13T22:58:43+08:00", "fetched_at": "2026-07-13T22:58:50+08:00",
            "content_hash": "b" * 64, "evidence_level": "tier_1", "observations": observations,
            "corroboration": {"collection_endpoint_count": 1, "independent_origin_count": 0, "origin_unknown": True},
        }],
    })
    monkeypatch.setattr(news_mapping, "map_news_items", lambda items: {
        "items": [{**items[0], "event_id": "event-1", "a_share": {"state": "mapped"}}],
        "summary": {"state": "complete", "mapped": 1, "candidates": 0, "total": 1},
    })

    monkeypatch.setattr(news, "_A_SHARE_LAST_GOOD_PATH", None)
    payload = news._refresh_a_share_news.__wrapped__(40)

    assert payload["sources_live"] == 4
    assert payload["fetched_at"] == "2026-07-13T22:58:50+08:00"
    assert payload["latest_event_time"] == "2026-07-13 22:58:43"
    assert payload["items"][0]["event_id"] == "event-1"
    assert payload["items"][0]["observations"] == observations
    assert payload["items"][0]["corroboration"]["origin_unknown"] is True
    assert payload["mapping"]["mapped"] == 1
    assert payload["pipeline"]["source_state"] == "degraded"
    assert payload["pipeline"]["entity_mapping_state"] == "complete"
    assert payload["pipeline"]["validation_state"] in {"disabled", "waiting", "mixed", "empty"}
    assert payload["snapshot"]["status"] == "live"
    assert payload["snapshot"]["served_from_snapshot"] is False


def test_a_share_news_serves_full_snapshot_and_refreshes_in_background(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(news, "_A_SHARE_LAST_GOOD_PATH", str(tmp_path / "a-share-page-last-good.json"))
    saved_at = (news.now_cn() - timedelta(seconds=90)).isoformat(timespec="seconds")
    payload = {
        "ok": True,
        "fetched_at": "2026-07-13T22:58:50+08:00",
        "items": [{"event_id": "event-1", "title": "真实事件"}],
        "pipeline": {"source_state": "complete"},
    }
    assert news._save_a_share_last_good(payload, saved_at=saved_at) == saved_at
    started: list[int] = []
    monkeypatch.setattr(news, "_start_a_share_refresh", lambda limit: started.append(limit) or True)

    result = news.a_share_news(40)

    assert result["items"] == payload["items"]
    assert result["snapshot"]["served_from_snapshot"] is True
    assert result["snapshot"]["status"] == "stale"
    assert result["snapshot"]["saved_at"] == saved_at
    assert result["snapshot"]["age_seconds"] >= 90
    assert result["snapshot"]["refreshing"] is True
    assert started == [40]


def test_a_share_news_snapshot_never_replaces_real_items_with_empty_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(news, "_A_SHARE_LAST_GOOD_PATH", str(tmp_path / "a-share-page-last-good.json"))
    good = {"ok": True, "items": [{"event_id": "event-1", "title": "真实事件"}]}
    assert news._save_a_share_last_good(good)
    assert news._save_a_share_last_good({"ok": True, "items": []}) is None

    loaded = news._load_a_share_last_good()

    assert loaded is not None
    assert loaded[0]["items"] == good["items"]
