"""同花顺金融数据服务 REST 适配器。

只接入本系统当前明确需要的四组低频/增强能力：集合竞价、板块目录与成分、
板块历史日线、完整财务报表与扩展估值。API Key 仅从环境变量
``HITHINK_FINANCE_API_KEY`` 读取，不进入日志、异常文本或前端响应。

上游成功契约为 HTTP 200 且响应 ``code == 0``。所有业务空值保持 ``None``；
不以 0、旧值或其他行情源推断补齐。响应同时保留上游 request_id、数据时间和
本系统 UTC 抓取时点，便于后续审计，但本模块不做跨供应商一致性校验。
"""
from __future__ import annotations

import concurrent.futures
import gzip
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Iterable

from .base import _record_source, direct_https_opener, log, ttl_cache

_BASE_URL = "https://fuyao.aicubes.cn"
_API_KEY_ENV = "HITHINK_FINANCE_API_KEY"
_SOURCE = "同花顺金融数据服务"
_MODE = "hithink_finance_api"
_TIMEOUT = 8.0
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_RETRIES = 2
_OPENER = direct_https_opener()

_A_SHARE_RE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_INDEX_RE = re.compile(r"^\d{6}\.(?:SH|SZ|TI)$")
_REPORT_RE = re.compile(r"^(?:19|20)\d{2}-[1-4]$")
_DATE_RE = re.compile(r"^(?:19|20)\d{2}-\d{2}-\d{2}$")


class FinancialAPIInputError(ValueError):
    """本地参数不满足上游公开契约。"""


def configured() -> bool:
    return bool(os.environ.get(_API_KEY_ENV, "").strip())


def _key() -> str:
    return os.environ.get(_API_KEY_ENV, "").strip()


def _fetched_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_error(exc: Exception) -> str:
    """仅暴露错误类型，避免 URL、参数或 Key 进入日志和响应。"""
    return type(exc).__name__


def _read_limited(response) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > _MAX_RESPONSE_BYTES:
        raise ValueError("response_too_large")
    raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("response_too_large")
    if str(response.headers.get("Content-Encoding") or "").lower() == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("response_too_large")
    return raw


def _decode_envelope(raw: bytes) -> dict | None:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _failure(
    state: str,
    *,
    upstream_code: int | None = None,
    request_id: str | None = None,
    message: str | None = None,
    http_status: int | None = None,
) -> dict:
    return {
        "ok": False,
        "state": state,
        "source": _SOURCE,
        "fetched_at": _fetched_at(),
        "request_id": request_id,
        "upstream_code": upstream_code,
        "upstream_message": message,
        "http_status": http_status,
        "data": None,
        "missing_semantics": "null_or_missing_means_unavailable; never_zero_fill",
    }


def _state_for_code(code: int | None) -> str:
    if code in {2001, 2003}:
        return "unauthorized"
    if code == 3001:
        return "not_found"
    if code == 3002:
        return "not_ready"
    if code == 3004:
        return "unsupported"
    if code == 4001:
        return "rate_limited"
    if code is not None and 1000 <= code < 3000:
        return "invalid_request"
    return "upstream_error"


