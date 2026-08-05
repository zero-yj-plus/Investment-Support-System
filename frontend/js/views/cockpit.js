/* views/cockpit.js — 首页「今日总览驾驶舱」(按参考图重建，密集决策大屏，不重复)
   ------------------------------------------------------------------
   信息流：一句话总收口(+情绪/广度/连板) → 盘型环境/涨跌停/情绪仪表 → 资金证据/个股资金/连板梯队
          → 主线&观察候选(5卡) → 风险边界 → 多周期涨幅榜/题材热力图。
   每条信息只出现一次(去掉旧版 banner+卡①重复、外盘hero与指数重复)。
   铁律：所有数字来自后端真实端点；一句话收口/主线分类/风险边界走规则(从真实数据派生)，不编数字；
   数据状态区分「决策可用 / 主要数据可用 / 部分覆盖 / 暂不可用」。
   持仓/观察池/推荐留痕在导航各页，不再塞首页。 */

import { api, fmtMoney, fmtNum, fmtPct, fmtPrice, pctClass, isTradingTime, todayStr } from '../api.js';
import { icon, escapeHtml, emptyState, skeleton, dataStamp } from '../ui.js';
import { mkChart, disposeCharts, treemapOption } from '../charts.js';
import { tickNum } from '../live.js';

let root = null;
let charts = [];
let timers = [];
let gen = 0;
let heatChart = null;
let _visHandler = null;   // 后台tab切回可见的强刷监听(mount 注册/unmount 移除)
let _visTimer = null;
let _deferredSetupTimer = null;
let _summaryEnhanceTimer = null;
let _sparkTimer = null;
let _deferredObserver = null;
let _lastRefreshAt = 0;
let _sparksLoaded = false;
let _downPoolExpanded = false;
let _riskEvidenceExpanded = false;
const _activeDeferred = new Set();
const _loaderInFlight = new Map();
const _requestInFlight = new Map();

const DEFERRED_LOADERS = {
  sectorFlow: { load: loadSectorFlow, refreshDuringTrading: true },
  stockFlow: { load: loadStockFlow, refreshDuringTrading: true },
  heatmap: { load: loadHeatmap, refreshDuringTrading: false },
  multiperiod: { load: loadMultiperiod, refreshDuringTrading: false },
};

function getBounded(path, timeoutMs = 8000) {
  const running = _requestInFlight.get(path);
  if (running) return running.promise;
  // 由全局 api 层统一负责超时、单飞和 SWR 缓存。离开页面后请求可继续
  // 填充 last-good，gen/root 守卫会阻止它回写旧 DOM；再次进入即可秒开。
  const promise = api.get(path, { timeoutMs })
    .finally(() => {
      if (_requestInFlight.get(path)?.promise === promise) _requestInFlight.delete(path);
    });
  _requestInFlight.set(path, { promise });
  return promise;
}

function runLoader(key, loader) {
  const running = _loaderInFlight.get(key);
  if (running) return running;
  const mountGen = gen;
  const task = Promise.resolve()
    .then(() => {
      if (!root || mountGen !== gen) return undefined;
      return loader();
    })
    .finally(() => {
      if (_loaderInFlight.get(key) === task) _loaderInFlight.delete(key);
    });
  _loaderInFlight.set(key, task);
  return task;
}

function activateDeferred(key) {
  const item = DEFERRED_LOADERS[key];
  if (!item || _activeDeferred.has(key)) return;
  _activeDeferred.add(key);
  runLoader(`deferred:${key}`, item.load);
}

function setupDeferredLoading() {
  const targets = [...(root?.querySelectorAll('[data-cockpit-lazy]') || [])];
  if (!targets.length) return;
  if ('IntersectionObserver' in window) {
    _deferredObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        activateDeferred(entry.target.dataset.cockpitLazy);
        observer.unobserve(entry.target);
      });
    }, { rootMargin: '160px 0px', threshold: 0.01 });
    targets.forEach((target) => _deferredObserver.observe(target));
    return;
  }
  // 旧浏览器无 IntersectionObserver 时也不阻塞首屏，短延迟后按原口径加载。
  timers.push(setTimeout(() => targets.forEach((target) => {
    activateDeferred(target.dataset.cockpitLazy);
  }), 800));
}

function refreshCockpit({ refreshDeep = false, force = false } = {}) {
  if (!root) return Promise.resolve([]);
  const now = Date.now();
  if (!force && now - _lastRefreshAt < 1200) return Promise.resolve([]);
  _lastRefreshAt = now;
  const tasks = [
    runLoader('summary', loadSummary),
    runLoader('board', loadBoard),
    runLoader('limitStats', loadLimitStats),
    runLoader('gauge', loadGauge),
    runLoader('sysrisk', loadSysrisk),
    runLoader('mainlines', loadMainlines),
  ];
  _activeDeferred.forEach((key) => {
    const item = DEFERRED_LOADERS[key];
    if (item && (refreshDeep || item.refreshDuringTrading)) {
      tasks.push(runLoader(`deferred:${key}`, item.load));
    }
  });
  return Promise.allSettled(tasks);
}

// 情绪相位色（与 sentiment 口径一致，红涨绿跌外的语义色）
// 浅色面板上的情绪文字统一使用 WCAG AA 深色；色彩仍只表达冷暖，不复用涨跌色。
const PHASE_COLORS = { 冰点期: '#047857', 退潮期: '#0f766e', 分歧期: '#a16207', 修复期: '#c2410c', 发酵期: '#be123c', 高潮期: '#9f1239' };
// 风险 1-5 级配色：蓝(正常)→红(极端) 危险度斜坡，与 morning.js/tail.js 的 LV_COLOR 完全一致。
// 2026-07-11 审计 M5 修复：原用 var(--down)/var(--up) 复用价格红涨绿跌变量——同卡内"风险红=危险"
// 与"主线红=上涨"撞色歧义，且与盘前/尾盘页同一等级不同色。全站风险色以此为准。
const RISK_COLORS = { 1: '#1d4ed8', 2: '#0f766e', 3: '#a16207', 4: '#c2410c', 5: '#be123c' };

