"""核心完整性校验 —— 母项目隔离铁律的「技术闸」。

定位：部署到客户机上的 `hermes_trader/` + `frontend/` 是**共享产品真源**，客户的一切定制只许落在
`clients/<id>/`(config / ext / data)。本模块给"客户不许改核心"加一道技术校验：
  · 打升级包时(`clients/package_core.sh`)用 `write_manifest()` 把全核心文件的 sha256 写进 `core.sha256`；
  · 客户机启动时(`main.py`)与升级时(`upgrade_core.sh`)用 `verify_core()` 重算并比对 →
    一旦核心被私改/删/增，立即报「已偏离母版本 vN」，便于远程发现并用 upgrade_core.sh 对齐回正。

设计：哈希逻辑只此一处实现，打包端与校验端共用 → 跨平台(mac 打包 / Linux 部署)结果一致。
开发态(仓库里没有 `core.sha256`)→ `verify_core` 返回 manifest_present=False、ok=True，绝不误报。
清单文件 `core.sha256` 放在**部署根**(与 hermes_trader/ 同级)，不在被哈希的核心目录内 → 无自引用。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import __version__

# 纳入完整性校验的核心源文件后缀（产品逻辑/界面）。data/缓存/字节码/配置不算核心。
_EXTS = {".py", ".js", ".css", ".html", ".mjs"}
_CORE_DIRS = ("hermes_trader", "frontend")
MANIFEST_NAME = "core.sha256"


def _iter_core_files(root: Path):
    """部署根下 hermes_trader/ + frontend/ 里的核心源文件相对路径（POSIX、排序、去 __pycache__）。"""
    for d in _CORE_DIRS:
        base = root / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if "__pycache__" in p.parts:
                continue
            if p.suffix.lower() not in _EXTS:
                continue
            yield p.relative_to(root).as_posix()


def _hash_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def compute_manifest(root) -> dict:
    """{相对路径: sha256} —— 当前 hermes_trader/+frontend/ 的全核心文件指纹。"""
    root = Path(root)
    return {rel: _hash_file(root / rel) for rel in _iter_core_files(root)}


def write_manifest(root, version: str | None = None) -> str:
    """把核心指纹清单写到 <root>/core.sha256（打升级包时调用）。返回清单路径。"""
    root = Path(root)
    data = {"version": version or __version__, "files": compute_manifest(root)}
    out = root / MANIFEST_NAME
    out.write_text(json.dumps(data, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    return str(out)


def core_fingerprint(root) -> str:
    """当前核心文件集的 16 位短指纹——任何核心文件变化（含纯前端热部署、无需重启）指纹即变。
    /api/status 暴露给前端做「版本感知自动刷新」：客户开着的旧页面在 60s 轮询内发现指纹变化
    → 自动重载新版。发版后客户零手动刷新（治"看到旧版/白屏"整类事故）。"""
    joined = "\n".join(f"{k}:{v}" for k, v in sorted(compute_manifest(Path(root)).items()))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def verify_core(root) -> dict:
    """比对当前核心与 <root>/core.sha256 清单。
    返回 {ok, manifest_present, version, running_version, drift[], removed[], added[]}。
    - drift   = 清单里有、但内容被改的文件（客户私改了核心）
    - removed = 清单里有、但部署里没了的文件
    - added   = 部署里有、但清单里没有的文件（客户私加了核心文件）
    无清单(开发态/未经 package_core 打包) → manifest_present=False, ok=True，不误报。"""
    root = Path(root)
    mf = root / MANIFEST_NAME
    if not mf.exists():
        return {"ok": True, "manifest_present": False, "version": __version__,
                "note": "无核心清单(开发态/未经 package_core 打包)，跳过完整性校验"}
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        recorded = data.get("files", {}) or {}
        recorded_ver = data.get("version")
    except Exception:
        return {"ok": False, "manifest_present": True, "version": None, "error": "核心清单 core.sha256 损坏或不可读"}
    current = compute_manifest(root)
    drift = sorted(f for f in recorded if f in current and current[f] != recorded[f])
    removed = sorted(f for f in recorded if f not in current)
    added = sorted(f for f in current if f not in recorded)
    return {
        "ok": not (drift or removed or added),
        "manifest_present": True,
        "version": recorded_ver,
        "running_version": __version__,
        "drift": drift,
        "removed": removed,
        "added": added,
    }


def summary(verify_result: dict | None) -> dict:
    """给 /api/status 用的精简摘要（不回传完整文件列表，只给计数与状态）。"""
    r = verify_result or {}
    return {
        "version": r.get("version") or r.get("running_version") or __version__,
        "manifest_present": bool(r.get("manifest_present")),
        "ok": bool(r.get("ok", True)),
        "drift_count": len(r.get("drift", []) or []),
        "removed_count": len(r.get("removed", []) or []),
        "added_count": len(r.get("added", []) or []),
    }
