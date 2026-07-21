"""资金持续追踪引擎（核心引擎，被 Web UI 与 MCP Server 两个头共享）。

做什么：定时采集 top 行业板块 + 热门股的【主力资金流】快照，落 SQLite 时序库，对外提供
"最新排名 + 历史趋势"查询。客户要的"数据持续性"= 我们定时快照累积，而非购买历史数据。

数据源：板块优先客户授权 TDX API v2.8，公开同花顺/东财做备源；热门股
仍用公开个股资金流排名。采集层与引擎/库/查询解耦，数据源变更时只需替换 collect_* 函数。

单位：公开源主力净额=元；TDX 板块公式值为 ``tdx_raw_unverified``，不换算也不与元混算。
失败一律返回空/None，绝不编造。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .base import get_ak, safe, to_float, now_cn, ts_str, log

_db_path: str | None = None
_lock = threading.Lock()


def init(data_dir) -> None:
    """初始化 SQLite（建表+索引）。data_dir 来自 config（每客户独立目录，互不串数据）。"""
    global _db_path
    p = Path(data_dir)
    p.mkdir(parents=True, exist_ok=True)
    _db_path = str(p / "capital.db")
    conn = _conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sector_flow(
                ts TEXT, trade_date TEXT, kind TEXT, name TEXT, rnk INTEGER,
                main_net REAL, change_pct REAL, source TEXT,
                value_unit TEXT, unit_verified INTEGER,
                PRIMARY KEY(ts, kind, name));
            CREATE TABLE IF NOT EXISTS hot_stock_flow(
                ts TEXT, trade_date TEXT, code TEXT, name TEXT, rnk INTEGER,
                main_net REAL, change_pct REAL, reason TEXT, source TEXT,
                value_unit TEXT, unit_verified INTEGER,
                PRIMARY KEY(ts, code));
            CREATE INDEX IF NOT EXISTS ix_sf_name ON sector_flow(name, ts);
            CREATE INDEX IF NOT EXISTS ix_hs_code ON hot_stock_flow(code, ts);
        """)
        # 旧库平滑升级。旧数据均来自已校准的公开元口径，因此补 CNY/verified；
        # 新 TDX 原值必须单独标记，不能被图表除 1e8 冒充“亿元”。
        for tbl in ("sector_flow", "hot_stock_flow"):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
            if "source" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN source TEXT")
            if "value_unit" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN value_unit TEXT")
            if "unit_verified" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN unit_verified INTEGER")
            conn.execute(f"UPDATE {tbl} SET value_unit='CNY' WHERE value_unit IS NULL OR value_unit='' ")
            conn.execute(f"UPDATE {tbl} SET unit_verified=1 WHERE unit_verified IS NULL")
        conn.commit()
    finally:
        conn.close()


def _conn():
    if _db_path is None:
        raise RuntimeError("capital_tracker 未 init（需先调用 init(data_dir)）")
    return sqlite3.connect(_db_path, timeout=10)


def available() -> bool:
    return _db_path is not None


# ---- 采集器（免费公开源；替换备源时只改这两个函数）----

