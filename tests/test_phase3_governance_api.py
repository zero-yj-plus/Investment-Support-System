from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse

from hermes_trader.api import routes
from hermes_trader.analysis import engine as engine_mod, recommend
from hermes_trader.analysis.advice_proposal import proposal_ledger_for
from hermes_trader.knowledge.store import Store


class Request:
    def __init__(self, state, body: dict | None = None):
        self.app = SimpleNamespace(state=state)
        self._body = body or {}
        self.query_params = {}

    async def json(self):
        return self._body


def response_body(response):
    if isinstance(response, JSONResponse):
        return json.loads(response.body)
    return response


@pytest.mark.asyncio
async def test_industry_enrichment_is_bounded_and_does_not_call_full_stock_detail(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def industries(codes):
        batch = list(codes)
        calls.append(batch)
        return {
            code: {"industry": f"行业{code}", "source": "TDX relation"}
            for code in batch
        }

    monkeypatch.setattr(routes.tdx_api, "industry_for_codes", industries)
    monkeypatch.setattr(
        routes.stock,
        "stock_detail",
        lambda _code: pytest.fail("行业核验不得调用完整 stock_detail"),
    )
    started = time.monotonic()
    result = await routes._server_verified_industries(f"{i:06d}" for i in range(1, 51))

    assert len(result) == 50
    assert len(calls) == 1
    assert len(calls[0]) == 50
    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_interactive_industry_gate_uses_one_tdx_relation_batch(monkeypatch) -> None:
    calls: list[list[str]] = []

    def industries(codes):
        calls.append(list(codes))
        return {
            code: {"industry": "银行", "source": "TDX relation"}
            for code in codes
        }

    monkeypatch.setattr(routes.tdx_api, "industry_for_codes", industries)
    monkeypatch.setattr(
        routes.public_data,
        "verified_industry",
        lambda _code: pytest.fail("交互风险闸不得逐股请求公开 F10"),
    )

    result = await routes._server_verified_industries(
        ["000001", "600000"], interactive=True
    )

    assert calls == [["000001", "600000"]]
    assert result == {"000001": "银行", "600000": "银行"}


@pytest.mark.asyncio
async def test_knowledge_review_queue_approve_and_reject_are_exposed(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save_report("close", "收盘复盘", "7月13日复盘", date="2026-07-13")
    store.save_report("close", "收盘复盘", "7月12日复盘", date="2026-07-12")
    approved_id = store.add_knowledge("交易原则", "手工规则", "用户明确输入")
    draft_id = store.add_knowledge(
        "复盘笔记", "待复核", "只有一次复盘",
        status="draft", origin="auto_summary",
        source_report_ids=["report:close:2026-07-13"], sample_count=1,
    )
    reject_id = store.add_knowledge(
        "复盘笔记", "错误归因", "样本不足",
        status="draft", origin="auto_summary",
        source_report_ids=["report:close:2026-07-12"], sample_count=1,
    )
    state = SimpleNamespace(store=store)

    listing = await routes.get_knowledge(Request(state))
    queue = await routes.knowledge_review_queue(Request(state))
    approved = await routes.approve_knowledge(draft_id, Request(state, {"approved_by": "forged-admin"}))
    rejected = await routes.reject_knowledge(
        reject_id, Request(state, {"reason": "只有单日样本，不能泛化"}),
    )

    assert listing["schema_version"] == "KnowledgeRule.v1"
    assert listing["review_queue_count"] == 2
    assert {item["id"] for item in queue["items"]} == {draft_id, reject_id}
    assert approved["item"]["status"] == "approved"
    assert approved["item"]["approved_by"] == "user:web"
    assert rejected["item"]["status"] == "rejected"
    digest = store.knowledge_digest()
    assert "手工规则" in digest and "待复核" in digest
    assert "错误归因" not in digest
    assert approved_id


@pytest.mark.asyncio
async def test_sentiment_event_archive_is_idempotent_and_stays_draft_until_approved(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store)
    event = {
        "event_id": "news-001",
        "title": "某公司发布项目进展公告",
        "content": "公司披露项目仍处于建设阶段。",
        "published_at": "2026-08-10T09:30:00+08:00",
        "fetched_at": "2026-08-10T09:31:00+08:00",
        "source": "公开采集",
        "a_share": {
            "matches": [{"kind": "stock", "code": "600000", "name": "浦发银行"}],
        },
        "observations": [{
            "collection_platform_name": "公开采集端",
            "source_url": "https://example.com/news/001",
        }],
    }

    first = await routes.archive_news_a_share(Request(state, {"event": event}))
    second = await routes.archive_news_a_share(Request(state, {"event": event}))
    item = store.knowledge_items()[0]

    assert first["ok"] is True and first["created"] is True
    assert second["ok"] is True and second["created"] is False
    assert first["id"] == second["id"] == item["id"]
    assert len(store.knowledge_items()) == 1
    assert item["category"] == "舆情留档"
    assert item["status"] == "draft"
    assert item["origin"] == "sentiment_event_archive"
    assert item["archive_key"]
    assert "公司披露项目仍处于建设阶段" in item["content"]
    assert "600000" in item["content"]
    assert item["title"] not in store.knowledge_digest()

    approved = await routes.approve_knowledge(item["id"], Request(state, {"sample_count": 1}))
    assert approved["ok"] is True
    assert approved["item"]["status"] == "approved"
    assert item["title"] in store.knowledge_digest()


@pytest.mark.asyncio
async def test_buy_recommendation_is_blocked_by_server_risk_level_five(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store, evidence_store=None, market_gateway=None)

    async def risk_five(_store):
        return {"risk_level": 5, "command": {"blackswan": ["跌停潮"]}}

    monkeypatch.setattr(routes, "_pulse_locked", risk_five)
    response = await routes.add_recommendation(Request(state, {
        "code": "000001", "rec_price": 10.0, "direction": "buy",
        "position_cap_pct": 3, "client_request_id": "blocked-buy",
    }))
    body = response_body(response)

    assert response.status_code == 409
    assert body["blocked"] is True
    assert body["risk_decision"]["decision"] == "veto"
    assert store.recommendations() == []
    assert recommend.event_store_for(store).events() == []
    proposals = proposal_ledger_for(store).events(body["proposal_id"])
    assert [event["state"] for event in proposals] == ["proposed", "vetoed"]
    assert all(event["score_eligible"] is False for event in proposals)


@pytest.mark.asyncio
async def test_watch_recommendation_does_not_need_market_permission(tmp_path: Path) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store, evidence_store=None, market_gateway=None)
    response = await routes.add_recommendation(Request(state, {
        "code": "000001", "rec_price": 10.0, "direction": "watch",
        "client_request_id": "watch-no-market",
    }))

    assert response["ok"] is True
    assert response["recommendation_created"] is False
    assert response["state"] == "approved_observation"
    assert store.recommendations() == []
    assert recommend.event_store_for(store).events() == []
    events = proposal_ledger_for(store).events(response["proposal_id"])
    assert [event["state"] for event in events] == ["proposed", "approved"]
    assert all(event["score_eligible"] is False for event in events)


@pytest.mark.asyncio
async def test_entry_without_fresh_price_evidence_is_vetoed_without_error(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store, evidence_store=None, market_gateway=None)
    body = await routes._authorize_and_record_advice(state, {
        "code": "600036", "direction": "buy", "position_cap_pct": 3,
        "client_request_id": "missing-price-entry",
    }, trusted_internal=True, preauthorized_risk={
        "decision": "allow", "final_action": "buy", "position_cap_pct": 3.0,
        "reason_codes": [], "reasons": [],
    })

    assert body["blocked"] is True
    assert body["recommendation_created"] is False
    assert "reference_price_evidence_incomplete" in body["risk_decision"]["reason_codes"]
    assert store.recommendations() == []
    assert recommend.event_store_for(store).events() == []
    proposals = proposal_ledger_for(store).events(body["proposal_id"])
    assert [event["state"] for event in proposals] == ["proposed", "vetoed"]


@pytest.mark.asyncio
async def test_risk_downgraded_avoid_without_price_stays_observation(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store, evidence_store=None, market_gateway=None)
    result = await routes._authorize_and_record_advice(state, {
        "code": "600036", "direction": "回避", "position_cap_pct": 0,
        "client_request_id": "risk-downgraded-no-price",
    }, trusted_internal=True, preauthorized_risk={
        "decision": "veto", "final_action": "avoid", "position_cap_pct": 0.0,
        "reason_codes": ["market_risk_level_5"], "reasons": ["市场风险高"],
    })

    assert result["ok"] is True
    assert result["recommendation_created"] is False
    assert result["state"] == "approved_observation"
    assert result["degraded_reason"] == "reference_price_unavailable"
    assert store.recommendations() == []
    assert recommend.event_store_for(store).events() == []
    events = proposal_ledger_for(store).events(result["proposal_id"])
    assert [event["state"] for event in events] == ["proposed", "approved"]


@pytest.mark.asyncio
async def test_advice_proposal_api_returns_latest_state_without_score_eligibility(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store, evidence_store=None, market_gateway=None)
    first = await routes.add_recommendation(Request(state, {
        "code": "000001", "rec_price": 10.0, "direction": "watch",
        "client_request_id": "proposal-list-watch",
    }))
    second = await routes.add_recommendation(Request(state, {
        "code": "000002", "rec_price": 12.0, "direction": "watch",
        "client_request_id": "proposal-list-watch-2",
    }))

    response = await routes.get_advice_proposals(Request(state))

    assert response["ok"] is True
    assert response["score_eligible"] is False
    assert response["count"] == 2
    rows = {row["proposal_id"]: row for row in response["proposals"]}
    assert rows[first["proposal_id"]]["state"] == "approved"
    assert rows[second["proposal_id"]]["state"] == "approved"
    assert all(row["score_eligible"] is False for row in rows.values())


@pytest.mark.asyncio
async def test_free_text_entry_condition_stays_proposal_until_trigger_is_verified(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store, evidence_store=None, market_gateway=None)

    async def risk_one(_store):
        return {"risk_level": 1, "command": {"blackswan": []}}

    monkeypatch.setattr(routes, "_pulse_locked", risk_one)
    async def complete_evidence(_state, body):
        return {
            **body,
            "reference_price_evidence": {
                "value": 10.0, "evidence_id": "ev-free-text", "source_id": "test",
                "observed_at": "2026-07-13T10:00:00+08:00", "quality_status": "ok",
            },
            "evidence_ids": ["ev-free-text"],
        }, True

    monkeypatch.setattr(routes, "_bind_reference_price_evidence", complete_evidence)
    monkeypatch.setattr(
        routes.tdx_api,
        "industry_for_codes",
        lambda codes: {
            code: {"industry": "银行", "source": "TDX relation"} for code in codes
        },
    )
    monkeypatch.setattr(routes.stock, "quick_quote", lambda _code: {"amount": 100_000_000})
    response = await routes.add_recommendation(Request(state, {
        "code": "000001", "rec_price": 10.0, "direction": "buy",
        "position_cap_pct": 3, "buy_condition": "回踩十日线不破后再考虑",
        "client_request_id": "free-text-entry",
    }))

    assert response["ok"] is True
    assert response["state"] == "approved_waiting_trigger"
    assert response["recommendation_created"] is False
    assert store.recommendations() == []
    assert recommend.event_store_for(store).events() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "body", "expected"),
    [
        ("stock", {"message": "分析一下", "code": "000001"}, "问股治理流"),
        ("position", {"message": "诊断持仓", "codes": ["000001"]}, "持仓治理流"),
    ],
)
async def test_actionable_chat_modes_cannot_fall_back_to_raw_model_stream(
    monkeypatch, tmp_path: Path, mode: str, body: dict, expected: str,
) -> None:
    class RawModelMustNotRun:
        def chat_stream(self, *_args, **_kwargs):
            pytest.fail("问股/持仓不得调用未治理的原始模型流")

    async def governed_stock(_state, _code):
        yield "问股治理流"

    async def governed_position(_state, _codes):
        yield "持仓治理流"

    monkeypatch.setattr(routes, "_governed_stock_chat_stream", governed_stock)
    monkeypatch.setattr(routes, "_governed_position_chat_stream", governed_position)
    state = SimpleNamespace(store=Store(tmp_path), engine=RawModelMustNotRun())

    response = await routes.chat(Request(state, {"mode": mode, **body}))
    chunks = [chunk async for chunk in response.body_iterator]
    events = [
        json.loads(line.removeprefix("data: "))
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("data: ")
    ]

    assert expected in "".join(chunks)
    assert events[0]["stage"]
    assert any(event.get("delta") == expected for event in events)
    assert events[-1] == {"done": True, "truncated": False}


