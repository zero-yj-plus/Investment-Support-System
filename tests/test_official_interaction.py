from __future__ import annotations

from datetime import datetime, timezone
import json
import stat
from types import SimpleNamespace
import urllib.parse

import pytest

from hermes_trader.data import official_interaction as interaction
from hermes_trader.api import routes


NOW = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
QUESTION_MS = int(datetime(2026, 7, 13, 2, 15, tzinfo=timezone.utc).timestamp() * 1000)
ANSWER_MS = int(datetime(2026, 7, 14, 3, 45, tzinfo=timezone.utc).timestamp() * 1000)


def _keyword_payload(*rows: dict) -> dict:
    return {"data": list(rows) or [{
        "stockcode": "000001",
        "secid": "gssz0000001",
        "secname": "平安银行",
    }]}


def _question_row(**overrides) -> dict:
    row = {
        "questionId": "q-1",
        "stockCode": "000001",
        "mainContent": "请问公司新业务进展如何？",
        "attachedContent": "公司正在按计划推进，感谢关注。",
        "pubDate": QUESTION_MS,
        "attachedPubDate": ANSWER_MS,
    }
    row.update(overrides)
    return row


class FakeWebsite:
    def __init__(self, keyword: dict | None = None, questions: dict | None = None):
        self.keyword = keyword if keyword is not None else _keyword_payload()
        self.questions = questions if questions is not None else {
            "total": 1,
            "rows": [_question_row()],
        }
        self.calls: list[tuple[str, dict]] = []
        self.fail_url: str | None = None

    def __call__(self, url: str, **kwargs) -> bytes:
        self.calls.append((url, kwargs))
        if url == self.fail_url:
            raise interaction.InteractionFetchError("upstream unavailable")
        if url == interaction.KEYWORD_URL:
            return json.dumps(self.keyword, ensure_ascii=False).encode()
        if url == interaction.QUESTION_URL:
            return json.dumps(self.questions, ensure_ascii=False).encode()
        raise AssertionError(f"unexpected URL: {url}")


def _install(monkeypatch: pytest.MonkeyPatch, website: FakeWebsite) -> None:
    # Every fetch test patches the production transport.  No test in this file
    # is allowed to make a real network request.
    monkeypatch.setattr(interaction, "_request_bytes", website)


def _fetch(**kwargs):
    return interaction.fetch_official_interactions(
        "000001",
        start_date="2026-07-01",
        end_date="2026-07-15",
        now_fn=lambda: NOW,
        **kwargs,
    )


def test_request_shape_matches_the_two_website_internal_endpoints(monkeypatch) -> None:
    website = FakeWebsite()
    _install(monkeypatch, website)

    result = _fetch(limit=7)

    assert result["state"] == "complete"
    assert len(website.calls) == 2
    keyword_url, keyword_call = website.calls[0]
    assert keyword_url == interaction.KEYWORD_URL
    assert keyword_call["method"] == "POST"
    assert keyword_call["form"] == {"keyWord": "000001"}
    assert keyword_call["params"] is None

    question_url, question_call = website.calls[1]
    assert question_url == interaction.QUESTION_URL
    assert question_call["method"] == "POST"
    assert question_call["form"] is None
    assert question_call["params"] == {
        "_t": "1",
        "stockcode": "000001",
        "orgId": "gssz0000001",
        "pageSize": "7",
        "pageNum": "1",
        "keyWord": "",
        "startDay": "2026-07-01",
        "endDay": "2026-07-15",
    }


def test_management_reply_has_separate_question_and_answer_times(monkeypatch) -> None:
    website = FakeWebsite()
    _install(monkeypatch, website)

    result = _fetch()
    item = result["items"][0]

    assert item["question_pub_date"] == "2026-07-13T10:15:00+08:00"
    assert item["answer_pub_date"] == "2026-07-14T11:45:00+08:00"
    assert item["answered"] is True
    assert item["answer_status"] == "answered"
    assert item["source_type"] == "交易所互动问答"
    assert item["answer_source_type"] == "管理层回复"
    assert item["is_formal_disclosure"] is False
    assert result["is_formal_disclosure"] is False
    assert result["interface_type"] == "website_internal_endpoint"
    assert result["evidence"]["evidence_level"] == "tier_3"
    assert result["evidence"]["data_state"] == "complete"
    assert result["evidence"]["content_hash"]
    assert result["replayed_at"] is None


