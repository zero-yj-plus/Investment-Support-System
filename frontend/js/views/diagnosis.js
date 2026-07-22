/* views/diagnosis.js — 个股诊断（证据链决策卡）
 *
 * 顶部：6 位代码输入 + 「诊断」按钮 + 常用代码 chips
 *   → api.get('/diagnose?code=') → 渲染结构化决策卡：
 *     · 超大 action 八态（买入/加仓=红、清仓/回避/止损/减仓=绿、持有/观望=琥珀）+ conviction
 *     · 并排两框「狙击位 entry_zone」「失效位 stop」
 *     · 证据列表（方向色点：利多=红 / 利空=绿 / 中性=灰 + 出处徽标）
 *     · summary 结论条 · missing 用 dataStamp 风格淡标
 *
 * 视觉参考 ashare-agent .dcard/.dc-ev/.dc-sn/.dc-chip；本文件只用 app.css 既有 CSS 变量
 * （--up/--down/--text/--text-2/--text-3/--panel/--line/--amber/--brand1 + 派生 bg/line），
 * 不新增 CSS 文件。红涨绿跌严格遵守：动作色与证据方向色均以 --up（红）表强势/利多。
 */

import { api, fmtMoney, fmtNum, fmtPct, fmtPrice, lsGet, lsSet } from '../api.js';
import { icon, escapeHtml, emptyState, skeleton, toast } from '../ui.js';
import { openEvidence } from '../evidence.js';

const LS_RECENT = 'hermes.diagnosis.recent';     // 最近诊断过的代码（最多 8 个）
const DEFAULT_CHIPS = ['600519', '300750', '601318', '000858', '002594', '600036'];

/* 八态 → 配色语义（红涨绿跌）：
 *   进攻/做多（买入/加仓）= 红 up；防守/离场（清仓/回避/止损/减仓）= 绿 down；中性（持有/观望）= 琥珀 amber。 */
const ACTION_TONE = {
  买入: 'up', 加仓: 'up',
  清仓: 'down', 回避: 'down', 止损: 'down', 减仓: 'down',
  持有: 'amber', 观望: 'amber',
};
function actionToneVars(action) {
  const tone = ACTION_TONE[action] || 'amber';
  if (tone === 'up') return { color: 'var(--up)', bg: 'var(--up-bg)', line: 'var(--up-line)' };
  if (tone === 'down') return { color: 'var(--down)', bg: 'var(--down-bg)', line: 'var(--down-line)' };
  return { color: 'var(--amber)', bg: 'var(--amber-dim)', line: 'var(--amber-line)' };
}

/* 证据方向 → 色点（利多=红 / 利空=绿 / 中性=灰）+ 三角符号 */
const DIR_META = {
  利多: { color: 'var(--up)', mark: '▲' },
  利空: { color: 'var(--down)', mark: '▼' },
  中性: { color: 'var(--text-3)', mark: '◆' },
};
function dirMeta(d) { return DIR_META[d] || DIR_META['中性']; }

let root = null;
let loading = false;
let aborter = null;     // 复用 fetch 仅为防陈旧响应：诊断期间换股则丢弃旧结果
let curReqCode = '';

/* ---------------- 渲染：决策卡各部件 ---------------- */

/** 顶部头部：名称 + 代码 + 超大 action 八态徽标 + conviction */
function headerHtml(d) {
  const code = escapeHtml(d.code || '');
  const name = escapeHtml(d.name || '');
  const action = d.action ? escapeHtml(d.action) : '—';
  const structure = String(d.technical_analysis?.structure || '');
  const headline = structure || action;
  const toneAction = structure.includes('偏强') ? '买入' : (structure.includes('偏弱') ? '回避' : d.action);
  const t = actionToneVars(toneAction);
  const conv = d.conviction ? escapeHtml(d.conviction) : '';
  // conviction 不带涨跌语义，统一中性文字色；只在 action 上承载红/绿/琥珀
  return `
    <div class="dg-head">
      <div class="dg-id">
        <div class="dg-id-line">
          <span class="dg-tag">A股</span>
          <span class="dg-name">${name || '—'}</span>
          <span class="dg-code num">${code}</span>
        </div>
      </div>
      <div class="dg-action-wrap">
        <div class="dg-action num" style="color:${t.color};background:${t.bg};border-color:${t.line}">${escapeHtml(headline)}</div>
        <div class="dg-action-cap">交易动作 <b style="color:var(--text-2)">${action}</b>${conv ? ` · 置信 <b style="color:var(--text-2)">${conv}</b>` : ''}</div>
      </div>
    </div>`;
}