@pytest.mark.asyncio
async def test_governed_stock_chat_answers_the_actual_question(monkeypatch) -> None:
    async def fake_card(_state, _code, *, source="问股"):
        return {
            "name": "平安银行", "action": "持有", "signal_action": "持有",
            "market_quote": {"price": 10.85, "change_pct": 1.5},
            "trade_plan": {
                "entry_trigger": "回踩 10.70–10.80 缩量企稳",
                "stop": "有效跌破 10.63",
            },
            "evidence": [{
                "finding": "现价高于昨收", "source": "TDX API+腾讯",
            }],
        }

    monkeypatch.setattr(routes, "_governed_and_record_diagnosis", fake_card)
    monkeypatch.setattr(
        routes,
        "build_market_context",
        lambda: {"market_session": "non_trading"},
    )
    state = SimpleNamespace(engine=SimpleNamespace())
    chunks = [
        chunk async for chunk in routes._governed_stock_chat_stream(
            state, "000001", "能否继续持有？",
        )
    ]
    answer = "".join(chunks)

    assert "可以继续持有，但不是无条件死拿" in answer
    assert "有效跌破 10.63" in answer
    assert "现价 10.85" in answer
    assert "·最近交易日回答" in answer
    assert "·盘中回答" not in answer
    assert "proposal_" not in answer


