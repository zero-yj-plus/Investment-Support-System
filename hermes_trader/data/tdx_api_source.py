"""TDX 数据 API v2.8 适配器。

正式热路径使用：

* ``/v1/realtime/quotes``：指数、自选、持仓的秒级价量/盘口；
* ``pricevol``：全市场 60 秒快照，用于市场/板块涨跌家数；
* ``instruments_a``：代码→名称本地缓存；
* ``sectors`` / ``sector_members`` / ``relation``：板块目录、成分股与个股板块关系。
* ``/v1/funds/sectors/rank``：板块资金排名（原始单位未核验）。

不使用已停更的 ``snapshot``，不消费 ``Zjl/Zjl_HB`` 或 GP/BK/SC 编码
资金流字段。所有返回统一到项目单位：价格=元、成交量=股、成交额=元。

凭据只从环境变量 ``TDXAPI_BASE`` / ``TDXAPI_KEY`` 读取，从不返回到前端、
日志或异常文本。
"""
from __future__ import annotations

import gzip
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterable

from .base import _record_source, direct_https_opener, log, to_float, ts_str, ttl_cache

_TIMEOUT = 12
_MAX_HOT_CODES = 400
_QUOTE_BATCH = 300
_PAGE_SIZE = 5000
_OPENER = direct_https_opener()
_CURSOR_RESTARTS = 2


def _base() -> str:
    return os.environ.get("TDXAPI_BASE", "").strip().rstrip("/")


def _key() -> str:
    return os.environ.get("TDXAPI_KEY", "").strip()


def configured() -> bool:
    return bool(_base() and _key())


def _safe_error(exc: Exception) -> str:
    """错误只记类型，避免 HTTPError/URL 把内网地址或查询内容透出。"""
    return type(exc).__name__


def _request_json_result(
    path: str,
    params: dict | None = None,
    *,
    label: str,
    timeout: float = _TIMEOUT,
) -> tuple[dict | None, int | None]:
    """请求 API 并保留 HTTP 状态码。

    v2.8 cursor 在翻页期间遇到数据集发布会返回 409。普通
    调用仍只需 ``_request_json``；分页器则用这个内部结果重启整次查询。
    """
    if not configured():
        return None, None
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{_base()}{path}" + (f"?{query}" if query else "")
    started = time.monotonic()
    try:
        req = urllib.request.Request(
            url,
            headers={
                "X-API-Key": _key(),
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "User-Agent": "Hermes-Trader/tdx-api-v2.8",
            },
        )
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read()
            if str(resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
        payload = json.loads(raw.decode("utf-8"))
        ok = isinstance(payload, dict)
        _record_source(label, (time.monotonic() - started) * 1000, ok, "tdx_api", None if ok else "invalid-json-shape")
        return (payload if ok else None), int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0) or None
        reason = f"HTTP_{status}" if status else _safe_error(exc)
        _record_source(label, (time.monotonic() - started) * 1000, False, "tdx_api", reason)
        log.info("TDX API %s 失败: %s", label, reason)
        return None, status
    except Exception as exc:  # noqa: BLE001 - 统一降级，不让外部数据源拖垮产品
        reason = _safe_error(exc)
        _record_source(label, (time.monotonic() - started) * 1000, False, "tdx_api", reason)
        log.info("TDX API %s 失败: %s", label, reason)
        return None, None


def _request_json(path: str, params: dict | None = None, *, label: str, timeout: float = _TIMEOUT):
    payload, _status = _request_json_result(path, params, label=label, timeout=timeout)
    return payload


def _dataset_rows(
    dataset: str,
    params: dict | None = None,
    *,
    label: str,
    timeout: int = 20,
    max_rows: int = 200_000,
) -> list[dict] | None:
    """cursor 分页读取数据集；409 时从首页重启，绝不混用两个版本。"""
    base_params = dict(params or {})
    base_params.pop("offset", None)
    base_params.pop("cursor", None)
    base_params["limit"] = min(_PAGE_SIZE, int(base_params.get("limit") or _PAGE_SIZE))
    for restart in range(_CURSOR_RESTARTS + 1):
        rows: list[dict] = []
        cursor = ""
        seen_cursors: set[str] = set()
        expected_total: int | None = None
        while True:
            if cursor in seen_cursors:
                log.warning("TDX API %s 返回重复 cursor，拒绝使用可能循环/重复的数据", dataset)
                return None
            seen_cursors.add(cursor)
            query = dict(base_params)
            if cursor:
                query["cursor"] = cursor
            payload, status = _request_json_result(
                f"/v1/data/{dataset}", query, label=label, timeout=timeout
            )
            if status == 409:
                log.info(
                    "TDX API %s cursor 期间数据已发布，从首页重查(%s/%s)",
                    dataset,
                    restart + 1,
                    _CURSOR_RESTARTS + 1,
                )
                break
            batch = payload.get("rows") if isinstance(payload, dict) else None
            if not isinstance(batch, list):
                return None
            normalized = [row for row in batch if isinstance(row, dict)]
            declared_count = (payload or {}).get("count")
            if declared_count is not None:
                try:
                    if int(declared_count) != len(batch):
                        log.warning("TDX API %s 页内 count 与 rows 不一致", dataset)
                        return None
                except (TypeError, ValueError):
                    log.warning("TDX API %s 页内 count 非整数", dataset)
                    return None
            declared_total = (payload or {}).get("total")
            if declared_total is not None:
                try:
                    one_total = int(declared_total)
                except (TypeError, ValueError):
                    log.warning("TDX API %s total 非整数", dataset)
                    return None
                if one_total < 0 or one_total > max_rows:
                    log.warning("TDX API %s total 超出安全范围", dataset)
                    return None
                if expected_total is None:
                    expected_total = one_total
                elif expected_total != one_total:
                    log.warning("TDX API %s 分页期间 total 发生变化", dataset)
                    return None
            if len(rows) + len(normalized) > max_rows:
                log.warning("TDX API %s 超过安全上限 %s 行，拒绝使用截断结果", dataset, max_rows)
                return None
            rows.extend(normalized)
            next_cursor = str((payload or {}).get("next_cursor") or "").strip()
            has_more = (payload or {}).get("has_more")
            if next_cursor:
                cursor = next_cursor
                continue
            if has_more is True:
                # v2.8 声明有后页却没给 cursor，这是契约错误，不回退
                # offset 以免静默重复/跳行。
                log.warning("TDX API %s 返回 has_more 但缺 next_cursor", dataset)
                return None
            if expected_total is not None and len(rows) != expected_total:
                log.warning(
                    "TDX API %s 终页数量 %s 与 total %s 不一致",
                    dataset, len(rows), expected_total,
                )
                return None
            return rows
    log.warning("TDX API %s cursor 连续冲突，拒绝返回混合版本结果", dataset)
    return None


