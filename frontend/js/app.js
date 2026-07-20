/* app.js — 路由、侧边导航、顶栏（时钟/指数/状态灯）、全局快捷键 */

import { api, appState, cnNow, fmtNum, fmtPct, pctClass, isTradingTime, lsGet, lsSet } from './api.js';
import { icon, escapeHtml, toast } from './ui.js';
import { refreshChartTheme } from './charts.js';
import { downloadPdf } from './export.js';
import {
  BUSINESS_NAV,
  UTILITY_NAV,
  WORKSPACES,
  buildHash,
  normalizeRecommendedRoute,
  parseHash,
  redirectsFromStatus,
  resolveHash,
} from './navigation.js';

/* ---------------- 日间/夜间主题 ---------------- */

function curTheme() {
  return document.documentElement.dataset.theme || 'dark';
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  lsSet('hermes.theme', theme);
  const btn = document.getElementById('tb-theme');
  if (btn) {
    btn.innerHTML = icon(theme === 'light' ? 'moon' : 'sun');
    btn.setAttribute('aria-label', theme === 'light' ? '切换到深色模式' : '切换到浅色模式');
  }
}

function initTheme() {
  let t = lsGet('hermes.theme', null);
  if (t !== 'light' && t !== 'dark') {
    // 跟随系统偏好，默认暗色
    t = (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
  }
  document.documentElement.dataset.theme = t;
}

function toggleTheme() {
  const next = curTheme() === 'light' ? 'dark' : 'light';
  // 切换瞬间启用全局过渡，400ms 后移除，避免后续数据刷新闪烁
  const html = document.documentElement;
  html.classList.add('theme-anim');
  applyTheme(next);
  try { refreshChartTheme(); } catch { /* noop */ }
  route(true);  // 重挂载当前视图，让 ECharts 用新主题色重建
  setTimeout(() => html.classList.remove('theme-anim'), 420);
}

/**
 * 叶子页按需加载。旧实现在 app.js 顶部静态 import 32 个页面，每次整页刷新都要
 * 经过完整模块图后才能进入用户点击的那一页。这个代理立即绘制局部加载态，
 * 仅下载当前叶子页；离开时用 generation 使较晚返回的 import 失效，不会串页。
 */
function lazyView(loader, label) {
  let loaded = null;
  let pending = null;
  let generation = 0;
  const ensure = () => {
    if (loaded) return Promise.resolve(loaded);
    if (!pending) {
      pending = loader().then((mod) => {
        if (!mod || typeof mod.mount !== 'function') throw new Error('页面模块未导出 mount');
        loaded = mod;
        return mod;
      }).finally(() => { pending = null; });
    }
    return pending;
  };
  return {
    mount(container) {
      const ticket = ++generation;
      container.innerHTML = `<section class="card lazy-view-loading"><div class="card-body">
        <span class="lazy-view-spinner" aria-hidden="true"></span><span>${escapeHtml(label)}正在加载…</span>
      </div></section>`;
      ensure().then((mod) => {
        if (ticket !== generation || !container.isConnected) return;
        mod.mount(container);
      }).catch((error) => {
        if (ticket !== generation || !container.isConnected) return;
        container.innerHTML = `<section class="card"><div class="card-body" style="padding:40px;text-align:center;color:var(--text-2)">
          ${escapeHtml(label)}加载失败：${escapeHtml(error?.message || '未知错误')}
        </div></section>`;
      });
    },
    beforeLeave() {
      return loaded && typeof loaded.beforeLeave === 'function' ? loaded.beforeLeave() : null;
    },
    unmount() {
      generation += 1;
      if (loaded && typeof loaded.unmount === 'function') loaded.unmount();
    },
    preload: ensure,
  };
}

const lazyBuiltins = Object.freeze({
  cockpit: lazyView(() => import('./views/cockpit.js'), '决策总览'),
  hotboard: lazyView(() => import('./views/hotboard.js'), '涨停题材'),
  forecast: lazyView(() => import('./views/forecast.js'), '明日预案'),
  dashboard: lazyView(() => import('./views/dashboard.js'), '大盘总览'),
  chat: lazyView(() => import('./views/chat.js'), '问股'),
  diagnosis: lazyView(() => import('./views/diagnosis.js'), '个股诊断'),
  reports: lazyView(() => import('./views/reports.js'), '报告'),
  sentiment: lazyView(() => import('./views/sentiment.js'), '情绪'),
  timing: lazyView(() => import('./views/timing.js'), '资金择时'),
  features: lazyView(() => import('./views/features.js'), '主线深研'),
  institutional: lazyView(() => import('./views/institutional.js'), '机构数据'),
  screener: lazyView(() => import('./views/screener.js'), '条件选股'),
  quant: lazyView(() => import('./views/quant.js'), '量化选股'),
  diy: lazyView(() => import('./views/diy.js'), 'DIY 研究'),
  holdings: lazyView(() => import('./views/holdings.js'), '我的持仓'),
  watchlist: lazyView(() => import('./views/watchlist.js'), '观察池'),
  simulation: lazyView(() => import('./views/simulation.js'), '模拟执行'),
  morning: lazyView(() => import('./views/morning.js'), '盘前报告'),
  tail: lazyView(() => import('./views/tail.js'), '尾盘决策'),
  close: lazyView(() => import('./views/close.js'), '收盘复盘'),
  evening: lazyView(() => import('./views/evening.js'), '次日策略'),
  company: lazyView(() => import('./views/company.js'), '公司研究'),
  chain: lazyView(() => import('./views/chain.js'), '产业链'),
  overseas: lazyView(() => import('./views/overseas.js'), '海外市场'),
  knowledge: lazyView(() => import('./views/knowledge.js'), '知识库'),
  settings: lazyView(() => import('./views/settings.js'), '系统设置'),
  macro: lazyView(() => import('./views/macro.js'), '宏观风险'),
  senti: lazyView(() => import('./views/senti.js'), '舆情'),
  mainforce: lazyView(() => import('./views/mainforce.js'), '主力结构'),
  capital: lazyView(() => import('./views/capital.js'), '资金追踪'),
  restructuring: lazyView(() => import('./views/restructuring.js'), '重组拆解'),
});

// 旧视图只登记为工作台的二级能力，不再直接铺成一级导航。
// 这个映射同时是扩展视图的注册表；页签通过 path 取 view，不复制任何业务逻辑。
const VIEW_ROUTES = new Map(Object.entries({
  ...lazyBuiltins,
  '__tail_view': lazyBuiltins.tail,
  '__settings_view': lazyBuiltins.settings,
}));

// 真正可见的 6+2 路由。createWorkspaceView 只做页签和生命周期代理。
const ROUTES = [...BUSINESS_NAV, ...UTILITY_NAV].map((item) => ({
  ...item,
  view: createWorkspaceView(item.path),
}));

let currentView = null;
let currentPath = '';
let currentRouteKey = '';
let recommendedHash = '#/today';
let serverRedirects = {};
let statusRedirects = {};
let extensionRedirects = {};
const extensionWorkspaceTabs = new Map();
// 品牌名（用于浏览器标题/状态栏等"名号"处）；随 /status 的 client 配置刷新，默认产品名
let brandTitle = 'W.U.D.I. 投研决策';
// 客户级导航偏好只控制 6+2 一级工作台；绝不能把具体业务功能一起藏掉。
// 叶子功能由 WORKSPACES 的 primary / more 分组管理，既收敛信息架构也保持可达。
let navHidden = new Set();
let _navHiddenSig = '';
// 客户级导航【改名】（config `ui.nav_labels` → /status.ui.nav_labels，{path: 新名称} 映射）：
// 只改侧栏显示文字——path/路由不动。未配置 = 用母项目默认名，其它客户零影响。
// 与 nav_hidden 同款：客户定制留在 config、不硬编污染共享核心，改错改 config 即回滚。
let navLabels = {};
let _navLabelsSig = '';
// 版本感知自动刷新：记录首次 /status 的核心指纹（core.fingerprint，任何核心文件变化即变）；
// 60s 轮询发现指纹变化 → 提示并自动 reload 加载新版。发版后客户零手动刷新。
// 流式生成中(.typing-cursor 在场)缓刷，避免打断问股/报告输出。
let _coreFp = '';
let _reloadArmed = false;
// v2 改为“全部能力默认可见、允许用户逐组收起”。使用新 key 是为了清除旧版只记住
// `today` 的折叠状态；否则升级后能力仍会像被删除一样消失。
const DEFAULT_OPEN_SIDEBAR_GROUPS = [...BUSINESS_NAV, ...UTILITY_NAV].map((item) => item.path);
const storedSidebarGroups = lsGet('hermes.nav.groups.v2', DEFAULT_OPEN_SIDEBAR_GROUPS);
let sidebarOpenGroups = new Set(
  Array.isArray(storedSidebarGroups) ? storedSidebarGroups : DEFAULT_OPEN_SIDEBAR_GROUPS,
);

/* ---------------- 侧边导航 ---------------- */

function buildSidebar() {
  const el = document.getElementById('sidebar');
  const workspaceTabs = (path) => {
    const workspace = WORKSPACES[path];
    if (!workspace) return [];
    return [
      ...workspace.tabs,
      ...(extensionWorkspaceTabs.get(path) || []),
    ].map(tabWithView).filter((item) => item && item.navigation !== false);
  };
  const renderItem = (r) => {
    const label = navLabels[r.path] || r.label;
    const tabs = workspaceTabs(r.path);
    const hasChildren = tabs.length > 1;
    const open = hasChildren && sidebarOpenGroups.has(r.path);
    const primary = tabs.filter((item) => item.placement !== 'more');
    const more = tabs.filter((item) => item.placement === 'more');
    const childrenId = `nav-children-${r.path}`;
    const childButtons = (items) => items.map((item) => `<button class="nav-child" type="button"
      data-path="${r.path}" data-tab="${item.id}" title="${escapeHtml(item.label)}">${escapeHtml(item.label)}</button>`).join('');
    return `<div class="nav-workspace${open ? ' open' : ''}" data-workspace-path="${r.path}">
      <div class="nav-item nav-parent" data-path="${r.path}" title="${escapeHtml(label)}" role="link" tabindex="0">
        ${icon(r.icon)}<span class="nav-label">${escapeHtml(label)}</span>
        ${hasChildren ? `<button class="nav-group-toggle" type="button" aria-label="${open ? '收起' : '展开'}${escapeHtml(label)}子目录"
          aria-expanded="${open ? 'true' : 'false'}" aria-controls="${childrenId}">${icon('chevR')}</button>` : ''}
      </div>
      ${hasChildren ? `<div class="nav-children" id="${childrenId}"${open ? '' : ' hidden'}>
        ${childButtons(primary)}
        ${more.length ? `<div class="nav-child-label">更多功能</div>${childButtons(more)}` : ''}
      </div>` : ''}
    </div>`;
  };
  const businessItems = BUSINESS_NAV.filter((r) => !navHidden.has(r.path));
  const utilityItems = UTILITY_NAV.filter((r) => !navHidden.has(r.path));
  el.innerHTML = `
    <div class="side-logo" id="side-logo" role="link" tabindex="0" title="返回决策首页">
      <div class="logo-mark">${icon('logo')}</div>
      <div class="logo-text"><div class="t1">W.U.D.I. 投研决策</div><div class="t2">A-SHARE DECISION</div></div>
    </div>
    <nav class="side-nav">
      <div class="nav-group">
        <div class="nav-group-label">交易工作台</div>
        ${businessItems.map(renderItem).join('')}
      </div>
    </nav>
    <div class="side-foot">
      <div class="side-utility" aria-label="工具入口">${utilityItems.map(renderItem).join('')}</div>
      <button class="collapse-btn" id="nav-collapse" title="折叠导航">${icon('chevL')}<span class="c-label">折叠</span></button>
    </div>`;

  const persistOpenGroups = () => lsSet('hermes.nav.groups.v2', [...sidebarOpenGroups]);
  const setSidebarCollapsed = (collapsed) => {
    const shell = document.getElementById('app');
    const button = el.querySelector('#nav-collapse');
    if (!shell || !button) return;
    shell.classList.toggle('nav-collapsed', collapsed);
    lsSet('hermes.nav.collapsed', collapsed);
    button.title = collapsed ? '展开导航' : '折叠导航';
    button.setAttribute('aria-label', collapsed ? '展开导航' : '折叠导航');
    const label = button.querySelector('.c-label');
    if (label) label.textContent = collapsed ? '展开' : '折叠';
  };
  const setGroupOpen = (workspace, open, persist = true) => {
    if (!workspace) return;
    const path = workspace.dataset.workspacePath;
    const children = workspace.querySelector('.nav-children');
    const toggle = workspace.querySelector('.nav-group-toggle');
    if (!children || !toggle) return;
    workspace.classList.toggle('open', open);
    children.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', `${open ? '收起' : '展开'}${navLabels[path] || WORKSPACES[path]?.label || ''}子目录`);
    if (open) sidebarOpenGroups.add(path);
    else sidebarOpenGroups.delete(path);
    if (persist) persistOpenGroups();
  };
  const activate = (item) => {
    if (!item) return;
    const path = item.dataset.path;
    const tab = item.dataset.tab;
    const params = new URLSearchParams();
    if (tab) params.set('tab', tab);
    location.hash = buildHash(path, params);
  };
  el.onclick = (e) => {
    const collapse = e.target.closest('#nav-collapse');
    if (collapse) {
      const shell = document.getElementById('app');
      setSidebarCollapsed(!shell?.classList.contains('nav-collapsed'));
      return;
    }
    const toggle = e.target.closest('.nav-group-toggle');
    if (toggle) {
      e.stopPropagation();
      const workspace = toggle.closest('.nav-workspace');
      setGroupOpen(workspace, !workspace.classList.contains('open'));
      return;
    }
    const child = e.target.closest('.nav-child');
    if (child) { activate(child); return; }
    const item = e.target.closest('.nav-item');
    if (item) activate(item);
  };
  el.onkeydown = (e) => {
    if (e.target.closest('.nav-group-toggle')) return;
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const item = e.target.closest('.nav-item, .nav-child');
    if (item) { e.preventDefault(); activate(item); }
  };
  const logo = el.querySelector('#side-logo');
  logo?.addEventListener('click', navigateHome);
  logo?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigateHome(); }
  });
  setSidebarCollapsed(Boolean(lsGet('hermes.nav.collapsed', false)));
}

