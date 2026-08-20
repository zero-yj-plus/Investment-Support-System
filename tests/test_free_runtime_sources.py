from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pandas as pd
import pytest

from hermes_trader.analysis import timing
from hermes_trader.data import (
    akshare_isolated,
    capital_tracker,
    free_source,
    mainforce_source,
    market,
    tencent_source,
)
from hermes_trader.data.akshare_worker import RESULT_PREFIX


def test_market_quality_separates_source_notes_from_decision_availability() -> None:
    available = market._with_quality({"missing": ["数据取自免费公开源"]})
    assert available["data_state"] == "complete"
    assert available["state_label"] == "决策可用"
    assert available["missing_fields"] == []
    assert available["notes"] == ["数据取自免费公开源"]

    enhanced = market._with_quality(
        {"missing": ["北向资金实时数据已停止公布"]},
        degraded_factors=["大盘主力净流入"],
    )
    assert enhanced["data_state"] == "degraded"
    assert enhanced["state_label"] == "主要数据可用"

    unavailable = market._with_quality(
        {"missing": ["上游未返回排名"]},
        missing_fields=["行业资金流"],
    )
    assert unavailable["data_state"] == "missing"
    assert unavailable["state_label"] == "暂不可用"


def _kline_payload(*, key: str = "qfqday") -> str:
    payload = {
        "code": 0,
        "msg": "",
        "data": {
            "sz000001": {
                key: [
                    ["2026-07-09", "10.55", "10.49", "10.59", "10.43", "747191.00", {}, "0.38", "78321.45"],
                    ["2026-07-10", "10.50", "10.45", "10.51", "10.40", "957320.00", {}, "0.49", "100123.45"],
                ]
            }
        },
    }
    return "kline_dayqfq=" + json.dumps(payload)


def test_tencent_daily_parser_preserves_ohlc_and_sixth_column() -> None:
    rows = tencent_source._parse_daily_kline_payload(
        _kline_payload(), "sz000001", adjust="qfq", fetched_at="2026-07-12T12:00:00+08:00"
    )

    assert rows[-1] == {
        "d": "2026-07-10",
        "o": 10.5,
        "c": 10.45,
        "h": 10.51,
        "l": 10.4,
        "v": 957_320.0,  # 腾讯数组第 6 列，没有×100，也没有换成成交额
        "v_shares": 95_732_000.0,
        "amount": 1_001_234_500.0,
        "source": "腾讯财经·前复权日K",
        "as_of": "2026-07-10",
        "fetched_at": "2026-07-12T12:00:00+08:00",
        "adjustment": "qfq",
        "volume_unit": "手",
        "amount_unit": "元",
    }
    # 请求前复权时，未复权 day 不能被当成 qfqday。
    assert tencent_source._parse_daily_kline_payload(
        _kline_payload(key="day"), "sz000001", adjust="qfq"
    ) == []


def test_tencent_daily_parser_normalizes_main_board_and_star_volume_units() -> None:
    payload = {
        "code": 0,
        "data": {
            "sh688001": {
                "qfqday": [[
                    "2026-07-20", "55.76", "47.80", "56.96", "45.89",
                    "14234671.00", {}, "3.02", "70315.99", "",
                ]],
            },
        },
    }
    row = tencent_source._parse_daily_kline_payload(
        "kline_dayqfq=" + json.dumps(payload), "sh688001", adjust="qfq"
    )[0]

    assert row["volume_unit"] == "股"
    assert row["v_shares"] == 14_234_671
    assert row["amount"] == 703_159_900
    assert row["amount"] / row["v_shares"] == pytest.approx(49.3977, rel=1e-4)


