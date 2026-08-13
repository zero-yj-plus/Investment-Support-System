"""REST API — 完整实现 docs/API_CONTRACT.md。"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import inspect
import ipaddress
import json
import logging
import os
import re
import socket
import time
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from pathlib import Path as _Path

from .. import __version__
from ..agent.hermes_client import HermesClient
from ..integrity import summary as _core_summary, core_fingerprint as _core_fp_calc
from ..data import (market, stock, news, sources, capital_tracker, xverify as xverify_mod,
                    overseas_source as overseas, deriv_source, mainforce_source as mainforce,
                    free_source as public_data, tdx_api_source as tdx_api,
                    official_disclosure, official_interaction, official_longhu_source)
from ..data.base import (
    is_trade_day, next_trade_day, now_cn, prev_trade_day,
    relay_proxy, trade_day_status, ttl_cache,
)
from ..delivery import (
    DELIVERY_RECEIPT_SCHEMA_VERSION,
    MAX_RECEIPT_LIMIT,
    DeliveryReceiptStore,
    daily_delivery_gate,
    delivery_readiness,
    delivery_channel,
    normalize_push_result,
    push_report_with_receipt,
)


@ttl_cache(30)
def _core_fp() -> str:
    """核心文件集实时指纹（30s 缓存）——前端「版本感知自动刷新」的信号源。
    与启动期缓存的 core_integrity 不同：每次重算当前磁盘文件，纯前端热部署（不重启）也会变。
    失败返回空串（前端视为无信号），绝不阻断 /status。"""
    try:
        return _core_fp_calc(_Path(__file__).resolve().parents[2])
    except Exception:  # noqa: BLE001
        return ""
from ..analysis import (sentiment as senti, features, timing as timing_mod, quant, screener,  # noqa: E402
                        diagnosis as diagnosis_mod, cockpit as cockpit_mod, recommend as recommend_mod,
                        advice_proposal as proposal_mod, mainline_timeline, mainline_runtime,
                        engine as engine_mod, stock_analysis_contract,
                        chat_materials as chat_materials_mod,
                        stock_perspectives as stock_perspectives_mod,
                        watchlist_zones as watchlist_zones_mod,
                        technical_screener as technical_screener_mod)
from ..jobs.scheduler import JOB_NAMES, apply_schedule, task_center_status  # noqa: E402
from ..product_state import build_market_context, route_lifecycle_contract  # noqa: E402
from ..decision.risk_gate import evaluate_risk_gate  # noqa: E402
from ..simulation import SimulationError  # noqa: E402
from ..jobs.simulation_matcher import order_valid_trade_date  # noqa: E402
from ..research_runs import (  # noqa: E402
    ActiveResearchRunDeleteError,
    ActiveResearchRunError,
    KINDS as RESEARCH_KINDS,
)

log = logging.getLogger("hermes.api")

router = APIRouter(prefix="/api")

_bg_tasks: set = set()  # 手动触发的后台任务强引用集（防 GC 提前回收）
_job_runs: dict[str, dict] = {}
_MODEL_PROBE_CACHE: dict = {"key": None, "at": 0.0, "connected": False, "capability": None}
_MODEL_PROBE_LOCK = asyncio.Lock()
_MODEL_PROBE_TTL = 300.0
_MODEL_PROBE_FAILURE_TTL = 10.0


def _model_probe_cache_fresh(probe_key: tuple[object, ...], now_mono: float) -> bool:
    """Keep healthy probes quiet, but retry startup/race failures quickly."""
    capability = _MODEL_PROBE_CACHE.get("capability")
    healthy = bool(
        _MODEL_PROBE_CACHE.get("connected") is True
        and isinstance(capability, dict)
        and capability.get("ready") is True
    )
    ttl = _MODEL_PROBE_TTL if healthy else _MODEL_PROBE_FAILURE_TTL
    return bool(
        _MODEL_PROBE_CACHE.get("key") == probe_key
        and now_mono - float(_MODEL_PROBE_CACHE.get("at") or 0) < ttl
        and isinstance(capability, dict)
    )

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CODE_RE = re.compile(r"^\d{6}$")

RTYPES = {"morning", "midday", "tail", "close", "evening", "trend"}

_BAD_NARRATIVE_VALUE_RE = re.compile(r"(?i)(?:None|null|undefined|nan)(?:%|板|家|元)?")
_BAD_MAINLINE_NARRATIVE_RE = re.compile(r"(?:围绕|跟随|做|买)[^\n；。]{0,24}净流入|净流入[^\n；。]{0,24}(?:主线|买入|低吸)")
_BAD_MAINLINE_ASSERTION_RE = re.compile(
    r"(?:第一|第二|今日|当前|最强|核心|首要|明确)?主线\s*(?:[：:]|是|为)|(?:是|为|成为|属于)主线"
)


def _narrative_field_is_safe(field: str, value: object) -> bool:
    """逐字段放行安全叙述，避免一个坏字段连带丢掉其它可用文案。"""
    text = str(value or "").strip()
    if not text or _BAD_NARRATIVE_VALUE_RE.search(text):
        return False
    # 这些字段会直接显示在顶部卡；凡夹带“主线”一律回退确定性文案，避免句式绕过。
    if field in {"定调", "回避", "change"} and "主线" in text:
        return False
    if field == "主线" and "净流入" in text:
        return False
    return not (
        _BAD_MAINLINE_NARRATIVE_RE.search(text)
        or _BAD_MAINLINE_ASSERTION_RE.search(text)
    )


def _narrative_is_safe(narrative: dict) -> bool:
    """拦截已缓存的旧叙述中 ``None板/None%`` 和「净流入=主线/买入依据」。"""
    if not isinstance(narrative, dict):
        return False
    fields = ("定调", "主线", "回避", "盘面推理", "操作结论", "change")
    return all(
        not narrative.get(field) or _narrative_field_is_safe(field, narrative[field])
        for field in fields
    )


def _merge_cockpit_narrative(command: dict, narrative: dict) -> dict:
    """把安全字段合入顶部决策卡；坏主线/坏推理不会连坐定调和回避。

    AI 叙述走 SWR：换档时会先秒回上一档并后台生成新档。旧文案虽然自身安全，
    但绝不能与新一档的确定性 regime/风险等级混在同一张卡里，否则会出现
    「弱势退潮」标题配「危机避险 / 情绪78」正文这样的自相矛盾。
    """
    cmd = dict(command or {})
    if not isinstance(narrative, dict) or not narrative.get("ok"):
        return cmd
    command_lock = str(cmd.get("定格时点") or "").strip()
    narrative_lock = str(narrative.get("定格时点") or "").strip()
    if narrative.get("stale") or (command_lock and narrative_lock and command_lock != narrative_lock):
        return cmd
    merged = False
    # 主线永不从 AI 叙述合入；页面只使用 /market/mainlines 的确定性市场结构结果。
    for src, dst in (("定调", "定调叙述"), ("回避", "回避叙述")):
        if _narrative_field_is_safe(src, narrative.get(src)):
            cmd[dst] = narrative[src]
            merged = True
    if _narrative_field_is_safe("change", narrative.get("change")):
        cmd["change"] = narrative["change"]
        merged = True
    if merged:
        cmd["叙述定格"] = narrative.get("定格时点", "")
    return cmd


def _state(request: Request):
    return request.app.state


def _simulation_store(request: Request):
    store = getattr(_state(request), "simulation_store", None)
    if store is None:
        raise SimulationError(
            "simulation_unavailable", "模拟执行账本未装配", status_code=503,
        )
    return store


def _simulation_error(exc: SimulationError) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": exc.message, "error_code": exc.code, "simulation_only": True},
        status_code=exc.status_code,
    )


async def _simulation_server_snapshot(request: Request, symbol: str, *, include_risk: bool) -> dict:
    """Server-authoritative risk/quote boundary for simulated execution.

    Client-provided risk, freshness, reference price, fill price, timestamp, and
    source are deliberately ignored by all simulation routes.
    """
    st = _state(request)
    simulation = _simulation_store(request)
    pulse: dict = {}
    if include_risk:
        try:
            value = await _pulse_locked(st.store)
            pulse = value if isinstance(value, dict) else {}
        except Exception:  # noqa: BLE001 - buy proposals fail closed below
            log.exception("模拟下单读取服务端 pulse 失败")
    try:
        value = await asyncio.to_thread(stock.quick_quote, symbol)
        quote = value if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001 - missing quote fails closed
        log.exception("模拟执行读取服务端 quick_quote 失败 symbol=%s", symbol)
        quote = {}

    missing: list[str] = []
    risk_level = pulse.get("risk_level") if isinstance(pulse.get("risk_level"), int) else None
    if include_risk:
        if risk_level is None:
            missing.append("server_risk_level")
        missing.extend(
            f"pulse:{item}" for item in (pulse.get("missing") or []) if str(item).strip()
        )
    market_price = quote.get("price")
    if not isinstance(market_price, (int, float)) or isinstance(market_price, bool):
        market_price = None
        missing.append("server_market_price")
    stale = bool(quote.get("stale"))
    if not quote:
        missing.append("server_quick_quote")

    # Price mode A: a fresh in-session quote is live; outside the session the
    # server may use quick_quote's latest trading-day close.  The close remains
    # explicitly stale as market data, but is an allowed simulation price mode.
    quote_as_of = quote.get("as_of")
    cn_now = now_cn()
    price_mode = "live"
    trade_date = cn_now.date().isoformat()
    as_of = simulation.current_time()
    source_id = "server_stock_quick_quote_live"
    if stale:
        price_mode = "latest_close"
        source_id = "server_stock_latest_close"
        quote_date_text = str(quote_as_of or "")[:10]
        try:
            quote_date = date.fromisoformat(quote_date_text)
            same_day_close_ready = quote_date < cn_now.date() or (
                quote_date == cn_now.date() and (cn_now.hour, cn_now.minute) >= (15, 0)
            )
            valid_close_date = same_day_close_ready and is_trade_day(quote_date)
        except (TypeError, ValueError):
            valid_close_date = False
        if valid_close_date:
            trade_date = quote_date_text
            as_of = f"{quote_date_text}T15:00:00+08:00"
        else:
            missing.append("server_close_trade_date")
            as_of = None
    if not as_of:
        missing.append("server_market_as_of")
    return {
        "risk_level": risk_level,
        "market_price": market_price,
        "as_of": as_of,
        "stale": stale,
        "missing": list(dict.fromkeys(missing)),
        "source_id": source_id,
        "price_mode": price_mode,
        "trade_date": trade_date,
        "quote_note": quote.get("quote_note"),
        "upstream_source": quote.get("source"),
    }


def _simulation_trade_date() -> str:
    today = now_cn().date()
    if is_trade_day(today):
        return today.isoformat()
    return prev_trade_day(today) or today.isoformat()


async def _simulation_account_quotes(request: Request) -> dict[str, dict]:
    symbols = await asyncio.to_thread(_simulation_store(request).account_symbols)
    if not symbols:
        return {}
    try:
        value = await asyncio.to_thread(stock.quick_quotes, symbols)
        rows = value if isinstance(value, dict) else {}
    except Exception:  # noqa: BLE001 - missing valuations stay explicit
        log.exception("模拟账户批量估值失败 symbols=%s", symbols)
        rows = {}
    normalized: dict[str, dict] = {}
    for symbol in symbols:
        quote = rows.get(symbol)
        if not isinstance(quote, dict):
            continue
        price = quote.get("price")
        if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
            continue
        normalized[symbol] = {
            "price": float(price),
            "price_mode": "latest_close" if quote.get("stale") else "live",
            "as_of": quote.get("as_of"),
            "source": quote.get("source") or "server_stock_quick_quotes",
        }
    return normalized


_ENTRY_ACTIONS = {"buy", "add", "open", "candidate", "long", "买入", "加仓", "建仓", "候选"}


def _candidate_action(body: dict) -> str:
    return str(
        body.get("direction") or body.get("action") or body.get("rec_type") or ""
    ).strip()


_INDUSTRY_BATCH_LOCK = asyncio.Lock()
_MAX_HOLDINGS = 50
_MAX_INDUSTRY_CODES = _MAX_HOLDINGS + 1


async def _server_verified_industries(codes, *, interactive: bool = False) -> dict[str, str]:
    """只用远程 TDX relation 批量核验行业；超时后由风险闸 fail closed。"""
    normalised = {
        str(code or "").split(".", 1)[0].zfill(6)
        for code in codes
    }
    normalised = {code for code in normalised if len(code) == 6 and code.isdigit()}
    if len(normalised) > _MAX_INDUSTRY_CODES:
        raise ValueError(f"单次最多核验 {_MAX_INDUSTRY_CODES} 只股票")
    if not normalised:
        return {}
    timeout = 1.5 if interactive else 6.0
    try:
        # 交互与保存/推荐链统一走同一份 relation 主数据。旧实现的普通路径
        # 会为每只持仓单独请求东财公开接口，既产生口径分叉，也会在 50 只
        # 持仓时把风险闸拖到十几秒。这里一次批量请求，缺失不再静默换源。
        async with asyncio.timeout(timeout):
            async with _INDUSTRY_BATCH_LOCK:
                rows = await asyncio.to_thread(
                    tdx_api.industry_for_codes, sorted(normalised)
                )
    except TimeoutError:
        log.warning(
            "服务端 TDX 行业关系整批核验超时 count=%s interactive=%s",
            len(normalised), interactive,
        )
        return {}
    except Exception:  # noqa: BLE001 — 缺失由风险闸收口
        log.warning(
            "服务端 TDX 行业关系核验失败 count=%s interactive=%s",
            len(normalised), interactive,
        )
        return {}
    return {
        code: str((item or {}).get("industry") or "").strip()[:80]
        for code, item in (rows or {}).items()
        if str((item or {}).get("industry") or "").strip()
    }


async def _server_risk_decision(
    st,
    body: dict,
    *,
    evidence_complete: bool,
    interactive: bool = False,
) -> dict:
    """用服务端市场风险档与真实持仓复核候选动作。

    客户端传入的 risk_decision 从不采信；它是本函数的输出。
    市场风险取全站同一 pulse 定格值，持仓取 Store 当前快照。
    """
    action_key = _candidate_action(body).casefold()
    if action_key in {action.casefold() for action in _ENTRY_ACTIONS}:
        try:
            pulse = await _pulse_locked(st.store)
        except Exception:  # noqa: BLE001 — 买入类将由 market_risk_missing fail closed
            log.exception("风险闸读取 pulse 失败")
            pulse = {}
    else:
        # 减仓/离场/观望不应被市场数据缺失阻断，纯规则闸会直接放行。
        pulse = {}
    command = pulse.get("command") if isinstance(pulse, dict) else {}
    command = command if isinstance(command, dict) else {}

    candidate = dict(body)
    candidate["action"] = _candidate_action(body)
    candidate["symbol"] = body.get("symbol") or body.get("code")
    base_code = str(candidate["symbol"] or "").split(".", 1)[0]

    # 组合、流动性与事件风险都是服务端信任边界：客户端传入的同名
    # 字段仅是显示意图，不能用来给自己放行。
    holdings = st.store.holdings()
    matching_holding = next(
        (row for row in holdings if str(row.get("code") or row.get("symbol") or "").split(".", 1)[0] == base_code),
        None,
    )
    if candidate["action"].casefold() in {"buy", "open", "candidate", "long", "买入", "建仓", "候选"} and matching_holding:
        candidate["action"] = "add"

    # 行业是集中度硬闸的主数据，不能信任持仓录入或推荐请求中的自由文本。
    # 对所有持仓及候选统一读取服务端轻量行业主数据；取不到就留空，由 RiskGate
    # 以 portfolio_sector_missing / sector_classification_missing fail closed。
    server_sector = ""
    quote: dict = {}
    verified_holdings: list[dict] = []
    if (
        action_key in {action.casefold() for action in _ENTRY_ACTIONS}
        and pulse.get("risk_level") != 5
    ):
        codes = {
            str(row.get("code") or row.get("symbol") or "").split(".", 1)[0].zfill(6)
            for row in holdings if isinstance(row, dict)
        }
        if len(base_code) == 6 and base_code.isdigit():
            codes.add(base_code)
        codes = {code for code in codes if len(code) == 6 and code.isdigit()}
        industry_task = asyncio.create_task(
            _server_verified_industries(codes, interactive=interactive)
        )
        quote_task = None
        if len(base_code) == 6 and base_code.isdigit():
            quote_task = asyncio.create_task(asyncio.wait_for(
                asyncio.to_thread(stock.quick_quote, base_code), timeout=5,
            ))
        industries = await industry_task
        if quote_task is not None:
            try:
                value = await quote_task
                quote = value if isinstance(value, dict) else {}
            except Exception:  # noqa: BLE001 — 流动性缺失由 RiskGate fail closed
                log.warning("风险闸候选轻量行情核验失败 code=%s", base_code)
        server_sector = industries.get(base_code, "")

        for row in holdings:
            if not isinstance(row, dict):
                continue
            verified = dict(row)
            holding_code = str(
                row.get("code") or row.get("symbol") or ""
            ).split(".", 1)[0].zfill(6)
            verified.pop("sector", None)
            verified["industry"] = industries.get(holding_code, "")
            verified_holdings.append(verified)
    else:
        # 非新增风险动作无需触发行情请求；仍剥离客户端行业，避免以后规则分支误用。
        for row in holdings:
            if not isinstance(row, dict):
                continue
            verified = dict(row)
            verified.pop("sector", None)
            verified["industry"] = ""
            verified_holdings.append(verified)
    candidate["sector"] = server_sector or None
    candidate["critical_evidence_complete"] = bool(evidence_complete)
    if not evidence_complete:
        supplied_missing = body.get("missing_evidence")
        if isinstance(supplied_missing, (list, tuple, set)):
            missing_items = [str(item) for item in supplied_missing if item]
        elif supplied_missing:
            missing_items = [str(supplied_missing)]
        else:
            missing_items = []
        candidate["missing_evidence"] = list(dict.fromkeys([
            *missing_items, "reference_price_evidence",
        ]))

    portfolio = {"holdings": verified_holdings}
    amount = quote.get("amount") if isinstance(quote, dict) else None
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = None
    liquidity = {"adequate": bool(amount >= 50_000_000)} if amount is not None else {}
    event = {}
    if "blackswan" in command:
        event["major_event"] = bool(command.get("blackswan"))

    return evaluate_risk_gate({
        "candidate": candidate,
        "market": {"risk_level": pulse.get("risk_level") if isinstance(pulse, dict) else None},
        "portfolio": portfolio,
        "liquidity": liquidity,
        "event": event,
    })


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _deterministic_diagnosis_proposal(card: dict, *, has_holding: bool) -> dict:
    """/诊断的动作、置信度和仓位权必须由确定性证据得出。

    模型仍可以组织证据叙述，但它的 action/conviction/价位只保留在
    ``model_hypothesis`` 供审计，不能再直接进入风险闸或客户界面的最终结论。
    """
    evidence_rows = [
        row for row in (card.get("decision_market_evidence") or [])
        if isinstance(row, dict)
    ]
    values = {
        str(row.get("field") or ""): _finite_number(row.get("value"))
        for row in evidence_rows
    }
    quality = card.get("decision_market_evidence_quality") or {}
    captured = int(quality.get("captured") or 0)
    requested = max(1, int(quality.get("requested") or 0))
    coverage = min(1.0, captured / requested)
    required = {"last_price", "prev_close", "change_pct", "amount", "volume_ratio", "turnover_rate"}
    missing_required = sorted(field for field in required if values.get(field) is None)
    conflict_fields = {
        str(row.get("field") or "")
        for row in evidence_rows
        if row.get("quality_status") == "conflict"
    }
    # 涨跌幅本就是现价/昨收的可复算派生值。如果上游对该字段报 conflict，
    # 但其中一个值与当前现价和昨收重算结果一致，不能把整只股票降级成
    # “证据不完整”。价格本身的冲突仍 fail closed。
    price, prev = values.get("last_price"), values.get("prev_close")
    change = values.get("change_pct")
    recalculated_change = (
        round((price / prev - 1) * 100, 4)
        if price is not None and prev is not None and prev > 0 else None
    )
    change_reconciled = bool(
        conflict_fields <= {"change_pct"}
        and change is not None
        and recalculated_change is not None
        and abs(change - recalculated_change) <= 0.35
    )
    quality_status = str(quality.get("status") or "")
    quality_usable = quality_status in {"ok", "degraded"} or (
        quality_status == "conflict" and change_reconciled
    )
    complete = bool(quality_usable and not missing_required)

    # 深度诊断已经完成 60 日趋势、RSI/MACD、ATR、量价、位置和资金等
    # 多维复算；治理层只负责把它映射到持仓语义与 RiskGate，不能再退回
    # 原先“8 个盘口字段打分”的浅层逻辑。
    deep = card.get("deep_signal") if isinstance(card.get("deep_signal"), dict) else None
    if deep and str(deep.get("rule_version") or "").startswith("diagnosis-deep-signal"):
        deep_complete = bool(deep.get("complete") and complete)
        action = str(deep.get("action") or "观望")
        if has_holding:
            action = {
                "买入": "加仓",
                "观望": "持有",
                "回避": "减仓",
            }.get(action, action)
        if not deep_complete:
            action = "持有" if has_holding else "观望"
        confidence = _finite_number(deep.get("confidence"))
        if confidence is None:
            confidence = 0.28
        if not deep_complete:
            confidence = min(confidence, 0.40)
        conviction = "高" if confidence >= 0.72 else ("中" if confidence >= 0.55 else "低")
        reasons = list(deep.get("reasons") or [])
        if not deep_complete:
            reasons.append("深度结构或关键快行情证据不完整，禁止新增风险")
        pipeline = card.get("analysis_pipeline") or {}
        total = max(1, int(pipeline.get("total") or 0))
        completed = max(0, int(pipeline.get("completed") or 0))
        return {
            "action": action,
            "confidence": round(confidence, 4),
            "conviction": conviction,
            "signal_score": int(deep.get("signal_score") or 0),
            "coverage": round(min(1.0, completed / total), 4),
            "complete": deep_complete,
            "missing_required": missing_required,
            "conflict_fields": sorted(conflict_fields),
            "recalculated_change_pct": recalculated_change,
            "change_reconciled": change_reconciled,
            "reasons": reasons,
            "reference_price": price,
            "overextended": bool(deep.get("overextended")),
            "rule_version": "diagnosis-deep-governance-v1",
        }

    score = 0
    reasons: list[str] = []
    safety_fallback = card.get("analysis_mode") in {
        "deterministic_market_fallback", "market_data_fallback",
    }
    if safety_fallback:
        degraded_reason = str(card.get("degraded_reason") or "").strip()
        reasons.append(
            degraded_reason
            or "模型增强未可用，已使用确定性/行情安全降级"
        )
    if quality_status == "conflict" and change_reconciled:
        reasons.append("涨跌幅跨源差异已按现价/昨收重算核对")
    if change is not None:
        if 1 <= change <= 5:
            score += 1
            reasons.append("涨幅处于非过热强势区间")
        elif change >= 7 or change <= -4:
            score -= 2
            reasons.append("价格过热或快速走弱")
        elif change < 0:
            score -= 1
            reasons.append("当日价格走弱")
    if price is not None and prev is not None and prev > 0:
        if price > prev:
            score += 1
            reasons.append("现价站上前收")
        elif price < prev:
            score -= 1
            reasons.append("现价低于前收")
    volume_ratio = values.get("volume_ratio")
    if volume_ratio is not None:
        if 1 <= volume_ratio <= 2.5:
            score += 1
            reasons.append("量比有效放大但未失真")
        elif volume_ratio > 4 or volume_ratio < 0.8:
            score -= 1
            reasons.append("量比过热或承接不足")
    turnover = values.get("turnover_rate")
    if turnover is not None:
        if 1 <= turnover <= 12:
            score += 1
            reasons.append("换手处于可交易区间")
        elif turnover > 20:
            score -= 1
            reasons.append("换手过热")
    amount = values.get("amount")
    if amount is not None:
        if amount >= 50_000_000:
            score += 1
            reasons.append("成交额通过流动性底线")
        elif amount < 20_000_000:
            score -= 2
            reasons.append("成交额低于可执行底线")

    if safety_fallback:
        action = "观望"
        confidence = min(0.40, round(0.20 + coverage * 0.20, 4))
        reasons.append("模型增强降级期间不新增风险敞口")
    elif not complete:
        action = "观望"
        confidence = min(0.40, round(0.20 + coverage * 0.20, 4))
        reasons.append("关键市场证据不完整，禁止新增风险")
    else:
        if has_holding:
            action = "加仓" if score >= 4 else ("减仓" if score <= -2 else "持有")
        else:
            action = "买入" if score >= 4 else ("回避" if score <= -2 else "观望")
        confidence = round(min(0.75, 0.46 + min(abs(score), 4) * 0.06), 4)
    conviction = "高" if confidence >= 0.72 else ("中" if confidence >= 0.55 else "低")
    return {
        "action": action,
        "confidence": confidence,
        "conviction": conviction,
        "signal_score": score,
        "coverage": round(coverage, 4),
        "complete": complete,
        "missing_required": missing_required,
        "conflict_fields": sorted(conflict_fields),
        "recalculated_change_pct": recalculated_change,
        "change_reconciled": change_reconciled,
        "reasons": reasons,
        "reference_price": price,
        "rule_version": "diagnosis-deterministic-v1",
    }


def _deterministic_trade_plan(card: dict, *, has_holding: bool) -> dict:
    """用已入库的实时价格锚点生成可复算条件，不让模型自造价位。

    优先级：当日低点/昨收/开盘价中不高于现价的最近支撑；阻力用当日高点。
    “回踩区间”上沿是支撑上方 1%，且不超过当前价，这是明示的
    确定性公式，不是隐含预测。
    """
    quote = card.get("market_quote") or {}
    price = _finite_number(quote.get("price"))
    prev = _finite_number(quote.get("prev_close"))
    opened = _finite_number(quote.get("open"))
    high = _finite_number(quote.get("high"))
    low = _finite_number(quote.get("low"))
    if price is None:
        price_row = next((
            row for row in (card.get("decision_market_evidence") or [])
            if isinstance(row, dict) and row.get("field") == "last_price"
        ), None)
        price = _finite_number((price_row or {}).get("value"))
    if prev is None:
        prev_row = next((
            row for row in (card.get("decision_market_evidence") or [])
            if isinstance(row, dict) and row.get("field") == "prev_close"
        ), None)
        prev = _finite_number((prev_row or {}).get("value"))

    technical = (
        card.get("technical_analysis")
        if isinstance(card.get("technical_analysis"), dict) else {}
    )
    deep_levels = (
        technical.get("levels")
        if isinstance(technical.get("levels"), dict) else {}
    )
    deep_support = _finite_number(deep_levels.get("support"))
    deep_resistance = _finite_number(deep_levels.get("resistance"))
    if deep_support is not None and price is not None:
        metrics = technical.get("metrics") or {}
        atr = _finite_number(metrics.get("atr14"))
        upper = min(price, deep_support + (atr * 0.35 if atr else price * 0.008))
        lower, upper = sorted((deep_support, upper))
        narrow = (upper - lower) < max(price * 0.003, 0.02)
        if narrow:
            entry_zone = f"约 {deep_support:.2f} 附近（结构支撑确认后）"
            entry_precision = "point_nearby"
        else:
            entry_zone = f"{lower:.2f}–{upper:.2f}（结构支撑区）"
            entry_precision = "range"
        support_basis = str(deep_levels.get("support_basis") or "日K结构")
        resistance_basis = str(deep_levels.get("resistance_basis") or "日K结构")
        entry_trigger = (
            f"回踩 {support_basis} {deep_support:.2f} 附近缩量企稳"
            + (
                f"，或放量突破 {resistance_basis} {deep_resistance:.2f}"
                if deep_resistance is not None and deep_resistance > upper else ""
            )
        )
        stop = f"收盘有效跌破 {support_basis} {deep_support:.2f}，且下一交易日不能收回"
        return {
            "entry_zone": entry_zone,
            "entry_zone_precision": entry_precision,
            "stop": stop,
            "entry_trigger": entry_trigger,
            "hold_trigger": (
                f"{support_basis} {deep_support:.2f} 未有效失守可继续跟踪"
                if has_holding else None
            ),
            "support": round(deep_support, 4),
            "resistance": round(deep_resistance, 4) if deep_resistance is not None else None,
            "invalidation": round(deep_support, 4),
            "basis": [
                item for item in (support_basis, resistance_basis if deep_resistance is not None else "")
                if item
            ],
            "atr14": round(atr, 4) if atr is not None else None,
            "rule_version": "diagnosis-structure-plan-v1",
        }

    support_candidates = [
        value for value in (low, prev, opened)
        if value is not None and value > 0 and (price is None or value <= price * 1.002)
    ]
    support = max(support_candidates) if support_candidates else (prev or low or opened)
    resistance = high if high is not None and (price is None or high >= price) else price
    invalidation = low or support or prev

    entry_zone = None
    entry_zone_precision = "unavailable"
    entry_trigger = "当前缺少可复算价格锚点，不设进场条件"
    if support is not None and price is not None:
        upper = min(price, support * 1.01)
        lower, upper = sorted((support, upper))
        # 支撑与现价几乎重合时，两位小数的“极窄区间”是假精度，
        # 实盘也不可执行。不人工放宽价位，改成基于真实支撑的“附近+条件”。
        narrow = (upper - lower) < max(price * 0.003, 0.02)
        if narrow:
            entry_zone = f"约 {support:.2f} 附近（回踩企稳，不追价）"
            entry_zone_precision = "point_nearby"
            pullback_trigger = f"回踩 {support:.2f} 附近缩量企稳"
        else:
            entry_zone = f"{lower:.2f}–{upper:.2f}（回踩企稳）"
            entry_zone_precision = "range"
            pullback_trigger = f"回踩 {lower:.2f}–{upper:.2f} 缩量企稳"
        if resistance is not None and resistance > upper:
            entry_trigger = (
                f"{pullback_trigger}，或放量站稳 {resistance:.2f}"
            )
        else:
            entry_trigger = pullback_trigger
    stop = f"有效跌破 {invalidation:.2f}" if invalidation is not None else None
    hold_trigger = (
        f"不破 {invalidation:.2f} 可继续跟踪"
        if invalidation is not None and has_holding else None
    )
    return {
        "entry_zone": entry_zone,
        "entry_zone_precision": entry_zone_precision,
        "stop": stop,
        "entry_trigger": entry_trigger,
        "hold_trigger": hold_trigger,
        "support": round(support, 4) if support is not None else None,
        "resistance": round(resistance, 4) if resistance is not None else None,
        "invalidation": round(invalidation, 4) if invalidation is not None else None,
        "basis": [
            label for label, value in (
                ("当日低点", low), ("昨收", prev), ("开盘价", opened), ("当日高点", high),
            ) if value is not None
        ],
        "rule_version": "diagnosis-price-plan-v2",
    }


def _deterministic_diagnosis_evidence(card: dict) -> list[dict]:
    """把字段级行情证据组装成用户可读证据链，不伪装成模型论据。"""
    rows = {
        str(row.get("field") or ""): row
        for row in (card.get("decision_market_evidence") or [])
        if isinstance(row, dict) and row.get("value") is not None
    }
    result: list[dict] = []

    def add(finding: str, direction: str, *fields: str) -> None:
        refs = [
            f"evidence:{rows[field].get('evidence_id')}"
            for field in fields if rows.get(field, {}).get("evidence_id")
        ]
        sources = [
            str(rows[field].get("source_id") or rows[field].get("source") or "实时行情")
            for field in fields if field in rows
        ]
        result.append({
            "finding": finding,
            "direction": direction,
            "source": "+".join(dict.fromkeys(sources)) or "实时行情",
            "evidence_ref": refs[0] if refs else "",
            "evidence_refs": refs,
            "rule_version": "diagnosis-deterministic-evidence-v1",
        })

    price = _finite_number(rows.get("last_price", {}).get("value"))
    prev = _finite_number(rows.get("prev_close", {}).get("value"))
    if price is not None and prev is not None:
        direction = "利多" if price > prev else ("利空" if price < prev else "中性")
        relation = "高于" if price > prev else ("低于" if price < prev else "等于")
        add(f"现价 {price:.2f}，{relation}昨收 {prev:.2f}", direction, "last_price", "prev_close")
    change = _finite_number(rows.get("change_pct", {}).get("value"))
    if change is not None:
        add(f"当日涨跌幅 {change:+.2f}%", "利多" if change > 0 else ("利空" if change < 0 else "中性"), "change_pct")
    volume_ratio = _finite_number(rows.get("volume_ratio", {}).get("value"))
    turnover = _finite_number(rows.get("turnover_rate", {}).get("value"))
    if volume_ratio is not None or turnover is not None:
        parts = []
        if volume_ratio is not None:
            parts.append(f"量比 {volume_ratio:.2f}")
        if turnover is not None:
            parts.append(f"换手 {turnover:.2f}%")
        add("，".join(parts), "中性", "volume_ratio", "turnover_rate")
    amount = _finite_number(rows.get("amount", {}).get("value"))
    if amount is not None:
        add(f"成交额 {amount / 100_000_000:.2f} 亿元", "中性", "amount")
    return result[:4]


async def governed_diagnosis_card(
    st,
    code: str,
    data_material_ids: tuple[str, ...] | None = None,
) -> dict:
    """Web/MCP 共用的唯一个股诊断治理入口。"""
    diagnose_kwargs = {"include_model": False}
    try:
        if "analysis_depth" in inspect.signature(diagnosis_mod.diagnose).parameters:
            diagnose_kwargs["analysis_depth"] = "deep"
        if (
            data_material_ids is not None
            and "data_material_ids" in inspect.signature(diagnosis_mod.diagnose).parameters
        ):
            diagnose_kwargs["data_material_ids"] = data_material_ids
    except (TypeError, ValueError):
        pass
    raw = await diagnosis_mod.diagnose(st.engine, code, **diagnose_kwargs)
    if raw.get("recognized") is False or raw.get("error_code") == "stock_not_found":
        return raw
    holdings = st.store.holdings()
    has_holding = any(
        str(row.get("code") or row.get("symbol") or "").split(".", 1)[0] == code
        for row in holdings if isinstance(row, dict)
    )
    proposal = _deterministic_diagnosis_proposal(raw, has_holding=has_holding)
    trade_plan = _deterministic_trade_plan(raw, has_holding=has_holding)
    evidence_ids = [
        str(row.get("evidence_id"))
        for row in (raw.get("decision_market_evidence") or [])
        if isinstance(row, dict) and row.get("evidence_id")
    ]
    # 快速诊断不请求模型，因此 evidence_validation / price_level_validation
    # 不存在是预期行为，不能被解读为“证据不完整”。正式动作的证据门
    # 以 MarketDataGateway 的字段级 evidence_id + 确定性重算为准。
    evidence_complete = bool(proposal["complete"] and evidence_ids)
    proposed_position = 2.0 if proposal["action"] in {"买入", "加仓"} else None
    risk = await _server_risk_decision(st, {
        "direction": proposal["action"],
        "code": code,
        "confidence": proposal["confidence"],
        "evidence_ids": evidence_ids,
        "position_cap_pct": proposed_position,
    }, evidence_complete=evidence_complete, interactive=True)

    card = dict(raw)
    card["model_hypothesis"] = {
        "action": raw.get("action"),
        "conviction": raw.get("conviction"),
        "entry_zone": raw.get("entry_zone"),
        "stop": raw.get("stop"),
        "summary": raw.get("summary"),
    }
    card["decision_authority"] = (
        "deep_deterministic_analysis+risk_gate"
        if raw.get("analysis_mode") == "deterministic_deep"
        else "deterministic_signal+risk_gate"
    )
    card["deterministic_signal"] = proposal
    card["trade_plan"] = trade_plan
    display_risk = dict(risk)
    if "major_event" in set(risk.get("reason_codes") or []):
        display_risk["raw_reasons"] = list(risk.get("reasons") or [])
        display_risk["reasons"] = [
            "全市场指数破位/跌停潮风险触发，暂停新开仓；这不是该公司的重大事件结论"
            if str(reason) == "存在重大事件风险，禁止买入或加仓"
            else f"{str(reason)}；这是全市场风险，不是该公司的重大事件结论"
            for reason in (risk.get("reasons") or [])
        ]
        display_risk["event_scope"] = "market_systemic"
    card["risk_decision"] = display_risk
    card["signal_action"] = proposal["action"]
    card["action"] = proposal["action"]
    card["confidence"] = risk.get("effective_confidence")
    confidence = _finite_number(card["confidence"])
    card["conviction"] = (
        "高" if confidence is not None and confidence >= 0.72
        else ("中" if confidence is not None and confidence >= 0.55 else "低")
    )
    card["position_cap_pct"] = (
        risk.get("position_cap_pct")
        if proposal["action"] in {"买入", "加仓"} else None
    )
    # 价位由实时 quote 中的当日高低、昨收和开盘价按公式生成；
    # 不借用未验证的 LLM 数字，但也不再把产品必需的条件全部置空。
    card["entry_zone"] = trade_plan.get("entry_zone")
    card["stop"] = trade_plan.get("stop")
    card["evidence"] = _deterministic_diagnosis_evidence(card)
    if risk.get("decision") == "veto" and proposal["action"] in {"买入", "加仓"}:
        card["original_action"] = proposal["action"]
        hard_veto_codes = {
            "market_risk_level_5", "liquidity_insufficient",
            "total_position_limit_reached", "projected_total_position_limit",
        }
        reason_codes = set(risk.get("reason_codes") or [])
        # 账户 NAV/行业归属等授权证据缺失，只能说“暂未放行新仓”，
        # 不能把技术面偏强的股票错译成“回避”。当前 major_event 来自
        # 全市场指数破位/跌停潮，已通过市场风险等级阻断新仓，不是该公司
        # 被立案或退市，不能再错译为这只股票本身必须回避。
        # 只有市场 5 级、个股流动性或组合硬上限才能拦截
        # 「新开仓」。对已有持仓，这些理由不能被错译成个股必须退出：
        # 原始信号是加仓时改为「持有、不加仓」，已有的减仓/止损
        # 信号仍按原规则执行。
        if has_holding:
            card["action"] = "持有"
            card["risk_block_scope"] = "new_risk_only"
        else:
            card["action"] = "回避" if reason_codes & hard_veto_codes else "观望"
        card["conviction"] = "低"
    structure = str((card.get("technical_analysis") or {}).get("structure") or "")
    if card["action"] == "观望" and proposal["action"] in {"买入", "加仓"}:
        card["summary"] = (
            f"{structure or '多维结构'}（{proposal['signal_score']:+d} 分），但账户/风控授权证据未齐，"
            f"当前只能观望；关注「{trade_plan.get('entry_trigger')}」，"
            f"失效条件为「{trade_plan.get('stop') or '价格锚点不足'}」。"
        )
    else:
        card["summary"] = (
            f"{structure + '；' if structure else ''}当前动作「{card['action']}」"
            f"（深度规则 {proposal['signal_score']:+d} 分）；"
            + ("、".join(proposal["reasons"][:3]) or "暂无足够证据改变仓位")
            + f"。执行条件：{trade_plan.get('entry_trigger')}；"
            + f"失效条件：{trade_plan.get('stop') or '价格锚点不足'}。"
        )
    evidence_quality = card.get("market_evidence_quality") or {}
    card["analysis_contract"] = stock_analysis_contract.build(
        entry="governed_stock_diagnosis",
        current_stage="governed_stock_diagnosis",
        analysis_executed=bool(
            (card.get("analysis_pipeline") or {}).get("completed")
            or card.get("deterministic_signal")
        ),
        deterministic_method="deep_technical_analysis+risk_gate",
        deterministic_rule_version=str(
            (card.get("deterministic_signal") or {}).get("rule_version")
            or "diagnosis-deterministic-v1"
        ),
        data_status=str(evidence_quality.get("status") or "missing"),
        data_as_of=card.get("decision_generated_at"),
        expected=evidence_quality.get("requested"),
        present=evidence_quality.get("captured"),
        missing=card.get("missing") or [],
        ai_status="not_requested",
        decision_authority=str(card.get("decision_authority") or "deterministic_only"),
    )
    return card


def _sse(gen):
    async def stream():
        try:
            async for event in gen:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception:
            # 不把原始异常/上游响应体回传给客户端（可能含内部细节）；详情只记服务端日志。
            log.exception("SSE 流异常")
            yield f"data: {json.dumps({'error': '服务内部错误，请稍后重试或查看服务端日志'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- 状态

def _agent_capability_product_semantics(capability: object) -> dict:
    """补充产品作用域，不改写 Agent 客户端的 fail-closed 结果。"""
    result = dict(capability) if isinstance(capability, dict) else {}
    result["required_for_ai_calls"] = result.get("required")
    result["blocks_core"] = False
    return result


@router.get("/shell-status")
async def shell_status(request: Request):
    """首屏壳状态：只返回路由、品牌和交易时态。

    该端点故意不做模型连通、Agent 权限、证据库健康、核心文件哈希等
    运维诊断，避免上游网关的 8 秒超时阻塞整个网页。完整健康信息仍由
    ``/status`` 提供，设置/诊断页的语义不变。
    """
    st = _state(request)
    market_context = await asyncio.to_thread(build_market_context)
    route_contract = route_lifecycle_contract()
    return {
        "ok": True,
        "client": {
            "name": st.config.client_name,
            "id": st.config.client_id,
            "brand": st.config.client_brand,
            "brand_sub": st.config.client_brand_sub,
        },
        "version": __version__,
        "date": market_context["date"],
        "is_trade_day": market_context["is_trade_day"],
        "market_session": market_context["market_session"],
        "recommended_route": market_context["recommended_route"],
        "market_context": market_context,
        "route_redirects": route_contract["redirects"],
        "ui": st.config.section("ui"),
        "data_source": (st.config.section("ui") or {}).get("data_source_label") or "多源行情",
        "status_scope": "shell",
    }


@router.get("/status")
async def status(request: Request):
    st = _state(request)
    model_configured = bool(getattr(st.config, "model_connection_custom", False))
    if model_configured:
        probe_key = (
            str(getattr(st.config, "hermes_base_url", "")),
            str(getattr(st.config, "hermes_model", "")),
            str(getattr(st.config, "model_provider", "")),
            bool(getattr(st.config, "hermes_api_key", "")),
        )
        now_mono = time.monotonic()
        cached_probe = _model_probe_cache_fresh(probe_key, now_mono)
        if not cached_probe:
            async with _MODEL_PROBE_LOCK:
                now_mono = time.monotonic()
                cached_probe = _model_probe_cache_fresh(probe_key, now_mono)
                if not cached_probe:
                    capability_fn = getattr(st.hermes, "capability_preflight", None)
                    capability_awaitable = (
                        capability_fn() if callable(capability_fn)
                        else asyncio.sleep(0, result={
                            "ready": False,
                            "required": True,
                            "status": "capability_unavailable",
                            "reason": "Agent 权限预检未装配",
                        })
                    )
                    try:
                        connected, agent_capability = await asyncio.wait_for(
                            asyncio.gather(st.hermes.check_connection(), capability_awaitable),
                            timeout=4.0,
                        )
                    except asyncio.TimeoutError:
                        connected = False
                        agent_capability = {
                            "ready": False,
                            "required": True,
                            "status": "model_probe_timeout",
                            "reason": "AI 连接探测超过4秒，已停止等待；不影响行情与决策页",
                        }
                    _MODEL_PROBE_CACHE.update({
                        "key": probe_key,
                        "at": time.monotonic(),
                        "connected": bool(connected),
                        "capability": dict(agent_capability or {}),
                    })
        connected = bool(_MODEL_PROBE_CACHE.get("connected"))
        agent_capability = dict(_MODEL_PROBE_CACHE.get("capability") or {})
    else:
        # AI 是可选增强。用户未保存模型连接时不主动探测
        # 默认 Hermes 端口，也不把“未配置”冒充成全局故障。
        connected = False
        agent_capability = {
            "ready": False,
            "required": True,
            "status": "model_connection_optional",
            "reason": "AI 增强未配置（可选）",
            "runtime_mode": "not_configured",
            "tool_execution": "none",
            "allowed_toolsets": [],
            "enabled_toolsets": [],
            "unexpected_toolsets": [],
        }
    agent_capability = _agent_capability_product_semantics(agent_capability)
    market_context = await asyncio.to_thread(build_market_context)
    task_center = task_center_status(st.scheduler, st.config, st.store)
    route_contract = route_lifecycle_contract()
    evidence_store = getattr(st, "evidence_store", None)
    market_gateway = getattr(st, "market_gateway", None)
    evidence_status = (
        await asyncio.to_thread(evidence_store.stats)
        if evidence_store is not None
        else {"sources": 0, "evidence_points": 0, "status": "unavailable"}
    )
    if market_gateway is not None:
        evidence_status = {
            **evidence_status,
            "runtime": await asyncio.to_thread(market_gateway.health),
        }
    return {
        "ok": True,
        "client": {"name": st.config.client_name, "id": st.config.client_id,
                    "brand": st.config.client_brand, "brand_sub": st.config.client_brand_sub},
        "version": __version__,
        # 核心完整性/版本(隔离闸)：core_version=客户当前核心版本；ok=False 表示客户私改了 hermes_trader/frontend。
        # fingerprint=实时核心指纹(30s缓存,丢线程池防100文件哈希卡事件循环)——前端自动刷新信号。
        "core": {**_core_summary(getattr(st, "core_integrity", None)),
                 "fingerprint": await asyncio.to_thread(_core_fp)},
        "date": market_context["date"],
        "is_trade_day": market_context["is_trade_day"],
        # Phase 1 产品状态机：所有页面只消费这一份北京时间/交易日事实。
        "market_session": market_context["market_session"],
        "recommended_route": market_context["recommended_route"],
        "market_context": market_context,
        # 旧字段与结构化完整契约同时提供：旧链接不删，前端可按 tab 精确跳转。
        "route_redirects": route_contract["redirects"],
        "route_lifecycle": route_contract,
        "hermes": {
            "base_url": st.config.hermes_base_url,
            "model": st.config.hermes_model,
            "provider": str(getattr(st.config, "model_provider", "hermes")),
            "connected": connected,
            "optional": True,
            "required_for_core": False,
            "configured": bool(
                model_configured
                and getattr(st.config, "model_uses_hermes_gateway", True)
            ),
            "active_as_model_gateway": bool(
                model_configured
                and getattr(st.config, "model_uses_hermes_gateway", True)
            ),
            "custom_connection": model_configured,
            "agent_capability": agent_capability,
            "run_summary": (
                await asyncio.to_thread(st.agent_runs.summary)
                if getattr(st, "agent_runs", None) is not None
                else {"counts": {}, "last_run": None}
            ),
        },
        "model_connection": {
            "configured": model_configured,
            "connection_id": getattr(st.config, "active_model_connection_id", None),
            "provider": str(getattr(st.config, "model_provider", "hermes")),
            "base_url": st.config.hermes_base_url,
            "model": st.config.hermes_model,
            "connected": connected if model_configured else None,
            "status": (
                "connected" if connected
                else ("unavailable" if model_configured else "not_configured")
            ),
            "required_for_core": False,
        },
        # 推送是否就绪：看当前活动推送器（网关投递看 CLI 是否在；飞书 webhook 看是否配置）
        "feishu": {"configured": bool(getattr(st.feishu, "configured", False))},
        # 数据源展示串：客户可在 config.ui.data_source_label 覆盖。未配时用中性
        # 「多源行情」；当前实际链路会按端点使用腾讯/通达信/新浪/同花顺等免费源。
        "data_source": (st.config.section("ui") or {}).get("data_source_label") or "多源行情",
        # 前端 UI 偏好（按客户 config.ui 子配置；off-by-default：未配置返回空 dict、前端照常）。
        # 如 wudi 的 ui.chat_compact=true → 问股页隐藏右侧个股数据面板。
        "ui": st.config.section("ui"),
        "relay": _relay_health(),
        # jobs 保留 v1 前端兼容；task_center 是包含内置+扩展+系统任务的新契约。
        "jobs": task_center["jobs"],
        "task_center": task_center,
        "evidence": evidence_status,
        "recommendation_ledger": {
            "schema_version": recommend_mod.RECOMMENDATION_EVENT_SCHEMA_VERSION,
            "startup_recovery": dict(
                getattr(
                    st.store,
                    "recommendation_recovery",
                    {"recovered": 0, "failed": ["startup_recovery_unreported"]},
                ) or {"recovered": 0, "failed": ["startup_recovery_unreported"]}
            ),
        },
    }


@router.get("/agent/capabilities")
async def agent_capabilities(request: Request, refresh: bool = False):
    """Agent 工具权限上线门禁；不回传 API key/profile 路径或配置原文。"""
    st = _state(request)
    capability = await st.hermes.capability_preflight(force=bool(refresh))
    return {"ok": True, "capability": _agent_capability_product_semantics(capability)}


@router.get("/agent/runs")
async def agent_runs(request: Request, limit: int = 30):
    """Agent 运行可追溯账本；只包含哈希/大小/终态，不含 prompt 和模型原文。"""
    rows = await asyncio.to_thread(
        _state(request).agent_runs.recent,
        max(1, min(int(limit), 100)),
    )
    return {"ok": True, "items": rows}


# ---------------------------------------------------------------- 模拟执行（只写本地账本，绝不连接券商）

@router.get("/simulation/account")
async def simulation_account(request: Request, curve_limit: int = 365):
    try:
        quotes = await _simulation_account_quotes(request)
        account = await asyncio.to_thread(
            _simulation_store(request).account_view,
            quotes=quotes,
            trade_date=_simulation_trade_date(),
            curve_limit=max(1, min(int(curve_limit), 2000)),
        )
        return {"ok": True, **account}
    except SimulationError as exc:
        return _simulation_error(exc)


@router.post("/simulation/account")
async def simulation_account_initialize(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise SimulationError("invalid_json", "请求体必须是 JSON 对象")
        result = await asyncio.to_thread(
            _simulation_store(request).initialize_account,
            client_request_id=body.get("client_request_id"),
            initial_cash=body.get("initial_cash"),
            commission_rate_pct=body.get("commission_rate_pct"),
            minimum_commission=body.get("minimum_commission"),
            stamp_duty_rate_pct=body.get("stamp_duty_rate_pct"),
            transfer_fee_rate_pct=body.get("transfer_fee_rate_pct"),
            slippage_bps=body.get("slippage_bps", 0),
        )
        account = await asyncio.to_thread(
            _simulation_store(request).account_view,
            trade_date=_simulation_trade_date(),
        )
        return {"ok": True, "simulation_only": True, **result, "account": account}
    except SimulationError as exc:
        return _simulation_error(exc)
    except Exception as exc:
        if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
            return _simulation_error(SimulationError("invalid_json", "无效 JSON 请求体"))
        raise


@router.post("/simulation/account/settings")
async def simulation_account_settings(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise SimulationError("invalid_json", "请求体必须是 JSON 对象")
        result = await asyncio.to_thread(
            _simulation_store(request).update_account_settings,
            client_request_id=body.get("client_request_id"),
            commission_rate_pct=body.get("commission_rate_pct"),
            minimum_commission=body.get("minimum_commission"),
            stamp_duty_rate_pct=body.get("stamp_duty_rate_pct"),
            transfer_fee_rate_pct=body.get("transfer_fee_rate_pct"),
            slippage_bps=body.get("slippage_bps", 0),
        )
        account = await asyncio.to_thread(
            _simulation_store(request).account_view,
            trade_date=_simulation_trade_date(),
        )
        return {"ok": True, "simulation_only": True, **result, "account": account}
    except SimulationError as exc:
        return _simulation_error(exc)
    except Exception as exc:
        if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
            return _simulation_error(SimulationError("invalid_json", "无效 JSON 请求体"))
        raise


@router.post("/simulation/account/valuation")
async def simulation_account_valuation(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise SimulationError("invalid_json", "请求体必须是 JSON 对象")
        quotes = await _simulation_account_quotes(request)
        trade_date = _simulation_trade_date()
        result = await asyncio.to_thread(
            _simulation_store(request).record_account_valuation,
            client_request_id=body.get("client_request_id"),
            quotes=quotes,
            trade_date=trade_date,
        )
        account = await asyncio.to_thread(
            _simulation_store(request).account_view,
            quotes=quotes,
            trade_date=trade_date,
        )
        return {"ok": True, "simulation_only": True, **result, "account": account}
    except SimulationError as exc:
        return _simulation_error(exc)
    except Exception as exc:
        if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
            return _simulation_error(SimulationError("invalid_json", "无效 JSON 请求体"))
        raise

@router.get("/simulation/status")
async def simulation_status(request: Request):
    try:
        status = await asyncio.to_thread(_simulation_store(request).status)
        return {"ok": True, **status}
    except SimulationError as exc:
        return _simulation_error(exc)


@router.get("/simulation/orders")
async def simulation_orders(request: Request, limit: int = 100, status: str | None = None):
    try:
        try:
            bounded = max(1, min(int(request.query_params.get("limit", limit)), 500))
        except (AttributeError, TypeError, ValueError):
            bounded = 100
        status_filter = request.query_params.get("status", status)
        rows = await asyncio.to_thread(
            _simulation_store(request).orders,
            limit=bounded,
            status=status_filter,
        )
        return {"ok": True, "simulation_only": True, "items": rows, "count": len(rows)}
    except SimulationError as exc:
        return _simulation_error(exc)


@router.post("/simulation/orders")
async def simulation_propose_order(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise SimulationError("invalid_json", "请求体必须是 JSON 对象")
        symbol = str(body.get("symbol") or "").strip()
        authority = await _simulation_server_snapshot(request, symbol, include_risk=True)
        result = await asyncio.to_thread(
            _simulation_store(request).propose,
            client_request_id=body.get("client_request_id"),
            symbol=symbol,
            side=body.get("side"),
            order_type=body.get("order_type"),
            quantity=body.get("quantity"),
            reference_price=authority["market_price"],
            limit_price=body.get("limit_price"),
            risk_level=authority["risk_level"],
            data_as_of=authority["as_of"],
            data_stale=authority["stale"],
            data_missing=authority["missing"],
            price_mode=authority["price_mode"],
            trade_date=authority["trade_date"],
            valid_trade_date=order_valid_trade_date(now_cn()),
        )
        return {"ok": True, "simulation_only": True, **result}
    except SimulationError as exc:
        return _simulation_error(exc)
    except Exception as exc:  # malformed JSON only; do not expose parser details
        if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
            return _simulation_error(SimulationError("invalid_json", "无效 JSON 请求体"))
        raise


@router.post("/simulation/orders/{order_id}/approve")
async def simulation_approve_order(order_id: str, request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise SimulationError("invalid_json", "请求体必须是 JSON 对象")
        result = await asyncio.to_thread(
            _simulation_store(request).approve,
            order_id,
            client_request_id=body.get("client_request_id"),
            approved_by=body.get("approved_by"),
        )
        return {"ok": True, "simulation_only": True, **result}
    except SimulationError as exc:
        return _simulation_error(exc)
    except Exception as exc:
        if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
            return _simulation_error(SimulationError("invalid_json", "无效 JSON 请求体"))
        raise


@router.post("/simulation/orders/{order_id}/fill")
async def simulation_fill_order(order_id: str, request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise SimulationError("invalid_json", "请求体必须是 JSON 对象")
        simulation = _simulation_store(request)
        order = await asyncio.to_thread(simulation.get_order, order_id)
        authority = await _simulation_server_snapshot(
            request, str(order.get("symbol") or ""), include_risk=False,
        )
        valuation_quotes = await _simulation_account_quotes(request)
        result = await asyncio.to_thread(
            simulation.fill,
            order_id,
            client_request_id=body.get("client_request_id"),
            market_price=authority["market_price"],
            as_of=authority["as_of"],
            market_source=authority["source_id"],
            market_stale=authority["stale"],
            market_missing=authority["missing"],
            price_mode=authority["price_mode"],
            trade_date=authority["trade_date"],
            unlock_trade_date=(
                next_trade_day(date.fromisoformat(authority["trade_date"]))
                if order.get("side") == "buy" else None
            ),
            valuation_quotes=valuation_quotes,
        )
        return {"ok": True, "simulation_only": True, **result}
    except SimulationError as exc:
        return _simulation_error(exc)
    except Exception as exc:
        if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
            return _simulation_error(SimulationError("invalid_json", "无效 JSON 请求体"))
        raise


@router.post("/simulation/orders/{order_id}/cancel")
async def simulation_cancel_order(order_id: str, request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise SimulationError("invalid_json", "请求体必须是 JSON 对象")
        result = await asyncio.to_thread(
            _simulation_store(request).cancel,
            order_id,
            client_request_id=body.get("client_request_id"),
            reason=body.get("reason", "operator_cancelled"),
        )
        return {"ok": True, "simulation_only": True, **result}
    except SimulationError as exc:
        return _simulation_error(exc)
    except Exception as exc:
        if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
            return _simulation_error(SimulationError("invalid_json", "无效 JSON 请求体"))
        raise


@router.post("/simulation/emergency-stop")
async def simulation_emergency_stop(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise SimulationError("invalid_json", "请求体必须是 JSON 对象")
        result = await asyncio.to_thread(
            _simulation_store(request).emergency_stop,
            client_request_id=body.get("client_request_id"),
            reason=body.get("reason"),
        )
        return {"ok": True, "simulation_only": True, **result}
    except SimulationError as exc:
        return _simulation_error(exc)
    except Exception as exc:
        if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
            return _simulation_error(SimulationError("invalid_json", "无效 JSON 请求体"))
        raise


@router.post("/simulation/emergency-stop/reset")
async def simulation_emergency_stop_reset(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise SimulationError("invalid_json", "请求体必须是 JSON 对象")
        result = await asyncio.to_thread(
            _simulation_store(request).reset_emergency_stop,
            client_request_id=body.get("client_request_id"),
            approved_by=body.get("approved_by"),
            reason=body.get("reason"),
        )
        return {"ok": True, "simulation_only": True, **result}
    except SimulationError as exc:
        return _simulation_error(exc)
    except Exception as exc:
        if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError)):
            return _simulation_error(SimulationError("invalid_json", "无效 JSON 请求体"))
        raise


def _relay_health() -> dict:
    """境内数据隧道健康摘要（供顶栏告警条），不额外发请求：只读 relay 环境与最近一次中台元数据。
    enabled=配置了 DATA_RELAY_PROXY 或 DATA_RELAY_API；ok=最近一次中台/spot 是否成功；
    stale=最近快照为陈旧缓存。未配置中继时 enabled=false（前端不显示告警）。"""
    enabled = bool(relay_proxy()) or bool(market._relay_api())
    rmeta = market.last_relay_meta()
    return {
        "enabled": enabled,
        # 未配中继或尚无任何中台调用记录时，ok 取 enabled 取反语义不成立，按"无异常"处理：
        # 默认 ok=True，仅当确有一次中台调用且其 ok 为 False 时才报异常，避免冷启动误报告警。
        "ok": bool(rmeta.get("ok", True)),
        "stale": bool(rmeta.get("stale", False)),
    }


@router.get("/jobs")
async def get_jobs(request: Request):
    """任务中心独立契约：无需发起 Hermes 连接探测即可刷新运行状态。"""
    st = _state(request)
    return {"ok": True, **task_center_status(st.scheduler, st.config, st.store)}


@router.post("/jobs/{job_id}/run")
async def run_job(job_id: str, request: Request):
    st = _state(request)
    job = st.scheduler.get_job(job_id)
    if job is None:
        return {"ok": False, "error": f"未知任务: {job_id}"}
    async def _invoke_registered_job():
        result = job.func(*tuple(getattr(job, "args", ()) or ()), **dict(getattr(job, "kwargs", {}) or {}))
        if inspect.isawaitable(result):
            return await result
        return result
    # 持引用防 GC 在任务完成前回收（CPython fire-and-forget footgun）；完成即从集合移除
    # core 任务走统一 run_job；ext/system 任务要执行 APScheduler 已登记的包装函数，
    # 否则把 ext_xxx 误传给 core 报告生成器会出现“未知报告类型”。
    run_id = f"run_{uuid.uuid4().hex}"
    _job_runs[run_id] = {
        "run_id": run_id, "job_id": job_id, "status": "queued",
        "started_at": now_cn().isoformat(), "finished_at": None, "result": None,
    }

    async def _tracked_run() -> None:
        _job_runs[run_id]["status"] = "running"
        try:
            result = await (st.scheduler.run_job(job_id) if job_id in JOB_NAMES else _invoke_registered_job())
            safe_result = result if isinstance(result, dict) else {"ok": True}
            _job_runs[run_id]["result"] = safe_result
            _job_runs[run_id]["status"] = "completed" if safe_result.get("ok", True) else "failed"
        except Exception:  # noqa: BLE001
            log.exception("手动任务执行异常 job=%s run=%s", job_id, run_id)
            _job_runs[run_id]["status"] = "failed"
            _job_runs[run_id]["result"] = {"ok": False, "error_code": "job_execution_failed"}
        finally:
            _job_runs[run_id]["finished_at"] = now_cn().isoformat()

    t = asyncio.create_task(_tracked_run())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return {"ok": True, "msg": "已触发", "run_id": run_id, "status": "queued"}


@router.get("/jobs/runs/{run_id}")
async def get_job_run(run_id: str):
    row = _job_runs.get(run_id)
    if row is None:
        return JSONResponse({"ok": False, "error": "任务运行记录不存在"}, status_code=404)
    return {"ok": True, **row}


# ---------------------------------------------------------------- 市场数据

@router.get("/market/overview")
async def overview():
    return await asyncio.to_thread(market.market_overview)


@router.get("/market/indices")
async def indices_light():
    """指数点位轻量快照（3s 缓存，只打腾讯批量指数）。供前端盘中 ~3s 轮询做数字跳动，
    不触发 overview 的慢宽度/涨停计算 → 高频轮询安全。"""
    return await asyncio.to_thread(market.index_quotes_light)


@router.get("/market/overview_cached")
async def overview_cached():
    """秒回版 overview：命中 ttl_cache 即返回实时值；进程刚起 ttl_cache 还空、网络未就绪的
    极窄窗口里回退启动快照（已标 stale/snapshot_age）。供前端首屏即时出图，后续刷新仍用 /market/overview。"""
    from ..jobs.prewarmer import overview_with_snapshot  # noqa: PLC0415

    ov = await asyncio.to_thread(overview_with_snapshot)
    return ov or {"ok": False, "missing": ["overview 暂不可用(预热未完成且无启动快照)"]}


@router.get("/market/limitup")
async def limitup():
    return await asyncio.to_thread(market.market_limitup)


@router.get("/market/limitup/themes")
async def limitup_themes(date: str = ""):
    """涨停题材明细：同花顺公开池独立口径，不与主涨停池数字混算。"""
    dc = str(date or "").strip() or public_data.quote_data_date().replace("-", "")
    if not (len(dc) == 8 and dc.isdigit()):
        return {
            "ok": False,
            "date": dc,
            "data_date": public_data.quote_data_date(),
            "missing": ["日期格式应为 YYYYMMDD"],
            "stocks": [],
        }
    return await asyncio.to_thread(public_data.ths_limit_up_pool, dc)


@router.get("/market/limitup-tracker/dates")
async def limitup_tracker_dates(request: Request, limit: int = 20):
    """最近真实交易日与快照可用状态；日期只由服务端日历/已有快照给出。"""
    tracker = _state(request).limitup_tracker
    return await asyncio.to_thread(tracker.dates, max(1, min(limit, 20)))


@router.get("/market/limitup-tracker")
async def limitup_tracker_snapshot(request: Request, date: str = ""):
    """指定涨停基准日的同批次 T+1/T+2 冻结或盘中观察结果。"""
    tracker = _state(request).limitup_tracker
    selected = str(date or "").strip()
    if not selected:
        dates = await asyncio.to_thread(tracker.dates, 20)
        selected = str(dates.get("default_date") or "")
    if not _DATE_RE.fullmatch(selected):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "日期格式应为 YYYY-MM-DD", "selected_date": selected},
        )
    result = await asyncio.to_thread(tracker.snapshot, selected)
    return JSONResponse(status_code=200 if result.get("ok") else 400, content=result)


@router.post("/market/limitup-tracker/backfill")
async def limitup_tracker_backfill(request: Request):
    """异步补齐最近20日基准池及已经到期的 T+1/T+2；重复触发复用活动任务。"""
    state = _state(request)
    registry = state.limitup_backfill_jobs
    active_id = registry.get("active")
    active = (registry.get("jobs") or {}).get(active_id) if active_id else None
    if active and active.get("status") == "running":
        return JSONResponse(status_code=202, content={**active, "already_running": True})

    job_id = uuid.uuid4().hex
    started_at = now_cn().isoformat(timespec="seconds")
    job = {
        "ok": True,
        "job_id": job_id,
        "status": "running",
        "started_at": started_at,
        "message": "正在补齐最近20个交易日的真实快照与到期结果",
    }
    registry.setdefault("jobs", {})[job_id] = job
    registry["active"] = job_id

    async def _run() -> None:
        try:
            result = await asyncio.to_thread(state.limitup_tracker.backfill_recent, 20)
            registry["jobs"][job_id] = {**job, **result, "job_id": job_id}
        except Exception as exc:  # noqa: BLE001 -- 任务失败必须可轮询，不影响 API 进程
            log.exception("涨停追踪补数据失败")
            registry["jobs"][job_id] = {
                **job,
                "ok": False,
                "status": "failed",
                "reason": str(exc)[:240],
                "finished_at": now_cn().isoformat(timespec="seconds"),
                "job_id": job_id,
            }
        finally:
            if registry.get("active") == job_id:
                registry["active"] = None

    task = asyncio.create_task(_run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return JSONResponse(status_code=202, content=job)


@router.get("/market/limitup-tracker/backfill/{job_id}")
async def limitup_tracker_backfill_status(request: Request, job_id: str):
    job = (_state(request).limitup_backfill_jobs.get("jobs") or {}).get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"ok": False, "status": "failed", "reason": "补数据任务不存在或服务已重启"})
    return job


@router.get("/market/sectors")
async def sectors():
    return await asyncio.to_thread(market.market_sectors)


@router.get("/market/mainlines")
async def market_mainlines(request: Request, n: int = 5):
    """今日行业结构：主线、次主线、退潮活口与涨停情绪链。

    以成交容量、相对强度、上涨宽度和涨停梯队共同判断；净流入权重为0。
    日期不一致、行业映射不明或跌停明细未落库时只标 missing，不跨日、
    不模糊匹配、不用家数反推明细。
    """
    limit = max(1, min(n, 30))
    cached = await asyncio.to_thread(
        mainline_runtime.load_current_snapshot,
        _state(request).config.data_dir,
        limit=limit,
    )
    if cached is not None:
        return cached
    response = await mainline_runtime.build_current_response(limit)
    try:
        result = await asyncio.to_thread(
            mainline_timeline.observe_response,
            _state(request).config.data_dir,
            response,
            now_cn(),
        )
        await asyncio.to_thread(
            mainline_runtime.save_current_snapshot,
            _state(request).config.data_dir,
            result,
        )
        return result
    except Exception:  # noqa: BLE001 -- 时间轴不得阻断当日截面主线结果
        log.exception("主线时间轴落盘失败，回退无历史注解")
        return mainline_timeline.annotate_response(response, [], now_cn())


@router.get("/market/top")
async def top():
    return await asyncio.to_thread(market.market_top)


@router.get("/market/stockflow")
async def stockflow(n: int = 6):
    """个股资金 净流入/净流出 TOP（首页卡）：主力净额降序前 n / 升序前 n。
    净额单位元、change_pct %，来自免费公开资金榜；取不到 → 空列表+missing(不编造)。"""
    return await asyncio.to_thread(market.market_stockflow, max(1, min(n, 30)))


@router.get("/market/multiperiod")
async def multiperiod(n: int = 12):
    """多周期涨幅榜（首页卡）：当日榜前 n 标的的 1/3/5/20 日累计涨幅(%)。
    周期值来自腾讯免费前复权日线，数据不足/不可用 → 该周期 None + missing(绝不编造)。"""
    return await asyncio.to_thread(market.market_multiperiod, max(1, min(n, 20)))


@router.get("/market/flow")
async def flow():
    return await asyncio.to_thread(market.market_flow)


# ---- 资金持续追踪（头 A：Web UI 取数；与 MCP Server 头共享同一引擎/库）----

@router.get("/capital/trends")
async def capital_trends(days: int = 1):
    """资金追踪综合：top 板块 + 热门股的日内主力资金时序（前端画"净流入曲线"，对照图2）。"""
    return await asyncio.to_thread(capital_tracker.trends, days)


@router.get("/capital/sectors")
async def capital_sectors():
    """最新一次快照的 top 板块主力资金排名。"""
    return await asyncio.to_thread(capital_tracker.latest_sectors)


@router.get("/capital/hot")
async def capital_hot():
    """最新一次快照的热门股主力资金排名。"""
    return await asyncio.to_thread(capital_tracker.latest_hot_stocks)


@router.get("/capital/sector/{name}/history")
async def capital_sector_history(name: str, days: int = 5):
    """某板块主力资金时间序列（趋势）。"""
    return await asyncio.to_thread(capital_tracker.sector_history, name, days)


@router.get("/capital/stock/{code}/history")
async def capital_stock_history(code: str, days: int = 5):
    """某股主力资金时间序列（趋势）。"""
    return await asyncio.to_thread(capital_tracker.hot_stock_history, code, days)


@router.post("/capital/snapshot")
async def capital_snapshot():
    """手动触发一次资金采集入库（通常由定时任务自动执行）。"""
    return await asyncio.to_thread(capital_tracker.snapshot)


@router.get("/index/{full_code}")
async def index_kline(full_code: str):
    if not re.match(r"^(sh|sz)\d{6}$", full_code):
        return {"ok": False, "error": "指数代码格式如 sh000001", "missing": ["代码格式"]}
    return await asyncio.to_thread(market.index_kline, full_code)


# ---------------------------------------------------------------- 情绪

# 评分定格公共入口（T9 §0-3 防跳变）：pulse 档快照 = 全站唯一真值源的定格版
# （阻尼+惯性在 pulse_core 内做，一处算、/pulse /cockpit /sysrisk /报告快照 四处同源同值）。
async def _pulse_locked(store):
    from ..analysis import score_lock  # noqa: PLC0415
    snapshot = await score_lock.locked(
        store.root, "pulse",
        lambda prev: asyncio.to_thread(cockpit_mod.pulse_core, store, prev))
    # score_lock 会读回当前 10 分钟档的持久快照。即使快照是旧代码
    # 写入，每次返回前也必须重新套「此刻是否可交易」的时态门禁；
    # 否则新交易日凌晨仍会在档内返回昨日的「可买」。
    if isinstance(snapshot, dict) and isinstance(snapshot.get("command"), dict):
        snapshot = dict(snapshot)
        guarded = cockpit_mod.apply_decision_time_guard(
            snapshot["command"], {"data_date": snapshot.get("trade_date")}
        )
        snapshot["command"] = guarded
        snapshot["decision_state"] = guarded.get("decision_state")
        snapshot["decision_tradeable"] = bool(guarded.get("decision_tradeable"))
        snapshot["market_session"] = guarded.get("market_session")
    return snapshot


@router.get("/sentiment/today")
async def sentiment_today(request: Request):
    """评分定格：情绪评分按 10 分钟档算一次、锁定，档内刷新不变（治"盯着看就跳"）。"""
    st = _state(request)
    from ..analysis import score_lock  # noqa: PLC0415
    return await score_lock.locked(
        st.store.root, "sentiment",
        lambda prev: asyncio.to_thread(senti.compute_today, st.store))


@router.get("/sentiment/history")
async def sentiment_history(request: Request, days: int = 30):
    st = _state(request)
    return {"ok": True, "days": st.store.sentiment_history(max(1, min(days, 250)))}


# ---------------------------------------------------------------- 今日驾驶舱
@router.get("/cockpit")
async def cockpit(request: Request):
    """首页今日交易驾驶舱·顶部总指令 + 今日总判断（卡片①）。
    风险等级/仓位上限/允许·禁止动作走确定性规则引擎(母文档§6.3/§10.4)，数字全来自情绪+大盘真实字段。
    评分定格：整块总指令按 10 分钟档定格（先取 pulse 档快照保证与 /pulse /sysrisk 同源同值）。"""
    st = _state(request)
    from ..analysis import score_lock  # noqa: PLC0415

    async def _compute(prev):  # noqa: ARG001 —— cockpit 的惯性在 pulse 档内做，此处无需 prev
        p = await _pulse_locked(st.store)
        return await asyncio.to_thread(cockpit_mod.cockpit, st.store, p if isinstance(p, dict) else None)

    resp = await score_lock.locked(st.store.root, "cockpit", _compute)
    if isinstance(resp, dict) and isinstance(resp.get("command"), dict):
        resp = dict(resp)
        # 旧版本留下的 10 分钟定格缓存可能尚无 data_date；只在字段缺失时
        # 使用与 market_overview 完全同口径的本地交易日推导，不为门禁再打一轮外部源。
        data_date = resp.get("data_date") or market.decision_data_date()
        resp["data_date"] = data_date
        resp["command"] = cockpit_mod.apply_decision_time_guard(
            resp["command"], {"data_date": data_date}
        )
    # AI 叙述层（T9 §1：过程+结果，客户要知道"为啥这么做"）：秒回+后台重算(A方案)；
    # AI 失败/未就绪 → 不合并，前端自动回退规则版文案，页面绝不空窗。
    narr = await score_lock.locked_swr(st.store.root, "narrate", st.engine.cockpit_narrate)
    if (
        isinstance(resp, dict)
        and isinstance(narr, dict)
        and narr.get("ok")
        and resp.get("command")
        and resp["command"].get("decision_tradeable") is not False
    ):
        resp = dict(resp)
        resp["command"] = _merge_cockpit_narrative(resp["command"], narr)
    return resp


@router.get("/pulse")
async def get_pulse(request: Request):
    """唯一真值源(T9 §0-4)：情绪温度(纯公式 ratio×60+ztdt×40)+风险等级+regime，一处算多处取。
    评分定格：10 分钟档 + 等级阻尼(±1,黑天鹅例外) + 观点惯性(change)，档内刷新不变。"""
    st = _state(request)
    return await _pulse_locked(st.store)


@router.get("/sysrisk")
async def get_sysrisk(request: Request):
    """系统性风险五维(T9 §3)：大盘技术/资金面/情绪面/消息面/海外 每维真实读数+结论+贡献+triggers；
    等级/regime/change 取 pulse 档快照(阻尼+惯性后)，绝不 AI 拍脑袋定级。整卡 10 分钟档定格。"""
    st = _state(request)
    from ..analysis import score_lock  # noqa: PLC0415

    async def _compute(prev):  # noqa: ARG001 —— 等级惯性已在 pulse 档内做
        p = await _pulse_locked(st.store)
        return await asyncio.to_thread(cockpit_mod.sysrisk, st.store, p if isinstance(p, dict) else None)

    # 风险融合卡 v1：换缓存键避免收盘档继续返回旧 schema。
    resp = await score_lock.locked(st.store.root, "sysrisk_card_v1", _compute)
    # AI 叙述层（T9 §3：盘面推理+操作结论段落）：与 /cockpit 共用同一档 narrate 快照（同源）。
    narr = await score_lock.locked_swr(st.store.root, "narrate", st.engine.cockpit_narrate)
    if (isinstance(resp, dict) and isinstance(narr, dict) and narr.get("ok")
            and _narrative_is_safe(narr) and (narr.get("盘面推理") or narr.get("操作结论"))):
        resp = dict(resp)
        resp["narrative"] = {"盘面推理": narr.get("盘面推理", ""), "操作结论": narr.get("操作结论", ""),
                             "定格时点": narr.get("定格时点", "")}
    return resp


@router.post("/risk/evaluate")
async def evaluate_risk(request: Request):
    """纯规则风险闸调试/审计端点：同一输入始终得到同一决策与 hash。"""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
        result = evaluate_risk_gate(body)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "风险闸输入不合法"}, status_code=400)
    return {"ok": True, "risk_decision": result}


# ---------------------------------------------------------------- 统一行情证据网关

@router.get("/market-data/contract")
async def market_data_contract(request: Request):
    """字段含义、单位、主备路由、新鲜度门槛与批次口径。"""
    gateway = getattr(_state(request), "market_gateway", None)
    if gateway is None:
        return JSONResponse(
            {"ok": False, "error": "统一行情证据网关尚未初始化"}, status_code=503
        )
    return await asyncio.to_thread(gateway.contract)


@router.get("/market-data/{symbol}/{field}")
async def market_data_field(
    symbol: str,
    field: str,
    request: Request,
    as_of: str | None = None,
):
    """字段级行情契约；``as_of`` 查询只读本地证据库、绝不回打实时源。"""
    gateway = getattr(_state(request), "market_gateway", None)
    if gateway is None:
        return JSONResponse(
            {"ok": False, "error": "统一行情证据网关尚未初始化"}, status_code=503
        )
    if field not in getattr(gateway, "policies", {}):
        return JSONResponse(
            {"ok": False, "error": f"未知标准字段: {field}"}, status_code=404
        )
    if as_of is not None:
        try:
            parsed_as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return JSONResponse(
                {"ok": False, "error": "as_of 必须是带时区的 ISO 时点"},
                status_code=400,
            )
        if parsed_as_of.tzinfo is None or parsed_as_of.utcoffset() is None:
            return JSONResponse(
                {"ok": False, "error": "as_of 必须包含 Z 或 UTC offset"},
                status_code=400,
            )
    try:
        # PIT 读取必须绝对离线：as_of 分支不计算市场时段，
        # 因为 build_market_context -> trade_dates 冷启可能请求腾讯交易日历。
        session = (
            "intraday"
            if as_of is not None
            else (await asyncio.to_thread(build_market_context))["market_session"]
        )
        point = await asyncio.to_thread(
            gateway.get,
            symbol,
            field,
            session=session,
            as_of=as_of,
        )
    except KeyError:
        return JSONResponse(
            {"ok": False, "error": f"未知标准字段: {field}"}, status_code=404
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": point.quality_status != "missing", "data": point.to_dict()}


@router.get("/evidence/sources")
async def evidence_sources(request: Request):
    store = getattr(_state(request), "evidence_store", None)
    if store is None:
        return {"ok": False, "sources": [], "error": "证据库尚未初始化"}
    rows = await asyncio.to_thread(store.list_sources)
    return {"ok": True, "sources": rows, "count": len(rows)}


@router.get("/evidence/stats")
async def evidence_stats(request: Request):
    st = _state(request)
    store = getattr(st, "evidence_store", None)
    if store is None:
        return {"ok": False, "error": "证据库尚未初始化"}
    result = {"ok": True, **(await asyncio.to_thread(store.stats))}
    gateway = getattr(st, "market_gateway", None)
    if gateway is not None:
        result["runtime"] = await asyncio.to_thread(gateway.health)
    return result


@router.get("/evidence/points")
async def evidence_points(request: Request, symbol: str = "", field: str = "", limit: int = 100):
    store = getattr(_state(request), "evidence_store", None)
    if store is None:
        return {"ok": False, "points": [], "error": "证据库尚未初始化"}
    rows = await asyncio.to_thread(
        store.list_points,
        symbol=symbol.strip() or None,
        field=field.strip() or None,
        limit=limit,
    )
    return {"ok": True, "points": rows, "count": len(rows)}


@router.get("/evidence/{evidence_id}")
async def evidence_detail(evidence_id: str, request: Request, include_raw: bool = False):
    store = getattr(_state(request), "evidence_store", None)
    if store is None:
        return JSONResponse({"ok": False, "error": "证据库尚未初始化"}, status_code=503)
    point = await asyncio.to_thread(store.get_point, evidence_id)
    if point is None:
        return JSONResponse({"ok": False, "error": "证据不存在"}, status_code=404)
    lineage = await asyncio.to_thread(store.get_lineage, evidence_id)
    source = await asyncio.to_thread(store.get_source, point["source_id"])
    parents = []
    for link in lineage:
        parent = await asyncio.to_thread(store.get_point, link["parent_id"])
        parents.append({**link, "point": parent})
    payload = None
    if include_raw and point.get("raw_payload_hash"):
        client_host = str(getattr(getattr(request, "client", None), "host", "") or "")
        raw_enabled = os.environ.get("HERMES_ALLOW_RAW_EVIDENCE", "").strip() == "1"
        if not raw_enabled or client_host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse(
                {"ok": False, "error": "原始载荷只在本机显式开启调试开关后可读"},
                status_code=403,
            )
        payload = await asyncio.to_thread(store.get_raw_payload, point["raw_payload_hash"])
    return {
        "ok": True,
        "point": point,
        "source": source,
        "lineage": parents,
        **({"raw_payload": payload} if include_raw else {}),
    }


# ---------------------------------------------------------------- 个股

@router.get("/stock/{code}")
async def stock_detail(code: str):
    """已弃用的基础快照兼容端点；不聚合增强层。"""
    if not _CODE_RE.match(code):
        return {"ok": False, "error": "股票代码应为 6 位数字", "missing": ["代码格式"]}
    return await asyncio.to_thread(stock.stock_detail, code)


@router.get("/stock/{code}/core")
async def stock_core(code: str):
    """个股首屏：报价、基础指标、最近日 K；不等待任何增强源。"""
    if not _CODE_RE.match(code):
        return JSONResponse(
            {"ok": False, "error_code": "invalid_code", "error": "股票代码应为 6 位数字"},
            status_code=400,
        )
    return await asyncio.to_thread(stock.stock_core, code)


@router.get("/stock/{code}/charts")
async def stock_charts(code: str):
    """个股图表：分时与前复权日 K 分段返回。"""
    if not _CODE_RE.match(code):
        return JSONResponse(
            {"ok": False, "error_code": "invalid_code", "error": "股票代码应为 6 位数字"},
            status_code=400,
        )
    return await asyncio.to_thread(stock.stock_charts, code)


@router.get("/stock/{code}/enrichment")
async def stock_enrichment(code: str):
    """个股增强：行业概念、资金流、基础财务；失败不影响首屏。"""
    if not _CODE_RE.match(code):
        return JSONResponse(
            {"ok": False, "error_code": "invalid_code", "error": "股票代码应为 6 位数字"},
            status_code=400,
        )
    return await asyncio.to_thread(stock.stock_enrichment, code)


@router.get("/stock/{code}/research-enrichment")
async def stock_research_enrichment(code: str):
    """盘后个股研究增强；低频数据不进入日内问股/主线评分。"""
    if not _CODE_RE.match(code):
        return {"ok": False, "error": "股票代码应为 6 位数字", "missing": ["代码格式"]}
    return await asyncio.to_thread(tdx_api.after_hours_research, code)


@router.get("/quote/{code}")
async def quick_quote(code: str):
    """单票兼容入口：TDX API 主、腾讯备。"""
    if not _CODE_RE.match(code):
        return JSONResponse(
            {"ok": False, "error_code": "invalid_code", "error": "股票代码应为 6 位数字"},
            status_code=400,
        )
    q = await asyncio.to_thread(stock.quick_quote, code)
    identity = await asyncio.to_thread(public_data.cached_stock_identity, code)
    if identity.get("universe_complete") and not identity.get("known"):
        return {
            "ok": False,
            "state": "not_found",
            "recognized": False,
            "error_code": "stock_not_found",
            "error": "该代码不在当前完整 A 股名录中",
            "identity": identity,
            "quote": None,
        }
    if q:
        q = dict(q)
        if not q.get("name") and identity.get("name"):
            q["name"] = identity["name"]
        return {
            "ok": True, "state": "complete", "recognized": True,
            "identity": identity, "quote": q,
        }
    if identity.get("known"):
        return {
            "ok": False,
            "state": "quote_unavailable",
            "recognized": True,
            "error_code": "quote_unavailable",
            "error": "股票已识别，但当前报价源暂未返回",
            "identity": identity,
            "quote": None,
        }
    return {
        "ok": False,
        "state": "quote_unavailable",
        "recognized": None,
        "error_code": "quote_unavailable",
        "error": "当前报价源暂未返回，且名录不完整，未判定为不存在",
        "identity": identity,
        "quote": None,
    }


@router.get("/quotes")
async def quick_quotes(codes: str = ""):
    """持仓/自选批量秒级报价；多只合并一次 TDX API 调用，不逐只浪费限额。"""
    items = list(dict.fromkeys(part.strip() for part in str(codes or "").split(",") if part.strip()))
    if not items or len(items) > _MAX_HOLDINGS or any(not _CODE_RE.match(code) for code in items):
        return JSONResponse(
            {"ok": False, "error": f"请提供1-{_MAX_HOLDINGS}个6位股票代码"}, status_code=400
        )
    rows = await asyncio.to_thread(stock.quick_quotes, items)
    return {"ok": bool(rows), "quotes": rows, "missing": [code for code in items if code not in rows]}


@router.get("/diagnose")
async def diagnose(code: str, request: Request):
    """个股诊断·证据链卡（结构化 JSON）：action 八态/狙击位/失效位/证据[出处]/missing。
    数字只来自服务端真实数据，AI 仅组织判断；未配模型时只取有预算的快行情，
    慢数据/模型失败均回退安全卡(观望/低)，绝不抛。"""
    code = (code or "").strip()
    if not _CODE_RE.match(code):
        return {"ok": False, "error": "股票代码应为 6 位数字", "missing": ["代码格式"]}
    st = _state(request)
    card = await _governed_and_record_diagnosis(st, code)
    if card.get("recognized") is False or card.get("error_code") == "stock_not_found":
        return JSONResponse({"ok": False, **card}, status_code=404)
    return {"ok": True, **card}


async def _governed_and_record_diagnosis(
    st,
    code: str,
    *,
    source: str = "问股",
    data_material_ids: tuple[str, ...] | None = None,
) -> dict:
    """生成结构化诊断，并在返回任何正式动作前完成统一建议留痕。"""
    diagnosis_kwargs = {}
    if data_material_ids is not None:
        try:
            if "data_material_ids" in inspect.signature(governed_diagnosis_card).parameters:
                diagnosis_kwargs["data_material_ids"] = data_material_ids
        except (TypeError, ValueError):
            diagnosis_kwargs["data_material_ids"] = data_material_ids
    card = await governed_diagnosis_card(st, code, **diagnosis_kwargs)
    if card.get("recognized") is False or card.get("error_code") == "stock_not_found":
        return card
    # 结构化诊断是正式动作入口：返回客户前先写 proposal，并仅在当前价证据和
    # 已冻结 RiskGate 回执完整时晋级 RecommendationEvent。
    price_row = next((
        row for row in (card.get("decision_market_evidence") or [])
        if isinstance(row, dict) and row.get("field") == "last_price"
    ), None)
    price_quality = str((price_row or {}).get("quality_status") or "")
    # degraded 可以用来解释「为什么观望」，但不能当作一条
    # 可直接绑定推荐价的新鲜证据。之前 stale 点会在统一入口
    # 再次校验时抛 ValueError，导致卡片成功但留痕报 ERROR。
    price_bindable = bool(price_row and price_quality == "ok")
    price_evidence_id = (price_row or {}).get("evidence_id") if price_bindable else None
    action = str(card.get("action") or "观望")
    try:
        advice_record = await _authorize_and_record_advice(
            st,
            {
                "code": code,
                "name": card.get("name"),
                "source": source,
                "direction": action,
                "rec_price": (price_row or {}).get("value") if price_bindable else None,
                # 冲突点可以作为“为什么观望”的证据，但不能被伪装成已通过
                # 推荐价绑定的单一真值。不给 requested_id 后，统一入口会再查
                # 当前网关；仍冲突就只记录观察 proposal，不抛异常、不造战绩。
                "price_evidence_id": price_evidence_id,
                "position_cap_pct": card.get("position_cap_pct"),
                "confidence": card.get("confidence"),
                "client_request_id": (
                    f"diagnosis:{source}:{code}:{card.get('decision_generated_at') or now_cn().isoformat(timespec='seconds')}"
                ),
                "source_artifact": {
                    "kind": "diagnosis_card" if source == "问股" else "holding_diagnosis_card",
                    "decision_generated_at": card.get("decision_generated_at"),
                    "signal_rule_version": (card.get("deterministic_signal") or {}).get("rule_version"),
                    "evidence_rule_version": (card.get("evidence_validation") or {}).get("rule_version"),
                    "risk_input_hash": (card.get("risk_decision") or {}).get("input_snapshot_hash"),
                },
            },
            trusted_internal=True,
            preauthorized_risk=card.get("risk_decision"),
        )
        card["advice_record"] = advice_record
        if advice_record.get("blocked") and action in {"买入", "加仓"}:
            card["original_action"] = action
            card["action"] = "观望"
            card["summary"] = "建议留痕前的最终风险复核未通过，已改为观望。"
    except Exception:  # noqa: BLE001 — 留痕失败时新增风险必须 fail closed
        log.exception("结构化诊断建议留痕失败 code=%s", code)
        card["advice_record"] = {
            "ok": False,
            "recommendation_created": False,
            "error_code": "advice_record_failed",
        }
        if action in {"买入", "加仓"}:
            card["original_action"] = action
            card["action"] = "观望"
            card["position_cap_pct"] = 0.0
            card["summary"] = "建议留痕未完成，已暂停新增风险；请稍后重试。"
    return card


@router.get("/ext/views")
async def ext_views(request: Request):
    """子项目扩展视图的完整注册表。

    列表在服务端已经先完整发现，再统一标注 visible/hidden/retired/redirect；
    ``hidden`` 的模块仍应注册，只是不进导航，从而不受 /status 与动态 import 返回顺序影响。
    """
    return getattr(request.app.state, "ext_views", []) or []


@router.get("/xverify")
async def xverify(code: str):
    """双源交叉验证 + 溯源徽标：?code=000001 给个股实时价交叉核对，?code=sh000001 走指数。
    校验失败如实返回 ok=False+missing，不抛。"""
    code = (code or "").strip()
    if re.match(r"^(sh|sz)\d{6}$", code):
        return await asyncio.to_thread(xverify_mod.xverify_index, code)
    if _CODE_RE.match(code):
        return await asyncio.to_thread(xverify_mod.xverify_quote, code)
    return {"ok": False, "error": "code 需为 6 位个股代码或 sh/sz 前缀指数代码（如 000001 / sh000001）", "missing": ["代码格式"]}


# ---------------------------------------------------------------- 美股 / 港股 个股+财务

_MARKETS = {"us", "hk"}


@router.get("/overseas/quote")
async def overseas_quote(market: str, code: str):
    """港/美个股实时行情（腾讯 qt.gtimg.cn，独立于东财，客户机可达）。
    market=us|hk，code 形如 AAPL / 00700（港股可省零，后端补齐）。
    取不到/代码非法如实返回 ok=False+missing，不抛、不编造。"""
    market = (market or "").strip().lower()
    if market not in _MARKETS:
        return {"ok": False, "error": "market 需为 us 或 hk", "missing": ["市场参数"]}
    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": "code 不能为空", "missing": ["代码"]}
    return await asyncio.to_thread(overseas.quote, market, code)


@router.get("/overseas/financials")
async def overseas_financials(market: str, code: str):
    """港/美个股基础财务（东财源：营收/净利/EPS/毛利率/ROE/资产负债率…含同比）。
    客户机东财被封时取不到 → ok=False+missing（绝不用行情冒充财务）。"""
    market = (market or "").strip().lower()
    if market not in _MARKETS:
        return {"ok": False, "error": "market 需为 us 或 hk", "missing": ["市场参数"]}
    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": "code 不能为空", "missing": ["代码"]}
    return await asyncio.to_thread(overseas.financials, market, code)


# ---------------------------------------------------------------- 期货 / 期权

@router.get("/deriv/futures")
async def deriv_futures():
    """股指期货主力(IF/IC/IH/IM)+商品期货主力概览（新浪主力连续日线为基，盘中实时盘口覆盖）。
    取不到的合约记入 missing，绝不编造、绝不拼口径；全失败 ok=False。"""
    return await asyncio.to_thread(deriv_source.futures_overview)


@router.get("/deriv/options")
async def deriv_options():
    """50ETF / 300ETF / 科创50ETF 期权近月概览（上交所新浪通道：标的现价+T型行情）：
    认购/认沽合约数、PCR、ATM 平值认购/认沽权利金。取不到记入 missing，绝不编造。"""
    return await asyncio.to_thread(deriv_source.options_overview)


# ---------------------------------------------------------------- 主力吸筹 mainforce

@router.get("/mainforce/northbound")
async def mainforce_northbound():
    """北向资金（沪深港通）：今日北/南向净流入 + 近 N 日趋势。盘后数据，盘中取上一交易日。"""
    return await asyncio.to_thread(mainforce.northbound)


@router.get("/mainforce/longhu")
async def mainforce_longhu(limit: int = 30):
    """龙虎榜：个股上榜净买榜 + 机构专用席位净买榜（按股聚合）。盘后数据。"""
    return await asyncio.to_thread(mainforce.longhu, None, limit)


@router.get("/mainforce/longhu/official")
async def mainforce_official_longhu(date: str = ""):
    """读取指定交易日已落盘的沪深交易所龙虎榜快照。

    该路由只读本地快照，不发官网请求，也不用其它日期的快照回填。
    """
    clean_date = str(date or "").strip()
    if not _DATE_RE.fullmatch(clean_date):
        return JSONResponse(
            {"ok": False, "error_code": "invalid_date", "error": "date 应为 YYYY-MM-DD"},
            status_code=400,
        )
    try:
        normalized = datetime.strptime(clean_date, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return JSONResponse(
            {"ok": False, "error_code": "invalid_date", "error": "date 应为有效的 YYYY-MM-DD 日期"},
            status_code=400,
        )

    snapshot = await asyncio.to_thread(official_longhu_source.load_snapshot, normalized)
    if snapshot is None:
        return JSONResponse(
            {
                "ok": False,
                "state": "missing",
                "error_code": "official_snapshot_not_cached",
                "trade_date": normalized,
                "source": "official_snapshot_cache",
                "cache_only": True,
                "network_requested": False,
                "events": [],
                "missing": ["指定交易日的官方龙虎榜快照尚未落盘"],
            },
            status_code=404,
        )
    result = dict(snapshot)
    result.update({
        "source": "official_snapshot_cache",
        "cache_only": True,
        "network_requested": False,
    })
    return result


@router.get("/mainforce/industry")
async def mainforce_industry(limit: int = 30):
    """行业资金流榜（东财优先/同花顺兜底），按主力净额降序。盘后数据。"""
    return await asyncio.to_thread(mainforce.industry, None, limit)


# ---------------------------------------------------------------- 官方公告证据

@router.get("/disclosures/announcements")
async def disclosure_announcements(
    request: Request,
    code: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 20,
    verify_pdf: bool = False,
):
    """巨潮官网页面数据 + 公司所属交易所公告的独立交叉验证。

    这是官网内部查询接口的可替换 adapter，不声称为巨潮正式付费
    Web API。任一链路失败时返回 degraded/missing，不把失败写成空公告。
    """
    st = _state(request)
    clean_code = str(code or "").strip()
    today = now_cn().date()
    end = str(end_date or today.isoformat()).strip()
    start = str(start_date or (today - timedelta(days=30)).isoformat()).strip()
    cache_path = _Path(st.config.data_dir) / "official-disclosures" / f"{clean_code}.json"
    try:
        return await asyncio.to_thread(
            official_disclosure.cached_official_disclosures,
            clean_code,
            start,
            end,
            max(1, min(int(limit), 50)),
            bool(verify_pdf),
            1,
            str(cache_path),
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/interactions/management")
async def management_interactions(
    request: Request,
    code: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 6,
):
    """深交所互动易的投资者提问与管理层回复。

    这是官网内部页面 adapter，不是法定公告，也不参与日内评分。沪市/北交所
    当前明确返回 not_supported，不能被页面解释成“查询窗口内无回复”。
    """
    clean_code = str(code or "").strip()
    if not _CODE_RE.match(clean_code):
        return JSONResponse(
            {"ok": False, "error_code": "invalid_stock_code", "error": "code 应为 6 位 A 股代码"},
            status_code=400,
        )
    if not clean_code.startswith(("0", "3")):
        return {
            "ok": True,
            "code": clean_code,
            "state": "not_supported",
            "platform": "深交所互动易",
            "content_role": "交易所互动问答",
            "answer_role": "管理层回复",
            "is_formal_disclosure": False,
            "items": [],
            "degraded_reasons": ["当前仅覆盖深市互动易；沪市/北交所互动平台尚未接入"],
            "evidence": None,
        }
    st = _state(request)
    today = now_cn().date()
    end = str(end_date or today.isoformat()).strip()
    start = str(start_date or (today - timedelta(days=90)).isoformat()).strip()
    try:
        return await asyncio.to_thread(
            official_interaction.cached_official_interactions,
            clean_code,
            start,
            end,
            max(1, min(int(limit), 20)),
            str(st.config.data_dir),
        )
    except ValueError as exc:
        return JSONResponse(
            {"ok": False, "error_code": "invalid_interaction_query", "error": str(exc)},
            status_code=400,
        )


# ---------------------------------------------------------------- 业绩预告掘金（确定性规则）

@router.get("/forecast/mining")
async def forecast_mining_api(request: Request, period: str = ""):
    """中报/年报预告掘金榜：主板±50%阈值/扭亏 + 披露时点分层 + 基数陷阱沉底 + 扣非疑点标记。
    数字全部来自免费公开业绩预告汇总（元/%，供应方口径），规则判定、AI 不参与；
    公开源失败时只回放同客户、同报告期的显式 stale last-good，绝不编造。"""
    from ..analysis import forecast_mining  # noqa: PLC0415
    return await asyncio.to_thread(
        forecast_mining.mine,
        period or None,
        _state(request).config.data_dir,
    )


# ---------------------------------------------------------------- AI 对话（SSE）


def _stock_question_answer(message: str, card: dict) -> str:
    """先用确定性结论直答常见问法；后续模型只能在该结论边界内解释。"""
    question = str(message or "").strip()
    action = str(card.get("action") or "观望")
    signal_action = str(card.get("signal_action") or action)
    plan = card.get("trade_plan") or {}
    stop = plan.get("stop") or "价格锚点不足，暂不设交易动作"
    entry = plan.get("entry_trigger") or "等待价格与量能共振"
    if any(word in question for word in ("继续持有", "能持有", "要不要拿", "能拿")):
        # 用户明确询问「继续持有」时，即使这只股尚未录入
        # 本地持仓表，也必须用持仓管理语义回答。全市场风险
        # 只禁止新增风险，不能把技术面偏强的已有持仓错译成
        # 「必须立即退出」。
        if signal_action in {"买入", "加仓", "持有"} and action in {"观望", "回避"}:
            return f"已有仓位可以条件性持有，但当前不加仓；{stop}时应减仓或退出。"
        if action in {"持有", "加仓"}:
            return f"可以继续持有，但不是无条件死拿；{stop}时应减仓或退出。"
        if action == "减仓":
            return f"不建议继续满仓硬招，当前应减仓；{stop}时继续收缩。"
        if action in {"止损", "清仓", "回避"}:
            return f"不建议继续硬招，当前结论是「{action}」；重新评估需等{entry}。"
        return f"已有仓位可条件性观察，未有仓位不追；{stop}时应退出观察。"
    if any(word in question for word in ("尾盘", "收盘")):
        return f"尾盘只看两个条件：{entry}；反向的失效条件是{stop}。当前动作为「{action}」。"
    if any(word in question for word in ("放量", "出货", "进攻", "量比", "换手")):
        reasons = "、".join((card.get("deterministic_signal") or {}).get("reasons") or [])
        return f"当前量价更接近「{action}」剧本，核心根据是{reasons or '量价证据不足'}；以{stop}作为证伪。"
    if any(word in question for word in ("明天", "次日", "预期")):
        return f"次日不做无条件猜涨跌：只有{entry}才上调预期；{stop}则结构失效。"
    if signal_action in {"买入", "加仓"} and action == "观望":
        return f"盘口信号偏强，但尚未取得新仓风控授权；现在只观察{entry}，不直接追价。"
    return f"直接结论是「{action}」；强化条件是{entry}，失效条件是{stop}。"


def _holding_sell_shares(row: dict, action: str) -> str:
    shares = _finite_number(row.get("shares"))
    if shares is None or shares <= 0 or action not in {"减仓", "止损", "清仓", "回避"}:
        return ""
    if action in {"止损", "清仓", "回避"}:
        target = int(shares)
    else:
        target = max(100, int((shares * 0.34) // 100 * 100)) if shares >= 100 else int(shares)
        target = min(int(shares), target)
    return f"；对应数量约 {target} 股"


def _stock_chat_stage_label(context: dict | None) -> str:
    session = str((context or {}).get("market_session") or "")
    return {
        "non_trading": "最近交易日回答",
        "premarket": "盘前回答",
        "intraday": "盘中回答",
        "tail": "尾盘回答",
        "review": "盘后回答",
    }.get(session, "个股回答")


def _populate_stock_material_report(card: dict, report: dict) -> None:
    """把已完成的单股确定性采集映射到 SSE 可复核材料状态。"""
    quote = card.get("market_quote") or {}
    quality = card.get("decision_market_evidence_quality") or card.get("market_evidence_quality") or {}
    missing = [str(item) for item in (card.get("missing") or []) if str(item)]
    required = report.get("required") or []
    if len(required) >= 3:
        required[0].update({
            "state": "complete" if quote else "missing",
            "injected": False,
            "data_date": quote.get("as_of") or card.get("decision_generated_at"),
            "source": quote.get("source"),
            "stale": bool(quote.get("stale")),
            "reason": quote.get("quote_note") or ("行情已核验" if quote else "行情未返回"),
        })
        required[1].update({
            "state": "complete" if card.get("risk_decision") and card.get("trade_plan") else "degraded",
            "injected": False,
            "data_date": card.get("decision_generated_at"),
            "source": "服务端确定性诊断与 RiskGate",
            "stale": False,
            "reason": "风险闸与失效条件已执行",
        })
        quality_state = str(quality.get("status") or "missing")
        required[2].update({
            "state": "complete" if quality_state == "ok" and not missing else "degraded",
            "injected": False,
            "data_date": card.get("decision_generated_at"),
            "source": "字段级证据质量契约",
            "stale": bool(quote.get("stale")),
            "reason": "；".join(missing[:3]) if missing else "缺失、过期与降级状态已核验",
        })
    chat_materials_mod.update_required_result(
        report,
        "holdings",
        **chat_materials_mod.holdings_result_fields(card.get("用户持仓")),
    )

    payloads = card.get("material_payloads") or {}
    technical = payloads.get("technical") if isinstance(payloads, dict) else None
    flow = payloads.get("fund_flow") if isinstance(payloads, dict) else None
    sector = payloads.get("sector") if isinstance(payloads, dict) else None
    fundamental = payloads.get("fundamental") if isinstance(payloads, dict) else None
    data_as_of = card.get("decision_generated_at") or (card.get("analysis_pipeline") or {}).get("data_as_of")
    if "technical" in set(report.get("selected_ids") or []):
        chat_materials_mod.update_result(
            report, "technical",
            state="complete" if technical else "missing",
            data_date=(technical or {}).get("minute_date") or data_as_of,
            source="腾讯前复权日K与新浪/授权分时",
            stale=bool(quote.get("stale")),
            reason="技术结构与量价已完成" if technical else "技术增强未返回",
        )
    if "fund_flow" in set(report.get("selected_ids") or []):
        trade_date = str((flow or {}).get("trade_date") or "")
        formatted_date = (
            f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
            if len(trade_date) == 8 else (trade_date or None)
        )
        chat_materials_mod.update_result(
            report, "fund_flow",
            state="complete" if flow else "missing",
            data_date=formatted_date,
            source=(flow or {}).get("source"),
            stale=bool(formatted_date and formatted_date != now_cn().strftime("%Y-%m-%d")),
            reason=(
                f"资金数据归属 {formatted_date}"
                if flow and formatted_date else ("资金流已返回" if flow else "资金流源未返回")
            ),
        )
    if "sector" in set(report.get("selected_ids") or []):
        chat_materials_mod.update_result(
            report, "sector",
            state="complete" if sector else "missing",
            data_date=data_as_of,
            source=(sector or {}).get("source"),
            stale=False,
            reason="行业与概念关系已返回" if sector else "行业/概念关系未返回",
        )
    if "fundamental" in set(report.get("selected_ids") or []):
        chat_materials_mod.update_result(
            report, "fundamental",
            state="complete" if fundamental else "missing",
            data_date=(fundamental or {}).get("data_date") or (fundamental or {}).get("end_date"),
            source=(fundamental or {}).get("source"),
            stale=None,
            reason="基础财务已返回" if fundamental else "基础财务源未返回",
        )


def _deterministic_stock_chat_fallback(
    card: dict,
    direct: str,
    stage_label: str,
    failure_reason: str = "model_unavailable",
) -> str:
    """模型深度成稿不可用时，返回一段自然语言安全说明而非字段卡片。"""
    quote = card.get("market_quote") or {}
    technical = card.get("technical_analysis") or {}
    plan = card.get("trade_plan") or {}
    name = card.get("name") or quote.get("name") or card.get("code") or "个股"
    code = str(card.get("code") or "").strip()
    title = f"## {name}（{code}）·{stage_label}" if code else f"## {name}·{stage_label}"

    snapshot: list[str] = []
    price = _finite_number(quote.get("price"))
    change = _finite_number(quote.get("change_pct"))
    if price is not None:
        snapshot.append(f"现价 {price:.2f} 元")
    if change is not None:
        snapshot.append(f"最近涨跌幅 {change:+.2f}%")

    structure = str(technical.get("structure") or "").strip()
    reasons = [
        str(item).strip() for item in (technical.get("reasons") or [])
        if str(item).strip()
    ][:3]
    structure_parts = []
    if structure:
        structure_parts.append(f"可复算结构暂定为“{structure}”")
    if reasons:
        structure_parts.append("主要依据是" + "、".join(reasons))
    if snapshot:
        structure_parts.append("；".join(snapshot))

    risk = card.get("risk_decision") or {}
    risk_reasons = [
        str(item).strip() for item in (risk.get("reasons") or [])
        if str(item).strip()
    ][:2]
    risk_text = (
        "当前风控还提示" + "；".join(risk_reasons) + "。"
        if risk_reasons else ""
    )
    entry = str(plan.get("entry_trigger") or "价格与量能形成有效确认").strip()
    stop = str(plan.get("stop") or "关键价格证据补齐后再设失效条件").strip()

    missing = [str(item).strip() for item in (card.get("missing") or []) if str(item).strip()]
    reason_text = {
        "model_unavailable": "模型服务本轮不可用",
        "model_incomplete": "模型未返回可采信的完整终态",
        "model_length": "模型正文达到输出长度上限，且未形成完整终态",
        "model_length_after_rewrite": "模型正文及自动修订稿均达到输出长度上限",
        "model_missing_stop": "模型返回了正文，但上游未提供标准的 stop 终态",
        "model_stream_interrupted": "模型已生成部分正文，但传输在完整终态前中断",
        "model_content_filter": "模型正文被上游内容策略终止",
        "model_nonstop_terminal": "模型以非 stop 状态结束，本轮正文不可采信",
        "safety_validation_failed": "模型正文与冻结动作或价格证据存在冲突",
        "output_too_large": "模型正文异常超出问股展示上限",
        "model_empty": "模型本轮未返回正文",
    }.get(failure_reason, "模型深度成稿本轮未完成")
    paragraphs = [
        title,
        "",
        f"**{direct}**",
    ]
    if structure_parts:
        paragraphs.extend(["", "。".join(structure_parts).rstrip("。") + "。" + risk_text])
    paragraphs.extend([
        "",
        f"接下来不需要预判单边走势：若出现{entry}，原判断才得到强化；"
        f"若{stop}，当前结构即被证伪，应转入防守。",
    ])
    selected = [
        str(item.get("name") or "").strip()
        for item in (card.get("selected_perspectives") or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    if selected:
        paragraphs.extend([
            "",
            "> 本轮已选择" + "、".join(selected)
            + "；由于深度成稿未完成，未伪造或补写这些视角的判断。",
        ])
    paragraphs.extend([
        "",
        f"> 深度成稿未采用：{reason_text}。以上仅保留已核验的确定性结论。",
    ])
    if missing:
        paragraphs.extend([
            "",
            "> 数据边界：本轮仍缺少"
            + "、".join(missing[:3])
            + "；这些数据只限制对应维度，不是深度成稿失败原因。",
        ])
    return "\n".join(paragraphs)


def _stock_model_answer_is_displayable(answer: str, card: dict) -> bool:
    """扩展实现只做安全兜底；篇幅和措辞不再作为整篇回退条件。"""
    text = str(answer or "").strip()
    action = str(card.get("action") or "").strip()
    if not text or "针对你这句话的补充" in text or "### 现在怎么做" in text:
        return False
    conservative = action in {"观望", "回避", "持有", "持有（不加仓）", "减仓", "止损", "清仓"}

    def locally_negated(line: str, start: int) -> bool:
        prefix = line[max(0, start - 12):start]
        return bool(re.search(
            r"(?:不|不能|不可|禁止|暂停|避免|回避|不得|取消|无需|不要)"
            r"(?:应|可|宜|要|再|去|考虑)?\s*$",
            prefix,
        ))

    for raw in text.splitlines():
        line = raw.strip()
        if not line or not re.search(r"买入|建仓|加仓|低吸|介入|试仓|抄底|进攻仓", line):
            continue
        if action == "持有（不加仓）":
            for match in re.finditer("加仓", line):
                if not locally_negated(line, match.start()):
                    return False
        if conservative:
            direct_order = re.compile(
                r"立即买入|直接买入|现在买入|马上买入|果断买入|直接下单|无条件买入|满仓|重仓"
            )
            for match in direct_order.finditer(line):
                if not locally_negated(line, match.start()):
                    return False
    return True


def _stock_data_boundary_markdown(card: dict) -> str:
    """由服务端稳定追加缺失/过期边界，不依赖模型是否主动说明。"""
    missing = [
        str(item).strip()
        for item in (card.get("missing") or [])
        if str(item).strip()
    ]
    if not missing:
        return ""
    return (
        "> 数据边界：本轮仍缺少或仅取得"
        + "、".join(missing[:4])
        + "；正文可用于理解已有证据，但对应维度不能视为最新、完整事实。"
    )


def _annotated_stock_model_answer(
    answer: str,
    card: dict,
    direct: str,
    warning_codes: list[str],
) -> str:
    """保留完整模型正文，同时把未通过的校验项与冻结边界放在正文之前。"""
    labels = {
        "unsupported_numeric_claim": "存在资料包外或无法反查来源的数字",
        "unsupported_price_claim": "存在未绑定当前股票证据的价格",
        "frozen_action_conflict": "买卖动作与服务端冻结结论冲突",
        "perspective_sections_incomplete": "已选分析视角未完整覆盖",
        "answer_too_short": "分析篇幅不足",
        "frozen_action_not_explicit": "冻结动作没有明确写出",
        "execution_boundary_incomplete": "强化或失效条件没有完整写出",
        "legacy_two_part_format": "仍含旧版两段式结构",
        "model_missing_stop": "上游未提供标准 stop 终态",
    }
    warnings = [labels.get(code, str(code)) for code in warning_codes if str(code)]
    warning_text = "；".join(dict.fromkeys(warnings)) or "正文未通过完整校验"
    plan = card.get("trade_plan") or {}
    entry = str(plan.get("entry_trigger") or "等待有效确认").strip()
    stop = str(plan.get("stop") or "结构失效后转入防守").strip()
    sections = [
        "> **校验标注：以下模型全文予以保留，但未全部采信。**",
        f"> 未采信项：{warning_text}。",
        f"> 服务端已核验结论：**{direct}** 强化条件为“{entry}”；失效条件为“{stop}”。",
        "",
        "### 模型分析全文（带校验标注）",
        "",
        str(answer or "").strip(),
    ]
    boundary = _stock_data_boundary_markdown(card)
    if boundary:
        sections.extend(["", boundary])
    return "\n".join(sections)


async def _governed_stock_chat_stream(
    st, code: str, message: str = "", history: list[dict] | None = None,
    perspective_ids: tuple[str, ...] | None = None,
    data_material_ids: tuple[str, ...] | None = None,
    material_report: dict | None = None,
):
    """个股问答只输出一份已治理完整成稿，失败时回退自然语言安全说明。"""
    try:
        diagnosis_kwargs = {}
        if data_material_ids is not None:
            try:
                if "data_material_ids" in inspect.signature(
                    _governed_and_record_diagnosis
                ).parameters:
                    diagnosis_kwargs["data_material_ids"] = data_material_ids
            except (TypeError, ValueError):
                diagnosis_kwargs["data_material_ids"] = data_material_ids
        card = await _governed_and_record_diagnosis(st, code, **diagnosis_kwargs)
    except Exception:  # noqa: BLE001
        log.exception("盘中问股治理失败 code=%s", code)
        if material_report is not None:
            chat_materials_mod.finalize_report(material_report, model_used=False)
        yield "当前无法完成证据核验与建议留痕，因此不输出买卖动作。请稍后重试。"
        return
    if card.get("recognized") is False or card.get("error_code") == "stock_not_found":
        if material_report is not None:
            _populate_stock_material_report(card, material_report)
            chat_materials_mod.finalize_report(material_report, model_used=False)
        yield (
            f"未找到 A 股代码 {code}。当前完整证券名录已核验，"
            "因此本次不会把指数别名或错误代码当成个股，也不会生成“观望/买入”等动作。"
        )
        return
    direct = _stock_question_answer(message, card)
    holding_question = any(
        word in str(message or "")
        for word in ("继续持有", "能持有", "要不要拿", "能拿")
    )
    holding_only_action = bool(
        holding_question
        and card.get("signal_action") in {"买入", "加仓", "持有"}
        and card.get("action") in {"观望", "回避"}
    )
    display_action = "持有（不加仓）" if holding_only_action else (card.get("action") or "观望")
    try:
        market_context = await asyncio.to_thread(build_market_context)
    except Exception:  # noqa: BLE001
        log.warning("个股问答无法取得交易阶段，使用中性标题 code=%s", code)
        market_context = {}
    stage_label = _stock_chat_stage_label(market_context)
    explanation_card = dict(card)
    explanation_card.setdefault("code", code)
    explanation_card["action"] = display_action
    explanation_card["direct_answer"] = direct
    explanation_card["answer_stage"] = stage_label
    selected_perspectives = stock_perspectives_mod.selected_metadata(
        perspective_ids or ()
    )
    explanation_card["selected_perspectives"] = selected_perspectives
    if material_report is not None:
        explanation_card["用户持仓"] = await _chat_holdings_context(st)
        _populate_stock_material_report(explanation_card, material_report)
    if holding_only_action:
        # 只调整这一轮问答的表达上下文；原始诊断卡和 RiskGate 回执
        # 保持不变，避免“不授权加仓”被扩写成“已有仓位必须退出”。
        explanation_card["summary"] = direct

    # 深度模型先在服务端完成并通过校验，之后才作为唯一正文下发。
    # 若上游中途失败，已收集的半截内容会被丢弃，不进入会话历史。
    explain = getattr(st.engine, "governed_stock_explanation_stream", None)
    failure_reason = "model_unavailable"
    if callable(explain) and message:
        parts: list[str] = []
        total = 0
        try:
            explain_kwargs = {"history": history or []}
            if perspective_ids:
                explain_kwargs["perspective_ids"] = perspective_ids
            if data_material_ids is not None:
                explain_kwargs["data_material_ids"] = data_material_ids
                explain_kwargs["material_report"] = material_report
            async for delta in explain(message, explanation_card, **explain_kwargs):
                text = str(delta or "")
                if not text:
                    continue
                total += len(text)
                if total > 12_000:
                    parts = []
                    failure_reason = "output_too_large"
                    break
                parts.append(text)
        except Exception:  # noqa: BLE001 — 半截模型正文必须丢弃
            parts = []
            failure_reason = "model_incomplete"
            log.warning("盘中问股深度成稿未返回，使用确定性回退 code=%s", code)
        answer = "".join(parts).strip()
        warning_codes = [
            str(item).strip()
            for item in (explanation_card.get("_explanation_warning_codes") or [])
            if str(item).strip()
        ]
        if answer and warning_codes:
            yield _annotated_stock_model_answer(
                answer,
                explanation_card,
                direct,
                warning_codes,
            )
            if material_report is not None:
                chat_materials_mod.finalize_report(material_report, model_used=True)
            return
        if _stock_model_answer_is_displayable(answer, explanation_card):
            boundary = _stock_data_boundary_markdown(explanation_card)
            yield answer + (f"\n\n{boundary}" if boundary else "")
            if material_report is not None:
                chat_materials_mod.finalize_report(material_report, model_used=True)
            return
        if answer:
            failure_reason = "safety_validation_failed"
            log.warning("盘中问股成稿未保留冻结边界，使用确定性回退 code=%s", code)
        else:
            failure_reason = str(
                explanation_card.get("_explanation_failure_reason") or failure_reason
            )

    if material_report is not None:
        chat_materials_mod.finalize_report(material_report, model_used=False)
    yield _deterministic_stock_chat_fallback(
        explanation_card,
        direct,
        stage_label,
        failure_reason=failure_reason,
    )


async def _governed_position_chat_stream(
    st, codes: list[str], message: str = "", history: list[dict] | None = None,
):
    """持仓诊断同时读取成本、数量、实时价与条件化风控，不再输出“持有+0%”。"""
    holdings = [
        row for row in st.store.holdings()
        if isinstance(row, dict) and _CODE_RE.match(str(row.get("code") or ""))
    ]
    selected = set(codes or [])
    if selected:
        holdings = [row for row in holdings if str(row.get("code")) in selected]
    if not holdings:
        yield "当前没有可诊断的持仓；请先在持仓管理中录入 6 位 A 股代码。"
        return
    sections: list[str] = ["## 持仓诊断·直接结论", ""]
    total_cost = 0.0
    total_value = 0.0
    evaluated = 0
    for row in holdings[:20]:
        code = str(row.get("code"))
        try:
            card = await _governed_and_record_diagnosis(st, code, source="持仓")
        except Exception:  # noqa: BLE001
            log.exception("持仓治理诊断失败 code=%s", code)
            sections.extend([
                f"### {row.get('name') or code}（{code}）",
                "证据核验或建议留痕失败，本次不输出加仓动作。",
                "",
            ])
            continue
        record = card.get("advice_record") or {}
        quote = card.get("market_quote") or {}
        price = _finite_number(quote.get("price"))
        cost = _finite_number(row.get("cost"))
        shares = _finite_number(row.get("shares"))
        pnl_pct = (
            (price / cost - 1) * 100
            if price is not None and cost is not None and cost > 0 else None
        )
        if shares is not None and shares > 0:
            if cost is not None:
                total_cost += cost * shares
            if price is not None:
                total_value += price * shares
            evaluated += 1
        action = str(card.get("action") or "持有")
        plan = card.get("trade_plan") or {}
        sell_note = _holding_sell_shares(row, action)
        status_bits = []
        if price is not None:
            status_bits.append(f"现价 {price:.2f}")
        if cost is not None:
            status_bits.append(f"成本 {cost:.2f}")
        if pnl_pct is not None:
            status_bits.append(f"持仓盈亏 {pnl_pct:+.2f}%")
        sections.extend([
            f"### {card.get('name') or row.get('name') or code}（{code}）",
            f"- 仓位现状：{' · '.join(status_bits) or '成本/现价数据不足'}",
            f"- 当前动作：**{action}**{sell_note}",
            f"- 继续持有/转强条件：{plan.get('hold_trigger') or plan.get('entry_trigger') or '价格锚点不足'}",
            f"- 减仓/退出条件：{plan.get('stop') or '价格锚点不足'}",
            f"- 依据：{card.get('summary') or '暂无足够证据改变持仓'}",
            "",
        ])
    if evaluated and total_cost > 0:
        portfolio_pnl = (total_value / total_cost - 1) * 100
        sections.extend([
            "### 组合小结",
            f"- 已取得成本与实时价的 {evaluated} 只持仓，合计浮动盈亏约 {portfolio_pnl:+.2f}%。",
            "- 未录入账户总资产时，不伪造仓位占比；新增仓位需等资产与行业集中度完整后再授权。",
        ])
    sections.append("> 上面的“持有”代表当前不必新增风险，不是“目标仓位 0%”。")
    yield "\n".join(sections)


async def _chat_holdings_context(st) -> list[dict]:
    """自由问答统一读取当前客户持仓；兼容旧测试桩与降级运行态。"""
    loader = getattr(getattr(st, "engine", None), "_enriched_holdings_context", None)
    if callable(loader):
        try:
            value = await loader()
            return value if isinstance(value, list) else []
        except Exception:  # noqa: BLE001 — 持仓缺失不拖垮自由问答
            log.exception("自由问答读取持仓上下文失败")

    getter = getattr(getattr(st, "store", None), "holdings", None)
    if not callable(getter):
        return []
    try:
        rows = await asyncio.to_thread(getter)
    except Exception:  # noqa: BLE001
        log.exception("自由问答读取持仓快照失败")
        return []
    return [dict(row) for row in (rows or [])[:20] if isinstance(row, dict)]


async def _market_bundle_for_chat(
    store,
    data_material_ids: tuple[str, ...] | None = None,
    material_report: dict | None = None,
) -> dict:
    """自由问答材料按选择并行采集；大盘基础时点始终保留。"""
    async def guarded(func, timeout: float) -> dict:
        try:
            value = await asyncio.wait_for(asyncio.to_thread(func), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError, Exception):  # noqa: BLE001
            return {}
        return value if isinstance(value, dict) else {}

    selected = set(data_material_ids or ())
    jobs = {"overview": guarded(market.market_overview, 5.0)}
    if "sector" in selected:
        jobs["sectors"] = guarded(market.market_sectors, 6.0)
    if "fund_flow" in selected:
        jobs["flow"] = guarded(market.market_flow, 5.0)
    if "market_context" in selected:
        jobs["sentiment"] = guarded(lambda: senti.compute_today(store), 4.0)
    if "events" in selected:
        jobs["events"] = guarded(news.cn_news, 6.0)
    keys = list(jobs)
    values = await asyncio.gather(*(jobs[key] for key in keys))
    result = dict(zip(keys, values))
    overview = result.get("overview") or {}
    if material_report is not None:
        required = material_report.get("required") or []
        if len(required) >= 3:
            data_date = overview.get("data_date") or overview.get("ts")
            required[0].update({
                "state": "complete" if overview else "missing",
                "data_date": data_date,
                "source": overview.get("source") or "市场总览聚合",
                "stale": bool(overview.get("stale")),
                "reason": "市场行情与时点已返回" if overview else "市场总览未返回",
            })
            required[1].update({
                "state": "complete",
                "source": "服务端自由问答风险边界",
                "reason": "确定性风险约束始终生效",
            })
            required[2].update({
                "state": "degraded" if overview.get("missing") else "complete",
                "data_date": data_date,
                "source": "数据质量契约",
                "stale": bool(overview.get("stale")),
                "reason": "；".join(str(item) for item in (overview.get("missing") or [])[:3])
                or "缺失、过期与降级状态已核验",
            })
        for material_id, key, default_source in (
            ("technical", "overview", "市场指数行情"),
            ("fund_flow", "flow", "市场资金方向"),
            ("sector", "sectors", "板块行情"),
            ("market_context", "sentiment", "市场情绪规则"),
            ("events", "events", "公开财经快讯"),
        ):
            if material_id not in selected:
                continue
            payload = result.get(key) or {}
            available = bool(payload)
            degraded_scope = material_id in {"technical", "fund_flow"} and available
            chat_materials_mod.update_result(
                material_report,
                material_id,
                state="degraded" if degraded_scope else ("complete" if available else "missing"),
                data_date=payload.get("data_date") or payload.get("date") or payload.get("ts"),
                source=payload.get("source") or default_source,
                stale=bool(payload.get("stale")),
                reason=(
                    "未识别单股，仅取得市场指数行情，不含个股分时、日K和技术指标"
                    if material_id == "technical" and available else
                    "未识别单股，降级为全市场资金方向，不作为个股资金流"
                    if material_id == "fund_flow" and available else
                    "材料已采集" if available else "对应数据源未返回"
                ),
            )
        if "fundamental" in selected:
            chat_materials_mod.update_result(
                material_report,
                "fundamental",
                state="missing",
                reason="问题中未识别到单只股票，未启动个股财务采集",
            )
    return result


async def _governed_free_chat_stream(
    st,
    message: str,
    *,
    data_material_ids: tuple[str, ...] | None = None,
    material_report: dict | None = None,
):
    """模型未连接时的自由问答：只回答可由当前市场数据支持的部分。"""
    bundle, holdings = await asyncio.gather(
        _market_bundle_for_chat(st.store, data_material_ids, material_report),
        _chat_holdings_context(st),
    )
    bundle["holdings"] = holdings
    if material_report is not None:
        chat_materials_mod.update_required_result(
            material_report,
            "holdings",
            **chat_materials_mod.holdings_result_fields(holdings),
        )
    overview = bundle.get("overview") or {}
    sectors = bundle.get("sectors") or {}
    flow = bundle.get("flow") or {}
    events = bundle.get("events") or {}
    question = str(message or "").strip()
    indexes = [row for row in (overview.get("indexes") or []) if isinstance(row, dict)]
    sector_rows = [row for row in (sectors.get("sectors") or []) if isinstance(row, dict)]
    flow_rows = [row for row in (flow.get("main_flow_sectors") or []) if isinstance(row, dict)]
    up = int(overview.get("up_count") or 0)
    down = int(overview.get("down_count") or 0)
    flat = int(overview.get("flat_count") or 0)
    limit_up = int(overview.get("limit_up") or 0)
    limit_down = int(overview.get("limit_down") or 0)
    market_date = str(overview.get("data_date") or overview.get("ts") or "未知")
    lines = [
        "## 市场数据答复",
        "",
        f"> 数据归属：{market_date}。AI 模型未连接，本次只做确定性市场分析，不编造自由推理。",
        "",
    ]
    if any(word in question for word in ("风险", "仓位", "情绪", "能不能买", "可以买")):
        breadth = (up / max(1, up + down + flat)) * 100.0
        lines.extend([
            "### 风险与情绪",
            f"- 涨/跌/平：**{up}/{down}/{flat}**，上涨占比 {breadth:.1f}%。",
            f"- 涨停/跌停：**{limit_up}/{limit_down}**；"
            + ("跌停显著高于涨停，当前先控制新增风险。" if limit_down > limit_up * 2 else "未触发“跌停显著压倒涨停”的极端条件。"),
        ])
    elif any(word in question for word in ("资金", "流入", "流出")):
        lines.append("### 资金方向")
        for row in flow_rows[:8]:
            value = _finite_number(row.get("main_net"))
            lines.append(
                f"- {row.get('name') or '—'}："
                + (f"{value:+.2f}（上游原始口径，未核验单位）" if value is not None else "净额缺失")
            )
        if not flow_rows:
            lines.append("- 当前资金源未返回可展示的板块排序。")
    elif any(word in question for word in ("主线", "板块", "行业", "热点")):
        lines.append("### 板块强弱")
        for row in sector_rows[:8]:
            chg = _finite_number(row.get("change_pct"))
            leader = str(row.get("leader_name") or "")
            lines.append(
                f"- {row.get('name') or '—'}：{chg:+.2f}%"
                + (f"；领涨 {leader}" if leader else "")
                if chg is not None else f"- {row.get('name') or '—'}：涨跌幅缺失"
            )
        if not sector_rows:
            lines.append("- 当前板块行情源未返回。")
    elif any(word in question for word in ("公告", "消息", "事件", "新闻", "利好", "利空")):
        lines.append("### 公告与事件")
        event_rows = [row for row in (events.get("items") or []) if isinstance(row, dict)]
        for row in event_rows[:8]:
            lines.append(f"- {row.get('title') or row.get('content') or '标题未提供'}")
        if not event_rows:
            lines.append("- 当前事件源未返回可核验条目；本次不补写消息或公告。")
    elif any(word in question for word in ("估值", "业绩", "基本面", "长期")):
        lines.extend([
            "### 基本面与估值",
            "- 本轮未识别到单只股票，因此没有启动个股财务或估值采集。",
            "- 请补充一只股票的六位代码或完整名称后再问；系统不会猜测标的。",
        ])
    else:
        lines.append("### 当前市场概览")
        for row in indexes[:5]:
            chg = _finite_number(row.get("change_pct"))
            lines.append(
                f"- {row.get('name') or row.get('code') or '—'}："
                + (f"{chg:+.2f}%" if chg is not None else "涨跌幅缺失")
            )
        lines.extend([
            f"- 市场广度：涨 {up} / 跌 {down} / 平 {flat}。",
            f"- 涨停 {limit_up} 家，跌停 {limit_down} 家。",
        ])
    missing_labels = {
        "north": "北向资金：当前公开源未返回可核验数据",
        "north_flow": "北向资金：当前公开源未返回可核验数据",
        "main_flow": "大盘主力资金：当前公开源未返回可核验数据",
        "sectors": "板块排行：当前行情源未返回",
    }
    missing = list(dict.fromkeys([
        *(overview.get("missing") or []),
        *(sectors.get("missing") or []),
        *(flow.get("missing") or []),
    ]))
    if missing:
        lines.extend([
            "",
            "### 数据限制",
            *[f"- {missing_labels.get(str(item), str(item))}" for item in missing[:6]],
        ])
    lines.extend([
        "",
        "在自由问答里写入一只股票的 6 位代码或完整名称，会自动读取该股的真实证据；"
        "未识别或同时出现多只股票时会明确提示，不会猜测标的。",
    ])
    if material_report is not None:
        chat_materials_mod.finalize_report(material_report, model_used=False)
    yield "\n".join(lines)


async def _governed_chain_chat_stream(
    st,
    industry: str,
    message: str,
    history: list[dict] | None = None,
    images: list[str] | None = None,
    *,
    model_available: bool = False,
    research_contract: str = "",
):
    """产业链统一流：先返回冻结结构化研究，模型只能在边界内解释。"""
    from ..analysis import industry_chain as industry_chain_mod  # noqa: PLC0415

    loader = getattr(st.engine, "industry_chain_analysis", None)
    try:
        if callable(loader):
            analysis = await loader(industry)
        else:
            analysis = await industry_chain_mod.collect_analysis(industry)
    except Exception:  # noqa: BLE001
        log.exception("产业链结构化分析采集失败 industry=%s", industry)
        analysis = industry_chain_mod.build_analysis(
            industry,
            catalog={},
            membership={},
            quotes={},
            sector_market={},
        )
        analysis["data_gaps"].insert(0, {
            "dimension": "本轮结构化采集",
            "status": "failed",
            "needed": ["稍后重试本地 TQ/公开板块目录与成分关系"],
        })
    analysis["request_contract"] = (
        research_contract if research_contract == "chain-research-v2" else "legacy-compatible"
    )
    explain = getattr(st.engine, "governed_chain_explanation_stream", None)
    if model_available and callable(explain) and message:
        emitted = False
        try:
            explanation_gen = _invoke_governed_chat_stream(
                explain,
                message,
                analysis,
                history=history or [],
                images=images or [],
            )
            async for delta in explanation_gen:
                text = str(delta or "")
                if not text:
                    continue
                if not emitted:
                    emitted = True
                    yield "# 产业链上下游研究\n\n"
                yield text
        except Exception:  # noqa: BLE001 — 结构化首答已完整，不因解释层失败降级
            log.warning("产业链模型解释未返回，保留结构化首答 industry=%s", industry)
        if not emitted:
            yield industry_chain_mod.render_generation_fallback(
                analysis,
                reason="模型未返回完整终态，未把提纲或半截正文冒充研究报告。",
            )
        else:
            yield (
                "\n\n---\n\n"
                "> 研究说明：本报告以产业结构、上下游传导和商业逻辑为主。"
                "公司业务归属、订单、份额、财务表现及当前交易时点需在具体研究中另行确认。"
                "\n\n"
                + industry_chain_mod.render_evidence_appendix(analysis)
            )
        return
    yield industry_chain_mod.render_generation_fallback(
        analysis,
        reason="当前模型连接或能力预检不可用，未执行模型增强。",
    )


async def _governed_chat_sse_events(
    gen,
    *,
    stage: str,
    done_payload: dict | None = None,
):
    """把治理问股的纯文本生成器转换成前端统一 SSE 事件协议。"""
    yield {"stage": stage}
    async for chunk in gen:
        text = str(chunk or "")
        if text:
            yield {"delta": text}
    yield {"done": True, "truncated": False, **(done_payload or {})}


def _invoke_governed_chat_stream(fn, *args, **kwargs):
    """调用治理流，同时兼容只接受旧版位置参数的测试桩/扩展实现。"""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
    allowed = {p.name for p in params}
    safe_kwargs = kwargs if accepts_kwargs else {k: v for k, v in kwargs.items() if k in allowed}
    return fn(*args, **safe_kwargs)


async def _with_material_report(gen, material_report: dict | None):
    """让自由问答的 done/error 都携带本轮真实材料终态。"""
    async for event in gen:
        if not isinstance(event, dict):
            yield event
            continue
        if event.get("error") and material_report is not None:
            chat_materials_mod.finalize_report(material_report, model_used=False)
            yield {**event, "data_materials": material_report, "model_used": False}
            continue
        if event.get("done") and material_report is not None:
            yield {**event, "data_materials": material_report}
            continue
        yield event

@router.get("/chat/perspectives")
async def chat_perspectives():
    """返回问股可选择的服务端白名单；前端不得自行注入提示词。"""
    return {
        "ok": True,
        "max_selected": stock_perspectives_mod.MAX_SELECTED_PERSPECTIVES,
        "perspectives": stock_perspectives_mod.public_perspectives(),
    }


@router.get("/chat/data-materials")
async def chat_data_materials():
    """返回自由问答材料白名单；采集能力仍以每轮真实结果为准。"""
    return {
        "ok": True,
        "max_selected": chat_materials_mod.MAX_SELECTED,
        "max_auto_selected": chat_materials_mod.MAX_AUTO_SELECTED,
        "required": [dict(item) for item in chat_materials_mod.REQUIRED_MATERIALS],
        "materials": chat_materials_mod.public_materials(),
    }


@router.post("/chat/data-materials/preselect")
async def chat_data_materials_preselect(request: Request):
    """按固定关键词规则返回稳定预选，不调用模型、不启动数据源。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)
    message = str((body or {}).get("message") or "").strip()
    selected = chat_materials_mod.preselect_material_ids(message)
    return {
        "ok": True,
        "preselected_ids": list(selected),
        "max_selected": chat_materials_mod.MAX_SELECTED,
    }


