#!/usr/bin/env python3
"""Create and verify portable, fail-closed backups of a Hermes data directory.

The archive format is a gzip-compressed tar containing only regular payload
files plus ``manifest.json`` and ``manifest.sha256``.  Restore never calls
``extractall``: every path and byte is validated before the staged directory is
atomically moved into the explicitly selected destination.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
MANIFEST_HASH_NAME = "manifest.sha256"
SQLITE_HEADER = b"SQLite format 3\x00"
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_FILES = 100_000
MAX_RESTORE_BYTES = 100 * 1024 * 1024 * 1024

_EXCLUDED_NAMES = {".DS_Store", "__pycache__"}
_EXCLUDED_SUFFIXES = {
    ".lock",
    ".lck",
    ".pid",
    ".tmp",
    ".temp",
    ".swp",
    ".swo",
    ".journal",
    ".wal",
    ".shm",
}
_EXCLUDED_ENDINGS = ("-wal", "-shm", "-journal", "~")


class BackupRestoreError(RuntimeError):
    """Expected validation or operational failure."""


class _JsonArgumentParser(argparse.ArgumentParser):
    """Route usage failures through the JSON CLI error boundary."""

    def error(self, message: str) -> None:
        raise BackupRestoreError(message)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _safe_relative_path(raw: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
        raise BackupRestoreError(f"unsafe archive path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw != path.as_posix():
        raise BackupRestoreError(f"unsafe archive path: {raw!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise BackupRestoreError(f"unsafe archive path: {raw!r}")
    if path.parts[0].endswith(":"):
        raise BackupRestoreError(f"unsafe archive path: {raw!r}")
    return path


def _excluded(path: Path, *, root: Path) -> str | None:
    # The root settings file contains server-managed credentials such as custom
    # model API keys.  It belongs to the deployment secret store, not to a
    # portable data backup.  Only the root file is special: business data may
    # legitimately contain nested files with the same basename.
    if path.parent == root and path.name == "settings.json":
        return "server-secret"
    name = path.name
    lowered = name.lower()
    if name in _EXCLUDED_NAMES:
        return "non-persistent-runtime-file"
    if lowered.endswith(_EXCLUDED_ENDINGS):
        return "non-persistent-runtime-file"
    if path.suffix.lower() in _EXCLUDED_SUFFIXES:
        return "non-persistent-runtime-file"
    return None


def _is_sqlite_candidate(path: Path) -> bool:
    if path.suffix.lower() in SQLITE_SUFFIXES:
        return True
    try:
        with path.open("rb") as stream:
            return stream.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError as exc:
        raise BackupRestoreError(f"cannot inspect {path}: {exc}") from exc


def _sqlite_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _sqlite_quick_check(path: Path) -> None:
    try:
        connection = sqlite3.connect(_sqlite_uri(path), uri=True, timeout=5.0)
        try:
            row = connection.execute("PRAGMA quick_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise BackupRestoreError(f"SQLite quick_check failed for {path.name}: {exc}") from exc
    if not row or row[0] != "ok":
        detail = row[0] if row else "no result"
        raise BackupRestoreError(f"SQLite quick_check failed for {path.name}: {detail}")


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    """Use SQLite's online backup API so committed WAL pages are not lost."""

    try:
        source_connection = sqlite3.connect(_sqlite_uri(source), uri=True, timeout=10.0)
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
            source_connection.close()
    except sqlite3.Error as exc:
        raise BackupRestoreError(f"cannot snapshot SQLite database {source.name}: {exc}") from exc
    _sqlite_quick_check(destination)


def _iter_source_files(root: Path) -> tuple[list[tuple[Path, str]], list[dict[str, str]]]:
    files: list[tuple[Path, str]] = []
    excluded: list[dict[str, str]] = []
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept_directories: list[str] = []
        for name in sorted(directory_names):
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if candidate.is_symlink():
                excluded.append({"path": relative, "reason": "symbolic-link"})
            elif reason := _excluded(candidate, root=root):
                excluded.append({"path": relative, "reason": reason})
            else:
                kept_directories.append(name)
        directory_names[:] = kept_directories

        for name in sorted(file_names):
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            reason = _excluded(candidate, root=root)
            try:
                mode = candidate.lstat().st_mode
            except OSError as exc:
                raise BackupRestoreError(f"cannot inspect {relative}: {exc}") from exc
            if stat.S_ISLNK(mode):
                excluded.append({"path": relative, "reason": "symbolic-link"})
            elif reason:
                excluded.append({"path": relative, "reason": reason})
            elif not stat.S_ISREG(mode):
                excluded.append({"path": relative, "reason": "non-regular-file"})
            else:
                _safe_relative_path(relative)
                files.append((candidate, relative))
    files.sort(key=lambda item: item[1])
    excluded.sort(key=lambda item: item["path"])
    return files, excluded


