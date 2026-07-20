/* views/screener.js — 条件 / 多因子选股
   条件表单（PE/换手/量比/流通市值/涨跌幅/成交额/板块/行业）→ /api/screen 全市场筛选
   → 结果表（可点代码跳「盘中问股」）。市值/成交额输入单位为【亿元】，与后端约定一致。 */

import { api, fmtMoney, fmtPct, fmtNum, fmtPrice, pctClass } from '../api.js';
import { dataStamp, skeleton, escapeHtml, toast } from '../ui.js';

let root = null;
let loading = false;
// 结果缓存：不仅供本地重排，还【跨视图切换/页面刷新持久】——切走再回来、定时刷新都不清空（客户#3）。
let lastStocks = [];
let lastMeta = null;
let lastFilters = null;   // 上次选股的筛选条件（回来时回填表单）
let lastSort = null;      // {by, desc}
let advancedOpen = false;
let activePresetId = '';
let resultStale = false;
let runController = null;
let progressTimer = null;

const SC_KEY = 'hermes.screener.state';
function saveState() {
  try { sessionStorage.setItem(SC_KEY, JSON.stringify({ stocks: lastStocks, meta: lastMeta, filters: lastFilters, sort: lastSort })); } catch { /* 配额满忽略 */ }
}
function loadState() {
  if (lastStocks.length) return;   // 模块态还在(切换回来)就不必读 sessionStorage
  try {
    const s = JSON.parse(sessionStorage.getItem(SC_KEY) || 'null');
    if (s && Array.isArray(s.stocks)) { lastStocks = s.stocks; lastMeta = s.meta || null; lastFilters = s.filters || null; lastSort = s.sort || null; }
  } catch { /* 坏数据忽略 */ }
}

const BOARDS = ['主板', '创业板', '科创板', '北交所'];

// 排序选项（与后端 _SORT_FIELDS 白名单一致）
const SORT_OPTS = [
  { v: 'amount', label: '成交额' },
  { v: 'turnover_rate', label: '换手率' },
  { v: 'volume_ratio', label: '量比' },
  { v: 'change_pct', label: '涨跌幅' },
  { v: 'circ_mv', label: '流通市值' },
  { v: 'pe_ttm', label: '市盈率TTM' },
  { v: 'pb', label: '市净率' },
];

// 所有预设只使用 collectFilters 已支持的真实字段；点击后回填表单，不偷偷追加后端条件。
const STRATEGY_PRESETS = [
  {
    id: 'active_volume', label: '活跃放量', desc: '放量且成交活跃，排除极端换手',
    filters: { turnover_min: 3, turnover_max: 18, volume_ratio_min: 1.5, amount_min: 5, chg_min: -2, chg_max: 7, exclude_st: true, exclude_loss: false },
    sort: { by: 'amount', desc: true },
  },
  {
    id: 'value_capacity', label: '低估值容量', desc: '正盈利、低 PE/PB 与中大市值',
    filters: { pe_min: 0, pe_max: 25, pb_min: 0, pb_max: 3, circ_mv_min: 100, amount_min: 3, exclude_st: true, exclude_loss: true },
    sort: { by: 'circ_mv', desc: true },
  },
  {
    id: 'small_cap', label: '小盘弹性', desc: '20–150 亿流通市值，兼顾量能',
    filters: { turnover_min: 4, turnover_max: 20, volume_ratio_min: 1.2, circ_mv_min: 20, circ_mv_max: 150, amount_min: 2, exclude_st: true, exclude_loss: false },
    sort: { by: 'turnover_rate', desc: true },
  },
  {
    id: 'trend_not_limit', label: '趋势强势', desc: '上涨但不追板，量价同步确认',
    filters: { turnover_min: 2, turnover_max: 15, volume_ratio_min: 1.2, chg_min: 2, chg_max: 8, amount_min: 5, exclude_st: true, exclude_loss: false },
    sort: { by: 'change_pct', desc: true },
  },
  {
    id: 'pullback', label: '回调承接', desc: '温和回撤且保持成交与换手',
    filters: { turnover_min: 1, turnover_max: 10, volume_ratio_min: 1, chg_min: -4, chg_max: 0, amount_min: 5, exclude_st: true, exclude_loss: false },
    sort: { by: 'amount', desc: true },
  },
];

const num = (v) => {
  const s = String(v ?? '').trim();
  if (s === '') return undefined;
  const n = Number(s);
  return Number.isFinite(n) ? n : undefined;
};

/* ---------------- 表单 → filters ---------------- */

