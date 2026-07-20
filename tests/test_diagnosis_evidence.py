from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

from hermes_trader.analysis import diagnosis
from hermes_trader.api.routes import _deterministic_diagnosis_proposal
from hermes_trader.data.gateway import DataPoint


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def get(self, symbol: str, field: str, *, session: str = "intraday") -> DataPoint:
        self.calls.append((symbol, field, session))
        missing = field == "pe"
        values = {
            "last_price": 10.2,
            "prev_close": 10.0,
            "change_pct": 2.0,
            "amount": 123_000_000.0,
            "turnover_rate": 2.3,
            "volume_ratio": 1.2,
            "pb": 1.5,
        }
        return DataPoint(
            symbol=symbol,
            market="CN-A",
            field=field,
            value=None if missing else values[field],
            unit="ratio" if field in {"pe", "pb", "volume_ratio"} else "CNY",
            source_id="primary",
            source_tier=2,
            source_url="https://example.test/quote",
            event_time="2026-07-13T02:00:05.000000Z",
            available_at="2026-07-13T02:00:06.000000Z",
            fetched_at="2026-07-13T02:00:06.000000Z",
            trading_day="2026-07-13",
            freshness="missing" if missing else "live",
            fallback_level=2 if missing else 0,
            quality_status="missing" if missing else "ok",
            raw_payload_hash=None,
            route_version="runtime-quote-v1",
            evidence_id=None if missing else f"evidence-{field}",
            missing_reason="all configured sources unavailable" if missing else None,
        )


class FakeHermes:
    def __init__(self, *, fail: bool) -> None:
        self.fail = fail
        self.chat_calls = 0

    async def chat(self, *_args, **_kwargs) -> str:
        self.chat_calls += 1
        if self.fail:
            raise RuntimeError("https://internal.example/v1?api_key=do-not-leak")
        return json.dumps(
            {
                "code": "000001",
                "name": "平安银行",
                "action": "持有",
                "conviction": "中",
                "entry_zone": "10.00-10.20",
                "stop": "跌破9.70",
                "evidence": [
                    {"finding": "现价10.20", "direction": "中性", "source": "实时盘口"},
                    {"finding": "量比1.2", "direction": "中性", "source": "实时盘口"},
                ],
                "summary": "依据真实数据持有观察",
                "missing": [],
            },
            ensure_ascii=False,
        )


class NeverCalledHermes:
    def __init__(self) -> None:
        self.chat_calls = 0
        self.connection_checks = 0

    async def check_connection(self) -> bool:
        self.connection_checks += 1
        raise AssertionError("unconfigured model must not be probed")

    async def chat(self, *_args, **_kwargs) -> str:
        self.chat_calls += 1
        raise AssertionError("unconfigured model must not be called")


class SlowUnavailableHermes:
    def __init__(self) -> None:
        self.chat_calls = 0
        self.connection_checks = 0

    async def check_connection(self) -> bool:
        self.connection_checks += 1
        await asyncio.sleep(10)
        return True

    async def chat(self, *_args, **_kwargs) -> str:
        self.chat_calls += 1
        raise AssertionError("unavailable model must not be called")


@pytest.mark.asyncio
async def test_fast_diagnosis_does_not_probe_or_call_model() -> None:
    gateway = FakeGateway()
    hermes = NeverCalledHermes()
    engine = SimpleNamespace(
        market_gateway=gateway,
        store=SimpleNamespace(knowledge_digest=lambda: ""),
        hermes=hermes,
        config=SimpleNamespace(
            model_connection_custom=True,
            model_provider="custom",
            hermes_fast_model="configured-model",
        ),
    )

    card = await diagnosis.diagnose(engine, "000001", include_model=False)

    assert hermes.connection_checks == 0
    assert hermes.chat_calls == 0
    assert card["analysis_mode"] == "deterministic_market"
    assert card["model_connection"] == {
        "configured": True,
        "status": "not_requested",
        "used_for_diagnosis": False,
        "fallback": None,
    }
    assert card["model_explanation_status"] == "not_requested"
    assert not any("模型" in item for item in card.get("missing") or [])
    assert card["market_quote"]["price"] == 10.2
    assert card["market_quote"]["prev_close"] == 10.0
    assert card["market_evidence_quality"]["captured"] == 7


