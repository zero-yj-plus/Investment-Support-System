from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend-vue/src/App.vue"
PAGE = ROOT / "frontend-vue/src/components/IndustryChainPage.vue"
FORM = ROOT / "frontend-vue/src/components/IndustryChainResearchForm.vue"
SUMMARY = ROOT / "frontend-vue/src/components/IndustryChainSummary.vue"
HISTORY = ROOT / "frontend-vue/src/components/IndustryChainHistory.vue"
COMPOSABLE = ROOT / "frontend-vue/src/composables/useIndustryChain.ts"
FORMAT = ROOT / "frontend-vue/src/utils/industryChainFormat.ts"
TYPES = ROOT / "frontend-vue/src/types/industryChain.ts"
AUDIT = ROOT / "frontend-vue/scripts/audit-ui.mjs"
RULES = ROOT / "design-system/wudi-decision-console/pages/industry-chain.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_vue_industry_chain_route_is_reachable_without_migrating_other_research_pages() -> None:
    app = read(APP)
    navigation = read(ROOT / "frontend-vue/src/navigation.ts")

    assert "import IndustryChainPage from './components/IndustryChainPage.vue'" in app
    assert "routePath.value === 'research' && routeTab.value === 'chain'" in app
    assert '<IndustryChainPage' in app
    assert "tab: 'chain', label: '产业链分析'" in navigation
    assert "isOverseas" not in app


def test_vue_industry_chain_uses_the_real_sse_and_knowledge_contracts() -> None:
    composable = read(COMPOSABLE)
    formatter = read(FORMAT)

    assert "postEventStream('/chat', buildIndustryChainRequest(industry)" in composable
    assert "postJson<IndustryChainKnowledgeResponse>('/knowledge'" in composable
    assert "mode: 'chain'" in formatter
    assert "research_contract: INDUSTRY_CHAIN_CONTRACT" in formatter
    assert "INDUSTRY_CHAIN_CONTRACT = 'chain-research-v2'" in formatter
    for field in ("industry", "message", "history", "images", "code", "codes"):
        assert f"{field}:" in formatter
    assert "category: '复盘笔记'" in composable


def test_vue_industry_chain_preserves_missing_empty_partial_and_governance_semantics() -> None:
    page = read(PAGE)
    summary = read(SUMMARY)
    formatter = read(FORMAT)

    for phrase in (
        "真实空结果",
        "不完整输出 · 已隔离",
        "缺失值不补 0",
        "接口未独立暴露",
        "数据可研分无量纲",
        "写入知识库不等于已审批",
    ):
        assert phrase in page or phrase in summary
    assert "TDX\\s*目录未匹配" in formatter
    assert "未取得可验证的\\s*TDX\\s*成分关系" in formatter
    assert "runState.value = 'partial'" in read(COMPOSABLE)
    assert "runState.value = 'error'" in read(COMPOSABLE)


def test_vue_industry_chain_cleans_every_request_and_timer_on_unmount() -> None:
    composable = read(COMPOSABLE)

    assert "controllers: Partial<Record<IndustryChainRequestKey, AbortController>>" in composable
    assert "const generations: Record<IndustryChainRequestKey, number>" in composable
    assert "reader.cancel" in read(ROOT / "frontend-vue/src/services/api.ts")
    assert "clearStageTimer()" in composable
    assert "onBeforeUnmount(() =>" in composable
    assert "abortRequest('research')" in composable
    assert "abortRequest('knowledge')" in composable
    assert "alive && controllers[key] === controller" in composable


def test_vue_industry_chain_uses_safe_markdown_and_native_interaction_semantics() -> None:
    production = "\n".join(read(path) for path in (PAGE, FORM, SUMMARY, HISTORY, COMPOSABLE, FORMAT, TYPES))

    assert "<ReportDocument :content=\"content\"" in production
    assert "parseReportMarkdown" in read(ROOT / "frontend-vue/src/components/ReportDocument.vue")
    assert " v-html=" not in production
    assert ".innerHTML" not in production
    assert '<form class="chain-form"' in production
    assert 'type="submit"' in production
    assert 'type="button"' in production
    assert ':href="`#/research?tab=diagnosis&code=' in production
    assert 'role="alert"' in production
    assert 'aria-live="polite"' in production
    assert 'role="button"' not in production


def test_vue_industry_chain_responsive_rules_contain_wide_content_and_keep_targets_large() -> None:
    page = read(PAGE)
    form = read(FORM)
    history = read(HISTORY)
    styles = read(ROOT / "frontend-vue/src/styles.css")

    assert "grid-template-columns: minmax(0, 1fr) 264px" in page
    assert "@media (max-width: 1039px)" in page
    assert "grid-template-columns: minmax(0, 1fr);" in page
    assert "@media (max-width: 720px)" in page
    assert "overflow-x: auto" in page
    assert "min-height: 44px" in form
    assert "min-height: 64px" in history
    assert ".report-table-wrap { max-width: 100%" in styles
    assert "overflow-x: auto" in styles
    assert "focus-visible" in styles
    assert "prefers-reduced-motion" in styles


def test_vue_industry_chain_has_page_rules_and_real_semantic_audit_fixture() -> None:
    rules = read(RULES)
    audit = read(AUDIT)

    assert "#/research?tab=chain" in rules
    assert "POST /api/chat" in rules
    assert "不采用技能检索返回的玻璃拟态" in rules
    assert "name: 'industry-chain'" in audit
    assert "#/research?tab=chain" in audit
    assert "产业结论：已用 TDX 验证" in audit
    for scenario in ("loading", "empty", "partial-failure"):
        assert f"page: 'industry-chain', scenario: '{scenario}'" in audit
