# Hermes Trader — API 契约 v1

前后端唯一事实来源。所有接口同源挂载在 `/api` 下，JSON 编码 UTF-8。
**大多数行情数据接口**含 `ok`(bool)、`ts`(取数时间字符串)、`missing`(缺失/降级字段说明数组)；
部分接口（如 `/status`、`/holdings`、`/knowledge`、`/settings`、`/archive` 等管理类）按各自定义返回，
不强制带 `ts`/`missing`——以下各节签名为准。
行情快照类接口（overview/limitup/sentiment 等）额外含 `data_date`(数据归属交易日 YYYY-MM-DD)：
盘前或非交易日数据归属上一交易日，此时 `missing` 会带相应提示，前端应优先显示 `data_date` 而非自然日。
情绪接口含 `confidence`(high/medium/low/none，因子缺失越多越低)，低可信时 `advice` 含人工确认提示。
**数据真实性约束：后端绝不编造数据；拿不到/降级的字段必须列入 `missing`，前端对 missing 显示「数据不全」标记。**

## 1. 系统状态

### GET /api/shell-status

首屏专用的轻量契约，只包含 `client`/`version`/北京时间交易状态/
`recommended_route`/`route_redirects`/`ui`/`data_source`。该请求不探测模型网关、
Agent 权限、证据库健康或任务中心，因此不得因运维上游超时而阻塞工作台路由。
完整健康状态仍以 `/api/status` 为准。

```json
{
  "ok": true,
  "status_scope": "shell",
  "client": {"name": "本地客户", "id": "local", "brand": "W.U.D.I.", "brand_sub": "A 股决策支持"},
  "version": "1.5.1",
  "date": "2026-06-10",
  "is_trade_day": true,
  "market_session": "intraday",
  "recommended_route": "today",
  "market_context": {"recommended_hash": "#/today"},
  "route_redirects": [],
  "ui": {"frontend": "vue", "legacy_enabled": false},
  "data_source": "多源行情"
}
```

`client.brand` 和 `client.brand_sub` 是客户可配置的显示品牌；`client.name` 是实例及报告署名，不能用作共享内核名称。Vue 页面不得写死 `W.U.D.I.`，服务端品牌不可用时回退显示 `Hermes Trader`。

`ui.frontend` 当前只允许 `vue`。`ui.legacy_enabled` 是启动期静态挂载开关：默认
`false` 时 `/legacy/` 与旧版兼容模块均为 404；改为 `true` 并重启后端后才提供旧版对照页面。
该配置不改变 `/api/*` 契约，Vue 开发服务器和正式构建都直接请求同一组 API。

### GET /api/status
```json
{
  "ok": true,
  "client": {"name": "本地客户", "id": "local"},
  "version": "1.0.0",
  "date": "2026-06-10",
  "is_trade_day": true,
  "market_session": "intraday",
  "recommended_route": "today",
  "market_context": {
    "timezone": "Asia/Shanghai",
    "now": "2026-06-10T10:30:00+08:00",
    "date": "2026-06-10",
    "is_trade_day": true,
    "trade_day": "2026-06-10",
    "previous_trade_day": "2026-06-09",
    "next_trade_day": "2026-06-11",
    "market_session": "intraday",
    "recommended_route": "today",
    "recommended_hash": "#/today"
  },
  "route_redirects": [
    {"from": "cockpit", "to": "today", "tab": "decision", "lifecycle": "redirect", "preserve_query": true}
  ],
  "route_lifecycle": {"schema_version": "route-lifecycle-v1", "allowed_lifecycle": ["visible", "hidden", "retired", "redirect"], "canonical": [], "redirects": []},
  "hermes": {
    "base_url": "http://127.0.0.1:8874/v1",
    "model": "hermes-agent",
    "connected": true,
    "optional": true,
    "required_for_core": false,
    "custom_connection": true,
    "agent_capability": {
      "ready": true,
      "required": true,
      "required_for_ai_calls": true,
      "blocks_core": false,
      "status": "restricted",
      "reason": "服务端工具权限已收敛",
      "runtime_mode": "server_agent",
      "tool_execution": "server",
      "allowed_toolsets": [],
      "enabled_toolsets": [],
      "unexpected_toolsets": [],
      "profile_attestation": "attested",
      "profile_hash": "46b2574e2bf0",
      "process_attestation": "bound",
      "checked_at": 1783906200.125
    },
    "run_summary": {"counts": {"completed": 18, "failed": 1}, "last_run": null}
  },
  "feishu": {"configured": true},
  "data_source": "多源行情",
  "relay": {"enabled": true, "ok": true, "stale": false},
  "jobs": [
    {"id": "morning", "name": "早盘内参 08:50", "kind": "core", "next_run": "2026-06-11 08:50:00", "enabled": true,
     "last_result": {"status": "ok", "started_at": "...", "finished_at": "...", "duration_ms": 3210, "error": null},
     "manual_run": {"enabled": true, "method": "POST", "endpoint": "/api/jobs/morning/run"},
     "retry": {"mode": "manual", "eligible": false, "endpoint": "/api/jobs/morning/run"}}
  ],
  "task_center": {"schema_version": "task-center-v1", "summary": {}, "capabilities": {}, "jobs": []}
}
```
`market_session` 枚举：`non_trading` `premarket` `intraday` `tail` `review`。
`recommended_route` 只会返回 8 个新一级路由之一：`premarket` `today` `risk` `tail` `review`
`research` `history` `settings`；交易时间自动推荐只使用 `premarket` `today` `tail` `review`。

`route_redirects` 是旧 hash 到新信息架构的结构化兼容契约；前端应保留 query，并把
`tab` 转为目标页签。旧 REST API 不因 hash 重定向被删除。

job id 至少包含内置 `morning` `midday` `tail` `close` `evening` `archive`，并会合并
`ext_*` 客户扩展任务与哨兵/预热/采集等 system 任务。`jobs` 是 v1 兼容列表，
`task_center.jobs` 是同一份列表的新契约位置。
> `hermes.model` 因客户而异：本地客户=本机 Hermes 网关的 `API_SERVER_MODEL_NAME`（默认 `hermes-agent`）；云端客户=relay 支持名（`gpt-5`）。示例值仅供参考，以各自 `config.yaml` 为准。

`hermes.connected` 只表示后端带鉴权请求 Hermes `GET /models` 得到 HTTP 200，即“网关可达”。
它**不表示模型已经成功生成内容，也不表示 Agent 权限门禁通过**。前端判断 Agent 是否可调用时必须读取
`hermes.agent_capability.ready`；最近运行的脱敏统计读取 `hermes.run_summary`。

Hermes/自定义模型是核心行情与规则功能之外的可选增强：`hermes.optional=true`且
`hermes.required_for_core=false` 表示未配置或预检失败不阻断核心产品。但 Agent 调用本身仍然
fail closed：`agent_capability.required` 是兼容字段，`required_for_ai_calls` 与它取值相同；
`blocks_core=false` 只说明影响范围，不得用来绕过 `agent_capability.ready`。

`relay`（境内数据隧道健康摘要，后端读 `market.last_relay_meta()` 与 relay 环境判断，**不额外发网络请求**）：
- `enabled`(bool)：是否配置了 `DATA_RELAY_PROXY` 或 `DATA_RELAY_API`。未配置中继时为 `false`。
- `ok`(bool)：最近一次中台 `/spot` 是否成功（未配置或冷启动尚无调用记录时默认 `true`，不误报）。
- `stale`(bool)：最近一次中台快照是否为陈旧缓存。
- 前端约定：当 `relay.enabled && !relay.ok` 时，在顶栏下方显示常驻告警横幅「⚠ 境内数据隧道异常，行情可能为陈旧/缺失数据」，恢复后自动消失。

### GET /api/agent/capabilities

读取 Agent 上线门禁。`refresh=true` 会跳过约 20 秒的 capability 缓存，重新验证 Hermes
`/capabilities`、`/toolsets` 与 HMAC 进程证明 `/attestation`。

```http
GET /api/agent/capabilities?refresh=true
```

```json
{
  "ok": true,
  "capability": {
    "ready": true,
    "required": true,
    "required_for_ai_calls": true,
    "blocks_core": false,
    "status": "restricted",
    "reason": "服务端工具权限已收敛",
    "runtime_mode": "server_agent",
    "tool_execution": "server",
    "allowed_toolsets": [],
    "enabled_toolsets": [],
    "unexpected_toolsets": [],
    "profile_attestation": "attested",
    "profile_hash": "46b2574e2bf0",
    "process_attestation": "bound",
    "checked_at": 1783906200.125
  }
}
```

- `ok` 只表示本 API handler 正常返回；是否准许调用 Agent 以 `capability.ready` 为准。
- `required` 是旧客户端兼容字段；`required_for_ai_calls` 保留同值并明确其仅作用于
  AI/Agent 调用。`blocks_core=false` 表示该门禁不阻断核心行情与规则功能。
- 正式受限运行状态必须是 `ready=true`、`status=restricted`、
  `process_attestation=bound`，且 `unexpected_toolsets=[]`。
- 无法证明零未授权工具、项目 profile 或 live child 进程时，接口返回 `ready=false`；
  当前实现使用稳定状态 `capability_unavailable` 并 fail closed，不会继续发送 prompt。
- `profile_hash` 是截断后的契约指纹，不是配置原文；响应不包含 API key、profile 路径或凭据。

### GET /api/agent/runs

读取最近的 Agent 运行账本。`limit` 默认 `30`，服务端限制为 `1..100`。

```http
GET /api/agent/runs?limit=5
```

```json
{
  "ok": true,
  "items": [
    {
      "run_id": "ar_2cbcb818f6c24e7da72fa13b9be94878",
      "client_request_id": "req_7d6bf50425be4fc6ac4d81a13caa4e07",
      "purpose": "report.morning",
      "model": "hermes-agent",
      "gateway": "http://127.0.0.1:8874/v1",
      "input_hash": "e447df0861aa",
      "input_bytes": 4268,
      "capability_status": "restricted",
      "capability_hash": "c502f7c93ed4",
      "capability_reason": "服务端工具权限已收敛",
      "status": "completed",
      "attempts": 1,
      "output_hash": "b9f661f09374",
      "output_bytes": 1832,
      "finish_reason": "stop",
      "error_code": null,
      "partial_output": false,
      "started_at": "2026-07-12T18:02:01.125+00:00",
      "completed_at": "2026-07-12T18:02:08.742+00:00",
      "tool_events": 0,
      "safe_to_use": true
    }
  ]
}
```

账本只保存并公开请求/输出哈希、字节数、capability 快照状态、重试次数、工具事件计数与终态；
不保存 prompt、模型原文、工具参数或工具结果。三个哈希字段在 API 中截断为 12 位。
`safe_to_use=true` 必须同时满足：`status=completed`、`finish_reason=stop`、
`capability_status` 为 `restricted` 或获准的 `direct_model_api`，且 `tool_events=0`。
`partial_output=true`、`safe_to_use=false` 或非 `completed` 终态都不得当作完整可信产出。

### GET /api/jobs
返回不需探测 Hermes 连接的轻量任务中心：

`technical_signal_refresh` 与 `news_event_observation` 的状态来自客户隔离的持久维护任务队列，
不依赖 API 进程内存。API 重启不会把仍由独立 Worker 执行的任务误标为中断；租约过期后任务
回到 `queued` 并从已提交游标续跑。

```json
{
  "ok": true,
  "schema_version": "task-center-v1",
  "generated_at": "2026-06-10T10:30:00+08:00",
  "summary": {"total": 12, "enabled": 12, "running": 0, "needs_attention": 1,
              "by_kind": {"core": 6, "extension": 3, "system": 3}},
  "capabilities": {"manual_run": true, "manual_retry": true, "automatic_retry": false},
  "jobs": []
}
```

### POST /api/jobs/{job_id}/run
立即手动触发某个定时任务（异步执行）。返回 `{"ok": true, "msg": "已触发"}`。

### GET /api/maintenance/jobs/{job_id}

返回持久维护任务的 `queued/running/completed/partial/failed/cancelled` 状态、来源、尝试次数、
心跳、进度游标和安全错误码。该接口不返回新闻正文、K 线内容或本地绝对路径。

### GET /api/ext/views
返回客户扩展视图完整注册表。服务端已经“先发现并注册全部扩展 API router，再一次性应用
`visible/hidden/retired/redirect`”。`hidden` 视图必须注册模块但不进导航；`retired/redirect` 不注册视图模块。

```json
[
  {"path": "quant_pick", "module": "/ext/quant_pick.js", "lifecycle": "visible",
   "visible": true, "navigation": true, "registered": true, "source": "quant_pick"}
]
```

## 2. 市场数据（全部真实数据，按字段走多源主备链路）

行情、估值、涨跌停、资金、新闻并不共用一个供应商。实际来源、时点、权限与降级状态由
`GET /api/datasources` 和各接口的 `missing` 字段共同说明；前端不得把“备用源”或 “T-1” 显示为实时主源。

### GET /api/market-data/{symbol}/{field}

统一字段级行情契约。`symbol` 支持 `000001`、`000001.SZ`、`sz000001`；`field`
当前包含 `last_price` `prev_close` `change_pct` `open` `high` `low` `amount`
`volume` `turnover_rate` `volume_ratio` `pe` `pb` `total_mv` `float_mv`。