@pytest.fixture(autouse=True)
def clear_diagnosis_cache():
    diagnosis._DIAG_CACHE.clear()
    yield
    diagnosis._DIAG_CACHE.clear()


def test_deep_levels_keep_support_below_and_resistance_above_price() -> None:
    daily = []
    for index in range(120):
        if index < 60:
            close, high, low = 100.0, 102.0, 98.0
        elif index < 100:
            close, high, low = 120.0, 122.0, 118.0
        else:
            close, high, low = 90.0, 105.0, 80.0
        daily.append({
            "d": f"2026-01-{index + 1:03d}",
            "c": close,
            "h": high,
            "l": low,
            "v": 1_000_000.0,
        })

    result = diagnosis._deep_technical_analysis(
        {"price": 100.0}, daily, None, None
    )

    assert result["levels"]["support"] <= 100.0
    assert result["levels"]["resistance"] >= 100.0
    assert result["levels"]["support"] != result["levels"]["resistance"]


@pytest.mark.asyncio
async def test_deep_diagnosis_rejects_non_stock_before_generating_action(monkeypatch) -> None:
    monkeypatch.setattr(
        diagnosis.tx,
        "cached_stock_identity",
        lambda _code: {
            "code": "999999", "known": False, "name": "",
            "universe_complete": True, "universe_as_of": "2026-07-17",
        },
    )
    monkeypatch.setattr(
        diagnosis.stock,
        "stock_core",
        lambda _code: pytest.fail("身份已确认不存在时不得再占用行情线程池"),
    )
    monkeypatch.setattr(
        diagnosis.stock,
        "stock_charts",
        lambda _code: pytest.fail("身份已确认不存在时不得请求图表"),
    )
    monkeypatch.setattr(
        diagnosis.stock,
        "stock_enrichment",
        lambda _code: pytest.fail("身份已确认不存在时不得请求增强源"),
    )
    engine = SimpleNamespace(
        market_gateway=FakeGateway(),
        config=SimpleNamespace(model_connection_custom=False),
    )

    card = await diagnosis.diagnose(
        engine, "999999", include_model=False, analysis_depth="deep"
    )

    assert card["ok"] is False
    assert card["recognized"] is False
    assert card["error_code"] == "stock_not_found"
    assert card["action"] is None
    assert card["analysis_status"] == "not_found"
    assert card["market_quote"] == {}
    assert card["market_evidence"] == []


@pytest.mark.asyncio
async def test_diagnosis_capacity_waits_for_healthy_previous_request(monkeypatch) -> None:
    monkeypatch.setattr(
        diagnosis, "_DIAGNOSIS_DATA_SLOTS", threading.BoundedSemaphore(1)
    )
    monkeypatch.setattr(diagnosis, "_DIAGNOSIS_DATA_SLOT_WAIT", 0.5)

    def slow(value: str) -> str:
        time.sleep(0.12)
        return value

    first = asyncio.create_task(
        asyncio.to_thread(diagnosis._guarded_blocking_call, slow, "first")
    )
    await asyncio.sleep(0.02)
    second = asyncio.create_task(
        asyncio.to_thread(diagnosis._guarded_blocking_call, slow, "second")
    )

    assert await asyncio.gather(first, second) == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ai_fails", [False, True], ids=["ai-success", "ai-fallback"])
