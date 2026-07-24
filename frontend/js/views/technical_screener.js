/* 技术面选股：全市场本地信号缓存 + 面向普通用户的自然语言规则编辑器。 */
import { api, fmtMoney, fmtPct, fmtPrice, pctClass } from '../api.js';
import { escapeHtml, skeleton, toast } from '../ui.js';

let root = null;
let catalog = null;
let cacheStatus = null;
let schemes = [];
let results = null;
let loading = false;
let selectedSchemeId = '';
let selectedPresetId = 'strong_macd';
let schemeNameDraft = '';
let undoRemoval = null;
let requestController = null;
let previewTimer = null;
let statusTimer = null;
let generation = 0;
let initialized = false;
let legacyReadOnlyPayload = null;
let universeState = { markets: ['MAIN', 'CHINEXT', 'STAR'], exclude_st: true, exclude_suspended: true, min_listing_days: 120, min_amount_ma20: 50_000_000 };
let defaultTimeframe = 'D';
let sortState = { field: 'signal_score', direction: 'desc' };
let libraryState = { open: false, target: '', category: '全部', filter: 'all', query: '', selected: [], removeEmptyOnCancel: false };
let ruleConfig = emptyRuleConfig();

const COMMON_SIGNALS = new Set([
  'MACD_GOLDEN_CROSS', 'LONG_MA_ARRAY', 'PRICE_BREAKOUT_N', 'RSI_OVERSOLD_RECOVERY',
  'VOLUME_BREAKOUT', 'BOLL_UPPER_BREAK', 'WIN_MARKET_N', 'SUPERTREND_BULLISH_FLIP',
]);
const PARAM_LABELS = {
  period: '周期', volume_period: '均量周期', multiplier: '倍数', threshold: '阈值',
  fast: '快线', slow: '慢线', signal: '信号线', days: '天数', range_pct: '振幅%',
  body_pct: '实体%', change_pct: '涨幅%', min_history: '最少历史', ratio: '比例',
  adx_threshold: 'ADX阈值', rsi_period: 'RSI周期', stoch_period: '随机周期',
  smooth_k: 'K平滑', smooth_d: 'D平滑', trix_period: 'TRIX周期', signal_period: '信号周期',
  short_period: '短周期', long_period: '长周期', channel_period: '通道周期',
  average_period: '平均周期', smooth_period: '平滑周期', stddev: '标准差倍数',
  step: '步长', maximum: '最大步长',
};

const MARKET_CODES = [
  ['MAIN', '主板'], ['CHINEXT', '创业板'], ['STAR', '科创板'], ['BSE', '北交所'],
];
const presets = [
  preset('strong_macd', '强势MACD金叉', '日线MACD今日金叉、价格位于MA60上方且ADX不低于20。', 'ALL', [
    cond('MACD_GOLDEN_CROSS'), cond('CLOSE_ABOVE_MA', { occurrence: 'CURRENT', params: { period: 60 } }), cond('ADX_STRONG', { occurrence: 'CURRENT', params: { threshold: 20 } }),
  ]),
  preset('ma_start', '均线多头启动', '刚形成均线多头排列，且成交量不低于20日均量。', 'ALL', [cond('LONG_MA_ARRAY_START'), cond('VOLUME_AT_LEAST_AVERAGE', { occurrence: 'CURRENT' })]),
  preset('volume_breakout', '放量突破', '突破此前20日高点，成交量达到20日均量1.5倍。', 'ALL', [cond('VOLUME_BREAKOUT', { params: { period: 20, volume_period: 20, multiplier: 1.5 } })]),
  preset('oversold', '超卖修复', 'RSI向上穿越30，并重新站上MA5。', 'ALL', [cond('RSI_OVERSOLD_RECOVERY'), cond('CLOSE_ABOVE_MA', { occurrence: 'CURRENT', params: { period: 5 } })]),
  preset('weekly_turn', '周线趋势转强', '确认完成的周线出现MACD金叉或Supertrend转多。', 'ANY', [cond('MACD_GOLDEN_CROSS', { timeframe: 'W' }), cond('SUPERTREND_BULLISH_FLIP', { timeframe: 'W' })]),
  preset('resonance', '日周共振', '日线MACD刚转强，同时周线DIF仍位于DEA上方。', 'ALL', [cond('MACD_GOLDEN_CROSS'), cond('MACD_BULLISH_STATE', { timeframe: 'W', occurrence: 'CURRENT' })]),
];

function cond(signal_code, extra = {}) {
  return { id: ruleId('c'), signal_code, timeframe: 'D', occurrence: 'TODAY', params: {}, ...extra };
}

function clone(value) { return JSON.parse(JSON.stringify(value)); }
function ruleId(prefix) { return `${prefix}_${globalThis.crypto?.randomUUID?.() || `${Date.now()}_${Math.random().toString(16).slice(2)}`}`; }
function makeGroup(kind, conditionMatch = kind === 'exclude' ? 'ANY' : 'ALL', conditions = []) {
  return { id: ruleId(kind === 'exclude' ? 'eg' : 'sg'), conditionMatch, conditions: clone(conditions).map((item) => ({ ...item, id: item.id || ruleId('c') })) };
}
function emptyRuleConfig() { return { schemaVersion: 2, satisfy: { groupMatch: 'ALL', groups: [] }, exclude: { groups: [] } }; }
function regenerateRuleIds(config) {
  const next = clone(config);
  ['satisfy', 'exclude'].forEach((kind) => (next[kind]?.groups || []).forEach((group) => {
    group.id = ruleId(kind === 'exclude' ? 'eg' : 'sg');
    (group.conditions || []).forEach((condition) => { condition.id = ruleId('c'); });
  }));
  return next;
}
function preset(id, name, summary, conditionMatch, conditions) {
  return { id, name, summary, config: { schemaVersion: 2, satisfy: { groupMatch: 'ALL', groups: [makeGroup('satisfy', conditionMatch, conditions)] }, exclude: { groups: [] } } };
}
function definition(code) { return catalog?.signals?.find((item) => item.signal_code === code); }
function listAt(path) {
  const [kind, groupIndex] = String(path).split('.');
  return ruleConfig[kind]?.groups?.[Number(groupIndex)]?.conditions || [];
}
function itemAt(path) {
  const parts = String(path).split('.'); const index = Number(parts.pop());
  return listAt(parts.join('.'))?.[index];
}
function allConditions(kind) { return (ruleConfig[kind]?.groups || []).flatMap((group) => group.conditions || []); }

function selectPreset(id, { render = true } = {}) {
  const item = presets.find((row) => row.id === id) || presets[0];
  selectedPresetId = item.id;
  selectedSchemeId = '';
  schemeNameDraft = item.name;
  undoRemoval = null;
  legacyReadOnlyPayload = null;
  ruleConfig = clone(item.config);
  if (render) { renderPage({ capture: false }); schedulePreview(); }
}

function currentUniverse() {
  if (!root?.querySelector('#ts-listing-days')) return clone(universeState);
  universeState = {
    markets: Array.from(root.querySelectorAll('[data-market]:checked')).map((input) => input.value),
    exclude_st: !!root.querySelector('#ts-ex-st')?.checked,
    exclude_suspended: !!root.querySelector('#ts-ex-paused')?.checked,
    min_listing_days: Number(root.querySelector('#ts-listing-days')?.value || 0),
    min_amount_ma20: Number(root.querySelector('#ts-min-amount')?.value || 0) * 10_000,
  };
  return clone(universeState);
}

function captureControls() {
  if (!root?.querySelector('#ts-listing-days')) return;
  schemeNameDraft = root.querySelector('#ts-scheme-name')?.value ?? schemeNameDraft;
  currentUniverse();
  defaultTimeframe = root.querySelector('#ts-default-timeframe')?.value || defaultTimeframe;
  sortState = {
    field: root.querySelector('#ts-sort')?.value || sortState.field,
    direction: root.querySelector('#ts-direction')?.value || sortState.direction,
  };
}

function backendSupportsRuleV2() { return Number(cacheStatus?.config_schema_version || 0) >= 2; }

function legacyRulePayload(config) {
  const node = (group) => ({
    operator: group.conditionMatch === 'ANY' ? 'OR' : 'AND',
    conditions: clone(group.conditions || []).map(({ id: _id, ...condition }) => condition),
  });
  const satisfyGroups = config.satisfy?.groups || [];
  return {
    must_all: config.satisfy?.groupMatch === 'ANY' ? [] : satisfyGroups.map(node),
    any_of: config.satisfy?.groupMatch === 'ANY' ? satisfyGroups.map(node) : [],
    exclude: (config.exclude?.groups || []).map(node),
  };
}

function requestPayload({ limit = 50, config = ruleConfig, forSave = false } = {}) {
  if (legacyReadOnlyPayload) return { ...clone(legacyReadOnlyPayload), limit };
  const rules = backendSupportsRuleV2() ? clone(config) : legacyRulePayload(config);
  if (forSave && !backendSupportsRuleV2()) rules.editor_config = clone(config);
  return {
    universe: currentUniverse(), default_timeframe: root?.querySelector('#ts-default-timeframe')?.value || defaultTimeframe,
    ...rules,
    sort: [{ field: root?.querySelector('#ts-sort')?.value || sortState.field, direction: root?.querySelector('#ts-direction')?.value || sortState.direction }],
    algorithm_ver: catalog?.algorithm_ver || 'local-signal-v1', adjustment: 'qfq', benchmark: '000300.SH', limit,
  };
}

function apiErrorMessage(error, fallback) {
  const messages = (error?.data?.errors || []).map((row) => row?.message).filter(Boolean);
  return messages.length ? messages.join('；') : (error?.data?.error || error?.message || fallback);
}

function paramsText(condition) {
  const entries = Object.entries(condition.params || {});
  return entries.length ? entries.map(([key, value]) => `${PARAM_LABELS[key] || key} ${value}`).join(' · ') : '无可调参数';
}

function conditionLabel(condition) {
  const def = definition(condition.signal_code);
  const tf = { D: '日', W: '周', M: '月' }[condition.timeframe] || condition.timeframe;
  return `${tf}·${def?.signal_name || condition.signal_code}`;
}

