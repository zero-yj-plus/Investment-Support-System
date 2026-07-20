from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from hermes_trader import mcp_server


ORIGINAL_TOOLS = {
    "sector_capital_ranking",
    "sector_capital_trend",
    "hot_stocks_capital",
    "stock_capital_trend",
    "capital_trends",
    "capital_snapshot",
    "market_overview",
    "stock_detail",
    "limit_up_ladder",
    "sector_heat",
    "market_news",
    "company_announcements",
    "company_management_interactions",
}

PLATFORM_READ_ONLY_TOOLS = {
    "official_longhu",
    "today_decision",
    "systemic_risk",
    "market_sentiment",
    "today_mainlines",
    "multi_period_performance",
    "sector_members",
    "trading_calendar",
    "data_health",
    "stock_research_enrichment",
    "mcp_capabilities",
    "news_event_history",
    "report_lineage",
    "daily_review_evidence",
    "task_status",
    "delivery_receipts",
}


def test_tool_catalog_preserves_original_tools_and_adds_platform_reads() -> None:
    names = set(mcp_server.mcp._tool_manager._tools)  # noqa: SLF001 -- MCP 注册契约

    assert ORIGINAL_TOOLS <= names
    assert PLATFORM_READ_ONLY_TOOLS <= names
    assert not {
        "push_report", "put_settings", "delete_connection", "send_message",
        "create_recommendation", "approve_recommendation", "execute_trade",
    } & names


def test_mcp_capabilities_makes_read_write_boundary_machine_readable(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "_write_tools_enabled", False)

    result = mcp_server.mcp_capabilities()

    assert result["ok"] is True
    assert result["default_mode"] == "read_only"
    assert result["forbidden_tool_names_registered"] == []
    assert result["optional_maintenance_writes"] == [{
        "tool": "capital_snapshot",
        "enabled": False,
        "scope": "append_market_capital_snapshot",
        "default": "disabled",
    }]
    assert "delivery_receipts" in result["read_only_tools"]
    assert "capital_snapshot" not in result["read_only_tools"]
    assert set(result["forbidden_capabilities"].values()) == {"not_exposed"}


