from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_ROOT = PROJECT_ROOT / ".venv"
VENV_PYTHON = VENV_ROOT / "Scripts" / "python.exe"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
REQUIRED_IMPORTS = ("fastapi", "uvicorn", "yaml", "apscheduler", "pandas", "httpx")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DeploymentError(RuntimeError):
    pass


def python_is_supported() -> bool:
    return sys.version_info >= (3, 10) and struct.calcsize("P") * 8 == 64


def require_supported_python() -> None:
    bits = struct.calcsize("P") * 8
    version = ".".join(str(part) for part in sys.version_info[:3])
    if not python_is_supported():
        raise DeploymentError(
            f"需要 64 位 Python 3.10+，当前为 Python {version} ({bits}-bit)。"
        )
    print(f"Python {version} ({bits}-bit)")


def run_checked(command: list[str], description: str) -> None:
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode:
        raise DeploymentError(f"{description}失败，退出码：{completed.returncode}")


def probe_venv() -> None:
    code = (
        "import struct,sys;"
        "ok=sys.version_info>=(3,10) and struct.calcsize('P')*8==64;"
        "raise SystemExit(0 if ok else 1)"
    )
    completed = subprocess.run(
        [str(VENV_PYTHON), "-c", code],
        cwd=PROJECT_ROOT,
        check=False,
    )
    if completed.returncode:
        raise DeploymentError(
            "现有 .venv 不是可用的 64 位 Python 3.10+ 环境；"
            "请将该目录改名备份后重新运行 install.bat。"
        )


def missing_runtime_dependencies() -> list[str]:
    return [name for name in REQUIRED_IMPORTS if importlib.util.find_spec(name) is None]


def probe_venv_dependencies() -> None:
    code = (
        "import importlib.util;"
        f"required={REQUIRED_IMPORTS!r};"
        "missing=[n for n in required if importlib.util.find_spec(n) is None];"
        "print('missing=' + ','.join(missing) if missing else 'dependencies=ok');"
        "raise SystemExit(1 if missing else 0)"
    )
    completed = subprocess.run(
        [str(VENV_PYTHON), "-c", code],
        cwd=PROJECT_ROOT,
        check=False,
    )
    if completed.returncode:
        raise DeploymentError(
            "关键运行依赖未安装完整，请检查上方 pip 输出后重新运行 install.bat。"
        )


def copy_template_if_missing(target_name: str, template_name: str) -> None:
    target = PROJECT_ROOT / target_name
    template = PROJECT_ROOT / template_name
    if target.exists():
        print(f"保留现有 {target_name}，未覆盖。")
        return
    if not template.is_file():
        raise DeploymentError(f"部署包不完整：缺少 {template_name}")
    shutil.copy2(template, target)
    print(f"已从 {template_name} 创建 {target_name}。")


