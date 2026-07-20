from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from hermes_trader.data import cls_news_source as cls


NOW = datetime(2026, 7, 13, 18, 30, tzinfo=timezone.utc)


def _payload() -> dict:
    return {
        "errno": 0,
        "data": {
            "roll_data": [{
                "id": 123,
                "ctime": 1783964430,
                "title": "",
                "brief": "WTI原油期货持续上涨",
                "content": "<b>WTI原油期货</b>持续上涨超7%",
                "shareurl": "https://api3.cls.cn/share/article/123?os=web",
            }],
        },
    }


def test_cls_sign_is_stable_and_has_no_secret_material() -> None:
    params = cls.signed_params(999)

    assert params["rn"] == "100"
    assert params["sign"] == cls.signed_params(100)["sign"]
    assert len(params["sign"]) == 32
    assert set(params) == {"appName", "os", "sv", "last_time", "refresh_type", "rn", "sign"}


def test_parse_cls_preserves_publisher_time_link_and_hash() -> None:
    rows = cls.parse_cls(_payload())

    assert rows[0]["publisher_name"] == "财联社"
    assert rows[0]["source_relation"] == "original_publisher"
    assert rows[0]["evidence_level"] == "tier_2"
    assert rows[0]["published_at"].endswith("+08:00")
    assert rows[0]["source_url"].startswith("https://api3.cls.cn/")
    assert len(rows[0]["content_hash"]) == 64
    assert "<b>" not in rows[0]["content"]


def test_parse_cls_rejects_field_drift_instead_of_claiming_empty() -> None:
    try:
        cls.parse_cls({"errno": 0, "data": {}})
    except cls.ClsNewsFetchError as exc:
        assert "roll_data" in str(exc)
    else:
        raise AssertionError("field drift must raise")


def test_nonempty_rows_that_all_fail_schema_are_not_confirmed_empty() -> None:
    payload = {"errno": 0, "data": {"roll_data": [{"id": 1, "ctime": None}]}}

    result = cls.fetch_cls_telegraph(
        transport=lambda *_args, **_kwargs: json.dumps(payload).encode(),
        now_fn=lambda: NOW,
    )

    assert result["state"] == "missing"
    assert result["result_state"] == "unavailable"


def test_partial_schema_is_degraded_and_does_not_replace_last_good(tmp_path) -> None:
    path = tmp_path / "cls" / "last-good.json"
    cls.fetch_cls_telegraph(
        limit=50,
        transport=lambda *_args, **_kwargs: json.dumps(_payload()).encode(),
        last_good_path=path,
        now_fn=lambda: NOW,
    )
    partial = _payload()
    partial["data"]["roll_data"].append({"id": 2, "ctime": None})

    result = cls.fetch_cls_telegraph(
        limit=50,
        transport=lambda *_args, **_kwargs: json.dumps(partial).encode(),
        last_good_path=path,
        now_fn=lambda: NOW + timedelta(minutes=1),
    )

    assert result["state"] == "degraded"
    assert result["result_state"] == "partial_schema"
    saved = json.loads(path.read_text())
    assert len(saved["items"]) == 1


def test_fetch_cls_builds_complete_evidence_contract() -> None:
    seen = {}

    def transport(url, **kwargs):
        seen.update({"url": url, **kwargs})
        return json.dumps(_payload()).encode()

    result = cls.fetch_cls_telegraph(limit=3, transport=transport, now_fn=lambda: NOW)

    assert result["ok"] is True
    assert result["state"] == "complete"
    assert result["result_state"] == "complete"
    assert result["evidence"]["schema_version"] == "data-evidence-v1"
    assert result["evidence"]["provider_family"] == "cls"
    assert seen["url"] == cls.CLS_ROLL_URL
    assert seen["params"]["sign"]


def test_successful_empty_response_is_distinct_from_failure() -> None:
    def transport(*_args, **_kwargs):
        return b'{"errno":0,"data":{"roll_data":[]}}'

    result = cls.fetch_cls_telegraph(transport=transport, now_fn=lambda: NOW)

    assert result["ok"] is True
    assert result["state"] == "complete"
    assert result["result_state"] == "complete_empty"
    assert result["items"] == []
    assert result["evidence"]["content_hash"]


def test_failure_without_last_good_is_missing() -> None:
    def transport(*_args, **_kwargs):
        raise TimeoutError("secret upstream detail")

    result = cls.fetch_cls_telegraph(transport=transport, now_fn=lambda: NOW)

    assert result["ok"] is False
    assert result["state"] == "missing"
    assert result["result_state"] == "unavailable"
    assert result["items"] == []
    assert result["evidence"]["content_hash"] is None
    assert "secret upstream detail" not in repr(result)


def test_failure_uses_last_good_as_degraded_snapshot(tmp_path) -> None:
    path = tmp_path / "cls" / "last-good.json"

    def success(*_args, **_kwargs):
        return json.dumps(_payload()).encode()

    first = cls.fetch_cls_telegraph(
        transport=success, last_good_path=path, now_fn=lambda: NOW,
    )

    def failure(*_args, **_kwargs):
        raise TimeoutError("offline")

    second = cls.fetch_cls_telegraph(
        transport=failure, last_good_path=path, now_fn=lambda: NOW,
    )

    assert first["state"] == "complete"
    assert second["state"] == "degraded"
    assert second["result_state"] == "last_good"
    assert second["used_last_good"] is True
    assert second["items"] == first["items"]
    assert second["source_fetched_at"] == first["fetched_at"]
    assert second["replayed_at"] == second["fetched_at"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_last_good_is_bound_to_limit_and_max_age(tmp_path) -> None:
    path = tmp_path / "cls" / "last-good.json"

    def success(*_args, **_kwargs):
        return json.dumps(_payload()).encode()

    def failure(*_args, **_kwargs):
        raise TimeoutError("offline")

    cls.fetch_cls_telegraph(
        limit=3, transport=success, last_good_path=path, now_fn=lambda: NOW,
    )

    wrong_query = cls.fetch_cls_telegraph(
        limit=4, transport=failure, last_good_path=path,
        now_fn=lambda: NOW + timedelta(minutes=1),
    )
    stale = cls.fetch_cls_telegraph(
        limit=3, transport=failure, last_good_path=path,
        now_fn=lambda: NOW + timedelta(hours=7),
    )

    assert wrong_query["state"] == "missing"
    assert stale["state"] == "missing"


def test_untrusted_story_url_is_not_exposed() -> None:
    payload = _payload()
    payload["data"]["roll_data"][0]["shareurl"] = "https://evil.example/story"

    assert cls.parse_cls(payload)[0]["source_url"] is None