function collectFilters() {
  const q = (id) => root.querySelector(id);
  const f = {};
  const put = (k, v) => { if (v !== undefined) f[k] = v; };

  put('pe_min', num(q('#sc-pe-min').value));
  put('pe_max', num(q('#sc-pe-max').value));
  put('pb_min', num(q('#sc-pb-min').value));
  put('pb_max', num(q('#sc-pb-max').value));
  put('turnover_min', num(q('#sc-tr-min').value));
  put('turnover_max', num(q('#sc-tr-max').value));
  put('volume_ratio_min', num(q('#sc-vr-min').value));
  put('circ_mv_min', num(q('#sc-mv-min').value));   // 亿元
  put('circ_mv_max', num(q('#sc-mv-max').value));   // 亿元
  put('chg_min', num(q('#sc-chg-min').value));
  put('chg_max', num(q('#sc-chg-max').value));
  put('amount_min', num(q('#sc-amt-min').value));   // 亿元

  const kw = q('#sc-ind').value.trim();
  if (kw) f.industry_kw = kw;

  const boards = Array.from(root.querySelectorAll('.sc-board:checked')).map((c) => c.value);
  if (boards.length && boards.length < BOARDS.length) f.boards = boards; // 全选=不限制

  f.exclude_st = q('#sc-ex-st').checked;
  f.exclude_loss = q('#sc-ex-loss').checked;
  return f;
}

function syncPresetUI() {
  root?.querySelectorAll('.sc-preset').forEach((btn) => {
    const active = btn.dataset.preset === activePresetId;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
  });
}

function setAdvancedOpen(open) {
  advancedOpen = !!open;
  const panel = root?.querySelector('#sc-advanced');
  const btn = root?.querySelector('#sc-advanced-toggle');
  if (!panel || !btn) return;
  panel.hidden = !advancedOpen;
  btn.setAttribute('aria-expanded', String(advancedOpen));
  btn.setAttribute('aria-label', advancedOpen ? '收起高级筛选条件' : '展开高级筛选条件');
  btn.querySelector('.sc-advanced-state').textContent = advancedOpen ? '收起' : '展开';
  btn.querySelector('.sc-advanced-chevron').textContent = advancedOpen ? '−' : '+';
}

function resetForm() {
  root.querySelectorAll('.sc-input').forEach((i) => { i.value = ''; });
  root.querySelectorAll('.sc-board').forEach((c) => { c.checked = true; });
  root.querySelector('#sc-ex-st').checked = true;
  root.querySelector('#sc-ex-loss').checked = false;
  root.querySelector('#sc-sort').value = 'amount';
  root.querySelector('#sc-dir').value = 'desc';
  activePresetId = '';
  if (lastMeta) resultStale = true;
  syncPresetUI();
  setAdvancedOpen(false);
  if (lastMeta) renderResult();
}

function applyPreset(id) {
  const preset = STRATEGY_PRESETS.find((item) => item.id === id);
  if (!preset) return;
  resetForm();
  restoreForm(preset.filters, preset.sort);
  activePresetId = preset.id;
  if (lastMeta) resultStale = true;
  syncPresetUI();
  const hasAdvanced = ['pe_min', 'pe_max', 'pb_min', 'pb_max', 'industry_kw', 'boards']
    .some((key) => preset.filters[key] !== undefined)
    || preset.filters.exclude_loss === true;
  setAdvancedOpen(hasAdvanced);
  if (lastMeta) renderResult();
}

function compactResultEmpty(title, detail, gaps = []) {
  const cleanGaps = [...new Set((gaps || []).filter(Boolean).map((x) => String(x).trim()).filter(Boolean))];
  return `<div class="sc-empty" role="status">
    <div class="sc-empty-copy"><b>${escapeHtml(title)}</b><span>${escapeHtml(detail)}</span></div>
    <div class="sc-empty-gap ${cleanGaps.length ? 'has-gap' : ''}">
      ${cleanGaps.length
        ? `数据缺口：${cleanGaps.map((x) => escapeHtml(x)).join('；')}`
        : '数据缺口：当前接口未报告缺失项'}
    </div>
  </div>`;
}

/* ---------------- 运行选股 ---------------- */

