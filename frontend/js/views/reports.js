/* views/reports.js — 报告中心（查看 / SSE 生成 / 下载 PDF / 推送） */

import { api, ssePost, todayStr } from '../api.js';
import { icon, escapeHtml, renderMd, toast, emptyState, skeleton } from '../ui.js';
import { downloadPdf } from '../export.js';

const RTYPES = [
  { id: 'morning', label: '早盘内参', time: '08:50' },
  { id: 'midday', label: '尾盘草案', time: '14:45' },
  { id: 'tail', label: '尾盘定稿', time: '14:52' },
  { id: 'close', label: '收盘复盘', time: '15:30' },
  { id: 'evening', label: '次日策略', time: '19:30' },
  { id: 'trend', label: '趋势票', time: '19:30' },
];

let root = null;
let rtype = 'morning';
let date = todayStr();
let generating = false;
let aborter = null;
let historyDates = [];
let loadSeq = 0;
// 分阶段等待：未收到任何 stage 事件时的兜底文案
const STAGE_FALLBACK = '处理中…';

function rtypeLabel() { return RTYPES.find((r) => r.id === rtype)?.label || rtype; }

function reportVersionLabel(report) {
  const version = report?.plan_version || report?.report_version || 1;
  const hash = String(report?.content_hash || '').slice(0, 12);
  return hash ? `定稿 v${version} · ${hash}` : `历史定稿 v${version} · 未记录指纹`;
}

function renderHistory() {
  const host = root?.querySelector('#report-history');
  if (!host) return;
  // 手工日期也要进入左侧日期轨道，否则日期输入已切换、左侧却没有选中项。
  const dates = [...new Set([date, ...historyDates, todayStr()])]
    .filter((item) => /^\d{4}-\d{2}-\d{2}$/.test(item))
    .sort().reverse();
  host.innerHTML = dates.slice(0, 30).map((item) => `
    <button class="report-history-item ${item === date ? 'active' : ''}" data-report-date="${escapeHtml(item)}">
      <span class="report-history-day num">${escapeHtml(item)}</span>
      <span>${item === todayStr() ? '今天' : '查看'}</span>
    </button>`).join('');
  host.querySelectorAll('[data-report-date]').forEach((btn) => btn.addEventListener('click', () => {
    if (generating) return;
    date = btn.dataset.reportDate;
    const input = root?.querySelector('#report-date');
    if (input) input.value = date;
    renderHistory();
    load();
  }));
}

async function loadHistory() {
  try {
    const data = await api.get('/archive/dates');
    historyDates = Array.isArray(data?.dates) ? data.dates.slice().sort().reverse() : [];
  } catch {
    historyDates = [];
  }
  renderHistory();
}

async function load() {
  if (generating) return;
  const seq = ++loadSeq;
  const requestedType = rtype;
  const requestedDate = date;
  const main = root.querySelector('#report-main');
  main.style.minHeight = '360px';
  main.innerHTML = `<div class="card-body">${skeleton(6, [26, 14, 14, 14, 14, 14])}</div>`;
  let d;
  try { d = await api.get(`/reports/${requestedType}?date=${encodeURIComponent(requestedDate)}`); } catch (e) {
    if (seq !== loadSeq || !root) return;
    main.style.removeProperty('min-height');
    main.innerHTML = `<div class="card-body">${emptyState('报告加载失败', e.message, '', 'alert')}</div>`;
    return;
  }
  if (seq !== loadSeq || !root) return;
  // 固定高度只用于加载骨架；空态、失败态和正文都按内容自然撑开，避免大片空白。
  main.style.removeProperty('min-height');
  if (!d || d.ok === false || !d.content_md) {
    main.innerHTML = `<div class="card-body">${emptyState(
      `${requestedDate} 暂无「${rtypeLabel()}」`,
      d?.error || '该报告尚未生成。生成任务会独立运行并保存；切换页面不会丢失任务。',
      `<button class="btn btn-primary" id="report-gen" style="margin-top:8px">${icon('spark')}生成今日${escapeHtml(rtypeLabel())}</button>`,
      'reports'
    )}</div>`;
    main.querySelector('#report-gen')?.addEventListener('click', generate);
    return;
  }
  const pushAllowed = requestedType !== 'midday';
  main.innerHTML = `
    <div class="card-body">
      <div class="report-state-strip">
        <span class="report-state ok">已生成</span>
        <span class="report-state ${pushAllowed && d.pushed ? 'ok' : 'idle'}">${pushAllowed ? (d.pushed ? '已推送' : '未推送') : '草案不推送'}</span>
        <span class="report-state idle" title="页面、推送与 PDF 用此内容指纹对账">${escapeHtml(reportVersionLabel(d))}</span>
      </div>
      <div class="report-meta" style="margin-bottom:14px">
        <h2 style="font-size:18px;color:var(--text)">${escapeHtml(d.title || rtypeLabel())}</h2>
        <span class="num">${escapeHtml(d.created_at || '')}</span>
        <span style="margin-left:auto;display:flex;gap:8px;align-items:center">
          <button class="btn btn-sm" id="report-pdf">${icon('save')}下载 PDF</button>
          <button class="btn btn-sm btn-primary" id="report-push" ${pushAllowed ? '' : 'disabled title="14:45 尾盘草案仅供 14:52 定稿复核，不能单独推送"'}>${icon('fly')}${pushAllowed ? '推送' : '不可推送'}</button>
        </span>
      </div>
      <article class="md-body report-body">${renderMd(d.content_md)}</article>
    </div>`;
  main.querySelector('#report-pdf')?.addEventListener('click', downloadReportPdf);
  main.querySelector('#report-push')?.addEventListener('click', push);
}

