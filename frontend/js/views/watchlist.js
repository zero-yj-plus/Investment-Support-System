/* views/watchlist.js — 观察池（母文档 §21）
   ------------------------------------------------------------------
   观察池 = 客户「想买但还没买」的股票集合，等价格触发才买（不是"准备推荐买"清单）。
   每条卡片回答：现在能不能买 / 到什么价格才能买 / 什么条件满足 / 涨高了能不能追 /
   跌破哪里放弃 / 是否移出观察。

   铁律（不可破）：
   - 所有现价/涨跌幅来自后端 /api/watchlist（内部使用批量 quick_quotes 真实行情）。
   - 现价取不到 → 后端 grade=null + missing 标注，本视图如实显示「现价缺失」，
     绝不用理想区间端点反推一个"假装的现价"，绝不编数字。
   - A–E 评级 / 今日状态 / 建议动作是后端确定性价格逻辑算出来的（现价 vs 理想买入区间），
     本视图只渲染、不二次计算、不调 AI 改写。前端仅做色标/排序/文案映射。
   - 双周期理想区间由后端纯数字算法计算；用户手填区间存在时只作为明确覆盖值。
   - 前端不计算价格、不调用 AI，不用旧交易日超短区间冒充当日结果。 */

import { api, fmtPrice, fmtPct, pctClass, isTradingTime } from '../api.js';
import { icon, escapeHtml, toast, emptyState, skeleton, dataStamp, confirmModal, openModal, closeModal } from '../ui.js';

let root = null;
let timer = null;
let gen = 0;            // 挂载代次：异步回来若已换页/重挂则丢弃（防陈旧覆盖）
let items = [];         // 后端返回的整条（含 grade/status/quote/missing）

/* A–E 评级 → 展示元信息（母文档 §21.4 + §21.5）。
   配色独立于 A股红涨绿跌语义：A 用强调色(接近买点最醒目)，E 用灰(建议移出)。
   color = 等级主色；act = §21.5 建议动作；rank = 排序权重(A 最前)。 */
const GRADE_META = {
  A: { label: 'A', name: '接近买点', color: 'var(--accent, #5b8cff)', act: '到区间，可按计划小仓建仓', rank: 0 },
  B: { label: 'B', name: '继续观察', color: 'var(--amber, #f5a623)', act: '略高于区间，继续等回落，不追', rank: 1 },
  C: { label: 'C', name: '价格不合适', color: '#9aa3b8', act: '涨高了，逻辑仍在但不追', rank: 2 },
  D: { label: 'D', name: '风险升高', color: 'var(--down, #18b56a)', act: '逼近下沿/放弃位，警惕，不抢', rank: 3 },
  E: { label: 'E', name: '建议移出', color: 'var(--text-3, #8b93a7)', act: '跌破放弃位，建议移出观察', rank: 4 },
};
// grade 缺失（现价取不到）排在最后，单独灰色处理。
const NULL_RANK = 9;

function gradeMeta(g) { return GRADE_META[g] || null; }
function gradeRank(g) { const m = gradeMeta(g); return m ? m.rank : NULL_RANK; }

const RISK_TXT = { 1: '极低', 2: '低', 3: '中', 4: '高', 5: '极高' };
// 风险等级斜坡：与涨跌红绿解耦，1 蓝(温和)→5 红(危险)。
const RISK_COLOR = { 1: '#3b82f6', 2: '#14b8a6', 3: '#f5a623', 4: '#ff7a45', 5: '#ff4d5e' };