function updateNav() {
  const currentTab = parseHash(location.hash).params.get('tab') || WORKSPACES[currentPath]?.defaultTab || '';
  document.querySelectorAll('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.path === currentPath));
  document.querySelectorAll('.nav-child').forEach((n) => {
    n.classList.toggle('active', n.dataset.path === currentPath && n.dataset.tab === currentTab);
  });
  const activeWorkspace = document.querySelector(`.nav-workspace[data-workspace-path="${currentPath}"]`);
  const children = activeWorkspace?.querySelector('.nav-children');
  const toggle = activeWorkspace?.querySelector('.nav-group-toggle');
  if (children && toggle && children.hidden) {
    activeWorkspace.classList.add('open');
    children.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    toggle.setAttribute('aria-label', `收起${navLabels[currentPath] || WORKSPACES[currentPath]?.label || ''}子目录`);
  }
}

function navigateRecommended() {
  location.hash = recommendedHash || '#/today';
}

function navigateHome() {
  location.hash = '#/today';
}

function updateRecommendedRoute(status) {
  const supplied = status?.market_context?.recommended_hash || status?.recommended_route || 'today';
  const resolved = resolveHash(String(supplied), serverRedirects);
  const path = normalizeRecommendedRoute(resolved.path, 'today');
  recommendedHash = buildHash(path, resolved.params);
  const routeDef = ROUTES.find((r) => r.path === path);
  const label = routeDef?.label || '今日决策';
  const title = `当前交易阶段：${label}`;
  const btn = document.getElementById('tb-today');
  if (btn) btn.title = title;
  const logo = document.getElementById('side-logo');
  if (logo) logo.title = '返回决策首页';
}

/* ---------------- 顶栏 ---------------- */

function buildTopbar() {
  const el = document.getElementById('topbar');
  // 顶栏保留完整北京时间与行情时态。日期/星期帮助用户确认交易日；“休市/盘外”必须明确
  // 告知页面展示最近交易日行情，避免把当前系统时间误读成行情实时性。
  el.innerHTML = `
    <div class="tb-left"><span class="tb-title" id="tb-page-title">交易工作台</span></div>
    <div class="tb-mid">
      <time class="tb-clock" id="tb-clock" datetime="">
        <span class="tb-clock-date">----年--月--日 星期-</span>
        <span class="tb-clock-meta"><span class="tb-clock-time num">--点--分</span><span class="tb-market-state" id="tb-market-state">状态确认中</span></span>
      </time>
      <span class="num" id="tb-fresh" title="最近一次成功取数距今；后端失联会变黄提醒" style="font-size:11px;margin-left:8px"></span>
    </div>
    <div class="tb-right">
      <button class="icon-btn tb-menu" id="tb-menu" title="菜单" aria-label="打开导航菜单" aria-expanded="false">${icon('menu')}</button>
      <button class="tb-today" id="tb-today" title="进入当前交易阶段" aria-label="进入当前交易阶段">${icon('dashboard')}<span>今日</span></button>
      <button class="icon-btn" id="tb-export-btn" title="下载当前页为 PDF" aria-label="下载当前页为 PDF">${icon('save')}</button>
      <button class="theme-toggle" id="tb-theme" title="切换日间/夜间模式" aria-label="${curTheme() === 'light' ? '切换到深色模式' : '切换到浅色模式'}">${icon(curTheme() === 'light' ? 'moon' : 'sun')}</button>
      <button class="icon-btn" id="tb-refresh" title="刷新当前页面数据" aria-label="刷新当前页面数据">${icon('refresh')}</button>
    </div>`;

  bindExportMenu(el);
  el.querySelector('#tb-today')?.addEventListener('click', navigateRecommended);
  el.querySelector('#tb-theme').addEventListener('click', toggleTheme);
  el.querySelector('#tb-refresh').addEventListener('click', () => {
    const btn = el.querySelector('#tb-refresh');
    btn.classList.add('spinning');
    setTimeout(() => btn.classList.remove('spinning'), 900);
    api.invalidate();
    loadShellStatus({ force: true });
    loadStatus({ force: true });
    route(true);   // 重挂载当前视图
  });

  // —— 移动端抽屉导航：汉堡开关 + 遮罩 + 选中即关 ——
  const app = document.getElementById('app');
  const menuBtn = el.querySelector('#tb-menu');
  let scrim = app.querySelector('.nav-scrim');
  if (!scrim) {
    scrim = document.createElement('div');
    scrim.className = 'nav-scrim';
    scrim.setAttribute('aria-hidden', 'true');
    app.appendChild(scrim);
  }
  const closeNav = () => {
    app.classList.remove('nav-open');
    menuBtn?.setAttribute('aria-expanded', 'false');
    menuBtn?.setAttribute('aria-label', '打开导航菜单');
    menuBtn?.setAttribute('title', '打开导航菜单');
  };
  menuBtn?.addEventListener('click', () => {
    const open = app.classList.toggle('nav-open');
    menuBtn.setAttribute('aria-expanded', String(open));
    menuBtn.setAttribute('aria-label', open ? '关闭导航菜单' : '打开导航菜单');
    menuBtn.setAttribute('title', open ? '关闭导航菜单' : '打开导航菜单');
  });
  scrim.addEventListener('click', closeNav);
  // 点导航项后自动收起抽屉（移动端体验）
  document.getElementById('sidebar')?.addEventListener('click', (e) => {
    if (e.target.closest('.nav-group-toggle')) return;
    if (e.target.closest('.nav-item, .nav-child, #side-logo')) closeNav();
  });
  // Esc 关闭 + 切到桌面宽度时复位，避免状态残留
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeNav(); });
  window.addEventListener('resize', () => { if (window.innerWidth > 768) closeNav(); });
}

