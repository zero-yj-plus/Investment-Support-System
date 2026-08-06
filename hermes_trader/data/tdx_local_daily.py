"""直接读取通达信本地 ``vipdoc/*/lday/*.day`` 并进行前复权。

K 线主体来自 32 字节定长 ``.day`` 文件；除权除息事件通过通达信自带的
``tqcenter.get_divid_factors`` 读取本地股本变迁缓存。这里不调用 TQ K 线接口，
也不缓存全市场 DataFrame，避免盘后刷新长期占用大量内存。
"""
from __future__ import annotations

from datetime import date
import importlib.util
import math
import os
from pathlib import Path
import struct
import sys
import threading
from typing import Iterable

from .base import log, now_cn

_DAY_RECORD = struct.Struct("<IIIIIfII")
_MAX_DAYS = 800
_TQ_MODULE_NAME = "_hermes_tdx_local_tqcenter"
_TQ_LOCK = threading.RLock()
_tq = None
_tq_load_attempted = False


def tdx_root() -> Path:
    raw = os.environ.get("TDX_LOCAL_DIR", r"D:\tdx").strip()
    return Path(raw) if raw else Path(r"D:\tdx")


def configured() -> bool:
    root = tdx_root()
    return all((root / "vipdoc" / market / "lday").is_dir() for market in ("sh", "sz", "bj"))


def _entity(code: str) -> str | None:
    raw = str(code or "").strip().upper()
    if "." in raw:
        number, market = raw.rsplit(".", 1)
    else:
        number = raw[-6:].zfill(6)
        if number.startswith(("6", "9", "5")):
            market = "SH"
        elif number.startswith(("4", "8", "92")):
            market = "BJ"
        else:
            market = "SZ"
    if len(number) != 6 or not number.isdigit() or market not in {"SH", "SZ", "BJ"}:
        return None
    return f"{number}.{market}"


def _day_path(entity: str) -> Path:
    number, market = entity.split(".")
    prefix = market.lower()
    return tdx_root() / "vipdoc" / prefix / "lday" / f"{prefix}{number}.day"


def read_raw_daily(
    code: str,
    days: int = 520,
    *,
    completed_only: bool = True,
) -> list[dict]:
    """读取单只证券最后若干根未复权日线；文件缺失或损坏时返回空列表。"""
    entity = _entity(code)
    if not entity:
        return []
    path = _day_path(entity)
    try:
        size = path.stat().st_size
        complete_size = size - size % _DAY_RECORD.size
        if complete_size < _DAY_RECORD.size:
            return []
        count = max(20, min(int(days), _MAX_DAYS))
        read_size = min(complete_size, count * _DAY_RECORD.size)
        with path.open("rb") as handle:
            handle.seek(complete_size - read_size)
            payload = handle.read(read_size)
    except (OSError, TypeError, ValueError):
        return []

    current = now_cn()
    exclude_today = completed_only and (current.hour * 60 + current.minute) < (15 * 60 + 15)
    today = int(current.strftime("%Y%m%d"))
    rows: list[dict] = []
    previous_date = 0
    for record in _DAY_RECORD.iter_unpack(payload):
        day, open_i, high_i, low_i, close_i, amount, volume, _reserved = record
        if day <= previous_date or (exclude_today and day == today):
            continue
        try:
            parsed_day = date.fromisoformat(
                f"{day // 10000:04d}-{day // 100 % 100:02d}-{day % 100:02d}"
            ).isoformat()
        except ValueError:
            continue
        prices = (open_i / 100.0, high_i / 100.0, low_i / 100.0, close_i / 100.0)
        if min(prices) <= 0 or not math.isfinite(amount) or amount < 0 or volume < 0:
            continue
        rows.append({
            "d": parsed_day,
            "o": prices[0], "h": prices[1], "l": prices[2], "c": prices[3],
            "v_shares": float(volume),
            "amount": float(amount),
            "adjustment": "none",
            "source": "通达信本地.day未复权日K",
            "volume_unit": "股",
            "amount_unit": "元",
        })
        previous_date = day
    return rows[-count:]


def _load_tq():
    global _tq, _tq_load_attempted
    with _TQ_LOCK:
        if _tq is not None:
            return _tq
        if _tq_load_attempted:
            return None
        _tq_load_attempted = True
        root = tdx_root()
        candidates = (
            root / "PYPlugins" / "user" / "tqcenter.py",
            root / "PYPlugins" / "sys" / "tqcenter.py",
        )
        module_path = next((path for path in candidates if path.is_file()), None)
        if module_path is None:
            return None
        try:
            spec = importlib.util.spec_from_file_location(_TQ_MODULE_NAME, module_path)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[_TQ_MODULE_NAME] = module
            spec.loader.exec_module(module)
            _tq = module.tq
            return _tq
        except Exception as exc:  # noqa: BLE001 - 可选本地能力，失败必须显式降级
            sys.modules.pop(_TQ_MODULE_NAME, None)
            log.info("通达信本地除权因子模块加载失败: %s", str(exc)[:160])
            return None