function injectStyle() {
  if (document.getElementById('cockpit2-style')) return;
  const css = `
  .ck2-sum{position:relative;border:1px solid var(--border-strong);border-radius:var(--radius);
    background:linear-gradient(100deg,color-mix(in srgb,var(--accent) 8%,var(--panel)),var(--panel) 60%);
    padding:15px 20px;margin-bottom:var(--gap);display:flex;align-items:center;gap:20px;flex-wrap:wrap}
  .ck2-sum::before{content:"";position:absolute;inset:0 auto 0 0;width:4px;background:var(--lv-c,var(--accent))}
  .ck2-sum-l{flex:1;min-width:300px}
  .ck2-sum-tag{display:inline-block;font-size:12px;font-weight:700;color:var(--accent);
    border:1px solid var(--accent);border-radius:4px;padding:1px 9px;margin-bottom:7px}
  .ck2-sum-txt{font-size:15px;line-height:1.55;color:var(--text);font-weight:600}
  .ck2-sum-r{display:flex;gap:22px}
  .ck2-metric{text-align:center}
  .ck2-metric .v{font-size:30px;font-weight:800;line-height:1;font-family:var(--mono,monospace)}
  .ck2-metric .k{font-size:12px;color:var(--text-3);margin-top:4px}

  .ck2-do{position:relative;border:1px solid var(--border-strong);border-radius:var(--radius);
    background:linear-gradient(100deg,color-mix(in srgb,var(--lv-c,var(--accent)) 8%,var(--panel)),var(--panel) 58%);
    padding:14px 18px 12px 22px;margin-bottom:var(--gap);box-shadow:var(--shadow-soft)}
  .ck2-do::before{content:"";position:absolute;inset:0 auto 0 0;width:4px;background:var(--lv-c,var(--accent))}
  .ck2-do-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:10px;flex-wrap:wrap}
  .ck2-do-title{font-size:17px;font-weight:760;line-height:1.3;color:var(--text);letter-spacing:.1px}
  .ck2-do-hr{display:inline-flex;align-items:center;gap:7px;flex-wrap:wrap}
  .ck2-do-badge,.ck2-risk-badge{display:inline-flex;align-items:center;min-height:24px;font-size:12px;font-weight:700;border:1px solid;border-radius:4px;padding:1px 8px}
  .ck2-do-badge{color:var(--accent);border-color:var(--accent)}
  .ck2-risk-badge{font-weight:750;background:var(--panel-2)}
  .ck2-do-chg{font-size:12px;font-weight:700;color:var(--amber);border:1px solid var(--amber-line,var(--amber));background:var(--amber-dim,transparent);border-radius:4px;padding:1px 8px;cursor:help;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ck2-lock{font-size:12px;color:var(--text-3);cursor:help}
  .ck2-do-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.8fr) minmax(0,.75fr) minmax(0,1.15fr);grid-template-areas:"state action position principle"}
  .ck2-do-cell{min-width:0;padding:8px 12px}
  .ck2-do-cell[data-kind="state"]{grid-area:state;padding-left:0}
  .ck2-do-cell[data-kind="action"]{grid-area:action;border-left:1px solid var(--border)}
  .ck2-do-cell[data-kind="position"]{grid-area:position;border-left:1px solid var(--border)}
  .ck2-do-cell[data-kind="principle"]{grid-area:principle;border-left:1px solid var(--border);padding-right:0}
  .ck2-do-cell .k{font-size:12px;line-height:1.35;color:var(--text-2);font-weight:650;margin-bottom:3px;letter-spacing:.1px}
  .ck2-do-cell .v{font-size:14px;font-weight:620;line-height:1.48;letter-spacing:0;color:var(--text);word-break:break-word;text-wrap:pretty}
  .ck2-do-cell[data-kind="state"] .v{font-size:15px;font-weight:680;line-height:1.48}
  .ck2-do-cell .why{font-size:12px;line-height:1.42;color:var(--text-3);margin-top:2px}

  /* 只把“今日怎么做”和“市场状态”并排。大盘与主线退出这张两列网格后全宽展示，
     右栏较短时不再把下面两张大卡旁边留成一整块空地。 */
  .ck2-overview-shell{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px,344px);gap:var(--gap);align-items:start;margin-bottom:var(--gap)}
  .ck2-overview-shell>#ck2-summary,.ck2-state-rail{width:100%;min-width:0}
  .ck2-overview-shell .ck2-do{height:auto;margin-bottom:0}
  .ck2-state-rail{align-self:start;overflow:hidden}
  .ck2-top-grid{display:block;margin-bottom:var(--gap)}
  .ck2-section-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin:22px 2px 10px}
  .ck2-section-head .t{font-size:16px;font-weight:750;color:var(--text)}
  .ck2-section-head .d{font-size:13px;color:var(--text-3)}
  .ck2-top-main{display:flex;flex-direction:column;gap:var(--gap);min-width:0}
  .ck2-top-row{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--gap);align-items:start}
  .ck2-limit-row{grid-template-columns:minmax(0,3fr) minmax(260px,1fr)}
  .ck2-top-row>.card{min-width:0}
  .ck2-top-row>.ck2-full{grid-column:1/-1}
  .ck2-mainline-card{margin-bottom:var(--gap)}
  .ck2-mainline-card .card-head{flex-wrap:wrap}
  #ck2-mainline-stamp{flex-wrap:wrap;justify-content:flex-end}
  .ck2-mainline-regime{display:inline-flex;align-items:center;min-height:22px;padding:1px 7px;font-size:12px;font-weight:700;line-height:1.45;white-space:nowrap;color:var(--amber);background:var(--amber-dim,transparent);border:1px solid var(--amber-line,var(--amber));border-radius:var(--radius-sm)}

  .ck2-idx-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
  .ck2-idx{border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel-2);padding:8px 10px;min-width:0}
  .ck2-idx .nm{font-size:12px;color:var(--text-3);display:flex;justify-content:space-between;gap:4px}
  .ck2-idx .px{font-size:17px;font-weight:800;font-family:var(--mono,monospace);margin-top:2px}
  .ck2-idx svg{display:block;width:100%;height:22px;margin-top:3px}
  .ck2-breadth{display:flex;align-items:center;gap:8px;margin-top:10px;font-size:13px}
  .ck2-breadth .bar{flex:1;height:14px;border-radius:7px;overflow:hidden;display:flex;background:var(--panel-2)}
  .ck2-breadth .bar i{display:block;height:100%}

  .ck2-ls{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
  .ck2-ls .cell{border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel-2);padding:9px 6px;text-align:center}
  .ck2-ls .cell .v{font-size:21px;font-weight:800;font-family:var(--mono,monospace)}
  .ck2-ls .cell .k{font-size:12px;color:var(--text-3);margin-top:2px}

  .ck2-gauge{height:170px}
  .ck2-gauge-cap{text-align:center;font-size:13px;color:var(--text-2);margin-top:-6px}
  .ck2-state-score-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px;align-items:stretch}
  .sr-hero,.ck2-emo2{height:100%;min-height:136px;border-radius:var(--radius-sm);padding:12px;background:var(--panel-2)}
  .sr-hero{border:1px solid color-mix(in srgb,var(--risk-c) 36%,var(--border));background:linear-gradient(140deg,color-mix(in srgb,var(--risk-c) 10%,var(--panel)),var(--panel-2))}
  .sr-hero-top{display:flex;flex-direction:column;align-items:flex-start;height:100%}
  .sr-hero-k{font-size:12px;color:var(--text-3);font-weight:650}
  .sr-hero-v{display:flex;align-items:baseline;gap:4px;margin-top:8px;color:var(--risk-c)}
  .sr-hero-v b{font-size:34px;line-height:1;font-weight:850;font-family:var(--mono,monospace)}
  .sr-hero-v span{font-size:13px;font-weight:750}
  .sr-hero-name{font-size:13px;line-height:1.35;font-weight:800;color:var(--risk-c);margin-top:5px}
  .sr-regime{display:inline-flex;margin-top:auto;font-size:12px;font-weight:700;color:var(--risk-c);border:1px solid color-mix(in srgb,var(--risk-c) 52%,var(--border));border-radius:4px;padding:2px 6px;background:var(--panel)}
  .ck2-state-overview{padding:10px 1px 11px;border-bottom:1px solid var(--border)}
  .sr-bars{display:grid;grid-template-columns:repeat(5,1fr);gap:5px;margin:0 0 8px}
  .sr-bars i{height:6px;border-radius:4px;background:var(--border)}
  .sr-hero-summary{font-size:13px;line-height:1.52;color:var(--text-2)}
  .sr-hero-meta{font-size:12px;line-height:1.45;color:var(--text-3);margin-top:4px}
  .ck2-emo2{display:flex;flex-direction:column;align-items:flex-start;border:1px solid color-mix(in srgb,var(--emo-c) 34%,var(--border));background:linear-gradient(140deg,color-mix(in srgb,var(--emo-c) 8%,var(--panel)),var(--panel-2))}
  .emo-k{font-size:12px;color:var(--text-3);font-weight:650;margin-bottom:1px}
  .emo-score{display:flex;align-items:baseline;gap:4px;margin-top:8px;color:var(--emo-c)}
  .emo-v{font-size:34px;font-weight:850;line-height:1;font-family:var(--mono,monospace)}
  .emo-unit{font-size:12px;font-weight:700}
  .emo-lb{font-size:13px;font-weight:800;margin-top:5px;color:var(--emo-c)}
  .emo-meter{width:100%;height:6px;border-radius:4px;background:var(--border);overflow:hidden;margin-top:auto}
  .emo-meter i{display:block;height:100%;min-width:5px;border-radius:inherit;background:var(--emo-c)}
  .emo-lock{font-size:12px;color:var(--text-3);cursor:help;line-height:1.35;margin-top:5px;white-space:nowrap}
  .sr-evidence-open{padding-top:9px}
  .sr-evidence-toggle{width:100%;min-height:34px;display:flex;align-items:center;justify-content:space-between;gap:10px;
    border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel-2);color:var(--text-2);
    font-size:13px;font-weight:700;padding:6px 10px;cursor:pointer;text-align:left}
  .sr-evidence-toggle:hover{border-color:var(--accent);color:var(--text)}
  .sr-evidence-toggle .hint{font-size:12px;font-weight:500;color:var(--text-3)}
  .sr-evidence-panel{padding-top:1px}
  .sr-evidence-panel[hidden]{display:none}
  .sr-head{display:flex;justify-content:space-between;align-items:center;margin:10px 0 9px}
  .sr-title{font-size:13px;font-weight:700;color:var(--text)}
  .sr-dims{display:flex;flex-direction:column;gap:7px}
  .sr-dim-h{display:flex;justify-content:space-between;align-items:baseline;font-size:13px}
  .sr-dim-n{color:var(--text-2);font-weight:600}
  .sr-c{font-weight:700;font-size:12px}
  .sr-dim-r{font-size:13px;color:var(--text-3);margin-top:1px;line-height:1.45}
  .sr-chg{font-size:12px;color:var(--amber);background:var(--amber-dim,transparent);border:1px solid var(--amber-line,var(--amber));border-radius:4px;padding:4px 9px;margin-bottom:8px;line-height:1.45}
  .sr-nar{font-size:12.5px;color:var(--text-2);line-height:1.65;margin-bottom:9px}
  .sr-nar b{color:var(--text);font-weight:700}

  .ck2-flowbar{display:flex;flex-direction:column;gap:9px}
  .ck2-flowbar .row{display:grid;grid-template-columns:84px 1fr 72px;align-items:center;gap:8px;font-size:13px}
  .ck2-flowbar .nm{color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ck2-flowbar .tk{height:13px;border-radius:7px;background:var(--up);opacity:.85}
  .ck2-flowbar .vv{text-align:right;font-family:var(--mono,monospace);font-weight:700}
  .fb-sec{margin-bottom:9px} .fb-sec:last-child{margin-bottom:0}
  .fb-h{font-size:12px;font-weight:700;margin-bottom:5px;font-family:var(--mono,monospace)}
  .fb-h.up{color:var(--up)} .fb-h.down{color:var(--down)}
  .fb-row{display:grid;grid-template-columns:72px 1fr 64px 50px 36px;align-items:center;gap:6px;padding:3px 0;font-size:13px}
  .fb-thead{font-size:12px;color:var(--text-3);border-bottom:1px solid var(--border);padding-bottom:3px;margin-bottom:2px}
  .fb-thead .fb-heat{cursor:help}
  .fb-nm{color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .fb-track{height:11px;background:var(--panel-2);border-radius:6px;overflow:hidden}
  .fb-tk{display:block;height:100%;border-radius:6px}
  .fb-tk.up{background:var(--up)} .fb-tk.down{background:var(--down)}
  .fb-vv{text-align:right;font-family:var(--mono,monospace);font-weight:700}
  .fb-vv.up{color:var(--up)} .fb-vv.down{color:var(--down)}
  .fb-pct{text-align:right;font-family:var(--mono,monospace);font-size:12px}
  .fb-heat{text-align:right;font-family:var(--mono,monospace);font-size:12px;font-weight:700}
  .fb-stale{font-size:12px;color:var(--amber);background:var(--amber-dim,transparent);border:1px solid var(--amber-line,var(--amber));border-radius:4px;padding:3px 8px;margin-bottom:6px;line-height:1.45}
  .fb-empty{font-size:12px;color:var(--text-3);padding:2px 0}

  .ck2-sf{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .ck2-compact-state{display:flex;align-items:flex-start;gap:10px;padding:11px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel-2)}
  .ck2-compact-state .mark{width:8px;height:8px;border-radius:50%;margin-top:6px;background:var(--amber);flex:0 0 auto}
  .ck2-compact-state .title{font-size:14px;font-weight:700;color:var(--text)}
  .ck2-compact-state .desc{font-size:13px;line-height:1.55;color:var(--text-3);margin-top:2px}
  .ck2-sf .col .h{font-size:12px;color:var(--text-3);margin-bottom:6px;font-family:var(--mono,monospace)}
  .ck2-sf .it{display:flex;justify-content:space-between;gap:8px;padding:5px 2px;border-bottom:1px solid var(--border);cursor:pointer;font-size:13px}
  .ck2-sf .it:last-child{border-bottom:none} .ck2-sf .it:hover{background:var(--panel-2)}
  .ck2-sf .it .nm{font-weight:600;color:var(--text)} .ck2-sf .it .vv{font-family:var(--mono,monospace);font-weight:700}

  .ck2-ml-withdraw{font-size:12px;line-height:1.5;color:var(--amber);background:var(--panel);border:1px dashed var(--amber-line,var(--amber));border-radius:5px;padding:6px 8px;margin:-2px 0 9px}
  .ck2-ml{display:grid;grid-template-columns:repeat(5,minmax(178px,1fr));gap:10px;overflow-x:auto;padding-bottom:2px}
  .ck2-ml .ml-card{display:flex;flex-direction:column;min-width:0;border:1px solid var(--border);border-top:3px solid var(--accent);border-radius:var(--radius-sm);background:var(--panel-2);padding:10px 11px}
  .ck2-ml .ml-card.role-main{border-top-color:var(--up)}
  .ck2-ml .ml-card.role-secondary{border-top-color:var(--amber)}
  .ck2-ml .ml-card.role-active{border-top-color:var(--accent)}
  .ck2-ml .ml-card.role-emotion{border-top-color:#8b5cf6}
  .ck2-ml .ml-card.unformed{border-top-color:var(--border);border-style:dashed;background:var(--panel)}
  .ck2-ml .ml-top{display:flex;align-items:center;justify-content:space-between;gap:6px;margin-bottom:5px}
  .ck2-ml .ml-kind{font-size:12px;font-weight:800;padding:1px 7px;border:1px solid currentColor;border-radius:3px;color:var(--accent)}
  .ck2-ml .role-main .ml-kind{color:var(--up)}
  .ck2-ml .role-secondary .ml-kind{color:var(--amber)}
  .ck2-ml .role-emotion .ml-kind{color:#8b5cf6}
  .ck2-ml .unformed .ml-kind{color:var(--text-3)}
  .ck2-ml .ml-rank{font-size:12px;color:var(--text-3);font-family:var(--mono,monospace)}
  .ck2-ml .ml-name{font-size:15px;font-weight:800;color:var(--text);margin:3px 0 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ck2-ml .unformed .ml-name{color:var(--text-3)}
  .ck2-ml .ml-note{min-height:38px;font-size:13px;color:var(--text-3);line-height:1.5;margin:5px 0 7px}
  .ck2-ml .ml-leader{font-size:12px;line-height:1.45;color:var(--text-2);padding:3px 0;margin:0 0 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ck2-ml .ml-leader b{color:var(--text);font-weight:700}
  .ck2-ml .ml-catalyst{font-size:12px;line-height:1.45;color:var(--text-3);background:var(--panel);border-left:2px solid var(--border);padding:4px 6px;margin:0 0 7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ck2-ml .ml-catalyst.confirmed{color:var(--text-2);border-left-color:var(--accent)}
  .ck2-ml .ml-proof{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0}
  .ck2-ml .ml-proof span{font-size:12px;color:var(--text-2);border:1px solid var(--border);background:var(--panel);border-radius:4px;padding:2px 5px}
  .ck2-ml .ml-gates{display:grid;grid-template-columns:1fr 1fr;gap:4px;margin:6px 0}
  .ck2-ml .ml-gates span{font-size:12px;color:var(--text-3);border:1px dashed var(--border);background:var(--panel-2);border-radius:4px;padding:3px 5px}
  .ck2-ml .ml-stk{display:flex;flex-wrap:wrap;gap:4px;margin-top:auto;padding-top:6px;border-top:1px solid var(--border)}
  .ck2-ml .ml-stk .s{max-width:100%;font-size:12px;padding:1px 6px;border-radius:4px;border:1px solid var(--border);background:var(--panel);cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ck2-ml .ml-stk .s:hover{border-color:var(--accent)}
  .ck2-ml .ml-empty-chain{font-size:12px;color:var(--text-3)}

  .ck2-down-list{display:flex;flex-direction:column}
  .ck2-down-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 8px;padding:7px 0;border-bottom:1px solid var(--border);cursor:default}
  .ck2-down-row[data-code]{cursor:pointer}
  .ck2-down-row:last-child{border-bottom:none}
  .ck2-down-row:hover{background:var(--panel-2)}
  .ck2-down-name{min-width:0;font-size:13px;font-weight:700;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ck2-down-pct{font-family:var(--mono,monospace);font-size:12px;font-weight:800;color:var(--down)}
  .ck2-down-meta{grid-column:1/-1;display:flex;justify-content:space-between;gap:8px;font-size:12px;color:var(--text-3)}
  .ck2-down-meta span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ck2-down-more{width:100%;appearance:none;border:0;border-top:1px dashed var(--border);background:transparent;
    margin-top:2px;padding:9px 4px 2px;text-align:center;font:inherit;font-size:12px;color:var(--accent);cursor:pointer}
  .ck2-down-more:hover{color:var(--text)}

  .ck2-risk{border:1px solid var(--down-line);background:var(--down-bg);border-radius:var(--radius);padding:13px 18px;margin-bottom:var(--gap)}
  .ck2-risk .h{font-weight:800;color:var(--down);margin-bottom:5px}
  .ck2-risk .b{font-size:14px;line-height:1.6;color:var(--text)}

  .ck2-heat{height:330px}
  /* 媒体查询看的是整个视口，还要扣掉约 232px 侧边栏。视口低于 1080px 时
     真实工作区已不足 850px，此时改成单列，避免窄屏仍保留大屏两列而产生拉伸和空白。 */
  @media (max-width:1280px){
    .ck2-do-grid{grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);grid-template-areas:"state position" "action principle"}
    .ck2-do-cell[data-kind="action"]{border-left:0;border-top:1px solid var(--border);padding-left:0}
    .ck2-do-cell[data-kind="position"],.ck2-do-cell[data-kind="principle"]{border-left:1px solid var(--border);padding-left:14px}
    .ck2-do-cell[data-kind="principle"]{border-top:1px solid var(--border)}
    .ck2-limit-row{grid-template-columns:minmax(0,2fr) minmax(260px,1fr)}
    .ck2-ml{grid-template-columns:repeat(6,minmax(0,1fr));overflow:visible}
    .ck2-ml .ml-card{grid-column:span 2}
    .ck2-ml .ml-card:nth-child(n+4){grid-column:span 3}
  }
  @media (max-width:900px){
    .ck2-overview-shell{grid-template-columns:1fr}
    .ck2-do-grid{grid-template-columns:1fr;grid-template-areas:"state" "action" "position" "principle"}
    .ck2-do-cell{padding:8px 0}
    .ck2-do-cell[data-kind="action"],.ck2-do-cell[data-kind="position"],.ck2-do-cell[data-kind="principle"]{border-left:0;border-top:1px solid var(--border);padding-left:0}
  }
  @media (max-width:760px){
    .ck2-mainline-card .card-head{flex-direction:column}
    #ck2-mainline-stamp{justify-content:flex-start;margin-left:14px}
    .ck2-sum-r{width:100%;justify-content:space-around}
    .ck2-do{padding:15px 15px 16px 19px}
    .ck2-do-head{align-items:flex-start}
    .ck2-do-hr{width:100%}
    .ck2-do-grid{grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-areas:"state state" "action action" "position principle"}
    .ck2-do-cell[data-kind="position"]{border-left:0;padding-left:0;padding-right:10px}
    .ck2-do-cell[data-kind="principle"]{border-left:1px solid var(--border);padding-left:10px}
    .ck2-do-cell .why{display:none}
    .ck2-do-cell .v,.ck2-do-cell[data-kind="state"] .v{font-size:15px;line-height:1.68}
    .ck2-top-row,.ck2-limit-row{grid-template-columns:1fr}
    .ck2-idx-grid,.ck2-ls{grid-template-columns:repeat(2,1fr)}
    .ck2-sf{grid-template-columns:1fr}
    .ck2-ml{grid-template-columns:1fr;overflow:visible}
    .ck2-ml .ml-card,.ck2-ml .ml-card:nth-child(n+4){grid-column:auto;min-width:0}
  }
  @media (max-width:420px){
    .ck2-do{padding:14px 14px 15px 17px}
    .ck2-do-title{font-size:16px}
  }`;
  const tag = document.createElement('style');
  tag.id = 'cockpit2-style';
  tag.textContent = css;
  document.head.appendChild(tag);
}

