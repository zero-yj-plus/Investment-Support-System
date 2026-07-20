from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from hermes_trader.delivery import (
    DeliveryReceiptStore,
    daily_delivery_gate,
    delivery_readiness,
    push_report_with_receipt,
)
from hermes_trader.delivery_outbox import DeliveryOutbox
from hermes_trader.knowledge.store import Store
from hermes_trader.notify import GatewaySender
from hermes_trader.feishu.push import FeishuPusher
from hermes_trader.api import routes


class AcceptedPusher:
    delivery_channel = "gateway"
    configured = True

    def __init__(self):
        self.calls = 0

    async def preflight(self):
        return {"configured": True, "ready": True, "targets": ["weixin"], "mode": "gateway"}

    async def push_report(self, _meta, _rec):
        self.calls += 1
        return {
            "ok": True,
            "status": "accepted",
            "message_id": "msg-1",
            "target_results": [{"target": "weixin", "status": "accepted"}],
        }


@pytest.mark.asyncio
async def test_same_delivery_is_idempotent_after_acceptance(tmp_path) -> None:
    store = Store(tmp_path)
    pusher = AcceptedPusher()
    rec = {"rtype": "morning", "date": "2026-07-13", "content_md": "private", "version": 1}

    first = await push_report_with_receipt(
        store=store, pusher=pusher, meta={}, rec=rec, rtype="morning", trigger="automatic",
    )
    second = await push_report_with_receipt(
        store=store, pusher=pusher, meta={}, rec=rec, rtype="morning", trigger="automatic",
    )

    assert first is True and second is True
    assert pusher.calls == 1
    rows = DeliveryOutbox(store).rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "accepted"
    assert rows[0]["attempts"] == 1


class TimeoutPusher(AcceptedPusher):
    async def push_report(self, _meta, _rec):
        self.calls += 1
        raise asyncio.TimeoutError("response timed out")


@pytest.mark.asyncio
async def test_ambiguous_timeout_requires_manual_review_and_is_not_resent(tmp_path) -> None:
    store = Store(tmp_path)
    pusher = TimeoutPusher()
    rec = {"rtype": "close", "date": "2026-07-13", "content_md": "private"}

    assert await push_report_with_receipt(
        store=store, pusher=pusher, meta={}, rec=rec, rtype="close", trigger="automatic",
    ) is False
    assert await push_report_with_receipt(
        store=store, pusher=pusher, meta={}, rec=rec, rtype="close", trigger="automatic",
    ) is False

    assert pusher.calls == 1
    row = DeliveryOutbox(store).rows()[0]
    assert row["status"] == "unknown"
    assert row["error_code"] == "push_timeout_ambiguous"


def test_concurrent_prepare_has_one_deterministic_intent(tmp_path) -> None:
    outbox = DeliveryOutbox(tmp_path)

    def prepare():
        return outbox.prepare(content_id="report:tail:2026-07-13", channel="gateway", version=1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: prepare(), range(20)))

    assert len({row["delivery_id"] for row in rows}) == 1
    assert len(outbox.rows()) == 1


def test_same_delivery_identity_rejects_a_different_content_hash(tmp_path) -> None:
    outbox = DeliveryOutbox(tmp_path)
    first = outbox.prepare(
        content_id="report:close:2026-07-13",
        content_hash="a" * 64,
        channel="gateway",
        version=1,
    )
    claim, row = outbox.claim(first["delivery_id"])
    assert claim == "claimed"
    outbox.finish(row["delivery_id"], expected_attempt=1, status="accepted")

    with pytest.raises(ValueError, match="different content_hash"):
        outbox.prepare(
            content_id="report:close:2026-07-13",
            content_hash="b" * 64,
            channel="gateway",
            version=1,
        )


