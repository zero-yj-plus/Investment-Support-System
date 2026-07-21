"""新闻到 A 股公司/行业/概念的确定性映射。

本模块不判断利多利空，也不使用模型猜实体：

* 股票必须同时存在于 TDX ``instruments_a`` 与交易所官网股票池；
* 公司只接受 6 位代码或完整正式简称命中；
* 行业/概念来自 TDX ``relation`` 或板块目录；
* 盘面只描述可观察状态，不把同涨同跌包装成新闻因果。
"""
from __future__ import annotations

from datetime import datetime, time
import hashlib
import re
from statistics import median
import unicodedata
from typing import Any, Iterable

from . import free_source, tdx_api_source as tdx
from .base import now_cn, ts_str


_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?:\.(?:SH|SZ|BJ))?(?!\d)", re.IGNORECASE)
_AMBIGUOUS_STOCK_NAMES = {
    "机器人", "人民网", "新大陆", "建设银行", "中国银行", "农业银行", "工商银行",
    "中国软件", "中国电影", "中国卫星", "国新健康", "国际实业", "时代出版",
}
_NAME_PREFIXES = ("*ST", "ST", "N")
_MAX_STOCKS = 4
_MAX_INDUSTRIES = 3
_MAX_CONCEPTS = 5
_SHORT_SECTOR_CONTEXT = ("板块", "行业", "指数", "概念", "个股", "股票", "期货", "涨停", "走强", "走弱")
_CORPORATE_NAME_EXTENSIONS = ("国际", "香港", "海外", "资管", "资产", "期货", "基金", "投行", "研究院")
_SECURITY_CODE_CONTEXT = ("股票代码", "证券代码", "代码", "证券", "公告")
# TDX 的 880 目录不仅包含产业概念，也混有行情形态/选股标签。它们即使与新闻
# 文本同名，也不能证明新闻涉及某条 A 股产业链，只能视作普通描述。
_NON_EVENT_TOPIC_NAMES = {
    "历史新高", "历史新低", "近期新高", "近期新低", "近期强势", "近期弱势",
    "连续上涨", "连续下跌", "超跌", "破净股", "低价股", "高价股", "百元股",
    "高股息", "低市盈率", "低市净率", "活跃股", "风险提示", "次新股",
}


def _text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).replace(" ", "").lower()


def _aliases(name: str) -> tuple[str, ...]:
    raw = unicodedata.normalize("NFKC", str(name or "")).replace(" ", "").strip()
    values = [raw] if raw else []
    upper = raw.upper()
    for prefix in _NAME_PREFIXES:
        if upper.startswith(prefix) and len(raw) - len(prefix) >= 3:
            values.append(raw[len(prefix):])
            break
    return tuple(dict.fromkeys(value for value in values if value))


def _name_is_specific(name: str) -> bool:
    compact = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", name)
    return len(compact) >= 4 and compact not in _AMBIGUOUS_STOCK_NAMES


def _listing_name_key(name: str) -> str:
    raw = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(name or "").upper())
    raw = re.sub(r"^(?:N|C|XD|DR|\*?ST)+", "", raw)
    raw = re.sub(r"(?:U|W|D)$", "", raw)
    return raw


def _names_consistent(tdx_name: str, official_name: str) -> bool:
    left, right = _listing_name_key(tdx_name), _listing_name_key(official_name)
    return bool(left and right and (left == right or (min(len(left), len(right)) >= 3 and (left.startswith(right) or right.startswith(left)))))


def _explicit_code_occurs(raw_text: str, code: str, names: tuple[str, ...]) -> bool:
    """六位普通数字不当证券代码；需后缀、证券语境或同时出现正式简称。"""
    for match in _CODE_RE.finditer(raw_text):
        if match.group(1) != code:
            continue
        full = match.group(0).lower()
        if re.search(r"\.(?:sh|sz|bj)$", full):
            return True
        window = raw_text[max(0, match.start() - 10):match.end() + 8]
        if any(marker in window for marker in _SECURITY_CODE_CONTEXT):
            return True
        if any(_text(name) and _text(name) in raw_text for name in names):
            return True
    return False