`GET /api/market-data/contract` 返回每个字段的业务含义、单位、值语义、主备路由、
路由版本、实时/延迟门槛和批次范围。调用方不得只按字段名猜测单位或时效。

```json
{
  "ok": true,
  "data": {
    "schema_version": "market-data-v1",
    "symbol": "000001.SZ", "market": "CN-A", "field": "last_price",
    "value": 10.45, "unit": "CNY",
    "source_id": "tencent_quote", "source_tier": 1,
    "event_time": "2026-07-10T07:00:00.000000Z",
    "available_at": "2026-07-12T19:00:00.000000Z",
    "fetched_at": "2026-07-12T19:00:00.000000Z",
    "trading_day": "2026-07-10", "freshness": "stale",
    "fallback_level": 0, "quality_status": "degraded",
    "field_meaning": "最近可观察成交价；非交易时段为最近交易日收盘快照",
    "value_semantics": "point_in_time_snapshot",
    "contract_version": "field-contract-v2",
    "batch_id": "quote-batch/tencent_quote/...",
    "batch_status": "complete",
    "batch_expected_count": 14, "batch_present_count": 14,
    "quality_reasons": ["stale"],
    "degraded_reason": "数据超过该字段的新鲜度门槛",
    "route_version": "free-runtime-v1", "derived": false,
    "evidence_id": "uuid", "raw_payload_hash": "sha256"
  }
}
```

`quality_status` 枚举：`ok` `degraded` `conflict` `missing`。`freshness` 枚举：
`live` `delayed` `stale` `missing`。`ok=false` 且 `data.quality_status=missing` 代表主备源
均不可用，不得把 `value=null` 改写为 0。

`batch_status` 枚举：`complete` `partial` `unknown` `missing`。它表示同一供应商、
同一股票、同一观测时点的整批字段是否齐全；单个字段有值不等于该批次完整。
`quality_reasons` 是稳定机器码，`degraded_reason` 是对应的人类可读原因。

可选 `as_of=<ISO timestamp>` 必须包含 `Z` 或 UTC offset，是严格 PIT 查询：只读本地证据库，且同时要求
`available_at <= as_of` 与 `fetched_at <= as_of`，绝不回打实时源。

### 证据与来源运行态

- `GET /api/evidence/sources`：来源登记表（Tier、字段、成本、条款、风险、刷新频率）。
- `GET /api/evidence/stats`：原始载荷/证据点/谱系边数与两个免费行情源的缓存、并发、熔断运行态。
- `GET /api/evidence/points?symbol=&field=&limit=100`：最新证据点，`limit` 服务端强制收敛到 1–500。
- `GET /api/evidence/{evidence_id}`：单条证据、来源登记和跨源对账谱系。`include_raw=true`
  只在请求来自 loopback 且运维显式设置 `HERMES_ALLOW_RAW_EVIDENCE=1` 时可用；默认 403。
  URL 中的凭据会被脱敏。

证据网关当前活跃行情源是配置 `TDXAPI_BASE/TDXAPI_KEY` 的 TDX API 兼容服务与腾讯财经公开端点。
TDX 兼容服务既可以是客户授权的远程 API，也可以是仅监听 loopback 的本地 TQ 只读兼容桥；
两者对业务层提供相同字段和降级契约。
公开源无商业 SLA；熔断、回退、时点推断和跨源冲突都会显式反映在上述字段中。

### GET /api/disclosures/announcements

查询单只沪/深 A 股的公司公告。当前同时请求巨潮官网页面查询与公司所属交易所，
两条链路独立建模。巨潮这条链是“官网页面数据 adapter”，不是需购买权限的深证信正式
Web API，不得在产品中混称为“免费开放 API”。

参数：

- `code`：必填，6 位沪/深 A 股代码。
- `start_date/end_date`：ISO 日期，默认最近 30 日，最长 366 日。
- `limit`：1–50。
- `verify_pdf=true`：最多下载并校验 1 份官方 PDF，返回 SHA-256 与字节数；默认不下载 PDF。

```json
{
  "ok": true,
  "code": "600000",
  "state": "complete",
  "verification_state": "cross_confirmed",
  "used_last_good": false,
  "items": [{
    "announcement_id": "1225416710",
    "announcement_date": "2026-07-10",
    "published_at": "2026-07-10",
    "published_at_precision": "date",
    "title": "...",
    "original_url": "https://static.cninfo.com.cn/...PDF",
    "source_confirmations": ["cninfo", "sse"],
    "pdf_hash_state": "verified",
    "pdf_sha256": "..."
  }],
  "sources": [{"provider_family": "cninfo", "state": "ok", "count": 1}],
  "evidence": {
    "schema_version": "data-evidence-v1",
    "evidence_level": "tier_1",
    "data_state": "complete",
    "data_as_of": "2026-07-14",
    "fetched_at": "2026-07-13T17:40:00.000000Z",
    "content_hash": "..."
  }
}
```

`state` 为 `complete/degraded/missing`。单源成功、两源无交叉命中、PDF 校验失败或使用
last-good 都必须是 `degraded`；两源失败且无 last-good 时为 `missing`，不得伪装成公告数为 0。

### GET /api/interactions/management

查询深市公司在深交所互动易的投资者提问与管理层回复。该链路是官网页面内部 adapter，
不是法定公告或 SLA 支持的开放 API；证据等级固定为 `tier_3`，不参与日内主线、风险或买卖评分。

- `code`：必填，6 位代码；当前仅 `0/3` 开头的深市 A 股可查询。
- `start_date/end_date`：ISO 日期，默认最近 90 日，最长 366 日。
- `limit`：1–20，默认 6。
- 沪市/北交所返回 `state=not_supported`，不得解释为查询窗口内没有回复。
- `answered=false` 只表示投资者提问，不能称为管理层表态；提问与回复时间分别保存在
  `question_pub_date` 和 `answer_pub_date`。
- `state` 为 `complete/complete_empty/degraded/last_good/missing/not_supported`；只有
  `complete_empty` 才是上游明确返回的空集。

### GET /api/mainforce/longhu/official

读取指定交易日后台已落盘的沪深交易所龙虎榜官方快照。`date` 必须为 `YYYY-MM-DD`。
该接口严格只读精确日期缓存，返回 `cache_only=true`、`network_requested=false`；没有缓存时
返回 404 `official_snapshot_not_cached`，不会在用户请求链触发深交所逐事件 N+1 官网采集，
也不会拿其他日期回填。

`GET /api/mainforce/longhu` 的 `official_verification` 只比较当日股票代码集合，不比较金额。
东财明细不可用时核验状态为 `unavailable`，不得制造 `official_only/eastmoney_only` 差异。

### GET /api/market/overview
```json
{
  "ok": true, "ts": "2026-06-10 14:30:05", "missing": [],
  "indexes": [
    {"code": "sh000001", "name": "上证指数", "price": 3120.55, "change_pct": 0.85, "amount": 543210000000.0}
  ],
  "total_amount": 1850000000000.0,
  "up_count": 3120, "down_count": 1850, "flat_count": 230,
  "limit_up": 68, "limit_down": 5,
  "lianban_height": 6,
  "zhaban_rate": 0.21,
  "limit_up_amount": 32500000000.0
}
```
indexes 至少含：上证指数、深证成指、创业板指、科创50（拿得到几个给几个）。
金额单位一律为 **元**；`change_pct`/`zhaban_rate` 为百分数数值（0.85 表示 0.85%；zhaban_rate 0.21 表示 21% 时给 21.0 —— 统一：**所有 *_pct / *_rate 字段都是百分数数值**）。

`GET /api/market/overview_cached` 在当前刷新未取得 `total_amount` 时，只复用**同一
`data_date`** 最近一次大于 0 的成交额，并返回 `total_amount_cached=true` 与原值时点
`total_amount_asof`；跨数据日不回填。若当天从未取得有效成交额，则 `total_amount=null`，
前端不渲染成交额标签，不显示 `—` 占位。

### GET /api/market/limitup
```json
{
  "ok": true, "ts": "...", "missing": [],
  "stocks": [
    {"code": "002131", "name": "利欧股份", "price": 5.21, "change_pct": 10.02,
     "lianban": 3, "lianban_label": "3连板", "seal_amount": 230000000.0,
     "first_time": "09:42:00", "last_time": "13:21:05", "zhaban_count": 1,
     "amount": 1830000000.0, "turnover_rate": 12.3, "reason": "AI应用",
     "float_mv": 18300000000.0}
  ]
}
```
按 `lianban` 降序排列。

### GET /api/market/limitup/themes
独立的同花顺公开涨停池口径，用于“涨停题材”页面；可选查询参数 `date=YYYYMMDD`。
该接口保留题材、板型、几天几板、封板成功率、回封、首封与封单额，不把数字字段回填到
`/api/market/limitup` 的东财/腾讯主口径。仅当查询日期为今天时，按代码一次批量合并腾讯行情的
当日累计成交额；`amount` 单位为元，`amount_as_of` 是腾讯行情时间 `YYYYMMDDHHMMSS`，
`amount_source=腾讯财经批量行情`。历史日期、行情日期不匹配或批量行情失败时保持 `null`，禁止用
换手率、市值或现价估算。涨停池与成交额分别缓存，成交额随页面刷新重新批量读取。
```json
{
  "ok": true, "date": "20260714", "data_date": "2026-07-14", "missing": [],
  "stocks": [
    {"code": "600664", "name": "哈药股份", "price": 4.08, "change_pct": 9.97,
     "reason_type": "中报预增+基药目录+化学制药+创新药", "limit_up_type": "换手板",
     "high_days": "3天3板", "is_again": true, "suc_rate_pct": 80.0,
     "open_num_ths": 1, "first_time": "09:42", "last_time": "09:42",
     "seal_amount": 110000000.0, "seal_volume": 27000000.0,
     "turnover_rate": 6.5, "circ_mv": 9500000000.0,
     "amount": 1830000000.0, "amount_as_of": "20260714103116",
     "amount_source": "腾讯财经批量行情"}
  ],
  "notes": ["同花顺公开池口径：沪深、创业板、科创板，不含北交所"]
}
```

### GET /api/market/limitup-tracker/dates

返回“涨停追踪”最近最多 20 个涨停基准日。`limit` 范围 1—20，默认 20；
`default_date` 是最近可作为涨停基准日的交易日。日期只来自服务端 A 股交易日历；日历不可用时
仅可返回已经落盘的真实快照日期，并在 `calendar.state=missing`、`missing[]` 说明，禁止根据浏览器
日期或简单工作日猜测。

```json
{
  "ok": true,
  "default_date": "2026-08-13",
  "calendar": {"state": "available", "source": "服务端A股交易日历"},
  "dates": [
    {"date": "2026-08-13", "snapshot_state": "intraday", "has_snapshot": false, "missing_label": null},
    {"date": "2026-08-12", "snapshot_state": "tracking", "has_snapshot": true, "missing_label": null},
    {"date": "2026-08-11", "snapshot_state": "missing", "has_snapshot": false, "missing_label": "快照缺失"}
  ],
  "generated_at": "2026-08-13T10:30:00+08:00",
  "missing": []
}
```

### GET /api/market/limitup-tracker

查询参数 `date=YYYY-MM-DD`；省略时使用上述 `default_date`。响应是一个原子批次：基准日、五项
统计、T+1/T+2 日期、股票长表、来源、时点、缺失和覆盖必须一起生成。前端不得自行推导交易日、
重新排序、计算晋级率或用别日快照回填。追踪只到 T+2，不产生 T+3。

```json
{
  "ok": true,
  "selected_date": "2026-08-10",
  "baseline_date": "2026-08-10",
  "baseline_count": 2,
  "tracking_status": "追踪结果已定稿",
  "t1_date": "2026-08-11",
  "t2_date": "2026-08-12",
  "metrics": {
    "t1_average": {"value": 2.5, "state": "final", "label": "收盘定稿", "sample_size": 2, "total_size": 2, "coverage": 1.0},
    "t1_red_rate": {"value": 50.0, "state": "final", "label": "收盘定稿", "sample_size": 2, "total_size": 2, "coverage": 1.0},
    "t1_promotion_rate": {"value": 50.0, "state": "final", "label": "收盘定稿", "sample_size": 2, "total_size": 2, "coverage": 1.0},
    "t2_average": {"value": 0.0, "state": "final", "label": "收盘定稿", "sample_size": 2, "total_size": 2, "coverage": 1.0},
    "t2_red_rate": {"value": 50.0, "state": "final", "label": "收盘定稿", "sample_size": 2, "total_size": 2, "coverage": 1.0}
  },
  "stocks": [{
    "position": 1, "code": "000001", "name": "示意名称", "industry": "银行",
    "lianban": 2, "lianban_label": "2连板", "baseline_change_pct": 10.01,
    "first_limit_time": "09:31:00",
    "t1": {"date": "2026-08-11", "change_pct": 10.0, "state": "final", "label": "收盘定稿"},
    "t2": {"date": "2026-08-12", "change_pct": -5.0, "state": "final", "label": "收盘定稿"}
  }],
  "data_date": "2026-08-10",
  "generated_at": "2026-08-13T18:00:00+08:00",
  "market_time": "2026-08-10",
  "sources": ["公开涨跌停池", "腾讯财经·前复权日K＋公开涨跌停池"],
  "is_intraday": false,
  "is_final": true,
  "missing": [],
  "degraded_reasons": [],
  "coverage": {"baseline": {"available": 2, "total": 2}, "t1": {"available": 2, "total": 2}, "t2": {"available": 2, "total": 2}}
}
```

