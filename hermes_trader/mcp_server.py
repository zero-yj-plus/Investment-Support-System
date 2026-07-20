"""Hermes Trader MCP Server —— 平台只读数据/决策头。

把市场、资金、今日决策与研究增强的真实数据路径暴露为 MCP 工具，供 Agent
用自然语言调用。Web UI 与 MCP 共用 ``data`` / ``analysis`` 层，不在 MCP 中另造口径。
本服务只新增只读查询能力；不暴露设置修改、对外发送、推荐写入或交易执行。

启动（独立进程，stdio 传输，由 Hermes Agent 拉起）：
    python -m hermes_trader.mcp_server --config clients/<id>/config.yaml
Hermes Agent 侧 mcpServers 配置示例：
    {"mcpServers": {"hermes-trader": {
        "command": "/abs/.venv/bin/python",
        "args": ["-m", "hermes_trader.mcp_server", "--config", "/abs/config.yaml"]}}}
"""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hermes-trader")
_runtime_config = None
_runtime_store = None
_write_tools_enabled = False
_full_product_access = False
_product_api_base = ""

_REPORT_TYPES = frozenset({"morning", "midday", "tail", "close", "evening", "trend"})
_FORBIDDEN_TOOL_NAMES = frozenset({
    "put_settings", "delete_connection", "push_report", "send_message",
    "create_recommendation", "approve_recommendation", "execute_trade",
})
_JOB_PUBLIC_FIELDS = (
    "last_run", "last_finished", "last_ok", "last_status", "last_error_code",
    "last_generated_ok", "last_push_ok", "last_push_time", "last_duration_ms",
    "calendar_status",
)
_RECEIPT_PUBLIC_FIELDS = (
    "schema_version", "receipt_id", "delivery_id", "content_id", "content_hash",
    "channel", "attempt", "status", "message_id", "error_code", "plan_id",
    "version", "trigger", "attempted_at", "sent_at", "ack_at", "recorded_at",
)


def _tool_error(tool: str, exc: Exception) -> dict:
    """统一工具降级契约。

    不回传原始异常文本，避免外部请求 URL、token 或运行路径通过 MCP 泄露。
    调用方仍可以依据 ``error_code`` / ``error_type`` 区分失败并选择备用路径。
    """
    return {
        "ok": False,
        "tool": tool,
        "error_code": "tool_unavailable",
        "error": f"{tool} 暂不可用，请检查对应数据源状态",
        "error_type": type(exc).__name__,
        "missing": [f"{tool} 数据"],
    }


def _safe(tool: str, fn: Callable[[], Any]):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- MCP 边界必须结构化降级
        return _tool_error(tool, exc)


async def _safe_async(tool: str, fn: Callable[[], Awaitable[Any]]):
    try:
        return await fn()
    except Exception as exc:  # noqa: BLE001 -- MCP 边界必须结构化降级
        return _tool_error(tool, exc)


def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(parsed, high))


def _trade_day(value: Any = "") -> str | None:
    """Return a strict ISO trade-day string without guessing calendar state."""
    raw = str(value or "").strip()
    if not raw:
        if _runtime_store is not None:
            raw = str(_runtime_store.today())
        else:
            from .data import base

            raw = base.now_cn().date().isoformat()
    try:
        normalized = date.fromisoformat(raw).isoformat()
    except ValueError:
        return None
    return normalized if len(raw) == 10 and normalized == raw else None


def _runtime_required(label: str) -> dict:
    return {
        "ok": False,
        "error_code": "runtime_not_booted",
        "error": f"MCP 尚未加载客户{label}",
        "missing": [f"客户{label}"],
    }


def _ensure_news_observation_reader() -> bool:
    """Attach to an existing ledger without creating observations or fetching data."""
    from .data import news_observation

    if news_observation.available():
        return True
    root = None
    if _runtime_store is not None:
        root = Path(_runtime_store.root)
    elif _runtime_config is not None:
        root = Path(_runtime_config.data_dir)
    # NewsObservationStore.__init__ creates/chmods its directory.  Build the
    # same reader shape without calling __init__, so an MCP query cannot even
    # change filesystem metadata.  The methods themselves only read JSONL.
    ledger_root = (root / "news_events") if root is not None else None
    if ledger_root is None or not ledger_root.is_dir():
        return False
    reader = object.__new__(news_observation.NewsObservationStore)
    reader.root = ledger_root
    reader.events_path = ledger_root / "events.v1.jsonl"
    reader.observations_path = ledger_root / "observations.v1.jsonl"
    news_observation._store = reader  # noqa: SLF001 -- attach existing read-only ledger
    return True


