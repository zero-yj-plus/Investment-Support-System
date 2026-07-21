"""Official A-share disclosure adapters with explicit provenance.

The module queries two independent publication paths for one company:

* CNINFO, the designated disclosure archive used by listed companies; and
* the company's own exchange (SSE or SZSE).

Every request is bounded by timeout and response size.  A failed source never
turns into an empty successful list.  The normalized result carries the shared
``data-evidence-v1`` contract, per-source state and optional PDF SHA-256.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable, Iterable
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from .base import _record_source, direct_https_opener, log, ttl_cache
from .evidence_contract import build_evidence_contract


CNINFO_STOCKS_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PDF_BASE = "https://static.cninfo.com.cn/"
SSE_QUERY_URL = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
SSE_PDF_BASE = "https://static.sse.com.cn/"
SZSE_QUERY_URL = "https://www.szse.cn/api/disc/announcement/annList"
SZSE_PDF_BASE = "https://disc.static.szse.cn/download"

PARSER_VERSION = "official-disclosure-v1"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/126 Safari/537.36"
)
_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_PDF_BYTES = 16 * 1024 * 1024
_LAST_GOOD_MAX_AGE_SECONDS = 96 * 60 * 60
_ALLOWED_PDF_HOSTS = frozenset({
    "static.cninfo.com.cn", "static.sse.com.cn", "disc.static.szse.cn",
})
_BEIJING = ZoneInfo("Asia/Shanghai")


class DisclosureFetchError(RuntimeError):
    """A source failed before a valid response contract was obtained."""


Transport = Callable[..., bytes]


@dataclass(frozen=True)
class SourceAttempt:
    provider_family: str
    api_product: str
    source_url: str
    state: str
    count: int
    error: str | None = None
    raw_count: int | None = None
    schema_valid: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_family": self.provider_family,
            "api_product": self.api_product,
            "source_url": self.source_url,
            "state": self.state,
            "count": self.count,
            "error": self.error,
            "raw_count": self.raw_count,
            "parsed_count": self.count,
            "schema_valid": self.schema_valid,
        }


def _request_bytes(
    url: str,
    *,
    label: str,
    timeout: float,
    referer: str,
    method: str = "GET",
    params: Iterable[tuple[str, str]] | dict[str, str] | None = None,
    form: Iterable[tuple[str, str]] | dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    max_bytes: int = _MAX_JSON_BYTES,
) -> bytes:
    """Fetch one allowlisted endpoint within wall-clock and size bounds."""
    started = time.monotonic()
    query = urllib.parse.urlencode(params or {}, doseq=True)
    target = f"{url}?{query}" if query else url
    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        content_type = "application/json; charset=UTF-8"
    elif method == "POST":
        body = urllib.parse.urlencode(form or {}, doseq=True).encode()
        content_type = "application/x-www-form-urlencoded; charset=UTF-8"
    else:
        body = None
        content_type = "application/x-www-form-urlencoded; charset=UTF-8"
    request = urllib.request.Request(
        target,
        data=body,
        method=method,
        headers={
            "User-Agent": _UA,
            "Referer": referer,
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": content_type,
        },
    )
    # Explicit direct opener avoids urllib trying to interpret a process-wide
    # socks5h relay as an unsupported URL scheme.  Official domains are probed
    # independently and fail closed when the deployment cannot reach them.
    opener = direct_https_opener()
    deadline = started + max(0.2, float(timeout))
    try:
        chunks: list[bytes] = []
        size = 0
        with opener.open(request, timeout=min(4.0, max(0.2, float(timeout)))) as response:
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > max_bytes:
                raise DisclosureFetchError(f"response too large: {declared}")
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"total wall timeout after {timeout:.1f}s")
                chunk = response.read(8192)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise DisclosureFetchError(f"response exceeded {max_bytes} bytes")
                chunks.append(chunk)
        raw = b"".join(chunks)
        _record_source(label, (time.monotonic() - started) * 1000, True, "official-direct", None)
        return raw
    except Exception as exc:  # noqa: BLE001 - source boundary must degrade explicitly
        reason = f"{type(exc).__name__}: {str(exc)[:160]}"
        _record_source(label, (time.monotonic() - started) * 1000, False, "official-direct", reason)
        raise DisclosureFetchError(reason) from exc


def _json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DisclosureFetchError(f"{label} returned non-JSON content") from exc
    if not isinstance(payload, dict):
        raise DisclosureFetchError(f"{label} returned an invalid JSON root")
    return payload


def _code(value: object) -> str:
    code = str(value or "").strip().split(".")[0].zfill(6)
    if not re.fullmatch(r"\d{6}", code) or not code.startswith(("0", "3", "6")):
        raise ValueError("official disclosure currently supports SSE/SZSE A-share codes")
    return code


def _window(
    start_date: str,
    end_date: str,
    *,
    max_end: date | None = None,
) -> tuple[str, str]:
    try:
        start = date.fromisoformat(str(start_date))
        end = date.fromisoformat(str(end_date))
    except ValueError as exc:
        raise ValueError("start_date and end_date must be ISO dates") from exc
    if start > end:
        raise ValueError("start_date cannot be later than end_date")
    if (end - start).days > 366:
        raise ValueError("disclosure query window cannot exceed 366 days")
    if max_end is not None and end > max_end:
        raise ValueError("end_date cannot be later than the current Beijing date")
    return start.isoformat(), end.isoformat()


def _title(value: object) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:300]


def _absolute(base: str, path: object) -> str | None:
    value = str(path or "").strip()
    if not value:
        return None
    resolved = urllib.parse.urljoin(base, value)
    target = urllib.parse.urlsplit(resolved)
    expected = urllib.parse.urlsplit(base)
    if (
        target.scheme != "https"
        or not target.hostname
        or target.hostname.lower() != str(expected.hostname or "").lower()
        or target.username
        or target.password
    ):
        return None
    return resolved


def _row(
    *,
    provider_family: str,
    api_product: str,
    announcement_id: object,
    code: object,
    name: object,
    title: object,
    announcement_date: object,
    published_at: object,
    original_url: str | None,
) -> dict[str, Any] | None:
    clean_title = _title(title)
    clean_code = str(code or "").strip().zfill(6)
    day = str(announcement_date or "")[:10]
    if not clean_title or not re.fullmatch(r"\d{6}", clean_code):
        return None
    try:
        day = date.fromisoformat(day).isoformat()
    except ValueError:
        return None
    published = str(published_at or day).strip()
    return {
        "announcement_id": str(announcement_id or "").strip() or None,
        "code": clean_code,
        "name": str(name or "").strip()[:80],
        "title": clean_title,
        "announcement_date": day,
        "published_at": published,
        "published_at_precision": "timestamp" if "T" in published or " " in published else "date",
        "original_url": original_url,
        "provider_family": provider_family,
        "api_product": api_product,
        "evidence_level": "tier_1",
        "pdf_sha256": None,
        "pdf_size_bytes": None,
        "pdf_hash_state": "not_requested",
    }


def parse_cninfo(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("announcements") or []
    if not isinstance(rows, list):
        raise DisclosureFetchError("cninfo announcements field is not a list")
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        raw_time = item.get("announcementTime")
        try:
            day = (
                datetime.fromtimestamp(float(raw_time) / 1000, tz=timezone.utc)
                .astimezone(_BEIJING)
                .date()
                .isoformat()
            )
        except (TypeError, ValueError, OSError):
            day = str(item.get("announcementDate") or "")[:10]
        normalized = _row(
            provider_family="cninfo",
            api_product="hisAnnouncement",
            announcement_id=item.get("announcementId"),
            code=item.get("secCode"),
            name=item.get("secName"),
            title=item.get("announcementTitle"),
            announcement_date=day,
            # CNINFO's millisecond value is date-granular for these records; do
            # not fabricate a clock time from its midnight representation.
            published_at=day,
            original_url=_absolute(CNINFO_PDF_BASE, item.get("adjunctUrl")),
        )
        if normalized:
            out.append(normalized)
    return out


def parse_sse(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("result")
    if rows is None:
        rows = (payload.get("pageHelp") or {}).get("data")
    if not isinstance(rows, list):
        raise DisclosureFetchError("SSE result field is not a list")
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        normalized = _row(
            provider_family="sse",
            api_product="queryCompanyBulletin",
            announcement_id=item.get("OPERATION_SEQ") or item.get("URL"),
            code=item.get("SECURITY_CODE"),
            name=item.get("SECURITY_NAME"),
            title=item.get("TITLE"),
            announcement_date=item.get("SSEDATE"),
            published_at=item.get("SSEDATE"),
            original_url=_absolute(SSE_PDF_BASE, item.get("URL")),
        )
        if normalized:
            out.append(normalized)
    return out


def parse_szse(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("data") or payload.get("announcements") or []
    if isinstance(rows, dict):
        rows = rows.get("data") or rows.get("list") or []
    if not isinstance(rows, list):
        raise DisclosureFetchError("SZSE data field is not a list")
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        published = str(item.get("publishTime") or item.get("publishDate") or "").strip()
        published_value = published[:10] if published.endswith(" 00:00:00") else published
        raw_code = item.get("secCode") or item.get("code")
        raw_name = item.get("secName") or item.get("name")
        if isinstance(raw_code, list):
            raw_code = raw_code[0] if raw_code else None
        if isinstance(raw_name, list):
            raw_name = raw_name[0] if raw_name else None
        normalized = _row(
            provider_family="szse",
            api_product="announcement-annList",
            announcement_id=(
                item.get("annId") or item.get("id")
                or item.get("announcementId") or item.get("attachPath")
            ),
            code=raw_code,
            name=raw_name,
            title=item.get("title"),
            announcement_date=published[:10],
            # Midnight is the site's date placeholder, not evidence that the
            # announcement was released at exactly 00:00:00.
            published_at=published_value,
            original_url=_absolute(SZSE_PDF_BASE, item.get("attachPath") or item.get("url")),
        )
        if normalized:
            out.append(normalized)
    return out


def _resolve_cninfo(code: str, transport: Transport, timeout: float) -> tuple[str, str]:
    raw = transport(
        CNINFO_STOCKS_URL,
        label="巨潮公告股票目录",
        timeout=timeout,
        referer="https://www.cninfo.com.cn/new/index",
    )
    payload = _json(raw, "cninfo stock catalogue")
    for item in payload.get("stockList") or []:
        if isinstance(item, dict) and str(item.get("code")) == code:
            org_id = str(item.get("orgId") or "").strip()
            if org_id:
                return org_id, str(item.get("zwjc") or "").strip()
    raise DisclosureFetchError(f"cninfo orgId not found for {code}")


def _fetch_cninfo(
    code: str, start: str, end: str, limit: int, transport: Transport, timeout: float,
) -> tuple[list[dict[str, Any]], SourceAttempt]:
    org_id, _ = _resolve_cninfo(code, transport, timeout)
    column = "sse" if code.startswith("6") else "szse"
    raw = transport(
        CNINFO_QUERY_URL,
        label="巨潮公告",
        timeout=timeout,
        referer="https://www.cninfo.com.cn/new/index",
        method="POST",
        form={
            "pageNum": "1", "pageSize": str(limit), "column": column,
            "tabName": "fulltext", "plate": "", "stock": f"{code},{org_id}",
            "searchkey": "", "secid": "", "category": "", "trade": "",
            "seDate": f"{start}~{end}", "sortName": "", "sortType": "",
            "isHLtitle": "true",
        },
    )
    payload = _json(raw, "cninfo announcements")
    raw_rows = payload.get("announcements")
    if not isinstance(raw_rows, list):
        raise DisclosureFetchError("cninfo announcements field is not a list")
    parsed = parse_cninfo(payload)
    items = [item for item in parsed if item.get("code") == code][:limit]
    if raw_rows and not items:
        raise DisclosureFetchError("cninfo returned rows but none passed schema/code validation")
    schema_valid = len(parsed) == len(raw_rows) and len(items) == len(parsed)
    state = "ok" if schema_valid else "degraded"
    error = None if schema_valid else f"parsed {len(items)} of {len(raw_rows)} rows for requested code"
    return items, SourceAttempt(
        "cninfo", "hisAnnouncement", CNINFO_QUERY_URL, state, len(items), error,
        raw_count=len(raw_rows), schema_valid=schema_valid,
    )


def _fetch_sse(
    code: str, start: str, end: str, limit: int, transport: Transport, timeout: float,
) -> tuple[list[dict[str, Any]], SourceAttempt]:
    params = {
        "isPagination": "true", "productId": code, "securityType": "0101",
        "reportType2": "", "reportType": "ALL", "beginDate": start, "endDate": end,
        "pageHelp.pageSize": str(limit), "pageHelp.pageCount": "50",
        "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
        "pageHelp.cacheSize": "1", "pageHelp.endPage": "5",
    }
    raw = transport(
        SSE_QUERY_URL,
        label="上交所公告",
        timeout=timeout,
        referer=f"https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId={code}",
        params=params,
    )
    payload = _json(raw, "SSE announcements")
    raw_rows = payload.get("result")
    if raw_rows is None:
        raw_rows = (payload.get("pageHelp") or {}).get("data")
    if not isinstance(raw_rows, list):
        raise DisclosureFetchError("SSE result field is not a list")
    parsed = parse_sse(payload)
    items = [item for item in parsed if item.get("code") == code][:limit]
    if raw_rows and not items:
        raise DisclosureFetchError("SSE returned rows but none passed schema/code validation")
    schema_valid = len(parsed) == len(raw_rows) and len(items) == len(parsed)
    state = "ok" if schema_valid else "degraded"
    error = None if schema_valid else f"parsed {len(items)} of {len(raw_rows)} rows for requested code"
    return items, SourceAttempt(
        "sse", "queryCompanyBulletin", SSE_QUERY_URL, state, len(items), error,
        raw_count=len(raw_rows), schema_valid=schema_valid,
    )


def _fetch_szse(
    code: str, start: str, end: str, limit: int, transport: Transport, timeout: float,
) -> tuple[list[dict[str, Any]], SourceAttempt]:
    raw = transport(
        SZSE_QUERY_URL,
        label="深交所公告",
        timeout=timeout,
        referer=f"https://www.szse.cn/disclosure/listed/notice/index.html?stock={code}",
        method="POST",
        json_body={
            "pageSize": limit,
            "pageNum": 1,
            "channelCode": ["listedNotice_disc"],
            "seDate": [start, end],
            "stock": [code],
        },
    )
    payload = _json(raw, "SZSE announcements")
    raw_rows = payload.get("data") or payload.get("announcements") or []
    if isinstance(raw_rows, dict):
        raw_rows = raw_rows.get("data") or raw_rows.get("list") or []
    if not isinstance(raw_rows, list):
        raise DisclosureFetchError("SZSE data field is not a list")
    parsed = parse_szse(payload)
    items = [item for item in parsed if item.get("code") == code][:limit]
    if raw_rows and not items:
        raise DisclosureFetchError("SZSE returned rows but none passed schema/code validation")
    schema_valid = len(parsed) == len(raw_rows) and len(items) == len(parsed)
    state = "ok" if schema_valid else "degraded"
    error = None if schema_valid else f"parsed {len(items)} of {len(raw_rows)} rows for requested code"
    return items, SourceAttempt(
        "szse", "announcement-annList", SZSE_QUERY_URL, state, len(items), error,
        raw_count=len(raw_rows), schema_valid=schema_valid,
    )


def _dedupe(source_items: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for items in source_items:
        for item in items:
            normalized_title = re.sub(r"[\s\W]+", "", item["title"], flags=re.UNICODE).lower()
            normalized_name = re.sub(r"[\s\W]+", "", item.get("name") or "", flags=re.UNICODE).lower()
            if normalized_name and normalized_title.startswith(normalized_name):
                normalized_title = normalized_title[len(normalized_name):]
            key = (item["code"], item["announcement_date"], normalized_title)
            if key not in by_key:
                merged = dict(item)
                merged["source_confirmations"] = [item["provider_family"]]
                by_key[key] = merged
            else:
                sources = by_key[key]["source_confirmations"]
                if item["provider_family"] not in sources:
                    sources.append(item["provider_family"])
                # Prefer a working official PDF URL when the first source did
                # not provide one; never silently replace a different URL.
                if not by_key[key].get("original_url") and item.get("original_url"):
                    by_key[key]["original_url"] = item["original_url"]
    return sorted(
        by_key.values(), key=lambda row: (row["announcement_date"], row["title"]), reverse=True,
    )[:limit]


def _hash_pdf(item: dict[str, Any], transport: Transport, timeout: float) -> None:
    url = item.get("original_url")
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        if parts.scheme != "https" or parts.hostname not in _ALLOWED_PDF_HOSTS:
            raise DisclosureFetchError("PDF URL is not on an official allowlisted host")
        raw = transport(
            str(url), label="公告PDF验证", timeout=timeout,
            referer="https://www.cninfo.com.cn/", max_bytes=_MAX_PDF_BYTES,
        )
        if not raw.startswith(b"%PDF-"):
            raise DisclosureFetchError("official URL did not return a PDF")
        item["pdf_sha256"] = hashlib.sha256(raw).hexdigest()
        item["pdf_size_bytes"] = len(raw)
        item["pdf_hash_state"] = "verified"
    except DisclosureFetchError as exc:
        item["pdf_hash_state"] = "unavailable"
        item["pdf_hash_error"] = str(exc)[:180]


def _save_last_good(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            tmp_path = Path(handle.name)
        tmp_path.chmod(0o600)
        tmp_path.replace(path)
        path.chmod(0o600)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _load_last_good(
    path: Path,
    code: str,
    query: dict[str, Any],
    replayed_at: datetime,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("code") != code
        or payload.get("query") != query
    ):
        return None
    if not isinstance(payload.get("items"), list):
        return None
    try:
        raw_fetched = str(payload.get("fetched_at") or "").replace("Z", "+00:00")
        source_fetched_at = datetime.fromisoformat(raw_fetched)
        if source_fetched_at.tzinfo is None or source_fetched_at.utcoffset() is None:
            return None
        age = (replayed_at.astimezone(timezone.utc) - source_fetched_at.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return None
    if age < 0 or age > _LAST_GOOD_MAX_AGE_SECONDS:
        return None
    payload["snapshot_age_seconds"] = int(age)
    return payload


def fetch_official_disclosures(
    code: str,
    *,
    start_date: str,
    end_date: str,
    limit: int = 20,
    timeout: float = 8.0,
    verify_pdf: bool = False,
    pdf_hash_limit: int = 3,
    last_good_path: str | Path | None = None,
    transport: Transport | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Return normalized CNINFO + exchange disclosures for one A-share code."""
    clean_code = _code(code)
    bounded_limit = max(1, min(int(limit), 50))
    bounded_hash_limit = max(0, min(int(pdf_hash_limit), bounded_limit))
    request = transport or _request_bytes
    clock = now_fn or (lambda: datetime.now(timezone.utc))
    started_at = clock()
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("now_fn must return a timezone-aware datetime")
    start, end = _window(
        start_date,
        end_date,
        max_end=started_at.astimezone(_BEIJING).date(),
    )
    query_contract = {
        "start": start,
        "end": end,
        "limit": bounded_limit,
        "verify_pdf": bool(verify_pdf),
        "pdf_hash_limit": bounded_hash_limit if verify_pdf else 0,
    }

    exchange_fetch = _fetch_sse if clean_code.startswith("6") else _fetch_szse
    calls = (
        ("cninfo", _fetch_cninfo),
        ("exchange", exchange_fetch),
    )
    successes: list[list[dict[str, Any]]] = []
    attempts: list[SourceAttempt] = []
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="official-disclosure")
    deadline = time.monotonic() + max(0.2, float(timeout)) + 1.0
    try:
        futures = {
            name: pool.submit(fn, clean_code, start, end, bounded_limit, request, timeout)
            for name, fn in calls
        }
        for name, future in futures.items():
            try:
                remaining = max(0.01, deadline - time.monotonic())
                items, attempt = future.result(timeout=remaining)
                successes.append(items)
                attempts.append(attempt)
            except Exception as exc:  # noqa: BLE001 - retain independent source result
                provider = "cninfo" if name == "cninfo" else ("sse" if clean_code.startswith("6") else "szse")
                product = "hisAnnouncement" if name == "cninfo" else (
                    "queryCompanyBulletin" if provider == "sse" else "announcement-annList"
                )
                source_url = CNINFO_QUERY_URL if name == "cninfo" else (
                    SSE_QUERY_URL if provider == "sse" else SZSE_QUERY_URL
                )
                attempts.append(SourceAttempt(
                    provider, product, source_url, "unavailable", 0, str(exc)[:180],
                    raw_count=None, schema_valid=False,
                ))
    finally:
        # Running urllib calls are bounded internally.  Do not make the API
        # response wait for ThreadPoolExecutor's context-manager shutdown after
        # the aggregate wall deadline has already expired.
        pool.shutdown(wait=False, cancel_futures=True)

    completed_at = clock()
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise ValueError("now_fn must return a timezone-aware datetime")
    replayed_at = completed_at.astimezone(timezone.utc)

    items = [
        item for item in _dedupe(successes, bounded_limit)
        if item.get("code") == clean_code
    ]
    failures = [attempt for attempt in attempts if attempt.state != "ok"]
    live_sources = [attempt for attempt in attempts if attempt.state in {"ok", "degraded"}]
    verification_state = "unavailable"
    degraded_reasons: list[str] = [
        f"{attempt.provider_family}:{attempt.error or '来源结构异常'}"
        for attempt in failures
        if attempt.state == "degraded"
    ]
    if len(live_sources) == 2:
        confirmed = sum(len(item.get("source_confirmations") or []) >= 2 for item in items)
        if not items:
            if all(attempt.state == "ok" for attempt in live_sources):
                verification_state = "empty_confirmed"
            else:
                verification_state = "empty_unverified"
                degraded_reasons.append("官方链路返回空结果，但字段结构未通过完整校验")
        elif confirmed == len(items) and all(attempt.state == "ok" for attempt in live_sources):
            verification_state = "cross_confirmed"
        elif confirmed:
            verification_state = "partially_cross_confirmed"
            degraded_reasons.append(
                f"当前仅 {confirmed}/{len(items)} 条公告完成巨潮与交易所双源交叉确认"
            )
        else:
            verification_state = "divergent"
            degraded_reasons.append("巨潮与交易所返回了数据，但当前窗口无同标题交叉命中")
    elif live_sources:
        verification_state = "single_source"
        degraded_reasons.extend(
            f"{attempt.provider_family}:{attempt.error or '当前不可用'}" for attempt in failures
        )

    if verify_pdf:
        for item in items[:bounded_hash_limit]:
            _hash_pdf(item, request, timeout)
        pdf_failures = sum(item.get("pdf_hash_state") == "unavailable" for item in items[:bounded_hash_limit])
        if pdf_failures:
            degraded_reasons.append(f"{pdf_failures}份公告PDF未完成哈希验证")

    cache_path = Path(last_good_path) if last_good_path else None
    used_last_good = False
    source_fetched_at: str | datetime = replayed_at
    snapshot_age_seconds: int | None = None
    if not live_sources and cache_path:
        cached = _load_last_good(cache_path, clean_code, query_contract, replayed_at)
        if cached:
            items = list(cached["items"])
            used_last_good = True
            verification_state = "last_good"
            degraded_reasons.append("两条官方链路当前失败，展示上次成功快照")
            data_as_of = cached.get("data_as_of") or end
            source_fetched_at = str(cached.get("fetched_at"))
            snapshot_age_seconds = cached.get("snapshot_age_seconds")
        else:
            data_as_of = None
    else:
        data_as_of = end if live_sources else None

    if live_sources and not degraded_reasons:
        data_state = "complete"
        degraded_reason = None
    elif live_sources or used_last_good:
        data_state = "degraded"
        degraded_reason = "；".join(dict.fromkeys(degraded_reasons)) or "仅有部分官方链路可用"
    else:
        data_state = "missing"
        degraded_reason = "；".join(
            f"{attempt.provider_family}:{attempt.error or '当前不可用'}" for attempt in attempts
        ) or "官方公告链路当前不可用"

    contract = build_evidence_contract(
        content=items if data_state != "missing" else None,
        provider_family="cninfo+exchange",
        api_product="official-disclosure-aggregate",
        data_as_of=data_as_of,
        fetched_at=source_fetched_at,
        parser_version=PARSER_VERSION,
        source_url=CNINFO_QUERY_URL,
        evidence_level="tier_1",
        data_state=data_state,
        degraded_reason=degraded_reason,
    )
    result = {
        "ok": data_state != "missing",
        "code": clean_code,
        "exchange": "sse" if clean_code.startswith("6") else "szse",
        "requested_window": {"start": start, "end": end},
        "data_as_of": data_as_of,
        "fetched_at": contract.fetched_at,
        "source_fetched_at": contract.fetched_at if data_state != "missing" else None,
        "replayed_at": replayed_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "snapshot_age_seconds": snapshot_age_seconds,
        "state": data_state,
        "verification_state": verification_state,
        "used_last_good": used_last_good,
        "items": items,
        "sources": [attempt.to_dict() for attempt in attempts],
        "evidence": contract.to_dict(),
        "degraded_reasons": [degraded_reason] if degraded_reason else [],
    }
    if cache_path and data_state == "complete":
        try:
            _save_last_good(cache_path, {
                "code": clean_code, "data_as_of": data_as_of,
                "fetched_at": contract.fetched_at, "items": items,
                "query": query_contract,
            })
        except OSError as exc:
            log.info("公告 last-good 写入失败: %s", str(exc)[:140])
    return result


@ttl_cache(300, fail_seconds=60)
def cached_official_disclosures(
    code: str,
    start_date: str,
    end_date: str,
    limit: int = 20,
    verify_pdf: bool = False,
    pdf_hash_limit: int = 1,
    last_good_path: str | None = None,
) -> dict[str, Any]:
    """Five-minute single-flight cache for UI/API consumers.

    The original ``fetched_at`` stays visible while cached, so a UI can tell a
    reused response from a new upstream request.  Runtime failures are still
    represented by the result's explicit state rather than a fabricated empty
    success.
    """
    return fetch_official_disclosures(
        code,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        verify_pdf=verify_pdf,
        pdf_hash_limit=pdf_hash_limit,
        last_good_path=last_good_path,
    )
