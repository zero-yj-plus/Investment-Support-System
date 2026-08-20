from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from hermes_trader.analysis.mainline import build_mainline_response
from hermes_trader.analysis.mainline_timeline import observe_response
from hermes_trader.analysis.engine import _trim_sectors_symmetric
from hermes_trader.jobs import mainline_sampler
from hermes_trader.api.routes import (
    _merge_cockpit_narrative,
    _narrative_field_is_safe,
    _narrative_is_safe,
)


DATA_DATE = "2026-07-10"


def _sector(name: str, amount, *, flow=0, change=1.0, up=7, down=3, flat=0) -> dict:
    return {
        "kind": "industry",
        "name": name,
        "amount": amount,
        "main_flow": flow,
        "change_pct": change,
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
    }


def _stock(code: str, name: str, *, lianban=1, change=10.0, **extra) -> dict:
    return {
        "code": code,
        "name": name,
        "lianban": lianban,
        "change_pct": change,
        **extra,
    }


def _response(sectors, stocks, industry_map, **kwargs):
    kwargs.setdefault(
        "market_context",
        {
            "up_count": 3500,
            "down_count": 1500,
            "flat_count": 50,
            "limit_up": 60,
            "limit_down": 8,
            "zhaban_rate": 18,
        },
    )
    return build_mainline_response(
        {
            "ok": True,
            "ts": f"{DATA_DATE} 15:10:00",
            "data_date": DATA_DATE,
            "missing": [],
            "sectors": sectors,
        },
        {
            "ok": True,
            "ts": f"{DATA_DATE} 15:10:00",
            "data_date": DATA_DATE,
            "missing": [],
            "stocks": stocks,
        },
        industry_map,
        limit=20,
        **kwargs,
    )


def _base_sectors():
    return [
        _sector("高容量", 1000, flow=-100),
        _sector("行业B", 800),
        _sector("行业C", 600),
        _sector("行业D", 400),
        _sector("高净流入但低容量", 200, flow=9_999_999_999),
    ]


def test_high_flow_low_amount_and_zero_limitups_is_not_mainline():
    result = _response(_base_sectors(), [], {})
    row = next(item for item in result["sectors"] if item["name"] == "高净流入但低容量")

    assert row["label"] == "观察方向"
    assert row["limit_up_count"] == 0
    assert row["main_net"] == 9_999_999_999
    assert all(item["label"] != "主线" for item in result["sectors"])


def test_high_amount_many_limitups_is_mainline_even_with_negative_flow():
    stocks = [_stock("000001", "甲", lianban=2), _stock("000002", "乙", lianban=3)]
    result = _response(
        _base_sectors(),
        stocks,
        {"000001": {"industry": "高容量"}, "000002": {"industry": "高容量"}},
    )
    row = next(item for item in result["sectors"] if item["name"] == "高容量")

    assert row["label"] == "主线"
    assert row["limit_up_count"] == 2
    assert row["max_lianban"] == 3
    assert row["main_net"] == -100
    assert row["score"] is not None
    assert result["data_state"] == "complete"
    assert result["coverage"]["limit_up_mapping"] == {"complete": 2, "total": 2}


def test_high_turnover_collapsing_sector_is_not_misclassified_as_mainline():
    sectors = [
        _sector("恐慌放量", 1000, change=-6.4, up=2, down=38),
        _sector("普通行业", 800, change=0.2, up=11, down=9),
        _sector("其它行业", 600, change=-0.3, up=8, down=12),
        _sector("防守行业", 400, change=0.8, up=13, down=7),
        _sector("弱势行业", 200, change=-1.0, up=4, down=16),
    ]
    stocks = [_stock("000001", "甲", lianban=3), _stock("000002", "乙", lianban=2)]
    result = _response(
        sectors,
        stocks,
        {"000001": "恐慌放量", "000002": "恐慌放量"},
    )
    row = next(item for item in result["sectors"] if item["name"] == "恐慌放量")

    assert row["amount_rank_pct"] == 0.2
    assert row["limit_up_count"] == 2
    assert row["breadth_ratio"] == 0.05
    assert row["classification"]["mainline"] is False
    assert row["label"] == "观察方向"


