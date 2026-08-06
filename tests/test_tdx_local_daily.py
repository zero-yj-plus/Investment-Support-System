from __future__ import annotations

from datetime import datetime
import struct

from hermes_trader.data import tdx_local_daily as daily


RECORD = struct.Struct("<IIIIIfII")


def _make_root(tmp_path, monkeypatch):
    for market in ("sh", "sz", "bj"):
        (tmp_path / "vipdoc" / market / "lday").mkdir(parents=True)
    monkeypatch.setenv("TDX_LOCAL_DIR", str(tmp_path))
    return tmp_path


def _write_day(root, market: str, code: str, rows: list[tuple]) -> None:
    path = root / "vipdoc" / market / "lday" / f"{market}{code}.day"
    path.write_bytes(b"".join(RECORD.pack(*row) for row in rows))


def test_read_raw_daily_parses_native_units_and_ignores_partial_record(tmp_path, monkeypatch) -> None:
    root = _make_root(tmp_path, monkeypatch)
    path = root / "vipdoc" / "sh" / "lday" / "sh600000.day"
    path.write_bytes(
        RECORD.pack(20260804, 1001, 1050, 980, 1025, 1_234_567.5, 123_400, 0)
        + RECORD.pack(20260805, 1025, 1080, 1010, 1075, 2_000_000.0, 200_000, 0)
        + b"partial"
    )

    bars = daily.read_raw_daily("600000.SH", 20, completed_only=False)

    assert daily.configured() is True
    assert bars[-1] == {
        "d": "2026-08-05", "o": 10.25, "h": 10.8, "l": 10.1, "c": 10.75,
        "v_shares": 200_000.0, "amount": 2_000_000.0,
        "adjustment": "none", "source": "通达信本地.day未复权日K",
        "volume_unit": "股", "amount_unit": "元",
    }


def test_read_raw_daily_excludes_unfinished_today(tmp_path, monkeypatch) -> None:
    root = _make_root(tmp_path, monkeypatch)
    _write_day(root, "sz", "000001", [
        (20260805, 1000, 1010, 990, 1005, 100_000.0, 10_000, 0),
        (20260806, 1010, 1020, 1000, 1015, 120_000.0, 12_000, 0),
    ])
    monkeypatch.setattr(daily, "now_cn", lambda: datetime(2026, 8, 6, 14, 30))

    bars = daily.read_raw_daily("000001.SZ", 20)

    assert [bar["d"] for bar in bars] == ["2026-08-05"]


def test_apply_qfq_uses_dividend_affine_transform_strictly_before_event() -> None:
    raw = [
        {"d": "2024-06-13", "o": 10.0, "h": 10.2, "l": 9.8, "c": 10.0,
         "v_shares": 100.0, "amount": 1_000.0, "adjustment": "none", "source": "raw"},
        {"d": "2024-06-14", "o": 10.0, "h": 10.2, "l": 9.8, "c": 10.0,
         "v_shares": 200.0, "amount": 2_000.0, "adjustment": "none", "source": "raw"},
    ]

    bars = daily.apply_qfq(raw, [("2024-06-14", 7.19, 0.0, 0.0, 0.0)])

    assert bars[0]["c"] == 9.28
    assert bars[1]["c"] == 10.0
    assert bars[0]["v_shares"] == 100.0
    assert bars[0]["amount"] == 1_000.0
    assert bars[0]["adjustment"] == "qfq"


def test_daily_batch_requires_factors_and_keeps_index_raw(tmp_path, monkeypatch) -> None:
    root = _make_root(tmp_path, monkeypatch)
    row = (20260805, 1000, 1010, 990, 1005, 100_000.0, 10_000, 0)
    _write_day(root, "sh", "600000", [row])
    _write_day(root, "sh", "000300", [row])

    monkeypatch.setattr(daily, "_dividend_events", lambda *_args: None)
    missing = daily.daily_bars_batch(["600000.SH"], 20, completed_only=False)
    index = daily.daily_bars_batch(["000300.SH"], 20, completed_only=False)

    assert missing == {}
    assert index["000300"][0]["adjustment"] == "none"

    monkeypatch.setattr(daily, "_dividend_events", lambda *_args: [])
    stock = daily.daily_bars_batch(["600000.SH"], 20, completed_only=False)
    assert stock["600000"][0]["adjustment"] == "qfq"
