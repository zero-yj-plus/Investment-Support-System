from __future__ import annotations

from datetime import datetime
import os

from hermes_trader.data import news_observation
from hermes_trader.data.base import CN_TZ
from hermes_trader.jobs import news_event_observer


TRADE_DATES = {"2026-07-13", "2026-07-14", "2026-07-15"}


def _now(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=CN_TZ)


def _frame(day: str, price: float, *, prev: float = 10.0) -> dict:
    return {
        "data_date": day,
        "as_of": day.replace("-", ""),
        "count": 2,
        "stocks": {
            "600000": {"code": "600000", "price": price, "prev_close": prev, "change_pct": round((price / prev - 1) * 100, 2)},
            "600001": {"code": "600001", "price": 8.0, "prev_close": 8.0, "change_pct": 0.0},
        },
    }


def _item() -> dict:
    return {
        "event_id": "event-after-close-1",
        "title": "浦发银行发布公告",
        "time": "2026-07-13 22:00:00",
        "a_share": {
            "state": "mapped",
            "level": "direct_stock",
            "matches": [
                {"kind": "stock", "code": "600000", "name": "浦发银行", "match_by": "explicit_code", "name_consistency": "matched"},
                {"kind": "industry", "code": "881155.SH", "name": "银行", "match_by": "derived_from_stock"},
            ],
        },
    }


def _capture(tmp_path, monkeypatch, now: str, frame: dict, items=None):
    news_observation.init(tmp_path)
    monkeypatch.setattr(news_observation, "trade_dates", lambda: set(TRADE_DATES))
    return news_observation.capture_mapped_items(
        list(items if items is not None else [_item()]),
        observed_at=_now(now),
        frame=frame,
        members_by_sector={"881155.SH": ("600000", "600001")},
        names={"600000": "浦发银行", "600001": "邯郸银行"},
    )


def test_after_close_event_waits_for_strict_next_trade_day_and_files_are_private(tmp_path, monkeypatch) -> None:
    result = _capture(tmp_path, monkeypatch, "2026-07-13 22:05:00", _frame("2026-07-13", 10.0, prev=9.8))

    assert result["created"] == 1
    event = news_observation._store.events()[0]
    assert event["target_trade_date"] == "2026-07-14"
    assert event["observation_basis"] == "next_session"
    contract = news_observation.enrich_items([_item()], now=_now("2026-07-13 22:06:00"))["items"][0]["a_share"]["observation"]
    assert contract["status"] == "waiting"
    assert contract["progress"] == {"complete": 1, "total": 3}
    assert contract["baseline"]["stocks"][0]["price"] == 10.0
    assert os.stat(tmp_path / "news_events" / "events.v1.jsonl").st_mode & 0o777 == 0o600
    assert os.stat(tmp_path / "news_events" / "observations.v1.jsonl").st_mode & 0o777 == 0o600


def test_waiting_to_observing_to_observed_uses_event_baseline(tmp_path, monkeypatch) -> None:
    _capture(tmp_path, monkeypatch, "2026-07-13 22:05:00", _frame("2026-07-13", 10.0, prev=9.8))
    live = _capture(tmp_path, monkeypatch, "2026-07-14 10:35:00", _frame("2026-07-14", 10.5), items=[])
    assert live["captured"] == 1

    observing = news_observation.enrich_items([_item()], now=_now("2026-07-14 10:36:00"))["items"][0]["a_share"]["observation"]
    assert observing["status"] == "observing"
    assert observing["progress"] == {"complete": 2, "total": 3}
    assert observing["latest"]["stocks"][0]["since_baseline_pct"] == 5.0

    closed = _capture(tmp_path, monkeypatch, "2026-07-14 15:10:00", _frame("2026-07-14", 10.2), items=[])
    assert closed["captured"] == 1
    observed = news_observation.enrich_items([_item()], now=_now("2026-07-14 15:11:00"))["items"][0]["a_share"]["observation"]
    assert observed["status"] == "observed"
    assert observed["progress"] == {"complete": 3, "total": 3}
    assert observed["result"]["stocks"][0]["since_baseline_pct"] == 2.0
    assert observed["causality"] == "not_inferred"
    assert observed["score_weight"] == 0

    reconciliation = news_observation.daily_reconciliation("2026-07-14")
    assert reconciliation["lifecycle"] == "observed"
    assert reconciliation["verdict"] == "证据不足"
    assert reconciliation["verdict_reason"] == "no_frozen_claim"
    assert reconciliation["causality"] == "not_inferred"
    assert reconciliation["items"][0]["fact_differences"]["stocks"][0]["close_vs_baseline_pct"] == 2.0
    assert len(reconciliation["content_hash"]) == 64


