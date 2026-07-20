"""交易价格 claim 硬闸的离线单测。

仅使用 Python 标准库，不访问任何行情源/模型/飞书。
运行：python -m unittest tests.test_claim_validator
"""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

import pytest

from hermes_trader.analysis.claim_validator import (
    collect_price_evidence,
    validate_report,
    validate_report_finalization,
)
from hermes_trader.knowledge.store import Store
from hermes_trader.api.routes import push_report


def _context_with_guoci() -> dict:
    return {
        "用户持仓": [
            {
                "code": "300285",
                "name": "国瓷材料",
                "实时行情": {
                    "code": "300285",
                    "name": "国瓷材料",
                    "price": 80.80,
                    "open": 75.20,
                    "high": 81.09,
                    "low": 74.00,
                    "prev_close": 74.60,
                },
            }
        ]
    }


class ClaimValidatorTest(unittest.TestCase):
    def test_collects_stock_bound_price_evidence(self):
        evidence = collect_price_evidence(_context_with_guoci())
        self.assertIn("300285", evidence)
        rec = evidence["300285"]
        self.assertEqual(rec["name"], "国瓷材料")
        self.assertLess(rec["allowed_low"], 74.0)
        self.assertGreater(rec["allowed_high"], 81.09)

    def test_known_bad_150_yuan_is_blocked(self):
        report = "国瓷材料（300285）：等待回踩至 150 元附近（约昨日均价）。"
        result = validate_report(report, _context_with_guoci())
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        blocked = [c for c in result["claims"] if c["status"] == "blocked"]
        self.assertTrue(any(c["value"] == 150 for c in blocked))
        self.assertTrue(all(c.get("code") == "300285" for c in blocked))
        self.assertTrue(all(c["reason"] == "price_outside_stock_evidence_range" for c in blocked))

    def test_supported_price_is_allowed(self):
        report = "国瓷材料 300285：回踩 78 元附近不破再观察。"
        result = validate_report(report, _context_with_guoci())
        self.assertTrue(result["ok"])
        self.assertEqual(result["claim_count"], 1)
        self.assertEqual(result["claims"][0]["code"], "300285")

    def test_labeled_price_without_yuan_is_checked(self):
        report = "国瓷材料 300285：止损位：150"
        result = validate_report(report, _context_with_guoci())
        self.assertFalse(result["ok"])
        blocked = next(c for c in result["claims"] if c["status"] == "blocked")
        self.assertEqual(blocked["value"], 150.0)

    def test_price_must_not_borrow_same_number_from_another_stock(self):
        data = _context_with_guoci()
        data["涨幅前30"] = {
            "stocks": [{"code": "600000", "name": "浦发银行", "price": 150.0}]
        }
        report = "国瓷材料（300285）等待回踩 150 元再买。"
        result = validate_report(report, data)
        self.assertFalse(result["ok"])
        blocked = next(c for c in result["claims"] if c["status"] == "blocked")
        self.assertEqual(blocked["code"], "300285")
        self.assertEqual(blocked["reason"], "price_outside_stock_evidence_range")

    def test_user_target_prices_cannot_authorize_themselves(self):
        data = _context_with_guoci()
        data["用户持仓"][0].update({
            "cost": 150.0,
            "target_low": 145.0,
            "target_high": 160.0,
            "stop_loss_price": 140.0,
        })

        result = validate_report("国瓷材料 300285：目标价 150 元。", data)

        self.assertFalse(result["ok"])
        blocked = next(c for c in result["claims"] if c["status"] == "blocked")
        self.assertEqual(blocked["reason"], "price_outside_stock_evidence_range")
        self.assertLess(blocked["allowed_high"], 100)

    def test_stock_without_price_source_is_blocked(self):
        data = {
            "可买候选池": {
                "stocks": [{
                    "code": "300285", "name": "国瓷材料",
                    "change_pct": 2.1, "amount_yi": 7.5, "circ_mv_yi": 120.0,
                }]
            }
        }
        result = validate_report("国瓷材料 300285：买入区间 75-78 元。", data)
        self.assertFalse(result["ok"])
        self.assertTrue(any(c["reason"] == "stock_has_no_price_evidence" for c in result["claims"]))

    def test_unknown_stock_with_trade_price_is_blocked(self):
        result = validate_report("未知股票（300999）：跌破 50 元止损。", _context_with_guoci())
        self.assertFalse(result["ok"])
        self.assertEqual(result["claims"][0]["reason"], "stock_not_in_injected_context")

    def test_unbound_trade_price_is_blocked(self):
        result = validate_report("建议等待回踩至 50 元再买。", _context_with_guoci())
        self.assertFalse(result["ok"])
        self.assertEqual(result["claims"][0]["reason"], "price_claim_not_bound_to_stock")

    def test_heading_context_binds_following_price_line(self):
        report = "### 国瓷材料（300285）\n\n- 止损位：150 元"
        result = validate_report(report, _context_with_guoci())
        self.assertFalse(result["ok"])
        blocked = next(c for c in result["claims"] if c["status"] == "blocked")
        self.assertEqual(blocked["code"], "300285")

    def test_both_ends_of_price_range_are_checked(self):
        report = "国瓷材料 300285：买入区间 78-150 元。"
        result = validate_report(report, _context_with_guoci())
        self.assertFalse(result["ok"])
        by_value = {c["value"]: c["status"] for c in result["claims"]}
        self.assertEqual(by_value[78.0], "passed")
        self.assertEqual(by_value[150.0], "blocked")

    def test_market_amount_is_not_treated_as_stock_price(self):
        report = "半导体板块主力资金净流入 150 亿元，成交额 300 亿元。"
        result = validate_report(report, _context_with_guoci())
        self.assertTrue(result["ok"])
        self.assertEqual(result["claim_count"], 0)

    def test_report_without_concrete_trade_price_passes(self):
        report = "国瓷材料 300285：价格数据不足，暂不给点位。"
        result = validate_report(report, {"可买候选池": {"stocks": [{"code": "300285", "name": "国瓷材料"}]}})
        self.assertTrue(result["ok"])
        self.assertEqual(result["claim_count"], 0)

    def test_finish_reason_length_can_never_finalize(self):
        result = validate_report_finalization(
            "国瓷材料 300285：回踩 78 元附近观察。",
            _context_with_guoci(),
            finish_reason="length",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("report_truncated", result["errors"])

    def test_passed_validation_receipt_is_persisted_with_report(self):
        validation = validate_report("国瓷材料 300285：回踩 78 元观察。", _context_with_guoci())
        self.assertTrue(validation["ok"])
        with TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.save_report(
                "morning", "早盘内参", "测试正文",
                date="2026-07-12", claim_validation=validation,
            )
            saved = store.get_report("morning", "2026-07-12")
        self.assertEqual(saved["claim_validation"]["status"], "passed")
        self.assertEqual(saved["claim_validation"]["validator"], "stock_price_claims_v1")


class _PushStore:
    def __init__(self, report: dict, root: Path):
        self.report = report
        self.marked = False
        self.root = root

    def get_report(self, _rtype: str, _date=None) -> dict:
        return self.report

    def mark_pushed(self, _rtype: str, _date: str) -> None:
        self.marked = True


class _PushTarget:
    def __init__(self):
        self.calls = 0

    async def push_report(self, _meta: dict, _report: dict) -> bool:
        self.calls += 1
        return True


class _PushRequest:
    def __init__(self, report: dict):
        self.tmp = TemporaryDirectory()
        self.store = _PushStore(report, Path(self.tmp.name))
        self.target = _PushTarget()
        self.app = SimpleNamespace(
            state=SimpleNamespace(store=self.store, feishu=self.target),
        )

    async def json(self) -> dict:
        return {}


@pytest.mark.asyncio
async def test_manual_push_rejects_legacy_unvalidated_report() -> None:
    request = _PushRequest({"date": "2026-07-12", "content_md": "legacy"})

    response = await push_report("morning", request)

    assert response.status_code == 409
    assert request.target.calls == 0
    assert request.store.marked is False


@pytest.mark.asyncio
async def test_manual_push_accepts_only_passed_validation_receipt() -> None:
    request = _PushRequest({
        "date": "2026-07-12",
        "content_md": "validated",
        "claim_validation": {"ok": True, "status": "passed"},
    })

    response = await push_report("morning", request)

    assert response == {"ok": True}
    assert request.target.calls == 1
    assert request.store.marked is True


if __name__ == "__main__":
    unittest.main()
