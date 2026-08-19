<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type {
  SimulationDialogMode,
  SimulationDialogPayload,
  SimulationAccount,
  SimulationOrder,
  SimulationOrderIntent,
  SimulationOrderSide,
  SimulationOrderType,
} from '../types/simulation'
import StockAutocomplete from './StockAutocomplete.vue'

const props = defineProps<{
  mode: SimulationDialogMode
  order?: SimulationOrder | null
  initialIntent?: Partial<SimulationOrderIntent> | null
  account?: SimulationAccount | null
  busy: boolean
  serverError?: string
}>()
const emit = defineEmits<{
  close: []
  submit: [payload: SimulationDialogPayload]
}>()

const dialog = ref<HTMLDialogElement | null>(null)
const firstField = ref<HTMLInputElement | null>(null)
const stockInput = ref<InstanceType<typeof StockAutocomplete> | null>(null)
const symbol = ref(String(props.initialIntent?.symbol ?? '').split('.', 1)[0].replace(/\D/g, '').slice(0, 6))
const side = ref<SimulationOrderSide>(props.initialIntent?.side ?? 'buy')
const orderType = ref<SimulationOrderType>(props.initialIntent?.order_type ?? 'limit')
const quantity = ref(String(props.initialIntent?.quantity ?? 100))
const limitPrice = ref(props.initialIntent?.limit_price == null ? '' : String(props.initialIntent.limit_price))
const approvedBy = ref('')
const reason = ref('')
const initialCash = ref('1000000')
const commissionRate = ref(String(props.account?.fee_settings?.commission_rate_pct ?? 0.03))
const minimumCommission = ref(String(props.account?.fee_settings?.minimum_commission ?? 5))
const stampDutyRate = ref(String(props.account?.fee_settings?.stamp_duty_rate_pct ?? 0.05))
const transferFeeRate = ref(String(props.account?.fee_settings?.transfer_fee_rate_pct ?? 0.002))
const slippageBps = ref(String(props.account?.fee_settings?.slippage_bps ?? 0))
const localError = ref('')

const isPositionSell = computed(() => props.mode === 'create' && props.initialIntent?.side === 'sell' && Boolean(props.initialIntent.symbol))

const title = computed(() => isPositionSell.value ? `卖出 ${symbol.value}` : ({
  'account-create': '创建完整模拟账户',
  'account-settings': '模拟费用与滑点',
  create: '新建模拟订单',
  cancel: '取消模拟订单',
  'emergency-stop': '开启紧急停止',
  'emergency-reset': '人工复核后恢复',
}[props.mode]))

const submitLabel = computed(() => isPositionSell.value ? '提交卖出挂单' : ({
  'account-create': '创建并锁定初始资金',
  'account-settings': '保存后续成交设置',
  create: '提交订单意图',
  cancel: '确认取消订单',
  'emergency-stop': '立即紧急停止',
  'emergency-reset': '确认人工恢复',
}[props.mode]))

const isDanger = computed(() => props.mode === 'cancel' || props.mode === 'emergency-stop')

function requestClose() {
  if (!props.busy) dialog.value?.close()
}

function handleBackdrop(event: MouseEvent) {
  if (event.target === dialog.value) requestClose()
}

