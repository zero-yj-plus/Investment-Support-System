"""量化套件：沪深300 免费快照打分 / 因子留痕 / 简单择时回测。

数据链只使用无付费 token 的公开源：沪深300 成分取中证公开表，当日截面
取腾讯行情，回测指数日线取腾讯、新浪公开接口。所有区间都核对请求根数与
as-of；上游不足时返回带 ``missing`` 的空结果，绝不静默缩短回测窗口。

铁律落地：①回测信号次日生效（无未来函数）；②因子缺失置 None，截面
z-score 只在真实到场因子间归一；③外部公开源失败优雅降级，不抛到调用方。
"""
from __future__ import annotations

import datetime
import math

from ..data.base import get_ak, safe, to_float, trade_dates, ts_str, ttl_cache, log, now_cn
from ..data import tencent_source as tc
from ..data import free_source
from . import stock_analysis_contract

# ------------------------------------------------------------ 常量

HS300 = "399300.SZ"          # 沪深300 成分（index_weight 用 .SZ 口径，env 实测可取 300 只）
HS300_BENCH = "000300.SH"    # 沪深300 指数行情（index_daily 用 .SH 口径，回测基准/择时标的）
UNIVERSE_CAP = 300           # 成分上限（沪深300 本就 ≈300）
MOM_WINDOW = 20              # 动量/波动窗口（交易日）

# 打分榜因子权重（z-score 合成）。value/低估两支(pe,pb)合占 .35，与动量同权重量级。
# 方向：pe/pb 取「便宜度」(1/pe、1/pb，越大越便宜越好)；换手取负 z(低换手偏好)；
# 量比取正 z(温和放量)；动量取正 z。
RANK_WEIGHTS = {"momentum": 0.30, "value_pe": 0.20, "value_pb": 0.15,
                "turnover": 0.15, "vol_ratio": 0.20}

# 因子有效性评估参数
HOLD = 5                     # 调仓间隔(交易日)，周频
IC_MOM_SPAN = 4              # 动量回看 = IC_MOM_SPAN 个调仓日(=20 交易日)
N_LAYERS = 5                 # 分层数
MAX_PERIODS = 12             # periods 内部封顶，防超时

# 回测参数
BT_MAX_DAYS = 500            # 回测最长窗口（交易日）
TRADING_DAYS = 252           # 年化交易日数


# ------------------------------------------------------------ 纯 Python 统计

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    """样本标准差(n-1)；不足 2 个或退化 → 0.0。"""
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var) if var > 0 else 0.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _zscores(values: dict, clip: float = 3.0) -> dict:
    """对 {key: raw} 在截面内做 z-score（None 不参与统计、回填 0=均值），3σ winsorize。
    全等/单点/有效值<2 → 全 0。"""
    present = [v for v in values.values() if v is not None]
    if len(present) < 2:
        return {k: 0.0 for k in values}
    m, sd = _mean(present), _std(present)
    if sd <= 0:
        return {k: 0.0 for k in values}
    out = {}
    for k, v in values.items():
        out[k] = _clamp((v - m) / sd, -clip, clip) if v is not None else 0.0
    return out


def _z_to_100(z: float, clip: float = 3.0) -> float:
    """把 [-clip, clip] 的 z 线性映射到 [0,100]，中位(z=0)=50。"""
    z = _clamp(z, -clip, clip)
    return round((z + clip) / (2 * clip) * 100.0, 1)


