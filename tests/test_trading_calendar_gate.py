from __future__ import annotations

from datetime import date

from hermes_trader.data import base


def test_weekday_is_unknown_when_calendar_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(base, "trade_dates", lambda: None)
    result = base.trade_day_status(date(2026, 7, 13))
    assert result["status"] == "unknown"
    assert result["is_trade_day"] is None
    assert result["quality"] == "missing"


def test_weekend_remains_known_closed_without_calendar(monkeypatch) -> None:
    monkeypatch.setattr(base, "trade_dates", lambda: None)
    result = base.trade_day_status(date(2026, 7, 12))
    assert result["status"] == "closed"
    assert result["is_trade_day"] is False