function analysisOverviewHtml(d) {
  const pipeline = d.analysis_pipeline;
  const technical = d.technical_analysis;
  if (!pipeline || !technical) return '';
  const stages = Array.isArray(pipeline.stages) ? pipeline.stages : [];
  const stateMeta = {
    complete: { label: '已分析', cls: 'up' },
    partial: { label: '部分', cls: 'flat' },
    missing: { label: '缺失', cls: 'down' },
  };
  const quoteNote = pipeline.quote_note
    ? `<div class="dg-analysis-note">${icon('clock')}${escapeHtml(pipeline.quote_note)}</div>`
    : '';
  const modelText = d.model_connection?.configured
    ? '模型解释可按需补充；交易动作仍只认下方可复算证据与风险闸。'
    : '模型未连接：本卡仍完成确定性多维分析，但不会冒充 AI 已做综合推理。';
  return `
    <section class="dg-analysis" aria-label="本次实际分析内容">
      <div class="dg-analysis-head">
        <div>
          <b>本次实际分析 ${Number(pipeline.completed || 0)}/${Number(pipeline.total || stages.length)} 个维度</b>
          <span>耗时 ${(Number(pipeline.elapsed_ms || 0) / 1000).toFixed(2)} 秒 · 至少 60 根前复权日K才形成完整趋势判断</span>
        </div>
        <span class="dg-analysis-state ${d.analysis_status === 'complete' ? 'up' : 'flat'}">${d.analysis_status === 'complete' ? '结构分析完成' : '数据不足·条件降级'}</span>
      </div>
      <div class="dg-analysis-grid">
        ${stages.map((stage) => {
          const meta = stateMeta[stage.status] || stateMeta.missing;
          const score = Number(stage.score || 0);
          return `<article class="dg-analysis-item">
            <div><b>${escapeHtml(stage.label || stage.id || '分析维度')}</b><i class="${meta.cls}">${meta.label}</i></div>
            <p>${escapeHtml(stage.summary || '—')}</p>
            <small>${escapeHtml(stage.source || '来源未标注')}${score ? ` · 规则分 ${score > 0 ? '+' : ''}${score}` : ''}</small>
          </article>`;
        }).join('')}
      </div>
      ${quoteNote}
      <div class="dg-model-note">${icon('info')}<span>${escapeHtml(modelText)}</span></div>
    </section>`;
}

/** 并排两框：狙击位 entry_zone / 失效位 stop（取不到 = —，绝不编造） */
function levelsHtml(d) {
  const entry = (d.entry_zone != null && d.entry_zone !== '') ? escapeHtml(d.entry_zone) : '—';
  const stop = (d.stop != null && d.stop !== '') ? escapeHtml(d.stop) : '—';
  const isEntry = ['买入', '加仓'].includes(String(d.action || ''));
  const hasHolding = ['持有', '加仓', '减仓', '止损', '清仓'].includes(String(d.signal_action || d.action || ''));
  const entryLabel = isEntry ? '执行区 · 进场' : '关注区 · 转强';
  const stopLabel = hasHolding ? '失效位 · 离场' : '失效条件';
  // 狙击位（进场）用红表“可买区”，失效位用绿表“离场/止损”——与红涨绿跌一致
  return `
    <div class="dg-levels">
      <div class="dg-level" style="border-color:var(--up-line)">
        <div class="dg-level-cap">${entryLabel}</div>
        <div class="dg-level-val num ${entry === '—' ? 'dg-na' : ''}" style="${entry === '—' ? '' : 'color:var(--up)'}">${entry}</div>
        <div class="dg-level-sub">entry_zone</div>
      </div>
      <div class="dg-level" style="border-color:var(--down-line)">
        <div class="dg-level-cap">${stopLabel}</div>
        <div class="dg-level-val num ${stop === '—' ? 'dg-na' : ''}" style="${stop === '—' ? '' : 'color:var(--down)'}">${stop}</div>
        <div class="dg-level-sub">stop</div>
      </div>
    </div>`;
}

