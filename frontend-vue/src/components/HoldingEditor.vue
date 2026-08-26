<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { HoldingDraft, NewHoldingInput } from '../types/holdings'
import { finiteNumber, formatMoney } from '../utils/holdingsFormat'

const props = defineProps<{
  open: boolean
  mode: 'add' | 'edit'
  holding: HoldingDraft | null
  busy: boolean
  serverError: string
}>()

const emit = defineEmits<{
  submit: [input: NewHoldingInput]
  close: []
  remove: []
}>()

const dialog = ref<HTMLDialogElement | null>(null)
const codeInput = ref<HTMLInputElement | null>(null)
const code = ref('')
const cost = ref('')
const shares = ref('')
const horizon = ref<NewHoldingInput['horizon']>('swing')
const entryType = ref<NewHoldingInput['entryType']>('general')
const entryDate = ref('')
const stopLossPrice = ref('')
const takeProfitPrice = ref('')
const note = ref('')
const localError = ref('')
const confirmDelete = ref(false)
let editorOpener: HTMLElement | null = null

const isEdit = computed(() => props.mode === 'edit' && props.holding != null)
const title = computed(() => isEdit.value ? `编辑持仓 · ${props.holding?.name || props.holding?.code}` : '添加持仓')

function resetForm() {
  const row = isEdit.value ? props.holding : null
  code.value = row?.code || ''
  cost.value = row?.cost == null ? '' : String(row.cost)
  shares.value = row?.shares == null ? '' : String(row.shares)
  horizon.value = row?.horizon || 'swing'
  entryType.value = row?.entryType || 'general'
  entryDate.value = row?.entryDate || ''
  stopLossPrice.value = row?.stopLossPrice == null ? '' : String(row.stopLossPrice)
  takeProfitPrice.value = row?.takeProfitPrice == null ? '' : String(row.takeProfitPrice)
  note.value = row?.note || ''
  localError.value = ''
  confirmDelete.value = false
}

function expectedCost(): string {
  const parsedCost = finiteNumber(cost.value)
  const parsedShares = finiteNumber(shares.value)
  return parsedCost != null && parsedCost > 0 && parsedShares != null && parsedShares > 0
    ? formatMoney(parsedCost * parsedShares)
    : '—'
}

function requestClose() {
  if (!props.busy) emit('close')
}

function handleClosed() {
  emit('close')
  void nextTick(() => editorOpener?.focus())
  editorOpener = null
}

function submit() {
  localError.value = ''
  const normalizedCode = code.value.replace(/\D/g, '')
  const parsedCost = finiteNumber(cost.value)
  const parsedShares = finiteNumber(shares.value)
  const parsedStopLoss = finiteNumber(stopLossPrice.value)
  const parsedTakeProfit = finiteNumber(takeProfitPrice.value)

  if (!/^\d{6}$/.test(normalizedCode)) {
    localError.value = '股票代码必须是 6 位数字。'
    void nextTick(() => codeInput.value?.focus())
    return
  }
  if (parsedCost == null || parsedCost <= 0) {
    localError.value = '持仓成本必须是大于 0 的有效金额。'
    return
  }
  if (parsedShares == null || parsedShares <= 0 || !Number.isInteger(parsedShares)) {
    localError.value = '持仓数量必须是大于 0 的整数。'
    return
  }
  if (stopLossPrice.value.trim() && parsedStopLoss == null) {
    localError.value = '客户风险线必须是有效数字，或留空。'
    return
  }
  if (parsedStopLoss != null && parsedStopLoss <= 0) {
    localError.value = '客户风险线必须大于 0，或留空。'
    return
  }
  if (takeProfitPrice.value.trim() && parsedTakeProfit == null) {
    localError.value = '客户目标价必须是有效数字，或留空。'
    return
  }
  if (parsedTakeProfit != null && parsedTakeProfit <= 0) {
    localError.value = '客户目标价必须大于 0，或留空。'
    return
  }
  if (parsedStopLoss != null && parsedTakeProfit != null && parsedStopLoss >= parsedTakeProfit) {
    localError.value = '客户风险线必须低于客户目标价。'
    return
  }

  emit('submit', {
    code: normalizedCode,
    cost: parsedCost,
    shares: parsedShares,
    horizon: horizon.value,
    entryType: entryType.value,
    entryDate: entryDate.value,
    stopLossPrice: parsedStopLoss,
    takeProfitPrice: parsedTakeProfit,
    note: note.value.trim(),
  })
}

