from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def _read(relative: str) -> str:
    return (VUE / relative).read_text(encoding="utf-8")


def test_vue_screener_route_is_reachable_without_migrating_research_siblings() -> None:
    app = _read("src/App.vue")
    navigation = _read("src/navigation.ts")

    assert "const isScreener = computed(() => routePath.value === 'research' && routeTab.value === 'screener')" in app
    assert '<ScreenerPage' in app
    assert 'v-else-if="isScreener"' in app
    assert "if (isScreener.value && screenerPage.value)" in app
    assert "{ path: 'research', tab: 'screener', label: '条件选股' }" in navigation
    assert "{ path: 'research', tab: 'quant-pick', label: '每日量化选股' }" in navigation
    assert "{ path: 'research', tab: 'events', label: '事件掘金' }" in navigation


def test_vue_screener_uses_only_real_screen_and_watchlist_contracts() -> None:
    composable = _read("src/composables/useScreener.ts")
    types = _read("src/types/screener.ts")

    assert "postJson<ScreenerResponse>('/screen', request, controller.signal)" in composable
    assert "postJson<ScreenerWatchlistResponse>('/watchlist'" in composable
    assert "result_limit: 300" in composable
    assert "minimum_ratio" in types and "sufficient" in types
    assert "industry_coverage" in types and "analysis_contract" in types
    for unrelated in ("/market/", "/reports/", "/chat", "/technical-screen", "/recommendations"):
        assert unrelated not in composable


def test_vue_screener_preserves_units_and_missing_semantics() -> None:
    page = _read("src/components/ScreenerPage.vue")
    results = _read("src/components/ScreenerResults.vue")
    health = _read("src/components/ScreenerDataHealth.vue")
    composable = _read("src/composables/useScreener.ts")
    formatter = _read("src/utils/screenerFormat.ts")
    combined = page + results + health + composable + formatter

    for phrase in (
        "真实空数组",
        "全市场筛选不可用",
        "部分覆盖",
        "陈旧快照",
        "缺失与降级原因",
        "股票池完整性字段缺失或未确认",
        "已有成功结果仍保留",
        "任何缺失值都不补 0",
        "表单市值与成交额输入单位为亿元",
        "响应单位为元",
        "涨跌幅与换手率为百分数",
        "页面不生成买入、风险或仓位结论",
    ):
        assert phrase in combined
    assert "value == null || value === ''" in formatter
    assert "return parsed == null ? '—'" in formatter
    assert "payload.ok === false" in composable
    assert "!Array.isArray(payload.stocks)" in composable
    assert "coverage?.sufficient === false" in page + health
    for source in (page, results, health, composable, formatter):
        assert "|| 0" not in source
        assert "?? 0" not in source


def test_vue_screener_cleans_lifecycle_and_prevents_duplicate_actions() -> None:
    page = _read("src/components/ScreenerPage.vue")
    composable = _read("src/composables/useScreener.ts")

    for contract in (
        "let screenController: AbortController | null = null",
        "const watchlistControllers = new Map<string, AbortController>()",
        "screenController?.abort()",
        "watchlistControllers.forEach((controller) => controller.abort())",
        "if (progressTimer) clearInterval(progressTimer)",
        "if (loadingDelay) clearTimeout(loadingDelay)",
        "if (screenTimeout) clearTimeout(screenTimeout)",
        "watchlistTimeouts.forEach((timeout) => clearTimeout(timeout))",
        "let alive = false",
        "if (!alive || controller !== screenController",
        "if (loading.value) return false",
        "watchlistStates[code] === 'adding'",
    ):
        assert contract in composable
    assert "onBeforeUnmount(() => emit('refreshState', false))" in page


