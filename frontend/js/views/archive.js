/* views/archive.js — 存档回看（日期列表 + 报告手风琴） */

import { api, todayStr } from '../api.js';
import { icon, escapeHtml, renderMd, emptyState, skeleton } from '../ui.js';
import { PHASE_COLORS } from '../charts.js';

const RTYPE_ORDER = [
  ['morning', '早盘内参'], ['midday', '尾盘决策草案'], ['tail', '尾盘决策定稿'],
  ['close', '收盘复盘'], ['evening', '次日策略'], ['trend', '每日趋势票'],
];

let root = null;
let dates = [];
let activeDate = '';

const ARCHIVE_STATUS = {
  proposed: ['候选提案', '#a16207'],
  approved: ['风险闸已批准', '#0f766e'],
  vetoed: ['风险闸已否决', '#be123c'],
  expired: ['已过期', '#64748b'],
  formal: ['正式建议', '#1d4ed8'],
  formal_legacy: ['旧格式正式记录', '#64748b'],
  triggered: ['条件已触发', '#0f766e'],
  not_triggered: ['条件未触发', '#a16207'],
  not_applicable: ['无需触发', '#64748b'],
  unknown: ['未留存触发证据', '#64748b'],
  hit: ['回验命中', '#0f766e'],
  miss: ['回验未命中', '#be123c'],
  partial: ['部分命中', '#a16207'],
  pending: ['待回验', '#64748b'],
  incomparable: ['证据不足·不可比', '#a16207'],
  untriggered: ['未触发·不计输赢', '#64748b'],
  evaluated: ['已回验', '#1d4ed8'],
};

function archiveStatus(status) {
  const key = String(status || 'unknown');
  const [label, color] = ARCHIVE_STATUS[key] || [key, '#64748b'];
  return `<span style="display:inline-flex;align-items:center;padding:2px 7px;border:1px solid ${color}55;border-radius:4px;color:${color};background:${color}12;font-size:12px;font-weight:650">${escapeHtml(label)}</span>`;
}

function archiveIdentity(row) {
  const code = String(row?.symbol || row?.code || '');
  const name = String(row?.name || '');
  return [code, name].filter(Boolean).join(' ');
}

function archiveLineage(row) {
  const parts = [];
  if (row?.proposal_id) parts.push(`proposal ${row.proposal_id}`);
  if (row?.event_id || row?.recommendation_event_id) parts.push(`event ${row.event_id || row.recommendation_event_id}`);
  if (row?.rec_id || row?.legacy_rec_id) parts.push(`record ${row.rec_id || row.legacy_rec_id}`);
  return parts.length
    ? `<div style="font-size:12px;color:var(--text-3);word-break:break-all;margin-top:5px">${escapeHtml(parts.join(' · '))}</div>`
    : '<div style="font-size:12px;color:var(--text-3);margin-top:5px">旧记录未保存统一 lineage ID</div>';
}

function archiveAdviceRow(row, stage) {
  const identity = archiveIdentity(row) || '未标记标的';
  const direction = String(row?.action || row?.direction || row?.rec_type || '');
  const source = String(row?.source || '');
  let detail = '';
  if (stage === 'candidate') {
    const condition = String(row?.entry_condition || '');
    detail = condition ? `入场条件：${condition}` : '未设结构化入场条件';
  } else if (stage === 'formal') {
    const price = row?.reference_price ?? row?.rec_price;
    detail = price == null ? '参考价未留存' : `当时参考价 ${price}`;
  } else if (stage === 'execution') {
    const evidence = row?.trigger?.evidence_id;
    detail = evidence ? `触发证据 ${evidence}` : '未绑定触发证据，不视为已成交';
  } else {
    const window = String(row?.window || '');
    const rawReturn = row?.final_return;
    const finalReturn = rawReturn !== null && rawReturn !== '' && rawReturn !== undefined && Number.isFinite(Number(rawReturn))
      ? `${Number(rawReturn).toFixed(2)}%`
      : '—';
    detail = `${window || '回验窗口未标记'} · 标的涨跌 ${finalReturn}`;
  }
  return `<div style="padding:10px 11px;border:1px solid var(--line);border-radius:6px;background:var(--panel-2)">
    <div style="display:flex;align-items:flex-start;gap:8px;justify-content:space-between;flex-wrap:wrap">
      <div style="font-size:14px;font-weight:650;color:var(--text)">${escapeHtml(identity)}${direction ? ` · ${escapeHtml(direction)}` : ''}</div>
      ${archiveStatus(row?.archive_status)}
    </div>
    <div style="font-size:13px;color:var(--text-2);line-height:1.55;margin-top:6px">${escapeHtml(detail)}</div>
    ${source ? `<div style="font-size:12px;color:var(--text-3);margin-top:4px">来源：${escapeHtml(source)}</div>` : ''}
    ${archiveLineage(row)}
  </div>`;
}