def _request_data(path: str, params: dict, *, label: str) -> dict:
    if not configured():
        return _failure("unconfigured", message=f"missing {_API_KEY_ENV}")

    query = urllib.parse.urlencode(params, doseq=True)
    url = f"{_BASE_URL}{path}?{query}"
    last_failure: dict | None = None
    for attempt in range(_MAX_RETRIES + 1):
        started = time.monotonic()
        status: int | None = None
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "X-api-key": _key(),
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "User-Agent": "Hermes-Trader/hithink-finance-v1",
                },
            )
            with _OPENER.open(request, timeout=_TIMEOUT) as response:
                status = int(getattr(response, "status", 200) or 200)
                envelope = _decode_envelope(_read_limited(response))
            if envelope is None:
                raise ValueError("invalid_json_envelope")
            upstream_code = int(envelope.get("code")) if envelope.get("code") is not None else None
            request_id = str(envelope.get("request_id") or "") or None
            message = str(envelope.get("message") or "") or None
            if status == 200 and upstream_code == 0 and isinstance(envelope.get("data"), dict):
                _record_source(label, (time.monotonic() - started) * 1000, True, _MODE, None)
                return {
                    "ok": True,
                    "state": "complete",
                    "source": _SOURCE,
                    "fetched_at": _fetched_at(),
                    "request_id": request_id,
                    "upstream_code": 0,
                    "http_status": status,
                    "data": envelope["data"],
                    "missing_semantics": "upstream null means undisclosed or unavailable; never_zero_fill",
                }

            state = _state_for_code(upstream_code)
            last_failure = _failure(
                state,
                upstream_code=upstream_code,
                request_id=request_id,
                message=message,
                http_status=status,
            )
            retryable = upstream_code == 4001 or bool(upstream_code and 5000 <= upstream_code < 6000)
            _record_source(
                label,
                (time.monotonic() - started) * 1000,
                False,
                _MODE,
                f"code_{upstream_code}" if upstream_code is not None else "invalid_envelope",
            )
            if not retryable or attempt >= _MAX_RETRIES:
                return last_failure
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0) or None
            envelope = None
            try:
                envelope = _decode_envelope(exc.read(_MAX_RESPONSE_BYTES + 1))
            except Exception:  # noqa: BLE001 - 错误体不是可信数据
                envelope = None
            upstream_code = None
            request_id = None
            message = None
            if envelope:
                try:
                    upstream_code = int(envelope.get("code")) if envelope.get("code") is not None else None
                except (TypeError, ValueError):
                    upstream_code = None
                request_id = str(envelope.get("request_id") or "") or None
                message = str(envelope.get("message") or "") or None
            last_failure = _failure(
                _state_for_code(upstream_code),
                upstream_code=upstream_code,
                request_id=request_id,
                message=message,
                http_status=status,
            )
            retryable = status in {408, 429, 500, 502, 503, 504} or upstream_code == 4001
            _record_source(
                label,
                (time.monotonic() - started) * 1000,
                False,
                _MODE,
                f"HTTP_{status}" if status else "HTTPError",
            )
            if not retryable or attempt >= _MAX_RETRIES:
                return last_failure
        except Exception as exc:  # noqa: BLE001 - 外部数据失败必须结构化降级
            reason = _safe_error(exc)
            last_failure = _failure("network_error", message=reason, http_status=status)
            _record_source(label, (time.monotonic() - started) * 1000, False, _MODE, reason)
            if attempt >= _MAX_RETRIES:
                log.info("同花顺金融 API %s 失败: %s", label, reason)
                return last_failure
        time.sleep(0.2 * (2**attempt))
    return last_failure or _failure("upstream_error")


def _public_result(result: dict, *, units: dict | None = None, semantics: str | None = None) -> dict:
    data = result.get("data")
    if not result.get("ok") or not isinstance(data, dict):
        return dict(result)
    return {
        "ok": True,
        "state": "complete",
        "source": result["source"],
        "fetched_at": result["fetched_at"],
        "request_id": result.get("request_id"),
        "upstream_code": 0,
        "data_timestamp_ms": data.get("timestamp"),
        "timestamp_semantics": semantics or "upstream_data_ready_time",
        "field_units": units or {},
        "missing_semantics": result["missing_semantics"],
        **data,
    }


def to_a_share_thscode(code: object) -> str:
    raw = str(code or "").strip().upper()
    if _A_SHARE_RE.fullmatch(raw):
        return raw
    if not re.fullmatch(r"\d{6}", raw):
        raise FinancialAPIInputError("股票代码应为 6 位数字或带 SH/SZ/BJ 后缀的 thscode")
    if raw.startswith(("4", "8", "92")):
        return f"{raw}.BJ"
    if raw.startswith(("5", "6", "9")):
        return f"{raw}.SH"
    return f"{raw}.SZ"


def _normalize_stocks(codes: Iterable[object]) -> tuple[str, ...]:
    raw = tuple(codes)
    if not 1 <= len(raw) <= 100:
        raise FinancialAPIInputError("股票代码数量应为 1 至 100")
    normalized: list[str] = []
    for code in raw:
        value = to_a_share_thscode(code)
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def normalize_index_thscode(thscode: object) -> str:
    value = str(thscode or "").strip().upper()
    if not _INDEX_RE.fullmatch(value):
        raise FinancialAPIInputError("板块/指数代码应为 6 位数字加 TI/SH/SZ 后缀")
    return value


