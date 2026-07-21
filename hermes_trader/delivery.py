"""推送交付回执。

交付回执与报告正文分开存储：此处只记录内容标识、渠道、尝试次数和平台结果，
不接收、不落盘报告正文、推送目标或认证信息。账本为 append-only JSONL，
便于区分「生成成功」、「已尝试推送」和「真正送达」。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("hermes.delivery")

DELIVERY_RECEIPT_SCHEMA_VERSION = "1.1"
DELIVERY_STATUSES = frozenset({
    "queued", "sending", "accepted", "delivered", "partial", "unknown",
    "failed", "failed_retryable", "failed_terminal", "acknowledged",
})
MAX_RECEIPT_LIMIT = 500

REQUIRED_DAILY_DELIVERIES = (
    ("morning", "盘前计划"),
    ("tail", "尾盘决策定稿"),
    ("close", "收盘复盘"),
    ("evening", "次日策略"),
    ("trend", "趋势票"),
    ("pdf_premarket", "盘前 PDF"),
    ("pdf_postmarket", "盘后 PDF"),
)

_APPEND_LOCK = threading.Lock()
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(authorization|bearer|token|secret|password|passwd|api[-_]?key|webhook)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|secret|password|api[-_]?key|sign|signature)=)[^&#\s]+"
)
_URL_RE = re.compile(r"(?i)https?://[^\s,;]+")


def utc_timestamp() -> str:
    """生成可排序、带时区的回执时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_daily_plan_id(client_id: str, trade_day: str) -> str:
    """跨页面、报告、PDF 与推送共用的确定性盘前计划 ID。"""
    key = f"hermes:{str(client_id or 'default')}:daily-plan:{str(trade_day)}"
    return f"dp_{uuid.uuid5(uuid.NAMESPACE_URL, key).hex}"


def _metadata_text(value, limit: int) -> str | None:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit] if text else None


def _safe_error(value) -> str | None:
    """错误可用于运维定位，但不得成为密钥或 webhook 响应的旁路日志。"""
    text = _metadata_text(value, 1000)
    if not text:
        return None
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    text = _URL_SECRET_RE.sub(lambda m: f"{m.group(1)}[REDACTED]", text)
    # webhook 常把凭据放在 URL path 而非 query；回执里不保留任何完整 URL。
    text = _URL_RE.sub("[URL_REDACTED]", text)
    return text[:500]


def _root_path(store_or_path) -> Path:
    # pathlib.Path.root 是路径的根部分（例如 "/"），不是 Store.root。
    # 因此必须先识别 path-like，再对 Store 鸭子类型取 root。
    if isinstance(store_or_path, (str, os.PathLike)):
        root = store_or_path
    else:
        root = getattr(store_or_path, "root", None)
    if root in (None, ""):
        raise ValueError("交付回执缺少数据目录")
    return Path(root)


def _plan_fields(rec: dict) -> tuple[str | None, int | str | None]:
    nested = rec.get("daily_plan")
    nested = nested if isinstance(nested, dict) else {}
    plan_id = _metadata_text(rec.get("plan_id") or nested.get("plan_id"), 160)
    version = (
        rec.get("plan_version")
        if rec.get("plan_version") not in (None, "")
        else rec.get("version")
    )
    if version in (None, ""):
        version = nested.get("plan_version") or nested.get("current_version") or 1
    if isinstance(version, str):
        version = _metadata_text(version, 40)
    elif isinstance(version, bool) or not isinstance(version, int):
        version = _metadata_text(version, 40)
    return plan_id, version


def report_content_id(rtype: str, rec: dict) -> str:
    explicit = _metadata_text(rec.get("content_id"), 240)
    if explicit:
        return explicit
    report_type = _metadata_text(rtype or rec.get("rtype"), 40) or "unknown"
    date = _metadata_text(rec.get("date"), 20) or "undated"
    return f"report:{report_type}:{date}"


