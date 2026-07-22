/* views/company.js — 单公司深度研究（机构研报式九段·SSE 全屏渲染 + 历史 + 存入知识库）
 *
 * 铁律边界（前端侧）：报告正文纯渲染 AI 返回；顶部盘后数据底座只渲染后端结构化字段，
 * 不在前端推算一致预期、股东比例或 GP 编码。
 * 输入仅做 6 位代码校验，名称可选（仅作历史标签展示，不参与数字）。
 */

import { api, aiReadiness, ssePost, lsGet, lsSet } from '../api.js';
import { icon, escapeHtml, renderMd, toast, emptyState, confirmModal } from '../ui.js';

const LS_KEY = 'hermes.company.history';
const MAX_HISTORY = 20;
// 流式重绘最短间隔（ms）：长研报逐 chunk 全量重渲染会 O(n²) 卡顿，节流到约每秒数次。
const STREAM_PAINT_MS = 120;
const CODE_RE = /^\d{6}$/;

let root = null;
let history = [];
let activeIdx = -1;       // 当前查看的历史索引
let running = false;
let aborter = null;
let curCode = '';
let curName = '';
let curContent = '';
let researchError = '';
let researchBase = null;
let researchBaseLoading = false;
let researchBaseSeq = 0;
let officialDisclosures = null;
let officialDisclosuresLoading = false;
let officialDisclosuresSeq = 0;
let managementInteractions = null;
let managementInteractionsLoading = false;
let managementInteractionsSeq = 0;

function fmtTime(ts) {
  const d = new Date(ts);
  if (isNaN(d)) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// 标题：有名称则「名称(代码)」，否则只显示代码
function label(code, name) {
  return name ? `${name}（${code}）` : code;
}

// 点代码 → 个股诊断（前端约定的跨视图跳转）
function gotoDiagnosis(code) {
  if (!CODE_RE.test(code)) return;
  sessionStorage.setItem('hermes.diagnosis.code', code);
  location.hash = '#/diagnosis';
}

function renderHistory() {
  const el = root.querySelector('#company-history');
  if (!history.length) {
    el.innerHTML = `<div style="font-size:14px;color:var(--text-3);padding:8px 4px">暂无研究记录</div>`;
    return;
  }
  el.innerHTML = history.map((h, i) => `
    <div class="history-item ${i === activeIdx ? 'active' : ''}" data-i="${i}">
      ${icon('search')}<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(label(h.code, h.name))}</span>
      <span class="hi-time num">${fmtTime(h.ts)}</span>
    </div>`).join('');
}

function fmtResearchNumber(value, digits = 2) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : '—';
}

function renderResearchBase() {
  if (researchBaseLoading) {
    return '<section class="company-basis loading"><div class="company-basis-title">盘后研究数据底座</div><div class="company-basis-note">正在读取机构一致预期与股东报告期…</div></section>';
  }
  if (!researchBase) return '';
  const consensus = researchBase.consensus || {};
  const holders = researchBase.holders_top10 || {};
  const gpjy = researchBase.gpjy || {};
  const eps = Array.isArray(consensus.eps_cny) ? consensus.eps_cny : [];
  const epsText = eps.length
    ? eps.map((item) => `${item.year || '—'}E ${fmtResearchNumber(item.value)}`).join(' / ')
    : '无非零预测';
  const reportDate = String(holders.report_date || '').replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3') || '—';
  const largest = holders.largest_holder || {};
  const missing = Array.isArray(researchBase.missing) && researchBase.missing.length
    ? `<div class="company-basis-note">数据缺口：${escapeHtml(researchBase.missing.join('；'))}</div>` : '';
  return `<section class="company-basis">
    <div class="company-basis-head"><div class="company-basis-title">盘后研究数据底座</div><span>日内评分权重 0</span></div>
    <div class="company-basis-grid">
      <div><b>机构一致预期</b><strong>目标价 ${fmtResearchNumber(consensus.target_price_cny)} 元</strong><small>EPS：${escapeHtml(epsText)}</small></div>
      <div><b>十大股东 · ${escapeHtml(reportDate)}</b><strong>前十合计 ${fmtResearchNumber(holders.top10_total_ratio_pct)}%</strong><small>${escapeHtml(largest.name || '第一大股东待更新')}${largest.ratio_pct != null ? ` ${fmtResearchNumber(largest.ratio_pct)}%` : ''}</small></div>
      <div class="blocked"><b>GPJY 交易面</b><strong>${gpjy.status === 'blocked' ? '已封锁' : '待验证'}</strong><small>${escapeHtml(gpjy.reason || '字段语义与单位待核验')}</small></div>
    </div>
    <div class="company-basis-note">一致预期为辅助口径、非公司承诺；股东数据按报告期展示，不参与日内主线或买卖评分。</div>
    ${missing}
  </section>`;
}

