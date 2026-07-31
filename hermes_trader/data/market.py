"""市场级真实数据采集：指数、宽度、涨停池、板块、资金、涨幅榜。

全部基于 akshare（东财/新浪公开接口）。每个函数返回契约定义的 dict，
拿不到的字段写入 missing —— 绝不编造。
"""
from __future__ import annotations

from datetime import timedelta
import json
import os
import time
import urllib.request

from .base import (
    get_ak, safe, to_float, to_int, ts_str, ttl_cache, now_cn,
    is_trade_day, prev_trade_day, _record_source, em_available, em_report, log,
)
from . import tencent_source as tc
from . import tdx_api_source as tdxapi
from . import free_source as tx

# 境内数据采集中台（SSH -L 转发到 127.0.0.1:8799）。配置后全市场快照优先走它，
# 由境内服务器本地分页聚合全市场，海外客户端只收一份 gzip 结果，规避东财每页限 100
# 导致的 58 页串行经隧道高失败率问题。运行时读取，以便 .env 在 config 加载后生效。
def _relay_api() -> str:
    return os.environ.get("DATA_RELAY_API", "").strip().rstrip("/")


def _eastmoney_direct() -> bool:
    """本机东财可直连时为 True（config data_source.eastmoney_direct=true，如境内云服务器）。
    config.py 注入环境位 HERMES_EM_DIRECT；走 env 桥避免 config↔data 循环导入。"""
    return os.environ.get("HERMES_EM_DIRECT", "").strip().lower() in ("1", "true", "yes")


def _use_eastmoney() -> bool:
    """是否优先走【东财直连源】：有中继(海外客户机经隧道回境内) 或 配置东财直连(境内服务器)。
    两者皆无或部署网络不可达时 → False，走腾讯/公开池/同花顺/新浪免费独立源。
    honor 客户"东财为主/同花顺辅"：东财可达即为主源，不可达才降级到独立源。"""
    return bool(_relay_api()) or _eastmoney_direct()


# 中台 /spot 最近一次返回的完整性/时效元数据（健康面板读取，不额外发请求）
_last_relay_meta: dict = {}


def last_relay_meta() -> dict:
    return dict(_last_relay_meta)


def relay_staleness_notes(spot) -> list[str]:
    """提取中台快照的陈旧/不完整标注，供所有快照消费方写入各自 missing。
    中台 _last_good 永不过期、只打 stale/incomplete 标记——任何消费方不读取
    标记就会把陈旧或残缺快照当实时全市场数据呈现（数据真实性约束）。"""
    rm = getattr(spot, "attrs", {}).get("relay_meta") or {}
    notes: list[str] = []
    if rm.get("incomplete"):
        if rm.get("total"):
            notes.append(f"全市场快照不完整(实采{rm.get('count')}/应有{rm.get('total')})")
        else:
            fp = rm.get("failed_pages") or []
            notes.append(f"全市场快照不完整(实采{rm.get('count')}，丢失{len(fp)}页且应有总数未知)")
    if rm.get("stale"):
        notes.append(f"全市场快照为陈旧缓存(距今约{int(rm.get('age') or 0)}秒)")
    return notes

INDEX_CODES = {
    "000001": ("sh000001", "上证指数"),
    "399001": ("sz399001", "深证成指"),
    "399006": ("sz399006", "创业板指"),
    "000688": ("sh000688", "科创50"),
    # 对齐 ashare-agent 的 7 指数总览：补 沪深300 / 中证500 / 北证50。
    # 三者腾讯 qt.gtimg.cn 同一 index_quotes 即取齐(price/change_pct/amount，实测 len≥87)，
    # 绕东财、海外亦可达。东财/新浪兜底 key 用 6 位码：000300/000905 在“沪深重要指数”快照内，
    # 北证50(899050)东财快照常缺→腾讯失败时该指数自然降级标 missing(不编造)。
    "000300": ("sh000300", "沪深300"),
    "000905": ("sh000905", "中证500"),
    "899050": ("bj899050", "北证50"),
}


def _spot_from_relay():
    """从境内数据中台拉全市场快照，转成 akshare stock_zh_a_spot_em 兼容 DataFrame。
    relay 作为『全市场快照』活跃主源时，必须同样向 _record_source 埋点，否则健康面板会把
    它误报为 idle/未采集（埋点原本只在 base.safe() 内发生，而本函数走裸 urllib 不经 safe）。"""
    global _last_relay_meta
    api = _relay_api()
    if not api:
        return None
    t0 = time.monotonic()
    try:
        import pandas as pd  # noqa: PLC0415

        req = urllib.request.Request(f"{api}/spot", headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=40) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip  # noqa: PLC0415

                raw = gzip.decompress(raw)
            payload = json.loads(raw.decode("utf-8"))
        rows = payload.get("rows") or []
        if len(rows) < 1000:  # 数据明显不全，视为失败交给兜底
            _record_source("全市场快照", (time.monotonic() - t0) * 1000, False, "relay",
                            f"中台快照数据不全 rows={len(rows)}")
            # 失败也要刷新元数据，否则 /datasources 面板会把历史成功的 count/stale 当当前状态
            _last_relay_meta = {**_last_relay_meta, "ok": False,
                                "last_error": f"中台快照数据不全 rows={len(rows)}",
                                "last_fail_epoch": time.time()}
            return None
        df = pd.DataFrame(rows)
        # 携带中台的完整性/时效元数据，供 market_overview 判定并标注 missing：
        # total=东财应有总数, count=实采数, failed_pages=失败页, stale/age=陈旧标记。
        total = payload.get("total")
        failed_pages = payload.get("failed_pages") or []
        meta = {
            "total": total,
            "count": len(rows),
            "failed_pages": failed_pages,
            "stale": bool(payload.get("stale")),
            "age": payload.get("age"),
            # 缺口比例：实采远少于应有时视为不完整（>2% 缺口）。
            # 中台首页失败时 total=0（应有总数未知）但仍可能丢页服务，此时凭 failed_pages
            # 判定，否则残缺快照会以全市场口径呈现且零标注。
            "incomplete": bool(total and len(rows) < total * 0.98)
                          or (not total and bool(failed_pages)),
            "ok": True,
        }
        df.attrs["relay_meta"] = meta
        # 一次性整体重绑定（CPython 下名字赋值原子），避免 clear()+update() 两步的并发半更新中间态。
        _last_relay_meta = meta
        _record_source("全市场快照", (time.monotonic() - t0) * 1000, True, "relay", None)
        return df
    except Exception as e:
        from .base import log  # noqa: PLC0415

        log.warning("数据中台 /spot 调用失败: %s", e)
        _record_source("全市场快照", (time.monotonic() - t0) * 1000, False, "relay", str(e)[:140])
        _last_relay_meta = {**_last_relay_meta, "ok": False, "last_error": str(e)[:140],
                            "last_fail_epoch": time.time()}
        return None


# 最近一次成功的全市场快照（serve-last-good）：任何刷新失败时回退到它并标记陈旧，
# 让"取到过的数据不会又变没"。铁律：陈旧值必须打 stale 标，绝不当实时全量呈现。
_last_good_spot = None  # (monotonic_ts, DataFrame)


def _spot_fetch():
    """单次全市场快照抓取（多级降级，不含 serve-last-good）。
    优先境内中台；否则 akshare 直连：全量快照(长超时让 ~54 页爬虫跑完) → 分市场拼接
    (沪/深/京各自页数少、单市场失败只丢一个) → 新浪全市场(列少, 缺列由消费方标注)。"""
    relay = _spot_from_relay()
    if relay is not None and not relay.empty:
        return relay
    if _relay_api():
        return None  # 配了中继就不再经隧道直打可能被限流的 push2
    ak = get_ak()
    if ak is None:
        return None
    # 直连主源：全市场快照是 ~54 页串行爬虫(每页强制 sleep)，远超默认 15s——给 60s 专用超时让它跑完
    df = safe(ak.stock_zh_a_spot_em, label="全市场快照", timeout=60)
    if df is not None and not df.empty:
        return df
    # 兜底①：分市场拼接（列结构与全量版逐列一致，可直接 concat；单市场失败只丢该市场并标注）
    import pandas as pd  # noqa: PLC0415
    parts, miss = [], []
    for fn, mk in ((getattr(ak, "stock_sh_a_spot_em", None), "沪A"),
                   (getattr(ak, "stock_sz_a_spot_em", None), "深A"),
                   (getattr(ak, "stock_bj_a_spot_em", None), "京A")):
        if fn is None:
            continue
        d = safe(fn, label=f"全市场快照({mk})", timeout=45)
        if d is not None and not d.empty:
            parts.append(d)
        else:
            miss.append(mk)
    if parts:
        merged = pd.concat(parts, ignore_index=True)
        if miss:
            merged.attrs["relay_meta"] = {"incomplete": True, "count": len(merged),
                                          "total": None, "failed_pages": miss,
                                          "stale": False, "age": None}
        return merged
    # 兜底②：新浪全市场（无换手/量比/市盈率/市值——消费方按缺列标注，不伪填）
    sina = getattr(ak, "stock_zh_a_spot", None)
    return safe(sina, label="全市场快照(新浪)", timeout=45) if sina else None


@ttl_cache(45, offhours_seconds=6 * 3600)
def _spot_all():
    """全市场 A 股快照（45s 缓存）。任何路径失败时回退到最近一次成功的快照并标记陈旧，
    避免"取到过又变没"导致仪表盘/个股/报告整片塌掉（铁律：陈旧必标注，绝不编造）。"""
    global _last_good_spot
    df = _spot_fetch()
    if df is not None and not df.empty:
        _last_good_spot = (time.monotonic(), df)
        return df
    # 全部失败 → 回退上次成功快照并叠加 stale 标记（不覆盖原有 incomplete 等元数据）
    if _last_good_spot is not None:
        age = time.monotonic() - _last_good_spot[0]
        stale = _last_good_spot[1]
        meta = dict(getattr(stale, "attrs", {}).get("relay_meta") or {})
        meta.update({"stale": True, "age": int(age)})
        try:
            stale.attrs["relay_meta"] = meta
        except Exception:
            pass
        return stale
    return None


@ttl_cache(30, offhours_seconds=6 * 3600)
def _zt_pool(date_str: str):
    ak = get_ak()
    if ak is None:
        return None
    return safe(ak.stock_zt_pool_em, date=date_str, label="涨停池")


@ttl_cache(30, offhours_seconds=6 * 3600)
def _dt_pool(date_str: str):
    ak = get_ak()
    if ak is None:
        return None
    return safe(ak.stock_zt_pool_dtgc_em, date=date_str, label="跌停池")


@ttl_cache(30, offhours_seconds=6 * 3600)
def _zb_pool(date_str: str):
    ak = get_ak()
    if ak is None:
        return None
    return safe(ak.stock_zt_pool_zbgc_em, date=date_str, label="炸板池")


# 东财池「当日 last-good」层：东财对中继 IP 也会间歇掐连接(RemoteDisconnected)，且 ttl_cache
# 对失败结果有 180s 缓存(防重试风暴)——一次瞬断会让盘中数据跳回 T-1 并赖住 3 分钟，体验极差。
# 修法：瞬断时改用【今天最后一次成功的池快照】(标注截至时点 HH:MM，几分钟前的今日值远比昨日值
# 有用且真实)；今日从未成功才交由调用方走免费公开池 T-1 兜底。降级链：实时 → 今日快照(标时点) → 昨日(标昨)。
_POOL_LG: dict = {}   # {(kind, dc): (asof "HH:MM", df)}