async function run() {
  if (loading) return;
  const body = root.querySelector('#sc-result');
  const btn = root.querySelector('#sc-run');
  const filters = collectFilters();
  const sortBy = root.querySelector('#sc-sort').value;
  const sortDesc = root.querySelector('#sc-dir').value !== 'asc';

  loading = true;
  runController = new AbortController();
  btn.disabled = true;
  btn.classList.add('btn-spin');
  btn.setAttribute('aria-busy', 'true');
  body.setAttribute('aria-busy', 'true');
  const startedAt = Date.now();
  const progressHtml = () => {
    const elapsed = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    const stage = elapsed < 4 ? '正在读取全 A 股快照'
      : elapsed < 10 ? '正在补齐估值、量能与行业字段'
        : '正在执行筛选与结果校验';
    return `<div class="sc-progress" id="sc-progress" role="status" aria-live="polite">
      <div><b>${stage}</b><span>已等待 ${elapsed} 秒 · 最多等待 30 秒，超时会自动停止</span></div>
      <button type="button" class="btn btn-sm" id="sc-cancel">停止本次</button>
    </div>`;
  };
  if (lastMeta) {
    renderResult();
    body.insertAdjacentHTML('afterbegin', progressHtml());
  } else {
    body.innerHTML = progressHtml() + skeleton(8);
  }
  const bindCancel = () => body.querySelector('#sc-cancel')?.addEventListener('click', () => runController?.abort());
  bindCancel();
  progressTimer = setInterval(() => {
    const old = body.querySelector('#sc-progress');
    if (!old) return;
    old.outerHTML = progressHtml();
    bindCancel();
  }, 1000);

  let d = null;
  try {
    d = await api.post(
      '/screen',
      { filters, sort_by: sortBy, sort_desc: sortDesc, result_limit: 300 },
      { timeoutMs: 30000, signal: runController.signal },
    );
  } catch (e) {
    if (root) {
      const cancelled = e?.name === 'AbortError';
      if (lastMeta) {
        renderResult();
        body.insertAdjacentHTML('afterbegin', compactResultEmpty(
          cancelled ? '已停止本次选股' : '新一轮选股未完成',
          cancelled ? '下方保留上次成功结果。' : (e.message || '请稍后重试') + '；下方保留上次成功结果。',
          cancelled ? [] : ['未取得新的全市场筛选结果'],
        ));
      } else {
        body.innerHTML = compactResultEmpty(
          cancelled ? '已停止本次选股' : '选股请求失败',
          cancelled ? '未产生结果，可调整条件后重试。' : e.message || '请稍后重试',
          cancelled ? [] : ['未取得全市场股票池或筛选指标'],
        );
      }
    }
    clearInterval(progressTimer); progressTimer = null; runController = null;
    loading = false; btn.disabled = false; btn.classList.remove('btn-spin');
    btn.setAttribute('aria-busy', 'false'); body.setAttribute('aria-busy', 'false');
    return;
  }
  clearInterval(progressTimer); progressTimer = null; runController = null;
  if (!root) { loading = false; return; }
  loading = false;
  btn.disabled = false;
  btn.classList.remove('btn-spin');
  btn.setAttribute('aria-busy', 'false');
  body.setAttribute('aria-busy', 'false');

  if (!d || d.ok === false) {
    const gaps = Array.isArray(d?.missing) ? d.missing : [];
    body.innerHTML = compactResultEmpty('选股不可用', d?.error || '免费公开数据源暂不可用，请稍后重试', gaps.length ? gaps : ['未取得全市场股票池或筛选指标']);
    return;
  }
  const returnedStocks = Array.isArray(d.stocks) ? d.stocks : [];
  if (filters.exclude_loss) {
    const invalid = returnedStocks.filter((row) => {
      const pe = Number(row?.pe_ttm ?? row?.pe);
      return !Number.isFinite(pe) || pe <= 0;
    });
    if (invalid.length) {
      body.innerHTML = compactResultEmpty(
        '选股结果未通过规则校验',
        `已要求剔除亏损股，但接口返回 ${invalid.length} 只 PE 非正或缺失的股票；本次结果已阻止展示。`,
        ['筛选条件与返回结果不一致']
      );
      return;
    }
  }
  lastStocks = returnedStocks;
  lastMeta = d;
  lastFilters = filters;
  lastSort = { by: sortBy, desc: sortDesc };
  resultStale = false;
  saveState();          // 持久化：切走再回来 / 刷新都还在（客户#3）
  renderResult();
}

