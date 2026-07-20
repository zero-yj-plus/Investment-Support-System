/* views/simulation.js — 本地模拟执行（不连接券商） */

import { api, fmtPrice } from '../api.js';
import {
  icon, escapeHtml, toast, openModal, closeModal, confirmModal, skeleton, emptyState,
} from '../ui.js';

let root = null;
let status = null;
let orders = [];
let generation = 0;

const STATUS_LABELS = Object.freeze({
  proposed: '待人工批准', approved: '已批准', filled: '已模拟成交',
  rejected: '已拒绝', cancelled: '已取消',
});

const REASON_LABELS = Object.freeze({
  invalid_symbol: '股票代码无效', invalid_side: '买卖方向无效',
  invalid_order_type: '订单类型无效', invalid_quantity: '数量无效',
  buy_quantity_not_board_lot_100: '买入数量必须为 100 股整数手',
  invalid_reference_price: '服务端现价缺失', invalid_limit_price: '限价无效',
  market_order_cannot_have_limit_price: '市价单不能带限价',
  risk_level_missing_or_invalid: '服务端风险等级缺失', risk_level_gte_4: '当前风险等级禁止新增买入',
  data_stale: '行情已经过期', data_missing: '关键行情证据缺失',
  data_as_of_missing_or_invalid: '行情时点缺失', data_as_of_future: '行情时点异常',
  invalid_market_price: '服务端成交价缺失', market_as_of_missing_or_invalid: '成交行情时点缺失',
  market_as_of_future: '成交行情时点异常', market_data_stale: '成交行情已经过期',
  market_data_missing: '成交行情证据缺失', real_market_source_required: '成交行情来源不可核验',
  buy_limit_not_marketable: '现价高于买入限价', sell_limit_not_marketable: '现价低于卖出限价',
  emergency_stop_active: '紧急停止已开启',
  operator_cancelled: '操作人取消', operator_cancelled_from_web: '操作人从网页取消',
});

function requestId(scope) {
  const key = `hermes.sim.req.${scope}`;
  let value = sessionStorage.getItem(key);
  if (!value) {
    const uuid = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    value = `web:${scope}:${uuid}`.replace(/[^A-Za-z0-9._:-]/g, '-').slice(0, 160);
    sessionStorage.setItem(key, value);
  }
  return { key, value };
}

function clearRequest(entry) {
  if (entry?.key) sessionStorage.removeItem(entry.key);
}

function formatTime(value) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(String(value));
  return date.toLocaleString('zh-CN', { hour12: false });
}

function formatReason(value) {
  if (!value) return '--';
  return String(value).split(';').filter(Boolean)
    .map((reason) => REASON_LABELS[reason] || reason).join('；');
}