/** 证据列表：每条带方向色点 + finding + 出处徽标 */
function evidenceHtml(d) {
  const list = Array.isArray(d.evidence) ? d.evidence : [];
  if (!list.length) {
    if (d.analysis_mode === 'deterministic_market') {
      return `<div class="dg-section-cap">解释方式</div>
        <div class="dg-ev-empty">本卡由上方行情证据与确定性风险规则直接计算；模型解释未在首屏请求，因此这里没有额外的模型论据。</div>`;
    }
    return `<div class="dg-section-cap">证据链</div>
      <div class="dg-ev-empty">暂无结构化证据（缺就标 missing，绝不编造）</div>`;
  }
  const rows = list.map((x) => {
    const m = dirMeta(x.direction);
    const finding = escapeHtml(x.finding || '');
    const dir = x.direction ? escapeHtml(x.direction) : '中性';
    const source = x.source ? escapeHtml(x.source) : '';
    const evidenceRef = String(x.evidence_ref || '');
    const evidenceId = evidenceRef.startsWith('evidence:') ? evidenceRef.slice('evidence:'.length) : '';
    const sourceTag = source ? (evidenceId
      ? `<button type="button" class="dg-ev-src dg-ev-ref" data-evidence-id="${escapeHtml(evidenceId)}" title="查看原始证据">${icon('datasources')}${source}</button>`
      : `<span class="dg-ev-src">${icon('datasources')}${source}</span>`) : '';
    return `
      <div class="dg-ev">
        <span class="dg-ev-dot" style="background:${m.color}"></span>
        <div class="dg-ev-body">
          <div class="dg-ev-find">${finding || '—'}</div>
          ${sourceTag}
        </div>
        <span class="dg-ev-dir" style="color:${m.color};border-color:${m.color}">${m.mark} ${dir}</span>
      </div>`;
  }).join('');
  return `<div class="dg-section-cap">⛏ 这个结论怎么来的 · 证据链</div>
    <div class="dg-ev-list">${rows}</div>`;
}