@router.post("/chat")
async def chat(request: Request):
    st = _state(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)
    mode = body.get("mode", "free")
    message = str(body.get("message", "")).strip()
    code = str(body.get("code", "")).strip()
    industry = str(body.get("industry", "")).strip()
    research_contract = str(body.get("research_contract", "")).strip()
    history = body.get("history") or []
    data_material_ids: tuple[str, ...] | None = None
    material_report: dict | None = None
    if mode == "free":
        try:
            data_material_ids = chat_materials_mod.resolve_material_ids(
                body.get("data_material_ids"), message=message
            )
        except chat_materials_mod.DataMaterialSelectionError as exc:
            return JSONResponse(
                {"ok": False, "error_code": "invalid_data_material_selection", "error": str(exc)},
                status_code=400,
            )
        material_report = chat_materials_mod.empty_report(data_material_ids)
    elif body.get("data_material_ids") is not None:
        return JSONResponse(
            {
                "ok": False,
                "error_code": "data_material_mode_not_supported",
                "error": "本轮数据材料目前只支持自由问答模式",
            },
            status_code=400,
        )
    try:
        perspective_ids = stock_perspectives_mod.resolve_perspective_ids(
            body.get("perspective_ids")
        )
    except stock_perspectives_mod.PerspectiveSelectionError as exc:
        return JSONResponse(
            {"ok": False, "error_code": "invalid_perspective_selection", "error": str(exc)},
            status_code=400,
        )
    if perspective_ids and mode != "stock":
        return JSONResponse(
            {
                "ok": False,
                "error_code": "perspective_mode_not_supported",
                "error": "分析视角目前只支持盘中问股模式",
            },
            status_code=400,
        )
    # 多模态图片：只收 data:image/* base64，最多 4 张、单张 ≤8MB（防请求体/视觉 token 爆）。
    _imgs = body.get("images") or []
    images = [u for u in _imgs if isinstance(u, str) and u.startswith("data:image/") and len(u) <= 8_000_000][:4] \
        if isinstance(_imgs, list) else []
    # 持仓诊断勾选子集（仅 position 模式生效）：6 位代码，最多 50；空=诊断全部持仓
    _codes = body.get("codes") or []
    codes = ([c for c in (str(x).strip() for x in _codes) if _CODE_RE.match(c)][:50]
             if isinstance(_codes, list) else [])
    if not message and not images:
        return JSONResponse({"ok": False, "error": "message 不能为空（或上传图片）"}, status_code=400)
    if not message and images:
        message = "（用户仅上传了图片、未输入文字）请结合当前模式与图片内容做分析解读。"
    if mode == "stock" and not _CODE_RE.match(code):
        return JSONResponse({"ok": False, "error": "盘中问股需提供 6 位股票代码"}, status_code=400)
    if mode == "company" and not _CODE_RE.match(code):
        return JSONResponse({"ok": False, "error": "单公司深度需提供 6 位股票代码"}, status_code=400)
    if mode == "restructuring" and code and not _CODE_RE.match(code):
        # 重组拆解的代码是可选项（给了才注入行情底座）；给了就得是合法 6 位，防脏值下探数据层
        return JSONResponse({"ok": False, "error": "重组拆解的股票代码需为 6 位数字（也可留空纯按材料分析）"}, status_code=400)
    if mode == "chain" and not industry:
        return JSONResponse({"ok": False, "error": "产业链挖掘需提供 industry"}, status_code=400)
    if mode == "free":
        stock_reference = engine_mod.resolve_free_stock_reference(message)
        if stock_reference["state"] == "matched":
            return _sse(_governed_chat_sse_events(
                _invoke_governed_chat_stream(
                    _governed_stock_chat_stream,
                    st,
                    str(stock_reference["code"]),
                    message=message,
                    history=history,
                    data_material_ids=data_material_ids,
                    material_report=material_report,
                ),
                stage="自由问答已识别个股，正在核验行情、证据与风险闸…",
                done_payload={
                    "resolved_mode": "stock",
                    "code": str(stock_reference["code"]),
                    "data_materials": material_report,
                },
            ))
        if stock_reference["state"] in {"ambiguous", "unrecognized"}:
            async def clarification_stream():
                yield engine_mod.stock_reference_clarification(stock_reference)

            if material_report is not None:
                for item in material_report.get("required") or []:
                    item.update({
                        "state": "missing",
                        "injected": False,
                        "reason": "股票标的需先确认，本轮未启动材料采集",
                    })
                for item in material_report.get("optional") or []:
                    if item.get("selected"):
                        item.update({
                            "state": "missing",
                            "injected": False,
                            "reason": "股票标的需先确认，本轮未启动材料采集",
                        })
                chat_materials_mod.finalize_report(material_report, model_used=False)

            return _sse(_governed_chat_sse_events(
                clarification_stream(),
                stage="正在核验提问中的股票标的…",
                done_payload={
                    "resolved_mode": "free",
                    "data_materials": material_report,
                },
            ))
    if mode == "stock":
        perspective_stage = (
            f"正在核验行情、证据与风险闸，并准备 {len(perspective_ids)} 个分析视角…"
            if perspective_ids else "正在核验行情、证据与风险闸…"
        )
        return _sse(_governed_chat_sse_events(
            _invoke_governed_chat_stream(
                _governed_stock_chat_stream,
                st,
                code,
                message=message,
                history=history,
                perspective_ids=perspective_ids,
            ),
            stage=perspective_stage,
        ))
    if mode == "position":
        return _sse(_governed_chat_sse_events(
            _invoke_governed_chat_stream(
                _governed_position_chat_stream, st, codes, message=message, history=history,
            ),
            stage="正在逐股核验持仓证据与风险闸…",
        ))
    model_status = await diagnosis_mod._model_connection_state(st.engine)
    if model_status != "available" and mode == "free":
        return _sse(_governed_chat_sse_events(
            _governed_free_chat_stream(
                st, message, data_material_ids=data_material_ids,
                material_report=material_report,
            ),
            stage="正在读取本轮所选数据材料…",
            done_payload={
                "resolved_mode": "free",
                "data_materials": material_report,
                "model_used": False,
            },
        ))
    if mode == "chain":
        return _sse(_governed_chat_sse_events(
            _invoke_governed_chat_stream(
                _governed_chain_chat_stream,
                st,
                industry,
                message,
                history=history,
                images=images,
                model_available=model_status == "available",
                research_contract=research_contract,
            ),
            stage="正在拆解需求、上下游与价值传导…",
        ))
    generated = _invoke_governed_chat_stream(
        st.engine.chat_stream,
        mode,
        message,
        code=code,
        industry=industry,
        history=history,
        images=images,
        codes=codes,
        data_material_ids=data_material_ids,
        material_report=material_report,
    )
    if mode == "free":
        generated = _with_material_report(generated, material_report)
    return _sse(generated)


