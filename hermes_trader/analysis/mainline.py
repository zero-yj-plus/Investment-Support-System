"""当日行业主线的确定性市场结构规则。

成交额和涨停家数仍是主线的容量/热度硬门槛，但成交额放大也可能来自
恐慌抛售，不能脱离板块方向与上涨宽度单独判主线。当前模型同时使用：

- 容量：行业成交额横截面排名；
- 方向：行业指数涨跌幅及相对强度；
- 宽度：行业成分股上涨家数占比；
- 热度：涨停家数、最高连板与板位梯队完整度。

主力净流入仅作返回给前端的补充证据，权重恒为 0。行业映射只做
Unicode/空白标准化后的等值匹配，不做包含、别名或题材文本猜测。

本模块的计算函数无 IO、不修改入参，便于用固定样本做契约测试。
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any


AMOUNT_TOP_RATIO = 0.20
SECONDARY_AMOUNT_TOP_RATIO = 0.35
MIN_LIMIT_UP_COUNT = 2
MAINLINE_MIN_BREADTH = 0.55
SECONDARY_MIN_BREADTH = 0.50
ACTIVE_MIN_BREADTH = 0.60
ACTIVE_RELATIVE_TOP_RATIO = 0.20
EMOTION_MIN_LIMIT_UP_COUNT = 3
EMOTION_MIN_LADDER_LEVELS = 2
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _number(value: Any) -> float | None:
    """只接受已统一单位的数值；带“万/亿”的文本不在规则层猜单位。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None:
        return None
    return int(number)


def _industry_key(value: Any) -> str:
    """只做格式归一，不做语义别名和模糊匹配。"""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return " ".join(text.split())


def _stock_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith(("SH", "SZ", "BJ")):
        text = text[2:]
    text = text.split(".", 1)[0]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else text


def _data_date(payload: Mapping[str, Any]) -> str | None:
    """只认可明确的数据归属日，不用采集 ts 冒充 data_date。"""
    for key in ("data_date", "trade_date"):
        value = str(payload.get(key) or "")[:10]
        if _DATE_RE.fullmatch(value):
            return value
    return None


def _unique_append(items: list[str], message: Any) -> None:
    text = str(message or "").strip()
    if text and text not in items:
        items.append(text)


def _industry_from_map(industry_by_code: Mapping[str, Any], code: str) -> str:
    raw = industry_by_code.get(code)
    if raw is None:
        # 兼容调用方使用 000001.SZ 作键，仍只是代码等值查找。
        raw = next(
            (value for key, value in industry_by_code.items() if _stock_code(key) == code),
            None,
        )
    if isinstance(raw, Mapping):
        raw = raw.get("industry")
    return str(raw or "").strip()


def _compact_limit_up(stock: Mapping[str, Any], industry: str) -> dict[str, Any]:
    return {
        "code": _stock_code(stock.get("code")),
        "name": str(stock.get("name") or ""),
        "change_pct": _number(stock.get("change_pct")),
        "lianban": max(1, _integer(stock.get("lianban")) or 1),
        "industry": industry,
        "first_time": str(stock.get("first_time") or "") or None,
        "last_time": str(stock.get("last_time") or "") or None,
        "open_times": _integer(stock.get("zhaban_count", stock.get("open_times"))),
        "seal_amount": _number(stock.get("seal_amount")),
        "amount": _number(stock.get("amount")),
        "turnover_rate": _number(stock.get("turnover_rate")),
        "reason": str(stock.get("reason") or "")[:80] or None,
    }


def _time_seconds(value: Any) -> int | None:
    match = re.fullmatch(r"(\d{2}):(\d{2})(?::(\d{2}))?", str(value or "").strip())
    if not match:
        return None
    hour, minute, second = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour * 3600 + minute * 60 + second


