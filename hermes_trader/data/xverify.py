"""双源交叉验证：实时价/指数点位的多源核对 + 溯源可信度徽标。

为什么需要它：单一行情源可能跳天、卡在旧值、或被限流返回脏数据。本模块对【实时价】
做多源交叉核对——价差小于阈值给"高可信"，超阈给"存疑"，单源时如实标"单源"，
让前端能给每个数字打上「来源 + 可信度」徽标，而不是把任一单源数字当权威呈现。

交叉源选择（都独立于已被封的东财，与本项目三层混合一致）：
  · 个股：客户授权 TDX API + 腾讯 qt.gtimg.cn，新浪 hq.sinajs.cn 有界兜底；
  · 指数：腾讯(主) + 新浪(交叉)；两个 HTTP 源都不依赖本地行情客户端。

铁律落地：某源取不到就【少一个交叉源、如实标】，绝不拿另一源的值伪造"双源确认"；
两源都缺 → confidence="none"，price=None，绝不编造。单位：price=元、指数 price=点、
spread_pct=百分比数值(0.42 表示 0.42%)。
"""
from __future__ import annotations

import time
import urllib.request

from .base import direct_https_opener, _record_source, log, to_float, ts_str, ttl_cache
from . import tencent_source as tc
from . import tdx_api_source as tdx_api

# 交叉一致阈值（%）：两源实时价相对中值的差异在此之内视为一致("高可信")。
# A 股最小报价单位 0.01 元，低价股一个最小变动也可能 >0.3%；且不同源刷新有亚秒级时差。
# 0.5% 兼顾"抓真异常(跳天/脏数据动辄差几个点)"与"不被正常微小时差误报存疑"，
# 与参考实现 ashare-agent/scripts/dual_source.py 的 PRICE_TOL 一致。
PRICE_TOL = 0.5

# 新浪实时行情：纯 stdlib，强制直连。中继(relay)部署下 base 会把进程级 ALL_PROXY 设成
# socks5h://，而其 NO_PROXY 豁免清单不含 sinajs.cn——裸 urlopen 会抛 'unknown url type:
# socks5h' 被吞成空响应。空 ProxyHandler({}) 屏蔽全部代理 scheme，让新浪无论部署形态都直连。
# （与 tencent_source._opener 同款思路；新浪也是境内直连免鉴权秒回的独立源。）
# 使用 certifi 严格验证证书链；同时强制直连，不继承中继模式的进程代理。
_sina_opener = direct_https_opener()
_SINA_HDR = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
_SINA_BASE = "https://hq.sinajs.cn/list="
_SINA_TIMEOUT = 2.5


def _sina_sym(code: str) -> str:
    """6 位代码 → 新浪前缀符号。沪 6/9/5(基金)、京 8/4/920、余深。
    与 tencent_source._symbol 同口径，保证两源指向同一标的。"""
    code = str(code).strip().zfill(6)
    if code.startswith(("8", "4")) or code.startswith("920"):
        return "bj" + code
    if code.startswith(("6", "9", "5")):
        return "sh" + code
    return "sz" + code


def _sina_fetch(symbols: list[str]) -> dict[str, list[str]]:
    """新浪批量行情 → {symbol: 字段list}。失败返回 {}（不抛、不编造）。
    个股行字段：f[0]=名称 f[1]=今开 f[2]=昨收 f[3]=现价 ... f[30]=日期 f[31]=时间(32列)。
    指数行同构（现价在 f[3]，无成交量列但本模块只取价/点位）。"""
    if not symbols:
        return {}
    url = _SINA_BASE + ",".join(symbols)
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers=_SINA_HDR)
        raw = _sina_opener.open(req, timeout=_SINA_TIMEOUT).read().decode("gbk", "ignore")
        out: dict[str, list[str]] = {}
        for line in raw.split("\n"):
            if "hq_str_" not in line or '"' not in line:
                continue
            sym = line.split("hq_str_", 1)[1].split("=", 1)[0].strip()
            body = line.split('"', 2)[1]
            f = body.split(",")
            if len(f) >= 4:                       # 至少要到现价列才算有效行
                out[sym] = f
        _record_source("新浪行情(交叉验证)", (time.monotonic() - t0) * 1000, bool(out),
                       "direct", None if out else "空响应")
        return out
    except Exception as e:  # noqa: BLE001
        _record_source("新浪行情(交叉验证)", (time.monotonic() - t0) * 1000, False,
                       "direct", str(e)[:120])
        log.info("新浪交叉验证行情失败: %s", str(e)[:120])
        return {}


def _sina_stock_price(code: str) -> float | None:
    """新浪个股现价（元）。守卫：价<=0 视为无效（停牌/未开盘新浪现价给 0.000）。"""
    sym = _sina_sym(code)
    f = _sina_fetch([sym]).get(sym)
    if not f or len(f) < 4:
        return None
    p = to_float(f[3])
    return p if (p is not None and p > 0) else None


# 指数符号 → 新浪 list 代码（新浪指数代码即 full_code 本身，如 sh000001）。
def _sina_index_price(full_code: str) -> float | None:
    """新浪指数点位。守卫：<=0 视为无效。"""
    f = _sina_fetch([full_code]).get(full_code)
    if not f or len(f) < 4:
        return None
    p = to_float(f[3])
    return p if (p is not None and p > 0) else None


# ---------------------------------------------------------------------------
# 交叉判定核心
# ---------------------------------------------------------------------------