// 导出 PDF：把当前视图 #view 的完整内容（含各板块）下载为 PDF。
// 不做"推送到微信"PDF 文件路径——iLink 底层对附件失败会假成功；待网关补丁上线后再加回。
function bindExportMenu(el) {
  const btn = el.querySelector('#tb-export-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    if (btn.disabled) return;
    const view = document.getElementById('view');
    const r = ROUTES.find((x) => x.path === currentPath) || ROUTES[0];
    const date = appState.status?.date || '';
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    btn.setAttribute('aria-label', '正在生成当前页 PDF');
    btn.title = '正在生成 PDF，请稍候';
    let result = null;
    try {
      result = await downloadPdf(view, `${brandTitle}-${r.label}-${date}`);
    } finally {
      btn.disabled = false;
      btn.setAttribute('aria-busy', 'false');
      const succeeded = result?.ok === true;
      btn.dataset.exportResult = succeeded ? 'success' : 'failed';
      btn.setAttribute('aria-label', succeeded ? 'PDF 已生成并开始下载' : 'PDF 生成失败，可重试');
      btn.title = succeeded ? 'PDF 已生成并开始下载' : 'PDF 生成失败，可重试';
      setTimeout(() => {
        if (!btn.isConnected) return;
        delete btn.dataset.exportResult;
        btn.setAttribute('aria-label', '下载当前页为 PDF');
        btn.title = '下载当前页为 PDF';
      }, 5000);
    }
  });
}

