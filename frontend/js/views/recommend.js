/* views/recommend.js — 推荐留痕与成功率（母文档 §29）
   ------------------------------------------------------------------
   闭环定位：所有推荐(盘前/尾盘/问股/趋势票/量化/产业链)落库即固定，
   T0/T1/T2/T5 分别用对应目标交易日收盘价回验，append-only 沉淀长期成功率。

   铁律（UI 侧也必须守）：
   - 标的涨跌/方向收益全部来自后端真实计算；目标日收盘价取不到 → 不可比 + 标注，
     界面显示「—」，绝不在前端拼一个收益数字。
   - 历史判断固定、不事后改写、不重新问一次换套说法（append-only）：界面顶部明确声明，
     推荐卡只读、验证结果按 hit/miss/pending 如实展示。
   - 样本未达后端 minimum_stable_sample 时不展示稳定胜率，原始观察比例仅供审计。
   红涨绿跌：收益率正=红(up)、负=绿(down)，与全站一致(pctClass)。 */

import { api, fmtPct, fmtPrice, pctClass } from '../api.js';
import { icon, escapeHtml, toast, emptyState, skeleton, dataStamp, closeModal } from '../ui.js';
import {
  reviewCoverage,
  reviewStateMeta,
  reviewWindowRows,
  latestReview,
  statsPercent,
} from './_recommend_review.js';

let root = null;
let latestPriceEvidence = null;

// 推荐来源（母文档 §29.1）；与后端验证窗口一一对应
const SOURCES = ['盘前', '尾盘', '问股', '趋势票', '量化', '产业链', '风险提示', '持仓'];
const DIRECTIONS = [
  { value: 'watch', label: '观察（默认）' },
  { value: 'buy', label: '买入 / 新建仓' },
  { value: 'add', label: '加仓' },
  { value: 'reduce', label: '减仓' },
  { value: 'exit', label: '清仓 / 退出' },
  { value: 'avoid', label: '回避 / 不买' },
];
const STATUS_LABEL = { active: '跟踪中', closed: '已结束' };