function safeOfficialUrl(raw) {
  try {
    const parsed = new URL(String(raw || ''));
    const officialHosts = new Set([
      'static.cninfo.com.cn',
      'static.sse.com.cn',
      'disc.static.szse.cn',
    ]);
    return parsed.protocol === 'https:' && officialHosts.has(parsed.hostname.toLowerCase())
      && (!parsed.port || parsed.port === '443') && !parsed.username && !parsed.password
      ? parsed.href : '';
  } catch {
    return '';
  }
}

function disclosureWindow(payload) {
  const start = String(payload?.requested_window?.start || '').trim();
  const end = String(payload?.requested_window?.end || '').trim();
  const isoDate = /^\d{4}-\d{2}-\d{2}$/;
  if (isoDate.test(start) && isoDate.test(end)) return `${start} 至 ${end}`;
  if (isoDate.test(start)) return `${start} 起`;
  if (isoDate.test(end)) return `截至 ${end}`;
  return '';
}

function disclosureConfirmation(item, exchange, verification, usedLastGood = false) {
  const sources = Array.isArray(item?.source_confirmations) ? item.source_confirmations : [];
  const hasCninfo = sources.includes('cninfo');
  const exchangeName = exchange === 'sse' ? '上交所' : '深交所';
  const hasExchange = sources.includes(exchange === 'sse' ? 'sse' : 'szse');
  const doubleListed = ['cross_confirmed', 'partially_cross_confirmed'].includes(verification);
  if (hasCninfo && hasExchange && doubleListed) return `巨潮 + ${exchangeName}均有同题记录`;
  if (hasCninfo && hasExchange && usedLastGood) return `上次成功快照 · 巨潮 + ${exchangeName}均有记录`;
  if (hasCninfo) return '仅巨潮官网记录';
  if (hasExchange) return `仅${exchangeName}官网记录`;
  return '来源确认待补充';
}

