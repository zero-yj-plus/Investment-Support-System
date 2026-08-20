"""面向客户消息渠道的报告关键摘要。

完整报告仍以 ``content_md`` 为唯一正式定稿；本模块只从已经通过治理的正文中
确定性摘取原文，不调用模型、不改写结论，也不把摘要写回报告记录。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


REPORT_PUSH_SUMMARY_MAX_BYTES = 8500
_SUMMARY_BODY_MAX_BYTES = 7200
_FALLBACK_BODY_MAX_BYTES = 4800

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_CODE_RE = re.compile(r"(?<!\d)(?:[0368]\d{5})(?!\d)")


@dataclass(frozen=True)
class _Section:
    level: int
    title: str
    body: str


# 顺序就是客户阅读顺序；预算按 UTF-8 字节计算，避免中文字符把飞书卡片撑成多片。
# 每一段都摘自正式报告原文，不能在这里补充或推断业务结论。
_PROFILE_RULES: dict[str, tuple[tuple[tuple[str, ...], int], ...]] = {
    "morning": (
        (("今日行动",), 3000),
        (("持仓处理",), 1200),
        (("验证条件",), 900),
        (("数据健康",), 650),
    ),
    "tail": (
        (("今天能不能做",), 1200),
        (("尾盘观察机会",), 2700),
        (("板块延续",), 1000),
        (("明日开盘先看",), 800),
    ),
    "close": (
        (("盘前总判断对账",), 1100),
        (("主线是否验证",), 1100),
        (("AI自我纠错", "自我纠错"), 900),
        (("持仓处理倾向", "持仓处理"), 1300),
        (("明日策略修正",), 1200),
    ),
    "evening": (
        (("明日基调",), 850),
        (("明日风险",), 850),
        (("明日仓位",), 600),
        (("明日重点方向",), 900),
        (("不能碰方向",), 750),
        (("确定性策略",), 1600),
        (("三种剧本",), 900),
    ),
}

_GENERIC_RULES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("结论", "判断", "行动"), 1500),
    (("风险", "不能碰", "不做"), 1200),
    (("仓位", "持仓处理"), 1200),
    (("策略", "机会", "方向", "验证"), 1800),
)

_RTYPE_BY_TITLE = {
    "早盘内参": "morning",
    "尾盘决策草案": "midday",
    "尾盘决策定稿": "tail",
    "收盘复盘": "close",
    "次日策略": "evening",
    "每日趋势票": "trend",
}

_REPORT_PUBLIC_HASHES = {
    "morning": "#/premarket?tab=report",
    "tail": "#/tail?tab=decision",
    "close": "#/review?tab=close",
    "evening": "#/review?tab=tomorrow",
    "trend": "#/history?tab=reports&section=trend",
}


def normalize_public_base_url(value: object) -> str:
    """校验并规范化部署方显式配置的客户访问根地址。

    定时推送没有可信的请求 Host，公网地址只能来自配置；只接受根路径的
    HTTP(S) URL，拒绝账号、查询和片段，避免把凭据或错误路由写进客户消息。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 2048 or any(ord(char) < 32 for char in text):
        raise ValueError("server.public_base_url 过长或包含控制字符")
    try:
        parsed = urlsplit(text)
        _ = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("server.public_base_url 不是有效 URL") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            "server.public_base_url 必须是 HTTP(S) 根地址，不得带账号、路径、查询或片段"
        )
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, "", "", "")).rstrip("/")


def build_report_public_url(rec: dict, public_base_url: object) -> str:
    """为报告生成客户 Web 原文地址；未配置或类型未知时不猜测。"""
    try:
        base = normalize_public_base_url(public_base_url)
    except ValueError:
        return ""
    route = _REPORT_PUBLIC_HASHES.get(_infer_rtype(rec))
    return f"{base}/{route}" if base and route else ""


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def _take_utf8(value: str, budget: int) -> str:
    if budget <= 0:
        return ""
    raw = value.encode("utf-8")
    if len(raw) <= budget:
        return value
    return raw[:budget].decode("utf-8", "ignore")


def _normalise_heading(value: str) -> str:
    value = re.sub(r"[*_`~]", "", value)
    value = re.sub(
        r"^\s*(?:[一二三四五六七八九十百]+|[0-9]+)\s*[、.．:：]\s*",
        "",
        value,
    )
    return re.sub(r"\s+", "", value).strip()


def _sections(markdown: str) -> list[_Section]:
    matches = list(_HEADING_RE.finditer(markdown or ""))
    result: list[_Section] = []
    for index, match in enumerate(matches):
        level = len(match.group(1))
        start = match.end()
        end = len(markdown)
        for following in matches[index + 1:]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        result.append(_Section(
            level=level,
            title=match.group(2).strip(),
            body=markdown[start:end].strip(),
        ))
    return result


