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
  "client": {"name": "本地客户", "id": "local", "brand": "W.U.D.I."},
  "version": "1.3.1",
  "date": "2026-06-10",
  "is_trade_day": true,
  "market_session": "intraday",
  "recommended_route": "today",
  "market_context": {"recommended_hash": "#/today"},
  "route_redirects": [],
  "ui": {},
  "data_source": "多源行情"
}
```

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
`/api/market/limitup` 的东财/腾讯主口径。
```json
{
  "ok": true, "date": "20260714", "data_date": "2026-07-14", "missing": [],
  "stocks": [
    {"code": "600664", "name": "哈药股份", "price": 4.08, "change_pct": 9.97,
     "reason_type": "中报预增+基药目录+化学制药+创新药", "limit_up_type": "换手板",
     "high_days": "3天3板", "is_again": true, "suc_rate_pct": 80.0,
     "open_num_ths": 1, "first_time": "09:42", "last_time": "09:42",
     "seal_amount": 110000000.0, "seal_volume": 27000000.0,
     "turnover_rate": 6.5, "circ_mv": 9500000000.0}
  ],
  "notes": ["同花顺公开池口径：沪深、创业板、科创板，不含北交所"]
}
```

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

## 5. AI 对话（与 Hermes Agent 打通，SSE 流式）

### POST /api/chat  → `text/event-stream`
请求：
```json
{
  "mode": "stock",
  "message": "这票现在能持有吗？冲高回落怎么应对？",
  "code": "002131",
  "industry": "",
  "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
}
```
- `mode`: `free`(自由问答) | `stock`(盘中问股，需 code) | `position`(持仓诊断，后端自动读取已保存持仓) | `chain`(产业链挖掘，需 industry)
- 后端自动注入对应实时数据上下文（个股盘口/板块/资金/市场情绪），再调 Hermes。

`chain` 模式的数据与降级约定：

- 候选池优先使用 TDX 板块成分；TDX 返回空成分时，后端自动回退到 AKShare 暴露的东方财富公开行业/概念板块及其成分。该回退不需要 API Key。
- 板块成分只证明“属于该公开集合”，不证明公司处于产业链哪个环节、相关收入占比、订单或国产替代进度。
- 实时行情不是产业研究的前置条件。行情缺失时仍返回公开成分候选与名称，但价格/估值保持缺失，`trade_timing` 语义固定为 `not_evaluated`。
- 后端仅对证据核验队列前 5 家公司异步刷新巨潮/交易所公告、深交所互动问答和免费基础财务；冷启动首轮可显示 `refreshing`，后续请求复用 30 分钟进程缓存与既有 last-good 快照。普通请求不会逐股同步抓取整个板块。
- 公告标题或互动问答命中产业关键词时只标记为 `evidence_clue`；没有公告/年报正文和分部收入证据时，不得标记为已验证产业角色或业绩兑现。
- 以上公开源均不新增密钥配置；上游不可用时字段保持 `missing`、`refreshing` 或明确降级状态，不用模型生成值补齐。

SSE 事件流，每条 `data:` 一个 JSON：
```
data: {"stage": "采集实时行情数据…"}
data: {"stage": "Hermes 生成中…"}
data: {"delta": "从分时看，"}
data: {"delta": "该股……"}
data: {"done": true}
```
出错：`data: {"error_code":"chat_generation_failed","error":"AI 生成服务暂不可用，本次回答未完成；请稍后重试。"}` 后流结束。
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

### POST /api/reports/{rtype}/generate → `text/event-stream`
立即生成（采集真实数据 → 调 Hermes 流式生成 → 自动落盘保存）。SSE 同 /api/chat 格式，`done` 事件附 `{"done":true,"saved":true,"date":"2026-06-10"}`。
**阶段事件（`stage`）**：首个 `delta` 前先发 `{"stage":"采集行情数据…"}`（采集前）→ `{"stage":"生成报告中…"}`（调 Hermes 前）；前端处理同 §5。

### POST /api/reports/{rtype}/push
把已生成的报告推送到飞书。body 可选 `{"date":"2026-06-10"}`。返回 `{"ok":true}` 或 `{"ok":false,"error":"..."}`。
`date` 非法（非字符串或不符 `YYYY-MM-DD`）时返回 HTTP 400 `{"ok":false,"error":"日期格式应为 YYYY-MM-DD"}`。

## 7. 持仓

### GET /api/holdings
`{"ok":true,"portfolio_total_asset":1000000,"holdings":[{"code":"002131","name":"利欧股份","cost":4.5,"shares":10000,"industry":"互联网","weight_pct":4.8,"market_value":48000,"note":"AI应用龙头"}]}`
### PUT /api/holdings
body `{"portfolio_total_asset":1000000,"holdings":[...]}`（整体覆盖保存）→ `{"ok":true,"holdings":[...]}`。
`portfolio_total_asset` 是含现金的账户 NAV；服务端按 `market_value / NAV` 计算仓位。
客户端提交的 `industry/sector` 不受信任，服务端按证券代码从免费公开个股主数据重新核验并覆盖；
取不到行业时保存为空，所有新增风险动作由风险闸 fail closed。
持仓数据不合法（条目非对象、cost/shares 非数值等）时返回 HTTP 400 `{"ok":false,"error":"持仓数据不合法：<原因>"}`，整批拒绝、不部分落盘。

## 8. 知识库

### GET /api/knowledge
`{"ok":true,"schema_version":"KnowledgeRule.v1","summary":{"approved":3,"draft":1},"review_queue_count":1,"items":[...]}`

每条包含 `status` (`draft/approved/rejected/superseded/legacy_unverified`)、`version`、
`origin`、`source_report_ids`、`evidence_ids`、`sample_count` 和审批时点。**只有
`approved` 会进入 `knowledge_digest()` 并注入后续 AI**；自动复盘只生成 `draft`。
category 枚举：`交易原则` `亏损禁区` `擅长模式` `不擅长模式` `仓位规则` `经验教训` `选股偏好` `复盘笔记`。
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

纯规则、无网络/无 AI 的 `risk-gate.v1`。输入 `candidate/market/portfolio/liquidity/event`，
输出 `allow/reduce/veto`、最终动作、仓位上限、原因码、规则版本和规范化输入 SHA-256。
同一输入必须得到同一输出。

### POST /api/recommendations

新增推荐前，服务端会：

1. 回读并校验 `price_evidence_id` 的代码/字段/数值/质量；
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
   "push_enabled": true,
   "hermes_base_url": "http://127.0.0.1:8874/v1",
   "hermes_model": "hermes-agent",
   "schedule": {"morning": "08:50", "midday": "14:45", "tail": "14:52",
                "close": "15:30", "evening": "19:30", "archive": "22:00"}
 }}
```
### PUT /api/settings  body 为 settings 子集 → `{"ok":true}`（热生效并持久化）
### POST /api/push/test → 发送一条飞书测试消息，返回 `{"ok":true}` / `{"ok":false,"error":"..."}`

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
汇总所有数据源的真实字段链路、用途、角色、最近调用状态与延迟（延迟为被动埋点，不额外发请求）。
该接口不会返回中继 API、代理地址、Webhook 或任何凭据。
```json
{
  "ok": true, "source_contract": "field-route-v2", "active_policy": "free_only", "ts": "2026-06-11 14:30:00",
  "infra": {
    "hermes": {"name":"Hermes AI 网关","base_url":"http://127.0.0.1:8874/v1","model":"hermes-agent","kind":"项目专属受限Hermes网关","connected":true,"latency_ms":120},
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

## 17. 模拟执行 simulation

模拟执行只写客户数据目录下的本地 SQLite/WAL 账本，不包含券商适配器、账户凭据或真实委托接口。页面和接口始终返回 `simulation_only=true`、`broker_connected=false`。

### GET /api/simulation/status

返回模式、紧急停止、各状态订单数量和最近事件。`manual_approval_required` 固定为 `true`。

### GET /api/simulation/orders?limit=100&status=proposed

按创建时间倒序返回模拟订单及追加式事件链。状态仅允许 `proposed / approved / filled / rejected / cancelled`。

### POST /api/simulation/orders

请求体仅接受订单意图：`client_request_id`、`symbol`、`side`、`order_type`、`quantity`、可选 `limit_price`。风险等级、参考价、行情时点和缺失状态由服务端实时读取；即使客户端伪造同名字段也会被忽略。买入缺风险、缺行情、行情陈旧、风险等级不低于 4 或不满足 100 股整手时拒绝。

### POST /api/simulation/orders/{order_id}/approve

请求体：`client_request_id`、`approved_by`。只有明确的人工身份可批准；`AI / Agent / bot / system / Hermes` 等自动化身份会被拒绝。

### POST /api/simulation/orders/{order_id}/fill

请求体只需要 `client_request_id`。成交价、行情时点和来源均由服务端重新读取，客户端传入的 `market_price / as_of / market_source` 不参与成交。只有已人工批准且未触发紧急停止的订单可模拟成交，并记录滑点。

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

技术面选股使用客户 `data_dir/technical_screener.sqlite3` 中的前复权 K 线和确定性信号缓存。普通查询只读本地缓存，不触发逐股历史行情 N+1。盘后 16:10 的 `technical_signal_refresh` 系统任务以及用户显式刷新动作负责更新缓存。

统一约束：

- `algorithm_ver=local-signal-v1`，复权口径固定为 `qfq`；算法升级不得静默复用旧版本方案。
- 突破窗口排除当前 K 线；指标预热期保持空值；周/月线先合成 OHLCV 后重新计算。
- `signal_kind=event` 只保存发生事件，`signal_kind=state` 只保存最新当前状态。
- DDX、主力资金等 Level-2 条件在没有可核验数据源时返回 `available=false`，不得用成交量推断。
- 历史功能只查询信号命中，不计算收益、胜率或策略回测。

### GET /api/technical-screen/catalog

返回稳定信号编码、中文名、分类、事件/状态、方向、默认参数、别名、可用性，以及 `D/W/M` 周期和 `TODAY/RECENT/CURRENT` 时间语义。不可用信号包含 `unavailable_reason`。

### GET /api/technical-screen/status

返回 `state ∈ {missing,running,partial,ready}`、缓存股票数/K线数/信号数、`data_date`、复权方式、算法版本、`config_schema_version=2` 和后台刷新进度。无缓存必须返回 `missing`，不能显示为 0 只符合。

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

显式提交重型后台刷新，立即返回 HTTP `202` 和 `state=queued`。可选维护参数：`max_symbols`（1—6000）和 `workers`（1—12）；默认全市场、6 个有界工作线程。刷新同时缓存沪深300不复权基准日线，供“近N日跑赢大盘”按同日期对齐计算。已有刷新运行时返回 `409 refresh_already_running`。

### GET /api/technical-screen/history

查询参数：可选 `code`、`signal_code`、`timeframe`，以及 `limit`（1—1000）。只返回 `signal_kind=event` 的追加式历史命中。

### 筛选方案

- `GET /api/technical-screen/schemes`：列出保存方案。
- `POST /api/technical-screen/schemes`：新建或导入方案。
- `PUT /api/technical-screen/schemes/{id}`：更新指定方案。
- `DELETE /api/technical-screen/schemes/{id}`：删除指定方案。

保存内容包含股票池、`schemaVersion=2` 规则结构、周期、参数、排序、算法版本、复权方式和基准指数。旧算法版本方案必须提示用户。

兼容规则：旧版 `must_all/any_of/exclude` 查询仍按原语义执行。读取旧保存方案时，可无损表达的结构在内存中稳定迁移为 v2，但不覆盖原数据库记录；超出固定两层表达能力的方案返回 `editable=false`、`migration_state=read_only_incompatible` 和 `migration_reason`，前端保留原逻辑筛选并要求用户创建重建草稿，禁止静默展平。新建或更新保存时最终写入 v2；旧请求只有在可无损迁移时才允许保存。
