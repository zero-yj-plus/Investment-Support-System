from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
import asyncio

import pytest

from hermes_trader.data import official_disclosure as disclosure
from hermes_trader.api import routes


CNINFO_ITEM = {
    "secCode": "600000",
    "secName": "浦发银行",
    "orgId": "gssh0600000",
    "announcementId": "1225406051",
    "announcementTitle": "2025年年度权益分派实施公告",
    "announcementTime": 1783008000000,
    "adjunctUrl": "finalpage/2026-07-03/1225406051.PDF",
}

SSE_ITEM = {
    "SECURITY_CODE": "600000",
    "SECURITY_NAME": "浦发银行",
    "SSEDATE": "2026-07-03",
    "TITLE": "2025年年度权益分派实施公告",
    "URL": "/disclosure/listedinfo/announcement/c/new/2026-07-03/600000.pdf",
}


def test_cninfo_parser_preserves_date_precision_and_original_pdf() -> None:
    items = disclosure.parse_cninfo({"announcements": [CNINFO_ITEM]})

    assert items == [{
        "announcement_id": "1225406051",
        "code": "600000",
        "name": "浦发银行",
        "title": "2025年年度权益分派实施公告",
        "announcement_date": "2026-07-03",
        "published_at": "2026-07-03",
        "published_at_precision": "date",
        "original_url": "https://static.cninfo.com.cn/finalpage/2026-07-03/1225406051.PDF",
        "provider_family": "cninfo",
        "api_product": "hisAnnouncement",
        "evidence_level": "tier_1",
        "pdf_sha256": None,
        "pdf_size_bytes": None,
        "pdf_hash_state": "not_requested",
    }]


def test_sse_parser_accepts_page_help_and_does_not_invent_publish_time() -> None:
    items = disclosure.parse_sse({"pageHelp": {"data": [SSE_ITEM]}})

    assert items[0]["announcement_date"] == "2026-07-03"
    assert items[0]["published_at"] == "2026-07-03"
    assert items[0]["published_at_precision"] == "date"
    assert items[0]["original_url"] == (
        "https://static.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-07-03/600000.pdf"
    )


def test_szse_parser_keeps_real_timestamp_and_download_path() -> None:
    payload = {"data": [{
        "id": "sz-1", "secCode": "000001", "secName": "平安银行",
        "title": "董事会决议公告", "publishTime": "2026-07-13 18:02:53",
        "attachPath": "/disc/disk03/finalpage/2026-07-13/a.pdf",
    }]}

    items = disclosure.parse_szse(payload)

    assert items[0]["published_at"] == "2026-07-13 18:02:53"
    assert items[0]["published_at_precision"] == "timestamp"
    assert items[0]["original_url"] == (
        "https://disc.static.szse.cn/disc/disk03/finalpage/2026-07-13/a.pdf"
    )


def test_szse_parser_handles_live_array_fields_and_midnight_date_placeholder() -> None:
    items = disclosure.parse_szse({"announceCount": 1, "data": [{
        "annId": 1225406051,
        "secCode": ["000001"],
        "secName": ["平安银行"],
        "title": "平安银行：董事会决议公告",
        "publishTime": "2026-07-03 00:00:00",
        "attachPath": "/disc/disk03/finalpage/2026-07-03/a.PDF",
    }]})

    assert items[0]["announcement_id"] == "1225406051"
    assert items[0]["code"] == "000001"
    assert items[0]["name"] == "平安银行"
    assert items[0]["published_at"] == "2026-07-03"
    assert items[0]["published_at_precision"] == "date"


class FakeTransport:
    def __init__(self, *, cninfo=True, exchange=True, pdf: bytes | None = None):
        self.cninfo = cninfo
        self.exchange = exchange
        self.pdf = pdf
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs) -> bytes:
        self.calls.append((url, kwargs))
        if url == disclosure.CNINFO_STOCKS_URL:
            if not self.cninfo:
                raise disclosure.DisclosureFetchError("cninfo catalogue unavailable")
            return json.dumps({"stockList": [{
                "code": "600000", "orgId": "gssh0600000", "zwjc": "浦发银行",
            }]}).encode()
        if url == disclosure.CNINFO_QUERY_URL:
            if not self.cninfo:
                raise disclosure.DisclosureFetchError("cninfo query unavailable")
            return json.dumps({"announcements": [CNINFO_ITEM]}).encode()
        if url == disclosure.SSE_QUERY_URL:
            if not self.exchange:
                raise disclosure.DisclosureFetchError("sse query unavailable")
            return json.dumps({"result": [SSE_ITEM]}).encode()
        if url.startswith("https://static."):
            if self.pdf is None:
                raise disclosure.DisclosureFetchError("pdf unavailable")
            return self.pdf
        raise AssertionError(url)


