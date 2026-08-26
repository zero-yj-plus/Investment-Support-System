"""持仓风险影子服务：后台采集、确定性评估、快照与显式降级。

核心算法位于 ``holding_risk``。本模块只负责适配现有应用装配和调度器；
影子卡固定不推送，普通 GET 只读快照，重型采集仅由后台任务或显式刷新触发。
"""
from __future__ import annotations

import asyncio
import copy
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import holding_risk as engine
from .holding_risk import (
    ENTRY_TYPE_LABELS,
    HORIZON_LABELS,
    HORIZON_PARAMS,
    RULE_VERSION,
    backtest_risk_events,
    evaluate_holding,
    load_cached_event_risk,
    normalize_daily_bars,
)
from ..data.base import is_trade_day, now_cn


log = logging.getLogger("hermes.holdings-risk")

SCHEMA_VERSION = "holding-risk-shadow-response-v1"
ALGORITHM_VERSION = RULE_VERSION
_REFRESH_WORKERS = 4


def wilder_atr(rows: list[dict], period: int = 14) -> float | None:
    """兼容旧导入；对日 K 使用同一引擎的 Wilder ATR。"""
    bars = normalize_daily_bars(rows, clock=datetime(2999, 1, 1, 16, 0))
    return engine._atr(bars, period)


def push_test_message() -> str:
    """固定链路测试文案；不读取真实持仓，也不是影子卡的正式推送。"""
    return (
        "【持仓风险影子卡·链路测试】\n"
        "测试标的（TEST）\n"
        "示例动作：暂停加仓，等待确认。\n"
        "依据：仅验证消息通道，未读取真实持仓、行情或风险状态。\n"
        "本消息不代表任何真实持仓结论。"
    )


def _safe_mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _market_context(overview: dict, benchmark: dict, sentiment_rows: list[dict]) -> dict:
    index_changes = [
        engine._number(row.get("change_pct"))
        for row in (overview.get("indexes") or [])[:7]
        if isinstance(row, dict)
    ]
    index_changes = [value for value in index_changes if value is not None]
    latest_sentiment = next(
        (row for row in reversed(sentiment_rows) if isinstance(row, dict)),
        {},
    )
    context = {
        "sentiment": engine._number(
            latest_sentiment.get("senti")
            if latest_sentiment.get("senti") is not None
            else latest_sentiment.get("score")
        ),
        "sentiment_label": latest_sentiment.get("senti_label") or latest_sentiment.get("label"),
        "regime": latest_sentiment.get("regime") or latest_sentiment.get("stage"),
        "risk_level": latest_sentiment.get("risk_level"),
        "market_change_pct": (
            sum(index_changes) / len(index_changes) if index_changes else None
        ),
        "data_date": overview.get("data_date") or latest_sentiment.get("date"),
        "benchmark": "沪深300",
    }
    benchmark_bars = normalize_daily_bars(benchmark.get("daily") or [])
    closes = [row["close"] for row in benchmark_bars]
    for period in (5, 20, 60):
        context[f"market_return_{period}d"] = (
            (closes[-1] / closes[-period - 1] - 1) * 100
            if len(closes) > period and closes[-period - 1]
            else None
        )
    return context


def _public_card(state: dict) -> dict:
    card = copy.deepcopy(state)
    card.pop("runtime_state", None)
    return card


