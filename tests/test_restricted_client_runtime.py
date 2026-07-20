from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _client_template(root: Path, client_id: str) -> Path:
    template = root / "clients" / client_id / "hermes-profile.example"
    template.mkdir(parents=True)
    (template / "config.yaml").write_text("mcp_servers: {}\n", encoding="utf-8")
    (template / ".env.example").write_text("API_SERVER_KEY=placeholder\n", encoding="utf-8")
    return template


def _resolve(root: Path, client_id: str = "", *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    script = (
        f'source "{ROOT / "scripts/client_runtime.sh"}"; '
        f'hermes_resolve_client_context "$1" "$2" || exit $?; '
        'printf "%s|%s|%s" "$CLIENT_ID" "$CLIENT_DIR" "$HERMES_CLIENT_ID"'
    )
    return subprocess.run(
        ["bash", "-c", script, "resolver", str(root), client_id],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_client_runtime_resolves_explicit_client_without_customer_hardcoding(tmp_path: Path) -> None:
    _client_template(tmp_path, "alpha-1")

    completed = _resolve(tmp_path, "alpha-1")

    assert completed.returncode == 0
    assert completed.stdout == f"alpha-1|{tmp_path}/clients/alpha-1|alpha-1"
    generic_sources = (
        ROOT / "scripts/client_runtime.sh",
        ROOT / "scripts/init_restricted_hermes_profile.sh",
        ROOT / "scripts/run_restricted_hermes.sh",
        ROOT / "scripts/restricted_hermes_proxy.py",
    )
    assert all("customer-specific-id" not in path.read_text(encoding="utf-8") for path in generic_sources)


def test_client_runtime_infers_only_client_in_release_bundle(tmp_path: Path) -> None:
    _client_template(tmp_path, "only-client")

    completed = _resolve(tmp_path)

    assert completed.returncode == 0
    assert completed.stdout.startswith("only-client|")


def test_client_runtime_requires_explicit_id_in_multi_client_checkout(tmp_path: Path) -> None:
    _client_template(tmp_path, "alpha")
    _client_template(tmp_path, "beta")

    completed = _resolve(tmp_path)

    assert completed.returncode != 0
    assert "Client ID is required" in completed.stderr


def test_client_runtime_rejects_path_escape_and_symlink(tmp_path: Path) -> None:
    _client_template(tmp_path, "safe")
    unsafe = tmp_path / "clients" / "unsafe"
    unsafe.symlink_to(tmp_path / "clients" / "safe", target_is_directory=True)

    escaped = _resolve(tmp_path, "../safe")
    linked = _resolve(tmp_path, "unsafe")

    assert escaped.returncode != 0
    assert "Invalid client ID" in escaped.stderr
    assert linked.returncode != 0
    assert "missing or unsafe" in linked.stderr


def test_client_runtime_accepts_client_id_from_environment(tmp_path: Path) -> None:
    _client_template(tmp_path, "from-env")
    env = dict(os.environ)
    env["HERMES_CLIENT_ID"] = "from-env"

    completed = _resolve(tmp_path, env=env)

    assert completed.returncode == 0
    assert completed.stdout.startswith("from-env|")