# ---------------------------------------------------------------- 持久化后台研究任务（产业链 / 单公司）

def _chain_research_message(industry: str, focus: str) -> str:
    research_focus = focus or "完成整套产业链研究，并给出后续需要核验的证据清单。"
    return "\n".join([
        f"请按研究中心统一的「产业链深度研究」契约分析「{industry}」。",
        f"本轮重点：{research_focus}",
        "必须沿同一条推导链完成：核心需求与驱动力 → 上中下游全景 → 利润池与价值分配 → "
        "核心卡点与竞争壁垒 → 国产替代路径 → A股映射思路 → 兑现节奏与观察指标 → 风险与失效条件。",
        "重点解释产业为什么成立、价值如何沿上下游传导、利润为什么集中、瓶颈如何形成；不要把报告写成数据清单。",
        "不要逐段输出依据边界、待公告核验、数据不全或数值占位；真实性边界只在报告末尾集中说明一次。",
        "产业研究与交易时点分开；本报告不输出目标价、仓位或即时买卖动作。",
    ])


def _research_manager(st):
    manager = getattr(st, "research_runs", None)
    if manager is None:
        raise RuntimeError("后台研究任务管理器未装配")
    return manager


@router.post("/research/runs")
async def create_research_run(request: Request):
    """立即返回任务 ID；模型生成在服务端后台继续，不依赖当前页面连接。"""
    st = _state(request)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)
    kind = str(body.get("kind") or "").strip()
    if kind not in RESEARCH_KINDS:
        return JSONResponse({"ok": False, "error": "kind 必须是 chain 或 company"}, status_code=400)

    if kind == "chain":
        subject = str(body.get("industry") or "").strip()[:120]
        focus = str(body.get("focus") or "").strip()[:1000]
        if not subject:
            return JSONResponse({"ok": False, "error": "产业链研究需提供 industry"}, status_code=400)
        message = _chain_research_message(subject, focus)

        async def generator():
            model_status = await diagnosis_mod._model_connection_state(st.engine)
            if model_status != "available":
                yield {"error": "模型连接或能力预检不可用，后台任务未启动生成。"}
                return
            governed = _invoke_governed_chat_stream(
                _governed_chain_chat_stream,
                st,
                subject,
                message,
                history=[],
                images=[],
                model_available=True,
                research_contract="chain-research-v2",
            )
            async for event in _governed_chat_sse_events(
                governed,
                stage="后台正在拆解需求、上下游与价值传导…",
            ):
                yield event
    else:
        subject = str(body.get("code") or "").strip()
        focus = ""
        if not _CODE_RE.fullmatch(subject):
            return JSONResponse({"ok": False, "error": "单公司研究需提供 6 位股票代码"}, status_code=400)

        async def generator():
            model_status = await diagnosis_mod._model_connection_state(st.engine)
            if model_status != "available":
                yield {"error": "模型连接或能力预检不可用，后台任务未启动生成。"}
                return
            async for event in st.engine.chat_stream(
                "company",
                "生成单公司深度研究报告",
                code=subject,
                industry="",
                history=[],
                images=[],
            ):
                yield event

    try:
        run, reused = await _research_manager(st).start(
            kind=kind,
            subject=subject,
            focus=focus,
            generator_factory=generator,
        )
    except ActiveResearchRunError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc), "active": exc.run},
            status_code=409,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "reused": reused, "run": run}, status_code=202)