五项统计全部由服务端计算，百分数字段仍使用“数值即百分点”口径：

- `t1_average`：冻结基准池各股票在 T+1 的当日涨跌幅算术平均；盘中用同批次真实报价的
  `change_pct`，历史补数用腾讯前复权日 K 的 `T+1 收盘 / 前一交易日收盘 - 1`。
- `t1_red_rate`：T+1 涨跌幅严格大于 0 的股票数 / 可用样本数。
- `t1_promotion_rate`：T+1 同一免费公开涨停池中 `lianban >= 2` 的股票数 / 冻结基准日
  涨停股票数。分子分母同为公开涨停池，沿用 `data.market._promote_rate` 的既有“昨日涨停今日继续板”
  口径；池为空或任一必要证据缺失时为 `null`。
- `t2_average`：冻结基准池各股票在 T+2 的**当日**涨跌幅算术平均，不是从基准日累计收益。
- `t2_red_rate`：T+2 当日涨跌幅严格大于 0 的股票数 / 可用样本数。

指标 `state` 为 `pending/intraday/final/missing/unavailable/not_applicable`，对应显示
“待收盘 / 盘中快照／未定稿 / 收盘定稿 / 数据暂缺 / 暂不可用 / 无样本”。盘中值不写入历史
冻结事件；完整收盘证据才记为 `final`。部分历史日 K 可以逐股显示已经确认的收盘涨跌幅，但聚合
统计在覆盖未完整时仍为 `value=null,state=missing`，绝不用 0 代替缺失。

冻结记录保存在客户 `server.data_dir/limitup-tracker/YYYY-MM-DD.jsonl`。基准池和 T+1/T+2
窗口都是 append-only 事件；首个完整冻结事件不覆盖，不完整观察只允许追加覆盖率更高的新记录。
指定历史日期的涨停池缺失时保持快照缺失，不回退到其他日期。当前交易日盘中只返回瞬时观察；
15:10 后若基准池或到期窗口取得完整收盘证据可冻结，否则仍标数据暂缺。

### POST /api/market/limitup-tracker/backfill

启动最近 20 个交易日的异步补数，返回 HTTP 202 和 `job_id/status=running`。相同实例已有活动任务时
返回同一活动任务并带 `already_running=true`，不重复启动。补数来源只有：指定交易日免费公开涨停池
（基准池、T+1 晋级分子）和腾讯财经前复权日 K（T+1/T+2 当日涨跌幅）；不使用示例值，不跨日期
回填，不把未复权日 K 混入。

### GET /api/market/limitup-tracker/backfill/{job_id}

轮询补数任务。终态：`complete`=所有可补范围完成；`partial`=部分真实数据落盘且仍有缺口；
`failed`=没有取得可保存的目标数据或任务异常；`noop`=没有已经到期且需要补的数据。响应含
`summary.completed/partial/failed/noop`、逐日期 `results[]`、`reason` 和 `finished_at`。
服务重启后内存任务状态不存在返回 404，但已追加的快照事件仍保留。

### GET /api/market/sectors
```json
{
  "ok": true, "ts": "...", "missing": [],
  "sectors": [
    {"kind": "industry", "name": "光模块", "change_pct": 4.2, "leader_name": "中际旭创",
     "leader_change_pct": 9.1, "amount": 32100000000.0, "main_flow": 1500000000.0,
     "up_count": 38, "down_count": 4}
  ]
}
```
`kind`: `industry`(行业) | `concept`(概念)。行业全量 + 概念涨幅前 40。按 change_pct 降序。
行业结构判定要求 `change_pct/amount/up_count/down_count` 同日可用。TDX 行业榜仅在这些字段
覆盖达标时直接采用；当前 TDX `pricevol` 未提供行业成交额时，行业段回退到隔离子进程读取的
同花顺当日行业一览表。该表的 `amount/main_flow` 裸数值按官方亿元口径校验后统一换算为 CNY，
TDX 行业关系仍用于涨停股到行业的精确映射。任何源失败都返回缺失/降级，不跨日补值。

### GET /api/market/auction?codes={codes}&stage=final

从同花顺金融数据服务批量读取 1—100 只 A 股的集合竞价专用快照。`codes` 接受六位代码或带
`.SH/.SZ/.BJ` 后缀的 `thscode`，`stage=live|final`。响应保留 `auction_phase`、
`data_status`、上游 `request_id`、UTC `fetched_at`、`data_timestamp_ms` 和 `field_units`；
其中 `auction_volume` 单位为手，百分比字段均为百分数原值。上游 `null`、`not_ready` 或
`suspended` 保持原状，不用普通成交量或 0 补齐。

### GET /api/market/auction/benchmark?date=YYYY-MM-DD

返回指定日期的竞价短线基准及上游标签。显式指定非交易日时不自动改查前一交易日。

### GET /api/market/sector-catalog?tag={tag}

返回同花顺板块目录。`tag` 仅支持 `cn_concept | region | tszs | industry`。单类别为全量结果；
调用方应按目标匹配，不应在普通交互页轮询全量目录。

### GET /api/market/sector/{thscode}/constituents

返回 `.TI/.SH/.SZ` 板块或指数的**当前**成分股。该结果不是历史调入调出记录，不得据此回写历史成分。

### GET /api/market/sector/{thscode}/history?start_ms={ms}&end_ms={ms}

返回单个板块/指数的未复权历史日线，固定 `1d`，窗口最长 10 年。字段含
`date_ms/open_price/high_price/low_price/close_price/volume/turnover`，响应声明各字段单位和
上游最新数据时间。

### GET /api/market/valuations?codes={codes}

批量返回最多 100 只 A 股的 `pe_ttm/pe_mrq/pb_mrq/ps_ttm/pcf_ttm`。负数是合法业务值，
`null` 表示上游无有效值；均不得取绝对值或补 0。`data_timestamp_ms` 是返回指标中的最新有效
上游时间，不代表五项指标在同一时点更新。

### GET /api/market/top
前 30 涨幅股：`{"ok":true,"ts":"...","missing":[],"stocks":[{"code","name","price","change_pct","amount","turnover_rate","speed"(涨速,可缺)}]}`

### GET /api/market/flow
```json
{
  "ok": true, "ts": "...", "missing": ["north"],
  "main_flow_sectors": [{"name": "通信设备", "main_net": 2500000000.0, "change_pct": 3.1}],
  "main_flow_total": -12500000000.0,
  "note": "北向实时数据已停止公布"
}
```
main_flow_sectors 含净流入前 10 与净流出前 5。

### GET /api/index/{full_code}
指数行情图数据，full_code 形如 `sh000001`（见 overview.indexes[].code）。
```json
{
  "ok": true, "ts": "...", "missing": [], "code": "sh000001", "name": "上证指数",
  "minute_date": "2026-06-10",
  "minute": [{"t": "09:31", "price": 3990.1, "avg": 3990.4, "volume": 120000}],
  "daily":  [{"d": "2026-06-10", "o": 4001.2, "c": 3993.2, "h": 4010.0, "l": 3985.1, "v": 312000000}],
  "weekly": [{"d": "2026-06-06", "o": 3980.0, "c": 4010.0, "h": 4030.0, "l": 3950.0, "v": 1500000000}]
}
```
daily 近 160 根、weekly 近 120 根。45s 缓存。

## 3. 情绪周期

### GET /api/sentiment/today
```json
{
  "ok": true, "date": "2026-06-10",
  "score": 62, "phase": "发酵期",
  "phase_desc": "连板梯队完整，炸板率可控，赚钱效应扩散中",
  "metrics": {"limit_up": 68, "limit_down": 5, "lianban_height": 6, "lianban_count": 18,
              "zhaban_rate": 21.0, "up_down_ratio": 1.69, "total_amount": 1850000000000.0},
  "advice": "可正常参与，优先龙头与补涨低位",
  "missing": []
}
```
phase 枚举：`冰点期` `修复期` `发酵期` `高潮期` `分歧期` `退潮期`。score 0-100。

### GET /api/sentiment/history?days=30
`{"ok":true,"days":[{"date":"2026-06-10","score":62,"phase":"发酵期","limit_up":68,"limit_down":5,"lianban_height":6,"total_amount":1.85e12}]}`
（按日累积快照，最早可能为空数组）

## 4. 个股

### GET /api/stocks/search?q={query}&limit=8

从当前客户实例的本地全 A 股主名录搜索股票候选，不在交互请求中刷新 TDX 或调用外部网站。`q` 支持六位代码/代码前缀、官方中文简称及其任意部分、全拼和拼音首字母；忽略 `*ST/ST/XD/XR/DR/N/C` 等临时名称前缀后再生成附加搜索键，但返回名称始终保留名录中的当前正式值。服务端最多返回 8 条，排序固定为完整代码/名称、前缀、中文包含、全拼、首字母。

```json
{
  "ok": true,
  "state": "ready",
  "query": "gzmt",
  "results": [{"code": "600519", "name": "贵州茅台"}],
  "pinyin_available": true,
  "universe": {
    "count": 5400,
    "complete": true,
    "stale": false,
    "refresh_required": false,
    "as_of": "2026-08-19",
    "source": "TDX 数据 API 股票列表",
    "authority": "tdx_instruments_a"
  },
  "missing": []
}
```

`state=degraded|unavailable`、`universe.stale=true` 或 `missing` 非空时，前端必须显示名录边界；允许保留正向候选，但不得据此硬判股票不存在。候选只负责把中文/拼音转换为六位代码，不替代 `/api/quote/{code}`、诊断、研究、持仓、观察池或模拟订单各自已有的真实身份/行情/风险核验。完整六位代码即使没有候选也可继续提交原业务接口；中文或拼音必须先明确选择候选。

### GET /api/stock/{code}
code 为 6 位数字（自动识别沪深）。
```json
{
  "ok": true, "ts": "...", "missing": ["flow"],
  "quote": {"code": "002131", "name": "利欧股份", "price": 5.21, "change_pct": 10.02,
            "open": 4.80, "high": 5.21, "low": 4.75, "prev_close": 4.74,
            "amount": 1830000000.0, "volume": 351000000, "turnover_rate": 12.3,
            "volume_ratio": 2.4, "pe": 38.2, "total_mv": 36100000000.0, "float_mv": 28000000000.0,
            "high_52w": 6.1, "low_52w": 2.2},
  "minute": [{"t": "09:31", "price": 4.85, "avg": 4.83, "volume": 1200000}],
  "daily": [{"d": "2026-05-12", "o": 4.1, "c": 4.2, "h": 4.3, "l": 4.0, "v": 98000000}],
  "board": {"industry": "互联网营销", "concepts": ["AI应用", "传媒"]},
  "flow": {"main_net": 120000000.0, "super_net": 80000000.0, "large_net": 40000000.0, "medium_net": -30000000.0, "small_net": -90000000.0}
}
```
`minute` 为当日分时（约 240 点）；`daily` 为最近 120 个交易日 K 线。

### GET /api/stock/{code}/enrichment

渐进式个股增强返回行业/概念、资金流、基础财务和集合竞价四个独立区块。集合竞价只读专用
Financial-API；未配置 `HITHINK_FINANCE_API_KEY` 时 `sections.auction` 为
`state=missing,reason=source_not_configured`，不影响其余区块和首屏。

### GET /api/stock/{code}/financials

固定并行读取利润表、资产负债表、现金流量表三张合并报表，不做逐行 N+1。参数：
`period=annual|quarterly`；最近期模式使用 `limit=1..20`（默认 4）；区间模式同时提供
`start_ms/end_ms`，最长 10 年，此时不向上游发送 `limit`。响应的
`statements.income/balance/cash_flow` 各自独立标记状态并保留报告期、发布日期、币种、单位、
上游 `request_id` 与 `null` 未披露语义；部分报表失败时总状态为 `partial`。

### GET /api/stock/{code}/financial-indicators?report=YYYY-[1-4]

返回指定报告期的成长、盈利、偿债、运营、现金流五类指标。`abilities` 是有序数组；指标
`value` 保留上游字符串或 `null`，不擅自转数值或补零。

### GET /api/stock/{code}/valuation

单股扩展估值，字段和缺失语义与 `/api/market/valuations` 相同。

上述新接口均只在服务端读取环境变量 `HITHINK_FINANCE_API_KEY`，凭据不会进入响应、日志或
配置视图。第三方交叉校验不在本次接入范围；现有 TDX/腾讯主行情与身份判定链保持不变。

## 5. 统一对话（SSE 流式；个股问股为确定性规则）

