"""问股用户画像的严格规范化。

画像只描述用户状态与风险约束，不参与行情取数，也不产生交易结论。
所有缺失值保留为 ``None``；调用方传入非法值时直接拒绝，不能静默修正。
"""
from __future__ import annotations

from datetime import datetime, time as dt_time
import math
from typing import Any, Mapping

from ..data.base import is_trade_day, now_cn


HOLDING_STATUSES = {"unheld", "held", "unspecified"}
SCENARIOS = {"intraday", "after_close"}
HORIZONS = {"NEXT_DAY", "SHORT_TERM", "SWING"}


class StockAskProfileError(ValueError):
    """画像字段不满足问股契约。"""


def _number(value: Any, field: str, *, minimum: float, maximum: float | None = None) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise StockAskProfileError(f"{field} 必须是数字")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise StockAskProfileError(f"{field} 必须是数字") from exc
    if not math.isfinite(parsed) or parsed < minimum or (maximum is not None and parsed > maximum):
        upper = f"—{maximum:g}" if maximum is not None else "以上"
        raise StockAskProfileError(f"{field} 必须在 {minimum:g}{upper} 范围内")
    return parsed


def _boolean(value: Any, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise StockAskProfileError(f"{field} 必须是布尔值")
    return value


def _source_number(
    value: Any, field: str, *, minimum: float, maximum: float | None = None
) -> float | None:
    """本地持仓/行情中的坏值按缺失处理，不能把运行数据错误转嫁成 400。"""
    try:
        return _number(value, field, minimum=minimum, maximum=maximum)
    except StockAskProfileError:
        return None


def _choice(value: Any, field: str, allowed: set[str], default: str) -> str:
    text = str(value or default).strip()
    if text not in allowed:
        raise StockAskProfileError(f"{field} 取值无效")
    return text


def validate_profile_payload(payload: Any) -> dict[str, Any]:
    """只校验用户显式提交的字段，返回可安全继续规范化的副本。"""
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise StockAskProfileError("stock_ask_profile 必须是对象")
    data = dict(payload)
    _choice(data.get("holding_status"), "holding_status", HOLDING_STATUSES, "unspecified")
    _choice(data.get("scenario"), "scenario", SCENARIOS, "intraday")
    _choice(data.get("horizon"), "horizon", HORIZONS, "SHORT_TERM")
    _number(data.get("current_price"), "current_price", minimum=0.01)
    _number(data.get("holding_cost"), "holding_cost", minimum=0.01)
    _number(data.get("position_pct"), "position_pct", minimum=0.01, maximum=100.0)
    _number(data.get("risk_budget_pct"), "risk_budget_pct", minimum=0.1, maximum=10.0)
    _number(data.get("max_position_pct"), "max_position_pct", minimum=1.0, maximum=100.0)
    _boolean(data.get("risk_budget_explicit"), "risk_budget_explicit", default=False)
    _boolean(data.get("allow_add"), "allow_add", default=False)
    return data


def _continuous_session(at: datetime) -> bool:
    local = at if at.tzinfo is not None else at.replace(tzinfo=now_cn().tzinfo)
    current = local.time().replace(tzinfo=None)
    return bool(
        is_trade_day(local.date())
        and (
            dt_time(9, 30) <= current <= dt_time(11, 30)
            or dt_time(13, 0) <= current <= dt_time(15, 0)
        )
    )


def normalize_profile(
    payload: Any,
    *,
    holding: Mapping[str, Any] | None = None,
    quote: Mapping[str, Any] | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    """合并用户输入、本地持仓和真实行情，生成可审计的最终画像。"""
    data = validate_profile_payload(payload)
    holding = holding if isinstance(holding, Mapping) else {}
    quote = quote if isinstance(quote, Mapping) else {}
    current = at or now_cn()

    requested_status = _choice(
        data.get("holding_status"), "holding_status", HOLDING_STATUSES, "unspecified"
    )
    effective_status = (
        "held" if holding else "unheld"
    ) if requested_status == "unspecified" else requested_status

    explicit_price = _number(data.get("current_price"), "current_price", minimum=0.01)
    quote_price = _source_number(quote.get("price"), "quote.price", minimum=0.01)
    reference_price = explicit_price if explicit_price is not None else quote_price

    holding_cost = _number(data.get("holding_cost"), "holding_cost", minimum=0.01)
    if holding_cost is None and effective_status == "held":
        holding_cost = _source_number(holding.get("cost"), "holding.cost", minimum=0.01)
    position_pct = _number(
        data.get("position_pct"), "position_pct", minimum=0.01, maximum=100.0
    )
    if position_pct is None and effective_status == "held":
        position_pct = _source_number(
            holding.get("weight_pct"), "holding.weight_pct", minimum=0.01, maximum=100.0
        )
    if effective_status != "held":
        holding_cost = None
        position_pct = None

    requested_scenario = _choice(data.get("scenario"), "scenario", SCENARIOS, "intraday")
    effective_scenario = (
        "intraday"
        if requested_scenario == "intraday" and _continuous_session(current)
        else "after_close"
    )
    risk_budget = _number(
        data.get("risk_budget_pct", 1.5), "risk_budget_pct", minimum=0.1, maximum=10.0
    )
    max_position = _number(
        data.get("max_position_pct", 70.0), "max_position_pct", minimum=1.0, maximum=100.0
    )
    return {
        "holding_status": effective_status,
        "holding_status_requested": requested_status,
        "holding_auto_detected": requested_status == "unspecified",
        "current_price": reference_price,
        "current_price_explicit": explicit_price is not None,
        "quote_price": quote_price,
        "holding_cost": holding_cost,
        "position_pct": position_pct,
        "scenario": requested_scenario,
        "effective_scenario": effective_scenario,
        "scenario_adjusted": requested_scenario != effective_scenario,
        "horizon": _choice(data.get("horizon"), "horizon", HORIZONS, "SHORT_TERM"),
        "risk_budget_pct": risk_budget if risk_budget is not None else 1.5,
        "risk_budget_explicit": _boolean(
            data.get("risk_budget_explicit"), "risk_budget_explicit", default=False
        ),
        "max_position_pct": max_position if max_position is not None else 70.0,
        "allow_add": _boolean(data.get("allow_add"), "allow_add", default=False),
        "normalized_at": current.isoformat(timespec="seconds"),
        "rule_version": "stock-ask-profile-v1",
    }
