<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import SimulationActionDialog from './SimulationActionDialog.vue'
import SimulationEquityCurve from './SimulationEquityCurve.vue'
import { useSimulation } from '../composables/useSimulation'
import type {
  SimulationAccountPayload,
  SimulationDialogMode,
  SimulationDialogPayload,
  SimulationOrder,
  SimulationOrderIntent,
  SimulationPosition,
  SimulationOrderStatus,
} from '../types/simulation'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  status, account, orders, loading, loaded, loadError, actionError, feedback,
  actionBusy, pageBusy, load, propose, cancel, emergencyStop,
  resetEmergencyStop, initializeAccount, updateAccountSettings, recordValuation,
  clearActionMessages,
} = useSimulation()

const dialogMode = ref<SimulationDialogMode | null>(null)
const dialogOrder = ref<SimulationOrder | null>(null)
const dialogIntent = ref<Partial<SimulationOrderIntent> | null>(null)
const emergencyActive = computed(() => status.value?.emergency_stop?.active === true)
const accountInitialized = computed(() => account.value?.initialized === true)
const statusRows: Array<{ key: SimulationOrderStatus; label: string }> = [
  { key: 'proposed', label: '旧挂单迁移中' }, { key: 'approved', label: '挂单中' },
  { key: 'filled', label: '已模拟成交' }, { key: 'rejected', label: '已拒绝' },
  { key: 'cancelled', label: '已取消' },
]
const statusLabels = Object.fromEntries(statusRows.map((row) => [row.key, row.label])) as Record<SimulationOrderStatus, string>
const reasonLabels: Record<string, string> = {
  invalid_symbol: '股票代码无效', invalid_side: '买卖方向无效', invalid_order_type: '订单类型无效',
  invalid_quantity: '数量无效', buy_quantity_not_board_lot_100: '买入数量必须为 100 股整数手',
  invalid_reference_price: '服务端现价缺失', invalid_limit_price: '限价无效',
  market_order_cannot_have_limit_price: '市价单不能带限价', risk_level_missing_or_invalid: '服务端风险等级缺失',
  risk_level_gte_4: '当前风险等级禁止新增买入', data_stale: '盘中实时行情已经过期', data_missing: '关键行情证据缺失',
  data_as_of_missing_or_invalid: '行情时点缺失', data_as_of_future: '行情时点异常',
  invalid_market_price: '服务端成交价缺失', market_as_of_missing_or_invalid: '成交行情时点缺失',
  market_as_of_future: '成交行情时点异常', market_data_stale: '盘中成交行情已经过期',
  market_data_missing: '成交行情证据缺失', real_market_source_required: '成交行情来源不可核验',
  account_not_initialized: '请先创建模拟账户并设置初始资金', insufficient_cash: '模拟账户可用资金不足',
  insufficient_position: '模拟账户持仓数量不足', t1_position_locked: '当日买入仓位受 T+1 限制，当前不可卖',
  trade_date_missing_or_invalid: '服务端交易日期缺失', server_close_trade_date: '收盘价没有可核验的交易日期',
  emergency_stop_active: '紧急停止已开启', operator_cancelled_from_vue: '操作人从 Vue 页面取消',
  day_order_expired: '当日挂单收盘后自动撤单', valid_trade_date_missing_or_invalid: '挂单有效交易日无效',
}
const eventLabels: Record<string, string> = {
  submitted: '已提交', queued: '已进入撮合池', proposed: '旧流程已提交', approved: '旧流程已批准',
  filled: '已成交', rejected: '已拒绝', cancelled: '已取消', expired: '当日到期撤单',
}

watch(pageBusy, (value) => emit('refreshState', value), { immediate: true })

function formatMoney(value: number | null | undefined, missing = '金额缺失'): string {
  return Number.isFinite(value) ? `${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元` : missing
}
function formatPrice(value: number | null | undefined): string { return formatMoney(value, '价格缺失') }
function formatPct(value: number | null | undefined): string { return Number.isFinite(value) ? `${Number(value).toFixed(2)}%` : '收益率缺失' }
function formatTime(value: string | null | undefined): string {
  if (!value) return '时点缺失'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}
