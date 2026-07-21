"""数据源健康面板 — 汇总所有数据源的来源、用途、主/备、实时延迟与状态。

延迟与成败来自 base._record_source 的被动埋点（safe() 每次调用记录），
不为面板产生额外请求；Hermes 与中台 /spot 的状态由路由层即时探测后传入。
"""
from __future__ import annotations

import os

from .base import source_metrics, now_cn
from .deployment import source_route_policy

# 数据源目录：按**实际字段链路**而不是按历史宣传页维护。
#
# ``match`` 是 safe()/适配器的精确或前缀标签；``match_contains`` 用于业务名与
# 供应商混合在标签中的调用。一个源状态只说明“最近一次相关调用”结果，
# 不表示它已覆盖所有字段；前端同时展示 feeds 和 role，避免把备用源误读成全量主源。
CATALOG = [
    {"key": "tdx_api", "name": "TDX 数据 API", "provider": "客户授权 TDX API v2.8",
     "feeds": "秒级价量与买一/卖一、全市场宽度、行业/概念关系、板块资金排名、K线与基础财务",
     "role": "客户授权主行情与行业关系源；板块资金原值单位未核验，只用排名/方向；失败退腾讯/公开板块源",
     "match_contains": ["TDX API"]},
    {"key": "universe", "name": "全 A 股股票池", "provider": "沪深京交易所官网 / TDX 数据 API / last-good",
     "feeds": "股票代码、名称、全市场筛选分母", "role": "免费基础名录，缺交易所/陈旧会显式标记",
     "match_contains": ["股票代码表("]},
    {"key": "tencent", "name": "腾讯行情", "provider": "qt.gtimg.cn",
     "feeds": "A股行情/估值、指数、港美股行情", "role": "免费带供应商时钟备源；补 PE/PB/换手/量比/市值", "match": ["腾讯行情"]},
    {"key": "public_structure", "name": "公开市场结构", "provider": "东方财富 / 同花顺公开接口（经 AkShare / 直连）",
     "feeds": "涨停、跌停、炸板、连板梯队、涨停原因", "role": "免费盘中/盘后结构源，失败如实标缺",
     "match_contains": ["涨跌停池(", "涨停原因("]},
    {"key": "public_fundamentals", "name": "公开基本面备用", "provider": "新浪 / 东方财富公开接口（经 AkShare）",
     "feeds": "财务指标备用、业绩预告", "role": "TDX 基础财务不可用时的低频备用，不参与交互热路径",
     "match": ["财务指标(新浪)", "业绩预告(公开源)"]},
    {"key": "public_flow", "name": "公开资金流", "provider": "新浪 / 同花顺公开接口（经 AkShare / 直连）",
     "feeds": "个股主力资金、全市场资金榜、行业资金", "role": "免费资金证据源，不以成交额伪装净流入",
     "match": ["个股资金流(新浪)", "个股资金流排名(公开源)", "行业资金流(同花顺隔离)"]},
    {"key": "eastmoney", "name": "东方财富", "provider": "东方财富 / 境内中继",
     "feeds": "全市场快照、板块、快讯、海外财务", "role": "免费可选备用源（需中继或直连可用）",
     "match": ["全市场快照", "涨停池", "跌停池", "炸板池", "行业板块", "概念板块", "东财全球快讯"]},
    {"key": "legu", "name": "乐咕乐股", "provider": "乐咕乐股",
     "feeds": "涨跌家数、涨跌停家数、市场活跃度", "role": "市场宽度兜底", "match": ["市场活跃度(乐咕)"]},
    {"key": "ths", "name": "同花顺", "provider": "同花顺公开接口（经 AkShare / 直连）",
     "feeds": "行业资金流、涨停原因、财经快讯", "role": "免费板块/研究证据源",
     "match": ["行业资金流(同花顺隔离)", "涨停原因(同花顺)", "快讯:ths"]},
    {"key": "sina", "name": "新浪财经", "provider": "新浪（经 AkShare）",
     "feeds": "指数、行情、分时日K、个股资金、财务、期货与期权", "role": "免费行情/资金/基本面备用源",
     "match_contains": ["新浪"]},
    {"key": "news", "name": "财经快讯聚合", "provider": "TDX 舆情 / 财联社 / 东方财富 / 同花顺 / 富途 / 新浪",
     "feeds": "盘前消息面、事件交叉验证", "role": "财联社保留原始发布方身份；其余采集端未知上游出处，多端命中不等于独立互证；非交易信号",
     "match": ["财联社电报", "东财全球快讯", "快讯:em", "快讯:ths", "快讯:futu", "快讯:sina"],
     "match_contains": ["TDX API:news_sentiment"]},
    {"key": "gdelt", "name": "全球事件元数据", "provider": "GDELT DOC 2.0 官方开放接口",
     "feeds": "全球新闻标题、发布时间、来源域名与原文链接",
     "role": "海外部署默认启用的低频研究增强；仅存元数据，不进入A股报价与交易闸门",
     "match": ["GDELT全球事件"]},
    {"key": "cls_news", "name": "财联社独立快讯", "provider": "财联社官网页面数据",
     "feeds": "独立 7×24 电报流", "role": "官网 v1 页面 adapter，无 API SLA；仅真实调用成功才显示可用",
     "match": ["财联社电报", "快讯:cls"]},
    {"key": "cninfo_disclosure", "name": "巨潮公告", "provider": "巨潮资讯官网页面数据",
     "feeds": "上市公司法定披露、公告 PDF 原文", "role": "官网页面查询 adapter，非深证信正式 Web API；与交易所公告交叉验证",
     "match": ["巨潮公告股票目录", "巨潮公告"]},
    {"key": "sse_disclosure", "name": "上交所公告", "provider": "上海证券交易所官网",
     "feeds": "沪市公司公告、PDF 原文", "role": "官网内部查询 adapter，无 SLA；只在官网响应成功时标记可用",
     "match": ["上交所公告"]},
    {"key": "szse_disclosure", "name": "深交所公告", "provider": "深圳证券交易所官网",
     "feeds": "深市公司公告、PDF 原文", "role": "官网内部查询 adapter，无 SLA；只在官网响应成功时标记可用",
     "match": ["深交所公告"]},
    {"key": "szse_interaction", "name": "深交所互动易", "provider": "深交所互动易官网页面数据",
     "feeds": "深市投资者提问、管理层回复及各自发布时间", "role": "Tier 3 研究线索；非正式公告、无 API SLA、不参与日内评分",
     "match": ["互动易公司精确检索", "交易所互动问答"]},
    {"key": "sse_longhu", "name": "上交所官方龙虎榜", "provider": "上海证券交易所官网",
     "feeds": "沪市异常交易公开信息与席位排名", "role": "后台盘后采集并落客户缓存；官网内部 adapter，无公开 API SLA",
     "match": ["上交所官方龙虎榜"]},
    {"key": "szse_longhu", "name": "深交所官方龙虎榜", "provider": "深圳证券交易所官网",
     "feeds": "深市异常交易公开信息与席位明细", "role": "后台有界分页/N+1 采集；页面、API、MCP 只读缓存，无公开 API SLA",
     "match": ["深交所官方龙虎榜", "深交所龙虎榜席位明细"]},
    {"key": "xueqiu", "name": "雪球估值", "provider": "雪球（经 AkShare）",
     "feeds": "单股 PE/市值补全", "role": "估值末级备用", "match": ["雪球估值"]},
    {"key": "calendar", "name": "交易日历", "provider": "新浪财经（经 AkShare）",
     "feeds": "交易日、上一/下一交易日推导", "role": "时间口径基础源", "match": ["交易日历"]},
]

