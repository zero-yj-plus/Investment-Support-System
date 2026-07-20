"""交付回执的离线测试：不访问真实推送渠道。"""
from __future__ import annotations

import json
import stat
from types import SimpleNamespace

import pytest

from hermes_trader.api.routes import get_delivery_receipts, push_report
from hermes_trader.analysis.engine import Engine
from hermes_trader.delivery import DeliveryReceiptStore, push_report_with_receipt
from hermes_trader.knowledge.store import Store


def test_receipt_ledger_is_append_only_0600_and_increments_attempt(tmp_path) -> None:
    ledger = DeliveryReceiptStore(tmp_path)
    first = ledger.record(
        content_id="report:morning:2026-07-13",
        channel="feishu",
        status="failed",
        error="timeout",
        plan_id="CN-A-20260713",
        version=2,
    )
    original = ledger.path.read_bytes()
    second = ledger.record(
        content_id="report:morning:2026-07-13",
        channel="feishu",
        status="delivered",
        message_id="om_123",
        plan_id="CN-A-20260713",
        version=2,
    )

    final = ledger.path.read_bytes()
    rows, total = ledger.receipts(10)

    assert final.startswith(original)
    assert len(final) > len(original)
    assert stat.S_IMODE(ledger.path.stat().st_mode) == 0o600
    assert first["attempt"] == 1
    assert second["attempt"] == 2
    assert second["status"] == "delivered"
    assert second["message_id"] == "om_123"
    assert second["sent_at"]
    assert second["ack_at"] is None
    assert total == 2
    assert [row["attempt"] for row in rows] == [2, 1]


def test_acknowledged_and_partial_are_valid_append_only_states(tmp_path) -> None:
    ledger = DeliveryReceiptStore(tmp_path)
    partial = ledger.record(
        content_id="report:evening:2026-07-13",
        channel="gateway",
        status="partial",
    )
    acknowledged = ledger.record(
        content_id="report:evening:2026-07-13",
        channel="gateway",
        status="acknowledged",
    )

    assert partial["sent_at"] and partial["ack_at"] is None
    assert acknowledged["sent_at"] and acknowledged["ack_at"]


class _RichPusher:
    delivery_channel = "feishu"

    async def push_report(self, _meta: dict, _rec: dict):
        return {"ok": True, "status": "delivered", "message_id": "om_rich"}


class _FailingPusher:
    delivery_channel = "feishu"

    async def push_report(self, _meta: dict, _rec: dict):
        raise RuntimeError(
            "Authorization=top-secret Bearer token-value "
            "https://example.invalid/send?token=query-secret"
        )


@pytest.mark.asyncio
async def test_push_wrapper_keeps_bool_interface_and_records_rich_receipt_without_content(tmp_path) -> None:
    store = Store(tmp_path)
    rec = {
        "rtype": "morning",
        "date": "2026-07-13",
        "content_md": "这段正文不得进入回执账本",
        "plan_id": "CN-A-20260713",
        "plan_version": 2,
    }

    ok = await push_report_with_receipt(
        store=store,
        pusher=_RichPusher(),
        meta={"title": "盘前计划"},
        rec=rec,
        rtype="morning",
        trigger="automatic",
    )

    receipts, _ = DeliveryReceiptStore(store).receipts()
    raw = DeliveryReceiptStore(store).path.read_text(encoding="utf-8")
    assert ok is True
    assert receipts[0]["content_id"] == "report:morning:2026-07-13"
    assert receipts[0]["message_id"] == "om_rich"
    assert receipts[0]["plan_id"] == "CN-A-20260713"
    assert receipts[0]["version"] == 2
    assert receipts[0]["trigger"] == "automatic"
    assert "content_md" not in raw
    assert "这段正文" not in raw


