from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

from hermes_trader.agent import prompts
from hermes_trader.analysis import engine as engine_module
from hermes_trader.analysis.engine import Engine, _report_stock_codes, _report_title
from hermes_trader.data import base


def test_adjacent_trade_days_use_exchange_calendar(monkeypatch) -> None:
    # 2026-10-12 作为日历中的下一开市日；中间日期即使是工作日也必须跳过。
    monkeypatch.setattr(base, "trade_dates", lambda: {
        "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13",
    })

    assert base.prev_trade_day(date(2026, 10, 12)) == "2026-10-09"
    assert base.next_trade_day(date(2026, 10, 9)) == "2026-10-12"


def test_adjacent_trade_days_fall_back_to_weekdays(monkeypatch) -> None:
    monkeypatch.setattr(base, "trade_dates", lambda: None)

    assert base.next_trade_day(date(2026, 7, 10)) == "2026-07-13"  # Friday -> Monday
    assert base.prev_trade_day(date(2026, 7, 13)) == "2026-07-10"  # Monday -> Friday


def test_evening_report_title_names_target_trade_day() -> None:
    assert _report_title(
        "evening",
        {"title": "次日策略"},
        {"次日日期": "2026-07-13"},
    ) == "次日策略（供 2026-07-13 使用）"
    assert _report_title(
        "close",
        {"title": "收盘复盘"},
        {"次日日期": "2026-07-13"},
    ) == "收盘复盘"


def test_report_stock_codes_are_unique_and_bounded() -> None:
    content = "300285 国瓷材料；300285 再次提及；600519 贵州茅台；999999 非 A 股代码"

    assert _report_stock_codes(content) == ["300285", "600519"]


class _ReportStore:
    root = None

    def __init__(self, morning: dict | None):
        self.morning = morning
        self.report_reads: list[tuple[str, str | None]] = []

    def get_report(self, rtype: str, report_date: str | None = None):
        self.report_reads.append((rtype, report_date))
        if rtype == "morning":
            return self.morning
        return None

    @staticmethod
    def holdings() -> list:
        return []

    @staticmethod
    def watchlist() -> list:
        return []

    @staticmethod
    def sentiment_history(_days: int) -> list:
        return []


class _MultiReportStore(_ReportStore):
    def __init__(self, reports: dict[str, dict]):
        super().__init__(reports.get("morning"))
        self.reports = reports

    def get_report(self, rtype: str, report_date: str | None = None):
        self.report_reads.append((rtype, report_date))
        return self.reports.get(rtype)


async def _minimal_market_context(*, full: bool = True) -> dict:
    assert full is True
    return {"市场总览": {"index": "real-close-snapshot"}}


