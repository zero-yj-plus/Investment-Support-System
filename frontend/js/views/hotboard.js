/* 涨停题材 — 基于已验证公开数据链路的业务模块。
 * 当前版保留原页面的题材筛选、几天几板、板型、封板率、首封与封单额，
 * 接口使用独立同花顺公开池口径，不与驾驶舱主涨停池的数字混算。 */

import { api, fmtMoney, fmtPct, pctClass, isTradingTime } from '../api.js';
import { escapeHtml, emptyState, skeleton, dataStamp } from '../ui.js';

let root = null;
let timers = [];
let lastGood = null;
let themeFilter = '';
let sortKey = null;
let sortDir = -1; // -1=降序 / 1=升序；新列首次点击按从高到低

function injectStyle() {
  if (document.getElementById('hotboard-style')) return;
  const css = `
  .hb-page-head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin:1px 2px 14px}
  .hb-page-head h2{font-size:22px;line-height:1.25;margin:0;color:var(--text)}
  .hb-page-head p{font-size:13px;line-height:1.5;margin:0;color:var(--text-3)}
  .hb-source-note{font-size:12px;color:var(--text-3);margin-top:9px;line-height:1.55}
  .hb-themes{display:flex;flex-wrap:wrap;gap:8px}
  .hb-pill{position:relative;appearance:none;padding:5px 12px;border:1px solid var(--border-strong);border-radius:16px;
    font:inherit;font-size:13px;line-height:1.3;cursor:pointer;color:var(--text-2);background:var(--panel);overflow:hidden;white-space:nowrap}
  .hb-pill::before{content:'';position:absolute;left:0;top:0;bottom:0;width:var(--w,0);background:var(--up-bg);z-index:0}
  .hb-pill>span{position:relative;z-index:1}.hb-pill:hover{border-color:var(--up)}
  .hb-pill.on{border-color:var(--up);color:var(--up);font-weight:700}
  .hb-table-wrap{width:100%;overflow:auto}
  .hb-table{min-width:980px}
  .hb-table td,.hb-table th{padding:8px 10px;font-size:13px;white-space:nowrap}
  .hb-table th.hb-sort-head{padding:4px 5px}
  .hb-sort{appearance:none;display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:30px;
    padding:4px 9px;border:1px solid var(--border-strong);border-radius:7px;background:var(--panel-2);color:var(--text-2);
    font:inherit;font-weight:700;line-height:1;cursor:pointer;white-space:nowrap;box-shadow:0 1px 2px rgba(0,0,0,.12);
    transition:border-color var(--t-fast),background var(--t-fast),color var(--t-fast),transform var(--t-fast)}
  .hb-sort-icon{display:inline-grid;place-items:center;min-width:17px;height:17px;border-radius:4px;background:var(--panel-3);
    color:var(--text-3);font-size:12px;font-family:var(--mono,monospace);line-height:1}
  .hb-sort:hover{border-color:var(--amber-line);background:var(--accent-weak);color:var(--accent);transform:translateY(-1px)}
  .hb-sort:hover .hb-sort-icon{color:var(--accent)}
  .hb-sort.is-active{border-color:var(--amber-line);background:var(--accent-weak);color:var(--accent);box-shadow:0 0 0 1px var(--amber-line)}
  .hb-sort.is-active .hb-sort-icon{background:var(--amber-line);color:var(--text)}
  .hb-sort:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  .hb-board{display:inline-flex;padding:2px 7px;border-radius:5px;background:var(--panel-2);font-size:12px}
  .hb-board.warm{color:var(--amber);background:var(--amber-dim)}
  .hb-board.hot{color:var(--up);background:var(--up-bg);font-weight:750}
  .hb-tag{margin-left:5px;padding:0 5px;border-radius:4px;font-size:11px;color:var(--amber);border:1px solid var(--amber-line)}
  .hb-reason{color:var(--text-2);max-width:360px;overflow:hidden;text-overflow:ellipsis}
  .hb-code{appearance:none;border:0;padding:0;background:transparent;color:var(--accent);font:inherit;font-family:var(--mono,monospace);cursor:pointer}
  .hb-code:hover{text-decoration:underline}
  @media(max-width:760px){.hb-page-head h2{font-size:19px}.hb-pill{font-size:12px;padding:5px 10px}}
  `;
  const style = document.createElement('style');
  style.id = 'hotboard-style';
  style.textContent = css;
  document.head.appendChild(style);
}