function injectStyle() {
  if (document.getElementById('watchlist-style')) return;
  const css = `
  .wl-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:var(--gap)}
  .wl-card{position:relative;border:1px solid var(--border);border-radius:var(--radius);
    background:var(--panel);padding:14px 16px 13px;display:flex;flex-direction:column;gap:9px;
    box-shadow:var(--shadow-soft);overflow:hidden;transition:border-color .12s,box-shadow .12s}
  .wl-card:hover{border-color:var(--border-strong)}
  .wl-card::before{content:"";position:absolute;inset:0 auto 0 0;width:4px;background:var(--g-c,var(--flat))}
  .wl-head{display:flex;align-items:flex-start;gap:10px}
  .wl-grade{flex:none;width:34px;height:34px;border-radius:9px;display:flex;align-items:center;justify-content:center;
    font-size:18px;font-weight:800;color:#fff;background:var(--g-c,var(--flat));line-height:1;
    box-shadow:0 2px 8px color-mix(in srgb,var(--g-c,var(--flat)) 40%,transparent)}
  .wl-grade.na{background:var(--text-3);box-shadow:none;font-size:12px}
  .wl-titlewrap{min-width:0;flex:1}
  .wl-name{font-size:16px;font-weight:800;color:var(--text);cursor:pointer;display:inline-flex;align-items:center;gap:6px}
  .wl-name:hover{color:var(--accent)}
  .wl-code{font-family:var(--mono,monospace);font-size:12px;color:var(--text-3);cursor:pointer}
  .wl-code:hover{color:var(--accent)}
  .wl-sub{margin-top:2px;font-size:12px;color:var(--text-3);display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .wl-status{font-size:13px;font-weight:700;color:var(--g-c,var(--text))}
  .wl-acts{flex:none;display:flex;gap:4px}
  .wl-px{display:flex;align-items:baseline;gap:10px;padding:7px 11px;border-radius:var(--radius-sm);
    background:var(--panel-2);border:1px solid var(--border)}
  .wl-px .pk{font-size:11px;color:var(--text-3)}
  .wl-px .pv{font-size:21px;font-weight:800;font-family:var(--mono,monospace);color:var(--text)}
  .wl-px .pv.na{font-size:14px;color:var(--text-3)}
  .wl-auto-zones{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}
  .wl-auto-zone{padding:7px 9px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel-2)}
  .wl-auto-zone .zk{display:flex;justify-content:space-between;gap:8px;font-size:11px;color:var(--text-3)}
  .wl-auto-zone .zv{margin-top:3px;font:700 14px var(--mono,monospace);color:var(--text)}
  .wl-auto-zone .zv.missing{font-family:inherit;font-weight:500;font-size:12px;color:var(--text-3)}
  .wl-px .pchg{font-family:var(--mono,monospace);font-size:13px;font-weight:700}
  .wl-px .pband{margin-left:auto;text-align:right}
  .wl-px .pband .bk{font-size:11px;color:var(--text-3)}
  .wl-px .pband .bv{font-size:14px;font-weight:700;font-family:var(--mono,monospace);color:var(--text-2)}
  .wl-rows{display:flex;flex-direction:column;gap:6px}
  .wl-row{display:flex;gap:8px;font-size:13px;line-height:1.5}
  .wl-row .rk{flex:none;width:62px;color:var(--text-3);font-size:12px;padding-top:1px}
  .wl-row .rv{flex:1;color:var(--text-2);word-break:break-word}
  .wl-row .rv.muted{color:var(--text-3)}
  .wl-act{margin-top:1px;padding:8px 11px;border-radius:var(--radius-sm);background:var(--g-bg,var(--panel-2));
    border:1px solid var(--g-line,var(--border));font-size:13px;line-height:1.5;color:var(--text)}
  .wl-act .at{font-size:11px;font-weight:800;letter-spacing:.4px;color:var(--g-c,var(--text-2));margin-bottom:2px}
  .wl-foot{display:flex;align-items:center;gap:10px;margin-top:1px;font-size:12px;color:var(--text-3);flex-wrap:wrap}
  .wl-risk{display:inline-flex;align-items:center;gap:5px}
  .wl-risk i{width:7px;height:7px;border-radius:50%;background:var(--rk-c,var(--flat));display:inline-block}
  .wl-missing{font-size:11px;color:var(--amber);border:1px solid var(--amber);border-radius:9px;padding:0 7px;line-height:1.7}

  /* 表单 */
  .wl-form{display:grid;grid-template-columns:repeat(2,1fr);gap:10px 14px}
  .wl-form .ff{display:flex;flex-direction:column;gap:4px}
  .wl-form .ff.full{grid-column:1/-1}
  .wl-form label{font-size:12px;color:var(--text-3);font-weight:600}
  .wl-form label .req{color:var(--down)}
  .wl-form input,.wl-form textarea,.wl-form select{width:100%;box-sizing:border-box;background:var(--panel-2);
    border:1px solid var(--border);border-radius:var(--radius-sm);padding:7px 10px;color:var(--text);font-size:14px;font-family:inherit}
  .wl-form textarea{resize:vertical;min-height:48px}
  .wl-form .hint{font-size:11px;color:var(--text-3);line-height:1.4}
  .wl-band2{display:flex;gap:8px;align-items:center}
  .wl-band2 input{flex:1}
  .wl-band2 span{color:var(--text-3);font-size:13px}

  @media (max-width:760px){
    .wl-grid{grid-template-columns:1fr}
    .wl-form{grid-template-columns:1fr}
    .wl-auto-zones{grid-template-columns:1fr}
    .wl-px .pband{margin-left:0;width:100%;text-align:left;margin-top:4px}
  }`;
  const tag = document.createElement('style');
  tag.id = 'watchlist-style';
  tag.textContent = css;
  document.head.appendChild(tag);
}

