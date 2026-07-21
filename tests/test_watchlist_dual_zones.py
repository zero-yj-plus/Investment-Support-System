from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from hermes_trader.analysis import watchlist_zones
from hermes_trader.knowledge.store import Store


def _daily_rows(count: int = 60) -> list[dict]:
    start = datetime(2026, 1, 1)
    rows = []
    for index in range(count):
        close = 10.0 + index * 0.05 + (0.12 if index % 6 == 0 else 0.0)
        low = close - (0.55 if index % 9 == 4 else 0.28)
        high = close + 0.32
        volume = 1_000_000 + index * 1_000
        rows.append({
            "d": (start + timedelta(days=index)).date().isoformat(),
            "o": close - 0.08, "h": high, "l": low, "c": close,
            "v_shares": volume, "amount": close * volume,
        })
    return rows


def _minute_rows(days: int = 3, bars_per_day: int = 20) -> list[dict]:
    start = datetime(2026, 3, 10, 9, 35)
    rows = []
    for day in range(days):
        for index in range(bars_per_day):
            close = 12.88 + day * 0.03 + (index % 7) * 0.01
            low = close - (0.10 if index % 8 == 3 else 0.04)
            high = close + 0.05
            volume = 20_000 + index * 100
            stamp = start + timedelta(days=day, minutes=index * 5)
            rows.append({
                "day": stamp.strftime("%Y-%m-%d %H:%M:%S"),
                "open": close - 0.01, "high": high, "low": low, "close": close,
                "volume": volume, "amount": close * volume,
            })
    return rows


def test_dual_zone_calculation_is_complete_and_reproducible() -> None:
    result = watchlist_zones.calculate_dual_zones(
        "600000", _daily_rows(), _minute_rows(), latest_price=12.96,
        calculated_at=datetime(2026, 4, 1, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result["ok"] is True
    assert result["input_counts"] == {"daily": 60, "minute_5m": 60, "minute_trade_days": 3}
    assert result["ultra"]["state"] == "complete"
    assert result["swing"]["state"] == "complete"
    for cycle in ("ultra", "swing"):
        zone = result[cycle]
        assert zone["low"] < zone["high"]
        assert zone["position"] in {"ABOVE_ZONE", "IN_ZONE", "BELOW_ZONE"}
        assert zone["low"] == pytest.approx(round(zone["low"], 2))
        assert zone["high"] == pytest.approx(round(zone["high"], 2))


def test_data_shortage_is_missing_instead_of_zero_or_guess() -> None:
    result = watchlist_zones.calculate_dual_zones(
        "600000", _daily_rows(59), _minute_rows(2), latest_price=12.0,
        calculated_at=datetime(2026, 4, 1, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result["ok"] is False
    assert result["ultra"]["state"] == "missing"
    assert result["swing"]["state"] == "missing"
    assert result["ultra"]["low"] is None
    assert any("日线不足60根" in reason for reason in result["missing"])
    assert any("5分钟线不足3个交易日" in reason for reason in result["missing"])


def test_intraday_swing_zone_uses_last_close_not_moving_quote() -> None:
    calculated_at = datetime(2026, 4, 1, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    lower_quote = watchlist_zones.calculate_dual_zones(
        "600000", _daily_rows(), _minute_rows(), latest_price=12.96, calculated_at=calculated_at,
    )
    higher_quote = watchlist_zones.calculate_dual_zones(
        "600000", _daily_rows(), _minute_rows(), latest_price=13.20, calculated_at=calculated_at,
    )

    assert lower_quote["swing"]["low"] == higher_quote["swing"]["low"]
    assert lower_quote["swing"]["high"] == higher_quote["swing"]["high"]
    assert lower_quote["swing"]["metrics"]["reference_price"] == pytest.approx(
        _daily_rows()[-1]["c"], abs=0.01
    )


def test_manual_zone_overrides_auto_and_new_day_expires_old_ultra() -> None:
    snapshot = {
        "schema_version": watchlist_zones.SCHEMA_VERSION,
        "algorithm_version": "2.0",
        "ok": True,
        "ultra": {"state": "complete", "low": 10.0, "high": 10.5, "data_date": "2026-07-20", "missing": []},
        "swing": {"state": "complete", "low": 9.5, "high": 10.2, "data_date": "2026-07-20", "missing": []},
        "missing": [],
    }
    quote = {"price": 10.3, "as_of": "2026-07-21"}

    expired = watchlist_zones.enrich_watch_item(
        {"code": "600000", "target_low": None, "target_high": None}, snapshot, quote,
    )
    assert expired["target_source"] == "missing"
    assert expired["target_low"] is None
    assert expired["ideal_buy_zones"]["ultra"]["state"] == "missing"

    manual = watchlist_zones.enrich_watch_item(
        {"code": "600000", "target_low": 9.8, "target_high": 10.1}, snapshot, quote,
    )
    assert manual["target_source"] == "manual_override"
    assert (manual["target_low"], manual["target_high"]) == (9.8, 10.1)


def test_auto_zone_drives_effective_target_and_grade() -> None:
    snapshot = {
        "ok": True,
        "ultra": {"state": "complete", "low": 10.0, "high": 10.5, "data_date": "2026-07-21", "missing": []},
        "swing": {"state": "complete", "low": 9.5, "high": 10.2, "data_date": "2026-07-20", "missing": []},
    }
    merged = watchlist_zones.enrich_watch_item(
        {"code": "600000", "target_low": None, "target_high": None},
        snapshot, {"price": 10.3, "as_of": "2026-07-21"},
    )

    assert merged["target_source"] == "auto_ultra"
    assert (merged["target_low"], merged["target_high"]) == (10.0, 10.5)
    assert merged["ideal_buy_zones"]["ultra"]["position"] == "IN_ZONE"


def test_zone_snapshots_are_stored_separately_from_user_watchlist(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save_watchlist_zones({"600000": {"ok": True, "ultra": {"low": 10.0}}})
    store.upsert_watchlist_zone("000001", {"ok": False, "missing": ["数据不足"]})

    assert set(store.watchlist_zones()) == {"600000", "000001"}
    assert store.watchlist() == []
