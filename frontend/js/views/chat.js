/* views/chat.js — 盘中问股（核心页：SSE 流式对话 + 个股盘口侧栏） */

import { api, appState, aiReadiness, ssePost, fmtMoney, fmtPct, fmtPrice, pctClass, lsGet, lsSet } from '../api.js';
import { icon, escapeHtml, renderMd, toast, dataStamp, emptyState, skeleton, confirmModal, sourceBadge } from '../ui.js';
import { mkChart, disposeCharts, minuteOption, dailyOption, UP, DOWN } from '../charts.js';
import { downloadPdf } from '../export.js';
import { swrGet, swrPeek } from '../swr.js';
import {
  buildChainResearchRequest,
  chainResearchScopeHint,
  CHAIN_RESEARCH_CONTRACT_VERSION,
} from './_chain_shared.js';

// swr 的 age(ms)→人话；stale 帧标「更新中」，fresh 帧重画时由 dataStamp 重置回纯时间戳，自动消失。
function agoTxt(age) {
  const s = Math.round(age / 1000);
  if (s < 60) return s + ' 秒前';
  if (s < 3600) return Math.round(s / 60) + ' 分钟前';
  return Math.round(s / 3600) + ' 小时前';
}

const LS_KEY = 'hermes.chat.messages';
// 流式重绘最短间隔（ms）：限制每秒重绘次数，避免长回答逐 chunk 重渲染卡顿
const STREAM_PAINT_MS = 120;
const MODES = [
  { id: 'free', label: '自由问答' },
  { id: 'stock', label: '盘中问股' },
  { id: 'position', label: '持仓诊断' },
  { id: 'chain', label: '产业链挖掘' },
];
const PRESETS = [
  '能否继续持有？', '冲高回落怎么应对？', '现在放量是出货还是进攻？',
  '尾盘怎么操作？', '明天预期如何？', '适合追涨还是低吸？', '当前处于什么阶段？',
];

let root = null;
let charts = [];
let messages = [];
let mode = 'stock';
let curCode = '';
let curStockName = '';
let streaming = false;
let aborter = null;
let modelReady = null;
let historyCollapsed = true;
let sideCollapsed = false;
let conversationEpoch = 0; // 用户切模式/新建会话后，废弃挂起的旧会话自动恢复
// 分阶段等待：未收到任何 stage 事件时的兜底文案（后端旧版本/SSE 不转发 stage 时仍有提示）
const STAGE_FALLBACK = '处理中…';

/* ---------------- 多模态图片输入 ---------------- */
const MAX_IMAGES = 4;
let pendingImages = [];   // 待发送图片 data:URL（本轮，发送后清空；不入持久化历史）
let positionCodes = [];   // 持仓诊断时用户勾选的子集代码（空=诊断全部）；进入页面时由持仓页跳转带入

function fileToDataUrl(file) {
  return new Promise((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result || ''));
    r.onerror = () => resolve('');
    r.readAsDataURL(file);
  });
}

async function addImageFiles(files) {
  for (const f of [...files]) {
    if (!f.type || !f.type.startsWith('image/')) continue;
    if (pendingImages.length >= MAX_IMAGES) { toast(`最多 ${MAX_IMAGES} 张图片`, 'error'); break; }
    if (f.size > 6 * 1024 * 1024) { toast('单张图片需小于 6MB', 'error'); continue; }
    const url = await fileToDataUrl(f);
    if (url.startsWith('data:image/')) pendingImages.push(url);
  }
  renderThumbs();
}

function renderThumbs() {
  const wrap = root?.querySelector('#chat-thumbs');
  if (!wrap) return;
  if (!pendingImages.length) { wrap.style.display = 'none'; wrap.innerHTML = ''; return; }
  wrap.style.display = 'flex';
  wrap.innerHTML = pendingImages.map((u, i) =>
    `<span style="position:relative;display:inline-block">`
    + `<img src="${u}" alt="图" style="width:56px;height:56px;object-fit:cover;border-radius:6px;border:1px solid var(--border)">`
    + `<button type="button" class="thumb-x" data-i="${i}" title="移除" aria-label="移除第 ${i + 1} 张图片" style="position:absolute;top:-6px;right:-6px;width:18px;height:18px;border-radius:50%;border:none;background:var(--down,#e2574c);color:#fff;cursor:pointer;font-size:12px;line-height:1;padding:0">×</button>`
    + `</span>`).join('');
}

/* ---------------- 消息渲染 ---------------- */

function msgHtml(m) {
  if (m.role === 'system') {
    // 错误气泡带一键重试：用导致该错误的同一条消息+同样 mode/code 重发（retry 上下文随消息定格）
    const retryBtn = m.retry
      ? `<button type="button" class="btn btn-sm error-retry" data-eid="${escapeHtml(m.eid || '')}" aria-label="重试这次请求">${icon('refresh')}重试</button>`
      : '';
    return `<div class="msg system" data-eid="${escapeHtml(m.eid || '')}"><div class="msg-avatar" style="background:var(--up-bg);color:var(--up)">!</div><div style="min-width:0"><div class="msg-bubble">${escapeHtml(m.content)}</div>${retryBtn ? `<div class="error-actions">${retryBtn}</div>` : ''}</div></div>`;
  }
  if (m.role === 'user') {
    const imgs = Array.isArray(m.images) ? m.images : [];
    const thumbs = imgs.length
      ? `<div class="msg-imgs" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px">${imgs.map((u) => `<img src="${u}" alt="图" style="max-width:120px;max-height:120px;border-radius:8px;border:1px solid var(--border)">`).join('')}</div>`
      : (m.hadImages ? `<div class="msg-meta" style="margin-bottom:4px">📎 ${m.hadImages} 张图片</div>` : '');
    return `<div class="msg user">
      <div class="msg-avatar">我</div>
      <div style="min-width:0">${thumbs}<div class="msg-bubble">${escapeHtml(m.content)}</div><div class="msg-meta">${escapeHtml(m.ts || '')}</div></div>
    </div>`;
  }
  return `<div class="msg ai">
    <div class="msg-avatar logo-mark">${icon('logo')}</div>
    <div style="min-width:0"><div class="msg-bubble md-body">${renderMd(m.content)}</div><div class="msg-meta">研究助手${m.ts ? ' · ' + escapeHtml(m.ts) : ''}<button type="button" class="msg-pdf" title="导出此回答为 PDF" aria-label="导出此回答为 PDF">${icon('save')}导出 PDF</button></div></div>
  </div>`;
}

function emptyChatHtml() {
  return `<div class="chat-empty" role="group" aria-label="开始问股">
    <div class="chat-empty-mark" aria-hidden="true">${icon('chat')}</div>
    <h2>从一个明确动作开始</h2>
    <p>四种模式各走独立证据链：问股做个股深度分析，持仓读取成本与数量，自由问答读取市场全景，产业链核验板块与成员关系。</p>
    <div class="chat-empty-actions">
      <button type="button" class="empty-launch primary" data-empty-action="code" aria-label="输入六位股票代码">
        ${icon('search')}<span><b>输入股票代码</b><small>查询实时盘口后提问</small></span>
      </button>
      <button type="button" class="empty-launch" data-empty-action="ask" aria-label="直接输入问题">
        ${icon('chat')}<span><b>直接提问</b><small>聊市场、策略或情绪</small></span>
      </button>
      <button type="button" class="empty-launch" data-empty-action="upload" aria-label="上传截图提问">
        ${icon('plus')}<span><b>上传截图</b><small>支持粘贴、拖拽与多图</small></span>
      </button>
    </div>
    <span class="chat-empty-tip">快捷键 <kbd>/</kbd> 聚焦输入框，<kbd>Enter</kbd> 发送</span>
  </div>`;
}

function renderStream() {
  // onError 等异步路径可能在页面卸载（root=null）后才触发
  const el = root?.querySelector('#chat-stream');
  if (!el) return;
  // 旧版把四种模式全部渲染在同一条时间线；即使发给后端的 history
  // 已过滤，用户仍会在“产业链”里看到“持仓治理诊断”。显示层也必须隔离。
  const visibleMessages = messages.filter((m) => {
    if (!m || m.role === 'system') return true;
    if (!m.mode) return mode === 'stock'; // 兼容旧版未打标的问股历史
    if (m.mode !== mode) return false;
    return mode !== 'stock' || !m.code || m.code === curCode;
  });
  if (!visibleMessages.length) {
    el.innerHTML = emptyChatHtml();
    return;
  }
  el.innerHTML = visibleMessages.map(msgHtml).join('');
  scrollBottom(true);
}

function scrollBottom(force) {
  const el = root?.querySelector('#chat-stream');
  if (!el) return;
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
  // 用 behavior:'instant' 覆盖 .chat-stream 的 CSS scroll-behavior:smooth：流式长回答每帧
  // 触发置底，平滑动画会互相打断、追不上增长的内容，导致结束后视图停在半截。瞬时置底才可靠。
  if (force || nearBottom) el.scrollTo({ top: el.scrollHeight, behavior: 'instant' });
}