def test_crash_left_sending_can_be_manually_acknowledged_or_retried(tmp_path) -> None:
    outbox = DeliveryOutbox(tmp_path)
    acknowledged = outbox.prepare(
        content_id="report:morning:2026-07-13", channel="gateway",
    )
    claim, first_attempt = outbox.claim(acknowledged["delivery_id"])
    assert claim == "claimed"
    closed = outbox.acknowledge(
        acknowledged["delivery_id"], review_note="已在外部平台确认消息存在",
    )
    assert closed["status"] == "acknowledged"
    assert closed["resolved_at"]
    assert closed["resolution_note"] == "已在外部平台确认消息存在"
    assert outbox.blockers() == []
    stale = outbox.finish(
        acknowledged["delivery_id"], expected_attempt=first_attempt["attempts"],
        status="failed_terminal",
    )
    assert stale["finish_applied"] is False
    assert stale["status"] == "acknowledged"
    assert outbox.blockers() == []

    retryable = outbox.prepare(
        content_id="report:tail:2026-07-13", channel="gateway",
    )
    assert outbox.claim(retryable["delivery_id"])[0] == "claimed"
    with pytest.raises(ValueError, match="核验说明"):
        outbox.authorize_retry(retryable["delivery_id"], allow_ambiguous=True)
    reviewed = outbox.authorize_retry(
        retryable["delivery_id"], allow_ambiguous=True,
        review_note="已核对平台，确认允许重发",
    )
    assert reviewed["status"] == "failed_retryable"
    assert outbox.claim(retryable["delivery_id"])[0] == "claimed"


@pytest.mark.asyncio
async def test_sending_acknowledge_api_closes_readiness_blocker(tmp_path) -> None:
    store = Store(tmp_path)
    outbox = DeliveryOutbox(store)
    row = outbox.prepare(content_id="report:close:2026-07-13", channel="gateway")
    outbox.claim(row["delivery_id"])

    class Request:
        app = SimpleNamespace(state=SimpleNamespace(
            store=store, config=SimpleNamespace(push_via="gateway"), feishu=None,
        ))

        async def json(self):
            return {"review_note": "已在外部平台核对到消息"}

    result = await routes.acknowledge_delivery(row["delivery_id"], Request())
    assert result["ok"] is True
    assert DeliveryOutbox(store).rows()[0]["status"] == "acknowledged"


@pytest.mark.asyncio
async def test_outbox_api_reports_the_actual_v2_storage_contract(tmp_path) -> None:
    store = Store(tmp_path)
    DeliveryOutbox(store).prepare(
        content_id="report:close:2026-07-13",
        channel="gateway",
    )

    class Request:
        app = SimpleNamespace(state=SimpleNamespace(store=store))
        query_params = {}

    result = await routes.get_delivery_outbox(Request())

    assert result["schema_version"] == "delivery-outbox.v2"
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_readiness_fails_closed_without_bound_target(tmp_path) -> None:
    store = Store(tmp_path)

    class NoTarget:
        configured = True
        delivery_channel = "gateway"

        async def preflight(self):
            return {"configured": True, "ready": False, "targets": [], "blocker": "push_target_unconfigured"}

    result = await delivery_readiness(
        store=store,
        pusher=NoTarget(),
        config=SimpleNamespace(push_enabled=True, push_via="gateway"),
    )
    assert result["launch_ready"] is False
    assert result["scheduled_delivery_enabled"] is False
    assert "push_target_unconfigured" in result["blockers"]
    assert result["schema_version"] == "delivery-readiness.v2"
    channels = {row["key"]: row for row in result["channels"]}
    assert channels["web"]["status"] == "ready"
    assert channels["web"]["ready"] is True
    assert channels["web"]["kind"] == "internal_view"
    assert channels["pdf"]["status"] == "ready"
    assert channels["feishu"]["status"] == "unconfigured"
    assert channels["wechat"]["status"] == "unconfigured"


