/* views/dashboard.js — 市场总览（终端版）
   行1：指数主图（4指数切换 × 分时/日K/周K，MA+量）｜ 情绪作战面板
   行2：涨停连板梯队 ｜ 板块热力树图（行业/概念）
   行3：主力资金 ｜ 涨幅前30 ｜ 任务时间线 */

import { api, cnNow, fmtMoney, fmtPct, fmtNum, fmtPrice, pctClass, isTradingTime, appState } from '../api.js';
import { dataStamp, emptyState, skeleton, icon, escapeHtml, toast } from '../ui.js';
import { mkChart, disposeCharts, klineOption, minuteOption, treemapOption, flowBarOption, PHASE_COLORS, UP, DOWN } from '../charts.js';
import { swrGet, swrPeek } from '../swr.js';
import { tickNum, seed } from '../live.js';
import { renderIntelHero } from './_intel_hero.js';

// swr 的 age(ms)→人话；stale 帧标「更新中」，fresh 帧重画时由 dataStamp 重置回纯时间戳，自动消失。
function agoTxt(age) {
  const s = Math.round(age / 1000);
  if (s < 60) return s + ' 秒前';
  if (s < 3600) return Math.round(s / 60) + ' 分钟前';
  return Math.round(s / 3600) + ' 小时前';
}

let root = null;
let charts = [];
let timers = [];
// 挂载代号：卸载/重挂载后的在途响应不得再触碰 DOM（防 null root 解引用、防 dispose 新视图的活图表）
let gen = 0;
let heroChart = null;
let flowChart = null;
let _visHandler = null;   // 后台tab切回可见的强刷监听(mount 注册/unmount 移除)

// mkt: 市场段控 a=A股(现状,带交互图) | hk=港股 | us=美股(只读指数条,复用 /news/multi)
const heroState = { mkt: 'a', idx: 'sh000001', mode: 'min', cache: {} };
let heatKind = 'industry';
let sectorsCache = [];

// serve-last-good：记录各板块最近一次"成功且非更残缺"的数据，供后台定时刷新(bg)比较。
// 后台刷新若 fetch 失败 / ok===false / 关键数组空 / missing 比上次多，则不覆盖已渲染的好内容
// （盘中降级时把有值字段刷成空/「—」正是"归零刷新"根因）。仅首次加载/手动刷新渲染空态。
let lastSenti = null;     // { s }（情绪对象）
let lastLadder = null;    // /market/limitup 整包
let lastFlow = null;      // /market/flow 整包
let lastTop = null;       // /market/top 整包
let lastNews = null;      // /news/multi 整包

// 后台刷新是否应放弃本次新数据（不覆盖上次的好内容）。
// serve-last-good 规则：bg 且已有 last，且 (新数据 ok===false || 关键数组为空 || missing 比上次多) → 放弃。
function staleGuard(bg, last, fresh, keyArr) {
  if (!bg || !last) return false;             // 首屏/手动刷新：照常渲染（含空态）
  if (!fresh || fresh.ok === false) return true;
  if (Array.isArray(keyArr) && keyArr.length === 0) return true;
  if ((fresh.missing?.length || 0) > (last.missing?.length || 0)) return true;
  return false;
}

const JOB_DEFS = [
  { id: 'morning', time: '08:50', name: '早盘内参' },
  { id: 'midday', time: '14:45', name: '尾盘决策草案（不推送）' },
  { id: 'tail', time: '14:52', name: '尾盘决策定稿' },
  { id: 'close', time: '15:30', name: '收盘复盘' },
  { id: 'evening', time: '19:30', name: '次日策略+趋势票' },
  { id: 'archive', time: '22:00', name: '晚间自动存档' },
];

const PHASES = ['冰点期', '退潮期', '分歧期', '修复期', '发酵期', '高潮期'];