function statusHtml() {
  const state = cacheStatus?.state || 'missing';
  const labels = { ready: '数据可用', partial: '部分覆盖', running: '正在刷新', queued: '等待刷新', missing: '尚无缓存' };
  const cls = state === 'ready' ? 'ok' : state === 'missing' ? 'bad' : 'warn';
  const progress = state === 'running' ? ` · ${cacheStatus?.refresh?.processed || 0}/${cacheStatus?.refresh?.total || '—'}` : '';
  return `<span class="ts-status ${cls}" role="status"><span aria-hidden="true" class="ts-dot"></span>${labels[state] || state}${progress}</span>
    <span class="ts-data-date">数据日 ${escapeHtml(cacheStatus?.data_date || '—')} · 前复权 · ${escapeHtml(cacheStatus?.algorithm_ver || 'local-signal-v1')}</span>`;
}

function universeHtml(readOnly = false) {
  return `<section class="ts-block" aria-labelledby="ts-universe-title"><div class="ts-block-head"><h3 id="ts-universe-title">股票池</h3><span>先限定范围，再计算技术条件</span></div>
    <div class="ts-checks">${MARKET_CODES.map(([code, name]) => `<label><input type="checkbox" data-market value="${code}" ${universeState.markets.includes(code) ? 'checked' : ''} ${readOnly ? 'disabled' : ''}>${name}</label>`).join('')}
      <span class="ts-sep" aria-hidden="true"></span><label><input id="ts-ex-st" type="checkbox" ${universeState.exclude_st ? 'checked' : ''} ${readOnly ? 'disabled' : ''}>排除ST</label><label><input id="ts-ex-paused" type="checkbox" ${universeState.exclude_suspended ? 'checked' : ''} ${readOnly ? 'disabled' : ''}>排除停牌</label></div>
    <div class="ts-universe-fields"><label>排除上市不足 <input id="ts-listing-days" class="input" type="number" min="0" max="1000" value="${universeState.min_listing_days}" ${readOnly ? 'disabled' : ''}> 日</label>
      <label>20日均成交额不低于 <input id="ts-min-amount" class="input" type="number" min="0" value="${universeState.min_amount_ma20 / 10000}" ${readOnly ? 'disabled' : ''}> 万元</label></div></section>`;
}

function targetLabel(path) {
  const [kind, groupIndex] = String(path).split('.');
  return `${kind === 'exclude' ? '排除规则' : '满足规则'} ${Number(groupIndex) + 1}`;
}

function signalPickerHtml(group) {
  const count = listAt(group).length;
  return `<div class="ts-picker-actions"><button type="button" class="btn btn-ghost ts-open-library" data-open-library="${group}" aria-haspopup="dialog" aria-expanded="${libraryState.open && libraryState.target === group}">添加技术信号</button><span>${count ? `当前 ${count} 个条件` : '可从完整信号库中多选'}</span></div>`;
}

function signalDescription(item) {
  if (!item.available) return item.unavailable_reason || '数据源不可用';
  return item.signal_kind === 'state' ? `当前周期持续满足“${item.signal_name}”` : `当前周期首次出现“${item.signal_name}”`;
}

function librarySignals() {
  const query = String(libraryState.query || '').trim().toLowerCase();
  return (catalog?.signals || []).filter((item) => {
    if (libraryState.category !== '全部' && item.category !== libraryState.category) return false;
    if (libraryState.filter === 'common' && !COMMON_SIGNALS.has(item.signal_code)) return false;
    if (libraryState.filter === 'selected' && !libraryState.selected.includes(item.signal_code)) return false;
    if (libraryState.filter === 'available' && !item.available) return false;
    const hay = [item.signal_code, item.signal_name, item.category, ...(item.aliases || [])].join(' ').toLowerCase();
    return !query || hay.includes(query);
  });
}

function signalLibraryHtml() {
  if (!libraryState.open) return '';
  const categories = ['全部', ...new Set((catalog?.signals || []).map((item) => item.category))];
  const rows = librarySignals();
  const selectedCount = libraryState.selected.length;
  return `<div class="ts-library-backdrop" data-library-dismiss><section class="ts-library-drawer" role="dialog" aria-modal="true" aria-labelledby="ts-library-title" tabindex="-1">
    <header class="ts-library-head"><div><h3 id="ts-library-title">技术信号库</h3><p>加入到：${escapeHtml(targetLabel(libraryState.target))}</p></div><button type="button" class="ts-library-close" data-library-close aria-label="关闭技术信号库">关闭</button></header>
    <div class="ts-library-tools"><label for="ts-library-search">搜索信号</label><input id="ts-library-search" class="input" data-library-search value="${escapeHtml(libraryState.query)}" placeholder="中文名、指标名、编码或别名" autocomplete="off">
      <div class="ts-library-filters" role="group" aria-label="信号过滤"><button type="button" data-library-filter="all" aria-pressed="${libraryState.filter === 'all'}">全部</button><button type="button" data-library-filter="common" aria-pressed="${libraryState.filter === 'common'}">常用</button><button type="button" data-library-filter="selected" aria-pressed="${libraryState.filter === 'selected'}">已选 ${selectedCount}</button><button type="button" data-library-filter="available" aria-pressed="${libraryState.filter === 'available'}">仅看可用</button></div></div>
    <div class="ts-library-main"><nav class="ts-library-categories" aria-label="技术信号分类">${categories.map((category) => {
      const count = category === '全部' ? (catalog?.signals || []).length : (catalog?.signals || []).filter((item) => item.category === category).length;
      return `<button type="button" data-library-category="${escapeHtml(category)}" aria-pressed="${libraryState.category === category}"><span>${escapeHtml(category)}</span><em>${count}</em></button>`;
    }).join('')}</nav><div class="ts-library-list" role="group" aria-label="可选技术信号">${rows.map((item) => {
      const checked = libraryState.selected.includes(item.signal_code);
      return `<label class="ts-library-row ${item.available ? '' : 'unavailable'}"><input type="checkbox" data-library-signal="${escapeHtml(item.signal_code)}" ${checked ? 'checked' : ''} ${item.available ? '' : 'disabled'}><span class="ts-library-copy"><span><b>${escapeHtml(item.signal_name)}</b><i class="ts-kind">${item.signal_kind === 'state' ? '状态型' : '事件型'}</i>${item.direction === 'bearish' ? '<i class="ts-bearish">偏空</i>' : ''}</span><small>${escapeHtml(signalDescription(item))}</small><em>${escapeHtml(item.signal_code)} · ${escapeHtml(paramsText({ params: item.default_params || {} }))}</em></span>${item.available ? '' : '<strong>数据源不可用</strong>'}</label>`;
    }).join('') || '<div class="ts-library-empty"><b>没有符合当前过滤条件的信号</b><span>可切换分类、清除搜索词或取消“仅看可用”。</span></div>'}</div></div>
    <footer class="ts-library-foot"><div aria-live="polite"><b>已选择 ${selectedCount} 项</b><button type="button" data-library-clear ${selectedCount ? '' : 'disabled'}>清空选择</button></div><div><button type="button" class="btn btn-ghost" data-library-close>取消</button><button type="button" class="btn btn-primary" data-library-commit ${selectedCount ? '' : 'disabled'}>加入当前条件组（${selectedCount}）</button></div></footer>
  </section></div>`;
}

function renderSignalLibraryPortal() {
  let host = document.getElementById('ts-library-portal');
  if (!libraryState.open) { host?.remove(); return; }
  if (!host) {
    host = document.createElement('div');
    host.id = 'ts-library-portal';
    (document.getElementById('modal-root') || document.body).appendChild(host);
  }
  host.innerHTML = signalLibraryHtml();
}

function libraryPortal() { return document.getElementById('ts-library-portal'); }

function paramFieldHtml(condition, key, value) {
  return `<label>${escapeHtml(PARAM_LABELS[key] || key)}<input class="input" data-param="${escapeHtml(key)}" type="number" step="any" value="${escapeHtml(value)}" aria-label="${escapeHtml(PARAM_LABELS[key] || key)}"></label>`;
}

function conditionCardHtml(group, condition, index, readOnly = false) {
  const def = definition(condition.signal_code) || {};
  const params = { ...(def.default_params || {}), ...(condition.params || {}) };
  const occurrence = def.signal_kind === 'state' ? 'CURRENT' : (condition.occurrence || 'TODAY');
  const path = `${group}.${index}`;
  return `<article class="ts-condition ${def.direction === 'bearish' ? 'bearish' : ''}" data-condition="${path}">
    <div class="ts-condition-summary"><span class="ts-kind">${def.signal_kind === 'state' ? '状态' : '事件'}</span><b>${escapeHtml(conditionLabel(condition))}</b><span>${escapeHtml(paramsText({ params }))}</span>
      ${readOnly ? '' : `<button type="button" class="ts-remove" data-remove="${path}" aria-label="删除${escapeHtml(def.signal_name || condition.signal_code)}条件">删除</button>`}</div>
    <div class="ts-condition-edit"><label>周期<select class="select" data-field="timeframe" ${readOnly ? 'disabled' : ''}><option value="D" ${condition.timeframe === 'D' ? 'selected' : ''}>日线</option><option value="W" ${condition.timeframe === 'W' ? 'selected' : ''}>周线</option><option value="M" ${condition.timeframe === 'M' ? 'selected' : ''}>月线</option></select></label>
      <label>时间<select class="select" data-field="occurrence" ${readOnly || def.signal_kind === 'state' ? 'disabled' : ''}><option value="TODAY" ${occurrence === 'TODAY' ? 'selected' : ''}>今日刚发生</option><option value="RECENT" ${occurrence === 'RECENT' ? 'selected' : ''}>近N周期发生</option><option value="CURRENT" ${occurrence === 'CURRENT' ? 'selected' : ''}>当前成立</option></select></label>
      ${occurrence === 'RECENT' ? `<label>近<input class="input" data-field="recent_periods" type="number" min="1" max="60" value="${Number(condition.recent_periods || 5)}" ${readOnly ? 'disabled' : ''}>周期</label>` : ''}
      ${condition.timeframe !== 'D' ? `<label class="ts-partial"><input type="checkbox" data-field="include_partial_period" ${condition.include_partial_period ? 'checked' : ''} ${readOnly ? 'disabled' : ''}>使用未完成周期（未确认）</label>` : ''}
      ${Object.entries(params).map(([key, value]) => paramFieldHtml(condition, key, value).replace('<input ', `<input ${readOnly ? 'disabled ' : ''}`)).join('')}
      ${readOnly ? '' : `<button type="button" class="btn btn-ghost btn-sm" data-defaults="${path}">恢复默认参数</button>`}</div>${condition.include_partial_period ? '<span class="ts-unconfirmed">未确认：周期结束前可能消失</span>' : ''}</article>`;
}

