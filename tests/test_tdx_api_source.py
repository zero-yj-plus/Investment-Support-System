from __future__ import annotations

import asyncio

import pytest

from hermes_trader.api import routes
from hermes_trader.data import tdx_api_source as tdx


def test_code_normalization_handles_a_share_markets_and_indexes() -> None:
    assert tdx._suffix_code("000001") == "000001.SZ"
    assert tdx._suffix_code("sh000001") == "000001.SH"
    assert tdx._suffix_code("600030.SH") == "600030.SH"
    assert tdx._suffix_code("430047") == "430047.BJ"
    assert tdx._suffix_code("920001") == "920001.BJ"
    assert tdx._suffix_code("bad") is None


def test_realtime_quote_v21_units_and_dynamic_depth_are_normalized() -> None:
    quote = tdx._normalize_quote(
        {
            "code": "600030.SH",
            "now": 29.47,
            "last_close": 29.30,
            "open": 29.35,
            "high": 29.60,
            "low": 29.21,
            "volume": 582_314,
            "amount": 171_324.5,
            "average": 29.41,
            "speed": 0.12,
            "inside": 200_000,
            "outside": 382_314,
            "buyp": [29.46, 29.45, 0, 0, 0],
            "buyv": [12, 8, 0, 0, 0],
            "sellp": [29.47],
            "sellv": [9],
            "age_seconds": 1.8,
        },
        {"600030": "中信证券"},
    )

    assert quote is not None
    assert quote["name"] == "中信证券"
    assert quote["volume"] == 58_231_400  # 实时 volume=手→股
    assert quote["amount"] == pytest.approx(1_713_245_000)  # 万元→元
    assert quote["inside"] == 20_000_000
    assert quote["outside"] == 38_231_400
    assert quote["average"] == 29.41
    assert quote["speed"] == 0.12
    assert quote["buyp"] == [29.46, 29.45]
    assert quote["buyv"] == [1200, 800]
    assert quote["sellp"] == [29.47]
    assert quote["sellv"] == [900]


def test_instruments_a_is_paginated_and_requires_a_complete_universe(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "configured", lambda: True)
    calls: list[str | None] = []

    def fake_request(_path, params, **_kwargs):
        cursor = params.get("cursor")
        calls.append(cursor)
        start, stop = (0, 5000) if cursor is None else (5000, 5535)
        return ({
            "rows": [
                {
                    "entity_key": f"{index:06d}.SZ",
                    "payload": {"Name": f"股票{index}"},
                }
                for index in range(start, stop)
            ],
            "has_more": cursor is None,
            "next_cursor": "page-2" if cursor is None else None,
        }, 200)

    monkeypatch.setattr(tdx, "_request_json_result", fake_request)
    names = tdx.stock_universe.__wrapped__()

    assert names is not None and len(names) == 5535
    assert names["000001"] == "股票1"
    assert calls == [None, "page-2"]


def test_cursor_pagination_restarts_whole_query_after_409(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "configured", lambda: True)
    calls: list[str | None] = []
    generation = {"value": 1}

    def fake_request(_path, params, **_kwargs):
        cursor = params.get("cursor")
        calls.append(cursor)
        if cursor == "old-page-2":
            generation["value"] = 2
            return None, 409
        if cursor == "new-page-2":
            return {"rows": [{"entity_key": "new-2"}], "has_more": False}, 200
        if generation["value"] == 1:
            return {"rows": [{"entity_key": "old-1"}], "has_more": True, "next_cursor": "old-page-2"}, 200
        return {"rows": [{"entity_key": "new-1"}], "has_more": True, "next_cursor": "new-page-2"}, 200

    monkeypatch.setattr(tdx, "_request_json_result", fake_request)
    rows = tdx._dataset_rows("demo", label="demo", max_rows=10)

    assert [row["entity_key"] for row in rows or []] == ["new-1", "new-2"]
    assert calls == [None, "old-page-2", None, "new-page-2"]


def test_cursor_pagination_rejects_repeated_cursor(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "configured", lambda: True)

    monkeypatch.setattr(
        tdx,
        "_request_json_result",
        lambda *_args, **_kwargs: ({
            "rows": [{"entity_key": "000001.SZ"}],
            "has_more": True,
            "next_cursor": "same-page",
        }, 200),
    )

    assert tdx._dataset_rows("demo", label="demo", max_rows=10) is None


def test_cursor_pagination_rejects_terminal_total_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "configured", lambda: True)

    monkeypatch.setattr(
        tdx,
        "_request_json_result",
        lambda *_args, **_kwargs: ({
            "rows": [{"entity_key": "000001.SZ"}],
            "count": 1,
            "total": 2,
            "has_more": False,
        }, 200),
    )

    assert tdx._dataset_rows("demo", label="demo", max_rows=10) is None


