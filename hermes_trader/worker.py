"""独立重型后台 Worker。

API 进程只向 ``MaintenanceQueue`` 写任务；本进程领取并执行技术刷新与新闻观察，
从而隔离默认线程池、GIL、CPU 和大部分磁盘写入压力。
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import logging
import os
from pathlib import Path
import signal
import time
from typing import Any

from .analysis import technical_screener
from .config import Config
from .data import news as news_data
from .data import news_observation
from .jobs import news_event_observer
from .maintenance_queue import MaintenanceQueue, default_worker_id


log = logging.getLogger("hermes.worker")
TECHNICAL_JOB = "technical_signal_refresh"
NEWS_JOB = "news_event_observation"
SUPPORTED_JOBS = (TECHNICAL_JOB, NEWS_JOB)


def _lower_process_priority() -> None:
    """降低重型进程调度优先级，让交互 API 优先获得 CPU。"""
    if os.name != "nt":
        try:
            os.nice(5)
        except (AttributeError, OSError) as exc:
            log.warning("无法降低后台 Worker 进程优先级: %s", str(exc)[:160])
        return
    try:
        below_normal_priority_class = 0x00004000
        process = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
        if not ctypes.windll.kernel32.SetPriorityClass(  # type: ignore[attr-defined]
            process, below_normal_priority_class,
        ):
            raise OSError("SetPriorityClass returned false")
    except Exception as exc:  # noqa: BLE001 - 优先级只是额外资源保护
        log.warning("无法降低后台 Worker 进程优先级: %s", str(exc)[:160])


class MaintenanceWorker:
    def __init__(self, config: Config, *, poll_seconds: float | None = None):
        settings = config.section("background_worker")
        self.config = config
        self.queue = MaintenanceQueue(config.data_dir)
        self.worker_id = default_worker_id()
        self.started_at = time.monotonic()
        self.poll_seconds = max(
            0.2,
            min(float(poll_seconds or settings.get("poll_seconds") or 2.0), 30.0),
        )
        self.lease_seconds = max(
            60.0,
            min(float(settings.get("lease_seconds") or 180.0), 900.0),
        )
        self.stop_event = asyncio.Event()
        self.news_ready = False

    def initialize_sources(self) -> None:
        technical_screener.init(self.config.data_dir, reconcile_interrupted=True)
        news_data.init(self.config.data_dir)
        news_observation.init(self.config.data_dir)

    async def migrate_news(self) -> None:
        try:
            await asyncio.to_thread(news_observation.migrate_legacy)
            self.news_ready = True
        except Exception:  # noqa: BLE001 - 技术刷新仍可继续，新闻任务明确失败
            log.exception("旧新闻观察账本迁移失败；新闻任务保持失败状态")
            self.news_ready = False

    async def _worker_lease_forever(self) -> None:
        # health 默认以 30 秒判 stale，所以 Worker 存活心跳不得慢于 15 秒。
        interval = max(5.0, min(15.0, self.lease_seconds / 4))
        while not self.stop_event.is_set():
            if not await asyncio.to_thread(
                self.queue.acquire_worker_lease,
                self.worker_id,
                lease_seconds=self.lease_seconds,
            ):
                log.error("重型 Worker 失去单实例租约")
                self.stop_event.set()
                return
            await asyncio.to_thread(self.queue.register_worker, self.worker_id)
            await asyncio.sleep(interval)

    async def _heartbeat_forever(self, job_id: str) -> None:
        interval = max(10.0, self.lease_seconds / 3)
        while not self.stop_event.is_set():
            await asyncio.sleep(interval)
            await asyncio.to_thread(
                self.queue.heartbeat,
                job_id,
                self.worker_id,
                lease_seconds=self.lease_seconds,
            )

    async def _run_technical(self, job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
        try:
            workers = max(1, min(int(payload.get("workers") or 4), 12))
            raw_limit = payload.get("max_symbols")
            limit = None if raw_limit in (None, "") else max(1, min(int(raw_limit), 6000))
        except (TypeError, ValueError):
            return "failed", {
                "ok": False,
                "state": "invalid",
                "error_code": "invalid_technical_refresh_payload",
            }

        def on_progress(value: dict[str, Any]) -> None:
            self.queue.heartbeat(
                str(job["job_id"]),
                self.worker_id,
                lease_seconds=self.lease_seconds,
                progress=value,
            )

        result = await asyncio.to_thread(
            technical_screener.refresh_all,
            max_symbols=limit,
            workers=workers,
            resume=progress,
            progress_callback=on_progress,
        )
        state = str(result.get("state") or "missing")
        final = "completed" if state == "ready" else ("partial" if state == "partial" else "failed")
        return final, result

    async def _run_news(self, _job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if not self.news_ready:
            return "failed", {
                "ok": False,
                "error_code": "news_observation_migration_unavailable",
            }
        result = await news_event_observer.capture(self.config)
        status, error = news_event_observer.capture_job_state(result)
        result = {**result, "capture_status": status}
        if error:
            result["capture_error"] = error
        return ("completed" if status == "ok" else "partial"), result

    async def run_job(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("job_id") or "")
        kind = str(job.get("kind") or "")
        heartbeat = asyncio.create_task(self._heartbeat_forever(job_id))
        try:
            log.info("开始重型任务 job=%s kind=%s attempt=%s", job_id, kind, job.get("attempts"))
            if kind == TECHNICAL_JOB:
                status, result = await self._run_technical(job)
            elif kind == NEWS_JOB:
                status, result = await self._run_news(job)
            else:
                status, result = "failed", {
                    "ok": False,
                    "error_code": "unsupported_maintenance_job",
                }
            error_code = None if status in {"completed", "partial"} else str(result.get("error_code") or "maintenance_job_failed")
            error_message = None if status in {"completed", "partial"} else str(result.get("error") or "重型后台任务执行失败")
            await asyncio.to_thread(
                self.queue.finish,
                job_id,
                self.worker_id,
                status=status,
                result=result,
                error_code=error_code,
                error_message=error_message,
            )
            log.info("结束重型任务 job=%s kind=%s status=%s", job_id, kind, status)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 队列必须得到明确失败终态
            log.exception("重型任务异常 job=%s kind=%s", job_id, kind)
            await asyncio.to_thread(
                self.queue.finish,
                job_id,
                self.worker_id,
                status="failed",
                result={"ok": False, "error_code": "maintenance_job_exception"},
                error_code="maintenance_job_exception",
                error_message=str(exc)[:300],
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def run(self, *, once: bool = False) -> int:
        if not self.queue.acquire_worker_lease(self.worker_id, lease_seconds=self.lease_seconds):
            log.error("已有同一 data_dir 的重型 Worker 存活，当前进程退出")
            return 2
        self.queue.register_worker(self.worker_id)
        lease_task = asyncio.create_task(self._worker_lease_forever())
        try:
            self.initialize_sources()
            await self.migrate_news()
            log.info(
                "重型后台 Worker 已启动 id=%s data_dir=%s poll=%.1fs",
                self.worker_id,
                self.config.data_dir,
                self.poll_seconds,
            )
            while not self.stop_event.is_set():
                if self.stop_event.is_set():
                    return 3
                job = await asyncio.to_thread(
                    self.queue.claim_next,
                    self.worker_id,
                    kinds=SUPPORTED_JOBS,
                    lease_seconds=self.lease_seconds,
                )
                if job is not None:
                    await self.run_job(job)
                    if once:
                        return 0
                    continue
                if once:
                    return 0
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=self.poll_seconds)
                except asyncio.TimeoutError:
                    pass
            return 0
        finally:
            lease_task.cancel()
            await asyncio.gather(lease_task, return_exceptions=True)
            self.queue.release_worker_lease(self.worker_id)


async def _async_main(args: argparse.Namespace) -> int:
    config = Config(args.config)
    settings = config.section("background_worker")
    if settings.get("enabled", True) is False:
        log.warning("background_worker.enabled=false，Worker 未启动")
        return 0
    _lower_process_priority()
    worker = MaintenanceWorker(config, poll_seconds=args.poll_seconds)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass
    return await worker.run(once=args.once)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Trader 重型后台 Worker")
    parser.add_argument("--config", required=True)
    parser.add_argument("--poll-seconds", type=float)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
