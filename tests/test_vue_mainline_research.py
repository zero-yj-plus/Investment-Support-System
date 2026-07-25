from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def _read(path: str) -> str:
    return (VUE / path).read_text(encoding="utf-8")


def test_vue_mainline_research_route_is_reachable_without_migrating_siblings() -> None:
    app = _read("src/App.vue")
    navigation = _read("src/navigation.ts")

    assert "const isMainlineResearch = computed(() => routePath.value === 'today' && routeTab.value === 'mainline')" in app
    assert '<MainlineResearchPage' in app
    assert "tab: 'mainline', label: '主线深研'" in navigation
    assert "v-else-if=\"isMainlineResearch\"" in app


def test_vue_mainline_research_uses_only_the_four_real_feature_endpoints() -> None:
    source = _read("src/composables/useMainlineResearch.ts")

    for endpoint in (
        "/features/money-effect",
        "/features/crowdedness",
        "/features/mainline",
        "/features/leaders",
    ):
        assert endpoint in source
    assert "await loadOne('moneyEffect')" in source
    assert "Promise.allSettled([" in source
    assert "/market/mainlines" not in source
    assert "/pulse" not in source


def test_vue_mainline_research_preserves_missing_empty_and_degraded_semantics() -> None:
    page = _read("src/components/MainlineResearchPage.vue")
    money = _read("src/components/MainlineMoneyEffect.vue")
    crowd = _read("src/components/MainlineCrowdedness.vue")
    continuity = _read("src/components/MainlineContinuity.vue")
    leaders = _read("src/components/MainlineLeaders.vue")
    formatting = _read("src/utils/mainlineFormat.ts")

    assert "接口成功返回真实空数组" in money
    assert "themes 字段缺失" in money
    assert "sectors 字段缺失" in crowd
    assert 'v-if="finite(item.crowd_score)"' in crowd
    assert "拥挤分未提供" in crowd
    assert "当前没有形成可展示的跨日主线" in continuity
    assert "当前批次未形成龙头梯队" in leaders
    assert "data_state === 'missing'" in formatting
    assert "data_state === 'degraded'" in formatting
    assert "data_state === 'accumulating'" in formatting
    assert "当前结论与行动边界" not in page
    assert "mainline-command" not in page
    assert "排名、分数、等级、梯队和切换信号由服务端返回" in page
    assert "return '—'" in formatting
    assert "?? 0" not in page + money + crowd + continuity + leaders


def test_vue_mainline_research_cleans_requests_timers_and_visibility_listener() -> None:
    source = _read("src/composables/useMainlineResearch.ts")

    assert "const controllers: Partial<Record<MainlineEndpoint, AbortController>>" in source
    assert "controllers[key]?.abort()" in source
    assert "if (refreshTimer) clearInterval(refreshTimer)" in source
    assert "document.addEventListener('visibilitychange', onVisibilityChange)" in source
    assert "document.removeEventListener('visibilitychange', onVisibilityChange)" in source
    assert "if (!alive || controllers[key] !== controller || generations[key] !== generation) return" in source
    assert "if (refreshing.value || Object.keys(controllers).length) return" in source


def test_vue_mainline_research_uses_safe_nodes_and_native_interactions() -> None:
    sources = "\n".join(
        _read(path)
        for path in (
            "src/components/MainlineResearchPage.vue",
            "src/components/MainlineMoneyEffect.vue",
            "src/components/MainlineCrowdedness.vue",
            "src/components/MainlineContinuity.vue",
            "src/components/MainlineLeaders.vue",
        )
    )

    assert "v-html" not in sources
    assert 'type="button"' in sources
    assert 'role="button"' not in sources
    assert 'aria-live="polite"' in sources
    assert 'role="progressbar"' in sources


def test_vue_mainline_research_responsive_contract_and_audit_scenarios() -> None:
    page = _read("src/components/MainlineResearchPage.vue")
    audit = _read("scripts/audit-ui.mjs")

    assert ".mainline-research-page { display: grid; min-width: 0;" in page
    assert "@media (max-width: 1180px)" in page
    assert "@media (max-width: 720px)" in page
    assert "grid-template-columns: minmax(0, 1fr)" in page
    assert "name: 'mainline', hash: '#/today?tab=mainline'" in audit
    for scenario in ("mainline-loading", "mainline-empty", "mainline-partial-failure"):
        assert scenario in audit
    for endpoint in (
        "/api/features/money-effect",
        "/api/features/crowdedness",
        "/api/features/mainline",
        "/api/features/leaders",
    ):
        assert endpoint in audit


def test_vue_mainline_research_emphasizes_numeric_evidence_without_fixed_oversized_text() -> None:
    page = _read("src/components/MainlineResearchPage.vue")
    money = _read("src/components/MainlineMoneyEffect.vue")
    crowd = _read("src/components/MainlineCrowdedness.vue")
    continuity = _read("src/components/MainlineContinuity.vue")
    leaders = _read("src/components/MainlineLeaders.vue")

    assert "--mainline-metric-size: clamp(16px, 1.15vw, 18px)" in page
    assert "--mainline-metric-compact-size: clamp(15px, 1vw, 16px)" in page
    assert "font-size: var(--mainline-metric-size, 17px)" in money
    assert crowd.count("font-size: var(--mainline-metric-size, 17px)") >= 2
    assert continuity.count("font-size: var(--mainline-metric-size, 17px)") >= 2
    assert leaders.count("font-size: var(--mainline-metric-compact-size, 16px)") >= 3
