from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def _read(path: str) -> str:
    return (VUE / path).read_text(encoding="utf-8")


def test_vue_capital_tracking_route_is_reachable_without_migrating_siblings() -> None:
    app = _read("src/App.vue")
    navigation = _read("src/navigation.ts")

    assert "const isCapitalTracking = computed(() => routePath.value === 'today' && routeTab.value === 'capital')" in app
    assert '<CapitalTrackingPage' in app
    assert 'v-else-if="isCapitalTracking"' in app
    assert "tab: 'capital', label: '资金追踪'" in navigation


def test_vue_capital_tracking_uses_only_real_capital_endpoints() -> None:
    source = _read("src/composables/useCapitalTracking.ts")

    assert "'/capital/trends?days=1'" in source
    assert "'/capital/snapshot'" in source
    for unrelated in ("/market/flow", "/market/stockflow", "/timing", "/pulse", "/cockpit"):
        assert unrelated not in source
    assert "getJson<CapitalTrendsResponse>" in source
    assert "postJson<CapitalSnapshotResponse>" in source


def test_vue_capital_tracking_preserves_units_missing_empty_and_stale_semantics() -> None:
    page = _read("src/components/CapitalTrackingPage.vue")
    ranking = _read("src/components/CapitalRankingPanel.vue")
    trend = _read("src/components/CapitalTrendPanel.vue")
    formatting = _read("src/utils/capitalFormat.ts")

    assert "当前无资金快照" in page
    assert "接口已返回真实空数组" in ranking
    assert "非今日 · 最近已发布交易日" in ranking
    assert "tdx_raw_unverified" in formatting
    assert "原始值 · 未核验" in ranking
    assert "空值不会按 0 画入趋势" in trend
    assert "if (value == null || value === '') return null" in formatting
    assert "if (value == null) return '—'" in formatting
    assert "return `${value.toFixed(0)} 元`" in formatting
    assert "?? 0" not in page + ranking + trend + formatting
    assert "|| 0" not in page + ranking + trend + formatting


def test_vue_capital_tracking_cleans_each_request_timer_and_listener() -> None:
    source = _read("src/composables/useCapitalTracking.ts")

    assert "let trendsController: AbortController | null = null" in source
    assert "let snapshotController: AbortController | null = null" in source
    assert "trendsController?.abort()" in source
    assert "snapshotController?.abort()" in source
    assert "if (refreshTimer) clearInterval(refreshTimer)" in source
    assert "document.addEventListener('visibilitychange', onVisibilityChange)" in source
    assert "document.removeEventListener('visibilitychange', onVisibilityChange)" in source
    assert "if (!alive || controller !== trendsController) return" in source
    assert "if (!alive || controller !== snapshotController) return" in source
    assert "if (snapshotting.value) return" in source


def test_vue_capital_tracking_uses_safe_nodes_and_native_interactions() -> None:
    sources = "\n".join(
        _read(path)
        for path in (
            "src/components/CapitalTrackingPage.vue",
            "src/components/CapitalRankingPanel.vue",
            "src/components/CapitalTrendPanel.vue",
        )
    )

    assert "v-html" not in sources
    assert 'type="button"' in sources
    assert "class=\"primary-button capital-snapshot-button\"" in sources
    assert "@click=\"takeSnapshot\"" in sources
    assert '<a v-if="kind === \'stock\' && row.code"' in sources
    assert 'role="button"' not in sources
    assert 'aria-live="polite"' in sources
    assert 'tabindex="0"' in sources


def test_vue_capital_tracking_responsive_contract_and_audit_scenarios() -> None:
    page = _read("src/components/CapitalTrackingPage.vue")
    ranking = _read("src/components/CapitalRankingPanel.vue")
    trend = _read("src/components/CapitalTrendPanel.vue")
    audit = _read("scripts/audit-ui.mjs")

    assert ".capital-page { display: grid; min-width: 0;" in page
    assert '<header class="capital-intro">' in page
    assert "{{ snapshotting ? '采集中…' : '采集真实快照' }}" in page
    assert "CapitalDecisionSummary" not in page
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in page
    assert "@media (max-width: 720px)" in page + ranking + trend
    assert ".capital-table-wrap { max-width: 100%; overflow-x: auto; }" in ranking
    assert "min-height: 44px" in ranking + trend
    assert "@media (prefers-reduced-motion: reduce)" in page
    assert "name: 'capital', hash: '#/today?tab=capital'" in audit
    for scenario in ("capital-loading-375", "capital-empty-1440", "capital-partial-failure-1440"):
        assert scenario in audit
    assert "'/api/capital/trends'" in audit
    assert "'/api/capital/snapshot'" in audit


def test_capital_audit_fixture_stays_out_of_production_components() -> None:
    audit = _read("scripts/audit-ui.mjs")
    production = "\n".join(
        _read(path)
        for path in (
            "src/components/CapitalTrackingPage.vue",
            "src/components/CapitalRankingPanel.vue",
            "src/components/CapitalTrendPanel.vue",
            "src/composables/useCapitalTracking.ts",
            "src/types/capitalTracking.ts",
            "src/utils/capitalFormat.ts",
        )
    )

    assert "工业金属" in audit and "紫金矿业" in audit
    assert "工业金属" not in production
    assert "紫金矿业" not in production
