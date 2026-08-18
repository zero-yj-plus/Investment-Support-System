from pathlib import Path
import sys
from types import SimpleNamespace

from scripts import windows_deploy


ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_windows_bat_entrypoints_replace_basic_powershell_scripts():
    names = ("install.bat", "start.bat", "start-tdx-bridge.bat", "check.bat")
    for name in names:
        text = _text(name)
        assert "%~dp0" in text
        assert "scripts\\windows_deploy.py" in text
        assert "powershell" not in text.lower()

    upgrade = _text("upgrade.bat")
    assert "%~dp0" in upgrade
    assert "scripts\\windows_upgrade.py" in upgrade
    assert "SHA-256 mismatch" in upgrade
    assert "powershell" not in upgrade.lower()

    for retired in ("install.ps1", "start.ps1", "start-tdx-bridge.ps1", "check.ps1"):
        assert not (ROOT / retired).exists()


def test_installer_uses_supported_python_and_preserves_configs():
    installer = _text("install.bat")
    helper = _text("scripts/windows_deploy.py")
    assert installer.index("py -3") < installer.index("python.exe")
    assert 'sys.version_info >= (3, 10)' in helper
    assert 'struct.calcsize("P") * 8 == 64' in helper
    assert '"-m", "venv"' in helper
    assert '"-m", "pip", "install", "-r"' in helper
    assert '"-m", "pip", "check"' in helper
    assert 'copy_template_if_missing("config.yaml"' in helper
    assert 'copy_template_if_missing(".env"' in helper


def test_python_probe_avoids_windows_powershell_native_quoting(monkeypatch):
    monkeypatch.setattr(windows_deploy, "VENV_PYTHON", Path(sys.executable))
    windows_deploy.probe_venv()


def test_runtime_launchers_load_dotenv_without_bridge_key_on_command_line():
    helper = _text("scripts/windows_deploy.py")
    start = _text("start.bat")
    bridge = _text("start-tdx-bridge.bat")
    assert "load_dotenv(PROJECT_ROOT / \".env\"" in helper
    assert "TQ_BRIDGE_API_KEY" in helper
    assert 'environment.get("TDXAPI_KEY")' in helper
    assert '"--api-key"' not in helper
    assert ".venv\\Scripts\\python.exe" in start
    assert ".venv\\Scripts\\python.exe" in bridge


def test_runtime_launchers_default_to_hidden_background_and_offer_foreground():
    start_args = windows_deploy.build_parser().parse_args(["start"])
    bridge_args = windows_deploy.build_parser().parse_args(["start-tdx-bridge"])
    assert start_args.foreground is False
    assert bridge_args.foreground is False
    assert windows_deploy.build_parser().parse_args(
        ["start", "--foreground"]
    ).foreground is True
    assert windows_deploy.build_parser().parse_args(
        ["start-tdx-bridge", "--foreground"]
    ).foreground is True


def test_background_launcher_detaches_and_writes_log(tmp_path, monkeypatch):
    observed = {}

    def fake_popen(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr(windows_deploy, "RUNTIME_LOG_DIR", tmp_path)
    monkeypatch.setattr(windows_deploy.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(windows_deploy, "_background_flags", lambda: 123)

    environment = {}
    result = windows_deploy._run_service(
        [sys.executable, "service.py"],
        environment,
        description="测试服务",
        log_stem="test-service",
        foreground=False,
    )

    assert result == 0
    assert environment["PYTHONUNBUFFERED"] == "1"
    assert observed["command"] == [sys.executable, "service.py"]
    assert observed["stdin"] is windows_deploy.subprocess.DEVNULL
    assert observed["stderr"] is windows_deploy.subprocess.STDOUT
    assert observed["creationflags"] == 123
    assert list(tmp_path.glob("test-service-*.log"))


def test_foreground_launcher_keeps_console(monkeypatch):
    observed = {}

    def fake_call(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return 17

    monkeypatch.setattr(windows_deploy.subprocess, "call", fake_call)
    result = windows_deploy._run_service(
        [sys.executable, "service.py"],
        {},
        description="测试服务",
        log_stem="unused",
        foreground=True,
    )

    assert result == 17
    assert observed["command"] == [sys.executable, "service.py"]
    assert "creationflags" not in observed


def test_check_covers_ui_core_apis_and_optional_bridge():
    helper = _text("scripts/windows_deploy.py")
    for literal in ("/api/status", "/api/datasources", "/v1/meta/health"):
        assert literal in helper
    assert "frontend-vue/dist/index.html" in helper
    assert "TQ/TDX 桥未配置" in helper


def test_windows_packager_includes_all_deployment_entrypoints():
    text = _text("package-windows.ps1")
    for name in (
        "install.bat",
        "start.bat",
        "start-tdx-bridge.bat",
        "tdx-login-guard.bat",
        "check.bat",
        "upgrade.bat",
        "docs\\TQ_TDX_BRIDGE.md",
        "docs\\WINDOWS_TDX_LOGIN_GUARD.md",
    ):
        assert text.count(f"'{name}'") >= 2

    assert "'scripts'" in text
    for name in (
        "scripts\\tdx-login-guard\\tdx-login-guard.ps1",
        "scripts\\tdx-login-guard\\tdx-login-guard.config.example.json",
        "scripts\\tdx-login-guard\\set-tdx-login-credential.ps1",
        "scripts\\tdx-login-guard\\install-tdx-login-guard.ps1",
        "scripts\\tdx-login-guard\\uninstall-tdx-login-guard.ps1",
        "scripts\\tdx-login-guard\\manage-tdx-login-guard.ps1",
    ):
        assert f"'{name}'" in text


def test_upgrade_helper_preserves_customer_state_and_fails_closed():
    helper = _text("scripts/windows_upgrade.py")
    for protected in (".env", ".venv", "config.yaml", "data", "upgrade-backups"):
        assert protected in helper
    assert "verify_external_hash" in helper
    assert "verify_release" in helper
    assert "create_state_backup" in helper
    assert "stop_owned_listener" in helper
    assert "rollback_release" in helper
    assert "跳过联网安装" in helper
    assert "--verify-only" in helper
