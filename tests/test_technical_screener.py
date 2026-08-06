from __future__ import annotations

import pandas as pd

from hermes_trader.analysis import technical_screener


def test_empty_cache_is_explicitly_unavailable(tmp_path) -> None:
    technical_screener.init(tmp_path)
    status = technical_screener.status()
    assert status["state"] == "missing"
    result = technical_screener.screen({"must_all": [], "any_of": [], "exclude": []})
    assert result["ok"] is False
    assert result["state"] == "missing"


def test_summary_status_skips_large_record_counts(tmp_path) -> None:
    technical_screener.init(tmp_path)
    summary = technical_screener.status(include_counts=False)
    assert summary["state"] == "missing"
    assert summary["stocks"] == 0
    assert summary["bars"] is None
    assert summary["signal_matches"] is None
    assert summary["data_date"] is None


def test_init_repairs_stale_running_refresh_state(tmp_path) -> None:
    technical_screener.init(tmp_path)
    technical_screener._meta_set("refresh_state", {
        "state": "running", "started_at": "2026-08-05 16:10:00", "processed": 40,
    })

    technical_screener.init(tmp_path)
    current = technical_screener.status(include_counts=False)

    assert current["state"] == "missing"
    assert current["refresh"]["state"] == "interrupted"
    assert current["refresh"]["error_code"] == "refresh_interrupted_by_restart"


def test_scheme_round_trip_preserves_algorithm_version(tmp_path) -> None:
    technical_screener.init(tmp_path)
    saved = technical_screener.save_scheme({
        "name": "我的金叉",
        "default_timeframe": "D",
        "must_all": [{"signal_code": "MACD_GOLDEN_CROSS", "timeframe": "D"}],
        "any_of": [], "exclude": [],
    })
    assert saved["algorithm_ver"] == "local-signal-v1"
    assert saved["schemaVersion"] == 2
    assert saved["satisfy"]["groups"][0]["conditionMatch"] == "ALL"
    rows = technical_screener.list_schemes()
    assert rows[0]["name"] == "我的金叉"
    assert technical_screener.delete_scheme(saved["id"])["ok"] is True


def test_invalid_condition_is_rejected(tmp_path) -> None:
    technical_screener.init(tmp_path)
    checked = technical_screener.validate_request({
        "must_all": [{"signal_code": "NOT_A_SIGNAL", "timeframe": "D"}],
    })
    assert checked["ok"] is False
    assert checked["errors"][0]["code"] == "unknown_signal"
    bad_params = technical_screener.validate_request({
        "must_all": [{
            "signal_code": "VOLUME_BREAKOUT", "timeframe": "D",
            "occurrence": "TODAY", "params": {"period": 20, "multiplier": 0},
        }],
    })
    assert bad_params["ok"] is False
    assert any(row["code"] == "invalid_param" for row in bad_params["errors"])
    too_deep = technical_screener.validate_request({
        "must_all": [{"operator": "AND", "conditions": [
            {"operator": "OR", "conditions": [{"signal_code": "MACD_GOLDEN_CROSS", "timeframe": "D", "occurrence": "TODAY"}]},
        ]}],
    })
    assert any(row["code"] == "nested_group_too_deep" for row in too_deep["errors"])


