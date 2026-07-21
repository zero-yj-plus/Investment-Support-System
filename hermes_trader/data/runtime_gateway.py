"""Runtime adapters for the A-share quote evidence gateway.

The generic :mod:`hermes_trader.data.gateway` deliberately knows nothing about
upstream providers.  This module is the small production wiring layer for the
two quote feeds shipped with this customer workspace:

* customer-authorized TDX HTTP API for primary quote evidence;
* Tencent ``qt.gtimg.cn`` for timestamped quote/valuation fallback;

All providers return a whole-symbol snapshot.  ``MarketDataGateway`` resolves
one field at a time, so calling the providers once per field would be wasteful
and would make public-endpoint throttling much more likely.  The adapters below
therefore cache one normalized snapshot per symbol for a few seconds.  The
cache is guarded per symbol: concurrent requests for the same stock coalesce
into one upstream call while different stocks may still load in parallel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta, timezone
import hashlib
import math
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from . import free_source, tdx_api_source, tencent_source
from .base import to_float
from .evidence_store import EvidenceStore
from .gateway import AdapterResult, FieldPolicy, MarketDataGateway


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SYMBOL_RE = re.compile(r"(?i)(?:(sh|sz|bj))?(\d{6})(?:\.(sh|sz|bj))?")

FIELD_UNITS: dict[str, str] = {
    "last_price": "CNY",
    "prev_close": "CNY",
    "change_pct": "percent",
    "open": "CNY",
    "high": "CNY",
    "low": "CNY",
    "amount": "CNY",
    "volume": "share",
    "turnover_rate": "percent",
    "volume_ratio": "ratio",
    "pe": "ratio",
    "pb": "ratio",
    "total_mv": "CNY",
    "float_mv": "CNY",
}

FIELD_SEMANTICS: dict[str, str] = {
    "last_price": "最近可观察成交价；非交易时段为最近交易日收盘快照",
    "prev_close": "当前业务交易日对应的上一交易日收盘价",
    "change_pct": "现价相对昨收的涨跌幅，百分数值而非小数比例",
    "open": "当前业务交易日开盘价",
    "high": "当前业务交易日截至观测时点的最高价",
    "low": "当前业务交易日截至观测时点的最低价",
    "amount": "当前业务交易日截至观测时点的累计成交额，单位元",
    "volume": "当前业务交易日截至观测时点的累计成交量，单位股",
    "turnover_rate": "当前业务交易日截至观测时点的换手率，百分数值",
    "volume_ratio": "当前时点量比，无单位比例值",
    "pe": "供应商当前口径市盈率；亏损或无有效值时缺失",
    "pb": "供应商当前口径市净率；无有效值时缺失",
    "total_mv": "按当前供应商快照口径计算的总市值，单位元",
    "float_mv": "按当前供应商快照口径计算的流通市值，单位元",
}

FIELD_VALUE_SEMANTICS: dict[str, str] = {
    "prev_close": "trading_day_reference",
    "pe": "provider_snapshot_ratio",
    "pb": "provider_snapshot_ratio",
    "total_mv": "provider_snapshot_valuation",
    "float_mv": "provider_snapshot_valuation",
}

_FIELD_TO_QUOTE_KEY = {
    "last_price": "price",
    "prev_close": "prev_close",
    "change_pct": "change_pct",
    "open": "open",
    "high": "high",
    "low": "low",
    "amount": "amount",
    "volume": "volume",
    "turnover_rate": "turnover_rate",
    "volume_ratio": "volume_ratio",
    "pe": "pe",
    "pb": "pb",
    "total_mv": "total_mv",
    "float_mv": "float_mv",
}

_TDX_API_FIELDS = frozenset({
    "last_price", "prev_close", "change_pct", "open", "high", "low",
    "amount", "volume",
})
_TENCENT_FIELDS = frozenset(FIELD_UNITS)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _exchange_for_code(code: str) -> str:
    if code.startswith(("4", "8", "92")):
        return "bj"
    if code.startswith(("5", "6", "9")):
        return "sh"
    return "sz"


def _canonical_symbol(symbol: str) -> str | None:
    raw = str(symbol or "").strip()
    match = _SYMBOL_RE.fullmatch(raw)
    if not match:
        return None
    prefix, code, suffix = match.groups()
    expected = _exchange_for_code(code)
    explicit = (prefix or suffix or "").lower()
    if prefix and suffix and prefix.lower() != suffix.lower():
        return None
    if explicit and explicit != expected:
        return None
    return f"{code}.{expected.upper()}"


def _normalize_symbol(symbol: str) -> str | None:
    canonical = _canonical_symbol(symbol)
    return canonical[:6] if canonical else None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_trade_day(value: str) -> str:
    raw = str(value or "").strip().replace("/", "-")
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    raise ValueError(f"invalid trading day: {value!r}")


def _inferred_tdx_event_time(fetched_at: datetime, trading_day: str) -> datetime:
    """Conservatively timestamp a TDX snapshot that has no exchange clock.

    During a live session the fetch clock is the closest observable event time.
    In fixed gaps (the 09:25 pause and lunch break) it is clamped to the last
    possible quote event.  Outside the session it is the trading-day close, so
    a previous close can never be mislabeled as a fresh overnight quote.
    """
    fetched_cn = _aware_utc(fetched_at).astimezone(_SHANGHAI)
    day = datetime.strptime(trading_day, "%Y-%m-%d").date()
    if fetched_cn.date() != day:
        return datetime.combine(day, datetime_time(15, 0), tzinfo=_SHANGHAI)

    wall = fetched_cn.timetz().replace(tzinfo=None)
    if datetime_time(9, 15) <= wall < datetime_time(9, 25):
        return fetched_cn
    if datetime_time(9, 25) <= wall < datetime_time(9, 30):
        return datetime.combine(day, datetime_time(9, 25), tzinfo=_SHANGHAI)
    if datetime_time(9, 30) <= wall <= datetime_time(11, 30):
        return fetched_cn
    if datetime_time(11, 30) < wall < datetime_time(13, 0):
        return datetime.combine(day, datetime_time(11, 30), tzinfo=_SHANGHAI)
    if datetime_time(13, 0) <= wall <= datetime_time(15, 0):
        return fetched_cn
    return datetime.combine(day, datetime_time(15, 0), tzinfo=_SHANGHAI)


def _positive(value: Any) -> float | None:
    number = to_float(value)
    return number if number is not None and number > 0 else None


def _finite_nonzero(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) != 0.0
    )


@dataclass(frozen=True)
class QuoteSnapshot:
    """One provider response before it is projected into field observations."""

    values: Mapping[str, Any]
    raw_payload: Any
    event_time: datetime | str | None = None
    trading_day: str | None = None
    source_url: str | None = None
    derived_fields: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _CachedSnapshot:
    snapshot: QuoteSnapshot | None
    fetched_at: datetime
    expires_at: float


class _CachedQuoteAdapter:
    source_id: str
    source_tier: int
    source_meta: Mapping[str, Any]
    supported_fields: frozenset[str]
    always_derived_fields: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        loader: Callable[[str], QuoteSnapshot | Mapping[str, Any] | None],
        ttl_seconds: float = 3.0,
        missing_ttl_seconds: float = 1.0,
        now_fn: Callable[[], datetime] = _utc_now,
        monotonic_fn: Callable[[], float] = time.monotonic,
        trade_day_fn: Callable[[], str] = free_source.quote_data_date,
        max_concurrency: int = 4,
        max_cache_entries: int = 2048,
        lock_stripes: int = 257,
        failure_threshold: int = 3,
        cooldown_seconds: float = 20.0,
    ) -> None:
        if ttl_seconds <= 0 or missing_ttl_seconds <= 0:
            raise ValueError("quote cache TTLs must be positive")
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        if max_cache_entries <= 0 or lock_stripes <= 0:
            raise ValueError("cache and lock bounds must be positive")
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be positive")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be positive")
        self._loader = loader
        self._ttl_seconds = float(ttl_seconds)
        self._missing_ttl_seconds = float(missing_ttl_seconds)
        self._now_fn = now_fn
        self._monotonic_fn = monotonic_fn
        self._trade_day_fn = trade_day_fn
        self._max_concurrency = int(max_concurrency)
        self._max_cache_entries = int(max_cache_entries)
        self._failure_threshold = int(failure_threshold)
        self._cooldown_seconds = float(cooldown_seconds)
        self._slots = threading.BoundedSemaphore(self._max_concurrency)
        self._cache: dict[str, _CachedSnapshot] = {}
        # 固定锁条带取代“每个用户输入代码创建一把永久锁”，
        # 防止公开 API 被大量六位数码撑大内存。同一代码仍会合并并发回源。
        self._lock_stripes = tuple(threading.Lock() for _ in range(int(lock_stripes)))
        self._guard = threading.RLock()
        self._breaker_state = "closed"
        self._consecutive_failures = 0
        self._opened_until = 0.0
        self._half_open_probe_in_flight = False
        self._in_flight = 0
        self._peak_in_flight = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._requests_attempted = 0
        self._requests_succeeded = 0
        self._requests_failed = 0
        self._requests_no_data = 0
        self._last_result = "never"
        self._last_success_at: str | None = None
        self._last_failure_at: str | None = None
        self._breaker_rejections = 0

    def clear_cache(self) -> None:
        with self._guard:
            self._cache.clear()

    def _symbol_lock(self, code: str) -> threading.Lock:
        return self._lock_stripes[hash(code) % len(self._lock_stripes)]

    def _prune_cache_locked(self, now_mono: float) -> None:
        expired = [key for key, value in self._cache.items() if now_mono >= value.expires_at]
        for key in expired:
            self._cache.pop(key, None)
        overflow = len(self._cache) - self._max_cache_entries
        if overflow > 0:
            # dict 保持插入顺序；先剔除最早放入的快照。TTL 只有 1–3s，
            # 不需要为这个小缓存引入更重的 LRU 结构。
            for key in tuple(self._cache)[:overflow]:
                self._cache.pop(key, None)

    def _cached(self, code: str, now_mono: float) -> _CachedSnapshot | None:
        with self._guard:
            self._prune_cache_locked(now_mono)
            entry = self._cache.get(code)
            if entry is not None and now_mono < entry.expires_at:
                self._cache_hits += 1
                return entry
            if entry is not None:
                self._cache.pop(code, None)
            return None

    def _reserve_upstream_call(self) -> bool:
        """Reserve a closed-breaker call or the single half-open probe."""
        now_mono = self._monotonic_fn()
        with self._guard:
            if self._breaker_state == "open":
                if now_mono < self._opened_until:
                    self._breaker_rejections += 1
                    return False
                self._breaker_state = "half_open"
            if self._breaker_state == "half_open":
                if self._half_open_probe_in_flight:
                    self._breaker_rejections += 1
                    return False
                self._half_open_probe_in_flight = True
            return True

    def _record_success(self) -> None:
        with self._guard:
            observed_at = _aware_utc(self._now_fn()).isoformat()
            self._requests_succeeded += 1
            self._last_result = "success"
            self._last_success_at = observed_at
            self._consecutive_failures = 0
            self._breaker_state = "closed"
            self._opened_until = 0.0
            self._half_open_probe_in_flight = False

    def _record_no_data(self) -> None:
        """供应商正常回应但该代码无数据，不污染全局熔断器。"""
        with self._guard:
            observed_at = _aware_utc(self._now_fn()).isoformat()
            self._requests_succeeded += 1
            self._requests_no_data += 1
            self._last_result = "no_data"
            self._last_success_at = observed_at
            self._consecutive_failures = 0
            self._breaker_state = "closed"
            self._opened_until = 0.0
            self._half_open_probe_in_flight = False

    def _record_failure(self) -> None:
        now_mono = self._monotonic_fn()
        with self._guard:
            observed_at = _aware_utc(self._now_fn()).isoformat()
            self._requests_failed += 1
            self._last_result = "failure"
            self._last_failure_at = observed_at
            self._consecutive_failures += 1
            if (
                self._breaker_state == "half_open"
                or self._consecutive_failures >= self._failure_threshold
            ):
                self._breaker_state = "open"
                self._opened_until = now_mono + self._cooldown_seconds
            self._half_open_probe_in_flight = False

    def _call_loader(self, code: str) -> QuoteSnapshot | None:
        # Acquire the process-wide source slot *before* consulting the breaker.
        # Otherwise a burst could reserve many closed-state calls, queue behind
        # the semaphore, and keep hitting a source after the first calls opened
        # the breaker.
        self._slots.acquire()
        if not self._reserve_upstream_call():
            self._slots.release()
            return None
        with self._guard:
            self._requests_attempted += 1
            self._in_flight += 1
            self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
        try:
            transport_failed = False
            try:
                snapshot = self._coerce_snapshot(self._loader(code))
            except Exception:  # Never expose provider exception text or secrets.
                snapshot = None
                transport_failed = True
            if transport_failed:
                self._record_failure()
            elif snapshot is None:
                self._record_no_data()
            else:
                self._record_success()
            return snapshot
        finally:
            with self._guard:
                self._in_flight -= 1
            self._slots.release()

    def health(self) -> dict[str, Any]:
        """Read-only operational state for this public-source adapter."""
        now_mono = self._monotonic_fn()
        with self._guard:
            entries = [
                item for item in self._cache.values()
                if now_mono < item.expires_at
            ]
            effective_state = self._breaker_state
            if effective_state == "open" and now_mono >= self._opened_until:
                # Report that a half-open probe is ready without mutating state.
                effective_state = "half_open"
            remaining = (
                max(0.0, self._opened_until - now_mono)
                if self._breaker_state == "open"
                else 0.0
            )
            return {
                "breaker": {
                    "state": effective_state,
                    "consecutive_failures": self._consecutive_failures,
                    "failure_threshold": self._failure_threshold,
                    "cooldown_seconds": self._cooldown_seconds,
                    "cooldown_remaining_seconds": round(remaining, 3),
                    "probe_in_flight": self._half_open_probe_in_flight,
                    "rejected_calls": self._breaker_rejections,
                },
                "concurrency": {
                    "limit": self._max_concurrency,
                    "in_flight": self._in_flight,
                    "peak_in_flight": self._peak_in_flight,
                },
                "cache": {
                    "enabled": True,
                    "ttl_seconds": self._ttl_seconds,
                    "missing_ttl_seconds": self._missing_ttl_seconds,
                    "entries": len(entries),
                    "positive_entries": sum(
                        item.snapshot is not None for item in entries
                    ),
                    "negative_entries": sum(
                        item.snapshot is None for item in entries
                    ),
                    "hits": self._cache_hits,
                    "misses": self._cache_misses,
                },
                "requests": {
                    "attempted": self._requests_attempted,
                    "succeeded": self._requests_succeeded,
                    "failed": self._requests_failed,
                    "no_data": self._requests_no_data,
                    "last_result": self._last_result,
                    "last_success_at": self._last_success_at,
                    "last_failure_at": self._last_failure_at,
                },
            }

    def _coerce_snapshot(
        self, value: QuoteSnapshot | Mapping[str, Any] | None
    ) -> QuoteSnapshot | None:
        if value is None:
            return None
        if isinstance(value, QuoteSnapshot):
            return value
        return QuoteSnapshot(values=dict(value), raw_payload=dict(value))

    def _load_snapshot(self, code: str) -> _CachedSnapshot:
        now_mono = self._monotonic_fn()
        hit = self._cached(code, now_mono)
        if hit is not None:
            return hit

        # Double-checked per-symbol locking coalesces a field fan-out or a
        # concurrent request burst without serializing unrelated securities.
        with self._symbol_lock(code):
            now_mono = self._monotonic_fn()
            hit = self._cached(code, now_mono)
            if hit is not None:
                return hit
            with self._guard:
                self._cache_misses += 1
            snapshot = self._call_loader(code)
            # ``fetched_at`` means the payload was available to Hermes, not
            # merely that an HTTP/TCP request had started.
            fetched_at = _aware_utc(self._now_fn())
            if snapshot is not None:
                ttl = self._ttl_seconds
            else:
                ttl = self._missing_ttl_seconds
                # A negative cache entry must never hide the moment an open
                # breaker becomes eligible for its half-open recovery probe.
                with self._guard:
                    if self._breaker_state == "open":
                        remaining = max(
                            0.0,
                            self._opened_until - self._monotonic_fn(),
                        )
                        ttl = min(ttl, remaining)
            entry = _CachedSnapshot(
                snapshot, fetched_at, self._monotonic_fn() + ttl
            )
            with self._guard:
                self._cache[code] = entry
                self._prune_cache_locked(self._monotonic_fn())
            return entry

    def _metadata(
        self, snapshot: QuoteSnapshot, fetched_at: datetime
    ) -> tuple[datetime | str, str]:
        trading_day = _iso_trade_day(
            snapshot.trading_day or self._trade_day_fn()
        )
        event_time = snapshot.event_time or _inferred_tdx_event_time(
            fetched_at, trading_day
        )
        return event_time, trading_day

    def fetch(self, symbol: str, field: str) -> AdapterResult | None:
        if field not in self.supported_fields:
            return None
        code = _normalize_symbol(symbol)
        if code is None:
            return None
        entry = self._load_snapshot(code)
        snapshot = entry.snapshot
        if snapshot is None:
            return None
        quote_key = _FIELD_TO_QUOTE_KEY[field]
        value = snapshot.values.get(quote_key)
        if value is None:
            return None
        event_time, trading_day = self._metadata(snapshot, entry.fetched_at)
        expected_fields = tuple(
            item for item in sorted(self.supported_fields)
            if item in _FIELD_TO_QUOTE_KEY
        )
        present_count = sum(
            snapshot.values.get(_FIELD_TO_QUOTE_KEY[item]) is not None
            for item in expected_fields
        )
        expected_count = len(expected_fields)
        batch_status = (
            "complete" if expected_count and present_count == expected_count
            else "partial"
        )
        batch_key = "|".join((
            self.source_id,
            code,
            trading_day,
            str(event_time),
            entry.fetched_at.isoformat(),
        ))
        batch_id = (
            f"quote-batch/{self.source_id}/"
            f"{hashlib.sha256(batch_key.encode('utf-8')).hexdigest()[:24]}"
        )
        return AdapterResult(
            value=value,
            raw_payload=snapshot.raw_payload,
            event_time=event_time,
            available_at=entry.fetched_at,
            fetched_at=entry.fetched_at,
            trading_day=trading_day,
            unit=FIELD_UNITS[field],
            source_id=self.source_id,
            source_tier=self.source_tier,
            source_url=snapshot.source_url,
            derived=(
                field in snapshot.derived_fields
                or field in self.always_derived_fields
            ),
            event_time_inferred=snapshot.event_time is None,
            batch_id=batch_id,
            batch_status=batch_status,
            batch_expected_count=expected_count,
            batch_present_count=present_count,
        )


def _tdx_api_loader(code: str) -> QuoteSnapshot | None:
    # 交互证据网关只读取当前快照，不等待后台 TDX 数据集刷新。等待会让首个
    # 字段把整张 8 项诊断证据卡的预算吃光；腾讯会在同一路由内即时兜底。
    quote = tdx_api_source.quote(code, max_age=30, wait=0)
    if not quote:
        return None
    age_seconds = to_float(quote.get("age_seconds"))
    observed_at = (
        _utc_now() - timedelta(seconds=max(0.0, age_seconds))
        if age_seconds is not None
        else None
    )
    return QuoteSnapshot(
        values=quote,
        raw_payload={"code": code, "quote": quote},
        event_time=observed_at,
        trading_day=free_source.quote_data_date(),
        # 虚拟证据 URI：不暴露真实 BASE，也不带 Key。
        source_url=f"tdx-api://realtime/quote/{code}",
    )


class TdxApiQuoteAdapter(_CachedQuoteAdapter):
    source_id = "tdx_api_quote"
    source_tier = 1
    supported_fields = _TDX_API_FIELDS
    source_meta = {
        "provider": "Customer-authorized TDX Data API v2.8",
        "purpose": "A-share primary quote evidence with source-reported age",
        "refresh_seconds": 3,
        "rate_limit": "60 requests/minute; multi-code endpoint available",
        "license": "customer-authorized read-only access",
        "owner": "market-data",
        "cost": "customer-provided access; no per-call points",
        "known_risks": "customer service dependency; current upstream exposes one effective depth level",
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("loader", _tdx_api_loader)
        kwargs.setdefault("max_concurrency", 2)
        kwargs.setdefault("failure_threshold", 3)
        kwargs.setdefault("cooldown_seconds", 20.0)
        super().__init__(**kwargs)


def _parse_tencent_event_time(fields: list[str]) -> datetime | None:
    raw = str(fields[30] if len(fields) > 30 else "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 14:
        return None
    try:
        observed = datetime.strptime(digits[:14], "%Y%m%d%H%M%S").replace(
            tzinfo=_SHANGHAI
        )
    except ValueError:
        return None
    wall = observed.timetz().replace(tzinfo=None)
    day = observed.date()
    # Tencent's field 30 can continue advancing after the exchange closes.  It
    # is a provider record time, not evidence of a new executable quote.  Clamp
    # fixed market gaps and post-close updates to the last possible quote event.
    if wall < datetime_time(9, 15):
        return None
    if datetime_time(9, 25) <= wall < datetime_time(9, 30):
        return datetime.combine(day, datetime_time(9, 25), tzinfo=_SHANGHAI)
    if datetime_time(11, 30) < wall < datetime_time(13, 0):
        return datetime.combine(day, datetime_time(11, 30), tzinfo=_SHANGHAI)
    if wall > datetime_time(15, 0):
        return datetime.combine(day, datetime_time(15, 0), tzinfo=_SHANGHAI)
    return observed


def _parse_tencent_quote(fields: list[str]) -> dict[str, Any] | None:
    """Normalize Tencent's tilde quote row without discarding its timestamp."""
    if len(fields) < 50:
        return None
    code = str(fields[2] or "").strip()
    price = _positive(fields[3])
    if price is None:
        return None
    change_pct = to_float(fields[32])
    # Beijing shares use a 30% limit while the other active A-share boards use
    # at most 20%.  Keep a small rounding allowance, but reject unit corruption.
    max_change = 31.0 if code.startswith(("4", "8", "920")) else 21.0
    if change_pct is not None and abs(change_pct) > max_change:
        return None

    amount_wan = to_float(fields[37])
    raw_volume = to_float(fields[6])
    if raw_volume is None:
        volume = None
    elif code.startswith("688"):
        volume = int(raw_volume)
    else:
        volume = int(raw_volume * 100)
    float_yi = to_float(fields[44])
    total_yi = to_float(fields[45])
    pe = to_float(fields[39])
    pb = to_float(fields[46])
    return {
        "price": price,
        "prev_close": _positive(fields[4]),
        "change_pct": change_pct,
        "open": _positive(fields[5]),
        "high": _positive(fields[33]),
        "low": _positive(fields[34]),
        "amount": amount_wan * 10_000 if amount_wan is not None else None,
        "volume": volume,
        "turnover_rate": to_float(fields[38]),
        "volume_ratio": to_float(fields[49]),
        "pe": pe if pe not in (None, 0) else None,
        "pb": pb if pb not in (None, 0) else None,
        "total_mv": total_yi * 100_000_000 if total_yi and total_yi > 0 else None,
        "float_mv": float_yi * 100_000_000 if float_yi and float_yi > 0 else None,
    }


