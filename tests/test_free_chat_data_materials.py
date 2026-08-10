from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse

from hermes_trader.analysis import chat_materials
from hermes_trader.analysis import engine as engine_mod
from hermes_trader.analysis import diagnosis as diagnosis_mod
from hermes_trader.api import routes
from hermes_trader.knowledge.store import Store


class _Request:
    def __init__(self, body: dict, *, state=None, mode: str = ""):
        self.app = SimpleNamespace(state=state or SimpleNamespace(engine=object(), store=object()))
        self._body = body
        self.query_params = {"mode": mode} if mode else {}

    async def json(self):
        return self._body


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("今天更适合进攻还是防守？", ("market_context", "sector")),
        ("为什么上涨？", ("technical", "fund_flow", "sector")),
        ("估值、业绩和长期基本面如何？", ("fundamental",)),
        ("公告有没有利好消息？", ("events",)),
    ],
)
def test_material_preselection_is_stable_and_capped(question, expected) -> None:
    selected = chat_materials.preselect_material_ids(question)
    assert selected == expected
    assert len(selected) <= 3


def test_material_selection_is_allowlisted_deduplicated_and_capped() -> None:
    assert chat_materials.resolve_material_ids(
        ["sector", "technical", "sector"], message="ignored"
    ) == ("sector", "technical")
    assert chat_materials.resolve_material_ids([], message="为什么上涨") == ()
    with pytest.raises(chat_materials.DataMaterialSelectionError, match="最多选择 4"):
        chat_materials.resolve_material_ids([
            "technical", "fund_flow", "sector", "market_context", "events",
        ])
    with pytest.raises(chat_materials.DataMaterialSelectionError, match="未知数据材料"):
        chat_materials.resolve_material_ids(["raw_minute_fields"])


@pytest.mark.asyncio
async def test_free_context_only_calls_selected_optional_collectors(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        engine_mod.market, "market_overview",
        lambda: calls.append("overview") or {"data_date": "2026-08-08", "indexes": []},
    )
    monkeypatch.setattr(
        engine_mod.market, "market_sectors",
        lambda: calls.append("sector") or {"sectors": [], "source": "test"},
    )
    monkeypatch.setattr(
        engine_mod.market, "market_flow",
        lambda: calls.append("fund_flow") or {"main_flow_sectors": []},
    )
    engine = engine_mod.Engine.__new__(engine_mod.Engine)
    engine.store = SimpleNamespace()
    report = chat_materials.empty_report(("sector",))

    context = await engine._free_chat_material_context(("sector",), report)

    assert set(calls) == {"overview", "sector"}
    assert "板块与题材" in context
    assert "个股资金流" not in context
    assert next(row for row in report["optional"] if row["id"] == "fund_flow")["state"] == "not_selected"


@pytest.mark.asyncio
async def test_generic_fund_flow_is_explicitly_degraded_to_market_scope(monkeypatch) -> None:
    monkeypatch.setattr(engine_mod.market, "market_overview", lambda: {"indexes": []})
    monkeypatch.setattr(
        engine_mod.market, "market_flow",
        lambda: {"main_flow_sectors": [{"name": "银行", "main_net": 1}]},
    )
    engine = engine_mod.Engine.__new__(engine_mod.Engine)
    engine.store = SimpleNamespace()
    report = chat_materials.empty_report(("fund_flow",))

    context = await engine._free_chat_material_context(("fund_flow",), report)

    flow_result = next(row for row in report["optional"] if row["id"] == "fund_flow")
    assert flow_result["state"] == "degraded"
    assert "不作为个股资金流" in flow_result["reason"]
    assert context["个股资金流"]["scope"] == "market_fallback_no_single_stock"


@pytest.mark.asyncio
async def test_chat_rejects_more_than_four_materials_before_collection() -> None:
    response = await routes.chat(_Request({
        "mode": "free",
        "message": "测试",
        "data_material_ids": [
            "technical", "fund_flow", "sector", "market_context", "events",
        ],
    }))
    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    assert response.body and b"invalid_data_material_selection" in response.body


@pytest.mark.asyncio
async def test_governed_stock_context_does_not_collect_unselected_enrichment(monkeypatch) -> None:
    calls: list[str] = []
    daily = [
        {
            "date": f"2026-07-{day:02d}", "open": 10 + day / 100,
            "high": 10.2 + day / 100, "low": 9.8 + day / 100,
            "close": 10.1 + day / 100, "volume": 100000 + day,
        }
        for day in range(1, 21)
    ]
    monkeypatch.setattr(
        diagnosis_mod.tx, "cached_stock_identity",
        lambda _code: {"known": True, "universe_complete": True, "name": "测试股"},
    )
    monkeypatch.setattr(
        diagnosis_mod.stock, "stock_core",
        lambda _code: calls.append("core") or {
            "recognized": True,
            "quote": {"code": "600519", "name": "测试股", "price": 10.3},
            "recent_daily": daily,
        },
    )
    monkeypatch.setattr(
        diagnosis_mod.stock, "stock_risk_daily",
        lambda _code: calls.append("risk_daily") or daily,
    )
    monkeypatch.setattr(
        diagnosis_mod.stock, "stock_flow_material",
        lambda _code: calls.append("fund_flow") or {
            "trade_date": "20260808", "main_net": 1, "source": "test",
        },
    )
    monkeypatch.setattr(
        diagnosis_mod.stock, "stock_charts",
        lambda _code: calls.append("technical") or {},
    )
    monkeypatch.setattr(
        diagnosis_mod.stock, "stock_board_material",
        lambda _code: calls.append("sector") or {},
    )
    monkeypatch.setattr(
        diagnosis_mod.stock, "stock_financial_material",
        lambda _code: calls.append("fundamental") or {},
    )

    async def evidence(_engine, _code, timeout):
        calls.append("evidence")
        return [], {}

    monkeypatch.setattr(diagnosis_mod, "_market_evidence_with_budget", evidence)
    engine = SimpleNamespace(config=SimpleNamespace(model_connection_custom=False))

    card = await diagnosis_mod._deterministic_deep_context(
        engine, "600519", data_material_ids=("fund_flow",)
    )

    assert set(calls) == {"core", "risk_daily", "fund_flow", "evidence"}
    assert card["material_payloads"]["fund_flow"]["main_net"] == 1
    assert card["material_payloads"]["technical"] is None


@pytest.mark.asyncio
async def test_conversation_history_filters_mode_and_persists_material_report(tmp_path) -> None:
    store = Store(tmp_path)
    free = store.create_conversation("free")
    store.create_conversation("stock")
    report = chat_materials.empty_report(("sector",))
    chat_materials.update_result(
        report, "sector", state="complete", source="test", data_date="2026-08-10"
    )
    chat_materials.finalize_report(report, model_used=True)
    state = SimpleNamespace(store=store, engine=object())

    added = await routes.chat_conversation_add(free["id"], _Request({
        "messages": [{
            "role": "assistant", "content": "完整回答", "data_materials": report,
        }],
    }, state=state))
    listing = await routes.chat_conversations(_Request({}, state=state, mode="free"))
    saved = store.get_conversation(free["id"])["messages"]

    assert added["ok"] is True
    assert [row["mode"] for row in listing["conversations"]] == ["free"]
    assert saved[0]["data_materials"]["selected_ids"] == ["sector"]
    assert saved[0]["data_materials"]["optional"][2]["source"] == "test"
