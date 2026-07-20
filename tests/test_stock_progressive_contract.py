from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from hermes_trader.data import stock


def test_tdx_board_returns_industry_and_concepts_without_public_f10(monkeypatch) -> None:
    monkeypatch.setattr(
        stock.tdxapi,
        "relations_for_codes",
        lambda _codes: {
            "600519": {
                "industry": {"name": "酿酒", "code": "881130.SH"},
                "concepts": [{"name": "白酒概念"}, {"name": "高股息"}],
                "source": "TDX relation",
            }
        },
    )

    assert stock._tdx_board("600519") == {
        "industry": "酿酒",
        "concepts": ["白酒概念", "高股息"],
        "source": "TDX relation",
    }


def test_detail_slot_is_released_only_after_all_background_work_finishes(monkeypatch) -> None:
    released: list[bool] = []
    monkeypatch.setattr(
        stock,
        "_INTERACTIVE_DETAIL_SLOTS",
        SimpleNamespace(release=lambda: released.append(True)),
    )
    first, second = Future(), Future()

    stock._release_detail_slot_when_done([first, second])
    first.set_result(None)
    assert released == []
    second.set_result(None)
    assert released == [True]


def test_segment_capacity_waits_for_healthy_previous_batch(monkeypatch) -> None:
    monkeypatch.setattr(stock, "_STOCK_SEGMENT_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(stock, "_STOCK_SEGMENT_SLOT_WAIT", 0.5)

    def slow(value: str) -> str:
        time.sleep(0.12)
        return value

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            stock._run_segment_jobs, {"value": (slow, ("first",))}, 0.4
        )
        time.sleep(0.02)
        second = pool.submit(
            stock._run_segment_jobs, {"value": (slow, ("second",))}, 0.4
        )
        first_result = first.result()
        second_result = second.result()

    assert first_result == ({"value": "first"}, set(), False)
    assert second_result == ({"value": "second"}, set(), False)


def test_quick_quote_does_not_wait_for_tdx_refresh(monkeypatch) -> None:
    def tdx_quotes(codes, *, max_age, wait):
        assert wait == 0
        assert max_age == 30
        return {}

    monkeypatch.setattr(stock.tdxapi, "quotes", tdx_quotes)
    monkeypatch.setattr(
        stock.tc,
        "quotes",
        lambda codes: {
            codes[0]: {
                "code": codes[0], "name": "测试股份", "price": 12.3,
                "change_pct": 1.2, "prev_close": 12.15, "open": 12.2,
                "pe": 18.0, "pb": 2.1, "source": "腾讯财经",
            }
        },
    )

    quote = stock.quick_quote("999991")

    assert quote is not None
    assert quote["name"] == "测试股份"
    assert quote["price"] == 12.3
    assert quote["prev_close"] == 12.15
    assert quote["pe"] == 18.0


def test_tdx_empty_name_never_overwrites_tencent_name(monkeypatch) -> None:
    monkeypatch.setattr(
        stock.tdxapi,
        "quotes",
        lambda codes, **_kwargs: {
            codes[0]: {
                "code": codes[0], "name": "", "price": 12.31,
                "prev_close": 12.0, "source": "TDX 数据 API",
            }
        },
    )
    monkeypatch.setattr(
        stock.tc,
        "quotes",
        lambda codes: {
            codes[0]: {
                "code": codes[0], "name": "测试股份", "price": 12.30,
                "prev_close": 12.0, "pe": 18.0, "source": "腾讯财经",
            }
        },
    )

    quote = stock.quick_quote("999994")

    assert quote is not None
    assert quote["name"] == "测试股份"
    assert quote["price"] == 12.31
    assert quote["pe"] == 18.0
    assert quote["source"] == "TDX 数据 API"


