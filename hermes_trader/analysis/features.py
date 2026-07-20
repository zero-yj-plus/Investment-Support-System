"""市场特征引擎 — 题材赚钱效应 / 交易拥挤度 / 市场主线 / 情绪龙头梯队。

设计参考：飞书《数据看板需求整理》+ nfquant 特征流水线（环境→主线→风格→赚钱效应
→个股辨识度→拥挤度）。所有指标基于 market.py 已采集的真实数据计算，算法口径透明、
参数可调；任何拿不到的因子进入 missing，绝不编造。
"""
from __future__ import annotations

from ..data import market
from ..data.base import get_ak, safe, to_float, to_int, ts_str, ttl_cache, em_available, em_report

# AI/半导体主线相关概念板块（机构容量票聚焦范围，参考飞书需求⑥）
CURATED_CONCEPTS = [
    "CPO", "光通信", "算力", "液冷", "半导体", "存储芯片",
    "PCB", "先进封装", "AI算力", "人工智能", "光模块", "服务器",
]
# 机构可参与的流通市值区间（太小机构进不去/易被举牌，太大弹性差）
FMV_FLOOR = 80e8
FMV_CEIL = 3000e8


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _match_zt_by_reason(sector_name: str, zt_stocks: list[dict]) -> list[dict]:
    """涨停池按『所属行业』匹配某板块（行业板块用，概念板块通常匹配不到）。"""
    if not sector_name:
        return []
    matched = []
    for stock in zt_stocks:
        industry = str(stock.get("industry") or "").strip()
        reason = str(stock.get("reason") or "").strip()
        if industry == sector_name or (reason and (sector_name in reason or reason in sector_name)):
            matched.append(stock)
    return matched


# ------------------------------------------------------------ 题材赚钱效应