function renderResult() {
  const body = root?.querySelector('#sc-result');
  if (!body || !lastMeta) return;
  const d = lastMeta;
  const stamp = root.querySelector('#sc-stamp');
  if (stamp) stamp.innerHTML = dataStamp(d.trade_date || d.ts, d.missing);

  const universe = d.universe || {};
  const missingExchanges = Array.isArray(universe.missing_exchanges) ? universe.missing_exchanges : [];
  const summary = `<div class="sc-summary">
    ${resultStale ? '<span class="sc-stale" role="status">筛选条件已变化，下面仍是上次结果；请重新点击“开始选股”</span>' : ''}
    当前可用股票池 <b class="num">${fmtNum(d.total_market)}</b> 只 · 命中
    <b class="num" style="color:var(--brand1)">${fmtNum(d.matched)}</b> 只
    ${d.returned < d.matched ? `（按排序展示前 <b class="num">${fmtNum(d.returned)}</b> 只）` : ''}
    · 数据日 <span class="num">${escapeHtml(d.trade_date || '--')}</span>
    ${missingExchanges.length ? `<span class="sc-miss">· 缺交易所 ${missingExchanges.map((x) => escapeHtml(String(x).toUpperCase())).join('/')}</span>` : ''}
    ${Array.isArray(d.missing) && d.missing.length ? `<span class="sc-miss" title="${escapeHtml(d.missing.join('；'))}">· 部分口径降级</span>` : ''}
    ${lastFilters?.exclude_loss ? '<span class="sc-applied">· 已执行：剔除亏损股（PE≤0或缺失）</span>' : ''}
  </div>`;

  if (!lastStocks.length) {
    const gaps = [
      ...(Array.isArray(d.missing) ? d.missing : []),
      ...missingExchanges.map((x) => `${String(x).toUpperCase()} 交易所股票池缺失`),
    ];
    const detail = gaps.length
      ? '当前结果可能受股票池或指标缺失影响；先查看缺口，再决定是否放宽条件。'
      : '可用股票池完整但没有命中；可降低换手/量比下限，或放宽市值与涨跌幅区间。';
    body.innerHTML = summary + compactResultEmpty('没有符合条件的股票', detail, gaps);
    return;
  }

  body.innerHTML = summary + `<div class="tbl-wrap" role="region" aria-label="选股结果表，可横向滚动" tabindex="0"><table class="tbl">
    <thead><tr>
      <th>#</th><th class="l" style="width:78px">代码</th><th class="l">名称</th><th class="l">行业</th>
      <th>现价</th><th>涨跌幅</th><th>换手</th><th>量比</th><th>PE(TTM)</th><th>PB</th>
      <th>流通市值</th><th>成交额</th><th>板块</th><th style="width:72px">操作</th>
    </tr></thead>
    <tbody>${lastStocks.map((s, i) => `<tr>
      <td style="color:var(--text-3)">${i + 1}</td>
      <td class="l"><button type="button" class="code-link" data-code="${escapeHtml(s.code)}" aria-label="分析股票 ${escapeHtml(s.code)} ${escapeHtml(s.name || '')}">${escapeHtml(s.code)}</button></td>
      <td class="l cn" style="font-weight:600">${escapeHtml(s.name || '--')}</td>
      <td class="l" style="color:var(--text-2)">${escapeHtml(s.industry || '--')}</td>
      <td>${fmtPrice(s.price)}</td>
      <td><span class="chg-pill ${pctClass(s.change_pct)}">${fmtPct(s.change_pct)}</span></td>
      <td>${s.turnover_rate != null ? fmtNum(s.turnover_rate, 1) + '%' : '--'}</td>
      <td>${s.volume_ratio != null ? fmtNum(s.volume_ratio, 2) : '--'}</td>
      <td>${s.pe_ttm != null ? fmtNum(s.pe_ttm, 1) : '--'}</td>
      <td>${s.pb != null ? fmtNum(s.pb, 2) : '--'}</td>
      <td>${fmtMoney(s.circ_mv, 1)}</td>
      <td>${fmtMoney(s.amount, 1)}</td>
      <td style="color:var(--text-3)">${escapeHtml(s.board || '--')}</td>
      <td><button type="button" class="btn btn-sm sc-add-wl" data-code="${escapeHtml(s.code)}" data-name="${escapeHtml(s.name || '')}" data-sector="${escapeHtml(s.industry || '')}" title="加入观察池，之后可在观察池补买入区间" aria-label="将 ${escapeHtml(s.name || s.code)} 加入观察池" style="white-space:nowrap;font-size:12px;padding:3px 9px">+ 观察</button></td>
    </tr>`).join('')}</tbody></table></div>
    <div style="font-size:15px;color:var(--text-3);margin-top:8px">点击代码会立即执行统一「个股诊断」，完成确定性计算后可按需让 AI 解释 · 「+ 观察」一键加入观察池 · 市值/成交额均为元口径</div>`;

  body.querySelectorAll('.code-link').forEach((a) => {
    a.addEventListener('click', () => {
      const code = a.dataset.code;
      if (!code) return;
      sessionStorage.setItem('hermes.diagnosis.code', code);
      location.hash = '#/diagnosis';
    });
  });

  // 客户#4：一键加入观察池（买入区间留空，之后在观察池页补；加成功即置"已加入"防重复）
  body.querySelectorAll('.sc-add-wl').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        const r = await api.post('/watchlist', {
          code: btn.dataset.code, name: btn.dataset.name || '',
          sector: btn.dataset.sector || '', add_reason: '条件选股命中',
        });
        if (r && r.ok !== false) { btn.textContent = '已加入'; btn.classList.remove('btn'); toast('已加入观察池', 'success'); }
        else { btn.disabled = false; toast('加入失败：' + (r?.error || '未知错误'), 'error'); }
      } catch (e) { btn.disabled = false; toast('加入失败：' + e.message, 'error'); }
    });
  });
}