def _suffix_code(code: str) -> str | None:
    """项目代码/TDX entity → ``000001.SZ``。"""
    raw = str(code or "").strip()
    if not raw:
        return None
    upper = raw.upper()
    if len(upper) == 9 and upper[6] == "." and upper[-2:] in {"SH", "SZ", "BJ"}:
        return upper
    lower = raw.lower()
    if len(lower) == 8 and lower[:2] in {"sh", "sz", "bj"} and lower[2:].isdigit():
        return f"{lower[2:]}.{lower[:2].upper()}"
    digits = raw.split(".", 1)[0].strip().zfill(6)
    if len(digits) != 6 or not digits.isdigit():
        return None
    if digits.startswith(("8", "4", "92")):
        market = "BJ"
    elif digits.startswith(("6", "9", "5")):
        market = "SH"
    else:
        market = "SZ"
    return f"{digits}.{market}"


def _code6(code: str) -> str:
    suffixed = _suffix_code(code)
    return suffixed[:6] if suffixed else ""


def _positive(value):
    number = to_float(value)
    return number if number is not None and number > 0 else None


def _hands_to_shares(value):
    number = to_float(value)
    return int(round(number * 100)) if number is not None and number >= 0 else None


def _wan_to_yuan(value):
    number = to_float(value)
    return round(number * 10_000, 2) if number is not None and number >= 0 else None


def _depth_side(prices, volumes) -> tuple[list[float], list[int]]:
    """按上游真实档位数返回对齐的价/量数组。

    休市时可能只有一档，也可能存在0价补位；遇到0价即截断，
    不会把后面的0量误当成有效档位。
    """
    if not isinstance(prices, list) or not isinstance(volumes, list):
        return [], []
    normalized_prices: list[float] = []
    normalized_volumes: list[int] = []
    for raw_price, raw_volume in zip(prices, volumes):
        price = _positive(raw_price)
        volume = _hands_to_shares(raw_volume)
        if price is None or volume is None:
            break
        normalized_prices.append(price)
        normalized_volumes.append(volume)
    return normalized_prices, normalized_volumes


def _normalize_quote(row: dict, names: dict[str, str]) -> dict | None:
    if not isinstance(row, dict) or row.get("pending") is True:
        return None
    price = _positive(row.get("now"))
    prev = _positive(row.get("last_close"))
    if price is None:
        return None
    change_pct = round((price / prev - 1) * 100, 2) if prev else None
    if change_pct is not None and abs(change_pct) > 35:
        return None
    code = _code6(row.get("code") or "")
    buyp, buyv = _depth_side(row.get("buyp"), row.get("buyv"))
    sellp, sellv = _depth_side(row.get("sellp"), row.get("sellv"))
    return {
        "code": code,
        "name": names.get(code, ""),
        "price": price,
        "change_pct": change_pct,
        "open": _positive(row.get("open")),
        "high": _positive(row.get("high")),
        "low": _positive(row.get("low")),
        "prev_close": prev,
        "average": _positive(row.get("average")),
        "speed": to_float(row.get("speed")),
        "amount": _wan_to_yuan(row.get("amount")),
        "volume": _hands_to_shares(row.get("volume")),
        "inside": _hands_to_shares(row.get("inside")),
        "outside": _hands_to_shares(row.get("outside")),
        "buyp": buyp,
        "buyv": buyv,
        "sellp": sellp,
        "sellv": sellv,
        "age_seconds": to_float(row.get("age_seconds")),
        "source": "TDX 数据 API",
    }


@ttl_cache(6 * 3600, fail_seconds=60)
def stock_universe() -> dict[str, str] | None:
    """TDX ``instruments_a`` 代码名称表，分页拉全后缓存。"""
    if not configured():
        return None
    rows = _dataset_rows(
        "instruments_a",
        {"series": "latest", "limit": _PAGE_SIZE},
        label="TDX API 股票名称表",
        timeout=20,
        max_rows=15_000,
    )
    if rows is None:
        return None
    names: dict[str, str] = {}
    for row in rows:
        entity = str(row.get("entity_key") or "").strip().upper()
        code = _code6(entity)
        body = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        name = str(body.get("Name") or body.get("name") or "").strip()
        expected_market = (
            "BJ" if code.startswith(("4", "8", "92"))
            else ("SH" if code.startswith("6") else ("SZ" if code.startswith(("0", "3")) else ""))
        )
        actual_market = entity.rsplit(".", 1)[-1] if "." in entity else ""
        if not expected_market or actual_market != expected_market or not name:
            continue
        if code in names:
            log.warning("TDX API instruments_a 返回重复代码 %s，拒绝使用", code)
            return None
        names[code] = name
    return names if len(names) >= 3000 else None