# relay_g：中继热路径通道；direct_x：持锁真直连（系统代理拓扑的主通道 / relay 隧道死透的灾备）。
# 两者对运维而言都是「直连/中继」语义，需归一化展示，否则健康面板会显示内部代号。
MODE_LABEL = {
    "direct": "直连",
    "direct_x": "直连",
    "relay": "境内中继",
    "relay_g": "境内中继",
    "proxy": "系统代理",
    "tdx_api": "TDX API",
    "tencent": "腾讯直连",
    "global_direct": "海外直连",
}


def _match_metric(metrics: dict, patterns: list[str], contains: list[str] | None = None) -> dict | None:
    """在埋点指标里找匹配该源的最近一次记录（多 label 取最近）。"""
    best = None
    for label, m in metrics.items():
        matched = False
        for p in patterns:
            # 精确匹配；以 ":" 或 "*" 结尾的视为前缀匹配（如 "概念成分:" / "美股指数*"）。
            if p.endswith((":", "*")):
                hit = label.startswith(p[:-1])
            else:
                hit = label == p
            if hit:
                matched = True
                break
        if not matched and any(str(token) in label for token in (contains or [])):
            matched = True
        if matched:
            # 用 epoch 排序键比较『最近』，而非 last_ts 字符串——后者只有时分秒时会把
            # 昨天 23:59 误判为比今天 00:30 更新（跨零点字典序错乱）。
            if best is None or (m.get("last_epoch", 0) or 0) > (best.get("last_epoch", 0) or 0):
                best = {**m, "_label": label}
    return best


def _display_ts(value: object) -> str | None:
    """将 canonical 时间收敛为设置页使用的紧凑形式。"""
    full = str(value or "").strip().replace("T", " ")
    if not full:
        return None
    today = now_cn().strftime("%Y-%m-%d")
    if full.startswith(today) and len(full) > 11:
        return full[11:19]
    return full[:19]


