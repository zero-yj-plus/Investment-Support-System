"""Deterministic single-account projection for the local simulation ledger.

The order event stream remains the execution audit source.  This module keeps
the cash/lot projection, fee snapshots and equity curve in the same SQLite
transaction as a successful fill.  It contains no broker or network adapter.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import sqlite3
from typing import Any
import uuid

from .simulation_types import stable_hash


MONEY = Decimal("0.01")
PRICE = Decimal("0.0001")


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _price(value: Decimal) -> Decimal:
    return value.quantize(PRICE, rounding=ROUND_HALF_UP)


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _setting(value: Any, name: str, *, maximum: Decimal) -> Decimal:
    number = _decimal(value)
    if number is None or number < 0 or number > maximum:
        from .simulation import SimulationError

        raise SimulationError(
            "invalid_account_settings",
            f"{name} 必须是 0 到 {maximum} 之间的有限数值",
        )
    return number


def normalize_settings(payload: dict[str, Any]) -> dict[str, float]:
    """Validate explicit UI/API units: rates are percentages, slippage is bps."""
    return {
        "commission_rate_pct": float(_setting(
            payload.get("commission_rate_pct"), "佣金率(%)", maximum=Decimal("10"),
        )),
        "minimum_commission": float(_setting(
            payload.get("minimum_commission"), "单笔最低佣金(元)", maximum=Decimal("1000000"),
        )),
        "stamp_duty_rate_pct": float(_setting(
            payload.get("stamp_duty_rate_pct"), "卖出印花税率(%)", maximum=Decimal("10"),
        )),
        "transfer_fee_rate_pct": float(_setting(
            payload.get("transfer_fee_rate_pct"), "过户费率(%)", maximum=Decimal("10"),
        )),
        "slippage_bps": float(_setting(
            payload.get("slippage_bps", 0), "模拟滑点(bp)", maximum=Decimal("1000"),
        )),
    }


class SimulationAccountLedger:
    """Schema and transaction helpers for one local simulated account."""

    @staticmethod
    def init_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS simulation_account (
                singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                initial_cash REAL NOT NULL,
                cash REAL NOT NULL,
                realized_pnl REAL NOT NULL DEFAULT 0,
                total_fees REAL NOT NULL DEFAULT 0,
                commission_rate_pct REAL NOT NULL,
                minimum_commission REAL NOT NULL,
                stamp_duty_rate_pct REAL NOT NULL,
                transfer_fee_rate_pct REAL NOT NULL,
                slippage_bps REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS simulation_position_lots (
                lot_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                acquired_trade_date TEXT NOT NULL,
                unlock_trade_date TEXT NOT NULL,
                quantity_initial INTEGER NOT NULL,
                quantity_remaining INTEGER NOT NULL,
                unit_cost REAL NOT NULL,
                gross_amount REAL NOT NULL,
                buy_fees REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sim_lots_symbol_unlock
                ON simulation_position_lots(symbol, unlock_trade_date, created_at);

            CREATE TABLE IF NOT EXISTS simulation_account_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation TEXT NOT NULL,
                event_type TEXT NOT NULL,
                order_id TEXT,
                client_request_id TEXT UNIQUE,
                request_hash TEXT,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS simulation_account_prices (
                symbol TEXT PRIMARY KEY,
                price REAL NOT NULL,
                price_mode TEXT NOT NULL,
                as_of TEXT,
                source TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS simulation_equity_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                order_id TEXT,
                trade_date TEXT,
                cash REAL NOT NULL,
                market_value REAL,
                total_assets REAL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL,
                total_pnl REAL,
                total_fees REAL NOT NULL,
                valuation_state TEXT NOT NULL,
                missing_symbols_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sim_equity_created
                ON simulation_equity_snapshots(created_at, snapshot_id);
            """
        )

    @staticmethod
    def account_exists(conn: sqlite3.Connection) -> bool:
        return conn.execute(
            "SELECT 1 FROM simulation_account WHERE singleton=1"
        ).fetchone() is not None

    @staticmethod
    def symbols(conn: sqlite3.Connection) -> list[str]:
        return [
            str(row["symbol"])
            for row in conn.execute(
                """SELECT DISTINCT symbol FROM simulation_position_lots
                   WHERE quantity_remaining > 0 ORDER BY symbol"""
            ).fetchall()
        ]

    @staticmethod
    def _event(
        conn: sqlite3.Connection,
        *,
        operation: str,
        event_type: str,
        created_at: str,
        detail: dict[str, Any],
        client_request_id: str | None = None,
        request_hash: str | None = None,
        order_id: str | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO simulation_account_events(
                   operation,event_type,order_id,client_request_id,request_hash,detail_json,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                operation, event_type, order_id, client_request_id, request_hash,
                json.dumps(detail, ensure_ascii=False, sort_keys=True), created_at,
            ),
        )

    @staticmethod
    def _replay(
        conn: sqlite3.Connection,
        *,
        client_request_id: str,
        operation: str,
        request_hash: str,
    ) -> bool:
        row = conn.execute(
            """SELECT operation,request_hash FROM simulation_account_events
               WHERE client_request_id=?""",
            (client_request_id,),
        ).fetchone()
        if row is None:
            return False
        if row["operation"] != operation or row["request_hash"] != request_hash:
            from .simulation import SimulationError

            raise SimulationError(
                "idempotency_conflict",
                "client_request_id 已用于不同账户操作或不同请求内容",
                status_code=409,
            )
        return True

    @classmethod
    def initialize(
        cls,
        conn: sqlite3.Connection,
        *,
        client_request_id: str,
        initial_cash: Any,
        settings: dict[str, Any],
        created_at: str,
    ) -> bool:
        from .simulation import SimulationError

        cash = _decimal(initial_cash)
        if cash is None or cash <= 0 or cash > Decimal("1000000000000"):
            raise SimulationError(
                "invalid_initial_cash", "初始资金必须大于 0 且不超过 1 万亿元",
            )
        normalized = normalize_settings(settings)
        request_hash = stable_hash({"initial_cash": str(cash), **normalized})
        if cls._replay(
            conn, client_request_id=client_request_id,
            operation="account_initialize", request_hash=request_hash,
        ):
            return True
        if cls.account_exists(conn):
            raise SimulationError(
                "account_already_initialized",
                "模拟账户已经初始化；初始资金不能覆盖，费用可单独修改",
                status_code=409,
            )
        initial = _money(cash)
        conn.execute(
            """INSERT INTO simulation_account(
                   singleton,initial_cash,cash,realized_pnl,total_fees,
                   commission_rate_pct,minimum_commission,stamp_duty_rate_pct,
                   transfer_fee_rate_pct,slippage_bps,created_at,updated_at
               ) VALUES(1,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                float(initial), float(initial), 0.0, 0.0,
                normalized["commission_rate_pct"], normalized["minimum_commission"],
                normalized["stamp_duty_rate_pct"], normalized["transfer_fee_rate_pct"],
                normalized["slippage_bps"], created_at, created_at,
            ),
        )
        cls._event(
            conn, operation="account_initialize", event_type="account_initialized",
            client_request_id=client_request_id, request_hash=request_hash,
            detail={"initial_cash": float(initial), "fee_settings": normalized},
            created_at=created_at,
        )
        cls._insert_snapshot(
            conn, reason="account_initialized", order_id=None, trade_date=None,
            created_at=created_at,
        )
        return False

    @classmethod
    def update_settings(
        cls,
        conn: sqlite3.Connection,
        *,
        client_request_id: str,
        settings: dict[str, Any],
        created_at: str,
    ) -> bool:
        from .simulation import SimulationError

        normalized = normalize_settings(settings)
        request_hash = stable_hash(normalized)
        if cls._replay(
            conn, client_request_id=client_request_id,
            operation="account_settings", request_hash=request_hash,
        ):
            return True
        if not cls.account_exists(conn):
            raise SimulationError("account_not_initialized", "请先设置模拟账户初始资金", status_code=409)
        conn.execute(
            """UPDATE simulation_account SET commission_rate_pct=?,minimum_commission=?,
                   stamp_duty_rate_pct=?,transfer_fee_rate_pct=?,slippage_bps=?,updated_at=?
               WHERE singleton=1""",
            (
                normalized["commission_rate_pct"], normalized["minimum_commission"],
                normalized["stamp_duty_rate_pct"], normalized["transfer_fee_rate_pct"],
                normalized["slippage_bps"], created_at,
            ),
        )
        cls._event(
            conn, operation="account_settings", event_type="fee_settings_updated",
            client_request_id=client_request_id, request_hash=request_hash,
            detail={"fee_settings": normalized, "effective_for": "future_fills_only"},
            created_at=created_at,
        )
        return False

    @staticmethod
    def _fees(account: sqlite3.Row, side: str, gross: Decimal) -> dict[str, Decimal]:
        commission_rate = Decimal(str(account["commission_rate_pct"])) / Decimal("100")
        minimum = Decimal(str(account["minimum_commission"]))
        commission = Decimal("0")
        if commission_rate > 0:
            commission = max(gross * commission_rate, minimum)
        stamp = (
            gross * Decimal(str(account["stamp_duty_rate_pct"])) / Decimal("100")
            if side == "sell" else Decimal("0")
        )
        transfer = gross * Decimal(str(account["transfer_fee_rate_pct"])) / Decimal("100")
        commission = _money(commission)
        stamp = _money(stamp)
        transfer = _money(transfer)
        return {
            "commission_fee": commission,
            "stamp_duty_fee": stamp,
            "transfer_fee": transfer,
            "total_fee": commission + stamp + transfer,
        }

    @classmethod
    def plan_fill(
        cls,
        conn: sqlite3.Connection,
        *,
        order: dict[str, Any],
        quote_price: float,
        trade_date: str,
    ) -> tuple[dict[str, Any], list[str]]:
        from .simulation import SimulationError

        account = conn.execute("SELECT * FROM simulation_account WHERE singleton=1").fetchone()
        if account is None:
            return {}, ["account_not_initialized"]
        raw_price = Decimal(str(quote_price))
        slippage = Decimal(str(account["slippage_bps"])) / Decimal("10000")
        execution = raw_price * (Decimal("1") + slippage if order["side"] == "buy" else Decimal("1") - slippage)
        if execution <= 0:
            raise SimulationError("invalid_account_settings", "模拟滑点导致成交价格无效")
        execution = _price(execution)
        quantity = int(order["quantity"])
        gross = _money(execution * quantity)
        fees = cls._fees(account, str(order["side"]), gross)
        plan: dict[str, Any] = {
            "execution_price": execution,
            "gross_amount": gross,
            **fees,
            "cash_before": Decimal(str(account["cash"])),
            "realized_pnl": Decimal("0"),
            "consumptions": [],
            "fee_settings": {
                "commission_rate_pct": float(account["commission_rate_pct"]),
                "minimum_commission": float(account["minimum_commission"]),
                "stamp_duty_rate_pct": float(account["stamp_duty_rate_pct"]),
                "transfer_fee_rate_pct": float(account["transfer_fee_rate_pct"]),
                "slippage_bps": float(account["slippage_bps"]),
            },
        }
        if order["side"] == "buy":
            required = gross + fees["total_fee"]
            plan["cash_change"] = -required
            if plan["cash_before"] < required:
                return plan, ["insufficient_cash"]
            return plan, []

        lots = conn.execute(
            """SELECT * FROM simulation_position_lots
               WHERE symbol=? AND quantity_remaining>0
               ORDER BY acquired_trade_date,created_at,lot_id""",
            (order["symbol"],),
        ).fetchall()
        total = sum(int(row["quantity_remaining"]) for row in lots)
        available_lots = [row for row in lots if str(row["unlock_trade_date"]) <= trade_date]
        available = sum(int(row["quantity_remaining"]) for row in available_lots)
        if total < quantity:
            return plan, ["insufficient_position"]
        if available < quantity:
            return plan, ["t1_position_locked"]
        remaining = quantity
        cost_basis = Decimal("0")
        consumptions: list[tuple[str, int]] = []
        for row in available_lots:
            taken = min(remaining, int(row["quantity_remaining"]))
            if taken:
                consumptions.append((str(row["lot_id"]), taken))
                cost_basis += Decimal(str(row["unit_cost"])) * taken
                remaining -= taken
            if remaining == 0:
                break
        net_proceeds = gross - fees["total_fee"]
        plan["cash_change"] = net_proceeds
        plan["cost_basis"] = _money(cost_basis)
        plan["realized_pnl"] = _money(net_proceeds - cost_basis)
        plan["consumptions"] = consumptions
        return plan, []

    @classmethod
    def apply_fill(
        cls,
        conn: sqlite3.Connection,
        *,
        order: dict[str, Any],
        plan: dict[str, Any],
        client_request_id: str,
        quote_price: float,
        price_mode: str,
        market_as_of: str,
        market_source: str,
        trade_date: str,
        unlock_trade_date: str | None,
        created_at: str,
    ) -> dict[str, Any]:
        cash_after = _money(plan["cash_before"] + plan["cash_change"])
        conn.execute(
            """UPDATE simulation_account SET cash=?,realized_pnl=ROUND(realized_pnl+?,2),
                   total_fees=ROUND(total_fees+?,2),updated_at=? WHERE singleton=1""",
            (
                float(cash_after), float(plan["realized_pnl"]),
                float(plan["total_fee"]), created_at,
            ),
        )
        if order["side"] == "buy":
            if not unlock_trade_date:
                from .simulation import SimulationError

                raise SimulationError("trade_calendar_missing", "无法确定 T+1 可卖日期")
            quantity = int(order["quantity"])
            unit_cost = (plan["gross_amount"] + plan["total_fee"]) / quantity
            conn.execute(
                """INSERT INTO simulation_position_lots(
                       lot_id,order_id,symbol,acquired_trade_date,unlock_trade_date,
                       quantity_initial,quantity_remaining,unit_cost,gross_amount,buy_fees,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"lot_{uuid.uuid4().hex}", order["order_id"], order["symbol"], trade_date,
                    unlock_trade_date, quantity, quantity, float(unit_cost),
                    float(plan["gross_amount"]), float(plan["total_fee"]), created_at,
                ),
            )
        else:
            for lot_id, taken in plan["consumptions"]:
                conn.execute(
                    """UPDATE simulation_position_lots
                       SET quantity_remaining=quantity_remaining-?
                       WHERE lot_id=? AND quantity_remaining>=?""",
                    (taken, lot_id, taken),
                )
        conn.execute(
            """INSERT INTO simulation_account_prices(symbol,price,price_mode,as_of,source,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET price=excluded.price,price_mode=excluded.price_mode,
                   as_of=excluded.as_of,source=excluded.source,updated_at=excluded.updated_at""",
            (order["symbol"], quote_price, price_mode, market_as_of, market_source, created_at),
        )
        detail = {
            "symbol": order["symbol"], "side": order["side"],
            "quantity": int(order["quantity"]), "trade_date": trade_date,
            "price_mode": price_mode, "quote_price": quote_price,
            "execution_price": _float(plan["execution_price"]),
            "gross_amount": _float(plan["gross_amount"]),
            "commission_fee": _float(plan["commission_fee"]),
            "stamp_duty_fee": _float(plan["stamp_duty_fee"]),
            "transfer_fee": _float(plan["transfer_fee"]),
            "total_fee": _float(plan["total_fee"]),
            "cash_after": float(cash_after),
            "realized_pnl": _float(plan["realized_pnl"]),
            "fee_settings": plan["fee_settings"],
        }
        cls._event(
            conn, operation="fill", event_type="account_fill_applied",
            order_id=order["order_id"], client_request_id=client_request_id,
            request_hash=stable_hash({"order_id": order["order_id"]}),
            detail=detail, created_at=created_at,
        )
        cls._insert_snapshot(
            conn, reason="order_filled", order_id=order["order_id"],
            trade_date=trade_date, created_at=created_at,
        )
        return detail

    @staticmethod
    def _quote_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        return {
            str(row["symbol"]): dict(row)
            for row in conn.execute("SELECT * FROM simulation_account_prices").fetchall()
        }

    @classmethod
    def update_prices(
        cls,
        conn: sqlite3.Connection,
        quotes: dict[str, dict[str, Any]] | None,
        *,
        updated_at: str,
    ) -> None:
        for symbol, quote in (quotes or {}).items():
            price = _decimal(quote.get("price")) if isinstance(quote, dict) else None
            if price is None or price <= 0 or not re_symbol(symbol):
                continue
            conn.execute(
                """INSERT INTO simulation_account_prices(symbol,price,price_mode,as_of,source,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET price=excluded.price,price_mode=excluded.price_mode,
                       as_of=excluded.as_of,source=excluded.source,updated_at=excluded.updated_at""",
                (
                    symbol, float(price), str(quote.get("price_mode") or "unknown"),
                    quote.get("as_of"), quote.get("source"), updated_at,
                ),
            )

    @classmethod
    def replace_position_prices(
        cls,
        conn: sqlite3.Connection,
        quotes: dict[str, dict[str, Any]],
        *,
        updated_at: str,
        preserve_symbols: set[str] | None = None,
    ) -> None:
        preserve = preserve_symbols or set()
        for symbol in cls.symbols(conn):
            if symbol not in quotes and symbol not in preserve:
                conn.execute("DELETE FROM simulation_account_prices WHERE symbol=?", (symbol,))
        cls.update_prices(conn, quotes, updated_at=updated_at)

    @classmethod
    def _view(
        cls,
        conn: sqlite3.Connection,
        *,
        trade_date: str | None,
        price_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        account = conn.execute("SELECT * FROM simulation_account WHERE singleton=1").fetchone()
        if account is None:
            return {
                "schema_version": "simulation-account.v1",
                "initialized": False,
                "simulation_only": True,
                "broker_connected": False,
                "positions": [],
                "equity_curve": [],
            }
        price_map = cls._quote_map(conn) if price_overrides is None else {
            str(symbol): dict(quote)
            for symbol, quote in price_overrides.items()
            if isinstance(quote, dict)
        }
        rows = conn.execute(
            """SELECT symbol,quantity_remaining,unit_cost,unlock_trade_date
               FROM simulation_position_lots WHERE quantity_remaining>0
               ORDER BY symbol,acquired_trade_date,created_at"""
        ).fetchall()
        grouped: dict[str, dict[str, Decimal | int | str | None]] = {}
        for row in rows:
            symbol = str(row["symbol"])
            item = grouped.setdefault(symbol, {
                "quantity": 0, "available_quantity": 0, "cost_value": Decimal("0"),
                "next_sellable_date": None,
            })
            quantity = int(row["quantity_remaining"])
            item["quantity"] = int(item["quantity"]) + quantity
            item["cost_value"] = Decimal(item["cost_value"]) + Decimal(str(row["unit_cost"])) * quantity
            unlock = str(row["unlock_trade_date"])
            if trade_date and unlock <= trade_date:
                item["available_quantity"] = int(item["available_quantity"]) + quantity
            elif item["next_sellable_date"] is None or unlock < str(item["next_sellable_date"]):
                item["next_sellable_date"] = unlock
        positions: list[dict[str, Any]] = []
        market_total = Decimal("0")
        unrealized_total = Decimal("0")
        missing: list[str] = []
        for symbol, item in grouped.items():
            quantity = int(item["quantity"])
            cost_value = Decimal(item["cost_value"])
            quote = price_map.get(symbol)
            price = _decimal(quote.get("price")) if quote else None
            market_value = _money(price * quantity) if price is not None else None
            unrealized = _money(market_value - cost_value) if market_value is not None else None
            if market_value is None:
                missing.append(symbol)
            else:
                market_total += market_value
                unrealized_total += unrealized or Decimal("0")
            positions.append({
                "symbol": symbol,
                "quantity": quantity,
                "available_quantity": int(item["available_quantity"]),
                "t1_locked_quantity": quantity - int(item["available_quantity"]),
                "next_sellable_date": item["next_sellable_date"],
                "average_cost": _float(cost_value / quantity),
                "cost_value": _float(_money(cost_value)),
                "market_price": _float(price),
                "market_value": _float(market_value),
                "unrealized_pnl": _float(unrealized),
                "price_mode": quote.get("price_mode") if quote else None,
                "price_as_of": quote.get("as_of") if quote else None,
                "price_source": quote.get("source") if quote else None,
                "valuation_state": "exact" if market_value is not None else "missing",
            })
        cash = Decimal(str(account["cash"]))
        initial = Decimal(str(account["initial_cash"]))
        complete = not missing
        market_value = _money(market_total) if complete else None
        total_assets = _money(cash + market_total) if complete else None
        total_pnl = _money(total_assets - initial) if total_assets is not None else None
        return_pct = (
            (total_pnl / initial * Decimal("100")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            if total_pnl is not None and initial else None
        )
        return {
            "schema_version": "simulation-account.v1",
            "initialized": True,
            "simulation_only": True,
            "broker_connected": False,
            "initial_cash": float(initial),
            "cash": float(cash),
            "market_value": _float(market_value),
            "total_assets": _float(total_assets),
            "realized_pnl": _float(_money(Decimal(str(account["realized_pnl"])))),
            "unrealized_pnl": _float(_money(unrealized_total)) if complete else None,
            "total_pnl": _float(total_pnl),
            "return_pct": _float(return_pct),
            "total_fees": _float(_money(Decimal(str(account["total_fees"])))),
            "valuation_state": "exact" if complete else "missing",
            "missing_symbols": missing,
            "trade_date": trade_date,
            "fee_settings": {
                "commission_rate_pct": float(account["commission_rate_pct"]),
                "minimum_commission": float(account["minimum_commission"]),
                "stamp_duty_rate_pct": float(account["stamp_duty_rate_pct"]),
                "transfer_fee_rate_pct": float(account["transfer_fee_rate_pct"]),
                "slippage_bps": float(account["slippage_bps"]),
            },
            "positions": positions,
            "created_at": account["created_at"],
            "updated_at": account["updated_at"],
        }

    @classmethod
    def _insert_snapshot(
        cls,
        conn: sqlite3.Connection,
        *,
        reason: str,
        order_id: str | None,
        trade_date: str | None,
        created_at: str,
    ) -> None:
        view = cls._view(conn, trade_date=trade_date)
        if not view.get("initialized"):
            return
        conn.execute(
            """INSERT INTO simulation_equity_snapshots(
                   snapshot_id,reason,order_id,trade_date,cash,market_value,total_assets,
                   realized_pnl,unrealized_pnl,total_pnl,total_fees,valuation_state,
                   missing_symbols_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"eq_{uuid.uuid4().hex}", reason, order_id, trade_date, view["cash"],
                view["market_value"], view["total_assets"], view["realized_pnl"],
                view["unrealized_pnl"], view["total_pnl"], view["total_fees"],
                view["valuation_state"], json.dumps(view["missing_symbols"]), created_at,
            ),
        )

    @classmethod
    def record_valuation(
        cls,
        conn: sqlite3.Connection,
        *,
        client_request_id: str,
        quotes: dict[str, dict[str, Any]],
        trade_date: str,
        created_at: str,
    ) -> bool:
        from .simulation import SimulationError

        # The client intent is only "record one current valuation".  Server
        # quotes may move during a lost-response retry and must replay the first
        # append instead of turning the same request id into a conflict.
        request_hash = stable_hash({"operation": "account_valuation"})
        if cls._replay(
            conn, client_request_id=client_request_id,
            operation="account_valuation", request_hash=request_hash,
        ):
            return True
        if not cls.account_exists(conn):
            raise SimulationError("account_not_initialized", "请先设置模拟账户初始资金", status_code=409)
        cls.replace_position_prices(conn, quotes, updated_at=created_at)
        cls._event(
            conn, operation="account_valuation", event_type="valuation_recorded",
            client_request_id=client_request_id, request_hash=request_hash,
            detail={"trade_date": trade_date, "symbols": sorted(quotes)},
            created_at=created_at,
        )
        cls._insert_snapshot(
            conn, reason="manual_valuation", order_id=None,
            trade_date=trade_date, created_at=created_at,
        )
        return False

    @classmethod
    def view(
        cls,
        conn: sqlite3.Connection,
        *,
        quotes: dict[str, dict[str, Any]] | None,
        trade_date: str | None,
        updated_at: str,
        curve_limit: int = 365,
    ) -> dict[str, Any]:
        # Current GET valuation is deliberately not persisted.  The caller uses
        # an explicit valuation POST to append a new curve point.
        result = cls._view(
            conn,
            trade_date=trade_date,
            price_overrides=quotes,
        )
        bounded = max(1, min(int(curve_limit), 2000))
        rows = conn.execute(
            """SELECT snapshot_id,reason,order_id,trade_date,cash,market_value,total_assets,
                      realized_pnl,unrealized_pnl,total_pnl,total_fees,valuation_state,
                      missing_symbols_json,created_at
               FROM simulation_equity_snapshots ORDER BY rowid DESC LIMIT ?""",
            (bounded,),
        ).fetchall()
        curve: list[dict[str, Any]] = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item["missing_symbols"] = json.loads(item.pop("missing_symbols_json") or "[]")
            except (TypeError, json.JSONDecodeError):
                item.pop("missing_symbols_json", None)
                item["missing_symbols"] = []
            curve.append(item)
        result["equity_curve"] = curve
        return result


def re_symbol(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 6 and text.isdigit()