function matchChoiceHtml(name, value, options, dataAttribute, readOnly) {
  return `<fieldset class="ts-match"><legend>${escapeHtml(name)}</legend><div class="ts-segmented">${options.map(([code, label]) => `<label><input type="radio" name="${escapeHtml(dataAttribute)}" value="${code}" data-${dataAttribute} ${value === code ? 'checked' : ''} ${readOnly ? 'disabled' : ''}><span>${escapeHtml(label)}</span></label>`).join('')}</div></fieldset>`;
}

function groupError(kind, index) {
  const group = ruleConfig[kind]?.groups?.[index];
  return group && !(group.conditions || []).length ? '请至少添加一个技术信号；空规则组不会保存或执行。' : '';
}

function ruleGroupHtml(kind, group, index, readOnly) {
  const path = `${kind}.${index}`;
  const isExclude = kind === 'exclude';
  const error = groupError(kind, index);
  return `${index ? `<div class="ts-connector" aria-label="与上一组的关系">${isExclude ? '或者命中' : (ruleConfig.satisfy.groupMatch === 'ANY' ? '或者' : '并且')}</div>` : ''}<article class="ts-rule-card ${error ? 'invalid' : ''}" data-rule-group="${path}" aria-labelledby="ts-${kind}-group-${index}">
    <div class="ts-rule-head"><div><span>${isExclude ? '排除规则' : '满足规则'} ${index + 1}</span><b id="ts-${kind}-group-${index}">${isExclude ? '出现以下情况时剔除' : '股票需要满足本组条件'}</b></div>${readOnly ? '<em>只读</em>' : `<button type="button" class="ts-remove" data-remove-rule-group="${path}" aria-label="删除${isExclude ? '排除' : '满足'}规则 ${index + 1}">删除本组</button>`}</div>
    ${matchChoiceHtml(isExclude ? '出现以下' : '本组条件', group.conditionMatch || (isExclude ? 'ANY' : 'ALL'), isExclude ? [['ANY', '任意一项出现时排除'], ['ALL', '全部条件同时出现时排除']] : [['ALL', '全部成立'], ['ANY', '任意一项成立']], `condition-match-${kind}-${index}`, readOnly).replaceAll(`data-condition-match-${kind}-${index}`, `data-condition-match="${path}"`)}
    ${readOnly ? '' : signalPickerHtml(path)}
    <div class="ts-condition-list">${(group.conditions || []).map((row, childIndex) => conditionCardHtml(path, row, childIndex, readOnly)).join('') || '<p class="ts-empty-group">尚未添加技术信号</p>'}</div>
    ${error ? `<p class="ts-inline-error" role="alert">${error}</p>` : ''}
  </article>`;
}

function naturalLanguagePreview() {
  const satisfyGroups = ruleConfig.satisfy.groups || [];
  const excludeGroups = ruleConfig.exclude.groups || [];
  const phrase = (group) => {
    const separator = group.conditionMatch === 'ANY' ? '，或者' : '，并且';
    const labels = (group.conditions || []).map((condition) => conditionLabel(condition));
    return labels.length ? labels.map((label) => escapeHtml(label)).join(separator) : '<span class="ts-preview-empty">尚未添加技术信号</span>';
  };
  const satisfyIntro = satisfyGroups.length > 1
    ? (ruleConfig.satisfy.groupMatch === 'ANY' ? '股票满足以下任意一个规则组即可：' : '股票需要同时满足以下规则组：')
    : '股票需要满足：';
  const satisfyList = satisfyGroups.length
    ? `<ol>${satisfyGroups.map((group) => `<li>${phrase(group)}。</li>`).join('')}</ol>`
    : '<p class="ts-preview-empty">尚未添加满足规则。</p>';
  const excludeList = excludeGroups.length
    ? `<div class="ts-preview-exclude"><b>出现以下情况时排除：</b><ol>${excludeGroups.map((group) => `<li>${phrase(group)}。</li>`).join('')}</ol>${excludeGroups.length > 1 ? '<p>命中以上任意一个排除组即剔除。</p>' : ''}</div>`
    : '<p class="ts-preview-muted">当前没有排除规则。</p>';
  return `<div class="ts-natural-preview" id="ts-natural-preview" aria-live="polite"><b>${satisfyIntro}</b>${satisfyList}${excludeList}</div>`;
}

function legacyNaturalPreview(payload) {
  const nodePhrase = (node) => {
    if (!Array.isArray(node?.conditions)) return escapeHtml(conditionLabel(node || {}));
    const separator = String(node.operator || '').toUpperCase() === 'OR' ? '，或者' : '，并且';
    return `（${node.conditions.map(nodePhrase).join(separator)}）`;
  };
  const must = (payload.must_all || []).map(nodePhrase);
  const any = (payload.any_of || []).map(nodePhrase);
  const excluded = (payload.exclude || []).map(nodePhrase);
  return `<div class="ts-natural-preview ts-legacy-preview"><b>原方案筛选含义</b>${must.length ? `<p>股票需要满足：${must.join('，并且')}。</p>` : ''}${any.length ? `<p>同时还需满足以下任意一项：${any.join('，或者')}。</p>` : ''}${excluded.length ? `<p>如果出现以下任意一项则排除：${excluded.join('，或者')}。</p>` : ''}</div>`;
}

function rulesEditorHtml() {
  if (legacyReadOnlyPayload) {
    return `<section class="ts-rule-section ts-legacy-readonly" role="status"><div class="ts-section-head"><div><h3>旧方案（只读）</h3><p>该方案包含固定两层界面无法无损表达的组合，已保留原筛选逻辑。</p></div><span>不可直接编辑</span></div><p>${escapeHtml(legacyReadOnlyPayload.migration_reason || '请创建重建草稿后，使用“满足规则”和“排除规则”重新组织。')}</p>${legacyNaturalPreview(legacyReadOnlyPayload)}</section>`;
  }
  const readOnly = !!selectedPresetId;
  const satisfyGroups = ruleConfig.satisfy.groups || [];
  const excludeGroups = ruleConfig.exclude.groups || [];
  return `<div class="ts-rule-editor"><section class="ts-rule-section" aria-labelledby="ts-satisfy-title"><div class="ts-section-head"><div><h3 id="ts-satisfy-title">满足规则</h3><p>决定股票需要满足什么条件</p></div>${readOnly ? '<span>预置方案 · 只读</span>' : ''}</div>
      ${satisfyGroups.length > 1 ? matchChoiceHtml('股票需要', ruleConfig.satisfy.groupMatch, [['ALL', '所有规则组都成立'], ['ANY', '任意规则组成立']], 'group-match', readOnly) : ''}
      <div class="ts-rule-list">${satisfyGroups.map((group, index) => ruleGroupHtml('satisfy', group, index, readOnly)).join('')}</div>
      ${readOnly ? '' : '<button type="button" class="btn btn-ghost ts-add-rule" data-add-rule-group="satisfy">添加一组满足规则</button>'}
    </section>
    <section class="ts-rule-section" aria-labelledby="ts-exclude-title"><div class="ts-section-head"><div><h3 id="ts-exclude-title">排除规则</h3><p>命中任意一个排除组，股票就会被剔除</p></div></div>
      <div class="ts-rule-list">${excludeGroups.map((group, index) => ruleGroupHtml('exclude', group, index, readOnly)).join('')}</div>
      ${readOnly ? '' : '<button type="button" class="btn btn-ghost ts-add-rule" data-add-rule-group="exclude">添加一组排除规则</button>'}
    </section>${naturalLanguagePreview()}</div>`;
}

function conflicts() {
  if (legacyReadOnlyPayload) return [];
  const errors = [];
  if (!(ruleConfig.satisfy.groups || []).some((group) => group.conditions?.length)) errors.push('请至少添加一组满足规则');
  ['satisfy', 'exclude'].forEach((kind) => (ruleConfig[kind].groups || []).forEach((group, index) => {
    if (!group.conditions?.length) errors.push(`${kind === 'satisfy' ? '满足' : '排除'}规则 ${index + 1} 还没有技术信号`);
  }));
  const all = allConditions('satisfy').map((c) => `${c.signal_code}:${c.timeframe}`);
  const excluded = allConditions('exclude').map((c) => `${c.signal_code}:${c.timeframe}`);
  all.filter((key) => excluded.includes(key)).forEach((key) => errors.push(`同一条件同时要求并排除：${key.split(':')[0]}`));
  [['MACD_GOLDEN_CROSS', 'MACD_DEAD_CROSS'], ['LONG_MA_ARRAY', 'SHORT_MA_ARRAY']].forEach(([a, b]) => {
    if (all.some((key) => key.startsWith(a)) && all.some((key) => key.startsWith(b))) errors.push(`满足规则中存在方向相反的条件：${a} / ${b}`);
  });
  return errors;
}

function schemeOptionsHtml() {
  const presetRows = presets.map((row) => `<button type="button" role="option" data-scheme-option="${escapeHtml(row.id)}" data-scheme-kind="preset" data-scheme-search="${escapeHtml(`${row.name} ${row.summary}`.toLowerCase())}" aria-selected="${row.id === selectedPresetId}"><span><b>${escapeHtml(row.name)}</b><small>${escapeHtml(row.summary)}</small></span><em>预置只读</em></button>`).join('');
  const customRows = schemes.map((row) => `<button type="button" role="option" data-scheme-option="${escapeHtml(row.id)}" data-scheme-kind="custom" data-scheme-search="${escapeHtml(String(row.name || '').toLowerCase())}" aria-selected="${row.id === selectedSchemeId}"><span><b>${escapeHtml(row.name)}</b><small>${row.editable === false ? '旧方案 · 只读保留' : (row.migration_state === 'migrated' ? '旧方案 · 已无损转换' : '自定义方案')}</small></span></button>`).join('');
  return `<div id="ts-scheme-options" class="ts-scheme-options" role="listbox" aria-label="技术选股方案" hidden><div class="ts-scheme-option-group" data-scheme-group="preset">预置方案</div>${presetRows}${customRows ? `<div class="ts-scheme-option-group" data-scheme-group="custom">自定义方案</div>${customRows}` : ''}<div class="ts-scheme-no-results" hidden>没有匹配方案，可直接输入新名称后保存</div></div>`;
}

