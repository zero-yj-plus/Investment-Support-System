/* views/timing.js — 资金择时
   1 贪恐情绪温度计（仪表盘 0~100 + 五因子条）
   2 多指数 0AMV 四区域（进攻/出击/震荡/防守，评分 + 均线结构 + 操作建议）
   3 个股择时（输入代码 → 该股四区域与评分） */

import { api, fmtMoney, fmtPct, fmtNum, pctClass, isTradingTime } from '../api.js';
import { dataStamp, emptyState, skeleton, escapeHtml, toast } from '../ui.js';
import { mkChart, disposeCharts, gaugeOption } from '../charts.js';

let root = null;
let charts = [];
let timers = [];
// 挂载代号：卸载/重挂载后在途响应不得再触碰 DOM 或创建图表（防 null root 解引用与孤儿图表泄漏）
let gen = 0;
let lastCode = '';

// 本视图私有样式（注入一次，幂等）。所有结构样式收敛在本文件内，不改共享 app.css。
function injectStyle() {
  if (document.getElementById('tm-style')) return;
  const css = `
  .tm-fg-wrap{display:flex;gap:18px;align-items:stretch;flex-wrap:wrap}
  .tm-fg-gauge{width:240px;height:170px;flex:0 0 240px}
  .tm-fg-side{flex:1;min-width:260px;display:flex;flex-direction:column;gap:8px}
  .tm-fg-head{display:flex;align-items:baseline;gap:10px}
  .tm-fg-label{font-size:22px;font-weight:800}
  .tm-fg-sub{font-size:13px;color:var(--text-3)}
  .tm-facs{display:flex;flex-direction:column;gap:7px;margin-top:2px}
  .tm-fac-row{display:flex;align-items:center;gap:10px}
  .tm-fac-name{flex:0 0 76px;font-size:13px;color:var(--text-2)}
  .tm-fac-track{flex:1;height:8px;border-radius:5px;background:var(--line);overflow:hidden}
  .tm-fac-bar{height:100%;border-radius:5px;transition:width .4s ease}
  .tm-fac-val{flex:0 0 30px;text-align:right;font-size:13px;color:var(--text-2)}
  .tm-fg-meta{font-size:13px;color:var(--text-3);margin-top:4px}
  .tm-card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
  .tm-card{border:1px solid var(--line);border-left-width:3px;border-radius:12px;padding:13px 15px 13px 14px;background:var(--panel);display:flex;flex-direction:column;gap:9px}
  .tm-card-big{max-width:520px}
  .tm-card.zone-offense{border-left-color:var(--up)}
  .tm-card.zone-defense{border-left-color:var(--down)}
  .tm-card.zone-strike{border-left-color:var(--amber)}
  .tm-card.zone-shock{border-left-color:var(--flat)}
  .tm-card-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .tm-name{font-size:15px;font-weight:700}
  .tm-price{font-size:15px}
  .tm-chg{font-size:13px}
  .tm-zone{margin-left:auto;font-size:12px;font-weight:700;padding:2px 9px;border-radius:999px;border:1px solid var(--line)}
  .tm-score-line{display:flex;align-items:center;gap:10px}
  .tm-score-track{flex:1;height:9px;border-radius:5px;background:var(--line);overflow:hidden}
  .tm-score-bar{height:100%;border-radius:5px;transition:width .4s ease}
  .tm-score-num{flex:0 0 34px;text-align:right;font-size:16px;font-weight:800;color:var(--text)}
  .tm-card-fac{font-size:12px;color:var(--text-3)}
  .tm-card-advice{font-size:13px;line-height:1.6;color:var(--text-2)}
  .tm-card-empty,.tm-stock-hint{font-size:13px;color:var(--text-3);padding:4px 0}
  .tm-code-box{display:flex;gap:8px;align-items:center}
  .tm-code-input{width:120px;padding:7px 11px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--text);font-size:14px;font-family:var(--mono,monospace)}
  .tm-code-input:focus{outline:none;border-color:var(--amber)}
  .tm-code-btn{padding:7px 16px;font-size:14px}
  .tm-stock-meta{font-size:13px;color:var(--text-3);margin-top:9px}`;
  const tag = document.createElement('style');
  tag.id = 'tm-style';
  tag.textContent = css;
  document.head.appendChild(tag);
}