def test_refresh_builds_cache_and_supports_default_and_custom_params(tmp_path, monkeypatch) -> None:
    technical_screener.init(tmp_path)
    dates = pd.bdate_range("2025-10-01", periods=100)
    bars = [{
        "d": stamp.date().isoformat(), "o": i + 9.8, "h": i + 10.5,
        "l": i + 9.5, "c": i + 10.0, "v_shares": 1_000_000 + i * 1000,
        "amount": (i + 10.0) * (1_000_000 + i * 1000),
        "adjustment": "qfq", "source": "测试前复权日K",
    } for i, stamp in enumerate(dates)]
    monkeypatch.setattr(technical_screener.free_source, "stock_universe", lambda: {"600001": "测试股份"})
    benchmark = [{**row, "c": 100 + i * 0.1} for i, row in enumerate(bars)]
    monkeypatch.setattr(
        technical_screener.tdx_local_daily,
        "daily_bars_batch",
        lambda codes, _days: {
            str(code).split(".")[0]: (benchmark if str(code).startswith("000300") else bars)
            for code in codes
        },
    )
    monkeypatch.setattr(
        technical_screener.tencent_source,
        "stock_daily_bars_uncached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("本地日线可用时不应访问腾讯")),
    )

    refreshed = technical_screener.refresh_all(max_symbols=1, workers=1)
    assert refreshed["state"] == "ready"
    assert refreshed["source_counts"] == {"tdx_day": 1, "tencent_fallback": 0}
    assert refreshed["max_pending"] == 2

    default_result = technical_screener.screen({
        "universe": {"markets": ["MAIN"], "min_listing_days": 60},
        "must_all": [{"signal_code": "LONG_MA_ARRAY", "timeframe": "D", "occurrence": "CURRENT"}],
        "any_of": [], "exclude": [],
    })
    assert [row["code"] for row in default_result["stocks"]] == ["600001"]

    custom_result = technical_screener.screen({
        "universe": {"markets": ["MAIN"], "min_listing_days": 60},
        "must_all": [{"signal_code": "CLOSE_ABOVE_MA", "timeframe": "D", "occurrence": "CURRENT", "params": {"period": 10}}],
        "any_of": [], "exclude": [],
    })
    assert custom_result["matched"] == 1
    assert custom_result["stocks"][0]["matched_reasons"][0]["metrics"]["ma"] > 0

    outperform = technical_screener.screen({
        "universe": {"markets": ["MAIN"], "min_listing_days": 60},
        "must_all": [{"signal_code": "WIN_MARKET_N", "timeframe": "D", "occurrence": "CURRENT", "params": {"period": 20}}],
        "any_of": [], "exclude": [],
    })
    assert outperform["matched"] == 1
    assert outperform["stocks"][0]["matched_reasons"][0]["metrics"]["outperform_pct"] > 0

    nested = technical_screener.screen({
        "universe": {"markets": ["MAIN"], "min_listing_days": 60},
        "must_all": [{"operator": "AND", "conditions": [
            {"signal_code": "LONG_MA_ARRAY", "timeframe": "D", "occurrence": "CURRENT"},
            {"signal_code": "CLOSE_ABOVE_MA", "timeframe": "D", "occurrence": "CURRENT", "params": {"period": 10}},
        ]}],
        "any_of": [], "exclude": [],
    })
    assert nested["matched"] == 1
    assert len(nested["stocks"][0]["matched_reasons"]) == 2


def test_refresh_fallback_is_uncached_and_work_is_bounded(tmp_path, monkeypatch) -> None:
    technical_screener.init(tmp_path)
    dates = pd.bdate_range("2026-01-01", periods=30)
    bars = [{
        "d": stamp.date().isoformat(), "o": 10.0, "h": 10.5, "l": 9.8,
        "c": 10.2, "v_shares": 100_000, "amount": 1_020_000,
        "adjustment": "qfq", "source": "腾讯测试前复权日K",
    } for stamp in dates]
    universe = {f"60000{index}": f"测试{index}" for index in range(5)}
    local_batches = []
    fallback_codes = []

    monkeypatch.setattr(technical_screener.free_source, "stock_universe", lambda: universe)

    def fake_local(codes, _days):
        local_batches.append(list(codes))
        if list(codes) == ["000300.SH"]:
            return {"000300": bars}
        return {}

    def fake_fallback(code, _days, **_kwargs):
        fallback_codes.append(code)
        return bars

    monkeypatch.setattr(technical_screener.tdx_local_daily, "daily_bars_batch", fake_local)
    monkeypatch.setattr(
        technical_screener.tencent_source, "stock_daily_bars_uncached", fake_fallback,
    )

    refreshed = technical_screener.refresh_all(workers=2)

    assert refreshed["state"] == "ready"
    assert refreshed["max_pending"] == 4
    assert refreshed["source_counts"] == {"tdx_day": 0, "tencent_fallback": 5}
    assert [len(group) for group in local_batches] == [1, 4, 1]
    assert sorted(fallback_codes) == sorted(universe)