export function mount(el) {
  gen += 1;
  root = el;
  root.innerHTML = `
    <div id="intel-hero" style="margin-bottom:var(--gap)"></div>
    <div class="grid12">
      <div class="card sp-8 hero-idx" id="dash-hero">
        <div class="segmented hero-mkt-seg" id="hero-mkt">
          <button class="seg active" data-mkt="a">A股</button>
          <button class="seg" data-mkt="hk">港股</button>
          <button class="seg" data-mkt="us">美股</button>
        </div>
        <div class="hero-tabs" id="hero-tabs">${skeleton(1, [44])}</div>
        <div class="hero-mktstat" id="hero-mktstat"></div>
        <div class="hero-toolbar" id="hero-toolbar">
          <div class="ohlc" id="hero-ohlc"></div>
          <div class="mode-tabs" id="hero-modes">
            <button data-m="min" class="active">分时</button>
            <button data-m="daily">日K</button>
            <button data-m="weekly">周K</button>
          </div>
        </div>
        <div class="hero-chart" id="hero-chart"></div>
      </div>

      <div class="card sp-4 senti-panel" id="dash-senti">
        <div class="card-head"><span class="card-title">市场情绪 · SENTIMENT</span><span class="card-extra" id="senti-stamp"></span></div>
        <div class="card-body" id="senti-body">${skeleton(5, [48, 14, 16, 40, 30])}</div>
      </div>

      <div class="card sp-7" id="dash-ladder">
        <div class="card-head"><span class="card-title">涨停连板梯队 · LIMIT LADDER</span><span class="card-extra" id="ladder-stamp"></span></div>
        <div class="card-body" id="ladder-body">${skeleton(5)}</div>
      </div>

      <div class="card sp-5" id="dash-heat">
        <div class="card-head">
          <span class="card-title">板块热力 · HEATMAP</span>
          <span class="card-extra">
            <div class="mode-tabs" id="heat-modes">
              <button data-k="industry" class="active">行业</button>
              <button data-k="concept">概念</button>
            </div>
          </span>
        </div>
        <div class="card-body" style="padding:6px"><div class="heat-chart" id="heat-chart"></div></div>
      </div>

      <div class="card sp-4" id="dash-flow">
        <div class="card-head"><span class="card-title">主力资金 · MAIN FLOW</span><span class="card-extra" id="flow-stamp"></span></div>
        <div class="card-body" id="flow-body">${skeleton(4)}</div>
      </div>

      <div class="card sp-5" id="dash-top">
        <div class="card-head"><span class="card-title">涨幅前 30 · TOP GAINERS</span><span class="card-extra" id="top-stamp"></span></div>
        <div class="card-body" id="top-body" style="max-height:360px;overflow-y:auto">${skeleton(6)}</div>
      </div>

      <div class="card sp-3" id="dash-jobs">
        <div class="card-head"><span class="card-title">今日任务 · SCHEDULE</span></div>
        <div class="card-body" id="jobs-body"></div>
      </div>

      <div class="card sp-12" id="dash-news">
        <div class="card-head"><span class="card-title">多市场异动 · GLOBAL MARKETS</span><span class="card-extra" id="news-stamp"></span></div>
        <div class="card-body" id="news-body">${skeleton(4)}</div>
      </div>
    </div>`;

  bindHero();
  bindHeat();
  loadAll();

  // 交易时段自动刷新（bg=true → serve-last-good：失败/更残缺时不覆盖已渲染的好内容）
  timers.push(setInterval(() => {
    if (!isTradingTime()) return;
    loadHero(true);
    loadSentiment(true);
    loadLadder(true);
  }, 15000));
  // 盘中指数数字 ~3s 跳动（轻量源，只更新指数条；K线/宽度/统计仍走 15s，不额外打慢源）
  timers.push(setInterval(() => {
    if (!isTradingTime()) return;
    refreshIndicesLight();
  }, 3000));
  timers.push(setInterval(() => {
    if (!isTradingTime()) return;
    loadHeat(true, true);
    loadFlow(true);
    loadTop(true);
    loadNews(true);
    loadIntelHero();
  }, 45000));
  // 任务推送状态徽标按分钟重绘（app.js 每 60s 刷新 appState.status；推送在 08:50/19:30/22:00 等
  // 多在交易时段外，故此定时器不设交易时段门控，确保"上次推成功/失败"能及时更新）
  timers.push(setInterval(renderJobs, 60000));

  // 后台标签页自愈：Chrome 冻结后台 tab 的定时器，切回时页面停在冻结前旧数据("还是昨天的"元凶)；
  // 收盘后 isTradingTime 门槛更会让其永久停更。切回可见 → 无条件全量刷一轮。
  _visHandler = () => {
    if (document.visibilityState !== 'visible' || !root) return;
    loadAll();
  };
  document.addEventListener('visibilitychange', _visHandler);
}

export function unmount() {
  timers.forEach(clearInterval);
  timers = [];
  if (_visHandler) { document.removeEventListener('visibilitychange', _visHandler); _visHandler = null; }
  disposeCharts(charts);
  if (flowChart) { disposeCharts([flowChart]); flowChart = null; }
  heroChart = null;
  heatChart = null;
  heroState.cache = {};
  heroState.mkt = 'a';        // 重入仪表盘回到 A 股（与 HTML 默认 active 段控一致）
  lastSenti = lastLadder = lastFlow = lastTop = lastNews = null;
  root = null;
}

function loadAll() {
  loadHero(true);
  loadSentiment();
  loadLadder();
  loadHeat(true);
  loadFlow();
  loadTop();
  loadNews();
  loadIntelHero();
  renderJobs();
}

// 全球资讯流 hero（外盘领先 LIVE）。复用 /news/multi 真数据，取不到则整块隐藏（组件内部处理）。
// 传 gen/isCurrent 防卸载/重挂载后的在途响应回写脏 DOM（与本视图 gen 机制一致）。
function loadIntelHero() {
  const host = root?.querySelector('#intel-hero');
  if (!host) return;
  const g = gen;
  renderIntelHero(host, { isCurrent: () => g === gen && !!root });
}

/* ================= 指数主图 ================= */

function bindHero() {
  // 市场段控 A/港/美：A=现状(实时指数 tab + 分时/日K/周K 交互图)；
  // 港/美=只读指数条(复用 /news/multi 的 hk.indexes / us.indexes，无交互图)。
  root.querySelector('#hero-mkt').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b || b.dataset.mkt === heroState.mkt) return;
    heroState.mkt = b.dataset.mkt;
    root.querySelectorAll('#hero-mkt .seg').forEach((x) => x.classList.toggle('active', x === b));
    switchMarket();
  });
  root.querySelector('#hero-modes').addEventListener('click', (e) => {
    if (heroState.mkt !== 'a') return;          // 港/美无交互图，模式切换无意义
    const b = e.target.closest('button');
    if (!b) return;
    heroState.mode = b.dataset.m;
    root.querySelectorAll('#hero-modes button').forEach((x) => x.classList.toggle('active', x === b));
    renderHeroChart();
  });
  root.querySelector('#hero-tabs').addEventListener('click', (e) => {
    if (heroState.mkt !== 'a') return;          // 港/美指数条不可点开 K 线
    const t = e.target.closest('.idx-tab');
    if (!t) return;
    heroState.idx = t.dataset.code;
    root.querySelectorAll('.idx-tab').forEach((x) => x.classList.toggle('active', x === t));
    // 已缓存→立即重绘；未缓存→只显加载态（绝不清空 host，否则会摘除 canvas 把图表实例搞成死的）
    if (heroState.cache[heroState.idx]) {
      renderHeroChart();
    } else {
      const ohlc = root.querySelector('#hero-ohlc');
      if (ohlc) ohlc.innerHTML = '<span style="color:var(--text-3)">加载中…</span>';
    }
    loadIndexData(heroState.idx).then(renderHeroChart);
  });
}

