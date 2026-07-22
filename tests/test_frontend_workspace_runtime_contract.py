from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "js" / "app.js"
NAVIGATION = ROOT / "frontend" / "js" / "navigation.js"
COCKPIT = ROOT / "frontend" / "js" / "views" / "cockpit.js"
HOLDINGS = ROOT / "frontend" / "js" / "views" / "holdings.js"
FEATURES = ROOT / "frontend" / "js" / "views" / "features.js"
CAPITAL = ROOT / "frontend" / "js" / "views" / "capital.js"
MAINFORCE = ROOT / "frontend" / "js" / "views" / "mainforce.js"
SENTI = ROOT / "frontend" / "js" / "views" / "senti.js"
CHAT = ROOT / "frontend" / "js" / "views" / "chat.js"
DIAGNOSIS = ROOT / "frontend" / "js" / "views" / "diagnosis.js"
CHAIN = ROOT / "frontend" / "js" / "views" / "chain.js"
COMPANY = ROOT / "frontend" / "js" / "views" / "company.js"
OVERSEAS = ROOT / "frontend" / "js" / "views" / "overseas.js"
RESTRUCTURING = ROOT / "frontend" / "js" / "views" / "restructuring.js"


def test_background_freshness_never_remounts_the_current_workspace() -> None:
    source = APP.read_text(encoding="utf-8")

    # A periodic route(true) unmounts/remounts the whole page, resetting scroll and
    # producing the visible flash the user reported. Manual refresh may still remount.
    assert "refresh: () => { loadStatus(); route(true); }" not in source
    assert "refresh: () => { loadStatus(); }" in source


def test_legacy_nav_hidden_does_not_hide_workspace_leaf_capabilities() -> None:
    source = APP.read_text(encoding="utf-8")

    # ui.nav_hidden predates the workspace IA. Applying it to leaf tabs made a
    # configured-but-important feature indistinguishable from a deleted feature.
    assert "&& !navHidden.has(path)" not in source
    assert "registered?.navigation !== false && tabDef.navigation !== false" in source


def test_first_open_lands_on_the_decision_home() -> None:
    source = APP.read_text(encoding="utf-8")

    assert "if (!location.hash) history.replaceState(null, '', '#/today');" in source


def test_workspace_children_live_in_a_collapsible_sidebar_without_duplicate_top_tabs() -> None:
    source = APP.read_text(encoding="utf-8")

    assert 'class="nav-group-toggle"' in source
    assert 'class="nav-child"' in source
    assert "sidebarOpenGroups" in source
    assert "location.hash = buildHash(path, params);" in source
    assert 'class="workspace-nav"' not in source
    assert 'class="tabs workspace-tabs"' not in source


def test_sidebar_starts_collapsed_and_only_expands_the_active_group() -> None:
    source = APP.read_text(encoding="utf-8")

    assert "let sidebarOpenGroups = new Set();" in source
    assert "workspace.classList.toggle('open', active);" in source
    assert "children.hidden = !active;" in source
    assert "if (active) sidebarOpenGroups.add(workspace.dataset.navGroup);" in source
    assert "el.querySelectorAll('.nav-workspace.open').forEach((other) =>" in source
    assert "activate(workspace.querySelector('.nav-parent'));" in source
    assert "hermes.nav.groups.v3" not in source


def test_sidebar_uses_the_requested_module_groups_labels_and_order() -> None:
    source = NAVIGATION.read_text(encoding="utf-8")
    sidebar = source[source.index("export const SIDEBAR_NAV") : source.index("// 旧页面不删")]

    labels = [
        "今日决策", "决策总览", "盘前报告", "尾盘决策", "今日复盘", "次日策略",
        "市场结构", "涨停题材", "主线深研", "资金追踪", "资金择时", "主力结构", "舆情事件",
        "持仓风控", "我的持仓", "观察池", "模拟执行",
        "研究中心", "问股", "个股诊断", "条件选股", "每日量化选股", "事件掘金",
        "产业链分析", "公司研究", "海外市场", "重组拆解", "宏观风险",
        "历史与知识", "全部报告", "知识库",
    ]
    positions = [sidebar.index(f"'{label}'") for label in labels]
    assert positions == sorted(positions)
    assert "sidebarChild('research', 'restructuring', '重组拆解', { placement: 'more' })" in sidebar
    assert "sidebarChild('research', 'macro', '宏观风险', { placement: 'more' })" in sidebar


