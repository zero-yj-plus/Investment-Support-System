from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def _read(path: str) -> str:
    return (VUE / path).read_text(encoding="utf-8")


def test_vue_sentiment_events_route_is_reachable_without_migrating_siblings() -> None:
    app = _read("src/App.vue")
    navigation = _read("src/navigation.ts")

    assert "const isSentimentEvents = computed(() => routePath.value === 'today' && routeTab.value === 'news')" in app
    assert '<SentimentEventsPage' in app
    assert 'v-else-if="isSentimentEvents"' in app
    assert "tab: 'news', label: '舆情事件'" in navigation


def test_vue_sentiment_events_uses_only_the_real_a_share_news_endpoint() -> None:
    source = _read("src/composables/useSentimentEvents.ts")

    assert "getJson<SentimentEventsResponse>('/news/a-share'" in source
    for unrelated in ("/news/multi", "/news/a-share/observations", "/news/a-share/reconciliation", "/market/flow", "/pulse"):
        assert unrelated not in source


def test_vue_sentiment_events_preserves_missing_empty_degraded_and_units() -> None:
    page = _read("src/components/SentimentEventsPage.vue")
    card = _read("src/components/SentimentEventCard.vue")
    formatting = _read("src/utils/sentimentFormat.ts")

    assert "真实空结果" in page
    assert "这不是“没有事件”" in page
    assert "部分覆盖" in page
    assert "沿用数据" in page
    assert "return '—'" in formatting
    assert "`${number > 0 ? '+' : ''}${number.toFixed(digits)}%`" in formatting
    assert "暂无盘面观察" in formatting
    assert "不判断利多利空" in page + card
    assert "?? 0" not in page + card + formatting
    assert "|| 0" not in page + card + formatting


def test_vue_sentiment_events_cleans_request_timer_and_visibility_listener() -> None:
    source = _read("src/composables/useSentimentEvents.ts")

    assert "let sentimentSessionSnapshot: SentimentEventsResponse | null = null" in source
    assert "ref<SentimentEventsResponse | null>(sentimentSessionSnapshot)" in source
    assert "const loading = ref(!sentimentSessionSnapshot)" in source
    assert "sentimentSessionSnapshot = payload" in source
    assert "refresh({ background: Boolean(sentimentSessionSnapshot) })" in source
    assert "let eventController: AbortController | null = null" in source
    assert "eventController?.abort()" in source
    assert "if (!alive || controller !== eventController) return" in source
    assert "if (refreshing.value) return" in source
    assert "if (refreshTimer) clearInterval(refreshTimer)" in source
    assert "document.addEventListener('visibilitychange', onVisibilityChange)" in source
    assert "document.removeEventListener('visibilitychange', onVisibilityChange)" in source
    assert "}, 30_000)" in source


def test_vue_sentiment_events_uses_safe_nodes_links_and_native_interactions() -> None:
    sources = "\n".join(
        _read(path)
        for path in (
            "src/components/SentimentEventsPage.vue",
            "src/components/SentimentEventFeed.vue",
            "src/components/SentimentEventCard.vue",
        )
    )
    formatting = _read("src/utils/sentimentFormat.ts")

    assert "v-html" not in sources
    assert 'type="button"' in sources
    assert '<a v-if="safeHttpUrl(source.source_url)"' in sources
    assert 'rel="noopener noreferrer"' in sources
    assert ':aria-expanded="expanded"' in sources
    assert 'role="button"' not in sources
    assert "parsed.protocol === 'http:' || parsed.protocol === 'https:'" in formatting
    assert 'aria-live="polite"' in sources


def test_vue_sentiment_events_responsive_contract_and_audit_scenarios() -> None:
    page = _read("src/components/SentimentEventsPage.vue")
    feed = _read("src/components/SentimentEventFeed.vue")
    card = _read("src/components/SentimentEventCard.vue")
    audit = _read("scripts/audit-ui.mjs")

    assert ".sentiment-page { display: grid; min-width: 0;" in page
    assert "SentimentEventSummary" not in page
    assert "sentiment-skeleton--summary" not in page
    assert ".sentiment-event-card { display: grid; min-width: 0;" in card
    assert "@media (max-width: 900px)" in feed + card
    assert "grid-template-columns: minmax(0, 1fr)" in page + feed + card
    assert "min-height: 44px" in feed + card
    assert "最近抓取 / 页面快照" in page
    assert "陈旧快照" in page
    assert "后台更新中" in page
    assert "data.value?.snapshot?.status === 'stale'" in page
    assert ".sentiment-snapshot-state.is-stale" in page
    assert "@media (prefers-reduced-motion: reduce)" in page
    assert "name: 'sentiment', hash: '#/today?tab=news'" in audit
    for scenario in ("sentiment-loading-375", "sentiment-empty-1440", "sentiment-partial-failure-1440"):
        assert scenario in audit
    assert "'/api/news/a-share'" in audit


def test_sentiment_audit_fixture_is_semantic_and_stays_out_of_production() -> None:
    audit = _read("scripts/audit-ui.mjs")
    production = "\n".join(
        _read(path)
        for path in (
            "src/components/SentimentEventsPage.vue",
            "src/components/SentimentEventFeed.vue",
            "src/components/SentimentEventCard.vue",
            "src/composables/useSentimentEvents.ts",
            "src/types/sentimentEvents.ts",
            "src/utils/sentimentFormat.ts",
        )
    )

    assert "世茂能源发布股权转让公告" in audit
    assert "钙钛矿电池研究取得新进展" in audit
    assert "世茂能源发布股权转让公告" not in production
    assert "钙钛矿电池研究取得新进展" not in production
