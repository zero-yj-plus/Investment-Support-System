from pathlib import Path
import re

from hermes_trader import __version__
from hermes_trader.main import _VERSIONED_CACHE_CONTROL, frontend_cache_control


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "frontend-vue" / "dist" / "index.html"
LEGACY_INDEX = ROOT / "frontend" / "index.html"
NAVIGATION = ROOT / "frontend" / "js" / "navigation.js"
APP = ROOT / "frontend" / "js" / "app.js"
API_CONTRACT = ROOT / "docs" / "API_CONTRACT.md"
PROJECT_GUIDE = ROOT / "PROJECT_GUIDE.md"
SOURCE_DELIVERY = ROOT / "SOURCE_DELIVERY.txt"
WINDOWS_PACKAGE = ROOT / "package-windows.ps1"


def test_vue_build_is_the_cache_safe_production_entry() -> None:
    source = INDEX.read_text(encoding="utf-8")
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', source)

    assert '<div id="app"></div>' in source
    assert assets
    assert all(frontend_cache_control(asset) == _VERSIONED_CACHE_CONTROL for asset in assets)


def test_all_exact_frontend_asset_versions_match_release() -> None:
    source = LEGACY_INDEX.read_text(encoding="utf-8")
    versions = re.findall(r"[?&]v=([0-9]+\.[0-9]+\.[0-9]+)(?:[^\"&]*)", source)

    assert versions
    assert set(versions) == {__version__}


def test_release_version_surfaces_match_source_version() -> None:
    assert f'"version": "{__version__}"' in API_CONTRACT.read_text(encoding="utf-8")
    assert f"当前源码版本：`{__version__}`" in PROJECT_GUIDE.read_text(encoding="utf-8")
    assert f"当前源码版本: {__version__}" in SOURCE_DELIVERY.read_text(encoding="utf-8")
    assert f"[string]$Version = '{__version__}'" in WINDOWS_PACKAGE.read_text(encoding="utf-8-sig")


def test_release_keeps_technical_screener_in_native_workspace() -> None:
    navigation = NAVIGATION.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")

    assert "tab('technical-screener', '技术面选股', 'technical_screener')" in navigation
    assert "sidebarChild('research', 'technical-screener', '技术面选股')" in navigation
    assert "./views/technical_screener.js" in app
