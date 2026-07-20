from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "frontend/js/views/_recommend_review.js"
VIEWS = {
    name: ROOT / f"frontend/js/views/{name}.js"
    for name in ("recommend", "close", "tail")
}


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_shared_v2_semantics_distinguish_every_non_scoring_state() -> None:
    source = _source(HELPER)

    assert "REVIEW_WINDOWS = Object.freeze(['T0', 'T1', 'T2', 'T5'])" in source
    for contract_value in (
        "evaluation_state",
        "pending_reason",
        "waiting",
        "untriggered",
        "incomparable",
        "not_triggered",
        "insufficient_evidence",
        "hit",
        "partial_hit",
        "invalidated",
    ):
        assert contract_value in source
    for label in ("待到期", "未触发", "不可比", "证据不足", "方向命中", "部分命中", "方向失效"):
        assert label in source


def test_all_review_surfaces_render_four_window_timeline_and_separate_returns() -> None:
    for name, path in VIEWS.items():
        source = _source(path)
        assert "from './_recommend_review.js'" in source, name
        assert "reviewWindowRows" in source, name
        assert "['T0', 'T1', 'T2', 'T5']" in source or "T0 T1 T2 T5" in source, name
        assert "final_return" in source, name
        assert "directional_return" in source, name
        assert "标的涨跌" in source, name
        assert "方向结果" in source, name
        assert "max_return" in source and "max_drawdown" in source, name
        assert "MFE" in source and "MAE" in source, name
        assert "target_trade_day" in source, name


def test_all_stats_surfaces_use_percent_contract_without_unit_guessing() -> None:
    joined = "\n".join(_source(path) for path in VIEWS.values())
    assert "Math.abs(Number(d.win_rate)) <= 1" not in joined
    assert "Number(d.win_rate) * 100" not in joined

    for name, path in VIEWS.items():
        source = _source(path)
        assert "statsPercent" in source, name
        assert "win_rate_unit" in source, name
        assert "observed_win_rate" in source, name
        assert "minimum_stable_sample" in source, name
        assert "稳定胜率" in source, name
        assert "原始观察比例" in source, name


def test_all_stats_surfaces_show_non_scoring_counts_windows_and_missing_reasons() -> None:
    for name, path in VIEWS.items():
        source = _source(path)
        for contract_value in ("waiting", "untriggered", "incomparable", "excluded", "per_window", "missing"):
            assert contract_value in source, f"{name} missing {contract_value}"
        for label in ("待到期", "未触发", "不可比", "已排除推荐"):
            assert label in source, f"{name} missing {label}"


def test_stats_percent_rejects_unknown_units_instead_of_guessing() -> None:
    source = _source(HELPER)

    assert "stats.win_rate_unit !== 'percent'" in source
    assert "return null" in source
    assert "* 100" not in source


def test_recommendation_page_separates_proposals_from_scoreable_recommendations() -> None:
    source = _source(VIEWS["recommend"])

    assert "api.get('/advice-proposals?limit=100')" in source
    assert "候选提案" in source
    assert "正式推荐复盘" in source
    assert "永不直接计入胜率" in source
    assert "recommendation_created" in source
    assert "风险闸不会自动代填" in source
    assert "['buy', 'add'].includes(direction) && !positionCap" in source
    assert "被否决的候选仍保留审计记录" in source
