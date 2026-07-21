"""Unified provenance metadata for externally sourced data.

This module is deliberately independent from individual adapters.  It defines
how an adapter describes *what* it fetched, *when* the source data was valid,
when Hermes obtained it, and whether the returned content is complete.

Time semantics are intentionally asymmetric:

* ``data_as_of`` is an A-share business time.  Timestamp values are serialized
  in ``Asia/Shanghai``; a date-only value remains a date and must not be
  fabricated into a midnight timestamp.
* ``fetched_at`` is a system acquisition instant and is serialized in UTC.

The content itself is not embedded in this metadata object.  ``content_hash``
is SHA-256 over the existing evidence-store canonical JSON representation, so
any caller holding the content can reproduce and verify the digest.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hmac
import re
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .evidence_store import canonical_hash, canonical_json, sanitize_url


SCHEMA_VERSION = "data-evidence-v1"
CONTENT_HASH_ALGORITHM = "sha256"
BEIJING_TIMEZONE = "Asia/Shanghai"
UTC_TIMEZONE = "UTC"

_BEIJING = ZoneInfo(BEIJING_TIMEZONE)
_EVIDENCE_LEVELS = frozenset({"tier_1", "tier_2", "tier_3"})
_DATA_STATES = frozenset({"complete", "degraded", "missing"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _required_text(value: object, field_name: str, *, max_length: int = 160) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if len(text) > max_length:
        raise ValueError(f"{field_name} exceeds {max_length} characters")
    return text


def _optional_reason(value: object) -> str | None:
    if value is None:
        return None
    reason = str(value).strip()
    if not reason:
        return None
    if len(reason) > 500:
        raise ValueError("degraded_reason exceeds 500 characters")
    return reason


def _parse_aware_datetime(value: datetime | str, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include an explicit timezone")
    return parsed


def normalize_data_as_of(
    value: datetime | date | str | None,
) -> tuple[str | None, str]:
    """Return ``(value, precision)`` using Beijing business-time semantics."""
    if value is None:
        return None, "unknown"
    if isinstance(value, datetime):
        parsed = _parse_aware_datetime(value, "data_as_of")
    elif isinstance(value, date):
        return value.isoformat(), "date"
    else:
        raw = str(value).strip()
        if not raw:
            return None, "unknown"
        try:
            if len(raw) == 10:
                return date.fromisoformat(raw).isoformat(), "date"
        except ValueError as exc:
            raise ValueError("data_as_of must be an ISO-8601 date or timestamp") from exc
        parsed = _parse_aware_datetime(raw, "data_as_of")
    normalized = parsed.astimezone(_BEIJING).isoformat(timespec="microseconds")
    return normalized, "timestamp"


def normalize_fetched_at(value: datetime | str) -> str:
    """Serialize an acquisition instant as microsecond-precision UTC RFC 3339."""
    parsed = _parse_aware_datetime(value, "fetched_at").astimezone(timezone.utc)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_source_url(value: str | None) -> str | None:
    """Return a credential-free HTTP(S) evidence URL or reject it."""
    cleaned = sanitize_url(value)
    if cleaned is None:
        return None
    parts = urlsplit(cleaned)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("source_url must be an absolute HTTP(S) URL")
    return cleaned


def compute_content_hash(content: Any) -> str:
    """Compute the reproducible SHA-256 digest for evidence content."""
    return canonical_hash(content)


def verify_content_hash(content: Any, expected_hash: str | None) -> bool:
    """Constant-time comparison of content against a contract digest."""
    expected = str(expected_hash or "").strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        return False
    return hmac.compare_digest(compute_content_hash(content), expected)


@dataclass(frozen=True)
class EvidenceContract:
    """Serializable provenance contract shared by all data-source adapters."""

    provider_family: str
    api_product: str
    data_as_of: str | None
    fetched_at: str
    degraded_reason: str | None
    parser_version: str
    source_url: str | None
    evidence_level: str
    content_hash: str | None
    data_state: str
    data_as_of_precision: str
    content_present: bool
    schema_version: str = SCHEMA_VERSION
    data_as_of_timezone: str = BEIJING_TIMEZONE
    fetched_at_timezone: str = UTC_TIMEZONE
    content_hash_algorithm: str = CONTENT_HASH_ALGORITHM

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider_family", _required_text(self.provider_family, "provider_family")
        )
        object.__setattr__(
            self, "api_product", _required_text(self.api_product, "api_product")
        )
        object.__setattr__(
            self, "parser_version", _required_text(self.parser_version, "parser_version")
        )
        normalized_as_of, derived_precision = normalize_data_as_of(self.data_as_of)
        if self.data_as_of_precision != derived_precision:
            raise ValueError(
                "data_as_of_precision does not match the serialized data_as_of value"
            )
        object.__setattr__(self, "data_as_of", normalized_as_of)
        object.__setattr__(self, "fetched_at", normalize_fetched_at(self.fetched_at))
        object.__setattr__(self, "source_url", normalize_source_url(self.source_url))
        if self.evidence_level not in _EVIDENCE_LEVELS:
            raise ValueError(
                f"evidence_level must be one of {sorted(_EVIDENCE_LEVELS)}"
            )
        if self.data_state not in _DATA_STATES:
            raise ValueError(f"data_state must be one of {sorted(_DATA_STATES)}")
        if self.data_as_of_precision not in {"date", "timestamp", "unknown"}:
            raise ValueError("invalid data_as_of_precision")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        if self.data_as_of_timezone != BEIJING_TIMEZONE:
            raise ValueError(f"data_as_of_timezone must be {BEIJING_TIMEZONE}")
        if self.fetched_at_timezone != UTC_TIMEZONE:
            raise ValueError(f"fetched_at_timezone must be {UTC_TIMEZONE}")
        if self.content_hash_algorithm != CONTENT_HASH_ALGORITHM:
            raise ValueError(
                f"content_hash_algorithm must be {CONTENT_HASH_ALGORITHM}"
            )

        reason = _optional_reason(self.degraded_reason)
        object.__setattr__(self, "degraded_reason", reason)
        if self.data_state == "complete" and reason is not None:
            raise ValueError("complete evidence cannot carry degraded_reason")
        if self.data_state in {"degraded", "missing"} and reason is None:
            raise ValueError(f"{self.data_state} evidence requires degraded_reason")
        if self.data_state == "missing":
            if self.content_present or self.content_hash is not None:
                raise ValueError("missing evidence cannot claim content or content_hash")
        else:
            if not self.content_present:
                raise ValueError(f"{self.data_state} evidence must contain content")
            if not _SHA256_RE.fullmatch(str(self.content_hash or "")):
                raise ValueError("non-missing evidence requires a SHA-256 content_hash")
        if self.data_state == "complete" and self.data_as_of is None:
            raise ValueError("complete evidence requires data_as_of")

        # Timestamp precision allows a real instant comparison.  Date-only data
        # intentionally does not invent a clock merely to enforce this check.
        if self.data_as_of_precision == "timestamp" and self.data_as_of is not None:
            as_of = _parse_aware_datetime(self.data_as_of, "data_as_of")
            fetched = _parse_aware_datetime(self.fetched_at, "fetched_at")
            if as_of.astimezone(timezone.utc) > fetched.astimezone(timezone.utc):
                raise ValueError("data_as_of cannot be later than fetched_at")

    def to_dict(self) -> dict[str, Any]:
        """Return all fields, including explicit ``None`` values, in stable form."""
        return asdict(self)

    def to_json(self) -> str:
        """Return deterministic compact JSON suitable for ledgers and signatures."""
        return canonical_json(self.to_dict())

    def verify_content(self, content: Any) -> bool:
        if self.data_state == "missing":
            return content is None and self.content_hash is None
        return verify_content_hash(content, self.content_hash)


def build_evidence_contract(
    *,
    content: Any,
    provider_family: str,
    api_product: str,
    data_as_of: datetime | date | str | None,
    fetched_at: datetime | str,
    parser_version: str,
    source_url: str | None,
    evidence_level: str,
    data_state: str,
    degraded_reason: str | None = None,
) -> EvidenceContract:
    """Validate and build metadata without guessing completeness from truthiness.

    ``None`` is the sole missing-content marker.  Empty collections are valid
    content because an authoritative query may truthfully return zero rows.
    Callers must still declare ``data_state`` explicitly; conflicting claims are
    rejected instead of silently rewritten.
    """
    normalized_state = str(data_state or "").strip().lower()
    content_present = content is not None
    if normalized_state == "missing" and content_present:
        raise ValueError("missing evidence requires content=None")
    if normalized_state != "missing" and not content_present:
        raise ValueError("non-missing evidence requires content")

    normalized_as_of, precision = normalize_data_as_of(data_as_of)
    return EvidenceContract(
        provider_family=provider_family,
        api_product=api_product,
        data_as_of=normalized_as_of,
        fetched_at=normalize_fetched_at(fetched_at),
        degraded_reason=degraded_reason,
        parser_version=parser_version,
        source_url=normalize_source_url(source_url),
        evidence_level=str(evidence_level or "").strip().lower(),
        content_hash=(compute_content_hash(content) if content_present else None),
        data_state=normalized_state,
        data_as_of_precision=precision,
        content_present=content_present,
    )


def evidence_contract_from_mapping(record: Mapping[str, Any]) -> EvidenceContract:
    """Re-validate a serialized contract read from an API or durable ledger."""
    allowed = EvidenceContract.__dataclass_fields__
    values = {key: record[key] for key in allowed if key in record}
    return EvidenceContract(**values)
