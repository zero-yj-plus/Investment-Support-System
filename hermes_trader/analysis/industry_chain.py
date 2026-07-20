"""产业链结构化研究内核。

TDX 板块成员只能证明“某只股票属于某个行业/概念集合”，不能证明其
供应链环节、客户、订单、产能、国产化率或投资价值。本模块把可验证事实
与待研究框架分开，并产出一份可由研究中心和问股 ``chain`` 模式共同消费
的冻结契约。模型不得改变候选池和证据，但可把公开产业常识用于定性研究；
所有常识推演必须标成待公告核验。
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from ..data import market, stock, tdx_api_source as tdx_api
from ..data.base import now_cn


SCHEMA_VERSION = "industry-chain-analysis.v1"
RULE_VERSION = "industry-chain-evidence-verification-v1"

_SECTOR_ALIASES = {
    "光模块": ("CPO概念", "光通信"),
    "cpo": ("CPO概念", "光通信"),
    "光通信": ("光通信", "CPO概念"),
    "ai液冷": ("液冷服务器",),
    "液冷": ("液冷服务器",),
}
_QUOTE_COVERAGE_FIELDS = ("price", "change_pct", "amount", "pe", "pb", "total_mv")


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


def match_sector_catalog(
    industry: str,
    catalog: dict[str, dict] | None,
    *,
    limit: int = 6,
) -> list[dict]:
    """按显式名称/别名匹配 TDX 目录；不使用模型猜板块或成分股。"""
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
        indexes = [
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
        })
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


def _company_mapping(
    matched: list[dict],
    membership: dict | None,
    quotes: dict[str, dict] | None,
    *,
    limit: int,
) -> tuple[list[dict], list[str]]:
    by_sector = (
        membership.get("by_sector")
        if isinstance(membership, dict) and isinstance(membership.get("by_sector"), dict)
        else {}
    )
    member_codes = _member_codes(matched, membership)
    quote_map = quotes if isinstance(quotes, dict) else {}
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
            "name": str(quote.get("name") or ""),
            "verification": "tdx_sector_member",
            "matched_sectors": sector_refs.get(code, []),
            "chain_role": {
                "status": "unverified",
                "value": None,
                "note": "板块成分关系不能证明上游/中游/下游角色，须用公告或财报核验。",
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
                "status": "not_verified",
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
                "tdx_membership": 50,
                "quote_coverage": coverage_points,
                "liquidity_researchability": liquidity_points,
            },
            "data_researchability_rule": (
                "50(TDX成员验证)+5×有效报价/估值字段数(最多30)"
                "+成交额横截面分位(0-20)；仅用于排证据核验顺序，"
                "不得作为产业研究、投资价值或交易排名。"
            ),
            "missing": [
                label
                for label, value in (
                    ("股票名称", quote.get("name")),
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
) -> dict:
    """从冻结数据构造可复算产业链研究契约。"""
    subject = str(industry or "").strip()
    generated = generated_at or now_cn().strftime("%Y-%m-%d %H:%M:%S")
    sector_market_data = sector_market if isinstance(sector_market, dict) else {}
    quote_data = quotes if isinstance(quotes, dict) else {}
    matched = match_sector_catalog(subject, catalog)
    companies, all_member_codes = _company_mapping(
        matched, membership, quote_data, limit=company_limit,
    )
    market_rows = _sector_market_rows(matched, sector_market_data)
    membership_date = (
        membership.get("data_date") if isinstance(membership, dict) else None
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

    evidence = [
        {
            "id": "tdx_sector_catalog",
            "source": "TDX 数据 API / sectors",
            "status": "available" if matched else "missing",
            "data_as_of": catalog_dates[-1] if catalog_dates else None,
            "supports": "板块名称与分类",
            "does_not_support": "公司业务角色、订单、客户、产能或投资结论",
        },
        {
            "id": "tdx_sector_members",
            "source": "TDX 数据 API / sector_members",
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
            "status": "missing",
            "needed": ["公告/年报业务收入占比", "订单与出货", "产能利用率", "客户验证"],
        },
    ]
    if not matched:
        data_gaps.insert(0, {
            "dimension": "TDX 板块匹配",
            "status": "missing",
            "needed": [f"与“{subject}”一致的 TDX 行业/概念名称或显式别名"],
        })
    if matched and not all_member_codes:
        data_gaps.insert(0, {
            "dimension": "TDX 成分关系",
            "status": "missing",
            "needed": ["匹配板块的 sector_members 数据"],
        })
    if all_member_codes and not companies:
        data_gaps.append({
            "dimension": "成员行情",
            "status": "missing",
            "needed": ["TDX 实时或收盘快照", "腾讯行情降级源"],
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
            "sector_market": next(
                (row.get("data_as_of") for row in market_rows if row.get("data_as_of")),
                None,
            ),
        },
        "conclusion": (
            f"已用 TDX 验证 {len(matched)} 个相关板块、{len(all_member_codes)} 只去重成员；"
            "这只能建立研究候选集合，不能证明供应链角色、业绩兑现或可交易性。"
            if matched
            else f"TDX 目录未匹配“{subject}”；本轮只保留研究框架，不生成公司排名。"
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
                        membership.get("by_sector", {}).get(item.get("code"), ())
                        if isinstance(membership, dict)
                        else ()
                    ) or ()
                ),
            }
            for item in matched
        ],
        "sector_market": market_rows,
        "company_mapping": companies,
        "coverage": {
            "matched_sector_count": len(matched),
            "tdx_member_count": len(all_member_codes),
            "quote_requested_count": min(len(all_member_codes), 300),
            "quoted_count": sum(
                1
                for code in all_member_codes
                if isinstance(quote_data.get(code), dict)
                and _number((quote_data.get(code) or {}).get("price")) is not None
            ),
            "displayed_company_count": len(companies),
        },
        "realization_and_valuation": {
            "realization_status": "not_verified",
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
            "TDX 板块口径或成分数据日期变化时，必须重跑成员集合与研究顺序。",
        ],
        "evidence": evidence,
        "data_gaps": data_gaps,
    }


async def _bounded_call(fn, *args, timeout: float, default):
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
    except Exception:  # noqa: BLE001 - 各数据源内部记录详细错误，聚合层只降级
        return default


async def collect_analysis(industry: str, *, company_limit: int = 20) -> dict:
    """按交互预算采集一次产业链证据并构造共享契约。"""
    catalog, sector_market = await asyncio.gather(
        _bounded_call(tdx_api.sector_catalog, timeout=7.0, default={}),
        _bounded_call(market.market_sectors, timeout=5.0, default={}),
    )
    matched = match_sector_catalog(industry, catalog)
    sector_codes = [str(item.get("code") or "") for item in matched if item.get("code")]
    membership = await _bounded_call(
        tdx_api.sector_members_for_codes,
        sector_codes,
        timeout=9.0,
        default={},
    ) if sector_codes else {}
    member_codes = _member_codes(matched, membership)
    quote_codes = member_codes[:300]
    quotes = await _bounded_call(
        stock.quick_quotes,
        quote_codes,
        timeout=5.0,
        default={},
    ) if quote_codes else {}
    return build_analysis(
        industry,
        catalog=catalog,
        membership=membership,
        quotes=quotes,
        sector_market=sector_market,
        company_limit=company_limit,
    )


def _fmt_number(value: Any, digits: int = 2) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:.{digits}f}"


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
        f"板块行情 {as_of.get('sector_market') or '缺失'}。",
        f"> 来源覆盖：{source_summary}。",
        f"> 页首缺口：{gap_summary}。",
    ]


def _company_table_lines(analysis: dict) -> list[str]:
    lines = [
        "|核验序|代码|名称|TDX板块|现价|涨跌幅|PE|PB|数据可研分|",
        "|---:|---|---|---|---:|---:|---:|---:|---:|",
    ]
    companies = analysis.get("company_mapping") or []
    for row in companies[:10]:
        sectors = " / ".join(
            str(item.get("name") or "") for item in (row.get("matched_sectors") or [])
        ) or "—"
        quote = row.get("quote") or {}
        valuation = row.get("valuation") or {}
        lines.append(
            f"|{row.get('evidence_verification_priority') or '—'}|{row.get('code') or '—'}|"
            f"{row.get('name') or '名称待行情补齐'}|{sectors}|"
            f"{_fmt_number(quote.get('price'))}|{_fmt_number(quote.get('change_pct'))}%|"
            f"{_fmt_number(valuation.get('pe'))}|{_fmt_number(valuation.get('pb'))}|"
            f"{_fmt_number(row.get('data_researchability_score'))}|"
        )
    if not companies:
        lines.append("|—|—|—|未取得可验证的 TDX 成分关系|—|—|—|—|—|")
    lines.extend([
        "",
        "- “数据可研分”=TDX成员验证 + 字段覆盖 + 成交额数据可得性；"
        "**只用于决定先核验谁，不是产业研究优先级、投资排名或买入排名。**",
        "- 公司产业链角色、相关收入、订单与国产替代仍须公告/财报证明；TDX 成分本身不作业务背书。",
    ])
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
        "- TDX 匹配板块："
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


def render_markdown(analysis: dict) -> str:
    """模型不可用时输出诚实的八段骨架，不用空缺数据伪造深度研究。"""
    lines = [
        *_header_lines(analysis),
        "",
        "### 1. 需求本质",
        f"- {((analysis.get('demand_essence') or {}).get('current_conclusion') or '数据缺失')}",
        f"- 核验问题：{((analysis.get('demand_essence') or {}).get('question') or '—')}",
        "",
        "### 2. 产业地图",
    ]
    for stage in analysis.get("chain_structure") or []:
        lines.append(
            f"- **{stage.get('label') or stage.get('stage')}**："
            f"{stage.get('research_question') or '待核验'}（{stage.get('status') or 'unknown'}）"
        )
    lines.extend([
        "",
        "### 3. 价值捕获",
        f"- 价值捕获：{((analysis.get('value_capture') or {}).get('current_conclusion') or '待核验')}",
        f"- 排序维度：{'、'.join((analysis.get('value_capture') or {}).get('ranking_dimensions') or []) or '待核验'}。",
        "",
        "### 4. 卡点",
        f"- 卡点：{((analysis.get('bottlenecks') or {}).get('current_conclusion') or '待核验')}",
        f"- 核验顺序：{'、'.join((analysis.get('bottlenecks') or {}).get('verification_order') or []) or '待核验'}。",
        "",
        "### 5. 国产替代",
        f"- 国产替代：{((analysis.get('domestic_substitution') or {}).get('current_conclusion') or '待核验')}",
        "",
        "### 6. 公司映射（TDX 真实成分约束）",
        *_company_table_lines(analysis),
        "",
        "### 7. 兑现与估值",
        f"- {((analysis.get('realization_and_valuation') or {}).get('note') or '数据缺失')}",
        "",
        "### 8. 研究优先级与失效条件",
        "- 当前模型不可用，无法基于公开产业常识形成有内容的定性优先级；"
        "下列仅是**证据核验队列**，不得当产业或投资排名。",
    ])
    for row in analysis.get("evidence_verification_queue") or []:
        lines.append(
            f"- P{row.get('priority')}：{row.get('name') or row.get('code')}（{row.get('code')}）"
            "—先核业务角色/收入占比，再核订单与毛利，最后做同环节估值。"
        )
    if not analysis.get("evidence_verification_queue"):
        lines.append("- 暂无证据核验队列；先解决板块匹配或成分数据缺口。")
    lines.extend(["", "**失效条件**"])
    lines.extend(f"- {item}" for item in (analysis.get("invalidation_conditions") or []))
    lines.extend(["", *_footer_lines(analysis)])
    return "\n".join(lines)
