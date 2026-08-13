"""ETF 观察低频资料与历史指标。

交互列表只消费已保存的 last-good 快照；公开基金档案、净值和日线仅由显式
后台刷新调用，避免普通 GET 对每只 ETF 发起 N+1 请求。取不到保持缺失，
绝不把报价字段、最新净值或历史收益互相冒充。
"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

from . import free_source
from .base import get_ak, now_cn, safe, to_float, ttl_cache

ETF_ENRICHMENT_SCHEMA = "etf-enrichment-v1"
_ETF_CODE_RE = re.compile(r"^(?:5\d{5}|1[56]\d{4})$")


def normalize_etf_code(value: object) -> str:
    code = str(value or "").strip().split(".", 1)[0]
    return code if _ETF_CODE_RE.fullmatch(code) else ""


def etf_market(code: str) -> str:
    return "sh" if str(code).startswith("5") else "sz"


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return None if not text or text in {"---", "--", "nan", "NaT", "None"} else text


def _date_text(value: object) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        return str(value.date()) if hasattr(value, "date") else text[:10]
    except Exception:
        return text[:10]


def _positive(value: object) -> float | None:
    parsed = to_float(value)
    return parsed if parsed is not None and math.isfinite(parsed) and parsed > 0 else None


def _number(value: object) -> float | None:
    parsed = to_float(value)
    return parsed if parsed is not None and math.isfinite(parsed) else None


def _billions_and_date(value: object) -> tuple[float | None, str | None]:
    text = _text(value) or ""
    match = re.search(r"([\d,.]+)\s*(亿元|亿份|万元|万份|元|份)", text)
    amount = None
    if match:
        numeric = to_float(match.group(1))
        scales = {"亿元": 1.0, "亿份": 1.0, "万元": 1e-4, "万份": 1e-4, "元": 1e-8, "份": 1e-8}
        amount = numeric * scales[match.group(2)] if numeric is not None else None
    date_match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
    data_date = None
    if date_match:
        data_date = date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))).isoformat()
    return amount, data_date


def _percent(value: object) -> float | None:
    text = _text(value)
    return _number(text.split("%", 1)[0]) if text else None


def _trade_rule(code: str, fund_type: str | None, tracking_target: str | None = None) -> str | None:
    text = f"{fund_type or ''} {tracking_target or ''}"
    if code.startswith(("511", "513", "518")) or any(
        key in text for key in ("QDII", "海外", "跨境", "商品", "黄金", "债券", "国债", "短融", "货币")
    ):
        return "T+0"
    if any(key in text for key in ("股票", "指数型", "混合型")):
        return "T+1"
    return None


def _kind(fund_type: str | None, tracking_target: str | None) -> tuple[str | None, str | None]:
    text = f"{fund_type or ''} {tracking_target or ''}".lower()
    if "qdii" in text or any(key in text for key in ("纳斯达克", "标普", "msci usa", "日经", "恒生")):
        return "cross", "跨境"
    if any(key in text for key in ("黄金", "商品", "原油", "有色金属期货")):
        return "commodity", "商品"
    if any(key in text for key in ("债券", "国债", "短融", "货币")):
        return "bond", "债券"
    if fund_type or tracking_target:
        return "equity", "股票"
    return None, None


def _overview(code: str) -> tuple[dict[str, Any], list[str]]:
    ak = get_ak()
    fn = getattr(ak, "fund_overview_em", None) if ak else None
    frame = safe(fn, symbol=code, label="ETF基金档案", timeout=8) if fn else None
    if frame is None or getattr(frame, "empty", True):
        return {}, ["基金档案"]
    try:
        row = frame.iloc[0]
        net_assets, asset_date = _billions_and_date(row.get("净资产规模"))
        fund_type = _text(row.get("基金类型"))
        tracking = _text(row.get("跟踪标的")) or _text(row.get("业绩比较基准"))
        kind, kind_label = _kind(fund_type, tracking)
        return {
            "official_name": _text(row.get("基金简称")) or _text(row.get("基金全称")),
            "fund_type": fund_type,
            "kind": kind,
            "kind_label": kind_label,
            "tracking_target": tracking,
            "manager": _text(row.get("基金管理人")),
            "custodian": _text(row.get("基金托管人")),
            "management_fee_pct": _percent(row.get("管理费率")),
            "custody_fee_pct": _percent(row.get("托管费率")),
            "net_assets_billion": net_assets,
            "asset_date": asset_date,
            "trade_rule": _trade_rule(code, fund_type, tracking),
            "profile_source": "天天基金公开档案",
        }, []
    except Exception:
        return {}, ["基金档案解析"]


@ttl_cache(300, fail_seconds=30)
def _nav_frame():
    ak = get_ak()
    fn = getattr(ak, "fund_etf_spot_ths", None) if ak else None
    return safe(fn, label="ETF净值申赎快照", timeout=8) if fn else None


def _nav_snapshot(code: str) -> tuple[dict[str, Any], list[str]]:
    frame = _nav_frame()
    if frame is None or getattr(frame, "empty", True) or "基金代码" not in frame.columns:
        return {}, ["净值申赎"]
    try:
        rows = frame[frame["基金代码"].astype(str).str.zfill(6) == code]
        if rows.empty:
            return {}, ["净值申赎"]
        row = rows.iloc[0]
        latest_nav = _positive(row.get("最新-单位净值"))
        latest_date = _date_text(row.get("最新-交易日"))
        current_nav = _positive(row.get("当前-单位净值"))
        current_date = _date_text(row.get("查询日期")) if current_nav is not None else None
        fund_type = _text(row.get("基金类型"))
        return {
            "official_name": _text(row.get("基金名称")),
            "fund_type": fund_type,
            "latest_nav": latest_nav,
            "nav_date": latest_date,
            "current_nav": current_nav,
            "current_nav_date": current_date,
            "subscribe_status": _text(row.get("申购状态")),
            "redeem_status": _text(row.get("赎回状态")),
            "trade_rule": _trade_rule(code, fund_type),
            "nav_source": "同花顺基金公开数据",
        }, []
    except Exception:
        return {}, ["净值申赎解析"]


def verified_etf_codes(codes: list[str], quote_rows: dict[str, dict] | None = None) -> set[str]:
    """用公开 ETF 名录核验代码；名录暂不可用时仅接受名称明确含 ETF 的报价。"""
    wanted = {normalize_etf_code(code) for code in codes}
    wanted.discard("")
    verified: set[str] = set()
    frame = _nav_frame()
    if frame is not None and not getattr(frame, "empty", True) and "基金代码" in frame.columns:
        try:
            catalog = set(frame["基金代码"].astype(str).str.zfill(6))
            verified.update(wanted & catalog)
        except Exception:
            pass
    for code in wanted - verified:
        name = str(((quote_rows or {}).get(code) or {}).get("name") or "").upper()
        if "ETF" in name:
            verified.add(code)
    return verified


def _history_rows(code: str) -> tuple[list[dict], str | None]:
    rows = list(free_source.daily_bars(code, 90) or [])
    if len(rows) >= 61:
        return rows, "腾讯财经·前复权日K"
    ak = get_ak()
    fn = getattr(ak, "fund_etf_hist_sina", None) if ak else None
    frame = safe(fn, symbol=f"{etf_market(code)}{code}", label="ETF日线新浪备源", timeout=8) if fn else None
    if frame is None or getattr(frame, "empty", True):
        return rows, "腾讯财经·前复权日K" if rows else None
    out = []
    for _, row in frame.tail(90).iterrows():
        close = _positive(row.get("close"))
        day = _date_text(row.get("date"))
        if close is not None and day:
            out.append({"d": day, "c": close})
    return (out, "新浪财经·ETF日K") if len(out) > len(rows) else (rows, "腾讯财经·前复权日K")


def _history_metrics(code: str) -> tuple[dict[str, Any], list[str]]:
    rows, source = _history_rows(code)
    closes = [_positive(row.get("c") if isinstance(row, dict) else None) for row in rows]
    closes = [value for value in closes if value is not None]
    if len(closes) < 61:
        return {}, ["前复权日线不足61根"]
    last = closes[-1]
    returns = {
        window: round((last / closes[-window - 1] - 1) * 100, 2)
        for window in (5, 20, 60)
        if len(closes) > window
    }
    day_returns = [(closes[index] / closes[index - 1] - 1) for index in range(1, len(closes))]
    recent_returns = day_returns[-20:]
    vol20 = None
    if len(recent_returns) >= 2:
        mean = sum(recent_returns) / len(recent_returns)
        variance = sum((value - mean) ** 2 for value in recent_returns) / (len(recent_returns) - 1)
        vol20 = round(math.sqrt(variance) * math.sqrt(250) * 100, 2)
    recent60 = closes[-60:]
    peak = recent60[0]
    drawdown = 0.0
    for value in recent60:
        peak = max(peak, value)
        drawdown = min(drawdown, (value / peak - 1) * 100)
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60
    trend = "MA20上方 · MA60上方" if last >= ma20 and last >= ma60 else (
        "MA20上方 · MA60下方" if last >= ma20 else (
            "MA20下方 · MA60上方" if last >= ma60 else "MA20下方 · MA60下方"
        )
    )
    return {
        "ret5_pct": returns.get(5),
        "ret20_pct": returns.get(20),
        "ret60_pct": returns.get(60),
        "volatility20_pct": vol20,
        "drawdown60_pct": round(drawdown, 2),
        "trend": trend,
        "history_date": _date_text(rows[-1].get("d")) if isinstance(rows[-1], dict) else None,
        "history_source": source,
        "history_adjustment": "qfq" if source and "前复权" in source else "provider_adjusted",
    }, []


def refresh_etf(code: str) -> dict[str, Any]:
    normalized = normalize_etf_code(code)
    if not normalized:
        raise ValueError("ETF代码应为沪深交易型基金代码")
    overview, missing_overview = _overview(normalized)
    nav, missing_nav = _nav_snapshot(normalized)
    history, missing_history = _history_metrics(normalized)
    official_name = nav.get("official_name") or overview.get("official_name")
    fund_type = overview.get("fund_type") or nav.get("fund_type")
    tracking = overview.get("tracking_target")
    kind, kind_label = _kind(fund_type, tracking)
    payload = {
        "schema_version": ETF_ENRICHMENT_SCHEMA,
        "code": normalized,
        **overview,
        **nav,
        **history,
        "official_name": official_name,
        "fund_type": fund_type,
        "kind": overview.get("kind") or kind,
        "kind_label": overview.get("kind_label") or kind_label,
        "trade_rule": overview.get("trade_rule") or nav.get("trade_rule") or _trade_rule(normalized, fund_type, tracking),
        "refreshed_at": now_cn().isoformat(timespec="seconds"),
    }
    payload["missing"] = list(dict.fromkeys(missing_overview + missing_nav + missing_history))
    meaningful_fields = (
        "official_name", "fund_type", "tracking_target", "manager", "net_assets_billion",
        "latest_nav", "subscribe_status", "ret5_pct", "ret20_pct", "ret60_pct", "trend",
    )
    has_meaningful_data = any(payload.get(key) is not None for key in meaningful_fields)
    payload["state"] = "complete" if not payload["missing"] else ("degraded" if has_meaningful_data else "missing")
    return payload
