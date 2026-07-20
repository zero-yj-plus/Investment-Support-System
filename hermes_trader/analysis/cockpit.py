"""今日交易驾驶舱 · 顶部总指令引擎（母文档 §6.2/§6.3/§10.4）。

铁律：本模块只做**确定性规则映射**——把真实的市场情绪分(0-100)与大盘宽度/涨跌停
转成「风险等级 / 仓位上限 / 允许·禁止动作 / 今日第一原则」。所有文案是母文档 §6.3
给定的固定话术，数字全部来自 sentiment / market_overview 的真实字段，**绝不生成数字、
绝不让 AI 拍脑袋定级**。取不到情绪分则返回中性 3 级并在 missing 标注，不假装能判断。

5 级风险（§6.3）：1 正常参与 / 2 谨慎参与 / 3 防守观察 / 4 减仓避险 / 5 极端风险·黑天鹅。
情绪分→基础等级（§10.4），再叠加黑天鹅触发（指数破位 / 跌停潮 / 情绪恶化）向上修正。
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime

from ..data import market
from ..data.base import is_trade_day, now_cn
from ..product_state import market_session
from ..analysis import sentiment as senti_mod

log = logging.getLogger("hermes.cockpit")

# 每级固定文案（母文档 §6.3 原文）。结构：名称 / 首页文案 / 仓位上限 / 允许 / 禁止 / 第一原则。
_LEVELS = {
    1: {
        "name": "正常参与",
        "text": "市场正常，可按计划参与。优先主线核心，仍需按价格条件执行。",
        "position_cap": "总仓位可至 70%",
        "max_position_pct": 70,
        "allow": ["按计划交易", "优先主线核心", "观察池满足价格条件可买"],
        "forbid": ["不追无逻辑后排"],
        "first_principle": "按计划执行，不情绪化追涨。",
        "action": "可按计划交易，优先主线核心，观察池满足条件可买。",
    },
    2: {
        "name": "谨慎参与",
        "text": "市场可参与但分歧加大。只做核心，不追后排，观察池未到价格不提前买。",
        "position_cap": "总仓位不超过 50%，新开仓 1—2 成",
        "max_position_pct": 50,
        "allow": ["轻仓", "只做主线核心", "观察池到价格条件再买"],
        "forbid": ["不追后排", "观察池未到价不提前买", "不追涨幅过大放量滞涨的票"],
        "first_principle": "少而准，宁可错过，不做错。",
        "action": "轻仓只做核心，不追后排，观察池未到价不买。",
    },
    3: {
        "name": "防守观察",
        "text": "进入防守观察。保持持仓，不主动加仓。观察池即使接近买点，也必须等资金确认。",
        "position_cap": "总仓位不超过 40%，不主动加仓",
        "max_position_pct": 40,
        "allow": ["保持持仓", "观察池只看不买", "等资金确认"],
        "forbid": ["不主动加仓", "不抄底", "观察池接近买点也先等"],
        "first_principle": "等资金确认再动，不猜底。",
        "action": "持仓为主，不主动加仓，观察池只看不买、等资金确认。",
    },
    4: {
        "name": "减仓避险",
        "text": "进入减仓避险。今天重点不是赚钱，而是减少亏损扩大。弱势持仓优先处理，观察池暂停买入。",
        "position_cap": "总仓位不超过 20%，弱势持仓优先减",
        "max_position_pct": 20,
        "allow": ["弱势持仓减仓", "降低高风险票仓位", "保留核心仓位观察"],
        "forbid": ["不补仓", "暂停观察池买入", "不追反弹"],
        "first_principle": "减亏比盈利更重要。",
        "action": "弱势持仓优先减仓，暂停观察池买入，不补仓。",
    },
    5: {
        "name": "极端风险",
        "text": "极端风险。当前不适合新开仓，不适合补仓，不追反弹。优先降低风险，必要时清仓或空仓。减亏比盈利更重要。",
        "position_cap": "总仓位 0—10%，清理高风险持仓，必要时空仓",
        "max_position_pct": 10,
        "allow": ["清理高风险持仓", "必要时空仓"],
        "forbid": ["不买入", "不补仓", "不追反弹"],
        "first_principle": "减亏比盈利更重要，保住本金第一。",
        "action": "清理高风险持仓，不买不补不追反弹，必要时空仓。",
    },
}


def _num(*vals):
    """返回第一个有效数字，全无则 None。"""
    for v in vals:
        if isinstance(v, (int, float)) and v == v:  # 排除 NaN
            return v
    return None


def _base_level(score) -> int:
    """情绪分→基础风险等级（母文档 §10.4 + §6.3）。

    86+ 过热警惕兑现→2；71-85 强势进攻但禁追后排→1；56-70 谨慎参与→2；
    46-55 中性防守→3；31-45 偏弱→4；0-30 冰点→4（默认防守，黑天鹅再上修到 5）。
    """
    if score is None:
        return 3
    if score >= 86:
        return 2
    if score >= 71:
        return 1
    if score >= 56:
        return 2
    if score >= 46:
        return 3
    if score >= 31:
        return 4
    return 4


# 市场状态(regime) → 风险等级下限（白皮书§5「状态切换」的确定性落地）。
# 同样的情绪分，在弱势/危机市里也要更早转防守——减亏优先，不给 AI 判、全用真实字段。
_REGIME_FLOOR = {"危机避险": 4, "弱势退潮": 3, "震荡分歧": 0, "强势多头": 0}


def _market_regime(score, phase, worst, avg_chg, up_cnt, down_cnt,
                   limit_up, limit_down) -> str:
    """确定性四态：强势多头 / 震荡分歧 / 弱势退潮 / 危机避险（对齐白皮书§5 牛/震/熊/危）。

    判据全部来自真实字段（情绪分 + 相位 + 全市场宽度 + 指数当日方向 + 涨跌停结构）；
    任一缺失只是少一个判据，全无信号则回退『震荡分歧』（中性、不加码）。绝不让 AI 参与。
    """
    # ① 危机避险：系统性风险——指数深跌 / 跌停潮远超涨停 / 情绪冰点崩塌。
    if (worst is not None and worst <= -3.0) \
            or (limit_down is not None and limit_up is not None
                and limit_down >= 40 and limit_down > limit_up * 2) \
            or (score is not None and score <= 12):
        return "危机避险"
    # 相位用子串匹配：sentiment 的 phase 带"期"后缀（冰点期/退潮期/发酵期/高潮期…），
    # 用 in 而非精确相等，对后缀/命名微调都鲁棒。
    ph = phase or ""
    # ② 强势多头：情绪高档 + 发酵/高潮相位 + (普涨 或 指数上行)。
    breadth_up = up_cnt is not None and down_cnt is not None and up_cnt > down_cnt * 1.5
    idx_up = avg_chg is not None and avg_chg > 0
    if score is not None and score >= 66 and ("发酵" in ph or "高潮" in ph) and (breadth_up or idx_up):
        return "强势多头"
    # ③ 弱势退潮：情绪低档 或 退潮/冰点相位 或 普跌。
    breadth_dn = up_cnt is not None and down_cnt is not None and down_cnt > up_cnt * 1.5
    if (score is not None and score <= 42) or "退潮" in ph or "冰点" in ph or breadth_dn:
        return "弱势退潮"
    # ④ 其余：震荡分歧。
    return "震荡分歧"


def risk_command(senti: dict | None, overview: dict | None) -> dict:
    """把真实情绪分 + 大盘数据 → 顶部总指令。纯函数、确定性、可单测。"""
    senti = senti or {}
    overview = overview or {}
    m = senti.get("metrics") or {}
    missing: list[str] = []

    score = _num(senti.get("score"))
    if score is None:
        missing.append("市场情绪分缺失，风险等级暂按中性 3 级，仅供参考")

    limit_up = _num(overview.get("limit_up"), m.get("limit_up"))
    limit_down = _num(overview.get("limit_down"), m.get("limit_down"))
    zhaban = _num(overview.get("zhaban_rate"), m.get("zhaban_rate"))
    indexes = overview.get("indexes") or []
    idx_chg = [_num(i.get("change_pct")) for i in indexes]
    idx_chg = [c for c in idx_chg if c is not None]
    worst = min(idx_chg) if idx_chg else None

    base = _base_level(score)

    # 黑天鹅 / 系统性风险叠加（§11）——全部基于真实数据，命中才上修，并记录理由。
    triggers: list[str] = []
    bump = 0
    if worst is not None and worst <= -2.0:
        triggers.append(f"主要指数跌幅达 {worst:.2f}%（指数破位）")
        bump += 1
    if limit_down is not None and limit_up is not None and limit_down > limit_up and limit_down >= 40:
        triggers.append(f"跌停 {int(limit_down)} 家 > 涨停 {int(limit_up)} 家（亏钱效应扩散）")
        bump += 1
    if zhaban is not None and zhaban > 55 and (limit_up or 0) < 20:
        triggers.append(f"炸板率 {zhaban:.0f}% 偏高且涨停稀少（情绪恶化）")
        bump += 1

    level = min(5, base + min(bump, 2))

    # 市场状态(regime) → 给风险等级加"状态下限"（白皮书§5 状态切换的确定性落地）：
    # 弱势退潮 / 危机避险 市里，即便情绪分不低也更早转防守（减亏优先）。全用真实字段，不让 AI 判。
    avg_chg = round(sum(idx_chg) / len(idx_chg), 2) if idx_chg else None
    up_cnt = _num(overview.get("up_count"), m.get("up_count"))
    down_cnt = _num(overview.get("down_count"), m.get("down_count"))
    regime = _market_regime(score, senti.get("phase"), worst, avg_chg,
                            up_cnt, down_cnt, limit_up, limit_down)
    level = max(level, _REGIME_FLOOR.get(regime, 0))
    spec = _LEVELS[level]

    # 今日风险描述：黑天鹅触发 > 防御性状态说明 > 情绪相位。
    if triggers:
        today_risk = "；".join(triggers)
    elif regime in ("弱势退潮", "危机避险"):
        today_risk = f"市场处于「{regime}」状态" + (
            f"：{senti.get('phase_desc')}" if senti.get("phase_desc") else "，反弹易成陷阱，防守优先")
    else:
        today_risk = senti.get("phase_desc") or (senti.get("phase") and f"当前处于{senti['phase']}") or "暂无显著系统性风险信号"

    # 过热(score>=86)额外强调兑现风险
    forbid = list(spec["forbid"])
    if score is not None and score >= 86 and "警惕高位兑现，不追高" not in forbid:
        forbid = ["警惕高位兑现，不追高"] + forbid

    for k in ("limit_up", "limit_down", "zhaban_rate"):
        if _num(overview.get(k), m.get(k)) is None:
            missing.append(f"{k} 缺失，黑天鹅判定可能不完整")

    return {
        "risk_level": level,
        "level_name": spec["name"],
        "level_text": spec["text"],
        "emotion_score": score,
        "regime": regime,
        "phase": senti.get("phase"),
        "trade_state": f"{regime}｜{_trade_state(senti, limit_up, limit_down)}",
        "position_cap": spec["position_cap"],
        "max_position_pct": spec["max_position_pct"],
        "allow": spec["allow"],
        "forbid": forbid,
        "first_principle": spec["first_principle"],
        "today_action": spec["action"],
        "today_risk": today_risk,
        "blackswan": triggers,
        "missing": missing,
    }


def apply_decision_time_guard(
    command: dict | None,
    overview: dict | None,
    *,
    at: datetime | None = None,
    trade_day_checker=None,
) -> dict:
    """把「市场结构参考」与「此刻可执行交易指令」分开。

    情绪/风险等级可以延用最近交易日收盘数据作盘前基线，但盘前、
    休市或数据归属日与当日不一致时，绝不得返回「可买」「可按计划交易」。
    这是确定性时态门禁，不修改原始风险评分，只修改它的执行语义。
    """
    out = dict(command or {})
    # 定格快照会跨时段/跨日复用。先保存未加时态门禁时的确定性结论，
    # 这样盘前参考态进入盘中后可恢复原动作，也不会重复套出
    # “盘前待确认｜盘前待确认｜...”或沿用错误的基线日期。
    original_action = str(out.get("reference_action") or out.get("today_action") or "").strip()
    raw_state = str(out.get("reference_trade_state") or out.get("trade_state") or out.get("regime") or "市场状态待确认")
    raw_state = re.sub(r"^(?:盘前待确认|休市|已收盘|暂停新指令)｜", "", raw_state)
    raw_state = re.sub(r"（\d{4}-\d{2}-\d{2} 收盘基线）$", "", raw_state)
    original_position = str(out.get("reference_position_cap") or out.get("position_cap") or "").strip()
    original_principle = str(out.get("reference_first_principle") or out.get("first_principle") or "").strip()
    generated_forbid = {"不把昨日收盘结论当作今日买入指令", "不依据跨日数据新开仓或加仓"}
    original_forbid = list(out.get("reference_forbid") or [
        item for item in (out.get("forbid") or []) if item not in generated_forbid
    ])
    original_allow = list(out.get("reference_allow") or out.get("allow") or [])
    out.update({
        "reference_action": original_action or None,
        "reference_trade_state": raw_state,
        "reference_position_cap": original_position or None,
        "reference_first_principle": original_principle or None,
        "reference_allow": original_allow,
        "reference_forbid": original_forbid,
    })
    current = at or now_cn()
    checker = trade_day_checker or is_trade_day
    trading_day = bool(checker(current.date()))
    session = market_session(current, trading_day=trading_day)
    data_date = str((overview or {}).get("data_date") or "")[:10]
    current_day = current.date().isoformat()
    aligned = bool(data_date and data_date == current_day)
    executable = aligned and session in {"intraday", "tail"}
    out.update({
        "decision_tradeable": executable,
        "decision_state": "executable" if executable else "reference_only",
        "market_session": session,
        "baseline_data_date": data_date or None,
    })
    if executable:
        if original_action:
            out["today_action"] = original_action
        out["trade_state"] = raw_state
        if original_position:
            out["position_cap"] = original_position
        if original_principle:
            out["first_principle"] = original_principle
        out["allow"] = original_allow
        out["forbid"] = original_forbid
        out.pop("action_label", None)
        return out

    # 上一档 AI 叙述可能已随持久快照落盘，它的「定调」不包含时态门禁。
    # 进入参考态时必须让确定性 trade_state 置顶，避免旧叙述盖掉「盘前待确认」。
    out.pop("定调叙述", None)

    if session == "premarket":
        baseline = f"{data_date} 收盘基线" if data_date else "最近收盘基线"
        out.update({
            "decision_state": "premarket_pending",
            "action_label": "盘前待确认",
            "trade_state": f"盘前待确认｜{raw_state}（{baseline}）",
            "today_action": "盘前仅做准备：等待当日盘前报告与 09:25 集合竞价确认，不执行买卖。",
            "position_cap": "最近风险上限仅作参考，今日开仓额度待确认",
            "first_principle": "先确认今日数据与集合竞价，不用昨日结论直接下单。",
            "allow": ["查看收盘基线", "准备观察池条件", "等待集合竞价确认"],
            "forbid": ["不把昨日收盘结论当作今日买入指令", *original_forbid],
        })
    elif session == "non_trading":
        out.update({
            "decision_state": "market_closed",
            "action_label": "休市参考",
            "trade_state": f"休市｜{raw_state}",
            "today_action": "当前休市，本页只供复盘与下一交易日准备，不生成可执行买卖指令。",
            "position_cap": "休市期间不计算当日开仓额度",
            "first_principle": "复盘与准备分开，下一交易日再确认。",
        })
    elif session == "review":
        out.update({
            "decision_state": "post_close_review",
            "action_label": "已收盘",
            "trade_state": f"已收盘｜{raw_state}",
            "today_action": "当日交易已结束，只做复盘和次日预案，不再显示可买指令。",
            "position_cap": "收盘后仅复盘仓位，次日上限待新报告确认",
            "first_principle": "收盘结论不直接冒充次日交易指令。",
        })
    else:
        out.update({
            "decision_state": "stale_data_blocked",
            "action_label": "数据日期未对齐",
            "trade_state": f"暂停新指令｜{raw_state}",
            "today_action": "行情数据归属日未与当日对齐，暂停新开仓与加仓。",
            "position_cap": "数据日期未对齐，不计算今日新开仓上限",
            "first_principle": "先恢复当日行情与风险数据，再生成可执行动作。",
            "allow": ["仅做风险收缩与数据核验"],
            "forbid": ["不依据跨日数据新开仓或加仓", *original_forbid],
        })
    return out


# ---- T9 §2 情绪周期（纯公式温度计）+ §0-4 唯一真值源 pulse ----
_SENTI_LABELS = [(75, "亢奋"), (58, "偏强"), (42, "中性"), (25, "偏弱")]


def _senti_label(v: float) -> str:
    for thr, name in _SENTI_LABELS:
        if v >= thr:
            return name
    return "冰点"


def pulse_core(store=None, prev: dict | None = None) -> dict:
    """唯一真值源核心（T9 §0-3/§0-4/§2，经 score_lock 按 10 分钟档定格）：
    · 情绪温度 = 纯公式 clamp(0,100, ratio*60 + ztdt*40)，秒级可算、绝对可复现、不漂移；
    · 风险等级/regime 复用确定性 risk_command（绝不 AI 拍脑袋定级）；
    · 等级阻尼（T9 §3）：本档等级相对【上一档 prev】最多 ±1；跳级仅当极端信号（黑天鹅触发），
      被阻尼时按新等级重查 §6.3 固定话术重建 command，阻尼原因写进 change；
    · 观点惯性（T9 §0-3/§1）：与 prev 比风险/regime/情绪档位，变了 → change 写明
      "较上一版 XX→YY + 依据读数"；无实质变化 change 空（前端 🔁 徽章据此显隐）。
    全站顶栏温度/情绪卡/行动条风险格/系统性风险/报告快照只从此取，杜绝"同屏两套数"。
    缺因子标 missing、绝不编。prev 由 score_lock 传入（当日上一档 → 昨日末档 → None）。"""
    ov = market.market_overview()
    missing: list[str] = []
    up, down = ov.get("up_count"), ov.get("down_count")
    lu, ld = ov.get("limit_up"), ov.get("limit_down")
    senti = senti_label = None
    if (up is not None and down is not None and (up + down) > 0
            and lu is not None and ld is not None and (lu + ld) > 0):
        ratio = up / (up + down)
        ztdt = lu / (lu + ld)
        senti = round(max(0.0, min(100.0, ratio * 60 + ztdt * 40)))
        senti_label = _senti_label(senti)
    else:
        missing.append("情绪温度（涨跌家数或涨跌停字段本次未返回）")
    se = None
    try:
        se = senti_mod.compute_today(store)
    except Exception:  # noqa: BLE001 —— 情绪分失败不拖垮 pulse，风险段降级标 missing
        se = None
    rc = dict(risk_command(se, ov)) if se else {}
    for m in (rc.get("missing") or []):
        if m not in missing:
            missing.append(m)

    # ---- 等级阻尼（T9 §3）：相对上一档最多 ±1；黑天鹅触发=极端信号允许直跳 ----
    raw_level = rc.get("risk_level")
    prev_level = (prev or {}).get("risk_level")
    level = raw_level
    damp_note = ""
    extreme = bool(rc.get("blackswan"))
    if (raw_level is not None and prev_level is not None
            and abs(raw_level - prev_level) > 1 and not extreme):
        level = prev_level + (1 if raw_level > prev_level else -1)
        damp_note = f"原始评级{raw_level}级，无极端信号，按阻尼相对上一档({prev_level}级)限±1"
        spec = _LEVELS[level]
        # 按阻尼后等级重建 §6.3 固定话术；保留 risk_command 的动态附加项（2026-07-11 审计 P2：
        # 原重建会丢 score≥86 前插的"警惕高位兑现"）——与 risk_command:190 同判据补回。
        forbid = list(spec["forbid"])
        score = _num((se or {}).get("score"))   # 与 risk_command:163 同判据（审计反查：防非数值 TypeError）
        if score is not None and score >= 86 and "警惕高位兑现，不追高" not in forbid:
            forbid = ["警惕高位兑现，不追高"] + forbid
        rc.update({
            "risk_level": level, "level_name": spec["name"], "level_text": spec["text"],
            "position_cap": spec["position_cap"], "max_position_pct": spec["max_position_pct"],
            "allow": list(spec["allow"]),
            "forbid": forbid, "first_principle": spec["first_principle"],
            "today_action": spec["action"],
        })

    # ---- 观点惯性（T9 §0-3/§1）：与上一档比，变了才写 change（引用真实读数），没变留空 ----
    changes: list[str] = []
    if prev:
        if prev.get("risk_level") is not None and level is not None and prev.get("risk_level") != level:
            changes.append(f"风险 {prev['risk_level']}级→{level}级")
        if prev.get("regime") and rc.get("regime") and prev.get("regime") != rc.get("regime"):
            changes.append(f"状态 {prev['regime']}→{rc['regime']}")
        if (prev.get("senti_label") and senti_label and prev.get("senti_label") != senti_label):
            changes.append(f"情绪 {prev['senti_label']}({prev.get('senti')})→{senti_label}({senti})")
    change = ""
    if changes:
        basis: list[str] = []
        if up is not None and down is not None:
            basis.append(f"上涨{up}家/下跌{down}家")
        if lu is not None and ld is not None:
            basis.append(f"涨停{lu}/跌停{ld}(含ST)")
        change = ("较上一版：" + "、".join(changes)
                  + (f"，依据 {'、'.join(basis)}" if basis else "")
                  + (f"（{damp_note}）" if damp_note else ""))
    if change:
        rc["change"] = change   # 挪进 command，前端五格直接读 cmd.change

    rc = apply_decision_time_guard(rc, ov)

    return {
        "ok": True,
        "trade_date": ov.get("data_date"),
        "ts": ov.get("ts"),
        "senti": senti,
        "senti_label": senti_label,
        "risk_level": level,
        "raw_level": raw_level,
        "risk_label": rc.get("level_name"),
        "regime": rc.get("regime"),
        "change": change,
        "command": rc or None,
        "decision_state": rc.get("decision_state"),
        "decision_tradeable": bool(rc.get("decision_tradeable")),
        "market_session": rc.get("market_session"),
        "missing": missing,
    }


def _signpct(v):
    return "—" if v is None else f"{v:+.2f}%"


def _yi(v):
    return "—" if v is None else f"{v / 1e8:+.1f}亿"


# T9 §3 triggers —— 「什么情况下要更紧张」的升级触发条件（确定性固定表，按当前等级给下一级观察条件）。
_UPGRADE_TRIGGERS = {
    1: ["主要指数单日跌超 2%", "跌停家数明显放大、逼近涨停家数", "主力资金转大幅净流出"],
    2: ["主要指数跌破关键位或单日跌超 2%", "跌停多于涨停（亏钱效应占优）", "情绪温度跌入偏弱区（<42）"],
    3: ["主要指数单日跌超 3%（指数破位）", "跌停潮（跌停远超涨停）", "主力大幅净流出且无回流迹象"],
    4: ["指数加速下破、连续大阴", "涨停家数枯竭（<10）且跌停扩散", "重大利空事件落地"],
    5: [],
}

# 「什么情况下才具备降级基础」。这些是观察条件，不是单因子触发后立即降级；
# 真实等级仍由 risk_command 重算，并经 pulse_core 按10分钟档最多下调1级。
_DOWNGRADE_TRIGGERS = {
    1: ["已处最低风险档：继续按计划执行，仍保留价格和止损约束"],
    2: [
        "综合情绪评分回到强势区（71—85）且未进入过热区",
        "主要指数稳定且无破位信号",
        "跌停未扩散、主力资金未转大幅净流出",
    ],
    3: [
        "综合情绪评分回到可参与区（≥56）",
        "上涨家数修复且跌停显著收缩",
        "资金面不再为高风险贡献、出现回流",
    ],
    4: [
        "综合情绪评分脱离偏弱区（≥46）",
        "主要指数止跌，最弱指数跌幅收窄至 2% 以内",
        "跌停不再多于涨停，主力资金流出明显收窄",
    ],
    5: [
        "指数停止加速下跌，最弱指数跌幅收窄至 3% 以内",
        "跌停潮显著收缩，不再远超涨停家数",
        "极端触发项解除，综合情绪评分回升至 12 以上",
    ],
}


def risk_level_guide() -> list[dict]:
    """将内部五级风险规则转成可展示的稳定协议，避免前端另维护一套文案。"""
    return [
        {
            "level": level,
            "name": spec["name"],
            "text": spec["text"],
            "position_cap": spec["position_cap"],
            "max_position_pct": spec["max_position_pct"],
            "allow": list(spec["allow"]),
            "forbid": list(spec["forbid"]),
            "first_principle": spec["first_principle"],
            "action": spec["action"],
        }
        for level, spec in sorted(_LEVELS.items())
    ]


def sysrisk(store=None, pulse_snap: dict | None = None) -> dict:
    """系统性风险五维（T9 §3）：大盘技术/资金面/情绪面/消息面/海外，每维一行真实读数 + 一句话结论 + 贡献(低/中性/高)。
    等级/regime/情绪/change 优先取【pulse 档快照】（唯一真值源+阻尼+惯性后的定格值，与全站同源同值）；
    无快照时回退自身 risk_command（健壮性，语义同旧版）。升/降级观察为确定性固定表。
    读数全来自真实数据、缺则标缺、绝不编。"""
    ov = market.market_overview()
    se = None
    try:
        se = senti_mod.compute_today(store)
    except Exception:  # noqa: BLE001
        se = None
    rc = (pulse_snap or {}).get("command") or (risk_command(se, ov) if se else {})
    dims: list[dict] = []
    missing: list[str] = []

    # 1 大盘技术：上证/创业板 当日涨跌 + 破位
    idx = {i.get("code"): i for i in (ov.get("indexes") or [])}
    sh = (idx.get("sh000001") or {}).get("change_pct")
    cy = (idx.get("sz399006") or {}).get("change_pct")
    cand = [c for c in (sh, cy) if c is not None]
    worst = min(cand) if cand else None
    if worst is None:
        dims.append({"dim": "大盘技术", "reading": "指数行情本次未取得", "contrib": "中性", "note": "技术面不参与本次风险定级，等待下次行情快照"})
        missing.append("大盘技术")
    else:
        c = "高" if worst <= -2 else ("中性" if worst < 0 else "低")
        dims.append({"dim": "大盘技术", "reading": f"上证{_signpct(sh)}·创业板{_signpct(cy)}", "contrib": c,
                     "note": "指数跌破关键位、技术走坏" if c == "高" else ("指数回落震荡、方向未明" if c == "中性" else "指数企稳偏强")})

    # 2 资金面：两市主力净额（北向未接入，如实标）
    try:
        fl = market.market_flow()
    except Exception:  # noqa: BLE001
        fl = None
    total = (fl or {}).get("main_flow_total")
    total_kind = str((fl or {}).get("main_flow_total_kind") or "")
    if total is None or total_kind != "market_total":
        if total is not None and total_kind == "industry_proxy":
            reading = f"行业资金样本净额 {_yi(total)}（非两市总额）"
            note = "行业资金代理值只作观察，不参与系统风险定级"
        else:
            reading = "主力净额本次未取得"
            note = "主力资金流字段本次未返回，不参与本次风险定级"
        dims.append({"dim": "资金面", "reading": reading, "contrib": "中性", "note": note})
        missing.append("资金面")
    else:
        c = "高" if total < -3e9 else ("中性" if total < 3e9 else "低")
        dims.append({"dim": "资金面", "reading": f"两市主力净额 {_yi(total)}（北向未接入）", "contrib": c,
                     "note": "主力大幅净流出、资金撤离" if c == "高" else ("主力小幅进出、资金观望" if c == "中性" else "主力净流入、资金进场")})

    # 3 情绪面：情绪温度 + 涨跌停（含ST）。温度优先取 pulse 档快照（同源同值）；无快照就地按同公式算（不另造口径）。
    p_senti = (pulse_snap or {}).get("senti")
    if p_senti is None:
        _u, _d = ov.get("up_count"), ov.get("down_count")
        _l, _dd = ov.get("limit_up"), ov.get("limit_down")
        if (_u is not None and _d is not None and (_u + _d) > 0
                and _l is not None and _dd is not None and (_l + _dd) > 0):
            p_senti = round(max(0.0, min(100.0, _u / (_u + _d) * 60 + _l / (_l + _dd) * 40)))
    lu, ld = ov.get("limit_up"), ov.get("limit_down")
    if p_senti is None:
        dims.append({"dim": "情绪面", "reading": "情绪温度本次未计算", "contrib": "中性", "note": "涨跌家数或涨跌停字段不足，不展示情绪结论"})
    else:
        extreme = (p_senti >= 85) or (lu is not None and ld is not None and ld > lu)
        c = "高" if extreme else ("中性" if p_senti < 42 else "低")
        dims.append({"dim": "情绪面", "reading": f"情绪温度 {p_senti} · 涨停{lu if lu is not None else '—'}/跌停{ld if ld is not None else '—'}(含ST)", "contrib": c,
                     "note": "情绪过热或亏钱效应扩散、易反转" if c == "高" else ("情绪偏弱、赚钱效应差" if c == "中性" else "情绪健康、赚钱效应可")})

    # 4 消息面：黑天鹅/风险触发（cockpit command 的新闻派生信号）
    bs = rc.get("blackswan") or []
    tr = rc.get("today_risk")
    if bs:
        dims.append({"dim": "消息面", "reading": "；".join(bs[:2]), "contrib": "高", "note": "存在黑天鹅/风险事件，需回避"})
    elif tr:
        dims.append({"dim": "消息面", "reading": str(tr)[:38], "contrib": "中性", "note": "有需留意的风险点"})
    else:
        dims.append({"dim": "消息面", "reading": "无显著黑天鹅信号", "contrib": "低", "note": "暂无重大利空事件"})

    # 5 海外：美股隔夜
    us = None
    try:
        from ..data import news as _news  # noqa: PLC0415
        nm = _news.multi_market_news()
        us = ((nm or {}).get("us") or {}).get("indexes")
    except Exception:  # noqa: BLE001
        us = None
    if us:
        vals = [x.get("change_pct") for x in us if x.get("change_pct") is not None]
        w = min(vals) if vals else None
        names = "、".join(f"{x.get('name')}{_signpct(x.get('change_pct'))}" for x in us[:2] if x.get("change_pct") is not None)
        c = "高" if (w is not None and w <= -2) else ("中性" if (w is not None and w < 0) else "低")
        dims.append({"dim": "海外", "reading": names or "隔夜美股行情本次未取得", "contrib": c,
                     "note": "隔夜美股大跌、外围施压" if c == "高" else ("外围小幅回落" if c == "中性" else "外围企稳/上涨")})
    else:
        dims.append({"dim": "海外", "reading": "美股隔夜数据待接入", "contrib": "中性", "note": "外围数据缺失"})
        missing.append("海外")

    # 升级/降级观察（T9 §3）：固定表按当前等级 + 点名当前贡献「高」的维度。
    # 降级条件只解释「什么信号值得复核」；实际级别仍由 pulse 唯一真值源逐档重算。
    level = rc.get("risk_level")
    upgrade_triggers = list(_UPGRADE_TRIGGERS.get(level, [])) if level is not None else []
    downgrade_triggers = list(_DOWNGRADE_TRIGGERS.get(level, [])) if level is not None else []
    hot = [x["dim"] for x in dims if x.get("contrib") == "高"]
    if hot and level is not None and level < 5:
        upgrade_triggers.append(f"当前已偏高的维度：{'、'.join(hot)}（若继续恶化即触发升级）")
    spec = _LEVELS.get(level, {})

    return {
        "ok": True, "ts": ov.get("ts"),
        "level": level, "level_name": rc.get("level_name"),
        "regime": rc.get("regime"),
        "risk_score": rc.get("emotion_score"),
        "summary": rc.get("today_risk") or rc.get("level_text"),
        "change": (pulse_snap or {}).get("change", ""),
        # 展示规则以当前代码中的级别规范为准；即使 pulse 是改版前的收盘快照，
        # 风险级别仍复用其唯一真值，但不继续展示旧版模糊仓位文案。
        "level_text": spec.get("text") or rc.get("level_text"),
        "position_cap": spec.get("position_cap") or rc.get("position_cap"),
        "max_position_pct": spec.get("max_position_pct", rc.get("max_position_pct")),
        "allow": list(rc.get("allow") or spec.get("allow") or []),
        "forbid": list(rc.get("forbid") or spec.get("forbid") or []),
        "first_principle": rc.get("first_principle") or spec.get("first_principle"),
        "today_action": rc.get("today_action") or spec.get("action"),
        "upgrade_triggers": upgrade_triggers,
        "downgrade_triggers": downgrade_triggers,
        "triggers": upgrade_triggers,  # 兼容旧前端；新版请使用语义明确的字段。
        "level_guide": risk_level_guide(),
        "review_rule": "按10分钟定格逐级复核；每档最多下调1级，极端信号可快速上调",
        "dims": dims, "missing": missing,
    }


def _trade_state(senti: dict, limit_up, limit_down) -> str:
    """一句话交易状态：相位 + 涨跌停结构的真实描述（不预测、不编数字）。"""
    phase = senti.get("phase") or "未知"
    if limit_up is not None and limit_down is not None:
        if limit_down > limit_up:
            return f"{phase} · 跌停多于涨停，亏钱效应占优"
        if limit_up >= 60:
            return f"{phase} · 涨停 {int(limit_up)} 家，赚钱效应活跃"
    return phase


# 持仓行业集中度（白皮书吸收⑤ 2026-07-05）：行业映射 30 分钟模块缓存——集中度是慢变量，
# 且 cockpit 被预热器高频调用，绝不能每次都请求公开 F10。
# 锁：cockpit 经 routes 的 asyncio.to_thread 与预热器并发进入（真多线程）。GIL 下元组交换
# 原子、不会损坏，但冷启会并发重复请求公开 F10（增加限流风险）——加锁根治（07-06 审计裁定）。
_IND_CACHE: tuple[float, dict] | None = None
_IND_LOCK = threading.Lock()
_IND_TTL = 1800
_CONC_LIMIT_PCT = 60.0   # 单一行业按持仓权重占比 ≥60% 且 ≥2 只 → 总指令「禁止」栏点名


def _holdings_concentration(store) -> dict | None:
    """持仓行业集中度硬提醒（确定性规则）。触发返回 {industry, share_pct, count, forbid_line}；
    无持仓/<2只/公开 F10 行业映射不可用/任何异常 → None，总指令与旧版完全一致（零影响）。
    权重口径：全部持仓都有 成本×数量 时按市值权重，否则按等权（不混口径）。"""
    global _IND_CACHE
    try:
        if store is None:
            return None
        holds = [h for h in (store.holdings() or []) if h.get("code")]
        if len(holds) < 2:
            return None
        import time as _t  # noqa: PLC0415
        with _IND_LOCK:
            now = _t.monotonic()
            if _IND_CACHE and now - _IND_CACHE[0] < _IND_TTL:
                imap = _IND_CACHE[1]
            else:
                # 行业不再依赖需 token 的全市场基础表。持仓通常只有少量股票，按持仓代码
                # 低频查询公开 F10 并缓存 30 分钟，避免为一条集中度提醒抓全市场行业表。
                from ..data import free_source  # noqa: PLC0415
                imap = {}
                for h in holds:
                    code = str(h.get("code") or "").zfill(6)
                    ind = free_source.industry(code)
                    if ind:
                        imap[code] = {"industry": ind}
                if not imap:
                    return None
                _IND_CACHE = (now, imap)

        weights = []
        for h in holds:
            try:
                w = float(h.get("cost") or 0) * float(h.get("shares") or 0)
            except (TypeError, ValueError):
                w = 0.0
            weights.append(w)
        if not all(w > 0 for w in weights):
            weights = [1.0] * len(holds)     # 任一缺成本 → 全体等权，不混口径

        by_ind: dict[str, list[float]] = {}
        total = 0.0
        for h, w in zip(holds, weights):
            ind = ((imap.get(str(h["code"]).zfill(6)) or {}).get("industry") or "").strip()
            if not ind:
                continue
            by_ind.setdefault(ind, []).append(w)
            total += w
        if total <= 0 or not by_ind:
            return None
        ind, ws = max(by_ind.items(), key=lambda kv: sum(kv[1]))
        share = sum(ws) / total * 100.0
        if share < _CONC_LIMIT_PCT or len(ws) < 2:
            return None
        return {
            "industry": ind, "share_pct": round(share, 1), "count": len(ws),
            "forbid_line": f"不加仓同行业（{ind} 已占持仓 {share:.0f}%·{len(ws)} 只，集中度超限）",
        }
    except Exception:  # noqa: BLE001 — 集中度提醒绝不影响总指令主流程
        log.debug("持仓集中度计算跳过", exc_info=True)
        return None


def cockpit(store=None, pulse_snap: dict | None = None) -> dict:
    """组装首页驾驶舱顶部总指令 + 今日总判断（卡片①）。

    只拉 sentiment + market_overview 两个已预热/缓存的快源，保证首屏秒出；
    板块主线(卡片②)由前端另取 /market/flow + /mainforce/industry，不在此处阻塞。
    评分定格：command 优先取【pulse 档快照】（阻尼+惯性后的定格指令，与 /pulse、/sysrisk、
    报告快照同源同值——唯一真值源）；无快照回退自算（健壮性，语义同旧版）。
    """
    senti = {}
    overview = {}
    try:
        senti = senti_mod.compute_today(store) or {}
    except Exception:
        log.exception("驾驶舱：情绪计算失败")
    try:
        overview = market.market_overview() or {}
    except Exception:
        log.exception("驾驶舱：大盘概览失败")

    _snap_cmd = (pulse_snap or {}).get("command")
    cmd = (
        dict(_snap_cmd)
        if _snap_cmd
        else risk_command(senti, overview)
    )
    # pulse 快照是风险评分的唯一真值，但“此刻能不能执行”必须始终用
    # 当前市场时段和本次 overview.data_date 重算，不能沿用旧快照日期。
    cmd = apply_decision_time_guard(cmd, overview)
    if _snap_cmd and pulse_snap.get("定格时点"):
        cmd["定格时点"] = pulse_snap["定格时点"]   # 前端"情绪温度·定格HH:MM"标注（诚实非实时）
    conc = _holdings_concentration(store)
    if conc:  # 集中度超限 → 「禁止」栏置顶点名 + 附结构化字段（前端未知键自动忽略）
        cmd["forbid"] = [conc["forbid_line"]] + list(cmd.get("forbid") or [])
        cmd["concentration"] = conc
    return {
        "ok": True,
        "date": senti.get("date"),
        # date 是情绪账本/业务日，data_date 才是本次行情证据的真实归属日。
        # 二者在盘前天然不同，API 时态门禁必须使用后者。
        "data_date": overview.get("data_date"),
        "command": cmd,
        # 卡片①今日总判断需要的真实字段（今日主线由前端用板块数据填）
        "overview": {
            "up_count": overview.get("up_count"),
            "down_count": overview.get("down_count"),
            "flat_count": overview.get("flat_count"),
            "limit_up": overview.get("limit_up"),
            "limit_down": overview.get("limit_down"),
            "lianban_height": overview.get("lianban_height"),
            "zhaban_rate": overview.get("zhaban_rate"),
            "total_amount": overview.get("total_amount"),
        },
        "missing": cmd.get("missing", []),
    }