function nowTs() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function persist() {
  // system 错误气泡只做本次会话的瞬时展示：持久化会让 401 等早已失效的错误跨刷新永久残留。
  // 图片 base64 不入 localStorage（体积大）：仅留 hadImages 张数标记，刷新后显示「📎N张图」。
  const slim = messages.filter((m) => m && m.role !== 'system').slice(-100)
    .map((m) => (m.images && m.images.length) ? { ...m, images: undefined, hadImages: m.images.length } : m);
  lsSet(LS_KEY, slim);
}

// 错误气泡唯一 id，供「重试」按钮定位并移除旧气泡
let errSeq = 0;

/** 追加一条带重试上下文的错误气泡（不入持久化历史），retry 定格当时的 mode/code */
function pushError(content, retry) {
  messages.push({ role: 'system', content, ts: nowTs(), eid: 'e' + (++errSeq), retry });
  persist(); renderStream();
}

/* ---------------- 模式与上下文区 ---------------- */

function syncHistoryUI() {
  const shell = root?.querySelector('#chat-phase5');
  const toggle = root?.querySelector('#conv-toggle');
  if (!shell || !toggle) return;
  shell.classList.toggle('history-collapsed', historyCollapsed);
  toggle.setAttribute('aria-expanded', String(!historyCollapsed));
  toggle.setAttribute('aria-label', historyCollapsed ? '展开历史对话' : '收起历史对话');
  toggle.title = historyCollapsed ? '展开历史对话' : '收起历史对话';
  toggle.querySelector('.conv-toggle-icon').innerHTML = icon(historyCollapsed ? 'chevR' : 'chevL');
}

function syncSideUI() {
  if (!root) return;
  const layout = root.querySelector('.chat-layout');
  const side = root.querySelector('#chat-side');
  const toggle = root.querySelector('#chat-side-toggle');
  if (!layout || !side || !toggle) return;
  const compact = !!(appState.status?.ui?.chat_compact);
  const available = !compact && mode === 'stock' && /^\d{6}$/.test(curCode);
  const expanded = available && !sideCollapsed;
  layout.classList.toggle('has-stock-side', expanded);
  layout.classList.toggle('no-side', !expanded);
  side.hidden = !expanded;
  side.style.display = expanded ? '' : 'none';
  toggle.hidden = !available;
  toggle.style.display = available ? '' : 'none';
  toggle.setAttribute('aria-expanded', String(expanded));
  toggle.setAttribute('aria-label', expanded ? '收起个股行情' : '展开个股行情');
  toggle.innerHTML = expanded
    ? `${icon('chevR')}<span>收起行情</span>`
    : `${icon('chevL')}<span>展开行情</span>`;
}

function renderModeUI() {
  if (!root) return; // queryStock 的 await 返回时页面可能已卸载
  root.querySelectorAll('#chat-modes .seg').forEach((b) => {
    const active = b.dataset.mode === mode;
    b.classList.toggle('active', active);
    b.setAttribute('aria-pressed', String(active));
  });
  const ctx = root.querySelector('#chat-context');
  if (mode === 'stock') {
    ctx.innerHTML = `
      <input class="input" id="ctx-code" maxlength="6" placeholder="股票代码（6位）" inputmode="numeric" aria-label="六位股票代码" value="${escapeHtml(curCode)}">
      <button class="btn btn-sm" id="ctx-query" aria-label="查询股票实时盘口">${icon('search')}查询盘口</button>
      <span class="ctx-hint">${curStockName ? `当前标的：<b style="color:var(--brand1)">${escapeHtml(curStockName)} ${escapeHtml(curCode)}</b>` : '先查询一只股票，提问时自动携带其实时盘口上下文'}</span>`;
    ctx.querySelector('#ctx-query').addEventListener('click', () => queryStock(ctx.querySelector('#ctx-code').value.trim()));
    ctx.querySelector('#ctx-code').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') queryStock(e.target.value.trim());
    });
  } else if (mode === 'chain') {
    ctx.innerHTML = `
      <input class="input" id="ctx-industry" style="width:220px" placeholder="行业/产业链名称，如：光模块" aria-label="行业或产业链名称">
      <span class="ctx-hint" data-contract="${escapeHtml(CHAIN_RESEARCH_CONTRACT_VERSION)}">与研究中心共用完整产业链契约 · ${escapeHtml(chainResearchScopeHint())}</span>`;
  } else if (mode === 'position') {
    ctx.innerHTML = `<span class="ctx-hint">持仓诊断模式：读取成本、数量与当前价，逐股完成 60 日趋势/动量/量价/风险分析，再给继续持有与退出条件</span>`;
  } else {
    ctx.innerHTML = `<span class="ctx-hint">自由问答模式：输入一只股票的 6 位代码或完整名称，会自动注入其行情、K 线和确定性证据，无需切换页面；普通问题仍读取大盘、板块与资金</span>`;
  }
  // 预设 chips
  const presets = root.querySelector('#chat-presets');
  if (mode === 'chain') {
    presets.innerHTML = ['这条产业链的上中下游格局？', '哪个环节竞争壁垒最高？', '弹性最大的 A 股标的是？']
      .map((q) => `<button type="button" class="chip clickable preset" data-q="${escapeHtml(q)}" aria-label="问题模板：${escapeHtml(q)}">${escapeHtml(q)}</button>`).join('');
  } else {
    presets.innerHTML = PRESETS.map((q) => `<button type="button" class="chip clickable preset" data-q="${escapeHtml(q)}" aria-label="问题模板：${escapeHtml(q)}">${escapeHtml(q)}</button>`).join('');
  }
  syncSideUI();
}

/* ---------------- 个股侧栏 ---------------- */

function sideCloseHtml() {
  return `<button type="button" class="side-inline-close" aria-label="收起个股行情">${icon('chevR')}<span>收起行情</span></button>`;
}

async function queryStock(code) {
  if (!/^\d{6}$/.test(code)) { toast('请输入 6 位股票代码', 'error'); return; }
  curCode = code;
  curStockName = '';
  // 窄屏只加载行情，不自动用抽屉盖住问答输入区；用户仍可从
  // 工具栏明确展开。常见桌面宽度继续左右并列展示。
  sideCollapsed = window.innerWidth <= 1180;
  renderModeUI();
  const side = root.querySelector('#chat-side');
  const paths = {
    core: `/stock/${code}/core`,
    charts: `/stock/${code}/charts`,
    enrichment: `/stock/${code}/enrichment`,
  };
  const parts = {
    preview: null,
    core: swrPeek(paths.core),
    charts: swrPeek(paths.charts),
    enrichment: swrPeek(paths.enrichment),
  };
  const failed = new Set();
  let previewShown = Boolean(parts.core?.quote);

  const compose = () => {
    const core = parts.core || {};
    const charts = parts.charts || {};
    const enrichment = parts.enrichment || {};
    const identity = core.recognition || {};
    const quote = core.quote || parts.preview?.quote || (identity.known
      ? { code, name: identity.name || '' }
      : null);
    const minute = Array.isArray(charts.minute) ? charts.minute : [];
    const daily = Array.isArray(charts.daily) && charts.daily.length
      ? charts.daily
      : (Array.isArray(core.recent_daily) ? core.recent_daily : []);
    const missing = [
      ...(core.missing || []),
      ...(charts.missing || []),
      ...(enrichment.missing || []),
      ...[...failed].map((name) => `${name}加载失败(不影响其他模块)`),
    ];
    return {
      ok: Boolean(quote || minute.length || daily.length),
      state: failed.size || core.state === 'partial' || charts.state === 'partial' || enrichment.state === 'partial'
        ? 'partial' : 'complete',
      ts: enrichment.ts || charts.ts || core.ts || parts.preview?.ts,
      quote,
      recognition: core.recognition || parts.preview?.identity,
      recognized: core.recognized ?? parts.preview?.recognized,
      error_code: core.error_code || parts.preview?.error_code,
      error: core.error || parts.preview?.error,
      minute,
      minute_date: charts.minute_date || null,
      daily,
      board: enrichment.board || { industry: '', concepts: [] },
      flow: enrichment.flow || null,
      flow_date: enrichment.flow_date || null,
      financial: enrichment.financial || null,
      auction: enrichment.auction || null,
      sections: { ...(core.sections || {}), ...(charts.sections || {}), ...(enrichment.sections || {}) },
      missing: [...new Set(missing.filter(Boolean))],
    };
  };

  const paintMerged = (meta = {}) => {
    if (curCode !== code || !root) return false;
    const merged = compose();
    if (!merged.ok) return false;
    previewShown = Boolean(merged.quote);
    paintSide(merged, code, meta);
    return true;
  };

  // 三段的 last-good 任一存在都先秒出，后台再各自更新。
  if (!paintMerged({ stale: true, progressive: true })) {
    side.innerHTML = `${sideCloseHtml()}<div class="card" style="padding:16px">${skeleton(6, [24, 40, 16, 16, 16, 120])}</div>`;
  }

  // 最快报价单独起跑：即使 core 正在补最近日K，名称和价格也可先显示。
  const quoteProbe = api.get(`/quote/${code}`, {
    ttlMs: 3_000,
    staleTtlMs: 5 * 60_000,
    timeoutMs: 5_000,
  }).then((qr) => {
    if (curCode !== code || !root) return;
    parts.preview = qr || null;
    if (qr?.quote && !parts.core?.quote) paintMerged({ progressive: true });
  }).catch(() => { /* 快报价失败不取消其他分段 */ });

  const loadSegment = (key, label) => swrGet(paths[key], (d, meta) => {
    if (curCode !== code || !root) return;
    parts[key] = d || null;
    if (!meta.stale) failed.delete(label);
    paintMerged({ stale: meta.stale, progressive: true });
  }, {
    maxAge: 6 * 3600e3,
    onError: (_error, { hadCache }) => {
      if (curCode !== code || !root || hadCache) return;
      failed.add(label);
      paintMerged({ progressive: true });
    },
  });

  await Promise.all([
    quoteProbe,
    loadSegment('core', '首屏行情'),
    loadSegment('charts', '分时/日K'),
    loadSegment('enrichment', '行业/资金/财务'),
  ]);
  if (curCode !== code || !root) return;

  const merged = compose();
  if (merged.ok) {
    // 所有分段都已落定：缺的显示“暂不可用”，不再继续假装加载。
    paintSide(merged, code, { progressive: false });
    return;
  }
  const notFound = merged.error_code === 'stock_not_found' && merged.recognized === false;
  const title = notFound ? '股票代码不存在' : '股票已识别，行情暂未返回';
  const detail = merged.error || (notFound
    ? '请检查 6 位股票代码'
    : '已停止等待当前慢源；这不代表股票不存在');
  side.innerHTML = `${sideCloseHtml()}<div class="card" style="padding:8px">${emptyState(title, detail, '', notFound ? 'search' : 'alert')}</div>`;
}

