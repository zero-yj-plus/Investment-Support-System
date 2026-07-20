from __future__ import annotations

from pathlib import Path


MAINFORCE = Path(__file__).resolve().parents[1] / "frontend/js/views/mainforce.js"


def test_mainforce_renders_existing_official_verification_without_new_request() -> None:
    source = MAINFORCE.read_text(encoding="utf-8")

    assert "d?.official_verification" in source
    assert "renderOfficialVerification" in source
    assert "官方代码集核验状态未返回" in source
    assert "/mainforce/longhu/official" not in source
    assert source.count("api.get('/mainforce/longhu')") == 1


def test_complete_and_partial_code_set_comparison_are_precise() -> None:
    source = MAINFORCE.read_text(encoding="utf-8")

    assert "state === 'complete'" in source
    assert "交易所官网代码集已核验" in source
    assert "state === 'partial'" in source
    assert "交易所官网代码集部分核验" in source
    assert "差异暂定" in source
    assert "counts.matched" in source
    assert "counts.eastmoney_only" in source
    assert "counts.official_only" in source
    assert "匹配 ${matched} · 东财独有 ${eastmoneyOnly} · 官网独有 ${officialOnly}" in source


def test_unavailable_explains_which_side_prevented_classification() -> None:
    source = MAINFORCE.read_text(encoding="utf-8")

    assert "v.eastmoney_available === false" in source
    assert "东财龙虎榜当前不可用，无法与官网代码集合比较" in source
    assert "后台将在 18:20 / 21:10 采集交易所官网快照" in source
    assert "当前不把缓存缺失当作代码差异" in source
    assert "数据不全" not in source


def test_verification_is_code_only_and_visible_even_when_institution_list_is_empty() -> None:
    source = MAINFORCE.read_text(encoding="utf-8")

    assert "只比股票代码集合，不比金额" in source
    verification = source.index("const officialVerification = renderOfficialVerification")
    empty_branch = source.index("if (!inst.length)", verification)
    # 四态横幅 + 官方核验在空态与有榜两个分支都渲染（官方核验始终可见）。
    empty_render = source.index("body.innerHTML = statusBanner + officialVerification", empty_branch)
    rows_render = source.index("body.innerHTML = statusBanner + officialVerification", empty_render + 1)
    assert verification < empty_branch < empty_render < rows_render