function requestRemove() {
  if (!confirmDelete.value) {
    confirmDelete.value = true
    return
  }
  emit('remove')
}

watch(
  () => [props.open, props.mode, props.holding?.code] as const,
  async ([open]) => {
    if (!open) {
      if (dialog.value?.open) dialog.value.close()
      return
    }
    editorOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    resetForm()
    await nextTick()
    if (dialog.value && !dialog.value.open) dialog.value.showModal()
    await nextTick()
    ;(isEdit.value ? dialog.value?.querySelector<HTMLElement>('input:not([disabled]), select') : codeInput.value)?.focus()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (dialog.value?.open) dialog.value.close()
})
</script>

<template>
  <Teleport to="body">
  <dialog
    ref="dialog"
    class="holding-dialog"
    aria-modal="true"
    aria-labelledby="holding-dialog-title"
    @cancel.prevent="requestClose"
    @close="handleClosed"
  >
    <form @submit.prevent="submit">
      <header class="holding-dialog__header">
        <div>
          <p class="eyebrow">{{ isEdit ? '持仓信息与监控方案' : '建立新的持仓监控' }}</p>
          <h2 id="holding-dialog-title">{{ title }}</h2>
        </div>
        <button type="button" class="holding-dialog__close" :disabled="busy" @click="requestClose">关闭</button>
      </header>

      <div class="holding-dialog__body">
        <fieldset>
          <legend>持仓信息</legend>
          <div class="holding-form-grid">
            <label>
              <span>股票代码 <b>必填</b></span>
              <input ref="codeInput" v-model="code" inputmode="numeric" maxlength="6" autocomplete="off" :disabled="isEdit" />
              <small>{{ isEdit ? '已添加持仓不支持更换股票代码' : '保存前将核验股票代码' }}</small>
            </label>
            <label>
              <span>持仓成本（元/股）<b>必填</b></span>
              <input v-model="cost" inputmode="decimal" autocomplete="off" />
              <small>用于计算浮动盈亏和风险单位</small>
            </label>
            <label>
              <span>持仓数量（股）<b>必填</b></span>
              <input v-model="shares" inputmode="numeric" autocomplete="off" />
              <small>必须为正整数</small>
            </label>
            <label>
              <span>买入日期</span>
              <input v-model="entryDate" type="date" autocomplete="off" />
              <small>选填，用于时间止损和盈利保护</small>
            </label>
          </div>
        </fieldset>

        <fieldset>
          <legend>监控方案</legend>
          <div class="holding-form-grid">
            <label>
              <span>持仓周期</span>
              <select v-model="horizon">
                <option value="general">通用监控</option><option value="overnight">隔日</option>
                <option value="ultra">超短</option><option value="short">短线</option>
                <option value="swing">波段</option><option value="trend">趋势</option>
              </select>
              <small>修改后会重新计算算法风险线</small>
            </label>
            <label>
              <span>买入逻辑</span>
              <select v-model="entryType">
                <option value="general">未指定</option><option value="breakout">突破</option>
                <option value="pullback">回踩</option><option value="event">事件</option>
              </select>
              <small>突破失败规则仅对突破型持仓启用</small>
            </label>
            <label>
              <span>客户风险线</span>
              <input v-model="stopLossPrice" inputmode="decimal" autocomplete="off" placeholder="选填" />
              <small>达到或跌破时优先提示</small>
            </label>
            <label>
              <span>客户目标价</span>
              <input v-model="takeProfitPrice" inputmode="decimal" autocomplete="off" placeholder="选填" />
              <small>达到或突破时优先提示</small>
            </label>
            <label class="holding-form-grid__wide">
              <span>备注</span>
              <textarea v-model="note" maxlength="200" rows="2" placeholder="选填，例如：等待业绩确认后再决定是否加仓"></textarea>
              <small>最多 200 字</small>
            </label>
          </div>
        </fieldset>

        <div class="holding-dialog__summary">
          <span>预计持仓成本</span><strong class="mono">{{ expectedCost() }}</strong>
          <em>保存后，组合统计与算法风险卡会自动更新。</em>
        </div>
        <p v-if="localError || serverError" class="holding-dialog__error" role="alert">{{ localError || serverError }}</p>
      </div>

      <footer class="holding-dialog__footer">
        <div>
          <button v-if="isEdit" type="button" class="danger-button" :class="{ 'is-confirming': confirmDelete }" :disabled="busy" @click="requestRemove">
            {{ confirmDelete ? '再次点击确认删除' : '删除持仓' }}
          </button>
        </div>
        <div class="holding-dialog__actions">
          <button type="button" class="secondary-button" :disabled="busy" @click="requestClose">取消</button>
          <button type="submit" class="primary-button" :disabled="busy">
            {{ busy ? '保存中…' : isEdit ? '保存修改' : '核验并添加' }}
          </button>
        </div>
      </footer>
    </form>
  </dialog>
  </Teleport>
