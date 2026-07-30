from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "frontend" / "js" / "views" / "_chain_shared.js"
CHAT = ROOT / "frontend" / "js" / "views" / "chat.js"


def test_chat_uses_the_exported_chain_request_contract() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    chat = CHAT.read_text(encoding="utf-8")

    assert "CHAIN_RESEARCH_CONTRACT_VERSION = 'chain-research-v2'" in contract
    assert "export function buildChainResearchRequest" in contract
    assert "buildChainResearchRequest({ industry, focus: msg, history, images: imgs })" in chat
    assert "msgMode === 'chain'" in chat
    assert "ssePost('/chat', requestBody" in chat


def test_chain_contract_is_content_first_and_separates_research_from_trade() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    chat = CHAT.read_text(encoding="utf-8")

    for phrase in ["核心需求与驱动力", "上中下游全景", "不要把报告写成数据清单", "产业研究与交易时点必须分开"]:
        assert phrase in contract
    assert "真实性边界只在报告末尾集中说明一次" in contract
    assert "export function chainResearchScopeHint" in contract
    assert "chainResearchScopeHint()" in chat


def test_chain_contract_preserves_images_and_does_not_change_other_chat_modes() -> None:
    contract = CONTRACT.read_text(encoding="utf-8")
    chat = CHAT.read_text(encoding="utf-8")

    assert "url.startsWith('data:image/')" in contract
    assert ".slice(0, 4)" in contract
    assert "code: ''" in contract
    assert "codes: []" in contract
    assert "用户附图只作补充线索" in contract
    assert ": { mode: msgMode, message: msg, code: msgCode, industry, history, images: imgs, codes }" in chat