def _cross(values: list[tuple[str, float]], tol: float) -> dict:
    """对【已取到的】(源名, 值) 列表做交叉判定，返回统一的徽标契约：
      {price, sources, confidence, spread_pct}
    - 0 源 → confidence='none', price=None（绝不编造）
    - 1 源 → confidence='single'（如实标单源，不臆造一致）
    - ≥2 源 → 取中位数为采用值；最大相对偏差(相对中位数)<=tol → 'high'，否则 'low'(存疑)

    采用中位数而非均值：≥3 源时一个跳天/脏数据的离群源不会把采用值带偏，
    且偏差以"极差相对中位数"衡量，任一源异常都会推高 spread_pct 触发'存疑'。"""
    vals = [v for _, v in values]
    sources = [s for s, _ in values]
    if not vals:
        return {"price": None, "sources": [], "confidence": "none", "spread_pct": None}
    if len(vals) == 1:
        return {"price": round(vals[0], 3), "sources": sources,
                "confidence": "single", "spread_pct": None}
    sv = sorted(vals)
    n = len(sv)
    median = sv[n // 2] if n % 2 else (sv[n // 2 - 1] + sv[n // 2]) / 2.0
    spread = (max(sv) - min(sv)) / median * 100 if median else 0.0
    return {
        "price": round(median, 3),
        "sources": sources,
        "confidence": "high" if spread <= tol else "low",
        "spread_pct": round(spread, 3),
    }


@ttl_cache(8)
def xverify_quote(code: str) -> dict:
    """个股实时价多源交叉验证。返回溯源徽标契约：
      {ok, code, name, price, change_pct, sources:[...], confidence, spread_pct,
       per_source:{...}, ts, missing:[...]}

    confidence: high=多源一致(差<=0.5%) / low=多源分歧(存疑) / single=仅一源 / none=均缺。
    交叉源：TDX API(wait=0) + 腾讯；两者不足时用新浪有界兜底。
    price 为采用值(多源取中位数)，单位=元；change_pct 优先取腾讯(单源即时涨跌幅最全)。"""
    code = str(code).strip().zfill(6)
    missing: list[str] = []
    per_source: dict[str, float] = {}
    collected: list[tuple[str, float]] = []
    name = ""
    change_pct = None

    # 源①客户授权 TDX API：wait=0 不等实时订阅；盘后 pending 立即降级）。
    aq = tdx_api.quote(code, max_age=30, wait=0)
    if aq and aq.get("price") is not None:
        per_source["TDX API"] = aq["price"]
        collected.append(("TDX API", aq["price"]))
        name = aq.get("name") or name
        change_pct = aq.get("change_pct")

    # 源②腾讯（一次给齐价/涨跌幅/名称，海外亦可达）
    tq = tc.quote(code)
    if tq and tq.get("price") is not None:
        per_source["腾讯"] = tq["price"]
        collected.append(("腾讯", tq["price"]))
        name = tq.get("name") or name
        change_pct = tq.get("change_pct")

    # 兜底③新浪：仅当上面不足 2 源时有界补充。本地 TDX 已从母版删除，
    # 不再进入交互链路或交叉验证预算。
    if len(collected) < 2:
        sp = _sina_stock_price(code)
        if sp is not None:
            per_source["新浪"] = sp
            collected.append(("新浪", sp))

    meta = _cross(collected, PRICE_TOL)
    if meta["confidence"] == "none":
        missing.append("实时价(TDX API/腾讯/新浪三源均不可用)")
    elif meta["confidence"] == "single":
        missing.append(f"实时价仅单源({meta['sources'][0]})，未获交叉确认")
    elif meta["confidence"] == "low":
        missing.append(f"多源价差{meta['spread_pct']}%超阈({PRICE_TOL}%)，实时价存疑")

    return {
        "ok": meta["price"] is not None,
        "code": code,
        "name": name or None,
        "price": meta["price"],
        "change_pct": change_pct,
        "sources": meta["sources"],
        "confidence": meta["confidence"],
        "spread_pct": meta["spread_pct"],
        "per_source": per_source,     # {源名: 各自原始价}，前端可在 tooltip 展开看分歧
        "ts": ts_str(),
        "missing": missing,
    }


@ttl_cache(8)
def xverify_index(full_code: str) -> dict:
    """指数点位多源交叉验证（full_code 形如 sh000001）。契约同 xverify_quote。
    交叉源：腾讯 + 新浪两个独立 HTTP 源。
    price 单位=点；change_pct 优先取腾讯。"""
    full_code = str(full_code).strip()
    missing: list[str] = []
    per_source: dict[str, float] = {}
    collected: list[tuple[str, float]] = []
    name = ""
    change_pct = None

    # 源①腾讯指数（含名称/涨跌幅/点位）
    tix = tc.index_quote(full_code)
    if tix and tix.get("price") is not None:
        per_source["腾讯"] = tix["price"]
        collected.append(("腾讯", tix["price"]))
        name = tix.get("name") or name
        change_pct = tix.get("change_pct")

    # 源②新浪指数（独立直连源）
    sp = _sina_index_price(full_code)
    if sp is not None:
        per_source["新浪"] = sp
        collected.append(("新浪", sp))

    meta = _cross(collected, PRICE_TOL)
    if meta["confidence"] == "none":
        missing.append("指数点位(腾讯/新浪两源均不可用)")
    elif meta["confidence"] == "single":
        missing.append(f"指数点位仅单源({meta['sources'][0]})，未获交叉确认")
    elif meta["confidence"] == "low":
        missing.append(f"两源点位差{meta['spread_pct']}%超阈({PRICE_TOL}%)，存疑")

    return {
        "ok": meta["price"] is not None,
        "code": full_code,
        "name": name or None,
        "price": meta["price"],
        "change_pct": change_pct,
        "sources": meta["sources"],
        "confidence": meta["confidence"],
        "spread_pct": meta["spread_pct"],
        "per_source": per_source,
        "ts": ts_str(),
        "missing": missing,
    }
