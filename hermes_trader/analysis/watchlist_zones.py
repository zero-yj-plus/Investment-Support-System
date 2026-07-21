"""观察池双周期纯数字价格区间（v2.0）。

只消费个股前复权日线与 5 分钟 OHLCV/成交额，不读取题材、情绪、估值或 AI 输出。
数据不足时返回明确 missing；不会用零值、猜测值或旧交易日超短区间补齐。
"""
from __future__ import annotations

import concurrent.futures
import copy
import math
import statistics
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from ..data.base import now_cn, to_float


SCHEMA_VERSION = "watchlist-dual-zone-v2"
ALGORITHM_VERSION = "2.0"
_REFRESH_WORKERS = 4


def _price(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _position(price: float | None, low: float | None, high: float | None) -> str | None:
    if price is None or low is None or high is None:
        return None
    if price > high:
        return "ABOVE_ZONE"
    if price < low:
        return "BELOW_ZONE"
    return "IN_ZONE"


def _wilder_atr(rows: list[dict], period: int = 14) -> float | None:
    true_ranges: list[float] = []
    previous_close: float | None = None
    for row in rows:
        high, low, close = (to_float(row.get(k)) for k in ("high", "low", "close"))
        if high is None or low is None or close is None or high < low:
            return None
        tr = high - low if previous_close is None else max(
            high - low, abs(high - previous_close), abs(low - previous_close)
        )
        true_ranges.append(tr)
        previous_close = close
    if len(true_ranges) < period:
        return None
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = ((period - 1) * atr + tr) / period
    return atr if math.isfinite(atr) and atr > 0 else None


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    value = values[0]
    for current in values[1:]:
        value = alpha * current + (1 - alpha) * value
    return value if math.isfinite(value) and value > 0 else None


def _vwap(rows: Iterable[dict]) -> float | None:
    amount_sum = 0.0
    volume_sum = 0.0
    count = 0
    for row in rows:
        amount = to_float(row.get("amount"))
        volume = to_float(row.get("volume"))
        if amount is None or amount < 0 or volume is None or volume <= 0:
            return None
        amount_sum += amount
        volume_sum += volume
        count += 1
    if count == 0 or volume_sum <= 0:
        return None
    value = amount_sum / volume_sum
    return value if math.isfinite(value) and value > 0 else None


def _confirmed_low(rows: list[dict]) -> float | None:
    latest: float | None = None
    for index in range(2, len(rows) - 2):
        window = [to_float(rows[offset].get("low")) for offset in range(index - 2, index + 3)]
        if any(value is None for value in window):
            continue
        center = window[2]
        if center == min(window):
            latest = center
    return latest


def _cluster_support(candidates: Iterable[float | None], radius: float, price: float) -> tuple[float | None, int]:
    values = sorted(float(value) for value in candidates if value is not None and math.isfinite(float(value)))
    if not values or radius < 0:
        return None, 0
    groups: list[list[float]] = [[values[0]]]
    for value in values[1:]:
        if value - groups[-1][-1] <= radius:
            groups[-1].append(value)
        else:
            groups.append([value])
    selected = min(
        groups,
        key=lambda group: (-len(group), abs(float(statistics.median(group)) - price)),
    )
    return float(statistics.median(selected)), len(selected)


def _daily_rows(raw_rows: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for raw in raw_rows:
        row = {
            "date": str(raw.get("d") or raw.get("date") or "")[:10],
            "open": to_float(raw.get("o") if "o" in raw else raw.get("open")),
            "high": to_float(raw.get("h") if "h" in raw else raw.get("high")),
            "low": to_float(raw.get("l") if "l" in raw else raw.get("low")),
            "close": to_float(raw.get("c") if "c" in raw else raw.get("close")),
            "volume": to_float(raw.get("v_shares") if "v_shares" in raw else raw.get("volume")),
            "amount": to_float(raw.get("amount")),
        }
        if len(row["date"]) == 10:
            normalized.append(row)
    normalized.sort(key=lambda item: item["date"])
    return normalized


def _minute_rows(raw_rows: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for raw in raw_rows:
        stamp = str(raw.get("day") or raw.get("datetime") or "")
        row = {
            "stamp": stamp,
            "date": stamp[:10],
            "open": to_float(raw.get("open")),
            "high": to_float(raw.get("high")),
            "low": to_float(raw.get("low")),
            "close": to_float(raw.get("close")),
            "volume": to_float(raw.get("volume")),
            "amount": to_float(raw.get("amount")),
        }
        if len(row["date"]) == 10:
            normalized.append(row)
    normalized.sort(key=lambda item: item["stamp"])
    return normalized


def _complete_rows(rows: list[dict]) -> bool:
    required = ("open", "high", "low", "close", "volume", "amount")
    return bool(rows) and all(
        all(row.get(key) is not None for key in required)
        and row["open"] > 0 and row["high"] > 0 and row["low"] > 0 and row["close"] > 0
        and row["volume"] > 0 and row["amount"] >= 0
        for row in rows
    )


def _missing_zone(label: str, reasons: list[str], data_date: str | None = None) -> dict:
    return {
        "state": "missing",
        "label": label,
        "low": None,
        "high": None,
        "position": None,
        "data_date": data_date,
        "missing": list(dict.fromkeys(reasons)),
    }


def calculate_dual_zones(
    code: str,
    daily: list[dict],
    minute_5m: list[dict],
    *,
    latest_price: float | None = None,
    calculated_at: datetime | None = None,
) -> dict:
    """按 v2.0 文档计算超短线与波段区间；输入和输出均可独立复算。"""
    current = calculated_at or now_cn()
    calculated_text = current.isoformat(timespec="seconds")
    all_daily_rows = _daily_rows(daily)
    current_date = current.date().isoformat()
    before_daily_settlement = current.hour * 60 + current.minute < 15 * 60 + 5
    if all_daily_rows and all_daily_rows[-1]["date"] == current_date and before_daily_settlement:
        # 当日未收盘的动态日 K 不能进入“每日收盘后重算”的日线指标。
        all_daily_rows = all_daily_rows[:-1]
    daily_rows = all_daily_rows[-60:]
    minute_rows = _minute_rows(minute_5m)
    minute_dates = sorted({row["date"] for row in minute_rows})
    selected_dates = minute_dates[-3:]
    minute_rows = [row for row in minute_rows if row["date"] in selected_dates]
    price = to_float(latest_price)
    if price is None and minute_rows:
        price = minute_rows[-1]["close"]
    if price is None and daily_rows:
        price = daily_rows[-1]["close"]

    result = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "code": str(code or "").strip().zfill(6),
        "calculated_at": calculated_text,
        "data_time": minute_rows[-1]["stamp"] if minute_rows else (daily_rows[-1]["date"] if daily_rows else None),
        "price": _price(price) if price is not None and price > 0 else None,
        "source": {
            "daily": "腾讯财经·前复权日K",
            "minute_5m": "新浪公开5分钟K",
            "price": "批量实时行情" if latest_price is not None else "最近K线收盘价",
        },
        "input_counts": {
            "daily": len(daily_rows),
            "minute_5m": len(minute_rows),
            "minute_trade_days": len(selected_dates),
        },
    }

    daily_reasons: list[str] = []
    if len(daily_rows) < 60:
        daily_reasons.append(f"日线不足60根（当前{len(daily_rows)}根）")
    if daily_rows and not _complete_rows(daily_rows):
        daily_reasons.append("日线OHLCV或成交额缺失")
    if price is None or price <= 0:
        daily_reasons.append("最新价格缺失")

    atr_daily = _wilder_atr(daily_rows) if not daily_reasons else None
    if not daily_reasons and atr_daily is None:
        daily_reasons.append("日线ATR14不可计算")

    ultra_reasons = list(daily_reasons)
    if len(selected_dates) < 3:
        ultra_reasons.append(f"5分钟线不足3个交易日（当前{len(selected_dates)}日）")
    if minute_rows and not _complete_rows(minute_rows):
        ultra_reasons.append("5分钟线OHLCV或成交额缺失")
    atr_5m = _wilder_atr(minute_rows) if not ultra_reasons else None
    if not ultra_reasons and atr_5m is None:
        ultra_reasons.append("5分钟ATR14不可计算")

    ultra_date = selected_dates[-1] if selected_dates else None
    ultra: dict | None = None
    if ultra_reasons:
        ultra = _missing_zone("超短线区间", ultra_reasons, ultra_date)
    else:
        today_rows = [row for row in minute_rows if row["date"] == ultra_date]
        vwap_today = _vwap(today_rows)
        vwap_3d = _vwap(minute_rows)
        structure_low = _confirmed_low(minute_rows)
        today_low = min(row["low"] for row in today_rows)
        candidates = [vwap_today, vwap_3d, structure_low, today_low]
        filtered = [value for value in candidates if price - atr_daily <= value <= price + 0.25 * atr_daily]
        support, members = _cluster_support(filtered, 0.25 * atr_daily, price)
        if members < 2:
            if vwap_today is None or structure_low is None:
                ultra = _missing_zone("超短线区间", ["超短线候选不足且回退支撑缺失"], ultra_date)
            else:
                support = float(statistics.median([vwap_today, structure_low]))
        if support is not None and ultra is None:
            width = max(1.20 * atr_5m, 0.12 * atr_daily)
            low, high = _price(support - width), _price(support + 0.50 * width)
            ultra = {
                "state": "complete", "label": "超短线区间", "low": low, "high": high,
                "position": _position(price, low, high), "data_date": ultra_date, "missing": [],
                "metrics": {
                    "support": _price(support), "width": _price(width),
                    "atr_daily": round(atr_daily, 6), "atr_5m": round(atr_5m, 6),
                    "cluster_members": members,
                },
            }

    swing_reasons = list(daily_reasons)
    swing_date = daily_rows[-1]["date"] if daily_rows else None
    swing_price = daily_rows[-1]["close"] if daily_rows and before_daily_settlement else price
    swing: dict | None = None
    if swing_reasons:
        swing = _missing_zone("波段区间", swing_reasons, swing_date)
    else:
        closes = [row["close"] for row in daily_rows]
        ema10, ema20 = _ema(closes, 10), _ema(closes, 20)
        vwap20, vwap60 = _vwap(daily_rows[-20:]), _vwap(daily_rows)
        low10 = min(row["low"] for row in daily_rows[-10:])
        structure_low_daily = _confirmed_low(daily_rows)
        previous_stage_high = max(row["high"] for row in daily_rows[-40:-20])
        candidates = [ema10, ema20, vwap20, vwap60, low10, structure_low_daily, previous_stage_high]
        filtered = [value for value in candidates if swing_price - 2 * atr_daily <= value <= swing_price + 0.50 * atr_daily]
        support, members = _cluster_support(filtered, 0.60 * atr_daily, swing_price)
        if members < 2:
            if vwap60 is None:
                swing = _missing_zone("波段区间", ["波段候选不足且近60日VWAP缺失"], swing_date)
            else:
                support = float(statistics.median([low10, vwap60]))
        if support is not None and swing is None:
            width = 0.35 * atr_daily
            low, high = _price(support - width), _price(support + 0.60 * width)
            swing = {
                "state": "complete", "label": "波段区间", "low": low, "high": high,
                "position": _position(price, low, high), "data_date": swing_date, "missing": [],
                "metrics": {
                    "support": _price(support), "width": _price(width),
                    "atr_daily": round(atr_daily, 6), "cluster_members": members,
                    "reference_price": _price(swing_price),
                },
            }

    result["ultra"] = ultra or _missing_zone("超短线区间", ["超短线区间不可计算"], ultra_date)
    result["swing"] = swing or _missing_zone("波段区间", ["波段区间不可计算"], swing_date)
    ultra, swing = result["ultra"], result["swing"]
    result["missing"] = list(dict.fromkeys((ultra.get("missing") or []) + (swing.get("missing") or [])))
    result["ok"] = ultra.get("state") == "complete" or swing.get("state") == "complete"
    return result


def _market_symbol(code: str) -> str:
    code = str(code or "").strip().zfill(6)
    if code.startswith(("4", "8", "92")):
        return "bj" + code
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


def calculate_for_code(code: str, quote: dict | None = None) -> dict:
    from ..data import free_source, stock  # noqa: PLC0415 - 纯算法导入不得初始化网络适配器

    code = str(code or "").strip().split(".", 1)[0].zfill(6)
    current_quote = quote if isinstance(quote, dict) else stock.quick_quote(code)
    # 盘中腾讯可能返回一根未收盘的当日日 K，多取一根以保证仍有 60 根完整日线。
    daily = free_source.daily_bars(code, 61)
    minute = free_source.minute_bars(_market_symbol(code), "5", 600)
    result = calculate_dual_zones(
        code, daily, minute,
        latest_price=(current_quote or {}).get("price"),
    )
    result["quote_date"] = str((current_quote or {}).get("as_of") or "")[:10] or None
    return result


def _retain_last_good(new: dict, old: dict | None) -> dict:
    if not isinstance(old, dict):
        return new
    merged = copy.deepcopy(new)
    for cycle in ("ultra", "swing"):
        fresh = merged.get(cycle) if isinstance(merged.get(cycle), dict) else {}
        previous = old.get(cycle) if isinstance(old.get(cycle), dict) else {}
        same_date = bool(
            fresh.get("data_date") and fresh.get("data_date") == previous.get("data_date")
        )
        if cycle == "swing" and fresh.get("data_date") is None:
            same_date = True
        if cycle == "ultra" and fresh.get("data_date") is None:
            same_date = bool(merged.get("quote_date") and merged.get("quote_date") == previous.get("data_date"))
        if fresh.get("state") != "complete" and previous.get("state") == "complete" and same_date:
            carried = copy.deepcopy(previous)
            carried["state"] = "degraded"
            carried["stale"] = True
            carried["missing"] = list(dict.fromkeys((fresh.get("missing") or []) + ["本次刷新失败，展示同交易日最近成功结果"]))
            merged[cycle] = carried
    merged["ok"] = any((merged.get(cycle) or {}).get("state") in {"complete", "degraded"} for cycle in ("ultra", "swing"))
    merged["missing"] = list(dict.fromkeys(
        item for cycle in ("ultra", "swing") for item in ((merged.get(cycle) or {}).get("missing") or [])
    ))
    return merged


def refresh_all(store) -> dict:
    from ..data import stock  # noqa: PLC0415 - 后台刷新时才装载行情适配器

    items = store.watchlist()
    codes = list(dict.fromkeys(str(item.get("code") or "").strip().zfill(6) for item in items if item.get("code")))
    existing = store.watchlist_zones()
    if not codes:
        store.save_watchlist_zones({})
        return {"ok": True, "count": 0, "complete": 0}
    quotes = stock.quick_quotes(codes)

    def calculate(code: str) -> tuple[str, dict]:
        try:
            return code, _retain_last_good(calculate_for_code(code, quotes.get(code)), existing.get(code))
        except Exception as exc:  # noqa: BLE001 - 单股失败不能阻断整仓刷新
            failed = {
                "schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION,
                "code": code, "calculated_at": now_cn().isoformat(timespec="seconds"),
                "ok": False, "ultra": _missing_zone("超短线区间", [f"计算异常：{type(exc).__name__}"]),
                "swing": _missing_zone("波段区间", [f"计算异常：{type(exc).__name__}"]),
                "missing": ["区间计算异常"],
            }
            return code, _retain_last_good(failed, existing.get(code))

    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=_REFRESH_WORKERS, thread_name_prefix="watch-zones") as pool:
        for code, result in pool.map(calculate, codes):
            results[code] = result
    store.save_watchlist_zones(results)
    return {
        "ok": True,
        "count": len(results),
        "complete": sum(1 for row in results.values() if row.get("ok")),
    }


def refresh_one(store, code: str) -> dict:
    normalized = str(code or "").strip().split(".", 1)[0].zfill(6)
    existing = store.watchlist_zones()
    result = _retain_last_good(calculate_for_code(normalized), existing.get(normalized))
    store.upsert_watchlist_zone(normalized, result)
    return result


def enrich_watch_item(item: dict, snapshot: dict | None, quote: dict | None = None) -> dict:
    """合并手工覆盖与自动超短区间；不修改持久化观察条目。"""
    out = dict(item)
    zones = copy.deepcopy(snapshot) if isinstance(snapshot, dict) else {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "ok": False,
        "ultra": _missing_zone("超短线区间", ["等待首次自动计算"]),
        "swing": _missing_zone("波段区间", ["等待首次自动计算"]),
        "missing": ["等待首次自动计算"],
    }
    current_price = to_float((quote or {}).get("price"))
    quote_date = str((quote or {}).get("as_of") or "")[:10]
    ultra = zones.get("ultra") if isinstance(zones.get("ultra"), dict) else _missing_zone("超短线区间", ["自动区间缺失"])
    if quote_date and ultra.get("data_date") and quote_date > str(ultra.get("data_date")):
        ultra = _missing_zone("超短线区间", ["新交易日尚未生成超短线区间"], quote_date)
        zones["ultra"] = ultra
    for cycle in ("ultra", "swing"):
        zone = zones.get(cycle)
        if isinstance(zone, dict):
            zone["position"] = _position(current_price, to_float(zone.get("low")), to_float(zone.get("high")))

    manual_low, manual_high = item.get("target_low"), item.get("target_high")
    manual_override = manual_low is not None or manual_high is not None
    auto_ready = ultra.get("state") in {"complete", "degraded"} and ultra.get("low") is not None and ultra.get("high") is not None
    effective_low = manual_low if manual_override else (ultra.get("low") if auto_ready else None)
    effective_high = manual_high if manual_override else (ultra.get("high") if auto_ready else None)
    out.update({
        "manual_target_low": manual_low,
        "manual_target_high": manual_high,
        "target_low": effective_low,
        "target_high": effective_high,
        "effective_target_low": effective_low,
        "effective_target_high": effective_high,
        "target_source": "manual_override" if manual_override else ("auto_ultra" if auto_ready else "missing"),
        "ideal_buy_zones": zones,
    })
    return out