@pytest.mark.asyncio
async def test_push_exception_returns_false_and_redacts_secrets_in_failed_receipt(tmp_path) -> None:
    store = Store(tmp_path)

    ok = await push_report_with_receipt(
        store=store,
        pusher=_FailingPusher(),
        meta={},
        rec={"rtype": "close", "date": "2026-07-13", "content_md": "private"},
        rtype="close",
        trigger="automatic",
    )

    receipt = DeliveryReceiptStore(store).receipts()[0][0]
    raw = DeliveryReceiptStore(store).path.read_text(encoding="utf-8")
    assert ok is False
    assert receipt["status"] == "failed_terminal"
    assert receipt["error_code"] == "push_exception"
    assert "top-secret" not in raw
    assert "token-value" not in raw
    assert "query-secret" not in raw
    assert "[REDACTED]" in raw
    assert "private" not in raw


class _Request:
    def __init__(self, state, *, body=None, query=None):
        self.app = SimpleNamespace(state=state)
        self._body = body or {}
        self.query_params = query or {}

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_manual_report_push_records_manual_receipt_and_keeps_response(tmp_path) -> None:
    store = Store(tmp_path)
    store.save_report(
        "morning",
        "早盘内参",
        "validated",
        date="2026-07-13",
        claim_validation={"ok": True, "status": "passed"},
    )
    state = SimpleNamespace(
        store=store,
        feishu=_RichPusher(),
        config=SimpleNamespace(push_via="feishu"),
    )

    response = await push_report("morning", _Request(state, body={"date": "2026-07-13"}))
    receipt = DeliveryReceiptStore(store).receipts()[0][0]

    assert response == {"ok": True}
    assert store.get_report("morning", "2026-07-13")["pushed"] is True
    assert receipt["trigger"] == "manual"
    assert receipt["status"] == "delivered"


@pytest.mark.asyncio
async def test_midday_draft_cannot_be_manually_pushed(tmp_path) -> None:
    store = Store(tmp_path)
    state = SimpleNamespace(store=store, feishu=_RichPusher(), config=SimpleNamespace(push_via="feishu"))

    response = await push_report("midday", _Request(state))

    assert response.status_code == 409
    assert "草案" in json.loads(response.body)["error"]
    assert DeliveryReceiptStore(store).receipts()[1] == 0


@pytest.mark.asyncio
async def test_scheduled_path_delivers_existing_validated_but_unpushed_report(tmp_path) -> None:
    store = Store(tmp_path)
    store.save_report(
        "morning",
        "早盘内参",
        "validated",
        date=store.today(),
        claim_validation={"ok": True, "status": "passed"},
    )
    engine = Engine(
        config=SimpleNamespace(push_via="feishu"),
        store=store,
        hermes=None,
        feishu=_RichPusher(),
    )

    result = await engine.generate_report("morning", push=True)
    receipt = DeliveryReceiptStore(store).receipts()[0][0]

    assert result["pushed"] is True
    assert receipt["trigger"] == "automatic"
    assert receipt["status"] == "delivered"
    assert receipt["content_id"].startswith("daily-plan:dp_")
    assert receipt["plan_id"].startswith("dp_")
    assert receipt["version"] == 2


@pytest.mark.asyncio
async def test_receipts_endpoint_is_newest_first_and_hard_bounds_limit(tmp_path) -> None:
    store = Store(tmp_path)
    ledger = DeliveryReceiptStore(store)
    for i in range(3):
        ledger.record(
            content_id=f"report:morning:2026-07-{13 + i:02d}",
            channel="feishu",
            status="delivered",
        )
    request = _Request(SimpleNamespace(store=store), query={"limit": "9999"})

    response = await get_delivery_receipts(request)

    assert response["ok"] is True
    assert response["limit"] == 500
    assert response["count"] == 3
    assert response["total"] == 3
    assert response["receipts"][0]["content_id"].endswith("2026-07-15")
    # 端点返回的是 JSON 元数据，不是推送正文。
    json.dumps(response, ensure_ascii=False)
