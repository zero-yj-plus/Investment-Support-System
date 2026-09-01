from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from hermes_trader.feishu.bot import FeishuBot


def _bot() -> FeishuBot:
    return FeishuBot(
        SimpleNamespace(feishu_app_id="", feishu_app_secret=""),
        SimpleNamespace(),
        asyncio.new_event_loop(),
    )


def test_feishu_reply_uses_installed_lark_sdk_request_types() -> None:
    bot = _bot()
    captured: dict[str, object] = {}

    class FakeResponse:
        code = 0
        msg = "ok"

        @staticmethod
        def success() -> bool:
            return True

    class FakeMessageApi:
        @staticmethod
        def reply(request):
            captured["request"] = request
            return FakeResponse()

    bot._api_client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(message=FakeMessageApi()))
    )

    bot._reply("om_test_message", "回复测试")

    request = captured["request"]
    assert request.message_id == "om_test_message"
    assert request.request_body.msg_type == "text"
    assert request.request_body.content == '{"text": "回复测试"}'
    bot.loop.close()


@pytest.mark.asyncio
async def test_feishu_stock_code_uses_governed_stock_path(monkeypatch) -> None:
    bot = _bot()

    async def governed(code: str) -> str:
        return f"governed:{code}"

    async def raw(*_args, **_kwargs) -> str:
        pytest.fail("飞书问股不得绕到自由模型流")

    monkeypatch.setattr(bot, "_collect_governed_stock", governed)
    monkeypatch.setattr(bot, "_collect", raw)

    assert await bot._dispatch("问 000001 怎么看") == "governed:000001"
    bot.loop.close()


@pytest.mark.asyncio
async def test_feishu_holdings_command_uses_governed_position_path(monkeypatch) -> None:
    bot = _bot()

    async def governed() -> str:
        return "governed:positions"

    async def raw(*_args, **_kwargs) -> str:
        pytest.fail("飞书持仓不得绕到自由模型流")

    monkeypatch.setattr(bot, "_collect_governed_positions", governed)
    monkeypatch.setattr(bot, "_collect", raw)

    assert await bot._dispatch("持仓诊断") == "governed:positions"
    bot.loop.close()
