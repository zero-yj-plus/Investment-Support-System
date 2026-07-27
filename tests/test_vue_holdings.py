from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def _read(path: str) -> str:
    return (VUE / path).read_text(encoding="utf-8")


def test_vue_holdings_route_is_reachable_and_uses_real_contracts() -> None:
    app = _read("src/App.vue")
    navigation = _read("src/navigation.ts")
    composable = _read("src/composables/useHoldings.ts")

    assert "path: 'risk', tab: 'holdings'" in navigation
    assert "routePath.value === 'risk' && routeTab.value === 'holdings'" in app
    assert "<HoldingsPage" in app
    for endpoint in (
        "'/holdings'",
        "`/quotes?codes=${encodeURIComponent(codes.join(','))}`",
        "`/quote/${input.code}`",
        "putJson<HoldingsResponse>('/holdings'",
    ):
        assert endpoint in composable
    for unrelated in ("/cockpit", "/pulse", "/recommendations", "/market/flow"):
        assert unrelated not in composable


def test_vue_holdings_preserves_missing_empty_stale_and_overwrite_safety() -> None:
    page = _read("src/components/HoldingsPage.vue")
    summary = _read("src/components/HoldingsSummary.vue")
    table = _read("src/components/HoldingsTable.vue")
    composable = _read("src/composables/useHoldings.ts")
    formatter = _read("src/utils/holdingsFormat.ts")

    combined = page + summary + table + composable + formatter
    for label in (
        "真实空数组",
        "持仓账本暂不可用",
        "报价局部失败，持仓账本仍可编辑",
        "陈旧报价",
        "持仓总市值",
        "投入成本",
        "成本价 × 股数",
        "持仓构成",
        "缺失值不补 0",
    ):
        assert label in combined
    assert "if (!loaded.value) return '持仓账本尚未成功加载，保存已锁定。'" in composable
    assert "portfolio_total_asset" not in composable
    assert "totalAssetText" not in composable
    assert "updateTotalAsset" not in composable
    assert "holdingSharePct" in composable
    assert "quoteCoverageComplete.value" in composable
    assert "missingQuoteCodes.value = codes" in composable
    assert "value == null ? '—'" in formatter
    for source in (page, summary, table, composable, formatter):
        assert "v-html" not in source
        assert "|| 0" not in source
        assert "?? 0" not in source


def test_vue_holdings_cleans_lifecycle_and_prevents_duplicate_actions() -> None:
    page = _read("src/components/HoldingsPage.vue")
    editor = _read("src/components/HoldingEditor.vue")
    composable = _read("src/composables/useHoldings.ts")
    api = _read("src/services/api.ts")

    for controller in (
        "holdingsController?.abort()",
        "quotesController?.abort()",
        "saveController?.abort()",
        "verifyController?.abort()",
    ):
        assert controller in composable
    assert "let alive = false" in composable
    assert "if (!alive || controller !==" in composable
    assert "clearInterval(quoteTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable
    assert "removeEventListener('beforeunload'" in composable
    assert "if (saving.value) return" in composable
    assert "if (adding.value) return false" in composable
    assert "method: 'PUT'" in api
    assert "onBeforeUnmount(() => emit('refreshState', false))" in page
    for source in (page, editor):
        assert '<button' in source
        assert 'role="button"' not in source
    summary = _read("src/components/HoldingsSummary.vue")
    assert '>添加持仓</button>' in summary
    assert ":class=\"dirty ? 'secondary-button' : 'primary-button'\"" in summary


def test_vue_holdings_responsive_accessible_semantics_and_ui_audit() -> None:
    page = _read("src/components/HoldingsPage.vue")
    summary = _read("src/components/HoldingsSummary.vue")
    table = _read("src/components/HoldingsTable.vue")
    editor = _read("src/components/HoldingEditor.vue")
    audit = _read("scripts/audit-ui.mjs")

    assert 'tabindex="0" role="region" aria-label="持仓明细宽表，可横向滚动"' in table
    assert "@media (max-width: 720px)" in page + table
    assert "@media (max-width: 520px)" in summary + editor
    assert "@media (prefers-reduced-motion: reduce)" in page
    assert "min-height: 44px" in page + table + editor
    for source in (page, summary, table, editor):
        assert "box-shadow" not in source
        assert "linear-gradient" not in source
    assert "hash: '#/risk?tab=holdings'" in audit
    assert "'/api/holdings'" in audit and "'/api/quotes'" in audit
    for screenshot in (
        "holdings-loading-375.png",
        "holdings-empty-1440.png",
        "holdings-partial-failure-1440.png",
    ):
        assert screenshot in audit
    assert "holdingsLoadingStateComplete" in audit
    assert "holdingsEmptyStateComplete" in audit
    assert "holdingsFailureStateComplete" in audit