def _iso_date(value) -> str | None:
    raw = str(value or "").replace("-", "")[:8]
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return None


@ttl_cache(6 * 3600, fail_seconds=90)
def sector_catalog() -> dict[str, dict] | None:
    """TDX 板块目录：``881xxx`` 行业、``880xxx`` 概念，其余保留为 other。"""
    if not configured():
        return None
    rows = _dataset_rows(
        "sectors",
        {"series": "latest"},
        label="TDX API 板块目录",
        timeout=20,
        max_rows=5_000,
    )
    if not rows:
        return None
    out: dict[str, dict] = {}
    for row in rows:
        entity = _suffix_code(row.get("entity_key") or "")
        body = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        name = str(body.get("Name") or body.get("name") or "").strip()
        if not entity or not name:
            continue
        code6 = _code6(entity)
        kind = "industry" if code6.startswith("881") else ("concept" if code6.startswith("880") else "other")
        out[entity] = {
            "code": entity,
            "name": name,
            "kind": kind,
            "data_date": _iso_date(row.get("as_of")),
        }
    industries = sum(1 for item in out.values() if item["kind"] == "industry")
    return out if industries >= 80 else None


@ttl_cache(6 * 3600, fail_seconds=90)
def sector_memberships() -> dict | None:
    """行业成分股双向索引：sector→stocks 与 stock→行业。

    只请求 128 个行业板块，不下载地区/风格/指数等与主线无关的成分关系。
    """
    catalog = sector_catalog() or {}
    industries = [item for item in catalog.values() if item.get("kind") == "industry"]
    if not industries:
        return None
    by_sector: dict[str, list[str]] = {item["code"]: [] for item in industries}
    by_stock: dict[str, dict] = {}
    as_of = ""
    # 控制 URL 长度，同时保证每批成分记录明显小于单页安全上限。
    for start in range(0, len(industries), 40):
        group = industries[start:start + 40]
        entities = ",".join(item["code"] for item in group)
        rows = _dataset_rows(
            "sector_members",
            {"entities": entities},
            label="TDX API 行业成分股",
            timeout=25,
            max_rows=20_000,
        )
        if rows is None:
            return None
        for row in rows:
            body = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            sector = _suffix_code(body.get("sector_code") or row.get("entity_key") or "")
            stock = _code6(body.get("Code") or row.get("series_key") or "")
            item = catalog.get(sector or "")
            if not item or item.get("kind") != "industry" or not stock:
                continue
            by_sector.setdefault(sector, []).append(stock)
            # 行业板块理论上一股一属；若上游异常重复，保留板块目录中先出现的确定值。
            by_stock.setdefault(stock, {
                "industry": item["name"],
                "sector_code": sector,
                "source": "TDX sector_members",
            })
            as_of = max(as_of, str(row.get("as_of") or ""))
    member_count = sum(len(values) for values in by_sector.values())
    if len(by_stock) < 3_000 or member_count < 3_000:
        return None
    return {
        "by_sector": {key: tuple(dict.fromkeys(values)) for key, values in by_sector.items()},
        "by_stock": by_stock,
        "stock_count": len(by_stock),
        "member_count": member_count,
        "data_date": _iso_date(as_of),
    }


@ttl_cache(6 * 3600, fail_seconds=90)
def _relation_industries_cached(entities: tuple[str, ...]) -> dict[str, dict] | None:
    if not entities:
        return {}
    rows = _dataset_rows(
        "relation",
        {"entities": ",".join(entities)},
        label="TDX API 个股行业关系",
        timeout=25,
        max_rows=max(5_000, len(entities) * 80),
    )
    if rows is None:
        return None
    out: dict[str, dict] = {}
    for row in rows:
        body = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if str(body.get("BlockType") or "").strip() != "行业":
            continue
        stock = _code6(row.get("entity_key") or "")
        industry = str(body.get("BlockName") or "").strip()
        sector = _suffix_code(body.get("BlockCode") or "")
        if stock and industry:
            out[stock] = {
                "industry": industry,
                "sector_code": sector,
                "source": "TDX relation",
            }
    return out


def industry_for_codes(codes: Iterable[str]) -> dict[str, dict]:
    """复用行业/概念关系主链，返回扁平行业映射。

    个股详情已经通过 :func:`relations_for_codes` 读取并缓存同一份 TDX
    ``relation`` 数据。风险闸若再走独立的 ``_relation_industries_cached``
    缓存，会重复发起远程请求；交互路径 1.5 秒超时后便把已验证的行业
    误判为缺失。这里统一复用完整关系缓存，再保持原有的扁平返回契约。
    """
    normalized = tuple(dict.fromkeys(filter(None, (_suffix_code(code) for code in codes))))
    out: dict[str, dict] = {}
    for start in range(0, len(normalized), 120):
        group = normalized[start:start + 120]
        relations = relations_for_codes(group)
        for code, relation in relations.items():
            industry = relation.get("industry") if isinstance(relation, dict) else None
            if isinstance(industry, dict):
                name = str(industry.get("name") or "").strip()
                sector_code = _suffix_code(industry.get("code") or "")
                source = str(industry.get("source") or relation.get("source") or "TDX relation")
            else:
                name = str(industry or "").strip()
                sector_code = _suffix_code((relation or {}).get("sector_code") or "")
                source = str((relation or {}).get("source") or "TDX relation")
            if not name:
                continue
            out[code] = {
                "industry": name,
                "sector_code": sector_code,
                "source": source,
            }
    return out