function injectStyle() {
  if (document.getElementById('recommend-style')) return;
  const css = `
  .rec-note{font-size:13px;color:var(--amber);background:color-mix(in srgb,var(--amber) 12%,var(--panel));
    border:1px solid color-mix(in srgb,var(--amber) 45%,transparent);border-radius:var(--radius-sm);
    padding:9px 13px;margin-bottom:var(--gap);line-height:1.55;display:flex;gap:8px;align-items:flex-start}
  .rec-note svg{width:16px;height:16px;flex:none;margin-top:1px}

  /* 统计面板 */
  .rec-stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}
  .rec-kpi{background:var(--panel-2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:11px 14px}
  .rec-kpi .k{font-size:12px;color:var(--text-3);font-family:var(--mono,monospace)}
  .rec-kpi .v{font-size:22px;font-weight:800;color:var(--text);font-family:var(--mono,monospace);margin-top:3px;line-height:1.1}
  .rec-kpi .v.sm{font-size:15px}
  .rec-warn{font-size:12px;color:var(--amber);margin-top:8px;line-height:1.5}
  .rec-breakdown{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}
  .rec-breakdown span{font-size:12px;color:var(--text-2);background:var(--panel-2);border:1px solid var(--border);
    border-radius:10px;padding:3px 8px}
  .rec-per-window{display:grid;grid-template-columns:repeat(4,minmax(95px,1fr));gap:7px;margin-top:9px}
  .rec-pw{border:1px solid var(--border);border-radius:var(--radius-sm);padding:6px 8px;background:var(--panel-2);
    display:flex;flex-direction:column;gap:2px}
  .rec-pw b{font-size:12px;color:var(--text)} .rec-pw span{font-size:11px;color:var(--text-3)}

  /* 推荐列表卡 */
  .rec-list{display:flex;flex-direction:column;gap:11px}
  .rec-card{border:1px solid var(--border);border-left-width:3px;border-radius:var(--radius-sm);
    background:var(--panel-2);padding:12px 14px}
  .rec-card.hit{border-left-color:var(--up)} .rec-card.invalidated{border-left-color:var(--down)}
  .rec-card.waiting,.rec-card.untriggered,.rec-card.incomparable,.rec-card.partial_hit{border-left-color:var(--text-3)}
  .rec-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .rec-name{font-size:16px;font-weight:800;color:var(--text);cursor:pointer}
  .rec-name:hover{color:var(--accent);text-decoration:underline}
  .rec-code{font-family:var(--mono,monospace);font-size:12px;color:var(--text-3);cursor:pointer}
  .rec-tag{font-size:11px;border-radius:10px;padding:1px 9px;font-weight:700;border:1px solid var(--border);color:var(--text-2)}
  .rec-tag.src{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,transparent)}
  .rec-hit{margin-left:auto;font-size:13px;font-weight:800;border-radius:10px;padding:2px 11px}
  .rec-hit.positive{color:var(--up);background:var(--up-bg,color-mix(in srgb,var(--up) 12%,transparent))}
  .rec-hit.negative{color:var(--down);background:var(--down-bg,color-mix(in srgb,var(--down) 12%,transparent))}
  .rec-hit.warning{color:var(--amber);background:color-mix(in srgb,var(--amber) 10%,var(--panel))}
  .rec-hit.waiting,.rec-hit.neutral{color:var(--text-2);background:var(--panel)}
  .rec-meta{display:flex;gap:16px;flex-wrap:wrap;margin-top:7px;font-size:13px;color:var(--text-2)}
  .rec-meta b{font-family:var(--mono,monospace);color:var(--text)}
  .rec-conds{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:6px 14px;margin-top:8px;
    font-size:12px;color:var(--text-3);line-height:1.5}
  .rec-conds .c b{color:var(--text-2);font-weight:600}
  .rec-reason{margin-top:7px;font-size:13px;color:var(--text-2);line-height:1.55}
  .rec-review{margin-top:9px;padding-top:8px;border-top:1px dashed var(--border);font-size:12px;color:var(--text-3);
    display:flex;gap:14px;flex-wrap:wrap;align-items:baseline}
  .rec-review .rr-ret{font-size:15px;font-weight:800;font-family:var(--mono,monospace)}
  .rec-review .rr-err{color:var(--down)}
  .rec-miss{font-size:11px;color:var(--text-3);margin-top:5px;line-height:1.5;font-style:italic}
  .rec-timeline{display:grid;grid-template-columns:repeat(4,minmax(105px,1fr));gap:7px;margin-top:9px}
  .rec-window{border:1px solid var(--border);border-radius:var(--radius-sm);padding:6px 8px;background:var(--panel);
    min-width:0;display:flex;flex-direction:column;gap:2px}
  .rec-window .rw-head{display:flex;justify-content:space-between;gap:6px;font-size:11px;color:var(--text-3)}
  .rec-window .rw-state{font-size:12px;font-weight:750;color:var(--text-2)}
  .rec-window.positive{border-color:color-mix(in srgb,var(--up) 45%,var(--border))}.rec-window.positive .rw-state{color:var(--up)}
  .rec-window.negative{border-color:color-mix(in srgb,var(--down) 45%,var(--border))}.rec-window.negative .rw-state{color:var(--down)}
  .rec-window.warning{border-color:color-mix(in srgb,var(--amber) 45%,var(--border))}.rec-window.warning .rw-state{color:var(--amber)}
  .rec-review-metrics{display:flex;gap:12px;flex-wrap:wrap}
  .rec-review-metrics b{font-family:var(--mono,monospace);color:var(--text)}

  /* 候选提案：只做决策审计，不进入正式推荐胜率 */
  .proposal-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}
  .proposal-card{border:1px solid var(--border);border-left:3px solid var(--amber);border-radius:var(--radius-sm);
    background:var(--panel-2);padding:11px 13px;min-width:0}
  .proposal-card.approved{border-left-color:var(--up)} .proposal-card.vetoed{border-left-color:var(--down)}
  .proposal-card.expired{border-left-color:var(--text-3)}
  .proposal-top{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
  .proposal-name{font-size:15px;font-weight:800;color:var(--text)}
  .proposal-state{margin-left:auto;font-size:11px;font-weight:750;border:1px solid var(--border);border-radius:10px;padding:2px 8px}
  .proposal-card.approved .proposal-state{color:var(--up)} .proposal-card.vetoed .proposal-state{color:var(--down)}
  .proposal-body{font-size:12px;color:var(--text-2);line-height:1.55;margin-top:8px}
  .proposal-body b{color:var(--text);font-weight:650}.proposal-foot{font-size:11px;color:var(--text-3);margin-top:8px;line-height:1.45}
  #proposal-list-body .empty-state,#rec-list-body .empty-state{
    display:grid;grid-template-columns:34px minmax(0,520px);grid-template-areas:"icon title" "icon sub";
    justify-content:center;align-items:center;column-gap:12px;row-gap:2px;padding:10px 16px;text-align:left}
  #proposal-list-body .empty-state svg,#rec-list-body .empty-state svg{grid-area:icon;width:30px;height:30px}
  #proposal-list-body .empty-state .es-title,#rec-list-body .empty-state .es-title{grid-area:title}
  #proposal-list-body .empty-state .es-sub,#rec-list-body .empty-state .es-sub{grid-area:sub;max-width:none}

  /* 录入表单（modal 内复用样式，亦可内嵌） */
  .rec-form{display:grid;grid-template-columns:1fr 1fr;gap:11px 14px}
  .rec-form .f-full{grid-column:1/-1}
  .rec-form label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--text-3)}
  .rec-form input,.rec-form select,.rec-form textarea{
    background:var(--panel-2);border:1px solid var(--border);border-radius:var(--radius-sm);
    color:var(--text);font-size:14px;padding:7px 10px;font-family:inherit;width:100%}
  .rec-form textarea{resize:vertical;min-height:48px}
  .rec-form .req{color:var(--down)}

  @media (max-width:1100px){.rec-stats{grid-template-columns:repeat(3,minmax(0,1fr))}}
  @media (max-width:760px){
    .rec-form{grid-template-columns:1fr}
    .rec-meta{gap:10px}
    .rec-stats{grid-template-columns:repeat(2,1fr)}
    .rec-timeline,.rec-per-window{grid-template-columns:repeat(2,1fr)}
  }`;
  const tag = document.createElement('style');
  tag.id = 'recommend-style';
  tag.textContent = css;
  document.head.appendChild(tag);
}