/* ---------------- 范围输入小组件 ---------------- */

function rangeField(label, idMin, idMax, ph1, ph2, unit) {
  return `<div class="field sc-field">
    <label class="field-label">${label}${unit ? `<span class="sc-unit">${unit}</span>` : ''}</label>
    <div class="sc-range">
      <input class="input sc-input" id="${idMin}" inputmode="decimal" placeholder="${ph1}" aria-label="${label}最小值${unit ? `，单位${unit}` : ''}">
      <span class="sc-dash">~</span>
      <input class="input sc-input" id="${idMax}" inputmode="decimal" placeholder="${ph2}" aria-label="${label}最大值${unit ? `，单位${unit}` : ''}">
    </div>
  </div>`;
}

function minField(label, id, ph, unit) {
  return `<div class="field sc-field">
    <label class="field-label" for="${id}">${label}${unit ? `<span class="sc-unit">${unit}</span>` : ''}</label>
    <input class="input sc-input" id="${id}" inputmode="decimal" placeholder="${ph}" aria-label="${label}${unit ? `，单位${unit}` : ''}" style="width:100%">
  </div>`;
}

/* ---------------- 挂载 ---------------- */

export function mount(container) {
  root = container;
  loading = false;
  advancedOpen = false;
  activePresetId = '';
  loadState();     // 恢复上次结果（客户#3：切走再回来/刷新不丢）

  container.innerHTML = `
    <style>
      #screener-phase5{min-width:0;max-width:100%;overflow-x:hidden}
      #screener-phase5 .sc-filter-card,#screener-phase5 .sc-result-card,#screener-phase5 .card-body{min-width:0}
      #screener-phase5 .sc-presets{display:flex;gap:9px;overflow-x:auto;overscroll-behavior-x:contain;padding:2px 1px 9px;scrollbar-width:thin}
      #screener-phase5 .sc-preset{flex:0 0 188px;min-height:70px;padding:11px 12px;text-align:left;border:1px solid var(--border);border-radius:10px;background:var(--panel-2);color:var(--text);cursor:pointer}
      #screener-phase5 .sc-preset:hover{border-color:var(--border-strong);background:var(--hover)}
      #screener-phase5 .sc-preset.active{border-color:var(--amber-line);background:var(--amber-dim);box-shadow:inset 3px 0 0 var(--amber)}
      #screener-phase5 .sc-preset b{display:block;font-size:14px;margin-bottom:4px}
      #screener-phase5 .sc-preset span{display:block;color:var(--text-3);font-size:12px;line-height:1.45}
      #screener-phase5 .sc-section-label{font-size:12px;color:var(--text-3);letter-spacing:.08em;margin:0 0 8px}
      #screener-phase5 .sc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px 18px}
      #screener-phase5 .sc-field{min-width:0}
      #screener-phase5 .sc-field .field-label{display:flex;align-items:center;gap:6px}
      #screener-phase5 .sc-unit{font-size:12px;color:var(--text-3);font-weight:400}
      #screener-phase5 .sc-range{display:flex;align-items:center;gap:7px;min-width:0}
      #screener-phase5 .sc-range .input{width:100%;min-width:0}
      #screener-phase5 .sc-dash{color:var(--text-3);flex:0 0 auto}
      #screener-phase5 .sc-advanced-toggle{width:100%;display:flex;align-items:center;gap:9px;margin-top:16px;padding:10px 12px;border:1px solid var(--border);border-radius:9px;background:var(--panel-2);color:var(--text-2);cursor:pointer;font:inherit;text-align:left}
      #screener-phase5 .sc-advanced-toggle:hover{border-color:var(--border-strong)}
      #screener-phase5 .sc-advanced-toggle .sc-advanced-state{margin-left:auto;color:var(--text-3);font-size:12px}
      #screener-phase5 .sc-advanced-chevron{width:20px;height:20px;display:grid;place-items:center;border:1px solid var(--border);border-radius:6px;color:var(--amber)}
      #screener-phase5 .sc-advanced{margin-top:10px;padding:14px;border:1px solid var(--border);border-radius:10px;background:color-mix(in srgb,var(--panel-2) 76%,transparent)}
      #screener-phase5 .sc-advanced[hidden]{display:none}
      #screener-phase5 .sc-boards{display:flex;flex-wrap:wrap;gap:13px;align-items:center}
      #screener-phase5 .sc-chk{display:inline-flex;align-items:center;gap:6px;font-size:14px;color:var(--text-2);cursor:pointer;user-select:none}
      #screener-phase5 .sc-chk input{accent-color:var(--brand1);width:15px;height:15px}
      #screener-phase5 .sc-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:18px;padding-top:14px;border-top:1px solid var(--border)}
      #screener-phase5 #sc-run{min-width:132px;min-height:42px;font-weight:800}
      #screener-phase5 #sc-reset{border:0;background:transparent;color:var(--text-3);box-shadow:none;padding-inline:8px}
      #screener-phase5 #sc-reset:hover{color:var(--text);background:var(--hover)}
      #screener-phase5 .sc-sort-wrap{display:flex;align-items:center;gap:7px;margin-left:auto;min-width:0}
      #screener-phase5 .sc-sort-wrap .select{padding:6px 10px;font-size:14px;min-width:0}
      #screener-phase5 .sc-summary{font-size:14px;color:var(--text-2);margin-bottom:10px;overflow-wrap:anywhere}
      #screener-phase5 .sc-stale{display:block;margin-bottom:8px;padding:7px 9px;border-left:3px solid var(--amber);background:var(--amber-dim);color:var(--text);font-weight:700}
      #screener-phase5 .sc-applied{color:var(--up)}
      #screener-phase5 .sc-miss{color:var(--amber);margin-left:4px;cursor:help}
      #screener-phase5 .sc-empty{display:flex;align-items:center;gap:14px;padding:12px 14px;border:1px dashed var(--border);border-radius:9px;background:var(--panel-2);min-width:0}
      #screener-phase5 .sc-empty-copy{display:flex;flex-direction:column;gap:3px;min-width:0;flex:1}
      #screener-phase5 .sc-empty-copy b{font-size:14px;color:var(--text)}
      #screener-phase5 .sc-empty-copy span{font-size:13px;color:var(--text-3);line-height:1.5}
      #screener-phase5 .sc-empty-gap{flex:0 1 42%;font-size:12px;color:var(--text-3);line-height:1.5;text-align:right;overflow-wrap:anywhere}
      #screener-phase5 .sc-empty-gap.has-gap{color:var(--amber)}
      #screener-phase5 .sc-progress{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:11px 13px;margin-bottom:12px;border:1px solid var(--amber-line);border-radius:9px;background:var(--amber-dim);color:var(--text-2)}
      #screener-phase5 .sc-progress b{display:block;color:var(--text);font-size:13px}.sc-progress span{display:block;color:var(--text-3);font-size:12px;line-height:1.5}
      #screener-phase5 .tbl-wrap{max-width:100%;overflow-x:auto;overscroll-behavior-x:contain;-webkit-overflow-scrolling:touch}
      #screener-phase5 .tbl{min-width:1120px}
      #screener-phase5 .tbl .code-link{appearance:none;border:0;background:transparent;padding:0}
      #screener-phase5 .sc-preset:focus-visible,#screener-phase5 .sc-advanced-toggle:focus-visible,#screener-phase5 .tbl-wrap:focus-visible{outline:2px solid var(--amber);outline-offset:2px}
      @media(max-width:720px){
        #screener-phase5 .page-head{align-items:flex-start}
        #screener-phase5 .card-head{align-items:flex-start}
        #screener-phase5 .card-extra{width:100%;margin-left:0}
        #screener-phase5 .sc-grid{grid-template-columns:minmax(0,1fr)}
        #screener-phase5 .sc-preset{flex-basis:172px}
        #screener-phase5 .sc-actions{align-items:stretch}
        #screener-phase5 #sc-run{width:100%}
        #screener-phase5 #sc-reset{order:3;margin-right:auto}
        #screener-phase5 .sc-sort-wrap{width:100%;margin-left:0;display:grid;grid-template-columns:auto minmax(0,1fr) minmax(0,1fr)}
        #screener-phase5 .sc-sort-wrap .select{width:100%}
        #screener-phase5 .sc-empty{align-items:flex-start;flex-direction:column;gap:7px}
        #screener-phase5 .sc-empty-gap{max-width:100%;text-align:left}
        #screener-phase5 .sc-progress{align-items:flex-start;flex-direction:column}
        #screener-phase5 .sc-progress .btn{width:100%}
      }
    </style>
    <div id="screener-phase5">
    <div class="page-head">
      <h2 class="page-title">条件选股</h2>
      <span class="page-sub">全市场多因子筛选 · 免费公开行情与每日指标 · 最多展示前 300 只</span>
      <div class="ph-extra"><span class="data-stamp" id="sc-stamp"></span></div>
    </div>

    <section class="card sc-filter-card" style="margin-bottom:var(--gap)">
      <div class="card-head"><h3 class="card-title">筛选条件</h3>
        <div class="card-extra"><span style="font-size:13px;color:var(--text-3)">市值 / 成交额单位：亿元 · 留空即不限制该项</span></div>
      </div>
      <div class="card-body">
        <div class="sc-section-label">策略预设 · 点击后会真实回填下方条件，可继续修改</div>
        <div class="sc-presets" role="group" aria-label="筛选策略预设">
          ${STRATEGY_PRESETS.map((p) => `<button type="button" class="sc-preset" data-preset="${p.id}" aria-pressed="false" aria-label="使用${p.label}策略：${p.desc}"><b>${p.label}</b><span>${p.desc}</span></button>`).join('')}
        </div>

        <div class="sc-section-label" style="margin-top:8px">常用条件</div>
        <div class="sc-grid">
          ${rangeField('换手率', 'sc-tr-min', 'sc-tr-max', '最小', '最大', '%')}
          ${minField('量比 ≥', 'sc-vr-min', '如 1.5', '倍')}
          ${rangeField('流通市值', 'sc-mv-min', 'sc-mv-max', '最小', '最大', '亿')}
          ${rangeField('当日涨跌幅', 'sc-chg-min', 'sc-chg-max', '最小', '最大', '%')}
          ${minField('成交额 ≥', 'sc-amt-min', '如 3', '亿')}
        </div>

        <button type="button" class="sc-advanced-toggle" id="sc-advanced-toggle" aria-expanded="false" aria-controls="sc-advanced" aria-label="展开高级筛选条件">
          <span class="sc-advanced-chevron" aria-hidden="true">+</span>
          <span><b>高级条件</b> · 估值、行业、板块与排除项</span>
          <span class="sc-advanced-state">展开</span>
        </button>
        <div class="sc-advanced" id="sc-advanced" hidden>
          <div class="sc-grid">
            ${rangeField('市盈率 PE(TTM)', 'sc-pe-min', 'sc-pe-max', '最小', '最大', '倍')}
            ${rangeField('市净率 PB', 'sc-pb-min', 'sc-pb-max', '最小', '最大', '倍')}
            ${minField('所属行业（关键词）', 'sc-ind', '如 半导体 / 证券', '')}
          </div>
          <div class="field sc-field" style="margin-top:16px">
            <label class="field-label">板块（多选，全选即不限制）</label>
            <div class="sc-boards">
              ${BOARDS.map((b) => `<label class="sc-chk"><input type="checkbox" class="sc-board" value="${b}" checked aria-label="包含${b}">${b}</label>`).join('')}
              <span aria-hidden="true" style="width:1px;height:18px;background:var(--border);margin:0 4px"></span>
              <label class="sc-chk"><input type="checkbox" id="sc-ex-st" checked aria-label="剔除 ST 和退市股票">剔除 ST / 退市</label>
              <label class="sc-chk"><input type="checkbox" id="sc-ex-loss" aria-label="剔除亏损股">剔除亏损股(PE≤0)</label>
            </div>
          </div>
        </div>

        <div class="sc-actions">
          <button type="button" class="btn btn-primary" id="sc-run" aria-label="按当前条件开始选股" aria-busy="false">开始选股</button>
          <button type="button" class="btn btn-ghost btn-sm" id="sc-reset" aria-label="重置全部筛选条件">重置条件</button>
          <div class="sc-sort-wrap">
            <label class="field-label" for="sc-sort" style="margin:0">排序</label>
            <select class="select" id="sc-sort" aria-label="结果排序字段">
              ${SORT_OPTS.map((o) => `<option value="${o.v}">${o.label}</option>`).join('')}
            </select>
            <select class="select" id="sc-dir" aria-label="结果排序方向">
              <option value="desc">从高到低</option>
              <option value="asc">从低到高</option>
            </select>
          </div>
        </div>
      </div>
    </section>

    <section class="card sc-result-card">
      <div class="card-head"><h3 class="card-title">选股结果</h3></div>
      <div class="card-body" id="sc-result" aria-live="polite" aria-busy="false">${compactResultEmpty('尚未运行筛选', '选择策略预设或设置条件，再点击“开始选股”。', ['尚未请求全市场股票池与每日指标'])}</div>
    </section>
    </div>`;

  container.querySelector('#sc-run').addEventListener('click', run);
  container.querySelector('#sc-reset').addEventListener('click', resetForm);
  container.querySelector('.sc-presets').addEventListener('click', (e) => {
    const btn = e.target.closest('.sc-preset');
    if (btn) applyPreset(btn.dataset.preset);
  });
  container.querySelector('#sc-advanced-toggle').addEventListener('click', () => setAdvancedOpen(!advancedOpen));
  const clearPresetOnManualEdit = (e) => {
    if (!e.target.matches('.sc-input,.sc-board,#sc-ex-st,#sc-ex-loss,#sc-sort,#sc-dir')) return;
    if (lastMeta && !e.target.matches('#sc-sort,#sc-dir')) {
      resultStale = true;
      renderResult();
    }
    if (!activePresetId) return;
    activePresetId = '';
    syncPresetUI();
  };
  container.querySelector('.sc-filter-card').addEventListener('input', clearPresetOnManualEdit);
  container.querySelector('.sc-filter-card').addEventListener('change', clearPresetOnManualEdit);
  // 切换排序：有结果则本地重排（避免重复请求全市场）；无结果则忽略
  const reorder = () => {
    if (!lastStocks.length) return;
    const sb = root.querySelector('#sc-sort').value;
    const desc = root.querySelector('#sc-dir').value !== 'asc';
    const sign = desc ? -1 : 1;
    lastStocks.sort((a, b) => {
      const va = a[sb], vb = b[sb];
      const na = va == null, nb = vb == null;
      if (na && nb) return 0;
      if (na) return 1;   // None 沉底
      if (nb) return -1;
      return sign * (va - vb);
    });
    renderResult();
  };
  container.querySelector('#sc-sort').addEventListener('change', reorder);
  container.querySelector('#sc-dir').addEventListener('change', reorder);

  // 回车即选股（任一输入框）
  container.querySelectorAll('.sc-input').forEach((i) => {
    i.addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
  });

  // 客户#3：有上次的结果 → 回填表单 + 直接重绘结果（不用重新选一遍）
  if (lastFilters) {
    restoreForm(lastFilters, lastSort);
    const restoredAdvanced = ['pe_min', 'pe_max', 'pb_min', 'pb_max', 'industry_kw', 'boards']
      .some((key) => lastFilters[key] !== undefined)
      || lastFilters.exclude_loss === true;
    setAdvancedOpen(restoredAdvanced);
  } else {
    setAdvancedOpen(false);
  }
  syncPresetUI();
  if (lastStocks.length && lastMeta) renderResult();
}

