/* navigation.js — 交易工作台的纯路由与侧栏信息架构契约
 *
 * 这里只放信息架构和 hash 解析，不依赖 DOM：
 * - BUSINESS_NAV / UTILITY_NAV 保持既有路由契约；
 * - SIDEBAR_NAV 只负责左侧展示分组，可跨工作台编排现有页签；
 * - 旧 hash 保留查询参数并定位到新入口的对应页签；
 * - 页签只引用旧 view path，具体 mount/unmount 由 app.js 统一代理。
 */

export const BUSINESS_NAV = Object.freeze([
  { path: 'today', label: '今日决策', icon: 'dashboard' },
  { path: 'premarket', label: '盘前报告', icon: 'reports' },
  { path: 'risk', label: '持仓风控', icon: 'holdings' },
  { path: 'tail', label: '尾盘决策', icon: 'sentiment' },
  { path: 'review', label: '复盘与明日', icon: 'archive' },
  { path: 'research', label: '研究中心', icon: 'search' },
]);

export const UTILITY_NAV = Object.freeze([
  { path: 'history', label: '历史与知识', icon: 'knowledge' },
  { path: 'settings', label: '系统设置', icon: 'settings' },
]);

// placement="primary" 表示常用能力优先露出；"more" 仍是可见入口，
// 统一收进一级工作台的可折叠侧栏子目录，避免把 31 个旧页面重新铺成顶部横排。
// 这不是权限/生命周期开关：任何已有能力都必须仍可从页面进入。
const tab = (id, label, paths, { placement = 'primary' } = {}) => Object.freeze({
  id,
  label,
  paths: Object.freeze(Array.isArray(paths) ? paths : [paths]),
  placement,
});

export const WORKSPACES = Object.freeze({
  premarket: Object.freeze({
    label: '盘前报告',
    defaultTab: 'report',
    tabs: Object.freeze([
      tab('report', '盘前报告', 'morning'),
    ]),
  }),
  today: Object.freeze({
    label: '今日决策',
    defaultTab: 'decision',
    tabs: Object.freeze([
      // 首页只保留一份决策驾驶舱：市场全景、情绪状态、主线候选均已在这张卡内。
      // 不再把同一盘面拆成并列页签，让用户误以为是不同功能。
      tab('decision', '决策总览', 'cockpit'),
      tab('hotboard', '涨停题材', 'hotboard'),
      // 这些是独立的复核工具，统一放进“今日决策”的侧栏子目录；市场全景和情绪仍只在总览出现一次。
      tab('mainline', '主线深研', 'features'),
      tab('capital', '资金追踪', 'capital'),
      tab('timing', '资金择时', 'timing'),
      tab('mainforce', '主力结构', 'mainforce'),
      tab('news', '舆情事件', 'senti'),
    ]),
  }),
  risk: Object.freeze({
    label: '持仓风控',
    defaultTab: 'holdings',
    tabs: Object.freeze([
      tab('holdings', '我的持仓', 'holdings'),
      tab('watchlist', '观察池', 'watchlist'),
      tab('simulation', '模拟执行', 'simulation'),
    ]),
  }),
  tail: Object.freeze({
    label: '尾盘决策',
    defaultTab: 'decision',
    tabs: Object.freeze([tab('decision', '尾盘决策', '__tail_view')]),
  }),
  review: Object.freeze({
    label: '复盘与明日',
    defaultTab: 'close',
    tabs: Object.freeze([
      tab('close', '今日复盘', 'close'),
      tab('tomorrow', '次日策略', 'evening'),
    ]),
  }),
  research: Object.freeze({
    label: '研究中心',
    defaultTab: 'ask',
    tabs: Object.freeze([
      tab('ask', '问股', 'chat'),
      tab('diagnosis', '个股诊断', 'diagnosis'),
      tab('screener', '条件筛选', 'screener'),
      tab('technical-screener', '技术面选股', 'technical_screener'),
      tab('quant-pick', '量化选股', 'quant'),
      tab('events', '事件掘金', 'forecast'),
      tab('company', '公司研究', 'company', { placement: 'more' }),
      tab('chain', '产业链', 'chain', { placement: 'more' }),
      tab('restructuring', '重组拆解', 'restructuring', { placement: 'more' }),
      tab('overseas', '海外市场', 'overseas', { placement: 'more' }),
      tab('macro', '宏观风险', 'macro', { placement: 'more' }),
      tab('logic', '市场逻辑', 'market_logic', { placement: 'more' }),
    ]),
  }),
  history: Object.freeze({
    label: '历史与知识',
    defaultTab: 'reports',
    tabs: Object.freeze([
      tab('reports', '报告中心', 'reports'),
      tab('knowledge', '知识库', 'knowledge'),
    ]),
  }),
  settings: Object.freeze({
    label: '系统设置',
    defaultTab: 'system',
    tabs: Object.freeze([tab('system', '系统设置', '__settings_view')]),
  }),
});