export function mount(el) {
  root = el;
  injectStyle();
  root.innerHTML = `
    <div class="hb-page-head">
      <h2>涨停题材</h2>
      <p>按题材筛选今日涨停股，复核几天几板、板型、封板率、首封与封单额</p>
    </div>
    <section class="card">
      <div class="card-head"><span class="card-title">今日热门题材 · 按涨停数</span><span class="card-extra" id="hb-stamp"></span></div>
      <div class="card-body" id="hb-themes">${skeleton(1)}</div>
    </section>
    <section class="card">
      <div class="card-head"><span class="card-title">涨停股明细</span><span class="card-extra" id="hb-count"></span></div>
      <div class="card-body" id="hb-body">${skeleton(8)}</div>
    </section>`;
  if (lastGood?.stocks?.length) render(lastGood);
  load(Boolean(lastGood));
  timers.push(setInterval(() => { if (isTradingTime()) load(true); }, 60000));
}

export function unmount() {
  timers.forEach(clearInterval);
  timers = [];
  root = null;
  themeFilter = '';
  sortKey = null;
  sortDir = -1;
}

function openDiagnosis(code) {
  const params = new URLSearchParams({ tab: 'diagnosis', code });
  location.hash = `#/research?${params.toString()}`;
}

async function load(background = false) {
  const body = root?.querySelector('#hb-body');
  if (!body) return;
  const payload = await api.get('/market/limitup/themes', { timeoutMs: 10000 }).catch(() => null);
  if (!root) return;
  const stocks = Array.isArray(payload?.stocks) ? payload.stocks.filter((item) => item?.code) : [];
  if (!stocks.length) {
    if (background && lastGood?.stocks?.length) return;
    body.innerHTML = emptyState('涨停题材暂不可用', payload?.missing?.[0] || '公开涨停池尚未返回当日明细');
    root.querySelector('#hb-themes').innerHTML = '';
    return;
  }
  lastGood = payload;
  render(payload);
}

function render(payload) {
  if (!root) return;
  const stamp = root.querySelector('#hb-stamp');
  if (stamp) stamp.innerHTML = dataStamp(payload.data_date || payload.ts, null, {
    data_state: 'complete',
    state_label: '决策可用',
    notes: [...(payload.notes || []), ...(payload.missing || [])],
  });
  renderThemes(payload.stocks || []);
  renderTable(payload.stocks || []);
}

function splitThemes(value) {
  return String(value || '').split('+').map((item) => item.trim()).filter(Boolean);
}

function renderThemes(stocks) {
  const host = root?.querySelector('#hb-themes');
  if (!host) return;
  const counts = new Map();
  stocks.forEach((stock) => splitThemes(stock.reason_type).forEach((theme) => {
    counts.set(theme, (counts.get(theme) || 0) + 1);
  }));
  const themes = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-CN')).slice(0, 18);
  const max = Math.max(1, ...themes.map(([, count]) => count));
  const pill = (theme, count, width) => `<button class="hb-pill${themeFilter === theme ? ' on' : ''}" type="button" data-theme="${escapeHtml(theme)}" style="--w:${width}%"><span>${escapeHtml(theme || '全部')} <b>${count}</b></span></button>`;
  host.innerHTML = `<div class="hb-themes">${pill('', stocks.length, 0)}${themes.map(([theme, count]) => pill(theme, count, Math.round(count / max * 100))).join('')}</div>
    <div class="hb-source-note">题材与表内封板字段来自同花顺公开池独立口径；不与驾驶舱主涨停池的东财、腾讯数字互相回填。</div>`;
  host.querySelectorAll('[data-theme]').forEach((button) => button.addEventListener('click', () => {
    themeFilter = button.dataset.theme || '';
    renderThemes(lastGood?.stocks || []);
    renderTable(lastGood?.stocks || []);
  }));
}

function boardCount(stock) {
  const value = String(stock.high_days || '');
  const match = /(\d+)板/.exec(value);
  return match ? Number(match[1]) : 1;
}

