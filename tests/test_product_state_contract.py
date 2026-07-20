from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from hermes_trader import product_state
from hermes_trader.api import routes
from hermes_trader.ext_loader import apply_view_lifecycle
from hermes_trader.jobs.scheduler import task_center_status
from hermes_trader.knowledge.store import Store as KnowledgeStore


@pytest.mark.parametrize(
    ("hhmm", "expected", "route"),
    [
        ("09:24", "premarket", "premarket"),
        ("09:25", "intraday", "today"),
        ("14:44", "intraday", "today"),
        ("14:45", "tail", "tail"),
        ("14:59", "tail", "tail"),
        ("15:00", "review", "review"),
    ],
)
def test_market_session_boundaries_use_beijing_time(
    monkeypatch: pytest.MonkeyPatch, hhmm: str, expected: str, route: str,
) -> None:
    monkeypatch.setattr(product_state, "prev_trade_day", lambda _d: "2026-07-10")
    monkeypatch.setattr(product_state, "next_trade_day", lambda _d: "2026-07-14")
    hour, minute = (int(x) for x in hhmm.split(":"))
    at = datetime(2026, 7, 13, hour, minute, tzinfo=product_state.CN_TZ)

    ctx = product_state.build_market_context(at, trade_day_checker=lambda _d: True)

    assert ctx["market_session"] == expected
    assert ctx["recommended_route"] == route
    assert ctx["recommended_hash"] == f"#/{route}"


def test_non_trading_day_always_routes_to_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(product_state, "prev_trade_day", lambda _d: "2026-07-10")
    monkeypatch.setattr(product_state, "next_trade_day", lambda _d: "2026-07-13")
    sunday = datetime(2026, 7, 12, 10, 0, tzinfo=product_state.CN_TZ)

    ctx = product_state.build_market_context(sunday, trade_day_checker=lambda _d: False)

    assert ctx["market_session"] == "non_trading"
    assert ctx["recommended_route"] == "review"
    assert ctx["trade_day"] == "2026-07-13"


def test_route_redirect_contract_targets_only_canonical_routes() -> None:
    contract = product_state.route_lifecycle_contract()
    canonical = {row["path"] for row in contract["canonical"]}
    redirects = {row["from"]: row for row in contract["redirects"]}

    assert canonical == {"premarket", "today", "risk", "tail", "review", "research", "history", "settings"}
    assert all(row["to"] in canonical for row in redirects.values())
    assert redirects["cockpit"]["to"] == "today"
    assert redirects["holdings"] == {
        "from": "holdings", "to": "risk", "tab": "holdings",
        "lifecycle": "redirect", "preserve_query": True,
    }
    assert redirects["archive"]["to"] == "review"
    assert redirects["archive"]["tab"] == "close"
    assert redirects["recommend"]["to"] == "review"
    assert redirects["recommend"]["tab"] == "close"
    assert redirects["hotboard"]["to"] == "today"
    assert redirects["hotboard"]["tab"] == "hotboard"


def test_extension_lifecycle_is_applied_after_complete_discovery() -> None:
    views = [
        {"path": "visible", "module": "/ext/a.js", "source": "a"},
        {"path": "hidden", "module": "/ext/b.js", "source": "b"},
        {"path": "old", "redirect_to": "today", "source": "c"},
        {"path": "gone", "lifecycle": "retired", "source": "d"},
        {"path": "visible", "module": "/ext/duplicate.js", "source": "z"},
    ]

    registry = apply_view_lifecycle(views, {"nav_hidden": ["hidden"]})
    by_path = {row["path"]: row for row in registry}

    assert len(registry) == 4
    assert by_path["visible"]["lifecycle"] == "visible"
    assert by_path["visible"]["conflicts"] == ["z"]
    assert by_path["hidden"]["lifecycle"] == "hidden"
    assert by_path["hidden"]["registered"] is True
    assert by_path["hidden"]["navigation"] is False
    assert by_path["old"]["lifecycle"] == "redirect"
    assert by_path["old"]["registered"] is False
    assert by_path["gone"]["lifecycle"] == "retired"


def test_task_center_merges_core_extension_and_system_jobs() -> None:
    core = SimpleNamespace(id="morning", name="morning", next_run_time=None)
    ext = SimpleNamespace(id="ext_daily", name="daily", next_run_time=None)
    system = SimpleNamespace(id="sentinel_1540", name="sentinel", next_run_time=None)

    class Scheduler:
        @staticmethod
        def get_job(job_id: str):
            return core if job_id == "morning" else None

        @staticmethod
        def get_jobs():
            return [core, ext, system]

    class Config:
        schedule = {
            "morning": "08:50", "midday": "14:45", "tail": "14:52",
            "close": "15:30", "evening": "19:30", "archive": "22:00",
        }

    class Store:
        @staticmethod
        def get_job_states():
            return {
                "ext_daily": {
                    "last_run": "2026-07-13 15:35:00",
                    "last_finished": "2026-07-13 15:35:02",
                    "last_status": "error",
                    "last_duration_ms": 2100,
                    "last_error": "upstream timeout",
                    "last_output_objects": [{
                        "kind": "report",
                        "content_id": "report:tail:2026-07-13",
                        "report_type": "tail",
                        "trade_day": "2026-07-13",
                        "version": 1,
                        "content_hash": "a" * 64,
                    }],
                }
            }

    center = task_center_status(Scheduler(), Config(), Store())
    rows = {row["id"]: row for row in center["jobs"]}

    assert center["schema_version"] == "task-center-v1"
    assert center["summary"]["by_kind"] == {"core": 6, "extension": 1, "system": 1}
    assert center["summary"]["needs_attention"] == 1
    assert rows["ext_daily"]["last_result"]["duration_ms"] == 2100
    assert rows["ext_daily"]["last_result"]["output_objects"][0]["content_id"] == "report:tail:2026-07-13"
    assert rows["ext_daily"]["last_output_objects"] == rows["ext_daily"]["last_result"]["output_objects"]
    assert rows["ext_daily"]["last_error"] == "任务执行失败，请查看服务端日志"
    assert rows["ext_daily"]["last_error_code"] == "job_execution_failed"
    assert "upstream timeout" not in repr(rows["ext_daily"])
    assert rows["ext_daily"]["manual_run"]["endpoint"] == "/api/jobs/ext_daily/run"
    assert rows["ext_daily"]["retry"]["eligible"] is True