function adviceSection(title, rows, stage, emptyText) {
  return `<section>
    <div style="font-size:13px;font-weight:700;color:var(--text);margin:0 0 7px">${escapeHtml(title)} <span class="num" style="color:var(--text-3);font-weight:500">${rows.length}</span></div>
    <div style="display:grid;gap:8px">${rows.length
    ? rows.map((row) => archiveAdviceRow(row, stage)).join('')
    : `<div style="padding:10px;border:1px dashed var(--line);border-radius:6px;color:var(--text-3);font-size:13px">${escapeHtml(emptyText)}</div>`}</div>
  </section>`;
}

function renderAdviceArchive(bundle) {
  const advice = bundle?.advice_review;
  if (!advice || advice.schema_version !== 'daily-advice-archive.v1') {
    return `<div class="acc-item">
      <div class="acc-head" data-acc><span style="color:var(--brand1)">${icon('reports')}</span>当日建议与回验<span class="acc-arrow">${icon('chevR')}</span></div>
      <div class="acc-body"><div style="padding:12px;border:1px solid var(--line);border-radius:6px;background:var(--panel-2);font-size:13px;color:var(--text-2);line-height:1.6">
        这是旧版日档案，当时未冻结建议与回验账本。系统没有用当前行情倒推或重算历史状态。
      </div></div>
    </div>`;
  }
  const candidates = Array.isArray(advice.candidates) ? advice.candidates : [];
  const formal = Array.isArray(advice.formal_recommendations) ? advice.formal_recommendations : [];
  const executions = Array.isArray(advice.executions) ? advice.executions : [];
  const reviews = Array.isArray(advice.reviews) ? advice.reviews : [];
  return `<div class="acc-item">
    <div class="acc-head" data-acc>
      <span style="color:var(--brand1)">${icon('reports')}</span>当日建议与回验
      <span style="font-size:13px;color:var(--text-3);font-weight:400">候选 ${candidates.length} · 正式 ${formal.length} · 回验 ${reviews.length}</span>
      <span class="acc-arrow">${icon('chevR')}</span>
    </div>
    <div class="acc-body">
      <div style="padding:10px 12px;border-left:3px solid var(--brand1);background:var(--panel-2);font-size:13px;color:var(--text-2);line-height:1.6;margin-bottom:12px">
        存档时点 ${escapeHtml(advice.snapshot_at || bundle.archived_at || '—')} · 历史只读 · 不从实时行情重算。<br>
        “执行状态”仅表示当时已落库的入场触发证据；未绑定券商成交回执，不得解读为真实成交。
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;align-items:start">
        ${adviceSection('候选提案', candidates, 'candidate', '当日无候选提案')}
        ${adviceSection('正式建议', formal, 'formal', '当日无正式建议')}
        ${adviceSection('执行 / 触发证据', executions, 'execution', '当日无可展示的触发记录')}
        ${adviceSection('当日回验', reviews, 'review', '当日无新的回验结果')}
      </div>
    </div>
  </div>`;
}

function renderDates() {
  const el = root.querySelector('#ar-dates');
  const today = todayStr();
  if (!dates.length) {
    el.innerHTML = emptyState('暂无存档', '每日 22:00 自动存档当天的报告与情绪快照', '', 'archive');
    return;
  }
  el.innerHTML = dates.map((d) => `
    <div class="ar-date ${d === activeDate ? 'active' : ''}" data-date="${escapeHtml(d)}">
      <span>${escapeHtml(d)}</span>
      ${d === today ? '<span class="today-tag">今天</span>' : ''}
    </div>`).join('');
}