def test_free_and_timing_daily_paths_use_pure_tencent_adapter(monkeypatch) -> None:
    rows = tencent_source._parse_daily_kline_payload(
        _kline_payload(), "sz000001", adjust="qfq", fetched_at="2026-07-12T12:00:00+08:00"
    )
    calls: list[tuple[str, int, str]] = []

    def fake_daily(code: str, days: int, *, adjust: str):
        calls.append((code, days, adjust))
        return rows

    monkeypatch.setattr(tencent_source, "stock_daily_bars", fake_daily)
    assert not hasattr(free_source, "tdx")

    assert free_source.daily_bars.__wrapped__("000001.SZ", 2) == rows
    assert timing._stock_bars.__wrapped__("000001", 2) == rows
    assert calls == [("000001", 2, "qfq"), ("000001", 2, "qfq")]


def test_qfq_contract_never_falls_back_to_unadjusted_tdx(monkeypatch) -> None:
    monkeypatch.setattr(tencent_source, "stock_daily_bars", lambda *_args, **_kwargs: [])
    assert not hasattr(free_source, "tdx")

    assert free_source.daily_bars.__wrapped__("000001", 120) == []
    assert timing._stock_bars.__wrapped__("000001", 120) is None


def test_isolated_worker_protocol_ignores_progress_noise(monkeypatch) -> None:
    payload = {
        "ok": True,
        "rows": [{"name": "银行", "net_raw": 1.25, "change_pct": 0.4}],
        "error": None,
        "schema": "ths-industry-flow.v1",
        "provider_version": "1.18.64",
    }
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="50%|progress\n" + RESULT_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n",
        stderr="progress only",
    )
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return completed

    monkeypatch.setattr(akshare_isolated.subprocess, "run", fake_run)
    result = akshare_isolated.industry_fund_flow(timeout=3)

    assert result["ok"] is True
    assert result["rows"] == payload["rows"]
    assert seen["command"][1:] == ["-m", "hermes_trader.data.akshare_worker", "industry-flow"]
    assert "shell" not in seen["kwargs"]
    assert "env" in seen["kwargs"]
    assert seen["kwargs"]["encoding"] == "utf-8"
    assert seen["kwargs"]["errors"] == "replace"
    assert not any(
        token in key.lower()
        for key in seen["kwargs"]["env"]
        for token in ("token", "secret", "password", "webhook", "api_key")
    )


