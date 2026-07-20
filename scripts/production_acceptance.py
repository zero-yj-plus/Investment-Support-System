#!/usr/bin/env python3
"""Read-only production launch acceptance for Hermes Trader.

The gate performs GET requests only.  It never changes settings, triggers jobs,
sends delivery messages, or asks the model to generate new content.

Exit codes:
    0  every blocking check passed (warnings are allowed)
    2  one or more launch blockers were found
    1  the acceptance runner itself failed
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import sys
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


CN_TZ = ZoneInfo("Asia/Shanghai")
CORE_ROUTES = (
    "premarket", "today", "risk", "tail", "review", "research", "history", "settings",
)
JSON_ENDPOINTS = {
    "status": "/api/status",
    "datasources": "/api/datasources",
    "capabilities": "/api/agent/capabilities?refresh=true",
    "agent_runs": "/api/agent/runs?limit=1",
    "delivery": "/api/delivery/readiness",
    "settings": "/api/settings",
    "simulation": "/api/simulation/status",
}
PAID_SOURCE_MARKERS = (
    "tushare", "wind", "万得", "choice", "ifind", "同花顺ifind",
    "paid_data", "paid_source", "付费数据", "积分数据",
)
SAFE_AGENT_CAPABILITIES = {
    "restricted",
    "direct_model_api",
    # 客户明确授权“完整产品 MCP”时，工具执行属于预期能力，不应被旧的
    # no-tool 上线闸误判为越权。
    "customer_full_product_mcp",
}


@dataclass(frozen=True)
class HttpObservation:
    key: str
    url: str
    status_code: int | None
    data: Any = None
    text: str = ""
    error: str = ""


@dataclass(frozen=True)
class CheckResult:
    id: str
    label: str
    status: str  # pass / block / warn
    detail: str
    evidence: Any = None

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        if self.evidence is None:
            value.pop("evidence", None)
        return value


Fetcher = Callable[[str, str, float], HttpObservation]


def _normalize_base_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("base URL is empty")
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an http(s) URL with a host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("base URL contains an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("base URL port is out of range")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def fetch_observation(key: str, url: str, timeout: float = 8.0) -> HttpObservation:
    """Fetch one URL without raising for remote/network failures."""
    request = Request(
        url,
        headers={
            "Accept": "application/json,text/html;q=0.9",
            "User-Agent": "hermes-production-acceptance/1.0",
        },
        method="GET",
    )
    status: int | None = None
    raw = b""
    error = ""
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-provided URL
            status = int(response.status)
            raw = response.read(2_000_001)
    except HTTPError as exc:
        status = int(exc.code)
        try:
            raw = exc.read(2_000_001)
        except Exception:  # noqa: BLE001 - diagnostic body is optional
            raw = b""
        error = f"HTTP {status}"
    except URLError as exc:
        error = f"network error: {exc.reason}"
    except TimeoutError:
        error = "network timeout"
    except OSError as exc:
        error = f"network error: {type(exc).__name__}"

    if len(raw) > 2_000_000:
        return HttpObservation(key, url, status, error="response exceeds 2 MB safety limit")
    text = raw.decode("utf-8", errors="replace")
    data: Any = None
    if text.lstrip().startswith(("{", "[")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            error = error or "invalid JSON response"
    return HttpObservation(key, url, status, data=data, text=text, error=error)


def collect_snapshot(
    base_url: str,
    *,
    timeout: float = 8.0,
    fetcher: Fetcher = fetch_observation,
) -> dict[str, HttpObservation]:
    """Collect the read-only evidence used by the pure evaluator."""
    base = _normalize_base_url(base_url)
    snapshot: dict[str, HttpObservation] = {}
    for key, path in JSON_ENDPOINTS.items():
        snapshot[key] = fetcher(key, base + path, timeout)
    snapshot["root"] = fetcher("root", base + "/", timeout)
    # URL fragments are client-side.  These requests still prove that every
    # canonical hash route resolves to the SPA shell, while /api/status proves
    # that the route is present in the server-published lifecycle contract.
    for route in CORE_ROUTES:
        key = f"route:{route}"
        snapshot[key] = fetcher(key, f"{base}/#/{route}", timeout)
    return snapshot


def _payload(snapshot: Mapping[str, HttpObservation], key: str) -> dict[str, Any]:
    observation = snapshot.get(key)
    return observation.data if observation and isinstance(observation.data, dict) else {}


def _check_http(snapshot: Mapping[str, HttpObservation]) -> CheckResult:
    failures: list[str] = []
    for key in (*JSON_ENDPOINTS.keys(), "root"):
        observation = snapshot.get(key)
        if observation is None:
            failures.append(f"{key}=missing")
            continue
        if observation.status_code != 200:
            failures.append(f"{key}=HTTP {observation.status_code or 'unreachable'}")
            continue
        if key != "root" and not isinstance(observation.data, dict):
            failures.append(f"{key}=invalid JSON")
    status = _payload(snapshot, "status")
    if status and status.get("ok") is not True:
        failures.append("status.ok!=true")
    root = snapshot.get("root")
    if root and root.status_code == 200 and not _is_spa_shell(root.text):
        failures.append("root=not SPA shell")
    if failures:
        return CheckResult("http_status", "HTTP 与状态接口", "block", "；".join(failures))
    core = status.get("core") if isinstance(status.get("core"), dict) else {}
    if core and core.get("ok") is False:
        return CheckResult(
            "http_status", "HTTP 与状态接口", "block",
            "服务可达，但核心完整性校验失败", {"core": core},
        )
    return CheckResult(
        "http_status", "HTTP 与状态接口", "pass",
        f"核心 API 与 SPA 均可达，version={status.get('version') or 'unknown'}",
    )


def _check_free_policy(snapshot: Mapping[str, HttpObservation]) -> CheckResult:
    data = _payload(snapshot, "datasources")
    policy = str(data.get("active_policy") or "")
    contract = str(data.get("source_contract") or "")
    if policy != "free_only":
        return CheckResult(
            "free_data_policy", "免费数据策略", "block",
            f"active_policy={policy or 'missing'}，必须为 free_only",
        )
    if contract != "field-route-v2":
        return CheckResult(
            "free_data_policy", "免费数据策略", "block",
            f"数据源契约缺失或不受支持：{contract or 'missing'}",
        )
    return CheckResult(
        "free_data_policy", "免费数据策略", "pass",
        "active_policy=free_only，字段路由契约为 field-route-v2",
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in {"", "0", "false", "none", "null", "disabled", "unset"}


def _paid_credential_flags(value: Any, path: tuple[str, ...] = ()) -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = (*path, key_text)
            joined = ".".join(child_path).lower().replace("-", "_")
            paid_context = any(marker in joined for marker in PAID_SOURCE_MARKERS)
            credential_field = any(
                token in key_text.lower()
                for token in ("token", "api_key", "apikey", "credential", "configured", "enabled")
            )
            if paid_context and credential_field and _truthy(child):
                hits.append(".".join(child_path))
            hits.extend(_paid_credential_flags(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(_paid_credential_flags(child, (*path, str(index))))
    return hits


def _check_paid_sources(snapshot: Mapping[str, HttpObservation]) -> CheckResult:
    data = _payload(snapshot, "datasources")
    settings = _payload(snapshot, "settings")
    active_paid: list[str] = []
    for source in data.get("sources") or []:
        if not isinstance(source, dict):
            continue
        haystack = " ".join(
            str(source.get(key) or "")
            for key in ("key", "name", "provider", "feeds", "role", "channel")
        ).lower().replace(" ", "")
        status = str(source.get("status") or "").lower()
        if any(marker.replace(" ", "") in haystack for marker in PAID_SOURCE_MARKERS) and status not in {
            "", "idle", "disabled", "off", "unconfigured",
        }:
            active_paid.append(str(source.get("name") or source.get("key") or "unknown"))
    flags = [
        *(f"datasources.{path}" for path in _paid_credential_flags(data)),
        *(f"settings.{path}" for path in _paid_credential_flags(settings)),
    ]
    if active_paid or flags:
        return CheckResult(
            "paid_sources", "付费数据源与 Token", "block",
            "检测到付费数据源或付费凭据处于活跃状态",
            {"active_paid_sources": active_paid, "credential_flags": flags},
        )
    return CheckResult(
        "paid_sources", "付费数据源与 Token", "pass",
        "运行态数据源目录未发现活跃付费源或付费 Token 配置",
    )


def _agent_integration_required(
    snapshot: Mapping[str, HttpObservation],
) -> tuple[bool, dict[str, Any]]:
    """Return whether launch explicitly depends on the optional AI integration.

    ``/api/status`` is the authoritative product contract: ``optional=true``
    means the deterministic core may launch without Hermes.  A saved custom
    connection (reported by either status or settings) or a future explicit
    ``enabled=true`` flag opts the deployment into the stricter Agent gates.

    Older servers that do not publish the optional contract remain fail-closed;
    the acceptance runner must not silently reinterpret a formerly required
    integration as optional.
    """
    status = _payload(snapshot, "status")
    hermes = status.get("hermes") if isinstance(status.get("hermes"), dict) else {}
    settings_payload = _payload(snapshot, "settings")
    settings = (
        settings_payload.get("settings")
        if isinstance(settings_payload.get("settings"), dict)
        else {}
    )
    explicitly_enabled = any(
        value is True
        for value in (
            hermes.get("enabled"),
            hermes.get("ai_enabled"),
            hermes.get("agent_enabled"),
            hermes.get("custom_connection"),
            settings.get("model_connection_custom"),
            settings.get("ai_enabled"),
            settings.get("agent_enabled"),
        )
    )
    optional_contract = hermes.get("optional")
    required = bool(explicitly_enabled or optional_contract is not True)
    return required, {
        "optional": optional_contract,
        "explicitly_enabled": explicitly_enabled,
        "custom_connection": bool(
            hermes.get("custom_connection") is True
            or settings.get("model_connection_custom") is True
        ),
    }


def _check_agent_capability(snapshot: Mapping[str, HttpObservation]) -> CheckResult:
    required, integration = _agent_integration_required(snapshot)
    if not required:
        return CheckResult(
            "agent_capability", "AI / Agent 可选集成", "pass",
            "Hermes 未显式启用；确定性核心不依赖模型或 Agent 权限即可上线",
            {**integration, "skipped": True},
        )
    response = _payload(snapshot, "capabilities")
    capability = response.get("capability") if isinstance(response.get("capability"), dict) else {}
    status_payload = _payload(snapshot, "status")
    hermes = status_payload.get("hermes") if isinstance(status_payload.get("hermes"), dict) else {}
    settings_payload = _payload(snapshot, "settings")
    settings = settings_payload.get("settings") if isinstance(settings_payload.get("settings"), dict) else {}

    reasons: list[str] = []
    capability_status = str(capability.get("status") or "")
    unexpected = capability.get("unexpected_toolsets")
    unexpected = unexpected if isinstance(unexpected, list) else ["contract_missing"]
    if capability.get("ready") is not True:
        reasons.append(f"ready=false ({capability.get('reason') or 'no reason'})")
    if capability_status not in SAFE_AGENT_CAPABILITIES:
        reasons.append(f"status={capability_status or 'missing'}")
    if unexpected:
        reasons.append("unexpected_toolsets=" + ",".join(str(x) for x in unexpected))
    if hermes.get("connected") is not True:
        reasons.append("Hermes /models 不可达")
    if settings.get("hermes_api_key_set") is not True:
        reasons.append("Hermes API 鉴权 key 未配置")

    if capability_status in {"restricted", "customer_full_product_mcp"}:
        if capability.get("profile_attestation") != "attested":
            reasons.append("profile_attestation 未通过")
        if capability.get("process_attestation") != "bound":
            reasons.append("process_attestation 未绑定 live child")
    if capability_status == "customer_full_product_mcp":
        if capability.get("tool_execution") != "server":
            reasons.append("完整产品 MCP 未声明服务端工具执行")
        allowed_toolsets = capability.get("allowed_toolsets")
        if not isinstance(allowed_toolsets, list) or "*" not in allowed_toolsets:
            reasons.append("完整产品 MCP 未声明客户授权的全工具范围")
    if capability_status == "direct_model_api":
        if capability.get("tool_execution") != "none":
            reasons.append("direct model API 仍声明工具执行面")
        if capability.get("enabled_toolsets") not in ([], None):
            reasons.append("direct model API 存在已启用工具集")

    if reasons:
        return CheckResult(
            "agent_capability", "Agent 权限门禁", "block", "；".join(reasons),
            {"status": capability_status, "unexpected_toolsets": unexpected},
        )
    return CheckResult(
        "agent_capability", "Agent 权限门禁", "pass",
        f"ready=true，status={capability_status}，unexpected_toolsets=[]",
    )


def _latest_agent_run(snapshot: Mapping[str, HttpObservation]) -> dict[str, Any] | None:
    response = _payload(snapshot, "agent_runs")
    items = response.get("items") if isinstance(response.get("items"), list) else []
    if items and isinstance(items[0], dict):
        return items[0]
    status = _payload(snapshot, "status")
    hermes = status.get("hermes") if isinstance(status.get("hermes"), dict) else {}
    summary = hermes.get("run_summary") if isinstance(hermes.get("run_summary"), dict) else {}
    return summary.get("last_run") if isinstance(summary.get("last_run"), dict) else None


def _check_agent_run(
    snapshot: Mapping[str, HttpObservation],
    *,
    now: datetime,
) -> CheckResult:
    required, integration = _agent_integration_required(snapshot)
    if not required:
        return CheckResult(
            "agent_last_run", "AI / Agent 真实运行证据", "pass",
            "Hermes 未显式启用；不要求外部 provider 登录或最近模型运行记录",
            {**integration, "skipped": True},
        )
    run = _latest_agent_run(snapshot)
    if not run:
        return CheckResult(
            "agent_last_run", "Agent 真实运行证据", "block",
            "没有可核验的 Agent 运行记录；外部 provider 登录与模型生成尚未被证明",
        )
    completed_at = _parse_cn_datetime(run.get("completed_at"))
    local_now = now.astimezone(CN_TZ) if now.tzinfo else now.replace(tzinfo=CN_TZ)
    try:
        tool_events = int(run.get("tool_events") or 0)
    except (TypeError, ValueError):
        tool_events = -1
    recent = bool(
        completed_at
        and -300 <= (local_now - completed_at).total_seconds() <= 24 * 3600
    )
    capability_status = str(run.get("capability_status") or "")
    tool_events_expected = capability_status == "customer_full_product_mcp"
    safe = bool(
        run.get("safe_to_use") is True
        and run.get("status") == "completed"
        and run.get("finish_reason") == "stop"
        and capability_status in SAFE_AGENT_CAPABILITIES
        and (tool_events >= 0 if tool_events_expected else tool_events == 0)
        and run.get("partial_output") is not True
        and recent
    )
    if not safe:
        return CheckResult(
            "agent_last_run", "Agent 真实运行证据", "block",
            "最近一次 Agent 运行不可安全使用，禁止把 completed 或部分输出冒充成功",
            {
                "run_id": run.get("run_id"), "status": run.get("status"),
                "finish_reason": run.get("finish_reason"),
                "capability_status": run.get("capability_status"),
                "tool_events": run.get("tool_events"), "partial_output": run.get("partial_output"),
                "safe_to_use": run.get("safe_to_use"), "error_code": run.get("error_code"),
                "completed_at": run.get("completed_at"), "recent_within_24h": recent,
            },
        )
    return CheckResult(
        "agent_last_run", "Agent 真实运行证据", "pass",
        f"最近运行 {run.get('run_id') or 'unknown'} 在 24 小时内完整结束且 safe_to_use=true",
    )


def _check_delivery(snapshot: Mapping[str, HttpObservation]) -> CheckResult:
    delivery = _payload(snapshot, "delivery")
    blockers = delivery.get("blockers") if isinstance(delivery.get("blockers"), list) else []
    targets = delivery.get("targets") if isinstance(delivery.get("targets"), list) else []
    ready = bool(
        delivery.get("launch_ready") is True
        and delivery.get("status") == "ready"
        and delivery.get("configured") is True
        and targets
        and not blockers
    )
    # 产品可通过 Web 登录和 Hermes/MCP 网关独立交付；若客户明确没有开启计划推送，
    # “没有外部消息渠道”是可选能力未配置，不应把整套产品误判为不可上线。
    # 只有已经开启计划推送却没有可用目标时，才是阻断项。
    if delivery.get("scheduled_delivery_enabled") is False:
        return CheckResult(
            "delivery_readiness", "外部消息推送", "warn",
            "计划推送未开启；Web 与网关交付不受影响，未向外部渠道发送消息",
            {
                "configured": delivery.get("configured"), "targets": targets,
                "manual_review_count": delivery.get("manual_review_count"), "blockers": blockers,
            },
        )
    if not ready:
        reason = blockers or [
            "push_channel_unconfigured" if delivery.get("configured") is not True else "push_target_unconfigured"
        ]
        return CheckResult(
            "delivery_readiness", "外部消息推送", "block",
            "飞书/外部交付未形成可验证闭环：" + ",".join(str(x) for x in reason),
            {
                "configured": delivery.get("configured"), "targets": targets,
                "manual_review_count": delivery.get("manual_review_count"), "blockers": blockers,
            },
        )
    return CheckResult(
        "delivery_readiness", "外部消息推送", "pass",
        f"channel={delivery.get('channel') or 'unknown'}，targets={len(targets)}，无待人工复核事务",
    )


def _check_simulation_boundary(snapshot: Mapping[str, HttpObservation]) -> CheckResult:
    simulation = _payload(snapshot, "simulation")
    emergency = (
        simulation.get("emergency_stop")
        if isinstance(simulation.get("emergency_stop"), dict)
        else {}
    )
    problems: list[str] = []
    if simulation.get("mode") != "simulation_only" or simulation.get("simulation_only") is not True:
        problems.append("未声明 simulation_only")
    if simulation.get("broker_connected") is not False:
        problems.append("broker_connected 必须为 false")
    if simulation.get("broker_interface_present") is not False:
        problems.append("broker_interface_present 必须为 false")
    if simulation.get("manual_approval_required") is not True:
        problems.append("未强制人工审批")
    if emergency.get("active") is True:
        problems.append("紧急停止仍处于开启状态")
    if problems:
        return CheckResult(
            "simulation_boundary", "模拟执行边界", "block", "；".join(problems),
            {
                "mode": simulation.get("mode"),
                "broker_connected": simulation.get("broker_connected"),
                "broker_interface_present": simulation.get("broker_interface_present"),
                "manual_approval_required": simulation.get("manual_approval_required"),
                "emergency_stop": emergency,
            },
        )
    return CheckResult(
        "simulation_boundary", "模拟执行边界", "pass",
        "仅本地模拟、无券商接口、强制人工审批，紧急停止未开启",
    )


def _parse_cn_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def _check_market_time(
    snapshot: Mapping[str, HttpObservation],
    *,
    now: datetime,
) -> CheckResult:
    status = _payload(snapshot, "status")
    context = status.get("market_context") if isinstance(status.get("market_context"), dict) else {}
    server_now = _parse_cn_datetime(context.get("now"))
    local_now = now.astimezone(CN_TZ) if now.tzinfo else now.replace(tzinfo=CN_TZ)
    problems: list[str] = []
    if server_now is None:
        problems.append("market_context.now 缺失或格式错误")
    elif abs((local_now - server_now).total_seconds()) > 300:
        problems.append(f"服务时钟偏差超过 5 分钟 ({server_now.isoformat()})")
    date_text = str(context.get("date") or "")
    if server_now and date_text != server_now.date().isoformat():
        problems.append("market_context.date 与北京时间不一致")
    if context.get("timezone") != "Asia/Shanghai":
        problems.append("timezone 不是 Asia/Shanghai")
    session = str(context.get("market_session") or "")
    is_trade_day = context.get("is_trade_day") is True
    if session not in {"non_trading", "premarket", "intraday", "tail", "review"}:
        problems.append(f"market_session={session or 'missing'}")
    if is_trade_day and context.get("trade_day") != date_text:
        problems.append("交易日当天 trade_day 与 date 不一致")
    if not is_trade_day and (session != "non_trading" or context.get("recommended_route") != "review"):
        problems.append("非交易日未进入 non_trading/review")
    for field in ("previous_trade_day", "trade_day", "next_trade_day"):
        try:
            datetime.fromisoformat(str(context.get(field) or ""))
        except ValueError:
            problems.append(f"{field} 缺失或格式错误")
    if problems:
        return CheckResult("market_clock", "交易日与服务时钟", "block", "；".join(problems))
    return CheckResult(
        "market_clock", "交易日与服务时钟", "pass",
        f"北京时间与交易日状态一致：{date_text} / {session}",
    )


def _check_data_freshness(
    snapshot: Mapping[str, HttpObservation],
    *,
    now: datetime,
) -> CheckResult:
    data = _payload(snapshot, "datasources")
    local_now = now.astimezone(CN_TZ) if now.tzinfo else now.replace(tzinfo=CN_TZ)
    report_time = _parse_cn_datetime(data.get("ts"))
    blocks: list[str] = []
    warnings: list[str] = []
    if report_time is None:
        blocks.append("datasources.ts 缺失或格式错误")
    elif abs((local_now - report_time).total_seconds()) > 600:
        blocks.append(f"数据源状态快照超过 10 分钟 ({report_time.isoformat()})")

    universe = data.get("universe") if isinstance(data.get("universe"), dict) else {}
    if universe:
        missing_exchanges = universe.get("missing_exchanges") or []
        if universe.get("complete") is False:
            blocks.append("全市场股票池不完整")
        if universe.get("stale") is True or universe.get("refresh_required") is True:
            blocks.append("全市场股票池陈旧或要求刷新")
        if missing_exchanges:
            blocks.append("缺交易所：" + ",".join(str(x) for x in missing_exchanges))
    else:
        warnings.append("股票池尚未预热，无法验证覆盖完整性")

    down_sources = [
        str(source.get("name") or source.get("key") or "unknown")
        for source in (data.get("sources") or [])
        if isinstance(source, dict) and source.get("status") == "down"
    ]
    if down_sources:
        warnings.append("最近失败源：" + ",".join(down_sources[:6]))
    if blocks:
        return CheckResult("data_freshness", "数据时点与股票池", "block", "；".join(blocks))
    if warnings:
        return CheckResult("data_freshness", "数据时点与股票池", "warn", "；".join(warnings))
    return CheckResult(
        "data_freshness", "数据时点与股票池", "pass",
        f"数据源状态快照新鲜，股票池 complete=true ({universe.get('count') or 'count unknown'})",
    )


def _check_deployment_topology(
    snapshot: Mapping[str, HttpObservation],
) -> CheckResult:
    """Require explicit, internally consistent regional source routing."""
    deployment = _payload(snapshot, "datasources").get("deployment")
    deployment = deployment if isinstance(deployment, dict) else {}
    profile = str(deployment.get("profile") or "")
    configured = str(deployment.get("configured") or "")
    route = str(deployment.get("china_public_route") or "")
    relay = bool(deployment.get("relay_configured"))
    problems: list[str] = []
    if profile not in {"cn_home", "cn_server", "overseas_server"}:
        problems.append("部署地域 profile 缺失或非法")
    if configured not in {"cn_home", "cn_server", "overseas_server"}:
        problems.append("交付配置仍为 auto；必须显式填写部署地域")
    if deployment.get("quote_primary") != "tdx_api":
        problems.append("报价主源不是 tdx_api")
    if deployment.get("quote_fallback") != "tencent":
        problems.append("报价备用源不是 tencent")
    expected_route = {
        "cn_home": "direct_low_concurrency",
        "cn_server": "direct",
        "overseas_server": "relay" if relay else "disabled",
    }.get(profile)
    if expected_route and route != expected_route:
        problems.append(f"{profile} 的国内公开源路由应为 {expected_route}")
    if problems:
        return CheckResult(
            "deployment_topology",
            "部署地域与网络路由",
            "block",
            "；".join(problems),
            {
                "profile": profile,
                "configured": configured,
                "china_public_route": route,
                "relay_configured": relay,
            },
        )
    return CheckResult(
        "deployment_topology",
        "部署地域与网络路由",
        "pass",
        f"profile={profile}，国内公开增强路由={route}，报价=TDX API→腾讯",
    )


def _check_recommendation_recovery(
    snapshot: Mapping[str, HttpObservation],
) -> CheckResult:
    status = _payload(snapshot, "status")
    ledger = (
        status.get("recommendation_ledger")
        if isinstance(status.get("recommendation_ledger"), dict) else {}
    )
    recovery = (
        ledger.get("startup_recovery")
        if isinstance(ledger.get("startup_recovery"), dict) else {}
    )
    failed = recovery.get("failed") if isinstance(recovery.get("failed"), list) else [
        "startup_recovery_unreported"
    ]
    if failed:
        return CheckResult(
            "recommendation_recovery",
            "推荐账本事务恢复",
            "block",
            "推荐双写事务存在未恢复项，禁止把账本当成完整",
            {"recovered": recovery.get("recovered"), "failed": failed[:20]},
        )
    return CheckResult(
        "recommendation_recovery",
        "推荐账本事务恢复",
        "pass",
        f"启动事务恢复完成，recovered={int(recovery.get('recovered') or 0)}，无失败项",
    )


def _is_spa_shell(text: str) -> bool:
    lowered = str(text or "").lower()
    # 允许发布版本通过查询串做静态资源 cache busting，例如
    # ``js/app.js?v=1.3.0.dev1``；门禁只验证入口脚本身份，不锁死 URL 结尾。
    return '<div id="app"' in lowered and 'src="js/app.js' in lowered


def _check_routes(snapshot: Mapping[str, HttpObservation]) -> CheckResult:
    status = _payload(snapshot, "status")
    lifecycle = status.get("route_lifecycle") if isinstance(status.get("route_lifecycle"), dict) else {}
    canonical = lifecycle.get("canonical") if isinstance(lifecycle.get("canonical"), list) else []
    visible = {
        str(row.get("path"))
        for row in canonical
        if isinstance(row, dict) and row.get("lifecycle") == "visible"
    }
    missing_contract = [route for route in CORE_ROUTES if route not in visible]
    load_failures: list[str] = []
    for route in CORE_ROUTES:
        observation = snapshot.get(f"route:{route}")
        if observation is None or observation.status_code != 200 or not _is_spa_shell(observation.text):
            load_failures.append(route)
    if missing_contract or load_failures:
        return CheckResult(
            "core_routes", "核心工作台路由", "block",
            "核心路由未完整注册或 SPA shell 无法加载",
            {"missing_contract": missing_contract, "load_failures": load_failures},
        )
    return CheckResult(
        "core_routes", "核心工作台路由", "pass",
        "8 个核心工作台路由均为 visible，且可加载 SPA shell",
    )


def evaluate_acceptance(
    snapshot: Mapping[str, HttpObservation],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure launch-gate evaluator; no I/O and no mutation."""
    effective_now = now or datetime.now(CN_TZ)
    checks = [
        _check_http(snapshot),
        _check_free_policy(snapshot),
        _check_paid_sources(snapshot),
        _check_agent_capability(snapshot),
        _check_agent_run(snapshot, now=effective_now),
        _check_delivery(snapshot),
        _check_simulation_boundary(snapshot),
        _check_market_time(snapshot, now=effective_now),
        _check_data_freshness(snapshot, now=effective_now),
        _check_deployment_topology(snapshot),
        _check_recommendation_recovery(snapshot),
        _check_routes(snapshot),
    ]
    counts = {status: sum(1 for check in checks if check.status == status) for status in ("pass", "block", "warn")}
    launch_ready = counts["block"] == 0
    return {
        "schema_version": "production-acceptance.v1",
        "generated_at": effective_now.astimezone(CN_TZ).isoformat(timespec="seconds"),
        "launch_ready": launch_ready,
        "summary": counts,
        "checks": [check.public() for check in checks],
    }