def test_startup_reconciles_persisted_running_jobs(tmp_path) -> None:
    store = KnowledgeStore(tmp_path)
    store.record_job(
        "midday",
        last_run="2026-07-14 11:30:00",
        last_status="running",
        last_error=None,
    )
    store.record_job(
        "archive",
        last_run="2026-07-14 22:00:00",
        last_status="ok",
        last_error=None,
    )

    changed = store.reconcile_interrupted_jobs()
    states = store.get_job_states()

    assert changed == ["midday"]
    assert states["midday"]["last_status"] == "interrupted"
    assert states["midday"]["last_error"] == "process_restarted_before_completion"
    assert states["midday"]["last_finished"]
    assert states["archive"]["last_status"] == "ok"
    assert store.reconcile_interrupted_jobs() == []


@pytest.mark.asyncio
async def test_shell_status_never_probes_model_or_operational_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = {
        "date": "2026-07-13",
        "is_trade_day": True,
        "market_session": "intraday",
        "recommended_route": "today",
        "recommended_hash": "#/today",
    }
    monkeypatch.setattr(routes, "build_market_context", lambda: context)

    class Hermes:
        @staticmethod
        async def check_connection():
            raise AssertionError("shell status must not probe the model gateway")

    config = SimpleNamespace(
        client_name="client",
        client_id="id",
        client_brand="brand",
        client_brand_sub="sub",
        section=lambda name: {"nav_hidden": [], "data_source_label": "test"}
        if name == "ui" else {},
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=config, hermes=Hermes()))
    )

    payload = await routes.shell_status(request)

    assert payload["status_scope"] == "shell"
    assert payload["market_context"] == context
    assert payload["recommended_route"] == "today"
    assert payload["data_source"] == "test"
    assert "hermes" not in payload
    assert "task_center" not in payload


@pytest.mark.asyncio
async def test_status_exposes_state_routes_and_task_center(monkeypatch: pytest.MonkeyPatch) -> None:
    context = {
        "timezone": "Asia/Shanghai",
        "now": "2026-07-13T10:00:00+08:00",
        "date": "2026-07-13",
        "is_trade_day": True,
        "trade_day": "2026-07-13",
        "previous_trade_day": "2026-07-10",
        "next_trade_day": "2026-07-14",
        "market_session": "intraday",
        "recommended_route": "today",
        "recommended_hash": "#/today",
    }
    monkeypatch.setattr(routes, "build_market_context", lambda: context)
    monkeypatch.setattr(routes, "_core_fp", lambda: "abc123")
    monkeypatch.setattr(routes, "_relay_health", lambda: {"enabled": False, "ok": True, "stale": False})

    class Hermes:
        @staticmethod
        async def check_connection():
            return True

    class Scheduler:
        @staticmethod
        def get_job(_job_id: str):
            return None

        @staticmethod
        def get_jobs():
            return []

    schedule = {
        "morning": "08:50", "midday": "14:45", "tail": "14:52",
        "close": "15:30", "evening": "19:30", "archive": "22:00",
    }
    config = SimpleNamespace(
        client_name="client", client_id="id", client_brand="brand", client_brand_sub="sub",
        hermes_base_url="http://127.0.0.1", hermes_model="model", schedule=schedule,
        section=lambda name: {} if name != "ui" else {"nav_hidden": []},
    )
    store = SimpleNamespace(get_job_states=lambda: {})
    state = SimpleNamespace(
        hermes=Hermes(), config=config, scheduler=Scheduler(), store=store,
        feishu=SimpleNamespace(configured=False), core_integrity=None,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    payload = await routes.status(request)

    assert payload["market_session"] == "intraday"
    assert payload["recommended_route"] == "today"
    assert payload["market_context"] == context
    assert payload["route_lifecycle"]["schema_version"] == "route-lifecycle-v1"
    assert payload["task_center"]["schema_version"] == "task-center-v1"
    assert payload["jobs"] == payload["task_center"]["jobs"]
    assert payload["hermes"]["required_for_core"] is False
    assert payload["hermes"]["agent_capability"]["required"] is True
    assert payload["hermes"]["agent_capability"]["required_for_ai_calls"] is True
    assert payload["hermes"]["agent_capability"]["blocks_core"] is False


@pytest.mark.asyncio
async def test_agent_capability_scope_keeps_ai_calls_fail_closed() -> None:
    original = {
        "ready": False,
        "required": True,
        "status": "capability_unavailable",
        "reason": "Agent 权限预检未通过",
    }

    class Hermes:
        @staticmethod
        async def capability_preflight(*, force: bool):
            assert force is True
            return original

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(hermes=Hermes()))
    )

    payload = await routes.agent_capabilities(request, refresh=True)

    assert original == {
        "ready": False,
        "required": True,
        "status": "capability_unavailable",
        "reason": "Agent 权限预检未通过",
    }
    assert payload["capability"]["ready"] is False
    assert payload["capability"]["required"] is True
    assert payload["capability"]["required_for_ai_calls"] is True
    assert payload["capability"]["blocks_core"] is False