def test_industry_summary_worker_uses_separate_protocol(monkeypatch) -> None:
    payload = {
        "ok": True,
        "rows": [{
            "name": "医疗服务", "amount_raw": 321.5, "net_raw": 8.2,
            "change_pct": 5.9, "up_count": 53, "down_count": 3,
            "leader_name": "示例股", "leader_change_pct": 10.0,
        }],
        "error": None,
        "schema": "ths-industry-summary.v1",
        "provider_version": "1.18.64",
    }
    seen = {}

    def fake_run(command, **_kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(
            args=command, returncode=0,
            stdout=RESULT_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n",
            stderr="",
        )

    akshare_isolated._reset_industry_summary_breaker()
    monkeypatch.setattr(akshare_isolated.subprocess, "run", fake_run)
    result = akshare_isolated.industry_summary(timeout=3)

    assert result["ok"] is True
    assert result["rows"] == payload["rows"]
    assert seen["command"][1:] == [
        "-m", "hermes_trader.data.akshare_worker", "industry-summary",
    ]
    akshare_isolated._reset_industry_summary_breaker()


def test_isolated_worker_env_keeps_windows_runtime_without_secrets(monkeypatch) -> None:
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("WINDIR", r"C:\Windows")
    monkeypatch.setenv("ACCESS_TOKEN", "must-not-leak")

    env = akshare_isolated._worker_env()

    assert env["SystemRoot"] == r"C:\Windows"
    assert env["WINDIR"] == r"C:\Windows"
    assert "ACCESS_TOKEN" not in env


def test_market_flow_worker_uses_its_own_protocol_and_breaker(monkeypatch) -> None:
    payload = {
        "ok": True,
        "rows": [{
            "code": "000001", "name": "平安银行", "net_raw": "1.25亿",
            "change_pct": "1.2%", "direction": "inflow",
        }],
        "error": None,
        "schema": "ths-market-fund-flow.v1",
        "provider_version": "1.18.64",
    }
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(
            args=command, returncode=0,
            stdout=RESULT_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n",
            stderr="",
        )

    akshare_isolated._reset_market_breaker()
    monkeypatch.setattr(akshare_isolated.subprocess, "run", fake_run)
    result = akshare_isolated.market_fund_flow(timeout=3)

    assert result["ok"] is True
    assert result["rows"] == payload["rows"]
    assert seen["command"][1:] == [
        "-m", "hermes_trader.data.akshare_worker", "market-fund-flow",
    ]
    akshare_isolated._reset_market_breaker()


def test_moneyflow_market_prefers_bounded_isolated_double_ended_rank(monkeypatch) -> None:
    records: list[tuple] = []
    monkeypatch.setattr(
        free_source.akshare_isolated,
        "market_fund_flow",
        lambda **_kwargs: {
            "ok": True,
            "latency_ms": 12,
            "rows": [
                {"code": "1", "name": "流入一", "net_raw": "2.5亿", "change_pct": "1.2%", "direction": "inflow"},
                {"code": "2", "name": "流出一", "net_raw": "-8000万", "change_pct": "-2.0%", "direction": "outflow"},
            ],
        },
    )
    monkeypatch.setattr(free_source, "quote_data_date", lambda: "2026-07-16")
    monkeypatch.setattr(free_source, "_record_source", lambda *args: records.append(args))
    monkeypatch.setattr(
        free_source,
        "_fund_flow_rank_frame",
        lambda: pytest.fail("东财多页接口不应在同花顺单页成功后继续执行"),
    )

    rows = free_source.moneyflow_market.__wrapped__(None)

    assert [row["code"] for row in rows] == ["000001", "000002"]
    assert rows[0]["main_net"] == pytest.approx(250_000_000)
    assert rows[1]["main_net"] == pytest.approx(-80_000_000)
    assert all(row["trade_date"] == "2026-07-16" for row in rows)
    assert records[-1][0] == "个股资金流排名(同花顺隔离)"
    assert records[-1][2] is True


def test_isolated_worker_failure_is_bounded_and_redacted(monkeypatch) -> None:
    secret = "https://user:password@example.test/path?token=SECRET"
    payload = {"ok": False, "rows": [], "error": f"RuntimeError: {secret}"}
    monkeypatch.setattr(
        akshare_isolated.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=-6,
            stdout=RESULT_PREFIX + json.dumps(payload) + "\n",
            stderr=f"fatal {secret}",
        ),
    )
    crashed = akshare_isolated.industry_fund_flow(timeout=1)
    assert crashed["ok"] is False
    assert crashed["error"] == "worker exit=-6: RuntimeError"
    assert "SECRET" not in crashed["error"]
    assert "password" not in crashed["error"]

    akshare_isolated._reset_industry_breaker()

    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="worker", timeout=0.1)

    monkeypatch.setattr(akshare_isolated.subprocess, "run", timed_out)
    timeout = akshare_isolated.industry_fund_flow(timeout=0.1)
    assert timeout["ok"] is False
    assert timeout["error"] == "worker timeout>0.1s"
    # 一次重失败后进入冷却，下次不再重复卡满25s。
    blocked = akshare_isolated.industry_fund_flow(timeout=25)
    assert blocked["ok"] is False and "circuit open" in blocked["error"]
    akshare_isolated._reset_industry_breaker()


def test_free_industry_worker_failure_degrades_explicitly_without_main_v8(monkeypatch) -> None:
    records: list[tuple] = []
    call_args: dict = {}

    def fake_industry_fund_flow(**kwargs):
        call_args.update(kwargs)
        return {
            "ok": False,
            "rows": [],
            "error": "worker exit=-6: worker-error",
            "latency_ms": 9.0,
        }

    monkeypatch.setattr(
        free_source.akshare_isolated,
        "industry_fund_flow",
        fake_industry_fund_flow,
    )
    monkeypatch.setattr(free_source, "_record_source", lambda *args: records.append(args))
    monkeypatch.setattr(
        free_source,
        "get_ak",
        lambda: pytest.fail("industry flow must not import/call AkShare in main process"),
    )

    assert free_source.moneyflow_ind_dc.__wrapped__() == []
    assert records[-1][0] == "行业资金流(同花顺隔离)"
    assert records[-1][2] is False
    assert records[-1][3] == "isolated-subprocess"
    assert "worker exit=-6" in records[-1][4]
    assert call_args["timeout"] == 4
    assert call_args["trip_breaker"] is False


