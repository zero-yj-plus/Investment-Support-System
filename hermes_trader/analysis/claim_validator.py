"""交易价格 claim 硬闸。

这里不做「报告里的数字在任意注入字段出现过即算合法」的宽松匹配，
而是只处理会影响下单的价格表述，并强制绑定到同一只股票的真实价格证据。

设计取舍：
- 无法绑定股票、该股无价格证据、或价格明显超出对应股票真实区间：阻断。
- 成交额/市值/资金流等金额不是交易价：不在本闸处理，避免误伤。
- 只把「对应股票的价格证据」作为来源，不会被另一只股票的同值数字碰撞放行。
- 报告被 max_tokens 截断时直接禁止定稿，即使已有部分文本也不例外。
"""
from __future__ import annotations

import re
from statistics import median
from typing import Any


VALIDATOR_VERSION = "stock_price_claims_v1"

_CODE_RE = re.compile(r"(?<!\d)([03468]\d{5})(?!\d)")
_IDENTITY_CODE_KEYS = ("code", "代码", "ts_code", "symbol", "stock_code", "股票代码")
_IDENTITY_NAME_KEYS = ("name", "名称", "stock_name", "股票名称", "证券简称")
_QUOTE_CONTAINER_KEYS = ("quote", "实时行情", "行情", "盘口")

# 只收集行情源的「已观测价格」。用户成本、观察池目标、AI 推荐价/止损价
# 都不能反过来当作行情证据，否则错误的 150 元目标本身就会把 150 元放行。
# 成交额/市值/涨跌幅等也不在此表。
_PRICE_KEYS = {
    "price", "current_price", "open", "high", "low", "close", "prev_close",
    "avg", "average", "close_price", "today_price", "up_limit", "down_limit",
    "现价", "最新价", "开盘价", "今开", "最高价", "最高", "最低价", "最低", "昨收", "前收盘",
    "收盘价", "收盘", "均价", "涨停价", "跌停价",
}
_DAILY_BAR_KEYS = {"o", "c", "h", "l"}

_PRICE_CONTEXT_RE = re.compile(
    r"当前价|现价|价格|价位|买入区间|介入区间|低吸区间|买入价|卖出价|"
    r"回踩|站上|突破|跌破|止损|止盈|目标|减仓|卖出|清仓|放弃价|认错线|"
    r"支撑|压力|开盘价|高开|低开|冲高|挂单|买点|卖点|成本价|持有至"
)
_STRONG_PRICE_CONTEXT_RE = re.compile(
    r"价|价位|区间|回踩|站上|突破|跌破|支撑|压力|目标|认错线|"
    r"买点|卖点|高开|低开|冲高|挂单"
)
_AMOUNT_CONTEXT_RE = re.compile(r"成交额|市值|资金|净流入|净流出|营收|订单|金额|亏损|盈利|损失|收益")

_RANGE_YUAN_RE = re.compile(
    r"(?<![\d.])(\d{1,5}(?:\.\d+)?)\s*(?:-|\u2014|~|～|至|到)\s*"
    r"(\d{1,5}(?:\.\d+)?)\s*元"
)
_SINGLE_YUAN_RE = re.compile(r"(?<![\d.])(\d{1,5}(?:\.\d+)?)\s*元")
_LABEL = (
    r"当前价|现价|买入区间|介入区间|低吸区间|买入价|卖出价|"
    r"回踩|站上|突破|跌破|止损位?|止损价|止盈位?|止盈价|目标价?|减仓位?|"
    r"放弃价|认错线|支撑位?|压力位?|开盘价|高开|低开|冲高|挂单价?|买点|卖点|成本价"
)
_LABELED_RE = re.compile(
    rf"(?P<kind>{_LABEL})\s*(?:为|在|约|至|到|看|设|：|:|=)?\s*"
    rf"(?P<a>\d{{1,5}}(?:\.\d+)?)"
    rf"(?:\s*(?:-|\u2014|~|～|至|到)\s*(?P<b>\d{{1,5}}(?:\.\d+)?))?"
)


def _normalise_code(value: Any) -> str:
    text = str(value or "").strip()
    m = _CODE_RE.search(text)
    return m.group(1) if m else ""