export function mount(el) {
  gen += 1;
  root = el;
  _emoCounted = false;   // count-up 一次性：每次进入页面重播一次，档内刷新不重播
  _downPoolExpanded = false;
  _riskEvidenceExpanded = false;
  injectStyle();
  root.innerHTML = `
    <!-- 首屏只并排两张同层决策卡；其余模块全宽向下流动，消灭固定右栏空洞。 -->
    <div class="ck2-overview-shell">
      <div id="ck2-summary">${skeleton(2, [52, 24])}</div>
      <aside class="card ck2-state-rail" id="ck2-emo"><div class="card-head"><span class="card-title">市场状态 · 系统性风险</span></div><div class="card-body"><div class="ck2-state-score-row"><div id="ck2-sysrisk-hero">${skeleton(1)}</div><div id="ck2-emo-body">${skeleton(1)}</div></div><div id="ck2-sysrisk-overview" class="ck2-state-overview"></div><div class="sr-evidence-open"><button class="sr-evidence-toggle" id="ck2-sysrisk-toggle" type="button" aria-expanded="false"><span>展开五项风险依据</span><span class="hint">大盘 · 资金 · 情绪 · 消息 · 海外</span></button><div id="ck2-sysrisk-body" class="sr-evidence-panel" hidden></div></div></div></aside>
    </div>
    <div class="card" id="ck2-board"><div class="card-head"><span class="card-title">大盘 / 市场环境 · A股</span><span class="card-extra" id="ck2-board-stamp"></span></div><div class="card-body" id="ck2-board-body">${skeleton(4)}</div></div>
    <div class="card ck2-mainline-card" id="ck2-mainline"><div class="card-head"><span class="card-title" id="ck2-mainline-title">今日行业主线 · 主线 / 次主线 / 活口 / 情绪链</span><span class="card-extra" id="ck2-mainline-stamp"></span></div><div class="card-body" id="ck2-mainline-body">${skeleton(2)}</div></div>
    <div class="ck2-section-head"><span class="t">盘面证据</span><span class="d">报价快更；风险与指令按定格时点保持稳定</span></div>
    <div class="ck2-top-grid">
      <div class="ck2-top-main">
        <div class="ck2-top-row ck2-limit-row">
          <div class="card" id="ck2-limit"><div class="card-head"><span class="card-title">涨跌停情况</span><span class="card-extra" id="ck2-limit-stamp"></span></div><div class="card-body" id="ck2-limit-body">${skeleton(3)}</div></div>
          <div class="card" id="ck2-downpool"><div class="card-head"><span class="card-title">跌停风险池</span><span class="card-extra" id="ck2-downpool-stamp"></span></div><div class="card-body" id="ck2-downpool-body">${skeleton(6)}</div></div>
        </div>
        <div class="ck2-top-row">
          <div class="card ck2-full" id="ck2-secflow"><div class="card-head"><span class="card-title">行业资金 · 净流入/净流出</span><span class="card-extra" id="ck2-secflow-stamp"></span></div><div class="card-body" id="ck2-secflow-body" data-cockpit-lazy="sectorFlow">${skeleton(5)}</div></div>
        </div>
        <div class="ck2-top-row">
          <div class="card ck2-full" id="ck2-stkflow"><div class="card-head"><span class="card-title">个股资金 · 净流入/净流出</span><span class="card-extra" id="ck2-stkflow-stamp"></span></div><div class="card-body" id="ck2-stkflow-body" data-cockpit-lazy="stockFlow">${skeleton(8)}</div></div>
        </div>
      </div>
    </div>
    <div class="ck2-section-head"><span class="t">深度结构</span><span class="d">用于复核题材热度与多周期持续性</span></div>
    <div class="grid12">
      <div class="card sp-12" id="ck2-heat"><div class="card-head"><span class="card-title">题材热力图 · 面积=成交 · 颜色=涨跌</span></div><div class="card-body" style="padding:6px" data-cockpit-lazy="heatmap"><div class="ck2-heat" id="ck2-heat-chart"></div></div></div>
      <div class="card sp-12" id="ck2-multi"><div class="card-head"><span class="card-title">多周期涨幅榜 TOP</span><span class="card-extra" id="ck2-multi-stamp"></span></div><div class="card-body" id="ck2-multi-body" style="max-height:480px;overflow-y:auto" data-cockpit-lazy="multiperiod">${skeleton(6)}</div></div>
    </div>
    `;

  _activeDeferred.clear();
  _loaderInFlight.clear();
  _requestInFlight.clear();
  _lastRefreshAt = 0;
  _sparksLoaded = false;
  refreshCockpit({ force: true });
  // 先给首屏确定性指令/风险/大盘一个发请窗口，再开始观察下方重卡。
  // 否则骨架高度较短时，520px 预取边界会把四个延迟卡全部误判为「即将进入」。
  _deferredSetupTimer = setTimeout(() => {
    _deferredSetupTimer = null;
    if (root) setupDeferredLoading();
  }, 1000);

  timers.push(setInterval(() => {
    if (!isTradingTime()) return;
    refreshCockpit();
  }, 30000));
  // 盘中指数板 ~3s 跳动（轻量源，只跳指数点位/%；其余环境统计仍 30s，不额外打慢源）
  timers.push(setInterval(() => {
    if (isTradingTime()) runLoader('boardIndices', refreshBoardIndices);
  }, 3000));

  // 后台标签页自愈：Chrome 对长期后台 tab 会节流/冻结 setInterval——用户上午挂后台、
  // 下午切回时页面还停在冻结前的旧数据(看着像"还是昨天的")；若切回时已收盘,
  // isTradingTime 门槛还会让它永远停更。切回可见 → 无条件强刷一轮(不看交易时段)。
  _visHandler = () => {
    if (document.visibilityState !== 'visible' || !root) return;
    if (_visTimer) clearTimeout(_visTimer);
    _visTimer = setTimeout(() => {
      _visTimer = null;
      if (document.visibilityState === 'visible' && root) refreshCockpit({ refreshDeep: true });
    }, 120);
  };
  document.addEventListener('visibilitychange', _visHandler);
}