# ---- 资金持续追踪（核心增量功能）----

@mcp.tool()
def sector_capital_ranking() -> list:
    """最新一次快照的 top 行业板块主力资金排名（main_net 单位=元，降序）。"""
    from .data import capital_tracker as ct
    return _safe("板块资金排名", ct.latest_sectors)


@mcp.tool()
def sector_capital_trend(name: str, days: int = 5) -> list:
    """某行业板块主力资金的时间序列（近 days 天），用于判断资金是否持续流入/流出。"""
    from .data import capital_tracker as ct
    return _safe("板块资金趋势", lambda: ct.sector_history(name, days))


@mcp.tool()
def hot_stocks_capital() -> list:
    """最新一次快照的热门股（当日主力净流入 top）资金排名。"""
    from .data import capital_tracker as ct
    return _safe("热门股资金排名", ct.latest_hot_stocks)


@mcp.tool()
def stock_capital_trend(code: str, days: int = 5) -> list:
    """某股主力资金的时间序列（近 days 天，code 为 6 位代码）。"""
    from .data import capital_tracker as ct
    return _safe("个股资金趋势", lambda: ct.hot_stock_history(code, days))


@mcp.tool()
def capital_trends(days: int = 1) -> dict:
    """资金追踪综合：当日(days=1)或近 days 天 top 板块 + 热门股的【日内主力净流入时序】，
    一次取全（含每标的最新净额 + 逐快照 series），用于"今天哪些板块资金持续流入"类问题。"""
    from .data import capital_tracker as ct
    return _safe("资金趋势综合", lambda: ct.trends(days))


@mcp.tool()
def capital_snapshot() -> dict:
    """显式可选的资金快照补采。默认 MCP 配置禁止写入；只有客户配置
    ``mcp.allow_write_tools=true`` 时才会采集并写入客户时序库。"""
    if not _write_tools_enabled:
        return {
            "ok": False,
            "error_code": "write_capability_disabled",
            "error": "MCP 写入能力未启用；资金快照由平台定时任务维护",
            "missing": [],
        }
    from .data import capital_tracker as ct
    return _safe("资金快照补采", ct.snapshot)


# ---- 大盘 / 个股（复用现有引擎）----

@mcp.tool()
def market_overview() -> dict:
    """大盘概览：指数、涨跌家数、涨停/跌停/连板高度/炸板率、两市成交额。"""
    from .data import market
    return _safe("大盘概览", market.market_overview)


@mcp.tool()
def stock_detail(code: str) -> dict:
    """个股详情：实时盘口、换手/量比/PE/PB/市值、资金流、行业、日K、集合竞价（code 为 6 位）。"""
    from .data import stock
    return _safe("个股详情", lambda: stock.stock_detail(code))


@mcp.tool()
def limit_up_ladder() -> dict:
    """今日涨停连板梯队：按连板数排序的涨停个股，含连板数/封单额/换手/首封时间（及涨停原因，视数据源）。"""
    from .data import market
    return _safe("涨停连板梯队", market.market_limitup)


@mcp.tool()
def sector_heat() -> dict:
    """板块热力：行业/概念板块涨跌幅、领涨股、主力资金净额。"""
    from .data import market
    return _safe("板块热力", market.market_sectors)


@mcp.tool()
def market_news() -> dict:
    """多市场资讯：A股财经快讯 + 美股三大指数/科技股 + 港股指数。"""
    from .data import news
    return _safe("多市场资讯", news.multi_market_news)


@mcp.tool()
def company_announcements(code: str, days: int = 90, limit: int = 20) -> dict:
    """官方公告证据：巨潮官网页面查询 + 公司所属交易所独立交叉确认。

    返回公告日期、标题、原文链接、来源确认与证据时点；不下载或
    代理 PDF 全文。这是官网页面 adapter，不冒充深证信付费 Web API。
    """
    normalized = str(code or "").strip()
    if len(normalized) != 6 or not normalized.isdigit() or not normalized.startswith(("0", "3", "6")):
        return {
            "ok": False,
            "error_code": "invalid_stock_code",
            "error": "code 应为 0/3/6 开头的 6 位沪深 A 股代码",
            "missing": ["合法股票代码"],
        }

    from .data import base, official_disclosure

    bounded_days = _bounded_int(days, default=90, low=1, high=366)
    bounded_limit = _bounded_int(limit, default=20, low=1, high=50)
    end = base.now_cn().date()
    start = end - timedelta(days=bounded_days)
    cache_path = None
    if _runtime_config is not None:
        from pathlib import Path

        cache_path = str(
            Path(_runtime_config.data_dir)
            / "official-disclosures"
            / f"{normalized}.json"
        )
    return _safe(
        "官方公告证据",
        lambda: official_disclosure.cached_official_disclosures(
            normalized,
            start.isoformat(),
            end.isoformat(),
            bounded_limit,
            False,
            1,
            cache_path,
        ),
    )