def _exact_name_occurs(raw_text: str, alias: str) -> bool:
    """排除“招商证券国际”这类包含 A 股简称、但实际指向子公司/境外机构的长实体。"""
    key = _text(alias)
    for match in re.finditer(re.escape(key), raw_text):
        suffix = raw_text[match.end():match.end() + 4]
        if any(suffix.startswith(extension) for extension in _CORPORATE_NAME_EXTENSIONS):
            continue
        return True
    return False


def _event_id(item: dict) -> str:
    raw = "\x1f".join(str(item.get(key) or "") for key in ("time", "title", "content"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    match = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)", raw)
    if match:
        try:
            return datetime.fromisoformat(f"{match.group(1)} {match.group(2)}")
        except ValueError:
            return None
    match = re.search(r"(\d{2}:\d{2}(?::\d{2})?)", raw)
    if match:
        try:
            return datetime.fromisoformat(f"{now_cn().date().isoformat()} {match.group(1)}")
        except ValueError:
            return None
    return None


def _frame_date(frame: dict | None):
    raw = str((frame or {}).get("data_date") or "")[:10]
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None


def _stock_matches(
    item: dict,
    tdx_names: dict[str, str],
    official_names: dict[str, str],
) -> list[dict]:
    raw_text = _text(f"{item.get('title') or ''} {item.get('content') or ''}")
    found: dict[str, dict] = {}
    # 代码是最强证据，但仍必须同时存在于两份真实股票池。
    for code in dict.fromkeys(_CODE_RE.findall(raw_text)):
        if code in tdx_names and code in official_names and _explicit_code_occurs(
            raw_text, code, (tdx_names[code], official_names[code]),
        ):
            consistent = _names_consistent(tdx_names[code], official_names[code])
            found[code] = {
                "kind": "stock",
                "code": code,
                "name": official_names.get(code) or tdx_names.get(code) or "",
                "match_by": "explicit_code",
                "evidence": code,
                "name_consistency": "matched" if consistent else "drifted",
            }
    # 名称采用最长精确匹配；短名、普通词和易歧义证券名不自动绑定。
    ordered = sorted(
        ((code, official_names.get(code) or name) for code, name in tdx_names.items() if code in official_names),
        key=lambda pair: len(pair[1] or ""),
        reverse=True,
    )
    occupied: list[str] = []
    for code, name in ordered:
        if code in found:
            continue
        for alias in _aliases(name):
            key = _text(alias)
            if not _name_is_specific(alias) or key not in raw_text or not _exact_name_occurs(raw_text, alias):
                continue
            # 已命中更长的正式简称时，不再把其内部短名绑定到另一只股票。
            if any(key in previous for previous in occupied):
                continue
            found[code] = {
                "kind": "stock",
                "code": code,
                "name": name,
                "match_by": "exact_short_name",
                "evidence": alias,
                "name_consistency": "matched" if _names_consistent(tdx_names[code], official_names[code]) else "drifted",
            }
            occupied.append(key)
            break
        if len(found) >= _MAX_STOCKS:
            break
    return list(found.values())[:_MAX_STOCKS]


def _direct_sector_matches(item: dict, catalog: dict[str, dict]) -> list[dict]:
    raw_text = _text(f"{item.get('title') or ''} {item.get('content') or ''}")
    candidates: list[dict] = []
    for sector in catalog.values():
        kind = str(sector.get("kind") or "")
        name = str(sector.get("name") or "").strip()
        code = str(sector.get("code") or "").strip()
        if kind not in {"industry", "concept"} or len(name) < 2 or not code:
            continue
        if kind == "concept" and name in _NON_EVENT_TOPIC_NAMES:
            continue
        key = _text(name)
        if key not in raw_text:
            continue
        if len(name) <= 2:
            windows = [
                raw_text[max(0, found - 5):found + len(key) + 5]
                for found in (match.start() for match in re.finditer(re.escape(key), raw_text))
            ]
            if not any(marker in window for marker in _SHORT_SECTOR_CONTEXT for window in windows):
                continue
        candidates.append({
            "kind": kind,
            "code": code,
            "name": name,
            "match_by": "exact_sector_name",
            "evidence": name,
        })
    # 优先保留长名称，避免“绿色电力”同时把内部“电力”冒充第二个直接命中。
    selected: list[dict] = []
    for item in sorted(candidates, key=lambda row: len(row["name"]), reverse=True):
        if any(item["name"] in previous["name"] for previous in selected):
            continue
        selected.append(item)
        if len(selected) >= _MAX_INDUSTRIES + _MAX_CONCEPTS:
            break
    return selected


def _dedupe_blocks(items: Iterable[dict], limit: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in items:
        code = str(item.get("code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(dict(item))
        if len(out) >= limit:
            break
    return out


def _market_timing(event_time: str, frame: dict | None) -> tuple[str, str, list[str]]:
    event = _parse_datetime(event_time)
    data_date = _frame_date(frame)
    if event is None:
        return "unavailable", "事件时间不完整", ["无法判断事件与盘面快照的先后关系"]
    if data_date is None:
        return "unavailable", "盘面快照暂不可用", ["pricevol 全市场完整帧不可用"]
    if event.date() > data_date or (event.date() == data_date and event.time() >= time(15, 0)):
        return "pending_next_session", "下一交易日待观察", ["事件发生于 A 股收盘后"]
    if event.date() < data_date:
        return "unavailable", "缺少事件时点历史快照", ["当前仅保留最新完整盘面帧"]
    return "observation_only", "同日盘面观察（非因果）", []


def sector_observation(
    block: dict,
    members_by_sector: dict[str, Iterable[str]],
    stocks: dict[str, dict],
    names: dict[str, str],
) -> dict | None:
    members = list(members_by_sector.get(str(block.get("code") or "")) or ())
    if not members:
        return None
    rows = [stocks.get(code) for code in members]
    usable = [row for row in rows if isinstance(row, dict) and row.get("change_pct") is not None]
    if not usable:
        return None
    changes = [float(row["change_pct"]) for row in usable]
    leaders = sorted(usable, key=lambda row: float(row.get("change_pct") or 0), reverse=True)[:2]
    return {
        "kind": block.get("kind"),
        "code": block.get("code"),
        "name": block.get("name"),
        "member_count": len(members),
        "covered": len(usable),
        "up_count": sum(value > 0 for value in changes),
        "down_count": sum(value < 0 for value in changes),
        "flat_count": sum(value == 0 for value in changes),
        "up_ratio_pct": round(sum(value > 0 for value in changes) / len(changes) * 100, 1),
        "median_change_pct": round(median(changes), 2),
        "leaders": [
            {
                "code": row.get("code"),
                "name": names.get(str(row.get("code") or ""), ""),
                "change_pct": round(float(row.get("change_pct") or 0), 2),
                "basis": "当前板块成员涨幅靠前",
            }
            for row in leaders
        ],
    }


def _base_unavailable(item: dict, missing: list[str]) -> dict:
    return {
        **item,
        "event_id": _event_id(item),
        "a_share": {
            "state": "unavailable",
            "level": "none",
            "matches": [],
            "topic_candidates": [],
            "hypothesis": {"direction": "unknown", "basis": []},
            "market_check": {
                "state": "unavailable", "label": "映射底座暂不可用", "as_of": None,
                "stocks": [], "sectors": [], "evidence": [], "counter_evidence": [],
            },
            "coverage": {
                "entities": {"complete": 0, "total": 0},
                "relations": {"complete": 0, "total": 0},
                "market_checks": {"complete": 0, "total": 0},
                "missing": missing,
            },
        },
    }


def map_news_items(items: list[dict]) -> dict:
    """给标准新闻列表附加确定性 ``a_share`` 合同与整体覆盖状态。"""
    source_items = [dict(item) for item in items if isinstance(item, dict)]
    tdx_names = tdx.stock_universe() or {}
    official_names = free_source.stock_universe() or {}
    official_status = free_source.stock_universe_status()
    catalog = tdx.sector_catalog() or {}
    base_missing: list[str] = []
    if not tdx_names:
        base_missing.append("TDX instruments_a 全 A 名录不可用")
    if not official_names:
        base_missing.append("交易所官网股票池不可用")
    if not catalog:
        base_missing.append("TDX 板块目录不可用")
    if base_missing:
        mapped = [_base_unavailable(item, base_missing) for item in source_items]
        return {
            "items": mapped,
            "summary": {
                "state": "unavailable", "mapped": 0, "candidates": 0, "total": len(mapped),
                "missing": base_missing,
            },
        }

    prepared: list[dict] = []
    all_stock_codes: list[str] = []
    for item in source_items:
        stocks = _stock_matches(item, tdx_names, official_names)
        direct_blocks = _direct_sector_matches(item, catalog)
        all_stock_codes.extend(stock["code"] for stock in stocks)
        prepared.append({"item": item, "stocks": stocks, "direct_blocks": direct_blocks})

    relations = tdx.relations_for_codes(all_stock_codes) if all_stock_codes else {}
    blocks_needed: dict[str, dict] = {}
    for row in prepared:
        derived: list[dict] = []
        for stock in row["stocks"]:
            relation = relations.get(stock["code"]) or {}
            industry = relation.get("industry")
            if isinstance(industry, dict) and industry.get("code"):
                derived.append({
                    "kind": "industry", "code": industry.get("code"), "name": industry.get("name"),
                    "match_by": "derived_from_stock", "evidence": stock.get("name"),
                })
            for concept in relation.get("concepts") or []:
                concept_name = str(concept.get("name") or "") if isinstance(concept, dict) else ""
                if isinstance(concept, dict) and concept.get("code") and concept_name not in _NON_EVENT_TOPIC_NAMES:
                    derived.append({
                        "kind": "concept", "code": concept.get("code"), "name": concept_name,
                        "match_by": "derived_from_stock", "evidence": stock.get("name"),
                    })
        row["derived_blocks"] = derived
        for block in [*row["direct_blocks"], *derived]:
            if block.get("code"):
                blocks_needed[str(block["code"])] = block

    # 行业静态索引与少量概念成员按需读取；任一不可用只降级盘面观察，不撤销实体映射。
    industry_memberships = tdx.sector_memberships() or {}
    concept_codes = [code for code, block in blocks_needed.items() if block.get("kind") == "concept"]
    concept_memberships = tdx.sector_members_for_codes(concept_codes) or {}
    members_by_sector = {
        **dict(industry_memberships.get("by_sector") or {}),
        **dict(concept_memberships.get("by_sector") or {}),
    }
    frame = tdx.pricevol_snapshot()
    frame_stocks = dict((frame or {}).get("stocks") or {})

    mapped_items: list[dict] = []
    mapped_count = candidate_count = 0
    for row in prepared:
        original = row["item"]
        stocks = row["stocks"]
        derived = row["derived_blocks"]
        direct_industries = [block for block in row["direct_blocks"] if block.get("kind") == "industry"]
        direct_concepts = [block for block in row["direct_blocks"] if block.get("kind") == "concept"]
        industries = _dedupe_blocks(
            [block for block in derived if block.get("kind") == "industry"] + direct_industries,
            _MAX_INDUSTRIES,
        )
        concepts = _dedupe_blocks(
            [block for block in derived if block.get("kind") == "concept"],
            _MAX_CONCEPTS,
        )
        topic_candidates = _dedupe_blocks(direct_concepts, _MAX_CONCEPTS)
        if stocks:
            state, level = "mapped", "direct_stock"
        elif direct_industries:
            state, level = "mapped", "direct_sector"
        elif topic_candidates:
            state, level = "candidate", "topic_candidate"
        else:
            state, level = "none", "none"

        if state == "mapped":
            mapped_count += 1
        elif state == "candidate":
            candidate_count += 1

        market_state, market_label, market_missing = _market_timing(
            str(original.get("time") or ""), frame,
        ) if state in {"mapped", "candidate"} else ("not_applicable", "无 A 股映射", [])
        stock_observations: list[dict] = []
        sector_observations: list[dict] = []
        if market_state == "observation_only":
            for stock in stocks:
                quote = frame_stocks.get(stock["code"])
                if quote and quote.get("change_pct") is not None:
                    stock_observations.append({
                        "code": stock["code"], "name": stock["name"],
                        "change_pct": round(float(quote["change_pct"]), 2),
                    })
            for block in _dedupe_blocks([*industries, *concepts, *topic_candidates], 4):
                observation = sector_observation(block, members_by_sector, frame_stocks, tdx_names)
                if observation:
                    sector_observations.append(observation)
            if not stock_observations and not sector_observations:
                market_state, market_label = "unavailable", "盘面观察暂不可用"
                market_missing.append("映射对象缺少完整价量或成分覆盖")

        relation_total = len(stocks)
        relation_complete = sum(bool(relations.get(stock["code"], {}).get("industry")) for stock in stocks)
        coverage_missing = list(market_missing)
        if stocks and relation_complete < relation_total:
            coverage_missing.append(f"{relation_total - relation_complete}只股票行业关系缺失")
        drifted_names = sum(stock.get("name_consistency") == "drifted" for stock in stocks)
        if drifted_names:
            coverage_missing.append(f"{drifted_names}只股票双名录简称存在漂移，代码关系仍保留")
        matches = [
            *stocks,
            *[{**block, "kind": "industry"} for block in industries],
            *[{**block, "kind": "concept"} for block in concepts],
        ]
        mapped_items.append({
            **original,
            "event_id": _event_id(original),
            "a_share": {
                "state": state,
                "level": level,
                "matches": matches,
                "topic_candidates": topic_candidates,
                "representative_stocks": [
                    leader
                    for sector in sector_observations
                    for leader in sector.get("leaders") or []
                ][:3],
                "hypothesis": {"direction": "unknown", "basis": []},
                "market_check": {
                    "state": market_state,
                    "label": market_label,
                    "as_of": (frame or {}).get("as_of"),
                    "stocks": stock_observations,
                    "sectors": sector_observations,
                    "evidence": [],
                    "counter_evidence": [],
                },
                "coverage": {
                    "entities": {"complete": len(stocks), "total": len(stocks)},
                    "relations": {"complete": relation_complete, "total": relation_total},
                    "market_checks": {
                        "complete": len(stock_observations) + len(sector_observations),
                        "total": len(stocks) + len(industries) + len(concepts) + len(topic_candidates),
                    },
                    "missing": coverage_missing,
                },
            },
        })

    base_state = "complete" if official_status.get("complete") and frame else "degraded"
    summary_missing: list[str] = []
    if not official_status.get("complete"):
        summary_missing.append("交易所官网股票池为部分覆盖或沿用值")
    if not frame:
        summary_missing.append("pricevol 全市场完整帧不可用")
    return {
        "items": mapped_items,
        "summary": {
            "state": base_state,
            "mapped": mapped_count,
            "candidates": candidate_count,
            "total": len(mapped_items),
            "generated_at": ts_str(),
            "score_weight": 0,
            "rule": "股票代码/正式简称双重名录校验；行业/概念来自TDX关系或目录；盘面只作观察",
            "universe": {
                "tdx_count": len(tdx_names),
                "official_count": len(official_names),
                "official_complete": bool(official_status.get("complete")),
                "official_stale": bool(official_status.get("stale")),
            },
            "market_frame": {
                "available": bool(frame),
                "data_date": (frame or {}).get("data_date"),
                "as_of": (frame or {}).get("as_of"),
                "count": (frame or {}).get("count", 0),
            },
            "missing": summary_missing,
        },
    }
