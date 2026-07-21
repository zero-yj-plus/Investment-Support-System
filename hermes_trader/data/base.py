"""数据层基础设施：TTL 缓存、安全调用、交易日历。

数据真实性约束的落地点：所有采集函数只返回真实拿到的数据；
拿不到的字段进入 missing 列表，绝不用假数据填充。
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import socket
import ssl
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import certifi

log = logging.getLogger("hermes.data")


def direct_https_opener(*handlers):
    """构造严格验证 TLS 且不继承进程代理的 urllib opener。

    macOS 的独立 Python/虚拟环常没有系统钥匙串 CA，裸 ``urlopen`` 会把
    新浪、交易所等正常 HTTPS 源误报为不可用。统一使用项目依赖 certifi
    的 CA bundle，不通过关闭证书验证来“修复”。
    """
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(
            context=ssl.create_default_context(cafile=certifi.where())
        ),
        *handlers,
    )

# 单次 akshare 调用的墙钟超时上限（秒）。akshare 底层裸 requests 多数无 timeout，
# 数据源被限流时可无限挂起；这里用线程级超时给每个通道兜底，超时即放弃进下一通道。
_CALL_TIMEOUT = 15.0
_POOL_MAX = 16
# 复用一个线程池执行被超时包裹的同步调用，避免每次新建线程的开销。
# 注意：线程无法真正被 kill，超时只保证 safe() 不会无限等待；被放弃的线程仍会在后台跑完。
_call_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=_POOL_MAX, thread_name_prefix="ak-call"
)

# 进程级 socket 默认超时兜底：akshare 底层裸 requests 多数不传 timeout，数据源被限流时
# worker 线程会永久挂起（线程无法被 kill）。设一个【略小于】线程级 _CALL_TIMEOUT 的默认
# socket 超时：这样挂起的请求会先自行抛 socket.timeout 退出、归还 worker 并释放 relay 模式
# 的 _proxy_mode_lock，而不是把锁一直占到 future 放弃之后（避免串行通道被一个僵尸长期堵死）。
# 不影响 Hermes：httpx 异步客户端用非阻塞 socket 且显式设 timeout，不读此默认值；
# 中台 /spot 的 urllib 调用也显式传了 timeout，同样不受影响。
if socket.getdefaulttimeout() is None:
    socket.setdefaulttimeout(13)

# 线程池在飞任务计数（仅作可观测指标，不用于门控）。
# 历史教训：曾用它在 pool_inflight>=_POOL_MAX 时"快速失败"，但 relay 模式下所有调用经
# _proxy_mode_lock 串行，一个慢调用会让多个 worker 排队等锁、瞬间打满计数，导致后续【全部】
# 调用被误杀成"数据不全"——典型的缓解措施反噬。已移除门控，僵尸由上面的 socket 超时兜底回收。
_pool_inflight = 0
_pool_count_lock = threading.Lock()

# AkShare 少数接口会在函数内部创建 py_mini_racer/V8 实例。MiniRacer 的地址池在
# uvicorn + asyncio.to_thread + 本模块线程池并发场景下并不安全：它不是抛 Python
# 异常，而是直接 FATAL/abort 整个服务进程。因此这里做最后一道集中门禁，任何新调用方
# 即使误把这些函数交给 safe()，也只会诚实降级为 None，不会把整站打崩。
#
# 每个被拦接口都有无 V8 的免费替代：腾讯历史/指数、国务院节假日日历、东财/腾讯资金链。
_UNSAFE_INPROCESS_SOURCE_NAMES = frozenset({
    "index_us_stock_sina",
    "stock_fund_flow_industry",
    "stock_zh_a_daily",
    "stock_zh_a_minute",
    "stock_zh_index_daily",
    "tool_trade_date_hist_sina",
})


def _pool_inc() -> None:
    global _pool_inflight
    with _pool_count_lock:
        _pool_inflight += 1


def _pool_dec(_f=None) -> None:
    global _pool_inflight
    with _pool_count_lock:
        _pool_inflight = max(0, _pool_inflight - 1)


def pool_inflight() -> int:
    with _pool_count_lock:
        return _pool_inflight

# 国内行情源（东财/新浪/财联社等）默认绕过系统代理直连（境内机器/服务器的正确姿势）。
# 通道策略见 safe()：系统代理部署下直连与 proxy 兜底都持锁串行互斥；
# 中继部署下代理为进程级默认（无锁并发），仅隧道进程死透时走单线程 direct_x 灾备直连。
_DOMESTIC_NO_PROXY = (
    "eastmoney.com,.eastmoney.com,sina.com.cn,.sina.com.cn,"
    "cls.cn,.cls.cn,10jqka.com.cn,.10jqka.com.cn,sinajs.cn,.sinajs.cn,"
    "legulegu.com,.legulegu.com"
)
_ORIG_NO_PROXY = {var: os.environ.get(var, "") for var in ("NO_PROXY", "no_proxy")}
_DIRECT_NO_PROXY = {
    var: ",".join(x for x in (val, _DOMESTIC_NO_PROXY) if x)
    for var, val in _ORIG_NO_PROXY.items()
}
# Compatibility hook retained for older diagnostics/tests. Runtime routing no
# longer trusts this import-time snapshot; ``_system_proxy_configured`` reads
# the environment after the customer .env has been loaded.
_HAS_PROXY = False
_proxy_mode_lock = threading.Lock()

# 境内数据中继：海外部署时设置后，国内行情源优先经境内服务器中转，绕开海外 IP 限制。
# 支持两种形式：
#   socks5://127.0.0.1:11080         —— SSH 隧道（推荐，autossh 保活，仅用境内服务器22端口）
#   http://user:pass@host:port       —— 认证型 HTTP forward proxy
# 运行时读取（而非 import 时），以便 .env 在 config 加载后即可生效，不依赖启动方式。
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")


def relay_proxy() -> str:
    return os.environ.get("DATA_RELAY_PROXY", "").strip()


def _system_proxy_configured() -> bool:
    """Read proxy topology at call time, after the customer .env is loaded."""
    return any(
        os.environ.get(var)
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    )


def _relay_requests() -> str:
    """requests/akshare 走 ALL_PROXY；socks 用 socks5h 让 DNS 也走代理端，避免本地解析被污染。"""
    p = relay_proxy()
    return p.replace("socks5://", "socks5h://") if p.startswith("socks5://") else p

for _var, _val in _DIRECT_NO_PROXY.items():
    os.environ[_var] = _val

# 配置了境内中继时，把代理一次性写入【进程级】环境变量，之后所有 akshare 调用并发经代理
# 直跑、不再过 _proxy_mode_lock 串行——这是消除"一个慢调用持锁堵死全部并发请求"瓶颈的关键。
# 历史教训：旧实现每次调用在锁内临时改 os.environ 跑完再还原，relay 为主通道时等于把所有
# 数据请求串行化，仪表盘一次并发 ~20 个请求时彼此排队等锁、集体撞 15s future 超时→全 None。
_relay_env_applied = False
_relay_env_lock = threading.Lock()


def _apply_relay_env() -> None:
    """整体写入 relay 模式的进程级代理环境（_ensure_relay_env 首次应用与 direct_x
    灾备通道退出时恢复共用，保证两处写入完全一致）。NO_PROXY 豁免清单的依据：
    - 本机回环：Hermes 网关与本机中台不走代理（httpx 已 trust_env=False，双保险）；
    - 部署原有白名单：不吞掉运维在环境里预置的 bypass 项；
    - 飞书域：webhook 推送与 lark-oapi bot 都是进程内 HTTP 客户端会读这些变量，
      不豁免会被 SOCKS 代理劫持经数据隧道——隧道一挂推送/回复全断；
    - 中台 API 主机：云端 Docker 下 DATA_RELAY_API 可能是 host.docker.internal
      而非 127.0.0.1，urllib 不支持 socks，不豁免必抛 unknown url type: socks5h。"""
    relay_url = _relay_requests()
    for v in _PROXY_VARS:
        os.environ[v] = relay_url
    hosts = ["localhost", "127.0.0.1"]
    orig = _ORIG_NO_PROXY.get("NO_PROXY") or _ORIG_NO_PROXY.get("no_proxy") or ""
    hosts += [x.strip() for x in orig.split(",") if x.strip()]
    hosts += [".feishu.cn", "open.feishu.cn"]
    try:
        api_host = urlparse(os.environ.get("DATA_RELAY_API", "").strip()).hostname
    except ValueError:
        api_host = None
    if api_host:
        hosts.append(api_host)
    no_proxy = ",".join(dict.fromkeys(hosts))
    for v in ("NO_PROXY", "no_proxy"):
        os.environ[v] = no_proxy


def _ensure_relay_env() -> None:
    """幂等地把境内中继代理设为进程级默认（NO_PROXY 豁免清单见 _apply_relay_env）。"""
    global _relay_env_applied
    if _relay_env_applied:
        return
    with _relay_env_lock:
        if _relay_env_applied:
            return
        _apply_relay_env()
        _relay_env_applied = True


def _tunnel_alive() -> bool:
    """对 DATA_RELAY_PROXY 的 host:port 做 0.5s TCP 探测，区分『隧道进程死透』与
    『隧道活着但上游异常』。不缓存：仅在 relay 双通道耗尽的灾备分支调用，频率天然受限。"""
    try:
        u = urlparse(relay_proxy())
        host, port = u.hostname, u.port
    except ValueError:
        return False
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _call_in_mode(mode: str, fn, *args, **kwargs):
    """mode='direct'   ：无代理部署的默认通道，直接调用（无锁）；
    mode='relay_g'  ：境内中继代理已设为进程级默认，并发直跑（无锁，热路径）；
    mode='direct_x' ：持锁摘掉全部代理环境变量后真直连——relay 隧道死透时的灾备，
                      也是系统代理部署下的直连通道（与 'proxy' 互斥，避免互踩 NO_PROXY）；
    mode='proxy'    ：持锁还原系统代理 + 原始 NO_PROXY 的兜底通道。"""
    if mode in ("direct", "relay_g"):
        return fn(*args, **kwargs)
    with _proxy_mode_lock:
        saved = {v: os.environ.get(v) for v in (*_PROXY_VARS, "NO_PROXY", "no_proxy")}
        try:
            if mode == "direct_x":
                for v in _PROXY_VARS:
                    os.environ.pop(v, None)
                for var, val in _DIRECT_NO_PROXY.items():
                    os.environ[var] = val
            else:  # proxy：还原系统代理 + 原始 NO_PROXY
                for v in ("NO_PROXY", "no_proxy"):
                    os.environ[v] = _ORIG_NO_PROXY.get(v, "")
            return fn(*args, **kwargs)
        finally:
            if _relay_env_applied:
                # relay 部署的灾备退出：整体重写 relay env 而非还原 saved——
                # 保证退出时全局状态确定地回到 relay_g 模式，不依赖进入时的快照。
                _apply_relay_env()
            else:
                for v, val in saved.items():
                    if val is None:
                        os.environ.pop(v, None)
                    else:
                        os.environ[v] = val
                for var, val in _DIRECT_NO_PROXY.items():
                    os.environ[var] = val

CN_TZ = ZoneInfo("Asia/Shanghai")

_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()

# 单飞（single-flight）：每个缓存 key 一把锁 + 引用计数，缓存击穿时同 key 并发
# 只触发一次真实抓取，其余请求等锁释放后复用回填的结果，避免对刚被限流的数据源/中台
# 发 N 倍突发请求。引用计数归零即清理，防止锁字典随 key 无限膨胀。
_inflight: dict[str, "list"] = {}  # key -> [threading.Lock, refcount]
_inflight_lock = threading.Lock()

# 数据源运行时观测指标：safe() 每次调用按 label 记录最近一次的延迟/成败/所用通道/时间，
# 供「数据源健康面板」展示来源与实时延迟，全程被动采集、不产生额外请求。
_source_metrics: dict[str, dict] = {}
_metrics_lock = threading.Lock()

# 慢源通用熔断：同一 label 一旦吃满超时，或连续出现明确的
# JSON 解析失败，10 分钟内直接返回 None 走上层 last-good/降级。
# 这不替代行情 runtime_gateway 的细粒度熔断；它专门兜住 akshare
# 这类无法取消的同步请求，避免用户刷新持续制造后台僵尸线程。
_SAFE_BRK: dict[str, dict[str, float | int | str]] = {}
_SAFE_BRK_LOCK = threading.Lock()
_SAFE_BRK_FAILS = 2
_SAFE_BRK_COOLDOWN = 600.0


def _safe_breaker_available(label: str) -> tuple[bool, float, str]:
    if not label:
        return True, 0.0, ""
    now = time.monotonic()
    with _SAFE_BRK_LOCK:
        row = _SAFE_BRK.get(label)
        if not row:
            return True, 0.0, ""
        remaining = max(0.0, float(row.get("until") or 0.0) - now)
        if remaining <= 0:
            row["until"] = 0.0
            return True, 0.0, ""
        return False, remaining, str(row.get("reason") or "source failure")


def _safe_breaker_report(label: str, *, ok: bool, reason: str = "", severe: bool = False) -> None:
    if not label:
        return
    with _SAFE_BRK_LOCK:
        if ok:
            _SAFE_BRK.pop(label, None)
            return
        row = _SAFE_BRK.setdefault(label, {"fails": 0, "until": 0.0, "reason": ""})
        row["fails"] = int(row.get("fails") or 0) + 1
        row["reason"] = str(reason or "source failure")[:140]
        if severe or int(row["fails"]) >= _SAFE_BRK_FAILS:
            row["until"] = time.monotonic() + _SAFE_BRK_COOLDOWN


def _record_source(label: str, latency_ms: float, ok: bool, mode: str, error: str | None) -> None:
    if not label:
        return
    with _metrics_lock:
        m = _source_metrics.setdefault(label, {"calls": 0, "fails": 0})
        m["calls"] += 1
        if not ok:
            m["fails"] += 1
        m["last_latency_ms"] = round(latency_ms)
        m["last_ok"] = ok
        m["last_mode"] = mode            # direct(直连) / relay(中继) / proxy(系统代理)
        _now = now_cn()
        # 存完整时间戳 + epoch 排序键：仅存 %H:%M:%S 会让 _match_metric 在跨零点时
        # 把昨天 23:59 误判为比今天 00:30 更新（字典序），面板展示时再裁剪到时分秒。
        m["last_ts"] = _now.strftime("%Y-%m-%d %H:%M:%S")
        m["last_epoch"] = _now.timestamp()
        m["last_error"] = error


def source_metrics() -> dict:
    """返回各数据源最近一次调用的观测指标快照（健康面板用）。"""
    with _metrics_lock:
        return {k: dict(v) for k, v in _source_metrics.items()}


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def ts_str() -> str:
    return now_cn().strftime("%Y-%m-%d %H:%M:%S")


def ttl_cache(
    seconds: float,
    fail_seconds: float | None = None,
    *,
    offhours_seconds: float | None = None,
    long_ok: "Callable[[Any], bool] | None" = None,
):
    """TTL 缓存装饰器（按函数名+参数缓存）。

    fail_seconds：结果为 None（数据源全链路失败）时使用的更长缓存时间，
    避免每个 TTL 周期都重新走一遍慢失败链路把接口拖死。默认 max(seconds, 180)。

    offhours_seconds：非 A 股盘中窗口（09:15–15:15）的成功结果 TTL。只由明确
    传入该参数的市场截面函数启用，不影响个股快照、新闻等需继续刷新的缓存。

    long_ok：可选谓词 long_ok(result)->bool。仅当返回 True 时，该次结果才享受
    offhours 长缓存；否则即使在盘外也只用基础 seconds。用于"空/未发布/源异常"这类
    非完整结果**不得进入盘外长缓存**（如龙虎榜今日结算中的空数据），配合快照到达时
    invalidate_ttl_cache 强制失效。默认 None → 任何非 None 成功结果都可长缓存（旧行为）。
    """
    fail_ttl = fail_seconds if fail_seconds is not None else max(seconds, 180.0)

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = f"{fn.__module__}.{fn.__name__}:{args}:{sorted(kwargs.items())}"
            # 盘中窗口判定可能进入 trade_dates() 的 TTL 缓存，必须在
            # _cache_lock 之外完成；否则外层缓存命中时会持有非可重入锁，
            # 内层 trade_dates() 再次取锁，导致请求与测试永久卡死。
            in_market_window = True
            if offhours_seconds is not None:
                current = now_cn()
                hm = current.hour * 60 + current.minute
                in_market_window = (
                    is_trade_day(current.date())
                    and (9 * 60 + 15) <= hm <= (15 * 60 + 15)
                )

            def _result_ttl(result) -> float:
                if result is None:
                    return fail_ttl
                # 空/未发布/源异常等非完整结果不进盘外长缓存（long_ok 判否）。
                if (
                    offhours_seconds is not None
                    and not in_market_window
                    and (long_ok is None or long_ok(result))
                ):
                    return max(seconds, float(offhours_seconds))
                return seconds

            def _fresh(hit) -> bool:
                if not hit:
                    return False
                age = time.monotonic() - hit[0]
                return age < _result_ttl(hit[1])

            with _cache_lock:
                hit = _cache.get(key)
                if _fresh(hit):
                    return hit[1]

            # 缓存 miss/过期：抢该 key 的单飞锁。抢到者执行真实抓取并回填；
            # 等待者拿到锁后重读缓存（此时通常已被回填），避免重复抓取上游。
            with _inflight_lock:
                entry = _inflight.get(key)
                if entry is None:
                    entry = [threading.Lock(), 0]
                    _inflight[key] = entry
                entry[1] += 1
            lock = entry[0]
            try:
                with lock:
                    # 持锁后再查一次缓存：可能在排队期间已被前一个持锁者回填。
                    with _cache_lock:
                        hit = _cache.get(key)
                        if _fresh(hit):
                            return hit[1]
                    result = fn(*args, **kwargs)
                    with _cache_lock:
                        _cache[key] = (time.monotonic(), result)
                    return result
            finally:
                # 引用计数归零即从字典移除，防止 key 锁无限累积。
                with _inflight_lock:
                    entry[1] -= 1
                    if entry[1] <= 0:
                        _inflight.pop(key, None)

        return wrapper

    return deco


def invalidate_ttl_cache(name_prefix: str) -> int:
    """按『模块.函数名』前缀强制失效 ttl_cache 缓存键，返回清除条数。

    用于官方龙虎榜快照到达时强制刷新 mainforce.longhu 的盘外缓存，
    避免"今日结算中"的空结果在 6 小时长缓存里冒充"今日无榜"。
    prefix 形如 "hermes_trader.data.mainforce_source.longhu:"（含冒号锚定到该函数）。
    """
    with _cache_lock:
        keys = [k for k in _cache if k.startswith(name_prefix)]
        for k in keys:
            _cache.pop(k, None)
    return len(keys)


# ---------------------------------------------------------------- 东财熔断器
# 东财"板块/资金流"类接口对中继 IP 会间歇掐连接(RemoteDisconnected)，每次失败都吃满 safe 超时；
# 一个 sectors/flow 请求里多个东财调用串行失败 = 首请求 24-27s(审计实测)。连续失败达阈值后
# 熔断一段时间：期间同类调用直接跳过(返 None 走既有兜底链，兜底本就诚实标注)，既提速又少打
# 无谓请求；任一成功立即复位。仅用于"板块/资金流"这组已知被掐接口——涨停三池不接(工作良好
# 且有当日 last-good 层)。计数无锁：竞态最多让熔断早/晚一拍，无害。
_EM_BRK = {"fails": 0, "until": 0.0}
_EM_BRK_FAILS = 3        # 连续失败 3 次进入熔断
_EM_BRK_COOLDOWN = 300.0  # 熔断 5 分钟


def em_available() -> bool:
    """东财板块/资金类调用当前是否值得尝试（熔断期内返回 False，调用方直接走兜底）。"""
    return time.monotonic() >= _EM_BRK["until"]


def em_report(ok: bool) -> None:
    """真实发起过东财板块/资金类调用后上报结果（跳过的不报）。"""
    if ok:
        _EM_BRK["fails"] = 0
        _EM_BRK["until"] = 0.0
        return
    _EM_BRK["fails"] += 1
    if _EM_BRK["fails"] >= _EM_BRK_FAILS:
        _EM_BRK["until"] = time.monotonic() + _EM_BRK_COOLDOWN
        log.info("东财板块/资金接口连续 %d 次失败，熔断 %.0fs（期间走兜底链）", _EM_BRK["fails"], _EM_BRK_COOLDOWN)


def safe(fn, *args, label: str = "", timeout: float = _CALL_TIMEOUT, **kwargs):
    """调用 akshare 等外部数据源，多通道自动降级：
    配置了境内中继 → 进程级代理无锁并发（relay_g×2）；两次均失败且隧道 TCP 探测
    不通（进程死透）时追加一次持锁直连灾备（direct_x）。
    有系统代理 → 持锁直连优先（direct_x），系统代理兜底（proxy）。
    无代理 → 直连重试。全部失败返回 None 并记录日志，不抛出。

    每个通道用线程级墙钟超时（默认 15s）包裹：akshare 底层裸 requests 多数无 timeout，
    数据源被限流时可无限挂起；超时即放弃该通道进下一个，保证 safe() 不会无限等待。
    线程无法真正 kill，被放弃的请求仍会在后台跑完，但调用方拿到超时即返回。"""
    fn_name = str(getattr(fn, "__name__", ""))
    if fn_name in _UNSAFE_INPROCESS_SOURCE_NAMES:
        source_label = label or fn_name
        reason = "已阻止进程内 V8 数据源；使用无 V8 免费备源"
        _record_source(source_label, 0, False, "blocked-v8", reason)
        log.warning("数据源 [%s] 被安全门禁阻止: %s", source_label, fn_name)
        return None

    available, remaining, breaker_reason = _safe_breaker_available(label)
    if not available:
        reason = f"熔断中，{remaining:.0f}s 后可探测；上次: {breaker_reason}"
        _record_source(label, 0, False, "breaker-open", reason)
        log.info("数据源 [%s] %s", label, reason)
        return None

    relay_on = bool(relay_proxy())
    if relay_on:
        # 中继为主：代理设进程级默认后用无锁 relay_g 并发直跑（两次重试应对瞬时抖动）。
        # 灾备 direct_x 不预排进通道表：见下方 except 分支，只在隧道死透时追加。
        _ensure_relay_env()
        modes = ["relay_g", "relay_g"]
    elif _system_proxy_configured():
        # 直连也走持锁 direct_x：无锁 direct 会在 proxy 兜底持锁期间读到被还原的
        # NO_PROXY 中间态、被静默路由经系统代理（伪失败+指标失真）；该拓扑调用量低，
        # 串行可接受，换两通道环境互不污染。
        modes = ["direct_x", "proxy", "proxy"]
    else:
        modes = ["direct", "direct"]
    last_err = None
    t_all = time.monotonic()
    disaster_added = False  # 灾备 direct_x 仅追加一次，避免反复探测/重试
    severe_failure = False
    i = 0
    while i < len(modes):
        mode = modes[i]
        t0 = time.monotonic()
        try:
            future = _call_pool.submit(_call_in_mode, mode, fn, *args, **kwargs)
            _pool_inc()
            future.add_done_callback(_pool_dec)  # 任务结束才减计数（仅作可观测指标，不门控）
            try:
                res = future.result(timeout=timeout)
                _record_source(label, (time.monotonic() - t0) * 1000, True, mode, None)
                _safe_breaker_report(label, ok=True)
                return res
            except concurrent.futures.TimeoutError as e:
                # 放弃等待该通道（后台线程仍会跑完但结果被丢弃）。
                future.cancel()
                last_err = TimeoutError(f"通道[{mode}]超时>{timeout}s")
                severe_failure = True
                log.warning(
                    "数据源调用超时 [%s] 通道=%s >%ss",
                    label or getattr(fn, "__name__", fn), mode, timeout,
                )
                raise last_err from e
        except Exception as e:
            last_err = e
            # 吃满墙钟超时或上游返回非 JSON 后，对同一坏源立即再打一次
            # 只会翻倍用户等待并多留一个无法取消的后台线程。这两类故障
            # 直接终止本轮并熔断；瞬时连接断开等快失败仍保留后备通道。
            terminal_failure = isinstance(e, TimeoutError) or type(e).__name__ == "JSONDecodeError"
            if terminal_failure:
                severe_failure = True
                break
            # relay 两通道均失败：仅当隧道进程死透（TCP 探测不通）时才追加一次 direct_x
            # 灾备抢直连。隧道活着但上游异常时不抢——避免污染其余并发的健康 relay 调用，
            # 也防止把一时的上游抖动升级为持锁串行直连。
            if (
                relay_on
                and not disaster_added
                and i == len(modes) - 1
                and not _tunnel_alive()
            ):
                disaster_added = True
                modes.append("direct_x")
                log.warning(
                    "数据源 [%s] relay 两通道失败且隧道已死，追加 direct_x 灾备直连",
                    label or getattr(fn, "__name__", fn),
                )
            if i < len(modes) - 1:
                time.sleep(0.8)
        i += 1
    _safe_breaker_report(label, ok=False, reason=str(last_err)[:140], severe=severe_failure)
    _record_source(label, (time.monotonic() - t_all) * 1000, False, modes[-1], str(last_err)[:140])
    log.warning("数据源调用失败 [%s]: %s", label or getattr(fn, "__name__", fn), last_err)
    return None


def get_ak():
    """惰性导入 akshare；未安装返回 None。"""
    try:
        import akshare as ak  # noqa: PLC0415

        return ak
    except Exception as e:
        log.warning("akshare 不可用: %s", e)
        return None


@ttl_cache(6 * 3600)
def trade_dates() -> set[str] | None:
    """A 股交易日（YYYY-MM-DD 字符串集合，无网络、无 V8）。

    A 股周末始终休市（即使国务院安排为调休工作日），工作日再排除国务院公开的
    法定节假日。``chinesecalendar`` 把当年正式公布的节假日表随包发布；若当前年份
    尚未收录，则返回 ``None`` 让上层明确退化为“仅工作日”判断，而不是调用会导致
    进程崩溃的新浪加密日历。
    """
    try:
        from chinese_calendar import is_holiday  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log.warning("本地交易日历不可用: %s", str(e)[:120])
        return None

    today = now_cn().date()
    start = date(2004, 1, 1)
    end = date(today.year, 12, 31)
    try:
        days: set[str] = set()
        cur = start
        while cur <= end:
            if cur.weekday() < 5 and not is_holiday(cur):
                days.add(cur.isoformat())
            cur += timedelta(days=1)
        # 腾讯上证指数日 K 是“实际开过市”的历史真值。用它覆盖最近约 640 个
        # 交易日，可修正法定节假日表与交易所特别休市不一致的日期（如
        # 2024-02-09）。未来日期仍由当年已公布的本地节假日表判断。
        try:
            from . import tencent_source as _tc  # noqa: PLC0415

            actual = _tc.index_trade_dates()
        except Exception:  # noqa: BLE001
            actual = set()
        if actual:
            lo, hi = min(actual), max(actual)
            days = {d for d in days if not (lo <= d <= hi)}
            days.update(actual)
            mode = "tencent-history+local-calendar"
        else:
            mode = "local-calendar-degraded"

        # 已由上交所历史交易记录确认的额外休市日。它们不一定被国务院“法定
        # 节假日”库标为 holiday，需在未来日历尚未沉淀为行情前显式排除。
        days.difference_update({"2024-02-09"})
        _record_source("A股交易日历", 0, True, mode, None)
        return days
    except (NotImplementedError, ValueError) as e:
        _record_source("A股交易日历", 0, False, "local-calendar", str(e)[:120])
        log.warning("本地交易日历尚未覆盖当前年份: %s", str(e)[:120])
        return None


def is_trade_day(d: date | None = None) -> bool:
    d = d or now_cn().date()
    dates = trade_dates()
    if dates is None:
        # 日历拿不到时退化为工作日判断
        return d.weekday() < 5
    return d.isoformat() in dates


def trade_day_status(d: date | None = None) -> dict:
    """自动交付使用的三态日历契约，禁止把「日历不可用」偷换成开市。"""
    target = d or now_cn().date()
    dates = trade_dates()
    if dates is None:
        if target.weekday() >= 5:
            return {
                "status": "closed", "is_trade_day": False,
                "source": "weekend_rule", "as_of": target.isoformat(), "quality": "derived",
            }
        return {
            "status": "unknown", "is_trade_day": None,
            "source": "calendar_unavailable", "as_of": target.isoformat(), "quality": "missing",
        }
    return {
        "status": "open" if target.isoformat() in dates else "closed",
        "is_trade_day": target.isoformat() in dates,
        "source": "tencent_history+chinese_calendar",
        "as_of": target.isoformat(),
        "quality": "derived",
    }


def _weekday_adjacent(d: date, step: int) -> str:
    """交易日历不可用或日历边界之外的最小降级：只跳过周末。

    节假日仍以 ``trade_dates`` 为唯一权威口径；此降级与 ``is_trade_day``
    在日历失败时的工作日判定保持一致，避免上层重新写 ``+/- 1 day``。
    """
    cur = d + timedelta(days=step)
    while cur.weekday() >= 5:
        cur += timedelta(days=step)
    return cur.isoformat()


def prev_trade_day(d: date | None = None) -> str | None:
    """返回 d 之前最近一个交易日（YYYY-MM-DD）。"""
    d = d or now_cn().date()
    dates = trade_dates()
    if dates:
        candidates = [x for x in dates if x < d.isoformat()]
        if candidates:
            return max(candidates)
    return _weekday_adjacent(d, -1)


def next_trade_day(d: date | None = None) -> str | None:
    """返回 d 之后最近一个交易日（YYYY-MM-DD）。

    所有“次日策略/验证窗口/报告标题”都应调用本函数，不得使用
    ``today + timedelta(days=1)``，否则周五会错指到周六。
    """
    d = d or now_cn().date()
    dates = trade_dates()
    if dates:
        candidates = [x for x in dates if x > d.isoformat()]
        if candidates:
            return min(candidates)
    return _weekday_adjacent(d, 1)


def to_float(v, default=None):
    """转 float，并兼容问小达/部分源返回的带格式字符串，避免静默丢字段。

    字符串额外识别：①千分位逗号去除；②结尾单位"万"×1e4、"亿"×1e8；
    ③百分号"%"去除（返回的是数值本身，不含 % 号，单位语义由调用方负责）。
    纯数字 / float / None / 不可解析 的原有行为不变（不可解析仍回退 default）。
    """
    try:
        if v is None:
            return default
        # 仅对字符串做格式归一化；数值（int/float/Decimal 等）直接交给 float()，
        # 不改变大量既有调用方依赖的纯数字/已是 float 的行为。
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return default
            s = s.replace(",", "").replace("，", "")  # 半角+全角千分位逗号
            mult = 1.0
            if s.endswith("%"):
                s = s[:-1].strip()
            elif s.endswith("万"):
                s, mult = s[:-1].strip(), 1e4
            elif s.endswith("亿"):
                s, mult = s[:-1].strip(), 1e8
            if not s:  # 去单位后为空（如 "万"/"%"）视为不可解析
                return default
            f = float(s) * mult
        else:
            f = float(v)
        if f != f:  # NaN
            return default
        return f
    except (TypeError, ValueError):
        return default


def to_int(v, default=None):
    f = to_float(v)
    return int(f) if f is not None else default
