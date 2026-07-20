from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from hermes_trader.analysis import recommend
from hermes_trader.knowledge.store import Store


CN = ZoneInfo("Asia/Shanghai")
CALENDAR = {
    "2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16",
    "2026-07-17", "2026-07-20",
}


def _rec(**overrides) -> dict:
    row = {
        "rec_id": "r_review_v2",
        "event_id": "re_review_v2",
        "rec_time": "2026-07-13 09:35:00",
        "trade_day": "2026-07-13",
        "source": "问股",
        "code": "000001",
        "direction": "buy",
        "rec_price": 10.0,
        "entry_trigger": {"state": "triggered", "evidence_id": "quote:1"},
        "score_eligible": True,
        "status": "active",
    }
    row.update(overrides)
    return row


def _bars(*rows):
    return [
        {"d": day, "o": close, "h": high, "l": low, "c": close,
         "source": "test-daily", "as_of": day, "adjustment": "none"}
        for day, close, high, low in rows
    ]


def test_free_text_entry_condition_is_insufficient_not_a_hindsight_hit(monkeypatch) -> None:
    monkeypatch.setattr(recommend, "trade_dates", lambda: CALENDAR)
    monkeypatch.setattr(recommend, "now_cn", lambda: datetime(2026, 7, 13, 15, 5, tzinfo=CN))

    review = recommend.verify_one(
        _rec(entry_trigger=None, buy_condition="放量站稳 10.20 后才买"),
        None,
        window="T0",
        price_path=_bars(("2026-07-13", 11.0, 11.2, 9.9)),
    )

    assert review["evaluation_state"] == "incomparable"
    assert review["pending_reason"] == "trigger_unknown"
    assert review["verdict"] == "insufficient_evidence"
    assert review["hit_status"] == "pending"
    assert review["terminal"] is True


def test_structured_condition_not_triggered_is_not_scored(monkeypatch) -> None:
    monkeypatch.setattr(recommend, "trade_dates", lambda: CALENDAR)
    monkeypatch.setattr(recommend, "now_cn", lambda: datetime(2026, 7, 14, 15, 5, tzinfo=CN))
    rec = _rec(entry_trigger={
        "state": "unknown",
        "rule": {"operator": "lte", "value": 9.0},
    })

    review = recommend.verify_one(
        rec, None, window="T1",
        price_path=_bars(
            ("2026-07-13", 10.3, 10.5, 10.0),
            ("2026-07-14", 11.0, 11.2, 10.1),
        ),
    )

    assert review["evaluation_state"] == "untriggered"
    assert review["verdict"] == "not_triggered"
    assert review["hit_status"] == "pending"
    assert review["terminal"] is True


def test_complete_path_calculates_mfe_mae_and_uses_target_close(monkeypatch) -> None:
    monkeypatch.setattr(recommend, "trade_dates", lambda: CALENDAR)
    # 补跑发生在 T2 收盘后，但 T1 必须仍读取 7/14 收盘 9.50。
    monkeypatch.setattr(recommend, "now_cn", lambda: datetime(2026, 7, 15, 15, 5, tzinfo=CN))
    review = recommend.verify_one(
        _rec(), 99.0, window="T1",
        price_path=_bars(
            ("2026-07-13", 10.5, 11.0, 9.5),
            ("2026-07-14", 9.5, 12.0, 9.0),
            ("2026-07-15", 99.0, 100.0, 8.0),
        ),
    )

    assert review["observation"]["trade_day"] == "2026-07-14"
    assert review["observation"]["price_basis"] == "target_close"
    assert review["current_price"] == 9.5
    assert review["final_return"] == -5.0
    assert review["max_return"] == 20.0
    assert review["max_drawdown"] == -10.0
    assert review["coverage"]["path_complete"] is True


@pytest.mark.asyncio
async def test_run_verify_freezes_each_window_independently(monkeypatch, tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.add_rec({
        "_rec_id": "r_windows",
        "code": "000001", "rec_price": 10.0,
        "rec_time": "2026-07-13 09:35:00", "source": "问股",
        "direction": "buy", "_server_validated": True, "_score_eligible": True,
    })
    # Store 白名单不含新字段时，模拟统一事件兼容桥附加的正式字段。
    rows = store.recommendations()
    rows[0].update({
        "event_id": "re_windows", "trade_day": "2026-07-13",
        "direction": "buy", "entry_trigger": {"state": "triggered"},
    })
    store._write_json(store._recommendations_path, rows)
    current = {"now": datetime(2026, 7, 15, 15, 5, tzinfo=CN)}
    monkeypatch.setattr(recommend, "trade_dates", lambda: CALENDAR)
    monkeypatch.setattr(recommend, "now_cn", lambda: current["now"])
    monkeypatch.setattr(
        "hermes_trader.data.tencent_source.stock_daily_bars",
        lambda *_args, **_kwargs: _bars(
            ("2026-07-13", 10.5, 10.6, 9.9),
            ("2026-07-14", 9.5, 10.1, 9.4),
            ("2026-07-15", 11.0, 11.2, 9.3),
            ("2026-07-16", 10.8, 11.0, 10.0),
            ("2026-07-17", 10.7, 10.9, 10.2),
            ("2026-07-20", 12.0, 12.2, 10.4),
        ),
    )

    first = await recommend.run_verify(store)
    first_reviews = store.rec_reviews()

    assert first["verified"] == 3
    assert {row["window"] for row in first_reviews} == {"T0", "T1", "T2"}
    assert {row["window"]: row["current_price"] for row in first_reviews} == {
        "T0": 10.5, "T1": 9.5, "T2": 11.0,
    }
    assert store.recommendations()[0]["status"] == "active"

    current["now"] = datetime(2026, 7, 20, 15, 5, tzinfo=CN)
    second = await recommend.run_verify(store)
    assert second["verified"] == 1
    assert {row["window"] for row in store.rec_reviews()} == {"T0", "T1", "T2", "T5"}
    assert store.recommendations()[0]["status"] == "closed"


def test_stats_separates_waiting_untriggered_incomparable_and_hides_small_win_rate() -> None:
    recs = [_rec(rec_id="r_stats")]
    reviews = [
        {"rec_id": "r_stats", "window": "T0", "review_time": "2026-07-13 15:05:00",
         "terminal": True, "evaluation_state": "evaluated", "verdict": "hit",
         "hit_status": "hit", "directional_return": 2.0},
        {"rec_id": "r_stats", "window": "T1", "review_time": "2026-07-14 15:05:00",
         "terminal": True, "evaluation_state": "untriggered", "verdict": "not_triggered",
         "hit_status": "pending"},
        {"rec_id": "r_stats", "window": "T2", "review_time": "2026-07-15 15:05:00",
         "terminal": True, "evaluation_state": "incomparable", "verdict": "insufficient_evidence",
         "hit_status": "pending"},
    ]

    result = recommend.stats(recs, reviews)

    assert result["sample"] == 1
    assert result["untriggered"] == 1
    assert result["incomparable"] == 1
    assert result["waiting"] == 1
    assert result["observed_win_rate"] == 100.0
    assert result["win_rate"] is None
    assert result["enough_sample"] is False
    assert result["win_rate_unit"] == "percent"
