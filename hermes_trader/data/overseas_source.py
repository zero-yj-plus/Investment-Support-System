"""美股 / 港股 个股实时行情 + 基础财务——【真数据，绝不编造】。

定位：本产品原以 A 股为主，海外仅在「多市场新闻」里给了港美【指数/几只科技股】的只读快照
（news.py）。本模块把那套能力上升为【可按代码查询的个股页】，给陪跑客户一个查 AAPL / 00700
的实时行情 + 基础财务的入口。两条腿、各自独立降级：

  ① 实时行情：复用 tencent_source.us_quotes / hk_quotes（qt.gtimg.cn，全球 CDN，
     客户机东财 push2 被封时仍可达）。输入 'AAPL' / '00700' → 价/涨跌/市值/PE/成交额。
     港股个股此前 tencent_source 只封装了【指数】(hk_index_quotes)，这里补上【个股】解析
     (hk_quotes)——列位与美股不同，下标以 qt.gtimg.cn 真实返回校准（见 tencent_source）。

  ② 基础财务：akshare 东财财务接口（us/hk analysis_indicator）。这是【东财源】，客户机
     （境内、东财 IP 被封）多半取不到 → 按铁律标 missing、返回 financials=None，绝不编造、
     绝不拿行情字段冒充财务。海外开发机 / 中继可达时给真实年报指标（营收/净利/EPS/毛利率/
     ROE/资产负债率…，含同比）。

单位与口径：价/成交额/市值=该市场本币（美元 / 港元），不强转人民币（避免拼汇率制造假精度）；
涨跌幅=%，与 A 股口径一致（前端 pctClass 红涨绿跌沿用本产品统一规则）。财务金额=本币元。
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .base import get_ak, now_cn, safe, to_float, to_int, ts_str, ttl_cache
from . import tencent_source as tc

# 行情 TTL：盘中也只需准实时，10s 足够且护住 qt.gtimg.cn；财务变化以季/年计，缓存 6h。
_QUOTE_TTL = 10
_FIN_TTL = 6 * 3600

_US_RE = re.compile(r"^[A-Za-z][A-Za-z.\-]{0,9}$")   # 美股 ticker：字母开头，允许 . -（BRK.B 等）
_HK_RE = re.compile(r"^\d{1,5}$")                     # 港股：1~5 位数字（输入 700 / 00700 均可）


def _parse_quote_as_of(raw: str | None, tz: ZoneInfo) -> datetime | None:
    text = str(raw or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def _market_timing(market: str, raw_as_of: str | None, *, now: datetime | None = None) -> dict:
    """把“HTTP 抓取时刻”与“交易所行情时刻”分开。

    只在当地交易时段且行情时间足够新时标记盘中；节假日等无法由
    简单周历判断的情况，会落到“休市或行情延迟”，不冒充实时。
    """
    if market == "us":
        tz = ZoneInfo("America/New_York")
        market_name = "纽约"
    else:
        tz = ZoneInfo("Asia/Hong_Kong")
        market_name = "香港"
    local_now = (now or now_cn()).astimezone(tz)
    observed = _parse_quote_as_of(raw_as_of, tz)
    hm = local_now.hour * 60 + local_now.minute
    weekday = local_now.weekday() < 5
    in_session = False
    if not weekday:
        session = "休市"
    elif market == "us":
        in_session = 570 <= hm < 960
        session = "盘中" if in_session else ("未开盘" if hm < 570 else "已收盘")
    else:
        in_session = 570 <= hm < 720 or 780 <= hm < 960
        if in_session:
            session = "盘中"
        elif hm < 570:
            session = "未开盘"
        elif 720 <= hm < 780:
            session = "午间休市"
        else:
            session = "已收盘"
    age_s = (local_now - observed).total_seconds() if observed else None
    live = bool(in_session and age_s is not None and -120 <= age_s <= 15 * 60)
    if in_session and not live:
        session = "休市或行情延迟"
    return {
        "market_tz": str(tz),
        "market_name": market_name,
        "market_session": session,
        "is_live": live,
        "quote_as_of": observed.strftime("%Y-%m-%d %H:%M:%S") if observed else None,
        "data_date": observed.strftime("%Y-%m-%d") if observed else None,
    }


def normalize_code(market: str, code: str) -> str | None:
    """把用户输入归一成各市场标准代码；非法返回 None（由调用方拒绝，不瞎猜）。
    美股 → 大写 ticker（AAPL）；港股 → 5 位零填充（00700）。"""
    code = (code or "").strip()
    if market == "us":
        c = code.upper()
        return c if _US_RE.match(c) else None
    if market == "hk":
        if not _HK_RE.match(code):
            return None
        return code.zfill(5)
    return None


# ---------------------------------------------------------------------------
# ① 实时行情（腾讯 qt.gtimg.cn，独立于东财，客户机可达）
# ---------------------------------------------------------------------------

@ttl_cache(_QUOTE_TTL)
def quote(market: str, code: str) -> dict:
    """单只港/美个股实时行情。返回契约：
      {ok, market, code, ts, missing:[...], quote:{name,price,change_pct,open,high,low,
       prev_close,amount,volume,pe,total_mv,currency}}
    取不到（腾讯空响应/代码非法）→ ok=False + missing，quote=None；绝不抛、绝不编造。"""
    norm = normalize_code(market, code)
    out: dict = {"ok": False, "market": market, "code": norm or code,
                 "ts": ts_str(), "missing": [], "quote": None}
    if norm is None:
        out["missing"].append("代码格式" + ("（美股形如 AAPL）" if market == "us" else "（港股形如 00700）"))
        return out

    if market == "us":
        sym = "us" + norm
        data = tc.us_quotes([sym])         # {sym: {name,price,change_pct,open,high,low,prev_close,amount,volume,pe,total_mv}}
        currency = "USD"
    elif market == "hk":
        sym = "hk" + norm
        data = hk_quotes([sym])
        currency = "HKD"
    else:
        out["missing"].append("未知市场（仅支持 us / hk）")
        return out

    q = data.get(sym)
    if not q or q.get("price") is None:
        out["missing"].append("实时行情（腾讯 qt.gtimg.cn 空响应，或该代码不存在/停牌）")
        return out

    out["ok"] = True
    out.update(_market_timing(market, q.get("as_of")))
    out["quote"] = {
        "name": q.get("name") or "",
        "code": norm,
        "price": q.get("price"),
        "change_pct": q.get("change_pct"),
        "open": q.get("open"),
        "high": q.get("high"),
        "low": q.get("low"),
        "prev_close": q.get("prev_close"),
        "amount": q.get("amount"),          # 本币元（美元/港元）
        "volume": q.get("volume"),          # 股
        "pe": q.get("pe"),
        "total_mv": q.get("total_mv"),      # 本币元
        "currency": currency,
        "as_of": q.get("as_of"),
    }
    # 港股个股市值/PE 腾讯多数能给；给不全时如实标注（仍展示已有字段，缺值前端显示 —）
    miss = [lbl for lbl, key in (("市盈率", "pe"), ("总市值", "total_mv")) if out["quote"].get(key) is None]
    if miss:
        out["missing"].append("/".join(miss) + "（腾讯未提供）")
    return out


def hk_quotes(symbols: list[str]) -> dict[str, dict]:
    """批量港股【个股】实时 → {symbol: {name,price,change_pct,...}}。
    symbol 形如 'hk00700'。失败/空返回 {}（不抛）。

    为什么不直接复用 tencent_source.hk_index_quotes：那只解析【指数】(仅取 price/change/amount)。
    港股【个股】qt.gtimg.cn 多给市值/PE 等列，且与美股下标布局不同，需单独解析。
    下标以 q=hk00700 真实返回校准（与美股 us_quotes 同列含义比对得出）：
      f[1]=名称  f[3]=现价  f[4]=昨收  f[5]=今开  f[6]=成交量(股)
      f[32]=涨跌幅%  f[33]=最高  f[34]=最低  f[37]=成交额(本币元)
      f[39]=市盈率TTM  f[45]=总市值(亿港元→×1e8)
    单位：price=港元、amount=港元(f[37] 已是元)、market cap=港元(亿港元×1e8)。"""
    out: dict[str, dict] = {}
    raw = tc._fetch(list(symbols))         # 复用腾讯抓取（强制直连绕代理、GBK 解码、合理性已在此层之外）
    for sym, f in raw.items():
        if len(f) < 38:
            continue
        price = to_float(f[3])
        if price is None or price <= 0:
            continue
        total_yi = to_float(f[45]) if len(f) > 45 else None     # 总市值(亿港元)
        out[sym] = {
            "name": f[1] if len(f) > 1 else "",
            "price": price,
            "change_pct": to_float(f[32]) if len(f) > 32 else None,
            "open": to_float(f[5]) if len(f) > 5 else None,
            "high": to_float(f[33]) if len(f) > 33 else None,
            "low": to_float(f[34]) if len(f) > 34 else None,
            "prev_close": to_float(f[4]) if len(f) > 4 else None,
            "amount": to_float(f[37]) if len(f) > 37 else None,         # 港元（已是元）
            "volume": to_int(f[6]) if len(f) > 6 else None,             # 股
            "pe": to_float(f[39]) if len(f) > 39 else None,            # 市盈率TTM
            "total_mv": total_yi * 1e8 if total_yi is not None else None,   # 亿港元→港元
            "as_of": f[30] if len(f) > 30 else None,
        }
    return out


# ---------------------------------------------------------------------------
# ② 基础财务（akshare 东财源；客户机东财被封 → 标 missing，绝不编造）
# ---------------------------------------------------------------------------

# 统一财务字段：东财 us / hk analysis_indicator 列名 → 本产品归一键 + 中文标签 + 类型。
# 类型 money=本币金额(元), pct=百分比(%), ratio=倍数, num=纯数值(EPS 等)。
_US_FIN_MAP = [
    ("OPERATE_INCOME", "revenue", "营业收入", "money"),
    ("OPERATE_INCOME_YOY", "revenue_yoy", "营收同比", "pct"),
    ("PARENT_HOLDER_NETPROFIT", "net_profit", "归母净利润", "money"),
    ("PARENT_HOLDER_NETPROFIT_YOY", "net_profit_yoy", "净利同比", "pct"),
    ("GROSS_PROFIT_RATIO", "gross_margin", "毛利率", "pct"),
    ("NET_PROFIT_RATIO", "net_margin", "净利率", "pct"),
    ("BASIC_EPS", "eps", "每股收益", "num"),
    ("ROE_AVG", "roe", "净资产收益率", "pct"),
    ("ROA", "roa", "总资产收益率", "pct"),
    ("DEBT_ASSET_RATIO", "debt_ratio", "资产负债率", "pct"),
    ("CURRENT_RATIO", "current_ratio", "流动比率", "ratio"),
]
_HK_FIN_MAP = [
    ("OPERATE_INCOME", "revenue", "营业收入", "money"),
    ("OPERATE_INCOME_YOY", "revenue_yoy", "营收同比", "pct"),
    ("HOLDER_PROFIT", "net_profit", "股东净利润", "money"),
    ("HOLDER_PROFIT_YOY", "net_profit_yoy", "净利同比", "pct"),
    ("GROSS_PROFIT_RATIO", "gross_margin", "毛利率", "pct"),
    ("NET_PROFIT_RATIO", "net_margin", "净利率", "pct"),
    ("BASIC_EPS", "eps", "每股收益", "num"),
    ("BPS", "bps", "每股净资产", "num"),
    ("ROE_AVG", "roe", "净资产收益率", "pct"),
    ("ROA", "roa", "总资产收益率", "pct"),
    ("DEBT_ASSET_RATIO", "debt_ratio", "资产负债率", "pct"),
    ("CURRENT_RATIO", "current_ratio", "流动比率", "ratio"),
]


# 东财财务接口的 CURRENCY 字段中英混杂（美股给 '美元'、港股给 'HKD'）。归一成稳定货币码，
# 让前端与任何下游（AI 注入）拿到一致 token，不必各自猜中文。未知则原样透传。
_CCY_MAP = {"美元": "USD", "港元": "HKD", "港币": "HKD", "人民币": "CNY",
            "USD": "USD", "HKD": "HKD", "CNY": "CNY", "RMB": "CNY"}


def _norm_ccy(raw: str, default: str) -> str:
    raw = str(raw or "").strip()
    return _CCY_MAP.get(raw, raw or default)


def _fin_period(row: dict) -> str:
    """报告期标签：取 REPORT_DATE 的年份（如 '2025年报'）。"""
    rd = str(row.get("REPORT_DATE") or "").strip()
    m = re.match(r"^(\d{4})", rd)
    return (m.group(1) + " 年报") if m else (rd[:10] or "最新年报")


def _build_metrics(row: dict, fmap) -> list[dict]:
    metrics = []
    for col, key, label, typ in fmap:
        v = to_float(row.get(col))
        metrics.append({"key": key, "label": label, "type": typ, "value": v})
    return metrics


@ttl_cache(_FIN_TTL)
def financials(market: str, code: str) -> dict:
    """港/美个股基础财务（最新年报 + 标签字段）。返回契约：
      {ok, market, code, ts, missing:[...], currency, period,
       metrics:[{key,label,type,value}], name}
    数据源为东财 akshare：客户机东财被封时取不到 → ok=False + missing，metrics=[]；
    绝不用行情/估算冒充财务（铁律）。"""
    norm = normalize_code(market, code)
    out: dict = {"ok": False, "market": market, "code": norm or code, "ts": ts_str(),
                 "missing": [], "currency": None, "period": None, "metrics": [], "name": ""}
    if norm is None:
        out["missing"].append("代码格式" + ("（美股形如 AAPL）" if market == "us" else "（港股形如 00700）"))
        return out
    ak = get_ak()
    if ak is None:
        out["missing"].append("财务（akshare 未安装）")
        return out

    if market == "us":
        df = safe(ak.stock_financial_us_analysis_indicator_em, symbol=norm, indicator="年报",
                  label="美股财务")
        fmap, currency_default = _US_FIN_MAP, "USD"
    elif market == "hk":
        df = safe(ak.stock_financial_hk_analysis_indicator_em, symbol=norm, indicator="年度",
                  label="港股财务")
        fmap, currency_default = _HK_FIN_MAP, "HKD"
    else:
        out["missing"].append("未知市场（仅支持 us / hk）")
        return out

    if df is None or getattr(df, "empty", True):
        # 多为客户机东财被封（境内 IP 级封禁）取不到——如实标 missing，不编造。
        out["missing"].append("基础财务（东财源不可达，可能为客户机封锁；海外/中继可取）")
        return out

    row = df.iloc[0].to_dict()
    out["ok"] = True
    out["name"] = str(row.get("SECURITY_NAME_ABBR") or "")
    out["currency"] = _norm_ccy(row.get("CURRENCY"), currency_default)
    out["period"] = _fin_period(row)
    out["metrics"] = _build_metrics(row, fmap)
    # 全部字段为空（接口返回了行但指标列空）也如实提示，避免给一张空表
    if all(m["value"] is None for m in out["metrics"]):
        out["ok"] = False
        out["metrics"] = []
        out["missing"].append("基础财务（接口返回但关键指标为空）")
    return out