def test_short_interactive_timeout_does_not_poison_background_breaker(monkeypatch) -> None:
    akshare_isolated._reset_industry_breaker()
    calls = 0
    payload = {
        "ok": True,
        "rows": [{"name": "银行", "net_raw": 1.0, "change_pct": 0.2}],
        "schema": "ths-industry-flow.v1",
        "provider_version": "1.18.64",
    }

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired(cmd="worker", timeout=0.1)
        return subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=RESULT_PREFIX + json.dumps(payload) + "\n", stderr="",
        )

    monkeypatch.setattr(akshare_isolated.subprocess, "run", fake_run)
    fast = akshare_isolated.industry_fund_flow(timeout=0.1, trip_breaker=False)
    complete = akshare_isolated.industry_fund_flow(timeout=25, trip_breaker=True)

    assert fast["ok"] is False
    assert complete["ok"] is True
    assert calls == 2
    akshare_isolated._reset_industry_breaker()


def test_capital_background_uses_complete_industry_budget(monkeypatch) -> None:
    seen = {}

    def fake_flow(*_args, **kwargs):
        seen.update(kwargs)
        return [
            {"name": f"行业{i}", "main_net": float(8 - i), "change_pct": 0.2}
            for i in range(8)
        ]

    monkeypatch.setattr(free_source, "moneyflow_ind_dc", fake_flow)
    rows, source, _data_date = capital_tracker.collect_sectors(top=10)

    assert rows and source == "同花顺·公开源"
    assert seen == {"timeout": 25, "trip_breaker": True}


def test_industry_numeric_unit_shift_is_rejected_before_yi_multiplier(monkeypatch) -> None:
    records: list[tuple] = []
    monkeypatch.setattr(
        free_source.akshare_isolated,
        "industry_fund_flow",
        lambda **_kwargs: {
            "ok": True,
            "rows": [{"name": "银行", "net_raw": 269_600_000, "change_pct": 1.0}],
            "latency_ms": 1.0,
        },
    )
    monkeypatch.setattr(free_source, "_record_source", lambda *args: records.append(args))

    assert free_source.moneyflow_ind_dc.__wrapped__() == []
    assert any("单位/量级校验失败" in str(item[-1]) for item in records)


def test_industry_summary_normalizes_complete_structure_to_yuan(monkeypatch) -> None:
    raw_rows = [
        {
            "name": f"行业{i}", "amount_raw": 100.0 + i, "net_raw": 2.5,
            "change_pct": 1.2, "up_count": 18, "down_count": 2,
            "leader_name": f"领涨{i}", "leader_change_pct": 9.9,
        }
        for i in range(50)
    ]
    records: list[tuple] = []
    monkeypatch.setattr(
        free_source.akshare_isolated,
        "industry_summary",
        lambda **_kwargs: {"ok": True, "rows": raw_rows, "latency_ms": 8.0},
    )
    monkeypatch.setattr(free_source, "quote_data_date", lambda: "2026-08-20")
    monkeypatch.setattr(free_source, "_record_source", lambda *args: records.append(args))

    result = free_source.industry_summary_ths.__wrapped__()

    assert result["ok"] is True
    assert result["data_date"] == "2026-08-20"
    assert result["coverage"] == {"complete": 50, "total": 50}
    assert result["sectors"][0]["amount"] == pytest.approx(10_000_000_000)
    assert result["sectors"][0]["main_flow"] == pytest.approx(250_000_000)
    assert result["sectors"][0]["up_count"] == 18
    assert result["sectors"][0]["amount_unit"] == "CNY"
    assert records[-1][0] == "行业一览(同花顺隔离)"
    assert records[-1][2] is True