def assess_leader_quality(stock: Mapping[str, Any] | None) -> dict[str, Any]:
    """用同日公开池原始字段解释龙头承接，不改主线结构分。"""
    if not isinstance(stock, Mapping):
        return {
            "status": "missing", "level": "待验证", "score_weight": 0,
            "evidence": [], "missing_fields": ["龙头未识别"],
        }
    evidence: list[str] = []
    missing_fields: list[str] = []
    strong = weak = observed = 0

    open_times = _integer(stock.get("open_times"))
    if open_times is None:
        missing_fields.append("开板次数")
    else:
        observed += 1
        evidence.append(f"开板{open_times}次")
        if open_times == 0:
            strong += 1
        elif open_times >= 2:
            weak += 1

    seal_amount = _number(stock.get("seal_amount"))
    amount = _number(stock.get("amount"))
    seal_ratio = None
    if seal_amount is not None and seal_amount >= 0 and amount is not None and amount > 0:
        seal_ratio = seal_amount / amount
        observed += 1
        evidence.append(f"封单/成交{seal_ratio * 100:.1f}%")
        if seal_ratio >= 0.10:
            strong += 1
        elif seal_ratio < 0.03:
            weak += 1
    else:
        missing_fields.append("封单/成交")

    first_time = str(stock.get("first_time") or "").strip()
    first_seconds = _time_seconds(first_time)
    if first_seconds is None:
        missing_fields.append("首次封板时间")
    else:
        observed += 1
        evidence.append(f"首封{first_time}")
        if first_seconds <= 9 * 3600 + 35 * 60:
            strong += 1
        elif first_seconds >= 13 * 3600 + 30 * 60:
            weak += 1

    last_time = str(stock.get("last_time") or "").strip()
    if last_time and last_time != first_time:
        evidence.append(f"末封{last_time}")
    turnover = _number(stock.get("turnover_rate"))
    if turnover is not None:
        evidence.append(f"换手{turnover:.1f}%")

    if observed < 2:
        level = "待验证"
    elif weak >= 2:
        level = "承接偏弱"
    elif strong >= 2 and weak == 0:
        level = "承接较强"
    else:
        level = "分歧承接"
    return {
        "status": "complete" if observed == 3 else ("partial" if observed else "missing"),
        "level": level,
        "score_weight": 0,
        "observed_dimensions": observed,
        "total_dimensions": 3,
        "evidence": evidence,
        "missing_fields": missing_fields,
        "seal_to_amount_ratio": round(seal_ratio, 4) if seal_ratio is not None else None,
        "rule": "早封、少开板、封单/成交充足为强信号；仅作承接解释",
    }


def _leader_sort_key(stock: Mapping[str, Any]) -> tuple[Any, ...]:
    """先看板位；同板位才用真实承接字段排龙头候选。"""
    open_times = _integer(stock.get("open_times"))
    seal = _number(stock.get("seal_amount"))
    amount = _number(stock.get("amount"))
    seal_ratio = seal / amount if seal is not None and seal >= 0 and amount is not None and amount > 0 else None
    first_seconds = _time_seconds(stock.get("first_time"))
    return (
        -(_integer(stock.get("lianban")) or 0),
        open_times if open_times is not None else 999,
        -(seal_ratio if seal_ratio is not None else -1),
        first_seconds if first_seconds is not None else 999_999,
        -(_number(stock.get("change_pct")) or -999),
        str(stock.get("code") or ""),
    )


def _competition_rank(values: Sequence[float], value: float) -> int:
    """并列值共用名次（1, 1, 3），避免同数值因输入顺序得到不同分。"""
    return 1 + sum(1 for candidate in values if candidate > value)


def _ratio(part: Any, total: Any) -> float | None:
    numerator = _number(part)
    denominator = _number(total)
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return max(0.0, min(1.0, numerator / denominator))


