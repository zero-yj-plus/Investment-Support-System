from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def _read(path: str) -> str:
    return (VUE / path).read_text(encoding="utf-8")


def test_vue_holdings_route_and_shadow_api_contracts() -> None:
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
        "'/holdings/risk'",
        "'/holdings/risk/refresh'",
    ):
        assert endpoint in composable
    assert "/risk-plan/reset" not in composable
    assert "/adjustment" not in composable
    assert "await refreshRisk(true)" in composable


def test_vue_holdings_preserves_missing_stale_and_exact_portfolio_formulas() -> None:
    page = _read("src/components/HoldingsPage.vue")
    table = _read("src/components/HoldingsTable.vue")
    composable = _read("src/composables/useHoldings.ts")
    formatter = _read("src/utils/holdingsFormat.ts")
    combined = page + table + composable + formatter

    for label in (
        "持仓账本暂不可用",
        "报价局部失败，持仓账本仍可编辑",
        "陈旧报价",
        "持仓总市值",
        "今日总盈亏率",
        "总浮动盈亏率",
        "最大单只持仓",
        "最大行业集中度",
        "风险结论已陈旧",
        "缺失持仓不会补造风险线或动作",
    ):
        assert label in combined
    assert "row.cost * row.shares" in formatter
    assert "previousTotal += row.marketValue / (1 + rate)" in page
    assert "covered += 1" in page
    assert "quoteCoverageComplete.value" in composable
    assert "portfolio_total_asset" not in composable
    assert "value == null ? '—'" in formatter
    assert "algorithmRisk?.state || 'normal'" in formatter
    for source in (page, table, composable, formatter):
        assert "v-html" not in source
        assert "|| 0" not in source
        assert "?? 0" not in source


def test_vue_holdings_immediately_persists_add_edit_delete_and_rolls_back() -> None:
    page = _read("src/components/HoldingsPage.vue")
    editor = _read("src/components/HoldingEditor.vue")
    composable = _read("src/composables/useHoldings.ts")

    for controller in (
        "holdingsController?.abort()",
        "quotesController?.abort()",
        "riskController?.abort()",
        "saveController?.abort()",
        "verifyController?.abort()",
    ):
        assert controller in composable
    assert "if (!alive || controller !==" in composable
    assert "clearInterval(quoteTimer)" in composable
    assert "clearInterval(riskTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable
    assert "removeEventListener('beforeunload'" in composable
    assert "const previousHoldings = holdings.value" in composable
    assert "holdings.value = previousHoldings" in composable
    assert "const previous = { ...row }" in composable
    assert "await persistHoldings(" not in composable
    assert "const saved = await save()" in composable
    assert "保存持仓变更" not in page
    assert '@submit="handleEditorSubmit"' in page
    assert '@remove="handleEditorRemove"' in page
    assert "持仓最多 50 条" in composable
    assert "stopLossPrice" in editor and "takeProfitPrice" in editor
    assert "horizon" in editor and "entryType" in editor


def test_vue_holdings_uses_top_layer_dialog_and_restores_focus() -> None:
    editor = _read("src/components/HoldingEditor.vue")

    assert '<Teleport to="body">' in editor
    assert "<dialog" in editor
    assert 'aria-modal="true"' in editor
    assert 'aria-labelledby="holding-dialog-title"' in editor
    assert '@cancel.prevent="requestClose"' in editor
    assert "dialog.value.showModal()" in editor
    assert "editorOpener?.focus()" in editor
    assert '@click="handleBackdrop"' not in editor
    assert "holding-dialog::backdrop" in editor
    assert "min-height: 44px" in editor
    assert ":focus-visible" in editor


def test_vue_holdings_matches_summary_and_full_width_action_card_structure() -> None:
    page = _read("src/components/HoldingsPage.vue")
    table = _read("src/components/HoldingsTable.vue")

    for label in (
        "组合行动",
        "逐笔持仓",
        "为什么这样判断",
        "现在怎么做",
        "算法风险线",
        "持仓市值",
        "持仓成本",
        "浮动盈亏",
    ):
        assert label in page + table
    assert "grid-template-columns: repeat(5, minmax(0, 1fr))" in page
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in table
    assert "document.addEventListener('click', closeEvidenceOnOutsideClick)" in table
    assert "details.contains(target)" in table
    assert "position-card:has(.evidence-popover[open])" in table
    assert "@media (max-width: 720px)" in page
    assert "@media (max-width: 430px)" in table
    for source in (page, table, _read("src/components/HoldingEditor.vue")):
        assert "box-shadow" not in source
        assert "linear-gradient" not in source
    assert "min-height: 44px" in page + table
    assert "✎" not in table
    assert "↘" not in table
    assert "✓" not in page + table


def test_retired_holdings_risk_cards_keeps_compatibility_tombstone() -> None:
    source = _read("src/components/HoldingsRiskCards.vue")
    assert "兼容墓碑" in source
    assert "HoldingsTable" in source