### POST /api/chat  → `text/event-stream`
请求：
```json
{
  "mode": "stock",
  "message": "这票现在能持有吗？冲高回落怎么应对？",
  "code": "002131",
  "industry": "",
  "stock_ask_profile": {
    "holding_status": "held",
    "current_price": null,
    "holding_cost": 12.30,
    "position_pct": 20,
    "scenario": "intraday",
    "horizon": "SHORT_TERM",
    "risk_budget_pct": 1.5,
    "risk_budget_explicit": false,
    "max_position_pct": 70,
    "allow_add": false
  },
  "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
}
```
- `mode`: `free`(自由问答) | `stock`(盘中问股，需 code) | `position`(持仓诊断，后端自动读取已保存持仓) | `chain`(产业链挖掘，需 industry) | `company`(单公司深研，需 code) | `restructuring`(重组拆解，code 可选)
- `stock_ask_profile`：仅 `stock` 模式可用。`holding_status=unheld|held|unspecified`，`scenario=intraday|after_close`，`horizon=NEXT_DAY|SHORT_TERM|SWING`；价格必须 `>=0.01`，仓位 `0.01—100`，风险预算 `0.1—10`，最大仓位 `1—100`。`unspecified` 自动读取当前客户实例的本地持仓；缺成本或仓位保持缺失。用户选择盘中但当前不在工作日 `09:30—11:30 / 13:00—15:00` 连续交易时段时，服务端自动按盘后规则执行。只有 `risk_budget_explicit=true` 时风险预算才是硬约束。
- `perspective_ids` 已退役；提交非空值返回 HTTP 400 `perspective_selection_retired`。
- `stock` 模式不读取图片；提交图片返回 HTTP 400 `stock_ask_images_not_supported`，避免把未参与规则计算的图片伪装为已分析证据。图片解读仅保留在 `free` 模式。
- `data_material_ids`：仅 `free` 模式可用的本轮最终材料 ID 数组；省略时使用服务端确定性预选，显式空数组表示不选任何可选材料，去重后最多 4 项。未知 ID、非数组、超过上限或在其他模式使用均返回 HTTP 400。该选择同时控制是否启动对应数据源和是否进入模型上下文，禁止先全量采集后裁剪。
- `stock` 后端自动采集并核验个股盘口、技术、行业/估值/事件和市场证据，不调用 Hermes；其它需要模型的模式仍按各自契约执行。

`free` 模式请求示例：

```json
{
  "mode": "free",
  "message": "今天更适合进攻还是防守？",
  "data_material_ids": ["sector", "market_context"],
  "history": []
}
```

自由问答始终执行行情与数据时点、风险边界与失效条件、缺失/过期/降级状态、用户持仓四个不可取消底座。用户持仓读取当前客户实例已保存的最多 20 条持仓，并批量补充行情来源、时点、新鲜度与缺失状态；行情失败时仍保留持仓事实并标记 `degraded`，未录入持仓时标记 `missing`，不得猜测。输入一只六位代码或证券名录中的完整名称时，服务端先核验标的，再进入同一受治理单股问股链路；同时识别到多只股票、名称歧义或完整名录中的未知代码时只返回确认提示，不猜测标的、不启动可选材料采集。模型不可用时返回确定性市场答复，并在 `done.data_materials.model_used=false` 中明确标记未调用模型。

未识别到单股时，`fundamental` 不启动个股财务源并标记 `missing`；`technical` 只能使用市场指数行情、`fund_flow` 只能使用全市场资金方向时均标记 `degraded`，上下文同时携带 scope 警告，禁止把它们表述成某只股票的分时/日 K/指标或今日个股资金流。

`stock` 模式的唯一决策权威是 `stock-ask-six-bucket-v2`。服务端先规范化画像，再汇集市场与板块（MV）、固定方法论解释（FV，不计分）、量价与威科夫（WV）、质量/估值/催化（QV）、趋势与相对强度（TV）、技术指标（IV），按周期权重计算 `-2—2` 综合分和数据置信度，再进入 A—E 状态机与 RiskGate。缺失信号不进入分子或分母，无法取得的行业历史、同行估值、官方事件或 60 分钟确认保持 `missing/partial` 并扣置信度，不得补 0。RiskGate 只能否决新增风险，不能把全市场风险改写为公司重大负面，也不能自动要求已有持仓退出。最终 Markdown 由 Python 确定性模板生成；SSE 顺序为 `stage → delta → done`，`done.truncated=false`。

### GET /api/chat/data-materials

返回自由问答材料目录、四个系统必需底座以及上限：`max_auto_selected=3`、`max_selected=4`。必需底座 ID 为 `quote_time | risk_boundary | quality_state | holdings`；第一版可选 ID 固定为 `technical | fund_flow | sector | market_context | fundamental | events`。分时、日 K 和技术指标归入 `technical`，公告、互动和新闻归入 `events`，不暴露原始字段级复选框。目录中的 `availability` 只表示发送前支持状态，不表示本轮数据已经可用。

### POST /api/chat/data-materials/preselect

请求 `{"mode":"free","message":"为什么上涨？"}`，返回 `{"ok":true,"preselected_ids":["technical","fund_flow","sector"],"max_selected":4}`。预选由固定关键词与组合意图规则产生，最多 3 项，不调用模型、不启动采集。用户发送时提交的 `data_material_ids` 才是最终范围。

### 会话与历史

- `GET /api/chat/conversations?mode=free|stock`：按模式返回会话；省略 `mode` 保持旧调用方的全量列表。
- `POST /api/chat/conversations`：以 `{"mode":"free"}` 或 `{"mode":"stock"}` 建立会话。
- `GET /api/chat/conversations/{cid}`：读取元数据和追加式消息。
- `POST /api/chat/conversations/{cid}/messages`：完整一轮结束后追加消息；自由问答 assistant 消息可附服务端白名单清洗后的 `data_materials` 报告。新问股历史不再写入模型视角元数据；既有历史事实不覆盖。图片只保留 `has_image`，不把 base64 写入历史。
- `DELETE /api/chat/conversations/{cid}`：删除指定会话。前端必须按 `mode` 隔离列表和活动会话；模式/会话切换时取消在途 SSE，旧响应不得写入新会话。

`company` 模式把行情、板块、龙虎榜、财务、市场资金、市场总览和情绪拆成独立并行有界请求；任一增强源超时只降级对应维度。模型正文完成治理后，服务端固定追加行情/估值、最近报告期财务、确定性技术结构、官方公告与互动问答附录。长篇 `chain` 请求使用至少 16000 token、`company` 使用至少 20000 token 的专用生成预算，模型请求单次上限为 300 秒。长篇请求发生模型读取超时后不重放整次请求，避免总等待翻倍。调用方显式传入 `max_tokens` 时仍以显式值为准。

Vue 正式产业链/公司研究页不再直接持有本接口的长 SSE：它们使用下述持久化后台任务接口，页面只轮询短状态请求。`POST /api/chat` 的 `chain` / `company` 仍为兼容调用方保留。

`chain` 模式的数据与降级约定：

- 候选池优先使用 TDX 板块成分；板块名称或别名存在精确匹配时，只采用精确匹配集合，不再并入仅包含关键词的宽泛板块（例如研究“电力”不会自动合并“绿色电力”）。没有精确匹配时才使用包含匹配。TDX 返回空成分时，后端自动回退到 AKShare 暴露的东方财富公开行业/概念板块及其成分。该回退不需要 API Key。
- 板块成分只证明“属于该公开集合”，不证明公司处于产业链哪个环节、相关收入占比、订单或国产替代进度。
- 产业链底座先读取低频板块目录、成分关系和成员名称，再对报告候选执行一次有界批量报价（最多 `company_limit` 只、3 秒聚合预算），不做逐股 N+1。可得的价格、涨跌幅、成交额和估值只进入结构化证据附录；`trade_timing` 语义仍固定为 `not_evaluated`，需要交易判断时另走当时行情与风险闸。
- 本地 TQ 兼容桥调用系统板块时保留完整板块代码（例如 `880656.SH`），并从 `sector_members.payload.Name` 透传成员名称。成员为零时仍可基于明确标注的公开产业常识完成行业级分析，但公司映射必须保持为空，不得生成受益标的。
- 模型可用时，响应固定为内容优先的八段报告：核心需求与驱动力、上中下游全景、利润池与价值分配、核心卡点与竞争壁垒、国产替代路径、A股映射思路、兑现节奏与观察指标、风险与失效条件；正文后由服务端追加候选公司行情/估值、经营证据、公告线索、数据时点和缺口附录。
- 模型没有返回可安全使用的完整终态（包括超时、`finish_reason=length` 或正文为空）时，接口只返回“产业链研究未完成”的状态、失败原因和已取得的可靠证据摘要；不再用通用八段骨架伪装成研究结论，也不追加大篇幅候选公司表。前端应将该状态标记为降级结果。
- 正文不逐段显示 `public_knowledge`、`待公告核验`、`数据不全` 或数值占位。真实性边界只在报告末尾用一条研究说明集中表达。
- 产品代际名称（例如 `800G`、`1.6T`）可以作为技术路线名称保留；未由冻结证据支持的比例、金额、毛利率区间与精确周期整行移除，不显示新的验证占位。
- 公司映射段由服务端按冻结候选池生成，模型不得增加候选池外 A 股公司；候选名称只作为后续单公司研究入口，不按涨跌、成交额或代码顺序解释为投资排名。
- `message` 可包含用户指定的研究重点；前端默认请求完整上下游报告，并把产业研究与交易判断分开。
- 后端仅对证据核验队列前 5 家公司异步刷新巨潮/交易所公告、深交所互动问答和免费基础财务；冷启动首轮可显示 `refreshing`，后续请求复用 30 分钟进程缓存与既有 last-good 快照。普通请求不会逐股同步抓取整个板块。
- 公告标题或互动问答命中产业关键词时只标记为 `evidence_clue`；没有公告/年报正文和分部收入证据时，不得标记为已验证产业角色或业绩兑现。
- 以上公开源均不新增密钥配置；上游不可用时字段保持 `missing`、`refreshing` 或明确降级状态，不用模型生成值补齐。

### 产业链 / 单公司持久化后台研究任务

#### POST /api/research/runs

产业链请求：

```json
{"kind":"chain","industry":"电力","focus":"重点分析利润池与电网投资"}
```

单公司请求：

```json
{"kind":"company","code":"000539"}
```

接口完成参数校验和任务落盘后立即返回 HTTP 202，不等待模型正文：

```json
{"ok":true,"reused":false,"run":{"run_id":"rr_...","kind":"chain","status":"queued","subject":"电力","stage":"任务已进入后台队列"}}
```

同类型、同标的和同重点已有活跃任务时返回原任务并标记 `reused=true`；同类型的其他标的仍在运行时返回 HTTP 409 和 `active`，避免并发重型生成互相挤占。任务状态枚举：`queued | running | canceling | completed | failed | canceled | interrupted`。

后台任务独立于浏览器请求。切页、刷新、关闭标签页或短轮询超时只停止前端查询，不取消模型生成。任务和正文按客户 `data_dir/research_runs/{kind}/` 隔离并原子落盘；服务重启后已完成结果仍可读取，重启瞬间尚未完成的任务标为 `interrupted`，不会伪装成成功或覆盖以前的成功报告。

#### GET /api/research/runs/latest?kind=chain|company

返回 `active`、`latest` 和 `latest_completed`。新任务失败或被取消时，`latest_completed` 继续指向最近一次成功报告，前端必须展示该成功结果并同时提示最新失败，不得用失败记录覆盖正文。

#### GET /api/research/runs/{run_id}

轮询单个任务的状态、阶段、正文、错误码和时间。Vue 正式页每 2 秒轮询一次，每次 HTTP 查询上限 15 秒；查询失败会继续重试，后台任务不受影响。

#### POST /api/research/runs/{run_id}/cancel

显式停止后台任务。只有用户点击“停止”才调用；组件卸载、路由切换和浏览器中止请求不得隐式调用。

#### DELETE /api/research/runs/{run_id}

删除一条已经结束的产业链或单公司研究记录。`completed | failed | canceled | interrupted` 可以删除；`queued | running | canceling` 返回 HTTP 409，必须先显式停止任务。成功后服务端删除客户隔离目录中的对应任务文件，并返回删除后的 `active`、`latest` 和 `latest_completed`。不存在的任务返回 HTTP 404。

Vue 历史列表删除前必须明确确认不可撤销；服务端删除成功后同步移除浏览器本地历史副本。旧版本遗留且没有 `rr_...` 服务端任务 ID 的条目只删除本地副本。删除当前查看项后显示剩余的最近完成记录；没有剩余记录时回到真实空态。

SSE 事件流，每条 `data:` 一个 JSON：
```
data: {"stage": "采集实时行情数据…"}
data: {"stage": "Hermes 生成中…"}
data: {"delta": "从分时看，"}
data: {"delta": "该股……"}
data: {"done": true, "resolved_mode": "free", "data_materials": {"model_used": true, "required": [], "optional": []}}
```
出错：`data: {"error_code":"chat_generation_failed","error":"AI 生成服务暂不可用，本次回答未完成；请稍后重试。","data_materials":{...}}` 后流结束。自由问答的 `done` 或 `error` 均携带真实材料终态：每项区分 `complete | degraded | missing | not_selected`、`injected`、`data_date`、`source`、`stale` 和 `reason`；只有真实进入模型上下文的可用/降级材料才可标为 `injected=true`，用户勾选本身不代表已注入。
异常详情只写服务端日志；SSE 永不返回上游 URL、Key、本机路径或原始异常文本。