def _pool_lastgood(kind: str, dc: str, fetcher):
    """调东财池；成功→记 last-good 并返回 (df, None)；失败但今日曾成功→(旧df, asof时点)；
    今日从未成功→(None, None)。asof 非空时调用方必须把时点标进 missing(铁律：不冒充最新)。"""
    df = fetcher(dc)
    if df is not None:
        _POOL_LG[(kind, dc)] = (now_cn().strftime("%H:%M"), df)
        return df, None
    hit = _POOL_LG.get((kind, dc))
    if hit is not None:
        return hit[1], hit[0]
    return None, None


def _today_compact() -> str:
    return now_cn().strftime("%Y%m%d")


def _resolve_data_day(date_compact: str | None = None):
    """解析取数日期与数据归属交易日，避免把昨日数据当今日。
    返回 (compact 查询日期, data_date 归属交易日 ISO, note 提示或 None)。
    规则：交易日且已开盘(>=09:25) → 今天；盘前或非交易日 → 上一交易日并给出提示。
    （东财涨停池等接口在非交易日/盘前用 date=今天 通常返回上一交易日数据，
    若不校正会把昨日数据贴上今日时间戳，违反数据真实性约束。）"""
    if date_compact:
        iso = f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}"
        return date_compact, iso, None
    now = now_cn()
    today = now.date()
    today_iso = today.isoformat()
    session_started = now.hour * 60 + now.minute >= 9 * 60 + 25
    if is_trade_day(today) and session_started:
        return today.strftime("%Y%m%d"), today_iso, None
    prev = prev_trade_day(today)
    if not prev:
        return today.strftime("%Y%m%d"), today_iso, None
    note = (f"盘前时段，涨停/市场宽度等为上一交易日({prev})数据"
            if is_trade_day(today)
            else f"非交易日，以下为最近交易日({prev})数据")
    return prev.replace("-", ""), prev, note


def decision_data_date() -> str:
    """当前决策视图应采用的行情归属日，不触发任何外部数据请求。"""
    return _resolve_data_day()[1]


def _limit_list_today_or_prev(dc: str, kind: str = "U"):
    """取免费公开涨跌停池；若 dc=今日且空（当日涨停连板明细尚未生成）
    → 退【上一交易日】数据并回传其日期，供前端醒目标注 T-1。返回 (list|None, stale_date_iso|None)。
    铁律：stale_date 非空=数据来自上一交易日，前端必须清楚标注、绝不当今日实时；历史指定日期查询不回退。"""
    data = tx.limit_list(dc, kind)
    if data:
        return data, None
    try:
        today_dc = now_cn().strftime("%Y%m%d")
    except Exception:  # noqa: BLE001
        return None, None
    if dc != today_dc:          # 只对"当日视图"回退；显式查历史某日不乱退成"上一交易日"
        return None, None
    prev = prev_trade_day()
    if not prev:
        return None, None
    pdc = prev.replace("-", "")
    if pdc == dc:
        return None, None
    pdata = tx.limit_list(pdc, kind)
    return (pdata, prev) if pdata else (None, None)


@ttl_cache(600, offhours_seconds=6 * 3600)
def _limit_up_count(dc: str):
    """某交易日免费公开涨停池(U)家数（晋级率分母用）。取不到返回 None（标缺，不编）。"""
    lst = tx.limit_list(dc, "U")
    return len(lst) if lst else None


def _promote_rate(lianban_counts, list_date_iso):
    """晋级率 = 该日涨停池中「连板数≥2」(=昨日涨停今日继续板,晋级成功)的家数 / 前一交易日涨停家数。
    分子分母同为免费公开涨停池口径（连板数来自同一列表；前日家数走同类 U 列表）——不跨源拼。
    前日家数/日历取不到 → None（前端显「—」，绝不编）。"""
    try:
        vals = [int(x) for x in lianban_counts if x is not None]
    except (TypeError, ValueError):
        return None
    if not vals:
        return None
    from datetime import date as _date  # noqa: PLC0415
    try:
        prev = prev_trade_day(_date.fromisoformat(str(list_date_iso)[:10]))
    except (TypeError, ValueError):
        prev = None
    if not prev:
        return None
    denom = _limit_up_count(prev.replace("-", ""))
    if not denom:
        return None
    return round(sum(1 for v in vals if v >= 2) / denom * 100, 1)


@ttl_cache(3)
def index_quotes_light() -> dict:
    """指数点位轻量快照（盘中跳动专用，3s 缓存）：只打腾讯 qt.gtimg 批量指数，
    不做宽度/涨停等慢计算 → 可安全 ~3s 轮询给前端做实时数字跳动（同 overview 的指数口径/字段）。
    腾讯失败退 akshare 东财/新浪快照；全失败则 indexes=[] + missing（不编造）。
    返回 {ok, ts, indexes:[{code,name,price,change_pct,amount}], missing}。"""
    indexes: list[dict] = []
    symbols = [v[0] for v in INDEX_CODES.values()]
    # TDX HTTP API v2.3 为客户授权的秒级通道；不可用时仍退腾讯。
    # 两者字段已统一为 价=元/额=元，不让前端感知单位差异。
    tix = tdxapi.index_quotes(symbols) or tc.index_quotes(symbols)
    if tix:
        for full, name in INDEX_CODES.values():
            d = tix.get(full)
            if d and d.get("price") is not None:
                indexes.append({"code": full, "name": name, "price": d["price"],
                                "change_pct": d.get("change_pct"), "amount": d.get("amount")})
    # 2026-07-11 审计 M2 修复：此前缺 `ak = get_ak()` 赋值 → 腾讯失败时 NameError（/market/indices
    # 3s 轮询报 500 而非降级），akshare 兜底整段成死代码。补上赋值让降级链真正可达。
    ak = get_ak()
    if not indexes and ak is not None:
        df = safe(ak.stock_zh_index_spot_em, symbol="沪深重要指数", label="指数快照(轻)")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("代码", ""))
                if code in INDEX_CODES:
                    full, name = INDEX_CODES[code]
                    indexes.append({"code": full, "name": name,
                                    "price": to_float(row.get("最新价")),
                                    "change_pct": to_float(row.get("涨跌幅")),
                                    "amount": to_float(row.get("成交额"))})
        order = {v[0]: i for i, v in enumerate(INDEX_CODES.values())}
        indexes.sort(key=lambda x: order.get(x["code"], 99))
    out: dict = {"ok": True, "ts": ts_str(), "indexes": indexes}
    if not indexes:
        out["missing"] = ["指数行情"]
    return out


def _with_quality(
    out: dict,
    *,
    missing_fields: list[str] | None = None,
    degraded_factors: list[str] | None = None,
    data_state: str | None = None,
    state_label: str | None = None,
) -> dict:
    """在保留旧 ``missing`` 字段的同时，给前端一个可执行的数据质量结论。

    ``missing`` 历史上混合了真缺失、来源和口径备注；结构化字段专门
    回答“核心决策是否可用”，``notes`` 只作可追溯说明。
    """
    missing_fields = list(dict.fromkeys(missing_fields or []))
    degraded_factors = list(dict.fromkeys(degraded_factors or []))
    state = data_state or (
        "missing" if missing_fields else ("degraded" if degraded_factors else "complete")
    )
    out["missing_fields"] = missing_fields
    out["degraded_factors"] = degraded_factors
    out["notes"] = list(dict.fromkeys(out.get("missing") or []))
    out["data_state"] = state
    if state_label:
        out["state_label"] = state_label
    elif state == "complete":
        out["state_label"] = "决策可用"
    elif state == "degraded":
        out["state_label"] = "增强项缺失"
    elif state == "missing":
        out["state_label"] = "暂不可用"
    return out


def _normalize_legu_activity(frame) -> dict:
    """把 AkShare 乐咕表格收敛为稳定的市场宽度契约。

    解析放在 ``safe`` 的受控调用内部：页面结构变化、字段缺失或非法负数都应
    计为本次数据源失败并进入熔断，而不是把“HTTP 成功但数据不可用”误报为健康。
    """
    if frame is None or getattr(frame, "empty", True):
        raise ValueError("乐咕市场活跃度返回空表")
    try:
        kv = {str(row["item"]).strip(): row["value"] for _, row in frame.iterrows()}
    except Exception as exc:  # noqa: BLE001 - 转为统一的源解析失败
        raise ValueError("乐咕市场活跃度表格结构异常") from exc

    result = {
        "up": to_int(kv.get("上涨")),
        "down": to_int(kv.get("下跌")),
        "flat": to_int(kv.get("平盘")),
        "limit_up": to_int(kv.get("涨停")),
        "limit_down": to_int(kv.get("跌停")),
        "data_time": str(kv.get("统计日期") or "").strip() or None,
        "source": "legulegu_market_activity",
    }
    required = (result["up"], result["down"], result["flat"])
    if any(value is None or value < 0 for value in required):
        raise ValueError("乐咕市场活跃度缺少有效涨跌家数")
    if any(
        value is not None and value < 0
        for value in (result["limit_up"], result["limit_down"])
    ):
        raise ValueError("乐咕市场活跃度涨跌停家数非法")
    return result


def _fetch_legu_activity() -> dict:
    ak = get_ak()
    if ak is None:
        raise RuntimeError("akshare 不可用")
    return _normalize_legu_activity(ak.stock_market_activity_legu())


@ttl_cache(60, fail_seconds=600, offhours_seconds=6 * 3600)
def market_activity_legu() -> dict | None:
    """共享的乐咕市场宽度快照。

    市场总览与资金择时必须复用同一缓存和超时预算；否则较短预算的一方会用
    同标签打开全局熔断器，导致另一方即使预算充足也被连带降级。
    """
    return safe(
        _fetch_legu_activity,
        label="市场活跃度(乐咕)",
        timeout=10,
    )


