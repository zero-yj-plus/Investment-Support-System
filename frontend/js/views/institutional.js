/* views/institutional.js — 机构容量票（AI/半导体筛选表，按 score 降序） */

import { api, fmtMoney, fmtPct, fmtNum, fmtPrice, pctClass, isTradingTime } from '../api.js';
import { dataStamp, emptyState, skeleton, escapeHtml } from '../ui.js';

let root = null;
let timers = [];

function trendBadge(trend) {
  if (!trend) return '<span class="inst-trend flat">--</span>';
  // 后端 trend 值域仅 强势/活跃/震荡（features.py institutional_capacity），不含「多/空」字样
  const cls = trend === '强势' ? 'up' : 'flat';
  const style = trend === '活跃' ? ' style="color:var(--amber);border-color:var(--amber-line);background:var(--amber-dim)"' : '';
  return `<span class="inst-trend ${cls}"${style}>${escapeHtml(trend)}</span>`;
}

export function mount(el) {
  root = el;
  root.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">容量龙头（机构可参与）</h2>
      <span class="page-sub">流通市值达标 + 成交额≥3亿 + 趋势强的大票——资金体量大、机构进得来的方向中军，不是小盘妖股</span>
      <div class="ph-extra"><span class="data-stamp" id="inst-stamp"></span></div>
    </div>
    <section class="card" id="inst-card">
      <div class="card-body" id="inst-body">${skeleton(8)}</div>
    </section>`;

  load();
  timers.push(setInterval(() => { if (isTradingTime()) load(); }, 25000));
}

export function unmount() {
  timers.forEach(clearInterval);
  timers = [];
  root = null;
}

function gotoStock(code) {
  if (!code) return;
  sessionStorage.setItem('hermes.chat.code', code);
  location.hash = '#/chat';
}

async function load() {
  const body = root?.querySelector('#inst-body');
  if (!body) return;
  let d = null;
  try { d = await api.get('/features/institutional'); } catch { /* 下方兜底 */ }
  if (!root) return;
  const stamp = root.querySelector('#inst-stamp');
  if (stamp && d) stamp.innerHTML = dataStamp(d.data_date || d.ts, d.missing);

  const stocks = (d?.stocks || []).filter((s) => s && s.code);
  if (!stocks.length) {
    body.innerHTML = emptyState('容量龙头暂不可用',
      '此表依赖东财板块/概念数据；本机所在网络对东财受限时会暂时无数据（非故障）。交易时段仍空则走"选股/盘前计划"看方向龙头。');
    return;
  }
  const rows = stocks.slice().sort((a, b) => (Number(b.score) || 0) - (Number(a.score) || 0));
  const maxScore = Math.max(1, ...rows.map((s) => Number(s.score) || 0));

  body.innerHTML = `<div class="tbl-wrap"><table class="tbl">
    <thead><tr>
      <th>#</th><th class="l">代码</th><th class="l">名称</th><th class="l">概念</th>
      <th>现价</th><th>涨跌幅</th><th>流通市值</th><th>成交额</th><th>换手</th>
      <th>主力净流入</th><th>趋势</th><th>评分</th>
    </tr></thead>
    <tbody>${rows.map((s, i) => {
      const score = Number(s.score) || 0;
      const w = Math.max(4, (score / maxScore) * 100);
      return `<tr>
        <td style="color:var(--text-3)">${i + 1}</td>
        <td class="l"><span class="code-link" data-code="${escapeHtml(s.code)}">${escapeHtml(s.code)}</span></td>
        <td class="l cn" style="font-weight:600">${escapeHtml(s.name || '--')}</td>
        <td class="l"><span class="inst-concept">${escapeHtml(s.concept || '--')}</span></td>
        <td>${fmtPrice(s.price)}</td>
        <td><span class="chg-pill ${pctClass(s.change_pct)}">${fmtPct(s.change_pct)}</span></td>
        <td>${fmtMoney(s.float_mv, 1)}</td>
        <td>${fmtMoney(s.amount, 1)}</td>
        <td>${s.turnover_rate != null ? fmtNum(s.turnover_rate, 1) + '%' : '--'}</td>
        <td class="${pctClass(s.main_net)}">${fmtMoney(s.main_net, 1)}</td>
        <td>${trendBadge(s.trend)}</td>
        <td><div class="inst-score" data-tip="综合评分 ${score}"><div class="inst-score-bar" style="width:${w}%"></div><span class="inst-score-num num">${Math.round(score)}</span></div></td>
      </tr>`;
    }).join('')}</tbody></table></div>
    <div style="font-size:15px;color:var(--text-3);margin-top:8px">点击代码进入「盘中问股」深度分析 · 评分越高代表容量/资金/趋势综合越优</div>`;

  body.querySelectorAll('.code-link').forEach((a) => {
    a.addEventListener('click', () => gotoStock(a.dataset.code));
  });
}
