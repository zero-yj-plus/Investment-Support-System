"""Point-in-time evidence persistence for normalized market data.

The store intentionally has no dependency on live adapters or the API layer. It
persists the source registry, canonical raw payloads, normalized observations,
and their lineage. PIT reads use both available_at and fetched_at, so a later
revision of an old event cannot leak into an earlier decision.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid


_SENSITIVE_QUERY_KEYS = {
    "access_token", "api_key", "apikey", "app_secret", "auth", "authorization",
    "credential", "key", "license", "password", "pwd", "secret", "sign",
    "signature", "token",
}


def normalize_timestamp(value: datetime | date | str | None) -> str | None:
    """Return a UTC ISO timestamp suitable for lexicographic SQLite order.

    Adapters should provide timezone-aware timestamps. Naive values are treated
    as UTC only as a compatibility fallback; source adapters remain responsible
    for attaching the correct exchange timezone.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, datetime_time.min, tzinfo=timezone.utc)
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"invalid ISO timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonicalize(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {str(k): _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_canonicalize(v) for v in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        )
    if isinstance(value, datetime):
        return normalize_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    if isinstance(value, bytes):
        return {"__bytes_b64__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        return {"__float__": str(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"__repr__": repr(value), "__type__": type(value).__name__}


def canonical_json(payload: Any) -> str:
    """Deterministic JSON representation used for payload identity."""
    return json.dumps(
        _canonicalize(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def sanitize_url(url: str | None) -> str | None:
    """Strip URL credentials and redact query-string secrets."""
    if not url:
        return None
    raw = str(url).strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        # Invalid ports must not turn evidence capture into an application error.
        return None
    if port is not None:
        host = f"{host}:{port}"
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        normalized = key.strip().lower().replace("-", "_")
        secret = (
            normalized in _SENSITIVE_QUERY_KEYS
            or normalized.endswith(("_token", "_secret", "_password", "_key"))
        )
        query.append((key, "[REDACTED]" if secret else value))
    return urlunsplit((parts.scheme, host, parts.path, urlencode(query), ""))


class EvidenceStore:
    """SQLite WAL store with connection-per-operation thread safety."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_points: int = 250_000,
        retention_days: int = 400,
        maintenance_every: int = 500,
    ):
        self.path = Path(path)
        self.max_points = max(1_000, int(max_points))
        self.retention_days = max(30, int(retention_days))
        self.maintenance_every = max(50, int(maintenance_every))
        self._maintenance_lock = threading.Lock()
        self._write_count = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_registry(
                    source_id TEXT PRIMARY KEY,
                    tier INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    refresh_seconds INTEGER,
                    rate_limit TEXT,
                    license TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    cost TEXT NOT NULL,
                    known_risks TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS raw_payload(
                    raw_hash TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    source_url TEXT,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES source_registry(source_id)
                );

                CREATE TABLE IF NOT EXISTS evidence_point(
                    evidence_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    market TEXT NOT NULL,
                    field TEXT NOT NULL,
                    value_json TEXT,
                    value_num REAL,
                    unit TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_tier INTEGER NOT NULL,
                    source_url TEXT,
                    event_time TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    trading_day TEXT NOT NULL,
                    freshness TEXT NOT NULL,
                    fallback_level INTEGER NOT NULL,
                    quality_status TEXT NOT NULL,
                    raw_payload_hash TEXT,
                    route_version TEXT NOT NULL,
                    derived INTEGER NOT NULL DEFAULT 0,
                    observation_role TEXT NOT NULL DEFAULT 'selected',
                    event_time_inferred INTEGER NOT NULL DEFAULT 0,
                    quality_reasons_json TEXT NOT NULL DEFAULT '[]',
                    contract_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES source_registry(source_id),
                    FOREIGN KEY(raw_payload_hash) REFERENCES raw_payload(raw_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_evidence_pit
                    ON evidence_point(symbol, market, field, available_at, fetched_at);
                CREATE INDEX IF NOT EXISTS idx_evidence_raw_hash
                    ON evidence_point(raw_payload_hash);

                CREATE TABLE IF NOT EXISTS evidence_lineage(
                    child_id TEXT NOT NULL,
                    parent_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    PRIMARY KEY(child_id, parent_id, role),
                    FOREIGN KEY(child_id) REFERENCES evidence_point(evidence_id),
                    FOREIGN KEY(parent_id) REFERENCES evidence_point(evidence_id)
                );
                """
            )
            # 旧客户数据库的在线迁移；SQLite 不支持 ADD COLUMN IF NOT EXISTS。
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(evidence_point)").fetchall()
            }
            if "observation_role" not in columns:
                conn.execute(
                    "ALTER TABLE evidence_point ADD COLUMN observation_role "
                    "TEXT NOT NULL DEFAULT 'selected'"
                )
            if "event_time_inferred" not in columns:
                conn.execute(
                    "ALTER TABLE evidence_point ADD COLUMN event_time_inferred "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "quality_reasons_json" not in columns:
                conn.execute(
                    "ALTER TABLE evidence_point ADD COLUMN quality_reasons_json "
                    "TEXT NOT NULL DEFAULT '[]'"
                )
            if "contract_json" not in columns:
                conn.execute(
                    "ALTER TABLE evidence_point ADD COLUMN contract_json "
                    "TEXT NOT NULL DEFAULT '{}'"
                )
            # 历史 cross-check 既往与主选点共表且无角色字段；
            # 从已有 lineage 可无损识别，避免 PIT 回放把备源当成当时决策值。
            conn.execute(
                """
                UPDATE evidence_point SET observation_role='cross_check'
                WHERE evidence_id IN (
                    SELECT parent_id FROM evidence_lineage WHERE role='cross_check'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evidence_selected_pit "
                "ON evidence_point(symbol,market,field,observation_role,available_at,fetched_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_evidence_created_at "
                "ON evidence_point(created_at)"
            )

    def register_source(
        self,
        source_id: str,
        *,
        tier: int,
        provider: str,
        purpose: str,
        fields: list[str] | tuple[str, ...],
        refresh_seconds: int | None = None,
        rate_limit: str = "unknown",
        license: str = "unknown",
        owner: str = "unassigned",
        cost: str = "free",
        known_risks: str = "public endpoint; no SLA",
        enabled: bool = True,
        updated_at: datetime | str | None = None,
    ) -> None:
        source_id = str(source_id).strip()
        if not source_id:
            raise ValueError("source_id is required")
        updated = normalize_timestamp(updated_at or datetime.now(timezone.utc))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO source_registry(
                    source_id,tier,provider,purpose,fields_json,refresh_seconds,
                    rate_limit,license,owner,cost,known_risks,enabled,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_id) DO UPDATE SET
                    tier=excluded.tier,
                    provider=excluded.provider,
                    purpose=excluded.purpose,
                    fields_json=excluded.fields_json,
                    refresh_seconds=excluded.refresh_seconds,
                    rate_limit=excluded.rate_limit,
                    license=excluded.license,
                    owner=excluded.owner,
                    cost=excluded.cost,
                    known_risks=excluded.known_risks,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    source_id, int(tier), str(provider), str(purpose),
                    canonical_json(sorted(set(str(f) for f in fields))),
                    int(refresh_seconds) if refresh_seconds is not None else None,
                    str(rate_limit), str(license), str(owner), str(cost),
                    str(known_risks), int(bool(enabled)), updated,
                ),
            )

    def get_source(self, source_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_registry WHERE source_id=?", (source_id,)
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["fields"] = json.loads(out.pop("fields_json"))
        out["enabled"] = bool(out["enabled"])
        return out

    def list_sources(self) -> list[dict]:
        with self._connect() as conn:
            ids = [
                row["source_id"]
                for row in conn.execute(
                    "SELECT source_id FROM source_registry ORDER BY source_id"
                ).fetchall()
            ]
        return [source for source_id in ids if (source := self.get_source(source_id))]

    def put_raw_payload(
        self,
        source_id: str,
        payload: Any,
        *,
        fetched_at: datetime | str | None = None,
        source_url: str | None = None,
        status: str = "ok",
    ) -> str:
        encoded = canonical_json(payload)
        # 原始载荷的身份同时包含“来源 + 内容”。不同供应商即使恰好
        # 返回同一 JSON，也必须保留两份独立来源证据；否则全局内容哈希
        # 去重会让第二个源的 evidence 指向第一个源的 raw row。
        identity = f"{source_id}\n{encoded}".encode("utf-8")
        raw_hash = hashlib.sha256(identity).hexdigest()
        fetched = normalize_timestamp(fetched_at or datetime.now(timezone.utc))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO raw_payload(
                    raw_hash,source_id,fetched_at,source_url,payload_json,status
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    raw_hash, source_id, fetched, sanitize_url(source_url),
                    encoded, str(status),
                ),
            )
        return raw_hash

    def get_raw_payload(self, raw_hash: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM raw_payload WHERE raw_hash=?", (raw_hash,)
            ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["payload"] = json.loads(out.pop("payload_json"))
        return out

    def save_point(
        self,
        point: Mapping[str, Any] | Any,
        *,
        parents: list[tuple[str, str]] | None = None,
    ) -> str:
        record = asdict(point) if is_dataclass(point) else dict(point)
        value = record.get("value")
        value_json = canonical_json(value) if value is not None else None
        value_num = (
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value))
            else None
        )
        event_time = normalize_timestamp(record.get("event_time"))
        fetched_at = normalize_timestamp(record.get("fetched_at"))
        available_at = normalize_timestamp(record.get("available_at") or fetched_at)
        if not event_time or not fetched_at or not available_at:
            raise ValueError("event_time, available_at and fetched_at are required")
        observation_role = str(record.get("observation_role") or "selected")
        if observation_role not in {"selected", "cross_check"}:
            raise ValueError("observation_role must be selected or cross_check")
        quality_reasons = tuple(
            str(reason) for reason in (record.get("quality_reasons") or ()) if str(reason)
        )
        contract_meta = {
            "field_meaning": str(record.get("field_meaning") or record["field"]),
            "value_semantics": str(
                record.get("value_semantics") or "point_in_time"
            ),
            "batch_id": record.get("batch_id"),
            "batch_status": str(record.get("batch_status") or "unknown"),
            "batch_expected_count": record.get("batch_expected_count"),
            "batch_present_count": record.get("batch_present_count"),
            "degraded_reason": record.get("degraded_reason"),
            "contract_version": str(
                record.get("contract_version") or "field-contract-v2"
            ),
        }
        identity = {
            "schema_version": str(record.get("schema_version") or "market-data-v1"),
            "symbol": str(record["symbol"]), "market": str(record["market"]),
            "field": str(record["field"]), "value": value, "unit": str(record["unit"]),
            "source_id": str(record["source_id"]), "source_tier": int(record["source_tier"]),
            "event_time": event_time, "available_at": available_at, "fetched_at": fetched_at,
            "trading_day": str(record["trading_day"]), "freshness": str(record["freshness"]),
            "fallback_level": int(record["fallback_level"]),
            "quality_status": str(record["quality_status"]),
            "raw_payload_hash": record.get("raw_payload_hash"),
            "route_version": str(record.get("route_version") or "v1"),
            "derived": bool(record.get("derived", False)),
            "observation_role": observation_role,
            "event_time_inferred": bool(record.get("event_time_inferred", False)),
            "quality_reasons": quality_reasons,
            "contract": contract_meta,
        }
        # 同一快照的同一字段重复读取必须幂等，不再每次轮询生成新 UUID。
        evidence_id = str(
            record.get("evidence_id")
            or uuid.uuid5(uuid.NAMESPACE_URL, canonical_hash(identity))
        )
        created_at = normalize_timestamp(datetime.now(timezone.utc))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO evidence_point(
                    evidence_id,schema_version,symbol,market,field,value_json,value_num,
                    unit,source_id,source_tier,source_url,event_time,available_at,
                    fetched_at,trading_day,freshness,fallback_level,quality_status,
                    raw_payload_hash,route_version,derived,observation_role,
                    event_time_inferred,quality_reasons_json,contract_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    evidence_id, str(record.get("schema_version") or "market-data-v1"),
                    str(record["symbol"]), str(record["market"]), str(record["field"]),
                    value_json, value_num, str(record["unit"]), str(record["source_id"]),
                    int(record["source_tier"]), sanitize_url(record.get("source_url")),
                    event_time, available_at, fetched_at, str(record["trading_day"]),
                    str(record["freshness"]), int(record["fallback_level"]),
                    str(record["quality_status"]), record.get("raw_payload_hash"),
                    str(record.get("route_version") or "v1"),
                    int(bool(record.get("derived", False))), observation_role,
                    int(bool(record.get("event_time_inferred", False))),
                    canonical_json(quality_reasons),
                    canonical_json(contract_meta),
                    created_at,
                ),
            )
            for parent_id, role in parents or []:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO evidence_lineage(child_id,parent_id,role)
                    VALUES(?,?,?)
                    """,
                    (evidence_id, str(parent_id), str(role)),
                )
        self._maybe_maintain()
        return evidence_id

    def _maybe_maintain(self) -> None:
        with self._maintenance_lock:
            self._write_count += 1
            due = self._write_count % self.maintenance_every == 0
        if due:
            self.prune()

    def prune(self, *, batch_limit: int = 10_000) -> dict[str, int]:
        """按时间和容量删除最旧证据，防止本地 SQLite 无限增长。"""
        limit = max(100, min(int(batch_limit), 50_000))
        cutoff = normalize_timestamp(
            datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        )
        with self._connect() as conn:
            old_ids = [
                row[0] for row in conn.execute(
                    "SELECT evidence_id FROM evidence_point WHERE created_at<? "
                    "ORDER BY created_at ASC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
            ]
            remaining = max(0, limit - len(old_ids))
            total = conn.execute("SELECT COUNT(*) FROM evidence_point").fetchone()[0]
            overflow = max(0, total - self.max_points - len(old_ids))
            if remaining and overflow:
                existing = set(old_ids)
                for row in conn.execute(
                    "SELECT evidence_id FROM evidence_point ORDER BY created_at ASC "
                    "LIMIT ? OFFSET ?",
                    (min(remaining, overflow), len(old_ids)),
                ).fetchall():
                    if row[0] not in existing:
                        old_ids.append(row[0])
                        existing.add(row[0])
            if old_ids:
                placeholders = ",".join("?" for _ in old_ids)
                conn.execute(
                    f"DELETE FROM evidence_lineage WHERE child_id IN ({placeholders}) "
                    f"OR parent_id IN ({placeholders})",
                    (*old_ids, *old_ids),
                )
                conn.execute(
                    f"DELETE FROM evidence_point WHERE evidence_id IN ({placeholders})",
                    old_ids,
                )
            raw_deleted = conn.execute(
                "DELETE FROM raw_payload WHERE raw_hash NOT IN ("
                "SELECT DISTINCT raw_payload_hash FROM evidence_point "
                "WHERE raw_payload_hash IS NOT NULL)"
            ).rowcount
        return {"evidence_points": len(old_ids), "raw_payloads": max(0, raw_deleted)}

    @staticmethod
    def _row_to_point(row: sqlite3.Row) -> dict:
        out = dict(row)
        raw_value = out.pop("value_json")
        out["value"] = json.loads(raw_value) if raw_value is not None else None
        out.pop("value_num", None)
        out.pop("created_at", None)
        out["derived"] = bool(out["derived"])
        out["event_time_inferred"] = bool(out.get("event_time_inferred", 0))
        raw_reasons = out.pop("quality_reasons_json", "[]")
        try:
            out["quality_reasons"] = json.loads(raw_reasons) if raw_reasons else []
        except (TypeError, ValueError, json.JSONDecodeError):
            out["quality_reasons"] = []
        raw_contract = out.pop("contract_json", "{}")
        try:
            contract = json.loads(raw_contract) if raw_contract else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            contract = {}
        if isinstance(contract, dict):
            out.update(contract)
        return out

    def get_point(self, evidence_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM evidence_point WHERE evidence_id=?", (evidence_id,)
            ).fetchone()
        return self._row_to_point(row) if row is not None else None

    def list_points(
        self,
        *,
        symbol: str | None = None,
        field: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return newest evidence points with bounded optional filters."""
        try:
            bounded = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            bounded = 100
        where: list[str] = []
        values: list[Any] = []
        if symbol:
            where.append("symbol=?")
            values.append(str(symbol))
        if field:
            where.append("field=?")
            values.append(str(field))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        values.append(bounded)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM evidence_point {clause}
                ORDER BY fetched_at DESC, created_at DESC, evidence_id DESC
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._row_to_point(row) for row in rows]

    def stats(self) -> dict:
        """Small operational summary; never exposes raw payload content."""
        with self._connect() as conn:
            counts = {
                "sources": conn.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0],
                "raw_payloads": conn.execute("SELECT COUNT(*) FROM raw_payload").fetchone()[0],
                "evidence_points": conn.execute("SELECT COUNT(*) FROM evidence_point").fetchone()[0],
                "lineage_edges": conn.execute("SELECT COUNT(*) FROM evidence_lineage").fetchone()[0],
            }
            quality = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT quality_status,COUNT(*) FROM evidence_point GROUP BY quality_status"
                ).fetchall()
            }
            latest = conn.execute(
                "SELECT MAX(fetched_at) FROM evidence_point"
            ).fetchone()[0]
        return {**counts, "by_quality": quality, "latest_fetched_at": latest}

    def query_as_of(
        self,
        symbol: str,
        field: str,
        as_of: datetime | str,
        *,
        market: str = "CN-A",
    ) -> dict | None:
        cutoff = normalize_timestamp(as_of)
        if cutoff is None:
            raise ValueError("as_of is required")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM evidence_point
                WHERE symbol=? AND market=? AND field=?
                  AND observation_role='selected'
                  AND available_at<=? AND fetched_at<=?
                ORDER BY available_at DESC, fetched_at DESC,
                         fallback_level ASC, created_at DESC, evidence_id DESC
                LIMIT 1
                """,
                (symbol, market, field, cutoff, cutoff),
            ).fetchone()
        return self._row_to_point(row) if row is not None else None

    def get_lineage(self, evidence_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT parent_id, role FROM evidence_lineage
                WHERE child_id=? ORDER BY role,parent_id
                """,
                (evidence_id,),
            ).fetchall()
        return [dict(row) for row in rows]
