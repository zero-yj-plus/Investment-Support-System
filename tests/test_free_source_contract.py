from __future__ import annotations

import ast
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from hermes_trader.analysis import diagnosis, forecast_mining, screener
from hermes_trader.api import routes
from hermes_trader.data import free_source, market, stock, tencent_source


ROOT = Path(__file__).resolve().parents[1]


def _tencent_fields(code: str = "600000") -> list[str]:
    """构造一条最小完整的腾讯 A 股行情记录。"""
    fields = [""] * 50
    fields[1] = "浦发银行"
    fields[2] = code
    fields[3] = "10.50"       # 现价：元
    fields[4] = "10.00"       # 昨收：元
    fields[5] = "10.10"       # 今开：元
    fields[6] = "12345"       # 主板=手，科创板=股
    fields[32] = "5.00"       # 涨跌幅：%
    fields[33] = "10.60"      # 最高：元
    fields[34] = "9.95"       # 最低：元
    fields[37] = "234.50"     # 成交额：万元
    fields[38] = "2.50"       # 换手率：%
    fields[39] = "12.20"      # PE
    fields[44] = "12.30"      # 流通市值：亿元
    fields[45] = "20.50"      # 总市值：亿元
    fields[46] = "1.30"       # PB
    fields[47] = "11.00"      # 涨停价：元
    fields[48] = "9.00"       # 跌停价：元
    fields[49] = "1.70"       # 量比
    return fields


def test_tencent_mapping_and_free_daily_basic_use_project_units(monkeypatch) -> None:
    quote = tencent_source._parse_stock(_tencent_fields())

    assert quote is not None
    assert quote["price"] == pytest.approx(10.50)
    assert quote["change_pct"] == pytest.approx(5.0)
    assert quote["volume"] == 1_234_500       # 主板：手→股
    assert quote["amount"] == pytest.approx(2_345_000)  # 万元→元
    assert quote["float_mv"] == pytest.approx(1_230_000_000)  # 亿元→元
    assert quote["total_mv"] == pytest.approx(2_050_000_000)
    assert quote["up_limit"] == pytest.approx(11.0)
    assert quote["down_limit"] == pytest.approx(9.0)

    science = tencent_source._parse_stock(_tencent_fields("688001"))
    assert science is not None
    assert science["volume"] == 12_345        # 科创板原值已是股，不能再×100

    monkeypatch.setattr(free_source.tc, "quote", lambda code: quote)
    monkeypatch.setattr(free_source, "quote_data_date", lambda: "2026-07-13")
    basic = free_source.daily_basic.__wrapped__("600000")

    assert basic == {
        "trade_date": "20260713",
        "turnover_rate": 2.5,
        "volume_ratio": 1.7,
        "pe": 12.2,
        "pe_ttm": 12.2,
        "pb": 1.3,
        "total_mv": 2_050_000_000.0,
        "circ_mv": 1_230_000_000.0,
        "source": "腾讯财经",
    }


def test_tencent_full_market_scan_keeps_exact_limit_down_names(monkeypatch) -> None:
    names = {f"{600000 + i:06d}": f"样本{i}" for i in range(3000)}
    monkeypatch.setattr(free_source, "_name_map", lambda: names)

    def fake_fetch_many(symbols):
        data = {}
        for index, symbol in enumerate(symbols):
            fields = _tencent_fields(symbol[-6:])
            fields[1] = names[symbol[-6:]]
            if index == 0:
                fields[3], fields[32], fields[33] = "11.00", "10.00", "11.00"
            elif index == 1:
                fields[3], fields[32], fields[33] = "9.00", "-10.00", "10.00"
            data[symbol] = fields
        return data

    monkeypatch.setattr(tencent_source, "_fetch_many", fake_fetch_many)
    result = tencent_source.market_zt_scan.__wrapped__()

    assert result is not None
    assert result["limit_up"] == 1
    assert result["lu_list"][0]["code"] == "600000"
    assert result["limit_down"] == 1
    assert result["ld_list"] == [{
        "code": "600001",
        "name": "样本1",
        "price": 9.0,
        "change_pct": -10.0,
        "amount": 2_345_000.0,
        "turnover_rate": 2.5,
        "first_time": None,
        "seal_amount": None,
        "open_times": None,
    }]