def test_retreat_roles_find_active_and_emotion_chain_without_forcing_mainline():
    sectors = [
        _sector("高容量崩跌", 1000, change=-6.8, up=2, down=38),
        _sector("化学制药", 800, change=-0.6, up=50, down=83),
        _sector("中药", 600, change=2.8, up=56, down=12),
        _sector("地方银行", 400, change=2.3, up=26, down=1),
        _sector("通用设备", 300, change=-6.7, up=12, down=241),
        _sector("其它行业", 200, change=-2.0, up=5, down=20),
    ]
    stocks = [
        _stock("000001", "药一", lianban=3),
        _stock("000002", "药二", lianban=2),
        _stock("000003", "药三", lianban=2),
        _stock("000004", "药四", lianban=1),
        _stock("000005", "中药活口", lianban=1),
    ]
    mapping = {
        "000001": "化学制药",
        "000002": "化学制药",
        "000003": "化学制药",
        "000004": "化学制药",
        "000005": "中药",
    }
    result = _response(
        sectors,
        stocks,
        mapping,
        market_context={
            "up_count": 714,
            "down_count": 4764,
            "flat_count": 50,
            "limit_up": 25,
            "limit_down": 125,
            "zhaban_rate": 40.5,
        },
    )

    assert [role["label"] for role in result["roles"]] == ["主线", "次主线", "次主线", "活口", "情绪链"]
    assert result["market_state"]["state"] == "退潮"
    assert result["roles"][0]["status"] == "unformed"
    assert result["roles"][1]["status"] == "unformed"
    assert result["roles"][2]["status"] == "unformed"
    assert result["roles"][3]["name"] == "中药"
    assert result["roles"][4]["name"] == "化学制药"
    formed_names = [role["name"] for role in result["roles"] if role["status"] == "formed"]
    assert len(formed_names) == len(set(formed_names))
    assert result["algorithm"]["main_flow_weight"] == 0


def test_main_flow_has_exactly_zero_weight():
    stocks = [_stock("000001", "甲"), _stock("000002", "乙")]
    mapping = {"000001": "高容量", "000002": "高容量"}
    low_flow = _response(_base_sectors(), stocks, mapping)
    changed = _base_sectors()
    changed[0]["main_flow"] = 10**15
    high_flow = _response(changed, stocks, mapping)

    low_row = next(item for item in low_flow["sectors"] if item["name"] == "高容量")
    high_row = next(item for item in high_flow["sectors"] if item["name"] == "高容量")
    assert low_row["score"] == high_row["score"]
    assert low_row["label"] == high_row["label"] == "主线"
    assert low_row["main_net"] != high_row["main_net"]


def test_catalyst_exact_match_adds_evidence_without_changing_structure_score():
    stocks = [_stock("000001", "龙头甲", lianban=2), _stock("000002", "龙头乙", lianban=3)]
    mapping = {"000001": "高容量", "000002": "高容量"}
    baseline = _response(_base_sectors(), stocks, mapping)
    with_news = _response(
        _base_sectors(),
        stocks,
        mapping,
        catalyst_payload={
            "items": [
                {
                    "title": "高容量行业获政策支持，龙头乙涨停",
                    "content": "高容量行业订单改善",
                    "time": f"{DATA_DATE} 10:20:00",
                    "confirms": 2,
                    "multi": True,
                },
                {
                    "title": "昨日高容量旧闻",
                    "content": "不同交易日不应命中",
                    "time": "2026-07-09 15:00:00",
                },
            ],
            "latest_event_time": f"{DATA_DATE} 10:20:00",
            "missing": [],
        },
    )

    before = next(item for item in baseline["sectors"] if item["name"] == "高容量")
    after = next(item for item in with_news["sectors"] if item["name"] == "高容量")
    assert after["score"] == before["score"]
    assert after["label"] == before["label"] == "主线"
    assert after["catalyst_status"] == "confirmed"
    assert after["catalyst_evidence"] == [{
        "title": "高容量行业获政策支持，龙头乙涨停",
        "time": f"{DATA_DATE} 10:20:00",
        "confirms": 2,
        "multi": True,
        "match": "行业名+龙头名",
    }]
    assert with_news["catalyst_layer"]["score_weight"] == 0
    assert with_news["catalyst_layer"]["candidate_items"] == 1


