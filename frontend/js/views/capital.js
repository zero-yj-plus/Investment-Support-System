/* views/capital.js — 资金追踪（top 板块 + 热门股 日内主力净流入时序，对照"主力资金净流入"盘面图）

   核心：盘中每 15 分钟快照累积 = 数据持续性；东财为主/同花顺辅（eastmoney_direct 开关）。
   主图 = 选中维度（板块/热门股）各标的当日主力净额随时间的曲线，线尾标"名 值亿"，正流入红、负流出绿；
   下方两榜 = 板块资金榜（流入/流出两端）+ 热门股资金榜（点击跳问股）。数据取 /api/capital/trends。

   单位：主力净额=元（图表内 /1e8 显示为亿）；颜色沿用全局红涨绿跌（pctClass / --up / --down）。 */

import { api, fmtMoney, fmtPct, pctClass, isTradingTime } from '../api.js';
import { dataStamp, emptyState, skeleton, escapeHtml, toast } from '../ui.js';
import { mkChart, disposeCharts, capitalTrendOption } from '../charts.js';

let root = null;
let timers = [];
let charts = [];
let gen = 0;           // 挂载代号：旧挂载的在途响应作废，避免写进已脱离 DOM 的旧节点
let mode = 'sectors';  // 主图维度：sectors | hot
let lastData = null;