def test_topbar_shows_the_active_large_module_instead_of_leaf_page_names() -> None:
    app = APP.read_text(encoding="utf-8")
    navigation = NAVIGATION.read_text(encoding="utf-8")

    assert "export function moduleLabelForRoute(" in navigation
    assert "const moduleLabel = moduleLabelForRoute(r.path, currentTab, navLabels);" in app
    assert "title.textContent = moduleLabel" in app
    assert app.count("document.title = `${moduleLabel} · ${brandTitle}`") == 2
    assert "title.textContent = r.label" not in app
    assert "document.title = `${r.label} · ${brandTitle}`" not in app


def test_topbar_reports_connection_without_claiming_every_field_is_realtime() -> None:
    source = APP.read_text(encoding="utf-8")

    assert "行情已连接" in source
    assert "trading && okAt" in source
    assert "连接检查中" in source
    assert "尚未完成首次行情连接检查" in source
    assert "timeZone: 'Asia/Shanghai'" in source
    assert "<span class=\"live-dot\"></span>实时" not in source


def test_mainline_research_has_a_page_title_and_decision_oriented_subtitle() -> None:
    source = FEATURES.read_text(encoding="utf-8")

    assert '<h2 class="page-title">主线深研</h2>' in source
    assert "交叉验证题材赚钱效应、交易拥挤度、持续性与龙头梯队" in source
    assert "识别当日主线与分歧风险" in source


def test_capital_and_mainforce_views_have_page_titles_and_explanatory_subtitles() -> None:
    capital = CAPITAL.read_text(encoding="utf-8")
    mainforce = MAINFORCE.read_text(encoding="utf-8")

    assert '<h2 class="page-title">资金追踪</h2>' in capital
    assert "追踪板块与热门股的主力资金日内流向" in capital
    assert '<h2 class="page-title">主力结构</h2>' in mainforce
    assert "盘后交叉观察龙虎榜机构净买与行业资金流向" in mainforce


def test_sentiment_event_view_uses_the_navigation_label_as_its_page_title() -> None:
    source = SENTI.read_text(encoding="utf-8")

    assert '<h2 class="page-title">舆情事件</h2>' in source
    assert '<h2 class="page-title">盘中事件</h2>' not in source


def test_research_center_views_have_page_titles_and_explanatory_subtitles() -> None:
    pages = (
        (CHAT, "问股", "结合真实行情与治理证据"),
        (DIAGNOSIS, "个股诊断", "综合趋势、量价、动量、资金、行业与财务证据"),
        (CHAIN, "产业链分析", "拆解需求、价值分配与产业卡点"),
        (COMPANY, "公司研究", "结合公告、互动、行情与财务证据"),
        (OVERSEAS, "海外市场", "查询美股、港股实时行情与基础财务"),
        (RESTRUCTURING, "重组拆解", "基于公告或消息原文拆解交易方案"),
    )

    for path, title, subtitle in pages:
        source = path.read_text(encoding="utf-8")
        assert f'<h2 class="page-title">{title}</h2>' in source
        assert subtitle in source


def test_topbar_clock_displays_beijing_time_to_the_second() -> None:
    source = APP.read_text(encoding="utf-8")

    assert "--点--分--秒" in source
    assert "`${p(c.hh)}点${p(c.mm)}分${p(c.ss)}秒`" in source
    assert "setInterval(tickClock, 1000);" in source