@router.get("/research/runs/latest")
async def latest_research_run(request: Request, kind: str):
    kind = str(kind or "").strip()
    if kind not in RESEARCH_KINDS:
        return JSONResponse({"ok": False, "error": "kind 必须是 chain 或 company"}, status_code=400)
    return {"ok": True, "kind": kind, **_research_manager(_state(request)).latest(kind)}


@router.get("/research/runs/{run_id}")
async def get_research_run(run_id: str, request: Request):
    run = _research_manager(_state(request)).get(run_id)
    if run is None:
        return JSONResponse({"ok": False, "error": "研究任务不存在"}, status_code=404)
    return {"ok": True, "run": run}


@router.post("/research/runs/{run_id}/cancel")
async def cancel_research_run(run_id: str, request: Request):
    run = await _research_manager(_state(request)).cancel(run_id)
    if run is None:
        return JSONResponse({"ok": False, "error": "研究任务不存在"}, status_code=404)
    return {"ok": True, "run": run}


@router.delete("/research/runs/{run_id}")
async def delete_research_run(run_id: str, request: Request):
    manager = _research_manager(_state(request))
    try:
        deleted = await asyncio.to_thread(manager.delete, run_id)
    except ActiveResearchRunDeleteError as exc:
        return JSONResponse(
            {"ok": False, "error": str(exc), "active": exc.run},
            status_code=409,
        )
    if deleted is None:
        return JSONResponse({"ok": False, "error": "研究任务不存在"}, status_code=404)
    kind = str(deleted.get("kind") or "")
    return {
        "ok": True,
        "deleted_run_id": run_id,
        "kind": kind,
        **manager.latest(kind),
    }