async function loadDay(date) {
  activeDate = date;
  renderDates();
  const el = root.querySelector('#ar-detail');
  el.style.minHeight = '400px';
  el.innerHTML = `<div class="card-body">${skeleton(5, [40, 20, 20, 20, 20])}</div>`;
  let d;
  try { d = await api.get(`/archive/${encodeURIComponent(date)}`); } catch (e) {
    if (!root || activeDate !== date) return;
    el.style.removeProperty('min-height');
    el.innerHTML = `<div class="card-body">${emptyState('存档加载失败', e.message, '', 'alert')}</div>`;
    return;
  }
  // 快速连点两个日期时，慢响应后到会覆盖详情面板，与左侧高亮日期错位——只渲染仍是当前选中日期的响应
  if (!root || activeDate !== date) return;
  // 详情的固定高度仅服务加载骨架，终态按实际内容自然撑开。
  el.style.removeProperty('min-height');
  if (!d || d.ok === false) {
    el.innerHTML = `<div class="card-body">${emptyState('该日无存档', d?.error || '')}</div>`;
    return;
  }
  const s = d.sentiment || null;
  const pc = s ? (PHASE_COLORS[s.phase] || '#8b93a7') : '#8b93a7';
  const reports = d.reports || {};
  const avail = RTYPE_ORDER.filter(([k]) => reports[k] && reports[k].content_md);

  el.innerHTML = `<div class="card-body">
    <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px">
      <h3 style="font-size:17px" class="num">${escapeHtml(d.date || date)}</h3>
      ${s ? `
        <span class="phase-badge" style="color:${pc};border-color:${pc}55;background:${pc}1a;margin-bottom:0">${escapeHtml(s.phase ?? '--')}</span>
        <span style="font-size:15px;color:var(--text-2)">情绪分 <b class="num" style="font-size:16px;color:var(--text)">${s.score ?? '--'}</b></span>`
        : '<span style="font-size:14px;color:var(--text-3)">该日无情绪快照</span>'}
      ${d.archived_at ? `<span style="margin-left:auto;font-size:15px;color:var(--text-3)" class="num">存档于 ${escapeHtml(d.archived_at)}</span>` : ''}
    </div>
    ${avail.map(([k, label], i) => {
      const r = reports[k];
      return `<div class="acc-item ${i === 0 ? 'open' : ''}">
        <div class="acc-head" data-acc>
          <span style="color:var(--brand1)">${icon('reports')}</span>
          ${escapeHtml(r.title || label)}
          <span style="font-size:15px;color:var(--text-3);font-weight:400" class="num">${escapeHtml(r.created_at || '')}</span>
          <span class="acc-arrow">${icon('chevR')}</span>
        </div>
        <div class="acc-body"><article class="md-body report-body">${renderMd(r.content_md)}</article></div>
      </div>`;
    }).join('')}
    ${renderAdviceArchive(d)}
    ${(d.holdings && d.holdings.length) ? `<div class="acc-item">
      <div class="acc-head" data-acc><span style="color:var(--brand1)">${icon('holdings')}</span>持仓快照（${d.holdings.length}）<span class="acc-arrow">${icon('chevR')}</span></div>
      <div class="acc-body"><div style="overflow-x:auto"><table class="num" style="width:100%;border-collapse:collapse;font-size:14px">
        <thead><tr style="color:var(--text-3);text-align:left"><th style="padding:6px">代码</th><th>名称</th><th>成本</th><th>股数</th><th>备注</th></tr></thead>
        <tbody>${d.holdings.map((h) => `<tr style="border-top:1px solid var(--line)"><td style="padding:6px">${escapeHtml(String(h.code || ''))}</td><td>${escapeHtml(String(h.name || ''))}</td><td>${escapeHtml(String(h.cost ?? ''))}</td><td>${escapeHtml(String(h.shares ?? ''))}</td><td style="color:var(--text-3)">${escapeHtml(String(h.note || ''))}</td></tr>`).join('')}</tbody>
      </table></div></div>
    </div>` : ''}
    ${(d.knowledge_today && d.knowledge_today.length) ? `<div class="acc-item">
      <div class="acc-head" data-acc><span style="color:var(--brand1)">${icon('knowledge')}</span>当日沉淀 · 产业链/知识（${d.knowledge_today.length}）<span class="acc-arrow">${icon('chevR')}</span></div>
      <div class="acc-body"><article class="md-body">${d.knowledge_today.map((k) => `<div style="border-left:2px solid var(--brand1);padding-left:10px;margin:0 0 12px"><div style="font-weight:600">${escapeHtml(String(k.title || ''))} <span style="font-size:12px;color:var(--text-3)">[${escapeHtml(String(k.category || ''))}]</span></div><div style="font-size:13px;color:var(--text-2);white-space:pre-wrap">${escapeHtml(String(k.content || '').slice(0, 800))}</div></div>`).join('')}</article></div>
    </div>` : ''}
    ${(!avail.length && !(d.holdings && d.holdings.length) && !(d.knowledge_today && d.knowledge_today.length)) ? emptyState('该日没有已存档的内容', '') : ''}
  </div>`;
}

async function load() {
  const el = root.querySelector('#ar-dates');
  el.innerHTML = skeleton(6, [18, 18, 18, 18, 18, 18]);
  try {
    const d = await api.get('/archive/dates');
    dates = (d && Array.isArray(d.dates)) ? d.dates : [];
  } catch (e) {
    el.innerHTML = emptyState('日期列表加载失败', e.message, '', 'alert');
    return;
  }
  if (!root) return;
  renderDates();
  if (dates.length) loadDay(dates[0]);
  else {
    const detail = root.querySelector('#ar-detail');
    detail.style.removeProperty('min-height');
    detail.innerHTML = `<div class="card-body">${emptyState('暂无任何存档', '系统每晚 22:00 自动归档当日报告与情绪数据', '', 'archive')}</div>`;
  }
}

export function mount(container) {
  root = container;
  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">蒸蛋日档案</h2>
      <span class="page-sub">日期 → 完整日档案 · 报告 / 情绪 / 建议回验 / 持仓 / 知识</span>
    </div>
    <div class="ar-layout">
      <section class="card" style="padding:10px;max-height:calc(100vh - 160px);overflow-y:auto" id="ar-dates"></section>
      <section class="card" id="ar-detail"></section>
    </div>`;

  container.querySelector('#ar-dates').addEventListener('click', (e) => {
    const item = e.target.closest('.ar-date');
    if (item && item.dataset.date !== activeDate) loadDay(item.dataset.date);
  });
  container.querySelector('#ar-detail').addEventListener('click', (e) => {
    const head = e.target.closest('[data-acc]');
    if (head) head.closest('.acc-item').classList.toggle('open');
  });

  load();
}

export function unmount() {
  root = null;
  dates = [];
  activeDate = '';
}