def _first_text(obj: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _as_price(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not (0 < number < 1_000_000):
        return None
    return number


def _identity(obj: dict, inherited_code: str, inherited_name: str) -> tuple[str, str]:
    code = _normalise_code(_first_text(obj, _IDENTITY_CODE_KEYS)) or inherited_code
    name = _first_text(obj, _IDENTITY_NAME_KEYS) or inherited_name

    # stock_detail 根对象的身份常在 quote 子对象，而日 K 是它的兄弟字段。
    # 只检查明确的行情容器，不从任意子对象「猜」身份，防止把整个市场上下文误绑给第一只股票。
    if not code:
        for key in _QUOTE_CONTAINER_KEYS:
            child = obj.get(key)
            if not isinstance(child, dict):
                continue
            code = _normalise_code(_first_text(child, _IDENTITY_CODE_KEYS))
            if code:
                name = _first_text(child, _IDENTITY_NAME_KEYS) or name
                break
    return code, name


def collect_price_evidence(data: dict) -> dict[str, dict]:
    """从注入上下文收集按股票绑定的价格证据。

    返回 code -> {code, name, aliases, values:[{value,path,key}], allowed_low, allowed_high}。
    allowed 区间在真实已观测区间上下各留 15% 容差，用于容纳基于支撑/压力的小幅推导；
    不接受跨数量级的「目标价」。
    """
    records: dict[str, dict] = {}

    def ensure(code: str, name: str) -> dict:
        rec = records.setdefault(code, {
            "code": code, "name": "", "aliases": {code}, "values": [],
        })
        if name:
            rec["aliases"].add(name)
            if not rec["name"]:
                rec["name"] = name
        return rec

    def walk(obj: Any, path: str = "$", inherited_code: str = "", inherited_name: str = "") -> None:
        if isinstance(obj, dict):
            code, name = _identity(obj, inherited_code, inherited_name)
            rec = ensure(code, name) if code else None
            path_lower = path.lower()
            for key, value in obj.items():
                key_text = str(key)
                norm_key = key_text.strip().lower()
                is_daily_bar = norm_key in _DAILY_BAR_KEYS and any(
                    marker in path_lower for marker in ("daily", "kline", "日k", "日_k")
                )
                if rec is not None and (norm_key in _PRICE_KEYS or is_daily_bar):
                    price = _as_price(value)
                    if price is not None:
                        rec["values"].append({
                            "value": price,
                            "path": f"{path}.{key_text}",
                            "key": key_text,
                        })
                child_path = f"{path}.{key_text}"
                walk(value, child_path, code, name)
        elif isinstance(obj, (list, tuple)):
            for idx, value in enumerate(obj):
                walk(value, f"{path}[{idx}]", inherited_code, inherited_name)

    walk(data or {})

    for rec in records.values():
        vals = [x["value"] for x in rec["values"]]
        # 一个异常源值不应把整个允许区间拉跨数量级。有足够样本时按中位数做宽松去异常。
        if len(vals) >= 3:
            mid = median(vals)
            near = [v for v in vals if mid * 0.5 <= v <= mid * 1.5]
            if len(near) >= 2:
                vals = near
        if vals:
            rec["allowed_low"] = round(max(0.01, min(vals) * 0.85), 4)
            rec["allowed_high"] = round(max(vals) * 1.15, 4)
        else:
            rec["allowed_low"] = None
            rec["allowed_high"] = None
        rec["aliases"] = sorted(rec["aliases"], key=len, reverse=True)
    return records


def _tail_has_non_price_unit(line: str, end: int) -> bool:
    return bool(re.match(r"\s*(?:%|成|万|亿|日|周|月|年|板|倍|只|家|手|股)", line[end:]))


def _extract_line_claims(line: str) -> list[dict]:
    if not _PRICE_CONTEXT_RE.search(line):
        return []
    # 如「亏损 1000 元就止损」表达的是仓位金额而非股价；只在同行有明确价格语义时才继续。
    if _AMOUNT_CONTEXT_RE.search(line) and not _STRONG_PRICE_CONTEXT_RE.search(line):
        return []

    claims: list[dict] = []
    occupied: list[tuple[int, int]] = []
    kind_match = _PRICE_CONTEXT_RE.search(line)
    default_kind = kind_match.group(0) if kind_match else "交易价"

    for match in _RANGE_YUAN_RE.finditer(line):
        occupied.append(match.span())
        for group in (1, 2):
            claims.append({"value": float(match.group(group)), "kind": default_kind, "span": match.span()})

    for match in _SINGLE_YUAN_RE.finditer(line):
        if any(start <= match.start() and match.end() <= end for start, end in occupied):
            continue
        claims.append({"value": float(match.group(1)), "kind": default_kind, "span": match.span()})

    # 容纳「止损位：72」这种省略「元」的常见表格写法。
    for match in _LABELED_RE.finditer(line):
        if _tail_has_non_price_unit(line, match.end()):
            continue
        for group in ("a", "b"):
            raw = match.group(group)
            if raw is not None:
                claims.append({"value": float(raw), "kind": match.group("kind"), "span": match.span()})

    # 同一个数字可能同时命中「150元」和「回踩150」，按值+位置去重。
    out: list[dict] = []
    seen: set[tuple[float, int, int]] = set()
    for claim in claims:
        key = (round(claim["value"], 6), claim["span"][0], claim["span"][1])
        if key in seen:
            continue
        # 重叠匹配只保留一份（按数值去重）。
        if any(round(x["value"], 6) == round(claim["value"], 6)
               and not (claim["span"][1] <= x["span"][0] or claim["span"][0] >= x["span"][1])
               for x in out):
            continue
        seen.add(key)
        out.append(claim)
    return out


def _mentioned_codes(line: str, evidence: dict[str, dict]) -> list[str]:
    found: list[str] = []
    for code, rec in evidence.items():
        if _CODE_RE.search(line) and re.search(rf"(?<!\d){re.escape(code)}(?!\d)", line):
            found.append(code)
            continue
        for alias in rec.get("aliases") or []:
            if alias != code and len(alias) >= 2 and alias in line:
                found.append(code)
                break
    return list(dict.fromkeys(found))


def _blocked_claim(claim: dict, line_no: int, line: str, reason: str,
                   code: str = "", rec: dict | None = None) -> dict:
    result = {
        "line": line_no,
        "kind": claim["kind"],
        "value": claim["value"],
        "code": code or None,
        "name": (rec or {}).get("name") or None,
        "status": "blocked",
        "reason": reason,
        "excerpt": line.strip()[:240],
    }
    if rec:
        result["allowed_low"] = rec.get("allowed_low")
        result["allowed_high"] = rec.get("allowed_high")
        result["source_paths"] = [v["path"] for v in (rec.get("values") or [])[:8]]
    return result


def validate_report(content_md: str, data: dict) -> dict:
    """校验报告中股票绑定的交易价格。

    该函数是 fail-closed 定稿闸的纯函数部分：不访问网络、不读写文件。
    """
    evidence = collect_price_evidence(data or {})
    claims_out: list[dict] = []
    active_codes: list[str] = []

    for line_no, line in enumerate((content_md or "").splitlines(), 1):
        explicit = _mentioned_codes(line, evidence)
        heading = re.match(r"^\s*(#{1,6})\s+", line)
        if explicit:
            active_codes = explicit
        elif heading and len(heading.group(1)) <= 3:
            active_codes = []

        line_claims = _extract_line_claims(line)
        if not line_claims:
            continue

        raw_codes = list(dict.fromkeys(_CODE_RE.findall(line)))
        unknown_codes = [code for code in raw_codes if code not in evidence]
        bound_codes = explicit or active_codes

        for claim in line_claims:
            if unknown_codes:
                claims_out.append(_blocked_claim(
                    claim, line_no, line, "stock_not_in_injected_context", unknown_codes[0], None,
                ))
                continue
            if not bound_codes:
                claims_out.append(_blocked_claim(
                    claim, line_no, line, "price_claim_not_bound_to_stock",
                ))
                continue
            if len(bound_codes) != 1:
                claims_out.append(_blocked_claim(
                    claim, line_no, line, "price_claim_bound_to_multiple_stocks",
                ))
                continue

            code = bound_codes[0]
            rec = evidence[code]
            low, high = rec.get("allowed_low"), rec.get("allowed_high")
            if low is None or high is None:
                claims_out.append(_blocked_claim(
                    claim, line_no, line, "stock_has_no_price_evidence", code, rec,
                ))
                continue
            if not (low <= claim["value"] <= high):
                claims_out.append(_blocked_claim(
                    claim, line_no, line, "price_outside_stock_evidence_range", code, rec,
                ))
                continue
            claims_out.append({
                "line": line_no,
                "kind": claim["kind"],
                "value": claim["value"],
                "code": code,
                "name": rec.get("name") or None,
                "status": "passed",
                "reason": "stock_bound_price_evidence",
                "allowed_low": low,
                "allowed_high": high,
                "source_paths": [v["path"] for v in (rec.get("values") or [])[:8]],
                "excerpt": line.strip()[:240],
            })

    blocked = [claim for claim in claims_out if claim["status"] == "blocked"]
    return {
        "ok": not blocked,
        "checked": True,
        "status": "passed" if not blocked else "blocked",
        "validator": VALIDATOR_VERSION,
        "claim_count": len(claims_out),
        "blocked_count": len(blocked),
        "claims": claims_out[:50],
        "errors": [
            f"line {claim['line']}: {claim['reason']}"
            for claim in blocked[:20]
        ],
    }


def validate_report_finalization(content_md: str, data: dict, finish_reason: str | None = None) -> dict:
    """正式定稿的单一入口：截断报告先于所有其他检查被拒绝。"""
    if finish_reason == "length":
        return {
            "ok": False,
            "checked": True,
            "status": "blocked",
            "validator": VALIDATOR_VERSION,
            "claim_count": 0,
            "blocked_count": 1,
            "claims": [],
            "errors": ["report_truncated"],
        }
    return validate_report(content_md, data)