def _market_regime(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """把全市场宽度、跌停与炸板合成“强势/震荡/退潮”环境。

    活口只在退潮环境有业务意义；正常行情里把普通强势方向叫活口会混淆
    主线与防守性逆势方向。
    """
    source = context or {}
    up = _integer(source.get("up_count"))
    down = _integer(source.get("down_count"))
    flat = _integer(source.get("flat_count")) or 0
    market_total = sum(value for value in (up, down, flat) if value is not None)
    up_ratio = _ratio(up, market_total) if up is not None and down is not None else None
    limit_up = _integer(source.get("limit_up"))
    limit_down = _integer(source.get("limit_down"))
    zhaban_rate = _number(source.get("zhaban_rate"))
    weak_votes = 0
    if up_ratio is not None and up_ratio < 0.35:
        weak_votes += 1
    if limit_up is not None and limit_down is not None and limit_down > max(20, limit_up * 1.2):
        weak_votes += 1
    if zhaban_rate is not None and zhaban_rate >= 35:
        weak_votes += 1
    strong = bool(
        up_ratio is not None and up_ratio >= 0.60
        and (limit_up is None or limit_down is None or limit_up >= limit_down)
        and (zhaban_rate is None or zhaban_rate < 25)
    )
    state = "退潮" if weak_votes >= 1 else ("强势" if strong else "震荡")
    return {
        "state": state,
        "is_weak": state == "退潮",
        "up_ratio": round(up_ratio, 4) if up_ratio is not None else None,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "zhaban_rate": zhaban_rate,
    }


def _role_reason(row: Mapping[str, Any], role: str, regime: Mapping[str, Any]) -> str:
    amount_pct = _number(row.get("amount_rank_pct"))
    breadth = _number(row.get("breadth_ratio"))
    change = _number(row.get("change_pct"))
    amount_text = f"容量前{amount_pct * 100:.0f}%" if amount_pct is not None else "容量未知"
    breadth_text = f"宽度{breadth * 100:.0f}%" if breadth is not None else "宽度未知"
    limit_text = f"涨停{_integer(row.get('limit_up_count')) or 0}家"
    height_text = f"最高{_integer(row.get('max_lianban')) or 0}板"
    change_text = f"{change:+.2f}%" if change is not None else "涨幅未知"
    if role == "活口":
        return f"{regime.get('state', '弱势')}盘逆势{change_text} · {breadth_text} · {limit_text}"
    if role == "情绪链":
        levels = "/".join(str(value) for value in row.get("ladder_levels") or []) or "无"
        leader = (row.get("leader") or {}).get("name") if isinstance(row.get("leader"), Mapping) else ""
        return f"{levels}板梯队 · {limit_text} · 龙头{leader or '待确认'}"
    return f"{amount_text} · {change_text} · {breadth_text} · {limit_text} · {height_text}"


def classify_market_structure(
    rows: Sequence[Mapping[str, Any]],
    market_context: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """输出固定五槽：主线、两条次主线、活口、情绪链。

    每个已形成角色只占一个行业，避免同一方向在五张卡里重复。没有达到硬门槛
    的角色返回“尚未形成”，不拿排行榜第四、第五名强行填满。
    """
    regime = _market_regime(market_context)
    candidates = [dict(row) for row in rows if row.get("name")]
    used: set[str] = set()

    def pick(flag: str, score_key: str, *, enabled: bool = True) -> dict[str, Any] | None:
        if not enabled:
            return None
        pool = [
            row for row in candidates
            if row.get("classification", {}).get(flag) and row.get("name") not in used
        ]
        pool.sort(key=lambda row: (
            -(_number(row.get(score_key)) or 0),
            -(_number(row.get("structure_score")) or 0),
            str(row.get("name") or ""),
        ))
        if not pool:
            return None
        chosen = pool[0]
        used.add(str(chosen.get("name")))
        return chosen

    primary = pick("mainline", "structure_score")
    active = pick("active", "survival_score", enabled=bool(regime.get("is_weak")))
    emotion = pick("emotion", "emotion_score")

    secondary_pool = [
        row for row in candidates
        if row.get("name") not in used
        and (
            row.get("classification", {}).get("mainline")
            or row.get("classification", {}).get("secondary")
        )
    ]
    secondary_pool.sort(key=lambda row: (
        -(_number(row.get("structure_score")) or 0),
        str(row.get("name") or ""),
    ))
    secondaries = secondary_pool[:2]
    used.update(str(row.get("name")) for row in secondaries)

    empty_reasons = {
        "主线": "未同时满足：板块上涨、宽度≥55%、成交额前20%、涨停≥2家",
        "次主线": "未达到：板块上涨、宽度≥50%、成交额前35%、涨停≥1家",
        "活口": (
            "当前不是退潮盘，不单列活口"
            if not regime.get("is_weak")
            else "退潮盘未出现宽度≥60%且有涨停承接的逆势方向"
        ),
        "情绪链": "未形成至少3只涨停、2个板位且最高≥2板的梯队",
    }

    def slot(role: str, row: Mapping[str, Any] | None) -> dict[str, Any]:
        if row is None:
            return {
                "label": role,
                "status": "unformed",
                "name": "尚未形成",
                "role_reason": empty_reasons[role],
                "change_pct": None,
                "amount": None,
                "amount_rank_pct": None,
                "breadth_ratio": None,
                "limit_up_count": None,
                "max_lianban": None,
                "limit_up_stocks": [],
                "ladder_levels": [],
                "score": None,
                "structure_score": None,
                "leader": None,
                "leader_quality": assess_leader_quality(None),
                "catalyst_status": "not_applicable",
                "catalyst_evidence": [],
            }
        item = dict(row)
        item["label"] = role
        item["status"] = "formed"
        item["role_reason"] = _role_reason(item, role, regime)
        return item

    roles = [
        slot("主线", primary),
        slot("次主线", secondaries[0] if len(secondaries) > 0 else None),
        slot("次主线", secondaries[1] if len(secondaries) > 1 else None),
        slot("活口", active),
        slot("情绪链", emotion),
    ]
    return roles, regime


def rank_mainlines(
    sector_rows: Sequence[Mapping[str, Any]],
    limit_up_rows: Sequence[Mapping[str, Any]],
    industry_by_code: Mapping[str, Any],
    *,
    limit_up_available: bool = True,
    limit: int = 5,
) -> tuple[list[dict[str, Any]], list[str]]:
    """纯函数：将行业板块、涨停股和 code→industry 映射计算为主线卡。

    返回 ``(sectors, missing)``。涨停映射不完整时会显式标记，但已精确映射的
    家数仍作为“已确认下界”证据；完全无映射时不把未知伪装成 0家。
    """
    missing: list[str] = []
    max_items = max(1, min(int(limit), 300))

    industries: list[dict[str, Any]] = []
    keys_to_names: dict[str, list[str]] = {}
    for raw in sector_rows or []:
        if not isinstance(raw, Mapping) or raw.get("kind") != "industry":
            continue
        name = str(raw.get("name") or "").strip()
        key = _industry_key(name)
        if not key:
            _unique_append(missing, "主线数据不足：存在行业名称缺失的板块记录")
            continue
        keys_to_names.setdefault(key, []).append(name)
        industries.append({
            "source": raw,
            "name": name,
            "key": key,
            "limit_up_stocks": [],
        })

    if not industries:
        _unique_append(missing, "主线数据不足：没有可用的行业板块")
        return [], missing

    ambiguous = {key for key, names in keys_to_names.items() if len(names) != 1}
    if ambiguous:
        names = "、".join(sorted({name for key in ambiguous for name in keys_to_names[key]})[:8])
        _unique_append(
            missing,
            f"主线数据不足：行业名称标准化后不唯一（{names}），未强行合并",
        )

    exact_targets = {
        item["key"]: item
        for item in industries
        if item["key"] not in ambiguous
    }
    valid_limitups = [row for row in (limit_up_rows or []) if isinstance(row, Mapping)]
    unmapped: list[str] = []
    mapped_count = 0
    for stock in valid_limitups:
        code = _stock_code(stock.get("code"))
        # 涨停公开池本身已提供“所属行业”，它与当日涨停行同源且无需逐股联网。
        # 优先使用行内精确值，再用低频 code→industry 映射兜底。
        industry = str(stock.get("industry") or "").strip()
        if not industry and code:
            industry = _industry_from_map(industry_by_code, code)
        target = exact_targets.get(_industry_key(industry)) if industry else None
        if target is None:
            marker = code or str(stock.get("name") or "未知股票")
            unmapped.append(marker)
            continue
        target["limit_up_stocks"].append(_compact_limit_up(stock, target["name"]))
        mapped_count += 1

    mapping_available = bool(limit_up_available and (not valid_limitups or mapped_count > 0))
    if not limit_up_available:
        _unique_append(
            missing,
            "主线数据不足：同日涨停明细不可用，未将空列表解释为0家",
        )
    if unmapped:
        sample = "、".join(unmapped[:8])
        suffix = f"等{len(unmapped)}只" if len(unmapped) > 8 else ""
        _unique_append(
            missing,
            f"主线数据不足：{len(unmapped)}只涨停股的行业未与行业榜精确对齐"
            f"（{sample}{suffix}）；未做模糊题材匹配",
        )
    if limit_up_available and valid_limitups and not mapping_available:
        _unique_append(
            missing,
            "主线数据不足：涨停股票行业映射全部缺失，"
            "未将未知当作0家，也未降级按净流入排序",
        )

    amount_values = [
        amount
        for item in industries
        if (amount := _number(item["source"].get("amount"))) is not None and amount >= 0
    ]
    amount_count = len(amount_values)
    if not amount_values:
        _unique_append(missing, "主线数据不足：行业成交额全部缺失，未降级按净流入排序")

    change_values = [
        change for item in industries
        if (change := _number(item["source"].get("change_pct"))) is not None
    ]
    ranked: list[dict[str, Any]] = []
    for item in industries:
        raw = item["source"]
        row_missing: list[str] = []
        amount = _number(raw.get("amount"))
        if amount is not None and amount < 0:
            amount = None
        if amount is None:
            row_missing.append("行业成交额缺失，不能判定主线")
        amount_rank = _competition_rank(amount_values, amount) if amount is not None and amount_values else None
        amount_rank_pct = round(amount_rank / amount_count, 4) if amount_rank and amount_count else None

        matched = sorted(
            item["limit_up_stocks"],
            key=_leader_sort_key,
        )
        confirmed_count = len(matched)
        limit_up_count: int | None = confirmed_count if mapping_available else None
        if not mapping_available:
            row_missing.append("涨停行业映射缺失，未知不按0家计")
        elif unmapped:
            row_missing.append("涨停映射不完整，家数仅为已精确对齐的下界")

        max_lianban = max((stock.get("lianban") or 1 for stock in matched), default=0)
        ladder_levels = sorted({int(stock.get("lianban") or 1) for stock in matched}, reverse=True)
        change_pct = _number(raw.get("change_pct"))
        change_rank = _competition_rank(change_values, change_pct) if change_pct is not None and change_values else None
        relative_rank_pct = round(change_rank / len(change_values), 4) if change_rank and change_values else None
        capacity_score = (
            1.0 if amount_count <= 1
            else 1.0 - (amount_rank - 1) / (amount_count - 1)
        ) if amount_rank is not None and amount_count else None
        relative_score = (
            1.0 if len(change_values) <= 1
            else 1.0 - (change_rank - 1) / (len(change_values) - 1)
        ) if change_rank is not None and change_values else None
        up_count = _integer(raw.get("up_count"))
        down_count = _integer(raw.get("down_count"))
        flat_count = _integer(raw.get("flat_count")) or 0
        member_total = sum(value for value in (up_count, down_count, flat_count) if value is not None)
        breadth_ratio = _ratio(up_count, member_total) if up_count is not None and down_count is not None else None
        if breadth_ratio is None:
            row_missing.append("板块上涨宽度缺失，不能确认主线/活口")
        limit_heat = min(1.0, confirmed_count / 4.0) if limit_up_count is not None else None
        height_score = min(1.0, max_lianban / 3.0)
        ladder_score = min(1.0, len(ladder_levels) / 3.0)
        emotion_score = round(
            100 * (
                (limit_heat or 0) * 0.45
                + height_score * 0.30
                + ladder_score * 0.25
            ),
            1,
        ) if limit_up_count is not None else None
        weighted = [
            (capacity_score, 0.25),
            (relative_score, 0.25),
            (breadth_ratio, 0.20),
            ((emotion_score / 100) if emotion_score is not None else None, 0.30),
        ]
        available_weight = sum(weight for value, weight in weighted if value is not None)
        structure_score = round(
            100 * sum(value * weight for value, weight in weighted if value is not None) / available_weight,
            1,
        ) if available_weight else None
        survival_score = round(
            100 * (
                (relative_score or 0) * 0.35
                + (breadth_ratio or 0) * 0.35
                + (limit_heat or 0) * 0.20
                + (capacity_score or 0) * 0.10
            ),
            1,
        ) if relative_score is not None and breadth_ratio is not None else None
        mainline_gate = bool(
            amount_rank_pct is not None and amount_rank_pct <= AMOUNT_TOP_RATIO
            and limit_up_count is not None and limit_up_count >= MIN_LIMIT_UP_COUNT
            and change_pct is not None and change_pct > 0
            and breadth_ratio is not None and breadth_ratio >= MAINLINE_MIN_BREADTH
        )
        secondary_gate = bool(
            amount_rank_pct is not None and amount_rank_pct <= SECONDARY_AMOUNT_TOP_RATIO
            and limit_up_count is not None and limit_up_count >= 1
            and change_pct is not None and change_pct > 0
            and breadth_ratio is not None and breadth_ratio >= SECONDARY_MIN_BREADTH
        )
        active_gate = bool(
            relative_rank_pct is not None and relative_rank_pct <= ACTIVE_RELATIVE_TOP_RATIO
            and change_pct is not None and change_pct > 0
            and breadth_ratio is not None and breadth_ratio >= ACTIVE_MIN_BREADTH
            and limit_up_count is not None
            and (limit_up_count >= 1 or max_lianban >= 2)
        )
        emotion_gate = bool(
            limit_up_count is not None and limit_up_count >= EMOTION_MIN_LIMIT_UP_COUNT
            and max_lianban >= 2
            and len(ladder_levels) >= EMOTION_MIN_LADDER_LEVELS
        )
        score = round(structure_score / 100, 4) if structure_score is not None else None
        qualifies = mainline_gate
        if amount_rank is None:
            evidence = "成交额缺失"
        else:
            evidence = f"成交额行业第{amount_rank}"
        if change_pct is not None:
            evidence += f"；板块{change_pct:+.2f}%"
        if breadth_ratio is not None:
            evidence += f"；上涨宽度{breadth_ratio * 100:.0f}%"
        evidence += (
            f"；涨停{limit_up_count}家；最高{max_lianban}板"
            if limit_up_count is not None
            else "；涨停行业映射缺失"
        )
        ranked.append({
            "name": item["name"],
            "label": "观察方向",
            "amount": amount,
            "amount_rank_pct": amount_rank_pct,
            "limit_up_count": limit_up_count,
            "limit_up_stocks": matched,
            "max_lianban": max_lianban,
            "change_pct": change_pct,
            "breadth_ratio": round(breadth_ratio, 4) if breadth_ratio is not None else None,
            "member_count": member_total or None,
            "relative_strength_rank_pct": relative_rank_pct,
            "ladder_levels": ladder_levels,
            "leader": matched[0] if matched else None,
            "leader_quality": assess_leader_quality(matched[0] if matched else None),
            "structure_score": structure_score,
            "survival_score": survival_score,
            "emotion_score": emotion_score,
            "classification": {
                "mainline": mainline_gate,
                "secondary": secondary_gate,
                "active": active_gate,
                "emotion": emotion_gate,
            },
            # 净流入只透传，不出现在 score/qualifies/排序键中。
            "main_net": _number(raw.get("main_net", raw.get("main_flow"))),
            "score": score,
            "evidence": evidence,
            "missing": row_missing,
            "_qualifies": qualifies,
            "_amount_rank": amount_rank,
        })

    def _sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            -(row.get("score") if row.get("score") is not None else -1.0),
            -(row.get("change_pct") if row.get("change_pct") is not None else -999.0),
            -(row.get("max_lianban") or 0),
            row.get("name") or "",
        )

    qualified = sorted((row for row in ranked if row["_qualifies"]), key=_sort_key)
    observations = sorted((row for row in ranked if not row["_qualifies"]), key=_sort_key)
    for index, row in enumerate(qualified):
        row["label"] = "主线" if index == 0 else ("次主线" if index <= 2 else "观察方向")

    result = []
    for row in (qualified + observations)[:max_items]:
        result.append({key: value for key, value in row.items() if not key.startswith("_")})
    return result, missing


def normalize_limit_down_pool(
    rows: Sequence[Mapping[str, Any]] | None,
    industry_by_code: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """纯函数：标准化同日可靠源返回的跌停明细；缺字段留 ``None``。"""
    missing: list[str] = []
    pool: list[dict[str, Any]] = []
    unmapped: list[str] = []
    for raw in rows or []:
        if not isinstance(raw, Mapping):
            continue
        code = _stock_code(raw.get("code"))
        if not code:
            _unique_append(missing, "跌停明细存在股票代码缺失的记录，已跳过")
            continue
        industry = str(raw.get("industry") or "").strip() or _industry_from_map(industry_by_code, code)
        if not industry:
            unmapped.append(code)
        pool.append({
            "code": code,
            "name": str(raw.get("name") or ""),
            "change_pct": _number(raw.get("change_pct")),
            "industry": industry or None,
            "first_time": str(raw.get("first_time") or "") or None,
            "seal_amount": _number(raw.get("seal_amount")),
            "open_times": _integer(raw.get("open_times")),
        })
    pool.sort(key=lambda row: (
        row.get("change_pct") if row.get("change_pct") is not None else 999.0,
        row.get("code") or "",
    ))
    if unmapped:
        sample = "、".join(unmapped[:8])
        _unique_append(missing, f"{len(unmapped)}只跌停股行业映射缺失（{sample}），未猜测")
    return pool, missing


def _catalyst_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", "", text)


def attach_catalyst_evidence(
    rows: Sequence[dict[str, Any]],
    catalyst_payload: Mapping[str, Any] | None,
    *,
    data_date: str | None,
) -> dict[str, Any]:
    """按行业名/龙头名精确文本命中催化；只补证据，绝不改分数或角色。

    新闻有明确日期时必须与主线 ``data_date`` 同日。没有日期的盘中时分流仍可
    作为当日候选，但保留原始时间，前端不把它包装成确定因果。
    """
    source = catalyst_payload if isinstance(catalyst_payload, Mapping) else {}
    raw_items = source.get("items")
    feed_available = isinstance(raw_items, list)
    candidates: list[dict[str, Any]] = []
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, Mapping):
            continue
        event_time = str(item.get("time") or "").strip()
        explicit_date = re.search(r"(\d{4}-\d{2}-\d{2})", event_time)
        if explicit_date and data_date and explicit_date.group(1) != data_date:
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        text = _catalyst_text(f"{title} {content}")
        if not text:
            continue
        candidates.append({
            "title": (title or content[:80])[:160],
            "time": event_time[:32],
            "confirms": max(1, _integer(item.get("confirms")) or 1),
            "multi": bool(item.get("multi")),
            "_text": text,
        })

    candidates.sort(key=lambda item: item["time"], reverse=True)
    matched_sectors = matched_items = 0
    for row in rows:
        sector_name = str(row.get("name") or "").strip()
        leader = row.get("leader") if isinstance(row.get("leader"), Mapping) else {}
        leader_name = str(leader.get("name") or "").strip()
        sector_key = _catalyst_text(sector_name) if len(sector_name) >= 2 else ""
        leader_key = _catalyst_text(leader_name) if len(leader_name) >= 2 else ""
        evidence: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in candidates:
            sector_hit = bool(sector_key and sector_key in item["_text"])
            leader_hit = bool(leader_key and leader_key in item["_text"])
            if not sector_hit and not leader_hit:
                continue
            dedupe = (item["title"], item["time"])
            if dedupe in seen:
                continue
            seen.add(dedupe)
            evidence.append({
                "title": item["title"],
                "time": item["time"],
                "confirms": item["confirms"],
                "multi": item["multi"],
                "match": (
                    "行业名+龙头名" if sector_hit and leader_hit
                    else ("龙头名" if leader_hit else "行业名")
                ),
            })
            if len(evidence) >= 3:
                break
        row["catalyst_evidence"] = evidence
        row["catalyst_status"] = (
            "confirmed" if evidence else ("structure_only" if feed_available else "unavailable")
        )
        if evidence:
            matched_sectors += 1
            matched_items += len(evidence)

    source_missing = [str(message) for message in source.get("missing") or [] if str(message)]
    return {
        "status": "available" if feed_available else "unavailable",
        "score_weight": 0,
        "candidate_items": len(candidates),
        "matched_sectors": matched_sectors,
        "matched_items": matched_items,
        "latest_event_time": source.get("latest_event_time"),
        "missing": source_missing,
        "rule": "仅按当日行业名或龙头名原文命中；催化不参与结构评分",
    }


def build_mainline_response(
    sector_payload: Mapping[str, Any],
    limitup_payload: Mapping[str, Any],
    industry_by_code: Mapping[str, Any],
    *,
    market_context: Mapping[str, Any] | None = None,
    limit_down_rows: Sequence[Mapping[str, Any]] | None = None,
    limit_down_missing: Sequence[str] | None = None,
    catalyst_payload: Mapping[str, Any] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """纯函数：组装 ``GET /api/market/mainlines`` 的固定响应契约。"""
    missing: list[str] = []
    rule_messages: list[str] = []
    for message in sector_payload.get("missing") or []:
        _unique_append(missing, message)
    for message in limitup_payload.get("missing") or []:
        _unique_append(missing, message)

    sector_date = _data_date(sector_payload)
    limitup_date = _data_date(limitup_payload)
    data_date = limitup_date or sector_date
    all_sectors: list[dict[str, Any]] = []
    if not sector_date or not limitup_date:
        _unique_append(missing, "主线数据不足：行业板块或涨停池缺data_date，未跨日混算")
    elif sector_date != limitup_date:
        _unique_append(
            missing,
            f"主线数据不足：行业板块({sector_date})与涨停池({limitup_date})"
            "data_date不一致，未跨日混算",
        )
    else:
        stocks = limitup_payload.get("stocks")
        if not isinstance(stocks, list):
            _unique_append(missing, "主线数据不足：涨停明细格式不可用")
        else:
            # 现有数据层在“明细未落库/源不可用”时也返 stocks=[]，但 missing
            # 会明确写出涨停池/梯队明细缺失。这种空不能当作真实 0 涨停。
            limitup_notes = [str(message or "") for message in limitup_payload.get("missing") or []]
            limit_up_available = bool(stocks) or not any(
                marker in message
                for message in limitup_notes
                for marker in ("涨停池", "涨停连板梯队明细", "涨停明细")
            )
            all_sectors, rule_missing = rank_mainlines(
                sector_payload.get("sectors") or [],
                stocks,
                industry_by_code,
                limit_up_available=limit_up_available,
                # 五个角色必须从完整行业横截面挑选；不能先截前5再寻找逆势活口。
                limit=max(1, len(sector_payload.get("sectors") or [])),
            )
            for message in rule_missing:
                _unique_append(missing, message)
                _unique_append(rule_messages, message)

    down_pool, down_rule_missing = normalize_limit_down_pool(limit_down_rows, industry_by_code)
    down_missing: list[str] = []
    for message in limit_down_missing or []:
        _unique_append(down_missing, message)
    for message in down_rule_missing:
        _unique_append(down_missing, message)

    limitup_rows = limitup_payload.get("stocks")
    limitup_total = len(limitup_rows) if isinstance(limitup_rows, list) else 0
    # ``sectors`` 已按 limit 截断，不能用返回前5张卡里的股票数冒充全市场映射覆盖。
    unmapped_total = 0
    for message in rule_messages:
        match = re.search(r"(\d+)只涨停股的行业未与行业榜精确对齐", message)
        if match:
            unmapped_total = max(unmapped_total, int(match.group(1)))
    mapped_total = max(0, limitup_total - unmapped_total)
    catalyst_layer = attach_catalyst_evidence(
        all_sectors,
        catalyst_payload,
        data_date=data_date,
    )
    roles, market_state = classify_market_structure(all_sectors, market_context)
    sectors = all_sectors[:max(1, min(int(limit), 30))]
    scored_sectors = sum(1 for row in all_sectors if row.get("score") is not None)
    missing_fields: list[str] = []
    degraded_factors: list[str] = []
    notes: list[str] = []
    for message in sector_payload.get("missing_fields") or []:
        if "概念" not in str(message):
            _unique_append(missing_fields, message)
    for message in sector_payload.get("degraded_factors") or []:
        if "概念" not in str(message):
            _unique_append(degraded_factors, message)
    for message in sector_payload.get("notes") or []:
        _unique_append(notes, message)
    completely_unmapped = bool(limitup_total and mapped_total == 0)
    for message in rule_messages:
        if "未与行业榜精确对齐" in message and not completely_unmapped:
            _unique_append(degraded_factors, message)
        else:
            _unique_append(missing_fields, message)
    # 旧 missing 继续保留给模型/兼容调用方；状态角标只消费结构化质量字段。
    for message in missing:
        if message not in missing_fields and message not in degraded_factors:
            _unique_append(notes, message)
    if not all_sectors:
        _unique_append(missing_fields, "主线候选不可计算")
    elif limitup_total and scored_sectors == 0:
        _unique_append(missing_fields, "主线核心分数不可计算")
    if not market_context:
        _unique_append(degraded_factors, "全市场涨跌宽度/涨跌停环境缺失，活口环境判定停用")
    data_state = (
        "missing" if missing_fields
        else ("degraded" if degraded_factors else "complete")
    )

    return {
        "ok": True,
        "ts": str(sector_payload.get("ts") or limitup_payload.get("ts") or ""),
        "data_date": data_date,
        "missing": missing,
        "missing_fields": missing_fields,
        "degraded_factors": degraded_factors,
        "notes": notes,
        "data_state": data_state,
        "coverage": {
            "limit_up_mapping": {"complete": mapped_total, "total": limitup_total},
            "scored_sectors": {"complete": scored_sectors, "total": len(all_sectors)},
        },
        "sectors": sectors,
        "roles": roles,
        "market_state": market_state,
        "catalyst_layer": catalyst_layer,
        "algorithm": {
            "version": "market-structure-v2",
            "mainline_gate": "板块上涨 + 上涨宽度≥55% + 成交额前20% + 涨停≥2家",
            "secondary_gate": "板块上涨 + 上涨宽度≥50% + 成交额前35% + 涨停≥1家",
            "active_gate": "仅退潮盘：相对强度前20% + 上涨宽度≥60% + 至少1家涨停承接",
            "emotion_gate": "涨停≥3家 + 至少2个板位 + 最高≥2板",
            "score_factors": "容量25% + 相对强度25% + 上涨宽度20% + 涨停梯队30%",
            "main_flow_weight": 0,
            "leader_quality_weight": 0,
            "leader_quality_rule": "同板位按少开板、封单/成交、首封时间排序；仅解释龙头承接",
        },
        "limit_down_pool": down_pool,
        "limit_down_missing": down_missing,
    }
