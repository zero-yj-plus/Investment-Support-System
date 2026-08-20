"""AkShare 高风险接口的独立子进程工作程序。

本模块只能通过 ``python -m hermes_trader.data.akshare_worker`` 启动。
它故意在子进程内导入 AkShare/MiniRacer：V8 即使 FATAL/abort，
也只会结束这个一次性进程，不会带走 uvicorn 主服务。
"""
from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from io import StringIO
import math
import sys
from typing import Any

RESULT_PREFIX = "HERMES_AKSHARE_RESULT="


def _scalar(value: Any) -> str | int | float | bool | None:
    """把 numpy/pandas 标量收敛到严格 JSON 标量。"""
    try:
        value = value.item()
    except (AttributeError, ValueError):
        pass
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _industry_flow() -> list[dict]:
    # 不要移到模块顶层：主进程永远不应通过普通 import
    # 间接载入 AkShare/MiniRacer。
    import akshare as ak  # noqa: PLC0415

    frame = ak.stock_fund_flow_industry(symbol="即时")
    if frame is None or getattr(frame, "empty", True):
        return []
    required = {"行业", "净额", "行业-涨跌幅"}
    if not required.issubset(set(frame.columns)):
        raise RuntimeError("IndustryFlowSchemaMismatch")
    rows: list[dict] = []
    for _, row in frame.iterrows():
        name = str(row.get("行业") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "name": name,
                # AkShare 1.18.64 的该表取自同花顺行业资金页，
                # 裸数值列「净额」口径为亿元。换算留在父进程纯函数中，
                # 便于测试防止字符串带单位时二次放大。
                "net_raw": _scalar(row.get("净额")),
                "change_pct": _scalar(row.get("行业-涨跌幅")),
            }
        )
    return rows


def _industry_summary() -> list[dict]:
    """同花顺行业一览表的当日结构字段。"""
    # 与行业资金流相同，AkShare/MiniRacer 只能存在于一次性子进程。
    import akshare as ak  # noqa: PLC0415

    frame = ak.stock_board_industry_summary_ths()
    if frame is None or getattr(frame, "empty", True):
        return []
    required = {
        "板块", "涨跌幅", "总成交额", "净流入", "上涨家数", "下跌家数",
        "领涨股", "领涨股-涨跌幅",
    }
    if not required.issubset(set(frame.columns)):
        raise RuntimeError("IndustrySummarySchemaMismatch")
    # 同花顺行业一览通常约 90 行；用有界行数防止上游页面结构变化后
    # 把错误表格或异常大响应带回主进程。
    if not 50 <= len(frame) <= 200:
        raise RuntimeError("IndustrySummaryRowCountMismatch")
    rows: list[dict] = []
    for _, row in frame.iterrows():
        name = str(row.get("板块") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "name": name,
                # AkShare 1.18.x 文档约定总成交额/净流入的裸数值单位为亿元；
                # 单位换算和量级校验统一留给父进程。
                "amount_raw": _scalar(row.get("总成交额")),
                "net_raw": _scalar(row.get("净流入")),
                "change_pct": _scalar(row.get("涨跌幅")),
                "up_count": _scalar(row.get("上涨家数")),
                "down_count": _scalar(row.get("下跌家数")),
                "leader_name": _scalar(row.get("领涨股")),
                "leader_change_pct": _scalar(row.get("领涨股-涨跌幅")),
            }
        )
    return rows


def _market_fund_flow() -> list[dict]:
    """同花顺个股资金榜的净流入/净流出两端。

    AkShare 的公开 ``stock_fund_flow_individual`` 会逐页下载全市场，生产机
    一次需要几十个请求；中途任一页被断开就会把整榜变成空。这里只读取
    服务端已经按「净额」排好序的首屏，分别取流入端和流出端，足够支撑
    产品的 TOP 榜与资金快照，同时把 MiniRacer/HTML 解析留在一次性子进程。
    """
    import pandas as pd  # noqa: PLC0415
    import requests  # noqa: PLC0415
    from akshare.stock_feature.stock_fund_flow import (  # noqa: PLC0415
        _get_file_content_ths,
        py_mini_racer,
    )

    js_code = py_mini_racer.MiniRacer()
    js_code.eval(_get_file_content_ths("ths.js"))
    token = js_code.call("v")
    headers = {
        "Accept": "text/html, */*; q=0.01",
        "Accept-Encoding": "gzip, deflate",
        "hexin-v": token,
        "Referer": "http://data.10jqka.com.cn/funds/ggzjl/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
    }
    required = {"股票代码", "股票简称", "涨跌幅", "净额(元)"}
    rows: list[dict] = []
    for direction in ("desc", "asc"):
        url = (
            "http://data.10jqka.com.cn/funds/ggzjl/field/zjjlr/"
            f"order/{direction}/page/1/ajax/1/free/1/"
        )
        response = requests.get(url, headers=headers, timeout=8)
        response.raise_for_status()
        frames = pd.read_html(StringIO(response.text))
        if not frames or not required.issubset(set(frames[0].columns)):
            raise RuntimeError("MarketFundFlowSchemaMismatch")
        for _, row in frames[0].iterrows():
            raw_code = str(row.get("股票代码") or "").strip()
            if raw_code.endswith(".0"):
                raw_code = raw_code[:-2]
            code = raw_code.zfill(6)
            name = str(row.get("股票简称") or "").strip()
            if len(code) != 6 or not code.isdigit() or not name:
                continue
            rows.append({
                "code": code,
                "name": name,
                "net_raw": _scalar(row.get("净额(元)")),
                "change_pct": _scalar(row.get("涨跌幅")),
                "direction": "inflow" if direction == "desc" else "outflow",
            })
    return rows


def _emit(payload: dict) -> None:
    # AkShare/tqdm 可能向 stdout 打进度条；因此父进程只识别
    # 这个唯一前缀，不对整段 stdout 盲目 json.loads。
    print(RESULT_PREFIX + json.dumps(payload, ensure_ascii=False, allow_nan=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    commands = {
        "industry-flow": (_industry_flow, "ths-industry-flow.v1"),
        "industry-summary": (_industry_summary, "ths-industry-summary.v1"),
        "market-fund-flow": (_market_fund_flow, "ths-market-fund-flow.v1"),
    }
    if len(args) != 1 or args[0] not in commands:
        _emit({"ok": False, "rows": [], "error": "unsupported command"})
        return 2
    worker, schema = commands[args[0]]
    try:
        rows = worker()
    except Exception as exc:  # V8 abort 无法在此捕获，由父进程检查退出码
        # 不把 requests 异常中的 URL/query，更不把环境信息带回父进程。
        _emit({"ok": False, "rows": [], "error": type(exc).__name__})
        return 1
    try:
        provider_version = version("akshare")
    except PackageNotFoundError:
        provider_version = "unknown"
    _emit({
        "ok": True, "rows": rows, "error": None,
        "schema": schema, "provider_version": provider_version,
    })
    return 0


if __name__ == "__main__":  # pragma: no cover - 由隔离进程/live smoke 覆盖
    raise SystemExit(main())