def collect_sectors(top: int = 10) -> tuple[list[dict], str, str]:
    """top 行业板块资金。同时取净流入【最多 top】+【流出最多 top】两端。
    优先级（铁律：标真实源，不冒充）：
      ① TDX v2.8 板块直接公式排名（单位未校准，只用方向/排名）；
      ② 同花顺免费即时口径（元）；
      ③ market_sectors 公开源兜底。
    返回行带 value_unit/unit_verified，盘前/休市日必须标上一交易日。"""
    inds: list[dict] = []
    source = ""
    data_date = ""
    # ① TDX 板块资金排名。只有同时拿到来源日期才接受，避免无日期原值冒充当日。
    try:
        from . import tdx_api_source as tdx  # noqa: PLC0415

        ranked = tdx.sector_fund_rank(limit=588)
        if ranked and ranked.get("data_date") and len(ranked.get("rows") or []) >= 8:
            inds = [
                {
                    "name": str(item.get("sector_name") or item.get("sector_code") or ""),
                    "main_flow": to_float(item.get("value")),
                    "change_pct": None,
                    "value_unit": str(ranked.get("unit") or "tdx_raw_unverified"),
                    "unit_verified": False,
                }
                for item in ranked["rows"]
                if to_float(item.get("value")) is not None
            ]
            source = f"TDX·板块资金({ranked.get('source') or 'auto'})"
            data_date = str(ranked.get("data_date") or "")
    except Exception as e:  # noqa: BLE001
        log.info("collect_sectors TDX 板块资金失败: %s", str(e)[:120])
    # ② 同花顺免费即时口径（只取今日、不回溯，避免拿当前快照冒充历史日）
    try:
        from . import free_source as tx  # noqa: PLC0415
        # 后台采集不受首屏 4 秒预算约束。该源在生产机实测约 8.35 秒
        # 可完整返回 90 行业；25 秒是采集预算，不会进入用户请求链。
        dc = tx.moneyflow_ind_dc(timeout=25, trip_breaker=True) if len(inds) < 8 else []
        if dc:
            inds = [{
                "name": s["name"], "main_flow": s["main_net"], "change_pct": s["change_pct"],
                "value_unit": "CNY", "unit_verified": True,
            } for s in dc]
            source = "同花顺·公开源"
    except Exception as e:  # noqa: BLE001
        log.info("collect_sectors 同花顺免费 moneyflow_ind_dc 失败: %s", str(e)[:120])
    # ③ 同花顺直采备路（盘中日内即时，90 行业，名+净额齐全，不靠名匹配）
    if len(inds) < 8:
        ths = _ths_sectors_direct()
        if len(ths) > len(inds):
            inds, source = ths, "同花顺·即时"
    # ④ 最后兜底：market_sectors（新浪列表+同花顺净额按名合并）
    if len(inds) < 8:
        try:
            from . import market  # noqa: PLC0415（避免顶层循环导入）
            mk = [s for s in (market.market_sectors() or {}).get("sectors", [])
                  if s.get("kind") == "industry" and s.get("main_flow") is not None]
            if len(mk) > len(inds):
                inds = [{**row, "value_unit": "CNY", "unit_verified": True} for row in mk]
                source = "新浪+同花顺"
        except Exception as e:  # noqa: BLE001
            log.info("collect_sectors market_sectors 失败: %s", str(e)[:120])
    inds.sort(key=lambda s: -(s.get("main_flow") or 0))   # 降序：流入最多 → 流出最多
    n = len(inds)
    if n > top * 2:
        picks = [(i + 1, inds[i]) for i in range(top)] + [(i + 1, inds[i]) for i in range(n - top, n)]
    else:
        picks = [(i + 1, s) for i, s in enumerate(inds)]
    out = []
    for rk, s in picks:
        out.append({
            "name": str(s.get("name", "")), "main_net": to_float(s.get("main_flow")),
            "change_pct": to_float(s.get("change_pct")), "rnk": rk,
            "value_unit": str(s.get("value_unit") or "CNY"),
            "unit_verified": bool(s.get("unit_verified", True)),
        })
    if out and not data_date:
        from .free_source import quote_data_date  # noqa: PLC0415
        data_date = quote_data_date()
    return out, source, data_date


def _ths_sectors_direct() -> list[dict]:
    """同花顺行业资金流隔离适配，返回 [{name, main_flow(元), change_pct}]。

    函数名保留兼容旧采集链，但不再在主进程直接调用
    ``stock_fund_flow_industry``。缓存、子进程超时和亿元→元换算均由
    ``free_source.moneyflow_ind_dc`` 统一处理。
    """
    from . import free_source  # noqa: PLC0415

    return [
        {
            "name": str(row.get("name", "")),
            "main_flow": to_float(row.get("main_net")),
            "change_pct": to_float(row.get("change_pct")),
            "value_unit": "CNY",
            "unit_verified": True,
        }
        for row in free_source.moneyflow_ind_dc(timeout=25, trip_breaker=True)
        if to_float(row.get("main_net")) is not None
    ]