def test_tencent_success_does_not_wait_for_slow_tdx_api(monkeypatch) -> None:
    def slow_tdx(_codes, *, max_age, wait):
        assert wait == 0
        assert max_age == 30
        time.sleep(0.6)
        return {}

    monkeypatch.setattr(stock.tdxapi, "quotes", slow_tdx)
    monkeypatch.setattr(
        stock.tc,
        "quotes",
        lambda codes: {
            codes[0]: {
                "code": codes[0], "name": "快速灾备", "price": 9.9,
                "prev_close": 9.8, "source": "腾讯财经",
            }
        },
    )

    started = time.monotonic()
    quote = stock.quick_quote("999993")

    assert time.monotonic() - started < 0.4
    assert quote is not None
    assert quote["name"] == "快速灾备"
    assert quote["price"] == 9.9


def test_quote_pool_never_queues_when_source_workers_are_busy() -> None:
    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    called: list[bool] = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = stock._submit_quote_task(
            pool, slots, lambda: called.append(True)
        )
    assert future is None
    assert called == []
    slots.release()


def test_interactive_detail_keeps_fast_sections_when_optional_source_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        stock,
        "quick_quote",
        lambda code: {"code": code, "name": "测试股份", "price": 12.3, "prev_close": 12.0},
    )
    monkeypatch.setattr(
        stock,
        "_interactive_minute",
        lambda _code: {"rows": [{"t": "14:59", "price": 12.3, "avg": 12.1, "volume": 100}], "data_date": "2026-07-14"},
    )
    monkeypatch.setattr(
        stock.tx,
        "daily_bars",
        lambda _code, _days: [{"d": "2026-07-14", "o": 12.0, "c": 12.3, "h": 12.4, "l": 11.9, "v": 1000}],
    )
    monkeypatch.setattr(
        stock.tx,
        "moneyflow",
        lambda _code: (_ for _ in ()).throw(AssertionError("首屏不得调用个股资金流")),
    )

    started = time.monotonic()
    detail = stock.stock_detail("999992")

    assert time.monotonic() - started < 1.0
    assert detail["ok"] is True
    assert detail["state"] == "complete"
    assert detail["quote"]["name"] == "测试股份"
    assert detail["minute"]
    assert detail["daily"]
    assert detail["sections"]["flow"]["state"] == "deferred"
    assert "个股资金流" in detail["deferred"]
    assert detail["sections"]["board"]["state"] == "deferred"


def test_stock_core_recognizes_stock_without_waiting_for_enrichment(monkeypatch) -> None:
    quote = {"code": "600519", "name": "贵州茅台", "price": 1500.0, "prev_close": 1490.0}
    bars = [
        {"d": "2026-07-15", "o": 1490.0, "c": 1500.0, "h": 1510.0, "l": 1480.0, "v": 100}
    ]
    monkeypatch.setattr(
        stock,
        "_run_segment_jobs",
        lambda tasks, budget: ({"quote": quote, "daily": bars}, set(), False),
    )
    monkeypatch.setattr(stock, "_quote_freshness", lambda: (False, "2026-07-15", ""))
    monkeypatch.setattr(
        stock.tx,
        "cached_stock_identity",
        lambda _code: {"code": "600519", "known": True, "name": "贵州茅台", "universe_complete": True},
    )

    result = stock.stock_core.__wrapped__("600519")

    assert result["ok"] is True
    assert result["state"] == "complete"
    assert result["recognized"] is True
    assert result["quote"]["price"] == 1500.0
    assert result["recent_daily"] == bars
    assert result["quote"]["high_52w"] == 1510.0
    assert result["layer_contract"]["layer"] == "base"
    assert "fund_flow" in result["layer_contract"]["independent_from"]


