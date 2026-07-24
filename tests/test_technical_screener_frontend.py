from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_technical_screener_is_a_research_center_page() -> None:
    navigation = (ROOT / "frontend/js/navigation.js").read_text(encoding="utf-8")
    app = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
    assert "tab('technical-screener', '技术面选股', 'technical_screener')" in navigation
    assert "sidebarChild('research', 'technical-screener', '技术面选股')" in navigation
    assert "./views/technical_screener.js" in app


def test_technical_screener_has_natural_language_rule_and_lifecycle_contract() -> None:
    source = (ROOT / "frontend/js/views/technical_screener.js").read_text(encoding="utf-8")
    for token in (
        "schemaVersion: 2", "satisfy", "exclude", "groupMatch", "conditionMatch",
        "满足规则", "排除规则", "全部成立", "任意一项成立",
        "任意一项出现时排除", "全部条件同时出现时排除", "naturalLanguagePreview",
    ):
        assert token in source
    for obsolete_copy in ("必须全部满足（AND）", "至少满足一个（OR）", "排除以下条件（NOT）", "子条件组", "添加二级条件组"):
        assert obsolete_copy not in source
    assert "technical-screen/query" in source
    assert "technical-screen/catalog" in source
    assert "technical-screen/history" in source
    assert "data-add-rule-group" in source
    assert "data-condition-match" in source
    assert "data-group-match" in source
    assert "ts-rule-card" in source
    assert "ts-natural-preview" in source
    assert "AbortController" in source
    assert "clearTimeout" in source
    assert "export function unmount" in source


def test_rule_editor_supports_old_running_backend_without_losing_v2_draft() -> None:
    source = (ROOT / "frontend/js/views/technical_screener.js").read_text(encoding="utf-8")
    for token in (
        "backendSupportsRuleV2", "cacheStatus?.config_schema_version",
        "legacyRulePayload", "must_all", "any_of", "editor_config",
        "requestPayload({ config: copiedConfig, forSave: true })", "apiErrorMessage",
    ):
        assert token in source
    assert "error?.data?.errors" in source


def test_technical_screener_exposes_accessible_and_missing_states() -> None:
    source = (ROOT / "frontend/js/views/technical_screener.js").read_text(encoding="utf-8")
    assert 'aria-live="polite"' in source
    assert "aria-expanded" in source
    assert "focus-visible" in source
    assert "数据源不可用" in source
    assert "未完成周线/月线信号可能在周期结束前消失" in source
    assert "@media(max-width:1100px)" in source
    assert "@media(max-width:720px)" in source
    assert ".ts-mobile-results{display:grid" in source
    assert "prefers-reduced-motion:reduce" in source


def test_advanced_mode_exposes_browsable_multi_select_signal_library() -> None:
    source = (ROOT / "frontend/js/views/technical_screener.js").read_text(encoding="utf-8")
    for token in (
        "技术信号库", "加入当前条件组", "仅看可用", "已选",
        'role="dialog"', 'aria-modal="true"', 'data-library-category',
        'data-library-signal', 'type="checkbox"', "default_params",
    ):
        assert token in source
    assert "duplicateCondition" in source
    assert "ts-library-backdrop" in source
    assert "@media(max-width:720px)" in source


def test_signal_library_keeps_primary_action_inside_a_bounded_drawer() -> None:
    source = (ROOT / "frontend/js/views/technical_screener.js").read_text(encoding="utf-8")
    assert "width:clamp(560px,52vw,760px)" in source
    assert "top:var(--topbar-h);right:0;bottom:var(--statusbar-h);left:0" in source
    assert "height:100%" in source
    assert ".ts-library-foot{position:relative;z-index:2" in source
    assert ".ts-library-list{min-width:0;min-height:0;overflow-y:auto" in source
    assert ".ts-library-drawer{width:100vw;height:100%" in source
    assert "renderSignalLibraryPortal" in source
    assert "document.getElementById('modal-root')" in source
    assert "ts-library-portal" in source


def test_scheme_save_controls_stay_together_without_json_actions() -> None:
    source = (ROOT / "frontend/js/views/technical_screener.js").read_text(encoding="utf-8")
    assert "导入JSON" not in source
    assert "导出JSON" not in source
    assert "ts-import-file" not in source
    assert "function importScheme" not in source
    assert "function exportScheme" not in source
    assert '<select id="ts-scheme"' not in source
    assert '<datalist' not in source
    assert 'role="combobox"' in source
    assert 'role="listbox"' in source
    assert 'id="ts-scheme-toggle"' in source
    assert 'data-scheme-option' in source
    assert "ts-combo-chevron" in source
    assert 'id="ts-scheme-name"' in source
    assert 'id="ts-save"' in source
    assert "schemeNameDraft" in source
    assert "selectSchemeByName" in source
    assert "openSchemeCombo" in source
    assert "filterSchemeOptions" in source
    assert ".ts-scheme-actions{display:grid;grid-template-columns:minmax(120px,1fr) auto auto auto" in source


def test_presets_are_read_only_combobox_options_without_mode_split() -> None:
    source = (ROOT / "frontend/js/views/technical_screener.js").read_text(encoding="utf-8")
    assert "简单模式" not in source
    assert "高级模式" not in source
    assert "data-mode" not in source
    assert "ts-templates" not in source
    assert "selectedPresetId" in source
    assert "selectPreset" in source
    assert 'data-scheme-kind="preset"' in source
    assert "预置只读" in source
    assert "预置方案不可直接修改" in source
    assert "selectedPresetId || legacyReadOnlyPayload ? 'disabled'" in source
    assert "预置方案 · 只读" in source


def test_technical_screener_api_and_prewarm_are_registered() -> None:
    routes = (ROOT / "hermes_trader/api/routes.py").read_text(encoding="utf-8")
    main = (ROOT / "hermes_trader/main.py").read_text(encoding="utf-8")
    scheduler = (ROOT / "hermes_trader/jobs/scheduler.py").read_text(encoding="utf-8")
    for endpoint in (
        '/technical-screen/catalog', '/technical-screen/status',
        '/technical-screen/query', '/technical-screen/refresh',
        '/technical-screen/history', '/technical-screen/schemes',
    ):
        assert endpoint in routes
    assert "technical_screener.init(config.data_dir)" in main
    assert 'id="technical_signal_refresh"' in scheduler