@pytest.mark.asyncio
async def test_holding_question_does_not_translate_market_entry_veto_into_forced_exit(
    monkeypatch,
) -> None:
    received = {}

    async def fake_card(_state, _code, *, source="问股"):
        return {
            "code": "000001", "name": "平安银行", "action": "回避",
            "signal_action": "买入", "summary": "全市场风险禁止新仓",
            "market_quote": {"price": 10.85, "change_pct": 1.5},
            "trade_plan": {
                "entry_trigger": "放量站稳 10.87",
                "stop": "有效跌破 10.63",
            },
            "evidence": [],
        }

    async def explain(_message, card, *, history):
        received.update(card)
        yield ""

    monkeypatch.setattr(routes, "_governed_and_record_diagnosis", fake_card)
    state = SimpleNamespace(
        engine=SimpleNamespace(governed_stock_explanation_stream=explain),
    )
    answer = "".join([
        chunk async for chunk in routes._governed_stock_chat_stream(
            state, "000001", "能否继续持有？",
        )
    ])

    assert "已有仓位可以条件性持有，但当前不加仓" in answer
    assert "### 现在怎么做" not in answer
    assert "当前动作：**回避**" not in answer
    assert "有效跌破 10.63" in answer
    assert received["action"] == "持有（不加仓）"


@pytest.mark.asyncio
async def test_stock_chat_emits_only_one_complete_model_answer(monkeypatch) -> None:
    async def fake_card(_state, _code, *, source="问股"):
        return {
            "code": "000001", "name": "平安银行", "action": "观望",
            "signal_action": "买入", "market_quote": {"price": 10.85},
            "trade_plan": {
                "entry_trigger": "放量站稳 10.87",
                "stop": "有效跌破 10.63",
            },
        }

    model_answer = (
        "## 平安银行（000001）·最近交易日分析\n\n"
        "当前先观望，放量站稳 10.87 后才确认转强；有效跌破 10.63 则转入防守。\n\n"
        "### 结构与量价\n目前更适合等待市场确认，而不是提前猜方向。"
        "趋势、量价和承接需要放在同一个框架里观察。\n\n"
        "### 下一交易日推演\n强于预期时等待确认，正常震荡时保持耐心，弱于预期时优先控制风险。"
    )

    async def explain(_message, _card, *, history):
        assert history == []
        yield model_answer[:80]
        yield model_answer[80:]

    monkeypatch.setattr(routes, "_governed_and_record_diagnosis", fake_card)
    monkeypatch.setattr(
        routes, "build_market_context", lambda: {"market_session": "non_trading"},
    )
    state = SimpleNamespace(
        engine=SimpleNamespace(governed_stock_explanation_stream=explain),
    )
    answer = "".join([
        chunk async for chunk in routes._governed_stock_chat_stream(
            state, "000001", "明天怎么看？",
        )
    ])

    assert answer == model_answer
    assert "### 现在怎么做" not in answer
    assert "### 为什么" not in answer
    assert "针对你这句话的补充" not in answer


@pytest.mark.asyncio
async def test_stock_chat_discards_partial_model_answer(monkeypatch) -> None:
    async def fake_card(_state, _code, *, source="问股"):
        return {
            "code": "000001", "name": "平安银行", "action": "观望",
            "signal_action": "观望", "market_quote": {"price": 10.85},
            "trade_plan": {
                "entry_trigger": "放量站稳 10.87",
                "stop": "有效跌破 10.63",
            },
            "missing": ["集合竞价暂无专用源"],
        }

    async def explain(_message, _card, *, history):
        yield "这是一段不应展示的半截模型正文"
        raise RuntimeError("stream interrupted")

    monkeypatch.setattr(routes, "_governed_and_record_diagnosis", fake_card)
    state = SimpleNamespace(
        engine=SimpleNamespace(governed_stock_explanation_stream=explain),
    )
    answer = "".join([
        chunk async for chunk in routes._governed_stock_chat_stream(
            state, "000001", "明天怎么看？",
        )
    ])

    assert "这是一段不应展示的半截模型正文" not in answer
    assert "次日不做无条件猜涨跌" in answer
    assert "模型未返回可采信的完整终态" in answer
    assert "集合竞价暂无专用源" in answer
    assert "不是深度成稿失败原因" in answer


@pytest.mark.asyncio
async def test_stock_chat_keeps_complete_unsafe_answer_with_validation_labels(
    monkeypatch,
) -> None:
    async def fake_card(_state, _code, *, source="问股"):
        return {
            "code": "000001", "name": "平安银行", "action": "观望",
            "signal_action": "观望", "market_quote": {"price": 10.85},
            "trade_plan": {
                "entry_trigger": "放量站稳 10.87",
                "stop": "有效跌破 10.63",
            },
            "missing": ["个股资金流为上一交易日数据"],
        }

    unsafe = "## 平安银行（000001）\n\n模型原文建议现在买入，并关注 99.99 元。"

    async def explain(_message, card, *, history):
        card["_explanation_failure_reason"] = "safety_validation_failed"
        card["_explanation_warning_codes"] = [
            "frozen_action_conflict", "unsupported_price_claim",
        ]
        yield unsafe

    monkeypatch.setattr(routes, "_governed_and_record_diagnosis", fake_card)
    state = SimpleNamespace(
        engine=SimpleNamespace(governed_stock_explanation_stream=explain),
    )
    answer = "".join([
        chunk async for chunk in routes._governed_stock_chat_stream(
            state, "000001", "现在怎么看？",
        )
    ])

    assert unsafe in answer
    assert "以下模型全文予以保留，但未全部采信" in answer
    assert "买卖动作与服务端冻结结论冲突" in answer
    assert "服务端已核验结论" in answer
    assert "个股资金流为上一交易日数据" in answer
    assert "深度成稿未采用" not in answer


