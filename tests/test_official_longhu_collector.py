from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from hermes_trader.data.base import CN_TZ
from hermes_trader.jobs import official_longhu_collector as collector


class _Config:
    timezone = "Asia/Shanghai"

    @staticmethod
    def section(name: str) -> dict:
        assert name == "official_longhu"
        return {
            "enabled": True,
            "times": ["18:20", "bad", "21:10", "21:10"],
            "timeout_sec": 99,
            "max_pages": 999,
            "max_detail_requests": 999,
            "request_gap_sec": 99,
        }


class _Store:
    def __init__(self):
        self.states = {}

    def record_job(self, job_id, **fields):
        self.states.setdefault(job_id, {}).update(fields)


class _Scheduler:
    def __init__(self):
        self.jobs = {}

    def add_job(self, func, trigger, **kwargs):
        self.jobs[kwargs["id"]] = SimpleNamespace(func=func, trigger=trigger, kwargs=kwargs)


def _now(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=CN_TZ)


def test_settings_are_bounded_and_times_are_deduplicated() -> None:
    settings = collector._bounded_settings(_Config())

    assert settings == {
        "enabled": True,
        "timeout": 20.0,
        "max_pages": 50,
        "max_detail_requests": 200,
        "request_gap_seconds": 5.0,
        "times": ("18:20", "21:10"),
    }


@pytest.mark.asyncio
async def test_capture_fetches_then_saves_snapshot(monkeypatch) -> None:
    calls = []

    def fake_fetch(day, **kwargs):
        calls.append((day, kwargs))
        return {"state": "complete", "trade_date": day, "event_count": 2, "seat_count": 10}

    monkeypatch.setattr(
        "hermes_trader.data.official_longhu_source.fetch_official_longhu", fake_fetch
    )
    monkeypatch.setattr(
        "hermes_trader.data.official_longhu_source.save_snapshot",
        lambda payload: SimpleNamespace(name=f"{payload['trade_date']}.json"),
    )

    result = await collector.capture(_Config(), "2026-07-13")

    assert calls == [("2026-07-13", {
        "timeout": 20.0,
        "max_pages": 50,
        "max_detail_requests": 200,
        "request_gap_seconds": 5.0,
    })]
    assert result["snapshot_saved"] is True
    assert result["snapshot_file"] == "2026-07-13.json"


@pytest.mark.asyncio
async def test_registered_jobs_record_complete_and_do_not_run_on_closed_day(monkeypatch) -> None:
    scheduler, store = _Scheduler(), _Store()
    collector.register(scheduler, _Config(), store)

    assert set(scheduler.jobs) == {"official_longhu_1820", "official_longhu_2110"}

    monkeypatch.setattr(
        collector,
        "trade_day_status",
        lambda: {"status": "open", "as_of": "2026-07-13", "quality": "complete"},
    )
    monkeypatch.setattr(collector, "now_cn", lambda: _now("2026-07-13 18:20:00"))

    async def fake_capture(_config, trade_date):
        return {
            "state": "complete",
            "trade_date": trade_date,
            "event_count": 3,
            "seat_count": 20,
            "snapshot_saved": True,
        }

    monkeypatch.setattr(collector, "capture", fake_capture)
    result = await scheduler.jobs["official_longhu_1820"].func()

    assert result == {"ok": True, "status": "ok", "state": "complete", "error_code": None}
    assert store.states["official_longhu_1820"]["last_status"] == "ok"
    assert store.states["official_longhu_1820"]["snapshot_saved"] is True

    monkeypatch.setattr(
        collector,
        "trade_day_status",
        lambda: {"status": "closed", "as_of": "2026-07-14", "quality": "complete"},
    )
    skipped = await scheduler.jobs["official_longhu_2110"].func()
    assert skipped["status"] == "skipped"
    assert skipped["error_code"] == "skipped_non_trading_day"


@pytest.mark.asyncio
async def test_missing_result_is_error_and_retains_snapshot_signal(monkeypatch) -> None:
    scheduler, store = _Scheduler(), _Store()
    collector.register(scheduler, _Config(), store)
    monkeypatch.setattr(
        collector,
        "trade_day_status",
        lambda: {"status": "open", "as_of": "2026-07-13", "quality": "complete"},
    )
    monkeypatch.setattr(collector, "now_cn", lambda: _now("2026-07-13 21:10:00"))

    async def fake_capture(_config, trade_date):
        return {
            "state": "missing",
            "trade_date": trade_date,
            "event_count": 0,
            "seat_count": 0,
            "snapshot_saved": False,
            "availability_reason": "两所链路均不可用",
        }

    monkeypatch.setattr(collector, "capture", fake_capture)
    result = await scheduler.jobs["official_longhu_2110"].func()

    assert result["ok"] is False
    assert result["error_code"] == "official_longhu_missing"
    assert store.states["official_longhu_2110"]["last_status"] == "error"
    assert store.states["official_longhu_2110"]["snapshot_saved"] is False
