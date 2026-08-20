"""跨进程重型维护任务队列。

API 进程只负责追加任务和读取状态；独立 worker 通过租约领取任务。任务进度、
游标和最终结果全部落在客户 ``data_dir`` 下，进程重启后不会丢失。
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import socket
import sqlite3
import time
import uuid
from typing import Any, Iterable

from .data.base import now_cn


ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "partial", "failed", "cancelled")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value)) if value not in (None, "") else default
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _stamp() -> str:
    return now_cn().isoformat(timespec="seconds")


class MaintenanceQueue:
    """SQLite/WAL 持久任务队列；每次操作使用独立短连接。"""

    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "maintenance_jobs.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _init_schema(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS maintenance_jobs(
                  job_id TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  status TEXT NOT NULL,
                  origin TEXT NOT NULL,
                  idempotency_key TEXT,
                  payload_json TEXT NOT NULL,
                  progress_json TEXT NOT NULL,
                  result_json TEXT,
                  created_at TEXT NOT NULL,
                  started_at TEXT,
                  heartbeat_at TEXT,
                  finished_at TEXT,
                  lease_owner TEXT,
                  lease_expires_epoch REAL,
                  attempts INTEGER NOT NULL DEFAULT 0,
                  error_code TEXT,
                  error_message TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_maintenance_idempotency
                  ON maintenance_jobs(kind,idempotency_key)
                  WHERE idempotency_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_maintenance_claim
                  ON maintenance_jobs(status,created_at);
                CREATE INDEX IF NOT EXISTS idx_maintenance_kind_created
                  ON maintenance_jobs(kind,created_at DESC);
                CREATE TABLE IF NOT EXISTS maintenance_workers(
                  worker_id TEXT PRIMARY KEY,
                  pid INTEGER NOT NULL,
                  hostname TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL,
                  last_seen_epoch REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS maintenance_worker_lease(
                  singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                  owner TEXT NOT NULL,
                  expires_epoch REAL NOT NULL,
                  updated_at TEXT NOT NULL
                );
                """
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["payload"] = _decode(result.pop("payload_json", None), {})
        result["progress"] = _decode(result.pop("progress_json", None), {})
        result["result"] = _decode(result.pop("result_json", None), None)
        return result

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        origin: str = "manual",
        idempotency_key: str | None = None,
        single_active: bool = True,
    ) -> tuple[dict[str, Any], bool]:
        """追加任务；同类任务运行中或幂等键已存在时返回原任务。"""
        clean_kind = str(kind or "").strip()
        if not clean_kind:
            raise ValueError("maintenance job kind is required")
        clean_key = str(idempotency_key or "").strip() or None
        now = _stamp()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if clean_key is not None:
                existing = db.execute(
                    "SELECT * FROM maintenance_jobs WHERE kind=? AND idempotency_key=?",
                    (clean_kind, clean_key),
                ).fetchone()
                if existing is not None:
                    db.commit()
                    return self._row(existing) or {}, False
            if single_active:
                existing = db.execute(
                    "SELECT * FROM maintenance_jobs WHERE kind=? AND status IN ('queued','running') "
                    "ORDER BY created_at LIMIT 1",
                    (clean_kind,),
                ).fetchone()
                if existing is not None:
                    db.commit()
                    return self._row(existing) or {}, False
            job_id = f"mj_{uuid.uuid4().hex}"
            db.execute(
                "INSERT INTO maintenance_jobs("
                "job_id,kind,status,origin,idempotency_key,payload_json,progress_json,created_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    clean_kind,
                    "queued",
                    str(origin or "manual")[:32],
                    clean_key,
                    _json(payload or {}),
                    _json({}),
                    now,
                ),
            )
            row = db.execute(
                "SELECT * FROM maintenance_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            db.commit()
        return self._row(row) or {}, True

    def claim_next(
        self,
        owner: str,
        *,
        kinds: Iterable[str],
        lease_seconds: float = 120.0,
    ) -> dict[str, Any] | None:
        wanted = tuple(dict.fromkeys(str(item) for item in kinds if str(item)))
        if not wanted:
            return None
        now_epoch = time.time()
        now = _stamp()
        placeholders = ",".join("?" for _ in wanted)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE maintenance_jobs SET status='queued',lease_owner=NULL,"
                "lease_expires_epoch=NULL,error_code='worker_lease_expired',"
                "error_message='后台 Worker 租约过期，任务等待续跑' "
                "WHERE status='running' AND lease_expires_epoch IS NOT NULL "
                "AND lease_expires_epoch<?",
                (now_epoch,),
            )
            row = db.execute(
                f"SELECT * FROM maintenance_jobs WHERE status='queued' "
                f"AND kind IN ({placeholders}) ORDER BY created_at LIMIT 1",
                wanted,
            ).fetchone()
            if row is None:
                db.commit()
                return None
            job_id = str(row["job_id"])
            db.execute(
                "UPDATE maintenance_jobs SET status='running',started_at=COALESCE(started_at,?),"
                "heartbeat_at=?,lease_owner=?,lease_expires_epoch=?,attempts=attempts+1,"
                "error_code=NULL,error_message=NULL WHERE job_id=?",
                (now, now, owner, now_epoch + max(30.0, float(lease_seconds)), job_id),
            )
            claimed = db.execute(
                "SELECT * FROM maintenance_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            db.commit()
        return self._row(claimed)

    def heartbeat(
        self,
        job_id: str,
        owner: str,
        *,
        lease_seconds: float = 120.0,
        progress: dict[str, Any] | None = None,
    ) -> bool:
        with self._connect() as db:
            current = db.execute(
                "SELECT progress_json FROM maintenance_jobs "
                "WHERE job_id=? AND status='running' AND lease_owner=?",
                (job_id, owner),
            ).fetchone()
            if current is None:
                return False
            merged = _decode(current["progress_json"], {})
            if progress:
                merged.update(progress)
            changed = db.execute(
                "UPDATE maintenance_jobs SET heartbeat_at=?,lease_expires_epoch=?,progress_json=? "
                "WHERE job_id=? AND status='running' AND lease_owner=?",
                (
                    _stamp(),
                    time.time() + max(30.0, float(lease_seconds)),
                    _json(merged),
                    job_id,
                    owner,
                ),
            ).rowcount
            return bool(changed)

    def finish(
        self,
        job_id: str,
        owner: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        final_status = str(status)
        if final_status not in TERMINAL_STATUSES:
            raise ValueError(f"invalid terminal status: {final_status}")
        with self._connect() as db:
            changed = db.execute(
                "UPDATE maintenance_jobs SET status=?,result_json=?,finished_at=?,heartbeat_at=?,"
                "lease_owner=NULL,lease_expires_epoch=NULL,error_code=?,error_message=? "
                "WHERE job_id=? AND status='running' AND lease_owner=?",
                (
                    final_status,
                    _json(result) if result is not None else None,
                    _stamp(),
                    _stamp(),
                    str(error_code or "")[:80] or None,
                    str(error_message or "")[:300] or None,
                    job_id,
                    owner,
                ),
            ).rowcount
            return bool(changed)

    def latest(self, kind: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM maintenance_jobs WHERE kind=? ORDER BY created_at DESC LIMIT 1",
                (str(kind),),
            ).fetchone()
        return self._row(row)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM maintenance_jobs WHERE job_id=?", (str(job_id),)
            ).fetchone()
        return self._row(row)

    def register_worker(self, worker_id: str, *, started_at: str | None = None) -> None:
        now = _stamp()
        with self._connect() as db:
            db.execute(
                "INSERT INTO maintenance_workers(worker_id,pid,hostname,started_at,last_seen_at,last_seen_epoch) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET "
                "last_seen_at=excluded.last_seen_at,last_seen_epoch=excluded.last_seen_epoch",
                (
                    str(worker_id),
                    os.getpid(),
                    socket.gethostname(),
                    started_at or now,
                    now,
                    time.time(),
                ),
            )

    def acquire_worker_lease(self, worker_id: str, *, lease_seconds: float = 30.0) -> bool:
        now_epoch = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT owner,expires_epoch FROM maintenance_worker_lease WHERE singleton_id=1"
            ).fetchone()
            if row is not None and str(row["owner"]) != str(worker_id) and float(row["expires_epoch"]) >= now_epoch:
                db.commit()
                return False
            db.execute(
                "INSERT INTO maintenance_worker_lease(singleton_id,owner,expires_epoch,updated_at) "
                "VALUES(1,?,?,?) ON CONFLICT(singleton_id) DO UPDATE SET "
                "owner=excluded.owner,expires_epoch=excluded.expires_epoch,updated_at=excluded.updated_at",
                (
                    str(worker_id),
                    now_epoch + max(15.0, float(lease_seconds)),
                    _stamp(),
                ),
            )
            db.commit()
        return True

    def release_worker_lease(self, worker_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM maintenance_worker_lease WHERE singleton_id=1 AND owner=?",
                (str(worker_id),),
            )

    def worker_status(self, *, stale_after_seconds: float = 30.0) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM maintenance_workers ORDER BY last_seen_epoch DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return {"status": "missing", "connected": False, "last_seen_at": None}
        value = dict(row)
        age = max(0.0, time.time() - float(value.get("last_seen_epoch") or 0))
        return {
            "status": "ready" if age <= stale_after_seconds else "stale",
            "connected": age <= stale_after_seconds,
            "worker_id": value.get("worker_id"),
            "pid": value.get("pid"),
            "hostname": value.get("hostname"),
            "started_at": value.get("started_at"),
            "last_seen_at": value.get("last_seen_at"),
            "age_seconds": round(age, 1),
        }

    def public_state(self, kind: str) -> dict[str, Any]:
        job = self.latest(kind)
        if not job:
            return {}
        status_map = {
            "queued": "queued",
            "running": "running",
            "completed": "ok",
            "partial": "warn",
            "failed": "error",
            "cancelled": "interrupted",
        }
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
        return {
            "last_run": job.get("started_at") or job.get("created_at"),
            "last_status": status_map.get(str(job.get("status")), str(job.get("status"))),
            "last_ok": job.get("finished_at") if job.get("status") == "completed" else None,
            "last_finished": job.get("finished_at"),
            "last_error": job.get("error_message"),
            "last_error_code": job.get("error_code"),
            "last_result": {**progress, **result, "job_id": job.get("job_id")},
        }


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
