from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COCKPIT = ROOT / "frontend/js/views/cockpit.js"


def test_cockpit_loads_critical_cards_before_deferred_evidence() -> None:
    source = COCKPIT.read_text(encoding="utf-8")
    mount = source[source.index("export function mount(el)") : source.index("export function unmount()")]
    initial = mount[mount.index("_activeDeferred.clear();") : mount.index("timers.push(setInterval")]

    assert "refreshCockpit({ force: true });" in initial
    assert initial.index("refreshCockpit({ force: true });") < initial.index("setupDeferredLoading();")
    assert "}, 1000);" in initial
    for direct_loader in ("loadSectorFlow();", "loadStockFlow();", "loadMultiperiod();", "loadHeatmap();"):
        assert direct_loader not in initial

    for key in ("sectorFlow", "stockFlow", "heatmap", "multiperiod"):
        assert f'data-cockpit-lazy="{key}"' in mount


def test_cockpit_deferred_cards_activate_near_the_viewport() -> None:
    source = COCKPIT.read_text(encoding="utf-8")

    assert "const DEFERRED_LOADERS = {" in source
    assert "sectorFlow: { load: loadSectorFlow, refreshDuringTrading: true }" in source
    assert "stockFlow: { load: loadStockFlow, refreshDuringTrading: true }" in source
    assert "heatmap: { load: loadHeatmap, refreshDuringTrading: false }" in source
    assert "multiperiod: { load: loadMultiperiod, refreshDuringTrading: false }" in source
    assert "new IntersectionObserver((entries, observer) =>" in source
    assert "{ rootMargin: '160px 0px', threshold: 0.01 }" in source
    assert "activateDeferred(entry.target.dataset.cockpitLazy);" in source
    assert "observer.unobserve(entry.target);" in source
    assert "_deferredObserver.disconnect();" in source


def test_cockpit_refreshes_keep_original_cadence_without_duplicate_request_storms() -> None:
    source = COCKPIT.read_text(encoding="utf-8")
    visibility = source[source.index("_visHandler = () =>") : source.index("document.addEventListener", source.index("_visHandler = () =>"))]

    assert "const _loaderInFlight = new Map();" in source
    assert "if (running) return running;" in source
    assert "if (!force && now - _lastRefreshAt < 1200)" in source
    assert "return Promise.allSettled(tasks);" in source
    assert "}, 30000));" in source
    assert "}, 3000));" in source
    assert "if (_visTimer) clearTimeout(_visTimer);" in visibility
    assert "}, 120);" in visibility
    assert "refreshCockpit({ refreshDeep: true })" in visibility
    for direct_loader in ("loadSummary();", "loadSectorFlow();", "loadStockFlow();", "loadMultiperiod();", "loadHeatmap();"):
        assert direct_loader not in visibility
    assert "return cockpitTask.then((value) =>" not in source


def test_cockpit_reuses_global_swr_cache_and_releases_local_singleflight() -> None:
    source = COCKPIT.read_text(encoding="utf-8")
    bounded = source[source.index("function getBounded(") : source.index("function runLoader(")]
    unmount = source[source.index("export function unmount()") : source.index("function gotoDiag(")]

    assert "api.get(path, { timeoutMs })" in bounded
    assert "ttlMs: 0" not in bounded
    assert "new AbortController" not in bounded
    assert ".finally(() =>" in bounded
    assert "_requestInFlight.delete(path)" in bounded
    assert "_requestInFlight.clear();" in unmount
    assert "controller.abort" not in unmount


def test_cockpit_renders_pulse_before_bounded_optional_narrative() -> None:
    source = COCKPIT.read_text(encoding="utf-8")
    summary = source[source.index("async function loadSummary()") : source.index("/* ========== 盘型/环境")]

    assert summary.index("await getBounded('/pulse', 6000)") < summary.index("getBounded('/cockpit', 6000)")
    assert "_summaryEnhanceTimer = setTimeout(() =>" in summary
    assert "}, 900);" in summary
    assert "const cockpitTask" not in summary


def test_cockpit_uses_fast_overview_and_defers_sparkline_enrichment() -> None:
    source = COCKPIT.read_text(encoding="utf-8")

    assert source.count("getBounded('/market/overview_cached', 4000)") == 2
    assert "getBounded('/market/overview'," not in source
    assert "for (const [nm, full] of SPARK_IDX)" in source
    assert "runLoader('boardSparklines', loadBoardSparklines);" in source
    assert "if (!_sparksLoaded) runLoader('boardSparklines', loadBoardSparklines);" in source
    assert "}, 2500);" in source
