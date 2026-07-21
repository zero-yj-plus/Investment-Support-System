"""期货 · 期权数据层 —— 股指/商品期货主力 + 50/300/科创50 ETF 期权概览。

为什么单列一个源：本产品原缺「期货期权」整片（文档 §6 P3，ashare-agent 有、hermes 无）。
客户机(境内)东财 push2 被 IP 封死，故这里【优先非东财源】：
  期货主力 → 新浪 futures_main_sina（主力连续日线，最稳，一定有上一交易日收盘）
            + futures_zh_realtime（新浪实时盘口，盘中有则覆盖 last/盘口，取不到不致命）
  ETF 期权 → 上交所 option_sse_*（新浪通道，标的现价）+ option_finance_board（金融期权
            T 型实时行情，含每个合约 当前价/涨跌幅/前结/行权价）。

铁律落地（与 base.py 一致）：只返回真实拿到的数据；任一段取不到 → 对应字段缺省、
写入 missing，绝不编造、绝不拼不同源/口径。失败一律返回 {ok:False,...} 或字段 None，不抛。

单位约定（与全项目一致）：
  期货：价=点/元(按品种,见 unit 字段) · 成交量=手(期货成交量本就以手计,不换算) · 持仓=手 · 涨跌幅=%
  期权：价=元(每份权利金) · 行权价=元 · 涨跌幅=% · 标的价=元 · 成交额=元
注意：期货合约价的「点 vs 元」因品种而异（股指=点，商品多为元/吨等），本模块不强行折算成
名义金额（需乘合约乘数，乘数随品种且会变），只如实给【报价数值】并在 unit 标注，绝不臆造名义市值。
"""
from __future__ import annotations

import time
from typing import Any

from .base import get_ak, safe, to_float, to_int, ts_str, ttl_cache, log, now_cn, is_trade_day

# ───────────────────────── 期货主力 ─────────────────────────
# 新浪主力连续合约 symbol（XXX0），一定能取上一交易日收盘；盘中再尝试用实时盘口覆盖。
# name=展示名；rt=futures_zh_realtime 的品种入参（必须是 futures_symbol_mark() 的 key，
#     与展示名常不同，写死映射避免每次拉表）；unit=报价单位语义（如实标注，不折算）。
INDEX_FUTURES = [
    {"symbol": "IF0", "name": "沪深300股指", "rt": "沪深300指数期货", "unit": "点"},
    {"symbol": "IC0", "name": "中证500股指", "rt": "中证500指数期货", "unit": "点"},
    {"symbol": "IH0", "name": "上证50股指", "rt": "上证50指数期货", "unit": "点"},
    {"symbol": "IM0", "name": "中证1000股指", "rt": "中证1000股指期货", "unit": "点"},
]

COMMODITY_FUTURES = [
    {"symbol": "RB0", "name": "螺纹钢", "rt": "螺纹钢", "unit": "元/吨"},
    {"symbol": "AU0", "name": "沪金", "rt": "黄金", "unit": "元/克"},
    {"symbol": "AG0", "name": "沪银", "rt": "白银", "unit": "元/千克"},
    {"symbol": "CU0", "name": "沪铜", "rt": "沪铜", "unit": "元/吨"},
    {"symbol": "SC0", "name": "原油", "rt": "原油", "unit": "元/桶"},
    {"symbol": "M0", "name": "豆粕", "rt": "豆粕", "unit": "元/吨"},
    {"symbol": "I0", "name": "铁矿石", "rt": "铁矿石", "unit": "元/吨"},
    {"symbol": "FG0", "name": "玻璃", "rt": "玻璃", "unit": "元/吨"},
]