def test_v2_structure_requires_non_empty_satisfy_groups(tmp_path) -> None:
    technical_screener.init(tmp_path)
    missing = technical_screener.validate_request({
        "schemaVersion": 2,
        "satisfy": {"groupMatch": "ALL", "groups": []},
        "exclude": {"groups": []},
    })
    assert missing["ok"] is False
    assert any(row["code"] == "missing_satisfy_group" for row in missing["errors"])

    empty = technical_screener.validate_request({
        "schemaVersion": 2,
        "satisfy": {"groupMatch": "ALL", "groups": [
            {"id": "sg_1", "conditionMatch": "ALL", "conditions": []},
        ]},
        "exclude": {"groups": []},
    })
    assert any(row["code"] == "empty_rule_group" for row in empty["errors"])
    assert any(row.get("group") == "satisfy.groups.0" for row in empty["errors"])


def test_legacy_migration_is_stable_and_refuses_lossy_flattening() -> None:
    legacy = {
        "name": "旧方案",
        "must_all": [{"signal_code": "LONG_MA_ARRAY", "timeframe": "D", "occurrence": "CURRENT"}],
        "any_of": [
            {"signal_code": "MACD_GOLDEN_CROSS", "timeframe": "D", "occurrence": "TODAY"},
            {"signal_code": "RSI_OVERSOLD_RECOVERY", "timeframe": "D", "occurrence": "TODAY"},
        ],
        "exclude": [{"signal_code": "SHORT_MA_ARRAY", "timeframe": "D", "occurrence": "CURRENT"}],
    }
    first = technical_screener.migrate_legacy_config(legacy)
    second = technical_screener.migrate_legacy_config(legacy)
    assert first == second
    assert first["ok"] is True
    config = first["config"]
    assert config["schemaVersion"] == 2
    assert config["satisfy"]["groupMatch"] == "ALL"
    assert [group["conditionMatch"] for group in config["satisfy"]["groups"]] == ["ALL", "ANY"]
    assert config["exclude"]["groups"][0]["conditionMatch"] == "ANY"

    incompatible = {
        "must_all": [{"signal_code": "LONG_MA_ARRAY", "timeframe": "D", "occurrence": "CURRENT"}],
        "any_of": [
            {"operator": "AND", "conditions": [
                {"signal_code": "MACD_GOLDEN_CROSS", "timeframe": "D", "occurrence": "TODAY"},
                {"signal_code": "CLOSE_ABOVE_MA", "timeframe": "D", "occurrence": "CURRENT"},
            ]},
            {"operator": "AND", "conditions": [
                {"signal_code": "RSI_OVERSOLD_RECOVERY", "timeframe": "D", "occurrence": "TODAY"},
                {"signal_code": "ADX_STRONG", "timeframe": "D", "occurrence": "CURRENT"},
            ]},
        ],
        "exclude": [],
    }
    failed = technical_screener.migrate_legacy_config(incompatible)
    assert failed["ok"] is False
    assert "无法无损" in failed["reason"]


def test_old_process_compatibility_payload_saves_as_clean_v2(tmp_path) -> None:
    technical_screener.init(tmp_path)
    editor_config = {
        "schemaVersion": 2,
        "satisfy": {"groupMatch": "ANY", "groups": [
            {"id": "sg_1", "conditionMatch": "ALL", "conditions": [
                {"id": "c_1", "signal_code": "LONG_MA_ARRAY", "timeframe": "D", "occurrence": "CURRENT"},
            ]},
            {"id": "sg_2", "conditionMatch": "ANY", "conditions": [
                {"id": "c_2", "signal_code": "MACD_GOLDEN_CROSS", "timeframe": "D", "occurrence": "TODAY"},
            ]},
        ]},
        "exclude": {"groups": []},
    }
    compatibility_payload = {
        "name": "旧进程兼容保存",
        "must_all": [],
        "any_of": [
            {"operator": "AND", "conditions": [{"signal_code": "LONG_MA_ARRAY", "timeframe": "D", "occurrence": "CURRENT"}]},
            {"operator": "OR", "conditions": [{"signal_code": "MACD_GOLDEN_CROSS", "timeframe": "D", "occurrence": "TODAY"}]},
        ],
        "exclude": [],
        "editor_config": editor_config,
    }
    saved = technical_screener.save_scheme(compatibility_payload)
    assert saved["ok"] is True
    assert saved["schemaVersion"] == 2
    assert saved["satisfy"]["groupMatch"] == "ANY"
    assert [group["conditionMatch"] for group in saved["satisfy"]["groups"]] == ["ALL", "ANY"]
    assert "editor_config" not in saved