def test_screener_uses_free_quote_date_and_requires_80_percent_coverage(monkeypatch) -> None:
    monkeypatch.setattr(free_source, "quote_data_date", lambda: "2026-07-13")
    assert screener._resolve_trade_day() == "20260713"

    universe = {
        f"{i:06d}": {"name": f"股票{i}", "industry": None, "board": "主板"}
        for i in range(3000)
    }
    quote = {
        "name": "示例",
        "price": 10.0,
        "change_pct": 1.0,
        "turnover_rate": 2.0,
        "volume_ratio": 1.2,
        "pe": 12.0,
        "pb": 1.5,
        "total_mv": 2_000_000_000,
        "float_mv": 1_000_000_000,
        "amount": 100_000_000,
    }
    monkeypatch.setattr(screener, "_stock_basic_map", lambda: universe)

    # 79.97% 不能再沿用旧的“超过 2500 只即完整”宽松口径。
    monkeypatch.setattr(
        tencent_source,
        "quotes",
        lambda _codes: {code: quote for code in list(universe)[:2399]},
    )
    insufficient = screener._market_snapshot.__wrapped__("20260713")
    assert insufficient["ok"] is False
    assert insufficient["trade_date"] == "2026-07-13"
    assert insufficient["coverage"] == {
        "received": 2399,
        "expected": 3000,
        "ratio": 0.7997,
        "percent": 79.97,
        "minimum_ratio": 0.8,
        "sufficient": False,
    }
    assert any("2399/3000" in item and "最低要求80%" in item for item in insufficient["missing"])

    # 恰好 80% 达到门禁，同时仍显式报告部分缺失和实际覆盖率。
    monkeypatch.setattr(
        tencent_source,
        "quotes",
        lambda _codes: {code: quote for code in list(universe)[:2400]},
    )
    sufficient = screener._market_snapshot.__wrapped__("20260713")
    assert sufficient["ok"] is True
    assert sufficient["coverage"]["sufficient"] is True
    assert sufficient["coverage"]["percent"] == 80.0
    assert any("2400/3000" in item and "已达到80%" in item for item in sufficient["missing"])


def test_screener_propagates_coverage_and_enriches_only_returned_candidates(monkeypatch) -> None:
    coverage = {
        "received": 2000,
        "expected": 3000,
        "ratio": 0.6667,
        "percent": 66.7,
        "minimum_ratio": 0.8,
        "sufficient": False,
    }
    monkeypatch.setattr(
        screener,
        "_market_snapshot",
        lambda _date: {
            "ok": False,
            "rows": [],
            "missing": ["覆盖不足"],
            "trade_date": "2026-07-13",
            "coverage": coverage,
            "universe": {
                "count": 3000, "complete": False,
                "refresh_required": True, "missing_exchanges": ["bse"],
            },
        },
    )
    failed = screener.screen()
    assert failed["ok"] is False
    assert failed["coverage"] == coverage
    assert failed["universe"]["missing_exchanges"] == ["bse"]
    assert failed["stocks"] == []

    calls: list[str] = []

    def fake_industry(code: str) -> str:
        calls.append(code)
        return "电子"

    monkeypatch.setattr(free_source, "industry", fake_industry)
    screener._INDUSTRY_CACHE.clear()
    candidates = [{"code": f"{i:06d}", "industry": None} for i in range(20)]
    stats = screener._enrich_candidate_industries(candidates)

    assert stats["queried"] == screener.CANDIDATE_INDUSTRY_LIMIT
    assert len(calls) == screener.CANDIDATE_INDUSTRY_LIMIT
    assert stats["filled"] == screener.CANDIDATE_INDUSTRY_LIMIT
    assert all(row["industry"] == "电子" for row in candidates[:screener.CANDIDATE_INDUSTRY_LIMIT])
    assert all(row["industry"] is None for row in candidates[screener.CANDIDATE_INDUSTRY_LIMIT:])

    # 同一榜单的下一次请求从缓存恢复前 12 只，并继续补后 8 只；不会永久卡在榜首。
    next_page = [{"code": f"{i:06d}", "industry": None} for i in range(20)]
    next_stats = screener._enrich_candidate_industries(next_page)
    assert next_stats["queried"] == 8
    assert next_stats["filled"] == 20
    assert len(calls) == 20
    screener._INDUSTRY_CACHE.clear()