def exit_code_for(report: Mapping[str, Any]) -> int:
    return 0 if report.get("launch_ready") is True else 2


def _print_report(report: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("Hermes Trader production acceptance")
    print("=" * 44)
    for check in report.get("checks") or []:
        status = str(check.get("status") or "unknown").upper()
        print(f"[{status:5}] {check.get('label')}: {check.get('detail')}")
        if check.get("evidence") is not None:
            print("        " + json.dumps(check["evidence"], ensure_ascii=False, sort_keys=True))
    summary = report.get("summary") or {}
    print("-" * 44)
    print(
        f"launch_ready={str(bool(report.get('launch_ready'))).lower()} "
        f"pass={summary.get('pass', 0)} block={summary.get('block', 0)} warn={summary.get('warn', 0)}"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Hermes Trader production launch gate")
    parser.add_argument(
        "--base-url", default="127.0.0.1:18869",
        help="product base URL (default: 127.0.0.1:18869)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--timeout", type=float, default=8.0, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    try:
        args = _parser().parse_args(argv)
        if not 0.2 <= args.timeout <= 60:
            raise ValueError("timeout must be between 0.2 and 60 seconds")
        snapshot = collect_snapshot(args.base_url, timeout=args.timeout)
        report = evaluate_acceptance(snapshot)
        _print_report(report, as_json=bool(args.json))
        return exit_code_for(report)
    except Exception as exc:  # noqa: BLE001 - maps runner failure to the documented exit code
        message = {"launch_ready": False, "runtime_error": f"{type(exc).__name__}: {exc}"}
        if args is not None and getattr(args, "json", False):
            print(json.dumps(message, ensure_ascii=False))
        else:
            print(f"production acceptance runtime error: {message['runtime_error']}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
