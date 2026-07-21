"""资金择时 — 0AMV 四区域(进攻/震荡/防守/出击) + 贪恐五因子温度计。

设计参考：ashare-agent/scripts/timing.py（指南针 0AMV 活跃市值 + CNN 式贪恐）的理念，
但口径按本项目【真实可取到的数据】重构：

  ① 区域择时(0AMV 四区域)：用【指数/个股的价 + 均线(MA20/MA60) + 量能(成交量/量能均线)】
     判 进攻/出击/震荡/防守 四区域，并给 0~100 择时评分。对【多指数 + 任意个股】都适用。
     —— 不照搬严格 0AMV 通达信公式（其依赖逐根换手率 turnover，指数级取不到），
        改用价/均线/量能这套等价、且指数与个股都拿得到的判据（与用户口径一致）。

  ② 贪恐五因子(市场级温度计，0~100，100=极度贪婪)，等权合成：
     涨跌家数比 / 涨停温度 / 成交量比 / 资金 / 波动反向。
     —— 北向资金实时数据已停止公布（见 data/market.py），故第④因子「资金」改用
        【两市主力净流入(market_flow)】，北向不纳入并明确标注 missing。

数据源（全部真实，取不到标 missing，绝不编造）：
  指数实时价/涨跌幅/成交额 —— tencent_source.index_quotes（强制直连，独立于东财）
  指数/个股日线(价/量) ——
      指数：market.index_kline（新浪指数日K，含 o/c/h/l/v，160 根）
      个股：腾讯免费前复权日线（口径不可用时显式缺失）
  个股实时价/换手/量比/市值 —— tencent_source.quote
  涨跌家数 / 涨停 / 跌停 —— akshare 乐咕活跃度(stock_market_activity_legu，免费、独立东财)
  两市主力净流入 —— market.market_flow（东财，直连机被封时为 None → 标 missing）

单位约定（与全项目一致）：价=元、成交额=元、涨跌幅/换手=%、市值=元、资金净额=元。

铁律落地：任何因子取不到 → 该因子置 None 并写入 missing，评分只用拿到的因子归一化平均；
全部缺失则 ok=False。所有评分公式在下方常量/注释里写清。
"""
from __future__ import annotations

import time
from typing import Any

from ..data import market
from ..data.base import to_float, ts_str, ttl_cache, log
from ..data import tencent_source as tc
from ..data import tdx_api_source as tdxapi

# 多指数：腾讯符号 -> 中文名（与 market.INDEX_CODES 对齐，可独立扩展）
INDEX_LIST = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
]

# 区域阈值（评分 0~100 切分）：进攻/出击/震荡/防守四区。
# 区域【主】由均线结构(多头/空头/纠缠)判，评分用于在结构内细分强弱与排序。
ZONE_OFFENSE = "进攻"      # 多头排列 + 量价配合：趋势进攻、可重仓
ZONE_STRIKE = "出击"       # 均线刚转多/底部抬升：试探出击、控制仓位
ZONE_SHOCK = "震荡"        # 均线纠缠、价绕 MA20：区间震荡、轻仓波段
ZONE_DEFENSE = "防守"      # 空头排列 + 价在均线下：防守持币

ZONE_ADVICE = {
    ZONE_OFFENSE: "多头排列且量价配合，趋势进攻区——可顺势重仓持有，回踩 MA20 不破即持。",
    ZONE_STRIKE: "均线由空转多/底部抬升，出击区——试探性建仓、控制在半仓内，站稳再加。",
    ZONE_SHOCK: "均线纠缠、价格围绕 MA20 反复，震荡区——轻仓高抛低吸做波段，不追涨杀跌。",
    ZONE_DEFENSE: "空头排列且价在均线之下，防守区——以持币观望为主，不抄底、等趋势修复。",
}

# 择时评分权重随区域自适应（白皮书§4「因子权重随市场状态切换」的确定性落地，四权重恒和为 1）：
#   进攻/趋势市——重趋势+动量（顺势为王）；震荡市——重位置+量能（高抛低吸、放量才动）；
#   出击市——均衡（默认）；防守市——重趋势(向下压制)、位置次之。
_ZONE_W = {
    ZONE_OFFENSE: {"trend": 0.45, "mom": 0.30, "vol": 0.15, "pos": 0.10},
    ZONE_STRIKE:  {"trend": 0.40, "mom": 0.25, "vol": 0.20, "pos": 0.15},
    ZONE_SHOCK:   {"trend": 0.25, "mom": 0.15, "vol": 0.25, "pos": 0.35},
    ZONE_DEFENSE: {"trend": 0.40, "mom": 0.25, "vol": 0.15, "pos": 0.20},
}

