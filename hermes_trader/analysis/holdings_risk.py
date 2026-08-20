"""持仓仓位管理 V1：确定性规则、独立快照、只升不降的计划边界。

该模块只消费用户已确认的成本/日期/周期与真实行情。AI 不参与风险线、触发计数、
降低比例或通知决策。行情失败时保留 last-good 并明确 stale，不用旧价伪装新结论。
"""
from __future__ import annotations

import asyncio
import copy
import logging
import math
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Iterable

from ..data.base import is_trade_day, now_cn, to_float, trade_dates


log = logging.getLogger("hermes.holdings-risk")

SCHEMA_VERSION = "holding-risk-evaluation-v1"
ALGORITHM_VERSION = "1.0"
SUPPORTED_PERIODS = {"ultra", "short", "swing"}
PERIOD_LABELS = {"ultra": "超短", "short": "短线", "swing": "波段"}
PERIOD_MAX_DAYS = {"ultra": 3, "short": 5, "swing": 10}
PERIOD_WINDOWS = {"ultra": "1–3个交易日", "short": "3–5个交易日", "swing": "5–10个交易日"}
REDUCTION_BY_TRIGGER_COUNT = {0: 0, 1: 25, 2: 33, 3: 50}
ALLOWED_REDUCTIONS = (0, 25, 33, 50, 67, 100)
_A_SHARE_CODE = re.compile(r"^(?:00[0123]\d{3}|30[01]\d{3}|60[0135]\d{3}|68[89]\d{3}|[48]\d{5}|92\d{4})$")
_REFRESH_WORKERS = 4


def _price(value: float | None) -> float | None:
    return round(value + 1e-10, 3) if value is not None and math.isfinite(value) else None


