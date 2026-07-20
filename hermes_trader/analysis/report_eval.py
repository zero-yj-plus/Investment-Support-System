"""研报数字质检 —— 报告文本中的数字反查注入数据（铁律的自动闸，v1 只标注不拦截）。

原则：**宁漏勿误报**。只检"有决策分量"的数字（≥1000，或带小数且 ≥10——价格/亿元级），
序号/小百分比/年份天然豁免；数据侧为每个数值生成「元↔万↔亿」双向候选（报告写 48.76亿、
注入是 4875970000 元，视为同一数）。可疑数字写回报告记录（eval 字段）+ 日志告警 +
哨兵日终汇总，供人工复核——**绝不自动改写报告**（当日定稿铁律）。"""
from __future__ import annotations

import re

_NUM = re.compile(r"\d+(?:\.\d+)?")
_YEAR = re.compile(r"20\d{2}")


def _key(v: float) -> str:
    """数值归一为比较键：四舍五入 2 位、去尾零（48.760→'48.76'，1489.00→'1489'）。"""
    return f"{round(v, 2):.2f}".rstrip("0").rstrip(".")


def _variants(v: float) -> set[str]:
    """一个数据值 → 元/万/亿 双向换算候选键（|值|≥10 的才有比较意义）。"""
    out: set[str] = set()
    for x in (v, v / 1e4, v / 1e8, v * 1e4, v * 1e8):
        try:
            if abs(x) >= 10:
                out.add(_key(x))
        except (OverflowError, ValueError):
            continue
    return out


def _collect(obj, acc: set[str]) -> None:
    """递归收集注入数据里的全部数值（含字符串内嵌数字，如 '48.76亿元'/'2026-07-03'）。"""
    if isinstance(obj, dict):
        for v in obj.values():
            _collect(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect(v, acc)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        acc |= _variants(float(obj))
    elif isinstance(obj, str):
        for m in _NUM.findall(obj):
            try:
                acc |= _variants(float(m))
            except ValueError:
                continue


def evaluate(content_md: str, data: dict) -> dict:
    """报告正文 vs 注入数据 → {checked, suspect_count, suspects[]}（最多 20 个可疑数字）。
    可疑 = 报告里出现、注入数据（含单位换算候选）里找不到的"有分量"数字。"""
    pool: set[str] = set()
    _collect(data or {}, pool)
    suspects: list[str] = []
    for m in _NUM.finditer(content_md or ""):
        tok = m.group(0)
        try:
            f = float(tok)
        except ValueError:
            continue
        # 只检有分量的数字：≥1000，或带小数且 ≥10（股价/亿元/万元级）；其余（序号/涨跌幅小数/成数）豁免
        if not (f >= 1000 or ("." in tok and f >= 10)):
            continue
        if _YEAR.fullmatch(tok):          # 年份豁免
            continue
        if _key(f) in pool:
            continue
        if tok not in suspects:
            suspects.append(tok)
        if len(suspects) >= 20:
            break
    return {"checked": True, "suspect_count": len(suspects), "suspects": suspects}
