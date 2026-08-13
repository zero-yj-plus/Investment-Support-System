from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue" / "src"


def _read(relative: str) -> str:
    return (VUE / relative).read_text(encoding="utf-8")


def test_limitup_tracker_navigation_and_hash_route_are_formally_wired() -> None:
    navigation = _read("navigation.ts")
    app = _read("App.vue")

    hotboard = navigation.index("tab: 'hotboard', label: '涨停题材'")
    tracker = navigation.index("tab: 'limitup', label: '涨停追踪'")
    mainline = navigation.index("tab: 'mainline', label: '主线深研'")
    assert hotboard < tracker < mainline
    assert "routePath.value === 'today' && routeTab.value === 'limitup'" in app
    assert "v-else-if=\"isLimitUpTracker\"" in app
    assert "ref=\"limitUpTrackerPage\"" in app


def test_limitup_tracker_uses_typed_service_methods_for_all_server_capabilities() -> None:
    api = _read("services/api.ts")
    types = _read("types/limitupTracker.ts")

    for endpoint in [
        "/market/limitup-tracker/dates",
        "/market/limitup-tracker?date=",
        "/market/limitup-tracker/backfill",
        "/market/limitup-tracker/backfill/",
    ]:
        assert endpoint in api
    for state in ["pending", "intraday", "final", "missing", "unavailable"]:
        assert f"'{state}'" in types
    assert "change_pct: number | null" in types


def test_limitup_tracker_date_switch_is_atomic_and_cleans_async_work() -> None:
    composable = _read("composables/useLimitUpTracker.ts")

    assert "const version = ++requestVersion" in composable
    assert "version !== requestVersion" in composable
    assert "snapshotController?.abort()" in composable
    assert "snapshot.value = payload\n      selectedDate.value = payload.selected_date" in composable
    assert "onBeforeUnmount" in composable
    assert "clearInterval(refreshTimer)" in composable
    assert "clearTimeout(pollTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable


def test_recent_20_is_a_popover_with_keyboard_and_outside_close() -> None:
    nav = _read("components/LimitUpSnapshotNav.vue")

    assert 'aria-controls="limitup-recent-20-panel"' in nav
    assert ':aria-expanded="open"' in nav
    assert "event.key === 'Escape'" in nav
    assert "!root.value.contains(event.target as Node)" in nav
    assert "position: absolute" in nav
    assert "dates.slice(0, 3)" in nav
    assert "最近20日" in nav and "补数据" in nav


def test_detail_table_has_exact_columns_t_window_sorting_and_scoped_mobile_scroll() -> None:
    table = _read("components/LimitUpTrackerTable.vue")
    labels = ["代码", "名称", "行业", "连板", "涨停日涨幅", "首封", "次日 T+1", "第三日 T+2"]
    positions = [table.index(f">{label}") for label in labels]

    assert positions == sorted(positions)
    assert "const visibleStocks = computed" in table
    assert "if (!sortKey.value) return props.stocks" in table
    assert "left.stock[key].change_pct" in table
    assert "if (leftValue == null) return 1" in table
    assert "if (rightValue == null) return -1" in table
    assert "left.index - right.index" in table
    for key in ["t1", "t2"]:
        assert f':aria-sort="ariaSort(\'{key}\')"' in table
        assert f'@click="sortBy(\'{key}\')"' in table
    assert "sortDirection.value = 'descending'" in table
    assert "tracker-sort-button.is-active" in table
    assert "tracker-sort-icon" in table and "'↕'" in table
    assert "table-layout: fixed" in table
    assert ".tracker-table-wrap { overflow-x: auto" in table
    assert ".tracker-table { min-width: 880px" in table
    assert "font-variant-numeric" not in table  # 统一复用全局 .mono / tabular-nums
    assert "board-label" in table and "lianban_label" in table


def test_page_exposes_all_metric_states_and_truthful_provenance() -> None:
    page = _read("components/LimitUpTrackerPage.vue")
    stats = _read("components/LimitUpTrackerStats.vue")

    for label in ["次日均涨", "次日红盘", "次日晋级", "第三日均涨", "第三日红盘"]:
        assert label in stats
    for label in ["数据日期", "生成时间", "行情时点", "数据来源", "缺失、降级与覆盖"]:
        assert label in page
    assert "盘中快照／未定稿" in page
    assert "收盘定稿" in page
    assert "不会生成示例数据" in page
    assert 'aria-live="polite"' in page
    assert ':aria-busy="busy"' in page
    assert "prefers-reduced-motion: reduce" in page


def test_backend_routes_and_append_only_tracker_are_present() -> None:
    routes = (ROOT / "hermes_trader" / "api" / "routes.py").read_text(encoding="utf-8")
    tracker = (ROOT / "hermes_trader" / "data" / "limitup_tracker.py").read_text(encoding="utf-8")

    for route in [
        '@router.get("/market/limitup-tracker/dates")',
        '@router.get("/market/limitup-tracker")',
        '@router.post("/market/limitup-tracker/backfill")',
        '@router.get("/market/limitup-tracker/backfill/{job_id}")',
    ]:
        assert route in routes
    assert "os.O_APPEND | os.O_CREAT | os.O_WRONLY" in tracker
    assert '"event": "baseline_frozen"' in tracker
    assert '"event": "window_observed"' in tracker
    assert "value if metric_state in {\"final\", \"intraday\"} else None" in tracker