function filterPanelHtml() {
  const errors = conflicts();
  const hasSelection = !!selectedSchemeId || !!selectedPresetId;
  const readOnly = !!selectedPresetId;
  return `${undoRemoval ? `<div class="ts-undo" role="status">已删除 ${escapeHtml(undoRemoval.label || '规则')}。<button type="button" id="ts-undo">撤销</button></div>` : ''}<div class="ts-toolbar">
    <div class="ts-scheme-actions"><div class="ts-scheme-combo"><input id="ts-scheme-name" class="input" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="ts-scheme-options" maxlength="80" placeholder="选择或输入方案名称" aria-label="选择预置或自定义方案，也可输入新方案名称" autocomplete="off" value="${escapeHtml(schemeNameDraft)}"><button type="button" id="ts-scheme-toggle" aria-label="展开方案列表" aria-expanded="false"><span class="ts-combo-chevron" aria-hidden="true"></span></button>${schemeOptionsHtml()}</div><button type="button" class="btn btn-ghost btn-sm" id="ts-save" ${selectedPresetId || legacyReadOnlyPayload ? 'disabled' : ''} title="${selectedPresetId ? '预置方案不可直接修改，请先复制' : (legacyReadOnlyPayload ? '该旧方案无法无损编辑，请先创建重建草稿' : '')}">${selectedPresetId ? '预置方案' : (legacyReadOnlyPayload ? '旧方案只读' : (selectedSchemeId ? '更新方案' : '保存方案'))}</button><button type="button" class="btn btn-ghost btn-sm" id="ts-copy" ${hasSelection ? '' : 'disabled'}>${legacyReadOnlyPayload ? '创建重建草稿' : '复制方案'}</button><button type="button" class="btn btn-ghost btn-sm" id="ts-delete" ${selectedSchemeId && !selectedPresetId ? '' : 'disabled'} title="${selectedPresetId ? '预置方案不可删除' : ''}">删除方案</button></div></div>
    ${universeHtml(readOnly)}
    <section class="ts-period"><label for="ts-default-timeframe">新增条件默认周期</label><select id="ts-default-timeframe" class="select" ${readOnly ? 'disabled' : ''}><option value="D" ${defaultTimeframe === 'D' ? 'selected' : ''}>日线</option><option value="W" ${defaultTimeframe === 'W' ? 'selected' : ''}>周线</option><option value="M" ${defaultTimeframe === 'M' ? 'selected' : ''}>月线</option></select><span>默认仅使用已完成周期</span><details><summary>未完成周期说明</summary><p>未完成周线/月线信号可能在周期结束前消失。本页当前不把未确认信号加入正式结果。</p></details></section>
    ${rulesEditorHtml()}
    ${errors.length ? `<div class="ts-conflicts" role="alert"><b>发现${errors.length}处冲突，提交已暂停</b>${errors.map((text) => `<span>${escapeHtml(text)}</span>`).join('')}</div>` : ''}
    <div class="ts-submit"><div><label for="ts-sort">结果排序</label><select id="ts-sort" class="select" ${readOnly ? 'disabled' : ''}><option value="signal_score" ${sortState.field === 'signal_score' ? 'selected' : ''}>综合信号分</option><option value="change_pct" ${sortState.field === 'change_pct' ? 'selected' : ''}>涨跌幅</option><option value="last_amount" ${sortState.field === 'last_amount' ? 'selected' : ''}>成交额</option><option value="avg_amount20" ${sortState.field === 'avg_amount20' ? 'selected' : ''}>20日均成交额</option></select><select id="ts-direction" class="select" aria-label="排序方向" ${readOnly ? 'disabled' : ''}><option value="desc" ${sortState.direction === 'desc' ? 'selected' : ''}>从高到低</option><option value="asc" ${sortState.direction === 'asc' ? 'selected' : ''}>从低到高</option></select></div>
      <div class="ts-run-actions"><button type="button" class="btn btn-primary" id="ts-run" ${errors.length || loading ? 'disabled' : ''}>${loading ? '筛选中…' : '开始筛选'}</button></div></div>`;
}

function metricText(metrics) {
  const entries = Object.entries(metrics || {}).slice(0, 3);
  return entries.map(([key, value]) => `${key} ${Number.isFinite(Number(value)) ? Number(value).toFixed(2) : value}`).join('，');
}

function resultHtml() {
  if (loading) return `<div class="ts-loading" aria-live="polite"><b>正在读取本地技术信号缓存</b><span>自定义参数可能需要重新扫描本地K线，页面会保留现有条件。</span>${skeleton(7)}</div>`;
  if (!cacheStatus?.stocks) return `<div class="ts-empty"><b>尚未生成全市场技术信号缓存</b><span>普通筛选不会触发逐股网络请求。请执行一次后台刷新，完成后结果会自动可用。</span><button type="button" class="btn btn-primary" id="ts-refresh">刷新技术信号数据</button></div>`;
  if (!results) return `<div class="ts-empty"><b>等待筛选</b><span>选择模板或添加条件后，系统会预览符合数量；点击“开始筛选”查看完整命中原因。</span></div>`;
  if (results.ok === false) return `<div class="ts-empty"><b>筛选不可用</b><span>${escapeHtml(results.error || results.missing?.join('；') || '请检查条件或缓存状态')}</span></div>`;
  const stocks = results.stocks || [];
  if (!stocks.length) return `<div class="ts-empty"><b>没有股票同时满足当前条件</b><span>${results.strongest_condition ? `限制最强的条件是“${escapeHtml(results.strongest_condition)}”，可尝试放宽该条件。` : '请检查条件组合或扩大股票池。'}</span></div>`;
  return `<div class="ts-result-summary"><b>符合 ${Number(results.matched || 0).toLocaleString('zh-CN')} 只</b><span>展示前 ${stocks.length} 只 · 数据日 ${escapeHtml(results.data_date || '—')} · 所有数值来自本地前复权K线</span></div>
    <div class="ts-table-wrap" tabindex="0" aria-label="技术选股结果表，可横向滚动"><table class="tbl ts-table"><thead><tr><th>代码/名称</th><th>最新价</th><th>涨跌幅</th><th>成交额</th><th>行业</th><th>信号分</th><th>主要命中原因</th></tr></thead><tbody>${stocks.map((row) => `<tr><td><button class="ts-code" data-code="${escapeHtml(row.code)}">${escapeHtml(row.code)}</button><small>${escapeHtml(row.name || '—')}</small></td><td>${fmtPrice(row.last_price)}</td><td class="${pctClass(row.change_pct)}">${fmtPct(row.change_pct)}</td><td>${fmtMoney(row.last_amount)}</td><td>${escapeHtml(row.industry || '—')}</td><td><b>${Number(row.signal_score || 0)}</b></td><td><details><summary>${escapeHtml(row.matched_reasons?.[0]?.signal_name || '查看命中原因')}</summary><ul>${(row.matched_reasons || []).map((reason) => `<li><b>${escapeHtml(({ D: '日', W: '周', M: '月' })[reason.timeframe] || reason.timeframe)}·${escapeHtml(reason.signal_name)}${reason.is_confirmed === false ? ' · 未确认' : ''}</b><span>${escapeHtml(reason.date || '')}${reason.metrics && Object.keys(reason.metrics).length ? `：${escapeHtml(metricText(reason.metrics))}` : ''}</span></li>`).join('')}</ul></details></td></tr>`).join('')}</tbody></table></div>
    <div class="ts-mobile-results">${stocks.map((row) => `<article><div><button class="ts-code" data-code="${escapeHtml(row.code)}">${escapeHtml(row.code)} ${escapeHtml(row.name || '')}</button><b class="${pctClass(row.change_pct)}">${fmtPct(row.change_pct)}</b></div><span>${fmtPrice(row.last_price)} · 成交额 ${fmtMoney(row.last_amount)} · 信号分 ${row.signal_score || 0}</span><ul>${(row.matched_reasons || []).map((reason) => `<li>${escapeHtml(conditionLabel(reason))} · ${escapeHtml(metricText(reason.metrics))}</li>`).join('')}</ul></article>`).join('')}</div>`;
}

function historyHtml() {
  return `<section class="card ts-history"><div class="card-head"><h3 class="card-title">历史信号命中</h3><span>仅记录事件，不计算策略收益</span></div><div class="card-body"><div class="ts-history-form"><label for="ts-history-code">股票代码</label><input id="ts-history-code" class="input" inputmode="numeric" maxlength="6" placeholder="如 600519"><button type="button" class="btn btn-ghost" id="ts-history-run">查询历史命中</button></div><div id="ts-history-result" aria-live="polite"><span class="ts-muted">输入代码后查看最近事件。</span></div></div></section>`;
}

function renderPage({ capture = true } = {}) {
  if (!root) return;
  if (capture) captureControls();
  root.innerHTML = `<style>${styles()}</style><div id="technical-screener"><div class="page-head"><div><h2 class="page-title">技术面选股</h2><p class="page-sub">全市场本地技术信号 · 事件与状态分离 · 结果可解释</p></div><div class="ts-head-status">${statusHtml()}<button type="button" class="btn btn-ghost btn-sm" id="ts-refresh-head">刷新数据</button></div></div>
    <div class="ts-layout"><section class="card ts-filter"><div class="card-body">${filterPanelHtml()}</div></section><section class="card ts-results"><div class="card-head"><h3 class="card-title">筛选结果</h3><span id="ts-preview" aria-live="polite">${results ? `符合 ${results.matched || 0} 只` : '尚未筛选'}</span></div><div class="card-body" id="ts-result" aria-live="polite" aria-busy="${loading}">${resultHtml()}</div></section></div>${historyHtml()}</div>`;
  renderSignalLibraryPortal();
  bind();
}

function newCondition(code, timeframe) {
  const def = definition(code);
  return { id: ruleId('c'), signal_code: code, timeframe, occurrence: def?.signal_kind === 'state' ? 'CURRENT' : 'TODAY', params: clone(def?.default_params || {}) };
}