// 渲染个股盘口侧栏（stale 与 fresh 两次调用安全：每次都 disposeCharts 重建分时/日K，无泄漏）。
function paintSide(d, code, meta = {}) {
  const side = root.querySelector('#chat-side');
  if (!side) return;
  const q = d.quote || {};
  curStockName = q.name || '';
  renderModeUI(); // 更新上下文提示里的当前标的
  const cls = pctClass(q.change_pct);
  const amp = (q.high != null && q.low != null && q.prev_close) ? ((q.high - q.low) / q.prev_close) * 100 : null;
  const chgAbs = (q.price != null && q.prev_close != null) ? q.price - q.prev_close : null;

  const flow = d.flow || null;
  const flowRows = flow ? [
    ['主力', flow.main_net], ['超大单', flow.super_net], ['大单', flow.large_net],
    ['中单', flow.medium_net], ['小单', flow.small_net],
  ] : [];
  const maxFlow = Math.max(1, ...flowRows.map(([, v]) => Math.abs(Number(v) || 0)));

  side.innerHTML = `${sideCloseHtml()}
    <div class="card" style="padding:15px 16px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div class="quote-head"><span class="q-name">${escapeHtml(q.name ?? '--')}</span><span class="q-code">${escapeHtml(q.code ?? code)}</span></div>
        ${dataStamp(d.ts, d.missing)}${meta.stale ? `<span style="color:var(--text-3);font-size:11px;margin-left:6px">${agoTxt(meta.age || 0)} · 更新中</span>` : ''}
      </div>
      <div class="quote-price">
        <span class="q-px num ${cls}" id="q-px-${escapeHtml(code)}">${fmtPrice(q.price)}</span>
        <span class="q-chg num ${cls}">${chgAbs == null ? '' : (chgAbs >= 0 ? '+' : '') + chgAbs.toFixed(2)}　${fmtPct(q.change_pct)}</span>
      </div>
      <div class="quote-grid">
        <div class="qg"><span>今开</span><b class="num ${pctClass(q.open - q.prev_close)}">${fmtPrice(q.open)}</b></div>
        <div class="qg"><span>最高</span><b class="num up">${fmtPrice(q.high)}</b></div>
        <div class="qg"><span>最低</span><b class="num down">${fmtPrice(q.low)}</b></div>
        <div class="qg"><span>昨收</span><b class="num">${fmtPrice(q.prev_close)}</b></div>
        <div class="qg"><span>换手率</span><b class="num">${fmtPct(q.turnover_rate, { signed: false, digits: 1 })}</b></div>
        <div class="qg"><span>量比</span><b class="num">${q.volume_ratio ?? '--'}</b></div>
        <div class="qg"><span>振幅</span><b class="num">${amp == null ? '--' : fmtPct(amp, { signed: false, digits: 1 })}</b></div>
        <div class="qg"><span>成交额</span><b class="num">${fmtMoney(q.amount)}</b></div>
        <div class="qg"><span>市盈率</span><b class="num">${q.pe ?? '--'}</b></div>
        <div class="qg"><span>总市值</span><b class="num">${fmtMoney(q.total_mv)}</b></div>
        <div class="qg"><span>流通市值</span><b class="num">${fmtMoney(q.float_mv)}</b></div>
        <div class="qg qg-wide"><span>52周</span><b class="num" style="font-size:13px">${fmtPrice(q.low_52w)}~${fmtPrice(q.high_52w)}</b></div>
      </div>
      ${d.board ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px">
        ${d.board.industry ? `<span class="chip" style="color:var(--brand1);border-color:rgba(34,211,238,.4)">${escapeHtml(d.board.industry)}</span>` : ''}
        ${(d.board.concepts || []).slice(0, 6).map((c) => `<span class="chip">${escapeHtml(c)}</span>`).join('')}
      </div>` : ''}
      ${flowRows.length ? `<div class="flow-rows" style="margin-top:12px">
        ${flowRows.map(([label, v]) => {
          const n = Number(v) || 0;
          const w = Math.min(100, (Math.abs(n) / maxFlow) * 100) / 2;
          const color = n >= 0 ? UP : DOWN;
          const left = n >= 0 ? 50 : 50 - w;
          return `<div class="flow-row">
            <span class="f-label">${label}</span>
            <div class="f-track"><div class="f-bar" style="left:${left}%;width:${w}%;background:${color}"></div></div>
            <span class="f-val num ${pctClass(n)}">${fmtMoney(n, 1)}</span>
          </div>`;
        }).join('')}
      </div>` : `<div style="font-size:15px;color:var(--text-3);margin-top:10px">本次未取得资金流向字段，相关维度不参与回答</div>`}
      ${d.auction ? (() => {
        const a = d.auction;
        const openPx = a.open_auction ? a.open_auction.close : a.price;   // 开盘集合竞价成交价
        const openChg = (openPx != null && a.pre_close) ? (openPx - a.pre_close) / a.pre_close * 100 : null;
        return `<div class="auction-box" style="margin-top:12px;border-top:1px solid var(--hairline);padding-top:10px">
          <div style="font-size:14px;color:var(--text-2);margin-bottom:6px">集合竞价 <span class="num" style="color:var(--text-3)">${escapeHtml(a.date || '')}</span></div>
          <div class="quote-grid">
            <div class="qg"><span>开盘竞价</span><b class="num ${pctClass(openChg)}">${fmtPrice(openPx)}${openChg == null ? '' : ' ' + fmtPct(openChg, { digits: 1 })}</b></div>
            <div class="qg"><span>竞价量比</span><b class="num">${a.volume_ratio == null ? '--' : Number(a.volume_ratio).toFixed(2)}</b></div>
            <div class="qg"><span>竞价额</span><b class="num">${fmtMoney(a.amount)}</b></div>
            <div class="qg"><span>收盘竞价</span><b class="num">${a.close_auction ? fmtPrice(a.close_auction.close) : '--'}</b></div>
          </div>
        </div>`;
      })() : ''}
    </div>
    <div class="card" style="padding:13px 14px">
      <h3 class="card-title" style="font-size:15px;margin-bottom:6px">分时走势</h3>
      <div class="mini-chart tall" id="side-minute"></div>
    </div>
    <div class="card" style="padding:13px 14px">
      <h3 class="card-title" style="font-size:15px;margin-bottom:6px">日 K（近 120 日）</h3>
      <div class="mini-chart" id="side-daily"></div>
    </div>`;

  disposeCharts(charts);
  const mEl = side.querySelector('#side-minute');
  const dEl = side.querySelector('#side-daily');
  if (Array.isArray(d.minute) && d.minute.length) {
    const c = mkChart(mEl);
    if (c) { c.setOption(minuteOption(d.minute, q.prev_close)); charts.push(c); }
  } else mEl.innerHTML = emptyState(meta.progressive ? '分时数据补充中' : '暂无分时数据', '', '', 'sentiment');
  if (Array.isArray(d.daily) && d.daily.length) {
    const c = mkChart(dEl);
    if (c) { c.setOption(dailyOption(d.daily)); charts.push(c); }
  } else dEl.innerHTML = emptyState(meta.progressive ? '日K数据补充中' : '暂无日K数据', '', '', 'sentiment');

  // 双源交叉验证溯源徽标：异步拉取后追加到实时价旁，不阻塞盘口面板渲染；
  // 失败/换股/卸载时静默放弃（用 curCode 比对防陈旧响应触碰已重渲染的 DOM）。
  if (!meta.progressive) api.get('/xverify?code=' + encodeURIComponent(code)).then((xv) => {
    if (curCode !== code || !xv || xv.ok === false) return;
    const pxEl = root?.querySelector(`#q-px-${CSS.escape(code)}`);
    if (pxEl && !pxEl.nextElementSibling?.classList?.contains('src-badge')) {
      pxEl.insertAdjacentHTML('afterend', sourceBadge(xv));
    }
  }).catch(() => { /* 溯源失败不影响盘口 */ });
}

