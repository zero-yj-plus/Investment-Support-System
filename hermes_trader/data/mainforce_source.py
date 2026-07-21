"""主力吸筹数据源：无需 token 的公开盘后资金动向。

定位：盯盘看「实时谁在买」之外，盘后用确定性 REST 看「主力沉淀去了哪」——
  ① 北向资金 moneyflow_hsgt —— 沪深港通每日北向(陆股通)/南向(港股通)净流入
  ② 龙虎榜 top_list + top_inst —— 个股上榜净买额、机构席位净买
  ③ 行业资金流：同花顺免费即时口径（兼容函数名 moneyflow_ind_dc）

三铁律落地：
- 只改写真实取到的字段；取不到 → 进 missing，绝不编造、绝不给错值。
- 盘后数据：moneyflow_hsgt/limit 类接口在盘中/当日未结算前常为空 → 取最近交易日并标注
  data_date 与提示；空集返回 ok=True + 空列表 + missing，绝不抛、绝不拿昨天冒充今天。
- 单位（P0 高发区，必核）：
    * （北向 moneyflow_hsgt 已停用：north/south 实为沪深港通成交额非净流入、且 2024-08 起停披露，见 northbound()）；
    * 龙虎榜 top_list 的 net_amount / amount / l_buy / l_sell 等单位为【元】，不转；
    * 龙虎榜 top_inst 的 buy / sell / net_buy 单位为【元】(实测单席净买达数亿元、与 top_list 同口径)，不转——误×1e4 刷成万亿假值；
    * moneyflow_ind_dc / moneyflow_ind_ths 的资金/成交字段单位为【元】，不转。
  （金额单位必须按供应方字段口径校验：漏乘或重复换算都会给交易员错价。）

所有函数失败/空均返回 ok=True+空并标缺，绝不抛、绝不把成交额冒充净流入。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .base import (
    get_ak,
    safe,
    to_float,
    to_int,
    ts_str,
    ttl_cache,
    now_cn,
    is_trade_day,
    prev_trade_day,
)
from . import free_source, official_longhu_source


# ---------------------------------------------------------------- 东财龙虎榜快照
# 与官方快照分离（source=eastmoney，按 trade_date + completeness），使"今日结算中/源异常"时
# 能显眼回退展示上一交易日的东财完整榜，而不是清空或拿今日空数据冒充。
_LONGHU_DATA_ROOT: Path | None = None


def init(data_dir: str | Path) -> None:
    """配置客户隔离的东财龙虎榜快照目录（main.py 启动时调用）。"""
    global _LONGHU_DATA_ROOT  # noqa: PLW0603 - 进程只加载一个客户
    _LONGHU_DATA_ROOT = Path(data_dir) / "eastmoney-longhu"


def _lh_snapshot_path(data_date: str) -> Path | None:
    return (_LONGHU_DATA_ROOT / f"{data_date}.json") if _LONGHU_DATA_ROOT is not None else None


def _save_longhu_snapshot(data_date: str, rows: list[dict], inst: list[dict]) -> None:
    """原子保存东财当日完整榜；空榜不落盘、不覆盖已有完整快照。"""
    path = _lh_snapshot_path(data_date or "")
    if path is None or not data_date or not rows:
        return
    payload = {
        "source": "eastmoney",
        "trade_date": data_date,
        "completeness": "complete",
        "saved_at": ts_str(),
        "list": rows,
        "inst": inst,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False)
            tmp = Path(handle.name)
        tmp.replace(path)
    except OSError:
        pass  # 快照落盘失败不影响实时返回


def _load_latest_longhu_snapshot(before_date: str | None = None) -> dict | None:
    """读取最近一份东财完整榜快照（trade_date ≤ before_date）；无则 None。"""
    if _LONGHU_DATA_ROOT is None or not _LONGHU_DATA_ROOT.is_dir():
        return None
    for path in sorted(_LONGHU_DATA_ROOT.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("completeness") != "complete":
            continue
        td = str(payload.get("trade_date") or "")
        if before_date and td > before_date:
            continue
        if isinstance(payload.get("list"), list):
            return payload
    return None


# ---------------------------------------------------------------- 取数日解析

def _resolve_post_close_day(date_compact: str | None = None):
    """解析盘后数据的归属交易日（北向/龙虎榜/行业资金流均为收盘后结算）。
    返回 (compact=YYYYMMDD, data_date=ISO, note 或 None)。

    与 market._resolve_data_day 的差异：这些是【盘后】数据，当日盘中乃至收盘后一段时间
    免费盘后公开数据尚未结算 → 当日 15:30 前一律取上一交易日，避免空集被误当『今日无资金动向』。
    显式传入 date_compact 时直接采信（供历史回看/复盘）。"""
    if date_compact:
        iso = f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}"
        return date_compact, iso, None
    now = now_cn()
    today = now.date()
    # 盘后数据通常 15:30 后才陆续结算入库；之前一律用上一交易日。
    settled = now.hour * 60 + now.minute >= 15 * 60 + 30
    if is_trade_day(today) and settled:
        return today.strftime("%Y%m%d"), today.isoformat(), None
    prev = prev_trade_day(today)
    if not prev:
        # 拿不到日历：退化用今天，不阻断（接口空则照常标 missing）
        return today.strftime("%Y%m%d"), today.isoformat(), None
    note = (f"盘后数据尚未结算，以下为上一交易日({prev})"
            if is_trade_day(today)
            else f"非交易日，以下为最近交易日({prev})")
    return prev.replace("-", ""), prev, note


def available() -> bool:
    """免费数据运行时是否就绪。"""
    return free_source.available()


def _a_share_codes(rows: list[dict], key: str = "code") -> set[str]:
    """只提取可用于代码集核验的沪深 A 股代码。

    这里故意不读取任何金额字段：官方与东财的统计口径不同，
    当前集成只能证明“这只股是否出现在当日龙虎榜”。
    """
    codes: set[str] = set()
    for row in rows:
        code = str(row.get(key) or "").strip()
        if len(code) == 6 and code.isdigit() and code.startswith(("0", "3", "6")):
            codes.add(code)
    return codes


def _official_verification(
    data_date: str,
    eastmoney_rows: list[dict],
    *,
    eastmoney_available: bool,
) -> dict:
    """用已落盘官方快照核验东财龙虎榜代码集。

    严禁在用户请求链发起官网请求；无快照时保持“不可核验”，
    不把东财代码误标成 eastmoney_only。
    """
    snapshot = official_longhu_source.load_snapshot(data_date)
    snapshot_events = (snapshot or {}).get("events") or []
    # 恢复策略：今日官方快照缺失/空(settling/损坏) → 回退最近完整交易日，明确标日期，
    # 不拿今日空冒充"当日无榜"，也不做今日 vs 上一日的误导性差异分类。
    recovered = None
    if not snapshot_events:
        recovered = official_longhu_source.load_latest_complete_snapshot(before_date=data_date)
    eastmoney_codes = _a_share_codes(eastmoney_rows)
    official_codes = _a_share_codes(snapshot_events)
    recovered_events = (recovered or {}).get("events") or []
    cache = {
        "available": snapshot is not None,
        "state": str((snapshot or {}).get("state") or "not_cached"),
        "trade_date": data_date,
        "fetched_at": (snapshot or {}).get("fetched_at"),
        "availability_reason": (
            (snapshot or {}).get("availability_reason")
            or "指定交易日的官方龙虎榜快照尚未落盘"
        ),
        "event_count": len(snapshot_events),
        "code_count": len(official_codes),
        # 恢复回退：今日空/损坏时展示的上一完整交易日快照日期与规模（供前端显眼标注）
        "recovered_from_date": (recovered.get("trade_date") if recovered else None),
        "recovered_code_count": len(_a_share_codes(recovered_events)),
    }
    result = {
        "comparison_basis": "code_set_only",
        "amounts_compared": False,
        "comparison_state": "unavailable",
        "classification_scope": "unavailable",
        "official_cache": cache,
        "eastmoney_available": bool(eastmoney_available),
        "eastmoney_code_count": len(eastmoney_codes),
        "matched": [],
        "eastmoney_only": [],
        "official_only": [],
        "counts": {"matched": 0, "eastmoney_only": 0, "official_only": 0},
    }
    # 今日有完整/降级(有事件)快照 → 正常代码集差异分类。
    if (
        eastmoney_available
        and snapshot_events
        and cache["state"] in {"complete", "degraded", "settling"}
    ):
        matched = sorted(eastmoney_codes & official_codes)
        eastmoney_only = sorted(eastmoney_codes - official_codes)
        official_only = sorted(official_codes - eastmoney_codes)
        result.update({
            "comparison_state": "complete" if cache["state"] == "complete" else "partial",
            "classification_scope": "confirmed" if cache["state"] == "complete" else "provisional",
            "matched": matched,
            "eastmoney_only": eastmoney_only,
            "official_only": official_only,
            "counts": {
                "matched": len(matched),
                "eastmoney_only": len(eastmoney_only),
                "official_only": len(official_only),
            },
        })
        return result
    # 今日空/损坏但有回退完整快照 → 明确"参考上一完整交易日"，不做今日差异分类。
    if recovered is not None:
        result.update({
            "comparison_state": "reference_prior_day",
            "classification_scope": "reference_only",
            "availability_note": (
                f"官方今日结算中/未发布，展示上一完整交易日（{recovered.get('trade_date')}）"
                "官方快照供参考，不做今日差异分类"
            ),
        })
    return result


# ---------------------------------------------------------------- ① 北向资金

def northbound(date_compact: str | None = None) -> dict:
    """【已停用·数据真实性铁律】北向资金净流入实时/日度数据自 2024-08-18 起交易所停止披露。

    此前经 moneyflow_hsgt 取到的 north_money/south_money 实为【沪深港通成交额】口径
    （恒等式 hgt+sgt≡north_money、单日动辄数千亿，绝非净流入）；若当『北向净流入』展示或喂给
    AI 研报，会凭空生成"北向大幅流入"的看多结论，直接触碰"绝不编造数字"铁律。故与
    market.py / timing.py 既定政策一致——北向不纳入分析。保留函数签名与返回结构
    （today=None / trend=[] + missing 说明）使前端自动降级为空态、AI 不再获注此维度。

    `date_compact` 参数保留仅为兼容旧调用签名，不再使用。"""
    return {"ok": True, "ts": ts_str(), "data_date": None,
            "missing": ["北向资金净流入实时数据已于 2024-08 停止披露，不纳入分析（南向同）"],
            "today": None, "trend": []}


# ---------------------------------------------------------------- ② 龙虎榜机构净买

@ttl_cache(
    300,
    offhours_seconds=6 * 3600,
    # 只有"今日完整"结果才进盘外 6 小时长缓存；"结算中/源异常/最近完整快照"
    # 只享受基础 300s，避免今日空数据在盘外冒充"今日无榜"6 小时。
    long_ok=lambda r: isinstance(r, dict) and r.get("status") == "complete",
)
def longhu(date_compact: str | None = None, limit: int = 30) -> dict:
    """龙虎榜：个股上榜净买额榜（top_list）+ 机构席位净买榜（top_inst 按股聚合）。

    发布/回退状态（四态，绝不一律写"不可用"）：
    - ``complete``      今日东财已返回完整榜（is_realtime=True，落盘快照）。
    - ``settling``      今日东财 HTTP 200 但业务空数据（结算中/未发布），不是永久不可用。
    - ``source_error``  东财链路失败（safe 全通道→None）= 当前源异常。
    - ``last_complete`` 今日空/异常但有历史完整快照 → 显眼展示最近完整快照及其日期，不清空。

    单位铁律不变（net_amount/buy/sell/amount 皆【元】不转）。空/异常一律 ok=True、绝不抛、
    绝不拿今日空数据冒充"今日无榜"、绝不拿旧快照冒充今日实时（快照必标 snapshot_date）。"""
    missing: list[str] = []
    dc, data_date, note = _resolve_post_close_day(date_compact)
    out: dict = {"ok": True, "ts": ts_str(), "data_date": data_date, "missing": missing,
                 "status": "settling", "is_realtime": False, "snapshot_date": None,
                 "list": [], "inst": []}
    if note:
        missing.append(note)
    n = max(5, min(int(limit or 30), 100))

    ak = get_ak()
    detail_fn = getattr(ak, "stock_lhb_detail_em", None) if ak else None
    inst_fn = getattr(ak, "stock_lhb_jgmmtj_em", None) if ak else None
    eastmoney_rows: list[dict] = []
    inst_rows: list[dict] = []
    eastmoney_available = False

    # ---- 个股上榜榜（公开龙虎榜，按净买额降序）----
    # safe() 已在适配层兜住 akshare result=null/异常（全通道失败→None，不抛）。
    # 区分：df is None = 源异常/失败；df 空(len 0) = 东财 200 空业务 = 今日结算中/未发布。
    df = safe(detail_fn, start_date=dc, end_date=dc, label="龙虎榜(公开源)", timeout=4) if detail_fn else None
    source_error = bool(detail_fn) and df is None
    if df is not None and len(df):
        try:
            df = df.sort_values("龙虎榜净买额", ascending=False)
        except Exception:
            pass
        for _, r in df.iterrows():
            eastmoney_rows.append({
                "code": str(r.get("代码", "")).zfill(6),
                "name": str(r.get("名称", "") or ""),
                "price": to_float(r.get("收盘价")),
                "change_pct": to_float(r.get("涨跌幅")),
                "turnover_rate": to_float(r.get("换手率")),
                "net_amount": to_float(r.get("龙虎榜净买额")),
                "buy": to_float(r.get("龙虎榜买入额")),
                "sell": to_float(r.get("龙虎榜卖出额")),
                "amount": to_float(r.get("龙虎榜成交额")),
                "reason": str(r.get("上榜原因", "") or ""),
            })
        eastmoney_available = True

    # ---- 机构席位净买榜（公开机构买卖统计）----
    # 个股榜已明确失败时不再紧接着打第二个慢端点；官方快照核验仍独立返回。
    dfi = (
        safe(inst_fn, start_date=dc, end_date=dc, label="龙虎榜机构(公开源)", timeout=4)
        if inst_fn and eastmoney_available
        else None
    )
    if dfi is not None and len(dfi):
        for _, r in dfi.iterrows():
            inst_rows.append({
                "code": str(r.get("代码", "")).zfill(6),
                "name": str(r.get("名称", "") or ""),
                "net_buy": to_float(r.get("机构买入净额")),
                "buy": to_float(r.get("机构买入总额")),
                "sell": to_float(r.get("机构卖出总额")),
                "seats": (to_int(r.get("买方机构数"), 0) or 0) + (to_int(r.get("卖方机构数"), 0) or 0),
            })
        inst_rows.sort(key=lambda x: x.get("net_buy") or 0, reverse=True)

    # ---- 四态判定 + 最近完整快照回退（保留而非清空）----
    if eastmoney_available:
        out["status"] = "complete"
        out["is_realtime"] = True
        out["list"] = eastmoney_rows[:n]
        out["inst"] = inst_rows[:n]
        if not inst_rows:
            missing.append("当日无机构专用席位上榜")  # 有榜但无机构席位=非异常
        _save_longhu_snapshot(data_date, eastmoney_rows, inst_rows)  # 仅完整才落盘
    else:
        snap = _load_latest_longhu_snapshot(before_date=data_date)
        if snap:
            out["status"] = "last_complete"
            out["snapshot_date"] = snap.get("trade_date")
            out["snapshot_saved_at"] = snap.get("saved_at")
            out["list"] = (snap.get("list") or [])[:n]
            out["inst"] = (snap.get("inst") or [])[:n]
            missing.append(
                f"东财今日{'源异常' if source_error else '结算中/未发布'}，"
                f"下方为最近完整快照（{snap.get('trade_date')}）"
            )
        else:
            out["status"] = "source_error" if source_error else "settling"
            missing.append("龙虎榜今日源异常，稍后重试" if source_error
                           else "龙虎榜今日结算中/未发布，尚无历史快照可回退")

    # 官方核验：用被展示的东财代码集（今日或快照）对该日官方快照，得 matched/独有分类。
    out["official_verification"] = _official_verification(
        out.get("snapshot_date") or data_date,
        eastmoney_rows if eastmoney_available else (out.get("list") or []),
        eastmoney_available=eastmoney_available or bool(out.get("list")),
    )
    return out


# ---------------------------------------------------------------- ③ 行业资金流

def industry(date_compact: str | None = None, limit: int = 30) -> dict:
    """行业资金流向榜：同花顺免费即时行业资金口径。
    按主力净额降序，红进绿出；即时源不支持冒充历史日回填。

    单位：*_amount / net_amount 资金字段为【元】，不转；个股资金流供应商口径可能不同。
    pct_change 为 %。公开源失败/空 → ok=True + 空榜 + missing。"""
    missing: list[str] = []
    current_date = free_source.quote_data_date()
    if date_compact:
        requested = f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}"
        if requested != current_date:
            return {"ok": True, "ts": ts_str(), "data_date": requested,
                    "missing": ["同花顺免费行业资金为即时源，不支持历史日期回填"],
                    "source": None, "industries": []}
    dc, data_date, note = current_date.replace("-", ""), current_date, None
    out: dict = {"ok": True, "ts": ts_str(), "data_date": data_date, "missing": missing,
                 "source": None, "industries": []}
    if note:
        missing.append(note)
    n = max(5, min(int(limit or 30), 100))

    # 行业资金榜优先读取后台完整采集的当日快照；交互请求不再重复等待
    # 公开源。没有当日快照时才做 4 秒快速探测，失败也不熔断后台任务。
    rows: list[dict] = []
    try:
        from . import capital_tracker  # noqa: PLC0415

        cached = capital_tracker.latest_sectors()
        rows = [
            {
                "name": row.get("name"),
                "main_net": row.get("main_net"),
                "change_pct": row.get("change_pct"),
            }
            for row in cached
            if str(row.get("trade_date") or "").replace("-", "") == dc
        ]
        if rows:
            out["source"] = str(cached[0].get("source") or "后台资金快照")
            out["ts"] = str(cached[0].get("ts") or out["ts"])
    except Exception:  # noqa: BLE001 -- 快照不可读时继续快速探测
        rows = []
    if not rows:
        rows = free_source.moneyflow_ind_dc(dc)
    if not rows:
        missing.append("行业资金流")
        return out
    if not out.get("source"):
        out["source"] = "同花顺·公开源"
    out["industries"] = [{
        "name": r.get("name"),
        "change_pct": r.get("change_pct"),
        "net_amount": r.get("main_net"),
        "net_rate": None,
        "super_buy": None,
        "large_buy": None,
        "lead_stock": "",
    } for r in rows if r.get("name")][:n]
    return out
