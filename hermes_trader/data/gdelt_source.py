"""Low-frequency global event headlines from the official GDELT DOC API.

Only metadata needed for evidence routing is retained: title, URL, publisher
domain and provider timestamp. Article bodies are never copied or cached.
"""
from __future__ import annotations

import json
import os
import time
from urllib.parse import urlencode
from urllib.request import Request

from .base import _record_source, direct_https_opener, ttl_cache
from .deployment import deployment_profile
from .evidence_contract import normalize_source_url

_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
_OPENER = direct_https_opener()
_DEFAULT_QUERY = (
    "(semiconductor OR \"artificial intelligence\" OR robotics OR "
    "\"electric vehicle\" OR energy OR sanctions)"
)


def _enabled() -> bool:
    configured = os.environ.get("HERMES_GLOBAL_EVENT_FEED", "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    # 海外服务器直连 GDELT；国内部署保留本地/授权快讯为主，避免让
    # 一个跨境增强源每轮超时。国内客户可显式开启。
    return deployment_profile() == "overseas_server"


@ttl_cache(300, fail_seconds=60)
def global_events(limit: int = 20, query: str = _DEFAULT_QUERY) -> dict:
    bounded = max(1, min(int(limit or 20), 50))
    if not _enabled():
        return {
            "ok": True,
            "state": "not_routed",
            "items": [],
            "source": "GDELT DOC 2.0",
            "note": "当前部署地域未启用跨境事件增强；可设 HERMES_GLOBAL_EVENT_FEED=1",
        }
    params = urlencode({
        "query": str(query or _DEFAULT_QUERY)[:500],
        "mode": "ArtList",
        "format": "json",
        "maxrecords": bounded,
        "sort": "DateDesc",
        "timespan": "12h",
    })
    started = time.monotonic()
    try:
        req = Request(
            f"{_ENDPOINT}?{params}",
            headers={"User-Agent": "WudiResearch/1.0 (metadata-only)"},
        )
        with _OPENER.open(req, timeout=3.5) as response:
            payload = json.loads(response.read(2_000_000).decode("utf-8"))
        items = []
        for row in payload.get("articles") or []:
            if not isinstance(row, dict):
                continue
            try:
                url = normalize_source_url(str(row.get("url") or ""))
            except ValueError:
                continue
            title = str(row.get("title") or "").strip()
            if not title or not url:
                continue
            items.append({
                "title": title[:300],
                "url": url,
                "published_at": str(row.get("seendate") or "")[:32] or None,
                "domain": str(row.get("domain") or "")[:120] or None,
                "language": str(row.get("language") or "")[:32] or None,
                "source_country": str(row.get("sourcecountry") or "")[:64] or None,
                "source_id": "gdelt_doc_2",
                "evidence_level": "tier_3",
            })
        latency = (time.monotonic() - started) * 1000
        _record_source("GDELT全球事件", latency, bool(items), "global-direct",
                       None if items else "empty_article_list")
        return {
            "ok": bool(items),
            "state": "complete" if items else "empty",
            "items": items[:bounded],
            "source": "GDELT DOC 2.0",
            "license_note": "仅保留标题、时间、来源域名与原文URL；不缓存正文",
        }
    except Exception as exc:  # noqa: BLE001 - 独立增强源失败不得影响主页面
        _record_source(
            "GDELT全球事件",
            (time.monotonic() - started) * 1000,
            False,
            "global-direct",
            type(exc).__name__,
        )
        return {
            "ok": False,
            "state": "unavailable",
            "items": [],
            "source": "GDELT DOC 2.0",
            "error": type(exc).__name__,
        }
