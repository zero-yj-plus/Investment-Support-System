"""个股诊断·证据链卡（结构化输出） — P0 陪跑核心。

与「盘中问股」(engine.chat_stream mode=stock) 的关键差异：
- 问股是 **SSE 自由文本**（Markdown 散文），适合人读、不可机器消费；
- 本模块产出 **严格 JSON 的证据链卡**（action 八态 / conviction / 狙击位 / 失效位 /
  evidence[出处] / missing），前后端按同一 schema 对齐，可结构化渲染成决策卡
  （参考 ashare-agent 的 .dcard / renderDecisionCard），并可被质检/回测程序消费。

数据真实性铁律（贯穿）：
- 所有数字只来自 data.stock.stock_detail(code) 的真实抓取结果（盘口/分时/日K/资金流/板块）；
  AI 只负责【组织与判断方向】，绝不生成/估算/修饰任何数字。
- stock_detail 取不到的字段会进入它自己的 missing 列表；本模块把该 missing 原样透传进
  卡片的 missing，并在 prompt 里明确「缺就标 missing，绝不编造」。
- entry_zone(狙击位)/stop(失效位) 由 AI 依据注入的真实价位/均线/前高前低【推导】，
  必须是注入数据可支撑的价位区间；无可支撑数据则置 null（不硬编一个数字给交易员）。
- AI 解析失败（非 JSON / schema 不符）一律优雅回退为「观望/低」的安全卡片，绝不抛错阻断。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import threading
import time

from ..agent import prompts
from ..data import stock
from ..data import free_source as tx
from ..data.base import now_cn
from .methodology import load_methodology

log = logging.getLogger("hermes.diagnosis")

# 诊断卡结果缓存：同股 90 秒内重复看直接返回（秒开 + 省 AI）。盘中价格会动，故只短缓存；
# 只缓存成功卡（失败/回退卡不进缓存，避免把一次瞬时失败钉死 90 秒）。
_DIAG_CACHE: dict = {}
_DIAG_TTL = 90.0

# 诊断页是高频交互入口，不应因未配置/失联的模型端点走完
# HermesClient 的多次网络超时。已配置的直连、本地模型仍使用同一
# ``check_connection`` 契约，只把预检时间限制在交互可接受的边界内。
# 公网 OpenAI-compatible 服务首次 TLS + /models 探测常见需要 2–5 秒。
# 旧值 1 秒会把已经通过设置页测试的 x402 等端点误判成“模型未连接”，
# 导致问股在真正发起生成前就回退规则版。仍以 HermesClient 自身 8 秒
# 网络上限为硬边界，缓存命中时不会额外等待。
_MODEL_CONNECTION_PREFLIGHT_TIMEOUT = 8.0
_FAST_MARKET_DATA_TIMEOUT = 4.0
_FULL_STOCK_DETAIL_TIMEOUT = 6.0
_FUNDAMENTAL_DATA_TIMEOUT = 1.0
# ``asyncio.wait_for(to_thread(...))`` 可以按时返回 HTTP，但无法强制终止
# 已进入同步 socket 的线程。用线程侧信号量绑定诊断慢数据的最大收尾数，
# 防止用户连续点击时把默认 executor 占满。
_DIAGNOSIS_DATA_SLOTS = threading.BoundedSemaphore(4)
_DIAGNOSIS_DATA_SLOT_WAIT = 1.0
_DATA_SLOT_UNAVAILABLE = object()

# 诊断页优先暴露的可点击定量证据。这些字段由 MarketDataGateway
# 统一定义单位、主备源、冲突阈值和 PIT 时点，不由模型生成。
_MARKET_EVIDENCE_FIELDS = (
    "last_price", "prev_close", "change_pct", "amount",
    "turnover_rate", "volume_ratio", "pe", "pb",
)

# action 八态白名单（schema 强约束）：AI 越界返回的动作一律回退到「观望」，
# 避免把模型臆造的、不在产品决策语义内的动作透出给交易员。
_ACTIONS = ("买入", "加仓", "持有", "减仓", "清仓", "回避", "观望", "止损")
# 信心三档。
_CONVICTIONS = ("高", "中", "低")
# 证据方向三态。
_DIRECTIONS = ("利多", "利空", "中性")

# 诊断卡 schema 说明（注入 prompt，让 AI 知道每个字段含义与取值域）。与前端渲染契约一致。
_SCHEMA_HINT = """{
 "code": "600519",
 "name": "贵州茅台",
 "action": "买入|加仓|持有|减仓|清仓|回避|观望|止损",
 "conviction": "高|中|低",
 "entry_zone": "进场/狙击位 如 1680-1700（数据不足以给出则 null）",
 "stop": "失效/止损位 如 跌破1650（数据不足以给出则 null）",
 "evidence": [
   {"finding": "具体证据(引用注入的数字/盘口/资金/位置)", "direction": "利多|利空|中性", "source": "出处 如 实时盘口/公开资金流/日K/板块"}
 ],
 "summary": "一句话可操作结论",
 "missing": ["取不到的字段(原样沿用注入数据的 missing)"]
}"""

_JSON_RULES = (
    "\n\n【本轮任务 · 个股诊断证据链卡（严格 JSON 输出）】\n"
    "你要对下方注入了真实数据的这只个股，输出一张结构化『证据链决策卡』。"
    "请按『情绪周期定相位→维科夫量价判断主力进攻/出货→龙头梯队位置→给操作与狙击/失效位』"
    "的方法论组织判断，但所有结论必须落在注入的真实数据上。\n"
    "【输出格式铁律 · 必须遵守】\n"
    "1) 只输出一个 JSON 对象，不要任何额外文字、不要解释、不要 markdown 代码块(```), 不要前后缀。\n"
    "2) JSON 必须严格符合下面的 schema（字段名、取值域照抄）：\n"
    f"{_SCHEMA_HINT}\n"
    "3) action 必须是这八个之一：买入/加仓/持有/减仓/清仓/回避/观望/止损；conviction 必须是 高/中/低。\n"
    "4) evidence 每条的 finding 必须引用注入数据里的【具体数字或事实】（如换手率、量比、主力净流入额、"
    "现价相对均线/前高位置、所属板块今日涨幅，以及基本面财务：营收/净利同比、ROE、毛利率、资产负债率），"
    "direction 为 利多/利空/中性，source 写明出处。至少给 2 条、至多 6 条证据；"
    "证据链尽量覆盖『基本面 + 资金 + 技术/位置』多个维度，没有可引用的真实数据就少给，绝不编造。\n"
    "5) entry_zone(狙击位)/stop(失效位)：只能基于注入的真实价位（现价、日K前高前低、均线、52周高低）推导，"
    "给得出就给区间/条件文字，给不出就置 null —— 绝不臆造一个价格给交易员。\n"
    "6) 数据真实性铁律：数字只能来自注入数据，绝不生成/估算。注入数据的 missing 项原样放进你的 missing 数组，"
    "缺失字段相关的判断要在 conviction 上体现（数据越缺，conviction 越低）。\n"
    "7) summary 一句话给可操作结论（与 action 一致、含触发或风控提示）。"
)


def _coerce_str_or_none(v):
    """entry_zone/stop：允许字符串或 null；空串/字面 'null'/'无'/'None' 归一为 None。
    数字也接受（转成字符串），但绝不把缺失渲染成 0。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).strip()
    if not s or s.lower() in ("null", "none", "nan") or s in ("无", "—", "-", "暂无", "不适用", "N/A", "n/a"):
        return None
    return s


def _normalize(card: dict, code: str, name: str, base_missing: list) -> dict:
    """把 AI 返回的 dict 按 schema 校验补全：
    - action/conviction 不在白名单 → 回退安全值（观望/低）并记一条 missing 说明；
    - evidence 过滤成 [{finding,direction,source}]，direction 越界归 中性；
    - code/name 以服务端真实值为准（不信任 AI 回填，防张冠李戴）；
    - missing 合并『注入数据真实缺失项 + AI 自报缺失』去重。
    """
    out: dict = {}
    out["code"] = code  # always trust server-side code, not the model
    # 名称优先用 stock_detail 的真实名称；AI 给了不同名也不采信（防错配个股）
    out["name"] = name or _coerce_str_or_none(card.get("name")) or ""

    action = card.get("action")
    if action not in _ACTIONS:
        action = "观望"
    out["action"] = action

    conv = card.get("conviction")
    if conv not in _CONVICTIONS:
        conv = "低"
    out["conviction"] = conv

    out["entry_zone"] = _coerce_str_or_none(card.get("entry_zone"))
    out["stop"] = _coerce_str_or_none(card.get("stop"))

    ev_out: list[dict] = []
    raw_ev = card.get("evidence")
    if isinstance(raw_ev, list):
        for item in raw_ev:
            if not isinstance(item, dict):
                continue
            finding = _coerce_str_or_none(item.get("finding"))
            if not finding:
                continue
            direction = item.get("direction")
            if direction not in _DIRECTIONS:
                direction = "中性"
            source = _coerce_str_or_none(item.get("source")) or ""
            ev_out.append({"finding": finding, "direction": direction, "source": source})
    out["evidence"] = ev_out

    out["summary"] = _coerce_str_or_none(card.get("summary")) or ""

    # missing：真实数据缺失项（来自 stock_detail）为权威，叠加 AI 自报的缺失，去重保序。
    missing: list[str] = []
    seen: set = set()
    for src in (base_missing, card.get("missing")):
        if isinstance(src, list):
            for m in src:
                ms = _coerce_str_or_none(m)
                if ms and ms not in seen:
                    seen.add(ms)
                    missing.append(ms)
    out["missing"] = missing
    return out


def _fallback(code: str, name: str, summary: str, base_missing: list, reason: str) -> dict:
    """解析失败/模型不可用时的安全回退卡：action=观望、conviction=低、无狙击/失效位、
    summary 取已有正文前 200 字（无则给降级说明），evidence 留空，missing 透传真实缺失 + 失败原因。"""
    missing: list[str] = []
    seen: set = set()
    for m in list(base_missing or []) + [reason]:
        ms = _coerce_str_or_none(m)
        if ms and ms not in seen:
            seen.add(ms)
            missing.append(ms)
    return {
        "code": code,
        "name": name or "",
        "action": "观望",
        "conviction": "低",
        "entry_zone": None,
        "stop": None,
        "evidence": [],
        "summary": (summary or "").strip()[:200] or "结构化诊断未能生成，请稍后重试或改用盘中问股查看详细分析。",
        "missing": missing,
    }