def collect_hot_stocks(top: int = 20) -> tuple[list[dict], str, str]:
    """热门股 = 当日主力净流入 top。
    源：东财公开排名可达时直取；不可达时走 free_source 公开资金榜兼容层。两者皆不可用
    →[]（绝不编造）。返回 (rows[{code,name,main_net(元),change_pct,reason,rnk}], source, data_date)。
    data_date=数据真正所属交易日；盘前/休市不得使用自然日盖戳。"""
    # 东财个股资金流排名直打 push2.eastmoney，被封机器会空等 20s → 按 _use_eastmoney() 门控：
    # 东财可达才走它(主源)，否则直接跳到免费公开资金榜兜底。
    use_em = False
    try:
        from . import market  # noqa: PLC0415（避免顶层循环导入）
        use_em = bool(market._use_eastmoney())
    except Exception:
        use_em = False
    today = now_cn().strftime("%Y-%m-%d")
    ak = get_ak()
    if use_em and ak is not None:
        df = safe(getattr(ak, "stock_individual_fund_flow_rank", None),
                  indicator="今日", label="个股资金流排名(资金追踪)", timeout=20)
        if df is not None and not df.empty:
            net_col = next((c for c in df.columns if "主力净流入-净额" in c), None)
            chg_col = next((c for c in df.columns if "涨跌幅" in c), None)
            if net_col:
                try:
                    import pandas as pd  # noqa: PLC0415
                    df = df.assign(_net=pd.to_numeric(df[net_col], errors="coerce")).dropna(subset=["_net"])
                    df = df.sort_values("_net", ascending=False).head(top)
                except Exception:
                    df = df.head(top)
                out = []
                for i, (_, r) in enumerate(df.iterrows(), 1):
                    out.append({"code": str(r.get("代码", "")).zfill(6), "name": str(r.get("名称", "")),
                                "main_net": to_float(r.get(net_col)),
                                "change_pct": to_float(r.get(chg_col)) if chg_col else None,
                                "reason": "主力净流入榜", "rnk": i})
                if out:
                    return out, "东财·即时", today   # 东财即时排名=今日实时
    # 东财不可用/为空 → 免费公开资金榜兼容层兜底。
    try:
        from . import free_source as tx  # noqa: PLC0415
        rows = tx.moneyflow_market(top) or []
        # 真实日期在每行 trade_date 里——原样取出，绝不用今天冒充。
        data_date = (rows[0].get("trade_date") if rows else "") or ""
        source = (rows[0].get("source") if rows else "") or "公开资金流"
        return rows, source, data_date
    except Exception as e:  # noqa: BLE001
        log.info("collect_hot_stocks 免费资金榜兜底失败: %s", str(e)[:120])
        return [], "", ""


# ---- 快照入库（定时任务调用）----

def snapshot() -> dict:
    """采集一次 top 板块 + 热门股资金，写入时序库。返回采集计数。"""
    if _db_path is None:
        return {"ok": False, "msg": "未 init"}
    ts = ts_str()
    today = now_cn().strftime("%Y-%m-%d")
    secs, sec_src, sec_date = collect_sectors()
    hots, hot_src, hot_date = collect_hot_stocks()
    # trade_date 一律存【数据真正所属交易日】，不再用采集当天冒充（#6 根因：盘后未结算时数据实为上一日）。
    sec_td = sec_date or today
    hot_td = hot_date or today
    missing = []
    if not secs:
        missing.append("行业资金流")
    if not hots:
        missing.append("个股资金流排名")
    if not secs and not hots:
        return {
            "ok": False, "complete": False, "ts": ts,
            "sectors": 0, "hot_stocks": 0,
            "missing": missing, "msg": "采集为空(源不可用)",
        }
    with _lock:
        conn = _conn()
        try:
            if secs:
                conn.executemany(
                    "INSERT OR REPLACE INTO sector_flow"
                    "(ts,trade_date,kind,name,rnk,main_net,change_pct,source,value_unit,unit_verified) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    [(ts, sec_td, "industry", s["name"], s["rnk"], s["main_net"], s["change_pct"],
                      sec_src, s.get("value_unit") or "CNY", int(bool(s.get("unit_verified", True)))) for s in secs])
            if hots:
                conn.executemany(
                    "INSERT OR REPLACE INTO hot_stock_flow"
                    "(ts,trade_date,code,name,rnk,main_net,change_pct,reason,source,value_unit,unit_verified) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    [(ts, hot_td, h["code"], h["name"], h["rnk"], h["main_net"], h["change_pct"],
                      h["reason"], hot_src, "CNY", 1) for h in hots])
            conn.commit()
        finally:
            conn.close()
    return {"ok": True, "complete": not missing, "ts": ts,
            "sectors": len(secs), "hot_stocks": len(hots), "missing": missing,
            "sector_source": sec_src, "hot_source": hot_src,
            "sector_date": sec_td, "hot_date": hot_td}


