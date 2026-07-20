from __future__ import annotations

import json
import time

from hermes_trader.analysis import mainline_runtime
from hermes_trader.jobs import prewarmer


def test_overview_cached_returns_last_good_without_touching_slow_source(tmp_path, monkeypatch):
    prewarmer._boot_snapshot = None
    payload = {"ok": True, "ts": "2026-07-14 14:20:00", "up_count": 3000}
    prewarmer.save_snapshot(tmp_path, payload)

    def must_not_fetch():
        raise AssertionError("fast endpoint must not call the slow market source")

    monkeypatch.setattr(prewarmer.market, "market_overview", must_not_fetch)
    assert prewarmer.overview_with_snapshot() == payload


def test_mainline_full_snapshot_round_trip_and_marks_stale(tmp_path):
    payload = {
        "ok": True,
        "data_date": "2026-07-14",
        "roles": [{"name": "元器件"}, {"name": "工业金属"}],
        "missing": [],
    }
    path = mainline_runtime.save_current_snapshot(tmp_path, payload)
    assert path is not None

    fresh = mainline_runtime.load_current_snapshot(tmp_path, limit=1)
    assert fresh is not None
    assert fresh["roles"] == [{"name": "元器件"}]
    assert fresh["cache_state"] == "sampled"
    assert fresh.get("stale") is not True

    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["saved_epoch"] = time.time() - 16 * 60
    path.write_text(json.dumps(envelope), encoding="utf-8")
    stale = mainline_runtime.load_current_snapshot(tmp_path)
    assert stale is not None
    assert stale["stale"] is True
    assert "后台正在更新" in "；".join(stale["missing"])


def test_mainline_snapshot_rejects_corrupt_file(tmp_path):
    (tmp_path / mainline_runtime.CURRENT_SNAPSHOT_FILE).write_text("{", encoding="utf-8")
    assert mainline_runtime.load_current_snapshot(tmp_path) is None
