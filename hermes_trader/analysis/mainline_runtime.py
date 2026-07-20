"""当前交易日行业主线的共享运行时组装。

API 和后台 10 分钟采样任务共用这一路径，避免「页面算一套、后台存另一套」。
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import re
import time

from ..data import free_source as public_data
from ..data import market
from ..data import news
from ..data import tdx_api_source as tdx_api
from ..data import tencent_source as tencent_data
from ..data.base import now_cn
from . import mainline as mainline_mod
from . import screener


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CURRENT_SNAPSHOT_FILE = "mainline_current.json"


def save_current_snapshot(data_dir, response: dict) -> Path | None:
    """持久化后台采样得到的完整主线响应，供页面首屏毫秒级读取。"""
    if not isinstance(response, dict) or not response.get("data_date"):
        return None
    try:
        path = Path(data_dir) / CURRENT_SNAPSHOT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"saved_epoch": time.time(), "response": response}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path
    except Exception:  # noqa: BLE001 -- 快照是性能层，失败不得影响主线采样
        return None


def load_current_snapshot(data_dir, *, limit: int = 5) -> dict | None:
    """读取最近一次完整采样；陈旧数据会明标，不冒充实时。"""
    try:
        payload = json.loads(
            (Path(data_dir) / CURRENT_SNAPSHOT_FILE).read_text(encoding="utf-8")
        )
        response = payload.get("response")
        if not isinstance(response, dict) or not response.get("data_date"):
            return None
        result = deepcopy(response)
        result["roles"] = list(result.get("roles") or [])[: max(1, min(int(limit), 30))]
        age = max(0, int(time.time() - float(payload.get("saved_epoch") or 0)))
        result["snapshot_age"] = age
        result["cache_state"] = "sampled"
        if age > 15 * 60:
            result["stale"] = True
            missing = list(result.get("missing") or [])
            missing.append(f"主线快照距上次成功采样约{age // 60}分钟，后台正在更新")
            result["missing"] = list(dict.fromkeys(missing))
        return result
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _dated_sector_payload() -> dict:
    """为行业横截面补充与涨停池同口径的 ``data_date``。"""
    payload = dict(market.market_sectors() or {})
    payload["missing"] = list(payload.get("missing") or [])
    try:
        _dc, data_date, note = market._resolve_data_day()  # noqa: SLF001 -- 与数据层同口径
    except Exception:  # noqa: BLE001 -- 无日期就由规则层拒绝混算
        data_date, note = None, None
    if data_date:
        payload["data_date"] = data_date
    if note and note not in payload["missing"]:
        payload["missing"].append(note)
    return payload


def _industry_payload() -> dict:
    """优先使用与 TDX 行业指数同体系的成分关系。"""
    tdx_map = tdx_api.stock_industry_map()
    if tdx_map:
        return tdx_map
    return screener._stock_basic_map() or {}  # noqa: SLF001 -- 唯一旧静态兜底


async def build_current_response(limit: int = 5) -> dict:
    """并行取当日真实输入，组装无时间轴副作用的主线截面。"""
    sector_payload, limitup_payload, industry_map, market_context, catalyst_payload = await asyncio.gather(
        asyncio.to_thread(_dated_sector_payload),
        asyncio.to_thread(market.market_limitup),
        asyncio.to_thread(_industry_payload),
        asyncio.to_thread(market.market_overview),
        asyncio.to_thread(news.cn_news_aggregated, 120),
    )

    data_date = str((limitup_payload or {}).get("data_date") or "")
    limit_down_rows = None
    limit_down_missing: list[str] = []
    if _DATE_RE.fullmatch(data_date):
        limit_down_rows = await asyncio.to_thread(
            public_data.limit_list, data_date.replace("-", ""), "D"
        )
        if limit_down_rows is None:
            scan = None
            if data_date == now_cn().date().isoformat():
                scan = await asyncio.to_thread(tencent_data.market_zt_scan)
            scanned_rows = scan.get("ld_list") if isinstance(scan, dict) else None
            if isinstance(scanned_rows, list):
                limit_down_rows = scanned_rows
                if scanned_rows:
                    limit_down_missing.append(
                        f"跌停名单来自腾讯全市场同一时点扫描({scan.get('asof')})；"
                        "首封时间、封单与开板次数待公开池补齐"
                    )
            else:
                limit_down_missing.append(
                    "跌停明细不可用：公开池与腾讯全市场扫描均未返回完整名单；"
                    "limit_down_pool留空，未按家数猜股票"
                )
    else:
        limit_down_missing.append("跌停明细缺data_date，limit_down_pool留空")

    return mainline_mod.build_mainline_response(
        sector_payload or {},
        limitup_payload or {},
        industry_map or {},
        market_context=market_context or {},
        limit_down_rows=limit_down_rows,
        limit_down_missing=limit_down_missing,
        catalyst_payload=catalyst_payload or {},
        limit=max(1, min(int(limit), 30)),
    )