def test_industry_summary_rejects_numeric_unit_shift(monkeypatch) -> None:
    raw_rows = [
        {
            "name": f"行业{i}", "amount_raw": 12_000_000_000, "net_raw": 2.5,
            "change_pct": 1.2, "up_count": 18, "down_count": 2,
        }
        for i in range(50)
    ]
    monkeypatch.setattr(
        free_source.akshare_isolated,
        "industry_summary",
        lambda **_kwargs: {"ok": True, "rows": raw_rows, "latency_ms": 1.0},
    )
    monkeypatch.setattr(free_source, "_record_source", lambda *_args: None)

    result = free_source.industry_summary_ths.__wrapped__()

    assert result["ok"] is False
    assert "单位或量级校验失败" in result["error"]


def test_market_and_capital_callers_consume_yuan_contract(monkeypatch) -> None:
    industry_rows = [
        {"name": "银行", "main_net": 250_000_000.0, "change_pct": 1.2, "unit": "CNY"},
        {"name": "医药", "main_net": -125_000_000.0, "change_pct": -0.3, "unit": "CNY"},
    ]
    monkeypatch.setattr(free_source, "moneyflow_ind_dc", lambda *_args, **_kwargs: industry_rows)

    assert capital_tracker._ths_sectors_direct() == [
        {"name": "银行", "main_flow": 250_000_000.0, "change_pct": 1.2,
         "value_unit": "CNY", "unit_verified": True},
        {"name": "医药", "main_flow": -125_000_000.0, "change_pct": -0.3,
         "value_unit": "CNY", "unit_verified": True},
    ]

    fake_ak = SimpleNamespace(stock_sector_spot=lambda **_kwargs: None)
    monkeypatch.setattr(market, "get_ak", lambda: fake_ak)
    monkeypatch.setattr(market, "_use_eastmoney", lambda: False)
    monkeypatch.setattr(mainforce_source, "industry", lambda **_kwargs: {})
    monkeypatch.setattr(
        free_source,
        "industry_summary_ths",
        lambda *_args, **_kwargs: {"ok": False, "sectors": [], "error": "测试降级"},
    )

    # market_flow 的子进程适配契约：main_net 已是元，不再×1e8。
    flow = market._market_flow_raw()
    assert flow["main_flow_sectors"] == [
        {"name": "银行", "main_net": 250_000_000.0, "change_pct": 1.2},
        {"name": "医药", "main_net": -125_000_000.0, "change_pct": -0.3},
    ]

    industry_frame = pd.DataFrame(
        [{"板块": "银行", "涨跌幅": 1.2, "股票名称": "平安银行", "个股-涨跌幅": 2.0, "总成交额": 9e9}]
    )

    def fake_safe(_fn, *_args, **kwargs):
        if kwargs.get("indicator") == "新浪行业":
            return industry_frame
        return pd.DataFrame()

    monkeypatch.setattr(market, "safe", fake_safe)
    sectors = market.market_sectors.__wrapped__()
    bank = next(row for row in sectors["sectors"] if row["kind"] == "industry")
    assert bank["name"] == "银行"
    assert bank["main_flow"] == 250_000_000.0