def test_boot_renders_route_before_background_status_and_reapplies_brand() -> None:
    source = APP.read_text(encoding="utf-8")

    boot_block = source[source.index("function boot()") :]
    assert boot_block.index("route(true);") < boot_block.index("Promise.allSettled([")
    assert "loadShellStatus({ deferShell: true })" in boot_block
    assert "const hydrateFullStatus = () => loadStatus().then(() => route(false));" in boot_block
    assert "requestIdleCallback(hydrateFullStatus" in boot_block
    settled = boot_block[boot_block.index("Promise.allSettled([") :]
    assert settled.index("buildSidebar();") < settled.index("updateBrand(appState.status || {});")


def test_builtin_and_extension_views_are_imported_only_when_opened() -> None:
    source = APP.read_text(encoding="utf-8")

    assert "import * as cockpit from './views/cockpit.js'" not in source
    assert "cockpit: lazyView(() => import('./views/cockpit.js')" in source
    assert "view: lazyView(() => import(v.module)" in source
    assert "const imports = await Promise.all(list.map" not in source


def test_workspace_preloads_sibling_code_only_when_browser_is_idle() -> None:
    source = APP.read_text(encoding="utf-8")

    assert "const warmSiblingModules = () =>" in source
    assert "typeof item.view?.preload === 'function'" in source
    assert "window.requestIdleCallback(warmSiblingModules, { timeout: 2500 });" in source
    assert "setTimeout(warmSiblingModules, 1200);" in source
    # 只预加载模块文件，不能在预热阶段 mount 页面或发数据请求。
    start = source.index("const warmSiblingModules = () =>")
    warm = source[start : source.index("    },\n    beforeLeave()", start)]
    assert ".preload()" in warm
    assert ".mount(" not in warm


def test_cockpit_market_state_is_not_named_as_a_second_sentiment_cycle() -> None:
    source = COCKPIT.read_text(encoding="utf-8")

    assert "市场状态 · 系统性风险" in source
    assert "情绪周期 · 系统性风险" not in source


def test_cockpit_separates_risk_level_from_emotion_without_repeating_actions() -> None:
    source = COCKPIT.read_text(encoding="utf-8")

    assert '<div class="sr-hero-k">系统风险</div>' in source
    assert '<div class="emo-k">情绪温度</div>' in source
    assert "系统性风险依据" in source
    assert "d.upgrade_triggers" not in source
    assert "d.downgrade_triggers" not in source
    assert "查看 1—5 级风险说明" not in source


def test_cockpit_places_market_and_market_state_before_deep_content() -> None:
    source = COCKPIT.read_text(encoding="utf-8")

    # 首页首屏包含总指令、市场全景、今日主线与独立风险侧栏；深度证据在后面。
    assert source.index('id="ck2-board"') < source.index('id="ck2-mainline"')
    assert source.index('id="ck2-emo"') < source.index('<div class="ck2-section-head"><span class="t">盘面证据</span>')


def test_cockpit_independent_main_flow_and_expanded_risk_rail_remove_blank_column() -> None:
    source = COCKPIT.read_text(encoding="utf-8")

    shell_start = source.index('<div class="ck2-overview-shell">')
    evidence_start = source.index('<div class="ck2-section-head"><span class="t">盘面证据</span>')
    summary = source.index('id="ck2-summary"')
    market_state = source.index('id="ck2-emo"')
    board = source.index('id="ck2-board"')
    mainline = source.index('id="ck2-mainline"')
    assert shell_start < summary < market_state < board < mainline < evidence_start
    assert source.count('id="ck2-mainline"') == 1
    assert '.ck2-overview-shell{display:grid' in source
    assert '.ck2-overview-shell>#ck2-summary,.ck2-state-rail' in source
    assert 'class="ck2-overview-main"' not in source
    assert '.ck2-state-rail{align-self:start' in source
    assert 'grid-template-areas:"board state" "mainline state"' not in source
    assert '<details class="sr-evidence">' not in source
    assert 'id="ck2-sysrisk-body"' in source
    assert '@media (max-width:1280px)' in source


def test_limit_down_pool_stays_visible_but_can_expand_without_forcing_blank_height() -> None:
    source = COCKPIT.read_text(encoding="utf-8")

    assert "const visible = _downPoolExpanded ? pool : pool.slice(0, 5);" in source
    assert "data-downpool-toggle" in source
    assert "展开全部" in source
    assert "收起明细" in source