@mcp.tool()
def company_management_interactions(code: str, days: int = 90, limit: int = 6) -> dict:
    """深市互动易的投资者提问与管理层回复（Tier 3 研究线索）。

    该内容不是法定公告，不参与日内评分；未回复的投资者提问不能被称为
    管理层表态。当前不支持沪市/北交所互动平台。
    """
    normalized = str(code or "").strip()
    if len(normalized) != 6 or not normalized.isdigit():
        return {
            "ok": False,
            "error_code": "invalid_stock_code",
            "error": "code 应为 6 位 A 股代码",
            "missing": ["合法股票代码"],
        }
    if not normalized.startswith(("0", "3")):
        return {
            "ok": True,
            "code": normalized,
            "state": "not_supported",
            "platform": "深交所互动易",
            "is_formal_disclosure": False,
            "items": [],
            "degraded_reasons": ["当前仅覆盖深市互动易；沪市/北交所互动平台尚未接入"],
        }
    if _runtime_config is None:
        return {
            "ok": False,
            "error_code": "runtime_not_booted",
            "error": "MCP 尚未加载客户互动问答缓存目录",
            "missing": ["客户数据目录"],
        }
    from .data import base, official_interaction

    bounded_days = _bounded_int(days, default=90, low=1, high=366)
    bounded_limit = _bounded_int(limit, default=6, low=1, high=20)
    end = base.now_cn().date()
    start = end - timedelta(days=bounded_days)
    return _safe(
        "管理层互动回复",
        lambda: official_interaction.cached_official_interactions(
            normalized,
            start.isoformat(),
            end.isoformat(),
            bounded_limit,
            str(_runtime_config.data_dir),
        ),
    )


@mcp.tool()
def official_longhu(trade_date: str) -> dict:
    """指定交易日的沪深交易所龙虎榜官方快照（只读落盘缓存）。

    ``trade_date`` 必须为 ``YYYY-MM-DD``。该工具不会在 Agent 请求链
    访问交易所官网，且不会用其它交易日快照回填。
    """
    raw = str(trade_date or "").strip()
    try:
        normalized = date.fromisoformat(raw).isoformat()
    except ValueError:
        normalized = ""
    if len(raw) != 10 or normalized != raw:
        return {
            "ok": False,
            "error_code": "invalid_date",
            "error": "trade_date 应为有效的 YYYY-MM-DD 日期",
            "missing": ["合法交易日期"],
        }
    if _runtime_config is None:
        return {
            "ok": False,
            "error_code": "runtime_not_booted",
            "error": "MCP 尚未加载客户官方快照目录",
            "missing": ["客户快照目录"],
        }

    from .data import official_longhu_source

    def _load():
        snapshot = official_longhu_source.load_snapshot(normalized)
        if snapshot is None:
            return {
                "ok": False,
                "state": "missing",
                "error_code": "official_snapshot_not_cached",
                "trade_date": normalized,
                "source": "official_snapshot_cache",
                "cache_only": True,
                "network_requested": False,
                "events": [],
                "missing": ["指定交易日的官方龙虎榜快照尚未落盘"],
            }
        result = dict(snapshot)
        result.update({
            "source": "official_snapshot_cache",
            "cache_only": True,
            "network_requested": False,
        })
        return result

    return _safe("官方龙虎榜快照", _load)


# ---- 平台核心只读能力（复用 Web 真实数据/分析路径）----

async def _locked_pulse() -> dict:
    if _runtime_store is None:
        return {
            "ok": False,
            "error_code": "runtime_not_booted",
            "error": "MCP 尚未加载客户数据定格",
            "missing": ["客户数据定格"],
        }
    from .analysis import cockpit as cockpit_mod, score_lock

    return await score_lock.locked(
        _runtime_store.root,
        "pulse",
        lambda prev: asyncio.to_thread(cockpit_mod.pulse_core, _runtime_store, prev),
    )