# 量能：成交量均线窗口（短/长），量能比 = 短均量 / 长均量
_VOL_FAST, _VOL_SLOW = 5, 20
# 区间位置/波动分位窗口
_RANGE_WIN = 120
_VOL_PCT_WIN = 120


# ----------------------------- 纯计算工具 -----------------------------

def _sma(x: list[float | None], n: int) -> float | None:
    """最近 n 个有效值的简单均值；不足 1 个返回 None。"""
    vals = [v for v in x[-n:] if v is not None]
    return sum(vals) / len(vals) if vals else None


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _pct_rank(series: list[float | None], window: int) -> float | None:
    """最新有效值在最近 window 个有效值中的分位(0~1)；样本 <5 返回 None。"""
    s = [v for v in series[-window:] if v is not None]
    if len(s) < 5:
        return None
    cur = s[-1]
    return sum(1 for v in s if v <= cur) / len(s)


def _greed_label(s: float) -> str:
    if s < 25:
        return "极度恐惧"
    if s < 45:
        return "恐惧"
    if s < 55:
        return "中性"
    if s < 75:
        return "贪婪"
    return "极度贪婪"


# ----------------------------- 日线取数（指数 / 个股） -----------------------------

def _index_bars(full_code: str) -> list[dict] | None:
    """指数日线 ``[{d,o,c,h,l,v}]``；交互链路只用腾讯 3s 有界 K 线。

    指数无除权语义，腾讯返回键是 ``day`` 而非 ``qfqday``，必须请求
    ``adjust=none``。若本轮失败就如实缺失，不再串行进入 AkShare 慢备源。
    """
    direct = tc.stock_daily_bars(full_code, 180, adjust="none")
    return direct[-160:] if direct else None


@ttl_cache(300, fail_seconds=600, offhours_seconds=6 * 3600)
def _stock_bars(code: str, days: int = 260) -> list[dict] | None:
    """个股前复权日线 [{d,o,c,h,l,v}]。
    只接受腾讯公开前复权日线。量能只参与比例计算，
    腾讯返回的第六列保持原量纲即可，不伪装成交额。"""
    code = str(code).strip().zfill(6)
    rows = tc.stock_daily_bars(code, days, adjust="qfq")
    return rows[-days:] if rows else None


# ----------------------------- 区域择时（0AMV 四区域） -----------------------------