function gotoDiag(code) {
  if (!code || !/^\d{6}$/.test(String(code))) return;
  sessionStorage.setItem('hermes.diagnosis.code', String(code));
  location.hash = '#/diagnosis';
}

/* ========== 渲染：统计面板 ========== */
function renderStats(s) {
  const body = root?.querySelector('#rec-stats-body');
  if (!body) return;
  if (!s || s.ok === false) {
    body.innerHTML = emptyState('统计暂不可用', '稍后重试');
    return;
  }
  const insufficient = s.enough_sample === false;
  const stableWinRate = statsPercent(s, 'win_rate');
  const observedWinRate = statsPercent(s, 'observed_win_rate');
  const unitMismatch = s.win_rate_unit !== 'percent';
  const minimum = s.minimum_stable_sample ?? '—';
  const missing = (Array.isArray(s.missing) ? s.missing : []).filter((item) => {
    const text = String(item || '');
    return !(insufficient && text.includes('可评分窗口') && text.includes('稳定胜率'));
  });
  // 收益率红绿：正=up(红) 负=down(绿)
  const kpi = (k, v, cls = '', sm = false) =>
    `<div class="rec-kpi"><div class="k">${escapeHtml(k)}</div><div class="v ${sm ? 'sm' : ''} ${cls}">${v}</div></div>`;
  body.innerHTML = `
    <div class="rec-stats">
      ${kpi('可评分窗口', `${s.sample ?? 0}<span style="font-size:12px;color:var(--text-3)"> / 计划 ${s.planned_windows ?? 0}</span>`, '', true)}
      ${kpi('稳定胜率', stableWinRate != null ? fmtPct(stableWinRate, { signed: false, digits: 1 }) : '—', stableWinRate != null && stableWinRate >= 50 ? 'up' : '')}
      ${kpi('原始观察比例', observedWinRate != null ? fmtPct(observedWinRate, { signed: false, digits: 1 }) : '—', '', true)}
      ${kpi('平均方向正收益', s.avg_win != null ? fmtPct(s.avg_win) : '—', pctClass(s.avg_win))}
      ${kpi('平均方向负收益', s.avg_loss != null ? fmtPct(s.avg_loss) : '—', pctClass(s.avg_loss))}
      ${kpi('盈亏比', s.profit_factor != null ? s.profit_factor.toFixed(2) : '—')}
    </div>
    <div class="rec-breakdown">
      <span>待到期 ${fmtNumSafe(s.waiting)}</span><span>未触发 ${fmtNumSafe(s.untriggered)}</span>
      <span>不可比 ${fmtNumSafe(s.incomparable)}</span><span>已排除推荐 ${fmtNumSafe(s.excluded)}</span>
    </div>
    ${renderPerWindowStats(s)}
    ${insufficient ? `<div class="rec-warn">⚠ 可评分窗口 ${s.sample ?? 0} 个（稳定胜率门槛 ${escapeHtml(String(minimum))}），不展示稳定胜率；原始观察比例仅供审计。</div>` : ''}
    ${unitMismatch ? '<div class="rec-warn">⚠ 胜率单位不是 percent，前端拒绝猜测换算，已留空。</div>' : ''}
    ${missing.length ? `<div class="rec-warn">⚠ ${missing.map(escapeHtml).join('；')}</div>` : ''}`;
}

function fmtNumSafe(value) {
  return value == null ? '—' : escapeHtml(String(value));
}

