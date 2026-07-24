from __future__ import annotations

import pandas as pd

from hermes_trader.analysis import technical_signals as signals


def _bars(closes: list[float]) -> list[dict]:
    rows = []
    dates = pd.bdate_range("2026-01-01", periods=len(closes))
    for i, (date, close) in enumerate(zip(dates, closes), 1):
        rows.append({
            "d": date.date().isoformat(),
            "o": close - 0.2,
            "h": close + 0.5,
            "l": close - 0.5,
            "c": close,
            "v_shares": 1_000_000 + i * 10_000,
            "amount": close * (1_000_000 + i * 10_000),
            "adjustment": "qfq",
        })
    return rows


def test_cross_helpers_keep_nan_false_and_emit_once() -> None:
    a = pd.Series([None, 1.0, 2.0, 3.0])
    b = pd.Series([1.0, 2.0, 1.5, 1.0])
    assert signals.cross_up(a, b).tolist() == [False, False, True, False]
    assert signals.cross_down(b, a).tolist() == [False, False, True, False]


def test_price_breakout_excludes_current_bar() -> None:
    frame = signals.normalized_frame(_bars([10, 11, 12, 13, 20]))
    matched = signals.evaluate_condition(
        frame,
        {"signal_code": "PRICE_BREAKOUT_N", "params": {"period": 4}},
    )
    assert matched["mask"].tolist() == [False, False, False, False, True]


def test_weekly_resample_recalculates_ohlcv() -> None:
    rows = _bars([10, 11, 12, 13, 14, 15, 16, 17, 18, 19])
    frame = signals.normalized_frame(rows)
    weekly = signals.resample_frame(frame, "W", include_partial=False)
    assert len(weekly) == 2
    assert weekly.iloc[0]["open"] == rows[0]["o"]
    assert weekly.iloc[1]["close"] == rows[6]["c"]
    assert weekly.iloc[1]["volume"] == sum(row["v_shares"] for row in rows[2:7])


def test_catalog_marks_level2_signals_unavailable_without_capability() -> None:
    catalog = signals.signal_catalog(level2_available=False)
    ddx = next(row for row in catalog if row["signal_code"] == "DDX_POSITIVE")
    assert ddx["available"] is False
    assert "Level-2" in ddx["unavailable_reason"]


def test_default_signal_events_do_not_repeat_state() -> None:
    frame = signals.normalized_frame(_bars(list(range(1, 90))))
    events = signals.default_signal_matches(frame, "D")
    starts = [row for row in events if row["signal_code"] == "LONG_MA_ARRAY_START"]
    states = [row for row in events if row["signal_code"] == "LONG_MA_ARRAY"]
    assert len(starts) == 1
    assert len(states) == 1
    assert states[0]["signal_kind"] == "state"
