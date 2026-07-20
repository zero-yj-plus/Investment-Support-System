from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts.data_backup_restore import (
    BackupRestoreError,
    MANIFEST_HASH_NAME,
    MANIFEST_NAME,
    create_backup,
    drill_backup,
    restore_backup,
)


def _create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE positions (symbol TEXT PRIMARY KEY, shares INTEGER NOT NULL)")
        connection.execute("INSERT INTO positions VALUES ('600000', 200)")
        connection.commit()
    finally:
        connection.close()


def _add_bytes(archive: tarfile.TarFile, name: str, payload: bytes, *, kind: bytes = b"") -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    if kind == b"symlink":
        info.type = tarfile.SYMTYPE
        info.linkname = "../../outside"
        info.size = 0
        archive.addfile(info)
    else:
        archive.addfile(info, io.BytesIO(payload))


def _write_archive(path: Path, files: dict[str, tuple[bytes, str]], *, extra_symlink: bool = False) -> None:
    entries = [
        {
            "kind": kind,
            "path": name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }
        for name, (payload, kind) in sorted(files.items())
    ]
    manifest = {
        "created_at": "2026-07-12T00:00:00+00:00",
        "excluded": [],
        "files": entries,
        "schema_version": 1,
        "source_name": "fixture",
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    with tarfile.open(path, "w:gz") as archive:
        _add_bytes(archive, MANIFEST_NAME, manifest_bytes)
        _add_bytes(archive, MANIFEST_HASH_NAME, hashlib.sha256(manifest_bytes).hexdigest().encode() + b"\n")
        for name, (payload, _kind) in sorted(files.items()):
            _add_bytes(archive, name, payload)
        if extra_symlink:
            _add_bytes(archive, "escape-link", b"", kind=b"symlink")


def test_backup_restore_preserves_data_and_excludes_runtime_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _create_database(data / "state.db")
    writer = sqlite3.connect(data / "state.db")
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("INSERT INTO positions VALUES ('000001', 100)")
    writer.commit()
    (data / "settings.json").write_text(
        '{"model_connections":{"fixture":{"api_key":"server-private-marker"}}}',
        encoding="utf-8",
    )
    (data / "nested").mkdir()
    (data / "nested" / "settings.json").write_text('{"mode":"paper"}', encoding="utf-8")
    assert (data / "state.db-wal").exists()
    assert (data / "state.db-shm").exists()
    (data / "worker.lock").write_text("123")
    (data / "scratch.tmp").write_text("temporary")

    archive = tmp_path / "backup.tar.gz"
    try:
        result = create_backup(data.resolve(), archive.resolve())
    finally:
        writer.close()

    assert result["status"] == "ok"
    assert result["file_count"] == 2
    assert result["excluded_count"] == 5
    with tarfile.open(archive, "r:gz") as packaged:
        names = set(packaged.getnames())
        assert names == {MANIFEST_NAME, MANIFEST_HASH_NAME, "state.db", "nested/settings.json"}
        manifest_file = packaged.extractfile(MANIFEST_NAME)
        assert manifest_file is not None
        manifest = json.load(manifest_file)
        assert {"path": "settings.json", "reason": "server-secret"} in manifest["excluded"]

    restored = tmp_path / "restored"
    restore_result = restore_backup(archive.resolve(), restored.resolve())
    assert restore_result["sqlite_checked"] == ["state.db"]
    assert (restored / "nested" / "settings.json").read_text() == '{"mode":"paper"}'
    connection = sqlite3.connect(restored / "state.db")
    try:
        assert connection.execute("SELECT symbol, shares FROM positions ORDER BY symbol").fetchall() == [
            ("000001", 100),
            ("600000", 200),
        ]
    finally:
        connection.close()
    assert not (restored / "state.db-wal").exists()


def test_backup_refuses_output_inside_data_dir_and_existing_output(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "note.txt").write_text("safe")
    with pytest.raises(BackupRestoreError, match="outside"):
        create_backup(data.resolve(), (data / "backup.tar.gz").resolve())
    output = tmp_path / "existing.tar.gz"
    output.write_text("do not replace")
    with pytest.raises(BackupRestoreError, match="already exists"):
        create_backup(data.resolve(), output.resolve())
    assert output.read_text() == "do not replace"


def test_restore_rejects_path_traversal_before_writing(tmp_path: Path) -> None:
    archive = tmp_path / "malicious.tar.gz"
    _write_archive(archive, {"../escaped.txt": (b"owned", "file")})
    destination = tmp_path / "restored"
    with pytest.raises(BackupRestoreError, match="unsafe archive path"):
        restore_backup(archive.resolve(), destination.resolve())
    assert not destination.exists()
    assert not (tmp_path / "escaped.txt").exists()


def test_restore_rejects_symlink_members(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.tar.gz"
    _write_archive(archive, {"safe.txt": (b"safe", "file")}, extra_symlink=True)
    with pytest.raises(BackupRestoreError, match="non-regular"):
        restore_backup(archive.resolve(), (tmp_path / "restored").resolve())


def test_restore_rejects_payload_tampering(tmp_path: Path) -> None:
    archive = tmp_path / "tampered.tar.gz"
    expected = b"expected"
    manifest = {
        "created_at": "2026-07-12T00:00:00+00:00",
        "excluded": [],
        "files": [
            {
                "kind": "file",
                "path": "payload.txt",
                "sha256": hashlib.sha256(expected).hexdigest(),
                "size": len(expected),
            }
        ],
        "schema_version": 1,
        "source_name": "fixture",
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    with tarfile.open(archive, "w:gz") as packaged:
        _add_bytes(packaged, MANIFEST_NAME, manifest_bytes)
        _add_bytes(packaged, MANIFEST_HASH_NAME, hashlib.sha256(manifest_bytes).hexdigest().encode() + b"\n")
        _add_bytes(packaged, "payload.txt", b"tampered")
    destination = tmp_path / "restored"
    with pytest.raises(BackupRestoreError, match="payload SHA-256 mismatch"):
        restore_backup(archive.resolve(), destination.resolve())
    assert not destination.exists()


def test_restore_refuses_nonempty_or_symlink_destination(tmp_path: Path) -> None:
    archive = tmp_path / "backup.tar.gz"
    _write_archive(archive, {"safe.txt": (b"safe", "file")})
    destination = tmp_path / "restored"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep")
    with pytest.raises(BackupRestoreError, match="not empty"):
        restore_backup(archive.resolve(), destination.resolve())
    assert (destination / "keep.txt").read_text() == "keep"

    real = tmp_path / "real-empty"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(BackupRestoreError, match="real directory"):
        restore_backup(archive.resolve(), linked.absolute())


def test_restore_runs_sqlite_quick_check_even_if_manifest_claims_regular_file(tmp_path: Path) -> None:
    archive = tmp_path / "corrupt-sqlite.tar.gz"
    corrupt = b"SQLite format 3\x00" + b"not-a-valid-database"
    _write_archive(archive, {"state.db": (corrupt, "file")})
    destination = tmp_path / "restored"
    with pytest.raises(BackupRestoreError, match="SQLite quick_check failed"):
        restore_backup(archive.resolve(), destination.resolve())
    assert not destination.exists()


def test_drill_uses_temporary_restore_and_does_not_modify_source(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _create_database(data / "state.sqlite")
    (data / "config.json").write_text("{}")
    before = {path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in data.iterdir()}
    archive = tmp_path / "drill.tar.gz"

    result = drill_backup(data_dir=data.resolve(), output=archive.resolve())

    after = {path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in data.iterdir()}
    assert result["status"] == "ok"
    assert result["temporary_restore_removed"] is True
    assert result["sqlite_checked"] == ["state.sqlite"]
    assert before == after
    assert archive.exists()


def test_cli_failure_is_json_and_nonzero(tmp_path: Path) -> None:
    archive = tmp_path / "malicious.tar.gz"
    _write_archive(archive, {"../escaped.txt": (b"owned", "file")})
    script = Path(__file__).parents[1] / "scripts" / "data_backup_restore.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "restore",
            "--archive",
            str(archive.resolve()),
            "--restore-dir",
            str((tmp_path / "restored").resolve()),
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["status"] == "error"
    assert payload["operation"] == "restore"
    assert "unsafe archive path" in payload["error"]


def test_cli_usage_failure_is_also_json() -> None:
    script = Path(__file__).parents[1] / "scripts" / "data_backup_restore.py"
    completed = subprocess.run(
        [sys.executable, str(script), "backup"],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 2
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["status"] == "error"
    assert "required" in payload["error"]