function formatReason(value: string | null | undefined): string {
  if (!value) return '未记录拒绝或取消原因'
  return String(value).split(';').filter(Boolean).map((reason) => reasonLabels[reason] || reason).join('；')
}
function formatEvent(value: string): string { return eventLabels[value] || value }
function pnlClass(value: number | null | undefined) { return { 'is-profit': Number(value) > 0, 'is-loss': Number(value) < 0 } }
function priceMode(mode: string | null | undefined): string { return mode === 'latest_close' ? '最近交易日收盘价' : mode === 'live' ? '盘中实时价' : '价格模式缺失' }

function openDialog(
  mode: SimulationDialogMode,
  order: SimulationOrder | null = null,
  intent: Partial<SimulationOrderIntent> | null = null,
) {
  clearActionMessages(); dialogMode.value = mode; dialogOrder.value = order; dialogIntent.value = intent
}
function openSellDialog(position: SimulationPosition) {
  openDialog('create', null, {
    symbol: position.symbol,
    side: 'sell',
    order_type: 'limit',
    quantity: position.available_quantity,
    limit_price: Number.isFinite(position.market_price) ? position.market_price : null,
  })
}
function closeDialog() {
  if (!actionBusy.value) { dialogMode.value = null; dialogOrder.value = null; dialogIntent.value = null }
}
async function submitDialog(payload: SimulationDialogPayload) {
  let success = false
  if (dialogMode.value === 'account-create') success = await initializeAccount(payload as SimulationAccountPayload)
  else if (dialogMode.value === 'account-settings') success = await updateAccountSettings(payload as SimulationAccountPayload)
  else if (dialogMode.value === 'create') success = await propose(payload as SimulationOrderIntent)
  else if (dialogMode.value === 'cancel' && dialogOrder.value) success = await cancel(dialogOrder.value)
  else if (dialogMode.value === 'emergency-stop' && payload.reason) success = await emergencyStop(payload.reason)
  else if (dialogMode.value === 'emergency-reset' && payload.approved_by && payload.reason) success = await resetEmergencyStop(payload.approved_by, payload.reason)
  if (success) closeDialog()
}

onBeforeUnmount(() => emit('refreshState', false))
defineExpose({ refresh: load })
</script>