def _ranks(vals: list[float]) -> list[float]:
    """求秩(1..n)，并列取平均秩(midrank)。"""
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman 秩相关(带并列 midrank)；长度<3 或方差为 0 → None。"""
    n = len(xs)
    if n < 3 or len(ys) != n:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = _mean(rx), _mean(ry)
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((r - mx) ** 2 for r in rx)
    vy = sum((r - my) ** 2 for r in ry)
    if vx <= 0 or vy <= 0:
        return None
    return _clamp(cov / math.sqrt(vx * vy), -1.0, 1.0)


def _layer_means(pairs: list[tuple], n_layers: int = N_LAYERS) -> list:
    """pairs=[(因子值, 前向收益), ...] 按因子值升序分 n_layers 层，返回各层平均前向收益。
    样本不足分层 → [None]*n_layers。"""
    out = [None] * n_layers
    m = len(pairs)
    if m < n_layers:
        return out
    ordered = sorted(pairs, key=lambda p: p[0])
    buckets: list[list[float]] = [[] for _ in range(n_layers)]
    for idx, p in enumerate(ordered):
        b = min(int(idx * n_layers / m), n_layers - 1)
        buckets[b].append(p[1])
    for i in range(n_layers):
        if buckets[i]:
            out[i] = _mean(buckets[i])
    return out


# ------------------------------------------------------------ 取数助手

@ttl_cache(6 * 3600)
def _trade_days(n_back: int = 40) -> list[str]:
    """最近 n_back 个 SSE 已开市交易日（YYYYMMDD 升序）。失败 → []。
    6h 缓存：交易日历静态，且打分/因子/回测共用，避免反复打 trade_cal。"""
    days = sorted((trade_dates() or set()))
    compact = [d.replace("-", "") for d in days if d <= now_cn().date().isoformat()]
    return compact[-n_back:] if n_back else compact


@ttl_cache(12 * 3600)
def _hs300_universe() -> list[str]:
    """沪深300 最近一期成分 con_code 列表（带后缀）。失败/空 → []。12h 缓存（每月调一次）。"""
    ak = get_ak()
    fn = getattr(ak, "index_stock_cons_csindex", None) if ak else None
    df = safe(fn, symbol="000300", label="沪深300成分(中证官方)", timeout=20) if fn else None
    if df is None or getattr(df, "empty", True):
        return []
    col = "成分券代码" if "成分券代码" in df.columns else ("code" if "code" in df.columns else None)
    if not col:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for value in df[col].tolist():
        code = str(value).split(".")[0].zfill(6)
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out[:UNIVERSE_CAP]


@ttl_cache(600)
def _basic_snapshot(date_compact: str) -> dict:
    """daily_basic 全市场当日快照（10min 缓存）→ {ts_code: {...}}。
    单位：pe_ttm/pb/turnover_rate_f(%)/volume_ratio/close(元)/circ_mv(万元)。失败 → {}。"""
    codes = _hs300_universe()
    quotes = tc.quotes(codes) if codes else {}
    return {
        code: {
            "close": q.get("price"),
            "turnover": q.get("turnover_rate"),
            "vol_ratio": q.get("volume_ratio"),
            "pe_ttm": q.get("pe"),
            "pb": q.get("pb"),
            "circ_mv": q.get("float_mv"),
        }
        for code, q in quotes.items()
    }


@ttl_cache(600)
def _price_snapshot(date_compact: str) -> dict:
    """daily 全市场当日快照（10min 缓存）→ {ts_code: {close, pct_chg}}。失败 → {}。
    close 为不复权收盘；沪深300 成分除权稀疏，比值收益口径影响可忽略。"""
    codes = _hs300_universe()
    quotes = tc.quotes(codes) if codes else {}
    return {code: {"close": q.get("price"), "pct_chg": q.get("change_pct")} for code, q in quotes.items()}


def _code6(ts_code: str) -> str:
    """'600519.SH' → '600519'。"""
    s = "".join(ch for ch in str(ts_code or "") if ch.isdigit())
    return s[:6] if s else str(ts_code or "")


def _parse_index_daily_frame(frame) -> list[dict]:
    """将腾讯/新浪指数日线 DataFrame 归一为升序 ``[{d,c,...}]``。

    两个公开源的字段名在 AkShare 版本间偶有差异；这里只接受带有效日期、
    正收盘价的行，并按日期去重，不做插值。
    """
    if frame is None or getattr(frame, "empty", True):
        return []
    by_date: dict[str, dict] = {}
    try:
        for _, row in frame.iterrows():
            raw_date = row.get("date")
            if raw_date is None:
                raw_date = row.get("日期")
            if raw_date is None:
                raw_date = row.get("day")
            compact = str(raw_date or "").replace("-", "")[:8]
            close = to_float(row.get("close") if "close" in row else row.get("收盘"))
            if len(compact) != 8 or not compact.isdigit() or close is None or close <= 0:
                continue
            by_date[compact] = {
                "d": compact,
                "o": to_float(row.get("open") if "open" in row else row.get("开盘")),
                "c": close,
                "h": to_float(row.get("high") if "high" in row else row.get("最高")),
                "l": to_float(row.get("low") if "low" in row else row.get("最低")),
                "v": to_float(row.get("volume") if "volume" in row else row.get("成交量")),
            }
    except Exception as exc:  # 外部表结构变化时必须降级，不把异常抛给 API
        log.info("指数日线解析失败: %s", str(exc)[:120])
        return []
    return [by_date[d] for d in sorted(by_date)]


@ttl_cache(1800)
def _index_daily_history(full_code: str, required_bars: int) -> dict:
    """按需取免费指数日线，不复用 UI 图表的 160 根截断。

    腾讯接口支持按日期区间请求，优先用它减少全量抓取；如果返回根数不足，
    再以新浪全量日线作免费备份。两源不拼接，避免复权口径混用。
    """
    try:
        required_bars = max(1, int(required_bars))
    except Exception:
        required_bars = 1
    result = {
        "rows": [], "source": None, "as_of": None,
        "requested": required_bars, "available": 0,
    }
    ak = get_ak()
    if ak is None:
        return result

    # A 股交易日约占自然日 68%；1.8 倍+固定余量能覆盖长假、停牌等。
    end_compact = free_source.quote_data_date().replace("-", "")
    try:
        end_date = datetime.datetime.strptime(end_compact, "%Y%m%d").date()
    except ValueError:
        end_date = now_cn().date()
        end_compact = end_date.strftime("%Y%m%d")
    lookback_days = max(180, int(math.ceil(required_bars * 1.8)) + 45)
    start_compact = (end_date - datetime.timedelta(days=lookback_days)).strftime("%Y%m%d")

    candidates: list[tuple[str, list[dict]]] = []
    tx_fn = getattr(ak, "stock_zh_index_daily_tx", None)
    if tx_fn is not None:
        tx_frame = safe(
            tx_fn,
            symbol=full_code,
            start_date=start_compact,
            end_date=end_compact,
            label="指数日K(腾讯公开源)",
            timeout=20,
        )
        candidates.append(("腾讯指数日K", _parse_index_daily_frame(tx_frame)))

    # 不以新浪加密历史接口补根数：其 MiniRacer 在服务线程池并发时可能直接 abort。
    # 腾讯不足就如实返回 available/requested，让上层展示历史不足，不混源、不冒充。

    if not candidates:
        return result
    source, rows = max(candidates, key=lambda item: len(item[1]))
    result.update({
        "rows": rows[-required_bars:],
        "source": source,
        "as_of": rows[-1]["d"] if rows else None,
        "available": len(rows),
    })
    return result


# ------------------------------------------------------------ ① 多因子打分榜

def _multi_day_closes(date_days: list[str]) -> dict:
    """取 date_days 各交易日的全市场 close → {ts_code: {date: close}}（仅用于动量窗口）。"""
    closes: dict = {}
    for d in date_days:
        snap = _price_snapshot(d)
        for code, v in snap.items():
            c = v.get("close")
            if c is not None and c > 0:
                closes.setdefault(code, {})[d] = c
    return closes


@ttl_cache(600)
def factor_rank(top_n: int = 50) -> dict:
    """沪深300 免费实时截面打分榜（TOP top_n）。

    成分来自中证指数官方公开表，行情/PE/PB/换手/量比来自腾讯。免费源没有一次性
    提供全成分历史截面的接口，因此 ``momentum`` 明确使用当日强弱，而不是伪称 20 日动量；
    真正的历史 IC 只在本地点时快照积累充分后启用。
    """
    missing: list[str] = []
    out: dict = {"ok": True, "ts": ts_str(), "missing": missing, "stocks": [],
                 "weights": RANK_WEIGHTS}
    universe = _hs300_universe()
    if not universe:
        missing.append("沪深300成分(中证官方源不可用)")
        out["analysis_contract"] = stock_analysis_contract.build(
            entry="quant_rank",
            current_stage="deterministic_candidate_selection",
            analysis_executed=False,
            deterministic_method="cross_sectional_zscore",
            deterministic_rule_version="quant-factor-rank-v2",
            data_status="missing",
            expected=300,
            present=0,
            missing=missing,
            decision_authority="candidate_only",
        )
        return out
    quotes = tc.quotes(universe)
    if len(quotes) < len(universe) * 0.8:
        missing.append(f"腾讯成分行情覆盖不足({len(quotes)}/{len(universe)})")
        out["analysis_contract"] = stock_analysis_contract.build(
            entry="quant_rank",
            current_stage="deterministic_candidate_selection",
            analysis_executed=False,
            deterministic_method="cross_sectional_zscore",
            deterministic_rule_version="quant-factor-rank-v2",
            data_status="partial",
            data_as_of=free_source.quote_data_date(),
            expected=len(universe),
            present=len(quotes),
            missing=missing,
            decision_authority="candidate_only",
        )
        return out
    out["data_date"] = free_source.quote_data_date()
    missing.append("免费截面榜的动量字段为当日强弱；20日历史动量待本地点时快照积累")
    rec: dict = {}
    for code in universe:
        q = quotes.get(code)
        if not q:
            continue
        px = q.get("price")
        if px is None or px <= 0:
            continue
        pe, pb = q.get("pe"), q.get("pb")
        if pe is None or pe <= 0:
            # 低估值因子对亏损股没有可比意义；不让缺 PE 的票仅靠低 PB/日内涨幅冲到榜首。
            continue
        rec[code] = {
            "name": q.get("name") or code,
            "code": code,
            "price": round(px, 3),
            "change_pct": q.get("change_pct"),
            "momentum": q.get("change_pct"),
            "pe_ttm": pe,
            "pb": pb,
            "value_pe": (1.0 / pe) if (pe is not None and pe > 0) else None,
            "value_pb": (1.0 / pb) if (pb is not None and pb > 0) else None,
            "turnover": q.get("turnover_rate"),
            "vol_ratio": q.get("volume_ratio"),
            "circ_mv": q.get("float_mv"),
        }
    if not rec:
        missing.append("无可用成分因子")
        out["analysis_contract"] = stock_analysis_contract.build(
            entry="quant_rank",
            current_stage="deterministic_candidate_selection",
            analysis_executed=False,
            deterministic_method="cross_sectional_zscore",
            deterministic_rule_version="quant-factor-rank-v2",
            data_status="partial",
            data_as_of=out.get("data_date"),
            expected=len(universe),
            present=len(quotes),
            missing=missing,
            decision_authority="candidate_only",
        )
        return out

    # 截面 z-score（每因子独立）
    z_mom = _zscores({k: v["momentum"] for k, v in rec.items()})
    z_pe = _zscores({k: v["value_pe"] for k, v in rec.items()})
    z_pb = _zscores({k: v["value_pb"] for k, v in rec.items()})
    z_to_raw = _zscores({k: v["turnover"] for k, v in rec.items()})
    z_turn = {k: -z for k, z in z_to_raw.items()}    # 换手取负 z（低换手得高分）
    z_vr = _zscores({k: v["vol_ratio"] for k, v in rec.items()})

    out_rows = []
    for k, v in rec.items():
        # 只对【有真实数据】的因子加权，并按到场因子权重重新归一——缺失因子不再当中性 0 计入，
        # 否则"没数据"的票会靠一堆 0 分排在"真实利空(负 z)"的票前面（MAJOR：无数据冒充中性）。
        zparts = [
            (RANK_WEIGHTS["momentum"], z_mom[k], v["momentum"]),
            (RANK_WEIGHTS["value_pe"], z_pe[k], v["value_pe"]),
            (RANK_WEIGHTS["value_pb"], z_pb[k], v["value_pb"]),
            (RANK_WEIGHTS["turnover"], z_turn[k], v["turnover"]),
            (RANK_WEIGHTS["vol_ratio"], z_vr[k], v["vol_ratio"]),
        ]
        present = [(w, z) for (w, z, raw) in zparts if raw is not None]
        n_present = len(present)
        wsum = sum(w for w, _ in present)
        composite = (sum(w * z for w, z in present) / wsum) if wsum > 0 else None
        out_rows.append({
            "code": v["code"], "name": v["name"], "price": v["price"],
            "change_pct": v.get("change_pct"),
            "momentum": round(v["momentum"], 2) if v["momentum"] is not None else None,
            "pe_ttm": round(v["pe_ttm"], 2) if v["pe_ttm"] is not None else None,
            "pb": round(v["pb"], 2) if v["pb"] is not None else None,
            "turnover_rate": round(v["turnover"], 2) if v["turnover"] is not None else None,
            "volume_ratio": round(v["vol_ratio"], 2) if v["vol_ratio"] is not None else None,
            "circ_mv": v["circ_mv"],
            "_composite": composite, "_n_present": n_present,
            # 分项 0~100 标准分（缺失显示 null，不填假 50，雷达/展示才如实）
            "factor_scores": {
                "momentum": _z_to_100(z_mom[k]) if v["momentum"] is not None else None,
                "value_pe": _z_to_100(z_pe[k]) if v["value_pe"] is not None else None,
                "value_pb": _z_to_100(z_pb[k]) if v["value_pb"] is not None else None,
                "turnover": _z_to_100(z_turn[k]) if v["turnover"] is not None else None,
                "vol_ratio": _z_to_100(z_vr[k]) if v["vol_ratio"] is not None else None,
            },
        })

    # 完整度门槛：到场因子 <3/5 视为数据不全 → 沉底、不参与置顶（防"没数据"靠中性分冒头）。
    _MIN_PRESENT = 3
    complete = [r for r in out_rows if r["_composite"] is not None and r["_n_present"] >= _MIN_PRESENT]
    incomplete = [r for r in out_rows if not (r["_composite"] is not None and r["_n_present"] >= _MIN_PRESENT)]
    comps = [r["_composite"] for r in complete] or [0.0]
    cmin, cmax = min(comps), max(comps)
    span = (cmax - cmin) or 1.0
    for r in complete:
        r["score"] = round((r["_composite"] - cmin) / span * 100.0, 1)
        r["incomplete"] = False
    for r in incomplete:
        r["score"] = None          # 因子不足不给综合分，不冒充
        r["incomplete"] = True
    for r in out_rows:
        r.pop("_composite", None)
        r.pop("_n_present", None)
    complete.sort(key=lambda r: -r["score"])
    ranked = complete + incomplete          # 数据不全的一律沉底
    out["stocks"] = ranked[:max(1, int(top_n))]
    out["universe_size"] = len(rec)
    if incomplete:
        missing.append(f"{len(incomplete)} 只成分因子不足(<{_MIN_PRESENT}/5)已沉底、不给综合分（防无数据冒充中性排前）")
    out["analysis_contract"] = stock_analysis_contract.build(
        entry="quant_rank",
        current_stage="deterministic_candidate_selection",
        analysis_executed=True,
        deterministic_method="cross_sectional_zscore",
        deterministic_rule_version="quant-factor-rank-v2",
        data_status="complete" if len(quotes) == len(universe) else "partial",
        data_as_of=out.get("data_date"),
        expected=len(universe),
        present=len(quotes),
        missing=missing,
        decision_authority="candidate_only",
    )
    return out


# ------------------------------------------------------------ ② 因子有效性 IC/ICIR/Spearman

ALL_FACTORS = ["momentum", "value_pe", "value_pb", "turnover", "vol_ratio"]
FACTOR_LABELS = {
    "momentum": "当日强弱", "value_pe": "PE便宜度(1/PE)", "value_pb": "PB便宜度(1/PB)",
    "turnover": "自由流通换手", "vol_ratio": "量比",
}


def _factor_at(name: str, code: str, basic: dict, price_hist: dict,
               rebals: list[str], ti: int):
    """取某票在调仓日 rebals[ti] 的因子原始值；缺失 → None（该票本期不参与）。"""
    try:
        if name == "value_pe":
            pe = basic.get("pe_ttm")
            return (1.0 / pe) if (pe is not None and pe > 0) else None
        if name == "value_pb":
            pb = basic.get("pb")
            return (1.0 / pb) if (pb is not None and pb > 0) else None
        if name == "turnover":
            return basic.get("turnover")
        if name == "vol_ratio":
            return basic.get("vol_ratio")
        if name == "momentum":
            if ti < IC_MOM_SPAN:
                return None
            d_now, d_old = rebals[ti], rebals[ti - IC_MOM_SPAN]
            c_now = (price_hist.get(d_now, {}).get(code) or {}).get("close")
            c_old = (price_hist.get(d_old, {}).get(code) or {}).get("close")
            if c_now is None or c_old is None or c_old <= 0:
                return None
            return c_now / c_old - 1.0
    except Exception:
        return None
    return None


@ttl_cache(1800)
def factor_eval(periods: int = 10) -> dict:
    """因子有效性：Spearman IC / ICIR / t / 五分层前向收益。
    universe=沪深300；调仓周频(HOLD=5)；因子值 t 对齐 t+HOLD 前向收益（无未来函数）。"""
    missing: list[str] = []
    out: dict = {"ok": True, "ts": ts_str(), "missing": missing, "factors": [],
                 "params": {"hold": HOLD, "n_layers": N_LAYERS}}
    # 免费公开源可提供当前截面，但无法一次性、可审计地回填过去每个调仓日的
    # 全市场估值/换手截面。必须先由本地 Point-in-Time 仓连续沉淀，样本不足时禁算，
    # 不能拿今天的 PE 倒灌到历史日期制造漂亮 IC。
    out["ok"] = False
    missing.append("因子评估等待本地点时快照积累，禁止用当前估值倒灌历史")
    return out

    try:
        periods = int(periods)
    except Exception:
        periods = 10
    periods = max(3, min(periods, MAX_PERIODS))

    uni = _hs300_universe()
    if not uni:
        missing.append("沪深300成分(因子评估universe缺失)")
        return out
    uni_set = set(uni)

    # 调仓日历：需 periods 个调仓期 + 前置回看(动量) + 末尾前向(HOLD)
    need_rebals = periods + IC_MOM_SPAN + 1
    need_trade_days = need_rebals * HOLD + HOLD + 6
    cal = _trade_days(need_trade_days)
    if len(cal) < (HOLD * 3 + 1):
        missing.append("交易日历不足(因子评估需更长历史)")
        return out

    # 从最新交易日往回每 HOLD 取一个调仓日；最后调仓日须留出 +HOLD 做前向，故止于 cal[-1-HOLD]
    idxs = sorted(range(len(cal) - 1 - HOLD, -1, -HOLD))
    rebals = [cal[i] for i in idxs]
    fwd_date = {cal[i]: cal[i + HOLD] for i in idxs}
    if len(rebals) < periods + 1:
        usable = len(rebals) - IC_MOM_SPAN
        periods = max(3, min(periods, usable))
        if periods < 3:
            missing.append("可评估调仓期不足3期")
            return out

    eval_start = max(len(rebals) - periods, IC_MOM_SPAN)
    eval_idxs = list(range(eval_start, len(rebals)))
    out["data_date"] = f"{rebals[-1][:4]}-{rebals[-1][4:6]}-{rebals[-1][6:]}"

    # 预取价格快照（全部调仓日含前置回看 ∪ 评估期前向日）+ 基本面快照（仅评估调仓日）
    price_dates = set(rebals)
    for ti in eval_idxs:
        fd = fwd_date.get(rebals[ti])
        if fd:
            price_dates.add(fd)
    price_hist: dict = {}
    for d in sorted(price_dates):
        price_hist[d] = _price_snapshot(d)
    basic_hist: dict = {}
    for ti in eval_idxs:
        d = rebals[ti]
        basic_hist[d] = _basic_snapshot(d)

    if not any(price_hist.values()):
        missing.append("历史日线快照全部缺失(因子评估无法计算)")
        return out

    for name in ALL_FACTORS:
        ic_series: list[float] = []
        layer_accum: list[list[float]] = [[] for _ in range(N_LAYERS)]
        n_obs = 0
        for ti in eval_idxs:
            t = rebals[ti]
            fd = fwd_date.get(t)
            if not fd:
                continue
            basic_day = basic_hist.get(t, {})
            price_now = price_hist.get(t, {})
            price_fwd = price_hist.get(fd, {})
            fvals, frets, pairs = [], [], []
            for code in uni_set:
                b = basic_day.get(code, {})
                fv = _factor_at(name, code, b, price_hist, rebals, ti)
                if fv is None:
                    continue
                c0 = (price_now.get(code) or {}).get("close")
                c1 = (price_fwd.get(code) or {}).get("close")
                if c0 is None or c1 is None or c0 <= 0:
                    continue
                fwd = (c1 / c0 - 1.0) * 100.0   # 前向收益(%)
                fvals.append(fv)
                frets.append(fwd)
                pairs.append((fv, fwd))
            if len(fvals) >= N_LAYERS:
                ic = _spearman(fvals, frets)
                if ic is not None:
                    ic_series.append(ic)
                lm = _layer_means(pairs, N_LAYERS)
                for i in range(N_LAYERS):
                    if lm[i] is not None:
                        layer_accum[i].append(lm[i])
                n_obs += len(fvals)

        if ic_series:
            ic_mean = _mean(ic_series)
            ic_sd = _std(ic_series)
            icir = (ic_mean / ic_sd) if ic_sd > 0 else 0.0
            t_stat = icir * math.sqrt(len(ic_series)) if ic_sd > 0 else 0.0
        else:
            ic_mean = icir = t_stat = 0.0
        layers = [round(_mean(layer_accum[i]), 4) if layer_accum[i] else None for i in range(N_LAYERS)]
        out["factors"].append({
            "name": name, "label": FACTOR_LABELS.get(name, name),
            "ic": round(ic_mean, 4), "icir": round(icir, 4), "t_stat": round(t_stat, 4),
            "layers": layers, "n": n_obs, "periods": len(ic_series),
            "long_short": (round(layers[-1] - layers[0], 4)
                           if (layers[0] is not None and layers[-1] is not None) else None),
        })
    out["eval_periods"] = len(eval_idxs)
    if not any(f["periods"] for f in out["factors"]):
        missing.append("有效评估期为0(历史快照缺失或样本不足)")
    return out


# ------------------------------------------------------------ ③ 简单择时回测

def _ma(seq: list[float], n: int) -> list:
    """简单移动均线（前 n-1 个为 None）。"""
    out: list = []
    for i in range(len(seq)):
        if i < n - 1:
            out.append(None)
        else:
            out.append(sum(seq[i - n + 1:i + 1]) / n)
    return out


def _max_drawdown(nav: list[float]) -> float:
    if not nav:
        return 0.0
    peak, mdd = nav[0], 0.0
    for v in nav:
        if v > peak:
            peak = v
        if peak > 0:
            dd = v / peak - 1.0
            if dd < mdd:
                mdd = dd
    return mdd


def _annualized(total_ret: float, n_days: int) -> float:
    if n_days <= 0:
        return 0.0
    base = 1.0 + total_ret
    if base <= 0:
        return -1.0
    try:
        return base ** (TRADING_DAYS / float(n_days)) - 1.0
    except (ValueError, OverflowError):
        return 0.0


def _sharpe(daily_rets: list[float]) -> float:
    m = len(daily_rets)
    if m < 2:
        return 0.0
    mean = sum(daily_rets) / m
    sd = (sum((r - mean) ** 2 for r in daily_rets) / (m - 1)) ** 0.5
    if sd <= 1e-12:
        return 0.0
    return (mean / sd) * (TRADING_DAYS ** 0.5)


def _downsample_idx(n: int, max_points: int = 120) -> list[int]:
    """等距下采样索引，保证含末点。"""
    if n <= max_points or n == 0:
        return list(range(n))
    step = (n - 1) / float(max_points - 1)
    idx = sorted(set(int(round(i * step)) for i in range(max_points)))
    if idx[-1] != n - 1:
        idx[-1] = n - 1
    return idx


@ttl_cache(1800)
def backtest(days: int = 250, fast: int = 20, slow: int = 60) -> dict:
    """沪深300 指数双均线择时 vs 买入持有，真净值回测（无未来函数）。
    策略：第 t 日收盘算出 MA{fast} 与 MA{slow}，MA{fast}>=MA{slow}（多头排列）→ 信号持仓，
    信号【次日(t+1)生效】吃 t+1 当日指数收益；否则空仓。买入持有=指数本身。"""
    missing: list[str] = []
    empty_metrics = {"ann": None, "sharpe": None, "mdd": None, "winrate": None,
                     "trades": 0, "excess": None, "bench_ann": None, "bench_mdd": None}
    out: dict = {"ok": True, "ts": ts_str(), "missing": missing,
                 "dates": [], "nav": [], "bench": [], "signal": [],
                 "metrics": dict(empty_metrics),
                 "params": {"index": HS300_BENCH, "days": days, "fast": fast, "slow": slow},
                 "source": None, "as_of": None,
                 "coverage": {}}
    try:
        days = max(60, min(int(days), BT_MAX_DAYS))
        fast = max(2, min(int(fast), 120))
        slow = max(fast + 1, min(int(slow), 250))
    except Exception:
        days, fast, slow = 250, 20, 60

    out["params"] = {"index": HS300_BENCH, "days": days, "fast": fast, "slow": slow}
    # 要得到完整 days 根回测窗，窗口前还要有 slow 根预热数据来计算首日慢均线。
    # UI 图表接口只返回 160 根，量化计算必须按需独立取数，不能复用其展示截断。
    required_bars = days + slow
    history = _index_daily_history("sh000300", required_bars)
    bars = history.get("rows") if isinstance(history, dict) else None
    out["source"] = history.get("source") if isinstance(history, dict) else None
    compact_as_of = history.get("as_of") if isinstance(history, dict) else None
    out["as_of"] = (
        f"{compact_as_of[:4]}-{compact_as_of[4:6]}-{compact_as_of[6:8]}"
        if compact_as_of and len(compact_as_of) >= 8 else None
    )
    out["coverage"] = {
        "requested_history_bars": required_bars,
        "available_history_bars": int(history.get("available") or 0) if isinstance(history, dict) else 0,
        "returned_history_bars": len(bars or []),
        "requested_window_days": days,
        "actual_window_days": 0,
        "return_days": 0,
    }
    if not bars:
        out["ok"] = False
        missing.append("沪深300指数日线(腾讯/新浪免费公开源均不可用; as_of=未知)")
        return out
    all_dates = [str(b.get("d") or "").replace("-", "")[:8] for b in bars]
    all_closes = [to_float(b.get("c")) for b in bars]

    # 过滤无效收盘
    rows = [(d, c) for d, c in zip(all_dates, all_closes) if c is not None and c > 0]
    if len(rows) < required_bars:
        out["ok"] = False
        missing.append(
            f"指数日线历史不足({len(rows)}/{required_bars}根; "
            f"请求窗口={days}交易日, MA预热={slow}根, as_of={out['as_of'] or '未知'})"
        )
        return out
    dates = [r[0] for r in rows]
    closes = [r[1] for r in rows]

    ma_fast = _ma(closes, fast)
    ma_slow = _ma(closes, slow)
    # 当根「目标持仓」原始信号：多头排列(MA_fast>=MA_slow)=1
    raw_sig = [1 if (ma_fast[i] is not None and ma_slow[i] is not None and ma_fast[i] >= ma_slow[i]) else 0
               for i in range(len(closes))]

    # 截取最近 days 个交易日做回测窗口（但信号用全量算好的 raw_sig，保证窗口首日的次日生效有前值）
    n_all = len(closes)
    w_start = n_all - days
    # 对齐收益日 t（w_start+1 .. n_all-1）：pos_t = raw_sig[t-1]（前一日收盘信号，次日生效→无未来函数）
    seg_dates = dates[w_start:]
    seg_closes = closes[w_start:]
    seg_sig = raw_sig[w_start:]
    if len(seg_closes) < 5:
        out["ok"] = False
        missing.append("回测窗口过短")
        return out

    out["coverage"].update({
        "actual_window_days": len(seg_dates),
        "from": f"{seg_dates[0][:4]}-{seg_dates[0][4:6]}-{seg_dates[0][6:]}",
        "to": f"{seg_dates[-1][:4]}-{seg_dates[-1][4:6]}-{seg_dates[-1][6:]}",
    })

    strat_rets, bench_rets, pos_on_day = [], [], []
    win_hold = hold_days = 0
    for t in range(1, len(seg_closes)):
        c0, c1 = seg_closes[t - 1], seg_closes[t]
        r = (c1 / c0 - 1.0) if (c0 and c0 > 0) else 0.0
        pos = seg_sig[t - 1]   # 前一日(t-1)收盘信号决定今日(t)持仓 → 次日生效
        bench_rets.append(r)
        strat_rets.append(r if pos == 1 else 0.0)
        pos_on_day.append(pos)
        if pos == 1:
            hold_days += 1
            if r > 0:
                win_hold += 1
    if not strat_rets:
        out["ok"] = False
        missing.append("无可用收益日")
        return out

    # 净值（从 1.0 起，对齐 seg_dates[0]）
    nav = [1.0]
    for r in strat_rets:
        nav.append(nav[-1] * (1.0 + r))
    bench = [1.0]
    for r in bench_rets:
        bench.append(bench[-1] * (1.0 + r))
    # 信号曲线（每根 K 的目标持仓，前端可标注持仓段）
    signal = list(seg_sig)

    n_days = len(strat_rets)
    out["coverage"]["return_days"] = n_days
    strat_total, bench_total = nav[-1] - 1.0, bench[-1] - 1.0
    trades, prev = 0, 0
    for p in pos_on_day:
        if p != prev:
            trades += 1
        prev = p

    out["metrics"] = {
        "ann": round(_annualized(strat_total, n_days), 4),
        "sharpe": round(_sharpe(strat_rets), 4),
        "mdd": round(_max_drawdown(nav), 4),
        "winrate": round(win_hold / hold_days, 4) if hold_days > 0 else 0.0,
        "trades": trades,
        "excess": round(strat_total - bench_total, 4),
        "bench_ann": round(_annualized(bench_total, n_days), 4),
        "bench_mdd": round(_max_drawdown(bench), 4),
        "total_return": round(strat_total, 4),
        "bench_total_return": round(bench_total, 4),
        "hold_ratio": round(sum(signal) / len(signal), 4) if signal else 0.0,
    }
    out["data_date"] = f"{seg_dates[-1][:4]}-{seg_dates[-1][4:6]}-{seg_dates[-1][6:]}"

    # 下采样到 <=120 点（四条曲线同一组索引保持对齐）
    idx = _downsample_idx(len(seg_dates), 120)
    def iso(d: str) -> str:
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"

    out["dates"] = [iso(seg_dates[i]) for i in idx]
    out["nav"] = [round(nav[i], 4) for i in idx]
    out["bench"] = [round(bench[i], 4) for i in idx]
    out["signal"] = [signal[i] for i in idx]
    return out
