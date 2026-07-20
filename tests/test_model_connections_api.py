from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse

from hermes_trader.agent.hermes_client import HermesClient
from hermes_trader.api import routes
from hermes_trader.config import Config


def _config(tmp_path: Path) -> Config:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
client: {id: test, name: Test}
server: {data_dir: ./data}
hermes:
  base_url: http://127.0.0.1:8742/v1
  model: fallback-model
  autodiscover_local_gateway: false
agent_runtime:
  require_restricted_toolsets: true
  require_profile_attestation: true
""",
        encoding="utf-8",
    )
    return Config(path)


class JsonRequest:
    def __init__(self, config: Config, payload: dict | None = None):
        self.app = SimpleNamespace(state=SimpleNamespace(config=config))
        self._payload = payload or {}

    async def json(self):
        return self._payload


def _json_response(response: JSONResponse) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_config_manages_multiple_connections_without_returning_secrets(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    first = config.save_model_connection(
        "first",
        name="DeepSeek",
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="first-secret",
    )
    second = config.save_model_connection(
        "second",
        name="Local vLLM",
        provider="vllm",
        base_url="http://127.0.0.1:8000/v1",
        model="qwen",
        api_key="second-secret",
        activate=False,
    )

    assert first["active"] is True
    assert second["active"] is False
    assert config.active_model_connection_id == "first"
    assert config.model_provider == "deepseek"
    assert config.hermes_model == "deepseek-chat"
    assert config.hermes_api_key == "first-secret"

    config.activate_model_connection("second")
    config.save_model_connection(
        "second",
        name="Local vLLM",
        provider="vllm",
        base_url="http://127.0.0.1:8000/v1",
        model="qwen-72b",
        api_key=None,
        require_existing=True,
    )

    view = config.settings_view()
    serialized = json.dumps(view, ensure_ascii=False)
    assert config.hermes_model == "qwen-72b"
    assert config.hermes_api_key == "second-secret"
    assert view["active_model_connection_id"] == "second"
    assert len(view["model_connections"]) == 2
    assert all("api_key" not in row for row in view["model_connections"])
    assert "first-secret" not in serialized
    assert "second-secret" not in serialized
    assert view["hermes_integration"]["optional"] is True
    assert view["hermes_integration"]["active_as_model_gateway"] is False
    assert config.model_uses_hermes_gateway is False

    assert config.delete_model_connection("second") is True
    assert config.active_model_connection_id is None
    assert config.model_connection_custom is False
    config.activate_model_connection("first")
    assert config.hermes_model == "deepseek-chat"


@pytest.mark.asyncio
async def test_model_connection_crud_and_switch_api_never_return_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    async def network_safe(_url: str) -> bool:
        return True

    monkeypatch.setattr(routes, "_model_endpoint_network_safe", network_safe)
    created = await routes.create_model_connection(JsonRequest(config, {
        "name": "Provider A",
        "provider": "provider-a",
        "base_url": "https://models.example.com/v1",
        "model": "model-a",
        "api_key": "api-secret-never-returned",
    }))
    first_id = created["connection"]["id"]
    assert created["connection"]["active"] is True
    assert created["connection"]["api_key_set"] is True
    assert "api-secret-never-returned" not in json.dumps(created)

    created_local = await routes.create_model_connection(JsonRequest(config, {
        "name": "Local Ollama",
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5",
        "activate": False,
    }))
    local_id = created_local["connection"]["id"]
    assert created_local["active_connection_id"] == first_id

    listed = await routes.list_model_connections(JsonRequest(config))
    assert listed["active_connection_id"] == first_id
    assert len(listed["connections"]) == 2
    assert "api-secret-never-returned" not in json.dumps(listed)

    updated = await routes.update_model_connection(first_id, JsonRequest(config, {
        "model": "model-a-v2",
    }))
    assert updated["connection"]["model"] == "model-a-v2"
    assert config.model_connection_api_key(first_id) == "api-secret-never-returned"

    activated = await routes.activate_model_connection(local_id, JsonRequest(config))
    assert activated["active_connection_id"] == local_id
    assert config.model_provider == "ollama"
    assert config.hermes_base_url == "http://127.0.0.1:11434/v1"

    deleted = await routes.delete_model_connection_by_id(local_id, JsonRequest(config))
    assert deleted["deleted_connection_id"] == local_id
    assert deleted["active_connection_id"] is None

    rejected = await routes.update_model_connection(first_id, JsonRequest(config, {
        "base_url": "https://new-models.example.com/v1",
    }))
    assert isinstance(rejected, JSONResponse)
    assert rejected.status_code == 400
    assert "API Key" in _json_response(rejected)["error"]


@pytest.mark.asyncio
async def test_saved_key_can_be_used_for_probe_without_being_returned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.save_model_connection(
        "saved",
        name="Saved",
        provider="custom",
        base_url="https://models.example.com/v1",
        model="model-a",
        api_key="saved-secret",
    )

    async def network_safe(_url: str) -> bool:
        return True

    observed: dict[str, str] = {}

    async def probe(base_url: str, api_key: str, model: str) -> dict:
        observed.update(base_url=base_url, api_key=api_key, model=model)
        return {"connected": True, "model": model}

    monkeypatch.setattr(routes, "_model_endpoint_network_safe", network_safe)
    monkeypatch.setattr(HermesClient, "probe_model_endpoint", probe)
    result = await routes.test_model_connection(JsonRequest(config, {
        "connection_id": "saved",
        "base_url": "https://models.example.com/v1",
        "model": "model-a",
    }))

    assert result["ok"] is True
    assert observed["api_key"] == "saved-secret"
    assert "saved-secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_custom_local_provider_does_not_require_hermes_gateway_attestation(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config.save_model_connection(
        "local",
        name="Local vLLM",
        provider="vllm",
        base_url="http://127.0.0.1:8000/v1",
        model="qwen",
        api_key="",
    )

    capability = await HermesClient(config).capability_preflight()

    assert capability["ready"] is True
    assert capability["status"] == "direct_model_api"
    assert capability["runtime_mode"] == "direct_model_api"
    assert capability["tool_execution"] == "none"


@pytest.mark.asyncio
async def test_unconfigured_optional_model_status_does_not_probe_or_fail_core(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    class UnconfiguredModel:
        async def check_connection(self):
            raise AssertionError("unconfigured optional model must not be probed")

        async def capability_preflight(self):
            raise AssertionError("unconfigured optional model must not be probed")

    monkeypatch.setattr(routes, "build_market_context", lambda: {
        "date": "2026-07-13",
        "is_trade_day": True,
        "market_session": "intraday",
        "recommended_route": "today",
    })
    monkeypatch.setattr(routes, "task_center_status", lambda *_args: {"jobs": []})
    monkeypatch.setattr(routes, "route_lifecycle_contract", lambda: {"redirects": {}})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        config=config,
        hermes=UnconfiguredModel(),
        scheduler=object(),
        store=object(),
        agent_runs=None,
        evidence_store=None,
        market_gateway=None,
        feishu=SimpleNamespace(configured=False),
        core_integrity=None,
    )))

    result = await routes.status(request)

    assert result["ok"] is True
    assert result["model_connection"]["status"] == "not_configured"
    assert result["model_connection"]["required_for_core"] is False
    assert result["hermes"]["configured"] is False
    assert result["hermes"]["agent_capability"]["status"] == "model_connection_optional"
    assert result["hermes"]["agent_capability"]["blocks_core"] is False