def test_tencent_bulk_failure_is_bounded_and_opens_short_circuit(monkeypatch) -> None:
    calls: list[list[str]] = []

    def empty_fetch(symbols: list[str]) -> dict:
        calls.append(symbols)
        return {}

    monkeypatch.setattr(tencent_source, "_fetch", empty_fetch)
    monkeypatch.setattr(tencent_source, "_record_source", lambda *_args: None)
    symbols = [f"sz{i:06d}" for i in range(1800)]  # 30 批，足以证明不能全部跑完

    tencent_source._bulk_breaker_reset()
    try:
        assert tencent_source._fetch_many(symbols) == {}
        # 初始在途 10 批，加上达到连续失败阈值前可能补交的少量批次；绝不能跑完 30 批。
        assert len(calls) <= (
            tencent_source._BULK_MAX_WORKERS + tencent_source._BULK_FAILURE_LIMIT
        )
        opened, reason = tencent_source._bulk_breaker_open()
        assert opened is True
        assert "严重批量失败" in reason

        before = len(calls)
        assert tencent_source._fetch_many(symbols) == {}
        assert len(calls) == before  # 冷却期直接返回，不能再打一轮全市场请求
    finally:
        tencent_source._bulk_breaker_reset()


def test_free_industry_flow_converts_yi_yuan_exactly_once(monkeypatch) -> None:
    assert free_source._numeric_amount_to_yuan(2.5, numeric_unit="yi") == pytest.approx(250_000_000)
    assert free_source._numeric_amount_to_yuan("2.5亿", numeric_unit="yi") == pytest.approx(250_000_000)
    assert free_source._numeric_amount_to_yuan("-3100万", numeric_unit="yi") == pytest.approx(-31_000_000)
    monkeypatch.setattr(
        free_source.akshare_isolated,
        "industry_fund_flow",
        lambda **_kwargs: {
            "ok": True,
            "rows": [
                {"name": "光模块", "net_raw": 2.5, "change_pct": 3.2},
                {"name": "银行", "net_raw": -1.25, "change_pct": -0.4},
            ],
            "error": None,
            "latency_ms": 12.0,
        },
    )

    rows = free_source.moneyflow_ind_dc.__wrapped__()

    assert rows[0]["name"] == "光模块"
    assert rows[0]["main_net"] == pytest.approx(250_000_000)  # 亿元→元，且只转一次
    assert rows[1]["main_net"] == pytest.approx(-125_000_000)
    assert [row["rnk"] for row in rows] == [1, 2]


def test_free_financial_mapping_and_failure_contract(monkeypatch) -> None:
    frame = pd.DataFrame(
        [
            {
                "日期": "2026-03-31",
                "净资产收益率(%)": 8.6,
                "销售毛利率(%)": 31.2,
                "净利润增长率(%)": 18.4,
                "主营业务收入增长率(%)": 12.7,
                "资产负债率(%)": 42.1,
            }
        ]
    )
    fake_ak = SimpleNamespace(stock_financial_analysis_indicator=lambda **_kwargs: frame)
    monkeypatch.setattr(free_source._tdx_research, "basic_financial", lambda _code: None)
    monkeypatch.setattr(free_source, "get_ak", lambda: fake_ak)
    monkeypatch.setattr(free_source, "safe", lambda *_args, **_kwargs: frame)

    result = free_source.fina_indicator.__wrapped__("600001")

    assert result == {
        "end_date": "20260331",
        "roe": 8.6,
        "grossprofit_margin": 31.2,
        "netprofit_yoy": 18.4,
        "or_yoy": 12.7,
        "debt_to_assets": 42.1,
        "source": "新浪财务分析",
    }

    monkeypatch.setattr(free_source, "safe", lambda *_args, **_kwargs: None)
    assert free_source.fina_indicator.__wrapped__("600002") is None


def test_free_financial_prefers_tdx_and_skips_public_backup(monkeypatch) -> None:
    primary = {"data_date": "2026-07-15", "roe": 8.8, "source": "TDX stock_info"}
    monkeypatch.setattr(free_source._tdx_research, "basic_financial", lambda _code: primary)
    monkeypatch.setattr(
        free_source,
        "get_ak",
        lambda: (_ for _ in ()).throw(AssertionError("TDX 成功后不得请求公开备源")),
    )

    assert free_source.fina_indicator.__wrapped__("600519") == primary