def theme_money_effect(top_n: int = 25) -> dict:
    """各题材（行业+概念）赚钱效应排名。
    money_score（signed，正=赚钱/负=亏钱）= 板块涨跌幅×权重 + 涨停贡献 + 上涨家数占比 + 最高连板。"""
    missing: list[str] = []
    out: dict = {
        "ok": True, "ts": ts_str(), "missing": missing, "themes": [],
        "missing_fields": [], "degraded_factors": [], "notes": [],
    }

    sectors = market.market_sectors()
    limitup = market.market_limitup()
    if not sectors.get("sectors"):
        missing.append("板块行情")
        missing.extend(m for m in sectors.get("missing", []) if m not in missing)
        out["missing_fields"] = ["板块行情"]
        out["data_state"] = "missing"
        return out
    # 透传 market_sectors 的全部降级标记：新浪行业兜底时会 append『行业涨跌家数』(market.py)，
    # 该串不含『板块/近似』关键词，旧的关键词过滤会把它丢弃，导致 up_ratio 静默缺失、
    # 赚钱分少算『上涨家数占比』分量而前端/模型看不到任何提示。改为整体透传去重。
    missing.extend(m for m in sectors.get("missing", []) if m not in missing)
    # 透传涨停池失败标记：接口失败时 market_limitup 返回 stocks=[] + missing=['涨停池数据']，
    # 若不透传则每个行业题材 zt_count=len([])=0 会把『接口失败』伪装成『真实零涨停』(数据真实性铁律)。
    missing.extend(m for m in limitup.get("missing", []) if m not in missing)
    zt_stocks = limitup.get("stocks", [])
    zt_failed = not isinstance(zt_stocks, list) or (
        not zt_stocks and any(
            marker in str(message)
            for message in limitup.get("missing", [])
            for marker in ("涨停池数据", "涨停连板梯队明细", "涨停明细不可用")
        )
    )
    out["data_date"] = limitup.get("data_date")

    zt_stocks = zt_stocks if isinstance(zt_stocks, list) else []
    themes = []
    for s in sectors["sectors"]:
        name = s.get("name", "")
        chg = s.get("change_pct")
        if chg is None:
            continue
        # 涨停池失败时行业题材 zt_count 置 None（区别于真实 0 涨停），跳过涨停/连板加分
        matched = _match_zt_by_reason(name, zt_stocks) if (s.get("kind") == "industry" and not zt_failed) else []
        zt_count = (len(matched) if not zt_failed else None) if s.get("kind") == "industry" else None
        max_lb = max((m.get("lianban") or 1 for m in matched), default=0) if matched else 0
        up, down = s.get("up_count"), s.get("down_count")
        up_ratio = (up / (up + down)) if (up is not None and down is not None and (up + down) > 0) else None

        score = _clamp(chg * 6, -45, 45)
        if zt_count:
            score += _clamp(zt_count * 2.5, 0, 25)
        if up_ratio is not None:
            score += _clamp((up_ratio - 0.5) * 30, -15, 15)
        if max_lb >= 2:
            score += _clamp((max_lb - 1) * 4, 0, 16)
        factor_inputs = {
            "change_pct": True,
            "breadth": up_ratio is not None,
        }
        if s.get("kind") == "industry":
            factor_inputs.update({
                "limitup_cluster": zt_count is not None,
                "board_height": zt_count is not None,
            })
        complete_factors = sum(1 for available in factor_inputs.values() if available)
        themes.append({
            "name": name,
            "kind": s.get("kind"),
            "change_pct": chg,
            "money_score": round(score, 1),
            "zt_count": zt_count,
            "max_lianban": max_lb or None,
            "up_count": up,
            "down_count": down,
            "leader_name": s.get("leader_name"),
            "leader_change_pct": s.get("leader_change_pct"),
            "main_flow": s.get("main_flow"),
            "amount": s.get("amount"),
            "score_coverage": {
                "complete": complete_factors,
                "total": len(factor_inputs),
                "state": "complete" if complete_factors == len(factor_inputs) else "partial",
                "missing": [key for key, available in factor_inputs.items() if not available],
            },
        })
    # 完整因子与部分因子的分数不等置：先排完整样本，同一覆盖状态内再按分数排序。
    themes.sort(key=lambda t: (
        t.get("score_coverage", {}).get("state") != "complete",
        -t["money_score"],
    ))
    if not themes:
        missing.append("板块涨跌数据")
        out["missing_fields"] = ["板块涨跌数据"]
        out["data_state"] = "missing"
        return out
    # 取赚钱效应最强的 top_n 与最差的若干（亏钱效应）
    out["themes"] = themes[:top_n]
    # 亏钱榜从 top_n 之后的剩余里取尾部，天然不与赚钱榜交叠：旧写法 themes[-8:] 在
    # top_n<len<=top_n+8 时会与头部重叠，同一题材同时上两榜；并只保留真正净亏(score<0)的题材。
    tail = themes[top_n:]
    out["losers"] = [t for t in tail[-8:][::-1] if t["money_score"] < 0]
    industry_rows = [row for row in out["themes"] if row.get("kind") == "industry"]
    concept_rows = [row for row in out["themes"] if row.get("kind") == "concept"]
    industry_complete = sum(
        1 for row in industry_rows
        if row.get("zt_count") is not None
        and row.get("up_count") is not None
        and row.get("down_count") is not None
        and row.get("amount") is not None
    )
    degraded: list[str] = []
    if industry_complete < len(industry_rows):
        degraded.append(f"行业辅助因子完整{industry_complete}/{len(industry_rows)}")
    concept_breadth = sum(
        1 for row in concept_rows
        if row.get("up_count") is not None and row.get("down_count") is not None
    )
    if concept_rows and concept_breadth < len(concept_rows):
        degraded.append(f"概念涨跌家数覆盖{concept_breadth}/{len(concept_rows)}")
    if zt_failed:
        degraded.append("涨停聚集因子暂不可用")
    notes = list(dict.fromkeys([
        *(sectors.get("notes") or []),
        *(limitup.get("notes") or []),
        *missing,
        "主力资金流未进入赚钱分；末列展示可核验成交额",
    ]))
    out["degraded_factors"] = degraded
    out["notes"] = notes
    out["coverage"] = {
        "themes": {"complete": len(out["themes"]), "total": len(out["themes"])},
        "industry_factors": {"complete": industry_complete, "total": len(industry_rows)},
        "concept_breadth": {"complete": concept_breadth, "total": len(concept_rows)},
    }
    out["score_semantics"] = {
        "name": "available_factor_score",
        "comparable_when": "score_coverage.state=complete",
        "warning": "因子不全时分数仅表示可用因子，不与完整因子分数等置比较",
    }
    out["data_state"] = "degraded" if degraded else "complete"
    return out


