"""产品级交易日状态机与路由生命周期契约。

这个模块只负责“此刻处于什么阶段、用户应去哪里”，不读行情、不生成结论。
所有页面和交付渠道共用 ``data.base`` 中的北京时间与交易日服务，
避免前端、调度器和报告各自重写一套时间判断。
"""
from __future__ import annotations

from datetime import datetime, time as dt_time
from typing import Callable

from .data.base import CN_TZ, is_trade_day, next_trade_day, now_cn, prev_trade_day

MARKET_SESSIONS = ("non_trading", "premarket", "intraday", "tail", "review")

# Phase 1 收敛后的 6 个业务入口 + 2 个底部工具入口。
CANONICAL_ROUTES: tuple[dict, ...] = (
    {"path": "premarket", "label": "盘前报告", "group": "business", "lifecycle": "visible"},
    {"path": "today", "label": "今日决策", "group": "business", "lifecycle": "visible"},
    {"path": "risk", "label": "持仓风控", "group": "business", "lifecycle": "visible"},
    {"path": "tail", "label": "尾盘决策", "group": "business", "lifecycle": "visible"},
    {"path": "review", "label": "复盘与明日", "group": "business", "lifecycle": "visible"},
    {"path": "research", "label": "研究中心", "group": "business", "lifecycle": "visible"},
    {"path": "history", "label": "历史与知识", "group": "utility", "lifecycle": "visible"},
    {"path": "settings", "label": "系统设置", "group": "utility", "lifecycle": "visible"},
)


def _redirect(source: str, target: str, *, tab: str | None = None) -> dict:
    row = {
        "from": source,
        "to": target,
        "lifecycle": "redirect",
        "preserve_query": True,
    }
    if tab:
        row["tab"] = tab
    return row


# 旧 hash 不删、API 不删；前端依此把旧收藏转到新信息架构的对应页签。
ROUTE_REDIRECTS: tuple[dict, ...] = (
    _redirect("premarket_board", "premarket", tab="report"),
    _redirect("morning", "premarket", tab="report"),
    _redirect("cockpit", "today", tab="decision"),
    _redirect("hotboard", "today", tab="hotboard"),
    _redirect("dashboard", "today", tab="decision"),
    _redirect("capital", "today", tab="capital"),
    _redirect("mainforce", "today", tab="mainforce"),
    _redirect("features", "today", tab="mainline"),
    _redirect("senti", "today", tab="news"),
    _redirect("sentiment", "today", tab="decision"),
    _redirect("timing", "today", tab="timing"),
    _redirect("sanshi", "today", tab="decision"),
    _redirect("holdings", "risk", tab="holdings"),
    _redirect("watchlist", "risk", tab="watchlist"),
    _redirect("simulation", "risk", tab="simulation"),
    _redirect("midday", "tail", tab="decision"),
    _redirect("close", "review", tab="close"),
    _redirect("evening", "review", tab="tomorrow"),
    _redirect("trend", "review", tab="tomorrow"),
    _redirect("recommend", "review", tab="close"),
    _redirect("chat", "research", tab="ask"),
    _redirect("diagnosis", "research", tab="diagnosis"),
    _redirect("company", "research", tab="company"),
    _redirect("chain", "research", tab="chain"),
    _redirect("restructuring", "research", tab="restructuring"),
    _redirect("screener", "research", tab="screener"),
    _redirect("quant", "research", tab="quant-pick"),
    _redirect("quant_pick", "research", tab="quant-pick"),
    _redirect("forecast", "research", tab="events"),
    _redirect("institutional", "research", tab="company"),
    _redirect("overseas", "research", tab="overseas"),
    _redirect("macro", "research", tab="macro"),
    _redirect("market_logic", "research", tab="logic"),
    _redirect("diy", "research", tab="ask"),
    _redirect("deriv", "research", tab="overseas"),
    _redirect("reports", "history", tab="reports"),
    _redirect("archive", "review", tab="close"),
    _redirect("knowledge", "history", tab="knowledge"),
    _redirect("datasources", "settings", tab="system"),
)

SESSION_ROUTE = {
    "non_trading": "review",
    "premarket": "premarket",
    "intraday": "today",
    "tail": "tail",
    "review": "review",
}


def market_session(at: datetime, *, trading_day: bool) -> str:
    """按北京时间返回统一交易阶段。

    09:25 与 14:45 为产品决策切换点，15:00 之后进入复盘；午间休市
    仍属于盘中验证阶段，不额外造一个前端状态。
    """
    local = at.astimezone(CN_TZ) if at.tzinfo else at.replace(tzinfo=CN_TZ)
    if not trading_day:
        return "non_trading"
    t = local.time().replace(tzinfo=None)
    if t < dt_time(9, 25):
        return "premarket"
    if t < dt_time(14, 45):
        return "intraday"
    if t < dt_time(15, 0):
        return "tail"
    return "review"


def build_market_context(
    at: datetime | None = None,
    *,
    trade_day_checker: Callable | None = None,
) -> dict:
    """一次性生成 /status 使用的交易日事实，防止同一请求多次取时。"""
    current = at or now_cn()
    current = current.astimezone(CN_TZ) if current.tzinfo else current.replace(tzinfo=CN_TZ)
    checker = trade_day_checker or is_trade_day
    trading = bool(checker(current.date()))
    session = market_session(current, trading_day=trading)
    route = SESSION_ROUTE[session]
    current_day = current.date().isoformat()
    previous = prev_trade_day(current.date())
    following = next_trade_day(current.date())
    return {
        "timezone": "Asia/Shanghai",
        "now": current.isoformat(timespec="seconds"),
        "date": current_day,
        "is_trade_day": trading,
        "trade_day": current_day if trading else following,
        "previous_trade_day": previous,
        "next_trade_day": following,
        "market_session": session,
        "recommended_route": route,
        "recommended_hash": f"#/{route}",
    }


def route_lifecycle_contract() -> dict:
    """可供前端一次性注册的路由契约；返回副本，调用方不能污染常量。"""
    canonical = [dict(row) for row in CANONICAL_ROUTES]
    redirects = [dict(row) for row in ROUTE_REDIRECTS]
    return {
        "schema_version": "route-lifecycle-v1",
        "allowed_lifecycle": ["visible", "hidden", "retired", "redirect"],
        "canonical": canonical,
        "redirects": redirects,
    }
