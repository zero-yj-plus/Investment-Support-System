from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE_ROOT = ROOT / "frontend-vue"


def _read(relative: str) -> str:
    return (VUE_ROOT / relative).read_text(encoding="utf-8")


def test_vue_decision_overview_uses_existing_real_data_contracts() -> None:
    composable = _read("src/composables/useDecisionOverview.ts")
    vite_config = _read("vite.config.ts")

    for endpoint in (
        "/shell-status",
        "/pulse",
        "/market/overview_cached",
        "/sysrisk",
        "/market/mainlines",
    ):
        assert endpoint in composable
    assert "Promise.allSettled(tasks)" in composable
    assert "controller?.abort()" in composable
    assert "clearInterval(refreshTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable
    assert "'http://127.0.0.1:18869'" in vite_config


def test_vue_market_evidence_is_progressive_and_uses_real_endpoints() -> None:
    app = _read("src/App.vue")
    evidence = _read("src/components/MarketEvidence.vue")
    composable = _read("src/composables/useMarketEvidence.ts")

    assert app.index("<MainlineOverview") < app.index("<MarketEvidence")
    for endpoint in (
        "/sentiment/today",
        "/market/flow",
        "/market/stockflow?n=10",
        "/market/sectors",
        "/market/multiperiod?n=12",
    ):
        assert endpoint in composable
    assert "IntersectionObserver" in evidence
    assert "rootMargin: '520px 0px'" in evidence
    assert "ensure(['sentiment', 'flow', 'stockflow'])" in evidence
    assert "ensure(['sectors', 'multiperiod'])" in evidence
    assert "observer?.disconnect()" in evidence
    assert "controllers.forEach((controller) => controller.abort())" in composable
    assert "clearInterval(refreshTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable


def test_vue_market_evidence_preserves_missing_semantics_and_exact_values() -> None:
    limit = _read("src/components/LimitEvidence.vue")
    flow = _read("src/components/CapitalFlowOverview.vue")
    heatmap = _read("src/components/SectorHeatmap.vue")
    multiperiod = _read("src/components/MultiperiodTable.vue")

    assert "limit_down_pool" in limit
    assert "limit_down_missing" in limit
    assert "今日暂无跌停风险" in limit
    assert "跌停明细暂不可用" in limit
    assert "不以成交额或零值冒充净流入" in flow
    assert "面积 = 成交额" in heatmap
    assert "右侧列表提供精确数值" in heatmap
    assert "row.kind === 'industry'" in heatmap
    assert "Math.abs(row.change_pct as number) * 1e9" in heatmap
    assert "该权重仅用于矩形布局" in heatmap
    assert "面积改用 |涨跌幅|" in heatmap
    assert "行业成交额及涨跌幅均缺失，无法生成面积矩阵" in heatmap
    audit = _read("scripts/audit-ui.mjs")
    assert "{ kind: 'industry', name: '游戏', amount: null, change_pct: -2.86 }" in audit
    assert "heatmapFallbackVisible" in audit
    assert "isLargeTile(item) && finite(item.row.change_pct)" in heatmap
    assert "heat-name--large" in heatmap
    assert "heat-name--compact" in heatmap
    assert "heat-value--large" in heatmap
    assert "heatmapLabelHierarchy" in audit
    assert "item.width >= 88 && item.height >= 56 && item.width * item.height >= 10_000" in heatmap
    assert "promotedNarrowTiles" in audit
    assert "['能源金属', '电网设备']" in audit
    styles = _read("src/styles.css")
    assert ".heat-name--large { font-size: 20px" in styles
    assert ".heat-name--compact { font-size: 12px" in styles
    assert ".heat-value--large { font-size: 16px" in styles
    assert ".heat-gradient--up .heat-stop--base { stop-color: var(--up); }" in styles
    assert ".heat-gradient--down .heat-stop--base { stop-color: var(--down); }" in styles
    assert 'id="heat-up-gradient"' in heatmap
    assert 'id="heat-down-gradient"' in heatmap
    assert 'x1="0%"' in heatmap and 'x2="100%"' in heatmap
    assert 'class="heat-stop heat-stop--base" offset="0%"' in heatmap
    assert 'offset="54%"' not in heatmap
    assert 'fill: url("#heat-up-gradient")' in styles
    assert 'fill: url("#heat-down-gradient")' in styles
    assert ".heat-gradient--up .heat-stop--light { stop-color: #ef7777; }" in styles
    assert ".heat-gradient--down .heat-stop--light { stop-color: #6be791; }" in styles
    assert ".heat-name { fill: #fff" in styles
    assert ".heat-value { fill: #fff" in styles
    assert "item.d20" in multiperiod
    assert "涨幅榜标的池或免费日线样本暂不可用" in multiperiod
    for source in (limit, flow, heatmap, multiperiod):
        assert "|| 0" not in source
        assert "?? 0" not in source