/* 点代码/名称 → 个股诊断（与全站约定一致） */
function gotoDiag(code) {
  if (!code) return;
  sessionStorage.setItem('hermes.diagnosis.code', code);
  location.hash = '#/diagnosis';
}

/* 理想买入区间文案：任一端缺失也照实显示（不补全、不编） */
function bandText(it) {
  const lo = it.target_low, hi = it.target_high;
  const has = (v) => v !== null && v !== undefined && isFinite(Number(v));
  if (has(lo) && has(hi)) return `${fmtPrice(lo)} ~ ${fmtPrice(hi)}`;
  if (has(lo)) return `≥ ${fmtPrice(lo)}（无上沿）`;
  if (has(hi)) return `≤ ${fmtPrice(hi)}（无下沿）`;
  return '未设区间';
}

function zoneText(zone) {
  const has = (v) => v !== null && v !== undefined && isFinite(Number(v));
  if (has(zone?.low) && has(zone?.high)) return `${fmtPrice(zone.low)} ~ ${fmtPrice(zone.high)}`;
  const reasons = Array.isArray(zone?.missing) ? zone.missing.filter(Boolean) : [];
  return reasons[0] || '等待自动计算';
}

function zoneCard(label, zone) {
  const complete = zone && ['complete', 'degraded'].includes(zone.state) && zone.low != null && zone.high != null;
  const state = zone?.state === 'degraded' ? '同日最近结果' : (zone?.data_date || '数据不足');
  return `<div class="wl-auto-zone">
    <div class="zk"><span>${label}</span><span>${escapeHtml(state)}</span></div>
    <div class="zv ${complete ? '' : 'missing'}" title="${escapeHtml(zoneText(zone))}">${escapeHtml(zoneText(zone))}</div>
  </div>`;
}

