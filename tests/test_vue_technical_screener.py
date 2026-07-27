from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue" / "src"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_vue_technical_screener_route_is_reachable() -> None:
    navigation = read("frontend-vue/src/navigation.ts")
    app = read("frontend-vue/src/App.vue")
    assert "tab: 'technical-screener', label: '技术面选股'" in navigation
    assert "isTechnicalScreener" in app
    assert "TechnicalScreenerPage" in app
    assert 'v-else-if="isTechnicalScreener"' in app


def test_vue_technical_screener_uses_only_real_contract_paths() -> None:
    source = read("frontend-vue/src/composables/useTechnicalScreener.ts")
    for endpoint in (
        "/technical-screen/catalog",
        "/technical-screen/status",
        "/technical-screen/query",
        "/technical-screen/refresh",
        "/technical-screen/history",
        "/technical-screen/schemes",
    ):
        assert endpoint in source
    assert "/screen'" not in source
    assert "schemaVersion: 2" in source
    assert "algorithm_ver" in source
    assert "adjustment: 'qfq'" in source
    assert "benchmark: '000300.SH'" in source


def test_vue_technical_screener_preserves_missing_empty_and_degraded_semantics() -> None:
    page = read("frontend-vue/src/components/TechnicalScreenerPage.vue")
    results = read("frontend-vue/src/components/TechnicalScreenerResults.vue")
    governance = read("frontend-vue/src/components/TechnicalScreenerGovernance.vue")
    formatter = read("frontend-vue/src/utils/technicalScreenerFormat.ts")
    combined = page + results + governance
    for token in (
        "缓存缺失",
        "状态确认中",
        "缓存状态暂不可用",
        "缓存股票数缺失",
        "部分覆盖",
        "真实空数组",
        "局部失败",
        "平台已接受并进入后台队列；数据尚未更新",
        "缺失、降级、空数组和传输失败分别展示",
    ):
        assert token in page + results + read("frontend-vue/src/composables/useTechnicalScreener.ts")
    assert "value == null" in formatter
    assert "signal_score || 0" not in combined
    assert "row.signal_score ||" not in combined
    assert "示例股票" in results


def test_vue_technical_screener_cleans_every_request_and_page_resource() -> None:
    source = read("frontend-vue/src/composables/useTechnicalScreener.ts")
    for controller in (
        "catalogController",
        "statusController",
        "schemesController",
        "queryController",
        "refreshController",
        "schemeController",
        "historyController",
    ):
        assert f"{controller}?.abort()" in source
    assert "onBeforeUnmount" in source
    assert "clearInterval(statusPollTimer)" in source
    assert "document.removeEventListener('visibilitychange', onVisibilityChange)" in source
    assert "lifecycleGeneration += 1" in source


def test_vue_technical_screener_uses_safe_nodes_and_native_semantics() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (VUE / "components").glob("Technical*.vue")
    )
    assert "v-html" not in sources
    assert "from 'marked'" not in sources
    assert 'from "marked"' not in sources
    assert "marked.parse" not in sources
    assert '<form id="technical-screener-form"' in sources
    assert '<button' in sources
    assert '<a class="technical-stock-link"' in sources
    assert 'role="button"' not in sources
    assert 'role="dialog"' in sources
    assert 'aria-modal="true"' in sources
    assert 'aria-live="polite"' in sources


def test_vue_technical_screener_has_responsive_table_and_accessibility_rules() -> None:
    page = read("frontend-vue/src/components/TechnicalScreenerPage.vue")
    rules = read("frontend-vue/src/components/TechnicalScreenerRules.vue")
    results = read("frontend-vue/src/components/TechnicalScreenerResults.vue")
    dialog = read("frontend-vue/src/components/TechnicalSignalDialog.vue")
    combined = page + rules + results + dialog
    assert "@media (max-width: 1024px)" in page
    assert combined.count("@media (max-width: 720px)") >= 4
    assert ".technical-table-wrap { max-width: 100%; overflow-x: auto; }" in results
    assert ".technical-result-cards { display: grid" in results
    assert "tabindex=\"0\"" in results
    assert "min-height: 44px" in combined
    assert "prefers-reduced-motion" in results
    assert "focus-visible" in rules


def test_vue_technical_screener_audit_fixture_is_explicit_and_not_production_data() -> None:
    audit = read("frontend-vue/scripts/audit-ui.mjs")
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in list((VUE / "components").glob("Technical*.vue"))
        + [VUE / "composables" / "useTechnicalScreener.ts"]
    )
    assert "technical-screener" in audit
    assert "技术审计股份" in audit
    assert "技术审计股份" not in production
    assert "technicalScreenerLoadingStateComplete" in audit
    assert "technicalScreenerEmptyStateComplete" in audit
    assert "technicalScreenerFailureStateComplete" in audit
