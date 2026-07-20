/* views/holdings.js — 持仓分析（行内编辑 + 实时盈亏 + 仓位环形图） */

import { api, fmtMoney, fmtPct, fmtPrice, pctClass, isTradingTime } from '../api.js';
import { icon, escapeHtml, toast, emptyState, skeleton, openModal, closeModal } from '../ui.js';
import { mkChart, disposeCharts, holdingsPieOption } from '../charts.js';
import { tickNum, flash } from '../live.js';

let root = null;
let charts = [];
let timers = [];        // 盘中自动刷新定时器——卸载/重挂时必须清，否则叠加泄漏
let rows = [];          // {code, name, cost, shares, note}
let quotes = {};        // code -> quote
let dirty = false;
let loaded = false;     // 持仓未从服务端加载成功前禁止保存——PUT /holdings 是整体覆写，空/陈旧 rows 会清空用户持仓
let sortKey = null;     // 表格排序列（点表头触发）；null=录入顺序
let sortDir = 1;        // 1=升序 / -1=降序
let totalAsset = null;  // 账户总资产（含现金），用于可审计的仓位/集中度计算

function num(v) {
  if (v == null || (typeof v === 'string' && !v.trim())) return null;
  const n = Number(v);
  return isFinite(n) ? n : null;
}

// 点表头排序：一次性重排 rows[]（不是每次渲染都排——否则编辑/自动刷新会把行洗乱）。
// 计算列（现价/盈亏/市值）从行情推导；缺值(未取到价/未填)恒排末尾，与升降方向无关。
function sortRowsNow() {
  if (!sortKey) return;
  const { detail } = calc();   // detail 与 rows 同序，detail[i] ↔ rows[i]
  const val = (d) => ({
    code: d.code || '', name: d.name || d.q?.name || '',
    cost: num(d.cost), shares: num(d.shares), price: d.price,
    change_pct: num(d.q?.change_pct), pnl: d.pnl, pnlPct: d.pnlPct, mv: d.mv,
  }[sortKey]);
  const idx = detail.map((_, i) => i);
  idx.sort((a, b) => {
    const va = val(detail[a]), vb = val(detail[b]);
    const na = va == null || va === '', nb = vb == null || vb === '';
    if (na && nb) return 0;
    if (na) return 1;            // 缺值恒排末尾
    if (nb) return -1;
    if (typeof va === 'string') return sortDir * String(va).localeCompare(String(vb), 'zh');
    return sortDir * (va - vb);
  });
  rows = idx.map((i) => rows[i]);
}

async function fetchQuotes(codes) {
  const valid = [...new Set((codes || []).map((code) => String(code || '').trim()).filter((code) => /^\d{6}$/.test(code)))];
  if (!valid.length) return;
  try {
    // 持仓合并一次 TDX quotes 请求；服务端再对缺失码批量退腾讯。
    // 这既保护 60次/分配额，也避免逐票请求将热集互相挤出。
    const d = await api.get(`/quotes?codes=${encodeURIComponent(valid.join(','))}`);
    const batch = d && typeof d.quotes === 'object' ? d.quotes : {};
    valid.forEach((code) => {
      const quote = batch[code];
      if (!quote) return;
      quotes[code] = quote;
      const r = rows.find((x) => x.code === code);
      if (r && !r.name && quote.name) r.name = quote.name;
    });
  } catch { /* 批次失败不覆盖已有价，等下一轮 */ }
}

async function fetchQuote(code) {
  if (!/^\d{6}$/.test(code)) return;
  try {
    await fetchQuotes([code]);
    if (!quotes[code]) {
      // 单票录入校验保留兼容端点；正常批量刷新不会走这里。
      const d = await api.get(`/quote/${code}`);
      if (d && d.ok !== false && d.quote) quotes[code] = d.quote;
    }
  } catch { /* 单只失败不阻塞 */ }
}

