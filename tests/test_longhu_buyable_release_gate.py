"""发布门禁回归测试：

1. 可买候选池时间语义（premarket_brief.buyable_pool）
   - 昨日涨停股不永久排除，纳入并标"今日竞价待核"，不给买入结论；
   - 输出带 as_of / is_realtime=False / phase / state_chain（盘前候选，非今日可执行）。

2. 龙虎榜四态 + 最近完整快照回退 + 缓存语义（mainforce_source.longhu）
   - complete / settling / source_error / last_complete 四态区分；
   - 完整才落盘；今日空/异常时显眼回退最近完整快照（标 snapshot_date）；
   - 空/非完整结果不进盘外 6 小时长缓存（ttl_cache long_ok）；
   - 官方快照到达时强制失效（invalidate_ttl_cache）。
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace

from hermes_trader.analysis import premarket_brief, screener
from hermes_trader.data import base, mainforce_source, official_longhu_source


# ---------------------------------------------------------------- 候选池
def test_buyable_pool_keeps_limit_up_as_auction_pending(monkeypatch) -> None:
    monkeypatch.setattr(screener, "_resolve_trade_day", lambda: "20260714")
    monkeypatch.setattr(screener, "_market_snapshot", lambda td: {
        "ok": True, "trade_date": "2026-07-14", "missing": [],
        "rows": [
            {"code": "600000", "name": "浦发银行", "change_pct": 2.0,
             "amount": 5e8, "circ_mv": 300e8, "price": 10.0, "industry": "银行"},
            {"code": "600519", "name": "贵州茅台", "change_pct": 10.0,   # 昨日涨停(主板10%)
             "amount": 8e8, "circ_mv": 500e8, "price": 1600.0, "industry": "白酒"},
        ],
    })
    out = premarket_brief.buyable_pool()

    # 时间语义元数据：盘前候选、非实时、有状态链与 as_of。
    assert out["phase"] == "T-1盘前候选"
    assert out["is_realtime"] is False
    assert out["as_of"] == "2026-07-14"
    assert out["state_chain"][0] == "T-1盘前候选"
    assert out["state_chain"][-1] == "盘中可执行候选"

    by_code = {s["code"]: s for s in out["stocks"]}
    # 昨日涨停股未被永久排除，纳入并标竞价待核、不给买入结论。
    assert "600519" in by_code
    assert by_code["600519"]["stage"] == "今日竞价待核"
    assert by_code["600519"]["auction_pending"] is True
    assert "竞价待核" in by_code["600519"]["note"]
    # 普通未封板股为盘前候选。
    assert by_code["600000"]["stage"] == "盘前候选"
    assert by_code["600000"]["auction_pending"] is False
    # 无任何条目被标为"盘中可执行候选"（08:50 盘前绝不产出可执行结论）。
    assert all(s["stage"] in ("盘前候选", "今日竞价待核") for s in out["stocks"])


# ---------------------------------------------------------------- 龙虎榜四态
class _Frame:
    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def sort_values(self, key, ascending=True):
        self.rows.sort(key=lambda r: r.get(key) or 0, reverse=not ascending)
        return self

    def iterrows(self):
        return enumerate(self.rows)


def _install_eastmoney(monkeypatch, detail_rows, *, error: bool = False) -> None:
    """error=True → safe 返回 None（源异常）；否则返回 _Frame(detail_rows)（空=结算中）。"""
    api = SimpleNamespace(
        stock_lhb_detail_em=lambda **_k: _Frame(list(detail_rows)),
        stock_lhb_jgmmtj_em=lambda **_k: _Frame([]),
    )
    monkeypatch.setattr(mainforce_source, "get_ak", lambda: api)
    if error:
        monkeypatch.setattr(mainforce_source, "safe", lambda fn, **k: None)
    else:
        monkeypatch.setattr(
            mainforce_source, "safe",
            lambda fn, **k: fn(**{x: k[x] for x in k if x in {"start_date", "end_date"}}),
        )
    monkeypatch.setattr(official_longhu_source, "load_snapshot", lambda d: None)


def test_longhu_complete_then_last_complete_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(mainforce_source, "_LONGHU_DATA_ROOT", tmp_path / "eastmoney-longhu")   # 东财快照目录

    # 今日完整 → status=complete、is_realtime、落盘快照。
    _install_eastmoney(monkeypatch, [{"代码": "600000", "名称": "浦发", "龙虎榜净买额": 10}])
    r1 = mainforce_source.longhu.__wrapped__("20260714")
    assert r1["status"] == "complete"
    assert r1["is_realtime"] is True
    assert r1["list"] and r1["list"][0]["code"] == "600000"
    assert (tmp_path / "eastmoney-longhu" / "2026-07-14.json").exists()

    # 次日东财空(HTTP200空业务) → 回退最近完整快照，显眼标日期，绝不清空。
    _install_eastmoney(monkeypatch, [])
    r2 = mainforce_source.longhu.__wrapped__("20260715")
    assert r2["status"] == "last_complete"
    assert r2["snapshot_date"] == "2026-07-14"
    assert r2["list"] and r2["list"][0]["code"] == "600000"
    assert r2["is_realtime"] is False


def test_longhu_settling_when_empty_no_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(mainforce_source, "_LONGHU_DATA_ROOT", tmp_path / "eastmoney-longhu")
    _install_eastmoney(monkeypatch, [])   # 空、无历史快照
    r = mainforce_source.longhu.__wrapped__("20260714")
    assert r["status"] == "settling"           # 结算中/未发布，不是"不可用"
    assert r["list"] == [] and r["snapshot_date"] is None


def test_longhu_source_error_when_safe_none(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(mainforce_source, "_LONGHU_DATA_ROOT", tmp_path / "eastmoney-longhu")
    _install_eastmoney(monkeypatch, [], error=True)   # safe→None
    r = mainforce_source.longhu.__wrapped__("20260714")
    assert r["status"] == "source_error"       # 当前源异常，区别于结算中


# ---------------------------------------------------------------- 缓存语义
def test_ttl_cache_long_ok_excludes_incomplete(monkeypatch) -> None:
    """盘外：非完整结果只享受基础 TTL，完整结果才进 6 小时长缓存。"""
    monkeypatch.setattr(base, "now_cn", lambda: datetime.datetime(2026, 7, 14, 22, 0))
    monkeypatch.setattr(base, "is_trade_day", lambda d: True)
    clock = {"t": 1000.0}
    monkeypatch.setattr(base.time, "monotonic", lambda: clock["t"])

    holder = {"status": "settling"}

    @base.ttl_cache(300, offhours_seconds=6 * 3600,
                    long_ok=lambda r: r.get("status") == "complete")
    def probe():
        return dict(holder)

    r1 = probe()
    clock["t"] += 400                       # 超基础 300s、远小于 6h
    r2 = probe()
    assert r2 is not r1                      # settling 未被 6h 长缓存 → 重新抓取

    holder["status"] = "complete"
    clock["t"] += 400                        # settling@... 过期
    rc = probe()
    assert rc["status"] == "complete"
    clock["t"] += 400                        # 超基础 300s、仍在 6h 内
    rc2 = probe()
    assert rc2 is rc                         # complete 进 6h 长缓存 → 同一对象


def test_invalidate_ttl_cache_forces_refresh() -> None:
    calls = {"n": 0}

    @base.ttl_cache(300)
    def probe2():
        calls["n"] += 1
        return {"v": calls["n"]}

    assert probe2()["v"] == 1
    assert probe2()["v"] == 1               # 命中缓存
    cleared = base.invalidate_ttl_cache(f"{probe2.__module__}.{probe2.__name__}:")
    assert cleared >= 1
    assert probe2()["v"] == 2               # 强制失效后重新执行


def test_official_snapshot_not_overwritten_by_empty(monkeypatch, tmp_path) -> None:
    """已落盘的官方完整快照不被今日空 settling 覆盖（最近完整证据可持续展示）。"""
    monkeypatch.setattr(official_longhu_source, "_DATA_ROOT", tmp_path / "official-longhu")
    complete = {
        "schema_version": "official-longhu-v1", "state": "complete", "trade_date": "2026-07-14",
        "events": [{"code": "600000"}, {"code": "000001"}, {"code": "300750"}],
    }
    assert official_longhu_source.save_snapshot(complete) is not None
    empty = {
        "schema_version": "official-longhu-v1", "state": "settling", "trade_date": "2026-07-14",
        "events": [],
    }
    assert official_longhu_source.save_snapshot(empty) is None          # 空不覆盖
    kept = official_longhu_source.load_snapshot("2026-07-14")
    assert kept["state"] == "complete" and len(kept["events"]) == 3     # 完整证据保留


# ---------------------------------------------------------------- 盘前候选前端/导出口径
def test_buyable_pool_exposes_date_and_validation_fields(monkeypatch) -> None:
    monkeypatch.setattr(screener, "_resolve_trade_day", lambda: "20260714")
    monkeypatch.setattr(screener, "_market_snapshot", lambda td: {
        "ok": True, "trade_date": "2026-07-14", "missing": [],
        "rows": [{"code": "600000", "name": "浦发", "change_pct": 2.0,
                  "amount": 5e8, "circ_mv": 300e8, "price": 10.0}],
    })
    out = premarket_brief.buyable_pool()
    assert out["data_date"] == "2026-07-14"
    assert out["today_validation_required"] is True
    assert all(s.get("today_validation_required") is True for s in out["stocks"])


def test_premarket_provenance_header_text() -> None:
    h = premarket_brief.premarket_provenance_header({"as_of": "2026-07-14", "auction_pending_count": 2})
    for token in ("基于 T-1", "2026-07-14", "非今日可执行", "竞价待核", "2 只", "盘中可执行"):
        assert token in h
    assert "上一交易日" in premarket_brief.premarket_provenance_header(None)   # 空 pool 降级不崩


# ---------------------------------------------------------------- 官方快照恢复策略
def test_official_load_latest_complete_skips_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(official_longhu_source, "_DATA_ROOT", tmp_path / "official-longhu")
    official_longhu_source.save_snapshot({
        "schema_version": "official-longhu-v1", "state": "complete", "trade_date": "2026-07-13",
        "events": [{"code": "600000"}]})
    official_longhu_source.save_snapshot({            # 07-14 空 settling（损坏空快照）
        "schema_version": "official-longhu-v1", "state": "settling", "trade_date": "2026-07-14",
        "events": []})
    got = official_longhu_source.load_latest_complete_snapshot()
    assert got is not None and got["trade_date"] == "2026-07-13"   # 跳过空，回退上一完整


def test_official_verification_recovers_to_prior_day(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(official_longhu_source, "_DATA_ROOT", tmp_path / "official-longhu")
    official_longhu_source.save_snapshot({
        "schema_version": "official-longhu-v1", "state": "complete", "trade_date": "2026-07-13",
        "events": [{"code": "600000"}, {"code": "000001"}]})
    # 今日 07-14 无快照 → 恢复回退 07-13，标日期，不做今日差异分类
    v = mainforce_source._official_verification("2026-07-14", [{"code": "600000"}], eastmoney_available=True)
    assert v["comparison_state"] == "reference_prior_day"
    assert v["official_cache"]["recovered_from_date"] == "2026-07-13"
    assert v["official_cache"]["recovered_code_count"] == 2
    assert v["counts"] == {"matched": 0, "eastmoney_only": 0, "official_only": 0}
