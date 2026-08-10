"""个股真实数据：实时盘口、分时、日K、板块归属、资金流。"""
from __future__ import annotations

import concurrent.futures
import copy
import threading
import time

from .base import is_trade_day, now_cn, prev_trade_day, to_float, to_int, ts_str, ttl_cache
from .market import _spot_all, relay_staleness_notes, _relay_api
from . import free_source as tx
from . import tdx_api_source as tdxapi
from . import tencent_source as tc


STOCK_PROGRESSIVE_CONTRACT_VERSION = "stock-progressive-v1"


def _layer_contract(
    layer: str,
    *,
    blocks: tuple[str, ...],
    independent_from: tuple[str, ...],
) -> dict:
    return {
        "version": STOCK_PROGRESSIVE_CONTRACT_VERSION,
        "layer": layer,
        "blocks": list(blocks),
        "independent_from": list(independent_from),
    }


def _market_prefix(code: str) -> str:
    # 北交所新段 920xxx 必须先于 "9"→沪市 判断，否则资金流/新浪兜底全部取不到
    # （沪市 B 股为 900 段，不会命中 "92"，不受影响）。
    if code.startswith(("4", "8")) or code.startswith("92"):
        return "bj"
    if code.startswith(("6", "9", "5")):
        return "sh"
    return "sz"


def _tdx_board(code: str) -> dict | None:
    """Return exact industry/concepts from the remote TDX relation dataset."""
    relation = tdxapi.relations_for_codes([code]).get(code) or {}
    industry = relation.get("industry")
    industry_name = str(industry.get("name") or "").strip() if isinstance(industry, dict) else str(industry or "").strip()
    concepts = []
    for item in relation.get("concepts") or []:
        name = str(item.get("name") or "").strip() if isinstance(item, dict) else str(item or "").strip()
        if name and name not in concepts:
            concepts.append(name)
    if not industry_name and not concepts:
        return None
    return {"industry": industry_name, "concepts": concepts, "source": relation.get("source") or "TDX relation"}


@ttl_cache(15, fail_seconds=3)
def stock_risk_daily(code: str) -> list[dict]:
    """受治理问股的不可取消日 K 底座；不触发分时或增强采集。"""
    return list(tx.daily_bars(code.strip().zfill(6), 120) or [])[-120:]


@ttl_cache(60, fail_seconds=10)
def stock_board_material(code: str) -> dict | None:
    """按材料选择单独读取行业/概念，不联动资金流或财务。"""
    return _tdx_board(code.strip().zfill(6))


@ttl_cache(60, fail_seconds=10)
def stock_flow_material(code: str) -> dict | None:
    """按材料选择单独读取个股资金流，不联动板块或财务。"""
    value = tx.moneyflow(code.strip().zfill(6))
    return value if isinstance(value, dict) else None


@ttl_cache(300, fail_seconds=20)
def stock_financial_material(code: str) -> dict | None:
    """按材料选择单独读取基础财务，不联动板块或资金流。"""
    value = tdxapi.basic_financial(code.strip().zfill(6))
    return value if isinstance(value, dict) else None


def _quote_freshness() -> tuple[bool, str | None, str]:
    """判断此刻"现价"是不是真实时。A股集合竞价 09:15、连续交易至 15:00 收盘。
    返回 (stale, as_of_date, note)：
      交易日 09:15~15:00 → (False, 今日, "")  实时
      交易日收盘后        → (True, 今日, "非交易时段，为今日收盘价，非实时")
      盘前/非交易日        → (True, 最近交易日, "非交易时段，为最近交易日X收盘价，非实时")
    杜绝把上一次收盘价当实时价给交易员（BLOCKER，同资金追踪#6 隐日病；周末/盘前最易误判）。"""
    now = now_cn()
    hm = now.hour * 60 + now.minute
    today = now.strftime("%Y-%m-%d")
    if is_trade_day(now.date()) and (9 * 60 + 15) <= hm <= (15 * 60):
        return False, today, ""
    if is_trade_day(now.date()) and hm > 15 * 60:
        return True, today, f"当前非交易时段，显示为今日（{today}）收盘价，非实时"
    prev = prev_trade_day(now.date()) or ""
    return True, prev, f"当前非交易时段，显示为最近交易日（{prev}）收盘价，非实时"

