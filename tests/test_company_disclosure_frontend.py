from __future__ import annotations

from pathlib import Path


COMPANY = Path(__file__).resolve().parents[1] / "frontend/js/views/company.js"


def test_company_research_loads_official_disclosures_without_slow_pdf_verification() -> None:
    source = COMPANY.read_text(encoding="utf-8")

    assert "/disclosures/announcements?code=${encodeURIComponent(code)}&limit=6" in source
    assert "verify_pdf" not in source
    assert "void disclosureTask" in source
    assert "repaintOfficialDisclosures()" in source


def test_company_disclosure_panel_preserves_complete_degraded_and_missing_semantics() -> None:
    source = COMPANY.read_text(encoding="utf-8")

    assert "官方公告证据" in source
    assert "verification === 'empty_confirmed'" in source
    assert "双目录空结果已核对" in source
    assert "部分官方链路可用" in source
    assert "当前无法读取官方公告" in source
    assert "不能据此判断查询窗口内“无公告”" in source
    assert "现有结果不足以证明查询窗口内确实无公告" in source
    assert "近 30 日" not in source


def test_company_disclosure_window_comes_from_api_contract() -> None:
    source = COMPANY.read_text(encoding="utf-8")

    assert "payload?.requested_window?.start" in source
    assert "payload?.requested_window?.end" in source
    assert "查询窗口内未发现公告" in source
    assert "查询窗口以上游返回为准" in source


def test_company_disclosures_show_per_item_cross_confirmation_and_safe_original_link() -> None:
    source = COMPANY.read_text(encoding="utf-8")

    assert "source_confirmations" in source
    assert "['cross_confirmed', 'partially_cross_confirmed'].includes(verification)" in source
    assert "巨潮 + ${exchangeName}均有同题记录" in source
    assert "不代表公告内容得到独立信源互证" in source
    assert "item.announcement_date || item.published_at" in source
    assert "item.title || '未命名公告'" in source
    assert "safeOfficialUrl(item.original_url)" in source
    assert 'target="_blank" rel="noopener noreferrer"' in source
    assert "parsed.protocol === 'https:'" in source
    assert "static.cninfo.com.cn" in source
    assert "static.sse.com.cn" in source
    assert "disc.static.szse.cn" in source
    assert "officialHosts.has(parsed.hostname.toLowerCase())" in source
    assert "(!parsed.port || parsed.port === '443')" in source
    assert "!parsed.username && !parsed.password" in source


def test_company_loads_management_interactions_without_blocking_research() -> None:
    source = COMPANY.read_text(encoding="utf-8")

    assert "/interactions/management?code=${encodeURIComponent(code)}&limit=6" in source
    assert "const interactionTask = loadManagementInteractions(code, { repaint: false })" in source
    assert "void interactionTask" in source
    assert "${renderOfficialDisclosures()}\n    ${renderManagementInteractions()}" in source


def test_company_interaction_panel_preserves_all_public_states() -> None:
    source = COMPANY.read_text(encoding="utf-8")

    for state in ("complete", "complete_empty", "degraded", "last_good", "missing", "not_supported"):
        assert f"{state}:" in source
    assert "查询窗口内暂无公开互动问答" in source
    assert "不能据此判断查询窗口内没有互动问答" in source
    assert "官方互动问答链路当前不可用；这不是“无记录”结论" in source
    assert "旧快照的空结果不能证明现在仍无互动问答" in source
    assert "当前仅覆盖深市互动易" in source
    assert "这是覆盖范围说明，不代表数据读取失败" in source


def test_company_interaction_panel_separates_question_and_management_reply() -> None:
    source = COMPANY.read_text(encoding="utf-8")

    assert "item.question_pub_date" in source
    assert "item.answer_pub_date" in source
    assert "item.answered === true && Boolean(answer)" in source
    assert "投资者提问" in source
    assert "管理层回复" in source
    assert "尚未收到公开回复，不标记为“管理层回复”" in source
    assert "is_formal_disclosure: false" in source
    assert "非正式公告" in source
    assert "Tier 3" in source
    assert "不参与日内评分" in source


def test_company_interaction_request_is_invalidated_on_history_switch_and_unmount() -> None:
    source = COMPANY.read_text(encoding="utf-8")

    assert "const seq = ++managementInteractionsSeq" in source
    assert "seq !== managementInteractionsSeq || code !== curCode" in source
    assert "managementInteractions = null" in source
    assert "managementInteractionsLoading = false" in source
    assert "void loadManagementInteractions(h.code, { repaint: false })" in source
    assert "managementInteractionsSeq += 1" in source
    assert source.count("managementInteractions = null") >= 5