// A/港/美切换：A 复原交互图区并照常加载；港/美隐藏 K 线工具条+图表，渲染只读指数条。
function switchMarket() {
  const toolbar = root?.querySelector('#hero-toolbar');
  const chart = root?.querySelector('#hero-chart');
  if (heroState.mkt === 'a') {
    if (toolbar) toolbar.style.display = '';
    if (chart) chart.style.display = '';
    loadHero(true);                  // 重新拉 A 股实时 tabs + 当前指数图
  } else {
    if (toolbar) toolbar.style.display = 'none';   // 港/美无分时/日K/周K 交互图
    if (chart) chart.style.display = 'none';
    renderMarketIndexes(heroState.mkt);
  }
}

// 港/美只读指数条：真数据优先复用 /news/multi（hk.indexes / us.indexes，{name,close,change_pct}），
// 取不到就如实显示「暂无数据」，绝不堆占位假数据。复用 .idx-tab 视觉（无 data-code → 不可点开 K 线）。
async function renderMarketIndexes(mkt) {
  const el = root?.querySelector('#hero-tabs');
  if (!el) return;
  const g = gen;
  el.innerHTML = `<div class="data-stamp" style="padding:10px 14px">加载${mkt === 'hk' ? '港股' : '美股'}指数中…</div>`;
  let d = null;
  try { d = await api.get('/news/multi'); } catch { /* 下方兜底 */ }
  if (g !== gen || !root || heroState.mkt !== mkt) return;   // 卸载/已切走则不再触碰 DOM
  const list = mkt === 'hk' ? (d?.hk?.indexes || []) : (d?.us?.indexes || []);
  if (!list.length) {
    el.innerHTML = `<div class="data-stamp" style="padding:10px 14px;color:var(--text-3)">${mkt === 'hk' ? '港股' : '美股'}指数暂无数据（数据源未配置或不可达）</div>`;
    return;
  }
  // close=指数点位、change_pct=%；无成交额/代码（海外源不供），故只渲染名称+点位+涨跌幅。
  el.innerHTML = list.map((x) => {
    const cls = pctClass(x.change_pct);
    const pts = x.close != null ? fmtNum(x.close, 2) : '--';
    return `<div class="idx-tab" style="cursor:default">
      <span class="it-name">${escapeHtml(x.name ?? '')}</span>
      <span class="it-px ${cls}">${pts}</span>
      <span class="it-chg ${cls}">${fmtPct(x.change_pct)}</span>
    </div>`;
  }).join('');
}

async function loadHero(refreshTabs) {
  if (heroState.mkt !== 'a') return;   // 港/美由 renderMarketIndexes 接管，A 股定时刷新不得回写指数条
  // 图表与 tabs 并行加载，互不阻塞
  const chartP = loadIndexData(heroState.idx).then(() => renderHeroChart());
  if (refreshTabs) {
    try {
      const ov = await api.get('/market/overview');
      if (ov?.indexes?.length) renderHeroTabs(ov.indexes);
      else root?.querySelector('#hero-tabs')?.replaceChildren(Object.assign(document.createElement('div'), { className: 'data-stamp', style: 'padding:10px 14px', textContent: '非交易时段，暂无实时行情' }));
      renderHeroMktStat(ov);   // 7 指数下方再挂一条大盘卡统计：涨停数 / 最高连板 / 两市成交(同 overview)
    } catch { /* 保留旧 tabs/统计 */ }
  }
  await chartP;
}

// 盘中指数条轻量轮询（~3s）：只拉 /market/indices（3s 缓存·只打腾讯批量指数），指数数字就地跳动，
// 不动 K 线/宽度统计（那些贵，仍走 15s）。真跳动的节拍来源就是这个。
async function refreshIndicesLight() {
  if (!root || heroState.mkt !== 'a') return;
  try {
    const d = await api.get('/market/indices');
    if (!root || heroState.mkt !== 'a') return;
    if (d?.indexes?.length) renderHeroTabs(d.indexes);
  } catch { /* 保留旧条，不清空 */ }
}

function renderHeroTabs(indexes) {
  const el = root?.querySelector('#hero-tabs');
  if (!el) return;
  // 盘中跳动：指数集不变时只就地更新变化的数字（点位滚动+红涨绿跌闪），不整块重画（重画=无跳动感的元凶）。
  const existing = [...el.querySelectorAll('.idx-tab')];
  const sameSet = existing.length === indexes.length
    && existing.every((b, i) => b.dataset.code === String(indexes[i].code));
  if (sameSet) {
    indexes.forEach((x, i) => {
      const b = existing[i];
      const cls = pctClass(x.change_pct);
      b.classList.toggle('active', x.code === heroState.idx);
      const px = b.querySelector('.it-px');
      const chg = b.querySelector('.it-chg');
      const amt = b.querySelector('.it-amt');
      if (px) { px.className = 'it-px ' + cls; tickNum(px, x.price, (n) => fmtNum(n, 2)); }
      if (chg) { chg.className = 'it-chg ' + cls; chg.textContent = fmtPct(x.change_pct); }
      if (amt) amt.textContent = fmtMoney(x.amount, 0);
    });
    return;
  }
  // 首屏/指数集变化：整块建，然后播种点位基准（第一次真变化才从正确基准滚动、不误闪）
  el.innerHTML = indexes.map((x) => {
    const cls = pctClass(x.change_pct);
    return `<button class="idx-tab ${x.code === heroState.idx ? 'active' : ''}" data-code="${escapeHtml(x.code)}">
      <span class="it-name">${escapeHtml(x.name ?? '')}<span class="it-code">${escapeHtml(String(x.code).toUpperCase())}</span></span>
      <span class="it-px ${cls}">${fmtNum(x.price, 2)}</span>
      <span><span class="it-chg ${cls}">${fmtPct(x.change_pct)}</span><span class="it-amt">${fmtMoney(x.amount, 0)}</span></span>
    </button>`;
  }).join('');
  el.querySelectorAll('.idx-tab').forEach((b, i) => seed(b.querySelector('.it-px'), indexes[i].price));
}

