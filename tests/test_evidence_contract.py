from __future__ import annotations

from datetime import date, datetime, timezone
import json

import pytest

from hermes_trader.data.evidence_contract import (
    BEIJING_TIMEZONE,
    SCHEMA_VERSION,
    EvidenceContract,
    build_evidence_contract,
    compute_content_hash,
    evidence_contract_from_mapping,
    verify_content_hash,
)


def _complete(**overrides) -> EvidenceContract:
    values = {
        "content": {"items": [{"code": "000001", "title": "公告"}], "count": 1},
        "provider_family": "cninfo",
        "api_product": "announcement_list",
        "data_as_of": "2026-07-13T09:30:00+08:00",
        "fetched_at": "2026-07-13T01:30:02.125Z",
        "parser_version": "cninfo-announcement-v1",
        "source_url": "https://www.cninfo.com.cn/new/disclosure?api_key=secret&column=szse",
        "evidence_level": "tier_1",
        "data_state": "complete",
    }
    values.update(overrides)
    return build_evidence_contract(**values)


def test_contract_makes_beijing_event_time_and_utc_fetch_time_explicit() -> None:
    contract = _complete()
    payload = contract.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["data_as_of"] == "2026-07-13T09:30:00.000000+08:00"
    assert payload["data_as_of_timezone"] == BEIJING_TIMEZONE
    assert payload["data_as_of_precision"] == "timestamp"
    assert payload["fetched_at"] == "2026-07-13T01:30:02.125000Z"
    assert payload["fetched_at_timezone"] == "UTC"
    assert payload["source_url"] == (
        "https://www.cninfo.com.cn/new/disclosure?api_key=%5BREDACTED%5D&column=szse"
    )
    assert payload["degraded_reason"] is None
    assert payload["content_present"] is True
    assert len(payload["content_hash"]) == 64


def test_date_only_as_of_remains_a_date_instead_of_fake_midnight() -> None:
    contract = _complete(
        data_as_of=date(2026, 7, 13),
        fetched_at=datetime(2026, 7, 13, 2, tzinfo=timezone.utc),
    )

    assert contract.data_as_of == "2026-07-13"
    assert contract.data_as_of_precision == "date"


def test_content_hash_and_serialization_are_stable_and_recomputable() -> None:
    first_content = {"codes": {"000002", "000001"}, "meta": {"b": 2, "a": 1}}
    second_content = {"meta": {"a": 1, "b": 2}, "codes": {"000001", "000002"}}
    first = _complete(content=first_content)
    second = _complete(content=second_content)

    assert first.content_hash == second.content_hash == compute_content_hash(second_content)
    assert first.verify_content(second_content) is True
    assert verify_content_hash(second_content, first.content_hash) is True
    assert first.verify_content({"meta": {"a": 1, "b": 999}, "codes": set()}) is False
    assert first.to_json() == evidence_contract_from_mapping(first.to_dict()).to_json()
    assert json.loads(first.to_json())["content_hash"] == first.content_hash


@pytest.mark.parametrize("state", ["degraded", "missing"])
def test_degraded_and_missing_states_require_an_explicit_reason(state: str) -> None:
    with pytest.raises(ValueError, match="requires degraded_reason"):
        _complete(
            content=None if state == "missing" else {"partial": True},
            data_state=state,
            degraded_reason=None,
        )


def test_missing_evidence_cannot_claim_content_or_hash() -> None:
    with pytest.raises(ValueError, match="requires content=None"):
        _complete(content=[], data_state="missing", degraded_reason="upstream unavailable")

    missing = _complete(
        content=None,
        data_as_of=None,
        data_state="missing",
        degraded_reason="upstream unavailable",
    )
    assert missing.content_present is False
    assert missing.content_hash is None
    assert missing.verify_content(None) is True


def test_complete_state_cannot_hide_a_degradation_reason() -> None:
    with pytest.raises(ValueError, match="complete evidence cannot carry degraded_reason"):
        _complete(degraded_reason="fallback parser used")


def test_degraded_content_remains_hashable_but_is_not_labeled_complete() -> None:
    contract = _complete(
        content=[],
        data_state="degraded",
        degraded_reason="one exchange timed out",
        evidence_level="tier_2",
    )

    assert contract.data_state == "degraded"
    assert contract.degraded_reason == "one exchange timed out"
    assert contract.content_hash == compute_content_hash([])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("data_as_of", "2026-07-13T09:30:00"),
        ("fetched_at", "2026-07-13T01:30:02"),
    ],
)
def test_naive_timestamps_are_rejected_as_ambiguous(field: str, value: str) -> None:
    with pytest.raises(ValueError, match="explicit timezone"):
        _complete(**{field: value})


def test_future_data_as_of_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be later"):
        _complete(
            data_as_of="2026-07-13T09:31:00+08:00",
            fetched_at="2026-07-13T01:30:59Z",
        )


@pytest.mark.parametrize("level", ["official", "", "tier_4"])
def test_evidence_level_uses_the_three_documented_tiers(level: str) -> None:
    with pytest.raises(ValueError, match="evidence_level"):
        _complete(evidence_level=level)


def test_serialized_contract_is_revalidated_on_read() -> None:
    record = _complete().to_dict()
    record["data_state"] = "missing"
    record["degraded_reason"] = "upstream unavailable"

    with pytest.raises(ValueError, match="cannot claim content"):
        evidence_contract_from_mapping(record)


def test_mapping_read_normalizes_equivalent_timestamp_encodings() -> None:
    contract = _complete()
    record = contract.to_dict()
    record["data_as_of"] = "2026-07-13T01:30:00Z"
    record["fetched_at"] = "2026-07-13T01:30:02.125000+00:00"

    restored = evidence_contract_from_mapping(record)

    assert restored.to_json() == contract.to_json()


def test_mapping_read_rejects_a_falsified_time_precision() -> None:
    record = _complete().to_dict()
    record["data_as_of_precision"] = "date"

    with pytest.raises(ValueError, match="does not match"):
        evidence_contract_from_mapping(record)