def test_v2_screen_combines_two_levels_and_ignores_editor_ids_in_cache(tmp_path, monkeypatch) -> None:
    technical_screener.init(tmp_path)
    technical_screener._query_cache.clear()
    monkeypatch.setattr(technical_screener, "status", lambda: {
        "state": "ready", "stocks": 3, "data_date": "2026-07-23",
        "level2_available": False, "level2_reason": "未接入Level-2",
    })
    monkeypatch.setattr(technical_screener, "_eligible_stocks", lambda _universe: {
        code: {"code": code, "name": code, "industry": "测试", "last_price": 10.0, "change_pct": 1.0, "last_amount": 1_000_000, "avg_amount20": 1_000_000}
        for code in ("A", "B", "C")
    })
    matches = {
        "LONG_MA_ARRAY": {"A", "B"},
        "CLOSE_ABOVE_MA": {"B", "C"},
        "MACD_GOLDEN_CROSS": {"A"},
        "ADX_STRONG": {"B"},
        "RSI_OVERBOUGHT": {"B", "C"},
        "SHORT_MA_ARRAY": {"C"},
    }
    calls = []

    def fake_matches(condition):
        calls.append(condition["signal_code"])
        codes = matches[condition["signal_code"]]
        return codes, {code: {"signal_code": condition["signal_code"], "signal_name": condition["signal_code"], "timeframe": "D", "date": "2026-07-23", "metrics": {}, "is_confirmed": True} for code in codes}

    monkeypatch.setattr(technical_screener, "_is_default", lambda _condition: True)
    monkeypatch.setattr(technical_screener, "_default_matches", fake_matches)
    config = {
        "schemaVersion": 2,
        "satisfy": {"groupMatch": "ALL", "groups": [
            {"id": "sg_a", "conditionMatch": "ALL", "conditions": [
                {"id": "c_1", "signal_code": "LONG_MA_ARRAY", "timeframe": "D", "occurrence": "CURRENT"},
                {"id": "c_2", "signal_code": "CLOSE_ABOVE_MA", "timeframe": "D", "occurrence": "CURRENT"},
            ]},
            {"id": "sg_b", "conditionMatch": "ANY", "conditions": [
                {"id": "c_3", "signal_code": "MACD_GOLDEN_CROSS", "timeframe": "D", "occurrence": "TODAY"},
                {"id": "c_4", "signal_code": "ADX_STRONG", "timeframe": "D", "occurrence": "CURRENT"},
            ]},
        ]},
        "exclude": {"groups": [
            {"id": "eg_a", "conditionMatch": "ALL", "conditions": [
                {"id": "c_5", "signal_code": "RSI_OVERBOUGHT", "timeframe": "D", "occurrence": "CURRENT"},
                {"id": "c_6", "signal_code": "SHORT_MA_ARRAY", "timeframe": "D", "occurrence": "CURRENT"},
            ]},
        ]},
        "limit": 50,
    }
    result = technical_screener.screen(config)
    assert [row["code"] for row in result["stocks"]] == ["B"]
    assert {row["signal_code"] for row in result["stocks"][0]["matched_reasons"]} == {
        "LONG_MA_ARRAY", "CLOSE_ABOVE_MA", "ADX_STRONG",
    }
    first_call_count = len(calls)

    changed_ids = {
        **config,
        "satisfy": {**config["satisfy"], "groups": [
            {**group, "id": f"changed_{index}", "conditions": [{**condition, "id": f"changed_{index}_{child}"} for child, condition in enumerate(group["conditions"])]}
            for index, group in enumerate(config["satisfy"]["groups"])
        ]},
    }
    cached = technical_screener.screen(changed_ids)
    assert cached["stocks"] == result["stocks"]
    assert len(calls) == first_call_count