# ------------------------------------------------------------ 交易拥挤度

def crowdedness(top_n: int = 25) -> dict:
    """各题材交易拥挤度（高=过热/反转风险，低=冷清）。
    口径：板块成交额占两市比为主因，叠加涨停占比与涨跌幅极端度，做横截面分位。"""
    missing: list[str] = []
    out: dict = {
        "ok": True, "ts": ts_str(), "missing": missing, "sectors": [],
        "missing_fields": [], "degraded_factors": [], "notes": [],
    }

    sectors = market.market_sectors()
    overview = market.market_overview()
    limitup = market.market_limitup()
    # data_date 须在任何早退之前落定，否则板块为空早退时响应缺 data_date，违反契约第11节
    out["data_date"] = overview.get("data_date")
    if not sectors.get("sectors"):
        missing.append("板块行情")
        out["missing_fields"] = ["板块行情"]
        out["data_state"] = "missing"
        return out
    total_amount = overview.get("total_amount")
    if total_amount is None:
        missing.append("两市成交额(拥挤度占比基准缺失，改用板块间相对成交额)")

    zt_stocks = limitup.get("stocks", [])
    # 涨停池失败时聚集度分量缺失，标注而非用 0 涨停冒充（与 theme_money_effect 同口径）
    zt_failed = not isinstance(zt_stocks, list) or (
        not zt_stocks and any(
            marker in str(message)
            for message in limitup.get("missing", [])
            for marker in ("涨停池数据", "涨停连板梯队明细", "涨停明细不可用")
        )
    )
    if zt_failed:
        missing.append("涨停聚集度缺失(涨停池接口失败，拥挤度未计涨停聚集分量)")
    rows = []
    amounts = [s.get("amount") for s in sectors["sectors"] if s.get("amount")]
    amt_base = total_amount or (sum(amounts) if amounts else None)
    has_amount = bool(amounts)
    if not has_amount:
        # 降级：板块成交额不可用时，拥挤度退化为『涨停聚集度×涨幅极端度』简化口径，明确标注。
        missing.append("板块成交额缺失，拥挤度为简化口径(仅涨停聚集+涨幅极端度，无成交额占比)")
    for s in sectors["sectors"]:
        chg = s.get("change_pct")
        if chg is None:
            continue
        amt = s.get("amount")
        share = (amt / amt_base * 100) if (amt and amt_base) else None
        zt_count = (
            len(_match_zt_by_reason(s.get("name", ""), zt_stocks))
            if s.get("kind") == "industry" and not zt_failed
            else None
        )
        # 有成交额：以占比为主因；无成交额：用涨停聚集+涨幅极端度作代理
        raw = ((share or 0) * (1 + min(zt_count or 0, 10) * 0.08) * (1 + abs(chg) / 20)
               if has_amount
               else (min(zt_count or 0, 12) * 1.5 + abs(chg) * 0.6))
        rows.append({
            "name": s.get("name"), "kind": s.get("kind"),
            "change_pct": chg, "amount": amt, "amt_share": round(share, 2) if share else None,
            "zt_count": zt_count, "_raw": raw,
            "score_coverage": {
                "complete": sum((chg is not None, bool(amt and amt_base), zt_count is not None)),
                "total": 3,
                "state": "complete" if chg is not None and bool(amt and amt_base) and zt_count is not None else "partial",
            },
        })
    if not rows:
        missing.append("板块行情(无可用涨跌数据)")
        out["missing_fields"] = ["板块行情(无可用涨跌数据)"]
        out["data_state"] = "missing"
        return out
    # 横截面分位 → 拥挤分 0-100
    raws = sorted(r["_raw"] for r in rows)
    n = len(raws)
    for r in rows:
        rank = sum(1 for x in raws if x <= r["_raw"])
        r["crowd_score"] = round(rank / n * 100)
        r["level"] = "过热拥挤" if r["crowd_score"] >= 85 else ("偏拥挤" if r["crowd_score"] >= 65 else ("正常" if r["crowd_score"] >= 30 else "冷清"))
        r.pop("_raw", None)
    rows.sort(key=lambda r: -r["crowd_score"])
    out["sectors"] = rows[:top_n]
    industry_rows = [row for row in out["sectors"] if row.get("kind") == "industry"]
    industry_zt = sum(1 for row in industry_rows if row.get("zt_count") is not None)
    degraded: list[str] = []
    if total_amount is None:
        degraded.append("两市成交额基准缺失，使用板块相对成交额")
    if not has_amount:
        degraded.append("板块成交额缺失，使用简化口径")
    if zt_failed:
        degraded.append("涨停聚集因子暂不可用")
    if industry_zt < len(industry_rows):
        degraded.append(f"行业涨停聚集覆盖{industry_zt}/{len(industry_rows)}")
    if any(row.get("kind") == "concept" for row in out["sectors"]):
        degraded.append("概念拥挤度未计涨停聚集分量")
    out["degraded_factors"] = list(dict.fromkeys(degraded))
    out["notes"] = list(dict.fromkeys([
        *(sectors.get("notes") or []),
        *(limitup.get("notes") or []),
        *missing,
    ]))
    out["coverage"] = {
        "rows": {"complete": len(out["sectors"]), "total": len(out["sectors"])},
        "industry_limitup_factor": {"complete": industry_zt, "total": len(industry_rows)},
    }
    out["score_semantics"] = {
        "name": "cross_section_percentile",
        "comparable_when": "score_coverage.state=complete",
        "warning": "因子不全时仅表示当前可用因子的横截面分位，不代表完整拥挤度",
    }
    out["data_state"] = "degraded" if out["degraded_factors"] else "complete"
    return out


