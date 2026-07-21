"""AkShare/MiniRacer 数据源的子进程隔离适配。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time

from .akshare_worker import RESULT_PREFIX

_ERROR_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,80}")
_BREAKER_LOCK = threading.Lock()
_BREAKER = {"until": 0.0, "failures": 0}
_MARKET_BREAKER = {"until": 0.0, "failures": 0}
_BREAKER_COOLDOWN_SECONDS = 300.0


def _worker_env() -> dict[str, str]:
    """只向受限子进程传递运行必需变量，不继承 token/webhook/key。"""
    allowed = (
        "HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "TZ",
        # Windows 的 asyncio/_overlapped 初始化依赖这两个系统目录变量；
        # 缺失时隔离 worker 会在导入 py_mini_racer 时触发 WinError 10106。
        "SystemRoot", "WINDIR",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    )
    env = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _breaker_available(breaker: dict | None = None) -> tuple[bool, float]:
    target = breaker if breaker is not None else _BREAKER
    with _BREAKER_LOCK:
        remaining = max(0.0, target["until"] - time.monotonic())
        return remaining <= 0, remaining


def _breaker_report(ok: bool, breaker: dict | None = None) -> None:
    target = breaker if breaker is not None else _BREAKER
    with _BREAKER_LOCK:
        if ok:
            target.update({"until": 0.0, "failures": 0})
        else:
            target["failures"] += 1
            target["until"] = time.monotonic() + _BREAKER_COOLDOWN_SECONDS


def _reset_industry_breaker() -> None:
    """测试/运维重置入口，业务路径不自动绕过冷却。"""
    with _BREAKER_LOCK:
        _BREAKER.update({"until": 0.0, "failures": 0})


def _reset_market_breaker() -> None:
    """测试/运维重置入口；与行业资金源使用独立熔断器。"""
    with _BREAKER_LOCK:
        _MARKET_BREAKER.update({"until": 0.0, "failures": 0})


def _failure(reason: str, *, latency_ms: float) -> dict:
    return {
        "ok": False,
        "rows": [],
        "error": str(reason)[:240],
        "latency_ms": round(latency_ms, 1),
        "mode": "isolated-subprocess",
    }


def _error_type(payload: dict | None) -> str:
    """只透出异常类型，不把子进程 stderr/URL/查询串写入指标 API。"""
    raw = str((payload or {}).get("error") or "")
    match = _ERROR_TYPE_RE.match(raw)
    return match.group(0) if match else "worker-error"


def _run_worker(
    command_name: str,
    expected_schema: str,
    *,
    timeout: float,
    trip_breaker: bool,
    breaker: dict,
) -> dict:
    started = time.monotonic()
    available, remaining = _breaker_available(breaker)
    if trip_breaker and not available:
        return _failure(
            f"worker circuit open; retry_after={remaining:.1f}s",
            latency_ms=0.0,
        )
    command = [sys.executable, "-m", "hermes_trader.data.akshare_worker", command_name]
    try:
        completed = subprocess.run(  # noqa: S603 - 参数是固定模块，不经 shell
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_worker_env(),
            timeout=max(0.1, float(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        # 首屏的短预算探测不能把后台完整采集也熔断。生产实测该公开源
        # 常在 8~10 秒成功；4 秒交互预算只代表“本次页面不等”，不代表
        # 数据源已经失效。只有后台完整预算调用才有资格改变熔断状态。
        if trip_breaker:
            _breaker_report(False, breaker)
        return _failure(
            f"worker timeout>{float(timeout):g}s",
            latency_ms=(time.monotonic() - started) * 1000,
        )
    except OSError as exc:
        if trip_breaker:
            _breaker_report(False, breaker)
        return _failure(
            f"worker start failed: {type(exc).__name__}",
            latency_ms=(time.monotonic() - started) * 1000,
        )

    latency = (time.monotonic() - started) * 1000
    payload: dict | None = None
    for line in reversed((completed.stdout or "").splitlines()):
        if not line.startswith(RESULT_PREFIX):
            continue
        try:
            candidate = json.loads(line[len(RESULT_PREFIX):])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    if completed.returncode != 0:
        if trip_breaker:
            _breaker_report(False, breaker)
        return _failure(
            f"worker exit={completed.returncode}: {_error_type(payload)}",
            latency_ms=latency,
        )
    if (
        not payload
        or payload.get("ok") is not True
        or payload.get("schema") != expected_schema
        or not str(payload.get("provider_version") or "").startswith("1.18.")
        or not isinstance(payload.get("rows"), list)
    ):
        if trip_breaker:
            _breaker_report(False, breaker)
        return _failure("worker invalid response", latency_ms=latency)
    if trip_breaker:
        _breaker_report(True, breaker)
    return {
        "ok": True,
        "rows": payload["rows"],
        "error": None,
        "latency_ms": round(latency, 1),
        "mode": "isolated-subprocess",
        "schema": payload["schema"],
        "provider_version": payload["provider_version"],
    }


def industry_fund_flow(*, timeout: float = 4.0, trip_breaker: bool = True) -> dict:
    """在一次性子进程调用同花顺行业资金流。"""
    return _run_worker(
        "industry-flow", "ths-industry-flow.v1",
        timeout=timeout, trip_breaker=trip_breaker, breaker=_BREAKER,
    )


def market_fund_flow(*, timeout: float = 10.0, trip_breaker: bool = True) -> dict:
    """在一次性子进程读取同花顺个股净流入/净流出首屏。"""
    return _run_worker(
        "market-fund-flow", "ths-market-fund-flow.v1",
        timeout=timeout, trip_breaker=trip_breaker, breaker=_MARKET_BREAKER,
    )