function injectStyle() {
  if (document.getElementById('simulation-style')) return;
  const style = document.createElement('style');
  style.id = 'simulation-style';
  style.textContent = `
    .sim-boundary{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(260px,.7fr);gap:16px;margin-bottom:16px}
    .sim-hero{padding:22px;border:1px solid var(--border);border-left:3px solid var(--orange);border-radius:var(--radius);background:linear-gradient(105deg,var(--panel),var(--panel-2))}
    .sim-kicker{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:750;letter-spacing:.06em;color:var(--orange);text-transform:uppercase}
    .sim-hero h3{margin:8px 0 7px;font-size:20px;line-height:1.35}.sim-hero p{margin:0;max-width:760px;color:var(--text-2);font-size:14px;line-height:1.7}
    .sim-rule-list{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-top:16px}.sim-rule{padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel);font-size:12px;color:var(--text-2)}.sim-rule strong{display:block;margin-bottom:3px;color:var(--text);font-size:13px}
    .sim-stop-card{padding:18px;border:1px solid var(--border);border-radius:var(--radius);background:var(--panel)}.sim-stop-card.active{border-color:rgba(240,68,90,.55);background:rgba(240,68,90,.06)}
    #sim-stop[hidden],#sim-reset[hidden]{display:none!important}
    .sim-stop-title{display:flex;align-items:center;justify-content:space-between;gap:10px;font-weight:750}.sim-stop-copy{min-height:43px;margin:9px 0 14px;color:var(--text-2);font-size:13px;line-height:1.6}.sim-stop-actions{display:flex;flex-wrap:wrap;gap:8px}
    .sim-counts{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:16px}.sim-count{padding:14px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel)}.sim-count b{display:block;font:700 22px/1.2 var(--font-mono);color:var(--text)}.sim-count span{font-size:12px;color:var(--text-2)}
    .sim-table-wrap{max-width:100%;overflow:auto}.sim-table{width:100%;min-width:1040px;border-collapse:collapse}.sim-table th,.sim-table td{padding:11px 10px;border-bottom:1px solid var(--border);font-size:13px;text-align:left;vertical-align:top}.sim-table th{color:var(--text-2);font-size:12px;white-space:nowrap}.sim-table td.num{font-family:var(--font-mono);white-space:nowrap}.sim-status{display:inline-flex;padding:3px 8px;border-radius:999px;border:1px solid var(--border);font-size:12px;font-weight:700;white-space:nowrap}.sim-status.proposed{color:var(--orange);border-color:rgba(255,142,60,.45)}.sim-status.approved{color:var(--blue);border-color:rgba(64,132,255,.45)}.sim-status.filled{color:var(--down);border-color:rgba(21,184,106,.45)}.sim-status.rejected,.sim-status.cancelled{color:var(--text-2)}
    .sim-actions{display:flex;flex-wrap:wrap;gap:6px}.sim-actions .btn{min-height:30px;padding:4px 9px}.sim-reason{max-width:260px;color:var(--text-2);line-height:1.5}.sim-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.sim-form-grid .full{grid-column:1/-1}.sim-form-note{padding:10px 12px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--panel-2);color:var(--text-2);font-size:13px;line-height:1.6}
    @media(max-width:900px){.sim-boundary{grid-template-columns:1fr}.sim-rule-list{grid-template-columns:1fr}.sim-counts{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media(max-width:560px){.sim-hero{padding:16px}.sim-form-grid{grid-template-columns:1fr}.sim-form-grid .full{grid-column:auto}.sim-counts{grid-template-columns:1fr 1fr}.sim-stop-actions .btn{width:100%}}
  `;
  document.head.appendChild(style);
}

function renderStatus() {
  if (!root || !status) return;
  const stop = status.emergency_stop || {};
  const stopCard = root.querySelector('#sim-stop-card');
  stopCard?.classList.toggle('active', stop.active === true);
  const title = root.querySelector('#sim-stop-state');
  if (title) title.textContent = stop.active ? '紧急停止已开启' : '紧急停止未开启';
  const copy = root.querySelector('#sim-stop-copy');
  if (copy) copy.textContent = stop.active
    ? `原因：${stop.reason || '未记录'}。所有待处理模拟订单已取消，新订单与批准都会被阻断。`
    : '紧急停止未开启。任何模拟成交仍需人工批准，且不会发送到券商。';
  const toggle = root.querySelector('#sim-stop');
  const reset = root.querySelector('#sim-reset');
  if (toggle) toggle.hidden = stop.active === true;
  if (reset) reset.hidden = stop.active !== true;

  const counts = status.counts || {};
  const host = root.querySelector('#sim-counts');
  if (host) host.innerHTML = ['proposed', 'approved', 'filled', 'rejected', 'cancelled'].map((key) => `
    <div class="sim-count"><b>${Number(counts[key] || 0)}</b><span>${STATUS_LABELS[key]}</span></div>`).join('');
}

function actionButtons(order) {
  if (order.status === 'proposed') return `
    <button class="btn btn-sm btn-primary" data-action="approve" data-id="${escapeHtml(order.order_id)}">${icon('check')}人工批准</button>
    <button class="btn btn-sm" data-action="cancel" data-id="${escapeHtml(order.order_id)}">取消</button>`;
  if (order.status === 'approved') return `
    <button class="btn btn-sm btn-primary" data-action="fill" data-id="${escapeHtml(order.order_id)}">${icon('play')}模拟成交</button>
    <button class="btn btn-sm" data-action="cancel" data-id="${escapeHtml(order.order_id)}">取消</button>`;
  return '<span style="color:var(--text-3)">已结束</span>';
}