def stock_industry_map() -> dict[str, dict]:
    """全市场 code→行业映射，供主线/跌停池等一次性批量关联。"""
    return dict((sector_memberships() or {}).get("by_stock") or {})


@ttl_cache(6 * 3600, fail_seconds=90)
def _relation_blocks_cached(entities: tuple[str, ...]) -> dict[str, dict] | None:
    """个股到行业/概念的精确关系；地区、指数和风格不进入事件映射。"""
    if not entities:
        return {}
    rows = _dataset_rows(
        "relation",
        {"entities": ",".join(entities)},
        label="TDX API 个股行业概念关系",
        timeout=25,
        max_rows=max(5_000, len(entities) * 80),
    )
    if rows is None:
        return None
    out: dict[str, dict] = {}
    for row in rows:
        body = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        block_type = str(body.get("BlockType") or "").strip()
        if block_type not in {"行业", "概念"}:
            continue
        stock = _code6(row.get("entity_key") or "")
        name = str(body.get("BlockName") or "").strip()
        sector = _suffix_code(body.get("BlockCode") or "")
        if not stock or not name or not sector:
            continue
        target = out.setdefault(stock, {"industry": None, "concepts": [], "source": "TDX relation"})
        block = {"name": name, "code": sector, "source": "TDX relation"}
        if block_type == "行业":
            target["industry"] = target["industry"] or block
        elif not any(item.get("code") == sector for item in target["concepts"]):
            target["concepts"].append(block)
    return out


def relations_for_codes(codes: Iterable[str]) -> dict[str, dict]:
    """少量股票的行业+概念关系，relation 优先，行业缺失时同源成分表兜底。"""
    normalized = tuple(dict.fromkeys(filter(None, (_suffix_code(code) for code in codes))))
    out: dict[str, dict] = {}
    for start in range(0, len(normalized), 80):
        group = normalized[start:start + 80]
        found = _relation_blocks_cached(group) or {}
        out.update(found)
    # 精确 relation 已包含行业时不应再加载全市场成分表。该全表读取可能
    # 需要数秒，曾让后续 RiskGate 的 1.5 秒行业核验超时，并把已经验证
    # 的行业误判为缺失。只有确实缺行业的代码才触发同源回退。
    missing_industry = {
        _code6(entity)
        for entity in normalized
        if _code6(entity) and not (out.get(_code6(entity)) or {}).get("industry")
    }
    fallback = (
        (sector_memberships() or {}).get("by_stock") or {}
        if missing_industry else {}
    )
    for entity in normalized:
        code = _code6(entity)
        if not code:
            continue
        target = out.setdefault(code, {"industry": None, "concepts": [], "source": "TDX relation"})
        if not target.get("industry") and code in fallback:
            item = fallback[code]
            target["industry"] = {
                "name": item.get("industry"),
                "code": item.get("sector_code"),
                "source": item.get("source") or "TDX sector_members",
            }
    return out


@ttl_cache(6 * 3600, fail_seconds=90)
def basic_financial(code: str) -> dict | None:
    """Read the latest TDX ``stock_info`` snapshot without guessing FN fields.

    ``stock_info`` exposes named basic-financial fields.  We only publish the
    directly named ROE value and ratios that can be recomputed from same-unit
    operands.  Growth rates need two comparable reporting periods, so they
    stay explicitly missing instead of being inferred from the collection
    date or from undocumented ``financial`` FN numbers.
    """
    entity = _suffix_code(code)
    if not entity or not configured():
        return None
    payload = _request_json(
        "/v1/data/stock_info",
        {"entities": entity, "order": "desc", "limit": 1},
        label="TDX API 基础财务",
        timeout=12,
    )
    rows = payload.get("rows") if isinstance(payload, dict) else None
    row = rows[0] if isinstance(rows, list) and rows else None
    if not isinstance(row, dict) or _suffix_code(row.get("entity_key") or "") != entity:
        return None
    body = row.get("payload") if isinstance(row.get("payload"), dict) else {}

    roe = to_float(body.get("J_jyl"))
    revenue = to_float(body.get("J_yysy"))
    operating_cost = to_float(body.get("J_yycb"))
    gross_margin = None
    if revenue is not None and operating_cost is not None and revenue > 0 and 0 <= operating_cost <= revenue:
        gross_margin = round((revenue - operating_cost) / revenue * 100, 4)

    assets = to_float(body.get("J_zzc"))
    current_liabilities = to_float(body.get("J_ldfz"))
    long_liabilities = to_float(body.get("J_cqfz"))
    debt_to_assets = None
    liabilities = sum(v for v in (current_liabilities, long_liabilities) if v is not None)
    if assets is not None and assets > 0 and liabilities > 0:
        debt_to_assets = round(liabilities / assets * 100, 4)

    result = {
        "end_date": None,
        "data_date": _iso_date(row.get("as_of") or row.get("series_key")),
        "roe": roe,
        "grossprofit_margin": gross_margin,
        "netprofit_yoy": None,
        "or_yoy": None,
        "debt_to_assets": debt_to_assets,
        "eps": to_float(body.get("J_mgsy")),
        "book_value_per_share": to_float(body.get("J_mgjzc")),
        "source": "TDX stock_info",
        "missing": ["净利润同比", "营收同比"],
    }
    useful = ("roe", "grossprofit_margin", "debt_to_assets", "eps", "book_value_per_share")
    return result if any(result.get(key) is not None for key in useful) else None