/* ---------------- 发送 ---------------- */

// override：重试路径传入定格的 { mode, code, industry }，绕过当前 UI 读取与校验，
// 用导致错误的原始上下文重发；普通发送 override 为空，按当前 UI 状态走原有校验逻辑。
async function send(text, override) {
  if (streaming) return;
  const msg = (text || '').trim();
  // 多模态：本轮图片来自 pendingImages（重试则取定格的 override.images）；允许「只发图不打字」
  const imgs = override ? (override.images || []) : pendingImages.slice();
  if (!msg && !imgs.length) return;
  if (!override) { pendingImages = []; renderThumbs(); }
  let industry = '';
  if (override) {
    industry = override.industry || '';
  } else {
    if (mode === 'stock' && !/^\d{6}$/.test(curCode)) {
      toast('盘中问股模式需先查询一只股票', 'error');
      root.querySelector('#ctx-code')?.focus();
      return;
    }
    if (mode === 'chain') {
      industry = root.querySelector('#ctx-industry')?.value.trim() || '';
      if (!industry) { toast('产业链模式需先填写行业名称', 'error'); root.querySelector('#ctx-industry')?.focus(); return; }
    }
  }

  if (modelReady === false && imgs.length && !msg) {
    pushError('图片理解需要可用的模型连接；请补充文字问题，或先到系统设置连接模型。确定性文字分析仍可直接使用。');
    return;
  }

  // 消息按 mode+code 打标，history 只注入同上下文的问答：换模式/换标的后
  // 不能把上一只股票的盘口结论带进新回答（旧消息无标记视为不匹配自然淘汰）。
  // 流式期间 curCode 可能被再次查询改写，发送时定格，供 finishAi 异步收尾使用
  const msgMode = override ? override.mode : mode;
  const msgCode = override ? override.code : (mode === 'stock' ? curCode : '');
  // 持仓诊断勾选子集：仅 position 模式携带；非 position 一律空(=全部)。重试时取定格值。
  const codes = override ? (override.codes || []) : (msgMode === 'position' ? positionCodes.slice() : []);
  const history = messages
    .filter((m) => (m.role === 'user' || m.role === 'assistant') && m.mode === msgMode && m.code === msgCode)
    .slice(-10)
    .map((m) => ({ role: m.role, content: m.content }));

  messages.push({ role: 'user', content: msg, images: imgs, ts: nowTs(), mode: msgMode, code: msgCode });
  persist();
  renderStream();
  const input = root.querySelector('#chat-input');
  input.value = '';
  autoGrow(input);

  // 占位 AI 消息：收到首个 delta 前显示阶段文案 + 已等待秒数（每秒自增）
  const stream = root.querySelector('#chat-stream');
  const aiWrap = document.createElement('div');
  aiWrap.className = 'msg ai';
  aiWrap.innerHTML = `<div class="msg-avatar logo-mark">${icon('logo')}</div><div style="min-width:0"><div class="msg-bubble md-body"><span class="stage-wait"><span class="stage-text">${escapeHtml(STAGE_FALLBACK)}</span> <span class="stage-secs num">0s</span><span class="typing-cursor"></span></span></div><div class="msg-meta">研究助手 · 分析中…</div></div>`;
  stream.appendChild(aiWrap);
  scrollBottom(true);
  const bubble = aiWrap.querySelector('.msg-bubble');
  const meta = aiWrap.querySelector('.msg-meta');

  // 阶段等待计时器：每秒刷新等待秒数；收到首个 delta / done / error 时清除
  let waitSecs = 0;
  let stageText = '';        // 最近一次 stage 事件文案，空则用兜底
  let firstDelta = false;    // 已收到首个 delta 后停止阶段提示，切回正文渲染
  let stageTimer = null;
  const renderStageWait = () => {
    if (firstDelta) return;
    const w = bubble.querySelector('.stage-wait');
    if (!w) return;
    w.querySelector('.stage-text').textContent = stageText || STAGE_FALLBACK;
    w.querySelector('.stage-secs').textContent = waitSecs + 's';
  };
  const clearStageTimer = () => { if (stageTimer) { clearInterval(stageTimer); stageTimer = null; } };
  stageTimer = setInterval(() => { waitSecs += 1; renderStageWait(); }, 1000);

  streaming = true;
  aborter = new AbortController();
  setSendBtn(true);
  let acc = '';
  let rafPending = false;
  let lastPaint = 0;
  // 流式阶段只做轻量纯文本渲染（escapeHtml + 换行），避免每帧全量 marked 解析导致 O(n²) 卡顿；
  // 配合时间节流（最短 STREAM_PAINT_MS 间隔重绘一次），完整 markdown 解析延到 finishAi 收尾时一次性完成。
  const paint = () => {
    // rAF 一旦排队无法取消：finishAi 置 rafPending=false 后，残留的 rAF 必须空转，
    // 否则会把最终 markdown 覆盖回纯文本并留下永久闪烁光标
    if (!rafPending) return;
    rafPending = false;
    lastPaint = performance.now();
    // 产业链有标题/表格，纯文本流式会让用户看到满屏 # / | / ---。
    // 该模式已有 6000 字硬上限，按当前节流重绘 Markdown 不再存在无限 O(n²) 风险。
    bubble.innerHTML = msgMode === 'chain'
      ? renderMd(acc) + '<span class="typing-cursor"></span>'
      : escapeHtml(acc).replace(/\n/g, '<br>') + '<span class="typing-cursor"></span>';
    scrollBottom(false);
  };
  const schedulePaint = () => {
    if (rafPending) return;
    rafPending = true;
    const wait = Math.max(0, STREAM_PAINT_MS - (performance.now() - lastPaint));
    if (wait <= 0) requestAnimationFrame(paint);
    else setTimeout(() => { if (rafPending) requestAnimationFrame(paint); }, wait);
  };

  // 重试上下文：定格本轮的用户消息与 mode/code/industry，错误气泡「重试」按钮原样重发
  const retryCtx = { text: msg, mode: msgMode, code: msgCode, industry, images: imgs, codes };

  const requestBody = msgMode === 'chain'
    ? buildChainResearchRequest({ industry, focus: msg, history, images: imgs })
    : { mode: msgMode, message: msg, code: msgCode, industry, history, images: imgs, codes };

  try {
    await ssePost('/chat', requestBody, {
      signal: aborter.signal,
      onStage(s) {
        // 收到首个 delta 后阶段提示已停止，迟到的 stage 事件忽略（向后兼容：未收到 stage 时用兜底文案）
        if (firstDelta) return;
        stageText = String(s || '');
        renderStageWait();
      },
      onDelta(t) {
        if (!firstDelta) { firstDelta = true; clearStageTimer(); acc = ''; }
        acc += t;
        schedulePaint();
      },
      onError(err) {
        clearStageTimer();
        if (acc) { quarantinePartial(acc); }
        else aiWrap.remove();
        pushError(err, retryCtx);
      },
      onDone() { clearStageTimer(); finishAi(acc); },
    });
  } catch (e) {
    clearStageTimer();
    if (e.name === 'AbortError') {
      firstDelta = true; // 中断也视为已离开阶段等待
      if (acc) {
        quarantinePartial(acc);
        pushError('已停止生成；部分内容仅供当前屏幕检查，不保存、不导出、不进入后续对话。', retryCtx);
      } else {
        // 零内容中断：多为网关连接断开 / 上游模型未就绪或限流（而非用户主动停止）。
        // 给可操作提示 + 重试，而不是留一个空的「（已中断）」气泡让人以为软件坏了。
        aiWrap.remove();
        pushError('生成中断：未收到任何内容。当前模型连接可能断开，或上游模型未就绪/限流。可点重试；若频繁出现，请到设置检查当前模型连接（只有当前连接使用 Hermes 时，才需检查 Hermes 集成）。', retryCtx);
      }
    } else {
      aiWrap.remove();
      pushError('请求失败：' + e.message, retryCtx);
    }
  } finally {
    clearStageTimer();
    streaming = false;
    aborter = null;
    setSendBtn(false);
  }

  function finishAi(content) {
    rafPending = false; // 取消尚未触发的流式纯文本重绘，避免覆盖最终 markdown
    if (!content) {
      // 流正常结束但模型零输出：多为上游模型鉴权失效/限流（网关返回 200 但 content 为空）。
      // 静默移除气泡会让用户以为"没反应"，故给出可操作提示 + 重试，而非凭空消失。
      aiWrap.remove();
      pushError('模型未返回内容（可能上游鉴权失效或限流）。可重试；若持续，请到设置检查当前模型连接；仅当该连接使用 Hermes 时再检查 Hermes 集成。', retryCtx);
      return;
    }
    // 避免重复 push（onError 后 onDone 不会触发，ssePost 内部保证一次性）
    if (!messages.length || messages[messages.length - 1].content !== content || messages[messages.length - 1].role !== 'assistant') {
      messages.push({ role: 'assistant', content, ts: nowTs(), mode: msgMode, code: msgCode });
      persist();
      saveActiveExchange(msg, imgs.length, content, msgMode, msgCode); // 按本轮定格的模式保存
    }
    // 收尾整条流重渲染：完整 markdown 解析（renderMd 已白名单净化），并让本条回答带上「导出 PDF」按钮
    renderStream();
  }

  function quarantinePartial(content) {
    rafPending = false;
    firstDelta = true;
    // 不 push messages、不 persist、不 saveActiveExchange，因此不会进历史、导出 PDF
    // 或下一轮 history。当前气泡只作临时取证，重新渲染/离开页面即消失。
    bubble.innerHTML = `
      <div style="padding:10px 12px;margin-bottom:10px;border:1px solid var(--up-line);background:var(--up-bg);color:var(--up);border-radius:8px;font-weight:650">
        未完整输出 · 已隔离，不可作为交易或后续对话依据
      </div>${renderMd(content)}`;
    meta.textContent = '研究助手 · 部分输出（未保存）';
    scrollBottom(false);
  }
}