def test_free_limit_pool_distinguishes_empty_market_from_source_failure(monkeypatch) -> None:
    frame = pd.DataFrame(
        [
            {
                "代码": "1",
                "名称": "示例股",
                "最新价": 11.0,
                "涨跌幅": 10.0,
                "连板数": 2,
                "炸板次数": 1,
                "封板资金": 345_000_000,
                "成交额": 980_000_000,
                "换手率": 6.8,
                "首次封板时间": 93012,
                "最后封板时间": 145501,
                "所属行业": "电子",
            }
        ]
    )
    monkeypatch.setattr(free_source, "_ak_limit_frame", lambda *_args: frame)

    rows = free_source.limit_list.__wrapped__("20260713", "U")

    assert rows is not None and len(rows) == 1
    assert rows[0]["code"] == "000001"
    assert rows[0]["lianban"] == 2
    assert rows[0]["seal_amount"] == pytest.approx(345_000_000)
    assert rows[0]["amount"] == pytest.approx(980_000_000)
    assert rows[0]["first_time"] == "09:30:12"
    assert rows[0]["source"] == "公开涨跌停池"

    # 真实空集是 []；上游失败是 None。两者不得混为“今日 0 家”。
    monkeypatch.setattr(free_source, "_ak_limit_frame", lambda *_args: pd.DataFrame())
    assert free_source.limit_list.__wrapped__("20260714", "U") == []

    monkeypatch.setattr(free_source, "_ak_limit_frame", lambda *_args: None)
    assert free_source.limit_list.__wrapped__("20260715", "U") is None


def test_auction_is_explicitly_unavailable_without_a_dedicated_source(monkeypatch) -> None:
    monkeypatch.setattr(
        free_source.tc,
        "quote",
        lambda _code: pytest.fail("不得用普通实时行情伪造集合竞价"),
    )
    assert free_source.auction.__wrapped__("600004") is None
    assert free_source.auction_oc.__wrapped__("600004") is None


def test_forecast_parser_preserves_loss_and_decline_signs(monkeypatch) -> None:
    frame = pd.DataFrame([
        {
            "股票代码": "601127", "股票简称": "赛力斯",
            "预测指标": "归属于上市公司股东的净利润", "预告类型": "首亏",
            "业绩变动": "预计净利润亏损:150,000万元至180,000万元。",
            "业绩变动幅度": -156.1, "预测数值": -1_650_000_000,
            "上年同期值": 2_941_000_000, "公告日期": "2026-07-13",
        },
        {
            "股票代码": "603920", "股票简称": "世运电路",
            "预测指标": "归属于上市公司股东的净利润", "预告类型": "预减",
            "业绩变动": "预计净利润盈利:6,700万元至10,000万元,同比上年下降:73.97%至82.56%,同比降低28,413.48万元。",
            "业绩变动幅度": -78.265, "预测数值": 83_500_000,
            "上年同期值": 384_134_800, "公告日期": "2026-07-11",
        },
        {
            "股票代码": "603693", "股票简称": "江苏新能",
            "预测指标": "归属于上市公司股东的净利润", "预告类型": "预减",
            "业绩变动": "预计净利润盈利约:13,300万元,同比上年下降:53%左右,同比降低14,885万元。",
            "业绩变动幅度": -53.0, "预测数值": 133_000_000,
            "上年同期值": 281_854_100, "公告日期": "2026-07-11",
        },
    ])
    monkeypatch.setattr(free_source, "safe", lambda *_args, **_kwargs: frame)
    monkeypatch.setattr(
        free_source,
        "get_ak",
        lambda: SimpleNamespace(stock_yjyg_em=lambda **_kwargs: frame),
    )

    rows = {row["code"]: row for row in free_source.forecast_list.__wrapped__("20260630")}

    assert rows["601127"]["net_min"] == pytest.approx(-1_800_000_000)
    assert rows["601127"]["net_max"] == pytest.approx(-1_500_000_000)
    assert rows["603920"]["p_chg_min"] == pytest.approx(-82.56)
    assert rows["603920"]["p_chg_max"] == pytest.approx(-73.97)
    assert rows["603693"]["net_min"] == rows["603693"]["net_max"] == pytest.approx(133_000_000)