function tickClock() {
  const el = document.getElementById('tb-clock');
  if (!el) return;
  const c = cnNow();
  const p = (n) => String(n).padStart(2, '0');
  const weeks = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];
  const compactMobile = window.innerWidth <= 480;
  const iso = `${c.y}-${c.m}-${c.d}T${p(c.hh)}:${p(c.mm)}:${p(c.ss)}+08:00`;
  const dateEl = el.querySelector('.tb-clock-date');
  const timeEl = el.querySelector('.tb-clock-time');
  if (dateEl) dateEl.textContent = compactMobile
    ? `${p(Number(c.m))}-${p(Number(c.d))} 周${'日一二三四五六'[c.day]}`
    : `${c.y}年${Number(c.m)}月${Number(c.d)}日 ${weeks[c.day]}`;
  if (timeEl) timeEl.textContent = `${p(c.hh)}点${p(c.mm)}分`;
  el.setAttribute('datetime', iso);

  const isTradeDay = appState.status ? appState.status.is_trade_day !== false : ![0, 6].includes(c.day);
  const marketSession = String(appState.status?.market_session || '');
  const trading = isTradeDay
    && ['intraday', 'tail'].includes(marketSession)
    && isTradingTime();
  const state = document.getElementById('tb-market-state');
  if (state) {
    state.className = 'tb-market-state';
    if (!isTradeDay) {
      state.textContent = compactMobile ? '休市' : '休市 · 最近交易日行情';
      state.classList.add('closed');
      state.title = '当前休市；页面行情卡展示最近一个交易日的数据，不代表当前时刻实时行情';
    } else if (marketSession === 'premarket') {
      state.textContent = '盘前';
      state.classList.add('closed');
      state.title = '当前为盘前阶段；行情卡展示最近交易日数据，集合竞价后再确认实时盘口';
    } else if (marketSession === 'tail' && trading) {
      state.textContent = '尾盘';
      state.classList.add('open');
      state.title = '当前为尾盘决策阶段，盘口仍处于交易时段';
    } else if (trading) {
      state.textContent = '交易中';
      state.classList.add('open');
      state.title = '当前处于 A 股交易时段';
    } else if (marketSession === 'review') {
      state.textContent = compactMobile ? '盘后' : '已收盘 · 进入复盘';
      state.classList.add('closed');
      state.title = '交易所已收盘；页面行情为本交易日收盘数据';
    } else {
      state.textContent = compactMobile ? '盘外' : '盘外 · 最近交易日行情';
      state.classList.add('closed');
      state.title = '当前不在交易时段；页面行情卡展示最近交易日数据';
    }
  }

  // 顶栏只表示连接状态，不替页面内所有字段背书为“实时”。行情、新闻、资金和定格结论
  // 刷新频率不同，各卡片继续显示自己的采集/事件/定格时点。
  // 只有真断线（有过成功、之后连续失败且超 60s）才亮黄"连接中断"（旧数据不冒充新，这条必须留）。
  const fr = document.getElementById('tb-fresh');
  if (fr) {
    const okAt = appState.netOkAt || 0;
    const broken = okAt > 0 && (appState.netFails || 0) >= 2 && (Date.now() - okAt > 60000);
    if (broken) {
      const mins = Math.max(1, Math.round((Date.now() - okAt) / 60000));
      fr.textContent = `⚠ 连接中断 · 显示${mins}分钟前数据`;
      fr.style.color = 'var(--amber, #f5a623)';
      fr.title = '';
    } else if (trading && okAt) {
      fr.innerHTML = '<span class="live-dot"></span>行情已连接';
      fr.style.color = 'var(--text-2)';
      fr.title = `连接检查 ${new Date(okAt).toLocaleTimeString('zh-CN', {
        hour12: false,
        timeZone: 'Asia/Shanghai',
      })}（北京时间）`;
    } else if (trading) {
      fr.textContent = '连接检查中';
      fr.style.color = 'var(--text-2)';
      fr.title = '尚未完成首次行情连接检查';
    } else {
      fr.textContent = '';
      fr.title = '';
    }
  }

}