@ttl_cache(6 * 3600, fail_seconds=90)
def _sector_members_for_codes_cached(entities: tuple[str, ...]) -> dict | None:
    """按需读取少量行业/概念板块成员，避免建立全量概念多对多索引。"""
    if not entities:
        return {"by_sector": {}, "names": {}, "member_count": 0, "data_date": None}
    by_sector: dict[str, list[str]] = {entity: [] for entity in entities}
    names: dict[str, str] = {}
    as_of = ""
    for start in range(0, len(entities), 30):
        group = entities[start:start + 30]
        rows = _dataset_rows(
            "sector_members",
            {"entities": ",".join(group)},
            label="TDX API 行业概念成分股",
            timeout=25,
            max_rows=100_000,
        )
        if rows is None:
            return None
        for row in rows:
            body = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            sector = _suffix_code(body.get("sector_code") or row.get("entity_key") or "")
            stock = _code6(body.get("Code") or row.get("series_key") or "")
            if sector in by_sector and stock:
                by_sector[sector].append(stock)
                name = str(body.get("Name") or body.get("name") or "").strip()
                if name:
                    names[stock] = name
                as_of = max(as_of, str(row.get("as_of") or ""))
    deduped = {key: tuple(dict.fromkeys(values)) for key, values in by_sector.items()}
    return {
        "by_sector": deduped,
        "names": names,
        "member_count": sum(len(values) for values in deduped.values()),
        "data_date": _iso_date(as_of),
    }


def sector_members_for_codes(codes: Iterable[str]) -> dict | None:
    """读取最多 40 个真实行业/概念板块的成分关系；任一分页失败整体降级。"""
    normalized = tuple(dict.fromkeys(filter(None, (_suffix_code(code) for code in codes))))[:40]
    return _sector_members_for_codes_cached(normalized)


