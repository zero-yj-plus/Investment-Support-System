from pathlib import Path
import re

from hermes_trader import __version__
from hermes_trader.main import _VERSIONED_CACHE_CONTROL, frontend_cache_control


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend" / "index.html"
NAVIGATION = ROOT / "frontend" / "js" / "navigation.js"
APP = ROOT / "frontend" / "js" / "app.js"


def test_frontend_entry_uses_running_release_version() -> None:
    source = INDEX.read_text(encoding="utf-8")

    assert f'<meta name="wudi-asset-version" content="{__version__}">' in source
    assert f'src="js/app.js?v={__version__}"' in source
    assert frontend_cache_control("/js/app.js", __version__) == _VERSIONED_CACHE_CONTROL


def test_all_exact_frontend_asset_versions_match_release() -> None:
    source = INDEX.read_text(encoding="utf-8")
    versions = re.findall(r"[?&]v=([0-9]+\.[0-9]+\.[0-9]+)(?:[^\"&]*)", source)

    assert versions
    assert set(versions) == {__version__}


def test_release_keeps_technical_screener_in_native_workspace() -> None:
    navigation = NAVIGATION.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    assert "tab('technical-screener', '技术面选股', 'technical_screener')" in navigation
    assert "sidebarChild('research', 'technical-screener', '技术面选股')" in navigation
    assert "./views/technical_screener.js" in app