def test_vue_stock_flow_places_comparable_bars_between_name_and_amount() -> None:
    flow = _read("src/components/CapitalFlowOverview.vue")
    styles = _read("src/styles.css")

    assert "const stockMaxAbs = computed" in flow
    assert 'class="stock-flow-track" aria-hidden="true"' in flow
    assert '</span>\n              <span class="stock-flow-track" aria-hidden="true"' in flow
    assert '</i></span>\n              <b class="is-up mono">' in flow
    assert ".stock-flow-row { display: grid; grid-template-columns:" in styles
    assert ".stock-flow-track .is-inflow" in styles
    assert ".stock-flow-track .is-outflow" in styles


def test_vue_limit_evidence_displays_market_turnover_in_trillion_yuan() -> None:
    limit = _read("src/components/LimitEvidence.vue")

    assert "function marketTurnover(value?: number | null)" in limit
    assert "(value / 1e8).toFixed(2)" in limit
    assert "万亿`" in limit
    assert "两市成交 {{ marketTurnover(market?.total_amount) }}" in limit


def test_vue_market_environment_displays_turnover_in_trillion_yuan() -> None:
    market = _read("src/components/MarketOverview.vue")

    assert "function formatAmount(value?: number | null)" in market
    assert "(value / 1e8).toFixed(2)" in market
    assert "成交额 {{ formatAmount(market?.total_amount) }}" in market
    assert "toFixed(0)} 亿" not in market


def test_vue_decision_overview_fails_closed_when_decision_data_is_missing() -> None:
    composable = _read("src/composables/useDecisionOverview.ts")
    command = _read("src/components/DecisionCommand.vue")
    market = _read("src/components/MarketOverview.vue")
    risk = _read("src/components/RiskOverview.vue")
    mainline = _read("src/components/MainlineOverview.vue")

    assert "payload.ok !== false" in composable
    for quality_field in ("payload.missing?.length", "payload.missing_fields?.length", "payload.degraded_factors?.length"):
        assert quality_field in composable
    assert "payload.stale" in composable
    assert "关键决策数据未连接，本卡暂停生成新结论" in command
    assert "不依据缺失数据新增或补仓" in command
    assert "数据质量不足时不强行给方向" in command
    assert "当前不生成市场方向判断" in market
    assert "市场宽度比例暂不可用" in market
    assert "当前不据此新增风险暴露" in risk
    assert "score == null ? undefined" in mainline
    assert "-webkit-line-clamp" not in _read("src/styles.css")


def test_vue_decision_overview_has_responsive_and_accessible_shell() -> None:
    app = _read("src/App.vue")
    sidebar = _read("src/components/SidebarNavigation.vue")
    risk = _read("src/components/RiskOverview.vue")
    styles = _read("src/styles.css")

    assert 'class="skip-link" href="#main-content"' in app
    assert 'id="main-content" tabindex="-1"' in app
    assert ':aria-expanded="menuOpen"' in app
    assert "firstItemFocused" in _read("scripts/audit-ui.mjs")
    assert "menuFocusRestored" in _read("scripts/audit-ui.mjs")
    assert ':aria-expanded="openGroupId === group.id"' in sidebar
    assert ':aria-controls="`nav-children-${group.id}`"' in sidebar
    assert ':aria-current="childIsActive(child.path, child.tab) ? \'page\' : undefined"' in sidebar
    assert 'class="topbar__center"' in app
    assert "label: '交易中'" in app
    assert "market_session" in app
    assert ':aria-expanded="expanded"' in risk
    assert 'aria-controls="risk-evidence"' in risk
    assert "情绪温度" in risk
    assert "纯公式 · 定格值" not in risk
    assert 'role="progressbar"' in risk
    assert "risk-step-${step}" in risk
    for breakpoint in (1240, 1024, 900, 720, 520):
        assert f"@media (max-width: {breakpoint}px)" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert "min-width: 44px" in styles
    assert "min-height: 44px" in styles
    assert "--bg: #111a27" in styles
    assert ".topbar__center" in styles


def test_vue_sidebar_rebuilds_the_grouped_desktop_workspace_contract() -> None:
    app = _read("src/App.vue")
    sidebar = _read("src/components/SidebarNavigation.vue")
    navigation = _read("src/navigation.ts")
    styles = _read("src/styles.css")

    for label in ("今日决策", "市场结构", "持仓风控", "研究中心", "历史与知识"):
        assert f"label: '{label}'" in navigation
    for child in (
        "决策总览", "盘前报告", "尾盘决策", "今日复盘", "次日策略",
        "涨停题材", "主线深研", "资金追踪", "资金择时", "主力结构", "舆情事件",
        "我的持仓", "观察池", "模拟执行", "问股", "个股诊断", "条件选股",
        "每日量化选股", "事件掘金", "产业链分析", "公司研究", "海外市场",
        "重组拆解", "宏观风险", "全部报告", "知识库",
    ):
        assert f"label: '{child}'" in navigation
    assert "更多功能" in sidebar
    assert "buildNavigationHash(child.path, child.tab)" in sidebar
    assert "openGroupId.value === group.id ? null : group.id" in sidebar
    assert "wudi-vue-sidebar-collapsed" in app
    assert "sidebar-collapsed" in app
    assert ".app-shell.sidebar-collapsed" in styles
    assert ".sidebar.is-collapsed .nav-children" in styles
    assert "box-shadow" not in "\n".join(
        line for line in styles.splitlines()
        if any(token in line for token in ("nav-", "sidebar", "brand"))
    )


