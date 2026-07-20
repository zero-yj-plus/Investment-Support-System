"""方法论技能层 — 把 knowledge/methodology/*.md 的体系化方法论蒸馏成精炼的
system 提示片段，注入问股/研报 LLM 调用，让 AI 按维科夫/超股养家/龙头/情绪周期
等体系分析，而非泛泛而谈。

设计：
- 方法论 md 是"知识底座"，但小上下文模型直接吃整篇会挤占数据注入预算（prompts 侧
  数据注入上限 60k 字符、问答总预算 48k），故这里做"要点提取 + 长度封顶"。
- 提取策略：保留每篇的一级标题（体系名）、章节小标题（## 段名）、列表要点；
  丢弃 markdown 装饰（> 引语、表格分隔线、空行、强调星号），把要点压成短行。
- 结果按文件 mtime 缓存：方法论 md 几乎不变，每次 LLM 调用重读+重解析是浪费。
- 拿不到目录/为空 → 返回空串（降级为通用 PERSONA），绝不报错阻断主流程。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("hermes.methodology")

# 方法论 md 目录（本模块在 hermes_trader/analysis/，目录在 hermes_trader/knowledge/methodology/）
_METHOD_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "methodology"

# 注入片段总字符上限。PERSONA 已含一份精简的"高手判断规则库"(~1.4k)，方法论片段是其
# 体系化展开；中文 token 密度高，6 篇蒸馏后控制在 ~6k 字符（约对应安全 token 量），
# 既给足体系框架又不挤占数据注入与历史预算。超限按文件顺序截断并标注。
_MAX_CHARS = 6000

# 注入顺序：先宏观相位(情绪周期)→心法(养家)→个股梯队(龙头)→量价(维科夫)→择时技术→
# 用户个性化(经验库优先级最高放最后，紧贴结论)。文件名（不含 .md）→ 顺序权重。
_ORDER = {
    "sentiment-cycle": 0,
    "regime-factor-matrix": 1,      # 相位×因子侧重矩阵（白皮书吸收①）：紧跟情绪周期之后
    "chaoguyangjia-method": 2,
    "dragon-head-tactics": 2,
    "wyckoff-vsa": 3,
    "short-term-technical": 4,
    "my-trading-notes": 5,
}

# 解析结果缓存：key=目录下所有 md 的 (name, mtime, size) 指纹，命中即复用，
# 避免每次 LLM 调用重读磁盘+重解析。md 内容变更（mtime/size 变）会自动失效重建。
_cache: dict[tuple, str] = {}


def _dir_fingerprint(files: list[Path]) -> tuple:
    sig = []
    for p in files:
        try:
            st = p.stat()
            sig.append((p.name, int(st.st_mtime), st.st_size))
        except OSError:
            sig.append((p.name, 0, 0))
    return tuple(sig)


def _distill_one(text: str) -> tuple[str, list[str]]:
    """把一篇方法论 md 蒸馏成 (体系名, 要点行列表)。

    - 一级标题 `# xxx` → 体系名（去掉行内括号补充说明只保留主名更省字）。
    - `## 段名` → 作为一条"【段名】"要点，后续该段的列表项接在其后。
    - 列表项 `- ` / `1. ` → 去掉项目符号、去掉 **强调** 星号、压去多余空白成短行。
    - 跳过：> 引语（含本项目加的"方法论体系："导语）、表格行/分隔线、空行、纯描述段落。
      （表格仅出现在情绪周期相位表，其要点已在相邻列表/正文覆盖，丢弃不损信息。）
    """
    name = ""
    points: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# "):
            name = line[2:].strip()
            continue
        if line.startswith("> "):  # 引语/导语，跳过
            continue
        if line.startswith("|"):   # markdown 表格行/分隔线，跳过
            continue
        if line.startswith("## "):
            seg = line[3:].strip()
            points.append(f"【{seg}】")
            continue
        if line.startswith("### "):
            points.append(f"〔{line[4:].strip()}〕")
            continue
        # 列表项（含有序/无序、含中文顿号编号的子项）
        m = re.match(r"^(?:[-*+]|\d+[.)、])\s+(.*)$", line)
        if m:
            body = m.group(1)
            # 去掉 markdown 强调星号/反引号，压缩内部连续空白
            body = body.replace("**", "").replace("`", "")
            body = re.sub(r"\s+", " ", body).strip()
            if body:
                points.append(f"- {body}")
    return name, points


def _build() -> str:
    if not _METHOD_DIR.is_dir():
        log.warning("方法论目录不存在: %s（降级为通用人格）", _METHOD_DIR)
        return ""
    files = sorted(
        _METHOD_DIR.glob("*.md"),
        key=lambda p: (_ORDER.get(p.stem, 99), p.stem),
    )
    if not files:
        log.warning("方法论目录为空: %s（降级为通用人格）", _METHOD_DIR)
        return ""
    fp = _dir_fingerprint(files)
    cached = _cache.get(fp)
    if cached is not None:
        return cached

    blocks: list[str] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("读取方法论失败 %s: %s", p.name, e)
            continue
        name, points = _distill_one(text)
        if not name or not points:
            continue
        body = "\n".join(points)
        blocks.append(f"◆ {name}\n{body}")

    if not blocks:
        return ""

    header = (
        "\n\n【方法论体系（按以下体系化框架分析，而非泛泛而谈）】\n"
        "下方是蒸馏的短线交易方法论要点。分析问股/研报时按相应体系展开："
        "先用『情绪周期』给市场相位定位、据此定攻防与仓位，再用『炒股养家心法』判断合力与顺势，"
        "用『龙头战法』看个股在梯队中的位置，用『维科夫量价』判断主力在吸筹还是派发，"
        "用『短线技术』找买卖点与止损；『我的交易经验库』为用户个性化规则，优先级最高。"
        "方法论指导判断，但所有结论必须落在注入的真实数据上，不得用方法论替代数据（数据真实性铁律最高优先）。\n"
    )
    payload = header + "\n\n".join(blocks) + "\n"
    if len(payload) > _MAX_CHARS:
        payload = (
            payload[:_MAX_CHARS]
            + "\n…（方法论要点超长已截断，后段体系未全部注入，分析时以已注入要点为准）\n"
        )
    _cache[fp] = payload
    return payload


def load_methodology() -> str:
    """返回可直接拼进 system prompt 的方法论提示片段（已蒸馏+长度封顶+按 mtime 缓存）。

    供 analysis/engine.py 在问股/研报的 LLM 调用处，加进 system prompt
    （见 engine_inject 说明）。目录缺失/为空/全部读取失败时返回空串，
    调用方原样拼接即等于不注入，安全降级为通用人格。"""
    try:
        return _build()
    except Exception as e:  # noqa: BLE001 — 方法论注入绝不阻断主流程
        log.warning("方法论加载异常，降级为通用人格: %s", e)
        return ""
