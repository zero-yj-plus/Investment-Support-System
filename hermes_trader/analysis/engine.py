"""分析引擎：采集真实数据 → 构建提示词 → 调 Hermes 流式生成 → 落盘/推送。

所有 akshare 同步采集都通过 asyncio.to_thread 跑在线程池，不阻塞事件循环。
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import AsyncIterator
import unicodedata

from ..agent import prompts
from ..agent.hermes_client import HermesClient
from ..data import (
    free_source,
    market,
    news,
    news_observation,
    official_disclosure,
    official_interaction,
    stock,
    tdx_api_source,
)
from ..data.base import is_trade_day, next_trade_day, now_cn, prev_trade_day, ts_str
from ..delivery import canonical_daily_plan_id, push_report_with_receipt
from ..analysis.advice_proposal import proposal_ledger_for
from ..analysis import sentiment as senti
from ..analysis import diagnosis as diagnosis_mod
from ..analysis import industry_chain as industry_chain_mod
from ..analysis.claim_validator import validate_report_finalization
from ..analysis.methodology import load_methodology
from ..analysis.report_lineage import build_lineage, report_ref

log = logging.getLogger("hermes.engine")


# SSE 是直接面向客户端的边界。上游异常常带 URL、查询参数、
# 本机路径甚至 API key，因此仅将完整异常写服务端日志；流内只发
# 稳定错误码和安全文案。这些错误码同时也是前端/运维可依赖的契约。
_STREAM_ERROR_MESSAGES = {
    "report_context_failed": "行情数据采集失败，报告未生成；请稍后重试。",
    "report_generation_failed": "AI 生成服务暂不可用，报告未生成；请稍后重试。",
    "report_internal_failure": "报告生成失败；请稍后重试或查看服务端日志。",
    "chat_context_failed": "实时数据采集失败，本次回答未生成；请稍后重试。",
    "chat_generation_failed": "AI 生成服务暂不可用，本次回答未完成；请稍后重试。",
    "chat_internal_failure": "问答准备失败；请稍后重试或查看服务端日志。",
    "diy_context_failed": "研究数据采集失败，本次研究未生成；请稍后重试。",
    "diy_generation_failed": "AI 生成服务暂不可用，本次研究未完成；请稍后重试。",
    "diy_internal_failure": "研究任务准备失败；请稍后重试或查看服务端日志。",
}


def _safe_stream_error(error_code: str) -> dict[str, str]:
    """构造可安全穿过 SSE 边界的错误事件。"""
    return {"error_code": error_code, "error": _STREAM_ERROR_MESSAGES[error_code]}


_RISK_INCREASING_RE = re.compile(r"买入|建仓|加仓|低吸|介入|试仓|抄底|进攻仓")
_NEGATED_ACTION_RE = re.compile(r"不|禁止|暂停|避免|回避|观望|休息|不得|取消|无|只看")
_DIRECT_ORDER_RE = re.compile(
    r"立即买入|直接买入|现在买入|马上买入|果断买入|直接下单|无条件买入|满仓|重仓"
)
_COMPANY_SECTION_EIGHT_RE = re.compile(
    r"(?ms)(^#{0,3}\s*八、交易节奏[^\n]*\n).*?(?=^#{0,3}\s*九、风险提示)"
)
_FREE_STOCK_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_A_SHARE_CODE_PREFIXES = ("0", "3", "4", "6", "8", "9")


def _normalized_stock_reference(value: object) -> str:
    """Normalize a user/company-name fragment without fuzzy guessing."""
    text = unicodedata.normalize("NFKC", str(value or "")).upper()
    return re.sub(r"[\s·•．。\-_/（）()\[\]【】]+", "", text)


def _stock_name_aliases(name: str) -> set[str]:
    """Return conservative full-name aliases for status/special-board suffixes."""
    normalized = _normalized_stock_reference(name)
    if not normalized:
        return set()
    aliases = {normalized}
    for prefix in ("*ST", "ST"):
        if normalized.startswith(prefix) and len(normalized) - len(prefix) >= 2:
            aliases.add(normalized[len(prefix):])
    # A/B and U/W suffixes are part of exchange display names. Accept the
    # complete company name without that suffix, but never create arbitrary
    # partial-name aliases.
    if normalized[-1:] in {"A", "B", "U", "W"} and len(normalized) >= 3:
        aliases.add(normalized[:-1])
    return {alias for alias in aliases if len(alias) >= 2}


def resolve_free_stock_reference(message: str) -> dict:
    """Resolve a single A-share mentioned in free chat from local last-good data.

    This boundary intentionally performs no network I/O. One unique match may
    enter the governed stock-analysis path; multiple or unrecognized explicit
    references must be clarified instead of silently choosing a security.
    """
    text = str(message or "").strip()
    normalized_text = _normalized_stock_reference(text)
    universe = free_source.cached_stock_universe()
    universe_status = free_source.stock_universe_status()
    universe_complete = bool(universe_status.get("complete")) and not bool(
        universe_status.get("missing_exchanges")
    )
    matches: dict[str, dict] = {}
    unrecognized_codes: list[str] = []

    for code in dict.fromkeys(_FREE_STOCK_CODE_RE.findall(text)):
        if not code.startswith(_A_SHARE_CODE_PREFIXES):
            continue
        name = str(universe.get(code) or "").strip()
        if name or not universe_complete:
            matches[code] = {
                "code": code,
                "name": name,
                "match_by": "code",
            }
        else:
            unrecognized_codes.append(code)

    if normalized_text and universe:
        for code, raw_name in universe.items():
            name = str(raw_name or "").strip()
            if not name:
                continue
            if any(alias in normalized_text for alias in _stock_name_aliases(name)):
                row = matches.setdefault(
                    str(code),
                    {"code": str(code), "name": name, "match_by": "name"},
                )
                if row["match_by"] == "code":
                    row["match_by"] = "code+name"
                if not row.get("name"):
                    row["name"] = name

    candidates = sorted(matches.values(), key=lambda row: row["code"])
    if len(candidates) == 1 and not unrecognized_codes:
        return {
            "state": "matched",
            **candidates[0],
            "candidates": candidates,
            "universe_complete": universe_complete,
            "universe_as_of": universe_status.get("as_of"),
        }
    if len(candidates) > 1 or (candidates and unrecognized_codes):
        return {
            "state": "ambiguous",
            "candidates": candidates,
            "unrecognized_codes": unrecognized_codes,
            "universe_complete": universe_complete,
            "universe_as_of": universe_status.get("as_of"),
        }
    if unrecognized_codes:
        return {
            "state": "unrecognized",
            "candidates": [],
            "unrecognized_codes": unrecognized_codes,
            "universe_complete": universe_complete,
            "universe_as_of": universe_status.get("as_of"),
        }
    return {
        "state": "none",
        "candidates": [],
        "unrecognized_codes": [],
        "universe_complete": universe_complete,
        "universe_as_of": universe_status.get("as_of"),
    }


def stock_reference_clarification(reference: dict) -> str:
    """Render a stable, model-free clarification for non-unique references."""
    if reference.get("state") == "unrecognized":
        codes = "、".join(reference.get("unrecognized_codes") or []) or "该代码"
        return (
            f"检测到疑似 A 股代码 {codes}，但本地完整证券名录未找到对应股票。"
            "本次不会把错误代码当成个股生成分析；请核对 6 位代码或输入完整股票名称。"
        )
    labels = [
        f"{row.get('name') or '名称待行情核验'}（{row.get('code')}）"
        for row in (reference.get("candidates") or [])[:6]
    ]
    labels.extend(
        f"未识别代码（{code}）"
        for code in (reference.get("unrecognized_codes") or [])[:3]
    )
    detail = "、".join(labels) or "多个股票引用"
    return (
        f"这句话同时识别到 {detail}。为避免把不同股票的数据混在一起，"
        "请每次明确一只股票的 6 位代码或完整名称；本次不猜测标的。"
    )


def _company_deterministic_trade_section(context: dict, *, market_closed: bool) -> str:
    """用确定性技术层重建公司研究的交易节奏，模型无权另造价位或动作。"""
    technical = context.get("确定性技术分析(唯一价位依据)") or {}
    levels = technical.get("levels") if isinstance(technical, dict) else {}
    levels = levels if isinstance(levels, dict) else {}

    def level_line(label: str, value_key: str, basis_key: str) -> str:
        value = levels.get(value_key)
        basis = str(levels.get(basis_key) or "").strip()
        if not isinstance(value, (int, float)):
            return f"- {label}：数据不全，暂不给点位。"
        basis_note = f"（{basis}）" if basis else ""
        return f"- {label}：{value:.2f} 元{basis_note}；只作为下一交易日复核位，不是无条件指令。"

    timing = (
        "当前为休市或非交易时段，本段只给下一交易日观察条件；"
        "不提供试仓、开仓、加仓、买入或仓位建议。"
        if market_closed
        else
        "本段只复述确定性技术层的观察位；任何动作仍须等待盘中量价确认与风险闸。"
    )
    return "\n".join([
        "八、交易节奏（确定性复核）",
        "",
        timing,
        "",
        level_line("上方观察位", "resistance", "resistance_basis"),
        level_line("下方观察位", "support", "support_basis"),
        "- 独立止损位：确定性技术层未给出时，数据不全，暂不给点位。",
        "- 复核要求：下一交易日重新读取实时行情、市场情绪与板块状态后再判断；本报告不构成交易授权。",
        "",
    ])


def _sanitize_company_report(
    content: str,
    context: dict,
    *,
    market_closed: bool,
) -> str:
    """公司研究最终边界：重建动作段并移除可确定识别的越界推断。"""
    text = str(content or "").strip()
    deterministic = _company_deterministic_trade_section(
        context, market_closed=market_closed
    )
    if _COMPANY_SECTION_EIGHT_RE.search(text):
        text = _COMPANY_SECTION_EIGHT_RE.sub(deterministic, text, count=1)
    else:
        section_nine = re.search(r"(?m)^#{0,3}\s*九、风险提示", text)
        if section_nine:
            text = text[:section_nine.start()] + deterministic + text[section_nine.start():]
        else:
            text = text + "\n\n" + deterministic

    cleaned: list[str] = []
    for raw in text.splitlines():
        line = raw
        stripped = line.strip()
        if (
            market_closed
            and _RISK_INCREASING_RE.search(stripped)
            and not _NEGATED_ACTION_RE.search(stripped)
        ):
            indent = re.match(r"^\s*", line).group(0)
            line = indent + "非交易时段不提供新增风险动作，本轮只保留观察与确认条件。"
            stripped = line.strip()
        if "现金流" in stripped and re.search(
            r"充裕|充沛|良好|强劲|优异|稳定|改善", stripped
        ):
            indent = re.match(r"^\s*", line).group(0)
            line = indent + "现金流明细未接入，本轮无法据此判断。"
            stripped = line.strip()
        if re.search(r"历史上.{0,60}(MA|均线|价位|位置|支撑|压力)", stripped):
            indent = re.match(r"^\s*", line).group(0)
            line = indent + "缺少历史价位验证序列，无法判断该位置以往表现。"
            stripped = line.strip()
        if (
            re.search(r"CAGR|上行空间|隐含|PEG", stripped, flags=re.IGNORECASE)
            and re.search(r"\d", stripped)
        ):
            indent = re.match(r"^\s*", line).group(0)
            line = indent + "本轮未注入该项可直接引用的计算结果，不输出二次推导数字。"
            stripped = line.strip()
        if re.search(r"回购.{0,28}(低估|托底|维护意图)", stripped):
            indent = re.match(r"^\s*", line).group(0)
            line = (
                indent
                + "已核验回购公告标题与日期；未注入公告全文，不推断管理层估值判断或股价影响。"
            )
        cleaned.append(line)

    text = "\n".join(cleaned)
    text = re.sub(
        r"处于历史(?:相对)?(?:中)?偏?低位",
        "缺少历史估值序列，无法判断历史分位",
        text,
    )
    text = re.sub(
        r"处于历史低位",
        "缺少历史估值序列，无法判断历史分位",
        text,
    )
    return text.strip()

def _chain_sector_search_terms(industry: str) -> tuple[str, ...]:
    """兼容旧调用；真实实现由共享产业链内核统一维护。"""
    return industry_chain_mod.sector_search_terms(industry)


def _sanitize_unbound_report_price_lines(content: str, validation: dict) -> tuple[str, dict | None]:
    """将「完全未绑定股票」的交易价所在行整行移除。

    这不是放宽价格硬闸：清理后会重新跑同一校验器，只有不再存在
    任何无法核验的交易价才能定稿。未知股票、超出真实行情区间、
    一行绑多股等其他错误仍 fail closed。
    """
    blocked = [
        claim for claim in (validation.get("claims") or [])
        if isinstance(claim, dict) and claim.get("status") == "blocked"
    ]
    if not blocked or any(
        claim.get("reason") != "price_claim_not_bound_to_stock"
        for claim in blocked
    ):
        return content, None
    line_numbers = sorted({
        int(claim.get("line")) for claim in blocked
        if str(claim.get("line") or "").isdigit() and int(claim.get("line")) > 0
    })
    if not line_numbers:
        return content, None
    lines = str(content or "").splitlines()
    replaced: list[int] = []
    for line_number in line_numbers:
        index = line_number - 1
        if index >= len(lines):
            continue
        indent = re.match(r"^\s*", lines[index]).group(0)
        lines[index] = (
            f"{indent}- 价格数据不足，暂不给点位"
            "（原价位未绑定到对应股票，已由安全闸移除）"
        )
        replaced.append(line_number)
    if not replaced:
        return content, None
    return "\n".join(lines), {
        "applied": True,
        "reason": "unbound_stock_price_lines_removed",
        "line_count": len(replaced),
        "lines": replaced[:20],
        "original_blocked_count": len(blocked),
    }


def validate_report_advice_governance(content: str, cockpit_snapshot: dict | None) -> dict:
    """报告建议治理闸：报告只能产生 proposal，不能冒充已批准下单指令。"""
    command = (cockpit_snapshot or {}).get("command") if isinstance(cockpit_snapshot, dict) else None
    command = command if isinstance(command, dict) else {}
    level = command.get("risk_level") if command.get("risk_level") in {1, 2, 3, 4, 5} else None
    errors: list[str] = []
    blocked_lines: list[str] = []
    if level is None:
        errors.append("risk_snapshot_missing")
    for raw in str(content or "").splitlines():
        line = raw.strip()
        if not line or not _RISK_INCREASING_RE.search(line):
            continue
        if _NEGATED_ACTION_RE.search(line):
            continue
        if _DIRECT_ORDER_RE.search(line):
            errors.append("direct_order_language")
            blocked_lines.append(line[:180])
            continue
        if level == 5:
            errors.append("risk_level_5_entry_proposal")
            blocked_lines.append(line[:180])
    errors = list(dict.fromkeys(errors))
    return {
        "schema_version": "report-advice-governance.v1",
        "ok": not errors,
        "status": "passed" if not errors else "blocked",
        "proposal_only": True,
        "risk_level": level,
        "risk_rule_version": (command.get("rule_version") or command.get("version")),
        "risk_as_of": (cockpit_snapshot or {}).get("as_of") if isinstance(cockpit_snapshot, dict) else None,
        "errors": errors,
        "blocked_lines": blocked_lines[:12],
    }


def _trim(items: list, n: int) -> list:
    return items[:n] if isinstance(items, list) else items


async def _bounded_thread_call(fn, *args, timeout: float, default):
    """交互上下文的同步源边界：单个增强失败只返回默认值，不拖垮整轮问答。"""
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
    except Exception:  # noqa: BLE001 - 数据源已各自记录详情
        return default


def _report_title(rtype: str, meta: dict, data: dict) -> str:
    """次日策略的展示/推送标题必须指向真实的下一交易日。"""
    title = str(meta.get("title") or "报告")
    target = data.get("次日日期") if rtype == "evening" else None
    return f"{title}（供 {target} 使用）" if target else title


def _report_stock_codes(content: str, limit: int = 12) -> list[str]:
    """从已定稿报告提取 A 股代码，仅用于补采逐票真实收盘表现。"""
    codes = re.findall(r"(?<!\d)([03468]\d{5})(?!\d)", str(content or ""))
    return list(dict.fromkeys(codes))[:limit]


def _trim_sectors_symmetric(sectors: dict, head: int, tail: int) -> None:
    """板块行情按涨幅降序，单纯 _trim(head) 会把当日真正下跌最多的板块整段砍掉，
    导致收盘/次日报告的『弱势板块』『高位退潮板块』只能从中游微涨板块里硬凑——伪造弱势榜。
    改为对称截取：保留涨幅前 head + 跌幅后 tail（去重），并在 missing 标明两段口径，
    让模型知道注入列表只是头尾两段、中段未注入。原地修改 sectors。"""
    items = sectors.get("sectors")
    if not isinstance(items, list):
        return
    total = len(items)
    if total <= head + tail:
        return
    picked = items[:head] + items[-tail:]
    # 去重（极端情况下 head/tail 区间可能交叠，此处 total>head+tail 已排除交叠，仍按 id 兜底）
    seen: set = set()
    uniq = []
    for s in picked:
        sid = id(s)
        if sid not in seen:
            seen.add(sid)
            uniq.append(s)
    sectors["sectors"] = uniq
    note = f"板块行情共 {total} 个，仅注入涨幅前{head}+跌幅后{tail}（中段未注入）"
    miss = sectors.setdefault("missing", [])
    if isinstance(miss, list) and note not in miss:
        miss.append(note)


# ---- 持仓处理倾向（确定性规则）+ 次日兑现回检 ----
# 策略自证：收盘对每只持仓按【今日涨跌幅】给确定性处理倾向 + 方向；次日用真实价对账"建议兑现没有"。
# 全程规则/真实价，AI 不参与评分——客户最在意的"策略到底准不准"由数据自己回答。
_ADVICE_NOISE = 0.5   # 次日涨跌在 ±0.5% 内视为"横盘/中性"，不计入命中/未命中
_HOLDINGS_ADVICE_RULE_VERSION = "holdings-tendency-v1"


def _holding_tendency(pct):
    """今日涨跌幅(%) → (处理倾向短句, 方向)。方向∈{看多,看空,中性}，供次日回检。"""
    if pct is None:
        return ("数据不全，暂不评级", "中性")
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return ("数据不全，暂不评级", "中性")
    if p >= 9.8:
        return ("强势涨停，持有；明日冲高防分歧、不追高", "看多")
    if p >= 3:
        return ("偏强，持有为主、不追加", "看多")
    if p >= _ADVICE_NOISE:
        return ("小幅红盘，持有观察、暂不加仓", "看多")
    if p > -_ADVICE_NOISE:
        return ("横盘整理，持有观察、暂不动", "中性")
    if p > -3:
        return ("小幅走弱，持有观察，跌破支撑减仓", "看空")
    return ("明显走弱，减仓/不补、先控风险", "看空")


def _advice_verdict(direction, change_pct):
    """方向 + 次日涨跌幅(%) → 兑现判定。看多盼涨、看空盼跌；±噪声内记中性（不计命中率）。"""
    if change_pct is None:
        return "—"
    try:
        c = float(change_pct)
    except (TypeError, ValueError):
        return "—"
    if direction == "看多":
        return "兑现" if c > _ADVICE_NOISE else ("未兑现" if c < -_ADVICE_NOISE else "中性")
    if direction == "看空":
        return "兑现" if c < -_ADVICE_NOISE else ("未兑现" if c > _ADVICE_NOISE else "中性")
    return "中性"


class _GenSession:
    """一次报告生成的后台会话：生成在独立 asyncio.Task 里跑+落盘，与任何 SSE 订阅解耦——
    订阅(切页)断开不取消生成，报告照常保存。订阅者各持一个队列，事件扇出 + 缓冲供中途订阅回放。"""
    __slots__ = ("events", "done", "subs", "task")

    def __init__(self):
        self.events: list = []          # 已产生事件（供中途订阅回放）
        self.done = False
        self.subs: list = []            # 订阅者队列 list[asyncio.Queue]
        self.task = None


class Engine:
    def __init__(self, config, store, hermes: HermesClient, feishu=None):
        self.config = config
        self.store = store
        self.hermes = hermes
        self.feishu = feishu  # FeishuPusher | None
        self._gen_sessions: dict[str, _GenSession] = {}  # rtype -> 后台生成会话
        self._report_locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _report_lock(self, rtype: str, trade_day: str | None = None) -> asyncio.Lock:
        day = str(trade_day or now_cn().strftime("%Y-%m-%d"))
        return self._report_locks.setdefault((day, str(rtype)), asyncio.Lock())

    async def _capture_cockpit_snapshot(self) -> dict | None:
        """在报告生成前冻结与 /pulse 同源的风险权威，供提示词和定稿闸共同使用。"""
        try:
            from . import cockpit as _ck  # noqa: PLC0415
            from . import score_lock as _sl  # noqa: PLC0415
            from ..data import market as _mkt  # noqa: PLC0415

            pulse = await _sl.locked(
                self.store.root,
                "pulse",
                lambda prev: asyncio.to_thread(_ck.pulse_core, self.store, prev),
            )
            cockpit = await asyncio.to_thread(
                _ck.cockpit, self.store, pulse if isinstance(pulse, dict) else None,
            )
            command = (cockpit or {}).get("command")
            if not isinstance(command, dict) or command.get("risk_level") not in {1, 2, 3, 4, 5}:
                return None
            directions: list[dict] = []
            try:
                sectors = await asyncio.to_thread(_mkt.market_sectors)
                industries = [
                    row for row in (sectors.get("sectors") or [])
                    if row.get("kind") == "industry"
                ]
                directions = [
                    {
                        "name": row.get("name"),
                        "change_pct": row.get("change_pct"),
                        "main_flow": row.get("main_flow"),
                    }
                    for row in sorted(
                        (row for row in industries if (row.get("main_flow") or 0) > 0),
                        key=lambda row: -(row.get("main_flow") or 0),
                    )[:3]
                ]
            except Exception:  # noqa: BLE001 — 方向增强缺失不改风险权威
                directions = []
            return {
                "command": command,
                "dir": directions,
                "as_of": (cockpit or {}).get("ts"),
            }
        except Exception:  # noqa: BLE001
            log.warning("报告生成前 cockpit 风险快照失败", exc_info=True)
            return None

    def _record_report_advice_proposals(
        self,
        *,
        rtype: str,
        trade_day: str,
        content: str,
        advice_governance: dict,
    ) -> list[str]:
        """给报告中出现的候选代码建 proposal 索引；不从散文猜正式买卖动作。"""
        if rtype not in {"morning", "midday", "tail", "evening", "trend"}:
            return []
        codes = _report_stock_codes(content, limit=30)
        if not codes:
            return []
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        source = {
            "morning": "盘前", "midday": "尾盘", "tail": "尾盘",
            "evening": "盘前", "trend": "趋势票",
        }[rtype]
        ledger = proposal_ledger_for(self.store)
        proposal_ids: list[str] = []
        for code in codes:
            event = ledger.record({
                "client_request_id": f"report-proposal:{rtype}:{trade_day}:{content_hash}:{code}",
                "source": source,
                "source_artifact": {
                    "kind": "daily_report_candidate_index",
                    "content_id": f"report:{rtype}:{trade_day}",
                    "content_hash": content_hash,
                    "rtype": rtype,
                    "trade_day": trade_day,
                    "symbol_occurrence": code,
                    "extraction_rule": "explicit_a_share_code_only-v1",
                    "semantic_limit": "proposal_only_no_action_inference",
                },
                "trade_day": trade_day,
                "symbol": code,
                "action": "watch",
                "entry_condition": "报告候选提案；须经结构化触发与中央风险闸后方可晋级",
                "invalidation": ["未完成结构化触发", "未取得中央风险闸正式回执"],
                "risk_decision": {
                    "decision": "not_evaluated",
                    "reason_codes": ["report_proposal_only"],
                    "risk_level": advice_governance.get("risk_level"),
                    "rule_version": advice_governance.get("risk_rule_version"),
                },
                "state": "proposed",
            })
            proposal_ids.append(event["proposal_id"])
        return proposal_ids

    # ------------------------------------------------ 数据上下文采集

    async def _market_context(self, full: bool = True) -> dict:
        """当日市场全景上下文（报告与问答共用）。
        各独立数据源并行采集以压缩报告/问答首字节延迟（依赖 base.ttl_cache 的单飞，
        避免并发对同一被限流数据源发重复突发请求）。"""
        overview, sentiment = await asyncio.gather(
            asyncio.to_thread(market.market_overview),
            asyncio.to_thread(senti.compute_today, self.store),
        )
        ctx = {"市场总览": overview, "情绪快照": sentiment}
        if full:
            limitup, sectors, top, flow = await asyncio.gather(
                asyncio.to_thread(market.market_limitup),
                asyncio.to_thread(market.market_sectors),
                asyncio.to_thread(market.market_top),
                asyncio.to_thread(market.market_flow),
            )
            limitup["stocks"] = _trim(limitup.get("stocks", []), 60)
            # 涨幅前35+跌幅后15：保留真实弱势板块供『弱势板块前3/高位退潮』段，避免伪造
            _trim_sectors_symmetric(sectors, 35, 15)
            ctx.update({
                "涨停池": limitup,
                "板块行情": sectors,
                "涨幅前30": top,
                "资金方向": flow,
            })
        return ctx

    async def _context_for_report(self, rtype: str) -> dict:
        today = now_cn().strftime("%Y-%m-%d")
        if rtype == "morning":
            def gather():
                prev = prev_trade_day()
                ctx = {
                    "今日日期": today,
                    "昨日交易日": prev,
                    "隔夜美股": news.us_market(),
                    "财经快讯": {"items": _trim(news.cn_news().get("items", []), 40)},
                }
                if prev:
                    prev_compact = prev.replace("-", "")
                    zt = market.market_limitup(prev_compact)
                    zt["stocks"] = _trim(zt.get("stocks", []), 40)
                    ctx["昨日涨停池(仅情绪/方向参考·连板股已封板买不进)"] = zt
                # 可买候选池(未封板可买)——核心股候选优先从这里挑（客户 #2：不要只从涨停池挑连板高位股，
                # 那些买不进/追高即高位接盘）。确定性 BUYRULE 过滤，数字真实，失败不拖垮盘前研报。
                try:
                    from . import premarket_brief  # noqa: PLC0415
                    ctx["可买候选池(未封板可买·核心股候选优先从此选)"] = premarket_brief.buyable_pool(25)
                except Exception:  # noqa: BLE001 — 增量层，失败按缺失降级
                    ctx["可买候选池(未封板可买·核心股候选优先从此选)"] = {"ok": False, "missing": ["可买候选池生成失败"]}
                ctx["昨日情绪历史"] = self.store.sentiment_history(5)
                # 对称截取（涨幅前30+跌幅后10），保留真实弱势板块，避免早盘内参伪造弱势榜
                msec = market.market_sectors()
                _trim_sectors_symmetric(msec, 30, 10)
                ctx["板块行情(最近快照)"] = {
                    "sectors": msec.get("sectors", []),
                    "missing": msec.get("missing", []),
                }
                # 港股辅助：盘前研报「港股辅助」段所需的真实港股指数（此前未接入 → 该段恒回退"数据不全"）。
                # 增量层、失败不拖垮盘前研报；全体客户纯受益。
                try:
                    ctx["港股辅助"] = news.hk_indexes()
                except Exception:  # noqa: BLE001 — 港股为辅助增量，失败按缺失降级
                    ctx["港股辅助"] = []
                return ctx

            return await asyncio.to_thread(gather)

        ctx = await self._market_context(full=True)
        if rtype == "close":
            # 收盘复盘的第一职责是对账，不是用收盘后的状态重写早盘记忆。
            # 必须显式读取当日已定稿 morning 记录，并保留它生成时锁定的 cockpit；
            # 将对账基线放在上下文最前，避免长行情数据触发注入预算后把它截掉。
            morning = self.store.get_report("morning", today)
            if morning:
                morning_content = str(morning.get("content_md") or "")
                plan = {
                    "status": "available",
                    "date": morning.get("date"),
                    "title": morning.get("title"),
                    "created_at": morning.get("created_at"),
                    "content_md": morning_content[:12000],
                }
                cockpit_baseline = morning.get("cockpit")
                if not isinstance(cockpit_baseline, dict):
                    cockpit_baseline = "【缺失】该盘前定稿没有保存当时的驾驶舱快照，不得用收盘状态反推。"
                plan_codes = _report_stock_codes(morning_content)
                if plan_codes:
                    def _plan_quotes():
                        rows = []
                        for code in plan_codes:
                            try:
                                q = stock.quick_quote(code)
                            except Exception:  # noqa: BLE001 — 单票失败只标缺失，不拖垮收盘报告
                                q = None
                            rows.append(q if isinstance(q, dict) else {
                                "code": code, "missing": "收盘行情获取失败",
                            })
                        return rows

                    plan_quotes = {
                        "items": await asyncio.to_thread(_plan_quotes),
                        "scope": "仅能证明收盘价/当日涨跌；如无开高低/分时证据，不能倒推盘中入场条件已触发",
                    }
                else:
                    plan_quotes = "【缺失】盘前定稿中未识别到可对账的 A 股代码。"
            else:
                plan = "【缺失】当日盘前定稿不存在，收盘复盘不得虚构早盘判断。"
                cockpit_baseline = "【缺失】当日盘前驾驶舱快照不存在。"
                plan_quotes = "【缺失】无盘前定稿，不采集、不虚构标的表现。"
            tail_final = self.store.get_report("tail", today)
            tail_input = (
                {
                    "status": "final",
                    "report_ref": report_ref(tail_final),
                    "title": tail_final.get("title"),
                    "created_at": tail_final.get("created_at"),
                    "content_md": str(tail_final.get("content_md") or "")[:10000],
                }
                if tail_final and tail_final.get("content_md")
                else "【缺失】当日尾盘定稿不存在；收盘复盘不得虚构尾盘观察。"
            )
            try:
                event_reconciliation = await asyncio.to_thread(
                    news_observation.daily_reconciliation, today, 80,
                )
            except Exception:  # noqa: BLE001 -- 新闻观察是事实增强，失败必须显式降级
                log.exception("收盘新闻事件观察对账失败")
                event_reconciliation = {
                    "ok": False,
                    "trade_day": today,
                    "lifecycle": "incomparable",
                    "verdict": "证据不足",
                    "reason_code": "observation_read_failed",
                    "items": [],
                    "score_weight": 0,
                    "causality": "not_inferred",
                }
            event_evidence = {
                "kind": "news_event_observation",
                "trade_day": today,
                "content_hash": event_reconciliation.get("content_hash"),
                "lifecycle": event_reconciliation.get("lifecycle"),
                "verdict": event_reconciliation.get("verdict"),
                "score_weight": 0,
            }
            lineage = build_lineage(
                trade_day=today,
                sources=(report_ref(morning), report_ref(tail_final)),
                evidence=(event_evidence,),
            )
            ctx = {
                "今日日期": today,
                "收盘对账判定口径(固定枚举)": {
                    "命中": "计划条件已触发，且实际方向与风险边界符合计划",
                    "部分命中": "只有部分条件/方向被验证，或结果未达计划完整预期",
                    "证伪": "计划条件已触发，但实际走势或风险结果与计划明确相反",
                    "未触发": "计划预设的入场/放弃/验证条件未发生，不得计为命中或证伪",
                    "证据不足": "基线、触发条件或可比行情缺失，暂不判定，不得冒充未触发",
                },
                "今日盘前计划定稿(原文·逐条对账基线)": plan,
                "盘前驾驶舱快照(原始定格·不得用收盘状态覆盖)": cockpit_baseline,
                "盘前计划标的收盘表现(真实行情·按代码对账)": plan_quotes,
                "今日尾盘决策定稿(同交易日·不得回读其他日期)": tail_input,
                "今日事件观察对账(盘面事实·不推断新闻因果)": event_reconciliation,
                "数据版本与来源锁(系统审计)": lineage,
                **ctx,
            }
        else:
            ctx["今日日期"] = today
        if rtype == "tail":
            draft = self.store.get_report("midday", today)
            ctx["尾盘决策草案(14:45原文·仅作复核输入)"] = (
                {
                    "status": "draft",
                    "created_at": draft.get("created_at"),
                    "content_md": str(draft.get("content_md") or "")[:8000],
                    "cockpit": draft.get("cockpit") or "【缺失】草案未保存 14:45 驾驶舱快照",
                }
                if draft
                else "【缺失】14:45 草案未生成；14:52 仍可按当前真实数据独立定稿，但不得虚构草案变化。"
            )
            ctx["尾盘定稿规则"] = (
                "14:45 仅为内部草案；14:52 是唯一对客正式尾盘结论。逐条复核草案后只输出定稿。"
            )
            ctx["数据版本与来源锁(系统审计)"] = build_lineage(
                trade_day=today,
                sources=(report_ref(draft),),
            )
        if rtype in ("close", "evening"):
            # 持仓逐只补实时价：收盘复盘/次日策略要给「高开冲X元减、回踩Y元持有」价格条件，
            # 仅注入原始持仓(代码/成本/股数)而无今日行情，AI 只能写「数据不全」。quick_quote
            # 走腾讯单票(绕东财、本机可达)，丢线程池避免阻塞事件循环；取不到才标缺失(铁律)。
            holds = self.store.holdings()
            if holds:
                def _enrich_holdings():
                    return [
                        {**h, "实时行情": stock.quick_quote(h.get("code", "")) or "数据不全"}
                        for h in holds[:20]
                    ]
                enriched = await asyncio.to_thread(_enrich_holdings)
                ctx["用户持仓"] = enriched
                if rtype == "close":
                    # 策略自证：今日处理倾向(规则)落库快照 + 注入；昨日建议用今日真实价回检后注入。
                    # 数字全程走规则/真实价、AI 不参与评分——客户最在意的"策略准不准"由数据自答。
                    advice = self._compute_holdings_advice(enriched)
                    try:
                        self.store.save_holdings_advice(today, advice)
                    except Exception:  # noqa: BLE001 — 快照失败不拖垮报告生成
                        log.exception("持仓建议快照落库失败")
                    try:
                        self._record_holdings_advice_proposals(today, advice)
                    except Exception:  # noqa: BLE001 — proposal 留痕失败不拖垮收盘报告
                        log.exception("持仓处理倾向 proposal 落库失败")
                    ctx["持仓处理倾向(系统规则·今日·点评时直接引用)"] = advice
                    review = await self.holdings_advice_review()
                    ctx["昨日持仓建议回检(真实价对账)"] = (
                        {"对照日": review.get("base_date"), "明细": review.get("rows"),
                         "命中统计": review.get("stats")}
                        if review.get("rows")
                        else "未注入昨日持仓建议快照，无法回检（收盘复盘生成后次日起可对账）"
                    )
            else:
                ctx["用户持仓"] = []
            ctx["情绪历史(近10日)"] = self.store.sentiment_history(10)
            # 公开盘后池增强(kpl_list，涨跌停池 + 同花顺公开原因字段)：
            # ① 炸板时间轴(首封→炸板时刻)——早盘炸=承接弱、尾盘炸=获利兑现，复盘情绪节奏的关键证据；
            # ② 涨停题材簇(theme 聚合家数)——当日主线归因(确定性计数，非 AI 归纳)。
            # 今日未落库 → 如实标注缺席，绝不拿昨日冒充今日(铁律)。
            try:
                from ..data import free_source as _tx  # noqa: PLC0415
                _dc = today.replace("-", "")
                _kzb = await asyncio.to_thread(_tx.kpl_list, _dc, "炸板") or []
                _kzt = await asyncio.to_thread(_tx.kpl_list, _dc, "涨停") or []
            except Exception:  # noqa: BLE001 — 增强层，失败按缺失降级
                _kzb, _kzt = [], []
            if _kzb:
                ctx["炸板时间轴(开盘啦)"] = [
                    {"名称": z.get("name"), "首封": z.get("lu_time"), "炸板": z.get("open_time"),
                     "题材": z.get("theme")} for z in _kzb[:30]]
            if _kzt:
                _tc: dict = {}
                for z in _kzt:
                    for t in str(z.get("theme") or "").split("、"):
                        t = t.strip()
                        if t:
                            _tc[t] = _tc.get(t, 0) + 1
                ctx["涨停题材簇(开盘啦,家数)"] = dict(sorted(_tc.items(), key=lambda x: -x[1])[:12])
            if not (_kzb or _kzt):
                ctx["开盘啦榜单"] = "【缺失】今日开盘啦涨停/炸板榜未落库(盘后更新)，炸板时间轴与题材簇本次缺席。"
        # 观察池注入（evening「观察池排序」/ tail「观察池等待」段要用）：此前从不注入 store.watchlist()
        # → AI 只能瞎写"暂无观察标的"(用户明明有)或编造。注入真实观察池+实时价，据此排序/给触发条件；
        # 为空则如实说"观察池为空"，绝不编。（审计 Tier4 修）
        if rtype in ("evening", "tail"):
            wl = self.store.watchlist()
            if wl:
                def _enrich_watch():
                    from . import watchlist_zones  # noqa: PLC0415

                    selected = wl[:20]
                    quotes = stock.quick_quotes([w.get("code", "") for w in selected])
                    zones = self.store.watchlist_zones() if hasattr(self.store, "watchlist_zones") else {}
                    enriched = []
                    for item in selected:
                        code = str(item.get("code") or "").strip().zfill(6)
                        quote = quotes.get(code)
                        merged = watchlist_zones.enrich_watch_item(item, zones.get(code), quote)
                        merged["实时行情"] = quote or "数据不全"
                        enriched.append(merged)
                    return enriched
                ctx["用户观察池(含理想买入区间/放弃价+实时价·据此排序与给触发条件)"] = await asyncio.to_thread(_enrich_watch)
            else:
                ctx["用户观察池"] = "空（用户尚未添加任何观察标的）——本段须如实说明「观察池为空」，绝不编造观察标的"

        if rtype == "evening":
            # 必须显式指定交易日，禁止“最近一份”在跨日/补跑时混入。
            tail = self.store.get_report("tail", today)
            close = self.store.get_report("close", today)
            # tail/close 未生成时显式标注缺失，而非塞裸字符串"无"被模型当真实空数据。
            tail_frozen = (
                {
                    "report_ref": report_ref(tail),
                    "content_md": str(tail.get("content_md") or "")[:10000],
                } if tail
                else "【缺失】今日尾盘观察报告尚未生成，本次次日策略缺少尾盘资金/观察清单输入，请据此降低确定性。"
            )
            close_frozen = (
                {
                    "report_ref": report_ref(close),
                    "content_md": str(close.get("content_md") or "")[:14000],
                } if close
                else "【缺失】今日收盘复盘报告尚未生成，本次次日策略缺少赚钱效应/周期判断输入，请据此降低确定性。"
            )
            frozen_rule = (
                "尾盘与收盘定稿是今日事实的唯一基线；晚间新数据只能用于构造次日条件，"
                "不得回头改写今日盘面、对账结论或已定稿的持仓处理倾向。"
            )
            lineage = build_lineage(
                trade_day=today,
                sources=(report_ref(tail), report_ref(close)),
            )
            # 注入器按字典顺序并有 60k 上限：必须把定稿母版放最前，
            # 再把 19:30 刷新数据收进明确的“次日条件”容器，防止母版被截断。
            refreshed = {key: value for key, value in ctx.items() if key != "今日日期"}
            ctx = {
                "今日日期": today,
                "今日事实冻结规则": frozen_rule,
                "今日尾盘观察(同日定稿)": tail_frozen,
                "今日收盘复盘(同日定稿)": close_frozen,
                "数据版本与来源锁(系统审计)": lineage,
                "次日日期": next_trade_day(now_cn().date()),
                "19:30盘后刷新(仅用于次日条件·不改写今日事实)": refreshed,
            }
        if rtype == "trend":
            ctx["要求"] = "从涨幅榜/板块/资金数据中挖掘趋势票，剔除名称含ST的股票"
        return ctx

    # ------------------------------------------------ 持仓建议·策略自证

    def _compute_holdings_advice(self, enriched: list[dict]) -> list[dict]:
        """从已补实时价的持仓列表算确定性处理倾向快照（落库 + 注入用）。"""
        out = []
        for h in enriched:
            q = h.get("实时行情")
            pct = q.get("change_pct") if isinstance(q, dict) else None
            price = q.get("price") if isinstance(q, dict) else None
            tendency, direction = _holding_tendency(pct)
            try:
                numeric_pct = float(pct)
            except (TypeError, ValueError):
                numeric_pct = None
            action = "watch" if numeric_pct is None else ("reduce" if numeric_pct <= -3 else "hold")
            out.append({
                "code": str(h.get("code", "")).zfill(6),
                "name": h.get("name") or (q.get("name") if isinstance(q, dict) else "") or str(h.get("code", "")),
                "tendency": tendency,
                "direction": direction,
                "action": action,
                "close_price": price,
                "today_pct": pct,
                "price_source": q.get("source") if isinstance(q, dict) else None,
                "price_as_of": q.get("as_of") if isinstance(q, dict) else None,
                "price_stale": bool(q.get("stale")) if isinstance(q, dict) else None,
                "rule_version": _HOLDINGS_ADVICE_RULE_VERSION,
            })
        return out

    def _record_holdings_advice_proposals(self, trade_day: str, advice: list[dict]) -> list[dict]:
        """把确定性持仓处理倾向追加为 proposal，不创建正式推荐。

        同一交易日/代码使用固定 client_request_id，收盘报告重试时只会幂等
        返回首次事件。即使动作是 reduce，也只是待风险闸/用户确认的 proposal。
        """
        ledger = proposal_ledger_for(self.store)
        recorded: list[dict] = []
        existing_by_request = {
            str(row.get("client_request_id") or ""): row
            for row in ledger.events()
            if row.get("client_request_id")
        }
        for item in advice or []:
            code = str(item.get("code") or "").strip().zfill(6)
            if not code or code == "000000":
                log.warning("持仓处理倾向缺少有效代码，已跳过 proposal")
                continue
            request_id = f"holdings-advice:{trade_day}:{code}"
            if request_id in existing_by_request:
                # 当日首次收盘快照是不可改写的事实。报告重试时即使行情源
                # 返回了稍有变化的价格，也只返回已落的首版，不追加、不改写。
                recorded.append(existing_by_request[request_id])
                continue
            price = item.get("close_price")
            reference_price_evidence = None
            if isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0:
                source_id = item.get("price_source")
                observed_at = item.get("price_as_of")
                reference_price_evidence = {
                    "value": price,
                    "currency": "CNY",
                    "field": "last_price",
                    "source_id": source_id,
                    "observed_at": observed_at,
                    "quality_status": (
                        "unverified" if not source_id or not observed_at
                        else ("stale" if item.get("price_stale") else "ok")
                    ),
                }
            event = ledger.record({
                "client_request_id": request_id,
                "source": "持仓",
                "source_artifact": {
                    "kind": "holdings_advice",
                    "artifact_id": f"holdings_advice/{trade_day}/{code}",
                    "date": trade_day,
                    "code": code,
                    "rule_version": item.get("rule_version") or _HOLDINGS_ADVICE_RULE_VERSION,
                },
                "trade_day": trade_day,
                "symbol": code,
                "name": item.get("name"),
                "action": item.get("action") or "watch",
                "entry_condition": item.get("tendency"),
                "invalidation": [],
                "reference_price_evidence": reference_price_evidence,
                "risk_decision": {
                    "decision": "not_evaluated",
                    "reason_codes": ["proposal_only"],
                    "rule_version": item.get("rule_version") or _HOLDINGS_ADVICE_RULE_VERSION,
                },
                "state": "proposed",
            })
            existing_by_request[request_id] = event
            recorded.append(event)
        return recorded

    async def holdings_advice_review(self) -> dict:
        """策略自证：读最近一个【早于今天】的持仓建议快照，用今日真实价对账"昨日建议兑现没有"。
        数字全程真实价/规则、AI 不参与。返回 {ok, base_date, rows, stats, missing}。"""
        today = now_cn().strftime("%Y-%m-%d")
        prev = self.store.holdings_advice_prev(today)
        if not prev or not prev.get("items"):
            return {"ok": True, "base_date": None, "rows": [], "stats": {},
                    "missing": ["暂无早于今日的持仓建议快照——收盘复盘生成后次日起即可回检"]}
        items = prev["items"]

        def gather():
            rows = []
            for it in items:
                code = str(it.get("code", "")).zfill(6)
                q = stock.quick_quote(code)
                today_price = q.get("price") if isinstance(q, dict) else None
                base_price = it.get("close_price")
                chg = None
                if (isinstance(today_price, (int, float)) and isinstance(base_price, (int, float))
                        and base_price):
                    chg = round((today_price - base_price) / base_price * 100, 2)
                direction = it.get("direction", "中性")
                rows.append({
                    "code": code,
                    "name": it.get("name") or code,
                    "tendency": it.get("tendency", ""),
                    "direction": direction,
                    "base_price": base_price,
                    "today_price": today_price,
                    "change_pct": chg,
                    "verdict": _advice_verdict(direction, chg),
                })
            return rows

        rows = await asyncio.to_thread(gather)
        hit = sum(1 for r in rows if r["verdict"] == "兑现")
        miss = sum(1 for r in rows if r["verdict"] == "未兑现")
        neutral = sum(1 for r in rows if r["verdict"] in ("中性", "—"))
        scored = hit + miss
        win_rate = round(hit / scored * 100, 1) if scored else None
        return {
            "ok": True,
            "base_date": prev["date"],
            "rows": rows,
            "stats": {"hit": hit, "miss": miss, "neutral": neutral,
                      "scored": scored, "win_rate": win_rate},
            "missing": [],
        }

    # ------------------------------------------------ 报告生成

    async def generate_report_stream(self, rtype: str, push: bool = False) -> AsyncIterator[dict]:
        """流式生成报告。yield {"delta": ...} / {"done": True, ...} / {"error": ...}"""
        meta = prompts.REPORT_META.get(rtype)
        if not meta:
            yield {"error": f"未知报告类型: {rtype}"}
            return
        # 分阶段等待事件（首个 delta 前）：报告采集数据耗时较长，前端据此显示阶段+已等待秒数
        yield {"stage": "采集行情数据…"}
        try:
            data = await self._context_for_report(rtype)
        except Exception:  # noqa: BLE001 — 详情只写服务端日志
            log.exception("采集数据失败")
            yield _safe_stream_error("report_context_failed")
            return
        try:
            digest = self.store.knowledge_digest()
            # 交易日意识：休市/非交易日时让 prompt 改写口径（不再把休市当"今天能不能做"的可交易日）。
            # is_trade_day 带日历缓存，丢线程池避免冷取卡事件循环。
            trade_today = await asyncio.to_thread(is_trade_day)
            cockpit_snap = (
                await self._capture_cockpit_snapshot()
                if trade_today
                else {
                    "command": {
                        "risk_level": 5,
                        "rule_version": "market-closed-v1",
                        "forbid": ["休市不新增风险"],
                    },
                    "dir": [],
                    "as_of": now_cn().isoformat(timespec="seconds"),
                }
            )
            if trade_today and cockpit_snap is None:
                log.error("报告 [%s] 缺少生成前风险快照，已阻止正式报告", rtype)
                yield {"error": "系统风险快照不可用，报告未生成；新增风险建议不能绕过五级风险闸。"}
                return
            data["系统风险闸定格(建议治理唯一权威)"] = cockpit_snap or {
                "state": "market_closed", "proposal_only": True,
            }
            messages = prompts.build_report_prompt(rtype, data, digest, is_trade_day=trade_today)
            # 方法论技能层：把蒸馏的短线方法论体系追加到 system 末尾（紧跟 PERSONA/数据真实性铁律之后，
            # 不抢最高优先级）；目录缺失/为空时 load_methodology() 返回空串，+= 即 no-op，安全降级。
            _m = load_methodology()
            if _m and messages and messages[0].get("role") == "system":
                messages[0]["content"] += _m
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] += (
                    "\n\n【建议治理硬约束】报告中的个股只能作为候选提案/待触发观察，"
                    "必须明确写待触发、待风险闸或不计胜率；禁止使用立即买入、直接下单、"
                    "满仓、重仓等已获授权的命令语气。正式动作只能由结构化诊断或建议晋级入口"
                    "生成 RecommendationEvent。风险 5 级不得给任何新增风险提案。"
                    "任何包含具体交易价、买卖区间、止损或目标价的行/表格行，"
                    "必须在同一行明写唯一一只股票的 6 位代码和名称；"
                    "无法逐行绑定时只能写「价格数据不足，暂不给点位」。"
                )
        except Exception:  # noqa: BLE001 — 详情只写服务端日志
            log.exception("报告生成准备异常 [%s]", rtype)
            yield _safe_stream_error("report_internal_failure")
            return
        yield {"stage": "生成报告中…"}
        parts: list[str] = []
        stream_meta: dict = {}
        try:
            async for delta in self.hermes.chat_stream(
                messages, meta=stream_meta, purpose=f"report:{rtype}"
            ):
                parts.append(delta)
        except Exception:  # noqa: BLE001 — 上游异常可含密钥/URL/路径
            # 报告依赖 AI 改写、无降级版本（不同于诊断卡有安全兜底）。
            log.exception("报告 AI 生成流异常 [%s]", rtype)
            yield _safe_stream_error("report_generation_failed")
            return
        content = "".join(parts).strip()
        if not content:
            yield {"error": "Hermes 返回为空，报告未保存"}
            return
        if rtype in {"morning", "midday", "tail", "evening", "trend"}:
            content = (
                "> 建议治理：以下个股仅为候选提案/待触发观察，尚未晋级正式建议，"
                "不计入胜率；实际动作须以结构化风险闸回执和建议事件为准。\n\n"
                + content
            )
        if rtype == "morning":
            # 盘前候选时间语义门禁（确定性，非 AI 生成）：报告正文顶部注入"基于 T-1 / 盘前候选
            # 非今日可执行 / 昨日涨停竞价待核"，随 content_md 同时进入前端展示与 PDF 导出，不只留后端 payload。
            from . import premarket_brief  # noqa: PLC0415
            _pool = (data.get("可买候选池(未封板可买·核心股候选优先从此选)")
                     if isinstance(data, dict) else None)
            content = premarket_brief.premarket_provenance_header(_pool) + content
        # 正式定稿硬闸：
        # 1) finish_reason=length 代表风控/止损等后段可能缺失，绝不保存为「当日唯一定稿」；
        # 2) 报告里任何会影响下单的具体价格，必须绑定到同一只股票的真实价格证据。
        # 校验必须在 save_report / push_report 之前执行，且校验器自身异常时 fail closed。
        truncated = stream_meta.get("finish_reason") == "length"
        try:
            claim_validation = await asyncio.to_thread(
                validate_report_finalization,
                content,
                data,
                stream_meta.get("finish_reason"),
            )
        except Exception:  # noqa: BLE001 — 硬闸本身失效不得降级放行
            log.exception("报告交易价格校验异常 [%s]，已阻止定稿", rtype)
            yield {"error": "交易价格校验异常，报告未保存、未推送；请重试或联系管理员。"}
            return
        # 模型有时在一个已绑股票的大节下又起小标题，导致后续
        # 价格行丢失逐行股票身份。这类价位不能放行，但也不应让
        # 整份收盘/尾盘报告在三次重试后完全失败。只对「未绑定股票」
        # 这一种错误删除整行价位，然后必须重跑原校验器。
        if not claim_validation.get("ok") and not truncated:
            sanitized_content, sanitation = _sanitize_unbound_report_price_lines(
                content, claim_validation,
            )
            if sanitation:
                sanitized_validation = await asyncio.to_thread(
                    validate_report_finalization,
                    sanitized_content,
                    data,
                    stream_meta.get("finish_reason"),
                )
                if sanitized_validation.get("ok"):
                    log.warning(
                        "报告 %s 移除 %s 行未绑定股票的价位，以数据降级定稿",
                        rtype, sanitation.get("line_count"),
                    )
                    content = sanitized_content
                    claim_validation = dict(sanitized_validation)
                    claim_validation["sanitation"] = sanitation
        if not claim_validation.get("ok"):
            if "report_truncated" in (claim_validation.get("errors") or []):
                log.warning("报告 %s 被 max_tokens 截断（finish_reason=length），已阻止定稿/推送", rtype)
                yield {"error": "报告因长度限制被截断，可能缺少止损或风险预案；已阻止定稿和推送，请缩短内容或提高输出额度后重试。"}
            else:
                blocked = [
                    f"{c.get('name') or c.get('code') or '未绑定股票'}:{c.get('value')}"
                    for c in (claim_validation.get("claims") or [])
                    if c.get("status") == "blocked"
                ]
                log.warning("报告交易价格校验未通过 [%s]：%s", rtype, blocked[:8])
                yield {"error": "报告包含无法由对应股票真实行情支撑的具体交易价；已阻止保存和推送。请改为「价格数据不足，暂不给点位」后重试。"}
            return
        advice_governance = validate_report_advice_governance(content, cockpit_snap)
        if not advice_governance.get("ok"):
            log.warning(
                "报告建议治理未通过 [%s]: %s",
                rtype, advice_governance.get("errors"),
            )
            yield {
                "error": "报告包含未经过建议治理的直接下单措辞，或当前风险等级禁止新增风险；"
                "已阻止保存和推送。"
            }
            return
        try:
            proposal_ids = self._record_report_advice_proposals(
                rtype=rtype,
                trade_day=str(data.get("今日日期") or self.store.today()),
                content=content,
                advice_governance=advice_governance,
            )
        except Exception:  # noqa: BLE001 — 可见候选未留痕时不得发布正式报告
            log.exception("报告候选 proposal 留痕失败 [%s]", rtype)
            yield {"error": "报告候选留痕失败，已阻止保存和推送；请稍后重试。"}
            return
        advice_governance["proposal_ids"] = proposal_ids
        advice_governance["proposal_count"] = len(proposal_ids)
        rec = self.store.save_report(
            rtype,
            _report_title(rtype, meta, data),
            content,
            date=str(data.get("今日日期") or self.store.today()),
            cockpit=cockpit_snap,
            claim_validation=claim_validation,
            advice_governance=advice_governance,
            input_lineage=data.get("数据版本与来源锁(系统审计)"),
            input_context=data,
        )
        if rtype == "morning":
            # 盘前页面、核心报告、PDF 和推送必须能指向同一 plan_id/version。
            # 只附加身份元数据，绝不改写已定稿正文与证据快照。
            plan_id = canonical_daily_plan_id(
                getattr(self.config, "client_id", "default"),
                rec["date"],
            )
            identity = {
                "plan_id": plan_id,
                "plan_version": 2,
                "content_id": f"daily-plan:{plan_id}:v2",
            }
            setter = getattr(self.store, "set_report_identity", None)
            rec = (
                setter(rtype, rec["date"], identity) if callable(setter) else None
            ) or {**rec, **identity}
        # 研报数字质检（铁律自动闸 v1·只标注不拦截）：报告数字反查注入数据，可疑项写回
        # 报告记录 eval 字段 + 日志 + 哨兵日终汇总。config report_eval.enabled 门控
        # （off-by-default）；失败绝不影响报告主流程，绝不改 content（当日定稿铁律）。
        try:
            if self.config.section("report_eval").get("enabled"):
                from .report_eval import evaluate  # noqa: PLC0415
                ev = await asyncio.to_thread(evaluate, content, data)
                self.store.set_report_eval(rtype, rec["date"], ev)
                if ev.get("suspect_count"):
                    log.warning("研报数字质检[%s]: %d 个可疑数字（未在注入数据中找到）: %s",
                                rtype, ev["suspect_count"], ev["suspects"][:8])
        except Exception:  # noqa: BLE001
            log.exception("研报数字质检失败（不影响报告）")
        pushed = False
        if push:
            pushed = await push_report_with_receipt(
                store=self.store,
                pusher=self.feishu,
                meta=meta,
                rec=rec,
                rtype=rtype,
                trigger="automatic",
                config=self.config,
            )
            if pushed:
                self.store.mark_pushed(rtype, rec["date"])
        # 正文只有在价格校验、建议治理、不可变保存全部完成后才释放给客户端；
        # 生成阶段只发 stage，防止用户看见随后被风险闸拒绝的半成品动作。
        for start in range(0, len(content), 4000):
            yield {"delta": content[start:start + 4000], "validated": True}
        yield {"done": True, "saved": True, "date": rec["date"], "pushed": pushed, "truncated": truncated}

    async def generate_report(self, rtype: str, push: bool = False) -> dict | None:
        """定时与手动入口共用同一个 (交易日, 报告类型) 原子锁。"""
        trade_day = now_cn().strftime("%Y-%m-%d")
        async with self._report_lock(rtype, trade_day):
            return await self._generate_report_unlocked(rtype, push=push, trade_day=trade_day)

    async def _generate_report_unlocked(
        self, rtype: str, push: bool = False, *, trade_day: str | None = None,
    ) -> dict | None:
        """非流式版（定时任务用）。返回保存的报告记录。
        当日定稿铁律（2026-07-03 用户裁定）：每类日报**当日只生成一次、首版即定稿**——
        AI 非确定性意味着重复生成必然产出不同文本，作为决策留痕的报告不允许"每看一次变一样"；
        已存在即跳过（含定时任务：手动先生成过则到点不再覆盖）。失败未落盘的不受影响、可重试。

        网关瞬断容错（2026-07-06 开盘前审计 P0 修）：定时报告是开盘关键交付，单次 AI 网关
        500/超时曾直接导致【当日零报告】——失败在落盘(save_report)前就 return，22:05 哨兵补推
        无内容可推，客户整天看不到当日报告。改为失败自动重试若干次（指数退避）；当日定稿锁
        (上面 existing 判断)保证重试绝不重复落盘；仅在全部重试耗尽后推一次失败通知（避免每次
        重试都刷屏）。attempts/backoff_sec 走 config `report_retry`（默认 3 次 / 20s 基数，
        缺配即默认；设 attempts=1 即恢复旧行为）——母项目通用、全客户受益、off 无副作用。"""
        trade_day = str(trade_day or now_cn().strftime("%Y-%m-%d"))
        existing = self.store.get_report(rtype, trade_day)
        if existing and existing.get("content_md"):
            log.info("报告 [%s] 今日已定稿，跳过重复生成（首版为准）", rtype)
            if rtype == "morning" and not existing.get("plan_id"):
                plan_id = canonical_daily_plan_id(
                    getattr(self.config, "client_id", "default"),
                    existing["date"],
                )
                identity = {
                    "plan_id": plan_id,
                    "plan_version": 2,
                    "content_id": f"daily-plan:{plan_id}:v2",
                }
                setter = getattr(self.store, "set_report_identity", None)
                existing = (
                    setter(rtype, existing["date"], identity) if callable(setter) else None
                ) or {**existing, **identity}
            # 人工提前生成不应让定时任务到点后跳过交付。仍只使用原定稿，
            # 但对尚未送达且已通过 claim 硬闸的报告补做一次自动投递。
            if push and not existing.get("pushed"):
                validation = existing.get("claim_validation") or {}
                if validation.get("status") == "passed" and validation.get("ok"):
                    pushed = await push_report_with_receipt(
                        store=self.store,
                        pusher=self.feishu,
                        meta=prompts.REPORT_META.get(rtype, {}),
                        rec=existing,
                        rtype=rtype,
                        trigger="automatic",
                        config=self.config,
                    )
                    if pushed:
                        self.store.mark_pushed(rtype, existing["date"])
                        existing = self.store.get_report(rtype, trade_day) or existing
                else:
                    log.warning("报告 [%s] 无有效定稿校验回执，已阻止自动补推", rtype)
            return existing
        rcfg = self.config.section("report_retry")
        attempts = max(1, int(rcfg.get("attempts", 3)))
        backoff = max(0, int(rcfg.get("backoff_sec", 20)))
        last_error = ""
        for i in range(attempts):
            result = None
            errored = False
            async for event in self.generate_report_stream(rtype, push=push):
                if "error" in event:
                    last_error = event["error"]
                    errored = True
                    break
                if event.get("done"):
                    result = self.store.get_report(rtype, trade_day)
            if not errored:
                return result
            if i < attempts - 1:
                wait = backoff * (i + 1)
                log.warning("报告生成失败 [%s]（第 %d/%d 次）：%s；%ds 后重试",
                            rtype, i + 1, attempts, last_error, wait)
                await asyncio.sleep(wait)
                ex2 = self.store.get_report(rtype, trade_day)   # 退避期间若另一路径已落盘 → 直接用，不重复生成
                if ex2 and ex2.get("content_md"):
                    return ex2
        log.error("报告生成失败 [%s]，%d 次重试均未成功：%s", rtype, attempts, last_error)
        if self.feishu:
            await self.feishu.push_text(
                f"⚠️ {prompts.REPORT_META.get(rtype, {}).get('title', rtype)} 生成失败"
                f"（已重试{attempts}次）：{last_error}"
            )
        return None

    # ------------------------------------------------ 后台生成会话（切页不丢）

    async def _run_gen(self, rtype: str, push: bool, sess: _GenSession) -> None:
        """后台驱动 generate_report_stream 跑到底（含落盘），独立于任何订阅——订阅断开不取消它。"""
        try:
            trade_day = now_cn().strftime("%Y-%m-%d")
            async with self._report_lock(rtype, trade_day):
                existing = self.store.get_report(rtype, trade_day)
                if existing and existing.get("content_md"):
                    event = {
                        "done": True, "saved": True, "date": trade_day,
                        "pushed": bool(existing.get("pushed")), "existing": True,
                    }
                    sess.events.append(event)
                    for q in sess.subs:
                        q.put_nowait(event)
                else:
                    async for ev in self.generate_report_stream(rtype, push=push):
                        sess.events.append(ev)
                        for q in sess.subs:
                            q.put_nowait(ev)
        except Exception:  # noqa: BLE001 — 详情只写服务端日志
            log.exception("后台报告生成异常 [%s]", rtype)
            ev = _safe_stream_error("report_internal_failure")
            sess.events.append(ev)
            for q in sess.subs:
                q.put_nowait(ev)
        finally:
            sess.done = True
            for q in sess.subs:
                q.put_nowait(None)  # 结束哨兵

    async def generate_report_managed(self, rtype: str, push: bool = False):
        """SSE 订阅入口：后台任务独立生成+落盘，订阅(切页)断开不影响生成、报告照常保存。
        同 rtype 正在生成则复用同一会话(不重复跑)并回放已产生事件；上一轮已完成则起新一轮。"""
        if rtype not in prompts.REPORT_META:
            yield {"error": f"未知报告类型: {rtype}"}
            return
        # 当日定稿铁律：已有当日成稿 → 拒绝再生成（前端正常路径不会走到这——已定稿时不显示生成按钮；
        # 这里是 API 层兜底，防直接调接口把当日报告刷成另一个版本）。
        existing = self.store.get_report(rtype)
        if existing and existing.get("content_md"):
            yield {"error": "今日已定稿：每日报告当日只生成一次、以首版为准（明日到点自动生成新一期）"}
            return
        sess = self._gen_sessions.get(rtype)
        if sess is None or sess.done:
            sess = _GenSession()
            self._gen_sessions[rtype] = sess
            sess.task = asyncio.create_task(self._run_gen(rtype, push, sess))
        # 下面到 append 之间无 await → 相对事件循环原子，与 _run_gen 无回放/订阅竞态
        q: asyncio.Queue = asyncio.Queue()
        for ev in list(sess.events):      # 回放已缓冲事件（中途订阅也能看到前面的 delta）
            q.put_nowait(ev)
        if sess.done:
            q.put_nowait(None)
        else:
            sess.subs.append(q)
        try:
            while True:
                ev = await q.get()
                if ev is None:
                    break
                yield ev
        finally:
            if q in sess.subs:
                sess.subs.remove(q)

    # ------------------------------------------------ 驾驶舱 AI 叙述（T9 §1/§3）

    async def cockpit_narrate(self, prev: dict | None = None) -> dict:
        """驾驶舱「今日该怎么做+系统性风险」AI 叙述（T9 §1 单次调用·每 10 分钟档一次，score_lock 定格）。
        AI 只把注入的真实数字组织成【过程+结果】叙述（客户要知道"为啥这么做"）；风险等级/仓位/regime
        由确定性引擎(pulse 档快照)注入、AI 绝不改。失败返回 ok=False（score_lock 不落坏值，
        前端自动回退规则版文案——页面绝不空窗、绝不因 AI 挂掉丢驾驶舱）。prev=上一档叙述（观点惯性）。"""
        from . import cockpit as _ck  # noqa: PLC0415
        from . import score_lock as _sl  # noqa: PLC0415
        from ..data import market as _mkt  # noqa: PLC0415
        try:
            p = await _sl.locked(self.store.root, "pulse",
                                 lambda pv: asyncio.to_thread(_ck.pulse_core, self.store, pv))
            if not isinstance(p, dict) or p.get("ok") is False:
                return {"ok": False}
            ov = await asyncio.to_thread(_mkt.market_overview)
            fl = await asyncio.to_thread(_mkt.market_flow)
        except Exception:  # noqa: BLE001
            log.exception("驾驶舱叙述：数据采集失败")
            return {"ok": False}
        cmd = p.get("command") or {}
        yi = lambda v: f"{v / 1e8:.2f}亿元" if v is not None else "数据不全"  # noqa: E731 —— 单位换算在数据层做，AI 只照抄
        pc = lambda v: f"{v:+.2f}%" if v is not None else "数据不全"  # noqa: E731
        count = lambda v, unit: f"{v}{unit}" if v is not None else "数据不全"  # noqa: E731
        secs = fl.get("main_flow_sectors") or []
        inflow = sorted([s for s in secs if (s.get("main_net") or 0) > 0], key=lambda s: -s["main_net"])[:8]
        outflow = sorted([s for s in secs if (s.get("main_net") or 0) < 0], key=lambda s: s["main_net"])[:6]
        idx = {i.get("code"): i for i in (ov.get("indexes") or [])}
        lines = [
            "【市场状态·确定性引擎已定(不可改)】"
            f"状态={p.get('regime') or '数据不全'}，风险 {p.get('risk_level')} 级·{p.get('risk_label') or ''}，"
            f"仓位上限：{cmd.get('position_cap') or '数据不全'}，情绪温度 {p.get('senti')}({p.get('senti_label') or ''})",
            "【盘面读数】"
            f"上涨{count(ov.get('up_count'), '家')}/下跌{count(ov.get('down_count'), '家')}；"
            f"涨停{count(ov.get('limit_up'), '家')}/跌停{count(ov.get('limit_down'), '家')}(含ST)；"
            f"最高连板{count(ov.get('lianban_height'), '板')}；炸板率{count(ov.get('zhaban_rate'), '%')}；"
            f"两市成交{yi(ov.get('total_amount'))}",
            "【指数】上证" + pc((idx.get("sh000001") or {}).get("change_pct"))
            + "、深成" + pc((idx.get("sz399001") or {}).get("change_pct"))
            + "、创业板" + pc((idx.get("sz399006") or {}).get("change_pct")),
            "【行业主力净流入(从大到小)】" + ("；".join(
                f"{s['name']} 净流入{yi(s['main_net'])}、涨{pc(s.get('change_pct'))}" for s in inflow) or "数据不全"),
            "【行业主力净流出】" + ("；".join(
                f"{s['name']} 净流出{yi(abs(s['main_net']))}、{pc(s.get('change_pct'))}" for s in outflow) or "数据不全"),
            "【主线判定铁律】净流入只是补充证据，不是主线。主线必须由同日成交容量、"
            "相对强度、上涨宽度与涨停梯队四因子结构规则卡判定；本叙述未注入该结构结果，"
            "不得将净流入榜命名为主线，也不得据此给买入方向。",
        ]
        if cmd.get("blackswan"):
            lines.append("【黑天鹅信号】" + "；".join(cmd["blackswan"]))
        if fl.get("stale"):
            lines.append("【数据新鲜度】行业资金流为沿用值：" + str(fl.get("note") or ""))
        messages = prompts.build_cockpit_narrate("\n".join(lines), (prev or {}).get("定调", ""))
        try:
            j = await self.hermes.chat_json(
                messages,
                required_keys=("定调", "主线"),
                validator=lambda value: all(
                    isinstance(value.get(key), (str, int, float))
                    for key in ("定调", "主线")
                ),
                purpose="cockpit:narrative",
            )
        except Exception:  # noqa: BLE001
            log.warning("驾驶舱叙述：AI 网关或结构校验失败（回退规则版）")
            return {"ok": False}
        out = {k: str(j.get(k) or "") for k in ("定调", "主线", "回避", "盘面推理", "操作结论", "change")}
        out.update({"ok": True, "ts": ts_str()})
        return out

    # ------------------------------------------------ 对话上下文

    async def chat_context(self, mode: str, code: str = "", industry: str = "",
                           codes: list[str] | None = None) -> dict:
        today = now_cn().strftime("%Y-%m-%d")
        if mode == "stock" and code:
            # 五路并发、各有独立预算。公开财务/F10 是增强项，不能再排在盘口前面
            # 串行阻塞模型首字；stock_detail 本身已是 5 秒内可部分成功的快照。
            from ..data import free_source as _tx  # noqa: PLC0415
            detail, sectors, fina, sentiment, overview = await asyncio.gather(
                _bounded_thread_call(stock.stock_detail, code, timeout=6.0, default={}),
                _bounded_thread_call(market.market_sectors, timeout=3.0, default={}),
                _bounded_thread_call(_tx.fina_indicator, code, timeout=2.0, default=None),
                _bounded_thread_call(senti.compute_today, self.store, timeout=3.0, default={}),
                _bounded_thread_call(market.market_overview, timeout=3.0, default={}),
            )
            detail = detail if isinstance(detail, dict) else {}
            minute = detail.get("minute", [])
            if len(minute) > 90:
                sampled = minute[::5]
                tail = minute[-30:]
                seen = {m["t"] for m in sampled}
                detail["minute"] = sorted(
                    sampled + [m for m in tail if m["t"] not in seen],
                    key=lambda m: m["t"],
                )
            detail["daily"] = _trim(detail.get("daily", []), 60)
            industry_name = detail.get("board", {}).get("industry", "")
            related = [
                sector for sector in (sectors.get("sectors", []) if isinstance(sectors, dict) else [])
                if industry_name and industry_name in sector.get("name", "")
            ]
            return {
                "今日日期": today,
                "个股数据": detail,
                "基本面财务(近报告期)": fina or "本轮未取得财务增强，基本面维度不参与结论",
                "所属板块今日表现": related or "本轮未匹配到板块增强，不影响盘口与量价分析",
                "市场情绪": sentiment,
                "市场总览": overview,
            }

        if mode == "company" and code:
            # 单公司深度研究：尽可能 gather 更丰富的真实上下文（仿 stock 分支，再叠加大盘资金/情绪/龙虎/北向）。
            # 龙虎/北向为盘后接口，须在线程池里同步采集；统一兜底「拿不到→标 missing」，绝不编造（数据真实性铁律）。
            from ..data import mainforce_source as mainforce  # noqa: PLC0415 局部导入，避免顶层循环依赖

            def gather_disclosures():
                if not code.startswith(("0", "3", "6")):
                    return {
                        "state": "not_supported",
                        "verification_state": "not_supported",
                        "data_as_of": None,
                        "fetched_at": None,
                        "degraded_reasons": ["当前官方公告 adapter 仅覆盖沪深 A 股"],
                        "items": [],
                    }
                disclosure_end = now_cn().date()
                disclosure_start = disclosure_end - timedelta(days=90)
                disclosure_cache = (
                    Path(self.config.data_dir)
                    / "official-disclosures"
                    / f"{code}.json"
                )
                try:
                    return official_disclosure.cached_official_disclosures(
                        code,
                        disclosure_start.isoformat(),
                        disclosure_end.isoformat(),
                        12,
                        False,
                        1,
                        str(disclosure_cache),
                    )
                except Exception as exc:  # noqa: BLE001 - 公告增强不能拖垮公司研究
                    log.warning("公司研究公告证据降级: %s", type(exc).__name__)
                    return {
                        "state": "missing",
                        "verification_state": "unavailable",
                        "data_as_of": None,
                        "fetched_at": None,
                        "degraded_reasons": ["官方公告增强当前不可用"],
                        "items": [],
                    }

            def gather_interactions():
                if not code.startswith(("0", "3")):
                    return {
                        "state": "not_supported",
                        "data_as_of": None,
                        "fetched_at": None,
                        "is_formal_disclosure": False,
                        "degraded_reasons": ["当前仅覆盖深市互动易；沪市/北交所互动平台尚未接入"],
                        "items": [],
                    }
                interaction_end = now_cn().date()
                interaction_start = interaction_end - timedelta(days=90)
                try:
                    return official_interaction.cached_official_interactions(
                        code,
                        interaction_start.isoformat(),
                        interaction_end.isoformat(),
                        6,
                        str(self.config.data_dir),
                    )
                except Exception as exc:  # noqa: BLE001 - Tier 3 增强不得拖垮公司研究
                    log.warning("公司研究互动问答降级: %s", type(exc).__name__)
                    return {
                        "state": "missing",
                        "data_as_of": None,
                        "fetched_at": None,
                        "is_formal_disclosure": False,
                        "degraded_reasons": ["交易所互动问答增强当前不可用"],
                        "items": [],
                    }

            def gather():
                detail = stock.stock_detail(code)
                # 分时同 stock：每5分钟采样 + 最近30分钟全量，按时间重排控制 token 又不让模型读反尾盘量价
                minute = detail.get("minute", [])
                if len(minute) > 90:
                    sampled = minute[::5]
                    tail = minute[-30:]
                    seen = {m["t"] for m in sampled}
                    detail["minute"] = sorted(
                        sampled + [m for m in tail if m["t"] not in seen],
                        key=lambda m: m["t"],
                    )
                # 日K 给到 120 根（比盘中问股的 60 更长，支撑趋势位置/中长线节奏判断）
                detail["daily"] = _trim(detail.get("daily", []), 120)
                technical = diagnosis_mod._deep_technical_analysis(
                    detail.get("quote") or {},
                    detail.get("daily") or [],
                    detail.get("flow") if isinstance(detail.get("flow"), dict) else None,
                    detail.get("board") or {},
                )

                industry_name = detail.get("board", {}).get("industry", "")
                sectors = market.market_sectors()
                related = [
                    s for s in sectors.get("sectors", [])
                    if industry_name and industry_name in s.get("name", "")
                ]

                # 龙虎榜：过滤本 code 上榜记录（个股榜 + 机构席位榜），都没有 → 明确「近期未上龙虎榜」
                lh = mainforce.longhu()
                lh_self_list = [r for r in lh.get("list", []) if r.get("code") == code]
                lh_self_inst = [r for r in lh.get("inst", []) if r.get("code") == code]
                if lh_self_list or lh_self_inst:
                    longhu_self = {
                        "data_date": lh.get("data_date"),
                        "个股上榜": lh_self_list or "本榜未见该股",
                        "机构席位净买": lh_self_inst or "无机构专用席位",
                        "missing": lh.get("missing", []),
                    }
                else:
                    longhu_self = (
                        f"该股近期未上龙虎榜（最近交易日 {lh.get('data_date') or '未知'} 龙虎榜中无此代码）"
                        if lh.get("list") or lh.get("inst")
                        else "龙虎榜数据不全（未取到当日龙虎榜，无法判断是否上榜）"
                    )

                # 基本面财务：TDX stock_info 主源、新浪公开财务备用。
                # 供应方只返回部分指标时按缺失处理，不推断未披露字段。
                from ..data import free_source as _tx  # noqa: PLC0415
                fina = _tx.fina_indicator(code)
                research_enrichment = tdx_api_source.after_hours_research(code)
                return {
                    "今日日期": today,
                    "个股代码": code,
                    "个股数据": detail,  # 含 quote/分时/日K/board.industry/flow 资金（单位见各字段，价/额/市值=元，幅/换手=%）
                    "确定性技术分析(唯一价位依据)": technical,
                    "基本面财务(近一期·TDX主/新浪备)": fina or "数据不全（未取到财务，基本面/估值段少下定论、勿臆造）",
                    "所属行业今日表现": related or "未匹配到所属板块行情（数据不全）",
                    "全市场资金方向": market.market_flow(),
                    "市场总览": market.market_overview(),
                    "市场情绪": senti.compute_today(self.store),
                    "本股龙虎榜": longhu_self,
                    "盘后研究增强(TDX低频源·不参与日内评分)": research_enrichment,
                    # 北向资金已移除：其净流入实时数据自 2024-08 停止披露，旧源(moneyflow_hsgt)回的是
                    # 沪深港通【成交额】口径(数千亿),当净流入喂 AI 会编造"北向大幅流入"看多结论(铁律红线)。
                    # 关键：明确告知 AI 哪些维度【无确定性数据源】，使其在报告中标「数据不全」而非编造（铁律）。
                    "数据缺口(无数据源·报告须如实标注不全·绝不编造)": [
                        "财务【摘要指标已注入】见『基本面财务』段(ROE/毛利率/净利同比/营收同比/负债率,近报告期)——"
                        "基本面与估值段用它分析；仅当该段为「数据不全」时才写财务不全。更细的年报明细"
                        "(分产品营收/现金流量表/单季拆分)未接入,如需只能定性说明,严禁臆造具体数字。",
                        "产业链上下游结构（上游供应商/下游客户/原材料/产能/订单可见性）：系统无产业链结构数据源，"
                        "只能基于公开产业常识做定性推演，且须明确区分「常识推演」与「数据支撑」，公司名务必真实、不确定的标「待核验」。",
                        "宏观（美债收益率/美元指数/汇率/海外利率）：本次未注入任何宏观数字，"
                        "如需提及只能定性，严禁编造点位/收益率数字。",
                        "市盈率/市净率/市值：仅当『个股数据.quote』中 pe/pb/total_mv 非空时方可引用其原值；"
                        "为空（— / null）即数据不全，不得估算。",
                    ],
                }

            # 官方公告与互动问答是独立低频增强，与公司行情/财务主上下文并行。
            # 主上下文完成后两项合计只额外等 0.5s；官网慢时明确降级，不阻塞 AI 首字。
            base_task = asyncio.create_task(asyncio.to_thread(gather))
            disclosure_task = asyncio.create_task(asyncio.to_thread(gather_disclosures))
            interaction_task = asyncio.create_task(asyncio.to_thread(gather_interactions))
            context = await base_task
            done, pending = await asyncio.wait(
                {disclosure_task, interaction_task}, timeout=0.5
            )
            for task in pending:
                task.cancel()
            if disclosure_task in done:
                disclosures = disclosure_task.result()
            else:
                disclosures = {
                    "state": "degraded",
                    "verification_state": "pending_async_refresh",
                    "data_as_of": None,
                    "fetched_at": None,
                    "degraded_reasons": ["官方公告查询超过研究首字等待预算，本次未注入"],
                    "items": [],
                }
            if interaction_task in done:
                interactions = interaction_task.result()
            else:
                interactions = {
                    "state": "degraded",
                    "data_as_of": None,
                    "fetched_at": None,
                    "is_formal_disclosure": False,
                    "degraded_reasons": ["互动问答查询超过研究首字等待预算，本次未注入"],
                    "items": [],
                }
            context["官方公告证据(近90日·不参与日内评分)"] = {
                "state": disclosures.get("state"),
                "verification_state": disclosures.get("verification_state"),
                "data_as_of": disclosures.get("data_as_of"),
                "fetched_at": disclosures.get("fetched_at"),
                "degraded_reasons": disclosures.get("degraded_reasons", []),
                "items": [
                    {
                        "announcement_date": item.get("announcement_date"),
                        "published_at": item.get("published_at"),
                        "title": item.get("title"),
                        "original_url": item.get("original_url"),
                        "source_confirmations": item.get("source_confirmations", []),
                        "verification": (
                            "cross_confirmed"
                            if len(item.get("source_confirmations", [])) >= 2
                            else "single_source"
                        ),
                    }
                    for item in disclosures.get("items", [])[:12]
                ],
                "usage_note": (
                    "仅 source_confirmations 同时含巨潮与交易所的条目，"
                    "其标题和日期才可称为已交叉确认；单源条目仅作待核线索。"
                    "未注入 PDF 全文，不得臆造公告内的金额、条款或管理层意图。"
                ),
            }
            context["交易所互动问答(近90日·Tier3·不参与日内评分)"] = {
                "state": interactions.get("state"),
                "data_as_of": interactions.get("data_as_of"),
                "fetched_at": interactions.get("fetched_at"),
                "is_formal_disclosure": False,
                "degraded_reasons": interactions.get("degraded_reasons", []),
                "items": [
                    {
                        "question": item.get("question"),
                        "question_pub_date": item.get("question_pub_date"),
                        "answered": bool(item.get("answered")),
                        "answer": item.get("answer") if item.get("answered") else None,
                        "answer_pub_date": item.get("answer_pub_date") if item.get("answered") else None,
                        "source_type": "管理层回复" if item.get("answered") else "未回复的投资者提问",
                    }
                    for item in interactions.get("items", [])[:6]
                ],
                "usage_note": (
                    "该数据来自深交所互动易官网页面，证据等级低于法定公告。"
                    "只有 answered=true 且 answer 非空的内容可称管理层回复；未回复提问不能当公司表态。"
                    "回复内容仅作研究线索，不得替代正式公告，不得进入日内主线或风险评分。"
                ),
            }
            return context

        if mode == "position":
            holdings = self.store.holdings()
            # 勾选子集：只诊断选中的票（前端持仓页勾选带入）；过滤后为空则回退全部(防御)
            if codes:
                want = {str(c).strip().zfill(6) for c in codes}
                picked = [h for h in holdings if str(h.get("code", "")).strip().zfill(6) in want]
                holdings = picked or holdings

            def gather():
                quotes = []
                for h in holdings[:20]:
                    q = stock.quick_quote(h["code"])
                    quotes.append({**h, "实时行情": q or "数据不全"})
                return {
                    "今日日期": today,
                    "用户持仓": quotes,
                    "市场情绪": senti.compute_today(self.store),
                    "市场总览": market.market_overview(),
                    "板块行情": {"sectors": _trim(market.market_sectors().get("sectors", []), 40)},
                }

            return await asyncio.to_thread(gather)

        if mode == "chain":
            return {
                "今日日期": today,
                "产业链结构化分析": await self.industry_chain_analysis(industry),
            }

        if mode == "restructuring":
            # 重组事件拆解：公告/材料原文在用户消息里（唯一事件事实源，交易条款/标的财务只认它）；
            # 行情底座可选——给了 code 才注入 stock_detail 真实行情（供估值块的现价/市值/PE/PB），
            # 不给 code 就纯按材料推演（prompt 强制 AI 如实标注「无行情底座」，绝不编行情）。
            if not code:
                return {
                    "今日日期": today,
                    "行情底座": "未提供股票代码——本次无实时行情/市值/PE 注入，估值块只能按材料口径推演并如实标注",
                }

            def gather():
                detail = stock.stock_detail(code)
                # 事件驱动非日内：分时清空省 token；日K 留 30 根供位置感（是否已炒作透支）。
                detail["minute"] = []
                detail["daily"] = _trim(detail.get("daily", []), 30)
                sectors = market.market_sectors()
                industry_name = detail.get("board", {}).get("industry", "")
                related = [
                    s for s in sectors.get("sectors", [])
                    if industry_name and industry_name in s.get("name", "")
                ]
                return {
                    "今日日期": today,
                    "个股数据": detail,  # quote 的现价/市值/pe/pb 供估值底座；缺失即「数据不全」
                    "所属板块今日表现": related or "未匹配到板块数据（数据不全）",
                }

            return await asyncio.to_thread(gather)

        # free
        return await self._market_context(full=False)

    async def industry_chain_analysis(self, industry: str) -> dict:
        """研究中心与问股 chain 模式共用的冻结结构化分析。"""
        return await industry_chain_mod.collect_analysis(industry)

    async def governed_chain_explanation_stream(
        self,
        message: str,
        analysis: dict,
        *,
        history: list[dict] | None = None,
        images: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """冻结证据，允许模型以公开产业常识完成八段定性研究。"""
        allowed_codes = [
            str(row.get("code") or "")
            for row in (analysis.get("company_mapping") or [])
            if row.get("verification") == "tdx_sector_member"
        ]
        candidate_pool = [
            {
                "code": row.get("code"),
                "name": row.get("name"),
                "verification": row.get("verification"),
                "matched_sectors": row.get("matched_sectors"),
                "chain_role": row.get("chain_role"),
                "quote": row.get("quote"),
                "valuation": row.get("valuation"),
                "realization": row.get("realization"),
                "missing": row.get("missing"),
            }
            for row in sorted(
                (analysis.get("company_mapping") or []),
                key=lambda item: str(item.get("code") or ""),
            )
        ]
        evidence_base = {
            "industry": analysis.get("industry"),
            "data_as_of": analysis.get("data_as_of"),
            "matched_sectors": analysis.get("matched_sectors"),
            "sector_market": analysis.get("sector_market"),
            # 故意按代码排序并移除数据可研分/核验序，避免模型把成交额
            # 或字段覆盖顺序偷换成产业研究优先级。
            "company_candidate_pool": candidate_pool,
            "candidate_pool_order": "code_ascending_not_research_rank",
            "coverage": analysis.get("coverage"),
            "evidence": analysis.get("evidence"),
            "data_gaps": analysis.get("data_gaps"),
            "research_trade_separation": analysis.get("research_trade_separation"),
        }
        required_headings = (
            "### 1. 需求本质",
            "### 2. 产业地图",
            "### 3. 价值捕获",
            "### 4. 卡点",
            "### 5. 国产替代",
            "### 6. 公司映射",
            "### 7. 兑现与估值",
            "### 8. 研究优先级与失效条件",
        )
        system = (
            prompts.system_prompt(self.store.knowledge_digest())
            + "\n\n【产业链研究·证据冻结与定性推演边界】"
            "下方只冻结 TDX 候选池、行情、来源、时点与缺口。你可以基于广泛公开的产业常识"
            "完成有内容的定性推演，但每个产业常识判断都必须显式标注"
            "『[public_knowledge / 待公告核验]』，不能伪装成数据源事实，也不得编造任何份额、国产化率、"
            "订单、产能、价格、客户、收入、利润或估值数字。"
            f"允许点名的 A 股代码仅限：{allowed_codes or ['无']}。"
            "候选池外公司一律不得点名；候选池内公司的上中下游角色也只能作"
            "『[public_knowledge / 待公告核验]』映射。TDX 成分只证明集合关系，不证明业务角色或投资价值。"
            "company_candidate_pool 已按代码排序，绝不是产业、投资或交易优先级。"
            "第8段可以提出『研究建议优先级』，但必须逐项说明定性产业依据与待核证据，"
            "不得按当日涨跌幅、成交额或数据可研分排序，不得写成确定性核心标的排名。"
            "产业研究与交易时点严格分离，不得生成目标价、仓位或即时买卖指令。"
            "严格使用八个三级标题且顺序固定："
            + "；".join(required_headings)
            + "。每段先给定性判断，再写依据类型和下一步核验；全文不超过1800个中文字。"
            "结尾必须另列『数据截至/来源覆盖/证据缺口』，并再次单列"
            "『产业结论』与『当前交易时点：未评估』。"
            "\n\n【冻结产业链证据底座 JSON】\n"
            + json.dumps(evidence_base, ensure_ascii=False, default=str)
        )
        recent = [
            {"role": row.get("role"), "content": str(row.get("content") or "")[:1200]}
            for row in (history or [])[-4:]
            if row.get("role") in {"user", "assistant"} and row.get("content")
        ]
        user_content: str | list[dict] = str(message or "")[:3000]
        safe_images = [
            url for url in (images or [])
            if isinstance(url, str) and url.startswith("data:image/")
        ][:4]
        if safe_images:
            user_content = [
                {"type": "text", "text": str(message or "")[:3000]},
                *[
                    {"type": "image_url", "image_url": {"url": url}}
                    for url in safe_images
                ],
            ]
        messages = [
            {"role": "system", "content": system},
            *recent,
            {"role": "user", "content": user_content},
        ]
        chunks: list[str] = []
        received = 0
        async for delta in self.hermes.chat_stream(
            messages,
            purpose="chat:chain-governed-explanation",
        ):
            text = str(delta or "")
            if not text or received >= 7000:
                continue
            part = text[:7000 - received]
            received += len(part)
            chunks.append(part)
        content = "".join(chunks).strip()
        if not content:
            return
        missing_headings = [heading for heading in required_headings if heading not in content]
        unknown_codes = {
            code
            for code in re.findall(r"(?<!\d)\d{6}(?!\d)", content)
            if code not in allowed_codes
        }
        forbidden_trade = re.search(
            r"立即买入|直接买入|建议买入|买入区间|目标价|建议仓位|立即卖出|建议卖出",
            content,
        )
        if missing_headings or unknown_codes or forbidden_trade:
            log.warning(
                "产业链模型解释未通过冻结边界 headings=%s unknown_codes=%s forbidden_trade=%s",
                missing_headings,
                sorted(unknown_codes),
                bool(forbidden_trade),
            )
            return
        if "public_knowledge" not in content or "待公告核验" not in content:
            log.warning("产业链模型解释缺少公开常识验证标签")
            return
        yield content

    async def governed_stock_explanation_stream(
        self, message: str, card: dict, *, history: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """在冻结的确定性结论边界内回答用户的具体问题。

        该通道不向模型开放 action/仓位/价位改写权；确定性快答已先向
        前端输出，因此模型或增强源慢/失败也不会让问股变成空白页。
        """
        code = str(card.get("code") or "").strip()
        if len(code) != 6 or not code.isdigit():
            return
        context = await self.chat_context("stock", code=code)
        frozen = {
            "code": code,
            "name": card.get("name") or (card.get("market_quote") or {}).get("name"),
            "final_action": card.get("action"),
            "signal_action": card.get("signal_action"),
            "entry_condition": (card.get("trade_plan") or {}).get("entry_trigger"),
            "invalidation_condition": (card.get("trade_plan") or {}).get("stop"),
            "summary": card.get("summary"),
            "evidence": card.get("evidence") or [],
        }
        system = (
            prompts.system_prompt(self.store.knowledge_digest())
            + "\n\n【盘中问股·已治理解释】"
            "用户的问题必须直接回答，不要重复一张治理卡，不要讲系统内部流程。"
            "下方冻结结论中的 final_action、entry_condition、invalidation_condition 是确定性风控结果，"
            "你只能解释，不得改写，不得再生成任何新价位、新仓位或无条件买卖建议。"
            "只引用注入数据；某维度缺失就写暂不可用，不要把增强源缺失说成股票不存在。"
            "输出不超过 450 个中文字，最多 6 个要点，先回答用户这句话，再给原因和条件。"
            + prompts._inject({"冻结结论": frozen, "实时盘面与增强数据": context})
        )
        recent = [
            {"role": row.get("role"), "content": str(row.get("content") or "")[:1200]}
            for row in (history or [])[-4:]
            if row.get("role") in {"user", "assistant"} and row.get("content")
        ]
        messages = [{"role": "system", "content": system}, *recent, {
            "role": "user", "content": str(message or "")[:3000],
        }]
        sent = 0
        async for delta in self.hermes.chat_stream(
            messages, purpose="chat:stock-governed-explanation"
        ):
            text = str(delta or "")
            if not text or sent >= 2200:
                continue
            part = text[:2200 - sent]
            sent += len(part)
            yield part

    async def chat_stream(
        self, mode: str, message: str, code: str = "", industry: str = "",
        history: list[dict] | None = None, images: list[str] | None = None,
        codes: list[str] | None = None,
    ) -> AsyncIterator[dict]:
        if mode == "free" and not code:
            stock_reference = resolve_free_stock_reference(message)
            if stock_reference["state"] == "matched":
                mode = "stock"
                code = str(stock_reference["code"])
            elif stock_reference["state"] in {"ambiguous", "unrecognized"}:
                yield {"stage": "正在核验提问中的股票标的…"}
                yield {"delta": stock_reference_clarification(stock_reference)}
                yield {"done": True, "truncated": False}
                return
        # 分阶段等待事件（首个 delta 前）：前端据此显示"正在做什么 + 已等待秒数"占位气泡
        yield {"stage": "采集实时行情数据…"}
        try:
            ctx = await self.chat_context(mode, code, industry, codes=codes)
        except Exception:  # noqa: BLE001 — 详情只写服务端日志
            log.exception("问答上下文采集失败")
            yield _safe_stream_error("chat_context_failed")
            return
        try:
            digest = self.store.knowledge_digest()
            messages = prompts.build_chat_messages(mode, message, ctx, history, digest, images=images)
            # 方法论技能层：追加到 system 末尾（同上，安全降级为空串 no-op）。
            _m = load_methodology()
            if _m and messages and messages[0].get("role") == "system":
                messages[0]["content"] += _m
        except Exception:  # noqa: BLE001 — 详情只写服务端日志
            log.exception("问答生成准备异常")
            yield _safe_stream_error("chat_internal_failure")
            return
        # 重组成稿「重复病」流式防线：个别模型会把整份报告原样再写一遍（speedle 实测同款）。
        # 该模式主标题由 prompt 固定为「# 重组事件深度拆解」，主标题第二次出现=复读开始→从该处截停。
        # 复读标题常**粘在行中间**，故不锚行首、只认「# +标题」子串；下发留 guard 尾缓冲防跨 chunk 漏检。
        guard = "# 重组事件深度拆解" if mode == "restructuring" else ""
        guard_tail = len(guard) + 160   # 留尾=guard+160字：防标题跨 chunk 漏检 + 截停时回收粘连残句
        output_cap = 6000 if mode == "chain" else None
        company_market_closed = False
        if mode == "company":
            current_cn = now_cn()
            current_hhmm = current_cn.hour * 100 + current_cn.minute
            company_market_closed = (
                not await asyncio.to_thread(is_trade_day)
                or current_hhmm < 915
                or current_hhmm > 1500
            )
        # HermesClient 已统一处理首字节前的网络/空响应重试。这里保留可配置的
        # 场景级重试，但默认只执行一轮，避免客户端 3 次 × 场景 2 次的重复放大；
        # 吐了一半才断仍绝不重试。
        try:
            rcfg = self.config.section("chat_retry")
            attempts = max(1, min(int(rcfg.get("attempts", 1)), 2))
            backoff = max(0, int(rcfg.get("backoff_sec", 3)))
        except Exception:  # noqa: BLE001 — 详情只写服务端日志
            log.exception("问答重试配置解析异常")
            yield _safe_stream_error("chat_internal_failure")
            return
        for attempt in range(attempts):
            yield {"stage": "Hermes 生成中…" if attempt == 0 else f"上游繁忙，正在自动重试（{attempt + 1}）…"}
            stream_meta: dict = {}
            acc = ""       # guard 用：已接收全文
            sent = 0       # guard 用：已下发字符数
            plain_sent = 0  # 非 guard 模式的硬上限计数
            company_parts: list[str] = []
            emitted = False  # 是否已向前端吐过 delta —— 吐过就不再重试，避免重复
            try:
                async for delta in self.hermes.chat_stream(
                    messages, meta=stream_meta, purpose=f"chat:{mode}"
                ):
                    if not guard:
                        text = str(delta or "")
                        if mode == "company":
                            if text:
                                company_parts.append(text)
                            continue
                        if output_cap is not None:
                            remaining = output_cap - plain_sent
                            if remaining <= 0:
                                yield {"done": True, "truncated": True}
                                return
                            text = text[:remaining]
                            plain_sent += len(text)
                        if not text:
                            continue
                        emitted = True
                        yield {"delta": text}
                        if output_cap is not None and plain_sent >= output_cap:
                            yield {"done": True, "truncated": True}
                            return
                        continue
                    acc += delta
                    first = acc.find(guard)
                    second = acc.find(guard, first + 1) if first != -1 else -1
                    if second != -1:
                        if second > sent:
                            seg = acc[sent:second]
                            nl = seg.rfind("\n")   # 丢弃粘在标题前的模型弃写残句（≤一行）
                            seg = seg[:nl] if nl != -1 else ""
                            if seg:
                                emitted = True
                                yield {"delta": seg}
                        yield {"done": True, "truncated": False}
                        return
                    safe = len(acc) - guard_tail
                    if safe > sent:
                        emitted = True
                        yield {"delta": acc[sent:safe]}
                        sent = safe
            except Exception:  # noqa: BLE001 — 上游异常可含密钥/URL/路径
                log.exception("问答 AI 生成流异常（第 %d/%d 次）", attempt + 1, attempts)
                if not emitted and not acc.strip() and attempt < attempts - 1:
                    await asyncio.sleep(backoff * (attempt + 1))
                    continue          # 空手而归的失败 → 重试（没吐过内容，安全）
                yield _safe_stream_error("chat_generation_failed")
                return
            # 上游接了但零内容 = 客户那个「模型未返回内容」→ 未吐过就重试
            if not emitted and not acc.strip():
                if mode == "company" and company_parts:
                    company_content = _sanitize_company_report(
                        "".join(company_parts),
                        ctx,
                        market_closed=company_market_closed,
                    )
                    if company_content:
                        emitted = True
                        yield {"delta": company_content}
                if emitted:
                    truncated = stream_meta.get("finish_reason") == "length"
                    if truncated:
                        yield {
                            "delta": "\n\n> ⚠️ **回答因长度限制被截断，后段缺失，可清空历史后重新提问。**"
                        }
                    yield {"done": True, "truncated": truncated}
                    return
                if attempt < attempts - 1:
                    await asyncio.sleep(backoff * (attempt + 1))
                    continue
                yield {"error": "模型多次返回空内容（上游限流或瞬断），已自动重试仍失败，请稍后再试。"}
                return
            # 有内容 → 正常收尾
            if guard and len(acc) > sent:
                yield {"delta": acc[sent:]}   # 无复读：补发留尾缓冲
            # max_tokens 截断检测：命中 length 回答会半句戛然而止，显著标注避免误当"说完了"。
            truncated = stream_meta.get("finish_reason") == "length"
            if truncated:
                yield {"delta": "\n\n> ⚠️ **回答因长度限制被截断，后段缺失，可清空历史后重新提问。**"}
            yield {"done": True, "truncated": truncated}
            return

    # ------------------------------------------------ DIY 研究工作流

    async def diy_run_stream(
        self, prompt_template: str, target_type: str = "free",
        code: str = "", theme: str = "",
    ) -> AsyncIterator[dict]:
        """DIY：用户自定义提示词模板 + 按 target_type 注入真实数据 → Hermes 流式。"""
        tpl = (prompt_template or "").strip()
        if not tpl:
            yield {"error": "提示词模板为空"}
            return
        user_prompt = tpl.replace("{code}", code or "").replace("{theme}", theme or "")
        # 复用 chat_context 的真实数据注入：stock→个股盘口；theme→相关板块；free→市场快照
        mode = "stock" if target_type == "stock" else ("chain" if target_type == "theme" else "free")
        yield {"stage": "采集数据…"}
        try:
            ctx = await self.chat_context(mode, code=code, industry=theme)
        except Exception:  # noqa: BLE001 — 详情只写服务端日志
            log.exception("DIY 上下文采集失败")
            yield _safe_stream_error("diy_context_failed")
            return
        try:
            digest = self.store.knowledge_digest()
            _method = load_methodology()  # 蒸馏方法论体系（目录缺失/为空时为空串，拼接即 no-op）
            sys = prompts.system_prompt(digest) + (
                "\n\n【DIY 自定义研究】用户用自定义提示词研究特定标的/题材，下方注入了对应的真实数据。"
                "严格基于注入数据回答，缺失的标注『数据不全』，不得编造。"
                + prompts._inject(ctx)
                + _method
            )
            messages = [{"role": "system", "content": sys}, {"role": "user", "content": user_prompt}]
        except Exception:  # noqa: BLE001 — 详情只写服务端日志
            log.exception("DIY 研究准备异常")
            yield _safe_stream_error("diy_internal_failure")
            return
        yield {"stage": "Hermes 生成中…"}
        stream_meta: dict = {}
        try:
            async for delta in self.hermes.chat_stream(
                messages, meta=stream_meta, purpose=f"diy:{target_type}"
            ):
                yield {"delta": delta}
        except Exception:  # noqa: BLE001 — 上游异常可含密钥/URL/路径
            log.exception("DIY AI 生成流异常")
            yield _safe_stream_error("diy_generation_failed")
            return
        # DIY 产业链分析的权重表/压力测试/风险段在尾部，被 length 截断恰好砍掉风控内容，须标注。
        truncated = stream_meta.get("finish_reason") == "length"
        if truncated:
            yield {"delta": "\n\n> ⚠️ **回答因长度限制被截断，后段（可能含权重表/压力测试/风险段）缺失，请缩短模板或分次研究。**"}
        yield {"done": True, "truncated": truncated}

    # ------------------------------------------------ 存档

    async def archive_today(self, push: bool = True) -> dict:
        # 情绪快照仅在交易日 09:25 后落盘；若当日数据源故障从未落盘，sentiment_history
        # 末条会是上一交易日数据。校验其 date 等于今天，否则视为今日无快照（留空），
        # 避免把陈旧情绪评分当今日数据存档/推送——拿不到就标缺失。
        today = now_cn().strftime("%Y-%m-%d")
        today_sent = self.store.sentiment_history(1)
        sentiment = (
            today_sent[-1] if today_sent and today_sent[-1].get("date") == today else {}
        )
        # M12 每日复盘自动汇总进知识库（config 开关，默认关 → 其它客户行为不变）。
        # 放在 archive_day 之前：自动沉淀的知识也随当日存档落盘（knowledge_today 会包含它）。
        if getattr(self.config, "knowledge_auto_summarize", False):
            await self._auto_summarize_day()
        bundle = self.store.archive_day(sentiment=sentiment)
        if push and self.feishu:
            titles = [v.get("title", k) for k, v in bundle["reports"].items()]
            await self.feishu.push_text(
                f"🗄️ {bundle['date']} 已自动存档\n"
                f"包含：{('、'.join(titles)) if titles else '（今日无报告）'}\n"
                f"情绪评分：{sentiment.get('score', '—')}（{sentiment.get('phase', '—')}）\n"
                f"可在可视化界面「存档回看」按日期回溯。"
            )
        return bundle

    async def _auto_summarize_day(self) -> None:
        """M12 每日复盘自动汇总：把当日【收盘复盘 + 次日策略】压缩成 2-4 条可长期复用的交易知识，
        只写入个人知识库待审区（draft）。自动总结没有用户批准权，因此绝不会被
        knowledge_digest 注入后续问答/研报；必须先通过 KnowledgeRule 审批门槛。
        best-effort：无复盘可汇总则跳过；AI/解析任何失败都吞掉，绝不阻断 22:00 存档主流程。
        仅在 config.knowledge_auto_summarize 开启时被调用（默认关，不影响其它客户）。"""
        close = self.store.get_report("close")
        evening = self.store.get_report("evening")
        src = ""
        if close and close.get("content_md"):
            src += "【收盘复盘】\n" + close["content_md"][:3500] + "\n\n"
        if evening and evening.get("content_md"):
            src += "【次日策略】\n" + evening["content_md"][:2500]
        if not src.strip():
            return  # 当日无复盘/策略，无可汇总
        today = now_cn().strftime("%Y-%m-%d")
        source_report_ids = []
        if close and close.get("content_md"):
            source_report_ids.append(f"report:close:{close.get('date') or today}")
        if evening and evening.get("content_md"):
            source_report_ids.append(f"report:evening:{evening.get('date') or today}")
        sys_prompt = (
            "你是交易复盘助手。把下面这份当日复盘/策略压缩成 2-4 条【可长期复用的交易知识】。"
            "每条占一行，严格用『类别|标题|要点』三段（竖线分隔），类别只能从 "
            "[交易原则,亏损禁区,仓位规则,选股偏好,复盘笔记] 里选。只输出这几行、不要任何解释。"
            "只基于给定文本提炼，绝不编造数字或个股名。"
        )
        try:
            raw = await self.hermes.chat(
                [{"role": "system", "content": sys_prompt}, {"role": "user", "content": src}],
                temperature=0.3, max_tokens=600, purpose="knowledge:auto_summary",
            )
        except Exception as e:  # noqa: BLE001 — 汇总是增量沉淀，失败不影响存档
            log.warning("每日复盘自动汇总 AI 调用失败: %s", e)
            return
        added = 0
        for line in (raw or "").splitlines():
            parts = [p.strip() for p in line.strip().lstrip("-•*").split("|")]
            if len(parts) >= 3 and parts[1] and parts[2]:
                try:
                    self.store.add_knowledge(
                        parts[0],
                        f"[自动·{today}] {parts[1]}"[:120],
                        parts[2],
                        status="draft",
                        origin="auto_summary",
                        source_report_ids=source_report_ids,
                        sample_count=len(source_report_ids),
                        created_by="system:auto_summary",
                    )
                    added += 1
                except Exception:  # noqa: BLE001 — 单条写入失败跳过
                    continue
            if added >= 4:
                break
        if added:
            log.info("每日复盘自动汇总：新增 %d 条 draft 进知识待审区", added)
