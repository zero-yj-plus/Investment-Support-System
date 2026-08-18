<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import type { HoldingDraft, HoldingPeriod, NewHoldingInput, TargetMode } from '../types/holdings'
import { finiteNumber, formatMoney } from '../utils/holdingsFormat'

const props = defineProps<{
  adding: boolean
  serverError: string
  mode?: 'add' | 'replan'
  initial?: HoldingDraft | null
}>()

const emit = defineEmits<{
  submit: [input: NewHoldingInput]
  cancel: []
}>()

const code = ref(props.initial?.code || '')
const cost = ref(props.initial?.cost == null ? '' : String(props.initial.cost))
const shares = ref(props.initial?.shares == null ? '' : String(props.initial.shares))
const buyDate = ref(props.initial?.riskPlan?.buy_date || '')
const period = ref<HoldingPeriod>(props.initial?.riskPlan?.period || 'short')
const customerRiskPrice = ref(props.initial?.riskPlan?.customer_risk_price == null ? '' : String(props.initial.riskPlan.customer_risk_price))
const customerTargetPrice = ref(props.initial?.riskPlan?.customer_target_price == null ? '' : String(props.initial.riskPlan.customer_target_price))
const targetMode = ref<TargetMode>(props.initial?.riskPlan?.target_mode || 'reminder')
const fees = ref('')
const note = ref(props.initial?.note || '')
const localError = ref('')
const codeInput = ref<HTMLInputElement | null>(null)

function expectedCost(): string {
  const parsedCost = finiteNumber(cost.value)
  const parsedShares = finiteNumber(shares.value)
  return parsedCost != null && parsedCost > 0 && parsedShares != null && parsedShares > 0
    ? formatMoney(parsedCost * parsedShares)
    : '—'
}

function submit() {
  localError.value = ''
  const normalizedCode = code.value.replace(/\D/g, '')
  const parsedCost = finiteNumber(cost.value)
  const parsedShares = finiteNumber(shares.value)
  const parsedRiskPrice = finiteNumber(customerRiskPrice.value)
  const parsedTargetPrice = finiteNumber(customerTargetPrice.value)
  const parsedFees = finiteNumber(fees.value)
  if (!/^\d{6}$/.test(normalizedCode)) {
    localError.value = '股票代码必须是 6 位数字。'
    void nextTick(() => codeInput.value?.focus())
    return
  }
  if (parsedCost == null || parsedCost <= 0) {
    localError.value = '成本价必须是大于 0 的有效金额。'
    return
  }
  if (parsedShares == null || parsedShares <= 0 || !Number.isInteger(parsedShares)) {
    localError.value = '股数必须是大于 0 的整数。'
    return
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(buyDate.value)) {
    localError.value = '请选择买入日期。'
    return
  }
  if (parsedRiskPrice != null && parsedRiskPrice <= 0) {
    localError.value = '客户风险线必须大于 0，或留空。'
    return
  }
  if (parsedTargetPrice != null && parsedTargetPrice <= 0) {
    localError.value = '客户目标价必须大于 0，或留空。'
    return
  }
  if (parsedFees != null && parsedFees < 0) {
    localError.value = '费用不能小于 0，或留空。'
    return
  }
  emit('submit', {
    code: normalizedCode, cost: parsedCost, shares: parsedShares,
    buyDate: buyDate.value, period: period.value,
    customerRiskPrice: parsedRiskPrice, customerTargetPrice: parsedTargetPrice,
    targetMode: targetMode.value, fees: parsedFees, note: note.value.trim(),
  })
}

onMounted(() => codeInput.value?.focus())
</script>