def delivery_channel(pusher=None, config=None) -> str:
    """返回当前传输层名称，不猜测网关背后实际已连平台。"""
    explicit = _metadata_text(getattr(pusher, "delivery_channel", None), 40)
    if explicit:
        return explicit
    push_via = _metadata_text(getattr(config, "push_via", None), 40)
    if push_via:
        return push_via
    name = type(pusher).__name__.lower() if pusher is not None else ""
    if "gateway" in name:
        return "gateway"
    if "feishu" in name:
        return "feishu"
    return "notification"


def normalize_push_result(raw) -> dict:
    """兼容旧 bool 接口，但不再把「命令返回 True」写成「已送达」。"""
    if isinstance(raw, dict):
        requested_status = str(raw.get("status") or "").strip().lower()
        if requested_status in DELIVERY_STATUSES:
            status = requested_status
        else:
            status = "accepted" if bool(raw.get("ok")) else "failed_terminal"
        if status == "failed":
            status = "failed_terminal"
        ok = status in {"accepted", "delivered", "acknowledged"}
        safe_targets = []
        for target in raw.get("target_results") if isinstance(raw.get("target_results"), list) else []:
            if not isinstance(target, dict):
                continue
            safe_targets.append({
                "target": _metadata_text(target.get("target"), 80),
                "status": _metadata_text(target.get("status"), 40),
                "message_id": _metadata_text(target.get("message_id"), 240),
                "provider_code": _metadata_text(target.get("provider_code"), 80),
                "error": _safe_error(target.get("error")),
            })
        return {
            "ok": ok,
            "status": status,
            "message_id": _metadata_text(raw.get("message_id"), 240),
            "error": _safe_error(raw.get("error") or (None if ok else raw.get("message"))),
            "error_code": _metadata_text(raw.get("error_code"), 80),
            "target_results": safe_targets,
        }
    if isinstance(raw, tuple):
        ok = bool(raw[0]) if raw else False
        detail = raw[1] if len(raw) > 1 else None
        return {
            "ok": ok,
            "status": "accepted" if ok else "failed_terminal",
            "message_id": None,
            "error": None if ok else _safe_error(detail),
            "error_code": None,
            "target_results": [],
        }
    ok = bool(raw)
    return {
        "ok": ok,
        "status": "accepted" if ok else "failed_terminal",
        "message_id": None,
        "error": None if ok else "delivery returned false",
        "error_code": None,
        "target_results": [],
    }