def _timing_from_bars(bars: list[dict], price: float | None = None,
                      change_pct: float | None = None) -> dict[str, Any]:
    """由日线 bars(+可选实时价) 计算 0AMV 四区域 + 0~100 择时评分。

    评分 = 四分量加权，每个分量 0~1；权重按区域自适应(见 _ZONE_W；括号内为出击/默认区权重)：
      trend  趋势(0.40)：价相对 MA20/MA60 的位置（>MA20 且 MA20>MA60 趋强）
      mom    动量(0.25)：20 日涨幅，±15% 线性映射满量程
      vol    量能(0.20)：量能比(5日均量/20日均量) 在 [0.6,1.8] 映射，放量为强
      pos    位置(0.15)：收盘在 RANGE_WIN 日高低区间的位置
    区域(主)由均线结构 + 价位判：
      多头(MA5>MA20>MA60)且价>MA20            -> 进攻
      MA20>MA60 但非完整多头(价刚站上/回踩)    -> 出击
      空头(MA5<MA20<MA60)且价<MA20            -> 防守
      其余(纠缠/价绕 MA20)                      -> 震荡
    """
    out: dict[str, Any] = {"ok": False, "score": None, "zone": "", "advice": "",
                           "factors": {}, "ma": {}, "missing": []}
    closes = [to_float(b.get("c")) for b in bars]
    closes = [c for c in closes if c is not None]
    if len(closes) < 20:
        out["missing"].append("日线不足(需≥20根)")
        return out
    highs = [to_float(b.get("h")) for b in bars]
    lows = [to_float(b.get("l")) for b in bars]
    vols = [to_float(b.get("v")) for b in bars]
    # high/low 缺失用 close 兜底（指数/部分源可能不全）
    closes_full = [to_float(b.get("c")) for b in bars]
    highs = [h if h is not None else c for h, c in zip(highs, closes_full)]
    lows = [l if l is not None else c for l, c in zip(lows, closes_full)]

    last_close = closes[-1]
    # 实时价优先作为"最新价位"（盘中比昨收更真实）；无则用日线收盘
    px = price if (price is not None and price > 0) else last_close

    ma5 = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    out["ma"] = {"ma5": round(ma5, 3) if ma5 else None,
                 "ma20": round(ma20, 3) if ma20 else None,
                 "ma60": round(ma60, 3) if ma60 else None,
                 "price": round(px, 3) if px else None}

    # ① 趋势分量
    f_trend = 0.5
    if ma20:
        # 价相对 MA20：±6% 映射到 ±0.5
        f_trend = _clamp01((px / ma20 - 1) / 0.12 + 0.5)
        if ma60:
            # 多头排列(MA20>MA60)整体抬升 0.12，空头压低 0.12
            f_trend = _clamp01(f_trend + (0.12 if ma20 > ma60 else -0.12))

    # ② 动量分量：20 日涨幅 ±15% 满量程
    base = closes[-21] if len(closes) > 21 else closes[0]
    f_mom = _clamp01((last_close / base - 1) / 0.30 + 0.5) if base else 0.5

    # ③ 量能分量：量能比 = 5日均量/20日均量，[0.6,1.8] 映射
    vol_fast = _sma(vols, _VOL_FAST)
    vol_slow = _sma(vols, _VOL_SLOW)
    vol_ratio = (vol_fast / vol_slow) if (vol_fast and vol_slow) else None
    f_vol = _clamp01((vol_ratio - 0.6) / (1.8 - 0.6)) if vol_ratio is not None else 0.5

    # ④ 位置分量：RANGE_WIN 日高低区间位置
    w = min(_RANGE_WIN, len(closes))
    hi, lo = max(highs[-w:]), min(lows[-w:])
    f_pos = _clamp01((last_close - lo) / (hi - lo)) if hi > lo else 0.5

    # 区域判定（均线结构为主）——先定区域，权重再按区域自适应（状态依赖加权，白皮书§4）。
    if ma5 and ma20 and ma60 and ma5 > ma20 > ma60 and px >= ma20:
        zone = ZONE_OFFENSE
    elif ma20 and ma60 and ma20 > ma60 and px >= ma20:
        zone = ZONE_STRIKE
    elif ma5 and ma20 and ma60 and ma5 < ma20 < ma60 and px < ma20:
        zone = ZONE_DEFENSE
    else:
        zone = ZONE_SHOCK

    # 权重随区域切换：趋势市重趋势/动量、震荡市重位置/量能、防守市重趋势(向下)。
    W = _ZONE_W[zone]
    score = (f_trend * W["trend"] + f_mom * W["mom"] + f_vol * W["vol"] + f_pos * W["pos"]) * 100

    out.update({
        "ok": True,
        "score": round(score, 1),
        "zone": zone,
        "advice": ZONE_ADVICE[zone],
        "change_pct": change_pct,
        "factors": {
            "趋势": round(f_trend * 100, 1),
            "动量": round(f_mom * 100, 1),
            "量能": round(f_vol * 100, 1),
            "位置": round(f_pos * 100, 1),
        },
        "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "bars": len(closes),
    })
    return out


def index_timing(*, deadline: float | None = None) -> list[dict]:
    """多指数择时：每个指数给 {code,name,price,change_pct,score,zone,advice,factors,ma}。"""
    quotes = {}
    try:
        quotes = tc.index_quotes([c for c, _ in INDEX_LIST]) or {}
    except Exception as e:
        log.info("择时 指数实时报价失败: %s", str(e)[:120])
    rows: list[dict] = []
    for code, name in INDEX_LIST:
        if deadline is not None and time.monotonic() >= deadline:
            rows.append({
                "code": code, "name": name, "price": None, "change_pct": None,
                "ok": False, "score": None, "zone": "", "advice": "",
                "factors": {}, "ma": {}, "missing": ["本轮已达交互预算，未发起该指数日线请求"],
            })
            continue
        q = quotes.get(code) or {}
        bars = _index_bars(code)
        item: dict[str, Any] = {"code": code, "name": name,
                                "price": q.get("price"), "change_pct": q.get("change_pct")}
        if not bars:
            item.update({"ok": False, "score": None, "zone": "", "advice": "",
                         "factors": {}, "ma": {}, "missing": ["指数日线"]})
            rows.append(item)
            continue
        t = _timing_from_bars(bars, price=q.get("price"), change_pct=q.get("change_pct"))
        item.update(t)
        rows.append(item)
    return rows