@pytest.mark.asyncio
async def test_engine_stock_answer_uses_unified_packet_and_complete_generation(
    monkeypatch,
) -> None:
    captured = {}
    content = (
        "## 平安银行（000001）·最近交易日分析\n\n"
        "当前结论是观望。只有放量站稳 10.87 后，结构才得到确认；"
        "有效跌破 10.63 后应转入防守。\n\n"
        "### 结构与量价\n"
        + "趋势、动量与成交量需要互相确认，单一信号不足以形成交易依据。" * 12
        + "\n\n### 市场与板块背景\n"
        + "个股强弱需要结合所属行业和全市场风险偏好判断，不能脱离环境追价。" * 8
        + "\n\n### 下一交易日三种情景\n"
        + "强于预期时等待有效确认，正常震荡时继续观察，弱于预期时优先防守。" * 5
        + "\n\n### 执行边界\n条件没有满足前不提前下注，失效后不再为原判断寻找理由。"
    )

    class FakeHermes:
        async def chat(self, messages, **kwargs):
            captured["messages"] = messages
            captured["kwargs"] = kwargs
            return content

    engine = engine_mod.Engine.__new__(engine_mod.Engine)
    engine.store = SimpleNamespace(knowledge_digest=lambda: "")
    engine.hermes = FakeHermes()
    monkeypatch.setattr(
        engine_mod.market, "market_sectors",
        lambda: {"sectors": [{"name": "银行", "change_pct": 0.5}]},
    )
    monkeypatch.setattr(engine_mod.senti, "compute_today", lambda _store: {"score": 60})
    monkeypatch.setattr(engine_mod.market, "market_overview", lambda: {"total_amount": 1})
    monkeypatch.setattr(
        engine_mod.stock, "stock_detail",
        lambda _code: pytest.fail("深度成稿不得再次抓取 stock_detail"),
    )
    card = {
        "code": "000001", "name": "平安银行", "action": "观望",
        "signal_action": "买入", "direct_answer": "当前先观望",
        "answer_stage": "最近交易日回答",
        "market_quote": {"price": 10.85, "name": "平安银行"},
        "trade_plan": {
            "entry_trigger": "放量站稳 10.87",
            "stop": "有效跌破 10.63",
        },
        "technical_analysis": {
            "structure": "震荡偏强",
            "metrics": {"ma20": 10.2, "ma60": 9.8},
        },
        "board": {"industry": "银行"},
    }

    answer = "".join([
        chunk async for chunk in engine.governed_stock_explanation_stream(
            "明天怎么看？", card, history=[],
        )
    ])

    assert answer == content
    system_prompt = captured["messages"][0]["content"]
    assert "统一分析资料包" in system_prompt
    assert "800—1300 个中文字符" in system_prompt
    assert "不再重新抓一次 stock_detail" not in system_prompt
    assert "450 个中文字" not in system_prompt
    assert "max_tokens" not in captured["kwargs"]


@pytest.mark.asyncio
async def test_engine_short_answer_is_soft_and_rewritten_once(monkeypatch) -> None:
    short = (
        "## 平安银行（000001）\n\n当前观望。放量站稳 10.87 才转强，"
        "有效跌破 10.63 则转入防守。"
    )
    rewritten = (
        short
        + "\n\n### 结构与量价\n"
        + "趋势、动量和量能需要互相确认，不能用单一指标替代完整判断。" * 14
        + "\n\n### 市场与板块背景\n"
        + "个股所处环境会影响信号成功率，板块联动和市场风险偏好需要同步观察。" * 8
        + "\n\n### 下一交易日三种情景\n"
        + "强于预期等待确认，正常震荡保持观察，弱于预期优先控制风险。" * 5
    )
    calls = []

    class FakeHermes:
        async def chat(self, messages, **kwargs):
            calls.append((messages, kwargs))
            return short if len(calls) == 1 else rewritten

    engine = engine_mod.Engine.__new__(engine_mod.Engine)
    engine.store = SimpleNamespace(knowledge_digest=lambda: "")
    engine.hermes = FakeHermes()
    monkeypatch.setattr(engine_mod.market, "market_sectors", lambda: {"sectors": []})
    monkeypatch.setattr(engine_mod.senti, "compute_today", lambda _store: {})
    monkeypatch.setattr(engine_mod.market, "market_overview", lambda: {})
    card = {
        "code": "000001", "name": "平安银行", "action": "观望",
        "direct_answer": "当前先观望",
        "market_quote": {"code": "000001", "name": "平安银行", "price": 10.85},
        "trade_plan": {
            "entry_trigger": "放量站稳 10.87",
            "stop": "有效跌破 10.63",
        },
    }

    answer = "".join([
        chunk async for chunk in engine.governed_stock_explanation_stream(
            "为什么？", card, history=[],
        )
    ])

    assert answer == rewritten
    assert len(calls) == 2
    assert calls[1][1]["purpose"] == "chat:stock-governed-explanation-rewrite"
    assert "answer_too_short" in calls[1][0][-1]["content"]