def test_vue_market_and_risk_colors_follow_visual_contract() -> None:
    market = _read("src/components/MarketOverview.vue")
    styles = _read("src/styles.css")

    assert '<strong :class="item.change_pct == null ? \'is-flat\' : item.change_pct >= 0 ? \'is-up\' : \'is-down\'">' in market
    for level, color in {
        1: "#1d4ed8",
        2: "#0f766e",
        3: "#a16207",
        4: "#c2410c",
        5: "#be123c",
    }.items():
        assert f"--risk-{level}: {color}" in styles
    assert "--up: #d7193f" in styles
    assert "--down: #007a4d" in styles


def test_vue_overview_swaps_market_cards_without_swapping_their_sizes() -> None:
    app = _read("src/App.vue")
    styles = _read("src/styles.css")

    assert app.index("<RiskOverview") < app.index("<MarketOverview")
    assert "grid-template-columns: minmax(300px, 332px) minmax(0, 1fr)" in styles
    assert "grid-template-columns: 128px minmax(180px, 1fr) 104px 76px" in styles


def test_vue_market_state_centers_and_enlarges_primary_readings() -> None:
    styles = _read("src/styles.css")

    assert ".risk-hero,.emotion-hero { position:relative; display:flex; flex-direction:column; align-items:center; justify-content:center" in styles
    assert '.risk-score strong,.emotion-score strong { font: 760 40px/1 "SFMono-Regular", Consolas, monospace; }' in styles
    assert ".risk-score span,.emotion-score span { color:var(--text-3); font-size:14px; }" in styles
    assert ".state-score-caption b { color:var(--text); font-size:16px" in styles
    assert ".state-score-caption span { color:var(--text-3); font-size:14px; }" in styles


def test_vue_market_breadth_numbers_outweigh_their_labels() -> None:
    styles = _read("src/styles.css")

    assert '.breadth__labels b, .breadth__facts b { color:var(--text); font:760 16px/1.25 "SFMono-Regular", Consolas, monospace;' in styles
    assert ".breadth__labels, .breadth__facts { display: flex" in styles
    assert "gap: 8px; font-size: 13px;" in styles


def test_vue_mainline_cards_emphasize_formed_status_and_primary_metrics() -> None:
    mainline = _read("src/components/MainlineOverview.vue")
    styles = _read("src/styles.css")

    assert "item.status === 'formed'" in mainline
    assert "<small v-if=\"isFormed(item)\">已形成</small>" in mainline
    assert "class=\"mainline-change\"" in mainline
    assert "'is-ladder-summary': item.label === '情绪链'" in mainline
    assert ".mainline-change { font-size: 18px; font-weight: 760;" in styles
    assert '.structure-score strong { color: var(--text); font: 760 22px/1.2 "SFMono-Regular", Consolas, monospace;' in styles
    assert ".mainline-card dl div { min-width: 0; padding: 7px 8px; border: 1px solid var(--border);" in styles
    assert ".mainline-summary.is-ladder-summary" in styles


def test_vue_typography_uses_larger_readable_scale() -> None:
    styles = _read("src/styles.css")

    assert "body { min-width: 320px; min-height: 100dvh; margin: 0; background: var(--bg); color: var(--text); font-size: 16px;" in styles
    assert "body { font-size: 18px; }" in styles
    assert "font-size: 9px" not in styles
    assert "font-size: 10px" not in styles
    assert "font-size: 11px" not in styles
    assert ".limit-stats-panel { display: flex" in styles
    assert "grid-template-rows: repeat(3, minmax(78px, 1fr))" in styles
    assert "clamp(18px, 1.45vw, 24px)" in styles
    assert "clamp(12px, .9vw, 14px)" in styles


def test_vue_ui_audit_covers_required_viewports_and_light_theme() -> None:
    audit = _read("scripts/audit-ui.mjs")

    for width in (375, 768, 1024, 1440):
        assert f"width: {width}" in audit
    assert "name: 'phone-landscape', width: 812" in audit
    assert "undersizedTargets" in audit
    assert "mainContentPresent" in audit
    assert "pageErrors" in audit
    assert "root.scrollWidth - window.innerWidth" in audit
    assert "wcag21aa" in audit
    assert "missingStateComplete" in audit
    assert "wudi-vue-theme', 'dark'" in audit
    assert "wudi-vue-theme', 'light'" in audit


