"""Deterministic holding-risk shadow engine.

The engine deliberately separates facts from actions.  Price structure decides
where the thesis is invalidated, ATR supplies the noise buffer, moving averages
describe trend health, and market/event overlays may only raise severity.  The
module never sends notifications; callers can inspect ``push_eligible`` and the
current shadow-mode flag after the rules have been validated out of sample.
"""

from __future__ import annotations

from datetime import date, datetime
import json
import math
from pathlib import Path
import threading
from typing import Any, Iterable

from ..data.base import now_cn


RULE_VERSION = "holding-risk-v1-shadow-r2"
RUNTIME_STATE_FILE = "holding_risk_states.json"
_RUNTIME_LOCK = threading.Lock()

STATE_RANK = {"gray": 0, "blue": 1, "yellow": 2, "orange": 3, "red": 4}
STATE_ACTION = {
    "gray": "数据不足，暂不判断",
    "blue": "继续持有",
    "yellow": "暂停加仓，等待确认",
    "orange": "建议减仓1/3",
    "red": "建议退出可卖持仓",
}

SIGNAL_LABELS = {
    "data": "数据不足",
    "hold": "继续持有",
    "warning": "风险观察",
    "customer_line": "客户风险线",
    "risk_reduce": "风险减仓",
    "risk_exit": "风险退出",
    "trend_reduce": "趋势减仓",
    "profit_take": "止盈兑现",
    "event_review": "事件复核",
    "event_exit": "重大事件",
    "time_risk": "时间风险",
}

SIGNAL_PRIORITY = {
    "data": 0,
    "hold": 1,
    "warning": 2,
    "event_review": 3,
    "time_risk": 4,
    "profit_take": 5,
    "trend_reduce": 6,
    "risk_reduce": 7,
    "risk_exit": 8,
    "event_exit": 9,
    "customer_line": 10,
}

HORIZON_LABELS = {
    "general": "通用",
    "overnight": "隔日",
    "ultra": "超短",
    "short": "短线",
    "swing": "波段",
    "trend": "趋势",
}

ENTRY_TYPE_LABELS = {
    "general": "未指定",
    "breakout": "突破",
    "pullback": "回踩",
    "event": "事件",
}

