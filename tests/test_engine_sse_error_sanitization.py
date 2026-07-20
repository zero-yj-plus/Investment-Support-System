"""SSE 安全边界：上游异常详情不得进入客户端事件。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from hermes_trader.analysis import engine as engine_module
from hermes_trader.analysis.engine import Engine


_SECRET_EXCEPTION = RuntimeError("https://internal.example/v1?api_key=do-not-leak")


class _Config:
    client_id = "sse-safety-test"

    def section(self, name: str) -> dict:
        if name == "chat_retry":
            return {"attempts": 1, "backoff_sec": 0}
        if name == "report_eval":
            return {"enabled": False}
        return {}


class _Store:
    def knowledge_digest(self) -> str:
        return ""


class _FailingStore:
    def knowledge_digest(self) -> str:
        raise _SECRET_EXCEPTION


class _BrokenHermes:
    async def chat_stream(self, *_args, **_kwargs):
        if False:  # pragma: no cover - 保持 async generator 形态
            yield ""
        raise _SECRET_EXCEPTION


class _UnusedHermes:
    async def chat_stream(self, *_args, **_kwargs):  # pragma: no cover - 上下文先失败
        raise AssertionError("上下文失败时不应调用 AI")
        yield ""


async def _collect(stream) -> list[dict]:
    return [event async for event in stream]


def _assert_safe_error(events: list[dict], expected_code: str) -> None:
    errors = [event for event in events if "error" in event]
    assert len(errors) == 1
    assert errors[0]["error_code"] == expected_code
    payload = json.dumps(events, ensure_ascii=False)
    for secret in ("https://", "internal.example", "/v1", "api_key", "do-not-leak"):
        assert secret not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flow", "expected_code"),
    [
        ("report", "report_generation_failed"),
        ("chat", "chat_generation_failed"),
        ("diy", "diy_generation_failed"),
    ],
)
async def test_generation_exceptions_are_sanitized_in_all_sse_flows(
    monkeypatch, flow: str, expected_code: str,
) -> None:
    """RuntimeError 的 URL/key 只能进日志，三种 SSE 均返回稳定契约。"""
    engine = Engine(_Config(), _Store(), _BrokenHermes())
    engine._context_for_report = AsyncMock(return_value={"今日日期": "2026-07-12"})
    engine.chat_context = AsyncMock(return_value={"今日日期": "2026-07-12"})
    monkeypatch.setattr(engine_module, "is_trade_day", lambda: False)

    if flow == "report":
        stream = engine.generate_report_stream("morning")
    elif flow == "chat":
        stream = engine.chat_stream("free", "今天市场怎么样？")
    else:
        stream = engine.diy_run_stream("请分析市场")

    _assert_safe_error(await _collect(stream), expected_code)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flow", "expected_code"),
    [
        ("report", "report_context_failed"),
        ("chat", "chat_context_failed"),
        ("diy", "diy_context_failed"),
    ],
)
async def test_context_exceptions_are_sanitized_in_all_sse_flows(
    flow: str, expected_code: str,
) -> None:
    engine = Engine(_Config(), _Store(), _UnusedHermes())
    engine._context_for_report = AsyncMock(side_effect=_SECRET_EXCEPTION)
    engine.chat_context = AsyncMock(side_effect=_SECRET_EXCEPTION)

    if flow == "report":
        stream = engine.generate_report_stream("morning")
    elif flow == "chat":
        stream = engine.chat_stream("free", "今天市场怎么样？")
    else:
        stream = engine.diy_run_stream("请分析市场")

    _assert_safe_error(await _collect(stream), expected_code)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flow", "expected_code"),
    [
        ("report", "report_internal_failure"),
        ("chat", "chat_internal_failure"),
        ("diy", "diy_internal_failure"),
    ],
)
async def test_preparation_exceptions_are_sanitized_in_all_sse_flows(
    flow: str, expected_code: str,
) -> None:
    engine = Engine(_Config(), _FailingStore(), _UnusedHermes())
    engine._context_for_report = AsyncMock(return_value={"今日日期": "2026-07-12"})
    engine.chat_context = AsyncMock(return_value={"今日日期": "2026-07-12"})

    if flow == "report":
        stream = engine.generate_report_stream("morning")
    elif flow == "chat":
        stream = engine.chat_stream("free", "今天市场怎么样？")
    else:
        stream = engine.diy_run_stream("请分析市场")

    _assert_safe_error(await _collect(stream), expected_code)


@pytest.mark.asyncio
async def test_safe_user_input_error_keeps_its_specific_message() -> None:
    engine = Engine(_Config(), _Store(), _UnusedHermes())

    events = await _collect(engine.generate_report_stream("not-a-report"))

    assert events == [{"error": "未知报告类型: not-a-report"}]