function normalizedParams(params) {
  return JSON.stringify(Object.entries(params || {}).sort(([a], [b]) => a.localeCompare(b)));
}

function duplicateCondition(group, condition) {
  return listAt(group).some((item) => !Array.isArray(item?.conditions)
    && item.signal_code === condition.signal_code
    && item.timeframe === condition.timeframe
    && normalizedParams(item.params) === normalizedParams(condition.params));
}

function openSignalLibrary(group, { removeEmptyOnCancel = false } = {}) {
  captureControls();
  if (libraryState.target !== group) libraryState = { ...libraryState, target: group, selected: [] };
  libraryState.open = true;
  libraryState.removeEmptyOnCancel = removeEmptyOnCancel;
  document.body.classList.add('ts-library-open');
  renderPage();
  queueMicrotask(() => libraryPortal()?.querySelector('#ts-library-search')?.focus());
}

function closeSignalLibrary() {
  const target = libraryState.target;
  if (libraryState.removeEmptyOnCancel && !listAt(target).length) {
    const [kind, index] = target.split('.');
    ruleConfig[kind]?.groups?.splice(Number(index), 1);
  }
  libraryState.open = false;
  libraryState.removeEmptyOnCancel = false;
  document.body.classList.remove('ts-library-open');
  renderPage();
  queueMicrotask(() => Array.from(root?.querySelectorAll('[data-open-library]') || []).find((button) => button.dataset.openLibrary === target)?.focus());
}

function renderLibraryAndFocus(selector, end = false) {
  renderPage();
  queueMicrotask(() => {
    const next = libraryPortal()?.querySelector(selector);
    next?.focus();
    if (end && next?.setSelectionRange) next.setSelectionRange(next.value.length, next.value.length);
  });
}

function commitLibrarySelection() {
  const timeframe = defaultTimeframe || 'D';
  let added = 0; let duplicates = 0;
  libraryState.selected.forEach((code) => {
    const def = definition(code);
    if (!def?.available) return;
    const condition = newCondition(code, timeframe);
    if (duplicateCondition(libraryState.target, condition)) { duplicates += 1; return; }
    listAt(libraryState.target).push(condition); added += 1;
  });
  libraryState = { ...libraryState, open: false, selected: [], removeEmptyOnCancel: false };
  document.body.classList.remove('ts-library-open');
  renderPage();
  if (added) { toast(`已加入 ${added} 个技术信号${duplicates ? `，跳过 ${duplicates} 个完全重复条件` : ''}`); schedulePreview(); }
  else if (duplicates) toast('所选信号与当前组中的条件完全重复', 'warning');
}

function updateCondition(target) {
  const card = target.closest('[data-condition]');
  if (!card) return;
  const item = itemAt(card.dataset.condition);
  if (!item) return;
  if (target.dataset.field) item[target.dataset.field] = target.type === 'number' ? Number(target.value) : (target.type === 'checkbox' ? target.checked : target.value);
  if (target.dataset.param) item.params[target.dataset.param] = Number(target.value);
  schedulePreview();
}

function schemeComboElements() {
  const combo = root?.querySelector('.ts-scheme-combo');
  return {
    combo,
    input: combo?.querySelector('#ts-scheme-name'),
    toggle: combo?.querySelector('#ts-scheme-toggle'),
    list: combo?.querySelector('#ts-scheme-options'),
  };
}

function filterSchemeOptions(query = '') {
  const { list } = schemeComboElements();
  if (!list) return [];
  const needle = String(query || '').trim().toLowerCase();
  const options = [...list.querySelectorAll('[data-scheme-option]')];
  options.forEach((option) => { option.hidden = !!needle && !String(option.dataset.schemeSearch || '').includes(needle); });
  ['preset', 'custom'].forEach((kind) => {
    const heading = list.querySelector(`[data-scheme-group="${kind}"]`);
    if (heading) heading.hidden = !options.some((option) => option.dataset.schemeKind === kind && !option.hidden);
  });
  const visible = options.filter((option) => !option.hidden);
  const empty = list.querySelector('.ts-scheme-no-results');
  if (empty) empty.hidden = !!visible.length;
  return visible;
}

function openSchemeCombo(open = true, { showAll = false } = {}) {
  const { combo, input, toggle, list } = schemeComboElements();
  if (!combo || !input || !toggle || !list) return;
  const expanded = !!open;
  list.hidden = !expanded;
  input.setAttribute('aria-expanded', String(expanded));
  toggle.setAttribute('aria-expanded', String(expanded));
  toggle.setAttribute('aria-label', expanded ? '收起方案列表' : '展开方案列表');
  combo.classList.toggle('open', expanded);
  if (expanded) filterSchemeOptions(showAll ? '' : input.value);
}

function chooseSchemeOption(option) {
  if (!option) return;
  const id = option.dataset.schemeOption;
  if (option.dataset.schemeKind === 'preset') selectPreset(id);
  else loadScheme(id);
}

function bind() {
  root.querySelectorAll('[data-open-library]').forEach((button) => button.addEventListener('click', () => openSignalLibrary(button.dataset.openLibrary)));
  const libraryRoot = libraryPortal();
  libraryRoot?.querySelector('[data-library-search]')?.addEventListener('input', (event) => { libraryState.query = event.target.value; renderLibraryAndFocus('#ts-library-search', true); });
  libraryRoot?.querySelectorAll('[data-library-category]').forEach((button) => button.addEventListener('click', () => { libraryState.category = button.dataset.libraryCategory; renderLibraryAndFocus(`[data-library-category="${button.dataset.libraryCategory}"]`); }));
  libraryRoot?.querySelectorAll('[data-library-filter]').forEach((button) => button.addEventListener('click', () => { libraryState.filter = button.dataset.libraryFilter; renderLibraryAndFocus(`[data-library-filter="${button.dataset.libraryFilter}"]`); }));
  libraryRoot?.querySelectorAll('[data-library-signal]').forEach((input) => input.addEventListener('change', () => {
    libraryState.selected = input.checked ? [...new Set([...libraryState.selected, input.dataset.librarySignal])] : libraryState.selected.filter((code) => code !== input.dataset.librarySignal);
    renderLibraryAndFocus(`[data-library-signal="${input.dataset.librarySignal}"]`);
  }));
  libraryRoot?.querySelectorAll('[data-library-close]').forEach((button) => button.addEventListener('click', closeSignalLibrary));
  libraryRoot?.querySelector('[data-library-clear]')?.addEventListener('click', () => { libraryState.selected = []; renderLibraryAndFocus('#ts-library-search'); });
  libraryRoot?.querySelector('[data-library-commit]')?.addEventListener('click', commitLibrarySelection);
  const backdrop = libraryRoot?.querySelector('[data-library-dismiss]');
  backdrop?.addEventListener('click', (event) => { if (event.target === backdrop) closeSignalLibrary(); });
  const dialog = libraryRoot?.querySelector('.ts-library-drawer');
  dialog?.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closeSignalLibrary(); return; }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(dialog.querySelectorAll('button:not([disabled]),input:not([disabled])'));
    if (!focusable.length) return;
    const first = focusable[0]; const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  root.querySelectorAll('[data-remove]').forEach((button) => button.addEventListener('click', () => { const parts = button.dataset.remove.split('.'); const index = Number(parts.pop()); const parentPath = parts.join('.'); const [removed] = listAt(parentPath).splice(index, 1); undoRemoval = { parentPath, index, item: clone(removed), label: definition(removed?.signal_code)?.signal_name || '技术信号' }; renderPage(); schedulePreview(); }));
  root.querySelectorAll('[data-defaults]').forEach((button) => button.addEventListener('click', () => { const item = itemAt(button.dataset.defaults); item.params = clone(definition(item.signal_code)?.default_params || {}); renderPage(); schedulePreview(); }));
  root.querySelectorAll('[data-add-rule-group]').forEach((button) => button.addEventListener('click', () => {
    const kind = button.dataset.addRuleGroup; const collection = ruleConfig[kind].groups; collection.push(makeGroup(kind));
    const target = `${kind}.${collection.length - 1}`; renderPage(); openSignalLibrary(target, { removeEmptyOnCancel: true });
  }));
  root.querySelectorAll('[data-remove-rule-group]').forEach((button) => button.addEventListener('click', () => {
    const [kind, rawIndex] = button.dataset.removeRuleGroup.split('.'); const index = Number(rawIndex); const [removed] = ruleConfig[kind].groups.splice(index, 1);
    undoRemoval = { type: 'group', kind, index, item: clone(removed), label: `${kind === 'exclude' ? '排除' : '满足'}规则 ${index + 1}` }; renderPage(); schedulePreview();
  }));
  root.querySelectorAll('[data-group-match]').forEach((input) => input.addEventListener('change', () => { ruleConfig.satisfy.groupMatch = input.value; renderPage(); schedulePreview(); }));
  root.querySelectorAll('[data-condition-match]').forEach((input) => input.addEventListener('change', () => { const [kind, index] = input.dataset.conditionMatch.split('.'); ruleConfig[kind].groups[Number(index)].conditionMatch = input.value; renderPage(); schedulePreview(); }));
  root.querySelector('#ts-undo')?.addEventListener('click', () => { if (!undoRemoval) return; if (undoRemoval.type === 'group') ruleConfig[undoRemoval.kind].groups.splice(undoRemoval.index, 0, undoRemoval.item); else listAt(undoRemoval.parentPath).splice(undoRemoval.index, 0, undoRemoval.item); undoRemoval = null; renderPage(); schedulePreview(); });
  root.querySelectorAll('[data-condition] input,[data-condition] select').forEach((input) => { input.addEventListener('change', () => updateCondition(input)); input.addEventListener('input', () => updateCondition(input)); });
  root.querySelectorAll('[data-market],#ts-ex-st,#ts-ex-paused,#ts-listing-days,#ts-min-amount,#ts-default-timeframe,#ts-sort,#ts-direction').forEach((input) => input.addEventListener('change', () => { captureControls(); schedulePreview(); }));
  root.querySelector('#ts-run')?.addEventListener('click', () => runScreen(false));
  root.querySelector('#ts-refresh')?.addEventListener('click', refreshData); root.querySelector('#ts-refresh-head')?.addEventListener('click', refreshData);
  root.querySelector('#ts-save')?.addEventListener('click', saveScheme);
  root.querySelector('#ts-copy')?.addEventListener('click', copyScheme);
  root.querySelector('#ts-delete')?.addEventListener('click', deleteScheme);
  const schemeNameInput = root.querySelector('#ts-scheme-name');
  schemeNameInput?.addEventListener('input', () => {
    schemeNameDraft = schemeNameInput.value;
    const selected = schemes.find((row) => row.id === selectedSchemeId);
    if (selected && selected.name !== schemeNameDraft.trim()) {
      selectedSchemeId = '';
      if (legacyReadOnlyPayload) { legacyReadOnlyPayload = null; ruleConfig = emptyRuleConfig(); ruleConfig.satisfy.groups.push(makeGroup('satisfy')); renderPage({ capture: false }); }
      syncSchemeActions();
    }
    const preset = presets.find((row) => row.id === selectedPresetId);
    if (preset && preset.name !== schemeNameDraft.trim()) { selectedPresetId = ''; syncSchemeActions(); }
    openSchemeCombo(true);
  });
  schemeNameInput?.addEventListener('focus', () => openSchemeCombo(true, { showAll: true }));
  schemeNameInput?.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault(); openSchemeCombo(true); filterSchemeOptions(schemeNameInput.value)[0]?.focus();
    } else if (event.key === 'Escape') {
      event.preventDefault(); openSchemeCombo(false);
    } else if (event.key === 'Enter') {
      event.preventDefault(); selectSchemeByName(schemeNameInput.value); openSchemeCombo(false);
    }
  });
  root.querySelector('#ts-scheme-toggle')?.addEventListener('click', () => {
    const { list } = schemeComboElements(); const expand = !!list?.hidden;
    openSchemeCombo(expand, { showAll: true });
    if (expand) schemeNameInput?.focus({ preventScroll: true });
  });
  root.querySelectorAll('[data-scheme-option]').forEach((option) => {
    option.addEventListener('click', () => chooseSchemeOption(option));
    option.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.preventDefault(); openSchemeCombo(false); schemeNameInput?.focus(); return; }
      if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
      event.preventDefault();
      const visible = [...root.querySelectorAll('[data-scheme-option]')].filter((row) => !row.hidden); const index = visible.indexOf(option);
      visible[(index + (event.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length]?.focus();
    });
  });
  root.querySelector('.ts-scheme-combo')?.addEventListener('focusout', (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) openSchemeCombo(false);
  });
  root.querySelector('#ts-history-run')?.addEventListener('click', loadHistory);
  root.querySelectorAll('.ts-code').forEach((button) => button.addEventListener('click', () => { sessionStorage.setItem('hermes.diagnosis.code', button.dataset.code); location.hash = `#/research?tab=diagnosis&code=${encodeURIComponent(button.dataset.code)}`; }));
}

