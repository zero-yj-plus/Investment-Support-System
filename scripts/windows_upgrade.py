from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
import zipfile


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "RELEASE_FILES.sha256"
PROTECTED_TOP_LEVEL = {".env", ".upgrade-work", ".venv", "config.yaml", "data", "upgrade-backups"}
REQUIRED_RELEASE_PATHS = {
    "hermes_trader/__init__.py",
    "hermes_trader/main.py",
    "hermes_trader/maintenance_queue.py",
    "hermes_trader/worker.py",
    "hermes_trader/data/__init__.py",
    "frontend-vue/dist/index.html",
    "scripts/windows_deploy.py",
    "scripts/windows_upgrade.py",
    "install.bat",
    "start.bat",
    "check.bat",
    "upgrade.bat",
    "requirements.txt",
}
WORKER_PID_RELATIVE = Path("data") / "logs" / "maintenance-worker.pid"
HASH_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\./(.+)$")


class UpgradeError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(raw: str) -> Path:
    normalized = raw.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise UpgradeError(f"发布包包含不安全路径：{raw}")
    if ":" in pure.parts[0]:
        raise UpgradeError(f"发布包包含不安全路径：{raw}")
    return Path(*pure.parts)


def _is_protected(relative: Path) -> bool:
    return bool(relative.parts) and relative.parts[0].lower() in PROTECTED_TOP_LEVEL


def verify_external_hash(package: Path, sha256_file: Path) -> None:
    if not sha256_file.is_file():
        raise UpgradeError(f"缺少压缩包校验文件：{sha256_file}")
    tokens = sha256_file.read_text(encoding="ascii", errors="strict").strip().split()
    if not tokens or not re.fullmatch(r"[0-9a-fA-F]{64}", tokens[0]):
        raise UpgradeError(f"无效的 SHA-256 文件：{sha256_file}")
    actual = _sha256(package)
    if actual.lower() != tokens[0].lower():
        raise UpgradeError(f"压缩包 SHA-256 不匹配：期望 {tokens[0].lower()}，实际 {actual}")
    print(f"[PASS] 压缩包 SHA-256：{actual}")


