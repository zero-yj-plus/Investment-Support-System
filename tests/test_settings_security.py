from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from hermes_trader.api.routes import (
    _model_endpoint_resolved_address_safe,
    _model_endpoint_network_safe,
    _safe_feishu_webhook,
    _safe_hermes_base_url,
    put_settings,
)


def _settings_request(body: dict, state: object) -> Request:
    encoded = json.dumps(body).encode("utf-8")

    async def receive() -> dict:
        return {"type": "http.request", "body": encoded, "more_body": False}

    return Request({
        "type": "http",
        "method": "PUT",
        "path": "/api/settings",
        "headers": [(b"content-type", b"application/json")],
        "app": SimpleNamespace(state=state),
    }, receive)


def test_supported_hermes_endpoints_are_allowed() -> None:
    assert _safe_hermes_base_url("http://127.0.0.1:8742/v1")
    assert _safe_hermes_base_url("https://localhost:8742/v1/")
    assert _safe_hermes_base_url("https://api.deepseek.com/v1")
    assert _safe_hermes_base_url("https://api.x402.at:443/v1")
    assert _safe_hermes_base_url("https://models.example.com/v1")
    assert _safe_hermes_base_url("https://models.example.com:8443/v1")


def test_hermes_endpoint_rejects_ssrf_and_lookalikes() -> None:
    rejected = [
        "http://api.deepseek.com/v1",
        "https://api.deepseek.com@evil.test/v1",
        "https://api.deepseek.com/v1?next=http://127.0.0.1",
        "https://api.deepseek.com/v1/models",
        "https://10.0.0.8/v1",
        "https://169.254.169.254/v1",
        "https://model.internal/v1",
        "https://intranet/v1",
        "http://0.0.0.0:8742/v1",
    ]
    assert all(not _safe_hermes_base_url(url) for url in rejected)


def test_only_official_feishu_webhooks_are_allowed() -> None:
    assert _safe_feishu_webhook(
        "https://open.feishu.cn/open-apis/bot/v2/hook/example-token"
    )
    assert _safe_feishu_webhook(
        "https://open.larksuite.com/open-apis/bot/v2/hook/example-token"
    )
    rejected = [
        "http://open.feishu.cn/open-apis/bot/v2/hook/token",
        "https://open.feishu.cn.evil.test/open-apis/bot/v2/hook/token",
        "https://open.feishu.cn@evil.test/open-apis/bot/v2/hook/token",
        "https://open.feishu.cn/open-apis/bot/v2/hook/",
        "https://open.feishu.cn/open-apis/bot/v2/hook/token/extra",
    ]
    assert all(not _safe_feishu_webhook(url) for url in rejected)


@pytest.mark.asyncio
async def test_scheduled_push_settings_accept_only_five_boolean_switches() -> None:
    class _Config:
        saved: list[dict] = []

        def save_overrides(self, patch: dict) -> None:
            self.saved.append(patch)

    config = _Config()
    state = SimpleNamespace(config=config, scheduler=None)
    accepted = await put_settings(_settings_request({
        "scheduled_push": {"tail": False},
    }, state))

    assert accepted == {"ok": True}
    assert config.saved == [{"scheduled_push": {"tail": False}}]

    for invalid in ({"midday": False}, {"tail": "false"}, {}):
        response = await put_settings(_settings_request({
            "scheduled_push": invalid,
        }, state))
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_custom_model_dns_must_resolve_only_to_public_addresses(monkeypatch) -> None:
    monkeypatch.setattr(
        "hermes_trader.api.routes.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    assert await _model_endpoint_network_safe("https://models.example.com/v1") is True

    monkeypatch.setattr(
        "hermes_trader.api.routes.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("10.0.0.8", 443))],
    )
    assert await _model_endpoint_network_safe("https://models.example.com/v1") is False


@pytest.mark.asyncio
async def test_custom_model_dns_accepts_transparent_proxy_synthetic_public_address(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "hermes_trader.api.routes.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("198.18.0.84", 443))],
    )
    assert await _model_endpoint_network_safe("https://api.x402.at/v1") is True
    # 合成地址只能是合法公网域名的本机解析结果，用户不能直接把它当 endpoint。
    assert _safe_hermes_base_url("https://198.18.0.84/v1") is False


def test_model_endpoint_resolved_address_still_rejects_real_private_ranges() -> None:
    assert _model_endpoint_resolved_address_safe("198.18.0.84") is True
    for address in (
        "127.0.0.1",
        "10.0.0.8",
        "172.16.0.8",
        "192.168.1.8",
        "169.254.169.254",
        "::1",
        "fc00::8",
    ):
        assert _model_endpoint_resolved_address_safe(address) is False
