from __future__ import annotations

from datetime import datetime
import json

from hermes_trader.data.limitup_tracker import LimitUpTracker


CALENDAR = {
    "2026-08-07",
    "2026-08-10",
    "2026-08-11",
    "2026-08-12",
    "2026-08-13",
    "2026-08-14",
}


def _pool(day: str, _kind: str = "U"):
    values = {
        "20260810": [
            {"code": "000001", "name": "甲", "industry": "银行", "lianban": 2, "change_pct": 10.01, "first_time": "09:31:00"},
            {"code": "000002", "name": "乙", "industry": "软件", "lianban": 1, "change_pct": 9.98, "first_time": "10:02:00"},
        ],
        "20260811": [
            {"code": "000001", "name": "甲", "industry": "银行", "lianban": 3, "change_pct": 10.0},
            {"code": "000003", "name": "丙", "industry": "通信", "lianban": 1, "change_pct": 10.0},
        ],
        "20260812": [],
    }
    return values.get(day)


def _bars(code: str, _days: int):
    closes = {
        "000001": (10.0, 11.0, 10.45),
        "000002": (20.0, 19.0, 19.95),
    }[code]
    return [
        {"d": "2026-08-10", "c": closes[0], "source": "腾讯财经·前复权日K"},
        {"d": "2026-08-11", "c": closes[1], "source": "腾讯财经·前复权日K"},
        {"d": "2026-08-12", "c": closes[2], "source": "腾讯财经·前复权日K"},
    ]


def _tracker(tmp_path, now: datetime, **overrides):
    kwargs = {
        "calendar_fn": lambda: set(CALENDAR),
        "now_fn": lambda: now,
        "limit_list_fn": _pool,
        "current_pool_fn": lambda _day: {"ok": False},
        "quotes_fn": lambda _codes: {},
        "bars_fn": _bars,
    }
    kwargs.update(overrides)
    return LimitUpTracker(tmp_path, **kwargs)


def test_dates_come_from_server_calendar_and_mark_missing_snapshots(tmp_path):
    tracker = _tracker(tmp_path, datetime(2026, 8, 13, 10, 0))

    payload = tracker.dates(3)

    assert [item["date"] for item in payload["dates"]] == [
        "2026-08-13", "2026-08-12", "2026-08-11"
    ]
    assert payload["default_date"] == "2026-08-13"
    assert payload["dates"][1]["missing_label"] == "快照缺失"
    assert payload["calendar"]["state"] == "available"


def test_intraday_snapshot_is_not_frozen_and_future_windows_are_pending(tmp_path):
    tracker = _tracker(
        tmp_path,
        datetime(2026, 8, 12, 10, 15),
        current_pool_fn=lambda _day: {
            "ok": True,
            "data_date": "2026-08-12",
            "ts": "2026-08-12 10:15:00",
            "stocks": [{"code": "000001", "name": "甲", "lianban": 1, "change_pct": 10.0}],
            "missing": ["首封时间待补"],
        },
    )

    payload = tracker.snapshot("2026-08-12")

    assert payload["is_intraday"] is True
    assert payload["baseline_count"] == 1
    assert payload["metrics"]["t1_average"]["state"] == "pending"
    assert payload["metrics"]["t1_average"]["value"] is None
    assert not (tmp_path / "limitup-tracker" / "2026-08-12.jsonl").exists()


def test_backfill_uses_daily_pct_and_existing_promotion_definition_then_freezes_append_only(tmp_path):
    tracker = _tracker(tmp_path, datetime(2026, 8, 13, 18, 0))
    baseline, meta = tracker._ensure_baseline("2026-08-10")
    assert baseline is not None and meta["frozen"] is True

    t1 = tracker._backfill_window("2026-08-10", baseline, "t1", "2026-08-11", sorted(CALENDAR))
    t2 = tracker._backfill_window("2026-08-10", baseline, "t2", "2026-08-12", sorted(CALENDAR))
    tracker._backfill_window("2026-08-10", baseline, "t1", "2026-08-11", sorted(CALENDAR))
    payload = tracker.snapshot("2026-08-10")

    assert t1["status"] == "complete"
    assert t2["status"] == "complete"
    assert payload["metrics"]["t1_average"]["value"] == 2.5
    assert payload["metrics"]["t1_red_rate"]["value"] == 50.0
    # 次日晋级 = T+1 涨停池中连板数>=2家数 / 冻结基准日涨停家数。
    assert payload["metrics"]["t1_promotion_rate"]["value"] == 50.0
    assert payload["metrics"]["t2_average"]["value"] == 0.0
    assert payload["metrics"]["t2_red_rate"]["value"] == 50.0
    assert all(metric["state"] == "final" for metric in payload["metrics"].values())
    assert [row["code"] for row in payload["stocks"]] == ["000001", "000002"]

    path = tmp_path / "limitup-tracker" / "2026-08-10.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events].count("baseline_frozen") == 1
    assert [event["event"] for event in events].count("window_observed") == 2


def test_missing_baseline_and_partial_window_never_become_zero(tmp_path):
    missing_tracker = _tracker(
        tmp_path / "missing",
        datetime(2026, 8, 13, 18, 0),
        limit_list_fn=lambda *_args: None,
    )
    missing = missing_tracker.snapshot("2026-08-10")
    assert missing["baseline_count"] is None
    assert all(metric["value"] is None for metric in missing["metrics"].values())
    assert all(metric["state"] == "missing" for metric in missing["metrics"].values())

    partial_tracker = _tracker(
        tmp_path / "partial",
        datetime(2026, 8, 13, 18, 0),
        bars_fn=lambda code, days: _bars(code, days) if code == "000001" else [],
    )
    baseline, _ = partial_tracker._ensure_baseline("2026-08-10")
    result = partial_tracker._backfill_window(
        "2026-08-10", baseline, "t1", "2026-08-11", sorted(CALENDAR)
    )
    payload = partial_tracker.snapshot("2026-08-10")
    assert result["status"] == "partial"
    assert payload["metrics"]["t1_average"]["state"] == "missing"
    assert payload["metrics"]["t1_average"]["value"] is None
    assert payload["stocks"][0]["t1"]["change_pct"] == 10.0
    assert payload["stocks"][0]["t1"]["state"] == "final"
    assert payload["stocks"][1]["t1"]["change_pct"] is None


def test_backfill_reports_noop_when_all_frozen_events_already_exist(tmp_path):
    tracker = _tracker(tmp_path, datetime(2026, 8, 13, 18, 0))
    baseline, _ = tracker._ensure_baseline("2026-08-10")
    tracker._backfill_window("2026-08-10", baseline, "t1", "2026-08-11", sorted(CALENDAR))
    tracker._backfill_window("2026-08-10", baseline, "t2", "2026-08-12", sorted(CALENDAR))
    tracker.dates = lambda _limit: {"dates": [{"date": "2026-08-10"}]}

    result = tracker.backfill_recent(1)

    assert result["status"] == "noop"
    assert result["reason"] == "没有已到期且可补的数据"