def test_instruments_a_rejects_duplicate_codes_and_wrong_market_suffix(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "configured", lambda: True)
    rows = [
        {
            "entity_key": f"{index:06d}.SZ",
            "payload": {"Name": f"股票{index}"},
        }
        for index in range(3000)
    ]
    rows.append({"entity_key": "000001.SZ", "payload": {"Name": "重复股票"}})
    monkeypatch.setattr(tdx, "_dataset_rows", lambda *_args, **_kwargs: rows)

    assert tdx.stock_universe.__wrapped__() is None

    rows[-1] = {"entity_key": "688563.SZ", "payload": {"Name": "错误市场"}}
    names = tdx.stock_universe.__wrapped__()
    assert names is not None
    assert "688563" not in names


def test_quotes_batch_does_not_block_on_instruments_and_rejects_pending(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "configured", lambda: True)
    monkeypatch.setattr(
        tdx,
        "stock_universe",
        lambda: (_ for _ in ()).throw(AssertionError("实时报价不得同步拉全 A 名录")),
    )
    monkeypatch.setattr(
        tdx,
        "_request_json",
        lambda *_args, **_kwargs: {
            "quotes": [
                {"code": "000001.SZ", "now": 10.5, "last_close": 10, "volume": 100, "amount": 20},
                {"code": "600030.SH", "pending": True},
            ]
        },
    )

    rows = tdx._quotes_cached.__wrapped__(("000001.SZ", "600030.SH"), max_age=15, wait=2)

    assert list(rows) == ["000001"]
    assert rows["000001"]["name"] == ""
    assert rows["000001"]["volume"] == 10_000
    assert rows["000001"]["amount"] == 200_000


def test_pricevol_computes_only_complete_market_breadth(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "configured", lambda: True)
    market_rows = []
    for index in range(4002):
        last = 10.0
        now = 10.1 if index < 2000 else (9.9 if index < 3500 else 10.0)
        market_rows.append(
            {
                "entity_key": f"{index:06d}.SZ",
                "as_of": "2026-07-13 11:00:00",
                "payload": {"Now": now, "LastClose": last, "Volume": 100},
            }
        )
    monkeypatch.setattr(
        tdx,
        "_request_json_result",
        lambda *_args, **_kwargs: ({"rows": market_rows, "has_more": False}, 200),
    )

    snapshot = tdx.pricevol_snapshot.__wrapped__()

    assert snapshot is not None
    assert snapshot["count"] == 4002
    assert snapshot["stocks"]["000001"]["volume"] == 10_000

    monkeypatch.setattr(tdx, "pricevol_snapshot", lambda: snapshot)
    breadth = tdx.pricevol_breadth.__wrapped__()

    assert breadth == {
        "up_count": 2000,
        "down_count": 1500,
        "flat_count": 502,
        "count": 4002,
        "as_of": "2026-07-13 11:00:00",
        "source": "TDX pricevol",
        "interval_seconds": 60,
    }


def test_health_is_redacted_and_does_not_advertise_snapshot(monkeypatch) -> None:
    monkeypatch.setenv("TDXAPI_BASE", "http://secret-host:8899")
    monkeypatch.setenv("TDXAPI_KEY", "test-key")
    monkeypatch.setattr(
        tdx,
        "_request_json",
        lambda path, **_kwargs: (
            {"count": 320, "last_push_at": "2026-07-13 09:35:02", "feed_age_seconds": 2.5}
            if path == "/v1/realtime/status"
            else {
                "market_data": {"lagging": False, "internal_host": "secret"},
                "items": [
                    {"dataset": "pricevol", "status": "fresh"},
                    {"dataset": "snapshot", "status": "stale"},
                    {"dataset": "l2_sector_fundflow", "status": "fresh"},
                    {"dataset": "l2_sector_realtime", "status": "fresh"},
                ],
            }
        ),
    )

    health = tdx.service_health.__wrapped__()
    encoded = str(health)

    assert health == {
        "configured": True,
        "connected": True,
        "market_lagging": False,
        "realtime_endpoint": "available",
        "realtime_quotes": "live",
        "realtime_count": 320,
        "realtime_feed_age_seconds": 2.5,
        "realtime_last_push_at": "2026-07-13 09:35:02",
        "pricevol": "fresh",
        "sectors": "unknown",
        "sector_members": "unknown",
        "relation": "unknown",
        "sector_fund_daily": "fresh",
        "sector_fund_realtime": "fresh",
    }
    assert "secret" not in encoded
    assert "snapshot" not in encoded


