"""后台数据健康快照。

设置页的 GET 请求只能读取本模块保存的进程内快照，不能同步探测外部服务。
外部探测由应用生命周期中的后台任务定期调用 ``refresh``；单项失败或超时只会
把对应基础设施标为降级，最近一份可展示快照始终保留。
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
import os
import threading
import time
from typing import Any

from . import free_source, market, news_observation, sources
from . import tdx_api_source as tdx_api
from .base import now_cn, relay_proxy

log = logging.getLogger("hermes.data.health")

_HERMES_PROBE_TIMEOUT_SECONDS = 5.0
_TDX_PROBE_TIMEOUT_SECONDS = 7.0
_LOCAL_ENRICHMENT_TIMEOUT_SECONDS = 3.0
_RETIRED_EVIDENCE_SOURCES = {"tdx_quote", "opentdx"}


def _checked_at() -> str:
    return now_cn().isoformat(timespec="seconds")


class DataHealthMonitor:
    """维护只读健康快照；``snapshot`` 本身不执行网络或磁盘探测。"""

    def __init__(self, config, hermes, store=None, market_gateway=None):
        self.config = config
        self.hermes = hermes
        self.store = store
        self.market_gateway = market_gateway
        self.evidence_store = getattr(market_gateway, "store", None)
        self._snapshot_lock = threading.Lock()
        self._refresh_lock = asyncio.Lock()
        self._snapshot = self._build_report(
            self._initial_hermes_health(),
            self._initial_tdx_health(),
            enrichment={},
            refresh={
                "status": "pending",
                "request_mode": "passive_snapshot",
                "last_started_at": None,
                "last_finished_at": None,
                "duration_ms": None,
                "error_codes": [],
            },
        )

    def snapshot(self) -> dict[str, Any]:
        """返回隔离副本；调用方不能改写后台持有的快照。"""
        with self._snapshot_lock:
            return deepcopy(self._snapshot)

    def _initial_hermes_health(self) -> dict[str, Any]:
        configured = bool(getattr(self.config, "model_connection_custom", False))
        return {
            "name": "Hermes AI 网关",
            "base_url": getattr(self.config, "hermes_base_url", ""),
            "model": getattr(self.config, "hermes_model", ""),
            "kind": self._hermes_kind(),
            "configured": configured,
            "connected": None if configured else False,
            "status": "unchecked" if configured else "not_configured",
            "latency_ms": None,
            "checked_at": None,
        }

    def _initial_tdx_health(self) -> dict[str, Any]:
        configured = bool(tdx_api.configured())
        return {
            "configured": configured,
            "connected": None if configured else False,
            "status": "unchecked" if configured else "not_configured",
            "checked_at": None,
        }

    def _hermes_kind(self) -> str:
        base_url = str(getattr(self.config, "hermes_base_url", ""))
        return "本机Hermes网关" if "127.0.0.1" in base_url or "localhost" in base_url else "云端relay"

    def _relay_health(self) -> dict[str, Any]:
        rmeta = market.last_relay_meta()
        relay_api_configured = bool(os.environ.get("DATA_RELAY_API", ""))
        relay_proxy_configured = bool(relay_proxy())
        return {
            "enabled": relay_api_configured or relay_proxy_configured,
            "transport": (
                "api" if relay_api_configured else ("proxy" if relay_proxy_configured else "direct")
            ),
            "api_configured": relay_api_configured,
            "proxy_configured": relay_proxy_configured,
            "spot": {
                "count": rmeta.get("count"),
                "total": rmeta.get("total"),
                "failed_pages": len(rmeta.get("failed_pages", []) or []),
                "stale": rmeta.get("stale"),
                "age_sec": round(rmeta.get("age")) if rmeta.get("age") else None,
                "incomplete": rmeta.get("incomplete"),
            } if rmeta else None,
        }

    def _feishu_health(self) -> dict[str, bool]:
        return {
            "configured": bool(getattr(self.config, "feishu_webhook", ""))
            or bool(
                getattr(self.config, "feishu_app_id", "")
                and getattr(self.config, "feishu_app_secret", "")
            ),
            "bot": bool(getattr(self.config, "feishu_app_id", "")),
        }

    def _build_report(
        self,
        hermes_health: dict[str, Any],
        tdx_health: dict[str, Any],
        *,
        enrichment: dict[str, Any],
        refresh: dict[str, Any],
    ) -> dict[str, Any]:
        canonical_universe = free_source.stock_universe_status()
        report = sources.build_report(
            {
                "hermes": hermes_health,
                "relay": self._relay_health(),
                "feishu": self._feishu_health(),
                "tdx_api": tdx_health,
            },
            canonical_universe,
        )
        report["universe"] = canonical_universe
        report["health_refresh"] = refresh

        if enrichment.get("field_contract") is not None:
            report["field_contract"] = enrichment["field_contract"]
        if enrichment.get("news_observation") is not None:
            report["news_observation"] = enrichment["news_observation"]
        if enrichment.get("evidence_registry") is not None:
            registry = enrichment["evidence_registry"]
            report["evidence_registry"] = [
                item for item in registry
                if str(item.get("source_id") or "") not in _RETIRED_EVIDENCE_SOURCES
            ]
            report["retired_evidence_sources"] = sorted(
                str(item.get("source_id") or "")
                for item in registry
                if str(item.get("source_id") or "") in _RETIRED_EVIDENCE_SOURCES
            )
        if enrichment.get("evidence_stats") is not None:
            report["evidence_stats"] = enrichment["evidence_stats"]
        if enrichment.get("evidence_health") is not None:
            report["evidence_health"] = enrichment["evidence_health"]
        return report

    async def _probe_hermes(self) -> dict[str, Any]:
        health = self._initial_hermes_health()
        if not health["configured"]:
            return health
        started = time.monotonic()
        try:
            connected = await asyncio.wait_for(
                self.hermes.check_connection(),
                timeout=_HERMES_PROBE_TIMEOUT_SECONDS,
            )
            health.update({
                "connected": bool(connected),
                "status": "ok" if connected else "down",
                "latency_ms": round((time.monotonic() - started) * 1000),
                "checked_at": _checked_at(),
            })
        except asyncio.TimeoutError:
            health.update({
                "connected": False,
                "status": "down",
                "latency_ms": round((time.monotonic() - started) * 1000),
                "checked_at": _checked_at(),
                "error_code": "hermes_probe_timeout",
            })
        except Exception:  # noqa: BLE001 -- 不把可能含 URL/凭据的异常返回前端
            health.update({
                "connected": False,
                "status": "down",
                "latency_ms": round((time.monotonic() - started) * 1000),
                "checked_at": _checked_at(),
                "error_code": "hermes_probe_failed",
            })
        return health

    async def _probe_tdx(self) -> dict[str, Any]:
        health = self._initial_tdx_health()
        if not health["configured"]:
            return health
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(tdx_api.service_health),
                timeout=_TDX_PROBE_TIMEOUT_SECONDS,
            )
            if isinstance(result, dict):
                health.update(result)
            connected = health.get("connected") is True
            health.update({
                "status": "ok" if connected else "down",
                "checked_at": _checked_at(),
            })
        except asyncio.TimeoutError:
            health.update({
                "connected": False,
                "status": "down",
                "checked_at": _checked_at(),
                "error_code": "tdx_probe_timeout",
            })
        except Exception:  # noqa: BLE001 -- 只公开稳定错误码
            health.update({
                "connected": False,
                "status": "down",
                "checked_at": _checked_at(),
                "error_code": "tdx_probe_failed",
            })
        return health

    def _read_local_enrichment(self) -> dict[str, Any]:
        enrichment: dict[str, Any] = {}
        if self.market_gateway is not None:
            enrichment["field_contract"] = self.market_gateway.contract()
            enrichment["evidence_health"] = self.market_gateway.health()
        if self.evidence_store is not None:
            enrichment["evidence_registry"] = self.evidence_store.list_sources()
            enrichment["evidence_stats"] = self.evidence_store.stats()

        summary = news_observation.observation_summary()
        job_states = self.store.get_job_states() if self.store is not None else {}
        news_job = (job_states or {}).get("news_event_observation", {})
        enrichment["news_observation"] = {
            **summary,
            "last_run": news_job.get("last_run"),
            "last_finished": news_job.get("last_finished"),
            "last_status": news_job.get("last_status"),
            "last_error": news_job.get("last_error"),
            "last_result": news_job.get("last_result") or {},
        }
        return enrichment

    async def refresh(self) -> dict[str, Any]:
        """后台并行刷新；最慢外部源不会进入设置页请求路径。"""
        async with self._refresh_lock:
            started_mono = time.monotonic()
            started_at = _checked_at()
            hermes_task = asyncio.create_task(self._probe_hermes())
            tdx_task = asyncio.create_task(self._probe_tdx())
            try:
                enrichment = await asyncio.wait_for(
                    asyncio.to_thread(self._read_local_enrichment),
                    timeout=_LOCAL_ENRICHMENT_TIMEOUT_SECONDS,
                )
                local_error = None
            except asyncio.TimeoutError:
                enrichment = {}
                local_error = "local_health_snapshot_timeout"
            except Exception:  # noqa: BLE001 -- 本地统计失败保留基础快照
                enrichment = {}
                local_error = "local_health_snapshot_failed"

            hermes_health, tdx_health = await asyncio.gather(hermes_task, tdx_task)
            error_codes = [
                str(code) for code in (
                    hermes_health.get("error_code"),
                    tdx_health.get("error_code"),
                    local_error,
                ) if code
            ]
            refresh = {
                "status": "degraded" if error_codes else "ready",
                "request_mode": "passive_snapshot",
                "last_started_at": started_at,
                "last_finished_at": _checked_at(),
                "duration_ms": round((time.monotonic() - started_mono) * 1000),
                "error_codes": error_codes,
            }
            next_snapshot = self._build_report(
                hermes_health,
                tdx_health,
                enrichment=enrichment,
                refresh=refresh,
            )
            with self._snapshot_lock:
                self._snapshot = next_snapshot
            if error_codes:
                log.warning("后台数据健康快照降级: %s", ",".join(error_codes))
            return self.snapshot()