class HoldingRiskService:
    def __init__(self, config, store, notifier) -> None:
        self.config = config
        self.store = store
        # 保留装配参数以兼容 main.py，但影子模式绝不调用通知器。
        self.notifier = notifier
        self._refresh_lock = asyncio.Lock()
        self._last_market_context: dict = {}

    def snapshots(self) -> dict[str, dict]:
        return {
            str(code).zfill(6): _public_card(state)
            for code, state in self.store.holdings_risk_states().items()
            if isinstance(state, dict)
        }

    def market_context(self) -> dict:
        return copy.deepcopy(self._last_market_context)

    async def refresh_all(self, force: bool = False) -> dict:
        if self._refresh_lock.locked():
            if force:
                # PUT 后的显式刷新不得因为后台首轮正在运行而读到旧快照。
                async with self._refresh_lock:
                    pass
            else:
                rows = self.snapshots()
                return {
                    "ok": True,
                    "skipped": "refresh_in_progress",
                    "shadow_mode": True,
                    "push_enabled": False,
                    "rule_version": RULE_VERSION,
                    "rows": rows,
                    "states": rows,
                }
        if self._refresh_lock.locked():
            rows = self.snapshots()
            return {
                "ok": True,
                "skipped": "refresh_in_progress",
                "shadow_mode": True,
                "push_enabled": False,
                "rule_version": RULE_VERSION,
                "rows": rows,
                "states": rows,
            }
        async with self._refresh_lock:
            holdings = [
                row for row in self.store.holdings()
                if isinstance(row, dict) and row.get("code")
            ]
            previous_states = self.store.holdings_risk_states()
            active_codes = {
                str(row.get("code") or "").split(".", 1)[0].zfill(6)
                for row in holdings
            }
            for obsolete_code in set(previous_states) - active_codes:
                self.store.delete_holding_risk_state(obsolete_code)
            if not holdings:
                return {
                    "ok": True,
                    "count": 0,
                    "shadow_mode": True,
                    "push_enabled": False,
                    "rule_version": RULE_VERSION,
                    "market_context": {},
                    "rows": {},
                    "states": {},
                }

            current = now_cn()
            minute_of_day = current.hour * 60 + current.minute
            active_session = bool(
                is_trade_day(current.date())
                and 9 * 60 + 25 <= minute_of_day <= 15 * 60 + 10
            )
            if not force and not active_session and active_codes.issubset(previous_states):
                rows = self.snapshots()
                return {
                    "ok": True,
                    "skipped": "outside_monitoring_session",
                    "shadow_mode": True,
                    "push_enabled": False,
                    "rule_version": RULE_VERSION,
                    "rows": rows,
                    "states": rows,
                }

            from ..data import free_source, market, stock  # noqa: PLC0415

            codes = [
                str(row.get("code") or "").split(".", 1)[0].zfill(6)
                for row in holdings
            ]
            source_results = await asyncio.gather(
                asyncio.to_thread(stock.quick_quotes, codes),
                asyncio.to_thread(market.market_overview),
                asyncio.to_thread(market.index_kline, "sh000300"),
                asyncio.to_thread(market.market_sectors),
                asyncio.to_thread(self.store.sentiment_history, 3),
                return_exceptions=True,
            )
            quotes = _safe_mapping(source_results[0])
            overview = _safe_mapping(source_results[1])
            benchmark = _safe_mapping(source_results[2])
            sector_payload = _safe_mapping(source_results[3])
            sentiment_rows = (
                source_results[4] if isinstance(source_results[4], list) else []
            )
            market_context = _market_context(
                overview,
                benchmark,
                sentiment_rows,
            )
            self._last_market_context = copy.deepcopy(market_context)
            sector_rows = {
                str(item.get("name") or "").strip(): item
                for item in (sector_payload.get("sectors") or [])
                if isinstance(item, dict)
                and item.get("kind") == "industry"
                and str(item.get("name") or "").strip()
            }
            index_rows = [
                item for item in (overview.get("indexes") or [])
                if isinstance(item, dict)
            ]

            def calculate(row: dict) -> tuple[str, dict]:
                code = str(row.get("code") or "").split(".", 1)[0].zfill(6)
                previous = (
                    previous_states.get(code)
                    if isinstance(previous_states.get(code), dict)
                    else None
                )
                try:
                    daily = free_source.daily_bars(code, 180)
                    if not isinstance(daily, list) or not daily:
                        raise RuntimeError("前复权日K未返回")
                    event_risk = load_cached_event_risk(
                        self.config.data_dir,
                        code,
                        today=current.date(),
                    )
                    card = evaluate_holding(
                        row,
                        quotes.get(code),
                        daily,
                        market_context=market_context,
                        event_risk=event_risk,
                        previous_runtime=(
                            previous.get("runtime_state")
                            if isinstance(previous, dict)
                            else None
                        ),
                        clock=current,
                    )
                    card["evaluated_at"] = current.isoformat(timespec="seconds")
                    card["fresh"] = card.get("coverage") == 1.0
                    card["stale"] = False
                except Exception as exc:  # noqa: BLE001
                    error = f"{type(exc).__name__}: {exc}"[:200]
                    log.info("持仓风险单票刷新失败 %s: %s", code, error)
                    if isinstance(previous, dict):
                        card = copy.deepcopy(previous)
                        card.update({
                            "shadow_mode": True,
                            "push_eligible": False,
                            "fresh": False,
                            "stale": True,
                            "refresh_failed_at": current.isoformat(timespec="seconds"),
                            "refresh_error": error,
                        })
                    else:
                        card = evaluate_holding(
                            row,
                            quotes.get(code),
                            [],
                            market_context=market_context,
                            event_risk={},
                            clock=current,
                        )
                        card.update({
                            "evaluated_at": current.isoformat(timespec="seconds"),
                            "fresh": False,
                            "stale": False,
                            "refresh_error": error,
                        })

                sector_name = str(row.get("industry") or "").strip()
                sector_row = sector_rows.get(sector_name) if sector_name else None
                if not sector_row and "ETF" in str(row.get("name") or "").upper():
                    matched = next(
                        (
                            item for item in index_rows
                            if str(item.get("name") or "")
                            and str(item.get("name") or "") in str(row.get("name") or "")
                        ),
                        None,
                    )
                    if matched:
                        sector_name = str(matched.get("name") or "ETF")
                        sector_row = matched
                    elif not sector_name:
                        sector_name = "ETF"
                stock_change = engine._number((quotes.get(code) or {}).get("change_pct"))
                sector_change = engine._number((sector_row or {}).get("change_pct"))
                card["sector"] = {
                    "name": sector_name or None,
                    "change_pct": (
                        round(sector_change, 4) if sector_change is not None else None
                    ),
                    "stock_change_pct": (
                        round(stock_change, 4) if stock_change is not None else None
                    ),
                    "relative_today_pct": (
                        round(stock_change - sector_change, 4)
                        if stock_change is not None and sector_change is not None
                        else None
                    ),
                    "data_date": (
                        sector_payload.get("data_date") or overview.get("data_date")
                    ),
                    "source": (sector_row or {}).get("source"),
                }
                return code, card

            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor(
                max_workers=_REFRESH_WORKERS,
                thread_name_prefix="holding-risk",
            ) as pool:
                results = await asyncio.gather(
                    *(loop.run_in_executor(pool, calculate, row) for row in holdings),
                    return_exceptions=True,
                )

            for result in results:
                if isinstance(result, BaseException):
                    log.info("持仓风险线程异常: %s", str(result)[:160])
                    continue
                code, state = result
                previous = previous_states.get(code)
                self.store.save_holding_risk_state(code, state)
                changed = (
                    previous is None
                    or previous.get("state") != state.get("state")
                    or previous.get("action") != state.get("action")
                    or previous.get("signal_type") != state.get("signal_type")
                    or (previous.get("levels") or {}).get("algorithm_stop")
                    != (state.get("levels") or {}).get("algorithm_stop")
                )
                if changed:
                    self.store.append_holding_risk_event({
                        "type": "shadow_evaluation_changed",
                        "code": code,
                        "rule_version": RULE_VERSION,
                        "state": state.get("state"),
                        "action": state.get("action"),
                        "signal_type": state.get("signal_type"),
                        "evaluated_at": state.get("evaluated_at"),
                        "push_eligible": False,
                    })

            rows = self.snapshots()
            return {
                "ok": True,
                "count": len(holdings),
                "evaluated": len(rows),
                "shadow_mode": True,
                "push_enabled": False,
                "rule_version": RULE_VERSION,
                "market_context": market_context,
                "rows": rows,
                # 兼容旧内部调用；新公开契约使用 rows。
                "states": rows,
            }


def register(scheduler, service: HoldingRiskService) -> None:
    """每5分钟后台刷新；影子模式只落快照和事件，不发送持仓通知。"""
    from apscheduler.triggers.interval import IntervalTrigger  # noqa: PLC0415

    scheduler.add_job(
        service.refresh_all,
        IntervalTrigger(minutes=5, timezone=service.config.timezone),
        id="holdings_risk_refresh",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
        next_run_time=now_cn(),
    )
