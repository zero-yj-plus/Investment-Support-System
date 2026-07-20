"""评分定格 —— 把「评分/结论类」数字按时间片(slot)算一次、锁定，同一片内所有请求返回同一份，
杜绝"盯着看就跳"（情绪评分/驾驶舱风险每十几秒变一次＝显得像编的）。

收编自 mike(mikeabcd3488) 工作区 2026-07-10 的 score_lock 范式（原子落盘 os.replace +
asyncio.Lock 并发锁 + 拿锁前后 double-check），按 T9 §0-1/§0-3 改良三点：
1) 档粒度 10 分钟（T9「盘面快照每 10 分钟」原文节奏；mike 用 30 分钟，10 更新鲜且足以治跳）；
2) compute 接收【上一档快照 prev】——供 T9 §0-3 防跳变两机制落地：观点惯性(change 字段)
   + 等级阻尼(相对上一版最多 ±1)；mike 版 compute 无参、只锁不惯性；
3) 跨日 prev：当日无档时回退昨日最后一档（隔夜也有"上一版"可比，不至于每天早上凭空重启）。

铁律：实时数据(现价/盈亏/指数)照旧实时刷、绝不锁；只把「评分/结论」定格。定格值明标
`定格时点`，前端显示"定格 HH:MM"（诚实、不冒充实时）。compute 失败/降级(ok=False)不落快照、
不锁坏值，本次原样返回、下次重试。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Awaitable, Callable

from ..data.base import now_cn

log = logging.getLogger("hermes.score_lock")

_RETENTION_DAYS = 14   # score_snap/<date>/ 目录保留天数（评分快照只服务惯性/阻尼，无长期价值）

_LOCKS: dict[str, asyncio.Lock] = {}


def _slot(now=None) -> tuple[str, str]:
    """当前时间片 (slot_id, 展示标签)。交易时段 10 分钟一档（09:30/09:40/…/14:50）；盘前/盘后各一档。"""
    n = now or now_cn()
    hm = n.hour * 60 + n.minute
    if hm < 9 * 60 + 25:
        return "pre", "盘前"
    if hm >= 15 * 60:
        return "close", "收盘"
    m = max(9 * 60 + 30, (hm // 10) * 10)
    return f"{m // 60:02d}{m % 60:02d}", f"{m // 60:02d}:{m % 60:02d}"


def _path(root, date: str, kind: str) -> Path:
    d = Path(root) / "score_snap" / date
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{kind}.json"


def _read(p: Path):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _atomic_write(p: Path, obj: dict) -> None:
    try:
        tmp = str(p) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, p)                     # 原子落盘：半截文件永不被读到
    except Exception:  # noqa: BLE001
        # 2026-07-11 审计 M4 修复：落盘失败绝不再静默——定格持久化失效会让每次请求重算
        # （narrate 档反复触发 AI 调用）且无人知晓。告警但不抛（定格失败不拖垮接口本身）。
        log.warning("评分定格落盘失败（%s）——本档将无法持久化、下次请求会重算", p, exc_info=True)


def _read_prev(root, kind: str, date: str):
    """跨日上一档：当日文件不存在时，往前回溯最近一个有快照的日期（最多 7 天——覆盖周末/
    小长假，周一早上能取到上周五的档做惯性/阻尼 prev；2026-07-11 审计 P2 修复，原先恒取
    now-1d，周一会读周日空档导致隔夜惯性失效）。"""
    for back in range(1, 8):
        y = (now_cn() - timedelta(days=back)).strftime("%Y-%m-%d")
        snap = _read(_path(root, y, kind))
        if snap:
            return snap
    return None


def _purge_old(root) -> None:
    """清理超过 _RETENTION_DAYS 的 score_snap/<date>/ 目录（审计 P2：原先无 retention 无限增长）。
    幂等、失败静默但记日志；只删本模块自己的目录，绝不碰 reports/ 等其他数据。"""
    try:
        base = Path(root) / "score_snap"
        if not base.is_dir():
            return
        cutoff = (now_cn() - timedelta(days=_RETENTION_DAYS)).strftime("%Y-%m-%d")
        for d in base.iterdir():
            if d.is_dir() and len(d.name) == 10 and d.name < cutoff:
                shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001
        log.warning("score_snap 过期清理失败（不影响定格功能）", exc_info=True)


_INFLIGHT: dict[str, bool] = {}


async def locked_swr(root, kind: str, compute: Callable[[dict | None], Awaitable[dict]]) -> dict | None:
    """秒回 + 后台重算（T9 §0-2 A方案，供 AI 叙述这类慢计算用）：
    当前档已定格 → 直接返回；过期 → 立刻返回上一份（标 stale=True，绝不让客户等白屏），
    同时后台起任务算新档（同 kind 全局只允许一个 in-flight，绝不堆积并发 AI 调用）；
    从未有过任何快照 → 本次内联算一次（首建，只发生在部署后第一次）。"""
    now = now_cn()
    date = now.strftime("%Y-%m-%d")
    slot, _label = _slot(now)
    snap = _read(_path(root, date, kind))
    if snap and snap.get("_slot") == slot:
        return snap
    prev = snap or _read_prev(root, kind, date)
    if prev:
        if not _INFLIGHT.get(kind):
            _INFLIGHT[kind] = True

            async def _bg():
                try:
                    await locked(root, kind, compute)
                except Exception:  # noqa: BLE001 —— 后台重算失败静默，旧份继续用，下轮再试
                    pass
                finally:
                    _INFLIGHT[kind] = False

            asyncio.create_task(_bg())
        out = dict(prev)
        out["stale"] = True          # 明标：这是上一档的叙述，新档正在后台生成
        return out
    return await locked(root, kind, compute)


async def locked(root, kind: str, compute: Callable[[dict | None], Awaitable[dict]]) -> dict:
    """取当前时间片的定格评分；无则算一次、原子落盘、锁定后返回。
    compute(prev): async，prev=上一档定格快照(当日旧档 → 昨日末档 → None)，供观点惯性/等级阻尼。
    只在换档/首次真正跑一次，档内直接读快照（刷新不变）。compute 失败/ok=False 不定格坏值。"""
    now = now_cn()
    date = now.strftime("%Y-%m-%d")
    slot, label = _slot(now)
    p = _path(root, date, kind)
    snap = _read(p)
    if snap and snap.get("_slot") == slot:
        return snap                            # 同档已定格 → 原样返回（刷新不变）
    lock = _LOCKS.setdefault(kind, asyncio.Lock())
    async with lock:
        snap = _read(p)                        # double-check：并发只让第一个真算、其余读快照
        if snap and snap.get("_slot") == slot:
            return snap
        prev = snap if snap else _read_prev(root, kind, date)   # 当日旧档优先，无则昨日末档
        result = await compute(prev)
        if not isinstance(result, dict) or result.get("ok") is False:
            return result                      # 算失败/降级 → 不定格坏值，本次原样返回
        out = dict(result)
        out["_slot"] = slot
        out["定格时点"] = label
        out["定格"] = True
        _atomic_write(p, out)
        _purge_old(root)          # 换档写盘时顺手清理过期快照（每档一次，开销可忽略）
        return out
