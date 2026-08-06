"""产业链/单公司研究的持久化后台任务。

浏览器只负责创建任务和轮询；生成协程由服务端持有，页面切换或 HTTP
连接断开不会取消模型调用。每个任务单独落盘到客户 data_dir，已完成结果
可在服务重启后继续读取；上次成功结果不会被后续失败覆盖。
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import json
import logging
import os
from pathlib import Path
import re
import threading
import uuid

from .data.base import now_cn


log = logging.getLogger("hermes.research_runs")

KINDS = {"chain", "company"}
ACTIVE_STATUSES = {"queued", "running", "canceling"}
_RUN_ID_RE = re.compile(r"^rr_[0-9a-f]{32}$")
_MAX_CONTENT_CHARS = 120_000
_FAILURE_MARKERS = ("# 产业链研究未完成",)
_COMPANY_SECTION_NUMERALS = "一二三四五六七八九"


class ActiveResearchRunError(RuntimeError):
    def __init__(self, run: dict):
        super().__init__("已有同类研究任务正在后台运行")
        self.run = run


class ActiveResearchRunDeleteError(RuntimeError):
    def __init__(self, run: dict):
        super().__init__("运行中的研究任务不能删除，请先停止任务")
        self.run = run


class ResearchRunManager:
    """后台执行并以原子 JSON 文件保存研究任务状态。"""

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir) / "research_runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._tasks: dict[str, asyncio.Task] = {}
        self._shutting_down = False
        self._reconcile_interrupted()

    @staticmethod
    def _now() -> str:
        return now_cn().isoformat(timespec="milliseconds")

    def _folder(self, kind: str) -> Path:
        if kind not in KINDS:
            raise ValueError("research kind 必须是 chain 或 company")
        folder = self.root / kind
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _path(self, kind: str, run_id: str) -> Path:
        if not _RUN_ID_RE.fullmatch(str(run_id or "")):
            raise ValueError("非法研究任务 ID")
        return self._folder(kind) / f"{run_id}.json"

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _write(path: Path, row: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(
            json.dumps(row, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def _records(self, kind: str) -> list[dict]:
        with self._lock:
            rows = [self._read(path) for path in self._folder(kind).glob("rr_*.json")]
        valid = [row for row in rows if isinstance(row, dict)]
        valid.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return valid

    def _reconcile_interrupted(self) -> None:
        for kind in KINDS:
            for row in self._records(kind):
                if row.get("status") not in ACTIVE_STATUSES:
                    continue
                row.update({
                    "status": "interrupted",
                    "stage": "服务进程已重启，后台生成被中断",
                    "error": "服务重启中断了未完成任务；上次成功结果仍保留，可重新生成。",
                    "error_code": "process_restarted",
                    "finished_at": self._now(),
                    "updated_at": self._now(),
                })
                self._write(self._path(kind, str(row["run_id"])), row)

    def get(self, run_id: str) -> dict | None:
        for kind in KINDS:
            try:
                row = self._read(self._path(kind, run_id))
            except ValueError:
                return None
            if row is not None:
                return row
        return None

    def latest(self, kind: str) -> dict:
        rows = self._records(kind)
        active = next((row for row in rows if row.get("status") in ACTIVE_STATUSES), None)
        completed = next((row for row in rows if row.get("status") == "completed"), None)
        return {
            "active": active,
            "latest": rows[0] if rows else None,
            "latest_completed": completed,
        }

    def _patch(self, run_id: str, **updates) -> dict | None:
        with self._lock:
            current = self.get(run_id)
            if current is None:
                return None
            current.update(updates)
            current["updated_at"] = self._now()
            self._write(self._path(str(current["kind"]), run_id), current)
            return current

    async def start(
        self,
        *,
        kind: str,
        subject: str,
        focus: str = "",
        generator_factory: Callable[[], AsyncIterator[dict]],
    ) -> tuple[dict, bool]:
        if kind not in KINDS:
            raise ValueError("research kind 必须是 chain 或 company")
        subject = str(subject or "").strip()
        if not subject:
            raise ValueError("研究标的不能为空")
        with self._lock:
            active = self.latest(kind).get("active")
            if active:
                if str(active.get("subject") or "") == subject and str(active.get("focus") or "") == focus:
                    return active, True
                raise ActiveResearchRunError(active)
            run_id = f"rr_{uuid.uuid4().hex}"
            created_at = self._now()
            row = {
                "schema_version": "research-run.v1",
                "run_id": run_id,
                "kind": kind,
                "subject": subject,
                "focus": str(focus or "")[:1000],
                "status": "queued",
                "stage": "任务已进入后台队列",
                "content": "",
                "error": None,
                "error_code": None,
                "truncated": False,
                "created_at": created_at,
                "started_at": None,
                "finished_at": None,
                "updated_at": created_at,
            }
            self._write(self._path(kind, run_id), row)
            task = asyncio.create_task(
                self._run(run_id, generator_factory),
                name=f"research-{kind}-{run_id}",
            )
            self._tasks[run_id] = task
            task.add_done_callback(lambda _task, rid=run_id: self._tasks.pop(rid, None))
            return row, False

    async def _run(
        self,
        run_id: str,
        generator_factory: Callable[[], AsyncIterator[dict]],
    ) -> None:
        row = self._patch(
            run_id,
            status="running",
            stage="后台任务正在采集数据",
            started_at=self._now(),
            error=None,
            error_code=None,
        )
        if row is None:
            return
        parts: list[str] = []
        done_seen = False
        truncated = False
        try:
            async for event in generator_factory():
                if isinstance(event, str):
                    event = {"delta": event}
                if not isinstance(event, dict):
                    continue
                if event.get("stage"):
                    self._patch(run_id, stage=str(event["stage"])[:300])
                if event.get("error"):
                    raise RuntimeError(str(event["error"]))
                if event.get("delta"):
                    parts.append(str(event["delta"]))
                    content = "".join(parts)[:_MAX_CONTENT_CHARS]
                    self._patch(run_id, content=content, stage="模型正文已返回，正在完成治理与落盘")
                if event.get("done"):
                    done_seen = True
                    truncated = bool(event.get("truncated"))
            content = "".join(parts)[:_MAX_CONTENT_CHARS].strip()
            failure_marker = next((marker for marker in _FAILURE_MARKERS if marker in content), "")
            kind = str(row.get("kind") or "")
            if kind == "chain":
                missing_sections = [
                    str(index) for index in range(1, 9)
                    if not re.search(rf"(?m)^###\s+{index}\.", content)
                ]
            else:
                missing_sections = [
                    numeral for numeral in _COMPANY_SECTION_NUMERALS
                    if not re.search(rf"(?m)^#{{1,4}}\s+{numeral}、", content)
                ]
            if not done_seen or not content or truncated or failure_marker or missing_sections:
                error_code = (
                    "generation_incomplete" if not done_seen or not content
                    else "generation_truncated" if truncated
                    else "required_sections_missing" if missing_sections
                    else "generation_failed"
                )
                self._patch(
                    run_id,
                    status="failed",
                    stage="后台研究未形成完整结果",
                    content=content,
                    truncated=truncated,
                    error=(
                        f"研究报告缺少必备章节（{','.join(missing_sections)}）；上次成功结果仍保留。"
                        if missing_sections
                        else "模型未返回可安全使用的完整终态；上次成功结果仍保留。"
                    ),
                    error_code=error_code,
                    finished_at=self._now(),
                )
                return
            self._patch(
                run_id,
                status="completed",
                stage="后台研究已完成",
                content=content,
                truncated=False,
                finished_at=self._now(),
            )
        except asyncio.CancelledError:
            interrupted = self._shutting_down
            self._patch(
                run_id,
                status="interrupted" if interrupted else "canceled",
                stage="服务关闭导致任务中断" if interrupted else "用户已停止后台任务",
                error="服务关闭中断了未完成任务。" if interrupted else "已停止本次后台研究。",
                error_code="service_shutdown" if interrupted else "user_canceled",
                finished_at=self._now(),
            )
        except Exception:  # noqa: BLE001 — 对外只保存安全错误码
            log.exception("后台研究任务失败 run_id=%s", run_id)
            self._patch(
                run_id,
                status="failed",
                stage="后台研究失败",
                error="后台研究生成失败；上次成功结果仍保留。",
                error_code="research_generation_failed",
                finished_at=self._now(),
            )

    async def cancel(self, run_id: str) -> dict | None:
        row = self.get(run_id)
        if row is None:
            return None
        if row.get("status") not in ACTIVE_STATUSES:
            return row
        self._patch(run_id, status="canceling", stage="正在停止后台任务")
        task = self._tasks.get(run_id)
        if task is None:
            return self._patch(
                run_id,
                status="canceled",
                stage="用户已停止后台任务",
                error="已停止本次后台研究。",
                error_code="user_canceled",
                finished_at=self._now(),
            )
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return self.get(run_id)

    def delete(self, run_id: str) -> dict | None:
        """删除已结束的持久化研究记录；活跃任务必须先显式停止。"""
        with self._lock:
            row = self.get(run_id)
            if row is None:
                return None
            if row.get("status") in ACTIVE_STATUSES:
                raise ActiveResearchRunDeleteError(row)
            path = self._path(str(row["kind"]), run_id)
            try:
                path.unlink()
            except FileNotFoundError:
                return None
            return row

    async def shutdown(self) -> None:
        self._shutting_down = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