function renderOrders() {
  if (!root) return;
  const host = root.querySelector('#sim-orders');
  if (!host) return;
  if (!orders.length) {
    host.innerHTML = emptyState(
      '还没有模拟订单',
      '新建一笔订单意图后，服务端会读取当前风险与行情，再决定进入待批准或直接拒绝。',
      '<button class="btn btn-primary" id="sim-empty-create">新建模拟订单</button>',
      'info',
    );
    host.querySelector('#sim-empty-create')?.addEventListener('click', openCreate);
    return;
  }
  host.innerHTML = `<div class="sim-table-wrap"><table class="sim-table">
    <thead><tr><th>状态</th><th>股票 / 方向</th><th>数量 / 类型</th><th>订单价格</th><th>风险 / 数据时点</th><th>成交与滑点</th><th>拒绝或取消原因</th><th>操作</th></tr></thead>
    <tbody>${orders.map((order) => `<tr>
      <td><span class="sim-status ${escapeHtml(order.status)}">${escapeHtml(STATUS_LABELS[order.status] || order.status)}</span></td>
      <td><strong class="num">${escapeHtml(order.symbol || '--')}</strong><br><span style="color:var(--text-2)">${order.side === 'buy' ? '买入' : '卖出'}</span></td>
      <td class="num">${order.quantity ?? '--'} 股<br><span style="color:var(--text-2);font-family:var(--font)">${order.order_type === 'limit' ? '限价单' : '市价单'}</span></td>
      <td class="num">参考 ${fmtPrice(order.reference_price)}<br>${order.order_type === 'limit' ? `限价 ${fmtPrice(order.limit_price)}` : '按服务端现价'}</td>
      <td>风险 ${order.risk_level ?? '--'} 级<br><span style="color:var(--text-2)">${formatTime(order.data_as_of)}</span></td>
      <td class="num">${order.market_price != null ? `成交 ${fmtPrice(order.market_price)}` : '--'}<br><span style="color:var(--text-2)">${order.slippage_bps != null ? `滑点 ${Number(order.slippage_bps).toFixed(2)} bp` : ''}</span></td>
      <td class="sim-reason">${escapeHtml(formatReason(order.rejection_reason))}</td>
      <td><div class="sim-actions">${actionButtons(order)}</div></td>
    </tr>`).join('')}</tbody>
  </table></div>`;
}

async function load({ quiet = false } = {}) {
  const current = ++generation;
  const host = root?.querySelector('#sim-orders');
  if (!quiet && host) host.innerHTML = skeleton(4);
  try {
    const [nextStatus, nextOrders] = await Promise.all([
      api.get('/simulation/status'), api.get('/simulation/orders?limit=100'),
    ]);
    if (!root || current !== generation) return;
    status = nextStatus;
    orders = Array.isArray(nextOrders?.items) ? nextOrders.items : [];
    renderStatus();
    renderOrders();
  } catch (error) {
    if (!root || current !== generation) return;
    if (!quiet && host) host.innerHTML = emptyState(
      '模拟账本加载失败', error.message,
      '<button class="btn btn-sm" id="sim-retry">重试</button>', 'alert',
    );
    host?.querySelector('#sim-retry')?.addEventListener('click', () => load());
  }
}

