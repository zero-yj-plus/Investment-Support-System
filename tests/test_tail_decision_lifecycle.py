from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_trader.agent import prompts
from hermes_trader.analysis.engine import Engine
from hermes_trader.api.routes import run_job
from hermes_trader.jobs.scheduler import build_scheduler, jobs_status
from hermes_trader.jobs import scheduler as scheduler_mod


class _Store:
    def __init__(self, reports: dict | None = None):
        self.root = Path(tempfile.mkdtemp())
        self.reports = reports or {}
        self.states: dict[str, dict] = {}

    def get_report(self, rtype: str, _date: str | None = None):
        return self.reports.get(rtype)

    def record_job(self, job_id: str, **fields) -> None:
        self.states.setdefault(job_id, {}).update(fields)

    def get_job_states(self) -> dict:
        return self.states

    @staticmethod
    def holdings() -> list:
        return []

    @staticmethod
    def watchlist() -> list:
        return []

    @staticmethod
    def sentiment_history(_days: int) -> list:
        return []


class _Config:
    timezone = "Asia/Shanghai"
    trading_days_only = False
    push_enabled = True
    capital_tracking_enabled = False
    schedule = {
        "morning": "08:50",
        "midday": "14:45",
        "tail": "14:52",
        "close": "15:30",
        "evening": "19:30",
        "archive": "22:00",
    }

    @staticmethod
    def section(_name: str) -> dict:
        return {}


class _ReadyPusher:
    configured = True
    delivery_channel = "test"

    async def preflight(self):
        return {"configured": True, "ready": True, "targets": ["test"], "mode": "test"}


class _UnavailablePusher:
    configured = False
    delivery_channel = "test"

    async def preflight(self):
        return {"configured": False, "ready": False, "targets": [], "mode": "test"}


class _JobEngine:
    def __init__(self):
        self.store = _Store()
        self.calls: list[tuple[str, bool]] = []
        self.archive_calls: list[bool] = []
        self.feishu = _ReadyPusher()

    async def generate_report(self, rtype: str, push: bool = False) -> dict:
        self.calls.append((rtype, push))
        return {"rtype": rtype, "content_md": "已定稿", "pushed": push}

    async def archive_today(self, push: bool = False) -> dict:
        self.archive_calls.append(push)
        return {"date": "2026-07-13", "reports": {}, "pushed": push}


@pytest.mark.asyncio
async def test_midday_is_internal_draft_and_tail_is_only_customer_push() -> None:
    engine = _JobEngine()
    scheduler = build_scheduler(_Config(), engine)

    await scheduler.run_job("midday")
    await scheduler.run_job("tail")

    assert engine.calls == [("midday", False), ("tail", True)]
    assert engine.store.states["midday"]["last_push_ok"] is None
    assert engine.store.states["midday"]["last_push_time"] is None
    assert engine.store.states["tail"]["last_push_ok"] is True


@pytest.mark.asyncio
async def test_scheduled_push_switches_are_independent_and_only_gate_delivery() -> None:
    class PartialPushConfig(_Config):
        scheduled_push = {
            "morning": True,
            "tail": False,
            "close": True,
            "evening": True,
            "archive": False,
        }

    engine = _JobEngine()
    scheduler = build_scheduler(PartialPushConfig(), engine)

    morning = await scheduler.run_job("morning")
    tail = await scheduler.run_job("tail")
    archive = await scheduler.run_job("archive")

    assert engine.calls == [("morning", True), ("tail", False)]
    assert engine.archive_calls == [False]
    assert morning["status"] == "ok"
    assert tail["status"] == "ok"
    assert tail["generated"] is True
    assert tail["pushed"] is False
    assert engine.store.states["tail"]["last_push_ok"] is None
    assert archive["status"] == "ok"
    assert archive["generated"] is True


@pytest.mark.asyncio
async def test_evening_requires_both_reports_to_be_accepted() -> None:
    engine = _JobEngine()

    async def partial(rtype: str, push: bool = False) -> dict:
        return {"rtype": rtype, "content_md": "已定稿", "pushed": rtype == "evening"}

    engine.generate_report = partial
    scheduler = build_scheduler(_Config(), engine)
    result = await scheduler.run_job("evening")

    assert result["ok"] is False
    assert result["error_code"] == "required_delivery_not_accepted"
    assert engine.store.states["evening"]["last_status"] == "error"