def _normalise_daily(raw_rows: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for raw in raw_rows or []:
        date = str(raw.get("d") or raw.get("date") or "")[:10]
        row = {
            "stamp": date,
            "date": date,
            "open": to_float(raw.get("o") if "o" in raw else raw.get("open")),
            "high": to_float(raw.get("h") if "h" in raw else raw.get("high")),
            "low": to_float(raw.get("l") if "l" in raw else raw.get("low")),
            "close": to_float(raw.get("c") if "c" in raw else raw.get("close")),
            "volume": to_float(raw.get("v_shares") if "v_shares" in raw else raw.get("volume")),
            "amount": to_float(raw.get("amount")),
        }
        if len(date) == 10 and all(row[key] is not None for key in ("open", "high", "low", "close")):
            rows.append(row)
    rows.sort(key=lambda item: item["stamp"])
    return rows


def _normalise_minute(raw_rows: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for raw in raw_rows or []:
        stamp = str(raw.get("day") or raw.get("datetime") or "")[:19]
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
        if len(stamp) >= 16 and all(row[key] is not None for key in ("open", "high", "low", "close")):
            rows.append(row)
    rows.sort(key=lambda item: item["stamp"])
    return rows


def _complete_daily(rows: list[dict], current: datetime) -> list[dict]:
    if rows and rows[-1]["date"] == current.date().isoformat() and (current.hour, current.minute) < (15, 5):
        return rows[:-1]
    return rows


def _complete_minutes(rows: list[dict], period_minutes: int, current: datetime) -> list[dict]:
    complete: list[dict] = []
    current_naive = current.replace(tzinfo=None)
    for row in rows:
        try:
            opened = datetime.strptime(row["stamp"][:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if opened + timedelta(minutes=period_minutes) <= current_naive:
            complete.append(row)
    return complete


def wilder_atr(rows: list[dict], period: int = 14) -> float | None:
    """Wilder ATR14；首个 ATR 用前 14 个 TR 算术均值，之后按 1/14 平滑。"""
    true_ranges: list[float] = []
    previous_close: float | None = None
    for row in rows:
        high, low, close = (to_float(row.get(key)) for key in ("high", "low", "close"))
        if high is None or low is None or close is None or high < low:
            return None
        true_ranges.append(high - low if previous_close is None else max(
            high - low, abs(high - previous_close), abs(low - previous_close),
        ))
        previous_close = close
    if len(true_ranges) < period:
        return None
    value = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        value = ((period - 1) * value + true_range) / period
    return value if math.isfinite(value) and value > 0 else None


def _ema(rows: list[dict], period: int) -> float | None:
    closes = [to_float(row.get("close")) for row in rows]
    if len(closes) < period or any(value is None for value in closes):
        return None
    alpha = 2 / (period + 1)
    value = float(closes[0])
    for close in closes[1:]:
        value = alpha * float(close) + (1 - alpha) * value
    return value


def _vwap(rows: Iterable[dict]) -> float | None:
    volume_sum = 0.0
    amount_sum = 0.0
    for row in rows:
        volume, amount = to_float(row.get("volume")), to_float(row.get("amount"))
        if volume is None or volume <= 0 or amount is None or amount < 0:
            return None
        volume_sum += volume
        amount_sum += amount
    return amount_sum / volume_sum if volume_sum > 0 else None


def _pivot(rows: list[dict], key: str, *, high: bool) -> float | None:
    latest: float | None = None
    for index in range(2, len(rows) - 2):
        values = [to_float(rows[offset].get(key)) for offset in range(index - 2, index + 3)]
        if any(value is None for value in values):
            continue
        center = float(values[2])
        if center == (max(values) if high else min(values)):
            latest = center
    return latest


def _cluster(candidates: Iterable[float | None], radius: float, reference: float,
             *, below: bool) -> tuple[float | None, int]:
    values = sorted({float(value) for value in candidates if value is not None and math.isfinite(float(value))})
    values = [value for value in values if value <= reference] if below else [value for value in values if value >= reference]
    if not values:
        return None, 0
    groups: list[list[float]] = [[values[0]]]
    for value in values[1:]:
        if value - groups[-1][-1] <= radius:
            groups[-1].append(value)
        else:
            groups.append([value])
    selected = min(groups, key=lambda group: (-len(group), abs(statistics.median(group) - reference)))
    if len(selected) < 2:
        return None, len(selected)
    return float(statistics.median(selected)), len(selected)


def _structure(primary: list[dict], daily: list[dict], price: float, atr: float) -> dict:
    primary_tail = primary[-40:]
    daily_tail = daily[-60:]
    support_candidates = [
        _pivot(primary_tail, "low", high=False),
        min((row["low"] for row in primary_tail[-10:]), default=None),
        _vwap(primary_tail[-20:]), _ema(primary_tail, 20),
        _pivot(daily_tail, "low", high=False),
        min((row["low"] for row in daily_tail[-20:]), default=None),
        _vwap(daily_tail[-20:]), _ema(daily_tail, 20),
    ]
    pressure_candidates = [
        _pivot(primary_tail, "high", high=True),
        max((row["high"] for row in primary_tail[-10:]), default=None),
        _pivot(daily_tail, "high", high=True),
        max((row["high"] for row in daily_tail[-20:]), default=None),
    ]
    support, support_members = _cluster(support_candidates, max(atr * 0.35, price * 0.002), price, below=True)
    pressure, pressure_members = _cluster(pressure_candidates, max(atr * 0.35, price * 0.002), price, below=False)
    support_basis = "cluster"
    if support is None:
        support = next((value for value in (
            _pivot(primary_tail, "low", high=False), _pivot(daily_tail, "low", high=False),
        ) if value is not None and value <= price), None)
        support_basis = "confirmed_pivot" if support is not None else "missing"
    pressure_basis = "cluster"
    if pressure is None:
        pressure = next((value for value in (
            _pivot(primary_tail, "high", high=True), _pivot(daily_tail, "high", high=True),
        ) if value is not None and value >= price), None)
        pressure_basis = "confirmed_pivot" if pressure is not None else "missing"
    return {
        "support": _price(support), "support_basis": support_basis,
        "support_cluster_members": support_members,
        "pressure": _price(pressure), "pressure_basis": pressure_basis,
        "pressure_cluster_members": pressure_members,
    }


def _period_parameters(period: str, atr_daily: float, atr_primary: float) -> dict:
    if period == "ultra":
        return {
            "structure_buffer": max(1.2 * atr_primary, 0.15 * atr_daily),
            "fallback_width": max(2.0 * atr_primary, 0.6 * atr_daily),
            "chandelier_width": max(2.0 * atr_primary, 0.5 * atr_daily),
            "chandelier_start_r": 1.0, "profit_observe_r": 1.0, "stagnation_threshold": 2,
        }
    if period == "short":
        return {
            "structure_buffer": max(0.8 * atr_primary, 0.25 * atr_daily),
            "fallback_width": 1.5 * atr_daily,
            "chandelier_width": 2.2 * atr_daily,
            "chandelier_start_r": 1.0, "profit_observe_r": 1.5, "stagnation_threshold": 2,
        }
    return {
        "structure_buffer": 0.35 * atr_daily,
        "fallback_width": 2.0 * atr_daily,
        "chandelier_width": 2.8 * atr_daily,
        "chandelier_start_r": 1.5, "profit_observe_r": 2.0, "stagnation_threshold": 3,
    }


def _trading_days_held(buy_date: str, daily_all: list[dict], quote: dict, current: datetime) -> int:
    dates = {row["date"] for row in daily_all if row.get("date") and row["date"] >= buy_date}
    quote_date = str(quote.get("as_of") or "")[:10]
    if (
        quote_date >= buy_date and quote_date == current.date().isoformat()
        and quote.get("stale") is not True and (current.hour, current.minute) >= (9, 30)
    ):
        dates.add(quote_date)
    if buy_date not in dates:
        calendar = trade_dates()
        if calendar and buy_date in calendar:
            dates.add(buy_date)
    return len(dates)


def _stagnation(primary: list[dict], pressure: float | None, price: float,
                atr_primary: float, r_multiple: float | None, params: dict) -> dict:
    entered = bool(
        pressure is not None and price >= pressure - 0.25 * atr_primary
        or r_multiple is not None and r_multiple >= params["profit_observe_r"]
    )
    checks: list[dict] = []
    if not entered or len(primary) < 22:
        return {"entered": entered, "confirmed": False, "matched": 0, "threshold": params["stagnation_threshold"], "checks": checks}
    latest, previous = primary[-1], primary[-2]
    checks.append({"code": "pressure_fallback", "matched": bool(
        pressure is not None and max(row["high"] for row in primary[-5:]) >= pressure and latest["close"] < pressure - 0.25 * atr_primary
    ), "label": "到达压力后跌回压力区下沿"})
    volumes = [to_float(row.get("volume")) for row in primary[-21:-1]]
    volume_average = sum(float(value) for value in volumes if value is not None) / len(volumes) if volumes and all(value is not None for value in volumes) else None
    checks.append({"code": "high_volume_stall", "matched": bool(
        volume_average and to_float(latest.get("volume")) is not None
        and latest["volume"] >= 1.5 * volume_average
        and latest["close"] - previous["close"] < 0.5 * atr_primary
    ), "label": "放量但价格推进不足0.5 ATR"})
    spread = latest["high"] - latest["low"]
    upper_wick = latest["high"] - max(latest["open"], latest["close"])
    checks.append({"code": "upper_wick", "matched": bool(
        spread > 0 and upper_wick / spread > 0.4 and latest["close"] <= latest["low"] + spread / 2
    ), "label": "长上影且收于下半区"})
    checks.append({"code": "no_new_high", "matched": bool(
        max(row["high"] for row in primary[-2:]) <= max(row["high"] for row in primary[-7:-2])
    ), "label": "连续两根完整K线未创新高"})
    checks.append({"code": "relative_weakness", "matched": False, "available": False, "label": "弱于所属板块1%以上（数据未接入）"})
    matched = sum(1 for item in checks if item.get("matched"))
    return {
        "entered": True, "confirmed": matched >= params["stagnation_threshold"],
        "matched": matched, "threshold": params["stagnation_threshold"], "checks": checks,
    }


def _data_missing(code: str, plan: dict | None, reasons: list[str], previous: dict | None,
                  current: datetime) -> dict:
    if isinstance(previous, dict) and previous.get("plan_version") == int((plan or {}).get("version") or 0):
        retained = copy.deepcopy(previous)
        retained.update({
            "state": "data_insufficient", "stale": True,
            "fresh": False, "refresh_attempted_at": current.isoformat(timespec="seconds"),
            "missing": list(dict.fromkeys(reasons)),
            "data_note": "本次刷新失败，保留最近有效价格结论及其原始时点；未执行新的价格计算。",
        })
        return retained
    return {
        "schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION,
        "code": code, "plan_version": int((plan or {}).get("version") or 0),
        "period": (plan or {}).get("period"), "state": "data_insufficient",
        "fresh": False, "stale": False, "evaluated_at": current.isoformat(timespec="seconds"),
        "target_reduction_pct": 0, "confirmed_reduction_pct": int((plan or {}).get("confirmed_reduction_pct") or 0),
        "action": "数据不足，暂不调整", "missing": list(dict.fromkeys(reasons)),
        "data_note": "没有可保留的有效价格结论。",
    }


def evaluate_holding(holding: dict, quote: dict | None, daily_raw: list[dict], minute_raw: list[dict],
                     *, previous: dict | None = None, calculated_at: datetime | None = None) -> dict:
    current = calculated_at or now_cn()
    code = str(holding.get("code") or "").strip().zfill(6)
    name = str((quote or {}).get("name") or holding.get("name") or "")
    plan = holding.get("risk_plan") if isinstance(holding.get("risk_plan"), dict) else None
    if plan is None:
        return _data_missing(code, None, ["旧持仓尚未建立成本日期与持仓周期计划"], previous, current)
    period = str(plan.get("period") or "")
    if period not in SUPPORTED_PERIODS:
        return _data_missing(code, plan, ["持仓周期不受支持"], previous, current)
    if not _A_SHARE_CODE.fullmatch(code):
        result = _data_missing(code, plan, ["仅支持普通A股、科创板、创业板和北交所股票"], None, current)
        result.update({"state": "unsupported", "action": "停止常规监控"})
        return result
    if "ST" in name.upper():
        result = _data_missing(code, plan, ["标的当前为ST股票，V1不执行常规技术算法"], None, current)
        result.update({"state": "unsupported", "action": "停止常规监控；请人工处理事件风险"})
        return result

    daily_all = _normalise_daily(daily_raw)
    daily = _complete_daily(daily_all, current)
    minute_period = 5 if period == "ultra" else 60
    minutes = _complete_minutes(_normalise_minute(minute_raw), minute_period, current)
    primary = minutes if period in {"ultra", "short"} else daily
    price = to_float((quote or {}).get("price"))
    quote_note = str((quote or {}).get("quote_note") or "")
    official_close = bool(
        price is not None and (quote or {}).get("stale") is True
        and "收盘价" in quote_note and str((quote or {}).get("as_of") or "")[:10]
    )
    quote_fresh = bool(price is not None and ((quote or {}).get("stale") is not True or official_close))
    missing: list[str] = []
    if len(daily) < 20:
        missing.append(f"完整日K不足20根（当前{len(daily)}根）")
    if len(primary) < 20:
        missing.append(f"完整{'5分钟' if period == 'ultra' else '60分钟' if period == 'short' else '日'}K不足20根（当前{len(primary)}根）")
    atr_daily = wilder_atr(daily)
    atr_primary = wilder_atr(primary)
    if atr_daily is None:
        missing.append("Wilder日ATR14不可计算")
    if atr_primary is None:
        missing.append(f"Wilder{'5分钟' if period == 'ultra' else '60分钟' if period == 'short' else '日'}ATR14不可计算")
    if not quote_fresh:
        missing.append("当前有效报价缺失或陈旧")
    if missing:
        retained = _data_missing(code, plan, missing, previous, current)
        # 价格失败不能重算风险线；但若完整日K仍证明该票当日有交易，可独立推进时间到期。
        price_only_failure = missing == ["当前有效报价缺失或陈旧"]
        if price_only_failure and isinstance(previous, dict):
            days_held = _trading_days_held(str(plan.get("buy_date") or ""), daily_all, quote or {}, current)
            expired = days_held >= PERIOD_MAX_DAYS[period]
            retained["time"] = {"trading_days_held": days_held, "max_days": PERIOD_MAX_DAYS[period], "expired": expired}
            if expired and "time_expired" not in list(retained.get("active_trigger_codes") or []):
                ordinary = list(retained.get("ordinary_triggers") or [])
                ordinary.append({"code": "time_expired", "label": f"已超出{PERIOD_LABELS[period]}区间（{PERIOD_WINDOWS[period]}）"})
                retained["ordinary_triggers"] = ordinary
                retained["ordinary_trigger_count"] = len(ordinary)
                retained["active_trigger_codes"] = [*list(retained.get("active_trigger_codes") or []), "time_expired"]
                target = max(int(retained.get("target_reduction_pct") or 0), REDUCTION_BY_TRIGGER_COUNT.get(len(ordinary), 67))
                retained["target_reduction_pct"] = target
                retained["retain_pct"] = 100 - target
                retained["action"] = f"建议将该持仓降低{target}%"
        return retained

    cost = to_float(holding.get("cost"))
    if cost is None or cost <= 0:
        return _data_missing(code, plan, ["持仓成本无效"], previous, current)
    params = _period_parameters(period, float(atr_daily), float(atr_primary))
    structure = _structure(primary, daily, float(price), float(atr_primary))
    fallback_line = cost - params["fallback_width"]
    structure_line = (
        float(structure["support"]) - params["structure_buffer"]
        if structure.get("support") is not None else None
    )
    candidate_initial = min(structure_line, fallback_line) if structure_line is not None else fallback_line
    prior_initial = to_float((previous or {}).get("initial_risk_line")) if (previous or {}).get("plan_version") == int(plan.get("version") or 1) else None
    initial_line = prior_initial if prior_initial is not None else candidate_initial
    initial_r = max(0.001, cost - initial_line)
    prior_highest = to_float((previous or {}).get("highest_price")) or cost
    highest = max(prior_highest, float(price), max((row["high"] for row in primary if row["date"] >= str(plan.get("buy_date") or "")), default=float(price)))
    r_multiple = (float(price) - cost) / initial_r
    chandelier = highest - params["chandelier_width"] if r_multiple >= params["chandelier_start_r"] else None
    structural_protection = structure_line if structure_line is not None else initial_line
    current_candidate = max(initial_line, structural_protection, chandelier if chandelier is not None else initial_line)
    same_plan_previous = bool(
        isinstance(previous, dict)
        and previous.get("plan_version") == int(plan.get("version") or 1)
        and to_float(previous.get("current_risk_line")) is not None
    )
    previous_line = to_float((previous or {}).get("current_risk_line")) or initial_line
    # 首次评估只冻结初始线；同一轮不能用刚识别的更高结构线制造“立即跌破”。
    current_line = max(previous_line, current_candidate) if same_plan_previous else initial_line
    primary_close = to_float(primary[-1].get("close"))
    extreme = float(price) <= current_line - 0.5 * float(atr_primary)
    customer_risk = to_float(plan.get("customer_risk_price"))
    customer_triggered = customer_risk is not None and float(price) <= customer_risk
    confirmed_break = primary_close is not None and primary_close < current_line
    intraperiod_break = float(price) < current_line and not confirmed_break and not extreme
    near_line = float(price) >= current_line and (float(price) - current_line) / float(atr_primary) <= 0.5

    days_held = _trading_days_held(str(plan.get("buy_date") or ""), daily_all, quote or {}, current)
    expired = days_held >= PERIOD_MAX_DAYS[period]
    target_price = to_float(plan.get("customer_target_price"))
    target_touched = target_price is not None and float(price) >= target_price
    target_counts = target_touched and plan.get("target_mode") == "ordinary_trigger"
    stagnation = _stagnation(primary, to_float(structure.get("pressure")), float(price), float(atr_primary), r_multiple, params)

    decisive_reasons: list[dict] = []
    if customer_triggered:
        decisive_reasons.append({"code": "customer_risk_line", "label": "已触及客户风险线"})
    if extreme:
        decisive_reasons.append({"code": "extreme_break", "label": "价格已明显低于算法风险线0.5 ATR"})
    if confirmed_break:
        decisive_reasons.append({"code": "confirmed_algorithm_break", "label": f"完整{('5分钟' if period == 'ultra' else '60分钟' if period == 'short' else '日')}K线确认跌破算法风险线"})
    ordinary: list[dict] = []
    if intraperiod_break:
        ordinary.append({"code": "intraperiod_break", "label": "盘中跌破算法风险线，等待完整K线确认"})
    if expired:
        ordinary.append({"code": "time_expired", "label": f"已超出{PERIOD_LABELS[period]}区间（{PERIOD_WINDOWS[period]}）"})
    if target_counts:
        ordinary.append({"code": "customer_target_trigger", "label": "达到客户目标价，按计划计入一次仓位触发"})
    if stagnation["confirmed"]:
        ordinary.append({"code": "stagnation_group", "label": f"收益观察区内出现{stagnation['matched']}项滞涨特征"})

    candidate_target = 100 if decisive_reasons else REDUCTION_BY_TRIGGER_COUNT.get(len(ordinary), 67)
    previous_target = int((previous or {}).get("target_reduction_pct") or 0)
    target_reduction = max(previous_target, candidate_target)
    confirmed_reduction = int(plan.get("confirmed_reduction_pct") or 0)
    pending = target_reduction > confirmed_reduction
    execution_constraints: list[str] = []
    if str(plan.get("buy_date") or "") == current.date().isoformat():
        execution_constraints.append("买入当日受T+1限制，可调整数量可能为0")
    previous_close = to_float((quote or {}).get("prev_close"))
    if previous_close is not None and previous_close > 0:
        limit_ratio = 0.30 if code.startswith(("4", "8", "92")) else 0.20 if code.startswith(("30", "68")) else 0.10
        if float(price) <= round(previous_close * (1 - limit_ratio), 2) + 0.01:
            execution_constraints.append("当前接近跌停，仓位建议保留但实际成交受流动性限制")
    action = (
        f"建议将该持仓降低{target_reduction}%" if pending and target_reduction > 0
        else "已完成当前调整，继续监控" if target_reduction > 0
        else "当前无需调整仓位"
    )
    active_trigger_codes = [item["code"] for item in decisive_reasons + ordinary]
    prior_active = list((previous or {}).get("active_trigger_codes") or [])
    prior_episode = int((previous or {}).get("risk_episode") or 0)
    risk_episode = prior_episode + 1 if active_trigger_codes and not prior_active else prior_episode
    return {
        "schema_version": SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION,
        "code": code, "name": name, "plan_version": int(plan.get("version") or 1),
        "period": period, "period_label": PERIOD_LABELS[period], "period_window": PERIOD_WINDOWS[period],
        "state": "pending_adjustment" if pending else "completed_adjustment" if target_reduction else "monitoring",
        "action": action, "target_reduction_pct": target_reduction,
        "retain_pct": 100 - target_reduction, "confirmed_reduction_pct": confirmed_reduction,
        "ordinary_trigger_count": len(ordinary), "ordinary_triggers": ordinary,
        "decisive_triggers": decisive_reasons, "active_trigger_codes": active_trigger_codes,
        "risk_episode": risk_episode, "near_risk_line": near_line,
        "target_touched": target_touched,
        "target_note": "达到客户目标价，仅提醒" if target_touched and not target_counts else None,
        "time": {"trading_days_held": days_held, "max_days": PERIOD_MAX_DAYS[period], "expired": expired},
        "price": _price(float(price)), "quote_as_of": (quote or {}).get("as_of"),
        "quote_source": (quote or {}).get("source"),
        "price_context": "official_close" if official_close else "realtime",
        "initial_risk_line": _price(initial_line), "current_risk_line": _price(current_line),
        "customer_risk_price": _price(customer_risk), "customer_target_price": _price(target_price),
        "risk_distance_pct": round((float(price) - current_line) / float(price) * 100, 2),
        "initial_r": _price(initial_r), "r_multiple": round(r_multiple, 2),
        "highest_price": _price(highest), "chandelier_line": _price(chandelier),
        "atr": {"daily_14": round(float(atr_daily), 6), "primary_14": round(float(atr_primary), 6), "method": "Wilder"},
        "structure": structure, "stagnation": stagnation,
        "environment": {"state": "unavailable", "note": "板块相对强弱尚无稳定同周期契约，本次不计入触发"},
        "events": {"state": "unavailable", "note": "正式事件分级尚无统一结构化契约，本次不计入触发"},
        "corporate_action": {"state": "unavailable", "note": "未取得可靠除权除息事件，不调整历史成本或风险线"},
        "execution_constraints": execution_constraints, "fresh": True, "stale": False, "missing": [],
        "evaluated_at": current.isoformat(timespec="seconds"), "data_as_of": primary[-1]["stamp"],
        "source": {"daily": "腾讯财经·前复权日K", "primary": "新浪公开分时" if period != "swing" else "腾讯财经·前复权日K"},
        "sent_notification_keys": list((previous or {}).get("sent_notification_keys") or []),
    }


def _symbol(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return f"bj{code}"
    return ("sh" if code.startswith(("5", "6", "9")) else "sz") + code


def _notification(state: dict, previous: dict | None) -> tuple[str, str] | None:
    if state.get("state") in {"data_insufficient", "unsupported"}:
        return None
    code = state["code"]
    version = state["plan_version"]
    target = int(state.get("target_reduction_pct") or 0)
    previous_target = int((previous or {}).get("target_reduction_pct") or 0)
    sent = set(state.get("sent_notification_keys") or [])
    key = f"{code}:v{version}:target:{target}"
    if target > previous_target and target in ALLOWED_REDUCTIONS and target > 0 and key not in sent:
        reasons = state.get("decisive_triggers") or state.get("ordinary_triggers") or []
        reason_text = "；".join(str(item.get("label") or "") for item in reasons) or "仓位管理条件升级"
        return key, (
            f"【持仓仓位管理】{state.get('name') or code}（{code}）\n"
            f"{state['action']}，保留{state.get('retain_pct')}%。\n"
            f"依据：{reason_text}\n当前价：{state.get('price')}；算法风险线：{state.get('current_risk_line')}\n"
            f"计划：{state.get('period_label')}，第{(state.get('time') or {}).get('trading_days_held')}个交易日。"
        )
    prior_active = list((previous or {}).get("active_trigger_codes") or [])
    current_active = list(state.get("active_trigger_codes") or [])
    episode = int((previous or {}).get("risk_episode") or state.get("risk_episode") or 0)
    recovery_key = f"{code}:v{version}:recovery:{episode}"
    if prior_active and not current_active and recovery_key not in sent:
        return recovery_key, (
            f"【持仓风险解除】{state.get('name') or code}（{code}）\n"
            "本轮价格风险条件已解除；历史累计降低目标不回退，不建议补回仓位。\n"
            f"当前价：{state.get('price')}；算法风险线：{state.get('current_risk_line')}。"
        )
    return None


def push_test_message() -> str:
    """固定的持仓提示链路测试文案；不读取真实持仓或行情。"""
    return (
        "【持仓仓位管理·链路测试】\n"
        "测试标的（TEST）\n"
        "建议将该持仓降低25%，保留75%。\n"
        "依据：飞书接口与消息格式测试，未读取真实触发条件。\n"
        "当前价：未读取；算法风险线：未读取。\n"
        "计划：短线（演示）。\n"
        "⚠️ 本消息仅用于推送链路测试，不代表任何真实持仓结论，请勿据此调整仓位。"
    )


class HoldingRiskService:
    def __init__(self, config, store, notifier) -> None:
        self.config = config
        self.store = store
        self.notifier = notifier
        self._refresh_lock = asyncio.Lock()

    def snapshots(self) -> dict[str, dict]:
        return self.store.holdings_risk_states()

    async def refresh_all(self, force: bool = False) -> dict:
        if self._refresh_lock.locked():
            return {"ok": True, "skipped": "refresh_in_progress", "states": self.snapshots()}
        async with self._refresh_lock:
            holdings = [row for row in self.store.holdings() if isinstance(row, dict) and row.get("code")]
            previous_states = self.store.holdings_risk_states()
            active_codes = {str(row.get("code") or "").zfill(6) for row in holdings}
            for obsolete_code in set(previous_states) - active_codes:
                self.store.delete_holding_risk_state(obsolete_code)
            if not holdings:
                return {"ok": True, "count": 0, "states": {}}
            current = now_cn()
            minute_of_day = current.hour * 60 + current.minute
            active_session = bool(
                is_trade_day(current.date()) and 9 * 60 + 25 <= minute_of_day <= 15 * 60 + 10
            )
            if not force and not active_session and active_codes.issubset(previous_states):
                return {"ok": True, "skipped": "outside_monitoring_session", "states": previous_states}
            from ..data import free_source, stock  # noqa: PLC0415

            codes = [str(row.get("code") or "").zfill(6) for row in holdings]
            quotes = await asyncio.to_thread(stock.quick_quotes, codes)

            def calculate(row: dict) -> tuple[str, dict]:
                code = str(row.get("code") or "").zfill(6)
                plan = row.get("risk_plan") if isinstance(row.get("risk_plan"), dict) else {}
                period = str(plan.get("period") or "short")
                daily = free_source.daily_bars(code, 140)
                minute = [] if period == "swing" else free_source.minute_bars(_symbol(code), "5" if period == "ultra" else "60", 600)
                return code, evaluate_holding(row, quotes.get(code), daily, minute, previous=previous_states.get(code))

            calculated: dict[str, dict] = {}
            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor(max_workers=_REFRESH_WORKERS, thread_name_prefix="holding-risk") as pool:
                futures = [loop.run_in_executor(pool, calculate, row) for row in holdings]
                results = await asyncio.gather(*futures, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        exc = result
                        log.info("持仓风险单票刷新失败: %s", str(exc)[:160])
                        continue
                    code, state = result
                    calculated[code] = state
                    previous = previous_states.get(code)
                    self.store.save_holding_risk_state(code, state)
                    changed = (
                        previous is None
                        or previous.get("target_reduction_pct") != state.get("target_reduction_pct")
                        or previous.get("state") != state.get("state")
                        or previous.get("active_trigger_codes") != state.get("active_trigger_codes")
                    )
                    if changed:
                        self.store.append_holding_risk_event({
                            "type": "evaluation_changed", "code": code,
                            "plan_version": state.get("plan_version"), "state": state.get("state"),
                            "target_reduction_pct": state.get("target_reduction_pct"),
                            "active_trigger_codes": state.get("active_trigger_codes"),
                            "evaluated_at": state.get("evaluated_at"),
                        })
                    notice = _notification(state, previous)
                    if notice and bool(getattr(self.notifier, "configured", False)):
                        key, message = notice
                        try:
                            pushed = await self.notifier.push_text(message)
                        except Exception as exc:  # noqa: BLE001
                            pushed = False
                            log.info("持仓风险推送异常: %s", str(exc)[:120])
                        self.store.append_holding_risk_event({
                            "type": "notification", "code": code, "plan_version": state.get("plan_version"),
                            "notification_key": key, "status": "sent" if pushed else "failed",
                        })
                        if pushed:
                            state["sent_notification_keys"] = list(dict.fromkeys([*state.get("sent_notification_keys", []), key]))[-100:]
                            self.store.save_holding_risk_state(code, state)
            return {"ok": True, "count": len(holdings), "evaluated": len(calculated), "states": calculated}


def register(scheduler, service: HoldingRiskService) -> None:
    """每5分钟刷新一次；上轮未完成时不叠加，首轮在调度器启动后立即执行。"""
    from apscheduler.triggers.interval import IntervalTrigger  # noqa: PLC0415

    scheduler.add_job(
        service.refresh_all,
        IntervalTrigger(minutes=5, timezone=service.config.timezone),
        id="holdings_risk_refresh", replace_existing=True, coalesce=True,
        max_instances=1, misfire_grace_time=300, next_run_time=now_cn(),
    )
