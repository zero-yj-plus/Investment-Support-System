from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _script(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_windows_deployment_scripts_are_present_and_use_project_venv():
    for name in ("install.ps1", "start.ps1", "start-tdx-bridge.ps1", "check.ps1"):
        text = _script(name)
        assert "$PSScriptRoot" in text

    installer = _script("install.ps1")
    assert "Join-Path $ProjectRoot '.venv'" in installer
    assert "Join-Path $VenvRoot 'Scripts\\python.exe'" in installer
    assert ".venv\\Scripts\\python.exe" in _script("start.ps1")
    assert ".venv\\Scripts\\python.exe" in _script("start-tdx-bridge.ps1")


def test_installer_falls_back_from_py_launcher_to_python_and_preserves_configs():
    text = _script("install.ps1")
    assert text.index("Get-Command 'py.exe'") < text.index("Get-Command 'python.exe'")
    assert "struct.calcsize" in text
    assert "sys.version_info >= (3,10)" in text
    assert "pip', 'install', '-r'" in text
    assert "if (-not (Test-Path -LiteralPath $ConfigPath" in text
    assert "if (-not (Test-Path -LiteralPath $EnvPath" in text


def test_runtime_scripts_load_dotenv_without_putting_bridge_key_on_command_line():
    start = _script("start.ps1")
    bridge = _script("start-tdx-bridge.ps1")
    assert "SetEnvironmentVariable($Name, $Value, 'Process')" in start
    assert "SetEnvironmentVariable($Name, $Value, 'Process')" in bridge
    assert "TQ_BRIDGE_API_KEY" in bridge
    assert "--api-key" not in bridge
    assert "Start-Process" not in start
    assert "Start-Process" not in bridge


def test_check_script_covers_ui_core_apis_and_optional_bridge():
    text = _script("check.ps1")
    for literal in ("/api/status", "/api/datasources", "/v1/meta/health"):
        assert literal in text
    assert "frontend-vue\\dist\\index.html" in text
    assert "TQ/TDX 桥未配置" in text
    assert "exit 1" in text