# ---------------------------------------------------------------- 对话会话（多会话/历史/分组）

@router.get("/chat/conversations")
async def chat_conversations(request: Request):
    st = _state(request)
    rows = await asyncio.to_thread(st.store.list_conversations)
    query = getattr(request, "query_params", {})
    mode = str(query.get("mode") or "").strip() if query is not None else ""
    if mode:
        rows = [row for row in rows if str(row.get("mode") or "free") == mode]
    return {"ok": True, "conversations": rows}


@router.post("/chat/conversations")
async def chat_conversation_new(request: Request):
    st = _state(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = str((body or {}).get("mode", "free")).strip() or "free"
    meta = await asyncio.to_thread(st.store.create_conversation, mode)
    return {"ok": True, "conversation": meta}


@router.get("/chat/conversations/{cid}")
async def chat_conversation_get(cid: str, request: Request):
    st = _state(request)
    conv = await asyncio.to_thread(st.store.get_conversation, cid)
    if not conv:
        return JSONResponse({"ok": False, "error": "会话不存在"}, status_code=404)
    return {"ok": True, **conv}


@router.post("/chat/conversations/{cid}/messages")
async def chat_conversation_add(cid: str, request: Request):
    """前端在每轮问答完成后调用，把 user+assistant 文本存入会话（图片不入库、只标 has_image）。"""
    st = _state(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)
    msgs = (body or {}).get("messages") or []
    normalized_msgs = []
    for message_row in msgs if isinstance(msgs, list) else []:
        if not isinstance(message_row, dict):
            continue
        row = dict(message_row)
        try:
            selected_ids = stock_perspectives_mod.resolve_perspective_ids(
                row.pop("perspective_ids", None)
            )
        except stock_perspectives_mod.PerspectiveSelectionError as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "error_code": "invalid_perspective_selection",
                    "error": str(exc),
                },
                status_code=400,
            )
        if row.get("role") == "assistant" and selected_ids:
            row["perspectives"] = stock_perspectives_mod.selected_metadata(selected_ids)
        if row.get("role") == "assistant":
            stored_report = chat_materials_mod.sanitize_report(row.get("data_materials"))
            if stored_report is not None:
                row["data_materials"] = stored_report
            else:
                row.pop("data_materials", None)
        else:
            row.pop("data_materials", None)
        normalized_msgs.append(row)
    ok = await asyncio.to_thread(st.store.add_messages, cid, normalized_msgs)
    return {"ok": bool(ok)}