def _sina_minute(code: str) -> tuple[list[dict], str]:
    """新浪 1 分钟线兜底：取最近一个交易日，均价=累计成交额/累计成交量。
    返回 (分时列表, 所属交易日 YYYY-MM-DD)。"""
    symbol = f"{_market_prefix(code)}{code}"
    rows = tx.minute_bars(symbol, "1")
    if not rows:
        return [], ""
    try:
        last_day = str(rows[-1].get("day") or "")[:10]
        today_rows = [row for row in rows if str(row.get("day") or "")[:10] == last_day]
        out, cum_amount, cum_volume = [], 0.0, 0.0
        for r in today_rows:
            price = to_float(r.get("close"))
            volume = to_float(r.get("volume")) or 0.0
            amount = to_float(r.get("amount")) or 0.0
            cum_amount += amount
            cum_volume += volume
            avg = round(cum_amount / cum_volume, 3) if cum_volume else price
            out.append(
                {"t": str(r.get("day", ""))[11:16], "price": price, "avg": avg, "volume": to_int(volume)}
            )
        return out, str(last_day)
    except Exception:
        return [], ""

def quick_quotes(codes: list[str]) -> dict[str, dict]:
    """持仓/自选批量报价（交互热路径）。

    TDX 的 HTTP 数据集可能正在由后台刷新。交互请求绝不等待刷新完成，而是立即
    读取可用快照，并同时用腾讯批量行情补齐名称/估值/市值。这样 TDX 卡顿不会再
    把所有股票的第一屏一起拖过浏览器超时。
    """
    normalized = list(dict.fromkeys(str(code or "").strip().split(".", 1)[0].zfill(6) for code in codes))
    normalized = [code for code in normalized if len(code) == 6 and code.isdigit()]
    if not normalized:
        return {}
    # TDX 与腾讯同时起跑，结果合并时仍以 TDX 有效字段为准。这不是“双倍等待”：
    # 盘后 TDX pending/链路异常时，腾讯结果可在 1.6s 墙钟内直接成为首屏报价；
    # 本地 TDX 不再进入这条交互链路。两个独立小线程池避免慢 TDX 占住腾讯的灾备名额。
    primary_future = _submit_quote_task(
        _TDX_QUOTE_POOL, _TDX_QUOTE_SLOTS,
        tdxapi.quotes, normalized, max_age=30, wait=0,
    )
    fallback_future = _submit_quote_task(
        _TENCENT_QUOTE_POOL, _TENCENT_QUOTE_SLOTS,
        tc.quotes, normalized,
    )
    # 腾讯通常在 0.3~0.8 秒返回；先给它 1.4 秒硬预算。成功后只再给
    # TDX API 150ms 的合并窗口，不能为了等待主源把已可展示的腾讯
    # 报价拖过 2 秒。若腾讯没有返回，TDX API 的总预算也只到 1.8 秒。
    # 这仍保持“TDX 有效字段优先”，只是主源慢时不再阻塞首屏。
    started = time.monotonic()

    def source_value(future, timeout: float = 0.0):
        if future is None:
            return {}
        try:
            value = future.result(timeout=max(0.0, timeout))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    # 腾讯一次批量请求成本很低；对全部代码请求，除价格灾备外，
    # 还负责补齐 TDX 基础快照没有的名称/PE/PB/量比/市值。
    fallback = source_value(fallback_future, 1.4)
    elapsed = time.monotonic() - started
    primary_wait = 0.15 if fallback else max(0.0, 1.8 - elapsed)
    primary = source_value(primary_future, primary_wait)
    for future in (primary_future, fallback_future):
        if future is not None and not future.done():
            future.cancel()
    # TDX 本身用 instruments_a 填名；腾讯响应自带名称。这里不为了
    # 一个可选名称触发慢的全市场名录备源。
    name_map: dict[str, str] = {}
    relay_rows: dict[str, dict] = {}
    still_missing = [
        code for code in normalized
        if code not in primary and code not in fallback
    ]
    if still_missing and _relay_api():
        spot = _spot_all()
        if spot is not None and not spot.empty and "代码" in spot.columns:
            relay_notes = relay_staleness_notes(spot)
            wanted = set(still_missing)
            for _, relay_row in spot.iterrows():
                relay_code = str(relay_row.get("代码") or "").strip().zfill(6)
                if relay_code not in wanted:
                    continue
                relay_rows[relay_code] = {
                    "name": str(relay_row.get("名称") or ""),
                    "price": to_float(relay_row.get("最新价")),
                    "change_pct": to_float(relay_row.get("涨跌幅")),
                    "turnover_rate": to_float(relay_row.get("换手率")),
                    "volume_ratio": to_float(relay_row.get("量比")),
                    "amount": to_float(relay_row.get("成交额")),
                    "source": "境内中继全市场快照",
                    "relay_notes": relay_notes,
                }
    off_hours_stale, as_of, off_hours_note = _quote_freshness()
    out: dict[str, dict] = {}
    for code in normalized:
        primary_row = primary.get(code) or {}
        fallback_row = fallback.get(code) or {}
        row = dict(fallback_row)
        # 主源真实存在的字段覆盖备用源；None/空深度不覆盖备用源的可用字段。
        for key, value in primary_row.items():
            # TDX 实时接口本身没有名称，冷名录未命中时会返回空串。
            # 空串不是有效主源证据，不得覆盖腾讯已返回的真实股票名称。
            if value is not None and value != [] and value != "":
                row[key] = value
        if not row:
            row = relay_rows.get(code) or {}
        if not row or row.get("price") is None:
            continue
        age = to_float(row.get("age_seconds"))
        feed_stale = age is not None and age > 30
        note = off_hours_note
        if feed_stale and not off_hours_stale:
            note = f"TDX报价数据龄{age:.1f}秒，超过30秒新鲜度门槛"
        relay_notes = row.get("relay_notes") if isinstance(row.get("relay_notes"), list) else []
        if relay_notes:
            note = "；".join(relay_notes)
        out[code] = {
            "code": code,
            "name": row.get("name") or name_map.get(code, ""),
            "price": row.get("price"),
            "change_pct": row.get("change_pct"),
            "open": row.get("open"),
            "prev_close": row.get("prev_close"),
            "turnover_rate": row.get("turnover_rate"),
            "volume_ratio": row.get("volume_ratio"),
            "pe": row.get("pe"),
            "pb": row.get("pb"),
            "total_mv": row.get("total_mv"),
            "float_mv": row.get("float_mv"),
            "amount": row.get("amount"),
            "volume": row.get("volume"),
            "high": row.get("high"),
            "low": row.get("low"),
            "average": row.get("average"),
            "speed": row.get("speed"),
            "inside": row.get("inside"),
            "outside": row.get("outside"),
            "buyp": row.get("buyp") or [],
            "buyv": row.get("buyv") or [],
            "sellp": row.get("sellp") or [],
            "sellv": row.get("sellv") or [],
            "age_seconds": age,
            "source": row.get("source") or "腾讯财经",
            "stale": bool(off_hours_stale or feed_stale or relay_notes),
            "as_of": as_of,
            "quote_note": note or None,
        }
    return out


