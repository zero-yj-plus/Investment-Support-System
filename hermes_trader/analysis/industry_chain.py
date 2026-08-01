"""产业链结构化研究内核。

公开板块成员只能证明“某只股票属于某个行业/概念集合”，不能证明其
供应链环节、客户、订单、产能、国产化率或投资价值。本模块把可验证事实
与待研究框架分开，并产出一份可由研究中心和问股 ``chain`` 模式共同消费
的冻结契约。模型不得改变候选池和证据，但可把公开产业常识用于定性研究；
所有常识推演必须标成待公告核验。
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import threading
import time
from typing import Any

from ..data import (
    free_source,
    official_disclosure,
    official_interaction,
    stock,
    tdx_api_source as tdx_api,
)
from ..data.base import get_ak, now_cn, safe, ttl_cache


SCHEMA_VERSION = "industry-chain-analysis.v1"
RULE_VERSION = "industry-chain-evidence-verification-v2"

_SECTOR_ALIASES = {
    "光模块": ("CPO概念", "光通信"),
    "cpo": ("CPO概念", "光通信"),
    "光通信": ("光通信", "CPO概念"),
    "ai液冷": ("液冷服务器",),
    "液冷": ("液冷服务器",),
}
_QUOTE_COVERAGE_FIELDS = ("price", "change_pct", "amount", "pe", "pb", "total_mv")
_PUBLIC_MEMBERSHIP_SOURCE = "东方财富公开板块（AKShare）"
_EVIDENCE_CACHE_TTL = 30 * 60
_EVIDENCE_REFRESH_LIMIT = 5
_EVIDENCE_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chain-evidence")
_EVIDENCE_LOCK = threading.RLock()
_EVIDENCE_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}
_EVIDENCE_INFLIGHT: set[tuple[str, str, str]] = set()


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def sector_search_terms(industry: str) -> tuple[str, ...]:
    normalized = str(industry or "").strip()
    if not normalized:
        return ()
    compact = "".join(normalized.split()).casefold()
    terms = [normalized]
    for key, aliases in _SECTOR_ALIASES.items():
        if key.casefold() in compact:
            terms.extend(aliases)
    return tuple(dict.fromkeys(term for term in terms if term))


def _frame_records(frame: Any) -> list[dict]:
    """兼容 pandas DataFrame 与测试中的普通记录列表。"""
    if isinstance(frame, list):
        return [row for row in frame if isinstance(row, dict)]
    if frame is None or getattr(frame, "empty", False):
        return []
    try:
        rows = frame.to_dict(orient="records")
    except (AttributeError, TypeError, ValueError):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _field(row: dict, *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _public_board_matches(
    industry: str,
    board_rows: Iterable[dict],
    *,
    limit: int = 2,
) -> list[dict]:
    """匹配东财公开行业/概念目录，不把模型猜测写入候选池。"""
    normalized = str(industry or "").strip()
    terms = sector_search_terms(normalized)
    matched: list[dict] = []
    for row in board_rows:
        name = _field(row, "板块名称", "名称", "name")
        if not name:
            continue
        folded = name.casefold()
        exact_indexes = [
            index for index, term in enumerate(terms)
            if term.casefold() == folded
        ]
        indexes = exact_indexes or [
            index
            for index, term in enumerate(terms)
            if term.casefold() in folded or folded in term.casefold()
        ]
        if not indexes:
            continue
        kind = _field(row, "_kind", "kind") or "concept"
        raw_code = _field(row, "板块代码", "代码", "code")
        code = raw_code or f"em-{kind}-{name}"
        matched.append({
            "code": code,
            "name": name,
            "kind": kind,
            "data_date": now_cn().date().isoformat(),
            "source": _PUBLIC_MEMBERSHIP_SOURCE,
            "_term_index": min(indexes),
            "_exact": bool(exact_indexes),
        })
    # 存在精确板块时，禁止把“绿色电力”“虚拟电厂”等宽泛包含项并入
    # “电力”的成员池。只有完全没有精确项时才使用包含匹配兜底。
    if any(item.get("_exact") for item in matched):
        matched = [item for item in matched if item.get("_exact")]
    matched.sort(key=lambda item: (
        0 if item["name"] == normalized else 1,
        item["_term_index"],
        0 if item["kind"] == "industry" else 1,
        len(item["name"]),
        item["code"],
    ))
    return [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in matched[:max(0, int(limit))]
    ]


def _public_member_rows(frame: Any) -> tuple[list[str], dict[str, str]]:
    codes: list[str] = []
    names: dict[str, str] = {}
    for row in _frame_records(frame):
        raw = _field(row, "代码", "股票代码", "code").split(".", 1)[0]
        code = raw.zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        codes.append(code)
        name = _field(row, "名称", "股票简称", "name")
        if name:
            names[code] = name
    return list(dict.fromkeys(codes)), names


@ttl_cache(6 * 3600, fail_seconds=120)
def _public_board_membership(industry: str) -> dict | None:
    """免密公开回退：最多两次目录调用和两次成分调用，无逐股 N+1。"""
    ak = get_ak()
    if ak is None:
        return None

    board_rows: list[dict] = []
    degraded: list[str] = []
    specs = (
        ("concept", "stock_board_concept_name_em", "东财概念板块目录"),
        ("industry", "stock_board_industry_name_em", "东财行业板块目录"),
    )

    def fetch_catalog(spec: tuple[str, str, str]) -> tuple[list[dict], str | None]:
        kind, function_name, label = spec
        function = getattr(ak, function_name, None)
        if function is None:
            return [], f"{label}函数不可用"
        frame = safe(function, label=label, timeout=10)
        rows = _frame_records(frame)
        return (
            [{**row, "_kind": kind} for row in rows],
            None if rows else f"{label}未返回记录",
        )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="chain-public-catalog") as pool:
        catalog_results = list(pool.map(fetch_catalog, specs))
    for rows, reason in catalog_results:
        board_rows.extend(rows)
        if reason:
            degraded.append(reason)

    matched = _public_board_matches(industry, board_rows)
    by_sector: dict[str, tuple[str, ...]] = {}
    names: dict[str, str] = {}

    def fetch_members(board: dict) -> tuple[dict, list[str], dict[str, str], str | None]:
        kind = str(board.get("kind") or "concept")
        function_name = (
            "stock_board_industry_cons_em"
            if kind == "industry"
            else "stock_board_concept_cons_em"
        )
        function = getattr(ak, function_name, None)
        if function is None:
            return board, [], {}, f"{board.get('name')}成分函数不可用"
        frame = safe(
            function,
            symbol=str(board.get("name") or ""),
            label=f"东财公开板块成分:{board.get('name')}",
            timeout=12,
        )
        codes, member_names = _public_member_rows(frame)
        return (
            board,
            codes,
            member_names,
            None if codes else f"{board.get('name')}未返回成分",
        )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="chain-public-members") as pool:
        member_results = list(pool.map(fetch_members, matched))
    for board, codes, member_names, reason in member_results:
        by_sector[str(board.get("code") or "")] = tuple(codes)
        names.update(member_names)
        if reason:
            degraded.append(reason)

    member_codes = list(dict.fromkeys(
        code for codes in by_sector.values() for code in codes
    ))
    if not member_codes:
        return None
    return {
        "state": "complete",
        "by_sector": by_sector,
        "member_count": len(member_codes),
        "data_date": now_cn().date().isoformat() if matched else None,
        "names": names,
        "source": _PUBLIC_MEMBERSHIP_SOURCE,
        "catalog_source": "东方财富公开行业/概念目录（AKShare）",
        "verification": "public_board_member",
        "matched_sectors": matched,
        "degraded_reasons": degraded,
    }


def match_sector_catalog(
    industry: str,
    catalog: dict[str, dict] | None,
    *,
    limit: int = 6,
) -> list[dict]:
    """按显式名称/别名匹配公开板块目录；不使用模型猜板块或成分股。"""
    normalized = str(industry or "").strip()
    terms = sector_search_terms(normalized)
    if not terms or not isinstance(catalog, dict):
        return []
    matched: list[dict] = []
    for item in catalog.values():
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        folded = name.casefold()
        exact_indexes = [
            index for index, term in enumerate(terms)
            if term.casefold() == folded
        ]
        indexes = exact_indexes or [
            index
            for index, term in enumerate(terms)
            if term.casefold() in folded or folded in term.casefold()
        ]
        if not indexes:
            continue
        matched.append({
            "code": str(item.get("code") or ""),
            "name": name,
            "kind": str(item.get("kind") or "other"),
            "data_date": item.get("data_date"),
            "_term_index": min(indexes),
            "_exact": bool(exact_indexes),
        })
    if any(item.get("_exact") for item in matched):
        matched = [item for item in matched if item.get("_exact")]
    matched.sort(key=lambda item: (
        0 if item["name"] == normalized else 1,
        item["_term_index"],
        0 if item["kind"] == "industry" else 1,
        len(item["name"]),
        item["code"],
    ))
    return [
        {key: value for key, value in item.items() if not key.startswith("_")}
        for item in matched[:max(0, int(limit))]
    ]


def _member_codes(matched: Iterable[dict], membership: dict | None) -> list[str]:
    by_sector = (
        membership.get("by_sector")
        if isinstance(membership, dict) and isinstance(membership.get("by_sector"), dict)
        else {}
    )
    codes: list[str] = []
    for sector in matched:
        for raw in by_sector.get(str(sector.get("code") or ""), ()) or ():
            code = str(raw or "").split(".", 1)[0].zfill(6)
            if len(code) == 6 and code.isdigit():
                codes.append(code)
    return list(dict.fromkeys(codes))


def _sector_market_rows(matched: list[dict], sector_market: dict | None) -> list[dict]:
    wanted = {str(item.get("name") or "") for item in matched}
    rows = (
        sector_market.get("sectors")
        if isinstance(sector_market, dict) and isinstance(sector_market.get("sectors"), list)
        else []
    )
    return [
        {
            "name": row.get("name"),
            "kind": row.get("kind"),
            "change_pct": row.get("change_pct"),
            "amount": row.get("amount"),
            "main_flow": row.get("main_flow"),
            "data_as_of": (
                row.get("as_of")
                or row.get("data_date")
                or (sector_market or {}).get("data_date")
                or (sector_market or {}).get("ts")
            ),
        }
        for row in rows
        if isinstance(row, dict) and str(row.get("name") or "") in wanted
    ]


def _matched_evidence_terms(industry: str, texts: Iterable[str]) -> list[str]:
    haystack = "\n".join(str(text or "") for text in texts).casefold()
    return [
        term
        for term in sector_search_terms(industry)
        if len(term.strip()) >= 2 and term.casefold() in haystack
    ]


def _compact_company_evidence(code: str, industry: str, data_dir: str) -> dict:
    """后台抓取单家公司证据；调用方严格限制并发和公司数。"""
    end = now_cn().date()
    start = end - timedelta(days=180)
    disclosure_path = Path(data_dir) / "official-disclosures" / f"{code}.json"
    try:
        disclosures = official_disclosure.cached_official_disclosures(
            code,
            start.isoformat(),
            end.isoformat(),
            8,
            False,
            1,
            str(disclosure_path),
        )
    except Exception:  # noqa: BLE001 - 后台增强失败须结构化降级
        disclosures = {
            "state": "missing",
            "verification_state": "unavailable",
            "data_as_of": None,
            "items": [],
        }

    if code.startswith(("0", "3")):
        try:
            interactions = official_interaction.cached_official_interactions(
                code,
                start.isoformat(),
                end.isoformat(),
                6,
                data_dir,
            )
        except Exception:  # noqa: BLE001 - Tier 3 增强不影响主分析
            interactions = {"state": "missing", "data_as_of": None, "items": []}
    else:
        interactions = {
            "state": "not_supported",
            "data_as_of": None,
            "items": [],
        }

    try:
        financial = free_source.fina_indicator(code)
    except Exception:  # noqa: BLE001 - 免费财务源失败须显式缺失
        financial = None

    formal_items = [
        {
            "title": str(item.get("title") or "")[:180],
            "announcement_date": item.get("announcement_date"),
            "original_url": item.get("original_url"),
            "source_confirmations": item.get("source_confirmations") or [],
            "evidence_level": item.get("evidence_level") or "tier_1",
        }
        for item in (disclosures.get("items") or [])[:5]
        if isinstance(item, dict) and item.get("title")
    ]
    interaction_items = [
        {
            "question": str(item.get("question") or "")[:180],
            "answer": str(item.get("answer") or "")[:240],
            "question_pub_date": item.get("question_pub_date"),
            "answer_pub_date": item.get("answer_pub_date"),
            "answered": bool(item.get("answered")),
            "evidence_level": item.get("evidence_level") or "tier_3",
        }
        for item in (interactions.get("items") or [])[:3]
        if isinstance(item, dict) and (item.get("question") or item.get("answer"))
    ]
    texts = [item["title"] for item in formal_items]
    texts.extend(
        f"{item.get('question') or ''} {item.get('answer') or ''}"
        for item in interaction_items
    )
    matched_terms = _matched_evidence_terms(industry, texts)
    selected_financial = None
    if isinstance(financial, dict):
        selected_financial = {
            key: financial.get(key)
            for key in (
                "end_date",
                "roe",
                "grossprofit_margin",
                "netprofit_yoy",
                "or_yoy",
                "debt_to_assets",
                "source",
            )
            if financial.get(key) is not None
        } or None
    available = bool(formal_items or interaction_items or selected_financial)
    return {
        "status": "available" if available else "missing",
        "refreshed_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "matched_terms": matched_terms,
        "business_relevance": (
            "evidence_clue" if matched_terms else "not_verified"
        ),
        "formal_disclosures": formal_items,
        "formal_disclosure_state": disclosures.get("state"),
        "formal_verification_state": disclosures.get("verification_state"),
        "official_interactions": interaction_items,
        "interaction_state": interactions.get("state"),
        "financial_snapshot": selected_financial,
        "data_as_of": max(
            (
                str(value)
                for value in (
                    disclosures.get("data_as_of"),
                    interactions.get("data_as_of"),
                    (selected_financial or {}).get("end_date"),
                )
                if value
            ),
            default=None,
        ),
        "limits": (
            "公告标题和互动问答中的关键词只构成业务线索；"
            "仍需公告/年报正文确认产业环节与相关收入，基础财务不能证明产业归因。"
        ),
    }


def _evidence_done(key: tuple[str, str, str], future: Any) -> None:
    try:
        result = future.result()
    except Exception:  # noqa: BLE001 - 保证后台任务总能释放 in-flight
        result = {
            "status": "missing",
            "refreshed_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
            "matched_terms": [],
            "limits": "官方公司证据后台刷新失败；未生成替代值。",
        }
    with _EVIDENCE_LOCK:
        _EVIDENCE_CACHE[key] = (time.monotonic(), result)
        _EVIDENCE_INFLIGHT.discard(key)


def _company_evidence_snapshot(
    codes: Iterable[str],
    industry: str,
    data_dir: str | Path | None,
) -> tuple[dict[str, dict], dict]:
    """立即返回已有快照，并为前五家公司触发有界后台刷新。"""
    if data_dir is None:
        return {}, {
            "status": "disabled",
            "scheduled_count": 0,
            "cached_count": 0,
            "limit": _EVIDENCE_REFRESH_LIMIT,
        }
    root = str(Path(data_dir).resolve())
    subject_key = "".join(str(industry or "").split()).casefold()
    clean_codes = list(dict.fromkeys(
        str(code or "").zfill(6)
        for code in codes
        if str(code or "").zfill(6).isdigit()
        and len(str(code or "").zfill(6)) == 6
    ))
    snapshot: dict[str, dict] = {}
    scheduled = 0
    refreshing = 0
    now_mono = time.monotonic()
    with _EVIDENCE_LOCK:
        for code in clean_codes:
            key = (root, subject_key, code)
            cached = _EVIDENCE_CACHE.get(key)
            if cached is not None:
                snapshot[code] = cached[1]
            if key in _EVIDENCE_INFLIGHT:
                refreshing += 1

        for code in clean_codes[:_EVIDENCE_REFRESH_LIMIT]:
            key = (root, subject_key, code)
            cached = _EVIDENCE_CACHE.get(key)
            fresh = cached is not None and now_mono - cached[0] < _EVIDENCE_CACHE_TTL
            if fresh or key in _EVIDENCE_INFLIGHT or len(_EVIDENCE_INFLIGHT) >= 10:
                continue
            _EVIDENCE_INFLIGHT.add(key)
            try:
                future = _EVIDENCE_POOL.submit(
                    _compact_company_evidence,
                    code,
                    industry,
                    root,
                )
            except RuntimeError:
                _EVIDENCE_INFLIGHT.discard(key)
                continue
            future.add_done_callback(lambda done, item=key: _evidence_done(item, done))
            scheduled += 1

    available_cached = sum(
        1 for item in snapshot.values() if item.get("status") == "available"
    )
    if scheduled or refreshing:
        status = "refreshing"
    elif available_cached:
        status = "available"
    else:
        status = "missing"
    return snapshot, {
        "status": status,
        "scheduled_count": scheduled,
        "cached_count": len(snapshot),
        "available_count": available_cached,
        "limit": _EVIDENCE_REFRESH_LIMIT,
        "mode": "bounded_background_cache",
    }


def _company_mapping(
    matched: list[dict],
    membership: dict | None,
    quotes: dict[str, dict] | None,
    *,
    limit: int,
    company_evidence: dict[str, dict] | None = None,
) -> tuple[list[dict], list[str]]:
    by_sector = (
        membership.get("by_sector")
        if isinstance(membership, dict) and isinstance(membership.get("by_sector"), dict)
        else {}
    )
    member_codes = _member_codes(matched, membership)
    quote_map = quotes if isinstance(quotes, dict) else {}
    evidence_map = company_evidence if isinstance(company_evidence, dict) else {}
    member_names = (
        membership.get("names")
        if isinstance(membership, dict) and isinstance(membership.get("names"), dict)
        else {}
    )
    verification = (
        str(membership.get("verification") or "tdx_sector_member")
        if isinstance(membership, dict)
        else "tdx_sector_member"
    )
    membership_source = (
        str(membership.get("source") or "TDX 数据 API / sector_members")
        if isinstance(membership, dict)
        else "TDX 数据 API / sector_members"
    )
    sector_refs: dict[str, list[dict]] = {code: [] for code in member_codes}
    for sector in matched:
        sector_code = str(sector.get("code") or "")
        for raw in by_sector.get(sector_code, ()) or ():
            code = str(raw or "").split(".", 1)[0].zfill(6)
            if code in sector_refs:
                sector_refs[code].append({
                    "code": sector_code,
                    "name": sector.get("name"),
                    "kind": sector.get("kind"),
                })

    amounts = {
        code: _number((quote_map.get(code) or {}).get("amount"))
        for code in member_codes
        if isinstance(quote_map.get(code), dict)
    }
    ranked_amounts = sorted(
        ((code, amount) for code, amount in amounts.items() if amount is not None and amount >= 0),
        key=lambda item: (-item[1], item[0]),
    )
    amount_rank = {code: index for index, (code, _amount) in enumerate(ranked_amounts)}
    amount_count = len(ranked_amounts)

    rows: list[dict] = []
    for code in member_codes:
        quote = quote_map.get(code) if isinstance(quote_map.get(code), dict) else {}
        official_evidence = (
            evidence_map.get(code) if isinstance(evidence_map.get(code), dict) else None
        )
        matched_terms = (
            official_evidence.get("matched_terms")
            if isinstance(official_evidence, dict)
            and isinstance(official_evidence.get("matched_terms"), list)
            else []
        )
        financial_context = (
            official_evidence.get("financial_snapshot")
            if isinstance(official_evidence, dict)
            and isinstance(official_evidence.get("financial_snapshot"), dict)
            else None
        )
        present = sum(1 for field in _QUOTE_COVERAGE_FIELDS if _number(quote.get(field)) is not None)
        coverage_points = present * 5
        if code in amount_rank:
            liquidity_points = (
                20
                if amount_count <= 1
                else round(20 * (amount_count - 1 - amount_rank[code]) / (amount_count - 1), 2)
            )
        else:
            liquidity_points = 0.0
        score = round(50 + coverage_points + liquidity_points, 2)
        valuation_values = {
            "pe": _number(quote.get("pe")),
            "pb": _number(quote.get("pb")),
            "total_mv": _number(quote.get("total_mv")),
        }
        valuation_available = sum(value is not None for value in valuation_values.values())
        rows.append({
            "code": code,
            "name": str(quote.get("name") or member_names.get(code) or ""),
            "verification": verification,
            "membership_source": membership_source,
            "matched_sectors": sector_refs.get(code, []),
            "chain_role": {
                "status": "evidence_clue" if matched_terms else "unverified",
                "value": None,
                "matched_terms": matched_terms,
                "note": (
                    "官方公告标题或交易所互动问答出现产业关键词，但仍须公告/年报正文确认具体环节。"
                    if matched_terms
                    else "板块成分关系不能证明上游/中游/下游角色，须用公告或财报核验。"
                ),
            },
            "quote": {
                "price": _number(quote.get("price")),
                "change_pct": _number(quote.get("change_pct")),
                "amount": _number(quote.get("amount")),
                "source": quote.get("source"),
                "data_as_of": quote.get("as_of"),
                "stale": bool(quote.get("stale")) if quote else None,
            },
            "realization": {
                "status": "financial_context_only" if financial_context else "not_verified",
                "financial_snapshot": financial_context,
                "required_evidence": ["相关业务收入占比", "订单/出货", "毛利率与产能利用率", "客户或产品公告"],
            },
            "valuation": {
                "status": "available" if valuation_available == 3 else (
                    "partial" if valuation_available else "missing"
                ),
                **valuation_values,
                "source": quote.get("source"),
                "data_as_of": quote.get("as_of"),
                "note": "仅为行情源估值快照，不代表产业逻辑已兑现。",
            },
            "data_researchability_score": score,
            "data_researchability_components": {
                (
                    "tdx_membership"
                    if verification == "tdx_sector_member"
                    else "public_board_membership"
                ): 50,
                "quote_coverage": coverage_points,
                "liquidity_researchability": liquidity_points,
            },
            "data_researchability_rule": (
                f"50({membership_source}成员验证)+5×有效报价/估值字段数(最多30)"
                "+成交额横截面分位(0-20)；仅用于排证据核验顺序，"
                "不得作为产业研究、投资价值或交易排名。"
            ),
            "company_evidence": official_evidence,
            "missing": [
                label
                for label, value in (
                    ("股票名称", quote.get("name") or member_names.get(code)),
                    ("实时报价", quote.get("price")),
                    ("成交额", quote.get("amount")),
                    ("PE", quote.get("pe")),
                    ("PB", quote.get("pb")),
                    ("总市值", quote.get("total_mv")),
                    ("产业链环节", None),
                    ("业绩兑现证据", None),
                )
                if value in (None, "")
            ],
        })
    rows.sort(key=lambda row: (
        -float(row["data_researchability_score"]),
        -(_number((row.get("quote") or {}).get("amount")) or 0.0),
        row["code"],
    ))
    for index, row in enumerate(rows[:max(0, int(limit))], start=1):
        row["evidence_verification_priority"] = index
    return rows[:max(0, int(limit))], member_codes


def build_analysis(
    industry: str,
    *,
    catalog: dict[str, dict] | None,
    membership: dict | None,
    quotes: dict[str, dict] | None,
    sector_market: dict | None = None,
    generated_at: str | None = None,
    company_limit: int = 20,
    company_evidence: dict[str, dict] | None = None,
    evidence_refresh: dict | None = None,
) -> dict:
    """从冻结数据构造可复算产业链研究契约。"""
    subject = str(industry or "").strip()
    generated = generated_at or now_cn().strftime("%Y-%m-%d %H:%M:%S")
    sector_market_data = sector_market if isinstance(sector_market, dict) else {}
    quote_data = quotes if isinstance(quotes, dict) else {}
    membership_data = membership if isinstance(membership, dict) else {}
    membership_source = str(
        membership_data.get("source") or "TDX 数据 API / sector_members"
    )
    catalog_source = str(
        membership_data.get("catalog_source") or "TDX 数据 API / sectors"
    )
    membership_verification = str(
        membership_data.get("verification") or "tdx_sector_member"
    )
    refresh_state = evidence_refresh if isinstance(evidence_refresh, dict) else {
        "status": "disabled",
        "scheduled_count": 0,
        "cached_count": 0,
        "limit": _EVIDENCE_REFRESH_LIMIT,
    }
    matched = match_sector_catalog(subject, catalog)
    companies, all_member_codes = _company_mapping(
        matched,
        membership_data,
        quote_data,
        limit=company_limit,
        company_evidence=company_evidence,
    )
    market_rows = _sector_market_rows(matched, sector_market_data)
    membership_date = (
        membership_data.get("data_date")
    )
    catalog_dates = sorted({
        str(item.get("data_date"))
        for item in matched
        if item.get("data_date")
    })
    quote_times = sorted({
        str((row.get("quote") or {}).get("data_as_of"))
        for row in companies
        if (row.get("quote") or {}).get("data_as_of")
    })
    company_evidence_count = sum(
        1
        for row in companies
        if isinstance(row.get("company_evidence"), dict)
        and (row.get("company_evidence") or {}).get("status") == "available"
    )
    company_evidence_dates = sorted({
        str((row.get("company_evidence") or {}).get("data_as_of"))
        for row in companies
        if isinstance(row.get("company_evidence"), dict)
        and (row.get("company_evidence") or {}).get("data_as_of")
    })

    evidence = [
        {
            "id": "sector_catalog",
            "source": catalog_source,
            "status": "available" if matched else "missing",
            "data_as_of": catalog_dates[-1] if catalog_dates else None,
            "supports": "板块名称与分类",
            "does_not_support": "公司业务角色、订单、客户、产能或投资结论",
        },
        {
            "id": "sector_members",
            "source": membership_source,
            "status": "available" if all_member_codes else "missing",
            "data_as_of": membership_date,
            "supports": "股票属于匹配板块集合",
            "does_not_support": "股票处于产业链哪个环节或相关业务收入占比",
        },
        {
            "id": "market_quotes",
            "source": "TDX 数据 API（主）/腾讯财经（降级合并）",
            "status": "available" if any((row.get("quote") or {}).get("price") is not None for row in companies) else "missing",
            "data_as_of": quote_times[-1] if quote_times else None,
            "supports": "当前价格、成交额与行情源估值快照",
            "does_not_support": "未来收益、订单兑现或买卖时点",
        },
        {
            "id": "sector_market",
            "source": "市场板块行情聚合",
            "status": "available" if market_rows else "missing",
            "data_as_of": (
                sector_market_data.get("data_date")
                or sector_market_data.get("ts")
            ),
            "supports": "匹配板块当前行情状态",
            "does_not_support": "中长期产业景气和公司基本面",
        },
        {
            "id": "official_company_evidence",
            "source": "巨潮/交易所公告 + 深交所互动问答 + 免费基础财务",
            "status": (
                "available"
                if company_evidence_count
                else str(refresh_state.get("status") or "missing")
            ),
            "data_as_of": company_evidence_dates[-1] if company_evidence_dates else None,
            "supports": "公告与互动问答线索、公司基础财务上下文",
            "does_not_support": "仅凭标题/问答关键词确认产业环节、相关收入或投资价值",
        },
    ]
    data_gaps = [
        {
            "dimension": "需求本质",
            "status": "missing",
            "needed": ["终端销量/装机/渗透率", "价格与库存", "政策或客户资本开支"],
        },
        {
            "dimension": "链条结构与价值捕获",
            "status": "missing",
            "needed": ["产品BOM/工艺路线", "供需格局", "各环节毛利率与议价权"],
        },
        {
            "dimension": "卡点与国产替代",
            "status": "missing",
            "needed": ["进口依赖", "国产认证/良率", "核心专利与扩产周期"],
        },
        {
            "dimension": "公司业务映射与兑现",
            "status": "partial" if company_evidence_count else (
                "refreshing" if refresh_state.get("status") == "refreshing" else "missing"
            ),
            "needed": ["公告/年报业务收入占比", "订单与出货", "产能利用率", "客户验证"],
        },
    ]
    if not matched:
        data_gaps.insert(0, {
            "dimension": (
                "TDX 板块匹配"
                if membership_verification == "tdx_sector_member"
                else "公开板块匹配"
            ),
            "status": "missing",
            "needed": [f"与“{subject}”一致的行业/概念名称或显式别名"],
        })
    if matched and not all_member_codes:
        data_gaps.insert(0, {
            "dimension": "板块成分关系",
            "status": "missing",
            "needed": [f"{membership_source}的匹配板块成分数据"],
        })
    if all_member_codes and not companies:
        data_gaps.append({
            "dimension": "成员行情",
            "status": "missing",
            "needed": ["TDX 实时或收盘快照", "腾讯行情降级源（均为可选）"],
        })

    return {
        "ok": bool(subject),
        "schema_version": SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "industry": subject,
        "generated_at": generated,
        "data_as_of": {
            "sector_catalog": catalog_dates[-1] if catalog_dates else None,
            "sector_members": membership_date,
            "quotes": quote_times[-1] if quote_times else None,
            "company_evidence": company_evidence_dates[-1] if company_evidence_dates else None,
            "sector_market": next(
                (row.get("data_as_of") for row in market_rows if row.get("data_as_of")),
                None,
            ),
        },
        "conclusion": (
            f"已用{membership_source}验证 {len(matched)} 个相关板块、{len(all_member_codes)} 只去重成员；"
            "这只能建立研究候选集合，不能证明供应链角色、业绩兑现或可交易性。"
            if matched
            else f"板块目录未匹配“{subject}”；本轮只保留研究框架，不生成公司排名。"
        ),
        "research_trade_separation": {
            "industry_conclusion": "只描述产业传导假设、证据状态与研究顺序。",
            "trade_timing": "not_evaluated",
            "trade_note": "当前是否可交易、价格、仓位和风险必须另用当时实时行情与风险闸复核。",
            "prohibited": ["即时买卖指令", "目标价", "仓位建议", "把研究优先级当投资排名"],
        },
        "demand_essence": {
            "status": "research_framework",
            "question": f"{subject}的终端需求究竟由数量、价格、渗透率还是替换周期驱动？",
            "current_conclusion": "当前结构化数据未覆盖终端需求，不能判断景气上行或下行。",
            "required_evidence": ["终端销量/装机", "渗透率", "库存与价格", "客户资本开支"],
        },
        "chain_structure": [
            {
                "stage": "upstream",
                "label": "上游·材料/设备/关键输入",
                "status": "unverified_framework",
                "research_question": "哪些输入决定成本、供给弹性与扩产周期？",
            },
            {
                "stage": "midstream",
                "label": "中游·制造/加工/模组/系统",
                "status": "unverified_framework",
                "research_question": "核心工艺、良率、规模效应和议价权集中在哪里？",
            },
            {
                "stage": "downstream",
                "label": "下游·应用/渠道/终端",
                "status": "unverified_framework",
                "research_question": "谁采购、为何采购、需求何时兑现，替代品是什么？",
            },
        ],
        "value_capture": {
            "status": "research_framework",
            "current_conclusion": "缺少分环节毛利、供需与议价数据，不能断言利润留在哪个环节。",
            "ranking_dimensions": ["稀缺性", "议价权", "良率/规模", "订单可见度", "资本开支强度"],
        },
        "bottlenecks": {
            "status": "research_framework",
            "current_conclusion": "尚未取得能证明实际卡点的供应链证据。",
            "verification_order": ["核心材料/设备可替代性", "认证周期", "良率与产能", "交付周期", "专利/标准"],
        },
        "domestic_substitution": {
            "status": "not_verified",
            "current_conclusion": "没有进口依赖、国产认证和份额数据，不输出国产化率。",
            "required_evidence": ["进口依赖基数", "国产认证进度", "量产良率", "份额与价格差", "客户切换成本"],
        },
        "matched_sectors": [
            {
                **item,
                "member_count": len(
                    (
                        membership_data.get("by_sector", {}).get(item.get("code"), ())
                    ) or ()
                ),
            }
            for item in matched
        ],
        "sector_market": market_rows,
        "company_mapping": companies,
        "coverage": {
            "matched_sector_count": len(matched),
            "verified_member_count": len(all_member_codes),
            "membership_source": membership_source,
            "membership_verification": membership_verification,
            # 兼容既有前端；新代码应读取 verified_member_count。
            "tdx_member_count": len(all_member_codes),
            "quote_requested_count": sum(
                1 for code in all_member_codes
                if isinstance(quote_data.get(code), dict)
            ),
            "quoted_count": sum(
                1
                for code in all_member_codes
                if isinstance(quote_data.get(code), dict)
                and _number((quote_data.get(code) or {}).get("price")) is not None
            ),
            "displayed_company_count": len(companies),
            "company_evidence_available_count": company_evidence_count,
        },
        "company_evidence_refresh": refresh_state,
        "realization_and_valuation": {
            "realization_status": (
                "financial_context_only"
                if any((row.get("realization") or {}).get("financial_snapshot") for row in companies)
                else "not_verified"
            ),
            "valuation_status": (
                "partial"
                if any((row.get("valuation") or {}).get("status") != "missing" for row in companies)
                else "missing"
            ),
            "note": "估值快照与业务兑现分开；PE/PB/市值可用也不能替代订单、收入和利润证据。",
        },
        "evidence_verification_queue": [
            {
                "priority": index,
                "code": row.get("code"),
                "name": row.get("name"),
                "data_researchability_score": row.get("data_researchability_score"),
                "next_verification": [
                    "先用公告/年报确认产业链环节和相关收入占比",
                    "再核验订单/出货/毛利/产能",
                    "最后才做同环节估值比较",
                ],
            }
            for index, row in enumerate(companies[:5], start=1)
        ],
        "invalidation_conditions": [
            "公司公告/财报无法证明其与目标产业存在实质业务收入时，剔除公司映射。",
            "终端销量、渗透率、价格或客户资本开支连续弱于研究假设时，下调产业景气判断。",
            "关键瓶颈被替代、认证失败、良率/交付不达标或供给快速过剩时，原价值捕获假设失效。",
            "公开板块口径、来源或成分数据日期变化时，必须重跑成员集合与研究顺序。",
        ],
        "evidence": evidence,
        "data_gaps": data_gaps,
    }


async def _bounded_call(fn, *args, timeout: float, default):
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
    except Exception:  # noqa: BLE001 - 各数据源内部记录详细错误，聚合层只降级
        return default


async def collect_analysis(
    industry: str,
    *,
    company_limit: int = 20,
    data_dir: str | Path | None = None,
) -> dict:
    """采集低频板块关系并构造共享契约；实时行情不参与首屏。"""
    catalog = await _bounded_call(tdx_api.sector_catalog, timeout=7.0, default={})
    matched = match_sector_catalog(industry, catalog)
    sector_codes = [str(item.get("code") or "") for item in matched if item.get("code")]
    membership = await _bounded_call(
        tdx_api.sector_members_for_codes,
        sector_codes,
        timeout=9.0,
        default={},
    ) if sector_codes else {}
    member_codes = _member_codes(matched, membership)
    effective_catalog = catalog
    if not member_codes:
        public_membership = await _bounded_call(
            _public_board_membership,
            industry,
            timeout=24.0,
            default={},
        )
        public_matched = (
            public_membership.get("matched_sectors")
            if isinstance(public_membership, dict)
            and isinstance(public_membership.get("matched_sectors"), list)
            else []
        )
        public_catalog = {
            str(item.get("code") or ""): item
            for item in public_matched
            if isinstance(item, dict) and item.get("code")
        }
        public_codes = _member_codes(public_matched, public_membership)
        if public_catalog and public_codes:
            membership = public_membership
            effective_catalog = public_catalog
            matched = public_matched
            member_codes = public_codes
    # 成员关系仍是低频研究底座；只对报告候选做一次有界批量报价，禁止逐股
    # N+1。行情只进入结构化附录，不改变产业结论或 trade_timing。
    quotes = await _bounded_call(
        stock.quick_quotes,
        sorted(member_codes)[:max(0, int(company_limit))],
        timeout=3.0,
        default={},
    ) if member_codes else {}
    sector_market: dict = {}
    evidence_codes = sorted(member_codes)
    company_evidence, evidence_refresh = _company_evidence_snapshot(
        evidence_codes,
        industry,
        data_dir,
    )
    return build_analysis(
        industry,
        catalog=effective_catalog,
        membership=membership,
        quotes=quotes,
        sector_market=sector_market,
        company_limit=company_limit,
        company_evidence=company_evidence,
        evidence_refresh=evidence_refresh,
    )


def _fmt_number(value: Any, digits: int = 2) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:.{digits}f}"


def _fmt_money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "—"
    if abs(number) >= 100_000_000:
        return f"{number / 100_000_000:.2f}亿元"
    if abs(number) >= 10_000:
        return f"{number / 10_000:.2f}万元"
    return f"{number:.2f}元"


def _md_cell(value: Any) -> str:
    text = str(value if value not in (None, "") else "—")
    return text.replace("|", "｜").replace("\r", " ").replace("\n", " ").strip()


def _header_lines(analysis: dict) -> list[str]:
    subject = str(analysis.get("industry") or "未命名产业")
    as_of = analysis.get("data_as_of") or {}
    evidence = analysis.get("evidence") or []
    source_summary = "；".join(
        f"{item.get('source')}={item.get('status')}"
        for item in evidence
        if isinstance(item, dict)
    ) or "来源状态缺失"
    gap_summary = "；".join(
        str(item.get("dimension") or "")
        for item in (analysis.get("data_gaps") or [])
        if isinstance(item, dict) and item.get("dimension")
    ) or "无已登记缺口"
    separation = analysis.get("research_trade_separation") or {}
    trade_timing = str(separation.get("trade_timing") or "not_evaluated")
    trade_timing_label = {
        "not_evaluated": "未评估",
        "unavailable": "暂不可用",
    }.get(trade_timing, trade_timing)
    return [
        f"## {subject}·产业链结构化研究",
        "",
        f"**产业结论：{analysis.get('conclusion') or '当前无法形成结构化结论。'}**",
        f"**当前交易时点：{trade_timing_label}。"
        f"{separation.get('trade_note') or '需另行核验实时行情和风险。'}**",
        "",
        f"> 数据时点：板块目录 {as_of.get('sector_catalog') or '缺失'}；"
        f"成分 {as_of.get('sector_members') or '缺失'}；报价 {as_of.get('quotes') or '缺失'}；"
        f"公司证据 {as_of.get('company_evidence') or '刷新中/缺失'}；"
        f"板块行情 {as_of.get('sector_market') or '缺失'}。",
        f"> 来源覆盖：{source_summary}。",
        f"> 页首缺口：{gap_summary}。",
    ]


def _company_table_lines(analysis: dict) -> list[str]:
    lines = [
        "|核验序|代码|名称|板块集合|公司证据|现价|涨跌幅|成交额|总市值|PE|PB|数据可研分|",
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    companies = analysis.get("company_mapping") or []
    for row in companies[:10]:
        sectors = " / ".join(
            str(item.get("name") or "") for item in (row.get("matched_sectors") or [])
        ) or "—"
        quote = row.get("quote") or {}
        valuation = row.get("valuation") or {}
        company_evidence = row.get("company_evidence") or {}
        evidence_status = company_evidence.get("status") or (
            (analysis.get("company_evidence_refresh") or {}).get("status")
            or "missing"
        )
        lines.append(
            f"|{row.get('evidence_verification_priority') or '—'}|{row.get('code') or '—'}|"
            f"{row.get('name') or '名称待行情补齐'}|{sectors}|"
            f"{evidence_status}|"
            f"{_fmt_number(quote.get('price'))}|{_fmt_number(quote.get('change_pct'))}%|"
            f"{_fmt_money(quote.get('amount'))}|{_fmt_money(valuation.get('total_mv'))}|"
            f"{_fmt_number(valuation.get('pe'))}|{_fmt_number(valuation.get('pb'))}|"
            f"{_fmt_number(row.get('data_researchability_score'))}|"
        )
    if not companies:
        lines.append("|—|—|—|未取得可验证的板块成分关系|missing|—|—|—|—|—|—|—|")
    lines.extend([
        "",
        "- “数据可研分”=公开板块成员验证 + 字段覆盖 + 成交额数据可得性；"
        "**只用于决定先核验谁，不是产业研究优先级、投资排名或买入排名。**",
        "- 公司产业链角色、相关收入、订单与国产替代仍须公告/财报正文证明；"
        "板块成分和互动问答关键词本身不作业务背书。",
    ])
    return lines


def _company_evidence_lines(analysis: dict) -> list[str]:
    """展示已有公司经营证据；没有就诚实保留刷新/缺失状态。"""
    lines = [
        "### 公司经营与官方证据快照",
        "",
        "|代码|名称|证据状态|财务期|ROE|毛利率|营收同比|净利同比|资产负债率|产业关键词|",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    rows = analysis.get("company_mapping") or []
    for row in rows[:10]:
        evidence = row.get("company_evidence") or {}
        financial = evidence.get("financial_snapshot") or {}
        keywords = "、".join(str(item) for item in (evidence.get("matched_terms") or [])) or "—"
        lines.append(
            f"|{_md_cell(row.get('code'))}|{_md_cell(row.get('name'))}|"
            f"{_md_cell(evidence.get('status') or (analysis.get('company_evidence_refresh') or {}).get('status') or 'missing')}|"
            f"{_md_cell(financial.get('end_date'))}|{_fmt_number(financial.get('roe'))}|"
            f"{_fmt_number(financial.get('grossprofit_margin'))}|{_fmt_number(financial.get('or_yoy'))}|"
            f"{_fmt_number(financial.get('netprofit_yoy'))}|{_fmt_number(financial.get('debt_to_assets'))}|"
            f"{_md_cell(keywords)}|"
        )
    if not rows:
        lines.append("|—|—|missing|—|—|—|—|—|—|—|")
    lines.extend([
        "",
        "- 财务指标均为最近可得报告期原值；单期数据不能推出历史改善或恶化。",
        "- 产业关键词只来自公告标题或交易所互动问答，构成核验线索，不证明收入占比、订单或产业链角色。",
    ])

    disclosures: list[str] = []
    for row in rows[:10]:
        evidence = row.get("company_evidence") or {}
        for item in (evidence.get("formal_disclosures") or [])[:2]:
            disclosures.append(
                f"- {_md_cell(row.get('name') or row.get('code'))}（{_md_cell(row.get('code'))}）"
                f"· {_md_cell(item.get('announcement_date'))} · {_md_cell(item.get('title'))}"
            )
    lines.extend(["", "### 近期官方公告线索"])
    lines.extend(disclosures[:12] or ["- 本轮未取得可展示的官方公告标题；不等于公司近期没有公告。"])
    return lines


def _footer_lines(analysis: dict) -> list[str]:
    lines = ["---", "**页末证据与缺口复核**"]
    for item in analysis.get("evidence") or []:
        lines.append(
            f"- {item.get('source')}：{item.get('status')}；截至 {item.get('data_as_of') or '未知'}；"
            f"只支持“{item.get('supports')}”。"
        )
    for gap in analysis.get("data_gaps") or []:
        needed = "、".join(str(item) for item in (gap.get("needed") or []))
        lines.append(f"- 缺口·{gap.get('dimension')}：{needed or '待补证据'}。")
    return lines


def render_evidence_baseline(analysis: dict) -> str:
    """模型可用时先返回冻结证据底座，避免生成失败造成整页空白。"""
    matched = analysis.get("matched_sectors") or []
    lines = [
        *_header_lines(analysis),
        "",
        "### 冻结证据底座",
        "- 公开来源匹配板块："
        + (
            "；".join(
                f"{item.get('name')}（{item.get('code')}，{item.get('member_count')}只）"
                for item in matched
            )
            if matched
            else "未匹配"
        ),
        "- 下表只冻结真实成分候选、行情快照与证据核验顺序；不冻结产业角色或研究结论。",
        "",
        *_company_table_lines(analysis),
        "",
        *_footer_lines(analysis),
    ]
    return "\n".join(lines)


def render_evidence_appendix(analysis: dict) -> str:
    """把冻结证据放在研究正文之后，避免证据表淹没产业结论。"""
    matched = analysis.get("matched_sectors") or []
    lines = [
        "## 可核验证据附录",
        "",
        "本附录只说明板块集合、候选公司、数据时点和待核事项，不代替前文产业判断。",
        "",
        "### 板块与成员口径",
        "- 公开来源匹配板块："
        + (
            "；".join(
                f"{item.get('name')}（{item.get('code')}，{item.get('member_count')}只）"
                for item in matched
            )
            if matched
            else "未匹配；公司映射保持为空，但不影响行业级产业结构分析"
        ),
        "- 下表只冻结真实成分候选与证据核验顺序，不证明产业角色或投资价值。",
        "",
        *_company_table_lines(analysis),
        "",
        *_company_evidence_lines(analysis),
        "",
        *_footer_lines(analysis),
    ]
    return "\n".join(lines)


def render_generation_fallback(analysis: dict, *, reason: str) -> str:
    """生成失败时只报告状态与少量可靠证据，不伪装成完整研究。"""
    subject = str(analysis.get("industry") or "目标产业")
    matched = analysis.get("matched_sectors") or []
    coverage = analysis.get("coverage") or {}
    as_of = analysis.get("data_as_of") or {}
    matched_text = "；".join(
        f"{item.get('name')}（{item.get('code')}，{item.get('member_count')}只）"
        for item in matched
        if isinstance(item, dict)
    ) or "未匹配到可验证板块"
    reliable_sources = [
        item for item in (analysis.get("evidence") or [])
        if isinstance(item, dict) and item.get("status") == "available"
    ]
    lines = [
        "# 产业链研究未完成",
        "",
        "## 本轮状态",
        f"“{subject}”本轮没有形成可采信的完整模型正文。{str(reason or '').strip()}",
        "部分生成内容未通过完整终态门禁，已丢弃，不能作为产业结论展示。",
        "",
        "## 已取得的可靠证据",
        f"- 精确匹配板块：{matched_text}。",
        f"- 去重板块成员：{int(coverage.get('verified_member_count') or 0)} 只；"
        f"取得报价：{int(coverage.get('quoted_count') or 0)} 只。",
        f"- 公司资料可用：{int(coverage.get('company_evidence_available_count') or 0)} 只；"
        "这只表示取得公告、互动或基础财务资料，不证明产业链角色。",
        f"- 数据时点：板块 {as_of.get('sector_members') or as_of.get('sector_catalog') or '未知'}；"
        f"行情 {as_of.get('quotes') or '未知'}。",
    ]
    if reliable_sources:
        lines.append(
            "- 可用来源：" + "；".join(
                f"{item.get('source')}（仅支持：{item.get('supports')}）"
                for item in reliable_sources
            ) + "。"
        )
    lines.extend([
        "",
        "> 本页未输出利润池、产业卡点、国产替代或公司受益排序；请修复模型生成后重试。",
    ])
    return "\n".join(lines)


def render_governed_company_mapping(analysis: dict, *, limit: int = 6) -> str:
    """由服务端提供简洁研究入口，避免板块数据表淹没产业思维。"""
    rows = sorted(
        analysis.get("company_mapping") or [],
        key=lambda row: (
            -int(bool(((row.get("company_evidence") or {}).get("matched_terms")))),
            -len(row.get("matched_sectors") or []),
            str(row.get("code") or ""),
        ),
    )
    lines = ["### 6. A股映射思路"]
    if not rows:
        lines.extend([
            "公司映射应从前述关键环节反推：先找掌握瓶颈资源和核心工艺的公司，"
            "再看规模制造与下游应用。本轮没有可靠的本地候选名称，因此不列公司名单。",
        ])
        return "\n".join(lines)

    lines.append("映射顺序应沿着“瓶颈环节 → 利润池 → 兑现能力”展开，而不是按板块涨跌或成交额选股。")
    lines.append("")
    lines.append("**本地板块候选线索**（仅作为后续公司研究入口）：")
    for row in rows[:max(1, int(limit))]:
        code = str(row.get("code") or "—")
        name = str(row.get("name") or code)
        sectors = " / ".join(
            str(item.get("name") or item.get("code") or "")
            if isinstance(item, dict)
            else str(item)
            for item in (row.get("matched_sectors") or [])
            if item
        ) or "匹配板块"
        lines.append(f"- **{name}（{code}）** · {sectors}")
    if len(rows) > limit:
        lines.append(f"- 另有 {len(rows) - limit} 只板块候选未展开。")
    lines.append("")
    lines.append("这些名称只代表板块关联；是否处在关键环节，应在单公司研究中判断。")
    return "\n".join(lines)


def render_markdown(analysis: dict) -> str:
    """模型不可用时保留内容优先的产业思维框架，不展示证据大表。"""
    subject = str(analysis.get("industry") or "目标产业")
    lines = [
        "# 产业链上下游研究",
        "",
        "## 产业结论",
        f"本轮暂未形成“{subject}”的模型增强分析，以下保留完整研究框架，"
        "便于继续梳理需求、上下游、利润池、卡点和国产替代。",
        "",
        "### 1. 核心需求与驱动力",
        f"先回答：{subject}解决了谁的什么问题，需求由新增、升级、替换还是降本驱动？",
        "",
        "### 2. 上中下游全景",
        "|环节|核心任务|价值来源|向下游传导|",
        "|---|---|---|---|",
        "|上游|提供关键材料、设备、芯片或核心输入|稀缺性、技术壁垒与供给约束|决定成本、性能和扩产速度|",
        "|中游|制造、加工、集成与交付|工艺、良率、规模与客户认证|把关键输入转化为可销售产品|",
        "|下游|应用、渠道与终端采购|场景渗透、运营效率与需求强度|决定订单节奏和产业天花板|",
        "",
        "### 3. 利润池与价值分配",
        "沿稀缺性、议价权、扩产难度、客户切换成本和规模效应判断利润更可能停留在哪一环。",
        "",
        "### 4. 核心卡点与竞争壁垒",
        "依次检查关键材料与设备、核心工艺、良率、认证、专利标准和交付能力。",
        "",
        "### 5. 国产替代路径",
        "从“能做”到“能稳定量产”，再到“能进入核心客户并形成规模收入”分阶段判断。",
        "",
    ]
    lines.extend([
        "",
        render_governed_company_mapping(analysis),
        "",
        "### 7. 兑现节奏与观察指标",
        "观察终端需求、关键产品迭代、客户认证、产能爬坡和商业化收入是否按顺序出现。",
        "",
        "### 8. 风险与失效条件",
    ])
    lines.extend(f"- {item}" for item in (analysis.get("invalidation_conditions") or []))
    lines.extend([
        "",
        "---",
        "",
        "> 研究说明：本报告以产业结构和商业逻辑为主；公司业务、订单、财务和交易时点需另行研究。",
    ])
    return "\n".join(lines)