# ------------------------------------------------------------ 情绪龙头梯队

def sentiment_leaders() -> dict:
    """情绪龙头辨识：空间龙头、连板梯队、卡位龙、人气榜。"""
    missing: list[str] = []
    out: dict = {"ok": True, "ts": ts_str(), "missing": missing, "kongban": None, "ladder": [], "popularity": []}

    limitup = market.market_limitup()
    stocks = limitup.get("stocks", [])
    out["data_date"] = limitup.get("data_date")
    missing.extend(m for m in limitup.get("missing", []) if m not in missing)
    if not stocks:
        missing.append("涨停池")
        return out

    known = [s for s in stocks if isinstance(s.get("lianban"), (int, float)) and s.get("lianban") > 0]
    max_lb = max((int(s["lianban"]) for s in known), default=None)
    if max_lb is not None:
        kongban = [s for s in known if int(s["lianban"]) == max_lb]
        out["kongban"] = {
            "height": max_lb,
            "stocks": [{"code": s["code"], "name": s["name"], "lianban": s.get("lianban"),
                        "reason": s.get("reason"), "seal_amount": s.get("seal_amount")} for s in kongban[:5]],
        }
    # 连板梯队（按高度分组，每组取封单最大的代表）
    groups: dict[int, list] = {}
    for s in known:
        groups.setdefault(int(s["lianban"]), []).append(s)
    ladder = []
    for h in sorted(groups, reverse=True):
        members = sorted(groups[h], key=lambda s: -(s.get("seal_amount") or 0))
        ladder.append({
            "lianban": h,
            "label": f"{h}连板" if h > 1 else "首板",
            "count": len(members),
            "stocks": [{"code": m["code"], "name": m["name"], "reason": m.get("reason"),
                        "seal_amount": m.get("seal_amount"), "turnover_rate": m.get("turnover_rate"),
                        "first_time": m.get("first_time"), "is_leader": (h == max_lb)} for m in members[:8]],
        })
    out["ladder"] = ladder
    if not ladder:
        members = sorted(stocks, key=lambda s: -(s.get("amount") or 0))
        out["ladder"] = [{
            "lianban": None,
            "label": "封板池 · 板位待补",
            "count": len(members),
            "stocks": [{
                "code": m["code"], "name": m["name"], "reason": m.get("reason"),
                "seal_amount": m.get("seal_amount"), "turnover_rate": m.get("turnover_rate"),
                "first_time": m.get("first_time"), "is_leader": False,
            } for m in members[:12]],
        }]
    # 人气榜：封单额 / 流通市值 作为承接强度近似
    pop = []
    for s in stocks:
        seal, fmv = s.get("seal_amount"), s.get("float_mv")
        if seal is None:
            continue
        ratio = (seal / fmv) if (seal and fmv) else None
        pop.append({"code": s["code"], "name": s["name"], "lianban": s.get("lianban"),
                    "seal_amount": seal, "seal_ratio": round(ratio * 100, 2) if ratio else None,
                    "reason": s.get("reason")})
    pop.sort(key=lambda p: -(p["seal_amount"] or 0))
    out["popularity"] = pop[:15]
    return out