<template>
  <section class="panel holding-editor" aria-labelledby="holding-editor-title">
    <header>
      <div><p class="eyebrow">{{ props.mode === 'replan' ? '重置风险计划' : '新增账本条目' }}</p><h2 id="holding-editor-title">{{ props.mode === 'replan' ? '重新规划持仓' : '添加持仓' }}</h2></div>
      <button type="button" class="secondary-button" :disabled="adding" @click="emit('cancel')">取消</button>
    </header>
    <form @submit.prevent="submit">
      <div class="holding-editor__fields">
        <label>
          <span>股票代码</span>
          <input ref="codeInput" v-model="code" :disabled="props.mode === 'replan'" required inputmode="numeric" maxlength="6" autocomplete="off" aria-describedby="holding-code-hint" />
          <small id="holding-code-hint">提交前通过真实单票接口核验</small>
        </label>
        <label>
          <span>成本价（元/股）</span>
          <input v-model="cost" required inputmode="decimal" autocomplete="off" />
          <small>必须大于 0</small>
        </label>
        <label>
          <span>持有股数（股）</span>
          <input v-model="shares" required inputmode="numeric" autocomplete="off" />
          <small>必须为正整数</small>
        </label>
        <label>
          <span>买入日期</span>
          <input v-model="buyDate" required type="date" autocomplete="off" />
          <small>买入日计为第1个交易日</small>
        </label>
        <label>
          <span>持仓周期</span>
          <select v-model="period" required>
            <option value="ultra">超短（1–3个交易日）</option>
            <option value="short">短线（3–5个交易日）</option>
            <option value="swing">波段（5–10个交易日）</option>
          </select>
          <small>默认短线；到期后持续显示</small>
        </label>
        <label>
          <span>客户风险线（可选）</span>
          <input v-model="customerRiskPrice" inputmode="decimal" autocomplete="off" />
          <small>实时触及会建议降低100%</small>
        </label>
        <label>
          <span>客户目标价（可选）</span>
          <input v-model="customerTargetPrice" inputmode="decimal" autocomplete="off" />
          <small>默认只提醒</small>
        </label>
        <label>
          <span>目标价处理</span>
          <select v-model="targetMode">
            <option value="reminder">只提醒，不计入触发</option>
            <option value="ordinary_trigger">计入一次普通触发</option>
          </select>
          <small>单独触发时建议降低25%</small>
        </label>
        <label v-if="props.mode !== 'replan'">
          <span>本次费用（可选）</span>
          <input v-model="fees" inputmode="decimal" autocomplete="off" />
          <small>重复代码补仓时计入综合成本</small>
        </label>
        <label class="holding-editor__note">
          <span>备注（可选）</span>
          <input v-model="note" maxlength="200" autocomplete="off" />
          <small>只保存用户输入</small>
        </label>
      </div>
      <div class="holding-editor__footer">
        <p>预计持仓成本：<strong class="mono">{{ expectedCost() }}</strong></p>
        <button type="submit" class="primary-button" :disabled="adding">{{ adding ? '处理中' : props.mode === 'replan' ? '确认重置计划' : '核验并加入' }}</button>
      </div>
      <p v-if="localError || serverError" class="holding-editor__error" role="alert">{{ localError || serverError }}</p>
    </form>
  </section>
</template>

<style scoped>
.holding-editor { overflow: hidden; }
.holding-editor > header { display: flex; min-height: 68px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.holding-editor h2 { margin: 0; font-size: 18px; }
.holding-editor form { padding: 14px; }
.holding-editor__fields { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.holding-editor label { display: grid; min-width: 0; gap: 5px; color: var(--text-2); font-size: 13px; font-weight: 680; }
.holding-editor input, .holding-editor select { width: 100%; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); }
.holding-editor input:focus, .holding-editor select:focus { border-color: var(--focus); }
.holding-editor small { color: var(--text-3); font-size: 12px; font-weight: 500; }
.holding-editor__footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border); }
.holding-editor__footer p { margin: 0; color: var(--text-2); }
.holding-editor__footer strong { color: var(--text); font-size: 17px; }
.holding-editor__error { margin: 10px 0 0; color: var(--danger); }
@media (max-width: 900px) {
  .holding-editor__fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 520px) {
  .holding-editor__fields { grid-template-columns: minmax(0, 1fr); }
  .holding-editor__footer { align-items: stretch; flex-direction: column; }
  .holding-editor__footer .primary-button { width: 100%; }
}
</style>