# ---- 查询（两个头共用）----

def _rows(sql: str, args=()) -> list[dict]:
    if _db_path is None:          # 未开启资金追踪的客户：优雅返回空，不报错
        return []
    with _lock:
        conn = _conn()
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()


def latest_sectors() -> list[dict]:
    """最新一次快照的 top 板块资金排名。"""
    return _rows("SELECT * FROM sector_flow WHERE ts=(SELECT MAX(ts) FROM sector_flow) ORDER BY rnk")


def latest_hot_stocks() -> list[dict]:
    """最新一次快照的热门股资金排名。"""
    return _rows("SELECT * FROM hot_stock_flow WHERE ts=(SELECT MAX(ts) FROM hot_stock_flow) ORDER BY rnk")


def sector_history(name: str, days: int = 5) -> list[dict]:
    """某板块资金的时间序列（近 days 天，按时间升序）——看资金持续流入/流出趋势。"""
    since = (now_cn().replace(hour=0, minute=0, second=0) - _td(days)).strftime("%Y-%m-%d %H:%M:%S")
    return _rows("SELECT ts,main_net,change_pct,rnk,source,value_unit,unit_verified "
                 "FROM sector_flow WHERE name=? AND ts>=? ORDER BY ts",
                 (name, since))


def hot_stock_history(code: str, days: int = 5) -> list[dict]:
    """某股资金的时间序列（近 days 天）。"""
    since = (now_cn().replace(hour=0, minute=0, second=0) - _td(days)).strftime("%Y-%m-%d %H:%M:%S")
    return _rows("SELECT ts,main_net,change_pct,rnk,source,value_unit,unit_verified "
                 "FROM hot_stock_flow WHERE code=? AND ts>=? ORDER BY ts",
                 (str(code).zfill(6), since))