**阶段事件（`stage`）**：在**首个 `delta` 之前**，后端按耗时阶段先发若干 `{"stage": "<中文短语>"}` 事件——
进入数据采集前发 `{"stage":"采集实时行情数据…"}`，数据就绪、调 Hermes 前发 `{"stage":"Hermes 生成中…"}`。
前端流式占位气泡在收到首个 `delta` 前，显示当前 stage 文案 + 已等待秒数（每秒自增）；收到首个 `delta` 后切回正文渲染。
**未知事件类型必须被忽略（向后兼容）**。diy_run 的 SSE（§12）同此机制，阶段文案为 `采集数据…` → `Hermes 生成中…`。

## 6. 报告中心

rtype 枚举与含义：
| rtype | 名称 | 定时 |
|---|---|---|
| morning | 早盘内参 | 08:50 |
| midday | 尾盘决策草案（内部，不推送） | 14:45 |
| tail | 尾盘决策定稿（唯一对客版本） | 14:52 |
| close | 收盘复盘 | 15:30 |
| evening | 次日策略（确定性+高盈亏比） | 19:30 |
| trend | 每日3只趋势票 | 19:30 |

### GET /api/reports/{rtype}?date=2026-06-10
`{"ok":true,"rtype":"close","date":"2026-06-10","title":"收盘复盘","content_md":"# ...","created_at":"2026-06-10 15:31:02","pushed":true}`
不存在时 `{"ok": false, "error": "该日期无此报告"}`（HTTP 仍 200）。

定稿报告可带 `claim_validation`。价格硬闸通过但执行过局部降级时，
`claim_validation.sanitation.replacements[]` 记录受影响行的 `line/format/code/name/blocked_reasons`，
`blocked_claims[]` 记录失败价位及候选股票身份；不保存已拒绝的完整原始建议行。
Markdown 表格中的失败行仍保持相同列数，只移除未通过校验的点位，避免后续合法候选脱离表格显示。

### POST /api/reports/{rtype}/generate → `text/event-stream`
立即生成（采集真实数据 → 调 Hermes 流式生成 → 自动落盘保存）。SSE 同 /api/chat 格式，`done` 事件附 `{"done":true,"saved":true,"date":"2026-06-10"}`。
**阶段事件（`stage`）**：首个 `delta` 前先发 `{"stage":"采集行情数据…"}`（采集前）→ `{"stage":"生成报告中…"}`（调 Hermes 前）；前端处理同 §5。

### POST /api/reports/{rtype}/push
把已生成报告的**客户关键摘要**推送到飞书。完整 `content_md` 仍是系统报告中心/PDF 的唯一正式定稿，飞书不会再发送全量正文；摘要按报告类型从已通过治理的原文章节确定性摘取，不调用模型二次改写，并保留原报告内容版本标识。结构异常的旧报告只发送有界节选，不回退为全量长文。

若客户配置了合法的 `server.public_base_url`，飞书互动卡片末尾增加“查看完整报告”按钮；经 Hermes 网关投递到飞书时附同一纯文本链接。报告类型映射到正式 Vue 路由：`morning → #/premarket?tab=report`、`tail → #/tail?tab=decision`、`close → #/review?tab=close`、`evening → #/review?tab=tomorrow`、`trend → #/history?tab=reports&section=trend`。未配置、配置非法或内部 `midday` 草案均不生成链接；系统不通过网卡、外部 IP 服务或请求 Host 猜测公网地址。

首次推送 body 可选 `{"date":"2026-06-10"}`。已经成功推送过的同版报告默认保持幂等；用户明确点击“再次推送”时，body 必须传 `{"date":"2026-06-10","force_resend":true,"resend_id":"客户端生成的8-80位幂等键"}`，服务端会创建独立、可审计的手工重发 intent，相同 `resend_id` 的网络重试不会重复外发。返回 `{"ok":true,"resent":false|true}` 或 `{"ok":false,"error":"..."}`。
`date` 非法（非字符串或不符 `YYYY-MM-DD`）时返回 HTTP 400 `{"ok":false,"error":"日期格式应为 YYYY-MM-DD"}`。

## 7. 持仓

### GET /api/holdings
`{"ok":true,"portfolio_total_asset":null,"holdings":[{"code":"002131","name":"利欧股份","cost":4.5,"shares":10000,"industry":"互联网","weight_pct":null,"market_value":45000,"stop_loss_price":4.1,"take_profit_price":5.6,"horizon":"short","entry_type":"breakout","entry_date":"2026-08-11","note":"突破后跟踪"}]}`

`horizon` 只允许 `general | overnight | ultra | short | swing | trend`；`entry_type` 只允许 `general | breakout | pullback | event`。客户风险线、客户目标价和买入日期可为空。旧持仓缺少这些字段时按 `general/general/空日期` 读取，不猜测日期；缺日期时仍可给基础风险提示，但不启用动态吊灯和时间规则。
### PUT /api/holdings
body `{"holdings":[...]}`（整体覆盖保存）→ `{"ok":true,"holdings":[...]}`。当前页面不提交账户总资产；单票持仓占比仅按 `单票当前市值 / 全部持仓当前市值` 展示，不代表含现金账户仓位。
客户端提交的 `industry/sector` 不受信任，服务端按证券代码从免费公开个股主数据重新核验并覆盖；
取不到行业时保存为空，所有新增风险动作由风险闸 fail closed。
最多 50 条且代码唯一；成本、数量必须是正的有限数字；可选风险线/目标价填写后必须大于 0，且风险线低于目标价；枚举和日期非法时返回 HTTP 400 `{"ok":false,"error":"持仓数据不合法：<原因>"}`，整批拒绝、不部分落盘。新增时先由 `/api/quote/{code}` 核验证券，编辑时不允许改代码。保存成功后页面通过显式风险刷新重新计算；周期、买入逻辑、成本或买入日期变化会改变运行指纹并重置对应动态状态。

### GET /api/holdings/risk
只读取最近后台评估快照，不触发日 K、市场、板块或事件 N+1 采集：

`{"ok":true,"shadow_mode":true,"push_enabled":false,"rule_version":"holding-risk-v1-shadow-r2","market_context":{},"rows":{"000001":{"state":"yellow","action":"暂停加仓，等待确认","summary":"价格接近风险线","reasons":["距离算法风险线不足 0.5×ATR"],"horizon":"short","entry_type":"general","levels":{"algorithm_stop":9.2},"metrics":{},"sector":{},"missing":[],"push_eligible":false}}}`。

状态固定为 `blue | yellow | orange | red | gray`，分别表示继续持有、暂停加仓等待确认、减仓或止盈 1/3、退出当前可卖持仓、数据不足。服务端按客户风险线、重大事件、算法有效破位、趋势确认、止盈滞涨、时间风险的优先级选唯一动作；LLM 不参与价格、风险线或动作。

算法使用至少 60 根完成的前复权日 K、Wilder ATR14、MA5/20/60、价格结构、沪深300相对表现、市场环境和只读缓存事件风险。初始逻辑线按突破/回踩/事件/未指定四种买入逻辑计算；动态结构线和吊灯线只升不降。缺价格、成本、历史或指标时返回灰色和明确 `missing`，不得补值。实时价不可用时可退到最近完成日线收盘，并在 `price_basis` 中说明。

后台刷新失败时：有 last-good 则保留原动作、风险线和原 `evaluated_at`，增加 `stale=true`、`refresh_failed_at` 和 `refresh_error`；无缓存则返回灰色缺失态。两种情况都不会把旧数据伪装成新结论。

### POST /api/holdings/risk/refresh
显式运行全部持仓评估并返回与 GET 相同的 `rows` 结构。后台也按5分钟周期执行；`max_instances=1`，不会叠加刷新。当前仍是影子模式：`shadow_mode=true`、`push_enabled=false`、每条 `push_eligible=false`，不会触发正式飞书持仓通知。

旧版 `/api/holdings/{code}/risk-plan/reset` 与 `/api/holdings/{code}/adjustment` 路由为已有集成保留，但当前“我的持仓”页面不再调用；新模型的成本、周期、买入逻辑和客户线统一通过整体 `PUT /api/holdings` 编辑并立即刷新影子卡。

## 8. 知识库

### GET /api/knowledge
`{"ok":true,"schema_version":"KnowledgeRule.v1","summary":{"approved":3,"draft":1},"review_queue_count":1,"items":[...]}`

每条包含 `status` (`draft/approved/rejected/superseded/legacy_unverified`)、`version`、
`origin`、`source_report_ids`、`evidence_ids`、`sample_count` 和审批时点。**只有
`approved` 会进入 `knowledge_digest()` 并注入后续 AI**；自动复盘只生成 `draft`。
category 枚举：`交易原则` `亏损禁区` `擅长模式` `不擅长模式` `仓位规则` `经验教训` `选股偏好` `复盘笔记` `舆情留档`。
### POST /api/knowledge  body `{category,title,content}` → `{"ok":true,"id":"..."}`
### GET /api/knowledge/review-queue → 待审 `draft/legacy_unverified`
### POST /api/knowledge/{id}/approve
body 可选 `{source_report_ids,evidence_ids,sample_count}`。无真实来源或 `sample_count<1` 时拒绝批准。
### POST /api/knowledge/{id}/reject  body `{reason}`
### POST /api/knowledge/{id}/supersede
以已批准新版原子替代旧版，旧版内容和版本保留。所有动作追加到 `knowledge/audit.jsonl`。
状态文件与审计日志之间使用 `knowledge-transaction-intent.v1` durable intent；`prepared`
事务在启动时回滚，`committed` 事务仅清理标记，避免进程崩溃后出现“状态已批准但无审计”。
### DELETE /api/knowledge/{id} → `{"ok":true}`

## 9. 推荐事件与确定性风险闸

### POST /api/risk/evaluate

纯规则、无网络/无 AI 的 `risk-gate.v2`。输入 `candidate/market/portfolio/liquidity/event`，
输出 `allow/reduce/veto`、最终动作、仓位上限、原因码、规则版本和规范化输入 SHA-256。
同一输入必须得到同一输出。

### POST /api/recommendations

新增推荐前，服务端会：

1. 回读并校验 `price_evidence_id` 的代码/字段/数值/质量；通常要求
   `quality_status=ok`。唯一例外是腾讯行情作为主源失败后的备用源，且该点必须同时满足
   `freshness=live`、`batch_status=complete`、无跨源冲突，并且唯一质量原因是
   `fallback_source`；此时仍保留 `quality_status=degraded` 的真实标签，但允许绑定推荐价。
   含 `batch_partial`、`stale`、`event_time_inferred` 或其他降级原因的点仍禁止绑定；
2. 用服务端 pulse 风险档和当前持仓执行最终风险闸，不信任客户端 `risk_decision`、
   `portfolio`、`liquidity`、`event` 或行业自由文本；候选和全部持仓行业均按代码从服务端主数据覆盖；
3. `veto` 返回 HTTP 409 且不落库；`reduce` 使用风险闸缩减后的仓位/信心；
4. 成功时同步写入旧表和 append-only `RecommendationEvent 1.0`。

双写前先落盘 `recommendation-dual-write-intent.v1`。中途故障保留 intent，下次调用幂等恢复；
`client_request_id` 支持安全重试，不会重复事件或旧记录。

### GET /api/recommendation-events
只读 append-only 推荐创建事件；不伪造旧数据回填。

### POST /api/recommendations/verify
仅在目标交易日 15:00 后结算。`buy/add/candidate` 按上涨验证，
`avoid/reduce/exit` 按下跌验证；方向不明始终 `pending/degraded`，不计入胜率。
只有服务端校验、价格证据完整且方向可判定的记录会标记 `_score_eligible=true` 并进入统计。

### 交付事务、回执与上线门禁

- `GET /api/delivery/readiness`：只做渠道/目标/outbox 预检，不发送；任一待人工核验事务存在时
  `launch_ready=false`。
- `POST /api/delivery/preflight`：与 readiness 同语义的主动预检。
- `GET /api/delivery/daily-gate?date=YYYY-MM-DD`：核对 morning/tail/close/evening/trend
  以及盘前/盘后 PDF 七项必交付物。
- `GET /api/delivery/outbox?limit=200`：读取 `delivery-outbox.v2`，状态包含
  `queued/sending/accepted/delivered/partial/unknown/failed_retryable/failed_terminal/acknowledged`。
- `POST /api/delivery/{delivery_id}/retry`：重发已知失败事务；`sending/unknown/partial`
  必须同时提交 `confirm_possible_duplicate=true` 和人工 `review_note`，防止盲目重复发送。
- `POST /api/delivery/{delivery_id}/acknowledge`：人工在外部平台核验确已接收后提交
  `{"review_note":"核验依据"}`，把歧义事务关闭为 `acknowledged`。
- `GET /api/delivery/receipts`：append-only 尝试回执。`accepted` 只表示平台接收，
  不得在 UI 中误写成最终已读/已送达。

所有自动报告和 PDF 在外发前先写确定性 outbox intent；进程若在外发后、写回前崩溃，
事务保留 `sending` 并阻断上线，必须由人工确认已接收或确认重复风险后重试。

## 10. 蒸蛋日档案