function marketEvidenceHtml(d) {
  const points = Array.isArray(d.market_evidence) ? d.market_evidence.filter((p) => p?.evidence_id) : [];
  const decisionPoints = Array.isArray(d.decision_market_evidence) ? d.decision_market_evidence.filter((p) => p?.evidence_id) : [];
  const quality = d.market_evidence_quality || {};
  const labels = {
    last_price: '现价', prev_close: '昨收', change_pct: '涨跌幅', amount: '成交额',
    turnover_rate: '换手率', volume_ratio: '量比', pe: '市盈率', pb: '市净率',
  };
  const missing = Array.isArray(quality.missing_fields) ? quality.missing_fields : [];
  const status = quality.status || (points.length ? 'ok' : 'unavailable');
  const statusText = status === 'ok' ? '全部可核验' : (status === 'conflict' ? '存在跨源冲突' : (status === 'degraded' ? '部分降级' : '暂无可核验行情'));
  const qualityText = { ok: '可用', degraded: '降级', conflict: '冲突', missing: '缺失' };
  const valueText = (point) => {
    if (point.value == null) return '—';
    if (point.unit === 'percent') return fmtPct(point.value);
    if (point.unit === 'CNY') return ['amount', 'total_mv', 'float_mv'].includes(point.field) ? fmtMoney(point.value) : `¥${fmtPrice(point.value)}`;
    if (point.unit === 'share') return `${fmtNum(point.value)} 股`;
    return point.unit === 'ratio' ? `${fmtNum(point.value, 2)} 倍` : fmtNum(point.value, 2);
  };
  const pointButtons = (rows) => rows.map((point) => `<button type="button" class="dg-evidence-btn"
      data-evidence-id="${escapeHtml(point.evidence_id)}">
      <span>${escapeHtml(labels[point.field] || point.field)}</span>
      <b class="num">${escapeHtml(valueText(point))}</b>
      <i class="${point.quality_status === 'ok' ? 'up' : (point.quality_status === 'conflict' ? 'down' : 'flat')}">${escapeHtml(qualityText[point.quality_status] || point.quality_status || '—')}</i>
    </button>`).join('');
  const currentIds = points.map((point) => point.evidence_id).sort().join('|');
  const decisionIds = decisionPoints.map((point) => point.evidence_id).sort().join('|');
  const sameSnapshot = Boolean(currentIds) && currentIds === decisionIds;
  const generatedAt = d.decision_generated_at ? new Date(d.decision_generated_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '未知';
  return `<div class="dg-section-cap">${sameSnapshot ? '结论生成时的行情证据' : '当前行情证据（快数据）'}</div>
    <div class="dg-evidence-summary ${status === 'ok' ? 'up' : (status === 'conflict' ? 'down' : 'flat')}">
      ${escapeHtml(statusText)} · ${Number(quality.captured ?? points.length)}/${Number(quality.requested ?? 8)}
      ${missing.length ? `<span>缺失：${missing.map((field) => escapeHtml(labels[field] || field)).join('、')}</span>` : ''}
    </div>
    ${points.length ? `<div class="dg-market-evidence">${pointButtons(points)}</div>` : '<div class="dg-ev-empty">主备免费行情源当前均不可用；本卡不会把缺失值当成 0。</div>'}
    ${sameSnapshot ? `<div class="dg-snapshot-note">上方动作生成于 ${escapeHtml(generatedAt)} 北京时间，当前快照与生成快照一致。</div>` : `<div class="dg-snapshot-note">当前快行情只用于展示与安全降级，未参与上方已锁定结论。</div>
      ${decisionPoints.length ? `<details class="dg-decision-evidence"><summary>查看结论生成快照 · ${escapeHtml(generatedAt)} 北京时间 · ${decisionPoints.length} 条</summary><div class="dg-market-evidence">${pointButtons(decisionPoints)}</div></details>` : ''}`}`;
}

/** summary 结论条 */
function summaryHtml(d) {
  const s = (d.summary != null && d.summary !== '') ? escapeHtml(d.summary) : '';
  if (!s) return '';
  return `
    <div class="dg-summary">
      <div class="dg-summary-cap">▶ 一句话结论</div>
      <div class="dg-summary-text">${s}</div>
    </div>`;
}

function riskHtml(d) {
  const risk = d.risk_decision;
  if (!risk || typeof risk !== 'object') return '';
  const signalAction = String(d.signal_action || d.action || '');
  const isEntrySignal = ['买入', '加仓'].includes(signalAction);
  const noNewExposure = !isEntrySignal && risk.decision === 'allow';
  const softEntryBlock = isEntrySignal && risk.decision === 'veto' && d.action === '观望';
  const meta = noNewExposure
    ? { label: '当前动作不增加风险', cls: 'flat' }
    : softEntryBlock
      ? { label: '信号可关注，新仓暂未授权', cls: 'flat' }
      : ({
    allow: { label: '风险闸通过', cls: 'up' },
    reduce: { label: '风险闸已缩仓/降信心', cls: 'flat' },
    veto: { label: '风险闸已禁止新增敞口', cls: 'down' },
  }[risk.decision] || { label: '风险闸状态未知', cls: 'flat' });
  const reasons = Array.isArray(risk.reasons) ? risk.reasons : [];
  const capText = isEntrySignal && risk.position_cap_pct != null
    ? ` · 新增仓位上限 ${escapeHtml(String(risk.position_cap_pct))}%`
    : '';
  return `<div class="dg-risk ${meta.cls}">
    <div><b>${escapeHtml(meta.label)}</b>
      <span>当前结论 ${escapeHtml(d.action || risk.final_action || '—')}${capText}</span>
    </div>
    ${reasons.length && !noNewExposure ? `<ul>${reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join('')}</ul>` : `<span>${noNewExposure ? '持有/观望不需新仓授权，不代表目标仓位是 0%' : '未触发额外风险限制'}</span>`}
    <small>${escapeHtml(risk.rule_version || '')} · input ${escapeHtml(String(risk.input_snapshot_hash || '').slice(0, 12))}</small>
  </div>`;
}

/** missing：缺失字段淡标（铁律：取不到就如实标，不编造） */
function missingHtml(d) {
  const miss = Array.isArray(d.missing) ? d.missing.filter(Boolean) : [];
  if (!miss.length) return '';
  return `
    <div class="dg-missing">
      ${icon('info')}
      <span>数据缺口（如实标注，作为约束参与结论且未补 0）：${miss.map((m) => `<code>${escapeHtml(String(m))}</code>`).join(' ')}</span>
    </div>`;
}

/** 完整决策卡 */
function cardHtml(d) {
  const deterministic = ['deterministic_market', 'deterministic_deep'].includes(d.analysis_mode);
  const foot = d.analysis_mode === 'deterministic_deep'
    ? '深度确定性诊断 · 60日趋势 / RSI / MACD / ATR / 量价 / 结构位 / 资金 / 行业 · 模型不参与规则动作'
    : deterministic
      ? '结构化诊断 · 真实行情证据 · 确定性规则复核 · 模型不参与首屏动作'
    : '结构化诊断 · 真实数据注入 · AI 推导 + 确定性规则复核 · 信息分析，非投资建议';
  return `
    <div class="dg-card">
      ${headerHtml(d)}
      ${analysisOverviewHtml(d)}
      ${levelsHtml(d)}
      ${summaryHtml(d)}
      ${riskHtml(d)}
      ${marketEvidenceHtml(d)}
      ${evidenceHtml(d)}
      ${missingHtml(d)}
      <div class="dg-next">
        <button type="button" class="btn btn-primary dg-explain" data-code="${escapeHtml(d.code || '')}">
          ${icon('chat')}让 AI 解释这张确定性诊断卡
        </button>
        <span>AI 只能解释已冻结的动作、证据与失效条件，不能改写规则结论或另造价位。</span>
      </div>
      <div class="dg-foot">${foot}</div>
    </div>`;
}

/* ---------------- 作用域样式（一次性注入，仅本视图用，全部基于既有 CSS 变量） ---------------- */

const STYLE_ID = 'diagnosis-scoped-style';
function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const el = document.createElement('style');
  el.id = STYLE_ID;
  el.textContent = `
.dg-wrap { max-width: 880px; margin: 0 auto; }
.dg-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }
.dg-bar .input { width: 200px; max-width: 100%; font-family: var(--mono); letter-spacing: 2px; }
.dg-chips { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 18px; }
.dg-chips .dg-chips-cap { font-size: 13px; color: var(--text-3); }
.dg-chips .chip .num { font-family: var(--mono); letter-spacing: 1px; }

.dg-card {
  position: relative; background: var(--panel); border: 1px solid var(--border);
  padding: 18px 20px 16px; border-radius: 14px;
}
.dg-ev-ref { appearance:none; cursor:pointer; font:inherit; }
.dg-ev-ref:hover { color:var(--brand1); border-color:var(--brand1); }
.dg-risk { margin:12px 0; padding:10px 12px; border:1px solid var(--line); border-radius:10px; background:var(--panel-2); font-size:12px; color:var(--text-2); line-height:1.6; }
.dg-risk > div { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; }
.dg-risk ul { margin:5px 0 0 18px; padding:0; }
.dg-risk small { color:var(--text-3); font-family:var(--mono); }
.dg-next { margin-top:14px; padding-top:14px; border-top:1px solid var(--line); display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.dg-next span { color:var(--text-3); font-size:12px; line-height:1.5; }
.dg-card::before, .dg-card::after {
  content: ""; position: absolute; width: 14px; height: 14px; pointer-events: none;
}
.dg-card::before { top: -1px; left: -1px; border-top: 1px solid var(--amber-line); border-left: 1px solid var(--amber-line); border-top-left-radius: 14px; }
.dg-card::after { bottom: -1px; right: -1px; border-bottom: 1px solid var(--amber-line); border-right: 1px solid var(--amber-line); border-bottom-right-radius: 14px; }

.dg-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; flex-wrap: wrap; }
.dg-id-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.dg-tag { font-size: 12px; color: var(--text-3); border: 1px solid var(--line); padding: 1px 7px; border-radius: 6px; }
.dg-name { font-size: 22px; font-weight: 800; color: var(--text); }
.dg-code { font-size: 16px; color: var(--text-3); font-family: var(--mono); letter-spacing: 1px; }
.dg-action-wrap { text-align: center; flex-shrink: 0; }
.dg-action {
  display: inline-block; min-width: 132px; padding: 9px 18px; border: 1.5px solid;
  border-radius: 12px; font-size: 23px; font-weight: 800; letter-spacing: 1px; line-height: 1.15;
}
.dg-action-cap { font-size: 12px; color: var(--text-3); margin-top: 5px; }

.dg-analysis { margin-top:16px; padding:14px; border:1px solid var(--border); border-radius:12px; background:var(--panel-2); }
.dg-analysis-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:10px; }
.dg-analysis-head>div { display:flex; flex-direction:column; gap:3px; }
.dg-analysis-head b { color:var(--text); font-size:14px; }
.dg-analysis-head span { color:var(--text-3); font-size:11px; line-height:1.5; }
.dg-analysis-state { flex:0 0 auto; padding:3px 8px; border:1px solid currentColor; border-radius:999px; font-size:11px; }
.dg-analysis-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
.dg-analysis-item { min-width:0; padding:10px 11px; border:1px solid var(--line); border-radius:9px; background:var(--panel); }
.dg-analysis-item>div { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.dg-analysis-item b { font-size:13px; color:var(--text); }
.dg-analysis-item i { flex:0 0 auto; font-size:10px; font-style:normal; }
.dg-analysis-item p { margin:6px 0 4px; color:var(--text-2); font-size:12px; line-height:1.55; }
.dg-analysis-item small { display:block; color:var(--text-3); font-size:10px; line-height:1.4; }
.dg-analysis-note,.dg-model-note { display:flex; align-items:flex-start; gap:6px; margin-top:9px; color:var(--text-3); font-size:11px; line-height:1.55; }
.dg-analysis-note svg,.dg-model-note svg { width:14px; height:14px; flex:0 0 auto; margin-top:1px; }
.dg-model-note { padding-top:9px; border-top:1px dashed var(--line); color:var(--text-2); }

.dg-levels { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 16px; }
.dg-level {
  border: 1.5px solid var(--line); background: var(--panel-2);
  border-radius: 12px; padding: 11px 14px; text-align: center;
}
.dg-level-cap { font-size: 12px; color: var(--text-3); }
.dg-level-val { font-size: 23px; font-weight: 800; color: var(--text); margin: 3px 0 1px; font-family: var(--mono); word-break: break-word; }
.dg-level-val.dg-na { color: var(--text-3); font-weight: 600; }
.dg-level-sub { font-size: 11px; color: var(--text-3); font-family: var(--mono); }

.dg-summary {
  margin-top: 16px; border: 1px solid var(--amber-line); background: var(--amber-dim);
  border-radius: 10px; padding: 11px 14px;
}
.dg-summary-cap { font-size: 12px; color: var(--amber); font-weight: 700; margin-bottom: 3px; }
.dg-summary-text { font-size: 15px; color: var(--text); line-height: 1.65; }

.dg-section-cap { font-size: 12px; color: var(--text-3); margin: 18px 0 8px; }
.dg-ev-list { display: flex; flex-direction: column; gap: 9px; }
.dg-ev {
  display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px;
  border: 1px solid var(--line); background: var(--panel-2); border-radius: 10px;
}
.dg-ev-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; margin-top: 5px; }
.dg-ev-body { flex: 1; min-width: 0; }
.dg-ev-find { font-size: 14px; color: var(--text); line-height: 1.55; }
.dg-ev-src {
  display: inline-flex; align-items: center; gap: 4px; margin-top: 5px;
  font-size: 12px; color: var(--text-3); border: 1px solid var(--line);
  padding: 1px 7px; border-radius: 6px;
}
.dg-ev-src svg { width: 12px; height: 12px; }
.dg-ev-dir {
  flex-shrink: 0; align-self: flex-start; white-space: nowrap;
  font-size: 12px; font-weight: 700; border: 1px solid; padding: 1px 8px; border-radius: 6px;
}
.dg-ev-empty { font-size: 14px; color: var(--text-3); padding: 10px 12px; border: 1px dashed var(--line); border-radius: 10px; }
.dg-market-evidence { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 8px; }
.dg-evidence-summary { display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:11px;margin:-2px 0 8px; }
.dg-snapshot-note { font-size:11px;color:var(--text-3);margin:8px 0 0;line-height:1.55; }
.dg-decision-evidence { margin-top:8px;border-top:1px dashed var(--line);padding-top:8px; }.dg-decision-evidence summary { cursor:pointer;font-size:12px;color:var(--text-2);margin-bottom:8px; }
.dg-evidence-btn { display:grid;grid-template-columns:minmax(64px,auto) 1fr auto;align-items:center;gap:8px;text-align:left;
  border:1px solid var(--line);background:var(--panel-2);color:var(--text);border-radius:9px;padding:9px 11px;cursor:pointer; }
.dg-evidence-btn:hover,.dg-evidence-btn:focus-visible { border-color:var(--accent);outline:none; }
.dg-evidence-btn span { font-size:12px;color:var(--text-3); }.dg-evidence-btn b { font-size:13px;text-align:right; }
.dg-evidence-btn i { font-size:10px;font-style:normal;text-transform:uppercase; }

.dg-missing {
  display: flex; align-items: flex-start; gap: 7px; margin-top: 16px;
  font-size: 13px; color: var(--text-3); line-height: 1.6;
}
.dg-missing svg { width: 15px; height: 15px; flex-shrink: 0; margin-top: 1px; }
.dg-missing code {
  font-family: var(--mono); font-size: 12px; color: var(--text-2);
  background: var(--panel-3); padding: 0 5px; border-radius: 4px;
}
.dg-foot {
  margin-top: 16px; border-top: 1px solid var(--line); padding-top: 9px;
  font-size: 11px; color: var(--text-3);
}

@media (max-width: 600px) {
  .dg-action { font-size: 22px; min-width: 84px; padding: 8px 16px; }
  .dg-name { font-size: 19px; }
  .dg-levels { grid-template-columns: 1fr; }   /* 移动端单列：狙击位/失效位上下叠放 */
  .dg-level-val { font-size: 21px; }
  .dg-ev { flex-wrap: wrap; }
  .dg-ev-dir { align-self: flex-start; }
  .dg-market-evidence { grid-template-columns: 1fr; }
  .dg-analysis-grid { grid-template-columns: 1fr; }
  .dg-analysis-head { flex-direction:column; }
}`;
  document.head.appendChild(el);
}