def stock_timing(code: str) -> dict:
    """个股择时：短预算行情 + 腾讯前复权日线 → 区域 + 评分。"""
    code = str(code).strip().zfill(6)
    out: dict[str, Any] = {"code": code, "name": "", "ok": False, "missing": []}
    q = None
    try:
        # 已配 TDX API 时使用 wait=0（网络最大 2.5s），盘后 pending 直接交给
        # 日线收盘价；不再在择时内叠加本地 TDX 或多个实时备源。
        q = (
            tdxapi.quote(code, max_age=30, wait=0)
            if tdxapi.configured() else tc.quote(code)
        )
    except Exception as e:
        log.info("择时 个股报价(%s)失败: %s", code, str(e)[:120])
    if q:
        out["name"] = q.get("name") or ""
        out["price"] = q.get("price")
        out["change_pct"] = q.get("change_pct")
        out["turnover_rate"] = q.get("turnover_rate")
        out["volume_ratio"] = q.get("volume_ratio")
    bars = _stock_bars(code)
    if not bars:
        out["missing"].append("个股日线(腾讯/通达信均未取到)")
        return out
    t = _timing_from_bars(bars, price=(q or {}).get("price"), change_pct=(q or {}).get("change_pct"))
    # _timing_from_bars 的 change_pct 用实时；若实时缺，用日线末两根收盘推算并标注
    if t.get("ok") and t.get("change_pct") is None and len(bars) >= 2:
        c1, c0 = to_float(bars[-1].get("c")), to_float(bars[-2].get("c"))
        if c1 and c0:
            t["change_pct"] = round((c1 / c0 - 1) * 100, 2)
            out["missing"].append("涨跌幅为日线推算(实时报价缺失)")
    out.update({k: v for k, v in t.items() if k != "missing"})
    out["missing"].extend(t.get("missing", []))
    out["ok"] = bool(t.get("ok"))
    return out


# ----------------------------- 贪恐五因子（市场级） -----------------------------

def _legu_breadth() -> dict | None:
    """复用市场层乐咕快照，避免 3 秒短预算污染共享数据源熔断器。"""
    return market.market_activity_legu()


def _index_vol_factors() -> dict:
    """上证指数日线派生的两个市场级因子原料：
      量能比 vol_ratio = 5日均量/20日均量（成交量比因子）
      20日已实现波动 的 VOL_PCT_WIN 日分位（波动因子，取反=贪）
    取不到则对应值为 None。"""
    bars = _index_bars("sh000001")
    out = {"vol_ratio": None, "vol_pctl": None, "last_date": None}
    if not bars:
        return out
    out["last_date"] = str(bars[-1].get("d") or "")[:10]   # 因子所依据的最近已收盘交易日（判隔日用）
    closes = [to_float(b.get("c")) for b in bars]
    closes = [c for c in closes if c is not None]
    vols = [to_float(b.get("v")) for b in bars]
    vf, vs = _sma(vols, _VOL_FAST), _sma(vols, _VOL_SLOW)
    if vf and vs:
        out["vol_ratio"] = vf / vs
    if len(closes) >= 25:
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        vol20: list[float] = []
        for i in range(len(rets)):
            seg = rets[max(0, i - 19):i + 1]
            mean = sum(seg) / len(seg)
            vol20.append((sum((x - mean) ** 2 for x in seg) / len(seg)) ** 0.5)
        out["vol_pctl"] = _pct_rank(vol20, _VOL_PCT_WIN)
    return out


