import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD_ROOT = ROOT / "scripts" / "tdx-login-guard"
GUARD_FILES = (
    "tdx-login-guard.ps1",
    "tdx-login-guard.config.example.json",
    "set-tdx-login-credential.ps1",
    "install-tdx-login-guard.ps1",
    "uninstall-tdx-login-guard.ps1",
)


def _text(name: str) -> str:
    return (GUARD_ROOT / name).read_text(encoding="utf-8")


def test_tdx_login_guard_files_live_in_a_dedicated_directory():
    for name in GUARD_FILES:
        assert (GUARD_ROOT / name).is_file()
        assert not (ROOT / name).exists()


def test_tdx_login_guard_files_and_config_are_portable():
    config = json.loads(_text("tdx-login-guard.config.example.json"))
    assert config["tdx_exe"].lower().endswith("tdxw.exe")
    assert config["tq_url"] == "http://127.0.0.1:17709/"
    assert config["bridge_url"] == "http://127.0.0.1:18879"
    assert config["recovery_mode"] == "close_dialog"
    assert config["start_tdx_if_missing"] is False
    assert config["username_control_id"] == 1443
    assert config["password_control_id"] == 1058
    assert config["max_login_attempts_per_recovery"] == 1
    assert config["enable_keyboard_fallback"] is False
    assert config["close_login_dialog_after_action"] is False
    assert "#32770" in config["login_window_classes"]
    assert "登录" in config["login_button_names"]
    assert "关闭" in config["dismiss_button_names"]
    assert "#32770" in config["dismiss_window_classes"]


def test_guard_uses_uia_state_and_real_tq_bridge_verification():
    guard = _text("tdx-login-guard.ps1")
    for literal in (
        "UIAutomationClient",
        "EnumChildWindows",
        "0x00F5",
        "0x0111",
        "0x0010",
        "PostMessage",
        "InvokePattern",
        "get_market_snapshot",
        "ErrorId",
        "/v1/meta/health",
        "/v1/realtime/quotes",
        "Dismiss-PostLoginPopup",
        "Close-LoginDialog",
        "close_dialog",
        "RecoveryArmed",
        "安全配置禁止自动启动",
        "CanStartAndLogin",
        "按自动登录配置启动客户端",
        "SendMessageText",
        "Import-Clixml",
        "DPAPI",
        "max_login_attempts_per_recovery",
        "AllowClassClose",
        "unhealthy_threshold",
        "action_cooldown_seconds",
        "-Inspect",
    ):
        assert literal in guard
    assert "mouse_event" not in guard
    assert "SetCursorPos" not in guard
    assert "ConvertTo-SecureString" not in guard
    assert "Get-Credential" not in guard
    assert "[REDACTED]" in guard


def test_guard_installer_uses_interactive_current_user_task_and_safe_uninstall():
    installer = _text("install-tdx-login-guard.ps1")
    uninstaller = _text("uninstall-tdx-login-guard.ps1")
    assert "$PSScriptRoot" in installer
    assert "Join-Path $PSScriptRoot '..\\..'" in installer
    assert "Join-Path $ProjectRoot '.env'" in installer
    assert "New-ScheduledTaskPrincipal" in installer
    assert "-LogonType Interactive" in installer
    assert "-RunLevel Limited" in installer
    assert "LOCALAPPDATA" in installer
    assert "set-tdx-login-credential.ps1" in installer
    assert "project_env_file" in installer
    assert "SupportsShouldProcess" in uninstaller
    assert "-RemoveFiles" in uninstaller
    assert "LOCALAPPDATA" in uninstaller
