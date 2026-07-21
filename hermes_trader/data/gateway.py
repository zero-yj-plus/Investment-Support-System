"""Field-level market-data routing with quality and PIT evidence contracts.

This module is deliberately adapter-agnostic. Existing data modules can be
wrapped later without changing the gateway or evidence schema.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import math
from statistics import median
from typing import Any, Callable, Mapping, Protocol

from .evidence_store import EvidenceStore, normalize_timestamp, sanitize_url


_FRESHNESS = {"live", "delayed", "stale", "missing"}
_QUALITY = {"ok", "degraded", "conflict", "missing"}
_BATCH_STATUS = {"complete", "partial", "unknown", "missing"}
FIELD_CONTRACT_VERSION = "field-contract-v2"

_DEGRADATION_MESSAGES = {
    "all_sources_unavailable": "所有配置来源均不可用或字段无效",
    "fallback_source": "主源不可用，当前字段使用备用源",
    "cross_source_conflict": "多个来源对同一字段存在超阈值分歧",
    "event_time_inferred": "上游未提供业务时点，当前时点由系统保守推断",
    "stale": "数据超过该字段的新鲜度门槛",
    "derived_value": "该字段由上游字段确定性计算得到",
    "batch_partial": "同一上游批次只返回了部分预期字段",
}


@dataclass(frozen=True)
class DataPoint:
    symbol: str
    market: str
    field: str
    value: Any
    unit: str
    source_id: str
    source_tier: int
    source_url: str | None
    event_time: str
    available_at: str
    fetched_at: str
    trading_day: str
    freshness: str
    fallback_level: int
    quality_status: str
    raw_payload_hash: str | None
    route_version: str
    derived: bool = False
    evidence_id: str | None = None
    schema_version: str = "market-data-v1"
    missing_reason: str | None = None
    observation_role: str = "selected"
    event_time_inferred: bool = False
    quality_reasons: tuple[str, ...] = ()
    field_meaning: str = ""
    value_semantics: str = "point_in_time"
    batch_id: str | None = None
    batch_status: str = "unknown"
    batch_expected_count: int | None = None
    batch_present_count: int | None = None
    degraded_reason: str | None = None
    contract_version: str = FIELD_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.freshness not in _FRESHNESS:
            raise ValueError(f"invalid freshness: {self.freshness}")
        if self.quality_status not in _QUALITY:
            raise ValueError(f"invalid quality_status: {self.quality_status}")
        if self.fallback_level < 0:
            raise ValueError("fallback_level must be >= 0")
        if self.observation_role not in {"selected", "cross_check"}:
            raise ValueError(f"invalid observation_role: {self.observation_role}")
        if self.batch_status not in _BATCH_STATUS:
            raise ValueError(f"invalid batch_status: {self.batch_status}")
        expected = self.batch_expected_count
        present = self.batch_present_count
        if expected is not None and expected < 0:
            raise ValueError("batch_expected_count must be >= 0")
        if present is not None and present < 0:
            raise ValueError("batch_present_count must be >= 0")
        if expected is not None and present is not None and present > expected:
            raise ValueError("batch_present_count cannot exceed batch_expected_count")
        meaning = str(self.field_meaning or self.field).strip()
        semantics = str(self.value_semantics or "point_in_time").strip()
        object.__setattr__(self, "field_meaning", meaning)
        object.__setattr__(self, "value_semantics", semantics)
        if self.contract_version != FIELD_CONTRACT_VERSION:
            raise ValueError(f"contract_version must be {FIELD_CONTRACT_VERSION}")
        reason = str(self.degraded_reason or "").strip() or None
        if self.quality_status == "ok":
            reason = None
        elif reason is None:
            if self.missing_reason:
                reason = str(self.missing_reason).strip()
            else:
                messages = [
                    _DEGRADATION_MESSAGES.get(code, code)
                    for code in self.quality_reasons
                ]
                reason = "；".join(dict.fromkeys(messages)) or "数据质量未达到完整可用"
        object.__setattr__(self, "degraded_reason", reason)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "DataPoint":
        allowed = cls.__dataclass_fields__
        values = {key: value for key, value in record.items() if key in allowed}
        values["quality_reasons"] = tuple(values.get("quality_reasons") or ())
        return cls(**values)


@dataclass(frozen=True)
class FieldPolicy:
    field: str
    unit: str
    route: tuple[str, ...]
    meaning: str = ""
    value_semantics: str = "point_in_time"
    batch_scope: str = "symbol_snapshot"
    market: str = "CN-A"
    route_version: str = "v1"
    live_max_age_seconds: float = 15.0
    delayed_max_age_seconds: float = 300.0
    live_sessions: frozenset[str] = frozenset({"intraday", "tail"})
    conflict_tolerance_pct: float | None = None
    conflict_tolerance_abs: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    validator: Callable[[Any], bool] | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not self.field or not self.unit:
            raise ValueError("field and unit are required")
        if not self.route or any(not str(item).strip() for item in self.route):
            raise ValueError("route must contain at least one source_id")
        if self.live_max_age_seconds < 0:
            raise ValueError("live_max_age_seconds must be >= 0")
        if self.delayed_max_age_seconds < self.live_max_age_seconds:
            raise ValueError("delayed_max_age_seconds must be >= live_max_age_seconds")
        object.__setattr__(self, "meaning", str(self.meaning or self.field).strip())
        object.__setattr__(
            self,
            "value_semantics",
            str(self.value_semantics or "point_in_time").strip(),
        )
        object.__setattr__(
            self,
            "batch_scope",
            str(self.batch_scope or "symbol_snapshot").strip(),
        )


@dataclass(frozen=True)
class AdapterResult:
    value: Any
    raw_payload: Any
    event_time: datetime | str | None
    trading_day: str
    unit: str | None = None
    source_id: str | None = None
    source_tier: int | None = None
    source_url: str | None = None
    available_at: datetime | str | None = None
    fetched_at: datetime | str | None = None
    freshness: str | None = None
    derived: bool = False
    # True only when the adapter had to infer the market-event clock because
    # the supplier payload did not contain one.  This is deliberately
    # independent from ``derived``: estimating when a quote was observed does
    # not turn the quote value itself into a derived value.
    event_time_inferred: bool = False
    batch_id: str | None = None
    batch_status: str = "unknown"
    batch_expected_count: int | None = None
    batch_present_count: int | None = None


class MarketDataAdapter(Protocol):
    source_id: str
    source_tier: int

    def fetch(self, symbol: str, field: str) -> AdapterResult | Mapping[str, Any] | None:
        ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class MarketDataGateway:
    """Resolve a field through ordered sources and persist auditable evidence."""

    def __init__(
        self,
        *,
        store: EvidenceStore,
        policies: list[FieldPolicy] | tuple[FieldPolicy, ...],
        adapters: Mapping[str, MarketDataAdapter],
        now_fn: Callable[[], datetime] = _utc_now,
        symbol_normalizer: Callable[[str], str | None] | None = None,
    ):
        self.store = store
        policy_list = tuple(policies)
        self.policies = {policy.field: policy for policy in policy_list}
        self.adapters = dict(adapters)
        self.now_fn = now_fn
        self.symbol_normalizer = symbol_normalizer or (
            lambda value: str(value or "").strip() or None
        )
        if len(self.policies) != len(policy_list):
            raise ValueError("duplicate field policy")
        for policy in self.policies.values():
            missing = [source_id for source_id in policy.route if source_id not in self.adapters]
            if missing:
                raise ValueError(f"missing adapters for {policy.field}: {missing}")
        self._register_sources()

    def _register_sources(self) -> None:
        fields_by_source: dict[str, list[str]] = {}
        for policy in self.policies.values():
            for source_id in policy.route:
                fields_by_source.setdefault(source_id, []).append(policy.field)
        for source_id, fields in fields_by_source.items():
            adapter = self.adapters[source_id]
            meta = dict(getattr(adapter, "source_meta", {}) or {})
            self.store.register_source(
                source_id,
                tier=int(getattr(adapter, "source_tier", meta.get("tier", 2))),
                provider=str(meta.get("provider") or source_id),
                purpose=str(meta.get("purpose") or "market data adapter"),
                fields=fields,
                refresh_seconds=meta.get("refresh_seconds"),
                rate_limit=str(meta.get("rate_limit") or "unknown"),
                license=str(meta.get("license") or "unknown"),
                owner=str(meta.get("owner") or "unassigned"),
                cost=str(meta.get("cost") or "free"),
                known_risks=str(meta.get("known_risks") or "unknown"),
                enabled=bool(meta.get("enabled", True)),
            )

    def health(self) -> dict[str, Any]:
        """Return a read-only, secret-free snapshot of runtime source health.

        Adapters are intentionally treated as untrusted here: only the small
        operational schema below is copied out.  Provider URLs, credentials,
        exception strings and arbitrary adapter metadata can therefore never
        leak through this endpoint.
        """

        section_keys = {
            "breaker": {
                "state", "consecutive_failures", "failure_threshold",
                "cooldown_seconds", "cooldown_remaining_seconds",
                "probe_in_flight", "rejected_calls",
            },
            "concurrency": {"limit", "in_flight", "peak_in_flight"},
            "cache": {
                "enabled", "ttl_seconds", "missing_ttl_seconds", "entries",
                "positive_entries", "negative_entries", "hits", "misses",
            },
            "requests": {
                "attempted", "succeeded", "failed", "no_data",
                "last_result", "last_success_at", "last_failure_at",
            },
        }
        sources: dict[str, dict[str, Any]] = {}
        for source_id, adapter in sorted(self.adapters.items()):
            raw: Mapping[str, Any] = {}
            adapter_health = getattr(adapter, "health", None)
            if callable(adapter_health):
                try:
                    candidate = adapter_health()
                    if isinstance(candidate, Mapping):
                        raw = candidate
                except Exception:
                    # Health inspection must never make the data path fail.
                    raw = {}
            if not raw:
                sources[source_id] = {
                    "breaker": {"state": "unmanaged"},
                    "concurrency": {"limit": None, "in_flight": None},
                    "cache": {"enabled": False},
                    "requests": {},
                }
                continue

            safe: dict[str, Any] = {}
            for section, allowed in section_keys.items():
                values = raw.get(section)
                if not isinstance(values, Mapping):
                    continue
                safe_values: dict[str, Any] = {}
                for key, value in values.items():
                    if key not in allowed:
                        continue
                    if key == "state":
                        safe_values[key] = (
                            value
                            if value in {"closed", "open", "half_open", "unmanaged"}
                            else "unknown"
                        )
                    elif key == "last_result":
                        safe_values[key] = (
                            value if value in {"never", "success", "failure", "no_data"}
                            else "unknown"
                        )
                    elif key in {"last_success_at", "last_failure_at"}:
                        safe_values[key] = (
                            str(value)[:40] if isinstance(value, str) else None
                        )
                    elif (
                        value is None
                        or isinstance(value, (int, bool))
                        or (isinstance(value, float) and math.isfinite(value))
                    ):
                        safe_values[key] = value
                safe[section] = safe_values
            sources[source_id] = safe
        return {
            "schema_version": "market-data-health-v1",
            "contract_version": FIELD_CONTRACT_VERSION,
            "policy_count": len(self.policies),
            "sources": sources,
        }

    def contract(self) -> dict[str, Any]:
        """Return the stable, secret-free field contract used by every point."""
        return {
            "ok": True,
            "schema_version": FIELD_CONTRACT_VERSION,
            "market": "CN-A",
            "fields": {
                name: {
                    "meaning": policy.meaning,
                    "unit": policy.unit,
                    "value_semantics": policy.value_semantics,
                    "route": list(policy.route),
                    "route_version": policy.route_version,
                    "batch_scope": policy.batch_scope,
                    "live_max_age_seconds": policy.live_max_age_seconds,
                    "delayed_max_age_seconds": policy.delayed_max_age_seconds,
                    "live_sessions": sorted(policy.live_sessions),
                }
                for name, policy in sorted(self.policies.items())
            },
        }

    @staticmethod
    def _coerce_result(value: AdapterResult | Mapping[str, Any]) -> AdapterResult:
        if isinstance(value, AdapterResult):
            return value
        return AdapterResult(**dict(value))

    @staticmethod
    def _valid_value(policy: FieldPolicy, value: Any) -> bool:
        if value is None:
            return False
        if policy.validator is not None:
            try:
                if not bool(policy.validator(value)):
                    return False
            except Exception:
                return False
        if policy.min_value is not None or policy.max_value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            number = float(value)
            if not math.isfinite(number):
                return False
            if policy.min_value is not None and number < policy.min_value:
                return False
            if policy.max_value is not None and number > policy.max_value:
                return False
        return True

    @staticmethod
    def _freshness(
        policy: FieldPolicy,
        *,
        event_time: str,
        now: str,
        session: str,
        supplied: str | None,
    ) -> str:
        age = max(0.0, (_as_datetime(now) - _as_datetime(event_time)).total_seconds())
        if session in policy.live_sessions and age <= policy.live_max_age_seconds:
            computed = "live"
        elif age <= policy.delayed_max_age_seconds:
            computed = "delayed"
        else:
            computed = "stale"

        # An adapter may downgrade its own observation but may never upgrade the
        # age/session result (for example, an old payload labelled as "live").
        if supplied in {"live", "delayed", "stale"}:
            rank = {"live": 0, "delayed": 1, "stale": 2}
            return max((computed, str(supplied)), key=rank.__getitem__)
        return computed

    @staticmethod
    def _has_conflict(policy: FieldPolicy, values: list[Any]) -> bool:
        numeric = [
            float(value)
            for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value))
        ]
        if len(numeric) < 2:
            return False
        spread_abs = max(numeric) - min(numeric)
        if (
            policy.conflict_tolerance_abs is not None
            and spread_abs > policy.conflict_tolerance_abs
        ):
            return True
        if policy.conflict_tolerance_pct is None:
            return False
        middle = abs(median(numeric))
        spread_pct = (spread_abs / middle * 100.0) if middle else (
            math.inf if spread_abs else 0.0
        )
        return spread_pct > policy.conflict_tolerance_pct

    def _missing_point(
        self,
        *,
        symbol: str,
        policy: FieldPolicy,
        market: str,
        at: datetime | str,
        reason: str,
        source_id: str,
    ) -> DataPoint:
        timestamp = normalize_timestamp(at)
        if timestamp is None:
            timestamp = normalize_timestamp(self.now_fn())
        adapter = self.adapters.get(source_id)
        return DataPoint(
            symbol=symbol,
            market=market,
            field=policy.field,
            value=None,
            unit=policy.unit,
            source_id=source_id,
            source_tier=int(getattr(adapter, "source_tier", 0)),
            source_url=None,
            event_time=timestamp,
            available_at=timestamp,
            fetched_at=timestamp,
            trading_day=timestamp[:10],
            freshness="missing",
            fallback_level=len(policy.route),
            quality_status="missing",
            raw_payload_hash=None,
            route_version=policy.route_version,
            missing_reason=reason,
            quality_reasons=("all_sources_unavailable",),
            field_meaning=policy.meaning,
            value_semantics=policy.value_semantics,
            batch_status="missing",
            batch_expected_count=0,
            batch_present_count=0,
            degraded_reason=reason,
        )

    def get(
        self,
        symbol: str,
        field: str,
        *,
        market: str | None = None,
        session: str = "intraday",
        as_of: datetime | str | None = None,
    ) -> DataPoint:
        symbol = self.symbol_normalizer(symbol)
        if not symbol:
            raise ValueError("invalid market symbol")
        policy = self.policies.get(field)
        if policy is None:
            raise KeyError(f"unknown field policy: {field}")
        market = market or policy.market

        # PIT is storage-only by contract. This branch occurs before any adapter lookup.
        if as_of is not None:
            record = self.store.query_as_of(symbol, field, as_of, market=market)
            if record is not None:
                return DataPoint.from_record(record)
            return self._missing_point(
                symbol=symbol,
                policy=policy,
                market=market,
                at=as_of,
                reason="no point-in-time evidence available",
                source_id="evidence_store",
            )

        now = normalize_timestamp(self.now_fn())
        if now is None:
            raise ValueError("now_fn returned no timestamp")
        candidates: list[dict[str, Any]] = []
        for fallback_level, source_id in enumerate(policy.route):
            adapter = self.adapters[source_id]
            try:
                raw_result = adapter.fetch(symbol, field)
            except Exception:
                continue
            if raw_result is None:
                continue
            try:
                result = self._coerce_result(raw_result)
            except (TypeError, ValueError):
                continue
            actual_source = result.source_id or source_id
            if actual_source != source_id:
                continue
            if result.unit is not None and result.unit != policy.unit:
                continue
            if not self._valid_value(policy, result.value):
                continue

            fetched_at = normalize_timestamp(result.fetched_at or now)
            event_inferred = bool(
                result.event_time_inferred or result.event_time is None
            )
            event_time = normalize_timestamp(result.event_time or fetched_at)
            available_at = normalize_timestamp(result.available_at or fetched_at)
            if not fetched_at or not event_time or not available_at:
                continue
            if not (
                _as_datetime(event_time)
                <= _as_datetime(available_at)
                <= _as_datetime(fetched_at)
            ):
                continue
            freshness = self._freshness(
                policy,
                event_time=event_time,
                now=now,
                session=session,
                supplied=result.freshness,
            )
            raw_payload = (
                result.raw_payload
                if result.raw_payload is not None
                else {
                    "field": field,
                    "symbol": symbol,
                    "value": result.value,
                    "event_time": event_time,
                }
            )
            raw_hash = self.store.put_raw_payload(
                source_id,
                raw_payload,
                fetched_at=fetched_at,
                source_url=result.source_url,
            )
            candidates.append(
                {
                    "fallback_level": fallback_level,
                    "source_id": source_id,
                    "source_tier": int(
                        result.source_tier
                        if result.source_tier is not None
                        else getattr(adapter, "source_tier", 2)
                    ),
                    "source_url": sanitize_url(result.source_url),
                    "value": result.value,
                    "event_time": event_time,
                    "available_at": available_at,
                    "fetched_at": fetched_at,
                    "trading_day": str(result.trading_day),
                    "freshness": freshness,
                    "raw_hash": raw_hash,
                    "derived": bool(result.derived),
                    "event_inferred": event_inferred,
                    "batch_id": result.batch_id,
                    "batch_status": (
                        result.batch_status
                        if result.batch_status in _BATCH_STATUS
                        else "unknown"
                    ),
                    "batch_expected_count": result.batch_expected_count,
                    "batch_present_count": result.batch_present_count,
                }
            )

        if not candidates:
            return self._missing_point(
                symbol=symbol,
                policy=policy,
                market=market,
                at=now,
                reason="all configured sources unavailable or invalid",
                source_id=policy.route[0],
            )

        candidates.sort(key=lambda item: item["fallback_level"])
        conflict = self._has_conflict(policy, [item["value"] for item in candidates])
        points: list[DataPoint] = []
        for point_index, item in enumerate(candidates):
            reasons: list[str] = []
            if conflict:
                quality = "conflict"
                reasons.append("cross_source_conflict")
            if item["fallback_level"] > 0:
                reasons.append("fallback_source")
            if item["event_inferred"]:
                reasons.append("event_time_inferred")
            if item["freshness"] == "stale":
                reasons.append("stale")
            if item["derived"]:
                reasons.append("derived_value")
            if item["batch_status"] == "partial":
                reasons.append("batch_partial")
            if not conflict and reasons:
                quality = "degraded"
            elif not conflict:
                quality = "ok"
            points.append(
                DataPoint(
                    symbol=symbol,
                    market=market,
                    field=field,
                    value=item["value"],
                    unit=policy.unit,
                    source_id=item["source_id"],
                    source_tier=item["source_tier"],
                    source_url=item["source_url"],
                    event_time=item["event_time"],
                    available_at=item["available_at"],
                    fetched_at=item["fetched_at"],
                    trading_day=item["trading_day"],
                    freshness=item["freshness"],
                    fallback_level=item["fallback_level"],
                    quality_status=quality,
                    raw_payload_hash=item["raw_hash"],
                    route_version=policy.route_version,
                    derived=item["derived"],
                    observation_role="selected" if point_index == 0 else "cross_check",
                    event_time_inferred=item["event_inferred"],
                    quality_reasons=tuple(reasons),
                    field_meaning=policy.meaning,
                    value_semantics=policy.value_semantics,
                    batch_id=item["batch_id"],
                    batch_status=item["batch_status"],
                    batch_expected_count=item["batch_expected_count"],
                    batch_present_count=item["batch_present_count"],
                )
            )

        # Persist cross-check candidates first, then link the selected point to them.
        parent_ids: list[tuple[str, str]] = []
        for point in points[1:]:
            parent_ids.append((self.store.save_point(point), "cross_check"))
        selected = points[0]
        evidence_id = self.store.save_point(selected, parents=parent_ids)
        return replace(selected, evidence_id=evidence_id)

    def get_many(
        self,
        symbols: list[str] | tuple[str, ...],
        field: str,
        **kwargs,
    ) -> list[DataPoint]:
        return [self.get(symbol, field, **kwargs) for symbol in symbols]
