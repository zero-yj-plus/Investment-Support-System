/* 字段级证据抽屉：只展示后端已持久化的来源、时点、质量和跨源对账。 */
import { api, fmtMoney, fmtNum, fmtPct, fmtPrice } from './api.js';
import { closeModal, escapeHtml, openModal, skeleton } from './ui.js';

const QUALITY = {
  ok: ['可用', 'up'], degraded: ['已降级', 'flat'], conflict: ['来源冲突', 'down'], missing: ['缺失', 'down'],
};
const FRESHNESS = { live: '实时', delayed: '延迟', stale: '过期', missing: '缺失' };
const REASONS = {
  cross_source_conflict: '跨源数值超出容差', fallback_source: '使用备用源',
  event_time_inferred: '供应商未给时钟，事件时间为保守推断', stale: '超过新鲜度窗口',
  derived_value: '数值为派生计算', all_sources_unavailable: '主备源均不可用',
};
let evidenceAborter = null;
let evidenceRequestId = 0;

function cnTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(date).replaceAll('/', '-')} 北京时间`;
}

function valueText(point) {
  if (point?.value == null) return '—';
  if (point.unit === 'percent') return fmtPct(point.value);
  if (point.unit === 'CNY') {
    return ['amount', 'total_mv', 'float_mv'].includes(point.field)
      ? fmtMoney(point.value) : `¥${fmtPrice(point.value)}`;
  }
  if (point.unit === 'share') return `${fmtNum(point.value)} 股`;
  return `${fmtNum(point.value, 2)}${point.unit === 'ratio' ? ' 倍' : ''}`;
}

function pointCard(point, role = '') {
  if (!point) return '';
  const [qualityLabel, qualityClass] = QUALITY[point.quality_status] || [point.quality_status || '未知', 'flat'];
  return `<div class="evd-point">
    <div class="evd-point-head">
      <div><b>${escapeHtml(point.field || '—')}</b>${role ? `<span>${escapeHtml(role)}</span>` : ''}</div>
      <span class="${qualityClass}">${escapeHtml(qualityLabel)}</span>
    </div>
    <div class="evd-value num">${escapeHtml(valueText(point))}</div>
    <dl class="evd-kv">
      <div><dt>来源</dt><dd>${escapeHtml(point.source_id || '—')} · Tier ${escapeHtml(point.source_tier ?? '—')}</dd></div>
      <div><dt>事件时点</dt><dd class="num">${escapeHtml(cnTime(point.event_time))}</dd></div>
      <div><dt>可用时点</dt><dd class="num">${escapeHtml(cnTime(point.available_at))}</dd></div>
      <div><dt>抓取时点</dt><dd class="num">${escapeHtml(cnTime(point.fetched_at))}</dd></div>
      <div><dt>交易日</dt><dd class="num">${escapeHtml(point.trading_day || '—')}</dd></div>
      <div><dt>新鲜度</dt><dd>${escapeHtml(FRESHNESS[point.freshness] || point.freshness || '—')}</dd></div>
      <div><dt>降级层级</dt><dd class="num">${escapeHtml(point.fallback_level ?? '—')}</dd></div>
      <div><dt>证据角色</dt><dd>${point.observation_role === 'cross_check' ? '交叉校验源' : '当时主选值'}</dd></div>
      <div><dt>数值口径</dt><dd>${point.derived ? '派生值' : '原始值'}${point.event_time_inferred ? ' · 时钟推断' : ''}</dd></div>
    </dl>
    ${(point.quality_reasons || []).length ? `<div class="evd-reasons">${point.quality_reasons.map((reason) => `<span>${escapeHtml(REASONS[reason] || reason)}</span>`).join('')}</div>` : ''}
  </div>`;
}

function renderDetail(data) {
  const point = data.point || {};
  const source = data.source || {};
  const parents = data.lineage || [];
  return `<div class="evd-wrap">
    ${pointCard(point)}
    <div class="evd-source">
      <b>来源登记</b>
      <div>${escapeHtml(source.provider || point.source_id || '—')}</div>
      <div class="muted">用途：${escapeHtml(source.purpose || '—')} · 成本：${escapeHtml(source.cost || '—')}</div>
      <div class="muted">授权/条款：${escapeHtml(source.license || '—')}</div>
      <div class="muted">已知风险：${escapeHtml(source.known_risks || '—')}</div>
    </div>
    ${parents.length ? `<div class="evd-cross"><b>跨源对账</b>${parents.map((row) => pointCard(row.point, row.role || '校验源')).join('')}</div>` : '<div class="evd-empty">本次没有可用的第二来源；质量状态已按单源规则标注。</div>'}
    <div class="evd-hash">evidence_id <code>${escapeHtml(point.evidence_id || '—')}</code><br>raw_payload_hash <code>${escapeHtml(point.raw_payload_hash || '—')}</code></div>
  </div>`;
}

function ensureStyle() {
  if (document.getElementById('evidence-drawer-style')) return;
  const tag = document.createElement('style');
  tag.id = 'evidence-drawer-style';
  tag.textContent = `
    .evd-wrap{display:flex;flex-direction:column;gap:12px}
    .evd-point,.evd-source,.evd-empty{border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px;background:var(--panel-2)}
    .evd-point-head{display:flex;justify-content:space-between;gap:10px;font-size:12px}.evd-point-head div span{color:var(--text-3);margin-left:7px}
    .evd-value{font-size:22px;font-weight:800;margin:6px 0 8px}
    .evd-kv{display:grid;grid-template-columns:1fr 1fr;gap:7px 18px;margin:0}.evd-kv div{min-width:0}.evd-kv dt{font-size:11px;color:var(--text-3)}.evd-kv dd{font-size:12px;margin:2px 0 0;overflow-wrap:anywhere}
    .evd-source{font-size:12px;line-height:1.65}.evd-source .muted{color:var(--text-3)}
    .evd-reasons{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}.evd-reasons span{font-size:11px;color:var(--warn);border:1px solid color-mix(in srgb,var(--warn) 35%,transparent);border-radius:999px;padding:2px 7px}
    .evd-cross>b{display:block;font-size:12px;margin-bottom:7px}.evd-cross{display:flex;flex-direction:column;gap:8px}
    .evd-empty{font-size:12px;color:var(--text-3)}.evd-hash{font-size:11px;color:var(--text-3);line-height:1.7;overflow-wrap:anywhere}.evd-hash code{font-size:10px}
    @media(max-width:600px){.evd-kv{grid-template-columns:1fr}.evd-value{font-size:19px}}
  `;
  document.head.appendChild(tag);
}

export async function openEvidence(evidenceId) {
  const id = String(evidenceId || '').trim();
  if (!id) return;
  try { evidenceAborter?.abort(); } catch { /* noop */ }
  evidenceAborter = new AbortController();
  const requestId = ++evidenceRequestId;
  ensureStyle();
  openModal({
    title: '字段证据',
    body: `<div id="evidence-modal-body">${skeleton(5)}</div>`,
    footer: '<button class="btn btn-sm" data-close>关闭</button>',
  });
  try {
    const data = await api.get(`/evidence/${encodeURIComponent(id)}`, { signal: evidenceAborter.signal });
    if (requestId !== evidenceRequestId) return;
    const body = document.querySelector('#evidence-modal-body');
    if (body) body.innerHTML = data?.ok ? renderDetail(data) : `<div class="evd-empty">${escapeHtml(data?.error || '证据读取失败')}</div>`;
  } catch (error) {
    if (error?.name === 'AbortError' || requestId !== evidenceRequestId) return;
    const body = document.querySelector('#evidence-modal-body');
    if (body) body.innerHTML = `<div class="evd-empty">${escapeHtml(error.message || '证据读取失败')}</div>`;
  }
}

export { closeModal };
