from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"
PAGE = VUE / "src/components/SettingsPage.vue"
COMPONENTS = [
    VUE / "src/components/SettingsReadiness.vue",
    VUE / "src/components/SettingsTaskPanel.vue",
    VUE / "src/components/SettingsDeliveryPanel.vue",
    VUE / "src/components/SettingsModelPanel.vue",
    VUE / "src/components/SettingsDataPanel.vue",
    VUE / "src/components/SettingsGovernance.vue",
]
COMPOSABLE = VUE / "src/composables/useSystemSettings.ts"
TYPES = VUE / "src/types/settings.ts"
FORMAT = VUE / "src/utils/settingsFormat.ts"
AUDIT = VUE / "scripts/audit-ui.mjs"
RULES = ROOT / "design-system/wudi-decision-console/pages/settings.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def production_source() -> str:
    return "\n".join(read(path) for path in [PAGE, COMPOSABLE, TYPES, FORMAT, *COMPONENTS])


def test_vue_settings_route_is_reachable_without_migrating_other_pages() -> None:
    app = read(VUE / "src/App.vue")
    sidebar = read(VUE / "src/components/SidebarNavigation.vue")
    page = read(PAGE)

    assert "import SettingsPage from './components/SettingsPage.vue'" in app
    assert "const settingsHashSections: Record<string, SettingsSection>" in app
    for route, section in (
        ("settings-tasks", "tasks"), ("settings-delivery", "delivery"),
        ("settings-models", "models"), ("settings-data", "data"),
        ("settings-governance", "governance"),
    ):
        assert f"'{route}': '{section}'" in app
        assert f'href="#{route}"' in page
    assert 'v-else-if="isSettings"' in app
    assert ':section="settingsSection"' in app
    assert "if (isSettings.value && settingsPage.value)" in app
    assert 'href="#settings-tasks"' in sidebar


def test_vue_settings_renders_only_the_selected_section() -> None:
    page = read(PAGE)

    assert 'v-if="props.section === \'tasks\'"' in page
    assert 'v-else-if="props.section === \'delivery\'"' in page
    assert 'v-else-if="props.section === \'models\'"' in page
    assert 'v-else-if="props.section === \'data\'"' in page
    assert '<SettingsGovernance v-else' in page
    for section in ("tasks", "delivery", "models", "data", "governance"):
        assert f"props.section === '{section}' ? 'page' : undefined" in page


def test_vue_settings_uses_only_real_settings_contracts() -> None:
    source = read(COMPOSABLE)
    for endpoint in (
        "getJson<SettingsResponse>('/settings'",
        "getJson<StatusResponse>('/status'",
        "getJson<JobsResponse>('/jobs'",
        "getJson<DatasourcesResponse>('/datasources'",
        "getJson<DeliveryReadinessResponse>('/delivery/readiness'",
        "getJson<DeliveryReceiptsResponse>('/delivery/receipts?limit=30'",
        "getJson<DeliveryOutboxResponse>('/delivery/outbox?limit=30'",
        "getJson<AgentRunsResponse>('/agent/runs?limit=12'",
        "getJson<ModelConnectionsResponse>('/settings/model-connections'",
        "putJson<MutationResponse>('/settings'",
        "postJson<MutationResponse>('/push/test'",
        "postJson<MutationResponse>('/settings/model/test'",
        "postJson<MutationResponse>('/settings/model-connections'",
        "`/settings/model-connections/${encodeURIComponent(connectionId)}/activate`",
        "deleteJson<MutationResponse>(`/settings/model-connections/${encodeURIComponent(connectionId)}`",
        "`/jobs/${encodeURIComponent(jobId)}/run`",
        "`/delivery/${encodeURIComponent(deliveryId)}/acknowledge`",
        "`/delivery/${encodeURIComponent(deliveryId)}/retry`",
    ):
        assert endpoint in source
    for unrelated in ("/pulse", "/market/", "/holdings", "/watchlist", "/screen", "/diagnose", "/reports/"):
        assert unrelated not in source


def test_vue_settings_preserves_missing_empty_degraded_and_delivery_semantics() -> None:
    source = production_source()
    for phrase in (
        "settings 字段缺失，不能执行覆盖保存",
        "jobs 缺失或不是数组，不能当作暂无任务",
        "sources 缺失或不是数组，不能当作暂无来源",
        "receipts 缺失或不是数组，不能当作暂无回执",
        "connections 缺失或不是数组，不能当作暂无连接",
        "服务端返回真实空数组：暂无已登记任务",
        "idle 不代表不可用",
        "最近调用失败",
        "部分可用",
        "平台已接受",
        "已送达",
        "结果待核验",
        "未配置（可选）",
        "明文不会回显",
        "空值不会被猜成默认时间",
    ):
        assert phrase in source
    assert "?? 0" not in source
    assert "|| 0" not in source