# ------------------------------------------------------------ 市场主线（多日）

def market_mainline(store, days: int = 5) -> dict:
    """市场主线量化：基于按日累积的题材赚钱效应快照，找持续占据强势榜的主线。
    强度 = 上榜天数(持续性) + 平均赚钱分；资金流只展示、不参与主线强度。"""
    missing: list[str] = []
    out: dict = {
        "ok": True, "ts": ts_str(), "missing": missing,
        "missing_fields": [], "degraded_factors": [], "notes": [],
        "mainlines": [], "switch_signal": None,
    }

    # 先落今日快照 —— 必须按数据归属交易日(data_date)落库，不能用日历日：
    # 否则周末/盘前会把同一份上一交易日数据按多个日历日重复入库，伪造出虚假的「持续天数」。
    today_me = theme_money_effect(top_n=15)
    day_key = today_me.get("data_date")  # _resolve_data_day 已保证其为真实交易日（盘前/非交易日回退到上一交易日）
    if store is not None and day_key and today_me.get("themes"):
        snap = [{"name": t["name"], "kind": t["kind"], "money_score": t["money_score"],
                 "main_flow": t.get("main_flow"), "change_pct": t.get("change_pct")}
                for t in today_me["themes"][:15]]
        # 同一 data_date 重复调用为幂等覆盖，盘中多次刷新只保留该交易日最新快照。
        store.save_sector_snapshot(day_key, snap)
    elif not day_key:
        missing.append("题材快照缺数据归属日(data_date)，本次不入库以免污染主线持续性统计")
        out["missing_fields"].append("题材快照缺数据归属日")

    history = store.sector_history(days) if store is not None else []
    if len(history) < 2:
        out["note"] = "市场主线需 ≥2 个交易日数据累积，当前仅单日快照——先看今日题材赚钱效应"
        out["today_top"] = today_me.get("themes", [])[:8]
        out["history_days"] = len(history)
        out["notes"] = list(dict.fromkeys([
            out["note"],
            *(today_me.get("notes") or []),
        ]))
        out["degraded_factors"] = list(today_me.get("degraded_factors") or [])
        out["data_state"] = "missing" if out["missing_fields"] else "accumulating"
        out["state_label"] = f"已积累{len(history)}/2日"
        return out

    # 聚合：每个题材的上榜天数、累计分、累计资金流
    # 键用 (kind, name) 复合：东财同名『半导体』可同时是行业与概念板块，按名合并会把两类
    # days/score/flow 混加、kind 取首次出现者，污染主线统计——故按类型分开累计。
    agg: dict[tuple, dict] = {}
    for day in history:
        for rank, t in enumerate(day.get("themes", [])):
            name = t.get("name")
            if not name:
                continue
            key = (t.get("kind"), name)
            a = agg.setdefault(key, {"name": name, "kind": t.get("kind"), "days": 0,
                                     "score_sum": 0.0, "flow_sum": 0.0, "flow_days": 0,
                                     "rank_sum": 0, "recent_score": 0})
            a["days"] += 1
            a["score_sum"] += t.get("money_score") or 0
            # 资金流三态：概念板块 main_flow 恒为 None（东财概念榜无主力资金字段），
            # 须区分『净流出(负)』与『无数据(None)』——只累计真实存在的值，并计其天数。
            mf = t.get("main_flow")
            if mf is not None:
                a["flow_sum"] += mf
                a["flow_days"] += 1
            a["rank_sum"] += rank + 1
            a["recent_score"] = t.get("money_score") or 0
    mainlines = []
    for a in agg.values():
        has_flow = a["flow_days"] > 0
        cum_flow = a["flow_sum"] if has_flow else None
        # 主线强度只看持续性与赚钱效应；无效/不稳定的资金字段不改变排序。
        strength = a["days"] * 10 + (a["score_sum"] / max(a["days"], 1))
        item = {
            "name": a["name"], "kind": a["kind"], "days_on_board": a["days"],
            "avg_money_score": round(a["score_sum"] / max(a["days"], 1), 1),
            "cum_main_flow": cum_flow, "strength": round(strength, 1),
            "recent_score": a["recent_score"],
        }
        if not has_flow:
            item["flow_note"] = "无资金流数据（概念板块东财不提供主力净额）"
        mainlines.append(item)
    mainlines.sort(key=lambda m: -m["strength"])
    out["mainlines"] = mainlines[:10]
    out["history_days"] = len(history)
    # 切换信号：今日榜首 vs 历史主线榜首
    if mainlines and today_me.get("themes"):
        hist_top = mainlines[0]["name"]
        today_top = today_me["themes"][0]["name"]
        if today_top != hist_top:
            out["switch_signal"] = f"今日赚钱效应榜首『{today_top}』与近{len(history)}日主线『{hist_top}』不一致，关注主线是否切换"
    out["degraded_factors"] = list(today_me.get("degraded_factors") or [])
    out["notes"] = list(today_me.get("notes") or [])
    out["coverage"] = {"history_days": {"complete": len(history), "total": max(2, len(history))}}
    out["data_state"] = "degraded" if out["degraded_factors"] else "complete"
    return out


