import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'
import { createServer } from 'vite'

const require = createRequire(import.meta.url)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const outputDir = resolve(root, 'audit-output')
const axeSource = await readFile(require.resolve('axe-core/axe.min.js'), 'utf8')
const chromePath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'

const industryChainMarkdown = `## 光模块·产业链结构化研究

**产业结论：已用 TDX 验证 2 个相关板块、3 只去重成员；这只能建立研究候选集合，不能证明供应链角色、业绩兑现或可交易性。**
**当前交易时点：未评估。当前是否可交易、价格、仓位和风险必须另用当时实时行情与风险闸复核。**

> 数据时点：板块目录 2026-07-22；成分 2026-07-22；报价 2026-07-22 10:31:00；板块行情 2026-07-22 10:31:05。
> 来源覆盖：TDX 数据 API / sectors=available；TDX 数据 API / sector_members=available；TDX 数据 API（主）/腾讯财经（降级合并）=available；市场板块行情聚合=available。
> 页首缺口：需求本质；链条结构与价值捕获；卡点与国产替代；公司业务映射与兑现。

### 1. 需求本质
- 当前结构化数据未覆盖终端需求，不能判断景气上行或下行。

### 2. 产业地图
- **上游·材料/设备/关键输入**：哪些输入决定成本、供给弹性与扩产周期？（unverified_framework）
- **中游·制造/加工/模组/系统**：核心工艺、良率、规模效应和议价权集中在哪里？（unverified_framework）
- **下游·应用/渠道/终端**：谁采购、为何采购、需求何时兑现，替代品是什么？（unverified_framework）

### 3. 价值捕获
- 缺少分环节毛利、供需与议价数据，不能断言利润留在哪个环节。

### 4. 卡点
- 尚未取得能证明实际卡点的供应链证据。

### 5. 国产替代
- 没有进口依赖、国产认证和份额数据，不输出国产化率。

## 可核验证据附录

### 板块与成员口径
|核验序|代码|名称|TDX板块|现价|涨跌幅|PE|PB|数据可研分|
|---:|---|---|---|---:|---:|---:|---:|---:|
|1|300308|审计光通信甲|CPO概念|88.40|2.30%|34.20|5.10|96.00|
|2|002281|审计光通信乙|光通信|31.70|-0.80%|—|3.40|82.00|

- “数据可研分”只用于决定先核验谁，不是产业研究优先级、投资排名或买入排名。

### 7. 兑现与估值
- 估值快照与业务兑现分开；PE/PB/市值可用也不能替代订单、收入和利润证据。

### 8. 研究优先级与失效条件
- P1：审计光通信甲（300308）—先核业务角色/收入占比，再核订单与毛利，最后做同环节估值。
- P2：审计光通信乙（002281）—先核业务角色/收入占比，再核订单与毛利，最后做同环节估值。

**失效条件**
- 公司公告/财报无法证明其与目标产业存在实质业务收入时，剔除公司映射。

---
**页末证据与缺口复核**
- TDX 数据 API / sectors：available；截至 2026-07-22；只支持“板块名称与分类”。
- 缺口·需求本质：终端销量/装机/渗透率、价格与库存。`

const emptyIndustryChainMarkdown = `## 未匹配产业·产业链结构化研究

**产业结论：TDX 目录未匹配“未匹配产业”；本轮只保留研究框架，不生成公司排名。**
**当前交易时点：未评估。当前是否可交易、价格、仓位和风险必须另用当时实时行情与风险闸复核。**

> 数据时点：板块目录 缺失；成分 缺失；报价 缺失；板块行情 缺失。
> 来源覆盖：TDX 数据 API / sectors=missing；TDX 数据 API / sector_members=missing。
> 页首缺口：TDX 板块匹配；需求本质；链条结构与价值捕获。

## 可核验证据附录

### 板块与成员口径
|核验序|代码|名称|TDX板块|现价|涨跌幅|PE|PB|数据可研分|
|---:|---|---|---|---:|---:|---:|---:|---:|
|—|—|—|未取得可验证的 TDX 成分关系|—|—|—|—|—|

**页末证据与缺口复核**
- 缺口·TDX 板块匹配：需要一致的 TDX 行业/概念名称或显式别名。`

const chainSse = (markdown, { complete = true } = {}) => [
  'data: ' + JSON.stringify({ stage: '正在匹配 TDX 板块目录、成员关系与当前行情…' }),
  'data: ' + JSON.stringify({ delta: markdown }),
  ...(complete ? ['data: ' + JSON.stringify({ done: true, truncated: false })] : []),
  '',
].join('\n\n')

const companyResearchMarkdown = `# 审计公司（000001）深度研究

## 一、结论速览

服务端已完成公司研究正文治理；本页不把模型正文提升为独立风险等级或仓位上限。

## 二、核心事件

- 审计公司年度报告已进入官方公告证据区，正文只作解释，不替代公告原文。

## 三、产业链位置

当前结构化接口未提供上下游收入占比，保持缺失，不推测供应链角色。

## 四、财务证据

|指标|数值|单位|口径|
|---|---:|---|---|
|ROE|—|%|结构化财务接口未返回，保持缺失|
|收入增长率|—|%|接口未返回，保持缺失|

## 五、估值

接口未返回历史分位，不输出“低估”或“高估”的精确结论。

## 六、多空推演

- 多头条件与空头条件均需结合后续真实披露继续核验。

## 七、交易边界

本页没有独立结构化风险等级和仓位上限；请进入个股诊断复核。

## 八、风险提示

- 一致预期不等于公司承诺；互动易回复不是法定公告。`

const companySse = (markdown, { complete = true } = {}) => [
  'data: ' + JSON.stringify({ stage: '正在生成受治理的单公司研究正文…' }),
  'data: ' + JSON.stringify({ delta: markdown }),
  ...(complete ? ['data: ' + JSON.stringify({ done: true, truncated: false })] : []),
  '',
].join('\n\n')

const reportCenterMarkdown = `# 审计早盘内参

## 一、当前结论

服务端定稿结论：中等风险，只做主线内有承接的确认机会，不追一致加速。

## 二、风险与仓位

- 总仓不超过 50%。
- 结构化价格条件缺失时保持等待，不以 0 补位。

## 三、证据完整性

|证据项|状态|数据时点|
|---|---|---|
|风险快照|完整|2026-07-28 08:50:00|
|结构化价格条件|缺失|—|

> 本报告正文只作受约束解释，确定性风险与仓位边界来自服务端定格快照。`

const reportCenterFixture = {
    ok: true,
    schema_version: 'daily-report-v2',
    rtype: 'morning',
    date: '2026-07-28',
    title: '审计早盘内参',
    content_md: reportCenterMarkdown,
    content_id: 'report:morning:2026-07-28',
    content_hash: 'a82be94d0a932b467dac5289f8a1c77b047191787599574b62e1294c0f8dc321',
    report_version: 1,
    created_at: '2026-07-28 08:51:12',
    pushed: true,
    cockpit: {
      as_of: '2026-07-28 08:50:00',
      command: {
        risk_level: 3,
        level_name: '中等风险 · 条件参与',
        today_action: '只做主线内有承接的确认机会，不追一致加速',
        position_cap: '总仓不超过 50%',
        first_principle: '先确认风险边界，再验证价格条件',
        forbid: ['不追一致加速', '价格条件缺失时不新增风险'],
      },
    },
    claim_validation: { ok: true, status: 'passed', errors: [] },
    advice_governance: { ok: true, status: 'passed', proposal_only: true, errors: [] },
    input_lineage: { evidence_as_of: '2026-07-28 08:50:00' },
}