// 四区域配色（与红涨绿跌体系协调：进攻=红/强、出击=琥珀、震荡=灰、防守=绿/弱）
const ZONE_META = {
  '进攻': { color: 'var(--up)', cls: 'zone-offense', hint: '趋势进攻' },
  '出击': { color: 'var(--amber)', cls: 'zone-strike', hint: '试探出击' },
  '震荡': { color: 'var(--flat)', cls: 'zone-shock', hint: '区间震荡' },
  '防守': { color: 'var(--down)', cls: 'zone-defense', hint: '防守持币' },
};

// 五因子说明（hover 解释口径，全部真实数据驱动）
const FG_FACTOR_TIP = {
  '涨跌家数比': '上涨家数 / (上涨+下跌)，越高越贪（乐咕活跃度）',
  '涨停温度': '涨停 / (涨停+跌停)，涨停越多越贪（乐咕活跃度）',
  '成交量比': '上证 5 日均量 / 20 日均量，放量为贪',
  '资金': '两市主力净流入（北向已停发，改用此口径）',
  '波动反向': '上证 20 日波动的 120 日分位取反，低波动=贪/自满',
};

function zoneBadge(zone) {
  const m = ZONE_META[zone];
  if (!m) return '<span class="tm-zone">--</span>';
  return `<span class="tm-zone" style="color:${m.color};border-color:${m.color}55;background:${m.color}1a">${escapeHtml(zone)}</span>`;
}

/* ================= 1 贪恐温度计 ================= */

async function loadFearGreed(data) {
  const el = root.querySelector('#tm-fg');
  const g = gen;
  const fg = data?.fear_greed;
  if (g !== gen || !root) return;
  if (!fg || fg.ok === false || fg.score == null) {
    el.innerHTML = emptyState('贪恐指标本次未计算', fg?.missing?.[0] || '涨跌分布或成交样本本次未取得', '', 'spark');
    return;
  }
  const m = fg.meta || {};
  // 五因子条：缺失的因子显示「—」并标注口径（绝不编造）
  const order = ['涨跌家数比', '涨停温度', '成交量比', '资金', '波动反向'];
  const bars = order.map((k) => {
    const v = fg.factors?.[k];
    const has = v != null;
    const w = has ? Math.max(2, Math.min(100, v)) : 0;
    // 颜色随分值：>55 偏贪(红) / <45 偏恐(绿) / 中性灰
    const col = !has ? 'var(--flat)' : v >= 55 ? 'var(--up)' : v <= 45 ? 'var(--down)' : 'var(--flat)';
    return `<div class="tm-fac-row" data-tip="${escapeHtml(FG_FACTOR_TIP[k] || '')}">
      <span class="tm-fac-name">${escapeHtml(k)}</span>
      <div class="tm-fac-track"><div class="tm-fac-bar" style="width:${w}%;background:${col}"></div></div>
      <span class="tm-fac-val num">${has ? Number(v).toFixed(0) : '—'}</span>
    </div>`;
  }).join('');

  el.innerHTML = `
    <div class="tm-fg-wrap">
      <div class="tm-fg-gauge" id="tm-fg-gauge"></div>
      <div class="tm-fg-side">
        <div class="tm-fg-head">
          <span class="tm-fg-label" style="color:${fg.score >= 55 ? 'var(--up)' : fg.score <= 45 ? 'var(--down)' : 'var(--text-2)'}">${escapeHtml(fg.label ?? '--')}</span>
          <span class="tm-fg-sub">综合贪恐指数（五因子等权）</span>
        </div>
        ${fg.stale ? `<div style="margin:2px 0 8px;padding:5px 9px;border:1px solid var(--amber-line,rgba(245,166,35,.4));background:rgba(245,166,35,.08);border-radius:7px;font-size:11.5px;color:var(--amber);line-height:1.5">⚠ 贪恐为截至 <b>${escapeHtml(fg.data_date || '')}</b> 收盘的日线温度，<b>非今日盘中实时</b>；今日收盘后自动更新。</div>` : ''}
        <div class="tm-facs">${bars}</div>
        <div class="tm-fg-meta num">
          涨 <span class="up">${fmtNum(m.up)}</span> · 跌 <span class="down">${fmtNum(m.down)}</span>
          · 涨停 <span class="up">${fmtNum(m.limit_up)}</span> · 跌停 <span class="down">${fmtNum(m.limit_down)}</span>
          ${m.main_flow_total != null ? ` · 两市主力 <span class="${pctClass(m.main_flow_total)}">${fmtMoney(m.main_flow_total, 1)}</span>` : ''}
        </div>
      </div>
    </div>`;

  const host = el.querySelector('#tm-fg-gauge');
  const c = mkChart(host);
  if (c) { c.setOption(gaugeOption(fg.score)); charts.push(c); }
  else if (host) host.innerHTML = `<div class="tm-fg-num num" style="font-size:34px;font-weight:800;text-align:center;padding:24px 0">${fg.score}<span style="font-size:14px;color:var(--text-3)">/100</span></div>`;
}