def test_tdx_market_news_reads_one_page_dedupes_and_redacts_credentials(monkeypatch) -> None:
    monkeypatch.setenv("TDXAPI_BASE", "http://secret-host:8899")
    monkeypatch.setenv("TDXAPI_KEY", "secret-key")
    calls: list[tuple[str, dict]] = []

    def fake_request(path, params, **_kwargs):
        calls.append((path, params))
        return {
            "rows": [
                {
                    "series_key": "2026-07-13 10:20:00.0",
                    "payload": {"title": "中药板块走强", "Summary": "中药板块走强，龙头甲涨停"},
                },
                {
                    "series_key": "2026-07-13 10:19:00.0",
                    "payload": {"title": "中药板块走强！", "Summary": "重复事件"},
                },
                {"series_key": "2026-07-13 10:18:00.0", "payload": {}},
            ]
        }

    monkeypatch.setattr(tdx, "_request_json", fake_request)
    result = tdx.market_news.__wrapped__(20)

    assert calls == [
        ("/v1/data/news_sentiment", {"limit": 120, "order": "desc"})
    ]
    assert len(result["items"]) == 1
    assert result["latest_event_time"] == "2026-07-13 10:20:00.0"
    assert result["coverage"] == {"summary_or_title": {"complete": 2, "total": 3}}
    assert "secret-host" not in str(result)
    assert "secret-key" not in str(result)


def test_sector_fund_rank_keeps_raw_unit_and_source_health(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "configured", lambda: True)
    monkeypatch.setattr(
        tdx,
        "_request_json",
        lambda path, params, **_kwargs: {
            "dataset": "l2_sector_realtime",
            "source": "realtime",
            "field": "ZJBY.主力净额",
            "direction": "desc",
            "count": 2,
            "total_ranked": 588,
            "unit": "tdx_raw_unverified",
            "source_completed_at": "2026-07-16 14:01:00",
            "source_health": {"status": "ok", "data_date": "20260716"},
            "rows": [
                {"rank": 1, "sector_code": "881001.SH", "sector_name": "行业甲", "value": "123.5"},
                {"rank": 2, "sector_code": "881002.SH", "sector_name": "行业乙", "value": -8},
            ],
        },
    )

    result = tdx.sector_fund_rank.__wrapped__(limit=588)

    assert result is not None
    assert result["source"] == "realtime"
    assert result["data_date"] == "2026-07-16"
    assert result["unit"] == "tdx_raw_unverified"
    assert result["unit_verified"] is False
    assert result["rows"][0]["value"] == pytest.approx(123.5)


def test_after_hours_research_routes_consensus_and_holders_but_blocks_gpjy(monkeypatch) -> None:
    calls: list[str] = []
    gp_one_rows = [{
        "entity_key": "600519.SH",
        "series_key": "20260713",
        "payload": {
            "GO3": "1724.20", "GO4": "2026", "GO5": "68.18", "GO6": "72.14", "GO7": "76.58",
            "GO8": "8537432", "GO9": "9033654", "GO10": "9589189",
            "GO11": "17893696", "GO12": "18839974", "GO13": "19904738",
            "GO20": "31.47", "GO21": "30.21", "GO22": "29.44",
        },
    }]
    holder_rows = [{
        "entity_key": "600519.SH",
        "series_key": "20260331",
        "payload": {"holders": [
            {"pm": "1", "name": "第一大股东", "cgsl": "1000", "cgbl": "54.4"},
            {"pm": "2", "name": "香港中央结算有限公司", "cgsl": "100", "cgbl": "8.1"},
        ]},
    }]

    def fake_rows(dataset, *_args, **_kwargs):
        calls.append(dataset)
        return gp_one_rows if dataset == "gp_one" else holder_rows

    monkeypatch.setattr(tdx, "_dataset_rows", fake_rows)
    result = tdx.after_hours_research.__wrapped__("600519")

    assert calls == ["gp_one", "holders_top10"]
    assert result["scope"] == "after_hours_research_only"
    assert result["intraday_score_weight"] == 0
    assert result["consensus"]["target_price_cny"] == pytest.approx(1724.2)
    assert result["consensus"]["net_profit_yi_cny"][0]["value"] == pytest.approx(853.74)
    assert result["holders_top10"]["top10_total_ratio_pct"] == pytest.approx(62.5)
    assert result["holders_top10"]["hk_clearing_ratio_pct"] == pytest.approx(8.1)
    assert result["gpjy"]["status"] == "blocked"
    assert "GP字段" in result["gpjy"]["reason"]


