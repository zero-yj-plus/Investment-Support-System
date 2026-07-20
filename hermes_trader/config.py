"""配置加载：YAML 基础配置 + 环境变量展开 + data/settings.json 运行时覆盖。

优先级（高 → 低）：settings.json 覆盖 > 环境变量 ${VAR} 展开 > config.yaml 字面值。
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

import yaml

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SETTINGS_LOCK = threading.Lock()

DEFAULT_SCHEDULE = {
    "morning": "08:50",
    "midday": "14:45",
    "tail": "14:52",
    "close": "15:30",
    "evening": "19:30",
    "archive": "22:00",
}

_DEPLOYMENT_PROFILES = frozenset({
    "auto", "cn_home", "cn_server", "overseas_server",
})


def _resolve_deployment_profile(configured: str, *, eastmoney_direct: bool) -> str:
    """Resolve data routing without geolocating or guessing from a public IP.

    ``auto`` only uses deployment facts the operator already configured. A
    relay means the application is outside the ideal domestic route; explicit
    Eastmoney direct access means a domestic server route. Otherwise the
    conservative low-concurrency home profile is used.
    """
    if configured != "auto":
        return configured
    if os.environ.get("DATA_RELAY_PROXY") or os.environ.get("DATA_RELAY_API"):
        return "overseas_server"
    if eastmoney_direct:
        return "cn_server"
    return "cn_home"


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _load_dotenv(path: Path) -> None:
    """加载配置文件同目录的 .env（不覆盖已有环境变量，无第三方依赖）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value:
            os.environ.setdefault(key, value)