function setSendBtn(stop) {
  // 流式中导航离开时 unmount 已置 root=null，send() 的 finally 仍会走到这里
  const btn = root?.querySelector('#chat-send');
  if (!btn) return;
  btn.classList.toggle('stop', stop);
  btn.innerHTML = stop ? `${icon('stop')}<span>停止</span>` : `${icon('send')}<span>发送</span>`;
  btn.title = stop ? '停止生成' : '发送（Enter）';
  btn.setAttribute('aria-label', stop ? '停止生成' : '发送消息');
}

function autoGrow(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(140, ta.scrollHeight) + 'px';
}

/* ---------------- 对话会话（多会话 / 历史 / 侧栏） ---------------- */
const LS_ACTIVE = 'hermes.chat.activeConv';
let activeConvId = '';
let convList = [];
const _modeCn = { free: '自由', stock: '问股', position: '持仓', chain: '产业链' };

async function loadConvList() {
  try {
    const d = await api.get('/chat/conversations');
    convList = (d && d.conversations) || [];
  } catch { convList = []; }
  renderConvList();
}

function renderConvList() {
  const el = root?.querySelector('#conv-list');
  if (!el) return;
  if (!convList.length) {
    el.innerHTML = '<div style="color:var(--text-3);padding:10px;font-size:12px;line-height:1.6">还没有历史对话。<br>点上方「新建对话」，或直接提问即自动建会话。</div>';
    return;
  }
  el.innerHTML = convList.map((c) => `
    <div class="conv-item" data-id="${escapeHtml(c.id)}" role="button" tabindex="0" aria-label="打开对话：${escapeHtml(c.title || '新对话')}" ${c.id === activeConvId ? 'aria-current="true"' : ''}
      style="display:flex;align-items:center;gap:6px;padding:8px;border-radius:6px;cursor:pointer;margin-bottom:2px;${c.id === activeConvId ? 'background:var(--amber-dim,rgba(255,176,0,.12))' : ''}">
      <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(c.title || '新对话')}">${escapeHtml(c.title || '新对话')}</span>
      <span style="flex:0 0 auto;color:var(--text-3);font-size:11px">${_modeCn[c.mode] || ''}</span>
      <button type="button" class="conv-del" data-id="${escapeHtml(c.id)}" title="删除对话" aria-label="删除对话：${escapeHtml(c.title || '新对话')}" style="flex:0 0 auto;border:none;background:none;color:var(--text-3);cursor:pointer;padding:0 2px;font-size:15px">×</button>
    </div>`).join('');
}

async function newConversation() {
  if (streaming) return;
  const requestedMode = mode;
  const epoch = ++conversationEpoch;
  try {
    const d = await api.post('/chat/conversations', { mode: requestedMode });
    if (!d || !d.conversation || epoch !== conversationEpoch || mode !== requestedMode) return;
    convList.unshift(d.conversation);
    activeConvId = d.conversation.id;
    lsSet(LS_ACTIVE, activeConvId);
    messages = [];
    persist(); renderConvList(); renderStream();
  } catch { toast('新建对话失败', 'error'); }
}

async function switchConversation(cid) {
  if (streaming || !cid || cid === activeConvId) return;
  const epoch = ++conversationEpoch;
  try {
    const d = await api.get('/chat/conversations/' + encodeURIComponent(cid));
    if (!d || d.ok === false || epoch !== conversationEpoch) return;
    activeConvId = cid;
    lsSet(LS_ACTIVE, cid);
    const cmode = (d.meta && d.meta.mode) || 'free';
    mode = MODES.some((item) => item.id === cmode) ? cmode : 'free';
    // 后端历史只存文本：还原为本地 messages（图片用 hadImages 标记，不回放 base64）
    messages = (d.messages || []).map((m) => ({
      role: m.role, content: m.content, ts: String(m.ts || '').slice(11),
      mode: cmode, code: '', hadImages: m.has_image ? 1 : 0,
    }));
    // 旧会话元数据没有单独存 code；问股会话从最近文本中恢复六位代码。
    if (mode === 'stock') {
      const joined = messages.map((m) => String(m.content || '')).join(' ');
      const found = joined.match(/(?<!\d)(\d{6})(?!\d)/);
      curCode = found ? found[1] : '';
    }
    renderModeUI();
    persist(); renderConvList(); renderStream();
    if (mode === 'stock' && curCode) queryStock(curCode);
  } catch { toast('加载对话失败', 'error'); }
}

async function deleteConversation(cid) {
  if (!cid) return;
  try { await fetch('/api/chat/conversations/' + encodeURIComponent(cid), { method: 'DELETE' }); } catch { /* noop */ }
  convList = convList.filter((c) => c.id !== cid);
  if (cid === activeConvId) {
    activeConvId = ''; lsSet(LS_ACTIVE, ''); messages = []; persist(); renderStream();
  }
  renderConvList();
}