@mcp.tool()
async def today_decision() -> dict:
    """今日决策：系统性风险级别、市场状态、仓位上限、允许/禁止动作与市场宽度。

    调用今日决策页的确定性 ``pulse_core`` + ``cockpit`` 引擎，不调用 AI、
    不生成或猜测数字。
    """
    if _runtime_store is None:
        return await _locked_pulse()
    from .analysis import cockpit as cockpit_mod, score_lock

    async def _load():
        pulse = await _locked_pulse()
        if pulse.get("ok") is False:
            return pulse

        async def _compute(_prev):
            return await asyncio.to_thread(cockpit_mod.cockpit, _runtime_store, pulse)

        result = await score_lock.locked(_runtime_store.root, "cockpit", _compute)
        result = dict(result or {})
        result["pulse"] = pulse
        return result

    return await _safe_async("今日决策", _load)


@mcp.tool()
async def systemic_risk() -> dict:
    """系统性风险：1—5 级风险与大盘技术/资金/情绪/消息/海外五维证据，
    包含升级、降级观察条件。等级由确定性规则引擎计算，不由模型定级。"""
    if _runtime_store is None:
        return await _locked_pulse()
    from .analysis import cockpit as cockpit_mod, score_lock

    async def _load():
        pulse = await _locked_pulse()
        if pulse.get("ok") is False:
            return pulse
        return await score_lock.locked(
            _runtime_store.root,
            "sysrisk_card_v1",
            lambda _prev: asyncio.to_thread(cockpit_mod.sysrisk, _runtime_store, pulse),
        )

    return await _safe_async("系统性风险", _load)


@mcp.tool()
async def market_sentiment() -> dict:
    """市场情绪真值：情绪温度、阶段、涨跌家数/涨跌停结构、风险指令与缺失因子。
    与今日决策页的 pulse 公式同源，不另造情绪分。"""
    return await _safe_async("市场情绪", _locked_pulse)


@mcp.tool()
async def today_mainlines(limit: int = 5) -> dict:
    """今日行业主线：主线/次主线/活口/情绪链及证据覆盖。

    综合行业容量、相对强度、上涨宽度和涨停梯队；主力净流入不参与主线评分。
    只返回当日截面，不写时间轴或历史回放。
    """
    from .analysis import mainline_runtime

    bounded = _bounded_int(limit, default=5, low=1, high=30)
    return await _safe_async("今日行业主线", lambda: mainline_runtime.build_current_response(bounded))


@mcp.tool()
def multi_period_performance(limit: int = 12) -> dict:
    """多周期强弱：当日强势标的 1/3/5/20 日真实累计涨幅与覆盖状态。
    周期日线不足时返回 ``None`` + ``missing``，不用0补值。"""
    from .data import market

    bounded = _bounded_int(limit, default=12, low=1, high=20)
    return _safe("多周期强弱", lambda: market.market_multiperiod(bounded))


@mcp.tool()
def sector_members(sector: str = "", stock_code: str = "", limit: int = 200) -> dict:
    """查询行业板块成分或个股所属行业。

    - ``sector``：行业名或 TDX 881xxx 板块代码，返回最多 ``limit`` 个成分；
    - ``stock_code``：6 位 A 股代码，通过 TDX relation/sector_members 返回精确行业映射。

    两者至少填一个；同时填写时优先查个股。
    """
    from .data import tdx_api_source as tdx_api

    bounded = _bounded_int(limit, default=200, low=1, high=500)
    code = str(stock_code or "").strip()
    if code:
        if len(code) != 6 or not code.isdigit():
            return {
                "ok": False,
                "error_code": "invalid_stock_code",
                "error": "stock_code 应为 6 位 A 股代码",
                "missing": ["合法股票代码"],
            }

        def _by_stock():
            relation = tdx_api.industry_for_codes([code]).get(code)
            return {
                "ok": bool(relation),
                "query": {"stock_code": code},
                "mapping": relation,
                "data_state": "complete" if relation else "missing",
                "missing": [] if relation else ["该股行业映射"],
            }

        return _safe("个股行业映射", _by_stock)

    query = str(sector or "").strip()
    if not query:
        return {
            "ok": False,
            "error_code": "query_required",
            "error": "请填写 sector 或 stock_code",
            "missing": ["板块或股票查询条件"],
        }

    def _by_sector():
        catalog = tdx_api.sector_catalog() or {}
        needle = query.casefold()
        industries = [
            item for item in catalog.values()
            if item.get("kind") == "industry"
        ]
        exact = [
            item for item in industries
            if needle in {
                str(item.get("name") or "").casefold(),
                str(item.get("code") or "").casefold(),
                str(item.get("code") or "")[:6].casefold(),
            }
        ]
        candidates = exact or [
            item for item in industries
            if needle in str(item.get("name") or "").casefold()
        ]
        if not candidates:
            return {
                "ok": False,
                "query": {"sector": query},
                "error_code": "sector_not_found",
                "error": "未在 TDX 行业目录中找到该板块",
                "data_state": "missing",
                "missing": ["板块目录匹配"],
            }
        if len(candidates) > 1 and not exact:
            return {
                "ok": False,
                "query": {"sector": query},
                "error_code": "sector_ambiguous",
                "error": "板块名称匹配到多个行业，请改用完整名称或 881xxx 代码",
                "candidates": [
                    {"code": item.get("code"), "name": item.get("name")}
                    for item in candidates[:20]
                ],
            }
        selected = candidates[0]
        memberships = tdx_api.sector_memberships() or {}
        codes = list((memberships.get("by_sector") or {}).get(selected.get("code"), ()))
        names = tdx_api.stock_universe() or {}
        rows = [{"code": item, "name": names.get(item, "")} for item in codes[:bounded]]
        return {
            "ok": bool(codes),
            "query": {"sector": query},
            "sector": {"code": selected.get("code"), "name": selected.get("name")},
            "member_count": len(codes),
            "returned": len(rows),
            "members": rows,
            "data_date": memberships.get("data_date"),
            "data_state": "degraded" if len(codes) > len(rows) else ("complete" if codes else "missing"),
            "missing": [] if codes else ["板块成分关系"],
        }

    return _safe("板块成分", _by_sector)