async def test_diagnosis_always_exposes_real_market_evidence_without_zero_fill(
    monkeypatch, ai_fails: bool,
) -> None:
    gateway = FakeGateway()
    detail = {
        "ok": True,
        "ts": "2026-07-13 10:00:06",
        "quote": {"name": "平安银行", "price": 10.2},
        "minute": [],
        "daily": [],
        "board": {},
        "missing": ["资金流"],
    }
    monkeypatch.setattr(diagnosis.stock, "stock_detail", lambda _code: dict(detail))
    monkeypatch.setattr(diagnosis.tx, "fina_indicator", lambda _code: None)
    monkeypatch.setattr(diagnosis, "load_methodology", lambda: "")
    engine = SimpleNamespace(
        market_gateway=gateway,
        store=SimpleNamespace(knowledge_digest=lambda: ""),
        hermes=FakeHermes(fail=ai_fails),
        config=SimpleNamespace(hermes_fast_model="test-model"),
    )

    card = await diagnosis.diagnose(engine, "000001")

    assert {row["field"] for row in card["market_evidence"]} == {
        "last_price", "prev_close", "change_pct", "amount",
        "turnover_rate", "volume_ratio", "pb",
    }
    assert all(row["value"] is not None for row in card["market_evidence"])
    assert all(row["value"] != 0 for row in card["market_evidence"])
    assert not any(row["field"] == "pe" for row in card["market_evidence"])
    assert card["market_evidence_quality"] == {
        "status": "degraded",
        "captured": 7,
        "requested": 8,
        "missing_fields": ["pe"],
    }
    assert card["decision_market_evidence"] == card["market_evidence"]
    assert card["decision_market_evidence_quality"] == card["market_evidence_quality"]
    assert card["decision_generated_at"]
    assert {field for _symbol, field, _session in gateway.calls} == set(
        diagnosis._MARKET_EVIDENCE_FIELDS
    )

    if ai_fails:
        assert card["action"] == "观望"
        assert card["conviction"] == "低"
        assert any("AI网关调用失败" in item for item in card["missing"])
        assert "do-not-leak" not in repr(card)
        assert "internal.example" not in repr(card)
    else:
        assert card["action"] == "持有"
        assert card["conviction"] == "中"
        assert card["evidence_validation"]["status"] == "passed"
        assert all(item["rule_version"] == "diagnosis-evidence-v2" for item in card["evidence"])
        assert {item["evidence_ref"] for item in card["evidence"]} == {
            "evidence:evidence-last_price", "evidence:evidence-volume_ratio",
        }


@pytest.mark.asyncio
async def test_cached_slow_diagnosis_refreshes_fast_market_evidence(monkeypatch) -> None:
    gateway = FakeGateway()
    detail = {
        "ok": True,
        "ts": "2026-07-13 10:00:06",
        "quote": {"name": "平安银行", "price": 10.2},
        "minute": [],
        "daily": [],
        "board": {},
        "missing": [],
    }
    monkeypatch.setattr(diagnosis.stock, "stock_detail", lambda _code: dict(detail))
    monkeypatch.setattr(diagnosis.tx, "fina_indicator", lambda _code: None)
    monkeypatch.setattr(diagnosis, "load_methodology", lambda: "")
    hermes = FakeHermes(fail=False)
    engine = SimpleNamespace(
        market_gateway=gateway,
        store=SimpleNamespace(knowledge_digest=lambda: ""),
        hermes=hermes,
        config=SimpleNamespace(hermes_fast_model="test-model"),
    )

    first = await diagnosis.diagnose(engine, "000001")
    first_call_count = len(gateway.calls)
    second = await diagnosis.diagnose(engine, "000001")

    assert first["summary"] == second["summary"]
    assert len(gateway.calls) == first_call_count + len(diagnosis._MARKET_EVIDENCE_FIELDS)
    assert second["decision_generated_at"] == first["decision_generated_at"]


@pytest.mark.asyncio
async def test_unconfigured_model_returns_market_safety_card_without_network_wait(
    monkeypatch,
) -> None:
    gateway = FakeGateway()
    detail = {
        "ok": True,
        "ts": "2026-07-13 10:00:06",
        "quote": {"name": "平安银行", "price": 10.2},
        "minute": [],
        "daily": [],
        "board": {},
        "missing": [],
    }
    stock_detail_calls = 0

    def blocking_stock_detail(_code):
        nonlocal stock_detail_calls
        stock_detail_calls += 1
        time.sleep(1)
        return dict(detail)

    monkeypatch.setattr(diagnosis.stock, "stock_detail", blocking_stock_detail)
    monkeypatch.setattr(
        diagnosis.stock,
        "quick_quote",
        lambda _code: {"code": "000001", "name": "平安银行", "price": 10.2},
    )
    hermes = NeverCalledHermes()
    engine = SimpleNamespace(
        market_gateway=gateway,
        store=SimpleNamespace(knowledge_digest=lambda: ""),
        hermes=hermes,
        config=SimpleNamespace(
            model_connection_custom=False,
            hermes_fast_model="unused-default-hermes-model",
        ),
    )

    started = time.monotonic()
    card = await diagnosis.diagnose(engine, "000001")
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert stock_detail_calls == 0
    assert hermes.connection_checks == 0
    assert hermes.chat_calls == 0
    assert card["action"] == "观望"
    assert card["conviction"] == "低"
    assert card["analysis_mode"] == "deterministic_market_fallback"
    assert card["model_connection"] == {
        "configured": False,
        "status": "not_configured",
        "used_for_diagnosis": False,
        "fallback": "deterministic_market",
    }
    assert "模型连接未配置，已使用确定性/行情安全降级" in card["summary"]
    assert card["market_evidence"]