@pytest.mark.asyncio
async def test_today_decision_reuses_web_score_lock_and_customer_store(monkeypatch, tmp_path) -> None:
    from hermes_trader.analysis import cockpit as cockpit_mod, score_lock

    seen = []
    pulse = {"command": {"risk_level": 3}, "senti": 47}
    store = SimpleNamespace(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_runtime_store", store)
    monkeypatch.setattr(cockpit_mod, "pulse_core", lambda store, _prev: seen.append(("pulse", store)) or pulse)
    monkeypatch.setattr(
        cockpit_mod,
        "cockpit",
        lambda store, snapshot: seen.append(("cockpit", store, snapshot)) or {"ok": True, "command": snapshot["command"]},
    )
    locked_calls = []

    async def unlocked(root, kind, compute):
        locked_calls.append((root, kind))
        return await compute(None)

    monkeypatch.setattr(score_lock, "locked", unlocked)

    result = await mcp_server.today_decision()

    assert result == {"ok": True, "command": {"risk_level": 3}, "pulse": pulse}
    assert seen == [("pulse", store), ("cockpit", store, pulse)]
    assert locked_calls == [(tmp_path, "pulse"), (tmp_path, "cockpit")]


@pytest.mark.asyncio
async def test_new_tool_exception_is_structured_and_does_not_leak_message(monkeypatch, tmp_path) -> None:
    from hermes_trader.analysis import cockpit as cockpit_mod, score_lock

    monkeypatch.setattr(mcp_server, "_runtime_store", SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(
        cockpit_mod,
        "pulse_core",
        lambda _store, _prev: (_ for _ in ()).throw(RuntimeError("https://secret.invalid?token=abc")),
    )

    async def unlocked(_root, _kind, compute):
        return await compute(None)

    monkeypatch.setattr(score_lock, "locked", unlocked)

    result = await mcp_server.market_sentiment()

    assert result["ok"] is False
    assert result["error_code"] == "tool_unavailable"
    assert result["error_type"] == "RuntimeError"
    assert "secret.invalid" not in repr(result)
    assert "token=abc" not in repr(result)


def test_capital_snapshot_is_denied_by_default_without_calling_writer(monkeypatch) -> None:
    from hermes_trader.data import capital_tracker

    monkeypatch.setattr(mcp_server, "_write_tools_enabled", False)
    monkeypatch.setattr(
        capital_tracker,
        "snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("writer must not run")),
    )

    result = mcp_server.capital_snapshot()

    assert result["ok"] is False
    assert result["error_code"] == "write_capability_disabled"


def test_original_tool_exception_also_uses_structured_error(monkeypatch) -> None:
    from hermes_trader.data import market

    monkeypatch.setattr(
        market,
        "market_overview",
        lambda: (_ for _ in ()).throw(TimeoutError("upstream timed out")),
    )

    result = mcp_server.market_overview()

    assert result["ok"] is False
    assert result["tool"] == "大盘概览"
    assert result["error_type"] == "TimeoutError"


def test_company_announcements_validates_bounds_and_avoids_pdf_proxy(monkeypatch) -> None:
    from hermes_trader.data import base, official_disclosure

    monkeypatch.setattr(
        base,
        "now_cn",
        lambda: __import__("datetime").datetime(2026, 7, 14, 9, 0),
    )
    seen = {}

    def fake_disclosures(code, start, end, limit, verify_pdf, hash_limit, cache_path):
        seen.update({
            "code": code,
            "start": start,
            "end": end,
            "limit": limit,
            "verify_pdf": verify_pdf,
            "hash_limit": hash_limit,
            "cache_path": cache_path,
        })
        return {"ok": True, "state": "complete", "items": []}

    monkeypatch.setattr(official_disclosure, "cached_official_disclosures", fake_disclosures)
    monkeypatch.setattr(mcp_server, "_runtime_config", None)

    invalid = mcp_server.company_announcements("920001")
    valid = mcp_server.company_announcements("600000", days=999, limit=999)

    assert invalid["error_code"] == "invalid_stock_code"
    assert valid["state"] == "complete"
    assert seen == {
        "code": "600000",
        "start": "2025-07-13",
        "end": "2026-07-14",
        "limit": 50,
        "verify_pdf": False,
        "hash_limit": 1,
        "cache_path": None,
    }


def test_official_longhu_reads_exact_customer_cache_without_network(monkeypatch, tmp_path) -> None:
    from hermes_trader.data import official_longhu_source

    monkeypatch.setattr(mcp_server, "_runtime_config", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(
        official_longhu_source,
        "fetch_official_longhu",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not run")),
    )
    seen = []

    def fake_load(trade_date):
        seen.append(trade_date)
        return {
            "ok": True,
            "state": "complete",
            "trade_date": trade_date,
            "fetched_at": "2026-07-13T18:30:00+08:00",
            "events": [{"code": "600000"}],
        }

    monkeypatch.setattr(official_longhu_source, "load_snapshot", fake_load)

    result = mcp_server.official_longhu("2026-07-13")

    assert seen == ["2026-07-13"]
    assert result["events"] == [{"code": "600000"}]
    assert result["source"] == "official_snapshot_cache"
    assert result["cache_only"] is True
    assert result["network_requested"] is False


def test_official_longhu_rejects_non_iso_date_before_cache_read(monkeypatch) -> None:
    from hermes_trader.data import official_longhu_source

    monkeypatch.setattr(mcp_server, "_runtime_config", SimpleNamespace(data_dir="/tmp/unused"))
    monkeypatch.setattr(
        official_longhu_source,
        "load_snapshot",
        lambda *_args: (_ for _ in ()).throw(AssertionError("cache must not be read")),
    )

    result = mcp_server.official_longhu("20260713")

    assert result["ok"] is False
    assert result["error_code"] == "invalid_date"


def test_management_interaction_tool_preserves_tier3_boundary(monkeypatch, tmp_path) -> None:
    from hermes_trader.data import base, official_interaction

    monkeypatch.setattr(mcp_server, "_runtime_config", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(
        base,
        "now_cn",
        lambda: __import__("datetime").datetime(2026, 7, 15, 9, 0),
    )
    seen = {}

    def fake_cached(code, start, end, limit, data_dir):
        seen.update(code=code, start=start, end=end, limit=limit, data_dir=data_dir)
        return {
            "ok": True,
            "state": "complete",
            "is_formal_disclosure": False,
            "items": [{"answered": True, "answer": "按计划推进"}],
        }

    monkeypatch.setattr(official_interaction, "cached_official_interactions", fake_cached)

    valid = mcp_server.company_management_interactions("000001", days=999, limit=999)
    unsupported = mcp_server.company_management_interactions("600000")

    assert valid["state"] == "complete"
    assert valid["is_formal_disclosure"] is False
    assert seen == {
        "code": "000001",
        "start": "2025-07-14",
        "end": "2026-07-15",
        "limit": 20,
        "data_dir": str(tmp_path),
    }
    assert unsupported["state"] == "not_supported"
    assert unsupported["items"] == []


@pytest.mark.asyncio
async def test_today_mainlines_is_current_snapshot_only(monkeypatch) -> None:
    from hermes_trader.analysis import mainline_runtime

    calls = []

    async def fake_build(limit):
        calls.append(limit)
        return {"ok": True, "data_date": "2026-07-13", "mainlines": []}

    monkeypatch.setattr(mainline_runtime, "build_current_response", fake_build)

    result = await mcp_server.today_mainlines(99)

    assert result["data_date"] == "2026-07-13"
    assert calls == [30]


def test_multi_period_performance_bounds_limit(monkeypatch) -> None:
    from hermes_trader.data import market

    monkeypatch.setattr(market, "market_multiperiod", lambda limit: {"ok": True, "limit": limit})

    assert mcp_server.multi_period_performance(0)["limit"] == 1
    assert mcp_server.multi_period_performance(200)["limit"] == 20


def test_sector_members_supports_stock_mapping(monkeypatch) -> None:
    from hermes_trader.data import tdx_api_source as tdx_api

    monkeypatch.setattr(
        tdx_api,
        "industry_for_codes",
        lambda codes: {codes[0]: {"industry": "证券", "sector_code": "881193.SH", "source": "TDX relation"}},
    )

    result = mcp_server.sector_members(stock_code="600030")

    assert result["ok"] is True
    assert result["mapping"]["industry"] == "证券"
    assert result["data_state"] == "complete"


def test_sector_members_returns_bounded_members_and_partial_state(monkeypatch) -> None:
    from hermes_trader.data import tdx_api_source as tdx_api

    monkeypatch.setattr(
        tdx_api,
        "sector_catalog",
        lambda: {
            "881105.SH": {
                "code": "881105.SH",
                "name": "银行",
                "kind": "industry",
                "data_date": "2026-07-13",
            }
        },
    )
    monkeypatch.setattr(
        tdx_api,
        "sector_memberships",
        lambda: {
            "by_sector": {"881105.SH": ("600000", "600036", "601398")},
            "data_date": "2026-07-13",
        },
    )
    monkeypatch.setattr(tdx_api, "stock_universe", lambda: {"600000": "浦发银行", "600036": "招商银行"})

    result = mcp_server.sector_members(sector="881105", limit=2)

    assert result["ok"] is True
    assert result["member_count"] == 3
    assert result["returned"] == 2
    assert result["data_state"] == "degraded"
    assert result["members"][0] == {"code": "600000", "name": "浦发银行"}


def test_trading_calendar_exposes_quality_and_adjacent_days(monkeypatch) -> None:
    from hermes_trader.data import base

    target = date(2026, 7, 13)
    monkeypatch.setattr(
        base,
        "trade_day_status",
        lambda day: {
            "status": "open",
            "is_trade_day": True,
            "source": "calendar-test",
            "as_of": day.isoformat(),
            "quality": "derived",
        },
    )
    monkeypatch.setattr(base, "prev_trade_day", lambda day: "2026-07-10" if day == target else None)
    monkeypatch.setattr(base, "next_trade_day", lambda day: "2026-07-14" if day == target else None)

    result = mcp_server.trading_calendar("2026-07-13")

    assert result["ok"] is True
    assert result["status"]["quality"] == "derived"
    assert result["previous_trade_day"] == "2026-07-10"
    assert result["next_trade_day"] == "2026-07-14"


def test_data_health_reuses_source_report_and_redacted_tdx_health(monkeypatch) -> None:
    from hermes_trader.data import free_source, sources
    from hermes_trader.data import tdx_api_source as tdx_api

    monkeypatch.setattr(tdx_api, "service_health", lambda: {"configured": True, "connected": True})
    monkeypatch.setattr(sources, "build_report", lambda infra: {"ok": True, "infra": infra, "sources": []})
    monkeypatch.setattr(free_source, "stock_universe_status", lambda: {"status": "ready", "count": 5400})

    result = mcp_server.data_health()

    assert result["ok"] is True
    assert result["tdx_api"] == {"configured": True, "connected": True}
    assert result["universe"]["count"] == 5400
    assert "base_url" not in repr(result)
    assert "api_key" not in repr(result)


def test_stock_research_enrichment_validates_code_and_reuses_tdx_path(monkeypatch) -> None:
    from hermes_trader.data import tdx_api_source as tdx_api

    monkeypatch.setattr(
        tdx_api,
        "after_hours_research",
        lambda code: {"ok": True, "code": code, "scope": "after_hours_research_only"},
    )

    invalid = mcp_server.stock_research_enrichment("abc")
    valid = mcp_server.stock_research_enrichment("600519")

    assert invalid["error_code"] == "invalid_stock_code"
    assert valid == {"ok": True, "code": "600519", "scope": "after_hours_research_only"}


def test_news_event_history_reads_existing_ledger_without_refreshing_sources(monkeypatch) -> None:
    from hermes_trader.data import news_observation

    monkeypatch.setattr(mcp_server, "_ensure_news_observation_reader", lambda: True)
    seen = []
    monkeypatch.setattr(
        news_observation,
        "history",
        lambda limit: seen.append(limit) or {
            "ok": True,
            "items": [{"event_id": "evt_1", "causality": "not_inferred"}],
        },
    )

    result = mcp_server.news_event_history(9999)

    assert seen == [500]
    assert result["items"][0]["causality"] == "not_inferred"
    assert result["ledger_only"] is True
    assert result["network_requested"] is False


def test_news_reader_attaches_existing_files_without_calling_writer_init(monkeypatch, tmp_path) -> None:
    from hermes_trader.data import news_observation

    ledger_root = tmp_path / "news_events"
    ledger_root.mkdir()
    (ledger_root / "events.v1.jsonl").write_text("", encoding="utf-8")
    (ledger_root / "observations.v1.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(news_observation, "_store", None)
    monkeypatch.setattr(
        news_observation,
        "init",
        lambda *_args: (_ for _ in ()).throw(AssertionError("read tool must not initialize writer")),
    )
    monkeypatch.setattr(mcp_server, "_runtime_store", SimpleNamespace(root=tmp_path))
    before = {path.name: path.stat().st_mtime_ns for path in ledger_root.iterdir()}

    assert mcp_server._ensure_news_observation_reader() is True

    after = {path.name: path.stat().st_mtime_ns for path in ledger_root.iterdir()}
    assert news_observation.available() is True
    assert before == after


def test_report_lineage_returns_audit_metadata_but_never_report_body(monkeypatch, tmp_path) -> None:
    from hermes_trader.analysis.report_lineage import build_lineage
    from hermes_trader.knowledge.store import Store

    store = Store(tmp_path)
    lineage = build_lineage(
        trade_day="2026-07-13",
        evidence=({"kind": "news_event_observation", "content_hash": "e" * 64},),
    )
    store.save_report(
        "close",
        "收盘复盘",
        "PRIVATE REPORT BODY",
        date="2026-07-13",
        input_lineage=lineage,
        input_context={"收盘快照": {"上证": -1.2}},
        claim_validation={"ok": True, "status": "passed"},
    )
    monkeypatch.setattr(mcp_server, "_runtime_store", store)

    result = mcp_server.report_lineage("close", "2026-07-13")

    assert result["ok"] is True
    assert result["report"]["content_id"] == "report:close:2026-07-13"
    assert len(result["report"]["content_hash"]) == 64
    assert result["input_lineage"]["evidence"][0]["kind"] == "news_event_observation"
    assert result["input_snapshot"]["captured"] is True
    assert result["content_included"] is False
    assert "PRIVATE REPORT BODY" not in repr(result)
    assert "input_snapshot_file" not in repr(result)


def test_report_lineage_rejects_path_like_report_type_before_store_read(monkeypatch) -> None:
    store = SimpleNamespace(
        today=lambda: "2026-07-13",
        get_report=lambda *_args: (_ for _ in ()).throw(AssertionError("store must not be read")),
    )
    monkeypatch.setattr(mcp_server, "_runtime_store", store)

    result = mcp_server.report_lineage("../../settings", "2026-07-13")

    assert result["ok"] is False
    assert result["error_code"] == "invalid_report_type"


def test_daily_review_evidence_joins_close_lineage_and_ledger_only_reconciliation(
    monkeypatch, tmp_path,
) -> None:
    from hermes_trader.data import news_observation
    from hermes_trader.knowledge.store import Store

    store = Store(tmp_path)
    store.save_report(
        "close",
        "收盘复盘",
        "DO NOT EXPOSE THIS BODY",
        date="2026-07-13",
        input_lineage={
            "schema_version": "daily-report-lineage-v1",
            "trade_day": "2026-07-13",
            "sources": [],
            "evidence": [{"kind": "news_event_observation", "verdict": "证据不足"}],
            "lineage_hash": "a" * 64,
        },
    )
    monkeypatch.setattr(mcp_server, "_runtime_store", store)
    monkeypatch.setattr(mcp_server, "_ensure_news_observation_reader", lambda: True)
    seen = []
    monkeypatch.setattr(
        news_observation,
        "daily_reconciliation",
        lambda day, limit: seen.append((day, limit)) or {
            "ok": True,
            "trade_day": day,
            "verdict": "证据不足",
            "causality": "not_inferred",
            "items": [],
        },
    )

    result = mcp_server.daily_review_evidence("2026-07-13", 9999)

    assert seen == [("2026-07-13", 500)]
    assert result["state"] == "complete"
    assert result["report_evidence"]["input_lineage"]["evidence"][0]["verdict"] == "证据不足"
    assert result["news_event_reconciliation"]["causality"] == "not_inferred"
    assert result["ledger_only"] is True
    assert result["network_requested"] is False
    assert "DO NOT EXPOSE THIS BODY" not in repr(result)


def test_task_status_uses_persisted_state_and_redacts_raw_errors(monkeypatch) -> None:
    store = SimpleNamespace(
        get_job_states=lambda: {
            "close": {
                "last_run": "2026-07-13 15:30:00",
                "last_status": "error",
                "last_error": "Authorization=top-secret https://secret.invalid",
                "last_error_code": "report_not_generated",
                "last_push_ok": False,
            },
            "ext_quant_pick": {
                "last_status": "ok",
                "last_finished": "2026-07-13 15:36:00",
            },
        },
    )
    monkeypatch.setattr(mcp_server, "_runtime_store", store)
    monkeypatch.setattr(
        mcp_server,
        "_runtime_config",
        SimpleNamespace(schedule={"close": "15:30"}),
    )

    result = mcp_server.task_status()
    by_id = {row["job_id"]: row for row in result["jobs"]}

    assert result["ok"] is True
    assert result["source"] == "persisted_jobs_state"
    assert result["scheduler_registration_available"] is False
    assert result["capabilities"] == {"manual_run": False, "manual_retry": False}
    assert by_id["close"]["configured_time"] == "15:30"
    assert by_id["close"]["next_run_at"] is None
    assert by_id["ext_quant_pick"]["kind"] == "extension"
    assert "top-secret" not in repr(result)
    assert "secret.invalid" not in repr(result)


def test_delivery_receipts_reads_filtered_public_metadata_only(monkeypatch, tmp_path) -> None:
    from hermes_trader.delivery import DeliveryReceiptStore
    from hermes_trader.knowledge.store import Store

    store = Store(tmp_path)
    ledger = DeliveryReceiptStore(store)
    ledger.record(
        content_id="report:close:2026-07-13",
        content_hash="a" * 64,
        channel="gateway",
        status="failed_terminal",
        error="Authorization=top-secret",
        error_code="push_exception",
        target_results=[{"target": "private-chat-id", "status": "failed"}],
    )
    ledger.record(
        content_id="report:close:2026-07-13",
        content_hash="a" * 64,
        channel="gateway",
        status="accepted",
        message_id="msg_1",
    )
    monkeypatch.setattr(mcp_server, "_runtime_store", store)

    result = mcp_server.delivery_receipts(10, "accepted")

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["receipts"][0]["status"] == "accepted"
    assert result["receipts"][0]["content_hash"] == "a" * 64
    assert result["content_included"] is False
    assert result["network_requested"] is False
    assert "target_results" not in repr(result)
    assert "private-chat-id" not in repr(result)
    assert "top-secret" not in repr(result)


def test_delivery_receipts_rejects_unknown_status_before_ledger_read(monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "_runtime_store", SimpleNamespace(root="/tmp/unused"))

    result = mcp_server.delivery_receipts(status="sent-ish")

    assert result["ok"] is False
    assert result["error_code"] == "invalid_delivery_status"
