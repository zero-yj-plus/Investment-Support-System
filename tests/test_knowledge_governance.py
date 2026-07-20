from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_trader.analysis.engine import Engine
from hermes_trader.knowledge.store import Store


def _by_id(store: Store, kid: str) -> dict:
    return next(item for item in store.knowledge_items() if item["id"] == kid)


def _audit(store: Store) -> list[dict]:
    path = store.root / "knowledge" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_manual_add_is_approved_and_backward_compatible(tmp_path: Path) -> None:
    store = Store(tmp_path)

    # 现有 API/客户 skill 都是三参数调用，不应因 v1 升级崩溃。
    kid = store.add_knowledge("交易原则", "不追高", "等确认再下单")
    item = _by_id(store, kid)

    assert item["schema"] == "KnowledgeRule.v1"
    assert item["status"] == "approved"
    assert item["version"] == 1
    assert item["origin"] == "explicit_user_input"
    assert item["sample_count"] == 1
    assert item["approved_at"]
    assert "不追高" in store.knowledge_digest()


class _SummaryHermes:
    async def chat(self, *_args, **_kwargs) -> str:
        return "交易原则|主线确认|只在多因子共振时参与\n仓位规则|降低仓位|风险期只用轻仓"


def test_auto_summary_only_creates_drafts_and_never_pollutes_digest(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save_report("close", "收盘复盘", "当日真实复盘内容")
    store.save_report("evening", "次日策略", "次日真实策略内容")
    engine = Engine(SimpleNamespace(), store, _SummaryHermes())

    asyncio.run(engine._auto_summarize_day())

    queue = store.review_queue()
    assert len(queue) == 2
    assert {item["status"] for item in queue} == {"draft"}
    assert {item["origin"] for item in queue} == {"auto_summary"}
    assert all(len(item["source_report_ids"]) == 2 for item in queue)
    assert all(item["sample_count"] == 2 for item in queue)
    assert store.knowledge_digest() == ""


def test_approval_requires_real_source_and_sample(tmp_path: Path) -> None:
    store = Store(tmp_path)
    kid = store.add_knowledge(
        "复盘笔记", "待验证", "一条自动结论",
        status="draft", origin="auto_summary", sample_count=0,
        created_by="system:auto_summary",
    )

    with pytest.raises(ValueError, match="sample_count"):
        store.approve(kid)
    assert _by_id(store, kid)["status"] == "draft"
    assert store.knowledge_digest() == ""

    store.save_report("close", "收盘复盘", "真实复盘", date="2026-07-13")
    approved = store.approve(
        kid,
        approved_by="reviewer",
        source_report_ids=["report:close:2026-07-13"],
        sample_count=1,
    )
    assert approved["status"] == "approved"
    assert approved["source_report_ids"] == ["report:close:2026-07-13"]
    assert "待验证" in store.knowledge_digest()
    assert [event["action"] for event in _audit(store)] == ["create", "approve"]


def test_legacy_items_fail_closed_without_fabricated_samples(tmp_path: Path) -> None:
    store = Store(tmp_path)
    path = tmp_path / "knowledge" / "items.json"
    path.write_text(json.dumps([{
        "id": "k_old",
        "category": "交易原则",
        "title": "历史原则",
        "content": "旧版本没有来源与样本",
        "created_at": "2024-01-01 10:00:00",
    }], ensure_ascii=False), encoding="utf-8")

    legacy = store.knowledge_items()[0]
    assert legacy["status"] == "legacy_unverified"
    assert legacy["origin"] == "legacy_import"
    assert legacy["source_report_ids"] == []
    assert legacy["evidence_ids"] == []
    assert legacy["sample_count"] == 0
    assert store.knowledge_digest() == ""
    assert store.review_queue()[0]["id"] == "k_old"

    with pytest.raises(ValueError, match="sample_count"):
        store.approve("k_old")
    store.save_report("close", "收盘复盘", "真实复盘", date="2026-07-10")
    approved = store.approve(
        "k_old", source_report_ids=["report:close:2026-07-10"], sample_count=1,
    )
    assert approved["status"] == "approved"


def test_knowledge_and_review_appends_are_not_lost_under_concurrency(tmp_path: Path) -> None:
    store = Store(tmp_path)

    def add_one(i: int) -> str:
        return store.add_knowledge("交易原则", f"规则{i}", f"内容{i}")

    with ThreadPoolExecutor(max_workers=20) as pool:
        ids = list(pool.map(add_one, range(100)))
    assert len(ids) == len(set(ids)) == 100
    assert len(store.knowledge_items()) == 100
    assert len(_audit(store)) == 100

    def review_one(i: int) -> dict:
        return store.save_rec_review(f"r_{i}", {
            "review_time": "2026-07-13 15:30:00",
            "hit_status": "pending",
        })

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(review_one, range(100)))
    assert len(store.rec_reviews()) == 100

    log = store._rec_reviews_log_path
    original = log.read_text(encoding="utf-8").splitlines()
    log.write_text("\n".join([original[0], '{broken', original[1]]) + "\n", encoding="utf-8")
    recovered = store.rec_reviews()
    # 线程池保证结果完整，不保证不同任务的日志落盘顺序；恢复断言应核对
    # 实际保留下来的两条，而不是把并发顺序误写成 r_0、r_1。
    expected = [json.loads(original[index])["rec_id"] for index in (0, 1)]
    assert [row["rec_id"] for row in recovered] == expected