def test_model_preflight_budget_does_not_reject_normal_public_tls_latency() -> None:
    # HermesClient 的 /models 网络上限为 8 秒；诊断/问股预检不能先用
    # 1 秒把已在设置页验证通过的公网模型误判成未连接。
    assert diagnosis._MODEL_CONNECTION_PREFLIGHT_TIMEOUT >= 8.0


@pytest.mark.asyncio
async def test_unavailable_configured_model_has_bounded_preflight_and_no_chat(
    monkeypatch,
) -> None:
    gateway = FakeGateway()
    detail = {
        "ok": True,
        "ts": "2026-07-13 10:00:06",
        "quote": {"name": "平安银行", "price": 10.2},
        "minute": [],
        "daily": [],
        "board": {},
        "missing": [],
    }
    stock_detail_calls = 0

    def blocking_stock_detail(_code):
        nonlocal stock_detail_calls
        stock_detail_calls += 1
        time.sleep(1)
        return dict(detail)

    monkeypatch.setattr(diagnosis.stock, "stock_detail", blocking_stock_detail)
    monkeypatch.setattr(
        diagnosis.stock,
        "quick_quote",
        lambda _code: {"code": "000001", "name": "平安银行", "price": 10.2},
    )
    monkeypatch.setattr(diagnosis, "_MODEL_CONNECTION_PREFLIGHT_TIMEOUT", 0.01)
    hermes = SlowUnavailableHermes()
    engine = SimpleNamespace(
        market_gateway=gateway,
        store=SimpleNamespace(knowledge_digest=lambda: ""),
        hermes=hermes,
        config=SimpleNamespace(
            model_connection_custom=True,
            model_provider="custom",
            hermes_fast_model="configured-but-unavailable",
        ),
    )

    started = time.monotonic()
    card = await diagnosis.diagnose(engine, "000001")
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert stock_detail_calls == 0
    assert hermes.connection_checks == 1
    assert hermes.chat_calls == 0
    assert card["action"] == "观望"
    assert card["model_connection"]["configured"] is True
    assert card["model_connection"]["status"] == "unavailable"
    assert "当前模型连接不可用，已使用确定性/行情安全降级" in card["summary"]


@pytest.mark.asyncio
async def test_configured_model_full_stock_detail_has_hard_collection_budget(
    monkeypatch,
) -> None:
    gateway = FakeGateway()

    def blocking_stock_detail(_code):
        time.sleep(0.5)
        return {
            "ok": True,
            "quote": {"name": "迟到数据", "price": 10.2},
            "missing": [],
        }

    monkeypatch.setattr(diagnosis.stock, "stock_detail", blocking_stock_detail)
    monkeypatch.setattr(diagnosis, "_FULL_STOCK_DETAIL_TIMEOUT", 0.01)
    hermes = FakeHermes(fail=False)

    async def connected() -> bool:
        return True

    hermes.check_connection = connected
    engine = SimpleNamespace(
        market_gateway=gateway,
        store=SimpleNamespace(knowledge_digest=lambda: ""),
        hermes=hermes,
        config=SimpleNamespace(
            model_connection_custom=True,
            model_provider="custom",
            hermes_fast_model="configured-model",
        ),
    )

    started = time.monotonic()
    card = await diagnosis.diagnose(engine, "000001")
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert hermes.chat_calls == 0
    assert card["action"] == "观望"
    assert card["analysis_mode"] == "market_data_fallback"
    assert card["model_connection"]["status"] == "available"
    assert card["model_connection"]["used_for_diagnosis"] is False
    assert "完整个股数据采集超时，已使用快行情安全降级" in card["summary"]