async def _model_connection_state(engine) -> str:
    """诊断前的最小模型连接门禁。

    返回 ``not_configured`` / ``available`` / ``unavailable`` / ``unchecked``。
    ``model_connection_custom`` 是设置中的唯一真实配置契约；不再把
    config.yaml 里的可选 Hermes 默认地址当成已配置模型。

    ``unchecked`` 仅为旧的内部调用/测试替身保留：它们没有新契约时
    继续走原 AI 路径，避免把“无法判断”误判成“未配置”。
    """
    config = getattr(engine, "config", None)
    configured = getattr(config, "model_connection_custom", None)
    if configured is None:
        return "unchecked"
    if not bool(configured):
        return "not_configured"

    check_connection = getattr(getattr(engine, "hermes", None), "check_connection", None)
    if not callable(check_connection):
        return "unchecked"
    try:
        connected = await asyncio.wait_for(
            check_connection(), timeout=_MODEL_CONNECTION_PREFLIGHT_TIMEOUT
        )
    except (TimeoutError, asyncio.TimeoutError):
        return "unavailable"
    except Exception:  # noqa: BLE001 - 预检异常不向客户暴露端点细节
        log.exception("个股诊断模型连接预检失败")
        return "unavailable"
    return "available" if connected else "unavailable"


def _model_connection_fallback(
    code: str,
    name: str,
    base_missing: list,
    status: str,
) -> dict:
    """模型未配置/不可用时的可机器消费安全卡。"""
    configured = status != "not_configured"
    reason = (
        "模型连接未配置，已使用确定性/行情安全降级"
        if status == "not_configured"
        else "当前模型连接不可用，已使用确定性/行情安全降级"
    )
    card = _fallback(code, name, reason, base_missing, reason)
    card["analysis_mode"] = "deterministic_market_fallback"
    card["model_connection"] = {
        "configured": configured,
        "status": status,
        "used_for_diagnosis": False,
        "fallback": "deterministic_market",
    }
    card["degraded_reason"] = reason
    return card


def _unavailable_market_evidence(reason: str) -> tuple[list[dict], dict]:
    return [], {
        "status": "unavailable",
        "captured": 0,
        "requested": len(_MARKET_EVIDENCE_FIELDS),
        "missing_fields": list(_MARKET_EVIDENCE_FIELDS),
        "reason": reason,
    }


async def _market_evidence_with_budget(
    engine,
    code: str,
    *,
    timeout: float,
) -> tuple[list[dict], dict]:
    """带总预算的行情证据采集。

    底层适配器是同步网络实现，``to_thread`` 超时后线程可能仍在
    收尾，但 HTTP 请求不再等待它；线程侧信号量与适配器熔断器共同
    防止慢源放大。
    """
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                _guarded_blocking_call, _capture_market_evidence, engine, code
            ),
            timeout=timeout,
        )
        if result is _DATA_SLOT_UNAVAILABLE:
            return _unavailable_market_evidence("diagnosis_data_capacity_busy")
        return result
    except (TimeoutError, asyncio.TimeoutError):
        log.warning("个股诊断快行情采集超时 code=%s", code)
        return _unavailable_market_evidence("market_evidence_timeout")
    except Exception:  # noqa: BLE001 - 行情降级不向客户暴露供应商异常
        log.exception("个股诊断快行情采集失败 code=%s", code)
        return _unavailable_market_evidence("market_evidence_failed")


async def _blocking_call_with_budget(func, *args, timeout: float):
    """将同步慢源限制在单次交互预算内；失败/超时统一返回 None。"""
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_guarded_blocking_call, func, *args), timeout=timeout
        )
        return None if result is _DATA_SLOT_UNAVAILABLE else result
    except (TimeoutError, asyncio.TimeoutError):
        return None
    except Exception:  # noqa: BLE001 - 详细异常由各数据适配器自身记录
        return None


def _guarded_blocking_call(func, *args):
    # 保持并发上限不变，但允许健康的前一请求短暂收尾。旧的非阻塞
    # 抢占会让连续换股时第二只直接变成 0/6；慢源超过 1 秒仍 fail-fast，
    # 不会靠增加 worker 或无限排队掩盖问题。
    if not _DIAGNOSIS_DATA_SLOTS.acquire(timeout=_DIAGNOSIS_DATA_SLOT_WAIT):
        return _DATA_SLOT_UNAVAILABLE
    try:
        return func(*args)
    finally:
        _DIAGNOSIS_DATA_SLOTS.release()


async def _fast_fallback_context(engine, code: str) -> tuple[dict, list[dict], dict]:
    """降级卡只采统一网关的快行情证据，不进入分时/日K/资金/财务慢链。

    不另外并发 ``stock.quick_quote``：网关已包含 TDX/腾讯快照且会复用
    单票缓存，再走一次直连只会在供应商异常时多留一个同步收尾线程。
    """
    quote, evidence = await asyncio.gather(
        _blocking_call_with_budget(
            stock.quick_quote, code, timeout=min(3.0, _FAST_MARKET_DATA_TIMEOUT)
        ),
        _market_evidence_with_budget(
            engine, code, timeout=_FAST_MARKET_DATA_TIMEOUT
        ),
    )
    rows, quality = evidence
    return quote if isinstance(quote, dict) else {}, rows, quality


def _market_data_fallback(
    code: str,
    name: str,
    base_missing: list,
    model_connection_status: str,
) -> dict:
    reason = "完整个股数据采集超时，已使用快行情安全降级"
    card = _fallback(code, name, reason, base_missing, reason)
    card["analysis_mode"] = "market_data_fallback"
    card["model_connection"] = {
        "configured": model_connection_status != "not_configured",
        "status": model_connection_status,
        "used_for_diagnosis": False,
        "fallback": "fast_market",
    }
    card["degraded_reason"] = reason
    return card


def _capture_market_evidence(engine, code: str) -> tuple[list[dict], dict]:
    """通过进程级统一网关采集诊断页的字段级证据。

    不可用字段不伪造为 0，只记入 missing；网关未装配或完全故障时返回
    明示降级摘要，不影响原有诊断的安全回退。
    """
    gateway = getattr(engine, "market_gateway", None)
    if gateway is None:
        return [], {
            "status": "unavailable", "captured": 0,
            "requested": len(_MARKET_EVIDENCE_FIELDS),
            "missing_fields": list(_MARKET_EVIDENCE_FIELDS),
        }
    try:
        # 这里只需要 freshness 分段，不能调 build_market_context：后者还会
        # 解析前后交易日，首次可触发历史日历源，超出诊断页快行情预算。
        # 工作日近似仅作数据龄标签，不改写价格/证据质量与最终动作。
        from ..product_state import market_session  # noqa: PLC0415

        current = now_cn()
        session = market_session(current, trading_day=current.weekday() < 5)
    except Exception:  # 时钟状态只影响 freshness 标签，不影响取值。
        session = "intraday"

    rows: list[dict] = []
    missing: list[str] = []
    qualities: set[str] = set()
    for field in _MARKET_EVIDENCE_FIELDS:
        try:
            point = gateway.get(code, field, session=session)
        except Exception:  # 单字段故障不能拖垮整张诊断卡。
            log.exception("诊断行情证据采集失败 code=%s field=%s", code, field)
            missing.append(field)
            continue
        if point.quality_status == "missing" or point.value is None:
            missing.append(field)
            continue
        rows.append(point.to_dict())
        qualities.add(point.quality_status)

    if "conflict" in qualities:
        status = "conflict"
    elif "degraded" in qualities or missing:
        status = "degraded"
    elif rows:
        status = "ok"
    else:
        status = "unavailable"
    return rows, {
        "status": status,
        "captured": len(rows),
        "requested": len(_MARKET_EVIDENCE_FIELDS),
        "missing_fields": missing,
    }


def _attach_market_evidence(card: dict, rows: list[dict], summary: dict) -> dict:
    out = dict(card)
    out["market_evidence"] = rows
    out["market_evidence_quality"] = summary
    # AI 慢结论的生成快照与当前快行情分层：命中 90s 缓存时
    # market_evidence 会刷新，但 decision_* 始终保留结论当时证据 ID。
    if "decision_market_evidence" not in out:
        out["decision_market_evidence"] = rows
        out["decision_market_evidence_quality"] = summary
        out["decision_generated_at"] = now_cn().isoformat(timespec="seconds")
    return out


def _apply_evidence_gate(card: dict, summary: dict) -> dict:
    """关键行情证据缺失/冲突时确定性降级，不让 AI 结论越过证据门。"""
    out = dict(card)
    status = str((summary or {}).get("status") or "unavailable")
    missing_fields = set((summary or {}).get("missing_fields") or [])
    critical_missing = bool({"last_price", "prev_close", "change_pct"} & missing_fields)
    hard_block = status in {"conflict", "unavailable"} or critical_missing
    missing = list(out.get("missing") or [])
    if hard_block:
        original_action = out.get("original_action") or out.get("action")
        action_blocked = out.get("action") in {"买入", "加仓"}
        if action_blocked:
            out["action"] = "观望"
        out["conviction"] = "低"
        out["entry_zone"] = None
        out["stop"] = None
        reason = "关键行情证据缺失或跨源冲突，已禁止新开/加仓结论"
        out["original_action"] = original_action
        out["gate_applied"] = True
        out["gate_reasons"] = list(dict.fromkeys([
            *(out.get("gate_reasons") or []), reason,
        ]))
        out["gate_reason"] = reason
        if action_blocked or re.search(r"买入|加仓|建仓|抄底|追涨", str(out.get("summary") or "")):
            out["summary"] = "关键行情证据不足或冲突，暂停新增风险敞口，等待数据恢复后重新诊断。"
        if reason not in missing:
            missing.append(reason)
    elif status == "degraded" and out.get("conviction") == "高":
        out["conviction"] = "中"
        reason = "行情证据部分降级，置信度已封顶为中"
        if reason not in missing:
            missing.append(reason)
    out["missing"] = missing
    return out


