from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"
PAGE = VUE / "src/components/ReportCenterPage.vue"
SUMMARY = VUE / "src/components/ReportCenterSummary.vue"
GOVERNANCE = VUE / "src/components/ReportCenterGovernance.vue"
COMPOSABLE = VUE / "src/composables/useReportCenter.ts"
TYPES = VUE / "src/types/reports.ts"
AUDIT = VUE / "scripts/audit-ui.mjs"
RULES = ROOT / "design-system/wudi-decision-console/pages/reports.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def production_source() -> str:
    return "\n".join(read(path) for path in (PAGE, SUMMARY, GOVERNANCE, COMPOSABLE, TYPES))


def test_vue_report_center_route_is_reachable() -> None:
    app = read(VUE / "src/App.vue")
    navigation = read(VUE / "src/navigation.ts")

    assert "import ReportCenterPage from './components/ReportCenterPage.vue'" in app
    assert "routePath.value === 'history' && routeTab.value === 'reports'" in app
    assert 'v-else-if="isReportCenter"' in app
    assert '<ReportCenterPage' in app
    assert "if (isReportCenter.value && reportCenterPage.value)" in app
    assert "{ path: 'history', tab: 'reports', label: '全部报告' }" in navigation


def test_vue_report_center_uses_only_real_report_and_delivery_contracts() -> None:
    composable = read(COMPOSABLE)

    for endpoint in (
        "'/archive/dates'",
        "`/reports/${selectedType.value}?date=${encodeURIComponent(selectedDate.value)}`",
        "`/reports/${selectedType.value}/generate`",
        "`/reports/${selectedType.value}/push`",
        "'/delivery/receipts?limit=100'",
    ):
        assert endpoint in composable
    for unrelated in ("/market/", "/features/", "/screen", "/diagnose", "/chat"):
        assert unrelated not in composable
    assert "force_resend: true" in composable
    assert "resend_id: crypto.randomUUID()" in composable
    assert "report.pushed ? '再次推送'" in read(PAGE)


def test_vue_report_center_preserves_missing_degraded_and_delivery_semantics() -> None:
    source = production_source()

    for field in (
        "content_md", "content_id", "content_hash", "report_version", "plan_version",
        "claim_validation", "advice_governance", "input_lineage", "attempted_at",
        "sent_at", "ack_at", "target_results",
    ):
        assert field in read(TYPES)
    for phrase in (
        "风险未提供",
        "仓位边界",
        "未提供",
        "不能据此视为“无禁止条件”",
        "平台已接受请求，但这不等于最终送达或已读",
        "旧记录仅标记 pushed，未提供平台接受或送达层级",
        "以上仅保留报告自身已有的推送标记，不能补充平台回执结论",
        "不完整输出 · 已隔离",
        "没有使用示例正文或数字补位",
        "陈旧数据：是",
        "数据状态：降级",
    ):
        assert phrase in source
    assert "?? 0" not in source
    assert "|| 0" not in source


def test_vue_report_center_cleans_requests_reader_timer_and_late_responses() -> None:
    composable = read(COMPOSABLE)
    api = read(VUE / "src/services/api.ts")

    for controller in (
        "reportController", "historyController", "receiptController",
        "generationController", "pushController",
    ):
        assert f"let {controller}: AbortController | null = null" in composable
        assert f"{controller}?.abort()" in composable
    for generation in ("report", "history", "receipts", "generate", "push"):
        assert f"generations.{generation}" in composable
    assert "if (!alive || controller !== reportController" in composable
    assert "if (!alive || controller !== receiptController" in composable
    assert "onBeforeUnmount(() =>" in composable
    assert "clearInterval(generationTimer)" in composable
    assert "reader.cancel" in api


def test_vue_report_center_uses_safe_markdown_and_native_interactions() -> None:
    source = production_source()
    page = read(PAGE)
    report_document = read(VUE / "src/components/ReportDocument.vue")

    assert '<ReportDocument :content="report.content_md || \'\'"' in page
    assert "parseReportMarkdown" in report_document
    assert " v-html=" not in source
    assert ".innerHTML" not in source
    assert '<button' in page and 'type="button"' in page
    assert '<a\n                  v-for="item in outline"' in page
    assert ':href="`#/history?tab=reports&section=' in page
    assert 'type="date"' in page
    assert 'class="report-type-tabs" tabindex="0" aria-label="报告类型，可横向滚动"' in page
    assert 'aria-live="polite"' in page
    assert 'role="button"' not in source


def test_vue_report_center_has_responsive_containment_and_accessible_targets() -> None:
    page = read(PAGE)
    summary = read(SUMMARY)
    governance = read(GOVERNANCE)
    combined = page + summary + governance

    assert ".report-center-page { display: grid; min-width: 0;" in page
    assert "grid-template-columns: 216px minmax(0, 1fr)" in page
    assert "grid-template-columns: minmax(0, 1fr) 260px" in page
    assert "overflow-x: auto" in page
    assert "min-height: 44px" in combined
    for breakpoint in (1100, 900, 720, 520):
        assert f"@media (max-width: {breakpoint}px)" in page
    assert "@media (prefers-reduced-motion: reduce)" in page
    assert ':focus-visible' in read(VUE / "src/styles.css")


def test_vue_report_center_has_page_rules_and_real_semantic_audit_fixture() -> None:
    rules = read(RULES)
    audit = read(AUDIT)

    assert "#/history?tab=reports" in rules
    for endpoint in (
        "GET /api/archive/dates", "GET /api/reports/{rtype}",
        "POST /api/reports/{rtype}/generate", "POST /api/reports/{rtype}/push",
        "GET /api/delivery/receipts?limit=100",
    ):
        assert endpoint in rules
    assert "不采用巨型 Hero" in rules
    assert "name: 'reports', hash: '#/history?tab=reports'" in audit
    assert "审计早盘内参" in audit
    assert "const reportCenterFixture =" in audit
    assert "path === '/api/reports/morning' ? reportCenterFixture : fixtures[path]" in audit
    for scenario in (
        "reports-loading-375",
        "reports-empty-1440",
        "reports-partial-failure-1440",
    ):
        assert scenario in audit
    assert "审计早盘内参" not in production_source()