def test_deterministic_governance_keeps_model_fallback_in_watch_mode() -> None:
    card = {
        "analysis_mode": "deterministic_market_fallback",
        "degraded_reason": "模型连接未配置，已使用确定性/行情安全降级",
        "decision_market_evidence": [
            {"field": "last_price", "value": 10.2},
            {"field": "prev_close", "value": 10.0},
            {"field": "change_pct", "value": 2.0},
            {"field": "amount", "value": 100_000_000},
            {"field": "volume_ratio", "value": 1.2},
            {"field": "turnover_rate", "value": 3.0},
        ],
        "decision_market_evidence_quality": {
            "status": "ok", "captured": 6, "requested": 6,
        },
    }

    proposal = _deterministic_diagnosis_proposal(card, has_holding=False)

    assert proposal["complete"] is True
    assert proposal["signal_score"] >= 4
    assert proposal["action"] == "观望"
    assert proposal["conviction"] == "低"
    assert proposal["confidence"] <= 0.4
    assert proposal["reasons"][0] == card["degraded_reason"]


def test_deterministic_governance_reconciles_only_derived_change_conflict() -> None:
    rows = [
        {"field": "last_price", "value": 10.2, "quality_status": "ok"},
        {"field": "prev_close", "value": 10.0, "quality_status": "ok"},
        {"field": "change_pct", "value": 2.0, "quality_status": "conflict"},
        {"field": "amount", "value": 100_000_000, "quality_status": "ok"},
        {"field": "volume_ratio", "value": 1.2, "quality_status": "ok"},
        {"field": "turnover_rate", "value": 3.0, "quality_status": "ok"},
    ]
    proposal = _deterministic_diagnosis_proposal({
        "analysis_mode": "deterministic_market",
        "decision_market_evidence": rows,
        "decision_market_evidence_quality": {
            "status": "conflict", "captured": 6, "requested": 6,
        },
    }, has_holding=False)

    assert proposal["complete"] is True
    assert proposal["change_reconciled"] is True
    assert proposal["conflict_fields"] == ["change_pct"]

    rows[0]["quality_status"] = "conflict"
    unsafe = _deterministic_diagnosis_proposal({
        "analysis_mode": "deterministic_market",
        "decision_market_evidence": rows,
        "decision_market_evidence_quality": {
            "status": "conflict", "captured": 6, "requested": 6,
        },
    }, has_holding=False)

    assert unsafe["complete"] is False
    assert unsafe["change_reconciled"] is False
    assert unsafe["action"] == "观望"


@pytest.mark.asyncio
async def test_configured_direct_model_still_uses_model_path(monkeypatch) -> None:
    gateway = FakeGateway()
    detail = {
        "ok": True,
        "ts": "2026-07-13 10:00:06",
        "quote": {"name": "平安银行", "price": 10.2},
        "minute": [],
        "daily": [],
        "board": {},
        "missing": [],
    }
    monkeypatch.setattr(diagnosis.stock, "stock_detail", lambda _code: dict(detail))
    monkeypatch.setattr(diagnosis.tx, "fina_indicator", lambda _code: None)
    monkeypatch.setattr(diagnosis, "load_methodology", lambda: "")
    hermes = FakeHermes(fail=False)

    async def connected() -> bool:
        return True

    hermes.check_connection = connected
    engine = SimpleNamespace(
        market_gateway=gateway,
        store=SimpleNamespace(knowledge_digest=lambda: ""),
        hermes=hermes,
        config=SimpleNamespace(
            model_connection_custom=True,
            model_provider="ollama",
            hermes_fast_model="local-model",
        ),
    )

    card = await diagnosis.diagnose(engine, "000001")

    assert hermes.chat_calls == 1
    assert card["analysis_mode"] == "model_enhanced"
    assert card["model_connection"]["status"] == "available"
    assert card["model_connection"]["used_for_diagnosis"] is True