export function unmount() {
  timers.forEach(clearInterval);
  timers = [];
  if (_visTimer) { clearTimeout(_visTimer); _visTimer = null; }
  if (_deferredSetupTimer) { clearTimeout(_deferredSetupTimer); _deferredSetupTimer = null; }
  if (_summaryEnhanceTimer) { clearTimeout(_summaryEnhanceTimer); _summaryEnhanceTimer = null; }
  if (_sparkTimer) { clearTimeout(_sparkTimer); _sparkTimer = null; }
  if (_visHandler) { document.removeEventListener('visibilitychange', _visHandler); _visHandler = null; }
  if (_deferredObserver) { _deferredObserver.disconnect(); _deferredObserver = null; }
  _activeDeferred.clear();
  _loaderInFlight.clear();
  _requestInFlight.clear();
  _lastRefreshAt = 0;
  _sparksLoaded = false;
  disposeCharts(charts); charts = [];
  heatChart = null;
  root = null;
}

function gotoDiag(code) {
  if (!code) return;
  sessionStorage.setItem('hermes.diagnosis.code', code);
  location.hash = '#/diagnosis';
}
function bindChips(scope) {
  scope.querySelectorAll('[data-code]').forEach((c) => c.addEventListener('click', () => gotoDiag(c.dataset.code)));
}

/* ========== 今日该怎么做（T9 §1 行动条：定调/动作/仓位/原则；风险等级集中在标题区）==========
   行业主线由下方独立结构卡完整展示，顶部不再重复同一结论，也不额外请求一次主线端点。 */
async function loadSummary() {
  const host = root?.querySelector('#ck2-summary');
  if (!host) return;
  const g = gen;
  // /cockpit 可能等待模型叙述。必须先让快速 /pulse.command 完成首帧，
  // 再延迟启动可选增强；不让慢模型请求抢在行情/风险前面占住单进程。
  let ck = null;
  const p = await getBounded('/pulse', 6000).catch(() => null);
  if (g !== gen || !root) return;

  const render = () => {
    if (g !== gen || !root || !host.isConnected) return;
    const cmd = ck?.command || p?.command || {};
    const hasDecision = Object.keys(cmd).length > 0 && (cmd.risk_level != null || cmd.today_action || cmd.trade_state);
    if (!hasDecision) {
      host.innerHTML = `
        <div class="ck2-do" style="--lv-c:var(--amber)">
          <div class="ck2-do-head"><span class="ck2-do-title">今日该怎么做</span>
            <span class="ck2-do-hr"><span class="ck2-risk-badge" style="color:var(--amber);border-color:var(--amber)">数据连接中断</span></span>
          </div>
          <div class="ck2-do-grid">
            <div class="ck2-do-cell" data-kind="state"><div class="k">当前状态</div><div class="v">关键决策数据未连接，本卡暂停生成新结论。</div></div>
            <div class="ck2-do-cell" data-kind="action"><div class="k">现在怎么做</div><div class="v">先恢复行情与风险数据，再依据最新时点重新确认动作。</div></div>
            <div class="ck2-do-cell" data-kind="position"><div class="k">仓位边界</div><div class="v">不依据缺失数据新增或补仓</div></div>
            <div class="ck2-do-cell" data-kind="principle"><div class="k">执行原则</div><div class="v">数据质量不足时不强行给方向</div></div>
          </div>
        </div>`;
      return;
    }
    const regime = cmd.regime || p?.regime || '';
    const level = p?.risk_level ?? cmd.risk_level;
    const levelName = p?.risk_label || cmd.level_name || '';
    const lvColor = RISK_COLORS[level] || 'var(--accent)';
    // 第一屏严格按“状态 → 唯一主动作 → 仓位 → 执行原则”组织。
    const stateTxt = escapeHtml(cmd['定调叙述'] || cmd.trade_state || regime || cmd.level_name || '状态待确认');
    const actionTxt = escapeHtml(cmd.today_action || cmd.level_text || '等待可验证条件');
    const actionWhy = cmd.level_text && cmd.level_text !== cmd.today_action ? escapeHtml(cmd.level_text) : '';
    const posTxt = escapeHtml(cmd.position_cap || '—');
    const principleTxt = escapeHtml(cmd.first_principle || '按价格条件与风险边界执行');
    const tradeable = cmd.decision_tradeable !== false;
    const actionLabel = escapeHtml(cmd.action_label || '今日唯一主动作');
    const riskTxt = level != null ? `${level} 级 · ${escapeHtml(levelName)}${tradeable ? '' : '（参考）'}` : '—';
    const cell = (kind, k, v, cls, why = '') => `<div class="ck2-do-cell" data-kind="${kind}"><div class="k">${k}</div><div class="v ${cls || ''}">${v}</div>${why ? `<div class="why">${why}</div>` : ''}</div>`;
    // 观点惯性（T9 §0-3/§1）：change 非空 → 🔁 徽章(hover 看全文="较上一版 XX→YY,原因")；定格时点=诚实标注非实时
    const chg = cmd.change || p?.change || '';
    const lockAt = cmd['定格时点'] || ck?.['定格时点'] || p?.['定格时点'] || '';
    host.innerHTML = `
      <div class="ck2-do" style="--lv-c:${lvColor}">
        <div class="ck2-do-head"><span class="ck2-do-title">今日该怎么做</span>
          <span class="ck2-do-hr">
            ${chg ? `<span class="ck2-do-chg" title="${escapeHtml(chg)}">较上一档：${escapeHtml(chg.length > 24 ? chg.slice(0, 24) + '…' : chg)}</span>` : ''}
            ${regime ? `<span class="ck2-do-badge" style="color:${lvColor};border-color:${lvColor}">${escapeHtml(regime)}</span>` : ''}
            <span class="ck2-risk-badge" style="color:${lvColor};border-color:${lvColor}">风险 ${riskTxt}</span>
            ${!tradeable && cmd.baseline_data_date ? `<span class="ck2-lock" title="当前是参考态，不是可执行交易指令">基线 ${escapeHtml(cmd.baseline_data_date)}</span>` : ''}
            ${lockAt ? `<span class="ck2-lock" title="总指令在此时点定格、10分钟档内不变、非实时">定格 ${escapeHtml(lockAt)}</span>` : ''}
          </span></div>
        <div class="ck2-do-grid">
          ${cell('state', '当前状态', stateTxt)}
          ${cell('action', actionLabel, actionTxt, '', actionWhy)}
          ${cell('position', '仓位边界', posTxt)}
          ${cell('principle', '执行原则', principleTxt)}
        </div>
      </div>`;
  };

  render();
  if (_summaryEnhanceTimer) clearTimeout(_summaryEnhanceTimer);
  _summaryEnhanceTimer = setTimeout(() => {
    _summaryEnhanceTimer = null;
    if (g !== gen || !root || !host.isConnected) return;
    runLoader('summaryEnhancement', async () => {
      const value = await getBounded('/cockpit', 6000).catch(() => null);
      if (g !== gen || !root || !host.isConnected || !value) return;
      ck = value;
      render();
    });
  }, 900);
}