def test_sector_catalog_and_memberships_build_one_consistent_industry_taxonomy(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "configured", lambda: True)
    catalog_rows = [
        {
            "entity_key": f"881{index:03d}.SH",
            "as_of": "20260713",
            "payload": {"Name": f"行业{index}"},
        }
        for index in range(80)
    ] + [{
        "entity_key": "880753.SH",
        "as_of": "20260713",
        "payload": {"Name": "绿色电力"},
    }]
    monkeypatch.setattr(
        tdx,
        "_dataset_rows",
        lambda dataset, *_args, **_kwargs: catalog_rows if dataset == "sectors" else None,
    )
    catalog = tdx.sector_catalog.__wrapped__()

    assert catalog is not None and len(catalog) == 81
    assert catalog["881000.SH"]["kind"] == "industry"
    assert catalog["880753.SH"]["kind"] == "concept"

    small_catalog = {
        "881001.SH": {"code": "881001.SH", "name": "行业甲", "kind": "industry"},
        "881002.SH": {"code": "881002.SH", "name": "行业乙", "kind": "industry"},
    }
    members = [
        {
            "entity_key": "881001.SH" if index < 2000 else "881002.SH",
            "series_key": f"{index:06d}.SZ",
            "as_of": "20260713",
            "payload": {
                "sector_code": "881001.SH" if index < 2000 else "881002.SH",
                "Code": f"{index:06d}.SZ",
            },
        }
        for index in range(3001)
    ]
    monkeypatch.setattr(tdx, "sector_catalog", lambda: small_catalog)
    monkeypatch.setattr(
        tdx,
        "_dataset_rows",
        lambda dataset, *_args, **_kwargs: members if dataset == "sector_members" else None,
    )
    index = tdx.sector_memberships.__wrapped__()

    assert index is not None
    assert index["stock_count"] == 3001
    assert index["by_stock"]["000001"]["industry"] == "行业甲"
    assert index["by_stock"]["002500"]["industry"] == "行业乙"


def test_relation_industry_is_filtered_from_non_industry_blocks(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "_dataset_rows", lambda *_args, **_kwargs: [
        {"entity_key": "000001.SZ", "payload": {"BlockType": "概念", "BlockName": "银行概念", "BlockCode": "880001.SH"}},
        {"entity_key": "000001.SZ", "payload": {"BlockType": "行业", "BlockName": "全国性银行", "BlockCode": "881157.SH"}},
    ])

    result = tdx._relation_industries_cached.__wrapped__(("000001.SZ",))

    assert result == {
        "000001": {
            "industry": "全国性银行",
            "sector_code": "881157.SH",
            "source": "TDX relation",
        }
    }


def test_relation_blocks_keep_industry_and_concepts_but_drop_style(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "_dataset_rows", lambda *_args, **_kwargs: [
        {"entity_key": "605028.SH", "payload": {"BlockType": "行业", "BlockName": "电力", "BlockCode": "881459.SH"}},
        {"entity_key": "605028.SH", "payload": {"BlockType": "概念", "BlockName": "绿色电力", "BlockCode": "880753.SH"}},
        {"entity_key": "605028.SH", "payload": {"BlockType": "概念", "BlockName": "光伏", "BlockCode": "880544.SH"}},
        {"entity_key": "605028.SH", "payload": {"BlockType": "风格", "BlockName": "昨日涨停", "BlockCode": "880863.SH"}},
    ])

    result = tdx._relation_blocks_cached.__wrapped__(("605028.SH",))

    assert result["605028"]["industry"]["name"] == "电力"
    assert [item["name"] for item in result["605028"]["concepts"]] == ["绿色电力", "光伏"]