def test_vue_premarket_report_uses_locked_report_and_live_verification_contracts() -> None:
    app = _read("src/App.vue")
    navigation = _read("src/navigation.ts")
    page = _read("src/components/PremarketReportPage.vue")
    snapshot = _read("src/components/PremarketDecisionSnapshot.vue")
    composable = _read("src/composables/usePremarketReport.ts")
    api = _read("src/services/api.ts")

    assert "path: 'premarket', tab: 'report'" in navigation
    assert 'v-if="isPremarket"' in app
    for endpoint in (
        "/reports/morning?date=",
        "/market/flow",
        "/reports/morning/generate",
        "/reports/morning/push",
    ):
        assert endpoint in composable
    assert "report?.cockpit" in page
    assert "方向主体按盘前时点锁定" in snapshot
    assert "盘中资金只作验证，不改写原预判" in snapshot
    assert "controller?.abort()" not in composable  # controllers are named by responsibility
    for controller in ("loadController?.abort()", "flowController?.abort()", "actionController?.abort()"):
        assert controller in composable
    assert "clearInterval(refreshTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable
    assert "readWithWatchdog" in api
    assert "90_000" in api
    assert "reader.cancel()" in api


def test_vue_premarket_report_renders_markdown_without_raw_html() -> None:
    document = _read("src/components/ReportDocument.vue")
    parser = _read("src/utils/reportMarkdown.ts")

    assert "v-html" not in document
    assert "parseReportMarkdown" in document
    assert "isTableDivider" in parser
    assert "ReportOutlineItem" in parser
    assert "report-section-${headingIndex}" in parser
    assert "报告数据表，可横向滚动" in document


def test_vue_premarket_report_has_dense_responsive_accessible_layout() -> None:
    page = _read("src/components/PremarketReportPage.vue")
    styles = _read("src/styles.css")

    assert 'aria-label="盘前报告章节"' in page
    assert 'role="alert"' in page
    assert 'role="status"' in page
    assert 'class="panel report-outline" open' in page
    assert "grid-template-columns: 268px minmax(0, 1fr)" in styles
    assert ".report-outline { position: sticky" in styles
    assert ".report-table-wrap { max-width: 100%" in styles
    assert "@media print" in styles
    assert "box-shadow" not in "\n".join(
        line for line in styles.splitlines() if "premarket" in line or "report-" in line
    )


def test_vue_tail_decision_preserves_locked_report_and_evidence_contracts() -> None:
    app = _read("src/App.vue")
    page = _read("src/components/TailReportPage.vue")
    gate = _read("src/components/TailDecisionSnapshot.vue")
    track = _read("src/components/TailTrackRecord.vue")
    watchlist = _read("src/components/TailWatchlist.vue")
    composable = _read("src/composables/useTailDecision.ts")

    assert 'v-else-if="isTailDecision"' in app
    for endpoint in (
        "/reports/tail?date=",
        "/recommendations/stats",
        "/recommendations",
        "/watchlist",
        "/recommendations/verify",
        "/reports/tail/generate",
        "/reports/tail/push",
    ):
        assert endpoint in composable
    assert "14:52 正式定稿" in gate
    assert "当日不随实时波动回写" in gate
    assert "win_rate_unit !== 'percent'" in track
    assert "target_low == null || item.target_high == null" in watchlist
    assert 'href="#/research?tab=diagnosis"' in watchlist
    assert 'role="row"' not in watchlist
    assert "review.hit_status === 'hit'" in track
    assert "review.hit_status === 'miss'" in track
    assert 'aria-label="尾盘决策报告章节"' in page
    assert "v-html" not in page
    for controller in (
        "reportController?.abort()",
        "statsController?.abort()",
        "recommendationController?.abort()",
        "watchlistController?.abort()",
        "actionController?.abort()",
    ):
        assert controller in composable
    assert "clearInterval(refreshTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable


def test_vue_tail_decision_has_dense_responsive_accessible_layout() -> None:
    styles = _read("src/styles.css")

    assert ".tail-evidence-grid { display: grid" in styles
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)" in styles
    assert ".tail-gate__guardrails" in styles
    assert ".tail-watchlist-row" in styles
    assert "linear-gradient" not in "\n".join(
        line for line in styles.splitlines() if "tail-" in line
    )
    assert "box-shadow" not in "\n".join(
        line for line in styles.splitlines() if "tail-" in line
    )