def _chunks(values: tuple[str, ...], size: int) -> Iterable[tuple[str, ...]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


@ttl_cache(3, fail_seconds=8)
def _quotes_cached(codes: tuple[str, ...], max_age: int = 15, wait: int = 2) -> dict[str, dict]:
    if not configured() or not codes or len(codes) > _MAX_HOT_CODES:
        return {}
    # 实时接口本身没有名称；不能为了补一个展示字段在冷启动时同步翻完整
    # instruments_a（最坏 20s+）。交互聚合层会用腾讯名称补齐，名录由后台低频缓存。
    names: dict[str, str] = {}
    out: dict[str, dict] = {}
    for group in _chunks(codes, _QUOTE_BATCH):
        payload = _request_json(
            "/v1/realtime/quotes",
            {"codes": ",".join(group), "max_age": max(1, min(int(max_age), 120)), "wait": max(0, min(int(wait), 5))},
            label="TDX API 实时行情",
            # wait=0 的契约就是“不等待后台刷新”。网络层也必须是短预算，
            # 否则上游虽立即返回 pending，链路异常时仍会吃满原来的 12s。
            timeout=1.8 if int(wait) == 0 else max(3.0, int(wait) + 2.0),
        )
        rows = payload.get("quotes") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            normalized = _normalize_quote(row, names)
            if normalized and normalized.get("code"):
                out[normalized["code"]] = normalized
    return out


def quotes(codes: Iterable[str], *, max_age: int = 15, wait: int = 2) -> dict[str, dict]:
    entities = tuple(dict.fromkeys(filter(None, (_suffix_code(code) for code in codes))))
    return _quotes_cached(entities, max_age=max_age, wait=wait)


def quote(code: str, *, max_age: int = 15, wait: int = 2) -> dict | None:
    return quotes([code], max_age=max_age, wait=wait).get(_code6(code))


def index_quotes(symbols: Iterable[str]) -> dict[str, dict]:
    """项目指数代码 ``sh000001`` → 统一行情，返回键保持原样。"""
    original = tuple(dict.fromkeys(str(symbol).strip() for symbol in symbols if str(symbol).strip()))
    # 指数首屏不等待服务端热集；pending 立即让上层转腾讯。
    data = quotes(original, max_age=20, wait=0)
    out: dict[str, dict] = {}
    for symbol in original:
        row = data.get(_code6(symbol))
        if row:
            out[symbol] = row
    return out


@ttl_cache(60, fail_seconds=20)
def pricevol_snapshot() -> dict | None:
    """完整 ``pricevol`` 60 秒帧。

    原始 ``Volume`` 遵循日频数据集单位（手），统一转换为股。只有覆盖至少 4000
    只股票时才返回，避免板块涨跌家数被残页污染。
    """
    if not configured():
        return None
    rows = _dataset_rows(
        "pricevol",
        {"series": "latest"},
        label="TDX API 全市场价量",
        timeout=20,
        max_rows=15_000,
    )
    if rows is None:
        return None
    stocks: dict[str, dict] = {}
    as_of = ""
    for row in rows:
        body = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        now = _positive(body.get("Now"))
        prev = _positive(body.get("LastClose"))
        code = _code6(row.get("entity_key") or "")
        if not code or now is None or prev is None:
            continue
        stocks[code] = {
            "code": code,
            "price": now,
            "prev_close": prev,
            "change_pct": round((now / prev - 1) * 100, 4),
            "volume": _hands_to_shares(body.get("Volume")),
        }
        as_of = max(as_of, str(row.get("as_of") or ""))
    if len(stocks) < 4000:
        return None
    return {
        "stocks": stocks,
        "count": len(stocks),
        "data_date": _iso_date(as_of),
        "as_of": as_of,
        "source": "TDX pricevol",
        "interval_seconds": 60,
    }


@ttl_cache(60, fail_seconds=20)
def pricevol_breadth() -> dict | None:
    """``pricevol`` 全市场快照计算涨/跌/平；不把稀疏字段冒充全量股票快照。"""
    snapshot = pricevol_snapshot()
    if not snapshot:
        return None
    stocks = snapshot["stocks"].values()
    up = sum(1 for row in stocks if row["price"] > row["prev_close"])
    down = sum(1 for row in stocks if row["price"] < row["prev_close"])
    flat = int(snapshot["count"]) - up - down
    return {
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "count": snapshot["count"],
        "as_of": snapshot["as_of"],
        "source": snapshot["source"],
        "interval_seconds": 60,
    }


@ttl_cache(75, fail_seconds=20, offhours_seconds=6 * 3600)
def sector_fund_rank(
    *,
    field: str = "ZJBY.主力净额",
    source: str = "auto",
    direction: str = "desc",
    limit: int = 588,
) -> dict | None:
    """TDX v2.8 板块资金排名，保留未校准的原始单位。

    这些值可用于同字段的顺序和方向，不换算成元/万元，也不进入
    依赖金额的评分、求和或风控逻辑。
    """
    if not configured():
        return None
    safe_source = source if source in {"auto", "realtime", "daily", "member_sum"} else "auto"
    safe_direction = direction if direction in {"asc", "desc"} else "desc"
    payload = _request_json(
        "/v1/funds/sectors/rank",
        {
            "field": str(field or "ZJBY.主力净额")[:64],
            "source": safe_source,
            "direction": safe_direction,
            "limit": max(1, min(int(limit), 588)),
        },
        label="TDX API 板块资金排名",
        timeout=8,
    )
    raw_rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(raw_rows, list):
        return None
    rows: list[dict] = []
    for index, raw in enumerate(raw_rows, 1):
        if not isinstance(raw, dict):
            continue
        value = to_float(raw.get("value"))
        name = str(raw.get("sector_name") or raw.get("name") or "").strip()
        code = str(raw.get("sector_code") or raw.get("code") or "").strip()
        if value is None or (not name and not code):
            continue
        rows.append({
            "rank": int(to_float(raw.get("rank")) or index),
            "sector_code": code,
            "sector_name": name,
            "value": value,
        })
    if not rows:
        return None
    source_health = payload.get("source_health") if isinstance(payload.get("source_health"), dict) else {}
    unit = str(payload.get("unit") or "tdx_raw_unverified")
    data_date = str(
        payload.get("data_date")
        or source_health.get("data_date")
        or payload.get("series")
        or ""
    )
    return {
        "ok": True,
        "field": str(payload.get("field") or field),
        "source": str(payload.get("source") or safe_source),
        "source_health": source_health,
        "source_completed_at": payload.get("source_completed_at"),
        "data_date": _iso_date(data_date) or data_date or None,
        "count": len(rows),
        "total_ranked": int(to_float(payload.get("total_ranked")) or len(rows)),
        "unit": unit,
        "unit_verified": False,
        "rows": rows,
    }


@ttl_cache(20, fail_seconds=30)
def industry_market() -> dict | None:
    """TDX 行业实时榜：行业指数行情 + 成分股涨跌家数。

    行业成交额直接取行业指数实时报价的 ``amount``，不是用现价×成交量估算；
    涨跌家数与领涨股则由 ``sector_members × pricevol`` 精确聚合。
    """
    catalog = sector_catalog() or {}
    industries = [item for item in catalog.values() if item.get("kind") == "industry"]
    memberships = sector_memberships()
    frame = pricevol_snapshot()
    if not industries or not memberships or not frame:
        return None
    sector_codes = tuple(item["code"] for item in industries)
    # 行业代码首次进入热集可能 pending。交互请求不再追加 3+5 秒等待；
    # 取不齐就让上层转公开板块备源。
    quote_map = quotes(sector_codes, max_age=30, wait=0)
    fund_rank = sector_fund_rank(limit=588)
    funds_by_code: dict[str, dict] = {}
    funds_by_name: dict[str, dict] = {}
    if fund_rank:
        for fund in fund_rank.get("rows") or []:
            fund_code = _code6(fund.get("sector_code") or "")
            fund_name = str(fund.get("sector_name") or "").strip()
            if fund_code:
                funds_by_code[fund_code] = fund
            if fund_name:
                funds_by_name[fund_name] = fund
    names = stock_universe() or {}
    by_sector = memberships.get("by_sector") or {}
    stock_frame = frame.get("stocks") or {}
    sectors: list[dict] = []
    member_seen = member_covered = 0
    for item in industries:
        quote = quote_map.get(_code6(item["code"]))
        if not quote or quote.get("price") is None or quote.get("change_pct") is None:
            continue
        members = by_sector.get(item["code"]) or ()
        fund = funds_by_code.get(_code6(item["code"])) or funds_by_name.get(item["name"])
        up = down = flat = 0
        leader_code = ""
        leader_change = None
        for code in members:
            member_seen += 1
            stock = stock_frame.get(code)
            if not stock:
                continue
            member_covered += 1
            change = stock.get("change_pct")
            if change is None:
                continue
            if change > 0:
                up += 1
            elif change < 0:
                down += 1
            else:
                flat += 1
            if leader_change is None or change > leader_change:
                leader_change = change
                leader_code = code
        sectors.append({
            "kind": "industry",
            "code": item["code"],
            "name": item["name"],
            "change_pct": quote.get("change_pct"),
            "leader_name": names.get(leader_code, ""),
            "leader_code": leader_code,
            "leader_change_pct": round(leader_change, 2) if leader_change is not None else None,
            "amount": quote.get("amount"),
            # 金额单位尚未核验，只暴露原值/排名；main_flow 仍为 None，
            # 避免下游按“元”计算主线评分或大盘净额。
            "main_flow": None,
            "main_flow_raw": fund.get("value") if fund else None,
            "main_flow_rank": fund.get("rank") if fund else None,
            "main_flow_unit": fund_rank.get("unit") if fund else None,
            "main_flow_unit_verified": False if fund else None,
            "main_flow_source": fund_rank.get("source") if fund else None,
            "up_count": up,
            "down_count": down,
            "flat_count": flat,
            "source": "TDX 行业指数+成分股聚合",
        })
    if len(sectors) < 80:
        return None
    quote_total = len(industries)
    quote_covered = len(sectors)
    member_ratio = round(member_covered / member_seen, 4) if member_seen else 0
    degraded: list[str] = []
    if quote_covered < quote_total:
        degraded.append(f"{quote_total - quote_covered}个行业指数实时行情暂缺")
    if member_ratio < 0.95:
        degraded.append(f"行业成分股价量覆盖{member_covered}/{member_seen}")
    return {
        "ok": True,
        "ts": ts_str(),
        "data_date": frame.get("data_date") or memberships.get("data_date"),
        "data_state": "degraded" if degraded else "complete",
        "missing_fields": [],
        "degraded_factors": degraded,
        "notes": [
            "行业行情取自TDX行业指数实时通道",
            "行业涨跌家数与领涨股由TDX sector_members×pricevol精确聚合",
            "TDX板块资金排名已接入；原值单位未核验，未进入金额评分",
        ],
        "coverage": {
            "sector_quotes": {"complete": quote_covered, "total": quote_total},
            "member_prices": {"complete": member_covered, "total": member_seen},
        },
        "sectors": sectors,
    }


def _normalize_news_row(row: dict) -> dict | None:
    """把 ``news_sentiment`` 单行收敛成公共快讯契约。"""
    body = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    title = str(body.get("title") or "").strip()
    summary = str(body.get("Summary") or "").strip()
    if not title and not summary:
        return None
    event_time = str(body.get("Issue_date") or row.get("series_key") or "").strip()
    return {
        "source": "TDX舆情",
        "title": (title or summary[:80])[:160],
        "content": (summary or title)[:500],
        "time": event_time[:32],
    }


def _news_dedupe_key(item: dict) -> str:
    text = str(item.get("title") or item.get("content") or "")[:80]
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).lower()[:24]


