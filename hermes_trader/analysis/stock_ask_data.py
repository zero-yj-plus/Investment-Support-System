"""问股六桶确定性分析与 Markdown 成稿。

本模块不访问模型，也不把缺失值换成 0。输入必须来自已经核验的诊断卡、
市场总览和用户画像；输出中的动作、价格与风险边界均可由输入复算。
"""
from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta
import math
from typing import Any, Iterable, Mapping

from ..data.base import next_trade_day, now_cn


RULE_VERSION = "stock-ask-six-bucket-v2"
HORIZON_WEIGHTS = {
    "NEXT_DAY": {"MV": 0.25, "FV": 0.15, "WV": 0.20, "QV": 0.10, "TV": 0.15, "IV": 0.15},
    "SHORT_TERM": {"MV": 0.25, "FV": 0.15, "WV": 0.20, "QV": 0.10, "TV": 0.15, "IV": 0.15},
    "SWING": {"MV": 0.15, "FV": 0.10, "WV": 0.15, "QV": 0.25, "TV": 0.25, "IV": 0.10},
}
BUCKET_NAMES = {
    "MV": "市场与板块",
    "FV": "固定方法论解释",
    "WV": "量价与威科夫",
    "QV": "质量、估值与催化",
    "TV": "趋势与相对强度",
    "IV": "技术指标",
}


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bar_number(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _bar_day(row: Mapping[str, Any]) -> date | None:
    raw = _text(row.get("date") or row.get("day") or row.get("trade_date") or row.get("d"))[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _completed_daily_rows(rows: Any, at: datetime) -> list[dict[str, Any]]:
    """排除尚未收盘的当日日 K；字段不完整的行不参与执行确认。"""
    completed: list[dict[str, Any]] = []
    current_day_complete = at.time().replace(tzinfo=None) >= dt_time(15, 0)
    for value in rows if isinstance(rows, list) else []:
        if not isinstance(value, Mapping):
            continue
        day = _bar_day(value)
        close = _bar_number(value, "close", "c")
        if day is None or close is None or day > at.date():
            continue
        if day == at.date() and not current_day_complete:
            continue
        completed.append(dict(value))
    completed.sort(key=lambda row: _bar_day(row) or date.min)
    return completed


def _signal(
    signal_id: str,
    label: str,
    value: int | None,
    *,
    weight: float = 1.0,
    evidence: str = "",
    source: str = "",
) -> dict[str, Any]:
    return {
        "id": signal_id,
        "label": label,
        "value": value if value in {-1, 0, 1} else None,
        "weight": float(weight),
        "evidence": evidence,
        "source": source,
    }


def _bucket(bucket_id: str, signals: Iterable[dict[str, Any]], *, score_cap: int | None = None) -> dict:
    rows = list(signals)
    available = [row for row in rows if row.get("value") is not None]
    total_weight = sum(max(0.0, float(row.get("weight") or 0.0)) for row in rows)
    used_weight = sum(max(0.0, float(row.get("weight") or 0.0)) for row in available)
    raw = (
        sum(float(row["value"]) * float(row.get("weight") or 0.0) for row in available) / used_weight
        if used_weight > 0 else None
    )
    if raw is None:
        score = 0
    elif raw >= 0.60:
        score = 2
    elif raw >= 0.20:
        score = 1
    elif raw <= -0.60:
        score = -2
    elif raw <= -0.20:
        score = -1
    else:
        score = 0
    if score_cap is not None and score > score_cap:
        score = score_cap
    evidence = [row["evidence"] for row in available if _text(row.get("evidence"))]
    return {
        "id": bucket_id,
        "name": BUCKET_NAMES[bucket_id],
        "score": score,
        "raw": round(raw, 4) if raw is not None else None,
        "coverage": round(used_weight / total_weight, 4) if total_weight else 0.0,
        "available_signals": len(available),
        "total_signals": len(rows),
        "signals": rows,
        "summary": "；".join(evidence[:3]) or "该桶暂无足够可核验证据",
    }


def build_context(
    card: Mapping[str, Any], market_overview: Mapping[str, Any] | None,
    *, at: datetime | None = None,
) -> dict:
    """把现有诊断卡映射成五类证据块；没有的能力保持 missing。"""
    quote = dict(card.get("market_quote") or {})
    technical = dict(card.get("technical_analysis") or {})
    metrics = dict(technical.get("metrics") or {})
    levels = dict(technical.get("levels") or {})
    board = dict(card.get("board") or {})
    financial = dict(card.get("financial") or {})
    overview = dict(market_overview or {})
    indexes = [row for row in (overview.get("indexes") or []) if isinstance(row, Mapping)]
    index_changes = [
        value for value in (_number(row.get("change_pct")) for row in indexes) if value is not None
    ]
    average_index_change = sum(index_changes) / len(index_changes) if index_changes else None
    up_count = _number(overview.get("up_count"))
    down_count = _number(overview.get("down_count"))

    peer_median = _number(card.get("peer_pe_median"))
    peer_count = int(_number(card.get("peer_pe_count")) or 0)
    events = [row for row in (card.get("stock_ask_events") or []) if isinstance(row, Mapping)]
    material_payloads = card.get("material_payloads") or {}
    technical_payload = (
        material_payloads.get("technical") if isinstance(material_payloads, Mapping) else None
    ) or {}
    minute_rows = technical_payload.get("minute") if isinstance(technical_payload, Mapping) else None
    completed_daily = _completed_daily_rows(
        technical_payload.get("daily") if isinstance(technical_payload, Mapping) else None,
        at or now_cn(),
    )
    verified_60m = card.get("stock_ask_60m_bars")
    completed_60m = [dict(row) for row in verified_60m if isinstance(row, Mapping)] if isinstance(verified_60m, list) else []
    board_return_5d = _number(card.get("industry_return_5d_pct"))
    board_return_10d = _number(card.get("industry_return_10d_pct"))
    board_complete = bool(
        board and board_return_5d is not None and board_return_10d is not None
    )

    return {
        "board": {
            "state": "complete" if board_complete else "partial" if board else "missing",
            "industry": _text(board.get("industry")) or None,
            "concepts": list(board.get("concepts") or []),
            "return_5d_pct": board_return_5d,
            "return_10d_pct": board_return_10d,
            "source": board.get("source"),
            "reason": None if board_complete else "当前只取得行业归属，行业 5/10 日历史未返回" if board else "行业与板块历史未返回",
        },
        "valuation": {
            "state": "complete" if peer_count >= 5 and peer_median is not None else "partial" if financial else "missing",
            "pe": _number(quote.get("pe") if quote.get("pe") is not None else financial.get("pe")),
            "peer_pe_median": peer_median,
            "peer_count": peer_count,
            "historical_valuation_available": bool(card.get("historical_valuation_available")),
            "source": financial.get("source") or quote.get("source"),
            "reason": "同行有效估值样本少于 5 家" if peer_count < 5 else None,
        },
        "events": {
            "state": "complete" if events else "missing",
            "items": [dict(row) for row in events],
            "source": card.get("stock_ask_events_source"),
            "reason": None if events else "官方公告事件账本未接入本轮证据",
        },
        "technical": {
            "state": "complete" if technical.get("complete") else "partial" if technical else "missing",
            "bars": int(_number(technical.get("bars")) or 0),
            "metrics": metrics,
            "levels": levels,
            "structure": technical.get("structure"),
            "complete": bool(technical.get("complete")),
            "minute_complete": bool(completed_60m),
            "intraday_rows_available": bool(isinstance(minute_rows, list) and minute_rows),
            "source": technical.get("source") or "前复权日K / 服务端确定性计算",
        },
        "market": {
            "state": "complete" if index_changes and up_count is not None and down_count is not None else "partial" if index_changes or up_count is not None else "missing",
            "average_index_change_pct": round(average_index_change, 4) if average_index_change is not None else None,
            "up_count": int(up_count) if up_count is not None else None,
            "down_count": int(down_count) if down_count is not None else None,
            "indexes": [dict(row) for row in indexes],
            "source": "市场总览多源聚合",
            "missing": list(overview.get("missing") or []),
        },
        "quote": quote,
        "financial": financial,
        "data_as_of": quote.get("as_of") or card.get("decision_generated_at") or (card.get("analysis_pipeline") or {}).get("data_as_of"),
        "missing": list(dict.fromkeys(_text(item) for item in (card.get("missing") or []) if _text(item))),
        "_completed_daily": completed_daily,
        "_completed_60m": completed_60m,
        "rule_version": "stock-ask-context-v1",
    }


def _market_regime(context: Mapping[str, Any]) -> tuple[int | None, str]:
    market_block = context.get("market") or {}
    average = _number(market_block.get("average_index_change_pct"))
    up_count = _number(market_block.get("up_count"))
    down_count = _number(market_block.get("down_count"))
    directions: list[int] = []
    if average is not None:
        directions.append(1 if average > 0.25 else -1 if average < -0.25 else 0)
    if up_count is not None and down_count is not None:
        ratio = up_count / max(down_count, 1.0)
        directions.append(1 if ratio >= 1.15 else -1 if ratio <= 0.87 else 0)
    if not directions:
        return None, "指数与市场宽度未返回"
    value = 1 if all(item >= 0 for item in directions) and any(item > 0 for item in directions) else (
        -1 if all(item <= 0 for item in directions) and any(item < 0 for item in directions) else 0
    )
    return value, f"主要指数均值 {average:+.2f}%" if average is not None else f"上涨/下跌 {int(up_count or 0)}/{int(down_count or 0)}"


def _six_buckets(context: Mapping[str, Any]) -> dict[str, dict]:
    quote = context.get("quote") or {}
    technical = context.get("technical") or {}
    metrics = technical.get("metrics") or {}
    levels = technical.get("levels") or {}
    market_block = context.get("market") or {}
    board = context.get("board") or {}
    valuation = context.get("valuation") or {}
    events_block = context.get("events") or {}
    financial = context.get("financial") or {}
    price = _number(quote.get("price"))
    change = _number(quote.get("change_pct"))
    average_index = _number(market_block.get("average_index_change_pct"))
    regime, regime_text = _market_regime(context)

    relative = (change - average_index) if change is not None and average_index is not None else None
    mv = _bucket("MV", [
        _signal("market_regime", "市场状态", regime, evidence=regime_text, source="市场总览"),
        _signal(
            "stock_relative_market", "个股相对大盘",
            1 if relative is not None and relative >= 0.5 else -1 if relative is not None and relative <= -0.5 else 0 if relative is not None else None,
            weight=2.0,
            evidence=f"个股相对主要指数 {relative:+.2f} 个百分点" if relative is not None else "",
            source="行情快照/指数快照",
        ),
        _signal(
            "industry_5d", "行业 5 日方向",
            1 if _number(board.get("return_5d_pct")) is not None and _number(board.get("return_5d_pct")) > 0 else -1 if _number(board.get("return_5d_pct")) is not None and _number(board.get("return_5d_pct")) < 0 else 0 if _number(board.get("return_5d_pct")) is not None else None,
            evidence=f"行业 5 日 {_number(board.get('return_5d_pct')):+.2f}%" if _number(board.get("return_5d_pct")) is not None else "",
            source=_text(board.get("source")),
        ),
        _signal(
            "industry_10d", "行业 10 日方向",
            1 if _number(board.get("return_10d_pct")) is not None and _number(board.get("return_10d_pct")) > 0 else -1 if _number(board.get("return_10d_pct")) is not None and _number(board.get("return_10d_pct")) < 0 else 0 if _number(board.get("return_10d_pct")) is not None else None,
            evidence=f"行业 10 日 {_number(board.get('return_10d_pct')):+.2f}%" if _number(board.get("return_10d_pct")) is not None else "",
            source=_text(board.get("source")),
        ),
    ])

    volume_ratio = _number(quote.get("volume_ratio"))
    if volume_ratio is None:
        volume_ratio = _number(metrics.get("volume_5_20"))
    support = _number(levels.get("support"))
    volume_value = None
    volume_evidence = ""
    if volume_ratio is not None and change is not None:
        if volume_ratio >= 1.1:
            volume_value = 1 if change > 0 else -1 if change < 0 else 0
            volume_evidence = f"量能比 {volume_ratio:.2f}，当期涨跌 {change:+.2f}%"
        elif volume_ratio <= 0.8 and change < 0 and price is not None and support is not None and price >= support:
            volume_value = 1
            volume_evidence = f"量能比 {volume_ratio:.2f}，价格仍在支撑 {support:.2f} 上方"
        else:
            volume_value = 0
            volume_evidence = f"量能比 {volume_ratio:.2f}"
    wv_signals = [
        _signal("volume_price", "量价关系", volume_value, evidence=volume_evidence, source="行情/前复权日K"),
        _signal("contraction_reversal", "缩量回调后放量反包", None, source="完成日K"),
        _signal("wyckoff_confirmation", "结构确认", None, source="完成K线"),
    ]
    daily_rows = context.get("_completed_daily") or []
    if len(daily_rows) >= 3:
        older, previous, latest = daily_rows[-3:]
        older_volume = _bar_number(older, "volume", "vol", "v")
        previous_volume = _bar_number(previous, "volume", "vol", "v")
        latest_volume = _bar_number(latest, "volume", "vol", "v")
        previous_open = _bar_number(previous, "open", "o")
        previous_close = _bar_number(previous, "close", "c")
        latest_open = _bar_number(latest, "open", "o")
        latest_close = _bar_number(latest, "close", "c")
        available = all(value is not None for value in (
            older_volume, previous_volume, latest_volume,
            previous_open, previous_close, latest_open, latest_close,
        ))
        confirmed = bool(
            available
            and previous_volume < older_volume
            and previous_close < previous_open
            and latest_volume > previous_volume
            and latest_close > latest_open
        )
        wv_signals[1] = _signal(
            "contraction_reversal", "缩量回调后放量反包",
            1 if confirmed else 0 if available else None,
            evidence=(
                f"前三根完成日K量能 {older_volume:.0f}/{previous_volume:.0f}/{latest_volume:.0f}"
                if available else ""
            ),
            source="完成日K",
        )
    wv = _bucket("WV", wv_signals, score_cap=1 if sum(row["value"] is not None for row in wv_signals) < 3 else None)

    roe = _number(financial.get("roe"))
    debt = _number(financial.get("debt_to_assets"))
    gross = _number(financial.get("grossprofit_margin"))
    pe = _number(valuation.get("pe"))
    peer_pe = _number(valuation.get("peer_pe_median"))
    peer_count = int(_number(valuation.get("peer_count")) or 0)
    effective_events = list(events_block.get("items") or [])
    event_directions = [
        -1 if _text(item.get("direction")) in {"negative", "major_negative", "利空", "重大负面"}
        else 1 if _text(item.get("direction")) in {"positive", "利好"}
        else 0
        for item in effective_events if isinstance(item, Mapping)
    ]
    qv = _bucket("QV", [
        _signal("roe", "ROE", 1 if roe is not None and roe >= 10 else -1 if roe is not None and roe <= 0 else 0 if roe is not None else None, evidence=f"ROE {roe:.2f}%" if roe is not None else "", source=_text(financial.get("source"))),
        _signal("debt", "资产负债率", 1 if debt is not None and debt <= 60 else -1 if debt is not None and debt >= 80 else 0 if debt is not None else None, evidence=f"资产负债率 {debt:.2f}%" if debt is not None else "", source=_text(financial.get("source"))),
        _signal("gross_margin", "毛利率", 1 if gross is not None and gross >= 20 else -1 if gross is not None and gross <= 0 else 0 if gross is not None else None, evidence=f"毛利率 {gross:.2f}%" if gross is not None else "", source=_text(financial.get("source"))),
        _signal("peer_pe", "同行估值", 1 if pe is not None and peer_pe is not None and pe <= peer_pe * 0.9 else -1 if pe is not None and peer_pe is not None and pe >= peer_pe * 1.25 else 0 if pe is not None and peer_pe is not None else None, evidence=f"PE {pe:.2f} / 同行中位 {peer_pe:.2f}" if pe is not None and peer_pe is not None else "", source=_text(valuation.get("source"))),
        _signal("events", "官方事件", -1 if any(item < 0 for item in event_directions) else 1 if any(item > 0 for item in event_directions) else 0 if event_directions else None, evidence=f"有效官方事件 {len(event_directions)} 条" if event_directions else "", source=_text(events_block.get("source"))),
    ])
    if qv["score"] > 0 and peer_count < 5 and not effective_events:
        qv["score"] = 0
        qv["positive_cap_reason"] = "同行有效样本少于 5 家且无有效官方事件，QV 不形成正分"

    ma5 = _number(metrics.get("ma5"))
    ma10 = _number(metrics.get("ma10"))
    ma20 = _number(metrics.get("ma20"))
    ma60 = _number(metrics.get("ma60"))
    ma20_prev = _number(metrics.get("ma20_prev"))
    stock_ret5 = _number(metrics.get("return5_pct"))
    stock_ret10 = _number(metrics.get("return10_pct"))
    industry_ret5 = _number(board.get("return_5d_pct"))
    industry_ret10 = _number(board.get("return_10d_pct"))
    tv_signals = []
    for key, label, average in (("price_ma5", "价格相对 MA5", ma5), ("price_ma10", "价格相对 MA10", ma10), ("price_ma20", "价格相对 MA20", ma20)):
        tv_signals.append(_signal(key, label, 1 if price is not None and average is not None and price >= average else -1 if price is not None and average is not None else None, evidence=f"现价 {price:.2f} / {label[-3:]} {average:.2f}" if price is not None and average is not None else "", source="前复权日K"))
    tv_signals.extend([
        _signal("ma20_ma60", "MA20 相对 MA60", 1 if ma20 is not None and ma60 is not None and ma20 >= ma60 else -1 if ma20 is not None and ma60 is not None else None, evidence=f"MA20 {ma20:.2f} / MA60 {ma60:.2f}" if ma20 is not None and ma60 is not None else "", source="前复权日K"),
        _signal("ma20_slope", "MA20 五日斜率", 1 if ma20 is not None and ma20_prev is not None and ma20 >= ma20_prev else -1 if ma20 is not None and ma20_prev is not None else None, evidence=f"MA20 当前 {ma20:.2f} / 五日前 {ma20_prev:.2f}" if ma20 is not None and ma20_prev is not None else "", source="前复权日K"),
        _signal("relative_industry_5d", "相对行业 5 日", 1 if stock_ret5 is not None and industry_ret5 is not None and stock_ret5 > industry_ret5 else -1 if stock_ret5 is not None and industry_ret5 is not None and stock_ret5 < industry_ret5 else 0 if stock_ret5 is not None and industry_ret5 is not None else None, evidence=f"个股/行业 5 日 {stock_ret5:+.2f}%/{industry_ret5:+.2f}%" if stock_ret5 is not None and industry_ret5 is not None else "", source="前复权日K/行业K"),
        _signal("relative_industry_10d", "相对行业 10 日", 1 if stock_ret10 is not None and industry_ret10 is not None and stock_ret10 > industry_ret10 else -1 if stock_ret10 is not None and industry_ret10 is not None and stock_ret10 < industry_ret10 else 0 if stock_ret10 is not None and industry_ret10 is not None else None, evidence=f"个股/行业 10 日 {stock_ret10:+.2f}%/{industry_ret10:+.2f}%" if stock_ret10 is not None and industry_ret10 is not None else "", source="前复权日K/行业K"),
    ])
    tv = _bucket("TV", tv_signals)

    rsi = _number(metrics.get("rsi14"))
    macd = _number(metrics.get("macd_hist"))
    macd_prev = _number(metrics.get("macd_hist_prev"))
    iv = _bucket("IV", [
        _signal("rsi14", "RSI14", 1 if rsi is not None and 48 <= rsi <= 68 else -1 if rsi is not None and rsi < 45 else 0 if rsi is not None else None, evidence=f"RSI14 {rsi:.1f}" if rsi is not None else "", source="前复权日K"),
        _signal("macd_sign", "MACD 柱方向", 1 if macd is not None and macd > 0 else -1 if macd is not None and macd < 0 else 0 if macd is not None else None, evidence=f"MACD 柱 {macd:+.4f}" if macd is not None else "", source="前复权日K"),
        _signal("macd_improving", "MACD 柱改善", 1 if macd is not None and macd_prev is not None and macd > macd_prev else -1 if macd is not None and macd_prev is not None and macd < macd_prev else 0 if macd is not None and macd_prev is not None else None, evidence=f"MACD 柱当前/前值 {macd:+.4f}/{macd_prev:+.4f}" if macd is not None and macd_prev is not None else "", source="前复权日K"),
    ])
    if iv["score"] >= 2 and (rsi is None or rsi < 70):
        iv["score"] = 1
        iv["positive_cap_reason"] = "RSI 未进入 70 以上，IV 正分封顶 +1"

    fv = {
        "id": "FV", "name": BUCKET_NAMES["FV"], "score": 0, "raw": 0.0,
        "coverage": 1.0, "available_signals": 1, "total_signals": 1,
        "signals": [], "summary": "固定方法论只解释证据，不重复计分", "explanation_only": True,
    }
    return {"MV": mv, "FV": fv, "WV": wv, "QV": qv, "TV": tv, "IV": iv}


def _confidence(
    context: Mapping[str, Any],
    profile: Mapping[str, Any],
    buckets: Mapping[str, Mapping[str, Any]],
    card: Mapping[str, Any],
) -> dict:
    deductions: list[dict[str, Any]] = []

    def deduct(code: str, label: str, points: int) -> None:
        if code not in {item["code"] for item in deductions}:
            deductions.append({"code": code, "label": label, "points": points})

    if _number(profile.get("current_price")) is None:
        deduct("reference_price_missing", "缺参考价", 15)
    if not (context.get("technical") or {}).get("complete"):
        deduct("technical_incomplete", "日线/技术结构不完整", 15)
    if _number((context.get("board") or {}).get("return_5d_pct")) is None:
        deduct("board_history_missing", "板块历史缺失", 5)
    valuation = context.get("valuation") or {}
    if int(_number(valuation.get("peer_count")) or 0) < 5:
        deduct("peer_sample_small", "同行有效样本少于 5", 10)
    if not valuation.get("historical_valuation_available"):
        deduct("historical_valuation_missing", "历史估值缺失", 5)
    if (context.get("events") or {}).get("state") != "complete":
        deduct("events_missing", "官方事件账本缺失", 5)
    market_block = context.get("market") or {}
    if market_block.get("up_count") is None or market_block.get("down_count") is None:
        deduct("market_breadth_missing", "市场宽度缺失", 5)
    if not (context.get("technical") or {}).get("minute_complete"):
        deduct("supplemental_bars_missing", "补充 60 分钟线缺失", 5)
    if (context.get("quote") or {}).get("stale"):
        deduct("quote_stale", "盘中行情延迟或当前为陈旧快照", 15)
    directed = [int(bucket.get("score") or 0) for key, bucket in buckets.items() if key != "FV" and int(bucket.get("score") or 0) != 0]
    if len(directed) >= 3 and any(item > 0 for item in directed) and any(item < 0 for item in directed):
        deduct("direction_conflict", "至少三个有方向的桶中多空并存", 10)
    quality = card.get("decision_market_evidence_quality") or card.get("market_evidence_quality") or {}
    if _text(quality.get("status")) == "conflict":
        deduct("market_evidence_conflict", "关键行情存在跨源冲突", 10)

    explicit_price = bool(profile.get("current_price_explicit"))
    reference = _number(profile.get("current_price"))
    quote_price = _number(profile.get("quote_price"))
    manual_price_conflict = bool(
        explicit_price and reference is not None and quote_price is not None and quote_price > 0
        and abs(reference / quote_price - 1.0) > 0.01
    )
    if manual_price_conflict:
        deduct("manual_price_conflict", "手填价与行情价偏差超过 1%", 15)
    score = max(0, 100 - sum(int(item["points"]) for item in deductions))
    if manual_price_conflict:
        score = min(score, 59)
    level = "较高" if score >= 80 else "中等" if score >= 60 else "较低" if score >= 40 else "数据不足"
    return {"score": score, "level": level, "deductions": deductions, "manual_price_conflict": manual_price_conflict}


def _expiry(profile: Mapping[str, Any], at: datetime) -> str:
    if profile.get("effective_scenario") == "intraday":
        return at.replace(hour=15, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    next_day = next_trade_day(at.date())
    if next_day:
        parsed_day = date.fromisoformat(next_day) if isinstance(next_day, str) else next_day
        return datetime.combine(parsed_day, dt_time(15, 0), tzinfo=at.tzinfo).isoformat(timespec="seconds")
    return (at + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def _execution(
    context: Mapping[str, Any], profile: Mapping[str, Any], trade_plan: Mapping[str, Any], at: datetime
) -> tuple[dict, dict]:
    price = _number(profile.get("current_price"))
    support = _number(trade_plan.get("support"))
    resistance = _number(trade_plan.get("resistance"))
    invalidation = _number(trade_plan.get("invalidation"))
    atr = _number(trade_plan.get("atr14"))
    if invalidation is None:
        ma10 = _number(((context.get("technical") or {}).get("metrics") or {}).get("ma10"))
        daily_closes = [
            value for value in (
                _bar_number(row, "close", "c") for row in (context.get("_completed_daily") or [])[-20:]
            ) if value is not None
        ]
        candidates = [value for value in daily_closes if ma10 is not None and value < ma10]
        invalidation = max(candidates) if candidates else support * 0.98 if support is not None else ma10
    reward = resistance - price if resistance is not None and price is not None and resistance > price else None
    risk = price - invalidation if price is not None and invalidation is not None and invalidation < price else None
    rr = reward / risk if reward is not None and risk not in (None, 0) else None
    single_risk = risk / price if risk is not None and price not in (None, 0) else None
    position_pct = _number(profile.get("position_pct"))
    account_risk_pct = position_pct * single_risk if position_pct is not None and single_risk is not None else None
    pos_max = None
    if profile.get("risk_budget_explicit") and single_risk not in (None, 0):
        pos_max = min(
            float(profile.get("max_position_pct") or 70.0),
            float(profile.get("risk_budget_pct") or 1.5) / single_risk,
        )
    volume_ratio = _number((context.get("quote") or {}).get("volume_ratio"))
    if volume_ratio is None:
        volume_ratio = _number(((context.get("technical") or {}).get("metrics") or {}).get("volume_5_20"))
    change = _number((context.get("quote") or {}).get("change_pct"))
    pullback_confirmed = bool(
        volume_ratio is not None and volume_ratio <= 0.8 and change is not None and change <= 0
        and price is not None and support is not None and price >= support
    )
    daily_rows = context.get("_completed_daily") or []
    daily_closes = [_bar_number(row, "close", "c") for row in daily_rows]
    support_observable = support is not None and bool(daily_closes)
    support_break_confirmed = bool(
        support_observable and len(daily_closes) >= 2
        and daily_closes[-1] < support and daily_closes[-2] < support
    )
    support_break_pending = bool(
        support_observable and daily_closes[-1] < support and not support_break_confirmed
    )
    breakout_confirmed = (
        bool(daily_closes[-1] > resistance)
        if daily_closes and resistance is not None else None
    )
    previous_low = _bar_number(daily_rows[-1], "low", "l") if daily_rows else None
    ma5 = _number(((context.get("technical") or {}).get("metrics") or {}).get("ma5"))
    moving_protection = (
        max(value for value in (ma5, previous_low) if value is not None)
        if profile.get("effective_scenario") == "after_close" and any(value is not None for value in (ma5, previous_low))
        else None
    )
    return ({
        "reference_price": price,
        "support": support,
        "resistance": resistance,
        "invalidation": invalidation,
        "pressure_zone_1": [resistance, round(resistance + atr * 0.4, 4)] if resistance is not None and atr is not None else ([resistance, resistance] if resistance is not None else None),
        "entry_zone": trade_plan.get("entry_zone"),
        "entry_trigger": trade_plan.get("entry_trigger") or "等待价格与量能形成有效确认",
        "hold_trigger": trade_plan.get("hold_trigger"),
        "stop": trade_plan.get("stop") or (f"结构失效位 {invalidation:.2f}" if invalidation is not None else None),
        "breakout_confirmed": breakout_confirmed,
        "pullback_confirmed": pullback_confirmed,
        "stagnation_confirmed": None,
        "support_break_pending": support_break_pending if support_observable else None,
        "support_break_confirmed": support_break_confirmed if support_observable else None,
        "moving_protection": moving_protection,
        "as_of": context.get("data_as_of") or at.isoformat(timespec="seconds"),
        "expires_at": _expiry(profile, at),
        "rule_version": "stock-ask-execution-v1",
    }, {
        "ratio": round(rr, 4) if rr is not None else None,
        "reward": round(reward, 4) if reward is not None else None,
        "risk": round(risk, 4) if risk is not None else None,
        "single_stock_price_risk_pct": round(single_risk * 100, 4) if single_risk is not None else None,
        "account_risk_pct": round(account_risk_pct, 4) if account_risk_pct is not None else None,
        "position_max_pct": round(pos_max, 2) if pos_max is not None else None,
    })


def _score_label(score: float) -> str:
    return "明显偏多" if score >= 0.80 else "温和偏多" if score >= 0.25 else "明显偏空" if score <= -0.80 else "温和偏空" if score <= -0.25 else "中性/证据冲突"


def _action(grade: str, profile: Mapping[str, Any], risk_reward: Mapping[str, Any]) -> tuple[str, str]:
    held = profile.get("holding_status") == "held"
    if grade == "E":
        return "DATA_INSUFFICIENT", "数据不足"
    if grade == "D":
        return ("REDUCE_100", "建议将该持仓降低100%") if held else ("AVOID", "暂不参与")
    if grade == "C":
        return ("REDUCE", "分批降低持仓") if held else ("AVOID", "暂不参与")
    if grade == "B":
        if not held:
            return "CONDITIONAL_BUY", "满足条件后买入"
        budget_blocked = bool(
            profile.get("risk_budget_explicit")
            and _number(profile.get("position_pct")) is not None
            and _number(risk_reward.get("position_max_pct")) is not None
            and float(profile["position_pct"]) >= float(risk_reward["position_max_pct"])
        )
        if profile.get("allow_add") and not budget_blocked:
            return "CONDITIONAL_ADD", "满足条件后可评估增加持仓"
        return "HOLD", "持有观察，不加仓"
    return ("HOLD", "持有观察，不加仓") if held else ("OBSERVE", "观察，不追高")


def build_analysis(
    card: Mapping[str, Any],
    profile: Mapping[str, Any],
    market_overview: Mapping[str, Any] | None,
    trade_plan: Mapping[str, Any] | None,
    *,
    at: datetime | None = None,
) -> dict:
    current = at or now_cn()
    context = build_context(card, market_overview, at=current)
    buckets = _six_buckets(context)
    weights = HORIZON_WEIGHTS[str(profile.get("horizon") or "SHORT_TERM")]
    score = max(-2.0, min(2.0, sum(float(buckets[key]["score"]) * weight for key, weight in weights.items())))
    confidence = _confidence(context, profile, buckets, card)
    execution, risk_reward = _execution(context, profile, trade_plan or {}, current)

    event_items = (context.get("events") or {}).get("items") or []
    major_negative = any(
        _text(item.get("direction")) in {"major_negative", "重大负面"}
        and _text(item.get("evidence_level")) in {"A", "official", "官方"}
        for item in event_items if isinstance(item, Mapping)
    )
    technical_complete = bool((context.get("technical") or {}).get("complete"))
    if _number(profile.get("current_price")) is None or not technical_complete or confidence["score"] < 40:
        grade = "E"
        grade_reason = "参考价、核心技术结构或数据置信度不足"
    elif major_negative or execution.get("support_break_confirmed") is True:
        grade = "D"
        grade_reason = "官方重大负面或结构破位已经确认"
    elif (
        score <= -0.25
        or execution.get("stagnation_confirmed") is True
        or execution.get("support_break_pending") is True
        or (
            _number((context.get("board") or {}).get("return_5d_pct")) is not None
            and _number((context.get("board") or {}).get("return_5d_pct")) < 0
            and _number(((context.get("technical") or {}).get("metrics") or {}).get("return5_pct")) is not None
            and _number(((context.get("technical") or {}).get("metrics") or {}).get("return5_pct"))
            < _number((context.get("board") or {}).get("return_5d_pct"))
        )
    ):
        grade = "C"
        grade_reason = "综合分偏空、关键支撑跌破待确认或风险确认条件已经触发"
    elif (
        score >= 0.80 and confidence["score"] >= 80
        and _number(risk_reward.get("ratio")) is not None and float(risk_reward["ratio"]) >= 3.0
        and (execution.get("breakout_confirmed") or execution.get("pullback_confirmed"))
    ):
        grade = "B"
        grade_reason = "强度、置信度、风险收益比与确认条件同时满足"
    else:
        grade = "A"
        grade_reason = "已有可用证据，但尚未同时满足进攻或防守升级条件"
    action_code, action = _action(grade, profile, risk_reward)
    context.pop("_completed_daily", None)
    context.pop("_completed_60m", None)
    return {
        "rule_version": RULE_VERSION,
        "profile": dict(profile),
        "holding": {
            "status": profile.get("holding_status"),
            "cost": profile.get("holding_cost"),
            "position_pct": profile.get("position_pct"),
        },
        "reference_price": profile.get("current_price"),
        "context": context,
        "buckets": buckets,
        "weights": weights,
        "score": round(score, 4),
        "score_label": _score_label(score),
        "confidence": confidence,
        "decision_grade": grade,
        "decision_reasons": [grade_reason],
        "risk_reward": risk_reward,
        "execution": execution,
        "action_code": action_code,
        "action": action,
        "pre_risk_action_code": action_code,
        "pre_risk_action": action,
        "model_used": False,
    }


def apply_risk_veto(analysis: Mapping[str, Any], risk_decision: Mapping[str, Any] | None) -> dict:
    result = dict(analysis)
    risk = dict(risk_decision or {})
    result["risk_decision"] = risk
    if risk.get("decision") != "veto" or result.get("action_code") not in {"CONDITIONAL_BUY", "CONDITIONAL_ADD"}:
        return result
    held = (result.get("profile") or {}).get("holding_status") == "held"
    hard_codes = {
        "market_risk_level_5", "liquidity_insufficient",
        "total_position_limit_reached", "projected_total_position_limit",
    }
    reason_codes = set(risk.get("reason_codes") or [])
    if held:
        result["action_code"], result["action"] = "HOLD", "持有观察，不加仓"
    elif reason_codes & hard_codes:
        result["action_code"], result["action"] = "AVOID", "暂不参与"
    else:
        result["action_code"], result["action"] = "OBSERVE", "观察，不追高"
    result["risk_adjustment"] = "RiskGate 只否决新增风险，未改写六桶评分或公司事实"
    return result


def _cell(value: Any) -> str:
    return _text(value).replace("|", "｜").replace("\n", " ") or "—"


def _price(value: Any) -> str | None:
    parsed = _number(value)
    return f"{parsed:.2f}" if parsed is not None else None


def render_markdown(card: Mapping[str, Any]) -> str:
    """把冻结分析直接渲染为 Markdown；不经过模型。"""
    analysis = card.get("stock_ask_analysis") or {}
    profile = analysis.get("profile") or {}
    execution = analysis.get("execution") or {}
    risk_reward = analysis.get("risk_reward") or {}
    confidence = analysis.get("confidence") or {}
    context = analysis.get("context") or {}
    quote = context.get("quote") or card.get("market_quote") or {}
    name = _text(card.get("name") or quote.get("name") or card.get("code") or "个股")
    code = _text(card.get("code"))
    scenario = "盘中" if profile.get("effective_scenario") == "intraday" else "盘后"
    holding_label = "持仓研判" if profile.get("holding_status") == "held" else "机会研判"
    lines = [
        f"# {name}（{code}）｜{scenario}{holding_label}",
        "",
        f"**行情截止：** {_cell(execution.get('as_of'))}　**周期：** {_cell(profile.get('horizon'))}",
        "",
        f"> **{_cell(analysis.get('action'))}**",
        "",
        "## 结论与关键位置",
        "",
        f"当前为 **{analysis.get('decision_grade') or '—'} 级状态**：{_cell((analysis.get('decision_reasons') or [''])[0])}。"
        f"六桶综合分 **{float(analysis.get('score') or 0):+.2f}**（{_cell(analysis.get('score_label'))}），"
        f"数据置信度 **{confidence.get('score', 0)}/100 · {_cell(confidence.get('level'))}**。",
        "",
        "| 位置 | 价格/区间 | 依据 |",
        "|---|---:|---|",
    ]
    level_rows = [
        ("参考价", _price(analysis.get("reference_price")), "本轮画像/行情快照"),
        ("观察线", _price(execution.get("support")), "已核验日K结构支撑"),
        ("压力/突破", _price(execution.get("resistance")), "已核验日K结构压力"),
        ("结构失效位", _price(execution.get("invalidation")), "服务端确定性失效边界"),
    ]
    emitted = 0
    for label, value, basis in level_rows:
        if value is None:
            continue
        lines.append(f"| {label} | {value} | {basis} |")
        emitted += 1
    if not emitted:
        lines.append("| 暂无可用价位 | — | 不补造缺失价格 |")

    lines.extend(["", "## 为什么这样处理", "", "| 证据桶 | 得分 | 覆盖率 | 已核验证据 |", "|---|---:|---:|---|"])
    for bucket_id in ("MV", "FV", "WV", "QV", "TV", "IV"):
        bucket = (analysis.get("buckets") or {}).get(bucket_id) or {}
        lines.append(
            f"| {bucket_id} · {_cell(bucket.get('name'))} | {int(bucket.get('score') or 0):+d} | "
            f"{float(bucket.get('coverage') or 0) * 100:.0f}% | {_cell(bucket.get('summary'))} |"
        )
    lines.extend([
        "",
        "固定方法论只用于解释上述证据，不再次计分，也不代表任何真实人物观点。",
        "",
        "## 执行计划",
        "",
        "| 场景 | 确认条件 | 动作 |",
        "|---|---|---|",
        f"| 强化 | {_cell(execution.get('entry_trigger'))} | {_cell(analysis.get('action'))}；条件未满足前不追价、不新增风险 |",
        f"| 维持 | {_cell(execution.get('hold_trigger') or '现有结构未被证伪')} | 继续观察，不预判单边走势 |",
        f"| 失效 | {_cell(execution.get('stop') or '有效失守结构边界')} | {'按计划降低持仓' if profile.get('holding_status') == 'held' else '保持不参与'} |",
        "",
        f"> 本计划有效至 **{_cell(execution.get('expires_at'))}**；过期后必须重新计算，不能沿用旧价位。",
        "",
        "## 风险边界",
        "",
    ])
    risk_item_count = 0
    if _number(risk_reward.get("single_stock_price_risk_pct")) is not None:
        lines.append(f"- 单股跌到失效位的价格风险：**{float(risk_reward['single_stock_price_risk_pct']):.2f}%**。")
        risk_item_count += 1
    if _number(risk_reward.get("account_risk_pct")) is not None:
        lines.append(f"- 按当前仓位估算的账户风险：**{float(risk_reward['account_risk_pct']):.2f}%**。")
        risk_item_count += 1
    if profile.get("risk_budget_explicit") and _number(risk_reward.get("position_max_pct")) is not None:
        lines.append(f"- 用户已显式设置风险预算；按当前失效位计算的仓位上限为 **{float(risk_reward['position_max_pct']):.2f}%**。")
        risk_item_count += 1
    risk = analysis.get("risk_decision") or card.get("risk_decision") or {}
    risk_reasons = [_text(item) for item in (risk.get("reasons") or []) if _text(item)]
    if risk.get("decision") == "veto":
        lines.append("- RiskGate 已否决新增风险；这不等同于公司存在重大负面，也不自动要求已有持仓退出。")
        risk_item_count += 1
    for reason in risk_reasons[:2]:
        lines.append(f"- 风险闸：{reason}")
        risk_item_count += 1
    if risk_item_count == 0:
        lines.append("- 当前没有足够数据计算精确账户风险，不作估算。")

    missing = list(context.get("missing") or [])
    for block_name in ("board", "valuation", "events", "technical", "market"):
        block = context.get(block_name) or {}
        if block.get("state") in {"missing", "partial"} and _text(block.get("reason")):
            missing.append(_text(block.get("reason")))
    missing = list(dict.fromkeys(item for item in missing if item))
    lines.extend(["", "## 数据说明", ""])
    if missing:
        lines.extend(f"- {item}" for item in missing[:8])
    else:
        lines.append("- 本轮核心证据未发现已登记的缺失或降级项。")
    if profile.get("scenario_adjusted"):
        lines.append("- 用户选择盘中分析，但当前不在连续交易时段，已按盘后规则执行。")
    lines.extend(["", "以上内容基于已核验数据与确定性规则，仅供研究参考，不构成投资建议。"])
    return "\n".join(lines).strip()
