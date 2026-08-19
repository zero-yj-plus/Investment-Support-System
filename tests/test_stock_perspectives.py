from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse

from hermes_trader.analysis import engine as engine_mod
from hermes_trader.analysis import stock_perspectives
from hermes_trader.api import routes
from hermes_trader.knowledge.store import Store


class Request:
    def __init__(self, state=None, body: dict | None = None):
        self.app = SimpleNamespace(state=state or SimpleNamespace())
        self._body = body or {}
        self.query_params = {}

    async def json(self):
        return self._body


def test_perspective_registry_has_seventeen_unique_versioned_items() -> None:
    public = stock_perspectives.public_perspectives()

    assert len(public) == 17
    assert len({item["id"] for item in public}) == 17
    assert sum(item["category"] == "A股短线与游资" for item in public) == 14
    assert sum(item["category"] == "宏观与趋势交易" for item in public) == 3
    assert all(item["version"] == "2026-08-06" for item in public)
    assert {item["name"] for item in public} >= {
        "92科比", "爱在冰川", "炒股养家", "陈小群", "葛卫东",
        "华东大导弹", "宁波敢死队", "任泽平", "退学炒股", "一瞬流光",
        "赵老哥", "Asking", "北京炒家", "欢乐海岸", "利弗莫尔",
        "乔帮主", "章盟主",
    }


def test_perspective_selection_is_allowlisted_deduplicated_and_capped() -> None:
    assert stock_perspectives.resolve_perspective_ids(
        ["92kobe", "asking", "92kobe"]
    ) == ("92kobe", "asking")

    with pytest.raises(stock_perspectives.PerspectiveSelectionError, match="最多选择 4 个"):
        stock_perspectives.resolve_perspective_ids([
            "92kobe", "asking", "aizaibingchuan", "chaoguyangjia", "chenxiaoqun",
        ])
    with pytest.raises(stock_perspectives.PerspectiveSelectionError, match="未知分析视角"):
        stock_perspectives.resolve_perspective_ids(["user-injected-prompt"])
    with pytest.raises(stock_perspectives.PerspectiveSelectionError, match="必须是数组"):
        stock_perspectives.resolve_perspective_ids("92kobe")


def test_perspective_prompt_keeps_views_separate_and_subordinate() -> None:
    prompt = stock_perspectives.build_perspective_prompt(
        ["aizaibingchuan", "asking"]
    )

    assert "#### 爱在冰川视角" in prompt
    assert "#### Asking视角" in prompt
    assert "多视角共识与分歧" in prompt
    assert "不得声称自己是这些人物" in prompt
    assert "无权覆盖 frozen_decision" in prompt
    assert "赵老哥视角" not in prompt


@pytest.mark.asyncio
async def test_perspective_api_is_retired_and_chat_rejects_legacy_selection() -> None:
    assert not hasattr(routes, "chat_perspectives")
    response = await routes.chat(Request(body={
        "mode": "stock",
        "message": "明天怎么看？",
        "code": "600519",
        "perspective_ids": ["92kobe"],
    }))
    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    assert json.loads(response.body)["error_code"] == "perspective_selection_retired"


@pytest.mark.asyncio
async def test_deterministic_stock_chat_rejects_unread_images() -> None:
    response = await routes.chat(Request(body={
        "mode": "stock",
        "message": "结合图片分析",
        "code": "600519",
        "images": ["data:image/png;base64,AAAA"],
    }))
    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    assert json.loads(response.body)["error_code"] == "stock_ask_images_not_supported"


@pytest.mark.asyncio
async def test_engine_injects_selected_views_and_requires_all_sections(monkeypatch) -> None:
    captured: list[list[dict]] = []
    content = (
        "## 平安银行（000001）·多视角问股\n\n"
        "当前冻结动作仍是观望。只有放量站稳 10.87 后才获得强化，"
        "有效跌破 10.63 后转入防守。\n\n"
        "#### 92科比视角\n"
        + "当前更重要的是存量资金是否形成连续承接，不能因单日冲高就把分歧误判成新周期。" * 4
        + "\n\n#### Asking视角\n"
        + "这里尚未形成只做最强所要求的明确跟随信号，等待市场先走出方向比主动预测更重要。" * 4
        + "\n\n### 多视角共识与分歧\n"
        + "两种框架都支持等待确认；前者更重视预期差和资金守恒，后者更重视势与最强地位。" * 4
        + "\n\n### 结构与量价\n"
        + "趋势、动量、成交量和承接需要互相确认，不能用单一信号替代完整判断。" * 5
        + "\n\n### 市场与板块背景\n"
        + "个股强弱必须放回所属行业和全市场风险偏好中观察，板块没有共振时不提高确定性。" * 4
        + "\n\n### 下一交易日三种情景\n"
        + "强于预期时等待有效确认，正常震荡时继续观望，弱于预期时优先控制风险。" * 4
        + "\n\n### 执行边界\n条件未满足前不提前下注，结构失效后不为原判断寻找理由。"
    )

    class FakeHermes:
        async def chat(self, messages, **_kwargs):
            captured.append(messages)
            return content

    engine = engine_mod.Engine.__new__(engine_mod.Engine)
    engine.store = SimpleNamespace(knowledge_digest=lambda: "")
    engine.hermes = FakeHermes()
    monkeypatch.setattr(engine_mod.market, "market_sectors", lambda: {"sectors": []})
    monkeypatch.setattr(engine_mod.senti, "compute_today", lambda _store: {"score": 60})
    monkeypatch.setattr(engine_mod.market, "market_overview", lambda: {"total_amount": 1})
    card = {
        "code": "000001",
        "name": "平安银行",
        "action": "观望",
        "signal_action": "买入",
        "direct_answer": "当前先观望",
        "answer_stage": "最近交易日回答",
        "market_quote": {"price": 10.85, "name": "平安银行"},
        "trade_plan": {"entry_trigger": "放量站稳 10.87", "stop": "有效跌破 10.63"},
        "technical_analysis": {"structure": "震荡偏强"},
        "board": {"industry": "银行"},
    }

    answer = "".join([
        chunk async for chunk in engine.governed_stock_explanation_stream(
            "明天怎么看？",
            card,
            history=[],
            perspective_ids=("92kobe", "asking"),
        )
    ])

    assert answer == content
    assert len(captured) == 1
    system_prompt = captured[0][0]["content"]
    assert "#### 92科比视角" in system_prompt
    assert "#### Asking视角" in system_prompt
    assert "赵老哥视角" not in system_prompt
    assert "回答目标为约 1200—1960 个中文字符" in system_prompt


def test_conversation_store_persists_perspective_name_and_version(tmp_path) -> None:
    store = Store(tmp_path)
    conversation = store.create_conversation("stock")
    assert store.add_messages(conversation["id"], [{
        "role": "assistant",
        "content": "完整回答",
        "perspectives": [{
            "id": "aizaibingchuan",
            "name": "爱在冰川",
            "version": "2026-08-06",
            "instruction": "不应写入历史",
        }],
    }])

    saved = store.get_conversation(conversation["id"])["messages"][0]
    assert saved["perspectives"] == [{
        "id": "aizaibingchuan",
        "name": "爱在冰川",
        "version": "2026-08-06",
    }]
