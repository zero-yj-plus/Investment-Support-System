from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_CSS = ROOT / "frontend/css/app.css"
RESPONSIVE_CSS = ROOT / "frontend/css/responsive.css"
QUANT_JS = ROOT / "frontend/js/views/quant.js"
TAIL_JS = ROOT / "frontend/js/views/tail.js"
CHAT_JS = ROOT / "frontend/js/views/chat.js"
API_JS = ROOT / "frontend/js/api.js"


def test_quant_backtest_uses_responsive_grid_and_scrollable_presets() -> None:
    source = QUANT_JS.read_text(encoding="utf-8")

    assert 'class="kpi-grid q-backtest-kpis"' in source
    assert 'class="kpi-grid" style="grid-template-columns:repeat(6,1fr)"' not in source
    assert ".q-backtest-kpis{grid-template-columns:repeat(6,minmax(0,1fr))}" in source
    assert "@media (max-width:1024px){.q-backtest-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}" in source
    assert ".q-backtest-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}" in source
    assert "#q-bt-seg{max-width:100%;overflow-x:auto" in source
    assert "#q-bt-seg .seg{flex:0 0 auto}" in source


def test_chat_stock_rail_stays_side_by_side_on_common_desktop_widths() -> None:
    source = CHAT_JS.read_text(encoding="utf-8")

    assert "@media (max-width:1180px)" in source
    assert "@media (max-width:1400px)" not in source
    assert "sideCollapsed = window.innerWidth <= 1180" in source
    assert ".chat-layout.has-stock-side .chat-side" in source
    assert "position:absolute" in source


def test_tail_and_global_mobile_layout_share_the_768_breakpoint() -> None:
    tail = TAIL_JS.read_text(encoding="utf-8")
    responsive = RESPONSIVE_CSS.read_text(encoding="utf-8")

    assert "@media (max-width:768px)" in tail
    assert "@media (max-width:760px)" not in tail
    assert ".tail-grid>*{min-width:0}" in tail
    assert ".tail-card{display:flex;flex-direction:column;min-width:0}" in tail
    assert ".card-head > .card-extra { width: 100%; min-width: 0; margin-left: 0; flex-wrap: wrap; }" in responsive


def test_key_microcopy_is_readable_and_model_errors_are_provider_neutral() -> None:
    app_css = APP_CSS.read_text(encoding="utf-8")
    quant = QUANT_JS.read_text(encoding="utf-8")
    tail = TAIL_JS.read_text(encoding="utf-8")
    chat = CHAT_JS.read_text(encoding="utf-8")
    api = API_JS.read_text(encoding="utf-8")

    assert ".therm-labels { display: flex; gap: 2px; font-size: 12px;" in app_css
    assert ".q-fbar-lab{position:relative;z-index:1;font-size:12px" in quant
    assert ".tail-per-window>div" in tail and "font-size:12px;color:var(--text-3);min-width:0" in tail
    assert "请到设置检查当前模型连接" in chat
    assert "仅当该连接使用 Hermes 时再检查 Hermes 集成" in chat
    assert "仅在当前连接使用 Hermes 时再检查 Hermes 集成" in api
    assert "请在 Hermes 客户端" not in chat
    assert "请检查 Hermes 网关与模型是否正常" not in api
