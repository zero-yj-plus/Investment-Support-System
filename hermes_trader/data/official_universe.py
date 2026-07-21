"""沪深京交易所官网公开股票列表。

仅请求交易所官网，不需要 token / API key，也不经过 AkShare 的
``stock_info_a_code_name`` 聚合函数。每个交易所是独立的有界请求：一家
失败时，另外两家仍可以返回部分真实股票池。
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from io import BytesIO
import http.cookiejar
import json
import re
import time
import urllib.parse
import urllib.request
import warnings

from .base import _record_source, direct_https_opener, log, now_cn


SSE_DOWNLOAD_URL = "https://query.sse.com.cn/security/stock/downloadStockListFile.do"
SZSE_DOWNLOAD_URL = "https://www.szse.cn/api/report/ShowReport"
BSE_LIST_URL = "https://www.bse.cn/nqxxController/nqxxCnzq.do"

# 供审计/测试检查：全部为交易所官方域名，参数不含凭据。
OFFICIAL_ENDPOINTS = {
    "sse": SSE_DOWNLOAD_URL,
    "szse": SZSE_DOWNLOAD_URL,
    "bse": BSE_LIST_URL,
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/126 Safari/537.36"
)
_COOKIE_JAR = http.cookiejar.CookieJar()
_OPENER = direct_https_opener(urllib.request.HTTPCookieProcessor(_COOKIE_JAR))


@dataclass(frozen=True)
class ExchangeUniverse:
    exchange: str
    names: dict[str, str]
    source_url: str
    fetched_at: str
    elapsed_ms: int
    complete: bool
    error: str | None = None


def _request_bytes(
    url: str,
    *,
    label: str,
    timeout: float,
    params: dict[str, str] | None = None,
    form: list[tuple[str, str]] | None = None,
    referer: str,
    max_bytes: int = 5 * 1024 * 1024,
) -> bytes | None:
    """直接请求官网，同时约束「绝对墙钟」和最大响应体。

    ``urllib timeout`` 只限制单次 socket 无活动；若对端持续以 10KB/s
    滴流，一次 ``read()`` 仍可卡一分钟。因此小块读取并每块检查 monotonic
    deadline，超时立即 close，不在后台留下慢下载线程。
    """
    started = time.monotonic()
    deadline = started + max(0.2, timeout)
    query = urllib.parse.urlencode(params or {})
    target = f"{url}?{query}" if query else url
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    try:
        req = urllib.request.Request(
            target,
            data=body,
            headers={"User-Agent": _UA, "Referer": referer, "Accept": "*/*"},
        )
        chunks: list[bytes] = []
        size = 0
        # 连接/单次无活动也不超过 3s；总墙钟由 deadline 控制。
        with _OPENER.open(req, timeout=min(3.0, max(0.2, timeout))) as response:
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > max_bytes:
                raise ValueError(f"response too large: {declared}")
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"total wall timeout after {timeout:.1f}s")
                chunk = response.read(1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"response exceeded {max_bytes} bytes")
                chunks.append(chunk)
        raw = b"".join(chunks)
        _record_source(label, (time.monotonic() - started) * 1000, True, "official-direct", None)
        return raw
    except Exception as exc:  # noqa: BLE001 - 外部官网必须可降级
        reason = str(exc)[:140]
        _record_source(
            label, (time.monotonic() - started) * 1000, False, "official-direct", reason
        )
        log.info("股票代码表 %s 不可用: %s", label, reason)
        return None


def _clean(names: dict[str, str], exchange: str) -> dict[str, str]:
    prefixes = {
        "sse": ("6",),
        "szse": ("0", "3"),
        "bse": ("4", "8", "92"),
    }[exchange]
    out: dict[str, str] = {}
    for raw_code, raw_name in names.items():
        code = str(raw_code or "").split(".")[0].strip().zfill(6)
        name = str(raw_name or "").strip()
        if re.fullmatch(r"\d{6}", code) and code.startswith(prefixes) and name:
            out[code] = name
    return out


def _parse_sse_text(raw: bytes) -> dict[str, str]:
    text = raw.decode("gb18030", "ignore")
    out: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        columns = [item.strip() for item in line.split("\t")]
        # 官方下载列：公司代码、公司简称、代码、简称……
        if len(columns) >= 2:
            out[columns[0]] = columns[1]
    return _clean(out, "sse")


def fetch_sse(timeout: float = 6.0) -> ExchangeUniverse:
    started = time.monotonic()
    params = [
        {"csrcCode": "", "stockCode": "", "areaName": "", "stockType": stock_type}
        for stock_type in ("1", "8")  # 主板 A 股 + 科创板
    ]
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sse-universe")
    futures = {
        executor.submit(
            _request_bytes,
            SSE_DOWNLOAD_URL,
            label=f"股票代码表(上交所-{item['stockType']})",
            timeout=timeout,
            params=item,
            referer="https://www.sse.com.cn/assortment/stock/list/share/",
        )
        for item in params
    }
    names: dict[str, str] = {}
    completed = 0
    try:
        done, _ = wait(futures, timeout=timeout, return_when=FIRST_COMPLETED)
        deadline = time.monotonic() + max(0.0, timeout - (time.monotonic() - started))
        pending = futures - done
        while done:
            for future in done:
                try:
                    raw = future.result()
                    if raw:
                        parsed = _parse_sse_text(raw)
                        if parsed:
                            names.update(parsed)
                            completed += 1
                except Exception:  # noqa: BLE001
                    pass
            if not pending:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    elapsed = round((time.monotonic() - started) * 1000)
    clean = _clean(names, "sse")
    return ExchangeUniverse(
        "sse", clean, SSE_DOWNLOAD_URL, now_cn().isoformat(timespec="seconds"), elapsed,
        completed == 2 and len(clean) >= 1500,
        None if clean else "上交所列表超时或格式异常",
    )


def _parse_szse_xlsx(raw: bytes) -> dict[str, str]:
    # openpyxl 是 AkShare 安装树中的纯 Python XLSX 解析依赖；不调用 AkShare。
    from openpyxl import load_workbook  # noqa: PLC0415

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        workbook = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    sheet = workbook.active
    # 深交所 1110 官方 XLSX 的 dimension 长期固定写成 A1，但 sheetData
    # 实际含数千行。read_only 模式会因错误 dimension 只读第一格。
    if sheet.calculate_dimension() in {"A1", "A1:A1"}:
        sheet.reset_dimensions()
    code_idx = name_idx = None
    out: dict[str, str] = {}
    for row in sheet.iter_rows(values_only=True):
        normalized = [re.sub(r"\s+", "", str(value or "")) for value in row]
        if code_idx is None:
            if "A股代码" in normalized and "A股简称" in normalized:
                code_idx = normalized.index("A股代码")
                name_idx = normalized.index("A股简称")
            continue
        if code_idx >= len(row) or name_idx is None or name_idx >= len(row):
            continue
        out[str(row[code_idx] or "")] = str(row[name_idx] or "")
    workbook.close()
    return _clean(out, "szse")


def fetch_szse(timeout: float = 6.0) -> ExchangeUniverse:
    started = time.monotonic()
    raw = _request_bytes(
        SZSE_DOWNLOAD_URL,
        label="股票代码表(深交所)",
        timeout=timeout,
        params={"SHOWTYPE": "xlsx", "CATALOGID": "1110", "TABKEY": "tab1"},
        referer="https://www.szse.cn/market/product/stock/list/index.html",
    )
    names: dict[str, str] = {}
    error = None
    if raw:
        try:
            names = _parse_szse_xlsx(raw)
        except Exception as exc:  # noqa: BLE001
            error = f"XLSX 解析失败: {str(exc)[:100]}"
    if not names and error is None:
        error = "深交所列表超时或空响应"
    return ExchangeUniverse(
        "szse", names, SZSE_DOWNLOAD_URL, now_cn().isoformat(timespec="seconds"),
        round((time.monotonic() - started) * 1000), len(names) >= 2000, error,
    )


def _decode_bse(raw: bytes) -> tuple[dict[str, str], int]:
    text = raw.decode("utf-8", "ignore")
    start = text.find("[")
    if start < 0:
        return {}, 0
    payload, _ = json.JSONDecoder().raw_decode(text[start:])
    page = payload[0] if isinstance(payload, list) and payload else {}
    content = page.get("content") if isinstance(page, dict) else []
    total_pages = int(page.get("totalPages") or 0) if isinstance(page, dict) else 0
    names: dict[str, str] = {}
    for row in content if isinstance(content, list) else []:
        if isinstance(row, dict):
            code = row.get("xxzqdm") or row.get("证券代码")
            name = row.get("xxzqjc") or row.get("证券简称")
        elif isinstance(row, list) and len(row) > 40:
            code, name = row[38], row[40]
        else:
            continue
        names[str(code or "")] = str(name or "")
    return _clean(names, "bse"), total_pages


def _bse_page(page: int, timeout: float) -> tuple[dict[str, str], int]:
    raw = _request_bytes(
        BSE_LIST_URL,
        label="股票代码表(北交所)",
        timeout=timeout,
        form=[
            ("page", str(page)), ("typejb", "T"), ("xxfcbj[]", "2"),
            ("xxzqdm", ""), ("sortfield", "xxzqdm"), ("sorttype", "asc"),
        ],
        referer="https://www.bse.cn/nq/listedcompany.html",
    )
    return _decode_bse(raw) if raw else ({}, 0)


def fetch_bse(timeout: float = 6.0) -> ExchangeUniverse:
    started = time.monotonic()
    # 北交所 WAF 首次会 307 到同 URL 并通过 Set-Cookie 下发 C3VK；
    # 先访问官网列表页预热 CookieJar，否则 POST 会无限 307。
    _request_bytes(
        "https://www.bse.cn/nq/listedcompany.html",
        label="股票代码表(北交所-cookie)",
        timeout=min(1.5, timeout),
        referer="https://www.bse.cn/",
        max_bytes=1024 * 1024,
    )
    first, total_pages = _bse_page(0, min(timeout, 3.0))
    names = dict(first)
    completed_pages = 1 if first else 0
    total_pages = max(1, min(total_pages, 64))
    if total_pages > 1 and time.monotonic() - started < timeout:
        executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="bse-universe")
        futures = {
            executor.submit(_bse_page, page, min(2.5, timeout)): page
            for page in range(1, total_pages)
        }
        try:
            done, _ = wait(
                futures,
                timeout=max(0.0, timeout - (time.monotonic() - started)),
            )
            for future in done:
                try:
                    page_names, _ = future.result()
                    if page_names:
                        names.update(page_names)
                        completed_pages += 1
                except Exception:  # noqa: BLE001
                    pass
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    complete = completed_pages == total_pages and len(names) >= 50
    return ExchangeUniverse(
        "bse", _clean(names, "bse"), BSE_LIST_URL,
        now_cn().isoformat(timespec="seconds"),
        round((time.monotonic() - started) * 1000), complete,
        None if names else "北交所列表超时或空响应",
    )


def fetch_exchange(exchange: str, timeout: float = 6.0) -> ExchangeUniverse:
    return {"sse": fetch_sse, "szse": fetch_szse, "bse": fetch_bse}[exchange](timeout)
