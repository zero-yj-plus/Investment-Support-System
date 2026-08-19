<script setup lang="ts">
import { ref, watch } from 'vue'
import type { StockAskProfile } from '../types/stockAskProfile'

const props = defineProps<{
  modelValue: StockAskProfile
  disabled?: boolean
  quotePrice?: number | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: StockAskProfile]
}>()

const expanded = ref(true)

watch(() => props.disabled, (disabled) => {
  if (disabled) expanded.value = false
})

function patch(values: Partial<StockAskProfile>) {
  emit('update:modelValue', { ...props.modelValue, ...values })
}

function numberValue(event: Event): number | null {
  const raw = (event.target as HTMLInputElement).value
  if (!raw) return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

function resetRiskBudget() {
  patch({ risk_budget_pct: 1.5, risk_budget_explicit: false })
}
</script>

<template>
  <section class="stock-ask-profile" aria-labelledby="stock-ask-profile-title">
    <button
      type="button"
      class="stock-ask-profile__toggle"
      :aria-expanded="expanded"
      aria-controls="stock-ask-profile-fields"
      @click="expanded = !expanded"
    >
      <span><strong id="stock-ask-profile-title">问股参数</strong><small>服务端规则输入，不交给模型决策</small></span>
      <span class="stock-ask-profile__summary">{{ modelValue.holding_status === 'held' ? '已持仓' : modelValue.holding_status === 'unheld' ? '未持仓' : '自动识别' }} · {{ modelValue.horizon === 'NEXT_DAY' ? '次日' : modelValue.horizon === 'SWING' ? '波段' : '短线' }}</span>
    </button>

    <div v-if="expanded" id="stock-ask-profile-fields" class="stock-ask-profile__body">
      <div class="stock-ask-profile__grid">
        <label>
          <span>持仓状态</span>
          <select :value="modelValue.holding_status" :disabled="disabled" @change="patch({ holding_status: ($event.target as HTMLSelectElement).value as StockAskProfile['holding_status'] })">
            <option value="unspecified">读取本地持仓</option>
            <option value="unheld">未持仓</option>
            <option value="held">已持仓</option>
          </select>
        </label>

        <label>
          <span>参考价</span>
          <input
            :value="modelValue.current_price ?? ''"
            :placeholder="quotePrice ? `行情 ${quotePrice.toFixed(2)}` : '自动取行情'"
            type="number"
            inputmode="decimal"
            min="0.01"
            step="0.01"
            :disabled="disabled"
            @input="patch({ current_price: numberValue($event) })"
          >
        </label>

        <label>
          <span>持仓成本</span>
          <input
            :value="modelValue.holding_cost ?? ''"
            placeholder="可读取本地持仓"
            type="number"
            inputmode="decimal"
            min="0.01"
            step="0.01"
            :disabled="disabled || modelValue.holding_status === 'unheld'"
            @input="patch({ holding_cost: numberValue($event) })"
          >
        </label>

        <label>
          <span>当前仓位 %</span>
          <input
            :value="modelValue.position_pct ?? ''"
            placeholder="可读取本地持仓"
            type="number"
            inputmode="decimal"
            min="0.01"
            max="100"
            step="0.01"
            :disabled="disabled || modelValue.holding_status === 'unheld'"
            @input="patch({ position_pct: numberValue($event) })"
          >
        </label>

        <label>
          <span>分析场景</span>
          <select :value="modelValue.scenario" :disabled="disabled" @change="patch({ scenario: ($event.target as HTMLSelectElement).value as StockAskProfile['scenario'] })">
            <option value="intraday">盘中</option>
            <option value="after_close">盘后</option>
          </select>
        </label>

        <label>
          <span>持有周期</span>
          <select :value="modelValue.horizon" :disabled="disabled" @change="patch({ horizon: ($event.target as HTMLSelectElement).value as StockAskProfile['horizon'] })">
            <option value="NEXT_DAY">次日</option>
            <option value="SHORT_TERM">短线</option>
            <option value="SWING">波段</option>
          </select>
        </label>

        <label>
          <span>风险预算 %</span>
          <input
            :value="modelValue.risk_budget_pct"
            type="number"
            inputmode="decimal"
            min="0.1"
            max="10"
            step="0.1"
            :disabled="disabled"
            @input="patch({ risk_budget_pct: numberValue($event) ?? 1.5, risk_budget_explicit: true })"
          >
        </label>

        <label>
          <span>最大仓位 %</span>
          <input
            :value="modelValue.max_position_pct"
            type="number"
            inputmode="decimal"
            min="1"
            max="100"
            step="1"
            :disabled="disabled"
            @input="patch({ max_position_pct: numberValue($event) ?? 70 })"
          >
        </label>
      </div>

      <div class="stock-ask-profile__foot">
        <label class="stock-ask-profile__check">
          <input
            type="checkbox"
            :checked="modelValue.allow_add"
            :disabled="disabled || modelValue.holding_status === 'unheld'"
            @change="patch({ allow_add: ($event.target as HTMLInputElement).checked })"
          >
          <span>已持仓时允许评估条件加仓</span>
        </label>
        <p :class="{ 'is-explicit': modelValue.risk_budget_explicit }">
          {{ modelValue.risk_budget_explicit ? '风险预算已成为硬约束' : '1.5% 仅为提示值，主动修改后才成为硬约束' }}
          <button v-if="modelValue.risk_budget_explicit" type="button" :disabled="disabled" @click="resetRiskBudget">恢复提示值</button>
        </p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.stock-ask-profile { border-bottom: 1px solid var(--border); background: color-mix(in srgb, var(--surface-2) 72%, var(--surface)); }
.stock-ask-profile__toggle { display: flex; width: 100%; min-height: 48px; align-items: center; justify-content: space-between; gap: 12px; padding: 6px 14px; border: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.stock-ask-profile__toggle:hover { background: var(--surface-2); }
.stock-ask-profile__toggle > span:first-child { display: grid; gap: 1px; }
.stock-ask-profile__toggle strong { font-size: 13px; }
.stock-ask-profile__toggle small { color: var(--text-3); font-size: 11px; }
.stock-ask-profile__summary { color: var(--accent); font-size: 12px; font-weight: 720; }
.stock-ask-profile__body { padding: 0 14px 10px; }
.stock-ask-profile__grid { display: grid; grid-template-columns: repeat(4, minmax(112px, 1fr)); gap: 8px; }
.stock-ask-profile__grid label { display: grid; min-width: 0; gap: 4px; color: var(--text-3); font-size: 11px; }
.stock-ask-profile__grid input, .stock-ask-profile__grid select { width: 100%; min-width: 0; min-height: 40px; padding: 0 9px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); font: inherit; font-size: 13px; }
.stock-ask-profile__grid input:focus-visible, .stock-ask-profile__grid select:focus-visible, .stock-ask-profile__toggle:focus-visible, .stock-ask-profile__foot button:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.stock-ask-profile__grid input:disabled, .stock-ask-profile__grid select:disabled { cursor: not-allowed; opacity: .55; }
.stock-ask-profile__foot { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 12px; margin-top: 8px; }
.stock-ask-profile__check { display: inline-flex; min-height: 44px; align-items: center; gap: 8px; color: var(--text-2); font-size: 12px; }
.stock-ask-profile__check input { width: 18px; height: 18px; accent-color: var(--accent); }
.stock-ask-profile__foot p { margin: 0; color: var(--text-3); font-size: 11px; }
.stock-ask-profile__foot p.is-explicit { color: var(--warning); }
.stock-ask-profile__foot button { min-height: 44px; margin-left: 6px; padding: 0 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: inherit; cursor: pointer; }

@media (max-width: 900px) {
  .stock-ask-profile__grid { grid-template-columns: repeat(2, minmax(130px, 1fr)); }
}

@media (max-width: 720px) {
  .stock-ask-profile__toggle { min-height: 52px; padding-inline: 10px; }
  .stock-ask-profile__body { padding-inline: 10px; }
  .stock-ask-profile__grid { grid-template-columns: minmax(0, 1fr); }
  .stock-ask-profile__grid input, .stock-ask-profile__grid select { min-height: 44px; font-size: 16px; }
  .stock-ask-profile__summary { max-width: 42%; text-align: right; }
}
</style>