function schedulePreview() {
  const naturalPreview = root?.querySelector('#ts-natural-preview');
  if (naturalPreview && !legacyReadOnlyPayload) naturalPreview.outerHTML = naturalLanguagePreview();
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => runScreen(true), 450);
}

async function runScreen(preview = false) {
  if (!root || loading || conflicts().length || !cacheStatus?.stocks) return;
  requestController?.abort(); requestController = new AbortController(); const ticket = generation;
  if (!preview) { loading = true; renderPage(); }
  try {
    const data = await api.post('/technical-screen/query', requestPayload({ limit: preview ? 10 : 50 }), { timeoutMs: 60000, signal: requestController.signal });
    if (!root || ticket !== generation) return;
    results = data; loading = false; renderPage();
  } catch (error) {
    if (error?.name === 'AbortError') return;
    loading = false;
    if (!preview) { results = { ok: false, error: error.message }; renderPage(); }
  }
}

async function refreshData() {
  if (cacheStatus?.state === 'running') return;
  try { await api.post('/technical-screen/refresh', {}); toast('已提交后台刷新，可继续使用其他页面'); cacheStatus = { ...cacheStatus, state: 'running', refresh: { processed: 0 } }; renderPage(); startStatusPolling(); }
  catch (error) { toast(error.message || '刷新任务提交失败', 'error'); }
}

function startStatusPolling() {
  clearInterval(statusTimer);
  statusTimer = setInterval(async () => {
    try {
      const next = await api.get('/technical-screen/status', { force: true });
      if (!root) return; cacheStatus = next;
      if (next.state !== 'running') { clearInterval(statusTimer); statusTimer = null; }
      renderPage();
    } catch { /* 保留上次状态 */ }
  }, 5000);
}

async function saveScheme() {
  if (selectedPresetId) { toast('预置方案不可直接修改，请先复制或输入新方案名称', 'warning'); return; }
  if (legacyReadOnlyPayload) { toast('该旧方案无法无损编辑，请先创建重建草稿', 'warning'); return; }
  if (conflicts().length) { toast('请先补全规则组中的技术信号', 'warning'); root.querySelector('.ts-inline-error')?.scrollIntoView({ block: 'center' }); return; }
  const name = root.querySelector('#ts-scheme-name')?.value.trim();
  if (!name) { toast('请先填写方案名称', 'warning'); root.querySelector('#ts-scheme-name')?.focus(); return; }
  const payload = { ...requestPayload({ forSave: true }), name };
  try { const saved = selectedSchemeId ? await api.put(`/technical-screen/schemes/${encodeURIComponent(selectedSchemeId)}`, payload) : await api.post('/technical-screen/schemes', payload); selectedSchemeId = saved.id; selectedPresetId = ''; schemeNameDraft = saved.name || name; toast('方案已保存'); schemes = (await api.get('/technical-screen/schemes', { force: true })).schemes || []; renderPage({ capture: false }); }
  catch (error) { toast(apiErrorMessage(error, '保存失败'), 'error'); }
}

function syncSchemeActions() {
  const selected = !!schemes.find((row) => row.id === selectedSchemeId);
  const preset = !!presets.find((row) => row.id === selectedPresetId);
  const save = root?.querySelector('#ts-save'); const copy = root?.querySelector('#ts-copy'); const remove = root?.querySelector('#ts-delete');
  if (save) { save.textContent = preset ? '预置方案' : (legacyReadOnlyPayload ? '旧方案只读' : (selected ? '更新方案' : '保存方案')); save.disabled = preset || !!legacyReadOnlyPayload; save.title = preset ? '预置方案不可直接修改，请先复制' : (legacyReadOnlyPayload ? '该旧方案无法无损编辑，请先创建重建草稿' : ''); }
  if (copy) copy.disabled = !(selected || preset);
  if (remove) { remove.disabled = !selected || preset; remove.title = preset ? '预置方案不可删除' : ''; }
  root?.querySelectorAll('[data-scheme-option]').forEach((option) => {
    const activeId = option.dataset.schemeKind === 'preset' ? selectedPresetId : selectedSchemeId;
    option.setAttribute('aria-selected', String(option.dataset.schemeOption === activeId));
  });
}

function selectSchemeByName(name) {
  schemeNameDraft = String(name || '');
  const preset = presets.find((row) => row.name === schemeNameDraft.trim());
  if (preset) { if (preset.id !== selectedPresetId) selectPreset(preset.id); return; }
  const item = schemes.find((row) => row.name === schemeNameDraft.trim());
  if (item) { if (item.id !== selectedSchemeId) loadScheme(item.id); return; }
  selectedSchemeId = ''; selectedPresetId = ''; syncSchemeActions();
}

function loadScheme(id) {
  const item = schemes.find((row) => row.id === id); selectedSchemeId = id || ''; if (!item) { renderPage(); return; }
  selectedPresetId = '';
  schemeNameDraft = item.name;
  undoRemoval = null;
  const editableConfig = item.schemaVersion === 2 && item.satisfy && item.exclude
    ? { schemaVersion: 2, satisfy: item.satisfy, exclude: item.exclude }
    : (item.editor_config?.schemaVersion === 2 ? item.editor_config : null);
  if (editableConfig) {
    legacyReadOnlyPayload = null;
    ruleConfig = clone(editableConfig);
  } else {
    legacyReadOnlyPayload = clone(item);
    ruleConfig = emptyRuleConfig();
  }
  universeState = { ...universeState, ...(item.universe || {}) }; defaultTimeframe = item.default_timeframe || 'D'; sortState = { ...(item.sort?.[0] || sortState) }; sortState.field ||= 'signal_score'; sortState.direction ||= 'desc';
  renderPage({ capture: false }); schedulePreview();
  if (item.algorithm_ver !== catalog.algorithm_ver) toast(`该方案使用旧算法版本 ${item.algorithm_ver}，当前不会静默改写`, 'warning');
}

async function copyScheme() {
  const item = schemes.find((row) => row.id === selectedSchemeId) || presets.find((row) => row.id === selectedPresetId); if (!item) return;
  if (legacyReadOnlyPayload) {
    selectedSchemeId = ''; selectedPresetId = ''; schemeNameDraft = `${item.name} 重建草稿`; legacyReadOnlyPayload = null; ruleConfig = emptyRuleConfig(); ruleConfig.satisfy.groups.push(makeGroup('satisfy'));
    renderPage({ capture: false }); toast('已创建空白重建草稿；原旧方案及其筛选逻辑未被修改', 'warning');
    queueMicrotask(() => openSignalLibrary('satisfy.0', { removeEmptyOnCancel: false })); return;
  }
  const copiedConfig = regenerateRuleIds(ruleConfig);
  try { const saved = await api.post('/technical-screen/schemes', { ...requestPayload({ config: copiedConfig, forSave: true }), name: `${item.name} 副本` }); selectedSchemeId = saved.id; selectedPresetId = ''; schemeNameDraft = saved.name || `${item.name} 副本`; ruleConfig = copiedConfig; schemes = (await api.get('/technical-screen/schemes', { force: true })).schemes || []; toast('方案副本已创建'); renderPage({ capture: false }); }
  catch (error) { toast(apiErrorMessage(error, '复制失败'), 'error'); }
}

async function deleteScheme() {
  if (selectedPresetId) { toast('预置方案不可删除', 'warning'); return; }
  if (!selectedSchemeId) return;
  if (!window.confirm('确认删除当前技术选股方案？')) return;
  try { await api.del(`/technical-screen/schemes/${encodeURIComponent(selectedSchemeId)}`); selectedSchemeId = ''; schemeNameDraft = ''; schemes = (await api.get('/technical-screen/schemes', { force: true })).schemes || []; toast('方案已删除'); renderPage({ capture: false }); }
  catch (error) { toast(error.message || '删除失败', 'error'); }
}