def _tencent_loader(code: str) -> QuoteSnapshot | None:
    symbol = tencent_source._symbol(code)
    fields = tencent_source._fetch([symbol], raise_transport=True).get(symbol)
    if not fields:
        return None
    quote = _parse_tencent_quote(fields)
    if quote is None:
        return None
    event_time = _parse_tencent_event_time(fields)
    return QuoteSnapshot(
        values=quote,
        raw_payload={"symbol": symbol, "fields": fields},
        event_time=event_time,
        trading_day=event_time.astimezone(_SHANGHAI).date().isoformat()
        if event_time is not None else None,
        source_url=f"https://qt.gtimg.cn/q={symbol}",
    )


class TencentQuoteAdapter(_CachedQuoteAdapter):
    source_id = "tencent_quote"
    source_tier = 1
    supported_fields = _TENCENT_FIELDS
    source_meta = {
        "provider": "Tencent Finance qt.gtimg.cn",
        "purpose": "A-share timestamped quote primary and valuation fields",
        "refresh_seconds": 3,
        "rate_limit": "provider controlled; max 4 concurrent; short-TTL cache",
        "license": "public endpoint; upstream terms apply",
        "owner": "market-data",
        "cost": "free; no API key",
        "known_risks": "public endpoint without SLA; schema may change",
    }

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("loader", _tencent_loader)
        kwargs.setdefault("max_concurrency", 4)
        kwargs.setdefault("failure_threshold", 3)
        kwargs.setdefault("cooldown_seconds", 15.0)
        super().__init__(**kwargs)


