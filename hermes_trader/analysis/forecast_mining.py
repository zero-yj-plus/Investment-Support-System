"""业绩预告掘金 —— 中报/年报预告的确定性规则筛选（母文档铁律：数字全走规则，AI 不参与）。

客户方法论落地（wudi 2026-07-02，四层规则全部可机器判定）：
  ① 政策阈值：主板（60/000/001/002/003…）中报强制预告=净利为负/扭亏为盈/盈利且同比±50%；
     双创&北交所中报为自愿披露（愿意发的多为抢跑真景气，单独标注、不与强制混淆）。
  ② 扣非疑点：预告阶段无扣非数——从变动原因文本做确定性标记（含"非经常/出售/处置/补助/
     公允价值/汇兑"→ ⚠️疑非经常驱动），并恒提示"正式中报需核扣非"。绝不臆断数值。
  ③ 披露时点分层：中报窗口截止 7/15（年报 1/31）。主动交卷（早）→质量分层高；
     压哨（最后两天）→业绩承压概率高。纯日期规则。
  ④ 基数陷阱：上年同期基数为负（扭亏族，同比%无意义）或极低基数+数千%高增 → 标记沉底，
     不硬删（透明降级，用户看得到为什么）。

产出：/api/forecast/mining 榜单页 + 每日定点推送（config forecast_mining 门控，off-by-default，
其它客户零影响）。所有数值来自免费公开业绩预告汇总（供应方口径），缺失标 missing，绝不编造。"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import tempfile

from ..data import free_source as tx
from ..data.base import now_cn

log = logging.getLogger(__name__)

# 基数陷阱阈值：上年同期归母净利 < 3000万(元) 且预告同比下限 > 300% → 低基数伪高增；
# 同比下限 ≥ 1000% 无论基数一律标记（"数千倍"式扭亏高增，客户点名要剔的坑）。
_LOW_BASE_YUAN = 3000e4
_TRAP_PCHG = 300.0
_HARD_TRAP_PCHG = 1000.0
# 变动原因里的"非经常性损益"信号词（确定性文本标记，不做任何数值推断）
_NONREC_WORDS = ("非经常", "出售", "处置", "政府补助", "补贴", "公允价值", "汇兑", "拆迁", "转让")

# 正向类型（掘金池）；负向类型只进统计（风险面），不进榜单
_POSITIVE_TYPES = ("预增", "扭亏", "续盈", "略增")

_LAST_GOOD_SCHEMA = "forecast-mining-last-good-v1"
_LAST_GOOD_MAX_AGE_SECONDS = 7 * 24 * 3600
_PERIOD_RE = re.compile(r"^\d{8}$")


def _last_good_path(data_dir: str | Path | None, period: str) -> Path | None:
    """每个客户、每个报告期独立保存，非法 period 不参与文件路径拼接。"""
    if data_dir is None or not _PERIOD_RE.fullmatch(period):
        return None
    return Path(data_dir) / "forecast_mining" / f"last-good-{period}.json"


def _save_last_good(path: Path, period: str, result: dict, fetched_at: datetime) -> None:
    """原子保存非空公开源结果；快照是缓存，不覆盖任何业务事件账本。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    payload = {
        "schema_version": _LAST_GOOD_SCHEMA,
        "period": period,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "result": result,
    }
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            tmp_path = Path(handle.name)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _load_last_good(path: Path, period: str, replayed_at: datetime) -> tuple[dict, int] | None:
    """只回放同报告期、七天内、结构完整的成功快照。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = payload.get("result")
        if (
            payload.get("schema_version") != _LAST_GOOD_SCHEMA
            or payload.get("period") != period
            or not isinstance(result, dict)
            or result.get("ok") is not True
            or not isinstance(result.get("items"), list)
            or not isinstance(result.get("traps"), list)
            or not isinstance(result.get("stats"), dict)
        ):
            return None
        fetched_at = datetime.fromisoformat(str(payload.get("fetched_at") or "").replace("Z", "+00:00"))
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            return None
        age = int(
            (replayed_at.astimezone(timezone.utc) - fetched_at.astimezone(timezone.utc)).total_seconds()
        )
        if age < 0 or age > _LAST_GOOD_MAX_AGE_SECONDS:
            return None
        return dict(result), age
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _board_of(code: str) -> tuple[str, bool]:
    """code6 → (板块名, 中报预告是否强制)。主板强制（触发阈值时），双创/北交所自愿。"""
    if code.startswith(("600", "601", "603", "605")):
        return "沪主板", True
    if code.startswith(("000", "001", "002", "003")):
        return "深主板", True
    if code.startswith(("300", "301")):
        return "创业板", False
    if code.startswith(("688", "689")):
        return "科创板", False
    return "北交所/其他", False


def _resolve_period(period: str | None) -> str:
    """报告期自动解析：5-9月=当年中报(0630)；10-12月=当年年报窗前瞻(1231)；1-4月=上年年报(1231)。"""
    if period:
        return str(period).strip()
    n = now_cn()
    if 5 <= n.month <= 9:
        return f"{n.year}0630"
    if n.month >= 10:
        return f"{n.year}1231"
    return f"{n.year - 1}1231"


def _layer_of(ann_date: str, period: str) -> str:
    """披露时点分层（纯日期规则）。中报截止 7/15：≤7/10 主动交卷、7/11-13 中性、7/14-15 压哨；
    年报截止次年 1/31：≤1/20 主动、1/21-28 中性、1/29-31 压哨。窗口外→提前/超期如实标注。"""
    if not ann_date or len(ann_date) < 8:
        return "未知"
    year, md = period[:4], period[4:]
    a = ann_date
    if md == "0630":
        lo, hi, mid = f"{year}0701", f"{year}0715", (f"{year}0710", f"{year}0713")
    elif md == "1231":
        y2 = str(int(year) + 1)
        lo, hi, mid = f"{y2}0101", f"{y2}0131", (f"{y2}0120", f"{y2}0128")
    else:
        return "非强制窗口"
    if a < lo:
        return "提前披露"
    if a > hi:
        return "超期/更正"
    if a <= mid[0]:
        return "主动交卷"
    if a <= mid[1]:
        return "中性"
    return "压哨"


_LAYER_RANK = {"主动交卷": 0, "提前披露": 1, "中性": 2, "非强制窗口": 3, "未知": 4, "超期/更正": 5, "压哨": 6}


def mine(period: str | None = None, data_dir: str | Path | None = None) -> dict:
    """业绩预告掘金榜（确定性规则）。返回 {ok, ts, period, deadline, stats, items, traps, missing}。
    items=达标掘金池（按 陷阱否→披露分层→同比下限 排序）；traps=被标记沉底的陷阱池（透明可见）。"""
    period = _resolve_period(period)
    captured_at = now_cn()
    snapshot_path = _last_good_path(data_dir, period)
    missing: list[str] = []
    raw = tx.forecast_list(period)
    if raw is None:
        cached = _load_last_good(snapshot_path, period, captured_at) if snapshot_path else None
        if cached is not None:
            result, age = cached
            note = f"业绩预告公开源当前不可用，展示最近成功榜单（距采集约{age}秒）"
            result.update({
                "ok": True,
                "ts": captured_at.strftime("%Y-%m-%d %H:%M:%S"),
                "state": "last_good",
                "degraded": True,
                "stale": True,
                "used_last_good": True,
                "snapshot_age_seconds": age,
                "source_error": "业绩预告公开源不可用",
                "missing": [note, *[item for item in result.get("missing", []) if item != note]],
            })
            return result
        return {
            "ok": False,
            "ts": captured_at.strftime("%Y-%m-%d %H:%M:%S"),
            "data_ts": None,
            "period": period,
            "deadline": "",
            "state": "missing",
            "degraded": False,
            "stale": False,
            "used_last_good": False,
            "snapshot_age_seconds": None,
            "stats": {},
            "items": [],
            "traps": [],
            "missing": ["业绩预告公开源不可用——绝不编造"],
        }
    if len(raw) >= 990:
        missing.append(f"预告数据量 {len(raw)} 行接近单次上限，可能截断（后续窗口内会随每日更新补全）")

    today = now_cn().strftime("%Y%m%d")
    stats: dict = {"总数": len(raw), "今日新增": 0, "按类型": {}, "掘金池": 0, "陷阱池": 0}
    items: list[dict] = []
    traps: list[dict] = []

    for r in raw:
        t = r.get("type") or "未知"
        stats["按类型"][t] = stats["按类型"].get(t, 0) + 1
        if r.get("ann_date") == today:
            stats["今日新增"] += 1
        if t not in _POSITIVE_TYPES:
            continue  # 负向类型只进统计（预减/首亏/续亏/略减=风险面，不进掘金榜）

        code = r.get("code") or ""
        board, mandatory = _board_of(code)
        p_min, p_max = r.get("p_chg_min"), r.get("p_chg_max")
        last_net = r.get("last_net")

        # ① 政策阈值：主板取"盈利且同比≥+50% 或 扭亏"；双创/北交所自愿披露全收(标 抢跑)
        hit_threshold = (t == "扭亏") or (p_min is not None and p_min >= 50)
        if mandatory and not hit_threshold:
            continue  # 主板未达 ±50% 阈值/非扭亏 → 不入掘金池（略增等不看）

        # ④ 基数陷阱（确定性）：基数为负=扭亏族(同比%无意义)单独归组；低基数+高同比 → 陷阱
        trap_reason = ""
        if t != "扭亏":
            if last_net is not None:
                if last_net <= 0:
                    trap_reason = "上年基数为负，同比%无意义（扭亏式）"
                elif last_net < _LOW_BASE_YUAN and p_min is not None and p_min > _TRAP_PCHG:
                    trap_reason = f"低基数(上年归母<{int(_LOW_BASE_YUAN / 1e4)}万)+高同比，疑基数陷阱"
            elif p_min is not None and p_min > _TRAP_PCHG:
                # 上年同期基数【缺失】+高同比 → 无法排除基数陷阱，保守归"待核陷阱池"不进干净掘金池
                # （MAJOR 修：原来 last_net 缺失直接跳过判定，让高同比陷阱股漏进干净池误导客户）
                trap_reason = "上年同期基数未披露，高同比无法排除基数陷阱（待核）"
        if not trap_reason and p_min is not None and p_min >= _HARD_TRAP_PCHG:
            trap_reason = "同比下限≥1000%，数千倍式高增大多来自极低基数"

        # ② 扣非疑点：变动原因文本含非经常信号词 → 标记（不推断数值）
        reason = r.get("reason") or ""
        nonrec = any(w in reason for w in _NONREC_WORDS)

        layer = _layer_of(r.get("ann_date") or "", period)
        item = {
            **r,
            "board": board,
            "mandatory": mandatory,          # True=主板强制窗口, False=自愿披露(抢跑信号)
            "layer": layer,
            "trap": bool(trap_reason),
            "trap_reason": trap_reason,
            "nonrec_flag": nonrec,           # ⚠️疑非经常驱动（正式中报需核扣非）
            "is_new_today": r.get("ann_date") == today,
        }
        (traps if trap_reason else items).append(item)

    # 排序：披露分层(主动优先) → 同比下限降序；自愿披露(抢跑)不加权、靠 board 字段透明区分
    items.sort(key=lambda x: (_LAYER_RANK.get(x["layer"], 9), -(x.get("p_chg_min") or 0)))
    traps.sort(key=lambda x: -(x.get("p_chg_min") or 0))
    stats["掘金池"] = len(items)
    stats["陷阱池"] = len(traps)

    year, md = period[:4], period[4:]
    deadline = f"{year}-07-15" if md == "0630" else (f"{int(year) + 1}-01-31" if md == "1231" else "")
    result = {
        "ok": True,
        "ts": captured_at.strftime("%Y-%m-%d %H:%M:%S"),
        "data_ts": captured_at.isoformat(timespec="seconds"),
        "period": period,
        "deadline": deadline,
        "state": "complete" if raw else "complete_empty",
        "degraded": bool(missing),
        "stale": False,
        "used_last_good": False,
        "snapshot_age_seconds": 0,
        "stats": stats,
        "items": items,
        "traps": traps,
        "missing": missing,
    }
    # 成功但真实空数组保持 complete_empty，不能用旧数据覆盖；也不能让空响应
    # 覆盖此前非空 last-good，否则下一次瞬时故障仍会被放大成整页无数据。
    if raw and snapshot_path is not None:
        try:
            _save_last_good(snapshot_path, period, result, captured_at)
        except Exception as exc:  # noqa: BLE001 -- 快照失败不影响本次真实结果
            log.info("业绩预告 last-good 写入失败: %s", str(exc)[:140])
    return result


def build_push_text(d: dict, top_n: int = 10) -> str | None:
    """掘金榜 → 推送文本（纯规则拼接，无 AI）。空榜/失败 → None 不推（避免噪音）。"""
    # last-good 只解决页面瞬时空白；主动推送不能把旧榜伪装成当天新采集结果。
    if not d.get("ok") or d.get("used_last_good") or d.get("stale") or not d.get("items"):
        return None
    items = d["items"][:max(1, top_n)]
    st = d.get("stats", {})
    lines = [
        f"📢 业绩预告掘金 · 报告期 {d.get('period', '')}（窗口截止 {d.get('deadline', '')}）",
        f"全量 {st.get('总数', 0)} 份 · 今日新增 {st.get('今日新增', 0)} · 掘金池 {st.get('掘金池', 0)} · 陷阱沉底 {st.get('陷阱池', 0)}",
        "——达标榜（主板±50%阈值/扭亏 + 自愿抢跑 · 主动交卷优先）——",
    ]
    for i, x in enumerate(items, 1):
        pm, px = x.get("p_chg_min"), x.get("p_chg_max")
        rng = (f"+{pm:.0f}%~+{px:.0f}%" if pm is not None and px is not None
               else (f"+{pm:.0f}%起" if pm is not None else "幅度未披露"))
        base = x.get("last_net")
        base_txt = f"{base / 1e8:.2f}亿" if isinstance(base, (int, float)) and abs(base) >= 1e8 \
            else (f"{base / 1e4:.0f}万" if isinstance(base, (int, float)) else "—")
        marks = ("🆕" if x.get("is_new_today") else "") + ("⚠️扣非待核" if x.get("nonrec_flag") else "")
        vol = "" if x.get("mandatory") else "·自愿抢跑"
        ann = x.get("ann_date") or ""
        ann_h = f"{ann[4:6]}/{ann[6:8]}" if len(ann) == 8 else ann
        lines.append(f"{i}. {x.get('name') or x.get('code')}({x.get('code')}) {x.get('type')} {rng}"
                     f" · 上年基数{base_txt} · {ann_h}披露[{x.get('layer')}{vol}] {marks}")
    lines.append("陷阱池(低基数/数千倍式)已沉底不上榜；正式中报请核扣非。规则筛选·仅供参考，不构成投资建议。")
    return "\n".join(lines)


async def push_daily(engine) -> None:
    """每日定点推送（scheduler 按 config forecast_mining 注册；off-by-default 其它客户零影响）。"""
    import asyncio  # noqa: PLC0415

    cfg = engine.config.section("forecast_mining")
    d = await asyncio.to_thread(mine, cfg.get("period") or None, engine.config.data_dir)
    text = build_push_text(d, int(cfg.get("top_n", 10)))
    if not text:
        log.info("预告掘金推送跳过：无达标榜单（%s）", "; ".join(d.get("missing", [])) or "空榜")
        return
    title = f"业绩预告掘金 · {now_cn().strftime('%m-%d')}"
    await engine.feishu.push_card(title, text)
    log.info("预告掘金已推送：掘金池 %s / 陷阱 %s", d.get("stats", {}).get("掘金池"), d.get("stats", {}).get("陷阱池"))