/* ========== 盘型/环境（指数 + sparkline + 涨跌分布） ========== */
const SPARK_IDX = [
  ['上证指数', 'sh000001'], ['深证成指', 'sz399001'], ['创业板指', 'sz399006'],
  ['科创50', 'sh000688'], ['北证50', 'bj899050'], ['沪深300', 'sh000300'],
];

// overview.missing 中既有真缺失，也有“乐咕宽度/指数口径成交额/免费涨停池”这类口径说明。
// 大盘卡的可用性必须由它实际展示的关键字段判断，不能因备用口径误报“数据不全”。
function boardDataQuality(ov) {
  const indexes = new Map((ov?.indexes || []).map((x) => [x.name, x]));
  const missing = [];
  SPARK_IDX.forEach(([name]) => {
    const row = indexes.get(name);
    if (row?.price == null || row?.change_pct == null) missing.push(`${name}行情`);
  });
  if (ov?.up_count == null || ov?.down_count == null || ov?.flat_count == null) {
    missing.push('涨跌家数');
  }
  return {
    missing,
    notes: Array.isArray(ov?.missing) ? ov.missing.filter(Boolean) : [],
  };
}
// 迷你线颜色按「当日涨跌方向」（红涨绿跌），不再按30日首末——否则指数当日红了、线却绿（图1 bug）。
function dirColor(chg) { return chg == null ? 'var(--flat)' : chg >= 0 ? 'var(--up)' : 'var(--down)'; }
function sparkSvg(closes, col) {
  if (!closes || closes.length < 2) return '';
  const w = 100, h = 22, n = closes.length;
  const min = Math.min(...closes), max = Math.max(...closes), rng = (max - min) || 1;
  const pts = closes.map((c, i) => `${(i / (n - 1) * w).toFixed(1)},${(h - (c - min) / rng * h).toFixed(1)}`).join(' ');
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${col || 'var(--flat)'}" stroke-width="1.4"/></svg>`;
}
async function loadBoard() {
  const body = root?.querySelector('#ck2-board-body');
  if (!body) return;
  const g = gen;
  const ov = await getBounded('/market/overview_cached', 4000).catch(() => null);
  if (g !== gen || !root) return;
  const previousSparks = new Map(
    [...body.querySelectorAll('.spark[data-nm]')].map((node) => [node.dataset.nm, node.innerHTML]),
  );
  const idxMap = new Map((ov?.indexes || []).map((x) => [x.name, x]));
  const stamp = root.querySelector('#ck2-board-stamp');
  if (stamp && ov) {
    const quality = boardDataQuality(ov);
    stamp.innerHTML = dataStamp(ov.ts || ov.data_date, null, {
      data_state: quality.missing.length ? 'missing' : 'complete',
      state_label: quality.missing.length ? '暂不可用' : '决策可用',
      missing_fields: quality.missing,
      notes: quality.notes,
    });
  }
  const up = ov?.up_count, down = ov?.down_count, flat = ov?.flat_count || 0;
  const tot = (up != null && down != null) ? up + down + flat : 0;
  body.innerHTML = `
    <div class="ck2-idx-grid">${SPARK_IDX.map(([nm]) => {
      const x = idxMap.get(nm) || {};
      const cls = pctClass(x.change_pct);
      return `<div class="ck2-idx" data-idx="${escapeHtml(nm)}">
        <div class="nm"><span>${escapeHtml(nm)}</span><span class="${cls}" data-cell="pct">${fmtPct(x.change_pct)}</span></div>
        <div class="px ${cls}" data-cell="px">${x.price != null ? fmtNum(x.price, 2) : '—'}</div>
        <div class="spark" data-nm="${escapeHtml(nm)}">${previousSparks.get(nm) || ''}</div>
      </div>`;
    }).join('')}</div>
    ${tot ? `<div class="ck2-breadth"><span class="up mono">↑${fmtNum(up)}</span>
      <span class="bar"><i class="b-up" style="width:${(up / tot * 100).toFixed(1)}%;background:var(--up)"></i><i style="width:${(flat / tot * 100).toFixed(1)}%;background:var(--flat)"></i><i class="b-down" style="width:${(down / tot * 100).toFixed(1)}%;background:var(--down)"></i></span>
      <span class="down mono">${fmtNum(down)}↓</span></div>`
    : '<div style="margin-top:8px;color:var(--text-3);font-size:13px">涨跌分布：暂不可用</div>'}`;
  // daily sparkline 是非关键视觉增强：首帧后串行补齐，不再同时抢 6 个日线端点。
  // 30s 大盘刷新保留已渲染 SVG，页面生命周期内只补一次。
  if (!_sparksLoaded && !_sparkTimer) {
    _sparkTimer = setTimeout(() => {
      _sparkTimer = null;
      if (!_sparksLoaded) runLoader('boardSparklines', loadBoardSparklines);
    }, 2500);
  }
}

async function loadBoardSparklines() {
  const g = gen;
  for (const [nm, full] of SPARK_IDX) {
    const d = await getBounded(`/index/${full}`, 12000).catch(() => null);
    if (g !== gen || !root) return;
    const closes = (d?.daily || []).slice(-30).map((b) => b.c).filter((c) => c != null);
    const card = [...root.querySelectorAll('#ck2-board-body .ck2-idx')].find((node) => node.dataset.idx === nm);
    const slot = card?.querySelector('.spark');
    const pct = card?.querySelector('[data-cell="pct"]');
    const color = pct?.classList.contains('up')
      ? 'var(--up)'
      : pct?.classList.contains('down') ? 'var(--down)' : 'var(--flat)';
    if (slot && closes.length > 1) slot.innerHTML = sparkSvg(closes, color);
  }
  if (g === gen && root) _sparksLoaded = true;
}

// 盘中指数板 ~3s 跳动：只拉 /market/indices 轻量源，就地更新各指数 点位/% (点位滚动+红涨绿跌闪)，
// 不重画（保留 sparkline）、不打 overview 慢源。指数板不存在时(未渲染)直接跳过。
async function refreshBoardIndices() {
  const body = root?.querySelector('#ck2-board-body');
  if (!body || !body.querySelector('.ck2-idx')) return;
  const d = await getBounded('/market/indices', 6000).catch(() => null);
  if (!root || !d?.indexes?.length) return;
  const byName = new Map(d.indexes.map((x) => [x.name, x]));
  body.querySelectorAll('.ck2-idx').forEach((card) => {
    const x = byName.get(card.dataset.idx);
    if (!x) return;
    const cls = pctClass(x.change_pct);
    const pct = card.querySelector('[data-cell="pct"]');
    if (pct) { pct.classList.remove('up', 'down', 'flat'); pct.classList.add(cls); pct.textContent = fmtPct(x.change_pct); }
    const px = card.querySelector('[data-cell="px"]');
    if (px && x.price != null) { px.classList.remove('up', 'down', 'flat'); px.classList.add(cls); tickNum(px, x.price, (n) => fmtNum(n, 2)); }
    const spk = card.querySelector('.spark polyline');   // 迷你线颜色随当日方向同步(否则跨零轴时线与数字撞色)
    if (spk) spk.setAttribute('stroke', dirColor(x.change_pct));
  });
}