def test_vue_close_review_route_uses_real_append_only_contracts() -> None:
    app = _read("src/App.vue")
    navigation = _read("src/navigation.ts")
    page = _read("src/components/CloseReviewPage.vue")
    advice = _read("src/components/CloseAdviceReview.vue")
    track = _read("src/components/CloseRecommendationReview.vue")
    composable = _read("src/composables/useCloseReview.ts")

    assert "path: 'review', tab: 'close'" in navigation
    assert 'v-else-if="isCloseReview"' in app
    assert "routePath.value === 'review' && routeTab.value === 'close'" in app
    for endpoint in (
        "/reports/close?date=",
        "/holdings/advice-review",
        "/recommendations/stats",
        "/recommendations",
        "/recommendations/verify",
        "/reports/close/generate",
        "/reports/close/push",
    ):
        assert endpoint in composable
    assert "report?.cockpit" in page
    assert "历史判断只追加、不回写" in page
    assert "win_rate_unit !== 'percent'" in track
    for state in ("waiting", "untriggered", "incomparable", "not_triggered", "insufficient_evidence", "hit", "partial_hit", "invalidated"):
        assert state in track
    assert "±0.5% 内为中性" in advice
    assert "today_price == null ? '未取得'" in advice
    assert "data?.missing?.join('；')" in advice
    for source in (page, advice, track):
        assert "v-html" not in source
        assert "|| 0" not in source
        assert "?? 0" not in source


def test_vue_close_review_cleans_lifecycle_and_uses_native_interactions() -> None:
    composable = _read("src/composables/useCloseReview.ts")
    page = _read("src/components/CloseReviewPage.vue")
    advice = _read("src/components/CloseAdviceReview.vue")
    track = _read("src/components/CloseRecommendationReview.vue")

    for controller in (
        "reportController?.abort()",
        "adviceController?.abort()",
        "statsController?.abort()",
        "recommendationController?.abort()",
        "generateController?.abort()",
        "pushController?.abort()",
        "verifyController?.abort()",
    ):
        assert controller in composable
    assert "let alive = false" in composable
    assert "if (!alive || controller !==" in composable
    assert "clearInterval(refreshTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable
    assert "reader.cancel()" in _read("src/services/api.ts")
    assert 'aria-label="今日复盘报告章节"' in page
    assert 'aria-label="昨日建议回检明细，可横向滚动"' in advice
    assert 'href="#/research?tab=diagnosis"' in advice
    assert 'href="#/research?tab=diagnosis"' in track
    assert '<button' in page and '<button' in advice and '<button' in track
    for source in (page, advice, track):
        assert 'role="button"' not in source


def test_vue_close_review_has_responsive_missing_and_accessible_states() -> None:
    page = _read("src/components/CloseReviewPage.vue")
    styles = _read("src/styles.css")

    assert "今日复盘正在加载" in page
    assert "今日复盘尚未定稿" in page
    assert "最新复盘刷新失败，当前保留已加载定稿" in page
    assert "缺失、空记录和传输失败分别展示" in page
    assert ".close-evidence-grid { display: grid" in styles
    assert "grid-template-columns: minmax(0, .92fr) minmax(0, 1.08fr)" in styles
    assert ".close-advice-table-wrap { max-width: 100%; overflow-x: auto; }" in styles
    assert ".close-advice-table td::before" in styles
    assert ".close-decision__guardrails" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert "box-shadow" not in "\n".join(
        line for line in styles.splitlines() if "close-" in line
    )
    assert "linear-gradient" not in "\n".join(
        line for line in styles.splitlines() if "close-" in line
    )


def test_vue_close_review_ui_audit_uses_semantic_fixture_and_failure_scenarios() -> None:
    audit = _read("scripts/audit-ui.mjs")

    assert "'/api/reports/close'" in audit
    assert "'/api/holdings/advice-review'" in audit
    assert "hash: '#/review?tab=close'" in audit
    assert "今日复盘 · 2026-07-22" in audit
    assert "review-loading-375.png" in audit
    assert "review-empty-1440.png" in audit
    assert "review-partial-failure-1440.png" in audit
    assert "reviewEmptyStateComplete" in audit
    assert "reviewPartialStateComplete" in audit
    assert "策略自证暂不可用" in audit


def test_vue_hotboard_route_uses_only_the_real_independent_contract() -> None:
    app = _read("src/App.vue")
    page = _read("src/components/HotboardPage.vue")
    composable = _read("src/composables/useHotboard.ts")
    types = _read("src/types/hotboard.ts")

    assert "routePath.value === 'today' && routeTab.value === 'hotboard'" in app
    assert 'v-else-if="isHotboard"' in app
    assert "if (isHotboard.value && hotboardPage.value)" in app
    assert "await Promise.resolve(hotboardPage.value.refresh())" in app
    assert "? reportRefreshing.value" in app
    assert "getJson<HotboardResponse>('/market/limitup/themes'" in composable
    assert "/market/limitup'" not in composable
    for field in (
        "reason_type", "limit_up_type", "high_days", "is_again", "suc_rate_pct",
        "first_time", "last_time", "seal_amount", "seal_volume", "turnover_rate", "circ_mv",
    ):
        assert field in types
    assert "不与驾驶舱主涨停池数字互相回填" in _read("src/components/HotboardThemeFilter.vue")
    assert "当前确定性结论" not in page
    assert "hotboard-decision" not in page