// 盘中每 5s 自动刷新持仓现价/盈亏：持仓 P&L 是最高实时诉求，客户不该盯着看还得手动点刷新。
// 只在【交易时段】刷（盘外市场没动，不刷）；用户【正在编辑单元格 / 有未保存改动】时跳过本轮，
// 绝不抢焦点、不覆盖正在填的值。原地重绘（换现价/盈亏、不闪 skeleton）。失败自吞、等下一轮。
// 服务器预热每 15s 批量刷新 quick_quotes；此处 20s 拉取通常命中热缓存。
function editingNow() {
  const ae = document.activeElement;
  return !!(ae && root && root.contains(ae) && /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName));
}
async function autoRefreshQuotes() {
  if (!loaded || !rows.length || dirty || editingNow() || !isTradingTime()) return;
  try {
    await fetchQuotes(rows.map((r) => r.code));
    if (dirty || editingNow()) return;   // 拉取期间用户开始编辑 → 放弃本次更新
    updateQuoteCells();                  // 就地跳动（滚动+红涨绿跌闪），不整块重画
  } catch { /* 自动刷新失败静默，下一轮再来 */ }
}

// 盘中就地跳动：只更新现价/涨跌/盈亏/市值单元格（数字滚动 + 红涨绿跌闪），不整块重画表格
// ——重画会丢滚动动画、并抢正在编辑的输入框。行数不符(增删了持仓)才回退整表重绘。
function updateQuoteCells() {
  if (!root) return;
  const tbl = root.querySelector('#hold-table');
  if (!tbl) return;
  const { detail, totalMv, totalCost, totalPnl } = calc();
  const totalPnlPct = totalCost ? (totalPnl / totalCost) * 100 : null;
  const portfolioWeight = totalAsset && totalAsset > 0 ? (totalMv / totalAsset) * 100 : null;
  const trs = tbl.querySelectorAll('tbody tr[data-i]');
  if (trs.length !== detail.length) { renderTable(); return; }
  const setDir = (el, cls) => { el.classList.remove('up', 'down', 'flat'); el.classList.add(cls); };
  detail.forEach((r, i) => {
    const tr = tbl.querySelector(`tbody tr[data-i="${i}"]`);
    if (!tr) return;
    const price = tr.querySelector('[data-cell="price"]');
    if (price && r.price != null) tickNum(price, r.price, (n) => fmtPrice(n));
    const pill = tr.querySelector('[data-cell="chg"] .chg-pill');
    if (pill) { pill.className = 'chg-pill ' + pctClass(r.q?.change_pct); pill.textContent = r.q ? fmtPct(r.q.change_pct) : '--'; }
    const pnl = tr.querySelector('[data-cell="pnl"]');
    if (pnl && r.pnl != null) { setDir(pnl, pctClass(r.pnl)); tickNum(pnl, r.pnl, (n) => (n >= 0 ? '+' : '') + fmtMoney(n)); }
    const pnlpct = tr.querySelector('[data-cell="pnlpct"]');
    if (pnlpct && r.pnlPct != null) { setDir(pnlpct, pctClass(r.pnl)); tickNum(pnlpct, r.pnlPct, (n) => fmtPct(n)); }
    const mv = tr.querySelector('[data-cell="mv"]');
    if (mv && r.mv != null) tickNum(mv, r.mv, (n) => fmtMoney(n));
  });
  const tp = tbl.querySelector('[data-cell="tpnl"]');
  if (tp && totalCost) { setDir(tp, pctClass(totalPnl)); tickNum(tp, totalPnl, (n) => (n >= 0 ? '+' : '') + fmtMoney(n)); }
  const tpp = tbl.querySelector('[data-cell="tpnlpct"]');
  if (tpp && totalPnlPct != null) { setDir(tpp, pctClass(totalPnl)); tickNum(tpp, totalPnlPct, (n) => fmtPct(n)); }
  const tmv = tbl.querySelector('[data-cell="tmv"]');
  if (tmv) tickNum(tmv, totalMv, (n) => fmtMoney(n));
}

