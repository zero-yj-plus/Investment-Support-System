from __future__ import annotations

import json
import hashlib
import hmac
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from hermes_trader.agent.hermes_client import HermesClient, HermesError
from hermes_trader.agent.run_store import AgentRunStore, stable_hash
from hermes_trader.api.routes import _deterministic_diagnosis_proposal
from hermes_trader.config import Config


def _profile_config(tmp_path: Path) -> Config:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "config.yaml").write_text(
        """
model:
  default: test-model
  provider: openai-codex
platform_toolsets:
  api_server: []
plugins:
  enabled: []
mcp_servers: {}
""",
        encoding="utf-8",
    )
    (profile / ".env").write_text(
        """HERMES_SAFE_MODE=1
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8875
HERMES_PROXY_PORT=8874
API_SERVER_KEY=1234567890abcdef1234567890abcdef
""",
        encoding="utf-8",
    )
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
client: {id: test, name: Test}
server: {data_dir: ./data}
hermes:
  base_url: http://127.0.0.1:8874/v1
  autodiscover_local_gateway: false
  model: test-model
agent_runtime:
  require_restricted_toolsets: true
  allowed_toolsets: []
  require_profile_attestation: true
  restricted_profile_dir: ./profile
""",
        encoding="utf-8",
    )
    return Config(cfg_path)


def test_restricted_profile_is_attested_and_supplies_local_api_key(tmp_path: Path) -> None:
    cfg = _profile_config(tmp_path)
    client = HermesClient(cfg)

    attestation = client._profile_attestation()

    assert attestation["ok"] is True
    assert attestation["status"] == "attested"
    assert cfg.hermes_api_key == "1234567890abcdef1234567890abcdef"


def test_profile_attestation_fails_if_mcp_is_added(tmp_path: Path) -> None:
    cfg = _profile_config(tmp_path)
    profile_cfg = cfg.agent_restricted_profile_dir / "config.yaml"
    profile_cfg.write_text(
        profile_cfg.read_text(encoding="utf-8")
        + "\nmcp_servers:\n  unsafe: {command: python}\n",
        encoding="utf-8",
    )
    assert HermesClient(cfg)._profile_attestation()["ok"] is False


def test_profile_attestation_accepts_explicit_customer_mcp_with_safe_mode_off(
    tmp_path: Path,
) -> None:
    cfg = _profile_config(tmp_path)
    cfg.agent_allowed_mcp_servers = ["hermes-trader"]
    profile_cfg = cfg.agent_restricted_profile_dir / "config.yaml"
    profile_cfg.write_text(
        profile_cfg.read_text(encoding="utf-8").replace(
            "mcp_servers: {}",
            "mcp_servers:\n  hermes-trader: {command: /opt/product/.venv/bin/python}",
        ),
        encoding="utf-8",
    )
    profile_env = cfg.agent_restricted_profile_dir / ".env"
    profile_env.write_text(
        profile_env.read_text(encoding="utf-8").replace(
            "HERMES_SAFE_MODE=1", "HERMES_SAFE_MODE=0"
        ),
        encoding="utf-8",
    )

    assert HermesClient(cfg)._profile_attestation()["ok"] is True


def test_run_store_recovers_orphaned_running_run_without_prompt_content(tmp_path: Path) -> None:
    path = tmp_path / "runs.sqlite3"
    store = AgentRunStore(path)
    run_id = store.start_run(
        purpose="test",
        model="m",
        gateway="http://127.0.0.1:1/v1",
        input_hash=stable_hash([{"role": "user", "content": "private prompt"}]),
        input_bytes=14,
    )
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE agent_runs SET owner_pid=2147483647 WHERE run_id=?", (run_id,))

    recovered = AgentRunStore(path).recent(1)[0]

    assert recovered["run_id"] == run_id
    assert recovered["status"] == "interrupted"
    assert recovered["safe_to_use"] is False
    assert "private prompt" not in path.read_bytes().decode("utf-8", "ignore")


def test_run_store_accepts_audited_full_product_mcp_tool_run(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    run_id = store.start_run(
        purpose="test:mcp",
        model="m",
        gateway="http://127.0.0.1:8874/v1",
        input_hash=stable_hash([{"role": "user", "content": "status"}]),
        input_bytes=6,
    )
    store.mark_capability(run_id, {"status": "customer_full_product_mcp"})
    store.record_tool_event(
        run_id,
        tool_name="mcp__hermes_trader__product_api_request",
        tool_call_id="call-1",
        status="completed",
    )
    store.finish(run_id, status="completed", finish_reason="stop")

    row = store.recent(1)[0]
    assert row["tool_events"] == 1
    assert row["safe_to_use"] is True


def test_completed_stop_without_approved_capability_is_not_safe(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    run_id = store.start_run(
        purpose="test", model="m", gateway="g", input_hash="a" * 64, input_bytes=1
    )
    store.finish(run_id, status="completed", finish_reason="stop", output_hash="b" * 64)
    assert store.recent(1)[0]["safe_to_use"] is False
    assert store.summary()["last_run"]["safe_to_use"] is False


def test_completed_stop_with_any_tool_event_is_not_safe(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    run_id = store.start_run(
        purpose="test", model="m", gateway="g", input_hash="a" * 64, input_bytes=1,
    )
    store.mark_capability(run_id, {"status": "restricted", "ready": True})
    store.record_tool_event(
        run_id, tool_name="unknown", tool_call_id="", status="completed", attempt=1
    )
    store.finish(run_id, status="completed", finish_reason="stop", output_hash="b" * 64)
    assert store.recent(1)[0]["safe_to_use"] is False


class _FakeResponse:
    status_code = 200

    def __init__(self, lines: list[str]):
        self.lines = lines

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class _FakeStream:
    def __init__(self, response: _FakeResponse):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        return False


class _FakeClient:
    lines: list[str] = []
    post_count = 0
    payloads: list[dict] = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, *_args, **_kwargs):
        type(self).post_count += 1
        type(self).payloads.append(dict(_kwargs.get("json") or {}))
        return _FakeStream(_FakeResponse(list(type(self).lines)))


class _JsonResponse:
    status_code = 200

    def __init__(self, value):
        self.value = value

    def json(self):
        return self.value


class _ProbeClient:
    responses: dict[str, dict] = {}

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url: str, **_kwargs):
        suffix = url.rsplit("/", 1)[-1]
        return _JsonResponse(type(self).responses[suffix])


def _signed_process_attestation(client: HermesClient) -> dict:
    profile_hash = client._profile_attestation()["profile_hash"]
    payload = {
        "schema": "restricted-hermes-attestation.v1",
        "profile_hash": profile_hash,
        "home_hash": hashlib.sha256(str(client.config.agent_restricted_profile_dir).encode()).hexdigest(),
        "supervisor_pid": 100,
        "child_pid": 101,
        "child_alive": True,
        "safe_mode": True,
        "backend_port": 8875,
        "proxy_port": 8874,
        "started_at": "2026-07-12T00:00:00+00:00",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["signature"] = hmac.new(
        client.config.hermes_api_key.encode(), canonical, hashlib.sha256
    ).hexdigest()
    return payload


@pytest.mark.asyncio
async def test_process_bound_attestation_and_empty_runtime_toolsets_pass(
    monkeypatch, tmp_path: Path,
) -> None:
    client = HermesClient(_profile_config(tmp_path))
    _ProbeClient.responses = {
        "capabilities": {"runtime": {"mode": "server_agent", "tool_execution": "server"}},
        "toolsets": {"data": []},
        "attestation": _signed_process_attestation(client),
    }
    monkeypatch.setattr("hermes_trader.agent.hermes_client.httpx.AsyncClient", _ProbeClient)

    result = await client.capability_preflight(force=True)

    assert result["ready"] is True
    assert result["process_attestation"] == "bound"


@pytest.mark.asyncio
async def test_forged_process_signature_or_broad_toolset_fails_closed(
    monkeypatch, tmp_path: Path,
) -> None:
    client = HermesClient(_profile_config(tmp_path))
    forged = _signed_process_attestation(client)
    forged["signature"] = "0" * 64
    _ProbeClient.responses = {
        "capabilities": {"runtime": {"mode": "server_agent", "tool_execution": "server"}},
        "toolsets": {"data": [{"name": "terminal", "enabled": True}]},
        "attestation": forged,
    }
    monkeypatch.setattr("hermes_trader.agent.hermes_client.httpx.AsyncClient", _ProbeClient)

    result = await client.capability_preflight(force=True)

    assert result["ready"] is False
    assert result["status"] == "capability_unavailable"


def _direct_client(tmp_path: Path) -> HermesClient:
    cfg = SimpleNamespace(
        data_dir=tmp_path,
        hermes_base_url="https://api.deepseek.com/v1",
        hermes_api_key="secret",
        hermes_model="test-model",
        hermes_timeout=10,
        hermes_temperature=0.2,
        hermes_max_tokens=100,
        agent_require_restricted_toolsets=True,
        agent_allowed_toolsets=[],
        agent_direct_model_hosts=["api.deepseek.com"],
        agent_capability_cache_sec=20,
    )
    return HermesClient(cfg)


def test_interactive_model_timeouts_are_bounded_but_report_budget_is_preserved(
    tmp_path: Path,
) -> None:
    client = _direct_client(tmp_path)
    client.config.hermes_timeout = 300

    assert client._timeout_budget("chat:free", None) == 120
    assert client._timeout_budget("chat:company", None) == 300
    assert client._timeout_budget("chat:chain-governed-explanation", None) == 300
    assert client._timeout_budget("cockpit:narrative", None) == 60
    assert client._timeout_budget("diagnosis:stock", None) == 90
    assert client._timeout_budget("report:evening", None) == 300
    assert client._timeout_budget("chat:free", 25) == 25
    assert client._retry_after_timeout("chat:company") is False
    assert client._retry_after_timeout("chat:chain-governed-explanation") is False
    assert client._retry_after_timeout("chat:free") is True
    assert client._max_tokens_budget("chat:free", None) == 100
    assert client._max_tokens_budget("chat:company", None) == 20_000
    assert client._max_tokens_budget("chat:chain-governed-explanation", None) == 16_000
    assert client._max_tokens_budget("report:morning", None) == 100
    assert client._max_tokens_budget("chat:chain-governed-explanation", 12_000) == 12_000


@pytest.mark.asyncio
async def test_deepseek_daily_reports_use_low_thinking_without_affecting_chat(
    monkeypatch, tmp_path: Path,
) -> None:
    client = _direct_client(tmp_path)
    client.config.hermes_model = "deepseek-v4-flash"
    _FakeClient.post_count = 0
    _FakeClient.payloads = []
    _FakeClient.lines = [
        'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr("hermes_trader.agent.hermes_client.httpx.AsyncClient", _FakeClient)

    assert await client.chat(
        [{"role": "user", "content": "report"}], purpose="report:morning"
    ) == "ok"
    assert await client.chat(
        [{"role": "user", "content": "chat"}], purpose="chat:free"
    ) == "ok"

    assert _FakeClient.payloads[0]["thinking"] == {"type": "enabled"}
    assert _FakeClient.payloads[0]["reasoning_effort"] == "low"
    assert _FakeClient.payloads[0]["max_tokens"] == 24_000
    assert "thinking" not in _FakeClient.payloads[1]
    assert "reasoning_effort" not in _FakeClient.payloads[1]


@pytest.mark.asyncio
async def test_complete_stop_stream_is_safe_and_audited(monkeypatch, tmp_path: Path) -> None:
    client = _direct_client(tmp_path)
    _FakeClient.post_count = 0
    _FakeClient.lines = [
        'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr("hermes_trader.agent.hermes_client.httpx.AsyncClient", _FakeClient)

    output = [part async for part in client.chat_stream(
        [{"role": "user", "content": "secret request"}], purpose="test:stop"
    )]
    row = client.run_store.recent(1)[0]

    assert output == ["ok"]
    assert row["status"] == "completed"
    assert row["finish_reason"] == "stop"
    assert row["safe_to_use"] is True
    assert _FakeClient.post_count == 1


@pytest.mark.asyncio
async def test_empty_stop_response_is_retried_before_any_content_is_emitted(
    monkeypatch, tmp_path: Path,
) -> None:
    class SequenceClient(_FakeClient):
        responses = [
            [
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ],
            [
                'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ],
        ]

        def stream(self, *_args, **_kwargs):
            type(self).post_count += 1
            lines = type(self).responses.pop(0)
            return _FakeStream(_FakeResponse(lines))

    client = _direct_client(tmp_path)
    SequenceClient.post_count = 0
    monkeypatch.setattr(
        "hermes_trader.agent.hermes_client.httpx.AsyncClient", SequenceClient
    )

    output = [
        part async for part in client.chat_stream(
            [{"role": "user", "content": "x"}], purpose="test:empty-retry"
        )
    ]
    row = client.run_store.recent(1)[0]

    assert output == ["ok"]
    assert SequenceClient.post_count == 2
    assert row["attempts"] == 2
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_repeated_empty_stop_response_fails_with_explicit_audit_code(
    monkeypatch, tmp_path: Path,
) -> None:
    client = _direct_client(tmp_path)
    _FakeClient.post_count = 0
    _FakeClient.lines = [
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(
        "hermes_trader.agent.hermes_client.httpx.AsyncClient", _FakeClient
    )

    with pytest.raises(HermesError, match="空响应"):
        await client.chat(
            [{"role": "user", "content": "x"}],
            purpose="test:empty-terminal",
        )

    row = client.run_store.recent(1)[0]
    assert _FakeClient.post_count == 3
    assert row["status"] == "failed"
    assert row["error_code"] == "empty_response"


@pytest.mark.asyncio
async def test_chat_json_regenerates_invalid_structure_and_validates_required_keys(
    monkeypatch, tmp_path: Path,
) -> None:
    client = _direct_client(tmp_path)
    responses = iter([
        "这不是 JSON",
        '```json\n{"定调":"防守","主线":"无","回避":"高位"}\n```',
    ])
    calls: list[list[dict]] = []

    async def fake_chat(messages, **_kwargs):
        calls.append(messages)
        return next(responses)

    monkeypatch.setattr(client, "chat", fake_chat)

    result = await client.chat_json(
        [{"role": "user", "content": "生成驾驶舱叙述"}],
        required_keys=("定调", "主线"),
        purpose="test:json",
    )

    assert result["定调"] == "防守"
    assert result["主线"] == "无"
    assert len(calls) == 2
    assert "上一版未通过结构校验" in calls[1][-1]["content"]


@pytest.mark.asyncio
async def test_chat_json_rejects_schema_incomplete_output_without_leaking_raw(
    monkeypatch, tmp_path: Path,
) -> None:
    client = _direct_client(tmp_path)

    async def incomplete(_messages, **_kwargs):
        return '{"定调":"进攻"}'

    monkeypatch.setattr(client, "chat", incomplete)

    with pytest.raises(HermesError, match="结构化输出"):
        await client.chat_json(
            [{"role": "user", "content": "x"}],
            required_keys=("定调", "主线"),
            purpose="test:json-invalid",
        )


@pytest.mark.asyncio
async def test_non_stop_terminal_is_partial_and_never_retried(monkeypatch, tmp_path: Path) -> None:
    client = _direct_client(tmp_path)
    _FakeClient.post_count = 0
    _FakeClient.lines = [
        'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr("hermes_trader.agent.hermes_client.httpx.AsyncClient", _FakeClient)

    with pytest.raises(HermesError):
        _ = [part async for part in client.chat_stream(
            [{"role": "user", "content": "x"}], purpose="test:length"
        )]
    row = client.run_store.recent(1)[0]

    assert row["status"] == "partial"
    assert row["finish_reason"] == "length"
    assert row["safe_to_use"] is False
    assert _FakeClient.post_count == 1


@pytest.mark.asyncio
async def test_runtime_tool_event_is_unknown_and_never_retried(monkeypatch, tmp_path: Path) -> None:
    client = _direct_client(tmp_path)
    _FakeClient.post_count = 0
    _FakeClient.lines = [
        "event: hermes.tool.progress",
        'data: {"tool":"terminal","toolCallId":"call-1","status":"running","args":{"secret":"x"}}',
    ]
    monkeypatch.setattr("hermes_trader.agent.hermes_client.httpx.AsyncClient", _FakeClient)

    with pytest.raises(HermesError):
        _ = [part async for part in client.chat_stream(
            [{"role": "user", "content": "x"}], purpose="test:tool"
        )]
    row = client.run_store.recent(1)[0]

    assert row["status"] == "unknown"
    assert row["error_code"] == "capability_violation_manual_review"
    assert row["tool_events"] == 1
    assert _FakeClient.post_count == 1
    assert "secret" not in (tmp_path / "agent_runs.sqlite3").read_bytes().decode("utf-8", "ignore")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    '{"tool":"terminal","toolCallId":"call-2","status":"completed"}',
    "{malformed-json",
])
async def test_every_runtime_tool_event_fails_closed(
    monkeypatch, tmp_path: Path, payload: str,
) -> None:
    client = _direct_client(tmp_path)
    _FakeClient.post_count = 0
    _FakeClient.lines = [
        "event: hermes.tool.progress",
        f"data: {payload}",
        'data: {"choices":[{"delta":{"content":"must-not-pass"},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr("hermes_trader.agent.hermes_client.httpx.AsyncClient", _FakeClient)

    with pytest.raises(HermesError):
        _ = [part async for part in client.chat_stream(
            [{"role": "user", "content": "x"}], purpose="test:tool-drift"
        )]
    row = client.run_store.recent(1)[0]
    assert row["status"] == "unknown"
    assert row["safe_to_use"] is False
    assert row["tool_events"] == 1
    assert _FakeClient.post_count == 1


@pytest.mark.asyncio
async def test_tool_event_audit_failure_never_replays_agent_request(
    monkeypatch, tmp_path: Path,
) -> None:
    client = _direct_client(tmp_path)
    _FakeClient.post_count = 0
    _FakeClient.lines = [
        "event: hermes.tool.progress",
        'data: {"tool":"terminal","toolCallId":"call-3","status":"running"}',
    ]
    monkeypatch.setattr("hermes_trader.agent.hermes_client.httpx.AsyncClient", _FakeClient)

    def disk_failure(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(client.run_store, "record_tool_event", disk_failure)
    with pytest.raises(HermesError):
        _ = [part async for part in client.chat_stream(
            [{"role": "user", "content": "x"}], purpose="test:audit-failure"
        )]
    assert _FakeClient.post_count == 1
    assert client.run_store.recent(1)[0]["safe_to_use"] is False


@pytest.mark.asyncio
async def test_required_false_cannot_bypass_capability_gate(monkeypatch, tmp_path: Path) -> None:
    client = _direct_client(tmp_path)
    client.config.agent_require_restricted_toolsets = False
    _FakeClient.post_count = 0
    monkeypatch.setattr("hermes_trader.agent.hermes_client.httpx.AsyncClient", _FakeClient)

    async def blocked(*_args, **_kwargs):
        return {"ready": False, "required": False, "status": "capability_unavailable"}

    monkeypatch.setattr(client, "capability_preflight", blocked)
    with pytest.raises(HermesError):
        _ = [part async for part in client.chat_stream(
            [{"role": "user", "content": "must not send"}], purpose="test:no-bypass"
        )]
    row = client.run_store.recent(1)[0]
    assert row["status"] == "blocked"
    assert row["safe_to_use"] is False
    assert _FakeClient.post_count == 0


@pytest.mark.asyncio
async def test_http_direct_model_host_is_rejected_without_network(tmp_path: Path) -> None:
    client = _direct_client(tmp_path)
    client.config.hermes_base_url = "http://api.deepseek.com/v1"
    result = await client.capability_preflight(force=True)
    assert result["ready"] is False
    assert result["status"] == "remote_endpoint_unapproved"


@pytest.mark.asyncio
async def test_saved_custom_public_https_model_is_direct_without_agent_tools(tmp_path: Path) -> None:
    client = _direct_client(tmp_path)
    client.config.hermes_base_url = "https://models.example.com:8443/v1"
    client.config.model_connection_custom = True

    result = await client.capability_preflight(force=True)

    assert result["ready"] is True
    assert result["status"] == "direct_model_api"
    assert result["tool_execution"] == "none"


@pytest.mark.asyncio
async def test_saved_custom_private_literal_is_rejected_without_network(tmp_path: Path) -> None:
    client = _direct_client(tmp_path)
    client.config.hermes_base_url = "https://10.0.0.8/v1"
    client.config.model_connection_custom = True

    result = await client.capability_preflight(force=True)

    assert result["ready"] is False
    assert result["status"] == "remote_endpoint_unapproved"


def test_diagnosis_final_action_ignores_model_action_and_conviction() -> None:
    evidence = [
        {"field": "last_price", "value": 10.2},
        {"field": "prev_close", "value": 10.0},
        {"field": "change_pct", "value": 2.0},
        {"field": "amount", "value": 100_000_000},
        {"field": "volume_ratio", "value": 1.2},
        {"field": "turnover_rate", "value": 3.0},
    ]
    base = {
        "decision_market_evidence": evidence,
        "decision_market_evidence_quality": {"status": "ok", "captured": 6, "requested": 6},
    }
    high = _deterministic_diagnosis_proposal(
        {**base, "action": "买入", "conviction": "高"}, has_holding=False
    )
    low = _deterministic_diagnosis_proposal(
        {**base, "action": "回避", "conviction": "低"}, has_holding=False
    )

    assert high == low
    assert high["action"] == "买入"
    assert high["confidence"] <= 0.75