/* ========== 涨跌停情况 ========== */
async function loadLimitStats() {
  const body = root?.querySelector('#ck2-limit-body');
  if (!body) return;
  const g = gen;
  const [ov, se] = await Promise.all([
    getBounded('/market/overview_cached', 4000).catch(() => null),
    getBounded('/sentiment/today', 6000).catch(() => null),
  ]);
  if (g !== gen || !root) return;
  const m = se?.metrics || {};
  const stamp = root.querySelector('#ck2-limit-stamp');
  // 1 位小数：与情绪面板同精度（0 位会把 2.2万亿 舍成 2万亿，两处显示"打架"像数据错误）
  if (stamp && ov) stamp.innerHTML = `两市成交 ${ov.total_amount != null ? fmtMoney(ov.total_amount, 1) : '—'}`;
  const cell = (k, v, cls = '') => `<div class="cell"><div class="v ${cls}">${v}</div><div class="k">${k}</div></div>`;
  const n = (x) => x != null ? fmtNum(x) : '—';
  // T-1 徽标：今日免费公开盘后数据尚未更新时，连板/炸板退上一交易日并【醒目标注】，
  // 绝不当今日实时(铁律：标注即诚实降级)。struct_stale_date/zhaban_stale_date 由后端给出(ISO 日期)。
  const tb = (iso) => iso ? ` <span style="font-size:12px;color:var(--amber);border:1px solid var(--amber);border-radius:3px;padding:0 4px;font-weight:700;vertical-align:middle">昨${String(iso).slice(5)}</span>` : '';
  body.innerHTML = `<div class="ck2-ls">
    ${cell('正式涨停', n(ov?.limit_up), 'up')}
    ${cell('炸板数', n(ov?.zhaban_count) + tb(ov?.zhaban_stale_date), 'amber')}
    ${cell('跌停', n(ov?.limit_down), 'down')}
    ${cell('炸板率', (ov?.zhaban_rate != null ? fmtNum(ov.zhaban_rate, 1) + '%' : '—') + tb(ov?.zhaban_rate_stale_date), 'amber')}
    ${cell('晋级率', (ov?.promote_rate != null ? fmtNum(ov.promote_rate, 1) + '%' : '—') + tb(ov?.promote_stale_date))}
    ${cell('最高连板', (ov?.lianban_height != null ? ov.lianban_height + '板' : '—') + tb(ov?.lianban_stale_date), 'up')}
    ${cell('封板成交', (ov?.limit_up_amount != null ? fmtMoney(ov.limit_up_amount, 0) : '—') + tb(ov?.struct_stale_date))}
    ${cell('昨涨停溢价', m.yesterday_limit_premium != null ? fmtPct(m.yesterday_limit_premium) : '—', pctClass(m.yesterday_limit_premium))}
    ${cell('涨跌比', m.up_down_ratio != null ? fmtNum(m.up_down_ratio, 2) : '—')}
  </div>`;
}

/* ========== 情绪仪表盘 ========== */
// 情绪温度分档配色（热=红/冷=绿蓝，与 senti_label 一致）
function sentiColor(v) {
  if (v >= 75) return 'var(--up)';        // 亢奋
  if (v >= 58) return '#c2410c';          // 偏强
  if (v >= 42) return 'var(--amber)';     // 中性
  if (v >= 25) return '#0f766e';          // 偏弱
  return 'var(--down)';                   // 冰点
}

// 情绪周期（T9 §2 温度计）：读 /pulse.senti（纯公式·唯一真值源·10分钟档定格）。
// 涨跌家数/涨跌停/上涨占比已在左侧两卡完整出现，这里删除重复统计，把系统风险直接上移。
let _emoCounted = false;
async function loadGauge() {
  const body = root?.querySelector('#ck2-emo-body');
  if (!body) return;
  const g = gen;
  const p = await getBounded('/pulse', 6000).catch(() => null);
  if (g !== gen || !root) return;
  const senti = p?.senti;
  if (senti == null) { body.innerHTML = emptyState('情绪温度暂不可用'); return; }
  const col = sentiColor(senti);
  const lockAt = p?.['定格时点'] || '';
  body.innerHTML = `
    <div class="ck2-emo2" style="--emo-c:${col}">
      <div class="emo-k">情绪温度</div>
      <div class="emo-score"><span class="emo-v">${senti}</span><span class="emo-unit">分</span></div>
      <div class="emo-lb">${escapeHtml(p.senti_label || '')}</div>
      <div class="emo-meter" aria-hidden="true"><i style="width:${Math.max(0, Math.min(100, Number(senti) || 0))}%"></i></div>
      ${lockAt ? `<div class="emo-lock" title="情绪评分在此时点定格、10分钟档内不变、非实时（纯公式可复现）">定格 ${escapeHtml(lockAt)} · 纯公式</div>` : ''}
    </div>`;
  // count-up 一次性（T9 §2）：仅本次进入页面后的首帧从 0 滚到值；档内/后续刷新不重播。
  if (!_emoCounted) {
    _emoCounted = true;
    const el = body.querySelector('.emo-v');
    if (el) {
      const t0 = performance.now(), dur = 550;
      const step = (now) => {
        const k = Math.min(1, (now - t0) / dur);
        el.textContent = Math.round(senti * k);
        if (k < 1 && root) requestAnimationFrame(step);
        else el.textContent = senti;
      };
      requestAnimationFrame(step);
    }
  }
}

// 历史定格快照不可回写；仅在展示层把旧版笼统缺口文案翻译成字段级状态与决策影响。
function preciseRiskReading(value) {
  const text = String(value || '').trim();
  const legacy = {
    '主力净额数据不全': '主力净额本次未取得 · 不参与本次风险定级',
    '指数数据不全': '指数行情本次未取得 · 技术面不参与本次风险定级',
    '情绪数据不全': '情绪温度本次未计算 · 不展示情绪结论',
    '美股隔夜数据不全': '隔夜美股行情本次未取得 · 海外维度不参与本次风险定级',
  };
  return legacy[text] || text || '—';
}

// 系统性风险五维（T9 §3）：读 /sysrisk（五维真实读数 + 贡献 + 等级确定性），等级/regime 复用 risk_command。
async function loadSysrisk() {
  const hero = root?.querySelector('#ck2-sysrisk-hero');
  const overview = root?.querySelector('#ck2-sysrisk-overview');
  const body = root?.querySelector('#ck2-sysrisk-body');
  const toggle = root?.querySelector('#ck2-sysrisk-toggle');
  if (!hero || !overview || !body) return;
  const g = gen;
  const d = await getBounded('/sysrisk', 6000).catch(() => null);
  if (g !== gen || !root) return;
  if (!d || d.level == null) {
    hero.innerHTML = '<div style="font-size:13px;color:var(--text-3)">系统性风险暂不可用</div>';
    overview.innerHTML = '';
    body.innerHTML = '';
    body.hidden = true;
    if (toggle) toggle.hidden = true;
    return;
  }
  if (toggle) toggle.hidden = false;
  const cc = { '高': 'var(--up)', '中性': 'var(--amber)', '低': 'var(--down)' };
  const lockAt = d['定格时点'] || '';
  const level = Number(d.level);
  const riskColor = RISK_COLORS[level] || 'var(--accent)';
  const bars = [1, 2, 3, 4, 5].map((n) => `<i title="${n}级" style="${n <= level ? `background:${RISK_COLORS[n]}` : ''}"></i>`).join('');
  hero.innerHTML = `
    <div class="sr-hero" style="--risk-c:${riskColor}">
      <div class="sr-hero-top">
        <div class="sr-hero-k">系统风险</div>
        <div class="sr-hero-v"><b>${level}</b><span>级</span></div>
        <div class="sr-hero-name">${escapeHtml(d.level_name || '')}</div>
        ${d.regime ? `<span class="sr-regime">${escapeHtml(d.regime)}</span>` : ''}
      </div>
    </div>`;
  overview.innerHTML = `
      <div class="sr-bars" aria-label="当前风险等级 ${level} 级">${bars}</div>
      <div class="sr-hero-summary">${escapeHtml(d.summary || d.level_text || '等待风险依据')}</div>
      ${d.risk_score != null ? `<div class="sr-hero-meta">定级输入：综合情绪评分 ${escapeHtml(d.risk_score)}（五因子）</div>` : ''}
    `;

  // 观点惯性 change（等级/状态/情绪较上一档变了才有）。
  const chgLine = d.change
    ? `<div class="sr-chg" title="${escapeHtml(d.change)}">🔁 ${escapeHtml(d.change)}</div>` : '';
  // AI 叙述段（T9 §3「过程+结果」）：盘面推理(为什么是这个性质) + 操作结论(落到动作)。
  // AI 未就绪/失败 → 无此段，五维读数照常（绝不空窗）。
  const nar = d.narrative || null;
  const narBlock = (nar && (nar['盘面推理'] || nar['操作结论']))
    ? `<div class="sr-nar">${escapeHtml(nar['盘面推理'] || '')}${nar['操作结论'] ? ` <b>操作结论：${escapeHtml(nar['操作结论'])}</b>` : ''}</div>` : '';
  body.innerHTML = `
    <div class="sr-head"><span class="sr-title">系统性风险依据${lockAt ? ` <span class="ck2-lock" title="等级在此时点定格、10分钟档内不变、相对上一档最多±1级(黑天鹅例外)">定格 ${escapeHtml(lockAt)}</span>` : ''}</span></div>
    ${chgLine}
    ${narBlock}
    <div class="sr-dims">${(d.dims || []).map((x) => `
      <div class="sr-dim">
        <div class="sr-dim-h"><span class="sr-dim-n">${escapeHtml(x.dim)}</span><span class="sr-c" style="color:${cc[x.contrib] || 'var(--text-2)'}">${escapeHtml(x.contrib)}</span></div>
        <div class="sr-dim-r">${escapeHtml(preciseRiskReading(x.reading))}</div>
      </div>`).join('')}</div>`;
  body.hidden = !_riskEvidenceExpanded;
  if (toggle) {
    toggle.setAttribute('aria-expanded', String(_riskEvidenceExpanded));
    toggle.querySelector('span').textContent = _riskEvidenceExpanded ? '收起五项风险依据' : '展开五项风险依据';
    toggle.onclick = () => {
      _riskEvidenceExpanded = !_riskEvidenceExpanded;
      body.hidden = !_riskEvidenceExpanded;
      toggle.setAttribute('aria-expanded', String(_riskEvidenceExpanded));
      toggle.querySelector('span').textContent = _riskEvidenceExpanded ? '收起五项风险依据' : '展开五项风险依据';
    };
  }
}