def test_cockpit_keeps_one_ladder_owner_and_uses_the_space_for_risk_evidence() -> None:
    source = COCKPIT.read_text(encoding="utf-8")

    limit_row = source.index('<div class="ck2-top-row ck2-limit-row">')
    limit_stats = source.index('id="ck2-limit"')
    downpool = source.index('id="ck2-downpool"')
    sector_flow = source.index('id="ck2-secflow"')
    deep_section = source.index('<div class="ck2-section-head"><span class="t">深度结构</span>')
    heat = source.index('id="ck2-heat"')
    assert limit_row < limit_stats < downpool < sector_flow < deep_section < heat
    assert 'id="ck2-ladder"' not in source
    assert "api.get('/market/limitup')" not in source
    assert '.ck2-limit-row{grid-template-columns:minmax(0,3fr) minmax(260px,1fr)}' in source
    assert '<div class="card sp-12" id="ck2-heat">' in source


def test_cockpit_mainline_panel_uses_fixed_market_structure_roles() -> None:
    source = COCKPIT.read_text(encoding="utf-8")

    assert "今日行业主线 · 主线 / 次主线 / 活口 / 情绪链" in source
    assert "今日行业主线（容量 × 强度 × 宽度 × 梯队）" not in source
    assert "function timedMainlineLabel(role)" in source
    assert "持续主线样本" not in source
    assert "主线（成交额 × 涨停家数）" not in source
    assert "const raw = d?.roles || d?.sectors || d?.mainlines || [];" in source
    assert "status === 'unformed'" in source
    assert "净流入权重 0" not in source
    assert "形成2档 / 降级1档 / 撤销2档" not in source
    assert '${dataStamp(d)}<span class="ck2-mainline-regime">' in source
    assert ".ck2-ml-rule" not in source
    assert "grid-template-columns:repeat(5,minmax(178px,1fr))" in source
    assert "grid-template-columns:repeat(6,minmax(0,1fr))" in source
    assert ".ck2-ml .ml-card:nth-child(n+4){grid-column:span 3}" in source


def test_cockpit_market_stamp_does_not_treat_source_notes_as_missing_data() -> None:
    source = COCKPIT.read_text(encoding="utf-8")

    assert "大盘 / 市场环境 · A股" in source
    assert "盘型 / 环境 · A股+海外" not in source
    assert "function boardDataQuality(ov)" in source
    assert "row?.price == null || row?.change_pct == null" in source
    assert "ov?.up_count == null || ov?.down_count == null || ov?.flat_count == null" in source
    assert "dataStamp(ov.ts || ov.data_date, null" in source
    assert "state_label: quality.missing.length ? '暂不可用' : '决策可用'" in source
    assert "missing_fields: quality.missing" in source
    assert "notes: quality.notes" in source


def test_structured_quality_states_do_not_turn_source_notes_into_missing_badges() -> None:
    ui = (ROOT / "frontend/js/ui.js").read_text(encoding="utf-8")
    cockpit = COCKPIT.read_text(encoding="utf-8")
    features = (ROOT / "frontend/js/views/features.js").read_text(encoding="utf-8")

    assert "const legacyPartial = !qualityAware && legacyMisses.length > 0;" in ui
    assert "labels.degraded = '部分覆盖'" in ui
    assert "(!value && !explicit)" in ui
    assert "[meta.freshness, meta.data_state, meta.state]" in ui
    assert "complete: '决策可用'" in ui
    assert "degraded: '增强项缺失'" in ui
    assert "missing: '暂不可用'" in ui
    assert "qualityAware && notes.length ? [] : legacyMisses" in ui
    assert "accumulating: '样本积累'" in ui
    assert "stamp.innerHTML = dataStamp(d);" in cockpit
    assert features.count("stamp.innerHTML = dataStamp(d);") >= 3


