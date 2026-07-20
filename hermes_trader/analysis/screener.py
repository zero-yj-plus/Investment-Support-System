"""条件 / 多因子选股 —— 基于腾讯/通达信免费全市场快照。

腾讯当前/最近收盘快照一次给出价格、涨跌幅、成交额、换手、量比、估值和市值；通达信/
公开代码表提供全市场分母、名称和板块。快照必须达到 80% 覆盖率才允许进入筛选，避免把
局部成功冒充全市场。行业不在 5500 只热路径逐票抓取：行业条件按板块成份查询，普通结果
仅对返回候选低频补全。

单位统一为元/%/倍；拿不到的数据进入 missing 或字段留 None，绝不编造。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

from ..data import free_source as tx
from ..data import tdx_api_source as tdx_api
from ..data import tencent_source as tc
from ..data.base import ts_str, ttl_cache
from . import stock_analysis_contract

# 单只可选因子的过滤上限保护：理论无上限（返回全部命中），但全市场约 5500 只，
# 这里仅作为 result_limit 默认（前端表格渲染上限，可由参数调大），不是筛选硬截断。
DEFAULT_RESULT_LIMIT = 300
MIN_MARKET_COVERAGE = 0.80
CANDIDATE_INDUSTRY_LIMIT = 12
CANDIDATE_INDUSTRY_WORKERS = 6
_INDUSTRY_SUCCESS_TTL = 7 * 86400.0
_INDUSTRY_FAILURE_TTL = 3600.0
_INDUSTRY_CACHE: dict[str, tuple[float, str | None]] = {}
_INDUSTRY_CACHE_LOCK = threading.Lock()

# 板块过滤白名单（stock_basic.market 的取值）：主板/创业板/科创板/北交所。
VALID_BOARDS = {"主板", "创业板", "科创板", "北交所"}


def _resolve_trade_day() -> str:
    """腾讯当前快照的真实归属日（YYYYMMDD）。

    盘中腾讯是当日实时行情，不能沿用旧日级源逻辑回退成昨日；盘前/休市则由免费源统一
    返回最近交易日，避免盖自然日。
    """
    return tx.quote_data_date().replace("-", "")


@ttl_cache(1800, fail_seconds=60)
def _stock_basic_map() -> dict[str, dict]:
    """全市场股票基础信息 code(6位)→{name, industry, board}（30m 缓存）。
    失败返空 dict，调用方据此把 name/industry/board 标为缺失。"""
    names = tx.stock_universe() or {}

    def board_of(code: str) -> str:
        if code.startswith("30"):
            return "创业板"
        if code.startswith("68"):
            return "科创板"
        if code.startswith(("4", "8", "92")):
            return "北交所"
        return "主板"

    return {
        code: {"name": name, "industry": None, "board": board_of(code)}
        for code, name in names.items()
    }


@ttl_cache(3600)
def _industry_members(keyword: str) -> tuple[set[str], dict[str, str]]:
    """行业筛选只认远程 TDX 目录与成分关系，不接网页/F10 回退。"""
    catalog = tdx_api.sector_catalog() or {}
    sectors = [
        row for row in catalog.values()
        if isinstance(row, dict)
        and row.get("kind") == "industry"
        and keyword in str(row.get("name") or "")
    ][:8]
    if not sectors:
        return set(), {}
    memberships = tdx_api.sector_members_for_codes(
        [str(row.get("code") or "") for row in sectors]
    )
    if not memberships:
        return set(), {}
    codes: set[str] = set()
    mapping: dict[str, str] = {}
    by_sector = memberships.get("by_sector") or {}
    for sector in sectors:
        sector_code = str(sector.get("code") or "")
        industry = str(sector.get("name") or "")
        for raw_code in by_sector.get(sector_code) or []:
            code = str(raw_code or "").split(".", 1)[0].zfill(6)
            if len(code) == 6 and code.isdigit():
                codes.add(code)
                mapping.setdefault(code, industry)
    return codes, mapping


def _coverage(received: int, expected: int) -> dict:
    ratio = received / expected if expected else 0.0
    return {
        "received": received,
        "expected": expected,
        "ratio": round(ratio, 4),
        # 门槛边界至少保留两位，避免 79.97% 显示成 80.0% 却判不通过。
        "percent": round(ratio * 100, 2),
        "minimum_ratio": MIN_MARKET_COVERAGE,
        "sufficient": expected >= 3000 and ratio >= MIN_MARKET_COVERAGE,
    }


def _industry_cache_get(code: str) -> tuple[bool, str | None]:
    with _INDUSTRY_CACHE_LOCK:
        hit = _INDUSTRY_CACHE.get(code)
        if hit is None:
            return False, None
        stored_at, value = hit
        ttl = _INDUSTRY_SUCCESS_TTL if value else _INDUSTRY_FAILURE_TTL
        if time.monotonic() - stored_at < ttl:
            return True, value
        _INDUSTRY_CACHE.pop(code, None)
    return False, None


def _industry_cache_put(code: str, value: str | None) -> None:
    with _INDUSTRY_CACHE_LOCK:
        _INDUSTRY_CACHE[code] = (time.monotonic(), value)


def _enrich_candidate_industries(stocks: list[dict]) -> dict:
    """只对返回候选补行业，避免全市场 5500 只逐票打公开 F10。

    tx.industry 自带 24h 成功/失败缓存；这里再限制每次至多 12 只并发查询。未查询或上游失败
    的候选继续保留 None，并把补全覆盖率交给调用方展示。
    """
    unresolved: list[dict] = []
    for stock in stocks:
        if stock.get("industry"):
            continue
        code = str(stock.get("code") or "")
        cached, value = _industry_cache_get(code)
        if cached:
            if value:
                stock["industry"] = value
        else:
            unresolved.append(stock)

    # 成功和短期失败都记在本地候选缓存里。下一次相同榜单会跳过已尝试标的，继续补下一批，
    # 而不是永远只查询榜首 12 只。
    targets = unresolved[:CANDIDATE_INDUSTRY_LIMIT]
    if targets:
        def lookup(code: str) -> tuple[str, str | None]:
            try:
                return code, tx.industry(code)
            except Exception:  # noqa: BLE001 - 行业补充失败不能拖垮整个筛选结果
                return code, None

        workers = min(CANDIDATE_INDUSTRY_WORKERS, len(targets))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            values = executor.map(lookup, [str(stock.get("code") or "") for stock in targets])
            for stock, (code, industry) in zip(targets, values):
                _industry_cache_put(code, industry)
                if industry:
                    stock["industry"] = industry

    filled = sum(bool(stock.get("industry")) for stock in stocks)
    total = len(stocks)
    return {
        "filled": filled,
        "returned": total,
        "queried": len(targets),
        "query_limit": CANDIDATE_INDUSTRY_LIMIT,
        "percent": round(filled / total * 100, 1) if total else 100.0,
    }


@ttl_cache(300)
def _market_snapshot(trade_date: str) -> dict:
    """腾讯免费全市场行情/估值快照（5 分钟缓存）。
    返回 {"ok": bool, "rows": [ {code,name,industry,board,price,change_pct,turnover_rate,
    volume_ratio,pe,pe_ttm,pb,total_mv,circ_mv,amount} ... ], "missing": [...], "trade_date": iso}。
    单位统一为元/%/倍。请求历史日期时仍只能返回当前或最近收盘快照，必须显式标注。"""
    missing: list[str] = []
    compact = str(trade_date or "").replace("-", "")[:8]
    requested_iso = (
        f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
        if len(compact) == 8 and compact.isdigit()
        else str(trade_date or "")
    )
    data_date = tx.quote_data_date()
    out: dict = {
        "ok": True,
        "rows": [],
        "missing": missing,
        "trade_date": data_date,
        "coverage": _coverage(0, 0),
    }
    if requested_iso != data_date:
        missing.append(f"请求日期{requested_iso}无历史截面，返回腾讯快照实际归属日{data_date}")

    basic = _stock_basic_map()
    universe = tx.stock_universe_status()
    out["universe"] = universe
    if universe.get("stale"):
        missing.append(
            f"股票池为陈旧快照(as_of={universe.get('as_of') or '未知'})"
        )
    missing_exchanges = list(universe.get("missing_exchanges") or [])
    if universe.get("refresh_required") or missing_exchanges:
        detail = "/".join(str(item).upper() for item in missing_exchanges) or "部分交易所"
        missing.append(f"股票池待刷新，当前缺失或未完整：{detail}")
    out["coverage"] = _coverage(0, len(basic))
    if len(basic) < 3000:
        missing.append("免费股票代码表不完整(通达信/公开HTTP均不可用)")
        out["ok"] = False
        return out
    quotes = {
        code: quote for code, quote in tc.quotes(list(basic)).items()
        if code in basic
    }
    coverage = _coverage(len(quotes), len(basic))
    out["coverage"] = coverage
    if not coverage["sufficient"]:
        missing.append(
            f"腾讯全市场快照覆盖不足：{coverage['received']}/{coverage['expected']}"
            f"（{coverage['percent']}%，最低要求{MIN_MARKET_COVERAGE:.0%}）"
        )
        out["ok"] = False
    elif len(quotes) < len(basic):
        missing.append(
            f"腾讯全市场快照部分缺失：{coverage['received']}/{coverage['expected']}"
            f"（覆盖率{coverage['percent']}%，已达到{MIN_MARKET_COVERAGE:.0%}可用门槛）"
        )
    rows = []
    for code, q in quotes.items():
        b = basic.get(code) or {}
        rows.append({
            "code": code,
            "name": q.get("name") or b.get("name") or None,
            "industry": b.get("industry"),
            "board": b.get("board"),
            "price": q.get("price"),
            "change_pct": q.get("change_pct"),
            "turnover_rate": q.get("turnover_rate"),
            "volume_ratio": q.get("volume_ratio"),
            "pe": q.get("pe"),
            "pe_ttm": q.get("pe"),
            "pb": q.get("pb"),
            "total_mv": q.get("total_mv"),
            "circ_mv": q.get("float_mv"),
            "amount": q.get("amount"),
        })
    out["rows"] = rows
    return out


def _passes(stock: dict, f: dict) -> bool:
    """单只股票是否命中全部过滤条件。条件缺失（None）即不约束该维度。
    任一被约束维度的数据缺失（个股该字段为 None）视为不命中——绝不放过无法验证的标的。"""
    # PE 区间（用 pe_ttm 优先，回退 pe）：pe_min / pe_max
    pe = stock.get("pe_ttm")
    if pe is None:
        pe = stock.get("pe")
    if f.get("pe_min") is not None:
        if pe is None or pe < f["pe_min"]:
            return False
    if f.get("pe_max") is not None:
        if pe is None or pe > f["pe_max"]:
            return False
    # 排除亏损（PE<=0）：exclude_loss=True 时剔除 PE 非正或缺失
    if f.get("exclude_loss"):
        if pe is None or pe <= 0:
            return False
    # PB 区间
    pb = stock.get("pb")
    if f.get("pb_min") is not None and (pb is None or pb < f["pb_min"]):
        return False
    if f.get("pb_max") is not None and (pb is None or pb > f["pb_max"]):
        return False
    # 换手率 >（%）
    tr = stock.get("turnover_rate")
    if f.get("turnover_min") is not None and (tr is None or tr < f["turnover_min"]):
        return False
    if f.get("turnover_max") is not None and (tr is None or tr > f["turnover_max"]):
        return False
    # 量比 >
    vr = stock.get("volume_ratio")
    if f.get("volume_ratio_min") is not None and (vr is None or vr < f["volume_ratio_min"]):
        return False
    # 流通市值区间（亿元入参，内部已转元）
    cmv = stock.get("circ_mv")
    if f.get("circ_mv_min") is not None and (cmv is None or cmv < f["circ_mv_min"]):
        return False
    if f.get("circ_mv_max") is not None and (cmv is None or cmv > f["circ_mv_max"]):
        return False
    # 涨跌幅区间（%）
    chg = stock.get("change_pct")
    if f.get("chg_min") is not None and (chg is None or chg < f["chg_min"]):
        return False
    if f.get("chg_max") is not None and (chg is None or chg > f["chg_max"]):
        return False
    # 成交额 >（亿元入参，内部已转元）
    amt = stock.get("amount")
    if f.get("amount_min") is not None and (amt is None or amt < f["amount_min"]):
        return False
    # 板块（主板/创业板/科创板/北交所，多选）
    boards = f.get("boards")
    if boards:
        if stock.get("board") not in boards:
            return False
    # 行业关键词（包含匹配，单个字符串）
    kw = f.get("industry_kw")
    if kw:
        codes = f.get("_industry_codes")
        ind = stock.get("industry") or ""
        if (codes and stock.get("code") not in codes) or (not codes and kw not in ind):
            return False
    # 排除 ST / 退市
    if f.get("exclude_st", True):
        name = (stock.get("name") or "").upper()
        if "ST" in name or "退" in (stock.get("name") or ""):
            return False
    return True


# 排序字段白名单：值越大越靠前的字段（默认按成交额降序，最活跃在前）。
_SORT_FIELDS = {
    "amount", "turnover_rate", "volume_ratio", "change_pct",
    "circ_mv", "total_mv", "pe", "pe_ttm", "pb",
}


@ttl_cache(600)
def market_daily_history(n: int = 3) -> dict:
    """最近 n 个交易日全市场日行情（每个交易日一次 pro.daily 全市场调用、非逐股；10 分钟缓存）。
    返回 {ok, trade_dates:[ISO 旧→新], by_code:{code:[{date,open,high,low,close,pct_chg,vol} 旧→新]}, missing}。
    供多日动量/涨停/阳线类【确定性】筛选用（数字真实、缺权限标 missing、绝不编造）。母项目通用工具。"""
    return {
        "ok": False,
        "trade_dates": [],
        "by_code": {},
        "missing": ["免费全市场多日截面尚未形成足够本地历史"],
    }


def screen(filters: dict | None = None, sort_by: str = "amount",
           sort_desc: bool = True, result_limit: int = DEFAULT_RESULT_LIMIT) -> dict:
    """条件选股主入口。

    filters 可选键（任一为 None/缺省即不约束该维度；市值/成交额入参单位为【亿元】，函数内转元）：
      pe_min / pe_max          市盈率(TTM优先)区间
      pb_min / pb_max          市净率区间
      exclude_loss             True=剔除亏损股(PE<=0)
      turnover_min / _max      换手率(%)区间
      volume_ratio_min         量比下限
      circ_mv_min / _max       流通市值(亿元)区间
      chg_min / chg_max        涨跌幅(%)区间
      amount_min               成交额(亿元)下限
      boards                   板块列表(主板/创业板/科创板/北交所)
      industry_kw              行业关键词(包含匹配)
      exclude_st               剔除 ST/退市(默认 True)

    返回 {ok, ts, trade_date, coverage, total_market, matched(命中总数), returned(本次返回数),
          industry_coverage, sort_by, missing, stocks:[...]}。
    命中 stocks 已按 sort_by 排序并截到 result_limit。"""
    t0 = time.monotonic()
    f = dict(filters or {})
    # 入参单位换算：亿元 → 元（前端表单填亿元，内部统一元口径比对）
    for k_in, k_norm in (("circ_mv_min", "circ_mv_min"), ("circ_mv_max", "circ_mv_max")):
        if f.get(k_in) is not None:
            f[k_norm] = float(f[k_in]) * 1e8
    if f.get("amount_min") is not None:
        f["amount_min"] = float(f["amount_min"]) * 1e8
    # 板块白名单清洗（防注入无效值）
    if f.get("boards"):
        f["boards"] = [b for b in f["boards"] if b in VALID_BOARDS] or None
    industry_map: dict[str, str] = {}
    if f.get("industry_kw"):
        codes, industry_map = _industry_members(str(f["industry_kw"]))
        f["_industry_codes"] = codes

    out: dict = {"ok": True, "ts": ts_str(), "missing": [], "stocks": [],
                 "total_market": 0, "matched": 0, "returned": 0,
                 "coverage": _coverage(0, 0),
                 "industry_coverage": {
                     "filled": 0, "returned": 0, "queried": 0,
                     "query_limit": CANDIDATE_INDUSTRY_LIMIT, "percent": 100.0,
                 },
                 "sort_by": sort_by if sort_by in _SORT_FIELDS else "amount"}

    trade_date = _resolve_trade_day()
    snap = _market_snapshot(trade_date)
    out["trade_date"] = snap.get("trade_date")
    out["coverage"] = dict(snap.get("coverage") or _coverage(0, 0))
    out["universe"] = dict(snap.get("universe") or {})
    out["missing"] = list(snap.get("missing", []))
    # 快照带 TTL 缓存；筛选/行业补全必须复制，不能把一次查询的行业条件污染进后续请求。
    rows = [dict(row) for row in (snap.get("rows") or [])]
    if industry_map:
        for row in rows:
            row["industry"] = industry_map.get(row.get("code"))
    if f.get("industry_kw") and not f.get("_industry_codes"):
        out["missing"].append("行业成份公开源不可用或未匹配到该行业")
    out["total_market"] = len(rows)
    if not rows or snap.get("ok") is False:
        out["ok"] = bool(rows) and snap.get("ok", True)
        if not rows and not out["missing"]:
            out["missing"].append("全市场快照为空")
        out["analysis_contract"] = stock_analysis_contract.build(
            entry="condition_screen",
            current_stage="deterministic_candidate_selection",
            analysis_executed=False,
            deterministic_method="user_filters+stable_sort",
            deterministic_rule_version="condition-screen-v2",
            data_status="missing",
            data_as_of=out.get("trade_date"),
            expected=(out.get("coverage") or {}).get("expected"),
            present=(out.get("coverage") or {}).get("received"),
            missing=out["missing"],
            decision_authority="candidate_only",
        )
        return out

    matched = [s for s in rows if _passes(s, f)]
    out["matched"] = len(matched)

    # 排序：白名单字段。None 值统一沉底（无论升降序，缺该指标的标的都排最后，不挤占榜首/榜尾）。
    # 主键 (s[sb] is None) 升序保证 None 组永远在后；次键对有值部分按方向排（降序取负）。
    sb = out["sort_by"]
    sign = -1 if sort_desc else 1
    matched.sort(key=lambda s: (s.get(sb) is None,
                                sign * s.get(sb) if s.get(sb) is not None else 0))

    limit = max(1, int(result_limit or DEFAULT_RESULT_LIMIT))
    out["stocks"] = matched[:limit]
    out["industry_coverage"] = _enrich_candidate_industries(out["stocks"])
    out["returned"] = len(out["stocks"])
    industry_coverage = out["industry_coverage"]
    if industry_coverage["filled"] < industry_coverage["returned"]:
        out["missing"].append(
            "行业仅对返回候选低频补全："
            f"{industry_coverage['filled']}/{industry_coverage['returned']}只已有行业，"
            f"本次最多查询{CANDIDATE_INDUSTRY_LIMIT}只，未取到者保留为空"
        )
    out["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    coverage = out.get("coverage") or {}
    out["analysis_contract"] = stock_analysis_contract.build(
        entry="condition_screen",
        current_stage="deterministic_candidate_selection",
        analysis_executed=True,
        deterministic_method="user_filters+stable_sort",
        deterministic_rule_version="condition-screen-v2",
        data_status="complete" if coverage.get("sufficient") else "partial",
        data_as_of=out.get("trade_date"),
        expected=coverage.get("expected"),
        present=coverage.get("received"),
        missing=out["missing"],
        decision_authority="candidate_only",
    )
    return out


if __name__ == "__main__":
    # 自测：真 token 跑一组条件，打印命中数与样本（单位对账）。
    import json
    r = screen(filters={
        "pe_min": 0, "pe_max": 60, "turnover_min": 3, "volume_ratio_min": 1.0,
        "circ_mv_min": 50, "circ_mv_max": 1000, "chg_min": 0,
        "boards": ["主板", "创业板"], "exclude_st": True,
    }, sort_by="amount", result_limit=10)
    print(f"trade_date={r.get('trade_date')} total_market={r.get('total_market')} "
          f"matched={r.get('matched')} returned={r.get('returned')} elapsed={r.get('elapsed_ms')}ms")
    print("missing:", r.get("missing"))
    for s in r["stocks"][:5]:
        print(f"  {s['code']} {s['name']:<6} 涨{s['change_pct']}% 换{s['turnover_rate']}% "
              f"量比{s['volume_ratio']} PE_TTM={s['pe_ttm']} 流通{(s['circ_mv'] or 0)/1e8:.1f}亿 "
              f"成交{(s['amount'] or 0)/1e8:.2f}亿 [{s['board']}/{s['industry']}]")