</template>

<style scoped>
.holding-dialog { width: min(760px, calc(100vw - 32px)); max-height: min(840px, calc(100dvh - 32px)); padding: 0; overflow: hidden; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--surface); color: var(--text); }
.holding-dialog::backdrop { background: rgb(5 11 20 / 60%); }
.holding-dialog form { display: grid; max-height: min(840px, calc(100dvh - 32px)); grid-template-rows: auto minmax(0, 1fr) auto; }
.holding-dialog__header, .holding-dialog__footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 18px; }
.holding-dialog__header { border-bottom: 1px solid var(--border); }
.holding-dialog__header h2 { margin: 1px 0 0; font-size: 20px; }
.holding-dialog__close { min-width: 64px; min-height: 44px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); color: var(--text-2); font-size: 14px; cursor: pointer; }
.holding-dialog__body { display: grid; gap: 14px; min-height: 0; overflow-y: auto; padding: 16px 18px; }
.holding-dialog fieldset { min-width: 0; margin: 0; padding: 13px 14px 14px; border: 1px solid var(--border); border-radius: 10px; }
.holding-dialog legend { padding: 0 7px; color: var(--text); font-size: 14px; font-weight: 800; }
.holding-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 13px 14px; }
.holding-form-grid label { display: grid; min-width: 0; gap: 5px; }
.holding-form-grid label > span { color: var(--text-2); font-size: 13px; font-weight: 700; }
.holding-form-grid label > span b { margin-left: 5px; color: var(--danger); font-size: 11px; }
.holding-form-grid input, .holding-form-grid select, .holding-form-grid textarea { width: 100%; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); color: var(--text); }
.holding-form-grid input:focus, .holding-form-grid select:focus, .holding-form-grid textarea:focus { border-color: var(--focus); outline: 2px solid color-mix(in srgb, var(--focus) 22%, transparent); outline-offset: 1px; }
.holding-form-grid input:disabled { color: var(--text-3); cursor: not-allowed; }
.holding-form-grid textarea { resize: vertical; line-height: 1.5; }
.holding-form-grid small { color: var(--text-3); font-size: 11px; }
.holding-form-grid__wide { grid-column: 1 / -1; }
.holding-dialog__summary { display: grid; grid-template-columns: auto auto minmax(0, 1fr); align-items: baseline; gap: 6px 10px; padding: 10px 12px; border-radius: 8px; background: var(--surface-2); color: var(--text-2); font-size: 12px; }
.holding-dialog__summary strong { color: var(--text); font-size: 16px; }
.holding-dialog__summary em { justify-self: end; color: var(--text-3); font-style: normal; }
.holding-dialog__error { margin: 0; padding: 9px 11px; border-left: 3px solid var(--danger); background: color-mix(in srgb, var(--danger) 7%, var(--surface)); color: var(--danger); font-size: 13px; font-weight: 650; }
.holding-dialog__footer { border-top: 1px solid var(--border); background: var(--surface); }
.holding-dialog__actions { display: flex; gap: 8px; }
.holding-dialog__footer button { min-height: 44px; }
.holding-dialog button:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.danger-button.is-confirming { border-color: var(--danger); background: var(--danger); color: white; }
@media (max-width: 620px) {
  .holding-dialog { width: calc(100vw - 16px); max-height: calc(100dvh - 16px); }
  .holding-dialog form { max-height: calc(100dvh - 16px); }
  .holding-form-grid { grid-template-columns: minmax(0, 1fr); }
  .holding-form-grid__wide { grid-column: auto; }
  .holding-dialog__summary { grid-template-columns: auto 1fr; }
  .holding-dialog__summary em { grid-column: 1 / -1; justify-self: start; }
  .holding-dialog__footer { align-items: stretch; flex-direction: column-reverse; }
  .holding-dialog__actions, .holding-dialog__actions button, .holding-dialog__footer > div:first-child button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .holding-dialog, .holding-dialog * { scroll-behavior: auto !important; }
}
</style>
