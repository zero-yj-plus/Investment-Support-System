"""Hermes Trader 启动入口。

用法：
  python -m hermes_trader --config clients/local/config.yaml
  python -m hermes_trader --config clients/cloud/config.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from . import __version__
from .agent.hermes_client import HermesClient
from .analysis.engine import Engine
from .api.routes import router
from .config import Config
from .feishu.push import FeishuPusher
from .jobs.scheduler import build_scheduler
from .jobs.mainline_sampler import register as register_mainline_sampler
from .jobs.news_event_observer import register as register_news_event_observer
from .jobs.official_longhu_collector import register as register_official_longhu_collector
from .jobs.simulation_matcher import SimulationMatcher, register as register_simulation_matcher
from .knowledge.store import Store
from .research_runs import ResearchRunManager
from .simulation import SimulationStore
from .data.limitup_tracker import LimitUpTracker

log = logging.getLogger("hermes")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VUE_FRONTEND_DIR = PROJECT_ROOT / "frontend-vue" / "dist"
LEGACY_FRONTEND_DIR = PROJECT_ROOT / "frontend"
_FINGERPRINTED_ASSET = re.compile(r"(?:^|[._-])[0-9a-f]{8,}(?:[._-]|$)", re.IGNORECASE)
_STATIC_PREFIXES = (
    "/assets/", "/ext/", "/css/", "/js/", "/vendor/",
    "/legacy/css/", "/legacy/js/", "/legacy/vendor/",
)
_STATIC_SUFFIXES = (
    ".css", ".js", ".mjs", ".woff", ".woff2", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico",
)
_HTML_CACHE_CONTROL = "no-cache, max-age=0, must-revalidate"
_VERSIONED_CACHE_CONTROL = "public, max-age=31536000, immutable"
_SHORT_STATIC_CACHE_CONTROL = "public, max-age=60, must-revalidate"


def frontend_cache_control(path: str, asset_version: str = "") -> str | None:
    """Return browser caching policy for frontend assets only.

    HTML must revalidate on every navigation.  Assets explicitly versioned with
    the running product version (or carrying a content fingerprint in the file
    name) are immutable.  Legacy/unversioned modules get a short cache so a
    release cannot leave clients on an old module graph for long.
    """
    normalized = str(path or "")
    if normalized in {"/", "/legacy", "/legacy/"} or normalized.endswith(".html"):
        return _HTML_CACHE_CONTROL
    if not normalized.startswith(_STATIC_PREFIXES) or not normalized.lower().endswith(_STATIC_SUFFIXES):
        return None
    # Vite 的 /assets/ 文件名均带内容哈希，但哈希字符集不保证只是十六进制。
    if normalized.startswith("/assets/"):
        return _VERSIONED_CACHE_CONTROL
    filename = normalized.rsplit("/", 1)[-1]
    if asset_version == __version__ or _FINGERPRINTED_ASSET.search(filename):
        return _VERSIONED_CACHE_CONTROL
    return _SHORT_STATIC_CACHE_CONTROL


def mount_frontends(app: FastAPI, config, *, project_root: Path = PROJECT_ROOT) -> None:
    """挂载 Vue 正式入口，以及按配置开启的旧版对照入口。"""
    vue_dir = project_root / "frontend-vue" / "dist"
    legacy_dir = project_root / "frontend"
    vue_index = vue_dir / "index.html"
    if not vue_index.is_file():
        raise RuntimeError(
            f"未找到 Vue 构建产物 {vue_index}；请先在 frontend-vue 运行 npm run build"
        )

    # 更具体的 /legacy/ 必须先于根静态 catch-all 注册。
    if bool(getattr(config, "legacy_enabled", False)):
        if not (legacy_dir / "index.html").is_file():
            raise RuntimeError(f"已启用旧版界面，但未找到 {legacy_dir / 'index.html'}")
        app.mount(
            "/legacy",
            StaticFiles(directory=str(legacy_dir), html=True),
            name="legacy_frontend",
        )
        # 客户扩展模块的既有契约允许绝对 import('/js/api.js')。这些兼容资源
        # 仅随 legacy 一起开放，默认生产实例不会暴露旧版模块图。
        for prefix in ("js", "css", "vendor"):
            asset_dir = legacy_dir / prefix
            if asset_dir.is_dir():
                app.mount(
                    f"/{prefix}",
                    StaticFiles(directory=str(asset_dir)),
                    name=f"legacy_{prefix}",
                )
        log.warning("旧版对照入口已启用：/legacy/（修改配置后需重启才能生效）")

    app.mount(
        "/",
        StaticFiles(directory=str(vue_dir), html=True),
        name="vue_frontend",
    )


def create_app(config_path: str) -> FastAPI:
    config = Config(config_path)
    store = Store(config.data_dir)
    interrupted_jobs = store.reconcile_interrupted_jobs()
    if interrupted_jobs:
        log.warning("启动期收口上一进程未完成任务: %s", ", ".join(interrupted_jobs))
    # 独立财联社电报的 last-good 快照按客户 data_dir 隔离。
    from .data import news as news_data  # noqa: PLC0415
    news_data.init(config.data_dir)
    from .data import official_longhu_source  # noqa: PLC0415
    official_longhu_source.init(config.data_dir)
    from .data import mainforce_source  # noqa: PLC0415
    mainforce_source.init(config.data_dir)  # 东财龙虎榜快照目录（最近完整快照回退）
    # Phase 6 模拟执行：每客户独立 SQLite/WAL 账本；只模拟，不装配任何券商接口。
    simulation_store = SimulationStore(config.data_dir / "simulation.sqlite3")
    try:
        from .analysis.recommend import recover_pending_recommendations  # noqa: PLC0415

        recovery = recover_pending_recommendations(store)
        if recovery.get("recovered") or recovery.get("failed"):
            log.info("启动期推荐事务恢复: %s", recovery)
        store.recommendation_recovery = recovery
    except Exception:  # noqa: BLE001 — 不跳过事务；保留 intent 供下次恢复
        log.exception("启动期推荐事务恢复失败")
        store.recommendation_recovery = {"recovered": 0, "failed": ["startup_recovery_failed"]}
    # 统一行情证据网关：证据库按客户 data_dir 隔离，源故障时
    # 不阻断产品启动，但 API/UI 会如实显示 unavailable，绝不伪造行情证据。
    market_gateway = None
    try:
        from .data.runtime_gateway import create_runtime_gateway  # noqa: PLC0415

        market_gateway = create_runtime_gateway(config.data_dir)
        log.info("统一行情证据网关已启用：客户 TDX API 主选 + 腾讯字段补齐/备用")
    except Exception:
        log.exception("统一行情证据网关初始化失败（产品按明示降级继续启动）")
    # 资金持续追踪引擎：仅【按客户开启】时初始化 SQLite 时序库（每客户独立 data_dir）。
    # 未开启该能力的实例不建库、不采集；资金 API/MCP 工具优雅返回空。
    if config.capital_tracking_enabled:
        from .data import capital_tracker  # noqa: PLC0415
        capital_tracker.init(config.data_dir)
    # 新闻事件观察闭环按客户开启：事件与观察分离、只追加，页面/API 只读。
    news_observation_cfg = config.section("news_event_observation")
    if news_observation_cfg.get("enabled", False):
        from .data import news_observation  # noqa: PLC0415

        news_observation.init(config.data_dir)
    # 客户专属「高手蒸馏」persona 注入：仅当 config 配了 persona.masters 时，把蒸馏规则追加进
    # system prompt（off-by-default：未配置时 system prompt 一字不变）。
    # 母项目通用能力、按客户 config 开关——与 capital_tracking 同款隔离范式。
    if getattr(config, "persona_extra", ""):
        from .agent import prompts  # noqa: PLC0415
        prompts.set_persona_extra(config.persona_extra)
        log.info("已注入客户专属高手蒸馏 persona（%d 字）", len(config.persona_extra))
    if getattr(config, "output_directive", ""):
        from .agent import prompts  # noqa: PLC0415
        prompts.set_output_directive(config.output_directive)
        log.info("已注入客户专属输出准则（%d 字）", len(config.output_directive))
    hermes = HermesClient(config)
    # 推送传输层按 config.push_via 装配：'gateway'=交给本机 Hermes 网关（自动识别已连的
    # 微信/企业微信/飞书等，连什么推什么）；其余=直发飞书 webhook。两者同接口，下游无需区分。
    if config.push_via == "gateway":
        from .notify import GatewaySender  # noqa: PLC0415
        notifier = GatewaySender(config)
        log.info("推送传输层：Hermes 网关（hermes send，自动识别已连平台）")
    else:
        notifier = FeishuPusher(config)
    engine = Engine(config, store, hermes, notifier)
    research_runs = ResearchRunManager(config.data_dir)
    limitup_tracker = LimitUpTracker(config.data_dir)
    # 分析层只消费统一网关契约；不在各页再各自直连行情源。
    engine.market_gateway = market_gateway
    # 技术选股只读客户隔离的本地缓存；网络采集由盘后任务或显式刷新触发。
    from .analysis import technical_screener  # noqa: PLC0415
    technical_screener.init(config.data_dir)
    scheduler = build_scheduler(config, engine)
    simulation_matcher = SimulationMatcher(simulation_store)
    register_simulation_matcher(scheduler, simulation_matcher)
    try:
        register_mainline_sampler(scheduler, config)
    except Exception:  # noqa: BLE001 -- 采样任务不得阻断服务启动
        log.exception("注册主线时间轴任务失败（不影响当前截面分析）")
    try:
        register_news_event_observer(scheduler, config, store)
    except Exception:  # noqa: BLE001 -- 事件观察任务不得阻断服务启动
        log.exception("注册新闻事件观察任务失败（不影响新闻原文与实体映射）")
    try:
        register_official_longhu_collector(scheduler, config, store)
    except Exception:  # noqa: BLE001 -- 官网盘后采集不得阻断服务启动
        log.exception("注册官方龙虎榜采集失败（不影响既有缓存与主服务）")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler.start()
        log.info(
            "Hermes Trader v%s 已启动 | 客户: %s(%s) | 数据目录: %s | Hermes: %s",
            __version__, config.client_name, config.client_id,
            config.data_dir, config.hermes_base_url,
        )
        # 后台预热器：读回启动快照(last-good) + 预热一次 + 注册 15s 周期任务（仅交易时段主动预热）。
        # 全程异常自吞——预热是纯优化层，任何失败都不得阻断主服务启动。
        try:
            from .jobs.prewarmer import boot as prewarm_boot  # noqa: PLC0415

            await prewarm_boot(scheduler, config, store)
        except Exception:
            log.exception("后台预热器启动失败（不影响主服务）")
        # 可选：飞书互动机器人（配置了 app_id/app_secret 才会启动）
        try:
            from .feishu.bot import FeishuBot  # noqa: PLC0415

            bot = FeishuBot(config, engine, asyncio.get_running_loop())
            if bot.configured:
                bot.start()
        except Exception:
            log.exception("飞书互动机器人启动失败（不影响主服务）")
        yield
        await research_runs.shutdown()
        scheduler.shutdown(wait=False)

    app = FastAPI(title="Hermes 交易陪跑", version=__version__, lifespan=lifespan)
    # SSH 隧道会直连 Uvicorn、绕过公网 Nginx；应用层也必须压缩大 JSON/静态文件。
    # Starlette 会跳过 text/event-stream，不会缓冲问股 SSE。
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    app.state.config = config
    app.state.store = store
    app.state.simulation_store = simulation_store
    app.state.simulation_matcher = simulation_matcher
    app.state.hermes = hermes
    app.state.agent_runs = hermes.run_store
    app.state.feishu = notifier   # 活动推送器（飞书 webhook 或 Hermes 网关，同接口鸭子类型）
    app.state.engine = engine
    app.state.research_runs = research_runs
    app.state.limitup_tracker = limitup_tracker
    app.state.limitup_backfill_jobs = {"active": None, "jobs": {}}
    app.state.scheduler = scheduler
    app.state.market_gateway = market_gateway
    app.state.evidence_store = market_gateway.store if market_gateway is not None else None
    # 核心完整性闸（隔离铁律的技术校验）：客户机上 hermes_trader/+frontend/ 若偏离母版本(私改/删/增)
    # → 启动期告警 + /api/status 暴露，便于远程发现"客户私改了核心"，提示用 upgrade_core.sh 对齐回正。
    # 开发态(仓库无 core.sha256 清单)不校验、不误报。
    try:
        from .integrity import verify_core  # noqa: PLC0415
        ci = verify_core(PROJECT_ROOT)
        app.state.core_integrity = ci
        if ci.get("manifest_present") and not ci.get("ok"):
            log.warning(
                "⚠ 核心完整性：已偏离母版本 v%s —— 私改 %d / 丢失 %d / 私增 %d 个核心文件。"
                "客户不应直接改 hermes_trader/frontend，请用 clients/upgrade_core.sh 对齐母版本。",
                ci.get("version"), len(ci.get("drift", [])), len(ci.get("removed", [])), len(ci.get("added", [])),
            )
    except Exception:
        app.state.core_integrity = {"ok": True, "manifest_present": False, "error": "校验异常"}
        log.exception("核心完整性校验失败（不影响主服务）")
    app.include_router(router)

    # 子项目扩展点（off-by-default）：自动发现 clients/<id>/ext/*.py，挂载其 router + 暴露视图给前端。
    # 没有 ext/ 目录就什么都不做；任何客户都能在自己目录加扩展、不改母项目共享代码（隔离铁律）。
    # 必须在下方 '/' 静态挂载之前完成，否则 catch-all 会吞掉 /ext 与 /api/ext 路由。
    from .ext_loader import apply_view_lifecycle as _apply_view_lifecycle  # noqa: PLC0415
    from .ext_loader import discover as _discover_ext  # noqa: PLC0415
    _ext = _discover_ext(config)
    # 阶段 1：无论导航是否可见，先完整注册所有扩展 API router。
    for _r in _ext["routers"]:
        app.include_router(_r)
    # 阶段 2：所有扩展都被发现/注册后，再统一应用视图生命周期。
    # hidden 只移出导航，仍注册模块供旧直链/组合页签使用。
    _ui = config.section("ui") if hasattr(config, "section") else {}
    app.state.ext_views = _apply_view_lifecycle(_ext["views"], _ui)
    if _ext["web_dir"] is not None:
        app.mount("/ext", StaticFiles(directory=str(_ext["web_dir"])), name="ext")
    # ext 定时任务（通用扩展点）：scheduler 此刻已建好（上方）且尚未 start，可安全注册。
    if _ext.get("jobs"):
        from .jobs.scheduler import register_ext_jobs  # noqa: PLC0415
        register_ext_jobs(scheduler, config, engine, _ext["jobs"])
    if _ext["routers"] or _ext["views"] or _ext.get("jobs"):
        log.info("子项目扩展已接入：%d 路由 / %d 视图 / %d 任务",
                 len(_ext["routers"]), len(_ext["views"]), len(_ext.get("jobs", [])))

    # HTML 每次重校验；带当前版本/内容指纹的静态资源长缓存；
    # 未指纹模块只短缓存，避免发布后长时间混用新旧模块图。
    @app.middleware("http")
    async def _frontend_cache_policy(request, call_next):
        resp = await call_next(request)
        if resp.status_code in {200, 304}:
            policy = frontend_cache_control(
                request.url.path,
                request.query_params.get("v", ""),
            )
            if policy:
                resp.headers["Cache-Control"] = policy
        return resp

    mount_frontends(app, config)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes 交易陪跑系统")
    parser.add_argument("--config", required=True, help="客户配置文件路径（clients/*/config.yaml）")
    parser.add_argument("--host", default=None, help="覆盖监听地址")
    parser.add_argument("--port", type=int, default=None, help="覆盖端口")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    app = create_app(args.config)
    config: Config = app.state.config
    uvicorn.run(
        app,
        host=args.host or config.host,
        port=args.port or config.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
