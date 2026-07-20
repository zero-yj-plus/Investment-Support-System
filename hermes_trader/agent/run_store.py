"""Agent 运行审计账本。

只保存请求/输出的哈希、字节数和状态，不保存 prompt、模型原文、
工具参数或工具结果。这个账本用来回答「什么时候调了模型、当时工具
权限是否通过门禁、产出是否完整」，而不是另造一份会泄露客户数据的会话库。
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def stable_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class AgentRunStore:
    """SQLite WAL 运行账本；每个客户的 ``data_dir`` 各自独立。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    client_request_id TEXT NOT NULL,
                    owner_pid INTEGER NOT NULL,
                    purpose TEXT NOT NULL,
                    model TEXT NOT NULL,
                    gateway TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    input_bytes INTEGER NOT NULL,
                    capability_status TEXT NOT NULL DEFAULT 'pending',
                    capability_hash TEXT,
                    capability_reason TEXT,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    output_hash TEXT,
                    output_bytes INTEGER NOT NULL DEFAULT 0,
                    finish_reason TEXT,
                    error_code TEXT,
                    partial_output INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_started
                    ON agent_runs(started_at DESC);
                CREATE TABLE IF NOT EXISTS agent_run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    tool_name TEXT,
                    tool_call_id TEXT,
                    status TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_run_events_run
                    ON agent_run_events(run_id, id);
                """
            )
            run_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(agent_runs)").fetchall()
            }
            if "client_request_id" not in run_columns:
                conn.execute("ALTER TABLE agent_runs ADD COLUMN client_request_id TEXT")
                conn.execute(
                    "UPDATE agent_runs SET client_request_id='legacy_' || run_id "
                    "WHERE client_request_id IS NULL"
                )
            if "owner_pid" not in run_columns:
                conn.execute("ALTER TABLE agent_runs ADD COLUMN owner_pid INTEGER NOT NULL DEFAULT 0")
            event_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(agent_run_events)").fetchall()
            }
            if "attempt" not in event_columns:
                conn.execute(
                    "ALTER TABLE agent_run_events ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_request "
                "ON agent_runs(client_request_id)"
            )
            # 进程退出时没有完成的 Agent run 不能在重启后继续显示 running。
            # 由于不知道上游是否已经执行过工具，按 interrupted + 不可采信收口。
            for row in conn.execute(
                "SELECT run_id,owner_pid FROM agent_runs WHERE status IN ('queued','running')"
            ).fetchall():
                pid = int(row["owner_pid"] or 0)
                alive = False
                if pid > 0:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except OSError:
                        alive = False
                if not alive:
                    conn.execute(
                        """UPDATE agent_runs
                           SET status='interrupted', error_code='process_restarted',
                               partial_output=CASE WHEN output_bytes>0 THEN 1 ELSE partial_output END,
                               completed_at=? WHERE run_id=?""",
                        (_utc_now(), row["run_id"]),
                    )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def start_run(
        self,
        *,
        purpose: str,
        model: str,
        gateway: str,
        input_hash: str,
        input_bytes: int,
        client_request_id: str | None = None,
    ) -> str:
        run_id = f"ar_{uuid.uuid4().hex}"
        client_request_id = str(client_request_id or f"req_{uuid.uuid4().hex}")[:160]
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO agent_runs(
                       run_id,client_request_id,owner_pid,purpose,model,gateway,input_hash,input_bytes,status,started_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    client_request_id,
                    os.getpid(),
                    str(purpose or "unspecified")[:120],
                    str(model or "")[:160],
                    str(gateway or "")[:240],
                    input_hash,
                    max(0, int(input_bytes)),
                    "running",
                    _utc_now(),
                ),
            )
        return run_id

    def mark_capability(self, run_id: str, snapshot: dict) -> None:
        status = str(snapshot.get("status") or "unknown")[:48]
        reason = str(snapshot.get("reason") or "")[:240]
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE agent_runs
                   SET capability_status=?, capability_hash=?, capability_reason=?
                   WHERE run_id=?""",
                (status, stable_hash(snapshot), reason, run_id),
            )

    def increment_attempt(self, run_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE agent_runs SET attempts=attempts+1 WHERE run_id=? AND status='running'",
                (run_id,),
            )

    def record_tool_event(
        self,
        run_id: str,
        *,
        tool_name: str,
        tool_call_id: str,
        status: str,
        attempt: int = 0,
    ) -> None:
        """只记工具名和生命周期；故意不接受 args/result/preview。"""
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO agent_run_events(
                       run_id,event_type,attempt,tool_name,tool_call_id,status,created_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    run_id,
                    "tool_progress",
                    max(0, int(attempt)),
                    str(tool_name or "unknown")[:120],
                    str(tool_call_id or "")[:160],
                    str(status or "unknown")[:40],
                    _utc_now(),
                ),
            )

    def finish(
        self,
        run_id: str,
        *,
        status: str,
        output_hash: str = "",
        output_bytes: int = 0,
        finish_reason: str = "",
        error_code: str = "",
        partial_output: bool = False,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE agent_runs
                   SET status=?,output_hash=?,output_bytes=?,finish_reason=?,error_code=?,
                       partial_output=?,completed_at=?
                   WHERE run_id=? AND status='running'""",
                (
                    str(status or "failed")[:48],
                    str(output_hash or "")[:80] or None,
                    max(0, int(output_bytes)),
                    str(finish_reason or "")[:80] or None,
                    str(error_code or "")[:120] or None,
                    1 if partial_output else 0,
                    _utc_now(),
                    run_id,
                ),
            )

    def recent(self, limit: int = 30) -> list[dict]:
        limit = max(1, min(int(limit), 100))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT r.*,
                          (SELECT COUNT(*) FROM agent_run_events e WHERE e.run_id=r.run_id)
                              AS tool_events
                   FROM agent_runs r ORDER BY started_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [self._public_row(dict(row)) for row in rows]

    def summary(self) -> dict:
        with self._lock, self._connect() as conn:
            counts = {
                str(row["status"]): int(row["n"])
                for row in conn.execute(
                    "SELECT status,COUNT(*) AS n FROM agent_runs GROUP BY status"
                ).fetchall()
            }
            row = conn.execute(
                """SELECT r.*,
                          (SELECT COUNT(*) FROM agent_run_events e WHERE e.run_id=r.run_id)
                              AS tool_events
                   FROM agent_runs r ORDER BY started_at DESC LIMIT 1"""
            ).fetchone()
        return {
            "counts": counts,
            "last_run": self._public_row(dict(row)) if row else None,
        }

    @staticmethod
    def _public_row(row: dict) -> dict:
        row.pop("owner_pid", None)
        # 内部输入/输出哈希可用于对账，但 UI 无需暴露完整指纹。
        for key in ("input_hash", "output_hash", "capability_hash"):
            if row.get(key):
                row[key] = str(row[key])[:12]
        row["partial_output"] = bool(row.get("partial_output"))
        capability_status = row.get("capability_status")
        tool_events = row.get("tool_events")
        completed_cleanly = (
            row.get("status") == "completed"
            and row.get("finish_reason") == "stop"
            # A missing count is unknown, never equivalent to an audited run.
            and tool_events is not None
        )
        row["safe_to_use"] = bool(
            completed_cleanly
            and (
                (
                    capability_status in {"restricted", "direct_model_api"}
                    and int(tool_events) == 0
                )
                or capability_status == "customer_full_product_mcp"
            )
        )
        return row