def test_unanswered_question_is_not_mislabeled_as_a_management_reply(monkeypatch) -> None:
    website = FakeWebsite(questions={
        "total": 1,
        "rows": [_question_row(
            attachedContent=None,
            attachedPubDate=None,
        )],
    })
    _install(monkeypatch, website)

    result = _fetch()
    item = result["items"][0]

    assert result["state"] == "complete"
    assert item["answered"] is False
    assert item["answer"] is None
    assert item["answer_pub_date"] is None
    assert item["answer_source_type"] is None


def test_reply_without_reply_time_is_kept_but_degraded(monkeypatch) -> None:
    website = FakeWebsite(questions={
        "total": 1,
        "rows": [_question_row(attachedPubDate=None)],
    })
    _install(monkeypatch, website)

    result = _fetch()

    assert result["state"] == "degraded"
    assert result["items"][0]["answered"] is True
    assert result["items"][0]["answer_pub_date"] is None
    assert result["evidence"]["data_state"] == "degraded"
    assert "回复时间" in result["evidence"]["degraded_reason"]


def test_reply_time_before_question_time_degrades_chronology(monkeypatch) -> None:
    website = FakeWebsite(questions={
        "total": 1,
        "rows": [_question_row(attachedPubDate=QUESTION_MS - 60_000)],
    })
    _install(monkeypatch, website)

    result = _fetch()

    assert result["state"] == "degraded"
    assert result["items"][0]["answered"] is True
    assert "早于提问时间" in result["evidence"]["degraded_reason"]


def test_exact_company_code_is_selected_instead_of_first_suggestion(monkeypatch) -> None:
    website = FakeWebsite(keyword=_keyword_payload(
        {"stockcode": "000002", "secid": "gssz0000002", "secname": "万科A"},
        {"stockcode": "000001", "secid": "gssz0000001", "secname": "平安银行"},
    ))
    _install(monkeypatch, website)

    result = _fetch()

    assert result["state"] == "complete"
    assert result["company"]["org_id"] == "gssz0000001"
    assert website.calls[1][1]["params"]["orgId"] == "gssz0000001"


@pytest.mark.parametrize("row_code", ["000002", "not-a-code"])
def test_mismatched_nonempty_rows_are_degraded_not_complete_empty(
    monkeypatch,
    row_code,
) -> None:
    website = FakeWebsite(questions={
        "total": 1,
        "rows": [_question_row(stockCode=row_code)],
    })
    _install(monkeypatch, website)

    result = _fetch()

    assert result["state"] == "degraded"
    assert result["items"] == []
    assert result["raw_count"] == 1
    assert result["evidence"]["content_present"] is True
    assert "代码不匹配" in result["evidence"]["degraded_reason"]


def test_zero_rows_requires_explicit_zero_total_to_be_complete_empty(monkeypatch) -> None:
    website = FakeWebsite(questions={"total": 0, "rows": []})
    _install(monkeypatch, website)

    result = _fetch()

    assert result["state"] == "complete_empty"
    assert result["ok"] is True
    assert result["items"] == []
    assert result["evidence"]["data_state"] == "complete"
    assert result["evidence"]["content_present"] is True
    assert result["data_as_of"] == "2026-07-15"


def test_ambiguous_empty_payload_is_degraded(monkeypatch) -> None:
    website = FakeWebsite(questions={"rows": []})
    _install(monkeypatch, website)

    result = _fetch()

    assert result["state"] == "degraded"
    assert result["state"] != "complete_empty"
    assert "total" in result["evidence"]["degraded_reason"]


def test_source_failure_is_missing_and_not_a_successful_empty_list(monkeypatch) -> None:
    website = FakeWebsite()
    website.fail_url = interaction.QUESTION_URL
    _install(monkeypatch, website)

    result = _fetch()

    assert result["state"] == "missing"
    assert result["ok"] is False
    assert result["items"] == []
    assert result["evidence"]["content_present"] is False
    assert result["evidence"]["content_hash"] is None