### GET /api/archive/dates
`{"ok":true,"dates":["2026-06-10","2026-06-09"]}`
### GET /api/archive/{date}
```json
{"schema_version":"daily-archive-v2", "ok": true, "date": "2026-06-10",
 "reports": {"morning": {"title": "早盘内参", "content_md": "...", "created_at": "..."}},
 "sentiment": {"score": 62, "phase": "发酵期"},
 "advice_review": {
   "schema_version":"daily-advice-archive.v1",
   "historical_read_only":true,
   "realtime_recalculation":false,
   "execution_scope":"entry_trigger_only_no_broker_execution",
   "candidates":[], "formal_recommendations":[], "executions":[], "reviews":[]
 },
 "holdings":[], "knowledge_today":[],
 "archived_at": "2026-06-10 22:00:01"}
```
日档案首次落盘后只读；重试返回原始快照，不用当前行情/建议账本倒推历史。
`executions` 只表示当时已冻结的入场条件触发证据；没有券商回执时不等于真实成交。
旧版档案保持原样兼容，前端必须明示“当时未冻结建议账本”。

## 11. 设置

### GET /api/settings
```json
{"ok": true,
 "settings": {
   "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
   "feishu_secret": "",
   "public_base_url": "https://wudi.example.com",
   "push_enabled": true,
   "hermes_base_url": "http://127.0.0.1:8874/v1",
   "hermes_model": "hermes-agent",
   "schedule": {"morning": "08:50", "midday": "14:45", "tail": "14:52",
                "close": "15:30", "evening": "19:30", "archive": "22:00"},
   "scheduled_push": {"morning": true, "tail": true, "close": true,
                      "evening": true, "archive": true}
 }}
```
### PUT /api/settings  body 为 settings 子集 → `{"ok":true}`（热生效并持久化）
`scheduled_push` 只接受 `morning | tail | close | evening | archive` 五个布尔字段，支持单字段增量更新，未保存过时全部默认为 `true`。该开关只控制对应定时任务的自动飞书推送：关闭后任务仍生成报告或存档，显式手动推送不受影响；`push_enabled=false` 仍是所有自动推送的总门控。
### POST /api/push/test
无 body 时发送通用飞书测试消息；body `{"kind":"holdings_risk"}` 时通过与真实持仓告警相同的 `push_text` 通道发送固定演示文案。演示文案不读取真实持仓、行情或风险状态，并明确标记“不代表任何真实持仓结论”。返回 `{"ok":true,"msg":"..."}` / `{"ok":false,"error":"..."}`。

## 前端约定

- 前端为纯静态 SPA，由后端 FastAPI 挂载在 `/`；vendor 库走相对路径 `vendor/echarts.min.js`、`vendor/marked.min.js`（已下载到 frontend/vendor/）。
- 中国市场颜色约定：**红涨绿跌**（上涨 #f0445a 系，下跌 #15b86a 系）。
- 交易时段（09:25–15:05）行情页自动轮询刷新（10–20s），非交易时段停止。
- 所有金额展示做人民币人性化（万/亿）。

---

# 新增功能（v2：市场特征 / DIY / 多市场）

## 12. 市场特征 features（题材赚钱效应/拥挤度/龙头/主线）

### GET /api/features/money-effect
题材赚钱效应排名（行业+概念）。
```json
{"ok":true,"ts":"...","data_date":"2026-06-11","missing":[],
 "themes":[{"name":"光模块","kind":"concept","change_pct":4.2,"money_score":38.5,
   "zt_count":6,"max_lianban":3,"up_count":38,"down_count":4,
   "leader_name":"中际旭创","leader_change_pct":9.1,"main_flow":1.5e9,"amount":3.2e10}],
 "losers":[{...}]}
```
money_score 为带符号赚钱分（正=赚钱/负=亏钱）；zt_count 概念板块可能为 null。

### GET /api/features/crowdedness
题材交易拥挤度（高=过热/反转风险）。
```json
{"ok":true,"ts":"...","data_date":"...","missing":[],
 "sectors":[{"name":"半导体","kind":"industry","change_pct":3.1,"amount":3.2e10,
   "amt_share":6.4,"zt_count":5,"crowd_score":92,"level":"过热拥挤"}]}
```
level∈{过热拥挤,偏拥挤,正常,冷清}；crowd_score 0-100 横截面分位。

### GET /api/features/leaders
情绪龙头梯队。
```json
{"ok":true,"ts":"...","data_date":"...","missing":[],
 "kongban":{"height":4,"stocks":[{"code","name","lianban","reason","seal_amount"}]},
 "ladder":[{"lianban":4,"label":"4连板","count":1,"stocks":[{"code","name","reason","seal_amount","turnover_rate","first_time","is_leader":true}]}],
 "popularity":[{"code","name","lianban","seal_amount","seal_ratio","reason"}]}
```

### GET /api/features/mainline
市场主线（多日累积）。数据不足时返回 note + today_top。
```json
{"ok":true,"ts":"...","missing":[],"history_days":5,
 "mainlines":[{"name":"光模块","kind":"concept","days_on_board":4,"avg_money_score":33.2,"cum_main_flow":5.2e9,"strength":78.5,"recent_score":38.5}],
 "switch_signal":"今日榜首『X』与近5日主线『Y』不一致...","note":null,"today_top":[]}
```

### GET /api/features/institutional
机构容量票（AI/半导体，流通市值达标+资金+趋势）。
```json
{"ok":true,"ts":"...","data_date":"...","missing":["个股主力净额(未逐票取)"],
 "stocks":[{"code","name","concept":"CPO","price":120.5,"change_pct":4.1,
   "float_mv":2.8e10,"amount":1.8e9,"turnover_rate":5.2,"main_net":null,"trend":"多头","score":82}]}
```
> 注：`main_net` 暂不逐票拉取（全市场快照不含单票主力净额），恒为 `null` 并在 `missing` 标注；筛选/打分不依赖它。空缺即缺数据，绝不以 0 或估值冒充。

### GET /api/forecast/mining

业绩预告掘金。`period` 可选，格式为 `YYYYMMDD`；默认按北京时间选择当前中报或年报报告期。
数值只来自公开业绩预告汇总，服务端执行确定性阈值、披露时点和基数陷阱规则。

实时公开源成功时返回：

```json
{"ok":true,"state":"complete","degraded":false,"stale":false,"used_last_good":false,
 "ts":"2026-07-28 10:00:00","data_ts":"2026-07-28T10:00:00+08:00","period":"20260630",
 "snapshot_age_seconds":0,"stats":{"总数":100,"掘金池":12,"陷阱池":3},"items":[],"traps":[],"missing":[]}
```

公开源失败时，只允许回放同客户、同报告期且不超过七天的最近非空成功快照：

```json
{"ok":true,"state":"last_good","degraded":true,"stale":true,"used_last_good":true,
 "ts":"2026-07-28 14:30:00","data_ts":"2026-07-28T10:00:00+08:00","period":"20260630",
 "snapshot_age_seconds":16200,"source_error":"业绩预告公开源不可用",
 "missing":["业绩预告公开源当前不可用，展示最近成功榜单（距采集约16200秒）"],"items":[],"traps":[]}
```

`ts` 是本次响应计算/回放时点，`data_ts` 是榜单最近成功采集时点，两者不得混用。
成功但真实空数组返回 `state=complete_empty`，不会用历史快照冒充当前结果，也不会覆盖此前非空 last-good。
没有合格快照时返回 `ok=false,state=missing,items=[],traps=[]`。快照按 `server.data_dir` 客户隔离，不跨报告期复用。
`state=last_good` 只供页面显式降级展示，不进入每日主动推送，避免把旧榜伪装成当天新榜。

## 13. DIY 研究工作流 diy

### GET /api/diy/templates
`{"ok":true,"templates":[{"id":"d_xxx","name":"龙头辨识","target_type":"stock","prompt_template":"...","created_at":"..."}]}`
### POST /api/diy/templates  body `{name,target_type(stock|theme|free),prompt_template}` → `{"ok":true,"id":"d_xxx"}`
prompt_template 中可用占位符：`{code}` `{theme}`（运行时替换）；真实数据由后端自动注入。
### DELETE /api/diy/templates/{id} → `{"ok":true}`
### POST /api/diy/run → text/event-stream
body `{"template_id":"d_xxx"}` 或 `{"prompt_template":"..."}`，加 `{"target_type":"stock","code":"002131"}` 或 `{"target_type":"theme","theme":"光模块"}`。
后端按 target_type 注入个股/题材真实数据 → 调 Hermes 流式。SSE 同 /api/chat（含 `stage` 阶段事件 / delta / done / error）；阶段文案为 `采集数据…` → `Hermes 生成中…`（见 §5）。

## 14. 多市场新闻 news

### GET /api/news/a-share
返回真实财经快讯、确定性 A 股实体映射和可选的追加式盘面观察。完整响应采用
stale-while-revalidate：存在六小时内的非空最近成功快照时先返回快照；超过 30 秒后只启动一个
后台刷新，不阻塞当前 GET。没有有效快照时才同步执行真实冷采集。空结果、失败结果和示例数据不得
覆盖最近成功快照。

```json
{
  "ok": true,
  "fetched_at": "2026-08-01T16:24:51+08:00",
  "items": [{"event_id": "...", "title": "...", "a_share": {"state": "mapped"}}],
  "pipeline": {
    "source_state": "degraded",
    "entity_mapping_state": "complete",
    "market_observation_state": "disabled"
  },
  "snapshot": {
    "served_from_snapshot": true,
    "status": "fresh",
    "saved_at": "2026-08-01T16:24:52+08:00",
    "age_seconds": 12,
    "refreshing": false
  }
}
```

`snapshot.status`：`live` 表示本次同步真实构建；`fresh` 表示 30 秒内完整快照；`stale` 表示超过
30 秒、页面应明确标为沿用数据。`refreshing=true` 表示后台刷新已启动或正在进行。服务端快照按客户
`data_dir/news/a-share-page-last-good.json` 隔离；前端还可在当前浏览器会话内保留最近成功响应，重进
页面时先显示再刷新。

新闻事件观察的在线账本位于客户 `data_dir/news_events/news_observation.sqlite3`，事件与观察历史
仍为 append-only；`observation_current` 只是按事件和检查点维护的派生索引。旧版
`events.v1.jsonl`、`observations.v1.jsonl` 由独立 Worker 流式导入并原样保留，在线追加、去重、
汇总和 API 查询不再扫描 JSONL。健康面板只读取 Worker 在采样完成时物化的摘要。

### POST /api/news/a-share/archive

请求 `{"event": <GET /api/news/a-share 返回的单条事件>}`，将用户明确选中的事件保存为
`KnowledgeRule.v1` 草稿，分类为 `舆情留档`、来源为 `sentiment_event_archive`。服务端只保留有界的
标题、正文、事件/抓取时间、A 股映射与 HTTP(S) 原文来源，并以事件正文、时间和聚合来源计算稳定
`archive_key`。同一事件重复提交幂等返回既有条目：
`{"ok":true,"id":"k_xxx","created":false,"item":{...}}`。留档不修改新闻完整快照，也不会让
未经人工批准的新闻直接进入 AI 知识摘要；条目可在“历史与知识 → 知识库”审核和长期查看。

### GET /api/news/multi
```json
{"ok":true,"ts":"...","missing":["港股新闻"],
 "cn":[{"source":"财联社","title","content","time"}],
 "us":{"indexes":[{"name":"纳斯达克","close","change_pct"}],"tech_stocks":[{"name","change_pct"}]},
 "hk":{"indexes":[{"name":"恒生指数","close","change_pct"}]}}
```
> 注：美股新闻 `us.news` 暂未实现（无数据源接入），后端恒返回 `[]`，故已从契约移除；前端也不消费该字段。

## 前端新增页面
- `#/features` 题材特征看板：赚钱效应榜 + 拥挤度 + 主线 + 龙头梯队（OBSIDIAN 终端风，红涨绿跌，等宽数字，方角面板+角标）。
- `#/institutional` 机构容量票（AI/半导体筛选表）。
- `#/diy` DIY 研究工作流（模板管理 + 运行 SSE 流式渲染）。
- 多市场新闻已并入 `#/dashboard` 底部「多市场异动 · GLOBAL MARKETS」卡片：隔夜美股指数+科技股映射、港股指数、A股快讯滚动列表（红涨绿跌，缺数据标「数据不全」）。

## 15. 数据源健康面板 datasources

### GET /api/datasources
汇总所有数据源的真实字段链路、用途、角色、最近调用状态与延迟。该 GET 只复制进程内
健康快照：不会同步访问 Hermes、TDX、交易所或证据库，也不会返回中继 API、代理地址、
Webhook 或任何凭据。普通数据源延迟继续来自被动埋点；Hermes/TDX 与本地证据统计由
后台循环并行刷新。Hermes、TDX 和本地统计分别受 5 秒、7 秒、3 秒上限约束，任何单项
超时只写入稳定 `error_codes` 并保留可展示快照，不把整页变成 HTTP 超时。同步探测使用各自
单工作线程和单飞 future；外层超时后底层尚未结束时，下一轮不得再次提交，因此不会堆积健康线程。