function submit() {
  localError.value = ''
  if (props.mode === 'account-create' || props.mode === 'account-settings') {
    const normalized = {
      initial_cash: Number(initialCash.value),
      commission_rate_pct: Number(commissionRate.value),
      minimum_commission: Number(minimumCommission.value),
      stamp_duty_rate_pct: Number(stampDutyRate.value),
      transfer_fee_rate_pct: Number(transferFeeRate.value),
      slippage_bps: Number(slippageBps.value),
    }
    const values = [
      normalized.commission_rate_pct,
      normalized.minimum_commission,
      normalized.stamp_duty_rate_pct,
      normalized.transfer_fee_rate_pct,
      normalized.slippage_bps,
    ]
    if (props.mode === 'account-create' && (!Number.isFinite(normalized.initial_cash) || normalized.initial_cash <= 0)) {
      localError.value = '初始资金必须是大于 0 的金额。'
      return
    }
    if (values.some((value) => !Number.isFinite(value) || value < 0)) {
      localError.value = '费率、最低佣金和滑点必须是大于或等于 0 的数字。'
      return
    }
    emit('submit', props.mode === 'account-create' ? normalized : {
      commission_rate_pct: normalized.commission_rate_pct,
      minimum_commission: normalized.minimum_commission,
      stamp_duty_rate_pct: normalized.stamp_duty_rate_pct,
      transfer_fee_rate_pct: normalized.transfer_fee_rate_pct,
      slippage_bps: normalized.slippage_bps,
    })
    return
  }
  if (props.mode === 'create') {
    const normalizedSymbol = symbol.value.replace(/\D/g, '')
    const normalizedQuantity = Number(quantity.value)
    const normalizedLimit = limitPrice.value.trim() ? Number(limitPrice.value) : null
    if (!/^\d{6}$/.test(normalizedSymbol)) {
      localError.value = '股票代码必须是 6 位数字。'
      void nextTick(() => stockInput.value?.focus())
      return
    }
    if (!Number.isInteger(normalizedQuantity) || normalizedQuantity <= 0) {
      localError.value = '数量必须是大于 0 的整数。'
      return
    }
    if (side.value === 'buy' && normalizedQuantity % 100 !== 0) {
      localError.value = 'A 股买入数量必须是 100 股整数手。'
      return
    }
    if (orderType.value === 'limit' && (normalizedLimit == null || !Number.isFinite(normalizedLimit) || normalizedLimit <= 0)) {
      localError.value = '限价单必须填写大于 0 的限价。'
      return
    }
    emit('submit', {
      symbol: normalizedSymbol,
      side: side.value,
      order_type: orderType.value,
      quantity: normalizedQuantity,
      limit_price: orderType.value === 'limit' ? normalizedLimit : null,
    })
    return
  }
  if (props.mode === 'emergency-stop') {
    if (!reason.value.trim()) {
      localError.value = '必须填写紧急停止原因。'
      return
    }
    emit('submit', { reason: reason.value.trim() })
    return
  }
  if (props.mode === 'emergency-reset') {
    if (!approvedBy.value.trim() || !reason.value.trim()) {
      localError.value = '必须填写人工复核人与恢复原因。'
      return
    }
    emit('submit', { approved_by: approvedBy.value.trim(), reason: reason.value.trim() })
    return
  }
  emit('submit', {})
}

onMounted(() => {
  dialog.value?.showModal()
  void nextTick(() => {
    if (props.mode === 'create') stockInput.value?.focus()
    else firstField.value?.focus()
  })
})

onBeforeUnmount(() => {
  if (dialog.value?.open) dialog.value.close()
})
</script>

<template>
  <dialog
    ref="dialog"
    class="simulation-dialog"
    :aria-labelledby="`simulation-dialog-title-${mode}`"
    aria-describedby="simulation-dialog-boundary"
    @cancel.prevent="requestClose"
    @click="handleBackdrop"
    @close="emit('close')"
  >
    <form @submit.prevent="submit">
      <header>
        <h2 :id="`simulation-dialog-title-${mode}`">{{ title }}</h2>
        <button type="button" class="secondary-button" :disabled="busy" @click="requestClose">取消</button>
      </header>

      <div class="simulation-dialog__body">
        <p id="simulation-dialog-boundary" class="simulation-dialog__boundary">
          只写本地模拟账本，不连接券商。风险、参考价、成交价和行情时点全部由服务端决定。
        </p>

        <template v-if="mode === 'account-create' || mode === 'account-settings'">
          <p class="simulation-dialog__note">费率字段单位为百分比（%），例如万三佣金填写 0.03；滑点单位为 bp。</p>
          <div class="simulation-dialog__grid">
            <label v-if="mode === 'account-create'" class="simulation-dialog__wide">
              <span>初始资金（元）</span>
              <input ref="firstField" v-model="initialCash" inputmode="decimal" autocomplete="off" required />
            </label>
            <label>
              <span>佣金率（% / 双边）</span>
              <input v-model="commissionRate" inputmode="decimal" autocomplete="off" required />
            </label>
            <label>
              <span>单笔最低佣金（元）</span>
              <input v-model="minimumCommission" inputmode="decimal" autocomplete="off" required />
            </label>
            <label>
              <span>印花税率（% / 仅卖出）</span>
              <input v-model="stampDutyRate" inputmode="decimal" autocomplete="off" required />
            </label>
            <label>
              <span>过户费率（% / 双边）</span>
              <input v-model="transferFeeRate" inputmode="decimal" autocomplete="off" required />
            </label>
            <label class="simulation-dialog__wide">
              <span>模拟滑点（bp）</span>
              <input v-model="slippageBps" inputmode="decimal" autocomplete="off" required />
            </label>
          </div>
          <p v-if="mode === 'account-create'" class="simulation-dialog__note">账户创建后不能直接覆盖初始资金；费用设置可以修改，并且只影响后续成交。</p>
        </template>

        <template v-else-if="mode === 'create'">
          <div class="simulation-dialog__grid">
            <StockAutocomplete
              ref="stockInput"
              v-model="symbol"
              class="simulation-dialog__stock-field"
              input-id="simulation-stock-code"
              label="股票代码"
              placeholder="代码 / 中文 / 拼音 / 首字母"
              required
              :readonly="isPositionSell"
              :aria-invalid="Boolean(localError && !/^\d{6}$/.test(symbol))"
            />
            <label>
              <span>买卖方向</span>
              <select v-model="side" :disabled="isPositionSell"><option value="buy">买入</option><option value="sell">卖出</option></select>
            </label>
            <label>
              <span>订单类型</span>
              <select v-model="orderType"><option value="limit">限价单</option><option value="market">市价单</option></select>
            </label>
            <label>
              <span>数量（股）</span>
              <input v-model="quantity" inputmode="numeric" autocomplete="off" required />
            </label>
            <label v-if="orderType === 'limit'" class="simulation-dialog__wide">
              <span>限价（元）</span>
              <input v-model="limitPrice" inputmode="decimal" autocomplete="off" required placeholder="必须大于 0" />
            </label>
          </div>
          <p v-if="isPositionSell" class="simulation-dialog__note">已预填当前可卖数量与现价；可调整数量和限价。提交后进入撮合池，不会直接成交。</p>
          <p v-else class="simulation-dialog__note">客户端不能提交或覆盖风险等级、现价、行情时点与数据缺口。</p>
        </template>

        <template v-else-if="mode === 'cancel'">
          <p>确认取消 {{ order?.symbol || '该标的' }} 的模拟订单？取消是终态，不能恢复。</p>
        </template>

        <template v-else-if="mode === 'emergency-stop'">
          <p>开启后会立即取消全部挂单，并阻止新订单进入撮合池。</p>
          <label>
            <span>停止原因</span>
            <input ref="firstField" v-model="reason" maxlength="240" autocomplete="off" required placeholder="例如：行情时钟异常" />
          </label>
        </template>

        <template v-else>
          <p>恢复只解除本地模拟账本的停止状态，不恢复此前已取消订单。</p>
          <label>
            <span>人工复核人</span>
            <input ref="firstField" v-model="approvedBy" maxlength="120" autocomplete="name" required placeholder="填写真实操作人姓名" />
          </label>
          <label>
            <span>恢复原因</span>
            <textarea v-model="reason" maxlength="240" rows="3" required placeholder="说明异常如何排除、证据如何复核"></textarea>
          </label>
        </template>

        <p v-if="localError || serverError" class="simulation-dialog__error" role="alert">{{ localError || serverError }}</p>
      </div>

      <footer>
        <span>{{ busy ? '正在等待服务端确认…' : '每次状态变化都会追加到事件链。' }}</span>
        <button type="submit" :class="isDanger ? 'danger-button' : 'primary-button'" :disabled="busy">
          {{ busy ? '处理中' : submitLabel }}
        </button>
      </footer>
    </form>
  </dialog>