/* ---------------- 数据请求与状态渲染 ---------------- */

function getRecent() {
  const r = lsGet(LS_RECENT, []);
  return Array.isArray(r) ? r.filter((c) => /^\d{6}$/.test(c)) : [];
}
function pushRecent(code) {
  const r = getRecent().filter((c) => c !== code);
  r.unshift(code);
  lsSet(LS_RECENT, r.slice(0, 8));
  renderChips();
}

function renderChips() {
  const wrap = root?.querySelector('#dg-chips');
  if (!wrap) return;
  const recent = getRecent();
  // 优先展示最近诊断过的代码，不足则用常用代码补齐到一行
  const codes = [...recent];
  for (const c of DEFAULT_CHIPS) { if (!codes.includes(c) && codes.length < 8) codes.push(c); }
  wrap.innerHTML = `<span class="dg-chips-cap">${recent.length ? '最近 / 常用' : '常用代码'}</span>`
    + codes.map((c) => `<span class="chip clickable dg-code-chip" data-code="${escapeHtml(c)}"><span class="num">${escapeHtml(c)}</span></span>`).join('');
}

function showResult(html) {
  const box = root?.querySelector('#dg-result');
  if (box) box.innerHTML = html;
}

// 数据卡先出：快行情只负责确认“股票与价格可用”；完整分析必须继续完成
// 60日趋势、动量、波动、量价、结构位、行业、资金和财务，不把秒级快照冒充结论。
function previewHtml(q) {
  const chg = q.change_pct;
  const c = chg == null ? 'flat' : chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat';
  const f = (v, s = '') => (v == null ? '—' : v + s);
  const amt = q.amount ? (Number(q.amount) / 1e8).toFixed(1) + '亿' : '—';
  return `<div class="dg-card">
    <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;padding:2px 0 12px;border-bottom:1px solid var(--border)">
      <b style="font-size:18px">${escapeHtml(q.name || q.code || '')}</b>
      <span style="color:var(--text-3)">${escapeHtml(q.code || '')}</span>
      <span style="font-size:22px;font-weight:600" class="${c}">${f(q.price)}</span>
      <span class="${c}">${chg == null ? '' : (chg > 0 ? '+' : '') + chg + '%'}</span>
      <span style="color:var(--text-3);font-size:13px">量比 ${f(q.volume_ratio)} · 换手 ${f(q.turnover_rate, '%')} · 成交额 ${amt}</span>
    </div>
    <div style="display:flex;align-items:center;gap:8px;color:var(--text-2);font-size:14px;padding:12px 0 8px">
      <span class="btn-spin"></span>基础行情已就绪；正在并行读取日K/行业/资金，并计算 MA、RSI、MACD、ATR 与结构位…
    </div>
    ${skeleton(4, [44, 16, 16, 16])}
  </div>`;
}