_FACT_META = {
    "last_price": ("现价", ("现价", "最新价", "股价"), "CNY"),
    "price": ("现价", ("现价", "最新价", "股价"), "CNY"),
    "prev_close": ("昨收", ("昨收", "前收"), "CNY"),
    "change_pct": ("涨跌幅", ("涨跌幅", "涨幅", "跌幅"), "percent"),
    "open": ("今开", ("今开", "开盘"), "CNY"),
    "high": ("最高价", ("最高", "日高"), "CNY"),
    "low": ("最低价", ("最低", "日低"), "CNY"),
    "amount": ("成交额", ("成交额",), "CNY-amount"),
    "volume": ("成交量", ("成交量",), "share"),
    "turnover_rate": ("换手率", ("换手",), "percent"),
    "volume_ratio": ("量比", ("量比",), "ratio"),
    "pe": ("市盈率", ("市盈", "PE", "pe"), "ratio"),
    "pb": ("市净率", ("市净", "PB", "pb"), "ratio"),
    "total_mv": ("总市值", ("总市值",), "CNY-amount"),
    "float_mv": ("流通市值", ("流通市值",), "CNY-amount"),
    "high_52w": ("52周最高", ("52周最高", "52周高"), "CNY"),
    "low_52w": ("52周最低", ("52周最低", "52周低"), "CNY"),
    "main_net": ("主力净流入", ("主力净流入", "主力资金"), "CNY-amount"),
    "super_net": ("超大单净流入", ("超大单",), "CNY-amount"),
    "large_net": ("大单净流入", ("大单",), "CNY-amount"),
    "medium_net": ("中单净流入", ("中单",), "CNY-amount"),
    "small_net": ("小单净流入", ("小单",), "CNY-amount"),
}


