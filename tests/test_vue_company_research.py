from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend-vue/src/App.vue"
PAGE = ROOT / "frontend-vue/src/components/CompanyResearchPage.vue"
FORM = ROOT / "frontend-vue/src/components/CompanyResearchForm.vue"
SUMMARY = ROOT / "frontend-vue/src/components/CompanyResearchSummary.vue"
EVIDENCE = ROOT / "frontend-vue/src/components/CompanyResearchEvidence.vue"
HISTORY = ROOT / "frontend-vue/src/components/CompanyResearchHistory.vue"
COMPOSABLE = ROOT / "frontend-vue/src/composables/useCompanyResearch.ts"
FORMAT = ROOT / "frontend-vue/src/utils/companyResearchFormat.ts"
TYPES = ROOT / "frontend-vue/src/types/companyResearch.ts"
AUDIT = ROOT / "frontend-vue/scripts/audit-ui.mjs"
RULES = ROOT / "design-system/wudi-decision-console/pages/company-research.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def production_source() -> str:
    return "\n".join(read(path) for path in (PAGE, FORM, SUMMARY, EVIDENCE, HISTORY, COMPOSABLE, FORMAT, TYPES))


def test_vue_company_research_route_is_reachable_without_migrating_other_placeholder_pages() -> None:
    app = read(APP)
    navigation = read(ROOT / "frontend-vue/src/navigation.ts")

    assert "import CompanyResearchPage from './components/CompanyResearchPage.vue'" in app
    assert "routePath.value === 'research' && routeTab.value === 'company'" in app
    assert '<CompanyResearchPage' in app
    assert "tab: 'company', label: '公司研究'" in navigation
    assert "isOverseas" not in app
    assert "isRestructuring" not in app


def test_vue_company_research_uses_only_the_real_page_contracts() -> None:
    composable = read(COMPOSABLE)

    for endpoint in (
        "getJson<CompanyModelStatus>('/status'",
        "/stock/${encodeURIComponent(code)}/research-enrichment",
        "/disclosures/announcements?code=${encodeURIComponent(code)}&limit=6",
        "/interactions/management?code=${encodeURIComponent(code)}&limit=6",
        "postEventStream('/chat'",
        "postJson<CompanyKnowledgeResponse>('/knowledge'",
    ):
        assert endpoint in composable
    assert "mode: 'company'" in composable
    assert "message: '生成单公司深度研究报告'" in composable
    assert "category: '复盘笔记'" in composable
    assert "/api/diagnose" not in production_source()
    assert "/features/" not in production_source()


def test_vue_company_research_preserves_units_and_missing_semantics() -> None:
    evidence = read(EVIDENCE)
    summary = read(SUMMARY)
    page = read(PAGE)
    formatter = read(FORMAT)

    for phrase in (
        "元/股",
        "收入/利润预测为亿元",
        "ROE 与股东比例为 %",
        "本页接口未独立提供，不从报告文本推导",
        "保持缺失，不补成 0%",
        "不能据此判断查询窗口内“无公告”",
        "这不是“无记录”结论",
        "旧快照的空结果不能证明现在仍无互动问答",
        "不完整输出 · 已隔离",
    ):
        assert phrase in evidence + summary + page
    for state in ("complete_empty", "degraded", "last_good", "missing", "not_supported"):
        assert state in evidence
    assert "value === null || value === undefined || value === ''" in formatter
    assert "return '—'" in formatter
    assert "Number.isFinite(number) ? number.toFixed(digits) : '—'" in formatter


def test_vue_company_research_cleans_every_request_reader_and_timer_on_unmount() -> None:
    composable = read(COMPOSABLE)
    api = read(ROOT / "frontend-vue/src/services/api.ts")

    assert "controllers: Partial<Record<CompanyRequestKey, AbortController>>" in composable
    assert "const generations: Record<CompanyRequestKey, number>" in composable
    for key in ("status", "enrichment", "disclosures", "interactions", "research", "knowledge"):
        assert f"{key}: 0" in composable
    assert "alive && controllers[key] === controller" in composable
    assert "onBeforeUnmount(() =>" in composable
    assert ".forEach(abortRequest)" in composable
    assert "clearStageTimer()" in composable
    assert "clearTimeout(timeout)" in composable
    assert "reader.cancel" in api


def test_vue_company_research_uses_safe_markdown_urls_and_native_semantics() -> None:
    source = production_source()
    formatter = read(FORMAT)

    assert '<ReportDocument :content="content"' in source
    assert "parseReportMarkdown" in read(ROOT / "frontend-vue/src/components/ReportDocument.vue")
    assert " v-html=" not in source
    assert ".innerHTML" not in source
    assert '<form class="company-form"' in source
    assert 'type="submit"' in source
    assert 'type="button"' in source
    assert ':href="`#/research?tab=diagnosis&code=' in source
    assert 'target="_blank" rel="noopener noreferrer"' in source
    for host in ("static.cninfo.com.cn", "static.sse.com.cn", "disc.static.szse.cn"):
        assert host in formatter
    assert "parsed.protocol !== 'https:'" in formatter
    assert "parsed.username || parsed.password" in formatter
    assert 'role="button"' not in source


def test_vue_company_research_responsive_rules_contain_wide_content_and_keep_targets_large() -> None:
    page = read(PAGE)
    form = read(FORM)
    evidence = read(EVIDENCE)
    history = read(HISTORY)
    styles = read(ROOT / "frontend-vue/src/styles.css")

    assert "grid-template-columns: minmax(0, 1fr) 280px" in page
    assert "@media (max-width: 1039px)" in page
    assert "grid-template-columns: minmax(0, 1fr);" in page
    assert "@media (max-width: 720px)" in page
    assert "min-height: 44px" in form
    assert "min-height: 44px" in evidence
    assert "min-height: 64px" in history
    assert ".report-table-wrap { max-width: 100%" in styles
    assert "overflow-x: auto" in styles
    assert "focus-visible" in styles
    assert "prefers-reduced-motion" in styles


def test_vue_company_research_has_page_rules_and_real_semantic_audit_fixture() -> None:
    rules = read(RULES)
    audit = read(AUDIT)

    assert "#/research?tab=company" in rules
    for endpoint in ("GET /api/status", "research-enrichment", "disclosures/announcements", "interactions/management", "POST /api/chat"):
        assert endpoint in rules
    assert "不采用技能检索返回的巨型 Hero" in rules
    assert "name: 'company-research'" in audit
    assert "#/research?tab=company" in audit
    assert "审计公司年度报告" in audit
    for scenario in ("loading", "empty", "partial-failure"):
        assert f"page: 'company-research', scenario: '{scenario}'" in audit