def test_approval_rejects_nonexistent_evidence(tmp_path: Path) -> None:
    store = Store(tmp_path)
    kid = store.add_knowledge(
        "复盘笔记", "伪证据", "不应批准",
        status="draft", origin="auto_summary", sample_count=0,
    )
    with pytest.raises(ValueError, match="不存在"):
        store.approve(kid, evidence_ids=["ev_does_not_exist"], sample_count=1)


def test_knowledge_state_rolls_back_when_audit_append_fails(monkeypatch, tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save_report("close", "收盘复盘", "真实复盘", date="2026-07-13")
    kid = store.add_knowledge(
        "复盘笔记", "待批准", "内容",
        status="draft", origin="auto_summary",
        source_report_ids=["report:close:2026-07-13"], sample_count=1,
    )
    before = list(_audit(store))

    def fail_audit(_event):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_append_knowledge_audit", fail_audit)
    with pytest.raises(OSError):
        store.approve(kid)

    assert _by_id(store, kid)["status"] == "draft"
    assert _audit(store) == before


def test_prepared_knowledge_intent_recovers_after_process_crash(monkeypatch, tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save_report("close", "收盘复盘", "真实复盘", date="2026-07-13")
    kid = store.add_knowledge(
        "复盘笔记", "待批准", "内容",
        status="draft", origin="auto_summary",
        source_report_ids=["report:close:2026-07-13"], sample_count=1,
    )
    before = list(_audit(store))

    def simulate_process_kill(_event):
        raise SystemExit("process killed between state and audit")

    monkeypatch.setattr(store, "_append_knowledge_audit", simulate_process_kill)
    with pytest.raises(SystemExit):
        store.approve(kid)

    assert (tmp_path / "knowledge" / "transaction.intent.json").exists()
    assert _by_id(store, kid)["status"] == "approved"

    recovered = Store(tmp_path)
    assert _by_id(recovered, kid)["status"] == "draft"
    assert _audit(recovered) == before
    assert not (tmp_path / "knowledge" / "transaction.intent.json").exists()


def test_committed_knowledge_intent_keeps_state_after_cleanup_crash(monkeypatch, tmp_path: Path) -> None:
    store = Store(tmp_path)
    original_delete = store._delete_durable

    def simulate_cleanup_crash(path):
        if path == store._knowledge_intent_path:
            raise SystemExit("process killed after commit marker")
        original_delete(path)

    monkeypatch.setattr(store, "_delete_durable", simulate_cleanup_crash)
    with pytest.raises(SystemExit):
        store.add_knowledge("交易原则", "已提交规则", "内容")

    recovered = Store(tmp_path)
    assert _by_id(recovered, recovered.knowledge_items()[0]["id"])["status"] == "approved"
    assert [event["action"] for event in _audit(recovered)] == ["create"]
    assert not (tmp_path / "knowledge" / "transaction.intent.json").exists()


def test_supersede_keeps_old_version_immutable_and_appends_audit(tmp_path: Path) -> None:
    store = Store(tmp_path)
    old_id = store.add_knowledge("仓位规则", "旧规则", "上限三成")
    before = dict(_by_id(store, old_id))

    replacement = store.supersede(
        old_id,
        title="新规则",
        content="上限两成",
        reason="风险预算下调",
    )
    old = _by_id(store, old_id)

    assert old["status"] == "superseded"
    assert old["title"] == before["title"]
    assert old["content"] == before["content"]
    assert old["version"] == before["version"] == 1
    assert replacement["rule_id"] == old["rule_id"]
    assert replacement["version"] == 2
    assert replacement["status"] == "approved"
    assert "新规则" in store.knowledge_digest()
    assert "旧规则" not in store.knowledge_digest()

    events = _audit(store)
    assert [event["action"] for event in events] == ["create", "supersede"]
    assert events[-1]["replacement_id"] == replacement["id"]


def test_reject_is_audited_and_stays_out_of_digest(tmp_path: Path) -> None:
    store = Store(tmp_path)
    kid = store.add_knowledge(
        "复盘笔记", "错误归因", "尚无证据",
        status="draft", origin="auto_summary",
    )
    rejected = store.reject(kid, "样本不足", rejected_by="reviewer")

    assert rejected["status"] == "rejected"
    assert rejected["rejection_reason"] == "样本不足"
    assert store.review_queue() == []
    assert store.knowledge_digest() == ""
    assert _audit(store)[-1]["action"] == "reject"


@pytest.mark.parametrize(
    "holdings, error",
    [
        ([{"code": "000001", "cost": 0, "shares": 100}], "成本必须大于 0"),
        ([{"code": "000001", "cost": -1, "shares": 100}], "成本必须大于 0"),
        ([{"code": "000001", "cost": 10, "shares": 0}], "数量必须大于 0"),
        ([{"code": "000001", "cost": 10, "shares": -1}], "数量必须大于 0"),
        ([{"code": "000001", "cost": float("nan"), "shares": 100}], "必须是有限数字"),
        ([{"code": "000001", "cost": 10, "shares": float("inf")}], "必须是有限数字"),
        ([
            {"code": "1", "cost": 10, "shares": 100},
            {"code": "000001", "cost": 11, "shares": 200},
        ], "重复"),
    ],
)
def test_holdings_reject_invalid_or_duplicate_rows(
    tmp_path: Path, holdings: list[dict], error: str,
) -> None:
    store = Store(tmp_path)
    baseline = [{"code": "600000", "name": "浦发银行", "cost": 9.5, "shares": 100}]
    store.save_holdings(baseline)
    saved_baseline = store.holdings()

    with pytest.raises(ValueError, match=error):
        store.save_holdings(holdings)

    # 整批 fail closed：错误输入不能部分覆盖之前的有效持仓。
    assert store.holdings() == saved_baseline


def test_holdings_persist_industry_and_compute_weight_from_account_nav(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save_holdings([
        {
            "code": "000001", "name": "平安银行", "cost": 10, "shares": 100,
            "market_value": 1200, "industry": "银行",
        },
    ], total_asset=10_000)

    saved = store.holdings()[0]
    assert saved["industry"] == "银行"
    assert saved["weight_pct"] == 12
    assert store.portfolio_meta()["total_asset"] == 10_000