def test_evidence_gate_blocks_buy_and_price_levels_when_critical_truth_is_missing() -> None:
    gated = diagnosis._apply_evidence_gate(
        {
            "action": "买入", "conviction": "高", "entry_zone": "10.0-10.2",
            "stop": "9.7", "summary": "强烈买入，立即建仓", "missing": [],
        },
        {
            "status": "degraded", "captured": 5, "requested": 8,
            "missing_fields": ["last_price", "pe", "pb"],
        },
    )

    assert gated["action"] == "观望"
    assert gated["conviction"] == "低"
    assert gated["entry_zone"] is None and gated["stop"] is None
    assert gated["original_action"] == "买入"
    assert gated["gate_applied"] is True
    assert not any(word in gated["summary"] for word in ("买入", "加仓", "建仓"))
    assert any("禁止新开" in item for item in gated["missing"])


def test_price_level_validator_rejects_hallucinated_levels_and_buy_action() -> None:
    card = diagnosis._validate_price_levels(
        {
            "action": "买入", "conviction": "高",
            "entry_zone": "999999-1000000", "stop": "跌破1元",
            "summary": "马上建仓", "missing": [],
        },
        {
            "price": 10.2, "prev_close": 10.0, "high": 10.3, "low": 9.9,
            "high_52w": 12.0, "low_52w": 8.0,
        },
        [
            {"d": "2026-07-09", "o": 10.0, "c": 10.1, "h": 10.2, "l": 9.8},
            {"d": "2026-07-10", "o": 10.1, "c": 10.2, "h": 10.3, "l": 10.0},
        ],
    )

    assert card["price_level_validation"]["status"] == "rejected"
    assert set(card["price_level_validation"]["rejected_fields"]) == {"entry_zone", "stop"}
    assert card["entry_zone"] is None and card["stop"] is None
    assert card["action"] == "观望" and card["conviction"] == "低"
    assert card["original_action"] == "买入"


def test_price_level_validator_accepts_only_traceable_quote_and_risk_bases() -> None:
    card = diagnosis._validate_price_levels(
        {
            "action": "持有", "conviction": "中",
            "entry_zone": "10.00-10.20", "stop": "跌破9.70",
            "summary": "持有观察", "missing": [],
        },
        {"price": 10.2, "prev_close": 10.0, "high": 10.3, "low": 9.9},
        [],
        market_evidence=[
            {"field": "last_price", "value": 10.2, "evidence_id": "evidence-last"},
            {"field": "prev_close", "value": 10.0, "evidence_id": "evidence-prev"},
        ],
    )

    validation = card["price_level_validation"]
    assert validation["status"] == "passed"
    assert card["entry_zone"] == "10.00-10.20"
    assert card["stop"] == "跌破9.70"
    assert set(validation["evidence_refs"]) == {"evidence-last", "evidence-prev"}
    assert validation["supported_fields"]["stop"][0]["basis"] == "现价下移5%"


def test_price_level_validator_does_not_parse_moving_average_period_as_price() -> None:
    daily = [
        {"d": f"2026-06-{day:02d}", "c": 10.0, "h": 10.2, "l": 9.8}
        for day in range(1, 11)
    ]
    card = diagnosis._validate_price_levels(
        {
            "action": "持有", "conviction": "中",
            "entry_zone": None, "stop": "跌破5日线后离场",
            "summary": "持有观察", "missing": [],
        },
        {"price": 10.2},
        daily,
    )

    assert card["price_level_validation"]["status"] == "passed"
    match = card["price_level_validation"]["supported_fields"]["stop"][0]
    assert match["text_value"] == "5日线"
    assert match["basis"] == "MA5"


def test_price_level_validator_rejects_arbitrary_number_inside_old_wide_range() -> None:
    card = diagnosis._validate_price_levels(
        {
            "action": "买入", "conviction": "高",
            "entry_zone": "11.45", "stop": None,
            "summary": "到价买入", "missing": [],
        },
        {
            "price": 10.2, "prev_close": 10.0, "high": 10.3, "low": 9.9,
            "high_52w": 12.0, "low_52w": 8.0,
        },
        [
            {"d": "2026-07-09", "o": 10.0, "c": 10.1, "h": 10.2, "l": 9.8},
            {"d": "2026-07-10", "o": 10.1, "c": 10.2, "h": 10.3, "l": 10.0},
        ],
    )

    assert card["price_level_validation"]["status"] == "rejected"
    assert card["entry_zone"] is None
    assert card["action"] == "观望"


