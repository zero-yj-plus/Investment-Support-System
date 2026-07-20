"""官方龙虎榜盘后采集任务。

交易所网站接口需要分页并逐事件读取深市席位明细，属于有界但较重的
N+1 采集。这里把写盘责任固定在后台任务，API、MCP 和页面只读取指定
交易日快照，避免用户访问触发官网请求。

默认 18:20 / 21:10 是产品侧观察与复核时点，不是交易所公开 SLA。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from typing import Any

from apscheduler.triggers.cron import CronTrigger

from ..data.base import now_cn, trade_day_status


log = logging.getLogger("hermes.official_longhu_collector")
DEFAULT_TIMES = ("18:20", "21:10")


def _valid_times(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return DEFAULT_TIMES
    accepted: list[str] = []
    for value in values[:4]:
        try:
            hour_text, minute_text = str(value).strip().split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            normalized = f"{hour:02d}:{minute:02d}"
            if normalized not in accepted:
                accepted.append(normalized)
    return tuple(accepted) or DEFAULT_TIMES


def _bounded_settings(config) -> dict[str, Any]:
    raw = config.section("official_longhu") if hasattr(config, "section") else {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "timeout": max(3.0, min(float(raw.get("timeout_sec") or 8.0), 20.0)),
        "max_pages": max(1, min(int(raw.get("max_pages") or 20), 50)),
        "max_detail_requests": max(
            0, min(int(raw.get("max_detail_requests") or 100), 200)
        ),
        "request_gap_seconds": max(
            0.0, min(float(raw.get("request_gap_sec") or 1.0), 5.0)
        ),
        "times": _valid_times(raw.get("times")),
    }


async def capture(config, trade_date: str | date | None = None) -> dict[str, Any]:
    """抓取并原子保存一个指定交易日；不以失败结果覆盖已有快照。"""
    from ..data import official_longhu_source  # noqa: PLC0415

    settings = _bounded_settings(config)
    result = await asyncio.to_thread(
        official_longhu_source.fetch_official_longhu,
        trade_date,
        timeout=settings["timeout"],
        max_pages=settings["max_pages"],
        max_detail_requests=settings["max_detail_requests"],
        request_gap_seconds=settings["request_gap_seconds"],
    )
    path = await asyncio.to_thread(official_longhu_source.save_snapshot, result)
    invalidated = 0
    if path is not None:
        # 官方快照到达 → 强制失效东财龙虎榜盘外缓存，让下次页面请求重取今日榜，
        # 不让"今日结算中"的空结果继续在 6 小时长缓存里冒充"今日无榜"。
        from ..data.base import invalidate_ttl_cache  # noqa: PLC0415

        invalidated = invalidate_ttl_cache("hermes_trader.data.mainforce_source.longhu:")
    return {
        **result,
        "snapshot_saved": path is not None,
        "snapshot_file": path.name if path is not None else None,
        "longhu_cache_invalidated": invalidated,
    }


def register(scheduler, config, store) -> None:
    """注册客户可配置的盘后采集与复核任务。"""
    settings = _bounded_settings(config)
    if not settings["enabled"]:
        return

    def _job_id(hhmm: str) -> str:
        return f"official_longhu_{hhmm.replace(':', '')}"

    def _make_tick(hhmm: str):
        job_id = _job_id(hhmm)

        async def _tick() -> dict[str, Any]:
            started = time.monotonic()
            started_at = now_cn().strftime("%Y-%m-%d %H:%M:%S")
            calendar = await asyncio.to_thread(trade_day_status)
            if calendar.get("status") != "open":
                status = "skipped" if calendar.get("status") == "closed" else "blocked"
                error_code = (
                    "skipped_non_trading_day"
                    if status == "skipped"
                    else "calendar_unavailable"
                )
                if store:
                    store.record_job(
                        job_id,
                        last_run=started_at,
                        last_finished=started_at,
                        last_status=status,
                        last_error=None,
                        last_error_code=error_code,
                        calendar_status=calendar,
                    )
                return {"ok": status == "skipped", "status": status, "error_code": error_code}

            if store:
                store.record_job(
                    job_id, last_run=started_at, last_status="running", last_error=None
                )
            try:
                result = await capture(config, now_cn().date().isoformat())
                state = str(result.get("state") or "missing")
                if state == "complete":
                    status, error_code = "ok", None
                elif state in {"degraded", "settling"}:
                    status, error_code = "warn", f"official_longhu_{state}"
                elif state == "not_due":
                    status, error_code = "skipped", "official_longhu_not_due"
                else:
                    status, error_code = "error", "official_longhu_missing"
                finished = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                if store:
                    store.record_job(
                        job_id,
                        last_status=status,
                        last_ok=finished if status == "ok" else None,
                        last_finished=finished,
                        last_duration_ms=max(0, round((time.monotonic() - started) * 1000)),
                        last_error=(
                            None if status == "ok" else str(result.get("availability_reason") or "")[:300]
                        ),
                        last_error_code=error_code,
                        data_date=result.get("trade_date"),
                        snapshot_saved=bool(result.get("snapshot_saved")),
                        event_count=int(result.get("event_count") or 0),
                        seat_count=int(result.get("seat_count") or 0),
                    )
                log.info(
                    "官方龙虎榜采集 %s: state=%s events=%s seats=%s saved=%s",
                    hhmm,
                    state,
                    result.get("event_count", 0),
                    result.get("seat_count", 0),
                    bool(result.get("snapshot_saved")),
                )
                return {
                    "ok": status in {"ok", "warn", "skipped"},
                    "status": status,
                    "state": state,
                    "error_code": error_code,
                }
            except Exception as exc:  # noqa: BLE001 -- 采集失败不阻断其它任务
                finished = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                if store:
                    store.record_job(
                        job_id,
                        last_status="error",
                        last_finished=finished,
                        last_duration_ms=max(0, round((time.monotonic() - started) * 1000)),
                        last_error=str(exc)[:300],
                        last_error_code="official_longhu_collection_failed",
                    )
                log.warning("官方龙虎榜采集 %s 失败，保留旧快照: %s", hhmm, str(exc)[:180])
                return {
                    "ok": False,
                    "status": "error",
                    "error_code": "official_longhu_collection_failed",
                }

        return _tick

    for hhmm in settings["times"]:
        hour_text, minute_text = hhmm.split(":", 1)
        scheduler.add_job(
            _make_tick(hhmm),
            CronTrigger(
                day_of_week="mon-fri",
                hour=int(hour_text),
                minute=int(minute_text),
                timezone=config.timezone,
            ),
            id=_job_id(hhmm),
            name=f"官方龙虎榜盘后采集 {hhmm}",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=900,
        )
    log.info(
        "已注册官方龙虎榜盘后采集（产品观察时点：%s；非交易所 SLA）",
        "、".join(settings["times"]),
    )