def test_vue_hotboard_preserves_missing_empty_degraded_and_failure_semantics() -> None:
    page = _read("src/components/HotboardPage.vue")
    table = _read("src/components/HotboardStockTable.vue")
    composable = _read("src/composables/useHotboard.ts")

    for label in ("暂不可用", "陈旧数据", "部分覆盖", "真实空结果", "当前无涨停题材数据"):
        assert label in page
    assert "payload.ok === false" in composable
    assert "Array.isArray(data.value?.stocks)" in composable
    assert "stock.price != null" in table
    assert "stock.suc_rate_pct == null" in table
    assert "stock.seal_amount == null" in table
    assert "stock.price || 0" not in table
    assert "stock.seal_amount || 0" not in table
    assert "页面不会用历史或示例数据补位" in page
    assert 'class="hotboard-stamp__coverage"' in page
    assert "hotboard-quality" not in page


def test_vue_hotboard_cleans_lifecycle_and_uses_native_safe_interactions() -> None:
    page = _read("src/components/HotboardPage.vue")
    filters = _read("src/components/HotboardThemeFilter.vue")
    table = _read("src/components/HotboardStockTable.vue")
    composable = _read("src/composables/useHotboard.ts")

    assert "listController?.abort()" in composable
    assert "let alive = false" in composable
    assert "controller !== listController" in composable
    assert "clearInterval(refreshTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable
    for source in (page, filters, table):
        assert "v-html" not in source
        assert 'role="button"' not in source
    assert '<button' in page and '<button' in filters and '<button' in table
    assert ':href="diagnosisHref(stock.code)"' in table
    assert 'tabindex="0"' in table
    assert 'aria-sort' in table
    assert '<th scope="col">回封</th>' not in table
    assert 'v-if="stock.is_again" class="hotboard-again">回封</span>' in table
    assert "white-space: nowrap" in table
    assert "hotboard-sort-button.is-active" in table
    for tone in ("is-first", "is-mid", "is-high", "is-missing"):
        assert f"hotboard-board.{tone}" in table
    assert "font-size: 15px" in table
    assert "font-weight: 730" in table


def test_vue_hotboard_has_responsive_accessible_layout_and_audit_scenarios() -> None:
    page = _read("src/components/HotboardPage.vue")
    filters = _read("src/components/HotboardThemeFilter.vue")
    table = _read("src/components/HotboardStockTable.vue")
    audit = _read("scripts/audit-ui.mjs")

    assert '@media (max-width: 720px)' in page
    assert '@media (max-width: 720px)' in filters
    assert '@media (max-width: 720px)' in table
    assert "grid-template-columns: minmax(88px, .42fr) minmax(0, 1fr)" in table
    assert "max-width: 100%; overflow-x: auto" in table
    assert "min-height: 44px" in filters and "min-height: 44px" in table
    assert '@media (prefers-reduced-motion: reduce)' in page
    assert "box-shadow" not in page + filters + table
    assert "linear-gradient" not in page + filters + table
    assert "hash: '#/today?tab=hotboard'" in audit
    assert "'/api/market/limitup/themes'" in audit
    assert "`${auditedPage.name}-${viewport.name}.png`" in audit
    assert "`${auditedPage.name}-wide-1440-light.png`" in audit
    for screenshot in ("hotboard-loading-375.png", "hotboard-empty-1440.png", "hotboard-partial-failure-1440.png"):
        assert screenshot in audit
    assert "hotboardLoadingStateComplete" in audit
    assert "hotboardEmptyStateComplete" in audit
    assert "hotboardFailureStateComplete" in audit


def test_vue_timing_route_uses_real_server_decision_and_timing_contracts() -> None:
    app = _read("src/App.vue")
    page = _read("src/components/TimingPage.vue")
    composable = _read("src/composables/useTiming.ts")
    types = _read("src/types/timing.ts")

    assert "routePath.value === 'today' && routeTab.value === 'timing'" in app
    assert 'v-else-if="isTiming"' in app
    assert ':pulse="data.pulse"' in app
    assert ':pulse-error="errors.pulse"' in app
    assert ':market-session="data.shell?.market_context?.market_session || data.shell?.market_session"' in app
    assert ':is-trade-day="data.shell?.market_context?.is_trade_day ?? data.shell?.is_trade_day"' in app
    assert "if (isTiming.value && timingPage.value)" in app
    assert "await Promise.resolve(timingPage.value.refresh())" in app
    assert "getJson<TimingResponse>('/timing'" in composable
    assert "`/timing?code=${encodeURIComponent(code)}`" in composable
    for field in (
        "fear_greed", "indexes", "stock", "score", "zone", "advice", "factors", "ma",
        "turnover_rate", "vol_ratio", "main_flow_total", "data_date", "stale", "missing",
    ):
        assert field in types
    assert "command?.today_action" in page
    assert "command?.position_cap" in page
    assert "command?.first_principle" in page
    assert "区域与评分均由服务端规则返回" in page


