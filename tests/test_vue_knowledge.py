from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VUE = ROOT / "frontend-vue"
PAGE = VUE / "src/components/KnowledgePage.vue"
SUMMARY = VUE / "src/components/KnowledgeSummary.vue"
FILTERS = VUE / "src/components/KnowledgeFilters.vue"
CARD = VUE / "src/components/KnowledgeCard.vue"
DIALOG = VUE / "src/components/KnowledgeActionDialog.vue"
COMPOSABLE = VUE / "src/composables/useKnowledge.ts"
TYPES = VUE / "src/types/knowledge.ts"
AUDIT = VUE / "scripts/audit-ui.mjs"
RULES = ROOT / "design-system/wudi-decision-console/pages/knowledge.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def production_source() -> str:
    return "\n".join(read(path) for path in (PAGE, SUMMARY, FILTERS, CARD, DIALOG, COMPOSABLE, TYPES))


def test_vue_knowledge_route_is_reachable_without_migrating_other_pages() -> None:
    app = read(VUE / "src/App.vue")
    navigation = read(VUE / "src/navigation.ts")

    assert "import KnowledgePage from './components/KnowledgePage.vue'" in app
    assert "routePath.value === 'history' && routeTab.value === 'knowledge'" in app
    assert 'v-else-if="isKnowledge"' in app
    assert "<KnowledgePage" in app
    assert "if (isKnowledge.value && knowledgePage.value)" in app
    assert "{ path: 'history', tab: 'knowledge', label: '知识库' }" in navigation


def test_vue_knowledge_uses_only_real_knowledge_contracts() -> None:
    composable = read(COMPOSABLE)

    for endpoint in (
        "getJson<KnowledgeResponse>('/knowledge'",
        "postJson<KnowledgeMutationResponse>('/knowledge'",
        "`/knowledge/${encodeURIComponent(id)}/approve`",
        "`/knowledge/${encodeURIComponent(id)}/reject`",
        "`/knowledge/${encodeURIComponent(id)}/supersede`",
        "`/knowledge/${encodeURIComponent(id)}`",
    ):
        assert endpoint in composable
    for unrelated in ("/market/", "/reports/", "/holdings", "/watchlist", "/screen", "/diagnose", "/chat"):
        assert unrelated not in composable


def test_vue_knowledge_preserves_missing_empty_and_governance_semantics() -> None:
    source = production_source()
    types = read(TYPES)

    for field in (
        "schema_version", "review_queue_count", "rule_id", "status", "version", "origin",
        "source_report_ids", "evidence_ids", "sample_count", "approved_at", "rejected_at",
        "superseded_at", "supersedes_id", "replacement_id",
    ):
        assert field in types
    for phrase in (
        "items 缺失或不是数组，不能当作空知识库",
        "知识库暂无条目",
        "真实空数组",
        "状态未提供",
        "来源字段未提供",
        "来源字段局部缺失",
        "样本数",
        "不是独立数据日期或实时性证明",
        "本接口不提供风险等级、仓位上限或交易结论",
        "当前后端契约会把用户明确录入直接记为 approved",
        "旧版内容与版本号仍保留",
    ):
        assert phrase in source
    assert "?? 0" not in source
    assert "|| 0" not in source


def test_vue_knowledge_cleans_each_request_and_ignores_late_responses() -> None:
    composable = read(COMPOSABLE)

    for controller in (
        "listController", "createController", "approveController", "rejectController",
        "supersedeController", "deleteController",
    ):
        assert f"let {controller}: AbortController | null = null" in composable
        assert f"{controller}?.abort()" in composable
    for generation in ("list", "create", "approve", "reject", "supersede", "delete"):
        assert f"generations.{generation}" in composable or "generations[type]" in composable
    assert "let alive = false" in composable
    assert "if (!alive || controller !== listController" in composable
    assert "if (!alive || actionController(type) !== controller" in composable
    assert "removeEventListener('visibilitychange', onVisibilityChange)" in composable
    assert "if (activeAction.value) return false" in composable
    assert "if (refreshing.value) return" in composable
    assert "clearTimeout(loadingDelayTimer)" in read(PAGE)


def test_vue_knowledge_uses_safe_nodes_and_native_interactions() -> None:
    source = production_source()
    page = read(PAGE)
    dialog = read(DIALOG)
    card = read(CARD)

    assert " v-html=" not in source
    assert ".innerHTML" not in source
    assert "white-space: pre-wrap" in card
    assert "{{ textLabel(item.content, '正文未提供') }}" in card
    assert '<dialog ref="dialog"' in dialog
    assert '<form method="dialog" @submit.prevent="submit">' in dialog
    assert '@cancel.prevent="requestClose"' in dialog
    assert 'inputmode="numeric"' in dialog
    assert "<Teleport to=\"body\">" in dialog
    assert "opener?.focus()" in dialog
    assert '<button' in page and 'type="button"' in page
    assert 'role="button"' not in source
    assert 'aria-live="polite"' in page


def test_vue_knowledge_has_responsive_containment_and_accessible_targets() -> None:
    source = production_source()
    page = read(PAGE)

    assert ".knowledge-page { display: grid; min-width: 0;" in page
    assert "grid-template-columns: 240px minmax(0, 1fr)" in page
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in page
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in page
    assert "min-height: 44px" in source
    for breakpoint in (1180, 900, 720, 520):
        assert f"@media (max-width: {breakpoint}px)" in source
    assert "@media (prefers-reduced-motion: reduce)" in source
    assert ":focus-visible" in read(VUE / "src/styles.css")
    assert "box-shadow" not in source
    assert "linear-gradient" not in source


def test_vue_knowledge_keeps_summary_and_cards_compact() -> None:
    summary = read(SUMMARY)
    card = read(CARD)
    page = read(PAGE)

    for removed in (
        "GOVERNED KNOWLEDGE",
        "当前无待审条目",
        "只有服务端状态为“已批准”的规则才会进入后续 AI 知识摘要。",
        "knowledge-summary__schema",
    ):
        assert removed not in summary
    assert "response?.schema_version" not in page
    for removed in ("<dt>样本数</dt>", "<dt>来源状态</dt>", "<dt>产生方式</dt>"):
        assert removed not in card
    assert card.count("<dt>") == 1
    assert "<dt>记录时点</dt>" in card
    assert "-webkit-line-clamp: 4" in card


def test_vue_knowledge_has_page_rules_and_real_semantic_audit_fixture() -> None:
    rules = read(RULES)
    audit = read(AUDIT)

    assert "#/history?tab=knowledge" in rules
    for endpoint in (
        "GET /api/knowledge", "POST /api/knowledge", "POST /api/knowledge/{id}/approve",
        "POST /api/knowledge/{id}/reject", "POST /api/knowledge/{id}/supersede",
        "DELETE /api/knowledge/{id}",
    ):
        assert endpoint in rules
    assert "不采用技能检索返回的 Bento 营销布局" in rules
    assert "name: 'knowledge', hash: '#/history?tab=knowledge'" in audit
    assert "审计待复核：确认信号需跨日验证" in audit
    for scenario in (
        "knowledge-loading-375",
        "knowledge-empty-1440",
        "knowledge-partial-failure-1440",
    ):
        assert scenario in audit
    assert "knowledgeLoadingStateComplete" in audit
    assert "knowledgeEmptyStateComplete" in audit
    assert "knowledgeFailureStateComplete" in audit
    assert "审计待复核：确认信号需跨日验证" not in production_source()
