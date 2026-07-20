"""情绪周期量化引擎 — 规则透明的赚钱效应/亏钱效应评分。

评分构成（0-100，50 为中性）：
  + 涨停/跌停结构（涨停多、跌停少 → 加分）
  + 涨跌家数比
  + 连板高度（梯队空间）
  - 炸板率（分歧度）
  + 昨日涨停今日溢价（打板赚钱效应）
阶段映射参考 A 股短线情绪周期六段论，结合昨日评分趋势修正分歧/退潮判定。
"""
from __future__ import annotations

from ..data import market
from ..data.base import now_cn

PHASES = ["冰点期", "退潮期", "分歧期", "修复期", "发酵期", "高潮期"]

PHASE_DESC = {
    "冰点期": "跌停多于涨停、连板断档，亏钱效应极致，往往孕育新周期起点",
    "退潮期": "高位股补跌、炸板率高企，赚钱效应快速收缩，防守为主",
    "分歧期": "涨停结构尚可但晋级率下降，多空分歧加大，操作降频",
    "修复期": "涨停回暖、出现穿越龙头，赚钱效应开始修复，可小仓试错",
    "发酵期": "连板梯队完整、补涨扩散，主升阶段，持股做多为主",
    "高潮期": "连板高度与涨停数量双高，情绪一致加速，兑现思维不追高",
}

PHASE_ADVICE = {
    "冰点期": "空仓休息或仅以极小仓位试错卡位龙，等待转折信号",
    "退潮期": "轻仓防守，不接力高位股，回避补跌风险",
    "分歧期": "降低频率，只做最强方向的低吸，不追涨",
    "修复期": "小仓位试错最强主线，确认晋级后再加",
    "发酵期": "正常参与，优先主线龙头与低位补涨，可上仓位",
    "高潮期": "持有不追高，逢一致加速分批兑现，警惕物极必反",
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def compute_today(store=None) -> dict:
    """采集真实数据并计算今日情绪快照。"""
    overview = market.market_overview()
    date_compact = now_cn().strftime("%Y%m%d")
    premium = market.yesterday_limit_premium(date_compact)

    missing = list(overview.get("missing", []))
    limit_up = overview.get("limit_up")
    limit_down = overview.get("limit_down")
    up_count = overview.get("up_count")
    down_count = overview.get("down_count")
    height = overview.get("lianban_height")
    zhaban = overview.get("zhaban_rate")

    if limit_up is None or up_count is None:
        return {
            "ok": False,
            "date": now_cn().strftime("%Y-%m-%d"),
            "data_date": overview.get("data_date"),
            "score": None,
            "confidence": "none",
            "phase": "未知",
            "phase_desc": "核心数据不全，无法计算情绪评分",
            "metrics": {},
            "advice": "数据不全，建议人工确认盘面后再决策",
            "missing": missing or ["涨停结构"],
        }

    # 因子覆盖度：缺失的评分因子越多，结论可信度越低（缺失因子直接跳过其加减项，
    # 用残缺因子算出的分若仍按齐全口径给确定结论，会误导操作，必须降级标注）。
    factors_total = 5  # 跌停池 / 涨跌比 / 连板高度 / 炸板率 / 昨日溢价（涨停数为必备硬门槛，不计入可选）
    factors_missing = 0
    score = 50.0
    # 跌停池单独接口可失败(limit_down=None)；不能把 None 当 0 跌停计分——崩盘日(如80跌停)
    # 缺数据会把退潮/冰点误判为修复/发酵。缺失时退化为仅涨停的保守口径并收紧上限，且计入降级。
    if limit_down is None:
        factors_missing += 1
        score += _clamp(limit_up * 0.35, 0, 12)
    else:
        score += _clamp((limit_up - limit_down * 3) * 0.35, -20, 20)
    if down_count:
        ratio = up_count / max(down_count, 1)
        score += _clamp((ratio - 1) * 10, -15, 15)
    else:
        factors_missing += 1
    if height is not None:
        score += _clamp((height - 3) * 3.0, -9, 12)
    else:
        factors_missing += 1
    if zhaban is not None:
        score -= _clamp((zhaban - 20) * 0.45, -9, 18)
    else:
        factors_missing += 1
    if premium.get("ok") and premium.get("avg_pct") is not None:
        score += _clamp(premium["avg_pct"] * 2.0, -10, 10)
    else:
        factors_missing += 1
        missing.append("昨日涨停溢价")
    score = round(_clamp(score, 0, 100))
    confidence = "high" if factors_missing == 0 else ("medium" if factors_missing <= 2 else "low")

    # 基础阶段映射
    if score >= 80:
        phase = "高潮期"
    elif score >= 64:
        phase = "发酵期"
    elif score >= 50:
        phase = "修复期"
    elif score >= 38:
        phase = "分歧期"
    elif score >= 24:
        phase = "退潮期"
    else:
        phase = "冰点期"

    # 趋势修正：昨日高位、今日大幅回落 → 至少视为分歧/退潮
    if store is not None:
        history = store.sentiment_history(5)
        prev = next((h for h in reversed(history) if h.get("date") != now_cn().strftime("%Y-%m-%d")), None)
        if prev and isinstance(prev.get("score"), (int, float)):
            drop = prev["score"] - score
            if drop >= 15 and prev["score"] >= 60 and phase in ("修复期", "发酵期"):
                phase = "分歧期"

    advice = PHASE_ADVICE[phase]
    if confidence != "high":
        advice = (f"⚠️ 关键因子缺失 {factors_missing} 项，评分与阶段判断可信度{'低' if confidence == 'low' else '中等'}，"
                  f"仅供参考、务必人工确认盘面后再决策。{advice}")

    snapshot = {
        "ok": True,
        "date": now_cn().strftime("%Y-%m-%d"),
        "data_date": overview.get("data_date"),
        "score": score,
        "confidence": confidence,
        "phase": phase,
        "phase_desc": PHASE_DESC[phase],
        "metrics": {
            "limit_up": limit_up,
            "limit_down": limit_down,
            "lianban_height": height,
            "zhaban_rate": zhaban,
            "up_down_ratio": round(up_count / max(down_count, 1), 2) if down_count else None,
            "up_count": up_count,
            "down_count": down_count,
            "total_amount": overview.get("total_amount"),
            "limit_up_amount": overview.get("limit_up_amount"),
            "yesterday_limit_premium": premium.get("avg_pct"),
        },
        "advice": advice,
        "missing": sorted(set(missing)),
    }
    # 只在交易日开盘后落历史快照，避免盘前访问把昨日收盘数据记成今天
    now = now_cn()
    in_session_or_after = now.hour * 60 + now.minute >= 9 * 60 + 25
    if store is not None and in_session_or_after:
        from ..data.base import is_trade_day  # noqa: PLC0415

        if is_trade_day():
            store.save_sentiment(
                {
                    "date": snapshot["date"],
                    "score": score,
                    "confidence": confidence,
                    "phase": phase,
                    "limit_up": limit_up,
                    "limit_down": limit_down,
                    "lianban_height": height,
                    "total_amount": overview.get("total_amount"),
                    # 成交额是否为指数口径近似——落库保留标记，趋势图/回溯时不致把近似当精确。
                    "amount_approx": any("成交额" in m and "近似" in m for m in missing),
                }
            )
    return snapshot