function reportArticle() { return root?.querySelector('.report-body'); }

async function downloadReportPdf() {
  const art = reportArticle();
  if (!art) { toast('请先生成报告', 'error'); return; }
  await downloadPdf(art, `${rtypeLabel()}-${date}`);
}

async function generate() {
  if (generating) return;
  // 生成接口始终生成今日版本；先同步日期，避免在历史日期页面误以为会回写历史报告。
  date = todayStr();
  const dateInput = root?.querySelector('#report-date');
  if (dateInput) dateInput.value = date;
  renderHistory();
  generating = true;
  aborter = new AbortController();
  const main = root.querySelector('#report-main');
  // 分阶段等待：收到首个 delta 前在指示器内显示阶段文案 + 已等待秒数（每秒自增）
  main.innerHTML = `
    <div class="card-body">
      <div class="report-meta" style="margin-bottom:14px">
        <h2 style="font-size:18px;color:var(--text)">${escapeHtml(rtypeLabel())}</h2>
        <span class="gen-indicator"><span class="gdot"></span><span class="gen-stage">${escapeHtml(STAGE_FALLBACK)}</span> <span class="gen-secs num">0s</span></span>
        <span style="margin-left:auto"><button class="btn btn-sm btn-danger" id="gen-stop">${icon('stop')}停止</button></span>
      </div>
      <article class="md-body report-body" id="gen-body"><span class="typing-cursor"></span></article>
    </div>`;
  const body = main.querySelector('#gen-body');
  main.querySelector('#gen-stop').addEventListener('click', () => aborter?.abort());

  // 阶段等待计时器：收到首个 delta / done / error 时清除；无 stage 事件时用兜底文案
  let waitSecs = 0;
  let stageText = '';
  let firstDelta = false;
  let stageTimer = null;
  const indicator = () => root?.querySelector('.gen-indicator');
  const renderStageWait = () => {
    if (firstDelta) return;
    const el = indicator();
    if (!el) return;
    const t = el.querySelector('.gen-stage');
    const s = el.querySelector('.gen-secs');
    if (t) t.textContent = stageText || STAGE_FALLBACK;
    if (s) s.textContent = waitSecs + 's';
  };
  const clearStageTimer = () => { if (stageTimer) { clearInterval(stageTimer); stageTimer = null; } };
  stageTimer = setInterval(() => { waitSecs += 1; renderStageWait(); }, 1000);

  let acc = '';
  let rafPending = false;
  const paint = () => {
    rafPending = false;
    if (body?.isConnected) body.innerHTML = renderMd(acc) + '<span class="typing-cursor"></span>';
  };

  try {
    await ssePost(`/reports/${rtype}/generate`, {}, {
      signal: aborter.signal,
      onStage(s) {
        if (firstDelta) return;
        stageText = String(s || '');
        renderStageWait();
      },
      onDelta(t) {
        if (!firstDelta) {
          // 收到首个 delta：停止阶段提示，指示器切回生成状态，正文开始渲染。
          firstDelta = true;
          clearStageTimer();
          const el = indicator();
          if (el) el.innerHTML = `<span class="gdot"></span>当前模型正在生成…`;
        }
        acc += t;
        if (!rafPending) { rafPending = true; requestAnimationFrame(paint); }
      },
      onError(err) {
        clearStageTimer();
        toast('生成失败：' + err, 'error');
      },
      onDone(obj) {
        clearStageTimer();
        if (obj && obj.truncated) toast('⚠️ 报告因长度被截断，后段可能缺失，建议重新生成', 'error', 6000);
        else if (obj && obj.saved) toast('报告已生成并保存', 'success');
        else if (acc) toast('生成完成', 'success');
        // 生成始终针对今日；done.date 缺失时回退到今天而非可能过时的选中日期，避免回查显示"暂无报告"。
        date = (obj && obj.date) || todayStr();
      },
    });
  } catch (e) {
    if (e.name === 'AbortError') toast('已停止生成', 'info');
    else toast('生成异常：' + e.message, 'error');
  } finally {
    clearStageTimer();
    generating = false;
    aborter = null;
    const dateInput = root?.querySelector('#report-date');
    if (dateInput) dateInput.value = date;
    if (root) load();
  }
}

