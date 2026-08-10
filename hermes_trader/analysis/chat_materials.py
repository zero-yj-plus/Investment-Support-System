"""自由问答数据材料目录、确定性预选与结果契约。"""
from __future__ import annotations

from collections.abc import Iterable
import re


MAX_AUTO_SELECTED = 3
MAX_SELECTED = 4

MATERIALS: tuple[dict, ...] = (
    {
        "id": "technical",
        "name": "深度技术与量价",
        "description": "趋势、均线、量价、关键位置、技术结构",
        "availability": "supported",
        "slow": False,
    },
    {
        "id": "fund_flow",
        "name": "个股资金流",
        "description": "主力净额、资金日期、来源和可用明细",
        "availability": "supported",
        "slow": False,
    },
    {
        "id": "sector",
        "name": "板块与题材",
        "description": "行业、概念、板块联动和题材归属",
        "availability": "supported",
        "slow": False,
    },
    {
        "id": "market_context",
        "name": "市场环境",
        "description": "指数、市场宽度、情绪、主线和市场背景",
        "availability": "supported",
        "slow": False,
    },
    {
        "id": "fundamental",
        "name": "基本面与估值",
        "description": "PE、PB、ROE、利润、负债及报告期",
        "availability": "supported",
        "slow": False,
    },
    {
        "id": "events",
        "name": "公告与事件",
        "description": "官方公告、互动问答、新闻事件和证据等级",
        "availability": "supported",
        "slow": True,
    },
)

REQUIRED_MATERIALS: tuple[dict, ...] = (
    {"id": "quote_time", "name": "行情与数据时点"},
    {"id": "risk_boundary", "name": "风险边界与失效条件"},
    {"id": "quality_state", "name": "缺失、过期和降级状态"},
)

_BY_ID = {item["id"]: item for item in MATERIALS}


class DataMaterialSelectionError(ValueError):
    """请求中的材料选择违反服务端白名单或数量上限。"""


def public_materials() -> list[dict]:
    return [dict(item) for item in MATERIALS]


def _contains(text: str, words: Iterable[str]) -> bool:
    return any(word in text for word in words)


def preselect_material_ids(message: str) -> tuple[str, ...]:
    """纯规则预选；顺序、上限和关键词均固定，不依赖模型。"""
    text = str(message or "").strip().lower()
    if not text:
        return ()

    # 两个组合意图优先，避免普通关键词把最相关的三项挤出上限。
    why_move = bool(re.search(r"(?:为什么|为何|原因).{0,12}(?:上涨|下跌|涨|跌)", text))
    if why_move:
        return ("technical", "fund_flow", "sector")
    if (
        ("进攻" in text and "防守" in text)
        or _contains(text, ("攻守", "风险偏好"))
    ):
        return ("market_context", "sector")

    selected: list[str] = []

    def add(material_id: str, words: tuple[str, ...]) -> None:
        if len(selected) < MAX_AUTO_SELECTED and _contains(text, words):
            selected.append(material_id)

    add("technical", (
        "行情", "走势", "趋势", "支撑", "压力", "买点", "卖点", "量价",
        "均线", "macd", "rsi", "k线", "分时", "涨", "跌",
    ))
    add("fund_flow", ("资金", "主力", "流入", "流出", "净额"))
    add("sector", ("板块", "题材", "主线", "联动", "行业", "概念"))
    add("market_context", (
        "大盘", "情绪", "市场风险", "市场环境", "赚钱效应", "宽度", "进攻", "防守",
    ))
    add("fundamental", (
        "估值", "业绩", "长期", "基本面", "市盈率", "市净率", "pe", "pb", "roe",
        "利润", "负债",
    ))
    add("events", (
        "公告", "消息", "事件", "利好", "利空", "新闻", "互动问答", "互动易",
    ))
    return tuple(selected[:MAX_AUTO_SELECTED])