@router.patch("/chat/conversations/{cid}")
async def chat_conversation_update(cid: str, request: Request):
    st = _state(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)
    fields = {k: v for k, v in (body or {}).items() if k in ("title", "group")}
    ok = await asyncio.to_thread(lambda: st.store.update_conversation(cid, **fields))
    return {"ok": bool(ok)}


@router.delete("/chat/conversations/{cid}")
async def chat_conversation_delete(cid: str, request: Request):
    st = _state(request)
    ok = await asyncio.to_thread(st.store.delete_conversation, cid)
    return {"ok": bool(ok)}


# ---------------------------------------------------------------- 报告

@router.get("/reports/{rtype}")
async def get_report(rtype: str, request: Request, date: str | None = None):
    st = _state(request)
    if rtype not in RTYPES:
        return {"ok": False, "error": f"未知报告类型: {rtype}"}
    if date and not _DATE_RE.match(date):
        return {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}
    rec = st.store.get_report(rtype, date)
    if not rec:
        return {"ok": False, "error": "该日期无此报告"}
    return {"ok": True, **rec}


@router.post("/reports/{rtype}/generate")
async def generate_report(rtype: str, request: Request):
    st = _state(request)
    if rtype not in RTYPES:
        return JSONResponse({"ok": False, "error": f"未知报告类型: {rtype}"}, status_code=400)
    # managed：后台任务生成+落盘，SSE 仅旁观——切页/断开不取消生成、报告照常保存（切页不丢）
    return _sse(st.engine.generate_report_managed(rtype, push=False))


@router.post("/reports/{rtype}/push")
async def push_report(rtype: str, request: Request):
    st = _state(request)
    if rtype not in RTYPES:
        return {"ok": False, "error": f"未知报告类型: {rtype}"}
    if rtype == "midday":
        return JSONResponse(
            {
                "ok": False,
                "error": "14:45 尾盘决策草案仅供 14:52 定稿复核，不能单独推送。",
            },
            status_code=409,
        )
    date = None
    force_resend = False
    resend_id = ""
    try:
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        date = body.get("date")
        force_resend = body.get("force_resend") is True
        resend_id = str(body.get("resend_id") or "").strip()
    except Exception:
        pass
    # date 会拼入报告文件路径，非字符串/越界格式必须拦在这里（防路径穿越与 500）
    if date is not None and (not isinstance(date, str) or not _DATE_RE.match(date)):
        return JSONResponse({"ok": False, "error": "日期格式应为 YYYY-MM-DD"}, status_code=400)
    if force_resend and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{7,79}", resend_id):
        return JSONResponse(
            {"ok": False, "error": "再次推送必须提供 8-80 位 resend_id 作为幂等键"},
            status_code=400,
        )
    rec = st.store.get_report(rtype, date)
    if not rec:
        return {"ok": False, "error": "报告不存在，请先生成"}
    validation = rec.get("claim_validation") or {}
    if validation.get("status") != "passed" or not validation.get("ok"):
        return JSONResponse(
            {
                "ok": False,
                "error": "该报告没有通过交易价格定稿校验，已阻止推送；请重新生成。",
            },
            status_code=409,
        )
    from ..agent.prompts import REPORT_META  # noqa: PLC0415

    ok = await push_report_with_receipt(
        store=st.store,
        pusher=getattr(st, "feishu", None),
        meta=REPORT_META.get(rtype, {}),
        rec=rec,
        rtype=rtype,
        trigger="manual",
        config=getattr(st, "config", None),
        delivery_target=f"manual_resend:{resend_id}" if force_resend else "aggregate",
    )
    if ok:
        st.store.mark_pushed(rtype, rec["date"])
        return {"ok": True, "resent": force_resend}
    return {"ok": False, "error": "推送失败，请检查推送配置"}


@router.get("/delivery/receipts")
async def get_delivery_receipts(request: Request, limit: int = 100):
    """交付回执列表（最新在前）。返回数量硬限 500，避免读取无界日志。"""
    try:
        raw_limit = request.query_params.get("limit", limit)
        bounded = max(1, min(int(raw_limit), MAX_RECEIPT_LIMIT))
    except (AttributeError, TypeError, ValueError):
        bounded = 100
    receipts, total = await asyncio.to_thread(
        DeliveryReceiptStore(_state(request).store).receipts,
        bounded,
    )
    return {
        "ok": True,
        "schema_version": DELIVERY_RECEIPT_SCHEMA_VERSION,
        "receipts": receipts,
        "count": len(receipts),
        "total": total,
        "limit": bounded,
    }


@router.get("/delivery/readiness")
async def get_delivery_readiness(request: Request):
    st = _state(request)
    return {
        "ok": True,
        **await delivery_readiness(
            store=st.store,
            pusher=getattr(st, "feishu", None),
            config=st.config,
        ),
    }


@router.post("/delivery/preflight")
async def run_delivery_preflight(request: Request):
    """仅探测配置、绑定与待人工核验事务，不发送消息。"""
    return await get_delivery_readiness(request)


@router.get("/delivery/daily-gate")
async def get_daily_delivery_gate(request: Request, date: str | None = None):
    st = _state(request)
    requested = str(request.query_params.get("date", date or st.store.today()) or "")
    if not _DATE_RE.match(requested):
        return JSONResponse({"ok": False, "error": "日期格式应为 YYYY-MM-DD"}, status_code=400)
    return {"ok": True, **await asyncio.to_thread(daily_delivery_gate, st.store, requested)}


@router.get("/delivery/outbox")
async def get_delivery_outbox(request: Request, limit: int = 200):
    from ..delivery_outbox import DeliveryOutbox, OUTBOX_SCHEMA_VERSION  # noqa: PLC0415

    try:
        bounded = max(1, min(int(request.query_params.get("limit", limit)), 1000))
    except (AttributeError, TypeError, ValueError):
        bounded = 200
    rows = await asyncio.to_thread(DeliveryOutbox(_state(request).store).rows, limit=bounded)
    return {
        "ok": True,
        "schema_version": OUTBOX_SCHEMA_VERSION,
        "items": rows,
        "count": len(rows),
    }