// 版本更新温和提示：角落常驻小胶囊「有新版·点此刷新」，点了才 location.reload()——绝不自动重载打断用户。
function showUpdatePrompt() {
  if (document.getElementById('update-prompt')) return;
  const el = document.createElement('button');
  el.id = 'update-prompt';
  el.type = 'button';
  el.innerHTML = `${icon('refresh')}<span>有新版本 · 点此刷新</span>`;
  el.title = '产品已更新，点击加载新版（不会自动刷新打断你）';
  el.addEventListener('click', () => location.reload());
  document.body.appendChild(el);
}

function applyNavigationPreferences(s, deferShell = false) {
  const hidden = Array.isArray(s.ui?.nav_hidden) ? s.ui.nav_hidden.map(String) : [];
  const labels = (s.ui?.nav_labels && typeof s.ui.nav_labels === 'object') ? s.ui.nav_labels : {};
  const sig = hidden.slice().sort().join(',');
  const labelSig = JSON.stringify(labels);
  if (sig === _navHiddenSig && labelSig === _navLabelsSig) return;
  _navHiddenSig = sig;
  _navLabelsSig = labelSig;
  navHidden = new Set(hidden);
  navLabels = labels;
  if (!deferShell) {
    buildSidebar();
    updateNav();
    updateRecommendedRoute(s);
  }
}

async function loadShellStatus({ deferShell = false, force = false } = {}) {
  try {
    const shell = await api.get('/shell-status', { force });
    appState.status = { ...(appState.status || {}), ...shell };
    statusRedirects = redirectsFromStatus(shell.route_redirects);
    serverRedirects = { ...statusRedirects, ...extensionRedirects };
    updateRecommendedRoute(shell);
    applyNavigationPreferences(shell, deferShell);
    updateBrand(shell);
    updateStatusbar();
    return shell;
  } catch {
    // 壳状态失败不阻塞路由；完整 /status 仍会在后台继续探测。
    return null;
  }
}

async function loadStatus({ deferShell = false, force = false } = {}) {
  try {
    const s = await api.get('/status', { force });
    appState.status = s;
    // 后端提供阶段路由与兼容映射；前端内置契约仍是旧版后端的兜底。
    statusRedirects = redirectsFromStatus(s.route_redirects);
    serverRedirects = { ...statusRedirects, ...extensionRedirects };
    updateRecommendedRoute(s);
    // 版本感知：核心指纹变化(发版) → 角落温和提示「有新版·点此刷新」，用户点了才 reload。
    // 绝不再强制整页重载——那会把页面从用户手里刷掉，打断问股/看盘（"网页定期强制刷新影响体验"投诉）。
    const _fp = s.core?.fingerprint || '';
    if (_fp) {
      if (!_coreFp) {
        _coreFp = _fp;
      } else if (_fp !== _coreFp && !_reloadArmed) {
        _reloadArmed = true;
        showUpdatePrompt();
      }
    }
    // 客户级导航隐藏/改名：ui.nav_hidden / ui.nav_labels 变化时重建侧栏（首载/改配置后 60s 内生效）
    applyNavigationPreferences(s, deferShell);
    const client = document.getElementById('tb-client');
    if (client && s.client?.name) {
      client.textContent = s.client.name;
      client.style.display = '';
      client.title = `客户 ID：${s.client.id ?? '--'} · 版本 ${s.version ?? '--'}`;
    }
    const customModel = s.hermes?.custom_connection === true;
    setDot(
      'tb-hermes',
      customModel && !!s.hermes?.connected,
      customModel
        ? `AI 增强${s.hermes.connected ? '已连接' : '未连接'} · ${s.hermes.model ?? ''}`
        : 'AI 增强未配置（可选）',
      !customModel,
    );
    setDot(
      'tb-feishu',
      !!s.feishu?.configured,
      s.feishu?.configured ? '推送已配置' : '推送未配置（可选）',
      !s.feishu?.configured,
    );
    updateBrand(s);
    updateRelayBanner(s.relay);
    updateStatusbar();
    return s;
  } catch {
    setDot('tb-hermes', false, '无法连接后端服务');
    setDot('tb-feishu', false, '无法连接后端服务');
    // 后端不可达时不臆造隧道异常：仅隐藏横幅（无法判定中继健康，交由 Hermes 状态灯反映断连）
    updateRelayBanner(null);
    return null;
  }
}

