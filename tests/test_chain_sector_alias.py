from hermes_trader.analysis.engine import _chain_sector_search_terms


def test_light_module_query_maps_to_tdx_catalog_names_without_guessing_members() -> None:
    terms = _chain_sector_search_terms("光模块")

    assert terms[0] == "光模块"
    assert "CPO概念" in terms
    assert "光通信" in terms


def test_unknown_chain_keeps_only_the_user_term() -> None:
    assert _chain_sector_search_terms("机器人") == ("机器人",)


def test_ai_liquid_cooling_maps_to_tdx_catalog_name() -> None:
    assert _chain_sector_search_terms("AI液冷") == ("AI液冷", "液冷服务器")
