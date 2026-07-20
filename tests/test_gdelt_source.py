from __future__ import annotations

import json

from hermes_trader.data import gdelt_source


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return json.dumps({
            "articles": [{
                "title": "Semiconductor supply chain update",
                "url": "https://example.com/story",
                "seendate": "20260718T120000Z",
                "domain": "example.com",
                "language": "English",
                "sourcecountry": "United States",
            }]
        }).encode()


def test_domestic_profile_does_not_wait_for_cross_border_enrichment(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_DEPLOYMENT_PROFILE", "cn_home")
    monkeypatch.delenv("HERMES_GLOBAL_EVENT_FEED", raising=False)
    monkeypatch.setattr(
        gdelt_source._OPENER, "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("国内默认不得请求跨境增强源")
        ),
    )

    result = gdelt_source.global_events.__wrapped__(10)

    assert result["state"] == "not_routed"
    assert result["items"] == []


def test_overseas_profile_collects_metadata_without_article_body(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_DEPLOYMENT_PROFILE", "overseas_server")
    monkeypatch.delenv("HERMES_GLOBAL_EVENT_FEED", raising=False)
    monkeypatch.setattr(gdelt_source._OPENER, "open", lambda *_args, **_kwargs: _Response())
    monkeypatch.setattr(gdelt_source, "_record_source", lambda *_args, **_kwargs: None)

    result = gdelt_source.global_events.__wrapped__(10)

    assert result["state"] == "complete"
    assert result["items"][0]["source_id"] == "gdelt_doc_2"
    assert result["items"][0]["url"] == "https://example.com/story"
    assert "content" not in result["items"][0]
    assert "正文" in result["license_note"]