def fear_greed() -> dict:
    """贪恐五因子温度计（市场级，0~100，100=极度贪婪），等权合成。

    五因子(每个 0~100，缺失=None 不计入平均)：
      ① 涨跌家数比：up/(up+down)，0.5→50 分，线性
      ② 涨停温度  ：limit_up/(limit_up+limit_down)，涨停越多越贪
      ③ 成交量比  ：上证 5/20 日量能比，[0.6,1.8] 映射(放量=贪)
      ④ 资金      ：两市主力净流入(market_flow.main_flow_total)，±2000亿映射
                    —— 北向资金实时已停发(见 market.py)，不纳入并标注
      ⑤ 波动反向  ：上证 20 日波动的 120 日分位取反(低波动=贪)
    """
    missing: list[str] = []
    factors: dict[str, float | None] = {}
    meta: dict[str, Any] = {}

    # ① 涨跌家数比 + ② 涨停温度
    breadth = _legu_breadth()
    if breadth and breadth.get("up") is not None and breadth.get("down") is not None:
        up, down = breadth["up"], breadth["down"]
        tot = up + down
        factors["涨跌家数比"] = round(up / tot * 100, 1) if tot > 0 else 50.0
        meta["up"], meta["down"], meta["flat"] = up, down, breadth.get("flat")
    else:
        factors["涨跌家数比"] = None
        missing.append("涨跌家数(乐咕不可用)")
    if breadth and breadth.get("limit_up") is not None:
        zu = breadth.get("limit_up") or 0
        zd = breadth.get("limit_down") or 0
        meta["limit_up"], meta["limit_down"] = zu, zd
        # 涨停温度：涨停占涨停+跌停比；二者都为 0(无涨跌停)视为中性 50
        factors["涨停温度"] = round(zu / (zu + zd) * 100, 1) if (zu + zd) > 0 else 50.0
    else:
        factors["涨停温度"] = None
        missing.append("涨停/跌停家数(乐咕不可用)")

    # ③ 成交量比 + ⑤ 波动反向（上证日线派生）
    iv = _index_vol_factors()
    vr = iv.get("vol_ratio")
    if vr is not None:
        factors["成交量比"] = round(_clamp01((vr - 0.6) / (1.8 - 0.6)) * 100, 1)
        meta["vol_ratio"] = round(vr, 2)
    else:
        factors["成交量比"] = None
        missing.append("成交量比(上证指数日线不可用)")
    vp = iv.get("vol_pctl")
    if vp is not None:
        factors["波动反向"] = round((1.0 - vp) * 100, 1)
        meta["vol_pctl"] = round(vp * 100, 1)
    else:
        factors["波动反向"] = None
        missing.append("波动分位(上证指数日线不足)")

    # ④ 资金（两市主力净流入；北向已停发）
    # 资金流源可能进入 25s 隔离子进程，不得由交互型择时接口触发。
    # 只消费后台预热/其他页面已成功留下的 last-good；没有就如实缺失。
    mf = market.market_flow_snapshot()
    main_total = mf.get("main_flow_total") if mf else None
    main_total_kind = str((mf or {}).get("main_flow_total_kind") or "")
    if main_total is not None and main_total_kind == "market_total":
        # ±2000 亿(2e11 元) 线性映射到 ±0.5
        factors["资金"] = round(_clamp01(main_total / 4e11 + 0.5) * 100, 1)
        meta["main_flow_total"] = main_total
    else:
        factors["资金"] = None
        missing.append(
            "两市主力净流入(仅有行业资金代理值，不参与资金因子)"
            if main_total is not None else "两市主力净流入(资金因子缺失)"
        )
    # 北向资金实时已停发，固定标注（不纳入五因子计算）
    missing.append("北向资金实时数据已停止公布，资金因子改用两市主力净流入")

    # 贪恐是【日线级】温度：基于最近已收盘交易日的家数/量能/波动，盘中日线未收不会变。
    # 必须如实标出"截至哪个交易日收盘"——否则盘前/盘中会把昨日温度当今日实时（BLOCKER，同资金追踪#6）。
    from ..data.base import now_cn  # noqa: PLC0415
    data_date = iv.get("last_date") or ""
    today = now_cn().strftime("%Y-%m-%d")
    stale = bool(data_date and data_date != today)
    if stale:
        missing.insert(0, f"贪恐为截至 {data_date} 收盘的日线温度，非今日盘中实时（今日收盘结算后自动更新为当日）")

    valid = [v for v in factors.values() if v is not None]
    if not valid:
        return {"ok": False, "score": None, "label": "数据不足",
                "factors": factors, "missing": missing, "meta": meta,
                "data_date": data_date, "stale": stale}
    score = round(sum(valid) / len(valid), 1)
    return {
        "ok": True,
        "score": score,
        "label": _greed_label(score),
        "factors": factors,        # 五因子 0~100（缺失为 None）
        "missing": missing,
        "meta": meta,              # 原始口径数值，便于前端展示真实数据
        "data_date": data_date,    # 因子所依据的最近收盘交易日
        "stale": stale,            # True=非今日（盘前/盘中显示的是上一交易日温度）
    }