@mcp.tool()
def trading_calendar(day: str = "") -> dict:
    """A 股交易日历：指定日期是否开市、上一/下一交易日与日历质量。
    ``day`` 留空为当前北京日期，或传 ``YYYY-MM-DD``。日历不可用时会明示 unknown，
    不把工作日偷换成已确认开市。"""
    from .data import base

    raw = str(day or "").strip()
    try:
        target = date.fromisoformat(raw) if raw else base.now_cn().date()
    except ValueError:
        return {
            "ok": False,
            "error_code": "invalid_date",
            "error": "day 应为 YYYY-MM-DD",
            "missing": ["合法日期"],
        }

    def _load():
        status = base.trade_day_status(target)
        return {
            "ok": status.get("status") != "unknown",
            "date": target.isoformat(),
            "status": status,
            "previous_trade_day": base.prev_trade_day(target),
            "next_trade_day": base.next_trade_day(target),
            "data_state": "complete" if status.get("status") != "unknown" else "missing",
            "missing": [] if status.get("status") != "unknown" else ["交易所日历"],
        }

    return _safe("交易日历", _load)


@mcp.tool()
def data_health() -> dict:
    """平台数据健康：字段路由契约、各数据源最近调用状态、TDX 核心数据集链路与
    全 A 名录状态。只返回脱敏状态，不返回数据源地址、key 或配置原文。"""
    def _load():
        from .data import free_source, sources
        from .data import tdx_api_source as tdx_api

        tdx_health = tdx_api.service_health()
        report = sources.build_report({"tdx_api": tdx_health})
        report["tdx_api"] = tdx_health
        report["universe"] = free_source.stock_universe_status()
        return report

    return _safe("平台数据健康", _load)


@mcp.tool()
def stock_research_enrichment(code: str) -> dict:
    """个股盘后研究增强：机构一致预期（目标价/EPS/净利/营收/ROE）与十大股东结构。

    该低频数据明确不参与日内主线/风险评分；GPJY 语义未验真的字段继续封锁。
    """
    from .data import tdx_api_source as tdx_api

    normalized = str(code or "").strip()
    if len(normalized) != 6 or not normalized.isdigit():
        return {
            "ok": False,
            "error_code": "invalid_stock_code",
            "error": "code 应为 6 位 A 股代码",
            "missing": ["合法股票代码"],
        }
    return _safe("个股研究增强", lambda: tdx_api.after_hours_research(normalized))


# ---- 平台审计与交付只读能力 ----

