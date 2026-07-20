"""盘前「可买候选池」——把 morning 报告「核心股候选」的候选源从【昨日涨停池】(全是连板/高位接盘、
买不进或高位接盘)换成【未封板可买的质量池】(客户问题汇总 #2；对齐 t9 盘前简报开发文档的候选股池)。

确定性规则筛选(BUYRULE)，数字全来自真实全市场快照，AI 只从池里按今日催化方向挑选、绝不自造代码：
- 未封板可买：涨跌幅 < 该板涨停线-1（排除涨停/一字/近涨停——追这些就是高位接盘）
- 有资金关注：成交额 ≥ 3 亿（流动性够、资金在里面）
- 容量适中：流通市值 20-800 亿（不推微盘妖股、也不推巨无霸滞涨）
- 剔除 ST/退市/次新(C/N 前缀)
盘前基于【上一交易日收盘】快照（morning 08:25 生成时无当日盘中数据）；按成交额降序取 top_n
（资金关注度代理——盘前快照无「主力净流入」字段，用成交额近似，如实标注口径）。
"""
from __future__ import annotations

import logging

log = logging.getLogger("hermes.premarket")


def _board_limit(code: str) -> float:
    """各板涨停幅度：创业板30/科创688 = 20%；北交(8/4) = 30%；主板 = 10%。"""
    if code.startswith(("30", "688")):
        return 20.0
    if code.startswith(("8", "4")):
        return 30.0
    return 10.0


# 盘前候选的确定性状态链（发布门禁）：08:50 只能产出 T-1 盘前候选，绝不冒充今日可执行。
# 只有拿到当日实时价格/涨停状态/停牌/一字板/流动性校验后，下游才能把某条晋级为「盘中可执行候选」。
STATE_CHAIN = [
    "T-1盘前候选",          # 本函数产出（基于上一交易日收盘/历史数据）
    "09:15-09:25竞价待核",  # 集合竞价观察
    "开盘承接确认",          # 开盘后承接与量能确认
    "风险闸通过",            # 中央风险闸
    "盘中可执行候选",        # 拿到当日实时证据后才可标
]


def premarket_provenance_header(pool: dict | None) -> str:
    """确定性 Markdown 头（非 AI 生成）：明确"基于 T-1 / 盘前候选非今日可执行 / 昨日涨停竞价待核"。

    随报告 content_md 顶部注入，同时进入前端展示与 PDF 导出，不只留后端 payload。
    """
    pool = pool if isinstance(pool, dict) else {}
    as_of = pool.get("as_of") or pool.get("data_date") or "上一交易日"
    apc = 0
    try:
        apc = int(pool.get("auction_pending_count") or 0)
    except (TypeError, ValueError):
        apc = 0
    return (
        f"> 📌 盘前候选口径（数据日期 {as_of}·非实时）：本页候选**基于 T-1（{as_of}）收盘/历史数据**，"
        f"均为**盘前候选（非今日可执行）**；昨日涨停股（{apc} 只）列为**今日竞价待核**——须开盘拿到"
        "当日实时价格 / 涨停状态 / 停牌·一字板 / 流动性校验通过后，方可晋级为**盘中可执行**"
        "（状态链：T-1盘前候选 → 09:15-09:25竞价待核 → 开盘承接确认 → 风险闸通过 → 盘中可执行）。\n\n"
    )