@ttl_cache(15, fail_seconds=20, offhours_seconds=6 * 3600)
def auction_snapshot(codes: tuple[object, ...], stage: str = "final") -> dict:
    normalized = _normalize_stocks(codes)
    stage_value = str(stage or "final").strip().lower()
    if stage_value not in {"live", "final"}:
        raise FinancialAPIInputError("stage 仅支持 live 或 final")
    result = _request_data(
        "/api/a-share/auction/snapshot",
        {"thscodes": ",".join(normalized), "stage": stage_value},
        label="同花顺金融API:集合竞价",
    )
    output = _public_result(
        result,
        units={
            "auction_price": "CNY/share",
            "auction_pct": "percent",
            "auction_volume": "lot",
            "auction_amount": "CNY",
            "auction_unmatched": "upstream_native_volume",
            "auction_turnover_pct": "percent",
            "auction_yesterday_ratio_pct": "percent",
            "auction_volume_ratio": "ratio",
            "pre_close_price": "CNY/share",
            "open_price": "CNY/share",
            "last_price": "CNY/share",
            "float_market_cap": "CNY",
        },
        semantics="response_assembly_time; auction_phase and data_status define readiness",
    )
    if output.get("ok") and str(output.get("data_status") or "").lower() in {"not_ready", "suspended"}:
        output["state"] = str(output.get("data_status")).lower()
    return output


@ttl_cache(60, fail_seconds=30, offhours_seconds=6 * 3600)
def auction_benchmark(query_date: str | None = None) -> dict:
    params: dict[str, str] = {}
    if query_date:
        value = str(query_date).strip()
        if not _DATE_RE.fullmatch(value):
            raise FinancialAPIInputError("date 应为 YYYY-MM-DD")
        params["date"] = value
    return _public_result(
        _request_data(
            "/api/a-share/auction/short-term-benchmark",
            params,
            label="同花顺金融API:竞价短线基准",
        ),
        units={"auction_pct": "percent", "date_ms": "unix_ms_Asia/Shanghai_midnight"},
        semantics="response_assembly_time; explicit non-trading dates never fall back",
    )


@ttl_cache(6 * 3600, fail_seconds=60)
def sector_catalog(tag: str = "cn_concept") -> dict:
    value = str(tag or "cn_concept").strip().lower()
    if value not in {"cn_concept", "region", "tszs", "industry"}:
        raise FinancialAPIInputError("tag 仅支持 cn_concept/region/tszs/industry")
    return _public_result(
        _request_data(
            "/api/a-share-index/catalog/ths-index-list",
            {"tag": value},
            label="同花顺金融API:板块目录",
        ),
        semantics="upstream_catalog_ready_time",
    )


@ttl_cache(3600, fail_seconds=60)
def sector_constituents(thscode: str) -> dict:
    normalized = normalize_index_thscode(thscode)
    return _public_result(
        _request_data(
            "/api/a-share-index/constituents/ths-stock-list",
            {"thscode": normalized},
            label="同花顺金融API:板块当前成分",
        ),
        semantics="current_constituents_ready_time; not historical membership",
    )


@ttl_cache(6 * 3600, fail_seconds=60)
def sector_history(thscode: str, start_ms: int, end_ms: int) -> dict:
    normalized = normalize_index_thscode(thscode)
    start_value, end_value = int(start_ms), int(end_ms)
    if start_value <= 0 or end_value <= start_value:
        raise FinancialAPIInputError("start_ms/end_ms 应为递增的正毫秒时间戳")
    if end_value - start_value > int(10 * 366 * 24 * 3600 * 1000):
        raise FinancialAPIInputError("板块历史窗口不能超过 10 年")
    return _public_result(
        _request_data(
            "/api/a-share-index/prices/historical",
            {"thscode": normalized, "interval": "1d", "start": start_value, "end": end_value},
            label="同花顺金融API:板块历史日线",
        ),
        units={
            "date_ms": "unix_ms",
            "open_price": "index_point",
            "high_price": "index_point",
            "low_price": "index_point",
            "close_price": "index_point",
            "volume": "share",
            "turnover": "CNY",
        },
        semantics="latest_bar_upstream_time; index series is unadjusted",
    )