def _tq_connection_path() -> str:
    """用应用入口区分 TQ 连接，避免所有进程共用本模块路径而撞名。"""
    raw = str(os.environ.get("TDX_TQ_CONNECTION_PATH") or sys.argv[0] or "").strip()
    path = Path(raw).resolve() if raw else Path(__file__).resolve()
    return str(path)


def _dividend_events(entity: str, start_day: str, end_day: str) -> list[tuple[str, float, float, float, float]] | None:
    """返回 category=1 权息事件；``None`` 表示因子能力不可用。"""
    provider = _load_tq()
    if provider is None:
        return None
    with _TQ_LOCK:
        try:
            if not getattr(provider, "_initialized", False):
                provider.initialize(_tq_connection_path())
            frame = provider.get_divid_factors(
                stock_code=entity,
                start_time=start_day.replace("-", ""),
                end_time=end_day.replace("-", ""),
            )
            if not getattr(provider, "_initialized", False):
                return None
        except Exception as exc:  # noqa: BLE001 - 单股因子失败由调用方降级
            log.info("通达信本地除权因子(%s)失败: %s", entity, str(exc)[:120])
            return None
    if frame is None or not hasattr(frame, "iterrows"):
        return None
    if getattr(frame, "empty", True):
        return []
    required = {"Type", "Bonus", "AllotPrice", "ShareBonus", "Allotment"}
    if not required.issubset(set(frame.columns)):
        return None
    events = []
    for stamp, row in frame.iterrows():
        try:
            if int(row["Type"]) != 1:
                continue
            values = tuple(float(row[name]) for name in ("Bonus", "AllotPrice", "ShareBonus", "Allotment"))
            if not all(math.isfinite(value) for value in values):
                continue
            day = stamp.date().isoformat()
        except (AttributeError, TypeError, ValueError):
            continue
        events.append((day, *values))
    return sorted(events)


def _round_half_up(value: float) -> float:
    scaled = value * 100.0
    cents = math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
    return cents / 100.0


def apply_qfq(raw_bars: list[dict], events: list[tuple[str, float, float, float, float]]) -> list[dict]:
    """按通达信权息事件对未复权 OHLC 做仿射前复权；成交量/额保持不变。"""
    if not raw_bars:
        return []
    latest = raw_bars[-1]["d"]
    active = [event for event in events if event[0] <= latest]
    event_index = len(active) - 1
    multiplier = 1.0
    offset = 0.0
    adjusted: list[dict] = [{} for _bar in raw_bars]
    for index in range(len(raw_bars) - 1, -1, -1):
        bar = raw_bars[index]
        while event_index >= 0 and active[event_index][0] > bar["d"]:
            _day, bonus, allot_price, share_bonus, allotment = active[event_index]
            m = (10.0 + share_bonus + allotment) / 10.0
            if m == 0:
                m = 1.0
            cash = (bonus - allotment * allot_price) / 10.0
            multiplier /= m
            offset -= multiplier * cash
            event_index -= 1
        current = dict(bar)
        for key in ("o", "h", "l", "c"):
            current[key] = _round_half_up(multiplier * float(bar[key]) + offset)
        current["adjustment"] = "qfq"
        current["source"] = "通达信本地.day+本地权息因子前复权日K"
        adjusted[index] = current
    return adjusted


def daily_bars_batch(
    codes: Iterable[str],
    days: int = 520,
    *,
    completed_only: bool = True,
) -> dict[str, list[dict]]:
    """顺序读取一小批本地日线；不保留进程级全市场缓存。"""
    output: dict[str, list[dict]] = {}
    for raw_code in dict.fromkeys(str(code) for code in codes):
        entity = _entity(raw_code)
        if not entity:
            continue
        bars = read_raw_daily(entity, days, completed_only=completed_only)
        if not bars:
            continue
        number = entity.split(".", 1)[0]
        # 指数不存在除权语义，未复权价格就是可用价格。
        if entity == "000300.SH":
            output[number] = bars
            continue
        events = _dividend_events(entity, bars[0]["d"], bars[-1]["d"])
        if events is None:
            continue
        output[number] = apply_qfq(bars, events)
    return output
