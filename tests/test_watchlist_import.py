from pathlib import Path

from hermes_trader.knowledge.store import Store


def test_watchlist_import_appends_atomically_and_skips_duplicates_and_non_a_shares(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.add_watch({"code": "000001", "name": "平安银行", "sector": "银行"})

    result = store.import_watchlist([
        {"code": "600519", "name": "贵州茅台", "sector": "白酒"},
        {"code": "000001", "name": "重复记录", "sector": "银行"},
        {"code": "600519", "name": "文件内重复", "sector": "白酒"},
        {"code": "09868", "name": "港股", "sector": "汽车"},
        {"code": "510300", "name": "ETF", "sector": "基金"},
        {"code": "300308", "name": "中际旭创", "sector": "通信设备"},
    ])

    assert result["added_codes"] == ["600519", "300308"]
    assert result["duplicate_codes"] == ["000001", "600519"]
    assert [row["code"] for row in result["invalid"]] == ["09868", "510300"]

    rows = store.watchlist()
    assert [row["code"] for row in rows] == ["000001", "600519", "300308"]
    assert len({row["id"] for row in rows}) == 3
    imported = rows[1]
    assert imported["name"] == "贵州茅台"
    assert imported["sector"] == "白酒"
    assert imported["add_reason"] == ""
    assert imported["target_low"] is None
    assert imported["target_high"] is None
    assert imported["trigger_condition"] == ""
    assert imported["no_buy_condition"] == ""
    assert imported["abandon_condition"] == ""
    assert imported["abandon_price"] is None
    assert imported["note"] == ""


def test_watchlist_import_rejects_empty_or_oversized_batches(tmp_path: Path) -> None:
    store = Store(tmp_path)

    for candidates in ([], [{"code": "600000"}] * 201):
        try:
            store.import_watchlist(candidates)
        except ValueError as exc:
            assert "1—200" in str(exc)
        else:
            raise AssertionError("invalid import batch should fail")
