from __future__ import annotations

from datetime import date
import time

from hermes_trader.data import base, news


def test_safe_timeout_does_not_retry_same_uncancellable_source(monkeypatch) -> None:
    label = "timeout-no-repeat-test"
    calls = 0

    def slow_source():
        nonlocal calls
        calls += 1
        time.sleep(0.08)
        return {"late": True}

    monkeypatch.setattr(base, "_HAS_PROXY", False)
    base._SAFE_BRK.pop(label, None)
    started = time.monotonic()
    assert base.safe(slow_source, label=label, timeout=0.01) is None
    elapsed = time.monotonic() - started

    assert calls == 1
    assert elapsed < 0.06
    metric = base.source_metrics()[label]
    assert metric["last_mode"] == "direct"
    assert "超时" in metric["last_error"]

    # 同标签已燔断，立即刷新不得又制造一个背景僵尸请求。
    assert base.safe(slow_source, label=label, timeout=0.01) is None
    assert calls == 1
    assert base.source_metrics()[label]["last_mode"] == "breaker-open"


def test_offhours_ttl_resolves_calendar_without_holding_cache_lock(monkeypatch) -> None:
    lock_was_free: list[bool] = []

    def checker(_day):
        acquired = base._cache_lock.acquire(blocking=False)
        lock_was_free.append(acquired)
        if acquired:
            base._cache_lock.release()
        return False

    monkeypatch.setattr(base, "is_trade_day", checker)

    @base.ttl_cache(1, offhours_seconds=3600)
    def cached_market_slice():
        return {"ok": True}

    assert cached_market_slice() == {"ok": True}
    assert lock_was_free == [True]


def test_safe_blocks_inprocess_mini_racer_before_function_runs() -> None:
    for function_name in (
        "index_us_stock_sina",
        "stock_fund_flow_industry",
        "stock_zh_a_daily",
        "stock_zh_a_minute",
        "stock_zh_index_daily",
        "tool_trade_date_hist_sina",
    ):
        called = False

        def unsafe():
            nonlocal called
            called = True
            raise AssertionError("unsafe function must not execute")

        unsafe.__name__ = function_name
        label = f"unsafe-test:{function_name}"
        assert base.safe(unsafe, label=label) is None
        assert called is False
        metric = base.source_metrics()[label]
        assert metric["last_mode"] == "blocked-v8"
        assert metric["last_ok"] is False


def test_local_trade_calendar_excludes_weekends_and_public_holidays(monkeypatch) -> None:
    from hermes_trader.data import tencent_source

    monkeypatch.setattr(tencent_source, "index_trade_dates", lambda: set())
    days = base.trade_dates.__wrapped__()

    assert days is not None
    assert "2026-07-13" in days          # ordinary Monday
    assert "2026-07-12" not in days      # Sunday
    assert "2026-01-01" not in days      # New Year's Day
    assert base.is_trade_day(date(2026, 7, 13)) is True


def test_trade_calendar_overlays_actual_exchange_sessions(monkeypatch) -> None:
    from hermes_trader.data import tencent_source

    monkeypatch.setattr(
        tencent_source,
        "index_trade_dates",
        lambda: {"2024-02-08", "2024-02-19"},
    )
    days = base.trade_dates.__wrapped__()

    assert "2024-02-08" in days
    assert "2024-02-09" not in days
    assert "2024-02-19" in days


def test_us_market_uses_tencent_for_indexes_without_akshare_history(monkeypatch) -> None:
    def quotes(symbols):
        if "usDJI" in symbols:
            return {
                "usDJI": {"name": "道琼斯", "price": 52000, "change_pct": 0.2, "as_of": "2026-07-10 17:00:00"},
                "usINX": {"name": "标普500", "price": 7500, "change_pct": 0.3, "as_of": "2026-07-10 17:00:00"},
                "usIXIC": {"name": "纳斯达克", "price": 26000, "change_pct": 0.1, "as_of": "2026-07-10 17:00:00"},
            }
        return {}

    monkeypatch.setattr(news.tc, "us_quotes", quotes)
    monkeypatch.setattr(news, "get_ak", lambda: None)

    result = news.us_market.__wrapped__()

    assert [row["name"] for row in result["indexes"]] == ["道琼斯", "标普500", "纳斯达克"]
    assert all(row["source"] == "腾讯财经" for row in result["indexes"])
    assert all(row["date"] == "2026-07-10" for row in result["indexes"])
