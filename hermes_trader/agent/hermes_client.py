"""Hermes Agent 客户端 — 走 OpenAI 兼容 /v1/chat/completions 接口。

本地客户：本机 Hermes Agent 网关 http://127.0.0.1:8742/v1
云端客户：relay https://api.wr0204.com/v1
两者协议一致，仅 base_url / api_key 不同。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
from pathlib import Path
import time
from typing import AsyncIterator, Callable
from urllib.parse import urlsplit

import httpx
import yaml

from .run_store import AgentRunStore, stable_hash

log = logging.getLogger("hermes.client")

_MAX_RETRIES = 2          # 首字节前的瞬时失败重试次数（已产出内容则不重试，避免重复）
_RETRY_BACKOFF = 0.8      # 退避基数(秒)，按尝试次数线性增长
_CHECK_TTL = 20.0         # check_connection 结果缓存秒数：顶栏/健康面板每次刷新都探测网关会形成
                          # 探测风暴，连接状态变化容忍 ~20s 延迟可接受（与 _spot_all 等数据源缓存同量级）。


class HermesError(RuntimeError):
    pass


class _RetryStream(Exception):
    """内部信号：上游 5xx 等可重试错误。"""


def _parse_json_object(text: str) -> dict | None:
    """Parse a single JSON object with bounded fence/prefix tolerance."""
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline >= 0:
            stripped = stripped[first_newline + 1:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3].rstrip()
    candidates = [stripped]
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


class HermesClient:
    def __init__(self, config):
        self.config = config
        # check_connection 的 TTL 缓存：(探测目标键, 时间戳, 结果)。
        # 锁保证并发刷新只放行一次真实探测（单飞），其余命中缓存或等待，避免探测风暴。
        self._conn_cache: tuple[str, float, bool] | None = None
        self._conn_lock = asyncio.Lock()
        self._cap_cache: tuple[str, float, dict] | None = None
        self._cap_lock = asyncio.Lock()
        self.run_store = AgentRunStore(config.data_dir / "agent_runs.sqlite3")

    @property
    def base_url(self) -> str:
        return self.config.hermes_base_url.rstrip("/")

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.config.hermes_api_key:
            headers["Authorization"] = f"Bearer {self.config.hermes_api_key}"
        return headers

    def _gateway_label(self) -> str:
        """可记录的网关标识，只含 scheme/host/port，不带查询、用户信息或密钥。"""
        try:
            parsed = urlsplit(self.base_url)
            host = parsed.hostname or "unknown"
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{host}{port}/v1"
        except Exception:
            return "invalid_gateway"

    def _timeout_budget(self, purpose: str, explicit: float | None) -> float:
        """Bound interactive/model-JSON calls without shortening report jobs."""
        configured = float(getattr(self.config, "hermes_timeout", 300) or 300)
        if explicit is not None:
            return max(5.0, min(float(explicit), configured, 600.0))
        # 长篇研究不能与普通问答共用 120 秒预算。产业链/公司研究已改为
        # 服务端后台任务，浏览器只轮询状态，因此可使用完整 300 秒模型预算。
        prefix_caps = (
            ("chat:company", 300.0),
            ("chat:chain", 300.0),
            ("chat:", 120.0),
            ("cockpit:", 60.0),
            ("diagnosis:", 90.0),
            ("knowledge:", 90.0),
            ("diy:", 180.0),
        )
        cap = next(
            (value for prefix, value in prefix_caps if purpose.startswith(prefix)),
            configured,
        )
        return max(5.0, min(configured, cap, 600.0))

    @staticmethod
    def _retry_after_timeout(purpose: str) -> bool:
        """长篇研究超时后不重放整次请求，避免总等待翻倍。"""
        return not purpose.startswith(("chat:company", "chat:chain"))

    def _max_tokens_budget(self, purpose: str, explicit: int | None) -> int:
        """为推理型模型保留长篇研究的思考与正文预算。"""
        if explicit is not None:
            return max(1, int(explicit))
        configured = max(1, int(getattr(self.config, "hermes_max_tokens", 8000) or 8000))
        if purpose.startswith("chat:company"):
            return max(configured, 20_000)
        if purpose.startswith("chat:chain"):
            return max(configured, 16_000)
        return configured

    def _deepseek_report_controls(
        self,
        purpose: str,
        model: str,
        thinking_mode: str | None = None,
    ) -> dict:
        """DeepSeek 日报默认低强度思考；显式回退时可切换为非思考。"""
        if not purpose.startswith("report:") or not str(model).startswith("deepseek-"):
            return {}
        try:
            host = (urlsplit(self.base_url).hostname or "").lower()
        except Exception:
            return {}
        if host != "api.deepseek.com":
            return {}
        mode = str(thinking_mode or "enabled").strip().lower()
        if mode not in {"enabled", "disabled"}:
            mode = "enabled"
        controls = {"thinking": {"type": mode}}
        if mode == "enabled":
            controls["reasoning_effort"] = "low"
        return controls

    def _is_local_gateway(self) -> bool:
        # A user-selected localhost OpenAI-compatible provider is a direct
        # model connection, not implicitly a Hermes Agent gateway.
        if (
            bool(getattr(self.config, "model_connection_custom", False))
            and not bool(getattr(self.config, "model_uses_hermes_gateway", False))
        ):
            return False
        try:
            return (urlsplit(self.base_url).hostname or "").lower() in {
                "127.0.0.1", "localhost", "::1",
            }
        except Exception:
            return False

    @staticmethod
    async def probe_model_endpoint(base_url: str, api_key: str, model: str) -> dict:
        """探测未保存的 OpenAI 兼容连接。

        只请求 ``/models``，不发 prompt，不将 key 写入日志或返回值。
        URL/SSRF 安全由 API 路由在调用前完成。
        """
        endpoint = str(base_url or "").strip().rstrip("/")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
                response = await client.get(f"{endpoint}/models", headers=headers)
            if response.status_code == 200:
                return {"connected": True, "model": str(model or "")}
            if response.status_code in {401, 403}:
                return {"connected": False, "error": "API Key 未通过验证"}
            if response.status_code == 404:
                return {"connected": False, "error": "端点未提供 OpenAI 兼容的 /v1/models"}
            return {"connected": False, "error": f"模型服务返回 HTTP {response.status_code}"}
        except (httpx.TimeoutException, httpx.NetworkError):
            return {"connected": False, "error": "无法连接模型服务"}
        except Exception:
            return {"connected": False, "error": "模型连接测试失败"}

    def _profile_attestation(self) -> dict:
        """静态核验项目专属 Hermes profile。

        ``/toolsets`` 不包含默认 MCP，所以运行时端点为空仍不足以证明
        ``no_mcp``。项目 profile 还必须同时声明空 API toolset、禁用插件，
        并与当前端口对上。无 MCP 时要求 HERMES_SAFE_MODE；客户显式授权
        MCP 时则要求关闭该总开关，否则 Hermes 本身不会注册任何 MCP。
        """
        required = bool(getattr(self.config, "agent_require_profile_attestation", True))
        if not required:
            return {"ok": True, "status": "not_required", "profile_hash": ""}
        profile_dir = getattr(self.config, "agent_restricted_profile_dir", None)
        if not isinstance(profile_dir, Path):
            return {"ok": False, "status": "profile_not_configured", "profile_hash": ""}
        config_path = profile_dir / "config.yaml"
        env_path = profile_dir / ".env"
        try:
            profile = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            env: dict[str, str] = {}
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip("'\"")
            api_toolsets = ((profile.get("platform_toolsets") or {}).get("api_server"))
            plugins = profile.get("plugins") or {}
            mcps = profile.get("mcp_servers")
            mcp_names = sorted(mcps) if isinstance(mcps, dict) else []
            expected_mcps = sorted(
                set(getattr(self.config, "agent_allowed_mcp_servers", []) or [])
            )
            safe_mode = env.get("HERMES_SAFE_MODE", "").lower() in {"1", "true", "yes", "on"}
            expected_safe_mode = not bool(expected_mcps)
            parsed = urlsplit(self.base_url)
            backend_port = int(env.get("API_SERVER_PORT") or 0)
            expected_port = int(env.get("HERMES_PROXY_PORT") or 0)
            actual_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            safe = bool(
                isinstance(api_toolsets, list)
                and api_toolsets == []
                and isinstance(plugins, dict)
                and plugins.get("enabled") == []
                and isinstance(mcps, dict)
                and mcp_names == expected_mcps
                and safe_mode is expected_safe_mode
                and env.get("API_SERVER_ENABLED", "").lower() in {"1", "true", "yes", "on"}
                and env.get("API_SERVER_HOST") in {"127.0.0.1", "localhost"}
                and expected_port == actual_port
                and len(env.get("API_SERVER_KEY") or "") >= 16
            )
            attested = {
                "api_toolsets": api_toolsets,
                "plugins_disabled": plugins.get("enabled") == [],
                "mcp_count": len(mcps) if isinstance(mcps, dict) else -1,
                "mcp_names": mcp_names,
                "safe_mode": safe_mode,
                "host": env.get("API_SERVER_HOST") or "",
                "backend_port": backend_port,
                "proxy_port": expected_port,
            }
            return {
                "ok": safe,
                "status": "attested" if safe else "profile_contract_invalid",
                "profile_hash": stable_hash(attested),
            }
        except Exception:
            return {"ok": False, "status": "profile_unavailable", "profile_hash": ""}

    async def capability_preflight(self, *, force: bool = False) -> dict:
        """证明 Agent 服务端工具权限已收敛。

        公网或用户明确选择的本地 OpenAI 兼容端点是直接模型 API，
        本请求没有授予 tools，不存在 Hermes server-agent 工具执行面。
        只有明确标记为 Hermes 的本机端点才必须同时读到
        /capabilities 和 /toolsets；端点缺失、响应异常或存在未允许工具均 fail closed。
        """
        required = bool(getattr(self.config, "agent_require_restricted_toolsets", True))
        allowed = sorted(set(getattr(self.config, "agent_allowed_toolsets", []) or []))
        parsed_base = urlsplit(self.base_url)
        host = (parsed_base.hostname or "").lower()
        direct_hosts = set(getattr(self.config, "agent_direct_model_hosts", []) or [])
        custom_direct = bool(getattr(self.config, "model_connection_custom", False))
        custom_local_direct = bool(
            custom_direct
            and not bool(getattr(self.config, "model_uses_hermes_gateway", False))
            and host in {"127.0.0.1", "localhost", "::1"}
            and parsed_base.scheme in {"http", "https"}
            and parsed_base.username is None
            and parsed_base.password is None
            and not parsed_base.query
            and not parsed_base.fragment
            and parsed_base.path.rstrip("/") == "/v1"
        )
        try:
            literal = ipaddress.ip_address(host)
            custom_host_safe = literal.is_global
        except ValueError:
            custom_host_safe = bool(
                host and "." in host
                and not host.endswith((".local", ".localhost", ".internal", ".home", ".lan"))
            )
        direct_transport_safe = bool(
            parsed_base.scheme == "https"
            and parsed_base.username is None
            and parsed_base.password is None
            and (
                parsed_base.port in {None, 443}
                or (custom_direct and parsed_base.port is not None and 1 <= parsed_base.port <= 65535)
            )
            and not parsed_base.query
            and not parsed_base.fragment
            and parsed_base.path.rstrip("/") == "/v1"
        )
        if (
            custom_local_direct
            or (
                not self._is_local_gateway()
                and (host in direct_hosts or (custom_direct and custom_host_safe))
                and direct_transport_safe
            )
        ):
            return {
                "ready": True,
                "required": required,
                "status": "direct_model_api",
                "reason": "直接模型 API；请求未授予工具",
                "runtime_mode": "direct_model_api",
                "tool_execution": "none",
                "allowed_toolsets": allowed,
                "enabled_toolsets": [],
                "unexpected_toolsets": [],
                "checked_at": time.time(),
            }
        if not self._is_local_gateway():
            return {
                "ready": False,
                "required": True,
                "status": "remote_endpoint_unapproved",
                "reason": "远程 AI 端点不在 HTTPS 直连模型白名单",
                "runtime_mode": "unknown",
                "tool_execution": "unknown",
                "allowed_toolsets": allowed,
                "enabled_toolsets": [],
                "unexpected_toolsets": [],
                "checked_at": time.time(),
            }

        key = f"{self.base_url}|{self.config.hermes_api_key or ''}|{','.join(allowed)}|{required}"
        ttl = float(getattr(self.config, "agent_capability_cache_sec", 20) or 20)
        now = time.monotonic()
        cached = self._cap_cache
        if not force and cached and cached[0] == key and now - cached[1] < ttl:
            return dict(cached[2])
        async with self._cap_lock:
            cached = self._cap_cache
            if not force and cached and cached[0] == key and time.monotonic() - cached[1] < ttl:
                return dict(cached[2])
            result = await self._probe_capabilities(required=required, allowed=allowed)
            self._cap_cache = (key, time.monotonic(), result)
            return dict(result)

    async def _probe_capabilities(self, *, required: bool, allowed: list[str]) -> dict:
        base = self.base_url
        attestation = self._profile_attestation() if self._is_local_gateway() else {
            "ok": False,
            "status": "remote_server_agent_unattested",
            "profile_hash": "",
        }
        try:
            async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
                cap_resp, tools_resp, process_resp = await asyncio.gather(
                    client.get(f"{base}/capabilities", headers=self._headers()),
                    client.get(f"{base}/toolsets", headers=self._headers()),
                    client.get(f"{base}/attestation", headers=self._headers()),
                )
            if (
                cap_resp.status_code != 200
                or tools_resp.status_code != 200
                or process_resp.status_code != 200
            ):
                raise ValueError("capability_endpoint_unavailable")
            capabilities = cap_resp.json()
            toolsets = tools_resp.json()
            runtime = capabilities.get("runtime") if isinstance(capabilities, dict) else None
            data = toolsets.get("data") if isinstance(toolsets, dict) else None
            if not isinstance(runtime, dict) or not isinstance(data, list):
                raise ValueError("capability_contract_invalid")
            runtime_mode = str(runtime.get("mode") or "")
            tool_execution = str(runtime.get("tool_execution") or "")
            if runtime_mode != "server_agent" or tool_execution != "server":
                raise ValueError("capability_runtime_unproven")
            process_attestation = process_resp.json()
            if not isinstance(process_attestation, dict):
                raise ValueError("process_attestation_invalid")
            signature = str(process_attestation.pop("signature", ""))
            canonical = json.dumps(
                process_attestation, sort_keys=True, separators=(",", ":")
            ).encode()
            expected_signature = hmac.new(
                str(self.config.hermes_api_key).encode(), canonical, hashlib.sha256
            ).hexdigest()
            expected_home_hash = hashlib.sha256(
                str(getattr(self.config, "agent_restricted_profile_dir", "")).encode()
            ).hexdigest()
            expected_safe_mode = not bool(
                getattr(self.config, "agent_allowed_mcp_servers", []) or []
            )
            process_bound = bool(
                signature
                and hmac.compare_digest(signature, expected_signature)
                and process_attestation.get("schema") == "restricted-hermes-attestation.v1"
                and process_attestation.get("profile_hash") == attestation.get("profile_hash")
                and process_attestation.get("home_hash") == expected_home_hash
                and process_attestation.get("child_alive") is True
                and process_attestation.get("safe_mode") is expected_safe_mode
            )
            if not process_bound:
                raise ValueError("process_attestation_unbound")
            enabled = sorted({
                str(item.get("name") or "").strip()
                for item in data if isinstance(item, dict) and item.get("enabled") is True
                if str(item.get("name") or "").strip()
            })
            unexpected = [] if "*" in allowed else sorted(set(enabled) - set(allowed))
            ready = not unexpected and bool(attestation.get("ok"))
            full_product_mcp = bool(
                "*" in allowed
                and getattr(self.config, "agent_allowed_mcp_servers", [])
            )
            status = (
                "customer_full_product_mcp"
                if ready and full_product_mcp
                else ("restricted" if ready else "blocked_unrestricted_tools")
            )
            reason = (
                (
                    "客户已授权完整产品 MCP"
                    if full_product_mcp else "服务端工具权限已收敛"
                )
                if ready else (
                    f"检测到 {len(unexpected)} 个未授权工具集"
                    if unexpected else "项目专属 profile 未通过 no-MCP 核验"
                )
            )
            return {
                "ready": ready,
                "required": required,
                "status": status,
                "reason": reason,
                "runtime_mode": runtime_mode,
                "tool_execution": tool_execution,
                "allowed_toolsets": list(allowed),
                "enabled_toolsets": enabled,
                "unexpected_toolsets": unexpected,
                "profile_attestation": str(attestation.get("status") or "unknown"),
                "profile_hash": str(attestation.get("profile_hash") or "")[:12],
                "process_attestation": "bound",
                "checked_at": time.time(),
            }
        except Exception as exc:  # 只返回稳定错误码，不泄露 URL/响应体
            log.warning("Hermes Agent 权限预检失败: %s", type(exc).__name__)
            return {
                "ready": False,
                "required": required,
                "status": "capability_unavailable",
                "reason": "无法证明服务端工具权限已收敛",
                "runtime_mode": "unknown",
                "tool_execution": "unknown",
                "allowed_toolsets": list(allowed),
                "enabled_toolsets": [],
                "unexpected_toolsets": [],
                "profile_attestation": str(attestation.get("status") or "unknown"),
                "profile_hash": str(attestation.get("profile_hash") or "")[:12],
                "process_attestation": "unavailable",
                "checked_at": time.time(),
            }

    async def check_connection(self) -> bool:
        """探测网关是否真正可用（GET /models 返回 200）。
        只把 200 视为已连接：401/403(鉴权失败)、404(路径错)、5xx(故障) 都不算连通，
        避免顶栏状态灯在 key 错误/网关半死时仍显示绿灯误导用户。

        结果带 ~20s TTL 缓存：顶栏/健康面板高频轮询 /status 若每次都真发探测会对网关
        形成探测风暴。缓存键含 base_url+api_key，settings 热改网关地址/Key 后旧缓存自动失效
        （键不命中即重新探测），不会拿旧网关的连通状态误导。"""
        key = f"{self.base_url}|{self.config.hermes_api_key or ''}"
        now = time.monotonic()
        cached = self._conn_cache
        cache_ttl = _CHECK_TTL if cached and cached[2] else min(5.0, _CHECK_TTL)
        if cached and cached[0] == key and now - cached[1] < cache_ttl:
            return cached[2]
        async with self._conn_lock:
            # 持锁后复检：等锁期间可能已有并发探测刷新了缓存（单飞），命中即直接返回
            cached = self._conn_cache
            cache_ttl = _CHECK_TTL if cached and cached[2] else min(5.0, _CHECK_TTL)
            if cached and cached[0] == key and time.monotonic() - cached[1] < cache_ttl:
                return cached[2]
            result = await self._probe_connection()
            self._conn_cache = (key, time.monotonic(), result)
            return result

    async def _probe_connection(self) -> bool:
        try:
            # trust_env=False：Hermes 网关直连其配置地址，绝不走数据层的境内 SOCKS 代理
            # （DATA_RELAY_PROXY 会把代理设进进程级环境变量，httpx 默认会读取并误用）。
            async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers())
                return resp.status_code == 200
        except Exception:
            return False

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
        meta: dict | None = None,
        model: str | None = None,
        purpose: str = "unspecified",
        client_request_id: str | None = None,
        timeout_sec: float | None = None,
        thinking_mode: str | None = None,
    ) -> AsyncIterator[str]:
        """流式对话，逐段 yield 文本增量。

        meta：可选可变 dict，流结束后会被写入 {"finish_reason": ...}，
        供上层判断是否被 max_tokens 截断（finish_reason == "length"）。
        temperature/max_tokens：None 时取 config 默认（可在 .env/settings 配置）。
        """
        if temperature is None:
            temperature = getattr(self.config, "hermes_temperature", 0.4)
        explicit_max_tokens = max_tokens
        max_tokens = self._max_tokens_budget(purpose, max_tokens)
        selected_model = model or self.config.hermes_model
        report_controls = self._deepseek_report_controls(
            purpose, selected_model, thinking_mode
        )
        if report_controls and explicit_max_tokens is None:
            max_tokens = max(max_tokens, 24_000)
        payload = {
            "model": selected_model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        payload.update(report_controls)
        timeout_budget = self._timeout_budget(purpose, timeout_sec)
        timeout = httpx.Timeout(timeout_budget, connect=min(15.0, timeout_budget))

        input_bytes = len(json.dumps(messages, ensure_ascii=False, default=str).encode("utf-8"))
        run_id = self.run_store.start_run(
            purpose=purpose,
            model=selected_model,
            gateway=self._gateway_label(),
            input_hash=stable_hash(messages),
            input_bytes=input_bytes,
            client_request_id=client_request_id,
        )
        if meta is not None:
            meta["run_id"] = run_id
            meta["timeout_sec"] = timeout_budget
        terminal = False
        output_hasher = hashlib.sha256()
        output_bytes = 0
        finish_reason = ""

        try:
            capability = await self.capability_preflight()
            self.run_store.mark_capability(run_id, capability)
            if meta is not None:
                meta["agent_capability_status"] = capability.get("status")
            # ready 是硬边界，不能通过把 config.required 改 false 绕过。
            # 直接模型 API 的安全分支本身会返回 ready=true。
            if not capability.get("ready"):
                self.run_store.finish(
                    run_id,
                    status="blocked",
                    error_code=str(capability.get("status") or "capability_blocked"),
                )
                terminal = True
                raise HermesError(
                    "Hermes Agent 权限未通过门禁，已拒绝发送客户数据。"
                    "请先将 API server 工具集收敛到产品允许范围。"
                )

            last_err: Exception | None = None
            last_error_code = "gateway_unavailable"
            for attempt in range(_MAX_RETRIES + 1):
                produced = False
                tool_started = False
                event_name = ""
                finish_reason = ""
                stop_retrying = False
                if meta is not None:
                    meta.pop("finish_reason", None)
                self.run_store.increment_attempt(run_id)
                try:
                    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                        async with client.stream(
                            "POST",
                            f"{self.base_url}/chat/completions",
                            headers=self._headers(),
                            json=payload,
                        ) as resp:
                            if resp.status_code != 200:
                                body = (await resp.aread()).decode("utf-8", "replace")[:300]
                                log.warning("Hermes 上游 %s 响应体: %s", resp.status_code, body)
                                if resp.status_code >= 500 or resp.status_code == 429:
                                    raise _RetryStream(f"上游 {resp.status_code}")
                                if resp.status_code in (401, 403):
                                    hint = "鉴权失败，请检查 API Key"
                                elif resp.status_code == 404:
                                    hint = "模型名或接口路径有误"
                                else:
                                    hint = "请求被拒，可能为上下文超长或参数错误，详见服务端日志"
                                raise HermesError(f"Hermes 网关返回 {resp.status_code}（{hint}）")
                            async for line in resp.aiter_lines():
                                line = line.strip()
                                if line.startswith("event:"):
                                    event_name = line[6:].strip()
                                    continue
                                if not line.startswith("data:"):
                                    continue
                                data = line[5:].strip()
                                if event_name == "hermes.tool.progress":
                                    # 默认仍 fail closed；只有客户配置明确授权 MCP
                                    # 工具事件时才继续。无论是否授权，只要工具已开始，
                                    # 后续失败都不可自动重试，避免写操作重复执行。
                                    try:
                                        event = json.loads(data)
                                    except json.JSONDecodeError:
                                        event = {}
                                    if not isinstance(event, dict):
                                        event = {}
                                    tool_name = str(event.get("tool") or "unknown")
                                    event_status = str(event.get("status") or "unknown")
                                    # Set both guards before touching the audit store.  If
                                    # the disk is full/locked/corrupt, the side-effect risk
                                    # still exists and the request must never be replayed.
                                    tool_started = True
                                    try:
                                        self.run_store.record_tool_event(
                                            run_id,
                                            tool_name=tool_name,
                                            tool_call_id=str(event.get("toolCallId") or ""),
                                            status=event_status,
                                            attempt=attempt + 1,
                                        )
                                    except Exception as audit_exc:
                                        log.error("Agent tool event audit write failed: %s", audit_exc)
                                    if not bool(getattr(self.config, "agent_allow_tool_events", False)):
                                        terminal = True
                                        try:
                                            self.run_store.finish(
                                                run_id,
                                                status="unknown",
                                                output_hash=output_hasher.hexdigest(),
                                                output_bytes=output_bytes,
                                                finish_reason=finish_reason,
                                                error_code="capability_violation_manual_review",
                                                partial_output=output_bytes > 0,
                                            )
                                        except Exception as audit_exc:
                                            log.error("Agent terminal audit write failed: %s", audit_exc)
                                        raise HermesError(
                                            "Hermes Agent 运行期出现未授权工具事件，"
                                            "已中止并转人工复核。"
                                        )
                                    event_name = ""
                                    continue
                                event_name = ""
                                if data == "[DONE]":
                                    if finish_reason != "stop":
                                        raise HermesError("Hermes Agent 未返回可采信的完整终态")
                                    if not produced:
                                        raise _RetryStream("返回空响应")
                                    self.run_store.finish(
                                        run_id,
                                        status="completed",
                                        output_hash=output_hasher.hexdigest(),
                                        output_bytes=output_bytes,
                                        finish_reason=finish_reason,
                                    )
                                    terminal = True
                                    return
                                try:
                                    chunk = json.loads(data)
                                except json.JSONDecodeError:
                                    continue
                                choices = chunk.get("choices") or []
                                if not choices:
                                    continue
                                fr = choices[0].get("finish_reason")
                                if fr:
                                    finish_reason = str(fr)
                                    if meta is not None:
                                        meta["finish_reason"] = finish_reason
                                # Hermes 流式返回 error 不能被当作完整成稿。
                                if finish_reason and finish_reason != "stop":
                                    raise HermesError(
                                        f"Hermes Agent 运行未完成（{finish_reason}）"
                                    )
                                content = (choices[0].get("delta") or {}).get("content")
                                if content:
                                    produced = True
                                    encoded = str(content).encode("utf-8")
                                    output_hasher.update(encoded)
                                    output_bytes += len(encoded)
                                    yield str(content)
                    if finish_reason != "stop":
                        raise HermesError("Hermes Agent 流结束但未返回可采信的 stop 终态")
                    if not produced:
                        raise _RetryStream("返回空响应")
                    self.run_store.finish(
                        run_id,
                        status="completed",
                        output_hash=output_hasher.hexdigest(),
                        output_bytes=output_bytes,
                        finish_reason=finish_reason,
                    )
                    terminal = True
                    return
                except HermesError as exc:
                    last_err = exc
                    if terminal:
                        raise
                    # 4xx/明确 Agent error 不重试；部分输出也不重试。
                    if produced or finish_reason or tool_started or "网关返回" in str(exc):
                        self.run_store.finish(
                            run_id,
                            status="partial" if produced else "failed",
                            output_hash=output_hasher.hexdigest(),
                            output_bytes=output_bytes,
                            finish_reason=finish_reason,
                            error_code=(
                                "agent_incomplete" if finish_reason
                                else ("tool_side_effect_unknown" if tool_started else "request_rejected")
                            ),
                            partial_output=produced,
                        )
                        terminal = True
                        raise
                except _RetryStream as exc:
                    last_err = HermesError(f"Hermes 网关{exc}")
                    last_error_code = (
                        "empty_response" if "空响应" in str(exc)
                        else "gateway_unavailable"
                    )
                except (httpx.ConnectError, httpx.TimeoutException) as exc:
                    last_err = exc
                    last_error_code = (
                        "gateway_timeout"
                        if isinstance(exc, httpx.TimeoutException)
                        else "gateway_unavailable"
                    )
                    stop_retrying = (
                        isinstance(exc, httpx.TimeoutException)
                        and not self._retry_after_timeout(purpose)
                    )
                except Exception as exc:
                    last_err = exc
                    last_error_code = "gateway_unavailable"
                if produced or tool_started:
                    self.run_store.finish(
                        run_id,
                        status="partial" if produced else "unknown",
                        output_hash=output_hasher.hexdigest(),
                        output_bytes=output_bytes,
                        finish_reason=finish_reason,
                        error_code=(
                            "tool_side_effect_unknown" if tool_started else "stream_interrupted"
                        ),
                        partial_output=True,
                    )
                    terminal = True
                    raise HermesError("Hermes 流式中断（已输出部分内容，不重试）") from last_err
                if stop_retrying:
                    break
                if attempt < _MAX_RETRIES:
                    log.warning("Hermes 调用失败，重试 %d/%d：%s", attempt + 1, _MAX_RETRIES, last_err)
                    await asyncio.sleep(_RETRY_BACKOFF * (attempt + 1))

            self.run_store.finish(
                run_id,
                status="failed",
                error_code=last_error_code,
                output_hash=output_hasher.hexdigest(),
                output_bytes=output_bytes,
            )
            terminal = True
            if isinstance(last_err, httpx.ConnectError):
                raise HermesError("Hermes 网关连接失败，请检查网关状态。") from last_err
            if isinstance(last_err, httpx.TimeoutException):
                raise HermesError(f"Hermes 网关响应超时（已重试 {_MAX_RETRIES} 次）。") from last_err
            if last_error_code == "empty_response":
                raise HermesError(
                    f"Hermes 模型多次返回空响应（已重试 {_MAX_RETRIES} 次）。"
                ) from last_err
            raise HermesError(f"Hermes 调用失败（已重试 {_MAX_RETRIES} 次）。") from last_err
        finally:
            if not terminal:
                self.run_store.finish(
                    run_id,
                    status="cancelled",
                    output_hash=output_hasher.hexdigest(),
                    output_bytes=output_bytes,
                    finish_reason=finish_reason,
                    error_code="consumer_cancelled",
                    partial_output=output_bytes > 0,
                )

    async def chat(self, messages: list[dict], **kw) -> str:
        """非流式：拼接全部增量后返回。"""
        parts: list[str] = []
        async for delta in self.chat_stream(messages, **kw):
            parts.append(delta)
        result = "".join(parts)
        if not result.strip():
            # chat_stream normally rejects this already. Keep the non-streaming
            # boundary fail-closed for custom/subclass implementations too.
            raise HermesError("Hermes 模型返回空响应。")
        return result

    async def chat_json(
        self,
        messages: list[dict],
        *,
        required_keys: tuple[str, ...] = (),
        validator: Callable[[dict], bool] | None = None,
        structure_attempts: int = 2,
        **kw,
    ) -> dict:
        """Return a validated JSON object; invalid structure is never accepted.

        Transport retries remain inside ``chat_stream``. This layer adds one
        bounded regeneration for syntactically invalid or schema-incomplete
        model output, without logging or returning the raw response.
        """
        attempts = max(1, min(int(structure_attempts), 3))
        current_messages = list(messages)
        for attempt in range(attempts):
            raw = await self.chat(current_messages, **kw)
            parsed = _parse_json_object(raw)
            required_ok = bool(
                parsed is not None
                and all(
                    key in parsed and parsed.get(key) not in (None, "")
                    for key in required_keys
                )
            )
            validator_ok = bool(
                parsed is not None
                and (validator is None or validator(parsed))
            )
            if required_ok and validator_ok:
                return parsed
            if attempt < attempts - 1:
                current_messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            "上一版未通过结构校验。请重新生成，只返回一个合法 JSON 对象；"
                            "不得输出 Markdown 围栏、解释或前后缀，且不得补造缺失数据。"
                        ),
                    },
                ]
        raise HermesError("Hermes 模型结构化输出多次未通过校验。")
