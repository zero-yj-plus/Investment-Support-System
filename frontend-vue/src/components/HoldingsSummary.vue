<script setup lang="ts">
import { computed } from 'vue'
import type { HoldingView } from '../types/holdings'
import { formatMoney, formatPercent, pnlTone } from '../utils/holdingsFormat'

const props = defineProps<{
  rows: HoldingView[]
  quotedCount: number
  aggregate: {
    marketValue: number | null
    costAmount: number | null
    pnl: number | null
    pnlPct: number | null
  }
  dirty: boolean
  saving: boolean
  quotesLoading: boolean
  hasStaleQuotes: boolean
}>()

const emit = defineEmits<{
  save: []
  add: []
}>()

const quoteComplete = computed(() => props.rows.length > 0 && props.quotedCount === props.rows.length)
const conclusion = computed(() => {
  if (!props.rows.length) return '当前未记录持仓，页面不会创建零金额占位记录。'
  if (props.dirty) return '存在未保存变更，当前首要动作是核对并保存持仓账本。'
  if (!quoteComplete.value) return `持仓账本已加载；报价覆盖 ${props.quotedCount}/${props.rows.length}，组合盈亏保持缺失。`
  if (props.hasStaleQuotes) return '持仓与报价已加载，但报价含陈旧数据，不能按实时口径解释。'
  return '持仓账本与当前报价已加载，可复核持仓总市值和浮动盈亏。'
})

const boundary = computed(() => {
  if (!props.rows.length) return '没有持仓时不生成市值、盈亏或风险判断。'
  if (!quoteComplete.value) return '任一现价缺失时，不汇总持仓总市值或总盈亏。'
  return '投入成本按成本价 × 股数自动汇总；持仓总市值按真实现价 × 股数自动汇总，不等同于包含现金的账户总资产。'
})
</script>

<template>
  <section class="panel holdings-summary" aria-labelledby="holdings-summary-title">
    <div class="holdings-summary__lead">
      <p class="eyebrow">当前结论 · 账本与报价边界</p>
      <h2 id="holdings-summary-title">{{ conclusion }}</h2>
      <p>{{ boundary }}</p>
      <div class="holdings-summary__actions">
        <button
          v-if="dirty"
          type="button"
          class="primary-button"
          :disabled="saving"
          @click="emit('save')"
        >{{ saving ? '保存中' : '保存持仓变更' }}</button>
        <button type="button" :class="dirty ? 'secondary-button' : 'primary-button'" :disabled="saving" @click="emit('add')">添加持仓</button>
      </div>
    </div>

    <dl class="holdings-summary__metrics" aria-label="持仓汇总指标">
      <div>
        <dt>持仓数量</dt>
        <dd class="mono">{{ rows.length }} 只</dd>
        <dd class="holdings-summary__note">账本真实条目</dd>
      </div>
      <div>
        <dt>报价覆盖</dt>
        <dd class="mono">{{ quotedCount }}/{{ rows.length }}</dd>
        <dd class="holdings-summary__note">{{ !rows.length ? '无持仓' : quotesLoading ? '正在刷新' : quoteComplete ? '全部有价' : '部分缺价' }}</dd>
      </div>
      <div>
        <dt>持仓总市值</dt>
        <dd class="mono">{{ formatMoney(aggregate.marketValue) }}</dd>
        <dd class="holdings-summary__note">{{ aggregate.marketValue == null ? '缺价时不汇总' : '按当前报价' }}</dd>
      </div>
      <div>
        <dt>浮动盈亏</dt>
        <dd class="mono" :class="`is-${pnlTone(aggregate.pnl)}`">{{ formatMoney(aggregate.pnl) }}</dd>
        <dd class="holdings-summary__note">{{ aggregate.pnlPct == null ? '—' : formatPercent(aggregate.pnlPct, true) }}</dd>
      </div>
      <div>
        <dt>投入成本</dt>
        <dd class="mono">{{ formatMoney(aggregate.costAmount) }}</dd>
        <dd class="holdings-summary__note">{{ aggregate.costAmount == null ? '成本或股数缺失' : '成本价 × 股数' }}</dd>
      </div>
    </dl>
  </section>
</template>

<style scoped>
.holdings-summary { display: grid; grid-template-columns: minmax(320px, 1.1fr) minmax(0, 1.9fr); overflow: hidden; border-color: color-mix(in srgb, var(--accent) 38%, var(--border)); }
.holdings-summary__lead { display: flex; min-width: 0; flex-direction: column; align-items: flex-start; justify-content: center; padding: 18px; border-right: 1px solid var(--border); }
.holdings-summary__lead h2 { margin: 0; font-size: 20px; line-height: 1.45; }
.holdings-summary__lead > p:last-of-type { margin: 8px 0 16px; color: var(--text-2); }
.holdings-summary__actions { display: flex; flex-wrap: wrap; gap: 8px; }
.holdings-summary__metrics { display: grid; min-width: 0; grid-template-columns: repeat(5, minmax(0, 1fr)); margin: 0; }
.holdings-summary__metrics > div { min-width: 0; padding: 16px 12px; border-right: 1px solid var(--border); }
.holdings-summary__metrics > div:last-child { border-right: 0; }
.holdings-summary__metrics dt, .holdings-summary__note { color: var(--text-3); font-size: 12px; }
.holdings-summary__metrics dd { margin: 6px 0 2px; font-size: 19px; font-weight: 760; overflow-wrap: anywhere; }
.holdings-summary__note { display: block; margin: 0; font-weight: 500; }
.is-up { color: var(--up); }
.is-down { color: var(--down); }
.is-flat { color: var(--text-2); }
.is-missing { color: var(--text-3); }
@media (max-width: 1180px) {
  .holdings-summary { grid-template-columns: minmax(0, 1fr); }
  .holdings-summary__lead { border-right: 0; border-bottom: 1px solid var(--border); }
}
@media (max-width: 820px) {
  .holdings-summary__metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .holdings-summary__metrics > div { border-bottom: 1px solid var(--border); }
}
@media (max-width: 520px) {
  .holdings-summary__lead { padding: 14px; }
  .holdings-summary__actions { width: 100%; }
  .holdings-summary__actions .primary-button, .holdings-summary__actions .secondary-button { flex: 1 1 150px; }
  .holdings-summary__metrics { grid-template-columns: minmax(0, 1fr); }
  .holdings-summary__metrics > div { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: baseline; gap: 2px 12px; padding: 11px 14px; border-right: 0; }
  .holdings-summary__metrics dd { margin: 0; text-align: right; }
  .holdings-summary__note { grid-column: 1 / -1; }
}
</style>
