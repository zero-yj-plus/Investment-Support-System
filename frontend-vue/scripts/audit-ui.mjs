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

const fixtures = {
  '/api/shell-status': {
    ok: true,
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
    items: [
      { code: '601899', name: '紫金矿业', grade: 'A', status: '进入超短区间', target_low: 18.42, target_high: 18.76, quote: { price: 18.68, change_pct: 2.31 } },
      { code: '300750', name: '宁德时代', grade: 'B', status: '接近区间上沿', target_low: 282.10, target_high: 286.80, quote: { price: 287.22, change_pct: 1.52 } },
      { code: '002594', name: '比亚迪', grade: 'C', status: '仍在区间上方', target_low: 108.20, target_high: 111.60, quote: { price: 113.05, change_pct: -1.17 } },
      { code: '300308', name: '中际旭创', grade: 'D', status: '等待结构修复', target_low: null, target_high: null, quote: { price: 162.30, change_pct: -2.04 } },
    ],
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
  { name: 'timing', hash: '#/today?tab=timing', titleSelector: '#timing-page-title' },
  { name: 'premarket', hash: '#/premarket?tab=report', titleSelector: '#premarket-page-title' },
  { name: 'tail', hash: '#/tail?tab=decision', titleSelector: '#tail-page-title' },
  { name: 'review', hash: '#/review?tab=close', titleSelector: '#close-review-page-title' },
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
    report.push({ viewport, layout, mobileNavigation, pageErrors, violations })

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
  || (item.layout && item.layout.mainContentHeight < 500)
  || item.layout?.heatmapFallbackVisible === false
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
  || item.hotboardLoadingStateComplete === false
  || item.hotboardEmptyStateComplete === false
  || item.hotboardFailureStateComplete === false
  || item.timingLoadingStateComplete === false
  || item.timingEmptyStateComplete === false
  || item.timingFailureStateComplete === false
  || (item.pageErrors?.length || 0) > 0
  || item.violations.length > 0
)
console.log(JSON.stringify(report, null, 2))
if (failed) process.exitCode = 1