@mcp.tool()
def mcp_capabilities() -> dict:
    """MCP 权限边界与已注册工具。

    默认只读；设置修改、对外发送、推荐写入与交易执行不会注册为
    MCP 工具。历史兼容的 ``capital_snapshot`` 是唯一可选维护写入，
    且必须在客户配置中显式开启；它不改变业务设置或交易状态。
    """
    registered = set(mcp._tool_manager._tools)  # noqa: SLF001 -- 返回实际注册契约
    if _full_product_access:
        return {
            "ok": True,
            "schema_version": "mcp-capabilities.v2",
            "default_mode": "customer_full_product_access",
            "tools": sorted(registered),
            "product_api_methods": ["GET", "POST", "PUT", "PATCH", "DELETE"],
            "product_api_scope": "/api/* on this customer product only",
            "host_terminal": "not_exposed",
            "host_filesystem": "not_exposed",
            "trade_execution": "available only when the customer product API implements it",
        }
    return {
        "ok": True,
        "schema_version": "mcp-capabilities.v1",
        "default_mode": "read_only",
        "read_only_tools": sorted(registered - {"capital_snapshot"}),
        "optional_maintenance_writes": [{
            "tool": "capital_snapshot",
            "enabled": bool(_write_tools_enabled),
            "scope": "append_market_capital_snapshot",
            "default": "disabled",
        }],
        "forbidden_capabilities": {
            "settings_mutation": "not_exposed",
            "external_delivery": "not_exposed",
            "recommendation_mutation": "not_exposed",
            "trade_execution": "not_exposed",
        },
        "forbidden_tool_names_registered": sorted(registered & _FORBIDDEN_TOOL_NAMES),
    }


@mcp.tool()
def product_api_request(method: str, path: str, body: dict | None = None) -> dict:
    """调用本客户产品的完整 HTTP API。

    客户显式开启 ``mcp.full_product_access`` 后，可使用 GET/POST/PUT/PATCH/DELETE
    访问该产品自身的 ``/api/*``。这覆盖网页已有的查询、生成、刷新、设置、发送
    与维护能力；目标主机固定来自客户配置，不能借此访问任意 URL 或宿主机终端。
    """
    if not _full_product_access or not _product_api_base:
        return {
            "ok": False,
            "error_code": "full_product_access_disabled",
            "error": "客户未启用完整产品 MCP",
            "missing": [],
        }
    normalized_method = str(method or "GET").strip().upper()
    if normalized_method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return {
            "ok": False,
            "error_code": "method_not_allowed",
            "error": "method 仅支持 GET/POST/PUT/PATCH/DELETE",
            "missing": [],
        }
    raw_path = str(path or "").strip()
    parsed = urllib_parse.urlsplit(raw_path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or not parsed.path.startswith("/api/")
        or "/../" in f"{parsed.path}/"
    ):
        return {
            "ok": False,
            "error_code": "invalid_product_api_path",
            "error": "path 必须是本客户产品内的 /api/* 路径",
            "missing": [],
        }

    payload = None
    headers = {"Accept": "application/json"}
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib_request.Request(
        f"{_product_api_base}{raw_path}",
        data=payload,
        headers=headers,
        method=normalized_method,
    )
    try:
        with urllib_request.urlopen(req, timeout=120) as response:
            raw = response.read(5 * 1024 * 1024 + 1)
            if len(raw) > 5 * 1024 * 1024:
                return {
                    "ok": False,
                    "error_code": "response_too_large",
                    "error": "产品 API 响应超过 5MB，请缩小查询范围",
                    "status": response.status,
                }
            content_type = str(response.headers.get("Content-Type") or "")
            if "json" in content_type.lower():
                result = json.loads(raw.decode("utf-8")) if raw else None
            else:
                result = raw.decode("utf-8", "replace")
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "method": normalized_method,
                "path": raw_path,
                "result": result,
            }
    except urllib_error.HTTPError as exc:
        raw = exc.read(512 * 1024)
        try:
            detail = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = raw.decode("utf-8", "replace")[:4000]
        return {
            "ok": False,
            "status": exc.code,
            "method": normalized_method,
            "path": raw_path,
            "error_code": "product_api_rejected",
            "detail": detail,
        }
    except Exception as exc:  # noqa: BLE001 -- MCP 边界结构化降级
        return _tool_error("完整产品 API", exc)


@mcp.tool()
def news_event_history(limit: int = 100) -> dict:
    """A 股新闻事件与盘面观察历史。

    只读已存在的 append-only 账本，不刷新新闻、不请求行情，也不将同步
    涨跌解释为新闻因果。
    """
    from .data import news_observation

    bounded = _bounded_int(limit, default=100, low=1, high=500)
    if not _ensure_news_observation_reader():
        return {
            "ok": False,
            "error_code": "observation_ledger_not_found",
            "error": "尚无已落盘的新闻事件观察账本",
            "items": [],
            "ledger_only": True,
            "network_requested": False,
            "missing": ["新闻事件观察账本"],
        }

    def _load():
        result = dict(news_observation.history(bounded) or {})
        result.update({"ledger_only": True, "network_requested": False, "limit": bounded})
        return result

    return _safe("新闻事件观察历史", _load)