def test_close_context_injects_original_morning_plan_and_cockpit(monkeypatch) -> None:
    fixed_now = datetime(2026, 7, 10, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(engine_module, "now_cn", lambda: fixed_now)
    monkeypatch.setattr(engine_module.stock, "quick_quote", lambda code: {
        "code": code,
        "name": "国瓷材料",
        "price": 81.2,
        "change_pct": 1.4,
        "as_of": "2026-07-10",
    })
    from hermes_trader.data import free_source
    monkeypatch.setattr(free_source, "kpl_list", lambda *_args, **_kwargs: [])

    cockpit = {
        "command": {"stance": "弱势持仓优先减仓"},
        "dir": [{"name": "半导体"}],
        "as_of": "2026-07-10 08:50:00",
    }
    store = _ReportStore({
        "rtype": "morning",
        "date": "2026-07-10",
        "title": "早盘内参",
        "created_at": "2026-07-10 08:50:10",
        "content_md": "# 盘前定稿\n300285 国瓷材料：若半导体高开不超 2% 且放量，才允许观察。",
        "cockpit": cockpit,
    })
    engine = Engine(config=None, store=store, hermes=None)
    monkeypatch.setattr(engine, "_market_context", _minimal_market_context)

    ctx = asyncio.run(engine._context_for_report("close"))

    assert store.report_reads[0] == ("morning", "2026-07-10")
    assert ctx["今日盘前计划定稿(原文·逐条对账基线)"]["content_md"].startswith("# 盘前定稿")
    assert ctx["盘前驾驶舱快照(原始定格·不得用收盘状态覆盖)"] == cockpit
    assert set(ctx["收盘对账判定口径(固定枚举)"]) == {
        "命中", "部分命中", "证伪", "未触发", "证据不足",
    }
    assert ctx["盘前计划标的收盘表现(真实行情·按代码对账)"]["items"][0]["price"] == 81.2
    assert list(ctx)[:5] == [
        "今日日期",
        "收盘对账判定口径(固定枚举)",
        "今日盘前计划定稿(原文·逐条对账基线)",
        "盘前驾驶舱快照(原始定格·不得用收盘状态覆盖)",
        "盘前计划标的收盘表现(真实行情·按代码对账)",
    ]
    assert ctx["市场总览"] == {"index": "real-close-snapshot"}


def test_close_context_marks_missing_baseline_without_inventing(monkeypatch) -> None:
    fixed_now = datetime(2026, 7, 10, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(engine_module, "now_cn", lambda: fixed_now)
    from hermes_trader.data import free_source
    monkeypatch.setattr(free_source, "kpl_list", lambda *_args, **_kwargs: [])

    engine = Engine(config=None, store=_ReportStore(None), hermes=None)
    monkeypatch.setattr(engine, "_market_context", _minimal_market_context)

    ctx = asyncio.run(engine._context_for_report("close"))

    assert ctx["今日盘前计划定稿(原文·逐条对账基线)"].startswith("【缺失】")
    assert ctx["盘前驾驶舱快照(原始定格·不得用收盘状态覆盖)"].startswith("【缺失】")
    assert ctx["盘前计划标的收盘表现(真实行情·按代码对账)"].startswith("【缺失】")


def test_close_prompt_requires_fixed_reconciliation_labels() -> None:
    user_prompt = prompts.build_report_prompt(
        "close",
        {"今日日期": "2026-07-10"},
    )[1]["content"]

    assert "命中 / 部分命中 / 证伪 / 未触发" in user_prompt
    assert "不得用收盘后的驾驶舱状态反推" in user_prompt
    assert "有效 / 无效 / 一般" not in user_prompt


def test_evening_reads_exact_same_day_finals_and_puts_them_before_refresh(monkeypatch) -> None:
    fixed_now = datetime(2026, 7, 10, 19, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(engine_module, "now_cn", lambda: fixed_now)
    monkeypatch.setattr(engine_module, "next_trade_day", lambda _date: "2026-07-13")
    from hermes_trader.data import free_source
    monkeypatch.setattr(free_source, "kpl_list", lambda *_args, **_kwargs: [])

    store = _MultiReportStore({
        "tail": {
            "rtype": "tail", "date": "2026-07-10", "title": "尾盘定稿",
            "created_at": "2026-07-10 14:52:00", "content_md": "尾盘原文",
        },
        "close": {
            "rtype": "close", "date": "2026-07-10", "title": "收盘复盘",
            "created_at": "2026-07-10 15:30:00", "content_md": "收盘原文",
        },
    })
    engine = Engine(config=None, store=store, hermes=None)
    monkeypatch.setattr(engine, "_market_context", _minimal_market_context)

    ctx = asyncio.run(engine._context_for_report("evening"))

    assert ("tail", "2026-07-10") in store.report_reads
    assert ("close", "2026-07-10") in store.report_reads
    assert list(ctx)[:5] == [
        "今日日期",
        "今日事实冻结规则",
        "今日尾盘观察(同日定稿)",
        "今日收盘复盘(同日定稿)",
        "数据版本与来源锁(系统审计)",
    ]
    assert ctx["今日尾盘观察(同日定稿)"]["content_md"] == "尾盘原文"
    assert ctx["今日收盘复盘(同日定稿)"]["content_md"] == "收盘原文"
    sources = ctx["数据版本与来源锁(系统审计)"]["sources"]
    assert [row["rtype"] for row in sources] == ["tail", "close"]
    assert ctx["次日日期"] == "2026-07-13"