<template>
  <section class="simulation-page" aria-labelledby="simulation-page-title" :aria-busy="pageBusy">
    <header class="simulation-header">
      <div class="page-heading">
        <h1 id="simulation-page-title">模拟执行</h1>
        <p class="page-subtitle">提交即进入服务端撮合池；每 5 秒检查真实行情，触价后自动成交并更新资金、成本与持仓。</p>
      </div>
      <button v-if="accountInitialized" type="button" class="primary-button" :disabled="loading || !!loadError || emergencyActive || !!actionBusy" @click="openDialog('create')">新建模拟订单</button>
      <button v-else type="button" class="primary-button" :disabled="loading || !!loadError || !!actionBusy" @click="openDialog('account-create')">创建模拟账户</button>
    </header>

    <div class="sr-only" aria-live="polite">
      {{ pageBusy ? '模拟执行页面正在处理请求' : feedback || actionError || loadError || '模拟执行账本已就绪' }}
    </div>

    <div v-if="loading" class="simulation-loading" role="status" aria-live="polite">
      <span class="sr-only">模拟执行账本正在加载</span>
      <section class="panel simulation-skeleton"></section>
      <section class="panel simulation-skeleton is-table"></section>
    </div>

    <template v-else>
      <section v-if="loadError && !loaded" class="panel simulation-fatal" role="alert">
        <h2>模拟执行账本暂不可用</h2>
        <p>{{ loadError }}</p>
        <p>页面不会在账本读取失败时构造订单、价格或券商状态。</p>
        <button type="button" class="primary-button" :disabled="pageBusy" @click="load">重试加载</button>
      </section>

      <template v-else>
        <div v-if="loadError" class="simulation-feedback is-error" role="alert"><strong>刷新失败</strong><span>{{ loadError }}；保留上次成功读取的账本。</span></div>
        <div v-if="feedback || (actionError && !dialogMode)" class="simulation-feedback" :class="{ 'is-error': actionError }" :role="actionError ? 'alert' : 'status'">
          <strong>{{ actionError ? '操作未完成' : '账本状态' }}</strong><span>{{ actionError || feedback }}</span>
        </div>

        <section v-if="!accountInitialized" class="panel simulation-account-empty" aria-labelledby="simulation-account-empty-title">
          <p class="simulation-kicker">FULL SIMULATED ACCOUNT</p>
          <h2 id="simulation-account-empty-title">先建立独立的本地模拟账户</h2>
          <p>设置初始资金、佣金、印花税、过户费和模拟滑点后，成交才会同步计算现金、T+1 持仓、盈亏与资产曲线。该账户与“我的持仓”数据完全分离。</p>
          <button type="button" class="primary-button" :disabled="!!actionBusy" @click="openDialog('account-create')">设置初始资金与费用</button>
        </section>

        <template v-else>
          <section class="simulation-account-summary" aria-label="模拟账户资产摘要">
            <article class="panel"><span>总资产</span><strong :class="pnlClass(account?.total_pnl)">{{ formatMoney(account?.total_assets, '持仓估值缺失') }}</strong><small>{{ account?.valuation_state === 'missing' ? `缺少 ${account?.missing_symbols?.join('、') || '持仓'} 行情` : `收益率 ${formatPct(account?.return_pct)}` }}</small></article>
            <article class="panel"><span>可用资金</span><strong>{{ formatMoney(account?.cash) }}</strong><small>初始 {{ formatMoney(account?.initial_cash) }}</small></article>
            <article class="panel"><span>持仓市值</span><strong>{{ formatMoney(account?.market_value, '估值缺失') }}</strong><small>{{ account?.positions.length || 0 }} 个持仓标的</small></article>
            <article class="panel"><span>累计盈亏</span><strong :class="pnlClass(account?.total_pnl)">{{ formatMoney(account?.total_pnl, '盈亏缺失') }}</strong><small>已实现 {{ formatMoney(account?.realized_pnl) }}</small></article>
            <article class="panel"><span>累计费用</span><strong>{{ formatMoney(account?.total_fees) }}</strong><small>逐笔按成交时设置留痕</small></article>
          </section>

          <div class="simulation-account-grid">
            <section class="panel simulation-positions" aria-labelledby="simulation-positions-title">
              <header>
                <div><h2 id="simulation-positions-title">模拟持仓</h2><p>A 股 T+1：当日买入批次要到下一交易日才能卖出。</p></div>
                <button type="button" class="secondary-button" :disabled="!!actionBusy" @click="openDialog('account-settings')">费用设置</button>
              </header>
              <div v-if="!account?.positions.length" class="simulation-positions__empty">暂无持仓。完成第一笔买入成交后显示。</div>
              <div v-else class="simulation-position-list">
                <article v-for="position in account.positions" :key="position.symbol" class="simulation-position-row">
                  <div class="simulation-position-row__identity">
                    <strong class="mono">{{ position.symbol }}</strong>
                    <span>{{ priceMode(position.price_mode) }}</span>
                    <small v-if="position.t1_locked_quantity > 0">{{ position.t1_locked_quantity }} 股 T+1 冻结<span v-if="position.next_sellable_date"> · {{ position.next_sellable_date }} 可卖</span></small>
                  </div>
                  <dl>
                    <div><dt>持仓 / 可卖</dt><dd>{{ position.quantity }} / {{ position.available_quantity }} 股</dd></div>
                    <div><dt>平均成本</dt><dd>{{ formatPrice(position.average_cost) }}</dd></div>
                    <div><dt>现价</dt><dd>{{ formatPrice(position.market_price) }}</dd></div>
                    <div><dt>浮动盈亏</dt><dd :class="pnlClass(position.unrealized_pnl)">{{ formatMoney(position.unrealized_pnl, '估值缺失') }}</dd></div>
                  </dl>
                  <button
                    type="button"
                    class="secondary-button simulation-sell-button"
                    :disabled="position.available_quantity <= 0 || !!actionBusy || emergencyActive"
                    :aria-label="`${position.symbol} 卖出`"
                    :title="position.available_quantity <= 0 ? '当前没有可卖数量' : emergencyActive ? '紧急停止期间不能新建挂单' : `卖出 ${position.symbol}`"
                    @click="openSellDialog(position)"
                  >卖出</button>
                </article>
              </div>
            </section>

            <section class="panel simulation-equity" aria-labelledby="simulation-equity-title">
              <header>
                <div><h2 id="simulation-equity-title">资产曲线</h2><p>从账户创建开始向前记录，不回填历史成交。</p></div>
                <button type="button" class="secondary-button" :disabled="!!actionBusy" @click="recordValuation">记录当前估值</button>
              </header>
              <SimulationEquityCurve :points="account?.equity_curve || []" />
            </section>
          </div>
        </template>

        <section class="panel simulation-stop" :class="{ 'is-active': emergencyActive }" aria-labelledby="simulation-stop-title">
          <div class="simulation-stop__copy">
            <div class="simulation-stop__title">
              <h2 id="simulation-stop-title">{{ emergencyActive ? '紧急停止已开启' : '紧急停止未开启' }}</h2>
              <span>无券商连接</span>
            </div>
            <p v-if="emergencyActive">原因：{{ status?.emergency_stop.reason || '原因未记录' }}。新订单与自动撮合已被阻断。</p>
            <p v-else>挂单由服务端自动撮合，不会发送到券商；账户执行 A 股 T+1。</p>
          </div>
          <button v-if="emergencyActive" type="button" class="secondary-button" :disabled="!!actionBusy" @click="openDialog('emergency-reset')">人工复核后恢复</button>
          <button v-else type="button" class="danger-button" :disabled="!!actionBusy" @click="openDialog('emergency-stop')">紧急停止</button>
        </section>

        <section class="simulation-counts" aria-label="模拟订单状态数量">
          <article v-for="row in statusRows" :key="row.key" class="panel">
            <strong>{{ status?.counts?.[row.key] ?? 0 }}</strong><span>{{ row.label }}</span>
          </article>
        </section>

        <section class="panel simulation-ledger" aria-labelledby="simulation-ledger-title">
          <header><div><h2 id="simulation-ledger-title">模拟订单与事件链</h2><p>订单状态以服务端本地账本为准，最多显示最近 100 笔。</p></div></header>

          <div v-if="!orders.length" class="simulation-empty" role="status">
            <h3>当前没有模拟订单</h3>
            <p>新建订单后，服务端核验风险与行情；有效订单直接进入撮合池，触价后自动成交。</p>
            <button type="button" class="secondary-button" :disabled="!accountInitialized || emergencyActive || !!actionBusy" @click="openDialog('create')">新建第一笔模拟订单</button>
          </div>

          <template v-else>
            <div class="simulation-table-wrap">
              <table>
                <thead><tr><th scope="col">状态</th><th scope="col">股票 / 方向</th><th scope="col">数量 / 类型</th><th scope="col">订单价格</th><th scope="col">风险 / 数据</th><th scope="col">成交</th><th scope="col">原因 / 事件</th><th scope="col">操作</th></tr></thead>
                <tbody>
                  <tr v-for="order in orders" :key="order.order_id">
                    <td><span class="simulation-status" :class="`is-${order.status}`">{{ statusLabels[order.status] || order.status }}</span></td>
                    <td><strong class="mono">{{ order.symbol }}</strong><span>{{ order.side === 'buy' ? '买入' : '卖出' }}</span></td>
                    <td><strong class="mono">{{ order.quantity }} 股</strong><span>{{ order.order_type === 'limit' ? '限价单' : '市价单' }}</span></td>
                    <td><strong class="mono">参考 {{ formatPrice(order.reference_price) }}</strong><span>{{ order.order_type === 'limit' ? `限价 ${formatPrice(order.limit_price)}` : '按服务端现价' }}</span></td>
                    <td><strong>{{ order.risk_level == null ? '风险等级缺失' : `风险 ${order.risk_level} 级` }}</strong><span :class="{ 'is-warning': order.data_stale && order.price_mode !== 'latest_close' }">{{ priceMode(order.price_mode) }} · {{ formatTime(order.data_as_of) }}</span></td>
                    <td><strong class="mono">{{ order.market_price == null ? '尚未成交' : formatPrice(order.market_price) }}</strong><span>{{ order.total_fee == null ? '费用未产生' : `费用 ${formatMoney(order.total_fee)}` }}</span></td>
                    <td class="simulation-reason"><span>{{ formatReason(order.rejection_reason) }}</span><details v-if="order.events?.length"><summary>查看 {{ order.events.length }} 条事件</summary><ol><li v-for="event in order.events" :key="event.event_id"><b>{{ formatEvent(event.event_type) }}</b><time>{{ formatTime(event.created_at) }}</time></li></ol></details></td>
                    <td><div class="simulation-actions"><button v-if="order.status === 'proposed' || order.status === 'approved'" type="button" class="secondary-button" :disabled="!!actionBusy" @click="openDialog('cancel', order)">取消挂单</button><span v-if="!['proposed', 'approved'].includes(order.status)">已结束</span></div></td>
                  </tr>
                </tbody>
              </table>
            </div>

            <ul class="simulation-cards" aria-label="模拟订单列表">
              <li v-for="order in orders" :key="order.order_id" class="simulation-order-card">
                <header><div><strong class="mono">{{ order.symbol }}</strong><span>{{ order.side === 'buy' ? '买入' : '卖出' }} · {{ order.quantity }} 股 · {{ order.order_type === 'limit' ? '限价单' : '市价单' }}</span></div><span class="simulation-status" :class="`is-${order.status}`">{{ statusLabels[order.status] }}</span></header>
                <dl><div><dt>参考价格</dt><dd>{{ formatPrice(order.reference_price) }}</dd></div><div><dt>限价</dt><dd>{{ order.order_type === 'limit' ? formatPrice(order.limit_price) : '市价单不设限价' }}</dd></div><div><dt>风险与数据</dt><dd>{{ order.risk_level == null ? '风险等级缺失' : `${order.risk_level} 级` }} · {{ priceMode(order.price_mode) }}</dd></div><div><dt>成交 / 费用</dt><dd>{{ order.market_price == null ? '尚未成交' : `${formatPrice(order.market_price)} / ${formatMoney(order.total_fee)}` }}</dd></div></dl>
                <p v-if="order.rejection_reason" class="simulation-order-card__reason">{{ formatReason(order.rejection_reason) }}</p>
                <details v-if="order.events?.length"><summary>查看 {{ order.events.length }} 条追加式事件</summary><ol><li v-for="event in order.events" :key="event.event_id"><b>{{ formatEvent(event.event_type) }}</b><time>{{ formatTime(event.created_at) }}</time></li></ol></details>
                <footer class="simulation-actions"><button v-if="order.status === 'proposed' || order.status === 'approved'" type="button" class="secondary-button" :disabled="!!actionBusy" @click="openDialog('cancel', order)">取消挂单</button></footer>
              </li>
            </ul>
          </template>
        </section>
      </template>
    </template>

    <SimulationActionDialog
      v-if="dialogMode"
      :key="`${dialogMode}-${dialogOrder?.order_id || dialogIntent?.symbol || 'global'}`"
      :mode="dialogMode"
      :order="dialogOrder"
      :initial-intent="dialogIntent"
      :account="account"
      :busy="!!actionBusy"
      :server-error="actionError"
      @submit="submitDialog"
      @close="closeDialog"
    />
  </section>