@pytest.mark.asyncio
async def test_engine_keeps_safe_short_answer_when_rewrite_fails(monkeypatch) -> None:
    short = "## 平安银行（000001）\n\n现有结构仍需等待确认，暂时不追价。"
    calls = 0

    class FakeHermes:
        async def chat(self, messages, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return short
            raise RuntimeError("rewrite unavailable")

    engine = engine_mod.Engine.__new__(engine_mod.Engine)
    engine.store = SimpleNamespace(knowledge_digest=lambda: "")
    engine.hermes = FakeHermes()
    monkeypatch.setattr(engine_mod.market, "market_sectors", lambda: {"sectors": []})
    monkeypatch.setattr(engine_mod.senti, "compute_today", lambda _store: {})
    monkeypatch.setattr(engine_mod.market, "market_overview", lambda: {})
    card = {
        "code": "000001", "name": "平安银行", "action": "观望",
        "direct_answer": "当前结论是观望，不提前追价。",
        "market_quote": {"code": "000001", "name": "平安银行", "price": 10.85},
        "trade_plan": {
            "entry_trigger": "放量站稳 10.87",
            "stop": "有效跌破 10.63",
        },
        "missing": ["集合竞价暂无专用源"],
    }

    answer = "".join([
        chunk async for chunk in engine.governed_stock_explanation_stream(
            "为什么？", card, history=[],
        )
    ])

    assert calls == 2
    assert "当前结论是观望，不提前追价" in answer
    assert "放量站稳 10.87" in answer
    assert "有效跌破 10.63" in answer
    assert card.get("_explanation_failure_reason") is None


@pytest.mark.asyncio
async def test_engine_blocks_conflicting_or_unsupported_price_answer(monkeypatch) -> None:
    unsafe = (
        "## 平安银行（000001）\n\n当前虽然冻结为观望，但现在买入，"
        "并把目标价设为 99.99 元。"
    )

    class FakeHermes:
        async def chat(self, messages, **kwargs):
            return unsafe

    engine = engine_mod.Engine.__new__(engine_mod.Engine)
    engine.store = SimpleNamespace(knowledge_digest=lambda: "")
    engine.hermes = FakeHermes()
    monkeypatch.setattr(engine_mod.market, "market_sectors", lambda: {"sectors": []})
    monkeypatch.setattr(engine_mod.senti, "compute_today", lambda _store: {})
    monkeypatch.setattr(engine_mod.market, "market_overview", lambda: {})
    card = {
        "code": "000001", "name": "平安银行", "action": "观望",
        "direct_answer": "当前观望",
        "market_quote": {"code": "000001", "name": "平安银行", "price": 10.85},
        "trade_plan": {
            "entry_trigger": "放量站稳 10.87",
            "stop": "有效跌破 10.63",
        },
    }

    answer = "".join([
        chunk async for chunk in engine.governed_stock_explanation_stream(
            "为什么？", card, history=[],
        )
    ])

    assert answer == unsafe
    assert card["_explanation_failure_reason"] == "safety_validation_failed"
    assert "frozen_action_conflict" in card["_explanation_warning_codes"]
    assert "unsupported_price_claim" in card["_explanation_warning_codes"]


def test_single_stock_section_prices_remain_bound_to_the_selected_stock() -> None:
    packet = {
        "subject": {"code": "002416", "name": "爱施德"},
        "frozen_decision": {
            "final_action": "观望",
            "entry_condition": "回踩 MA20 9.12 企稳，或突破 20日高点 10.08",
            "invalidation_condition": "有效跌破 MA20 9.12",
        },
        "quote": {
            "code": "002416", "name": "爱施德", "price": 10.01,
            "high": 10.08, "low": 9.95, "prev_close": 9.96,
        },
        "technical_analysis": {
            "metrics": {"ma20": 9.12},
            "levels": {"resistance": 10.08},
        },
    }
    content = (
        "## 爱施德（002416）·最近交易日分析\n\n当前结论为观望。\n\n"
        "### 结构与量价\n回踩 MA20 9.12 企稳后才观察转强。\n\n"
        "### 执行边界\n突破 20日高点 10.08 才确认；有效跌破 MA20 9.12 则失效。"
    )

    review = engine_mod._review_stock_explanation(content, packet)

    assert review["ok"] is True
    assert "unsupported_price_claim" not in review["hard_errors"]


@pytest.mark.asyncio
async def test_engine_rewrites_cached_partial_when_model_finishes_by_length(
    monkeypatch,
) -> None:
    partial = "## 贵州茅台（600519）\n\n当前观望，趋势结构仍需确认。"
    repaired = (
        "## 贵州茅台（600519）·盘中分析\n\n当前结论是观望。"
        "回踩 MA20 1274.40 附近企稳后才观察强化，"
        "放量突破 20日高点 1363.35 才确认转强；"
        "有效跌破 MA20 1274.40 则转入防守。"
    )
    calls = []

    class FakeHermes:
        async def chat_stream(self, messages, **kwargs):
            calls.append((messages, kwargs))
            meta = kwargs["meta"]
            if len(calls) == 1:
                meta["finish_reason"] = "length"
                yield partial
                raise RuntimeError("Hermes Agent 运行未完成（length）")
            meta["finish_reason"] = "stop"
            yield repaired

    engine = engine_mod.Engine.__new__(engine_mod.Engine)
    engine.store = SimpleNamespace(knowledge_digest=lambda: "")
    engine.hermes = FakeHermes()
    monkeypatch.setattr(engine_mod.market, "market_sectors", lambda: {"sectors": []})
    monkeypatch.setattr(engine_mod.senti, "compute_today", lambda _store: {})
    monkeypatch.setattr(engine_mod.market, "market_overview", lambda: {})
    card = {
        "code": "600519", "name": "贵州茅台", "action": "观望",
        "direct_answer": "当前结论是观望。",
        "market_quote": {
            "code": "600519", "name": "贵州茅台", "price": 1351.0,
            "high": 1363.35, "low": 1340.0,
        },
        "technical_analysis": {"metrics": {"ma20": 1274.40}},
        "trade_plan": {
            "entry_trigger": "回踩 MA20 1274.40 附近企稳，或放量突破 20日高点 1363.35",
            "stop": "有效跌破 MA20 1274.40",
        },
    }

    answer = "".join([
        chunk async for chunk in engine.governed_stock_explanation_stream(
            "明天的关键预期是什么？", card, history=[],
        )
    ])

    assert repaired in answer
    assert len(calls) == 2
    assert calls[0][1]["purpose"] == "chat:stock-governed-explanation"
    assert calls[1][1]["purpose"] == "chat:stock-governed-explanation-rewrite"
    assert "max_tokens" not in calls[0][1]
    assert "max_tokens" not in calls[1][1]
    assert partial in calls[1][0][-2]["content"]
    assert card.get("_explanation_failure_reason") is None


@pytest.mark.asyncio
async def test_engine_accepts_complete_missing_stop_after_full_validation(
    monkeypatch,
) -> None:
    content = (
        "## 贵州茅台（600519）次日关键预期\n\n"
        "当前动作维持观望，只有等待确认后才调整判断；若结构失效则转入防守。\n\n"
        "### 结构与量价\n"
        + "趋势、动量和成交变化需要相互验证，单一信号不能替代完整判断。" * 16
        + "\n\n### 市场与板块背景\n"
        + "个股强弱仍需放在板块联动和市场风险偏好中观察，不能脱离环境追价。" * 10
        + "\n\n### 下一交易日三种情景\n"
        + "强于预期时等待确认，正常震荡时保持耐心，弱于预期时优先控制风险。" * 8
        + "\n\n### 执行边界\n只有等待确认后才考虑调整；若结构失效，当前判断随即作废。"
    )

    class FakeHermes:
        async def chat_stream(self, messages, **kwargs):
            yield content
            raise RuntimeError("Hermes Agent 未返回可采信的完整终态")

    engine = engine_mod.Engine.__new__(engine_mod.Engine)
    engine.store = SimpleNamespace(knowledge_digest=lambda: "")
    engine.hermes = FakeHermes()
    monkeypatch.setattr(engine_mod.market, "market_sectors", lambda: {"sectors": []})
    monkeypatch.setattr(engine_mod.senti, "compute_today", lambda _store: {})
    monkeypatch.setattr(engine_mod.market, "market_overview", lambda: {})
    card = {
        "code": "600519", "name": "贵州茅台", "action": "观望",
        "direct_answer": "当前观望",
        "market_quote": {"code": "600519", "name": "贵州茅台", "price": 1351.0},
        "trade_plan": {"entry_trigger": "等待确认", "stop": "结构失效"},
        "missing": ["基础财务"],
    }

    answer = "".join([
        chunk async for chunk in engine.governed_stock_explanation_stream(
            "明天的关键预期是什么？", card, history=[],
        )
    ])

    assert answer == content
    assert card.get("_explanation_failure_reason") is None


@pytest.mark.asyncio
async def test_engine_rejects_short_missing_stop_after_rewrite_still_incomplete(
    monkeypatch,
) -> None:
    calls = 0

    class FakeHermes:
        async def chat_stream(self, messages, **kwargs):
            nonlocal calls
            calls += 1
            yield "## 贵州茅台（600519）\n\n正文看似完成，但上游没有终态。"
            raise RuntimeError("Hermes Agent 未返回可采信的完整终态")

    engine = engine_mod.Engine.__new__(engine_mod.Engine)
    engine.store = SimpleNamespace(knowledge_digest=lambda: "")
    engine.hermes = FakeHermes()
    monkeypatch.setattr(engine_mod.market, "market_sectors", lambda: {"sectors": []})
    monkeypatch.setattr(engine_mod.senti, "compute_today", lambda _store: {})
    monkeypatch.setattr(engine_mod.market, "market_overview", lambda: {})
    card = {
        "code": "600519", "name": "贵州茅台", "action": "观望",
        "direct_answer": "当前观望",
        "market_quote": {"code": "600519", "name": "贵州茅台", "price": 1351.0},
        "trade_plan": {"entry_trigger": "等待确认", "stop": "结构失效"},
        "missing": ["基础财务"],
    }

    answer = "".join([
        chunk async for chunk in engine.governed_stock_explanation_stream(
            "为什么？", card, history=[],
        )
    ])

    assert calls == 2
    assert "正文看似完成" in answer
    assert card["_explanation_failure_reason"] == "model_missing_stop"
    assert "model_missing_stop" in card["_explanation_warning_codes"]


@pytest.mark.asyncio
async def test_stock_chat_displays_exact_missing_stop_reason(monkeypatch) -> None:
    async def fake_card(_state, _code, *, source="问股"):
        return {
            "code": "600519", "name": "贵州茅台", "action": "观望",
            "signal_action": "观望", "market_quote": {"price": 1351.0},
            "trade_plan": {"entry_trigger": "等待确认", "stop": "结构失效"},
            "missing": ["基础财务"],
        }

    async def explain(_message, card, *, history):
        card["_explanation_failure_reason"] = "model_missing_stop"
        yield ""

    monkeypatch.setattr(routes, "_governed_and_record_diagnosis", fake_card)
    state = SimpleNamespace(
        engine=SimpleNamespace(governed_stock_explanation_stream=explain),
    )
    answer = "".join([
        chunk async for chunk in routes._governed_stock_chat_stream(
            state, "600519", "为什么？",
        )
    ])

    assert "上游未提供标准的 stop 终态" in answer
    assert "基础财务" in answer
    assert "不是深度成稿失败原因" in answer


@pytest.mark.asyncio
async def test_position_chat_uses_cost_pnl_and_share_count(monkeypatch) -> None:
    async def fake_card(_state, _code, *, source="问股"):
        assert source == "持仓"
        return {
            "name": "示例股份", "action": "减仓", "summary": "冲高承接不足",
            "market_quote": {"price": 12.0},
            "trade_plan": {
                "hold_trigger": "不破 11.50 可继续跟踪",
                "stop": "有效跌破 11.50",
            },
        }

    monkeypatch.setattr(routes, "_governed_and_record_diagnosis", fake_card)
    state = SimpleNamespace(
        store=SimpleNamespace(holdings=lambda: [{
            "code": "000001", "name": "示例股份", "cost": 10.0, "shares": 300,
        }]),
        engine=SimpleNamespace(),
    )
    chunks = [
        chunk async for chunk in routes._governed_position_chat_stream(
            state, ["000001"], "我这只怎么处理？",
        )
    ]
    answer = "".join(chunks)

    assert "现价 12.00 · 成本 10.00 · 持仓盈亏 +20.00%" in answer
    assert "当前动作：**减仓**；对应数量约 100 股" in answer
    assert "目标仓位 0%" in answer


@pytest.mark.asyncio
async def test_global_market_event_blocks_entry_without_mislabeling_stock_as_avoid(
    monkeypatch,
) -> None:
    async def fake_diagnose(_engine, _code, *, include_model=True):
        assert include_model is False
        return {
            "code": "000001", "name": "平安银行", "action": "观望",
            "decision_market_evidence": [
                {"field": "last_price", "value": 10.2, "evidence_id": "e1"},
                {"field": "prev_close", "value": 10.0, "evidence_id": "e2"},
                {"field": "change_pct", "value": 2.0, "evidence_id": "e3"},
                {"field": "amount", "value": 100_000_000, "evidence_id": "e4"},
                {"field": "volume_ratio", "value": 1.2, "evidence_id": "e5"},
                {"field": "turnover_rate", "value": 3.0, "evidence_id": "e6"},
            ],
            "decision_market_evidence_quality": {
                "status": "ok", "captured": 6, "requested": 6,
            },
            "market_quote": {
                "price": 10.2, "prev_close": 10.0, "open": 10.0,
                "high": 10.3, "low": 9.9,
            },
        }

    async def market_veto(*_args, **_kwargs):
        return {
            "decision": "veto", "final_action": "avoid", "position_cap_pct": 0.0,
            "effective_confidence": 0.7, "reason_codes": ["major_event"],
            "reasons": ["主要指数破位，暂停新开仓"],
        }

    monkeypatch.setattr(routes.diagnosis_mod, "diagnose", fake_diagnose)
    monkeypatch.setattr(routes, "_server_risk_decision", market_veto)
    state = SimpleNamespace(
        engine=object(), store=SimpleNamespace(holdings=lambda: []),
    )

    card = await routes.governed_diagnosis_card(state, "000001")

    assert card["signal_action"] == "买入"
    assert card["action"] == "观望"
    assert card["original_action"] == "买入"
    assert "必须回避" not in card["summary"]
    assert card["risk_decision"]["event_scope"] == "market_systemic"
    assert "不是该公司的重大事件结论" in card["risk_decision"]["reasons"][0]


@pytest.mark.asyncio
async def test_market_entry_veto_keeps_existing_holding_without_authorizing_add(
    monkeypatch,
) -> None:
    async def fake_diagnose(_engine, _code, *, include_model=True):
        assert include_model is False
        return {
            "code": "000001", "name": "平安银行", "action": "观望",
            "decision_market_evidence": [
                {"field": "last_price", "value": 10.2, "evidence_id": "e1"},
                {"field": "prev_close", "value": 10.0, "evidence_id": "e2"},
                {"field": "change_pct", "value": 2.0, "evidence_id": "e3"},
                {"field": "amount", "value": 100_000_000, "evidence_id": "e4"},
                {"field": "volume_ratio", "value": 1.2, "evidence_id": "e5"},
                {"field": "turnover_rate", "value": 3.0, "evidence_id": "e6"},
            ],
            "decision_market_evidence_quality": {
                "status": "ok", "captured": 6, "requested": 6,
            },
            "market_quote": {
                "price": 10.2, "prev_close": 10.0, "open": 10.0,
                "high": 10.3, "low": 9.9,
            },
        }

    async def market_veto(*_args, **_kwargs):
        return {
            "decision": "veto", "final_action": "avoid", "position_cap_pct": 0.0,
            "effective_confidence": 0.7,
            "reason_codes": ["market_risk_level_5", "major_event"],
            "reasons": ["市场风险为 5 级，禁止买入或加仓"],
        }

    monkeypatch.setattr(routes.diagnosis_mod, "diagnose", fake_diagnose)
    monkeypatch.setattr(routes, "_server_risk_decision", market_veto)
    state = SimpleNamespace(
        engine=object(),
        store=SimpleNamespace(holdings=lambda: [{"code": "000001", "shares": 100}]),
    )

    card = await routes.governed_diagnosis_card(state, "000001")

    assert card["signal_action"] == "加仓"
    assert card["action"] == "持有"
    assert card["risk_block_scope"] == "new_risk_only"
    assert card["position_cap_pct"] == 0.0


@pytest.mark.asyncio
async def test_conflicting_live_price_records_observation_without_forced_evidence_id(
    monkeypatch,
) -> None:
    async def fake_card(_state, _code):
        return {
            "name": "示例股份", "action": "观望", "confidence": 0.3,
            "decision_generated_at": "2026-07-15T14:00:00+08:00",
            "decision_market_evidence": [{
                "field": "last_price", "value": 10.2, "evidence_id": "conflict-price",
                "quality_status": "conflict",
            }],
            "deterministic_signal": {"reference_price": 10.2},
            "risk_decision": {"decision": "allow", "position_cap_pct": 0.0},
        }

    async def fake_record(_state, payload, **_kwargs):
        assert payload["rec_price"] is None
        assert payload["price_evidence_id"] is None
        return {"ok": True, "state": "approved_observation"}

    monkeypatch.setattr(routes, "governed_diagnosis_card", fake_card)
    monkeypatch.setattr(routes, "_authorize_and_record_advice", fake_record)
    result = await routes._governed_and_record_diagnosis(SimpleNamespace(), "000001")

    assert result["advice_record"]["state"] == "approved_observation"


@pytest.mark.asyncio
async def test_unverified_defensive_recommendation_cannot_manufacture_win_rate(tmp_path: Path) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store, evidence_store=None, market_gateway=None)
    response = await routes.add_recommendation(Request(state, {
        "code": "000001", "rec_price": 100.0, "direction": "avoid",
        "rec_time": "2020-01-01 09:00:00", "verify_days": 0,
        "client_request_id": "unverified-avoid",
    }))

    assert response["ok"] is True
    rec = store.recommendations()[0]
    assert rec["score_eligible"] is False
    assert rec["rec_time"].startswith("2026-")
    store.save_rec_review(rec["rec_id"], {
        "review_time": "2026-07-13 15:30:00", "hit_status": "hit",
        "directional_return": 50.0,
    })
    result = recommend.stats(store.recommendations(), store.rec_reviews())
    assert result["sample"] == 0
    assert result["win_rate"] is None
    assert any("排除出胜率" in row for row in result["missing"])


