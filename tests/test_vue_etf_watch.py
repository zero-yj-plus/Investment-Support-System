from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend-vue" / "src" / "components" / "EtfWatchPage.vue"
COMPOSABLE = ROOT / "frontend-vue" / "src" / "composables" / "useEtfWatch.ts"


def test_etf_watch_is_registered_as_a_real_page() -> None:
    navigation = (ROOT / "frontend-vue" / "src" / "navigation.ts").read_text(encoding="utf-8")
    app = (ROOT / "frontend-vue" / "src" / "App.vue").read_text(encoding="utf-8")

    assert "{ path: 'risk', tab: 'etf-watch', label: 'ETF观察' }" in navigation
    assert "EtfWatchPage" in app
    assert "isEtfWatch" in app


def test_etf_watch_page_uses_real_api_and_cleans_lifecycle_resources() -> None:
    page = PAGE.read_text(encoding="utf-8")
    composable = COMPOSABLE.read_text(encoding="utf-8")

    assert "getJson<EtfWatchResponse>('/etf-watch'" in composable
    assert "postJson<EtfWatchMutationResponse>('/etf-watch/import'" in composable
    assert "postJson<EtfWatchMutationResponse>('/etf-watch/refresh'" in composable
    assert "clearInterval(refreshTimer)" in composable
    assert "clearTimeout(timer)" in composable
    assert "listController?.abort()" in composable
    assert "mutationController?.abort()" in composable
    assert "removeEventListener('visibilitychange'" in composable
    assert "v-html" not in page
    assert "?? 0" not in page and "|| 0" not in page


def test_etf_watch_avoids_dash_heavy_fields_and_dual_cycle_calculation() -> None:
    page = PAGE.read_text(encoding="utf-8")

    assert "ETF观察" in page
    assert "基金资料补充中" in page
    assert "历史指标补充中" in page
    assert "费率申赎补充中" in page
    assert "PE/PB、五档盘口、IOPV、资金分单" in page
    assert "ideal_buy_zones" not in page
    assert "ultra" not in page and "swing" not in page
    assert "510300" not in page and "513100" not in page and "159915" not in page
    assert "@media (max-width: 680px)" in page
    assert "min-height: 44px" in page
    assert "prefers-reduced-motion" in page