// 左侧导航只是现有页面的展示编排，不改变 WORKSPACES、hash 或页面生命周期。
// 一级入口默认打开本组首项，所有子项均精确指向原页签。
const sidebarChild = (path, tabId, label, { placement = 'primary' } = {}) => Object.freeze({
  path,
  tab: tabId,
  label,
  placement,
});

export const SIDEBAR_NAV = Object.freeze([
  Object.freeze({
    id: 'today', label: '今日决策', icon: 'dashboard', path: 'today', tab: 'decision',
    children: Object.freeze([
      sidebarChild('today', 'decision', '决策总览'),
      sidebarChild('premarket', 'report', '盘前报告'),
      sidebarChild('tail', 'decision', '尾盘决策'),
      sidebarChild('review', 'close', '今日复盘'),
      sidebarChild('review', 'tomorrow', '次日策略'),
    ]),
  }),
  Object.freeze({
    id: 'market', label: '市场结构', icon: 'sentiment', path: 'today', tab: 'hotboard',
    children: Object.freeze([
      sidebarChild('today', 'hotboard', '涨停题材'),
      sidebarChild('today', 'mainline', '主线深研'),
      sidebarChild('today', 'capital', '资金追踪'),
      sidebarChild('today', 'timing', '资金择时'),
      sidebarChild('today', 'mainforce', '主力结构'),
      sidebarChild('today', 'news', '舆情事件'),
    ]),
  }),
  Object.freeze({
    id: 'risk', label: '持仓风控', icon: 'holdings', path: 'risk', tab: 'holdings',
    children: Object.freeze([
      sidebarChild('risk', 'holdings', '我的持仓'),
      sidebarChild('risk', 'watchlist', '观察池'),
      sidebarChild('risk', 'simulation', '模拟执行'),
    ]),
  }),
  Object.freeze({
    id: 'research', label: '研究中心', icon: 'search', path: 'research', tab: 'ask', extensionWorkspace: 'research',
    children: Object.freeze([
      sidebarChild('research', 'ask', '问股'),
      sidebarChild('research', 'diagnosis', '个股诊断'),
      sidebarChild('research', 'screener', '条件选股'),
      sidebarChild('research', 'technical-screener', '技术面选股'),
      sidebarChild('research', 'quant-pick', '每日量化选股'),
      sidebarChild('research', 'events', '事件掘金'),
      sidebarChild('research', 'chain', '产业链分析'),
      sidebarChild('research', 'company', '公司研究'),
      sidebarChild('research', 'overseas', '海外市场'),
      sidebarChild('research', 'restructuring', '重组拆解', { placement: 'more' }),
      sidebarChild('research', 'macro', '宏观风险', { placement: 'more' }),
    ]),
  }),
  Object.freeze({
    id: 'history', label: '历史与知识', icon: 'knowledge', path: 'history', tab: 'reports',
    children: Object.freeze([
      sidebarChild('history', 'reports', '全部报告'),
      sidebarChild('history', 'knowledge', '知识库'),
    ]),
  }),
]);

// 顶栏只显示当前页面所属的一级模块名，不随二级页面标题切换。
// 优先按精确的 path + tab 匹配侧栏分组；扩展页没有固定子项时按工作台归属回退。
export function moduleLabelForRoute(path, requestedTab = '', labelOverrides = {}) {
  const workspace = WORKSPACES[path] || WORKSPACES.today;
  const tabId = requestedTab || workspace.defaultTab || '';
  const exactGroup = SIDEBAR_NAV.find((group) => {
    const groupTab = group.tab || WORKSPACES[group.path]?.defaultTab || '';
    if (group.path === path && groupTab === tabId) return true;
    return (group.children || []).some((child) => child.path === path && child.tab === tabId);
  });
  const group = exactGroup
    || SIDEBAR_NAV.find((item) => item.id === path || item.path === path);
  const labelKey = group?.id || path;
  return labelOverrides[labelKey] || group?.label || workspace.label || '今日决策';
}

