from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_trader.config import Config
from hermes_trader.main import (
    _HTML_CACHE_CONTROL,
    _VERSIONED_CACHE_CONTROL,
    create_app,
    frontend_cache_control,
    mount_frontends,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_config(tmp_path: Path, ui: str = "") -> Config:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
client: {{id: ui-test, name: UI Test}}
server: {{data_dir: ./data}}
{ui}
""",
        encoding="utf-8",
    )
    return Config(config_path)


def _static_app(*, legacy_enabled: bool) -> FastAPI:
    app = FastAPI()

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    mount_frontends(
        app,
        SimpleNamespace(ui_frontend="vue", legacy_enabled=legacy_enabled),
        project_root=ROOT,
    )
    return app


def test_ui_config_defaults_to_vue_only(tmp_path: Path) -> None:
    config = _write_config(tmp_path)

    assert config.ui_frontend == "vue"
    assert config.legacy_enabled is False
    assert config.section("ui") == {"frontend": "vue", "legacy_enabled": False}


def test_ui_config_accepts_explicit_legacy_switch_and_rejects_ambiguous_values(
    tmp_path: Path,
) -> None:
    enabled = tmp_path / "enabled"
    enabled.mkdir()
    config = _write_config(
        enabled,
        "ui:\n  frontend: vue\n  legacy_enabled: true",
    )
    assert config.legacy_enabled is True

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    with pytest.raises(ValueError, match="legacy_enabled"):
        _write_config(
            invalid,
            "ui:\n  frontend: vue\n  legacy_enabled: 'false'",
        )

    unsupported = tmp_path / "unsupported"
    unsupported.mkdir()
    with pytest.raises(ValueError, match="正式前端"):
        _write_config(
            unsupported,
            "ui:\n  frontend: legacy\n  legacy_enabled: true",
        )


def test_vue_is_the_only_default_page_and_api_routes_are_not_swallowed() -> None:
    client = TestClient(_static_app(legacy_enabled=False))

    root = client.get("/")
    assert root.status_code == 200
    assert '<div id="app"></div>' in root.text
    assert "决策总览 · Hermes Trader" in root.text
    assert "W.U.D.I." not in root.text
    assert client.get("/api/ping").json() == {"ok": True}
    assert client.get("/legacy/", follow_redirects=False).status_code == 404
    assert client.get("/js/app.js", follow_redirects=False).status_code == 404


def test_legacy_page_and_compatibility_modules_exist_only_when_enabled() -> None:
    client = TestClient(_static_app(legacy_enabled=True))

    legacy = client.get("/legacy/")
    assert legacy.status_code == 200
    assert "投研决策" in legacy.text
    assert client.get("/js/api.js").status_code == 200
    assert client.get("/api/ping").json() == {"ok": True}


def test_missing_vue_build_fails_cold_start_with_actionable_message(tmp_path: Path) -> None:
    (tmp_path / "frontend").mkdir()
    app = FastAPI()

    with pytest.raises(RuntimeError, match="npm run build"):
        mount_frontends(
            app,
            SimpleNamespace(ui_frontend="vue", legacy_enabled=False),
            project_root=tmp_path,
        )


def test_vue_and_legacy_cache_policies_do_not_mix() -> None:
    assert frontend_cache_control("/") == _HTML_CACHE_CONTROL
    assert frontend_cache_control("/legacy/") == _HTML_CACHE_CONTROL
    assert frontend_cache_control("/assets/index-uRjSxOOj.js") == _VERSIONED_CACHE_CONTROL


def test_full_fastapi_cold_start_serves_vue_shell_and_api_without_legacy(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
client: {id: cold-start, name: Cold Start}
server: {host: 127.0.0.1, port: 18869, data_dir: ./data}
ui: {frontend: vue, legacy_enabled: false}
hermes: {base_url: 'http://127.0.0.1:8874/v1', autodiscover_local_gateway: false}
push: {enabled: false}
capital_tracking: {enabled: false}
news_event_observation: {enabled: false}
official_longhu: {enabled: false}
data_source: {policy: free_only, deployment_profile: cn_home, eastmoney_direct: false}
""",
        encoding="utf-8",
    )

    app = create_app(str(config_path))
    client = TestClient(app)

    assert client.get("/").status_code == 200
    shell = client.get("/api/shell-status")
    assert shell.status_code == 200
    assert shell.json()["ui"] == {"frontend": "vue", "legacy_enabled": False}
    assert client.get("/legacy/", follow_redirects=False).status_code == 404


def test_legacy_mode_keeps_customer_extension_registry_and_module_dependencies(
    tmp_path: Path,
) -> None:
    ext_dir = tmp_path / "ext"
    web_dir = ext_dir / "web"
    web_dir.mkdir(parents=True)
    (ext_dir / "sample.py").write_text(
        """
EXT_VIEWS = [{
    "path": "sample_ext",
    "label": "测试扩展",
    "module": "/ext/sample.js",
    "group": "research",
}]
""",
        encoding="utf-8",
    )
    (web_dir / "sample.js").write_text(
        "import { api } from '/js/api.js';\nexport function mount() { return api; }\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
client: {id: extension-start, name: Extension Start}
server: {data_dir: ./data}
ui: {frontend: vue, legacy_enabled: true}
hermes: {base_url: 'http://127.0.0.1:8874/v1', autodiscover_local_gateway: false}
push: {enabled: false}
data_source: {policy: free_only, deployment_profile: cn_home, eastmoney_direct: false}
""",
        encoding="utf-8",
    )

    client = TestClient(create_app(str(config_path)))
    registry = client.get("/api/ext/views")

    assert registry.status_code == 200
    assert registry.json()[0]["path"] == "sample_ext"
    assert registry.json()[0]["registered"] is True
    assert client.get("/ext/sample.js").status_code == 200
    assert client.get("/js/api.js").status_code == 200
    assert client.get("/legacy/").status_code == 200