def test_catalyst_absence_is_structure_only_not_negative_score():
    stocks = [_stock("000001", "龙头甲"), _stock("000002", "龙头乙")]
    result = _response(
        _base_sectors(),
        stocks,
        {"000001": "高容量", "000002": "高容量"},
        catalyst_payload={
            "items": [{
                "title": "完全无关行业消息", "content": "没有行业或龙头原文",
                "time": f"{DATA_DATE} 11:00:00",
            }],
            "missing": [],
        },
    )
    row = next(item for item in result["sectors"] if item["name"] == "高容量")

    assert row["catalyst_status"] == "structure_only"
    assert row["catalyst_evidence"] == []
    assert row["score"] is not None


def test_leader_quality_uses_verified_pool_fields_without_changing_structure_score():
    weak = _stock(
        "000001", "反复开板", lianban=2, change=10.0,
        zhaban_count=3, seal_amount=1_000_000, amount=100_000_000,
        first_time="14:20:00", last_time="14:55:00", turnover_rate=28.2,
    )
    strong = _stock(
        "000002", "早封龙头", lianban=2, change=9.9,
        zhaban_count=0, seal_amount=30_000_000, amount=100_000_000,
        first_time="09:31:00", last_time="09:31:00", turnover_rate=8.5,
    )
    mapping = {"000001": "高容量", "000002": "高容量"}
    baseline = _response(_base_sectors(), [weak, strong], mapping)
    reversed_input = _response(_base_sectors(), [strong, weak], mapping)
    row = next(item for item in baseline["sectors"] if item["name"] == "高容量")
    same = next(item for item in reversed_input["sectors"] if item["name"] == "高容量")

    assert row["leader"]["name"] == same["leader"]["name"] == "早封龙头"
    assert row["leader_quality"]["level"] == "承接较强"
    assert row["leader_quality"]["status"] == "complete"
    assert row["leader_quality"]["evidence"][:3] == [
        "开板0次", "封单/成交30.0%", "首封09:31:00",
    ]
    assert row["score"] == same["score"]
    assert baseline["algorithm"]["leader_quality_weight"] == 0


def test_missing_leader_quality_fields_stay_explicit() -> None:
    result = _response(
        _base_sectors(),
        [_stock("000001", "字段不全甲"), _stock("000002", "字段不全乙")],
        {"000001": "高容量", "000002": "高容量"},
    )
    row = next(item for item in result["sectors"] if item["name"] == "高容量")

    assert row["leader_quality"]["level"] == "待验证"
    assert row["leader_quality"]["observed_dimensions"] == 0
    assert row["leader_quality"]["missing_fields"] == [
        "开板次数", "封单/成交", "首次封板时间",
    ]


def test_calculation_does_not_mutate_source_payloads():
    sectors = _base_sectors()
    stocks = [_stock("000001", "甲"), _stock("000002", "乙")]
    mapping = {"000001": {"industry": "高容量"}, "000002": {"industry": "高容量"}}
    before = deepcopy((sectors, stocks, mapping))

    _response(sectors, stocks, mapping)

    assert (sectors, stocks, mapping) == before


def test_report_sector_trimming_does_not_mutate_cached_source_payload() -> None:
    source = {
        "sectors": [{"kind": "industry", "name": f"行业{i}"} for i in range(10)],
        "missing": ["上游说明"],
        "coverage": {"industries": 10},
    }
    before = deepcopy(source)

    trimmed = _trim_sectors_symmetric(source, 2, 2)

    assert source == before
    assert [row["name"] for row in trimmed["sectors"]] == [
        "行业0", "行业1", "行业8", "行业9",
    ]
    assert trimmed["coverage"] == {"industries": 10}
    assert trimmed["missing"][0] == "上游说明"
    assert "仅注入涨幅前2+跌幅后2" in trimmed["missing"][1]