def test_cockpit_keeps_a_compact_action_contract_and_fails_closed_offline() -> None:
    source = COCKPIT.read_text(encoding="utf-8")

    for label in ["当前状态", "今日唯一主动作", "仓位边界", "执行原则"]:
        assert label in source
    assert 'grid-template-columns:minmax(0,1fr) minmax(0,1.8fr) minmax(0,.75fr) minmax(0,1.15fr)' in source
    assert 'grid-template-areas:"state action position principle"' in source
    assert 'data-kind="mainline"' not in source
    assert "当前等级怎么做" not in source
    assert "降级观察" not in source
    assert "查看 1—5 级风险说明" not in source
    assert "ck2-guardrails" not in source
    assert "ck2-review" not in source
    assert "今天可以做" not in source
    assert "今天禁止做" not in source
    assert "关键决策数据未连接，本卡暂停生成新结论" in source
    assert "数据质量不足时不强行给方向" in source


def test_mainline_research_uses_one_workbench_with_independent_columns() -> None:
    source = FEATURES.read_text(encoding="utf-8")

    assert source.count('class="card ft-workbench-card"') == 1
    assert 'class="ft-workbench"' in source
    assert source.count('class="ft-workbench-col"') == 2
    for module_id in ["ft-money", "ft-crowd", "ft-mainline", "ft-leaders"]:
        assert f'id="{module_id}"' in source
    assert 'class="ft-module ft-module-wide"' in source
    assert 'class="card sp-7"' not in source
    assert 'class="card sp-5"' not in source


def test_mainline_leaders_render_the_highest_stock_once() -> None:
    source = FEATURES.read_text(encoding="utf-8")

    assert "空间龙头" not in source
    assert "const rawLadder" in source
    assert "const ladder = rawLadder.length ? rawLadder" in source
    assert 'class="ft-ladder"' in source


def test_holdings_batches_quotes_instead_of_polling_each_stock() -> None:
    source = HOLDINGS.read_text(encoding="utf-8")

    assert "`/quotes?codes=${encodeURIComponent(valid.join(','))}`" in source
    assert "await fetchQuotes(rows.map((r) => r.code));" in source
    assert "Promise.all(rows.map((r) => fetchQuote(r.code)))" not in source


def test_holdings_gives_the_detail_table_full_width_and_compacts_distribution() -> None:
    source = HOLDINGS.read_text(encoding="utf-8")
    charts = (ROOT / "frontend/js/charts.js").read_text(encoding="utf-8")

    assert 'class="grid12 holdings-grid"' in source
    assert 'class="card hoverable sp-12" id="hold-detail-card"' in source
    assert 'class="card hoverable sp-12" id="hold-pie-card"' in source
    assert "#hold-detail-card .tbl-wrap{overflow-x:visible}" in source
    assert "@media(max-width:1320px){#hold-detail-card .tbl-wrap{overflow-x:auto}}" in source
    assert 'height:150px" id="hold-pie-chart"' in source
    assert source.count("const portfolioWeight = totalAsset && totalAsset > 0") == 2
    assert "center: ['12%', '50%']" in charts
    assert "label: { show: false }" in charts


def test_research_modes_isolate_timeline_and_render_chain_markdown_during_stream() -> None:
    source = CHAT.read_text(encoding="utf-8")

    assert "let conversationEpoch = 0" in source
    assert "if (m.mode !== mode) return false" in source
    assert "epoch !== conversationEpoch" in source
    assert "msgMode === 'chain'" in source
    assert "? renderMd(acc)" in source
    assert "saveActiveExchange(msg, imgs.length, content, msgMode, msgCode)" in source
    assert "activeMeta.mode !== frozenMode" in source
    assert "mode = 'free';" in source
    assert "loadConvList();" in source
    assert "switchConversation(savedConv)" not in source


def test_diagnosis_does_not_translate_hold_into_zero_target_position() -> None:
    source = DIAGNOSIS.read_text(encoding="utf-8")

    assert "持有/观望不需新仓授权，不代表目标仓位是 0%" in source
    assert "当前动作不增加风险" in source