def runtime_field_policies() -> tuple[FieldPolicy, ...]:
    """Validated units, ranges and field-level source routes."""
    base = {
        "market": "CN-A",
        "route_version": "tdx-api-runtime-v2",
        "live_max_age_seconds": 12.0,
        "delayed_max_age_seconds": 300.0,
    }
    # TDX HTTP API 主源带 age_seconds，可还原观测时点；腾讯是独立的
    # 基础行情备源，并补齐 TDX API 当前不提供的换手率/量比/估值/市值。
    base_quotes = ("tdx_api_quote", "tencent_quote")
    turnover = ("tencent_quote",)
    tencent = ("tencent_quote",)

    def make(field: str, route: tuple[str, ...], **kwargs: Any) -> FieldPolicy:
        return FieldPolicy(
            field=field,
            unit=FIELD_UNITS[field],
            route=route,
            meaning=FIELD_SEMANTICS[field],
            value_semantics=FIELD_VALUE_SEMANTICS.get(
                field, "point_in_time_snapshot"
            ),
            batch_scope="provider_symbol_quote_snapshot",
            **base,
            **kwargs,
        )

    return (
        make("last_price", base_quotes, min_value=0.001, max_value=100_000,
             conflict_tolerance_pct=0.5),
        make("prev_close", base_quotes, min_value=0.001, max_value=100_000,
             conflict_tolerance_pct=0.2),
        make("change_pct", base_quotes, min_value=-31.5, max_value=31.5,
             conflict_tolerance_abs=0.2),
        make("open", base_quotes, min_value=0.001, max_value=100_000,
             conflict_tolerance_pct=0.5),
        make("high", base_quotes, min_value=0.001, max_value=100_000,
             conflict_tolerance_pct=0.5),
        make("low", base_quotes, min_value=0.001, max_value=100_000,
             conflict_tolerance_pct=0.5),
        make("amount", base_quotes, min_value=0, max_value=10**15,
             conflict_tolerance_pct=5.0),
        make("volume", base_quotes, min_value=0, max_value=10**13,
             conflict_tolerance_pct=5.0),
        make("turnover_rate", turnover, min_value=0, max_value=200,
             conflict_tolerance_abs=0.5),
        make("volume_ratio", tencent, min_value=0, max_value=10_000),
        make("pe", tencent, min_value=-100_000, max_value=100_000,
             validator=_finite_nonzero),
        make("pb", tencent, min_value=-10_000, max_value=10_000,
             validator=_finite_nonzero),
        make("total_mv", tencent, min_value=1, max_value=10**16),
        make("float_mv", tencent, min_value=1, max_value=10**16),
    )


def create_runtime_gateway(data_dir: str | Path) -> MarketDataGateway:
    """Create the process-wide quote gateway and its durable evidence DB."""
    root = Path(data_dir).expanduser()
    store = EvidenceStore(root / "evidence" / "market_data.sqlite3")
    adapters = {
        "tdx_api_quote": TdxApiQuoteAdapter(),
        "tencent_quote": TencentQuoteAdapter(),
    }
    return MarketDataGateway(
        store=store,
        policies=runtime_field_policies(),
        adapters=adapters,
        symbol_normalizer=_canonical_symbol,
    )


__all__ = [
    "FIELD_SEMANTICS",
    "FIELD_UNITS",
    "QuoteSnapshot",
    "TdxApiQuoteAdapter",
    "TencentQuoteAdapter",
    "create_runtime_gateway",
    "runtime_field_policies",
]
