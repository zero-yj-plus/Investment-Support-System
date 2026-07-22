/* views/restructuring.js — 重组事件深度拆解（8 大块机构研报式·SSE 渲染 + 历史 + 存入知识库）
 *
 * 铁律边界（前端侧）：本视图不在前端拼任何数字，纯渲染 AI 返回的 markdown；
 * 事件事实只来自用户贴入的公告/材料原文（走 /chat mode='restructuring'，材料即用户消息），
 * 行情底座（可选 6 位代码）由后端 chat_context 注入真实 quote；缺的关键信息由后端 prompt
 * 强制列进「⚠️ 还需你提供」清单，绝不编造。
 */

import { api, aiReadiness, ssePost, lsGet, lsSet } from '../api.js';
import { icon, escapeHtml, renderMd, toast, emptyState, confirmModal } from '../ui.js';

const LS_KEY = 'hermes.restructuring.history';
const MAX_HISTORY = 20;
// 流式重绘最短间隔（ms）：长研报逐 chunk 全量重渲染会 O(n²) 卡顿，节流到约每秒数次。
const STREAM_PAINT_MS = 120;
const CODE_RE = /^\d{6}$/;

let root = null;
let history = [];
let activeIdx = -1;
let running = false;
let aborter = null;
let curLabel = '';
let curCode = '';
let curMaterial = '';
let curContent = '';