const fixtures = {
  '/api/archive/dates': {
    ok: true,
    dates: ['2026-07-28', '2026-07-27', '2026-07-24'],
  },
  '/api/reports/morning': reportCenterFixture,
  '/api/delivery/receipts': {
    ok: true,
    schema_version: 'delivery-receipt.v1',
    receipts: [{
      receipt_id: 'dr_audit_report',
      content_id: 'report:morning:2026-07-28',
      content_hash: 'a82be94d0a932b467dac5289f8a1c77b047191787599574b62e1294c0f8dc321',
      channel: 'gateway',
      attempt: 1,
      status: 'accepted',
      attempted_at: '2026-07-22T00:52:00Z',
      sent_at: '2026-07-22T00:52:01Z',
      ack_at: null,
      target_results: [{ target: 'feishu', status: 'accepted' }],
    }],
    count: 1,
    total: 1,
  },
  '/api/knowledge': {
    ok: true,
    schema_version: 'KnowledgeRule.v1',
    summary: { approved: 2, draft: 1, legacy_unverified: 1, rejected: 1, superseded: 1 },
    review_queue_count: 2,
    items: [
      {
        schema: 'KnowledgeRule.v1', id: 'k_audit_approved', rule_id: 'rule_audit_position',
        category: '仓位规则', title: '审计规则：风险期降低试错频率',
        content: '仅在已核验的市场风险边界内执行；没有服务端价格条件时保持等待。',
        status: 'approved', version: 2, origin: 'explicit_user_input',
        source_report_ids: [], evidence_ids: [], sample_count: 1,
        created_at: '2026-07-28 09:12:00', created_by: 'user:web',
        approved_at: '2026-07-28 09:12:00', approved_by: 'user:web',
        supersedes_id: 'k_audit_superseded',
      },
      {
        schema: 'KnowledgeRule.v1', id: 'k_audit_draft', rule_id: 'k_audit_draft',
        category: '复盘笔记', title: '审计待复核：确认信号需跨日验证',
        content: '该归纳来自一次收盘复盘，尚不能泛化为稳定交易原则。',
        status: 'draft', version: 1, origin: 'auto_summary',
        source_report_ids: ['report:close:2026-07-28'], evidence_ids: [], sample_count: 1,
        created_at: '2026-07-28 15:38:00', created_by: 'system:auto_summary',
        approved_at: null, rejected_at: null,
      },
      {
        schema: 'KnowledgeRule.v1', id: 'k_audit_legacy', rule_id: 'k_audit_legacy',
        category: '经验教训', title: '审计历史条目：来源待补充',
        content: '旧版本没有来源和样本信息，保持待补证状态。',
        status: 'legacy_unverified', version: 1, origin: 'legacy_import',
        source_report_ids: [], evidence_ids: [], sample_count: 0,
        created_at: '2024-01-03 10:00:00', created_by: 'legacy',
      },
      {
        schema: 'KnowledgeRule.v1', id: 'k_audit_rejected', rule_id: 'k_audit_rejected',
        category: '经验教训', title: '审计已拒绝：单日归因',
        content: '仅有单日样本，不能形成稳定规则。',
        status: 'rejected', version: 1, origin: 'auto_summary',
        source_report_ids: ['report:close:2026-07-24'], evidence_ids: [], sample_count: 1,
        created_at: '2026-07-24 15:40:00', rejected_at: '2026-07-25 08:30:00',
        rejection_reason: '样本不足，不能泛化',
      },
      {
        schema: 'KnowledgeRule.v1', id: 'k_audit_superseded', rule_id: 'rule_audit_position',
        category: '仓位规则', title: '审计旧版仓位规则',
        content: '旧版正文保留，仅用于版本审计。',
        status: 'superseded', version: 1, origin: 'explicit_user_input',
        source_report_ids: [], evidence_ids: [], sample_count: 1,
        created_at: '2026-07-20 09:00:00', superseded_at: '2026-07-28 09:12:00',
        replacement_id: 'k_audit_approved',
      },
      {
        schema: 'KnowledgeRule.v1', id: 'k_audit_missing', rule_id: 'k_audit_missing',
        category: null, title: null, content: null, status: null, version: null,
        origin: null, source_report_ids: null, evidence_ids: null, sample_count: null,
        created_at: null, created_by: null,
      },
    ],
  },
  '/api/status': {
    ok: true,
    client: { name: '审计个人投资者', id: 'audit' },
    version: '1.4.2-audit',
    date: '2026-07-28',
    data_source: '客户授权 TDX API + 免费公开源',
    model_connection: { configured: true, connected: true, status: 'connected', required_for_core: false },
    hermes: { connected: true, optional: true, required_for_core: false, agent_capability: { ready: true, status: 'restricted', reason: '审计 fixture：服务端工具权限已收敛', runtime_mode: 'server_agent', profile_attestation: 'attested', process_attestation: 'bound', unexpected_toolsets: [] } },
  },
  '/api/settings': {
    ok: true,
    settings: {
      feishu_webhook: '', feishu_webhook_set: true, feishu_secret: '', feishu_secret_set: true,
      push_enabled: true, push_via: 'feishu', active_model_connection_id: 'audit-model',
      schedule: { morning: '08:50', midday: '14:45', tail: '14:52', close: '15:30', evening: '19:30', archive: '22:00' },
      model_connections: [{ id: 'audit-model', name: '审计受限模型', provider: 'hermes', base_url: 'http://127.0.0.1:8874/v1', model: 'hermes-agent', api_key_set: true, active: true }],
      hermes_integration: { optional: true, advanced_diagnostics: true, active_as_model_gateway: true },
      agent_require_restricted_toolsets: true, agent_allowed_toolsets: [], agent_require_profile_attestation: true,
    },
  },
  '/api/jobs': {
    ok: true, schema_version: 'task-center-v1', generated_at: '2026-07-28T10:31:00+08:00',
    jobs: [
      { id: 'morning', name: '早盘内参 08:50', kind: 'core', enabled: true, next_run: '2026-07-29 08:50:00', last_ok: '2026-07-28 08:51:12', last_result: { status: 'ok', started_at: '2026-07-28 08:50:00', duration_ms: 71200, output_objects: [{ kind: '正式报告', content_id: 'report:morning:2026-07-28', trade_day: '2026-07-28', version: 1, content_hash: 'a82be94d0a93' }] }, manual_run: { enabled: true }, retry: { eligible: false } },
      { id: 'data_sentinel_am', name: '开盘前数据体检', kind: 'system', enabled: true, next_run_at: '2026-07-29 08:20:00', last_ok: '2026-07-28 08:21:03', last_result: { status: 'ok', duration_ms: 1840, output_objects: [] }, manual_run: { enabled: true }, retry: { eligible: false } },
      { id: 'capital_snapshot', name: '资金快照', kind: 'system', enabled: true, next_run_at: '2026-07-28 10:40:00', last_run: '2026-07-28 10:30:00', last_result: { status: 'warn', duration_ms: 4630, error_code: 'industry_flow_partial', output_objects: [] }, manual_run: { enabled: true }, retry: { eligible: true } },
    ],
  },
  '/api/datasources': {
    ok: true, source_contract: 'field-route-v2', active_policy: 'free_only', ts: '2026-07-28 10:31:00',
    deployment: { configured: 'cn_home', profile: 'cn_home', china_public_route: 'direct_low_concurrency', public_note: '审计 fixture：境内低并发直连，失败时显式降级。' },
    universe: { count: 5412, source: 'TDX API', as_of: '2026-07-28', stale: false, refresh_required: false, missing_exchanges: [] },
    sources: [
      { key: 'tdx_api', name: 'TDX API', provider: '客户授权数据 API', feeds: 'A 股实时价量、股票池与板块关系', role: '基础行情主链路', status: 'ok', latency_ms: 38, last_ts: '2026-07-28 10:30:58', note: '最近调用成功' },
      { key: 'tencent', name: '腾讯行情', provider: 'qt.gtimg.cn', feeds: '行情备用与估值补全', role: '免费实时备用', status: 'ok', latency_ms: 26, last_ts: '2026-07-28 10:30:59', note: '腾讯直连' },
      { key: 'public_structure', name: '公开市场结构', provider: '同花顺公开接口', feeds: '涨跌停与板块结构', role: '增强数据', status: 'degraded', latency_ms: 6120, last_ts: '2026-07-28 10:30:40', note: '部分字段缺失，不影响基础行情' },
      { key: 'public_fundamentals', name: '公开基本面', provider: '公开低频源', feeds: '财务指标与业绩预告', role: '低频研究增强', status: 'idle', latency_ms: null, last_ts: null, note: '本进程尚未采集' },
    ],
    field_contract: { schema_version: 'field-contract-v2', fields: { last_price: { meaning: '最近可观察成交价', unit: 'CNY', value_semantics: 'point_in_time_snapshot', route: ['tdx_api', 'tencent_quote'], live_max_age_seconds: 15, delayed_max_age_seconds: 120, batch_scope: 'quote' }, amount: { meaning: '成交额', unit: 'CNY', value_semantics: 'accumulated_intraday', route: ['tdx_api', 'tencent_quote'], live_max_age_seconds: 30, delayed_max_age_seconds: 180, batch_scope: 'quote' } } },
  },
  '/api/delivery/readiness': {
    ok: true, launch_ready: true, configured: true, channel: 'feishu', targets: ['home'], blockers: [], manual_review_count: 0,
    channels: [
      { key: 'web', label: 'Web 页面', role: 'internal_view', state: 'ready', configured: true, enabled: true, last_success_at: '2026-07-28 08:51:12' },
      { key: 'pdf', label: 'PDF 文件', role: 'generated_file', state: 'ready', configured: true, enabled: true, last_success_at: '2026-07-28 08:52:03' },
      { key: 'feishu', label: '飞书', role: 'external_message', state: 'ready', configured: true, enabled: true, last_success_at: '2026-07-28 08:52:05' },
      { key: 'wechat', label: '微信', role: 'external_message', state: 'unconfigured', configured: false, enabled: false, last_success_at: null },
    ],
  },
  '/api/delivery/outbox': { ok: true, schema_version: 'delivery-outbox.v2', items: [{ delivery_id: 'delivery-audit-accepted', content_id: 'report:morning:2026-07-28', channel: 'feishu', status: 'accepted', attempts: 1, updated_at: '2026-07-28T00:52:01Z' }], count: 1 },
  '/api/agent/runs': { ok: true, items: [{ run_id: 'ar_audit_restricted_001', purpose: 'report.morning', model: 'hermes-agent', capability_status: 'restricted', status: 'completed', attempts: 1, tool_events: 0, finish_reason: 'stop', partial_output: false, safe_to_use: true, started_at: '2026-07-28T00:50:01Z', completed_at: '2026-07-28T00:51:12Z' }] },
  '/api/settings/model-connections': { ok: true, active_connection_id: 'audit-model', connections: [{ id: 'audit-model', name: '审计受限模型', provider: 'hermes', base_url: 'http://127.0.0.1:8874/v1', model: 'hermes-agent', api_key_set: true, active: true }] },
  '/api/shell-status': {
    ok: true,
    client: { name: '审计个人投资者', id: 'audit', brand: '审计白标', brand_sub: 'A 股决策支持' },
    version: '1.4.2-audit',
    is_trade_day: true,
    market_session: 'intraday',
    market_context: {
      now: '2026-07-22T10:31:00+08:00',
      is_trade_day: true,
      market_session: 'intraday',
      recommended_hash: '#/today',
    },
  },
  '/api/pulse': {
    ok: true,
    regime: '震荡修复',
    risk_level: 3,
    risk_label: '中等风险',
    senti: 54,
    senti_label: '中性偏强',
    '定格时点': '2026-07-22 10:30',
    command: {
      risk_level: 3,
      level_name: '中等风险',
      trade_state: '指数修复，结构机会仍需确认持续性',
      today_action: '只做主线内有承接的确认机会，不追一致加速',
      position_cap: '总仓不超过 50%',
      first_principle: '先确认风险边界，再验证价格条件',
      action_label: '今日唯一主动作',
    },
  },
  '/api/market/overview_cached': {
    ok: true,
    data_date: '2026-07-22 10:31',
    total_amount: 682300000000,
    up_count: 2841,
    down_count: 1926,
    flat_count: 221,
    limit_up: 47,
    limit_down: 8,
    zhaban_count: 16,
    zhaban_rate: 25.4,
    promote_rate: 18.7,
    lianban_height: 5,
    limit_up_amount: 72400000000,
    indexes: [
      { name: '上证指数', code: 'sh000001', price: 3548.26, change_pct: 0.42 },
      { name: '深证成指', code: 'sz399001', price: 10829.31, change_pct: 0.71 },
      { name: '创业板指', code: 'sz399006', price: 2267.45, change_pct: -0.18 },
      { name: '科创50', code: 'sh000688', price: 1042.73, change_pct: 0.36 },
      { name: '北证50', code: 'bj899050', price: 1287.09, change_pct: -0.52 },
      { name: '沪深300', code: 'sh000300', price: 4216.62, change_pct: 0.29 },
    ],
  },
  '/api/sysrisk': {
    ok: true,
    level: 3,
    level_name: '中等风险',
    regime: '震荡修复',
    summary: '指数与情绪同步修复，但量能和海外维度仍需继续验证。',
    risk_score: 54,
    '定格时点': '10:30',
    dims: [
      { dim: '大盘', contrib: '中性', reading: '主要指数多数上涨，市场宽度回升' },
      { dim: '资金', contrib: '中性', reading: '成交额较前一时段温和放大' },
      { dim: '情绪', contrib: '中性', reading: '情绪温度处于中性区间' },
      { dim: '消息', contrib: '低', reading: '未出现新的系统性负面事件' },
      { dim: '海外', contrib: '中性', reading: '隔夜风险输入未显著放大' },
    ],
  },
  '/api/market/mainlines': {
    ok: true,
    data_date: '2026-07-22 10:31',
    market_state: { state: '结构轮动', up_ratio: 0.57 },
    limit_down_missing: [],
    limit_down_pool: [
      { code: '001226', name: '拓山重工', change_pct: -10.01, industry: '机械设备', first_time: '09:31', open_times: 0 },
      { code: '001395', name: '亚联机械', change_pct: -10.00, industry: '专用设备', first_time: '09:37', open_times: 1 },
      { code: '001309', name: '德明利', change_pct: -9.98, industry: '半导体', first_time: '10:12', open_times: 2 },
    ],
    roles: [
        { label: '主线', status: 'formed', name: '先进制造', change_pct: 2.46, structure_score: 82, amount_rank_pct: 0.08, breadth_ratio: 0.71, limit_up_count: 12, max_lianban: 4, role_reason: '容量、宽度和涨停梯队同时满足主线门槛。' },
        { label: '次主线', status: 'formed', name: '算力基础设施', change_pct: 1.73, structure_score: 74, amount_rank_pct: 0.13, breadth_ratio: 0.64, limit_up_count: 8, max_lianban: 3, role_reason: '资金承接较强，持续性仍需下一时段确认。' },
        { label: '活口', status: 'formed', name: '创新药', change_pct: 1.12, structure_score: 63, amount_rank_pct: 0.21, breadth_ratio: 0.55, limit_up_count: 5, max_lianban: 2, role_reason: '分歧后保留活跃标的，板块宽度一般。' },
        { label: '情绪链', status: 'formed', name: '风电设备', change_pct: 1.16, structure_score: 79, amount_rank_pct: 0.24, breadth_ratio: 0.48, limit_up_count: 5, max_lianban: 6, role_reason: '6/2/1板梯队 · 涨停5家 · 龙头立新能源' },
    ],
  },
  '/api/features/money-effect': {
    ok: true,
    ts: '2026-07-22 10:31:26',
    data_date: '2026-07-22',
    data_state: 'degraded',
    missing: ['概念涨跌家数'],
    missing_fields: [],
    degraded_factors: ['概念涨跌家数覆盖1/2'],
    notes: ['主力资金流未进入赚钱分；末列展示可核验成交额'],
    score_semantics: { name: 'available_factor_score', comparable_when: 'score_coverage.state=complete', warning: '因子不全时分数不与完整因子分数等置比较' },
    themes: [
      { name: '先进制造', kind: 'industry', change_pct: 2.46, money_score: 32.8, zt_count: 12, max_lianban: 4, up_count: 74, down_count: 21, leader_name: '立新能源', leader_change_pct: 10.02, amount: 87600000000, main_flow: 4230000000, score_coverage: { complete: 4, total: 4, state: 'complete', missing: [] } },
      { name: '算力基础设施', kind: 'concept', change_pct: 1.73, money_score: 24.6, zt_count: null, max_lianban: null, up_count: 48, down_count: 19, leader_name: '中科曙光', leader_change_pct: 10.00, amount: 71200000000, main_flow: null, score_coverage: { complete: 2, total: 2, state: 'complete', missing: [] } },
      { name: '创新药', kind: 'concept', change_pct: 1.12, money_score: 17.4, zt_count: null, max_lianban: null, up_count: null, down_count: null, leader_name: '众生药业', leader_change_pct: 10.01, amount: 43800000000, main_flow: null, score_coverage: { complete: 1, total: 2, state: 'partial', missing: ['breadth'] } },
      { name: '风电设备', kind: 'industry', change_pct: 0.84, money_score: 13.6, zt_count: 5, max_lianban: 6, up_count: 39, down_count: 26, leader_name: '立新能源', leader_change_pct: 10.02, amount: 39100000000, main_flow: -620000000, score_coverage: { complete: 4, total: 4, state: 'complete', missing: [] } },
    ],
    losers: [
      { name: '游戏', kind: 'industry', change_pct: -2.86, money_score: -18.2, zt_count: 0, max_lianban: null, up_count: 8, down_count: 41, leader_name: null, leader_change_pct: null, amount: 29800000000, score_coverage: { complete: 4, total: 4, state: 'complete', missing: [] } },
    ],
  },
  '/api/features/crowdedness': {
    ok: true,
    ts: '2026-07-22 10:31:28',
    data_date: '2026-07-22',
    data_state: 'degraded',
    missing: [],
    missing_fields: [],
    degraded_factors: ['概念拥挤度未计涨停聚集分量'],
    score_semantics: { name: 'cross_section_percentile', comparable_when: 'score_coverage.state=complete', warning: '部分因子分仅表示当前可用因子的横截面分位' },
    sectors: [
      { name: '先进制造', kind: 'industry', change_pct: 2.46, amount: 87600000000, amt_share: 12.84, zt_count: 12, crowd_score: 96, level: '过热拥挤', score_coverage: { complete: 3, total: 3, state: 'complete' } },
      { name: '算力基础设施', kind: 'concept', change_pct: 1.73, amount: 71200000000, amt_share: 10.43, zt_count: null, crowd_score: 84, level: '偏拥挤', score_coverage: { complete: 2, total: 3, state: 'partial' } },
      { name: '创新药', kind: 'concept', change_pct: 1.12, amount: 43800000000, amt_share: 6.42, zt_count: null, crowd_score: 66, level: '偏拥挤', score_coverage: { complete: 2, total: 3, state: 'partial' } },
      { name: '风电设备', kind: 'industry', change_pct: 0.84, amount: 39100000000, amt_share: 5.73, zt_count: 5, crowd_score: 58, level: '正常', score_coverage: { complete: 3, total: 3, state: 'complete' } },
    ],
  },
  '/api/features/mainline': {
    ok: true,
    ts: '2026-07-22 10:31:30',
    data_date: '2026-07-22',
    data_state: 'complete',
    missing: [],
    missing_fields: [],
    degraded_factors: [],
    history_days: 5,
    switch_signal: '今日赚钱效应榜首『先进制造』与近5日主线『算力基础设施』不一致，关注主线是否切换',
    note: null,
    today_top: [],
    mainlines: [
      { name: '算力基础设施', kind: 'concept', days_on_board: 4, avg_money_score: 31.2, cum_main_flow: null, strength: 71.2, recent_score: 24.6, flow_note: '无资金流数据（概念板块不提供主力净额）' },
      { name: '先进制造', kind: 'industry', days_on_board: 3, avg_money_score: 34.8, cum_main_flow: 12600000000, strength: 64.8, recent_score: 32.8 },
      { name: '创新药', kind: 'concept', days_on_board: 3, avg_money_score: 24.1, cum_main_flow: null, strength: 54.1, recent_score: 17.4, flow_note: '无资金流数据（概念板块不提供主力净额）' },
    ],
  },
  '/api/features/leaders': {
    ok: true,
    ts: '2026-07-22 10:31:32',
    data_date: '2026-07-22',
    missing: [],
    kongban: { height: 6, stocks: [{ code: '001269', name: '立新能源', lianban: 6, reason: '风电设备', seal_amount: 318000000 }] },
    ladder: [
      { lianban: 6, label: '6连板', count: 1, stocks: [{ code: '001269', name: '立新能源', reason: '风电设备', seal_amount: 318000000, turnover_rate: 17.6, first_time: '09:31', is_leader: true }] },
      { lianban: 3, label: '3连板', count: 2, stocks: [{ code: '600664', name: '哈药股份', reason: '中报预增+创新药', seal_amount: 110000000, turnover_rate: 8.4, first_time: '09:42', is_leader: false }, { code: '002317', name: '众生药业', reason: '创新药+医药商业', seal_amount: 286000000, turnover_rate: null, first_time: '09:35', is_leader: false }] },
      { lianban: 1, label: '首板', count: 2, stocks: [{ code: '603019', name: '中科曙光', reason: '算力基础设施+国产芯片', seal_amount: 192000000, turnover_rate: 6.7, first_time: '10:12', is_leader: false }, { code: '002230', name: '科大讯飞', reason: '算力基础设施+人工智能', seal_amount: 143000000, turnover_rate: 4.8, first_time: '09:58', is_leader: false }] },
    ],
    popularity: [
      { code: '001269', name: '立新能源', lianban: 6, seal_amount: 318000000, seal_ratio: 1.7, reason: '风电设备' },
      { code: '002317', name: '众生药业', lianban: 3, seal_amount: 286000000, seal_ratio: null, reason: '创新药+医药商业' },
      { code: '603019', name: '中科曙光', lianban: 1, seal_amount: 192000000, seal_ratio: 0.6, reason: '算力基础设施+国产芯片' },
    ],
  },
  '/api/sentiment/today': {
    ok: true,
    metrics: { yesterday_limit_premium: 2.04, up_down_ratio: 1.47 },
  },
  '/api/market/flow': {
    ok: true,
    ts: '2026-07-22 10:31',
    source: '后台资金快照',
    main_flow_sectors: [
      { name: '工业金属', main_net: 4030000000, change_pct: 1.24 },
      { name: '贵金属', main_net: 3640000000, change_pct: 6.16 },
      { name: '计算机设备', main_net: 1450000000, change_pct: -0.85 },
      { name: '半导体', main_net: -8650000000, change_pct: 0.17 },
      { name: '通信设备', main_net: -8240000000, change_pct: -0.38 },
      { name: '光学光电子', main_net: -4050000000, change_pct: -1.96 },
    ],
  },
  '/api/market/stockflow': {
    ok: true,
    data_date: '2026-07-21',
    data_state: 'delayed',
    state_label: '最近交易日',
    note: '公开资金排名为最近交易日口径',
    inflow: [
      { code: '300750', name: '宁德时代', net: 2890000000, change_pct: 1.52 },
      { code: '601899', name: '紫金矿业', net: 2520000000, change_pct: 2.31 },
      { code: '601600', name: '中国铝业', net: 1480000000, change_pct: 3.09 },
    ],
    outflow: [
      { code: '002594', name: '比亚迪', net: -3820000000, change_pct: -1.17 },
      { code: '300308', name: '中际旭创', net: -3320000000, change_pct: -2.04 },
      { code: '002230', name: '科大讯飞', net: -3180000000, change_pct: -0.78 },
    ],
  },
  '/api/market/sectors': {
    ok: true,
    data_date: '2026-07-22 10:31',
    sectors: [
      { kind: 'industry', name: '贵金属', amount: 128000000000, change_pct: 6.35 },
      { kind: 'industry', name: '游戏', amount: null, change_pct: -2.86 },
      { kind: 'industry', name: '保险', amount: 69000000000, change_pct: 2.25 },
      { kind: 'industry', name: '通用设备', amount: 65000000000, change_pct: -1.80 },
      { kind: 'industry', name: '建筑材料', amount: 59000000000, change_pct: -1.50 },
      { kind: 'industry', name: '能源金属', amount: 47000000000, change_pct: 1.42 },
      { kind: 'industry', name: '光学光电子', amount: 41000000000, change_pct: -0.60 },
      { kind: 'industry', name: '电网设备', amount: 39000000000, change_pct: 1.05 },
      { kind: 'industry', name: '农牧饲渔', amount: 15000000000, change_pct: -0.42 },
    ],
  },
  '/api/market/multiperiod': {
    ok: true,
    data_date: '2026-07-22',
    data_state: 'complete',
    coverage: { period_rows: { complete: 4, total: 4 } },
    stocks: [
      { code: '300834', name: '星辉环材', theme: '化学原料', d1: 20.01, d3: 14.66, d5: 1.25, d20: -21.57 },
      { code: '300688', name: '创业黑马', theme: '教育', d1: 20.01, d3: 19.67, d5: -3.62, d20: -12.61 },
      { code: '301234', name: '五洲医疗', theme: '医疗器械', d1: 20.00, d3: 10.75, d5: 18.54, d20: -0.85 },
      { code: '300779', name: '惠城环保', theme: '环境治理', d1: 20.00, d3: 38.74, d5: 28.24, d20: 21.67 },
    ],
  },
  '/api/market/limitup/themes': {
    ok: true,
    date: '20260722',
    data_date: '2026-07-22',
    ts: '2026-07-22 10:31:18',
    missing: [],
    notes: ['同花顺公开池口径：沪深、创业板、科创板，不含北交所'],
    stocks: [
      { code: '600664', name: '哈药股份', price: 4.08, change_pct: 9.97, reason_type: '中报预增+创新药', limit_up_type: '换手板', high_days: '3天3板', is_again: true, suc_rate_pct: 80.0, first_time: '09:42', last_time: '09:42', seal_amount: 110000000 },
      { code: '002317', name: '众生药业', price: 18.62, change_pct: 10.01, reason_type: '创新药+医药商业', limit_up_type: '换手板', high_days: '2天2板', is_again: false, suc_rate_pct: 100.0, first_time: '09:35', last_time: '09:35', seal_amount: 286000000 },
      { code: '300573', name: '兴齐眼药', price: 86.41, change_pct: 20.00, reason_type: '创新药+眼科医疗', limit_up_type: '换手板', high_days: '首板', is_again: false, suc_rate_pct: 75.0, first_time: '10:06', last_time: '10:18', seal_amount: 65000000 },
      { code: '603019', name: '中科曙光', price: 72.16, change_pct: 10.00, reason_type: '算力基础设施+国产芯片', limit_up_type: '换手板', high_days: '首板', is_again: true, suc_rate_pct: 66.7, first_time: '10:12', last_time: '10:26', seal_amount: 192000000 },
      { code: '002230', name: '科大讯飞', price: 58.74, change_pct: 9.99, reason_type: '算力基础设施+人工智能', limit_up_type: '换手板', high_days: '首板', is_again: false, suc_rate_pct: 83.3, first_time: '09:58', last_time: '09:58', seal_amount: 143000000 },
      { code: '301269', name: '华大九天', price: 103.52, change_pct: 19.99, reason_type: '国产芯片+EDA', limit_up_type: '', high_days: '首板', is_again: false, suc_rate_pct: null, first_time: '', last_time: '', seal_amount: null },
    ],
  },
  '/api/news/a-share': {
    ok: true,
    ts: '2026-07-22 10:31:44',
    cn_fetched_at: '2026-07-22 10:31:42',
    fetched_at: '2026-07-22T10:31:42+08:00',
    latest_event_time: '2026-07-22 10:30:18',
    refresh_seconds: 30,
    sources_tried: 3,
    sources_live: 2,
    missing: ['东财公开聚合端暂不可用'],
    mapping: { state: 'complete', mapped: 2, candidates: 1, total: 3, missing: [] },
    observation: { enabled: true, state: 'mixed', tracked: 2, waiting: 1, observing: 0, observed: 1, missing: [] },
    pipeline: { source_state: 'degraded', entity_mapping_state: 'complete', market_observation_state: 'mixed', validation_state: 'mixed' },
    items: [
      {
        event_id: 'event-audit-direct',
        title: '世茂能源发布股权转让公告',
        content: '世茂能源公告称，控股股东拟协议转让部分公司股份。',
        time: '2026-07-22 10:30:18',
        published_at: '2026-07-22T10:30:18+08:00',
        fetched_at: '2026-07-22T10:30:24+08:00',
        evidence_level: 'tier_1',
        confirms: 1,
        corroboration: { collection_endpoint_count: 1, independent_origin_count: 0, independent_origin_confirmed: false, origin_unknown: true },
        observations: [{
          source_id: 'tdx_news_sentiment', publisher_name: null, collection_platform_name: 'TDX 舆情电讯',
          source_relation: 'customer_authorized_feed', access_path: 'authorized_api', evidence_level: 'tier_1',
          published_at: '2026-07-22T10:30:18+08:00', fetched_at: '2026-07-22T10:30:24+08:00', source_url: null,
          content_hash: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', degraded_reasons: ['original_url_unavailable'],
        }],
        a_share: {
          state: 'mapped', level: 'direct_stock',
          matches: [
            { kind: 'stock', code: '605028', name: '世茂能源', match_by: 'explicit_name', name_consistency: 'matched' },
            { kind: 'industry', code: '881459.SH', name: '电力', match_by: 'derived_from_stock' },
          ],
          coverage: { missing: [] },
          market_check: { state: 'pending_next_session', label: '下一交易日待观察' },
          observation: { status: 'waiting', target_trade_date: '2026-07-23', progress: { complete: 1, total: 3 }, checkpoints: [] },
        },
      },
      {
        event_id: 'event-audit-sector',
        title: '半导体行业盘中出现新产能消息',
        content: '公开信息显示，多家半导体企业披露新产能进展，行业映射仅用于后续观察。',
        time: '2026-07-22 10:22:05',
        published_at: '2026-07-22T10:22:05+08:00',
        fetched_at: '2026-07-22T10:22:11+08:00',
        evidence_level: 'tier_2',
        corroboration: { collection_endpoint_count: 1, independent_origin_count: 1, independent_origin_confirmed: false, origin_unknown: false },
        observations: [{
          source_id: 'cls_telegraph', publisher_name: '财联社', collection_platform_name: '财联社电报',
          source_relation: 'original_publisher', access_path: 'public_web', evidence_level: 'tier_2',
          published_at: '2026-07-22T10:22:05+08:00', fetched_at: '2026-07-22T10:22:11+08:00',
          source_url: 'https://www.cls.cn/detail/1234567', content_hash: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', degraded_reasons: [],
        }],
        a_share: {
          state: 'mapped', level: 'direct_sector',
          matches: [{ kind: 'industry', code: '881319.SH', name: '半导体', match_by: 'sector_catalog' }],
          coverage: { missing: [] },
          market_check: { state: 'observation_only', label: '同日盘面观察（非因果）' },
          observation: {
            status: 'observed', target_trade_date: '2026-07-22', progress: { complete: 3, total: 3 }, causality: 'not_inferred',
            result: { stocks: [], sectors: [{ code: '881319.SH', name: '半导体', median_change_pct: 1.2, up_ratio_pct: 64.3 }] },
          },
        },
      },
      {
        event_id: 'event-audit-candidate',
        title: '钙钛矿电池研究取得新进展',
        content: '研究机构公布实验进展，相关概念仍需公司与行业关系进一步核验。',
        time: '2026-07-22 10:18:40',
        published_at: '2026-07-22T10:18:40+08:00', fetched_at: '2026-07-22T10:18:48+08:00',
        observations: [],
        a_share: { state: 'candidate', level: 'topic_candidate', matches: [], topic_candidates: [{ kind: 'concept', code: '880655.SH', name: '钙钛矿电池' }], coverage: { missing: ['未通过公司或行业精确映射'] } },
      },
    ],
  },
  '/api/capital/trends': {
    ok: true,
    ts: '2026-07-22 10:31:36',
    snap_ts: '2026-07-22 10:30:00',
    today: '2026-07-22',
    days: 1,
    view_state: 'last_good',
    sector_source: 'TDX·板块资金(realtime)',
    hot_source: '新浪资金流估算口径',
    sector_date: '2026-07-22',
    hot_date: '2026-07-21',
    stale_sector: false,
    stale_hot: true,
    last_good_sector: false,
    last_good_hot: true,
    sectors: [
      { name: '工业金属', main_net: 138.6, change_pct: 2.14, rnk: 1, value_unit: 'tdx_raw_unverified', unit_verified: false, series: [{ t: '09:45', v: 92.1 }, { t: '10:00', v: 116.4 }, { t: '10:15', v: null }, { t: '10:30', v: 138.6 }] },
      { name: '贵金属', main_net: 94.2, change_pct: 5.82, rnk: 2, value_unit: 'tdx_raw_unverified', unit_verified: false, series: [{ t: '09:45', v: 51.8 }, { t: '10:00', v: 76.4 }, { t: '10:15', v: 88.9 }, { t: '10:30', v: 94.2 }] },
      { name: '半导体', main_net: -72.8, change_pct: null, rnk: 9, value_unit: 'tdx_raw_unverified', unit_verified: false, series: [{ t: '09:45', v: -21.2 }, { t: '10:00', v: -38.5 }, { t: '10:15', v: -54.1 }, { t: '10:30', v: -72.8 }] },
    ],
    hot_stocks: [
      { code: '601899', name: '紫金矿业', main_net: 2520000000, change_pct: 2.31, rnk: 1, value_unit: 'CNY', unit_verified: true, series: [{ t: '09:45', v: 1180000000 }, { t: '10:00', v: 1840000000 }, { t: '10:15', v: 2190000000 }, { t: '10:30', v: 2520000000 }] },
      { code: '300750', name: '宁德时代', main_net: 1480000000, change_pct: 1.52, rnk: 2, value_unit: 'CNY', unit_verified: true, series: [{ t: '09:45', v: 710000000 }, { t: '10:00', v: 990000000 }, { t: '10:15', v: null }, { t: '10:30', v: 1480000000 }] },
      { code: '002594', name: '比亚迪', main_net: -3820000000, change_pct: -1.17, rnk: 18, value_unit: 'CNY', unit_verified: true, series: [{ t: '09:45', v: -1460000000 }, { t: '10:00', v: -2380000000 }, { t: '10:15', v: -3160000000 }, { t: '10:30', v: -3820000000 }] },
      { code: '300308', name: '中际旭创', main_net: null, change_pct: null, rnk: null, value_unit: 'CNY', unit_verified: true, series: [] },
    ],
  },
  '/api/capital/snapshot': {
    ok: true,
    complete: true,
    ts: '2026-07-22 10:31:42',
    sectors: 3,
    hot_stocks: 4,
    missing: [],
    sector_date: '2026-07-22',
    hot_date: '2026-07-22',
  },
  '/api/timing': {
    ok: true,
    ts: '2026-07-22 10:31:24',
    data_date: '2026-07-21',
    stale: true,
    missing: [
      '贪恐为截至 2026-07-21 收盘的日线温度，非今日盘中实时',
      '北向资金实时数据已停止公布，资金因子改用两市主力净流入',
    ],
    fear_greed: {
      ok: true,
      score: 60.1,
      label: '贪婪',
      data_date: '2026-07-21',
      stale: true,
      factors: { '涨跌家数比': 58.4, '涨停温度': 76.2, '成交量比': 64.1, '资金': null, '波动反向': 41.7 },
      meta: { up: 2986, down: 2128, flat: 116, limit_up: 61, limit_down: 19, vol_ratio: 1.37, vol_pctl: 58.3, main_flow_total: null },
      missing: [
        '贪恐为截至 2026-07-21 收盘的日线温度，非今日盘中实时',
        '两市主力净流入(资金因子缺失)',
        '北向资金实时数据已停止公布，资金因子改用两市主力净流入',
      ],
    },
    indexes: [
      { code: 'sh000001', name: '上证指数', ok: true, price: 3548.26, change_pct: 0.42, score: 68.4, zone: '出击', advice: '均线由空转多/底部抬升，出击区——试探性建仓、控制在半仓内，站稳再加。', factors: { '趋势': 72.5, '动量': 61.8, '量能': 66.2, '位置': 70.1 }, ma: { price: 3548.26, ma5: 3528.41, ma20: 3496.72, ma60: 3482.16 }, vol_ratio: 1.21, bars: 160, missing: [] },
      { code: 'sz399001', name: '深证成指', ok: true, price: 10829.31, change_pct: 0.71, score: 74.2, zone: '进攻', advice: '多头排列且量价配合，趋势进攻区——可顺势重仓持有，回踩 MA20 不破即持。', factors: { '趋势': 80.3, '动量': 71.7, '量能': 68.8, '位置': 72.4 }, ma: { price: 10829.31, ma5: 10721.43, ma20: 10584.62, ma60: 10491.38 }, vol_ratio: 1.28, bars: 160, missing: [] },
      { code: 'sz399006', name: '创业板指', ok: true, price: 2267.45, change_pct: -0.18, score: 53.6, zone: '震荡', advice: '均线纠缠、价格围绕 MA20 反复，震荡区——轻仓高抛低吸做波段，不追涨杀跌。', factors: { '趋势': 51.2, '动量': 47.6, '量能': 58.9, '位置': 55.3 }, ma: { price: 2267.45, ma5: 2271.16, ma20: 2262.48, ma60: 2258.39 }, vol_ratio: 1.04, bars: 160, missing: [] },
      { code: 'sh000688', name: '科创50', ok: true, price: null, change_pct: null, score: 39.8, zone: '防守', advice: '空头排列且价在均线之下，防守区——以持币观望为主，不抄底、等趋势修复。', factors: { '趋势': 32.5, '动量': 36.4, '量能': 49.1, '位置': 43.7 }, ma: { price: 1039.82, ma5: 1048.31, ma20: 1062.48, ma60: 1084.76 }, vol_ratio: 0.92, bars: 160, missing: ['实时报价缺失，区域使用最近收盘日线'] },
    ],
    stock: null,
  },
  '/api/reports/morning': {
    ok: true,
    rtype: 'morning',
    date: '2026-07-22',
    title: '早盘内参',
    created_at: '2026-07-22 08:50:12',
    report_version: 2,
    content_hash: '8f4b7d19a6e2d843e05809d661c5f8a1',
    pushed: false,
    claim_validation: { status: 'passed' },
    advice_governance: { status: 'passed', proposal_only: true },
    cockpit: {
      as_of: '2026-07-22 08:50',
      command: {
        risk_level: 3,
        level_name: '防守观察',
        today_action: '等待集合竞价确认，不追一致加速，只观察主线回踩承接。',
        position_cap: '总仓不超过 40%',
        first_principle: '先确认今日数据与集合竞价，不用昨日结论直接下单。',
        forbid: ['不追高开加速', '不参与后排跟风', '不因缺失数据补仓'],
      },
      dir: [
        { name: '半导体', change_pct: 2.83, main_flow: 21600000000 },
        { name: '通信设备', change_pct: 1.48, main_flow: 11090000000 },
        { name: '光学光电子', change_pct: -0.61, main_flow: 5050000000 },
      ],
    },
    content_md: `# 早盘内参 · 2026-07-22

> 本页候选基于 T-1 收盘数据，均为盘前候选（非今日可执行）。实际动作须通过集合竞价和风险闸确认。

## 一、今日行动

**今天能不能做：防守观察。** 只观察主线内有承接的机会，不追高开一致。

- 先看集合竞价量价结构。
- 再核对方向资金是否续流。
- 任一关键数据缺失时，不新增风险暴露。

## 二、隔夜市场影响

海外科技股偏强，但 A 股昨日已先行反应。外部信号只作风险偏好辅助，不直接生成买入结论。

## 三、重点方向

| 排序 | 方向 | 盘前变化 | 资金快照 | 今日处理 |
| --- | --- | ---: | ---: | --- |
| 1 | 半导体 | +2.83% | 216.0 亿 | 等回踩承接 |
| 2 | 通信设备 | +1.48% | 110.9 亿 | 观察资金续流 |
| 3 | 光学光电子 | -0.61% | 50.5 亿 | 不追后排 |

## 四、验证与失效条件

如果 10:00 前方向资金转为净流出、核心股高开低走或跌停风险扩散，立即转为防守，不开新仓。

## 五、数据健康

报告只引用已接入数据；未接入字段保持缺失，不使用零值或推测值补齐。`,
  },
  '/api/reports/tail': {
    ok: true,
    rtype: 'tail',
    date: '2026-07-22',
    title: '尾盘决策定稿',
    created_at: '2026-07-22 14:52:18',
    report_version: 3,
    content_hash: '3b2c711d5e044c1bb7c091b8fd6826ae',
    pushed: false,
    claim_validation: { status: 'passed' },
    advice_governance: { status: 'passed', proposal_only: true },
    cockpit: {
      as_of: '2026-07-22 14:52',
      command: {
        risk_level: 3,
        level_name: '防守观察',
        today_action: '只观察主线回踩后重新转强的到价机会，不抢尾盘一致加速。',
        position_cap: '总仓不超过 40%',
        first_principle: '不到计划价格不动手，任一关键证据缺失时放弃新增风险暴露。',
        forbid: ['不追尾盘急拉', '不临时新增计划外标的', '不因错过而抬高买点'],
      },
    },
    content_md: `# 尾盘决策定稿 · 2026-07-22

> 本报告按 14:52 真实数据定格。候选提案不是下单指令，实际动作仍须满足价格条件与风险闸。

## 一、今日结论

**今天能不能做：仅看不抢。** 市场处于结构轮动，尾盘只核对计划内标的是否进入真实区间。

## 二、可执行条件

| 标的 | 买入前提 | 不买条件 | 仓位边界 |
| --- | --- | --- | ---: |
| 计划内候选 | 回踩后承接重新确认 | 尾盘急拉或关键数据缺失 | 单标的不超过 10% |

## 三、失效与次日验证

- 收盘前资金转为净流出则取消候选。
- T+1 只按报告已冻结条件回验，不事后修改原判断。

## 四、数据健康

报告只引用已接入的真实数据；缺失字段保持缺失，不使用零值或推测值补齐。`,
  },
  '/api/reports/close': {
    ok: true,
    rtype: 'close',
    date: '2026-07-22',
    title: '收盘复盘',
    created_at: '2026-07-22 15:31:26',
    report_version: 1,
    content_hash: '91ab74f2d6068b3d3de74b501e138b92',
    pushed: false,
    claim_validation: { status: 'passed' },
    advice_governance: { status: 'passed', proposal_only: true },
    cockpit: {
      as_of: '2026-07-22 15:30',
      command: {
        risk_level: 3,
        level_name: '中等风险',
        today_action: '收盘后先复核计划偏差与风险暴露，不把今日强势直接外推为明日买入结论。',
        position_cap: '总仓不超过 40%',
        first_principle: '先对账、再归因；下一交易日必须重新通过风险闸。',
        forbid: ['不事后改写早盘判断', '不把未触发推荐计入胜率', '不因单日盈利扩大次日仓位'],
      },
    },
    content_md: `# 今日复盘 · 2026-07-22

> 本报告按 15:30 收盘事实定格。复盘用于验证与修正，不把后续信息回写成当时已知事实。

## 一、今日结论

**市场维持结构轮动。** 今日计划内方向有分化，执行重点是核对是否遵守价格条件，而不是只看收盘涨跌。

## 二、盘前计划逐条对账

| 计划项 | 收盘事实 | 判定 | 明日处理 |
| --- | --- | --- | --- |
| 主线回踩承接 | 尾盘承接仍在，但未形成一致加速 | 部分验证 | 等下一交易日重新确认 |
| 不追高开加速 | 盘中高位分歧扩大 | 纪律有效 | 继续保留禁止条件 |

## 三、赚钱与亏钱效应

- 赚钱效应集中在有容量承接的主线前排。
- 后排跟风与盘中追高仍是主要风险来源。
- 缺少可比价格证据的推荐保持不可比，不计入胜率。

## 四、推荐与持仓复核

推荐回验按 T0/T1/T2/T5 目标交易日追加记录；历史建议不覆盖，未触发和证据不足不计输赢。

## 五、明日修正与数据健康

    下一交易日先重新确认风险等级、集合竞价与资金承接。接口缺失保持缺失，不使用零值或推测值补齐。`,
  },
  '/api/reports/evening': {
    ok: true,
    rtype: 'evening',
    date: '2026-07-22',
    title: '次日策略（供 2026-07-23 使用）',
    created_at: '2026-07-22 19:31:08',
    report_version: 1,
    plan_version: 1,
    content_hash: 'bfc63d3174a62653437a39f0154a8d19',
    pushed: true,
    claim_validation: { status: 'passed' },
    advice_governance: { status: 'passed', proposal_only: true },
    input_lineage: {
      schema_version: '1.0', trade_day: '2026-07-22', lineage_hash: '6d159bc2957d78b0bb214e06179b0bc1',
      sources: [
        { rtype: 'tail', trade_day: '2026-07-22', version: 1, content_hash: '7ab37b03991b819e251ae0d5dcc091ee', created_at: '2026-07-22 14:53:05' },
        { rtype: 'close', trade_day: '2026-07-22', version: 1, content_hash: '91ab74f2d6068b3d3de74b501e138b92', created_at: '2026-07-22 15:31:26' },
      ],
    },
    cockpit: {
      as_of: '2026-07-22 19:30',
      command: {
        risk_level: 3, level_name: '防守观察', trade_state: '条件参与', decision_tradeable: true,
        today_action: '明日只核对计划内标的是否回到服务端价格区间并重新确认承接，不因高开临时抬价。',
        position_cap: '总仓不超过 40%；单标的不超过 10%',
        first_principle: '不到计划价格不动手，任一关键证据缺失则放弃新增风险暴露。',
        allow: ['回到计划区间后出现可复核承接', '开盘风险闸未升级且服务端状态仍允许参与'],
        forbid: ['不追高开加速', '不新增计划外标的', '关键行情或区间缺失时不交易'],
        blackswan: ['集合竞价风险闸升级到禁止参与', '核心方向出现不可解释的集中跌停'],
      },
    },
    content_md: `# 次日策略 · 2026-07-23

> 本策略依据 2026-07-22 尾盘与收盘正式定稿生成。它不是下单指令，明日开盘后必须重新通过实时风险闸。

## 一、当前结论

**只做计划内的条件参与。** 风险等级为 3 级，优先等待价格与承接同时满足，不追高开一致加速。

## 二、明日唯一动作

开盘后先核对风险闸，再检查观察池是否进入服务端给出的目标区间；没有完整证据就继续等待。

## 三、仓位与禁止条件

| 边界 | 服务端定稿 |
| --- | --- |
| 总仓上限 | 不超过 40% |
| 单标的上限 | 不超过 10% |
| 禁止项 | 追高开加速、计划外临时交易、缺失证据下交易 |

## 四、观察池验证

- 紫金矿业：只在计划区间内重新确认承接。
- 宁德时代：当前接近区间上沿，不追价。
- 区间缺失的标的继续观察，不以零值代替。

## 五、失效条件

风险闸升级、核心方向集中转弱或报价证据陈旧时，原计划立即暂停。

## 六、数据健康与治理

尾盘和收盘来源指纹齐备；历史事实采用追加式留痕，后续信息不得回写原报告。`,
  },
  '/api/holdings/advice-review': {
    ok: true,
    base_date: '2026-07-21',
    stats: { hit: 2, miss: 1, neutral: 1, scored: 3, win_rate: 66.7 },
    missing: [],
    rows: [
      { code: '601899', name: '紫金矿业', base_price: 18.26, today_price: 18.68, change_pct: 2.30, tendency: '持有观察，跌破条件则减仓', direction: '看多', verdict: '兑现' },
      { code: '300750', name: '宁德时代', base_price: 283.11, today_price: 287.22, change_pct: 1.45, tendency: '只持有，不追高加仓', direction: '看多', verdict: '兑现' },
      { code: '002594', name: '比亚迪', base_price: 114.39, today_price: 113.05, change_pct: -1.17, tendency: '等待反抽减仓', direction: '看空', verdict: '兑现' },
      { code: '300308', name: '中际旭创', base_price: 162.44, today_price: null, change_pct: null, tendency: '行情证据缺失时保持观察', direction: '中性', verdict: '—' },
    ],
  },
  '/api/holdings': {
    ok: true,
    holdings: [
      { code: '601899', name: '紫金矿业', cost: 17.42, shares: 6000, industry: '工业金属', weight_pct: 16.48, market_value: 112080, note: '核心观察仓，等待服务端风险条件复核', risk_plan: { version: 1, period: 'short', buy_date: '2026-07-16', customer_risk_price: 17.60, customer_target_price: 19.20, target_mode: 'reminder', confirmed_reduction_pct: 0 } },
      { code: '300750', name: '宁德时代', cost: 274.80, shares: 300, industry: '电池', weight_pct: 12.67, market_value: 86166, note: '只按计划处理，不临时追价', risk_plan: { version: 1, period: 'swing', buy_date: '2026-07-14', customer_risk_price: null, customer_target_price: 295.00, target_mode: 'ordinary_trigger', confirmed_reduction_pct: 25 } },
      { code: '002594', name: '比亚迪', cost: 116.20, shares: 500, industry: '汽车整车', weight_pct: 8.31, market_value: 56525, note: '', risk_plan: { version: 1, period: 'ultra', buy_date: '2026-07-22', customer_risk_price: 109.80, customer_target_price: null, target_mode: 'reminder', confirmed_reduction_pct: 0 } },
    ],
    risk_states: {
      '601899': { code: '601899', name: '紫金矿业', plan_version: 1, period: 'short', period_label: '短线', period_window: '3–5个交易日', state: 'pending_adjustment', action: '建议将该持仓降低33%', target_reduction_pct: 33, retain_pct: 67, confirmed_reduction_pct: 0, ordinary_trigger_count: 2, ordinary_triggers: [{ code: 'time_expired', label: '已超出短线区间（3–5个交易日）' }, { code: 'stagnation_group', label: '收益观察区内出现2项滞涨特征' }], decisive_triggers: [], near_risk_line: false, time: { trading_days_held: 5, max_days: 5, expired: true }, initial_risk_line: 16.88, current_risk_line: 17.75, customer_risk_price: 17.60, customer_target_price: 19.20, risk_distance_pct: 4.98, initial_r: 0.54, r_multiple: 2.33, fresh: true, stale: false, evaluated_at: '2026-07-22T10:30:00+08:00', data_as_of: '2026-07-22 10:00:00' },
      '300750': { code: '300750', name: '宁德时代', plan_version: 1, period: 'swing', period_label: '波段', period_window: '5–10个交易日', state: 'completed_adjustment', action: '已完成当前调整，继续监控', target_reduction_pct: 25, retain_pct: 75, confirmed_reduction_pct: 25, ordinary_trigger_count: 1, ordinary_triggers: [], decisive_triggers: [], near_risk_line: false, target_touched: true, target_note: '达到客户目标价，仅提醒', time: { trading_days_held: 7, max_days: 10, expired: false }, initial_risk_line: 260.10, current_risk_line: 278.40, customer_risk_price: null, customer_target_price: 295.00, risk_distance_pct: 3.07, initial_r: 14.70, r_multiple: 0.84, fresh: true, stale: false, evaluated_at: '2026-07-22T10:30:00+08:00', data_as_of: '2026-07-21' },
      '002594': { code: '002594', name: '比亚迪', plan_version: 1, period: 'ultra', period_label: '超短', period_window: '1–3个交易日', state: 'monitoring', action: '当前无需调整仓位', target_reduction_pct: 0, retain_pct: 100, confirmed_reduction_pct: 0, ordinary_trigger_count: 0, ordinary_triggers: [], decisive_triggers: [], near_risk_line: true, time: { trading_days_held: 1, max_days: 3, expired: false }, initial_risk_line: 112.20, current_risk_line: 112.40, customer_risk_price: 109.80, customer_target_price: null, risk_distance_pct: 0.57, initial_r: 4.00, r_multiple: -0.79, execution_constraints: ['买入当日受T+1限制，可调整数量可能为0'], fresh: true, stale: false, evaluated_at: '2026-07-22T10:30:00+08:00', data_as_of: '2026-07-22 10:25:00' },
    },
  },
  '/api/holdings/risk': {
    ok: true,
    schema_version: 'holding-risk-evaluation-v1',
    algorithm_version: '1.0',
    states: {
      '601899': { code: '601899', name: '紫金矿业', plan_version: 1, period: 'short', period_label: '短线', period_window: '3–5个交易日', state: 'pending_adjustment', action: '建议将该持仓降低33%', target_reduction_pct: 33, retain_pct: 67, confirmed_reduction_pct: 0, ordinary_trigger_count: 2, ordinary_triggers: [{ code: 'time_expired', label: '已超出短线区间（3–5个交易日）' }, { code: 'stagnation_group', label: '收益观察区内出现2项滞涨特征' }], decisive_triggers: [], near_risk_line: false, time: { trading_days_held: 5, max_days: 5, expired: true }, initial_risk_line: 16.88, current_risk_line: 17.75, customer_risk_price: 17.60, customer_target_price: 19.20, risk_distance_pct: 4.98, initial_r: 0.54, r_multiple: 2.33, fresh: true, stale: false, evaluated_at: '2026-07-22T10:30:00+08:00', data_as_of: '2026-07-22 10:00:00' },
      '300750': { code: '300750', name: '宁德时代', plan_version: 1, period: 'swing', period_label: '波段', period_window: '5–10个交易日', state: 'completed_adjustment', action: '已完成当前调整，继续监控', target_reduction_pct: 25, retain_pct: 75, confirmed_reduction_pct: 25, ordinary_trigger_count: 1, ordinary_triggers: [], decisive_triggers: [], near_risk_line: false, target_touched: true, target_note: '达到客户目标价，仅提醒', time: { trading_days_held: 7, max_days: 10, expired: false }, initial_risk_line: 260.10, current_risk_line: 278.40, customer_risk_price: null, customer_target_price: 295.00, risk_distance_pct: 3.07, initial_r: 14.70, r_multiple: 0.84, fresh: true, stale: false, evaluated_at: '2026-07-22T10:30:00+08:00', data_as_of: '2026-07-21' },
      '002594': { code: '002594', name: '比亚迪', plan_version: 1, period: 'ultra', period_label: '超短', period_window: '1–3个交易日', state: 'monitoring', action: '当前无需调整仓位', target_reduction_pct: 0, retain_pct: 100, confirmed_reduction_pct: 0, ordinary_trigger_count: 0, ordinary_triggers: [], decisive_triggers: [], near_risk_line: true, time: { trading_days_held: 1, max_days: 3, expired: false }, initial_risk_line: 112.20, current_risk_line: 112.40, customer_risk_price: 109.80, customer_target_price: null, risk_distance_pct: 0.57, initial_r: 4.00, r_multiple: -0.79, execution_constraints: ['买入当日受T+1限制，可调整数量可能为0'], fresh: true, stale: false, evaluated_at: '2026-07-22T10:30:00+08:00', data_as_of: '2026-07-22 10:25:00' },
    },
  },
  '/api/quotes': {
    ok: true,
    quotes: {
      '601899': { code: '601899', name: '紫金矿业', price: 18.68, change_pct: 2.30, source: 'TDX API', stale: false, as_of: '2026-07-22 10:31:18', quote_note: null },
      '300750': { code: '300750', name: '宁德时代', price: 287.22, change_pct: 1.45, source: '腾讯财经', stale: false, as_of: '2026-07-22 10:31:19', quote_note: null },
      '002594': { code: '002594', name: '比亚迪', price: 113.05, change_pct: -1.17, source: 'TDX API', stale: false, as_of: '2026-07-22 10:31:18', quote_note: null },
    },
    missing: [],
  },
  '/api/recommendations/stats': {
    ok: true,
    win_rate_unit: 'percent',
    enough_sample: false,
    minimum_stable_sample: 30,
    sample: 18,
    win_rate: null,
    observed_win_rate: 61.1,
    avg_win: 3.24,
    avg_loss: -2.28,
    profit_factor: 1.42,
    waiting: 5,
    untriggered: 3,
    incomparable: 2,
    excluded: 1,
    per_window: {
      T0: { sample: 6, observed_win_rate: 66.7 },
      T1: { sample: 5, observed_win_rate: 60.0 },
      T2: { sample: 4, observed_win_rate: 50.0 },
      T5: { sample: 3, observed_win_rate: 66.7 },
    },
  },
  '/api/recommendations': {
    ok: true,
    recommendations: [
      { rec_id: 'rec-1', code: '601899', name: '紫金矿业', source: 'tail', rec_price: 18.62, review: { window: 'T1', evaluation_state: 'waiting', verdict: 'pending', target_trade_day: '2026-07-23' }, review_windows: { T0: { window: 'T0', evaluation_state: 'evaluated', verdict: 'hit', final_return: 0.32, directional_return: 0.32, max_return: 1.28, max_drawdown: -0.42, target_trade_day: '2026-07-22' }, T1: { window: 'T1', evaluation_state: 'waiting', target_trade_day: '2026-07-23' } } },
      { rec_id: 'rec-2', code: '300750', name: '宁德时代', source: 'tail', rec_price: 286.40, review: { window: 'T0', evaluation_state: 'evaluated', verdict: 'hit', final_return: 0.29, directional_return: 0.29, max_return: 1.16, max_drawdown: -0.68, target_trade_day: '2026-07-22' }, review_windows: { T0: { window: 'T0', evaluation_state: 'evaluated', verdict: 'hit', target_trade_day: '2026-07-22' } } },
      { rec_id: 'rec-3', code: '002594', name: '比亚迪', source: 'tail', rec_price: 112.36, review: { window: 'T1', evaluation_state: 'untriggered', verdict: 'not_triggered', target_trade_day: '2026-07-23' }, review_windows: { T0: { window: 'T0', evaluation_state: 'incomparable', verdict: 'insufficient_evidence', target_trade_day: '2026-07-22' }, T1: { window: 'T1', evaluation_state: 'untriggered', verdict: 'not_triggered', target_trade_day: '2026-07-23' } } },
    ],
  },
  '/api/watchlist': {
    ok: true,
    ts: '2026-07-22 10:31:22',
    missing: [],
    items: [
      {
        id: 'watch-audit-1', code: '601899', name: '紫金矿业', sector: '工业金属', grade: 'A', status: '在理想买入区间内',
        risk_level: 2, add_reason: '用户记录：等待价格进入计划区间后再复核。', trigger_condition: '服务端区间内且用户条件仍成立',
        no_buy_condition: '价格脱离计划区间时不追', abandon_condition: '用户记录的逻辑失效条件', abandon_price: 17.66, note: '审计固定 fixture，仅用于语义与布局验证',
        manual_target_low: null, manual_target_high: null, target_low: 18.42, target_high: 18.76,
        effective_target_low: 18.42, effective_target_high: 18.76, target_source: 'auto_ultra', created_at: '2026-07-18 09:12:00', missing: [],
        quote: { name: '紫金矿业', price: 18.68, change_pct: 2.31, as_of: '2026-07-22 10:31:18', stale: false, source: 'TDX API' },
        ideal_buy_zones: {
          schema_version: 'watchlist-dual-zone-v2', algorithm_version: '2.0', ok: true, calculated_at: '2026-07-22 10:30:00', missing: [],
          ultra: { state: 'complete', low: 18.42, high: 18.76, position: 'IN_ZONE', data_date: '2026-07-22', stale: false, missing: [], metrics: { sample_days: 3 } },
          swing: { state: 'complete', low: 17.88, high: 18.35, position: 'ABOVE_ZONE', data_date: '2026-07-21', stale: false, missing: [], metrics: { daily_count: 60 } },
        },
      },
      {
        id: 'watch-audit-2', code: '300750', name: '宁德时代', sector: '电池', grade: 'B', status: '略高于上沿，继续观察',
        risk_level: 3, add_reason: '用户记录：只等待条件，不追价。', trigger_condition: '价格回到手工边界并重新核对',
        no_buy_condition: '服务端状态未进入计划区间', abandon_condition: '', abandon_price: null, note: '',
        manual_target_low: 282.10, manual_target_high: null, target_low: 282.10, target_high: null,
        effective_target_low: 282.10, effective_target_high: null, target_source: 'manual_override', created_at: '2026-07-19 14:20:00', missing: [],
        quote: { name: '宁德时代', price: 287.22, change_pct: 1.52, as_of: '2026-07-22 10:28:05', stale: true, source: '腾讯财经' },
        ideal_buy_zones: {
          schema_version: 'watchlist-dual-zone-v2', algorithm_version: '2.0', ok: true, calculated_at: '2026-07-22 10:25:00', missing: ['超短线本次刷新失败'],
          ultra: { state: 'degraded', low: 282.10, high: 286.80, position: 'ABOVE_ZONE', data_date: '2026-07-22', stale: true, missing: ['本次刷新失败，展示同交易日最近成功结果'] },
          swing: { state: 'complete', low: 274.30, high: 280.90, position: 'ABOVE_ZONE', data_date: '2026-07-21', stale: false, missing: [] },
        },
      },
      {
        id: 'watch-audit-3', code: '300308', name: '中际旭创', sector: null, grade: null, status: '理想买入区间缺失',
        risk_level: 4, add_reason: '', trigger_condition: '', no_buy_condition: '数据不完整时不动作', abandon_condition: '', abandon_price: null, note: '',
        manual_target_low: null, manual_target_high: null, target_low: null, target_high: null,
        effective_target_low: null, effective_target_high: null, target_source: 'missing', created_at: '2026-07-20 11:00:00', missing: ['现价', '理想买入区间'], quote: null,
        identity_state: 'quote_unavailable',
        ideal_buy_zones: {
          schema_version: 'watchlist-dual-zone-v2', algorithm_version: '2.0', ok: false, missing: ['日线不足60根', '5分钟线不足3个交易日'],
          ultra: { state: 'missing', low: null, high: null, position: null, data_date: '2026-07-22', missing: ['5分钟线不足3个交易日'] },
          swing: { state: 'missing', low: null, high: null, position: null, data_date: '2026-07-21', missing: ['日线不足60根'] },
        },
      },
      {
        id: 'watch-audit-4', code: '600000', name: '浦发银行', sector: '银行', grade: 'C', status: '低于理想买入区间',
        risk_level: 3, add_reason: '等待服务端价格条件重新确认。', trigger_condition: '重新进入生效区间',
        no_buy_condition: '关键证据缺失时不动作', abandon_condition: '跌破用户记录的放弃价', abandon_price: 11.72, note: '',
        manual_target_low: null, manual_target_high: null, target_low: 12.08, target_high: 12.24,
        effective_target_low: 12.08, effective_target_high: 12.24, target_source: 'auto_ultra', created_at: '2026-07-21 09:40:00', missing: [],
        quote: { name: '浦发银行', price: 11.96, change_pct: -0.42, as_of: '2026-07-22 10:30:44', stale: false, source: 'TDX API' },
        ideal_buy_zones: {
          schema_version: 'watchlist-dual-zone-v2', algorithm_version: '2.0', ok: true, calculated_at: '2026-07-22 10:30:00', missing: [],
          ultra: { state: 'complete', low: 12.08, high: 12.24, position: 'BELOW_ZONE', data_date: '2026-07-22', stale: false, missing: [] },
          swing: { state: 'complete', low: 11.80, high: 12.02, position: 'IN_ZONE', data_date: '2026-07-21', stale: false, missing: [] },
        },
      },
    ],
  },
  '/api/quote/600000': {
    ok: false, state: 'quote_unavailable', recognized: true,
    error: '股票已识别，但当前报价源暂未返回', identity: { name: '浦发银行' }, quote: null,
  },
  '/api/quote/600519': {
    ok: true, state: 'complete', recognized: true,
    identity: { code: '600519', known: true, name: '贵州茅台', universe_complete: true, universe_as_of: '2026-07-22' },
    quote: {
      code: '600519', name: '贵州茅台', price: 1488.52, prev_close: 1470.00, change_pct: 1.26,
      open: 1472.00, high: 1496.80, low: 1468.20, amount: 4238000000,
      turnover_rate: 0.42, volume_ratio: 1.18, pe: 24.62, pb: 7.82,
      source: 'TDX 数据 API + 腾讯字段补齐', stale: false,
      as_of: '2026-07-22 10:31:38',
    },
  },
  '/api/diagnose': {
    ok: true, code: '600519', name: '贵州茅台', recognized: true,
    action: '观望', signal_action: '买入', original_action: '买入', conviction: '低', confidence: 0.38,
    position_cap_pct: null,
    entry_zone: '1470.00–1484.70（结构支撑区）',
    stop: '收盘有效跌破 昨收 1470.00，且下一交易日不能收回',
    summary: '趋势偏强（深度规则 +5 分），但账户与风控授权证据未齐，当前只能观望；等待回踩支撑企稳。',
    missing: ['基础财务本次未返回', '账户净值证据缺失'],
    analysis_mode: 'deterministic_deep', analysis_status: 'partial',
    decision_authority: 'deep_deterministic_analysis+risk_gate',
    decision_generated_at: '2026-07-22T10:31:46+08:00',
    analysis_pipeline: {
      rule_version: 'diagnosis-pipeline-v1', completed: 7, total: 8, elapsed_ms: 2860,
      data_as_of: '2026-07-22 10:31:44', quote_stale: false,
      quote_note: '盘口为盘中快照；技术结构使用前复权日线。',
      stages: [
        { id: 'trend', label: '趋势结构', status: 'complete', score: 2, summary: '收盘价位于 MA20 上方，趋势偏强', source: '前复权日K', data_date: '2026-07-22' },
        { id: 'momentum', label: '动量', status: 'complete', score: 1, summary: 'RSI 与 MACD 未进入过热区', source: '前复权日K', data_date: '2026-07-22' },
        { id: 'volume', label: '量价', status: 'complete', score: 1, summary: '量比温和放大，换手率保持低位', source: '实时盘口' },
        { id: 'volatility', label: '波动', status: 'complete', score: 0, summary: 'ATR 波动未触发异常阈值', source: '前复权日K' },
        { id: 'levels', label: '结构位', status: 'complete', score: 1, summary: '1470.00 附近为服务端识别支撑', source: '日K结构位' },
        { id: 'flow', label: '资金', status: 'complete', score: 0, summary: '主力净额为正，仅作增强证据', source: '新浪资金流估算口径' },
        { id: 'industry', label: '行业', status: 'complete', score: 0, summary: '所属白酒行业，未据此单独给动作', source: 'TDX relation' },
        { id: 'fundamental', label: '基础财务', status: 'missing', score: null, summary: '基础财务本次未返回', source: 'TDX stock_info' },
      ],
    },
    technical_analysis: {
      structure: '趋势偏强', complete: true, score: 5,
      levels: { support: 1470.00, resistance: 1496.80, support_basis: '昨收', resistance_basis: '当日高点' },
    },
    trade_plan: {
      entry_zone: '1470.00–1484.70（结构支撑区）', entry_trigger: '回踩昨收 1470.00 附近缩量企稳，或放量突破当日高点 1496.80',
      stop: '收盘有效跌破 昨收 1470.00，且下一交易日不能收回', support: 1470.00,
      resistance: 1496.80, invalidation: 1470.00, basis: ['昨收', '当日高点'], rule_version: 'diagnosis-structure-plan-v1',
    },
    risk_decision: {
      decision: 'veto', final_action: '观望', position_cap_pct: 0, effective_confidence: 0.38,
      reasons: ['账户净值与总仓证据不足，当前未授权新增风险'], reason_codes: ['account_nav_missing'],
      rule_version: 'risk-gate-v2', input_snapshot_hash: 'a63f17e22b9c934d4caa',
    },
    deterministic_signal: { signal_score: 5, coverage: 0.875, complete: true, reasons: ['趋势偏强', '量价未过热'], rule_version: 'diagnosis-deep-governance-v1' },
    market_quote: {
      code: '600519', name: '贵州茅台', price: 1488.52, prev_close: 1470.00,
      change_pct: 1.26, open: 1472.00, high: 1496.80, low: 1468.20,
      amount: 4238000000, turnover_rate: 0.42, volume_ratio: 1.18,
      as_of: '2026-07-22 10:31:38', source: 'TDX 数据 API', stale: false,
    },
    market_evidence_quality: { status: 'degraded', captured: 7, requested: 8, missing_fields: ['pe'] },
    decision_market_evidence_quality: { status: 'degraded', captured: 7, requested: 8, missing_fields: ['pe'] },
    market_evidence: [
      { field: 'last_price', value: 1488.52, unit: 'CNY', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-price' },
      { field: 'prev_close', value: 1470.00, unit: 'CNY', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-prev' },
      { field: 'change_pct', value: 1.26, unit: 'percent', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-change' },
      { field: 'amount', value: 4238000000, unit: 'CNY', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-amount' },
      { field: 'turnover_rate', value: 0.42, unit: 'percent', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-turnover' },
      { field: 'volume_ratio', value: 1.18, unit: 'ratio', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-volume' },
      { field: 'pb', value: 7.82, unit: 'ratio', source_id: 'tencent_quote', quality_status: 'degraded', freshness: 'delayed', trading_day: '2026-07-22', evidence_id: 'ev-dg-pb' },
    ],
    decision_market_evidence: [
      { field: 'last_price', value: 1488.52, unit: 'CNY', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-price' },
      { field: 'prev_close', value: 1470.00, unit: 'CNY', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-prev' },
      { field: 'change_pct', value: 1.26, unit: 'percent', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-change' },
      { field: 'amount', value: 4238000000, unit: 'CNY', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-amount' },
      { field: 'turnover_rate', value: 0.42, unit: 'percent', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-turnover' },
      { field: 'volume_ratio', value: 1.18, unit: 'ratio', source_id: 'tdx_quote', quality_status: 'ok', freshness: 'live', trading_day: '2026-07-22', evidence_id: 'ev-dg-volume' },
      { field: 'pb', value: 7.82, unit: 'ratio', source_id: 'tencent_quote', quality_status: 'degraded', freshness: 'delayed', trading_day: '2026-07-22', evidence_id: 'ev-dg-pb' },
    ],
    evidence: [
      { finding: '现价 1488.52，高于昨收 1470.00', direction: '利多', source: 'tdx_quote', evidence_ref: 'evidence:ev-dg-price', rule_version: 'diagnosis-deterministic-evidence-v1' },
      { finding: '当日涨跌幅 +1.26%', direction: '利多', source: 'tdx_quote', evidence_ref: 'evidence:ev-dg-change', rule_version: 'diagnosis-deterministic-evidence-v1' },
      { finding: '量比 1.18，换手 0.42%', direction: '中性', source: 'tdx_quote', evidence_ref: 'evidence:ev-dg-volume', rule_version: 'diagnosis-deterministic-evidence-v1' },
    ],
    model_connection: { configured: true, status: 'not_requested', used_for_diagnosis: false, fallback: null },
    model_explanation_status: 'available_on_request',
    advice_record: { ok: true, blocked: true, recommendation_created: false },
    analysis_contract: { version: 'stock-analysis-pipeline-v1', data_status: 'degraded', data_as_of: '2026-07-22T10:31:46+08:00', decision_authority: 'deep_deterministic_analysis+risk_gate' },
  },
  '/api/chat/conversations': {
    ok: true,
    conversations: [
      { id: 'abc123', title: '贵州茅台盘中承接', mode: 'stock', updated_at: '2026-07-22 10:31:40', msg_count: 4 },
      { id: 'def456', title: '旧自由问答不会出现在本页', mode: 'free', updated_at: '2026-07-22 10:20:00', msg_count: 2 },
    ],
  },
  '/api/chat/perspectives': {
    ok: true,
    max_selected: 4,
    perspectives: [
      ['92kobe', '92科比', 'A股短线与游资'], ['aizaibingchuan', '爱在冰川', 'A股短线与游资'],
      ['chaoguyangjia', '炒股养家', 'A股短线与游资'], ['chenxiaoqun', '陈小群', 'A股短线与游资'],
      ['huadongdandaodan', '华东大导弹', 'A股短线与游资'], ['ningbogansidui', '宁波敢死队', 'A股短线与游资'],
      ['tuixuechaogu', '退学炒股', 'A股短线与游资'], ['yishunliuguang', '一瞬流光', 'A股短线与游资'],
      ['zhaolao-ge', '赵老哥', 'A股短线与游资'], ['asking', 'Asking', 'A股短线与游资'],
      ['beijing-chaogu', '北京炒家', 'A股短线与游资'], ['huanle-haian', '欢乐海岸', 'A股短线与游资'],
      ['qiao-bangzhu', '乔帮主', 'A股短线与游资'], ['zhang-mengzhu', '章盟主', 'A股短线与游资'],
      ['geweidong', '葛卫东', '宏观与趋势交易'], ['renzeping', '任泽平', '宏观与趋势交易'],
      ['livermore', '利弗莫尔', '宏观与趋势交易'],
    ].map(([id, name, category]) => ({ id, name, category, description: `${name}结构化分析视角`, tags: [], version: '2026-08-06' })),
  },
  '/api/chat/data-materials': {
    ok: true,
    max_selected: 4,
    max_auto_selected: 3,
    required: [
      { id: 'quote_time', name: '行情与数据时点' },
      { id: 'risk_boundary', name: '风险边界与失效条件' },
      { id: 'quality_state', name: '缺失、过期和降级状态' },
    ],
    materials: [
      ['technical', '深度技术与量价', '趋势、均线、量价、关键位置、技术结构', false],
      ['fund_flow', '个股资金流', '主力净额、资金日期、来源和可用明细', false],
      ['sector', '板块与题材', '行业、概念、板块联动和题材归属', false],
      ['market_context', '市场环境', '指数、市场宽度、情绪、主线和市场背景', false],
      ['fundamental', '基本面与估值', 'PE、PB、ROE、利润、负债及报告期', false],
      ['events', '公告与事件', '官方公告、互动问答、新闻事件和证据等级', true],
    ].map(([id, name, description, slow]) => ({ id, name, description, slow, availability: 'supported' })),
  },
  '/api/chat/data-materials/preselect': {
    ok: true, preselected_ids: [], max_selected: 4,
  },
  '/api/stock/600519/core': {
    ok: true, state: 'complete', ts: '2026-07-22 10:31:42', recognized: true, missing: [],
    recent_daily: [{ d: '2026-07-22', o: 1472.00, c: 1488.52, h: 1496.80, l: 1468.20, v: 3261547 }],
    recognition: { code: '600519', known: true, name: '贵州茅台', universe_complete: true },
    quote: {
      code: '600519', name: '贵州茅台', price: 1488.52, change_pct: 1.26,
      open: 1472.00, high: 1496.80, low: 1468.20, prev_close: 1470.00,
      amount: 4238000000, turnover_rate: 0.42, volume_ratio: 1.18,
      pe: 23.6, total_mv: 1870000000000, float_mv: 1870000000000,
      high_52w: 1598.00, low_52w: 1268.00, source: 'TDX 数据 API', stale: false,
      as_of: '2026-07-22 10:31:38',
    },
    sections: { quote: { state: 'complete' }, recent_daily: { state: 'complete' } },
  },
  '/api/stock/600519/enrichment': {
    ok: true, state: 'complete', ts: '2026-07-22 10:31:44', flow_date: '2026-07-22',
    board: { industry: '白酒', concepts: ['高股息', '消费龙头'], source: 'TDX relation' },
    flow: { trade_date: '20260722', main_net: 318000000, source: '新浪资金流估算口径', derived: true },
    financial: { end_date: '2026-03-31', roe: 8.42, grossprofit_margin: 91.8, netprofit_yoy: 11.2, debt_to_assets: 18.7, source: 'TDX stock_info' },
    sections: { board: { state: 'complete' }, flow: { state: 'complete' }, financial: { state: 'complete' }, auction: { state: 'unavailable' } },
    missing: ['集合竞价(暂无可验证专用源)'],
  },
  '/api/screen': {
    ok: true,
    ts: '2026-07-22 10:31:48',
    trade_date: '2026-07-22',
    elapsed_ms: 842,
    total_market: 5268,
    matched: 2,
    returned: 2,
    sort_by: 'amount',
    coverage: { received: 5072, expected: 5268, ratio: 0.9628, percent: 96.28, minimum_ratio: 0.8, sufficient: true },
    universe: { count: 5268, complete: true, stale: false, as_of: '2026-07-22', refresh_required: false, missing_exchanges: [] },
    industry_coverage: { filled: 1, returned: 2, queried: 2, query_limit: 12, percent: 50.0 },
    missing: ['行业仅对返回候选低频补全：1/2只已有行业，未取到者保留为空'],
    analysis_contract: {
      version: 'stock-analysis-pipeline-v1', current_stage: 'deterministic_candidate_selection',
      deterministic_rule_version: 'condition-screen-v2', decision_authority: 'candidate_only',
      data_status: 'complete', data_as_of: '2026-07-22', analysis_executed: true,
      ai: { role: 'explanation_only', status: 'not_requested', may_change_deterministic_action: false },
    },
    stocks: [
      { code: '600036', name: '招商银行', industry: '银行', board: '主板', price: 46.38, change_pct: 1.24, turnover_rate: 0.56, volume_ratio: 1.18, pe_ttm: 7.1, pb: 1.14, circ_mv: 957200000000, amount: 3148000000 },
      { code: '300750', name: '宁德时代', industry: null, board: '创业板', price: 282.10, change_pct: -0.73, turnover_rate: 0.92, volume_ratio: null, pe_ttm: 19.8, pb: 4.72, circ_mv: 1186400000000, amount: 5263000000 },
    ],
  },
  '/api/technical-screen/catalog': {
    ok: true,
    algorithm_ver: 'local-signal-v1',
    config_schema_version: 2,
    timeframes: [{ code: 'D', name: '日线' }, { code: 'W', name: '周线' }, { code: 'M', name: '月线' }],
    occurrences: [{ code: 'TODAY', name: '今日刚发生' }, { code: 'RECENT', name: '近N个交易周期内发生' }, { code: 'CURRENT', name: '当前仍然成立' }],
    signals: [
      { signal_code: 'MACD_GOLDEN_CROSS', signal_name: 'MACD金叉', category: '趋势', signal_kind: 'event', direction: 'bullish', default_params: { fast: 12, slow: 26, signal: 9 }, aliases: ['DIF', 'DEA', '金叉'], available: true, unavailable_reason: null },
      { signal_code: 'LONG_MA_ARRAY', signal_name: '均线多头排列', category: '趋势', signal_kind: 'state', direction: 'bullish', default_params: {}, aliases: ['均线'], available: true, unavailable_reason: null },
      { signal_code: 'PRICE_BREAKOUT_N', signal_name: 'N日价格突破', category: '突破', signal_kind: 'event', direction: 'bullish', default_params: { period: 20 }, aliases: ['突破'], available: true, unavailable_reason: null },
      { signal_code: 'RSI_OVERBOUGHT_REVERSAL', signal_name: 'RSI超买回落', category: '风险', signal_kind: 'event', direction: 'bearish', default_params: { period: 14, threshold: 70 }, aliases: ['RSI'], available: true, unavailable_reason: null },
      { signal_code: 'DDX_POSITIVE', signal_name: 'DDX连续为正', category: 'Level-2', signal_kind: 'state', direction: 'bullish', default_params: {}, aliases: ['DDX'], available: false, unavailable_reason: '当前没有接入可核验的逐笔/Level-2 DDX 历史数据' },
    ],
  },
  '/api/technical-screen/status': {
    ok: true,
    state: 'ready',
    schema_version: 'technical-screener-v1',
    config_schema_version: 2,
    algorithm_ver: 'local-signal-v1',
    stocks: 5026,
    bars: 1507800,
    signal_matches: 18432,
    data_date: '2026-07-22',
    adjustment: 'qfq',
    history_days: 420,
    benchmark: '000300.SH',
    benchmark_bars: 420,
    level2_available: false,
    level2_reason: '当前没有接入可核验的逐笔/Level-2 DDX 历史数据',
    refresh: { state: 'ready', processed: 5026, total: 5026, succeeded: 5026, failed: 0, finished_at: '2026-07-22 16:18:42' },
  },
  '/api/technical-screen/schemes': {
    ok: true,
    schemes: [],
  },
  '/api/technical-screen/query': {
    ok: true,
    state: 'ready',
    schema_version: 'technical-screener-v1',
    config_schema_version: 2,
    algorithm_ver: 'local-signal-v1',
    data_date: '2026-07-22',
    adjustment: 'qfq',
    matched: 2,
    returned: 2,
    offset: 0,
    limit: 50,
    coverage: { cached: 5026, eligible: 4388 },
    strongest_condition: null,
    missing: ['当前没有接入可核验的逐笔/Level-2 DDX 历史数据'],
    stocks: [
      { code: '600123', name: '技术审计股份', industry: '机械设备', last_price: 18.62, change_pct: 2.34, last_amount: 728000000, avg_amount20: 542000000, signal_score: 2, matched_reasons: [{ signal_code: 'MACD_GOLDEN_CROSS', signal_name: 'MACD金叉', signal_kind: 'event', timeframe: 'D', date: '2026-07-22', metrics: { dif: 0.42, dea: 0.38 }, is_confirmed: true }, { signal_code: 'LONG_MA_ARRAY', signal_name: '均线多头排列', signal_kind: 'state', timeframe: 'D', date: '2026-07-22', metrics: { ma20: 17.84, ma60: 16.91 }, is_confirmed: true }] },
      { code: '300456', name: '缓存核验科技', industry: null, last_price: null, change_pct: null, last_amount: 386000000, avg_amount20: null, signal_score: null, matched_reasons: [{ signal_code: 'MACD_GOLDEN_CROSS', signal_name: 'MACD金叉', signal_kind: 'event', timeframe: 'W', date: '2026-07-18', metrics: {}, is_confirmed: false }] },
    ],
  },
  '/api/technical-screen/refresh': {
    ok: true,
    state: 'queued',
    message: '全市场技术信号刷新已进入后台队列',
  },
  '/api/technical-screen/history': {
    ok: true,
    returned: 1,
    algorithm_ver: 'local-signal-v1',
    events: [{ code: '600123', signal_code: 'MACD_GOLDEN_CROSS', signal_name: 'MACD金叉', timeframe: 'D', event_date: '2026-07-22', metrics: { dif: 0.42, dea: 0.38 }, is_confirmed: true }],
  },
  '/api/forecast/mining': {
    ok: true,
    ts: '2026-07-22 10:31:38',
    period: '20260630',
    deadline: '2026-07-15',
    stats: { '总数': 186, '今日新增': 12, '按类型': { '预增': 96, '扭亏': 31, '略增': 59 }, '掘金池': 2, '陷阱池': 1 },
    missing: [],
    items: [
      { code: '600321', name: '审计预增制造', ann_date: '20260708', type: '预增', p_chg_min: 68, p_chg_max: 82, net_min: 438000000, net_max: 492000000, last_net: 271000000, summary: '主营产品需求增长，订单交付节奏改善。', reason: '主营产品需求增长，订单交付节奏改善。', source: '公开业绩预告', board: '沪主板', mandatory: true, layer: '主动交卷', trap: false, trap_reason: '', nonrec_flag: false, is_new_today: false },
      { code: '301678', name: '审计自愿科技', ann_date: '20260722', type: '扭亏', p_chg_min: null, p_chg_max: null, net_min: 52000000, net_max: null, last_net: null, summary: '预计实现扭亏，具体同比幅度和上年同期基数未披露。', reason: '产品结构改善并收到政府补助。', source: '公开业绩预告', board: '创业板', mandatory: false, layer: '超期/更正', trap: false, trap_reason: '', nonrec_flag: true, is_new_today: true },
    ],
    traps: [
      { code: '002789', name: '审计低基数股份', ann_date: '20260714', type: '预增', p_chg_min: 1280, p_chg_max: 1360, net_min: 41000000, net_max: 45000000, last_net: 3100000, summary: '上年同期基数较低。', reason: '上年同期基数较低。', source: '公开业绩预告', board: '深主板', mandatory: true, layer: '压哨', trap: true, trap_reason: '同比下限≥1000%，数千倍式高增大多来自极低基数', nonrec_flag: false, is_new_today: false },
    ],
  },
  '/api/disclosures/announcements': {
    ok: true,
    code: '000001',
    exchange: 'szse',
    state: 'complete',
    verification_state: 'cross_confirmed',
    used_last_good: false,
    data_as_of: '2026-07-22',
    requested_window: { start: '2026-06-22', end: '2026-07-22' },
    items: [{
      announcement_id: 'audit-company-report',
      announcement_date: '2026-07-18',
      published_at: '2026-07-18',
      title: '审计公司年度报告',
      original_url: 'https://disc.static.szse.cn/download/disc/audit-company-report.pdf',
      source_confirmations: ['cninfo', 'szse'],
    }],
    degraded_reasons: [],
  },
  '/api/interactions/management': {
    ok: true,
    code: '000001',
    state: 'complete',
    data_as_of: '2026-07-22',
    is_formal_disclosure: false,
    items: [{
      question: '年度经营目标的执行进度如何？',
      question_pub_date: '2026-07-20T09:18:00+08:00',
      answered: true,
      answer: '公司按已披露的年度经营计划推进，具体经营结果以定期报告为准。',
      answer_pub_date: '2026-07-21T15:06:00+08:00',
    }],
    degraded_reasons: [],
  },
}

const viewports = [
  { name: 'phone-375', width: 375, height: 812 },
  { name: 'phone-landscape', width: 812, height: 375 },
  { name: 'tablet-768', width: 768, height: 1024 },
  { name: 'desktop-1024', width: 1024, height: 900 },
  { name: 'wide-1440', width: 1440, height: 1000 },
]
const auditedPages = [
  { name: 'hotboard', hash: '#/today?tab=hotboard', titleSelector: '#hotboard-page-title' },
  { name: 'capital', hash: '#/today?tab=capital', titleSelector: '#capital-page-title' },
  { name: 'timing', hash: '#/today?tab=timing', titleSelector: '#timing-page-title' },
  { name: 'mainline', hash: '#/today?tab=mainline', titleSelector: '#mainline-research-title' },
  { name: 'sentiment', hash: '#/today?tab=news', titleSelector: '#sentiment-page-title' },
  { name: 'premarket', hash: '#/premarket?tab=report', titleSelector: '#premarket-page-title' },
  { name: 'tail', hash: '#/tail?tab=decision', titleSelector: '#tail-page-title' },
  { name: 'review', hash: '#/review?tab=close', titleSelector: '#close-review-page-title' },
  { name: 'tomorrow', hash: '#/review?tab=tomorrow', titleSelector: '#tomorrow-page-title' },
  { name: 'holdings', hash: '#/risk?tab=holdings', titleSelector: '#holdings-page-title' },
  { name: 'watchlist', hash: '#/risk?tab=watchlist', titleSelector: '#watchlist-page-title' },
  { name: 'stock-ask', hash: '#/research?tab=ask', titleSelector: '#stock-ask-page-title' },
  { name: 'diagnosis', hash: '#/research?tab=diagnosis&code=600519', titleSelector: '#diagnosis-page-title' },
  { name: 'screener', hash: '#/research?tab=screener', titleSelector: '#screener-page-title' },
  { name: 'technical-screener', hash: '#/research?tab=technical-screener', titleSelector: '#technical-screener-page-title' },
  { name: 'forecast-mining', hash: '#/research?tab=events', titleSelector: '#forecast-page-title' },
  { name: 'industry-chain', hash: '#/research?tab=chain', titleSelector: '#industry-chain-page-title' },
  { name: 'company-research', hash: '#/research?tab=company', titleSelector: '#company-page-title' },
  { name: 'reports', hash: '#/history?tab=reports', titleSelector: '#report-center-page-title' },
  { name: 'knowledge', hash: '#/history?tab=knowledge', titleSelector: '#knowledge-page-title' },
  { name: 'settings-tasks', hash: '#settings-tasks', titleSelector: '#settings-page-title' },
  { name: 'settings-delivery', hash: '#settings-delivery', titleSelector: '#settings-page-title' },
  { name: 'settings-models', hash: '#settings-models', titleSelector: '#settings-page-title' },
  { name: 'settings-data', hash: '#settings-data', titleSelector: '#settings-page-title' },
  { name: 'settings-governance', hash: '#settings-governance', titleSelector: '#settings-page-title' },
]

await mkdir(outputDir, { recursive: true })
const server = await createServer({ root, server: { host: '127.0.0.1', port: 4175, strictPort: true }, logLevel: 'error' })
await server.listen()
const browser = await chromium.launch({ executablePath: chromePath, headless: true })
const report = []

try {
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height }, deviceScaleFactor: 1 })
    const pageErrors = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      const body = fixtures[path]
      if (!body) return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'audit fixture missing' }) })
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    })
    await page.goto('http://127.0.0.1:4175/', { waitUntil: 'networkidle' })
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
    await page.waitForTimeout(1200)
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.addScriptTag({ content: axeSource })

    const layout = await page.evaluate(() => {
      const root = document.documentElement
      const targets = [...document.querySelectorAll('button, a')]
        .filter((node) => !node.classList.contains('skip-link'))
        .map((node) => {
          const rect = node.getBoundingClientRect()
          const style = getComputedStyle(node)
          return { label: node.getAttribute('aria-label') || node.textContent?.trim() || node.tagName, width: rect.width, height: rect.height, visible: style.visibility !== 'hidden' && style.display !== 'none' }
        })
        .filter((item) => item.visible && item.width > 0 && item.height > 0)
      return {
        viewport: window.innerWidth,
        scrollWidth: root.scrollWidth,
        scrollHeight: root.scrollHeight,
        overflow: Math.max(0, root.scrollWidth - window.innerWidth),
        mainContentPresent: Boolean(document.querySelector('#main-content h1')),
        mainContentHeight: document.querySelector('#main-content')?.getBoundingClientRect().height || 0,
        configuredBrandVisible: document.querySelector('.brand__copy strong')?.textContent?.trim() === '审计白标'
          && document.querySelector('.brand__copy > span')?.textContent?.trim() === 'A 股决策支持'
          && document.title.endsWith('· 审计白标'),
        simulationNavigationPresent: document.querySelector('#primary-navigation')?.textContent?.includes('模拟执行') === true,
        heatmapFallbackVisible: document.body.innerText.includes('面积改用 |涨跌幅|')
          && document.querySelectorAll('.sector-treemap rect').length === 9,
        heatmapLabelHierarchy: {
          largeNames: document.querySelectorAll('.heat-name--large').length,
          compactNames: document.querySelectorAll('.heat-name--compact').length,
          largeValues: document.querySelectorAll('.heat-value--large').length,
          promotedNarrowTiles: ['能源金属', '电网设备'].every((name) => (
            [...document.querySelectorAll('.heat-name--large')].some((node) => node.textContent?.trim() === name)
          )),
        },
        undersizedTargets: targets.filter((item) => item.width < 44 || item.height < 44),
      }
    })
    const axe = await page.evaluate(async () => window.axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    }))
    const violations = axe.violations.map((item) => ({
      id: item.id,
      impact: item.impact,
      help: item.help,
      nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })),
    }))
    let mobileNavigation = null
    if (viewport.width <= 720) {
      const hiddenBeforeOpen = await page.locator('#primary-navigation').evaluate((node) => getComputedStyle(node).visibility === 'hidden')
      await page.click('.menu-button')
      await page.waitForTimeout(250)
      const firstItemFocused = await page.evaluate(() => document.activeElement === document.querySelector('#primary-navigation .nav-parent'))
      await page.keyboard.press('Escape')
      await page.waitForTimeout(250)
      const menuFocusRestored = await page.evaluate(() => document.activeElement === document.querySelector('.menu-button'))
      const hiddenAfterClose = await page.locator('#primary-navigation').evaluate((node) => getComputedStyle(node).visibility === 'hidden')
      mobileNavigation = { hiddenBeforeOpen, firstItemFocused, menuFocusRestored, hiddenAfterClose }
    }
    const scrollResetOnNavigation = await page.evaluate(async () => {
      const spacer = document.createElement('div')
      spacer.style.height = '2000px'
      document.body.append(spacer)
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' })
      const before = window.scrollY
      window.location.hash = '#/today?tab=hotboard'
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      const after = window.scrollY
      window.location.hash = '#/today?tab=decision'
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      spacer.remove()
      return { startedBelowTop: before > 0, endedAtTop: after === 0 }
    })
    report.push({ viewport, layout, mobileNavigation, scrollResetOnNavigation, pageErrors, violations })

    if (viewport.width === 375 || viewport.width === 1440) {
      await page.screenshot({ path: resolve(outputDir, `${viewport.name}.png`), fullPage: true })
    }
    await page.close()
  }

  for (const auditedPage of auditedPages) {
    for (const viewport of viewports.filter(({ width }) => [375, 768, 1024, 1440].includes(width))) {
    const page = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height }, deviceScaleFactor: 1 })
    const pageErrors = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
    await page.route('**/api/**', async (route) => {
      const body = fixtures[new URL(route.request().url()).pathname]
      return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
    })
    await page.goto(`http://127.0.0.1:4175/${auditedPage.hash}`, { waitUntil: 'networkidle' })
    await page.addScriptTag({ content: axeSource })
    const layout = await page.evaluate((titleSelector) => {
      const root = document.documentElement
      const targets = [...document.querySelectorAll('button, a')]
        .filter((node) => !node.classList.contains('skip-link'))
        .map((node) => {
          const rect = node.getBoundingClientRect()
          const style = getComputedStyle(node)
          return { label: node.getAttribute('aria-label') || node.textContent?.trim() || node.tagName, width: rect.width, height: rect.height, visible: style.visibility !== 'hidden' && style.display !== 'none' }
        })
        .filter((item) => item.visible && item.width > 0 && item.height > 0)
      return {
        viewport: window.innerWidth,
        scrollWidth: root.scrollWidth,
        overflow: Math.max(0, root.scrollWidth - window.innerWidth),
        mainContentPresent: Boolean(document.querySelector(titleSelector)),
        mainContentHeight: document.querySelector('#main-content')?.getBoundingClientRect().height || 0,
        undersizedTargets: targets.filter((item) => item.width < 44 || item.height < 44),
      }
    }, auditedPage.titleSelector)
    const axe = await page.evaluate(async () => window.axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    }))
    report.push({
      page: auditedPage.name,
      viewport,
      layout,
      pageErrors,
      violations: axe.violations.map((item) => ({
        id: item.id,
        impact: item.impact,
        help: item.help,
        nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })),
      })),
    })
    if (viewport.width === 375 || viewport.width === 1440) {
      await page.screenshot({ path: resolve(outputDir, `${auditedPage.name}-${viewport.name}.png`), fullPage: true })
    }
    await page.close()
    }
  }

  const lightPage = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  await lightPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'light'))
  await lightPage.route('**/api/**', async (route) => {
    const body = fixtures[new URL(route.request().url()).pathname]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await lightPage.goto('http://127.0.0.1:4175/', { waitUntil: 'networkidle' })
  await lightPage.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
  await lightPage.waitForTimeout(1200)
  await lightPage.evaluate(() => window.scrollTo(0, 0))
  await lightPage.screenshot({ path: resolve(outputDir, 'wide-1440-light.png'), fullPage: true })
  await lightPage.addScriptTag({ content: axeSource })
  const lightAxe = await lightPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  report.push({ theme: 'light', violations: lightAxe.violations.map((item) => ({
    id: item.id,
    impact: item.impact,
    help: item.help,
    nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })),
  })) })
  for (const auditedPage of auditedPages) {
    await lightPage.goto(`http://127.0.0.1:4175/${auditedPage.hash}`, { waitUntil: 'networkidle' })
    await lightPage.screenshot({ path: resolve(outputDir, `${auditedPage.name}-wide-1440-light.png`), fullPage: true })
    await lightPage.addScriptTag({ content: axeSource })
    const pageLightAxe = await lightPage.evaluate(async () => window.axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    }))
    report.push({ page: auditedPage.name, theme: 'light', violations: pageLightAxe.violations.map((item) => ({
      id: item.id,
      impact: item.impact,
      help: item.help,
      nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })),
    })) })
  }
  await lightPage.close()

  const reviewLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const reviewLoadingErrors = []
  reviewLoadingPage.on('pageerror', (error) => reviewLoadingErrors.push(error.message))
  await reviewLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await reviewLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (['/api/reports/close', '/api/holdings/advice-review', '/api/recommendations/stats', '/api/recommendations'].includes(path)) {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await reviewLoadingPage.goto('http://127.0.0.1:4175/#/review?tab=close', { waitUntil: 'domcontentloaded' })
  await reviewLoadingPage.waitForSelector('.close-review-loading')
  await reviewLoadingPage.addScriptTag({ content: axeSource })
  const reviewLoadingAxe = await reviewLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await reviewLoadingPage.screenshot({ path: resolve(outputDir, 'review-loading-375.png'), fullPage: true })
  report.push({
    page: 'review', scenario: 'loading', loadingStateComplete: await reviewLoadingPage.locator('.close-review-loading').isVisible(),
    pageErrors: reviewLoadingErrors,
    violations: reviewLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await reviewLoadingPage.close()

  const reviewEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const reviewEmptyErrors = []
  reviewEmptyPage.on('pageerror', (error) => reviewEmptyErrors.push(error.message))
  await reviewEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await reviewEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    let body = fixtures[path]
    if (path === '/api/reports/close') body = { ok: false, error: '审计场景：该日期暂无今日复盘定稿' }
    if (path === '/api/holdings/advice-review') body = { ok: true, base_date: null, rows: [], stats: {}, missing: ['审计场景：暂无早于今日的持仓建议快照'] }
    if (path === '/api/recommendations') body = { ok: true, recommendations: [] }
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await reviewEmptyPage.goto('http://127.0.0.1:4175/#/review?tab=close', { waitUntil: 'networkidle' })
  await reviewEmptyPage.addScriptTag({ content: axeSource })
  const reviewEmptyState = await reviewEmptyPage.evaluate(() => {
    const text = document.body.innerText
    return {
      report: text.includes('今日复盘尚未定稿'),
      advice: text.includes('暂无可回检的昨日建议'),
      recommendations: text.includes('暂无推荐留痕'),
      missingRisk: text.includes('风险缺失'),
    }
  })
  const reviewEmptyAxe = await reviewEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await reviewEmptyPage.screenshot({ path: resolve(outputDir, 'review-empty-1440.png'), fullPage: true })
  report.push({
    page: 'review', scenario: 'empty', reviewEmptyState,
    reviewEmptyStateComplete: Object.values(reviewEmptyState).every(Boolean),
    pageErrors: reviewEmptyErrors,
    violations: reviewEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await reviewEmptyPage.close()

  const reviewPartialPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const reviewPartialErrors = []
  reviewPartialPage.on('pageerror', (error) => reviewPartialErrors.push(error.message))
  await reviewPartialPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await reviewPartialPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/holdings/advice-review') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：策略回检源暂不可用' }) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await reviewPartialPage.goto('http://127.0.0.1:4175/#/review?tab=close', { waitUntil: 'networkidle' })
  await reviewPartialPage.addScriptTag({ content: axeSource })
  const reviewPartialState = await reviewPartialPage.evaluate(() => {
    const text = document.body.innerText
    return {
      reportSurvives: text.includes('收盘复盘') && text.includes('盘前计划逐条对账'),
      localFailure: text.includes('策略自证暂不可用') && text.includes('策略回检源暂不可用'),
      statsSurvive: text.includes('原始观察') && text.includes('61.1%'),
    }
  })
  const reviewPartialAxe = await reviewPartialPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await reviewPartialPage.screenshot({ path: resolve(outputDir, 'review-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'review', scenario: 'partial-failure', reviewPartialState,
    reviewPartialStateComplete: Object.values(reviewPartialState).every(Boolean),
    pageErrors: reviewPartialErrors,
    violations: reviewPartialAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await reviewPartialPage.close()

  const tomorrowLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const tomorrowLoadingErrors = []
  tomorrowLoadingPage.on('pageerror', (error) => tomorrowLoadingErrors.push(error.message))
  await tomorrowLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await tomorrowLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (['/api/reports/evening', '/api/watchlist'].includes(path)) await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await tomorrowLoadingPage.goto('http://127.0.0.1:4175/#/review?tab=tomorrow', { waitUntil: 'domcontentloaded' })
  await tomorrowLoadingPage.waitForSelector('.tomorrow-loading')
  await tomorrowLoadingPage.addScriptTag({ content: axeSource })
  const tomorrowLoadingAxe = await tomorrowLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await tomorrowLoadingPage.screenshot({ path: resolve(outputDir, 'tomorrow-loading-375.png'), fullPage: true })
  report.push({
    page: 'tomorrow', scenario: 'loading',
    tomorrowLoadingStateComplete: await tomorrowLoadingPage.locator('.tomorrow-loading').isVisible(),
    pageErrors: tomorrowLoadingErrors,
    violations: tomorrowLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await tomorrowLoadingPage.close()

  const tomorrowEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const tomorrowEmptyErrors = []
  tomorrowEmptyPage.on('pageerror', (error) => tomorrowEmptyErrors.push(error.message))
  await tomorrowEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await tomorrowEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    let body = fixtures[path]
    if (path === '/api/reports/evening') body = { ok: false, error: '审计场景：该日期暂无次日策略定稿' }
    if (path === '/api/watchlist') body = { ok: true, items: [], missing: [] }
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await tomorrowEmptyPage.goto('http://127.0.0.1:4175/#/review?tab=tomorrow', { waitUntil: 'networkidle' })
  await tomorrowEmptyPage.addScriptTag({ content: axeSource })
  const tomorrowEmptyState = await tomorrowEmptyPage.evaluate(() => {
    const text = document.body.innerText
    return {
      report: text.includes('次日策略尚未定稿'),
      watchlist: text.includes('观察池返回真实空数组'),
      missingRisk: text.includes('收盘风险结论暂不可用'),
      noZeroFill: !text.includes('0 级'),
    }
  })
  const tomorrowEmptyAxe = await tomorrowEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await tomorrowEmptyPage.screenshot({ path: resolve(outputDir, 'tomorrow-empty-1440.png'), fullPage: true })
  report.push({
    page: 'tomorrow', scenario: 'empty', tomorrowEmptyState,
    tomorrowEmptyStateComplete: Object.values(tomorrowEmptyState).every(Boolean),
    pageErrors: tomorrowEmptyErrors,
    violations: tomorrowEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await tomorrowEmptyPage.close()

  const tomorrowPartialPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const tomorrowPartialErrors = []
  tomorrowPartialPage.on('pageerror', (error) => tomorrowPartialErrors.push(error.message))
  await tomorrowPartialPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await tomorrowPartialPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/watchlist') return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：观察池行情源暂不可用' }) })
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await tomorrowPartialPage.goto('http://127.0.0.1:4175/#/review?tab=tomorrow', { waitUntil: 'networkidle' })
  await tomorrowPartialPage.addScriptTag({ content: axeSource })
  const tomorrowPartialState = await tomorrowPartialPage.evaluate(() => {
    const text = document.body.innerText
    return {
      reportSurvives: text.includes('次日策略（供 2026-07-23 使用）') && text.includes('明日唯一动作'),
      localFailure: text.includes('明日观察池暂不可用') && text.includes('观察池行情源暂不可用'),
      governanceSurvives: text.includes('尾盘 + 收盘齐备') && text.includes('已提交推送，未确认送达'),
    }
  })
  const tomorrowPartialAxe = await tomorrowPartialPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await tomorrowPartialPage.screenshot({ path: resolve(outputDir, 'tomorrow-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'tomorrow', scenario: 'partial-failure', tomorrowPartialState,
    tomorrowPartialStateComplete: Object.values(tomorrowPartialState).every(Boolean),
    pageErrors: tomorrowPartialErrors,
    violations: tomorrowPartialAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await tomorrowPartialPage.close()

  const hotboardLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const hotboardLoadingErrors = []
  hotboardLoadingPage.on('pageerror', (error) => hotboardLoadingErrors.push(error.message))
  await hotboardLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await hotboardLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/market/limitup/themes') await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await hotboardLoadingPage.goto('http://127.0.0.1:4175/#/today?tab=hotboard', { waitUntil: 'domcontentloaded' })
  await hotboardLoadingPage.waitForSelector('.hotboard-loading')
  await hotboardLoadingPage.addScriptTag({ content: axeSource })
  const hotboardLoadingAxe = await hotboardLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await hotboardLoadingPage.screenshot({ path: resolve(outputDir, 'hotboard-loading-375.png'), fullPage: true })
  report.push({
    page: 'hotboard', scenario: 'loading',
    hotboardLoadingStateComplete: await hotboardLoadingPage.locator('.hotboard-loading').isVisible(),
    pageErrors: hotboardLoadingErrors,
    violations: hotboardLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await hotboardLoadingPage.close()

  const hotboardEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const hotboardEmptyErrors = []
  hotboardEmptyPage.on('pageerror', (error) => hotboardEmptyErrors.push(error.message))
  await hotboardEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await hotboardEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/market/limitup/themes'
      ? { ok: true, date: '20260722', data_date: '2026-07-22', ts: '2026-07-22 09:10:00', missing: ['审计场景：尚未开盘，当日公开涨停池为空'], stocks: [] }
      : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await hotboardEmptyPage.goto('http://127.0.0.1:4175/#/today?tab=hotboard', { waitUntil: 'networkidle' })
  await hotboardEmptyPage.addScriptTag({ content: axeSource })
  const hotboardEmptyState = await hotboardEmptyPage.evaluate(() => {
    const text = document.body.innerText
    return {
      empty: text.includes('当前无涨停题材数据'),
      noZeroFill: !text.includes('0 只涨停股分布于'),
      reason: text.includes('尚未开盘，当日公开涨停池为空'),
    }
  })
  const hotboardEmptyAxe = await hotboardEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await hotboardEmptyPage.screenshot({ path: resolve(outputDir, 'hotboard-empty-1440.png'), fullPage: true })
  report.push({
    page: 'hotboard', scenario: 'empty', hotboardEmptyState,
    hotboardEmptyStateComplete: Object.values(hotboardEmptyState).every(Boolean),
    pageErrors: hotboardEmptyErrors,
    violations: hotboardEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await hotboardEmptyPage.close()

  const hotboardFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const hotboardFailureErrors = []
  hotboardFailurePage.on('pageerror', (error) => hotboardFailureErrors.push(error.message))
  await hotboardFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await hotboardFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/market/limitup/themes') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：同花顺公开涨停池当前不可达' }) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await hotboardFailurePage.goto('http://127.0.0.1:4175/#/today?tab=hotboard', { waitUntil: 'networkidle' })
  await hotboardFailurePage.addScriptTag({ content: axeSource })
  const hotboardFailureState = await hotboardFailurePage.evaluate(() => {
    const text = document.body.innerText
    return {
      localFailure: text.includes('涨停题材暂不可用') && text.includes('同花顺公开涨停池当前不可达'),
      shellSurvives: text.includes('市场结构') && text.includes('北京时间'),
      retry: [...document.querySelectorAll('button')].some((button) => button.textContent?.includes('重试')),
    }
  })
  const hotboardFailureAxe = await hotboardFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await hotboardFailurePage.screenshot({ path: resolve(outputDir, 'hotboard-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'hotboard', scenario: 'partial-failure', hotboardFailureState,
    hotboardFailureStateComplete: Object.values(hotboardFailureState).every(Boolean),
    pageErrors: hotboardFailureErrors,
    violations: hotboardFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await hotboardFailurePage.close()

  const timingLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const timingLoadingErrors = []
  timingLoadingPage.on('pageerror', (error) => timingLoadingErrors.push(error.message))
  await timingLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await timingLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/timing') await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await timingLoadingPage.goto('http://127.0.0.1:4175/#/today?tab=timing', { waitUntil: 'domcontentloaded' })
  await timingLoadingPage.waitForSelector('.timing-loading')
  await timingLoadingPage.addScriptTag({ content: axeSource })
  const timingLoadingAxe = await timingLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await timingLoadingPage.screenshot({ path: resolve(outputDir, 'timing-loading-375.png'), fullPage: true })
  report.push({
    page: 'timing', scenario: 'loading',
    timingLoadingStateComplete: await timingLoadingPage.locator('.timing-loading').isVisible(),
    pageErrors: timingLoadingErrors,
    violations: timingLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await timingLoadingPage.close()

  const timingEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const timingEmptyErrors = []
  timingEmptyPage.on('pageerror', (error) => timingEmptyErrors.push(error.message))
  await timingEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await timingEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/timing'
      ? {
          ok: true,
          ts: '2026-07-22 08:40:00',
          data_date: null,
          stale: false,
          missing: ['审计场景：盘前日线与市场活跃度尚未形成可计算样本'],
          fear_greed: {
            ok: false,
            score: null,
            label: '数据不足',
            factors: { '涨跌家数比': null, '涨停温度': null, '成交量比': null, '资金': null, '波动反向': null },
            meta: {},
            data_date: '',
            stale: false,
            missing: ['审计场景：五项因子本轮均缺失'],
          },
          indexes: [],
          stock: null,
        }
      : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await timingEmptyPage.goto('http://127.0.0.1:4175/#/today?tab=timing', { waitUntil: 'networkidle' })
  await timingEmptyPage.addScriptTag({ content: axeSource })
  const timingEmptyState = await timingEmptyPage.evaluate(() => {
    const text = document.body.innerText
    return {
      fearMissing: text.includes('贪恐指标暂不可计算'),
      indexesEmpty: text.includes('指数择时返回真实空数组'),
      reason: text.includes('五项因子本轮均缺失'),
      noZeroFill: !text.includes('0.0 / 100'),
    }
  })
  const timingEmptyAxe = await timingEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await timingEmptyPage.screenshot({ path: resolve(outputDir, 'timing-empty-1440.png'), fullPage: true })
  report.push({
    page: 'timing', scenario: 'empty', timingEmptyState,
    timingEmptyStateComplete: Object.values(timingEmptyState).every(Boolean),
    pageErrors: timingEmptyErrors,
    violations: timingEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await timingEmptyPage.close()

  const timingFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const timingFailureErrors = []
  timingFailurePage.on('pageerror', (error) => timingFailureErrors.push(error.message))
  await timingFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await timingFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/timing') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：资金择时数据源当前不可达' }) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await timingFailurePage.goto('http://127.0.0.1:4175/#/today?tab=timing', { waitUntil: 'networkidle' })
  await timingFailurePage.addScriptTag({ content: axeSource })
  const timingFailureState = await timingFailurePage.evaluate(() => {
    const text = document.body.innerText
    return {
      localFailure: text.includes('择时证据暂不可用') && text.includes('资金择时数据源当前不可达'),
      decisionSurvives: text.includes('只做主线内有承接的确认机会') && text.includes('总仓不超过 50%'),
      stockSurvives: text.includes('个股择时核对'),
      retry: [...document.querySelectorAll('button')].some((button) => button.textContent?.includes('重试')),
    }
  })
  const timingFailureAxe = await timingFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await timingFailurePage.screenshot({ path: resolve(outputDir, 'timing-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'timing', scenario: 'partial-failure', timingFailureState,
    timingFailureStateComplete: Object.values(timingFailureState).every(Boolean),
    pageErrors: timingFailureErrors,
    violations: timingFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await timingFailurePage.close()

  const mainlineLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const mainlineLoadingErrors = []
  mainlineLoadingPage.on('pageerror', (error) => mainlineLoadingErrors.push(error.message))
  await mainlineLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await mainlineLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path.startsWith('/api/features/')) await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await mainlineLoadingPage.goto('http://127.0.0.1:4175/#/today?tab=mainline', { waitUntil: 'domcontentloaded' })
  await mainlineLoadingPage.waitForSelector('.mainline-workbench [role="status"]')
  await mainlineLoadingPage.addScriptTag({ content: axeSource })
  const mainlineLoadingCount = await mainlineLoadingPage.locator('.mainline-workbench [role="status"]').count()
  const mainlineLoadingAxe = await mainlineLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await mainlineLoadingPage.screenshot({ path: resolve(outputDir, 'mainline-loading-375.png'), fullPage: true })
  report.push({
    page: 'mainline', scenario: 'loading', mainlineLoadingStateComplete: mainlineLoadingCount >= 4,
    pageErrors: mainlineLoadingErrors,
    violations: mainlineLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await mainlineLoadingPage.close()

  const mainlineEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const mainlineEmptyErrors = []
  mainlineEmptyPage.on('pageerror', (error) => mainlineEmptyErrors.push(error.message))
  await mainlineEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await mainlineEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    let body = fixtures[path]
    if (path === '/api/features/money-effect') body = { ok: true, ts: '2026-07-22 10:31:26', data_date: '2026-07-22', data_state: 'complete', missing: [], themes: [], losers: [] }
    if (path === '/api/features/crowdedness') body = { ok: true, ts: '2026-07-22 10:31:28', data_date: '2026-07-22', data_state: 'complete', missing: [], sectors: [] }
    if (path === '/api/features/mainline') body = { ok: true, ts: '2026-07-22 10:31:30', data_date: '2026-07-22', data_state: 'accumulating', state_label: '已积累0/2日', note: '市场主线需至少 2 个交易日数据累积', missing: [], mainlines: [], today_top: [] }
    if (path === '/api/features/leaders') body = { ok: true, ts: '2026-07-22 10:31:32', data_date: '2026-07-22', missing: [], kongban: null, ladder: [], popularity: [] }
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await mainlineEmptyPage.goto('http://127.0.0.1:4175/#/today?tab=mainline', { waitUntil: 'networkidle' })
  await mainlineEmptyPage.addScriptTag({ content: axeSource })
  const mainlineEmptyState = await mainlineEmptyPage.evaluate(() => {
    const text = document.body.innerText
    return {
      money: text.includes('本次未计算出赚钱效应记录'),
      crowd: text.includes('本次未计算出拥挤度记录'),
      continuity: text.includes('当前没有形成可展示的跨日主线'),
      leaders: text.includes('当前批次未形成龙头梯队'),
      noSampleClaim: !text.includes('近 0 个交易日强度首位'),
    }
  })
  const mainlineEmptyAxe = await mainlineEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await mainlineEmptyPage.screenshot({ path: resolve(outputDir, 'mainline-empty-1440.png'), fullPage: true })
  report.push({
    page: 'mainline', scenario: 'empty', mainlineEmptyState,
    mainlineEmptyStateComplete: Object.values(mainlineEmptyState).every(Boolean),
    pageErrors: mainlineEmptyErrors,
    violations: mainlineEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await mainlineEmptyPage.close()

  const mainlineFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const mainlineFailureErrors = []
  mainlineFailurePage.on('pageerror', (error) => mainlineFailureErrors.push(error.message))
  await mainlineFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await mainlineFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/features/crowdedness') return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：拥挤度数据源当前不可达' }) })
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await mainlineFailurePage.goto('http://127.0.0.1:4175/#/today?tab=mainline', { waitUntil: 'networkidle' })
  await mainlineFailurePage.addScriptTag({ content: axeSource })
  const mainlineFailureState = await mainlineFailurePage.evaluate(() => {
    const text = document.body.innerText
    return {
      localError: text.includes('交易拥挤度暂不可用') && text.includes('审计场景：拥挤度数据源当前不可达'),
      moneyRetained: text.includes('题材赚钱效应') && text.includes('先进制造'),
      mainlineRetained: text.includes('跨日主线持续性') && text.includes('算力基础设施'),
      leadersRetained: text.includes('情绪龙头梯队') && text.includes('立新能源'),
    }
  })
  const mainlineFailureAxe = await mainlineFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await mainlineFailurePage.screenshot({ path: resolve(outputDir, 'mainline-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'mainline', scenario: 'partial-failure', mainlineFailureState,
    mainlineFailureStateComplete: Object.values(mainlineFailureState).every(Boolean),
    pageErrors: mainlineFailureErrors,
    violations: mainlineFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await mainlineFailurePage.close()

  const capitalLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const capitalLoadingErrors = []
  capitalLoadingPage.on('pageerror', (error) => capitalLoadingErrors.push(error.message))
  await capitalLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await capitalLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/capital/trends') await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await capitalLoadingPage.goto('http://127.0.0.1:4175/#/today?tab=capital', { waitUntil: 'domcontentloaded' })
  await capitalLoadingPage.waitForSelector('.capital-loading')
  await capitalLoadingPage.addScriptTag({ content: axeSource })
  const capitalLoadingAxe = await capitalLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await capitalLoadingPage.screenshot({ path: resolve(outputDir, 'capital-loading-375.png'), fullPage: true })
  report.push({
    page: 'capital', scenario: 'loading',
    capitalLoadingStateComplete: await capitalLoadingPage.locator('.capital-loading').isVisible(),
    pageErrors: capitalLoadingErrors,
    violations: capitalLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await capitalLoadingPage.close()

  const capitalEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const capitalEmptyErrors = []
  capitalEmptyPage.on('pageerror', (error) => capitalEmptyErrors.push(error.message))
  await capitalEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await capitalEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/capital/trends'
      ? { ok: true, ts: '2026-07-22 10:31:36', snap_ts: '', today: '2026-07-22', sector_date: '', hot_date: '', view_state: 'empty', stale_sector: false, stale_hot: false, sectors: [], hot_stocks: [] }
      : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await capitalEmptyPage.goto('http://127.0.0.1:4175/#/today?tab=capital', { waitUntil: 'networkidle' })
  await capitalEmptyPage.addScriptTag({ content: axeSource })
  const capitalEmptyState = await capitalEmptyPage.evaluate(() => {
    const text = document.body.innerText
    return {
      empty: text.includes('当前无资金快照'),
      noFixture: !text.includes('紫金矿业') && !text.includes('工业金属'),
      missingNotZero: !text.includes('0.00 亿'),
    }
  })
  const capitalEmptyAxe = await capitalEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await capitalEmptyPage.screenshot({ path: resolve(outputDir, 'capital-empty-1440.png'), fullPage: true })
  report.push({
    page: 'capital', scenario: 'empty', capitalEmptyState,
    capitalEmptyStateComplete: Object.values(capitalEmptyState).every(Boolean),
    pageErrors: capitalEmptyErrors,
    violations: capitalEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await capitalEmptyPage.close()

  const capitalFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const capitalFailureErrors = []
  capitalFailurePage.on('pageerror', (error) => capitalFailureErrors.push(error.message))
  await capitalFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await capitalFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/capital/snapshot' && route.request().method() === 'POST') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：手动快照采集源暂不可用' }) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await capitalFailurePage.goto('http://127.0.0.1:4175/#/today?tab=capital', { waitUntil: 'networkidle' })
  await capitalFailurePage.locator('.capital-snapshot-button').click()
  await capitalFailurePage.waitForSelector('.capital-action-feedback.is-danger')
  await capitalFailurePage.addScriptTag({ content: axeSource })
  const capitalFailureState = await capitalFailurePage.evaluate(() => {
    const text = document.body.innerText
    return {
      actionFailure: text.includes('采集失败') && text.includes('手动快照采集源暂不可用'),
      sectorRetained: text.includes('工业金属'),
      stockRetained: text.includes('紫金矿业'),
      unitWarningRetained: text.includes('原始值 · 未核验'),
    }
  })
  const capitalFailureAxe = await capitalFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await capitalFailurePage.screenshot({ path: resolve(outputDir, 'capital-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'capital', scenario: 'partial-failure', capitalFailureState,
    capitalFailureStateComplete: Object.values(capitalFailureState).every(Boolean),
    pageErrors: capitalFailureErrors,
    violations: capitalFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await capitalFailurePage.close()

  const sentimentLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const sentimentLoadingErrors = []
  sentimentLoadingPage.on('pageerror', (error) => sentimentLoadingErrors.push(error.message))
  await sentimentLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await sentimentLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/news/a-share') await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await sentimentLoadingPage.goto('http://127.0.0.1:4175/#/today?tab=news', { waitUntil: 'domcontentloaded' })
  await sentimentLoadingPage.waitForSelector('.sentiment-loading')
  await sentimentLoadingPage.addScriptTag({ content: axeSource })
  const sentimentLoadingAxe = await sentimentLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await sentimentLoadingPage.screenshot({ path: resolve(outputDir, 'sentiment-loading-375.png'), fullPage: true })
  report.push({
    page: 'sentiment', scenario: 'loading',
    sentimentLoadingStateComplete: await sentimentLoadingPage.locator('.sentiment-loading').isVisible(),
    pageErrors: sentimentLoadingErrors,
    violations: sentimentLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await sentimentLoadingPage.close()

  const sentimentEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const sentimentEmptyErrors = []
  sentimentEmptyPage.on('pageerror', (error) => sentimentEmptyErrors.push(error.message))
  await sentimentEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await sentimentEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/news/a-share'
      ? { ok: true, ts: '2026-07-22 10:40:00', cn_fetched_at: '2026-07-22 10:40:00', fetched_at: '2026-07-22T10:40:00+08:00', latest_event_time: null, refresh_seconds: 30, sources_tried: 3, sources_live: 3, missing: [], items: [], mapping: { state: 'complete', mapped: 0, candidates: 0, total: 0, missing: [] }, observation: { enabled: true, state: 'empty', tracked: 0, missing: [] }, pipeline: { source_state: 'complete', entity_mapping_state: 'complete', market_observation_state: 'empty', validation_state: 'empty' } }
      : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await sentimentEmptyPage.goto('http://127.0.0.1:4175/#/today?tab=news', { waitUntil: 'networkidle' })
  await sentimentEmptyPage.addScriptTag({ content: axeSource })
  const sentimentEmptyState = await sentimentEmptyPage.evaluate(() => {
    const text = document.body.innerText
    return {
      emptyLabel: text.includes('真实空结果'),
      noEvent: text.includes('当前暂无可展示的舆情事件'),
      missingTimeNotZero: text.includes('最新事件\n—') && !text.includes('最新事件\n00:00'),
    }
  })
  const sentimentEmptyAxe = await sentimentEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await sentimentEmptyPage.screenshot({ path: resolve(outputDir, 'sentiment-empty-1440.png'), fullPage: true })
  report.push({
    page: 'sentiment', scenario: 'empty', sentimentEmptyState,
    sentimentEmptyStateComplete: Object.values(sentimentEmptyState).every(Boolean),
    pageErrors: sentimentEmptyErrors,
    violations: sentimentEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await sentimentEmptyPage.close()

  const sentimentFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const sentimentFailureErrors = []
  let sentimentCalls = 0
  sentimentFailurePage.on('pageerror', (error) => sentimentFailureErrors.push(error.message))
  await sentimentFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await sentimentFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/news/a-share') {
      sentimentCalls += 1
      if (sentimentCalls > 1) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：新闻采集链暂不可用' }) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await sentimentFailurePage.goto('http://127.0.0.1:4175/#/today?tab=news', { waitUntil: 'networkidle' })
  await sentimentFailurePage.locator('.sentiment-event-toggle').first().click()
  await sentimentFailurePage.locator('.refresh-button').click()
  await sentimentFailurePage.waitForSelector('.sentiment-alert')
  await sentimentFailurePage.addScriptTag({ content: axeSource })
  const sentimentFailureState = await sentimentFailurePage.evaluate(() => {
    const text = document.body.innerText
    return {
      localFailure: text.includes('最新刷新失败，保留已加载事件'),
      retainedEvent: text.includes('世茂能源发布股权转让公告'),
      truthfulBoundary: text.includes('不判断利多利空'),
      evidenceDetails: text.includes('采集证据') && text.includes('独立信源待确认'),
    }
  })
  const sentimentFailureAxe = await sentimentFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await sentimentFailurePage.screenshot({ path: resolve(outputDir, 'sentiment-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'sentiment', scenario: 'partial-failure', sentimentFailureState,
    sentimentFailureStateComplete: Object.values(sentimentFailureState).every(Boolean),
    pageErrors: sentimentFailureErrors,
    violations: sentimentFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await sentimentFailurePage.close()

  const holdingsLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const holdingsLoadingErrors = []
  holdingsLoadingPage.on('pageerror', (error) => holdingsLoadingErrors.push(error.message))
  await holdingsLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await holdingsLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/holdings') await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await holdingsLoadingPage.goto('http://127.0.0.1:4175/#/risk?tab=holdings', { waitUntil: 'domcontentloaded' })
  await holdingsLoadingPage.waitForSelector('.holdings-loading')
  await holdingsLoadingPage.addScriptTag({ content: axeSource })
  const holdingsLoadingAxe = await holdingsLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await holdingsLoadingPage.screenshot({ path: resolve(outputDir, 'holdings-loading-375.png'), fullPage: true })
  report.push({
    page: 'holdings', scenario: 'loading',
    holdingsLoadingStateComplete: await holdingsLoadingPage.locator('.holdings-loading').isVisible(),
    pageErrors: holdingsLoadingErrors,
    violations: holdingsLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await holdingsLoadingPage.close()

  const holdingsEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const holdingsEmptyErrors = []
  holdingsEmptyPage.on('pageerror', (error) => holdingsEmptyErrors.push(error.message))
  await holdingsEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await holdingsEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/holdings'
      ? { ok: true, holdings: [] }
      : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await holdingsEmptyPage.goto('http://127.0.0.1:4175/#/risk?tab=holdings', { waitUntil: 'networkidle' })
  await holdingsEmptyPage.addScriptTag({ content: axeSource })
  const holdingsEmptyState = await holdingsEmptyPage.evaluate(() => {
    const text = document.body.innerText
    return {
      truthfulEmpty: text.includes('真实空数组') && text.includes('当前未记录持仓'),
      noPlaceholderRows: text.includes('不会插入示例股票、零成本或零股数'),
      automaticTotals: text.includes('持仓总市值\n—') && text.includes('投入成本\n—'),
    }
  })
  const holdingsEmptyAxe = await holdingsEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await holdingsEmptyPage.screenshot({ path: resolve(outputDir, 'holdings-empty-1440.png'), fullPage: true })
  report.push({
    page: 'holdings', scenario: 'empty', holdingsEmptyState,
    holdingsEmptyStateComplete: Object.values(holdingsEmptyState).every(Boolean),
    pageErrors: holdingsEmptyErrors,
    violations: holdingsEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await holdingsEmptyPage.close()

  const holdingsFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const holdingsFailureErrors = []
  holdingsFailurePage.on('pageerror', (error) => holdingsFailureErrors.push(error.message))
  await holdingsFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await holdingsFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/quotes') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：批量报价源暂不可用' }) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await holdingsFailurePage.goto('http://127.0.0.1:4175/#/risk?tab=holdings', { waitUntil: 'networkidle' })
  await holdingsFailurePage.addScriptTag({ content: axeSource })
  const holdingsFailureState = await holdingsFailurePage.evaluate(() => {
    const text = document.body.innerText
    return {
      localFailure: text.includes('报价局部失败，持仓账本仍可编辑') && text.includes('批量报价源暂不可用'),
      holdingsSurvive: text.includes('紫金矿业') && text.includes('宁德时代') && text.includes('比亚迪'),
      aggregateMissing: text.includes('持仓总市值\n—') && text.includes('浮动盈亏\n—'),
      retryAvailable: text.includes('重试报价'),
    }
  })
  const holdingsFailureAxe = await holdingsFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await holdingsFailurePage.screenshot({ path: resolve(outputDir, 'holdings-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'holdings', scenario: 'partial-failure', holdingsFailureState,
    holdingsFailureStateComplete: Object.values(holdingsFailureState).every(Boolean),
    pageErrors: holdingsFailureErrors,
    violations: holdingsFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await holdingsFailurePage.close()

  const watchlistLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const watchlistLoadingErrors = []
  watchlistLoadingPage.on('pageerror', (error) => watchlistLoadingErrors.push(error.message))
  await watchlistLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await watchlistLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/watchlist') await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await watchlistLoadingPage.goto('http://127.0.0.1:4175/#/risk?tab=watchlist', { waitUntil: 'domcontentloaded' })
  await watchlistLoadingPage.waitForSelector('.watchlist-loading')
  await watchlistLoadingPage.addScriptTag({ content: axeSource })
  const watchlistLoadingAxe = await watchlistLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await watchlistLoadingPage.screenshot({ path: resolve(outputDir, 'watchlist-loading-375.png'), fullPage: true })
  report.push({
    page: 'watchlist', scenario: 'loading',
    watchlistLoadingStateComplete: await watchlistLoadingPage.locator('.watchlist-loading').isVisible(),
    pageErrors: watchlistLoadingErrors,
    violations: watchlistLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await watchlistLoadingPage.close()

  const watchlistEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const watchlistEmptyErrors = []
  watchlistEmptyPage.on('pageerror', (error) => watchlistEmptyErrors.push(error.message))
  await watchlistEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await watchlistEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/watchlist'
      ? { ok: true, ts: '2026-07-22 10:31:22', items: [], missing: [] }
      : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await watchlistEmptyPage.goto('http://127.0.0.1:4175/#/risk?tab=watchlist', { waitUntil: 'networkidle' })
  await watchlistEmptyPage.addScriptTag({ content: axeSource })
  const watchlistEmptyState = await watchlistEmptyPage.evaluate(() => {
    const text = document.body.innerText
    return {
      truthfulEmpty: text.includes('真实空数组') && text.includes('当前观察池为空'),
      primaryAction: text.includes('添加第一只观察标的'),
      noFixtureRows: !text.includes('紫金矿业') && !text.includes('宁德时代'),
      noZeroFill: !text.includes('0.00 元'),
    }
  })
  const watchlistEmptyAxe = await watchlistEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await watchlistEmptyPage.screenshot({ path: resolve(outputDir, 'watchlist-empty-1440.png'), fullPage: true })
  report.push({
    page: 'watchlist', scenario: 'empty', watchlistEmptyState,
    watchlistEmptyStateComplete: Object.values(watchlistEmptyState).every(Boolean),
    pageErrors: watchlistEmptyErrors,
    violations: watchlistEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await watchlistEmptyPage.getByRole('button', { name: '批量导入' }).click()
  await watchlistEmptyPage.waitForSelector('dialog.watchlist-import[open]')
  await watchlistEmptyPage.locator('dialog.watchlist-import input[type="file"]').setInputFiles({
    name: 'Table_5680.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from([
      '初始    代码      名称        所属行业',
      '1       600519    贵州茅台    白酒',
      '2       09868     小鹏集团-W  汽车',
      '3       300750    宁德时代    电池',
    ].join('\n'), 'utf8'),
  })
  await watchlistEmptyPage.waitForFunction(() => document.querySelectorAll('.watchlist-import__rows li').length === 3)
  const watchlistImportState = await watchlistEmptyPage.evaluate(() => {
    const dialog = document.querySelector('dialog.watchlist-import')
    const text = dialog?.textContent || ''
    return {
      nativeModal: dialog instanceof HTMLDialogElement && dialog.open,
      sourceDetected: text.includes('东方财富'),
      validRows: text.includes('600519') && text.includes('300750') && text.includes('可导入') && text.includes('2'),
      skippedRows: text.includes('09868') && text.includes('不是可识别的 6 位 A 股代码'),
      appendOnlyCopy: text.includes('不会清空或覆盖'),
      noHorizontalOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    }
  })
  const watchlistImportAxe = await watchlistEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await watchlistEmptyPage.screenshot({ path: resolve(outputDir, 'watchlist-import-preview-1440.png'), fullPage: true })
  report.push({
    page: 'watchlist', scenario: 'import-preview', watchlistImportState,
    watchlistImportStateComplete: Object.values(watchlistImportState).every(Boolean),
    pageErrors: watchlistEmptyErrors,
    violations: watchlistImportAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await watchlistEmptyPage.getByRole('button', { name: '取消' }).click()
  await watchlistEmptyPage.getByRole('button', { name: '添加第一只观察标的' }).click()
  await watchlistEmptyPage.waitForSelector('dialog.watchlist-editor[open]')
  const watchlistModalState = await watchlistEmptyPage.evaluate(() => {
    const dialog = document.querySelector('dialog.watchlist-editor')
    const text = dialog?.textContent || ''
    return {
      nativeModal: dialog instanceof HTMLDialogElement && dialog.open,
      serverRiskOnly: text.includes('风险边界等级由服务端计算') && !text.includes('请选择风险等级'),
      codeFocused: document.activeElement?.getAttribute('inputmode') === 'numeric',
      saveVisible: (() => {
        const rect = dialog?.querySelector('button[type="submit"]')?.getBoundingClientRect()
        return Boolean(rect && rect.top >= 0 && rect.bottom <= window.innerHeight)
      })(),
      backgroundStable: document.querySelectorAll('.watchlist-row-item').length === 0,
    }
  })
  const watchlistModalAxe = await watchlistEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await watchlistEmptyPage.screenshot({ path: resolve(outputDir, 'watchlist-add-modal-1440.png'), fullPage: true })
  report.push({
    page: 'watchlist', scenario: 'add-modal', watchlistModalState,
    watchlistModalStateComplete: Object.values(watchlistModalState).every(Boolean),
    pageErrors: watchlistEmptyErrors,
    violations: watchlistModalAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await watchlistEmptyPage.close()

  const watchlistFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const watchlistFailureErrors = []
  let watchlistCalls = 0
  watchlistFailurePage.on('pageerror', (error) => watchlistFailureErrors.push(error.message))
  await watchlistFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await watchlistFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/watchlist') {
      watchlistCalls += 1
      if (watchlistCalls > 1) {
        return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：观察池刷新链暂不可用' }) })
      }
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await watchlistFailurePage.goto('http://127.0.0.1:4175/#/risk?tab=watchlist', { waitUntil: 'networkidle' })
  await watchlistFailurePage.locator('.refresh-button').click()
  await watchlistFailurePage.waitForSelector('.watchlist-alert')
  await watchlistFailurePage.addScriptTag({ content: axeSource })
  const watchlistFailureState = await watchlistFailurePage.evaluate(() => {
    const text = document.body.innerText
    const compactRows = [...document.querySelectorAll('.watchlist-row')]
      .slice(0, 4)
      .map((row) => row.getBoundingClientRect())
    const header = document.querySelector('.watchlist-list__head')
    const headerColor = header ? getComputedStyle(header).color : ''
    return {
      localFailure: text.includes('自动刷新失败，保留最近已加载内容') && text.includes('观察池刷新链暂不可用'),
      itemsSurvive: text.includes('紫金矿业') && text.includes('宁德时代') && text.includes('中际旭创') && text.includes('浦发银行'),
      degradedSurvives: text.includes('降级快照') && text.includes('陈旧'),
      retryAvailable: text.includes('重试刷新'),
      compactRowsAligned: compactRows.length === 4
        && compactRows.every((rect) => rect.height <= 90 && Math.abs(rect.left - compactRows[0].left) < 2),
      blackHeaderText: headerColor === 'rgb(17, 24, 39)',
    }
  })
  const watchlistFailureAxe = await watchlistFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await watchlistFailurePage.screenshot({ path: resolve(outputDir, 'watchlist-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'watchlist', scenario: 'partial-failure', watchlistFailureState,
    watchlistFailureStateComplete: Object.values(watchlistFailureState).every(Boolean),
    pageErrors: watchlistFailureErrors,
    violations: watchlistFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await watchlistFailurePage.close()

  const diagnosisLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const diagnosisLoadingErrors = []
  diagnosisLoadingPage.on('pageerror', (error) => diagnosisLoadingErrors.push(error.message))
  await diagnosisLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await diagnosisLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/quote/600519' || path === '/api/diagnose') {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await diagnosisLoadingPage.goto('http://127.0.0.1:4175/#/research?tab=diagnosis&code=600519', { waitUntil: 'domcontentloaded' })
  await diagnosisLoadingPage.waitForSelector('.diagnosis-loading')
  await diagnosisLoadingPage.addScriptTag({ content: axeSource })
  const diagnosisLoadingState = await diagnosisLoadingPage.evaluate(() => ({
    loadingVisible: Boolean(document.querySelector('.diagnosis-loading')),
    submitDisabled: Boolean(document.querySelector('.diagnosis-search__submit')?.disabled),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    honestLoadingCopy: document.body.innerText.includes('缺失项不会补 0'),
  }))
  const diagnosisLoadingAxe = await diagnosisLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await diagnosisLoadingPage.screenshot({ path: resolve(outputDir, 'diagnosis-loading-375.png'), fullPage: true })
  report.push({
    page: 'diagnosis', scenario: 'loading', diagnosisLoadingState,
    diagnosisLoadingStateComplete: Object.values(diagnosisLoadingState).every(Boolean),
    pageErrors: diagnosisLoadingErrors,
    violations: diagnosisLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await diagnosisLoadingPage.close()

  const diagnosisEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const diagnosisEmptyErrors = []
  const diagnosisOwnRequests = []
  diagnosisEmptyPage.on('pageerror', (error) => diagnosisEmptyErrors.push(error.message))
  await diagnosisEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await diagnosisEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/quote/600519' || path === '/api/diagnose') diagnosisOwnRequests.push(path)
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await diagnosisEmptyPage.goto('http://127.0.0.1:4175/#/research?tab=diagnosis', { waitUntil: 'networkidle' })
  await diagnosisEmptyPage.addScriptTag({ content: axeSource })
  const diagnosisEmptyState = await diagnosisEmptyPage.evaluate((ownRequests) => ({
    emptyVisible: Boolean(document.querySelector('.diagnosis-empty')),
    removedDescription: !document.body.innerText.includes('当前没有诊断数据。页面不会自动请求其他业务接口，也不会用示例股票、演示数字或历史值填充。'),
    noUnrelatedDiagnosisRequest: ownRequests.length === 0,
    nativeInput: document.querySelector('#diagnosis-code')?.tagName === 'INPUT',
  }), diagnosisOwnRequests)
  const diagnosisEmptyAxe = await diagnosisEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await diagnosisEmptyPage.screenshot({ path: resolve(outputDir, 'diagnosis-empty-1440.png'), fullPage: true })
  report.push({
    page: 'diagnosis', scenario: 'empty', diagnosisEmptyState,
    diagnosisEmptyStateComplete: Object.values(diagnosisEmptyState).every(Boolean),
    pageErrors: diagnosisEmptyErrors,
    violations: diagnosisEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await diagnosisEmptyPage.close()

  const diagnosisFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const diagnosisFailureErrors = []
  diagnosisFailurePage.on('pageerror', (error) => diagnosisFailureErrors.push(error.message))
  await diagnosisFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await diagnosisFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/quote/600519') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：快速报价源暂不可用' }) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await diagnosisFailurePage.goto('http://127.0.0.1:4175/#/research?tab=diagnosis&code=600519', { waitUntil: 'networkidle' })
  await diagnosisFailurePage.waitForSelector('.diagnosis-decision')
  await diagnosisFailurePage.addScriptTag({ content: axeSource })
  const diagnosisFailureState = await diagnosisFailurePage.evaluate(() => {
    const text = document.body.innerText
    return {
      decisionSurvives: text.includes('贵州茅台') && text.includes('当前动作') && text.includes('观望'),
      localFailure: text.includes('快速报价局部失败') && text.includes('快速报价源暂不可用'),
      governanceSurvives: text.includes('数据健康、版本与治理') && text.includes('建议已留痕'),
      noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    }
  })
  const diagnosisFailureAxe = await diagnosisFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await diagnosisFailurePage.screenshot({ path: resolve(outputDir, 'diagnosis-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'diagnosis', scenario: 'partial-failure', diagnosisFailureState,
    diagnosisFailureStateComplete: Object.values(diagnosisFailureState).every(Boolean),
    pageErrors: diagnosisFailureErrors,
    violations: diagnosisFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await diagnosisFailurePage.close()

  const diagnosisEvidencePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const diagnosisEvidenceErrors = []
  diagnosisEvidencePage.on('pageerror', (error) => diagnosisEvidenceErrors.push(error.message))
  await diagnosisEvidencePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await diagnosisEvidencePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/diagnose') {
      const body = structuredClone(fixtures[path])
      const quality = {
        status: 'unavailable', captured: 0, requested: 8,
        missing_fields: ['last_price', 'prev_close', 'change_pct', 'amount', 'turnover_rate', 'volume_ratio', 'pe', 'pb'],
        reason: 'market_evidence_timeout',
      }
      body.market_evidence = []
      body.decision_market_evidence = []
      body.market_evidence_quality = quality
      body.decision_market_evidence_quality = quality
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await diagnosisEvidencePage.goto('http://127.0.0.1:4175/#/research?tab=diagnosis&code=600519', { waitUntil: 'networkidle' })
  await diagnosisEvidencePage.waitForSelector('.diagnosis-quote-fallback')
  await diagnosisEvidencePage.addScriptTag({ content: axeSource })
  const diagnosisEvidenceState = await diagnosisEvidencePage.evaluate(() => {
    const text = document.body.innerText
    return {
      removedEvidenceBlockHidden: !text.includes('结论绑定的行情证据') && !text.includes('正式行情证据采集未完成') && !text.includes('未绑定正式证据'),
      unboundSnapshotVisible: text.includes('当前快速行情') && text.includes('仅供核对 · 未绑定证据 ID'),
      realValuesVisible: text.includes('1,488.52 元') && text.includes('24.62 倍') && text.includes('7.82 倍'),
      missingNotZeroFilled: !text.includes('行情证据真实空数组'),
      noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    }
  })
  const diagnosisEvidenceAxe = await diagnosisEvidencePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await diagnosisEvidencePage.screenshot({ path: resolve(outputDir, 'diagnosis-evidence-unavailable-1440.png'), fullPage: true })
  report.push({
    page: 'diagnosis', scenario: 'evidence-unavailable', diagnosisEvidenceState,
    diagnosisEvidenceStateComplete: Object.values(diagnosisEvidenceState).every(Boolean),
    pageErrors: diagnosisEvidenceErrors,
    violations: diagnosisEvidenceAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await diagnosisEvidencePage.close()

  const stockAskLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const stockAskLoadingErrors = []
  stockAskLoadingPage.on('pageerror', (error) => stockAskLoadingErrors.push(error.message))
  await stockAskLoadingPage.addInitScript(() => {
    localStorage.setItem('wudi-vue-theme', 'dark')
    sessionStorage.setItem('hermes.chat.code', '600519')
  })
  await stockAskLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/stock/600519/core' || path === '/api/stock/600519/enrichment') {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await stockAskLoadingPage.goto('http://127.0.0.1:4175/#/research?tab=ask', { waitUntil: 'domcontentloaded' })
  await stockAskLoadingPage.waitForSelector('.stock-evidence__loading')
  await stockAskLoadingPage.addScriptTag({ content: axeSource })
  const stockAskLoadingAxe = await stockAskLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  const stockAskLoadingState = await stockAskLoadingPage.evaluate(() => ({
    stockModeActive: document.querySelector('.stock-ask-mode-switch button[aria-selected="true"]')?.textContent?.trim() === '盘中问股',
    modeSwitchVisible: document.body.innerText.includes('盘中问股') && document.body.innerText.includes('自由问答'),
    evidenceLoading: Boolean(document.querySelector('.stock-evidence__loading')),
    equalComposerHeight: (() => {
      const upload = document.querySelector('.stock-ask-upload')?.getBoundingClientRect().height
      const input = document.querySelector('#stock-ask-prompt')?.getBoundingClientRect().height
      const send = document.querySelector('.stock-ask-send')?.getBoundingClientRect().height
      return Boolean(upload && input && send && upload === input && input === send)
    })(),
    mobileComposerStatic: getComputedStyle(document.querySelector('.stock-ask-composer')).position === 'static',
  }))
  await stockAskLoadingPage.screenshot({ path: resolve(outputDir, 'stock-ask-loading-375.png'), fullPage: true })
  report.push({
    page: 'stock-ask', scenario: 'loading', stockAskLoadingState,
    stockAskLoadingStateComplete: Object.values(stockAskLoadingState).every(Boolean),
    pageErrors: stockAskLoadingErrors,
    violations: stockAskLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await stockAskLoadingPage.close()

  const stockAskEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const stockAskEmptyErrors = []
  stockAskEmptyPage.on('pageerror', (error) => stockAskEmptyErrors.push(error.message))
  await stockAskEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await stockAskEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/chat/conversations' ? { ok: true, conversations: [] } : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await stockAskEmptyPage.goto('http://127.0.0.1:4175/#/research?tab=ask', { waitUntil: 'networkidle' })
  const materialInputs = stockAskEmptyPage.locator('.stock-ask-materials fieldset input')
  for (let index = 0; index < 4; index += 1) await materialInputs.nth(index).check()
  await stockAskEmptyPage.addScriptTag({ content: axeSource })
  const stockAskEmptyState = await stockAskEmptyPage.evaluate(() => {
    const text = document.body.innerText
    return {
      noHistory: text.includes('暂无问答记录'),
      directQuestion: text.includes('直接提出一个问题'),
      missingNotZero: !text.includes('0.00 元') && !text.includes('0.00%'),
      modeSwitch: document.querySelectorAll('.stock-ask-mode-switch button').length === 2,
      onePresetRow: getComputedStyle(document.querySelector('.stock-ask-presets')).gridTemplateRows.split(' ').length === 1,
      requiredCount: document.querySelectorAll('.stock-ask-materials__required').length === 3,
      materialCount: document.querySelectorAll('.stock-ask-materials fieldset input').length === 6,
      materialLimit: document.querySelectorAll('.stock-ask-materials fieldset input:checked').length === 4
        && document.querySelectorAll('.stock-ask-materials fieldset input:disabled:not(:checked)').length === 2,
    }
  })
  const stockAskEmptyAxe = await stockAskEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await stockAskEmptyPage.screenshot({ path: resolve(outputDir, 'stock-ask-empty-1440.png'), fullPage: true })
  report.push({
    page: 'stock-ask', scenario: 'empty', stockAskEmptyState,
    stockAskEmptyStateComplete: Object.values(stockAskEmptyState).every(Boolean),
    pageErrors: stockAskEmptyErrors,
    violations: stockAskEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await stockAskEmptyPage.close()

  const stockAskFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const stockAskFailureErrors = []
  stockAskFailurePage.on('pageerror', (error) => stockAskFailureErrors.push(error.message))
  await stockAskFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await stockAskFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/stock/600519/enrichment') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：个股增强证据源暂不可用' }) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await stockAskFailurePage.goto('http://127.0.0.1:4175/#/research?tab=ask', { waitUntil: 'networkidle' })
  await stockAskFailurePage.getByRole('tab', { name: '盘中问股' }).click()
  await stockAskFailurePage.locator('#stock-ask-code').fill('600519')
  await stockAskFailurePage.locator('.stock-ask-conversation__selector button[type="submit"]').click()
  await stockAskFailurePage.waitForSelector('.stock-evidence__quote')
  await stockAskFailurePage.addScriptTag({ content: axeSource })
  const stockAskFailureState = await stockAskFailurePage.evaluate(() => {
    const text = document.body.innerText
    return {
      quoteSurvives: text.includes('贵州茅台') && text.includes('1,488.52') === false && text.includes('1488.52'),
      localFailure: text.includes('增强证据局部失败') && text.includes('个股增强证据源暂不可用'),
      inputUsable: !document.querySelector('#stock-ask-prompt').disabled,
      noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    }
  })
  const stockAskFailureAxe = await stockAskFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await stockAskFailurePage.screenshot({ path: resolve(outputDir, 'stock-ask-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'stock-ask', scenario: 'partial-failure', stockAskFailureState,
    stockAskFailureStateComplete: Object.values(stockAskFailureState).every(Boolean),
    pageErrors: stockAskFailureErrors,
    violations: stockAskFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await stockAskFailurePage.close()

  const screenerLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const screenerLoadingErrors = []
  screenerLoadingPage.on('pageerror', (error) => screenerLoadingErrors.push(error.message))
  await screenerLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await screenerLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/screen') await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await screenerLoadingPage.goto('http://127.0.0.1:4175/#/research?tab=screener', { waitUntil: 'networkidle' })
  await screenerLoadingPage.locator('.screener-primary-action .primary-button').click()
  await screenerLoadingPage.waitForSelector('.screener-loading')
  await screenerLoadingPage.addScriptTag({ content: axeSource })
  const screenerLoadingState = await screenerLoadingPage.evaluate(() => ({
    loadingVisible: Boolean(document.querySelector('.screener-loading')),
    submitDisabled: Boolean(document.querySelector('.screener-primary-action .primary-button')?.disabled),
    stopNativeButton: document.querySelector('.screener-loading button')?.tagName === 'BUTTON',
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const screenerLoadingAxe = await screenerLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await screenerLoadingPage.screenshot({ path: resolve(outputDir, 'screener-loading-375.png'), fullPage: true })
  report.push({
    page: 'screener', scenario: 'loading', screenerLoadingState,
    screenerLoadingStateComplete: Object.values(screenerLoadingState).every(Boolean),
    pageErrors: screenerLoadingErrors,
    violations: screenerLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await screenerLoadingPage.close()

  const screenerEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const screenerEmptyErrors = []
  screenerEmptyPage.on('pageerror', (error) => screenerEmptyErrors.push(error.message))
  await screenerEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await screenerEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/screen'
      ? { ...fixtures[path], matched: 0, returned: 0, stocks: [], missing: [] }
      : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await screenerEmptyPage.goto('http://127.0.0.1:4175/#/research?tab=screener', { waitUntil: 'networkidle' })
  await screenerEmptyPage.locator('.screener-primary-action .primary-button').click()
  await screenerEmptyPage.waitForSelector('.screener-result-state.is-empty')
  await screenerEmptyPage.addScriptTag({ content: axeSource })
  const screenerEmptyState = await screenerEmptyPage.evaluate(() => {
    const text = document.body.innerText
    return {
      trueEmpty: text.includes('筛选结果为真实空数组'),
      nextStep: text.includes('可调整条件后重新执行'),
      noExampleStocks: !document.querySelector('.screener-stock-link'),
      nativeForm: document.querySelector('#screener-filter-form')?.tagName === 'FORM',
      noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    }
  })
  const screenerEmptyAxe = await screenerEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await screenerEmptyPage.screenshot({ path: resolve(outputDir, 'screener-empty-1440.png'), fullPage: true })
  report.push({
    page: 'screener', scenario: 'empty', screenerEmptyState,
    screenerEmptyStateComplete: Object.values(screenerEmptyState).every(Boolean),
    pageErrors: screenerEmptyErrors,
    violations: screenerEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await screenerEmptyPage.close()

  const screenerFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const screenerFailureErrors = []
  let screenerCalls = 0
  screenerFailurePage.on('pageerror', (error) => screenerFailureErrors.push(error.message))
  await screenerFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await screenerFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/screen') {
      screenerCalls += 1
      if (screenerCalls > 1) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：全市场快照暂不可用' }) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await screenerFailurePage.goto('http://127.0.0.1:4175/#/research?tab=screener', { waitUntil: 'networkidle' })
  await screenerFailurePage.locator('.screener-primary-action .primary-button').click()
  await screenerFailurePage.waitForSelector('.screener-table')
  await screenerFailurePage.locator('.screener-primary-action .primary-button').click()
  await screenerFailurePage.waitForSelector('.screener-result-alert')
  const priceSortButton = screenerFailurePage.locator('.screener-sort-button', { hasText: '现价' })
  await priceSortButton.click()
  await priceSortButton.click()
  await screenerFailurePage.waitForFunction(() => document.querySelector('.screener-table tbody tr .screener-stock-link')?.textContent?.includes('招商银行'))
  await screenerFailurePage.addScriptTag({ content: axeSource })
  const screenerFailureState = await screenerFailurePage.evaluate(() => {
    const text = document.body.innerText
    const sortButtons = [...document.querySelectorAll('.screener-sort-button')]
    return {
      localFailure: text.includes('新一轮筛选局部失败') && text.includes('全市场快照暂不可用'),
      resultsSurvive: document.querySelectorAll('.screener-table tbody tr').length === 2,
      missingPreserved: text.includes('行业缺失') && !text.includes('量比：0'),
      realDiagnosisLink: document.querySelector('.screener-stock-link')?.tagName === 'A',
      sortingRemovedFromFilter: !document.querySelector('#screener-filter-form select'),
      noStandaloneSortFields: !document.querySelector('.screener-results-heading select'),
      sortHeadersComplete: sortButtons.length === 8
        && sortButtons.map((button) => button.textContent?.replace(/[↕↑↓]/g, '').trim()).join('|') === '现价|涨跌幅|换手率|量比|PE TTM|PB|流通市值|成交额',
      currentRowsSortedByPriceAscending: document.querySelector('.screener-table tbody tr .screener-stock-link')?.textContent?.includes('招商银行'),
      priceHeaderAriaSort: document.querySelector('.screener-table th:nth-child(2)')?.getAttribute('aria-sort') === 'ascending',
      currentResultScopeClear: text.includes('点击数值列表头排序') && text.includes('仅重排当前返回结果'),
      tableContained: document.querySelector('.screener-table-wrap')?.scrollWidth >= document.querySelector('.screener-table-wrap')?.clientWidth,
      noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    }
  })
  const screenerFailureAxe = await screenerFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await screenerFailurePage.screenshot({ path: resolve(outputDir, 'screener-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'screener', scenario: 'partial-failure', screenerFailureState,
    screenerFailureStateComplete: Object.values(screenerFailureState).every(Boolean),
    pageErrors: screenerFailureErrors,
    violations: screenerFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await screenerFailurePage.close()

  const technicalScreenerLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const technicalScreenerLoadingErrors = []
  technicalScreenerLoadingPage.on('pageerror', (error) => technicalScreenerLoadingErrors.push(error.message))
  await technicalScreenerLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await technicalScreenerLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (['/api/technical-screen/catalog', '/api/technical-screen/status', '/api/technical-screen/schemes'].includes(path)) {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await technicalScreenerLoadingPage.goto('http://127.0.0.1:4175/#/research?tab=technical-screener', { waitUntil: 'domcontentloaded' })
  await technicalScreenerLoadingPage.waitForSelector('.technical-screener-loading')
  await technicalScreenerLoadingPage.addScriptTag({ content: axeSource })
  const technicalScreenerLoadingState = await technicalScreenerLoadingPage.evaluate(() => ({
    loadingVisible: Boolean(document.querySelector('.technical-screener-loading')),
    noExampleData: document.body.innerText.includes('不会用示例数字填充'),
    actionDisabled: Boolean(document.querySelector('.technical-decision__action .primary-button')?.disabled),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const technicalScreenerLoadingAxe = await technicalScreenerLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await technicalScreenerLoadingPage.screenshot({ path: resolve(outputDir, 'technical-screener-loading-375.png'), fullPage: true })
  report.push({
    page: 'technical-screener', scenario: 'loading', technicalScreenerLoadingState,
    technicalScreenerLoadingStateComplete: Object.values(technicalScreenerLoadingState).every(Boolean),
    pageErrors: technicalScreenerLoadingErrors,
    violations: technicalScreenerLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await technicalScreenerLoadingPage.close()

  const technicalScreenerEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const technicalScreenerEmptyErrors = []
  technicalScreenerEmptyPage.on('pageerror', (error) => technicalScreenerEmptyErrors.push(error.message))
  await technicalScreenerEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await technicalScreenerEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/technical-screen/query'
      ? { ...fixtures[path], matched: 0, returned: 0, stocks: [], strongest_condition: '满足规则 1', missing: [] }
      : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await technicalScreenerEmptyPage.goto('http://127.0.0.1:4175/#/research?tab=technical-screener', { waitUntil: 'networkidle' })
  await technicalScreenerEmptyPage.locator('.technical-decision__action .primary-button').click()
  await technicalScreenerEmptyPage.waitForSelector('.technical-result-state.is-empty')
  await technicalScreenerEmptyPage.addScriptTag({ content: axeSource })
  const technicalScreenerEmptyState = await technicalScreenerEmptyPage.evaluate(() => ({
    trueEmpty: document.body.innerText.includes('筛选结果为真实空数组'),
    strongestCondition: document.body.innerText.includes('限制最强：满足规则 1'),
    noExampleStocks: !document.querySelector('.technical-stock-link'),
    nativeForm: document.querySelector('#technical-screener-form')?.tagName === 'FORM',
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const technicalScreenerEmptyAxe = await technicalScreenerEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await technicalScreenerEmptyPage.screenshot({ path: resolve(outputDir, 'technical-screener-empty-1440.png'), fullPage: true })
  report.push({
    page: 'technical-screener', scenario: 'empty', technicalScreenerEmptyState,
    technicalScreenerEmptyStateComplete: Object.values(technicalScreenerEmptyState).every(Boolean),
    pageErrors: technicalScreenerEmptyErrors,
    violations: technicalScreenerEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await technicalScreenerEmptyPage.close()

  const technicalScreenerFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const technicalScreenerFailureErrors = []
  let technicalScreenerQueryCalls = 0
  technicalScreenerFailurePage.on('pageerror', (error) => technicalScreenerFailureErrors.push(error.message))
  await technicalScreenerFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await technicalScreenerFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/technical-screen/query') {
      technicalScreenerQueryCalls += 1
      if (technicalScreenerQueryCalls > 1) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：本地技术缓存暂不可读' }) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await technicalScreenerFailurePage.goto('http://127.0.0.1:4175/#/research?tab=technical-screener', { waitUntil: 'networkidle' })
  await technicalScreenerFailurePage.locator('.technical-decision__action .primary-button').click()
  await technicalScreenerFailurePage.waitForSelector('.technical-table')
  await technicalScreenerFailurePage.locator('.technical-decision__action .primary-button').click()
  await technicalScreenerFailurePage.waitForSelector('.technical-result-alert')
  await technicalScreenerFailurePage.addScriptTag({ content: axeSource })
  const technicalScreenerFailureState = await technicalScreenerFailurePage.evaluate(() => {
    const text = document.body.innerText
    const secondRow = document.querySelector('.technical-table tbody tr:nth-child(2)')?.textContent || ''
    return {
      localFailure: text.includes('新一轮筛选局部失败') && text.includes('本地技术缓存暂不可读'),
      resultsSurvive: document.querySelectorAll('.technical-table tbody tr').length === 2,
      missingPreserved: secondRow.includes('行业缺失') && secondRow.includes('—') && !secondRow.includes('0.00 元'),
      realDiagnosisLink: document.querySelector('.technical-stock-link')?.tagName === 'A',
      tableContained: document.querySelector('.technical-table-wrap')?.scrollWidth >= document.querySelector('.technical-table-wrap')?.clientWidth,
      noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    }
  })
  const technicalScreenerFailureAxe = await technicalScreenerFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await technicalScreenerFailurePage.screenshot({ path: resolve(outputDir, 'technical-screener-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'technical-screener', scenario: 'partial-failure', technicalScreenerFailureState,
    technicalScreenerFailureStateComplete: Object.values(technicalScreenerFailureState).every(Boolean),
    pageErrors: technicalScreenerFailureErrors,
    violations: technicalScreenerFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await technicalScreenerFailurePage.close()

  const forecastLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const forecastLoadingErrors = []
  forecastLoadingPage.on('pageerror', (error) => forecastLoadingErrors.push(error.message))
  await forecastLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await forecastLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/forecast/mining') await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await forecastLoadingPage.goto('http://127.0.0.1:4175/#/research?tab=events', { waitUntil: 'domcontentloaded' })
  await forecastLoadingPage.waitForSelector('.forecast-loading')
  await forecastLoadingPage.addScriptTag({ content: axeSource })
  const forecastLoadingState = await forecastLoadingPage.evaluate(() => ({
    loadingVisible: Boolean(document.querySelector('.forecast-loading')),
    titleVisible: document.body.innerText.includes('业绩预告掘金'),
    refreshDisabled: Boolean(document.querySelector('.refresh-button')?.disabled),
    fixtureAbsent: !document.body.innerText.includes('审计预增制造'),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const forecastLoadingAxe = await forecastLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await forecastLoadingPage.screenshot({ path: resolve(outputDir, 'forecast-loading-375.png'), fullPage: true })
  report.push({
    page: 'forecast-mining', scenario: 'loading', forecastLoadingState,
    forecastLoadingStateComplete: Object.values(forecastLoadingState).every(Boolean),
    pageErrors: forecastLoadingErrors,
    violations: forecastLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await forecastLoadingPage.close()

  const forecastEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const forecastEmptyErrors = []
  forecastEmptyPage.on('pageerror', (error) => forecastEmptyErrors.push(error.message))
  await forecastEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await forecastEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/forecast/mining'
      ? { ...fixtures[path], stats: { '总数': 0, '今日新增': 0, '按类型': {}, '掘金池': 0, '陷阱池': 0 }, items: [], traps: [], missing: [] }
      : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await forecastEmptyPage.goto('http://127.0.0.1:4175/#/research?tab=events', { waitUntil: 'networkidle' })
  await forecastEmptyPage.waitForSelector('.forecast-empty')
  await forecastEmptyPage.addScriptTag({ content: axeSource })
  const forecastEmptyState = await forecastEmptyPage.evaluate(() => ({
    trueEmpty: document.body.innerText.includes('接口成功返回 `items=[]` 与 `traps=[]`'),
    noFixtureStocks: !document.querySelector('.forecast-stock-link'),
    unavailableAbsent: !document.body.innerText.includes('榜单暂不可用'),
    nativeRetry: document.querySelector('.forecast-empty button')?.tagName === 'BUTTON',
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const forecastEmptyAxe = await forecastEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await forecastEmptyPage.screenshot({ path: resolve(outputDir, 'forecast-empty-1440.png'), fullPage: true })
  report.push({
    page: 'forecast-mining', scenario: 'empty', forecastEmptyState,
    forecastEmptyStateComplete: Object.values(forecastEmptyState).every(Boolean),
    pageErrors: forecastEmptyErrors,
    violations: forecastEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await forecastEmptyPage.close()

  const forecastFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const forecastFailureErrors = []
  let forecastCalls = 0
  forecastFailurePage.on('pageerror', (error) => forecastFailureErrors.push(error.message))
  await forecastFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await forecastFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/forecast/mining') {
      forecastCalls += 1
      if (forecastCalls > 1) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：公开业绩预告源暂不可达' }) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await forecastFailurePage.goto('http://127.0.0.1:4175/#/research?tab=events', { waitUntil: 'networkidle' })
  await forecastFailurePage.waitForSelector('.forecast-table')
  const forecastTypeFilter = forecastFailurePage.locator('.forecast-type-filter', { hasText: '扭亏' })
  await forecastTypeFilter.click()
  await forecastFailurePage.waitForFunction(() => document.querySelectorAll('.forecast-table tbody tr').length === 1)
  await forecastFailurePage.locator('#forecast-code-search').fill('301678')
  const forecastFilterState = await forecastFailurePage.evaluate(() => ({
    typeFilter: document.querySelectorAll('.forecast-table tbody tr').length === 1
      && document.querySelector('.forecast-table tbody tr')?.textContent?.includes('审计自愿科技'),
    codeFilter: document.querySelector('#forecast-code-search')?.value === '301678'
      && document.querySelectorAll('.forecast-table tbody tr').length === 1,
    activeTypeAnnounced: document.querySelector('.forecast-type-filter[aria-pressed="true"]')?.textContent?.includes('扭亏'),
  }))
  await forecastTypeFilter.click()
  await forecastFailurePage.locator('#forecast-code-search').fill('')
  await forecastFailurePage.waitForFunction(() => document.querySelectorAll('.forecast-table tbody tr').length === 2)
  await forecastFailurePage.locator('.refresh-button').click()
  await forecastFailurePage.waitForSelector('.forecast-alert')
  await forecastFailurePage.addScriptTag({ content: axeSource })
  const forecastFailureDetails = await forecastFailurePage.evaluate(() => {
    const secondRow = document.querySelector('.forecast-table tbody tr:nth-child(2)')?.textContent || ''
    return {
      localFailure: document.body.innerText.includes('最新刷新失败，保留已加载榜单') && document.body.innerText.includes('公开业绩预告源暂不可达'),
      resultsSurvive: document.querySelectorAll('.forecast-table tbody tr').length === 2,
      missingPreserved: secondRow.includes('—') && !secondRow.includes('0%') && !secondRow.includes('0 元'),
      realDiagnosisLink: document.querySelector('.forecast-stock-link')?.tagName === 'A',
      tableContained: document.querySelector('.forecast-table-wrap')?.scrollWidth >= document.querySelector('.forecast-table-wrap')?.clientWidth,
      noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    }
  })
  const forecastFailureState = { ...forecastFilterState, ...forecastFailureDetails }
  const forecastFailureAxe = await forecastFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await forecastFailurePage.screenshot({ path: resolve(outputDir, 'forecast-partial-failure-1440.png'), fullPage: true })
  report.push({
    page: 'forecast-mining', scenario: 'partial-failure', forecastFailureState,
    forecastFailureStateComplete: Object.values(forecastFailureState).every(Boolean),
    pageErrors: forecastFailureErrors,
    violations: forecastFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await forecastFailurePage.close()

  for (const variant of [
    { name: '375-dark', width: 375, height: 812, theme: 'dark' },
    { name: '1440-dark', width: 1440, height: 1000, theme: 'dark' },
    { name: '1440-light', width: 1440, height: 1000, theme: 'light' },
  ]) {
    const chainPage = await browser.newPage({ viewport: { width: variant.width, height: variant.height }, deviceScaleFactor: 1 })
    const chainErrors = []
    chainPage.on('pageerror', (error) => chainErrors.push(error.message))
    await chainPage.addInitScript((theme) => localStorage.setItem('wudi-vue-theme', theme), variant.theme)
    await chainPage.route('**/api/**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname === '/api/chat' && route.request().method() === 'POST') {
        return route.fulfill({ status: 200, contentType: 'text/event-stream; charset=utf-8', body: chainSse(industryChainMarkdown) })
      }
      if (url.pathname === '/api/knowledge' && route.request().method() === 'POST') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, id: 'audit-chain-note' }) })
      }
      const body = fixtures[url.pathname]
      return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
    })
    await chainPage.goto('http://127.0.0.1:4175/#/research?tab=chain', { waitUntil: 'networkidle' })
    await chainPage.locator('#industry-chain-input').fill('光模块')
    await chainPage.locator('.chain-form .primary-button').click()
    await chainPage.waitForSelector('.report-diagnosis-link')
    if (variant.name === '1440-dark') {
      await chainPage.locator('.chain-document__actions .primary-button').click()
      await chainPage.waitForSelector('.chain-save-feedback .is-success')
    }
    await chainPage.addScriptTag({ content: axeSource })
    const chainCompleteState = await chainPage.evaluate((expectedTheme) => {
      const targets = [...document.querySelectorAll('.industry-chain-page button, .industry-chain-page a')]
        .filter((node) => {
          const rect = node.getBoundingClientRect()
          const style = getComputedStyle(node)
          return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
        })
        .map((node) => {
          const rect = node.getBoundingClientRect()
          return { width: rect.width, height: rect.height }
        })
      const table = document.querySelector('.chain-document .report-table-wrap')
      return {
        titleVisible: document.body.innerText.includes('产业链研究'),
        conclusionVisible: document.body.innerText.includes('产业结论：已用 TDX 验证'),
        tradeBoundaryVisible: document.body.innerText.includes('当前是否可交易、价格、仓位和风险必须另用当时实时行情与风险闸复核'),
        realCompanyLinks: document.querySelectorAll('.report-diagnosis-link[href*="tab=diagnosis&code="]').length === 2,
        noCandidateShortcut: !document.querySelector('.chain-company-links'),
        semanticForm: document.querySelector('.chain-form')?.tagName === 'FORM',
        nativeActions: [...document.querySelectorAll('.chain-document__actions button')].every((node) => node.tagName === 'BUTTON'),
        tableContained: Boolean(table && table.scrollWidth >= table.clientWidth),
        noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
        targetsAtLeast44: targets.every((target) => target.width >= 44 && target.height >= 44),
        themeApplied: document.documentElement.dataset.theme === expectedTheme,
        missingPreserved: document.body.innerText.includes('—') && !document.body.innerText.includes('0.00%'),
      }
    }, variant.theme)
    const chainAxe = await chainPage.evaluate(async () => window.axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    }))
    await chainPage.screenshot({ path: resolve(outputDir, `industry-chain-complete-${variant.name}.png`), fullPage: true })
    report.push({
      page: 'industry-chain', scenario: `complete-${variant.name}`, chainCompleteState,
      industryChainCompleteStateComplete: Object.values(chainCompleteState).every(Boolean),
      pageErrors: chainErrors,
      violations: chainAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
    })
    await chainPage.close()
  }

  const chainLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const chainLoadingErrors = []
  chainLoadingPage.on('pageerror', (error) => chainLoadingErrors.push(error.message))
  await chainLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await chainLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/chat') {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
      return route.fulfill({ status: 200, contentType: 'text/event-stream; charset=utf-8', body: chainSse(industryChainMarkdown) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await chainLoadingPage.goto('http://127.0.0.1:4175/#/research?tab=chain', { waitUntil: 'networkidle' })
  await chainLoadingPage.locator('#industry-chain-input').fill('光模块')
  await chainLoadingPage.locator('.chain-form .primary-button').click()
  await chainLoadingPage.waitForSelector('.chain-progress')
  await chainLoadingPage.addScriptTag({ content: axeSource })
  const chainLoadingState = await chainLoadingPage.evaluate(() => ({
    loadingVisible: Boolean(document.querySelector('.chain-progress')),
    stopNative: document.querySelector('.chain-form .danger-button')?.tagName === 'BUTTON',
    conclusionDeferred: document.body.innerText.includes('正在建立证据底座，暂不形成结论'),
    fixtureAbsent: !document.body.innerText.includes('审计光通信甲'),
    refreshDisabled: Boolean(document.querySelector('.refresh-button')?.disabled),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const chainLoadingAxe = await chainLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await chainLoadingPage.screenshot({ path: resolve(outputDir, 'industry-chain-loading-375-dark.png'), fullPage: true })
  report.push({
    page: 'industry-chain', scenario: 'loading', chainLoadingState,
    industryChainLoadingStateComplete: Object.values(chainLoadingState).every(Boolean),
    pageErrors: chainLoadingErrors,
    violations: chainLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await chainLoadingPage.close()

  const chainEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const chainEmptyErrors = []
  chainEmptyPage.on('pageerror', (error) => chainEmptyErrors.push(error.message))
  await chainEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await chainEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/chat') return route.fulfill({ status: 200, contentType: 'text/event-stream; charset=utf-8', body: chainSse(emptyIndustryChainMarkdown) })
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await chainEmptyPage.goto('http://127.0.0.1:4175/#/research?tab=chain', { waitUntil: 'networkidle' })
  await chainEmptyPage.locator('#industry-chain-input').fill('未匹配产业')
  await chainEmptyPage.locator('.chain-form .primary-button').click()
  await chainEmptyPage.waitForSelector('.chain-true-empty')
  await chainEmptyPage.addScriptTag({ content: axeSource })
  const chainEmptyState = await chainEmptyPage.evaluate(() => ({
    trueEmpty: document.body.innerText.includes('真实空结果：TDX 板块或成分关系未匹配'),
    noCompanyLinks: !document.querySelector('.report-diagnosis-link'),
    noInventedZero: !document.body.innerText.includes('0.00%') && !document.body.innerText.includes('0 元'),
    missingTimeVisible: document.body.innerText.includes('板块目录 缺失'),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const chainEmptyAxe = await chainEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await chainEmptyPage.screenshot({ path: resolve(outputDir, 'industry-chain-empty-1440-dark.png'), fullPage: true })
  report.push({
    page: 'industry-chain', scenario: 'empty', chainEmptyState,
    industryChainEmptyStateComplete: Object.values(chainEmptyState).every(Boolean),
    pageErrors: chainEmptyErrors,
    violations: chainEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await chainEmptyPage.close()

  const chainFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const chainFailureErrors = []
  chainFailurePage.on('pageerror', (error) => chainFailureErrors.push(error.message))
  await chainFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await chainFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/chat') return route.fulfill({ status: 200, contentType: 'text/event-stream; charset=utf-8', body: chainSse(industryChainMarkdown.slice(0, 760), { complete: false }) })
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await chainFailurePage.goto('http://127.0.0.1:4175/#/research?tab=chain', { waitUntil: 'networkidle' })
  await chainFailurePage.locator('#industry-chain-input').fill('光模块')
  await chainFailurePage.locator('.chain-form .primary-button').click()
  await chainFailurePage.waitForSelector('.chain-quarantine')
  await chainFailurePage.addScriptTag({ content: axeSource })
  const chainFailureState = await chainFailurePage.evaluate(() => ({
    partialIsolated: document.body.innerText.includes('不完整输出 · 已隔离 · 不可存入知识库'),
    localFailureExplained: document.body.innerText.includes('研究流未完整结束'),
    partialContentVisible: document.body.innerText.includes('产业结论'),
    saveUnavailable: !document.querySelector('.chain-document__actions .primary-button'),
    shellSurvives: Boolean(document.querySelector('.topbar')),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const chainFailureAxe = await chainFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await chainFailurePage.screenshot({ path: resolve(outputDir, 'industry-chain-partial-failure-1440-dark.png'), fullPage: true })
  report.push({
    page: 'industry-chain', scenario: 'partial-failure', chainFailureState,
    industryChainFailureStateComplete: Object.values(chainFailureState).every(Boolean),
    pageErrors: chainFailureErrors,
    violations: chainFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await chainFailurePage.close()

  for (const variant of [
    { name: '375-dark', width: 375, height: 812, theme: 'dark' },
    { name: '1440-dark', width: 1440, height: 1000, theme: 'dark' },
    { name: '1440-light', width: 1440, height: 1000, theme: 'light' },
  ]) {
    const companyPage = await browser.newPage({ viewport: { width: variant.width, height: variant.height }, deviceScaleFactor: 1 })
    const companyErrors = []
    companyPage.on('pageerror', (error) => companyErrors.push(error.message))
    await companyPage.addInitScript((theme) => localStorage.setItem('wudi-vue-theme', theme), variant.theme)
    await companyPage.route('**/api/**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname === '/api/chat' && route.request().method() === 'POST') {
        return route.fulfill({ status: 200, contentType: 'text/event-stream; charset=utf-8', body: companySse(companyResearchMarkdown) })
      }
      if (url.pathname === '/api/knowledge' && route.request().method() === 'POST') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, id: 'audit-company-note' }) })
      }
      const body = fixtures[url.pathname]
      return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
    })
    await companyPage.goto('http://127.0.0.1:4175/#/research?tab=company', { waitUntil: 'networkidle' })
    await companyPage.locator('#company-research-code').fill('000001')
    await companyPage.locator('.company-form .primary-button').click()
    await companyPage.waitForSelector('.company-report__body')
    await companyPage.getByText('审计公司年度报告', { exact: true }).first().waitFor()
    if (variant.name === '1440-dark') {
      await companyPage.locator('.company-report__actions .primary-button').click()
      await companyPage.waitForSelector('.company-save-feedback .is-success')
    }
    await companyPage.evaluate(() => window.scrollTo(0, 0))
    await companyPage.addScriptTag({ content: axeSource })
    const companyCompleteState = await companyPage.evaluate((expectedTheme) => {
      const targets = [...document.querySelectorAll('.company-research-page button, .company-research-page a')]
        .filter((node) => {
          const rect = node.getBoundingClientRect()
          const style = getComputedStyle(node)
          return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
        })
        .map((node) => {
          const rect = node.getBoundingClientRect()
          return { width: rect.width, height: rect.height }
        })
      const table = document.querySelector('.company-report .report-table-wrap')
      return {
        titleVisible: document.body.innerText.includes('公司研究'),
        reportVisible: document.body.innerText.includes('审计公司（000001）深度研究'),
        officialEvidenceVisible: document.body.innerText.includes('审计公司年度报告'),
        managementReplySeparated: document.body.innerText.includes('投资者提问') && document.body.innerText.includes('管理层回复'),
        deterministicBoundaryVisible: document.body.innerText.includes('本页接口未独立提供，不从报告文本推导'),
        realDiagnosisLink: Boolean(document.querySelector('a[href*="tab=diagnosis&code=000001"]')),
        safeOfficialLink: Boolean(document.querySelector('a[href^="https://disc.static.szse.cn/"][rel="noopener noreferrer"]')),
        semanticForm: document.querySelector('.company-form')?.tagName === 'FORM',
        nativeActions: [...document.querySelectorAll('.company-report__actions button')].every((node) => node.tagName === 'BUTTON'),
        tableContained: Boolean(table && table.scrollWidth >= table.clientWidth),
        noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
        targetsAtLeast44: targets.every((target) => target.width >= 44 && target.height >= 44),
        themeApplied: document.documentElement.dataset.theme === expectedTheme,
        missingPreserved: document.body.innerText.includes('—') && !document.body.innerText.includes('0.00%'),
      }
    }, variant.theme)
    const companyAxe = await companyPage.evaluate(async () => window.axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    }))
    await companyPage.screenshot({ path: resolve(outputDir, `company-research-complete-${variant.name}.png`), fullPage: true })
    report.push({
      page: 'company-research', scenario: `complete-${variant.name}`, companyCompleteState,
      companyResearchCompleteStateComplete: Object.values(companyCompleteState).every(Boolean),
      pageErrors: companyErrors,
      violations: companyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
    })
    await companyPage.close()
  }

  const companyLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const companyLoadingErrors = []
  companyLoadingPage.on('pageerror', (error) => companyLoadingErrors.push(error.message))
  await companyLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await companyLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/chat') {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
      return route.fulfill({ status: 200, contentType: 'text/event-stream; charset=utf-8', body: companySse(companyResearchMarkdown) })
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await companyLoadingPage.goto('http://127.0.0.1:4175/#/research?tab=company', { waitUntil: 'networkidle' })
  await companyLoadingPage.locator('#company-research-code').fill('000001')
  await companyLoadingPage.locator('.company-form .primary-button').click()
  await companyLoadingPage.waitForSelector('.company-progress')
  await companyLoadingPage.addScriptTag({ content: axeSource })
  const companyLoadingState = await companyLoadingPage.evaluate(() => ({
    loadingVisible: Boolean(document.querySelector('.company-progress')),
    stopNative: document.querySelector('.company-form .danger-button')?.tagName === 'BUTTON',
    conclusionDeferred: document.body.innerText.includes('暂不形成新结论'),
    fixtureReportAbsent: !document.body.innerText.includes('审计公司（000001）深度研究'),
    refreshDisabled: Boolean(document.querySelector('.refresh-button')?.disabled),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const companyLoadingAxe = await companyLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await companyLoadingPage.screenshot({ path: resolve(outputDir, 'company-research-loading-375-dark.png'), fullPage: true })
  report.push({
    page: 'company-research', scenario: 'loading', companyLoadingState,
    companyResearchLoadingStateComplete: Object.values(companyLoadingState).every(Boolean),
    pageErrors: companyLoadingErrors,
    violations: companyLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await companyLoadingPage.close()

  const companyEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const companyEmptyErrors = []
  companyEmptyPage.on('pageerror', (error) => companyEmptyErrors.push(error.message))
  await companyEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await companyEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/status') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ model_connection: { configured: false, connected: null }, hermes: { agent_capability: { ready: false, reason: '审计场景：模型未配置' } } }) })
    if (path === '/api/disclosures/announcements') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, code: '000001', exchange: 'szse', state: 'complete', verification_state: 'empty_confirmed', used_last_good: false, data_as_of: '2026-07-22', requested_window: { start: '2026-06-22', end: '2026-07-22' }, items: [] }) })
    if (path === '/api/interactions/management') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, code: '000001', state: 'complete_empty', data_as_of: '2026-07-22', is_formal_disclosure: false, items: [] }) })
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await companyEmptyPage.goto('http://127.0.0.1:4175/#/research?tab=company', { waitUntil: 'networkidle' })
  await companyEmptyPage.locator('#company-research-code').fill('000001')
  await companyEmptyPage.locator('.company-form .primary-button').click()
  await companyEmptyPage.getByText('双目录空结果已核对', { exact: true }).waitFor()
  await companyEmptyPage.addScriptTag({ content: axeSource })
  const companyEmptyState = await companyEmptyPage.evaluate(() => ({
    disclosureTrueEmpty: document.body.innerText.includes('查询窗口内未发现公告'),
    interactionTrueEmpty: document.body.innerText.includes('查询窗口内暂无公开互动问答'),
    researchFoundationRemoved: !document.body.innerText.includes('盘后研究底座'),
    reportAbsent: document.body.innerText.includes('本轮没有形成研究正文'),
    noInventedZero: !document.body.innerText.includes('0.00%') && !document.body.innerText.includes('0 元'),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const companyEmptyAxe = await companyEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await companyEmptyPage.screenshot({ path: resolve(outputDir, 'company-research-empty-1440-dark.png'), fullPage: true })
  report.push({
    page: 'company-research', scenario: 'empty', companyEmptyState,
    companyResearchEmptyStateComplete: Object.values(companyEmptyState).every(Boolean),
    pageErrors: companyEmptyErrors,
    violations: companyEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await companyEmptyPage.close()

  const companyFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const companyFailureErrors = []
  companyFailurePage.on('pageerror', (error) => companyFailureErrors.push(error.message))
  await companyFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await companyFailurePage.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/chat') return route.fulfill({ status: 200, contentType: 'text/event-stream; charset=utf-8', body: companySse(companyResearchMarkdown) })
    if (url.pathname === '/api/disclosures/announcements') return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：公告链路暂不可用' }) })
    const body = fixtures[url.pathname]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await companyFailurePage.goto('http://127.0.0.1:4175/#/research?tab=company', { waitUntil: 'networkidle' })
  await companyFailurePage.locator('#company-research-code').fill('000001')
  await companyFailurePage.locator('.company-form .primary-button').click()
  await companyFailurePage.waitForSelector('.company-report__body')
  await companyFailurePage.getByText('当前无法读取官方公告', { exact: true }).waitFor()
  await companyFailurePage.addScriptTag({ content: axeSource })
  const companyFailureState = await companyFailurePage.evaluate(() => ({
    localFailureExplained: document.body.innerText.includes('审计场景：公告链路暂不可用'),
    cannotClaimEmpty: document.body.innerText.includes('不能据此判断查询窗口内“无公告”'),
    reportSurvives: document.body.innerText.includes('审计公司（000001）深度研究'),
    otherEvidenceSurvives: document.body.innerText.includes('年度经营目标的执行进度如何'),
    shellSurvives: Boolean(document.querySelector('.topbar')),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const companyFailureAxe = await companyFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await companyFailurePage.screenshot({ path: resolve(outputDir, 'company-research-partial-failure-1440-dark.png'), fullPage: true })
  report.push({
    page: 'company-research', scenario: 'partial-failure', companyFailureState,
    companyResearchFailureStateComplete: Object.values(companyFailureState).every(Boolean),
    pageErrors: companyFailureErrors,
    violations: companyFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await companyFailurePage.close()

  const reportsLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const reportsLoadingErrors = []
  reportsLoadingPage.on('pageerror', (error) => reportsLoadingErrors.push(error.message))
  await reportsLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await reportsLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (['/api/archive/dates', '/api/reports/morning', '/api/delivery/receipts'].includes(path)) {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    }
    const body = path === '/api/reports/morning' ? reportCenterFixture : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await reportsLoadingPage.goto('http://127.0.0.1:4175/#/history?tab=reports', { waitUntil: 'domcontentloaded' })
  await reportsLoadingPage.waitForSelector('.report-center-loading')
  await reportsLoadingPage.addScriptTag({ content: axeSource })
  const reportsLoadingState = await reportsLoadingPage.evaluate(() => ({
    skeletonsVisible: document.querySelectorAll('.report-center-loading .skeleton-block').length === 2,
    shellSurvives: Boolean(document.querySelector('.topbar')),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const reportsLoadingAxe = await reportsLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await reportsLoadingPage.screenshot({ path: resolve(outputDir, 'reports-loading-375-dark.png'), fullPage: true })
  report.push({
    page: 'reports', scenario: 'reports-loading-375', reportsLoadingState,
    reportsLoadingStateComplete: Object.values(reportsLoadingState).every(Boolean),
    pageErrors: reportsLoadingErrors,
    violations: reportsLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await reportsLoadingPage.close()

  const reportsEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const reportsEmptyErrors = []
  reportsEmptyPage.on('pageerror', (error) => reportsEmptyErrors.push(error.message))
  await reportsEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await reportsEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/reports/morning'
      ? { ok: false, error: '该日期无此报告' }
      : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await reportsEmptyPage.goto('http://127.0.0.1:4175/#/history?tab=reports', { waitUntil: 'networkidle' })
  await reportsEmptyPage.addScriptTag({ content: axeSource })
  const reportsEmptyState = await reportsEmptyPage.evaluate(() => ({
    emptyExplained: document.body.innerText.includes('暂无「早盘内参」') && document.body.innerText.includes('该日期无此报告'),
    generationAvailable: Boolean([...document.querySelectorAll('button')].find((node) => node.textContent?.includes('生成今日报告'))),
    noExampleNumbers: !document.body.innerText.includes('审计早盘内参'),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const reportsEmptyAxe = await reportsEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await reportsEmptyPage.screenshot({ path: resolve(outputDir, 'reports-empty-1440-dark.png'), fullPage: true })
  report.push({
    page: 'reports', scenario: 'reports-empty-1440', reportsEmptyState,
    reportsEmptyStateComplete: Object.values(reportsEmptyState).every(Boolean),
    pageErrors: reportsEmptyErrors,
    violations: reportsEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await reportsEmptyPage.close()

  const reportsFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const reportsFailureErrors = []
  reportsFailurePage.on('pageerror', (error) => reportsFailureErrors.push(error.message))
  await reportsFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await reportsFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/archive/dates' || path === '/api/delivery/receipts') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：局部链路暂不可用' }) })
    }
    const body = path === '/api/reports/morning' ? reportCenterFixture : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await reportsFailurePage.goto('http://127.0.0.1:4175/#/history?tab=reports', { waitUntil: 'networkidle' })
  await reportsFailurePage.addScriptTag({ content: axeSource })
  const reportsFailureState = await reportsFailurePage.evaluate(() => ({
    reportSurvives: document.body.innerText.includes('审计早盘内参'),
    historyFailureExplained: document.body.innerText.includes('历史日期读取失败'),
    receiptFailureExplained: document.body.innerText.includes('回执读取失败'),
    deliveryNotOverstated: document.body.innerText.includes('历史推送记录') && document.body.innerText.includes('未提供平台接受或送达层级'),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const reportsFailureAxe = await reportsFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await reportsFailurePage.screenshot({ path: resolve(outputDir, 'reports-partial-failure-1440-dark.png'), fullPage: true })
  report.push({
    page: 'reports', scenario: 'reports-partial-failure-1440', reportsFailureState,
    reportsFailureStateComplete: Object.values(reportsFailureState).every(Boolean),
    pageErrors: reportsFailureErrors,
    violations: reportsFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await reportsFailurePage.close()

  const knowledgeLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const knowledgeLoadingErrors = []
  knowledgeLoadingPage.on('pageerror', (error) => knowledgeLoadingErrors.push(error.message))
  await knowledgeLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await knowledgeLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/knowledge') await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await knowledgeLoadingPage.goto('http://127.0.0.1:4175/#/history?tab=knowledge', { waitUntil: 'domcontentloaded' })
  await knowledgeLoadingPage.waitForSelector('.knowledge-loading')
  await knowledgeLoadingPage.addScriptTag({ content: axeSource })
  const knowledgeLoadingState = await knowledgeLoadingPage.evaluate(() => ({
    delayedSkeletonVisible: document.querySelectorAll('.knowledge-loading__cards > .panel').length === 4,
    shellSurvives: Boolean(document.querySelector('.topbar')),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const knowledgeLoadingAxe = await knowledgeLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await knowledgeLoadingPage.screenshot({ path: resolve(outputDir, 'knowledge-loading-375-dark.png'), fullPage: true })
  report.push({
    page: 'knowledge', scenario: 'knowledge-loading-375', knowledgeLoadingState,
    knowledgeLoadingStateComplete: Object.values(knowledgeLoadingState).every(Boolean),
    pageErrors: knowledgeLoadingErrors,
    violations: knowledgeLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await knowledgeLoadingPage.close()

  const knowledgeEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const knowledgeEmptyErrors = []
  knowledgeEmptyPage.on('pageerror', (error) => knowledgeEmptyErrors.push(error.message))
  await knowledgeEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await knowledgeEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/knowledge'
      ? { ok: true, schema_version: 'KnowledgeRule.v1', summary: { approved: 0 }, review_queue_count: 0, items: [] }
      : fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await knowledgeEmptyPage.goto('http://127.0.0.1:4175/#/history?tab=knowledge', { waitUntil: 'networkidle' })
  await knowledgeEmptyPage.addScriptTag({ content: axeSource })
  const knowledgeEmptyState = await knowledgeEmptyPage.evaluate(() => ({
    emptyExplained: document.body.innerText.includes('知识库暂无条目') && document.body.innerText.includes('真实空数组'),
    createAvailable: Boolean([...document.querySelectorAll('button')].find((node) => node.textContent?.includes('新增第一条知识'))),
    noFixtureContent: !document.body.innerText.includes('审计规则：风险期降低试错频率'),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const knowledgeEmptyAxe = await knowledgeEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await knowledgeEmptyPage.screenshot({ path: resolve(outputDir, 'knowledge-empty-1440-dark.png'), fullPage: true })
  report.push({
    page: 'knowledge', scenario: 'knowledge-empty-1440', knowledgeEmptyState,
    knowledgeEmptyStateComplete: Object.values(knowledgeEmptyState).every(Boolean),
    pageErrors: knowledgeEmptyErrors,
    violations: knowledgeEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await knowledgeEmptyPage.close()

  const knowledgeFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const knowledgeFailureErrors = []
  knowledgeFailurePage.on('pageerror', (error) => knowledgeFailureErrors.push(error.message))
  await knowledgeFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await knowledgeFailurePage.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname === '/api/knowledge/k_audit_draft/approve' && route.request().method() === 'POST') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：审批链路暂不可用' }) })
    }
    const body = fixtures[url.pathname]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await knowledgeFailurePage.goto('http://127.0.0.1:4175/#/history?tab=knowledge', { waitUntil: 'networkidle' })
  await knowledgeFailurePage.getByRole('button', { name: '审批', exact: true }).first().click()
  await knowledgeFailurePage.getByRole('button', { name: '确认批准' }).click()
  await knowledgeFailurePage.getByText('审计场景：审批链路暂不可用', { exact: true }).waitFor()
  await knowledgeFailurePage.addScriptTag({ content: axeSource })
  const knowledgeFailureState = await knowledgeFailurePage.evaluate(() => ({
    actionFailureExplained: document.body.innerText.includes('审计场景：审批链路暂不可用'),
    listSurvives: document.body.innerText.includes('审计待复核：确认信号需跨日验证') && document.body.innerText.includes('审计规则：风险期降低试错频率'),
    dialogStaysOpen: Boolean(document.querySelector('.knowledge-dialog[open]')),
    shellSurvives: Boolean(document.querySelector('.topbar')),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const knowledgeFailureAxe = await knowledgeFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await knowledgeFailurePage.screenshot({ path: resolve(outputDir, 'knowledge-partial-failure-1440-dark.png'), fullPage: true })
  report.push({
    page: 'knowledge', scenario: 'knowledge-partial-failure-1440', knowledgeFailureState,
    knowledgeFailureStateComplete: Object.values(knowledgeFailureState).every(Boolean),
    pageErrors: knowledgeFailureErrors,
    violations: knowledgeFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await knowledgeFailurePage.close()

  const settingsLoadingPage = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  const settingsLoadingErrors = []
  settingsLoadingPage.on('pageerror', (error) => settingsLoadingErrors.push(error.message))
  await settingsLoadingPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await settingsLoadingPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (['/api/settings', '/api/status', '/api/jobs', '/api/datasources', '/api/delivery/readiness', '/api/delivery/receipts', '/api/delivery/outbox', '/api/agent/runs', '/api/settings/model-connections'].includes(path)) {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
    }
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await settingsLoadingPage.goto('http://127.0.0.1:4175/#settings-tasks', { waitUntil: 'domcontentloaded' })
  await settingsLoadingPage.waitForSelector('.settings-loading')
  await settingsLoadingPage.addScriptTag({ content: axeSource })
  const settingsLoadingState = await settingsLoadingPage.evaluate(() => ({
    delayedSkeletonVisible: document.querySelectorAll('.settings-loading > .panel').length === 4,
    shellSurvives: Boolean(document.querySelector('.topbar')),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }))
  const settingsLoadingAxe = await settingsLoadingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await settingsLoadingPage.screenshot({ path: resolve(outputDir, 'settings-loading-375-dark.png'), fullPage: true })
  report.push({
    page: 'settings', scenario: 'settings-loading-375', settingsLoadingState,
    settingsLoadingStateComplete: Object.values(settingsLoadingState).every(Boolean),
    pageErrors: settingsLoadingErrors,
    violations: settingsLoadingAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await settingsLoadingPage.close()

  const settingsEmptyPage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const settingsEmptyErrors = []
  settingsEmptyPage.on('pageerror', (error) => settingsEmptyErrors.push(error.message))
  await settingsEmptyPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await settingsEmptyPage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    let body = fixtures[path]
    if (path === '/api/jobs') body = { ok: true, schema_version: 'task-center-v1', generated_at: '2026-07-28T10:31:00+08:00', jobs: [] }
    if (path === '/api/datasources') body = { ok: true, source_contract: 'field-route-v2', active_policy: 'free_only', ts: '2026-07-28 10:31:00', sources: [] }
    if (path === '/api/delivery/readiness') body = { ok: true, launch_ready: false, configured: false, channel: null, targets: [], blockers: ['push_channel_unconfigured'], manual_review_count: 0, channels: [] }
    if (path === '/api/delivery/receipts') body = { ok: true, receipts: [], count: 0, total: 0 }
    if (path === '/api/delivery/outbox') body = { ok: true, items: [], count: 0 }
    if (path === '/api/agent/runs') body = { ok: true, items: [] }
    if (path === '/api/settings/model-connections') body = { ok: true, active_connection_id: null, connections: [] }
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await settingsEmptyPage.goto('http://127.0.0.1:4175/#settings-tasks', { waitUntil: 'networkidle' })
  await settingsEmptyPage.addScriptTag({ content: axeSource })
  const taskEmptyExplained = await settingsEmptyPage.evaluate(() => document.body.innerText.includes('暂无已登记任务'))
  await settingsEmptyPage.evaluate(() => { window.location.hash = '#settings-data' })
  await settingsEmptyPage.waitForSelector('#settings-data')
  const datasourceEmptyExplained = await settingsEmptyPage.evaluate(() => document.body.innerText.includes('真实空来源列表'))
  await settingsEmptyPage.evaluate(() => { window.location.hash = '#settings-models' })
  await settingsEmptyPage.waitForSelector('#settings-models')
  const modelEmptyExplained = await settingsEmptyPage.evaluate(() => document.body.innerText.includes('真实空数组：尚未保存自定义模型连接'))
  await settingsEmptyPage.evaluate(() => { window.location.hash = '#settings-delivery' })
  await settingsEmptyPage.waitForSelector('#settings-delivery')
  const receiptEmptyExplained = await settingsEmptyPage.evaluate(() => document.body.innerText.includes('真实空数组：暂无交付尝试记录'))
  const settingsEmptyState = await settingsEmptyPage.evaluate(({ taskEmptyExplained, datasourceEmptyExplained, modelEmptyExplained, receiptEmptyExplained }) => ({
    taskEmptyExplained, datasourceEmptyExplained, modelEmptyExplained, receiptEmptyExplained,
    noProductionFixture: !document.body.innerText.includes('审计受限模型'),
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }), { taskEmptyExplained, datasourceEmptyExplained, modelEmptyExplained, receiptEmptyExplained })
  const settingsEmptyAxe = await settingsEmptyPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await settingsEmptyPage.screenshot({ path: resolve(outputDir, 'settings-empty-1440-dark.png'), fullPage: true })
  report.push({
    page: 'settings', scenario: 'settings-empty-1440', settingsEmptyState,
    settingsEmptyStateComplete: Object.values(settingsEmptyState).every(Boolean),
    pageErrors: settingsEmptyErrors,
    violations: settingsEmptyAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await settingsEmptyPage.close()

  const settingsFailurePage = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const settingsFailureErrors = []
  settingsFailurePage.on('pageerror', (error) => settingsFailureErrors.push(error.message))
  await settingsFailurePage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await settingsFailurePage.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/datasources') return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：数据源健康接口暂不可用' }) })
    const body = fixtures[path]
    return route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body || { error: 'audit fixture missing' }) })
  })
  await settingsFailurePage.goto('http://127.0.0.1:4175/#settings-data', { waitUntil: 'networkidle' })
  await settingsFailurePage.addScriptTag({ content: axeSource })
  const localFailureExplained = await settingsFailurePage.evaluate(() => document.body.innerText.includes('数据健康暂不可用') && document.body.innerText.includes('审计场景：数据源健康接口暂不可用'))
  await settingsFailurePage.evaluate(() => { window.location.hash = '#settings-tasks' })
  await settingsFailurePage.waitForSelector('#settings-tasks')
  const taskSurvives = await settingsFailurePage.evaluate(() => document.body.innerText.includes('早盘内参') && document.body.innerText.includes('资金快照'))
  await settingsFailurePage.evaluate(() => { window.location.hash = '#settings-delivery' })
  await settingsFailurePage.waitForSelector('#settings-delivery')
  const deliverySurvives = await settingsFailurePage.evaluate(() => document.body.innerText.includes('自动发送已就绪'))
  await settingsFailurePage.evaluate(() => { window.location.hash = '#settings-models' })
  await settingsFailurePage.waitForSelector('#settings-models')
  const modelSurvives = await settingsFailurePage.evaluate(() => document.body.innerText.includes('审计受限模型'))
  await settingsFailurePage.evaluate(() => { window.location.hash = '#settings-data' })
  await settingsFailurePage.waitForSelector('#settings-data')
  const settingsFailureState = await settingsFailurePage.evaluate(({ localFailureExplained, taskSurvives, deliverySurvives, modelSurvives }) => ({
    localFailureExplained, taskSurvives, deliverySurvives, modelSurvives,
    noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
  }), { localFailureExplained, taskSurvives, deliverySurvives, modelSurvives })
  const settingsFailureAxe = await settingsFailurePage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  await settingsFailurePage.screenshot({ path: resolve(outputDir, 'settings-partial-failure-1440-dark.png'), fullPage: true })
  report.push({
    page: 'settings', scenario: 'settings-partial-failure-1440', settingsFailureState,
    settingsFailureStateComplete: Object.values(settingsFailureState).every(Boolean),
    pageErrors: settingsFailureErrors,
    violations: settingsFailureAxe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help, nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })) })),
  })
  await settingsFailurePage.close()

  const missingPage = await browser.newPage({ viewport: { width: 375, height: 812 } })
  const missingPageErrors = []
  missingPage.on('pageerror', (error) => missingPageErrors.push(error.message))
  await missingPage.route('**/api/**', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ ok: false, missing: ['审计场景：接口暂不可用'] }),
  }))
  await missingPage.goto('http://127.0.0.1:4175/', { waitUntil: 'networkidle' })
  await missingPage.addScriptTag({ content: axeSource })
  const missingState = await missingPage.evaluate(() => {
    const text = document.body.innerText
    return {
      decision: text.includes('关键决策数据未连接，本卡暂停生成新结论'),
      market: text.includes('市场数据暂不可用'),
      risk: text.includes('系统性风险暂不可用'),
      mainline: text.includes('主线数据不足'),
      zeroFilledRisk: text.includes('0 / 5 级'),
    }
  })
  const missingAxe = await missingPage.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  report.push({
    scenario: 'missing-data',
    missingStateComplete: missingState.decision && missingState.market && missingState.risk && missingState.mainline && !missingState.zeroFilledRisk,
    missingState,
    pageErrors: missingPageErrors,
    violations: missingAxe.violations.map((item) => ({
      id: item.id,
      impact: item.impact,
      help: item.help,
      nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })),
    })),
  })
  await missingPage.close()
} finally {
  await browser.close()
  await server.close()
}

await writeFile(resolve(outputDir, 'report.json'), JSON.stringify(report, null, 2), 'utf8')
const failed = report.some((item) =>
  (item.layout?.overflow || 0) > 0
  || item.layout?.mainContentPresent === false
  || item.layout?.configuredBrandVisible === false
  || item.layout?.simulationNavigationPresent === false
  || (item.layout && item.layout.mainContentHeight < 500)
  || item.layout?.heatmapFallbackVisible === false
  || (item.scrollResetOnNavigation && Object.values(item.scrollResetOnNavigation).some((value) => value !== true))
  || (item.layout?.heatmapLabelHierarchy && (
    item.layout.heatmapLabelHierarchy.largeNames < 1
    || item.layout.heatmapLabelHierarchy.compactNames < 1
    || item.layout.heatmapLabelHierarchy.largeValues !== item.layout.heatmapLabelHierarchy.largeNames
    || item.layout.heatmapLabelHierarchy.promotedNarrowTiles !== true
  ))
  || (item.layout?.undersizedTargets?.length || 0) > 0
  || (item.mobileNavigation && Object.values(item.mobileNavigation).some((value) => value !== true))
  || item.missingStateComplete === false
  || item.loadingStateComplete === false
  || item.reviewEmptyStateComplete === false
  || item.reviewPartialStateComplete === false
  || item.tomorrowLoadingStateComplete === false
  || item.tomorrowEmptyStateComplete === false
  || item.tomorrowPartialStateComplete === false
  || item.hotboardLoadingStateComplete === false
  || item.hotboardEmptyStateComplete === false
  || item.hotboardFailureStateComplete === false
  || item.timingLoadingStateComplete === false
  || item.timingEmptyStateComplete === false
  || item.timingFailureStateComplete === false
  || item.mainlineLoadingStateComplete === false
  || item.mainlineEmptyStateComplete === false
  || item.mainlineFailureStateComplete === false
  || item.capitalLoadingStateComplete === false
  || item.capitalEmptyStateComplete === false
  || item.capitalFailureStateComplete === false
  || item.sentimentLoadingStateComplete === false
  || item.sentimentEmptyStateComplete === false
  || item.sentimentFailureStateComplete === false
  || item.holdingsLoadingStateComplete === false
  || item.holdingsEmptyStateComplete === false
  || item.holdingsFailureStateComplete === false
  || item.watchlistLoadingStateComplete === false
  || item.watchlistEmptyStateComplete === false
  || item.watchlistImportStateComplete === false
  || item.watchlistModalStateComplete === false
  || item.watchlistFailureStateComplete === false
  || item.diagnosisLoadingStateComplete === false
  || item.diagnosisEmptyStateComplete === false
  || item.diagnosisFailureStateComplete === false
  || item.diagnosisEvidenceStateComplete === false
  || item.stockAskLoadingStateComplete === false
  || item.stockAskEmptyStateComplete === false
  || item.stockAskFailureStateComplete === false
  || item.screenerLoadingStateComplete === false
  || item.screenerEmptyStateComplete === false
  || item.screenerFailureStateComplete === false
  || item.technicalScreenerLoadingStateComplete === false
  || item.technicalScreenerEmptyStateComplete === false
  || item.technicalScreenerFailureStateComplete === false
  || item.forecastLoadingStateComplete === false
  || item.forecastEmptyStateComplete === false
  || item.forecastFailureStateComplete === false
  || item.industryChainCompleteStateComplete === false
  || item.industryChainLoadingStateComplete === false
  || item.industryChainEmptyStateComplete === false
  || item.industryChainFailureStateComplete === false
  || item.companyResearchCompleteStateComplete === false
  || item.companyResearchLoadingStateComplete === false
  || item.companyResearchEmptyStateComplete === false
  || item.companyResearchFailureStateComplete === false
  || item.reportsLoadingStateComplete === false
  || item.reportsEmptyStateComplete === false
  || item.reportsFailureStateComplete === false
  || item.knowledgeLoadingStateComplete === false
  || item.knowledgeEmptyStateComplete === false
  || item.knowledgeFailureStateComplete === false
  || item.settingsLoadingStateComplete === false
  || item.settingsEmptyStateComplete === false
  || item.settingsFailureStateComplete === false
  || (item.pageErrors?.length || 0) > 0
  || item.violations.length > 0
)
await writeFile(resolve(outputDir, 'audit-report.json'), JSON.stringify({ failed, report }, null, 2), 'utf8')
console.log(JSON.stringify(report, null, 2))
if (failed) process.exitCode = 1