def test_only_one_gate_met_stays_observation():
    sectors = _base_sectors()
    stocks = [
        _stock("000001", "顶部行业独苗"),
        _stock("000010", "小板块甲"),
        _stock("000011", "小板块乙"),
        _stock("000012", "小板块丙"),
    ]
    mapping = {
        "000001": "高容量",
        "000010": "高净流入但低容量",
        "000011": "高净流入但低容量",
        "000012": "高净流入但低容量",
    }
    result = _response(sectors, stocks, mapping)
    by_name = {row["name"]: row for row in result["sectors"]}

    assert by_name["高容量"]["limit_up_count"] == 1
    assert by_name["高容量"]["label"] == "观察方向"
    assert by_name["高净流入但低容量"]["limit_up_count"] == 3
    assert by_name["高净流入但低容量"]["label"] == "观察方向"


def test_missing_mapping_is_reported_and_never_replaced_by_flow_sort():
    result = _response(
        _base_sectors(),
        [_stock("000999", "行业未知股")],
        {},
    )

    assert any("行业映射" in message for message in result["missing"])
    assert any("未降级按净流入排序" in message for message in result["missing"])
    assert all(item["limit_up_count"] is None for item in result["sectors"])
    assert all(item["label"] == "观察方向" for item in result["sectors"])
    assert result["data_state"] == "missing"
    assert result["missing_fields"]


def test_unavailable_empty_limitup_detail_is_not_treated_as_zero():
    result = build_mainline_response(
        {
            "data_date": DATA_DATE,
            "ts": f"{DATA_DATE} 10:00:00",
            "missing": [],
            "sectors": _base_sectors(),
        },
        {
            "data_date": DATA_DATE,
            "missing": ["涨停连板梯队明细盘后更新"],
            "stocks": [],
        },
        {},
        limit=20,
    )

    assert all(item["limit_up_count"] is None for item in result["sectors"])
    assert any("未将空列表解释为0家" in message for message in result["missing"])


def test_data_dates_must_match():
    result = build_mainline_response(
        {"data_date": "2026-07-10", "ts": "x", "missing": [], "sectors": _base_sectors()},
        {"data_date": "2026-07-09", "missing": [], "stocks": []},
        {},
    )

    assert result["sectors"] == []
    assert result["data_date"] == "2026-07-09"
    assert any("data_date不一致" in message for message in result["missing"])


def test_limit_down_pool_has_fixed_shape_and_missing_industry_is_explicit():
    result = _response(
        _base_sectors(),
        [],
        {},
        limit_down_rows=[{
            "code": "600001",
            "name": "跌停样本",
            "change_pct": -10.0,
            "first_time": "09:31:00",
            "seal_amount": 2_000_000,
            "open_times": 1,
        }],
    )

    assert result["limit_down_pool"] == [{
        "code": "600001",
        "name": "跌停样本",
        "change_pct": -10.0,
        "industry": None,
        "first_time": "09:31:00",
        "seal_amount": 2_000_000.0,
        "open_times": 1,
    }]
    assert any("跌停股行业映射缺失" in message for message in result["limit_down_missing"])


def test_same_row_industry_is_used_before_global_mapping():
    sectors = _base_sectors()
    result = _response(
        sectors,
        [{
            "code": "000001", "name": "同源行业样本", "industry": sectors[0]["name"],
            "change_pct": 10.0, "lianban": 2,
        }],
        {},
        limit_down_rows=[{
            "code": "600001", "name": "跌停同源样本", "industry": sectors[0]["name"],
            "change_pct": -10.0,
        }],
    )

    assert result["sectors"][0]["limit_up_count"] == 1
    assert result["sectors"][0]["limit_up_stocks"][0]["industry"] == sectors[0]["name"]
    assert result["limit_down_pool"][0]["industry"] == sectors[0]["name"]
    assert not any("行业未与行业榜精确对齐" in message for message in result["missing"])


def test_no_limit_down_details_keeps_pool_empty_with_explicit_reason():
    result = _response(
        _base_sectors(),
        [],
        {},
        limit_down_rows=None,
        limit_down_missing=["当前可靠源仅有跌停家数，不猜明细"],
    )

    assert result["limit_down_pool"] == []
    assert result["limit_down_missing"] == ["当前可靠源仅有跌停家数，不猜明细"]