function numericValue(stock, key) {
  if (key === 'board_count') return boardCount(stock);
  const raw = stock?.[key];
  if (raw == null || (typeof raw === 'string' && !raw.trim())) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function defaultStockOrder(a, b) {
  return boardCount(b) - boardCount(a)
    || (numericValue(b, 'seal_amount') ?? -Infinity) - (numericValue(a, 'seal_amount') ?? -Infinity);
}

function sortedStocks(stocks) {
  const rows = stocks.slice().sort(defaultStockOrder);
  if (!sortKey) return rows;
  return rows.sort((a, b) => {
    const left = numericValue(a, sortKey);
    const right = numericValue(b, sortKey);
    if (left == null && right == null) return defaultStockOrder(a, b);
    if (left == null) return 1;
    if (right == null) return -1;
    return sortDir * (left - right) || defaultStockOrder(a, b);
  });
}

function sortableHead(key, label) {
  const active = sortKey === key;
  const ariaSort = active ? (sortDir < 0 ? 'descending' : 'ascending') : 'none';
  const arrow = active ? (sortDir < 0 ? '↓' : '↑') : '↕';
  const state = active ? `当前${sortDir < 0 ? '降序' : '升序'}` : '可排序';
  return `<th class="hb-sort-head" aria-sort="${ariaSort}"><button class="hb-sort${active ? ' is-active' : ''}" type="button" data-sort="${key}" title="点击排序；首次按从高到低" aria-label="${label}排序，${state}"><span>${label}</span><span class="hb-sort-icon" aria-hidden="true">${arrow}</span></button></th>`;
}

function renderTable(stocks) {
  const body = root?.querySelector('#hb-body');
  const count = root?.querySelector('#hb-count');
  if (!body) return;
  const rows = sortedStocks(stocks
    .filter((stock) => !themeFilter || splitThemes(stock.reason_type).includes(themeFilter)));
  if (count) count.textContent = `${rows.length} 只${themeFilter ? ` · ${themeFilter}` : ''}`;
  body.innerHTML = `<div class="hb-table-wrap"><table class="tbl hb-table">
    <thead><tr><th class="l">代码</th><th class="l">名称</th>${sortableHead('price', '现价')}<th>涨幅</th>${sortableHead('board_count', '几天几板')}<th>板型</th>${sortableHead('suc_rate_pct', '封板率')}<th>首封</th>${sortableHead('seal_amount', '封单额')}<th class="l">题材</th></tr></thead>
    <tbody>${rows.map((stock) => {
      const boards = boardCount(stock);
      const boardClass = boards >= 3 ? 'hot' : boards === 2 ? 'warm' : '';
      return `<tr>
        <td class="l"><button class="hb-code" type="button" data-code="${escapeHtml(stock.code)}">${escapeHtml(stock.code)}</button></td>
        <td class="l cn" style="font-weight:700">${escapeHtml(stock.name || '—')}${stock.is_again ? '<span class="hb-tag">回封</span>' : ''}</td>
        <td class="num">${stock.price ?? '—'}</td>
        <td class="num ${pctClass(stock.change_pct)}">${fmtPct(stock.change_pct)}</td>
        <td><span class="hb-board ${boardClass}">${escapeHtml(stock.high_days || '首板')}</span></td>
        <td>${escapeHtml(stock.limit_up_type || '—')}</td>
        <td class="num">${stock.suc_rate_pct != null ? `${stock.suc_rate_pct}%` : '—'}</td>
        <td class="num">${escapeHtml(stock.first_time || '—')}</td>
        <td class="num">${stock.seal_amount != null ? fmtMoney(stock.seal_amount, 1) : '—'}</td>
        <td class="l hb-reason" title="${escapeHtml(stock.reason_type || '')}">${escapeHtml(stock.reason_type || '—')}</td>
      </tr>`;
    }).join('')}</tbody></table></div>`;
  body.querySelectorAll('[data-sort]').forEach((button) => button.addEventListener('click', () => {
    const key = button.dataset.sort;
    if (sortKey === key) sortDir = -sortDir;
    else { sortKey = key; sortDir = -1; }
    renderTable(lastGood?.stocks || []);
  }));
  body.querySelectorAll('[data-code]').forEach((button) => button.addEventListener('click', () => openDiagnosis(button.dataset.code)));
}
