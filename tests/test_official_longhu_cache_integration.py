from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from hermes_trader.api import routes
from hermes_trader.data import mainforce_source, official_longhu_source


class _Frame:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def __len__(self) -> int:
        return len(self.rows)

    def sort_values(self, key: str, ascending: bool = True):
        self.rows.sort(key=lambda row: row.get(key) or 0, reverse=not ascending)
        return self

    def iterrows(self):
        return enumerate(self.rows)


def _install_eastmoney(monkeypatch, detail_rows: list[dict]) -> None:
    api = SimpleNamespace(
        stock_lhb_detail_em=lambda **_kwargs: _Frame(detail_rows),
        stock_lhb_jgmmtj_em=lambda **_kwargs: _Frame([]),
    )
    monkeypatch.setattr(mainforce_source, "get_ak", lambda: api)
    monkeypatch.setattr(
        mainforce_source,
        "safe",
        lambda fn, **kwargs: fn(**{
            key: value for key, value in kwargs.items() if key in {"start_date", "end_date"}
        }),
    )


def test_mainforce_longhu_adds_code_set_only_official_verification(monkeypatch) -> None:
    _install_eastmoney(monkeypatch, [
        {"代码": "600001", "名称": "浦发", "龙虎榜净买额": 10},
        {"代码": "000001", "名称": "平安", "龙虎榜净买额": 20},
    ])
    monkeypatch.setattr(
        official_longhu_source,
        "load_snapshot",
        lambda trade_date: {
            "ok": True,
            "state": "complete",
            "trade_date": trade_date,
            "fetched_at": "2026-07-13T18:30:00+08:00",
            "availability_reason": "沪深两所官网响应完整",
            "events": [
                {"code": "600001", "buy_amount_yuan": 999999999},
                {"code": "300001", "buy_amount_yuan": 888888888},
            ],
        },
    )

    result = mainforce_source.longhu.__wrapped__("20260713")
    verification = result["official_verification"]

    assert verification["comparison_basis"] == "code_set_only"
    assert verification["amounts_compared"] is False
    assert verification["comparison_state"] == "complete"
    assert verification["matched"] == ["600001"]
    assert verification["eastmoney_only"] == ["000001"]
    assert verification["official_only"] == ["300001"]
    assert verification["official_cache"] == {
        "available": True,
        "state": "complete",
        "trade_date": "2026-07-13",
        "fetched_at": "2026-07-13T18:30:00+08:00",
        "availability_reason": "沪深两所官网响应完整",
        "event_count": 2,
        "code_count": 2,
        # 今日有完整快照时无需恢复回退（新增字段，均为空）
        "recovered_from_date": None,
        "recovered_code_count": 0,
    }
    assert "999999999" not in repr(verification)


def test_mainforce_longhu_does_not_call_missing_cache_a_mismatch(monkeypatch) -> None:
    _install_eastmoney(monkeypatch, [
        {"代码": "600001", "名称": "浦发", "龙虎榜净买额": 10},
    ])
    monkeypatch.setattr(official_longhu_source, "load_snapshot", lambda _trade_date: None)

    verification = mainforce_source.longhu.__wrapped__("20260713")["official_verification"]

    assert verification["comparison_state"] == "unavailable"
    assert verification["official_cache"]["state"] == "not_cached"
    assert verification["eastmoney_code_count"] == 1
    assert verification["matched"] == []
    assert verification["eastmoney_only"] == []
    assert verification["official_only"] == []


def test_mainforce_longhu_does_not_diff_when_eastmoney_detail_is_unavailable(monkeypatch) -> None:
    _install_eastmoney(monkeypatch, [])
    monkeypatch.setattr(
        official_longhu_source,
        "load_snapshot",
        lambda trade_date: {
            "ok": True,
            "state": "complete",
            "trade_date": trade_date,
            "fetched_at": "2026-07-13T18:30:00+08:00",
            "availability_reason": "沪深两所官网响应完整",
            "events": [{"code": "600001"}],
        },
    )

    result = mainforce_source.longhu.__wrapped__("20260713")
    verification = result["official_verification"]

    # 东财空数据(HTTP200 空业务) → "今日结算中/未发布"，四态之一，绝不一律"不可用"；
    # 无历史快照可回退时保持 settling，缺失说明含"龙虎榜"。
    assert result["status"] == "settling"
    assert any("龙虎榜" in m for m in result["missing"])
    assert verification["eastmoney_available"] is False
    assert verification["comparison_state"] == "unavailable"
    assert verification["classification_scope"] == "unavailable"
    assert verification["matched"] == []
    assert verification["eastmoney_only"] == []
    assert verification["official_only"] == []
    assert verification["official_cache"]["state"] == "complete"
    assert verification["official_cache"]["code_count"] == 1


def test_official_longhu_api_reads_cache_only_and_validates_date(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        official_longhu_source,
        "fetch_official_longhu",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not run")),
    )
    monkeypatch.setattr(
        official_longhu_source,
        "load_snapshot",
        lambda trade_date: calls.append(trade_date) or {
            "ok": True,
            "state": "complete",
            "trade_date": trade_date,
            "events": [{"code": "600000"}],
        },
    )

    invalid = asyncio.run(routes.mainforce_official_longhu("2026-02-30"))
    result = asyncio.run(routes.mainforce_official_longhu("2026-07-13"))

    assert invalid.status_code == 400
    assert json.loads(invalid.body)["error_code"] == "invalid_date"
    assert calls == ["2026-07-13"]
    assert result["events"] == [{"code": "600000"}]
    assert result["source"] == "official_snapshot_cache"
    assert result["cache_only"] is True
    assert result["network_requested"] is False


def test_official_longhu_api_returns_explicit_not_cached_state(monkeypatch) -> None:
    monkeypatch.setattr(official_longhu_source, "load_snapshot", lambda _trade_date: None)

    response = asyncio.run(routes.mainforce_official_longhu("2026-07-13"))
    body = json.loads(response.body)

    assert response.status_code == 404
    assert body["error_code"] == "official_snapshot_not_cached"
    assert body["trade_date"] == "2026-07-13"
    assert body["network_requested"] is False