def buyable_pool(top_n: int = 25) -> dict:
    """T-1 盘前候选池（确定性规则、数字真实，基于上一交易日收盘/历史数据）。

    发布门禁（时间语义）：
    - 这是 08:50 盘前产物，**只能**基于 T-1 收盘/历史快照，绝不声称"今日未封板可买/可执行"。
    - 昨日涨停股**不永久排除**：纳入候选但标 stage="今日竞价待核"，不给买入结论；只有当日实时价格、
      涨停状态、停牌/一字板/流动性校验通过后，下游才能晋级为"盘中可执行候选"（见 STATE_CHAIN）。
    - 输出带 as_of / is_realtime / phase，供前端明确区分盘前候选与盘中可执行，不得混称。

    返回 {ok, trade_date, as_of, is_realtime, phase, state_chain, rule,
    stocks:[{code,name,industry,price,price_as_of,change_pct,amount_yi,circ_mv_yi,
    turnover_rate,volume_ratio,pe_ttm,stage,auction_pending,note}], missing:[...]}。取不到一律标 missing、绝不编。"""
    from . import screener  # noqa: PLC0415 — 复用全市场快照 helper，避免循环导入

    out: dict = {
        "ok": True, "trade_date": None,
        "as_of": None, "data_date": None, "is_realtime": False,
        "phase": "T-1盘前候选", "today_validation_required": True,
        "state_chain": list(STATE_CHAIN),
        "rule": ("T-1盘前候选：基于上一交易日收盘·成交额≥3亿·流通市值20-800亿·剔ST/退/次新，按成交额排序；"
                 "昨日涨停股纳入并标『今日竞价待核』——非今日可执行，开盘需实时校验后方可晋级"),
        "stocks": [], "missing": [],
    }
    try:
        td = screener._resolve_trade_day()
    except Exception as e:  # noqa: BLE001
        out["ok"] = False
        out["missing"].append(f"交易日解析失败：{str(e)[:80]}")
        return out

    snap = screener._market_snapshot(td)
    out["trade_date"] = snap.get("trade_date")
    out["as_of"] = snap.get("trade_date")   # 数据口径日期 = 上一交易日收盘
    out["data_date"] = snap.get("trade_date")
    out["missing"] = list(snap.get("missing") or [])
    if not snap.get("ok"):
        out["ok"] = False
        return out

    cands: list[dict] = []
    auction_pending_count = 0
    for r in snap.get("rows", []):
        code = r.get("code") or ""
        nm = r.get("name") or ""
        cp, amt, cmv = r.get("change_pct"), r.get("amount"), r.get("circ_mv")
        if not code or cp is None or amt is None or cmv is None:   # 缺关键字段不入池（不猜）
            continue
        if "ST" in nm or "退" in nm or nm.startswith(("C", "N")):   # 剔 ST/退/次新
            continue
        if cp < -6.0:                                               # 大跌趋势坏，不作买入候选
            continue
        if amt < 3e8:                                               # 成交额 < 3 亿，资金关注不足
            continue
        if not (20e8 <= cmv <= 800e8):                             # 流通市值 20-800 亿
            continue
        # 昨日涨停（含近涨停/一字）不永久排除：纳入候选但标"今日竞价待核"，不给买入结论。
        limit_up = cp >= _board_limit(code) - 0.5
        stage = "今日竞价待核" if limit_up else "盘前候选"
        if limit_up:
            auction_pending_count += 1
        cands.append({
            "code": code, "name": nm, "industry": r.get("industry"),
            # 上一交易日收盘价，供盘前简报的交易价 claim 硬校验；
            # 与当日实时价分开标注，绝不冒充盘中价。
            "price": r.get("price"), "price_as_of": snap.get("trade_date"),
            "change_pct": round(cp, 2), "amount_yi": round(amt / 1e8, 1),
            "circ_mv_yi": round(cmv / 1e8, 1), "turnover_rate": r.get("turnover_rate"),
            "volume_ratio": r.get("volume_ratio"), "pe_ttm": r.get("pe_ttm"),
            "stage": stage,
            "auction_pending": limit_up,
            "today_validation_required": True,   # 盘前候选一律需当日实时校验后才可执行
            "note": ("昨日涨停·今日竞价待核（非今日可执行，开盘需实时校验）"
                     if limit_up else "T-1盘前候选·上日未封板"),
        })
    # 非涨停候选优先（可直接进盘前候选），涨停待核其次；组内按成交额降序（资金关注度代理）。
    cands.sort(key=lambda x: (x["auction_pending"], -(x["amount_yi"] or 0)))
    out["stocks"] = cands[:top_n]
    out["auction_pending_count"] = min(auction_pending_count, len(out["stocks"]))
    if not cands:
        out["missing"].append("未筛出符合规则的盘前候选（可能非交易日/免费公开快照数据不全）")
    return out