</template>

<style scoped>
.simulation-dialog { width: min(680px, calc(100vw - 32px)); max-width: none; max-height: calc(100dvh - 32px); margin: auto; padding: 0; overflow: hidden; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--surface); color: var(--text); }
.simulation-dialog::backdrop { background: color-mix(in srgb, var(--bg) 76%, transparent); }
.simulation-dialog form { display: grid; max-height: calc(100dvh - 32px); grid-template-rows: auto minmax(0, 1fr) auto; }
.simulation-dialog header, .simulation-dialog footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; }
.simulation-dialog header { border-bottom: 1px solid var(--border); }
.simulation-dialog footer { border-top: 1px solid var(--border); }
.simulation-dialog h2 { margin: 0; font-size: 19px; }
.simulation-dialog__body { display: grid; gap: 12px; min-height: 0; overflow-y: auto; padding: 16px; }
.simulation-dialog__body > p { margin: 0; color: var(--text-2); }
.simulation-dialog__boundary, .simulation-dialog__note { padding: 10px 12px; border-left: 3px solid var(--info); background: var(--surface-2); font-size: 13px; }
.simulation-dialog__grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.simulation-dialog__wide { grid-column: 1 / -1; }
.simulation-dialog label { display: grid; min-width: 0; gap: 5px; color: var(--text-2); font-size: 13px; font-weight: 700; }
.simulation-dialog input, .simulation-dialog select, .simulation-dialog textarea { width: 100%; min-height: 44px; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); }
.simulation-dialog textarea { resize: vertical; }
.simulation-dialog__error { color: var(--danger) !important; font-weight: 680; }
.simulation-dialog footer span { color: var(--text-3); font-size: 13px; }
.simulation-dialog button { min-height: 44px; }
@media (max-width: 560px) {
  .simulation-dialog { width: calc(100vw - 16px); max-height: calc(100dvh - 16px); }
  .simulation-dialog form { max-height: calc(100dvh - 16px); }
  .simulation-dialog__grid { grid-template-columns: minmax(0, 1fr); }
  .simulation-dialog__wide { grid-column: auto; }
  .simulation-dialog footer { align-items: stretch; flex-direction: column; }
  .simulation-dialog footer button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .simulation-dialog, .simulation-dialog * { scroll-behavior: auto !important; }
}
</style>