@ttl_cache(30, fail_seconds=15)
def market_news(limit: int = 80) -> dict:
    """TDX 舆情最新页；只返回去重后的文本、时间与公开质量统计。

    该数据仅作为催化证据，不能参与市场结构分数。为避免为了取最新几十条
    而分页下载全历史，这里有意只读取一个倒序页。
    """
    requested = max(1, min(int(limit), 200))
    if not configured():
        return {
            "ok": False,
            "ts": ts_str(),
            "items": [],
            "missing": ["TDX舆情未配置"],
            "latest_event_time": None,
        }
    page_size = max(120, min(500, requested * 4))
    payload = _request_json(
        "/v1/data/news_sentiment",
        {"limit": page_size, "order": "desc"},
        label="TDX API 舆情快讯",
        timeout=20,
    )
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {
            "ok": False,
            "ts": ts_str(),
            "items": [],
            "missing": ["TDX舆情暂不可用"],
            "latest_event_time": None,
        }
    items: list[dict] = []
    seen: set[str] = set()
    nonempty = 0
    for row in rows:
        item = _normalize_news_row(row) if isinstance(row, dict) else None
        if not item:
            continue
        nonempty += 1
        key = _news_dedupe_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(item)
    items.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
    items = items[:requested]
    return {
        "ok": True,
        "ts": ts_str(),
        "items": items,
        "missing": [] if items else ["TDX舆情无有效摘要"],
        "latest_event_time": items[0]["time"] if items else None,
        "coverage": {
            "summary_or_title": {"complete": nonempty, "total": len(rows)},
        },
    }


def _latest_nonzero_fields(rows: list[dict]) -> dict[str, tuple[float, str]]:
    out: dict[str, tuple[float, str]] = {}
    for row in sorted(rows, key=lambda item: str(item.get("series_key") or ""), reverse=True):
        series = str(row.get("series_key") or "")
        body = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        for key, raw in body.items():
            if key in out:
                continue
            value = raw[0] if isinstance(raw, list) and raw else raw
            number = to_float(value)
            if number is not None and number != 0:
                out[str(key)] = (number, series)
    return out


def _consensus_research(rows: list[dict]) -> dict:
    fields = _latest_nonzero_fields(rows)

    def value(key: str) -> float | None:
        return fields.get(key, (None, ""))[0]

    start_year_value = value("GO4")
    start_year = int(start_year_value) if start_year_value and 2000 <= start_year_value <= 2200 else None

    def series(keys: tuple[str, str, str], *, divisor: float = 1.0) -> list[dict]:
        values: list[dict] = []
        for offset, key in enumerate(keys):
            number = value(key)
            if number is None:
                continue
            values.append({
                "year": start_year + offset if start_year else None,
                "value": round(number / divisor, 2),
                "as_of": fields[key][1],
            })
        return values

    target = value("GO3")
    eps = series(("GO5", "GO6", "GO7"))
    profit = series(("GO8", "GO9", "GO10"), divisor=10_000)
    revenue = series(("GO11", "GO12", "GO13"), divisor=10_000)
    roe = series(("GO20", "GO21", "GO22"))
    available = sum(bool(item) for item in (target, eps, profit, revenue, roe))
    return {
        "status": "available" if available else "missing",
        "as_of": max((str(row.get("series_key") or "") for row in rows), default=None),
        "target_price_cny": round(target, 2) if target is not None else None,
        "forecast_start_year": start_year,
        "eps_cny": eps,
        "net_profit_yi_cny": profit,
        "revenue_yi_cny": revenue,
        "roe_pct": roe,
        "coverage": {"complete": available, "total": 5},
        "mapping_status": "conditional",
        "note": "TDX GO 字段按一致预期口径解析；仅作盘后辅助，不参与日内评分",
    }