def _validate_model_evidence(
    card: dict,
    detail: dict,
    fina: object,
    market_evidence: list[dict],
) -> dict:
    """把模型自由文本证据收紧为服务端可复算的事实引用。

    模型只能选择事实和给出方向；数字由后端与注入数据或网关证据精确
    匹配后重新格式化。无法匹配的“主力净流入999亿”之类文本直接丢弃，
    不再把提示词约束当成事实校验。
    """
    out = dict(card)
    facts: list[dict] = []
    seen_refs: set[str] = set()

    def add_fact(
        *, key: str, value: object, ref: str, source: str,
        label: str | None = None, aliases: tuple[str, ...] | None = None,
        unit: str | None = None,
    ) -> None:
        if ref in seen_refs or value is None or isinstance(value, bool):
            return
        meta = _FACT_META.get(key)
        fact_label = label or (meta[0] if meta else key)
        fact_aliases = tuple(alias for alias in (aliases or (meta[1] if meta else (fact_label, key))) if alias)
        fact_unit = unit or (meta[2] if meta else "number")
        if isinstance(value, (int, float)):
            number = float(value)
            if not math.isfinite(number):
                return
            fact_value: object = number
        else:
            text = str(value).strip()
            if not text or len(text) > 120:
                return
            fact_value = text
        seen_refs.add(ref)
        facts.append({
            "key": key, "label": fact_label, "aliases": fact_aliases,
            "value": fact_value, "unit": fact_unit, "ref": ref, "source": source,
        })

    for row in market_evidence or []:
        if not isinstance(row, dict) or row.get("value") is None:
            continue
        field = str(row.get("field") or "")
        evidence_id = str(row.get("evidence_id") or "").strip()
        add_fact(
            key=field,
            value=row.get("value"),
            ref=f"evidence:{evidence_id}" if evidence_id else f"input:market_evidence.{field}",
            source=str(row.get("source_id") or "字段证据网关"),
            unit=str(row.get("unit") or "") or None,
        )

    quote = (detail or {}).get("quote") or {}
    for key, value in quote.items():
        if key in _FACT_META:
            add_fact(key=key, value=value, ref=f"input:quote.{key}", source="stock_detail.quote")
    flow = (detail or {}).get("flow") or {}
    for key, value in flow.items():
        if key in _FACT_META:
            add_fact(key=key, value=value, ref=f"input:flow.{key}", source="stock_detail.flow")
    industry = ((detail or {}).get("board") or {}).get("industry")
    add_fact(
        key="industry", value=industry, ref="input:board.industry", source="stock_detail.board",
        label="所属行业", aliases=("行业", "板块"), unit="text",
    )

    bars = [bar for bar in ((detail or {}).get("daily") or [])[-60:] if isinstance(bar, dict)]
    closes: list[float] = []
    for bar in bars:
        try:
            close = float(bar.get("c", bar.get("close")))
        except (TypeError, ValueError):
            continue
        if math.isfinite(close) and close > 0:
            closes.append(close)
    for window in (5, 10, 20, 60):
        if len(closes) >= window:
            add_fact(
                key=f"ma{window}", value=sum(closes[-window:]) / window,
                ref=f"derived:daily.ma{window}", source="前复权日K确定性计算",
                label=f"MA{window}", aliases=(f"MA{window}", f"{window}日线", f"{window}日均线"),
                unit="CNY",
            )

    def add_fina(value: object, path: str = "fina", depth: int = 0) -> None:
        if depth > 3:
            return
        if isinstance(value, dict):
            for key, child in list(value.items())[:80]:
                add_fina(child, f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value[:10]):
                add_fina(child, f"{path}.{index}", depth + 1)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            key = path.rsplit(".", 1)[-1]
            add_fact(
                key=key, value=value, ref=f"input:{path}", source="fina_indicator",
                label=key, aliases=(key,), unit="number",
            )

    if isinstance(fina, (dict, list)):
        add_fina(fina)

    def number_tokens(text: str) -> list[float]:
        clean = re.sub(r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b", "", text)
        clean = re.sub(r"(?i)MA\s*\d+", "", clean)
        clean = re.sub(r"\d+\s*(?:日均线|日线|均线|周线)", "", clean)
        return [float(token) for token in re.findall(r"-?\d+(?:\.\d+)?", clean)]

    def candidate_value(fact: dict, text: str) -> float | None:
        value = fact["value"]
        if not isinstance(value, (int, float)):
            return None
        if "亿" in text and fact["unit"] in {"CNY", "CNY-amount"}:
            return float(value) / 100_000_000
        if "万" in text and fact["unit"] in {"CNY", "CNY-amount", "share"}:
            return float(value) / 10_000
        return float(value)

    def canonical(fact: dict) -> str:
        value = fact["value"]
        if not isinstance(value, (int, float)):
            return f"{fact['label']}：{value}"
        unit = fact["unit"]
        if unit == "CNY-amount":
            if abs(value) >= 100_000_000:
                return f"{fact['label']} {value / 100_000_000:.2f} 亿元"
            if abs(value) >= 10_000:
                return f"{fact['label']} {value / 10_000:.2f} 万元"
            return f"{fact['label']} {value:.2f} 元"
        if unit == "CNY":
            return f"{fact['label']} {value:.2f} 元"
        if unit == "percent":
            return f"{fact['label']} {value:.2f}%"
        if unit == "share":
            return f"{fact['label']} {value:,.0f} 股"
        if unit == "ratio":
            return f"{fact['label']} {value:.2f}"
        return f"{fact['label']} {value:g}"

    verified: list[dict] = []
    rejected = 0
    for item in out.get("evidence") or []:
        finding = str(item.get("finding") or "").strip()
        if not finding:
            rejected += 1
            continue
        lower = finding.lower()
        candidates = [
            fact for fact in facts
            if any(alias.lower() in lower for alias in fact["aliases"])
        ]
        tokens = number_tokens(finding)
        matched: list[dict] = []
        if tokens:
            for token in tokens:
                choices = []
                for fact in candidates:
                    expected = candidate_value(fact, finding)
                    if expected is None:
                        continue
                    tolerance = max(0.02, abs(expected) * 0.01)
                    if abs(token - expected) <= tolerance:
                        choices.append((abs(token - expected), fact))
                if not choices:
                    matched = []
                    break
                matched.append(min(choices, key=lambda choice: choice[0])[1])
        else:
            # 无数字事实只接受明确的字符串值（如“所属银行板块”）
            # 或已由服务端计算存在的 MA 别名。
            matched = [
                fact for fact in candidates
                if (
                    isinstance(fact["value"], str) and str(fact["value"]).lower() in lower
                ) or fact["ref"].startswith("derived:daily.ma")
            ][:2]
        unique = []
        refs = set()
        for fact in matched:
            if fact["ref"] not in refs:
                refs.add(fact["ref"])
                unique.append(fact)
        if not unique:
            rejected += 1
            continue
        verified.append({
            "finding": "；".join(canonical(fact) for fact in unique),
            "direction": item.get("direction") if item.get("direction") in _DIRECTIONS else "中性",
            "source": " / ".join(dict.fromkeys(str(fact["source"]) for fact in unique)),
            "evidence_ref": unique[0]["ref"],
            "evidence_refs": [fact["ref"] for fact in unique],
            "derivation_rule": "server_fact_match",
            "rule_version": "diagnosis-evidence-v2",
        })

    out["evidence"] = verified[:6]
    out["evidence_validation"] = {
        "rule_version": "diagnosis-evidence-v2",
        "verified": len(verified),
        "rejected": rejected,
        "status": "passed" if verified and rejected == 0 else ("partial" if verified else "rejected"),
    }
    if rejected:
        reason = f"模型证据有{rejected}条无法绑定到服务端真实字段，已丢弃"
        missing = list(out.get("missing") or [])
        if reason not in missing:
            missing.append(reason)
        out["missing"] = missing
    if not verified and out.get("action") in {"买入", "加仓"}:
        reason = "新增风险结论没有通过确定性证据绑定，已禁止"
        out["original_action"] = out.get("original_action") or out.get("action")
        out["action"] = "观望"
        out["conviction"] = "低"
        out["entry_zone"] = None
        out["stop"] = None
        out["summary"] = "模型结论缺少可复算证据，暂停新增风险敞口。"
        out["gate_applied"] = True
        out["gate_reason"] = reason
        out["gate_reasons"] = list(dict.fromkeys([*(out.get("gate_reasons") or []), reason]))
    return out


def _validate_price_levels(
    card: dict,
    quote: dict,
    daily: list[dict],
    market_evidence: list[dict] | None = None,
) -> dict:
    """只接受能映射到确定性真实基准的狙击/失效价。

    仅做“历史价格最小值到最大值”的宽区间校验会让任意幻觉数字过关。
    这里改为白名单基准：实时 OHLC/昨收/52 周高低、最新日 K OHLC、
    MA5/10/20/60、各窗口高低点，以及现价常用 3/5/8/10/15/20% 风险偏移。
    文本舍入只允许 1.5% 内的误差；“5日线”等周期数字不会被误当成股价。
    """
    out = dict(card)
    bases: list[dict] = []
    evidence_points = {
        str(row.get("field")): row
        for row in (market_evidence or [])
        if isinstance(row, dict) and row.get("field") and row.get("value") is not None
    }

    def add_basis(
        label: str,
        value: object,
        *,
        support_ref: str,
        evidence_field: str | None = None,
    ) -> None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        if not (0 < number < 1_000_000):
            return
        item = {"label": label, "value": number, "support_ref": support_ref}
        point = evidence_points.get(evidence_field or "")
        if point is not None:
            try:
                point_value = float(point.get("value"))
            except (TypeError, ValueError):
                point_value = None
            # 绝不能把 legacy stock_detail.quote=12.24 直接挂到网关
            # evidence=10.20 上。只有两者值真实一致时才绑该 ID。
            if (
                point_value is not None
                and abs(number - point_value) <= max(0.001, abs(point_value) * 0.001)
                and point.get("evidence_id")
            ):
                item["evidence_id"] = str(point["evidence_id"])
                item["support_ref"] = f"evidence:{point['evidence_id']}"
        bases.append(item)

    quote_fields = (
        ("price", "现价", "last_price"),
        ("prev_close", "昨收", "prev_close"),
        ("open", "今开", "open"),
        ("high", "当日最高", "high"),
        ("low", "当日最低", "low"),
        ("high_52w", "52周最高", None),
        ("low_52w", "52周最低", None),
    )
    for key, label, evidence_field in quote_fields:
        point = evidence_points.get(evidence_field or "")
        if point is not None:
            # 统一网关是价/昨收等字段的权威选中值；一旦存在，
            # 不再把可能陈旧的 legacy quote 值加入候选集。
            add_basis(
                label,
                point.get("value"),
                evidence_field=evidence_field,
                support_ref=f"evidence:{point.get('evidence_id') or evidence_field}",
            )
        else:
            add_basis(
                label,
                quote.get(key),
                evidence_field=evidence_field,
                support_ref=f"input:quote.{key}",
            )

    bars: list[dict] = [bar for bar in (daily or [])[-250:] if isinstance(bar, dict)]

    def bar_number(bar: dict, *keys: str) -> float | None:
        for key in keys:
            try:
                value = float(bar.get(key))
            except (TypeError, ValueError):
                continue
            if 0 < value < 1_000_000:
                return value
        return None

    if bars:
        latest = bars[-1]
        for keys, label in (
            (("o", "open"), "最新日K开盘"),
            (("c", "close"), "最新日K收盘"),
            (("h", "high"), "最新日K最高"),
            (("l", "low"), "最新日K最低"),
        ):
            add_basis(
                label,
                bar_number(latest, *keys),
                support_ref=f"input:daily.latest.{keys[0]}",
            )

    closes = [value for bar in bars if (value := bar_number(bar, "c", "close")) is not None]
    for window in (5, 10, 20, 60):
        if len(closes) >= window:
            add_basis(
                f"MA{window}",
                sum(closes[-window:]) / window,
                support_ref=f"derived:daily.ma{window}",
            )
        recent = bars[-window:]
        if len(recent) >= window:
            highs = [value for bar in recent if (value := bar_number(bar, "h", "high")) is not None]
            lows = [value for bar in recent if (value := bar_number(bar, "l", "low")) is not None]
            if highs:
                add_basis(
                    f"近{window}日最高", max(highs),
                    support_ref=f"derived:daily.high{window}",
                )
            if lows:
                add_basis(
                    f"近{window}日最低", min(lows),
                    support_ref=f"derived:daily.low{window}",
                )

    current_basis = next((item for item in bases if item["label"] == "现价"), None)
    current = current_basis["value"] if current_basis is not None else None
    if current_basis is not None:
        for pct in (3, 5, 8, 10, 15, 20):
            item = {
                "label": f"现价下移{pct}%",
                "value": current * (1 - pct / 100),
                "support_ref": current_basis["support_ref"],
            }
            if current_basis.get("evidence_id"):
                item["evidence_id"] = current_basis["evidence_id"]
            bases.append(item)

    def extract_prices(text: object) -> tuple[list[float], set[int]]:
        clean = str(text or "")
        named_windows = {
            int(value)
            for value in re.findall(r"(?i)(?:MA\s*|)(\d+)\s*(?:日均线|日线|均线|周线)", clean)
        }
        clean = re.sub(r"(?i)MA\s*\d+", "", clean)
        clean = re.sub(r"\d+\s*(?:日均线|日线|均线|周线)", "", clean)
        clean = re.sub(r"\d+(?:\.\d+)?\s*%", "", clean)
        clean = re.sub(r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b", "", clean)
        return [float(value) for value in re.findall(r"\d+(?:\.\d+)?", clean)], named_windows

    rejected: list[str] = []
    supported: dict[str, list[dict]] = {}
    for field in ("entry_zone", "stop"):
        raw = out.get(field)
        if raw is None:
            continue
        values, named_windows = extract_prices(raw)
        matches: list[dict] = []
        for window in named_windows:
            named = next((item for item in bases if item["label"] == f"MA{window}"), None)
            if named is None:
                rejected.append(field)
                break
            matches.append({
                "text_value": f"{window}日线",
                "basis": named["label"],
                "basis_value": round(named["value"], 4),
                "evidence_id": named.get("evidence_id"),
                "support_ref": named["support_ref"],
            })
        if field in rejected:
            out[field] = None
            continue
        if not values and not named_windows:
            out[field] = None
            rejected.append(field)
            continue
        for value in values:
            candidates = [
                item for item in bases
                if abs(value - item["value"]) <= max(0.02, item["value"] * 0.015)
            ]
            if not candidates:
                out[field] = None
                rejected.append(field)
                break
            best = min(candidates, key=lambda item: abs(value - item["value"]))
            matches.append({
                "text_value": value,
                "basis": best["label"],
                "basis_value": round(best["value"], 4),
                "deviation_pct": round(abs(value - best["value"]) / best["value"] * 100, 4),
                "evidence_id": best.get("evidence_id"),
                "support_ref": best["support_ref"],
            })
        # 做多语义下的失效位必须严格低于权威现价；现价、日高
        # 或任何上方价都不是“止损”。入场位也不得借风控偏移给出
        # 远高于现价的追高点，最多允许 5% 的明确突破触发。
        long_context = (out.get("original_action") or out.get("action")) in {"买入", "加仓", "持有"}
        matched_values = [float(match["basis_value"]) for match in matches]
        if (
            field == "stop"
            and long_context
            and current is not None
            and any(value >= current * 0.999 for value in matched_values)
        ):
            out[field] = None
            rejected.append(field)
        elif (
            field == "entry_zone"
            and long_context
            and current is not None
            and any(value > current * 1.05 for value in matched_values)
        ):
            out[field] = None
            rejected.append(field)
        if field not in rejected:
            supported[field] = matches

    rejected = list(dict.fromkeys(rejected))
    out["price_level_validation"] = {
        "rule_version": "diagnosis-price-level.v2",
        "status": "rejected" if rejected else "passed",
        "rejected_fields": rejected,
        "supported_fields": supported,
        "evidence_refs": sorted({
            str(match["evidence_id"])
            for matches in supported.values()
            for match in matches
            if match.get("evidence_id")
        }),
        "support_refs": sorted({
            str(match["support_ref"])
            for matches in supported.values()
            for match in matches
            if match.get("support_ref")
        }),
    }
    if rejected:
        reason = "模型价位无法映射到真实行情或确定性风险基准，已置空"
        original_action = out.get("original_action") or out.get("action")
        if out.get("action") in {"买入", "加仓"}:
            out["action"] = "观望"
            out["summary"] = "模型给出的交易价位无法由真实行情验证，已取消新开/加仓结论。"
        out["original_action"] = original_action
        out["conviction"] = "低"
        out["gate_applied"] = True
        out["gate_reason"] = reason
        out["gate_reasons"] = list(dict.fromkeys([
            *(out.get("gate_reasons") or []), reason,
        ]))
        missing = list(out.get("missing") or [])
        if reason not in missing:
            missing.append(reason)
        out["missing"] = missing
    return out


# ---- 止损波动底线（白皮书吸收② 2026-07-05：确定性规则，防失效位贴价被日常噪音打掉）----
_STOP_MIN_PCT = 3.0   # A股日内常态波动约±2%，止损距离 <3% 极易被无意义震出（白皮书 §10 同款下限）
_ATR_MULT = 1.5       # ATR 倍数底线：高波动票的噪音容忍度按真实波幅放大


def _atr14(daily: list) -> float | None:
    """ATR14（TR 简单均值，元）。数字全部来自注入的真实日K；数据不足 15 根/字段缺 → None。
    键名兼容：stock_detail 的日K为短键 h/l/c（省 token 的列式口径），K线源亦有 high/low/close 长键。"""
    def _f(b: dict, *keys):
        for k in keys:
            v = b.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        return None

    try:
        bars = []
        for b in (daily or []):
            h, lo, c = _f(b, "high", "h"), _f(b, "low", "l"), _f(b, "close", "c")
            if h is not None and lo is not None and c is not None:
                bars.append((h, lo, c))
        if len(bars) < 15:
            return None
        bars = bars[-15:]
        trs = []
        for (ph, pl, pc), (h, lo, _c) in zip(bars[:-1], bars[1:]):
            trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        return (sum(trs) / len(trs)) if trs else None
    except (TypeError, ValueError, KeyError):
        return None


def _apply_stop_floor(card: dict, quote: dict, daily: list) -> None:
    """止损距离底线校验：失效位距现价 < max(3%, 1.5×ATR14) 时，**不改写 AI 的结构位文本**，
    只追加一条中性证据提醒"放宽或降仓"，并挂 stop_floor 附加字段（前端未知键自动忽略）。
    price/stop 任一缺失、或 stop 不在现价下方（非做多语境）→ 静默跳过，行为与旧版一致。
    校验层自身任何异常都吞掉——底线提醒绝不能影响出卡。"""
    try:
        price = quote.get("price")
        stop_text = str(card.get("stop") or "")
        stop_text = re.sub(r"(?i)MA\s*\d+", "", stop_text)
        stop_text = re.sub(r"\d+\s*(?:日均线|日线|均线|周线)", "", stop_text)
        nums = re.findall(r"\d+(?:\.\d+)?", stop_text)
        if not isinstance(price, (int, float)) or price <= 0 or not nums:
            return
        # 失效位文本常含多个价位（"跌破1180或前低1177.14"）——取【现价下方最近的一档】：
        # 它是最先被触发的位置，用它算距离对"是否贴价"判断最准（07-06 审计裁定）。
        below = [v for v in (float(x) for x in nums) if 0 < v < price]
        if not below:
            return
        stop_val = max(below)
        dist_pct = (price - stop_val) / price * 100.0
        atr = _atr14(daily)
        floor_pct = max(_STOP_MIN_PCT, round(_ATR_MULT * atr / price * 100.0, 1)) if atr else _STOP_MIN_PCT
        if dist_pct >= floor_pct:
            return
        atr_txt = f"，ATR14≈{atr:.2f}元" if atr else ""
        card.setdefault("evidence", []).append({
            "finding": (f"失效位 {stop_val:g} 距现价仅 {dist_pct:.1f}%，低于波动底线 {floor_pct:.1f}%"
                        f"（日常波动即可能误触{atr_txt}）——建议放宽至 ≤{price * (1 - floor_pct / 100.0):.2f}"
                        f" 或相应降低仓位"),
            "direction": "中性",
            "source": "ATR14波动底线规则",
            "evidence_ref": "rule:atr14-stop-floor",
            "evidence_refs": ["rule:atr14-stop-floor"],
            "derivation_rule": "atr14_stop_floor",
            "rule_version": "diagnosis-risk-rule-v1",
        })
        card["stop_floor"] = {"floor_pct": floor_pct, "atr14": round(atr, 3) if atr else None,
                              "stop_dist_pct": round(dist_pct, 2)}
    except Exception:  # noqa: BLE001 — 附加校验绝不阻断诊断卡产出
        return


def _deep_bar_number(bar: dict, *keys: str) -> float | None:
    for key in keys:
        try:
            value = float(bar.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def _deep_ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1.0 - alpha) * out[-1])
    return out


def _deep_rsi14(closes: list[float]) -> float | None:
    if len(closes) < 15:
        return None
    changes = [right - left for left, right in zip(closes[-15:-1], closes[-14:])]
    gains = sum(max(change, 0.0) for change in changes) / 14.0
    losses = sum(max(-change, 0.0) for change in changes) / 14.0
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def _deep_technical_analysis(
    quote: dict,
    daily: list[dict],
    flow: dict | None,
    board: dict | None,
) -> dict:
    """用前复权日 K + 当前快照生成可复算的多维结构分析。

    这不是把盘口字段再换个说法：趋势至少需要 60 根前复权日 K，动量使用
    RSI14/MACD，风险使用 ATR14，量价使用 5/20 日成交量比，位置使用
    20/60 日高低。任何维度缺失都明确降级，不补零。
    """
    bars: list[dict] = []
    for raw in daily or []:
        if not isinstance(raw, dict):
            continue
        close = _deep_bar_number(raw, "c", "close")
        high = _deep_bar_number(raw, "h", "high")
        low = _deep_bar_number(raw, "l", "low")
        if close is None or high is None or low is None or close <= 0:
            continue
        bars.append({
            "d": str(raw.get("d") or raw.get("date") or ""),
            "c": close,
            "h": high,
            "l": low,
            "v": _deep_bar_number(raw, "v", "volume"),
        })

    price = _deep_bar_number(quote, "price")
    closes = [bar["c"] for bar in bars]
    volumes = [bar["v"] for bar in bars if bar["v"] is not None and bar["v"] >= 0]

    def mean_tail(values: list[float], window: int) -> float | None:
        return sum(values[-window:]) / window if len(values) >= window else None

    ma5 = mean_tail(closes, 5)
    ma10 = mean_tail(closes, 10)
    ma20 = mean_tail(closes, 20)
    ma60 = mean_tail(closes, 60)
    ma20_prev = (
        sum(closes[-25:-5]) / 20.0
        if len(closes) >= 25 else None
    )
    rsi14 = _deep_rsi14(closes)
    ema12 = _deep_ema(closes, 12)
    ema26 = _deep_ema(closes, 26)
    dif = [a - b for a, b in zip(ema12[-len(ema26):], ema26)] if ema26 else []
    dea = _deep_ema(dif, 9)
    macd_hist = (dif[-1] - dea[-1]) * 2.0 if dif and dea else None
    macd_prev = (dif[-2] - dea[-2]) * 2.0 if len(dif) >= 2 and len(dea) >= 2 else None
    atr14 = _atr14(daily)
    ret20 = (closes[-1] / closes[-21] - 1.0) * 100.0 if len(closes) >= 21 else None
    ret60 = (closes[-1] / closes[-61] - 1.0) * 100.0 if len(closes) >= 61 else None
    ret5 = (closes[-1] / closes[-6] - 1.0) * 100.0 if len(closes) >= 6 else None
    ret10 = (closes[-1] / closes[-11] - 1.0) * 100.0 if len(closes) >= 11 else None
    volume5 = mean_tail(volumes, 5)
    volume20 = mean_tail(volumes, 20)
    volume_ratio_5_20 = (
        volume5 / volume20
        if volume5 is not None and volume20 not in (None, 0) else None
    )
    recent20 = bars[-20:] if len(bars) >= 20 else []
    recent60 = bars[-60:] if len(bars) >= 60 else []
    low20 = min((bar["l"] for bar in recent20), default=None)
    high20 = max((bar["h"] for bar in recent20), default=None)
    low60 = min((bar["l"] for bar in recent60), default=None)
    high60 = max((bar["h"] for bar in recent60), default=None)
    position60 = (
        (price - low60) / (high60 - low60)
        if price is not None and low60 is not None and high60 is not None and high60 > low60
        else None
    )
    distance_ma20 = (
        (price / ma20 - 1.0) * 100.0
        if price is not None and ma20 not in (None, 0) else None
    )

    score = 0
    reasons: list[str] = []
    dimensions: list[dict] = []

    trend_points = 0
    if price is not None and ma20 is not None:
        trend_points += 1 if price >= ma20 else -1
        reasons.append(f"现价{'站上' if price >= ma20 else '跌破'}MA20")
    if ma20 is not None and ma60 is not None:
        trend_points += 2 if ma20 >= ma60 else -2
        reasons.append(f"MA20{'高于' if ma20 >= ma60 else '低于'}MA60")
    if ma20 is not None and ma20_prev is not None:
        trend_points += 1 if ma20 >= ma20_prev else -1
        reasons.append(f"MA20{'向上' if ma20 >= ma20_prev else '向下'}")
    score += trend_points
    dimensions.append({
        "id": "trend",
        "label": "趋势结构",
        "status": "complete" if len(bars) >= 60 else ("partial" if len(bars) >= 20 else "missing"),
        "score": trend_points,
        "summary": (
            f"现价/MA20/MA60={price:.2f}/{ma20:.2f}/{ma60:.2f}"
            if price is not None and ma20 is not None and ma60 is not None
            else f"有效前复权日K {len(bars)} 根，趋势维度不足"
        ),
        "source": "腾讯财经·前复权日K",
    })

    momentum_points = 0
    if rsi14 is not None:
        if 48 <= rsi14 <= 68:
            momentum_points += 1
        elif rsi14 >= 75:
            momentum_points -= 1
    if macd_hist is not None:
        momentum_points += 1 if macd_hist > 0 else -1
        if macd_prev is not None and macd_hist > macd_prev:
            momentum_points += 1
        elif macd_prev is not None and macd_hist < macd_prev:
            momentum_points -= 1
    score += momentum_points
    dimensions.append({
        "id": "momentum",
        "label": "动量拐点",
        "status": "complete" if rsi14 is not None and macd_hist is not None else "missing",
        "score": momentum_points,
        "summary": (
            f"RSI14 {rsi14:.1f} · MACD柱 {macd_hist:+.2f}"
            if rsi14 is not None and macd_hist is not None else "RSI/MACD 数据不足"
        ),
        "source": "前复权日K确定性计算",
    })

    volume_points = 0
    last_return = (
        (closes[-1] / closes[-2] - 1.0) * 100.0 if len(closes) >= 2 and closes[-2] else None
    )
    if volume_ratio_5_20 is not None:
        if volume_ratio_5_20 >= 1.1 and (last_return or 0) > 0:
            volume_points += 1
        elif volume_ratio_5_20 >= 1.1 and (last_return or 0) < 0:
            volume_points -= 1
    score += volume_points
    dimensions.append({
        "id": "volume",
        "label": "量价确认",
        "status": "complete" if volume_ratio_5_20 is not None else "missing",
        "score": volume_points,
        "summary": (
            f"近5日/20日均量 {volume_ratio_5_20:.2f} 倍"
            if volume_ratio_5_20 is not None else "成交量序列不足"
        ),
        "source": "前复权日K成交量",
    })

    flow_value = _deep_bar_number(flow or {}, "main_net")
    flow_points = 1 if flow_value is not None and flow_value > 0 else (
        -1 if flow_value is not None and flow_value < 0 else 0
    )
    score += flow_points
    dimensions.append({
        "id": "capital",
        "label": "资金方向",
        "status": "complete" if flow_value is not None else "missing",
        "score": flow_points,
        "summary": (
            f"主力口径净额 {flow_value / 100_000_000:+.2f} 亿元"
            if flow_value is not None else "个股资金源本次未返回"
        ),
        "source": str((flow or {}).get("source") or "新浪资金流估算口径"),
        "data_date": (flow or {}).get("trade_date"),
    })

    industry = str((board or {}).get("industry") or "").strip()
    concepts = [
        str(item).strip() for item in ((board or {}).get("concepts") or [])
        if str(item).strip()
    ]
    dimensions.append({
        "id": "sector",
        "label": "行业概念",
        "status": "complete" if industry or concepts else "missing",
        "score": 0,
        "summary": " · ".join(filter(None, [industry, "、".join(concepts[:3])])) or "行业/概念关系未返回",
        "source": str((board or {}).get("source") or "TDX relation"),
    })

    overextended = bool(
        (distance_ma20 is not None and distance_ma20 > 12.0)
        or (rsi14 is not None and rsi14 >= 75.0)
    )
    complete = bool(price is not None and len(bars) >= 60 and ma20 is not None and ma60 is not None)
    if not complete:
        structure = "证据不足"
    elif score >= 5:
        structure = "结构偏强"
    elif score >= 2:
        structure = "震荡偏强"
    elif score <= -5:
        structure = "结构偏弱"
    elif score <= -2:
        structure = "震荡偏弱"
    else:
        structure = "中性整理"

    support_candidates = [
        (label, value) for label, value in (
            ("MA20", ma20), ("MA60", ma60), ("20日低点", low20), ("60日低点", low60),
        )
        # 支撑必须位于当前价下方（或恰好等于当前价）。此前为了容忍
        # 轻微价格抖动放宽到 +1.5%，会把现价上方的均线同时标成支撑
        # 和阻力，产生不可执行的价位语义。
        if value is not None and price is not None and value <= price
    ]
    resistance_candidates = [
        (label, value) for label, value in (
            ("MA20", ma20), ("MA60", ma60), ("20日高点", high20), ("60日高点", high60),
        )
        if value is not None and price is not None and value >= price
    ]
    support_item = max(support_candidates, key=lambda item: item[1]) if support_candidates else None
    resistance_item = min(resistance_candidates, key=lambda item: item[1]) if resistance_candidates else None

    return {
        "rule_version": "diagnosis-deep-technical-v1",
        "bars": len(bars),
        "score": score,
        "structure": structure,
        "complete": complete,
        "overextended": overextended,
        "reasons": reasons,
        "dimensions": dimensions,
        "metrics": {
            "ma5": round(ma5, 4) if ma5 is not None else None,
            "ma10": round(ma10, 4) if ma10 is not None else None,
            "ma20": round(ma20, 4) if ma20 is not None else None,
            "ma60": round(ma60, 4) if ma60 is not None else None,
            "ma20_prev": round(ma20_prev, 4) if ma20_prev is not None else None,
            "rsi14": round(rsi14, 2) if rsi14 is not None else None,
            "macd_hist": round(macd_hist, 4) if macd_hist is not None else None,
            "macd_hist_prev": round(macd_prev, 4) if macd_prev is not None else None,
            "atr14": round(atr14, 4) if atr14 is not None else None,
            "return5_pct": round(ret5, 2) if ret5 is not None else None,
            "return10_pct": round(ret10, 2) if ret10 is not None else None,
            "return20_pct": round(ret20, 2) if ret20 is not None else None,
            "return60_pct": round(ret60, 2) if ret60 is not None else None,
            "volume_5_20": round(volume_ratio_5_20, 3) if volume_ratio_5_20 is not None else None,
            "position_60": round(position60, 4) if position60 is not None else None,
            "distance_ma20_pct": round(distance_ma20, 2) if distance_ma20 is not None else None,
        },
        "levels": {
            "support": round(support_item[1], 4) if support_item else None,
            "support_basis": support_item[0] if support_item else None,
            "resistance": round(resistance_item[1], 4) if resistance_item else None,
            "resistance_basis": resistance_item[0] if resistance_item else None,
            "low20": round(low20, 4) if low20 is not None else None,
            "high20": round(high20, 4) if high20 is not None else None,
        },
    }


def _deep_evidence(
    technical: dict,
    quote: dict,
    market_evidence: list[dict],
) -> list[dict]:
    market_by_field = {
        str(row.get("field") or ""): row
        for row in market_evidence
        if isinstance(row, dict)
    }
    rows: list[dict] = []

    def add(finding: str, direction: str, source: str, ref: str = "") -> None:
        rows.append({
            "finding": finding,
            "direction": direction,
            "source": source,
            "evidence_ref": ref,
            "evidence_refs": [ref] if ref else [],
            "rule_version": "diagnosis-deep-evidence-v1",
        })

    price_point = market_by_field.get("last_price") or {}
    price = _deep_bar_number(quote, "price")
    if price is not None:
        add(
            f"现价 {price:.2f}；本结论使用该快照叠加 60 日结构，不用单日涨跌替代趋势",
            "中性",
            str(price_point.get("source_id") or quote.get("source") or "实时行情"),
            f"evidence:{price_point.get('evidence_id')}" if price_point.get("evidence_id") else "",
        )
    metrics = technical.get("metrics") or {}
    ma20, ma60 = metrics.get("ma20"), metrics.get("ma60")
    if ma20 is not None and ma60 is not None and price is not None:
        bullish = price >= ma20 and ma20 >= ma60
        bearish = price < ma20 and ma20 < ma60
        add(
            f"趋势：现价 {price:.2f}，MA20 {ma20:.2f}，MA60 {ma60:.2f}",
            "利多" if bullish else ("利空" if bearish else "中性"),
            "腾讯财经·前复权日K",
            "derived:daily.ma20-ma60",
        )
    rsi, macd = metrics.get("rsi14"), metrics.get("macd_hist")
    if rsi is not None and macd is not None:
        add(
            f"动量：RSI14 {rsi:.1f}，MACD柱 {macd:+.2f}",
            "利多" if 50 <= rsi < 75 and macd > 0 else ("利空" if rsi < 45 and macd < 0 else "中性"),
            "前复权日K确定性计算",
            "derived:daily.rsi14-macd",
        )
    volume_ratio = metrics.get("volume_5_20")
    if volume_ratio is not None:
        add(
            f"量价：近5日均量为20日均量的 {volume_ratio:.2f} 倍",
            "中性",
            "前复权日K成交量",
            "derived:daily.volume5-volume20",
        )
    for dimension in technical.get("dimensions") or []:
        if dimension.get("id") != "capital" or dimension.get("status") != "complete":
            continue
        add(
            str(dimension.get("summary") or ""),
            "利多" if dimension.get("score", 0) > 0 else ("利空" if dimension.get("score", 0) < 0 else "中性"),
            str(dimension.get("source") or "资金流"),
            "derived:moneyflow.main_net",
        )
    levels = technical.get("levels") or {}
    if levels.get("support") is not None or levels.get("resistance") is not None:
        add(
            "结构位："
            + (
                f"{levels.get('support_basis')}支撑 {levels['support']:.2f}"
                if levels.get("support") is not None else "支撑不足"
            )
            + "；"
            + (
                f"{levels.get('resistance_basis')}压力 {levels['resistance']:.2f}"
                if levels.get("resistance") is not None else "压力不足"
            ),
            "中性",
            "前复权日K结构位",
            "derived:daily.structure-levels",
        )
    return rows[:8]


def _deep_not_found_card(
    engine,
    code: str,
    identity: dict,
    *,
    started: float,
    data_as_of: str | None = None,
    error: str = "",
) -> dict:
    return {
        "ok": False,
        "code": code,
        "name": str(identity.get("name") or ""),
        "recognized": False,
        "recognition": identity,
        "error_code": "stock_not_found",
        "error": error or "该代码不在当前完整 A 股名录中",
        "action": None,
        "conviction": None,
        "confidence": 0.0,
        "entry_zone": None,
        "stop": None,
        "evidence": [],
        "summary": "未找到该 A 股代码，因此没有生成分析或交易动作。",
        "missing": [],
        "analysis_mode": "identity_rejected",
        "analysis_status": "not_found",
        "analysis_pipeline": {
            "rule_version": "diagnosis-pipeline-v1",
            "completed": 0,
            "total": 0,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "data_as_of": data_as_of,
            "stages": [],
        },
        "technical_analysis": {},
        "deep_signal": {
            "action": None,
            "confidence": 0.0,
            "complete": False,
            "rule_version": "diagnosis-deep-signal-v1",
        },
        "market_quote": {},
        "market_evidence": [],
        "market_evidence_quality": {
            "status": "not_applicable",
            "captured": 0,
            "requested": 0,
            "missing_fields": [],
        },
        "model_connection": {
            "configured": bool(
                getattr(getattr(engine, "config", None), "model_connection_custom", False)
            ),
            "status": "not_requested",
            "used_for_diagnosis": False,
            "fallback": None,
        },
        "model_explanation_status": "not_applicable",
    }


async def _deterministic_deep_context(
    engine,
    code: str,
    data_material_ids: tuple[str, ...] | None = None,
) -> dict:
    started = time.monotonic()
    # 身份门禁不与行情/图表线程池共用容量。否则并发查询时 stock_core
    # 可能因容量忙返回空结果，让指数别名或错误代码绕过身份核验。
    try:
        identity = await asyncio.wait_for(
            asyncio.to_thread(tx.cached_stock_identity, code),
            timeout=1.5,
        )
    except (TimeoutError, asyncio.TimeoutError, Exception):  # noqa: BLE001
        identity = {}
    identity = identity if isinstance(identity, dict) else {}
    if identity.get("universe_complete") and not identity.get("known"):
        return _deep_not_found_card(engine, code, identity, started=started)

    selected = None if data_material_ids is None else set(data_material_ids)
    if selected is None:
        core_result, charts_result, enrichment_result, evidence_result = await asyncio.gather(
            _blocking_call_with_budget(stock.stock_core, code, timeout=4.2),
            _blocking_call_with_budget(stock.stock_charts, code, timeout=4.5),
            _blocking_call_with_budget(stock.stock_enrichment, code, timeout=5.2),
            _market_evidence_with_budget(engine, code, timeout=_FAST_MARKET_DATA_TIMEOUT),
        )
        risk_daily_result = None
    else:
        jobs = {
            "core": _blocking_call_with_budget(stock.stock_core, code, timeout=4.2),
            "risk_daily": _blocking_call_with_budget(stock.stock_risk_daily, code, timeout=4.5),
            "evidence": _market_evidence_with_budget(
                engine, code, timeout=_FAST_MARKET_DATA_TIMEOUT
            ),
        }
        if "technical" in selected:
            jobs["technical"] = _blocking_call_with_budget(
                stock.stock_charts, code, timeout=4.5
            )
        if "sector" in selected:
            jobs["board"] = _blocking_call_with_budget(
                stock.stock_board_material, code, timeout=4.0
            )
        if "fund_flow" in selected:
            jobs["flow"] = _blocking_call_with_budget(
                stock.stock_flow_material, code, timeout=4.5
            )
        if "fundamental" in selected:
            jobs["financial"] = _blocking_call_with_budget(
                stock.stock_financial_material, code, timeout=4.0
            )
        keys = list(jobs)
        values = await asyncio.gather(*(jobs[key] for key in keys))
        material_values = dict(zip(keys, values))
        core_result = material_values.get("core")
        risk_daily_result = material_values.get("risk_daily")
        evidence_result = material_values.get("evidence") or ([], {})
        charts_result = material_values.get("technical") or {}
        enrichment_result = {
            "board": material_values.get("board"),
            "flow": material_values.get("flow"),
            "financial": material_values.get("financial"),
            "missing": [
                label
                for material_id, label, key in (
                    ("technical", "深度技术与量价", "technical"),
                    ("sector", "行业/概念归属", "board"),
                    ("fund_flow", "个股资金流", "flow"),
                    ("fundamental", "基础财务", "financial"),
                )
                if material_id in selected and not material_values.get(key)
            ],
            "pending": [],
        }
    core = core_result if isinstance(core_result, dict) else {}
    charts = charts_result if isinstance(charts_result, dict) else {}
    enrichment = enrichment_result if isinstance(enrichment_result, dict) else {}
    market_evidence, market_quality = evidence_result
    if core.get("recognized") is False or core.get("error_code") == "stock_not_found":
        core_identity = (
            core.get("recognition")
            if isinstance(core.get("recognition"), dict) else identity
        )
        return _deep_not_found_card(
            engine,
            code,
            core_identity,
            started=started,
            data_as_of=core.get("ts"),
            error=str(core.get("error") or ""),
        )
    quote = dict(core.get("quote") or {})
    daily = (
        charts.get("daily") or core.get("recent_daily") or []
    ) if selected is None else (
        risk_daily_result or core.get("recent_daily") or []
    )
    board = enrichment.get("board") or {}
    flow = enrichment.get("flow") if isinstance(enrichment.get("flow"), dict) else None
    financial = enrichment.get("financial") if isinstance(enrichment.get("financial"), dict) else None
    # 自由问答材料选择只影响增强采集与模型注入，不允许用户通过勾选
    # 资金或板块改变服务端冻结动作。核心技术与风控始终基于行情和日K。
    technical = _deep_technical_analysis(
        quote,
        daily,
        flow if selected is None else None,
        board if selected is None else None,
    )

    action = "观望"
    if technical["complete"]:
        if technical["score"] >= 5 and not technical["overextended"]:
            action = "买入"
        elif technical["score"] <= -4:
            action = "回避"
    confidence = (
        min(0.82, 0.52 + min(abs(technical["score"]), 5) * 0.05)
        if technical["complete"] else 0.28
    )
    conviction = "高" if confidence >= 0.72 else ("中" if confidence >= 0.55 else "低")
    missing = list(dict.fromkeys([
        *(core.get("missing") or []),
        *(charts.get("missing") or []),
        *(enrichment.get("missing") or []),
        *([f"行情核心待完成:{'/'.join(core.get('pending') or [])}"] if core.get("pending") else []),
        *([f"图表待完成:{'/'.join(charts.get('pending') or [])}"] if charts.get("pending") else []),
        *([f"增强待完成:{'/'.join(enrichment.get('pending') or [])}"] if enrichment.get("pending") else []),
    ]))
    configured = bool(getattr(getattr(engine, "config", None), "model_connection_custom", False))
    dimensions = list(technical.get("dimensions") or [])
    if selected is None or "fundamental" in selected:
        dimensions.append({
            "id": "fundamental",
            "label": "基础财务",
            "status": "complete" if financial else "missing",
            "score": 0,
            "summary": (
                " · ".join(
                    f"{label} {financial.get(key):.2f}"
                    for key, label in (
                        ("roe", "ROE"), ("grossprofit_margin", "毛利率"),
                        ("debt_to_assets", "资产负债率"), ("eps", "EPS"),
                    )
                    if financial.get(key) is not None
                ) or "基础财务字段已返回"
                if financial else "基础财务本次未返回"
            ),
            "source": str((financial or {}).get("source") or "TDX stock_info"),
            "data_date": (financial or {}).get("data_date"),
        })
    technical["dimensions"] = dimensions
    completed = sum(item.get("status") == "complete" for item in dimensions)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    summary = (
        f"{technical['structure']}（深度规则 {technical['score']:+d} 分）："
        + "、".join(technical.get("reasons")[:3])
        + "。"
        + (
            "模型未连接，本卡只展示可复算的结构判断与条件，不冒充 AI 综合结论。"
            if not configured else
            "本卡先完成可复算结构分析；模型解释可在问股中按需补充，但无权改写证据。"
        )
    )
    card = {
        "code": code,
        "name": str(quote.get("name") or ""),
        "recognized": True if core.get("recognized") is True else None,
        "recognition": core.get("recognition") or {},
        "action": action,
        "conviction": conviction,
        "confidence": round(confidence, 4),
        "entry_zone": None,
        "stop": None,
        "evidence": _deep_evidence(technical, quote, market_evidence),
        "summary": summary,
        "missing": missing,
        "analysis_mode": "deterministic_deep",
        "analysis_status": "complete" if technical["complete"] else "partial",
        "analysis_pipeline": {
            "rule_version": "diagnosis-pipeline-v1",
            "completed": completed,
            "total": len(dimensions),
            "elapsed_ms": elapsed_ms,
            "data_as_of": core.get("ts") or charts.get("ts"),
            "quote_stale": bool(quote.get("stale")),
            "quote_note": quote.get("quote_note"),
            "stages": dimensions,
        },
        "technical_analysis": technical,
        "deep_signal": {
            "action": action,
            "confidence": round(confidence, 4),
            "conviction": conviction,
            "signal_score": technical["score"],
            "complete": technical["complete"],
            "overextended": technical["overextended"],
            "reasons": technical.get("reasons") or [],
            "rule_version": "diagnosis-deep-signal-v1",
        },
        "market_quote": quote,
        "board": board,
        "flow": flow,
        "financial": financial,
        "selected_data_material_ids": list(data_material_ids or []),
        "material_payloads": ({
            "technical": {
                "technical_analysis": technical,
                "minute": charts.get("minute") or [],
                "minute_date": charts.get("minute_date"),
                "daily": list(daily)[-60:],
            } if selected is not None and "technical" in selected else None,
            "fund_flow": flow if selected is not None and "fund_flow" in selected else None,
            "sector": board if selected is not None and "sector" in selected else None,
            "fundamental": financial if selected is not None and "fundamental" in selected else None,
        } if selected is not None else {}),
        "model_connection": {
            "configured": configured,
            "status": "not_requested" if configured else "not_configured",
            "used_for_diagnosis": False,
            "fallback": None,
        },
        "model_explanation_status": "available_on_request" if configured else "not_configured",
    }
    return _attach_market_evidence(card, market_evidence, market_quality)


def _strip_code_fence(text: str) -> str:
    """容错：个别模型仍会用 ```json ... ``` 包裹。剥掉围栏只取里面内容，
    再交给下面的对象切片兜底。非围栏文本原样返回。"""
    s = text.strip()
    if s.startswith("```"):
        # 去掉首行 ``` 或 ```json
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _parse_json(text: str):
    """尽力把模型输出解析成 dict：直接 loads → 剥代码围栏后 loads →
    截取第一个 '{' 到最后一个 '}' 的子串 loads。全失败返回 None。"""
    if not text:
        return None
    candidates = []
    stripped = _strip_code_fence(text)
    candidates.append(stripped)
    # 对象切片兜底：模型可能在 JSON 前后夹了客套话
    lo = stripped.find("{")
    hi = stripped.rfind("}")
    if lo != -1 and hi != -1 and hi > lo:
        candidates.append(stripped[lo:hi + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            continue
    return None


async def diagnose(
    engine,
    code: str,
    *,
    include_model: bool = True,
    analysis_depth: str = "fast",
    data_material_ids: tuple[str, ...] | None = None,
) -> dict:
    """对单只个股产出结构化『证据链诊断卡』(见模块 docstring 的 schema)。

    参数:
        engine: analysis.engine.Engine 实例（用其 .hermes 非流式 chat、.store 取知识库摘要）。
        code:   6 位股票代码（调用方应已做格式校验；此处也会 zfill 兜底）。

    返回: dict —— 始终是合法的诊断卡（即使数据/模型失败也回退安全卡），调用方可直接 JSON 返回前端。
    数据真实性：卡内一切数字源自 stock.stock_detail(code)；AI 仅组织判断方向，绝不造数。
    """
    code = (code or "").strip().zfill(6)

    if not include_model and analysis_depth == "deep":
        return await _deterministic_deep_context(
            engine, code, data_material_ids=data_material_ids
        )

    # Web 诊断首屏只需要可复算的行情证据与确定性规则，不应把模型、F10
    # 或其它增强链路串在 HTTP 事务里。模型解释仍可由问股/研究链路按需
    # 获取；这里显式返回 not_requested，不能冒充“模型未配置/失联”。
    if not include_model:
        quote, market_evidence, market_evidence_quality = await _fast_fallback_context(
            engine, code
        )
        config = getattr(engine, "config", None)
        configured = bool(getattr(config, "model_connection_custom", False))
        name = _coerce_str_or_none(quote.get("name")) or ""
        card = _fallback(
            code,
            name,
            "快速诊断已使用实时行情证据；模型解释未阻塞首屏。",
            [],
            "模型解释未在首屏请求",
        )
        card["analysis_mode"] = "deterministic_market"
        card["model_connection"] = {
            "configured": configured,
            "status": "not_requested",
            "used_for_diagnosis": False,
            "fallback": None,
        }
        # “首屏不调模型”是产品策略，不是数据缺口。以前把它塞进 missing，
        # 又用只针对模型输出的 evidence gate 覆盖快行情，导致 8/8 实时
        # 字段齐全仍被标成“关键证据不完整”。快速诊断的正式证据就是
        # MarketDataGateway 的字段级快照；模型证据验证不应参与此分支。
        card["missing"] = [
            item for item in (card.get("missing") or [])
            if item != "模型解释未在首屏请求"
        ]
        card["model_explanation_status"] = "not_requested"
        evidence_values = {
            str(row.get("field") or ""): row.get("value")
            for row in market_evidence
            if isinstance(row, dict) and row.get("value") is not None
        }
        # 快照接口偶发缺一票时，统一网关已经拿到的字段仍是正式证据。
        # 不能因为 quote 字典为空就让交易计划和前端再次丢掉现价/昨收。
        normalized_quote = dict(quote)
        normalized_quote.setdefault("code", code)
        for target, field in (
            ("price", "last_price"), ("prev_close", "prev_close"),
            ("change_pct", "change_pct"), ("amount", "amount"),
            ("turnover_rate", "turnover_rate"), ("volume_ratio", "volume_ratio"),
        ):
            # 这些字段的决策权在 MarketDataGateway；直连快照只补名称和
            # OHLC。两者同时存在时也必须使用已登记 evidence_id 的值，
            # 避免卡片正文与证据链显示两个现价。
            if evidence_values.get(field) is not None:
                normalized_quote[target] = evidence_values[field]
        card["market_quote"] = {
            key: normalized_quote.get(key)
            for key in (
                "code", "name", "price", "prev_close", "open", "high", "low",
                "change_pct", "amount", "turnover_rate", "volume_ratio", "source",
            )
            if normalized_quote.get(key) is not None
        }
        return _attach_market_evidence(
            card, market_evidence, market_evidence_quality
        )

    # 先判定模型契约，再决定是否值得启动完整慢数据链。
    # 未配置/不可用时只取快行情，避免分时、日K、板块、资金和财务
    # 中任何一个慢源占住页面。这也防止删除模型连接后返回 90s 内的旧 AI 缓存。
    model_connection_status = await _model_connection_state(engine)
    if model_connection_status in {"not_configured", "unavailable"}:
        quote, market_evidence, market_evidence_quality = await _fast_fallback_context(
            engine, code
        )
        name = _coerce_str_or_none(quote.get("name")) or ""
        card = _model_connection_fallback(
            code,
            name,
            ["本次为行情安全降级，未启动完整个股慢数据采集"],
            model_connection_status,
        )
        return _apply_evidence_gate(
            _attach_market_evidence(card, market_evidence, market_evidence_quality),
            market_evidence_quality,
        )

    # 结果缓存：同股 90 秒内重复看直接返回（秒开 + 省一次 AI 调用）。盘中价会动，故只短缓存。
    _hit = _DIAG_CACHE.get(code)
    if _hit and time.monotonic() - _hit[0] < _DIAG_TTL:
        # AI 结论属于慢数据，可锁 90 秒；行情证据是快数据，必须按统一
        # 网关的 3 秒快照节奏刷新。避免因整卡缓存让“可点击原始证据”
        # 比实时价慢 90 秒。
        market_evidence, market_evidence_quality = await _market_evidence_with_budget(
            engine, code, timeout=_FAST_MARKET_DATA_TIMEOUT
        )
        return _apply_evidence_gate(_attach_market_evidence(
            _hit[1], market_evidence, market_evidence_quality
        ), market_evidence_quality)

    # 1) 取真实个股数据（同步抓取，丢线程池不阻塞事件循环）。stock_detail 内部已做
    #    多源兜底 + missing 标注，取不到的字段自带 missing，绝不编造。
    detail_result, evidence_result = await asyncio.gather(
        _blocking_call_with_budget(
            stock.stock_detail, code, timeout=_FULL_STOCK_DETAIL_TIMEOUT
        ),
        _market_evidence_with_budget(
            engine, code, timeout=_FAST_MARKET_DATA_TIMEOUT
        ),
    )
    detail = detail_result if isinstance(detail_result, dict) else None
    market_evidence, market_evidence_quality = evidence_result
    if detail is None:
        # 字段级快行情已经与完整详情并行完成。这里不能再串行发起一次
        # ``quick_quote``：慢线程虽然被 wait_for 放弃，重复查询仍会把
        # 超时降级拖到下一段网络预算，并放大供应商压力。名称可由前端
        # 已展示的快照或后续详情补齐，诊断安全卡不依赖名称成立。
        card = _market_data_fallback(
            code,
            "",
            ["完整个股数据未在交互预算内返回"],
            model_connection_status,
        )
        return _apply_evidence_gate(
            _attach_market_evidence(card, market_evidence, market_evidence_quality),
            market_evidence_quality,
        )

    quote = (detail or {}).get("quote") or {}
    name = _coerce_str_or_none(quote.get("name")) or ""
    base_missing = list((detail or {}).get("missing") or [])

    # 盘口都没拿到（停牌/源全挂）→ 不必再喂模型，直接回退安全卡并透传缺失。
    if not detail or detail.get("ok") is False or not quote:
        miss = base_missing + (["实时盘口"] if not quote else [])
        return _attach_market_evidence(
            _fallback(code, name, "", miss, "实时盘口与基础数据缺失，无法生成结构化诊断"),
            market_evidence,
            market_evidence_quality,
        )

    # 2) 控 token：分时只留每 5 分钟采样 + 最近 30 分钟（与 engine.chat_context 同口径），
    #    日K 截到最近 60 根。原 detail 不可变心智无所谓——它是本函数私有的一次性返回值。
    minute = detail.get("minute") or []
    if len(minute) > 90:
        sampled = minute[::5]
        tail = minute[-30:]
        seen = {m.get("t") for m in sampled}
        detail["minute"] = sorted(
            sampled + [m for m in tail if m.get("t") not in seen],
            key=lambda m: m.get("t") or "",
        )
    if isinstance(detail.get("daily"), list) and len(detail["daily"]) > 60:
        detail["daily"] = detail["daily"][-60:]

    # 3) 拼 prompt：system = 通用人格(数据真实性铁律) + 知识库摘要 + 方法论体系 + JSON 输出铁律；
    #    user = 注入的真实个股数据(prompts._inject 走同一列式编码 + missing 透出)。
    try:
        digest = engine.store.knowledge_digest()
    except Exception:  # noqa: BLE001 — 知识库摘要非关键，失败按空处理
        digest = ""
    methodology = load_methodology()  # 目录缺失/空 → 空串，拼接即 no-op，安全降级
    system = prompts.system_prompt(digest) + methodology + _JSON_RULES

    # 基本面财务（新浪免费公开财务分析：营收/净利同比、ROE、毛利率、负债率）——补齐证据链的"基本面"维度，
    # 判估值杀 vs 业绩杀。非关键：公开源取不到按 None 降级并标 missing，绝不阻断诊断、绝不编造。报告期级非实时。
    fina = None
    try:
        fina = await _blocking_call_with_budget(
            tx.fina_indicator, code, timeout=_FUNDAMENTAL_DATA_TIMEOUT
        )
    except Exception as e:  # noqa: BLE001 — 财务取数失败按缺失降级，不影响诊断
        log.info("个股诊断财务取数失败 code=%s: %s", code, e)
    if not fina:
        base_missing = base_missing + ["基本面财务(新浪公开源取不到)"]

    inject = {
        "今日日期": (detail or {}).get("ts", ""),
        "个股数据": detail,
        "所属板块": (detail.get("board") or {}),
        "基本面财务": fina or "数据不全（无财务权限/取不到，基本面维度请少下定论、相应调低 conviction）",
    }
    user = (
        f"请对股票 {code}（{name or '名称见注入数据'}）输出结构化证据链诊断卡。"
        "严格只返回符合 schema 的 JSON，不要任何额外文字。" + prompts._inject(inject)
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # 4) 调 Hermes 非流式 chat（温度调低让 JSON 更稳；max_tokens 给足以容纳证据列表）。
    #    HermesClient.chat 内部已有重试/超时处理；任何异常都回退安全卡，绝不阻断。
    raw = ""
    try:
        structured = getattr(engine.hermes, "chat_json", None)
        if callable(structured):
            parsed = await structured(
                messages,
                required_keys=("code", "action", "conviction", "evidence", "summary"),
                validator=lambda value: (
                    value.get("action") in _ACTIONS
                    and value.get("conviction") in _CONVICTIONS
                    and isinstance(value.get("evidence"), list)
                    and isinstance(value.get("missing", []), list)
                ),
                temperature=0.2,
                max_tokens=1500,
                model=engine.config.hermes_fast_model,
                purpose="diagnosis:stock",
            )
        else:
            raw = await engine.hermes.chat(
                messages,
                temperature=0.2,
                max_tokens=1500,
                model=engine.config.hermes_fast_model,
                purpose="diagnosis:stock",
            )
            parsed = _parse_json(raw)
    except Exception:  # noqa: BLE001 — 网关异常回退安全卡（含真实缺失透传）
        log.exception("个股诊断 Hermes 调用失败 code=%s", code)
        return _attach_market_evidence(
            _fallback(code, name, "", base_missing, "AI网关调用失败（ai_gateway_unavailable）"),
            market_evidence,
            market_evidence_quality,
        )

    # 5) 解析 + schema 校验补全；解析失败回退安全卡（summary 取正文前 200 字）。
    if parsed is None:
        log.warning("个股诊断 JSON 解析失败 code=%s", code)
        return _attach_market_evidence(
            _fallback(code, name, "", base_missing, "AI返回非JSON,已回退"),
            market_evidence,
            market_evidence_quality,
        )
    card = _normalize(parsed, code, name, base_missing)
    card["analysis_mode"] = "model_enhanced"
    card["model_connection"] = {
        "configured": model_connection_status != "unchecked",
        "status": model_connection_status,
        "used_for_diagnosis": True,
        "fallback": None,
    }
    card["sector"] = str((detail.get("board") or {}).get("industry") or "")[:80]
    card = _validate_model_evidence(card, detail, fina, market_evidence)
    _apply_stop_floor(card, quote, detail.get("daily") or [])   # 确定性止损底线（白皮书吸收②）
    card = _validate_price_levels(
        card,
        quote,
        detail.get("daily") or [],
        market_evidence=market_evidence,
    )
    card = _apply_evidence_gate(
        _attach_market_evidence(card, market_evidence, market_evidence_quality),
        market_evidence_quality,
    )
    if len(_DIAG_CACHE) > 256:                 # 防无限增长
        _DIAG_CACHE.clear()
    _DIAG_CACHE[code] = (time.monotonic(), card)
    return card