def _main_daily_quote(item: dict) -> dict | None:
    """单个主力连续合约的报价：以新浪主力日线为基（取最后两根算涨跌幅），
    盘中若 futures_zh_realtime 可用则用其实时 last/盘口覆盖。任一字段缺即留 None，不编造。"""
    ak = get_ak()
    if ak is None:
        return None
    df = safe(
        ak.futures_main_sina,
        symbol=item["symbol"],
        label=f"期货主力日线 {item['symbol']}",
        timeout=4,
    )
    if df is None or getattr(df, "empty", True) or len(df) < 1:
        return None
    cols = set(df.columns)
    if not {"收盘价", "日期"} <= cols:
        return None
    last = df.iloc[-1]
    close = to_float(last.get("收盘价"))
    if close is None or close <= 0:
        return None
    prev_close = to_float(df.iloc[-2].get("收盘价")) if len(df) >= 2 else None
    out: dict[str, Any] = {
        "symbol": item["symbol"],
        "name": item["name"],
        "unit": item["unit"],
        "price": close,                                   # 收盘价（盘中可能被实时覆盖）
        "prev_close": prev_close,
        "open": to_float(last.get("开盘价")),
        "high": to_float(last.get("最高价")),
        "low": to_float(last.get("最低价")),
        "volume": to_int(last.get("成交量")),             # 手（期货本以手计，不换算）
        "open_interest": to_int(last.get("持仓量")),      # 持仓量（手）
        "data_date": str(last.get("日期"))[:10],
        # 涨跌幅由【同源】最后两根日线算，绝不跨源拼（前结价此源不给，不混用）。
        "change_pct": round((close - prev_close) / prev_close * 100, 2)
        if (prev_close and prev_close > 0) else None,
        "live": False,
    }
    # 盘中实时盘口覆盖（新浪 futures_zh_realtime，按品种名 rt 查；取不到则保留日线值，不报错）。
    current = now_cn()
    hm = current.hour * 60 + current.minute
    in_market_window = is_trade_day(current.date()) and (9 * 60) <= hm <= (15 * 60 + 15)
    rt = safe(
        ak.futures_zh_realtime,
        symbol=item["rt"],
        label=f"期货实时 {item['symbol']}",
        timeout=3,
    ) if in_market_window else None
    if rt is not None and not getattr(rt, "empty", True):
        try:
            # 只有明确命中当前合约才能覆盖。过去在未命中时取第一行，可能把别的
            # 合约报价误标成当前品种的“盘中实时”。
            if "symbol" not in rt.columns:
                return out
            row = rt[rt["symbol"] == item["symbol"]]
            if row.empty:
                return out
            r = row.iloc[0]
            trade = to_float(r.get("trade"))
            preset = to_float(r.get("prevsettlement")) or to_float(r.get("presettlement"))
            if trade is not None and trade > 0:
                out["price"] = trade
                out["live"] = True
                out["data_date"] = current.date().isoformat()
                out["observed_at"] = current.isoformat()
                out["open"] = to_float(r.get("open")) or out["open"]
                out["high"] = to_float(r.get("high")) or out["high"]
                out["low"] = to_float(r.get("low")) or out["low"]
                out["volume"] = to_int(r.get("volume")) or out["volume"]
                out["open_interest"] = to_int(r.get("position")) or out["open_interest"]
                out["settlement"] = preset                # 前结算价（实时源口径，单独字段不与日线涨跌幅混用）
                # 实时涨跌幅优先用其自带 changepercent（相对前收盘，分数→%）；缺则退回日线算的值。
                cp = to_float(r.get("changepercent"))
                if cp is not None:
                    out["change_pct"] = round(cp * 100, 2)
        except Exception as e:  # 实时源字段漂移不应影响日线主数据
            log.info("期货实时覆盖失败 %s: %s", item["symbol"], str(e)[:120])
    return out