/* ================= 2 多指数四区域 ================= */

function timingCard(it, big = false) {
  const ok = it && it.ok;
  const score = ok ? Math.max(0, Math.min(100, Number(it.score) || 0)) : 0;
  const m = ZONE_META[it?.zone] || {};
  const ma = it?.ma || {};
  const f = it?.factors || {};
  const chgCls = pctClass(it?.change_pct);
  // 均线结构紧凑展示（价 vs MA20 vs MA60）
  const maStr = (ma.price != null && ma.ma20 != null)
    ? `价 ${fmtNum(ma.price, 2)} · MA20 ${fmtNum(ma.ma20, 2)} · MA60 ${ma.ma60 != null ? fmtNum(ma.ma60, 2) : '--'}`
    : '均线数据不足';
  const facStr = ok
    ? `趋势${Number(f['趋势'] ?? 0).toFixed(0)} 动量${Number(f['动量'] ?? 0).toFixed(0)} 量能${Number(f['量能'] ?? 0).toFixed(0)} 位置${Number(f['位置'] ?? 0).toFixed(0)}`
    : '';
  return `<div class="tm-card ${m.cls || ''} ${big ? 'tm-card-big' : ''}">
    <div class="tm-card-top">
      <span class="tm-name">${escapeHtml(it?.name || it?.code || '--')}</span>
      <span class="tm-price num ${chgCls}">${it?.price != null ? fmtNum(it.price, 2) : '--'}</span>
      <span class="tm-chg num ${chgCls}">${fmtPct(it?.change_pct)}</span>
      ${zoneBadge(it?.zone)}
    </div>
    ${ok ? `
    <div class="tm-score-line">
      <div class="tm-score-track"><div class="tm-score-bar" style="width:${score}%;background:${m.color || 'var(--flat)'}"></div></div>
      <span class="tm-score-num num">${score.toFixed(0)}</span>
    </div>
    <div class="tm-card-fac num" data-tip="${escapeHtml(facStr)}">${escapeHtml(maStr)}</div>
    <div class="tm-card-advice">${escapeHtml(it.advice || '')}</div>
    ` : `<div class="tm-card-empty">${escapeHtml((it?.missing && it.missing[0]) || '择时数据不可用')}</div>`}
  </div>`;
}

function renderIndexes(data) {
  const el = root.querySelector('#tm-indexes');
  if (!el) return;
  const idx = (data?.indexes || []).filter((x) => x);
  if (!idx.length) {
    el.innerHTML = emptyState('指数择时本次未计算', '指数行情或均线样本尚未达到本次计算条件', '', 'features');
    return;
  }
  el.innerHTML = `<div class="tm-card-grid">${idx.map((it) => timingCard(it)).join('')}</div>`;
}

/* ================= 3 个股择时 ================= */

function renderStock(data) {
  const el = root.querySelector('#tm-stock-body');
  if (!el) return;
  const st = data?.stock;
  if (!st) {
    el.innerHTML = `<div class="tm-stock-hint">${escapeHtml('输入 6 位代码查看个股的资金择时区域与评分（如 600519 / 300750）。')}</div>`;
    return;
  }
  if (st.ok === false) {
    el.innerHTML = emptyState(`暂无 ${escapeHtml(st.code || '')} 的择时数据`, (st.missing && st.missing[0]) || '日线数据未取到', '', 'features');
    return;
  }
  el.innerHTML = `<div class="tm-card-grid">${timingCard({ ...st, name: st.name || st.code }, true)}</div>
    ${st.turnover_rate != null || st.volume_ratio != null ? `<div class="tm-stock-meta num">换手 ${st.turnover_rate != null ? fmtNum(st.turnover_rate, 2) + '%' : '--'} · 量比 ${st.volume_ratio != null ? fmtNum(st.volume_ratio, 2) : '--'}</div>` : ''}`;
}