@mcp.tool()
def report_lineage(report_type: str = "close", trade_day: str = "") -> dict:
    """日报的交易日、内容哈希、版本、上游报告与证据 lineage。

    只返回审计元数据，不返回报告正文或本地文件路径。
    """
    if _runtime_store is None:
        return _runtime_required("报告库")
    normalized_type = str(report_type or "").strip().lower()
    if normalized_type not in _REPORT_TYPES:
        return {
            "ok": False,
            "error_code": "invalid_report_type",
            "error": "report_type 不受支持",
            "allowed": sorted(_REPORT_TYPES),
            "missing": ["合法报告类型"],
        }
    normalized_day = _trade_day(trade_day)
    if not normalized_day:
        return {
            "ok": False,
            "error_code": "invalid_date",
            "error": "trade_day 应为有效的 YYYY-MM-DD 日期",
            "missing": ["合法交易日期"],
        }

    from .analysis import report_lineage as lineage_mod

    def _load():
        rec = _runtime_store.get_report(normalized_type, normalized_day)
        if not rec:
            return {
                "ok": False,
                "state": "missing",
                "error_code": "report_not_found",
                "report_type": normalized_type,
                "trade_day": normalized_day,
                "missing": ["指定日报告定稿"],
            }
        return {
            "ok": True,
            "state": "complete",
            "report": lineage_mod.report_ref(rec),
            "input_lineage": rec.get("input_lineage"),
            "claim_validation": rec.get("claim_validation"),
            "advice_governance": rec.get("advice_governance"),
            "input_snapshot": {
                "captured": bool(rec.get("input_context_hash")),
                "content_hash": rec.get("input_context_hash"),
            },
            "delivery_marked_pushed": bool(rec.get("pushed")),
            "content_included": False,
        }

    return _safe("报告 lineage", _load)


@mcp.tool()
def daily_review_evidence(trade_day: str = "", limit: int = 100) -> dict:
    """指定交易日的收盘复盘证据索引与新闻事件观察对账。

    数据来自已定稿 ``close`` 报告和观察账本；不回放行情、不评判新闻
    利多利空，也不为缺失证据补值。
    """
    if _runtime_store is None:
        return _runtime_required("复盘证据库")
    normalized_day = _trade_day(trade_day)
    if not normalized_day:
        return {
            "ok": False,
            "error_code": "invalid_date",
            "error": "trade_day 应为有效的 YYYY-MM-DD 日期",
            "missing": ["合法交易日期"],
        }
    bounded = _bounded_int(limit, default=100, low=1, high=500)

    from .analysis import report_lineage as lineage_mod
    from .data import news_observation

    def _load():
        close = _runtime_store.get_report("close", normalized_day)
        observation_ready = _ensure_news_observation_reader()
        reconciliation = (
            news_observation.daily_reconciliation(normalized_day, bounded)
            if observation_ready else {
                "ok": False,
                "trade_day": normalized_day,
                "reason_code": "observation_ledger_not_found",
                "items": [],
                "score_weight": 0,
                "causality": "not_inferred",
            }
        )
        missing = []
        if not close:
            missing.append("收盘复盘定稿")
        if not observation_ready:
            missing.append("新闻事件观察账本")
        report_evidence = None
        if close:
            report_evidence = {
                "report": lineage_mod.report_ref(close),
                "input_lineage": close.get("input_lineage"),
                "claim_validation": close.get("claim_validation"),
                "advice_governance": close.get("advice_governance"),
                "input_context_hash": close.get("input_context_hash"),
                "content_included": False,
            }
        return {
            "ok": bool(close or reconciliation.get("ok")),
            "state": "complete" if not missing else ("degraded" if close or observation_ready else "missing"),
            "trade_day": normalized_day,
            "report_evidence": report_evidence,
            "news_event_reconciliation": reconciliation,
            "ledger_only": True,
            "network_requested": False,
            "missing": missing,
        }

    return _safe("每日复盘证据", _load)


