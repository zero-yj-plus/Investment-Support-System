"""Bounded adapter for the Shenzhen Stock Exchange interaction website.

The upstream paths used here are website-internal endpoints, not a documented
public API and not an SLA-backed service.  Their content is deliberately
modelled as ``交易所互动问答`` and ``管理层回复``.  It is research context,
not a formal disclosure or an exchange announcement.

Two requests are required by the live website:

* ``queryKeyboardInfo`` receives ``keyWord`` in a form-encoded POST body and
  resolves the exact security code to its ``secid``;
* ``company/question`` receives its parameters in the query string while the
  POST body remains empty.

Failures, confirmed empty results, schema drift and last-good replay are kept
as distinct states.  A non-empty malformed response can therefore never be
reported as a successful empty result.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable, Iterable
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from .base import _record_source, direct_https_opener, log, ttl_cache
from .evidence_contract import build_evidence_contract, compute_content_hash


KEYWORD_URL = "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo"
QUESTION_URL = "https://irm.cninfo.com.cn/newircs/company/question"
REFERER_URL = "https://irm.cninfo.com.cn/newircs/"

PARSER_VERSION = "official-interaction-v1"
PROVIDER_FAMILY = "szse-irm"
API_PRODUCT = "website-internal-company-question"
_ALLOWED_HOSTS = frozenset({"irm.cninfo.com.cn"})
_MAX_JSON_BYTES = 4 * 1024 * 1024
_LAST_GOOD_MAX_AGE_SECONDS = 96 * 60 * 60
_BEIJING = ZoneInfo("Asia/Shanghai")
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/126 Safari/537.36"
)


class InteractionFetchError(RuntimeError):
    """The interaction website failed before a valid contract was obtained."""


Transport = Callable[..., bytes]


@dataclass(frozen=True)
class SourceAttempt:
    endpoint: str
    source_url: str
    state: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_family": PROVIDER_FAMILY,
            "api_product": self.endpoint,
            "source_url": self.source_url,
            "state": self.state,
            "error": self.error,
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Fail closed rather than following a redirect outside the allowlist."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _bounded_timeout(value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be a finite positive number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("timeout must be a finite positive number")
    return min(max(parsed, 0.2), 30.0)


def _validate_official_url(url: str) -> str:
    value = str(url or "").strip()
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise InteractionFetchError("invalid official interaction URL") from exc
    if (
        parts.scheme.lower() != "https"
        or str(parts.hostname or "").lower() not in _ALLOWED_HOSTS
        or parts.username
        or parts.password
        or parts.fragment
        or port not in {None, 443}
    ):
        raise InteractionFetchError(
            "interaction request must use an allowlisted official HTTPS host"
        )
    return value


def _request_bytes(
    url: str,
    *,
    label: str,
    timeout: float,
    referer: str,
    method: str = "POST",
    params: Iterable[tuple[str, str]] | dict[str, str] | None = None,
    form: Iterable[tuple[str, str]] | dict[str, str] | None = None,
    max_bytes: int = _MAX_JSON_BYTES,
) -> bytes:
    """Fetch one allowlisted endpoint within wall-clock and size boundaries."""
    official_url = _validate_official_url(url)
    official_referer = _validate_official_url(referer)
    bounded_timeout = _bounded_timeout(timeout)
    if not isinstance(max_bytes, int) or max_bytes < 1 or max_bytes > _MAX_JSON_BYTES:
        raise ValueError(f"max_bytes must be between 1 and {_MAX_JSON_BYTES}")
    normalized_method = str(method or "").upper()
    if normalized_method != "POST":
        raise ValueError("interaction website endpoints require POST")

    query = urllib.parse.urlencode(params or {}, doseq=True)
    target = f"{official_url}?{query}" if query else official_url
    # ``form=None`` intentionally becomes an empty POST body for
    # company/question.  It is not equivalent to a GET request.
    body = urllib.parse.urlencode(form or {}, doseq=True).encode("utf-8")
    request = urllib.request.Request(
        target,
        data=body,
        method="POST",
        headers={
            "User-Agent": _UA,
            "Referer": official_referer,
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    opener = direct_https_opener(_NoRedirect())
    started = time.monotonic()
    deadline = started + bounded_timeout
    try:
        chunks: list[bytes] = []
        received = 0
        with opener.open(request, timeout=min(4.0, bounded_timeout)) as response:
            _validate_official_url(str(response.geturl() or target))
            raw_length = response.headers.get("Content-Length")
            if raw_length:
                try:
                    declared = int(raw_length)
                except (TypeError, ValueError) as exc:
                    raise InteractionFetchError("invalid Content-Length header") from exc
                if declared < 0 or declared > max_bytes:
                    raise InteractionFetchError(f"response too large: {declared}")
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"total wall timeout after {bounded_timeout:.1f}s"
                    )
                chunk = response.read(8192)
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise InteractionFetchError(
                        f"response exceeded {max_bytes} bytes"
                    )
                chunks.append(chunk)
        raw = b"".join(chunks)
        _record_source(
            label,
            (time.monotonic() - started) * 1000,
            True,
            "official-direct",
            None,
        )
        return raw
    except Exception as exc:  # noqa: BLE001 - source boundary degrades explicitly
        reason = f"{type(exc).__name__}: {str(exc)[:160]}"
        _record_source(
            label,
            (time.monotonic() - started) * 1000,
            False,
            "official-direct",
            reason,
        )
        raise InteractionFetchError(reason) from exc


def _json(raw: bytes, label: str) -> dict[str, Any]:
    if len(raw) > _MAX_JSON_BYTES:
        raise InteractionFetchError(f"{label} response exceeded size boundary")
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InteractionFetchError(f"{label} returned non-JSON content") from exc
    if not isinstance(payload, dict):
        raise InteractionFetchError(f"{label} returned an invalid JSON root")
    return payload


def _code(value: object) -> str:
    raw = str(value or "").strip().upper()
    if "." in raw:
        raw = raw.split(".", 1)[0]
    code = raw.zfill(6)
    if not re.fullmatch(r"\d{6}", code) or not code.startswith(("0", "3")):
        raise ValueError(
            "official interaction currently supports Shenzhen A-share codes"
        )
    return code


def _row_code(value: object) -> str | None:
    raw = str(value or "").strip().upper()
    if not raw:
        return None
    if "." in raw:
        raw = raw.split(".", 1)[0]
    code = raw.zfill(6)
    return code if re.fullmatch(r"\d{6}", code) else None


def _window(
    start_date: str | None,
    end_date: str | None,
    *,
    current_beijing_date: date,
) -> tuple[str, str]:
    start_raw = str(start_date or "").strip()
    end_raw = str(end_date or "").strip()
    if not start_raw and not end_raw:
        return "", ""
    if not start_raw or not end_raw:
        raise ValueError("start_date and end_date must be supplied together")
    try:
        start = date.fromisoformat(start_raw)
        end = date.fromisoformat(end_raw)
    except ValueError as exc:
        raise ValueError("start_date and end_date must be ISO dates") from exc
    if start > end:
        raise ValueError("start_date cannot be later than end_date")
    if (end - start).days > 366:
        raise ValueError("interaction query window cannot exceed 366 days")
    if end > current_beijing_date:
        raise ValueError("end_date cannot be later than the current Beijing date")
    return start.isoformat(), end.isoformat()


def _clean_text(value: object, *, max_length: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value)))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None, False
    truncated = len(text) > max_length
    return text[:max_length], truncated


def _site_time(value: object) -> tuple[str | None, str | None]:
    """Normalize website milliseconds/ISO text with explicit Beijing semantics."""
    if value is None or value == "":
        return None, None
    parsed: datetime | None = None
    raw = str(value).strip()
    try:
        numeric = float(raw)
        if math.isfinite(numeric):
            seconds = numeric / 1000 if abs(numeric) >= 100_000_000_000 else numeric
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(_BEIJING)
    except (ValueError, TypeError, OSError, OverflowError):
        parsed = None
    if parsed is None:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            try:
                day = date.fromisoformat(raw)
            except ValueError:
                return None, None
            if day.year < 2000:
                return None, None
            return day.isoformat(), "date"
        iso_raw = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(iso_raw)
        except ValueError:
            return None, None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=_BEIJING)
        else:
            parsed = parsed.astimezone(_BEIJING)
    if parsed.year < 2000:
        return None, None
    return parsed.isoformat(timespec="seconds"), "timestamp"


def _is_future(value: str | None, fetched_at: datetime) -> bool:
    if not value:
        return False
    if len(value) == 10:
        return date.fromisoformat(value) > fetched_at.astimezone(_BEIJING).date()
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(timezone.utc) > fetched_at.astimezone(timezone.utc)


def parse_keyword_info(payload: dict[str, Any], code: str) -> dict[str, str]:
    """Select an exact-code company from the autocomplete response."""
    clean_code = _code(code)
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise InteractionFetchError("queryKeyboardInfo data field is not a list")
    matches: list[dict[str, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        candidate = None
        for key in ("stockcode", "stockCode", "secCode", "code"):
            if item.get(key) not in (None, ""):
                candidate = _row_code(item.get(key))
                break
        if candidate != clean_code:
            continue
        org_id = str(item.get("secid") or item.get("orgId") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", org_id):
            continue
        name, _ = _clean_text(
            item.get("secname") or item.get("secName") or item.get("name"),
            max_length=80,
        )
        matches.append({
            "code": clean_code,
            "org_id": org_id,
            "name": name or "",
        })
    org_ids = {item["org_id"] for item in matches}
    if not matches:
        raise InteractionFetchError(
            "queryKeyboardInfo returned no exact security-code match"
        )
    if len(org_ids) != 1:
        raise InteractionFetchError(
            "queryKeyboardInfo returned ambiguous exact-code matches"
        )
    return matches[0]


def _payload_total(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    value = payload.get("total")
    if isinstance(value, bool):
        return None, "total 字段类型异常"
    try:
        total = int(value)
    except (TypeError, ValueError):
        return None, "total 字段缺失或无效"
    if total < 0:
        return None, "total 字段不得为负数"
    return total, None


def parse_question_page(
    payload: dict[str, Any],
    *,
    code: str,
    company_name: str,
    fetched_at: datetime,
    limit: int,
) -> tuple[list[dict[str, Any]], int | None, int, list[str]]:
    """Normalize one page and return items, total, raw count and issues."""
    clean_code = _code(code)
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise InteractionFetchError("company/question rows field is not a list")
    total, total_issue = _payload_total(payload)
    issues: list[str] = [total_issue] if total_issue else []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            issues.append(f"第 {index + 1} 条记录不是对象")
            continue
        raw_row_codes = [
            item.get(key)
            for key in ("stockcode", "stockCode", "secCode", "code")
            if item.get(key) not in (None, "")
        ]
        normalized_row_codes = [_row_code(value) for value in raw_row_codes]
        row_codes = {value for value in normalized_row_codes if value is not None}
        # The live response is already scoped by stockcode + orgId and may not
        # repeat a code in each row.  When it does, any mismatch is rejected.
        if raw_row_codes and (
            any(value is None for value in normalized_row_codes)
            or row_codes != {clean_code}
        ):
            issues.append(f"第 {index + 1} 条记录股票代码不匹配")
            continue

        question, question_truncated = _clean_text(
            item.get("mainContent") or item.get("questionContent"),
            max_length=12_000,
        )
        answer, answer_truncated = _clean_text(
            item.get("attachedContent") or item.get("answerContent"),
            max_length=20_000,
        )
        question_time, question_precision = _site_time(
            item.get("pubDate") or item.get("questionPubDate")
        )
        if not question or not question_time or _is_future(question_time, fetched_at):
            issues.append(f"第 {index + 1} 条记录缺少有效提问内容或时间")
            continue
        if question_truncated:
            issues.append(f"第 {index + 1} 条提问内容超长，已截断")

        answered = answer is not None
        answer_time: str | None = None
        answer_precision: str | None = None
        raw_answer_time = item.get("attachedPubDate") or item.get("answerPubDate")
        if answered:
            answer_time, answer_precision = _site_time(raw_answer_time)
            if not answer_time or _is_future(answer_time, fetched_at):
                answer_time = None
                answer_precision = None
                issues.append(f"第 {index + 1} 条管理层回复缺少有效回复时间")
            elif (
                answer_time[:10] < question_time[:10]
                or (
                    answer_precision == "timestamp"
                    and question_precision == "timestamp"
                    and datetime.fromisoformat(answer_time)
                    < datetime.fromisoformat(question_time)
                )
            ):
                issues.append(f"第 {index + 1} 条管理层回复时间早于提问时间")
            if answer_truncated:
                issues.append(f"第 {index + 1} 条管理层回复超长，已截断")
        elif raw_answer_time not in (None, "", 0, "0"):
            issues.append(f"第 {index + 1} 条存在回复时间但无回复内容")

        question_id = str(
            item.get("questionId") or item.get("id") or item.get("questionNo") or ""
        ).strip() or None
        dedupe_key = question_id or hashlib.sha256(
            f"{clean_code}\0{question_time}\0{question}".encode("utf-8")
        ).hexdigest()
        if dedupe_key in seen:
            issues.append(f"第 {index + 1} 条记录重复")
            continue
        seen.add(dedupe_key)
        items.append({
            "question_id": question_id,
            "code": clean_code,
            "company_name": company_name,
            "question": question,
            "answer": answer,
            "answered": answered,
            "answer_status": "answered" if answered else "unanswered",
            "question_pub_date": question_time,
            "question_pub_date_precision": question_precision,
            "answer_pub_date": answer_time,
            "answer_pub_date_precision": answer_precision,
            "source_type": "交易所互动问答",
            "answer_source_type": "管理层回复" if answered else None,
            "is_formal_disclosure": False,
            "provider_family": PROVIDER_FAMILY,
            "api_product": API_PRODUCT,
            "evidence_level": "tier_3",
            "source_url": QUESTION_URL,
        })

    raw_count = len(rows)
    if raw_count > limit:
        issues.append(f"来源返回 {raw_count} 条，超过请求上限 {limit} 条")
        items = items[:limit]
    if total is not None:
        if total == 0 and raw_count:
            issues.append("total 为 0 但 rows 非空")
        elif total < raw_count:
            issues.append("total 小于当前页 rows 数量")
    return items, total, raw_count, issues


def _query_contract(
    code: str,
    start_date: str,
    end_date: str,
    limit: int,
) -> dict[str, Any]:
    return {
        "code": code,
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "page_num": 1,
    }


def initialize_customer_cache(customer_data_dir: str | Path) -> Path:
    """Create the customer-scoped interaction cache directory as private."""
    root = Path(customer_data_dir).expanduser() / "official-interactions"
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    if not root.is_dir():
        raise OSError("interaction cache path is not a directory")
    return root


def customer_cache_path(
    customer_data_dir: str | Path,
    query: dict[str, Any],
) -> Path:
    """Return a private, exact-query cache path without window collisions."""
    code = _code(query.get("code"))
    canonical = json.dumps(
        query,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:16]
    return initialize_customer_cache(customer_data_dir) / f"{code}-{digest}.json"


def _save_last_good(path: Path, payload: dict[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise OSError("refusing to replace a symlink cache path")
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
            handle.flush()
            os.fsync(handle.fileno())
            tmp_path = Path(handle.name)
        tmp_path.chmod(0o600)
        os.replace(tmp_path, path)
        path.chmod(0o600)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _aware_utc(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _load_last_good(
    path: Path,
    *,
    code: str,
    query: dict[str, Any],
    replayed_at: datetime,
    max_age_seconds: float,
) -> dict[str, Any] | None:
    if path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("parser_version") != PARSER_VERSION
        or payload.get("code") != code
        or payload.get("query") != query
        or payload.get("source_state") not in {"complete", "complete_empty"}
        or not isinstance(payload.get("items"), list)
        or not payload.get("data_as_of")
    ):
        return None
    items = payload["items"]
    if payload.get("content_hash") != compute_content_hash(items):
        return None
    if any(
        not isinstance(item, dict)
        or item.get("code") != code
        or item.get("is_formal_disclosure") is not False
        for item in items
    ):
        return None
    source_fetched_at = _aware_utc(payload.get("fetched_at"))
    if source_fetched_at is None:
        return None
    age = (
        replayed_at.astimezone(timezone.utc) - source_fetched_at
    ).total_seconds()
    if age < 0 or age > max_age_seconds:
        return None
    payload["snapshot_age_seconds"] = int(age)
    return payload


def _remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise InteractionFetchError("interaction request exceeded total wall timeout")
    return value


def _as_of(items: list[dict[str, Any]], fallback: str) -> str:
    values = [
        str(value)
        for item in items
        for value in (item.get("question_pub_date"), item.get("answer_pub_date"))
        if value
    ]
    return max(values) if values else fallback


def _reason(reasons: list[str], fallback: str) -> str:
    joined = "；".join(dict.fromkeys(str(item).strip() for item in reasons if item))
    return (joined or fallback)[:500]


def fetch_official_interactions(
    code: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 20,
    timeout: float = 8.0,
    last_good_path: str | Path | None = None,
    customer_data_dir: str | Path | None = None,
    last_good_max_age_seconds: float = _LAST_GOOD_MAX_AGE_SECONDS,
    transport: Transport | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Fetch normalized Shenzhen exchange interactions for one exact code."""
    clean_code = _code(code)
    bounded_limit = max(1, min(int(limit), 100))
    bounded_timeout = _bounded_timeout(timeout)
    try:
        max_age = float(last_good_max_age_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("last_good_max_age_seconds must be non-negative") from exc
    if not math.isfinite(max_age) or max_age < 0:
        raise ValueError("last_good_max_age_seconds must be non-negative")
    if last_good_path is not None and customer_data_dir is not None:
        raise ValueError("use either last_good_path or customer_data_dir, not both")

    clock = now_fn or (lambda: datetime.now(timezone.utc))
    started_at = clock()
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("now_fn must return a timezone-aware datetime")
    start, end = _window(
        start_date,
        end_date,
        current_beijing_date=started_at.astimezone(_BEIJING).date(),
    )
    query = _query_contract(clean_code, start, end, bounded_limit)
    cache_path = Path(last_good_path) if last_good_path is not None else None
    if customer_data_dir is not None:
        cache_path = customer_cache_path(customer_data_dir, query)

    request = transport or _request_bytes
    deadline = time.monotonic() + bounded_timeout
    attempts: list[SourceAttempt] = []
    items: list[dict[str, Any]] = []
    total: int | None = None
    raw_count: int | None = None
    issues: list[str] = []
    live_error: str | None = None
    company: dict[str, str] | None = None

    try:
        keyword_payload = _json(
            request(
                KEYWORD_URL,
                label="互动易公司精确检索",
                timeout=_remaining(deadline),
                referer=REFERER_URL,
                method="POST",
                form={"keyWord": clean_code},
                params=None,
                max_bytes=_MAX_JSON_BYTES,
            ),
            "queryKeyboardInfo",
        )
        company = parse_keyword_info(keyword_payload, clean_code)
        attempts.append(SourceAttempt(
            "queryKeyboardInfo", KEYWORD_URL, "ok", None,
        ))

        question_params = {
            "_t": "1",
            "stockcode": clean_code,
            "orgId": company["org_id"],
            "pageSize": str(bounded_limit),
            "pageNum": "1",
            "keyWord": "",
            "startDay": start,
            "endDay": end,
        }
        question_payload = _json(
            request(
                QUESTION_URL,
                label="交易所互动问答",
                timeout=_remaining(deadline),
                referer=REFERER_URL,
                method="POST",
                params=question_params,
                form=None,
                max_bytes=_MAX_JSON_BYTES,
            ),
            "company/question",
        )
        completed_at = clock()
        if completed_at.tzinfo is None or completed_at.utcoffset() is None:
            raise ValueError("now_fn must return a timezone-aware datetime")
        items, total, raw_count, issues = parse_question_page(
            question_payload,
            code=clean_code,
            company_name=company["name"],
            fetched_at=completed_at,
            limit=bounded_limit,
        )
        attempts.append(SourceAttempt(
            "company/question",
            QUESTION_URL,
            "degraded" if issues else "ok",
            _reason(issues, "") if issues else None,
        ))
    except Exception as exc:  # noqa: BLE001 - boundary becomes missing/last-good
        if isinstance(exc, ValueError) and str(exc) == "now_fn must return a timezone-aware datetime":
            raise
        live_error = f"{type(exc).__name__}: {str(exc)[:180]}"
        if not attempts or attempts[-1].endpoint != "company/question":
            endpoint = "queryKeyboardInfo" if company is None else "company/question"
            source_url = KEYWORD_URL if company is None else QUESTION_URL
            attempts.append(SourceAttempt(endpoint, source_url, "unavailable", live_error))

    replayed_at = clock()
    if replayed_at.tzinfo is None or replayed_at.utcoffset() is None:
        raise ValueError("now_fn must return a timezone-aware datetime")
    replayed_at_utc = replayed_at.astimezone(timezone.utc)
    fallback_as_of = end or replayed_at.astimezone(_BEIJING).date().isoformat()

    used_last_good = False
    snapshot_age_seconds: int | None = None
    source_fetched_at: datetime | str = replayed_at_utc
    public_state: str
    evidence_state: str
    degraded_reason: str | None
    data_as_of: str | None

    if live_error is None:
        if issues:
            public_state = "degraded"
            evidence_state = "degraded"
            degraded_reason = _reason(issues, "互动问答字段未完整通过校验")
        elif raw_count == 0 and total == 0:
            public_state = "complete_empty"
            evidence_state = "complete"
            degraded_reason = None
        elif raw_count and items:
            public_state = "complete"
            evidence_state = "complete"
            degraded_reason = None
        else:
            # This covers rows=[] with absent/non-zero total, and any future
            # schema shape that parsed no trustworthy item.
            public_state = "degraded"
            evidence_state = "degraded"
            degraded_reason = "来源未返回可确认的空结果或有效问答"
        data_as_of = _as_of(items, fallback_as_of)
    else:
        cached = None
        if cache_path is not None:
            cached = _load_last_good(
                cache_path,
                code=clean_code,
                query=query,
                replayed_at=replayed_at_utc,
                max_age_seconds=max_age,
            )
        if cached is not None:
            items = list(cached["items"])
            total = cached.get("total")
            raw_count = cached.get("raw_count")
            data_as_of = str(cached["data_as_of"])
            source_fetched_at = str(cached["fetched_at"])
            snapshot_age_seconds = int(cached["snapshot_age_seconds"])
            used_last_good = True
            public_state = "last_good"
            evidence_state = "degraded"
            degraded_reason = _reason(
                [live_error, "官方互动问答当前不可用，展示精确查询的上次成功快照"],
                "展示上次成功快照",
            )
        else:
            items = []
            total = None
            raw_count = None
            data_as_of = None
            public_state = "missing"
            evidence_state = "missing"
            degraded_reason = _reason(
                [live_error] if live_error else [],
                "官方互动问答链路当前不可用",
            )

    contract = build_evidence_contract(
        content=items if evidence_state != "missing" else None,
        provider_family=PROVIDER_FAMILY,
        api_product=API_PRODUCT,
        data_as_of=data_as_of,
        fetched_at=source_fetched_at,
        parser_version=PARSER_VERSION,
        source_url=QUESTION_URL,
        evidence_level="tier_3",
        data_state=evidence_state,
        degraded_reason=degraded_reason,
    )
    result = {
        "ok": public_state != "missing",
        "code": clean_code,
        "exchange": "szse",
        "platform": "深交所互动易",
        "content_role": "交易所互动问答",
        "answer_role": "管理层回复",
        "is_formal_disclosure": False,
        "interface_type": "website_internal_endpoint",
        "query": query,
        "company": company,
        "state": public_state,
        "used_last_good": used_last_good,
        "snapshot_age_seconds": snapshot_age_seconds,
        "total": total,
        "raw_count": raw_count,
        "items": items,
        "data_as_of": data_as_of,
        "fetched_at": contract.fetched_at,
        "source_fetched_at": (
            contract.fetched_at if evidence_state != "missing" else None
        ),
        "replayed_at": (
            replayed_at_utc.isoformat(timespec="microseconds").replace("+00:00", "Z")
            if used_last_good else None
        ),
        "sources": [attempt.to_dict() for attempt in attempts],
        "degraded_reasons": [degraded_reason] if degraded_reason else [],
        "evidence": contract.to_dict(),
    }

    if (
        cache_path is not None
        and public_state in {"complete", "complete_empty"}
    ):
        try:
            _save_last_good(cache_path, {
                "parser_version": PARSER_VERSION,
                "code": clean_code,
                "query": query,
                "source_state": public_state,
                "data_as_of": data_as_of,
                "fetched_at": contract.fetched_at,
                "total": total,
                "raw_count": raw_count,
                "items": items,
                "content_hash": contract.content_hash,
            })
        except OSError as exc:
            log.info("互动问答 last-good 写入失败: %s", str(exc)[:140])
    return result


@ttl_cache(300, fail_seconds=60)
def cached_official_interactions(
    code: str,
    start_date: str,
    end_date: str,
    limit: int,
    customer_data_dir: str,
) -> dict[str, Any]:
    """五分钟单飞缓存，让页面与 AI 研究复用同一次官网查询。"""
    return fetch_official_interactions(
        code,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        customer_data_dir=customer_data_dir,
    )


__all__ = [
    "API_PRODUCT",
    "KEYWORD_URL",
    "PARSER_VERSION",
    "PROVIDER_FAMILY",
    "QUESTION_URL",
    "InteractionFetchError",
    "cached_official_interactions",
    "customer_cache_path",
    "fetch_official_interactions",
    "initialize_customer_cache",
    "parse_keyword_info",
    "parse_question_page",
]