/* ================= 数据加载 ================= */

async function load(code) {
  const g = gen;
  const stampEl = root.querySelector('#tm-stamp');
  let d = null;
  const path = code ? `/timing?code=${encodeURIComponent(code)}` : '/timing';
  try { d = await api.get(path); } catch {
    if (g !== gen || !root) return;
    root.querySelector('#tm-fg').innerHTML = emptyState('择时数据加载失败', '点击顶栏刷新或稍后重试', '', 'alert');
    return;
  }
  if (g !== gen || !root) return;
  if (stampEl && d) stampEl.innerHTML = dataStamp(d.ts, d.missing);
  loadFearGreed(d);
  renderIndexes(d);
  renderStock(d);
}

function submitCode() {
  const input = root.querySelector('#tm-code-input');
  if (!input) return;
  const code = (input.value || '').trim();
  if (code && !/^\d{6}$/.test(code)) { toast('请输入 6 位股票代码', 'error'); return; }
  lastCode = code;
  sessionStorage.setItem('hermes.timing.code', code);
  renderStock(null);  // 占位提示，避免旧股残留
  if (code) root.querySelector('#tm-stock-body').innerHTML = skeleton(2, [60, 16]);
  load(code);
}

export function mount(container) {
  gen += 1;
  root = container;
  injectStyle();
  lastCode = sessionStorage.getItem('hermes.timing.code') || '';

  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">资金择时</h2>
      <span class="page-sub">贪恐情绪温度计 · 0AMV 四区域（进攻/出击/震荡/防守）· 多指数 + 个股</span>
      <span style="margin-left:auto" id="tm-stamp"></span>
    </div>

    <div class="grid12" style="margin-bottom:var(--gap)">
      <section class="card hoverable sp-12">
        <div class="card-head"><h3 class="card-title">贪恐情绪温度计</h3>
          <div class="card-extra"><span style="font-size:13px;color:var(--text-3)">涨跌家数比 · 涨停温度 · 成交量比 · 资金 · 波动反向（等权）</span></div>
        </div>
        <div class="card-body" id="tm-fg">${skeleton(3, [120, 16, 16])}</div>
      </section>
    </div>

    <div class="grid12" style="margin-bottom:var(--gap)">
      <section class="card hoverable sp-12">
        <div class="card-head"><h3 class="card-title">指数择时 · 0AMV 四区域</h3>
          <div class="card-extra" style="display:flex;gap:9px;flex-wrap:wrap;font-size:13px;color:var(--text-3)">
            ${Object.entries(ZONE_META).map(([k, v]) => `<span style="display:inline-flex;align-items:center;gap:4px"><i style="width:8px;height:8px;border-radius:2px;background:${v.color};display:inline-block"></i>${k}</span>`).join('')}
          </div>
        </div>
        <div class="card-body" id="tm-indexes">${skeleton(4)}</div>
      </section>
    </div>

    <div class="grid12">
      <section class="card hoverable sp-12">
        <div class="card-head"><h3 class="card-title">个股择时</h3>
          <div class="card-extra">
            <div class="tm-code-box">
              <input id="tm-code-input" class="tm-code-input" type="text" inputmode="numeric" maxlength="6" placeholder="6 位代码" value="${escapeHtml(lastCode)}" />
              <button class="btn btn-primary tm-code-btn" id="tm-code-go">查询</button>
            </div>
          </div>
        </div>
        <div class="card-body" id="tm-stock-body"></div>
      </section>
    </div>`;

  // 绑定个股查询
  root.querySelector('#tm-code-go').addEventListener('click', submitCode);
  root.querySelector('#tm-code-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') submitCode(); });

  renderStock(null);
  if (lastCode) root.querySelector('#tm-stock-body').innerHTML = skeleton(2, [60, 16]);
  load(lastCode);

  // 交易时段每 30s 刷新（与情绪/特征页一致；盘外不刷，避免无谓请求）
  timers.push(setInterval(() => { if (isTradingTime()) load(lastCode); }, 30000));
}

export function unmount() {
  timers.forEach(clearInterval);
  timers = [];
  disposeCharts(charts);
  root = null;
}
