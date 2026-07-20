from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts.production_acceptance import (
    CORE_ROUTES,
    HttpObservation,
    collect_snapshot,
    evaluate_acceptance,
    exit_code_for,
)


CN = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 13, 10, 0, tzinfo=CN)
SPA = '<!doctype html><div id="app"></div><script type="module" src="js/app.js"></script>'


def _json_observation(key: str, data: dict, status: int = 200) -> HttpObservation:
    return HttpObservation(
        key=key,
        url=f"http://127.0.0.1:18869/{key}",
        status_code=status,
        data=data,
        text="{}",
    )


def _html_observation(key: str, text: str = SPA, status: int = 200) -> HttpObservation:
    return HttpObservation(
        key=key,
        url=f"http://127.0.0.1:18869/#/{key}",
        status_code=status,
        text=text,
    )


def ready_snapshot() -> dict[str, HttpObservation]:
    completed_at = (NOW - timedelta(minutes=3)).isoformat()
    safe_run = {
        "run_id": "ar_safe",
        "status": "completed",
        "finish_reason": "stop",
        "capability_status": "restricted",
        "tool_events": 0,
        "partial_output": False,
        "safe_to_use": True,
        "completed_at": completed_at,
        "error_code": None,
    }
    status = {
        "ok": True,
        "version": "1.0.0",
        "core": {"ok": True, "status": "verified"},
        "market_context": {
            "timezone": "Asia/Shanghai",
            "now": NOW.isoformat(),
            "date": "2026-07-13",
            "is_trade_day": True,
            "trade_day": "2026-07-13",
            "previous_trade_day": "2026-07-10",
            "next_trade_day": "2026-07-14",
            "market_session": "intraday",
            "recommended_route": "today",
        },
        "route_lifecycle": {
            "schema_version": "route-lifecycle-v1",
            "canonical": [
                {"path": route, "lifecycle": "visible"} for route in CORE_ROUTES
            ],
        },
        "hermes": {
            "connected": True,
            "optional": True,
            "custom_connection": True,
            "run_summary": {"counts": {"completed": 1}, "last_run": safe_run},
        },
        "recommendation_ledger": {
            "schema_version": "recommendation-event.v1",
            "startup_recovery": {"recovered": 0, "failed": []},
        },
    }
    datasources = {
        "ok": True,
        "source_contract": "field-route-v2",
        "active_policy": "free_only",
        "ts": NOW.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": [
            {
                "key": "tencent", "name": "腾讯行情", "provider": "qt.gtimg.cn",
                "role": "免费实时主源", "status": "ok", "channel": "腾讯直连",
            }
        ],
        "universe": {
            "count": 5350,
            "complete": True,
            "stale": False,
            "refresh_required": False,
            "missing_exchanges": [],
        },
        "deployment": {
            "profile": "cn_server",
            "configured": "cn_server",
            "quote_primary": "tdx_api",
            "quote_fallback": "tencent",
            "china_public_route": "direct",
            "relay_configured": False,
        },
    }
    capability = {
        "ok": True,
        "capability": {
            "ready": True,
            "status": "restricted",
            "reason": "服务端工具权限已收敛",
            "runtime_mode": "server_agent",
            "tool_execution": "server",
            "enabled_toolsets": [],
            "unexpected_toolsets": [],
            "profile_attestation": "attested",
            "process_attestation": "bound",
        },
    }
    delivery = {
        "ok": True,
        "launch_ready": True,
        "status": "ready",
        "configured": True,
        "channel": "feishu",
        "targets": ["customer-group"],
        "blockers": [],
        "manual_review_count": 0,
    }
    simulation = {
        "ok": True,
        "mode": "simulation_only",
        "simulation_only": True,
        "broker_connected": False,
        "broker_interface_present": False,
        "manual_approval_required": True,
        "emergency_stop": {"active": False, "reason": None, "updated_at": None},
    }
    snapshot = {
        "status": _json_observation("status", status),
        "datasources": _json_observation("datasources", datasources),
        "capabilities": _json_observation("capabilities", capability),
        "agent_runs": _json_observation("agent_runs", {"ok": True, "items": [safe_run]}),
        "delivery": _json_observation("delivery", delivery),
        "simulation": _json_observation("simulation", simulation),
        "settings": _json_observation(
            "settings", {"ok": True, "settings": {
                "hermes_api_key_set": True,
                "model_connection_custom": True,
            }}
        ),
        "root": _html_observation("root"),
    }
    snapshot.update({f"route:{route}": _html_observation(route) for route in CORE_ROUTES})
    return snapshot


def _check(report: dict, check_id: str) -> dict:
    return next(check for check in report["checks"] if check["id"] == check_id)