@mcp.tool()
def task_status() -> dict:
    """定时任务最近运行/生成/发送状态。

    MCP 独立进程没有 Web 进程的 APScheduler 实例，因此只返回真实持久化
    ``jobs_state`` 和配置时点，不伪造下次运行时间或已注册状态。
    """
    if _runtime_store is None:
        return _runtime_required("任务状态库")

    from .jobs.scheduler import JOB_NAMES

    def _load():
        stored = _runtime_store.get_job_states() or {}
        schedule = getattr(_runtime_config, "schedule", {}) if _runtime_config is not None else {}
        schedule = schedule if isinstance(schedule, dict) else {}
        job_ids = list(JOB_NAMES)
        job_ids.extend(sorted(str(key) for key in stored if str(key) not in JOB_NAMES))
        rows = []
        for job_id in job_ids:
            raw = stored.get(job_id, {})
            raw = raw if isinstance(raw, dict) else {}
            kind = "core" if job_id in JOB_NAMES else ("extension" if job_id.startswith("ext_") else "system")
            row = {
                "job_id": job_id,
                "label": JOB_NAMES.get(job_id, job_id),
                "kind": kind,
                "configured_time": schedule.get(job_id),
                "registration_state": "unknown_in_mcp_process",
                "next_run_at": None,
            }
            row.update({key: raw.get(key) for key in _JOB_PUBLIC_FIELDS})
            row["last_status"] = str(raw.get("last_status") or "never_run")
            rows.append(row)
        return {
            "ok": True,
            "schema_version": "mcp-task-status.v1",
            "source": "persisted_jobs_state",
            "scheduler_registration_available": False,
            "capabilities": {"manual_run": False, "manual_retry": False},
            "summary": {
                "total": len(rows),
                "running": sum(row["last_status"] == "running" for row in rows),
                "needs_attention": sum(row["last_status"] in {"error", "warn", "blocked"} for row in rows),
                "never_run": sum(row["last_status"] == "never_run" for row in rows),
            },
            "jobs": rows,
        }

    return _safe("任务状态", _load)


@mcp.tool()
def delivery_receipts(limit: int = 100, status: str = "") -> dict:
    """交付回执元数据（最新在前）。

    只读 append-only 回执账本，不返回报告正文、推送目标或认证信息，
    也不提供重试、确认或发送操作。
    """
    if _runtime_store is None:
        return _runtime_required("交付回执库")

    from .delivery import DELIVERY_STATUSES, MAX_RECEIPT_LIMIT, DeliveryReceiptStore

    bounded = _bounded_int(limit, default=100, low=1, high=MAX_RECEIPT_LIMIT)
    normalized_status = str(status or "").strip().lower()
    if normalized_status and normalized_status not in DELIVERY_STATUSES:
        return {
            "ok": False,
            "error_code": "invalid_delivery_status",
            "error": "status 不受支持",
            "allowed": sorted(DELIVERY_STATUSES),
            "missing": ["合法交付状态"],
        }

    def _load():
        scan_limit = MAX_RECEIPT_LIMIT if normalized_status else bounded
        rows, total = DeliveryReceiptStore(_runtime_store).receipts(scan_limit)
        if normalized_status:
            rows = [row for row in rows if str(row.get("status") or "").lower() == normalized_status]
        selected = rows[:bounded]
        public_rows = [
            {key: row.get(key) for key in _RECEIPT_PUBLIC_FIELDS if key in row}
            for row in selected
        ]
        return {
            "ok": True,
            "schema_version": "mcp-delivery-receipts.v1",
            "receipts": public_rows,
            "count": len(public_rows),
            "total": total,
            "scanned": min(total, scan_limit),
            "filter": {"status": normalized_status or None},
            "window_capped": bool(total > scan_limit),
            "ledger_only": True,
            "network_requested": False,
            "content_included": False,
        }

    return _safe("交付回执", _load)


def _boot(config_path: str):
    """加载客户配置（注入 token、解析 data_dir）并初始化资金时序库。"""
    global _runtime_config, _runtime_store, _write_tools_enabled  # noqa: PLW0603
    global _full_product_access, _product_api_base  # noqa: PLW0603
    from .config import Config
    from .knowledge.store import Store
    cfg = Config(config_path)
    _runtime_config = cfg
    _runtime_store = Store(cfg.data_dir)
    mcp_config = cfg.section("mcp")
    _write_tools_enabled = bool(mcp_config.get("allow_write_tools", False))
    _full_product_access = bool(mcp_config.get("full_product_access", False))
    _product_api_base = str(mcp_config.get("product_api_base", "")).strip().rstrip("/")
    if _full_product_access:
        parsed = urllib_parse.urlsplit(_product_api_base)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("mcp.product_api_base 必须是无凭据的本机 HTTP 地址")
    from .data import official_longhu_source
    official_longhu_source.init(cfg.data_dir)
    if getattr(cfg, "capital_tracking_enabled", False):
        from .data import capital_tracker as ct
        ct.init(cfg.data_dir)
    return cfg


def main():
    ap = argparse.ArgumentParser(description="Hermes Trader MCP Server")
    ap.add_argument("--config", required=True, help="客户配置 clients/<id>/config.yaml")
    args = ap.parse_args()
    _boot(args.config)
    mcp.run()  # 默认 stdio 传输，由 Hermes Agent 拉起


if __name__ == "__main__":
    main()