@pytest.mark.asyncio
async def test_delivery_not_ready_still_freezes_report_and_only_blocks_sending() -> None:
    engine = _JobEngine()
    engine.feishu = _UnavailablePusher()

    async def generated(rtype: str, push: bool = False) -> dict:
        engine.calls.append((rtype, push))
        return {"rtype": rtype, "content_md": "已冻结定稿", "pushed": False}

    engine.generate_report = generated
    scheduler = build_scheduler(_Config(), engine)
    result = await scheduler.run_job("close")

    assert engine.calls == [("close", False)]
    assert result["generated"] is True
    assert result["status"] == "blocked"
    assert result["error_code"] == "delivery_not_ready"
    assert engine.store.states["close"]["last_generated_ok"] is True
    assert engine.store.states["close"]["last_push_ok"] is None


@pytest.mark.asyncio
async def test_unknown_calendar_blocks_automatic_job(monkeypatch) -> None:
    engine = _JobEngine()

    class TradingConfig(_Config):
        trading_days_only = True

    monkeypatch.setattr(scheduler_mod, "trade_day_status", lambda: {
        "status": "unknown", "is_trade_day": None, "source": "calendar_unavailable",
        "as_of": "2026-07-13", "quality": "missing",
    })
    scheduler = build_scheduler(TradingConfig(), engine)
    result = await scheduler.run_job("morning")

    assert result["status"] == "blocked"
    assert result["error_code"] == "calendar_unavailable"
    assert engine.calls == []
    assert engine.store.states["morning"]["last_status"] == "blocked"


@pytest.mark.asyncio
async def test_tail_context_receives_1445_draft(monkeypatch) -> None:
    store = _Store({
        "midday": {
            "rtype": "midday",
            "created_at": "2026-07-13 14:45:10",
            "content_md": "# 尾盘决策草案\n观察 000001，但须等待 14:52 复核。",
            "cockpit": {"command": {"risk_level": 4}},
        }
    })
    engine = Engine(config=None, store=store, hermes=None)

    async def _market_context(*, full: bool = True) -> dict:
        assert full is True
        return {"市场总览": {"ok": True}}

    monkeypatch.setattr(engine, "_market_context", _market_context)
    from hermes_trader.data import free_source
    monkeypatch.setattr(free_source, "kpl_list", lambda *_args, **_kwargs: [])

    ctx = await engine._context_for_report("tail")

    draft = ctx["尾盘决策草案(14:45原文·仅作复核输入)"]
    assert draft["status"] == "draft"
    assert "000001" in draft["content_md"]
    assert "唯一对客正式尾盘结论" in ctx["尾盘定稿规则"]


def test_tail_prompt_and_metadata_use_single_lifecycle_language() -> None:
    assert prompts.REPORT_META["midday"]["title"] == "尾盘决策草案"
    assert prompts.REPORT_META["tail"]["title"] == "尾盘决策定稿"
    user_prompt = prompts.build_report_prompt("tail", {"今日日期": "2026-07-13"})[1]["content"]
    assert "逐条复核草案" in user_prompt
    assert "最终只输出这一份正式结论" in user_prompt


def test_status_includes_extension_and_system_jobs() -> None:
    core_job = SimpleNamespace(id="morning", name="morning", next_run_time=None)
    ext_job = SimpleNamespace(id="ext_daily_plan_finalize", name="daily_plan_finalize", next_run_time=None)
    system_job = SimpleNamespace(id="sentinel_1540", name="sentinel", next_run_time=None)

    class _Scheduler:
        @staticmethod
        def get_job(job_id: str):
            return core_job if job_id == "morning" else None

        @staticmethod
        def get_jobs() -> list:
            return [core_job, ext_job, system_job]

    store = _Store()
    store.record_job("ext_daily_plan_finalize", last_status="ok")
    rows = jobs_status(_Scheduler(), _Config(), store)
    by_id = {row["id"]: row for row in rows}

    assert by_id["ext_daily_plan_finalize"]["kind"] == "extension"
    assert by_id["ext_daily_plan_finalize"]["last_status"] == "ok"
    assert by_id["sentinel_1540"]["kind"] == "system"


@pytest.mark.asyncio
async def test_manual_ext_job_runs_registered_wrapper_not_core_report_runner() -> None:
    finished = asyncio.Event()

    async def _ext(value: str) -> None:
        assert value == "daily-plan"
        finished.set()

    job = SimpleNamespace(
        id="ext_daily_plan_finalize",
        func=_ext,
        args=("daily-plan",),
        kwargs={},
    )

    class _Scheduler:
        @staticmethod
        def get_job(job_id: str):
            return job if job_id == job.id else None

        @staticmethod
        async def run_job(_job_id: str) -> None:
            raise AssertionError("ext task must not enter the core report runner")

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler=_Scheduler())))
    response = await run_job(job.id, request)
    await asyncio.wait_for(finished.wait(), timeout=1)

    assert response["ok"] is True
    assert response["msg"] == "已触发"
    assert response["run_id"].startswith("run_")