def _universe_report_row(catalog: dict, canonical: dict, metric: dict | None) -> dict:
    """股票池行只以 canonical status 决定可用性。

    交易所、TDX 和 last-good 都是候选构建路径；某个候选路径的最新
    失败只能作为诊断，不得把已构建完整的最终股票池改写为 down。
    """
    count = int(canonical.get("count") or 0)
    complete = bool(canonical.get("complete"))
    stale = bool(canonical.get("stale"))
    refresh_required = bool(canonical.get("refresh_required"))
    explicit = str(canonical.get("status") or "").strip().lower()
    attempted = metric is not None or explicit in {"down", "error", "unavailable"}

    if count > 0 and complete and not stale and not refresh_required:
        status = "ok"
    elif count > 0:
        status = "degraded"
    elif attempted:
        status = "down"
    else:
        status = "idle"

    diagnostics = []
    if metric is not None and not metric.get("last_ok"):
        diagnostics.append({
            "source": metric.get("_label"),
            "status": "down",
            "last_ts": metric.get("last_ts") or None,
            "note": metric.get("last_error") or "候选来源最近调用失败",
        })

    source = str(canonical.get("source") or "").strip()
    missing = [str(item).upper() for item in (canonical.get("missing_exchanges") or [])]
    if status == "ok":
        note = f"{source or '股票池'}完整 · {count:,}只"
    elif status == "degraded":
        reasons = []
        if stale:
            reasons.append("快照陈旧")
        if refresh_required:
            reasons.append("待刷新")
        if missing:
            reasons.append(f"缺{'/'.join(missing)}")
        note = f"已有 {count:,}只 · {' · '.join(reasons) or '部分覆盖'}"
    elif diagnostics:
        note = str(diagnostics[0]["note"])
    else:
        note = "尚未构建可用股票池" if status == "down" else "尚未采集"

    if diagnostics and status in {"ok", "degraded"}:
        note += f"；候选源 {diagnostics[0]['source'] or '未知'} 失败，未影响当前股票池"

    return {
        "key": catalog["key"], "name": catalog["name"], "provider": catalog["provider"],
        "feeds": catalog["feeds"], "role": catalog["role"], "status": status,
        "latency_ms": None,
        "last_ts": _display_ts(canonical.get("fetched_at") or canonical.get("as_of")),
        "channel": source if source and source != "unavailable" else None,
        "calls": metric.get("calls", 0) if metric else 0,
        "note": note,
        "diagnostics": diagnostics,
    }


def build_report(infra: dict, stock_universe: dict | None = None) -> dict:
    """infra：路由层即时探测得到的 {hermes, relay, feishu} 状态。

    ``stock_universe`` 是已解析股票池的唯一真值；不传时只读取本地
    canonical 状态，不会为健康面板发起网络采集。
    """
    if stock_universe is None:
        from . import free_source  # noqa: PLC0415 -- 避免模块初始化时扩大依赖

        stock_universe = free_source.stock_universe_status()
    stock_universe = dict(stock_universe or {})
    metrics = source_metrics()
    sources = []
    for c in CATALOG:
        m = _match_metric(metrics, c.get("match", []), c.get("match_contains"))
        if c["key"] == "universe":
            sources.append(_universe_report_row(c, stock_universe, m))
            continue
        if m is None:
            status, latency, ts, mode, note, calls = "idle", None, None, None, "尚未采集", 0
        else:
            ok = m.get("last_ok")
            fails, calls = m.get("fails", 0), m.get("calls", 0)
            status = "ok" if ok else "down"
            latency = m.get("last_latency_ms")
            # last_ts 现为完整 %Y-%m-%d %H:%M:%S：当天只展示时分秒，跨天才显示日期，保持面板紧凑又不丢信息。
            ts_full = m.get("last_ts") or ""
            today = now_cn().strftime("%Y-%m-%d")
            ts = ts_full[11:] if ts_full.startswith(today) else (ts_full or None)
            mode = MODE_LABEL.get(m.get("last_mode"), m.get("last_mode"))
            note = m.get("last_error") if not ok else (f"经{mode}" if mode else "")
        sources.append({
            "key": c["key"], "name": c["name"], "provider": c["provider"], "feeds": c["feeds"],
            "role": c["role"], "status": status, "latency_ms": latency,
            "last_ts": ts, "channel": mode, "calls": calls, "note": note,
        })
    return {
        "ok": True,
        "source_contract": "field-route-v2",
        "active_policy": os.environ.get("HERMES_DATA_SOURCE_POLICY", "free_only"),
        "deployment": source_route_policy(),
        "ts": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "infra": infra,
        "sources": sources,
    }