def test_vue_settings_cleans_each_request_and_ignores_late_responses() -> None:
    source = read(COMPOSABLE)
    for controller in (
        "settingsController", "statusController", "jobsController", "datasourcesController",
        "readinessController", "receiptsController", "outboxController", "agentRunsController",
        "modelsController", "saveController", "pushTestController", "modelController",
        "jobController", "deliveryController",
    ):
        assert f"let {controller}: AbortController | null = null" in source
        assert f"{controller}?.abort()" in source
    assert "controller !== requestController(key)" in source
    assert "generation !== generations[key]" in source
    assert "generation !== actionGenerations[key]" in source
    assert "controller.abort('timeout')" in source
    assert "document.addEventListener('visibilitychange', onVisibilityChange)" in source
    assert "document.removeEventListener('visibilitychange', onVisibilityChange)" in source
    assert "let alive = false" in source
    assert "if (activeAction.value) return false" in source
    assert "if (loading.value) return" in source
    assert "clearTimeout(loadingDelayTimer)" in read(PAGE)


def test_vue_settings_uses_safe_nodes_and_native_interactions() -> None:
    source = production_source()
    page = read(PAGE)
    delivery = read(VUE / "src/components/SettingsDeliveryPanel.vue")

    assert " v-html=" not in source
    assert ".innerHTML" not in source
    assert "reportMarkdown" not in source
    assert '<button type="button"' in source
    assert 'href="#settings-tasks"' in page
    assert 'role="button"' not in source
    assert 'aria-live="polite"' in page
    assert 'type="password"' in source
    assert 'autocomplete="new-password"' in source
    assert "填写核验依据后再确认或重试" in delivery


def test_vue_settings_has_five_immediate_persistent_scheduled_push_switches() -> None:
    task_panel = read(VUE / "src/components/SettingsTaskPanel.vue")
    page = read(PAGE)
    types = read(TYPES)

    assert "const scheduledPushKeys: ScheduledPushKey[] = ['morning', 'tail', 'close', 'evening', 'archive']" in task_panel
    assert "scheduled_push?: ScheduledPushSettings | null" in types
    assert 'type="checkbox"' in task_panel
    assert 'role="switch"' in task_panel
    assert "emit('saveScheduledPush', key, next" in task_panel
    assert "if (!saved) scheduledPush[key] = previous" in task_panel
    assert "飞书开关切换后立即生效并单独持久化" in task_panel
    assert "关闭只跳过自动推送，不停止任务生成" in task_panel
    assert "const saveScheduledPush = async" in read(COMPOSABLE)
    assert "function updateScheduledPush" in page
    assert '@save-scheduled-push="updateScheduledPush"' in page


def test_vue_settings_has_responsive_containment_and_accessible_targets() -> None:
    source = production_source()
    page = read(PAGE)

    assert ".settings-page { display: grid; min-width: 0;" in page
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in source
    assert "grid-template-columns: 1fr" in source
    assert 'tabindex="0" aria-label="任务运行状态表，可横向滚动"' in source
    assert 'tabindex="0" aria-label="数据源健康表，可横向滚动"' in source
    assert "min-height: 44px" in source
    for breakpoint in (1100, 1050, 1020, 960, 900, 820, 760, 720, 620, 560, 420):
        assert f"max-width: {breakpoint}px" in source
    assert "font-size: 18px" in page
    assert "@media (prefers-reduced-motion: reduce)" in page
    assert ":focus-visible" in read(VUE / "src/styles.css")
    assert "box-shadow" not in source
    assert "linear-gradient" not in source


def test_vue_settings_has_page_rules_and_real_semantic_audit_fixtures() -> None:
    rules = read(RULES)
    audit = read(AUDIT)

    for route in (
        "#settings-tasks", "#settings-delivery", "#settings-models",
        "#settings-data", "#settings-governance",
    ):
        assert route in rules
    assert "#/settings?tab=system" in rules
    for endpoint in (
        "GET /api/settings", "GET /api/status", "GET /api/jobs", "GET /api/datasources",
        "GET /api/delivery/readiness", "GET /api/delivery/receipts", "GET /api/delivery/outbox",
        "GET /api/agent/runs", "GET /api/settings/model-connections", "PUT /api/settings",
    ):
        assert endpoint in rules
    assert "ui-ux-pro-max" in rules
    for name, route in (
        ("settings-tasks", "#settings-tasks"),
        ("settings-delivery", "#settings-delivery"),
        ("settings-models", "#settings-models"),
        ("settings-data", "#settings-data"),
        ("settings-governance", "#settings-governance"),
    ):
        assert f"name: '{name}', hash: '{route}'" in audit
    assert "审计受限模型" in audit
    for scenario in (
        "settings-loading-375", "settings-empty-1440", "settings-partial-failure-1440",
    ):
        assert scenario in audit
    assert "settingsLoadingStateComplete" in audit
    assert "settingsEmptyStateComplete" in audit
    assert "settingsFailureStateComplete" in audit
    assert "审计受限模型" not in production_source()