def test_cached_narrative_cannot_restore_net_inflow_as_mainline() -> None:
    assert not _narrative_is_safe({
        "主线": "第一主线：计算机设备，净流入26.96亿元",
        "操作结论": "只允许围绕净流入靠前方向低吸",
    })


def test_cached_narrative_rejects_none_values_but_allows_risk_outflow_evidence() -> None:
    assert not _narrative_is_safe({"盘面推理": "最高连板None板，炸板率None%"})
    assert _narrative_is_safe({
        "主线": "主线以成交额×涨停家数双门槛规则卡为准",
        "回避": "半导体净流出279亿元，先等止跌确认",
        "操作结论": "弱势持仓优先减仓，现金优先",
    })


def test_bad_cached_mainline_does_not_block_safe_tone_and_avoid_copy() -> None:
    old_cache = {
        "ok": True,
        "定调": "危机避险：今天保命优先现金为王，弱势持仓优先减。",
        "主线": "第一主线：计算机设备，净流入26.96亿元",
        "回避": "回避半导体净流出279亿元，先等止跌确认。",
        "盘面推理": "最高连板None板，炸板率None%",
        "定格时点": "收盘",
    }

    assert _narrative_field_is_safe("定调", old_cache["定调"])
    assert not _narrative_field_is_safe("主线", old_cache["主线"])
    assert _narrative_field_is_safe("回避", old_cache["回避"])
    merged = _merge_cockpit_narrative({"risk_level": 4}, old_cache)

    assert merged["定调叙述"] == old_cache["定调"]
    assert merged["回避叙述"] == old_cache["回避"]
    assert "主线叙述" not in merged
    assert merged["叙述定格"] == "收盘"


def test_stale_narrative_cannot_mix_with_a_newer_locked_command() -> None:
    merged = _merge_cockpit_narrative({
        "regime": "弱势退潮",
        "risk_level": 4,
        "定格时点": "09:40",
    }, {
        "ok": True,
        "stale": True,
        "定格时点": "09:30",
        "定调": "危机避险：今天保命优先现金为王，情绪温度78(亢奋)。",
        "回避": "不追高。",
    })

    assert "定调叙述" not in merged
    assert "回避叙述" not in merged


def test_narrative_with_a_different_lock_is_not_merged_even_without_stale_flag() -> None:
    merged = _merge_cockpit_narrative({
        "regime": "弱势退潮",
        "risk_level": 4,
        "定格时点": "09:40",
    }, {
        "ok": True,
        "定格时点": "09:30",
        "定调": "危机避险：今天保命优先现金为王。",
    })

    assert "定调叙述" not in merged


def test_ai_mainline_is_never_merged_even_without_net_inflow_wording() -> None:
    fabricated = "第一主线：白酒，成交额9999亿元、涨停12家"

    assert not _narrative_field_is_safe("主线", fabricated)
    merged = _merge_cockpit_narrative({"risk_level": 2}, {
        "ok": True,
        "主线": "主线以成交额×涨停家数双门槛规则卡为准",
    })
    assert "主线叙述" not in merged


def test_tone_change_and_avoid_cannot_smuggle_mainline_assertions() -> None:
    for field, value in (
        ("定调", "今日主线是白酒，优先参与"),
        ("定调", "白酒是今日主线，优先参与"),
        ("回避", "白酒属于当前主线，其它方向回避"),
        ("change", "白酒成为新的第一主线"),
        ("change", "主线切换至白酒，积极参与"),
    ):
        assert not _narrative_field_is_safe(field, value)

    merged = _merge_cockpit_narrative({"risk_level": 2}, {
        "ok": True,
        "定调": "今日主线是白酒，优先参与",
        "回避": "当前主线为白酒，其它方向回避",
        "change": "第一主线：白酒",
    })
    assert "定调叙述" not in merged
    assert "回避叙述" not in merged
    assert "change" not in merged


def test_none_like_values_are_rejected_in_each_merged_copy_field() -> None:
    for field, value in (
        ("定调", "最高连板 None 板，保持观望"),
        ("回避", "炸板率 null%，先回避"),
        ("change", "情绪 undefined，观点变化"),
    ):
        assert not _narrative_field_is_safe(field, value)

    merged = _merge_cockpit_narrative({"risk_level": 3}, {
        "ok": True,
        "定调": "最高连板 None 板，保持观望",
        "回避": "炸板率 null%，先回避",
        "change": "情绪 undefined，观点变化",
    })
    assert "定调叙述" not in merged
    assert "回避叙述" not in merged
    assert "change" not in merged