// 每轮问答完成后把 user+assistant 文本存入当前会话（无会话则先建）。图片只标记 has_image 不入库。
async function saveActiveExchange(userContent, hadImg, aiContent, exchangeMode, exchangeCode = '') {
  const frozenMode = MODES.some((item) => item.id === exchangeMode) ? exchangeMode : 'free';
  // 流式回答可以在用户离开本页后继续收尾。此时全局 mode /
  // activeConvId 可能已经属于另一个入口，不能把「持仓诊断」
  // 写进「自由问答」会话。只复用与本轮模式一致的会话。
  let targetConvId = activeConvId;
  const activeMeta = convList.find((c) => c.id === targetConvId);
  if (!activeMeta || activeMeta.mode !== frozenMode) targetConvId = '';
  if (!targetConvId) {
    try {
      const d = await api.post('/chat/conversations', { mode: frozenMode });
      if (d && d.conversation) {
        targetConvId = d.conversation.id;
        convList.unshift(d.conversation);
        // 只有用户仍在同一模式时，才把它设为当前活动会话。
        if (root && mode === frozenMode) {
          activeConvId = targetConvId;
          lsSet(LS_ACTIVE, activeConvId);
        }
      }
    } catch { return; }
  }
  if (!targetConvId) return;
  const payload = { messages: [
    { role: 'user', content: userContent || '', has_image: !!hadImg, mode: frozenMode, code: exchangeCode },
    { role: 'assistant', content: aiContent || '', mode: frozenMode, code: exchangeCode },
  ] };
  try {
    await fetch('/api/chat/conversations/' + encodeURIComponent(targetConvId) + '/messages',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (root) await loadConvList();   // 刷新自动标题与排序
  } catch { /* 保存失败不影响对话本身 */ }
}

/* ---------------- 挂载 ---------------- */

export function mount(container) {
  root = container;
  mode = 'free';          // 四个入口每次从明确、干净的自由问答起点开始
  positionCodes = [];   // 新进页面先清空持仓诊断子集，避免上次勾选残留到本次直接访问
  curCode = '';
  curStockName = '';
  historyCollapsed = true;
  sideCollapsed = false;
  // 历史会话由左侧列表显式打开；首屏不再自动灌入旧版
  // localStorage 的混合时间线。这样不删历史，也不会让一段旧
  // 持仓回答在「自由问答」首屏里冒出来。
  messages = [];
  persist();

  container.innerHTML = `
    <style>
      #chat-phase5 { display:flex; gap:14px; height:calc(100dvh - var(--topbar-h) - var(--statusbar-h,0px) - 108px); min-height:640px; position:relative; }
      #chat-phase5 .chat-convs {
        flex:0 0 220px; width:220px; display:flex; flex-direction:column; min-height:0;
        background:var(--panel,#14161c); border:1px solid var(--border); border-radius:12px;
        overflow:hidden; transition:flex-basis .18s ease,width .18s ease; box-shadow:0 12px 32px rgba(0,0,0,.08);
      }
      #chat-phase5 .conv-head { display:flex; align-items:center; gap:8px; padding:10px; border-bottom:1px solid var(--border); }
      #chat-phase5 .conv-head strong { flex:1; min-width:0; font-size:13px; letter-spacing:.04em; }
      #chat-phase5 .conv-toggle-icon, #chat-phase5 .conv-toggle-icon svg { width:18px; height:18px; display:block; }
      #chat-phase5 .conv-toggle-icon .ph { font-size:18px; }
      #chat-phase5 .conv-body { display:flex; flex:1; min-height:0; flex-direction:column; }
      #chat-phase5 .conv-new-wrap { padding:9px; border-bottom:1px solid var(--border); }
      #chat-phase5 #conv-new { width:100%; justify-content:center; }
      #chat-phase5 #conv-list { flex:1; overflow-y:auto; padding:6px; font-size:13px; }
      #chat-phase5.history-collapsed .chat-convs { flex-basis:54px; width:54px; }
      #chat-phase5.history-collapsed .conv-head { justify-content:center; padding:10px 7px; }
      #chat-phase5.history-collapsed .conv-head strong,
      #chat-phase5.history-collapsed .conv-new-label,
      #chat-phase5.history-collapsed #conv-list { display:none; }
      #chat-phase5.history-collapsed .conv-body { flex:0 0 auto; }
      #chat-phase5.history-collapsed .conv-new-wrap { padding:8px 7px; border-bottom:0; }
      #chat-phase5.history-collapsed #conv-new { width:38px; min-width:38px; height:38px; padding:0; }
      #chat-phase5 .chat-layout {
        flex:1; min-width:0; height:100%; display:grid; grid-template-columns:minmax(0,840px);
        justify-content:center; gap:16px;
      }
      #chat-phase5 .chat-layout.has-stock-side { grid-template-columns:minmax(560px,840px) minmax(300px,360px); }
      #chat-phase5 .chat-main { width:100%; max-width:840px; height:100%; border-radius:12px; }
      #chat-phase5 .chat-toolbar { padding:10px 14px; background:color-mix(in srgb,var(--panel) 96%,transparent); }
      #chat-phase5 #chat-side-toggle svg { width:15px; height:15px; }
      #chat-phase5 #chat-side-toggle .ph { font-size:15px; }
      #chat-phase5 .chat-context { padding:10px 14px; }
      #chat-phase5 #chat-model-state[hidden] { display:none !important; }
      #chat-phase5 .chat-stream { padding:22px clamp(14px,3vw,28px) 16px; }
      #chat-phase5 .msg-bubble { max-width:min(740px,92%); }
      #chat-phase5 .chat-compose-dock {
        position:sticky; bottom:0; z-index:6; flex-shrink:0;
        background:linear-gradient(180deg,color-mix(in srgb,var(--panel) 76%,transparent),var(--panel) 24%);
        border-top:1px solid var(--border); box-shadow:0 -12px 28px rgba(0,0,0,.08);
      }
      #chat-phase5 .chat-presets {
        display:flex; flex-wrap:nowrap; gap:7px; overflow-x:auto; overscroll-behavior-x:contain;
        padding:9px 14px 3px; scrollbar-width:thin;
      }
      #chat-phase5 .chat-presets .preset {
        flex:0 0 auto; appearance:none; font:inherit; color:var(--text-2); white-space:nowrap;
      }
      #chat-phase5 .chat-thumbs { padding:7px 14px 0 !important; }
      #chat-phase5 .chat-inputbar { padding:9px 14px 14px; gap:10px; align-items:flex-end; }
      #chat-phase5 .chat-inputbar .textarea {
        min-height:52px !important; padding:13px 14px; border-radius:12px; resize:none;
        background:var(--panel-2); border-color:var(--border-strong);
      }
      #chat-phase5 #chat-upload { width:52px; height:52px; border-radius:12px; }
      #chat-phase5 .send-btn {
        width:auto; min-width:88px; height:52px; padding:0 17px; border-radius:12px;
        display:flex; align-items:center; justify-content:center; gap:8px; font-weight:800;
        letter-spacing:.04em; box-shadow:0 8px 22px rgba(255,176,0,.25);
      }
      #chat-phase5 .send-btn svg { width:19px; height:19px; }
      #chat-phase5 .send-btn .ph { font-size:19px; }
      #chat-phase5 .chat-side { width:100%; max-width:360px; position:relative; }
      #chat-phase5 .chat-side .quote-price { flex-wrap:wrap; row-gap:4px; }
      #chat-phase5 .chat-side .quote-price .q-chg { white-space:nowrap; }
      #chat-phase5 .chat-side .quote-grid {
        grid-template-columns:repeat(2,minmax(0,1fr)); gap:5px 12px;
      }
      #chat-phase5 .chat-side .quote-grid .qg { min-width:0; }
      #chat-phase5 .chat-side .quote-grid .qg-wide { grid-column:1 / -1; }
      #chat-phase5 .chat-side .quote-grid .qg b {
        min-width:0; white-space:nowrap; font-size:13px;
      }
      #chat-phase5 .side-inline-close {
        display:flex; align-items:center; gap:4px; margin:0 0 8px auto; border:0; background:transparent;
        color:var(--text-3); cursor:pointer; font:inherit; font-size:12px; padding:4px 6px; border-radius:6px;
      }
      #chat-phase5 .side-inline-close:hover { color:var(--text); background:var(--hover); }
      #chat-phase5 .side-inline-close svg { width:14px; height:14px; }
      #chat-phase5 .side-inline-close .ph { font-size:14px; }
      #chat-phase5 .chat-empty {
        min-height:100%; display:flex; flex-direction:column; justify-content:center; align-items:center;
        text-align:center; padding:28px 12px; color:var(--text-2);
      }
      #chat-phase5 .chat-empty-mark {
        width:54px; height:54px; display:grid; place-items:center; color:var(--amber);
        border:1px solid var(--amber-line); border-radius:16px; background:var(--amber-dim); margin-bottom:14px;
      }
      #chat-phase5 .chat-empty-mark svg { width:25px; height:25px; }
      #chat-phase5 .chat-empty-mark .ph { font-size:25px; }
      #chat-phase5 .chat-empty h2 { color:var(--text); font-size:20px; margin:0 0 7px; }
      #chat-phase5 .chat-empty p { max-width:520px; margin:0 0 20px; line-height:1.65; }
      #chat-phase5 .chat-empty-actions { width:min(100%,640px); display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
      #chat-phase5 .empty-launch {
        min-width:0; min-height:86px; display:flex; align-items:flex-start; gap:10px; text-align:left;
        padding:14px; color:var(--text); background:var(--panel-2); border:1px solid var(--border);
        border-radius:12px; cursor:pointer; transition:border-color .15s ease,transform .15s ease,background .15s ease;
      }
      #chat-phase5 .empty-launch:hover { border-color:var(--border-strong); background:var(--hover); transform:translateY(-1px); }
      #chat-phase5 .empty-launch.primary { border-color:var(--amber-line); background:var(--amber-dim); }
      #chat-phase5 .empty-launch > svg { width:20px; height:20px; flex:0 0 auto; color:var(--amber); margin-top:1px; }
      #chat-phase5 .empty-launch > .ph { width:20px; height:20px; flex:0 0 auto; font-size:20px; color:var(--amber); margin-top:1px; }
      #chat-phase5 .empty-launch span { min-width:0; display:flex; flex-direction:column; gap:4px; }
      #chat-phase5 .empty-launch b { font-size:14px; }
      #chat-phase5 .empty-launch small { color:var(--text-3); font-size:12px; line-height:1.45; }
      #chat-phase5 .chat-empty-tip { margin-top:18px; color:var(--text-3); font-size:12px; }
      #chat-phase5 .chat-empty-tip kbd { padding:2px 6px; border:1px solid var(--border); border-radius:5px; background:var(--panel-2); }
      #chat-phase5 .conv-item:focus-visible, #chat-phase5 .empty-launch:focus-visible,
      #chat-phase5 .thumb-x:focus-visible, #chat-phase5 .msg-pdf:focus-visible { outline:2px solid var(--amber); outline-offset:2px; }
      /* 1280px 桌面减去主导航后仍放得下 560px 对话区 + 360px 行情区。
         旧的 1400px 断点会过早把行情区改成浮层，直接盖住输入与发送按钮。 */
      @media (max-width:1180px) {
        #chat-phase5 .chat-layout.has-stock-side { grid-template-columns:minmax(0,840px); position:relative; }
        #chat-phase5 .chat-layout.has-stock-side .chat-side {
          display:flex; position:absolute; z-index:12; right:0; top:0; bottom:0; width:min(360px,calc(100% - 20px));
          padding:10px; overflow-y:auto; background:var(--panel,#14161c); border:1px solid var(--border); border-radius:12px;
          box-shadow:-18px 0 40px rgba(0,0,0,.28);
        }
      }
      @media (max-width:768px) {
        #chat-phase5 { gap:0; height:calc(100dvh - var(--topbar-h) - 24px); min-height:0; }
        #chat-phase5 .chat-convs {
          position:absolute; z-index:20; left:8px; top:8px; bottom:8px;
          width:min(300px,calc(100% - 16px)); flex-basis:min(300px,calc(100% - 16px));
        }
        #chat-phase5.history-collapsed .chat-convs {
          left:8px; top:8px; bottom:auto; width:44px; height:44px; flex-basis:44px;
          border-radius:10px; box-shadow:none;
        }
        #chat-phase5.history-collapsed .conv-head { width:44px; height:44px; padding:8px; border-bottom:0; }
        #chat-phase5.history-collapsed .conv-toggle-icon,
        #chat-phase5.history-collapsed .conv-toggle-icon svg { width:20px; height:20px; }
        #chat-phase5 .chat-layout { margin-left:0; width:100%; }
        #chat-phase5:not(.history-collapsed) .chat-convs { box-shadow:18px 0 38px rgba(0,0,0,.32); }
        #chat-phase5 .chat-main { border-radius:12px; }
        #chat-phase5 .chat-empty { justify-content:flex-start; min-height:0; padding:28px 12px 18px; }
        #chat-phase5 .chat-empty-actions { grid-template-columns:1fr; gap:8px; }
        #chat-phase5 .empty-launch { min-height:64px; }
        #chat-phase5 .chat-toolbar {
          display:grid; grid-template-columns:minmax(0,1fr) 44px; align-items:center;
          gap:8px; padding:8px 8px 8px 60px;
        }
        #chat-phase5 .chat-toolbar>div:last-child { margin-left:0!important; gap:0!important; }
        #chat-phase5 #chat-clear { width:44px; min-width:44px; padding:0; font-size:0; }
        #chat-phase5 #chat-clear svg { margin:0; }
        #chat-phase5 #chat-modes { width:100%; min-width:0; overflow-x:auto; flex-wrap:nowrap; scrollbar-width:thin; }
        #chat-phase5 #chat-modes .seg { flex:0 0 auto; }
        #chat-phase5 .chat-context { display:grid; grid-template-columns:minmax(0,1fr) auto; padding:10px; }
        #chat-phase5 .chat-context .input { width:100%!important; min-width:0; }
        #chat-phase5 .chat-context .ctx-hint { grid-column:1/-1; }
        #chat-phase5 .chat-presets { padding:7px 8px 2px; }
        #chat-phase5 .chat-inputbar { padding:8px; gap:7px; }
        #chat-phase5 #chat-upload { width:44px; height:44px; border-radius:10px; }
        #chat-phase5 .chat-inputbar .textarea { min-height:44px!important; padding:10px 11px; }
        #chat-phase5 .send-btn { min-width:52px; width:52px; padding:0; }
        #chat-phase5 .send-btn span { display:none; }
      }
    </style>
    <div class="chat-shell history-collapsed" id="chat-phase5">
      <aside class="chat-convs" id="chat-convs" aria-label="历史对话">
        <div class="conv-head">
          <strong>历史对话</strong>
          <button type="button" class="icon-btn" id="conv-toggle" title="展开历史对话" aria-label="展开历史对话" aria-expanded="false">
            <span class="conv-toggle-icon" aria-hidden="true">${icon('chevR')}</span>
          </button>
        </div>
        <div class="conv-body">
          <div class="conv-new-wrap">
            <button type="button" class="btn btn-sm" id="conv-new" aria-label="新建对话">${icon('plus')}<span class="conv-new-label">新建对话</span></button>
          </div>
          <div id="conv-list" aria-label="对话列表"></div>
        </div>
      </aside>
    <div class="chat-layout no-side">
      <section class="card chat-main">
        <div class="chat-toolbar">
          <div class="segmented" id="chat-modes" role="group" aria-label="分析模式">
            ${MODES.map((m) => `<button type="button" class="seg" data-mode="${m.id}" aria-label="切换到${m.label}" aria-pressed="false">${m.label}</button>`).join('')}
          </div>
          <div style="margin-left:auto;display:flex;gap:8px">
            <button type="button" class="btn btn-sm btn-ghost" id="chat-side-toggle" title="收起个股行情" aria-label="收起个股行情" aria-expanded="true" hidden></button>
            <button type="button" class="btn btn-sm btn-ghost" id="chat-clear" title="清空会话历史" aria-label="清空当前会话">${icon('trash')}清空会话</button>
          </div>
        </div>
        <div class="chat-context" id="chat-context"></div>
        <div class="model-gate" id="chat-model-state" role="status" aria-live="polite" hidden></div>
        <div class="chat-stream" id="chat-stream" role="log" aria-live="polite" aria-label="问股对话记录"></div>
        <div class="chat-compose-dock">
          <div class="chat-presets" id="chat-presets" role="group" aria-label="问题模板"></div>
          <div class="chat-thumbs" id="chat-thumbs" aria-label="待发送图片" style="display:none;gap:8px;flex-wrap:wrap"></div>
          <div class="chat-inputbar">
            <button type="button" class="icon-btn" id="chat-upload" title="上传图片（也可拖拽 / 粘贴截图 Ctrl+V）" aria-label="上传图片" style="flex:0 0 auto;display:flex;align-items:center;justify-content:center">${icon('image')}</button>
            <input type="file" id="chat-file" accept="image/*" multiple style="display:none" aria-label="选择图片">
            <textarea class="textarea" id="chat-input" rows="2" placeholder="向研究助手提问…（Shift+Enter 换行）" aria-label="输入要问研究助手的问题"></textarea>
            <button type="button" class="send-btn" id="chat-send" title="发送（Enter）" aria-label="发送消息">${icon('send')}<span>发送</span></button>
          </div>
        </div>
      </section>
      <aside class="chat-side" id="chat-side" aria-label="个股实时行情" hidden></aside>
    </div>
    </div>`;

  // compact 客户（config ui.chat_compact，如 wudi）：隐藏右侧个股数据面板。appState.status 由 app.js
  // 异步拉取，chat 可能先挂载（竞态）→ 这里兜底自取一次 /status 再判，确保面板可靠隐藏。
  (async () => {
    if (!appState.status) { try { appState.status = await api.get('/status'); } catch { /* 用默认 */ } }
    syncSideUI();
    const readiness = await aiReadiness();
    if (!root) return;
    const gate = root.querySelector('#chat-model-state');
    modelReady = Boolean(readiness.ready);
    if (!readiness.ready && gate) {
      gate.hidden = false;
      gate.innerHTML = `<b>确定性分析已开启，AI 增强未连接</b><span>问股、持仓、市场和产业链证据查询都可使用；${escapeHtml(readiness.reason)}图片理解与开放式推理暂不可用。</span><button type="button" class="btn btn-sm" id="chat-model-settings">连接 AI 增强</button>`;
      root.querySelector('#chat-model-settings')?.addEventListener('click', () => { location.hash = '#/settings'; });
    } else if (gate) {
      gate.hidden = true;
      gate.dataset.note = '当前模型助手已连接，可在确定性证据之上补充开放式解释';
    }
  })();

  container.querySelector('#conv-toggle').addEventListener('click', () => {
    historyCollapsed = !historyCollapsed;
    syncHistoryUI();
  });

  container.querySelector('#chat-side-toggle').addEventListener('click', () => {
    sideCollapsed = !sideCollapsed;
    syncSideUI();
  });

  // 模式切换
  container.querySelector('#chat-modes').addEventListener('click', (e) => {
    const seg = e.target.closest('.seg');
    if (!seg || streaming) return;
    const nextMode = seg.dataset.mode;
    if (!MODES.some((item) => item.id === nextMode) || nextMode === mode) return;
    conversationEpoch += 1;
    mode = nextMode;
    activeConvId = '';
    lsSet(LS_ACTIVE, '');
    messages = [];
    persist();
    renderModeUI();
    renderConvList();
    renderStream();
  });

  // 预设 chips（事件委托）
  container.querySelector('#chat-presets').addEventListener('click', (e) => {
    const chip = e.target.closest('.preset');
    if (chip) send(chip.dataset.q);
  });

  // 错误气泡「重试」（事件委托）：移除旧错误气泡，用定格的 mode/code 重发同一条消息
  container.querySelector('#chat-stream').addEventListener('click', (e) => {
    const launch = e.target.closest('[data-empty-action]');
    if (launch) {
      const action = launch.dataset.emptyAction;
      if (action === 'code') {
        mode = 'stock';
        renderModeUI();
        root?.querySelector('#ctx-code')?.focus();
      } else if (action === 'ask') {
        if (mode === 'stock' && !curCode) { mode = 'free'; renderModeUI(); }
        root?.querySelector('#chat-input')?.focus();
      } else if (action === 'upload') {
        root?.querySelector('#chat-file')?.click();
        root?.querySelector('#chat-input')?.focus();
      }
      return;
    }
    // 导出该条 AI 回答为 PDF（截取该气泡内容）
    const pdfBtn = e.target.closest('.msg-pdf');
    if (pdfBtn) {
      const bubble = pdfBtn.closest('.msg.ai')?.querySelector('.msg-bubble');
      if (bubble) {
        const name = (curStockName ? curStockName + '_' : '') + '问股_' + nowTs().replace(/:/g, '');
        downloadPdf(bubble, name);
      }
      return;
    }
    const btn = e.target.closest('.error-retry');
    if (!btn || streaming) return;
    const eid = btn.dataset.eid;
    const m = messages.find((x) => x.role === 'system' && x.eid === eid);
    if (!m || !m.retry) return;
    const r = m.retry;
    messages = messages.filter((x) => x !== m); // 移除旧错误气泡
    renderStream();
    send(r.text, r); // 复用发送逻辑，用定格上下文重发
  });

  // 清空
  container.querySelector('#chat-clear').addEventListener('click', async () => {
    if (!messages.length) return;
    if (await confirmModal('清空会话', '将删除本地保存的全部对话记录，确定继续？', { danger: true, okText: '清空' })) {
      messages = [];
      persist();
      renderStream();
      toast('会话已清空', 'success');
    }
  });

  // 输入
  const input = container.querySelector('#chat-input');
  input.addEventListener('input', () => autoGrow(input));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send(input.value);
    }
  });
  container.querySelector('#chat-send').addEventListener('click', () => {
    if (streaming) { aborter?.abort(); return; }
    send(input.value);
  });

  // ── 图片输入：上传按钮 / 文件选择 / 缩略图移除 / 粘贴截图(Ctrl+V) / 拖拽 ──
  container.querySelector('#chat-upload').addEventListener('click', () => container.querySelector('#chat-file').click());
  container.querySelector('#chat-file').addEventListener('change', (e) => { addImageFiles(e.target.files); e.target.value = ''; });
  container.querySelector('#chat-thumbs').addEventListener('click', (e) => {
    const x = e.target.closest('.thumb-x'); if (!x) return;
    pendingImages.splice(Number(x.dataset.i), 1); renderThumbs();
  });
  input.addEventListener('paste', (e) => {
    const files = [...(e.clipboardData?.items || [])]
      .filter((it) => it.type && it.type.startsWith('image/')).map((it) => it.getAsFile()).filter(Boolean);
    if (files.length) { e.preventDefault(); addImageFiles(files); }
  });
  const bar = container.querySelector('.chat-inputbar');
  bar.addEventListener('dragover', (e) => { e.preventDefault(); bar.style.outline = '2px dashed var(--brand1)'; });
  bar.addEventListener('dragleave', () => { bar.style.outline = ''; });
  bar.addEventListener('drop', (e) => {
    e.preventDefault(); bar.style.outline = '';
    if (e.dataTransfer?.files?.length) addImageFiles(e.dataTransfer.files);
  });

  // ── 会话侧栏：新建 / 切换 / 删除 ──
  container.querySelector('#conv-new').addEventListener('click', newConversation);
  container.querySelector('#conv-list').addEventListener('click', (e) => {
    const del = e.target.closest('.conv-del');
    if (del) { e.stopPropagation(); deleteConversation(del.dataset.id); return; }
    const item = e.target.closest('.conv-item');
    if (item) switchConversation(item.dataset.id);
  });
  container.querySelector('#conv-list').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const item = e.target.closest('.conv-item');
    if (!item || e.target.closest('.conv-del')) return;
    e.preventDefault();
    switchConversation(item.dataset.id);
  });
  container.querySelector('#chat-side').addEventListener('click', (e) => {
    if (!e.target.closest('.side-inline-close')) return;
    sideCollapsed = true;
    syncSideUI();
  });

  renderModeUI();
  syncHistoryUI();
  renderStream();

  // 会话：只加载列表。用户显式点历史后才恢复内容，
  // 避免旧版误标 mode 的会话自动污染当前入口。
  activeConvId = '';
  // 跨页跳转携带的意图
  const pendingCode = sessionStorage.getItem('hermes.chat.code');
  // 客户#5：从选股/诊断点代码进来 = 针对新股开一段【干净对话】，别落在上一段旧对话里、也不用手动点"新建对话"。
  // 故有 pendingCode 时【不恢复】上次会话，清空当前消息给个空白 chat（下一句提问会自动建会话）。
  loadConvList();

  if (pendingCode) {
    sessionStorage.removeItem('hermes.chat.code');
    mode = 'stock';
    curCode = pendingCode;
    activeConvId = '';
    lsSet(LS_ACTIVE, '');
    messages = [];
    persist();
    renderModeUI();
    renderStream();
    queryStock(pendingCode);
  }
  const pendingPrompt = sessionStorage.getItem('hermes.chat.prompt');
  if (pendingPrompt) {
    sessionStorage.removeItem('hermes.chat.prompt');
    setTimeout(() => send(pendingPrompt), 80);
  }
  const pendingAction = sessionStorage.getItem('hermes.chat.action');
  if (pendingAction === 'position') {
    sessionStorage.removeItem('hermes.chat.action');
    // 持仓页勾选带入的子集：只诊断这几只；为空=全部（向后兼容）
    let picked = [];
    try { picked = JSON.parse(sessionStorage.getItem('hermes.chat.position.codes') || '[]'); } catch { picked = []; }
    sessionStorage.removeItem('hermes.chat.position.codes');
    positionCodes = Array.isArray(picked) ? picked.filter((c) => /^\d{6}$/.test(String(c))) : [];
    mode = 'position';
    renderModeUI();
    const msg = positionCodes.length
      ? `请只诊断我持仓中选中的这 ${positionCodes.length} 只（${positionCodes.join('、')}）：逐只分析当前位置与盘口状态，给出持有/加仓/减仓/止损的明确建议（带价格条件与对应股数），并评估这几只的整体风险。`
      : '请基于我保存的持仓做一次全面诊断：逐只分析当前位置与盘口状态，给出持有/加仓/减仓/止损的明确建议，并评估整体仓位结构与风险敞口。';
    setTimeout(() => send(msg), 80);
  }
  if (sessionStorage.getItem('hermes.chat.focus')) {
    sessionStorage.removeItem('hermes.chat.focus');
    input.focus();
  }
}

export function unmount() {
  // 【修复"切走侧边栏→AI 自动停止、答案丢失"】不再 abort 流式：导航离开时让 AI 继续生成完，
  // 答案照常写进 messages（持久化）/会话；各渲染函数对 root=null 已 no-op 容错，回到问股页即可见。
  // 不置 streaming=false：交给流式 finally 收尾（否则后台流未完就放行新发送→并发写乱）。
  // 手动停止仍可点输入框那颗停止按钮。
  disposeCharts(charts);
  pendingImages = [];   // 清未发送的草稿图片，避免下次进来把"看不见的旧图"一起发出
  modelReady = null;
  root = null;
}

/** 供全局快捷键调用 */
export function focusInput() {
  root?.querySelector('#chat-input')?.focus();
}