function calc() {
  let totalMv = 0, totalCost = 0;
  const detail = rows.map((r) => {
    const q = quotes[r.code];
    const price = q ? num(q.price) : null;
    const cost = num(r.cost), shares = num(r.shares);
    const mv = price != null && shares != null ? price * shares : null;
    const costAmt = cost != null && shares != null ? cost * shares : null;
    const pnl = mv != null && costAmt != null ? mv - costAmt : null;
    const pnlPct = pnl != null && costAmt ? (pnl / costAmt) * 100 : null;
    if (mv != null) totalMv += mv;
    if (costAmt != null) totalCost += costAmt;
    return { ...r, q, price, mv, costAmt, pnl, pnlPct };
  });
  return { detail, totalMv, totalCost, totalPnl: totalMv - totalCost };
}

function renderTable() {
  if (!root) return;
  const el = root.querySelector('#hold-table');
  const detailCard = root.querySelector('#hold-detail-card');
  const pieCard = root.querySelector('#hold-pie-card');
  const headerAdd = root.querySelector('#hold-add');
  const secondary = root.querySelectorAll('[data-hold-secondary]');
  if (headerAdd) headerAdd.hidden = !rows.length;
  secondary.forEach((node) => {
    // 删除最后一只后必须保留保存入口，否则空持仓无法提交到服务端。
    node.hidden = !rows.length && !(node.id === 'hold-save' && dirty);
  });
  if (!rows.length) {
    if (pieCard) pieCard.hidden = true;
    el.innerHTML = `<div class="hold-empty">
      <div class="hold-empty-copy">
        <div class="hold-empty-title">建立你的第一笔持仓</div>
        <div class="hold-empty-desc">填写股票代码、成本价与股数后再保存；系统不会先插入 000000 或零金额假记录。</div>
      </div>
      <button class="btn btn-primary" id="hold-empty-add">${icon('plus')}添加持仓</button>
    </div>`;
    el.querySelector('#hold-empty-add')?.addEventListener('click', openAddHolding);
    renderPie([]);
    return;
  }
  if (pieCard) pieCard.hidden = false;
  const { detail, totalMv, totalCost, totalPnl } = calc();
  const totalPnlPct = totalCost ? (totalPnl / totalCost) * 100 : null;
  const portfolioWeight = totalAsset && totalAsset > 0 ? (totalMv / totalAsset) * 100 : null;

  // B-2：非交易时段"现价"其实是收盘价——顶部如实提示一次，别让客户把收盘当实时看盈亏
  const staleQ = detail.find((r) => r.q && r.q.stale);
  const staleBanner = staleQ
    ? `<div style="margin-bottom:8px;padding:6px 10px;border:1px solid var(--amber-line,rgba(245,166,35,.4));background:rgba(245,166,35,.08);border-radius:7px;font-size:12px;color:var(--amber);line-height:1.5">⚠ ${escapeHtml(staleQ.q.quote_note || '当前非交易时段，现价为收盘价、非实时')}</div>`
    : '';
  const assetBanner = totalAsset && totalAsset > 0
    ? ''
    : `<div style="margin-bottom:8px;padding:6px 10px;border:1px solid var(--border);background:var(--panel-2);border-radius:7px;font-size:12px;color:var(--text-2);line-height:1.5">未设置账户总资产，「仓位占比」不作估算；下方图表仅表示已录入持仓之间的市值构成。</div>`;

  // 可排序表头：点列头排序（升↑/降↓），像 Excel。备注/仓位占比(与市值同序)不设排序。
  const sh = (k, label, cls = '', style = '') =>
    `<th class="${cls} hold-sortable" data-sort="${k}" style="cursor:pointer;user-select:none;white-space:nowrap;${style}" title="点击排序">${label}${sortKey === k ? (sortDir > 0 ? ' ↑' : ' ↓') : ''}</th>`;
  el.innerHTML = staleBanner + assetBanner + `<div class="tbl-wrap"><table class="tbl">
    <thead><tr>
      <th style="width:34px"><input type="checkbox" id="hold-check-all" checked title="全选 / 全不选"></th>
      ${sh('code', '代码', 'l', 'width:90px')}${sh('name', '名称', 'l')}<th class="l">行业（服务端核验）</th>
      ${sh('cost', '成本价', '', 'width:90px')}${sh('shares', '股数', '', 'width:100px')}
      ${sh('price', '现价')}${sh('change_pct', '今日涨跌')}${sh('pnl', '浮动盈亏')}${sh('pnlPct', '盈亏比例')}${sh('mv', '市值')}<th class="l">仓位占比</th>
      <th class="l">备注</th><th style="width:44px"></th>
    </tr></thead>
    <tbody>
      ${detail.map((r, i) => {
        const pCls = pctClass(r.pnl);
        return `<tr data-i="${i}">
          <td style="text-align:center"><input type="checkbox" class="hold-check" data-code="${escapeHtml(r.code ?? '')}" checked title="勾选后 AI 诊断只分析选中的票"></td>
          <td class="l"><input class="hold-input num" data-f="code" value="${escapeHtml(r.code ?? '')}" maxlength="6" placeholder="000000" style="width:80px"></td>
          <td class="l" style="font-weight:600">${escapeHtml(r.name || r.q?.name || '--')}</td>
          <td class="l"><span title="行业按股票代码由服务端免费公开源核验，不能手工修改">${escapeHtml(r.industry || '待核验')}</span></td>
          <td><input class="hold-input num" data-f="cost" value="${escapeHtml(r.cost ?? '')}" placeholder="0.00" style="text-align:right"></td>
          <td><input class="hold-input num" data-f="shares" value="${escapeHtml(r.shares ?? '')}" placeholder="0" style="text-align:right"></td>
          <td class="num" data-cell="price">${r.price != null ? fmtPrice(r.price) : '<span style="color:var(--text-3)">--</span>'}</td>
          <td data-cell="chg"><span class="chg-pill ${pctClass(r.q?.change_pct)}">${r.q ? fmtPct(r.q.change_pct) : '--'}</span></td>
          <td class="num ${pCls}" data-cell="pnl" style="font-weight:600">${r.pnl != null ? (r.pnl >= 0 ? '+' : '') + fmtMoney(r.pnl) : '--'}</td>
          <td class="num ${pCls}" data-cell="pnlpct">${r.pnlPct != null ? fmtPct(r.pnlPct) : '--'}</td>
          <td class="num" data-cell="mv">${r.mv != null ? fmtMoney(r.mv) : '--'}</td>
          <td class="num">${r.mv != null && totalAsset ? fmtPct((r.mv / totalAsset) * 100, { signed: false, digits: 1 }) : '--'}</td>
          <td class="l"><input class="hold-input" data-f="note" value="${escapeHtml(r.note ?? '')}" placeholder="备注…" style="min-width:110px"></td>
          <td><button class="btn-danger-ghost row-del" data-del="${i}" title="删除该持仓" style="font-size:12px;padding:3px 9px;white-space:nowrap;color:var(--down);border:1px solid var(--down);border-radius:4px;background:transparent">${icon('trash')} 删除</button></td>
        </tr>`;
      }).join('')}
    </tbody>
    <tfoot><tr>
      <td class="l" colspan="4">合计 ${detail.length} 只</td>
      <td class="num" colspan="2">成本 ${fmtMoney(totalCost)}</td>
      <td></td><td></td>
      <td class="num ${pctClass(totalPnl)}" data-cell="tpnl">${totalCost ? (totalPnl >= 0 ? '+' : '') + fmtMoney(totalPnl) : '--'}</td>
      <td class="num ${pctClass(totalPnl)}" data-cell="tpnlpct">${totalPnlPct != null ? fmtPct(totalPnlPct) : '--'}</td>
      <td class="num" data-cell="tmv">${fmtMoney(totalMv)}</td>
      <td class="num">${portfolioWeight != null ? fmtPct(portfolioWeight, { signed: false, digits: 1 }) : '未设置'}</td>
      <td colspan="2"></td>
    </tr></tfoot>
  </table></div>`;

  renderPie(detail.filter((r) => r.mv != null && r.mv > 0).map((r) => ({ name: r.name || r.code || '未知', value: Math.round(r.mv) })));
}