def test_market_sectors_uses_ths_summary_when_tdx_has_no_amount(monkeypatch) -> None:
    monkeypatch.setattr(
        market.tdxapi,
        "industry_market",
        lambda: {
            "data_date": "2026-08-20",
            "sectors": [
                {
                    "kind": "industry", "name": f"TDX行业{i}", "change_pct": 1.0,
                    "amount": None, "up_count": 10, "down_count": 2,
                }
                for i in range(60)
            ],
            "coverage": {},
        },
    )
    summary_rows = [
        {
            "kind": "industry", "name": "医疗服务", "change_pct": 5.9,
            "amount": 32_100_000_000, "main_flow": 820_000_000,
            "up_count": 53, "down_count": 3, "leader_name": "示例股",
            "leader_change_pct": 10.0, "source": "同花顺行业一览表(AkShare隔离)",
        }
    ]
    monkeypatch.setattr(market, "get_ak", lambda: None)
    monkeypatch.setattr(
        free_source,
        "industry_summary_ths",
        lambda *_args, **_kwargs: {
            "ok": True, "data_date": "2026-08-20", "sectors": summary_rows,
            "coverage": {"complete": 90, "total": 90},
        },
    )
    monkeypatch.setattr(
        mainforce_source,
        "industry",
        lambda **_kwargs: pytest.fail("完整行业一览成功后不应调用残缺兜底"),
    )
    monkeypatch.setattr(
        free_source,
        "moneyflow_ind_dc",
        lambda *_args, **_kwargs: pytest.fail("完整行业一览成功后不应重复调用资金流"),
    )

    result = market.market_sectors.__wrapped__()
    industries = [row for row in result["sectors"] if row["kind"] == "industry"]

    assert industries == summary_rows
    assert result["data_date"] == "2026-08-20"
    assert result["coverage"]["tdx_industry"]["amount_complete"] == 0
    assert result["coverage"]["industry_summary"] == {"complete": 90, "total": 90}
    assert any("TDX行业榜未进入主线判定" in note for note in result["notes"])


def test_market_sectors_keeps_tdx_when_core_fields_are_complete(monkeypatch) -> None:
    tdx_rows = [
        {
            "kind": "industry", "name": f"TDX行业{i}", "change_pct": 1.0,
            "amount": 10_000_000_000 + i, "main_flow": None,
            "up_count": 10, "down_count": 2,
        }
        for i in range(60)
    ]
    monkeypatch.setattr(
        market.tdxapi,
        "industry_market",
        lambda: {"data_date": "2026-08-20", "sectors": tdx_rows},
    )
    monkeypatch.setattr(market, "get_ak", lambda: None)
    monkeypatch.setattr(
        free_source,
        "industry_summary_ths",
        lambda *_args, **_kwargs: pytest.fail("TDX核心字段完整时不应切换行业分类"),
    )

    result = market.market_sectors.__wrapped__()
    industries = [row for row in result["sectors"] if row["kind"] == "industry"]

    assert industries == tdx_rows
    assert result["data_date"] == "2026-08-20"
    assert result["coverage"]["tdx_industry"] == {
        "rows": 60, "amount_complete": 60, "breadth_complete": 60,
    }


def test_market_top_uses_tdx_full_market_snapshot_before_limitup_fallback(monkeypatch) -> None:
    frame = {
        "stocks": {
            "000001": {"code": "000001", "price": 10.5, "change_pct": 5.0},
            "600030": {"code": "600030", "price": 30.0, "change_pct": 8.0},
        },
        "count": 5528,
        "data_date": "2026-07-13",
        "source": "TDX pricevol",
    }
    monkeypatch.setattr(market.tdxapi, "pricevol_snapshot", lambda: frame)
    monkeypatch.setattr(
        market.tdxapi,
        "stock_universe",
        lambda: {"000001": "平安银行", "600030": "中信证券"},
    )
    monkeypatch.setattr(
        market.tdxapi,
        "quotes",
        lambda *_args, **_kwargs: {
            "600030": {
                "name": "中信证券",
                "price": 30.1,
                "change_pct": 8.36,
                "amount": 2_000_000_000,
                "speed": 0.2,
            }
        },
    )
    monkeypatch.setattr(
        market,
        "market_limitup",
        lambda: pytest.fail("TDX full-market snapshot must prevent the limit-up-pool fallback"),
    )

    result = market.market_top.__wrapped__(2)

    assert result["data_state"] == "degraded"
    assert result["data_date"] == "2026-07-13"
    assert result["stocks"][0] == {
        "code": "600030",
        "name": "中信证券",
        "price": 30.1,
        "change_pct": 8.36,
        "amount": 2_000_000_000,
        "turnover_rate": None,
        "speed": 0.2,
    }
    assert result["coverage"]["market_snapshot"] == {"complete": 5528, "total": 5528}
    assert result["coverage"]["quote_enrichment"] == {"complete": 1, "total": 2}
    assert not any("涨停池口径近似" in note for note in result["missing"])