# ------------------------------------------------------------ 机构容量票（AI/半导体）

@ttl_cache(24 * 3600)
def _concept_constituents(board: str) -> dict[str, str]:
    """概念板块成分股 code→所属概念（24h 缓存，避免反复打东财）。"""
    ak = get_ak()
    if ak is None:
        return {}
    if not em_available():   # 东财熔断期内直接跳过(该接口同属被掐的板块类,详见 base 熔断器)
        return {}
    df = safe(ak.stock_board_concept_cons_em, symbol=board, label=f"概念成分:{board}")
    em_report(df is not None)
    if df is None or df.empty or "代码" not in df.columns:
        return {}
    try:
        return {str(r["代码"]).zfill(6): board for _, r in df.iterrows()}
    except Exception:
        return {}


def institutional_capacity(top_n: int = 30) -> dict:
    """机构容量票筛选：聚焦 AI/半导体概念，流通市值达标 + 流动性充足 +
    所属板块资金/涨幅强 的机构可参与标的。基于成分股映射 + 全市场快照，不逐票打接口。"""
    missing: list[str] = []
    out: dict = {"ok": True, "ts": ts_str(), "missing": missing, "stocks": []}

    # 直连机(无中继)上东财全市场快照被封会拖 ~160s；机构容量筛选必须基于全市场快照、无近似口径，
    # 故直连机直接落到下方"快照不可用"的缺失标注分支(如实告知不可用)，绝不触发那条死链。
    spot = market._spot_all() if market._relay_api() else None
    if spot is None or spot.empty:
        missing.append("全市场快照(机构容量筛选依赖)")
        return out
    sectors = {s.get("name"): s for s in market.market_sectors().get("sectors", [])}
    out["data_date"] = market.market_overview().get("data_date")

    # 汇总目标概念成分股 → 概念名（一只票可能命中多个概念，保留板块最强的）
    code2concept: dict[str, str] = {}
    got_any = False
    for board in CURATED_CONCEPTS:
        cons = _concept_constituents(board)
        if cons:
            got_any = True
        for code, _b in cons.items():
            # 已有则保留所属板块涨幅更高者
            prev = code2concept.get(code)
            if prev is None:
                code2concept[code] = board
            else:
                pc = sectors.get(prev, {}).get("change_pct") or -999
                bc = sectors.get(board, {}).get("change_pct") or -999
                if bc > pc:
                    code2concept[code] = board
    if not got_any:
        missing.append("概念成分股(东财不可用，机构容量票暂不可筛)")
        return out

    import pandas as pd  # noqa: PLC0415

    rows = []
    for _, r in spot.iterrows():
        code = str(r.get("代码", "")).zfill(6)
        concept = code2concept.get(code)
        if not concept:
            continue
        name = str(r.get("名称", ""))
        if "ST" in name.upper() or "退" in name:
            continue
        fmv = to_float(r.get("流通市值"))
        amt = to_float(r.get("成交额"))
        chg = to_float(r.get("涨跌幅"))
        tr = to_float(r.get("换手率"))
        if fmv is None or amt is None or chg is None:
            continue
        if not (FMV_FLOOR <= fmv <= FMV_CEIL):
            continue
        if amt < 3e8:  # 流动性门槛：日成交额 ≥3 亿，机构能进出
            continue
        # 评分：板块强度 + 个股动量 + 流动性 + 市值甜区 + 换手健康度
        sec = sectors.get(concept, {})
        board_chg = sec.get("change_pct") or 0
        board_flow = sec.get("main_flow") or 0
        score = 50.0
        score += _clamp(chg * 2.0, -15, 15)
        score += _clamp(board_chg * 1.5, -10, 12)
        score += 8 if board_flow and board_flow > 0 else (-5 if board_flow and board_flow < 0 else 0)
        score += _clamp((amt / 1e8 - 3) * 0.5, 0, 12)  # 流动性越好越高
        # 市值甜区 150-1200 亿加分，过大减分
        if 150e8 <= fmv <= 1200e8:
            score += 8
        elif fmv > 2000e8:
            score -= 6
        # 换手健康：5-20% 最佳，过热(>30)或过冷(<2)扣分
        if tr is not None:
            if 5 <= tr <= 20:
                score += 5
            elif tr > 30 or tr < 2:
                score -= 5
        trend = "强势" if (chg > 3 and board_chg > 0) else ("活跃" if amt > 1e9 else "震荡")
        rows.append({
            "code": code, "name": name, "concept": concept,
            "price": to_float(r.get("最新价")), "change_pct": chg,
            "float_mv": fmv, "amount": amt, "turnover_rate": tr,
            "main_net": None, "trend": trend, "score": round(_clamp(score, 0, 100)),
        })
    missing.append("个股主力净流入(避免逐票打接口未取，main_net为空)")
    rows.sort(key=lambda x: -x["score"])
    out["stocks"] = rows[:top_n]
    if not rows:
        missing.append("无满足机构容量条件的标的(或成分/市值数据不全)")
    return out