@pytest.mark.asyncio
async def test_selected_transport_is_not_reported_configured_without_credentials(tmp_path) -> None:
    store = Store(tmp_path)

    class MissingWebhook:
        configured = False
        delivery_channel = "feishu"

        async def preflight(self):
            return {"configured": False, "ready": False, "targets": []}

    result = await delivery_readiness(
        store=store,
        pusher=MissingWebhook(),
        config=SimpleNamespace(push_enabled=True, push_via="feishu"),
    )
    channels = {row["key"]: row for row in result["channels"]}

    assert result["launch_ready"] is False
    assert channels["feishu"]["configured"] is False
    assert channels["feishu"]["enabled"] is False
    assert channels["feishu"]["state"] == "unconfigured"


@pytest.mark.asyncio
async def test_readiness_exposes_configured_gateway_targets_per_channel(tmp_path) -> None:
    store = Store(tmp_path)

    class MultiTarget:
        configured = True
        delivery_channel = "gateway"

        async def preflight(self):
            return {
                "configured": True,
                "ready": True,
                "targets": ["weixin", "feishu"],
            }

    result = await delivery_readiness(
        store=store,
        pusher=MultiTarget(),
        config=SimpleNamespace(push_enabled=True, push_via="gateway"),
    )
    channels = {row["key"]: row for row in result["channels"]}

    assert result["launch_ready"] is True
    assert channels["feishu"]["status"] == "ready"
    assert channels["feishu"]["state"] == "ready"
    assert channels["feishu"]["ready"] is True
    assert channels["feishu"]["enabled"] is True
    assert channels["wechat"]["status"] == "ready"
    assert channels["wechat"]["enabled"] is True


@pytest.mark.asyncio
async def test_channel_matrix_keeps_last_success_when_latest_attempt_failed(tmp_path) -> None:
    store = Store(tmp_path)
    receipts = DeliveryReceiptStore(store)
    succeeded = receipts.record(
        content_id="report:close:2026-07-12",
        channel="gateway",
        status="accepted",
        attempted_at="2026-07-12 15:31:00",
        target_results=[{"target": "feishu", "status": "accepted"}],
    )
    receipts.record(
        content_id="report:close:2026-07-13",
        channel="gateway",
        status="failed_terminal",
        attempted_at="2026-07-13 15:31:00",
        error_code="delivery_rejected",
        target_results=[{"target": "feishu", "status": "failed_terminal"}],
    )

    class FeishuTarget:
        configured = True
        delivery_channel = "gateway"

        async def preflight(self):
            return {"configured": True, "ready": True, "targets": ["feishu"]}

    result = await delivery_readiness(
        store=store,
        pusher=FeishuTarget(),
        config=SimpleNamespace(push_enabled=True, push_via="gateway"),
    )
    channel = {row["key"]: row for row in result["channels"]}["feishu"]

    assert channel["latest_receipt"]["status"] == "failed_terminal"
    assert channel["last_success_at"] == succeeded["sent_at"]


@pytest.mark.asyncio
async def test_channel_matrix_does_not_turn_failed_target_into_success_after_ack(tmp_path) -> None:
    store = Store(tmp_path)
    receipts = DeliveryReceiptStore(store)
    receipts.record(
        content_id="report:morning:2026-07-13",
        channel="gateway",
        status="acknowledged",
        attempted_at="2026-07-13 08:31:00",
        target_results=[
            {"target": "feishu", "status": "accepted"},
            {
                "target": "weixin",
                "status": "failed_terminal",
                "error_code": "gateway_rate_limited",
            },
        ],
    )

    class MultiTarget:
        configured = True
        delivery_channel = "gateway"

        async def preflight(self):
            return {"configured": True, "ready": True, "targets": ["weixin", "feishu"]}

    result = await delivery_readiness(
        store=store,
        pusher=MultiTarget(),
        config=SimpleNamespace(push_enabled=True, push_via="gateway"),
    )
    channels = {row["key"]: row for row in result["channels"]}

    assert channels["feishu"]["last_success_at"] is not None
    assert channels["feishu"]["latest_receipt"]["status"] == "accepted"
    assert channels["wechat"]["last_success_at"] is None
    assert channels["wechat"]["latest_receipt"]["status"] == "failed_terminal"
    assert channels["wechat"]["latest_receipt"]["error_code"] == "gateway_rate_limited"