function numberValue(input) {
  const text = String(input?.value || '').trim();
  if (!text) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

function openCreate() {
  openModal({
    title: '新建模拟订单',
    body: `<div class="sim-form-grid">
      <div class="field"><label class="field-label" for="sim-symbol">股票代码</label><input class="input num" id="sim-symbol" maxlength="6" inputmode="numeric" placeholder="例如 600519"></div>
      <div class="field"><label class="field-label" for="sim-side">方向</label><select class="input" id="sim-side"><option value="buy">买入</option><option value="sell">卖出</option></select></div>
      <div class="field"><label class="field-label" for="sim-type">订单类型</label><select class="input" id="sim-type"><option value="limit">限价单</option><option value="market">市价单</option></select></div>
      <div class="field"><label class="field-label" for="sim-qty">数量（股）</label><input class="input num" id="sim-qty" inputmode="numeric" value="100"></div>
      <div class="field full" id="sim-limit-field"><label class="field-label" for="sim-limit">限价（元）</label><input class="input num" id="sim-limit" inputmode="decimal" placeholder="必须大于 0"></div>
      <div class="sim-form-note full">风险等级、参考价、行情时间和数据缺口由服务端读取。这里不能手填，也不会接受前端伪造值。</div>
      <div class="field-hint full" id="sim-create-error" role="alert" style="color:var(--up);min-height:20px"></div>
    </div>`,
    footer: '<button class="btn btn-ghost" data-cancel>取消</button><button class="btn btn-primary" data-submit>提交订单意图</button>',
    onMount(modal) {
      const type = modal.querySelector('#sim-type');
      const limitField = modal.querySelector('#sim-limit-field');
      type.addEventListener('change', () => { limitField.hidden = type.value === 'market'; });
      modal.querySelector('[data-cancel]').addEventListener('click', closeModal);
      modal.querySelector('[data-submit]').addEventListener('click', async (event) => {
        const symbol = modal.querySelector('#sim-symbol').value.trim();
        const side = modal.querySelector('#sim-side').value;
        const orderType = type.value;
        const quantity = numberValue(modal.querySelector('#sim-qty'));
        const limitPrice = numberValue(modal.querySelector('#sim-limit'));
        const error = modal.querySelector('#sim-create-error');
        error.textContent = '';
        if (!/^\d{6}$/.test(symbol)) { error.textContent = '股票代码必须为 6 位数字。'; return; }
        if (!Number.isInteger(quantity) || quantity <= 0) { error.textContent = '数量必须为正整数。'; return; }
        if (side === 'buy' && quantity % 100 !== 0) { error.textContent = 'A 股买入数量必须为 100 股整数手。'; return; }
        if (orderType === 'limit' && !(limitPrice > 0)) { error.textContent = '限价单必须填写大于 0 的限价。'; return; }
        const intentKey = `create:${symbol}:${side}:${orderType}:${quantity}:${orderType === 'limit' ? limitPrice : 'market'}`;
        const req = requestId(intentKey);
        event.currentTarget.disabled = true;
        try {
          const response = await api.post('/simulation/orders', {
            client_request_id: req.value, symbol, side, order_type: orderType,
            quantity, limit_price: orderType === 'limit' ? limitPrice : null,
          });
          clearRequest(req);
          closeModal();
          await load({ quiet: true });
          const order = response?.order;
          toast(order?.status === 'rejected' ? `订单被风控拒绝：${formatReason(order.rejection_reason)}` : '订单已进入待人工批准', order?.status === 'rejected' ? 'error' : 'success', 5200);
        } catch (requestError) {
          if (requestError.status === 409) { clearRequest(req); await load({ quiet: true }); }
          error.textContent = `提交失败：${requestError.message}`;
          event.currentTarget.disabled = false;
        }
      });
      modal.querySelector('#sim-symbol').focus();
    },
  });
}

function openApproval(order) {
  openModal({
    title: '人工批准模拟订单',
    body: `<div class="field"><label class="field-label" for="sim-approver">批准人</label><input class="input" id="sim-approver" maxlength="120" autocomplete="name" placeholder="填写真实操作人姓名"></div>
      <div class="sim-form-note" style="margin-top:12px">批准只改变本地模拟账本；不会连接券商。AI、Agent、bot、system 或 Hermes 不能作为批准人。</div>
      <div class="field-hint" id="sim-approve-error" role="alert" style="color:var(--up);min-height:20px;margin-top:8px"></div>`,
    footer: '<button class="btn btn-ghost" data-cancel>取消</button><button class="btn btn-primary" data-submit>继续确认</button>',
    onMount(modal) {
      modal.querySelector('[data-cancel]').addEventListener('click', closeModal);
      modal.querySelector('[data-submit]').addEventListener('click', async () => {
        const approvedBy = modal.querySelector('#sim-approver').value.trim();
        const error = modal.querySelector('#sim-approve-error');
        if (!approvedBy) { error.textContent = '必须填写可识别的人工批准人。'; return; }
        closeModal();
        const ok = await confirmModal('确认人工批准', `确认由「${approvedBy}」批准 ${order.symbol} 的本地模拟订单？这不会产生真实委托。`, { okText: '确认批准' });
        if (!ok) return;
        await mutateOrder('approve', order, { approved_by: approvedBy });
      });
      modal.querySelector('#sim-approver').focus();
    },
  });
}

async function mutateOrder(action, order, extra = {}) {
  const req = requestId(`${action}:${order.order_id}`);
  try {
    const response = await api.post(`/simulation/orders/${encodeURIComponent(order.order_id)}/${action}`, {
      client_request_id: req.value, ...extra,
    });
    clearRequest(req);
    await load({ quiet: true });
    const next = response?.order;
    const failed = next?.status === 'rejected';
    toast(failed ? `模拟成交被拒绝：${formatReason(next.rejection_reason)}` : (action === 'approve' ? '已人工批准' : action === 'fill' ? '已按服务端行情模拟成交' : '已取消'), failed ? 'error' : 'success', 5200);
  } catch (error) {
    if (error.status === 409) { clearRequest(req); await load({ quiet: true }); }
    toast(`${action === 'approve' ? '批准' : action === 'fill' ? '成交' : '取消'}失败：${error.message}`, 'error');
  }
}

function openEmergencyStop() {
  openModal({
    title: '开启紧急停止',
    body: `<div class="field"><label class="field-label" for="sim-stop-reason">停止原因</label><textarea class="input" id="sim-stop-reason" maxlength="240" rows="3" placeholder="例如：行情时钟异常 / 数据源不可核验"></textarea></div>
      <div class="sim-form-note" style="margin-top:12px">开启后会立即取消全部待批准和已批准订单，并阻止新订单。恢复必须由人工再次复核。</div>`,
    footer: '<button class="btn btn-ghost" data-cancel>取消</button><button class="btn btn-danger" data-submit>继续确认</button>',
    onMount(modal) {
      modal.querySelector('[data-cancel]').addEventListener('click', closeModal);
      modal.querySelector('[data-submit]').addEventListener('click', async () => {
        const reason = modal.querySelector('#sim-stop-reason').value.trim();
        if (!reason) { toast('必须填写紧急停止原因', 'error'); return; }
        closeModal();
        const ok = await confirmModal('确认紧急停止', '这会取消所有待处理模拟订单并持续阻断新订单。确认立即执行？', { danger: true, okText: '立即停止' });
        if (!ok) return;
        const req = requestId('emergency-stop');
        try {
          await api.post('/simulation/emergency-stop', { client_request_id: req.value, reason });
          clearRequest(req); await load({ quiet: true }); toast('紧急停止已开启', 'error', 5000);
        } catch (error) { if (error.status === 409) clearRequest(req); toast(`开启失败：${error.message}`, 'error'); }
      });
      modal.querySelector('#sim-stop-reason').focus();
    },
  });
}

function openEmergencyReset() {
  openModal({
    title: '人工复核后恢复',
    body: `<div class="sim-form-grid"><div class="field full"><label class="field-label" for="sim-reset-approver">复核人</label><input class="input" id="sim-reset-approver" maxlength="120" placeholder="填写真实操作人姓名"></div>
      <div class="field full"><label class="field-label" for="sim-reset-reason">恢复原因</label><textarea class="input" id="sim-reset-reason" maxlength="240" rows="3" placeholder="说明异常已经如何排除、证据如何复核"></textarea></div>
      <div class="sim-form-note full">恢复只解除本地模拟账本的停止状态，不会恢复已取消订单，也不会连接券商。</div></div>`,
    footer: '<button class="btn btn-ghost" data-cancel>取消</button><button class="btn" data-submit>继续确认</button>',
    onMount(modal) {
      modal.querySelector('[data-cancel]').addEventListener('click', closeModal);
      modal.querySelector('[data-submit]').addEventListener('click', async () => {
        const approvedBy = modal.querySelector('#sim-reset-approver').value.trim();
        const reason = modal.querySelector('#sim-reset-reason').value.trim();
        if (!approvedBy || !reason) { toast('必须填写人工复核人与恢复原因', 'error'); return; }
        closeModal();
        const ok = await confirmModal('确认解除紧急停止', `确认由「${approvedBy}」完成复核并恢复模拟执行？已取消订单不会恢复。`, { okText: '确认恢复' });
        if (!ok) return;
        const req = requestId('emergency-reset');
        try {
          await api.post('/simulation/emergency-stop/reset', { client_request_id: req.value, approved_by: approvedBy, reason });
          clearRequest(req); await load({ quiet: true }); toast('紧急停止已由人工复核解除', 'success');
        } catch (error) { if (error.status === 409) clearRequest(req); toast(`恢复失败：${error.message}`, 'error'); }
      });
      modal.querySelector('#sim-reset-approver').focus();
    },
  });
}

export function mount(container) {
  generation += 1;
  root = container;
  status = null;
  orders = [];
  injectStyle();
  container.innerHTML = `
    <div class="page-head">
      <h2 class="page-title">模拟执行</h2>
      <span class="page-sub">把建议变成可审计订单 · 人工批准 · 服务端行情 · 不连接券商</span>
      <div class="ph-extra"><button class="btn btn-sm" id="sim-refresh">${icon('refresh')}刷新账本</button><button class="btn btn-primary" id="sim-create">${icon('plus')}新建模拟订单</button></div>
    </div>
    <div class="sim-boundary">
      <section class="sim-hero"><div class="sim-kicker">${icon('info')} SIMULATION ONLY</div><h3>只验证决策与执行约束，不产生真实委托</h3><p>系统不会读取券商账户，也没有券商接口。前端只提交股票、方向、数量和限价；风险、参考价、成交价与行情时点全部由服务端决定。</p><div class="sim-rule-list"><div class="sim-rule"><strong>服务端定价</strong>客户端价格字段无效</div><div class="sim-rule"><strong>必须人工批准</strong>自动化身份不能放行</div><div class="sim-rule"><strong>全程可追溯</strong>每次状态变化追加留痕</div></div></section>
      <section class="sim-stop-card" id="sim-stop-card"><div class="sim-stop-title"><span id="sim-stop-state">读取状态中</span><span class="sim-status">无券商连接</span></div><div class="sim-stop-copy" id="sim-stop-copy">正在读取模拟账本状态…</div><div class="sim-stop-actions"><button class="btn btn-danger" id="sim-stop">${icon('stop')}紧急停止</button><button class="btn" id="sim-reset" hidden>${icon('play')}人工复核后恢复</button></div></section>
    </div>
    <div class="sim-counts" id="sim-counts"></div>
    <section class="card"><div class="card-head"><h3 class="card-title">模拟订单与事件链</h3><span style="font-size:12px;color:var(--text-2)">订单状态以服务端账本为准</span></div><div class="card-body" id="sim-orders"></div></section>`;
  container.querySelector('#sim-refresh').addEventListener('click', async () => { await load(); toast('模拟账本已刷新', 'success'); });
  container.querySelector('#sim-create').addEventListener('click', openCreate);
  container.querySelector('#sim-stop').addEventListener('click', openEmergencyStop);
  container.querySelector('#sim-reset').addEventListener('click', openEmergencyReset);
  container.querySelector('#sim-orders').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const order = orders.find((item) => item.order_id === button.dataset.id);
    if (!order) return;
    if (button.dataset.action === 'approve') { openApproval(order); return; }
    if (button.dataset.action === 'fill') {
      const ok = await confirmModal('确认模拟成交', `将按服务端重新读取的 ${order.symbol} 行情执行模拟成交，不连接券商。继续吗？`, { okText: '模拟成交' });
      if (ok) await mutateOrder('fill', order);
      return;
    }
    if (button.dataset.action === 'cancel') {
      const ok = await confirmModal('取消模拟订单', `确认取消 ${order.symbol} 的模拟订单？`, { danger: true, okText: '确认取消' });
      if (ok) await mutateOrder('cancel', order, { reason: 'operator_cancelled_from_web' });
    }
  });
  load();
}

export function unmount() {
  generation += 1;
  closeModal();
  root = null;
  status = null;
  orders = [];
}