def _holders_research(rows: list[dict]) -> dict:
    ordered = sorted(rows, key=lambda item: str(item.get("series_key") or ""), reverse=True)
    selected = next((row for row in ordered if isinstance((row.get("payload") or {}).get("holders"), list) and (row.get("payload") or {}).get("holders")), None)
    if selected is None:
        return {"status": "missing", "report_date": None, "holders": []}
    raw_holders = (selected.get("payload") or {}).get("holders") or []
    holders: list[dict] = []
    for raw in raw_holders[:10]:
        if not isinstance(raw, dict):
            continue
        ratio = to_float(raw.get("cgbl"))
        shares = to_float(raw.get("cgsl"))
        holders.append({
            "rank": int(to_float(raw.get("pm")) or len(holders) + 1),
            "name": str(raw.get("name") or "").strip(),
            "shares": int(shares) if shares is not None and shares >= 0 else None,
            "ratio_pct": round(ratio, 4) if ratio is not None else None,
        })
    ratios = [item["ratio_pct"] for item in holders if item.get("ratio_pct") is not None]
    hk_ratios = [
        item["ratio_pct"] for item in holders
        if item.get("ratio_pct") is not None and "香港中央结算" in str(item.get("name") or "")
    ]
    return {
        "status": "available" if holders else "missing",
        "report_date": str(selected.get("series_key") or "") or None,
        "top10_total_ratio_pct": round(sum(ratios), 4) if ratios else None,
        "hk_clearing_ratio_pct": round(sum(hk_ratios), 4) if hk_ratios else None,
        "largest_holder": holders[0] if holders else None,
        "holders": holders,
        "note": "报告期低频筹码结构，仅用于盘后研究",
    }


@ttl_cache(300, fail_seconds=30)
def after_hours_research(code: str) -> dict:
    """机构一致预期 + 十大股东；GPJY 保持封锁并公开替代路径。"""
    entity = _suffix_code(code)
    if not entity:
        return {"ok": False, "error": "invalid_code", "missing": ["股票代码"]}
    consensus_rows = _dataset_rows(
        "gp_one", {"entities": entity}, label="TDX API 机构一致预期", max_rows=1_000
    )
    holder_rows = _dataset_rows(
        "holders_top10", {"entities": entity}, label="TDX API 十大股东", max_rows=100
    )
    consensus = _consensus_research(consensus_rows or [])
    holders = _holders_research(holder_rows or [])
    available = sum(item.get("status") == "available" for item in (consensus, holders))
    missing: list[str] = []
    if consensus_rows is None:
        missing.append("机构一致预期接口不可用")
    elif consensus.get("status") != "available":
        missing.append("该股无非零机构预测")
    if holder_rows is None:
        missing.append("十大股东接口不可用")
    elif holders.get("status") != "available":
        missing.append("该股无有效十大股东报告期")
    return {
        "ok": bool(available),
        "ts": ts_str(),
        "code": _code6(entity),
        "scope": "after_hours_research_only",
        "intraday_score_weight": 0,
        "data_state": "complete" if available == 2 else ("degraded" if available else "missing"),
        "missing": missing,
        "consensus": consensus,
        "holders_top10": holders,
        "gpjy": {
            "status": "blocked",
            "reason": "GP字段语义/单位验真失败，不输出疑似精确数字",
            "replacement": ["龙虎榜使用相同交易日公开榜单", "两融/质押/股东户数等待字段字典或独立公开源"],
        },
    }


@ttl_cache(30, fail_seconds=15)
def service_health() -> dict:
    """只返回可公开的链路状态，不返回 BASE 或 Key。"""
    if not configured():
        return {"configured": False, "connected": False}
    payload = _request_json("/v1/meta/health", label="TDX API 健康检查", timeout=10)
    if not isinstance(payload, dict):
        return {"configured": True, "connected": False}
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    statuses = {str(item.get("dataset")): str(item.get("status")) for item in items if isinstance(item, dict)}
    market_data = payload.get("market_data") if isinstance(payload.get("market_data"), dict) else {}
    realtime = _request_json("/v1/realtime/status", label="TDX API 实时流状态", timeout=5)
    realtime_count = int(to_float((realtime or {}).get("count")) or 0) if isinstance(realtime, dict) else None
    realtime_age = to_float((realtime or {}).get("feed_age_seconds")) if isinstance(realtime, dict) else None
    market_closed = any(
        statuses.get(name) == "market_closed"
        for name in ("pricevol", "l2_sector_realtime")
    )
    if market_closed:
        realtime_state = "market_closed"
    elif realtime_count is None:
        realtime_state = "unknown"
    elif realtime_count <= 0:
        realtime_state = "empty"
    elif realtime_age is None:
        realtime_state = "available"
    else:
        realtime_state = "live" if realtime_age <= 10 else "stale"
    return {
        "configured": True,
        "connected": True,
        "market_lagging": market_data.get("lagging") if isinstance(market_data.get("lagging"), bool) else None,
        "realtime_endpoint": "available" if isinstance(realtime, dict) else "unreachable",
        "realtime_quotes": realtime_state,
        "realtime_count": realtime_count,
        "realtime_feed_age_seconds": realtime_age,
        "realtime_last_push_at": (realtime or {}).get("last_push_at") if isinstance(realtime, dict) else None,
        "pricevol": statuses.get("pricevol") or "unknown",
        "sectors": statuses.get("sectors") or "unknown",
        "sector_members": statuses.get("sector_members") or "unknown",
        "relation": statuses.get("relation") or "unknown",
        "sector_fund_daily": statuses.get("l2_sector_fundflow") or "unknown",
        "sector_fund_realtime": statuses.get("l2_sector_realtime") or "unknown",
    }