def test_last_good_replay_is_an_explicit_fifth_state(monkeypatch, tmp_path) -> None:
    path = tmp_path / "customer-a" / "interaction.json"
    website = FakeWebsite()
    _install(monkeypatch, website)
    first = _fetch(last_good_path=path)

    assert first["state"] == "complete"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    failed = FakeWebsite()
    failed.fail_url = interaction.KEYWORD_URL
    _install(monkeypatch, failed)
    replay = interaction.fetch_official_interactions(
        "000001",
        start_date="2026-07-01",
        end_date="2026-07-15",
        last_good_path=path,
        now_fn=lambda: datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc),
    )

    assert replay["state"] == "last_good"
    assert replay["used_last_good"] is True
    assert replay["source_fetched_at"] == first["source_fetched_at"]
    assert replay["replayed_at"].startswith("2026-07-15T02:00:00")
    assert replay["snapshot_age_seconds"] == 7200
    assert replay["evidence"]["data_state"] == "degraded"
    assert "上次成功快照" in replay["evidence"]["degraded_reason"]


def test_last_good_is_bound_to_the_exact_query_contract(monkeypatch, tmp_path) -> None:
    path = tmp_path / "interaction.json"
    website = FakeWebsite()
    _install(monkeypatch, website)
    _fetch(last_good_path=path, limit=5)

    failed = FakeWebsite()
    failed.fail_url = interaction.KEYWORD_URL
    _install(monkeypatch, failed)
    mismatch = interaction.fetch_official_interactions(
        "000001",
        start_date="2026-06-01",
        end_date="2026-07-15",
        limit=10,
        last_good_path=path,
        now_fn=lambda: datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc),
    )

    assert mismatch["state"] == "missing"
    assert mismatch["used_last_good"] is False