// 大盘卡统计条（对齐 ashare 的 stat 行）：涨停数 / 最高连板 / 两市成交。
// 三个值全来自 /market/overview 既有字段(limit_up / lianban_height / total_amount)，
// 不新增端点、不另发请求。任一缺失按铁律显「—」+标注来源(missing 中"近似/口径"提示进 title)。
function renderHeroMktStat(ov) {
  const el = root?.querySelector('#hero-mktstat');
  if (!el || !ov) return;
  const lu = ov.limit_up, lb = ov.lianban_height, amt = ov.total_amount;
  // 两市成交在 overview 里可能是“指数口径近似(沪指+深成指)”——把该标注透到 tooltip，避免当全口径误读。
  const amtNote = (ov.missing || []).find((m) => m.includes('两市成交')) || '';
  const dash = '<span style="color:var(--text-3)">—</span>';
  el.innerHTML = `
    <div class="ms-cell"><span class="ms-k">涨停</span><span class="ms-v up">${lu != null ? fmtNum(lu) : dash}</span></div>
    <div class="ms-cell"><span class="ms-k">最高连板</span><span class="ms-v">${lb != null ? lb + '板' : dash}</span></div>
    <div class="ms-cell" title="${escapeHtml(amtNote)}"><span class="ms-k">两市成交</span><span class="ms-v">${amt != null ? fmtMoney(amt, 1) : dash}</span></div>`;
}

async function loadIndexData(code) {
  // 先用 swr 缓存填充 heroState.cache（renderHeroChart 立刻有图可画，不显「加载中…」）；
  // 同时后台拉新，新数据到位无缝替换。日内 K 线缓存 6h 仍可秒出。
  const c = swrPeek(`/index/${code}`);
  if (c && c.ok !== false && !heroState.cache[code]) heroState.cache[code] = c;
  return swrGet(`/index/${code}`, (d) => {
    if (d && d.ok !== false) heroState.cache[code] = d;
    if (root && heroState.mkt === 'a' && heroState.idx === code) renderHeroChart();  // 当前选中指数才重绘
  }, { maxAge: 6 * 3600e3 }).then(() => heroState.cache[code]);
}

function renderHeroChart() {
  const host = root?.querySelector('#hero-chart');
  if (!host) return;
  const d = heroState.cache[heroState.idx];
  if (!d) {
    // 数据未就绪：不清空 host（清空会摘除 canvas、令 heroChart 成为挂在已移除 DOM 上的死实例→后续 setOption 不显示）
    const ohlc = root.querySelector('#hero-ohlc');
    if (ohlc && !ohlc.textContent.trim()) ohlc.innerHTML = '<span style="color:var(--text-3)">加载中…</span>';
    return;
  }
  // 实例已被 dispose（主题切换重挂载 / host 被替换）则丢弃重建，避免向死实例写入
  if (heroChart && typeof heroChart.isDisposed === 'function' && heroChart.isDisposed()) heroChart = null;
  if (!heroChart) {
    heroChart = mkChart(host);
    if (heroChart) charts.push(heroChart);
  }
  if (!heroChart) { host.innerHTML = '<div class="empty-state"><div class="es-title">图表库未加载</div></div>'; return; }

  const ohlcEl = root.querySelector('#hero-ohlc');
  if (heroState.mode === 'min') {
    const daily = d.daily || [];
    let prevClose = null;
    if (daily.length) {
      const last = daily[daily.length - 1];
      prevClose = (d.minute_date && last.d === d.minute_date && daily.length >= 2) ? daily[daily.length - 2].c : last.c;
    }
    heroChart.setOption(minuteOption(d.minute, prevClose), true);
    const m = d.minute || [];
    const lastM = m[m.length - 1] || {};
    const chg = prevClose && lastM.price != null ? ((lastM.price - prevClose) / prevClose) * 100 : null;
    const cls = pctClass(chg);
    ohlcEl.innerHTML = m.length
      ? `<span>${escapeHtml(d.minute_date || '')}</span><span>现价 <b class="${cls}">${fmtNum(lastM.price, 2)}</b></span><span>涨跌 <b class="${cls}">${fmtPct(chg)}</b></span><span>均价 <b>${fmtNum(lastM.avg, 2)}</b></span>`
      : '<span>非交易时段，暂无实时分时</span>';
  } else {
    const bars = heroState.mode === 'daily' ? d.daily : d.weekly;
    heroChart.setOption(klineOption(bars, { zoomStart: heroState.mode === 'daily' ? 55 : 30 }), true);
    const b = (bars || [])[bars.length - 1] || {};
    const prev = (bars || [])[bars.length - 2];
    const chg = prev?.c ? ((b.c - prev.c) / prev.c) * 100 : null;
    const cls = pctClass(chg);
    ohlcEl.innerHTML = bars?.length
      ? `<span>${escapeHtml(b.d || '')}</span><span>开 <b>${fmtNum(b.o, 2)}</b></span><span>高 <b class="up">${fmtNum(b.h, 2)}</b></span><span>低 <b class="down">${fmtNum(b.l, 2)}</b></span><span>收 <b class="${cls}">${fmtNum(b.c, 2)}</b> <b class="${cls}">${fmtPct(chg)}</b></span>`
      : '<span>K线暂无数据</span>';
  }
}