function openAddHolding() {
  let resolvedName = '';
  let verifySeq = 0;
  openModal({
    title: '添加持仓',
    body: `<div class="hold-add-form">
      <div class="field">
        <label class="field-label" for="hold-add-code">股票代码</label>
        <input class="input num" id="hold-add-code" inputmode="numeric" maxlength="6" autocomplete="off" placeholder="例如 600519">
        <div class="field-hint" id="hold-add-code-hint">输入 6 位 A 股代码后自动核验名称</div>
      </div>
      <div class="hold-add-grid">
        <div class="field"><label class="field-label" for="hold-add-cost">成本价</label><input class="input num" id="hold-add-cost" inputmode="decimal" placeholder="0.00"></div>
        <div class="field"><label class="field-label" for="hold-add-shares">股数</label><input class="input num" id="hold-add-shares" inputmode="numeric" placeholder="100"></div>
      </div>
      <div class="field"><label class="field-label" for="hold-add-note">备注（可选）</label><input class="input" id="hold-add-note" maxlength="80" placeholder="例如：核心仓 / 等待财报"></div>
      <div class="hold-add-preview" id="hold-add-preview">输入成本价和股数后显示持仓成本</div>
      <div class="field-hint" id="hold-add-error" role="alert" style="color:var(--up);min-height:20px"></div>
    </div>`,
    footer: '<button class="btn btn-ghost" data-hold-cancel>取消</button><button class="btn btn-primary" data-hold-confirm>确认添加</button>',
    onMount(modal) {
      const code = modal.querySelector('#hold-add-code');
      const cost = modal.querySelector('#hold-add-cost');
      const shares = modal.querySelector('#hold-add-shares');
      const note = modal.querySelector('#hold-add-note');
      const hint = modal.querySelector('#hold-add-code-hint');
      const preview = modal.querySelector('#hold-add-preview');
      const error = modal.querySelector('#hold-add-error');
      const updatePreview = () => {
        const c = num(cost.value), s = num(shares.value);
        preview.textContent = c != null && c > 0 && s != null && s > 0
          ? `预计持仓成本：${fmtMoney(c * s)}${resolvedName ? ` · ${resolvedName}` : ''}`
          : '输入成本价和股数后显示持仓成本';
      };
      const confirm = modal.querySelector('[data-hold-confirm]');
      const verify = async (expected = code.value.trim()) => {
        const seq = ++verifySeq;
        const value = expected;
        resolvedName = '';
        if (!/^\d{6}$/.test(value)) { hint.textContent = '请输入完整的 6 位股票代码'; return ''; }
        hint.textContent = '正在核验股票名称…';
        await fetchQuote(value);
        // 用户可能在请求返回前改了代码；过期结果不能覆盖当前名称。
        if (seq !== verifySeq || code.value.trim() !== value) return '';
        resolvedName = quotes[value]?.name || '';
        hint.textContent = resolvedName ? `已识别：${resolvedName}` : '暂未取到名称，保存后服务端会再次核验';
        updatePreview();
        return resolvedName;
      };
      code.addEventListener('input', () => {
        resolvedName = '';
        verifySeq += 1;
        if (code.value.trim().length === 6) verify(code.value.trim());
        else hint.textContent = '输入 6 位 A 股代码后自动核验名称';
      });
      code.addEventListener('blur', () => verify(code.value.trim()));
      cost.addEventListener('input', updatePreview);
      shares.addEventListener('input', updatePreview);
      modal.querySelector('[data-hold-cancel]').addEventListener('click', closeModal);
      confirm.addEventListener('click', async () => {
        const value = code.value.trim();
        const c = num(cost.value), s = num(shares.value);
        error.textContent = '';
        if (!/^\d{6}$/.test(value)) { error.textContent = '股票代码必须是 6 位数字。'; code.focus(); return; }
        if (!(c > 0)) { error.textContent = '成本价必须大于 0。'; cost.focus(); return; }
        if (!(s > 0) || !Number.isInteger(s)) { error.textContent = '股数必须是大于 0 的整数。'; shares.focus(); return; }
        if (rows.some((row) => String(row.code) === value)) { error.textContent = '该股票已经在持仓中，请直接编辑原记录。'; code.focus(); return; }
        confirm.disabled = true;
        try {
          const name = resolvedName || await verify(value);
          if (code.value.trim() !== value) {
            error.textContent = '股票代码已变化，请确认后重试。';
            code.focus();
            return;
          }
          rows.push({ code: value, name, cost: c, shares: s, note: note.value.trim() });
          closeModal();
          renderTable();
          markDirty();
          toast('已加入待保存持仓，请确认后点击保存', 'info');
        } finally {
          if (confirm.isConnected) confirm.disabled = false;
        }
      });
      code.focus();
    },
  });
}