async function push() {
  if (rtype === 'midday') {
    toast('14:45 尾盘草案仅供定稿复核，不能单独推送', 'info');
    return;
  }
  // 推送走文字卡片（经 /reports/{rtype}/push → push_report，与定时推送同款、已验证可靠）。
  // 不走 PDF 文件附件：iLink 底层对附件失败会"假成功"，待网关补丁上线后再切真 PDF 推送。
  const btn = root.querySelector('#report-push');
  if (btn) { btn.disabled = true; btn.innerHTML = `<span class="btn-spin"></span>推送中`; }
  try {
    const r = await api.post(`/reports/${rtype}/push`, { date });
    if (r && r.ok) { toast('已推送' + (r.msg ? '（' + r.msg + '）' : ''), 'success'); load(); }
    else toast('推送失败：' + (r?.error || '未知错误'), 'error');
  } catch (e) {
    toast('推送失败：' + e.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = `${icon('fly')}推送`; }
  }
}

export function mount(container) {
  root = container;
  date = todayStr();
  historyDates = [];
  if (!document.getElementById('reports-ux-style')) {
    const style = document.createElement('style');
    style.id = 'reports-ux-style';
    style.textContent = `.reports-shell{display:grid;grid-template-columns:220px minmax(0,1fr);gap:16px;align-items:start}.report-history-card{position:sticky;top:calc(var(--topbar-h) + 16px)}
      .report-history-list{display:flex;flex-direction:column;gap:5px}.report-history-item{display:flex;align-items:center;justify-content:space-between;gap:8px;width:100%;min-height:42px;padding:8px 10px;border:1px solid transparent;border-radius:var(--radius-sm);background:transparent;color:var(--text-2);cursor:pointer;text-align:left}
      .report-history-item:hover{background:var(--panel-2);color:var(--text)}.report-history-item.active{color:var(--amber);border-color:var(--amber-line);background:var(--amber-dim)}.report-history-day{font-size:13px;font-weight:650}
      .report-state-strip{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:14px}.report-state{display:inline-flex;align-items:center;min-height:26px;padding:2px 8px;border-radius:999px;border:1px solid var(--border);font-size:12px;font-weight:700}.report-state.ok{color:var(--down);border-color:var(--down-line);background:var(--down-bg)}.report-state.idle{color:var(--text-2);background:var(--panel-2)}
      .report-work{min-width:0}.report-toolbar{margin-bottom:12px}.report-toolbar .tabs{overflow-x:auto;flex-wrap:nowrap}.report-toolbar .tab{flex:0 0 auto}
      @media(max-width:900px){.reports-shell{grid-template-columns:1fr}.report-history-card{position:static}.report-history-list{display:flex;flex-direction:row;overflow-x:auto}.report-history-item{min-width:150px}.report-toolbar{display:flex;flex-direction:column;align-items:stretch;gap:10px}.report-toolbar .tabs{width:100%}#report-date{width:100%!important}}`;
    document.head.appendChild(style);
  }
  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">报告中心</h2>
      <span class="page-sub">六类定时报告 · 支持即时生成与 PDF，定稿类可推送</span>
    </div>
    <div class="reports-shell">
      <aside class="card report-history-card"><div class="card-head"><h3 class="card-title">报告日期</h3></div><div class="card-body"><div class="report-history-list" id="report-history">${skeleton(5)}</div></div></aside>
      <div class="report-work">
        <div class="report-toolbar">
          <div class="tabs" id="report-tabs" style="flex:1;border-bottom:none">
            ${RTYPES.map((r) => `<button class="tab ${r.id === rtype ? 'active' : ''}" data-rtype="${r.id}">${r.label}<span style="font-size:12px;color:var(--text-3);margin-left:5px">${r.time}</span></button>`).join('')}
          </div>
          <label class="sr-only" for="report-date">报告日期</label><input type="date" class="input" id="report-date" style="width:160px" value="${date}" max="${todayStr()}">
        </div>
        <section class="card" id="report-main"></section>
      </div>
    </div>`;

  container.querySelector('#report-tabs').addEventListener('click', (e) => {
    const tab = e.target.closest('.tab');
    if (!tab || generating) return;
    rtype = tab.dataset.rtype;
    container.querySelectorAll('#report-tabs .tab').forEach((t) => t.classList.toggle('active', t === tab));
    load();
  });
  container.querySelector('#report-date').addEventListener('change', (e) => {
    if (generating) { e.target.value = date; return; }
    date = e.target.value || todayStr();
    renderHistory();
    load();
  });

  loadHistory();
  load();
}

export function unmount() {
  if (aborter) { try { aborter.abort(); } catch { /* noop */ } aborter = null; }
  generating = false;
  loadSeq += 1;
  root = null;
}
