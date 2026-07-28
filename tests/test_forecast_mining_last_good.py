import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from hermes_trader.analysis import forecast_mining
from hermes_trader.api import routes


CN_TZ = ZoneInfo("Asia/Shanghai")
PERIOD = "20260630"


def _row() -> dict:
    return {
        "code": "600001",
        "name": "测试股份",
        "ann_date": "20260710",
        "type": "预增",
        "p_chg_min": 60.0,
        "p_chg_max": 80.0,
        "net_min": 160_000_000.0,
        "net_max": 180_000_000.0,
        "last_net": 100_000_000.0,
        "summary": "预计归母净利润同比增长60%至80%",
        "reason": "主营业务增长",
        "source": "公开业绩预告",
    }


def test_success_persists_customer_scoped_last_good(monkeypatch, tmp_path: Path) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=CN_TZ)
    monkeypatch.setattr(forecast_mining, "now_cn", lambda: now)
    monkeypatch.setattr(forecast_mining.tx, "forecast_list", lambda _period: [_row()])

    result = forecast_mining.mine(PERIOD, tmp_path)

    assert result["state"] == "complete"
    assert result["stale"] is False
    assert result["used_last_good"] is False
    assert result["data_ts"] == "2026-07-28T10:00:00+08:00"
    path = tmp_path / "forecast_mining" / f"last-good-{PERIOD}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "forecast-mining-last-good-v1"
    assert payload["period"] == PERIOD
    assert payload["result"]["items"][0]["code"] == "600001"


def test_failure_replays_same_period_snapshot_with_explicit_stale_state(monkeypatch, tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 28, 10, 0, tzinfo=CN_TZ)]
    monkeypatch.setattr(forecast_mining, "now_cn", lambda: clock[0])
    monkeypatch.setattr(forecast_mining.tx, "forecast_list", lambda _period: [_row()])
    live = forecast_mining.mine(PERIOD, tmp_path)

    clock[0] += timedelta(hours=3)
    monkeypatch.setattr(forecast_mining.tx, "forecast_list", lambda _period: None)
    replay = forecast_mining.mine(PERIOD, tmp_path)

    assert replay["ok"] is True
    assert replay["state"] == "last_good"
    assert replay["degraded"] is True
    assert replay["stale"] is True
    assert replay["used_last_good"] is True
    assert replay["snapshot_age_seconds"] == 3 * 3600
    assert replay["data_ts"] == live["data_ts"]
    assert replay["items"] == live["items"]
    assert "公开源当前不可用" in replay["missing"][0]
    assert forecast_mining.build_push_text(replay) is None


def test_snapshot_is_not_replayed_for_another_period_or_after_seven_days(monkeypatch, tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 28, 10, 0, tzinfo=CN_TZ)]
    monkeypatch.setattr(forecast_mining, "now_cn", lambda: clock[0])
    monkeypatch.setattr(forecast_mining.tx, "forecast_list", lambda _period: [_row()])
    forecast_mining.mine(PERIOD, tmp_path)

    monkeypatch.setattr(forecast_mining.tx, "forecast_list", lambda _period: None)
    wrong_period = forecast_mining.mine("20251231", tmp_path)
    assert wrong_period["ok"] is False
    assert wrong_period["state"] == "missing"

    clock[0] += timedelta(days=8)
    expired = forecast_mining.mine(PERIOD, tmp_path)
    assert expired["ok"] is False
    assert expired["used_last_good"] is False


def test_successful_empty_result_does_not_overwrite_nonempty_snapshot(monkeypatch, tmp_path: Path) -> None:
    clock = [datetime(2026, 7, 28, 10, 0, tzinfo=CN_TZ)]
    monkeypatch.setattr(forecast_mining, "now_cn", lambda: clock[0])
    monkeypatch.setattr(forecast_mining.tx, "forecast_list", lambda _period: [_row()])
    forecast_mining.mine(PERIOD, tmp_path)

    clock[0] += timedelta(hours=1)
    monkeypatch.setattr(forecast_mining.tx, "forecast_list", lambda _period: [])
    empty = forecast_mining.mine(PERIOD, tmp_path)
    assert empty["state"] == "complete_empty"
    assert empty["items"] == []

    monkeypatch.setattr(forecast_mining.tx, "forecast_list", lambda _period: None)
    replay = forecast_mining.mine(PERIOD, tmp_path)
    assert replay["state"] == "last_good"
    assert replay["items"][0]["code"] == "600001"


def test_api_passes_customer_data_dir_to_mining(monkeypatch, tmp_path: Path) -> None:
    seen = {}

    def fake(period, data_dir):
        seen["period"] = period
        seen["data_dir"] = data_dir
        return {"ok": True, "items": [], "traps": []}

    monkeypatch.setattr(forecast_mining, "mine", fake)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
    )))

    result = asyncio.run(routes.forecast_mining_api(request, period=PERIOD))

    assert result["ok"] is True
    assert seen == {"period": PERIOD, "data_dir": tmp_path}