def test_intraday_mainline_needs_two_consecutive_buckets_after_1030(tmp_path) -> None:
    stocks = [_stock("000001", "甲", lianban=2), _stock("000002", "乙", lianban=3)]
    result = _response(
        _base_sectors(),
        stocks,
        {"000001": "高容量", "000002": "高容量"},
    )
    cn = ZoneInfo("Asia/Shanghai")

    first = observe_response(tmp_path, result, datetime(2026, 7, 10, 10, 21, tzinfo=cn))
    first_main = next(role for role in first["roles"] if role["label"] == "主线")
    assert first_main["time_status"] == "forming"
    assert first["today_candidates"][0]["name"] == "高容量"
    assert first["today_mainlines"] == []

    second = observe_response(tmp_path, result, datetime(2026, 7, 10, 10, 31, tzinfo=cn))
    second_main = next(role for role in second["roles"] if role["label"] == "主线")
    assert second_main["time_status"] == "confirmed"
    assert second_main["consecutive_hits"] == 2
    assert second["today_mainlines"][0]["name"] == "高容量"


def test_non_consecutive_intraday_buckets_do_not_confirm(tmp_path) -> None:
    stocks = [_stock("000001", "甲", lianban=2), _stock("000002", "乙", lianban=3)]
    result = _response(
        _base_sectors(),
        stocks,
        {"000001": "高容量", "000002": "高容量"},
    )
    cn = ZoneInfo("Asia/Shanghai")

    observe_response(tmp_path, result, datetime(2026, 7, 10, 9, 51, tzinfo=cn))
    later = observe_response(tmp_path, result, datetime(2026, 7, 10, 10, 31, tzinfo=cn))
    main = next(role for role in later["roles"] if role["label"] == "主线")
    assert main["time_status"] == "forming"
    assert main["consecutive_hits"] == 1


def test_confirmed_mainline_downgrades_then_revokes_after_two_valid_misses(tmp_path) -> None:
    stocks = [_stock("000001", "甲", lianban=2), _stock("000002", "乙", lianban=3)]
    formed = _response(
        _base_sectors(),
        stocks,
        {"000001": "高容量", "000002": "高容量"},
    )
    absent = _response(_base_sectors(), [], {})
    cn = ZoneInfo("Asia/Shanghai")

    observe_response(tmp_path, formed, datetime(2026, 7, 10, 10, 21, tzinfo=cn))
    confirmed = observe_response(tmp_path, formed, datetime(2026, 7, 10, 10, 31, tzinfo=cn))
    assert confirmed["today_mainlines"][0]["name"] == "高容量"

    weakening = observe_response(tmp_path, absent, datetime(2026, 7, 10, 10, 41, tzinfo=cn))
    assert weakening["today_mainlines"] == []
    assert weakening["withdrawn_mainlines"] == [{
        "name": "高容量",
        "status": "weakening",
        "status_label": "降级观察",
        "miss_streak": 1,
        "last_seen_at": datetime(2026, 7, 10, 10, 31, tzinfo=cn).isoformat(),
        "reason": "连续1个10分钟档未通过结构硬门槛",
    }]

    revoked = observe_response(tmp_path, absent, datetime(2026, 7, 10, 10, 51, tzinfo=cn))
    assert revoked["withdrawn_mainlines"][0]["status"] == "revoked"
    assert revoked["withdrawn_mainlines"][0]["miss_streak"] == 2
    assert revoked["time_scope"]["missing_data_rule"] == "数据缺失档不触发形成、降级或撤销"


