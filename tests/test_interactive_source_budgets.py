from __future__ import annotations

import ast
from pathlib import Path

from hermes_trader.analysis import timing
from hermes_trader.data import deriv_source, market, xverify


ROOT = Path(__file__).resolve().parents[1]


def test_production_package_has_no_local_tdx_runtime_or_dependency() -> None:
    assert not (ROOT / "hermes_trader/data/tdx_source.py").exists()
    assert not (ROOT / "hermes_trader/data/tdx_mcp_source.py").exists()
    assert not (ROOT / "hermes_trader/data/tushare_source.py").exists()
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "opentdx" not in requirements
    prohibited = {"opentdx", "tdx_source", "tdx_mcp_source", "tushare_source"}
    offenders: list[str] = []
    for path in (ROOT / "hermes_trader").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or "", *(alias.name for alias in node.names)]
            else:
                continue
            for name in names:
                leaf = name.rsplit(".", 1)[-1]
                if leaf in prohibited:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
    assert offenders == []


def test_xverify_prefers_fast_tdx_api_and_tencent_without_local_tdx(monkeypatch) -> None:
    monkeypatch.setattr(
        xverify.tdx_api, "quote",
        lambda code, **kwargs: {"code": code, "name": "贵州茅台", "price": 1214.88, "change_pct": 0.32},
    )
    monkeypatch.setattr(
        xverify.tc, "quote",
        lambda _code: {"name": "贵州茅台", "price": 1214.87, "change_pct": 0.32},
    )
    monkeypatch.setattr(
        xverify, "_sina_stock_price",
        lambda _code: (_ for _ in ()).throw(AssertionError("两个快源已齐，不应再请求兜底")),
    )

    result = xverify.xverify_quote.__wrapped__("600519")

    assert result["ok"] is True
    assert result["confidence"] == "high"
    assert result["sources"] == ["TDX API", "腾讯"]


def test_multiperiod_keeps_rows_but_stops_history_enrichment_at_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        market,
        "market_top",
        lambda _n: {
            "stocks": [
                {"code": "000001", "name": "A", "change_pct": 1.0},
                {"code": "000002", "name": "B", "change_pct": 2.0},
                {"code": "000003", "name": "C", "change_pct": 3.0},
            ],
            "missing": [],
            "data_date": "2026-07-15",
        },
    )
    monkeypatch.setattr(market.tdxapi, "industry_for_codes", lambda _codes: {})
    monkeypatch.setattr(
        market,
        "_recent_closes_dated",
        lambda _code, count=30: [(f"202607{i:02d}", 10.0 + i) for i in range(1, 16)],
    )
    ticks = iter([0.0, 0.0, 9.0, 9.0])
    monkeypatch.setattr(market.time, "monotonic", lambda: next(ticks, 9.0))

    result = market.market_multiperiod.__wrapped__(3)

    assert len(result["stocks"]) == 3
    assert result["stocks"][0]["d3"] is not None
    assert result["stocks"][1]["d3"] is None
    assert any("8 秒交互预算" in note for note in result["missing"])


def test_futures_overview_stops_remaining_contracts_at_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        deriv_source,
        "_main_daily_quote",
        lambda item: {"symbol": item["symbol"], "name": item["name"], "price": 100},
    )
    ticks = iter([0.0, 0.0, 5.0])
    monkeypatch.setattr(deriv_source.time, "monotonic", lambda: next(ticks, 5.0))

    result = deriv_source.futures_overview.__wrapped__()

    assert len(result["index"]) == 2
    assert result["commodity"] == []
    assert any("4.5 秒交互预算" in note for note in result["missing"])


def test_index_timing_prefers_bounded_tencent_kline(monkeypatch) -> None:
    rows = [{"d": f"2026-06-{day:02d}", "o": 1, "c": 1, "h": 1, "l": 1, "v": 1} for day in range(1, 21)]
    monkeypatch.setattr(timing.tc, "stock_daily_bars", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(
        timing.market, "index_kline",
        lambda _code: (_ for _ in ()).throw(AssertionError("腾讯 K 线成功后不应等待慢备源")),
    )

    assert timing._index_bars("sh000001") == rows


def test_fear_greed_consumes_only_existing_fundflow_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(timing, "_legu_breadth", lambda: None)
    monkeypatch.setattr(timing, "_index_vol_factors", lambda: {
        "vol_ratio": None, "vol_pctl": None, "last_date": None,
    })
    monkeypatch.setattr(timing.market, "market_flow_snapshot", lambda: None)
    monkeypatch.setattr(
        timing.market, "market_flow",
        lambda: (_ for _ in ()).throw(AssertionError("交互择时不得触发慢资金流源")),
    )

    result = timing.fear_greed()

    assert result["factors"]["资金"] is None
    assert "两市主力净流入(资金因子缺失)" in result["missing"]