function renderPie(data) {
  if (!root) return;
  const el = root.querySelector('#hold-pie');
  disposeCharts(charts);
  if (!data.length) { el.innerHTML = emptyState('暂无仓位数据', '录入持仓并拉取到实时价后显示', '', 'holdings'); return; }
  el.innerHTML = '<div style="width:100%;height:150px" id="hold-pie-chart"></div>';
  const c = mkChart(el.querySelector('#hold-pie-chart'));
  if (c) { c.setOption(holdingsPieOption(data)); charts.push(c); }
}

function markDirty() {
  dirty = true;
  const btn = root.querySelector('#hold-save');
  if (btn) btn.classList.add('btn-primary');
}

// 加载失败时禁用保存/刷新：保存会用空 rows 覆盖服务端，刷新会把失败提示替换成「暂无持仓」假象
function setActionsEnabled(on) {
  const save = root?.querySelector('#hold-save');
  const refresh = root?.querySelector('#hold-refresh');
  if (save) { save.disabled = !on; save.title = on ? '' : '行情未加载，禁止覆盖'; }
  if (refresh) refresh.disabled = !on;
}

async function load() {
  const el = root.querySelector('#hold-table');
  el.innerHTML = skeleton(5);
  try {
    const d = await api.get('/holdings');
    rows = (d && Array.isArray(d.holdings)) ? d.holdings.map((h) => ({ ...h })) : [];
    totalAsset = num(d?.portfolio_total_asset);
    const navInput = root.querySelector('#hold-total-asset');
    if (navInput) navInput.value = totalAsset == null ? '' : String(totalAsset);
  } catch (e) {
    loaded = false;
    setActionsEnabled(false);
    el.innerHTML = emptyState('持仓加载失败', e.message, `<button class="btn btn-sm" id="hold-retry" style="margin-top:6px">重试加载</button>`, 'alert');
    el.querySelector('#hold-retry')?.addEventListener('click', load);
    return;
  }
  loaded = true;
  setActionsEnabled(true);
  renderTable();
  await fetchQuotes(rows.map((r) => r.code));
  renderTable();
}