function renderCard(it) {
  const g = it.grade;                         // 后端确定性评级：A–E 或 null
  const meta = gradeMeta(g);
  const q = it.quote || {};
  const price = q.price;                      // 真实现价（后端注入），缺失为 null
  const hasPrice = price !== null && price !== undefined && isFinite(Number(price));
  const status = it.status || (meta ? meta.name : (hasPrice ? '观察中' : '现价缺失'));
  const gColor = meta ? meta.color : 'var(--text-3)';
  const risk = Number(it.risk_level) || 3;
  const rkColor = RISK_COLOR[risk] || 'var(--flat)';
  const missing = Array.isArray(it.missing) ? it.missing : [];
  const zones = it.ideal_buy_zones || {};
  const sourceLabel = it.target_source === 'manual_override' ? '手工覆盖' : (it.target_source === 'auto_ultra' ? '自动·超短线' : '待计算');

  // 建议动作：优先后端给的 status/grade 推导文案；缺现价时给「等待真实行情」而非编造。
  const actTxt = meta ? meta.act : (hasPrice ? '继续观察，等条件明确' : '现价缺失，待行情恢复再判断，暂不动作');

  // 一行 label/value（value 空 → 灰「—」，绝不留空白假象）
  const row = (k, v) => {
    const empty = v === null || v === undefined || String(v).trim() === '';
    return `<div class="wl-row"><span class="rk">${k}</span><span class="rv ${empty ? 'muted' : ''}">${empty ? '—' : escapeHtml(String(v))}</span></div>`;
  };

  return `
  <div class="wl-card" data-id="${escapeHtml(it.id)}"
    style="--g-c:${gColor};--g-bg:color-mix(in srgb,${gColor} 9%,var(--panel));--g-line:color-mix(in srgb,${gColor} 30%,var(--border))">
    <div class="wl-head">
      <span class="wl-grade ${meta ? '' : 'na'}" title="${meta ? meta.name : '现价缺失，无法评级'}">${meta ? meta.label : 'N/A'}</span>
      <div class="wl-titlewrap">
        <div>
          <span class="wl-name" data-go="${escapeHtml(it.code)}">${escapeHtml(it.name || it.code || '未命名')}</span>
          <span class="wl-code" data-go="${escapeHtml(it.code)}">${escapeHtml(it.code || '')}</span>
        </div>
        <div class="wl-sub">
          <span class="wl-status">今日状态：${escapeHtml(status)}</span>
          ${it.sector ? `<span>· ${escapeHtml(it.sector)}</span>` : ''}
          ${missing.length ? `<span class="wl-missing" title="${escapeHtml(missing.join('、'))}">缺：${escapeHtml(missing.join('、'))}</span>` : ''}
        </div>
      </div>
      <div class="wl-acts">
        <button class="icon-btn wl-edit" title="编辑" style="width:28px;height:28px">${icon('edit')}</button>
        <button class="icon-btn wl-del" title="移出观察池" style="width:28px;height:28px">${icon('trash')}</button>
      </div>
    </div>

    <div class="wl-px">
      <div>
        <div class="pk">当前价格</div>
        <div>
          <span class="pv ${hasPrice ? '' : 'na'}">${hasPrice ? fmtPrice(price) : '现价缺失'}</span>
          ${hasPrice && q.change_pct != null ? `<span class="pchg ${pctClass(q.change_pct)}">${fmtPct(q.change_pct)}</span>` : ''}
        </div>
      </div>
      <div class="pband">
        <div class="bk">生效理想买入区间（${sourceLabel}）</div>
        <div class="bv">${bandText(it)}</div>
      </div>
    </div>

    <div class="wl-auto-zones">
      ${zoneCard('超短线 · 1—3日', zones.ultra)}
      ${zoneCard('波段 · 3—10日', zones.swing)}
    </div>

    <div class="wl-rows">
      ${row('加入理由', it.add_reason)}
      ${row('买入前提', it.trigger_condition)}
      ${row('不买条件', it.no_buy_condition)}
      ${row('放弃条件', it.abandon_price != null && isFinite(Number(it.abandon_price))
        ? `跌破 ${fmtPrice(it.abandon_price)} 元${it.abandon_condition ? '；' + it.abandon_condition : ''}`
        : (it.abandon_condition || ''))}
      ${it.note ? row('备注', it.note) : ''}
    </div>

    <div class="wl-act">
      <div class="at">建议动作</div>
      ${escapeHtml(actTxt)}
    </div>

    <div class="wl-foot">
      <span class="wl-risk" style="--rk-c:${rkColor}"><i></i>风险 ${risk} 级 · ${RISK_TXT[risk] || '中'}</span>
      ${it.created_at ? `<span>· 加入于 ${escapeHtml(String(it.created_at).slice(0, 16))}</span>` : ''}
    </div>
  </div>`;
}