/* ========== 行业资金 · 净流入/净流出双向柱状图（T9 §4）========== */
async function loadSectorFlow() {
  const body = root?.querySelector('#ck2-secflow-body');
  if (!body) return;
  const g = gen;
  const fl = await getBounded('/market/flow', 15000).catch(() => null);
  if (g !== gen || !root) return;
  const all = fl?.main_flow_sectors || [];
  const stamp = root.querySelector('#ck2-secflow-stamp');
  if (stamp && fl) stamp.innerHTML = dataStamp(fl);
  if (!all.length) { body.innerHTML = emptyState('行业资金暂不可用', '公开资金源尚未返回可用的行业排名'); return; }
  const inflow = all.filter((s) => (s.main_net || 0) > 0).sort((a, b) => b.main_net - a.main_net).slice(0, 12);
  const outflow = all.filter((s) => (s.main_net || 0) < 0).sort((a, b) => a.main_net - b.main_net).slice(0, 8);
  // 全体 |净额| 归一化（同一比例尺 → 进出直接可比，T9 §4）
  const maxAbs = Math.max(1, ...all.map((s) => Math.abs(s.main_net || 0)));
  // 热度 = clamp(0,1, |净额|/mx*0.7 + max(0,涨幅)/10*0.3)：资金为主(70%)+日内涨幅为辅(30%)，跌不加分
  const heat = (s) => Math.max(0, Math.min(1, Math.abs(s.main_net || 0) / maxAbs * 0.7 + Math.max(0, s.change_pct || 0) / 10 * 0.3));
  // T9 §4 五列：板块名 | 柱(红/绿) | 主力净额 | 涨跌幅(2位) | 热度(0~1,2位)——扫热度列即锁定当日主战场
  // （热度高+涨=大资金真攻；净额大但跌=跌中吸筹/出逃，热度不加分）。柱透明度也随热度，双编码。
  const bar = (s, dir) => `<div class="fb-row" title="${escapeHtml(s.name)} ${fmtMoney(s.main_net, 1)} · 涨幅${fmtPct(s.change_pct)} · 热度${heat(s).toFixed(2)}">
      <span class="fb-nm">${escapeHtml(s.name)}</span>
      <span class="fb-track"><i class="fb-tk ${dir}" style="width:${Math.max(4, Math.abs(s.main_net) / maxAbs * 100).toFixed(0)}%;opacity:${(0.4 + heat(s) * 0.6).toFixed(2)}"></i></span>
      <span class="fb-vv ${dir}">${fmtMoney(s.main_net, 1)}</span>
      <span class="fb-pct ${pctClass(s.change_pct)}">${fmtPct(s.change_pct)}</span>
      <span class="fb-heat" style="color:${heat(s) >= 0.55 ? 'var(--amber)' : 'var(--text-3)'}">${heat(s).toFixed(2)}</span></div>`;
  const head = `<div class="fb-row fb-thead"><span class="fb-nm">板块</span><span></span><span class="fb-vv">净额</span><span class="fb-pct">涨跌</span><span class="fb-heat" title="热度=资金占比70%+日内涨幅30%，跌不加分；扫这列锁定当日主战场">热度</span></div>`;
  // 断供保险（T9 §4）：上游采集失败沿用上次成功值时，明标"沿用 XX 时点"，绝不冒充最新
  const staleNote = (fl.stale && fl.note) ? `<div class="fb-stale">⚠ ${escapeHtml(fl.note)}</div>` : '';
  body.innerHTML = staleNote + head + `
    <div class="fb-sec"><div class="fb-h up">净流入 ${inflow.length}</div>${inflow.map((s) => bar(s, 'up')).join('') || '<div class="fb-empty">无净流入行业</div>'}</div>
    <div class="fb-sec"><div class="fb-h down">净流出 ${outflow.length}</div>${outflow.map((s) => bar(s, 'down')).join('') || '<div class="fb-empty">无净流出行业</div>'}</div>`;
}

/* ========== 个股资金 净流入/净流出 ========== */
async function loadStockFlow() {
  const body = root?.querySelector('#ck2-stkflow-body');
  if (!body) return;
  const g = gen;
  // 6 行在桌面卡内留下大段空白；取 10 行保持可扫读密度，仍远低于后端 30 行上限。
  const d = await getBounded('/market/stockflow?n=10', 15000).catch(() => null);
  if (g !== gen || !root) return;
  const inflow = d?.inflow || [], outflow = d?.outflow || [];
  const stamp = root.querySelector('#ck2-stkflow-stamp');
  if (stamp && d) {
    // 归属日角标：moneyflow 盘后落库，盘中/刚收盘是上一交易日口径——醒目标注，绝不冒充今日
    const notToday = d.data_date && d.data_date !== todayStr();
    const tag = notToday
      ? ` <span style="font-size:12px;color:var(--amber);border:1px solid var(--amber);border-radius:3px;padding:0 4px;font-weight:700;vertical-align:middle">昨${escapeHtml(String(d.data_date).slice(5))}</span>`
      : '';
    stamp.innerHTML = dataStamp(d) + tag;
  }
  if (!inflow.length && !outflow.length) {
    body.innerHTML = '<div class="ck2-compact-state"><span class="mark"></span><div><div class="title">个股资金流暂不可用</div><div class="desc">免费公开资金源尚未更新或暂不可达；本卡不以成交额或零值冒充净流入。</div></div></div>';
    return;
  }
  const col = (title, list, cls) => `<div class="col"><div class="h">${title}</div>${list.map((s) => `
    <div class="it" data-code="${escapeHtml(s.code)}"><span class="nm">${escapeHtml(s.name || s.code)}</span><span class="vv ${cls}">${fmtMoney(s.net, 1)}</span></div>`).join('') || '<div style="color:var(--text-3);font-size:12px">—</div>'}</div>`;
  body.innerHTML = `<div class="ck2-sf">${col('净流入 TOP', inflow, 'up')}${col('净流出 TOP', outflow, 'down')}</div>`;
  bindChips(body);
}

/* ========== 市场结构（容量 + 相对强度 + 宽度 + 涨停梯队；净流入权重0） ========== */
function mainlineKind(label, status) {
  const name = String(label || '观察');
  if (status === 'unformed') return { name, cls: 'unformed' };
  if (name === '主线') return { name, cls: 'role-main' };
  if (name === '次主线') return { name, cls: 'role-secondary' };
  if (name === '活口') return { name, cls: 'role-active' };
  if (name === '情绪链') return { name, cls: 'role-emotion' };
  return { name, cls: 'role-observe' };
}

function timedMainlineLabel(role) {
  if (role?.label !== '主线' && role?.label !== '次主线') return String(role?.label || '观察');
  const base = role?.label === '次主线' ? '次主线' : '主线';
  if (role?.status === 'unformed') return base;
  const stage = String(role?.time_status || '');
  if (stage === 'candidate') return `${base}候选`;
  if (stage === 'forming') return `${base}形成中`;
  if (stage === 'confirmed') return `${base}已确认`;
  if (stage === 'final') return `${base}定稿`;
  return base;
}

function mainlineGates(label) {
  if (label === '主线') return ['板块上涨', '宽度 ≥55%', '容量 前20%', '涨停 ≥2家'];
  if (label === '次主线') return ['板块上涨', '宽度 ≥50%', '容量 前35%', '涨停 ≥1家'];
  if (label === '活口') return ['市场退潮', '强度 前20%', '宽度 ≥60%', '涨停承接'];
  if (label === '情绪链') return ['涨停 ≥3家', '板位 ≥2层', '最高 ≥2板', '龙头可识别'];
  return [];
}

function renderDownPool(d) {
  const body = root?.querySelector('#ck2-downpool-body');
  if (!body) return;
  if (!d) {
    body.innerHTML = emptyState('跌停明细暂不可用', '主线数据端点暂不可用');
    return;
  }
  const stamp = root.querySelector('#ck2-downpool-stamp');
  const pool = Array.isArray(d?.limit_down_pool) ? d.limit_down_pool : [];
  const missing = Array.isArray(d?.limit_down_missing) ? d.limit_down_missing.filter(Boolean) : [];
  if (stamp && d) stamp.innerHTML = dataStamp(d.data_date || d.ts, null, {
    data_state: pool.length || !missing.length ? 'complete' : 'missing',
    state_label: pool.length || !missing.length ? '决策可用' : '暂不可用',
    missing_fields: pool.length || !missing.length ? [] : ['跌停明细'],
    notes: missing,
  });
  if (!pool.length) {
    body.innerHTML = missing.length
      ? emptyState('跌停明细暂不可用', missing.join('；'))
      : emptyState('今日暂无跌停风险', '当前数据口径未发现跌停个股');
    return;
  }
  const warning = missing.length ? `<div class="fb-stale">${escapeHtml(missing.join('；'))}</div>` : '';
  const visible = _downPoolExpanded ? pool : pool.slice(0, 5);
  body.innerHTML = warning + `<div class="ck2-down-list">${visible.map((s) => {
    const code = String(s.code || '');
    const time = s.first_time ? `首封 ${escapeHtml(String(s.first_time))}` : '首封时间 —';
    const opens = s.open_times != null ? `开板 ${fmtNum(s.open_times)} 次` : '';
    const seal = s.seal_amount != null ? `封单 ${fmtMoney(s.seal_amount, 1)}` : '';
    return `<div class="ck2-down-row" ${code ? `data-code="${escapeHtml(code)}"` : ''} title="${escapeHtml([seal, opens].filter(Boolean).join(' · '))}">
      <span class="ck2-down-name">${escapeHtml(s.name || code || '未知标的')}${code ? ` <span class="num" style="font-size:12px;color:var(--text-3)">${escapeHtml(code)}</span>` : ''}</span>
      <span class="ck2-down-pct">${fmtPct(s.change_pct)}</span>
      <span class="ck2-down-meta"><span>${escapeHtml(s.industry || '行业未标注')}</span><span>${time}${opens ? ` · ${opens}` : ''}</span></span>
    </div>`;
  }).join('')}${pool.length > 5 ? `<button class="ck2-down-more" type="button" data-downpool-toggle>${_downPoolExpanded ? '收起明细' : `展开全部 ${fmtNum(pool.length)} 只`}</button>` : ''}</div>`;
  body.querySelector('[data-downpool-toggle]')?.addEventListener('click', () => {
    _downPoolExpanded = !_downPoolExpanded;
    renderDownPool(d);
  });
  bindChips(body);
}

