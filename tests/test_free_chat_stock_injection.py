from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hermes_trader.analysis import engine as engine_module
from hermes_trader.analysis import chat_materials
from hermes_trader.analysis.engine import Engine
from hermes_trader.api import routes


class _Request:
    def __init__(self, state, body: dict):
        self.app = SimpleNamespace(state=state)
        self._body = body

    async def json(self):
        return self._body


class _Config:
    def section(self, name: str) -> dict:
        if name == "chat_retry":
            return {"attempts": 1, "backoff_sec": 0}
        return {}


class _Store:
    def knowledge_digest(self) -> str:
        return ""


class _Hermes:
    async def chat_stream(self, _messages, **_kwargs):
        yield "已基于个股证据回答"


def _patch_universe(monkeypatch) -> None:
    monkeypatch.setattr(
        engine_module.free_source,
        "cached_stock_universe",
        lambda: {
            "000001": "平安银行",
            "000002": "万科A",
            "600519": "贵州茅台",
        },
    )
    monkeypatch.setattr(
        engine_module.free_source,
        "stock_universe_status",
        lambda: {
            "complete": True,
            "missing_exchanges": [],
            "as_of": "2026-07-18",
        },
    )


@pytest.mark.parametrize(
    ("message", "state", "code"),
    [
        ("分析一下 600519 今天的量价", "matched", "600519"),
        ("贵州茅台现在处于什么阶段？", "matched", "600519"),
        ("万科怎么看？", "matched", "000002"),
        ("今天市场风险大不大？", "none", None),
    ],
)
def test_free_stock_reference_resolves_code_full_name_and_ordinary_question(
    monkeypatch, message: str, state: str, code: str | None,
) -> None:
    _patch_universe(monkeypatch)

    result = engine_module.resolve_free_stock_reference(message)

    assert result["state"] == state
    if code:
        assert result["code"] == code


def test_free_stock_reference_never_silently_chooses_between_two_stocks(
    monkeypatch,
) -> None:
    _patch_universe(monkeypatch)

    result = engine_module.resolve_free_stock_reference(
        "比较一下平安银行和贵州茅台"
    )

    assert result["state"] == "ambiguous"
    assert {row["code"] for row in result["candidates"]} == {"000001", "600519"}


def test_free_stock_reference_rejects_unknown_code_when_universe_is_complete(
    monkeypatch,
) -> None:
    _patch_universe(monkeypatch)

    result = engine_module.resolve_free_stock_reference("分析 600999")

    assert result["state"] == "unrecognized"
    assert result["unrecognized_codes"] == ["600999"]


@pytest.mark.asyncio
async def test_engine_boundary_changes_free_stock_question_to_stock_context(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        engine_module,
        "resolve_free_stock_reference",
        lambda _message: {
            "state": "matched",
            "code": "600519",
            "name": "贵州茅台",
            "candidates": [],
        },
    )
    engine = Engine(_Config(), _Store(), _Hermes())
    engine.chat_context = AsyncMock(return_value={"个股数据": {"quote": {}}})

    events = [
        event
        async for event in engine.chat_stream("free", "贵州茅台现在怎么看？")
    ]

    engine.chat_context.assert_awaited_once_with(
        "stock", "600519", "", codes=None
    )
    assert any(event.get("delta") == "已基于个股证据回答" for event in events)
    assert events[-1] == {"done": True, "truncated": False}