def safe_extract(package: Path, destination: Path) -> None:
    with zipfile.ZipFile(package) as archive:
        for info in archive.infolist():
            stripped = info.filename.rstrip("/")
            if not stripped:
                continue
            relative = _relative_path(stripped)
            unix_mode = (info.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise UpgradeError(f"发布包不允许符号链接：{info.filename}")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def read_manifest(root: Path) -> dict[Path, str]:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise UpgradeError(f"发布包缺少 {MANIFEST_NAME}")
    entries: dict[Path, str] = {}
    for line_number, raw in enumerate(manifest_path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw.strip():
            continue
        match = HASH_LINE.fullmatch(raw.strip())
        if not match:
            raise UpgradeError(f"{MANIFEST_NAME} 第 {line_number} 行格式无效")
        relative = _relative_path(match.group(2))
        if relative == Path(MANIFEST_NAME) or relative in entries:
            raise UpgradeError(f"{MANIFEST_NAME} 包含重复或非法项目：{relative}")
        entries[relative] = match.group(1).lower()
    return entries


def verify_release(root: Path) -> set[Path]:
    entries = read_manifest(root)
    actual_files = {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and path.name != MANIFEST_NAME
    }
    expected_files = set(entries)
    missing = sorted(str(path) for path in expected_files - actual_files)
    extra = sorted(str(path) for path in actual_files - expected_files)
    if missing or extra:
        raise UpgradeError(f"发布包文件清单不一致：缺少={missing}，额外={extra}")
    for relative, expected_hash in entries.items():
        if _is_protected(relative):
            raise UpgradeError(f"发布包不得携带客户状态文件：{relative}")
        if _sha256(root / relative) != expected_hash:
            raise UpgradeError(f"发布文件校验失败：{relative}")
    normalized = {path.as_posix() for path in entries}
    required_missing = sorted(REQUIRED_RELEASE_PATHS - normalized)
    if required_missing:
        raise UpgradeError(f"发布包缺少升级必需文件：{required_missing}")
    print(f"[PASS] 发布文件清单：{len(entries)} 项")
    return set(entries)


def _load_dotenv(path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    if not path.is_file():
        return environment
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            environment[name] = value
    return environment


def runtime_info(project_root: Path) -> dict[str, object]:
    python = project_root / ".venv" / "Scripts" / "python.exe"
    config = project_root / "config.yaml"
    if not python.is_file() or not config.is_file():
        raise UpgradeError("当前安装缺少 .venv\\Scripts\\python.exe 或 config.yaml")
    code = (
        "import json,sys;"
        "from hermes_trader.config import Config;"
        "c=Config(sys.argv[1]);"
        "print(json.dumps({'host':c.host,'port':c.port,'data_dir':str(c.data_dir)}))"
    )
    completed = subprocess.run(
        [str(python), "-c", code, str(config)],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        raise UpgradeError(f"读取当前运行配置失败：{completed.stderr.strip() or completed.stdout.strip()}")
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        payload["port"] = int(payload["port"])
        payload["data_dir"] = str(Path(str(payload["data_dir"])).resolve())
        return payload
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise UpgradeError(f"无法解析当前运行配置：{completed.stdout.strip()}") from exc


def _listening_pids(port: int) -> set[int]:
    if os.name != "nt":
        return set()
    completed = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    pids: set[int] = set()
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[-2].upper() != "LISTENING":
            continue
        local = parts[1].strip("[]")
        try:
            local_port = int(local.rsplit(":", 1)[1])
            pid = int(parts[-1])
        except (IndexError, ValueError):
            continue
        if local_port == port:
            pids.add(pid)
    return pids


def _process_details(pid: int) -> dict[str, str]:
    shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if not shell:
        raise UpgradeError("无法安全识别占用端口的进程：未找到 PowerShell")
    command = (
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}';"
        "if($p){$p|Select-Object ProcessId,ExecutablePath,CommandLine|ConvertTo-Json -Compress}"
        "else{Write-Output '__MISSING__'}"
    )
    completed = subprocess.run(
        [shell, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode or not completed.stdout.strip():
        raise UpgradeError(f"无法读取 PID {pid} 的进程信息")
    output = completed.stdout.strip()
    if output == "__MISSING__":
        raise ProcessLookupError(pid)
    payload = json.loads(output)
    return {key: str(payload.get(key) or "") for key in ("ExecutablePath", "CommandLine")}


def _owned_process(pid: int, project_root: Path, marker: str) -> bool:
    details = _process_details(pid)
    root_text = str(project_root.resolve()).replace("/", "\\").lower().rstrip("\\")
    executable = details["ExecutablePath"].replace("/", "\\").lower()
    command_line = details["CommandLine"].replace("/", "\\").lower()
    return executable.startswith(root_text + "\\.venv\\") and root_text in command_line and marker.lower() in command_line


def stop_owned_listener(project_root: Path, port: int, marker: str) -> bool:
    pids = _listening_pids(port)
    if not pids:
        return False
    unowned = [pid for pid in pids if not _owned_process(pid, project_root, marker)]
    if unowned:
        raise UpgradeError(f"端口 {port} 被非当前安装目录进程占用，拒绝终止：PID {unowned}")
    for pid in sorted(pids):
        print(f"停止旧进程：PID {pid}，端口 {port}")
        completed = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        if completed.returncode:
            raise UpgradeError(f"停止 PID {pid} 失败")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not _listening_pids(port):
            return True
        time.sleep(0.5)
    raise UpgradeError(f"端口 {port} 在停止进程后仍被占用")


def stop_owned_pid_file(project_root: Path, pid_file: Path, marker: str) -> bool:
    """停止无监听端口的本项目后台进程；绝不终止无法确认归属的 PID。"""
    if not pid_file.is_file():
        return False
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        pid_file.unlink(missing_ok=True)
        return False
    try:
        owned = _owned_process(pid, project_root, marker)
    except ProcessLookupError:
        # CIM 已明确确认进程不存在，PID 文件只是陈旧状态。
        pid_file.unlink(missing_ok=True)
        return False
    if not owned:
        print(f"[WARN] PID 文件不是当前安装目录进程，未终止 PID {pid}。")
        pid_file.unlink(missing_ok=True)
        return False
    print(f"停止旧重型后台 Worker：PID {pid}")
    completed = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
    if completed.returncode:
        raise UpgradeError(f"停止重型后台 Worker PID {pid} 失败")
    pid_file.unlink(missing_ok=True)
    return True


def create_state_backup(project_root: Path, data_dir: Path, backup_root: Path) -> Path:
    state_root = backup_root / "state"
    state_root.mkdir(parents=True, exist_ok=False)
    for name in ("config.yaml", ".env"):
        source = project_root / name
        if source.is_file():
            shutil.copy2(source, state_root / name)
    archive = state_root / "data-before-upgrade.tar.gz"
    python = project_root / ".venv" / "Scripts" / "python.exe"
    helper = project_root / "scripts" / "data_backup_restore.py"
    frozen = subprocess.run(
        [str(python), "-m", "pip", "freeze"],
        cwd=project_root,
        capture_output=True,
        check=False,
    )
    if frozen.returncode == 0:
        (state_root / "pip-freeze.txt").write_bytes(frozen.stdout)
    completed = subprocess.run(
        [str(python), str(helper), "backup", "--data-dir", str(data_dir), "--output", str(archive)],
        cwd=project_root,
        check=False,
    )
    if completed.returncode or not archive.is_file():
        raise UpgradeError("运行数据备份失败，未执行程序替换")
    print(f"[PASS] 配置和运行数据备份：{state_root}")
    return archive


def validate_data_dir(project_root: Path, data_dir: Path) -> None:
    resolved = data_dir.resolve()
    if resolved == Path(resolved.anchor) or resolved in {project_root.resolve(), project_root.resolve().parent}:
        raise UpgradeError(f"server.data_dir 指向过宽目录，拒绝升级：{resolved}")
    if not resolved.is_dir():
        raise UpgradeError(f"运行数据目录不存在：{resolved}")


def restore_state_data(project_root: Path, data_dir: Path, archive: Path) -> Path:
    validate_data_dir(project_root, data_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    failed_data = data_dir.with_name(f"{data_dir.name}.failed-upgrade-{stamp}")
    if failed_data.exists():
        raise UpgradeError(f"回滚暂存目录已存在：{failed_data}")
    shutil.move(str(data_dir), str(failed_data))
    helper = project_root / "scripts" / "data_backup_restore.py"
    python = project_root / ".venv" / "Scripts" / "python.exe"
    completed = subprocess.run(
        [str(python), str(helper), "restore", "--archive", str(archive), "--restore-dir", str(data_dir)],
        cwd=project_root,
        check=False,
    )
    if completed.returncode:
        if data_dir.is_dir():
            shutil.rmtree(data_dir)
        shutil.move(str(failed_data), str(data_dir))
        raise UpgradeError("自动恢复升级前数据失败；已保留升级失败时的数据目录")
    print(f"[PASS] 已恢复升级前运行数据；失败版本数据保留在：{failed_data}")
    return failed_data


def _old_manifest_paths(project_root: Path) -> set[Path]:
    try:
        return set(read_manifest(project_root))
    except UpgradeError:
        print("[WARN] 旧安装缺少有效发布清单，只替换新版包中的顶层程序项。")
        return set()


def swap_release(project_root: Path, staged_root: Path, backup_root: Path, new_files: set[Path]) -> dict[str, object]:
    program_backup = backup_root / "program"
    program_backup.mkdir(parents=True, exist_ok=False)
    old_files = _old_manifest_paths(project_root)
    incoming_top = sorted({path.parts[0] for path in new_files} | {MANIFEST_NAME})
    records: list[dict[str, object]] = []

    for name in incoming_top:
        if name.lower() in PROTECTED_TOP_LEVEL:
            raise UpgradeError(f"拒绝替换客户状态路径：{name}")
        source = staged_root / name
        target = project_root / name
        if name.lower() == "upgrade.bat" and target.exists():
            continue
        backup = program_backup / name
        had_old = target.exists()
        if had_old:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(backup))
        shutil.move(str(source), str(target))
        records.append({"relative": name, "had_old": had_old})

    incoming_top_set = set(incoming_top)
    stale_records: list[str] = []
    for relative in sorted(old_files - new_files, key=lambda item: (len(item.parts), item.as_posix()), reverse=True):
        if _is_protected(relative) or relative.parts[0] in incoming_top_set:
            continue
        target = project_root / relative
        if not target.exists() or target.is_dir():
            continue
        backup = program_backup / "_stale" / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(backup))
        stale_records.append(relative.as_posix())

    record = {"swapped": records, "stale": stale_records}
    (backup_root / "swap.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def rollback_release(project_root: Path, backup_root: Path, record: dict[str, object]) -> None:
    program_backup = backup_root / "program"
    for item in reversed(list(record.get("swapped", []))):
        relative = Path(str(item["relative"]))
        target = project_root / relative
        backup = program_backup / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if bool(item["had_old"]):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup), str(target))
    for raw in record.get("stale", []):
        relative = Path(str(raw))
        backup = program_backup / "_stale" / relative
        target = project_root / relative
        if backup.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup), str(target))


def install_dependencies(project_root: Path, previous_requirements_hash: str | None) -> bool:
    python = project_root / ".venv" / "Scripts" / "python.exe"
    requirements = project_root / "requirements.txt"
    changed = previous_requirements_hash != _sha256(requirements)
    commands: list[list[str]] = []
    if changed:
        commands.append([str(python), "-m", "pip", "install", "-r", str(requirements)])
    else:
        print("[PASS] 生产依赖清单未变化，跳过联网安装。")
    commands.extend(
        (
            [str(python), "-m", "pip", "check"],
            [str(python), "-c", "import hermes_trader.data; from hermes_trader.main import main"],
        )
    )
    for command in commands:
        completed = subprocess.run(command, cwd=project_root, check=False)
        if completed.returncode:
            raise UpgradeError(f"升级依赖或导入检查失败：{' '.join(command[1:])}")
    return changed


def _background_flags() -> int:
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


def start_runtime(
    project_root: Path,
    data_dir: Path,
    config: Path,
    start_app: bool,
    start_bridge: bool,
) -> tuple[
    subprocess.Popen | None,
    subprocess.Popen | None,
    subprocess.Popen | None,
]:
    python = project_root / ".venv" / "Scripts" / "python.exe"
    environment = _load_dotenv(project_root / ".env")
    environment["PYTHONUNBUFFERED"] = "1"
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bridge_process = None
    if start_bridge:
        bridge_log = (log_dir / f"tdx-bridge-upgrade-{stamp}.log").open("ab")
        try:
            bridge_process = subprocess.Popen(
                [str(python), str(project_root / "scripts" / "tq_tdx_bridge.py")],
                cwd=project_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=bridge_log,
                stderr=subprocess.STDOUT,
                creationflags=_background_flags(),
            )
        finally:
            bridge_log.close()
    worker_process = None
    if start_app:
        worker_log = (log_dir / f"hermes-worker-upgrade-{stamp}.log").open("ab")
        try:
            worker_process = subprocess.Popen(
                [str(python), "-m", "hermes_trader.worker", "--config", str(config)],
                cwd=project_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=worker_log,
                stderr=subprocess.STDOUT,
                creationflags=_background_flags(),
            )
            worker_pid_file = project_root / WORKER_PID_RELATIVE
            worker_pid_file.parent.mkdir(parents=True, exist_ok=True)
            worker_pid_file.write_text(str(worker_process.pid), encoding="ascii")
        finally:
            worker_log.close()
    app_process = None
    if start_app:
        app_log = (log_dir / f"hermes-upgrade-{stamp}.log").open("ab")
        try:
            app_process = subprocess.Popen(
                [str(python), "-m", "hermes_trader", "--config", str(config)],
                cwd=project_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=app_log,
                stderr=subprocess.STDOUT,
                creationflags=_background_flags(),
            )
        finally:
            app_log.close()
    return app_process, bridge_process, worker_process


def wait_for_status(base_url: str, process: subprocess.Popen, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise UpgradeError(f"新版服务启动后退出，退出码：{process.returncode}")
        try:
            request = Request(base_url.rstrip("/") + "/api/status", method="GET")
            with urlopen(request, timeout=5) as response:
                if int(response.status) == 200:
                    response.read(1024 * 1024)
                    print(f"[PASS] 新版服务健康检查：{base_url}/api/status")
                    return
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1)
    raise UpgradeError(f"新版服务在 {timeout} 秒内未通过健康检查：{last_error}")


def terminate_started(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def discover_package(project_root: Path) -> Path:
    candidates = sorted(
        project_root.glob("WUDI-*-Windows-deploy-*.zip"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise UpgradeError("未指定新版 ZIP，当前目录也没有 WUDI-*-Windows-deploy-*.zip")
    if len(candidates) > 1:
        print(f"[WARN] 检测到多个部署包，将使用最新文件：{candidates[0].name}")
    return candidates[0]


def upgrade(args: argparse.Namespace) -> int:
    project_root = Path(args.app_root).resolve()
    package = Path(args.package).resolve() if args.package else discover_package(project_root)
    if not package.is_file():
        raise UpgradeError(f"找不到新版压缩包：{package}")
    sha256_file = Path(args.sha256_file).resolve() if args.sha256_file else Path(str(package) + ".sha256")
    verify_external_hash(package, sha256_file)
    work_root = project_root / ".upgrade-work"
    work_root.mkdir(parents=False, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stage-", dir=work_root) as stage_name:
        staged_root = Path(stage_name)
        safe_extract(package, staged_root)
        new_files = verify_release(staged_root)
        if args.verify_only:
            print("发布包升级前校验通过；未停止服务、未修改程序或客户数据。")
            return 0

        info = runtime_info(project_root)
        port = int(info["port"])
        data_dir = Path(str(info["data_dir"]))
        validate_data_dir(project_root, data_dir)
        bridge_environment = _load_dotenv(project_root / ".env")
        bridge_port = int(bridge_environment.get("TQ_BRIDGE_PORT", "18879"))
        base_url = args.base_url or f"http://127.0.0.1:{port}"

        app_was_running = bool(_listening_pids(port))
        bridge_was_running = bool(_listening_pids(bridge_port))
        app_stopped = False
        bridge_stopped = False
        worker_stopped = False
        record: dict[str, object] | None = None
        state_archive: Path | None = None
        app_process = None
        bridge_process = None
        worker_process = None
        backup_root: Path | None = None
        try:
            if app_was_running:
                app_stopped = stop_owned_listener(project_root, port, "hermes_trader")
            worker_stopped = stop_owned_pid_file(
                project_root,
                project_root / WORKER_PID_RELATIVE,
                "hermes_trader.worker",
            )
            if bridge_was_running:
                bridge_stopped = stop_owned_listener(project_root, bridge_port, "tq_tdx_bridge.py")

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_root = project_root / "upgrade-backups" / stamp
            backup_root.mkdir(parents=True, exist_ok=False)
            state_archive = create_state_backup(project_root, data_dir, backup_root)
            old_requirements = project_root / "requirements.txt"
            old_requirements_hash = _sha256(old_requirements) if old_requirements.is_file() else None
            record = swap_release(project_root, staged_root, backup_root, new_files)
            dependencies_changed = install_dependencies(project_root, old_requirements_hash)
            should_start = app_was_running or args.start
            if should_start or bridge_was_running:
                app_process, bridge_process, worker_process = start_runtime(
                    project_root,
                    data_dir,
                    project_root / "config.yaml",
                    should_start,
                    bridge_was_running,
                )
            if app_process is not None:
                wait_for_status(base_url, app_process, args.timeout)
            print("\n升级完成。")
            print(f"程序目录：{project_root}")
            print(f"回滚备份：{backup_root}")
            if not should_start:
                print("旧服务升级前未运行，因此新版保持停止；需要时运行 start.bat。")
            return 0
        except Exception:
            new_app_started = app_process is not None
            terminate_started(app_process)
            terminate_started(bridge_process)
            terminate_started(worker_process)
            if record is not None and backup_root is not None:
                print("[WARN] 升级失败，正在恢复旧程序……", file=sys.stderr)
                rollback_release(project_root, backup_root, record)
                frozen_requirements = backup_root / "state" / "pip-freeze.txt"
                if locals().get("dependencies_changed") and frozen_requirements.is_file():
                    subprocess.run(
                        [str(project_root / ".venv" / "Scripts" / "python.exe"), "-m", "pip", "install", "-r", str(frozen_requirements)],
                        cwd=project_root,
                        check=False,
                    )
                if new_app_started and state_archive is not None:
                    restore_state_data(project_root, data_dir, state_archive)
            if app_stopped or bridge_stopped or worker_stopped:
                print("[WARN] 正在重新启动升级前的服务……", file=sys.stderr)
                old_app, _old_bridge, _old_worker = start_runtime(
                    project_root,
                    data_dir,
                    project_root / "config.yaml",
                    app_stopped,
                    bridge_stopped,
                )
                if old_app is not None:
                    wait_for_status(base_url, old_app, args.timeout)
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="W.U.D.I. Windows 原目录无感升级")
    parser.add_argument("package", nargs="?", help="新版 Windows deploy ZIP；省略时使用当前目录最新包")
    parser.add_argument("--app-root", default=str(DEFAULT_PROJECT_ROOT), help="当前客户安装目录")
    parser.add_argument("--sha256-file", help="ZIP 的 .sha256 文件；默认使用 <ZIP>.sha256")
    parser.add_argument("--base-url", help="健康检查地址；默认使用 config.yaml 中的端口")
    parser.add_argument("--timeout", type=int, default=60, help="新版健康检查等待秒数")
    parser.add_argument("--start", action="store_true", help="即使旧服务未运行也启动新版")
    parser.add_argument("--verify-only", action="store_true", help="只校验 ZIP 和发布清单，不停止或修改服务")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return upgrade(build_parser().parse_args(argv))
    except UpgradeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[ERROR] 用户中止升级。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"[ERROR] 升级失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