def test_stock_core_rejects_index_alias_when_complete_stock_universe_misses(monkeypatch) -> None:
    """TDX 的 999999 指数别名不能被个股接口误认成一只股票。"""
    quote = {
        "code": "999999", "name": "", "price": 3843.08,
        "prev_close": 3882.41, "source": "TDX 数据 API",
    }
    monkeypatch.setattr(
        stock,
        "_run_segment_jobs",
        lambda tasks, budget: ({"quote": quote, "daily": []}, set(), False),
    )
    monkeypatch.setattr(stock, "_quote_freshness", lambda: (True, "2026-07-17", "收盘快照"))
    monkeypatch.setattr(
        stock.tx,
        "cached_stock_identity",
        lambda _code: {
            "code": "999999", "known": False, "name": "",
            "universe_complete": True, "universe_as_of": "2026-07-17",
        },
    )

    result = stock.stock_core.__wrapped__("999999")

    assert result["ok"] is False
    assert result["state"] == "not_found"
    assert result["recognized"] is False
    assert result["error_code"] == "stock_not_found"
    assert result["quote"] is None
    assert result["recent_daily"] == []


def test_stock_charts_keeps_daily_when_minute_source_fails(monkeypatch) -> None:
    bars = [
        {"d": "2026-07-15", "o": 10.0, "c": 10.2, "h": 10.3, "l": 9.9, "v": 100}
    ]
    monkeypatch.setattr(
        stock,
        "_run_segment_jobs",
        lambda tasks, budget: ({"minute": None, "daily": bars}, set(), False),
    )

    result = stock.stock_charts.__wrapped__("600001")

    assert result["ok"] is True
    assert result["state"] == "partial"
    assert result["daily"] == bars
    assert result["sections"]["minute"]["state"] == "missing"
    assert "分时" in result["missing"]
    assert result["layer_contract"]["layer"] == "charts"


def test_stock_enrichment_never_uses_public_f10_and_isolates_failures(monkeypatch) -> None:
    tasks_seen = set()

    def run(tasks, budget):
        tasks_seen.update(tasks)
        return {
            "board": {"industry": "白酒", "concepts": ["高股息"], "source": "TDX relation"},
            "flow": None,
            "financial": {"roe": 20.0, "source": "TDX stock_info"},
        }, set(), False

    monkeypatch.setattr(stock, "_run_segment_jobs", run)

    result = stock.stock_enrichment.__wrapped__("600519")

    assert tasks_seen == {"board", "flow", "financial"}
    assert result["ok"] is True
    assert result["state"] == "partial"
    assert result["board"]["source"] == "TDX relation"
    assert result["sections"]["flow"]["state"] == "missing"
    assert result["sections"]["auction"] == {
        "state": "unavailable", "reason": "authoritative_source_not_integrated"
    }
    assert "个股资金流" in result["missing"]
    assert result["layer_contract"] == {
        "version": "stock-progressive-v1",
        "layer": "enrichment",
        "blocks": ["industry", "concepts", "fund_flow", "financial"],
        "independent_from": ["quote", "minute", "daily"],
    }


def test_chat_consumes_three_independent_stock_segments() -> None:
    source = (Path(__file__).parents[1] / "frontend/js/views/chat.js").read_text(encoding="utf-8")

    assert "`/stock/${code}/core`" in source
    assert "`/stock/${code}/charts`" in source
    assert "`/stock/${code}/enrichment`" in source
    assert "swrGet(`/stock/${code}`" not in source
    assert "这不代表股票不存在" in source


def test_legacy_stock_detail_is_base_only_and_points_to_progressive_endpoints(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        stock,
        "_stock_detail_interactive_cached",
        lambda _code: {
            "ok": True,
            "quote": {"code": "600519", "name": "贵州茅台", "price": 1500.0},
            "minute": [],
            "daily": [],
        },
    )
    monkeypatch.setattr(
        stock.tx,
        "cached_stock_identity",
        lambda _code: {
            "code": "600519",
            "known": True,
            "name": "贵州茅台",
            "universe_complete": True,
        },
    )

    result = stock.stock_detail("600519")

    assert result["layer_contract"]["layer"] == "legacy_base_compat"
    assert result["deprecation"]["deprecated"] is True
    assert result["deprecation"]["replacement"] == [
        "/api/stock/600519/core",
        "/api/stock/600519/charts",
        "/api/stock/600519/enrichment",
    ]
    assert result.get("flow") is None
    assert result.get("financial") is None
