"""财联社 7×24 电报的独立发布方适配器。

这条链路直接读取 CLS 官网 v1 滚动列表，签名是官网客户端使用的
本地摘要算法，不需要密钥。它仍是网页内部 adapter，没有对外 SLA；
因此产品可以说“财联社独立发布链路”，但不能说“稳定开放 API”。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from .base import _record_source, direct_https_opener, log, ttl_cache
from .evidence_contract import build_evidence_contract, compute_content_hash, normalize_source_url


CLS_ROLL_URL = "https://www.cls.cn/v1/roll/get_roll_list"
PARSER_VERSION = "cls-roll-v1"
_BEIJING = ZoneInfo("Asia/Shanghai")
_MAX_BYTES = 4 * 1024 * 1024
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/126 Safari/537.36"
)
_ALLOWED_LINK_HOSTS = frozenset({"www.cls.cn", "cls.cn", "api3.cls.cn"})
_LAST_GOOD_MAX_AGE = timedelta(hours=6)


class ClsNewsFetchError(RuntimeError):
    """财联社链路未能返回可验证的结构化响应。"""


Transport = Callable[..., bytes]


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def signed_params(limit: int) -> dict[str, str]:
    """生成不含密钥的 CLS 官网查询参数。"""
    params = {
        "appName": "CailianpressWeb",
        "os": "web",
        "sv": "7.7.5",
        "last_time": "",
        "refresh_type": "1",
        "rn": str(max(1, min(int(limit), 100))),
    }
    canonical = "&".join(f"{key}={params[key]}" for key in sorted(params))
    sha1 = hashlib.sha1(canonical.encode("utf-8")).hexdigest()  # noqa: S324 - upstream protocol
    params["sign"] = hashlib.md5(sha1.encode("ascii")).hexdigest()  # noqa: S324 - upstream protocol
    return params


def _request_bytes(
    url: str,
    *,
    params: dict[str, str],
    timeout: float,
    max_bytes: int = _MAX_BYTES,
) -> bytes:
    started = time.monotonic()
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "User-Agent": _UA,
            "Referer": "https://www.cls.cn/",
            "Accept": "application/json, text/plain, */*",
        },
    )
    opener = direct_https_opener()
    deadline = started + max(0.2, float(timeout))
    try:
        chunks: list[bytes] = []
        size = 0
        with opener.open(request, timeout=min(4.0, max(0.2, float(timeout)))) as response:
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > max_bytes:
                raise ClsNewsFetchError(f"response too large: {declared}")
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"total wall timeout after {timeout:.1f}s")
                chunk = response.read(8192)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ClsNewsFetchError(f"response exceeded {max_bytes} bytes")
                chunks.append(chunk)
        raw = b"".join(chunks)
        _record_source("财联社电报", (time.monotonic() - started) * 1000, True, "official-direct", None)
        return raw
    except Exception as exc:  # noqa: BLE001 - source boundary degrades explicitly
        reason = f"{type(exc).__name__}: {str(exc)[:160]}"
        _record_source("财联社电报", (time.monotonic() - started) * 1000, False, "official-direct", reason)
        raise ClsNewsFetchError(reason) from exc


def _clean(value: object, limit: int) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _source_url(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_LINK_HOSTS:
            return None
        return normalize_source_url(raw)
    except ValueError:
        return None


def parse_cls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """归一化 CLS 响应；字段漂移不能被解释成空新闻。"""
    if str(payload.get("errno")) not in {"0", "None"}:
        raise ClsNewsFetchError(f"CLS errno={str(payload.get('errno'))[:40]}")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("roll_data"), list):
        raise ClsNewsFetchError("CLS roll_data field is missing or not a list")
    rows: list[dict[str, Any]] = []
    for item in data["roll_data"]:
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title") or item.get("brief"), 180)
        content = _clean(item.get("content") or item.get("brief"), 800)
        try:
            published = datetime.fromtimestamp(float(item.get("ctime")), tz=timezone.utc).astimezone(_BEIJING)
        except (TypeError, ValueError, OSError):
            continue
        if not title and not content:
            continue
        normalized = {
            "id": str(item.get("id") or "").strip() or None,
            "title": title or content[:180],
            "content": content or title,
            "published_at": published.isoformat(timespec="seconds"),
            "published_at_precision": "timestamp",
            "source_url": _source_url(item.get("shareurl") or item.get("assocArticleUrl")),
            "publisher_name": "财联社",
            "provider_family": "cls",
            "api_product": "v1-roll-get_roll_list",
            "source_relation": "original_publisher",
            "access_path": "official_website_internal_adapter",
            "evidence_level": "tier_2",
        }
        normalized["content_hash"] = compute_content_hash({
            key: normalized[key]
            for key in ("id", "title", "content", "published_at", "source_url")
        })
        rows.append(normalized)
    return rows


def _save_last_good(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
            tmp = Path(handle.name)
        tmp.chmod(0o600)
        tmp.replace(path)
        path.chmod(0o600)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _load_last_good(
    path: Path,
    *,
    query_contract: dict[str, int],
    now: datetime,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("items"), list)
        or payload.get("query_contract") != query_contract
    ):
        return None
    try:
        source_fetched_at = datetime.fromisoformat(str(payload.get("fetched_at") or ""))
    except ValueError:
        return None
    if source_fetched_at.tzinfo is None or source_fetched_at.utcoffset() is None:
        return None
    age = now.astimezone(timezone.utc) - source_fetched_at.astimezone(timezone.utc)
    if age < timedelta(0) or age > _LAST_GOOD_MAX_AGE:
        return None
    return payload


def fetch_cls_telegraph(
    *,
    limit: int = 50,
    timeout: float = 7.0,
    last_good_path: str | Path | None = None,
    transport: Transport | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """返回财联社电报与完整证据状态。"""
    bounded = max(1, min(int(limit), 100))
    now = (now_fn or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now_fn must return a timezone-aware datetime")
    fetched_at = now.astimezone(timezone.utc)
    request = transport or _request_bytes
    cache_path = Path(last_good_path) if last_good_path else None
    query_contract = {"limit": bounded}
    used_last_good = False
    degraded_reason = None
    source_fetched_at = fetched_at

    try:
        raw = request(CLS_ROLL_URL, params=signed_params(bounded), timeout=timeout)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ClsNewsFetchError("CLS returned non-JSON content") from exc
        if not isinstance(payload, dict):
            raise ClsNewsFetchError("CLS returned an invalid JSON root")
        parsed_items = parse_cls(payload)
        raw_rows = payload["data"]["roll_data"]
        if raw_rows and not parsed_items:
            raise ClsNewsFetchError("CLS returned non-empty rows but none matched the parser schema")
        malformed_count = len(raw_rows) - len(parsed_items)
        items = parsed_items[:bounded]
        state = "degraded" if malformed_count else "complete"
        result_state = (
            "partial_schema"
            if malformed_count
            else "complete" if items else "complete_empty"
        )
        if malformed_count:
            degraded_reason = f"财联社返回 {len(raw_rows)} 条，其中 {malformed_count} 条字段不完整"
        data_as_of: str | datetime = items[0]["published_at"] if items else fetched_at.astimezone(_BEIJING).date()
        # “成功但空”不是 last-good，不能把已有非空快讯快照覆盖掉。
        if cache_path and state == "complete" and items:
            try:
                _save_last_good(cache_path, {
                    "fetched_at": _utc_text(fetched_at),
                    "data_as_of": str(data_as_of),
                    "query_contract": query_contract,
                    "items": items,
                })
            except OSError as exc:
                log.info("财联社 last-good 写入失败: %s", str(exc)[:140])
    except Exception as exc:  # noqa: BLE001 - independent source failure is structured
        cached = (
            _load_last_good(
                cache_path,
                query_contract=query_contract,
                now=fetched_at,
            )
            if cache_path else None
        )
        if cached:
            items = list(cached["items"])
            state = "degraded"
            result_state = "last_good"
            used_last_good = True
            data_as_of = cached.get("data_as_of") or None
            source_fetched_at = datetime.fromisoformat(str(cached["fetched_at"]))
            degraded_reason = f"财联社官网当前请求失败，展示上次成功快照：{type(exc).__name__}"
        else:
            items = []
            state = "missing"
            result_state = "unavailable"
            data_as_of = None
            degraded_reason = f"财联社官网当前请求失败：{type(exc).__name__}"

    contract = build_evidence_contract(
        content=items if state != "missing" else None,
        provider_family="cls",
        api_product="v1-roll-get_roll_list",
        data_as_of=data_as_of,
        fetched_at=fetched_at,
        parser_version=PARSER_VERSION,
        source_url=CLS_ROLL_URL,
        evidence_level="tier_2",
        data_state=state,
        degraded_reason=degraded_reason,
    )
    return {
        "ok": state != "missing",
        "state": state,
        "result_state": result_state,
        "used_last_good": used_last_good,
        "items": items,
        "data_as_of": contract.data_as_of,
        "fetched_at": contract.fetched_at,
        "source_fetched_at": _utc_text(source_fetched_at),
        "replayed_at": contract.fetched_at if used_last_good else None,
        "degraded_reasons": [degraded_reason] if degraded_reason else [],
        "evidence": contract.to_dict(),
    }


@ttl_cache(30, fail_seconds=60)
def cached_cls_telegraph(
    limit: int = 50,
    last_good_path: str | None = None,
) -> dict[str, Any]:
    """30 秒单飞缓存，与新闻页刷新频率对齐。"""
    return fetch_cls_telegraph(limit=limit, last_good_path=last_good_path)