def test_stale_last_good_is_not_replayed(monkeypatch, tmp_path) -> None:
    path = tmp_path / "interaction.json"
    website = FakeWebsite()
    _install(monkeypatch, website)
    _fetch(last_good_path=path)

    failed = FakeWebsite()
    failed.fail_url = interaction.KEYWORD_URL
    _install(monkeypatch, failed)
    stale = interaction.fetch_official_interactions(
        "000001",
        start_date="2026-07-01",
        end_date="2026-07-15",
        last_good_path=path,
        now_fn=lambda: datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    assert stale["state"] == "missing"
    assert stale["used_last_good"] is False


def test_modified_last_good_content_is_not_replayed(monkeypatch, tmp_path) -> None:
    path = tmp_path / "interaction.json"
    website = FakeWebsite()
    _install(monkeypatch, website)
    _fetch(last_good_path=path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["items"][0]["answer"] = "被篡改的回复"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    failed = FakeWebsite()
    failed.fail_url = interaction.KEYWORD_URL
    _install(monkeypatch, failed)
    result = interaction.fetch_official_interactions(
        "000001",
        start_date="2026-07-01",
        end_date="2026-07-15",
        last_good_path=path,
        now_fn=lambda: datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc),
    )

    assert result["state"] == "missing"
    assert result["used_last_good"] is False


def test_customer_cache_path_initializes_private_scoped_directory(tmp_path) -> None:
    query = {
        "code": "000001",
        "start_date": "2026-07-01",
        "end_date": "2026-07-15",
        "limit": 20,
        "page_num": 1,
    }

    path = interaction.customer_cache_path(tmp_path / "customer-a", query)

    assert path.parent == tmp_path / "customer-a" / "official-interactions"
    assert path.name.startswith("000001-")
    assert path.suffix == ".json"
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_allowlist_rejects_non_https_and_non_official_hosts(monkeypatch) -> None:
    def no_open(*_args, **_kwargs):
        raise AssertionError("network must not be reached")

    monkeypatch.setattr(interaction.urllib.request, "build_opener", no_open)

    for url in (
        "http://irm.cninfo.com.cn/newircs/company/question",
        "https://evil.example/newircs/company/question",
        "https://irm.cninfo.com.cn.evil.example/newircs/company/question",
    ):
        with pytest.raises(interaction.InteractionFetchError, match="allowlisted"):
            interaction._request_bytes(
                url,
                label="test",
                timeout=1,
                referer=interaction.REFERER_URL,
            )


def test_real_transport_builds_query_string_post_with_an_empty_body(monkeypatch) -> None:
    captured = {}

    class Response:
        headers = {}

        def __init__(self):
            self.read_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return interaction.QUESTION_URL

        def read(self, _size):
            self.read_count += 1
            return b"{}" if self.read_count == 1 else b""

    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(
        interaction.urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )

    raw = interaction._request_bytes(
        interaction.QUESTION_URL,
        label="test",
        timeout=2,
        referer=interaction.REFERER_URL,
        params={"stockcode": "000001", "orgId": "gssz0000001"},
        form=None,
    )

    request = captured["request"]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
    assert raw == b"{}"
    assert request.get_method() == "POST"
    assert request.data == b""
    assert query == {"stockcode": ["000001"], "orgId": ["gssz0000001"]}


def test_real_transport_rejects_declared_oversized_body_without_reading(monkeypatch) -> None:
    class Response:
        headers = {"Content-Length": "11"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return interaction.KEYWORD_URL

        def read(self, _size):
            raise AssertionError("oversized response must not be read")

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(
        interaction.urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )

    with pytest.raises(interaction.InteractionFetchError, match="too large"):
        interaction._request_bytes(
            interaction.KEYWORD_URL,
            label="test",
            timeout=2,
            referer=interaction.REFERER_URL,
            form={"keyWord": "000001"},
            max_bytes=10,
        )


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected_before_network(monkeypatch, timeout) -> None:
    website = FakeWebsite()
    _install(monkeypatch, website)

    with pytest.raises(ValueError, match="finite positive"):
        _fetch(timeout=timeout)
    assert website.calls == []


@pytest.mark.parametrize("code", ["600000", "920001", "abc"])
def test_non_shenzhen_or_invalid_code_is_rejected_before_network(
    monkeypatch,
    code,
) -> None:
    website = FakeWebsite()
    _install(monkeypatch, website)

    with pytest.raises(ValueError, match="Shenzhen"):
        interaction.fetch_official_interactions(code)
    assert website.calls == []


def test_future_window_is_rejected_before_network(monkeypatch) -> None:
    website = FakeWebsite()
    _install(monkeypatch, website)

    with pytest.raises(ValueError, match="current Beijing date"):
        interaction.fetch_official_interactions(
            "000001",
            start_date="2099-01-01",
            end_date="2099-01-02",
            now_fn=lambda: NOW,
        )
    assert website.calls == []


def test_cached_wrapper_singleflights_identical_company_query(monkeypatch, tmp_path) -> None:
    website = FakeWebsite()
    _install(monkeypatch, website)

    first = interaction.cached_official_interactions(
        "000001", "", "", 6, str(tmp_path)
    )
    second = interaction.cached_official_interactions(
        "000001", "", "", 6, str(tmp_path)
    )

    assert first == second
    assert len(website.calls) == 2


@pytest.mark.asyncio
async def test_management_interaction_api_uses_customer_cache_and_marks_unsupported(
    monkeypatch, tmp_path,
) -> None:
    seen = {}

    def fake_cached(code, start, end, limit, data_dir):
        seen.update(code=code, start=start, end=end, limit=limit, data_dir=data_dir)
        return {"ok": True, "state": "complete", "items": [{"answered": True}]}

    monkeypatch.setattr(interaction, "cached_official_interactions", fake_cached)
    monkeypatch.setattr(routes, "now_cn", lambda: datetime(2026, 7, 15, tzinfo=timezone.utc))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path)))
    )

    result = await routes.management_interactions(request, code="000001", limit=999)
    unsupported = await routes.management_interactions(request, code="600000")

    assert result["state"] == "complete"
    assert seen == {
        "code": "000001",
        "start": "2026-04-16",
        "end": "2026-07-15",
        "limit": 20,
        "data_dir": str(tmp_path),
    }
    assert unsupported["state"] == "not_supported"
    assert unsupported["ok"] is True
    assert unsupported["is_formal_disclosure"] is False