def test_daily_gate_requires_every_declared_delivery(tmp_path) -> None:
    store = Store(tmp_path)
    outbox = DeliveryOutbox(store)
    for key in ("morning", "tail", "close", "evening", "trend", "pdf_premarket", "pdf_postmarket"):
        row = outbox.prepare(content_id=f"report:{key}:2026-07-13", channel="gateway")
        _, claimed = outbox.claim(row["delivery_id"])
        outbox.finish(
            row["delivery_id"], expected_attempt=claimed["attempts"], status="accepted",
        )

    complete = daily_delivery_gate(store, "2026-07-13")
    assert complete["complete"] is True
    assert complete["missing"] == []
    assert complete["gate_scope"] == "external_delivery"
    assert complete["generation_evaluated"] is False
    assert complete["missing_semantics"] == "not_delivered"
    assert all(item["delivery_status"] == "accepted" for item in complete["items"])


@pytest.mark.asyncio
async def test_gateway_requires_every_target_and_preserves_partial_results(monkeypatch, tmp_path) -> None:
    cfg = SimpleNamespace(
        push_gateway_cli=str(tmp_path / "python"), push_gateway_targets=["weixin", "feishu"],
        client_name="test",
    )
    sender = GatewaySender(cfg)

    async def targets():
        return ["weixin", "feishu"]

    async def deliver(target, _body):
        return (target == "weixin", "rejected" if target == "feishu" else "")

    monkeypatch.setattr(sender, "_targets", targets)
    monkeypatch.setattr(sender, "_deliver_retry", deliver)
    result = await sender.push_report({}, {"title": "R", "date": "2026-07-13", "content_md": "x"})

    assert result["ok"] is False
    assert result["status"] == "partial"
    assert [row["status"] for row in result["target_results"]] == ["accepted", "failed_terminal"]


@pytest.mark.asyncio
async def test_gateway_discovery_ignores_internal_home_channel_without_platform(monkeypatch, tmp_path) -> None:
    cli = tmp_path / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
    cli.parent.mkdir(parents=True)
    cli.touch()
    (tmp_path / ".hermes" / ".env").write_text(
        "FEISHU_HOME_CHANNEL=oc_valid\nLIGHTCLAWBOT_HOME_CHANNEL=internal\n",
        encoding="utf-8",
    )
    sender = GatewaySender(SimpleNamespace(
        push_gateway_cli=str(cli), push_gateway_targets=[], client_name="test",
    ))

    async def fake_run(_args, **_kwargs):
        return 0, '{"platforms":{"weixin":[{"id":"wx"}],"lightclawbot":[]}}', ""

    monkeypatch.setattr(sender, "_run", fake_run)

    # 仅“已连接”不代表允许接收定时报告；只有 /sethome 持久化的
    # FEISHU_HOME_CHANNEL 才是有效目标。
    assert await sender.discover() == ["feishu"]


@pytest.mark.asyncio
async def test_gateway_requires_one_primary_platform_when_multiple_homes_exist(tmp_path) -> None:
    cli = tmp_path / ".hermes" / "hermes-agent" / "venv" / "bin" / "python"
    cli.parent.mkdir(parents=True)
    cli.touch()
    (tmp_path / ".hermes" / ".env").write_text(
        "FEISHU_HOME_CHANNEL=oc_feishu\nWEIXIN_HOME_CHANNEL=wx_user\n",
        encoding="utf-8",
    )
    sender = GatewaySender(SimpleNamespace(
        push_gateway_cli=str(cli), push_gateway_targets=[], client_name="test",
    ))

    assert await sender._targets() == []
    preflight = await sender.preflight()
    assert preflight["ready"] is False
    assert preflight["targets"] == []
    assert preflight["home_channel_only"] is True
    assert preflight["blocker"] == "push_primary_channel_ambiguous"