def quick_quote(code: str) -> dict | None:
    """单票兼容入口；高频页面应优先调 ``quick_quotes`` 合并请求。"""
    code = code.strip().split(".", 1)[0].zfill(6)
    return quick_quotes([code]).get(code)


# 报价主备源各用独立小池，慢主源不得阻塞快备源。
_TDX_QUOTE_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="quote-tdx-api"
)
_TENCENT_QUOTE_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="quote-tencent"
)
_TDX_QUOTE_SLOTS = threading.BoundedSemaphore(2)
_TENCENT_QUOTE_SLOTS = threading.BoundedSemaphore(4)


def _submit_quote_task(pool, slots, fn, *args, **kwargs):
    """只在对应报价池确有空位时提交，禁止超时请求在无界队列里堆积。

    ``ThreadPoolExecutor`` 的内部队列没有上限。旧实现即使 HTTP 响应已超时，
    连续换股仍会把同一坏源排入后台队列；等源恢复后还会执行一批已经失去价值
    的陈旧请求。这里将可运行任务数严格限制为 worker 数，拥堵时立即跳过该源，
    由另一个独立行情源继续完成当前请求。
    """
    if not slots.acquire(blocking=False):
        return None
    try:
        future = pool.submit(fn, *args, **kwargs)
    except Exception:
        slots.release()
        raise
    future.add_done_callback(lambda _future: slots.release())
    return future