function renderPerWindowStats(stats) {
  const rows = stats.per_window && typeof stats.per_window === 'object' ? stats.per_window : {};
  return `<div class="rec-per-window">${['T0', 'T1', 'T2', 'T5'].map((window) => {
    const row = rows[window] || {};
    const observed = statsPercent({ ...row, win_rate_unit: stats.win_rate_unit }, 'observed_win_rate');
    const stable = statsPercent({ ...row, win_rate_unit: stats.win_rate_unit }, 'win_rate');
    return `<div class="rec-pw"><b>${window} · 样本 ${fmtNumSafe(row.sample)}</b>
      <span>原始观察 ${observed == null ? '—' : fmtPct(observed, { signed: false, digits: 1 })}</span>
      <span>稳定胜率 ${stable == null ? '—' : fmtPct(stable, { signed: false, digits: 1 })}</span></div>`;
  }).join('')}</div>`;
}

/* ========== 渲染：推荐列表 ========== */
function renderList(recs) {
  const body = root?.querySelector('#rec-list-body');
  if (!body) return;
  if (!recs || !recs.length) {
    body.innerHTML = emptyState(
      '暂无推荐留痕',
      '点击右上「新增候选」。候选通过证据、风险闸和触发确认后才会晋级正式推荐，并按目标交易日收盘证据复盘。',
      '', 'archive',
    );
    return;
  }
  body.innerHTML = `<div class="rec-list">${recs.map(renderCard).join('')}</div>`;
  // 点名称/代码 → 诊断
  body.querySelectorAll('[data-diag]').forEach((el) =>
    el.addEventListener('click', () => gotoDiag(el.dataset.diag)));
}

function renderCard(r) {
  const rv = latestReview(r);
  const state = reviewStateMeta(rv);
  const rawReturn = rv?.final_return ?? null;
  const directionalReturn = rv?.directional_return ?? null;
  const coverage = reviewCoverage(rv);
  const risk = (r.risk_decision && typeof r.risk_decision === 'object') ? r.risk_decision : null;
  const riskLabel = { allow: '风险闸通过', reduce: '风险闸已缩仓', veto: '风险闸禁止' }[risk?.decision] || '';
  const cond = (label, v) => v ? `<div class="c"><b>${label}</b> ${escapeHtml(v)}</div>` : '';
  const meta = (label, v) => `<span>${label} <b>${v}</b></span>`;
  return `
    <div class="rec-card ${state.key}">
      <div class="rec-top">
        <span class="rec-name" data-diag="${escapeHtml(r.code || '')}">${escapeHtml(r.name || r.code || '未命名')}</span>
        <span class="rec-code" data-diag="${escapeHtml(r.code || '')}">${escapeHtml(r.code || '')}</span>
        <span class="rec-tag src">${escapeHtml(r.source || '—')}</span>
        ${(r.direction || r.rec_type) ? `<span class="rec-tag">${escapeHtml(r.direction || r.rec_type)}</span>` : ''}
        ${riskLabel ? `<span class="rec-tag">${escapeHtml(riskLabel)}${risk.position_cap_pct != null ? ` · 上限 ${escapeHtml(String(risk.position_cap_pct))}%` : ''}</span>` : ''}
        <span class="rec-tag">${STATUS_LABEL[r.status] || r.status || ''}</span>
        <span class="rec-hit ${state.tone}" title="${escapeHtml(state.detail)}">${escapeHtml(state.label)}</span>
      </div>
      <div class="rec-meta">
        ${meta('推荐价', r.rec_price != null && r.rec_price !== '' ? fmtPrice(r.rec_price) : '—')}
        ${meta('推荐时间', escapeHtml(r.rec_time || '—'))}
        ${r.expected_holding_days ? meta('预期持有', `${escapeHtml(String(r.expected_holding_days))} 日`) : ''}
        ${r.market_state ? meta('市场', escapeHtml(r.market_state)) : ''}
      </div>
      ${r.rec_reason ? `<div class="rec-reason">${escapeHtml(r.rec_reason)}</div>` : ''}
      ${(risk?.reasons || []).length ? `<div class="rec-miss">确定性风控：${risk.reasons.map(escapeHtml).join('；')}</div>` : ''}
      <div class="rec-conds">
        ${cond('买入条件', r.buy_condition)}
        ${cond('卖出/不买', r.sell_condition)}
        ${cond('止损', r.stop_loss_condition)}
        ${cond('止盈', r.take_profit_condition)}
      </div>
      ${renderReviewTimeline(r)}
      ${rv ? `
        <div class="rec-review">
          <span>${escapeHtml(rv.window || '最新窗口')} · 目标交易日 <b>${escapeHtml(rv.target_trade_day || '—')}</b></span>
          <span>回验时间 ${escapeHtml(rv.review_time || '—')}</span>
          <span>现价 <b style="font-family:var(--mono,monospace)">${rv.current_price != null ? fmtPrice(rv.current_price) : '—'}</b></span>
          <span>标的涨跌 <span class="rr-ret ${pctClass(rawReturn)}">${rawReturn != null ? fmtPct(rawReturn) : '—'}</span></span>
          <span>方向结果 <span class="rr-ret ${pctClass(directionalReturn)}">${directionalReturn != null ? fmtPct(directionalReturn) : '—'}</span></span>
          <span>MFE <b class="${pctClass(rv.max_return)}">${rv.max_return != null ? fmtPct(rv.max_return) : '—'}</b></span>
          <span>MAE <b class="${pctClass(rv.max_drawdown)}">${rv.max_drawdown != null ? fmtPct(rv.max_drawdown) : '—'}</b></span>
          <span>路径覆盖 <b>${coverage.observed ?? '—'}/${coverage.expected ?? '—'}</b>${coverage.complete ? ' · 完整' : ' · 不完整'}</span>
          <span class="${state.tone === 'negative' ? 'rr-err' : ''}">${escapeHtml(state.detail)}</span>
        </div>
        ${(rv.missing && rv.missing.length) ? `<div class="rec-miss">注：${rv.missing.map(escapeHtml).join('；')}</div>` : ''}
      ` : '<div class="rec-review"><span style="color:var(--text-3)">尚无窗口回验；点「立即验证」只会追加真实证据，不会改写历史。</span></div>'}
    </div>`;
}