def test_industry_for_codes_reuses_full_relation_cache(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def relation_blocks(entities: tuple[str, ...]):
        calls.append(entities)
        return {
            "605028": {
                "industry": {
                    "name": "电力",
                    "code": "881459.SH",
                    "source": "TDX relation",
                },
                "concepts": [{"name": "绿色电力", "code": "880753.SH"}],
                "source": "TDX relation",
            }
        }

    monkeypatch.setattr(tdx, "_relation_blocks_cached", relation_blocks)
    monkeypatch.setattr(
        tdx,
        "_relation_industries_cached",
        lambda _entities: pytest.fail("风险行业核验不得重复请求独立 relation 缓存"),
    )
    monkeypatch.setattr(
        tdx,
        "sector_memberships",
        lambda: pytest.fail("精确 relation 已有行业时不得再读全市场成分表"),
    )

    result = tdx.industry_for_codes(["605028"])

    assert calls == [("605028.SH",)]
    assert result == {
        "605028": {
            "industry": "电力",
            "sector_code": "881459.SH",
            "source": "TDX relation",
        }
    }


def test_basic_financial_uses_named_stock_info_fields_without_inventing_growth(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "configured", lambda: True)
    monkeypatch.setattr(
        tdx,
        "_request_json",
        lambda path, params, **_kwargs: {
            "rows": [{
                "entity_key": "600519.SH",
                "series_key": "20260715",
                "as_of": "20260715",
                "payload": {
                    "J_jyl": "10.06",
                    "J_yysy": "5390925.00",
                    "J_yycb": "552072.94",
                    "J_zzc": "31991884.00",
                    "J_ldfz": "3845559.50",
                    "J_cqfz": "1024185.13",
                    "J_mgsy": "87.17",
                    "J_mgjzc": "216.32",
                },
            }]
        },
    )

    result = tdx.basic_financial.__wrapped__("600519")

    assert result is not None
    assert result["data_date"] == "2026-07-15"
    assert result["roe"] == 10.06
    assert result["grossprofit_margin"] == pytest.approx(89.7592)
    assert result["debt_to_assets"] == pytest.approx(15.2218)
    assert result["eps"] == 87.17
    assert result["netprofit_yoy"] is None
    assert result["or_yoy"] is None
    assert result["missing"] == ["净利润同比", "营收同比"]


def test_sector_members_for_codes_supports_concept_blocks(monkeypatch) -> None:
    monkeypatch.setattr(tdx, "_dataset_rows", lambda *_args, **_kwargs: [
        {"entity_key": "880753.SH", "series_key": "605028.SH", "as_of": "20260713", "payload": {"sector_code": "880753.SH", "Code": "605028.SH", "Name": "世茂能源"}},
        {"entity_key": "880753.SH", "series_key": "600900.SH", "as_of": "20260713", "payload": {"sector_code": "880753.SH", "Code": "600900.SH", "Name": "长江电力"}},
    ])

    result = tdx._sector_members_for_codes_cached.__wrapped__(("880753.SH",))

    assert result["by_sector"]["880753.SH"] == ("605028", "600900")
    assert result["names"] == {"605028": "世茂能源", "600900": "长江电力"}
    assert result["member_count"] == 2
    assert result["data_date"] == "2026-07-13"


def test_industry_market_uses_sector_quote_amount_and_member_breadth(monkeypatch) -> None:
    industries = {
        f"881{index:03d}.SH": {
            "code": f"881{index:03d}.SH", "name": f"行业{index}", "kind": "industry",
        }
        for index in range(80)
    }
    by_sector = {
        code: (f"{index:06d}",)
        for index, code in enumerate(industries)
    }
    stock_frame = {
        f"{index:06d}": {
            "price": 10 + index, "prev_close": 10, "change_pct": float(index),
        }
        for index in range(80)
    }
    quote_map = {
        tdx._code6(code): {
            "price": 100 + index, "change_pct": float(index) / 10, "amount": 1_000_000 + index,
        }
        for index, code in enumerate(industries)
    }
    monkeypatch.setattr(tdx, "sector_catalog", lambda: industries)
    monkeypatch.setattr(tdx, "sector_memberships", lambda: {"by_sector": by_sector, "data_date": "2026-07-13"})
    monkeypatch.setattr(tdx, "pricevol_snapshot", lambda: {"stocks": stock_frame, "data_date": "2026-07-13"})
    monkeypatch.setattr(tdx, "quotes", lambda *_args, **_kwargs: quote_map)
    monkeypatch.setattr(tdx, "stock_universe", lambda: {f"{index:06d}": f"股票{index}" for index in range(80)})

    result = tdx.industry_market.__wrapped__()

    assert result is not None
    assert result["data_state"] == "complete"
    assert result["coverage"]["sector_quotes"] == {"complete": 80, "total": 80}
    assert result["sectors"][1]["amount"] == 1_000_001
    assert result["sectors"][1]["up_count"] == 1
    assert result["sectors"][1]["leader_name"] == "股票1"


def test_batch_quote_route_validates_and_returns_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        routes.stock,
        "quick_quotes",
        lambda codes: {"000001": {"code": "000001", "price": 10.5, "source": "TDX 数据 API"}},
    )

    response = asyncio.run(routes.quick_quotes("000001,600030,000001"))

    assert response["ok"] is True
    assert list(response["quotes"]) == ["000001"]
    assert response["missing"] == ["600030"]
