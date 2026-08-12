"""Broker-free, append-only simulated order ledger.

This module intentionally contains no broker adapter, account credential, or
network call.  Orders can only move through the local SQLite state machine and
every lifecycle change is appended to ``simulation_order_events``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Callable
import uuid

from .simulation_account import SimulationAccountLedger


ORDER_STATES = {"proposed", "approved", "filled", "rejected", "cancelled"}
TERMINAL_STATES = {"filled", "rejected", "cancelled"}
VALID_SIDES = {"buy", "sell"}
VALID_ORDER_TYPES = {"market", "limit"}
_SYMBOL_RE = re.compile(r"^\d{6}$")
_REQUEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$")
_UNTRUSTED_MARKET_SOURCES = {"", "manual", "mock", "simulated", "simulation", "synthetic", "test"}
_AUTOMATED_APPROVERS = {
    "ai", "agent", "auto", "automatic", "bot", "hermes", "model", "system",
    "自动", "机器人", "模型", "系统",
}


class SimulationError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _aware_utc(value).isoformat(timespec="milliseconds")


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _positive_price(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    price = float(value)
    return price if math.isfinite(price) and price > 0 else None


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware_utc(parsed)


class SimulationStore:
    """SQLite/WAL simulated-order state machine with append-only events."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
        max_market_age_seconds: int = 120,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.max_market_age_seconds = max(1, int(max_market_age_seconds))
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS simulation_orders (
                    order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    quantity INTEGER,
                    limit_price REAL,
                    reference_price REAL,
                    risk_level INTEGER,
                    data_as_of TEXT,
                    data_stale INTEGER NOT NULL DEFAULT 0,
                    data_missing_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    rejection_reason TEXT,
                    approved_by TEXT,
                    approved_at TEXT,
                    market_price REAL,
                    market_as_of TEXT,
                    market_source TEXT,
                    slippage_per_share REAL,
                    slippage_bps REAL,
                    slippage_value REAL,
                    filled_at TEXT,
                    cancelled_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (status IN ('proposed','approved','filled','rejected','cancelled'))
                );
                CREATE INDEX IF NOT EXISTS idx_sim_orders_created
                    ON simulation_orders(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sim_orders_status
                    ON simulation_orders(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS simulation_order_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT REFERENCES simulation_orders(order_id),
                    operation TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT,
                    client_request_id TEXT UNIQUE,
                    request_hash TEXT,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sim_events_order
                    ON simulation_order_events(order_id, event_id);

                CREATE TABLE IF NOT EXISTS simulation_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                    emergency_stop INTEGER NOT NULL DEFAULT 0,
                    emergency_reason TEXT,
                    updated_at TEXT,
                    last_reset_by TEXT,
                    last_reset_reason TEXT,
                    last_reset_at TEXT
                );
                INSERT OR IGNORE INTO simulation_state(singleton, emergency_stop)
                    VALUES(1, 0);
                """
            )
            state_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(simulation_state)").fetchall()
            }
            for column in ("last_reset_by", "last_reset_reason", "last_reset_at"):
                if column not in state_columns:
                    conn.execute(f"ALTER TABLE simulation_state ADD COLUMN {column} TEXT")
            order_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(simulation_orders)").fetchall()
            }
            order_migrations = {
                "price_mode": "TEXT",
                "trade_date": "TEXT",
                "raw_quote_price": "REAL",
                "gross_amount": "REAL",
                "commission_fee": "REAL",
                "stamp_duty_fee": "REAL",
                "transfer_fee": "REAL",
                "total_fee": "REAL",
                "net_cash_change": "REAL",
                "realized_pnl": "REAL",
                "account_applied": "INTEGER NOT NULL DEFAULT 0",
                "valid_trade_date": "TEXT",
            }
            for column, definition in order_migrations.items():
                if column not in order_columns:
                    conn.execute(f"ALTER TABLE simulation_orders ADD COLUMN {column} {definition}")
            conn.execute(
                """UPDATE simulation_orders
                   SET valid_trade_date=COALESCE(valid_trade_date,trade_date,substr(created_at,1,10))
                   WHERE valid_trade_date IS NULL OR valid_trade_date=''"""
            )
            SimulationAccountLedger.init_schema(conn)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _now(self) -> datetime:
        return _aware_utc(self.clock())

    def current_time(self) -> str:
        """Server-authoritative observation time for API-side market snapshots."""
        return _iso(self._now())

    @staticmethod
    def _validate_request_id(client_request_id: Any) -> str:
        value = str(client_request_id or "").strip()
        if not _REQUEST_RE.fullmatch(value):
            raise SimulationError(
                "invalid_client_request_id",
                "client_request_id 需为 8-160 位字母、数字、点、冒号、下划线或连字符",
            )
        return value

    @staticmethod
    def _manual_approver(value: Any) -> str:
        actor = str(value or "").strip()[:120]
        actor_key = actor.casefold()
        actor_tokens = {
            token for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", actor_key) if token
        }
        automated = bool(actor_tokens & _AUTOMATED_APPROVERS) or any(
            marker in actor_key for marker in {"机器人", "自动批准", "系统自动", "ai agent"}
        )
        if not actor or automated:
            raise SimulationError(
                "manual_approver_required",
                "必须提供可识别的人工批准人，禁止 AI/系统自动批准",
            )
        return actor

    @staticmethod
    def _event_detail(row: sqlite3.Row) -> dict[str, Any]:
        try:
            return json.loads(row["detail_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}

    def _events_for(self, conn: sqlite3.Connection, order_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """SELECT event_id,event_type,from_status,to_status,detail_json,created_at
               FROM simulation_order_events WHERE order_id=? ORDER BY event_id""",
            (order_id,),
        ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "event_type": row["event_type"],
                "from_status": row["from_status"],
                "to_status": row["to_status"],
                "detail": self._event_detail(row),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _order_from_row(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        include_events: bool = True,
    ) -> dict[str, Any]:
        try:
            data_missing = json.loads(row["data_missing_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            data_missing = []
        order = dict(row)
        order["data_stale"] = bool(order.get("data_stale"))
        order["account_applied"] = bool(order.get("account_applied"))
        order["price_mode"] = order.get("price_mode") or (
            "latest_close" if order["data_stale"] else "live"
        )
        order["data_missing"] = data_missing if isinstance(data_missing, list) else []
        order.pop("data_missing_json", None)
        order["simulation_only"] = True
        order["broker_order_id"] = None
        if include_events:
            order["events"] = self._events_for(conn, str(row["order_id"]))
        return order

    def _order_in_conn(
        self,
        conn: sqlite3.Connection,
        order_id: str,
        *,
        include_events: bool = True,
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM simulation_orders WHERE order_id=?", (str(order_id),)
        ).fetchone()
        if row is None:
            raise SimulationError("order_not_found", "模拟订单不存在", status_code=404)
        return self._order_from_row(conn, row, include_events=include_events)

    def _append_event(
        self,
        conn: sqlite3.Connection,
        *,
        order_id: str | None,
        operation: str,
        event_type: str,
        from_status: str | None,
        to_status: str | None,
        detail: dict[str, Any] | None = None,
        client_request_id: str | None = None,
        request_hash: str | None = None,
        created_at: str,
    ) -> None:
        conn.execute(
            """INSERT INTO simulation_order_events(
                   order_id,operation,event_type,from_status,to_status,
                   client_request_id,request_hash,detail_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                order_id, operation, event_type, from_status, to_status,
                client_request_id, request_hash,
                json.dumps(detail or {}, ensure_ascii=False, sort_keys=True), created_at,
            ),
        )

    def _replay(
        self,
        conn: sqlite3.Connection,
        *,
        client_request_id: str,
        operation: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """SELECT order_id,operation,request_hash FROM simulation_order_events
               WHERE client_request_id=?""",
            (client_request_id,),
        ).fetchone()
        if row is None:
            return None
        if row["operation"] != operation or row["request_hash"] != request_hash:
            raise SimulationError(
                "idempotency_conflict",
                "client_request_id 已用于不同操作或不同请求内容",
                status_code=409,
            )
        if row["order_id"]:
            return {"idempotent_replay": True, "order": self._order_in_conn(conn, row["order_id"])}
        return {"idempotent_replay": True, "status": self._status_in_conn(conn)}

    def _is_emergency_stopped(self, conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT emergency_stop FROM simulation_state WHERE singleton=1"
        ).fetchone()
        return bool(row and row["emergency_stop"])

    def propose(
        self,
        *,
        client_request_id: str,
        symbol: Any,
        side: Any,
        order_type: Any,
        quantity: Any,
        reference_price: Any,
        limit_price: Any = None,
        risk_level: Any = None,
        data_as_of: Any = None,
        data_stale: Any = False,
        data_missing: Any = None,
        price_mode: Any = "live",
        trade_date: Any = None,
        valid_trade_date: Any = None,
    ) -> dict[str, Any]:
        request_id = self._validate_request_id(client_request_id)
        symbol_text = str(symbol or "").strip()
        side_text = str(side or "").strip().lower()
        type_text = str(order_type or "").strip().lower()
        missing = (
            [str(item).strip() for item in data_missing if str(item).strip()]
            if isinstance(data_missing, list)
            else (["data_missing_contract_invalid"] if data_missing not in (None, "") else [])
        )
        # Idempotency is bound to the stable client order intent.  Server risk,
        # quote, freshness, and as_of are authoritative snapshots that may
        # legitimately change during a network retry and must not turn a replay
        # into a 409.  Changing symbol/side/type/quantity/limit still conflicts.
        intent_payload = {
            "symbol": symbol_text,
            "side": side_text,
            "order_type": type_text,
            "quantity": quantity,
            "limit_price": limit_price,
        }
        request_hash = _stable_hash(intent_payload)
        now = self._now()
        now_text = _iso(now)
        data_time = _parse_time(data_as_of)
        ref_price = _positive_price(reference_price)
        normalized_limit = _positive_price(limit_price) if limit_price is not None else None
        valid_quantity = isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0
        valid_risk = isinstance(risk_level, int) and not isinstance(risk_level, bool)
        derived_data_stale = bool(data_stale)
        price_mode_text = str(price_mode or "").strip().lower()
        trade_date_text = str(trade_date or "").strip()[:10]
        valid_trade_date_text = str(
            valid_trade_date or trade_date_text or now.date().isoformat()
        ).strip()[:10]

        reasons: list[str] = []
        if not _SYMBOL_RE.fullmatch(symbol_text):
            reasons.append("invalid_symbol")
        if side_text not in VALID_SIDES:
            reasons.append("invalid_side")
        if type_text not in VALID_ORDER_TYPES:
            reasons.append("invalid_order_type")
        if not valid_quantity:
            reasons.append("invalid_quantity")
        elif side_text == "buy" and quantity % 100 != 0:
            reasons.append("buy_quantity_not_board_lot_100")
        if ref_price is None:
            reasons.append("invalid_reference_price")
        if type_text == "limit" and normalized_limit is None:
            reasons.append("invalid_limit_price")
        if type_text == "market" and limit_price is not None:
            reasons.append("market_order_cannot_have_limit_price")
        if price_mode_text not in {"live", "latest_close"}:
            reasons.append("invalid_price_mode")
        if price_mode_text == "latest_close" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", trade_date_text):
            reasons.append("trade_date_missing_or_invalid")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valid_trade_date_text):
            reasons.append("valid_trade_date_missing_or_invalid")

        if side_text == "buy":
            if not valid_risk:
                reasons.append("risk_level_missing_or_invalid")
            elif risk_level >= 4:
                reasons.append("risk_level_gte_4")
            if bool(data_stale) and price_mode_text != "latest_close":
                reasons.append("data_stale")
            if missing:
                reasons.append("data_missing")
            if data_time is None:
                reasons.append("data_as_of_missing_or_invalid")
            else:
                if price_mode_text == "live":
                    age = (now - data_time).total_seconds()
                    if age < 0:
                        reasons.append("data_as_of_future")
                    elif age > self.max_market_age_seconds:
                        reasons.append("data_stale")
                        derived_data_stale = True

        reasons = list(dict.fromkeys(reasons))
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._replay(
                conn, client_request_id=request_id, operation="propose", request_hash=request_hash,
            )
            if replay is not None:
                return replay
            if self._is_emergency_stopped(conn):
                reasons.append("emergency_stop_active")
                reasons = list(dict.fromkeys(reasons))
            order_id = f"sim_{uuid.uuid4().hex}"
            # 用户提交订单本身就是确认。沿用旧库允许的 ``approved`` 值表示
            # “已进入服务端撮合池”，避免重建带 CHECK/FK 的历史 SQLite 表。
            status = "rejected" if reasons else "approved"
            rejection = ";".join(reasons) or None
            conn.execute(
                """INSERT INTO simulation_orders(
                       order_id,symbol,side,order_type,quantity,limit_price,reference_price,
                       risk_level,data_as_of,data_stale,data_missing_json,price_mode,trade_date,
                       valid_trade_date,status,rejection_reason,approved_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    order_id, symbol_text, side_text, type_text,
                    quantity if valid_quantity else None, normalized_limit, ref_price,
                    risk_level if valid_risk else None,
                    _iso(data_time) if data_time else None,
                    1 if derived_data_stale else 0,
                    json.dumps(missing, ensure_ascii=False), price_mode_text, trade_date_text or None,
                    valid_trade_date_text or None, status, rejection,
                    None,
                    now_text, now_text,
                ),
            )
            if reasons:
                self._append_event(
                    conn,
                    order_id=order_id, operation="propose", event_type="submitted",
                    from_status=None, to_status="proposed", client_request_id=request_id,
                    request_hash=request_hash,
                    detail={"simulation_only": True, "time_in_force": "DAY"}, created_at=now_text,
                )
                self._append_event(
                    conn,
                    order_id=order_id, operation="validation", event_type="rejected",
                    from_status="proposed", to_status="rejected",
                    detail={"reasons": reasons}, created_at=now_text,
                )
            else:
                self._append_event(
                    conn,
                    order_id=order_id, operation="propose", event_type="queued",
                    from_status=None, to_status="approved", client_request_id=request_id,
                    request_hash=request_hash,
                    detail={
                        "simulation_only": True,
                        "matching_pool": True,
                        "time_in_force": "DAY",
                        "valid_trade_date": valid_trade_date_text,
                    },
                    created_at=now_text,
                )
            return {"idempotent_replay": False, "order": self._order_in_conn(conn, order_id)}

    def approve(
        self,
        order_id: str,
        *,
        client_request_id: str,
        approved_by: Any,
    ) -> dict[str, Any]:
        request_id = self._validate_request_id(client_request_id)
        actor = self._manual_approver(approved_by)
        request_hash = _stable_hash({"order_id": order_id, "approved_by": actor})
        now_text = _iso(self._now())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._replay(
                conn, client_request_id=request_id, operation="approve", request_hash=request_hash,
            )
            if replay is not None:
                return replay
            order = self._order_in_conn(conn, order_id, include_events=False)
            if order["status"] != "proposed":
                raise SimulationError(
                    "invalid_transition",
                    f"只有 proposed 订单可人工批准，当前为 {order['status']}",
                    status_code=409,
                )
            if self._is_emergency_stopped(conn):
                raise SimulationError("emergency_stop_active", "紧急停止已启用，禁止批准", status_code=409)
            conn.execute(
                """UPDATE simulation_orders SET status='approved',approved_by=?,approved_at=?,updated_at=?
                   WHERE order_id=? AND status='proposed'""",
                (actor, now_text, now_text, order_id),
            )
            self._append_event(
                conn,
                order_id=order_id, operation="approve", event_type="approved",
                from_status="proposed", to_status="approved",
                client_request_id=request_id, request_hash=request_hash,
                detail={"approved_by": actor, "manual_approval": True}, created_at=now_text,
            )
            return {"idempotent_replay": False, "order": self._order_in_conn(conn, order_id)}

    def fill(
        self,
        order_id: str,
        *,
        client_request_id: str,
        market_price: Any,
        as_of: Any,
        market_source: Any,
        market_stale: Any = False,
        market_missing: Any = None,
        price_mode: Any = "live",
        trade_date: Any = None,
        unlock_trade_date: Any = None,
        valuation_quotes: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        request_id = self._validate_request_id(client_request_id)
        source = str(market_source or "").strip()
        # Fill has exactly one stable client intent: execute this approved order.
        # Price/source/as_of are server snapshots and are excluded so retries
        # replay the first terminal result even when the live quote has moved.
        request_hash = _stable_hash({"order_id": order_id})
        now = self._now()
        now_text = _iso(now)
        price = _positive_price(market_price)
        market_time = _parse_time(as_of)
        price_mode_text = str(price_mode or "").strip().lower()
        trade_date_text = str(trade_date or "").strip()[:10]
        unlock_date_text = str(unlock_trade_date or "").strip()[:10]
        reasons: list[str] = []
        if price is None:
            reasons.append("invalid_market_price")
        if market_time is None:
            reasons.append("market_as_of_missing_or_invalid")
        else:
            if price_mode_text == "live":
                age = (now - market_time).total_seconds()
                if age < 0:
                    reasons.append("market_as_of_future")
                elif age > self.max_market_age_seconds:
                    reasons.append("market_data_stale")
        if source.lower() in _UNTRUSTED_MARKET_SOURCES:
            reasons.append("real_market_source_required")
        if bool(market_stale) and price_mode_text != "latest_close":
            reasons.append("market_data_stale")
        if price_mode_text not in {"live", "latest_close"}:
            reasons.append("invalid_price_mode")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", trade_date_text):
            reasons.append("trade_date_missing_or_invalid")
        if isinstance(market_missing, list) and any(str(item).strip() for item in market_missing):
            reasons.append("market_data_missing")
        elif market_missing not in (None, [], ""):
            reasons.append("market_data_missing")

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._replay(
                conn, client_request_id=request_id, operation="fill", request_hash=request_hash,
            )
            if replay is not None:
                return replay
            order = self._order_in_conn(conn, order_id, include_events=False)
            if order["status"] != "approved":
                raise SimulationError(
                    "manual_approval_required" if order["status"] == "proposed" else "invalid_transition",
                    f"只有人工批准后的 approved 订单可模拟成交，当前为 {order['status']}",
                    status_code=409,
                )
            if self._is_emergency_stopped(conn):
                reasons.append("emergency_stop_active")
            if not reasons and order["order_type"] == "limit":
                limit_price = float(order["limit_price"])
                marketable = (
                    float(price) <= limit_price
                    if order["side"] == "buy"
                    else float(price) >= limit_price
                )
                if not marketable:
                    # 未触价是挂单的正常状态，不是拒绝，更不能把订单变成终态。
                    return {
                        "idempotent_replay": False,
                        "matched": False,
                        "order": self._order_in_conn(conn, order_id),
                    }

            account_plan: dict[str, Any] = {}
            if not reasons:
                account_plan, account_reasons = SimulationAccountLedger.plan_fill(
                    conn,
                    order=order,
                    quote_price=float(price),
                    trade_date=trade_date_text,
                )
                reasons.extend(account_reasons)
            execution_price = float(account_plan.get("execution_price") or price or 0)

            if reasons:
                rejection = ";".join(list(dict.fromkeys(reasons)))
                conn.execute(
                    """UPDATE simulation_orders SET status='rejected',rejection_reason=?,updated_at=?
                       WHERE order_id=? AND status='approved'""",
                    (rejection, now_text, order_id),
                )
                self._append_event(
                    conn,
                    order_id=order_id, operation="fill", event_type="rejected",
                    from_status="approved", to_status="rejected",
                    client_request_id=request_id, request_hash=request_hash,
                    detail={"reasons": list(dict.fromkeys(reasons))}, created_at=now_text,
                )
                return {"idempotent_replay": False, "order": self._order_in_conn(conn, order_id)}

            if valuation_quotes is not None:
                SimulationAccountLedger.replace_position_prices(
                    conn,
                    valuation_quotes,
                    updated_at=now_text,
                    preserve_symbols={str(order["symbol"])},
                )
            financial = SimulationAccountLedger.apply_fill(
                conn,
                order=order,
                plan=account_plan,
                client_request_id=request_id,
                quote_price=float(price),
                price_mode=price_mode_text,
                market_as_of=_iso(market_time),
                market_source=source,
                trade_date=trade_date_text,
                unlock_trade_date=unlock_date_text or None,
                created_at=now_text,
            )
            raw_quote = float(price)
            adverse_per_share = execution_price - raw_quote if order["side"] == "buy" else raw_quote - execution_price
            slippage_bps = adverse_per_share / raw_quote * 10_000
            slippage_value = adverse_per_share * int(order["quantity"])
            conn.execute(
                """UPDATE simulation_orders SET
                       status='filled',market_price=?,raw_quote_price=?,market_as_of=?,market_source=?,
                       price_mode=?,trade_date=?,gross_amount=?,commission_fee=?,stamp_duty_fee=?,
                       transfer_fee=?,total_fee=?,net_cash_change=?,realized_pnl=?,account_applied=1,
                       slippage_per_share=?,slippage_bps=?,slippage_value=?,filled_at=?,updated_at=?
                   WHERE order_id=? AND status='approved'""",
                (
                    execution_price, price, _iso(market_time), source,
                    price_mode_text, trade_date_text, financial["gross_amount"],
                    financial["commission_fee"], financial["stamp_duty_fee"],
                    financial["transfer_fee"], financial["total_fee"],
                    round(financial["cash_after"] - float(account_plan["cash_before"]), 2),
                    financial["realized_pnl"],
                    round(adverse_per_share, 6), round(slippage_bps, 4), round(slippage_value, 4),
                    now_text, now_text, order_id,
                ),
            )
            self._append_event(
                conn,
                order_id=order_id, operation="fill", event_type="filled",
                from_status="approved", to_status="filled",
                client_request_id=request_id, request_hash=request_hash,
                detail={
                    "market_price": execution_price, "raw_quote_price": price,
                    "market_as_of": _iso(market_time), "market_source": source,
                    "price_mode": price_mode_text, "trade_date": trade_date_text,
                    "slippage_bps": round(slippage_bps, 4),
                    "fees": {
                        "commission": financial["commission_fee"],
                        "stamp_duty": financial["stamp_duty_fee"],
                        "transfer": financial["transfer_fee"],
                        "total": financial["total_fee"],
                    },
                },
                created_at=now_text,
            )
            return {
                "idempotent_replay": False,
                "matched": True,
                "order": self._order_in_conn(conn, order_id),
            }

    def queue_legacy_proposals(self, *, valid_on: str) -> int:
        """Move old manually-approved proposals into the automatic matching pool."""
        date_text = str(valid_on or "").strip()[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
            raise SimulationError("valid_trade_date_missing_or_invalid", "撮合交易日无效")
        now_text = _iso(self._now())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT order_id FROM simulation_orders
                   WHERE status='proposed' AND valid_trade_date>=?""",
                (date_text,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """UPDATE simulation_orders SET status='approved',updated_at=?
                       WHERE order_id=? AND status='proposed'""",
                    (now_text, row["order_id"]),
                )
                self._append_event(
                    conn,
                    order_id=row["order_id"], operation="lifecycle_migration",
                    event_type="queued", from_status="proposed", to_status="approved",
                    detail={"matching_pool": True, "reason": "automatic_matching_enabled"},
                    created_at=now_text,
                )
            return len(rows)

    def expire_day_orders(self, *, valid_on: str, after_close: bool = False) -> int:
        """Cancel unfilled DAY orders once their matching session has ended."""
        date_text = str(valid_on or "").strip()[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
            raise SimulationError("valid_trade_date_missing_or_invalid", "撮合交易日无效")
        now_text = _iso(self._now())
        comparator = "<=" if after_close else "<"
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"""SELECT order_id,status FROM simulation_orders
                    WHERE status IN ('proposed','approved')
                      AND valid_trade_date {comparator} ?""",
                (date_text,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """UPDATE simulation_orders SET status='cancelled',rejection_reason='day_order_expired',
                           cancelled_at=?,updated_at=?
                       WHERE order_id=? AND status IN ('proposed','approved')""",
                    (now_text, now_text, row["order_id"]),
                )
                self._append_event(
                    conn,
                    order_id=row["order_id"], operation="day_expiry", event_type="expired",
                    from_status=row["status"], to_status="cancelled",
                    detail={"reason": "day_order_expired", "valid_trade_date": date_text},
                    created_at=now_text,
                )
            return len(rows)

    def cancel(
        self,
        order_id: str,
        *,
        client_request_id: str,
        reason: Any = "operator_cancelled",
    ) -> dict[str, Any]:
        request_id = self._validate_request_id(client_request_id)
        reason_text = str(reason or "operator_cancelled").strip()[:240] or "operator_cancelled"
        request_hash = _stable_hash({"order_id": order_id, "reason": reason_text})
        now_text = _iso(self._now())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._replay(
                conn, client_request_id=request_id, operation="cancel", request_hash=request_hash,
            )
            if replay is not None:
                return replay
            order = self._order_in_conn(conn, order_id, include_events=False)
            if order["status"] not in {"proposed", "approved"}:
                raise SimulationError(
                    "invalid_transition",
                    f"只有 proposed/approved 订单可取消，当前为 {order['status']}",
                    status_code=409,
                )
            conn.execute(
                """UPDATE simulation_orders SET status='cancelled',rejection_reason=?,cancelled_at=?,updated_at=?
                   WHERE order_id=? AND status IN ('proposed','approved')""",
                (reason_text, now_text, now_text, order_id),
            )
            self._append_event(
                conn,
                order_id=order_id, operation="cancel", event_type="cancelled",
                from_status=order["status"], to_status="cancelled",
                client_request_id=request_id, request_hash=request_hash,
                detail={"reason": reason_text}, created_at=now_text,
            )
            return {"idempotent_replay": False, "order": self._order_in_conn(conn, order_id)}

    def emergency_stop(
        self,
        *,
        client_request_id: str,
        reason: Any,
    ) -> dict[str, Any]:
        request_id = self._validate_request_id(client_request_id)
        reason_text = str(reason or "").strip()[:240]
        if not reason_text:
            raise SimulationError("emergency_reason_required", "紧急停止必须填写原因")
        request_hash = _stable_hash({"reason": reason_text})
        now_text = _iso(self._now())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._replay(
                conn,
                client_request_id=request_id,
                operation="emergency_stop",
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            pending = conn.execute(
                "SELECT order_id,status FROM simulation_orders WHERE status IN ('proposed','approved')"
            ).fetchall()
            conn.execute(
                """UPDATE simulation_state SET emergency_stop=1,emergency_reason=?,updated_at=?
                   WHERE singleton=1""",
                (reason_text, now_text),
            )
            for row in pending:
                conn.execute(
                    """UPDATE simulation_orders SET status='cancelled',rejection_reason=?,cancelled_at=?,updated_at=?
                       WHERE order_id=? AND status IN ('proposed','approved')""",
                    ("emergency_stop:" + reason_text, now_text, now_text, row["order_id"]),
                )
                self._append_event(
                    conn,
                    order_id=row["order_id"], operation="emergency_stop",
                    event_type="cancelled", from_status=row["status"], to_status="cancelled",
                    detail={"reason": "emergency_stop", "emergency_reason": reason_text},
                    created_at=now_text,
                )
            self._append_event(
                conn,
                order_id=None, operation="emergency_stop", event_type="emergency_stop_enabled",
                from_status=None, to_status=None,
                client_request_id=request_id, request_hash=request_hash,
                detail={"reason": reason_text, "cancelled_orders": len(pending)}, created_at=now_text,
            )
            return {"idempotent_replay": False, "status": self._status_in_conn(conn)}

    def reset_emergency_stop(
        self,
        *,
        client_request_id: str,
        approved_by: Any,
        reason: Any,
    ) -> dict[str, Any]:
        """Manually clear emergency stop while preserving an append-only audit event."""
        request_id = self._validate_request_id(client_request_id)
        actor = self._manual_approver(approved_by)
        reason_text = str(reason or "").strip()[:240]
        if not reason_text:
            raise SimulationError("reset_reason_required", "解除紧急停止必须填写人工复核原因")
        request_hash = _stable_hash({"approved_by": actor, "reason": reason_text})
        now_text = _iso(self._now())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = self._replay(
                conn,
                client_request_id=request_id,
                operation="emergency_stop_reset",
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            if not self._is_emergency_stopped(conn):
                raise SimulationError(
                    "emergency_stop_not_active",
                    "紧急停止当前未启用，不能重复静默解除",
                    status_code=409,
                )
            conn.execute(
                """UPDATE simulation_state SET
                       emergency_stop=0,emergency_reason=NULL,updated_at=?,
                       last_reset_by=?,last_reset_reason=?,last_reset_at=?
                   WHERE singleton=1 AND emergency_stop=1""",
                (now_text, actor, reason_text, now_text),
            )
            self._append_event(
                conn,
                order_id=None,
                operation="emergency_stop_reset",
                event_type="emergency_stop_reset",
                from_status=None,
                to_status=None,
                client_request_id=request_id,
                request_hash=request_hash,
                detail={
                    "approved_by": actor,
                    "reason": reason_text,
                    "manual_approval": True,
                },
                created_at=now_text,
            )
            return {"idempotent_replay": False, "status": self._status_in_conn(conn)}

    def get_order(self, order_id: str) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            return self._order_in_conn(conn, order_id)

    def orders(self, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        status_text = str(status or "").strip().lower()
        if status_text and status_text not in ORDER_STATES:
            raise SimulationError("invalid_status_filter", "未知模拟订单状态")
        with self._lock, self._connect() as conn:
            if status_text:
                rows = conn.execute(
                    """SELECT * FROM simulation_orders WHERE status=?
                       ORDER BY created_at DESC LIMIT ?""",
                    (status_text, bounded),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM simulation_orders ORDER BY created_at DESC LIMIT ?",
                    (bounded,),
                ).fetchall()
            return [self._order_from_row(conn, row) for row in rows]

    def initialize_account(
        self,
        *,
        client_request_id: str,
        initial_cash: Any,
        commission_rate_pct: Any,
        minimum_commission: Any,
        stamp_duty_rate_pct: Any,
        transfer_fee_rate_pct: Any,
        slippage_bps: Any = 0,
    ) -> dict[str, Any]:
        request_id = self._validate_request_id(client_request_id)
        now_text = _iso(self._now())
        settings = {
            "commission_rate_pct": commission_rate_pct,
            "minimum_commission": minimum_commission,
            "stamp_duty_rate_pct": stamp_duty_rate_pct,
            "transfer_fee_rate_pct": transfer_fee_rate_pct,
            "slippage_bps": slippage_bps,
        }
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = SimulationAccountLedger.initialize(
                conn,
                client_request_id=request_id,
                initial_cash=initial_cash,
                settings=settings,
                created_at=now_text,
            )
            return {"idempotent_replay": replay}

    def update_account_settings(
        self,
        *,
        client_request_id: str,
        commission_rate_pct: Any,
        minimum_commission: Any,
        stamp_duty_rate_pct: Any,
        transfer_fee_rate_pct: Any,
        slippage_bps: Any = 0,
    ) -> dict[str, Any]:
        request_id = self._validate_request_id(client_request_id)
        now_text = _iso(self._now())
        settings = {
            "commission_rate_pct": commission_rate_pct,
            "minimum_commission": minimum_commission,
            "stamp_duty_rate_pct": stamp_duty_rate_pct,
            "transfer_fee_rate_pct": transfer_fee_rate_pct,
            "slippage_bps": slippage_bps,
        }
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = SimulationAccountLedger.update_settings(
                conn,
                client_request_id=request_id,
                settings=settings,
                created_at=now_text,
            )
            return {"idempotent_replay": replay}

    def account_symbols(self) -> list[str]:
        with self._lock, self._connect() as conn:
            return SimulationAccountLedger.symbols(conn)

    def account_view(
        self,
        *,
        quotes: dict[str, dict[str, Any]] | None = None,
        trade_date: str | None = None,
        curve_limit: int = 365,
    ) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            return SimulationAccountLedger.view(
                conn,
                quotes=quotes,
                trade_date=trade_date,
                updated_at=_iso(self._now()),
                curve_limit=curve_limit,
            )

    def record_account_valuation(
        self,
        *,
        client_request_id: str,
        quotes: dict[str, dict[str, Any]],
        trade_date: str,
    ) -> dict[str, Any]:
        request_id = self._validate_request_id(client_request_id)
        now_text = _iso(self._now())
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            replay = SimulationAccountLedger.record_valuation(
                conn,
                client_request_id=request_id,
                quotes=quotes,
                trade_date=trade_date,
                created_at=now_text,
            )
            return {"idempotent_replay": replay}

    def _status_in_conn(self, conn: sqlite3.Connection) -> dict[str, Any]:
        state = conn.execute(
            """SELECT emergency_stop,emergency_reason,updated_at,
                      last_reset_by,last_reset_reason,last_reset_at
               FROM simulation_state WHERE singleton=1"""
        ).fetchone()
        counts = {state_name: 0 for state_name in sorted(ORDER_STATES)}
        counts.update({
            row["status"]: int(row["n"])
            for row in conn.execute(
                "SELECT status,COUNT(*) AS n FROM simulation_orders GROUP BY status"
            ).fetchall()
        })
        latest = conn.execute(
            """SELECT event_id,event_type,order_id,detail_json,created_at FROM simulation_order_events
               ORDER BY event_id DESC LIMIT 1"""
        ).fetchone()
        latest_event = None
        if latest:
            latest_event = dict(latest)
            try:
                latest_event["detail"] = json.loads(latest_event.pop("detail_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                latest_event.pop("detail_json", None)
                latest_event["detail"] = {}
        return {
            "schema_version": "simulation-status.v3",
            "mode": "simulation_only",
            "simulation_only": True,
            "broker_connected": False,
            "broker_interface_present": False,
            "manual_approval_required": False,
            "automatic_matching": True,
            "matching_interval_seconds": 5,
            "order_time_in_force": "DAY",
            "account_initialized": SimulationAccountLedger.account_exists(conn),
            "settlement_rule": "A_SHARE_T_PLUS_1",
            "emergency_stop": {
                "active": bool(state and state["emergency_stop"]),
                "reason": state["emergency_reason"] if state else None,
                "updated_at": state["updated_at"] if state else None,
            },
            "last_emergency_reset": {
                "approved_by": state["last_reset_by"],
                "reason": state["last_reset_reason"],
                "at": state["last_reset_at"],
            } if state and state["last_reset_at"] else None,
            "counts": counts,
            "latest_event": latest_event,
            "disclaimer": "仅模拟记录，不连接券商，不会产生真实委托或成交。",
        }

    def status(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            return self._status_in_conn(conn)
