"""Server-side automatic matching for the local simulated account."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
import logging
import math
from typing import Any, Callable

from apscheduler.triggers.interval import IntervalTrigger

from ..data import stock
from ..data.base import is_trade_day, next_trade_day, now_cn
from ..simulation import SimulationError, SimulationStore


log = logging.getLogger("hermes.simulation_matcher")
MATCH_INTERVAL_SECONDS = 5


def in_matching_session(current: datetime | None = None) -> bool:
    """A-share auction/continuous matching window used by the simulator."""
    current = current or now_cn()
    if not is_trade_day(current.date()):
        return False
    hm = current.hour * 60 + current.minute
    return (9 * 60 + 25) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60)


def order_valid_trade_date(current: datetime | None = None) -> str:
    """Return the DAY session assigned to a newly submitted order."""
    current = current or now_cn()
    hm = current.hour * 60 + current.minute
    if is_trade_day(current.date()) and hm <= 15 * 60:
        return current.date().isoformat()
    value = next_trade_day(current.date())
    return value or current.date().isoformat()


class SimulationMatcher:
    def __init__(
        self,
        store: SimulationStore,
        *,
        quote_provider: Callable[[list[str]], dict[str, dict[str, Any]]] = stock.quick_quotes,
        clock: Callable[[], datetime] = now_cn,
    ) -> None:
        self.store = store
        self.quote_provider = quote_provider
        self.clock = clock

    async def _quotes(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}
        try:
            value = await asyncio.to_thread(self.quote_provider, symbols)
            return value if isinstance(value, dict) else {}
        except Exception:  # noqa: BLE001 - a failed quote round leaves orders pending
            log.exception("模拟撮合批量行情读取失败")
            return {}

    async def run_once(self) -> dict[str, Any]:
        current = self.clock()
        today = current.date().isoformat()
        after_close = (
            is_trade_day(current.date())
            and (current.hour * 60 + current.minute) > 15 * 60
        )
        expired = await asyncio.to_thread(
            self.store.expire_day_orders,
            valid_on=today,
            after_close=after_close,
        )
        migrated = await asyncio.to_thread(self.store.queue_legacy_proposals, valid_on=today)
        if not in_matching_session(current):
            return {
                "ok": True,
                "session": "closed",
                "matched": 0,
                "expired": expired,
                "migrated": migrated,
            }

        pending = await asyncio.to_thread(self.store.orders, limit=500, status="approved")
        pending = [row for row in pending if row.get("valid_trade_date") == today]
        if not pending:
            return {
                "ok": True,
                "session": "matching",
                "matched": 0,
                "waiting": 0,
                "skipped": 0,
                "expired": expired,
                "migrated": migrated,
            }
        symbols = list(dict.fromkeys([
            *(str(row.get("symbol") or "") for row in pending),
            *(await asyncio.to_thread(self.store.account_symbols)),
        ]))
        quotes = await self._quotes([symbol for symbol in symbols if symbol])
        matched = 0
        waiting = 0
        skipped = 0
        for order in pending:
            quote = quotes.get(str(order.get("symbol") or ""))
            price = quote.get("price") if isinstance(quote, dict) else None
            if (
                not isinstance(price, (int, float))
                or isinstance(price, bool)
                or not math.isfinite(float(price))
                or float(price) <= 0
                or bool((quote or {}).get("stale"))
            ):
                skipped += 1
                continue
            try:
                result = await asyncio.to_thread(
                    self.store.fill,
                    str(order["order_id"]),
                    client_request_id=(
                        f"matcher:{str(order['order_id'])[-36:]}:"
                        f"{current.strftime('%Y%m%dT%H%M%S%f')}"
                    ),
                    market_price=float(price),
                    as_of=self.store.current_time(),
                    market_source="server_stock_batch_quote_live",
                    market_stale=False,
                    market_missing=[],
                    price_mode="live",
                    trade_date=today,
                    unlock_trade_date=(
                        next_trade_day(date.fromisoformat(today))
                        if order.get("side") == "buy" else None
                    ),
                    valuation_quotes=quotes,
                )
                if result.get("matched"):
                    matched += 1
                else:
                    waiting += 1
            except SimulationError as exc:
                # Concurrent cancellation/fill is expected; the SQLite state machine
                # remains authoritative and no retry event is fabricated here.
                if exc.code not in {"invalid_transition", "order_not_found"}:
                    log.warning(
                        "模拟撮合订单处理失败 order=%s code=%s",
                        order.get("order_id"), exc.code,
                    )
                skipped += 1
            except Exception:  # noqa: BLE001 - one order must not block the pool
                log.exception("模拟撮合订单异常 order=%s", order.get("order_id"))
                skipped += 1
        return {
            "ok": True,
            "session": "matching",
            "matched": matched,
            "waiting": waiting,
            "skipped": skipped,
            "expired": expired,
            "migrated": migrated,
        }


def register(scheduler, matcher: SimulationMatcher) -> None:
    async def _tick() -> None:
        try:
            result = await matcher.run_once()
            if result.get("matched") or result.get("expired") or result.get("migrated"):
                log.info("模拟订单撮合完成: %s", result)
        except Exception:  # noqa: BLE001 - matching must not stop other jobs
            log.exception("模拟订单撮合轮次失败")

    scheduler.add_job(
        _tick,
        IntervalTrigger(seconds=MATCH_INTERVAL_SECONDS),
        id="simulation_matcher",
        name="模拟订单自动撮合",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=MATCH_INTERVAL_SECONDS,
    )
    log.info("已注册模拟订单自动撮合（每 %d 秒，DAY 有效）", MATCH_INTERVAL_SECONDS)