async function loadMainlines() {
  const body = root?.querySelector('#ck2-mainline-body');
  if (!body) return;
  const g = gen;
  const slow = setTimeout(() => {
    if (g !== gen || !root || !body.isConnected || !body.querySelector('.skel-group')) return;
    body.innerHTML = emptyState('今日行业主线仍在采集', '容量、强度、上涨宽度与涨停梯队正在对齐，其它驾驶舱数据可继续使用');
    const down = root.querySelector('#ck2-downpool-body');
    if (down?.querySelector('.skel-group')) down.innerHTML = emptyState('跌停明细仍在采集', '不使用跌停家数反推个股');
  }, 8000);
  const d = await getBounded('/market/mainlines', 20000).catch(() => null);
  clearTimeout(slow);
  if (g !== gen || !root) return;
  renderDownPool(d);
  const marketState = d?.market_state || {};
  const upRatio = marketState.up_ratio != null ? ` · 上涨宽度 ${fmtNum(Number(marketState.up_ratio) * 100, 0)}%` : '';
  const stamp = root.querySelector('#ck2-mainline-stamp');
  if (stamp && d) {
    stamp.innerHTML = `${dataStamp(d)}<span class="ck2-mainline-regime">${escapeHtml(marketState.state || '环境待确认')}${upRatio}</span>`;
  }
  const title = root.querySelector('#ck2-mainline-title');
  const productLabel = d?.time_scope?.product_label || '今日行业主线';
  if (title) title.textContent = `${productLabel} · 主线 / 次主线 / 活口 / 情绪链`;
  const raw = d?.roles || d?.sectors || d?.mainlines || [];
  const sectors = Array.isArray(raw) ? raw.slice(0, 5) : [];
  if (!sectors.length) {
    const why = Array.isArray(d?.missing) && d.missing.length ? d.missing.join('；') : '容量、相对强度、宽度或涨停梯队暂不可用';
    body.innerHTML = emptyState('主线数据不足', why);
    return;
  }
  const withdrawn = Array.isArray(d?.withdrawn_mainlines) ? d.withdrawn_mainlines : [];
  const withdrawnHtml = withdrawn.length
    ? `<div class="ck2-ml-withdraw">${withdrawn.map((item) => `${escapeHtml(item.status_label || '撤销观察')} · ${escapeHtml(item.name || '')}（${escapeHtml(item.reason || '')}）`).join('；')}</div>`
    : '';
  body.innerHTML = `${withdrawnHtml}<div class="ck2-ml">${sectors.map((s) => {
    const status = s?.status || 'formed';
    const kind = mainlineKind(s?.label, status);
    const cls = pctClass(s.change_pct);
    const members = (Array.isArray(s.limit_up_stocks) ? s.limit_up_stocks : []).slice(0, 4).map((x) => (
      typeof x === 'string' ? { name: x, code: '', lianban: null } : { name: x?.name || x?.code || '', code: x?.code || '', lianban: x?.lianban }
    )).filter((x) => x.name);
    const note = String(s.role_reason || s.evidence || '结构证据待确认');
    const rankPct = s.amount_rank_pct != null
      ? `${fmtNum(Number(s.amount_rank_pct) <= 1 ? Number(s.amount_rank_pct) * 100 : s.amount_rank_pct, 0)}%`
      : '—';
    const breadth = s.breadth_ratio != null ? `${fmtNum(Number(s.breadth_ratio) * 100, 0)}%` : '—';
    const strength = s.structure_score != null
      ? fmtNum(s.structure_score, 0)
      : s.score != null
        ? fmtNum(Number(s.score) <= 1 ? Number(s.score) * 100 : s.score, 0)
      : null;
    const hitText = ['主线', '次主线'].includes(String(s?.label || '')) && Number(s?.consecutive_hits || 0) > 0
      ? ` · 连续${fmtNum(s.consecutive_hits)}档` : '';
    const transitionText = ['promoted', 'demoted'].includes(String(s?.role_transition || ''))
      ? ` · ${s.role_transition_label || ''}` : '';
    const formed = status !== 'unformed';
    const gates = mainlineGates(kind.name);
    const catalyst = Array.isArray(s?.catalyst_evidence) ? s.catalyst_evidence[0] : null;
    const catalystConfirmed = Boolean(catalyst);
    const catalystPrefix = catalyst
      ? ((catalyst.multi || Number(catalyst.confirms || 0) >= 2) ? `催化确认 · 多源 ${fmtNum(catalyst.confirms || 2)}` : '催化线索 · 单源')
      : (s?.catalyst_status === 'unavailable' ? '催化数据暂缺' : '结构驱动');
    const catalystText = catalyst
      ? `${catalystPrefix} · ${catalyst.title || '原文命中'}`
      : `${catalystPrefix} · ${s?.catalyst_status === 'unavailable' ? '不影响结构评分' : '暂无同日行业/龙头原文命中'}`;
    const leaderQuality = s?.leader_quality || {};
    const leaderEvidence = Array.isArray(leaderQuality.evidence) ? leaderQuality.evidence.slice(0, 3) : [];
    const leaderText = s?.leader
      ? `龙头承接 · ${leaderQuality.level || '待验证'}${leaderEvidence.length ? ` · ${leaderEvidence.join(' / ')}` : ''}`
      : '';
    return `<div class="ml-card ${kind.cls}">
      <div class="ml-top"><span class="ml-kind">${escapeHtml(timedMainlineLabel(s))}</span><span class="ml-rank">${formed && strength != null ? `结构 ${strength}${hitText}${escapeHtml(transitionText)}` : '未形成'}</span></div>
      <div class="ml-name">${escapeHtml(s.name || '尚未形成')}${formed && s.change_pct != null ? ` <span class="${cls}" style="font-size:13px">${fmtPct(s.change_pct)}</span>` : ''}</div>
      ${formed ? `<div class="ml-proof"><span>容量前 ${rankPct}</span><span>宽度 ${breadth}</span><span>涨停 ${s.limit_up_count != null ? fmtNum(s.limit_up_count) : '—'} 家</span><span>最高 ${s.max_lianban != null ? fmtNum(s.max_lianban) + ' 板' : '—'}</span></div>` : `<div class="ml-gates">${gates.map((gate) => `<span>${escapeHtml(gate)}</span>`).join('')}</div>`}
      <div class="ml-note">${escapeHtml(note)}</div>
      ${formed && leaderText ? `<div class="ml-leader" title="${escapeHtml([leaderText, leaderQuality.rule].filter(Boolean).join('；'))}"><b>${escapeHtml(s.leader?.name || '龙头')}</b> · ${escapeHtml(leaderText)}</div>` : ''}
      ${formed ? `<div class="ml-catalyst ${catalystConfirmed ? 'confirmed' : ''}" title="${escapeHtml(catalystText)}">${escapeHtml(catalystText)}</div>` : ''}
      ${formed ? `<div class="ml-stk">${members.length ? members.map((m) => `<span class="s" ${m.code ? `data-code="${escapeHtml(m.code)}"` : 'style="cursor:default"'}>${m.lianban ? `${fmtNum(m.lianban)}板 ` : ''}${escapeHtml(m.name)}</span>`).join('') : '<span class="ml-empty-chain">暂无涨停梯队</span>'}</div>` : '<div class="ml-stk"><span class="ml-empty-chain">全部条件同时成立才点亮</span></div>'}
    </div>`;
  }).join('')}</div>`;
  bindChips(body);
}

/* ========== 多周期涨幅榜 ========== */
async function loadMultiperiod() {
  const body = root?.querySelector('#ck2-multi-body');
  if (!body) return;
  const g = gen;
  const d = await getBounded('/market/multiperiod', 18000).catch(() => null);
  if (g !== gen || !root) return;
  const rows = d?.stocks || [];
  const stamp = root.querySelector('#ck2-multi-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d);
  if (!rows.length) { body.innerHTML = emptyState('多周期涨幅暂不可用'); return; }
  const c = (v) => v != null ? `<span class="${pctClass(v)}">${fmtPct(v)}</span>` : '<span style="color:var(--text-3)">—</span>';
  body.innerHTML = `<div class="tbl-wrap"><table class="tbl">
    <thead><tr><th class="l">名称</th><th class="l">题材</th><th>当日</th><th>3日</th><th>5日</th><th>20日</th></tr></thead>
    <tbody>${rows.map((s) => `<tr>
      <td class="l cn"><span class="code-link" data-code="${escapeHtml(s.code)}" style="font-weight:600;cursor:pointer">${escapeHtml(s.name || s.code)}${s.name && s.name !== s.code ? ` <span class="num" style="color:var(--text-3);font-weight:400;font-size:12px">${escapeHtml(s.code)}</span>` : ''}</span></td>
      <td class="l" style="color:var(--text-3)">${escapeHtml(s.theme || '—')}</td>
      <td>${c(s.d1)}</td><td>${c(s.d3)}</td><td>${c(s.d5)}</td><td>${c(s.d20)}</td>
    </tr>`).join('')}</tbody></table></div>`;
  bindChips(body);
}

/* ========== 题材热力图 ========== */
async function loadHeatmap() {
  const host = root?.querySelector('#ck2-heat-chart');
  if (!host) return;
  const g = gen;
  const se = await getBounded('/market/sectors', 15000).catch(() => null);
  if (g !== gen || !root) return;
  const rows = (se?.sectors || []).filter((s) => s.kind === 'industry').slice(0, 40);
  // 2026-07-11 审计 M3 修复：原每次调用（含每次切回可见）无条件 mkChart 且不 dispose 旧实例
  // → ECharts 实例 + ResizeObserver 按切回次数泄漏。改与 dashboard.renderHeat 同款：
  // 空数据先 dispose 再显空态；有数据仅在无实例时新建，否则 setOption 就地更新。
  if (!rows.length) {
    if (heatChart) { disposeCharts([heatChart]); charts = charts.filter((c) => c !== heatChart); heatChart = null; }
    host.innerHTML = '<div class="empty-state"><div class="es-title">板块行情暂不可用</div></div>';
    return;
  }
  if (heatChart && heatChart.isDisposed?.()) heatChart = null;
  if (!heatChart) {
    heatChart = mkChart(host);
    if (heatChart) charts.push(heatChart);
  }
  if (heatChart) heatChart.setOption(treemapOption(rows), true);
}