class Config:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path).resolve()
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
        # 客户配置和同目录 .env 可能包含模型、推送及数据源凭据。Git 忽略只能
        # 防误提交，不能阻止同机其它用户读取；启动时尽力收紧到仅属主可读写。
        # 只读 secret mount / 非属主运行时 chmod 可能被拒绝，此时保持可启动，
        # 由生产门禁继续报告权限问题。
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass
        env_path = self.config_path.parent / ".env"
        if env_path.exists():
            try:
                os.chmod(env_path, 0o600)
            except OSError:
                pass
        _load_dotenv(env_path)
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        self._raw: dict = _expand_env(raw)

        client = self._raw.get("client", {})
        self.client_name: str = client.get("name", "默认客户")
        self.client_id: str = client.get("id", "local")
        # 品牌定制（按客户配置）：界面左上角主标题与副标题。不写则用产品默认。
        # 其余「需要名号的输出」（状态栏/浏览器标题/报告与飞书标题）统一用 client_name。
        self.client_brand: str = client.get("brand", "Hermes 交易陪跑")
        self.client_brand_sub: str = client.get("brand_sub", "TRADING COPILOT")
        # 客户专属「高手蒸馏」persona：persona.masters 文本会在启动期追加进 system prompt
        # （off-by-default：不配则为空、system prompt 一字不变）。母项目通用能力、按客户 config 开关；
        # 规则文本是数据（来自对指定公众号/高手的蒸馏），不是代码——客户差异只走 config，不改共享逻辑。
        persona = self._raw.get("persona", {})
        persona = persona if isinstance(persona, dict) else {}
        self.persona_extra: str = str(persona.get("masters", "")).strip()
        # 本客户「输出准则」：研报/问股/诊断结论形态硬约束（强推荐+价格+动作+不追涨停）；
        # 顶层 output_directive 文本，off-by-default——未配置的客户行为一字不变。
        self.output_directive: str = str(self._raw.get("output_directive", "")).strip()

        # 每日复盘自动汇总进知识库（M12）：会多一次 AI 调用 + 自动写知识 = 行为变化，
        # 故默认关、仅按客户 config 开（off-by-default：未配置的客户行为一字不变）。
        knowledge_cfg = self._raw.get("knowledge", {})
        knowledge_cfg = knowledge_cfg if isinstance(knowledge_cfg, dict) else {}
        self.knowledge_auto_summarize: bool = bool(knowledge_cfg.get("auto_summarize", False))

        server = self._raw.get("server", {})
        self.host: str = server.get("host", "0.0.0.0")
        self.port: int = int(server.get("port", 8868))
        data_dir = server.get("data_dir", "./data")
        # data_dir 相对于配置文件所在目录解析，保证两个客户目录互不串数据
        self.data_dir: Path = (self.config_path.parent / data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass
        # 数据适配层不反向 import Config，通过环境变量获取客户隔离的持久缓存目录。
        # 只放代码表/点时快照等非密钥数据，不影响现有 config 安全模型。
        os.environ["HERMES_DATA_DIR"] = str(self.data_dir)

        hermes = self._raw.get("hermes", {})
        self._hermes_base_url: str = hermes.get("base_url", "http://127.0.0.1:8742/v1")
        self._hermes_model: str = hermes.get("model", "gpt-5.4-mini")
        configured_provider = str(hermes.get("provider", "")).strip()
        self._hermes_provider: str = configured_provider or (
            "hermes" if any(
                host in self._hermes_base_url
                for host in ("127.0.0.1", "localhost", "::1")
            ) else "openai-compatible"
        )
        # 结构化/低延迟场景（诊断卡/解套卡）用的快模型；未配置则回退主模型、行为不变。
        # 仅 x402 等多模型供应商可设（如 gpt-5.4-mini）；本机网关单模型勿设。
        self._hermes_fast_model: str = hermes.get("fast_model", "")
        self._hermes_api_key: str = hermes.get("api_key", "") or os.environ.get("HERMES_API_KEY", "")
        self.hermes_timeout: int = int(hermes.get("timeout", 300))
        # 模型采样参数可配（不写则用稳健默认）；报告/问答场景统一从这里取。
        self.hermes_temperature: float = float(hermes.get("temperature", 0.4))
        self.hermes_max_tokens: int = int(hermes.get("max_tokens", 8000))
        self.hermes_autodiscover_local_gateway: bool = bool(
            hermes.get("autodiscover_local_gateway", True)
        )
        # Agent 权限门禁：本机 Hermes API server 会在服务端创建可执行工具的
        # AIAgent，而请求体里的 tools=[] 并不会限制它。因此本产品默认要求
        # /capabilities + /toolsets 可证明服务端权限已收敛；未证明就拒绝发 prompt。
        agent_runtime = self._raw.get("agent_runtime", {})
        agent_runtime = agent_runtime if isinstance(agent_runtime, dict) else {}
        self.agent_require_restricted_toolsets: bool = bool(
            agent_runtime.get("require_restricted_toolsets", True)
        )
        allowed = agent_runtime.get("allowed_toolsets", [])
        self.agent_allowed_toolsets: list[str] = sorted({
            str(item).strip() for item in (allowed if isinstance(allowed, list) else [])
            if str(item).strip()
        })
        allowed_mcps = agent_runtime.get("allowed_mcp_servers", [])
        self.agent_allowed_mcp_servers: list[str] = sorted({
            str(item).strip()
            for item in (allowed_mcps if isinstance(allowed_mcps, list) else [])
            if str(item).strip()
        })
        self.agent_allow_tool_events: bool = bool(
            agent_runtime.get("allow_tool_events", False)
        )
        self.agent_capability_cache_sec: int = max(
            5, min(int(agent_runtime.get("capability_cache_sec", 20) or 20), 300)
        )
        profile_dir = str(agent_runtime.get("restricted_profile_dir", "")).strip()
        self.agent_restricted_profile_dir: Path | None = (
            (self.config_path.parent / profile_dir).resolve() if profile_dir else None
        )
        self.agent_require_profile_attestation: bool = bool(
            agent_runtime.get("require_profile_attestation", True)
        )
        direct_hosts = agent_runtime.get(
            "direct_model_hosts", ["api.deepseek.com", "api.x402.at"]
        )
        self.agent_direct_model_hosts: list[str] = sorted({
            str(item).strip().lower()
            for item in (direct_hosts if isinstance(direct_hosts, list) else [])
            if str(item).strip()
        })
        # 受限 profile 的 API_SERVER_KEY 只保存在该 profile/.env（0600、gitignore）。
        # 产品可以就地读取它连本机受限网关，无需把 key 复制到客户配置。
        if self.agent_restricted_profile_dir and not self._hermes_api_key:
            profile_env = self.agent_restricted_profile_dir / ".env"
            try:
                for line in profile_env.read_text(encoding="utf-8").splitlines():
                    key, sep, value = line.strip().partition("=")
                    if sep and key == "API_SERVER_KEY":
                        self._hermes_api_key = value.strip().strip("'\"")
                        break
            except OSError:
                pass
        # 可视化是 hermes agent 的 skill/mcp、与网关同机共生——本就不该手动配 key。
        # 本机网关(127.0.0.1)时自动从 ~/.hermes/.env 读 API server 的 host/port/key/model，
        # 网关凭证为准（config.yaml 里写错的 api_key 也不影响）。云端/relay 客户不触发。
        if self.hermes_autodiscover_local_gateway:
            self._autodiscover_local_gateway()

        feishu = self._raw.get("feishu", {})
        self._feishu_webhook: str = feishu.get("webhook", "")
        self._feishu_secret: str = feishu.get("secret", "")
        self.feishu_app_id: str = feishu.get("app_id", "")
        self.feishu_app_secret: str = feishu.get("app_secret", "")

        sched = self._raw.get("schedule", {})
        self.timezone: str = sched.get("timezone", "Asia/Shanghai")
        self.trading_days_only: bool = bool(sched.get("trading_days_only", True))
        self._schedule: dict[str, str] = {**DEFAULT_SCHEDULE}
        for key in DEFAULT_SCHEDULE:
            if key in sched:
                self._schedule[key] = str(sched[key])

        push = self._raw.get("push", {})
        self._push_enabled: bool = bool(push.get("enabled", True))
        # 推送传输层：'feishu'=直发飞书 webhook（跨云端也能用）；
        # 'gateway'=交给本机 Hermes 网关投递（hermes send，自动识别网关已连的微信/企业微信/飞书/
        # Telegram… 连什么推什么，仅本机部署可用）。默认 feishu 保持既有行为不变。
        self.push_via: str = str(push.get("via", "feishu")).strip().lower()
        gw = push.get("gateway", {}) if isinstance(push.get("gateway"), dict) else {}
        # 网关 CLI 解释器（默认 = Hermes Agent 自带 venv 里的 python）。安装位置随 OS 不同：
        #   Windows：%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python.exe
        #   Mac/Linux：~/.hermes/hermes-agent/venv/bin/python
        # 客户机若装在别处，在 config 的 push.gateway.cli 写绝对路径覆盖即可。
        # targets 是允许定时外发的平台。生产环境建议只填一个，例如
        # ["feishu"]；具体收件会话由客户在该平台执行 /sethome 决定。
        # 留空时仅接受唯一一个已设置 HOME_CHANNEL 的平台；若存在多个
        # 主频道则 fail closed，避免把同一报告同时群发到多个平台。
        if os.name == "nt":
            _hermes_root = os.path.join(
                os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"), "hermes"
            )
            _default_cli = os.path.join(_hermes_root, "hermes-agent", "venv", "Scripts", "python.exe")
        else:
            _default_cli = os.path.expanduser(os.path.join("~", ".hermes", "hermes-agent", "venv", "bin", "python"))
        self.push_gateway_cli: str = str(gw.get("cli", "")).strip() or _default_cli
        _t = gw.get("targets", [])
        self.push_gateway_targets: list[str] = [str(x).strip() for x in _t if str(x).strip()] \
            if isinstance(_t, list) else []
        # 容器产品无法直接执行宿主机 Hermes CLI 时，可调用宿主机上
        # 受认证、只允许 home-channel 的投递桥。token 必须来自 .env，
        # 不进入模板或状态接口。
        self.push_gateway_bridge_url: str = str(gw.get("bridge_url", "")).strip()
        self.push_gateway_bridge_token: str = (
            str(gw.get("bridge_token", "")).strip()
            # __init__ 尚未加载 settings overrides；这里只能读取已经由
            # config/profile .env 解析好的底层 key，不能调用动态 property。
            or (self._hermes_api_key if self.push_gateway_bridge_url else "")
        )

        # 注：通达信「问小达」MCP 已弃用（按次扣 AI 积分）。数据层改走
        # 客户自有 TDX 数据 API + 腾讯/乐咕/新浪/同花顺字段级主备。
        # TDX API 访问凭据由客户提供、只走 .env，不是 Tushare 付费积分链路。

        # 资金持续追踪：母项目能力，【按客户 config 开启】（每个客户想法不同，不强塞给所有人）。
        # 默认关闭——只有显式 capital_tracking.enabled: true 的客户(如 wudi)才定时采集落库。
        cap = self._raw.get("capital_tracking", {}) if isinstance(self._raw.get("capital_tracking"), dict) else {}
        self.capital_tracking_enabled: bool = bool(cap.get("enabled", False))
        self.capital_tracking_interval_min: int = int(cap.get("interval_min", 15) or 15)

        # 数据源策略：free_only 是当前唯一可运行的正式策略。这个开关用来让健康面板、
        # 日志和部署验收共用同一个事实源，不是只改配置即可恢复旧付费适配器的隐藏开关。
        # 若需回滚，必须整体恢复改造前代码与依赖，凭据只能走环境变量，不得写入配置或文档。
        ds = self._raw.get("data_source", {}) if isinstance(self._raw.get("data_source"), dict) else {}
        self.data_source_policy: str = str(ds.get("policy", "free_only")).strip().lower() or "free_only"
        if self.data_source_policy != "free_only":
            raise ValueError(
                f"不支持的数据源策略: {self.data_source_policy}；"
                "当前版本仅支持 free_only，旧链路须整体回滚代码与依赖"
            )
        os.environ["HERMES_DATA_SOURCE_POLICY"] = self.data_source_policy

        # 东财直连开关：本机东财公开端点可直达时可作免费备用源；不可达则自动走
        # 腾讯/同花顺/新浪独立源。注入 HERMES_EM_DIRECT 供 data/market.py 读取，
        # 避免 config↔data 循环导入（与 DATA_RELAY_API 同机制）。
        self.eastmoney_direct: bool = bool(ds.get("eastmoney_direct", False))
        if self.eastmoney_direct:
            os.environ["HERMES_EM_DIRECT"] = "1"
        else:
            # Config 是单一事实源：显式 false 必须清掉同进程中旧实例留下的环境值，
            # 否则配置页显示关闭，数据层仍会误走东财直连。
            os.environ.pop("HERMES_EM_DIRECT", None)

        # 部署地域只允许显式配置或按已存在的网络拓扑推断，不做公网 IP
        # geolocation。IP 归属、CDN 出口和家庭宽带 NAT 都可能误判，且启动
        # 期额外请求会制造新的单点故障。
        configured_profile = str(
            ds.get("deployment_profile", "auto")
        ).strip().lower() or "auto"
        if configured_profile not in _DEPLOYMENT_PROFILES:
            raise ValueError(
                f"不支持的部署地域策略: {configured_profile}；"
                "可选 auto/cn_home/cn_server/overseas_server"
            )
        self.deployment_profile_configured: str = configured_profile
        self.deployment_profile: str = _resolve_deployment_profile(
            configured_profile, eastmoney_direct=self.eastmoney_direct
        )
        os.environ["HERMES_DEPLOYMENT_PROFILE_CONFIGURED"] = configured_profile
        os.environ["HERMES_DEPLOYMENT_PROFILE"] = self.deployment_profile

        self._overrides: dict = {}
        self._load_overrides()

    def _autodiscover_local_gateway(self) -> None:
        """本机 Hermes Agent 网关凭证自动发现（可视化=网关的 skill/mcp，同机共生，免手动配 key）。

        网关是本机回环(127.0.0.1/localhost)时，从 ~/.hermes/.env 读 API server 的
        HOST/PORT/KEY/MODEL_NAME 并采用之——以网关为准（覆盖 config.yaml 里可能写错的 key/model，
        地址按 HOST/PORT 拼）。云端/relay 客户(base_url 指向 relay，非本机)不触发，仍用 config 的 key。
        读不到 ~/.hermes/.env 时静默跳过（退回 config.yaml 的值，行为不变）。
        """
        if not any(h in self._hermes_base_url for h in ("127.0.0.1", "localhost", "0.0.0.0")):
            return
        env_path = Path(os.path.expanduser("~/.hermes/.env"))
        if not env_path.exists():
            return
        g: dict[str, str] = {}
        try:
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip().startswith("API_SERVER_"):
                    g[key.strip()] = val.strip().strip("'\"")
        except OSError:
            return
        if g.get("API_SERVER_ENABLED", "true").lower() in ("false", "0", "no", "off"):
            return
        host = g.get("API_SERVER_HOST") or "127.0.0.1"
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"   # 网关 bind 全网卡时，客户端仍连回环
        self._hermes_base_url = f"http://{host}:{g.get('API_SERVER_PORT') or '8742'}/v1"
        if g.get("API_SERVER_KEY"):
            self._hermes_api_key = g["API_SERVER_KEY"]           # 网关 key 为准
        if g.get("API_SERVER_MODEL_NAME"):
            self._hermes_model = g["API_SERVER_MODEL_NAME"]     # 网关模型名为准

    # ---- settings.json 运行时覆盖 ----

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def _load_overrides(self) -> None:
        if self.settings_path.exists():
            try:
                self._overrides = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except Exception:
                self._overrides = {}

    def save_overrides(self, patch: dict) -> None:
        """合并写入运行时设置覆盖。

        敏感设置使用同目录临时文件 + fsync + replace 原子落盘，并强制 0600；
        进程内锁避免两个并发 PUT 互相覆盖或产生半截 JSON。
        """
        with _SETTINGS_LOCK:
            self._load_overrides()
            for key, value in patch.items():
                if key == "schedule" and isinstance(value, dict):
                    merged = {**self._overrides.get("schedule", {}), **value}
                    self._overrides["schedule"] = merged
                else:
                    self._overrides[key] = value
            self._write_overrides_locked()

    def delete_overrides(self, keys: set[str] | list[str] | tuple[str, ...]) -> None:
        """删除指定运行时覆盖并原子落盘。

        用于“删除自定义模型连接”：只移除 settings.json 中的
        endpoint/key/model，恢复客户 config.yaml 默认值，不修改源配置。
        """
        wanted = {str(key) for key in keys if str(key)}
        if not wanted:
            return
        with _SETTINGS_LOCK:
            self._load_overrides()
            for key in wanted:
                self._overrides.pop(key, None)
            self._write_overrides_locked()

    def _write_overrides_locked(self) -> None:
        """Persist ``_overrides`` while the process-wide settings lock is held."""
        payload = json.dumps(self._overrides, ensure_ascii=False, indent=2)
        tmp = self.settings_path.with_name(f".{self.settings_path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, self.settings_path)
            os.chmod(self.settings_path, 0o600)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _model_connection_records(self) -> dict[str, dict[str, Any]]:
        raw = self._overrides.get("model_connections")
        if not isinstance(raw, dict):
            return {}
        records: dict[str, dict[str, Any]] = {}
        for raw_id, raw_record in raw.items():
            connection_id = str(raw_id or "").strip()
            if not connection_id or not isinstance(raw_record, dict):
                continue
            records[connection_id] = dict(raw_record)
        return records

    def _active_model_connection(self) -> dict[str, Any] | None:
        connection_id = str(
            self._overrides.get("active_model_connection_id") or ""
        ).strip()
        record = self._model_connection_records().get(connection_id)
        if not record:
            return None
        if not str(record.get("base_url") or "").strip() or not str(
            record.get("model") or ""
        ).strip():
            return None
        return {**record, "id": connection_id}

    @property
    def active_model_connection_id(self) -> str | None:
        active = self._active_model_connection()
        return str(active["id"]) if active else None

    def model_connection_view(self, connection_id: str) -> dict[str, Any] | None:
        record = self._model_connection_records().get(str(connection_id or "").strip())
        if not record:
            return None
        return {
            "id": str(connection_id),
            "name": str(record.get("name") or ""),
            "provider": str(record.get("provider") or "custom"),
            "base_url": str(record.get("base_url") or ""),
            "model": str(record.get("model") or ""),
            "api_key_set": bool(record.get("api_key")),
            "active": str(connection_id) == self.active_model_connection_id,
        }

    def model_connections_view(self) -> list[dict[str, Any]]:
        return [
            view
            for connection_id in self._model_connection_records()
            if (view := self.model_connection_view(connection_id)) is not None
        ]

    def model_connection_api_key(self, connection_id: str) -> str:
        """Server-only secret lookup; API serializers must use model_connection_view."""
        record = self._model_connection_records().get(str(connection_id or "").strip())
        return str((record or {}).get("api_key") or "")

    def save_model_connection(
        self,
        connection_id: str,
        *,
        name: str,
        provider: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        activate: bool = True,
        require_existing: bool = False,
    ) -> dict[str, Any]:
        """Create/update one server-side model connection without exposing its key."""
        connection_id = str(connection_id or "").strip()
        if not connection_id:
            raise ValueError("connection_id is required")
        with _SETTINGS_LOCK:
            self._load_overrides()
            records = self._model_connection_records()
            existing = records.get(connection_id)
            if require_existing and existing is None:
                raise KeyError(connection_id)
            secret = (
                str(api_key)
                if api_key is not None
                else str((existing or {}).get("api_key") or "")
            )
            records[connection_id] = {
                "name": str(name),
                "provider": str(provider),
                "base_url": str(base_url),
                "model": str(model),
                "api_key": secret,
            }
            self._overrides["model_connections"] = records
            if activate or not self._overrides.get("active_model_connection_id"):
                self._overrides["active_model_connection_id"] = connection_id
            self._write_overrides_locked()
        view = self.model_connection_view(connection_id)
        if view is None:  # pragma: no cover - protected by validated write above
            raise RuntimeError("model connection was not persisted")
        return view

    def activate_model_connection(self, connection_id: str) -> dict[str, Any]:
        connection_id = str(connection_id or "").strip()
        with _SETTINGS_LOCK:
            self._load_overrides()
            if connection_id not in self._model_connection_records():
                raise KeyError(connection_id)
            self._overrides["active_model_connection_id"] = connection_id
            self._write_overrides_locked()
        view = self.model_connection_view(connection_id)
        if view is None:  # pragma: no cover
            raise RuntimeError("active model connection is unavailable")
        return view

    def delete_model_connection(self, connection_id: str) -> bool:
        connection_id = str(connection_id or "").strip()
        with _SETTINGS_LOCK:
            self._load_overrides()
            records = self._model_connection_records()
            if connection_id not in records:
                return False
            records.pop(connection_id, None)
            if records:
                self._overrides["model_connections"] = records
            else:
                self._overrides.pop("model_connections", None)
            if self._overrides.get("active_model_connection_id") == connection_id:
                self._overrides.pop("active_model_connection_id", None)
            self._write_overrides_locked()
        return True

    # ---- 可被覆盖的动态属性 ----

    @property
    def hermes_base_url(self) -> str:
        active = self._active_model_connection()
        if active:
            return str(active["base_url"])
        override = self._overrides.get("hermes_base_url")
        if override and getattr(self, "agent_require_profile_attestation", False):
            try:
                from urllib.parse import urlsplit

                parsed = urlsplit(str(override))
                configured = urlsplit(self._hermes_base_url)
                if (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}:
                    parsed_port = parsed.port or (443 if parsed.scheme == "https" else 80)
                    configured_port = configured.port or (443 if configured.scheme == "https" else 80)
                    if parsed_port != configured_port:
                        override = None
            except (TypeError, ValueError):
                override = None
        return override or self._hermes_base_url

    @property
    def hermes_model(self) -> str:
        active = self._active_model_connection()
        if active:
            return str(active["model"])
        return self._overrides.get("hermes_model") or self._hermes_model

    @property
    def model_provider(self) -> str:
        active = self._active_model_connection()
        if active:
            return str(active.get("provider") or "custom")
        return str(self._overrides.get("model_provider") or self._hermes_provider)

    @property
    def model_connection_custom(self) -> bool:
        """是否已显式保存用户自定义模型连接。"""
        return self._active_model_connection() is not None or bool(
            self._overrides.get("hermes_base_url")
            and self._overrides.get("hermes_model")
        )

    @property
    def model_uses_hermes_gateway(self) -> bool:
        provider = self.model_provider.strip().lower().replace("_", "-")
        return provider in {"hermes", "hermes-agent", "hermes-gateway"}

    @property
    def hermes_fast_model(self) -> str:
        """诊断/解套等结构化卡片用的快模型；未配置 → 回退主模型（行为不变）。"""
        return (getattr(self, "_overrides", {}).get("hermes_fast_model")
                or self._hermes_fast_model or self.hermes_model)

    @property
    def hermes_api_key(self) -> str:
        # 可被 settings.json 覆盖（前端"模型"卡自填 key，各端口模式自行填入）；
        # 防御性读 _overrides（此属性可能早于 _overrides 初始化前被访问）。
        active = self._active_model_connection()
        override = (
            active.get("api_key") if active
            else getattr(self, "_overrides", {}).get("hermes_api_key")
        )
        try:
            from urllib.parse import urlsplit

            local_restricted = (
                self.model_uses_hermes_gateway
                and getattr(self, "agent_require_profile_attestation", False)
                and (urlsplit(self.hermes_base_url).hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
            )
        except (TypeError, ValueError):
            local_restricted = False
        if local_restricted:
            return self._hermes_api_key
        return override or self._hermes_api_key

    @property
    def feishu_webhook(self) -> str:
        return self._overrides.get("feishu_webhook") or self._feishu_webhook

    @property
    def feishu_secret(self) -> str:
        return self._overrides.get("feishu_secret") or self._feishu_secret

    @property
    def push_enabled(self) -> bool:
        if "push_enabled" in self._overrides:
            return bool(self._overrides["push_enabled"])
        return self._push_enabled

    @property
    def schedule(self) -> dict[str, str]:
        return {**self._schedule, **self._overrides.get("schedule", {})}

    def section(self, name: str) -> dict:
        """返回 config.yaml 的某个顶层子配置（供 clients/<id>/ext 读取自有配置，
        无需触碰 _raw 私有属性，也不为某客户在共享 config 上加字段——隔离铁律）。"""
        v = self._raw.get(name)
        return dict(v) if isinstance(v, dict) else {}

    def settings_view(self) -> dict:
        """GET /api/settings 返回的视图。
        飞书签名密钥（signing secret）不回传明文——只回 *_set 布尔指示是否已配置，
        前端据此显示『已配置（留空则不修改）』，PUT 时留空即不覆盖。"""
        return {
            # webhook URL 本身就是发送凭据，和 secret/key 一样不得回传明文。
            "feishu_webhook": "",
            "feishu_webhook_set": bool(self.feishu_webhook),
            "feishu_secret": "",
            "feishu_secret_set": bool(self.feishu_secret),
            "push_enabled": self.push_enabled,
            "hermes_base_url": self.hermes_base_url,
            "hermes_model": self.hermes_model,
            "model_provider": self.model_provider,
            "hermes_api_key": "",                          # 不回传明文（脱敏）
            "hermes_api_key_set": bool(self.hermes_api_key),
            # 只有运行时明确保存的 endpoint+model 才算“自定义连接”。
            # config.yaml 里的 Hermes 默认值属于可选集成，不应误报为用户已配置。
            "model_connection_custom": self.model_connection_custom,
            "active_model_connection_id": self.active_model_connection_id,
            "model_connections": self.model_connections_view(),
            "hermes_integration": {
                "optional": True,
                "advanced_diagnostics": True,
                "active_as_model_gateway": bool(
                    self.model_connection_custom and self.model_uses_hermes_gateway
                ),
            },
            "agent_require_restricted_toolsets": self.agent_require_restricted_toolsets,
            "agent_allowed_toolsets": list(self.agent_allowed_toolsets),
            "agent_require_profile_attestation": self.agent_require_profile_attestation,
            "push_via": self.push_via,                     # gateway / feishu → 前端据此决定推送卡形态
            "schedule": self.schedule,
        }