function renderOfficialDisclosures() {
  if (!curCode) return '';
  if (officialDisclosuresLoading) {
    return `<section class="company-disclosures loading" id="company-disclosures">
      <div class="company-disclosure-head"><div><b>官方公告证据</b><small>正在核对巨潮与公司所属交易所…</small></div><span class="state checking">核对中</span></div>
    </section>`;
  }

  const payload = officialDisclosures || {};
  const items = Array.isArray(payload.items) ? payload.items : [];
  const state = String(payload.state || 'missing');
  const verification = String(payload.verification_state || 'unavailable');
  const evidenceReason = String(
    payload.evidence?.degraded_reason || payload.degraded_reasons?.[0] || ''
  ).trim();
  const exchangeName = payload.exchange === 'sse' ? '上交所' : '深交所';
  const requestedWindow = disclosureWindow(payload);
  const windowLabel = requestedWindow ? `查询窗口 ${requestedWindow}` : '查询窗口以上游返回为准';
  const stateMeta = state === 'complete'
    ? { cls: 'complete', label: verification === 'empty_confirmed' ? '双目录空结果已核对' : '官方双目录已核对' }
    : state === 'degraded'
      ? { cls: 'degraded', label: payload.used_last_good
        ? '上次成功快照'
        : verification === 'partially_cross_confirmed' ? '部分公告双目录有记录' : '部分官方链路可用' }
      : { cls: 'missing', label: '当前无法核验' };
  const asOf = payload.data_as_of ? `数据截至 ${escapeHtml(payload.data_as_of)}` : '数据时点暂不可用';

  let body = '';
  if (state === 'missing') {
    body = `<div class="company-disclosure-empty missing"><b>当前无法读取官方公告</b><span>${escapeHtml(evidenceReason || '巨潮与交易所公告链路当前不可用，不能据此判断查询窗口内“无公告”。')}</span></div>`;
  } else if (!items.length && verification === 'empty_confirmed' && state === 'complete') {
    body = `<div class="company-disclosure-empty verified"><b>查询窗口内未发现公告</b><span>${escapeHtml(windowLabel)}；巨潮与${exchangeName}均已返回空列表。</span></div>`;
  } else if (!items.length) {
    body = `<div class="company-disclosure-empty degraded"><b>当前没有可展示的公告</b><span>${escapeHtml(evidenceReason || '官方链路未完成双源核对，现有结果不足以证明查询窗口内确实无公告。')}</span></div>`;
  } else {
    body = `<div class="company-disclosure-list">${items.slice(0, 6).map((item) => {
      const url = safeOfficialUrl(item.original_url);
      const title = escapeHtml(item.title || '未命名公告');
      const date = escapeHtml(item.announcement_date || item.published_at || '日期待核验');
      const confirmation = escapeHtml(disclosureConfirmation(
        item, payload.exchange, verification, Boolean(payload.used_last_good)
      ));
      return `<div class="company-disclosure-item">
        <time>${date}</time><div><b>${title}</b><small>${confirmation}</small></div>
        ${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">原文</a>` : '<span class="link-missing">原文链接不可用</span>'}
      </div>`;
    }).join('')}</div>`;
  }

  const degradedNote = state === 'degraded' && evidenceReason
    ? `<div class="company-disclosure-note">降级说明：${escapeHtml(evidenceReason)}</div>` : '';
  const evidenceNote = items.length
    ? '<div class="company-disclosure-note">“双目录均有记录”仅表示巨潮与交易所出现同题公告，不代表公告内容得到独立信源互证。</div>' : '';
  return `<section class="company-disclosures" id="company-disclosures">
    <div class="company-disclosure-head"><div><b>官方公告证据</b><small>${escapeHtml(windowLabel)} · ${asOf} · 不参与日内买卖评分</small></div><span class="state ${stateMeta.cls}">${stateMeta.label}</span></div>
    ${body}${degradedNote}${evidenceNote}
  </section>`;
}

function repaintOfficialDisclosures() {
  const panel = root?.querySelector('#company-disclosures');
  if (panel) panel.outerHTML = renderOfficialDisclosures();
}

function interactionTime(value, missingLabel) {
  const raw = String(value || '').trim();
  return raw ? raw.replace('T', ' ').replace(/\+08:00$/, '') : missingLabel;
}

function renderManagementInteractions() {
  if (!curCode) return '';
  if (managementInteractionsLoading) {
    return `<section class="company-interactions loading" id="company-interactions">
      <div class="company-interaction-head"><div><b>管理层回复 · 互动问答</b><small>正在读取深交所互动易…</small></div><span class="state checking">读取中</span></div>
    </section>`;
  }

  const payload = managementInteractions || {};
  const state = String(payload.state || 'missing');
  const items = Array.isArray(payload.items) ? payload.items : [];
  const reason = String(
    payload.evidence?.degraded_reason || payload.degraded_reasons?.[0] || ''
  ).trim();
  const stateMeta = ({
    complete: { cls: 'complete', label: '互动易已返回' },
    complete_empty: { cls: 'complete', label: '已查询 · 无记录' },
    degraded: { cls: 'degraded', label: '部分字段待核验' },
    last_good: { cls: 'degraded', label: '上次成功快照' },
    missing: { cls: 'missing', label: '当前不可用' },
    not_supported: { cls: 'neutral', label: '当前未覆盖' },
  })[state] || { cls: 'missing', label: '状态待核验' };
  const dataAsOf = payload.data_as_of ? `数据截至 ${escapeHtml(payload.data_as_of)}` : '数据时点暂不可用';
  const formalRole = payload.is_formal_disclosure === false ? '非正式公告' : '正式性待核验';

  let body = '';
  if (state === 'not_supported') {
    body = '<div class="company-interaction-empty neutral"><b>当前仅覆盖深市互动易</b><span>沪市/北交所公司暂不适用此数据源；这是覆盖范围说明，不代表数据读取失败。</span></div>';
  } else if (state === 'complete_empty') {
    body = '<div class="company-interaction-empty verified"><b>查询窗口内暂无公开互动问答</b><span>互动易返回了明确空结果；不等同于公司没有其他形式的投资者沟通。</span></div>';
  } else if (state === 'missing') {
    body = `<div class="company-interaction-empty missing"><b>当前无法读取互动问答</b><span>${escapeHtml(reason || '官方互动问答链路当前不可用；这不是“无记录”结论。')}</span></div>`;
  } else if (state === 'last_good' && !items.length) {
    body = '<div class="company-interaction-empty degraded"><b>上次成功快照未含问答</b><span>当前实时链路不可用；旧快照的空结果不能证明现在仍无互动问答。</span></div>';
  } else if (!items.length) {
    body = `<div class="company-interaction-empty degraded"><b>当前没有可确认的互动问答</b><span>${escapeHtml(reason || '返回状态或字段未通过完整校验，不能据此判断查询窗口内没有互动问答。')}</span></div>`;
  } else {
    body = `<div class="company-interaction-list">${items.slice(0, 6).map((item) => {
      const question = String(item.question || '').trim();
      const answer = String(item.answer || '').trim();
      const hasAnswer = item.answered === true && Boolean(answer);
      const questionTime = interactionTime(item.question_pub_date, '提问时间待核验');
      const answerTime = interactionTime(item.answer_pub_date, '回复时间待核验');
      return `<article class="company-interaction-item">
        <div class="company-interaction-times"><span>提问 ${escapeHtml(questionTime)}</span>${hasAnswer ? `<span>回复 ${escapeHtml(answerTime)}</span>` : '<span>尚未回复</span>'}</div>
        <div class="company-interaction-copy question"><b>投资者提问</b><p>${escapeHtml(question || '提问内容待核验')}</p></div>
        ${hasAnswer
          ? `<div class="company-interaction-copy answer"><b>管理层回复</b><p>${escapeHtml(answer)}</p></div>`
          : '<div class="company-interaction-pending">该问题尚未收到公开回复，不标记为“管理层回复”。</div>'}
      </article>`;
    }).join('')}</div>`;
  }

  const stateNote = ['degraded', 'last_good'].includes(state) && reason
    ? `<div class="company-interaction-note">${state === 'last_good' ? '快照说明' : '降级说明'}：${escapeHtml(reason)}</div>` : '';
  return `<section class="company-interactions" id="company-interactions">
    <div class="company-interaction-head"><div><b>管理层回复 · 互动问答</b><small>深交所互动易 · Tier 3 · ${escapeHtml(formalRole)} · ${dataAsOf} · 不参与日内评分</small></div><span class="state ${stateMeta.cls}">${stateMeta.label}</span></div>
    ${body}${stateNote}
  </section>`;
}

function repaintManagementInteractions() {
  const panel = root?.querySelector('#company-interactions');
  if (panel) panel.outerHTML = renderManagementInteractions();
}

async function loadResearchBase(code, { repaint = true } = {}) {
  const seq = ++researchBaseSeq;
  researchBase = null;
  researchBaseLoading = true;
  if (repaint && root) renderResult({ streaming: running });
  const payload = await api.get(`/stock/${code}/research-enrichment`).catch(() => null);
  if (seq !== researchBaseSeq || code !== curCode) return;
  researchBase = payload;
  researchBaseLoading = false;
  if (root) renderResult({ streaming: running });
}

async function loadOfficialDisclosures(code, { repaint = true } = {}) {
  const seq = ++officialDisclosuresSeq;
  officialDisclosures = null;
  officialDisclosuresLoading = true;
  if (repaint) repaintOfficialDisclosures();
  const payload = await api.get(
    `/disclosures/announcements?code=${encodeURIComponent(code)}&limit=6`
  ).catch(() => ({
    ok: false,
    state: 'missing',
    verification_state: 'unavailable',
    items: [],
    degraded_reasons: ['官方公告接口当前不可达，不能据此判断“无公告”。'],
  }));
  if (seq !== officialDisclosuresSeq || code !== curCode) return;
  officialDisclosures = payload;
  officialDisclosuresLoading = false;
  repaintOfficialDisclosures();
}

async function loadManagementInteractions(code, { repaint = true } = {}) {
  const seq = ++managementInteractionsSeq;
  managementInteractions = null;
  managementInteractionsLoading = true;
  if (repaint) repaintManagementInteractions();
  const payload = await api.get(
    `/interactions/management?code=${encodeURIComponent(code)}&limit=6`
  ).catch(() => ({
    ok: false,
    state: 'missing',
    content_role: '交易所互动问答',
    answer_role: '管理层回复',
    is_formal_disclosure: false,
    items: [],
    degraded_reasons: ['官方互动问答接口当前不可达；不能据此判断“无记录”。'],
  }));
  if (seq !== managementInteractionsSeq || code !== curCode) return;
  managementInteractions = payload;
  managementInteractionsLoading = false;
  repaintManagementInteractions();
}

function renderResult({ streaming = false } = {}) {
  const el = root.querySelector('#company-result');
  if (!curContent && !streaming && !researchBase && !researchBaseLoading
    && !officialDisclosures && !officialDisclosuresLoading
    && !managementInteractions && !managementInteractionsLoading) {
    el.innerHTML = emptyState(
      '输入 6 位股票代码开始深度研究',
      '例如：600519、000858、300750——当前模型助手将按机构研报式九段（结论速览 / 核心事件 / 产业链位置 / 财务 / 估值 / 评分卡 / 多空推演 / 交易节奏 / 风险）展开。缺口会区分为「未接入」「暂不可用」或「官方未披露」，并说明因此无法核验的结论，绝不编造。',
      '', 'search');
    return;
  }
  // 报告标题里的代码可点 → 诊断（红涨绿跌主题由全局 CSS 变量控制，此处不写死颜色）
  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap">
      <h3 style="font-size:16px">单公司深度研究：
        <a href="javascript:void(0)" id="company-code-link" class="num" style="color:var(--accent);text-decoration:none"
           title="点击查看个股诊断">${escapeHtml(label(curCode, curName))}</a>
      </h3>
      ${streaming
        ? `<span class="gen-indicator"><span class="gdot"></span>研究中…</span><span style="margin-left:auto"><button class="btn btn-sm btn-danger" id="company-stop">${icon('stop')}停止</button></span>`
        : `${researchError ? '<span class="company-run-state failed" role="status">研究未完成</span>' : ''}<span style="margin-left:auto;display:flex;gap:8px">
            <button class="btn btn-sm" id="company-rerun">${icon('refresh')}重新生成</button>
            ${curContent ? `<button class="btn btn-sm btn-primary" id="company-save">${icon('save')}存入知识库</button>` : ''}
          </span>`}
    </div>
    ${renderOfficialDisclosures()}
    ${renderManagementInteractions()}
    ${renderResearchBase()}
    ${curContent || streaming
      ? `<article class="md-body report-body" id="company-md">${renderMd(curContent)}${streaming ? '<span class="typing-cursor"></span>' : ''}</article>`
      : researchError
        ? `<div class="company-report-state failed" id="company-md" role="alert"><b>本次没有生成研究正文</b><span>${escapeHtml(researchError)}</span><small>行情、公告等结构化证据仍可查看，但不能把它们冒充为已经完成的九段研究报告。</small></div>`
        : '<div class="company-report-state waiting" id="company-md" role="status"><b>研究正文尚未形成</b><span>请重新生成；在模型返回完整正文前，本次不会标记为完成。</span></div>'}`;
  el.querySelector('#company-code-link')?.addEventListener('click', () => gotoDiagnosis(curCode));
  el.querySelector('#company-stop')?.addEventListener('click', () => aborter?.abort());
  el.querySelector('#company-rerun')?.addEventListener('click', () => research(curCode, curName));
  el.querySelector('#company-save')?.addEventListener('click', saveToKnowledge);
}

async function research(code, name) {
  if (running) return;
  code = (code || '').trim();
  name = (name || '').trim();
  if (!CODE_RE.test(code)) { toast('请输入 6 位股票代码', 'error'); return; }
  const readiness = await aiReadiness();
  if (!readiness.ready) {
    curCode = code;
    curName = name;
    curContent = '';
    researchError = `${readiness.reason}行情、公告和结构化证据仍可正常查看。`;
    activeIdx = -1;
    renderHistory();
    renderResult();
    void loadOfficialDisclosures(code);
    void loadManagementInteractions(code);
    void loadResearchBase(code);
    toast('AI 研究尚未启用', 'error');
    return;
  }
  running = true;
  curCode = code;
  curName = name;
  curContent = '';
  researchError = '';
  activeIdx = -1;
  aborter = new AbortController();
  const disclosureTask = loadOfficialDisclosures(code, { repaint: false });
  const interactionTask = loadManagementInteractions(code, { repaint: false });
  renderHistory();
  renderResult({ streaming: true });
  await loadResearchBase(code, { repaint: false });
  // 公告双源可能比模型首字更慢；独立更新证据区，不阻塞 SSE 研究。
  void disclosureTask;
  // 互动问答独立加载，失败或不支持不阻塞公司报告生成。
  void interactionTask;
  const mdEl = () => root?.querySelector('#company-md');

  let rafPending = false;
  let lastPaint = 0;
  // 流式阶段只做轻量纯文本渲染（escapeHtml + 换行），完整 markdown 解析延到 onDone/finally。
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

  try {
    await ssePost('/chat', {
      // mode='company' 走 /chat SSE 通道（不进日报 store，按代码按需生成）；code 走后端 6 位校验。
      mode: 'company',
      message: '生成单公司深度研究报告',
      code,
      industry: '',
      history: [],
    }, {
      signal: aborter.signal,
      onError(err) {
        researchError = String(err || '模型连接未返回可用结果');
        toast('研究失败：' + researchError, 'error');
      },
      onDelta(t) {
        researchError = '';
        curContent += t;
        schedulePaint();
      },
      onDone() {
        if (curContent.trim()) {
          history.unshift({ code, name, ts: Date.now(), content: curContent.slice(0, 60000) });
          history = history.slice(0, MAX_HISTORY);
          lsSet(LS_KEY, history);
          activeIdx = 0;
          toast('研究完成', 'success');
        } else {
          researchError ||= '模型已结束响应，但没有返回任何研究正文。';
          toast('研究未完成：模型未返回正文', 'error');
        }
      },
    });
  } catch (e) {
    if (e.name === 'AbortError') {
      toast('已停止', 'info');
      if (curContent) {
        history.unshift({ code, name, ts: Date.now(), content: curContent.slice(0, 60000) });
        history = history.slice(0, MAX_HISTORY);
        lsSet(LS_KEY, history);
        activeIdx = 0;
      } else {
        researchError = '研究已停止，尚未形成可保存的正文。';
      }
    } else {
      researchError = e.message || '研究过程发生异常';
      toast('研究异常：' + researchError, 'error');
    }
  } finally {
    running = false;
    aborter = null;
    rafPending = false; // 取消尚未触发的流式纯文本重绘，避免覆盖 renderResult 的最终 markdown
    if (root) { renderHistory(); renderResult(); } // renderResult 内做一次完整 markdown 解析+净化
  }
}

async function saveToKnowledge() {
  if (!curContent) return;
  try {
    const r = await api.post('/knowledge', {
      category: '复盘笔记',
      title: `单公司深度研究：${label(curCode, curName)}`,
      content: curContent,
    });
    if (r && r.ok !== false) toast('已存入知识库（复盘笔记）', 'success');
    else toast('保存失败：' + (r?.error || '未知错误'), 'error');
  } catch (e) {
    toast('保存失败：' + e.message, 'error');
  }
}

export function mount(container) {
  managementInteractionsSeq += 1;
  root = container;
  history = lsGet(LS_KEY, []);
  if (!Array.isArray(history)) history = [];
  activeIdx = -1;
  curCode = '';
  curName = '';
  curContent = '';
  researchError = '';
  researchBase = null;
  researchBaseLoading = false;
  officialDisclosures = null;
  officialDisclosuresLoading = false;
  managementInteractions = null;
  managementInteractionsLoading = false;

  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">公司研究</h2>
      <span class="page-sub">结合公告、互动、行情与财务证据，开展单公司结构化深度研究</span>
    </div>
    <div class="grid12">
      <section class="card sp-12">
        <div class="chain-hero">
          <h2>单公司深度研究</h2>
          <p>给当前模型助手一个 6 位股票代码，它按机构研报式九段展开：结论速览 → 核心事件 → 产业链位置 → 财务 → 估值 → 评分卡 → 多空推演 → 交易节奏（含买卖点/止损） → 风险。缺口会区分为「未接入」「暂不可用」或「官方未披露」，并说明因此无法核验的结论，绝不编造。</p>
          <div class="chain-form">
            <input class="input" id="company-code" inputmode="numeric" maxlength="6" placeholder="股票代码（6 位），如：600519">
            <input class="input" id="company-name" maxlength="12" placeholder="名称（可选）">
            <button class="btn btn-primary" id="company-go">${icon('spark')}开始研究</button>
          </div>
          <div class="model-gate" id="company-model-state" role="status" aria-live="polite" hidden></div>
        </div>
      </section>
      <section class="card hoverable sp-3">
        <div class="card-head"><h3 class="card-title">历史记录</h3>
          <div class="card-extra"><button class="btn btn-sm btn-ghost" id="company-clear" title="清空历史">${icon('trash')}</button></div>
        </div>
        <div class="card-body" id="company-history"></div>
      </section>
      <section class="card sp-9" style="min-height:420px">
        <div class="card-body" id="company-result"></div>
      </section>
    </div>`;

  injectStyle();

  const codeEl = container.querySelector('#company-code');
  const nameEl = container.querySelector('#company-name');
  const go = () => research(codeEl.value, nameEl.value);
  const goBtn = container.querySelector('#company-go');
  goBtn.addEventListener('click', go);
  codeEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  nameEl.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  void aiReadiness().then((readiness) => {
    if (!root || readiness.ready) return;
    const gate = root.querySelector('#company-model-state');
    if (!gate) return;
    gate.hidden = false;
    gate.innerHTML = `<b>AI 研究尚未启用</b><span>${escapeHtml(readiness.reason)}行情、公告与数据底座仍可查看。</span><button type="button" class="btn btn-sm">去系统设置</button>`;
    goBtn.disabled = true;
    goBtn.setAttribute('aria-disabled', 'true');
    gate.querySelector('button')?.addEventListener('click', () => { location.hash = '#/settings'; });
  });

  container.querySelector('#company-history').addEventListener('click', (e) => {
    const item = e.target.closest('.history-item');
    if (!item || running) return;
    activeIdx = Number(item.dataset.i);
    const h = history[activeIdx];
    if (h) {
      curCode = h.code;
      curName = h.name || '';
      curContent = h.content || '';
      researchError = '';
      researchBase = null;
      researchBaseLoading = false;
      officialDisclosures = null;
      officialDisclosuresLoading = false;
      managementInteractions = null;
      managementInteractionsLoading = false;
      codeEl.value = h.code;
      nameEl.value = h.name || '';
      void loadOfficialDisclosures(h.code, { repaint: false });
      void loadManagementInteractions(h.code, { repaint: false });
      renderHistory();
      renderResult();
      loadResearchBase(h.code);
    }
  });
  container.querySelector('#company-clear').addEventListener('click', async () => {
    if (!history.length) return;
    if (await confirmModal('清空历史', '将删除本地保存的全部单公司研究记录，确定？', { danger: true, okText: '清空' })) {
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
  researchBaseSeq += 1;
  officialDisclosuresSeq += 1;
  managementInteractionsSeq += 1;
  researchBaseLoading = false;
  officialDisclosuresLoading = false;
  managementInteractions = null;
  managementInteractionsLoading = false;
  running = false;
  root = null;
}

// 样式：复用 chain 的 hero/form 视觉；手机端（≤760px）输入框堆叠单列不溢出。带 id 幂等注入。
function injectStyle() {
  if (document.getElementById('company-style')) return;
  const s = document.createElement('style');
  s.id = 'company-style';
  s.textContent = `
    #company-code{font-variant-numeric:tabular-nums;letter-spacing:.5px}
    .company-basis{margin:0 0 18px;padding:12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel-2)}
    .company-basis-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:9px}
    .company-basis-head>span{font-size:11px;color:var(--text-3)}
    .company-basis-title{font-size:13px;font-weight:800;color:var(--text)}
    .company-basis-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
    .company-basis-grid>div{display:flex;flex-direction:column;gap:4px;min-width:0;padding:9px;border:1px solid var(--border);border-radius:6px;background:var(--panel)}
    .company-basis-grid b{font-size:11px;color:var(--text-3)}
    .company-basis-grid strong{font-size:14px;color:var(--text)}
    .company-basis-grid small,.company-basis-note{font-size:11px;line-height:1.45;color:var(--text-3)}
    .company-basis-grid .blocked strong{color:var(--down)}
    .company-basis-note{margin-top:8px}
    .company-basis.loading{min-height:72px}
    .company-disclosures{margin:0 0 12px;padding:11px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel)}
    .company-disclosures.loading{min-height:54px;background:var(--panel-2)}
    .company-disclosure-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
    .company-disclosure-head>div{display:flex;flex-direction:column;gap:3px;min-width:0}
    .company-disclosure-head b{font-size:14px;color:var(--text)}
    .company-disclosure-head small{font-size:12px;line-height:1.4;color:var(--text-2)}
    .company-disclosure-head .state{flex:none;padding:3px 7px;border:1px solid currentColor;border-radius:999px;font-size:12px;font-weight:700}
    .company-disclosure-head .state.complete{color:var(--up)}
    .company-disclosure-head .state.degraded,.company-disclosure-head .state.checking{color:var(--warn)}
    .company-disclosure-head .state.missing{color:var(--down)}
    .company-disclosure-list{display:flex;flex-direction:column;margin-top:9px;border-top:1px solid var(--border)}
    .company-disclosure-item{display:grid;grid-template-columns:84px minmax(0,1fr) auto;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)}
    .company-disclosure-item time{font-size:12px;color:var(--text-2);font-variant-numeric:tabular-nums}
    .company-disclosure-item>div{display:flex;flex-direction:column;gap:2px;min-width:0}
    .company-disclosure-item b{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;color:var(--text)}
    .company-disclosure-item small{font-size:12px;color:var(--text-2)}
    .company-disclosure-item a{font-size:12px;font-weight:700;color:var(--accent);text-decoration:none}
    .company-disclosure-item a:hover{text-decoration:underline}
    .company-disclosure-item .link-missing{font-size:12px;color:var(--text-3)}
    .company-disclosure-empty{display:flex;flex-direction:column;gap:3px;margin-top:9px;padding:9px 10px;border-radius:6px;background:var(--panel-2)}
    .company-disclosure-empty b{font-size:13px;color:var(--text)}
    .company-disclosure-empty span,.company-disclosure-note{font-size:12px;line-height:1.5;color:var(--text-2)}
    .company-disclosure-empty.missing{border-left:3px solid var(--down)}
    .company-disclosure-empty.degraded{border-left:3px solid var(--warn)}
    .company-disclosure-empty.verified{border-left:3px solid var(--up)}
    .company-disclosure-note{margin-top:7px}
    .company-interactions{margin:0 0 12px;padding:11px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel)}
    .company-interactions.loading{min-height:54px;background:var(--panel-2)}
    .company-interaction-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
    .company-interaction-head>div{display:flex;flex-direction:column;gap:3px;min-width:0}
    .company-interaction-head b{font-size:14px;color:var(--text)}
    .company-interaction-head small{font-size:12px;line-height:1.4;color:var(--text-2)}
    .company-interaction-head .state{flex:none;padding:3px 7px;border:1px solid currentColor;border-radius:999px;font-size:12px;font-weight:700}
    .company-interaction-head .state.complete{color:var(--up)}
    .company-interaction-head .state.degraded,.company-interaction-head .state.checking{color:var(--warn)}
    .company-interaction-head .state.missing{color:var(--down)}
    .company-interaction-head .state.neutral{color:var(--text-2)}
    .company-interaction-list{display:flex;flex-direction:column;gap:8px;margin-top:9px}
    .company-interaction-item{padding:9px 10px;border:1px solid var(--border);border-radius:6px;background:var(--panel-2)}
    .company-interaction-times{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:6px;font-size:11px;color:var(--text-2);font-variant-numeric:tabular-nums}
    .company-interaction-copy{display:grid;grid-template-columns:72px minmax(0,1fr);gap:8px;align-items:start;margin-top:4px}
    .company-interaction-copy b{font-size:12px;color:var(--text-2)}
    .company-interaction-copy.answer b{color:var(--accent)}
    .company-interaction-copy p{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2;margin:0;font-size:12px;line-height:1.55;color:var(--text)}
    .company-interaction-pending,.company-interaction-note{margin-top:6px;font-size:12px;line-height:1.5;color:var(--text-2)}
    .company-interaction-empty{display:flex;flex-direction:column;gap:3px;margin-top:9px;padding:9px 10px;border-radius:6px;background:var(--panel-2)}
    .company-interaction-empty b{font-size:13px;color:var(--text)}
    .company-interaction-empty span{font-size:12px;line-height:1.5;color:var(--text-2)}
    .company-interaction-empty.verified{border-left:3px solid var(--up)}
    .company-interaction-empty.degraded{border-left:3px solid var(--warn)}
    .company-interaction-empty.missing{border-left:3px solid var(--down)}
    .company-interaction-empty.neutral{border-left:3px solid var(--text-3)}
    .company-run-state.failed{font-size:12px;font-weight:800;color:var(--down);border:1px solid var(--down-line);background:var(--down-dim);border-radius:999px;padding:3px 9px}
    .company-report-state{display:flex;flex-direction:column;gap:6px;padding:14px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel-2)}
    .company-report-state b{font-size:14px;color:var(--text)}
    .company-report-state span,.company-report-state small{font-size:12px;line-height:1.55;color:var(--text-2)}
    .company-report-state.failed{border-left:3px solid var(--down)}
    .company-report-state.waiting{border-left:3px solid var(--amber)}
    @media (max-width:760px){
      .grid12 .card.sp-3, .grid12 .card.sp-9{grid-column:1 / -1 !important}
      #company-code, #company-name{width:100%}
      .company-basis-grid{grid-template-columns:1fr}
      .company-disclosure-head{align-items:flex-start;flex-direction:column}
      .company-disclosure-item{grid-template-columns:1fr auto;gap:4px 10px}
      .company-disclosure-item time{grid-column:1 / -1}
      .company-interaction-head{align-items:flex-start;flex-direction:column}
      .company-interaction-copy{grid-template-columns:1fr;gap:2px}
    }`;
  document.head.appendChild(s);
}
