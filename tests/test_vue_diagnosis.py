from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def _read(relative: str) -> str:
    return (VUE / relative).read_text(encoding="utf-8")


def test_vue_diagnosis_route_is_reachable_without_migrating_research_siblings() -> None:
    app = _read("src/App.vue")
    navigation = _read("src/navigation.ts")

    assert "const isDiagnosis = computed(() => routePath.value === 'research' && routeTab.value === 'diagnosis')" in app
    assert '<DiagnosisPage' in app
    assert 'v-else-if="isDiagnosis"' in app
    assert "{ path: 'research', tab: 'diagnosis', label: '个股诊断' }" in navigation
    assert "{ path: 'research', tab: 'screener', label: '条件选股' }" in navigation
    assert "{ path: 'research', tab: 'quant-pick', label: '每日量化选股' }" not in navigation


def test_vue_diagnosis_uses_only_real_quote_and_governed_diagnosis_endpoints() -> None:
    source = _read("src/composables/useDiagnosis.ts")

    assert "`/quote/${encodeURIComponent(code)}`" in source
    assert "`/diagnose?code=${encodeURIComponent(code)}`" in source
    assert "Promise.allSettled([loadQuote(code), loadDiagnosis(code)])" in source
    for unrelated in ("/stock/${", "/chat", "/market/", "/holdings", "/reports/"):
        assert unrelated not in source


def test_vue_diagnosis_keeps_missing_degraded_conflict_and_empty_semantics() -> None:
    page = _read("src/components/DiagnosisPage.vue")
    evidence = _read("src/components/DiagnosisEvidence.vue")
    health = _read("src/components/DiagnosisDataHealth.vue")
    formatter = _read("src/utils/diagnosisFormat.ts")
    combined = page + evidence + health + formatter

    for phrase in (
        "当前快速行情",
        "仅供核对 · 未绑定证据 ID",
        "证据链真实空数组",
        "stages 字段缺失或为空",
        "缺失项不会补 0",
        "陈旧或部分覆盖",
        "跨源冲突",
        "本次不输出新的交易动作",
    ):
        assert phrase in combined
    assert "value == null || value === ''" in formatter
    assert "return parsed == null ? '—'" in formatter
    assert "point.unit === 'percent'" in formatter
    assert "point.unit === 'CNY'" in formatter
    assert "point.unit === 'ratio'" in formatter
    assert "point.unit === 'share'" in formatter
    assert "market_evidence_timeout" in formatter
    assert "diagnosis_data_capacity_busy" in formatter
    assert "market_evidence_failed" in formatter
    assert "当前没有诊断数据。页面不会自动请求其他业务接口，也不会用示例股票、演示数字或历史值填充。" not in page
    assert "结论绑定的行情证据" not in evidence
    assert "正式行情证据采集未完成" not in evidence
    assert "未绑定正式证据" not in evidence
    assert ':quote="displayedQuote"' in page


def test_vue_diagnosis_has_independent_abort_and_stale_response_guards() -> None:
    source = _read("src/composables/useDiagnosis.ts")

    for contract in (
        "let quoteController: AbortController | null = null",
        "let diagnosisController: AbortController | null = null",
        "quoteController?.abort()",
        "diagnosisController?.abort()",
        "controller !== quoteController",
        "controller !== diagnosisController",
        "let alive = false",
        "if (!alive || controller !==",
        "window.removeEventListener('hashchange', syncIncomingCode)",
    ):
        assert contract in source
    assert "if (diagnosisLoading.value && requestedCode.value === code) return false" in source
    assert "const keepCurrent = requestedCode.value === code && diagnosis.value != null" in source
    assert "if (!keepCurrent)" in source


def test_vue_diagnosis_uses_safe_text_and_native_interaction_semantics() -> None:
    sources = [
        _read("src/components/DiagnosisPage.vue"),
        _read("src/components/DiagnosisSearch.vue"),
        _read("src/components/DiagnosisDecision.vue"),
        _read("src/components/DiagnosisEvidence.vue"),
        _read("src/components/DiagnosisDataHealth.vue"),
    ]
    combined = "".join(sources)

    assert "v-html" not in combined
    assert "innerHTML" not in combined
    assert 'role="button"' not in combined
    assert '<form' in combined and '<label' in combined and '<input' in combined
    assert '<button' in combined
    assert '<a href="#/research?tab=ask"' in combined
    assert 'inputmode="numeric"' in combined
    assert 'aria-live="polite"' in combined
    assert "min-height: 44px" in combined


def test_vue_diagnosis_surfaces_governed_action_and_compacts_secondary_data() -> None:
    search = _read("src/components/DiagnosisSearch.vue")
    decision = _read("src/components/DiagnosisDecision.vue")
    evidence = _read("src/components/DiagnosisEvidence.vue")
    health = _read("src/components/DiagnosisDataHealth.vue")
    types = _read("src/types/diagnosis.ts")

    for phrase in (
        "结构信号",
        "服务端治理后",
        "关键行情证据不完整",
        "等待触发",
        "结论失效",
        "不代表低风险或可以买入",
    ):
        assert phrase in decision
    assert "cap.value == null" in decision
    assert "新增仓位上限 ${formatNumber(cap.value, 2)}%" in decision
    assert "当前动作不增加新风险；未提供值不可按 0% 解读" not in decision
    assert "deep_signal?:" in types
    assert "六维诊断依据" in evidence
    assert "repeat(4, minmax(0, 1fr))" in evidence
    assert "repeat(3, minmax(0, 1fr))" in evidence
    assert "diagnosis-health__audit" in health
    assert "展开规则、模型、留痕与全部缺口" in health
    assert "diagnosis-search__controls" in search
    assert "grid-template-columns: minmax(360px, 1fr) auto" in search


def test_vue_diagnosis_has_responsive_and_reduced_motion_rules() -> None:
    page = _read("src/components/DiagnosisPage.vue")
    decision = _read("src/components/DiagnosisDecision.vue")
    evidence = _read("src/components/DiagnosisEvidence.vue")
    search = _read("src/components/DiagnosisSearch.vue")
    combined = page + decision + evidence + search

    for breakpoint in ("max-width: 1180px", "max-width: 980px", "max-width: 720px"):
        assert breakpoint in combined
    assert "grid-template-columns: minmax(0, 1fr)" in combined
    assert "min-width: 0" in combined
    assert "prefers-reduced-motion: reduce" in page
    assert "overflow-x: auto" not in combined


def test_vue_diagnosis_audit_has_real_semantic_fixture_and_required_states() -> None:
    audit = _read("scripts/audit-ui.mjs")

    assert "name: 'diagnosis'" in audit
    assert "#/research?tab=diagnosis&code=600519" in audit
    assert "'/api/quote/600519'" in audit
    assert "'/api/diagnose'" in audit
    assert "diagnosisEvidenceStateComplete" in audit
    for screenshot in (
        "diagnosis-loading-375.png",
        "diagnosis-empty-1440.png",
        "diagnosis-partial-failure-1440.png",
        "diagnosis-evidence-unavailable-1440.png",
    ):
        assert screenshot in audit