def _fetch(transport: FakeTransport, **kwargs):
    return disclosure.fetch_official_disclosures(
        "600000",
        start_date="2026-07-01",
        end_date="2026-07-14",
        transport=transport,
        now_fn=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
        **kwargs,
    )


def test_two_official_paths_cross_confirm_and_build_tier_one_contract() -> None:
    result = _fetch(FakeTransport())

    assert result["ok"] is True
    assert result["state"] == "complete"
    assert result["verification_state"] == "cross_confirmed"
    assert result["items"][0]["source_confirmations"] == ["cninfo", "sse"]
    assert result["evidence"]["evidence_level"] == "tier_1"
    assert result["evidence"]["data_as_of"] == "2026-07-14"
    assert result["evidence"]["content_hash"]


def test_one_live_source_is_degraded_not_complete_or_empty() -> None:
    result = _fetch(FakeTransport(exchange=False))

    assert result["ok"] is True
    assert result["state"] == "degraded"
    assert result["verification_state"] == "single_source"
    assert result["items"]
    assert "sse:" in result["evidence"]["degraded_reason"]


def test_all_failed_sources_are_missing_not_a_successful_empty_list() -> None:
    result = _fetch(FakeTransport(cninfo=False, exchange=False))

    assert result["ok"] is False
    assert result["state"] == "missing"
    assert result["items"] == []
    assert result["evidence"]["content_present"] is False
    assert result["evidence"]["content_hash"] is None


def test_verified_pdf_hash_is_in_normalized_content_and_contract() -> None:
    raw_pdf = b"%PDF-1.7\ntruthful disclosure"
    result = _fetch(FakeTransport(pdf=raw_pdf), verify_pdf=True, pdf_hash_limit=1)

    item = result["items"][0]
    assert item["pdf_hash_state"] == "verified"
    assert item["pdf_sha256"] == hashlib.sha256(raw_pdf).hexdigest()
    assert item["pdf_size_bytes"] == len(raw_pdf)
    assert result["state"] == "complete"


def test_failed_pdf_verification_degrades_the_result_without_hiding_announcements() -> None:
    result = _fetch(FakeTransport(), verify_pdf=True, pdf_hash_limit=1)

    assert result["state"] == "degraded"
    assert result["items"][0]["pdf_hash_state"] == "unavailable"
    assert "PDF" in result["evidence"]["degraded_reason"]


def test_last_good_is_used_only_as_an_explicit_degradation(tmp_path) -> None:
    path = tmp_path / "600000.json"
    first = _fetch(FakeTransport(), last_good_path=path)
    assert first["used_last_good"] is False
    assert path.exists()

    fallback = _fetch(
        FakeTransport(cninfo=False, exchange=False),
        last_good_path=path,
    )

    assert fallback["ok"] is True
    assert fallback["state"] == "degraded"
    assert fallback["verification_state"] == "last_good"
    assert fallback["used_last_good"] is True
    assert "上次成功快照" in fallback["evidence"]["degraded_reason"]


@pytest.mark.parametrize("code", ["abc", "920001", "700001"])
def test_unsupported_or_invalid_code_is_rejected(code: str) -> None:
    with pytest.raises(ValueError, match="supports SSE/SZSE"):
        disclosure.fetch_official_disclosures(
            code, start_date="2026-07-01", end_date="2026-07-14",
        )


def test_disclosure_api_passes_customer_scoped_last_good_path(monkeypatch, tmp_path) -> None:
    seen = {}

    def fake(code, start, end, limit, verify_pdf, pdf_hash_limit, last_good_path):
        seen.update(locals())
        return {"ok": True, "state": "complete", "items": []}

    monkeypatch.setattr(disclosure, "cached_official_disclosures", fake)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
    )))

    result = asyncio.run(routes.disclosure_announcements(
        request, code="600000", start_date="2026-07-01",
        end_date="2026-07-14", limit=99, verify_pdf=True,
    ))

    assert result == {"ok": True, "state": "complete", "items": []}
    assert seen["code"] == "600000"
    assert seen["limit"] == 50
    assert seen["verify_pdf"] is True
    assert seen["pdf_hash_limit"] == 1
    assert seen["last_good_path"] == str(
        tmp_path / "official-disclosures" / "600000.json"
    )


