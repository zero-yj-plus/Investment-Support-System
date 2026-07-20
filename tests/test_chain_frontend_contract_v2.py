from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHAIN = ROOT / "frontend/js/views/chain.js"
CHAIN_SHARED = ROOT / "frontend/js/views/_chain_shared.js"


def source() -> str:
    return CHAIN.read_text(encoding="utf-8")


def shared_source() -> str:
    return CHAIN_SHARED.read_text(encoding="utf-8")


def test_chain_frontend_uses_shared_research_request_contract() -> None:
    text = source()
    shared = shared_source()

    assert "from './_chain_shared.js'" in text
    assert "buildChainResearchRequest({" in text
    assert "await ssePost('/chat', request" in text
    assert "chainResearchScopeHint()" in text
    assert "CHAIN_RESEARCH_CONTRACT_VERSION" in text
    assert "export function buildChainResearchRequest" in shared
    assert "research_contract: CHAIN_RESEARCH_CONTRACT_VERSION" in shared


def test_chain_frontend_accepts_structured_and_legacy_stream_contracts() -> None:
    text = source()

    assert "export function normalizeChainPayload" in text
    for envelope in ("structured", "industry_chain", "chain_result", "result", "data"):
        assert f"'{envelope}'" in text
    assert "parseWholeJson(rawText)" in text
    assert "renderMd(curContent)" in text
    assert "export function extractChainMarkdownSections" in text
    assert "renderMarkdownReport(curContent)" in text
    assert "结构化 Markdown" in text
    assert "lastInlineDecision(content, '产业结论')" in text
    assert "lastInlineDecision(content, '当前交易时点')" in text
    assert "'chain_structure'" in text
    assert "'realization_and_valuation'" in text
    assert "'data_as_of'" in text


def test_chain_frontend_renders_required_research_dimensions() -> None:
    text = source()

    assert "'产业地图'" in text
    assert "'卡点'" in text
    assert "dimension.id === 'evidence'" in text
    for label in (
        "需求本质",
        "上中下游结构",
        "价值捕获",
        "关键卡点",
        "国产替代",
        "公司映射",
        "兑现与估值",
        "研究优先级",
        "失效条件",
        "证据缺口",
    ):
        assert label in text
    for field in (
        "demand_essence",
        "value_capture",
        "bottlenecks",
        "domestic_substitution",
        "company_mapping",
        "realization_valuation",
        "priorities",
        "invalidations",
    ):
        assert field in text


def test_chain_frontend_separates_industry_conclusion_from_trading_timing() -> None:
    text = source()

    assert "产业结论" in text
    assert "交易时点（与产业结论分开）" in text
    assert "不被单日涨跌改写" in text
    assert "不把产业逻辑直接等同于当下买点" in text
    assert "产业结构 ≠ 当下买点" in text
    assert "trading_timing" in text
    assert "industry_conclusion" in text


def test_chain_frontend_keeps_provenance_gaps_history_and_knowledge() -> None:
    text = source()

    for label in ("数据时点", "证据覆盖", "质量状态", "证据源"):
        assert label in text
    assert "evidence_coverage" in text
    assert "evidence_gaps" in text
    assert "hermes.chain.history" in text
    assert "saveToKnowledge" in text
    assert "structuredChainToMarkdown" in text
    assert "api.post('/knowledge'" in text


def test_chain_frontend_does_not_block_deterministic_fallback_without_ai() -> None:
    text = source()

    assert "确定性产业链证据可用" in text
    assert "不会凭常识补公司" in text
    assert "goButton.disabled = true" not in text
    assert "if (!readiness.ready)" not in text