# 交互详情只跑确定性较高的免费快源。公开 F10、东财资金流、集合竞价等慢源
# 不能再占用 /stock 的首屏预算；它们由各自低频页面/后台任务按需获取。
_INTERACTIVE_DETAIL_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="stock-interactive"
)
_INTERACTIVE_DETAIL_SLOTS = threading.BoundedSemaphore(2)
_INTERACTIVE_DETAIL_BUDGET = 3.5
# 分段接口共用上面的 8 个既有 worker，不再增加线程池。这个门禁只限制
# 「已经超过 HTTP 交互预算、但底层网络请求尚未真正结束」的批次数。
# 一次页面正常会并行请求 core/charts/enrichment 三段，6 个批次可容纳
# 用户快速切换两只股，超出后直接返回 pending，不再继续堆僵尸请求。
_STOCK_SEGMENT_SLOTS = threading.BoundedSemaphore(6)
_STOCK_SEGMENT_SLOT_WAIT = 1.0


def _release_detail_slot_when_done(futures) -> None:
    """慢分段超过交互预算后可以继续在后台收尾，但不能提前归还并发槽。

    ``Future.cancel()`` 无法终止已运行的网络请求。若响应一返回就释放槽位，
    连续换股会不断堆积实际仍在运行的慢请求。只有本批任务全部真正结束才归还槽位。
    """
    items = tuple(futures)
    remaining = len(items)
    lock = threading.Lock()

    if remaining == 0:
        _INTERACTIVE_DETAIL_SLOTS.release()
        return

    def completed(_future) -> None:
        nonlocal remaining
        with lock:
            remaining -= 1
            if remaining == 0:
                _INTERACTIVE_DETAIL_SLOTS.release()

    for future in items:
        future.add_done_callback(completed)


def _run_segment_jobs(tasks: dict[str, object], budget: float) -> tuple[dict, set[str], bool]:
    """在既有交互线程池中并行执行一个分段的数据任务。

    返回 ``(results, pending_names, busy)``。墙钟预算到期后不会谎称任务已取消：
    已运行的 Future 会在后台自然收尾，并且只有它们全部真正结束后才归还并发槽。
    """
    names = set(tasks)
    # 两次快速换股可以共存；再有短突发时等上一批健康请求收尾，
    # 而不是立刻返回整段 pending。等待仍有硬上限，不增加既有 8 个 worker。
    if not _STOCK_SEGMENT_SLOTS.acquire(timeout=_STOCK_SEGMENT_SLOT_WAIT):
        return {}, names, True
    futures: dict[str, concurrent.futures.Future] = {}
    try:
        for name, task in tasks.items():
            fn, args = task
            futures[name] = _INTERACTIVE_DETAIL_POOL.submit(fn, *args)
    except Exception:
        _STOCK_SEGMENT_SLOTS.release()
        raise

    remaining = len(futures)
    release_lock = threading.Lock()

    def completed(_future) -> None:
        nonlocal remaining
        with release_lock:
            remaining -= 1
            if remaining == 0:
                _STOCK_SEGMENT_SLOTS.release()

    if not futures:
        _STOCK_SEGMENT_SLOTS.release()
        return {}, set(), False
    for future in futures.values():
        future.add_done_callback(completed)

    done, pending_futures = concurrent.futures.wait(
        futures.values(), timeout=max(0.05, float(budget))
    )
    for future in pending_futures:
        future.cancel()
    results: dict = {}
    pending: set[str] = set()
    for name, future in futures.items():
        if future not in done:
            pending.add(name)
            continue
        try:
            results[name] = future.result()
        except Exception:
            results[name] = None
    return results, pending, False