@ttl_cache(20, fail_seconds=600, offhours_seconds=6 * 3600)
def futures_overview() -> dict:
    """股指期货主力(IF/IC/IH/IM) + 商品期货主力概览。

    返回 {ok, ts, index:[...], commodity:[...], missing:[...]}。
    任一合约取不到 → 不进列表、记入 missing；全取不到 ok=False。绝不编造。"""
    index, commodity, missing = [], [], []
    items = [*(('index', item) for item in INDEX_FUTURES), *(('commodity', item) for item in COMMODITY_FUTURES)]
    deadline = time.monotonic() + 4.5
    for pos, (kind, it) in enumerate(items):
        if pos and time.monotonic() >= deadline:
            missing.extend(
                f"{'股指期货' if rest_kind == 'index' else '商品期货'} {rest['name']}({rest['symbol']})"
                for rest_kind, rest in items[pos:]
            )
            missing.append("期货概览已达 4.5 秒交互预算，剩余合约本轮跳过")
            break
        q = _main_daily_quote(it)
        if q:
            (index if kind == 'index' else commodity).append(q)
            continue
        missing.append(f"{'股指期货' if kind == 'index' else '商品期货'} {it['name']}({it['symbol']})")
        # 这些合约共用同一新浪源。首个或中途吃满超时时不再对剩余
        # 合约逐个复制同一慢失败；保留已成功的部分并明确标部分覆盖。
        remaining = items[pos + 1:]
        missing.extend(
            f"{'股指期货' if rest_kind == 'index' else '商品期货'} {rest['name']}({rest['symbol']})"
            for rest_kind, rest in remaining
        )
        if remaining:
            missing.append("期货公开源本轮超时，已停止重复请求剩余合约")
        break
    ok = bool(index or commodity)
    return {
        "ok": ok,
        "ts": ts_str(),
        "index": index,
        "commodity": commodity,
        "missing": missing,
        **({} if ok else {"error": "期货数据源全部不可用（新浪主力连续行情取不到）"}),
    }


# ───────────────────────── ETF 期权 ─────────────────────────
# board=option_finance_board 的 symbol；underlying=上交所标的代码(取标的现价用)；
# end=取最近月（由 option_sse_list_sina 动态拿首月，避免写死过期月份）。
ETF_OPTIONS = [
    {"key": "50ETF", "board": "华夏上证50ETF期权", "underlying": "sh510050", "name": "上证50ETF期权"},
    {"key": "300ETF", "board": "华泰柏瑞沪深300ETF期权", "underlying": "sh510300", "name": "沪深300ETF期权"},
    {"key": "KC50", "board": "华夏科创50ETF期权", "underlying": "sh588000", "name": "科创50ETF期权"},
]


def _underlying_spot(under: str) -> dict | None:
    """ETF 标的现价/涨跌幅（上交所新浪通道，键值表）。失败返回 None，不编造。"""
    ak = get_ak()
    if ak is None:
        return None
    df = safe(
        ak.option_sse_underlying_spot_price_sina,
        symbol=under,
        label=f"期权标的现价 {under}",
        timeout=4,
    )
    if df is None or getattr(df, "empty", True):
        return None
    try:
        kv = {str(r["字段"]): r["值"] for _, r in df.iterrows()}
    except Exception:
        return None
    price = to_float(kv.get("最近成交价"))
    prev = to_float(kv.get("昨日收盘价"))
    if price is None or price <= 0:
        return None
    return {
        "name": str(kv.get("证券简称") or "").strip(),
        "price": price,
        "prev_close": prev,
        "change_pct": round((price - prev) / prev * 100, 2) if (prev and prev > 0) else None,
        "amount": to_float(kv.get("成交金额")),           # 元
    }


def _parse_contract(code: str) -> dict | None:
    """合约交易代码 → {right, strike, expiry}。形如 510050C2607M02800：
    标的(510050) + C/P(认购/认沽) + 到期(2607) + M + 行权价(02800 → ×0.001=2.800元)。"""
    code = str(code or "").strip()
    if len(code) < 12:
        return None
    right = None
    if "C" in code[6:8]:
        right = "call"
    elif "P" in code[6:8]:
        right = "put"
    if right is None:
        return None
    try:
        i = code.index("M")
        expiry = code[7:i] if right else ""
        strike_raw = to_int(code[i + 1:])
    except (ValueError, TypeError):
        return None
    if strike_raw is None:
        return None
    return {"right": right, "strike": round(strike_raw * 0.001, 3), "expiry": expiry}