# ----------------------------- 对外总入口 -----------------------------

@ttl_cache(20, fail_seconds=600, offhours_seconds=6 * 3600)
def timing(code: str | None = None) -> dict:
    """资金择时总成：贪恐五因子 + 多指数四区域 + (可选)个股四区域。

    GET /api/timing?code=  —— code 为 6 位个股代码时附 stock 区域。
    返回 {ok, ts, missing, fear_greed{...}, indexes:[...], stock:{...}|None}。
    """
    started = time.monotonic()
    deadline = started + 8.0
    missing: list[str] = []
    out: dict[str, Any] = {"ok": True, "ts": ts_str(), "missing": missing}

    fg = fear_greed()
    out["fear_greed"] = fg
    # 顶层透出贪恐的数据日期/隔日标记，前端据此显式提示"非今日实时"（同资金追踪#6）
    out["data_date"] = fg.get("data_date")
    out["stale"] = fg.get("stale")
    if fg.get("stale") and fg.get("missing"):
        note = fg["missing"][0]
        if note not in missing:
            missing.append(note)

    out["stock"] = None
    code = (code or "").strip()
    if code:
        if len(code) == 6 and code.isdigit():
            # 显式查询个股时优先完成个股区域，剩余预算再补市场指数。
            st = stock_timing(code) if time.monotonic() < deadline else {
                "code": code, "ok": False, "missing": ["本轮已达交互预算，个股日线未发起"],
            }
            out["stock"] = st
            if not st.get("ok"):
                missing.extend(m for m in st.get("missing", []) if m not in missing)
        else:
            missing.append("个股代码应为6位数字")
    idx = index_timing(deadline=deadline)
    out["indexes"] = idx
    if not any(i.get("ok") for i in idx):
        missing.append("全部指数择时数据不可用")
    if time.monotonic() >= deadline:
        missing.append("资金择时已达 8 秒交互预算，未发起的增强请求已跳过")
    return out


# ----------------------------------- 自测 -----------------------------------

if __name__ == "__main__":
    import json

    print("=" * 72)
    print("timing.py 自测：_timing_from_bars 纯计算 + 区域/评分边界")
    print("=" * 72)

    # 造一段确定性上行样本（多头排列 → 进攻区）
    def _mk(days=160, trend=0.004):
        import math
        bars, price = [], 3000.0
        for i in range(days):
            wob = math.sin(i / 8.0) * 0.003
            price *= (1 + trend + wob)
            bars.append({"d": f"D{i:03d}", "o": round(price * 0.999, 2),
                         "c": round(price, 2), "h": round(price * 1.01, 2),
                         "l": round(price * 0.99, 2), "v": round(1e8 * (1 + math.sin(i / 6.0) * 0.4))})
        return bars

    up = _mk(trend=0.004)
    t = _timing_from_bars(up, price=up[-1]["c"])
    print("[上行样本]", json.dumps({k: t[k] for k in ("ok", "score", "zone", "factors", "ma")},
                                ensure_ascii=False))
    assert t["ok"] and 0 <= t["score"] <= 100, "评分应在 0~100"
    assert t["zone"] in (ZONE_OFFENSE, ZONE_STRIKE), f"上行应进攻/出击，实际 {t['zone']}"

    down = _mk(trend=-0.004)
    td = _timing_from_bars(down, price=down[-1]["c"])
    print("[下行样本]", json.dumps({k: td[k] for k in ("ok", "score", "zone")}, ensure_ascii=False))
    assert td["zone"] in (ZONE_DEFENSE, ZONE_SHOCK), f"下行应防守/震荡，实际 {td['zone']}"
    assert td["score"] < t["score"], "下行评分应低于上行"

    # 边界：数据不足
    assert _timing_from_bars(up[:10])["ok"] is False, "不足20根应 ok=False"

    # _greed_label 分档
    assert _greed_label(10) == "极度恐惧" and _greed_label(90) == "极度贪婪"
    assert _greed_label(50) == "中性"

    # _pct_rank / _sma 微校
    assert _pct_rank([1, 2, 3, 4, 5], 10) == 1.0
    assert _pct_rank([1, 2], 10) is None
    assert abs(_sma([2, 4, 6], 2) - 5.0) < 1e-9

    print("\n纯计算断言全部通过 ✓")
    print("=" * 72)