// #3 数据隧道全局告警条：仅当配置了中继(enabled) 且最近一次中台/spot 失败(!ok) 时常驻；
// 恢复(ok)或未配置(enabled=false)/后端不可达时移除。横幅插在 ticker 之下、正文之上，不遮挡内容。
function updateRelayBanner(relay) {
  const wrap = document.querySelector('.main-wrap');
  if (!wrap) return;
  const show = !!(relay && relay.enabled && !relay.ok);
  let el = document.getElementById('relay-banner');
  if (!show) { el?.remove(); return; }
  if (!el) {
    el = document.createElement('div');
    el.id = 'relay-banner';
    el.className = 'relay-banner';
    el.setAttribute('role', 'alert');
    el.innerHTML = `${icon('alert')}<span>境内数据隧道异常，行情可能为陈旧/缺失数据，已自动重连中</span>`;
    const ticker = document.getElementById('ticker');
    // 插在 ticker 之后；ticker 缺失时回退到 main-wrap 顶部
    if (ticker && ticker.parentNode === wrap) wrap.insertBefore(el, ticker.nextSibling);
    else wrap.insertBefore(el, wrap.firstChild);
  }
}

function setDot(id, on, title, neutral = false) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('on', !neutral && on);
  el.classList.toggle('off', !neutral && !on);
  if (title) el.title = title;
}

/* ---------------- 底部状态条 ---------------- */

function buildStatusbar() {
  const el = document.getElementById('statusbar');
  if (!el) return;
  el.innerHTML = `
    <span class="sb-item"><span class="sb-amber">●</span> <b id="sb-client">--</b></span>
    <span class="sb-item" id="sb-data-source">DATA 状态确认中</span>
    <span class="sb-item" id="sb-trade">--</span>
    <span class="sb-item" id="sb-next">下一任务 --</span>
    <span class="sb-spacer"></span>
    <span class="sb-item right" id="sb-ver">HERMES TRADER</span>
    <span class="sb-item right">所有分析仅供参考 · 不构成投资建议</span>`;
}

function updateBrand(s) {
  const c = s.client || {};
  // 左上角主标题/副标题（按客户配置）；副标题为空则隐藏，标题为单行
  const t1 = document.querySelector('.side-logo .t1');
  const t2 = document.querySelector('.side-logo .t2');
  if (t1 && c.brand) t1.textContent = c.brand;
  if (t2) {
    if (c.brand_sub) { t2.textContent = c.brand_sub; t2.style.display = ''; }
    else if (c.brand) { t2.style.display = 'none'; }
  }
  // 其余"名号"处用 client.name（无则退回产品名）
  brandTitle = c.name || brandTitle;
  const r = ROUTES.find((x) => x.path === currentPath) || ROUTES[0];
  document.title = `${r.label} · ${brandTitle}`;
}

function updateStatusbar() {
  const s = appState.status;
  if (!s) return;
  const c = document.getElementById('sb-client');
  if (c) c.textContent = `${s.client?.name ?? '--'} / ${(s.client?.id ?? '').toUpperCase()}`;
  const t = document.getElementById('sb-trade');
  if (t) t.textContent = `${s.date ?? ''} ${s.is_trade_day ? '交易日' : '休市'}`;
  const ds = document.getElementById('sb-data-source');
  if (ds) {
    const source = String(s.data_source || '数据源');
    const live = s.is_trade_day
      && ['intraday', 'tail'].includes(String(s.market_session || ''))
      && isTradingTime();
    ds.textContent = live
      ? `DATA ${source} · 交易时段直连`
      : `DATA ${source} · 最近交易日数据`;
  }
  const v = document.getElementById('sb-ver');
  if (v) v.textContent = `${s.client?.name ?? 'HERMES TRADER'} v${s.version ?? ''} · ${s.hermes?.model ?? ''}`;
  // 底栏只告诉用户下一个业务节点；预热 tick/资金快照等系统实现名不应泄漏到产品界面。
  const allJobs = (s.jobs || []).filter((j) => j.next_run);
  const jobs = allJobs.filter((j) => !isInternalSystemJob(j));
  if (jobs.length) {
    jobs.sort((a, b) => String(a.next_run).localeCompare(String(b.next_run)));
    const n = document.getElementById('sb-next');
    if (n) n.textContent = `下一任务 ${jobDisplayName(jobs[0])} @ ${String(jobs[0].next_run).slice(5, 16)}`;
  } else {
    const n = document.getElementById('sb-next');
    if (n) n.textContent = '下一业务任务 --';
  }
}

const JOB_LABELS = Object.freeze({
  prewarm_tick: '数据预热',
  capital_snapshot: '资金快照',
});

function internalJobKey(job) {
  const raw = `${job?.id || ''} ${job?.name || ''}`.toLowerCase();
  if (raw.includes('prewarm_tick') || raw.includes('register.<locals>')) return 'prewarm_tick';
  if (raw.includes('capital_snapshot') || raw.includes('build_scheduler.<locals>')) return 'capital_snapshot';
  return '';
}

function isInternalSystemJob(job) {
  return Boolean(internalJobKey(job));
}

function jobDisplayName(job) {
  const key = internalJobKey(job);
  if (key) return JOB_LABELS[key];
  const raw = String(job?.name || job?.id || '未命名任务');
  return /<locals>|build_scheduler|register\./i.test(raw) ? '系统维护任务' : raw;
}

/* ---------------- 路由 ---------------- */

let currentLeafPath = '';

