"""新闻事件观察账本的后台采样任务。"""
from __future__ import annotations

import asyncio
import logging
import time

from apscheduler.triggers.cron import CronTrigger

from ..data.base import now_cn


log = logging.getLogger("hermes.news_observer")


def capture_job_state(result: dict) -> tuple[str, str | None]:
    """任务执行成功与数据完整分开记账。"""
    tried = int(result.get("sources_tried") or 0)
    live = int(result.get("sources_live") or 0)
    missing = int(result.get("missing") or 0)
    items = int(result.get("news_items") or 0)
    reasons = []
    if missing:
        reasons.append(f"observation_checkpoints_missing={missing}")
    if tried and live < tried:
        reasons.append(f"news_sources_live={live}/{tried}")
    if items <= 0:
        reasons.append("news_feed_empty")
    if result.get("ok") is False:
        reasons.append(str(result.get("error_code") or "capture_failed"))
    return ("warn", "; ".join(dict.fromkeys(reasons))[:300]) if reasons else ("ok", None)


async def capture(config) -> dict:
    """采集当前新闻并补齐所有到期事件；页面访问不参与写盘。"""
    from ..data import news, news_mapping, news_observation  # noqa: PLC0415

    feed = await asyncio.to_thread(news.cn_news_aggregated, 80)
    source_items = list((feed or {}).get("items") or [])
    mapped = await asyncio.to_thread(news_mapping.map_news_items, source_items)
    result = await asyncio.to_thread(
        news_observation.capture_mapped_items,
        list((mapped or {}).get("items") or []),
    )
    return {
        **result,
        "news_items": len(source_items),
        "sources_tried": int((feed or {}).get("sources_tried") or 0),
        "sources_live": int((feed or {}).get("sources_live") or 0),
    }


def register(scheduler, config, store) -> None:
    settings = config.section("news_event_observation") if hasattr(config, "section") else {}
    if not settings.get("enabled", False):
        return
    interval = max(5, min(int(settings.get("interval_min") or 10), 30))

    async def _tick() -> None:
        started = time.monotonic()
        started_at = now_cn().strftime("%Y-%m-%d %H:%M:%S")
        if store:
            store.record_job("news_event_observation", last_run=started_at, last_status="running", last_error=None)
        try:
            result = await capture(config)
            finished = now_cn().strftime("%Y-%m-%d %H:%M:%S")
            status, error = capture_job_state(result)
            if store:
                store.record_job(
                    "news_event_observation",
                    last_status=status,
                    last_ok=finished if status == "ok" else None,
                    last_finished=finished,
                    last_duration_ms=max(0, round((time.monotonic() - started) * 1000)),
                    last_error=error,
                    last_result={
                        "status": status,
                        "news_items": int(result.get("news_items") or 0),
                        "sources_live": int(result.get("sources_live") or 0),
                        "sources_tried": int(result.get("sources_tried") or 0),
                        "created": int(result.get("created") or 0),
                        "captured": int(result.get("captured") or 0),
                        "missing": int(result.get("missing") or 0),
                    },
                )
            log.info(
                "新闻事件观察采样: 新增%s，检查点%s，缺失%s",
                result.get("created", 0), result.get("captured", 0), result.get("missing", 0),
            )
        except Exception as exc:  # noqa: BLE001 -- 观察任务不得阻断主服务
            log.warning("新闻事件观察采样失败（本档保留旧账本）: %s", str(exc)[:180])
            if store:
                store.record_job(
                    "news_event_observation", last_status="error",
                    last_finished=now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                    last_duration_ms=max(0, round((time.monotonic() - started) * 1000)),
                    last_error=str(exc)[:300],
                )

    scheduler.add_job(
        _tick,
        CronTrigger(day_of_week="mon-sun", minute=f"*/{interval}", timezone=config.timezone),
        id="news_event_observation",
        name="A股新闻事件观察账本",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
        next_run_time=now_cn(),
    )
    log.info("已注册新闻事件观察账本（全天每%d分钟，启动即采样）", interval)