def _recognition_fields(code: str, quote: dict | None) -> dict:
    """统一股票识别语义：增强源缺失绝不等于股票不存在。"""
    identity = tx.cached_stock_identity(code)
    if identity.get("known"):
        if quote and not quote.get("name") and identity.get("name"):
            quote["name"] = identity["name"]
        return {
            "recognition": identity,
            "recognized": True,
            "error_code": None if quote else "quote_unavailable",
            **({} if quote else {"error": "股票已识别，但当前报价源暂未返回"}),
        }
    # 完整证券名录是身份真值。TDX 的 999999 等代码可能是指数别名，
    # 实时端点仍会返回指数价格；它不能因此被误认成 A 股个股。
    if identity.get("universe_complete"):
        return {
            "recognition": identity,
            "recognized": False,
            "error_code": "stock_not_found",
            "error": "该代码不在当前完整 A 股名录中",
        }
    if quote:
        return {
            "recognition": identity,
            "recognized": True,
            "error_code": None,
        }
    return {
        "recognition": identity,
        "recognized": None,
        "error_code": "quote_unavailable",
        "error": "当前报价源暂未返回，且股票名录不完整，未判定为不存在",
    }


@ttl_cache(8, fail_seconds=2)
def stock_core(code: str) -> dict:
    """个股首屏：快报价、基础指标和最近日 K，硬预算内可部分成功。"""
    normalized = code.strip().zfill(6)
    results, pending, busy = _run_segment_jobs(
        {
            "quote": (quick_quote, (normalized,)),
            # 多取一年用于 52 周高低，响应只返回最近 30 根。
            "daily": (tx.daily_bars, (normalized, 380)),
        },
        3.2,
    )
    quote = copy.deepcopy(results.get("quote")) if isinstance(results.get("quote"), dict) else None
    daily_all = results.get("daily") if isinstance(results.get("daily"), list) else []
    recent_daily = daily_all[-30:]
    if quote and daily_all:
        highs = [to_float(row.get("h")) for row in daily_all if to_float(row.get("h")) is not None]
        lows = [to_float(row.get("l")) for row in daily_all if to_float(row.get("l")) is not None]
        if highs:
            quote["high_52w"] = max(highs)
            quote["low_52w"] = min(lows)

    sections = {
        "quote": {"state": "pending" if "quote" in pending else ("complete" if quote else "missing")},
        "recent_daily": {"state": "pending" if "daily" in pending else ("complete" if recent_daily else "missing")},
    }
    missing = []
    if "quote" not in pending and not quote:
        missing.append("实时盘口")
    if "daily" not in pending and not recent_daily:
        missing.append("前复权日K")
    if quote:
        stale, as_of, note = _quote_freshness()
        quote["stale"] = stale
        quote["as_of"] = as_of
        if note:
            quote["quote_note"] = note
    ok = bool(quote or recent_daily)
    payload = {
        "ok": ok,
        "state": "complete" if quote and recent_daily else ("partial" if ok or pending else "missing"),
        "data_mode": "stock_core",
        "layer_contract": _layer_contract(
            "base",
            blocks=("quote", "recent_daily"),
            independent_from=("industry", "concepts", "fund_flow", "financial", "f10"),
        ),
        "ts": ts_str(),
        "quote": quote,
        "recent_daily": recent_daily,
        "sections": sections,
        "pending": sorted(pending),
        "busy": busy,
        "missing": missing,
    }
    recognition = _recognition_fields(normalized, quote)
    if recognition.get("recognized") is False:
        # 上游若把指数别名当作个股代码返回，不能把那份报价或日K继续
        # 暴露给个股页，更不能进入诊断和建议留痕。
        payload.update({
            "ok": False,
            "state": "not_found",
            "quote": None,
            "recent_daily": [],
            "sections": {
                "quote": {"state": "not_found"},
                "recent_daily": {"state": "not_found"},
            },
            "pending": [],
            "missing": [],
        })
    payload.update(recognition)
    return payload