function renderList() {
  if (!root) return;
  const host = root.querySelector('#wl-list');
  if (!items.length) {
    host.innerHTML = emptyState(
      '观察池是空的',
      '把「想买但还没到价」的票放进来。系统按理想买入区间盯住现价，到价才提示（A 接近买点 → E 建议移出），不到价不催你买。',
      `<button class="btn btn-sm btn-primary" id="wl-empty-add" style="margin-top:8px">${icon('plus')}添加第一只观察标的</button>`,
      'search',
    );
    host.querySelector('#wl-empty-add')?.addEventListener('click', () => openForm());
    return;
  }
  // 排序：grade A→E→无评级；同级按现价相对区间下沿的接近度无从稳定排，退化为加入时间倒序。
  const sorted = [...items].sort((a, b) => {
    const r = gradeRank(a.grade) - gradeRank(b.grade);
    if (r !== 0) return r;
    return String(b.created_at || '').localeCompare(String(a.created_at || ''));
  });
  host.innerHTML = `<div class="wl-grid">${sorted.map(renderCard).join('')}</div>`;
}

async function load(bg = false) {
  const g = gen;
  const host = root?.querySelector('#wl-list');
  if (!host) return;
  if (!bg && !items.length) host.innerHTML = skeleton(4);
  let d = null;
  try { d = await api.get('/watchlist'); } catch (e) {
    if (g !== gen || !root) return;
    if (bg) return;                            // 后台刷新失败：保留已渲染，不覆盖
    host.innerHTML = emptyState('观察池加载失败', e.message,
      `<button class="btn btn-sm" id="wl-retry" style="margin-top:6px">重试</button>`, 'alert');
    host.querySelector('#wl-retry')?.addEventListener('click', () => load());
    return;
  }
  if (g !== gen || !root) return;
  if (bg && (!d || d.ok === false)) return;
  if (!d || d.ok === false) {
    if (!items.length) host.innerHTML = emptyState('观察池加载失败', d?.error || '后端返回异常');
    return;
  }
  items = Array.isArray(d.items) ? d.items : [];
  const stamp = root.querySelector('#wl-stamp');
  if (stamp) stamp.innerHTML = items.length ? dataStamp(d.ts, d.missing) : '';
  renderList();
}

/* ========== 添加 / 编辑表单 ========== */
function num(v) { const s = String(v ?? '').trim(); if (s === '') return ''; const n = Number(s); return isFinite(n) ? n : NaN; }