def resolve_material_ids(value, *, message: str = "") -> tuple[str, ...]:
    """规范化用户最终选择；省略字段时使用服务端预选。"""
    if value is None:
        return preselect_material_ids(message)
    if not isinstance(value, list):
        raise DataMaterialSelectionError("data_material_ids 必须是数组")
    selected: list[str] = []
    for raw in value:
        material_id = str(raw or "").strip()
        if material_id not in _BY_ID:
            raise DataMaterialSelectionError(f"未知数据材料：{material_id or '空值'}")
        if material_id not in selected:
            selected.append(material_id)
    if len(selected) > MAX_SELECTED:
        raise DataMaterialSelectionError(f"每轮最多选择 {MAX_SELECTED} 个可选数据材料")
    return tuple(selected)


def empty_report(selected_ids: Iterable[str]) -> dict:
    selected = tuple(selected_ids)
    return {
        "selected_ids": list(selected),
        "max_selected": MAX_SELECTED,
        "model_used": False,
        "required": [
            {
                **item,
                "state": "pending",
                "injected": False,
                "reason": "等待采集",
            }
            for item in REQUIRED_MATERIALS
        ],
        "optional": [
            {
                "id": item["id"],
                "name": item["name"],
                "selected": item["id"] in selected,
                "state": "pending" if item["id"] in selected else "not_selected",
                "injected": False,
                "data_date": None,
                "source": None,
                "stale": None,
                "reason": "等待采集" if item["id"] in selected else "用户本轮未选择",
            }
            for item in MATERIALS
        ],
    }


def update_result(report: dict, material_id: str, **fields) -> None:
    for item in report.get("optional") or []:
        if item.get("id") == material_id:
            item.update({key: value for key, value in fields.items() if value is not None})
            return


def finalize_report(report: dict, *, model_used: bool) -> dict:
    """只有真实进入模型上下文的材料才标记 injected。"""
    report["model_used"] = bool(model_used)
    for item in report.get("required") or []:
        if item.get("state") == "pending":
            item["state"] = "missing"
            item["reason"] = "系统必需底座未返回可核验结果"
        item["injected"] = bool(model_used and item.get("state") in {"complete", "degraded"})
    for item in report.get("optional") or []:
        if not item.get("selected"):
            item["state"] = "not_selected"
            item["injected"] = False
            continue
        state = str(item.get("state") or "missing")
        if state == "pending":
            item["state"] = "missing"
            item["reason"] = "采集任务未返回可用结果"
            state = "missing"
        item["injected"] = bool(model_used and state in {"complete", "degraded"})
    return report


def sanitize_report(value) -> dict | None:
    """把客户端回传的结果压缩为可安全留存、可显示的材料契约。"""
    if not isinstance(value, dict):
        return None
    valid_states = {"pending", "complete", "degraded", "missing", "not_selected"}

    def clean_rows(rows, definitions: tuple[dict, ...], *, optional: bool) -> list[dict]:
        incoming = {
            str(item.get("id") or ""): item
            for item in (rows if isinstance(rows, list) else [])
            if isinstance(item, dict)
        }
        cleaned: list[dict] = []
        for definition in definitions:
            row = incoming.get(definition["id"]) or {}
            state = str(row.get("state") or ("not_selected" if optional else "missing"))
            if state not in valid_states:
                state = "missing"
            item = {
                "id": definition["id"],
                "name": definition["name"],
                "state": state,
                "injected": bool(row.get("injected")),
                "data_date": str(row.get("data_date") or "")[:40] or None,
                "source": str(row.get("source") or "")[:120] or None,
                "stale": row.get("stale") if isinstance(row.get("stale"), bool) else None,
                "reason": str(row.get("reason") or "")[:240] or None,
            }
            if optional:
                item["selected"] = bool(row.get("selected"))
            cleaned.append(item)
        return cleaned

    optional = clean_rows(value.get("optional"), MATERIALS, optional=True)
    selected_ids = [item["id"] for item in optional if item.get("selected")][:MAX_SELECTED]
    return {
        "selected_ids": selected_ids,
        "max_selected": MAX_SELECTED,
        "model_used": bool(value.get("model_used")),
        "required": clean_rows(value.get("required"), REQUIRED_MATERIALS, optional=False),
        "optional": optional,
    }