async function diagnose(code) {
  code = String(code || '').trim();
  if (!/^\d{6}$/.test(code)) {
    toast('请输入 6 位股票代码', 'error');
    root?.querySelector('#dg-code')?.focus();
    return;
  }
  if (loading) { try { aborter?.abort(); } catch { /* noop */ } }
  loading = true;
  curReqCode = code;
  setBtnBusy(true);
  const inp = root?.querySelector('#dg-code');
  if (inp) inp.value = code;

  // 骨架：模拟头部 + 两框 + 几条证据的高度
  showResult(`<div class="dg-card">${skeleton(7, [30, 56, 18, 44, 16, 16, 16])}</div>`);
  let previewQuote = null;

  // 数据卡先出：并行秒拉 quick_quote（~0.1s）先显现价，AI 决策全卡随后覆盖（不阻塞下方全卡请求）。
  aborter = new AbortController();
  api.get(`/quote/${encodeURIComponent(code)}`, { signal: aborter.signal }).then((qr) => {
    if (curReqCode === code && qr && qr.quote) {
      previewQuote = qr.quote;
      if (loading) showResult(previewHtml(qr.quote));
    }
  }).catch(() => { /* 预览失败无妨，继续等全卡 */ });

  let d;
  try {
    d = await api.get(`/diagnose?code=${encodeURIComponent(code)}`, { signal: aborter.signal });
  } catch (e) {
    if (e.name === 'AbortError') return;       // 被新一轮诊断取代，静默
    if (curReqCode !== code) return;           // 陈旧响应
    const notFound = e?.status === 404 && e?.data?.error_code === 'stock_not_found';
    const action = notFound
      ? ''
      : `<button class="btn btn-sm btn-primary dg-retry" data-code="${escapeHtml(code)}">${icon('refresh')}重试</button>`;
    if (previewQuote) {
      // 快行情已成功时，只降级“诊断增强”，不再把整张行情卡擦掉。
      showResult(`${previewHtml(previewQuote)}<div class="dg-ev-empty" style="margin-top:10px">诊断增强本次未完成：${escapeHtml(e.message || '请求超时')}。基础行情仍可用，本次不输出新动作。 ${action}</div>`);
    } else {
      showResult(emptyState(
        notFound
          ? '未找到该 A 股代码'
          : (e?.name === 'TimeoutError' ? '诊断增强超时' : '诊断服务暂时不可用'),
        e.message || '请求出错，请稍后重试', action, notFound ? 'search' : 'alert',
      ));
    }
    return;
  } finally {
    if (curReqCode === code) { loading = false; setBtnBusy(false); }
  }

  if (curReqCode !== code) return;             // 期间已换股，丢弃
  if (!d || d.ok === false) {
    showResult(emptyState(
      '暂无诊断结果',
      (d && d.error) || '未取到该股票的结构化诊断',
      `<button class="btn btn-sm btn-primary dg-retry" data-code="${escapeHtml(code)}">${icon('refresh')}重试</button>`,
      'search',
    ));
    return;
  }

  // 慢诊断即使降级也不能把已经成功的快盘口名称清空。数值证据仍只认后端
  // evidence_id；这里仅保留已展示过的真实名称/代码，不在前端伪造结论字段。
  if (previewQuote && !d.name) d.name = previewQuote.name || '';
  if (!d.code) d.code = code;

  pushRecent(code);
  showResult(cardHtml(d));
}