@pytest.mark.asyncio
async def test_reference_price_evidence_must_match_server_point(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    # 证据新鲜度是本用例的前置条件，不应绑定为某个过期的日历日。
    observed_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    cn_tz = timezone(timedelta(hours=8))
    observed_cn = observed_at.astimezone(cn_tz)
    monkeypatch.setattr(routes, "now_cn", lambda: observed_cn)
    monkeypatch.setattr(routes, "trade_day_status", lambda _day: {"status": "open"})
    monkeypatch.setattr(routes, "prev_trade_day", lambda _day: observed_cn.date().isoformat())

    class EvidenceStore:
        @staticmethod
        def get_point(evidence_id: str):
            return {
                "evidence_id": evidence_id,
                "symbol": "000001.SZ", "field": "last_price", "value": 10.2,
                "source_id": "tdx_api_quote", "event_time": observed_at.isoformat(),
                "quality_status": "ok",
            }

    state = SimpleNamespace(store=store, evidence_store=EvidenceStore(), market_gateway=None)
    bound, complete = await routes._bind_reference_price_evidence(state, {
        "code": "000001", "rec_price": 10.2,
        "price_evidence_id": "ev-price",
    })
    assert complete is True
    assert bound["reference_price_evidence"]["evidence_id"] == "ev-price"
    assert bound["evidence_ids"] == ["ev-price"]

    with pytest.raises(ValueError, match="不一致"):
        await routes._bind_reference_price_evidence(state, {
            "code": "000001", "rec_price": 12.2,
            "price_evidence_id": "ev-price",
        })


@pytest.mark.asyncio
async def test_risk_evaluate_api_is_deterministic() -> None:
    payload = {
        "candidate": {
            "action": "buy", "symbol": "000001", "position_cap_pct": 4,
            "confidence": 0.6, "evidence_ids": ["ev"],
            "critical_evidence_complete": True, "sector": "银行",
        },
        "market": {"risk_level": 2},
        "portfolio": {"target_position_pct": 0, "sector_position_pct": 0},
        "liquidity": {"adequate": True},
        "event": {"major_event": False},
    }
    first = await routes.evaluate_risk(Request(SimpleNamespace(), payload))
    second = await routes.evaluate_risk(Request(SimpleNamespace(), payload))

    assert first == second
    assert first["risk_decision"]["decision"] == "allow"
    assert len(first["risk_decision"]["input_snapshot_hash"]) == 64


@pytest.mark.asyncio
async def test_server_gate_ignores_client_risk_context_and_existing_buy_becomes_add(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    store.save_holdings([
        {"code": "000001", "name": "平安银行", "cost": 10, "shares": 100,
         "industry": "银行", "weight_pct": 10},
    ])
    state = SimpleNamespace(store=store)

    async def risk_one(_store):
        return {"risk_level": 1, "command": {"blackswan": []}}

    monkeypatch.setattr(routes, "_pulse_locked", risk_one)
    monkeypatch.setattr(
        routes.tdx_api,
        "industry_for_codes",
        lambda codes: {
            code: {"industry": "银行", "source": "TDX relation"} for code in codes
        },
    )
    monkeypatch.setattr(routes.stock, "quick_quote", lambda _code: {"amount": 100_000_000})
    decision = await routes._server_risk_decision(state, {
        "code": "000001", "direction": "buy", "position_cap_pct": 8,
        "portfolio": {"target_position_pct": 0, "sector_position_pct": 0},
        "liquidity": {"adequate": True},
        "event": {"major_event": False},
    }, evidence_complete=True)

    assert decision["decision"] == "veto"
    assert decision["final_action"] == "avoid"
    assert "single_name_limit_reached" in decision["reason_codes"]


@pytest.mark.asyncio
async def test_server_gate_reads_lightweight_server_industry_and_liquidity(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store)

    async def risk_one(_store):
        return {"risk_level": 1, "command": {"blackswan": []}}

    monkeypatch.setattr(routes, "_pulse_locked", risk_one)
    monkeypatch.setattr(
        routes.tdx_api,
        "industry_for_codes",
        lambda codes: {
            code: {"industry": "银行", "source": "TDX relation"} for code in codes
        },
    )
    monkeypatch.setattr(routes.stock, "quick_quote", lambda _code: {"amount": 100_000_000})
    decision = await routes._server_risk_decision(state, {
        "code": "000001", "direction": "buy", "position_cap_pct": 4,
    }, evidence_complete=True)

    assert decision["decision"] == "allow"
    assert decision["final_action"] == "buy"


@pytest.mark.asyncio
async def test_server_gate_overrides_forged_industries_for_entire_portfolio(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    store.save_holdings([
        {
            "code": f"{i:06d}", "name": f"持仓{i}", "cost": 10, "shares": 100,
            "industry": f"伪造行业{i}", "weight_pct": 5,
        }
        for i in range(1, 11)
    ])
    state = SimpleNamespace(store=store)

    async def risk_one(_store):
        return {"risk_level": 1, "command": {"blackswan": []}}

    monkeypatch.setattr(routes, "_pulse_locked", risk_one)
    monkeypatch.setattr(
        routes.tdx_api,
        "industry_for_codes",
        lambda codes: {
            code: {"industry": "银行", "source": "TDX relation"} for code in codes
        },
    )
    monkeypatch.setattr(routes.stock, "quick_quote", lambda _code: {"amount": 100_000_000})
    decision = await routes._server_risk_decision(state, {
        "code": "000001", "direction": "add", "position_cap_pct": 8,
    }, evidence_complete=True)

    assert decision["decision"] != "allow"
    assert "sector_concentration_cap" in decision["reason_codes"]


@pytest.mark.asyncio
async def test_holdings_save_ignores_client_industry_and_persists_server_value(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store)
    monkeypatch.setattr(
        routes.tdx_api,
        "industry_for_codes",
        lambda codes: {
            code: {"industry": "银行", "source": "TDX relation"} for code in codes
        },
    )

    result = await routes.put_holdings(Request(state, {
        "holdings": [{
            "code": "000001", "name": "平安银行", "cost": 10, "shares": 100,
            "industry": "用户伪造行业", "weight_pct": 5,
        }],
    }))

    assert result["ok"] is True
    assert result["holdings"][0]["industry"] == "银行"
    assert store.holdings()[0]["industry"] == "银行"


@pytest.mark.asyncio
async def test_holdings_save_rejects_more_than_fifty_before_enrichment(
    monkeypatch, tmp_path: Path,
) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store)
    monkeypatch.setattr(
        routes.tdx_api,
        "industry_for_codes",
        lambda _codes: pytest.fail("超限请求不得触发上游行业核验"),
    )
    response = await routes.put_holdings(Request(state, {
        "holdings": [
            {"code": f"{i:06d}", "cost": 10, "shares": 100}
            for i in range(1, 52)
        ],
    }))

    assert response.status_code == 400
    assert "最多 50" in response_body(response)["error"]


@pytest.mark.asyncio
async def test_historic_price_point_cannot_be_bound_as_current_evidence(tmp_path: Path) -> None:
    store = Store(tmp_path)

    class EvidenceStore:
        @staticmethod
        def get_point(evidence_id: str):
            return {
                "evidence_id": evidence_id,
                "symbol": "000001.SZ", "field": "last_price", "value": 10.2,
                "source_id": "tencent_quote", "event_time": "2026-06-01T02:00:00Z",
                "quality_status": "ok",
            }

    state = SimpleNamespace(store=store, evidence_store=EvidenceStore(), market_gateway=None)
    with pytest.raises(ValueError, match="过期"):
        await routes._bind_reference_price_evidence(state, {
            "code": "000001", "rec_price": 10.2, "price_evidence_id": "old-price",
        })


@pytest.mark.asyncio
async def test_future_price_point_cannot_be_bound_as_current_evidence(tmp_path: Path) -> None:
    store = Store(tmp_path)

    class EvidenceStore:
        @staticmethod
        def get_point(evidence_id: str):
            return {
                "evidence_id": evidence_id,
                "symbol": "000001.SZ", "field": "last_price", "value": 10.2,
                "source_id": "tencent_quote", "event_time": "2099-01-01T00:00:00Z",
                "quality_status": "ok",
            }

    state = SimpleNamespace(store=store, evidence_store=EvidenceStore(), market_gateway=None)
    with pytest.raises(ValueError, match="过期"):
        await routes._bind_reference_price_evidence(state, {
            "code": "000001", "rec_price": 10.2, "price_evidence_id": "future-price",
        })


@pytest.mark.asyncio
async def test_wrong_trade_day_price_is_rejected_even_inside_96_hours(tmp_path: Path) -> None:
    store = Store(tmp_path)

    class EvidenceStore:
        @staticmethod
        def get_point(evidence_id: str):
            return {
                "evidence_id": evidence_id,
                "symbol": "000001.SZ", "field": "last_price", "value": 10.2,
                "source_id": "tencent_quote", "event_time": "2026-07-09T07:00:00Z",
                "quality_status": "ok",
            }

    state = SimpleNamespace(store=store, evidence_store=EvidenceStore(), market_gateway=None)
    with pytest.raises(ValueError, match="过期"):
        await routes._bind_reference_price_evidence(state, {
            "code": "000001", "rec_price": 10.2, "price_evidence_id": "wrong-day",
        })


@pytest.mark.asyncio
async def test_diagnosis_buy_is_overridden_by_final_risk_veto(monkeypatch, tmp_path: Path) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store, engine=object())

    async def fake_diagnose(_engine, _code, *, include_model=True):
        assert include_model is False
        return {
            "action": "买入", "conviction": "高", "entry_zone": "10.0", "stop": "9.5",
            "summary": "买入", "sector": "银行",
            "decision_market_evidence": [{"evidence_id": "ev-price"}],
            "decision_market_evidence_quality": {"status": "ok"},
            "evidence_validation": {"verified": 1},
            "price_level_validation": {"status": "passed"},
        }

    async def risk_five(_store):
        return {"risk_level": 5, "command": {"blackswan": []}}

    monkeypatch.setattr(routes.diagnosis_mod, "diagnose", fake_diagnose)
    monkeypatch.setattr(routes, "_pulse_locked", risk_five)

    result = await routes.diagnose("000001", Request(state))

    assert result["ok"] is True
    # Phase 4: 模型的「买入/高」只是 hypothesis；该 fixture 没有规则层
    # 必需的完整市场字段，所以确定性结论是观望，不再借模型动作触发闸门。
    assert result["action"] == "观望"
    assert result["model_hypothesis"]["action"] == "买入"
    assert result["entry_zone"] is None and result["stop"] is None
    assert result["decision_authority"] == "deterministic_signal+risk_gate"
    assert result["risk_decision"]["decision"] == "allow"


@pytest.mark.asyncio
async def test_diagnosis_not_found_returns_404_without_advice_action(monkeypatch) -> None:
    async def fake_diagnose(_engine, _code, *, include_model=True, analysis_depth="fast"):
        assert include_model is False
        assert analysis_depth == "deep"
        return {
            "ok": False,
            "code": "999999",
            "recognized": False,
            "error_code": "stock_not_found",
            "error": "该代码不在当前完整 A 股名录中",
            "action": None,
            "analysis_mode": "identity_rejected",
        }

    monkeypatch.setattr(routes.diagnosis_mod, "diagnose", fake_diagnose)
    state = SimpleNamespace(
        engine=object(),
        store=SimpleNamespace(holdings=lambda: []),
    )

    response = await routes.diagnose("999999", Request(state))
    body = response_body(response)

    assert response.status_code == 404
    assert body["ok"] is False
    assert body["recognized"] is False
    assert body["error_code"] == "stock_not_found"
    assert body["action"] is None