def _option_chain(item: dict, end_month: str) -> dict | None:
    """单个 ETF 期权近月概览：标的现价 + 看涨/看跌合约数 + ATM(平值附近)认购/认沽权利金
    + 看跌看涨比(PCR, 按当前在挂合约数)。失败返回 None，不编造。"""
    ak = get_ak()
    if ak is None:
        return None
    df = safe(
        ak.option_finance_board,
        symbol=item["board"],
        end_month=end_month,
        label=f"期权行情 {item['key']}",
        timeout=4,
    )
    if df is None or getattr(df, "empty", True) or "合约交易代码" not in df.columns:
        return None
    spot = _underlying_spot(item["underlying"])           # 可能 None（标的现价缺）
    spot_price = spot.get("price") if spot else None

    calls, puts = [], []
    for _, r in df.iterrows():
        meta = _parse_contract(r.get("合约交易代码"))
        if not meta:
            continue
        rec = {
            "strike": meta["strike"],
            "price": to_float(r.get("当前价")),
            "change_pct": to_float(r.get("涨跌幅")),       # 该源已是百分数
            "prev_settle": to_float(r.get("前结价")),
            "expiry": meta["expiry"],
        }
        (calls if meta["right"] == "call" else puts).append(rec)

    if not calls and not puts:
        return None
    calls.sort(key=lambda x: (x["strike"] if x["strike"] is not None else 1e9))
    puts.sort(key=lambda x: (x["strike"] if x["strike"] is not None else 1e9))

    # 平值(ATM)：行权价最接近标的现价者。标的现价缺则不臆造，ATM 字段留 None。
    def _atm(rows: list[dict]) -> dict | None:
        if not rows or spot_price is None:
            return None
        cand = [x for x in rows if x["strike"] is not None and x["price"] is not None]
        if not cand:
            return None
        return min(cand, key=lambda x: abs(x["strike"] - spot_price))

    atm_call, atm_put = _atm(calls), _atm(puts)
    # PCR：当前在挂合约数 put/call（口径=合约数量，非持仓量；持仓量该源不给，绝不冒充）。
    pcr = round(len(puts) / len(calls), 2) if calls else None
    return {
        "key": item["key"],
        "name": item["name"],
        "underlying_code": item["underlying"],
        "underlying_name": (spot or {}).get("name") if spot else None,
        "underlying_price": spot_price,
        "underlying_change_pct": (spot or {}).get("change_pct") if spot else None,
        "expiry": end_month,
        "call_count": len(calls),
        "put_count": len(puts),
        "pcr_count": pcr,                                  # 看跌/看涨在挂合约数比
        "atm_strike": (atm_call or atm_put or {}).get("strike"),
        "atm_call": atm_call,                             # {strike,price,change_pct,prev_settle,expiry}
        "atm_put": atm_put,
        "strikes": sorted({x["strike"] for x in (calls + puts) if x["strike"] is not None}),
    }


@ttl_cache(30, fail_seconds=600, offhours_seconds=6 * 3600)
def options_overview() -> dict:
    """50ETF / 300ETF / 科创50ETF 期权近月概览。

    返回 {ok, ts, options:[...], missing:[...]}。
    近月由 option_sse_list_sina 动态取（不写死月份）；任一标的取不到记入 missing；
    全取不到 ok=False。绝不编造、绝不拼口径。"""
    ak = get_ak()
    if ak is None:
        return {"ok": False, "ts": ts_str(), "options": [], "missing": ["akshare 不可用"],
                "error": "akshare 未安装"}
    options, missing = [], []
    for pos, it in enumerate(ETF_OPTIONS):
        # 动态取首个（最近）到期月，避免写死月份过期。取不到则跳过该标的并记 missing。
        months = safe(
            ak.option_sse_list_sina,
            symbol=it["key"],
            exchange="null",
            label=f"期权到期月 {it['key']}",
            timeout=4,
        )
        if not months:
            missing.append(f"{it['name']}（到期月列表取不到）")
            missing.extend(f"{rest['name']}（已跳过重复坏源）" for rest in ETF_OPTIONS[pos + 1:])
            missing.append("期权公开源本轮不可用，已停止重复请求其余标的")
            break
        end_month = str(months[0])[-4:]                   # 'YYYYMM' → 'YYMM'，option_finance_board 口径
        chain = _option_chain(it, end_month)
        (options.append(chain) if chain else missing.append(f"{it['name']}（行情取不到）"))
    ok = bool(options)
    return {
        "ok": ok,
        "ts": ts_str(),
        "options": options,
        "missing": missing,
        **({} if ok else {"error": "期权数据源全部不可用（上交所 ETF 期权行情取不到）"}),
    }