function setBtnBusy(busy) {
  const btn = root?.querySelector('#dg-go');
  if (!btn) return;
  btn.disabled = busy;
  btn.innerHTML = busy
    ? `<span class="btn-spin"></span>诊断中…`
    : `${icon('spark')}诊断`;
}

/* ---------------- 挂载 / 卸载 ---------------- */

export function mount(container) {
  root = container;
  ensureStyle();

  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">个股诊断</h2>
      <span class="page-sub">综合趋势、量价、动量、资金、行业与财务证据，形成条件化结论</span>
    </div>
    <div class="dg-wrap">
      <section class="card" style="padding:16px 18px">
        <div class="dg-bar">
          <input class="input" id="dg-code" maxlength="6" inputmode="numeric"
                 placeholder="股票代码（6 位，如 600519）" autocomplete="off">
          <button class="btn btn-primary" id="dg-go">${icon('spark')}诊断</button>
          <span style="font-size:13px;color:var(--text-3)">输入 6 位代码，完成趋势、动量、量价、波动、位置、资金、行业与财务分析后再形成条件化结论</span>
        </div>
        <div class="dg-chips" id="dg-chips"></div>
      </section>
      <div id="dg-result" style="margin-top:16px">
        ${emptyState('输入股票代码开始深度诊断', '先确认实时行情，再并行分析至少 60 根前复权日K、量价、动量、波动、结构位、资金和行业；取不到的数据如实标注，不用秒级快照冒充分析。', '', 'search')}
      </div>
    </div>`;

  renderChips();

  const inp = container.querySelector('#dg-code');
  container.querySelector('#dg-go').addEventListener('click', () => diagnose(inp.value));
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); diagnose(e.target.value); }
  });
  // 只允许数字输入（粘贴含字母时即时清洗）
  inp.addEventListener('input', () => { inp.value = inp.value.replace(/\D/g, '').slice(0, 6); });

  // 常用 / 最近代码 chips（事件委托）
  container.querySelector('#dg-chips').addEventListener('click', (e) => {
    const chip = e.target.closest('.dg-code-chip');
    if (chip) diagnose(chip.dataset.code);
  });

  // 失败/空态的「重试」按钮（事件委托）
  container.querySelector('#dg-result').addEventListener('click', (e) => {
    const btn = e.target.closest('.dg-retry');
    if (btn) diagnose(btn.dataset.code);
    const explain = e.target.closest('.dg-explain');
    if (explain && /^\d{6}$/.test(explain.dataset.code || '')) {
      sessionStorage.setItem('hermes.chat.code', explain.dataset.code);
      sessionStorage.setItem(
        'hermes.chat.prompt',
        '请解释这只股票刚刚的确定性诊断：先回答现在最应关注什么，再说明趋势、量价、风险闸和失效条件。不得改写冻结动作和价位。',
      );
      location.hash = '#/chat';
      return;
    }
    const evidence = e.target.closest('[data-evidence-id]');
    if (evidence) openEvidence(evidence.dataset.evidenceId);
  });

  // 跨页跳转携带的诊断意图（与 chat.js 的 hermes.chat.code 同源约定，独立 key 避免互相清空）
  const pending = sessionStorage.getItem('hermes.diagnosis.code');
  if (pending && /^\d{6}$/.test(pending)) {
    sessionStorage.removeItem('hermes.diagnosis.code');
    diagnose(pending);
  }
}

export function unmount() {
  if (aborter) { try { aborter.abort(); } catch { /* noop */ } aborter = null; }
  loading = false;
  root = null;
}