def test_model_evidence_is_reformatted_from_server_facts_and_hallucination_is_dropped() -> None:
    card = diagnosis._validate_model_evidence(
        {
            "action": "买入", "conviction": "高",
            "entry_zone": "10.2", "stop": "9.7", "summary": "立即建仓", "missing": [],
            "evidence": [
                {"finding": "现价10.20元", "direction": "利多", "source": "实时盘口"},
                {"finding": "主力净流入999亿元", "direction": "利多", "source": "资金流"},
            ],
        },
        {
            "quote": {"price": 10.2},
            "flow": {"main_net": 123_000_000.0},
            "daily": [],
            "board": {"industry": "银行"},
        },
        None,
        [{
            "field": "last_price", "value": 10.2, "unit": "CNY",
            "source_id": "tencent_quote", "evidence_id": "evidence-last",
        }],
    )

    assert card["evidence_validation"] == {
        "rule_version": "diagnosis-evidence-v2",
        "verified": 1,
        "rejected": 1,
        "status": "partial",
    }
    assert card["evidence"] == [{
        "finding": "现价 10.20 元",
        "direction": "利多",
        "source": "tencent_quote",
        "evidence_ref": "evidence:evidence-last",
        "evidence_refs": ["evidence:evidence-last"],
        "derivation_rule": "server_fact_match",
        "rule_version": "diagnosis-evidence-v2",
    }]
    assert any("已丢弃" in reason for reason in card["missing"])


def test_unverified_model_evidence_blocks_new_risk_action() -> None:
    card = diagnosis._validate_model_evidence(
        {
            "action": "加仓", "conviction": "高",
            "entry_zone": "10.2", "stop": "9.7", "summary": "立即加仓", "missing": [],
            "evidence": [{"finding": "主力净流入999亿元", "direction": "利多", "source": "资金流"}],
        },
        {"quote": {"price": 10.2}, "flow": {"main_net": 123_000_000.0}},
        None,
        [],
    )

    assert card["action"] == "观望"
    assert card["original_action"] == "加仓"
    assert card["conviction"] == "低"
    assert card["entry_zone"] is None and card["stop"] is None
    assert card["evidence"] == []


def test_price_validator_never_attaches_mismatched_legacy_quote_to_gateway_evidence() -> None:
    card = diagnosis._validate_price_levels(
        {
            "action": "买入", "conviction": "高",
            "entry_zone": "12.24", "stop": "11.63",
            "summary": "买入", "missing": [],
        },
        {"price": 12.24, "prev_close": 12.0, "high": 12.3, "low": 11.9},
        [],
        market_evidence=[{
            "field": "last_price", "value": 10.20,
            "evidence_id": "ev-actual-10.20",
        }],
    )

    assert card["price_level_validation"]["status"] == "rejected"
    assert card["entry_zone"] is None and card["stop"] is None
    assert card["action"] == "观望"
    assert "ev-actual-10.20" not in card["price_level_validation"]["evidence_refs"]


@pytest.mark.parametrize("stop", ["10.20", "10.30", "12.24"])
def test_long_stop_must_be_strictly_below_authoritative_current_price(stop: str) -> None:
    card = diagnosis._validate_price_levels(
        {
            "action": "买入", "conviction": "高",
            "entry_zone": "10.20", "stop": stop,
            "summary": "买入", "missing": [],
        },
        {"price": 10.2, "high": 10.3, "high_52w": 12.24},
        [],
        market_evidence=[{
            "field": "last_price", "value": 10.20,
            "evidence_id": "ev-current",
        }],
    )

    assert card["price_level_validation"]["status"] == "rejected"
    assert card["stop"] is None
    assert card["action"] == "观望"


def test_long_entry_named_moving_average_cannot_hide_remote_chase_price() -> None:
    daily = [
        {"d": f"2026-05-{index + 1:02d}", "c": 12.24, "h": 12.30, "l": 12.10}
        for index in range(60)
    ]
    card = diagnosis._validate_price_levels(
        {
            "action": "买入", "conviction": "高",
            "entry_zone": "60日线", "stop": "9.69",
            "summary": "买入", "missing": [],
        },
        {"price": 10.2},
        daily,
        market_evidence=[{
            "field": "last_price", "value": 10.20,
            "evidence_id": "ev-current",
        }],
    )

    assert card["price_level_validation"]["status"] == "rejected"
    assert card["entry_zone"] is None
    assert card["action"] == "观望"
