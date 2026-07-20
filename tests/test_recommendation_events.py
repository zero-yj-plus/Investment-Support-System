"""统一推荐事件的离线测试。

只使用临时目录，不访问行情、模型或客户数据。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from hermes_trader.analysis.recommend import (
    REVIEW_WINDOWS,
    RecommendationEvent,
    RecommendationEventStore,
    event_store_for,
    recover_pending_recommendations,
    record_recommendation,
    verify_one,
)
from hermes_trader.analysis import recommend as recommend_module
from hermes_trader.api.routes import add_recommendation, get_recommendation_events
from hermes_trader.knowledge.store import Store


def _legacy_payload(**overrides) -> dict:
    payload = {
        "code": "1",
        "name": "平安银行",
        "rec_price": 12.34,
        "source": "问股",
        "rec_type": "watch",
        "buy_condition": "放量站稳 12.50 后观察",
    }
    payload.update(overrides)
    return payload


def test_event_schema_keeps_missing_evidence_explicit_instead_of_inventing_it() -> None:
    event = RecommendationEvent.from_input(_legacy_payload()).as_dict()

    assert event["source"] == "问股"
    assert event["symbol"] == "000001"
    assert event["entry_condition"] == "放量站稳 12.50 后观察"
    assert event["reference_price"] == 12.34
    assert event["reference_price_evidence"] == {
        "value": 12.34,
        "currency": None,
        "evidence_id": None,
        "source_id": None,
        "observed_at": None,
        "provenance_status": "unverified",
    }
    assert event["stop"] == {"price": None, "condition": None}
    assert event["target"] == {"prices": [], "condition": None}
    assert event["position_cap_pct"] is None
    assert event["regime"] is None
    assert event["confidence"] is None
    assert event["trade_day"] is None
    assert event["evidence_ids"] == []
    assert event["invalidation"] == []
    assert event["review_windows"] == [dict(window) for window in REVIEW_WINDOWS]


def test_event_schema_preserves_structured_decision_and_traceable_price_evidence() -> None:
    event = RecommendationEvent.from_input(_legacy_payload(
        trade_day="2026-07-13",
        direction="buy",
        stop=11.80,
        target=[12.90, 13.40],
        position_cap_pct=3,
        regime="range",
        confidence=0.68,
        confidence_scale="0_to_1",
        risk_decision={"decision": "PASS", "position_cap_pct": 3},
        evidence_ids=["quote:000001:20260713T093500", "factor:flow:42"],
        invalidation=["成交额低于阈值", "大盘转为危机状态"],
        reference_price_evidence={
            "evidence_id": "quote:000001:20260713T093500",
            "source_id": "tdx_snapshot",
            "observed_at": "2026-07-13T09:35:00+08:00",
            "currency": "CNY",
        },
    )).as_dict()

    assert event["trade_day"] == "2026-07-13"
    assert event["direction"] == "buy"
    assert event["stop"]["price"] == 11.8
    assert event["target"]["prices"] == [12.9, 13.4]
    assert event["position_cap_pct"] == 3.0
    assert event["regime"] == "range"
    assert event["confidence"] == 0.68
    assert event["confidence_scale"] == "0_to_1"
    assert event["risk_decision"] == {"decision": "PASS", "position_cap_pct": 3}
    assert event["reference_price_evidence"]["provenance_status"] == "traceable"
    assert event["reference_price_evidence"]["observed_at"] == "2026-07-13T09:35:00+08:00"


def test_jsonl_store_only_appends_and_keeps_previous_bytes() -> None:
    with TemporaryDirectory() as tmp:
        ledger = RecommendationEventStore(tmp)
        first = RecommendationEvent.from_input(_legacy_payload(code="000001"))
        second = RecommendationEvent.from_input(_legacy_payload(code="000002", name="万科A"))

        ledger.append(first)
        original_bytes = ledger.path.read_bytes()
        ledger.append(second)
        final_bytes = ledger.path.read_bytes()

        assert final_bytes.startswith(original_bytes)
        assert len(final_bytes) > len(original_bytes)
        lines = final_bytes.decode("utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["event_id"] == first.as_dict()["event_id"]
        assert [row["symbol"] for row in ledger.events()] == ["000001", "000002"]


def test_jsonl_append_is_idempotent_for_identical_event_id() -> None:
    with TemporaryDirectory() as tmp:
        ledger = RecommendationEventStore(tmp)
        event = RecommendationEvent.from_input(_legacy_payload(code="000001"))

        first = ledger.append(event)
        second = ledger.append(event)

        assert first == second
        assert len(ledger.events()) == 1


def test_unified_recording_keeps_legacy_store_compatible_and_links_ids() -> None:
    with TemporaryDirectory() as tmp:
        store = Store(Path(tmp))
        result = record_recommendation(store, _legacy_payload(
            direction="avoid",
            entry_condition="跌破 12.20 后回避",
            risk_decision={"decision": "REDUCE", "reason_codes": ["liquidity"]},
            evidence_ids=["quote:000001:1", "risk:gate:7"],
            stop_loss_condition="跌破 11.80",
        ))

        legacy = store.recommendations()
        events = event_store_for(store).events()

    assert len(legacy) == 1
    assert legacy[0]["rec_id"] == result["rec_id"]
    assert legacy[0]["code"] == "000001"
    assert legacy[0]["rec_price"] == 12.34
    # 旧字段仍可用，新决策字段也不再被 Store.add_rec 白名单静默丢弃。
    assert legacy[0]["rec_type"] == "watch"
    assert legacy[0]["buy_condition"] == "放量站稳 12.50 后观察"
    assert legacy[0]["direction"] == "avoid"
    assert legacy[0]["entry_condition"] == "跌破 12.20 后回避"
    assert legacy[0]["risk_decision"] == {
        "decision": "REDUCE", "reason_codes": ["liquidity"],
    }
    assert legacy[0]["evidence_ids"] == ["quote:000001:1", "risk:gate:7"]
    assert legacy[0]["event_id"] == result["event_id"]
    assert len(events) == 1
    assert events[0]["event_id"] == result["event_id"]
    assert events[0]["legacy_rec_id"] == result["rec_id"]
    assert events[0]["stop"]["condition"] == "跌破 11.80"


def test_new_symbol_and_reference_price_names_still_dual_write_to_legacy_shape() -> None:
    with TemporaryDirectory() as tmp:
        store = Store(Path(tmp))
        result = record_recommendation(store, {
            "symbol": "000001.SZ",
            "reference_price": 12.34,
            "direction": "watch",
        })

        legacy = store.recommendations()[0]
        event = event_store_for(store).events()[0]

    assert legacy["rec_id"] == result["rec_id"]
    assert legacy["code"] == "000001"
    assert legacy["rec_price"] == 12.34
    assert legacy["rec_type"] == "watch"
    assert legacy["direction"] == "watch"
    assert event["symbol"] == "000001.SZ"
    assert event["source"] == "generic_api"


def test_invalid_price_is_rejected_before_either_store_is_written() -> None:
    with TemporaryDirectory() as tmp:
        store = Store(Path(tmp))

        with pytest.raises(ValueError, match="推荐价应为正数"):
            record_recommendation(store, _legacy_payload(rec_price="unknown"))

        assert store.recommendations() == []
        assert event_store_for(store).events() == []


def test_dual_write_intent_recovers_after_event_append_failure(monkeypatch, tmp_path: Path) -> None:
    store = Store(tmp_path)
    original_append = RecommendationEventStore.append
    calls = 0

    def fail_once(self, event):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated ledger failure")
        return original_append(self, event)

    monkeypatch.setattr(RecommendationEventStore, "append", fail_once)
    payload = _legacy_payload(client_request_id="retry-safe-request-1")

    with pytest.raises(OSError, match="simulated"):
        record_recommendation(store, payload)

    # 旧表可能已物化，但 intent 必须保留，API 也没有返回成功。
    assert len(store.recommendations()) == 1
    assert event_store_for(store).events() == []
    assert len(list((tmp_path / "recommendation_events" / "pending").glob("*.json"))) == 1

    recovered = recover_pending_recommendations(store)
    assert recovered == {"recovered": 1, "failed": []}
    assert len(store.recommendations()) == 1
    assert len(event_store_for(store).events()) == 1
    assert list((tmp_path / "recommendation_events" / "pending").glob("*.json")) == []

    # 重放多次幂等，不重复任何一侧。
    assert recover_pending_recommendations(store) == {"recovered": 0, "failed": []}
    replay = record_recommendation(store, payload)
    assert replay["idempotent_replay"] is True
    assert len(store.recommendations()) == 1
    assert len(event_store_for(store).events()) == 1


def test_client_request_id_rejects_conflicting_reuse(tmp_path: Path) -> None:
    store = Store(tmp_path)
    record_recommendation(store, _legacy_payload(
        client_request_id="same-request", rec_price=10.0,
    ))

    with pytest.raises(ValueError, match="client_request_id"):
        record_recommendation(store, _legacy_payload(
            client_request_id="same-request", rec_price=11.0,
        ))

    assert len(store.recommendations()) == 1
    assert len(event_store_for(store).events()) == 1


class _Request:
    def __init__(self, store: Store, body: dict | None = None, query: dict | None = None):
        self.app = SimpleNamespace(state=SimpleNamespace(store=store))
        self._body = body or {}
        self.query_params = query or {}

    async def json(self) -> dict:
        return self._body


@pytest.mark.asyncio
async def test_legacy_watch_post_becomes_non_scoreable_proposal() -> None:
    with TemporaryDirectory() as tmp:
        store = Store(Path(tmp))
        response = await add_recommendation(_Request(store, _legacy_payload()))

        assert response["ok"] is True
        assert response["proposal_id"].startswith("ap_")
        assert response["recommendation_created"] is False
        assert store.recommendations() == []
        assert event_store_for(store).events() == []


@pytest.mark.asyncio
async def test_event_api_returns_append_only_records_without_backfilling_legacy_rows() -> None:
    with TemporaryDirectory() as tmp:
        store = Store(Path(tmp))
        # 模拟历史旧记录：没有来源证据时不会自动伪造 RecommendationEvent。
        store.add_rec(_legacy_payload(code="000001"))
        record_recommendation(store, _legacy_payload(code="000002", name="万科A"))

        response = await get_recommendation_events(_Request(store, query={"limit": "50"}))

    assert response["ok"] is True
    assert response["count"] == 1
    assert len(response["events"]) == 1
    assert response["events"][0]["symbol"] == "000002"


def test_t0_recommendation_only_matures_after_market_close(monkeypatch) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    monkeypatch.setattr(recommend_module, "trade_dates", lambda: {"2026-07-13"})
    rec = {
        "rec_time": "2026-07-13 09:35:00",
        "source": "盘前",
        "direction": "buy",
        "rec_price": 10.0,
    }

    monkeypatch.setattr(recommend_module, "now_cn", lambda: datetime(2026, 7, 13, 14, 59, tzinfo=tz))
    before_close = verify_one(rec, 10.5)
    monkeypatch.setattr(recommend_module, "now_cn", lambda: datetime(2026, 7, 13, 15, 1, tzinfo=tz))
    after_close = verify_one(rec, 10.5)

    assert before_close["hit_status"] == "pending"
    assert before_close["continue_follow"] is True
    assert after_close["hit_status"] == "hit"
    assert after_close["continue_follow"] is False


def test_t1_window_uses_trading_days_not_weekend_days(monkeypatch) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    monkeypatch.setattr(recommend_module, "trade_dates", lambda: {
        "2026-07-10",  # Friday
        "2026-07-13",  # Monday
    })
    rec = {
        "rec_time": "2026-07-10 14:52:00",
        "source": "尾盘",
        "direction": "buy",
        "rec_price": 10.0,
    }

    monkeypatch.setattr(recommend_module, "now_cn", lambda: datetime(2026, 7, 12, 18, 0, tzinfo=tz))
    weekend = verify_one(rec, 10.5)
    monkeypatch.setattr(recommend_module, "now_cn", lambda: datetime(2026, 7, 13, 15, 1, tzinfo=tz))
    monday = verify_one(rec, 10.5)

    assert weekend["hit_status"] == "pending"
    assert monday["hit_status"] == "hit"


@pytest.mark.parametrize(
    ("direction", "current_price", "expected_raw_return"),
    [
        ("buy", 11.0, 10.0),
        ("add", 11.0, 10.0),
        ("avoid", 9.0, -10.0),
        ("reduce", 9.0, -10.0),
        ("exit", 9.0, -10.0),
    ],
)
def test_verify_scores_hit_against_recommendation_direction(
    monkeypatch, direction: str, current_price: float, expected_raw_return: float,
) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    monkeypatch.setattr(recommend_module, "trade_dates", lambda: {"2026-07-13"})
    monkeypatch.setattr(
        recommend_module, "now_cn",
        lambda: datetime(2026, 7, 13, 15, 1, tzinfo=tz),
    )

    review = verify_one({
        "rec_time": "2026-07-13 09:35:00",
        "source": "盘前",
        "direction": direction,
        "rec_price": 10.0,
    }, current_price)

    assert review["hit_status"] == "hit"
    assert review["final_return"] == expected_raw_return
    assert review["directional_return"] == 10.0
    assert review["quality_status"] == "ok"


def test_candidate_is_a_proposal_not_a_scoreable_direction(monkeypatch) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    monkeypatch.setattr(recommend_module, "trade_dates", lambda: {"2026-07-13"})
    monkeypatch.setattr(
        recommend_module, "now_cn",
        lambda: datetime(2026, 7, 13, 15, 1, tzinfo=tz),
    )

    review = verify_one({
        "rec_time": "2026-07-13 09:35:00",
        "source": "盘前",
        "direction": "candidate",
        "rec_price": 10.0,
    }, 11.0)

    assert review["hit_status"] == "pending"
    assert review["direction_group"] == "unknown"
    assert review["verdict"] is None


def test_verify_unknown_direction_is_explicitly_degraded_and_pending(monkeypatch) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    monkeypatch.setattr(recommend_module, "trade_dates", lambda: {"2026-07-13"})
    monkeypatch.setattr(
        recommend_module, "now_cn",
        lambda: datetime(2026, 7, 13, 15, 1, tzinfo=tz),
    )

    review = verify_one({
        "rec_time": "2026-07-13 09:35:00",
        "source": "盘前",
        "direction": "watch",
        "rec_price": 10.0,
    }, 10.5)

    assert review["hit_status"] == "pending"
    assert review["continue_follow"] is True
    assert review["direction_group"] == "unknown"
    assert review["directional_return"] is None
    assert review["quality_status"] == "degraded"
    assert "方向" in review["error_reason"]
    assert any("方向缺失或无法识别" in text for text in review["missing"])


@pytest.mark.parametrize(
    ("window", "target_date"),
    [(0, "2026-07-13"), (1, "2026-07-14"), (2, "2026-07-15"), (5, "2026-07-20")],
)
def test_each_review_window_only_matures_after_target_trade_day_close(
    monkeypatch, window: int, target_date: str,
) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    monkeypatch.setattr(recommend_module, "trade_dates", lambda: {
        "2026-07-13", "2026-07-14", "2026-07-15",
        "2026-07-16", "2026-07-17", "2026-07-20",
    })
    year, month, day = (int(part) for part in target_date.split("-"))
    rec = {
        "rec_time": "2026-07-13 09:35:00",
        "source": "自定义",
        "direction": "buy",
        "verify_days": window,
        "rec_price": 10.0,
    }

    monkeypatch.setattr(
        recommend_module, "now_cn",
        lambda: datetime(year, month, day, 14, 59, tzinfo=tz),
    )
    before_close = verify_one(rec, 10.5)
    monkeypatch.setattr(
        recommend_module, "now_cn",
        lambda: datetime(year, month, day, 15, 0, tzinfo=tz),
    )
    after_close = verify_one(rec, 10.5)

    assert before_close["hit_status"] == "pending"
    assert before_close["continue_follow"] is True
    assert after_close["hit_status"] == "hit"
    assert after_close["continue_follow"] is False


def test_t1_next_trade_day_open_does_not_close_recommendation(monkeypatch) -> None:
    tz = ZoneInfo("Asia/Shanghai")
    monkeypatch.setattr(
        recommend_module, "trade_dates",
        lambda: {"2026-07-13", "2026-07-14"},
    )
    monkeypatch.setattr(
        recommend_module, "now_cn",
        lambda: datetime(2026, 7, 14, 9, 31, tzinfo=tz),
    )

    review = verify_one({
        "rec_time": "2026-07-13 14:52:00",
        "source": "尾盘",
        "direction": "buy",
        "rec_price": 10.0,
    }, 10.5)

    assert review["hit_status"] == "pending"
    assert review["continue_follow"] is True