function fmtTime(ts) {
  const d = new Date(ts);
  if (isNaN(d)) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// 历史条目标签：取材料首个非空行前 18 字（公告标题通常在第一行），否则用代码
function makeLabel(material, code) {
  const line = (material || '').split('\n').map((s) => s.trim()).find(Boolean) || '';
  const head = line.slice(0, 18);
  if (head) return head + (line.length > 18 ? '…' : '');
  return code || '重组拆解';
}

function renderHistory() {
  const el = root.querySelector('#restr-history');
  if (!history.length) {
    el.innerHTML = `<div style="font-size:14px;color:var(--text-3);padding:8px 4px">暂无拆解记录</div>`;
    return;
  }
  el.innerHTML = history.map((h, i) => `
    <div class="history-item ${i === activeIdx ? 'active' : ''}" data-i="${i}">
      ${icon('reports')}<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(h.label || h.code || '重组拆解')}</span>
      <span class="hi-time num">${fmtTime(h.ts)}</span>
    </div>`).join('');
}

function renderResult({ streaming = false } = {}) {
  const el = root.querySelector('#restr-result');
  if (!curContent && !streaming) {
    el.innerHTML = emptyState(
      '贴入重组/并购公告，开始 8 大块深度拆解',
      '框架：公告要点速览 / 标的全景基本面 / 上市公司现状 / 战略逻辑 / 五大整合方向 / 业绩预期 / 估值逻辑 / 风险提示。材料未披露的关键信息会列进「⚠️ 还需你提供」清单，绝不编造；填 6 位代码可注入真实行情做估值底座。',
      '', 'reports');
    return;
  }
  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap">
      <h3 style="font-size:16px">重组拆解：<span>${escapeHtml(curLabel)}</span>${curCode ? `<span class="num" style="color:var(--text-3);font-size:13px;margin-left:8px">${escapeHtml(curCode)}</span>` : ''}</h3>
      ${streaming
        ? `<span class="gen-indicator"><span class="gdot"></span>拆解中…</span><span style="margin-left:auto"><button class="btn btn-sm btn-danger" id="restr-stop">${icon('stop')}停止</button></span>`
        : `<span style="margin-left:auto;display:flex;gap:8px">
            <button class="btn btn-sm" id="restr-rerun">${icon('refresh')}重新生成</button>
            <button class="btn btn-sm btn-primary" id="restr-save">${icon('save')}存入知识库</button>
          </span>`}
    </div>
    <article class="md-body report-body" id="restr-md">${renderMd(curContent)}${streaming ? '<span class="typing-cursor"></span>' : ''}</article>`;
  el.querySelector('#restr-stop')?.addEventListener('click', () => aborter?.abort());
  el.querySelector('#restr-rerun')?.addEventListener('click', () => analyze(curMaterial, curCode));
  el.querySelector('#restr-save')?.addEventListener('click', saveToKnowledge);
}

async function analyze(material, code) {
  if (running) return;
  material = (material || '').trim();
  code = (code || '').trim();
  if (code && !CODE_RE.test(code)) { toast('股票代码需为 6 位数字（或留空）', 'error'); return; }
  if (!material && !code) { toast('请先贴入重组公告/材料原文（或至少给 6 位代码）', 'error'); return; }
  const readiness = await aiReadiness();
  if (!readiness.ready) {
    root.querySelector('#restr-result').innerHTML = emptyState(
      'AI 重组拆解尚未启用',
      `${readiness.reason}已输入的材料不会被发送，也不会伪造报告。`,
      '<button type="button" class="btn btn-sm" id="restr-model-settings">去系统设置</button>',
      'alert',
    );
    root.querySelector('#restr-model-settings')?.addEventListener('click', () => { location.hash = '#/settings'; });
    return;
  }
  running = true;
  curMaterial = material;
  curCode = code;
  curLabel = makeLabel(material, code);
  curContent = '';
  activeIdx = -1;
  aborter = new AbortController();
  renderHistory();
  renderResult({ streaming: true });
  const mdEl = () => root?.querySelector('#restr-md');

  let rafPending = false;
  let lastPaint = 0;
  // 流式阶段只做轻量纯文本渲染，完整 markdown 解析延到结束后一次做。
  const paint = () => {
    rafPending = false;
    lastPaint = performance.now();
    const el = mdEl();
    if (el) el.innerHTML = escapeHtml(curContent).replace(/\n/g, '<br>') + '<span class="typing-cursor"></span>';
  };
  const schedulePaint = () => {
    if (rafPending) return;
    rafPending = true;
    const wait = Math.max(0, STREAM_PAINT_MS - (performance.now() - lastPaint));
    if (wait <= 0) requestAnimationFrame(paint);
    else setTimeout(() => { if (rafPending) requestAnimationFrame(paint); }, wait);
  };

  // 材料即用户消息（后端数据边界：交易条款/标的财务只认它）；只给代码时明确告知 AI 材料缺席。
  const message = material
    || '（用户未贴公告材料，仅提供了 6 位股票代码）请按 8 大块框架输出：可定性推演的定性并标 [常识推演]，其余关键信息逐条列入「⚠️ 还需你提供」，绝不编造。';

  const saveHist = () => {
    if (!curContent) return;
    history.unshift({ label: curLabel, code, material: material.slice(0, 20000), ts: Date.now(), content: curContent.slice(0, 60000) });
    history = history.slice(0, MAX_HISTORY);
    lsSet(LS_KEY, history);
    activeIdx = 0;
  };

  try {
    await ssePost('/chat', {
      mode: 'restructuring',
      message,
      code,
      industry: '',
      history: [],
    }, {
      signal: aborter.signal,
      onError(err) { toast('拆解失败：' + err, 'error'); },
      onDelta(t) {
        curContent += t;
        schedulePaint();
      },
      onDone() {
        saveHist();
        if (curContent) toast('拆解完成', 'success');
      },
    });
  } catch (e) {
    if (e.name === 'AbortError') {
      toast('已停止', 'info');
      saveHist();
    } else toast('拆解异常：' + e.message, 'error');
  } finally {
    running = false;
    aborter = null;
    rafPending = false; // 取消未触发的流式重绘，避免覆盖 renderResult 的最终 markdown
    if (root) { renderHistory(); renderResult(); }
  }
}

async function saveToKnowledge() {
  if (!curContent) return;
  try {
    const r = await api.post('/knowledge', {
      category: '复盘笔记',
      title: `重组拆解：${curLabel}${curCode ? `（${curCode}）` : ''}`,
      content: curContent,
    });
    if (r && r.ok !== false) toast('已存入知识库（复盘笔记）', 'success');
    else toast('保存失败：' + (r?.error || '未知错误'), 'error');
  } catch (e) {
    toast('保存失败：' + e.message, 'error');
  }
}

export function mount(container) {
  root = container;
  history = lsGet(LS_KEY, []);
  if (!Array.isArray(history)) history = [];
  activeIdx = -1;
  curLabel = '';
  curCode = '';
  curMaterial = '';
  curContent = '';

  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">重组拆解</h2>
      <span class="page-sub">基于公告或消息原文拆解交易方案、整合逻辑、估值影响与风险</span>
    </div>
    <div class="grid12">
      <section class="card sp-12">
        <div class="chain-hero">
          <h2>重组事件深度拆解</h2>
          <p>贴入上市公司重组/并购公告（或相关消息）原文，当前模型助手按 8 大块机构研报式拆解：公告要点 → 标的全景 → 上市公司现状 → 战略逻辑 → 五大整合方向 → 业绩预期 → 估值逻辑 → 风险提示。材料未披露的关键信息会列进「⚠️ 还需你提供」清单，绝不编造；可选填 6 位代码，注入真实行情作估值底座。</p>
          <textarea class="input" id="restr-material" placeholder="贴重组公告/消息原文…（核心章节即可：交易方案、标的财务、业绩承诺、募资用途；材料越全，报告越实，超长会自动截断）"></textarea>
          <div class="chain-form">
            <input class="input" id="restr-code" inputmode="numeric" maxlength="6" placeholder="上市公司代码（6 位，可选）">
            <button class="btn btn-primary" id="restr-go">${icon('spark')}开始拆解</button>
          </div>
          <div class="model-gate" id="restr-model-state" role="status" aria-live="polite" hidden></div>
        </div>
      </section>
      <section class="card hoverable sp-3">
        <div class="card-head"><h3 class="card-title">历史记录</h3>
          <div class="card-extra"><button class="btn btn-sm btn-ghost" id="restr-clear" title="清空历史">${icon('trash')}</button></div>
        </div>
        <div class="card-body" id="restr-history"></div>
      </section>
      <section class="card sp-9" style="min-height:420px">
        <div class="card-body" id="restr-result"></div>
      </section>
    </div>`;

  injectStyle();

  const matEl = container.querySelector('#restr-material');
  const codeEl = container.querySelector('#restr-code');
  const go = () => analyze(matEl.value, codeEl.value);
  const goBtn = container.querySelector('#restr-go');
  goBtn.addEventListener('click', go);
  codeEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  void aiReadiness().then((readiness) => {
    if (!root || readiness.ready) return;
    const gate = root.querySelector('#restr-model-state');
    if (!gate) return;
    gate.hidden = false;
    gate.innerHTML = `<b>AI 重组拆解尚未启用</b><span>${escapeHtml(readiness.reason)}</span><button type="button" class="btn btn-sm">去系统设置</button>`;
    goBtn.disabled = true;
    goBtn.setAttribute('aria-disabled', 'true');
    gate.querySelector('button')?.addEventListener('click', () => { location.hash = '#/settings'; });
  });

  container.querySelector('#restr-history').addEventListener('click', (e) => {
    const item = e.target.closest('.history-item');
    if (!item || running) return;
    activeIdx = Number(item.dataset.i);
    const h = history[activeIdx];
    if (h) {
      curLabel = h.label || h.code || '重组拆解';
      curCode = h.code || '';
      curMaterial = h.material || '';
      curContent = h.content || '';
      matEl.value = h.material || '';
      codeEl.value = h.code || '';
      renderHistory();
      renderResult();
    }
  });
  container.querySelector('#restr-clear').addEventListener('click', async () => {
    if (!history.length) return;
    if (await confirmModal('清空历史', '将删除本地保存的全部重组拆解记录，确定？', { danger: true, okText: '清空' })) {
      history = [];
      lsSet(LS_KEY, history);
      activeIdx = -1;
      renderHistory();
    }
  });

  renderHistory();
  renderResult();
}

export function unmount() {
  if (aborter) { try { aborter.abort(); } catch { /* noop */ } aborter = null; }
  running = false;
  root = null;
}

// 样式：复用 chain/company 的 hero/form 视觉；材料框独占整行，手机端单列不溢出。带 id 幂等注入。
function injectStyle() {
  if (document.getElementById('restructuring-style')) return;
  const s = document.createElement('style');
  s.id = 'restructuring-style';
  s.textContent = `
    #restr-material{width:100%;min-height:150px;resize:vertical;font-size:14px;line-height:1.6;
      padding:10px;box-sizing:border-box;margin-bottom:10px;font-family:inherit}
    #restr-code{font-variant-numeric:tabular-nums;letter-spacing:.5px}
    @media (max-width:760px){
      .grid12 .card.sp-3, .grid12 .card.sp-9{grid-column:1 / -1 !important}
      #restr-code{width:100%}
    }`;
  document.head.appendChild(s);
}