@pytest.mark.asyncio
async def test_gateway_bridge_uses_only_configured_home_platform(monkeypatch, tmp_path) -> None:
    sender = GatewaySender(SimpleNamespace(
        push_gateway_cli=str(tmp_path / "missing-python"),
        push_gateway_targets=["feishu"],
        push_gateway_bridge_url="http://172.17.0.1:18877/v1/delivery/home",
        push_gateway_bridge_token="test-token",
        client_name="test",
    ))
    calls: list[tuple[str, str, bool]] = []

    async def bridge(plat, body="", *, probe=False):
        calls.append((plat, body, probe))
        return True, "", {"ok": True, "home_channel_only": True}

    monkeypatch.setattr(sender, "_bridge_request", bridge)

    assert sender.configured is True
    preflight = await sender.preflight()
    assert preflight["ready"] is True
    assert preflight["targets"] == ["feishu"]
    result = await sender.push_report(
        {}, {"title": "R", "date": "2026-07-16", "content_md": "x"},
    )
    assert result["ok"] is True
    assert result["target_results"] == [
        {"target": "feishu", "status": "accepted", "error": None},
    ]
    assert all(row[0] == "feishu" for row in calls)
    assert not any(row[0] == "weixin" for row in calls)


@pytest.mark.asyncio
async def test_gateway_honours_reported_weixin_cooldown(monkeypatch, tmp_path) -> None:
    sender = GatewaySender(SimpleNamespace(
        push_gateway_cli=str(tmp_path / "python"), push_gateway_targets=["weixin"],
        client_name="test",
    ))
    calls = 0
    sleeps: list[float] = []

    async def deliver(_target, _body):
        nonlocal calls
        calls += 1
        if calls == 1:
            return False, "iLink sendmessage rate limited; cooldown active for 30.0s"
        return True, ""

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(sender, "_deliver", deliver)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    assert await sender._deliver_retry("weixin", "body") == (True, "")
    assert calls == 2
    assert sleeps == [30.5]


@pytest.mark.asyncio
async def test_feishu_multichunk_partial_is_not_flattened_to_terminal_failure(monkeypatch, tmp_path) -> None:
    cfg = SimpleNamespace(
        feishu_webhook="https://example.invalid/hook", feishu_secret="",
        feishu_app_id="", feishu_app_secret="", data_dir=tmp_path,
        client_name="test", client_id="test",
    )
    pusher = FeishuPusher(cfg)
    calls = 0

    async def send(_payload):
        nonlocal calls
        calls += 1
        pusher._last_send_ambiguous = False
        return calls == 1

    monkeypatch.setattr(pusher, "_send", send)
    result = await pusher.push_report({}, {
        "title": "R", "date": "2026-07-13", "rtype": "morning",
        "content_md": "中" * 7000,
    })

    assert calls == 2
    assert result["status"] == "partial"
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_unsupported_retry_does_not_mutate_outbox_state(tmp_path) -> None:
    store = Store(tmp_path)
    outbox = DeliveryOutbox(store)
    row = outbox.prepare(content_id="pdf:premarket:2026-07-13", channel="gateway")
    _, claimed = outbox.claim(row["delivery_id"])
    outbox.finish(
        row["delivery_id"], expected_attempt=claimed["attempts"], status="failed_terminal",
    )

    class Request:
        app = SimpleNamespace(state=SimpleNamespace(store=store))

        async def json(self):
            return {}

    response = await routes.retry_delivery(row["delivery_id"], Request())
    assert response.status_code == 409
    assert DeliveryOutbox(store).rows()[0]["status"] == "failed_terminal"