async function loadHistory() {
  const code = root.querySelector('#ts-history-code')?.value.trim(); const body = root.querySelector('#ts-history-result');
  if (!/^\d{6}$/.test(code)) { body.innerHTML = '<span class="ts-error">请输入6位股票代码</span>'; return; }
  body.innerHTML = skeleton(4);
  try { const data = await api.get(`/technical-screen/history?code=${encodeURIComponent(code)}&limit=200`, { force: true }); body.innerHTML = data.events?.length ? `<ol class="ts-history-list">${data.events.map((row) => `<li><time>${escapeHtml(row.event_date)}</time><b>${escapeHtml(({ D: '日', W: '周', M: '月' })[row.timeframe] || row.timeframe)}·${escapeHtml(row.signal_name)}</b><span>${escapeHtml(metricText(row.metrics))}</span></li>`).join('')}</ol>` : '<span class="ts-muted">缓存范围内没有该股票的事件命中。</span>'; }
  catch (error) { body.innerHTML = `<span class="ts-error">${escapeHtml(error.message || '查询失败')}</span>`; }
}

async function boot(ticket) {
  try {
    const [cat, state, saved] = await Promise.all([api.get('/technical-screen/catalog'), api.get('/technical-screen/status'), api.get('/technical-screen/schemes')]);
    if (!root || ticket !== generation) return; catalog = cat; cacheStatus = state; schemes = saved.schemes || []; if (!initialized) { selectPreset(selectedPresetId, { render: false }); initialized = true; } renderPage({ capture: false }); if (state.state === 'running') startStatusPolling();
  } catch (error) {
    if (!root || ticket !== generation) return; root.innerHTML = `<section class="card"><div class="card-body ts-empty"><b>技术选股初始化失败</b><span>${escapeHtml(error.message || '接口不可用')}</span></div></section>`;
  }
}

export function mount(container) {
  root = container; generation += 1; const ticket = generation; loading = false;
  container.innerHTML = `<section class="card"><div class="card-body">${skeleton(9)}</div></section>`;
  boot(ticket);
}

export function unmount() {
  generation += 1; requestController?.abort(); requestController = null; clearTimeout(previewTimer); previewTimer = null; clearInterval(statusTimer); statusTimer = null; libraryState.open = false; document.body.classList.remove('ts-library-open'); document.getElementById('ts-library-portal')?.remove(); root = null;
}