export function mount(el) {
  gen += 1;
  root = el;
  mode = 'sectors';
  lastData = null;
  root.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">资金追踪</h2>
      <span class="page-sub">追踪板块与热门股的主力资金日内流向，识别资金聚焦、扩散与分歧</span>
    </div>
    <div class="grid12">
      <div class="card sp-12" id="cap-chart-card">
        <div class="card-head">
          <span class="card-title" id="cap-chart-title">主力资金日内净流入 · CAPITAL FLOW</span>
          <span class="card-extra" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <span class="segmented" id="cap-mode">
              <button class="seg active" data-key="sectors">板块</button>
              <button class="seg" data-key="hot">热门股</button>
            </span>
            <button class="btn-sm" id="cap-snap" title="立即采集一次入库（通常由盘中定时任务自动执行）">立即采集</button>
            <span class="data-stamp" id="cap-src" style="color:var(--brand1)"></span>
            <span class="cap-stamp-wrap" id="cap-stamp"></span>
          </span>
        </div>
        <div class="card-body"><div id="cap-chart" style="height:440px">${skeleton(6)}</div></div>
      </div>

      <div class="card sp-7">
        <div class="card-head"><span class="card-title">板块资金榜 · 流入 / 流出</span></div>
        <div class="card-body" id="cap-sec-list">${skeleton(6)}</div>
      </div>

      <div class="card sp-5">
        <div class="card-head"><span class="card-title">热门股资金榜</span></div>
        <div class="card-body" id="cap-hot-list">${skeleton(6)}</div>
      </div>
    </div>`;

  bindUI();
  loadAll();
  // 快照每 15min 更新，盘中 60s 兜底刷新即可拾取新点；非交易时段不请求（静态）。
  timers.push(setInterval(() => { if (isTradingTime()) loadAll(); }, 60000));
}

export function unmount() {
  timers.forEach(clearInterval);
  timers = [];
  disposeCharts(charts);
  root = null;
  lastData = null;
}

function bindUI() {
  root.querySelector('#cap-mode')?.addEventListener('click', (e) => {
    const b = e.target.closest('.seg');
    if (!b) return;
    mode = b.dataset.key;
    root.querySelectorAll('#cap-mode .seg').forEach((x) => x.classList.toggle('active', x === b));
    renderChart();
    renderSourceMeta();
  });
  root.querySelector('#cap-snap')?.addEventListener('click', async () => {
    const btn = root.querySelector('#cap-snap');
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    btn.textContent = '采集中…';
    try {
      const r = await api.post('/capital/snapshot');
      if (r && r.ok) {
        toast(`采集完成：板块 ${r.sectors} · 热门股 ${r.hot_stocks}`, 'ok');
        loadAll();
      } else {
        toast(`采集为空：${r?.msg || '数据源不可用'}`, 'error', 5000);
      }
    } catch {
      toast('采集失败', 'error');
    } finally {
      if (root) { btn.disabled = false; btn.textContent = '立即采集'; }
    }
  });
}

async function loadAll() {
  const g = gen;
  let d = null;
  try { d = await api.get('/capital/trends?days=1'); } catch { /* 下方兜底空态 */ }
  if (g !== gen || !root) return;
  lastData = d;
  renderSourceMeta();
  renderChart();
  renderList('#cap-sec-list', d?.sectors, 'sector');
  renderList('#cap-hot-list', d?.hot_stocks, 'hot');
}

function isRawValue(row) {
  return row?.unit_verified === false || row?.value_unit === 'tdx_raw_unverified';
}

function formatCapitalValue(row, digits = 1) {
  const value = Number(row?.main_net);
  if (!Number.isFinite(value)) return '--';
  if (!isRawValue(row)) return fmtMoney(value, digits);
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

function renderSourceMeta() {
  if (!root) return;
  const d = lastData;
  const rows = mode === 'hot' ? (d?.hot_stocks || []) : (d?.sectors || []);
  const srcEl = root.querySelector('#cap-src');
  if (srcEl) {
    const src = mode === 'hot' ? (d?.hot_source || '') : (d?.sector_source || '');
    srcEl.textContent = src ? `数据源 ${src}` : '';
  }
  const stamp = root.querySelector('#cap-stamp');
  if (stamp) {
    // #6 修：标"数据真正所属交易日"（不是采集时刻）。盘后未结算时热门股实为上一已结算日，
    // 必须显式告知"非今日"——否则客户拿今日实时价对比会以为数据错(紫光股份+10%实为隔日涨停日数据)。
    const st = (d && (d.snap_ts || d.ts)) || '';
    const src = mode === 'hot' ? ((d && d.hot_source) || '') : ((d && d.sector_source) || '');
    const dataDate = mode === 'hot' ? ((d && d.hot_date) || '') : ((d && d.sector_date) || '');
    const stale = mode === 'hot' ? !!(d && d.stale_hot) : !!(d && d.stale_sector);
    const dateLabel = dataDate
      ? `<span style="color:var(--text-2);font-size:11px;margin-left:8px">数据日期 ${escapeHtml(dataDate)}${stale ? '' : '（今日）'}</span>` : '';
    const staleWarn = stale
      ? `<div style="margin-top:7px;padding:6px 10px;border:1px solid var(--amber-line,rgba(245,166,35,.4));background:rgba(245,166,35,.08);border-radius:7px;font-size:12px;color:var(--amber);line-height:1.55">⚠ 当前展示最近已发布交易日 <b>${escapeHtml(dataDate)}</b> 的资金快照，<b>非今日实时</b>。</div>`
      : '';
    const rawWarn = rows.some(isRawValue)
      ? `<div style="margin-top:7px;padding:6px 10px;border:1px solid var(--amber-line,rgba(245,166,35,.4));background:rgba(245,166,35,.08);border-radius:7px;font-size:12px;color:var(--amber);line-height:1.55">TDX 板块资金为原始公式值：可比较排名与流入/流出方向，单位尚未核验，未标成“元/亿元”，也不参与金额求和。</div>`
      : '';
    stamp.innerHTML = (st
      ? `${dataStamp(st)}${dateLabel}<span style="color:var(--text-3);font-size:11px;margin-left:8px">${src ? escapeHtml(src) : ''}</span>`
      : '') + staleWarn + rawWarn;
  }
}

function renderChart() {
  const host = root?.querySelector('#cap-chart');
  if (!host) return;
  root?.querySelector('.cap-chart-note')?.remove();
  const d = lastData;
  const rows = mode === 'hot' ? (d?.hot_stocks || []) : (d?.sectors || []);
  const title = root.querySelector('#cap-chart-title');
  if (title) title.textContent = rows.some(isRawValue)
    ? '板块资金强弱 · TDX RAW'
    : '主力资金日内净流入 · CAPITAL FLOW';
  disposeCharts(charts);
  if (!d || !d.ok) {
    host.innerHTML = emptyState('资金追踪未开启', '在该客户 config.yaml 设 capital_tracking.enabled: true 后盘中自动采集');
    return;
  }
  if (!rows.length) {
    host.innerHTML = emptyState('今日暂无资金快照',
      '盘中每 15 分钟自动采集累积；可点右上"立即采集"先手动补一次（非交易时段源可能为空）');
    return;
  }
  host.innerHTML = '';
  const c = mkChart(host);
  if (c) {
    c.setOption(capitalTrendOption(rows));
    charts.push(c);
    if (rows.length > 8) {
      const note = document.createElement('div');
      note.className = 'cap-chart-note';
      note.textContent = '图中默认展示资金流入/流出两端各 4 条，完整排名见下方榜单；悬浮曲线查看对应时点。';
      host.insertAdjacentElement('afterend', note);
    }
  }
  else host.innerHTML = emptyState('图表库未加载', 'vendor/echarts.min.js 缺失');
}

function renderList(sel, rows, kind) {
  const body = root?.querySelector(sel);
  if (!body) return;
  rows = (rows || []).filter((r) => r);
  if (!rows.length) {
    body.innerHTML = emptyState(
      kind === 'hot' ? '尚无热门股资金快照' : '尚无行业资金快照',
      '当前时段尚未生成盘中采集记录；完成下一次采集后显示。',
    );
    return;
  }
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(Number(r.main_net) || 0)));
  const html = rows.map((r, i) => {
    const v = Number(r.main_net) || 0;
    const w = Math.max(3, (Math.abs(v) / maxAbs) * 100);
    const cls = pctClass(v);
    const name = kind === 'hot'
      ? `${escapeHtml(r.name || r.code || '')}<span class="num" style="color:var(--text-3);margin-left:6px;font-size:11px">${escapeHtml(r.code || '')}</span>`
      : escapeHtml(r.name || '');
    const valueLabel = isRawValue(r) ? '资金公式原值' : '主力净额';
    const tip = escapeHtml(`${r.name || r.code}\n${valueLabel} ${formatCapitalValue(r)}\n涨跌 ${fmtPct(r.change_pct)}`);
    const codeAttr = kind === 'hot' && r.code ? ` clickable" data-code="${escapeHtml(r.code)}` : '';
    return `<div class="cap-row${codeAttr}" data-tip="${tip}"
        style="display:flex;align-items:center;gap:8px;padding:5px 2px;border-bottom:1px solid var(--line)${kind === 'hot' && r.code ? ';cursor:pointer' : ''}">
      <span class="num" style="flex:0 0 20px;color:var(--text-3);text-align:right">${i + 1}</span>
      <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${name}</span>
      <span class="${pctClass(r.change_pct)} num" style="flex:0 0 58px;text-align:right" title="快照时点涨跌幅·非实时">${fmtPct(r.change_pct)}</span>
      <span style="flex:0 0 84px;height:8px;background:var(--line);border-radius:3px;overflow:hidden">
        <span style="display:block;height:100%;width:${w}%;background:var(--${v >= 0 ? 'up' : 'down'});opacity:.85"></span>
      </span>
      <span class="${cls} num" style="flex:0 0 88px;text-align:right">${formatCapitalValue(r, 1)}</span>
    </div>`;
  }).join('');
  body.innerHTML = `<div class="cap-list">${html}</div>`;
  if (kind === 'hot') {
    body.querySelectorAll('.cap-row[data-code]').forEach((c) => {
      c.addEventListener('click', () => {
        const code = c.dataset.code;
        if (!code) return;
        sessionStorage.setItem('hermes.chat.code', code);
        location.hash = '#/chat';
      });
    });
  }
}