def test_ths_limit_reason_respects_page_limit_and_china_timezone(monkeypatch) -> None:
    captured: dict[str, str] = {}
    epoch = int(datetime(2026, 7, 10, 14, 56, 40, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())

    def fake_json(url: str, **_kwargs):
        captured["url"] = url
        return {"data": {"page": {"total": 1}, "info": [{
            "code": "000001", "name": "平安银行", "first_limit_up_time": epoch,
            "reason_type": "银行", "high_days": "2天2板", "limit_up_type": "换手板",
            "limit_up_suc_rate": 0.75, "order_amount": 12_000_000,
        }]}}

    monkeypatch.setattr(free_source, "_direct_json", fake_json)
    pool = free_source.ths_limit_up_pool.__wrapped__("20260710")
    reasons = {row["code"]: row for row in pool["stocks"]}
    query = parse_qs(urlsplit(captured["url"]).query)

    assert query["limit"] == ["200"]
    assert reasons["000001"]["first_time"] == "14:56"
    assert reasons["000001"]["suc_rate_pct"] == 75.0
    assert reasons["000001"]["high_days"] == "2天2板"
    assert reasons["000001"]["seal_amount"] == 12_000_000


def test_zero_open_count_and_amount_units_are_not_lost_or_doubled(monkeypatch) -> None:
    frame = pd.DataFrame([{
        "代码": "1", "名称": "零炸板", "连板数": 1, "炸板次数": 0,
        "封板资金": 0, "成交额": 1_000_000, "所属行业": "测试",
    }])
    monkeypatch.setattr(free_source, "_ak_limit_frame", lambda *_args: frame)

    row = free_source.limit_list.__wrapped__("20260713", "U")[0]

    assert row["open_times"] == 0
    assert row["seal_amount"] == 0
    assert free_source._numeric_amount_to_yuan("5.23亿", numeric_unit="yi") == pytest.approx(523_000_000)
    assert market._ths_net_to_yuan("-3100万") == pytest.approx(-31_000_000)


def test_moneyflow_both_uses_full_tail(monkeypatch) -> None:
    rows = [
        {"code": "1", "main_net": 20, "trade_date": "2026-07-10"},
        {"code": "2", "main_net": -5, "trade_date": "2026-07-10"},
        {"code": "3", "main_net": -99, "trade_date": "2026-07-10"},
    ]
    requested: list[int | None] = []

    def fake_market(top):
        requested.append(top)
        return rows

    monkeypatch.setattr(free_source, "moneyflow_market", fake_market)
    result = free_source.moneyflow_market_both.__wrapped__(1)

    assert requested == [None]
    assert result["inflow"][0]["code"] == "1"
    assert result["outflow"][0]["code"] == "3"


def test_stock_universe_last_good_cache_is_customer_scoped_and_expires(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path))
    names = {str(600000 + i): f"样本{i}" for i in range(3000)}

    free_source._save_universe_cache(names)

    assert free_source._universe_cache_path().is_relative_to(tmp_path)
    assert free_source._load_universe_cache() == names

    path = free_source._universe_cache_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["saved_at_epoch"] = 1
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert free_source._load_universe_cache() is None


def test_free_http_failure_is_reported_and_never_becomes_success(monkeypatch) -> None:
    class BrokenOpener:
        def open(self, *_args, **_kwargs):
            raise TimeoutError("public source timed out")

    events: list[tuple] = []
    monkeypatch.setattr(free_source, "_DIRECT", BrokenOpener())
    monkeypatch.setattr(
        free_source,
        "_record_source",
        lambda *args: events.append(args),
    )

    result = free_source._direct_json(
        "https://invalid.example/free-data",
        label="免费源断网测试",
        timeout=0,
    )

    assert result is None
    assert len(events) == 1
    label, _latency, ok, mode, error = events[0]
    assert label == "免费源断网测试"
    assert ok is False
    assert mode == "direct"
    assert "timed out" in error


def test_verified_industry_uses_only_tdx_relation(monkeypatch) -> None:
    monkeypatch.setattr(
        free_source._tdx_research,
        "relations_for_codes",
        lambda codes: {
            "123456": {
                "industry": {"name": "银行"},
                "source": "TDX relation",
            }
        } if "123456" in codes else {},
    )
    monkeypatch.setattr(
        free_source,
        "_direct_json",
        lambda *_args, **_kwargs: pytest.fail("行业核验不得请求公开网页回退"),
    )

    assert free_source.verified_industry("123456") == "银行"
    assert free_source.verified_industry("123457") is None


def test_tushare_is_not_an_active_runtime_or_install_dependency() -> None:
    # 核心消费者必须统一指向免费兼容层，不能因环境里恰好有 token 而悄悄切回付费源。
    assert market.tx is free_source
    assert stock.tx is free_source
    assert routes.public_data is free_source
    assert diagnosis.tx is free_source
    assert forecast_mining.tx is free_source
    assert screener.tx is free_source

    offenders: list[str] = []
    for base in (ROOT / "hermes_trader",):
        for path in base.rglob("*.py"):
            # 旧适配器可留作迁移参考，但任何活动模块都不得再 import 它。
            if path.name in {"tushare_source.py", "tdx_mcp_source.py"}:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "tushare" or alias.name.endswith(".tushare_source"):
                            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for alias in node.names:
                        if (
                            module == "tushare"
                            or module.endswith(".tushare_source")
                            or alias.name == "tushare_source"
                        ):
                            offenders.append(
                                f"{path.relative_to(ROOT)}:{node.lineno}:{module}:{alias.name}"
                            )
    assert offenders == []

    active_requirements = [
        line.strip().lower()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(line.startswith("tushare") for line in active_requirements)
