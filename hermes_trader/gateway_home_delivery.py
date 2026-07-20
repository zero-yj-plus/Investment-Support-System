"""Authenticated product-to-Hermes home-channel delivery bridge.

Containerized Wudi products cannot execute the host's Hermes CLI directly.
This bridge is registered on the already authenticated restricted proxy and
only accepts a platform name plus content.  Recipient/chat IDs are never
accepted: Hermes resolves the destination exclusively from `/sethome`.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


ALLOWED_PLATFORMS = frozenset({"feishu", "weixin"})
BODY_LIMIT = 2_000_000
_MEDIA_LINE_RE = re.compile(r"(?m)^MEDIA:(.+)$")


def home_platforms_from_env(hermes_home: Path) -> set[str]:
    """Return supported platforms with a persisted HOME_CHANNEL, never IDs."""
    found: set[str] = set()
    try:
        lines = (hermes_home / ".env").read_text(
            encoding="utf-8", errors="replace",
        ).splitlines()
    except OSError:
        return found
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if not key.endswith("_HOME_CHANNEL") or not value.strip().strip("'\""):
            continue
        platform = key[: -len("_HOME_CHANNEL")].lower()
        if platform in ALLOWED_PLATFORMS:
            found.add(platform)
    return found


def translate_delivery_media(body: str, shared_data: Path) -> str:
    """Map container customer-data paths to the host and reject other files."""
    shared = shared_data.resolve()

    def replace(match: re.Match) -> str:
        raw_path = match.group(1).strip()
        marker = "/data/"
        if raw_path.startswith("/app/clients/") and marker in raw_path:
            candidate = (shared / raw_path.split(marker, 1)[1]).resolve()
        else:
            candidate = Path(raw_path).expanduser().resolve()
        if not candidate.is_relative_to(shared) or not candidate.is_file():
            raise ValueError(
                "delivery media is outside the customer shared data directory"
            )
        return f"MEDIA:{candidate}"

    return _MEDIA_LINE_RE.sub(replace, body)


def _process_environment(hermes_home: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in (
        "HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL",
        "SSL_CERT_FILE", "SSL_CERT_DIR",
    ):
        if os.environ.get(key):
            env[key] = os.environ[key]
    env["HERMES_HOME"] = str(hermes_home)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def register_home_delivery(
    app: FastAPI,
    *,
    root: Path,
    authorized: Callable[[Request], bool],
) -> None:
    """Register the authenticated bridge before the proxy catch-all route."""
    hermes_home = (Path.home() / ".hermes").resolve()
    shared_data = (root.resolve().parent / "shared" / "data").resolve()
    lock = asyncio.Lock()

    async def send(platform: str, body: str) -> tuple[bool, str]:
        hermes = shutil.which("hermes")
        if not hermes:
            return False, "hermes executable not found"
        try:
            body = translate_delivery_media(body, shared_data)
        except ValueError as exc:
            return False, str(exc)
        async with lock:
            proc = await asyncio.create_subprocess_exec(
                hermes, "send", "--to", platform, "--json",
                cwd=str(hermes_home / "hermes-agent"),
                env=_process_environment(hermes_home),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(body.encode("utf-8")), timeout=45,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return False, "ambiguous timeout; manual review required"
        stdout = out.decode("utf-8", "replace").strip()
        stderr = err.decode("utf-8", "replace").strip()
        detail = (stderr or stdout)[:500]
        if proc.returncode == 0:
            try:
                payload = json.loads(stdout or "{}")
            except ValueError:
                payload = {}
            if not isinstance(payload, dict) or not payload.get("error"):
                return True, ""
            detail = str(payload.get("error"))[:500]
        return False, detail or "Hermes delivery rejected"

    @app.post("/v1/delivery/home")
    async def delivery_home(request: Request):
        if not authorized(request):
            return JSONResponse(
                {"ok": False, "error": "unauthorized"}, status_code=401,
            )
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse(
                {"ok": False, "error": "invalid_json"}, status_code=400,
            )
        payload = payload if isinstance(payload, dict) else {}
        platform = str(payload.get("platform") or "").strip().lower()
        if platform not in ALLOWED_PLATFORMS:
            return JSONResponse(
                {"ok": False, "error": "platform_not_allowed"}, status_code=400,
            )
        if platform not in home_platforms_from_env(hermes_home):
            return JSONResponse(
                {"ok": False, "error": "home_channel_unconfigured"},
                status_code=409,
            )
        if bool(payload.get("probe")):
            return {
                "ok": True,
                "ready": True,
                "platform": platform,
                "home_channel_only": True,
            }
        body = str(payload.get("body") or "")
        if not body or len(body.encode("utf-8")) > BODY_LIMIT:
            return JSONResponse(
                {"ok": False, "error": "delivery_body_invalid"}, status_code=400,
            )
        ok, detail = await send(platform, body)
        result = {
            "ok": ok,
            "status": "accepted" if ok else (
                "unknown" if "ambiguous timeout" in detail.lower()
                else "failed_terminal"
            ),
            "platform": platform,
            "home_channel_only": True,
            "error": None if ok else detail,
        }
        return JSONResponse(result, status_code=200 if ok else 502)