class DeliveryReceiptStore:
    """JSONL 交付账本：只提供追加和读取，不提供覆盖或删除。"""

    def __init__(self, store_or_path):
        self.path = _root_path(store_or_path) / "delivery_receipts" / "receipts.v1.jsonl"

    def _rows_unlocked(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows: list[dict] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows

    def record(
        self,
        *,
        content_id: str,
        channel: str,
        status: str,
        content_hash: str | None = None,
        message_id: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
        plan_id: str | None = None,
        version: int | str | None = None,
        trigger: str = "automatic",
        attempted_at: str | None = None,
        attempt: int | None = None,
        delivery_id: str | None = None,
        target_results: list[dict] | None = None,
    ) -> dict:
        content_id = _metadata_text(content_id, 240)
        channel = _metadata_text(channel, 40)
        status = str(status or "").strip().lower()
        if not content_id or not channel:
            raise ValueError("交付回执缺少 content_id/channel")
        if status not in DELIVERY_STATUSES:
            raise ValueError("交付回执 status 不受支持")
        if attempt is not None and (isinstance(attempt, bool) or int(attempt) < 1):
            raise ValueError("交付回执 attempt 必须为正整数")

        now = utc_timestamp()
        with _APPEND_LOCK:
            rows = self._rows_unlocked()
            if attempt is None:
                prior: list[int] = []
                for row in rows:
                    if row.get("content_id") != content_id or row.get("channel") != channel:
                        continue
                    try:
                        prior.append(int(row.get("attempt") or 0))
                    except (TypeError, ValueError):
                        # 一条损坏旧回执不应阻断后续投递留痕。
                        continue
                attempt = max(prior, default=0) + 1
            else:
                attempt = int(attempt)

            sent_at = now if status in {"accepted", "delivered", "partial", "acknowledged"} else None
            record = {
                "schema_version": DELIVERY_RECEIPT_SCHEMA_VERSION,
                "receipt_id": f"dr_{uuid.uuid4().hex}",
                "delivery_id": _metadata_text(delivery_id, 160),
                "content_id": content_id,
                "content_hash": _metadata_text(content_hash, 64),
                "channel": channel,
                "attempt": attempt,
                "status": status,
                "message_id": _metadata_text(message_id, 240),
                "error": _safe_error(error),
                "error_code": _metadata_text(error_code, 80),
                "plan_id": _metadata_text(plan_id, 160),
                "version": version,
                "trigger": _metadata_text(trigger, 40) or "automatic",
                "attempted_at": _metadata_text(attempted_at, 40) or now,
                "sent_at": sent_at,
                "ack_at": now if status == "acknowledged" else None,
                "recorded_at": now,
                "target_results": target_results if isinstance(target_results, list) else [],
            }
            encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass
            fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                # 即使文件曾被宽松 umask/人工操作改权限，下次写入也会收紧回 0600。
                # Windows 没有 os.fchmod；回执追加不能因此失败。
                fchmod = getattr(os, "fchmod", None)
                try:
                    if fchmod is not None:
                        fchmod(fd, 0o600)
                    else:
                        os.chmod(self.path, 0o600)
                except OSError:
                    pass
                payload = memoryview(encoded.encode("utf-8"))
                while payload:
                    written = os.write(fd, payload)
                    if written <= 0:
                        raise OSError("交付回执追加写入失败")
                    payload = payload[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
        return record

    def receipts(self, limit: int = 100) -> tuple[list[dict], int]:
        try:
            bounded = max(1, min(int(limit), MAX_RECEIPT_LIMIT))
        except (TypeError, ValueError):
            bounded = 100
        with _APPEND_LOCK:
            rows = self._rows_unlocked()
        return list(reversed(rows[-bounded:])), len(rows)


async def push_report_with_receipt(
    *,
    store,
    pusher,
    meta: dict,
    rec: dict,
    rtype: str,
    trigger: str,
    config=None,
) -> bool:
    """以持久化 intent 包住外发，无论成败都追加审计回执。"""
    attempted_at = utc_timestamp()
    channel = delivery_channel(pusher, config)
    plan_id, version = _plan_fields(rec)
    content_id = report_content_id(rtype, rec)
    delivery_content_id = f"report:{str(rtype or rec.get('rtype') or 'unknown')}:{str(rec.get('date') or 'undated')}"
    from .delivery_outbox import DeliveryOutbox, TERMINAL_SUCCESS  # noqa: PLC0415 — 避免模块加载期循环

    outbox = DeliveryOutbox(store)
    try:
        intent = await asyncio.to_thread(
            outbox.prepare,
            content_id=delivery_content_id,
            content_hash=rec.get("content_hash"),
            channel=channel,
            version=version,
            trigger=trigger,
        )
    except ValueError as exc:
        # 同一交付身份曾经绑定过另一个内容指纹时 fail closed，
        # 绝不沿用旧“已发送”状态、也不自动发第二版。
        log.error("交付内容指纹冲突 [%s]: %s", delivery_content_id, exc)
        try:
            await asyncio.to_thread(
                DeliveryReceiptStore(store).record,
                content_id=content_id,
                content_hash=rec.get("content_hash"),
                channel=channel,
                status="failed_terminal",
                error="delivery content fingerprint conflict",
                error_code="content_hash_conflict",
                plan_id=plan_id,
                version=version,
                trigger=trigger,
                attempted_at=attempted_at,
            )
        except Exception:  # noqa: BLE001 -- 冲突已阻止，回执失败只记日志
            log.exception("交付指纹冲突回执落盘失败 [%s]", rtype)
        return False
    claim, intent = await asyncio.to_thread(outbox.claim, intent["delivery_id"])
    if claim == "failed_terminal" and trigger == "manual":
        await asyncio.to_thread(outbox.authorize_retry, intent["delivery_id"])
        claim, intent = await asyncio.to_thread(outbox.claim, intent["delivery_id"])
    if claim == "already_accepted":
        return True
    if claim != "claimed":
        log.warning("交付未重发 [%s]: %s", content_id, claim)
        return False

    outcome: dict
    sender = getattr(pusher, "push_report", None)
    if not callable(sender):
        outcome = {
            "ok": False,
            "status": "failed_terminal",
            "message_id": None,
            "error": "delivery channel unavailable",
            "error_code": "channel_unavailable",
        }
    else:
        try:
            outcome = normalize_push_result(await sender(meta, rec))
        except (asyncio.TimeoutError, TimeoutError) as exc:
            # 超时时对端可能已收到，盲重试会造成重复推送。
            outcome = {
                "ok": False,
                "status": "unknown",
                "message_id": None,
                "error": f"{type(exc).__name__}: {exc}",
                "error_code": "push_timeout_ambiguous",
                "target_results": [],
            }
        except Exception as exc:  # noqa: BLE001 - 推送失败必须先记回执，不击穿旧 bool 接口
            log.exception("报告推送异常 [%s]", rtype)
            outcome = {
                "ok": False,
                "status": "failed_terminal",
                "message_id": None,
                "error": f"{type(exc).__name__}: {exc}",
                "error_code": "push_exception",
                "target_results": [],
            }

    finished = await asyncio.to_thread(
        outbox.finish,
        intent["delivery_id"],
        expected_attempt=int(intent.get("attempts") or 0),
        status=outcome["status"],
        message_id=outcome.get("message_id"),
        error=outcome.get("error"),
        error_code=outcome.get("error_code"),
        target_results=outcome.get("target_results"),
    )
    if not finished.get("finish_applied"):
        log.warning(
            "忽略迟到交付结果 [%s] attempt=%s current=%s",
            content_id, intent.get("attempts"), finished.get("status"),
        )
        return str(finished.get("status")) in TERMINAL_SUCCESS

    try:
        ledger = DeliveryReceiptStore(store)
        await asyncio.to_thread(
            ledger.record,
            content_id=content_id,
            content_hash=rec.get("content_hash"),
            channel=channel,
            status=outcome["status"],
            message_id=outcome.get("message_id"),
            error=outcome.get("error"),
            error_code=outcome.get("error_code"),
            plan_id=plan_id,
            version=version,
            trigger=trigger,
            attempted_at=attempted_at,
            attempt=int(intent.get("attempts") or 1),
            delivery_id=intent["delivery_id"],
            target_results=outcome.get("target_results"),
        )
    except Exception:  # noqa: BLE001 - 回执层故障不能改变已完成的外部投递结果
        log.exception("交付回执落盘失败 [%s]", rtype)
    return bool(outcome["ok"])


async def delivery_readiness(*, store, pusher, config) -> dict:
    """非发送型交付预检：「程序存在」不等于「可交付」。"""
    from .delivery_outbox import DeliveryOutbox  # noqa: PLC0415

    enabled = bool(getattr(config, "push_enabled", False))
    configured = bool(getattr(pusher, "configured", False)) if pusher is not None else False
    preflight = {"configured": configured, "ready": configured, "targets": []}
    checker = getattr(pusher, "preflight", None)
    if callable(checker):
        try:
            value = await checker()
            if isinstance(value, dict):
                preflight = value
        except Exception:  # noqa: BLE001 — 预检失败必须 fail closed
            log.exception("交付预检异常")
            preflight = {"configured": configured, "ready": False, "targets": [], "blocker": "preflight_failed"}

    blockers: list[str] = []
    if not enabled:
        blockers.append("scheduled_push_disabled")
    if not bool(preflight.get("configured")):
        blockers.append("push_channel_unconfigured")
    elif not bool(preflight.get("ready")) or not preflight.get("targets"):
        blockers.append(str(preflight.get("blocker") or "push_target_unconfigured"))
    outbox_blockers = await asyncio.to_thread(DeliveryOutbox(store).blockers)
    if outbox_blockers:
        blockers.append("delivery_manual_review_required")
    blockers = list(dict.fromkeys(blockers))
    channels = await asyncio.to_thread(
        _delivery_channel_matrix,
        store,
        enabled=enabled,
        transport=delivery_channel(pusher, config),
        preflight=preflight,
    )
    return {
        "schema_version": "delivery-readiness.v2",
        "launch_ready": not blockers,
        "status": "ready" if not blockers else "blocked",
        "scheduled_delivery_enabled": bool(enabled and not blockers),
        "channel": delivery_channel(pusher, config),
        "configured": bool(preflight.get("configured")),
        "targets": list(preflight.get("targets") or []),
        "mode": preflight.get("mode"),
        "blockers": blockers,
        "manual_review_count": len(outbox_blockers),
        "channels": channels,
    }


def _delivery_channel_matrix(
    store,
    *,
    enabled: bool,
    transport: str,
    preflight: dict,
) -> list[dict]:
    """固定输出 Web/PDF/飞书/微信四条真实状态，不把“未配置”写成故障。"""
    receipts, _ = DeliveryReceiptStore(store).receipts(MAX_RECEIPT_LIMIT)
    success_statuses = {"accepted", "delivered", "acknowledged"}

    def logical_target(value) -> str | None:
        text = str(value or "").strip().casefold()
        if any(token in text for token in ("feishu", "lark", "飞书")):
            return "feishu"
        if any(token in text for token in ("wechat", "weixin", "wx", "微信")):
            return "wechat"
        return None

    def matches_channel(row: dict, key: str) -> bool:
        content_id = str(row.get("content_id") or "").casefold()
        row_channel = logical_target(row.get("channel"))
        target_keys = {
            logical_target(target.get("target"))
            for target in row.get("target_results") or []
            if isinstance(target, dict)
        }
        return bool(
            (key == "pdf" and "pdf" in content_id)
            or key == row_channel
            or key in target_keys
        )

    def target_outcomes(row: dict, key: str) -> list[dict]:
        return [
            target
            for target in row.get("target_results") or []
            if isinstance(target, dict) and logical_target(target.get("target")) == key
        ]

    def channel_succeeded(row: dict, key: str) -> bool:
        """Use per-target truth when a gateway receipt contains mixed outcomes."""
        outcomes = target_outcomes(row, key)
        if outcomes:
            return any(target.get("status") in success_statuses for target in outcomes)
        return row.get("status") in success_statuses

    def latest_for(key: str, *, success_only: bool = False) -> dict | None:
        for row in receipts:
            if matches_channel(row, key) and (
                not success_only or channel_succeeded(row, key)
            ):
                return row
        return None

    def receipt_view(row: dict | None, key: str | None = None) -> dict | None:
        if not isinstance(row, dict):
            return None
        outcome = None
        if key:
            matches = target_outcomes(row, key)
            if matches:
                outcome = next(
                    (item for item in matches if item.get("status") not in success_statuses),
                    matches[0],
                )
        return {
            "content_id": row.get("content_id"),
            "status": outcome.get("status") if outcome else row.get("status"),
            "attempted_at": row.get("attempted_at"),
            "sent_at": row.get("sent_at"),
            "error_code": (
                outcome.get("error_code") or outcome.get("error")
                if outcome else row.get("error_code")
            ),
        }

    today = str(getattr(store, "today", lambda: "")() or "")
    reports = getattr(store, "reports_of", lambda _date: {})(today) if today else {}
    report_rows = list(reports.values()) if isinstance(reports, dict) else []
    web_last = max(
        (str(row.get("created_at") or "") for row in report_rows if isinstance(row, dict)),
        default=None,
    )
    states = getattr(store, "get_job_states", lambda: {})()
    pdf_last = max(
        (
            str(state.get("last_ok") or "")
            for job_id, state in (states.items() if isinstance(states, dict) else [])
            if "pdf" in str(job_id).casefold() and isinstance(state, dict) and state.get("last_ok")
        ),
        default=None,
    )

    raw_targets = [str(item) for item in preflight.get("targets") or []]
    target_keys = {logical_target(item) for item in raw_targets}
    direct = logical_target(transport)
    # transport 只表示选中了哪种通道，不能证明该通道已填凭据/目标。
    # 例如 push_via=feishu 但 webhook 为空时，必须仍然显示“未配置”。
    if direct and bool(preflight.get("configured")):
        target_keys.add(direct)

    result = [
        {
            "key": "web", "label": "Web 页面", "kind": "internal_view",
            "role": "internal_view", "configured": True, "enabled": True,
            "ready": True, "state": "ready", "status": "ready",
            "last_success_at": web_last, "last_success": web_last,
            "latest_receipt": None,
        },
        {
            "key": "pdf", "label": "PDF 文件", "kind": "generated_file",
            "role": "generated_file", "configured": True, "enabled": True,
            "ready": True, "state": "ready", "status": "ready",
            "last_success_at": pdf_last, "last_success": pdf_last,
            "latest_receipt": receipt_view(latest_for("pdf")),
        },
    ]
    for key, label in (("feishu", "飞书"), ("wechat", "微信")):
        configured = key in target_keys
        latest = latest_for(key)
        if not configured:
            status = "unconfigured"
        elif not enabled:
            status = "disabled"
        elif bool(preflight.get("ready")):
            status = "ready"
        else:
            status = "needs_attention"
        last_successful = latest_for(key, success_only=True)
        last_success = (
            last_successful.get("sent_at") or last_successful.get("attempted_at")
            if last_successful else None
        )
        result.append({
            "key": key,
            "label": label,
            "kind": "external_message",
            "role": "external_message",
            "configured": configured,
            "enabled": bool(enabled and configured),
            "ready": status == "ready",
            "state": status,
            "status": status,
            "last_success_at": last_success,
            "last_success": last_success,
            "latest_receipt": receipt_view(latest, key),
        })
    return result


def daily_delivery_gate(store, trade_day: str) -> dict:
    """按交易日对账外部交付回执。

    这个闸只证明内容是否进入 outbox 并被渠道接受，不判断报告文件是否已
    在本地生成。保留 ``missing`` 字段兼容 v1 客户端，同时补充明确的交付语义，
    避免把“未发送”误解为“未生成”。
    """
    from .delivery_outbox import DeliveryOutbox, TERMINAL_SUCCESS  # noqa: PLC0415

    rows = DeliveryOutbox(store).rows(limit=1000)
    accepted = [
        row for row in rows
        if row.get("status") in TERMINAL_SUCCESS and trade_day in str(row.get("content_id") or "")
    ]
    items = []
    for key, label in REQUIRED_DAILY_DELIVERIES:
        matches = [
            row for row in accepted
            if (
                f":{key}:" in str(row.get("content_id") or "")
                or str(row.get("content_id") or "").startswith(f"{key}:")
                or key in str(row.get("content_id") or "")
            )
        ]
        items.append({
            "key": key,
            "label": label,
            "status": "accepted" if matches else "missing",
            "delivery_status": "accepted" if matches else "not_delivered",
            "status_label": "渠道已接受" if matches else "未发送或无成功回执",
            "delivery_ids": [row.get("delivery_id") for row in matches],
        })
    missing = [row["key"] for row in items if row["status"] != "accepted"]
    return {
        "schema_version": "daily-delivery-gate.v1",
        "gate_scope": "external_delivery",
        "generation_evaluated": False,
        "missing_semantics": "not_delivered",
        "trade_day": trade_day,
        "complete": not missing,
        "status": "complete" if not missing else "incomplete",
        "items": items,
        "missing": missing,
    }