@ttl_cache(12, offhours_seconds=6 * 3600)
def market_overview(date_compact: str | None = None) -> dict:
    """指数 + 市场宽度 + 涨停结构。整体 12s 缓存（内部各源失败链路较慢）。"""
    missing: list[str] = []
    dc, data_date, day_note = _resolve_data_day(date_compact)
    out: dict = {"ok": True, "ts": ts_str(), "data_date": data_date, "missing": missing}
    if day_note:
        missing.append(day_note)
    ak = get_ak()

    # ---- 指数（腾讯主源[qt.gtimg.cn，独立于东财、海外亦可达]，东财/新浪兜底）----
    indexes = []
    symbols = [v[0] for v in INDEX_CODES.values()]
    tix = tdxapi.index_quotes(symbols) or tc.index_quotes(symbols)
    if tix:
        for full, name in INDEX_CODES.values():
            d = tix.get(full)
            if d and d.get("price") is not None:
                indexes.append({
                    "code": full, "name": name,
                    "price": d["price"], "change_pct": d.get("change_pct"),
                    "amount": d.get("amount"),
                })
    if not indexes and ak is not None:
        df = safe(ak.stock_zh_index_spot_em, symbol="沪深重要指数", label="指数快照")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("代码", ""))
                if code in INDEX_CODES:
                    full, name = INDEX_CODES[code]
                    indexes.append(
                        {
                            "code": full,
                            "name": name,
                            "price": to_float(row.get("最新价")),
                            "change_pct": to_float(row.get("涨跌幅")),
                            "amount": to_float(row.get("成交额")),
                        }
                    )
        if not indexes:
            df = safe(ak.stock_zh_index_spot_sina, label="指数快照(新浪)")
            if df is not None and not df.empty:
                wanted = {v[0]: v[1] for v in INDEX_CODES.values()}
                for _, row in df.iterrows():
                    full = str(row.get("代码", ""))
                    if full in wanted:
                        indexes.append(
                            {
                                "code": full,
                                "name": wanted[full],
                                "price": to_float(row.get("最新价")),
                                "change_pct": to_float(row.get("涨跌幅")),
                                "amount": to_float(row.get("成交额")),
                            }
                        )
        order = {v[0]: i for i, v in enumerate(INDEX_CODES.values())}
        indexes.sort(key=lambda x: order.get(x["code"], 99))
    if not indexes:
        missing.append("指数行情")
    out["indexes"] = indexes

    # ---- 市场宽度与成交额 ----
    # 关键：直连机器(无中继)上东财全市场快照被封会拖 ~160s(60+45+45+45 串行超时)，每次冷加载
    # 都卡死仪表盘。故【仅在配了中继时】用预聚合全市场快照(云端/中台快且全)；直连机器直接走下方
    # 乐咕活跃度(legulegu，独立于东财，1 次快调用)拿涨跌家数，成交额用指数口径近似。绝不在直连机
    # 上触发那条必然超时的快照链。
    out["up_count"] = out["down_count"] = out["flat_count"] = None
    out["total_amount"] = None
    _legu_zt = _legu_dt = None   # 乐咕涨停/跌停家数(免费、与涨跌家数同源同次请求),供下方涨停结构兜底
    # TDX pricevol 是服务端专门的 60s 全市场帧，一次分页取全，不占 quotes
    # 的 400 码热集。它只有 Now/LastClose/Volume，因此只用来算宽度，不冒充
    # 带成交额/估值的全量快照。
    tdx_breadth = tdxapi.pricevol_breadth()
    if tdx_breadth:
        out["up_count"] = tdx_breadth.get("up_count")
        out["down_count"] = tdx_breadth.get("down_count")
        out["flat_count"] = tdx_breadth.get("flat_count")
        missing.append(
            f"涨跌家数取自TDX pricevol 60秒全市场口径（有效{tdx_breadth.get('count')}只）"
        )
    spot = _spot_all() if out["up_count"] is None and _relay_api() else None
    if spot is not None and not spot.empty and "涨跌幅" in spot.columns:
        try:
            import pandas as pd  # noqa: PLC0415

            # 快照不完整(丢页)时【弃用】其宽度与成交额：部分和会把两市成交虚低一半以上
            #（实测丢20页→1.0万亿 vs 真实~2.2万亿），"部分口径"标注挡不住首页大字误导。
            # 铁律：宁可退乐咕(宽度全口径)+指数口径(成交近似)，不给会误导交易员的部分和。
            _notes = relay_staleness_notes(spot)
            _incomplete = any(n.startswith("全市场快照不完整") for n in _notes)
            if _incomplete:
                missing.append(_notes[0] + "，已弃用快照宽度/成交额（退乐咕全口径+指数口径成交）")
            else:
                chg = pd.to_numeric(spot["涨跌幅"], errors="coerce")
                # 整列不可数值化时 (>0).sum()=0、amt.sum()=0.0 会被当成"全部平盘/零成交"的真实值。
                # 必须确认确有有效数值，否则不采用，交由下方 missing/兜底处理。
                if chg.notna().any():
                    out["up_count"] = int((chg > 0).sum())
                    out["down_count"] = int((chg < 0).sum())
                    out["flat_count"] = int(((chg == 0)).sum())
                amt = pd.to_numeric(spot["成交额"], errors="coerce") if "成交额" in spot.columns else None
                if amt is not None and amt.notna().any():
                    out["total_amount"] = float(amt.sum())
                for note in _notes:   # 陈旧等其余标注照常透出
                    missing.append(note)
        except Exception:
            pass
    if out["up_count"] is None and ak is not None:
        legu = market_activity_legu()
        if legu:
            out["up_count"] = legu.get("up")
            out["down_count"] = legu.get("down")
            out["flat_count"] = legu.get("flat")
            _legu_zt = legu.get("limit_up")   # 乐咕同次请求即含涨停/跌停家数(免费)
            _legu_dt = legu.get("limit_down")
            # 乐咕口径与全市场快照可能略有差异（剔除 ST/统计时点不同），如实标注来源。
            if out["up_count"] is not None:
                missing.append("涨跌家数为乐咕活跃度口径(全市场快照不可用时的降级源)")
    if out["up_count"] is None:
        missing.append("涨跌家数")
    if out["total_amount"] is None:
        # 指数口径近似：沪市(上证指数) + 深市(深证成指) 成交额合计
        sh = next((i["amount"] for i in indexes if i["code"] == "sh000001"), None)
        sz = next((i["amount"] for i in indexes if i["code"] == "sz399001"), None)
        if sh is not None and sz is not None:
            out["total_amount"] = sh + sz
            missing.append("两市成交额为指数口径近似(沪指+深成指)")
        else:
            missing.append("两市成交额")

    # ---- 涨停结构 ----
    # 中继/云端：东财涨停池(push2ex)优先(连板高度/成交额齐全)；失败由 market_limitup 走免费公开涨停池兜底。
    # 直连机(无中继)：东财 push2ex 已被封，跳过必然失败的东财调用，涨停结构走免费公开池 / 乐咕家数。
    # 东财三池经「当日 last-good」层：瞬断用今日最后成功快照(标时点)，不跳回昨日(见 _pool_lastgood)。
    zt, _zt_asof = (_pool_lastgood("U", dc, _zt_pool) if _relay_api() else (None, None))
    # 跌停/炸板池同属东财 push2ex，直连机被封→跳过不死等(跌停走乐咕家数；炸板率免费源给不了→标缺失)。
    dt, _dt_asof = (_pool_lastgood("D", dc, _dt_pool) if _relay_api() else (None, None))
    zb, _zb_asof = (_pool_lastgood("Z", dc, _zb_pool) if _relay_api() else (None, None))
    _asof = next((a for a in (_zb_asof, _zt_asof, _dt_asof) if a), None)
    if _asof:
        missing.append(f"涨跌停/炸板池为今日 {_asof} 快照(东财池瞬时不可达，已用当日最后成功值，非最新)")
    # 直连机(东财池被封)：用免费公开涨停池（含连板天数/炸板/封单等当次可用字段）；
    # 公开池不可用再退乐咕(仅家数)。按次扣费的通达信 MCP 不进活动链路。
    tzt, tzt_stale = (_limit_list_today_or_prev(dc, "U") if zt is None else (None, None))
    # —— 全市场盘中扫描（腾讯批量·今日实时）：直连机在"盘中/盘后未落库"窗口的涨停/炸板/跌停真源
    #    (现价·当日最高·交易所涨停价 f47 同一响应直判,90s 缓存)。仅"今日视图"启用——盘前
    #    _resolve_data_day 已切昨日(dc≠今天)自然不触发；东财池可用(中继)或公开池已有今日数据时也不用。
    _scan = None
    if zt is None and (not tzt or tzt_stale) and dc == now_cn().strftime("%Y%m%d"):
        try:
            from . import tencent_source as _tcs  # noqa: PLC0415
            _scan = _tcs.market_zt_scan()
        except Exception:  # noqa: BLE001 — 扫描失败走原兜底链(乐咕+T-1 标昨)，绝不阻断
            _scan = None
    if zt is not None:
        _pool_n = int(len(zt))
        # 【口径恒定铁律 · 2026-07-06 事故】东财 zt 池是【剔除 ST】口径(实测 64 = 乐咕"真实涨停"，
        # 64+ST涨停9 = 含ST 73)。而免费公开池/腾讯扫描/乐咕"涨停"均为【含 ST】口径——
        # 若主数字直接用池家数，东财池断/通切换时卡面会在 73↔64 之间跳变(用户视角=数据错)。
        # 修：主数字恒定【含 ST】(优先乐咕)；东财池只供结构明细(连板/封板成交/晋级)，差异标注。
        # (07-02 曾根据单日 93=93 认为两池同口径——系当日巧合，结论作废。)
        # 含ST主数字源优先级：乐咕 →(快照完整时乐咕未被拉过)→ 腾讯扫描(ttl90缓存,同口径) → 池值兜底
        if _scan is None and dc == now_cn().strftime("%Y%m%d"):
            try:
                _scan = tc.market_zt_scan()
            except Exception:  # noqa: BLE001
                _scan = None
        _st_lu = _legu_zt if _legu_zt is not None else ((_scan or {}).get("limit_up"))
        if _st_lu is not None:
            out["limit_up"] = _st_lu
            if _st_lu != _pool_n:
                missing.append(f"涨停{_st_lu}为含ST口径；东财池明细{_pool_n}只为剔除ST口径(连板/封板成交/晋级为池口径)")
        else:
            out["limit_up"] = _pool_n
            missing.append(f"涨停家数为东财池口径(剔除ST，{_pool_n})；含ST口径源(乐咕/扫描)暂不可用")
        try:
            import pandas as pd  # noqa: PLC0415

            lb = pd.to_numeric(zt.get("连板数"), errors="coerce")
            out["lianban_height"] = int(lb.max()) if lb is not None and len(lb) else 0
            amt = pd.to_numeric(zt.get("成交额"), errors="coerce")
            out["limit_up_amount"] = float(amt.sum()) if amt is not None else None
            # 晋级率（今日口径）：东财池(剔ST)连板数≥2 家数 / 前一交易日涨停家数
            out["promote_rate"] = _promote_rate(
                [int(x) for x in lb.dropna().tolist()] if lb is not None else [], data_date)
        except Exception:
            out["lianban_height"] = None
            out["limit_up_amount"] = None
            missing.append("连板高度")
        # 多头验证(同为含ST口径才⚠)：公开涨停池 vs 乐咕"涨停"
        _tu_u = tx.limit_list(dc, "U")
        if _tu_u and _legu_zt is not None and abs(len(_tu_u) - _legu_zt) > max(3, int(_legu_zt * 0.05)):
            missing.append(f"⚠涨停家数双源偏差(含ST口径)：公开池{len(_tu_u)} vs 乐咕{_legu_zt}，请人工核对")
    elif tzt and not tzt_stale:
        out["limit_up"] = len(tzt)
        out["lianban_height"] = max((s.get("lianban") or 1) for s in tzt)
        _amts = [s.get("amount") for s in tzt if s.get("amount") is not None]
        out["limit_up_amount"] = float(sum(_amts)) if _amts else None
        # 晋级率（今日口径）：公开池连板数≥2 家数 / 前一交易日涨停家数（同源）
        out["promote_rate"] = _promote_rate([s.get("lianban") or 1 for s in tzt], data_date)
        missing.append("涨停取自免费公开涨跌停池（字段以当次返回为准）")
        # 乐咕为"触板/宽口径"计法,与收盘封板口径天然有差 → 信息注释,不告警(同口径对账见东财池路径)
        if _legu_zt is not None and abs(len(tzt) - _legu_zt) > max(5, int(len(tzt) * 0.1)):
            missing.append(f"涨停口径参考：乐咕宽口径{_legu_zt}(含触板/ST统计法不同)，本图以收盘封板口径{len(tzt)}为准")
    else:
        # 今日 limit_list_d 未落库：涨停【家数】优先今日腾讯全市场扫描(实时封板口径)，
        # 次选乐咕(触板宽口径)；连板高度/涨停成交额【退上一交易日】+醒目标 T-1。
        # 家数是今日、连板细分是昨日——前端必须分区标注，绝不当今日实时(铁律：标注即诚实降级，非冒充)。
        if _scan:
            out["limit_up"] = _scan["limit_up"]
            missing.append(
                f"涨停/炸板/跌停为腾讯全市场盘中扫描(沪深{_scan['judged']}只·{_scan['asof']}·实时封板口径,"
                "收盘后以公开盘后池核定)")
            if _legu_zt is not None and abs(_scan["limit_up"] - _legu_zt) > max(5, int((_scan["limit_up"] or 1) * 0.2)):
                missing.append(f"涨停口径参考：乐咕宽口径{_legu_zt}(含触板计法)，本图为实时封板口径{_scan['limit_up']}")
        else:
            out["limit_up"] = _legu_zt
        if tzt and tzt_stale:
            # 封板成交额：含今日新首板，盘中无全口径源(东财封板池/免费公开盘后池) → 恒 T-1 并标昨
            _amts = [s.get("amount") for s in tzt if s.get("amount") is not None]
            out["limit_up_amount"] = float(sum(_amts)) if _amts else None
            out["struct_stale_date"] = tzt_stale     # 语义=封板成交额的 T-1 标（连板/晋级见下,优先实时）
            # —— 盘中实时结构（确定性，腾讯单源自洽）：昨日公开涨停池名单 × 腾讯批量报价
            #    (price/high/当日涨停价 up_limit=f[47]，已三板块实测)。对交易者，晋级率/连板高度
            #    本质是【今日盘中】指标——昨日值对盘中打板决策几乎无用：
            #    · 晋级率 = 昨池今日仍封板家数 / 可判定昨池家数；
            #    · 连板高度 = max(续板者昨连板数+1)；无人续板而今日有涨停(乐咕)→高度=1
            #      （今日新首板恰为 1 板，不可能更高——口径精确，非近似）。
            #    腾讯批量失败/覆盖率不足 8 成 → 退 T-1 值并标昨（样本残缺不冒充总体，绝不编）。
            _live_done = False
            try:
                from . import tencent_source as _tc  # noqa: PLC0415
                _codes = [str(s.get("code") or "")[:6] for s in tzt if s.get("code")]
                _q = _tc.quotes(_codes) if _codes else {}
                _judged = 0      # 报价+涨停价齐全、可判定的昨池样本数
                _seal = []       # 今日续封者的昨日连板数
                _pool_zb = 0     # 昨池今日炸板(盘中曾触涨停价后开板)——部分口径,仅昨池范围
                for s in tzt:
                    q = _q.get(str(s.get("code") or "")[:6]) or {}
                    px, hi, ul = q.get("price"), q.get("high"), q.get("up_limit")
                    if px is None or ul is None:
                        continue
                    _judged += 1
                    if px >= ul - 1e-4:
                        _seal.append(int(s.get("lianban") or 1))
                    elif hi is not None and hi >= ul - 1e-4:
                        _pool_zb += 1
                if _judged >= max(5, int(len(tzt) * 0.8)):
                    out["promote_rate"] = round(len(_seal) / _judged * 100, 1)
                    out["lianban_height"] = (max(_seal) + 1) if _seal else (1 if (_legu_zt or 0) > 0 else None)
                    missing.append(
                        f"晋级率/连板高度为盘中实时推算(昨日涨停池{_judged}只×腾讯现价/涨停价,单源自洽)"
                        + (f"；昨池今日炸板{_pool_zb}只(部分口径,全市场炸板待盘后落库)" if _pool_zb else ""))
                    _live_done = True
            except Exception:  # noqa: BLE001 — 实时推算失败按缺失降级,退 T-1 标昨
                pass
            if not _live_done:
                out["lianban_height"] = max((s.get("lianban") or 1) for s in tzt)
                out["lianban_stale_date"] = tzt_stale
                # 晋级率（昨日口径,同日自洽）：昨日池连板≥2 / 前日涨停家数；标 T-1
                out["promote_rate"] = _promote_rate([s.get("lianban") or 1 for s in tzt], tzt_stale)
                if out["promote_rate"] is not None:
                    out["promote_stale_date"] = tzt_stale
            missing.append(f"涨停成交额为上一交易日({tzt_stale})数据，今日盘后待公开池更新")
        else:
            out["lianban_height"] = None
            out["limit_up_amount"] = None
            if _legu_zt is not None:
                missing.append("涨停家数取自乐咕(免费源,仅家数;连板高度/涨停成交额暂缺)")
            else:
                missing.append("涨停池")
    # 跌停：东财(中继)→ 免费公开跌停池→ 乐咕家数。【口径恒定】东财 dt 池同为剔除ST口径
    # (2026-07-06 实测 46 vs 乐咕含ST 53)——主数字优先乐咕(含ST)，池值仅作明细,差异标注。
    if dt is not None:
        _st_ld = _legu_dt if _legu_dt is not None else ((_scan or {}).get("limit_down"))
        if _st_ld is not None:
            out["limit_down"] = _st_ld
            if _st_ld != int(len(dt)):
                missing.append(f"跌停{_st_ld}为含ST口径；东财池明细{int(len(dt))}只为剔除ST口径")
        else:
            out["limit_down"] = int(len(dt))
            missing.append(f"跌停家数为东财池口径(剔除ST，{int(len(dt))})；含ST口径源暂不可用")
    elif _scan and _scan.get("limit_down") is not None:
        out["limit_down"] = _scan["limit_down"]   # 腾讯全市场扫描·今日实时(现价≤交易所跌停价)
    else:
        tdt = tx.limit_list(dc, "D")
        if tdt is not None:
            # 空列表是已确认的真实空池，必须保留为 0；只有 None 才表示上游失败。
            out["limit_down"] = len(tdt)
        elif _legu_dt is not None:
            out["limit_down"] = _legu_dt
            missing.append("跌停家数取自乐咕(免费源,仅家数)")
        else:
            # 公开跌停池与乐咕同时失败时，复用腾讯全市场同一时点扫描兜底。
            # 该扫描已有 90 秒缓存；仅今日视图触发，历史日期不拿实时值冒充。
            if _scan is None and dc == now_cn().strftime("%Y%m%d"):
                try:
                    _scan = tc.market_zt_scan()
                except Exception:  # noqa: BLE001 -- 扫描失败保持缺失，不阻断总览
                    _scan = None
            if _scan and _scan.get("limit_down") is not None:
                out["limit_down"] = _scan["limit_down"]
                missing.append(
                    f"跌停家数来自腾讯全市场同一时点扫描({_scan.get('asof')}·实时封板口径)"
                )
            else:
                out["limit_down"] = None
                missing.append("跌停池")
    # 炸板率 = 炸板数/(涨停数+炸板数)：东财(中继)→ 腾讯全市场扫描(今日实时)→ 免费公开炸板池
    if zt is not None and zb is not None:
        # 【口径恒定】东财 zb 池同为剔除ST口径(2026-07-06 实测 41 vs 扫描含ST 51)。主数字优先
        # 腾讯扫描(含ST,与涨停73同口径同时刻自洽,ttl90s 缓存开销可忽略)；扫描不可得才退池口径并标注。
        _scan_zb = _scan if _scan else (tc.market_zt_scan() if dc == now_cn().strftime("%Y%m%d") else None)
        if _scan_zb and _scan_zb.get("zhaban") is not None:
            out["zhaban_count"] = _scan_zb["zhaban"]
            out["zhaban_rate"] = _scan_zb.get("zhaban_rate")
            missing.append(f"炸板为腾讯全市场扫描含ST口径({_scan_zb.get('asof')})；东财池明细{len(zb)}只为剔除ST口径")
        else:
            total = len(zt) + len(zb)
            out["zhaban_count"] = len(zb)   # 炸板数（东财池口径，剔除ST）：与炸板率同源，精确非派生
            # 池为空（盘前未生成/无数据）时炸板率"未知"而非 0%——0% 会给情绪评分错误的乐观加分。
            out["zhaban_rate"] = round(len(zb) / total * 100, 1) if total else None
            missing.append("炸板为东财池口径(剔除ST)")
            if total == 0:
                missing.append("炸板率未知(涨停/炸板池为空)")
    elif _scan:
        # 今日实时·同时刻同口径：炸板=盘中触涨停价后开板，炸板率=炸/(炸+封)。无任何 T-1 标。
        out["zhaban_count"] = _scan["zhaban"]
        out["zhaban_rate"] = _scan["zhaban_rate"]
    else:
        tzb, tzb_stale = (_limit_list_today_or_prev(dc, "Z") if zb is None else (None, None))
        _zn = len(tzb) if tzb else None
        out["zhaban_count"] = _zn       # 公开炸板池家数（取不到为 None，前端显「—」不编）
        _un = out.get("limit_up")
        if tzb_stale and _zn is not None:
            out["zhaban_stale_date"] = tzb_stale
            # 昨日炸板 × 今日涨停 跨天混算无意义(铁律不拼口径)；但若涨停结构同样退到了同一
            # 交易日(tzt_stale==tzb_stale)，昨涨停+昨炸板为【同日自洽口径】→ 可算"昨日炸板率"并标 T-1。
            if tzt and tzt_stale == tzb_stale:
                _u_prev = len(tzt)
                out["zhaban_rate"] = round(_zn / (_u_prev + _zn) * 100, 1) if (_u_prev + _zn) else None
                if out["zhaban_rate"] is not None:
                    out["zhaban_rate_stale_date"] = tzb_stale
                missing.append(f"炸板数/炸板率为上一交易日({tzb_stale})同日口径，当日公开池尚未可用")
            else:
                out["zhaban_rate"] = None
                missing.append(f"炸板数为上一交易日({tzb_stale})数据；炸板率不与今日涨停跨天混算(铁律)")
        elif _zn is not None and _un:
            out["zhaban_rate"] = round(_zn / (_un + _zn) * 100, 1) if (_un + _zn) else None
        else:
            out["zhaban_rate"] = None
            missing.append("炸板数据")
    core_missing: list[str] = []
    if not out.get("indexes"):
        core_missing.append("指数行情")
    if any(out.get(key) is None for key in ("up_count", "down_count", "flat_count")):
        core_missing.append("全市场涨跌宽度")
    enhancements = ["两市成交额"] if out.get("total_amount") is None and not core_missing else []
    return _with_quality(out, missing_fields=core_missing, degraded_factors=enhancements)