// 把上次的 filters/sort 回填进表单（collectFilters 的逆操作）
function restoreForm(f, sort) {
  if (!root || !f) return;
  const set = (id, v) => { const el = root.querySelector(id); if (el && v != null) el.value = v; };
  set('#sc-pe-min', f.pe_min); set('#sc-pe-max', f.pe_max);
  set('#sc-pb-min', f.pb_min); set('#sc-pb-max', f.pb_max);
  set('#sc-tr-min', f.turnover_min); set('#sc-tr-max', f.turnover_max);
  set('#sc-vr-min', f.volume_ratio_min);
  set('#sc-mv-min', f.circ_mv_min); set('#sc-mv-max', f.circ_mv_max);
  set('#sc-chg-min', f.chg_min); set('#sc-chg-max', f.chg_max);
  set('#sc-amt-min', f.amount_min); set('#sc-ind', f.industry_kw);
  if (Array.isArray(f.boards)) root.querySelectorAll('.sc-board').forEach((c) => { c.checked = f.boards.includes(c.value); });
  const exSt = root.querySelector('#sc-ex-st'); if (exSt) exSt.checked = f.exclude_st !== false;
  const exLoss = root.querySelector('#sc-ex-loss'); if (exLoss) exLoss.checked = !!f.exclude_loss;
  if (sort) { set('#sc-sort', sort.by); const dir = root.querySelector('#sc-dir'); if (dir) dir.value = sort.desc === false ? 'asc' : 'desc'; }
}

export function unmount() {
  runController?.abort();
  runController = null;
  clearInterval(progressTimer);
  progressTimer = null;
  root = null;
  loading = false;
  // 【不清】lastStocks/lastMeta/lastFilters —— 保留到下次挂载，切走再回来结果还在（客户#3）
}