@ttl_cache(15, fail_seconds=3)
def stock_charts(code: str) -> dict:
    """个股图表：分时与完整前复权日 K 互不连坐。"""
    normalized = code.strip().zfill(6)
    results, pending, busy = _run_segment_jobs(
        {
            "minute": (_interactive_minute, (normalized,)),
            "daily": (tx.daily_bars, (normalized, 380)),
        },
        3.5,
    )
    minute_result = results.get("minute") if isinstance(results.get("minute"), dict) else {}
    minute = minute_result.get("rows") if isinstance(minute_result.get("rows"), list) else []
    minute_date = minute_result.get("data_date") or None
    daily_all = results.get("daily") if isinstance(results.get("daily"), list) else []
    daily = daily_all[-120:]
    today = now_cn().strftime("%Y-%m-%d")
    missing = []
    if "minute" not in pending and not minute:
        missing.append("分时")
    if minute_date and minute_date != today:
        missing.append(f"分时为上一交易日({minute_date})数据")
    if "daily" not in pending and not daily:
        missing.append("前复权日K")
    ok = bool(minute or daily)
    return {
        "ok": ok,
        "state": "complete" if minute and daily else ("partial" if ok or pending else "missing"),
        "data_mode": "stock_charts",
        "layer_contract": _layer_contract(
            "charts",
            blocks=("minute", "daily"),
            independent_from=("industry", "concepts", "fund_flow", "financial", "f10"),
        ),
        "ts": ts_str(),
        "minute": minute,
        "minute_date": minute_date,
        "daily": daily,
        "sections": {
            "minute": {"state": "pending" if "minute" in pending else ("complete" if minute else "missing")},
            "daily": {"state": "pending" if "daily" in pending else ("complete" if daily else "missing")},
        },
        "pending": sorted(pending),
        "busy": busy,
        "missing": missing,
    }


@ttl_cache(60, fail_seconds=10)
def stock_enrichment(code: str) -> dict:
    """个股增强：行业概念、资金流和基础财务各自独立失败。

    公开 F10 已从热链删除；板块只使用远程 TDX relation。当前没有可验证的
    竞价专用源，所以竞价明确返回 unavailable，绝不用普通成交量伪造。
    """
    normalized = code.strip().zfill(6)
    results, pending, busy = _run_segment_jobs(
        {
            "board": (_tdx_board, (normalized,)),
            "flow": (tx.moneyflow, (normalized,)),
            "financial": (tdxapi.basic_financial, (normalized,)),
        },
        4.5,
    )
    board = results.get("board") if isinstance(results.get("board"), dict) else None
    flow = results.get("flow") if isinstance(results.get("flow"), dict) else None
    financial = results.get("financial") if isinstance(results.get("financial"), dict) else None
    flow_date = None
    if flow:
        raw_date = str(flow.get("trade_date") or "")
        flow_date = (
            f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            if len(raw_date) == 8 else (raw_date or None)
        )
    missing = []
    if "board" not in pending and not board:
        missing.append("行业/概念归属")
    if "flow" not in pending and not flow:
        missing.append("个股资金流")
    if "financial" not in pending and not financial:
        missing.append("基础财务")
    if flow_date and flow_date != now_cn().strftime("%Y-%m-%d"):
        missing.append(f"个股资金流为上一交易日({flow_date})数据")
    completed = sum(bool(value) for value in (board, flow, financial))
    return {
        "ok": bool(completed),
        "state": "complete" if completed == 3 else ("partial" if completed or pending else "missing"),
        "data_mode": "stock_enrichment",
        "layer_contract": _layer_contract(
            "enrichment",
            blocks=("industry", "concepts", "fund_flow", "financial"),
            independent_from=("quote", "minute", "daily"),
        ),
        "ts": ts_str(),
        "board": board or {"industry": "", "concepts": []},
        "flow": flow,
        "flow_date": flow_date,
        "financial": financial,
        "auction": None,
        "sections": {
            "board": {"state": "pending" if "board" in pending else ("complete" if board else "missing")},
            "flow": {"state": "pending" if "flow" in pending else ("complete" if flow else "missing")},
            "financial": {"state": "pending" if "financial" in pending else ("complete" if financial else "missing")},
            "auction": {"state": "unavailable", "reason": "authoritative_source_not_integrated"},
        },
        "pending": sorted(pending),
        "busy": busy,
        "missing": missing + ["集合竞价(暂无可验证专用源)"],
    }