def _enrich_tdx_industry(stocks: list[dict]) -> list[dict]:
    """把涨跌停池行业统一到 TDX relation 分类，原来源行业留作审计字段。"""
    if not stocks:
        return stocks
    mapping = tdxapi.industry_for_codes(stock.get("code") for stock in stocks)
    if not mapping:
        return stocks
    for stock in stocks:
        code = str(stock.get("code") or "").zfill(6)
        item = mapping.get(code)
        industry = str((item or {}).get("industry") or "").strip()
        if not industry:
            continue
        original = str(stock.get("industry") or "").strip()
        if original and original != industry:
            stock["source_industry"] = original
        stock["industry"] = industry
        stock["industry_source"] = str(item.get("source") or "TDX relation")
    return stocks


@ttl_cache(20, offhours_seconds=6 * 3600)
def market_limitup(date_compact: str | None = None) -> dict:
    missing: list[str] = []
    dc, data_date, day_note = _resolve_data_day(date_compact)
    out: dict = {"ok": True, "ts": ts_str(), "data_date": data_date, "missing": missing, "stocks": []}
    if day_note:
        missing.append(day_note)
    # 中继/云端：东财涨停池优先(字段最全)。直连机(东财 push2ex 被封)：用免费公开涨停池，
    # 字段以当次返回为准。按次扣费的通达信 MCP 不进活动链路。
    # 经「当日 last-good」层：瞬断用今日最后成功快照(标时点)，不跳回 T-1 梯队。
    zt, _zt_asof = (_pool_lastgood("U", dc, _zt_pool) if _relay_api() else (None, None))
    if _zt_asof:
        missing.append(f"涨停池为今日 {_zt_asof} 快照(东财瞬时不可达，已用当日最后成功值，非最新)")
    if zt is None:
        tl, tl_stale = _limit_list_today_or_prev(dc, "U")
        # —— 今日公开池尚无当日梯队(盘中/收盘初) → 腾讯全市场扫描的【今日封板名单】——
        # 2026-07-06 事故：此窗口曾直接退 T-1，页面顶着 data_date=今日 却装着上一交易日 108 只
        # (当日实际 73 只)，与同屏涨跌停卡自相矛盾。修：名单/价/涨跌幅/成交额全用今日扫描(单源自洽)；
        # 连板数 = 昨日涨停池推算(昨池续封=昨连板+1，新面孔=首板1——连板≥2必然昨日涨停，口径精确)；
        # 封单/首封时间/炸板次数今日无源 → None(标缺，绝不编)。盘后公开池有当日数据后自动切换。
        if (not tl or tl_stale) and dc == now_cn().strftime("%Y%m%d"):
            try:
                _scan = tc.market_zt_scan()
            except Exception:  # noqa: BLE001
                _scan = None
            if _scan and _scan.get("lu_list"):
                _prev_lb = {s.get("code"): (s.get("lianban") or 1) for s in (tl or [])}  # 昨池 code→连板
                stocks = []
                for q in _scan["lu_list"]:
                    prev_lb = _prev_lb.get(q.get("code"))
                    lb = prev_lb + 1 if prev_lb is not None else None
                    stocks.append({
                        "code": q.get("code"), "name": q.get("name"),
                        "price": q.get("price"), "change_pct": q.get("change_pct"),
                        "lianban": lb,
                        "lianban_label": (f"{lb}连板" if lb > 1 else "首板") if lb is not None else "板位待补",
                        "seal_amount": None, "first_time": "", "last_time": None,
                        "zhaban_count": None,
                        "amount": q.get("amount"), "turnover_rate": q.get("turnover_rate"),
                        "reason": "", "industry": None, "float_mv": None,
                    })
                stocks.sort(key=lambda s: (-(s.get("lianban") or 0), -(s.get("amount") or 0)))
                out["stocks"] = _enrich_tdx_industry(stocks)
                if _prev_lb:
                    missing.append(
                        f"今日梯队为腾讯全市场扫描封板口径({_scan.get('asof')})；连板=昨日涨停池推算"
                        "(续封+1/新面孔首板)；封单/首封时间/涨停原因待公开池补齐")
                else:
                    missing.append(
                        f"今日封板名单来自腾讯全市场扫描({_scan.get('asof')})；昨日涨停池暂不可达，"
                        "连板层级待补，未把未知板位冒充首板")
                return _with_quality(
                    out,
                    degraded_factors=[
                        "封单/首封时间/涨停原因待公开池补齐",
                        *([] if _prev_lb else ["连板层级待昨日涨停池补齐"]),
                    ],
                )
        if tl:
            # 开盘啦榜单增量 join（同日、按 code 对齐）：填涨停原因/题材 + 最后封板时间。
            # 主行情字段(价/连板/封单/成交)仍全来自 limit_list_d；kpl 只补"润色"字段，
            # 失败/当日公开数据未生成 → reason 留空照旧（增量层零影响，不拼主口径）。
            _kpl_dc = (tl_stale.replace("-", "") if tl_stale else dc)
            try:
                _kpl = {k["code"]: k for k in (tx.kpl_list(_kpl_dc, "涨停") or [])}
            except Exception:  # noqa: BLE001
                _kpl = {}
            stocks = []
            for s in tl:
                lb = s.get("lianban") or 1
                _k = _kpl.get(s.get("code") or "") or {}
                # 涨停原因优先 lu_desc(板块归因)，无则 theme(题材标签)；截断防超长
                _reason = (_k.get("reason") or _k.get("theme") or "")[:24]
                stocks.append({
                    "code": s.get("code"), "name": s.get("name"),
                    "price": s.get("price"), "change_pct": s.get("change_pct"),
                    "lianban": lb, "lianban_label": f"{lb}连板" if lb > 1 else "首板",
                    "seal_amount": s.get("seal_amount"), "first_time": s.get("first_time"),
                    "last_time": _k.get("last_time") or None, "zhaban_count": s.get("open_times"),
                    "amount": s.get("amount"), "turnover_rate": s.get("turnover_rate"),
                    "reason": _reason, "industry": s.get("industry") or None, "float_mv": None,
                })
            stocks.sort(key=lambda s: -(s.get("lianban") or 0))   # 连板数降序成梯队
            out["stocks"] = _enrich_tdx_industry(stocks)
            if _kpl:
                missing.append("涨停原因/题材来自同花顺公开池同日按代码对齐；主行情仍为公开涨停池")
            if tl_stale:
                out["stale_date"] = tl_stale
                # 归属日必须跟着内容走(2026-07-06 事故：data_date=今日 却装着昨日名单=冒充今日)
                out["data_date"] = tl_stale
                missing.append(f"连板梯队为上一交易日({tl_stale})数据，当日公开池尚未可用")
            else:
                missing.append("涨停梯队取自免费公开涨跌停池（连板/封单口径）")
        else:
            # limit_list_d 收盘后才落库：交易时段(尤其盘中)当日梯队尚无 → 明确区分，
            # 不让前端误报"非交易时段"(诚实性：缺什么说清什么；盘中涨停家数仍由总览/乐咕给出)。
            from datetime import time as _t  # noqa: PLC0415
            _n = now_cn()
            if is_trade_day(_n.date()) and _t(9, 15) <= _n.time() <= _t(15, 30):
                missing.append("涨停连板梯队明细尚未更新；盘中涨停家数见上方总览")
            else:
                missing.append("涨停池数据(非交易时段或当日无涨停)")
        if not out["stocks"]:
            return _with_quality(out, missing_fields=["涨停梯队明细"])
        if out.get("stale_date"):
            return _with_quality(out, data_state="stale", state_label="上一交易日")
        return _with_quality(out)
    stocks = []
    for _, row in zt.iterrows():
        lianban = to_int(row.get("连板数"), 1) or 1
        stocks.append(
            {
                "code": str(row.get("代码", "")).zfill(6),
                "name": str(row.get("名称", "")),
                "price": to_float(row.get("最新价")),
                "change_pct": to_float(row.get("涨跌幅")),
                "lianban": lianban,
                "lianban_label": f"{lianban}连板" if lianban > 1 else "首板",
                "seal_amount": to_float(row.get("封板资金")),
                "first_time": _fmt_time(row.get("首次封板时间")),
                "last_time": _fmt_time(row.get("最后封板时间")),
                "zhaban_count": to_int(row.get("炸板次数"), 0),
                "amount": to_float(row.get("成交额")),
                "turnover_rate": to_float(row.get("换手率")),
                "reason": str(row.get("所属行业", "") or ""),
                "industry": str(row.get("所属行业", "") or "") or None,
                "float_mv": to_float(row.get("流通市值")),
            }
        )
    stocks.sort(key=lambda s: (-(s["lianban"] or 0), -(s["seal_amount"] or 0)))
    out["stocks"] = _enrich_tdx_industry(stocks)
    # 东财涨停池为【剔除 ST】口径(2026-07-06 实测 64 vs 含ST 73)——梯队少的那几只是 ST 涨停，
    # 含 ST 总数见总览卡(乐咕口径)。如实标注，防止与涨跌停卡家数对不上被当数据错。
    missing.append(f"梯队为东财涨停池明细(剔除ST口径，{len(stocks)}只)；含ST涨停总数见涨跌停卡")
    if _zt_asof:
        return _with_quality(out, data_state="delayed", state_label="当日快照")
    return _with_quality(out)