function styles() { return `
  .ts-unconfirmed{display:block;margin-top:7px;padding:5px;border:1px dashed var(--amber);color:var(--amber);font-size:11px}
  .ts-rule-editor{display:grid;gap:14px;margin-top:14px}.ts-rule-section{padding:14px;border:1px solid var(--border);border-radius:9px;background:var(--panel-2)}.ts-section-head,.ts-rule-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.ts-section-head h3{margin:0;font-size:15px}.ts-section-head p{margin:4px 0 0;color:var(--text-3);font-size:12px}.ts-section-head>span,.ts-rule-head em{flex:0 0 auto;padding:3px 7px;border-radius:999px;background:var(--blue-dim);color:var(--blue);font-size:10px;font-style:normal}.ts-rule-list{display:grid;gap:0;margin-top:12px}.ts-rule-card{padding:12px;border:1px solid var(--border);border-radius:8px;background:var(--panel)}.ts-rule-card.invalid{border-color:color-mix(in srgb,var(--risk) 58%,var(--border))}.ts-rule-head>div span,.ts-rule-head>div b{display:block}.ts-rule-head>div span{color:var(--text-3);font-size:11px}.ts-rule-head>div b{margin-top:3px;font-size:13px}.ts-rule-head .ts-remove{min-height:36px}.ts-connector{display:flex;align-items:center;gap:8px;padding:7px 12px;color:var(--text-2);font-size:11px;font-weight:800}.ts-connector:before,.ts-connector:after{height:1px;content:'';flex:1;background:var(--border)}.ts-add-rule{width:100%;min-height:42px;margin-top:10px;border-style:dashed}.ts-inline-error{margin:9px 0 0;color:var(--risk);font-size:12px}.ts-match{min-width:0;margin:11px 0;padding:0;border:0}.ts-match legend{margin-bottom:6px;color:var(--text-2);font-size:11px;font-weight:700}.ts-segmented{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px;padding:4px;border:1px solid var(--border);border-radius:8px;background:var(--panel-2)}.ts-segmented label{min-width:0;cursor:pointer}.ts-segmented label:has(input:disabled){cursor:default}.ts-segmented input{position:absolute;opacity:0;pointer-events:none}.ts-segmented span{min-height:38px;display:flex;align-items:center;justify-content:center;padding:6px 8px;border-radius:6px;color:var(--text-2);font-size:11px;font-weight:700;text-align:center;line-height:1.3}.ts-segmented input:checked+span{background:var(--blue-dim);box-shadow:inset 0 0 0 1px var(--blue);color:var(--text)}.ts-segmented input:focus-visible+span{outline:3px solid var(--amber);outline-offset:2px}.ts-natural-preview{padding:14px;border-left:3px solid var(--blue);border-radius:7px;background:var(--blue-dim);font-size:12px;line-height:1.65}.ts-natural-preview>ol,.ts-preview-exclude ol{margin:7px 0;padding-left:20px}.ts-preview-exclude{margin-top:10px;padding-top:10px;border-top:1px solid var(--border)}.ts-preview-exclude p,.ts-preview-muted{margin:6px 0 0;color:var(--text-3)}.ts-preview-empty{color:var(--risk)}.ts-legacy-readonly{margin-top:14px}.ts-legacy-readonly>p{margin-bottom:0;color:var(--text-2);font-size:12px;line-height:1.5}
  .ts-undo{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:9px;padding:8px 10px;border-left:3px solid var(--amber);background:var(--amber-dim);font-size:13px}.ts-undo button{min-height:36px;border:0;background:transparent;color:var(--amber);font-weight:700;cursor:pointer}
  #technical-screener{min-width:0;max-width:100%;overflow-x:hidden}.ts-head-status{margin-left:auto;display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end}.ts-status{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:700}.ts-dot{width:8px;height:8px;border-radius:50%;background:var(--amber)}.ts-status.ok .ts-dot{background:var(--down)}.ts-status.bad .ts-dot{background:var(--risk)}.ts-data-date,.ts-muted{color:var(--text-3);font-size:12px}
  .ts-layout{display:grid;grid-template-columns:minmax(440px,560px) minmax(0,1fr);gap:var(--gap);align-items:start}.ts-filter,.ts-results{min-width:0}.ts-toolbar,.ts-submit,.ts-period,.ts-block-head,.ts-condition-summary,.ts-universe-fields,.ts-history-form{display:flex;align-items:center;gap:10px}.ts-toolbar,.ts-submit{justify-content:space-between;flex-wrap:wrap}.ts-scheme-actions{display:grid;grid-template-columns:minmax(120px,1fr) auto auto auto;align-items:center;gap:6px;width:100%}.ts-scheme-actions .btn{white-space:nowrap}.ts-scheme-combo{position:relative;min-width:0}.ts-scheme-combo.open{z-index:30}.ts-scheme-combo .input{width:100%;min-width:120px;min-height:40px;padding-right:40px}.ts-scheme-combo>#ts-scheme-toggle{position:absolute;top:0;right:0;width:40px;height:40px;display:grid;place-items:center;padding:0;border:0;background:transparent;color:var(--text-3);cursor:pointer}.ts-scheme-combo>#ts-scheme-toggle:hover{color:var(--text);background:var(--hover)}.ts-combo-chevron{width:8px;height:8px;border-right:2px solid currentColor;border-bottom:2px solid currentColor;transform:translateY(-2px) rotate(45deg);transition:transform 160ms ease}.ts-scheme-combo.open .ts-combo-chevron{transform:translateY(2px) rotate(225deg)}.ts-scheme-options{position:absolute;top:calc(100% + 5px);left:0;z-index:31;width:min(360px,calc(100vw - 32px));max-height:320px;overflow-y:auto;padding:5px;background:var(--panel);border:1px solid var(--border-strong);border-radius:8px;box-shadow:0 14px 34px rgba(0,0,0,.3)}.ts-scheme-options[hidden],.ts-scheme-options [hidden]{display:none}.ts-scheme-option-group{padding:8px 9px 5px;color:var(--text-3);font-size:11px;font-weight:700}.ts-scheme-options [data-scheme-option]{width:100%;min-height:54px;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 9px;border:0;border-radius:6px;background:transparent;color:var(--text);text-align:left;cursor:pointer}.ts-scheme-options [data-scheme-option]:hover,.ts-scheme-options [data-scheme-option]:focus-visible{background:var(--hover)}.ts-scheme-options [data-scheme-option][aria-selected="true"]{background:var(--blue-dim);box-shadow:inset 3px 0 var(--blue)}.ts-scheme-options [data-scheme-option]>span{min-width:0}.ts-scheme-options b,.ts-scheme-options small{display:block}.ts-scheme-options b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.ts-scheme-options small{margin-top:3px;color:var(--text-3);font-size:11px;line-height:1.35}.ts-scheme-options em{flex:0 0 auto;color:var(--text-3);font-size:10px;font-style:normal}.ts-scheme-no-results{padding:18px 10px;color:var(--text-3);font-size:12px;text-align:center}
  .ts-block,.ts-period{margin-top:14px;padding:13px;border:1px solid var(--border);border-radius:8px;background:var(--panel-2)}
  .ts-block-head{justify-content:space-between;margin-bottom:9px}.ts-block-head h3{display:inline;font-size:14px;margin:0}.ts-block-head>span,.ts-block-head div+span{color:var(--text-3);font-size:12px}.ts-checks{display:flex;flex-wrap:wrap;gap:12px}.ts-checks label{display:flex;align-items:center;gap:5px;min-height:32px;font-size:13px}.ts-checks input{accent-color:var(--brand1)}.ts-sep{width:1px;background:var(--border);margin:4px}.ts-universe-fields{margin-top:9px;flex-wrap:wrap}.ts-universe-fields label{font-size:13px}.ts-universe-fields .input{width:84px;padding:6px 8px}.ts-period{flex-wrap:wrap;font-size:13px}.ts-period details{margin-left:auto;color:var(--text-3)}.ts-period details p{max-width:330px;margin:7px 0 0;line-height:1.5}.ts-logic{display:inline-flex;min-width:38px;justify-content:center;margin-right:7px;padding:2px 5px;border-radius:4px;background:var(--blue-dim);color:var(--blue);font-size:11px;font-weight:800}
  .ts-picker-actions{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.ts-picker-actions span,.ts-empty-group{color:var(--text-3);font-size:12px}.ts-open-library{min-height:40px}.ts-condition-list{display:grid;gap:8px;margin-top:9px}.ts-condition{border-left:3px solid var(--blue);border-radius:6px;background:var(--panel);padding:9px}.ts-condition.bearish{border-left-color:var(--risk)}.ts-condition-summary{min-width:0}.ts-condition-summary b{font-size:13px}.ts-condition-summary>span:not(.ts-kind){min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-3);font-size:11px}.ts-kind{font-size:10px;padding:2px 4px;border-radius:3px;background:var(--hover);color:var(--text-2)}.ts-remove{margin-left:auto;border:0;background:transparent;color:var(--text-3);cursor:pointer}.ts-condition-edit{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;margin-top:8px}.ts-condition-edit label{font-size:11px;color:var(--text-3)}.ts-condition-edit .input,.ts-condition-edit .select{display:block;width:100%;margin-top:3px;padding:6px}.ts-conflicts{display:grid;gap:4px;margin-top:12px;padding:10px;border-left:3px solid var(--risk);background:var(--risk-dim);font-size:12px}.ts-submit{margin-top:14px;padding-top:12px;border-top:1px solid var(--border)}.ts-submit>div{display:flex;align-items:center;gap:7px}.ts-submit label{font-size:12px;color:var(--text-3)}
  .ts-library-open{overflow:hidden}.ts-library-backdrop{position:fixed;z-index:1000;top:var(--topbar-h);right:0;bottom:var(--statusbar-h);left:0;display:flex;align-items:stretch;justify-content:flex-end;padding:0;overflow:hidden;background:rgba(0,0,0,.58)}.ts-library-drawer{width:clamp(560px,52vw,760px);height:100%;min-width:0;display:grid;grid-template-rows:auto auto minmax(0,1fr) auto;overflow:hidden;background:var(--panel);border:0;border-left:1px solid var(--border-strong);border-radius:10px 0 0 10px;box-shadow:-16px 0 40px rgba(0,0,0,.28);animation:tsLibraryIn 180ms ease-out}.ts-library-head,.ts-library-foot{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 18px;border-bottom:1px solid var(--border)}.ts-library-head h3{margin:0;font-size:18px}.ts-library-head p{margin:4px 0 0;color:var(--text-3);font-size:12px}.ts-library-close{min-width:44px;min-height:44px;border:0;background:transparent;color:var(--text-2);cursor:pointer}.ts-library-tools{padding:12px 18px;border-bottom:1px solid var(--border)}.ts-library-tools>label{display:block;margin-bottom:5px;color:var(--text-2);font-size:12px}.ts-library-tools>.input{width:100%;min-height:44px}.ts-library-filters{display:flex;gap:7px;margin-top:9px;overflow-x:auto}.ts-library-filters button,.ts-library-categories button{border:1px solid transparent;background:transparent;color:var(--text-2);cursor:pointer}.ts-library-filters button{min-height:36px;padding:0 11px;border-radius:6px;background:var(--panel-2);white-space:nowrap}.ts-library-filters button[aria-pressed="true"]{border-color:var(--amber);color:var(--text);background:var(--amber-dim)}.ts-library-main{min-height:0;display:grid;grid-template-columns:160px minmax(0,1fr);overflow:hidden}.ts-library-categories{min-height:0;padding:10px;border-right:1px solid var(--border);overflow-y:auto}.ts-library-categories button{width:100%;min-height:42px;display:flex;align-items:center;justify-content:space-between;padding:0 10px;border-radius:6px;text-align:left}.ts-library-categories button:hover,.ts-library-categories button[aria-pressed="true"]{background:var(--hover);color:var(--text)}.ts-library-categories button[aria-pressed="true"]{box-shadow:inset 3px 0 var(--amber)}.ts-library-categories em{color:var(--text-3);font-size:11px;font-style:normal}.ts-library-list{min-width:0;min-height:0;overflow-y:auto;padding:8px 14px 16px}.ts-library-row{min-height:72px;display:flex;align-items:flex-start;gap:11px;padding:11px 7px;border-bottom:1px solid var(--border);cursor:pointer}.ts-library-row:hover{background:var(--hover)}.ts-library-row>input{flex:0 0 auto;width:18px;height:18px;margin-top:3px;accent-color:var(--brand1)}.ts-library-copy{min-width:0;flex:1}.ts-library-copy>span{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.ts-library-copy b{font-size:13px}.ts-library-copy small,.ts-library-copy em{display:block;margin-top:4px;color:var(--text-3);font-size:11px;font-style:normal;line-height:1.4}.ts-library-row.unavailable{cursor:not-allowed;opacity:.62}.ts-library-row>strong{flex:0 0 auto;padding:3px 6px;border-radius:4px;background:var(--risk-dim);color:var(--risk);font-size:10px}.ts-bearish{padding:2px 4px;border-radius:3px;background:var(--risk-dim);color:var(--risk);font-size:10px;font-style:normal}.ts-library-empty{min-height:220px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;color:var(--text-2);text-align:center}.ts-library-empty span{color:var(--text-3);font-size:12px}.ts-library-foot{position:relative;z-index:2;border-top:1px solid var(--border);border-bottom:0;background:var(--panel);box-shadow:0 -8px 20px rgba(0,0,0,.12)}.ts-library-foot>div{display:flex;align-items:center;gap:9px}.ts-library-foot [data-library-clear]{border:0;background:transparent;color:var(--blue);cursor:pointer}.ts-library-foot [data-library-clear]:disabled{opacity:.45;cursor:not-allowed}@keyframes tsLibraryIn{from{transform:translateX(24px);opacity:.65}to{transform:translateX(0);opacity:1}}
  .ts-empty{min-height:220px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;text-align:center;color:var(--text-2)}.ts-empty span{max-width:520px;color:var(--text-3);line-height:1.5}.ts-result-summary{display:flex;justify-content:space-between;gap:10px;margin-bottom:10px;font-size:13px}.ts-result-summary span{color:var(--text-3)}.ts-table-wrap{overflow-x:auto}.ts-table{min-width:900px}.ts-code{border:0;background:transparent;color:var(--blue);font:inherit;font-weight:700;cursor:pointer}.ts-table td:first-child small{display:block;color:var(--text-3)}.ts-table details summary{cursor:pointer}.ts-table details ul{min-width:300px;padding-left:17px}.ts-table details li{margin:5px 0}.ts-table details li span{display:block;color:var(--text-3);font-size:12px}.ts-mobile-results{display:none}.ts-history{margin-top:var(--gap)}.ts-history .card-head>span{margin-left:auto;color:var(--text-3);font-size:12px}.ts-history-form{flex-wrap:wrap}.ts-history-form .input{max-width:180px}.ts-history-list{display:grid;gap:6px;margin:12px 0 0;padding:0;list-style:none}.ts-history-list li{display:grid;grid-template-columns:100px minmax(160px,240px) 1fr;gap:10px;padding:7px;border-bottom:1px solid var(--border);font-size:13px}.ts-history-list time,.ts-history-list span{color:var(--text-3)}.ts-error{color:var(--risk)}
  button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,.ts-table-wrap:focus-visible{outline:3px solid var(--amber);outline-offset:2px}@media(max-width:1100px){.ts-layout{grid-template-columns:1fr}.ts-results{min-height:260px}}@media(max-width:720px){.ts-head-status{width:100%;justify-content:flex-start}.ts-toolbar,.ts-submit,.ts-result-summary{align-items:stretch;flex-direction:column}.ts-submit>div{width:100%;flex-wrap:wrap}.ts-scheme-actions{grid-template-columns:1fr 1fr}.ts-scheme-combo{grid-column:1/-1}.ts-scheme-actions>*{min-height:44px}.ts-scheme-combo .input,.ts-scheme-combo>#ts-scheme-toggle{min-height:44px;height:44px}.ts-submit .btn,.ts-period .select{min-height:44px;flex:1}.ts-condition-edit{grid-template-columns:1fr}.ts-condition-edit .input,.ts-condition-edit .select{min-height:44px;font-size:16px}.ts-segmented span,.ts-add-rule{min-height:44px}.ts-table-wrap{display:none}.ts-mobile-results{display:grid;gap:8px}.ts-mobile-results article{padding:11px;border:1px solid var(--border);border-radius:7px;background:var(--panel-2)}.ts-mobile-results article>div{display:flex;justify-content:space-between}.ts-mobile-results article>span{display:block;margin-top:4px;color:var(--text-3);font-size:12px}.ts-mobile-results ul{padding-left:18px;font-size:12px}.ts-history-list li{grid-template-columns:1fr;gap:3px}.ts-checks label,.ts-remove{min-height:44px}.ts-library-backdrop{align-items:stretch;padding:0}.ts-library-drawer{width:100vw;height:100%;border:0;border-radius:0}.ts-library-head{padding:max(11px,env(safe-area-inset-top)) 12px 11px}.ts-library-foot{padding:11px 12px max(11px,env(safe-area-inset-bottom))}.ts-library-tools{padding:9px 12px}.ts-library-filters button,.ts-library-categories button{min-height:44px}.ts-library-main{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}.ts-library-categories{display:flex;gap:5px;padding:7px 10px;border-right:0;border-bottom:1px solid var(--border);overflow-x:auto;overflow-y:hidden}.ts-library-categories button{width:auto;min-width:max-content;padding:0 11px}.ts-library-categories button[aria-pressed="true"]{box-shadow:inset 0 -3px var(--amber)}.ts-library-list{padding:5px 10px 14px}.ts-library-foot{align-items:stretch;flex-direction:column}.ts-library-foot>div{justify-content:space-between}.ts-library-foot .btn{min-height:44px;flex:1}}@media(prefers-reduced-motion:reduce){#technical-screener *,.ts-library-backdrop *{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
`; }