function renderReviewTimeline(rec) {
  return `<div class="rec-timeline" aria-label="T0 T1 T2 T5 逐窗口回验">${reviewWindowRows(rec).map(({ window, review, meta }) => `
    <div class="rec-window ${meta.tone}" title="${escapeHtml(meta.detail)}">
      <div class="rw-head"><b>${window}</b><span>${escapeHtml(review?.target_trade_day || '—')}</span></div>
      <div class="rw-state">${escapeHtml(meta.label)}</div>
    </div>`).join('')}</div>`;
}

/* ========== 渲染：候选提案（永不计入胜率） ========== */
function renderProposals(proposals) {
  const body = root?.querySelector('#proposal-list-body');
  if (!body) return;
  if (!proposals || !proposals.length) {
    body.innerHTML = emptyState('暂无候选提案', '报告、问股、量化与手工观察产生的候选会先在这里留痕，通过风险闸且满足触发证据后才可能晋级正式推荐。');
    return;
  }
  const stateLabel = { proposed: '待审查 / 待触发', approved: '已通过候选审查', vetoed: '风险闸否决', expired: '已过期' };
  const actionLabel = {
    watch: '观察', hold: '持有观察', buy: '拟买入', add: '拟加仓', reduce: '拟减仓', exit: '拟退出', avoid: '回避',
  };
  body.innerHTML = `<div class="proposal-list">${proposals.map((p) => {
    const evidence = p.reference_price_evidence || {};
    const risk = p.risk_decision || {};
    const invalidation = Array.isArray(p.invalidation) ? p.invalidation.filter(Boolean) : [];
    const riskReasons = Array.isArray(risk.reasons) ? risk.reasons.filter(Boolean) : [];
    return `<article class="proposal-card ${escapeHtml(p.state || 'proposed')}">
      <div class="proposal-top">
        <span class="proposal-name">${escapeHtml(p.name || p.symbol || '未命名')}</span>
        <span class="rec-code" data-diag="${escapeHtml(p.symbol || '')}">${escapeHtml(p.symbol || '')}</span>
        <span class="rec-tag src">${escapeHtml(p.source || '—')}</span>
        <span class="rec-tag">${escapeHtml(actionLabel[p.action] || p.action || '观察')}</span>
        <span class="proposal-state">${escapeHtml(stateLabel[p.state] || p.state || '待审查')}</span>
      </div>
      <div class="proposal-body">
        ${p.entry_condition ? `<div><b>触发条件：</b>${escapeHtml(p.entry_condition)}</div>` : '<div><b>触发条件：</b>未设定，仅作观察候选</div>'}
        ${invalidation.length ? `<div><b>失效条件：</b>${invalidation.map(escapeHtml).join('；')}</div>` : ''}
        ${riskReasons.length ? `<div><b>风险闸：</b>${riskReasons.map(escapeHtml).join('；')}</div>` : ''}
      </div>
      <div class="proposal-foot">${escapeHtml(p.created_at || '—')} · ${evidence.evidence_id ? `参考价证据 ${escapeHtml(evidence.evidence_id)}` : '未绑定可核验参考价'} · 不计胜率</div>
    </article>`;
  }).join('')}</div>`;
  body.querySelectorAll('[data-diag]').forEach((el) =>
    el.addEventListener('click', () => gotoDiag(el.dataset.diag)));
}