def test_malformed_nonempty_rows_cannot_be_reported_as_confirmed_empty() -> None:
    def malformed(url: str, **_kwargs) -> bytes:
        if url == disclosure.CNINFO_STOCKS_URL:
            return json.dumps({"stockList": [{
                "code": "600000", "orgId": "gssh0600000",
            }]}).encode()
        if url == disclosure.CNINFO_QUERY_URL:
            return b'{"announcements":[{"unexpected":"field-drift"}]}'
        if url == disclosure.SSE_QUERY_URL:
            return b'{"result":[{"unexpected":"field-drift"}]}'
        raise AssertionError(url)

    result = disclosure.fetch_official_disclosures(
        "600000",
        start_date="2026-07-01",
        end_date="2026-07-14",
        transport=malformed,
        now_fn=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert result["state"] == "missing"
    assert result["verification_state"] != "empty_confirmed"
    assert all(source["schema_valid"] is False for source in result["sources"])


def test_partial_cross_confirmation_degrades_the_aggregate() -> None:
    cninfo_unique = {
        **CNINFO_ITEM,
        "announcementId": "cn-only",
        "announcementTitle": "巨潮单源公告",
    }
    sse_unique = {
        **SSE_ITEM,
        "OPERATION_SEQ": "sse-only",
        "TITLE": "交易所单源公告",
    }

    def partial(url: str, **_kwargs) -> bytes:
        if url == disclosure.CNINFO_STOCKS_URL:
            return json.dumps({"stockList": [{
                "code": "600000", "orgId": "gssh0600000",
            }]}).encode()
        if url == disclosure.CNINFO_QUERY_URL:
            return json.dumps({"announcements": [CNINFO_ITEM, cninfo_unique]}).encode()
        if url == disclosure.SSE_QUERY_URL:
            return json.dumps({"result": [SSE_ITEM, sse_unique]}).encode()
        raise AssertionError(url)

    result = disclosure.fetch_official_disclosures(
        "600000",
        start_date="2026-07-01",
        end_date="2026-07-14",
        transport=partial,
        now_fn=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert result["state"] == "degraded"
    assert result["verification_state"] == "partially_cross_confirmed"
    assert sum(len(item["source_confirmations"]) == 2 for item in result["items"]) == 1
    assert "1/3" in result["evidence"]["degraded_reason"]


def test_future_query_window_is_rejected_before_network_access() -> None:
    with pytest.raises(ValueError, match="current Beijing date"):
        disclosure.fetch_official_disclosures(
            "600000",
            start_date="2099-01-01",
            end_date="2099-01-02",
            transport=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("network must not be called")
            ),
            now_fn=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
        )


def test_last_good_is_bound_to_exact_query_window(tmp_path) -> None:
    path = tmp_path / "600000.json"
    first = _fetch(FakeTransport(), last_good_path=path, limit=6)
    assert first["state"] == "complete"

    mismatch = disclosure.fetch_official_disclosures(
        "600000",
        start_date="2026-05-01",
        end_date="2026-07-14",
        limit=12,
        last_good_path=path,
        transport=FakeTransport(cninfo=False, exchange=False),
        now_fn=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert mismatch["state"] == "missing"
    assert mismatch["used_last_good"] is False


def test_last_good_keeps_original_fetch_time_and_exposes_replay_time(tmp_path) -> None:
    path = tmp_path / "600000.json"
    first_time = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    replay_time = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)
    first = disclosure.fetch_official_disclosures(
        "600000",
        start_date="2026-07-01",
        end_date="2026-07-14",
        last_good_path=path,
        transport=FakeTransport(),
        now_fn=lambda: first_time,
    )
    replay = disclosure.fetch_official_disclosures(
        "600000",
        start_date="2026-07-01",
        end_date="2026-07-14",
        last_good_path=path,
        transport=FakeTransport(cninfo=False, exchange=False),
        now_fn=lambda: replay_time,
    )

    assert replay["state"] == "degraded"
    assert replay["source_fetched_at"] == first["source_fetched_at"]
    assert replay["replayed_at"].startswith("2026-07-15T02:00:00")
    assert replay["snapshot_age_seconds"] == 7200


def test_last_good_older_than_four_days_is_not_replayed(tmp_path) -> None:
    path = tmp_path / "600000.json"
    disclosure.fetch_official_disclosures(
        "600000",
        start_date="2026-07-01",
        end_date="2026-07-14",
        last_good_path=path,
        transport=FakeTransport(),
        now_fn=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    stale = disclosure.fetch_official_disclosures(
        "600000",
        start_date="2026-07-01",
        end_date="2026-07-14",
        last_good_path=path,
        transport=FakeTransport(cninfo=False, exchange=False),
        now_fn=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    assert stale["state"] == "missing"
    assert stale["used_last_good"] is False


def test_untrusted_absolute_pdf_urls_are_removed_during_normalization() -> None:
    item = {**CNINFO_ITEM, "adjunctUrl": "https://evil.example/fake.pdf"}

    assert disclosure.parse_cninfo({"announcements": [item]})[0]["original_url"] is None
