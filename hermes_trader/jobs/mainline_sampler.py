"""交易时段主线时间轴自动采样。"""
from __future__ import annotations

import asyncio
import logging

from ..analysis import mainline_runtime, mainline_timeline
from ..data.base import is_trade_day, now_cn


log = logging.getLogger("hermes.mainline_sampler")
_BOOT_TASKS: set[asyncio.Task] = set()


def in_sampling_window(now=None) -> bool:
    """09:25–11:30 / 13:00–15:00；15:00 这一档作为收盘定稿。"""
    now = now or now_cn()
    if not is_trade_day(now.date()):
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 25) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60)


async def capture(config) -> dict:
    """生成与 API 同源的截面并原子更新客户时间轴。"""
    response = await mainline_runtime.build_current_response(limit=5)
    result = await asyncio.to_thread(
        mainline_timeline.observe_response,
        config.data_dir,
        response,
        now_cn(),
    )
    await asyncio.to_thread(mainline_runtime.save_current_snapshot, config.data_dir, result)
    return result


def register(scheduler, config) -> None:
    """每 10 分钟发起一档，午休/非交易日由任务内守卫跳过。"""
    from apscheduler.triggers.cron import CronTrigger  # noqa: PLC0415

    async def _tick() -> None:
        if not in_sampling_window():
            return
        try:
            result = await capture(config)
            log.debug(
                "主线时间轴采样完成: %s %s",
                result.get("data_date"),
                result.get("time_scope", {}).get("session_label"),
            )
        except Exception as exc:  # noqa: BLE001 -- 采样不得阻断其它交易任务
            log.warning("主线时间轴采样失败（本档跳过）: %s", str(exc)[:180])

    scheduler.add_job(
        _tick,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/10", timezone=config.timezone),
        id="mainline_timeline_sample",
        name="今日行业主线时间轴",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=180,
    )
    # 服务重启后不等下一个整 10 分钟档；交易时段后台立即补一份完整快照。
    # 任务不 await，不阻塞启动，也不让第一个页面请求承担 10–20 秒的真实源计算。
    if in_sampling_window():
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task = loop.create_task(_tick())
            _BOOT_TASKS.add(task)
            task.add_done_callback(_BOOT_TASKS.discard)
    log.info("已注册主线时间轴（交易时段每10分钟，15:00收盘定稿）")