function tabWithView(tabDef) {
  for (const path of tabDef.paths) {
    const registered = VIEW_ROUTES.get(path);
    const view = registered?.view || registered;
    if (view && typeof view.mount === 'function') {
      return {
        ...tabDef,
        label: navLabels[path] || tabDef.label,
        path,
        view,
        // ui.nav_hidden 面向一级工作台；历史上把它套到 leaf tab，导致“隐藏≠删除”
        // 在实际产品里变成了用户找不到的删除。叶子页只尊重自身/扩展的显式 navigation=false。
        navigation: registered?.navigation !== false && tabDef.navigation !== false,
      };
    }
  }
  return null;
}

function prepareLeafIntent(path, params) {
  const code = String(params.get('code') || '').replace(/\D/g, '').slice(0, 6);
  if (code && path === 'diagnosis') sessionStorage.setItem('hermes.diagnosis.code', code);
  if (code && path === 'chat') sessionStorage.setItem('hermes.chat.code', code);
  if (path === 'overseas') {
    const market = params.get('market');
    if (market) sessionStorage.setItem('hermes.overseas.market', market);
    if (params.get('code')) sessionStorage.setItem('hermes.overseas.code', params.get('code'));
  }
}

/**
 * 将现有 view 组合成新一级入口。代理只负责页签、路由意图和生命周期：
 * 内层视图原有的取数、业务状态、beforeLeave/mount/unmount 全部保留。
 */
function createWorkspaceView(path) {
  let leaf = null;

  const disposeLeaf = () => {
    if (leaf?.view && typeof leaf.view.unmount === 'function') {
      try { leaf.view.unmount(); } catch (e) { console.error('leaf unmount error', e); }
    }
    leaf = null;
  };

  return {
    mount(container) {
      disposeLeaf();
      const workspace = WORKSPACES[path] || WORKSPACES.today;
      const state = parseHash(location.hash);
      const tabDefs = [
        ...workspace.tabs,
        ...(extensionWorkspaceTabs.get(path) || []),
      ];
      const requestedId = state.params.get('tab') || workspace.defaultTab;
      const tabs = tabDefs.map(tabWithView).filter(Boolean);
      const accessibleTabs = tabs.filter((item) => item.navigation !== false);
      const primaryTabs = accessibleTabs.filter((item) => item.placement !== 'more');
      const hasRequestedTab = Boolean(state.params.get('tab'));
      const selected = (hasRequestedTab ? tabs.find((item) => item.id === requestedId) : null)
        || accessibleTabs.find((item) => item.id === requestedId)
        || primaryTabs.find((item) => item.id === workspace.defaultTab)
        || primaryTabs[0]
        || accessibleTabs[0]
        || tabs[0];

      if (!selected) {
        currentLeafPath = '';
        container.innerHTML = `<section class="card"><div class="card-body" style="padding:40px;text-align:center;color:var(--text-2)">
          该工作台的功能模块尚未加载，请稍后点击刷新。
        </div></section>`;
        return;
      }

      currentLeafPath = selected.path;
      prepareLeafIntent(selected.path, state.params);
      if (hasRequestedTab && state.params.get('tab') !== selected.id) {
        const normalizedParams = new URLSearchParams(state.params);
        normalizedParams.set('tab', selected.id);
        const normalizedHash = buildHash(path, normalizedParams);
        history.replaceState(null, '', normalizedHash);
        currentRouteKey = normalizedHash;
      }
      const panelId = `workspace-panel-${path}`;
      container.innerHTML = `<div class="workspace-view" data-workspace="${path}">
        <div class="workspace-panel" id="${panelId}" data-workspace-panel></div>
      </div>`;

      const host = container.querySelector('[data-workspace-panel]');
      leaf = selected;
      selected.view.mount(host);

      // 当前页首屏完成后，只在浏览器空闲时预加载同工作台的其他常用 JS 模块。
      // 这里不 mount、不取数；配合 api 层 SWR，用户第一次点“资金”时少等一次文件，
      // 再切回“决策”则直接使用 last-good 并后台更新。
      const warmSiblingModules = () => {
        primaryTabs
          .filter((item) => item !== selected && typeof item.view?.preload === 'function')
          .forEach((item) => { item.view.preload().catch(() => undefined); });
      };
      if (typeof window.requestIdleCallback === 'function') {
        window.requestIdleCallback(warmSiblingModules, { timeout: 2500 });
      } else {
        setTimeout(warmSiblingModules, 1200);
      }
    },
    beforeLeave() {
      if (!leaf?.view || typeof leaf.view.beforeLeave !== 'function') return null;
      return leaf.view.beforeLeave();
    },
    unmount() {
      disposeLeaf();
      currentLeafPath = '';
    },
  };
}

function route(forceRemount = false) {
  const resolved = resolveHash(location.hash, serverRedirects);
  const canonicalHash = buildHash(resolved.path, resolved.params);
  const r = ROUTES.find((x) => x.path === resolved.path) || ROUTES.find((x) => x.path === 'today');
  if (resolved.redirected && location.hash !== canonicalHash) {
    history.replaceState(null, '', canonicalHash);
  }
  if (!forceRemount && canonicalHash === currentRouteKey && currentView) return;

  // 未保存守卫（客户#1）：当前视图 beforeLeave() 返回提示串 → 弹确认；取消则回退 URL、不离开。
  // 回退 location.hash 会再触发本函数，但此时 path===currentPath → 上面提前 return，不会死循环。
  if (currentView && typeof currentView.beforeLeave === 'function') {
    let warn = '';
    try { warn = currentView.beforeLeave(); } catch { warn = ''; }
    if (warn && !window.confirm(warn)) {
      if (currentRouteKey && currentRouteKey !== location.hash) {
        history.replaceState(null, '', currentRouteKey);
      }
      return;
    }
  }

  if (currentView && typeof currentView.unmount === 'function') {
    try { currentView.unmount(); } catch (e) { console.error('unmount error', e); }
  }
  currentPath = r.path;
  currentRouteKey = canonicalHash;
  currentView = r.view;
  updateNav();

  const title = document.getElementById('tb-page-title');
  if (title) title.textContent = r.label;
  document.title = `${r.label} · ${brandTitle}`;

  const main = document.getElementById('view');
  main.innerHTML = '';
  main.classList.remove('view'); // 重触发进入动画
  void main.offsetWidth;
  main.classList.add('view');
  try {
    r.view.mount(main);
  } catch (e) {
    console.error('mount error', e);
    main.innerHTML = `<div class="card" style="padding:40px;text-align:center;color:var(--text-2)">页面渲染异常：${escapeHtml(e.message)}<br><span style="font-size:14px;color:var(--text-3)">请点击顶栏刷新按钮重试</span></div>`;
  }
  main.scrollTop = 0;
  window.scrollTo(0, 0);
}