// 旧页面不删：将原 path 映射到新工作台的精确页签。
// preserveQuery 默认为 true，确保 diagnosis?code=... 等收藏链接继续可用。
export const LEGACY_REDIRECTS = Object.freeze({
  cockpit: { to: 'today', tab: 'decision' },
  hotboard: { to: 'today', tab: 'hotboard' },
  // 首页驾驶舱已经呈现市场全景与情绪状态；旧链接回到同一份首页，而不是再开一张重复页。
  dashboard: { to: 'today', tab: 'decision' },
  features: { to: 'today', tab: 'mainline' },
  capital: { to: 'today', tab: 'capital' },
  timing: { to: 'today', tab: 'timing' },
  mainforce: { to: 'today', tab: 'mainforce' },
  sentiment: { to: 'today', tab: 'decision' },
  senti: { to: 'today', tab: 'news' },
  morning: { to: 'premarket', tab: 'report' },
  premarket_board: { to: 'premarket', tab: 'report' },
  holdings: { to: 'risk', tab: 'holdings' },
  watchlist: { to: 'risk', tab: 'watchlist' },
  simulation: { to: 'risk', tab: 'simulation' },
  close: { to: 'review', tab: 'close' },
  evening: { to: 'review', tab: 'tomorrow' },
  recommend: { to: 'review', tab: 'close' },
  chat: { to: 'research', tab: 'ask' },
  diagnosis: { to: 'research', tab: 'diagnosis' },
  screener: { to: 'research', tab: 'screener' },
  technical_screener: { to: 'research', tab: 'technical-screener' },
  quant: { to: 'research', tab: 'quant-pick' },
  quant_pick: { to: 'research', tab: 'quant-pick' },
  forecast: { to: 'research', tab: 'events' },
  company: { to: 'research', tab: 'company' },
  chain: { to: 'research', tab: 'chain' },
  restructuring: { to: 'research', tab: 'restructuring' },
  institutional: { to: 'research', tab: 'company' },
  overseas: { to: 'research', tab: 'overseas' },
  macro: { to: 'research', tab: 'macro' },
  market_logic: { to: 'research', tab: 'logic' },
  diy: { to: 'research', tab: 'ask' },
  reports: { to: 'history', tab: 'reports' },
  archive: { to: 'review', tab: 'close' },
  knowledge: { to: 'history', tab: 'knowledge' },
  datasources: { to: 'settings', tab: 'system' },
  deriv: { to: 'research', tab: 'overseas' },
  sanshi: { to: 'today', tab: 'decision' },
  midday: { to: 'tail', tab: 'decision' },
  trend: { to: 'review', tab: 'tomorrow' },
});

export const CANONICAL_PATHS = Object.freeze(new Set(Object.keys(WORKSPACES)));

export function parseHash(hash = '') {
  const raw = String(hash || '').replace(/^#\/?/, '');
  const q = raw.indexOf('?');
  const routePart = q >= 0 ? raw.slice(0, q) : raw;
  const queryPart = q >= 0 ? raw.slice(q + 1) : '';
  const path = routePart.split('/')[0] || '';
  return { path, params: new URLSearchParams(queryPart) };
}

export function buildHash(path, params = new URLSearchParams()) {
  const query = params.toString();
  return `#/${path}${query ? `?${query}` : ''}`;
}

function normalizedRedirect(value) {
  if (!value) return null;
  if (typeof value === 'string') {
    const parsed = parseHash(value);
    return parsed.path ? { to: parsed.path, tab: parsed.params.get('tab') || '' } : null;
  }
  const to = String(value.to || value.path || '').replace(/^#\/?/, '').split('?')[0];
  if (!to) return null;
  return {
    to,
    tab: value.tab ? String(value.tab) : '',
    preserveQuery: value.preserve_query !== false && value.preserveQuery !== false,
  };
}

export function redirectsFromStatus(rows) {
  const out = {};
  if (!Array.isArray(rows)) return out;
  rows.forEach((row) => {
    const from = String(row?.from || '').replace(/^#\/?/, '').split(/[/?]/)[0];
    const redirect = normalizedRedirect(row);
    if (from && redirect) out[from] = redirect;
  });
  return out;
}

export function resolveHash(hash, serverRedirects = {}) {
  const parsed = parseHash(hash);
  const originalPath = parsed.path;
  if (CANONICAL_PATHS.has(originalPath)) {
    return { ...parsed, redirected: false, originalPath };
  }
  const redirect = normalizedRedirect(serverRedirects[originalPath])
    || normalizedRedirect(LEGACY_REDIRECTS[originalPath]);
  if (!redirect || !CANONICAL_PATHS.has(redirect.to)) {
    return { path: 'today', params: new URLSearchParams(), redirected: true, originalPath };
  }
  const params = redirect.preserveQuery === false ? new URLSearchParams() : new URLSearchParams(parsed.params);
  if (redirect.tab) params.set('tab', redirect.tab);
  return { path: redirect.to, params, redirected: true, originalPath };
}

export function normalizeRecommendedRoute(value, fallback = 'today') {
  const raw = typeof value === 'object' && value ? (value.path || value.route || value.hash) : value;
  const resolved = resolveHash(String(raw || ''));
  return CANONICAL_PATHS.has(resolved.path) ? resolved.path : fallback;
}

export function workspaceTab(path, requestedTab = '') {
  const workspace = WORKSPACES[path] || WORKSPACES.today;
  return workspace.tabs.find((item) => item.id === requestedTab)
    || workspace.tabs.find((item) => item.id === workspace.defaultTab)
    || workspace.tabs[0];
}