def test_vue_timing_preserves_missing_empty_stale_and_unit_semantics() -> None:
    page = _read("src/components/TimingPage.vue")
    fear = _read("src/components/TimingFearGreed.vue")
    card = _read("src/components/TimingRegionCard.vue")
    stock = _read("src/components/TimingStockLookup.vue")
    formatter = _read("src/utils/timingFormat.ts")

    for label in (
        "择时证据暂不可用", "指数择时返回真实空数组", "指数择时字段缺失",
        "贪恐指标暂不可计算", "陈旧数据", "个股结果缺失",
    ):
        assert label in page + fear + stock
    assert "页面未使用历史指数或示例数值补位" in page
    assert "不从贪恐分数反推仓位或交易动作" in page
    assert "价格：个股为元、指数为点" in page
    assert "formatMoneyYuan(reading?.meta?.main_flow_total)" in fear
    assert "kind === 'index' ? '点' : '元'" in card
    assert "finite(reading.price)" in card
    assert "finite(reading.score)" in card
    for source in (page, fear, card, stock, formatter):
        assert "|| 0" not in source
        assert "?? 0" not in source


def test_vue_timing_cleans_lifecycle_and_uses_native_safe_interactions() -> None:
    page = _read("src/components/TimingPage.vue")
    stock = _read("src/components/TimingStockLookup.vue")
    composable = _read("src/composables/useTiming.ts")

    for controller in ("marketController?.abort()", "stockController?.abort()"):
        assert controller in composable
    assert "let alive = false" in composable
    assert "marketGeneration" in composable and "stockGeneration" in composable
    assert "controller !== marketController" in composable
    assert "controller !== stockController" in composable
    assert "clearInterval(refreshTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable
    assert "shouldAutoRefresh()" in composable
    assert "['intraday', 'tail'].includes" in page
    assert "onBeforeUnmount(() => emit('refreshState', false))" in page
    assert "EventSource" not in composable and "getReader" not in composable
    for source in (page, stock):
        assert "v-html" not in source
        assert 'role="button"' not in source
    assert '<form' in stock
    assert '<button' in stock and '<button' in page
    assert 'type="submit"' in stock
    assert 'inputmode="numeric"' in stock
    assert 'pattern="[0-9]{6}"' in stock
    assert "本页不消费 Markdown" in page


def test_vue_timing_has_responsive_accessible_layout_and_focus_semantics() -> None:
    page = _read("src/components/TimingPage.vue")
    fear = _read("src/components/TimingFearGreed.vue")
    card = _read("src/components/TimingRegionCard.vue")
    stock = _read("src/components/TimingStockLookup.vue")

    assert 'aria-labelledby="timing-page-title"' in page
    assert 'aria-live="polite"' in page
    assert 'role="alert"' in page and 'role="status"' in page
    assert 'role="progressbar"' in fear and 'role="progressbar"' in card
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in page
    assert "grid-template-columns: minmax(0, 1fr)" in page
    assert '@media (max-width: 720px)' in page + fear + stock
    assert '@media (max-width: 520px)' in card
    assert '@media (prefers-reduced-motion: reduce)' in page + stock
    assert "min-height: 44px" in stock and "min-height: 44px" in card
    assert "box-shadow" not in page + fear + card + stock
    assert "linear-gradient" not in page + fear + card + stock


def test_vue_timing_ui_audit_uses_semantic_fixture_and_required_states() -> None:
    audit = _read("scripts/audit-ui.mjs")

    assert "'/api/timing'" in audit
    assert "hash: '#/today?tab=timing'" in audit
    assert "data_date: '2026-07-21'" in audit
    assert "score: 60.1" in audit and "label: '贪婪'" in audit
    for screenshot in ("timing-loading-375.png", "timing-empty-1440.png", "timing-partial-failure-1440.png"):
        assert screenshot in audit
    assert "`${auditedPage.name}-${viewport.name}.png`" in audit
    assert "`${auditedPage.name}-wide-1440-light.png`" in audit
    assert "timingLoadingStateComplete" in audit
    assert "timingEmptyStateComplete" in audit
    assert "timingFailureStateComplete" in audit