`health_refresh.status` 为 `pending/ready/degraded`；`request_mode` 固定为
`passive_snapshot`。`infra.*.status=unchecked` 表示后台尚未完成首次检查，不等于失败。
```json
{
  "ok": true, "source_contract": "field-route-v2", "active_policy": "free_only", "ts": "2026-06-11 14:30:00",
  "health_refresh": {"status":"ready","request_mode":"passive_snapshot","last_started_at":"2026-06-11T14:29:58+08:00","last_finished_at":"2026-06-11T14:30:00+08:00","duration_ms":1820,"error_codes":[]},
  "background_worker": {"status":"ready","connected":true,"pid":4321,"last_seen_at":"2026-06-11T14:29:59+08:00"},
  "infra": {
    "hermes": {"name":"Hermes AI 网关","base_url":"http://127.0.0.1:8874/v1","model":"hermes-agent","kind":"本机Hermes网关","configured":true,"connected":true,"status":"ok","latency_ms":120,"checked_at":"2026-06-11T14:30:00+08:00"},
    "tdx_api": {"configured":true,"connected":true,"status":"ok","checked_at":"2026-06-11T14:30:00+08:00","pricevol":"fresh"},
    "relay": {"enabled":true,"transport":"api","api_configured":true,"proxy_configured":false,
              "spot":{"count":5862,"total":5862,"failed_pages":0,"stale":false,"age_sec":12,"incomplete":false}},
    "feishu": {"configured":false,"bot":false}
  },
  "sources": [
    {"key":"tencent","name":"腾讯行情","provider":"qt.gtimg.cn","feeds":"A股实时/估值、指数、港美股行情",
     "role":"免费实时主源与字段补全","status":"ok","latency_ms":21,"last_ts":"14:29:58","channel":"腾讯直连","calls":12,"note":"经腾讯直连"},
    {"key":"public_structure","name":"公开市场结构","provider":"东方财富 / 同花顺公开接口（经 AkShare / 直连）",
     "feeds":"涨停、跌停、炸板、连板梯队、涨停原因","role":"免费盘中/盘后结构源，失败如实标缺",
     "status":"idle","latency_ms":null,"last_ts":null,"channel":null,"calls":0,"note":"尚未采集"},
    {"key":"public_fundamentals","name":"公开基本面","provider":"新浪 / 东方财富公开接口（经 AkShare）",
     "feeds":"财务指标、行业归属、业绩预告","role":"免费低频研究源，不承诺盘中实时",
     "status":"idle","latency_ms":null,"last_ts":null,"channel":null,"calls":0,"note":"尚未采集"}
  ]
}
```

### 当前正式链路（`free_only`）

当前正式链路：TDX API v2.8 兼容服务（客户授权远程 API，或可选的本地 TQ 只读桥；提供实时价量/盘口、代码名录、行业概念、全市场快照）、腾讯（行情备用及估值/换手/量比/市值补齐）、新浪与同花顺公开接口（资金/财务/行业资金/涨停原因）、AkShare 封装公开源、乐咕（市场宽度）、东财中继或直连（可选备用）、多源财经快讯（研究证据）。标准部署不要求安装本地通达信；选择 TQ 桥时必须先启动支持 TQ 的本地客户端和 `scripts/tq_tdx_bridge.py`。不使用按次扣积分的数据源。

行业主线的当日成交额、涨跌家数、涨跌幅和领涨股可由 AkShare 隔离子进程读取同花顺行业
一览表；金额从亿元校验并换算为元。TDX 当前负责全市场行情与个股行业关系，未提供的行业
成交额不得由成交量估算或填零。两源只按精确行业名称关联，无法对应的涨停股保留映射缺失。

全 A 股身份主名录固定为 TDX `instruments_a`。适配器按 cursor 拉到明确终页；响应若提供
`count/total`，页内数量和终页总数必须一致，并拒绝重复 cursor、重复代码和市场后缀错误。
只有沪深京覆盖均通过且写入 `a-share-universe-v3`、`authority=tdx_instruments_a`
的快照才允许 `complete=true`，并据此对未命中代码返回 `stock_not_found`。旧版缓存、
交易所内置启动快照、陈旧快照或覆盖不足快照仍可展示已有名称，但必须
`refresh_required=true`、`complete=false`，无权判定股票不存在。服务启动时在后台刷新一次
TDX 名录，不阻塞 HTTP 启动；失败时保留真实降级状态。

新闻 A 股实体映射同样只消费该主名录；其 `summary.universe` 返回
`authority/count/complete/stale`，不再把同一份 TDX 数据伪装成“TDX + 交易所”双名录
交叉验证。名录不具权威性时仍可保留正向命中，但整体状态必须为 `degraded`。

本地 TQ 桥只暴露 TDX 适配器需要的只读端点，明确拒绝账户、持仓、下单、撤单和客户端执行类能力。它支持实时快照、股票名录、板块目录/成分、行业概念关系、基础财务和全市场价量；无等价契约的板块资金排名、舆情、机构一致预期和十大股东返回 `501`，由既有公开源回退或显示缺失，禁止伪装为空数据。配置与运行说明见 `docs/TQ_TDX_BRIDGE.md`。

免费公开源不提供 SLA；限流、反爬或盘后更新延迟时，路由必须降级到其他免费源或返回缺失状态，不得以零值伪装正常结果。

> 回滚说明：若确需恢复历史付费链路，必须整体切回改造前的代码、依赖与适配器引用，然后重跑全量数据验收；不能只修改 `policy`。历史凭据仅允许使用部署机环境变量，不得写入 YAML、日志、文档或 API 返回。

status ∈ {ok, down, idle(尚未采集)}；`idle` 不代表不可用，只代表本进程尚未调用该类字段；`down` 表示最近一次调用失败，业务页应显示降级或缺失。channel 可为直连、境内中继、系统代理、TDX API 或腾讯直连；latency_ms 为最近一次调用耗时。

## 16. 观察池双周期理想买入区间

### GET /api/watchlist

列表仍返回观察条目、实时 `quote`、确定性 `grade/status`，并新增：

- `manual_target_low/manual_target_high`：用户原始手工覆盖值；均为空时才采用自动区间。
- `target_low/target_high` 与 `effective_target_low/effective_target_high`：当前生效区间。手工任一端有值时来源为 `manual_override`，否则使用完整的自动超短线区间，来源为 `auto_ultra`；两者均不可用时为 `missing`。
- `ideal_buy_zones.ultra`：1—3 个交易日超短线区间；盘中后台每 5 分钟重算。新交易日行情日期晚于快照日期时，旧超短区间立即失效，不自动沿用。
- `ideal_buy_zones.swing`：3—10 个交易日波段区间；15:05 前排除尚未收盘的当日日 K。
- 两个周期均返回 `state ∈ {complete,degraded,missing}`、`low/high`、`position ∈ {ABOVE_ZONE,IN_ZONE,BELOW_ZONE}`、`data_date`、`missing` 和可复算 `metrics`。数据不足 60 根完整前复权日线或 3 个交易日 5 分钟线时，对应区间必须为 `missing`，不得用 0 或猜测值补齐。

自动算法只使用腾讯前复权日 K 与新浪 5 分钟 OHLCV/成交额；日 K 成交额从万元统一为元，成交量按科创板“股”、其他 A 股“手”归一为股。AI 不参与区间、位置或评级计算。

### POST /api/watchlist/recalculate

触发全观察池后台重算，立即返回 `{"ok":true,"zone_status":"queued"}`。新增或编辑观察条目也会自动排队计算；普通列表 GET 只读派生快照，不触发逐股历史行情 N+1。

### POST /api/watchlist/import

批量追加观察项，请求体为 `{"items":[{"code":"600519","name":"贵州茅台","sector":"白酒"}]}`，单次限 1—200 条。接口只保存代码、名称和所属行业，其余观察条件保持为空；现有观察池不会被清空或覆盖。

服务端在一次加锁的读改写中按 6 位 A 股代码去重：已有代码和本批次重复代码进入 `duplicate_codes`，非 A 股或格式无效代码进入 `invalid`。返回 `added_count / duplicate_count / invalid_count` 及对应代码；存在新增时 `zone_status=queued`，表示双周期区间已排队而非已经计算完成。

## 16A. ETF观察

ETF观察与 A 股观察池使用独立账本和接口，**不生成、复用或返回双周期买入区间**。普通读取只执行一次批量行情请求并合并已保存的低频 last-good 快照，不按 ETF 逐只访问公开档案或历史接口。

### GET /api/etf-watch

返回 `etf-watch-v1`：`items[]`、`ts`、`low_frequency_refresh=explicit_background` 和 `dual_cycle_zones=false`。每项包含：

- 列表事实：`id/code/name/group/note/created_at`。
- `quote`：批量行情中的 `price/change_pct/open/high/low/prev_close/volume/amount/turnover_rate/volume_ratio/total_mv/source/as_of/stale/quote_note`，以及由同一快照最高、最低、昨收复算的 `amplitude_pct`。TDX API 有效字段优先，腾讯补齐或灾备；字段单位沿用行情契约：价格为元、涨跌幅/换手率/振幅为百分数、成交额/市值为元、成交量为份。
- `profile`：低频快照中的基金类型、跟踪标的、管理人、管理费率/托管费率、净资产规模及日期、最近单位净值及日期、申购/赎回状态、5/20/60 日收益、20 日年化波动、60 日区间最大回撤、MA20/MA60 趋势、日线日期/复权说明及各来源。
- `trade_rule` 只有公开基金类型或跟踪资产足以判定时才返回 `T+0/T+1`；不能仅凭 `1599xx` 等宽代码段猜测，证据不足保持 `null`。
- `state ∈ {complete,degraded,missing}` 与 `missing[]` 保留逐项缺失。盘后行情可同时 `stale=true` 并附 `quote_note`；前端必须标作盘后/陈旧快照，不能称为实时。

页面主行只长期展示覆盖率较高的行情、流动性、规模/净值、历史趋势/波动、费率/申赎和来源状态。PE/PB、五档盘口、IOPV、资金分单不属于 v1 主行契约；低频整组从未成功时显示“补充中”，不渲染成一排占位符。

### 写接口

- `POST /api/etf-watch`：新增单只，先核验真实报价及公开 ETF 名录；名录临时不可用时只接受报价名称明确包含 ETF 的代码。成功返回 `profile_status=queued`。
- `POST /api/etf-watch/import`：请求 `{"codes":[...]}`，单次 1—50 个；一次批量报价和一次缓存 ETF 名录核验后追加，返回 `added_codes/duplicate_codes/rejected_codes/profile_status`，不覆盖现有列表。
- `POST /api/etf-watch/refresh`：显式排队刷新全部低频资料，立即返回 queued；不表示资料已经取得。
- `PUT /api/etf-watch/{id}`：只更新名称、分组、备注；`DELETE /api/etf-watch/{id}`：移出 ETF 观察列表。

低频刷新调用公开基金档案、同花顺 ETF 净值/申赎表和腾讯前复权日线（不足时使用新浪 ETF 日线备源）。60 日收益需要至少 61 个有效收盘价。刷新失败时保留已有非空字段，只更新降级/缺失说明；从未成功的字段保持 `null/missing`，不以行情、市值或 0 冒充。

## 17. 模拟执行 simulation

模拟执行只写客户数据目录下的本地 SQLite/WAL 账本，不包含券商适配器、账户凭据或真实委托接口。每个系统实例只有一个独立模拟账户，与 `/holdings` 及 `holdings.json` 完全分离。页面和接口始终返回 `simulation_only=true`、`broker_connected=false`。

### GET /api/simulation/account?curve_limit=365

返回账户初始化状态、初始资金、现金、持仓市值、总资产、已实现/未实现/累计盈亏、累计费用、收益率、费用设置、T+1 持仓和向前采集的资产曲线。持仓估值缺少任一真实报价时，`market_value / total_assets / unrealized_pnl / total_pnl / return_pct` 保持 `null`，并返回 `valuation_state=missing` 与 `missing_symbols`，不得用 0 或旧价伪装完整估值。该 GET 只做当前展示估值，不追加曲线点。

### POST /api/simulation/account

创建唯一模拟账户。请求体：`client_request_id`、`initial_cash`、`commission_rate_pct`、`minimum_commission`、`stamp_duty_rate_pct`、`transfer_fee_rate_pct`、可选 `slippage_bps`。所有 `*_rate_pct` 的单位都是百分比，例如万三佣金填写 `0.03`；滑点单位为 bp。初始资金创建后不能直接覆盖，重复创建返回 `409 account_already_initialized`。

### POST /api/simulation/account/settings

按相同单位更新佣金、最低佣金、卖出印花税、双边过户费和模拟滑点。设置只对后续成交生效；每笔成交会保存当时的费用配置和费用分项，不重算历史。

### POST /api/simulation/account/valuation

请求体只需 `client_request_id`。服务端批量读取当前持仓报价并追加一个资产曲线点；缺行情的标的按缺失语义保存，不用旧报价或 0 补齐。资产曲线从账户创建时开始向前记录，不回填迁移前订单或历史行情。

### GET /api/simulation/status

返回 `simulation-status.v4`：模式、账户是否初始化、`A_SHARE_T_PLUS_1` 交收规则、自动撮合开关、紧急停止、各状态订单数量和最近事件。`manual_approval_required=false`、`automatic_matching=true` 表示服务端支持自动撮合，`automatic_matching_enabled` 表示持久化运行状态，`matching_interval_seconds=5`、`order_time_in_force=DAY`。

### POST /api/simulation/automatic-matching