def test_event_finishes_after_it_has_left_the_rolling_news_feed_and_retries_are_idempotent(tmp_path, monkeypatch) -> None:
    _capture(tmp_path, monkeypatch, "2026-07-13 22:05:00", _frame("2026-07-13", 10.0, prev=9.8))
    _capture(tmp_path, monkeypatch, "2026-07-14 15:10:00", _frame("2026-07-14", 10.2), items=[])
    before = len(news_observation._store.observations())
    _capture(tmp_path, monkeypatch, "2026-07-14 15:20:00", _frame("2026-07-14", 10.3), items=[])

    assert len(news_observation._store.observations()) == before
    history = news_observation.history()
    assert history["items"][0]["observation"]["status"] == "observed"


def test_advanced_market_frame_is_incomparable_not_a_later_day_substitute(tmp_path, monkeypatch) -> None:
    _capture(tmp_path, monkeypatch, "2026-07-13 22:05:00", _frame("2026-07-13", 10.0, prev=9.8))
    _capture(tmp_path, monkeypatch, "2026-07-15 10:00:00", _frame("2026-07-15", 11.0, prev=10.2), items=[])

    contract = news_observation.enrich_items([_item()], now=_now("2026-07-15 10:01:00"))["items"][0]["a_share"]["observation"]
    assert contract["status"] == "incomparable"
    assert contract["reason_code"] == "target_frame_missed"
    assert contract["result"] is None
    close_checkpoint = next(row for row in contract["checkpoints"] if row["key"] == "target_close")
    assert close_checkpoint["status"] == "missing"
    assert close_checkpoint["reason_code"] == "target_frame_missed"


def test_unknown_calendar_never_uses_weekday_as_final_target(tmp_path, monkeypatch) -> None:
    news_observation.init(tmp_path)
    monkeypatch.setattr(news_observation, "trade_dates", lambda: None)
    news_observation.capture_mapped_items(
        [_item()], observed_at=_now("2026-07-13 22:05:00"),
        frame=_frame("2026-07-13", 10.0), members_by_sector={}, names={"600000": "浦发银行"},
    )

    event = news_observation._store.events()[0]
    contract = news_observation.enrich_items([_item()], now=_now("2026-07-13 22:06:00"))["items"][0]["a_share"]["observation"]
    assert event["target_trade_date"] is None
    assert contract["status"] == "incomparable"
    assert contract["reason_code"] == "calendar_unavailable"


def test_api_enrichment_is_read_only(tmp_path, monkeypatch) -> None:
    _capture(tmp_path, monkeypatch, "2026-07-13 22:05:00", _frame("2026-07-13", 10.0))
    events = tmp_path / "news_events" / "events.v1.jsonl"
    observations = tmp_path / "news_events" / "observations.v1.jsonl"
    before = (events.read_bytes(), observations.read_bytes())

    news_observation.enrich_items([_item()], now=_now("2026-07-13 22:06:00"))

    assert (events.read_bytes(), observations.read_bytes()) == before


def test_observer_schedule_covers_midnight_and_early_morning() -> None:
    recorded = {}

    class _Scheduler:
        def add_job(self, func, trigger, **kwargs):
            recorded.update(func=func, trigger=trigger, kwargs=kwargs)

    class _Config:
        timezone = CN_TZ

        @staticmethod
        def section(name):
            assert name == "news_event_observation"
            return {"enabled": True, "interval_min": 10}

    news_event_observer.register(_Scheduler(), _Config(), None)

    next_fire = recorded["trigger"].get_next_fire_time(None, _now("2026-07-14 00:01:00"))
    assert next_fire.hour == 0
    assert next_fire.minute == 10
