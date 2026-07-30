#!/usr/bin/env python3
"""把本机通达信 TQ HTTP 服务适配为项目现有的 TDX Data API v2.8 子集。

桥只监听 loopback，只暴露行情、代码表、板块关系和基础财务等只读能力。
它不会转发账户、持仓、委托、下单、撤单、公式执行或客户端控制方法。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hmac
import ipaddress
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


DEFAULT_TQ_BASE = "http://127.0.0.1:17709"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18879
MAX_REQUEST_BYTES = 64 * 1024
MAX_TQ_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_QUOTES = 300
MAX_RELATIONS = 120
MAX_QUOTE_CACHE = 2048
QUOTE_REFRESH_WORKERS = 2
QUOTE_DETAIL_BATCH = 4
DATASETS = {
    "instruments_a",
    "sectors",
    "sector_members",
    "relation",
    "stock_info",
    "pricevol",
}
TQ_READ_METHODS = {
    "get_market_snapshot",
    "get_stock_list",
    "get_sector_list",
    "get_stock_list_in_sector",
    "get_relation",
    "get_stock_info",
    "get_pricevol",
}
UNSUPPORTED_DATASETS = {"news_sentiment", "gp_one", "holders_top10"}
CN_TZ = timezone(timedelta(hours=8))


class BridgeError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)

    def payload(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message}


class TTLCache:
    def __init__(self) -> None:
        self._values: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_load(self, key: str, ttl: float, loader: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            item = self._values.get(key)
            if item and item[0] > now:
                return item[1]
        value = loader()
        with self._lock:
            self._values[key] = (now + max(0.0, ttl), value)
        return value


class TQClient:
    """有界调用官方 TQ HTTP 入口；方法名受固定白名单限制。"""

    def __init__(self, base_url: str, *, timeout: float = 12.0) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("TQ_HTTP_BASE 必须是有效的 HTTP(S) 地址")
        if not _is_loopback_host(parsed.hostname):
            raise ValueError("TQ_HTTP_BASE 必须指向本机 loopback")
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = max(1.0, min(float(timeout), 60.0))
        self._request_id = 0
        self._lock = threading.Lock()

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if method not in TQ_READ_METHODS:
            raise BridgeError(HTTPStatus.FORBIDDEN, "read_only_method_denied", "该 TQ 方法不在只读白名单")
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
        body = json.dumps(
            {"id": request_id, "method": method, "params": params or {}},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        req = urllib.request.Request(
            self.base_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(MAX_TQ_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise BridgeError(HTTPStatus.BAD_GATEWAY, "tq_http_error", f"TQ HTTP {exc.code}") from None
        except Exception as exc:
            raise BridgeError(HTTPStatus.BAD_GATEWAY, "tq_unreachable", type(exc).__name__) from None
        if len(raw) > MAX_TQ_RESPONSE_BYTES:
            raise BridgeError(HTTPStatus.BAD_GATEWAY, "tq_response_too_large", "TQ 响应超过安全上限")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BridgeError(HTTPStatus.BAD_GATEWAY, "tq_invalid_json", "TQ 返回的不是有效 JSON") from None
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise BridgeError(HTTPStatus.BAD_GATEWAY, "tq_invalid_shape", "TQ 响应缺少 result")
        error_id = str(result.get("ErrorId", "0"))
        if error_id != "0":
            raise BridgeError(HTTPStatus.BAD_GATEWAY, "tq_call_failed", f"TQ ErrorId={error_id}")
        return result.get("Value", result)


def _is_loopback_host(host: str) -> bool:
    if str(host).lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _now_cn() -> datetime:
    return datetime.now(CN_TZ)


def _as_of() -> str:
    return _now_cn().strftime("%Y%m%d%H%M%S")


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _pick(row: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _entity(value: Any, *, sector: bool = False) -> str | None:
    raw = str(value or "").strip().upper()
    if not raw:
        return None
    if "." in raw:
        code, suffix = raw.rsplit(".", 1)
        if len(code) == 6 and code.isdigit() and suffix in {"SH", "SZ", "BJ"}:
            return f"{code}.{suffix}"
        return None
    code = "".join(ch for ch in raw if ch.isdigit())
    if len(code) != 6:
        return None
    if sector or code.startswith(("880", "881")):
        suffix = "SH"
    elif code.startswith(("8", "4", "92")):
        suffix = "BJ"
    elif code.startswith(("6", "9", "5")):
        suffix = "SH"
    else:
        suffix = "SZ"
    return f"{code}.{suffix}"


def _list_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        rows = value.get("rows") or value.get("Rows")
        if isinstance(rows, list):
            return rows
        return [{"Code": key, "Name": val} for key, val in value.items()]
    return []


def _code_name(item: Any, *, sector: bool = False) -> tuple[str | None, str]:
    if isinstance(item, dict):
        code = _pick(item, "Code", "StockCode", "block_code", "SectorCode", "code")
        name = _pick(item, "Name", "StockName", "block_name", "SectorName", "name")
        return _entity(code, sector=sector), str(name or "").strip()
    raw = str(item or "").strip()
    for separator in ("|", ",", "\t"):
        if separator in raw:
            code, name = raw.split(separator, 1)
            return _entity(code, sector=sector), name.strip()
    return _entity(raw, sector=sector), ""


def _chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[start : start + size] for start in range(0, len(values), size)]


def _to_hands(value: Any) -> float | None:
    number = _float(value)
    return round(number / 100.0, 4) if number is not None and number >= 0 else None


def _to_wan_yuan(value: Any) -> float | None:
    number = _float(value)
    return round(number / 10_000.0, 4) if number is not None and number >= 0 else None


def _depth(row: dict[str, Any], side: str, kind: str) -> list[Any]:
    plural = _pick(row, f"{side}{kind}", f"{side}_{kind}")
    if isinstance(plural, list):
        return plural
    values: list[Any] = []
    for level in range(1, 11):
        value = _pick(row, f"{side}{kind}{level}", f"{side}{level}{kind}")
        if value is None:
            break
        values.append(value)
    return values


class BridgeApp:
    def __init__(self, client: TQClient, api_key: str) -> None:
        self.client = client
        self.api_key = api_key
        self.cache = TTLCache()
        self._last_quote_at: str | None = None
        self._quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._quote_inflight: dict[str, concurrent.futures.Future[dict[str, dict[str, Any]]]] = {}
        self._quote_batches: set[concurrent.futures.Future[dict[str, dict[str, Any]]]] = set()
        self._quote_lock = threading.Lock()
        self._quote_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=QUOTE_REFRESH_WORKERS,
            thread_name_prefix="tq-quote-refresh",
        )
        self._closed = False

    def close(self) -> None:
        with self._quote_lock:
            if self._closed:
                return
            self._closed = True
            futures = tuple(self._quote_batches)
        for future in futures:
            future.cancel()
        self._quote_executor.shutdown(wait=False, cancel_futures=True)

    def dispatch(self, method: str, target: str, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        if method != "GET":
            return HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read_only", "message": "兼容桥只接受 GET"}
        supplied = headers.get("x-api-key", "")
        if self.api_key and not hmac.compare_digest(supplied, self.api_key):
            return HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}
        parsed = urllib.parse.urlparse(target)
        query = {key: values[-1] for key, values in urllib.parse.parse_qs(parsed.query).items() if values}
        try:
            if parsed.path == "/v1/meta/health":
                return HTTPStatus.OK, self._health()
            if parsed.path == "/v1/realtime/status":
                return HTTPStatus.OK, self._realtime_status()
            if parsed.path == "/v1/realtime/quotes":
                return HTTPStatus.OK, self._quotes(query)
            if parsed.path == "/v1/funds/sectors/rank":
                return self._unsupported("TQ 没有与 TDX 板块资金排名等价的实时契约")
            prefix = "/v1/data/"
            if parsed.path.startswith(prefix):
                dataset = parsed.path[len(prefix) :]
                if dataset in UNSUPPORTED_DATASETS:
                    return self._unsupported(f"TQ 不提供 {dataset} 的等价只读契约")
                if dataset not in DATASETS:
                    return HTTPStatus.NOT_FOUND, {"error": "unknown_dataset"}
                return HTTPStatus.OK, self._dataset_page(dataset, query)
            return HTTPStatus.NOT_FOUND, {"error": "not_found"}
        except BridgeError as exc:
            return exc.status, exc.payload()
        except Exception as exc:
            return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "bridge_internal_error", "message": type(exc).__name__}

    @staticmethod
    def _unsupported(message: str) -> tuple[int, dict[str, Any]]:
        return HTTPStatus.NOT_IMPLEMENTED, {"error": "unsupported_by_tq_bridge", "message": message, "missing": True}

    def _health(self) -> dict[str, Any]:
        connected = True
        try:
            self.cache.get_or_load(
                "health",
                10,
                lambda: self.client.call("get_market_snapshot", {"stock_code": "000001.SH", "field_list": []}),
            )
        except BridgeError:
            connected = False
        state = "available" if connected else "unavailable"
        return {
            "ok": connected,
            "service": "tq-tdx-readonly-bridge",
            "items": [
                {"dataset": name, "status": state}
                for name in ("pricevol", "sectors", "sector_members", "relation")
            ],
            "market_data": {"lagging": None},
        }

    def _realtime_status(self) -> dict[str, Any]:
        return {
            "status": "available" if self._last_quote_at else "idle",
            "count": 1 if self._last_quote_at else 0,
            "feed_age_seconds": 0 if self._last_quote_at else None,
            "last_push_at": self._last_quote_at,
        }

    def _quotes(self, query: dict[str, str]) -> dict[str, Any]:
        requested = [item.strip() for item in query.get("codes", "").split(",") if item.strip()]
        codes = list(dict.fromkeys(filter(None, (_entity(item) for item in requested))))
        if not codes:
            raise BridgeError(HTTPStatus.BAD_REQUEST, "codes_required", "codes 不能为空")
        if len(codes) > MAX_QUOTES:
            raise BridgeError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_many_codes", f"单次最多 {MAX_QUOTES} 只")
        max_age = self._quote_query_int(query, "max_age", default=15, minimum=1, maximum=120)
        wait = self._quote_query_int(query, "wait", default=2, minimum=0, maximum=5)

        now = time.monotonic()
        refresh_after = max(0.5, max_age / 2.0)
        with self._quote_lock:
            refresh_codes = [
                code
                for code in codes
                if (
                    code not in self._quote_cache
                    or now - self._quote_cache[code][0] >= refresh_after
                )
            ]
        futures = self._schedule_quote_refresh(refresh_codes)
        completed: dict[str, dict[str, Any]] = {}
        if wait and futures:
            # wait 是整次 HTTP 请求的总预算，不是每只股票各等待一次。
            done, _pending = concurrent.futures.wait(futures, timeout=float(wait))
            for future in done:
                try:
                    completed.update(future.result())
                except Exception:
                    continue
        else:
            # Future 的完成通知早于 done callback 执行；直接采用已完成结果，
            # 避免极窄窗口内误报 pending，缓存仍由 callback 统一落入。
            for future in futures:
                if not future.done():
                    continue
                try:
                    completed.update(future.result())
                except Exception:
                    continue

        rows: list[dict[str, Any]] = []
        valid_count = 0
        now = time.monotonic()
        with self._quote_lock:
            cached = {code: self._quote_cache.get(code) for code in codes}
        for code in codes:
            item = cached.get(code)
            if code in completed:
                item = (now, completed[code])
            if item is None:
                rows.append({"code": code, "pending": True})
                continue
            observed_at, quote = item
            age = max(0.0, now - observed_at)
            if age > max_age:
                rows.append({"code": code, "pending": True, "age_seconds": round(age, 3)})
                continue
            current = dict(quote)
            current["age_seconds"] = round(age, 3)
            rows.append(current)
            valid_count += 1
        return {"quotes": rows, "count": valid_count, "source": "local_tq"}

    @staticmethod
    def _quote_query_int(
        query: dict[str, str],
        name: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            value = int(query.get(name, str(default)))
        except (TypeError, ValueError):
            raise BridgeError(HTTPStatus.BAD_REQUEST, "invalid_quote_query", f"{name} 必须是整数") from None
        return max(minimum, min(value, maximum))

    def _schedule_quote_refresh(
        self,
        codes: list[str],
    ) -> set[concurrent.futures.Future[dict[str, dict[str, Any]]]]:
        if not codes:
            return set()
        with self._quote_lock:
            existing = {self._quote_inflight[code] for code in codes if code in self._quote_inflight}
            pending = [code for code in codes if code not in self._quote_inflight]
            # ThreadPoolExecutor 的内部队列无界；只允许 worker 数量以内的批次，
            # 拥堵时本次返回 pending，由腾讯等既有备源接管。
            if self._closed or not pending or len(self._quote_batches) >= QUOTE_REFRESH_WORKERS:
                return existing
            future = self._quote_executor.submit(self._load_quote_batch, tuple(pending))
            self._quote_batches.add(future)
            for code in pending:
                self._quote_inflight[code] = future
        future.add_done_callback(self._finish_quote_batch)
        existing.add(future)
        return existing

    def _load_quote_batch(self, codes: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        if len(codes) > QUOTE_DETAIL_BATCH:
            # TQ 为批量价量提供 get_pricevol；大批量不能逐只串行调用快照。
            try:
                value = self.client.call("get_pricevol", {"stock_list": list(codes)})
            except BridgeError:
                return rows
            if isinstance(value, dict):
                for raw_code, item in value.items():
                    code = _entity(raw_code)
                    if code in codes and isinstance(item, dict):
                        normalized = self._quote_row(code, item)
                        if normalized:
                            rows[code] = normalized
            return rows

        for code in codes:
            try:
                value = self.client.call("get_market_snapshot", {"stock_code": code, "field_list": []})
            except BridgeError:
                continue
            row = value.get(code) if isinstance(value, dict) and isinstance(value.get(code), dict) else value
            if isinstance(row, dict):
                normalized = self._quote_row(code, row)
                if normalized:
                    rows[code] = normalized
        return rows

    def _finish_quote_batch(
        self,
        future: concurrent.futures.Future[dict[str, dict[str, Any]]],
    ) -> None:
        try:
            rows = future.result()
        except Exception:
            rows = {}
        observed_at = time.monotonic()
        with self._quote_lock:
            codes = [code for code, current in self._quote_inflight.items() if current is future]
            for code in codes:
                self._quote_inflight.pop(code, None)
            self._quote_batches.discard(future)
            for code, quote in rows.items():
                self._quote_cache[code] = (observed_at, quote)
            if len(self._quote_cache) > MAX_QUOTE_CACHE:
                oldest = sorted(self._quote_cache, key=lambda code: self._quote_cache[code][0])
                for code in oldest[: len(self._quote_cache) - MAX_QUOTE_CACHE]:
                    self._quote_cache.pop(code, None)
            if rows:
                self._last_quote_at = _now_cn().isoformat(timespec="seconds")

    @staticmethod
    def _quote_row(code: str, row: dict[str, Any]) -> dict[str, Any] | None:
        now = _float(_pick(row, "Now", "Last", "Price", "Close"))
        if now is None or now <= 0:
            return None
        buy_prices = _depth(row, "Buy", "Price")
        sell_prices = _depth(row, "Sell", "Price")
        buy_volumes = [_to_hands(value) for value in _depth(row, "Buy", "Vol")]
        sell_volumes = [_to_hands(value) for value in _depth(row, "Sell", "Vol")]
        return {
            "code": code,
            "now": now,
            "last_close": _float(_pick(row, "LastClose", "PreClose", "PrevClose")),
            "open": _float(_pick(row, "Open")),
            "high": _float(_pick(row, "High")),
            "low": _float(_pick(row, "Low")),
            "average": _float(_pick(row, "Average", "AvgPrice")),
            "speed": _float(_pick(row, "Speed")),
            "amount": _to_wan_yuan(_pick(row, "Amount")),
            "volume": _to_hands(_pick(row, "Volume", "Vol")),
            "inside": _to_hands(_pick(row, "Inside", "InnerVol")),
            "outside": _to_hands(_pick(row, "Outside", "OuterVol")),
            "buyp": [_float(value) for value in buy_prices],
            "buyv": buy_volumes,
            "sellp": [_float(value) for value in sell_prices],
            "sellv": sell_volumes,
            "age_seconds": 0,
        }

    def _dataset_page(self, dataset: str, query: dict[str, str]) -> dict[str, Any]:
        rows = self._dataset(dataset, query)
        try:
            limit = max(1, min(int(query.get("limit", "5000")), 5000))
            offset = max(0, int(query.get("cursor", "0") or 0))
        except ValueError:
            raise BridgeError(HTTPStatus.BAD_REQUEST, "invalid_pagination", "limit/cursor 必须是整数") from None
        page = rows[offset : offset + limit]
        next_offset = offset + len(page)
        has_more = next_offset < len(rows)
        return {
            "rows": page,
            "next_cursor": str(next_offset) if has_more else "",
            "has_more": has_more,
            "count": len(page),
        }

    def _dataset(self, dataset: str, query: dict[str, str]) -> list[dict[str, Any]]:
        if dataset == "instruments_a":
            return self.cache.get_or_load(dataset, 6 * 3600, self._instruments)
        if dataset == "sectors":
            return self.cache.get_or_load(dataset, 6 * 3600, self._sectors)
        if dataset == "pricevol":
            return self.cache.get_or_load(dataset, 45, self._pricevol)
        entities = [item.strip() for item in query.get("entities", "").split(",") if item.strip()]
        if dataset == "sector_members":
            return self._sector_members(entities)
        if dataset == "relation":
            return self._relations(entities)
        if dataset == "stock_info":
            return self._stock_info(entities)
        return []

    def _instruments(self) -> list[dict[str, Any]]:
        value = self.client.call("get_stock_list", {"market": "5", "list_type": 1})
        rows: list[dict[str, Any]] = []
        as_of = _as_of()
        for item in _list_items(value):
            code, name = _code_name(item)
            if code:
                rows.append({"entity_key": code, "series_key": "latest", "as_of": as_of, "payload": {"Name": name}})
        return rows

    def _sectors(self) -> list[dict[str, Any]]:
        value = self.client.call("get_sector_list", {"list_type": 1})
        rows: list[dict[str, Any]] = []
        as_of = _as_of()
        for item in _list_items(value):
            code, name = _code_name(item, sector=True)
            if code:
                rows.append({"entity_key": code, "series_key": "latest", "as_of": as_of, "payload": {"Name": name}})
        return rows

    def _sector_members(self, entities: list[str]) -> list[dict[str, Any]]:
        sectors = list(dict.fromkeys(filter(None, (_entity(item, sector=True) for item in entities))))
        if not sectors:
            return []
        if len(sectors) > 40:
            raise BridgeError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_many_entities", "单次最多 40 个板块")
        rows: list[dict[str, Any]] = []
        as_of = _as_of()
        for sector in sectors:
            value = self.client.call(
                "get_stock_list_in_sector",
                # TQ 的系统板块查询必须保留市场后缀，例如 ``880656.SH``。
                # 只传 ``880656`` 会返回成功但空列表，导致上层误判板块无成分。
                {"block_code": sector, "block_type": 0, "list_type": 1},
            )
            for item in _list_items(value):
                stock, name = _code_name(item)
                if stock:
                    rows.append({
                        "entity_key": sector,
                        "series_key": stock,
                        "as_of": as_of,
                        "payload": {"sector_code": sector, "Code": stock, "Name": name},
                    })
        return rows

    def _relations(self, entities: list[str]) -> list[dict[str, Any]]:
        stocks = list(dict.fromkeys(filter(None, (_entity(item) for item in entities))))
        if not stocks:
            return []
        if len(stocks) > MAX_RELATIONS:
            raise BridgeError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_many_entities", f"单次最多 {MAX_RELATIONS} 只股票")
        rows: list[dict[str, Any]] = []
        as_of = _as_of()
        for stock in stocks:
            value = self.client.call("get_relation", {"stock_code": stock})
            for item in _list_items(value):
                if not isinstance(item, dict):
                    continue
                payload = dict(item)
                block_code = _entity(_pick(payload, "BlockCode", "block_code", "Code"), sector=True)
                if block_code:
                    payload["BlockCode"] = block_code
                rows.append({"entity_key": stock, "series_key": block_code or "", "as_of": as_of, "payload": payload})
        return rows

    def _stock_info(self, entities: list[str]) -> list[dict[str, Any]]:
        stocks = list(dict.fromkeys(filter(None, (_entity(item) for item in entities))))
        if len(stocks) != 1:
            raise BridgeError(HTTPStatus.BAD_REQUEST, "single_entity_required", "stock_info 每次必须且只能查询一只股票")
        stock = stocks[0]
        value = self.client.call("get_stock_info", {"stock_code": stock, "field_list": []})
        body = value.get(stock) if isinstance(value, dict) and isinstance(value.get(stock), dict) else value
        if not isinstance(body, dict):
            return []
        return [{"entity_key": stock, "series_key": "latest", "as_of": _as_of(), "payload": body}]

    def _pricevol(self) -> list[dict[str, Any]]:
        instruments = self._dataset("instruments_a", {})
        stocks = [str(row.get("entity_key")) for row in instruments if row.get("entity_key")]
        rows: list[dict[str, Any]] = []
        as_of = _as_of()
        for group in _chunked(stocks, 500):
            value = self.client.call("get_pricevol", {"stock_list": group})
            if isinstance(value, dict):
                items = value.items()
            else:
                items = []
            for raw_code, item in items:
                if not isinstance(item, dict):
                    continue
                code = _entity(raw_code)
                if not code:
                    continue
                payload = dict(item)
                volume = _pick(payload, "Volume", "Vol")
                if volume is not None:
                    payload["Volume"] = _to_hands(volume)
                now = _pick(payload, "Now", "Price", "Close")
                previous = _pick(payload, "LastClose", "PreClose", "PrevClose")
                if now is not None:
                    payload["Now"] = _float(now)
                if previous is not None:
                    payload["LastClose"] = _float(previous)
                rows.append({"entity_key": code, "series_key": "latest", "as_of": as_of, "payload": payload})
        return rows


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "TQTDXBridge/1.0"

    def _handle(self) -> None:
        app: BridgeApp = self.server.app  # type: ignore[attr-defined]
        headers = {str(key).lower(): str(value) for key, value in self.headers.items()}
        status, payload = app.dispatch(self.command, self.path, headers)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # 客户端已超时或主动取消；响应内容不能再送达，不应打印线程堆栈。
            return

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: BridgeApp) -> None:
        super().__init__(address, BridgeHandler)
        self.app = app

    def server_close(self) -> None:
        self.app.close()
        super().server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地 TQ → TDX API v2.8 只读兼容桥")
    parser.add_argument("--tq-base", default=os.environ.get("TQ_HTTP_BASE", DEFAULT_TQ_BASE))
    parser.add_argument("--host", default=os.environ.get("TQ_BRIDGE_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TQ_BRIDGE_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--api-key", default=os.environ.get("TQ_BRIDGE_API_KEY") or os.environ.get("TDXAPI_KEY", ""))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("TQ_HTTP_TIMEOUT", "12")))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not _is_loopback_host(args.host):
        raise SystemExit("安全限制：TQ_BRIDGE_HOST 只能是 loopback 地址")
    if not (1 <= args.port <= 65535):
        raise SystemExit("TQ_BRIDGE_PORT 超出有效范围")
    if not args.api_key:
        raise SystemExit("请设置 TQ_BRIDGE_API_KEY 或 TDXAPI_KEY；兼容桥不允许无鉴权启动")
    app = BridgeApp(TQClient(args.tq_base, timeout=args.timeout), args.api_key)
    server = BridgeServer((args.host, args.port), app)
    print(f"TQ 只读兼容桥监听 http://{args.host}:{args.port}，上游 {args.tq_base}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