/* ================= 情绪作战面板 ================= */

async function loadSentiment(bg = false) {
  const body = root?.querySelector('#senti-body');
  if (!body) return;
  const g = gen;
  // 宽度数据(overview)仍走原逻辑并行补全，不经 swr
  const oP = api.get('/market/overview').catch(() => null);

  // 后台定时刷新(bg)：维持原 api.get + staleGuard 链路（serve-last-good 已覆盖），不经 swr
  if (bg) {
    const s = await api.get('/sentiment/today').catch(() => null);
    if (g !== gen || !root) return;
    if (staleGuard(bg, lastSenti, s, null)) return;
    if (!s) return;                       // 后台失败：保留已渲染好内容
    lastSenti = s;
    renderSentiment(body, s, null);
    const ov = await oP;
    if (g === gen && root && ov) renderSentiment(body, s, ov);
    return;
  }

  // 首屏：swr 秒出上次情绪 → 后台刷新无缝替换
  await swrGet('/sentiment/today', (s, meta) => {
    if (g !== gen || !root || !s) return;
    if (!meta.stale) lastSenti = s;       // 仅真正 fetch 成功才更新 serve-last-good 基准
    renderSentiment(body, s, null);
    if (meta.stale) {
      const stamp = root.querySelector('#senti-stamp');
      if (stamp) stamp.innerHTML += `<span style="color:var(--text-3);font-size:11px;margin-left:6px">${agoTxt(meta.age)} · 更新中</span>`;
    }
    oP.then((ov) => { if (g === gen && root && ov) renderSentiment(body, s, ov); });
  }, { maxAge: 12 * 3600e3, onError: (e, { hadCache }) => {
    if (g !== gen || !root) return;
    if (!hadCache) body.innerHTML = emptyState('情绪数据加载失败', '点击顶栏刷新重试');  // 无缓存才报错
  } });
}

function renderSentiment(body, s, ov) {

  const stamp = root.querySelector('#senti-stamp');
  if (stamp) stamp.innerHTML = dataStamp(s.date, s.missing);

  const color = PHASE_COLORS[s.phase] || 'var(--flat)';
  const phaseIdx = PHASES.indexOf(s.phase);
  const m = s.metrics || {};
  const up = ov?.up_count, down = ov?.down_count, flat = ov?.flat_count;
  // 三者必须都为有效数字才画宽度条，否则部分缺失会渲染出 NaN%/畸形条——统一降级为「暂无数据」。
  const breadthOk = [up, down, flat].every((v) => typeof v === 'number' && isFinite(v));
  const total = breadthOk ? up + down + flat : 0;

  body.innerHTML = `
    <div class="sp-score-row">
      <div class="sp-score" style="color:${color}">${s.score ?? '--'}</div>
      <div class="sp-side">
        <span class="phase-badge" style="color:${color};border-color:${color};background:${color}1a">${escapeHtml(s.phase ?? '未知')}</span>
        <div class="desc">${escapeHtml(s.phase_desc ?? '')}</div>
      </div>
    </div>
    <div class="therm">${PHASES.map((p, i) => {
      const c = PHASE_COLORS[p];
      const lit = phaseIdx >= 0 && i <= phaseIdx;
      return `<i class="${lit ? 'lit' : ''}" style="${lit ? `background:${c};color:${c}` : ''}"></i>`;
    }).join('')}</div>
    <div class="therm-labels">${PHASES.map((p) => `<span>${p.replace('期', '')}</span>`).join('')}</div>
    ${total ? `
    <div class="breadth-bar">
      <div class="b-up" style="width:${(up / total) * 100}%">${fmtNum(up)}</div>
      <div class="b-flat" style="width:${Math.max(4, (flat / total) * 100)}%">${fmtNum(flat)}</div>
      <div class="b-down" style="width:${(down / total) * 100}%">${fmtNum(down)}</div>
    </div>` : '<div class="desc" style="margin-top:10px;color:var(--text-3)">涨跌家数：暂无数据</div>'}
    <div class="kpi-grid">
      ${kpi('涨停', m.limit_up, 'up', true)}
      ${kpi('跌停', m.limit_down, 'down')}
      ${kpi('连板高度', m.lianban_height != null ? m.lianban_height + '板' + tb(ov?.lianban_stale_date) : null, '', true)}
      ${kpi('炸板率', (ov?.zhaban_rate ?? m.zhaban_rate) != null ? fmtNum(ov?.zhaban_rate ?? m.zhaban_rate, 1) + '%' + tb(ov?.zhaban_rate_stale_date) : null)}
      ${kpi('两市成交', ov?.total_amount != null ? fmtMoney(ov.total_amount, 1) : null)}
      ${kpi('涨停板成交', ov?.limit_up_amount != null ? fmtMoney(ov.limit_up_amount, 0) + tb(ov?.struct_stale_date) : null)}
    </div>
    <div class="advice"><b>操作建议</b> ${escapeHtml(s.advice ?? '--')}</div>`;
}