def test_post_close_full_snapshot_can_revoke_after_one_miss(tmp_path) -> None:
    stocks = [_stock("000001", "甲", lianban=2), _stock("000002", "乙", lianban=3)]
    formed = _response(
        _base_sectors(), stocks, {"000001": "高容量", "000002": "高容量"},
    )
    absent = _response(_base_sectors(), [], {})
    cn = ZoneInfo("Asia/Shanghai")

    observe_response(tmp_path, formed, datetime(2026, 7, 10, 14, 41, tzinfo=cn))
    observe_response(tmp_path, formed, datetime(2026, 7, 10, 14, 51, tzinfo=cn))
    final = observe_response(tmp_path, absent, datetime(2026, 7, 10, 15, 1, tzinfo=cn))

    assert final["withdrawn_mainlines"][0]["status"] == "revoked"
    assert final["withdrawn_mainlines"][0]["reason"] == "收盘完整截面未再通过结构硬门槛"


def test_mainline_and_secondary_role_changes_are_explicit(tmp_path) -> None:
    cn = ZoneInfo("Asia/Shanghai")
    first = {
        "data_date": DATA_DATE,
        "data_state": "complete",
        "roles": [
            {"label": "主线", "status": "formed", "name": "行业甲", "structure_score": 80},
            {"label": "次主线", "status": "formed", "name": "行业乙", "structure_score": 70},
        ],
        "algorithm": {},
    }
    swapped = deepcopy(first)
    swapped["roles"] = [
        {"label": "主线", "status": "formed", "name": "行业乙", "structure_score": 82},
        {"label": "次主线", "status": "formed", "name": "行业甲", "structure_score": 72},
    ]

    observe_response(tmp_path, first, datetime(2026, 7, 10, 10, 21, tzinfo=cn))
    result = observe_response(tmp_path, swapped, datetime(2026, 7, 10, 10, 31, tzinfo=cn))
    by_name = {role["name"]: role for role in result["roles"]}

    assert by_name["行业乙"]["role_transition"] == "promoted"
    assert by_name["行业乙"]["role_transition_label"] == "次升主"
    assert by_name["行业甲"]["role_transition"] == "demoted"
    assert by_name["行业甲"]["role_transition_label"] == "主转次"
    assert {item["status"] for item in result["role_transitions"]} == {"promoted", "demoted"}


def test_post_close_snapshot_is_final_but_persistent_mainline_accumulates(tmp_path) -> None:
    stocks = [_stock("000001", "甲", lianban=2), _stock("000002", "乙", lianban=3)]
    result = _response(
        _base_sectors(),
        stocks,
        {"000001": "高容量", "000002": "高容量"},
    )
    cn = ZoneInfo("Asia/Shanghai")

    first = observe_response(tmp_path, result, datetime(2026, 7, 10, 15, 1, tzinfo=cn))
    main = next(role for role in first["roles"] if role["label"] == "主线")
    assert main["time_status"] == "final"
    assert first["time_scope"]["product_label"] == "今日行业主线"
    assert first["persistence"]["status"] == "accumulating"
    assert first["persistence"]["covered_days"] == 1

    for day in (11, 12):
        next_day = deepcopy(result)
        next_day["data_date"] = f"2026-07-{day:02d}"
        first = observe_response(tmp_path, next_day, datetime(2026, 7, day, 15, 1, tzinfo=cn))
    assert first["persistence"]["status"] == "ready"
    assert first["persistent_mainlines"][0]["name"] == "高容量"
    assert first["persistent_mainlines"][0]["hits_3d"] == 3


def test_mainline_sampler_runs_only_in_a_share_sampling_windows(monkeypatch) -> None:
    monkeypatch.setattr(mainline_sampler, "is_trade_day", lambda _date: True)
    cn = ZoneInfo("Asia/Shanghai")

    assert not mainline_sampler.in_sampling_window(datetime(2026, 7, 10, 9, 20, tzinfo=cn))
    assert mainline_sampler.in_sampling_window(datetime(2026, 7, 10, 9, 30, tzinfo=cn))
    assert mainline_sampler.in_sampling_window(datetime(2026, 7, 10, 11, 30, tzinfo=cn))
    assert not mainline_sampler.in_sampling_window(datetime(2026, 7, 10, 12, 0, tzinfo=cn))
    assert mainline_sampler.in_sampling_window(datetime(2026, 7, 10, 15, 0, tzinfo=cn))
    assert not mainline_sampler.in_sampling_window(datetime(2026, 7, 10, 15, 1, tzinfo=cn))
