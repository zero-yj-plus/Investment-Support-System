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