def _fmt_time(v) -> str:
    s = str(v or "").strip()
    if len(s) == 6 and s.isdigit():
        return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"
    return s


def _ths_net_to_yuan(v):
    """同花顺资金流净额：数值单位为亿元；兼容 '5.23亿'/'-3100万' 字符串。"""
    if isinstance(v, str) and v.strip().endswith(("亿", "万")):
        return to_float(v)  # to_float 已完成单位换算，不可二次相乘
    f = to_float(v)
    return f * 1e8 if f is not None else None


@ttl_cache(45, offhours_seconds=6 * 3600)
def market_sectors() -> dict:
    """行业 + 概念涨幅榜。

    行业主路径为 TDX 行业指数 + sector_members×pricevol，同一分类体系同时供
    主线的涨停映射使用；公开板块源只作降级。已确认无效的 Zjl 资金流不参与模型。
    """
    missing: list[str] = []
    notes: list[str] = []
    degraded: list[str] = []
    out: dict = {
        "ok": True,
        "ts": ts_str(),
        "missing": missing,
        "missing_fields": [],
        "degraded_factors": degraded,
        "notes": notes,
        "sectors": [],
    }
    sectors: list[dict] = []

    # TDX 行业指数可直接返回实时涨跌幅/成交额；成分关系和 pricevol 补齐涨跌家数、领涨股。
    # 这三者与 relation 使用同一套行业名称，避免新浪49行业与涨停池细行业永远对不齐。
    tdx_industry = tdxapi.industry_market()
    if tdx_industry and tdx_industry.get("sectors"):
        sectors.extend(dict(row) for row in tdx_industry["sectors"])
        out["data_date"] = tdx_industry.get("data_date")
        notes.extend(message for message in tdx_industry.get("notes", []) if message not in notes)
        degraded.extend(
            message for message in tdx_industry.get("degraded_factors", [])
            if message not in degraded
        )
        out["coverage"] = dict(tdx_industry.get("coverage") or {})

    def _has_industry() -> bool:
        return any(row.get("kind") == "industry" for row in sectors)

    ak = get_ak()
    if ak is None:
        if not _has_industry():
            missing.append("行业板块行情")
            out["missing_fields"].append("行业板块行情")
        degraded.append("概念板块行情（akshare 未安装）")
        out["data_state"] = "missing" if not sectors else "degraded"
        out["sectors"] = sectors
        return out

    # 东财为主/同花顺辅：东财可达(中继 或 eastmoney_direct 境内服务器)→ 东财行业资金流为主源；
    # 东财不可达时跳到独立兜底，避免 3 个 push2 主源串行慢失败拖垮页面
    # (下方同花顺资金流 / 新浪行业 / 新浪概念，实测可达)。资金追踪 top10 板块即取这里的 main_flow。
    _use_em = _use_eastmoney() and em_available()   # 熔断期内直接走兜底(base.em_available)
    # TDX 行业可用时不再请求/合并另一套行业分类，更不能把两套名称硬拼。
    flow = None
    industry = None
    if not _has_industry() and _use_em:
        flow = safe(ak.stock_sector_fund_flow_rank, indicator="今日", sector_type="行业资金流",
                    label="行业资金流")
        industry = safe(ak.stock_board_industry_name_em, label="行业板块")
        em_report(flow is not None or industry is not None)
    flow_map: dict[str, float] = {}
    if flow is not None and not flow.empty:
        name_col = "名称" if "名称" in flow.columns else flow.columns[1]
        net_col = next((c for c in flow.columns if "主力净流入-净额" in c), None)
        if net_col:
            for _, row in flow.iterrows():
                v = to_float(row.get(net_col))
                if v is not None:
                    flow_map[str(row.get(name_col, ""))] = v
    if not flow_map and not _has_industry():
        # 东财资金流不可用 → 同花顺行业资金流隔离适配。
        # MiniRacer/V8 只在一次性子进程中运行；此处拿到的 main_net 已统一为元。
        for row in tx.moneyflow_ind_dc():
            v = to_float(row.get("main_net"))
            if v is not None:
                flow_map[str(row.get("name", ""))] = v
    if not _has_industry() and industry is not None and not industry.empty:
        for _, row in industry.iterrows():
            name = str(row.get("板块名称", ""))
            sectors.append(
                {
                    "kind": "industry",
                    "name": name,
                    "change_pct": to_float(row.get("涨跌幅")),
                    "leader_name": str(row.get("领涨股票", "") or ""),
                    "leader_change_pct": to_float(row.get("领涨股票-涨跌幅")),
                    "amount": to_float(row.get("成交额")),
                    "main_flow": flow_map.get(name),
                    "up_count": to_int(row.get("上涨家数")),
                    "down_count": to_int(row.get("下跌家数")),
                }
            )
    elif not _has_industry():
        missing.append("行业板块行情")

    if not _has_industry():
        # 东财行情断 → 第二层：同花顺免费行业资金榜重建行业段。
        # 领涨股/家数无→如实标缺。盘中它是 T-1 → 绝不拿昨日涨跌幅冒充今日行情,跳过走新浪。
        # (名称回填方案已实测否决:新浪行业名 vs 东财口径行业名 精确交集仅 4%,硬配=错配。)
        try:
            from . import mainforce_source as _mf  # noqa: PLC0415
            _ind = _mf.industry(limit=120) or {}
            _today_iso = tx.quote_data_date()
            if _ind.get("industries") and str(_ind.get("data_date")) == _today_iso:
                for r in _ind["industries"]:
                    sectors.append({
                        "kind": "industry", "name": r.get("name"),
                        "change_pct": r.get("change_pct"),
                        "leader_name": "", "leader_change_pct": None,
                        "amount": None,
                        "main_flow": r.get("net_amount"),   # 主力净额(元,东财口径)
                        "up_count": None, "down_count": None,
                    })
                missing.append("行业行情/主力资金取自同花顺免费公开榜（领涨股/家数/成交额缺）")
        except Exception:  # noqa: BLE001 — 回填层失败按缺失降级,走新浪
            pass
    if not _has_industry():
        # 东财行业不可用 → 新浪行业兜底（含领涨股与成交额，无主力资金）
        sina = safe(ak.stock_sector_spot, indicator="新浪行业", label="新浪行业")
        if sina is not None and not sina.empty:
            missing[:] = [m for m in missing if m != "行业板块行情"]
            for _, row in sina.iterrows():
                sectors.append(
                    {
                        "kind": "industry",
                        "name": str(row.get("板块", "")),
                        "change_pct": to_float(row.get("涨跌幅")),
                        "leader_name": str(row.get("股票名称", "") or ""),
                        "leader_change_pct": to_float(row.get("个股-涨跌幅")),
                        "amount": to_float(row.get("总成交额")),
                        "main_flow": flow_map.get(str(row.get("板块", ""))),
                        "up_count": None,
                        "down_count": None,
                    }
                )
            missing.append("行业涨跌家数")

    concept = safe(ak.stock_board_concept_name_em, label="概念板块") if _use_em else None
    if concept is not None and not concept.empty:
        try:
            import pandas as pd  # noqa: PLC0415

            concept = concept.assign(_chg=pd.to_numeric(concept["涨跌幅"], errors="coerce"))
            concept = concept.sort_values("_chg", ascending=False).head(40)
        except Exception:
            concept = concept.head(40)
        for _, row in concept.iterrows():
            sectors.append(
                {
                    "kind": "concept",
                    "name": str(row.get("板块名称", "")),
                    "change_pct": to_float(row.get("涨跌幅")),
                    "leader_name": str(row.get("领涨股票", "") or ""),
                    "leader_change_pct": to_float(row.get("领涨股票-涨跌幅")),
                    "amount": to_float(row.get("成交额")),
                    "main_flow": None,
                    "up_count": to_int(row.get("上涨家数")),
                    "down_count": to_int(row.get("下跌家数")),
                }
            )
    else:
        sina_c = safe(ak.stock_sector_spot, indicator="概念", label="新浪概念")
        if sina_c is not None and not sina_c.empty:
            try:
                import pandas as pd  # noqa: PLC0415

                sina_c = sina_c.assign(_chg=pd.to_numeric(sina_c["涨跌幅"], errors="coerce"))
                sina_c = sina_c.sort_values("_chg", ascending=False).head(40)
            except Exception:
                sina_c = sina_c.head(40)
            for _, row in sina_c.iterrows():
                sectors.append(
                    {
                        "kind": "concept",
                        "name": str(row.get("板块", "")),
                        "change_pct": to_float(row.get("涨跌幅")),
                        "leader_name": str(row.get("股票名称", "") or ""),
                        "leader_change_pct": to_float(row.get("个股-涨跌幅")),
                        "amount": to_float(row.get("总成交额")),
                        "main_flow": None,
                        "up_count": None,
                        "down_count": None,
                    }
                )
            missing.append("概念板块涨跌家数(新浪兜底口径不含)")
        else:
            missing.append("概念板块行情")
    sectors.sort(key=lambda s: -(s["change_pct"] if s["change_pct"] is not None else -999))
    out["sectors"] = sectors
    industry_rows = [row for row in sectors if row.get("kind") == "industry"]
    concept_rows = [row for row in sectors if row.get("kind") == "concept"]
    if not industry_rows and "行业板块行情" not in out["missing_fields"]:
        out["missing_fields"].append("行业板块行情")
    # 概念不是行业主线结构判定的必要输入；缺概念只降级，不把整张行业榜判死。
    if not concept_rows:
        degraded.append("概念板块行情")
    for message in missing:
        if message not in out["missing_fields"] and message not in degraded:
            degraded.append(message)
    out["coverage"] = {
        **(out.get("coverage") or {}),
        "industries": len(industry_rows),
        "concepts": len(concept_rows),
    }
    out["data_state"] = (
        "missing" if out["missing_fields"]
        else ("degraded" if degraded else "complete")
    )
    return out


