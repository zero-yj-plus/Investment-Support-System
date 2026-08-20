from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from scripts.windows_upgrade import (
    UpgradeError,
    rollback_release,
    safe_extract,
    swap_release,
    verify_external_hash,
    verify_release,
)


def _write_manifest(root: Path) -> set[Path]:
    files = {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and path.name != "RELEASE_FILES.sha256"
    }
    lines = [
        f"{hashlib.sha256((root / path).read_bytes()).hexdigest()}  ./{path.as_posix()}"
        for path in sorted(files)
    ]
    (root / "RELEASE_FILES.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return files


def _minimal_release(root: Path, marker: str = "new") -> set[Path]:
    required = (
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
    )
    for relative in required:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{marker}:{relative}", encoding="utf-8")
    return _write_manifest(root)


def test_release_hash_manifest_and_safe_extract(tmp_path: Path):
    release = tmp_path / "release"
    release.mkdir()
    _minimal_release(release)
    package = tmp_path / "release.zip"
    with zipfile.ZipFile(package, "w") as archive:
        for path in release.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(release).as_posix())
    sidecar = Path(str(package) + ".sha256")
    sidecar.write_text(
        f"{hashlib.sha256(package.read_bytes()).hexdigest()}  {package.name}\n",
        encoding="ascii",
    )

    verify_external_hash(package, sidecar)
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    safe_extract(package, extracted)
    verified = verify_release(extracted)
    assert Path("frontend-vue/dist/index.html") in verified


def test_release_rejects_customer_state(tmp_path: Path):
    release = tmp_path / "release"
    release.mkdir()
    _minimal_release(release)
    (release / "config.yaml").write_text("secret: true", encoding="utf-8")
    _write_manifest(release)
    with pytest.raises(UpgradeError, match="客户状态"):
        verify_release(release)


def test_swap_and_rollback_preserve_customer_state(tmp_path: Path):
    project = tmp_path / "app"
    stage = tmp_path / "stage"
    backup = tmp_path / "backup"
    project.mkdir()
    stage.mkdir()
    backup.mkdir()

    (project / "hermes_trader").mkdir()
    (project / "hermes_trader" / "main.py").write_text("old", encoding="utf-8")
    (project / "frontend-vue" / "dist").mkdir(parents=True)
    (project / "frontend-vue" / "dist" / "index.html").write_text("old-ui", encoding="utf-8")
    (project / "config.yaml").write_text("customer", encoding="utf-8")
    (project / ".env").write_text("KEY=secret", encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "holdings.json").write_text("holdings", encoding="utf-8")

    new_files = _minimal_release(stage)
    record = swap_release(project, stage, backup, new_files)
    assert (project / "hermes_trader" / "main.py").read_text(encoding="utf-8").startswith("new:")
    assert (project / "config.yaml").read_text(encoding="utf-8") == "customer"
    assert (project / ".env").read_text(encoding="utf-8") == "KEY=secret"
    assert (project / "data" / "holdings.json").read_text(encoding="utf-8") == "holdings"

    rollback_release(project, backup, record)
    assert (project / "hermes_trader" / "main.py").read_text(encoding="utf-8") == "old"
    assert (project / "frontend-vue" / "dist" / "index.html").read_text(encoding="utf-8") == "old-ui"