/* ========== 录入表单（弹窗） ========== */
function openRecForm() {
  injectStyle();
  latestPriceEvidence = null;
  const opt = (arr, name, req) =>
    `<label class="${name === 'rec_reason' ? 'f-full' : ''}"><span>${labelOf(name)}${req ? ' <span class="req">*</span>' : ''}</span>
      <select name="${name}">${arr.map((x) => `<option value="${x}">${x}</option>`).join('')}</select></label>`;
  const body = `
    <form class="rec-form" id="rec-form">
      <label><span>股票代码 <span class="req">*</span></span><input name="code" maxlength="6" placeholder="000000" inputmode="numeric"></label>
      <label><span>名称</span><input name="name" maxlength="20" placeholder="可留空，验证时按行情补"></label>
      ${opt(SOURCES, 'source', true)}
      <label><span>推荐价(元) <span class="req">*</span></span><input name="rec_price" inputmode="decimal" placeholder="输入代码后自动取当前可核验价"><small id="rec-price-proof" style="color:var(--text-3)">尚未绑定行情证据</small></label>
      <label><span>明确方向 <span class="req">*</span></span><select name="direction">${DIRECTIONS.map((x) => `<option value="${x.value}">${x.label}</option>`).join('')}</select></label>
      <label><span>目标仓位上限(%) <span style="color:var(--text-3)">买入/加仓必填</span></span><input name="position_cap_pct" inputmode="decimal" placeholder="如 5；风险闸只会压低，不会代填"></label>
      <label><span>信心(0-100)</span><input name="confidence" inputmode="decimal" placeholder="如 65，最终由证据门封顶"></label>
      <label><span>所属行业</span><input name="industry" maxlength="40" placeholder="用于持仓集中度复核"></label>
      <label><span>预期持有(日)</span><input name="expected_holding_days" inputmode="numeric" placeholder="如 5"></label>
      <label><span>市场状态</span><input name="market_state" maxlength="20" placeholder="如 震荡/进攻"></label>
      <label class="f-full"><span>推荐理由</span><textarea name="rec_reason" maxlength="500" placeholder="当时的判断依据（固定留痕，不事后改写）"></textarea></label>
      <label><span>买入条件</span><input name="buy_condition" maxlength="120" placeholder="如 回踩10日线不破"></label>
      <label><span>卖出/不买条件</span><input name="sell_condition" maxlength="120" placeholder="如 放量滞涨即走"></label>
      <label><span>止损条件</span><input name="stop_loss_condition" maxlength="120" placeholder="如 跌破8%止损"></label>
      <label><span>止盈条件</span><input name="take_profit_condition" maxlength="120" placeholder="如 +20%分批"></label>
      <label><span>板块状态</span><input name="sector_state" maxlength="20" placeholder="如 资金流入"></label>
      <label><span>资金状态</span><input name="capital_state" maxlength="20" placeholder="如 北向净买"></label>
    </form>
    <p style="margin:10px 2px 0;font-size:12px;color:var(--amber);line-height:1.5">⚠ 保存后先形成候选提案。买入/加仓会在服务端检查市场风险、组合仓位与证据完整性；被否决的候选仍保留审计记录，但不会成为正式推荐或计入胜率。自由文本触发条件未被真实行情确认前，也只停留在候选层。</p>`;
  // 用 ui.js 的 openModal（动态 import 避免顶层耦合）
  import('../ui.js').then(({ openModal, closeModal }) => {
    openModal({
      title: '新增候选（先审查 · 后晋级）',
      body,
      footer: `<button class="btn btn-ghost" data-act="cancel">取消</button>
               <button class="btn btn-primary" data-act="save">${icon('save')}保存候选</button>`,
      onMount(modal) {
        modal.dataset.requestId = globalThis.crypto?.randomUUID?.() || `rec-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        modal.querySelector('[data-act="cancel"]').addEventListener('click', closeModal);
        modal.querySelector('[data-act="save"]').addEventListener('click', () => submitRec(modal, closeModal));
        const codeInput = modal.querySelector('input[name="code"]');
        codeInput?.focus();
        codeInput?.addEventListener('input', () => {
          codeInput.value = codeInput.value.replace(/\D/g, '').slice(0, 6);
          if (codeInput.value.length === 6) loadPriceEvidence(modal, codeInput.value);
        });
        modal.querySelector('input[name="rec_price"]')?.addEventListener('input', () => {
          const proof = modal.querySelector('#rec-price-proof');
          const entered = Number(modal.querySelector('input[name="rec_price"]').value);
          const matched = latestPriceEvidence && Math.abs(entered - latestPriceEvidence.value) <= Math.max(0.01, latestPriceEvidence.value * 0.002);
          if (proof && !matched) proof.textContent = '当前为手工价；保存时后端会重新核对，匹配不上则标为未验证';
        });
      },
    });
  });
}

async function loadPriceEvidence(modal, code) {
  const proof = modal.querySelector('#rec-price-proof');
  if (proof) proof.textContent = '正在读取统一行情证据…';
  const requestedCode = code;
  try {
    const response = await api.get(`/market-data/${encodeURIComponent(code)}/last_price`);
    if (modal.querySelector('input[name="code"]')?.value !== requestedCode) return;
    const point = response?.data || response;
    if (!point?.evidence_id || point.value == null || ['missing', 'conflict'].includes(point.quality_status)) throw new Error('当前价不可核验');
    latestPriceEvidence = { code, value: Number(point.value), evidence_id: point.evidence_id, quality_status: point.quality_status };
    const priceInput = modal.querySelector('input[name="rec_price"]');
    if (priceInput && !priceInput.value) priceInput.value = String(point.value);
    if (proof) {
      proof.textContent = `已绑定 ${point.source_id || '免费行情源'} · ${point.quality_status === 'ok' ? '可核验' : '降级'} · ${point.event_time || ''}`;
      proof.style.color = point.quality_status === 'ok' ? 'var(--up)' : 'var(--amber)';
    }
  } catch {
    latestPriceEvidence = null;
    if (proof) { proof.textContent = '当前行情证据不可用，可手工记录，但不会伪装成已验证价'; proof.style.color = 'var(--amber)'; }
  }
}

function labelOf(name) {
  return { source: '推荐来源' }[name] || name;
}

async function submitRec(modal, closeModal) {
  const form = modal.querySelector('#rec-form');
  const get = (n) => (form.elements[n]?.value || '').trim();
  const code = get('code');
  const recPrice = get('rec_price');
  // 前端基础校验：代码 6 位、推荐价为正数（铁律：推荐价必须真实/录入，不可空）
  if (!/^\d{6}$/.test(code)) { toast('股票代码应为 6 位数字', 'error'); return; }
  const priceNum = Number(recPrice);
  if (!recPrice || !isFinite(priceNum) || priceNum <= 0) {
    toast('推荐价必须是有效正数（真实成交/盘口价，不可凭空）', 'error'); return;
  }
  const positionCap = get('position_cap_pct');
  const confidence = get('confidence');
  const direction = get('direction');
  if (['buy', 'add'].includes(direction) && !positionCap) {
    toast('买入/加仓候选必须填写明确目标仓位，风险闸不会自动代填', 'error'); return;
  }
  if (positionCap && (!isFinite(Number(positionCap)) || Number(positionCap) <= 0 || Number(positionCap) > 100)) {
    toast('仓位上限应为 0-100 之间的正数', 'error'); return;
  }
  if (confidence && (!isFinite(Number(confidence)) || Number(confidence) < 0 || Number(confidence) > 100)) {
    toast('信心应为 0-100 之间的数字', 'error'); return;
  }
  const proofMatches = latestPriceEvidence
    && latestPriceEvidence.code === code
    && Math.abs(priceNum - latestPriceEvidence.value) <= Math.max(0.01, latestPriceEvidence.value * 0.002);
  const payload = {
    code,
    name: get('name'),
    source: get('source'),
    rec_price: recPrice,                  // 字符串原样传，后端 float() 校验并落库
    direction,
    rec_type: direction,
    position_cap_pct: positionCap || null,
    confidence: confidence || null,
    confidence_scale: '0_to_100',
    industry: get('industry'),
    expected_holding_days: get('expected_holding_days'),
    market_state: get('market_state'),
    sector_state: get('sector_state'),
    capital_state: get('capital_state'),
    rec_reason: get('rec_reason'),
    buy_condition: get('buy_condition'),
    sell_condition: get('sell_condition'),
    stop_loss_condition: get('stop_loss_condition'),
    take_profit_condition: get('take_profit_condition'),
    client_request_id: modal.dataset.requestId,
    ...(proofMatches ? { price_evidence_id: latestPriceEvidence.evidence_id } : {}),
  };
  const btn = modal.querySelector('[data-act="save"]');
  if (btn) btn.disabled = true;
  try {
    const r = await api.post('/recommendations', payload);
    if (r && r.ok !== false) {
      toast(r.recommendation_created ? '已通过风险闸并形成正式推荐' : '候选提案已留痕，暂不计入胜率', 'success');
      closeModal(); await loadAll();
    }
    else {
      const reasons = (r?.risk_decision?.reasons || []).join('；');
      toast('保存失败：' + (r?.error || '未知错误') + (reasons ? `（${reasons}）` : ''), 'error');
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    const reasons = (e?.data?.risk_decision?.reasons || []).join('；');
    const vetoed = e?.data?.blocked && e?.data?.proposal_id;
    toast((vetoed ? '候选已留痕，但被风险闸否决：' : '保存失败：') + e.message + (reasons ? `（${reasons}）` : ''), 'error');
    if (btn) btn.disabled = false;
  }
}

/* ========== 立即验证（按真实现价回验，append-only） ========== */
async function verifyNow() {
  const btn = root?.querySelector('#rec-verify');
  if (btn) { btn.disabled = true; }
  try {
    const r = await api.post('/recommendations/verify', {});
    if (r && r.ok !== false) {
      const n = r.verified != null ? r.verified : '';
      toast(`已用目标交易日收盘证据回验 ${n} 条（历史判断固定，仅追加验证记录）`, 'success');
      await loadAll();
    } else {
      toast('验证失败：' + (r?.error || '未知错误'), 'error');
    }
  } catch (e) {
    toast('验证失败：' + e.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ========== 加载 ========== */
async function loadAll() {
  const listBody = root?.querySelector('#rec-list-body');
  const statBody = root?.querySelector('#rec-stats-body');
  const proposalBody = root?.querySelector('#proposal-list-body');
  if (listBody) listBody.innerHTML = skeleton(4);
  if (statBody) statBody.innerHTML = skeleton(1, [60]);
  if (proposalBody) proposalBody.innerHTML = skeleton(2);
  // 正式推荐、统计、候选提案并行；任一失败只降级该块，不互相阻塞
  const [listRes, statRes, proposalRes] = await Promise.allSettled([
    api.get('/recommendations'),
    api.get('/recommendations/stats'),
    api.get('/advice-proposals?limit=100'),
  ]);
  if (!root) return;
  if (listRes.status === 'fulfilled') {
    const d = listRes.value;
    renderList(d?.recommendations || d?.list || []);
    const stamp = root.querySelector('#rec-list-stamp');
    if (stamp) stamp.innerHTML = dataStamp(d?.ts);
  } else if (listBody) {
    listBody.innerHTML = emptyState('推荐列表加载失败', listRes.reason?.message || '请重试');
  }
  if (statRes.status === 'fulfilled') renderStats(statRes.value);
  else if (statBody) statBody.innerHTML = emptyState('统计加载失败', '');
  if (proposalRes.status === 'fulfilled') renderProposals(proposalRes.value?.proposals || []);
  else if (proposalBody) proposalBody.innerHTML = emptyState('候选提案加载失败', proposalRes.reason?.message || '请重试');
}

/* ========== 生命周期 ========== */
export function mount(el) {
  root = el;
  injectStyle();
  el.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">建议治理与复盘</h2>
      <span class="page-sub">候选先审查 · 正式推荐才计分 · 历史不可改写</span>
      <div class="ph-extra">
        <button class="btn btn-sm" id="rec-verify">${icon('refresh')}立即验证</button>
        <button class="btn btn-primary" id="rec-add">${icon('plus')}新增候选</button>
      </div>
    </div>
    <div class="rec-note">${icon('info')}<span>候选提案不等于推荐：只有通过证据校验、风险闸与触发确认后，才会晋级正式推荐并进入复盘。历史事件只<b>追加</b>，不覆盖、不事后改写；T0/T1/T2/T5 使用各自目标交易日收盘证据。</span></div>
    <section class="card" style="margin-bottom:var(--gap)">
      <div class="card-head"><h3 class="card-title">候选提案</h3><span class="card-extra" style="font-size:12px;color:var(--text-3)">报告 / 问股 / 量化 / 持仓产生 · 永不直接计入胜率</span></div>
      <div class="card-body" id="proposal-list-body">${skeleton(2)}</div>
    </section>
    <section class="card" style="margin-bottom:var(--gap)">
      <div class="card-head"><h3 class="card-title">正式推荐复盘</h3><span class="card-extra" style="font-size:12px;color:var(--text-3)">仅统计已晋级且可回验的正式推荐</span></div>
      <div class="card-body" id="rec-stats-body">${skeleton(1, [60])}</div>
    </section>
    <section class="card">
      <div class="card-head"><h3 class="card-title">正式推荐列表</h3><span class="card-extra" id="rec-list-stamp"></span></div>
      <div class="card-body" id="rec-list-body">${skeleton(4)}</div>
    </section>`;

  el.querySelector('#rec-add')?.addEventListener('click', openRecForm);
  el.querySelector('#rec-verify')?.addEventListener('click', verifyNow);
  loadAll();
}

export function unmount() {
  closeModal();   // 切走时关掉可能开着的"记录推荐"弹窗（挂在 #modal-root，否则会残留盖在新页面上 + Esc 监听泄漏）
  root = null;
}