def _copy_regular_stably(source: Path, destination: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        source_descriptor = os.open(source, flags)
    except OSError as exc:
        raise BackupRestoreError(f"cannot safely open source file {source.name}: {exc}") from exc
    try:
        before = os.fstat(source_descriptor)
        path_before = source.lstat()
        if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (
            path_before.st_dev,
            path_before.st_ino,
        ):
            raise BackupRestoreError(f"source changed type during backup: {source.name}")
        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            destination_flags |= os.O_NOFOLLOW
        destination_descriptor = os.open(destination, destination_flags, 0o600)
        with os.fdopen(source_descriptor, "rb", closefd=False) as input_stream, os.fdopen(
            destination_descriptor, "wb"
        ) as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        after = os.fstat(source_descriptor)
        path_after = source.lstat()
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        path_identity_after = (
            path_after.st_dev,
            path_after.st_ino,
            path_after.st_size,
            path_after.st_mtime_ns,
        )
        if identity_before != identity_after or identity_after != path_identity_after:
            raise BackupRestoreError(f"source changed while being backed up: {source.name}")
    finally:
        os.close(source_descriptor)


def _tar_regular(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    info.mtime = 0
    archive.addfile(info, io.BytesIO(data))


def _publish_without_overwrite(temporary: Path, output: Path) -> None:
    try:
        os.link(temporary, output)
    except FileExistsError as exc:
        raise BackupRestoreError(f"output already exists: {output}") from exc
    except OSError as exc:
        raise BackupRestoreError(f"cannot publish backup {output}: {exc}") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def create_backup(data_dir: str | os.PathLike[str], output: str | os.PathLike[str]) -> dict[str, Any]:
    root = Path(data_dir).expanduser()
    output_path = Path(output).expanduser()
    if not root.is_absolute() or not output_path.is_absolute():
        raise BackupRestoreError("--data-dir and --output must be absolute paths")
    if not root.exists() or not root.is_dir() or root.is_symlink():
        raise BackupRestoreError(f"data directory must be a real directory: {root}")
    root = root.resolve(strict=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_parent = output_path.parent.resolve(strict=True)
    output_path = output_parent / output_path.name
    if _lexists(output_path):
        raise BackupRestoreError(f"output already exists: {output_path}")
    try:
        output_path.relative_to(root)
    except ValueError:
        pass
    else:
        raise BackupRestoreError("backup output must be outside --data-dir")

    source_files, excluded = _iter_source_files(root)
    with tempfile.TemporaryDirectory(prefix="hermes-backup-stage-") as stage_name:
        stage = Path(stage_name)
        manifest_files: list[dict[str, Any]] = []
        for source, relative in source_files:
            staged = stage.joinpath(*PurePosixPath(relative).parts)
            staged.parent.mkdir(parents=True, exist_ok=True)
            if _is_sqlite_candidate(source):
                _snapshot_sqlite(source, staged)
                kind = "sqlite"
            else:
                _copy_regular_stably(source, staged)
                kind = "file"
            manifest_files.append(
                {
                    "kind": kind,
                    "path": relative,
                    "sha256": _sha256_file(staged),
                    "size": staged.stat().st_size,
                }
            )

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "excluded": excluded,
            "files": manifest_files,
            "schema_version": SCHEMA_VERSION,
            "source_name": root.name,
        }
        manifest_bytes = _json_bytes(manifest)
        manifest_sha256 = _sha256_bytes(manifest_bytes)
        signature_bytes = f"{manifest_sha256}\n".encode("ascii")

        descriptor, temporary_name = tempfile.mkstemp(prefix=".hermes-backup-", suffix=".tar.gz", dir=output_parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with tarfile.open(temporary, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                _tar_regular(archive, MANIFEST_NAME, manifest_bytes)
                _tar_regular(archive, MANIFEST_HASH_NAME, signature_bytes)
                for entry in manifest_files:
                    relative = entry["path"]
                    staged = stage.joinpath(*PurePosixPath(relative).parts)
                    info = tarfile.TarInfo(relative)
                    info.size = entry["size"]
                    info.mode = 0o600
                    info.mtime = 0
                    with staged.open("rb") as stream:
                        archive.addfile(info, stream)
            _publish_without_overwrite(temporary, output_path)
        finally:
            temporary.unlink(missing_ok=True)

    return {
        "archive": str(output_path),
        "excluded_count": len(excluded),
        "file_count": len(manifest_files),
        "manifest_sha256": manifest_sha256,
        "operation": "backup",
        "status": "ok",
    }


def _read_small_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    if member.size < 0 or member.size > MAX_MANIFEST_BYTES:
        raise BackupRestoreError(f"metadata member is too large: {member.name}")
    stream = archive.extractfile(member)
    if stream is None:
        raise BackupRestoreError(f"cannot read archive member: {member.name}")
    data = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(data) != member.size:
        raise BackupRestoreError(f"truncated archive member: {member.name}")
    return data


def _validated_archive(archive_path: Path) -> tuple[tarfile.TarFile, dict[str, tarfile.TarInfo], dict[str, Any], str]:
    try:
        archive = tarfile.open(archive_path, mode="r:*")
    except (OSError, tarfile.TarError) as exc:
        raise BackupRestoreError(f"cannot open backup archive: {exc}") from exc
    try:
        members: dict[str, tarfile.TarInfo] = {}
        total_size = 0
        for member in archive.getmembers():
            _safe_relative_path(member.name)
            if not member.isreg():
                raise BackupRestoreError(f"archive contains non-regular member: {member.name}")
            if member.name in members:
                raise BackupRestoreError(f"archive contains duplicate member: {member.name}")
            members[member.name] = member
            total_size += member.size
        if len(members) > MAX_ARCHIVE_FILES + 2 or total_size > MAX_RESTORE_BYTES:
            raise BackupRestoreError("archive exceeds restore safety limits")
        if MANIFEST_NAME not in members or MANIFEST_HASH_NAME not in members:
            raise BackupRestoreError("archive is missing signed manifest metadata")

        manifest_bytes = _read_small_member(archive, members[MANIFEST_NAME])
        signature_bytes = _read_small_member(archive, members[MANIFEST_HASH_NAME])
        manifest_sha256 = _sha256_bytes(manifest_bytes)
        try:
            declared_signature = signature_bytes.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise BackupRestoreError("manifest signature is not ASCII") from exc
        if declared_signature != manifest_sha256:
            raise BackupRestoreError("manifest SHA-256 mismatch")
        try:
            manifest = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BackupRestoreError(f"invalid manifest JSON: {exc}") from exc
        entries = _validate_manifest(manifest)
        expected = {MANIFEST_NAME, MANIFEST_HASH_NAME, *(entry["path"] for entry in entries)}
        if set(members) != expected:
            missing = sorted(expected - set(members))
            extra = sorted(set(members) - expected)
            raise BackupRestoreError(f"archive/manifest file set mismatch; missing={missing}, extra={extra}")
        for entry in entries:
            if members[entry["path"]].size != entry["size"]:
                raise BackupRestoreError(f"size mismatch for {entry['path']}")
        return archive, members, manifest, manifest_sha256
    except Exception:
        archive.close()
        raise


def _validate_manifest(manifest: Any) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise BackupRestoreError("unsupported or invalid manifest schema")
    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list) or len(raw_entries) > MAX_ARCHIVE_FILES:
        raise BackupRestoreError("invalid manifest files list")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise BackupRestoreError("invalid manifest file entry")
        path = raw.get("path")
        _safe_relative_path(path)
        if path in {MANIFEST_NAME, MANIFEST_HASH_NAME} or path in seen:
            raise BackupRestoreError(f"duplicate or reserved manifest path: {path}")
        size = raw.get("size")
        digest = raw.get("sha256")
        kind = raw.get("kind")
        if type(size) is not int or size < 0:
            raise BackupRestoreError(f"invalid size for {path}")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise BackupRestoreError(f"invalid SHA-256 for {path}")
        if kind not in {"file", "sqlite"}:
            raise BackupRestoreError(f"invalid file kind for {path}")
        total += size
        if total > MAX_RESTORE_BYTES:
            raise BackupRestoreError("manifest exceeds restore safety limit")
        seen.add(path)
        entries.append({"kind": kind, "path": path, "sha256": digest, "size": size})
    return entries


def _assert_new_or_empty(path: Path) -> None:
    if not _lexists(path):
        return
    if path.is_symlink() or not path.is_dir():
        raise BackupRestoreError(f"restore destination must be a real directory: {path}")
    if next(path.iterdir(), None) is not None:
        raise BackupRestoreError(f"restore destination is not empty: {path}")


def restore_backup(archive: str | os.PathLike[str], restore_dir: str | os.PathLike[str]) -> dict[str, Any]:
    archive_path = Path(archive).expanduser()
    requested_target = Path(restore_dir).expanduser()
    if not archive_path.is_absolute() or not requested_target.is_absolute():
        raise BackupRestoreError("--archive and --restore-dir must be absolute paths")
    if not archive_path.is_file():
        raise BackupRestoreError(f"archive does not exist: {archive_path}")
    requested_target.parent.mkdir(parents=True, exist_ok=True)
    target_parent = requested_target.parent.resolve(strict=True)
    target = target_parent / requested_target.name
    _assert_new_or_empty(target)

    opened_archive, members, manifest, manifest_sha256 = _validated_archive(archive_path)
    entries = _validate_manifest(manifest)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.restore-", dir=target_parent))
    try:
        for entry in entries:
            relative = _safe_relative_path(entry["path"])
            destination = stage.joinpath(*relative.parts)
            resolved_parent = destination.parent.resolve(strict=False)
            try:
                resolved_parent.relative_to(stage.resolve())
            except ValueError as exc:
                raise BackupRestoreError(f"restore path escapes destination: {entry['path']}") from exc
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            stream = opened_archive.extractfile(members[entry["path"]])
            if stream is None:
                raise BackupRestoreError(f"cannot read {entry['path']} from archive")
            digest = hashlib.sha256()
            remaining = entry["size"]
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(destination, flags, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise BackupRestoreError(f"truncated payload: {entry['path']}")
                        output.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if stream.read(1):
                        raise BackupRestoreError(f"oversized payload: {entry['path']}")
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
            if digest.hexdigest() != entry["sha256"]:
                raise BackupRestoreError(f"payload SHA-256 mismatch: {entry['path']}")

        sqlite_checked: list[str] = []
        for entry in entries:
            restored = stage.joinpath(*PurePosixPath(entry["path"]).parts)
            if entry["kind"] == "sqlite" or _is_sqlite_candidate(restored):
                _sqlite_quick_check(restored)
                sqlite_checked.append(entry["path"])

        _assert_new_or_empty(target)
        if _lexists(target):
            target.rmdir()
        os.replace(stage, target)
        return {
            "file_count": len(entries),
            "manifest_sha256": manifest_sha256,
            "operation": "restore",
            "restore_dir": str(target),
            "sqlite_checked": sqlite_checked,
            "status": "ok",
        }
    finally:
        opened_archive.close()
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)


def drill_backup(
    data_dir: str | os.PathLike[str] | None = None,
    output: str | os.PathLike[str] | None = None,
    archive: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    if archive is not None and (data_dir is not None or output is not None):
        raise BackupRestoreError("drill accepts either --archive or --data-dir with --output")
    backup_result: dict[str, Any] | None = None
    if archive is None:
        if data_dir is None or output is None:
            raise BackupRestoreError("drill with --data-dir requires explicit --output")
        backup_result = create_backup(data_dir, output)
        archive_path = Path(output)
    else:
        archive_path = Path(archive)
    with tempfile.TemporaryDirectory(prefix="hermes-restore-drill-") as temporary:
        restore_path = Path(temporary) / "restored-data"
        restore_result = restore_backup(archive_path, restore_path)
        return {
            "archive": str(archive_path),
            "backup": backup_result,
            "file_count": restore_result["file_count"],
            "manifest_sha256": restore_result["manifest_sha256"],
            "operation": "drill",
            "sqlite_checked": restore_result["sqlite_checked"],
            "status": "ok",
            "temporary_restore_removed": True,
        }


def _parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(description="Safe Hermes data backup and restore drill")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="create a signed backup archive")
    backup_parser.add_argument("--data-dir", required=True)
    backup_parser.add_argument("--output", required=True)

    restore_parser = subparsers.add_parser("restore", help="restore into a new or empty directory")
    restore_parser.add_argument("--archive", required=True)
    restore_parser.add_argument("--restore-dir", required=True)

    drill_parser = subparsers.add_parser("drill", help="restore and verify in an ephemeral directory")
    drill_source = drill_parser.add_mutually_exclusive_group(required=True)
    drill_source.add_argument("--data-dir")
    drill_source.add_argument("--archive")
    drill_parser.add_argument("--output")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        if args.command == "backup":
            result = create_backup(args.data_dir, args.output)
        elif args.command == "restore":
            result = restore_backup(args.archive, args.restore_dir)
        else:
            if args.data_dir and not args.output:
                raise BackupRestoreError("drill with --data-dir requires explicit --output")
            if args.archive and args.output:
                raise BackupRestoreError("--output is only valid with drill --data-dir")
            result = drill_backup(data_dir=args.data_dir, output=args.output, archive=args.archive)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except BackupRestoreError as exc:
        print(
            json.dumps(
                {"error": str(exc), "operation": getattr(locals().get("args"), "command", None), "status": "error"},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    except Exception as exc:  # pragma: no cover - final fail-closed CLI boundary
        print(json.dumps({"error": str(exc), "status": "error"}, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