def _interactive_minute(code: str) -> dict:
    rows, data_date = _sina_minute(code)
    return {"rows": rows, "data_date": data_date}


@ttl_cache(12, fail_seconds=3)
def _stock_detail_interactive_cached(code: str) -> dict:
    """返回可部分成功的交互快照；总预算耗尽时保留已经完成的分段。"""
    code = code.strip().zfill(6)
    now = now_cn()
    today = now.strftime("%Y-%m-%d")
    out: dict = {
        "ok": True,
        "state": "partial",
        "data_mode": "interactive_snapshot",
        "ts": ts_str(),
        "missing": [],
        "pending": [],
        # 这些是明确从首屏剥离的低频增强，不是一直旋转的“加载中”。
        "deferred": ["个股资金流", "板块/概念", "集合竞价"],
        "quote": None,
        "minute": [],
        "minute_date": None,
        "daily": [],
        "board": {"industry": "", "concepts": []},
        "flow": None,
        "flow_date": None,
        "auction": None,
        "sections": {
            "quote": {"state": "pending"},
            "minute": {"state": "pending"},
            "daily": {"state": "pending"},
            "flow": {"state": "deferred", "reason": "not_in_first_screen"},
            "board": {"state": "deferred", "reason": "not_in_first_screen"},
            "auction": {"state": "deferred", "reason": "not_in_first_screen"},
        },
    }

    # 高并发时仍先给快报价，不再排队等待一整套增强数据。
    if not _INTERACTIVE_DETAIL_SLOTS.acquire(blocking=False):
        quote = quick_quote(code)
        out["quote"] = quote
        out["sections"]["quote"] = {"state": "complete" if quote else "missing"}
        out["missing"] = [] if quote else ["实时盘口"]
        out["pending"].extend(["分时", "日K"])
        out["state"] = "partial" if quote else "missing"
        out["ok"] = bool(quote)
        return out

    slot_release_delegated = False
    try:
        jobs = {
            "quote": _INTERACTIVE_DETAIL_POOL.submit(quick_quote, code),
            "minute": _INTERACTIVE_DETAIL_POOL.submit(_interactive_minute, code),
            "daily": _INTERACTIVE_DETAIL_POOL.submit(tx.daily_bars, code, 380),
        }
        # 槽位跟随实际 Future 生命周期，不跟随 HTTP 响应生命周期。
        slot_release_delegated = True
        _release_detail_slot_when_done(jobs.values())
        done, pending = concurrent.futures.wait(
            jobs.values(), timeout=_INTERACTIVE_DETAIL_BUDGET
        )
        for future in pending:
            future.cancel()

        def value(name, default=None):
            future = jobs[name]
            if future not in done:
                return default
            try:
                return future.result()
            except Exception:
                return default

        quote = value("quote")
        minute_result = value("minute", {}) or {}
        minute = minute_result.get("rows") or []
        minute_date = minute_result.get("data_date") or None
        daily_all = value("daily", []) or []
        daily = daily_all[-120:]
        flow = None

        out["quote"] = quote
        out["minute"] = minute
        out["minute_date"] = minute_date
        out["daily"] = daily
        out["flow"] = flow
        out["flow_date"] = (
            f"{str(flow.get('trade_date'))[:4]}-{str(flow.get('trade_date'))[4:6]}-{str(flow.get('trade_date'))[6:8]}"
            if isinstance(flow, dict) and len(str(flow.get("trade_date") or "")) == 8
            else None
        )

        if quote and daily_all:
            highs = [to_float(row.get("h")) for row in daily_all if to_float(row.get("h")) is not None]
            lows = [to_float(row.get("l")) for row in daily_all if to_float(row.get("l")) is not None]
            if highs:
                quote["high_52w"] = max(highs)
                quote["low_52w"] = min(lows)

        completed = 0
        for name, payload in (("quote", quote), ("minute", minute), ("daily", daily)):
            if payload:
                out["sections"][name] = {"state": "complete"}
                completed += 1
            elif jobs[name] in pending:
                out["sections"][name] = {"state": "pending", "reason": "interaction_budget"}
                if name not in out["pending"]:
                    out["pending"].append({"quote": "实时盘口", "minute": "分时", "daily": "日K", "flow": "资金流"}[name])
            else:
                out["sections"][name] = {"state": "missing", "reason": "source_unavailable"}
                out["missing"].append({"quote": "实时盘口", "minute": "分时", "daily": "前复权日K", "flow": "个股资金流"}[name])

        if minute_date and minute_date != today:
            out["missing"].append(f"分时为上一交易日({minute_date})数据")
        if quote:
            stale, as_of, note = _quote_freshness()
            quote["stale"] = stale
            quote["as_of"] = as_of
            if note:
                quote["quote_note"] = note

        out["ok"] = bool(quote or minute or daily)
        out["state"] = "complete" if completed == 3 else ("partial" if out["ok"] else "missing")
        return out
    finally:
        if not slot_release_delegated:
            _INTERACTIVE_DETAIL_SLOTS.release()