@router.post("/delivery/{delivery_id}/acknowledge")
async def acknowledge_delivery(delivery_id: str, request: Request):
    """人工核验歧义交付已被外部平台接收，并留下不可省略的说明。"""
    from ..delivery_outbox import DeliveryOutbox  # noqa: PLC0415

    st = _state(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    note = str(body.get("review_note") or "").strip()
    outbox = DeliveryOutbox(st.store)
    try:
        row = await asyncio.to_thread(outbox.acknowledge, delivery_id, review_note=note)
    except KeyError:
        return JSONResponse({"ok": False, "error": "交付事务不存在"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    await asyncio.to_thread(
        DeliveryReceiptStore(st.store).record,
        content_id=str(row.get("content_id") or delivery_id),
        channel=str(row.get("channel") or delivery_channel(getattr(st, "feishu", None), st.config)),
        status="acknowledged",
        message_id=row.get("message_id"),
        error=None,
        error_code="manual_external_confirmation",
        trigger="manual_review",
        attempt=int(row.get("attempts") or 1),
        delivery_id=delivery_id,
        target_results=row.get("target_results"),
    )
    return {"ok": True, "delivery_id": delivery_id, "status": "acknowledged"}


@router.post("/delivery/{delivery_id}/retry")
async def retry_delivery(delivery_id: str, request: Request):
    """人工重试单条交付；sending/unknown/partial 必须显式确认可能重复。"""
    from ..delivery_outbox import DeliveryOutbox  # noqa: PLC0415
    from ..agent.prompts import REPORT_META  # noqa: PLC0415

    st = _state(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    outbox = DeliveryOutbox(st.store)
    existing = next((item for item in await asyncio.to_thread(outbox.rows, limit=1000) if item.get("delivery_id") == delivery_id), None)
    if existing is None:
        return JSONResponse({"ok": False, "error": "交付事务不存在"}, status_code=404)
    match = re.match(r"^report:([^:]+):(\d{4}-\d{2}-\d{2})$", str(existing.get("content_id") or ""))
    if not match:
        return JSONResponse({
            "ok": False, "error": "该交付类型尚不支持在线重发，事务状态未改变",
            "delivery": existing,
        }, status_code=409)
    rtype, trade_day = match.groups()
    pdf_kind = rtype.removeprefix("pdf_") if rtype.startswith("pdf_") else None
    rec = None if pdf_kind else st.store.get_report(rtype, trade_day)
    pdf_path = None
    if pdf_kind:
        title = "盘前研报" if pdf_kind == "premarket" else "盘后复盘与次日策略"
        pdf_path = _Path(st.config.data_dir) / "pdf" / f"{title}_{trade_day}.pdf"
        if pdf_kind not in {"premarket", "postmarket"} or not pdf_path.is_file():
            return JSONResponse({"ok": False, "error": "待重发 PDF 不存在，事务状态未改变"}, status_code=404)
    elif not rec:
        return JSONResponse({"ok": False, "error": "待重发报告不存在，事务状态未改变"}, status_code=404)
    try:
        await asyncio.to_thread(
            outbox.authorize_retry,
            delivery_id,
            allow_ambiguous=bool(body.get("confirm_possible_duplicate")),
            review_note=str(body.get("review_note") or "").strip(),
        )
    except KeyError:
        return JSONResponse({"ok": False, "error": "交付事务不存在"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
    if pdf_kind:
        claim, claimed = await asyncio.to_thread(outbox.claim, delivery_id)
        if claim != "claimed":
            return JSONResponse({"ok": False, "error": "PDF 重发事务未取得执行权"}, status_code=409)
        push_file = getattr(getattr(st, "feishu", None), "push_file", None)
        if not callable(push_file):
            outcome = normalize_push_result({"ok": False, "error": "当前渠道不支持文件", "error_code": "channel_unavailable"})
        else:
            try:
                raw = await push_file(str(pdf_path), caption=f"📄 {st.config.client_name} · {pdf_path.stem}")
                outcome = normalize_push_result(raw)
            except (asyncio.TimeoutError, TimeoutError) as exc:
                outcome = normalize_push_result({"ok": False, "status": "unknown", "error": str(exc), "error_code": "push_timeout_ambiguous"})
            except Exception as exc:  # noqa: BLE001
                log.exception("PDF 人工重发异常 delivery=%s", delivery_id)
                outcome = normalize_push_result({"ok": False, "error": str(exc), "error_code": "push_exception"})
        finished = await asyncio.to_thread(
            outbox.finish,
            delivery_id,
            expected_attempt=int(claimed.get("attempts") or 0),
            status=outcome["status"],
            message_id=outcome.get("message_id"),
            error=outcome.get("error"),
            error_code=outcome.get("error_code"),
            target_results=outcome.get("target_results"),
        )
        if not finished.get("finish_applied"):
            stale_result = {
                "ok": str(finished.get("status")) in {"accepted", "delivered", "acknowledged"},
                "delivery_id": delivery_id,
                "status": finished.get("status"),
                "error": "该尝试结果已过期，事务已被其他人工处置",
            }
            return stale_result if stale_result["ok"] else JSONResponse(stale_result, status_code=409)
        await asyncio.to_thread(
            DeliveryReceiptStore(st.store).record,
            content_id=f"pdf:{pdf_kind}:{trade_day}",
            channel=delivery_channel(getattr(st, "feishu", None), st.config),
            status=outcome["status"],
            message_id=outcome.get("message_id"),
            error=outcome.get("error"),
            error_code=outcome.get("error_code"),
            trigger="manual",
            attempt=int(finished.get("attempts") or 1),
            delivery_id=delivery_id,
            target_results=outcome.get("target_results"),
        )
        return {"ok": bool(outcome["ok"]), "delivery_id": delivery_id, "status": outcome["status"]}
    ok = await push_report_with_receipt(
        store=st.store,
        pusher=getattr(st, "feishu", None),
        meta=REPORT_META.get(rtype, {}),
        rec=rec,
        rtype=rtype,
        trigger="manual",
        config=st.config,
    )
    if ok:
        st.store.mark_pushed(rtype, trade_day)
    return {"ok": ok, "delivery_id": delivery_id}


@router.post("/export/push")
async def export_push(request: Request, file: UploadFile = File(...), caption: str = Form("")):
    """接收前端生成的 PDF，经当前推送器投递到已连通讯软件。
    网关模式(MEDIA:附件)与飞书模式(bot API im/v1/files)均支持发文件；两者皆不可用时返回明确提示。"""
    st = _state(request)
    push_file = getattr(getattr(st, "feishu", None), "push_file", None)
    if not callable(push_file):
        return JSONResponse(
            {"ok": False, "error": "当前推送方式不支持发送文件附件（需本机网关投递模式）"}, status_code=400)
    raw = await file.read()
    if not raw:
        return JSONResponse({"ok": False, "error": "空文件"}, status_code=400)
    if len(raw) > 20 * 1024 * 1024:
        return JSONResponse({"ok": False, "error": "文件过大（>20MB）"}, status_code=400)
    safe = re.sub(r"[^\w.\-]+", "_", (file.filename or "export.pdf")).strip("_") or "export.pdf"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    tmp_dir = st.config.data_dir / "exports"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / safe
    try:
        path.write_bytes(raw)
        attempted = None
        try:
            attempted = await push_file(str(path), (caption or "").strip())
            outcome = normalize_push_result(attempted)
        except Exception as exc:  # noqa: BLE001 — 人工补推异常也需留痕
            outcome = normalize_push_result({
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "error_code": "push_exception",
            })
        try:
            await asyncio.to_thread(
                DeliveryReceiptStore(st.store).record,
                content_id=f"pdf:manual:{st.store.today()}:{safe}",
                channel=delivery_channel(getattr(st, "feishu", None), getattr(st, "config", None)),
                status=outcome["status"],
                message_id=outcome.get("message_id"),
                error=outcome.get("error"),
                error_code=outcome.get("error_code"),
                trigger="manual",
            )
        except Exception:  # noqa: BLE001
            log.exception("人工 PDF 交付回执落盘失败")
        msg = (
            attempted[1]
            if isinstance(attempted, tuple) and len(attempted) > 1
            else outcome.get("error") or ""
        )
        return {
            "ok": bool(outcome["ok"]),
            **({"msg": msg} if outcome["ok"] else {"error": msg or "推送失败"}),
        }
    finally:
        try:
            path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- 持仓

@router.get("/holdings")
async def get_holdings(request: Request):
    store = _state(request).store
    return {
        "ok": True,
        "holdings": store.holdings(),
        "portfolio_total_asset": store.portfolio_meta().get("total_asset"),
    }


@router.get("/holdings/advice-review")
async def holdings_advice_review(request: Request):
    """策略自证：昨日收盘对各持仓的处理倾向，用今日真实价回检是否兑现。
    返回 昨日建议|昨日收盘|今日价|涨跌|兑现 + 命中率。数字全程规则/真实价，AI 不参与。"""
    return await _state(request).engine.holdings_advice_review()


@router.put("/holdings")
async def put_holdings(request: Request):
    st = _state(request)
    try:
        body = await request.json()
        holdings = body.get("holdings", [])
        total_asset = body.get("portfolio_total_asset")
        assert isinstance(holdings, list)
    except Exception:
        return JSONResponse({"ok": False, "error": "无效请求体"}, status_code=400)
    try:
        if len(holdings) > _MAX_HOLDINGS:
            raise ValueError(f"持仓最多 {_MAX_HOLDINGS} 条")
        # 客户端行业只是旧版展示字段，不进入权威持仓快照。按代码用服务端免费
        # 个股主数据覆盖；取不到保存为空，后续新增风险动作会 fail closed。
        industries = await _server_verified_industries(
            row.get("code") for row in holdings if isinstance(row, dict)
        )
        verified_holdings = []
        for row in holdings:
            if not isinstance(row, dict):
                verified_holdings.append(row)
                continue
            item = dict(row)
            code = str(item.get("code") or "").split(".", 1)[0].zfill(6)
            item.pop("sector", None)
            item["industry"] = industries.get(code, "")
            verified_holdings.append(item)
        st.store.save_holdings(verified_holdings, total_asset=total_asset)
    except ValueError as e:
        # 畸形条目由 store 层抛出，转 400 告知具体不合法项而非 500
        return JSONResponse({"ok": False, "error": f"持仓数据不合法：{e}"}, status_code=400)
    return {"ok": True, "holdings": st.store.holdings()}


# ---------------------------------------------------------------- 观察池（母文档 §21）
# 想买还没买、等价格触发再买。A–E 评级是"真实现价 vs 用户录入理想买入区间"的确定性计算：
# 现价只来自 stock.stock_detail（取不到→grade=None+missing，绝不用区间端点反推假现价）。
# AI 全程不参与——评级/状态/建议都是规则，杜绝编数字。
#   现价缺失→None；跌破放弃价→E(优先)；区间内→A；略高(≤+5%)→B；过高→C；逼近/低于下沿→D。

def _watch_grade(price, low, high, abandon_price):
    if price is None:
        return None, "现价缺失"
    if abandon_price is not None and price < abandon_price:
        return "E", "跌破放弃价，建议移出"
    has_low = low is not None
    has_high = high is not None
    if has_low and has_high:
        if low <= price <= high:
            return "A", "接近买点/到区间"
        if high < price <= high * 1.05:
            return "B", "略高于区间，继续观察"
        if price > high * 1.05:
            return "C", "价格不合适，不追"
        if price >= low * 0.98:
            return "D", "逼近下沿，风险升高"
        return "D", "低于下沿，风险升高"
    if has_high:
        if price <= high:
            return "A", "在上沿之内（无下沿，到可买区）"
        if price <= high * 1.05:
            return "B", "略高于上沿，继续观察"
        return "C", "价格不合适，不追"
    if has_low:
        if price >= low:
            return "A", "在下沿之上（无上沿，到可买区）"
        if price >= low * 0.98:
            return "D", "逼近下沿，风险升高"
        return "D", "低于下沿，风险升高"
    return None, "理想买入区间缺失"


def _queue_watchlist_zone_refresh(store, code: str | None = None) -> None:
    """写接口只排后台计算，不把两路历史行情延迟塞进用户请求。"""
    async def _run() -> None:
        try:
            if code:
                await asyncio.to_thread(watchlist_zones_mod.refresh_one, store, code)
            else:
                await asyncio.to_thread(watchlist_zones_mod.refresh_all, store)
        except Exception:  # noqa: BLE001 - 派生区间失败不影响观察池事实写入
            log.exception("观察池自动区间后台计算失败 code=%s", code or "ALL")

    task = asyncio.create_task(_run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


@router.get("/watchlist")
async def get_watchlist(request: Request):
    """观察池列表：每条附实时现价(quote.price/change_pct) + 确定性 A–E 评级 + 状态 + missing。
    现价走【轻量报价】stock.quick_quote(~0.1s/票·腾讯直连) 且【并发】拉取——不再对每条串行调 ~7s 的
    stock_detail（那是观察池"加载很慢"的元凶：N 票×7s 串行）。单只失败标 missing 不抛（取不到标缺，绝不编）。"""
    st = _state(request)
    items = st.store.watchlist()
    zone_map = st.store.watchlist_zones() if hasattr(st.store, "watchlist_zones") else {}
    codes = [str(it.get("code", "")).strip() for it in items]
    # TDX API 热集上限400码：观察池必须合并成一次批量请求，不逐只轮询。
    quote_map = await asyncio.to_thread(stock.quick_quotes, codes)
    out = []
    for it in items:
        q = quote_map.get(str(it.get("code", "")).strip())
        quote = None
        missing: list[str] = []
        if isinstance(q, dict) and q.get("price") is not None:
            quote = {
                "name": q.get("name") or it.get("name") or "",
                "price": q.get("price"), "change_pct": q.get("change_pct"),
                "as_of": q.get("as_of"), "stale": q.get("stale"), "source": q.get("source"),
            }
        else:
            missing.append("现价")
        merged = watchlist_zones_mod.enrich_watch_item(
            it, zone_map.get(str(it.get("code", "")).strip().zfill(6)), quote,
        )
        price = quote.get("price") if quote else None
        grade, status = _watch_grade(
            price, merged.get("target_low"), merged.get("target_high"), merged.get("abandon_price")
        )
        if merged.get("target_source") == "missing":
            missing.append("理想买入区间")
        out.append({**merged, "quote": quote, "grade": grade, "status": status, "missing": missing})
    return {"ok": True, "ts": st.store.now_str(), "items": out, "missing": []}


@router.post("/watchlist")
async def add_watchlist(request: Request):
    st = _state(request)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        return JSONResponse({"ok": False, "error": "无效请求体"}, status_code=400)
    try:
        wid = st.store.add_watch(body)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": f"观察池条目不合法：{e}"}, status_code=400)
    _queue_watchlist_zone_refresh(st.store, str(body.get("code") or ""))
    return {"ok": True, "id": wid, "zone_status": "queued"}


@router.post("/watchlist/import")
async def import_watchlist(request: Request):
    """批量追加 TXT 已解析出的观察项；单次原子写入，重复与无效代码显式跳过。"""
    st = _state(request)
    try:
        body = await request.json()
        items = body.get("items") if isinstance(body, dict) else None
    except Exception:
        items = None
    if not isinstance(items, list):
        return JSONResponse({"ok": False, "error": "items 应为数组"}, status_code=400)
    try:
        result = st.store.import_watchlist(items)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": f"观察池批量导入不合法：{e}"}, status_code=400)

    added_codes = result.get("added_codes") or []
    duplicate_codes = result.get("duplicate_codes") or []
    invalid = result.get("invalid") or []
    if added_codes:
        _queue_watchlist_zone_refresh(st.store)
    return {
        "ok": True,
        "added_count": len(added_codes),
        "duplicate_count": len(duplicate_codes),
        "invalid_count": len(invalid),
        "added_codes": added_codes,
        "duplicate_codes": duplicate_codes,
        "invalid": invalid,
        "zone_status": "queued" if added_codes else None,
    }


@router.post("/watchlist/recalculate")
async def recalculate_watchlist(request: Request):
    """显式触发全观察池重算；实际采集在后台执行，避免请求超时。"""
    _queue_watchlist_zone_refresh(_state(request).store)
    return {"ok": True, "zone_status": "queued"}


@router.put("/watchlist/{wid}")
async def update_watchlist(wid: str, request: Request):
    st = _state(request)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        return JSONResponse({"ok": False, "error": "无效请求体"}, status_code=400)
    try:
        ok = st.store.update_watch(wid, body)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": f"观察池条目不合法：{e}"}, status_code=400)
    if not ok:
        return {"ok": False, "error": "条目不存在"}
    code = str(body.get("code") or "").strip()
    if not code:
        hit = next((item for item in st.store.watchlist() if item.get("id") == wid), {})
        code = str(hit.get("code") or "")
    _queue_watchlist_zone_refresh(st.store, code)
    return {"ok": True, "zone_status": "queued"}


@router.delete("/watchlist/{wid}")
async def delete_watchlist(wid: str, request: Request):
    store = _state(request).store
    existing = next((item for item in store.watchlist() if item.get("id") == wid), None)
    if store.delete_watch(wid):
        code = str((existing or {}).get("code") or "").strip().zfill(6)
        if code and hasattr(store, "delete_watchlist_zone"):
            still_used = any(str(item.get("code") or "").strip().zfill(6) == code for item in store.watchlist())
            if not still_used:
                store.delete_watchlist_zone(code)
        return {"ok": True}
    return {"ok": False, "error": "条目不存在"}


# ---------------------------------------------------------------- 推荐留痕与成功率（母文档 §29）

@router.get("/recommendations")
async def get_recommendations(request: Request):
    """推荐列表：同时返回逐窗口 review 与兼容用最新 review。"""
    st = _state(request)
    recs = st.store.recommendations()
    reviews = st.store.rec_reviews()
    latest: dict = {}
    by_rec: dict[str, list[dict]] = {}
    for rv in reviews:
        rid = rv.get("rec_id")
        if not rid:
            continue
        by_rec.setdefault(str(rid), []).append(rv)
        prev = latest.get(rid)
        if prev is None or str(rv.get("review_time", "")) >= str(prev.get("review_time", "")):
            latest[rid] = rv
    out = []
    for r in recs:
        item = dict(r)
        item["review"] = latest.get(r.get("rec_id"))
        item_reviews = sorted(
            by_rec.get(str(r.get("rec_id")), []),
            key=lambda row: (
                {"T0": 0, "T1": 1, "T2": 2, "T5": 5}.get(str(row.get("window")), 99),
                str(row.get("review_time", "")),
            ),
        )
        item["reviews"] = item_reviews
        item["review_windows"] = {
            label: next((row for row in reversed(item_reviews) if row.get("window") == label), None)
            for label in ("T0", "T1", "T2", "T5")
        }
        out.append(item)
    out.sort(key=lambda x: str(x.get("rec_time", "")), reverse=True)
    return {
        "ok": True,
        "schema_version": "recommendation-list.v2",
        "review_windows": ["T0", "T1", "T2", "T5"],
        "recommendations": out,
        "ts": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/recommendation-events")
async def get_recommendation_events(request: Request):
    """统一推荐事件账本（append-only）。

    该端点与旧 ``/recommendations`` 并存：旧页面继续消费原结构，新的回验/审计链读取
    不可覆盖的事件。当前只返回新基础上线后产生的事件，不会在没有价格证据时自动伪造旧数据回填。
    """
    st = _state(request)
    events = recommend_mod.event_store_for(st.store).events()
    events.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
    try:
        limit = int(request.query_params.get("limit", "200"))
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 1000))
    return {
        "ok": True,
        "schema_version": recommend_mod.RECOMMENDATION_EVENT_SCHEMA_VERSION,
        "events": events[:limit],
        "count": len(events),
        "ts": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/advice-proposals")
async def get_advice_proposals(request: Request):
    """研究候选/待触发建议账本；proposal 永不进入推荐胜率。"""
    st = _state(request)
    ledger = proposal_mod.proposal_ledger_for(st.store)
    latest: dict[str, dict] = {}
    for event in ledger.events():
        proposal_id = str(event.get("proposal_id") or "")
        if proposal_id:
            latest[proposal_id] = event
    rows = sorted(
        latest.values(), key=lambda row: str(row.get("created_at") or ""), reverse=True,
    )
    try:
        limit = int(request.query_params.get("limit", "200"))
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 1000))
    return {
        "ok": True,
        "schema_version": proposal_mod.ADVICE_PROPOSAL_SCHEMA_VERSION,
        "score_eligible": False,
        "proposals": rows[:limit],
        "count": len(rows),
        "ts": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def _bind_reference_price_evidence(st, body: dict) -> tuple[dict, bool]:
    """把推荐价与服务端证据库的 last_price 真实绑定。

    客户端不能仅传一个 evidence_id 就自称 traceable；必须由后端回读
    并校验代码、字段、数值与质量。没有 ID 时可用当前统一网关快照
    自动绑定；历史手工价匹配不上则保持 unverified，绝不伪造来源。
    """
    out = dict(body)
    code = str(out.get("code") or out.get("symbol") or "").strip()
    base_code = code.split(".", 1)[0].replace("sh", "").replace("sz", "").replace("bj", "")
    raw_price = out.get("reference_price") if out.get("reference_price") not in (None, "") else out.get("rec_price")
    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        return out, False
    if price <= 0:
        return out, False

    nested = out.get("reference_price_evidence")
    nested = dict(nested) if isinstance(nested, dict) else {}
    requested_id = str(
        nested.get("evidence_id") or out.get("price_evidence_id")
        or out.get("reference_price_evidence_id") or ""
    ).strip()
    point = None
    if requested_id:
        store = getattr(st, "evidence_store", None)
        point = await asyncio.to_thread(store.get_point, requested_id) if store is not None else None
        if point is None:
            raise ValueError("推荐价证据不存在")
    else:
        gateway = getattr(st, "market_gateway", None)
        if gateway is not None and len(base_code) == 6 and base_code.isdigit():
            try:
                candidate = await asyncio.to_thread(gateway.get, base_code, "last_price")
                point = candidate.to_dict() if getattr(candidate, "value", None) is not None else None
            except Exception:  # noqa: BLE001 — 自动绑定失败只降级为手工价
                log.exception("推荐价自动证据绑定失败 code=%s", base_code)

    if point is None:
        out.pop("reference_price_evidence", None)
        return out, False
    try:
        point_value = float(point.get("value"))
    except (TypeError, ValueError):
        point_value = None
    symbol_matches = str(point.get("symbol") or "").split(".", 1)[0] == base_code
    value_matches = (
        point_value is not None
        and abs(point_value - price) <= max(0.01, abs(point_value) * 0.002)
    )
    point_time = point.get("event_time") or point.get("available_at") or point.get("ingested_at")
    fresh = False
    try:
        parsed_time = datetime.fromisoformat(str(point_time).replace("Z", "+00:00"))
        if parsed_time.tzinfo is None:
            parsed_time = parsed_time.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - parsed_time.astimezone(timezone.utc)
        cn_now = now_cn()
        in_live_session = (
            cn_now.weekday() < 5
            and "09:15" <= cn_now.strftime("%H:%M") <= "15:30"
        )
        max_age = timedelta(minutes=15) if in_live_session else timedelta(hours=96)
        calendar = await asyncio.to_thread(trade_day_status, cn_now.date())
        if calendar.get("status") == "open" and cn_now.strftime("%H:%M") >= "09:15":
            expected_trade_day = cn_now.date().isoformat()
        else:
            expected_trade_day = await asyncio.to_thread(prev_trade_day, cn_now.date())
        point_trade_day = parsed_time.astimezone(cn_now.tzinfo).date().isoformat()
        fresh = (
            -timedelta(minutes=5) <= age <= max_age
            and bool(expected_trade_day)
            and point_trade_day == expected_trade_day
        )
    except (TypeError, ValueError):
        fresh = False
    valid = (
        symbol_matches
        and point.get("field") == "last_price"
        and value_matches
        and point.get("quality_status") not in {"missing", "conflict"}
        and fresh
    )
    if requested_id and not valid:
        raise ValueError("推荐价与所引行情证据不一致或证据已过期")
    if not valid:
        out.pop("reference_price_evidence", None)
        return out, False

    evidence_id = str(point.get("evidence_id") or requested_id)
    out["reference_price_evidence"] = {
        "value": point_value,
        "currency": "CNY",
        "evidence_id": evidence_id,
        "source_id": point.get("source_id"),
        "observed_at": point.get("event_time"),
        "quality_status": point.get("quality_status"),
    }
    evidence_ids = body.get("evidence_ids")
    evidence_ids = list(evidence_ids) if isinstance(evidence_ids, (list, tuple, set)) else []
    if evidence_id and evidence_id not in evidence_ids:
        evidence_ids.append(evidence_id)
    out["evidence_ids"] = evidence_ids
    return out, bool(point.get("quality_status") == "ok")


async def _authorize_and_record_advice(
    st,
    payload: dict,
    *,
    trusted_internal: bool = False,
    default_source_artifact: dict | None = None,
    preauthorized_risk: dict | None = None,
) -> dict:
    """统一建议封口：先 proposal，再证据绑定与 RiskGate，最后才可晋级推荐。"""
    body = dict(payload or {})
    body.pop("risk_decision", None)
    for untrusted in ("_server_validated", "_score_eligible", "trade_day", "verify_days"):
        body.pop(untrusted, None)
    if not trusted_internal:
        for untrusted in ("entry_trigger", "trigger_status", "source_artifact", "proposal_id"):
            body.pop(untrusted, None)

    allowed_sources = {
        "盘前", "尾盘", "问股", "趋势票", "量化", "量化选股",
        "产业链", "风险提示", "持仓", "手工留痕",
    }
    body["source"] = str(body.get("source") or "手工留痕")
    if body["source"] not in allowed_sources:
        body["source"] = "手工留痕"
    request_id = str(body.get("client_request_id") or f"server-{uuid.uuid4().hex}")[:160]
    body["client_request_id"] = request_id
    body["rec_time"] = now_cn().strftime("%Y-%m-%d %H:%M:%S")
    body["trade_day"] = now_cn().strftime("%Y-%m-%d")
    body, evidence_complete = await _bind_reference_price_evidence(st, body)

    body["proposed_confidence"] = body.get("confidence")
    body["proposed_position_cap_pct"] = body.get("position_cap_pct")
    body["confidence"] = 0.62 if evidence_complete else 0.35
    body["confidence_scale"] = "0_to_1"
    # RiskGate v2 禁止缺仓位时自动补最大值；客户端提案仍只作为提案，
    # 但由服务端规整为明确目标值后才进入闸门。
    proposed_position = _finite_number(body.get("proposed_position_cap_pct"))
    body["position_cap_pct"] = proposed_position
    body["decision_authority"] = "evidence_formula+risk_gate"

    artifact = body.get("source_artifact") if trusted_internal else None
    artifact = artifact if isinstance(artifact, dict) else (
        default_source_artifact or {"kind": "manual_form", "channel": "web"}
    )
    proposal_payload = {
        "client_request_id": f"{request_id}:proposal",
        "source": body["source"],
        "source_artifact": artifact,
        "trade_day": body["trade_day"],
        "symbol": body.get("symbol") or body.get("code"),
        "name": body.get("name"),
        "action": _candidate_action(body) or "watch",
        "entry_condition": body.get("entry_condition") or body.get("buy_condition"),
        "invalidation": body.get("invalidation") or body.get("no_buy_condition")
        or body.get("abandon_condition"),
        "expires_at": body.get("expires_at"),
        "reference_price_evidence": body.get("reference_price_evidence"),
        "state": "proposed",
    }
    ledger = proposal_mod.proposal_ledger_for(st.store)
    proposed = ledger.record(proposal_payload)

    risk_decision = (
        dict(preauthorized_risk)
        if trusted_internal and isinstance(preauthorized_risk, dict)
        else await _server_risk_decision(st, body, evidence_complete=evidence_complete)
    )
    body["risk_decision"] = risk_decision
    is_entry = _candidate_action(body).casefold() in {
        action.casefold() for action in _ENTRY_ACTIONS
    }
    if risk_decision.get("decision") == "veto" and is_entry:
        vetoed = ledger.record({
            "proposal_id": proposed["proposal_id"],
            "client_request_id": f"{request_id}:vetoed",
            "state": "vetoed",
            "risk_decision": risk_decision,
        })
        return {
            "ok": False,
            "blocked": True,
            "error": "确定性风险闸已禁止该新开/加仓建议",
            "risk_decision": risk_decision,
            "proposal_id": vetoed["proposal_id"],
            "recommendation_created": False,
        }

    # 新开/加仓必须先有当前可绑定的真实价格证据。
    # 不能让一张使用 stale/conflict/missing 行情做解释的
    # 诊断卡，因为方向是 upside 就继续进入 RecommendationEvent。
    # 此处保留 proposal 审计轨迹，但以证据门禁 veto，
    # 不制造推荐价、不进胜率、不在服务端打 ERROR。
    if is_entry and not evidence_complete:
        evidence_veto = dict(risk_decision)
        evidence_veto["decision"] = "veto"
        evidence_veto["final_action"] = "avoid"
        evidence_veto["position_cap_pct"] = 0.0
        evidence_veto["evidence_complete"] = False
        evidence_veto["reason_codes"] = list(dict.fromkeys([
            *(evidence_veto.get("reason_codes") or []),
            "reference_price_evidence_incomplete",
        ]))
        evidence_veto["reasons"] = list(dict.fromkeys([
            *(evidence_veto.get("reasons") or []),
            "当前真实价格证据缺失或降级，不允许新开/加仓建议晋级",
        ]))
        vetoed = ledger.record({
            "proposal_id": proposed["proposal_id"],
            "client_request_id": f"{request_id}:vetoed",
            "state": "vetoed",
            "risk_decision": evidence_veto,
        })
        return {
            "ok": False,
            "blocked": True,
            "error": "当前真实价格证据不完整，已降级为观望",
            "risk_decision": evidence_veto,
            "proposal_id": vetoed["proposal_id"],
            "recommendation_created": False,
        }

    approved = ledger.record({
        "proposal_id": proposed["proposal_id"],
        "client_request_id": f"{request_id}:approved",
        "state": "approved",
        "risk_decision": risk_decision,
        "reference_price_evidence": body.get("reference_price_evidence"),
    })
    body["proposal_id"] = approved["proposal_id"]
    body["source_artifact"] = artifact
    body["position_cap_pct"] = risk_decision.get("position_cap_pct")
    body["confidence"] = risk_decision.get("effective_confidence")
    body["confidence_scale"] = "0_to_1"
    direction_group, _ = recommend_mod._direction_group(body)

    # 风险闸可能已把原始“买入”改成“回避”。这时
    # is_entry=False，但回避在回验语义中仍是 downside。若当前
    # 价格本身已缺失，不能继续构造需要推荐价的
    # RecommendationEvent；只保留已批准的观察 proposal。
    record_price = _finite_number(
        body.get("reference_price")
        if body.get("reference_price") not in (None, "")
        else body.get("rec_price")
    )
    if record_price is None or record_price <= 0:
        return {
            "ok": True,
            "proposal_id": approved["proposal_id"],
            "recommendation_created": False,
            "state": "approved_observation",
            "degraded_reason": "reference_price_unavailable",
            "risk_decision": risk_decision,
        }

    # 观察/持有/候选，或尚未有结构化触发证据的条件单，都只停留在 proposal；
    # 它们有历史留痕，但绝不进入可回验战绩。
    entry_condition = str(body.get("entry_condition") or body.get("buy_condition") or "").strip()
    trigger = body.get("entry_trigger") if isinstance(body.get("entry_trigger"), dict) else {}
    trigger_state = str(trigger.get("state") or "").casefold()
    if direction_group not in {"upside", "downside"} or (
        entry_condition and trigger_state != "triggered"
    ):
        return {
            "ok": True,
            "proposal_id": approved["proposal_id"],
            "recommendation_created": False,
            "state": "approved_waiting_trigger" if entry_condition else "approved_observation",
            "risk_decision": risk_decision,
        }

    body["_server_validated"] = True
    body["_score_eligible"] = bool(evidence_complete and direction_group in {"upside", "downside"})
    saved = recommend_mod.record_recommendation(st.store, body)
    return {
        "ok": True,
        "rec_id": saved["rec_id"],
        "event_id": saved["event_id"],
        "proposal_id": approved["proposal_id"],
        "recommendation_created": True,
        "risk_decision": risk_decision,
    }


@router.post("/recommendations")
async def add_recommendation(request: Request):
    """新增推荐留痕。

    保留原 recommendations.json 及 rec_id 响应，同时写入 append-only RecommendationEvent。
    rec_price 非法由统一入口抛 ValueError → 转 400（铁律：价格必须真实/录入）。
    """
    st = _state(request)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        return JSONResponse({"ok": False, "error": "无效请求体"}, status_code=400)
    try:
        result = await _authorize_and_record_advice(st, body)
        if result.get("blocked"):
            return JSONResponse(result, status_code=409)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": f"推荐数据不合法：{e}"}, status_code=400)
    except (OSError, RuntimeError):
        log.exception("推荐双写事务失败")
        return JSONResponse({
            "ok": False,
            "error": "推荐留痕暂未完成，系统已保留可恢复事务，请重试",
            "error_code": "recommendation_transaction_pending",
        }, status_code=503)
    return result


@router.post("/recommendations/verify")
async def verify_recommendations(request: Request):
    """对 active 且**尚未判定**的推荐用真实现价回验→save_rec_review(append-only)。
    铁律§29：到期已判 hit/miss 的推荐结论锁定，跳过且置 closed，绝不被后续实时价反复改判。
    逻辑收敛在 recommend.run_verify()，与收盘后自动回验定时任务(scheduler)复用同一实现。"""
    result = await recommend_mod.run_verify(_state(request).store)
    return {"ok": True, **result}


@router.get("/recommendations/stats")
async def recommendation_stats(request: Request):
    """成功率统计（§29.3）：样本数/胜率/平均收益/平均亏损/盈亏比，全部来自真实 final_return。"""
    st = _state(request)
    s = recommend_mod.stats(st.store.recommendations(), st.store.rec_reviews())
    return {"ok": True, **s}


# ---------------------------------------------------------------- 知识库

@router.get("/knowledge")
async def get_knowledge(request: Request):
    items = _state(request).store.knowledge_items()
    summary: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "legacy_unverified")
        summary[status] = summary.get(status, 0) + 1
    return {
        "ok": True,
        "schema_version": "KnowledgeRule.v1",
        "items": items,
        "summary": summary,
        "review_queue_count": sum(
            count for status, count in summary.items()
            if status in {"draft", "legacy_unverified"}
        ),
    }


@router.get("/knowledge/review-queue")
async def knowledge_review_queue(request: Request):
    items = _state(request).store.review_queue()
    return {
        "ok": True,
        "schema_version": "KnowledgeRule.v1",
        "items": items,
        "count": len(items),
    }


@router.post("/knowledge")
async def add_knowledge(request: Request):
    st = _state(request)
    try:
        body = await request.json()
        title = str(body.get("title", "")).strip()
        content = str(body.get("content", "")).strip()
        category = str(body.get("category", "复盘笔记"))
        assert title and content
    except Exception:
        return JSONResponse({"ok": False, "error": "title 与 content 不能为空"}, status_code=400)
    kid = st.store.add_knowledge(
        category, title, content,
        status="approved",
        origin="explicit_user_input",
        created_by="user:web",
    )
    return {"ok": True, "id": kid}


@router.post("/knowledge/{kid}/approve")
async def approve_knowledge(kid: str, request: Request):
    st = _state(request)
    try:
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        current = next((item for item in st.store.knowledge_items() if item.get("id") == kid), None)
        if current is None:
            raise KeyError("knowledge missing")
        report_ids = (
            body.get("source_report_ids") if "source_report_ids" in body
            else current.get("source_report_ids")
        ) or []
        evidence_ids = (
            body.get("evidence_ids") if "evidence_ids" in body
            else current.get("evidence_ids")
        ) or []
        missing_sources: list[str] = []
        for report_id in report_ids:
            parts = str(report_id).split(":")
            exists = len(parts) == 3 and parts[0] == "report" and bool(st.store.get_report(parts[1], parts[2]))
            if not exists:
                missing_sources.append(str(report_id))
        evidence_store = getattr(st, "evidence_store", None)
        for evidence_id in evidence_ids:
            point = (
                await asyncio.to_thread(evidence_store.get_point, str(evidence_id))
                if evidence_store is not None else None
            )
            if point is None:
                missing_sources.append(str(evidence_id))
        if missing_sources:
            raise ValueError("批准失败：来源/证据不存在：" + "、".join(missing_sources[:10]))
        proposed_samples = body.get("sample_count", current.get("sample_count", 0))
        if current.get("origin") != "explicit_user_input":
            source_count = (
                len(set(map(str, report_ids)))
                + len(set(map(str, evidence_ids)))
                + int(bool(
                    current.get("origin") == "sentiment_event_archive"
                    and current.get("archive_key")
                ))
            )
            if int(proposed_samples or 0) > source_count:
                raise ValueError("批准失败：sample_count 不得超过已核验来源数")
        item = st.store.approve(
            kid,
            approved_by="user:web",
            source_report_ids=(body.get("source_report_ids") if "source_report_ids" in body else None),
            evidence_ids=(body.get("evidence_ids") if "evidence_ids" in body else None),
            sample_count=(body.get("sample_count") if "sample_count" in body else None),
        )
    except KeyError:
        return JSONResponse({"ok": False, "error": "知识条目不存在"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "item": item}


@router.post("/knowledge/{kid}/reject")
async def reject_knowledge(kid: str, request: Request):
    st = _state(request)
    try:
        body = await request.json()
        body = body if isinstance(body, dict) else {}
        item = st.store.reject(
            kid,
            str(body.get("reason") or ""),
            rejected_by="user:web",
        )
    except KeyError:
        return JSONResponse({"ok": False, "error": "知识条目不存在"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "item": item}


@router.post("/knowledge/{kid}/supersede")
async def supersede_knowledge(kid: str, request: Request):
    st = _state(request)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("无效请求体")
        item = st.store.supersede(
            kid,
            title=body.get("title"),
            content=body.get("content"),
            category=body.get("category"),
            superseded_by="user:web",
            origin="explicit_user_input",
            source_report_ids=body.get("source_report_ids"),
            evidence_ids=body.get("evidence_ids"),
            sample_count=body.get("sample_count"),
            reason=str(body.get("reason") or ""),
        )
    except KeyError:
        return JSONResponse({"ok": False, "error": "知识条目不存在"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, "item": item}


@router.delete("/knowledge/{kid}")
async def delete_knowledge(kid: str, request: Request):
    if _state(request).store.delete_knowledge(kid):
        return {"ok": True}
    return {"ok": False, "error": "条目不存在"}


# ---------------------------------------------------------------- 存档

@router.get("/archive/dates")
async def archive_dates(request: Request):
    return {"ok": True, "dates": _state(request).store.archive_dates()}


@router.get("/archive/{date}")
async def get_archive(date: str, request: Request):
    if not _DATE_RE.match(date):
        return {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}
    bundle = _state(request).store.get_archive(date)
    if not bundle:
        return {"ok": False, "error": "该日期无存档"}
    return bundle


# ---------------------------------------------------------------- 设置

@router.get("/settings")
async def get_settings(request: Request):
    return {"ok": True, "settings": _state(request).config.settings_view()}


def _safe_hermes_base_url(url: str) -> bool:
    """校验 OpenAI 兼容 API 终点的结构安全。

    解析 URL 后分字段比较，避免 ``trusted.example@evil``、查询串、
    任意子路径等绕过。公网域名的 DNS 结果还会在保存和
    测试前由 ``_model_endpoint_network_safe`` 逐个核验，防止解析到内网。
    """
    try:
        parsed = urlsplit(str(url).strip())
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if (
        not parsed.scheme
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        return False

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if host in {"127.0.0.1", "localhost", "::1"}:
        return parsed.scheme in {"http", "https"} and (port is None or 1 <= port <= 65535)
    if (
        parsed.scheme != "https"
        or (port is not None and not 1 <= port <= 65535)
        or not host
    ):
        return False
    # 单标签主机名通常是内网搜索域；.local 为 mDNS，均不允许携密钥访问。
    if "." not in host or host.endswith((".local", ".localhost", ".internal", ".home", ".lan")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        try:
            host.encode("idna")
        except UnicodeError:
            return False
        return True


_SYNTHETIC_PUBLIC_DNS_NETWORKS = (
    # macOS/Codex 的透明网络代理会把公网域名合成到 RFC 2544
    # benchmark 网段，再按原始 HTTPS hostname 转发。这个地址不是用户
    # 可以直接填写的模型终点（_safe_hermes_base_url 仍会拒绝它），
    # 这里只兼容“合法公网域名经本机解析器得到合成地址”的情况。
    ipaddress.ip_network("198.18.0.0/15"),
)


def _model_endpoint_resolved_address_safe(address: str) -> bool:
    """判断域名的一条解析结果能否用于模型连接。

    普通环境只接受真实 global IP；透明代理环境额外接受其 DNS 合成网段。
    回环、私网、链路本地、云元数据等地址仍全部拒绝。
    """
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return False
    return bool(
        ip.is_global
        or any(ip in network for network in _SYNTHETIC_PUBLIC_DNS_NETWORKS)
    )


async def _model_endpoint_network_safe(url: str) -> bool:
    """公网自定义终点必须解析到 global IP 或本机代理的公网合成 IP。

    localhost 是显式的本机集成例外，但其它私网、回环、链路本地、
    保留和多播地址全部拒绝。合成 IP 只接受域名解析结果，不能作为
    用户填写的 endpoint。DNS 失败按 fail-closed 处理。
    """
    if not _safe_hermes_base_url(url):
        return False
    parsed = urlsplit(str(url).strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True
    port = parsed.port or 443
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, port, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    except OSError:
        return False
    addresses = {str(item[4][0]).split("%", 1)[0] for item in infos if item[4]}
    if not addresses:
        return False
    return all(_model_endpoint_resolved_address_safe(address) for address in addresses)


def _valid_model_setting_text(value: object, *, limit: int) -> bool:
    text = str(value or "").strip()
    return bool(text and len(text) <= limit and all(ord(char) >= 32 for char in text))


async def _validated_model_connection_payload(
    st,
    body: dict,
    *,
    existing: dict | None = None,
) -> tuple[dict | None, JSONResponse | None]:
    """Normalize one create/update payload without ever reading a secret into responses."""
    provider = str(
        body.get("provider")
        if "provider" in body
        else (existing or {}).get("provider") or "custom"
    ).strip()
    base_url = str(
        body.get("base_url")
        if "base_url" in body
        else (existing or {}).get("base_url") or ""
    ).strip().rstrip("/")
    model = str(
        body.get("model")
        if "model" in body
        else (existing or {}).get("model") or ""
    ).strip()
    name = str(
        body.get("name")
        if "name" in body
        else (existing or {}).get("name") or f"{provider} · {model}"
    ).strip()
    if not _valid_model_setting_text(provider, limit=80):
        return None, JSONResponse(
            {"ok": False, "error": "请填写有效的 provider"}, status_code=400
        )
    if not _valid_model_setting_text(name, limit=120):
        return None, JSONResponse(
            {"ok": False, "error": "请填写有效的连接名称"}, status_code=400
        )
    if not _valid_model_setting_text(model, limit=200):
        return None, JSONResponse(
            {"ok": False, "error": "请填写有效的模型名"}, status_code=400
        )
    if len(base_url) > 2048 or not _safe_hermes_base_url(base_url):
        return None, JSONResponse(
            {
                "ok": False,
                "error": "模型地址必须是公网 HTTPS /v1（或显式 localhost），不得带账号、查询参数或子路径",
            },
            status_code=400,
        )
    if not await _model_endpoint_network_safe(base_url):
        return None, JSONResponse(
            {"ok": False, "error": "模型地址无法解析到安全的公网地址，已拒绝保存"},
            status_code=400,
        )

    api_key_supplied = bool(str(body.get("api_key") or "").strip())
    clear_api_key = body.get("clear_api_key") is True
    api_key = "" if clear_api_key else (
        str(body.get("api_key") or "").strip() if api_key_supplied else None
    )
    if api_key is not None and len(api_key) > 4096:
        return None, JSONResponse(
            {"ok": False, "error": "API Key 过长"}, status_code=400
        )
    parsed = urlsplit(base_url)
    local_endpoint = (parsed.hostname or "").lower() in {
        "127.0.0.1", "localhost", "::1",
    }
    endpoint_changed = bool(
        existing
        and base_url.rstrip("/") != str(existing.get("base_url") or "").rstrip("/")
    )
    has_saved_key = bool((existing or {}).get("api_key_set"))
    if not local_endpoint and not api_key_supplied and (
        existing is None or endpoint_changed or not has_saved_key or clear_api_key
    ):
        return None, JSONResponse(
            {"ok": False, "error": "公网模型连接需要该端点的 API Key"}, status_code=400
        )

    # 只有用户明确声明 Hermes provider 时才约束到项目受限端口。
    # Ollama/vLLM/自建 OpenAI 兼容服务不再被强制绕经 Hermes。
    provider_key = provider.lower().replace("_", "-")
    if (
        local_endpoint
        and provider_key in {"hermes", "hermes-agent", "hermes-gateway"}
        and bool(getattr(st.config, "agent_require_profile_attestation", True))
    ):
        configured = urlsplit(st.config._hermes_base_url)  # noqa: SLF001
        configured_port = configured.port or (443 if configured.scheme == "https" else 80)
        next_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if next_port != configured_port:
            return None, JSONResponse(
                {"ok": False, "error": "Hermes 只允许项目专属受限网关端口"},
                status_code=400,
            )

    return {
        "name": name,
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
    }, None


@router.get("/settings/model-connections")
async def list_model_connections(request: Request):
    config = _state(request).config
    return {
        "ok": True,
        "active_connection_id": config.active_model_connection_id,
        "connections": config.model_connections_view(),
    }


@router.post("/settings/model-connections")
async def create_model_connection(request: Request):
    st = _state(request)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        return JSONResponse({"ok": False, "error": "无效请求体"}, status_code=400)
    payload, error = await _validated_model_connection_payload(st, body)
    if error is not None:
        return error
    assert payload is not None
    connection_id = uuid.uuid4().hex
    connection = st.config.save_model_connection(
        connection_id,
        **payload,
        activate=body.get("activate") is not False,
    )
    return {
        "ok": True,
        "active_connection_id": st.config.active_model_connection_id,
        "connection": connection,
    }


@router.put("/settings/model-connections/{connection_id}")
async def update_model_connection(connection_id: str, request: Request):
    st = _state(request)
    existing = st.config.model_connection_view(connection_id)
    if existing is None:
        return JSONResponse(
            {"ok": False, "error": "模型连接不存在"}, status_code=404
        )
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        return JSONResponse({"ok": False, "error": "无效请求体"}, status_code=400)
    payload, error = await _validated_model_connection_payload(
        st, body, existing=existing
    )
    if error is not None:
        return error
    assert payload is not None
    connection = st.config.save_model_connection(
        connection_id,
        **payload,
        activate=bool(body.get("activate", existing.get("active"))),
        require_existing=True,
    )
    return {
        "ok": True,
        "active_connection_id": st.config.active_model_connection_id,
        "connection": connection,
    }


@router.post("/settings/model-connections/{connection_id}/activate")
async def activate_model_connection(connection_id: str, request: Request):
    config = _state(request).config
    try:
        connection = config.activate_model_connection(connection_id)
    except KeyError:
        return JSONResponse(
            {"ok": False, "error": "模型连接不存在"}, status_code=404
        )
    return {
        "ok": True,
        "active_connection_id": config.active_model_connection_id,
        "connection": connection,
    }


@router.delete("/settings/model-connections/{connection_id}")
async def delete_model_connection_by_id(connection_id: str, request: Request):
    config = _state(request).config
    if not config.delete_model_connection(connection_id):
        return JSONResponse(
            {"ok": False, "error": "模型连接不存在"}, status_code=404
        )
    return {
        "ok": True,
        "deleted_connection_id": connection_id,
        "active_connection_id": config.active_model_connection_id,
        "connections": config.model_connections_view(),
    }


def _safe_feishu_webhook(url: str) -> bool:
    """飞书 webhook 是发送凭据，只允许官方 HTTPS 机器人终点。"""
    try:
        parsed = urlsplit(str(url).strip())
        port = parsed.port
    except (TypeError, ValueError):
        return False
    prefix = "/open-apis/bot/v2/hook/"
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in {"open.feishu.cn", "open.larksuite.com"}
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path.startswith(prefix)
        and len(parsed.path) > len(prefix)
        and "/" not in parsed.path[len(prefix):]
    )


@router.put("/settings")
async def put_settings(request: Request):
    st = _state(request)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        return JSONResponse({"ok": False, "error": "无效请求体"}, status_code=400)
    allowed = {
        "feishu_webhook", "feishu_secret", "push_enabled",
        "hermes_base_url", "hermes_model", "hermes_api_key", "schedule",
    }
    patch = {k: v for k, v in body.items() if k in allowed}
    # 三类凭据留空="不修改"（GET 不回传明文），避免把脱敏占位写回。
    if not str(patch.get("feishu_webhook", "")).strip():
        patch.pop("feishu_webhook", None)
    if not str(patch.get("feishu_secret", "")).strip():
        patch.pop("feishu_secret", None)
    if not str(patch.get("hermes_api_key", "")).strip():
        patch.pop("hermes_api_key", None)
    for key in ("feishu_webhook", "feishu_secret", "hermes_base_url", "hermes_api_key"):
        if key in patch:
            patch[key] = str(patch[key]).strip()
    if "hermes_model" in patch:
        patch["hermes_model"] = str(patch["hermes_model"]).strip()
        if (
            not patch["hermes_model"]
            or len(patch["hermes_model"]) > 200
            or any(ord(char) < 32 for char in patch["hermes_model"])
        ):
            return JSONResponse(
                {"ok": False, "error": "请填写有效的模型名"}, status_code=400
            )
    if len(str(patch.get("hermes_base_url") or "")) > 2048:
        return JSONResponse({"ok": False, "error": "模型地址过长"}, status_code=400)
    if len(str(patch.get("hermes_api_key") or "")) > 4096:
        return JSONResponse({"ok": False, "error": "API Key 过长"}, status_code=400)
    if "feishu_webhook" in patch and not _safe_feishu_webhook(patch["feishu_webhook"]):
        return JSONResponse(
            {"ok": False, "error": "Webhook 仅允许飞书/Lark 官方 HTTPS 机器人地址"},
            status_code=400,
        )
    # SSRF 防护：自定义 endpoint 必须为安全的 /v1 结构，且 DNS 不得指向内网。
    if "hermes_base_url" in patch and not _safe_hermes_base_url(patch["hermes_base_url"]):
        return JSONResponse(
            {"ok": False, "error": "模型地址必须是公网 HTTPS /v1（或显式 localhost），不得带账号、查询参数或子路径"},
            status_code=400,
        )
    if "hermes_base_url" in patch and not await _model_endpoint_network_safe(patch["hermes_base_url"]):
        return JSONResponse(
            {"ok": False, "error": "模型地址无法解析到安全的公网地址，已拒绝保存"},
            status_code=400,
        )
    if "hermes_base_url" in patch:
        current_host = (urlsplit(st.config.hermes_base_url).hostname or "").lower()
        next_parsed = urlsplit(str(patch["hermes_base_url"]))
        next_host = (next_parsed.hostname or "").lower()
        if (
            next_host in {"127.0.0.1", "localhost", "::1"}
            and bool(getattr(st.config, "agent_require_profile_attestation", True))
        ):
            configured = urlsplit(st.config._hermes_base_url)  # noqa: SLF001 -- 受限 profile 固定端口
            configured_port = configured.port or (443 if configured.scheme == "https" else 80)
            next_port = next_parsed.port or (443 if next_parsed.scheme == "https" else 80)
            if next_port != configured_port:
                return JSONResponse(
                    {"ok": False, "error": "本机 AI 只允许项目专属受限网关，不得切换到全局广权限端口"},
                    status_code=400,
                )
        if next_host not in {"127.0.0.1", "localhost", "::1"}:
            # 切换公网供应商时不得沿用旧站点凭据；同一终点
            # 留空可沿用已安全保存的 key。
            same_endpoint = (
                str(patch["hermes_base_url"]).rstrip("/")
                == str(st.config.hermes_base_url).rstrip("/")
            )
            if "hermes_api_key" not in patch and not (
                same_endpoint and bool(st.config.hermes_api_key)
            ):
                message = (
                    "切换公网模型服务时必须同时输入该服务的 API Key"
                    if next_host != current_host else "公网模型连接需要 API Key"
                )
                return JSONResponse({"ok": False, "error": message}, status_code=400)
    if not patch:
        return {"ok": False, "error": "没有可更新的字段"}
    st.config.save_overrides(patch)
    if "schedule" in patch:
        apply_schedule(st.scheduler, st.config)
    return {"ok": True}


@router.post("/settings/model/test")
async def test_model_connection(request: Request):
    """测试当前表单，不先保存，不回传或记录 API Key。"""
    st = _state(request)
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        return JSONResponse({"ok": False, "error": "无效请求体"}, status_code=400)
    base_url = str(body.get("base_url") or "").strip()
    model = str(body.get("model") or "").strip()
    api_key = str(body.get("api_key") or "").strip()
    connection_id = str(body.get("connection_id") or "").strip()
    if not base_url or not model:
        return JSONResponse(
            {"ok": False, "error": "请完整填写接口地址和模型名"}, status_code=400
        )
    if not _safe_hermes_base_url(base_url) or not await _model_endpoint_network_safe(base_url):
        return JSONResponse(
            {"ok": False, "error": "模型地址未通过公网/localhost SSRF 安全检查"}, status_code=400
        )
    if not api_key and connection_id:
        saved = st.config.model_connection_view(connection_id)
        if saved and base_url.rstrip("/") == str(saved.get("base_url") or "").rstrip("/"):
            api_key = st.config.model_connection_api_key(connection_id)
    same_endpoint = base_url.rstrip("/") == st.config.hermes_base_url.rstrip("/")
    if not api_key and same_endpoint:
        api_key = st.config.hermes_api_key
    if not api_key and (urlsplit(base_url).hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
        return JSONResponse(
            {"ok": False, "error": "公网模型连接需要 API Key"}, status_code=400
        )
    probe = await HermesClient.probe_model_endpoint(base_url, api_key, model)
    if not probe.get("connected"):
        return JSONResponse(
            {"ok": False, "error": probe.get("error") or "连接失败"}, status_code=400
        )
    return {"ok": True, "connected": True, "model": model, "base_url": base_url}


@router.delete("/settings/model")
async def delete_model_connection(request: Request):
    """删除用户保存的自定义连接，恢复 config.yaml 默认/可选 Hermes 集成。"""
    st = _state(request)
    active_id = getattr(st.config, "active_model_connection_id", None)
    if active_id:
        st.config.delete_model_connection(active_id)
    st.config.delete_overrides({"hermes_base_url", "hermes_model", "hermes_api_key"})
    return {
        "ok": True,
        "deleted_connection_id": active_id,
        "custom_connection": st.config.model_connection_custom,
    }


# ---------------------------------------------------------------- 市场特征 features

@router.get("/features/money-effect")
async def feat_money_effect():
    return await asyncio.to_thread(features.theme_money_effect)


@router.get("/features/crowdedness")
async def feat_crowdedness():
    return await asyncio.to_thread(features.crowdedness)


@router.get("/features/leaders")
async def feat_leaders():
    return await asyncio.to_thread(features.sentiment_leaders)


@router.get("/features/mainline")
async def feat_mainline(request: Request):
    st = _state(request)
    return await asyncio.to_thread(features.market_mainline, st.store)


@router.get("/features/institutional")
async def feat_institutional():
    return await asyncio.to_thread(features.institutional_capacity)


# ---------------------------------------------------------------- 资金择时 timing

@router.get("/timing")
async def timing_endpoint(code: str | None = None):
    """资金择时：贪恐五因子 + 多指数0AMV四区域 + (可选 code) 个股四区域。
    code 为 6 位个股代码时附 stock 区域；非法/缺失则仅返回指数与贪恐。"""
    return await asyncio.to_thread(timing_mod.timing, (code or "").strip() or None)


# ---------------------------------------------------------------- 量化套件 quant

@router.get("/quant/rank")
async def quant_rank(top_n: int = 50):
    """沪深300 多因子打分榜(动量/PE低估/PB低估/低换手/量比 z-score 合成 TOP top_n)。"""
    return await asyncio.to_thread(quant.factor_rank, max(10, min(top_n, 300)))


@router.get("/quant/factor")
async def quant_factor(periods: int = 10):
    """因子有效性: Spearman IC / ICIR / t / 五分层前向收益(沪深300, 周频, 无未来函数)。"""
    return await asyncio.to_thread(quant.factor_eval, max(3, min(periods, 12)))


@router.get("/quant/backtest")
async def quant_backtest(days: int = 250, fast: int = 20, slow: int = 60):
    """沪深300 双均线择时 vs 买入持有, 真净值回测(信号次日生效, 无未来函数)。
    days 回测窗口(交易日, 60~500) / fast 快线 / slow 慢线。"""
    return await asyncio.to_thread(quant.backtest, days, fast, slow)


# ---------------------------------------------------------------- 条件/多因子选股 screener

@router.post("/screen")
async def screen(request: Request):
    """条件/多因子选股：全市场免费公开快照与行业映射筛选，命中全部返回(按 sort_by 截到 result_limit)。
    body: {filters:{pe_min,pe_max,pb_min,pb_max,exclude_loss,turnover_min,turnover_max,
    volume_ratio_min,circ_mv_min,circ_mv_max(亿元),chg_min,chg_max,amount_min(亿元),
    boards:[主板/创业板/科创板/北交所],industry_kw,exclude_st}, sort_by, sort_desc, result_limit}"""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except Exception:
        return JSONResponse({"ok": False, "error": "无效请求体"}, status_code=400)
    filters = body.get("filters") if isinstance(body.get("filters"), dict) else {}
    sort_by = str(body.get("sort_by") or "amount")
    sort_desc = body.get("sort_desc", True) is not False
    try:
        result_limit = int(body.get("result_limit") or 300)
    except (TypeError, ValueError):
        result_limit = 300
    return await asyncio.to_thread(
        screener.screen, filters, sort_by, sort_desc, result_limit
    )


# ---------------------------------------------------------------- 技术面选股（只读本地缓存；刷新是显式重型动作）

@router.get("/technical-screen/catalog")
async def technical_screen_catalog():
    return await asyncio.to_thread(technical_screener_mod.catalog)


@router.get("/technical-screen/status")
async def technical_screen_status(include_counts: bool = True):
    return await asyncio.to_thread(technical_screener_mod.status, include_counts=include_counts)


@router.post("/technical-screen/query")
async def technical_screen_query(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except Exception:
        return JSONResponse({"ok": False, "state": "invalid", "error": "无效请求体"}, status_code=400)
    result = await asyncio.to_thread(technical_screener_mod.screen, body)
    status_code = 400 if result.get("state") == "invalid" else 200
    return JSONResponse(result, status_code=status_code)


@router.post("/technical-screen/refresh")
async def technical_screen_refresh(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}
    if technical_screener_mod.refresh_running():
        return JSONResponse({"ok": False, "state": "running", "error_code": "refresh_already_running"}, status_code=409)
    try:
        app_state = _state(request)
        refresh_config = app_state.config.section("technical_signal_refresh")
        default_workers = refresh_config.get("workers", 4)
        limit = body.get("max_symbols")
        limit = None if limit in (None, "") else max(1, min(int(limit), 6000))
        workers = max(1, min(int(body.get("workers") or default_workers), 12))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "state": "invalid", "error": "刷新参数不合法"}, status_code=400)

    async def _run_refresh() -> None:
        store = getattr(app_state, "store", None)
        started = now_cn().strftime("%Y-%m-%d %H:%M:%S")
        if store:
            store.record_job("technical_signal_refresh", last_run=started, last_status="running", last_error=None)
        result = await asyncio.to_thread(technical_screener_mod.refresh_all, max_symbols=limit, workers=workers)
        if store:
            finished = now_cn().strftime("%Y-%m-%d %H:%M:%S")
            store.record_job(
                "technical_signal_refresh",
                last_status=("ok" if result.get("state") == "ready" else "warn"),
                last_ok=(finished if result.get("state") == "ready" else None),
                last_finished=finished, last_error=None,
                last_result={key: result.get(key) for key in ("state", "processed", "succeeded", "failed", "coverage_ratio")},
            )

    task = asyncio.create_task(_run_refresh())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return JSONResponse({"ok": True, "state": "queued", "message": "全市场技术信号刷新已进入后台队列"}, status_code=202)


@router.get("/technical-screen/history")
async def technical_screen_history(
    code: str | None = None, signal_code: str | None = None,
    timeframe: str | None = None, limit: int = 200,
):
    if code and not _CODE_RE.fullmatch(code):
        return JSONResponse({"ok": False, "error": "股票代码必须为6位数字"}, status_code=400)
    if timeframe and timeframe not in {"D", "W", "M"}:
        return JSONResponse({"ok": False, "error": "周期必须为D/W/M"}, status_code=400)
    return await asyncio.to_thread(
        technical_screener_mod.history, code=code, signal_code=signal_code,
        timeframe=timeframe, limit=limit,
    )


@router.get("/technical-screen/schemes")
async def technical_screen_schemes():
    return {"ok": True, "schemes": await asyncio.to_thread(technical_screener_mod.list_schemes)}


@router.post("/technical-screen/schemes")
async def technical_screen_scheme_create(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except Exception:
        return JSONResponse({"ok": False, "error": "无效请求体"}, status_code=400)
    result = await asyncio.to_thread(technical_screener_mod.save_scheme, body)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.put("/technical-screen/schemes/{scheme_id}")
async def technical_screen_scheme_update(scheme_id: str, request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except Exception:
        return JSONResponse({"ok": False, "error": "无效请求体"}, status_code=400)
    result = await asyncio.to_thread(technical_screener_mod.save_scheme, body, scheme_id)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@router.delete("/technical-screen/schemes/{scheme_id}")
async def technical_screen_scheme_delete(scheme_id: str):
    result = await asyncio.to_thread(technical_screener_mod.delete_scheme, scheme_id)
    return JSONResponse(result, status_code=200 if result.get("ok") else 404)


# ---------------------------------------------------------------- 多市场新闻

@router.get("/news/multi")
async def news_multi():
    return await asyncio.to_thread(news.multi_market_news)


@router.get("/news/a-share")
async def news_a_share():
    """新闻到 A 股公司/行业/概念的确定性映射；盘面字段只表达观察，不宣称因果。"""
    return await asyncio.to_thread(news.a_share_news)


@router.post("/news/a-share/archive")
async def archive_news_a_share(request: Request):
    """把用户当前选中的事件幂等留档为知识草稿，不修改新闻源快照。"""
    try:
        body = await request.json()
        event = body.get("event") if isinstance(body, dict) else None
        if not isinstance(event, dict):
            raise ValueError("event 必须是对象")
        result = await asyncio.to_thread(_state(request).store.archive_sentiment_event, event)
    except (TypeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {"ok": True, **result}


@router.get("/news/a-share/observations")
async def news_a_share_observations(limit: int = 100):
    """持久事件观察账本；只返回公共新闻映射与盘面事实，不含客户私有数据。"""
    from ..data import news_observation  # noqa: PLC0415

    return await asyncio.to_thread(news_observation.history, max(1, min(limit, 500)))


@router.get("/news/a-share/reconciliation")
async def news_a_share_reconciliation(date: str | None = None, limit: int = 100):
    """按交易日读取事件观察对账。只读本地 append-only 账本，不触发新闻或行情网络请求。"""
    from ..data import news_observation  # noqa: PLC0415

    trade_day = str(date or now_cn().strftime("%Y-%m-%d"))
    if not _DATE_RE.match(trade_day):
        return {"ok": False, "error": "日期格式应为 YYYY-MM-DD"}
    result = await asyncio.to_thread(
        news_observation.daily_reconciliation,
        trade_day,
        max(1, min(limit, 500)),
    )
    return {**result, "ledger_only": True, "network_requested": False}


# ---------------------------------------------------------------- 数据源健康面板

@router.get("/datasources")
async def datasources(request: Request):
    st = _state(request)
    cfg = st.config
    # 即时探测 Hermes 网关延迟
    import time as _t
    hermes = {"name": "Hermes AI 网关", "base_url": cfg.hermes_base_url, "model": cfg.hermes_model,
              "kind": "本机Hermes网关" if "127.0.0.1" in cfg.hermes_base_url or "localhost" in cfg.hermes_base_url else "云端relay"}
    t0 = _t.monotonic()
    hermes["connected"] = await st.hermes.check_connection()
    hermes["latency_ms"] = round((_t.monotonic() - t0) * 1000)

    import os as _os
    rmeta = market.last_relay_meta()
    # DATA_RELAY_API / proxy URL 可能携带 access token。数据源健康页只需知道链路是否配置，
    # 绝不能把完整中继地址或代理串回传到浏览器。
    relay_api_configured = bool(_os.environ.get("DATA_RELAY_API", ""))
    relay_proxy_configured = bool(relay_proxy())
    relay_enabled = relay_api_configured or relay_proxy_configured
    relay = {
        "enabled": relay_enabled,
        "transport": (
            "api" if relay_api_configured else ("proxy" if relay_proxy_configured else "direct")
        ),
        "api_configured": relay_api_configured,
        "proxy_configured": relay_proxy_configured,
        "spot": {
            "count": rmeta.get("count"), "total": rmeta.get("total"),
            "failed_pages": len(rmeta.get("failed_pages", []) or []),
            "stale": rmeta.get("stale"), "age_sec": round(rmeta.get("age")) if rmeta.get("age") else None,
            "incomplete": rmeta.get("incomplete"),
        } if rmeta else None,
    }
    # configured 与 FeishuPusher.configured 同语义：webhook 或 应用凭据(bot API 直发)任一即已配置
    # （审计反查：原仅看 webhook，纯 app 凭据部署时设置页显"未配置"与工具栏矛盾）
    feishu = {"configured": bool(cfg.feishu_webhook) or bool(getattr(cfg, "feishu_app_id", "") and getattr(cfg, "feishu_app_secret", "")),
              "bot": bool(getattr(cfg, "feishu_app_id", ""))}
    tdx_api_health = await asyncio.to_thread(tdx_api.service_health)
    # 股票池 canonical 状态只读一次：顶部摘要与数据源表格共用同一快照，
    # 避免备用构建路径的失败埋点把已完整的股票池误报为 down。
    canonical_universe = public_data.stock_universe_status()
    report = await asyncio.to_thread(
        sources.build_report,
        {"hermes": hermes, "relay": relay, "feishu": feishu, "tdx_api": tdx_api_health},
        canonical_universe,
    )
    market_gateway = getattr(st, "market_gateway", None)
    if market_gateway is not None:
        report["field_contract"] = await asyncio.to_thread(market_gateway.contract)
    # 设置页只读最近已有状态，不因查看健康页额外请求交易所。
    report["universe"] = canonical_universe
    from ..data import news_observation  # noqa: PLC0415
    news_summary = news_observation.observation_summary()
    news_job = (
        (st.store.get_job_states() or {}).get("news_event_observation", {})
        if getattr(st, "store", None) is not None else {}
    )
    report["news_observation"] = {
        **news_summary,
        "last_run": news_job.get("last_run"),
        "last_finished": news_job.get("last_finished"),
        "last_status": news_job.get("last_status"),
        "last_error": news_job.get("last_error"),
        "last_result": news_job.get("last_result") or {},
    }
    evidence_store = getattr(st, "evidence_store", None)
    if evidence_store is not None:
        registry, evidence_summary = await asyncio.gather(
            asyncio.to_thread(evidence_store.list_sources),
            asyncio.to_thread(evidence_store.stats),
        )
        # 老版本曾把本地报价适配器注册为 ``tdx_quote``。适配器已从母版删除，
        # 但客户运行库可能仍保留历史 registry 行；健康页只展示当前可调用源，
        # 避免把退役链路误报成仍在运行。历史证据数据不做破坏性删除。
        retired_source_ids = {"tdx_quote", "opentdx"}
        report["evidence_registry"] = [
            item for item in registry
            if str(item.get("source_id") or "") not in retired_source_ids
        ]
        report["retired_evidence_sources"] = sorted(
            str(item.get("source_id") or "")
            for item in registry
            if str(item.get("source_id") or "") in retired_source_ids
        )
        report["evidence_stats"] = evidence_summary
        if market_gateway is not None:
            report["evidence_health"] = await asyncio.to_thread(market_gateway.health)
    return report


# ---------------------------------------------------------------- DIY 研究工作流

@router.get("/diy/templates")
async def diy_list(request: Request):
    return {"ok": True, "templates": _state(request).store.diy_templates()}


@router.post("/diy/templates")
async def diy_add(request: Request):
    st = _state(request)
    try:
        body = await request.json()
        name = str(body.get("name", "")).strip()
        tpl = str(body.get("prompt_template", "")).strip()
        ttype = str(body.get("target_type", "free"))
        assert name and tpl
    except Exception:
        return JSONResponse({"ok": False, "error": "name 与 prompt_template 不能为空"}, status_code=400)
    tid = st.store.add_diy_template(name, ttype, tpl)
    return {"ok": True, "id": tid}


@router.delete("/diy/templates/{tid}")
async def diy_delete(tid: str, request: Request):
    if _state(request).store.delete_diy_template(tid):
        return {"ok": True}
    return {"ok": False, "error": "模板不存在"}


@router.post("/diy/run")
async def diy_run(request: Request):
    st = _state(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)
    ttype = str(body.get("target_type", "free"))
    code = str(body.get("code", "")).strip()
    theme = str(body.get("theme", "")).strip()
    tpl = str(body.get("prompt_template", "")).strip()
    if not tpl and body.get("template_id"):
        rec = st.store.get_diy_template(str(body["template_id"]))
        if not rec:
            return JSONResponse({"ok": False, "error": "模板不存在"}, status_code=400)
        tpl = rec.get("prompt_template", "")
        ttype = rec.get("target_type", ttype)
    if not tpl:
        return JSONResponse({"ok": False, "error": "缺少 prompt_template 或 template_id"}, status_code=400)
    if ttype == "stock" and not _CODE_RE.match(code):
        return JSONResponse({"ok": False, "error": "stock 类型需提供 6 位代码"}, status_code=400)
    if ttype == "theme" and not theme:
        return JSONResponse({"ok": False, "error": "theme 类型需提供题材名"}, status_code=400)
    return _sse(st.engine.diy_run_stream(tpl, ttype, code=code, theme=theme))


@router.post("/push/test")
async def push_test(request: Request):
    ok, msg = await _state(request).feishu.push_test()
    # 成功语放 msg、失败原因放 error，避免成功时也挂在 error 字段上显得像出错
    return {"ok": ok, **({"msg": msg} if ok else {"error": msg})} if msg else {"ok": ok}
