<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import type { NewHoldingInput } from '../types/holdings'
import { finiteNumber, formatMoney } from '../utils/holdingsFormat'

defineProps<{
  adding: boolean
  serverError: string
}>()

const emit = defineEmits<{
  submit: [input: NewHoldingInput]
  cancel: []
}>()

const code = ref('')
const cost = ref('')
const shares = ref('')
const note = ref('')
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
  emit('submit', { code: normalizedCode, cost: parsedCost, shares: parsedShares, note: note.value.trim() })
}

onMounted(() => codeInput.value?.focus())
</script>

<template>
  <section class="panel holding-editor" aria-labelledby="holding-editor-title">
    <header>
      <div><p class="eyebrow">新增账本条目</p><h2 id="holding-editor-title">添加持仓</h2></div>
      <button type="button" class="secondary-button" :disabled="adding" @click="emit('cancel')">取消</button>
    </header>
    <form @submit.prevent="submit">
      <div class="holding-editor__fields">
        <label>
          <span>股票代码</span>
          <input ref="codeInput" v-model="code" inputmode="numeric" maxlength="6" autocomplete="off" aria-describedby="holding-code-hint" />
          <small id="holding-code-hint">提交前通过真实单票接口核验</small>
        </label>
        <label>
          <span>成本价（元/股）</span>
          <input v-model="cost" inputmode="decimal" autocomplete="off" />
          <small>必须大于 0</small>
        </label>
        <label>
          <span>持有股数（股）</span>
          <input v-model="shares" inputmode="numeric" autocomplete="off" />
          <small>必须为正整数</small>
        </label>
        <label class="holding-editor__note">
          <span>备注（可选）</span>
          <input v-model="note" maxlength="200" autocomplete="off" />
          <small>只保存用户输入，不生成交易结论</small>
        </label>
      </div>
      <div class="holding-editor__footer">
        <p>预计持仓成本：<strong class="mono">{{ expectedCost() }}</strong></p>
        <button type="submit" class="primary-button" :disabled="adding">{{ adding ? '核验中' : '核验并加入' }}</button>
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
.holding-editor__fields { display: grid; grid-template-columns: .8fr .8fr .8fr 1.6fr; gap: 12px; }
.holding-editor label { display: grid; min-width: 0; gap: 5px; color: var(--text-2); font-size: 13px; font-weight: 680; }
.holding-editor input { width: 100%; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); }
.holding-editor input:focus { border-color: var(--focus); }
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
