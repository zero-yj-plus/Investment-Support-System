"""Durable, idempotent delivery intent ledger.

The outbox is deliberately separate from report content.  It stores only
delivery metadata and a deterministic identifier, so a restart can distinguish
"never sent" from "may have been sent" without copying private report bodies.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from .delivery import _metadata_text, _root_path, _safe_error, utc_timestamp


OUTBOX_SCHEMA_VERSION = "delivery-outbox.v2"
TERMINAL_SUCCESS = frozenset({"accepted", "delivered", "acknowledged"})
MANUAL_REVIEW = frozenset({"sending", "unknown", "partial"})
RETRYABLE = frozenset({"queued", "failed_retryable"})


def deterministic_delivery_id(
    content_id: str,
    channel: str,
    *,
    version: int | str | None = None,
    target: str = "aggregate",
) -> str:
    key = "|".join((str(content_id), str(version or 1), str(channel), str(target)))
    return f"dlv_{uuid.uuid5(uuid.NAMESPACE_URL, 'hermes-delivery:' + key).hex}"


class DeliveryOutbox:
    def __init__(self, store_or_path):
        root = _root_path(store_or_path)
        self.path = Path(root) / "delivery_outbox" / "outbox.v1.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    content_id TEXT NOT NULL,
                    content_hash TEXT,
                    channel TEXT NOT NULL,
                    target TEXT NOT NULL,
                    version TEXT,
                    trigger TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    message_id TEXT,
                    error TEXT,
                    error_code TEXT,
                    target_results_json TEXT,
                    prepared_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    accepted_at TEXT,
                    resolved_at TEXT,
                    resolution_note TEXT
                )
            """)
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(deliveries)").fetchall()
            }
            if "resolved_at" not in columns:
                conn.execute("ALTER TABLE deliveries ADD COLUMN resolved_at TEXT")
            if "resolution_note" not in columns:
                conn.execute("ALTER TABLE deliveries ADD COLUMN resolution_note TEXT")
            if "content_hash" not in columns:
                conn.execute("ALTER TABLE deliveries ADD COLUMN content_hash TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS deliveries_status_idx ON deliveries(status, updated_at)"
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        value = dict(row)
        try:
            value["target_results"] = json.loads(value.pop("target_results_json") or "[]")
        except (TypeError, ValueError):
            value["target_results"] = []
        return value

    def prepare(
        self,
        *,
        content_id: str,
        content_hash: str | None = None,
        channel: str,
        version: int | str | None = None,
        target: str = "aggregate",
        trigger: str = "automatic",
    ) -> dict:
        content_id = _metadata_text(content_id, 240)
        channel = _metadata_text(channel, 40)
        target = _metadata_text(target, 120) or "aggregate"
        content_hash = _metadata_text(content_hash, 64)
        if not content_id or not channel:
            raise ValueError("delivery intent requires content_id/channel")
        delivery_id = deterministic_delivery_id(
            content_id, channel, version=version, target=target,
        )
        now = utc_timestamp()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT OR IGNORE INTO deliveries
                   (delivery_id,schema_version,content_id,content_hash,channel,target,version,trigger,status,
                    attempts,prepared_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,0,?,?)""",
                (
                    delivery_id, OUTBOX_SCHEMA_VERSION, content_id, content_hash, channel, target,
                    str(version) if version not in (None, "") else None,
                    _metadata_text(trigger, 40) or "automatic", "queued", now, now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,),
            ).fetchone()
            if row is not None and content_hash:
                bound_hash = str(row["content_hash"] or "")
                if bound_hash and bound_hash != content_hash:
                    conn.execute("ROLLBACK")
                    raise ValueError("delivery identity is already bound to different content_hash")
                if not bound_hash:
                    if int(row["attempts"] or 0) > 0:
                        conn.execute("ROLLBACK")
                        raise ValueError("legacy delivery attempt has no verifiable content_hash")
                    conn.execute(
                        "UPDATE deliveries SET content_hash=?,schema_version=?,updated_at=? WHERE delivery_id=?",
                        (content_hash, OUTBOX_SCHEMA_VERSION, now, delivery_id),
                    )
                    row = conn.execute(
                        "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,),
                    ).fetchone()
            conn.execute("COMMIT")
        return self._row(row) or {}

    def claim(self, delivery_id: str) -> tuple[str, dict]:
        """Claim a safe-to-send intent.

        ``sending`` is deliberately not auto-retried after a crash: the external
        channel may already have accepted it.  Such rows become manual review,
        preventing duplicate customer messages.
        """
        now = utc_timestamp()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(delivery_id)
            status = str(row["status"])
            if status in TERMINAL_SUCCESS:
                outcome = "already_accepted"
            elif status in MANUAL_REVIEW:
                outcome = "manual_review"
            elif status == "failed_terminal":
                outcome = "failed_terminal"
            elif status in RETRYABLE:
                conn.execute(
                    """UPDATE deliveries SET status='sending', attempts=attempts+1,
                       updated_at=?, error=NULL, error_code=NULL WHERE delivery_id=?""",
                    (now, delivery_id),
                )
                outcome = "claimed"
            else:
                outcome = "manual_review"
            row = conn.execute(
                "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,),
            ).fetchone()
            conn.execute("COMMIT")
        return outcome, self._row(row) or {}

    def finish(
        self,
        delivery_id: str,
        *,
        expected_attempt: int,
        status: str,
        message_id: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
        target_results: list[dict] | None = None,
    ) -> dict:
        allowed = TERMINAL_SUCCESS | MANUAL_REVIEW | {"failed_retryable", "failed_terminal"}
        status = str(status or "").strip().lower()
        if status not in allowed:
            raise ValueError(f"unsupported outbox status: {status}")
        now = utc_timestamp()
        accepted_at = now if status in TERMINAL_SUCCESS else None
        encoded_targets = json.dumps(target_results or [], ensure_ascii=False, separators=(",", ":"))
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """UPDATE deliveries SET status=?,message_id=?,error=?,error_code=?,
                   target_results_json=?,updated_at=?,accepted_at=COALESCE(accepted_at,?)
                   WHERE delivery_id=? AND status='sending' AND attempts=?""",
                (
                    status, _metadata_text(message_id, 240), _safe_error(error),
                    _metadata_text(error_code, 80), encoded_targets, now, accepted_at,
                    delivery_id, int(expected_attempt),
                ),
            )
            row = conn.execute(
                "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,),
            ).fetchone()
            applied = bool(cursor.rowcount == 1)
            conn.execute("COMMIT")
        if row is None:
            raise KeyError(delivery_id)
        result = self._row(row) or {}
        result["finish_applied"] = applied
        if not result["finish_applied"]:
            result["finish_rejected_reason"] = "stale_or_resolved_attempt"
        return result

    def authorize_retry(
        self,
        delivery_id: str,
        *,
        allow_ambiguous: bool = False,
        review_note: str | None = None,
    ) -> dict:
        """显式人工重试授权；sending/unknown/partial 必须确认可能重复。"""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(delivery_id)
            status = str(row["status"])
            ambiguous = status in {"sending", "unknown", "partial"}
            note = _metadata_text(review_note, 500) or ""
            if ambiguous and allow_ambiguous and len(note) < 3:
                conn.execute("ROLLBACK")
                raise ValueError("歧义交付重试必须填写至少 3 个字符的人工核验说明")
            allowed = status == "failed_terminal" or (allow_ambiguous and ambiguous)
            if not allowed:
                conn.execute("ROLLBACK")
                raise ValueError("当前交付状态不允许重试，或尚未确认重复风险")
            now = utc_timestamp()
            conn.execute(
                """UPDATE deliveries SET status='failed_retryable',updated_at=?,resolved_at=?,
                   resolution_note=?,error_code='manual_retry_authorized' WHERE delivery_id=?""",
                (now, now, note, delivery_id),
            )
            row = conn.execute(
                "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,),
            ).fetchone()
            conn.execute("COMMIT")
        return self._row(row) or {}

    def acknowledge(self, delivery_id: str, *, review_note: str) -> dict:
        """人工核验外部平台确已接收；只允许关闭含歧义的事务。"""
        note = _metadata_text(review_note, 500) or ""
        if len(note) < 3:
            raise ValueError("人工确认必须填写至少 3 个字符的核验说明")
        now = utc_timestamp()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(delivery_id)
            if str(row["status"]) not in MANUAL_REVIEW:
                conn.execute("ROLLBACK")
                raise ValueError("只有 sending/unknown/partial 事务可人工确认")
            conn.execute(
                """UPDATE deliveries SET status='acknowledged',updated_at=?,accepted_at=?,
                   resolved_at=?,resolution_note=?,error_code='manual_external_confirmation'
                   WHERE delivery_id=?""",
                (now, now, now, note, delivery_id),
            )
            row = conn.execute(
                "SELECT * FROM deliveries WHERE delivery_id=?", (delivery_id,),
            ).fetchone()
            conn.execute("COMMIT")
        return self._row(row) or {}

    def rows(self, *, limit: int = 200, status: str | None = None) -> list[dict]:
        bounded = max(1, min(int(limit), 1000))
        with closing(self._connect()) as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM deliveries WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                    (status, bounded),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM deliveries ORDER BY updated_at DESC LIMIT ?", (bounded,),
                ).fetchall()
        return [self._row(row) or {} for row in rows]

    def blockers(self) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT * FROM deliveries
                   WHERE status IN ('sending','unknown','partial','failed_terminal')
                   ORDER BY updated_at DESC"""
            ).fetchall()
        return [self._row(row) or {} for row in rows]