def test_vue_screener_uses_safe_text_and_native_accessible_semantics() -> None:
    files = [
        "src/components/ScreenerPage.vue",
        "src/components/ScreenerFilters.vue",
        "src/components/ScreenerResults.vue",
        "src/components/ScreenerDataHealth.vue",
    ]
    combined = "".join(_read(path) for path in files)

    assert "v-html" not in combined
    assert "innerHTML" not in combined
    assert "reportMarkdown" not in combined
    assert 'role="button"' not in combined
    assert '<form id="screener-filter-form"' in combined
    assert '<label' in combined and '<input' in combined
    assert '<button' in combined
    assert ':href="diagnosisHref(stock.code)"' in combined
    assert '<caption class="sr-only">' in combined
    assert 'tabindex="0"' in combined
    assert 'aria-live="polite"' in combined
    assert 'role="alert"' in combined
    assert "min-height: 44px" in combined
    for preset in ("活跃放量", "低估值容量", "小盘弹性", "趋势强势", "回调承接"):
        assert preset in combined
    assert "服务端" not in combined


def test_vue_screener_uses_hotboard_style_sortable_column_headers() -> None:
    filters = _read("src/components/ScreenerFilters.vue")
    results = _read("src/components/ScreenerResults.vue")
    composable = _read("src/composables/useScreener.ts")
    types = _read("src/types/screener.ts")

    assert "screener-sort-button" not in filters
    assert "sortBy" not in filters + types
    assert "sortDesc" not in filters + types
    assert '<select' not in results
    assert "const sortKey = ref<ResultSortKey | null>(null)" in results
    assert "const sortDirection = ref<'ascending' | 'descending'>('descending')" in results
    for value, label in (
        ("price", "现价"),
        ("change_pct", "涨跌幅"),
        ("turnover_rate", "换手率"),
        ("volume_ratio", "量比"),
        ("pe_ttm", "PE TTM"),
        ("pb", "PB"),
        ("circ_mv", "流通市值"),
        ("amount", "成交额"),
    ):
        assert f'@click="sortBy(\'{value}\')"' in results
        assert f"sortLabel('{value}', '{label}')" in results
        assert f'ariaSort(\'{value}\')' in results
    assert "[...props.stocks].sort" in results
    assert 'type="button" class="screener-sort-button"' in results
    assert "首次点击按从高到低" in results
    assert "if (leftValue == null) return 1" in results
    assert "if (rightValue == null) return -1" in results
    assert "点击数值列表头排序 · 仅重排当前返回结果" in results
    assert "min-height: 44px" in results
    assert "sort_by: 'amount', sort_desc: true, result_limit: 300" in composable


def test_vue_screener_has_mobile_cards_and_reduced_motion_rules() -> None:
    page = _read("src/components/ScreenerPage.vue")
    filters = _read("src/components/ScreenerFilters.vue")
    results = _read("src/components/ScreenerResults.vue")
    health = _read("src/components/ScreenerDataHealth.vue")
    combined = page + filters + results + health

    for breakpoint in ("max-width: 1240px", "max-width: 1180px", "max-width: 860px", "max-width: 720px"):
        assert breakpoint in combined
    assert "grid-template-columns: minmax(0, 1fr)" in combined
    assert ".screener-table thead { position: absolute" in results
    assert ".screener-table, .screener-table tbody, .screener-table tr, .screener-table td { display: block" in results
    assert ".screener-table-wrap { max-width: 100%; overflow-x: auto" in results
    assert "min-width: 0" in combined
    assert "prefers-reduced-motion: reduce" in results


def test_vue_screener_audit_has_real_fixture_and_required_states() -> None:
    audit = _read("scripts/audit-ui.mjs")

    assert "name: 'screener'" in audit
    assert "#/research?tab=screener" in audit
    assert "'/api/screen'" in audit
    for semantic in (
        "minimum_ratio",
        "industry_coverage",
        "condition-screen-v2",
        "candidate_only",
        "missing_exchanges",
    ):
        assert semantic in audit
    for screenshot in (
        "screener-loading-375.png",
        "screener-empty-1440.png",
        "screener-partial-failure-1440.png",
    ):
        assert screenshot in audit
    assert "screenerLoadingStateComplete" in audit
    assert "screenerEmptyStateComplete" in audit
    assert "screenerFailureStateComplete" in audit