def install(skip_pip_upgrade: bool) -> int:
    require_supported_python()
    if not REQUIREMENTS.is_file():
        raise DeploymentError(f"部署包不完整：缺少 {REQUIREMENTS.name}")
    if not (PROJECT_ROOT / "hermes_trader").is_dir():
        raise DeploymentError("部署包不完整：缺少 hermes_trader 目录")

    print(f"部署目录：{PROJECT_ROOT}")
    if VENV_PYTHON.is_file():
        print("检测到现有 .venv，将复用该环境。")
        probe_venv()
    else:
        print("正在创建项目虚拟环境 .venv ...")
        run_checked(
            [sys.executable, "-m", "venv", str(VENV_ROOT)],
            "创建虚拟环境",
        )

    if not skip_pip_upgrade:
        print("正在升级 pip ...")
        run_checked(
            [str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"],
            "升级 pip",
        )

    print("正在安装生产依赖 ...")
    run_checked(
        [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
        "安装生产依赖",
    )
    run_checked([str(VENV_PYTHON), "-m", "pip", "check"], "检查 Python 依赖")
    probe_venv_dependencies()

    copy_template_if_missing("config.yaml", "config.example.yaml")
    copy_template_if_missing(".env", ".env.example")
    print("\n安装完成。请填写 config.yaml 和 .env，然后运行 start.bat。")
    print("如使用本机通达信 TQ 服务，再单独运行 start-tdx-bridge.bat。")
    return 0


def load_dotenv(path: Path, environment: dict[str, str]) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise DeploymentError(f"无效的 .env 行：{raw_line}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not ENV_NAME.fullmatch(name):
            raise DeploymentError(f"无效的 .env 变量名：{name}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        environment[name] = value


def require_runtime() -> None:
    missing = missing_runtime_dependencies()
    if missing:
        raise DeploymentError(
            "项目虚拟环境缺少关键依赖："
            + ", ".join(missing)
            + "。请先运行 install.bat。"
        )


def start(config: str, host: str | None, port: int | None) -> int:
    require_runtime()
    config_path = Path(config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    if not config_path.is_file():
        raise DeploymentError(f"找不到配置文件：{config_path}。请先运行 install.bat。")
    if not (PROJECT_ROOT / "frontend-vue" / "dist" / "index.html").is_file():
        raise DeploymentError("部署包缺少预构建前端 frontend-vue\\dist\\index.html。")

    environment = os.environ.copy()
    load_dotenv(PROJECT_ROOT / ".env", environment)
    command = [sys.executable, "-m", "hermes_trader", "--config", str(config_path)]
    if host:
        command.extend(["--host", host])
    if port is not None:
        if not 1 <= port <= 65535:
            raise DeploymentError("端口必须在 1 到 65535 之间。")
        command.extend(["--port", str(port)])

    print("正在前台启动 Hermes Trader；按 Ctrl+C 停止。")
    return subprocess.call(command, cwd=PROJECT_ROOT, env=environment)


def start_tdx_bridge() -> int:
    bridge_script = PROJECT_ROOT / "scripts" / "tq_tdx_bridge.py"
    if not bridge_script.is_file():
        raise DeploymentError("部署包不完整：缺少 scripts\\tq_tdx_bridge.py。")

    environment = os.environ.copy()
    load_dotenv(PROJECT_ROOT / ".env", environment)
    bridge_key = environment.get("TQ_BRIDGE_API_KEY") or environment.get("TDXAPI_KEY")
    if not bridge_key:
        raise DeploymentError(
            "请先在 .env 中设置 TQ_BRIDGE_API_KEY 或 TDXAPI_KEY；"
            "桥接服务拒绝无密钥启动。"
        )
    if not environment.get("TDXAPI_BASE"):
        print(
            "[WARN] TDXAPI_BASE 尚未配置；建议设置为 "
            "http://127.0.0.1:18879。"
        )

    print("正在前台启动只读 TQ/TDX 兼容桥；按 Ctrl+C 停止。")
    return subprocess.call(
        [sys.executable, str(bridge_script)],
        cwd=PROJECT_ROOT,
        env=environment,
    )


def request_json(url: str, timeout: int, headers: dict[str, str] | None = None):
    request = Request(url, headers=headers or {}, method="GET")
    with urlopen(request, timeout=timeout) as response:
        status = int(response.status)
        payload = response.read(2 * 1024 * 1024)
    if status != 200:
        raise DeploymentError(f"HTTP {status}")
    return json.loads(payload.decode("utf-8"))


def request_home(url: str, timeout: int) -> None:
    request = Request(url, method="GET")
    with urlopen(request, timeout=timeout) as response:
        if int(response.status) != 200:
            raise DeploymentError(f"HTTP {response.status}")
        response.read(1024)


def is_loopback_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def check(base_url: str, skip_bridge: bool, timeout: int) -> int:
    if not 1 <= timeout <= 60:
        raise DeploymentError("timeout 必须在 1 到 60 秒之间。")
    failures: list[str] = []

    def passed(message: str) -> None:
        print(f"[PASS] {message}")

    def skipped(message: str) -> None:
        print(f"[SKIP] {message}")

    def failed(message: str) -> None:
        failures.append(message)
        print(f"[FAIL] {message}")

    for relative in (
        ".venv/Scripts/python.exe",
        "config.yaml",
        "requirements.txt",
        "hermes_trader/__init__.py",
        "frontend-vue/dist/index.html",
    ):
        display_path = relative.replace("/", "\\")
        if (PROJECT_ROOT / relative).exists():
            passed(display_path)
        else:
            failed(f"缺少 {display_path}")

    base_url = base_url.rstrip("/")
    try:
        request_home(base_url + "/", timeout)
        passed(f"首页 {base_url}/")
    except (DeploymentError, HTTPError, URLError, TimeoutError, OSError) as exc:
        failed(f"首页不可达：{exc}")

    for endpoint in ("/api/status", "/api/datasources"):
        try:
            request_json(base_url + endpoint, timeout)
            passed(endpoint)
        except (DeploymentError, HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            failed(f"{endpoint} 不可达：{exc}")

    if skip_bridge:
        skipped("按参数跳过 TQ/TDX 桥检查")
    else:
        environment = os.environ.copy()
        load_dotenv(PROJECT_ROOT / ".env", environment)
        tdx_base = environment.get("TDXAPI_BASE", "")
        bridge_key = environment.get("TQ_BRIDGE_API_KEY") or environment.get("TDXAPI_KEY")
        if not tdx_base or not bridge_key:
            skipped("TQ/TDX 桥未配置")
        elif not is_loopback_url(tdx_base):
            skipped("TDXAPI_BASE 不是本机桥地址，不发送本地桥健康检查")
        else:
            try:
                health = request_json(
                    tdx_base.rstrip("/") + "/v1/meta/health",
                    timeout,
                    {"X-API-Key": bridge_key},
                )
                if health.get("connected") is True:
                    passed("TQ/TDX 桥及上游 TQ")
                else:
                    failed("TQ/TDX 桥可达，但上游 TQ 未连接")
            except (DeploymentError, HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                failed(f"TQ/TDX 桥检查失败：{exc}")

    print()
    if failures:
        print(f"检查失败：{len(failures)} 项。")
        return 1
    print("基础部署检查通过。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Trader Windows 部署助手")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("probe")

    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--skip-pip-upgrade", action="store_true")

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--config", default="config.yaml")
    start_parser.add_argument("--host")
    start_parser.add_argument("--port", type=int)

    subparsers.add_parser("start-tdx-bridge")

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--base-url", default="http://127.0.0.1:18869")
    check_parser.add_argument("--skip-bridge", action="store_true")
    check_parser.add_argument("--timeout", type=int, default=10)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "probe":
            return 0 if python_is_supported() else 1
        if args.command == "install":
            return install(args.skip_pip_upgrade)
        if args.command == "start":
            return start(args.config, args.host, args.port)
        if args.command == "start-tdx-bridge":
            return start_tdx_bridge()
        if args.command == "check":
            return check(args.base_url, args.skip_bridge, args.timeout)
        raise DeploymentError(f"未知命令：{args.command}")
    except DeploymentError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