@ttl_cache(20, offhours_seconds=6 * 3600)
def market_top(n: int = 30) -> dict:
    missing: list[str] = []
    out: dict = {
        "ok": True,
        "ts": ts_str(),
        "data_date": None,
        "missing": missing,
        "missing_fields": [],
        "degraded_factors": [],
        "notes": [],
        "stocks": [],
    }
    # 客户 TDX ``pricevol`` 一次返回全市场 60 秒帧，先用它确定真实涨幅榜成分；
    # 再仅对榜单成员请求 quotes，补成交额/涨速。这样不再因为本机无法直连东财
    # 就把「涨停池」冒充「全市场涨幅榜」，也不会触碰服务端 400 码热集上限。
    tdx_frame = tdxapi.pricevol_snapshot()
    if tdx_frame:
        names = tdxapi.stock_universe() or {}
        ranked = sorted(
            (tdx_frame.get("stocks") or {}).values(),
            key=lambda row: -(row.get("change_pct") if row.get("change_pct") is not None else -999),
        )[:n]
        codes = tuple(row.get("code") for row in ranked if row.get("code"))
        live = tdxapi.quotes(codes, max_age=60, wait=2) if codes else {}
        # 新代码首次进入供应方热集时 2 秒可能仍为 pending；与行业指数通道一致，
        # 覆盖不足时只重试一次并等满 5 秒，避免把正常冷启动误报成长期数据缺口。
        if codes and len(live) < max(1, int(len(codes) * 0.7)):
            live = tdxapi.quotes(codes, max_age=60, wait=5)
        out["stocks"] = [
            {
                "code": row.get("code"),
                "name": (live.get(row.get("code")) or {}).get("name")
                or names.get(row.get("code"), ""),
                "price": (live.get(row.get("code")) or {}).get("price") or row.get("price"),
                "change_pct": (live.get(row.get("code")) or {}).get("change_pct")
                if (live.get(row.get("code")) or {}).get("change_pct") is not None
                else row.get("change_pct"),
                "amount": (live.get(row.get("code")) or {}).get("amount"),
                "turnover_rate": None,
                "speed": (live.get(row.get("code")) or {}).get("speed"),
            }
            for row in ranked
        ]
        out["data_date"] = tdx_frame.get("data_date")
        out["source"] = tdx_frame.get("source") or "TDX pricevol"
        out["coverage"] = {
            "market_snapshot": {
                "complete": int(tdx_frame.get("count") or 0),
                "total": int(tdx_frame.get("count") or 0),
            },
            "quote_enrichment": {"complete": len(live), "total": len(ranked)},
        }
        out["notes"] = [
            f"全市场涨幅榜取自TDX pricevol 60秒帧（{int(tdx_frame.get('count') or 0)}只）",
            "榜单成员成交额/涨速由TDX realtime quotes补齐；换手率源字段未提供，留空不编造",
        ]
        if len(live) < len(ranked):
            out["degraded_factors"] = [f"榜单实时报价补齐{len(live)}/{len(ranked)}"]
        out["data_state"] = "degraded" if out["degraded_factors"] else "complete"
        return out
    # 直连机(无中继)上东财全市场快照被封会拖 ~160s 串行超时(详见 market_overview 同款守卫)，
    # 故仅中继/云端走预聚合快照；直连机直接落到下方涨停池近似涨幅榜分支。
    spot = _spot_all() if _relay_api() else None
    # 残缺快照(丢页)做涨幅榜必失真——审计实测:丢50页只剩1000只时,"涨幅榜"首位竟是 -0.58%,
    # 而当日实有 93 只涨停(全不在残池里)。成分失真比数值陈旧更误导 → 视同快照不可用,走涨停池口径。
    if spot is not None and not spot.empty and any(
        n.startswith("全市场快照不完整") for n in relay_staleness_notes(spot)
    ):
        missing.append("全市场快照不完整(丢页),涨幅榜改用涨停池口径(残池排榜必失真)")
        spot = None
    if spot is None or (hasattr(spot, "empty") and spot.empty):
        # 快照不可用 → 用涨停池近似涨幅榜（口径变窄，如实标注）
        zt = market_limitup()
        if zt.get("stocks"):
            stocks = sorted(
                zt["stocks"], key=lambda s: -(s["change_pct"] if s["change_pct"] is not None else 0)
            )[:n]
            out["stocks"] = [
                {
                    "code": s["code"], "name": s["name"], "price": s["price"],
                    "change_pct": s["change_pct"], "amount": s["amount"],
                    "turnover_rate": s["turnover_rate"], "speed": None,
                }
                for s in stocks
            ]
            missing.append("涨幅榜为涨停池口径近似（全市场快照不可用）")
            # 涨停池在盘前/非交易日回退到上一交易日，归属日与提示必须随数据一并透传，
            # 否则昨日涨停股会顶着当前时间戳被当成今日涨幅榜（含注入 LLM 上下文）。
            out["data_date"] = zt.get("data_date")
            missing.extend(m for m in zt.get("missing", []) if m not in missing)
        else:
            missing.append("全市场快照")
        return out
    # 中台快照陈旧/不完整时榜单非实时全市场口径，同步标注
    _stale_notes = relay_staleness_notes(spot)
    missing.extend(_stale_notes)
    out["data_date"] = now_cn().strftime("%Y-%m-%d")   # 快照归属今日(哪怕时点旧)；修此前恒 None
    try:
        import pandas as pd  # noqa: PLC0415

        df = spot.copy()
        df["_chg"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
        df = df.dropna(subset=["_chg"]).sort_values("_chg", ascending=False).head(n)
        stocks = []
        for _, row in df.iterrows():
            stocks.append(
                {
                    "code": str(row.get("代码", "")).zfill(6),
                    "name": str(row.get("名称", "")),
                    "price": to_float(row.get("最新价")),
                    "change_pct": to_float(row.get("涨跌幅")),
                    "amount": to_float(row.get("成交额")),
                    "turnover_rate": to_float(row.get("换手率")),
                    "speed": to_float(row.get("涨速")),
                }
            )
        # 快照陈旧/不完整 → 用腾讯批量把榜单成员的 价/涨跌幅/成交额/换手 刷成实时并重排。
        # (审计实测:中台快照可陈旧 4.6 小时,涨幅榜顶着旧值=给交易员错价。榜单【成分】仍取自
        #  快照(可能漏新面孔,如实标注),但【数值】保证实时;腾讯失败则保留原值+原陈旧标注,不编。)
        if stocks and _stale_notes:
            try:
                from . import tencent_source as _tc  # noqa: PLC0415
                _q = _tc.quotes([s["code"] for s in stocks]) or {}
                _hit = 0
                for s in stocks:
                    q = _q.get(s["code"]) or {}
                    if q.get("price") is not None:
                        _hit += 1
                        s["price"] = q.get("price")
                        s["change_pct"] = q.get("change_pct")
                        s["amount"] = q.get("amount")
                        s["turnover_rate"] = q.get("turnover_rate")
                        s["speed"] = None   # 腾讯无涨速,置缺不留旧值
                if _hit >= max(3, int(len(stocks) * 0.7)):
                    stocks.sort(key=lambda s: -(s["change_pct"] if s["change_pct"] is not None else -99))
                    missing.append(f"涨幅榜成分取自快照、数值已用腾讯实时刷新({_hit}/{len(stocks)}只)并重排；新上榜股可能缺席")
            except Exception:  # noqa: BLE001 — 刷新失败保留快照原值(已有陈旧标注),绝不编
                pass
        out["stocks"] = stocks
    except Exception:
        missing.append("涨幅榜解析")
    return out


@ttl_cache(60, offhours_seconds=6 * 3600)
def market_stockflow(n: int = 6) -> dict:
    """个股资金 净流入/净流出 TOP（首页「个股资金 TOP」卡）。

    数据源：free_source.moneyflow_market_both（全市场免费公开个股资金排名，
    在全市场两端取净额降序前 n[流入] 与升序前 n[流出]——故净流出股可如实呈现）。
    口径：主力净额遵循公开排名供应方口径，不伪拆超大/大/中/小单；单位【元】，change_pct 为当日涨跌幅【%】。
    公开源失败/空 → ok=True + 空列表 + 结构化状态「暂不可用」（绝不编造）。
    返回 {ok, ts, inflow:[{code,name,net,change_pct}], outflow:[...], missing:[]}（net 单位元）。"""
    missing: list[str] = []
    out: dict = {"ok": True, "ts": ts_str(), "inflow": [], "outflow": [], "missing": missing}
    both = tx.moneyflow_market_both(n)
    # 归属日透传+标注：moneyflow 盘后~18点落库，盘中/刚收盘拿到的是上一交易日——
    # 必须标清，否则暴跌日首页挂着上一交易日 +9% 的"净流入王"= 冒充今日(2026-07-06 事故)。
    _dd = both.get("data_date")
    out["data_date"] = _dd
    if _dd and _dd != now_cn().strftime("%Y-%m-%d"):
        missing.append(f"个股资金为最近交易日({_dd})盘后口径，今日数据约 18:00 后更新")

    def _pack(r: dict) -> dict:
        return {
            "code": r.get("code"),
            "name": r.get("name") or "",
            "net": r.get("main_net"),          # 主力净额(元)，正=净流入 负=净流出
            "change_pct": r.get("change_pct"), # 当日涨跌幅(%)
        }

    out["inflow"] = [_pack(r) for r in (both.get("inflow") or [])]
    out["outflow"] = [_pack(r) for r in (both.get("outflow") or [])]
    if not out["inflow"] and not out["outflow"]:
        missing.append("个股资金流数据不全")
        return _with_quality(out, missing_fields=["个股资金流排名"])
    if _dd and _dd != now_cn().strftime("%Y-%m-%d"):
        return _with_quality(out, data_state="delayed", state_label="最近交易日")
    return _with_quality(out)


@ttl_cache(120, offhours_seconds=6 * 3600)
def market_multiperiod(n: int = 12) -> dict:
    """多周期涨幅榜（首页「多周期涨幅榜」卡）：当日榜前 n 标的的 1/3/5/20 日累计涨幅。

    标的池：复用 market_top（当日涨幅榜，拿 code+name+change_pct(当日%)）。
    多周期：每个标的拉最近 ~25 根腾讯免费前复权日线的 close，按交易日算
      d3=(今收/3交易日前收-1)*100、d5、d20 同理；数据不足/失败 → 该周期 None（标 missing，不编）。
    题材 theme：当日榜常无行业字段，留空字符串（不臆造）。
    性能：n 控制在 ~12，每股一次免费日线调用（有源埋点）；失败不抛、记 missing。
    返回 {ok, ts, stocks:[{code,name,theme,d1,d3,d5,d20}], missing:[]}（d1=当日%）。"""
    request_started = time.monotonic()
    missing: list[str] = []
    out: dict = {
        "ok": True, "ts": ts_str(), "stocks": [], "missing": missing,
        "missing_fields": [], "degraded_factors": [], "notes": [],
    }
    today_compact = now_cn().strftime("%Y%m%d")   # 判断免费日线是否已含今日，决定多周期口径
    base = market_top(max(n, 1))
    pool = base.get("stocks") or []
    # 透传标的池来源的归属日/口径提示（涨停池近似、非交易日回退等），避免把昨日榜当今日。
    for m in base.get("missing", []):
        if m not in missing:
            missing.append(m)
    if base.get("data_date"):
        out["data_date"] = base.get("data_date")
    if not pool:
        missing.append("多周期涨幅榜标的池(当日涨幅榜不可用)")
        out["missing_fields"] = ["多周期涨幅榜标的池"]
        out["notes"] = list(missing)
        out["data_state"] = "missing"
        return out

    pool = pool[:n]
    industry_map = tdxapi.industry_for_codes(s.get("code") for s in pool)
    # 包含标的池 market_top 在内的整个接口只给 8s 预算。冷启动时
    # market_top 可能已用 7–9s，此时应立即返回 d1，不再叠加 12 次日线等待。
    history_deadline = request_started + 8.0
    history_budget_exhausted = False

    def _period_pct(closes, n, d1, today_in):
        """N 日累计涨幅%，口径统一到"今日"（与当日 d1 一致，杜绝"对不上"）。closes 升序、单位元。
        today_in=True（今日已入 daily）：(今收/N交易日前收-1)。
        today_in=False（今日未入库，closes 止于昨日）：今价=昨收×(1+d1%)，再除以 N 交易日前收 closes[-n]。
        —— 原 bug：d1 含今日、d3/d5/d20 却止于昨日（盘中/收盘初日线尚未更新），两者口径打架。"""
        if today_in:
            if len(closes) <= n or not closes[-1 - n] or closes[-1 - n] <= 0:
                return None
            return round((closes[-1] / closes[-1 - n] - 1.0) * 100.0, 2)
        if d1 is None or len(closes) < n or not closes[-n] or closes[-n] <= 0:
            return None
        today_px = closes[-1] * (1 + d1 / 100.0)
        return round((today_px / closes[-n] - 1.0) * 100.0, 2)

    any_period_missing = False
    for s in pool:
        code = s.get("code")
        rec = {"code": code, "name": s.get("name") or "",
               "theme": str((industry_map.get(str(code or "").zfill(6)) or {}).get("industry") or ""),
               "d1": s.get("change_pct"), "d3": None, "d5": None, "d20": None}
        if code and not history_budget_exhausted:
            if time.monotonic() >= history_deadline:
                history_budget_exhausted = True
            else:
                dated = _recent_closes_dated(code, count=30)
                if dated and len(dated) >= 2:
                    closes = [c for _, c in dated]
                    today_in = dated[-1][0] == today_compact   # 今日已入 daily？决定口径统一到今日
                    rec["d3"] = _period_pct(closes, 3, rec["d1"], today_in)
                    rec["d5"] = _period_pct(closes, 5, rec["d1"], today_in)
                    rec["d20"] = _period_pct(closes, 20, rec["d1"], today_in)
                else:
                    any_period_missing = True
        if code and history_budget_exhausted:
            any_period_missing = True
        if rec["d3"] is None or rec["d5"] is None or rec["d20"] is None:
            any_period_missing = True
        out["stocks"].append(rec)
    if any_period_missing:
        missing.append("部分标的的多周期涨幅数据不全（免费日线样本不足/不可用）")
    if history_budget_exhausted:
        missing.append("多周期接口已达 8 秒交互预算，剩余标的仅显示当日涨幅")
    complete = sum(
        1 for row in out["stocks"]
        if row.get("d1") is not None and row.get("d3") is not None
        and row.get("d5") is not None and row.get("d20") is not None
    )
    total = len(out["stocks"])
    out["coverage"] = {"period_rows": {"complete": complete, "total": total}}
    out["notes"] = list(dict.fromkeys(missing))
    if complete < total:
        out["degraded_factors"] = [f"多周期历史完整{complete}/{total}"]
        out["data_state"] = "degraded"
        out["state_label"] = f"部分覆盖 {complete}/{total}"
    else:
        out["data_state"] = "complete"
    return out


def _recent_closes(code: str, count: int = 25) -> list[float] | None:
    """取个股最近 count 根免费日线收盘价（升序，单位元）。"""
    bars = tx.daily_bars(code, count)
    vals = [to_float(row.get("c")) for row in bars]
    vals = [v for v in vals if v is not None and v > 0]
    return vals[-count:] if vals else None


def _recent_closes_dated(code: str, count: int = 30) -> list[tuple[str, float]] | None:
    """最近 count 根免费日线 ``[(trade_date, close)]`` 升序。"""
    vals: list[tuple[str, float]] = []
    for row in tx.daily_bars(code, count):
        d = str(row.get("d") or "").replace("-", "")[:8]
        c = to_float(row.get("c"))
        if len(d) == 8 and c is not None and c > 0:
            vals.append((d, c))
    return vals[-count:] if vals else None


_FLOW_LAST_GOOD: dict | None = None   # T9 §4 断供保险：最近一次成功的行业资金流（内存级，重启即空）


def market_flow_snapshot() -> dict | None:
    """只读已有的资金流快照，绝不在交互请求中触发慢上源。"""
    return dict(_FLOW_LAST_GOOD) if _FLOW_LAST_GOOD else None


@ttl_cache(45, offhours_seconds=6 * 3600)
def market_flow() -> dict:
    """行业+大盘主力资金流（45s 缓存）。断供保险（T9 §4）：上游时坏时好 → 本次采集失败/空
    时沿用上次成功值并明标「沿用 XX 时点」，页面绝不空窗；恢复后自动换新。ts 保留成功当刻
    （诚实：数据就是那个时点的）。重启后首次即失败无可沿用（内存级），如实显示暂不可用。"""
    global _FLOW_LAST_GOOD
    out = _market_flow_raw()
    if out.get("main_flow_sectors"):
        _FLOW_LAST_GOOD = dict(out)
        return out
    if _FLOW_LAST_GOOD and _FLOW_LAST_GOOD.get("main_flow_sectors"):
        prev = dict(_FLOW_LAST_GOOD)
        note = f"行业资金流本次采集失败，沿用 {prev.get('ts', '上次')} 成功值（非最新）"
        prev["note"] = note
        prev["missing"] = list(dict.fromkeys([*(prev.get("missing") or []), note]))
        prev["stale"] = True
        prev["data_state"] = "stale"
        prev["state_label"] = "沿用上次成功值"
        prev["notes"] = list(dict.fromkeys([*(prev.get("notes") or []), note]))
        return prev
    return out


def _market_flow_raw() -> dict:
    missing: list[str] = []
    out: dict = {
        "ok": True,
        "ts": ts_str(),
        "missing": missing,
        "main_flow_sectors": [],
        "main_flow_total": None,
        "main_flow_total_kind": None,
        "note": "",
    }
    ak = get_ak()
    if ak is None:
        missing.append("资金流数据（akshare 未安装）")
        return _with_quality(out, missing_fields=["行业资金流"])
    # 东财行业资金流仅在东财可达(中继 或 eastmoney_direct)时打；不可达(如纯免费源境内机)直接 None →
    # 走下方同花顺兜底，避免每次白打东财 push2 触发 RemoteDisconnected 报警 + 空等超时。
    _fem = _use_eastmoney() and em_available()   # 熔断门控(base.em_available)
    flow = safe(ak.stock_sector_fund_flow_rank, indicator="今日", sector_type="行业资金流", label="行业资金流") if _fem else None
    if _fem:
        em_report(flow is not None)
    if flow is not None and not flow.empty:
        net_col = next((c for c in flow.columns if "主力净流入-净额" in c), None)
        chg_col = next((c for c in flow.columns if "涨跌幅" in c), None)
        if net_col:
            try:
                import pandas as pd  # noqa: PLC0415

                df = flow.copy()
                df["_net"] = pd.to_numeric(df[net_col], errors="coerce")
                df = df.dropna(subset=["_net"]).sort_values("_net", ascending=False)
                # 净流入前10 + 净流出前5；板块数<15 时 head/tail 会重叠，按行索引去重避免重复计入。
                picked = list(dict.fromkeys(list(df.head(10).index) + list(df.tail(5).index)))
                rows = [(i, df.loc[i]) for i in picked]
                for _, row in rows:
                    out["main_flow_sectors"].append(
                        {
                            "name": str(row.get("名称", "")),
                            "main_net": to_float(row.get(net_col)),
                            "change_pct": to_float(row.get(chg_col)) if chg_col else None,
                        }
                    )
            except Exception:
                missing.append("行业资金流解析")
    if not out["main_flow_sectors"]:
        # 页面优先读取后台每 15 分钟落库的当日完整快照，不在用户请求中
        # 重跑 8~10 秒的同花顺采集。只有今天确实还没有快照时，才继续
        # 走下面 4 秒的快速探测；昨日数据不能冒充今日实时值。
        try:
            from . import capital_tracker  # noqa: PLC0415

            cached = capital_tracker.latest_sectors()
            today = now_cn().strftime("%Y-%m-%d")
            current = [row for row in cached if str(row.get("trade_date") or "") == today]
            if current:
                out["main_flow_sectors"] = [
                    {
                        "name": str(row.get("name") or ""),
                        "main_net": to_float(row.get("main_net")),
                        "change_pct": to_float(row.get("change_pct")),
                    }
                    for row in current
                    if row.get("name") and to_float(row.get("main_net")) is not None
                ]
                out["ts"] = str(current[0].get("ts") or out["ts"])
                out["source"] = str(current[0].get("source") or "后台资金快照")
                out["note"] = "读取盘中后台资金快照；页面请求不阻塞等待慢源"
        except Exception as exc:  # noqa: BLE001 -- 快照读取失败继续走快速在线探测
            log.info("行业资金后台快照读取失败: %s", str(exc)[:120])
    if not out["main_flow_sectors"]:
        # 东财不可用 → 子进程隔离的同花顺行业资金流兜底。
        rows = [
            {
                "name": str(row.get("name", "")),
                "main_net": to_float(row.get("main_net")),
                "change_pct": to_float(row.get("change_pct")),
            }
            for row in tx.moneyflow_ind_dc()
            if to_float(row.get("main_net")) is not None
        ]
        if rows:
            rows.sort(key=lambda r: -r["main_net"])
            out["main_flow_sectors"] = rows[:10] + rows[-5:] if len(rows) > 15 else rows
            missing[:] = [m for m in missing if m != "行业资金流"]
        else:
            missing.append("行业资金流")
    # 大盘资金流同样东财门控(含熔断)：不可达直接 None → 下方标"大盘主力净流入"缺失，不白打东财报警。
    _bem = _use_eastmoney() and em_available()
    big = safe(ak.stock_market_fund_flow, label="大盘资金流") if _bem else None
    if _bem:
        em_report(big is not None)
    if big is not None and not big.empty:
        net_col = next((c for c in big.columns if "主力净流入-净额" in c), None)
        if net_col is not None:
            out["main_flow_total"] = to_float(big.iloc[-1].get(net_col))
            if out["main_flow_total"] is not None:
                out["main_flow_total_kind"] = "market_total"
    if out["main_flow_total"] is None:
        # 东财日内大盘资金断 → 回填同花顺免费行业即时主力净额合计。
        # 行业合计只作公开供应方口径代理；是 T-1 则绝不冒充、保持缺失。
        try:
            from . import mainforce_source as _mf  # noqa: PLC0415
            _ind = _mf.industry(limit=120) or {}
            if _ind.get("industries") and str(_ind.get("data_date")) == now_cn().strftime("%Y-%m-%d"):
                _vals = [r.get("net_amount") for r in _ind["industries"] if r.get("net_amount") is not None]
                if _vals:
                    out["main_flow_total"] = float(sum(_vals))
                    out["main_flow_total_kind"] = "industry_proxy"
                    missing.append("大盘主力净流入为同花顺免费行业资金口径合计（当日代理值）")
        except Exception:  # noqa: BLE001
            pass
    if out["main_flow_total"] is None:
        missing.append("大盘主力净流入")
    missing.append("north")
    out["note"] = "北向资金实时数据已停止公布，不纳入分析"
    core_missing = [] if out["main_flow_sectors"] else ["行业资金流"]
    enhancements = ["大盘主力净流入"] if out["main_flow_total"] is None and not core_missing else []
    return _with_quality(out, missing_fields=core_missing, degraded_factors=enhancements)


@ttl_cache(45, offhours_seconds=3600)
def index_kline(full_code: str) -> dict:
    """指数行情图数据：当日分时 + 日K(160) + 周K(120)。full_code 如 sh000001。"""
    missing: list[str] = []
    name = next((v[1] for v in INDEX_CODES.values() if v[0] == full_code), full_code)
    out: dict = {"ok": True, "ts": ts_str(), "missing": missing, "code": full_code,
                 "name": name, "minute": [], "daily": [], "weekly": []}
    ak = get_ak()
    if ak is None:
        missing.extend(["分时", "日K"])
        return out

    # ---- 分时（新浪公开 JSONP，多日数据取最后一个交易日；不经 AkShare 隐藏 V8）----
    minute_rows = tx.minute_bars(full_code, "1")
    if minute_rows:
        try:
            minute_date = str(minute_rows[-1].get("day") or "")[:10]
            today_rows = [r for r in minute_rows if str(r.get("day") or "")[:10] == minute_date]
            cum_sum, n = 0.0, 0
            for r in today_rows:
                price = to_float(r.get("close"))
                if price is None:
                    continue
                n += 1
                cum_sum += price
                out["minute"].append(
                    {"t": str(r.get("day", ""))[11:16], "price": price,
                     "avg": round(cum_sum / n, 2),
                     "volume": to_int(r.get("volume"), 0)}
                )
            out["minute_date"] = minute_date
        except Exception:
            missing.append("分时解析")
    else:
        missing.append("分时")

    # ---- 日K（腾讯公开历史 → 取近 160；周K由日K重采样 120）----
    # 新浪同名历史接口内部使用 MiniRacer，线程池并发会导致进程级崩溃，禁止使用。
    ddf = safe(
        ak.stock_zh_index_daily_tx,
        symbol=full_code,
        start_date=(now_cn() - timedelta(days=1200)).strftime("%Y%m%d"),
        end_date=now_cn().strftime("%Y%m%d"),
        label="指数日K(腾讯)",
    )
    if ddf is not None and not ddf.empty:
        try:
            import pandas as pd  # noqa: PLC0415

            ddf = ddf.tail(800).copy()
            rows = [
                {
                    "d": str(r.get("date", ""))[:10],
                    "o": to_float(r.get("open")), "c": to_float(r.get("close")),
                    "h": to_float(r.get("high")), "l": to_float(r.get("low")),
                    "v": to_float(r.get("volume")),
                }
                for _, r in ddf.iterrows()
            ]
            out["daily"] = rows[-160:]
            wdf = ddf.copy()
            wdf["date"] = pd.to_datetime(wdf["date"])
            wdf = wdf.set_index("date").resample("W-FRI").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna()
            out["weekly"] = [
                {
                    "d": idx.strftime("%Y-%m-%d"),
                    "o": to_float(r["open"]), "c": to_float(r["close"]),
                    "h": to_float(r["high"]), "l": to_float(r["low"]), "v": to_float(r["volume"]),
                }
                for idx, r in wdf.tail(120).iterrows()
            ]
        except Exception:
            missing.append("日K解析")
    else:
        missing.append("日K")
    return out


def yesterday_limit_premium(date_compact: str) -> dict:
    """昨日涨停股今日表现（打板溢价），用于情绪评分。"""
    ak = get_ak()
    out: dict = {"ok": False, "avg_pct": None, "count": 0}
    if ak is None:
        return out
    df = safe(ak.stock_zt_pool_previous_em, date=date_compact, label="昨日涨停池")
    if df is None or df.empty:
        return out
    try:
        import pandas as pd  # noqa: PLC0415

        chg = pd.to_numeric(df["涨跌幅"], errors="coerce").dropna()
        if len(chg):
            out.update({"ok": True, "avg_pct": round(float(chg.mean()), 2), "count": int(len(chg))})
    except Exception:
        pass
    return out