def _trim_markdown(value: str, budget: int) -> tuple[str, bool]:
    """在行边界精简 Markdown；超长单行按 UTF-8 安全截取。"""
    text = (value or "").strip()
    if _utf8_size(text) <= budget:
        return text, False

    suffix = "\n\n> 本节其余内容请在完整报告中查看。"
    content_budget = max(0, budget - _utf8_size(suffix))
    kept: list[str] = []
    used = 0
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        addition = (("\n" if kept else "") + line)
        size = _utf8_size(addition)
        if used + size <= content_budget:
            kept.append(line)
            used += size
            continue
        remaining = content_budget - used - (_utf8_size("\n") if kept else 0)
        # 表格数据行不能截成半行，否则股票、价格和条件可能错列；宁可整行留到完整报告。
        if (
            remaining > 12
            and line
            and not line.lstrip().startswith("|")
            and not re.match(r"^\s*:?-{3,}", line)
        ):
            prefix = _take_utf8(line, max(0, remaining - _utf8_size("…"))).rstrip()
            if prefix:
                kept.append(prefix + "…")
        break
    compact = "\n".join(kept).rstrip()
    return (compact + suffix).strip(), True


def _select_by_rules(
    sections: list[_Section],
    rules: tuple[tuple[tuple[str, ...], int], ...],
) -> list[tuple[_Section, int]]:
    selected: list[tuple[_Section, int]] = []
    used: set[int] = set()
    for terms, budget in rules:
        for index, section in enumerate(sections):
            if index in used or section.level == 1:
                continue
            normalised = _normalise_heading(section.title)
            if any(term.lower() in normalised.lower() for term in terms):
                selected.append((section, budget))
                used.add(index)
                break
    return selected


def _trend_sections(sections: list[_Section]) -> list[tuple[_Section, int]]:
    candidates = [
        section for section in sections
        if section.level == 2 and (
            _CODE_RE.search(section.title)
            or re.match(r"^\s*[一二三四五六七八九十0-9]+[.、．]", section.title)
        )
    ]
    if not candidates:
        candidates = [section for section in sections if section.level == 2]
    return [(section, 1800) for section in candidates[:3]]


def _infer_rtype(rec: dict) -> str:
    explicit = str(rec.get("rtype") or "").strip().lower()
    if explicit:
        return explicit
    title = str(rec.get("title") or "")
    for marker, rtype in _RTYPE_BY_TITLE.items():
        if marker in title:
            return rtype
    return ""


def build_report_push_summary(rec: dict) -> str:
    """从正式报告原文构造单卡片级客户摘要。

    结构化报告按报告类型选取最重要章节；旧报告或异常结构只发送有界节选，
    绝不因结构未识别而回退为全量长文。
    """
    content = str(rec.get("content_md") or "").strip()
    rtype = _infer_rtype(rec)
    sections = _sections(content)

    if rtype == "trend":
        selected = _trend_sections(sections)
    else:
        selected = _select_by_rules(sections, _PROFILE_RULES.get(rtype, _GENERIC_RULES))
        if not selected and sections:
            selected = _select_by_rules(sections, _GENERIC_RULES)

    blocks: list[str] = []
    used_body_bytes = 0
    for section, section_budget in selected:
        remaining = _SUMMARY_BODY_MAX_BYTES - used_body_bytes
        if remaining <= 160:
            break
        body, _ = _trim_markdown(section.body, min(section_budget, remaining))
        if not body:
            continue
        block = f"## {section.title}\n{body}".strip()
        blocks.append(block)
        used_body_bytes += _utf8_size(block) + 2

    if not blocks:
        excerpt, _ = _trim_markdown(content or "报告正文为空。", _FALLBACK_BODY_MAX_BYTES)
        blocks = [f"## 报告关键节选\n{excerpt}"]

    intro = (
        "> **客户推送摘要**：仅保留关键结论、动作边界与风险；"
        "完整报告和证据链请在系统“报告中心”查看。"
    )
    version = rec.get("plan_version") or rec.get("report_version") or 1
    footer = "完整报告：系统报告中心"
    if rec.get("content_hash"):
        footer += f" · 内容版本 v{version} · {str(rec['content_hash'])[:12]}"
    summary = f"{intro}\n\n" + "\n\n".join(blocks) + f"\n\n---\n{footer}"
    if _utf8_size(summary) > REPORT_PUSH_SUMMARY_MAX_BYTES:
        summary, _ = _trim_markdown(summary, REPORT_PUSH_SUMMARY_MAX_BYTES)
    return summary.strip()
