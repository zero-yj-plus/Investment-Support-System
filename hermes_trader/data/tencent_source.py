"""腾讯财经行情适配（qt.gtimg.cn）——【独立于东财的第二实时层】。

为什么需要它：客户机(境内)东财 push2 接口被 IP 级封禁（RemoteDisconnected），
而腾讯 qt.gtimg.cn 是另一套基建（全球 CDN 可达，海外开发机 93ms 即通），且【一次请求】
就给齐 价/涨跌幅/换手/量比/市盈率/市净率/流通市值/总市值/五档——这正是 TDX 数据 API 实时行情
【拿不到】的衍生估值字段（PE/量比/市值）。三层混合里它补上"估值字段层 + 指数层"。

可靠性：纯 stdlib urllib，短超时，失败一律返回 None/{}（由调用方回退），绝不抛、绝不编造。
单位已归一到本项目约定：价=元、成交额=元、成交量=股、市值=元、换手/涨跌幅=%。
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import ssl
import threading
import time
import urllib.parse
import urllib.request

import certifi

from .base import _record_source, log, now_cn, to_float, to_int, ttl_cache

_BASE = "https://qt.gtimg.cn/q="
_TIMEOUT = 6          # 腾讯很快(<200ms)，6s 足够且不拖慢页面
_KLINE_TIMEOUT = 3    # K 线属增强数据，不得一个标的吃掉整页交互预算
_UA = {"User-Agent": "Mozilla/5.0"}
_BATCH = 60           # 单请求最多拼多少个代码
_BULK_MAX_WORKERS = 10
_BULK_FAILURE_LIMIT = 6
_BULK_BREAKER_COOLDOWN = 60.0

# 全市场批量抓取独立熔断器。单股/指数查询不受它影响，避免一次全市场
# 网络故障把后续小查询也一并封死。一次批量任务内已经过多批探测，故确认
# 严重故障后可直接进入短冷却，无需再让 90+ 批逐批吃满超时。
_BULK_BREAKER = {"until": 0.0, "reason": ""}
_BULK_BREAKER_LOCK = threading.Lock()

_KLINE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"


class TencentTransportError(RuntimeError):
    """腾讯链路级失败；只供运行时熔断器区分“无此代码”。"""

# 腾讯是全球 CDN，必须【强制直连】：中继(relay)部署下 base._apply_relay_env() 会把进程级
# ALL_PROXY/HTTP(S)_PROXY 设成 socks5h://...，且其 NO_PROXY 豁免清单不含 qt.gtimg.cn——
# 裸 urlopen 会因此抛 'unknown url type: socks5h' 被吞成空响应，使腾讯在中继/云端机彻底哑火。
# 空 ProxyHandler({}) 屏蔽全部代理 scheme(含 all)，让腾讯无论部署形态都直连其 CDN。
_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    # macOS/Python 的系统 CA 在部分客户机无法验证腾讯证书链；
    # httpx 已依赖 certifi，显式用其 CA bundle 保持 HTTPS 严格校验，
    # 不使用关闭证书验证的“修复”。
    urllib.request.HTTPSHandler(
        context=ssl.create_default_context(cafile=certifi.where())
    ),
)


@ttl_cache(6 * 3600, fail_seconds=300)
def index_trade_dates() -> set[str]:
    """最近约 640 个上证指数真实开市日，作为交易日历的历史真值。

    腾讯公开 K 线一次请求返回约 2.5 年，不执行 JS/V8；失败返回空集合，由
    ``base.trade_dates`` 明确降级到本地节假日表。
    """
    url = _KLINE_URL
    params = {
        "_var": "kline_dayqfq",
        "param": (
            f"sh000001,day,{now_cn().year - 3}-01-01,"
            f"{now_cn().year + 1}-12-31,640,qfq"
        ),
        "r": "0.8205512681390605",
    }
    started = time.monotonic()
    try:
        req = urllib.request.Request(
            f"{url}?{urllib.parse.urlencode(params)}", headers=_UA
        )
        raw = _opener.open(req, timeout=_TIMEOUT).read().decode("utf-8", "ignore")
        marker = raw.find("={")
        payload = json.loads(raw[marker + 1:] if marker >= 0 else raw)
        block = ((payload.get("data") or {}).get("sh000001") or {})
        rows = block.get("day") or block.get("qfqday") or []
        dates = {
            str(row[0])[:10]
            for row in rows
            if isinstance(row, list) and row and len(str(row[0])) >= 10
        }
        _record_source(
            "腾讯上证指数交易日",
            (time.monotonic() - started) * 1000,
            bool(dates),
            "tencent",
            None if dates else "空响应",
        )
        return dates
    except Exception as exc:  # noqa: BLE001
        _record_source(
            "腾讯上证指数交易日",
            (time.monotonic() - started) * 1000,
            False,
            "tencent",
            str(exc)[:120],
        )
        return set()


def _symbol(code: str) -> str:
    """6 位代码 → 腾讯前缀符号。沪 6/9/5(基金)、京 8/4/920、余深。"""
    code = str(code).strip().zfill(6)
    if code.startswith(("8", "4")) or code.startswith("920"):
        return "bj" + code
    if code.startswith(("6", "9", "5")):
        return "sh" + code
    return "sz" + code


def _parse_daily_kline_payload(
    raw: str,
    symbol: str,
    *,
    adjust: str = "qfq",
    fetched_at: str | None = None,
) -> list[dict]:
    """解析腾讯 ``newfqkline`` 日 K 响应。

    腾讯的单根数组契约为
    ``[日期, 开, 收, 高, 低, 量能, 除权信息, 换手率, 成交额(万元), ...]``。
    第 6 列在科创板是股、其余 A 股是手；保留兼容字段 ``v`` 的供应方
    原值，同时新增统一为股的 ``v_shares`` 和统一为元的 ``amount``。
    调整方式请求前复权时，只接受 ``qfqday``；上游若只回 ``day`` 就
    显式返空，不把未复权数据冒充前复权。
    """
    try:
        marker = raw.find("={")
        payload = json.loads(raw[marker + 1:] if marker >= 0 else raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("code") not in (0, "0", None):
        return []
    block = ((payload.get("data") or {}).get(symbol) or {})
    key = "qfqday" if adjust == "qfq" else "day"
    source_rows = block.get(key)
    if not isinstance(source_rows, list):
        return []

    fetched = fetched_at or now_cn().isoformat(timespec="seconds")
    source = "腾讯财经·前复权日K" if adjust == "qfq" else "腾讯财经·日K"
    by_date: dict[str, dict] = {}
    for row in source_rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        day = str(row[0] or "")[:10]
        if len(day) != 10 or day[4:5] != "-" or day[7:8] != "-":
            continue
        open_, close, high, low = (
            to_float(row[1]),
            to_float(row[2]),
            to_float(row[3]),
            to_float(row[4]),
        )
        volume = to_float(row[5])
        amount_wan = to_float(row[8]) if len(row) > 8 else None
        if (
            any(value is None or value <= 0 for value in (open_, close, high, low))
            or volume is None
            or volume < 0
        ):
            continue
        code6 = symbol[2:]
        volume_multiplier = 1 if code6.startswith("688") else 100
        by_date[day] = {
            "d": day,
            "o": open_,
            "c": close,
            "h": high,
            "l": low,
            "v": volume,
            "v_shares": volume * volume_multiplier,
            "amount": round(amount_wan * 10_000, 2) if amount_wan is not None and amount_wan >= 0 else None,
            "source": source,
            # 日 K 事件时点只能确认到交易日，不伪造分秒。
            "as_of": day,
            "fetched_at": fetched,
            "adjustment": adjust,
            "volume_unit": "股" if volume_multiplier == 1 else "手",
            "amount_unit": "元",
        }
    return [by_date[day] for day in sorted(by_date)]


def _stock_daily_bars_uncached(code: str, days: int = 120, *, adjust: str = "qfq") -> list[dict]:
    """纯 urllib/JSON 的腾讯 A 股日 K，不导入 AkShare/JavaScript/V8。

    失败返回空列表，交由调用方显式降级；绝不将调整口径或缺失值
    偷换为可用数据。
    """
    count = max(1, min(int(days), 800))
    raw_code = str(code or "").strip().lower()
    symbol = raw_code if (
        len(raw_code) == 8 and raw_code[:2] in {"sh", "sz", "bj"}
        and raw_code[2:].isdigit()
    ) else _symbol(raw_code)
    adjustment = "qfq" if adjust == "qfq" else "none"
    params = {
        "_var": "kline_dayqfq" if adjustment == "qfq" else "kline_day",
        "param": f"{symbol},day,,,{count},{adjustment if adjustment == 'qfq' else ''}",
        "r": f"{time.time():.6f}",
    }
    started = time.monotonic()
    try:
        req = urllib.request.Request(
            f"{_KLINE_URL}?{urllib.parse.urlencode(params)}", headers=_UA
        )
        raw = _opener.open(req, timeout=_KLINE_TIMEOUT).read().decode("utf-8", "ignore")
        rows = _parse_daily_kline_payload(raw, symbol, adjust=adjustment)
        rows = rows[-count:]
        _record_source(
            "腾讯个股日K",
            (time.monotonic() - started) * 1000,
            bool(rows),
            "tencent-urllib",
            None if rows else f"缺少{adjustment}数组或空响应",
        )
        return rows
    except Exception as exc:  # noqa: BLE001 - 数据边界必须显式降级
        _record_source(
            "腾讯个股日K",
            (time.monotonic() - started) * 1000,
            False,
            "tencent-urllib",
            str(exc)[:120],
        )
        log.info("腾讯个股日K(%s) 失败: %s", symbol, str(exc)[:120])
        return []


@ttl_cache(300, fail_seconds=30)
def stock_daily_bars(code: str, days: int = 120, *, adjust: str = "qfq") -> list[dict]:
    """带短 TTL 的交互查询入口。"""
    return _stock_daily_bars_uncached(code, days, adjust=adjust)


def stock_daily_bars_uncached(code: str, days: int = 120, *, adjust: str = "qfq") -> list[dict]:
    """批处理降级入口；结果不留在进程级 TTL 缓存，避免全市场刷新撑高内存。"""
    return _stock_daily_bars_uncached(code, days, adjust=adjust)


def _fetch(
    symbols: list[str], *, raise_transport: bool = False
) -> dict[str, list[str]]:
    """批量拉取，返回 {symbol: 字段list}。失败返回 {}（不抛）。"""
    if not symbols:
        return {}
    url = _BASE + ",".join(symbols)
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers=_UA)
        raw = _opener.open(req, timeout=_TIMEOUT).read().decode("gbk", "ignore")
        out: dict[str, list[str]] = {}
        for line in raw.split(";"):
            line = line.strip()
            if "=" not in line or '"' not in line:
                continue
            key = line.split("=", 1)[0].strip()          # v_sh600000
            sym = key.split("_", 1)[-1]
            payload = line.split('"', 2)[1]
            f = payload.split("~")
            if len(f) >= 50:                              # 完整行才收
                out[sym] = f
        _record_source("腾讯行情", (time.monotonic() - t0) * 1000, bool(out), "tencent",
                       None if out else "空响应")
        return out
    except Exception as e:
        _record_source("腾讯行情", (time.monotonic() - t0) * 1000, False, "tencent", str(e)[:120])
        log.info("腾讯行情失败: %s", str(e)[:120])
        if raise_transport:
            raise TencentTransportError(type(e).__name__) from None
        return {}


def _bulk_breaker_open() -> tuple[bool, str]:
    with _BULK_BREAKER_LOCK:
        return time.monotonic() < _BULK_BREAKER["until"], str(_BULK_BREAKER["reason"])


def _bulk_breaker_reset() -> None:
    with _BULK_BREAKER_LOCK:
        _BULK_BREAKER["until"] = 0.0
        _BULK_BREAKER["reason"] = ""


def _bulk_breaker_trip(reason: str) -> None:
    with _BULK_BREAKER_LOCK:
        _BULK_BREAKER["until"] = time.monotonic() + _BULK_BREAKER_COOLDOWN
        _BULK_BREAKER["reason"] = reason
    log.warning("腾讯批量行情熔断 %.0f 秒: %s", _BULK_BREAKER_COOLDOWN, reason)


def _fetch_many(symbols: list[str]) -> dict[str, list[str]]:
    """并发拉取大批量腾讯行情，严重失败时有界熔断。

    只维持最多 _BULK_MAX_WORKERS 个在途请求；连续空批达阈值后不再提交
    剩余批次。因此全断时最多等待一组在途请求，不会把 90+ 批串成长等待。
    空响应仍由上层覆盖率门禁决定是否可用。
    """
    unique = list(dict.fromkeys(symbols))
    if not unique:
        return {}
    if len(unique) <= _BATCH:
        return _fetch(unique)

    opened, reason = _bulk_breaker_open()
    if opened:
        _record_source("腾讯批量行情", 0, False, "circuit", reason or "熔断冷却中")
        return {}

    chunks = [unique[i:i + _BATCH] for i in range(0, len(unique), _BATCH)]
    out: dict[str, list[str]] = {}
    iterator = iter(chunks)
    pending = set()
    empty_streak = 0
    empty_batches = 0
    completed_batches = 0
    tripped = False
    t0 = time.monotonic()

    executor = ThreadPoolExecutor(max_workers=min(_BULK_MAX_WORKERS, len(chunks)))
    try:
        for _ in range(min(_BULK_MAX_WORKERS, len(chunks))):
            pending.add(executor.submit(_fetch, next(iterator)))

        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    data = future.result()
                except Exception:  # noqa: BLE001 - _fetch 已保证不抛，再做边界保护
                    data = {}
                completed_batches += 1
                if data:
                    out.update(data)
                    empty_streak = 0
                else:
                    empty_batches += 1
                    empty_streak += 1

            # 连续空批，或已有足够样本证明是大面积故障时，停止继续提交。
            severe_failure = (
                empty_streak >= _BULK_FAILURE_LIMIT
                or (
                    completed_batches >= _BULK_MAX_WORKERS
                    and empty_batches / completed_batches >= 0.8
                    and len(out) < len(unique) * 0.2
                )
            )
            if severe_failure:
                tripped = True
                break

            while len(pending) < _BULK_MAX_WORKERS:
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                pending.add(executor.submit(_fetch, chunk))
    finally:
        for future in pending:
            future.cancel()
        # 只需等已经在运行的有界任务（最多 10 个、6s 超时）。
        executor.shutdown(wait=True, cancel_futures=True)

    latency_ms = (time.monotonic() - t0) * 1000
    received_ratio = len(out) / len(unique)
    if tripped and received_ratio < 0.2:
        reason = (
            f"严重批量失败：已完成{completed_batches}批、空{empty_batches}批、"
            f"收到{len(out)}/{len(unique)}"
        )
        _bulk_breaker_trip(reason)
    elif out:
        _bulk_breaker_reset()
    _record_source(
        "腾讯批量行情",
        latency_ms,
        bool(out),
        "tencent-bulk",
        None if out else "批量行情空响应",
    )
    return out


def _pos(x):
    """价格类零值守卫：股价/开高低不可能为 ≤0，零成交伪零价(0.00)置 None，绝不显示 0.00 误导。"""
    return x if (x is not None and x > 0) else None


def _parse_stock(f: list[str]) -> dict | None:
    """腾讯字段 list → 个股报价 dict（单位已归一）。含合理性守卫。"""
    price = to_float(f[3])
    if price is None or price <= 0:
        return None
    chg = to_float(f[32]) if len(f) > 32 else None
    if chg is not None and abs(chg) > 21:               # A股单日涨跌幅上限+余量，越界视为脏数据
        return None
    amount_wan = to_float(f[37]) if len(f) > 37 else None   # 成交额(万元)
    vol_raw = to_float(f[6]) if len(f) > 6 else None        # 成交量原值：见下方单位说明
    float_yi = to_float(f[44]) if len(f) > 44 else None     # 流通市值(亿元)
    total_yi = to_float(f[45]) if len(f) > 45 else None     # 总市值(亿元)
    # 成交量单位陷阱：腾讯对【科创板(688xxx)】的 f[6] 直接给【股】，其余板块(主板/创业板300/
    # 京A 8·4·920/基金)给【手】(1手=100股)。一律 *100 会把科创板虚增约 100 倍——实测 688981
    # amount/volume 推得均价 1.39 vs 实际 140.7（101x）。故按代码(f[2])判科创板，科创板不 *100。
    code6 = f[2] if len(f) > 2 else ""
    if vol_raw is None:
        volume = None
    elif code6.startswith("688"):
        volume = int(vol_raw)                               # 科创板：f[6] 已是股
    else:
        volume = int(vol_raw * 100)                         # 其余板块：手→股
    return {
        "name": f[1] if len(f) > 1 else "",
        "price": price,
        "change_pct": chg,
        # 价格类零值守卫：零成交个股(常见北交所)腾讯回 open/high/low=0，0.00 元是伪价(股价不可能为0)，
        # 置 None 而非显示 0.00 误导(铁律：宁缺毋假)。成交量/额可为 0(真零成交)，不在此守卫。
        "open": _pos(to_float(f[5])) if len(f) > 5 else None,
        "high": _pos(to_float(f[33])) if len(f) > 33 else None,
        "low": _pos(to_float(f[34])) if len(f) > 34 else None,
        "prev_close": _pos(to_float(f[4])) if len(f) > 4 else None,
        "amount": amount_wan * 1e4 if amount_wan is not None else None,    # 万元→元
        "volume": volume,                                                  # 股（科创板原值，余手→股）
        "turnover_rate": to_float(f[38]) if len(f) > 38 else None,         # 换手率%
        "volume_ratio": to_float(f[49]) if len(f) > 49 else None,          # 量比
        "pe": to_float(f[39]) if len(f) > 39 else None,                    # 市盈率(TTM)
        "pb": to_float(f[46]) if len(f) > 46 else None,                    # 市净率
        "total_mv": total_yi * 1e8 if total_yi is not None else None,      # 亿元→元
        "float_mv": float_yi * 1e8 if float_yi is not None else None,      # 亿元→元
        # 当日涨/跌停价（交易所口径，腾讯 47/48 字段）。2026-07-02 三板块实测核验：
        # 600519 主板 1193.01×1.1=1312.31 ✓ / 300308 创业 ×1.2=1467.80 ✓ / 688981 科创 ×1.2=185.38 ✓。
        # 供"昨日涨停池今日是否续封/炸板"的盘中实时判定（与 price/high 同一响应，单源自洽零拼口径）。
        "up_limit": _pos(to_float(f[47])) if len(f) > 47 else None,
        "down_limit": _pos(to_float(f[48])) if len(f) > 48 else None,
        # 供应方行情时间（A 股格式 YYYYMMDDHHMMSS）。证据网关据此判断盘前/盘后
        # 陈旧度，不能用本次 HTTP 抓取时刻冒充行情事件时刻。
        "as_of": f[30] if len(f) > 30 else None,
    }


@ttl_cache(90)
def market_zt_scan() -> dict | None:
    """全市场涨停/炸板/跌停【盘中实时】扫描（腾讯批量并行，独立于东财及盘后池）。

    为什么存在：直连机(东财被封)盘中拿不到全市场炸板/封板明细（公开池盘后才稳定）——此前只能
    退上一交易日并标"昨"。腾讯每条报价自带【交易所口径】当日涨停价 f[47]/跌停价 f[48]
    (含 ST±5%/主板10%/双创20% 规则，2026-07-02 三板块实测)，与现价/当日最高同一响应单源自洽：
      封板 = 现价≥涨停价；炸板 = 当日最高≥涨停价 且 现价<涨停价；跌停 = 现价≤跌停价。
    范围：沪深(60/00/30/68)，不含北交(±30%会触发 _parse_stock 涨跌幅>21 脏数据守卫，且对齐
    公开收盘池口径)。停牌/新股无有效高价或涨停价 → 不计入(judged 覆盖率兜底)。
    ≈87 批×10 线程并行，整体 1.5~3s；调用方自带 ttl 缓存。失败/覆盖率<80% → None(绝不冒充总体)。"""
    from . import free_source as _tx  # noqa: PLC0415 — 运行时导入，仅取免费代码表

    names = _tx._name_map()
    codes = [c for c in names if c[:2] in ("60", "00", "30", "68")]
    if len(codes) < 3000:   # 代码表残缺 → 不扫，退调用方兜底链
        return None
    syms = [_symbol(c) for c in codes]
    lu = zb = ld = judged = 0
    lu_list: list[dict] = []   # 今日封板名单(供涨停梯队在公开盘后池未稳定窗口显示今日口径)
    ld_list: list[dict] = []   # 今日跌停名单(供跌停风险池在公开盘后池未稳定窗口显示今日口径)
    data = _fetch_many(syms)
    for _sym, f in data.items():
        q = _parse_stock(f)
        if not q:
            continue
        px, hi, ul, dl = q.get("price"), q.get("high"), q.get("up_limit"), q.get("down_limit")
        if px is None or ul is None:
            continue
        judged += 1
        if px >= ul - 1e-4:
            lu += 1
            lu_list.append({
                "code": _sym[2:], "name": q.get("name") or "",
                "price": px, "change_pct": q.get("change_pct"),
                "amount": q.get("amount"), "turnover_rate": q.get("turnover_rate"),
            })
        elif hi is not None and hi >= ul - 1e-4:
            zb += 1
        if dl is not None and px <= dl + 1e-4:
            ld += 1
            ld_list.append({
                "code": _sym[2:], "name": q.get("name") or "",
                "price": px, "change_pct": q.get("change_pct"),
                "amount": q.get("amount"), "turnover_rate": q.get("turnover_rate"),
                "first_time": None, "seal_amount": None, "open_times": None,
            })
    if judged < int(len(codes) * 0.8):   # 样本残缺不冒充总体(网络劣化/腾讯限流时宁缺)
        log.warning("腾讯全市场扫描覆盖率不足: %d/%d", judged, len(codes))
        return None
    total = lu + zb
    return {
        # 【时钟铁律】必须北京时间(交易时间基准)——time.strftime 会用本机时区，开发机是 PDT，
        # 曾把 15:15 收盘扫描标成"00:15"直接吓坏用户(2026-07-06 事故)。
        "asof": now_cn().strftime("%H:%M"),
        "judged": judged,                 # 实际可判定样本数(剔停牌/无涨停价)
        "limit_up": lu,                   # 现价封板家数(实时封板口径)
        "zhaban": zb,                     # 触及涨停后开板(同时刻同口径)
        "limit_down": ld,
        "zhaban_rate": round(zb / total * 100, 1) if total else None,
        "lu_list": lu_list,               # 封板明细(价/涨跌幅/成交额/换手,单位元/%)
        "ld_list": ld_list,               # 跌停明细(价/涨跌幅/成交额/换手,单位元/%)
    }


def quote(code: str) -> dict | None:
    """单只个股实时报价（腾讯，独立于东财）：价/涨跌幅/换手/量比/PE/PB/市值/盘口。"""
    sym = _symbol(code)
    data = _fetch([sym])
    f = data.get(sym)
    return _parse_stock(f) if f else None


@ttl_cache(3, fail_seconds=2)
def _quotes_cached(codes: tuple[str, ...]) -> dict[str, dict]:
    """三秒批量快照，避免同一页的 quote/stock/诊断重复打同一公开源。"""
    out: dict[str, dict] = {}
    syms = [_symbol(c) for c in codes]
    for sym, f in _fetch_many(syms).items():
        q = _parse_stock(f)
        if q is not None:
            out[sym[-6:]] = q
    return out


def quotes(codes: list[str]) -> dict[str, dict]:
    """批量个股报价 → {6位代码: 报价dict}。并发分批并在严重失败时熔断。"""
    normalized = tuple(dict.fromkeys(str(code or "").strip().split(".", 1)[0].zfill(6) for code in codes))
    return _quotes_cached(normalized)


def index_quote(symbol: str) -> dict | None:
    """指数实时（symbol 形如 'sh000001'/'sz399001'）：价/涨跌幅/成交额。"""
    data = _fetch([symbol])
    f = data.get(symbol)
    if not f or len(f) < 38:
        return None
    price = to_float(f[3])
    if price is None or price <= 0:
        return None
    amount_wan = to_float(f[37])
    return {
        "name": f[1] if len(f) > 1 else "",
        "price": price,
        "change_pct": to_float(f[32]),
        "amount": amount_wan * 1e4 if amount_wan is not None else None,   # 万元→元
    }


def index_quotes(symbols: list[str]) -> dict[str, dict]:
    """批量指数 → {symbol: {price,change_pct,amount}}。"""
    out: dict[str, dict] = {}
    for sym, f in _fetch(list(symbols)).items():
        if len(f) < 38:
            continue
        price = to_float(f[3])
        if price is None or price <= 0:
            continue
        amount_wan = to_float(f[37])
        out[sym] = {
            "name": f[1] if len(f) > 1 else "",
            "price": price,
            "change_pct": to_float(f[32]),
            "amount": amount_wan * 1e4 if amount_wan is not None else None,
        }
    return out


# ---------------------------------------------------------------------------
# 港股指数 / 美股个股——【海外 CDN 可达，补客户机东财死后的整片空白】
# 列位与 A 股【不同】，下面的位号全部以 qt.gtimg.cn 真实返回校准(2026-06-18 实测)：
#
# 港股指数 q=hkHSI / hkHSTECH / hkHSCEI（实测 len=78，下标以恒生指数为准）：
#   f[1]=名称(恒生指数)  f[2]=代码(HSI)  f[3]=点位/现价(23924.810)
#   f[4]=昨收(24312.160) f[5]=今开(24145.190) f[31]=涨跌点(-387.350)
#   f[32]=涨跌幅%(-1.59) f[33]=最高(24163.250) f[34]=最低(23749.990)
#   f[37]=成交额【万港元】(35871454.161 → 3587亿港元，与恒指日成交额量级一致 → ×1e4=港元)
#
# 美股个股 q=usAAPL / usNVDA / usMSFT（实测 len=71，下标以苹果为准）：
#   f[1]=名称(苹果)  f[2]=代码(AAPL.OQ)  f[3]=现价(298.01)
#   f[4]=昨收(295.95) f[5]=今开(298.11) f[6]=成交量【股】(85962201，price*f[6]≈f[37]，故已是股、不 *100)
#   f[31]=涨跌额(2.06) f[32]=涨跌幅%(0.70) f[33]=最高(300.57) f[34]=最低(295.62)
#   f[35]=币种(USD) f[37]=成交额【元/美元】(25625100141 ≈ price*vol=256亿，已是元、非万)
#   f[39]=市盈率TTM(36.08) f[45]=总市值【亿美元】(43769.79 → 4.38万亿美元 → ×1e8=元)
# ---------------------------------------------------------------------------


def hk_index_quotes(symbols: list[str]) -> dict[str, dict]:
    """批量港股指数 → {symbol: {name,price,change_pct,amount}}。
    symbol 形如 'hkHSI'/'hkHSTECH'/'hkHSCEI'。失败/空返回 {}（不抛）。
    单位：price=点、amount=元(港元，f[37]万港元×1e4)。"""
    out: dict[str, dict] = {}
    for sym, f in _fetch(list(symbols)).items():
        if len(f) < 38:
            continue
        price = to_float(f[3])
        if price is None or price <= 0:
            continue
        amount_wan = to_float(f[37])               # 成交额(万港元) —— 与 A 股指数同列同单位
        out[sym] = {
            "name": f[1] if len(f) > 1 else "",
            "price": price,
            "change_pct": to_float(f[32]),
            "amount": amount_wan * 1e4 if amount_wan is not None else None,   # 万港元→港元
        }
    return out


def us_quotes(symbols: list[str]) -> dict[str, dict]:
    """批量美股个股 → {symbol: {name,price,change_pct,...}}。
    symbol 形如 'usAAPL'/'usNVDA'/'usMSFT'。失败/空返回 {}（不抛）。
    单位：price=元(美元)、amount=元(美元，f[37] 直接是元)、market cap=元(亿美元×1e8)。"""
    out: dict[str, dict] = {}
    for sym, f in _fetch(list(symbols)).items():
        if len(f) < 46:                            # 美股完整行 len=71；取到 f[45] 市值才算齐
            continue
        price = to_float(f[3])
        if price is None or price <= 0:
            continue
        chg = to_float(f[32]) if len(f) > 32 else None
        amount = to_float(f[37]) if len(f) > 37 else None       # 成交额：美股 f[37] 已是【元】(非万)
        vol = to_float(f[6]) if len(f) > 6 else None            # 美股 f[6] 已是【股】(非手，不 *100)
        total_yi = to_float(f[45]) if len(f) > 45 else None     # 总市值(亿美元)
        out[sym] = {
            "name": f[1] if len(f) > 1 else "",
            "price": price,
            "change_pct": chg,
            "open": to_float(f[5]) if len(f) > 5 else None,
            "high": to_float(f[33]) if len(f) > 33 else None,
            "low": to_float(f[34]) if len(f) > 34 else None,
            "prev_close": to_float(f[4]) if len(f) > 4 else None,
            "amount": amount,                                   # 元(美元)
            "volume": int(vol) if vol is not None else None,    # 股
            "pe": to_float(f[39]) if len(f) > 39 else None,     # 市盈率TTM
            "total_mv": total_yi * 1e8 if total_yi is not None else None,   # 亿美元→元
            "as_of": f[30] if len(f) > 30 else None,
        }
    return out
