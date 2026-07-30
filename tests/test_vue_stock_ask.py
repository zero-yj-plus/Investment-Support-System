from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"


def _read(path: str) -> str:
    return (VUE / path).read_text(encoding="utf-8")


def test_vue_stock_ask_route_is_reachable_without_migrating_research_siblings() -> None:
    app = _read("src/App.vue")
    navigation = _read("src/navigation.ts")

    assert "const isStockAsk = computed(() => routePath.value === 'research' && routeTab.value === 'ask')" in app
    assert '<StockAskPage' in app
    assert 'v-else-if="isStockAsk"' in app
    assert "{ path: 'research', tab: 'ask', label: '问股' }" in navigation
    assert "tab: 'diagnosis', label: '个股诊断'" in navigation
    assert "tab: 'screener', label: '条件选股'" in navigation


def test_vue_stock_ask_uses_only_real_stock_chat_and_history_endpoints() -> None:
    source = _read("src/composables/useStockAsk.ts")

    for endpoint in (
        "/stock/${encodeURIComponent(code)}/core",
        "/stock/${encodeURIComponent(code)}/enrichment",
        "/chat/conversations",
        "/chat/conversations/${encodeURIComponent(id)}",
        "/chat/conversations/${encodeURIComponent(conversationId)}/messages",
        "postEventStream('/chat'",
    ):
        assert endpoint in source
    assert "mode: 'stock'" in source
    assert "/stock/${encodeURIComponent(code)}/charts" not in source
    assert "/market/" not in source
    assert "item.mode === 'stock'" in source


def test_vue_stock_ask_preserves_missing_partial_and_identity_semantics() -> None:
    composable = _read("src/composables/useStockAsk.ts")
    evidence = _read("src/components/StockAskEvidence.vue")
    formatting = _read("src/utils/stockAskFormat.ts")

    assert "payload.recognized === false || payload.state === 'not_found'" in composable
    assert "股票已识别，但首屏行情暂未返回" in composable
    assert "增强证据局部失败" in evidence
    assert "个股资金流暂未返回，不参与页面结论" in evidence
    assert "陈旧快照" in evidence
    assert "集合竞价" not in evidence
    assert "return finite(value) ? value.toFixed(2) : '—'" in formatting
    assert "?? 0" not in composable + evidence + formatting
    assert "1488.52" not in composable + evidence


def test_vue_stock_ask_cleans_requests_sse_timers_readers_and_listener() -> None:
    source = _read("src/composables/useStockAsk.ts")
    composer = _read("src/components/StockAskComposer.vue")
    api = _read("src/services/api.ts")

    assert "const controllers: Partial<Record<StockAskRequestKey, AbortController>>" in source
    assert "controllers[key]?.abort()" in source
    assert "generations[key] += 1" in source
    assert "alive && controllers[key] === controller && generations[key] === generation" in source
    assert "clearStageTimer()" in source
    assert "document.addEventListener('visibilitychange', onVisibilityChange)" in source
    assert "document.removeEventListener('visibilitychange', onVisibilityChange)" in source
    assert ";(Object.keys(controllers) as StockAskRequestKey[]).forEach(abortRequest)" in source
    assert "readers.forEach((reader) => reader.abort())" in composer
    assert "await reader.cancel().catch(() => undefined)" in api


def test_vue_stock_ask_uses_safe_markdown_and_native_interactions() -> None:
    paths = (
        "src/components/StockAskPage.vue",
        "src/components/StockAskConversation.vue",
        "src/components/StockAskComposer.vue",
        "src/components/StockAskHistory.vue",
        "src/components/StockAskMessage.vue",
        "src/components/StockAskEvidence.vue",
    )
    sources = "\n".join(_read(path) for path in paths)

    assert "v-html" not in sources
    assert "<ReportDocument" in sources
    assert 'type="button"' in sources
    assert 'role="button"' not in sources
    assert 'role="log"' in sources
    assert 'aria-live="polite"' in sources
    assert '<label for="stock-ask-code">' in sources
    assert 'inputmode="numeric"' in sources


def test_vue_stock_ask_is_dedicated_and_keeps_two_row_equal_height_composer() -> None:
    page = _read("src/components/StockAskPage.vue")
    composer = _read("src/components/StockAskComposer.vue")

    assert "本页不提供自由问答、持仓判断或产业链挖掘" in page
    assert "--composer-control-height: 52px" in composer
    assert "grid-template-rows: repeat(2, minmax(44px, auto))" in composer
    assert "height: var(--composer-control-height)" in composer
    assert "min-height: var(--composer-control-height)" in composer
    assert "max-height: var(--composer-control-height)" in composer
    assert "textarea" in composer and "resize: none" in composer
    assert "@media (max-width: 800px)" in composer
    assert "position: static" in composer


def test_vue_stock_ask_responsive_contract_and_audit_scenarios() -> None:
    page = _read("src/components/StockAskPage.vue")
    conversation = _read("src/components/StockAskConversation.vue")
    history = _read("src/components/StockAskHistory.vue")
    audit = _read("scripts/audit-ui.mjs")

    assert ".stock-ask-page { display: grid; min-width: 0;" in page
    assert "grid-template-columns: 56px minmax(460px, 1fr) minmax(260px, 320px)" in page
    assert ".stock-ask-workbench.is-history-expanded" in page
    assert "const historyExpanded = ref(false)" in page
    assert "stock-ask-selector panel" not in page
    assert 'class="stock-ask-conversation__selector"' in conversation
    assert '.stock-ask-conversation__selector button[type="submit"]' in audit
    assert 'aria-expanded="expanded"' in history
    assert 'v-if="expanded" id="stock-ask-history-content"' in history
    assert "@media (max-width: 1180px)" in page
    assert "@media (max-width: 800px)" in page
    assert "@media (max-width: 720px)" in page
    assert 'font-size: 18px' in page
    assert "name: 'stock-ask', hash: '#/research?tab=ask'" in audit
    for scenario in (
        "stock-ask-loading-375.png",
        "stock-ask-empty-1440.png",
        "stock-ask-partial-failure-1440.png",
    ):
        assert scenario in audit
    for endpoint in (
        "/api/chat/conversations",
        "/api/stock/600519/core",
        "/api/stock/600519/enrichment",
    ):
        assert endpoint in audit
