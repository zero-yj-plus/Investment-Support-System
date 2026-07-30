from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def read(relative: str) -> str:
    return (VUE / relative).read_text(encoding="utf-8")


def production_sources() -> str:
    paths = [
        "src/components/ForecastMiningPage.vue",
        "src/components/ForecastMiningSummary.vue",
        "src/components/ForecastMiningTable.vue",
        "src/composables/useForecastMining.ts",
        "src/types/forecastMining.ts",
        "src/utils/forecastFormat.ts",
    ]
    return "\n".join(read(path) for path in paths)


def test_vue_forecast_mining_route_remains_reachable_with_migrated_research_siblings() -> None:
    app = read("src/App.vue")
    navigation = read("src/navigation.ts")

    assert "const isForecastMining = computed(() => routePath.value === 'research' && routeTab.value === 'events')" in app
    assert '<ForecastMiningPage' in app
    assert 'v-else-if="isForecastMining"' in app
    assert "if (isForecastMining.value && forecastMiningPage.value)" in app
    assert "{ path: 'research', tab: 'events', label: '事件掘金' }" in navigation
    assert "const isCompanyResearch" in app
    assert "const isIndustryChain" in app


def test_vue_forecast_mining_uses_only_the_real_deterministic_endpoint() -> None:
    composable = read("src/composables/useForecastMining.ts")

    assert "getJson<ForecastMiningResponse>('/forecast/mining', controller.signal)" in composable
    assert composable.count("getJson<") == 1
    for unrelated in ("/reports/", "/chat", "/push", "/screen", "/diagnose"):
        assert unrelated not in composable


def test_vue_forecast_mining_preserves_units_and_missing_semantics() -> None:
    sources = production_sources()
    types = read("src/types/forecastMining.ts")
    formatter = read("src/utils/forecastFormat.ts")

    for field in (
        "p_chg_min", "p_chg_max", "net_min", "net_max", "last_net", "ann_date",
        "mandatory", "layer", "trap_reason", "nonrec_flag", "is_new_today", "data_ts",
        "used_last_good", "snapshot_age_seconds",
    ):
        assert field in types
    assert "if (low == null && high == null) return '—'" in formatter
    assert "if (number == null) return '—'" in formatter
    assert "100_000_000" in formatter and "10_000" in formatter
    assert "同比单位为 %" in sources
    assert "原始单位为人民币元" in sources
    assert "接口未提供独立数据日期或陈旧标记" in sources
    assert "计算时点不等于公告数据日期" in sources
    assert "降级 · 上次成功快照" in sources
    assert "当前使用上次成功快照" in sources
    assert "接口成功返回 `items=[]` 与 `traps=[]`" in sources
    assert "payload.ok === false" in sources
    assert "payload.missing?.[0]" in sources
    assert "|| 0" not in formatter


def test_vue_forecast_mining_cleans_request_timeout_listener_and_late_responses() -> None:
    composable = read("src/composables/useForecastMining.ts")

    for contract in (
        "let miningController: AbortController | null = null",
        "let requestTimeout: ReturnType<typeof setTimeout> | null = null",
        "miningController?.abort()",
        "clearTimeout(requestTimeout)",
        "if (!alive || controller !== miningController) return",
        "document.addEventListener('visibilitychange', onVisibilityChange)",
        "document.removeEventListener('visibilitychange', onVisibilityChange)",
        "if (refreshing.value) return",
    ):
        assert contract in composable


def test_vue_forecast_mining_uses_safe_nodes_and_native_interactions() -> None:
    sources = production_sources()

    assert "v-html" not in sources
    assert "innerHTML" not in sources
    assert "reportMarkdown" not in sources
    assert 'role="button"' not in sources
    assert '<button type="button"' in sources
    assert '<a class="forecast-stock-link"' in sources
    assert ':href="diagnosisHref(row.code)"' in sources
    assert '<caption class="sr-only">' in sources
    assert 'tabindex="0"' in sources
    assert 'aria-live="polite"' in sources
    assert 'role="alert"' in sources
    assert 'aria-pressed="pool === \'items\'"' in sources
    assert 'class="forecast-type-filter"' in sources
    assert ':aria-pressed="selectedType === label"' in sources
    assert '<label for="forecast-code-search">股票代码搜索</label>' in sources
    assert 'type="search"' in sources
    assert 'inputmode="numeric"' in sources
    assert 'maxlength="6"' in sources


def test_vue_forecast_mining_filters_current_server_rows_by_type_and_code() -> None:
    page = read("src/components/ForecastMiningPage.vue")
    table = read("src/components/ForecastMiningTable.vue")

    assert "const selectedType = ref('')" in page
    assert "const codeQuery = ref('')" in page
    assert "row.type !== selectedType.value" in page
    assert "String(row.code || '').includes(codeQuery.value)" in page
    assert "value.replace(/\\D/g, '').slice(0, 6)" in page
    assert ':rows="filteredRows"' in page
    assert ':source-count="rows.length"' in page
    assert "emit('updateSearchCode', (event.target as HTMLInputElement).value)" in table
    assert "当前筛选没有匹配标的" in table
    assert "服务端返回顺序" in table
    audit = read("scripts/audit-ui.mjs")
    assert "forecastTypeFilter.click()" in audit
    assert "typeFilter:" in audit
    assert "codeFilter:" in audit
    assert "activeTypeAnnounced:" in audit


def test_vue_forecast_mining_has_responsive_table_and_accessibility_rules() -> None:
    page = read("src/components/ForecastMiningPage.vue")
    summary = read("src/components/ForecastMiningSummary.vue")
    table = read("src/components/ForecastMiningTable.vue")
    combined = page + summary + table

    assert ".forecast-page { display: grid; min-width: 0;" in page
    assert ".forecast-table-wrap { max-width: 100%; overflow-x: auto; }" in table
    assert "@media (max-width: 720px)" in combined
    assert ".forecast-table, .forecast-table tbody, .forecast-table tr, .forecast-table td { display: block; width: 100%; }" in table
    assert "min-height: 44px" in table
    assert "prefers-reduced-motion: reduce" in page + table
    assert ":focus-visible" in read("src/styles.css")


def test_vue_forecast_mining_omits_removed_conclusion_panel() -> None:
    summary = read("src/components/ForecastMiningSummary.vue")

    for removed in (
        "forecast-conclusion",
        "服务端规则给出",
        "下一步只做证据复核",
        "风险、仓位和禁止条件",
        "本接口未提供仓位上限或价格区间",
    ):
        assert removed not in summary
    assert 'class="forecast-metrics"' in summary
    assert 'aria-label="榜单统计"' in summary


def test_vue_forecast_mining_audit_has_real_fixture_and_required_states() -> None:
    audit = read("scripts/audit-ui.mjs")
    production = production_sources()

    assert "'/api/forecast/mining'" in audit
    assert "name: 'forecast-mining', hash: '#/research?tab=events'" in audit
    for scenario in (
        "forecast-loading-375",
        "forecast-empty-1440",
        "forecast-partial-failure-1440",
    ):
        assert scenario in audit
    for semantic_fixture in (
        "审计预增制造",
        "审计自愿科技",
        "审计低基数股份",
        "p_chg_min: null",
        "last_net: null",
        "nonrec_flag: true",
        "trap: true",
    ):
        assert semantic_fixture in audit
    for fixture_name in ("审计预增制造", "审计自愿科技", "审计低基数股份"):
        assert fixture_name not in production