# V1 research baselines.  These are intentionally visible in the API so that
# backtests and customer snapshots can always identify the exact rule set.
HORIZON_PARAMS = {
    "general": {"buffer": .25, "initial_atr": 1.5, "trail_atr": 2.2, "trail_r": 1.0,
                "target1_r": 1.5, "target2_r": 2.5, "watch_days": None, "exit_days": None},
    "overnight": {"buffer": .15, "initial_atr": 1.0, "trail_atr": 1.5, "trail_r": .8,
                  "target1_r": 1.0, "target2_r": 1.5, "watch_days": 1, "exit_days": 1},
    "ultra": {"buffer": .20, "initial_atr": 1.5, "trail_atr": 2.0, "trail_r": 1.0,
              "target1_r": 1.2, "target2_r": 2.0, "watch_days": 1, "exit_days": 1},
    "short": {"buffer": .25, "initial_atr": 1.5, "trail_atr": 2.2, "trail_r": 1.0,
              "target1_r": 1.5, "target2_r": 2.5, "watch_days": 5, "exit_days": 8},
    "swing": {"buffer": .35, "initial_atr": 2.0, "trail_atr": 2.8, "trail_r": 1.5,
              "target1_r": 2.0, "target2_r": 3.0, "watch_days": 10, "exit_days": 20},
    "trend": {"buffer": .50, "initial_atr": 2.5, "trail_atr": 3.0, "trail_r": 2.0,
              "target1_r": 2.0, "target2_r": None, "watch_days": 20, "exit_days": None},
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _pick(row: dict, *keys: str) -> Any:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row.get(key)
    return None


def normalize_daily_bars(rows: Iterable[dict], *, clock: datetime | None = None) -> list[dict]:
    """Normalize qfq daily bars and remove an unfinished current-day candle."""
    values: list[dict] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        day = str(_pick(raw, "d", "date", "day") or "")[:10]
        opened = _number(_pick(raw, "o", "open"))
        high = _number(_pick(raw, "h", "high"))
        low = _number(_pick(raw, "l", "low"))
        close = _number(_pick(raw, "c", "close"))
        volume = _number(_pick(raw, "v_shares", "v", "volume"))
        if not day or None in (opened, high, low, close):
            continue
        values.append({
            "date": day, "open": opened, "high": high, "low": low,
            "close": close, "volume": volume,
        })
    values.sort(key=lambda row: row["date"])
    current = clock or now_cn()
    if values and values[-1]["date"] == current.date().isoformat():
        if (current.hour, current.minute) < (15, 0):
            values.pop()
    return values


def _sma(values: list[float], period: int, *, end: int | None = None) -> float | None:
    boundary = len(values) if end is None else end
    start = boundary - period
    if start < 0 or boundary > len(values):
        return None
    window = values[start:boundary]
    return sum(window) / period if len(window) == period else None


def _atr(bars: list[dict], period: int = 14, *, end: int | None = None) -> float | None:
    boundary = len(bars) if end is None else end
    if boundary < period + 1:
        return None
    ranges: list[float] = []
    for index in range(1, boundary):
        row, previous = bars[index], bars[index - 1]
        ranges.append(max(
            row["high"] - row["low"],
            abs(row["high"] - previous["close"]),
            abs(row["low"] - previous["close"]),
        ))
    if len(ranges) < period:
        return None
    value = sum(ranges[:period]) / period
    for true_range in ranges[period:]:
        value = ((period - 1) * value + true_range) / period
    return value if math.isfinite(value) and value > 0 else None


def _confirmed_pivots(bars: list[dict], *, span: int = 2) -> tuple[list[dict], list[dict]]:
    lows: list[dict] = []
    highs: list[dict] = []
    for index in range(span, len(bars) - span):
        row = bars[index]
        neighbours = bars[index - span:index] + bars[index + 1:index + span + 1]
        if all(row["low"] <= item["low"] for item in neighbours):
            lows.append({"date": row["date"], "price": row["low"], "basis": "确认局部低点"})
        if all(row["high"] >= item["high"] for item in neighbours):
            highs.append({"date": row["date"], "price": row["high"], "basis": "确认局部高点"})
    return lows, highs


def _trading_age(bars: list[dict], entry_date: str | None) -> int | None:
    if not entry_date:
        return None
    eligible = [row for row in bars if row["date"] >= entry_date]
    return max(0, len(eligible) - 1) if eligible else None


def _ratcheting_trail(
    bars: list[dict], *, entry_date: str | None, activation_price: float,
    atr_multiple: float,
) -> tuple[float | None, float | None]:
    if not entry_date:
        return None, None
    start = next((index for index, row in enumerate(bars) if row["date"] >= entry_date), None)
    if start is None:
        return None, None
    high_since: float | None = None
    trail: float | None = None
    for index in range(start, len(bars)):
        high_since = max(high_since or bars[index]["high"], bars[index]["high"])
        if high_since < activation_price:
            continue
        atr_value = _atr(bars, 14, end=index + 1)
        if atr_value is None:
            continue
        candidate = high_since - atr_multiple * atr_value
        trail = candidate if trail is None else max(trail, candidate)
    return trail, high_since


def _runtime_fingerprint(holding: dict, horizon: str, entry_type: str) -> str:
    """Identity of one monitored position; changing its thesis resets its line."""
    cost = _number(holding.get("cost"))
    return "|".join((
        RULE_VERSION,
        str(holding.get("code") or "").split(".", 1)[0].zfill(6),
        f"{cost:.6f}" if cost is not None else "",
        str(holding.get("entry_date") or "")[:10],
        horizon,
        entry_type,
    ))


def load_risk_states(data_dir: str | Path) -> dict[str, dict]:
    """Load persisted per-position risk lines. Invalid rows fail closed."""
    path = Path(data_dir) / RUNTIME_STATE_FILE
    with _RUNTIME_LOCK:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
    rows = payload.get("positions") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        return {}
    return {str(code).zfill(6): dict(value) for code, value in rows.items() if isinstance(value, dict)}


def save_risk_states(data_dir: str | Path, states: dict[str, dict]) -> None:
    """Atomically persist the latest ratcheted line for active holdings only."""
    path = Path(data_dir) / RUNTIME_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "rule_version": RULE_VERSION,
        "updated_at": now_cn().isoformat(timespec="seconds"),
        "positions": {
            str(code).zfill(6): value
            for code, value in states.items()
            if isinstance(value, dict)
        },
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    with _RUNTIME_LOCK:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def _entry_boundary(bars: list[dict], entry_date: str | None) -> int:
    if not entry_date:
        return len(bars)
    matches = [index for index, row in enumerate(bars) if row["date"] <= entry_date]
    return matches[-1] + 1 if matches else len(bars)


def _initial_invalidation(
    bars: list[dict], *, cost: float, entry_date: str | None,
    entry_type: str, params: dict,
) -> tuple[float, str, float]:
    """Choose one thesis-aware initial line; ATR is a fallback, not a looser rival."""
    boundary = max(15, _entry_boundary(bars, entry_date))
    boundary = min(boundary, len(bars))
    history = bars[:boundary]
    entry_atr = _atr(history) or _atr(bars)
    if entry_atr is None or entry_atr <= 0:
        raise ValueError("ATR unavailable")
    volatility_stop = cost - float(params["initial_atr"]) * entry_atr
    buffer = float(params["buffer"]) * entry_atr
    candidate: float | None = None
    basis = "ATR备用风险线"
    pivot_lows, _pivot_highs = _confirmed_pivots(history[-80:])

    if entry_type == "breakout" and len(history) >= 21:
        breakout_level = max(row["high"] for row in history[-21:-1])
        candidate = breakout_level - buffer
        basis = "突破关键位下方"
    elif entry_type == "pullback":
        lows = [row for row in pivot_lows[-8:] if row["price"] < cost]
        if lows:
            candidate = lows[-1]["price"] - buffer
            basis = "回踩低点下方"
    elif entry_type == "event" and len(history) >= 10:
        candidate = min(row["low"] for row in history[-10:]) - buffer
        basis = "事件前平台下沿"
    else:
        supports: list[tuple[float, str]] = [
            (row["price"], "确认局部低点") for row in pivot_lows[-8:] if row["price"] < cost
        ]
        closes = [row["close"] for row in history]
        for period, label in ((20, "MA20支撑"), (60, "MA60支撑")):
            value = _sma(closes, period)
            if value is not None and value < cost:
                supports.append((value, label))
        if supports:
            support, label = max(supports, key=lambda item: item[0])
            candidate = support - buffer
            basis = f"{label}下方"

    # A structure line inside normal noise or implausibly far away is not usable.
    if candidate is not None:
        distance = cost - candidate
        if candidate <= 0 or distance < .45 * entry_atr or distance > 3.5 * entry_atr:
            candidate = None
    if candidate is None or candidate >= cost:
        candidate = volatility_stop
        basis = "ATR备用风险线"
    return candidate, basis, entry_atr


def load_cached_event_risk(data_dir: str | Path, code: str, *, today: date | None = None) -> dict:
    """Read only an existing event ledger; holdings refresh never triggers slow news fetches."""
    path = Path(data_dir) / "stock_ask_events" / f"{str(code).zfill(6)}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"state": "missing", "major_negative": False, "negative": False, "title": None}
    current = (today or now_cn().date()).isoformat()
    active = [
        row for row in payload.get("events", [])
        if isinstance(row, dict) and str(row.get("expiry_at") or "9999-12-31")[:10] >= current
    ]
    severe = next((row for row in active if row.get("major_negative") is True), None)
    negative = next((row for row in active if row.get("category") == "negative"), None)
    selected = severe or negative
    return {
        "state": str(payload.get("state") or "complete"),
        "major_negative": severe is not None,
        "negative": negative is not None,
        "title": str((selected or {}).get("title") or "")[:120] or None,
    }


def evaluate_holding(
    holding: dict,
    quote: dict | None,
    daily_rows: Iterable[dict],
    *,
    market_context: dict | None = None,
    event_risk: dict | None = None,
    previous_runtime: dict | None = None,
    clock: datetime | None = None,
) -> dict:
    """Evaluate one long A-share/ETF holding using only point-in-time inputs."""
    current = clock or now_cn()
    code = str(holding.get("code") or "").split(".", 1)[0].zfill(6)
    horizon = str(holding.get("horizon") or "general")
    if horizon not in HORIZON_PARAMS:
        horizon = "general"
    entry_type = str(holding.get("entry_type") or "general")
    if entry_type not in ENTRY_TYPE_LABELS:
        entry_type = "general"
    params = HORIZON_PARAMS[horizon]
    bars = normalize_daily_bars(daily_rows, clock=current)
    quote = quote if isinstance(quote, dict) else {}
    quote_price = _number(quote.get("price"))
    quote_fresh = quote_price is not None and quote.get("stale") is not True
    close_price = bars[-1]["close"] if bars else None
    reference_price = quote_price if quote_fresh else close_price
    cost = _number(holding.get("cost"))
    manual_stop = _number(holding.get("stop_loss_price"))
    manual_target = _number(holding.get("take_profit_price"))
    result = {
        "code": code,
        "name": str(holding.get("name") or quote.get("name") or ""),
        "rule_version": RULE_VERSION,
        "shadow_mode": True,
        "push_eligible": False,
        "state": "gray",
        "action": STATE_ACTION["gray"],
        "signal_type": "data",
        "signal_type_label": SIGNAL_LABELS["data"],
        "confidence": "low",
        "summary": "历史数据不足，暂不生成算法动作",
        "reasons": [],
        "horizon": horizon,
        "horizon_label": HORIZON_LABELS[horizon],
        "entry_type": entry_type,
        "entry_type_label": ENTRY_TYPE_LABELS[entry_type],
        "as_of": bars[-1]["date"] if bars else quote.get("as_of"),
        "price_basis": "实时价" if quote_fresh else ("最近完成日线收盘" if close_price is not None else "缺失"),
        "metrics": {},
        "levels": {"manual_stop": manual_stop, "manual_target": manual_target},
        "coverage": 0.0,
        "missing": [],
        "parameters": dict(params),
        "runtime_state": None,
    }
    if reference_price is None or cost is None or cost <= 0 or len(bars) < 60:
        if reference_price is None:
            result["missing"].append("可用价格")
        if cost is None or cost <= 0:
            result["missing"].append("持仓成本")
        if len(bars) < 60:
            result["missing"].append("至少60根前复权日K")
        return result

    closes = [row["close"] for row in bars]
    volumes = [row["volume"] for row in bars]
    ma5, ma20, ma60 = _sma(closes, 5), _sma(closes, 20), _sma(closes, 60)
    previous_ma5 = _sma(closes, 5, end=len(closes) - 1)
    previous_ma20 = _sma(closes, 20, end=len(closes) - 1)
    previous_ma60 = _sma(closes, 60, end=len(closes) - 1)
    atr14 = _atr(bars)
    if None in (ma5, ma20, ma60, atr14) or not atr14 or atr14 <= 0:
        result["missing"].append("MA5/MA20/MA60/ATR14")
        return result

    pivot_lows, pivot_highs = _confirmed_pivots(bars[-80:])
    support_candidates = [
        item for item in pivot_lows[-8:] if item["price"] <= reference_price
    ]
    for label, value in (("MA20", ma20), ("MA60", ma60)):
        if value <= reference_price:
            support_candidates.append({"date": bars[-1]["date"], "price": value, "basis": label})
    support_item = max(support_candidates, key=lambda item: item["price"], default=None)

    resistance_candidates = [
        item for item in pivot_highs[-10:] if item["price"] > reference_price
    ]
    high20 = max(row["high"] for row in bars[-20:])
    high60 = max(row["high"] for row in bars[-60:])
    for label, value in (("20日高点", high20), ("60日高点", high60)):
        if value > reference_price:
            resistance_candidates.append({"date": bars[-1]["date"], "price": value, "basis": label})
    resistance_item = min(resistance_candidates, key=lambda item: item["price"], default=None)

    entry_date = str(holding.get("entry_date") or "")[:10] or None
    fingerprint = _runtime_fingerprint(holding, horizon, entry_type)
    runtime = previous_runtime if isinstance(previous_runtime, dict) else {}
    if runtime.get("fingerprint") != fingerprint:
        runtime = {}
    monitor_since = str(runtime.get("monitor_since") or entry_date or bars[-1]["date"])[:10]
    computed_initial, computed_basis, _entry_atr = _initial_invalidation(
        bars,
        cost=cost,
        entry_date=entry_date,
        entry_type=entry_type,
        params=params,
    )
    stored_initial = _number(runtime.get("initial_stop"))
    initial_stop = stored_initial if stored_initial is not None and stored_initial < cost else computed_initial
    initial_basis = str(runtime.get("initial_basis") or computed_basis)
    risk_unit = cost - initial_stop
    if risk_unit <= 0:
        initial_stop = cost - float(params["initial_atr"]) * atr14
        initial_basis = "ATR备用风险线"
        risk_unit = cost - initial_stop

    tracking_start = entry_date or monitor_since
    new_structure_lows = [
        item for item in pivot_lows
        if item["date"] > tracking_start and item["price"] < close_price
    ]
    structure_trail_item = new_structure_lows[-1] if new_structure_lows else None
    current_structure_stop = (
        structure_trail_item["price"] - float(params["buffer"]) * atr14
        if structure_trail_item else None
    )
    activation_price = cost + float(params["trail_r"]) * risk_unit
    reconstructed_trail, reconstructed_high = _ratcheting_trail(
        bars, entry_date=tracking_start, activation_price=activation_price,
        atr_multiple=float(params["trail_atr"]),
    )
    stored_high = _number(runtime.get("highest_price"))
    high_since_entry = max(
        value for value in (stored_high, reconstructed_high, bars[-1]["high"]) if value is not None
    )
    live_chandelier = (
        high_since_entry - float(params["trail_atr"]) * atr14
        if high_since_entry >= activation_price else None
    )
    stored_trail = _number(runtime.get("trailing_stop"))
    trailing_values = [
        value for value in (stored_trail, reconstructed_trail, live_chandelier)
        if value is not None
    ]
    trailing_stop = max(trailing_values) if trailing_values else None
    stored_algorithm_stop = _number(runtime.get("algorithm_stop"))
    stop_candidates = [initial_stop]
    if current_structure_stop is not None and current_structure_stop < reference_price:
        stop_candidates.append(current_structure_stop)
    if trailing_stop is not None:
        stop_candidates.append(trailing_stop)
    if stored_algorithm_stop is not None:
        stop_candidates.append(stored_algorithm_stop)
    algorithm_stop = max(stop_candidates)
    target1 = cost + float(params["target1_r"]) * risk_unit
    target2_r = params.get("target2_r")
    target2 = cost + float(target2_r) * risk_unit if target2_r is not None else None
    resistance = resistance_item["price"] if resistance_item else None
    resistance_zone = (
        {"lower": max(reference_price, resistance - .25 * atr14), "upper": resistance + .25 * atr14,
         "basis": resistance_item["basis"]}
        if resistance is not None else None
    )
    result["runtime_state"] = {
        "fingerprint": fingerprint,
        "monitor_since": monitor_since,
        "initial_stop": round(initial_stop, 6),
        "initial_basis": initial_basis,
        "algorithm_stop": round(algorithm_stop, 6),
        "trailing_stop": round(trailing_stop, 6) if trailing_stop is not None else None,
        "highest_price": round(high_since_entry, 6),
        "updated_at": current.isoformat(timespec="seconds"),
        "rule_version": RULE_VERSION,
    }

    last = bars[-1]
    previous_close = bars[-2]["close"]
    last_return = (last["close"] / previous_close - 1) * 100 if previous_close else None
    prior_volumes = [value for value in volumes[-21:-1] if value is not None]
    volume_ratio = (
        last["volume"] / (sum(prior_volumes) / len(prior_volumes))
        if last["volume"] is not None and prior_volumes and sum(prior_volumes) > 0 else None
    )
    full_range = last["high"] - last["low"]
    upper_shadow_ratio = (
        (last["high"] - max(last["open"], last["close"])) / full_range
        if full_range > 0 else 0.0
    )
    close_in_lower_half = full_range > 0 and last["close"] <= last["low"] + full_range * .5
    ma5_slope = (ma5 / previous_ma5 - 1) * 100 if previous_ma5 else None
    ma20_slope = (ma20 / previous_ma20 - 1) * 100 if previous_ma20 else None
    ma60_slope = (ma60 / previous_ma60 - 1) * 100 if previous_ma60 else None
    previous_below_ma5 = previous_ma5 is not None and previous_close < previous_ma5
    ma5_break = reference_price < ma5
    ma5_confirmed = last["close"] < ma5 and previous_below_ma5 and (ma5_slope or 0) < 0
    ma20_confirmed = last["close"] < ma20 and previous_ma20 is not None and previous_close < previous_ma20
    ma60_confirmed = last["close"] < ma60 and previous_ma60 is not None and previous_close < previous_ma60 and (ma60_slope or 0) < 0

    market_context = market_context if isinstance(market_context, dict) else {}
    sentiment = _number(market_context.get("sentiment"))
    relative_period = 60 if horizon == "trend" else (20 if horizon == "swing" else 5)
    stock_period_return = (
        (closes[-1] / closes[-relative_period - 1] - 1) * 100
        if len(closes) > relative_period and closes[-relative_period - 1] else None
    )
    market_period_return = _number(market_context.get(f"market_return_{relative_period}d"))
    relative_strength = (
        stock_period_return - market_period_return
        if stock_period_return is not None and market_period_return is not None else None
    )
    regime = str(market_context.get("regime") or "")
    market_weak = bool(
        (sentiment is not None and sentiment < 42)
        or any(word in regime for word in ("弱", "退潮", "冰点", "防守", "高风险"))
    )
    down_volume = bool(last_return is not None and last_return < 0 and volume_ratio is not None and volume_ratio >= 1.2)

    event_risk = event_risk if isinstance(event_risk, dict) else {}
    age = _trading_age(bars, entry_date)
    high_after_entry = high_since_entry
    progress_r = (
        (high_after_entry - cost) / risk_unit
        if high_after_entry is not None and risk_unit > 0 else None
    )

    result["coverage"] = 1.0
    result["metrics"] = {
        "reference_price": round(reference_price, 4),
        "cost": round(cost, 4),
        "ma5": round(ma5, 4), "ma20": round(ma20, 4), "ma60": round(ma60, 4),
        "ma5_slope_pct": round(ma5_slope, 4) if ma5_slope is not None else None,
        "ma20_slope_pct": round(ma20_slope, 4) if ma20_slope is not None else None,
        "ma60_slope_pct": round(ma60_slope, 4) if ma60_slope is not None else None,
        "atr14": round(atr14, 4),
        "last_return_pct": round(last_return, 4) if last_return is not None else None,
        "volume_vs_20": round(volume_ratio, 4) if volume_ratio is not None else None,
        "upper_shadow_ratio": round(upper_shadow_ratio, 4),
        "relative_period_days": relative_period,
        "stock_period_return_pct": round(stock_period_return, 4) if stock_period_return is not None else None,
        "market_period_return_pct": round(market_period_return, 4) if market_period_return is not None else None,
        "relative_to_market_pct": round(relative_strength, 4) if relative_strength is not None else None,
        "sentiment": sentiment,
        "market_regime": regime or None,
        "holding_trading_days": age,
        "progress_r": round(progress_r, 4) if progress_r is not None else None,
    }
    result["levels"].update({
        "support": round(support_item["price"], 4) if support_item else None,
        "support_basis": support_item["basis"] if support_item else None,
        "resistance": round(resistance, 4) if resistance is not None else None,
        "resistance_basis": resistance_item["basis"] if resistance_item else None,
        "initial_stop": round(initial_stop, 4),
        "initial_stop_basis": initial_basis,
        "current_structure_stop": round(current_structure_stop, 4) if current_structure_stop is not None else None,
        "current_structure_basis": structure_trail_item["basis"] if structure_trail_item else None,
        "trailing_stop": round(trailing_stop, 4) if trailing_stop is not None else None,
        "algorithm_stop": round(algorithm_stop, 4),
        "target1": round(target1, 4),
        "target2": round(target2, 4) if target2 is not None else None,
        "resistance_zone": (
            {**resistance_zone,
             "lower": round(resistance_zone["lower"], 4),
             "upper": round(resistance_zone["upper"], 4)}
            if resistance_zone else None
        ),
        "risk_unit": round(risk_unit, 4),
    })

    state = "blue"
    signal_type = "hold"
    action = STATE_ACTION["blue"]
    confidence = "medium"
    reasons: list[str] = []
    primary_reason = ""
    summary = "趋势与结构尚未触发风险动作"

    def raise_to(
        new_state: str,
        reason: str,
        text: str,
        *,
        kind: str = "warning",
        instruction: str | None = None,
        certainty: str = "medium",
    ) -> None:
        nonlocal state, summary, signal_type, action, confidence, primary_reason
        reasons.append(reason)
        more_severe = STATE_RANK[new_state] > STATE_RANK[state]
        same_but_clearer = (
            new_state == state
            and SIGNAL_PRIORITY.get(kind, 0) > SIGNAL_PRIORITY.get(signal_type, 0)
        )
        if more_severe or same_but_clearer:
            state = new_state
            summary = text
            signal_type = kind
            action = instruction or STATE_ACTION[new_state]
            confidence = certainty
            primary_reason = reason

    live_price = quote_price if quote_fresh else None
    manual_reference = live_price if live_price is not None else close_price
    if manual_stop is not None and manual_reference is not None and manual_reference <= manual_stop:
        raise_to(
            "red", f"已触及客户风险线 {manual_stop:.2f}", "客户预设风险线已经触发",
            kind="customer_line", instruction="退出当前可卖持仓", certainty="high",
        )
    if event_risk.get("major_negative") is True:
        raise_to(
            "red", f"重大事件：{event_risk.get('title') or '重大负面公告'}", "重大事件风险优先于普通技术指标",
            kind="event_exit", instruction="退出当前可卖持仓并复核公告", certainty="high",
        )

    confirmed_break = close_price is not None and close_price <= algorithm_stop
    live_break = live_price is not None and live_price <= algorithm_stop
    extreme_break = live_price is not None and live_price <= algorithm_stop - .5 * atr14
    if confirmed_break or extreme_break:
        raise_to(
            "red", f"有效跌破算法风险线 {algorithm_stop:.2f}", "价格结构已经确认失效",
            kind="risk_exit", instruction="退出当前可卖持仓", certainty="high",
        )
    elif live_break:
        raise_to(
            "orange", f"盘中跌破算法风险线 {algorithm_stop:.2f}，尚待收盘确认", "风险线盘中失守",
            kind="risk_reduce", instruction="风险减仓1/3", certainty="medium",
        )
    elif reference_price - algorithm_stop <= .5 * atr14:
        raise_to("yellow", f"距离算法风险线不足 0.5×ATR", "价格接近风险线", kind="warning", instruction="暂停加仓，继续观察")

    if ma5_break:
        raise_to("yellow", f"价格低于MA5 {ma5:.2f}", "短期趋势转弱", kind="warning", instruction="暂停加仓，继续观察")
    if horizon in {"overnight", "ultra", "short", "general"}:
        if ma5_confirmed and (market_weak or down_volume or (relative_strength is not None and relative_strength < -1)):
            raise_to(
                "orange", "连续位于MA5下方，且量价/市场环境至少一项转弱", "短期趋势确认转弱",
                kind="trend_reduce", instruction="风险减仓1/3",
            )
    elif horizon == "swing" and ma20_confirmed and (market_weak or (relative_strength is not None and relative_strength < -1)):
        raise_to(
            "orange", "波段结构跌破MA20，且市场或相对强弱转差", "波段趋势转弱",
            kind="trend_reduce", instruction="风险减仓1/3",
        )
    elif horizon == "trend" and ma60_confirmed and (market_weak or (relative_strength is not None and relative_strength < -2)):
        raise_to(
            "orange", "趋势结构连续位于MA60下方，且市场或60日相对强弱转差", "中期趋势确认转弱",
            kind="trend_reduce", instruction="风险减仓1/3",
        )

    target_reference = live_price if live_price is not None else close_price
    if manual_target is not None and target_reference is not None and target_reference >= manual_target:
        raise_to(
            "orange", f"已达到客户目标价 {manual_target:.2f}", "达到客户目标价",
            kind="profit_take", instruction="止盈兑现1/3", certainty="high",
        )

    pressure_reached = last["high"] >= target1 or (
        resistance_zone is not None and last["high"] >= resistance_zone["lower"]
    )
    stall_reasons: list[str] = []
    if pressure_reached:
        if resistance_zone is not None and last["high"] >= resistance_zone["lower"] and last["close"] < resistance_zone["lower"]:
            stall_reasons.append("触及压力区后收回区间下方")
        if volume_ratio is not None and volume_ratio >= 1.5 and (last_return or 0) < .8:
            stall_reasons.append("放量但涨幅不足0.8%")
        if upper_shadow_ratio >= .4 and close_in_lower_half:
            stall_reasons.append("长上影且收在振幅下半区")
        if len(bars) >= 8 and max(row["high"] for row in bars[-2:]) <= max(row["high"] for row in bars[-7:-2]):
            stall_reasons.append("连续两日未创新高")
        if relative_strength is not None and relative_strength < -1:
            stall_reasons.append("同期弱于大盘1%以上")
        required = 3 if horizon == "trend" else 2
        if len(stall_reasons) >= required:
            raise_to(
                "orange", "；".join(stall_reasons[:3]), "压力区出现滞涨",
                kind="profit_take", instruction="止盈兑现1/3",
            )
        elif state == "blue":
            raise_to("yellow", "已进入第一收益观察区，尚未形成滞涨确认", "进入收益观察区", kind="warning", instruction="继续持有，观察突破")
    if target2 is not None and target_reference is not None and target_reference >= target2:
        raise_to(
            "orange", f"已进入主要止盈区 {target2:.2f} 附近", "进入主要止盈区",
            kind="profit_take", instruction="止盈兑现1/3",
        )

    watch_days, exit_days = params.get("watch_days"), params.get("exit_days")
    no_progress = progress_r is not None and progress_r < .5
    if age is not None and no_progress and watch_days is not None and age >= int(watch_days):
        raise_to("yellow", f"持仓{age}个交易日，最大进展不足0.5R", "预期行情尚未兑现", kind="time_risk", instruction="暂停加仓，复核买入逻辑")
    if age is not None and no_progress and exit_days is not None and age >= int(exit_days) and (ma5_confirmed or market_weak):
        raise_to(
            "orange", "时间条件到期且趋势未改善", "买入逻辑未在有效期内兑现",
            kind="time_risk", instruction="风险减仓1/3",
        )

    if event_risk.get("negative") is True and state not in {"red"}:
        raise_to(
            "yellow", f"近期负面事件：{event_risk.get('title') or '待复核'}", "事件风险上升",
            kind="event_review", instruction="暂停加仓，复核事件",
        )

    result["state"] = state
    result["action"] = action
    result["signal_type"] = signal_type
    result["signal_type_label"] = SIGNAL_LABELS.get(signal_type, "风险提示")
    result["confidence"] = confidence
    result["summary"] = summary
    ordered_reasons = ([primary_reason] if primary_reason else []) + reasons
    result["reasons"] = list(dict.fromkeys(reason for reason in ordered_reasons if reason))[:6]
    if isinstance(result.get("runtime_state"), dict):
        result["runtime_state"].update({
            "last_signal_state": state,
            "last_signal_type": signal_type,
            "last_signal_at": current.isoformat(timespec="seconds"),
        })
    if not entry_date:
        result["missing"].append("买入日期：动态吊灯和时间止损未启用")
    if horizon == "ultra":
        result["missing"].append("超短15分钟确认尚未接入，本轮仅展示日线保护层")
    return result


def backtest_risk_events(
    holding: dict,
    daily_rows: Iterable[dict],
    *,
    warmup: int = 80,
    forward_days: int = 5,
    benchmark_rows: Iterable[dict] | None = None,
) -> dict:
    """Validate fixed, independent holding cohorts without changing their entry.

    Each cohort has one immutable cost/date and its own ratcheting runtime state.
    This remains a signal follow-through diagnostic, not a portfolio P&L claim.
    """
    bars = normalize_daily_bars(daily_rows, clock=datetime(2999, 1, 1, 16, 0))
    benchmark = normalize_daily_bars(benchmark_rows or [], clock=datetime(2999, 1, 1, 16, 0))
    events: list[dict] = []
    if len(bars) < warmup + forward_days + 1:
        return {"ok": False, "events": [], "reason": "历史样本不足"}
    base = dict(holding)
    base.setdefault("horizon", "short")
    horizon = str(base.get("horizon") or "short")
    holding_windows = {
        "overnight": 2, "ultra": 5, "short": 20,
        "swing": 60, "trend": 120, "general": 40,
    }
    window = holding_windows.get(horizon, 40)
    explicit_entry = str(base.get("entry_date") or "")[:10]
    if explicit_entry:
        entry_index = next((i for i, row in enumerate(bars) if row["date"] >= explicit_entry), None)
        entry_indexes = [entry_index] if entry_index is not None and entry_index >= warmup else []
    else:
        stride = max(5, window // 4)
        entry_indexes = list(range(warmup, len(bars) - forward_days, stride))

    for cohort_number, entry_index in enumerate(entry_indexes, start=1):
        if entry_index is None:
            continue
        test = {
            **base,
            "cost": _number(base.get("cost")) or bars[entry_index]["close"],
            "entry_date": bars[entry_index]["date"],
            "stop_loss_price": None,
            "take_profit_price": None,
        }
        runtime: dict | None = None
        previous_state = "blue"
        last_event_index = entry_index - forward_days
        boundary = min(len(bars) - forward_days, entry_index + window + 1)
        for index in range(entry_index, boundary):
            quote = {
                "price": bars[index]["close"],
                "change_pct": (bars[index]["close"] / bars[index - 1]["close"] - 1) * 100,
                "stale": False,
                "as_of": bars[index]["date"],
            }
            market_context: dict[str, float] = {}
            if benchmark:
                available = [row["close"] for row in benchmark if row["date"] <= bars[index]["date"]]
                for period in (5, 20, 60):
                    if len(available) > period and available[-period - 1]:
                        market_context[f"market_return_{period}d"] = (
                            available[-1] / available[-period - 1] - 1
                        ) * 100
            clock = datetime.fromisoformat(f"{bars[index]['date']}T16:00:00")
            signal = evaluate_holding(
                test,
                quote,
                bars[:index + 1],
                market_context=market_context,
                previous_runtime=runtime,
                clock=clock,
            )
            runtime = signal.get("runtime_state") if isinstance(signal.get("runtime_state"), dict) else runtime
            state = signal["state"]
            is_new_action = (
                state in {"orange", "red"}
                and (
                    previous_state not in {"orange", "red"}
                    or STATE_RANK[state] > STATE_RANK.get(previous_state, 0)
                )
                and index - last_event_index >= forward_days
            )
            previous_state = state
            if not is_new_action:
                continue
            last_event_index = index
            future = bars[index + 1:index + forward_days + 1]
            start = bars[index]["close"]
            future_return = (future[-1]["close"] / start - 1) * 100
            max_adverse = (min(row["low"] for row in future) / start - 1) * 100
            max_favourable = (max(row["high"] for row in future) / start - 1) * 100
            events.append({
                "cohort": cohort_number,
                "entry_date": test["entry_date"],
                "entry_price": round(float(test["cost"]), 4),
                "date": bars[index]["date"],
                "state": state,
                "signal_type": signal.get("signal_type"),
                "price": start,
                "forward_return_pct": round(future_return, 4),
                "max_adverse_pct": round(max_adverse, 4),
                "max_favourable_pct": round(max_favourable, 4),
            })
    if not events:
        return {
            "ok": True, "events": [], "event_count": 0,
            "cohort_count": len(entry_indexes), "rule_version": RULE_VERSION,
        }
    negative_followthrough = sum(item["forward_return_pct"] < 0 for item in events) / len(events)
    adverse_2pct = sum(item["max_adverse_pct"] <= -2 for item in events) / len(events)
    fast_recovery = sum(item["max_favourable_pct"] >= 3 for item in events) / len(events)

    def state_summary(state: str) -> dict:
        rows = [item for item in events if item["state"] == state]
        if not rows:
            return {"event_count": 0}
        return {
            "event_count": len(rows),
            "negative_followthrough_rate": round(sum(item["forward_return_pct"] < 0 for item in rows) / len(rows), 4),
            "adverse_2pct_rate": round(sum(item["max_adverse_pct"] <= -2 for item in rows) / len(rows), 4),
            "average_forward_return_pct": round(sum(item["forward_return_pct"] for item in rows) / len(rows), 4),
        }
    return {
        "ok": True,
        "rule_version": RULE_VERSION,
        "cohort_count": len(entry_indexes),
        "benchmark_covered": bool(benchmark),
        "historical_event_overlay_covered": False,
        "event_count": len(events),
        "negative_followthrough_rate": round(negative_followthrough, 4),
        "adverse_2pct_rate": round(adverse_2pct, 4),
        "fast_recovery_rate": round(fast_recovery, 4),
        "average_forward_return_pct": round(sum(item["forward_return_pct"] for item in events) / len(events), 4),
        "average_max_adverse_pct": round(sum(item["max_adverse_pct"] for item in events) / len(events), 4),
        "by_state": {state: state_summary(state) for state in ("orange", "red")},
        "events": events,
    }
