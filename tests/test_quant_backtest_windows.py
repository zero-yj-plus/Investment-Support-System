from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd

from hermes_trader.analysis import quant


def _bars(n: int, *, end: date = date(2026, 7, 10)) -> list[dict]:
    """构造 n 根单调可用的模拟交易日日线。"""
    days = pd.bdate_range(end=end, periods=n)
    return [
        {"d": d.strftime("%Y%m%d"), "c": 3000.0 + i * 2.0 + (i % 7) * 0.3}
        for i, d in enumerate(days)
    ]


def test_backtest_requests_full_history_and_keeps_three_real_windows(monkeypatch) -> None:
    full = _bars(800)
    requested: list[int] = []

    def fake_history(_code: str, required: int) -> dict:
        requested.append(required)
        return {
            "rows": full[-required:],
            "source": "腾讯指数日K",
            "as_of": full[-1]["d"],
            "requested": required,
            "available": len(full),
        }

    monkeypatch.setattr(quant, "_index_daily_history", fake_history)

    half = quant.backtest.__wrapped__(days=120, fast=5, slow=20)
    year = quant.backtest.__wrapped__(days=250, fast=20, slow=60)
    two_year = quant.backtest.__wrapped__(days=480, fast=20, slow=60)

    assert requested == [140, 310, 540]  # 回测窗口 + 慢线预热，不再固定 160 根
    assert [
        half["coverage"]["actual_window_days"],
        year["coverage"]["actual_window_days"],
        two_year["coverage"]["actual_window_days"],
    ] == [120, 250, 480]
    assert [
        half["coverage"]["return_days"],
        year["coverage"]["return_days"],
        two_year["coverage"]["return_days"],
    ] == [119, 249, 479]
    assert len({
        half["coverage"]["from"],
        year["coverage"]["from"],
        two_year["coverage"]["from"],
    }) == 3
    assert all(result["ok"] for result in (half, year, two_year))
    assert all(result["as_of"] == "2026-07-10" for result in (half, year, two_year))


def test_backtest_refuses_to_silently_shrink_when_history_is_short(monkeypatch) -> None:
    short = _bars(160)
    monkeypatch.setattr(
        quant,
        "_index_daily_history",
        lambda _code, required: {
            "rows": short,
            "source": "腾讯指数日K",
            "as_of": short[-1]["d"],
            "requested": required,
            "available": len(short),
        },
    )

    result = quant.backtest.__wrapped__(days=250, fast=20, slow=60)

    assert result["ok"] is False
    assert result["dates"] == []
    assert result["as_of"] == "2026-07-10"
    assert result["coverage"]["requested_history_bars"] == 310
    assert result["coverage"]["returned_history_bars"] == 160
    assert any("历史不足(160/310根" in item for item in result["missing"])
    assert any("as_of=2026-07-10" in item for item in result["missing"])


def test_free_index_history_never_uses_v8_sina_fallback(monkeypatch) -> None:
    tx_rows = _bars(200)
    sina_rows = _bars(420)

    def frame(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "date": [r["d"] for r in rows],
                "open": [r["c"] - 1 for r in rows],
                "close": [r["c"] for r in rows],
                "high": [r["c"] + 1 for r in rows],
                "low": [r["c"] - 2 for r in rows],
                "volume": [1_000_000] * len(rows),
            }
        )

    calls: list[str] = []

    def tx_fn(**_kwargs):
        calls.append("tx")
        return frame(tx_rows)

    def sina_fn(**_kwargs):
        calls.append("sina")
        return frame(sina_rows)

    fake_ak = SimpleNamespace(
        stock_zh_index_daily_tx=tx_fn,
        stock_zh_index_daily=sina_fn,
    )
    monkeypatch.setattr(quant, "get_ak", lambda: fake_ak)
    monkeypatch.setattr(quant, "safe", lambda fn, *args, **kwargs: fn(**{k: v for k, v in kwargs.items() if k not in {"label", "timeout"}}))
    monkeypatch.setattr(quant.free_source, "quote_data_date", lambda: "2026-07-10")

    result = quant._index_daily_history.__wrapped__("sh000300", 310)

    assert calls == ["tx"]
    assert result["source"] == "腾讯指数日K"
    assert result["requested"] == 310
    assert result["available"] == 200
    assert len(result["rows"]) == 200
    assert result["rows"][0]["d"] == tx_rows[0]["d"]
    assert result["as_of"] == "20260710"
