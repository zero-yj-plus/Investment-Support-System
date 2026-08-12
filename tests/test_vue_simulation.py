from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def _read(path: str) -> str:
    return (VUE / path).read_text(encoding="utf-8")


def test_vue_simulation_restores_the_local_order_lifecycle() -> None:
    page = _read("src/components/SimulationPage.vue")
    dialog = _read("src/components/SimulationActionDialog.vue")
    composable = _read("src/composables/useSimulation.ts")
    navigation = _read("src/navigation.ts")
    app = _read("src/App.vue")

    assert "tab: 'simulation'" in navigation
    assert "v-else-if=\"isSimulation\"" in app
    for endpoint in (
        "/simulation/status",
        "/simulation/account?curve_limit=365",
        "/simulation/account/settings",
        "/simulation/account/valuation",
        "/simulation/orders?limit=100",
        "/simulation/orders/${encodeURIComponent(order.order_id)}/approve",
        "/simulation/orders/${encodeURIComponent(order.order_id)}/fill",
        "/simulation/orders/${encodeURIComponent(order.order_id)}/cancel",
        "/simulation/emergency-stop",
        "/simulation/emergency-stop/reset",
    ):
        assert endpoint in composable
    assert "manual_approval_required" in page or "必须人工批准" in page
    assert "服务端定价" in page
    assert "追加式留痕" in page
    assert "T+1 冻结" in page
    assert "累计盈亏" in page
    assert "SimulationEquityCurve" in page
    assert "初始资金（元）" in dialog
    assert "佣金率（% / 双边）" in dialog
    assert "AI、Agent、bot、system 或 Hermes 不能作为批准人" in dialog


def test_vue_simulation_keeps_server_authority_and_missing_semantics() -> None:
    page = _read("src/components/SimulationPage.vue")
    dialog = _read("src/components/SimulationActionDialog.vue")
    composable = _read("src/composables/useSimulation.ts")

    assert "payload.simulation_only !== true" in composable
    assert "payload.broker_connected !== false" in composable
    assert "客户端不能提交或覆盖风险等级、现价、行情时点与数据缺口" in dialog
    assert "服务端重新读取" in dialog
    assert "盘中实时，盘外最近收盘" in page
    for missing_label in ("价格缺失", "时点缺失", "风险等级缺失", "尚未成交"):
        assert missing_label in page
    assert "不会在账本读取失败时构造订单、价格或券商状态" in page


def test_vue_simulation_cleans_up_requests_and_has_responsive_controls() -> None:
    page = _read("src/components/SimulationPage.vue")
    dialog = _read("src/components/SimulationActionDialog.vue")
    composable = _read("src/composables/useSimulation.ts")

    assert "onBeforeUnmount" in composable
    assert "loadController?.abort()" in composable
    assert "actionController?.abort()" in composable
    assert "min-height: 44px" in page
    assert "simulation-cards" in page
    assert "@media (max-width: 760px)" in page
    assert "aria-labelledby" in dialog
    assert "role=\"alert\"" in dialog


def test_vue_simulation_has_a_targeted_interaction_and_accessibility_audit() -> None:
    package = _read("package.json")
    audit = _read("scripts/audit-simulation-ui.mjs")

    assert '"audit:simulation": "node scripts/audit-simulation-ui.mjs"' in package
    assert "#/risk?tab=simulation" in audit
    assert "确认人工批准" in audit
    assert "按服务端行情模拟成交" in audit
    assert "确认人工恢复" in audit
    assert "保存后续成交设置" in audit
    assert "记录当前估值" in audit
    assert "globalThis.axe.run(document)" in audit
    assert "undersizedTargets" in audit
    assert "{ name: 'phone-375-dark', width: 375, height: 812, theme: 'dark' }" in audit
    assert "checks.violations.length > 0" in audit