/* ---------------- 全局快捷键 ---------------- */

function bindShortcuts() {
  document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || document.activeElement?.isContentEditable) return;
    e.preventDefault();
    if (currentLeafPath === 'chat') {
      document.getElementById('chat-input')?.focus();
    } else {
      sessionStorage.setItem('hermes.chat.focus', '1');
      location.hash = '#/research?tab=ask';
    }
  });
}

/* ---------------- 启动 ---------------- */

// 子项目扩展点（off-by-default）：从 /api/ext/views 拉取完整注册表。
// 只加载注册表：扩展模块与内置视图一样，用户真正进入时才 import。
// hidden 仍注册二级能力；retired/redirect 不 import，只由 hash 契约导向新入口。
// 视图模块须用绝对路径 import（'/js/api.js'、'/js/ui.js'），因其服务路径 /ext/ 与母项目 /js/ 不同。
async function loadExtensions() {
  let list = [];
  try {
    list = await api.get('/ext/views');
  } catch { return 0; }
  if (!Array.isArray(list) || !list.length) return 0;
  extensionRedirects = {};
  list.forEach((v) => {
    if (!v?.path) return;
    const lifecycle = String(v.lifecycle || 'visible').toLowerCase();
    if (lifecycle === 'redirect' && v.redirect_to) {
      extensionRedirects[String(v.path)] = v.redirect_to;
    }
  });
  serverRedirects = { ...statusRedirects, ...extensionRedirects };

  const registrations = list.map((v) => {
    if (!v || !v.path || !v.module) return null;
    const lifecycle = String(v.lifecycle || 'visible').toLowerCase();
    if (v.registered === false || lifecycle === 'retired' || lifecycle === 'redirect') return null;
    if (VIEW_ROUTES.has(String(v.path))) return null;
    return {
      path: String(v.path),
      view: lazyView(() => import(v.module), String(v.tab_label || v.label || v.path)),
      navigation: v.navigation !== false,
      meta: v,
    };
  });
  let added = 0;
  registrations.forEach((item) => {
    if (!item || VIEW_ROUTES.has(item.path)) return;
    VIEW_ROUTES.set(item.path, { view: item.view, navigation: item.navigation });
    const alreadyMapped = Object.values(WORKSPACES).some((workspace) =>
      workspace.tabs.some((tab) => tab.paths.includes(item.path)));
    if (!alreadyMapped) {
      const requestedWorkspace = String(item.meta?.workspace || '').trim();
      const workspace = Object.prototype.hasOwnProperty.call(WORKSPACES, requestedWorkspace)
        ? requestedWorkspace
        : 'research';
      const id = String(item.meta?.tab_id || item.path).replace(/_/g, '-');
      const extra = extensionWorkspaceTabs.get(workspace) || [];
      if (!extra.some((tab) => tab.id === id || tab.paths.includes(item.path))) {
        extra.push({
          id,
          label: String(item.meta?.tab_label || item.meta?.label || item.path),
          paths: [item.path],
        });
        extensionWorkspaceTabs.set(workspace, extra);
      }
      extensionRedirects[item.path] = { to: workspace, tab: id, preserveQuery: true };
    }
    added += 1;
  });
  serverRedirects = { ...statusRedirects, ...extensionRedirects };
  return added;
}

function boot() {
  initTheme();          // 先定主题，避免首屏闪烁
  buildSidebar();
  buildTopbar();
  buildStatusbar();
  bindShortcuts();

  tickClock();
  setInterval(tickClock, 1000);

  // 先进入用户指定的工作台，再后台补齐品牌、阶段路由和运维状态。
  // 模型网关/证据库即使超时，也不再把整个网页锁在“正在加载”。
  if (!location.hash) history.replaceState(null, '', '#/today');
  route(true);

  Promise.allSettled([
    loadShellStatus({ deferShell: true }),
    loadExtensions(),
  ]).then(() => {
    buildSidebar();
    updateBrand(appState.status || {});
    updateStatusbar();
    updateRecommendedRoute(appState.status || {});
    // 只有扩展注册表/服务端重定向真正改变了当前路由时才换页，
    // 普通首页不因状态返回再重挂载一次。
    route(false);
  });

  // 完整运维状态等首屏请求发出后再读；避免模型预检与决策首屏争抢单 worker。
  const hydrateFullStatus = () => loadStatus().then(() => route(false));
  if (typeof window.requestIdleCallback === 'function') {
    window.requestIdleCallback(hydrateFullStatus, { timeout: 1500 });
  } else {
    setTimeout(hydrateFullStatus, 600);
  }
  setInterval(() => { loadStatus(); }, 60000);

  // 全局新鲜度层只刷新连接/阶段状态；每个行情视图负责自己的原地增量更新。
  // 动态 import + catch：模块加载失败只是退回手动刷新，绝不拖垮主应用（老缓存/老浏览器安全）。
  import('./freshness.js')
    .then((m) => m.initFreshness({
      // 禁止用 route(true) 重挂整个工作台：那会每 45 秒闪屏、丢滚动和页签上下文。
      refresh: () => { loadStatus(); },
      getPath: () => currentPath,
    }))
    .catch(() => { /* 降级：无自动刷新，功能不受影响 */ });

  window.addEventListener('hashchange', () => route(false));

  if (!window.echarts) {
    toast('图表库未加载（vendor/echarts.min.js），图表区域将显示占位', 'error', 6000);
  }
}

boot();
