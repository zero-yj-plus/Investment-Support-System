"""Launch and attest the project-scoped Hermes gateway.

The product connects to the proxy port (8874), never directly to the Hermes
child (8875).  The proxy starts the child with an explicit environment allowlist
and exposes an HMAC-signed attestation binding the live child PID to the exact
restricted profile contract.  This closes the gap where a safe config file and
an unrelated process on the same port could otherwise be mistaken for each
other.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import re
from typing import AsyncIterator

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from hermes_trader.gateway_home_delivery import register_home_delivery
from starlette.background import BackgroundTask


ROOT = Path(__file__).resolve().parents[1]
_CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _client_paths() -> tuple[str, Path, Path, Path]:
    client_id = str(os.environ.get("HERMES_CLIENT_ID") or "").strip()
    if _CLIENT_ID_PATTERN.fullmatch(client_id) is None:
        raise RuntimeError("HERMES_CLIENT_ID is missing or invalid")
    client_dir = ROOT / "clients" / client_id
    if not client_dir.is_dir() or client_dir.is_symlink():
        raise RuntimeError("restricted Hermes client directory is missing or unsafe")
    expected_parent = (client_dir / "data").resolve()
    profile = (expected_parent / "hermes-profile").resolve()
    template = client_dir / "hermes-profile.example" / "config.yaml"
    return client_id, profile, expected_parent, template


CLIENT_ID, PROFILE, EXPECTED_PARENT, TEMPLATE = _client_paths()


def _env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _contract(profile: dict, env: dict[str, str]) -> dict:
    mcps = profile.get("mcp_servers") or {}
    return {
        "api_toolsets": ((profile.get("platform_toolsets") or {}).get("api_server")),
        "plugins_disabled": (profile.get("plugins") or {}).get("enabled") == [],
        "mcp_count": len(mcps),
        "mcp_names": sorted(mcps) if isinstance(mcps, dict) else [],
        "safe_mode": env.get("HERMES_SAFE_MODE") == "1",
        "host": env.get("API_SERVER_HOST") or "",
        "backend_port": int(env.get("API_SERVER_PORT") or 0),
        "proxy_port": int(env.get("HERMES_PROXY_PORT") or 0),
    }


def _stable_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _load_and_validate() -> tuple[dict[str, str], dict, str]:
    if PROFILE.parent != EXPECTED_PARENT or PROFILE.is_symlink():
        raise RuntimeError("restricted profile path escaped its project data directory")
    config_path = PROFILE / "config.yaml"
    env_path = PROFILE / ".env"
    if not config_path.is_file() or not env_path.is_file():
        raise RuntimeError("restricted profile is not initialized")
    if config_path.read_bytes() != TEMPLATE.read_bytes():
        raise RuntimeError("restricted profile config drifted from the reviewed template")
    profile = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    env = _env_file(env_path)
    allowed_env_keys = {
        "HERMES_SAFE_MODE", "API_SERVER_ENABLED", "API_SERVER_HOST",
        "API_SERVER_PORT", "HERMES_PROXY_PORT", "API_SERVER_KEY",
        "API_SERVER_MODEL_NAME",
    }
    if set(env) != allowed_env_keys:
        raise RuntimeError("restricted profile .env has unexpected or missing keys")
    contract = _contract(profile, env)
    if not (
        contract["api_toolsets"] == []
        and contract["plugins_disabled"]
        and contract["mcp_names"] in ([], ["hermes-trader"])
        and contract["mcp_count"] == len(contract["mcp_names"])
        # HERMES_SAFE_MODE disables MCP discovery. Keep it enabled for the
        # no-MCP profile and require it off only for the reviewed customer
        # product MCP.
        and contract["safe_mode"] is (not bool(contract["mcp_names"]))
        and contract["host"] == "127.0.0.1"
        and contract["backend_port"] == 8875
        and contract["proxy_port"] == 8874
        and env.get("API_SERVER_ENABLED") == "true"
        and re.fullmatch(r"[0-9a-fA-F]{64}", env.get("API_SERVER_KEY") or "") is not None
    ):
        raise RuntimeError("restricted profile contract is unsafe")
    return env, contract, _stable_hash(contract)


ENV, CONTRACT, PROFILE_HASH = _load_and_validate()
API_KEY = ENV["API_SERVER_KEY"]
BACKEND = f"http://127.0.0.1:{CONTRACT['backend_port']}"
STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")
CHILD: subprocess.Popen | None = None


def _child_environment() -> dict[str, str]:
    # Deliberately do not inherit arbitrary HERMES_*, KANBAN, MCP, plugin, proxy,
    # shell-hook, or project variables from the caller.
    env: dict[str, str] = {}
    for key in ("HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    env.update({
        "HERMES_HOME": str(PROFILE),
        "HERMES_SAFE_MODE": "1" if CONTRACT["safe_mode"] else "0",
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": "127.0.0.1",
        "API_SERVER_PORT": str(CONTRACT["backend_port"]),
        "API_SERVER_KEY": API_KEY,
        "API_SERVER_MODEL_NAME": ENV.get("API_SERVER_MODEL_NAME", "hermes-agent"),
        "PYTHONUNBUFFERED": "1",
    })
    # 部署可通过 systemd EnvironmentFile 只注入当前模型凭据；仍不继承
    # 任意 HERMES/MCP/代理/终端环境。没有该变量时继续使用 profile auth 池。
    if os.environ.get("DEEPSEEK_API_KEY"):
        env["DEEPSEEK_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]
    return env


async def _wait_for_child() -> None:
    headers = {"Authorization": f"Bearer {API_KEY}"}
    async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
        for _ in range(100):
            if CHILD is None or CHILD.poll() is not None:
                raise RuntimeError("restricted Hermes child exited during startup")
            try:
                response = await client.get(f"{BACKEND}/v1/capabilities", headers=headers)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise RuntimeError("restricted Hermes child did not become ready")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global CHILD
    hermes = shutil.which("hermes")
    if not hermes:
        raise RuntimeError("hermes executable not found")
    CHILD = subprocess.Popen(
        [hermes, "gateway", "run", "--force"],
        cwd=str(PROFILE),
        env=_child_environment(),
        stdin=subprocess.DEVNULL,
        stdout=sys.stderr,
        stderr=sys.stderr,
        start_new_session=True,
    )
    try:
        # Startup readiness belongs inside the cleanup boundary too.  A child
        # can be alive yet never become ready; that failure must not leave the
        # restricted backend listening after the supervisor exits.
        await _wait_for_child()
        yield
    finally:
        child = CHILD
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=12)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except OSError:
                    pass
        CHILD = None


app = FastAPI(title="Restricted Hermes Attestation Proxy", lifespan=lifespan)


def _authorized(request: Request) -> bool:
    supplied = request.headers.get("authorization", "")
    return hmac.compare_digest(supplied, f"Bearer {API_KEY}")


def _signed_attestation() -> dict:
    child = CHILD
    payload = {
        "schema": "restricted-hermes-attestation.v1",
        "profile_hash": PROFILE_HASH,
        "home_hash": hashlib.sha256(str(PROFILE).encode()).hexdigest(),
        "supervisor_pid": os.getpid(),
        "child_pid": child.pid if child else None,
        "child_alive": bool(child and child.poll() is None),
        "safe_mode": CONTRACT["safe_mode"],
        "backend_port": CONTRACT["backend_port"],
        "proxy_port": CONTRACT["proxy_port"],
        "started_at": STARTED_AT,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["signature"] = hmac.new(API_KEY.encode(), canonical, hashlib.sha256).hexdigest()
    return payload


@app.get("/v1/attestation")
async def attestation(request: Request):
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return _signed_attestation()


register_home_delivery(app, root=ROOT, authorized=_authorized)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request):
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    child = CHILD
    if child is None or child.poll() is not None:
        return JSONResponse({"error": "restricted_gateway_unavailable"}, status_code=503)
    body = await request.body()
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": request.headers.get("content-type", "application/json"),
        "Accept": request.headers.get("accept", "*/*"),
    }
    client = httpx.AsyncClient(timeout=None, trust_env=False)
    query = f"?{request.url.query}" if request.url.query else ""
    try:
        upstream = await client.send(
            client.build_request(request.method, f"{BACKEND}/{path}{query}", headers=headers, content=body),
            stream=True,
        )
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse({"error": "restricted_gateway_upstream_error"}, status_code=502)
    response_headers = {
        key: value for key, value in upstream.headers.items()
        if key.lower() in {"content-type", "cache-control", "x-accel-buffering", "x-hermes-session-id", "x-hermes-session-key"}
    }

    async def close_upstream() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(close_upstream),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=CONTRACT["proxy_port"], log_level="info")
