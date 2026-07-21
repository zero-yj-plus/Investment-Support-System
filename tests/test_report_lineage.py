from concurrent.futures import ThreadPoolExecutor
import os
import stat

from hermes_trader.analysis.report_lineage import build_lineage, report_ref
from hermes_trader.knowledge.store import Store


def test_report_store_is_create_once_and_records_content_fingerprint(tmp_path) -> None:
    store = Store(tmp_path)
    first = store.save_report(
        "close", "收盘复盘", "首版事实", date="2026-07-13",
        input_context={"收盘行情": {"上证": -1.2}},
    )
    second = store.save_report("close", "收盘复盘", "不得覆盖", date="2026-07-13")

    assert second == first
    assert first["schema_version"] == "daily-report-v2"
    assert first["content_id"] == "report:close:2026-07-13"
    assert first["report_version"] == 1
    assert len(first["content_hash"]) == 64
    assert len(first["input_context_hash"]) == 64
    snapshot = tmp_path / first["input_snapshot_file"]
    assert snapshot.is_file()
    mode = stat.S_IMODE(snapshot.stat().st_mode)
    if os.name == "nt":
        assert mode & stat.S_IWRITE
    else:
        assert mode == 0o600
    assert store.get_report("close", "2026-07-13")["content_md"] == "首版事实"


def test_concurrent_report_finalization_can_only_create_one_body(tmp_path) -> None:
    store = Store(tmp_path)

    def save(body: str) -> dict:
        return store.save_report("evening", "次日策略", body, date="2026-07-13")

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(save, ("版本 A", "版本 B")))

    assert rows[0]["content_hash"] == rows[1]["content_hash"]
    assert rows[0]["content_md"] == rows[1]["content_md"]
    assert store.get_report("evening", "2026-07-13")["content_md"] in {"版本 A", "版本 B"}


def test_lineage_binds_parent_report_day_version_and_hash(tmp_path) -> None:
    store = Store(tmp_path)
    tail = store.save_report("tail", "尾盘定稿", "尾盘事实", date="2026-07-13")
    close = store.save_report("close", "收盘复盘", "收盘事实", date="2026-07-13")
    lineage = build_lineage(
        trade_day="2026-07-13",
        sources=(report_ref(tail), report_ref(close)),
    )

    assert lineage["trade_day"] == "2026-07-13"
    assert [row["rtype"] for row in lineage["sources"]] == ["tail", "close"]
    assert all(len(row["content_hash"]) == 64 for row in lineage["sources"])
    assert len(lineage["lineage_hash"]) == 64


def test_one_trade_day_can_be_traced_from_plan_to_close_and_next_day(tmp_path) -> None:
    """阶段2闭环验收：用固定交易日契约验证父版本链，不依赖实时网络。"""
    day = "2026-07-13"
    store = Store(tmp_path)
    morning = store.save_report(
        "morning", "早盘内参", "盘前原计划：条件 A 满足才观察。", date=day,
        input_context={"今日日期": day, "盘前快照": "frozen"},
    )
    midday = store.save_report(
        "midday", "尾盘草案", "14:45 草案", date=day,
        input_context={"今日日期": day},
    )
    tail_lineage = build_lineage(trade_day=day, sources=(report_ref(midday),))
    tail = store.save_report(
        "tail", "尾盘定稿", "14:52 尾盘定稿", date=day,
        input_lineage=tail_lineage, input_context={"今日日期": day},
    )
    close_lineage = build_lineage(
        trade_day=day,
        sources=(report_ref(morning), report_ref(tail)),
        evidence=({
            "kind": "news_event_observation", "trade_day": day,
            "content_hash": "e" * 64, "verdict": "证据不足",
        },),
    )
    close = store.save_report(
        "close", "收盘复盘", "条件 A 缺路径证据 → 证据不足。", date=day,
        input_lineage=close_lineage, input_context={"今日日期": day},
    )
    evening_lineage = build_lineage(
        trade_day=day,
        sources=(report_ref(tail), report_ref(close)),
    )
    evening = store.save_report(
        "evening", "次日策略", "依据今日定稿，明日等待条件 B。", date=day,
        input_lineage=evening_lineage, input_context={"今日日期": day},
    )

    assert [row["content_id"] for row in close["input_lineage"]["sources"]] == [
        morning["content_id"], tail["content_id"],
    ]
    assert [row["content_id"] for row in evening["input_lineage"]["sources"]] == [
        tail["content_id"], close["content_id"],
    ]
    assert close["input_lineage"]["evidence"][0]["verdict"] == "证据不足"
    assert len({morning["date"], tail["date"], close["date"], evening["date"]}) == 1

    # 即使事后重跑，历史结论也不能被“回看后改口”。
    repeated = store.save_report("close", "收盘复盘", "事后改写", date=day)
    assert repeated["content_hash"] == close["content_hash"]
    assert repeated["content_md"] == close["content_md"]
