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


def test_vue_industry_chain_uses_persistent_background_runs_and_knowledge_contracts() -> None:
    composable = read(COMPOSABLE)
    formatter = read(FORMAT)

    assert "postJson<ResearchRunResponse>('/research/runs'" in composable
    assert "getJson<ResearchRunLatestResponse>('/research/runs/latest?kind=chain'" in composable
    assert "`/research/runs/${encodeURIComponent(runId)}`" in composable
    assert "deleteJson<ResearchRunDeleteResponse>(`/research/runs/${encodeURIComponent(entry.id)}`" in composable
    assert "kind: 'chain', industry, focus" in composable
    assert "postJson<IndustryChainKnowledgeResponse>('/knowledge'" in composable
    assert "mode: 'chain'" in formatter
    assert "research_contract: INDUSTRY_CHAIN_CONTRACT" in formatter
    assert "INDUSTRY_CHAIN_CONTRACT = 'chain-research-v2'" in formatter
    for field in ("industry", "message", "history", "images", "code", "codes"):
        assert f"{field}:" in formatter
    assert "category: '复盘笔记'" in composable
    assert "研究重点（可选）" in read(FORM)
    assert "focusInput" in composable
    assert "重点解释产业为什么成立、价值如何沿上下游传导" in formatter


def test_vue_industry_chain_preserves_missing_empty_partial_and_governance_semantics() -> None:
    page = read(PAGE)
    summary = read(SUMMARY)
    formatter = read(FORMAT)

    for phrase in (
        "不完整输出 · 已隔离",
        "产业逻辑速览",
        "报告以产业结构和商业逻辑为主",
        "写入知识库不等于已审批",
    ):
        assert phrase in page or phrase in summary
    for removed_summary_copy in ("阅读主线", "报告定位", "研究主题", "核心框架", "公司映射", "交易判断"):
        assert removed_summary_copy not in summary
    assert "板块目录未匹配" in formatter
    assert "未取得可验证的" in formatter
    assert "成分关系" in formatter
    assert "产业链研究未完成" in formatter
    assert "entry.incomplete ? 'partial' : 'complete'" in read(COMPOSABLE)
    assert "runState.value = 'error'" in read(COMPOSABLE)


def test_vue_industry_chain_cleans_every_request_and_timer_on_unmount() -> None:
    composable = read(COMPOSABLE)

    assert "controllers: Partial<Record<IndustryChainRequestKey, AbortController>>" in composable
    assert "const generations: Record<IndustryChainRequestKey, number>" in composable
    assert "reader.cancel" in read(ROOT / "frontend-vue/src/services/api.ts")
    assert "clearStageTimer()" in composable
    assert "clearPollTimer()" in composable
    assert "onBeforeUnmount(() =>" in composable
    assert "abortRequest('research')" in composable
    assert "abortRequest('knowledge')" in composable
    assert "abortRequest('delete')" in composable
    assert "alive && controllers[key] === controller" in composable
    assert "requestGuard('research', 15_000)" in composable
    assert "服务端后台研究任务继续运行" in composable
    assert "setTimeout(() => { void pollRun(runId) }, 2_000)" in composable


def test_vue_industry_chain_uses_safe_markdown_and_native_interaction_semantics() -> None:
    production = "\n".join(read(path) for path in (PAGE, FORM, SUMMARY, HISTORY, COMPOSABLE, FORMAT, TYPES))

    assert "<ReportDocument" in PAGE.read_text(encoding="utf-8")
    assert ':content="content"' in PAGE.read_text(encoding="utf-8")
    report_document = read(ROOT / "frontend-vue/src/components/ReportDocument.vue")
    report_inline = read(ROOT / "frontend-vue/src/components/ReportInline.vue")
    assert "parseReportMarkdown" in report_document
    assert " v-html=" not in production
    assert ".innerHTML" not in production
    assert '<form class="chain-form"' in production
    assert 'type="submit"' in production
    assert 'type="button"' in production
    assert "核验候选" not in PAGE.read_text(encoding="utf-8")
    assert "chain-company-links" not in PAGE.read_text(encoding="utf-8")
    assert ':diagnosis-codes="summary.companies.map((company) => company.code)"' in PAGE.read_text(encoding="utf-8")
    assert 'diagnosis-section="板块与成员口径"' in PAGE.read_text(encoding="utf-8")
    assert "block.text === target" in report_document
    assert "block.level <= sectionLevel" in report_document
    assert ':href="`#/research?tab=diagnosis&code=' in report_inline
    assert 'class="report-diagnosis-link"' in report_inline
    assert 'role="alert"' in production
    assert 'aria-live="polite"' in production
    assert 'role="button"' not in production
    assert "window.confirm" in read(HISTORY)
    assert "此操作不可撤销" in read(HISTORY)
    assert 'class="chain-history__delete"' in read(HISTORY)
    assert '@delete="deleteHistory"' in read(PAGE)


def test_vue_industry_chain_responsive_rules_contain_wide_content_and_keep_targets_large() -> None:
    page = read(PAGE)
    form = read(FORM)
    history = read(HISTORY)
    styles = read(ROOT / "frontend-vue/src/styles.css")

    assert "grid-template-columns: minmax(0, 1fr) 264px" in page
    assert "@media (max-width: 1039px)" in page
    assert "grid-template-columns: minmax(0, 1fr);" in page
    assert "@media (max-width: 720px)" in page
    assert "min-height: 44px" in form
    assert "min-height: 64px" in history
    assert "min-height: 44px" in history
    assert "min-height: 44px" in read(ROOT / "frontend-vue/src/components/ReportInline.vue")
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