async function save() {
  if (!loaded) { toast('行情未加载，禁止覆盖', 'error'); return; }
  const btn = root.querySelector('#hold-save');
  btn.disabled = true;
  const payload = rows
    .filter((r) => r.code)
    .map((r) => ({
      code: String(r.code).trim(),
      name: r.name || quotes[r.code]?.name || '',
      cost: num(r.cost) ?? 0,
      shares: num(r.shares) ?? 0,
      // 权重只能由「当前市值 / 账户总资产」审计派生。缺总资产时不沿用
      // 历史 weight_pct，否则账户总资产已清空时页面仍会显示虚假的 100% 仓位。
      weight_pct: null,
      market_value: (num(quotes[r.code]?.price) ?? num(r.cost) ?? 0) * (num(r.shares) ?? 0),
      note: r.note || '',
    }));
  try {
    const r = await api.put('/holdings', { holdings: payload, portfolio_total_asset: totalAsset });
    if (r && r.ok !== false) {
      if (Array.isArray(r.holdings)) rows = r.holdings.map((h) => ({ ...h }));
      dirty = false;
      btn.classList.remove('btn-primary');
      renderTable();
      toast('持仓已保存，行业已由服务端核验', 'success');
    }
    else toast('保存失败：' + (r?.error || '未知错误'), 'error');
  } catch (e) {
    toast('保存失败：' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

export function mount(container) {
  root = container;
  rows = [];            // 不清会让上次挂载的陈旧持仓在本次加载失败时被「保存」覆写到服务端
  quotes = {};
  dirty = false;
  loaded = false;
  totalAsset = null;
  sortKey = null; sortDir = 1;   // 重新进页面回到录入顺序
  if (!document.getElementById('holdings-ux-style')) {
    const style = document.createElement('style');
    style.id = 'holdings-ux-style';
    style.textContent = `.hold-empty{min-height:230px;display:flex;align-items:center;justify-content:center;gap:28px;padding:28px;border:1px dashed var(--border-strong);border-radius:var(--radius-sm);background:var(--panel-2)}
      #hold-add[hidden],#hold-refresh[hidden],#hold-save[hidden],#hold-ai[hidden]{display:none!important}
      .hold-empty-copy{max-width:520px}.hold-empty-title{font-size:20px;font-weight:750;color:var(--text);margin-bottom:7px}.hold-empty-desc{font-size:14px;line-height:1.7;color:var(--text-2)}
      .hold-add-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.hold-add-preview{padding:11px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel-2);font-size:14px;font-weight:650;color:var(--text)}
      .holdings-grid{align-items:start}.holdings-grid #hold-detail-card,.holdings-grid #hold-pie-card{grid-column:1/-1}
      #hold-detail-card{order:1}#hold-pie-card{order:2}#hold-detail-card .tbl-wrap{overflow-x:visible}
      #hold-pie-card .card-body{padding:4px 18px 10px}
      @media(max-width:1320px){#hold-detail-card .tbl-wrap{overflow-x:auto}}
      @media(max-width:680px){.hold-empty{min-height:0;align-items:flex-start;flex-direction:column}.hold-add-grid{grid-template-columns:1fr}}`;
    document.head.appendChild(style);
  }
  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">我的持仓</h2>
      <span class="page-sub">录入/管理你的持仓 · 行内编辑 · 实时盈亏 · 勾选个股一键 AI 诊断</span>
      <div class="ph-extra">
        <button class="btn btn-primary" id="hold-add">${icon('plus')}添加持仓</button>
        <button class="btn btn-sm" id="hold-refresh" data-hold-secondary>${icon('refresh')}刷新行情</button>
        <button class="btn btn-sm" id="hold-save" data-hold-secondary>${icon('save')}保存持仓</button>
        <button class="btn btn-sm" id="hold-ai" data-hold-secondary>${icon('spark')}AI 持仓诊断</button>
      </div>
    </div>
    <div class="grid12 holdings-grid">
      <section class="card hoverable sp-12" id="hold-detail-card">
        <div class="card-head"><h3 class="card-title">持仓明细</h3></div>
        <div class="card-body">
          <div style="display:flex;align-items:center;gap:10px;justify-content:flex-end;margin-bottom:12px">
            <label for="hold-total-asset" style="font-size:12px;color:var(--text-2);white-space:nowrap">账户总资产（含现金）</label>
            <input id="hold-total-asset" class="input" inputmode="decimal" placeholder="用于计算真实仓位" style="width:180px;height:34px">
          </div>
          <div id="hold-table"></div>
        </div>
      </section>
      <section class="card hoverable sp-12" id="hold-pie-card">
        <div class="card-head"><h3 class="card-title">持仓市值构成</h3></div>
        <div class="card-body" id="hold-pie"></div>
      </section>
    </div>`;

  container.querySelector('#hold-add').addEventListener('click', openAddHolding);
  container.querySelector('#hold-refresh').addEventListener('click', async () => {
    await fetchQuotes(rows.map((r) => r.code));
    renderTable();
    toast('行情已刷新', 'success');
  });
  container.querySelector('#hold-save').addEventListener('click', save);
  container.querySelector('#hold-total-asset').addEventListener('input', (e) => {
    totalAsset = num(e.target.value);
    markDirty();
  });
  container.querySelector('#hold-ai').addEventListener('click', () => {
    const allCodes = rows.map((r) => String(r.code || '').trim()).filter((c) => /^\d{6}$/.test(c));
    if (!allCodes.length) { toast('请先录入持仓代码', 'error'); return; }
    const checked = Array.from(container.querySelectorAll('.hold-check:checked'))
      .map((c) => c.dataset.code).filter((c) => /^\d{6}$/.test(c));
    if (!checked.length) { toast('请至少勾选一只要诊断的票', 'error'); return; }
    // 勾选子集 → 只诊断这几只；全选(=勾满) → 传空数组，后端按"全部"处理(向后兼容)
    const subset = checked.length < allCodes.length ? checked : [];
    sessionStorage.setItem('hermes.chat.action', 'position');
    sessionStorage.setItem('hermes.chat.position.codes', JSON.stringify(subset));
    location.hash = '#/chat';
  });

  // 表格编辑/删除（事件委托）
  const tableEl = container.querySelector('#hold-table');
  tableEl.addEventListener('change', async (e) => {
    // 表头「全选」→ 同步所有行勾选框
    if (e.target.id === 'hold-check-all') {
      const on = e.target.checked;
      tableEl.querySelectorAll('.hold-check').forEach((c) => { c.checked = on; });
      return;
    }
    // 单行勾选变化 → 回写表头「全选」态（全勾才勾上）
    if (e.target.classList.contains('hold-check')) {
      const boxes = tableEl.querySelectorAll('.hold-check');
      const allEl = tableEl.querySelector('#hold-check-all');
      if (allEl) allEl.checked = boxes.length > 0 && Array.from(boxes).every((c) => c.checked);
      return;
    }
    const input = e.target.closest('.hold-input');
    if (!input) return;
    const tr = input.closest('tr');
    const i = Number(tr?.dataset.i);
    if (!rows[i]) return;
    const f = input.dataset.f;
    rows[i][f] = input.value.trim();
    markDirty();
    if (f === 'code' && /^\d{6}$/.test(input.value.trim())) {
      rows[i].name = '';
      await fetchQuote(input.value.trim());
    }
    renderTable();
  });
  tableEl.addEventListener('click', (e) => {
    // 点表头排序：同列切升降、换列默认升序 → 一次性重排 rows + 重绘
    const th = e.target.closest('[data-sort]');
    if (th) {
      const k = th.dataset.sort;
      if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = 1; }
      sortRowsNow();
      renderTable();
      return;
    }
    const del = e.target.closest('[data-del]');
    if (!del) return;
    rows.splice(Number(del.dataset.del), 1);
    markDirty();
    renderTable();
  });

  load();
  // 盘中每 5s 自动刷新现价/盈亏并就地跳动（/quote 腾讯直连~0.15s，就地更新不重画）——交易时段门控 + 编辑保护在 autoRefreshQuotes 内
  timers.forEach(clearInterval);
  timers = [];
  timers.push(setInterval(autoRefreshQuotes, 5000));
  window.addEventListener('beforeunload', beforeUnloadGuard);   // 客户#1：刷新/关页前拦未保存
}

// 客户#1：有未保存改动就拦。beforeLeave 给 app 路由（切页）用；beforeUnloadGuard 给刷新/关页用。
function beforeUnloadGuard(e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } }
export function beforeLeave() {
  return dirty ? '你有未保存的持仓改动，离开将丢失。确定离开吗？\n（点「取消」留下，先点右上角「保存持仓」）' : null;
}

export function unmount() {
  timers.forEach(clearInterval);   // 必清定时器，否则重挂/切页叠加泄漏
  timers = [];
  window.removeEventListener('beforeunload', beforeUnloadGuard);
  disposeCharts(charts);
  root = null;
}