def test_ready_snapshot_passes_every_launch_blocker() -> None:
    report = evaluate_acceptance(ready_snapshot(), now=NOW)

    assert report["launch_ready"] is True
    assert report["summary"] == {"pass": 12, "block": 0, "warn": 0}
    assert exit_code_for(report) == 0
    assert all(check["status"] == "pass" for check in report["checks"])


def test_versioned_app_asset_is_still_recognized_as_spa_shell() -> None:
    snapshot = ready_snapshot()
    versioned = SPA.replace('js/app.js"', 'js/app.js?v=1.3.0.dev1"')
    snapshot["root"] = _html_observation("root", text=versioned)
    snapshot.update(
        {
            f"route:{route}": _html_observation(route, text=versioned)
            for route in CORE_ROUTES
        }
    )

    report = evaluate_acceptance(snapshot, now=NOW)

    assert _check(report, "http_status")["status"] == "pass"
    assert _check(report, "core_routes")["status"] == "pass"


def _paid_policy(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["datasources"].data["active_policy"] = "paid_first"


def _legacy_field_route_contract(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["datasources"].data["source_contract"] = "field-route-v1"


def _active_paid_source(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["datasources"].data["sources"].append(
        {"key": "tushare", "name": "Tushare Pro", "provider": "Tushare", "status": "ok"}
    )


def _active_paid_token(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["datasources"].data["tushare_token_set"] = True


def _active_paid_token_hidden_in_settings(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["settings"].data["settings"]["tushare_token_set"] = True


def _unexpected_agent_tool(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["capabilities"].data["capability"]["unexpected_toolsets"] = ["terminal"]


def _unsafe_completed_run(snapshot: dict[str, HttpObservation]) -> None:
    run = snapshot["agent_runs"].data["items"][0]
    run["safe_to_use"] = False
    run["tool_events"] = 1


def _delivery_unconfigured(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["delivery"].data.update(
        launch_ready=False,
        status="blocked",
        configured=False,
        targets=[],
        blockers=["push_channel_unconfigured"],
    )


def _simulation_broker_leak(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["simulation"].data["broker_connected"] = True


def _stale_incomplete_data(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["datasources"].data["ts"] = "2026-07-13 08:00:00"
    snapshot["datasources"].data["universe"].update(
        complete=False,
        stale=True,
        refresh_required=True,
        missing_exchanges=["bj"],
    )


def _missing_route(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["route:today"] = _html_observation("today", text="not an app", status=200)


def _deployment_left_auto(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["datasources"].data["deployment"]["configured"] = "auto"


def _recommendation_recovery_failed(snapshot: dict[str, HttpObservation]) -> None:
    snapshot["status"].data["recommendation_ledger"]["startup_recovery"] = {
        "recovered": 0,
        "failed": ["pending-intent-1"],
    }


@pytest.mark.parametrize(
    ("mutate", "blocked_check"),
    [
        (_paid_policy, "free_data_policy"),
        (_legacy_field_route_contract, "free_data_policy"),
        (_active_paid_source, "paid_sources"),
        (_active_paid_token, "paid_sources"),
        (_active_paid_token_hidden_in_settings, "paid_sources"),
        (_unexpected_agent_tool, "agent_capability"),
        (_unsafe_completed_run, "agent_last_run"),
        (_delivery_unconfigured, "delivery_readiness"),
        (_simulation_broker_leak, "simulation_boundary"),
        (_stale_incomplete_data, "data_freshness"),
        (_deployment_left_auto, "deployment_topology"),
        (_recommendation_recovery_failed, "recommendation_recovery"),
        (_missing_route, "core_routes"),
    ],
)
def test_each_unsafe_fake_response_blocks_launch(mutate, blocked_check: str) -> None:
    snapshot = deepcopy(ready_snapshot())
    mutate(snapshot)

    report = evaluate_acceptance(snapshot, now=NOW)

    assert report["launch_ready"] is False
    assert exit_code_for(report) == 2
    assert _check(report, blocked_check)["status"] == "block"


def test_missing_agent_run_blocks_unproven_external_provider_auth() -> None:
    snapshot = ready_snapshot()
    snapshot["agent_runs"].data["items"] = []
    snapshot["status"].data["hermes"]["run_summary"]["last_run"] = None

    report = evaluate_acceptance(snapshot, now=NOW)
    check = _check(report, "agent_last_run")

    assert report["launch_ready"] is False
    assert check["status"] == "block"
    assert "provider" in check["detail"]


def test_unconfigured_optional_hermes_does_not_block_deterministic_core() -> None:
    snapshot = ready_snapshot()
    snapshot["status"].data["hermes"].update(
        connected=False,
        optional=True,
        custom_connection=False,
        run_summary={"counts": {}, "last_run": None},
    )
    snapshot["settings"].data["settings"].update(
        hermes_api_key_set=False,
        model_connection_custom=False,
    )
    snapshot["capabilities"].data["capability"] = {
        "ready": False,
        "status": "model_connection_optional",
        "reason": "AI 增强未配置",
    }
    snapshot["agent_runs"].data["items"] = []

    report = evaluate_acceptance(snapshot, now=NOW)

    assert report["launch_ready"] is True
    capability = _check(report, "agent_capability")
    run = _check(report, "agent_last_run")
    assert capability["status"] == "pass"
    assert capability["evidence"]["skipped"] is True
    assert run["status"] == "pass"
    assert run["evidence"]["skipped"] is True


@pytest.mark.parametrize("failure", ["connection", "key"])
def test_explicitly_enabled_optional_hermes_keeps_strict_connection_and_key_gate(
    failure: str,
) -> None:
    snapshot = ready_snapshot()
    if failure == "connection":
        snapshot["status"].data["hermes"]["connected"] = False
    else:
        snapshot["settings"].data["settings"]["hermes_api_key_set"] = False

    report = evaluate_acceptance(snapshot, now=NOW)

    assert report["launch_ready"] is False
    assert _check(report, "agent_capability")["status"] == "block"


def test_optional_hermes_does_not_relax_non_agent_delivery_gate() -> None:
    snapshot = ready_snapshot()
    snapshot["status"].data["hermes"].update(
        connected=False, optional=True, custom_connection=False,
    )
    snapshot["settings"].data["settings"].update(
        hermes_api_key_set=False, model_connection_custom=False,
    )
    snapshot["agent_runs"].data["items"] = []
    _delivery_unconfigured(snapshot)

    report = evaluate_acceptance(snapshot, now=NOW)

    assert report["launch_ready"] is False
    assert _check(report, "agent_capability")["status"] == "pass"
    assert _check(report, "agent_last_run")["status"] == "pass"
    assert _check(report, "delivery_readiness")["status"] == "block"


def test_approved_direct_model_capability_with_no_tools_can_pass() -> None:
    snapshot = ready_snapshot()
    capability = snapshot["capabilities"].data["capability"]
    capability.update(
        status="direct_model_api",
        runtime_mode="direct_model_api",
        tool_execution="none",
        enabled_toolsets=[],
        unexpected_toolsets=[],
    )
    capability.pop("profile_attestation", None)
    capability.pop("process_attestation", None)
    snapshot["agent_runs"].data["items"][0]["capability_status"] = "direct_model_api"

    report = evaluate_acceptance(snapshot, now=NOW)

    assert _check(report, "agent_capability")["status"] == "pass"
    assert _check(report, "agent_last_run")["status"] == "pass"
    assert report["launch_ready"] is True


def test_old_safe_run_no_longer_proves_provider_auth() -> None:
    snapshot = ready_snapshot()
    old = (NOW - timedelta(days=2)).isoformat()
    snapshot["agent_runs"].data["items"][0]["completed_at"] = old

    report = evaluate_acceptance(snapshot, now=NOW)

    assert _check(report, "agent_last_run")["status"] == "block"
    assert report["launch_ready"] is False


def test_missing_universe_and_down_optional_source_warn_without_faking_pass() -> None:
    snapshot = ready_snapshot()
    snapshot["datasources"].data.pop("universe")
    snapshot["datasources"].data["sources"][0]["status"] = "down"

    report = evaluate_acceptance(snapshot, now=NOW)

    assert report["launch_ready"] is True
    assert _check(report, "data_freshness")["status"] == "warn"
    assert report["summary"]["warn"] == 1


def test_collect_snapshot_uses_only_read_only_get_observations() -> None:
    calls: list[tuple[str, str, float]] = []

    def fake_fetcher(key: str, url: str, timeout: float) -> HttpObservation:
        calls.append((key, url, timeout))
        if key == "root" or key.startswith("route:"):
            return HttpObservation(key, url, 200, text=SPA)
        return HttpObservation(key, url, 200, data={"ok": True}, text="{}")

    snapshot = collect_snapshot("127.0.0.1:18869", timeout=3.5, fetcher=fake_fetcher)

    assert set(snapshot) == {
        "status", "datasources", "capabilities", "agent_runs", "delivery", "settings",
        "simulation", "root",
        *(f"route:{route}" for route in CORE_ROUTES),
    }
    assert all(url.startswith("http://127.0.0.1:18869/") for _, url, _ in calls)
    assert all(timeout == 3.5 for _, _, timeout in calls)
    assert all("/api/delivery/preflight" not in url and "/api/jobs/" not in url for _, url, _ in calls)