def test_vue_tomorrow_strategy_uses_only_real_report_and_watchlist_contracts() -> None:
    app = _read("src/App.vue")
    navigation = _read("src/navigation.ts")
    page = _read("src/components/TomorrowStrategyPage.vue")
    summary = _read("src/components/TomorrowDecisionSummary.vue")
    watchlist = _read("src/components/TomorrowWatchlist.vue")
    health = _read("src/components/TomorrowDataHealth.vue")
    composable = _read("src/composables/useTomorrowStrategy.ts")

    assert "path: 'review', tab: 'tomorrow'" in navigation
    assert "routePath.value === 'review' && routeTab.value === 'tomorrow'" in app
    assert 'v-else-if="isTomorrowStrategy"' in app
    for endpoint in (
        "/reports/evening?date=", "/watchlist", "/status",
        "/reports/evening/generate", "/reports/evening/push",
    ):
        assert endpoint in composable
    for unrelated in ("/recommendations", "/market/flow", "/cockpit"):
        assert unrelated not in composable
    assert "19:30 生成时风险底座" in summary
    assert "不等于明日开盘后的最终风险闸" in summary
    assert "今日尾盘与收盘定稿只作上游事实" in page
    assert "item.quote?.price" in watchlist and "target_low" in watchlist and "target_high" in watchlist
    assert "value == null" in watchlist
    assert "change_pct == null" in watchlist
    assert "元" in watchlist and "%" in watchlist
    assert "input_lineage" in health and "rtype === 'tail'" in health and "rtype === 'close'" in health


def test_vue_tomorrow_strategy_preserves_missing_empty_stale_and_delivery_semantics() -> None:
    page = _read("src/components/TomorrowStrategyPage.vue")
    watchlist = _read("src/components/TomorrowWatchlist.vue")
    health = _read("src/components/TomorrowDataHealth.vue")
    composable = _read("src/composables/useTomorrowStrategy.ts")

    for label in (
        "次日策略尚未定稿", "观察池返回真实空数组", "陈旧报价",
        "明日观察池暂不可用", "部分覆盖", "局部降级",
    ):
        assert label in page + watchlist + health
    assert "当前未确认平台送达" in composable
    assert "已提交推送，未确认送达" in page + health
    assert "平台送达状态仍需通过交付回执核验" in composable
    for source in (page, watchlist, health):
        assert "v-html" not in source
        assert "|| 0" not in source
        assert "?? 0" not in source


def test_vue_tomorrow_strategy_cleans_lifecycle_and_uses_native_interactions() -> None:
    page = _read("src/components/TomorrowStrategyPage.vue")
    summary = _read("src/components/TomorrowDecisionSummary.vue")
    watchlist = _read("src/components/TomorrowWatchlist.vue")
    health = _read("src/components/TomorrowDataHealth.vue")
    composable = _read("src/composables/useTomorrowStrategy.ts")

    for controller in (
        "reportController?.abort()", "watchlistController?.abort()",
        "generateController?.abort()", "pushController?.abort()",
    ):
        assert controller in composable
    assert "let alive = false" in composable
    assert "if (!alive || controller !==" in composable
    assert "clearInterval(watchlistTimer)" in composable
    assert "removeEventListener('visibilitychange'" in composable
    assert "reader.cancel()" in _read("src/services/api.ts")
    assert 'aria-label="次日策略报告章节"' in page
    assert 'href="#tomorrow-watchlist"' in summary
    assert 'href="#/research?tab=diagnosis"' in watchlist
    for source in (page, summary, watchlist, health):
        assert 'role="button"' not in source
    assert "<button" in page + watchlist
    assert "<a" in page + summary + watchlist


def test_vue_tomorrow_strategy_has_responsive_accessible_layout_and_audit_states() -> None:
    page = _read("src/components/TomorrowStrategyPage.vue")
    summary = _read("src/components/TomorrowDecisionSummary.vue")
    watchlist = _read("src/components/TomorrowWatchlist.vue")
    audit = _read("scripts/audit-ui.mjs")

    assert 'aria-labelledby="tomorrow-page-title"' in page
    assert 'role="alert"' in page and 'role="status"' in page
    assert "grid-template-columns: minmax(0, 1.25fr) minmax(320px, .75fr)" in page
    assert "@media (max-width: 1100px)" in page
    assert "@media (max-width: 720px)" in page + summary + watchlist
    assert "@media (max-width: 520px)" in watchlist
    assert "@media (prefers-reduced-motion: reduce)" in page + summary + watchlist
    assert "box-shadow" not in page + summary + watchlist
    assert "linear-gradient" not in page + summary + watchlist
    assert "hash: '#/review?tab=tomorrow'" in audit
    assert "'/api/reports/evening'" in audit
    assert "次日策略（供 2026-07-23 使用）" in audit
    for screenshot in (
        "tomorrow-loading-375.png", "tomorrow-empty-1440.png",
        "tomorrow-partial-failure-1440.png",
    ):
        assert screenshot in audit
    assert "tomorrowLoadingStateComplete" in audit
    assert "tomorrowEmptyStateComplete" in audit
    assert "tomorrowPartialStateComplete" in audit
