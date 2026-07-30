from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def _read(path: str) -> str:
    return (VUE / path).read_text(encoding="utf-8")


def test_vue_watchlist_route_is_reachable_and_uses_only_real_contracts() -> None:
    app = _read("src/App.vue")
    navigation = _read("src/navigation.ts")
    composable = _read("src/composables/useWatchlist.ts")
    api = _read("src/services/api.ts")

    assert "path: 'risk', tab: 'watchlist'" in navigation
    assert "routePath.value === 'risk' && routeTab.value === 'watchlist'" in app
    assert "<WatchlistPage" in app
    assert "if (isWatchlist.value && watchlistPage.value)" in app
    for endpoint in (
        "getJson<WatchlistResponse>('/watchlist'",
        "`/quote/${encodeURIComponent(code)}`",
        "postJson<WatchlistMutationResponse>('/watchlist'",
        "`/watchlist/${encodeURIComponent(existingId)}`",
        "`/watchlist/${encodeURIComponent(id)}`",
        "postJson<WatchlistMutationResponse>('/watchlist/recalculate'",
    ):
        assert endpoint in composable
    for unrelated in ("/reports/", "/recommendations", "/screen", "/market/flow", "/cockpit"):
        assert unrelated not in composable
    assert "method: 'DELETE'" in api


def test_vue_watchlist_preserves_units_and_all_missing_semantics() -> None:
    page = _read("src/components/WatchlistPage.vue")
    row = _read("src/components/WatchlistItemRow.vue")
    editor = _read("src/components/WatchlistEditor.vue")
    composable = _read("src/composables/useWatchlist.ts")
    formatter = _read("src/utils/watchlistFormat.ts")
    combined = page + row + editor + composable + formatter

    for label in (
        "真实空数组",
        "观察池暂不可用",
        "接口返回局部缺失",
        "降级快照",
        "陈旧",
        "报价缺失",
        "评级缺失",
        "状态未提供",
        "缺失与降级原因",
        "任何缺失值都不补 0",
        "单位：元",
        "%",
    ):
        assert label in combined
    assert "if (!Array.isArray(payload.items))" in composable
    assert "payload.recognized === false" in composable
    assert "payload.recognized !== true" in composable
    assert "quoteAvailable: payload.quote?.price != null" in composable
    assert "result.zone_status === 'queued'" in composable
    assert "尚未完成" in composable
    assert "parsed == null ? '—'" in formatter
    for source in (page, row, editor, composable, formatter):
        assert "|| 0" not in source
        assert "?? 0" not in source
    assert "riskLevel" not in editor
    assert "risk_level:" not in editor
    assert "风险边界等级由服务端计算" in editor


def test_vue_watchlist_cleans_lifecycle_and_prevents_duplicate_actions() -> None:
    page = _read("src/components/WatchlistPage.vue")
    row = _read("src/components/WatchlistItemRow.vue")
    editor = _read("src/components/WatchlistEditor.vue")
    composable = _read("src/composables/useWatchlist.ts")

    for controller in (
        "listController?.abort()",
        "verifyController?.abort()",
        "saveController?.abort()",
        "deleteController?.abort()",
        "recalculateController?.abort()",
    ):
        assert controller in composable
    assert "let alive = false" in composable
    assert "if (!alive || controller !==" in composable
    assert "clearInterval(refreshTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable
    assert "if (refreshing.value) return" in composable
    assert "if (saving.value || verifying.value) return false" in composable
    assert "if (!id || deletingId.value) return false" in composable
    assert "if (recalculating.value) return" in composable
    assert "onBeforeUnmount(() => emit('refreshState', false))" in page
    for source in (page, row, editor):
        assert '<button' in source
        assert 'role="button"' not in source
    assert ':href="diagnosisHash"' in row


def test_vue_watchlist_uses_safe_text_and_responsive_accessible_semantics() -> None:
    page = _read("src/components/WatchlistPage.vue")
    row = _read("src/components/WatchlistItemRow.vue")
    editor = _read("src/components/WatchlistEditor.vue")
    combined = page + row + editor

    for source in (page, row, editor):
        assert "v-html" not in source
        assert "box-shadow" not in source
        assert "linear-gradient" not in source
    assert "white-space: pre-wrap" in row
    assert 'aria-live="polite"' in page
    assert 'role="alert"' in page and 'role="status"' in page
    assert '<form @submit.prevent="submit">' in editor
    assert 'required' in editor and 'inputmode="numeric"' in editor
    assert '<dialog' in editor and "showModal()" in editor
    assert '@cancel.prevent="requestClose"' in editor
    assert "position: sticky" in editor and "bottom: -14px" in editor
    assert "repeat(6, minmax(0, 1fr))" in editor
    assert '<Teleport to="body">' in page
    assert "opener?.focus()" in page
    assert 'class="watchlist-list__head"' in page
    assert "--watchlist-head-text: #111827" in page
    assert "grid-template-columns: var(--watchlist-columns)" in page + row
    assert ':hidden="!expanded"' in row and ':aria-expanded="expanded"' in row
    assert "服务端评级" not in row
    assert 'place-items: center' in row and 'font: 800 24px/1' in row
    assert "min-height: 44px" in row + editor
    assert "@media (max-width: 900px)" in page + row
    assert "@media (max-width: 680px)" in row
    assert "@media (prefers-reduced-motion: reduce)" in page
    for fixture_value in ("紫金矿业", "宁德时代", "18.68", "282.10"):
        assert fixture_value not in combined


def test_vue_watchlist_places_actions_in_title_bar_without_summary_panel() -> None:
    page = _read("src/components/WatchlistPage.vue")
    header = page.split('<header class="watchlist-intro">', 1)[1].split("</header>", 1)[0]

    assert '<div class="watchlist-intro__actions">' in header
    assert "添加观察" in header
    assert "重算双周期区间" in header
    assert header.index("重算双周期区间") < header.index("添加观察")
    assert "grid-template-columns: repeat(2, max-content)" in page
    assert "watchlist-summary" not in page
    assert "当前确定性结论" not in page
    assert "服务端 A 级" not in page


def test_vue_watchlist_ui_audit_has_real_fixture_and_required_states() -> None:
    audit = _read("scripts/audit-ui.mjs")

    assert "hash: '#/risk?tab=watchlist'" in audit
    assert "'/api/watchlist'" in audit
    for semantic in (
        "manual_target_low",
        "effective_target_low",
        "ideal_buy_zones",
        "algorithm_version",
        "quote_unavailable",
    ):
        assert semantic in audit
    for screenshot in (
        "watchlist-loading-375.png",
        "watchlist-empty-1440.png",
        "watchlist-add-modal-1440.png",
        "watchlist-partial-failure-1440.png",
    ):
        assert screenshot in audit
    assert "watchlistLoadingStateComplete" in audit
    assert "watchlistEmptyStateComplete" in audit
    assert "watchlistModalStateComplete" in audit
    assert "saveVisible" in audit
    assert "watchlistFailureStateComplete" in audit
    assert "compactRowsAligned" in audit
    assert "`${auditedPage.name}-${viewport.name}.png`" in audit
    assert "`${auditedPage.name}-wide-1440-light.png`" in audit