function kpi(label, val, cls = '', hi = false) {
  return `<div class="kpi ${hi ? 'hi' : ''}"><div class="k-label">${label}</div><div class="k-val ${cls}">${val ?? '<span style="color:var(--text-3);font-size:15px">—</span>'}</div></div>`;
}

// T-1 角标（与 cockpit 涨跌停卡同款语义）：值来自上一交易日时醒目标「昨MM-DD」，绝不当今日实时（铁律）。
function tb(iso) {
  return iso ? ` <sup style="font-size:9px;color:var(--amber);font-weight:700">昨${escapeHtml(String(iso).slice(5))}</sup>` : '';
}

/* ================= 涨停梯队 ================= */

async function loadLadder(bg = false) {
  const body = root?.querySelector('#ladder-body');
  if (!body) return;
  const g = gen;
  let d = null;
  try { d = await api.get('/market/limitup'); } catch { /* 下方兜底 */ }
  if (g !== gen || !root) return;
  const stocks = d?.stocks || [];
  // 后台刷新 serve-last-good：拉取失败/更残缺/涨停池空时保留上次梯队，不刷成空态。
  if (staleGuard(bg, lastLadder, d, stocks)) return;
  const stamp = root.querySelector('#ladder-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d.ts, d.missing);
  if (!stocks.length) {
    // 用后端 missing 的诚实说明(盘中明细盘后落库 / 非交易时段)，不再硬编码误导文案。
    const why = (d?.missing || []).find((m) => m.includes('梯队') || m.includes('涨停')) || '涨停梯队本次未取得';
    body.innerHTML = emptyState('暂无涨停梯队明细', why);
    return;
  }
  lastLadder = d;
  const groups = new Map();
  stocks.forEach((s) => {
    const k = s.lianban >= 5 ? '5+' : String(s.lianban || 1);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(s);
  });
  const order = ['5+', '4', '3', '2', '1'];
  body.innerHTML = order.filter((k) => groups.has(k)).map((k) => {
    const list = groups.get(k);
    const label = k === '1' ? '首板' : k === '5+' ? '5板+' : `${k}连板`;
    const hot = k === '5+' || (k === '4' && !groups.has('5+'));
    return `<div class="ladder-row">
      <span class="ladder-tag ${hot ? 'hot' : ''}">${label}<span class="lt-cnt">${list.length}只</span></span>
      <div class="ladder-stocks">${list.slice(0, k === '1' ? 36 : 24).map((s) => `
        <span class="stock-chip" data-code="${escapeHtml(s.code)}" data-tip="${escapeHtml(`${s.name} ${s.code}\n封单 ${fmtMoney(s.seal_amount)} · 炸板${s.zhaban_count ?? 0}次\n首封 ${s.first_time || '--'} · 换手 ${fmtNum(s.turnover_rate, 1)}%`)}">
          <span class="sc-name">${escapeHtml(s.name)}</span><span class="num" style="color:var(--text-3);font-size:10px;margin-left:3px">${escapeHtml(s.code)}</span>
          ${s.reason ? `<span class="sc-reason">${escapeHtml(s.reason)}</span>` : ''}
          <span class="sc-info">${fmtMoney(s.seal_amount, 0)}</span>
        </span>`).join('')}
        ${list.length > (k === '1' ? 36 : 24) ? `<span class="chip">+${list.length - (k === '1' ? 36 : 24)}</span>` : ''}
      </div>
    </div>`;
  }).join('');
  body.querySelectorAll('.stock-chip').forEach((c) => {
    c.addEventListener('click', () => gotoStock(c.dataset.code));
  });
}

/* ================= 板块热力树图 ================= */

function bindHeat() {
  root.querySelector('#heat-modes').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    heatKind = b.dataset.k;
    root.querySelectorAll('#heat-modes button').forEach((x) => x.classList.toggle('active', x === b));
    renderHeat();
  });
}

async function loadHeat(fetch, bg = false) {
  if (fetch) {
    try {
      const d = await api.get('/market/sectors');
      // serve-last-good：仅当拿到非空板块列表才覆盖缓存；后台刷新时若新列表为空/失败，
      // 保留上次非空缓存（sectorsCache 本身即 last-good），避免把热力图刷成「暂无数据」。
      const fresh = d?.sectors;
      if (Array.isArray(fresh) && fresh.length) {
        sectorsCache = fresh;
      } else if (!bg) {
        sectorsCache = Array.isArray(fresh) ? fresh : [];   // 首屏/手动刷新：如实反映空态
      }
    } catch { /* 保留缓存 */ }
  }
  renderHeat();
}

let heatChart = null;
function renderHeat() {
  const host = root?.querySelector('#heat-chart');
  if (!host) return;
  const rows = sectorsCache.filter((s) => s.kind === heatKind).slice(0, 40);
  if (!rows.length) {
    // 清空 host 前必须先 dispose 并从 charts 移除 heatChart：否则 canvas 被摘除而实例残留，
    // 数据恢复后 setOption 写入死实例 → 永久空白 + ResizeObserver 泄漏（与已修的指数图同类）。
    if (heatChart) {
      disposeCharts([heatChart]);
      const i = charts.indexOf(heatChart); if (i >= 0) charts.splice(i, 1);
      heatChart = null;
    }
    host.innerHTML = '<div class="empty-state"><div class="es-title">板块暂无数据</div></div>';
    return;
  }
  // 实例已被 dispose（主题切换重挂载等）则重建，避免向死实例写入
  if (heatChart && typeof heatChart.isDisposed === 'function' && heatChart.isDisposed()) heatChart = null;
  if (!heatChart) {
    heatChart = mkChart(host);
    if (heatChart) charts.push(heatChart);
  }
  if (heatChart) heatChart.setOption(treemapOption(rows), true);
}

