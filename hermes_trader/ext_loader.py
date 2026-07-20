"""子项目扩展点 —— 自动发现并加载 clients/<id>/ext/*.py（off-by-default，母项目通用机制）。

设计动机：客户专属功能（如 wudi 的「解套诊断」）的代码不应进入共享的 hermes_trader/、frontend/，
否则会污染全体客户、违反母项目隔离铁律。本加载器让每个客户都能把自己的扩展放在 clients/<id>/ext/，
母项目只提供"发现 + 挂载"的通用一次性机制——没有 ext/ 目录就什么都不做，任何其它客户零影响。

扩展模块约定（全可选，鸭子类型）：
  - router: fastapi.APIRouter   —— 自带 prefix，会被 app.include_router 挂载（后端接口）。
  - EXT_VIEWS: list[dict]        —— 前端视图元数据，每项 {path,label,icon,group,module}；
                                    经 GET /api/ext/views 暴露给 app.js，启动时动态 import 注入导航。
  - EXT_JOBS: list[dict]         —— 定时任务，每项 {id, interval_min, func(async,收 engine),
                                    trading_hours_only(默认True)}；由 scheduler.register_ext_jobs 注册，
                                    带交易日/时段守卫 + 异常自吞（通用扩展点，任意客户可加后台任务）。
前端静态资源（视图 js）放 clients/<id>/ext/web/，由 main.py 挂到 /ext/。视图 js 用绝对路径
import（如 '/js/api.js'），因为它的服务路径 /ext/ 与母项目 /js/ 不同，相对路径会解析错。

容错原则同预热器：任何单个扩展加载失败都被吞掉 + 日志告警，绝不阻断主服务启动（扩展是增量层）。
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

log = logging.getLogger("hermes.ext")

_LIFECYCLES = {"visible", "hidden", "retired", "redirect"}


def apply_view_lifecycle(views: list[dict], ui: dict | None = None) -> list[dict]:
    """在所有扩展声明都收集完后，一次性应用可见性与生命周期。

    ``hidden`` 仅从导航隐藏，模块仍注册，因而旧直链与组合页签仍可使用；
    ``retired``/``redirect`` 不再注册视图模块。这个二阶段步骤不依赖前端
    ``/status`` 与 ``/ext/views`` 谁先返回，消除启动时的顺序漂移。
    """
    ui = ui if isinstance(ui, dict) else {}
    hidden = {str(x).strip() for x in (ui.get("nav_hidden") or []) if str(x).strip()}
    registry: list[dict] = []
    seen: dict[str, dict] = {}

    for raw in views or []:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "").strip()
        if not path:
            log.warning("扩展视图缺少 path，已忽略: %s", raw.get("source") or "unknown")
            continue
        if path in seen:
            winner = seen[path]
            log.warning(
                "扩展视图 path 重复，按完整发现后的稳定顺序保留首个: %s (%s > %s)",
                path, winner.get("source", "unknown"), raw.get("source", "unknown"),
            )
            winner.setdefault("conflicts", []).append(str(raw.get("source") or "unknown"))
            continue

        row = dict(raw)
        declared = str(row.get("lifecycle") or row.get("status") or "visible").strip().lower()
        if row.get("redirect_to"):
            declared = "redirect"
        if declared not in _LIFECYCLES:
            row["lifecycle_reason"] = f"无效生命周期: {declared}"
            declared = "retired"
        if declared == "redirect" and not str(row.get("redirect_to") or "").strip():
            row["lifecycle_reason"] = "redirect 缺少 redirect_to"
            declared = "retired"
        if declared == "visible" and path in hidden:
            declared = "hidden"
            row["lifecycle_reason"] = "config.ui.nav_hidden"

        row.pop("status", None)
        row["path"] = path
        row["lifecycle"] = declared
        row["visible"] = declared == "visible"
        row["navigation"] = declared == "visible"
        row["registered"] = declared in {"visible", "hidden"}
        seen[path] = row
        registry.append(row)
    return registry


def discover(config) -> dict:
    """扫描 config 所在客户目录下的 ext/*.py。返回 {routers, views, web_dir}。

    off-by-default：无 ext/ 目录 → 返回全空，调用方据此什么都不挂。
    """
    result: dict = {"routers": [], "views": [], "web_dir": None, "jobs": []}
    raw_views: list[dict] = []
    ext_dir = Path(config.config_path).parent / "ext"
    if not ext_dir.is_dir():
        return result

    web = ext_dir / "web"
    if web.is_dir():
        result["web_dir"] = web

    for py in sorted(ext_dir.glob("*.py")):
        if py.name.startswith("_"):
            continue
        try:
            mod_name = f"hermes_ext_{config.client_id}_{py.stem}"
            spec = importlib.util.spec_from_file_location(mod_name, py)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001 — 单个扩展失败不致命，跳过即可
            log.exception("加载子项目扩展失败（跳过，不影响主服务）: %s", py)
            continue

        router = getattr(mod, "router", None)
        if router is not None:
            result["routers"].append(router)
        views = getattr(mod, "EXT_VIEWS", None)
        if isinstance(views, list):
            for view in views:
                if isinstance(view, dict):
                    row = dict(view)
                    row.setdefault("source", py.stem)
                    raw_views.append(row)
        jobs = getattr(mod, "EXT_JOBS", None)
        if isinstance(jobs, list):
            result["jobs"].extend(jobs)
        log.info(
            "已加载子项目扩展 %s（router=%s, views=%d, jobs=%d）",
            py.name, router is not None, len(views or []), len(jobs or []),
        )
    # 只返回完整发现结果；main.py 先把所有 API router 挂载到 FastAPI，
    # 再调 apply_view_lifecycle 一次性应用视图路由状态，实现严格的两阶段顺序。
    result["views"] = raw_views
    return result
