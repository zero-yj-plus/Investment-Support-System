"""定时任务调度 — APScheduler，6 个交易日任务节点。

  morning 08:50  早盘内参         → 生成 + 飞书推送
  midday  14:45  尾盘决策草案      → 只生成、不推送
  tail    14:52  尾盘决策定稿      → 读取草案后生成 + 单次推送
  close   15:30  收盘复盘         → 生成 + 飞书推送（含情绪快照落盘）
  evening 19:30  次日策略 + 趋势票 → 两份报告生成 + 飞书推送
  archive 22:00  按日期自动存档    → 打包 + 飞书通知

设置页修改时间后调用 apply_schedule() 热生效。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..data.base import is_trade_day, now_cn, trade_day_status

log = logging.getLogger("hermes.jobs")

JOB_NAMES = {
    "morning": "早盘内参",
    "midday": "尾盘决策草案",
    "tail": "尾盘决策定稿",
    "close": "收盘复盘",
    "evening": "次日策略+趋势票",
    "archive": "晚间自动存档",
}


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


def _report_output_artifact(report: dict | None) -> dict | None:
    """把报告结果收敛为可公开的任务产物指针，不暴露正文或本地路径。"""
    if not isinstance(report, dict) or not report.get("content_id"):
        return None
    return {
        "kind": "report",
        "content_id": str(report["content_id"]),
        "report_type": str(report.get("rtype") or ""),
        "trade_day": str(report.get("date") or ""),
        "version": report.get("plan_version") or report.get("report_version"),
        "content_hash": str(report.get("content_hash") or ""),
    }


def _archive_output_artifact(bundle: dict | None) -> dict | None:
    """每日存档只公开日期与包含的报告类型，不返回持仓/知识正文。"""
    if not isinstance(bundle, dict) or not bundle.get("date"):
        return None
    reports = bundle.get("reports") if isinstance(bundle.get("reports"), dict) else {}
    archive_date = str(bundle["date"])
    return {
        "kind": "archive",
        "content_id": f"archive:{archive_date}",
        "trade_day": archive_date,
        "report_types": sorted(str(key) for key in reports),
    }


def _parse_hhmm(value: str, fallback: str) -> tuple[int, int]:
    try:
        h, m = str(value).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, AttributeError):
        pass
    h, m = fallback.split(":")
    return int(h), int(m)


def build_scheduler(config, engine) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.timezone)

    store = getattr(engine, "store", None)

    async def run_job(job_id: str) -> dict:
        # is_trade_day 冷缓存时会同步阻塞抓取交易日历（最坏约 30 秒），
        # AsyncIOScheduler 跑在 uvicorn 主事件循环上，必须丢线程池执行。
        if config.trading_days_only:
            calendar = await asyncio.to_thread(trade_day_status)
            if calendar["status"] != "open":
                status = "skipped" if calendar["status"] == "closed" else "blocked"
                error_code = "skipped_non_trading_day" if status == "skipped" else "calendar_unavailable"
                log.info("交易日门禁 %s，%s 任务 %s", calendar["status"], status, job_id)
                if store:
                    now = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                    store.record_job(
                        job_id, last_run=now, last_finished=now, last_status=status,
                        last_error=None, last_error_code=error_code,
                        calendar_status=calendar,
                    )
                return {"ok": status == "skipped", "status": status, "error_code": error_code, "calendar": calendar}
        required_delivery = bool(config.push_enabled and job_id in {"morning", "tail", "close", "evening"})
        delivery_ready = True
        readiness = None
        if required_delivery:
            from ..delivery import delivery_readiness  # noqa: PLC0415

            readiness = await delivery_readiness(
                store=store,
                pusher=getattr(engine, "feishu", None),
                config=config,
            )
            delivery_ready = bool(readiness["launch_ready"])
            if not delivery_ready:
                # 发送通道不可用只能阻塞“自动发送”，不得连带阻塞当日事实定稿。
                # 先生成并冻结，通道恢复后再从该版本发送。
                log.warning("自动发送未就绪，%s 仍继续生成定稿", job_id)
        log.info("执行定时任务: %s（%s）", job_id, JOB_NAMES.get(job_id, ""))
        started = time.monotonic()
        now = now_cn().strftime("%Y-%m-%d %H:%M:%S")
        if store:
            store.record_job(job_id, last_run=now, last_status="running", last_error=None)
        try:
            push_now = bool(config.push_enabled and (not required_delivery or delivery_ready))
            generated = False
            output_artifacts: list[dict] = []
            if job_id == "archive":
                bundle = await engine.archive_today(push=config.push_enabled)
                pushed = None
                generated = True
                artifact = _archive_output_artifact(bundle)
                if artifact:
                    output_artifacts.append(artifact)
            elif job_id == "evening":
                r1 = await engine.generate_report("evening", push=push_now)
                r2 = await engine.generate_report("trend", push=push_now)
                generated = bool(
                    r1 and r1.get("content_md") and r2 and r2.get("content_md")
                )
                pushed = bool((r1 and r1.get("pushed")) and (r2 and r2.get("pushed")))
                output_artifacts = [
                    artifact for artifact in (
                        _report_output_artifact(r1), _report_output_artifact(r2),
                    ) if artifact
                ]
            elif job_id == "midday":
                # 14:45 是尾盘决策草案，只作为 14:52 定稿的输入。过去这里与 tail
                # 连续推两条高度重叠的报告，用户无法判断哪份才是正式结论。
                # 保留 midday 报告/旧 API 兼容，但定时链路不再把草案推给客户。
                r = await engine.generate_report("midday", push=False)
                generated = bool(r and r.get("content_md"))
                pushed = None
                artifact = _report_output_artifact(r)
                if artifact:
                    output_artifacts.append(artifact)
            else:
                r = await engine.generate_report(job_id, push=push_now)
                generated = bool(r and r.get("content_md"))
                pushed = bool(r and r.get("pushed"))
                artifact = _report_output_artifact(r)
                if artifact:
                    output_artifacts.append(artifact)
            # 收盘后自动回验推荐留痕：用真实收盘价对账昨日/今日推荐，到期即锁定结论，
            # 不依赖用户手点（母文档 §29 成功率闭环）。失败不影响报告任务主流程。
            if job_id == "close" and store:
                try:
                    from ..analysis import recommend  # noqa: PLC0415
                    res = await recommend.run_verify(store)
                    log.info("收盘自动回验推荐: %s", res)
                except Exception as e:  # noqa: BLE001
                    log.warning("收盘自动回验推荐失败: %s", str(e)[:160])
            required_push = required_delivery
            if not generated:
                task_status, task_error_code = "error", "report_not_generated"
            elif required_push and not delivery_ready:
                task_status, task_error_code = "blocked", "delivery_not_ready"
            elif required_push and not pushed:
                task_status, task_error_code = "error", "required_delivery_not_accepted"
            else:
                task_status, task_error_code = "ok", None
            if store:
                finished = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                # 推送禁用时 last_push_ok 记 None（区别于"推送失败"=False）
                store.record_job(
                    job_id, last_status=task_status,
                    last_ok=(finished if task_status == "ok" else None), last_finished=finished,
                    last_duration_ms=_elapsed_ms(started), last_error=None,
                    last_error_code=task_error_code,
                    last_generated_ok=generated,
                    last_output_objects=output_artifacts,
                    delivery_readiness=readiness,
                    last_push_ok=(pushed if push_now and pushed is not None else None),
                    last_push_time=(finished if push_now and pushed is not None else None),
                )
            return {
                "ok": task_status == "ok", "status": task_status,
                "job_id": job_id, "generated": generated, "pushed": pushed,
                "error_code": task_error_code, "readiness": readiness,
                "output_objects": output_artifacts,
            }
        except Exception as e:
            log.exception("定时任务 %s 执行异常", job_id)
            if store:
                store.record_job(
                    job_id,
                    last_status="error",
                    last_finished=now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                    last_duration_ms=_elapsed_ms(started),
                    last_error=str(e)[:200],
                    last_error_code="job_execution_failed",
                )
            return {"ok": False, "status": "error", "job_id": job_id, "error_code": "job_execution_failed"}

    scheduler.run_job = run_job  # 暴露给手动触发接口复用

    # 资金持续追踪：盘中每 15 分钟采集 top 板块 + 热门股资金快照入库。客户要的"数据持续性"
    # 靠定时累积实现（非买历史）。阻塞型采集丢线程池，不卡事件循环；非交易日跳过。
    async def _capital_snapshot() -> None:
        if config.trading_days_only and not await asyncio.to_thread(is_trade_day):
            return
        started = time.monotonic()
        now = now_cn().strftime("%Y-%m-%d %H:%M:%S")
        if store:
            store.record_job("capital_snapshot", last_run=now, last_status="running", last_error=None)
        try:
            from ..data import capital_tracker  # noqa: PLC0415
            r = await asyncio.to_thread(capital_tracker.snapshot)
            log.info("资金快照入库: %s", r)
            if store:
                finished = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                if r.get("ok") is not True:
                    snapshot_status = "error"
                    snapshot_error = str(r.get("msg") or "资金快照采集为空")[:300]
                elif r.get("complete") is False:
                    snapshot_status = "degraded"
                    missing = "、".join(str(x) for x in (r.get("missing") or []))
                    snapshot_error = f"部分采集缺失：{missing}"[:300]
                else:
                    snapshot_status = "ok"
                    snapshot_error = None
                store.record_job(
                    "capital_snapshot",
                    last_status=snapshot_status,
                    last_ok=(finished if snapshot_status == "ok" else None),
                    last_finished=finished,
                    last_duration_ms=_elapsed_ms(started),
                    last_error=snapshot_error,
                )
        except Exception as e:  # noqa: BLE001
            log.warning("资金快照失败: %s", str(e)[:160])
            if store:
                store.record_job(
                    "capital_snapshot", last_status="error",
                    last_finished=now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                    last_duration_ms=_elapsed_ms(started), last_error=str(e)[:300],
                )

    if getattr(config, "capital_tracking_enabled", False):
        _iv = max(1, min(30, getattr(config, "capital_tracking_interval_min", 15)))
        scheduler.add_job(
            _capital_snapshot,
            CronTrigger(day_of_week="mon-fri", hour="9-15", minute=f"*/{_iv}", timezone=config.timezone),
            id="capital_snapshot", replace_existing=True, misfire_grace_time=120,
        )

    # 观察池纯数字双周期区间：后台每 5 分钟刷新，普通 GET 只读快照，避免页面加载触发
    # 每只股票两路历史行情的重型 N+1。波段计算会自动排除 15:05 前未收盘的当日日 K；
    # 超短区间在新交易日生成前不会沿用旧结果。
    async def _watchlist_zone_refresh() -> None:
        if not store or not hasattr(store, "watchlist") or not hasattr(store, "save_watchlist_zones"):
            return
        if not await asyncio.to_thread(is_trade_day):
            return
        started = time.monotonic()
        now = now_cn().strftime("%Y-%m-%d %H:%M:%S")
        if hasattr(store, "record_job"):
            store.record_job("watchlist_zones", last_run=now, last_status="running", last_error=None)
        try:
            from ..analysis import watchlist_zones  # noqa: PLC0415

            result = await asyncio.to_thread(watchlist_zones.refresh_all, store)
            if hasattr(store, "record_job"):
                finished = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                store.record_job(
                    "watchlist_zones", last_status="ok", last_ok=finished,
                    last_finished=finished, last_duration_ms=_elapsed_ms(started),
                    last_error=None, last_result=result,
                )
        except Exception as exc:  # noqa: BLE001 - 派生区间不能阻断主调度器
            log.warning("观察池双周期区间刷新失败: %s", str(exc)[:180])
            if hasattr(store, "record_job"):
                store.record_job(
                    "watchlist_zones", last_status="error",
                    last_finished=now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                    last_duration_ms=_elapsed_ms(started), last_error=str(exc)[:300],
                )

    scheduler.add_job(
        _watchlist_zone_refresh,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5", timezone=config.timezone),
        id="watchlist_zones", name="观察池双周期区间·每5分钟",
        replace_existing=True, misfire_grace_time=120, coalesce=True, max_instances=1,
        next_run_time=now_cn(),
    )

    # 全市场技术信号只在盘后后台预计算。普通筛选请求严格只读 SQLite，禁止页面 GET
    # 触发约 5500 只股票的历史 K 线 N+1。
    technical_refresh_config = (
        config.section("technical_signal_refresh") if hasattr(config, "section") else {}
    )
    try:
        technical_refresh_workers = max(
            1, min(int(technical_refresh_config.get("workers", 4)), 12)
        )
    except (TypeError, ValueError):
        technical_refresh_workers = 4

    async def _technical_signal_refresh() -> None:
        if config.trading_days_only and not await asyncio.to_thread(is_trade_day):
            return
        started = time.monotonic()
        stamp = now_cn().strftime("%Y-%m-%d %H:%M:%S")
        if store:
            store.record_job("technical_signal_refresh", last_run=stamp, last_status="running", last_error=None)
        try:
            from ..analysis import technical_screener  # noqa: PLC0415

            result = await asyncio.to_thread(
                technical_screener.refresh_all, workers=technical_refresh_workers,
            )
            final = now_cn().strftime("%Y-%m-%d %H:%M:%S")
            state = str(result.get("state") or "missing")
            if store:
                store.record_job(
                    "technical_signal_refresh",
                    last_status=("ok" if state == "ready" else "warn"),
                    last_ok=(final if state == "ready" else None), last_finished=final,
                    last_duration_ms=_elapsed_ms(started), last_error=None,
                    last_result={key: result.get(key) for key in ("state", "processed", "succeeded", "failed", "coverage_ratio")},
                )
        except Exception as exc:  # noqa: BLE001 - 派生缓存失败不得阻断主服务
            log.warning("全市场技术信号刷新失败: %s", str(exc)[:180])
            if store:
                store.record_job(
                    "technical_signal_refresh", last_status="error",
                    last_finished=now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                    last_duration_ms=_elapsed_ms(started), last_error=str(exc)[:300],
                )

    if technical_refresh_config.get("enabled", True):
        scheduler.add_job(
            _technical_signal_refresh,
            CronTrigger(day_of_week="mon-fri", hour=16, minute=10, timezone=config.timezone),
            id="technical_signal_refresh", name="全市场技术信号·盘后预计算",
            replace_existing=True, misfire_grace_time=1800, coalesce=True, max_instances=1,
        )

    # 业绩预告掘金每日推送（config `forecast_mining.push_enabled` 门控，off-by-default → 未配置
    # 的客户零影响）。默认 20:30：预告公告集中在晚间落库，晚推能带上当晚新增；时点可 config 改。
    _fm = config.section("forecast_mining") if hasattr(config, "section") else {}
    if _fm.get("push_enabled"):
        async def _forecast_push() -> None:
            if config.trading_days_only and not await asyncio.to_thread(is_trade_day):
                return
            started = time.monotonic()
            now = now_cn().strftime("%Y-%m-%d %H:%M:%S")
            if store:
                store.record_job("forecast_mining", last_run=now, last_status="running", last_error=None)
            try:
                from ..analysis import forecast_mining  # noqa: PLC0415
                await forecast_mining.push_daily(engine)
                if store:
                    finished = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                    store.record_job(
                        "forecast_mining", last_status="ok", last_ok=finished, last_finished=finished,
                        last_duration_ms=_elapsed_ms(started), last_error=None,
                    )
            except Exception as e:  # noqa: BLE001 — 推送失败不阻断其它任务
                log.warning("预告掘金推送失败: %s", str(e)[:180])
                if store:
                    store.record_job(
                        "forecast_mining", last_status="error",
                        last_finished=now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                        last_duration_ms=_elapsed_ms(started), last_error=str(e)[:300],
                    )

        _hh, _mm = _parse_hhmm(str(_fm.get("push_time", "20:30")), "20:30")
        scheduler.add_job(
            _forecast_push,
            CronTrigger(day_of_week="mon-fri", hour=_hh, minute=_mm, timezone=config.timezone),
            id="forecast_mining", replace_existing=True, misfire_grace_time=600,
        )
        log.info("已注册 预告掘金推送（每交易日 %02d:%02d）", int(_hh), int(_mm))

    # 运行哨兵（数据/推送两条军规的总闸）：定点确定性自检 + 告警 + 日终对账补推。
    # config `sentinel` 门控 off-by-default；异常自吞绝不阻断其它任务；jobs_state 键 "sentinel"。
    _sn = config.section("sentinel") if hasattr(config, "section") else {}
    if _sn.get("enabled"):
        def _mk_sentinel(hhmm: str, job_id: str):
            async def _run_sentinel() -> None:
                started = time.monotonic()
                now = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                if store:
                    store.record_job(job_id, last_run=now, last_status="running", last_error=None)
                try:
                    from ..analysis import sentinel  # noqa: PLC0415
                    r = await sentinel.run(engine, hhmm)
                    if store:
                        finished = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                        store.record_job(
                            job_id,
                            last_status=("ok" if not r["fails"] else "warn"),
                            last_ok=finished,
                            last_finished=finished,
                            last_duration_ms=_elapsed_ms(started),
                            last_error=("; ".join(r["fails"])[:300] or None),
                        )
                except Exception as e:  # noqa: BLE001 — 哨兵自身故障不阻断，但要留痕
                    log.warning("哨兵执行异常: %s", str(e)[:180])
                    if store:
                        store.record_job(
                            job_id, last_status="error",
                            last_finished=now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                            last_duration_ms=_elapsed_ms(started), last_error=str(e)[:300],
                        )
            return _run_sentinel

        _times = _sn.get("times") or ["09:35", "22:05"]
        for _t in _times:
            _h2, _m2 = _parse_hhmm(str(_t), "22:05")
            _sentinel_id = f"sentinel_{_h2:02d}{_m2:02d}"
            scheduler.add_job(
                _mk_sentinel(f"{_h2:02d}:{_m2:02d}", _sentinel_id),
                CronTrigger(day_of_week="mon-fri", hour=_h2, minute=_m2, timezone=config.timezone),
                id=_sentinel_id, replace_existing=True, misfire_grace_time=600,
            )
        log.info("已注册 运行哨兵（每交易日 %s）", "、".join(str(t) for t in _times))

    apply_schedule(scheduler, config)
    return scheduler


def apply_schedule(scheduler: AsyncIOScheduler, config) -> None:
    """按当前配置（含 settings.json 覆盖）重建全部任务触发器。"""
    times = config.schedule
    defaults = {
        "morning": "08:50", "midday": "14:45", "tail": "14:52",
        "close": "15:30", "evening": "19:30", "archive": "22:00",
    }
    for job_id, fallback in defaults.items():
        hour, minute = _parse_hhmm(times.get(job_id, fallback), fallback)
        trigger = CronTrigger(
            day_of_week="mon-fri", hour=hour, minute=minute, timezone=config.timezone
        )
        existing = scheduler.get_job(job_id)
        if existing:
            scheduler.reschedule_job(job_id, trigger=trigger)
        else:
            scheduler.add_job(
                scheduler.run_job,
                trigger=trigger,
                id=job_id,
                name=JOB_NAMES.get(job_id, job_id),
                args=[job_id],
                misfire_grace_time=600,
                coalesce=True,
                max_instances=1,
            )
    log.info("定时任务计划已应用: %s", {k: times.get(k, v) for k, v in defaults.items()})


def register_ext_jobs(scheduler: AsyncIOScheduler, config, engine, jobs: list[dict]) -> None:
    """注册子项目 ext 提供的定时任务（通用扩展点，任意客户都能加后台任务）。
    每项 job 二选一触发：
      间隔型 {id, interval_min, func(async,收 engine), trading_hours_only(默认True)}；
      定点型 {id, cron:"HH:MM", func}（每交易日该时刻跑一次，如盘后选股 15:10）。
    与内置任务同等：交易日守卫 + 异常自吞，绝不阻断主服务、不污染共享核心。"""
    for j in jobs or []:
        try:
            jid = str(j.get("id") or "").strip()
            func = j.get("func")
            if not jid or not callable(func):
                continue
            cron = str(j.get("cron") or "").strip()           # "HH:MM" → 每交易日定点跑一次
            th_only = bool(j.get("trading_hours_only", True))
            guard_td = bool(cron) or th_only                  # 定点/盘中任务都只在交易日跑

            async def _run(_f=func, _jid=jid, _g=guard_td) -> None:
                if _g and config.trading_days_only:
                    calendar = await asyncio.to_thread(trade_day_status)
                    if calendar["status"] != "open":
                        _store = getattr(engine, "store", None)
                        if _store:
                            now = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                            _store.record_job(
                                f"ext_{_jid}", last_run=now, last_finished=now,
                                last_status=("skipped" if calendar["status"] == "closed" else "blocked"),
                                last_error=None,
                                last_error_code=("skipped_non_trading_day" if calendar["status"] == "closed" else "calendar_unavailable"),
                                calendar_status=calendar,
                            )
                        return
                # ext 任务与内置任务同款 jobs_state 留痕（键 ext_<id>）——否则跑没跑只能翻网关
                # 日志反推（7/2 审计的观测盲区）。失败仍自吞不阻断，只是把状态记下来。
                _store = getattr(engine, "store", None)
                _started = time.monotonic()
                _now = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                if _store:
                    _store.record_job(f"ext_{_jid}", last_run=_now, last_status="running", last_error=None)
                try:
                    _result = _f(engine)
                    if inspect.isawaitable(_result):
                        _result = await _result
                    if _result is False or (isinstance(_result, dict) and _result.get("ok") is False):
                        raise RuntimeError(
                            str((_result or {}).get("error_code") or (_result or {}).get("error") or "extension_result_failed")
                            if isinstance(_result, dict) else "extension_result_failed"
                        )
                    if _store:
                        # last_ok 记录【完成】时刻（_now 是开始时刻）——长任务下两者可差数十秒
                        _finished = now_cn().strftime("%Y-%m-%d %H:%M:%S")
                        _store.record_job(
                            f"ext_{_jid}", last_status="ok", last_ok=_finished, last_finished=_finished,
                            last_duration_ms=_elapsed_ms(_started), last_error=None,
                        )
                except Exception as e:  # noqa: BLE001 — ext 任务失败不阻断、不影响内置任务
                    log.warning("ext 定时任务 %s 执行异常: %s", _jid, str(e)[:180])
                    if _store:
                        _store.record_job(
                            f"ext_{_jid}", last_status="error",
                            last_finished=now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                            last_duration_ms=_elapsed_ms(_started), last_error=str(e)[:300],
                        )

            if cron and ":" in cron:
                hh, mm = cron.split(":", 1)
                trigger = CronTrigger(day_of_week="mon-fri", hour=int(hh), minute=int(mm), timezone=config.timezone)
                desc = f"每交易日 {cron}"
            else:
                iv = max(1, min(60, int(j.get("interval_min") or 15)))
                trigger = (
                    CronTrigger(day_of_week="mon-fri", hour="9-15", minute=f"*/{iv}", timezone=config.timezone)
                    if th_only else
                    CronTrigger(minute=f"*/{iv}", timezone=config.timezone)
                )
                desc = f"每 {iv} 分钟" + ("·交易时段" if th_only else "")
            scheduler.add_job(
                _run,
                trigger,
                id=f"ext_{jid}",
                name=str(j.get("name") or jid),
                replace_existing=True,
                misfire_grace_time=300,
            )
            log.info("已注册 ext 定时任务 %s（%s）", jid, desc)
        except Exception:  # noqa: BLE001
            log.exception("注册 ext 定时任务失败（跳过，不影响主服务）")


def _status_row(
    job_id: str,
    name: str,
    *,
    kind: str,
    enabled: bool,
    next_run: str | None,
    state: dict,
    configured_time: str | None = None,
) -> dict:
    status = state.get("last_status")
    # 调度器落盘的 last_error 供服务端排障，其中可能含内部 URL、
    # 文件路径或上游响应。客户端只返回稳定错误码和安全文案。
    has_error = bool(state.get("last_error"))
    interrupted = status == "interrupted"
    degraded = status == "warn"
    public_error = (
        "上次任务随服务重启中断，本次未产生完整结果"
        if interrupted else (
            "任务已完成，但部分数据源或检查项不完整"
            if degraded else ("任务执行失败，请查看服务端日志" if has_error else None)
        )
    )
    public_error_code = (
        "job_interrupted_by_restart" if interrupted
        else ("job_data_degraded" if degraded else ("job_execution_failed" if has_error else None))
    )
    endpoint = f"/api/jobs/{job_id}/run"
    return {
        "id": job_id,
        "name": name,
        "kind": kind,
        "enabled": enabled,
        "configured_time": configured_time,
        # next_run 保留 v1 兼容；next_run_at 是 task-center-v1 的语义化字段。
        "next_run": next_run,
        "next_run_at": next_run,
        "last_run": state.get("last_run"),
        "last_status": status,
        "last_ok": state.get("last_ok"),
        "last_finished": state.get("last_finished") or state.get("last_ok"),
        "last_duration_ms": state.get("last_duration_ms"),
        "last_output_objects": (
            state.get("last_output_objects")
            if isinstance(state.get("last_output_objects"), list) else []
        ),
        "last_push_ok": state.get("last_push_ok"),
        "last_push_time": state.get("last_push_time"),
        "last_error": public_error,
        "last_error_code": public_error_code,
        "last_result": {
            **(state.get("last_result") if isinstance(state.get("last_result"), dict) else {}),
            "status": status,
            "started_at": state.get("last_run"),
            "finished_at": state.get("last_finished") or state.get("last_ok"),
            "duration_ms": state.get("last_duration_ms"),
            "output_objects": (
                state.get("last_output_objects")
                if isinstance(state.get("last_output_objects"), list) else []
            ),
            "error": public_error,
            "error_code": public_error_code,
            "push_ok": state.get("last_push_ok"),
            "push_at": state.get("last_push_time"),
        },
        "manual_run": {
            "enabled": enabled,
            "method": "POST",
            "endpoint": endpoint,
        },
        # APScheduler 未启用隐式自动重试；失败任务由同一手动端点明确重试。
        "retry": {
            "mode": "manual",
            "eligible": enabled and status in {"error", "warn", "interrupted"},
            "endpoint": endpoint,
        },
    }


def jobs_status(scheduler: AsyncIOScheduler, config, store=None) -> list[dict]:
    times = config.schedule
    states = store.get_job_states() if store is not None else {}
    out = []
    known: set[str] = set()
    for job_id, label in JOB_NAMES.items():
        known.add(job_id)
        job = scheduler.get_job(job_id)
        next_dt = getattr(job, "next_run_time", None) if job else None
        next_run = next_dt.strftime("%Y-%m-%d %H:%M:%S") if next_dt else None
        configured_time = times.get(job_id)
        out.append(_status_row(
            job_id,
            f"{label} {configured_time or ''}".strip(),
            kind="core",
            enabled=job is not None,
            next_run=next_run,
            state=states.get(job_id, {}),
            configured_time=configured_time,
        ))
    # 客户扩展与哨兵/资金追踪也是真实运行任务，不能只在日志里存在。
    for job in sorted(scheduler.get_jobs(), key=lambda item: str(getattr(item, "id", ""))):
        job_id = str(getattr(job, "id", ""))
        if not job_id or job_id in known:
            continue
        next_dt = getattr(job, "next_run_time", None)
        out.append(_status_row(
            job_id,
            str(getattr(job, "name", None) or job_id),
            kind="extension" if job_id.startswith("ext_") else "system",
            enabled=True,
            next_run=next_dt.strftime("%Y-%m-%d %H:%M:%S") if next_dt else None,
            state=states.get(job_id, {}),
        ))
    return out


def task_center_status(scheduler: AsyncIOScheduler, config, store=None) -> dict:
    """统一任务中心契约：内置、客户扩展与系统任务同一列表。"""
    rows = jobs_status(scheduler, config, store)
    by_kind = {"core": 0, "extension": 0, "system": 0}
    for row in rows:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    return {
        "schema_version": "task-center-v1",
        "generated_at": now_cn().isoformat(timespec="seconds"),
        "summary": {
            "total": len(rows),
            "enabled": sum(1 for row in rows if row["enabled"]),
            "running": sum(1 for row in rows if row["last_status"] == "running"),
            "needs_attention": sum(
                1 for row in rows if row["last_status"] in {"error", "warn", "interrupted"}
            ),
            "by_kind": by_kind,
        },
        "capabilities": {
            "manual_run": True,
            "manual_retry": True,
            "automatic_retry": False,
        },
        "jobs": rows,
    }