def trends(days: int = 1) -> dict:
    """资金追踪综合视图数据：当日（days=1）或近 days 天的 top 板块 + 热门股【日内主力资金时序】，
    供前端一次性取全、直接画"日内净流入曲线"（对照图2）。每条含最新净额(排序用)与逐快照 series。
    返回 {ok, ts, days, sectors:[{name,main_net,change_pct,rnk,series:[{t,v}]}], hot_stocks:[{code,...}]}。"""
    if _db_path is None:
        return {"ok": False, "ts": ts_str(), "days": days, "msg": "未开启资金追踪",
                "sectors": [], "hot_stocks": []}
    # days=1 取今日 0 点起；days>1 多回溯（净额按交易日累积，跨日会各自从 0 起，前端按 series 画即可）。
    since = (now_cn().replace(hour=0, minute=0, second=0, microsecond=0)
             - _td(max(0, days - 1))).strftime("%Y-%m-%d %H:%M:%S")
    sec_rows = _rows("SELECT ts,trade_date,name,main_net,change_pct,rnk,source,value_unit,unit_verified FROM sector_flow "
                     "WHERE ts>=? ORDER BY ts", (since,))
    hot_rows = _rows("SELECT ts,trade_date,code,name,main_net,change_pct,rnk,source,value_unit,unit_verified FROM hot_stock_flow "
                     "WHERE ts>=? ORDER BY ts", (since,))
    # 跨日后 days=1 不能把昨日真实快照直接刷成空白。当日尚未产生新快照时，
    # 分别读回两张表最近一个「实际采集日」的完整日内曲线。返回会同时标
    # last_good/stale/真实数据日期，所以是“保留真实旧数据”，不是把旧数据伪装成实时。
    fallback_sector = False
    fallback_hot = False
    if days == 1 and not sec_rows:
        sec_rows = _rows(
            "SELECT ts,trade_date,name,main_net,change_pct,rnk,source,value_unit,unit_verified "
            "FROM sector_flow WHERE substr(ts,1,10)=(SELECT MAX(substr(ts,1,10)) FROM sector_flow) "
            "ORDER BY ts"
        )
        fallback_sector = bool(sec_rows)
    if days == 1 and not hot_rows:
        hot_rows = _rows(
            "SELECT ts,trade_date,code,name,main_net,change_pct,rnk,source,value_unit,unit_verified "
            "FROM hot_stock_flow WHERE substr(ts,1,10)=(SELECT MAX(substr(ts,1,10)) FROM hot_stock_flow) "
            "ORDER BY ts"
        )
        fallback_hot = bool(hot_rows)
    # 最新快照的【真实时点】（rows 按 ts 升序，末行=最近一次快照）。前端应用它打戳，
    # 不能用 ts_str()=请求当下时间冒充实时（#6：客户拿实时价对不上快照涨跌幅→误判"过时"）。
    _snap_ts = ""
    if sec_rows:
        _snap_ts = str(sec_rows[-1].get("ts", ""))
    if hot_rows:
        _snap_ts = max(_snap_ts, str(hot_rows[-1].get("ts", "")))
    today = now_cn().strftime("%Y-%m-%d")
    hot_date = str(hot_rows[-1].get("trade_date", "")) if hot_rows else ""
    sector_date = str(sec_rows[-1].get("trade_date", "")) if sec_rows else ""
    return {"ok": True, "ts": ts_str(), "days": days, "snap_ts": _snap_ts, "today": today,
            # 顶层数据源标注（最新快照的真实源，铁律——前端如实展示"东财/同花顺"，不冒充）
            "sector_source": (sec_rows[-1].get("source") if sec_rows else ""),
            "hot_source": (hot_rows[-1].get("source") if hot_rows else ""),
            # 数据真正所属交易日：前端据此判断是否隔日，隔日必须显式提示"非今日·最近已结算日"（#6 铁律）。
            "hot_date": hot_date, "sector_date": sector_date,
            "stale_hot": bool(fallback_hot or (hot_date and hot_date != today)),
            "stale_sector": bool(fallback_sector or (sector_date and sector_date != today)),
            "last_good_hot": fallback_hot,
            "last_good_sector": fallback_sector,
            "view_state": "last_good" if fallback_hot or fallback_sector else (
                "complete" if sec_rows or hot_rows else "empty"
            ),
            "sectors": _group_series(sec_rows, "name"),
            "hot_stocks": _group_series(hot_rows, "code")}


def _group_series(rows: list[dict], key: str) -> list[dict]:
    """把按 ts 升序的快照行按 name/code 归并成 [{<key>,name,main_net,change_pct,rnk,series:[{t,v}]}]。
    main_net/change_pct/rnk 取该序列最后一个快照（=最新值，rows 已按 ts 升序），按最新净额降序输出。"""
    by: dict = {}
    for r in rows:
        k = str(r.get(key, ""))
        if not k:
            continue
        value_unit = str(r.get("value_unit") or "CNY")
        unit_verified = bool(r.get("unit_verified"))
        g = by.get(k)
        # 一旦主备源在盘中切换，CNY 和 TDX raw 不能连成一条线。
        # 保留最新单位的连续片段，旧口径仍在库内可审计。
        if g is None or g.get("value_unit") != value_unit or g.get("unit_verified") != unit_verified:
            g = {
                key: k,
                "name": r.get("name", k),
                "series": [],
                "value_unit": value_unit,
                "unit_verified": unit_verified,
            }
            by[k] = g
        g["name"] = r.get("name", k)
        g["main_net"] = r.get("main_net")          # 末值即最新（升序，后写覆盖前）
        g["change_pct"] = r.get("change_pct")
        g["rnk"] = r.get("rnk")
        g["source"] = r.get("source")              # 末值即最新数据源
        g["value_unit"] = value_unit
        g["unit_verified"] = unit_verified
        g["series"].append({"t": str(r.get("ts", ""))[11:16], "v": r.get("main_net")})  # HH:MM
    out = list(by.values())
    out.sort(key=lambda x: -(x.get("main_net") or 0))   # 按最新净额降序（流入在上、流出在下）
    return out


def _td(days: int):
    from datetime import timedelta  # noqa: PLC0415
    return timedelta(days=days)