@pytest.mark.asyncio
async def test_api_free_stock_question_uses_governed_stock_path_before_model_probe(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        routes.engine_mod,
        "resolve_free_stock_reference",
        lambda _message: {
            "state": "matched",
            "code": "600519",
            "name": "贵州茅台",
            "candidates": [],
        },
    )
    received: dict = {}

    async def governed(_state, code, message="", history=None):
        received.update(code=code, message=message, history=history)
        yield "治理个股答复"

    monkeypatch.setattr(routes, "_governed_stock_chat_stream", governed)
    monkeypatch.setattr(
        routes.diagnosis_mod,
        "_model_connection_state",
        AsyncMock(side_effect=AssertionError("识股路由不应等待模型探测")),
    )
    state = SimpleNamespace(engine=object(), store=object())

    response = await routes.chat(_Request(
        state,
        {
            "mode": "free",
            "message": "贵州茅台现在怎么看？",
            "history": [{"role": "user", "content": "上一问"}],
        },
    ))
    chunks = [chunk async for chunk in response.body_iterator]
    events = [
        json.loads(line.removeprefix("data: "))
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("data: ")
    ]

    assert received == {
        "code": "600519",
        "message": "贵州茅台现在怎么看？",
        "history": [{"role": "user", "content": "上一问"}],
    }
    assert any(event.get("delta") == "治理个股答复" for event in events)
    assert events[-1]["done"] is True
    assert events[-1]["truncated"] is False
    assert events[-1]["resolved_mode"] == "stock"
    assert events[-1]["code"] == "600519"
    assert events[-1]["data_materials"]["max_selected"] == 4


@pytest.mark.asyncio
async def test_api_ambiguous_free_stock_question_requests_clarification(
    monkeypatch,
) -> None:
    reference = {
        "state": "ambiguous",
        "candidates": [
            {"code": "000001", "name": "平安银行"},
            {"code": "600519", "name": "贵州茅台"},
        ],
        "unrecognized_codes": [],
    }
    monkeypatch.setattr(
        routes.engine_mod,
        "resolve_free_stock_reference",
        lambda _message: reference,
    )
    state = SimpleNamespace(engine=object(), store=object())

    response = await routes.chat(_Request(
        state,
        {"mode": "free", "message": "比较平安银行和贵州茅台"},
    ))
    body = "".join([chunk async for chunk in response.body_iterator])

    assert "平安银行（000001）" in body
    assert "贵州茅台（600519）" in body
    assert "本次不猜测标的" in body


@pytest.mark.asyncio
async def test_free_stock_governed_path_passes_saved_holdings_to_model_card(
    monkeypatch,
) -> None:
    async def fake_card(_state, _code, **_kwargs):
        return {
            "code": "600519", "name": "贵州茅台", "recognized": True,
            "action": "观望", "signal_action": "观望",
            "market_quote": {
                "code": "600519", "name": "贵州茅台", "price": 1297.41,
                "source": "腾讯财经", "as_of": "2026-08-11T11:30:00+08:00",
            },
            "trade_plan": {"entry_trigger": "等待确认", "stop": "结构失效"},
            "risk_decision": {"decision": "watch"},
        }

    received: dict = {}

    async def holdings_context():
        return [{
            "code": "600519", "name": "贵州茅台", "cost": 100.0, "shares": 100.0,
            "实时行情": {
                "code": "600519", "price": 1297.41, "source": "腾讯财经",
                "as_of": "2026-08-11T11:30:00+08:00", "stale": False,
            },
        }]

    async def explain(_message, card, **_kwargs):
        received.update(card)
        yield ""

    monkeypatch.setattr(routes, "_governed_and_record_diagnosis", fake_card)
    monkeypatch.setattr(routes, "build_market_context", lambda: {"market_session": "intraday"})
    state = SimpleNamespace(
        engine=SimpleNamespace(
            _enriched_holdings_context=holdings_context,
            governed_stock_explanation_stream=explain,
        ),
        store=SimpleNamespace(),
    )
    report = chat_materials.empty_report(())

    _ = "".join([
        chunk async for chunk in routes._governed_stock_chat_stream(
            state,
            "600519",
            "我的贵州茅台怎么看？",
            data_material_ids=(),
            material_report=report,
        )
    ])

    assert received["用户持仓"][0]["cost"] == 100.0
    holdings_result = next(row for row in report["required"] if row["id"] == "holdings")
    assert holdings_result["state"] == "complete"
    assert "1 条持仓" in holdings_result["reason"]