def _statement_params(
    thscode: str,
    period: str,
    limit: int | None,
    start_ms: int | None,
    end_ms: int | None,
) -> dict:
    period_value = str(period or "annual").strip().lower()
    if period_value not in {"annual", "quarterly"}:
        raise FinancialAPIInputError("period 仅支持 annual 或 quarterly")
    params: dict[str, object] = {"thscode": thscode, "period": period_value}
    range_mode = start_ms is not None or end_ms is not None
    if range_mode:
        if start_ms is None or end_ms is None:
            raise FinancialAPIInputError("区间模式必须同时提供 start_ms 和 end_ms")
        start_value, end_value = int(start_ms), int(end_ms)
        if start_value <= 0 or end_value <= start_value:
            raise FinancialAPIInputError("start_ms/end_ms 应为递增的正毫秒时间戳")
        if end_value - start_value > int(10 * 366 * 24 * 3600 * 1000):
            raise FinancialAPIInputError("财报区间不能超过 10 年")
        params.update({"start": start_value, "end": end_value})
    else:
        limit_value = 4 if limit is None else int(limit)
        if not 1 <= limit_value <= 20:
            raise FinancialAPIInputError("limit 应为 1 至 20")
        params["limit"] = limit_value
    return params


def _statement(path: str, params: dict, label: str, units: dict) -> dict:
    return _public_result(
        _request_data(path, params, label=label),
        units=units,
        semantics="statement_data_ready_time; item sorted by period_end_ms descending",
    )


@ttl_cache(6 * 3600, fail_seconds=90)
def financial_statements(
    code: str,
    period: str = "annual",
    limit: int | None = 4,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict:
    """并行读取三张合并报表；固定三请求，不做逐行 N+1。"""
    thscode = to_a_share_thscode(code)
    params = _statement_params(thscode, period, limit, start_ms, end_ms)
    common_units = {
        "period_end_ms": "unix_ms",
        "report_date_ms": "unix_ms",
        "currency": "ISO-4217",
    }
    specs = {
        "income": (
            "/api/a-share/financials/income-statements",
            "同花顺金融API:利润表",
            {**common_units, "basic_eps": "CNY/share", "all_other_amount_fields": "currency"},
        ),
        "balance": (
            "/api/a-share/financials/balance-sheets",
            "同花顺金融API:资产负债表",
            {**common_units, "all_amount_fields": "currency"},
        ),
        "cash_flow": (
            "/api/a-share/financials/cash-flow-statements",
            "同花顺金融API:现金流量表",
            {**common_units, "all_amount_fields": "currency"},
        ),
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="financial-api") as pool:
        futures = {
            name: pool.submit(_statement, path, params, label, units)
            for name, (path, label, units) in specs.items()
        }
        statements = {name: future.result() for name, future in futures.items()}
    completed = sum(bool(value.get("ok")) for value in statements.values())
    return {
        "ok": completed > 0,
        "state": "complete" if completed == 3 else ("partial" if completed else "missing"),
        "source": _SOURCE,
        "fetched_at": _fetched_at(),
        "thscode": thscode,
        "period": params["period"],
        "query": {key: value for key, value in params.items() if key != "thscode"},
        "statements": statements,
        "missing": [name for name, value in statements.items() if not value.get("ok")],
        "missing_semantics": "upstream null means not disclosed; never_zero_fill",
    }


@ttl_cache(6 * 3600, fail_seconds=90)
def financial_indicators(code: str, report: str) -> dict:
    thscode = to_a_share_thscode(code)
    report_value = str(report or "").strip()
    if not _REPORT_RE.fullmatch(report_value):
        raise FinancialAPIInputError("report 应为 YYYY-[1-4]")
    return _public_result(
        _request_data(
            "/api/a-share/financials/indicators",
            {"thscode": thscode, "report": report_value},
            label="同花顺金融API:财务指标",
        ),
        units={"value": "upstream_indicator_specific_string"},
        semantics="report_period_snapshot; abilities is an ordered array",
    )


@ttl_cache(60, fail_seconds=30, offhours_seconds=6 * 3600)
def valuation_snapshot(codes: tuple[object, ...]) -> dict:
    normalized = _normalize_stocks(codes)
    return _public_result(
        _request_data(
            "/api/a-share/valuations/snapshot",
            {"thscodes": ",".join(normalized)},
            label="同花顺金融API:扩展估值",
        ),
        units={
            "pe_ttm": "ratio",
            "pe_mrq": "ratio",
            "pb_mrq": "ratio",
            "ps_ttm": "ratio",
            "pcf_ttm": "ratio",
        },
        semantics="latest valid time among returned valuation fields, not per-field timestamp",
    )
