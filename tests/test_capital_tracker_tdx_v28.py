from __future__ import annotations

from datetime import datetime

from hermes_trader.data import capital_tracker
from hermes_trader.data import free_source
from hermes_trader.data import tdx_api_source


def test_sector_collection_prefers_tdx_rank_without_inventing_currency(monkeypatch) -> None:
    rows = [
        {"rank": index + 1, "sector_code": f"881{index:03d}.SH", "sector_name": f"行业{index}", "value": 100 - index}
        for index in range(10)
    ]
    monkeypatch.setattr(
        tdx_api_source,
        "sector_fund_rank",
        lambda **_kwargs: {
            "source": "realtime",
            "data_date": "2026-07-16",
            "unit": "tdx_raw_unverified",
            "rows": rows,
        },
    )
    monkeypatch.setattr(
        free_source,
        "moneyflow_ind_dc",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("TDX 完整时不应再调慢公开源")),
    )

    result, source, data_date = capital_tracker.collect_sectors(top=2)

    assert len(result) == 4
    assert source == "TDX·板块资金(realtime)"
    assert data_date == "2026-07-16"
    assert all(row["value_unit"] == "tdx_raw_unverified" for row in result)
    assert all(row["unit_verified"] is False for row in result)


def test_capital_snapshot_persists_raw_unit_and_exposes_it_to_ui(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(capital_tracker, "_db_path", None)
    capital_tracker.init(tmp_path)
    monkeypatch.setattr(capital_tracker, "ts_str", lambda: "2026-07-16 10:00:00")
    monkeypatch.setattr(
        capital_tracker,
        "collect_sectors",
        lambda: ([{
            "name": "行业甲",
            "main_net": 123.5,
            "change_pct": None,
            "rnk": 1,
            "value_unit": "tdx_raw_unverified",
            "unit_verified": False,
        }], "TDX·板块资金(realtime)", "2026-07-16"),
    )
    monkeypatch.setattr(capital_tracker, "collect_hot_stocks", lambda: ([], "", ""))

    saved = capital_tracker.snapshot()
    view = capital_tracker.trends(1)

    assert saved["ok"] is True
    assert saved["complete"] is False
    assert view["sectors"][0]["main_net"] == 123.5
    assert view["sectors"][0]["value_unit"] == "tdx_raw_unverified"
    assert view["sectors"][0]["unit_verified"] is False
    assert view["sector_date"] == "2026-07-16"


def test_series_drops_old_segment_when_source_unit_changes() -> None:
    rows = [
        {"ts": "2026-07-16 09:45:00", "name": "行业甲", "main_net": 1e8, "value_unit": "CNY", "unit_verified": 1},
        {"ts": "2026-07-16 10:00:00", "name": "行业甲", "main_net": 12, "value_unit": "tdx_raw_unverified", "unit_verified": 0},
        {"ts": "2026-07-16 10:15:00", "name": "行业甲", "main_net": 15, "value_unit": "tdx_raw_unverified", "unit_verified": 0},
    ]

    grouped = capital_tracker._group_series(rows, "name")

    assert grouped[0]["value_unit"] == "tdx_raw_unverified"
    assert grouped[0]["series"] == [{"t": "10:00", "v": 12}, {"t": "10:15", "v": 15}]


def test_frontend_does_not_label_tdx_raw_as_yuan() -> None:
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    capital_js = (root / "frontend/js/views/capital.js").read_text(encoding="utf-8")
    charts_js = (root / "frontend/js/charts.js").read_text(encoding="utf-8")

    assert "单位尚未核验" in capital_js
    assert "value_unit === 'tdx_raw_unverified'" in capital_js
    assert "const divisor = rawUnit ? 1 : 1e8" in charts_js


def test_next_day_view_replays_last_good_with_explicit_stale_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(capital_tracker, "_db_path", None)
    capital_tracker.init(tmp_path)
    monkeypatch.setattr(capital_tracker, "ts_str", lambda: "2026-07-16 14:45:00")
    monkeypatch.setattr(capital_tracker, "now_cn", lambda: datetime(2026, 7, 16, 14, 45))
    monkeypatch.setattr(
        capital_tracker,
        "collect_sectors",
        lambda: ([{
            "name": "行业甲", "main_net": 123.5, "change_pct": 1.2, "rnk": 1,
            "value_unit": "tdx_raw_unverified", "unit_verified": False,
        }], "TDX·板块资金(daily)", "2026-07-16"),
    )
    monkeypatch.setattr(
        capital_tracker,
        "collect_hot_stocks",
        lambda: ([{
            "code": "600519", "name": "贵州茅台", "main_net": 4.8e8,
            "change_pct": 0.6, "reason": "", "rnk": 1,
        }], "新浪资金流估算口径", "2026-07-16"),
    )
    assert capital_tracker.snapshot()["ok"] is True

    monkeypatch.setattr(capital_tracker, "now_cn", lambda: datetime(2026, 7, 17, 0, 5))
    view = capital_tracker.trends(1)

    assert view["view_state"] == "last_good"
    assert view["last_good_sector"] is True
    assert view["last_good_hot"] is True
    assert view["stale_sector"] is True
    assert view["stale_hot"] is True
    assert view["sector_date"] == "2026-07-16"
    assert view["hot_date"] == "2026-07-16"
    assert view["sectors"][0]["name"] == "行业甲"
    assert view["hot_stocks"][0]["code"] == "600519"
