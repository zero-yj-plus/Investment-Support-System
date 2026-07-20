"""运行哨兵 —— 数据/推送两条军规的总闸（确定性自检 + 告警 + 日终对账补推）。

定位：此前每次数据/推送事故都是【人先发现】（客户截图→运营转达→排查）。哨兵把顺序反过来：
每天定点（默认 09:35 开盘后、22:05 归档后）跑一组**确定性断言**，全过→静默；任何失败→
push_card 告警到网关家频道（当前部署=运营者飞书私聊，不打扰客户）。22:05 班次顺带对账当日
`pushed` 标记——生成了却没推出去的报告，**自动补推一次**并 mark_pushed（推送闭环的最后一道网）。

铁律姿态：哨兵只【读+断言+补推已定稿报告】，绝不重新生成内容、绝不改数据。
config `sentinel:`（enabled/times/push_alerts）门控，off-by-default——未配置客户零影响。"""
from __future__ import annotations

import asyncio
import logging
import shutil

from ..agent.prompts import REPORT_META
from ..delivery import push_report_with_receipt
from ..data import market
from ..data.base import is_trade_day, now_cn

log = logging.getLogger("hermes.sentinel")

# 日报完整集（与调度节点一致；trend 与 evening 同节点生成）
_DAILY_RTYPES = ("morning", "midday", "tail", "close", "evening", "trend")
_PUSH_RTYPES = {"morning", "tail", "close", "evening", "trend"}


async def run(engine, when: str) -> dict:
    """执行一班哨兵。when="HH:MM"（决定检查深度：>=15:00 才查报告齐/推送齐/快照）。
    返回 {when,date,trade_day,fails,notes,repushed}；异常单项自吞、绝不抛。"""
    cfg = engine.config.section("sentinel")
    today = now_cn().strftime("%Y-%m-%d")
    trade = await asyncio.to_thread(is_trade_day)
    fails: list[str] = []
    notes: list[str] = []
    repushed: list[str] = []

    # ── ① 数据新鲜度（交易日核心断言）─────────────────────────────
    try:
        ov = await asyncio.to_thread(market.market_overview)
        if not ov.get("ok"):
            fails.append("市场总览 ok=false")
        if trade and ov.get("data_date") != today:
            fails.append(f"数据归属日 {ov.get('data_date')} ≠ 今日 {today}（疑数据源整体卡死）")
        if not ov.get("indexes"):
            fails.append("指数列表为空")
        if trade and when >= "09:35" and ov.get("limit_up") is None:
            # 开盘初(09:35)数据源可能仍在预热/竞争，limit_up 短暂为空属良性，降为【提示】而非
            # ⚠️告警——避免云端刚上线时冷启动误报吓运营。真·源整体卡死由上面 data_date≠今日
            # 的硬断言(fails)兜底，不会漏。
            notes.append("涨停家数暂为空（开盘初数据源或仍在预热；若持续为空需查腾讯扫描/乐咕/公开涨停池）")
    except Exception as e:  # noqa: BLE001
        fails.append(f"市场总览采集异常: {str(e)[:100]}")

    # ── ② 定时任务健康（jobs_state 里有 error 态即告警）───────────
    try:
        states = engine.store.get_job_states()
        errs = [k for k, v in (states or {}).items() if v.get("last_status") == "error"]
        if errs:
            fails.append("任务错误态: " + ", ".join(sorted(errs)[:6]))
    except Exception:  # noqa: BLE001
        pass

    # ── ③ 晚班：报告齐 + 推送齐（缺推自动补推）+ 自证快照 + 质检汇总 ──
    if trade and when >= "15:00":
        for rt in _DAILY_RTYPES:
            rec = engine.store.get_report(rt, today)
            if not rec:
                if when >= "22:00":
                    fails.append(f"报告缺失: {rt}")
                continue
            if rt in _PUSH_RTYPES and not rec.get("pushed") and engine.config.push_enabled and engine.feishu:
                validation = rec.get("claim_validation") or {}
                if validation.get("status") != "passed" or not validation.get("ok"):
                    fails.append(f"补推已阻止: {rt} 未通过交易价格定稿校验")
                    continue
                ok = False
                ok = await push_report_with_receipt(
                    store=engine.store,
                    pusher=engine.feishu,
                    meta=REPORT_META.get(rt, {"title": rt}),
                    rec=rec,
                    rtype=rt,
                    trigger="sentinel_retry",
                    config=engine.config,
                )
                if ok:
                    engine.store.mark_pushed(rt, today)
                    repushed.append(rt)
                else:
                    fails.append(f"补推失败: {rt}")
        if engine.store.holdings() and when >= "22:00":
            if not engine.store.holdings_advice(today):
                fails.append("今日持仓自证快照缺失（收盘复盘未落库？）")
        sus = []
        for rt in _DAILY_RTYPES:
            rec = engine.store.get_report(rt, today) or {}
            n = (rec.get("eval") or {}).get("suspect_count") or 0
            if n:
                sus.append(f"{rt}×{n}")
        if sus:
            notes.append("研报数字质检可疑（仅参考，人工复核）: " + "、".join(sus))

    # ── ④ 磁盘余量 ───────────────────────────────────────────────
    try:
        free = shutil.disk_usage(str(engine.store.root)).free
        if free < 2 * 1024 ** 3:
            fails.append(f"磁盘剩余不足 2G（{free / 1024 ** 3:.1f}G）")
    except Exception:  # noqa: BLE001
        pass

    if repushed:
        notes.append("已自动补推: " + "、".join(repushed))

    result = {"when": when, "date": today, "trade_day": trade,
              "fails": fails, "notes": notes, "repushed": repushed}
    log.info("哨兵[%s]: %s", when, "全过·静默" if not (fails or notes) else f"异常{len(fails)} 提示{len(notes)}")

    # 告警推送：有异常/提示才推（全过静默，不制造噪音）；推送本身失败只记日志
    if (fails or notes) and cfg.get("push_alerts", True) and engine.feishu:
        title = ("⚠️ 运行哨兵告警" if fails else "ℹ️ 运行哨兵提示") + f" · {today} {when}"
        body = "\n".join([f"✗ {x}" for x in fails] + [f"· {x}" for x in notes])
        try:
            await engine.feishu.push_card(title, body + "\n\n（系统自检消息，面向运营者，非客户内容）")
        except Exception:  # noqa: BLE001
            log.warning("哨兵告警推送失败（异常清单已在日志）")
    return result