请求体：`client_request_id`、`enabled`（布尔值）。关闭后服务器暂停 `simulation_matcher` 定时作业，并在撮合器入口再次阻断；不读取待处理订单、不拉取撮合行情、不迁移或到期撤销 DAY 挂单。已有挂单保持不变，新订单仍可进入挂单池；恢复后先按 DAY 规则清理已过期挂单，再继续正常撮合。开关写入本地 SQLite，服务重启后保持原状态；每次控制请求追加审计事件。该开关不等同于紧急停止，不会撤单或禁止新单。

### GET /api/simulation/orders?limit=100&status=approved

按创建时间倒序返回模拟订单及追加式事件链。状态仍兼容 `proposed / approved / filled / rejected / cancelled`；其中 `approved` 在当前自动撮合契约中表示“挂单中/已进入撮合池”，`proposed` 仅用于兼容旧账本并会自动迁移。

### POST /api/simulation/orders

请求体仅接受订单意图：`client_request_id`、`symbol`、`side`、`order_type`、`quantity`、可选 `limit_price`。用户提交本身即为确认；通过服务端风险与行情校验后，订单直接进入撮合池，不再要求普通订单人工批准或手动成交。交易时段使用服务端盘中行情 `price_mode=live`；非交易时段允许用核验后的最近收盘价建立下一交易日 DAY 挂单。风险等级、参考价、行情时点和缺失状态均由服务端读取，客户端伪造同名字段会被忽略。买入缺风险、缺行情、盘中行情陈旧、风险等级不低于 4 或不满足 100 股整手时拒绝。

自动撮合开启时，服务端每 5 秒批量读取挂单标的行情并撮合，页面关闭不影响。买入限价单在原始市场价 `<= limit_price` 时触发，成交价为 `min(市场价 × (1 + 滑点), limit_price)`；卖出限价单在原始市场价 `>= limit_price` 时触发，成交价为 `max(市场价 × (1 - 滑点), limit_price)`。未触价、行情暂缺或行情陈旧时继续挂单，不写入拒绝终态。市场价单在下一个有效撮合轮次成交。DAY 挂单在有效交易日 15:00 撮合结束后自动撤单，原因 `day_order_expired`。

### POST /api/simulation/orders/{order_id}/approve

旧账本兼容端点。只用于把历史 `proposed` 订单转为撮合池状态；当前前端和新订单不调用。

### POST /api/simulation/orders/{order_id}/fill

旧客户端兼容端点；当前前端不展示“手动成交”。成交价、行情时点和来源仍由服务端重新读取，客户端传入的 `market_price / as_of / market_source` 不参与成交。未触价返回原挂单且 `matched=false`，不拒绝；触价后按资金、A 股 T+1、FIFO、费用及上述限价内滑点规则成交，并在同一 SQLite 事务中更新账户与追加式事件。

### POST /api/simulation/orders/{order_id}/cancel

取消 `proposed / approved` 订单，请求体为 `client_request_id` 与可选 `reason`。

### POST /api/simulation/emergency-stop

请求体：`client_request_id`、`reason`。开启后立即取消所有待处理模拟订单并阻止新订单。

### POST /api/simulation/emergency-stop/reset

请求体：`client_request_id`、`approved_by`、`reason`。只有可识别的人工复核人可以解除；自动化身份会被拒绝。解除动作追加审计事件，不恢复已经取消的订单。

所有写接口都要求 8–160 位 `client_request_id` 并提供幂等重放；同一 ID 用于不同操作或不同内容返回 `409 idempotency_conflict`。

## 18. 只读生产验收门禁

运行：

```bash
python3 scripts/production_acceptance.py --base-url 127.0.0.1:18869
```

脚本只发 GET，不触发任务、生成报告或发送消息。它核验服务与 8 个核心路由、`free_only`、付费源/Token、Agent 最小权限与最近完整运行、外部交付、北京时间/交易日、数据新鲜度和股票池完整性。退出码 `0` 表示所有阻断项通过，`2` 表示仍有上线阻断，`1` 表示验收程序自身失败；不能把 `warn` 或部分成功解释成完整上线就绪。

## 19. 备份与恢复演练

工具只对显式绝对路径工作，备份带逐文件 SHA-256 清单；SQLite 使用在线快照，恢复只允许新的或空的目录，并在完成哈希与 `PRAGMA quick_check` 后原子落位。

```bash
python3 scripts/data_backup_restore.py backup --data-dir /abs/data --output /abs/backup.tar.gz
python3 scripts/data_backup_restore.py drill --archive /abs/backup.tar.gz
python3 scripts/data_backup_restore.py restore --archive /abs/backup.tar.gz --restore-dir /abs/restore
```

`drill` 在临时目录恢复并自动清理，源目录只读。路径穿越、软链接、重复成员、包体篡改、非空恢复目录或 SQLite 完整性失败都会返回非零退出码；生产上线前应保存 JSON 输出作为恢复证据。

## 20. 全市场技术面选股

技术面选股使用客户 `data_dir/technical_screener.sqlite3` 中的前复权 K 线和确定性信号缓存。普通查询只读本地缓存，不触发逐股历史行情 N+1。盘后 16:10 的 `technical_signal_refresh` 系统任务以及用户显式刷新动作只向 `maintenance_jobs.sqlite3` 入队；独立 `hermes_trader.worker` 进程负责实际刷新。

刷新链路直接读取 `TDX_LOCAL_DIR`（默认 `D:\tdx`）下的 `vipdoc/{sh,sz,bj}/lday/*.day`，不复制原始行情库，也不调用 TQ 的批量 K 线接口。`.day` 提供未复权 OHLC、成交量和成交额；个股仅通过本机 `tqcenter.get_divid_factors` 读取权息事件并在进程内计算前复权，指数保留未复权口径。因子能力不可用时不得把原始价格伪装成前复权，而是按单股降级到腾讯前复权日 K。成交量统一为股、成交额统一为元。

业务端每批最多只保留 `workers * 2` 只股票的 K 线、DataFrame 和信号结果；每批提交后持久化
`cursor/processed/succeeded/failed/skipped` 和 Worker 租约心跳。进程异常退出后，新 Worker 从最后
完整批次续跑。K 线指纹和算法版本均未变化的股票直接跳过；仅算法变化时重算信号而不重写 K 线。
进度和终态中的 `stage_seconds` 分别累计 `benchmark/universe/local_bars/compute_write/final_counts`，
用于区分本地 `.day`/权息因子、信号计算写盘和最终物化计数耗时，不包含股票明细。
腾讯批处理降级不进入进程 TTL 缓存。`TDX_TQ_CONNECTION_PATH` 可为同机多套业务指定不同的 TQ 连接标识，避免同名实例冲突。

`config.yaml` 的 `technical_signal_refresh.enabled` 控制定时刷新是否启用，`workers` 控制计算并发，默认 `4`。对 4 GB 内存机器不建议调高；提高并发会线性增加同时驻留的 K 线、DataFrame 和信号结果。

统一约束：

- `algorithm_ver=local-signal-v1`，复权口径固定为 `qfq`；算法升级不得静默复用旧版本方案。
- 突破窗口排除当前 K 线；指标预热期保持空值；周/月线先合成 OHLCV 后重新计算。
- `signal_kind=event` 只保存发生事件，`signal_kind=state` 只保存最新当前状态。
- DDX、主力资金等 Level-2 条件在没有可核验数据源时返回 `available=false`，不得用成交量推断。
- 历史功能只查询信号命中，不计算收益、胜率或策略回测。

### GET /api/technical-screen/catalog

返回稳定信号编码、中文名、分类、事件/状态、方向、默认参数、别名、可用性，以及 `D/W/M` 周期和 `TODAY/RECENT/CURRENT` 时间语义。不可用信号包含 `unavailable_reason`。

### GET /api/technical-screen/status

返回 `state ∈ {missing,running,partial,ready}`、缓存股票数/K线数/信号数、`data_date`、复权方式、算法版本、`config_schema_version=2`、`refresh_job` 和后台刷新进度。无缓存必须返回 `missing`，不能显示为 0 只符合。`bars`、`signal_matches` 的精确数量只由 Worker 在刷新完成时统计并写入元数据；旧库尚无物化计数时返回 `null`，交互请求不得执行大表 `COUNT(*)`。

可选查询参数 `include_counts=false` 用于首屏摘要：仍返回精确缓存股票数、数据日期、缓存状态和刷新进度，但 `bars`、`signal_matches` 返回 `null`。省略参数返回最近一次 Worker 物化的精确计数，同样不会扫描大表。

### POST /api/technical-screen/query

请求示例：

```json
{
  "universe": {
    "markets": ["MAIN", "CHINEXT", "STAR"],
    "exclude_st": true,
    "exclude_suspended": true,
    "min_listing_days": 120,
    "min_amount_ma20": 50000000
  },
  "default_timeframe": "D",
  "schemaVersion": 2,
  "satisfy": {
    "groupMatch": "ALL",
    "groups": [
      {
        "id": "sg_1",
        "conditionMatch": "ALL",
        "conditions": [
          {"id":"c_1","signal_code":"MACD_GOLDEN_CROSS","timeframe":"D","occurrence":"TODAY","params":{"fast":12,"slow":26,"signal":9}},
          {"id":"c_2","signal_code":"CLOSE_ABOVE_MA","timeframe":"D","occurrence":"CURRENT","params":{"period":60}}
        ]
      }
    ]
  },
  "exclude": {
    "groups": [
      {
        "id": "eg_1",
        "conditionMatch": "ANY",
        "conditions": [
          {"id":"c_3","signal_code":"RSI_OVERBOUGHT","timeframe":"D","occurrence":"CURRENT","params":{"threshold":70}}
        ]
      }
    ]
  },
  "sort": [{"field":"signal_score","direction":"desc"}],
  "algorithm_ver": "local-signal-v1",
  "adjustment": "qfq",
  "benchmark": "000300.SH",
  "limit": 50,
  "offset": 0
}
```

`schemaVersion=2` 是面向规则编辑器的固定两层结构：

- `satisfy.groupMatch ∈ {ALL,ANY}`：所有满足规则组都成立，或任意满足规则组成立。只有一个满足组时前端隐藏该选择并提交 `ALL`。
- `satisfy.groups[].conditionMatch ∈ {ALL,ANY}`：组内技术信号全部成立，或任意一项成立。
- `exclude.groups[].conditionMatch ∈ {ALL,ANY}`：组内任意一项出现时排除，或全部条件同时出现时排除。
- 排除组之间固定为任意一组命中即剔除，不提供也不接受可修改的顶层关系字段。
- `id` 只用于编辑器稳定定位和删除撤销，不参与筛选缓存键。数组顺序用于展示，不影响逻辑结果。

前端面向普通用户只显示“所有/任意/排除”等自然语言，不显示逻辑运算符。后端保存和执行前重复校验：至少一个非空满足组，每个已存在的组至少一个技术信号，排除组可完全为空，组内不得继续嵌套。空组、未知编码、非法周期、不可用数据源或非法结构返回 `state=invalid`。事件只能使用 `TODAY/RECENT`，状态只能使用 `CURRENT`。默认参数从预计算匹配表查询；自定义参数只在本地 K 线缓存上重算，不发网络请求。

周/月条件可显式设置 `include_partial_period=true`，此时只在本地缓存上重算，并在命中原因返回 `is_confirmed=false`。

成功响应返回 `matched/returned/stocks`。每只股票包含 `matched_reasons[]`，逐条给出信号编码、名称、周期、信号日期和关键指标值。缓存为空返回 `state=missing` 和明确 `missing[]`。

### POST /api/technical-screen/refresh

显式提交重型后台刷新，立即返回 HTTP `202`、`state=queued` 和持久 `job_id`。可选维护参数：`max_symbols`（1—6000）和 `workers`（1—12）；默认全市场、4 个有界工作线程。刷新同时缓存沪深300不复权基准日线，供“近N日跑赢大盘”按同日期对齐计算。已有同类任务排队或运行时返回 `409 refresh_already_running`，API 进程本身不执行刷新。
`background_worker.enabled=false` 时拒绝新刷新并返回 HTTP `503 maintenance_worker_disabled`，避免任务永久滞留在队列。

### GET /api/technical-screen/history

查询参数：可选 `code`、`signal_code`、`timeframe`，以及 `limit`（1—1000）。只返回 `signal_kind=event` 的追加式历史命中。

### 筛选方案

- `GET /api/technical-screen/schemes`：列出保存方案。
- `POST /api/technical-screen/schemes`：新建或导入方案。
- `PUT /api/technical-screen/schemes/{id}`：更新指定方案。
- `DELETE /api/technical-screen/schemes/{id}`：删除指定方案。

保存内容包含股票池、`schemaVersion=2` 规则结构、周期、参数、排序、算法版本、复权方式和基准指数。旧算法版本方案必须提示用户。

兼容规则：旧版 `must_all/any_of/exclude` 查询仍按原语义执行。读取旧保存方案时，可无损表达的结构在内存中稳定迁移为 v2，但不覆盖原数据库记录；超出固定两层表达能力的方案返回 `editable=false`、`migration_state=read_only_incompatible` 和 `migration_reason`，前端保留原逻辑筛选并要求用户创建重建草稿，禁止静默展平。新建或更新保存时最终写入 v2；旧请求只有在可无损迁移时才允许保存。