def stock_detail(code: str) -> dict:
    """兼容基础快照；不再聚合行业、资金、F10 等增强层。"""
    normalized = code.strip().zfill(6)
    result = copy.deepcopy(_stock_detail_interactive_cached(normalized))
    result["layer_contract"] = _layer_contract(
        "legacy_base_compat",
        blocks=("quote", "minute", "daily"),
        independent_from=("industry", "concepts", "fund_flow", "financial", "f10"),
    )
    result["deprecation"] = {
        "deprecated": True,
        "replacement": [
            f"/api/stock/{normalized}/core",
            f"/api/stock/{normalized}/charts",
            f"/api/stock/{normalized}/enrichment",
        ],
        "note": "兼容接口只返回基础快照；新页面应并行请求分层端点",
    }
    identity = tx.cached_stock_identity(normalized)
    result["recognition"] = identity
    if result.get("quote"):
        if not result["quote"].get("name") and identity.get("name"):
            result["quote"]["name"] = identity["name"]
        result["recognized"] = True
        result["error_code"] = None
    elif identity.get("known"):
        result["recognized"] = True
        result["error_code"] = "quote_unavailable"
        result["error"] = "股票已识别，但当前报价源暂未返回"
    elif identity.get("universe_complete"):
        result["recognized"] = False
        result["error_code"] = "stock_not_found"
        result["error"] = "该代码不在当前完整 A 股名录中"
    else:
        result["recognized"] = None
        result["error_code"] = "quote_unavailable"
        result["error"] = "当前报价源暂未返回，且股票名录不完整，未判定为不存在"
    return result