/* ================= 主力资金 ================= */

async function loadFlow(bg = false) {
  const body = root?.querySelector('#flow-body');
  if (!body) return;
  const g = gen;
  let d = null;
  try { d = await api.get('/market/flow'); } catch { /* 下方兜底 */ }
  // 重挂载后的陈旧响应若继续执行，会 dispose 新视图的 flowChart 并绑到脱离 DOM 的旧节点
  if (g !== gen || !root) return;
  const rows = d?.main_flow_sectors || [];
  // 后台刷新 serve-last-good：失败/更残缺/资金流空时直接返回，绝不走下方 dispose+重写 innerHTML，
  // 否则会把已渲染的资金流图表清成空态（归零）。须在 dispose 之前拦截。
  if (staleGuard(bg, lastFlow, d, rows)) return;
  const stamp = root.querySelector('#flow-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d.ts, d.missing);
  if (!rows.length) { body.innerHTML = emptyState('资金流暂无数据'); return; }
  lastFlow = d;
  // 先 dispose 上一轮的资金流图表实例：loadFlow 每 45s 重写 innerHTML 会丢弃旧 canvas，
  // 不 dispose 会每次泄漏一个 ECharts 实例 + ResizeObserver。
  if (flowChart) { disposeCharts([flowChart]); flowChart = null; }
  body.innerHTML = `${d.main_flow_total != null ? `<div style="font-size:15px;color:var(--text-3);margin-bottom:4px" class="mono">两市主力净额 <b class="${d.main_flow_total >= 0 ? 'up' : 'down'}">${fmtMoney(d.main_flow_total)}</b></div>` : ''}<div class="flow-chart" id="flow-chart"></div>${d.note ? `<div style="font-size:15px;color:var(--text-3)">${escapeHtml(d.note)}</div>` : ''}`;
  flowChart = mkChart(body.querySelector('#flow-chart'));
  if (flowChart) flowChart.setOption(flowBarOption(rows));
}

/* ================= 涨幅前30 ================= */

async function loadTop(bg = false) {
  const body = root?.querySelector('#top-body');
  if (!body) return;
  const g = gen;
  let d = null;
  try { d = await api.get('/market/top'); } catch { /* 下方兜底 */ }
  if (g !== gen || !root) return;
  const stocks = d?.stocks || [];
  // 后台刷新 serve-last-good：失败/更残缺/榜单空时保留上次涨幅榜，不刷成空态。
  if (staleGuard(bg, lastTop, d, stocks)) return;
  const stamp = root.querySelector('#top-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d.ts, d.missing);
  if (!stocks.length) { body.innerHTML = emptyState('涨幅榜暂无数据'); return; }
  lastTop = d;
  body.innerHTML = `<div class="tbl-wrap"><table class="tbl">
    <thead><tr><th>#</th><th class="l">名称</th><th class="l">代码</th><th>现价</th><th>涨幅</th><th>成交额</th><th>换手</th></tr></thead>
    <tbody>${stocks.map((s, i) => `<tr>
      <td style="color:var(--text-3)">${i + 1}</td>
      <td class="l cn" style="font-weight:600">${escapeHtml(s.name)}</td>
      <td class="l"><span class="code-link" data-code="${escapeHtml(s.code)}">${escapeHtml(s.code)}</span></td>
      <td>${fmtPrice(s.price)}</td>
      <td><span class="chg-pill ${pctClass(s.change_pct)}">${fmtPct(s.change_pct)}</span></td>
      <td>${fmtMoney(s.amount, 1)}</td>
      <td>${s.turnover_rate != null ? fmtNum(s.turnover_rate, 1) + '%' : '--'}</td>
    </tr>`).join('')}</tbody></table></div>
    <div style="font-size:15px;color:var(--text-3);margin-top:6px">点击代码进入「盘中问股」深度分析</div>`;
  body.querySelectorAll('.code-link').forEach((a) => {
    a.addEventListener('click', () => gotoStock(a.dataset.code));
  });
}

/* ================= 多市场异动（A股快讯 / 隔夜美股 / 港股） ================= */

async function loadNews(bg = false) {
  const body = root?.querySelector('#news-body');
  if (!body) return;
  let d = null;
  try { d = await api.get('/news/multi'); } catch { /* 下方兜底 */ }
  if (!root) return;

  const us = d?.us || {}, hk = d?.hk || {};
  const usIdx = us.indexes || [], usTech = us.tech_stocks || [], hkIdx = hk.indexes || [];
  const cn = d?.cn || [];
  // 后台刷新 serve-last-good：失败/更残缺/三市内容全空时保留上次多市场内容，不刷成空态。
  // 三段(美股/港股/A股快讯)任一非空即视为"有内容"，全空才算关键数组为空。
  const newsArr = usIdx.length || hkIdx.length || cn.length ? [1] : [];
  if (staleGuard(bg, lastNews, d, newsArr)) return;
  const stamp = root.querySelector('#news-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d.ts, d.missing);
  if (!d) { body.innerHTML = emptyState('多市场数据加载失败'); return; }
  lastNews = d;
  // 行情药丸：名称 + 涨跌幅，红涨绿跌；现价/收盘进 tooltip。缺数据走「暂无数据」而非编造。
  const pill = (x) => `<span class="mkt-pill ${pctClass(x.change_pct)}" data-tip="${escapeHtml(`${x.name || ''} ${x.close != null ? fmtNum(x.close, 2) : (x.price != null ? fmtNum(x.price, 2) : '--')}`)}">${escapeHtml(x.name || '')}<b>${fmtPct(x.change_pct)}</b></span>`;
  const hm = (t) => { const m = String(t || '').match(/\d{1,2}:\d{2}/); return m ? m[0] : ''; };

  body.innerHTML = `
    <div class="mkt-grid">
      <div class="mkt-col">
        <div class="mkt-sub">隔夜美股 · US</div>
        <div class="mkt-pills">${usIdx.length ? usIdx.map(pill).join('') : '<span class="mkt-empty">美股行情暂无数据</span>'}</div>
        ${usTech.length ? `<div class="mkt-sub">科技股映射 · TECH</div><div class="mkt-pills">${usTech.map(pill).join('')}</div>` : ''}
        <div class="mkt-sub">港股 · HK</div>
        <div class="mkt-pills">${hkIdx.length ? hkIdx.map(pill).join('') : '<span class="mkt-empty">港股指数暂无数据</span>'}</div>
      </div>
      <div class="mkt-col mkt-news">
        <div class="mkt-sub">A股快讯 · FLASH${cn.length ? `<span class="mkt-cnt">${cn.length}</span>` : ''}</div>
        ${cn.length
          ? `<div class="flash-list">${cn.slice(0, 24).map((n) => `<div class="flash-item">
              <span class="flash-time mono">${escapeHtml(hm(n.time) || '--')}</span>
              <span class="flash-src">${escapeHtml(n.source || '')}</span>
              <span class="flash-title" data-tip="${escapeHtml(n.content || n.title || '')}">${escapeHtml(n.title || '')}</span>
            </div>`).join('')}</div>`
          : '<span class="mkt-empty">A股快讯暂无数据</span>'}
      </div>
    </div>`;
}

/* ================= 任务时间线 ================= */

async function renderJobs() {
  if (!root) return;
  // app.js 的 loadStatus 可能尚未返回——任务状态为空时主动拉一次，保证推送状态徽标能正常显示
  if (!(appState.status?.jobs || []).length) {
    try { const s = await api.get('/status'); if (s) appState.status = s; } catch { /* 用现有 */ }
  }
  const body = root?.querySelector('#jobs-body');
  if (!body) return;
  const statusJobs = appState.status?.jobs || [];
  const c = cnNow();
  const nowHM = c.hh * 60 + c.mm;
  body.innerHTML = `<div class="timeline">${JOB_DEFS.map((j) => {
    const sj = statusJobs.find((x) => x.id === j.id);
    // 时间以后端真实调度为准：从 sj.name 尾部解析 HH:MM（用户在设置页改时间后会热生效），
    // 解析不到才回退 JOB_DEFS 默认值。tl-time 显示与 RUN/DONE 判定都用这个真实时间，避免与后端漂移。
    const realTime = sj?.name?.match(/(\d{2}:\d{2})$/)?.[1] || j.time;
    const [h, m] = realTime.split(':').map(Number);
    const t = h * 60 + m;
    // 优先用后端真实运行/推送状态（仅当属今日）；否则回退按时间判定 WAIT/RUN/DONE
    const today = appState.status?.date || '';
    const pushedToday = sj?.last_push_time && String(sj.last_push_time).slice(0, 10) === today;
    const ranToday = sj?.last_run && String(sj.last_run).slice(0, 10) === today;
    let state = 'todo', stateText = 'WAIT';
    let tip = sj?.next_run ? '下次执行：' + sj.next_run : '';
    if (ranToday && sj.last_status === 'running') { state = 'active'; stateText = 'RUN'; tip = '生成中…'; }
    else if (pushedToday && sj.last_push_ok === true) { state = 'done'; stateText = '✓已推'; tip = '今日已推送 ' + String(sj.last_push_time).slice(11, 16); }
    else if (pushedToday && sj.last_push_ok === false) { state = 'fail'; stateText = '✗失败'; tip = '今日推送失败 ' + String(sj.last_push_time).slice(11, 16) + (sj.last_error ? ' · ' + sj.last_error : ''); }
    else if (ranToday && sj.last_status === 'ok') { state = 'done'; stateText = '✓已生成'; tip = '今日已生成 ' + String(sj.last_run).slice(11, 16) + '（未推/推送已关）'; }
    else if (ranToday && sj.last_status === 'error') { state = 'fail'; stateText = '✗异常'; tip = '今日执行异常' + (sj.last_error ? ' · ' + sj.last_error : ''); }
    else if (nowHM >= t && nowHM < t + 10) { state = 'active'; stateText = 'RUN'; }
    else if (nowHM >= t + 10) { state = 'done'; stateText = 'DONE'; }
    return `<div class="tl-item ${state}">
      <span class="tl-time">${realTime}</span>
      <span class="tl-name" title="${escapeHtml(tip)}">${escapeHtml(sj?.name?.replace(/\s*\d{2}:\d{2}$/, '') || j.name)}</span>
      <span class="tl-state" title="${escapeHtml(tip)}">${stateText}</span>
      <button class="btn btn-sm btn-ghost run-job" data-job="${j.id}" title="立即手动执行">${icon('play')}</button>
    </div>`;
  }).join('')}</div>`;
  body.querySelectorAll('.run-job').forEach((b) => {
    b.addEventListener('click', async () => {
      b.disabled = true;
      try {
        const r = await api.post(`/jobs/${b.dataset.job}/run`, {});
        toast(r.ok ? '任务已触发，生成完成后可在报告中心查看' : (r.error || '触发失败'), r.ok ? 'success' : 'error');
      } catch (e) { toast('触发失败：' + e.message, 'error'); }
      setTimeout(() => { b.disabled = false; }, 3000);
    });
  });
}

function gotoStock(code) {
  sessionStorage.setItem('hermes.chat.code', code);
  location.hash = '#/chat';
}