</template>

<style scoped>
.simulation-page { display: grid; gap: 14px; }
.simulation-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.simulation-header h1 { margin: 0; font-size: clamp(22px, 2vw, 28px); }
.simulation-header .primary-button { flex: 0 0 auto; }
.simulation-loading { display: grid; gap: 14px; }
.simulation-skeleton { min-height: 220px; background: linear-gradient(90deg, var(--surface) 20%, var(--surface-2) 50%, var(--surface) 80%); background-size: 220% 100%; animation: simulation-pulse 1.4s ease infinite; }
.simulation-skeleton.is-table { min-height: 360px; }
.simulation-fatal, .simulation-empty { display: grid; min-height: 280px; place-content: center; justify-items: start; padding: 24px; }
.simulation-fatal h2, .simulation-empty h3 { margin: 0; }
.simulation-fatal p, .simulation-empty p { max-width: 680px; margin: 8px 0 14px; color: var(--text-2); }
.simulation-feedback { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border: 1px solid color-mix(in srgb, var(--down) 45%, var(--border)); border-radius: var(--radius-sm); background: color-mix(in srgb, var(--down) 7%, var(--surface)); color: var(--text-2); }
.simulation-feedback strong { color: var(--down); }
.simulation-feedback.is-error { border-color: color-mix(in srgb, var(--danger) 50%, var(--border)); background: color-mix(in srgb, var(--danger) 7%, var(--surface)); }
.simulation-feedback.is-error strong { color: var(--danger); }
.simulation-account-empty { display: grid; justify-items: start; gap: 10px; padding: 22px; border-left: 3px solid var(--accent); }
.simulation-account-empty h2 { margin: 0; font-size: 20px; }
.simulation-account-empty > p:not(.simulation-kicker) { max-width: 820px; margin: 0; color: var(--text-2); }
.simulation-account-summary { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
.simulation-account-summary article { display: grid; min-width: 0; gap: 4px; padding: 13px; }
.simulation-account-summary span, .simulation-account-summary small { color: var(--text-3); }
.simulation-account-summary strong { overflow-wrap: anywhere; font-family: "SFMono-Regular", Consolas, monospace; font-size: clamp(17px, 1.5vw, 22px); font-variant-numeric: tabular-nums; }
.simulation-account-grid { display: grid; grid-template-columns: minmax(560px, 1.2fr) minmax(0, 1fr); gap: 14px; }
.simulation-positions, .simulation-equity { min-width: 0; overflow: hidden; }
.simulation-positions > header, .simulation-equity > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 14px; border-bottom: 1px solid var(--border); }
.simulation-positions h2, .simulation-equity h2 { margin: 0; font-size: 18px; }
.simulation-positions header p, .simulation-equity header p { margin: 3px 0 0; color: var(--text-3); font-size: 12px; }
.simulation-positions__empty { display: grid; min-height: 210px; place-content: center; padding: 18px; color: var(--text-3); text-align: center; }
.simulation-position-list { display: grid; padding: 0 14px; }
.simulation-position-row { display: grid; grid-template-columns: minmax(120px, .8fr) minmax(0, 2.6fr) auto; align-items: center; gap: 16px; min-height: 82px; padding: 12px 0; border-bottom: 1px solid var(--border); }
.simulation-position-row:last-child { border-bottom: 0; }
.simulation-position-row__identity { display: grid; min-width: 0; gap: 2px; }
.simulation-position-row__identity > span, .simulation-position-row__identity > small { color: var(--text-3); font-size: 12px; overflow-wrap: anywhere; }
.simulation-position-list dl { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 0; }
.simulation-position-list dt { color: var(--text-3); font-size: 12px; }
.simulation-position-list dd { margin: 2px 0 0; overflow-wrap: anywhere; font-size: 13px; }
.simulation-sell-button { min-width: 72px; min-height: 44px; }
.simulation-equity > :deep(.equity-chart) { padding: 10px 14px 14px; }
.is-profit { color: var(--down) !important; }
.is-loss { color: var(--danger) !important; }
.simulation-kicker { margin: 0; color: var(--accent); font-size: 12px; font-weight: 800; letter-spacing: .08em; }
.simulation-stop h2, .simulation-ledger h2 { margin: 0; font-size: 16px; }
.simulation-ledger header p { margin: 0; color: var(--text-2); font-size: 13px; }
.simulation-stop { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 11px 14px; }
.simulation-stop.is-active { border-color: color-mix(in srgb, var(--danger) 60%, var(--border)); background: color-mix(in srgb, var(--danger) 6%, var(--surface)); }
.simulation-stop__copy { display: grid; min-width: 0; gap: 3px; }
.simulation-stop__title { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.simulation-stop__title span { padding: 3px 7px; border: 1px solid var(--border); border-radius: 999px; color: var(--text-3); font-size: 12px; white-space: nowrap; }
.simulation-stop p { margin: 0; color: var(--text-2); font-size: 13px; }
.simulation-stop button { flex: 0 0 auto; min-height: 44px; }
.simulation-counts { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
.simulation-counts article { padding: 12px; }
.simulation-counts strong { display: block; font-family: "SFMono-Regular", Consolas, monospace; font-size: 22px; }
.simulation-counts span { color: var(--text-3); font-size: 12px; }
.simulation-ledger { overflow: hidden; }
.simulation-ledger > header { padding: 14px; border-bottom: 1px solid var(--border); }
.simulation-table-wrap { max-width: 100%; overflow-x: auto; }
.simulation-table-wrap table { width: 100%; min-width: 1160px; border-collapse: collapse; }
.simulation-table-wrap th, .simulation-table-wrap td { padding: 11px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
.simulation-table-wrap th { color: var(--text-3); font-size: 12px; white-space: nowrap; }
.simulation-table-wrap td { font-size: 13px; }
.simulation-table-wrap td > strong, .simulation-table-wrap td > span { display: block; }
.simulation-table-wrap td > span { margin-top: 3px; color: var(--text-3); }
.simulation-status { display: inline-flex !important; width: fit-content; padding: 3px 8px; border: 1px solid var(--border); border-radius: 999px; font-size: 12px; font-weight: 750; white-space: nowrap; }
.simulation-status.is-proposed { border-color: color-mix(in srgb, var(--warning) 55%, var(--border)); color: var(--warning); }
.simulation-status.is-approved { border-color: color-mix(in srgb, var(--info) 55%, var(--border)); color: var(--info); }
.simulation-status.is-filled { border-color: color-mix(in srgb, var(--down) 55%, var(--border)); color: var(--down); }
.simulation-status.is-rejected, .simulation-status.is-cancelled { color: var(--text-3); }
.simulation-reason { max-width: 250px; }
.simulation-table-wrap details, .simulation-order-card details { margin-top: 6px; }
.simulation-table-wrap summary, .simulation-order-card summary { color: var(--info); cursor: pointer; }
.simulation-table-wrap ol, .simulation-order-card ol { display: grid; gap: 4px; margin: 6px 0 0; padding-left: 20px; }
.simulation-table-wrap li time, .simulation-order-card li time { display: block; color: var(--text-3); font-size: 11px; }
.simulation-actions { display: flex; flex-wrap: wrap; gap: 7px; }
.simulation-actions button { min-height: 44px; padding-inline: 10px; }
.simulation-actions > span { color: var(--text-3); }
.simulation-cards { display: none; margin: 0; padding: 0; list-style: none; }
.mono { font-family: "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }
.is-warning { color: var(--warning) !important; }
@keyframes simulation-pulse { from { background-position: 100% 0; } to { background-position: -100% 0; } }
@media (max-width: 980px) {
  .simulation-account-summary { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .simulation-account-grid { grid-template-columns: minmax(0, 1fr); }
  .simulation-counts { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .simulation-header { align-items: stretch; flex-direction: column; }
  .simulation-header .primary-button { width: 100%; }
  .simulation-account-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .simulation-positions > header, .simulation-equity > header { align-items: stretch; flex-direction: column; }
  .simulation-positions > header button, .simulation-equity > header button { width: 100%; }
  .simulation-position-row { grid-template-columns: minmax(0, 1fr) auto; gap: 10px 12px; }
  .simulation-position-list dl { grid-column: 1 / -1; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .simulation-stop { align-items: stretch; flex-direction: column; }
  .simulation-stop button { width: 100%; }
  .simulation-counts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .simulation-table-wrap { display: none; }
  .simulation-cards { display: grid; gap: 10px; padding: 10px; }
  .simulation-order-card { display: grid; gap: 10px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
  .simulation-order-card > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
  .simulation-order-card > header div { display: grid; }
  .simulation-order-card > header div span { color: var(--text-3); font-size: 12px; }
  .simulation-order-card dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 0; }
  .simulation-order-card dl div { min-width: 0; }
  .simulation-order-card dt { color: var(--text-3); font-size: 12px; }
  .simulation-order-card dd { margin: 2px 0 0; overflow-wrap: anywhere; }
  .simulation-order-card__reason { margin: 0; color: var(--danger); font-size: 13px; }
  .simulation-actions button { flex: 1 1 120px; }
}
@media (max-width: 430px) {
  .simulation-account-summary, .simulation-counts, .simulation-order-card dl { grid-template-columns: minmax(0, 1fr); }
  .simulation-stop__title { align-items: flex-start; flex-direction: column; }
}
@media (prefers-reduced-motion: reduce) {
  .simulation-skeleton { animation: none; }
}
</style>
