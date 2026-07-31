"""后台预热器 + 持久快照 —— 解决『每次加载慢 + 大盘/个股基础数据刷新不及时』。

做什么（只读、不改动现有任何文件）：
1) 交易时段主动按 ~15s 节拍调用 market 热函数 + 持仓批量 quick_quotes，把 base.ttl_cache
   预热成热缓存 → 前端任意请求恒命中热缓存，秒开（绝不让用户的请求去等冷抓取）。
2) 每次预热把 market_overview 写 data_dir/cache_snapshot.json（带 ts/epoch）；进程启动时
   load_snapshot() 读回作 last-good，重启后首屏即时（标 stale，直到首次预热完成）。
3) 全程复用现有 safe()/ttl_cache：失败静默不抛、不污染缓存（数据真实性铁律不破）。

为什么不打爆数据源 / 不烧钱：
- 节拍 ~15s 与 market 热函数最短 TTL（overview 12s）对齐——绝大多数预热轮命中现有缓存、
  只有跨过 TTL 的那一轮才真抓一次，等于把"用户撞 miss"提前成"后台撞 miss"，总抓取量不增。
- 单飞（ttl_cache 内的 _inflight 锁）保证预热与用户请求同 key 并发只抓一次，不放大突发。
- 预热只触达 market_overview/sectors/flow/top + 持仓批量 quick_quotes；
  绝不触达按次计费源（通达信 MCP 等由各热函数内部按既有策略决定，预热不额外引入）。
- 盘外（非交易时段/非交易日）停止主动预热，只保留启动时的一次 load_snapshot + 单发预热。

接线方式见本模块末尾 wire-up 文档串 + 子代理返回说明。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from pathlib import Path

from ..data import market
from ..data.base import is_trade_day, now_cn

log = logging.getLogger("hermes.prewarm")

# 预热节拍（秒）。与 market_overview 的 12s TTL 对齐取 15s：
# 既保证用户来时缓存几乎总是新鲜，又不会比 TTL 更密集地触发真实抓取。
DEFAULT_INTERVAL = 15.0

# 持久快照文件名（落在各客户 config.data_dir 下，互不串数据）。
SNAPSHOT_FILE = "cache_snapshot.json"

# 进程内已读回的启动快照（仅在首次预热完成前作为 overview 的 last-good 回退）。
_boot_snapshot: dict | None = None
# 首次预热是否已完成：完成后 ttl_cache 已有热数据，启动快照不再需要兜底。
_warmed_once = False
# 启动预热的后台任务引用（防被 GC 回收）——启动预热丢后台跑、绝不阻塞主服务起来。
_BOOT_TASKS: set = set()


# ----------------------------------------------------------------- 交易时段判定

def _in_trading_session(now=None) -> bool:
    """A 股连续竞价时段（含集合竞价起点）：09:25–11:30 / 13:00–15:00，且为交易日。

    复用 base.is_trade_day（其内部已 serve-last-good 缓存交易日历，不产生额外抓取）。
    盘外返回 False → 调度侧停止主动预热，避免无意义抓取/夜间烧资源。
    取 09:25 起点与 market._resolve_data_day 的『已开盘』判定同口径。"""
    now = now or now_cn()
    if not is_trade_day(now.date()):
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 25) <= hm <= (11 * 60 + 30) or (13 * 60) <= hm <= (15 * 60)


# ----------------------------------------------------------------- 持久快照

def _snapshot_path(data_dir) -> Path:
    return Path(data_dir) / SNAPSHOT_FILE


def _valid_total_amount(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _with_total_amount_last_good(overview: dict) -> dict:
    """当前成交额缺失时，仅复用同一数据日最近一次有效值，并显式标注缓存时点。"""
    current = dict(overview)
    if _valid_total_amount(current.get("total_amount")):
        current.pop("total_amount_cached", None)
        current.pop("total_amount_asof", None)
        return current

    previous = _boot_snapshot
    same_data_day = (
        isinstance(previous, dict)
        and bool(current.get("data_date"))
        and current.get("data_date") == previous.get("data_date")
    )
    if not same_data_day or not _valid_total_amount(previous.get("total_amount")):
        current.pop("total_amount_cached", None)
        current.pop("total_amount_asof", None)
        return current

    asof = previous.get("total_amount_asof") or previous.get("ts")
    current["total_amount"] = previous["total_amount"]
    current["total_amount_cached"] = True
    current["total_amount_asof"] = asof
    missing = [
        str(item)
        for item in (current.get("missing") or [])
        if str(item).strip() != "两市成交额"
    ]
    cache_note = "两市成交额取自同数据日 last-good 缓存（当前刷新未取得新值）"
    if asof:
        cache_note = f"两市成交额取自同数据日 {asof} last-good 缓存（当前刷新未取得新值）"
    if cache_note not in missing:
        missing.append(cache_note)
    current["missing"] = missing
    return current


def save_snapshot(data_dir, overview: dict) -> None:
    """把一份 market_overview 结果原子落盘（带采集时间戳）。失败静默——快照是优化，不是关键路径。"""
    global _boot_snapshot
    if not isinstance(overview, dict) or not overview.get("ok"):
        return  # 只持久化成功的 overview，绝不把失败/空结果落成 last-good
    try:
        overview = _with_total_amount_last_good(overview)
        # 同时更新进程内 last-good。首屏只读端点必须可以直接返回上一轮成功值，
        # 不能为了“检查缓存是否热”再次调用 market_overview() 并把用户请求拖进慢源。
        _boot_snapshot = dict(overview)
        path = _snapshot_path(data_dir)
        payload = {
            "ts": overview.get("ts"),          # 数据归属的人类可读时间（采集函数已写）
            "saved_epoch": time.time(),         # 落盘墙钟，启动回读时据此算 stale age
            "overview": overview,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:  # noqa: BLE001 —— 快照写失败绝不影响主流程
        log.debug("快照落盘失败（忽略）: %s", str(e)[:120])


def load_snapshot(data_dir) -> dict | None:
    """进程启动时读回上次落盘的 overview 作 last-good，存入 _boot_snapshot。

    返回带 stale 标记的 overview（或 None）：重启后首个请求即可用它即时出图，
    直到首次后台预热把 ttl_cache 填上真实新鲜数据（铁律：陈旧必标 stale，绝不当实时呈现）。"""
    global _boot_snapshot
    try:
        path = _snapshot_path(data_dir)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        ov = payload.get("overview")
        if not isinstance(ov, dict):
            return None
        age = int(max(0, time.time() - float(payload.get("saved_epoch") or 0)))
        ov = dict(ov)  # 不就地改读回对象
        miss = list(ov.get("missing") or [])
        miss.insert(0, f"启动快照(重启后回退，距上次采集约{age}秒，预热完成后自动刷新)")
        ov["missing"] = miss
        ov["stale"] = True
        ov["snapshot_age"] = age
        _boot_snapshot = ov
        log.info("已读回启动快照作 last-good（距今约 %ss）", age)
        return ov
    except Exception as e:  # noqa: BLE001
        log.debug("读取启动快照失败（忽略）: %s", str(e)[:120])
        return None


def overview_with_snapshot() -> dict | None:
    """供只读端点用的 overview：首次预热完成前回退启动快照（标 stale），完成后透传热缓存实时值。

    注意：market.market_overview() 命中 ttl_cache 时本身就是秒回，这里只是在
    『进程刚起、ttl_cache 还空、且尚无网络』的窗口里多给一层启动快照兜底。"""
    # 预热器每轮成功后都会原子替换这份 last-good；直接返回即可。旧实现先调用
    # market_overview()，在 TTL miss / 上游卡住时会让所谓“秒回端点”等 6–60 秒。
    if _boot_snapshot is not None:
        return dict(_boot_snapshot)
    try:
        ov = market.market_overview()
    except Exception:  # noqa: BLE001 —— 与 safe() 同哲学：不抛
        ov = None
    if ov and ov.get("ok"):
        return ov
    return None


# ----------------------------------------------------------------- 预热一轮

def _holding_codes(store, limit: int = 30) -> list[str]:
    """从 knowledge.Store 读持仓股票代码（已 zfill 6 位）。store 不可用/读空时返回 []。"""
    try:
        codes = []
        for h in (store.holdings() if store else []):
            c = str((h or {}).get("code", "")).strip()
            if c:
                codes.append(c.zfill(6))
        # 去重保序，封顶 limit，避免持仓异常多时把一轮预热拖长
        seen, out = set(), []
        for c in codes:
            if c not in seen:
                seen.add(c)
                out.append(c)
            if len(out) >= limit:
                break
        return out
    except Exception:  # noqa: BLE001
        return []


def warm_once(config, store) -> dict:
    """同步执行一轮预热（阻塞型，调度侧务必丢线程池调用，勿在事件循环里直接跑）。

    调用 market 热函数并对持仓做一次批量 quick_quotes：命中现有 ttl_cache 即秒回，
    跨 TTL 的那一轮触发一次真实抓取并回填缓存。每个调用各自被 ttl_cache+safe() 兜底，
    单点失败静默跳过、不污染缓存、不影响同轮其余预热。返回本轮计数（仅供日志/可观测）。"""
    global _warmed_once
    warmed, failed = 0, 0

    def _hit(fn, *a):
        nonlocal warmed, failed
        try:
            r = fn(*a)
            # market_* 约定返回带 ok 的 dict；stock_detail 同样。None/ok=False 记失败但不抛。
            if isinstance(r, dict) and r.get("ok"):
                warmed += 1
            else:
                failed += 1
            return r
        except Exception as e:  # noqa: BLE001 —— 预热绝不抛，单点失败不拖垮整轮
            failed += 1
            log.debug("预热单点失败（忽略）: %s", str(e)[:120])
            return None

    # 1) 市场热函数：与各自 TTL 对齐，预热把"用户撞 miss"提前成"后台撞 miss"，总抓取量不增。
    ov = _hit(market.market_overview)
    _hit(market.market_sectors)
    _hit(market.market_flow)
    _hit(market.market_top)

    # 2) 持仓个股盘口只做一次批量快照。旧实现每 15 秒逐股 stock_detail，
    #    会把 F10/竞价/资金等低频慢源带进预热，反过来挤占用户问股线程。
    from ..data import stock  # noqa: PLC0415 —— 局部导入，避免与 market 的潜在循环依赖
    holding_codes = _holding_codes(store)
    if holding_codes:
        quotes = stock.quick_quotes(holding_codes)
        if quotes:
            warmed += 1
        else:
            failed += 1

    # 3) 持久快照：把这轮的 overview 落盘作下次重启的 last-good。
    if ov and ov.get("ok"):
        save_snapshot(config.data_dir, ov)

    _warmed_once = True
    return {"warmed": warmed, "failed": failed}


# ----------------------------------------------------------------- 调度注册

def register(scheduler, config, store) -> None:
    """把周期预热任务挂到现有 AsyncIOScheduler 上（与 build_scheduler 里 capital_snapshot 同范式）。

    - 每 DEFAULT_INTERVAL 秒触发一次；任务内先判交易时段，盘外直接 return（低频空转、几乎零成本）。
    - 阻塞型 warm_once 丢线程池跑，绝不卡 uvicorn 主事件循环（与既有定时任务同处理）。
    - coalesce + max_instances=1：上一轮没跑完不叠加并发，misfire 合并为一次。
    """
    from apscheduler.triggers.interval import IntervalTrigger  # noqa: PLC0415

    interval = _resolve_interval(config)

    async def _tick() -> None:
        if not _in_trading_session():
            return  # 盘外：停止主动预热（启动时已 load_snapshot + 预热一次兜底首屏）
        try:
            r = await asyncio.to_thread(warm_once, config, store)
            log.debug("预热轮完成: %s", r)
        except Exception as e:  # noqa: BLE001
            log.debug("预热轮异常（忽略）: %s", str(e)[:120])

    scheduler.add_job(
        _tick,
        IntervalTrigger(seconds=interval, timezone=config.timezone),
        id="prewarm_tick",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=int(interval),  # APScheduler 要求 int；落后超一个周期就丢弃该次、不补跑堆积
    )
    log.info("后台预热器已注册（间隔 %ss，仅交易时段主动预热）", interval)


def _resolve_interval(config) -> float:
    """读取预热间隔：优先 settings.json 覆盖键 prewarm_interval / config.yaml 顶层 prewarm.interval，
    否则用默认 15s。下限 5s 兜底，防止误配成 0/极小值把源打爆。"""
    val = None
    # settings.json 运行时覆盖（与 schedule 同机制，可热改）
    try:
        val = (getattr(config, "_overrides", {}) or {}).get("prewarm_interval")
    except Exception:  # noqa: BLE001
        val = None
    # config.yaml 顶层 prewarm: { interval: N, enabled: bool }
    if val is None:
        try:
            pw = (getattr(config, "_raw", {}) or {}).get("prewarm", {})
            if isinstance(pw, dict):
                val = pw.get("interval")
        except Exception:  # noqa: BLE001
            val = None
    try:
        return max(5.0, float(val)) if val is not None else DEFAULT_INTERVAL
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL


def prewarm_enabled(config) -> bool:
    """是否启用预热（默认开）。config.yaml 顶层 prewarm.enabled=false 或
    settings.json prewarm_enabled=false 可关闭。本机/盘中默认开即可，无需任何配置。"""
    try:
        ov = (getattr(config, "_overrides", {}) or {}).get("prewarm_enabled")
        if ov is not None:
            return bool(ov)
    except Exception:  # noqa: BLE001
        pass
    try:
        pw = (getattr(config, "_raw", {}) or {}).get("prewarm", {})
        if isinstance(pw, dict) and "enabled" in pw:
            return bool(pw["enabled"])
    except Exception:  # noqa: BLE001
        pass
    return True


async def boot(scheduler, config, store) -> None:
    """启动总入口：① 读回启动快照作 last-good → ② 立刻预热一次（丢线程池）→ ③ 注册周期任务。

    在 main.py 的 lifespan 内、scheduler.start() 之后 await 调用即可（见返回的接线说明）。
    全程异常自吞：预热是纯优化层，任何失败都不得阻断主服务启动。"""
    if not prewarm_enabled(config):
        log.info("后台预热器已禁用（prewarm.enabled=false）")
        return
    try:
        load_snapshot(config.data_dir)  # 重启后首屏即时（标 stale，直到首轮预热刷新）
    except Exception:  # noqa: BLE001
        pass
    # 启动即热一遍，但【绝不阻塞主服务启动】：丢后台任务跑（旧实现 await 到 warm 完成，源慢/
    # 东财被封时 warm_once 串行超时可卡 60–480s，若重启恰逢盘中＝关键交易时段整站不可用）。
    # 首屏由上面 load_snapshot 的 last-good 兜底（标 stale），warm 在后台刷新后无缝转热。
    async def _boot_warm() -> None:
        try:
            await asyncio.to_thread(warm_once, config, store)
        except Exception as e:  # noqa: BLE001
            log.debug("启动预热异常（忽略）: %s", str(e)[:120])
    try:
        _t = asyncio.create_task(_boot_warm())
        _BOOT_TASKS.add(_t)
        _t.add_done_callback(_BOOT_TASKS.discard)
    except RuntimeError:  # 极端：无运行中的事件循环 → 退回同步预热，至少不漏热
        try:
            await asyncio.to_thread(warm_once, config, store)
        except Exception:  # noqa: BLE001
            pass
    try:
        register(scheduler, config, store)
    except Exception:  # noqa: BLE001
        log.exception("注册周期预热任务失败（不影响主服务）")
