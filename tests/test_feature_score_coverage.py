from __future__ import annotations

from hermes_trader.analysis import features


def _sectors() -> dict:
    return {
        "sectors": [
            {
                "name": "完整行业", "kind": "industry", "change_pct": 1.0,
                "up_count": 8, "down_count": 2, "amount": 10_000_000_000,
            },
            {
                "name": "缺宽度概念", "kind": "concept", "change_pct": 9.0,
                "up_count": None, "down_count": None, "amount": 8_000_000_000,
            },
        ],
        "missing": ["概念涨跌家数"],
        "notes": [],
    }


def _limitup() -> dict:
    return {
        "stocks": [{"name": "样本股", "industry": "完整行业", "reason": "", "lianban": 2}],
        "missing": [],
        "notes": [],
        "data_date": "2026-07-15",
    }


def test_money_effect_marks_partial_scores_and_does_not_rank_them_as_equal(monkeypatch) -> None:
    monkeypatch.setattr(features.market, "market_sectors", _sectors)
    monkeypatch.setattr(features.market, "market_limitup", _limitup)

    result = features.theme_money_effect(top_n=10)

    by_name = {row["name"]: row for row in result["themes"]}
    assert result["data_state"] == "degraded"
    assert result["score_semantics"]["name"] == "available_factor_score"
    assert by_name["完整行业"]["score_coverage"]["state"] == "complete"
    assert by_name["缺宽度概念"]["score_coverage"] == {
        "complete": 1,
        "total": 2,
        "state": "partial",
        "missing": ["breadth"],
    }
    # 概念的部分因子分虽更高，也不得压过完整因子样本冒充同口径榜首。
    assert result["themes"][0]["name"] == "完整行业"


def test_crowdedness_exposes_factor_coverage_for_each_row(monkeypatch) -> None:
    monkeypatch.setattr(features.market, "market_sectors", _sectors)
    monkeypatch.setattr(features.market, "market_limitup", _limitup)
    monkeypatch.setattr(
        features.market,
        "market_overview",
        lambda: {"total_amount": 100_000_000_000, "data_date": "2026-07-15"},
    )

    result = features.crowdedness(top_n=10)
    by_name = {row["name"]: row for row in result["sectors"]}

    assert by_name["完整行业"]["score_coverage"]["state"] == "complete"
    assert by_name["缺宽度概念"]["score_coverage"]["state"] == "partial"
    assert result["score_semantics"]["name"] == "cross_section_percentile"