function formBody(it = {}) {
  const v = (x) => (x === null || x === undefined ? '' : escapeHtml(String(x)));
  const hasManualFields = Object.prototype.hasOwnProperty.call(it, 'manual_target_low');
  const manualLow = hasManualFields ? it.manual_target_low : it.target_low;
  const manualHigh = hasManualFields ? it.manual_target_high : it.target_high;
  return `
  <div class="wl-form">
    <div class="ff">
      <label for="wf-code">股票代码 <span class="req">*</span></label>
      <input id="wf-code" maxlength="6" inputmode="numeric" autocomplete="off" required aria-required="true" aria-describedby="wf-code-hint wf-error" placeholder="000000" value="${v(it.code)}" ${it.id ? 'readonly style="opacity:.7;cursor:not-allowed"' : ''}>
      <span class="hint" id="wf-code-hint">请输入 6 位 A 股代码。</span>
    </div>
    <div class="ff">
      <label>名称</label>
      <input id="wf-name" maxlength="20" placeholder="可留空，自动按行情补" value="${v(it.name)}">
    </div>
    <div class="ff">
      <label>所属方向 / 板块</label>
      <input id="wf-sector" maxlength="30" placeholder="如：AI算力 / 创新药" value="${v(it.sector)}">
    </div>
    <div class="ff">
      <label>风险等级</label>
      <select id="wf-risk">
        ${[1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${Number(it.risk_level || 3) === n ? 'selected' : ''}>${n} 级 · ${RISK_TXT[n]}</option>`).join('')}
      </select>
    </div>
    <div class="ff full">
      <label>加入理由</label>
      <textarea id="wf-reason" maxlength="500" placeholder="为什么把它放进观察池（逻辑/催化/估值…）">${v(it.add_reason)}</textarea>
    </div>
    <div class="ff full">
      <label>理想买入区间（元）</label>
      <div class="wl-band2">
        <input id="wf-low" inputmode="decimal" placeholder="下沿 如 10.50" value="${v(manualLow)}">
        <span>~</span>
        <input id="wf-high" inputmode="decimal" placeholder="上沿 如 11.80" value="${v(manualHigh)}">
      </div>
      <span class="hint">留空则使用系统每 5 分钟计算的超短线区间评级；填写任一端即启用手工覆盖。波段区间始终单独展示。</span>
    </div>
    <div class="ff full">
      <label>买入触发条件</label>
      <textarea id="wf-trigger" maxlength="500" placeholder="满足什么才真买，如：回踩区间且缩量企稳 / 放量突破平台">${v(it.trigger_condition)}</textarea>
    </div>
    <div class="ff full">
      <label>不买条件</label>
      <textarea id="wf-nobuy" maxlength="500" placeholder="出现什么就不买，如：高开追高 / 逻辑被证伪 / 大盘极端">${v(it.no_buy_condition)}</textarea>
    </div>
    <div class="ff">
      <label>放弃价（元，可选）</label>
      <input id="wf-abandon-price" inputmode="decimal" placeholder="跌破即移出 如 9.20" value="${v(it.abandon_price)}">
      <span class="hint">现价跌破此价 → 评 E（建议移出）。</span>
    </div>
    <div class="ff">
      <label>放弃条件（文本）</label>
      <input id="wf-abandon-cond" maxlength="300" placeholder="如：周线破位 / 基本面变差" value="${v(it.abandon_condition)}">
    </div>
    <div class="ff full">
      <label>备注</label>
      <input id="wf-note" maxlength="300" placeholder="其他备注" value="${v(it.note)}">
    </div>
    <div class="ff full wl-form-error" id="wf-error" role="alert" aria-live="assertive"></div>
  </div>`;
}

/* 把表单收集为 payload；区间/放弃价用 num()，非法 NaN 提前拦下报错（铁律：宁缺勿造） */
function collect(modal) {
  const get = (id) => modal.querySelector('#' + id);
  const error = get('wf-error');
  const fail = (message, field = null) => {
    if (error) error.textContent = message;
    if (field) {
      field.setAttribute('aria-invalid', 'true');
      field.focus();
    }
    toast(message, 'error');
    return null;
  };
  if (error) error.textContent = '';
  modal.querySelectorAll('[aria-invalid="true"]').forEach((field) => field.removeAttribute('aria-invalid'));
  const code = (get('wf-code').value || '').trim();
  if (!/^\d{6}$/.test(code)) return fail('股票代码应为 6 位数字。', get('wf-code'));
  const lo = num(get('wf-low').value);
  const hi = num(get('wf-high').value);
  const ab = num(get('wf-abandon-price').value);
  if (Number.isNaN(lo) || Number.isNaN(hi) || Number.isNaN(ab)) {
    return fail('买入区间 / 放弃价必须是数字（或留空）。');
  }
  if (lo !== '' && hi !== '' && lo > hi) return fail('区间下沿不能高于上沿。', get('wf-low'));
  return {
    code,
    name: (get('wf-name').value || '').trim(),
    sector: (get('wf-sector').value || '').trim(),
    add_reason: (get('wf-reason').value || '').trim(),
    target_low: lo === '' ? null : lo,
    target_high: hi === '' ? null : hi,
    trigger_condition: (get('wf-trigger').value || '').trim(),
    no_buy_condition: (get('wf-nobuy').value || '').trim(),
    abandon_condition: (get('wf-abandon-cond').value || '').trim(),
    abandon_price: ab === '' ? null : ab,
    risk_level: Number(get('wf-risk').value) || 3,
    note: (get('wf-note').value || '').trim(),
  };
}

function openForm(existing = null) {
  openModal({
    title: existing ? '编辑观察标的' : '添加观察标的',
    body: formBody(existing || {}),
    footer: `
      <button class="btn btn-ghost" data-act="cancel">取消</button>
      <button class="btn btn-primary" data-act="save">${icon('save')}${existing ? '保存修改' : '加入观察池'}</button>`,
    onMount(modal) {
      modal.querySelector('[data-act="cancel"]').addEventListener('click', () => closeModal());
      modal.querySelector('#wf-code')?.addEventListener('input', (e) => {
        e.target.removeAttribute('aria-invalid');
        const error = modal.querySelector('#wf-error');
        if (error) error.textContent = '';
      });
      const saveBtn = modal.querySelector('[data-act="save"]');
      saveBtn.addEventListener('click', async () => {
        const payload = collect(modal);
        if (!payload) return;
        saveBtn.disabled = true;
        try {
          let r;
          if (existing && existing.id) r = await api.put(`/watchlist/${existing.id}`, payload);
          else r = await api.post('/watchlist', payload);
          if (r && r.ok !== false) {
            toast(existing ? '已更新' : '已加入观察池', 'success');
            closeModal();
            await load(true);
          } else {
            toast('保存失败：' + (r?.error || '未知错误'), 'error');
            saveBtn.disabled = false;
          }
        } catch (e) {
          toast('保存失败：' + e.message, 'error');
          saveBtn.disabled = false;
        }
      });
    },
  });
}

async function doDelete(it) {
  const ok = await confirmModal('移出观察池', `确定把「${it.name || it.code}」移出观察池吗？此操作不可撤销。`, { danger: true, okText: '移出' });
  if (!ok) return;
  try {
    const r = await api.del(`/watchlist/${it.id}`);
    if (r && r.ok !== false) { toast('已移出观察池', 'success'); items = items.filter((x) => x.id !== it.id); renderList(); }
    else toast('移除失败：' + (r?.error || '条目不存在'), 'error');
  } catch (e) { toast('移除失败：' + e.message, 'error'); }
}

export function mount(el) {
  gen += 1;
  root = el;
  items = [];
  injectStyle();
  el.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">观察池</h2>
      <span class="page-sub">双周期纯数字区间 · 超短线自动评级 · 手工区间可覆盖</span>
      <div class="ph-extra">
        <span id="wl-stamp"></span>
        <button class="btn btn-sm" id="wl-refresh">${icon('refresh')}刷新现价</button>
        <button class="btn btn-sm" id="wl-recalc">重算区间</button>
        <button class="btn btn-primary" id="wl-add">${icon('plus')}添加观察</button>
      </div>
    </div>
    <div id="wl-list"></div>`;

  el.querySelector('#wl-add').addEventListener('click', () => openForm());
  el.querySelector('#wl-refresh').addEventListener('click', async () => {
    await load();
    toast('现价已刷新', 'success');
  });
  el.querySelector('#wl-recalc').addEventListener('click', async () => {
    try {
      const result = await api.post('/watchlist/recalculate', {});
      if (result?.ok === false) throw new Error(result.error || '触发失败');
      toast('双周期区间已进入后台重算', 'success');
    } catch (e) {
      toast('重算失败：' + e.message, 'error');
    }
  });

  // 卡片内事件委托：编辑 / 删除 / 点代码跳诊断
  el.querySelector('#wl-list').addEventListener('click', (e) => {
    const go = e.target.closest('[data-go]');
    if (go) { gotoDiag(go.dataset.go); return; }
    const card = e.target.closest('.wl-card');
    if (!card) return;
    const it = items.find((x) => x.id === card.dataset.id);
    if (!it) return;
    if (e.target.closest('.wl-edit')) { openForm(it); return; }
    if (e.target.closest('.wl-del')) { doDelete(it); return; }
  });

  load();

  // 交易时段每 30s 后台刷新现价（失败/空不覆盖；评级由后端随现价重算）。
  // 非交易时段不自动刷新（避免无谓拉取），用户仍可手动「刷新现价」。
  timer = setInterval(() => { if (isTradingTime()) load(true); }, 30000);
}

export function unmount() {
  if (timer) { clearInterval(timer); timer = null; }
  closeModal();   // 切走时关掉可能开着的"添加观察"弹窗（否则残留盖在新页面 + Esc 监听泄漏）
  root = null;
  items = [];
}
