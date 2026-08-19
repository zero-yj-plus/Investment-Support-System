<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import HoldingEditor from './HoldingEditor.vue'
import HoldingsRiskCards from './HoldingsRiskCards.vue'
import HoldingsTable from './HoldingsTable.vue'
import { useHoldings } from '../composables/useHoldings'
import type { NewHoldingInput } from '../types/holdings'
import { formatMoney, pnlTone } from '../utils/holdingsFormat'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const showEditor = ref(false)
const replanClientKey = ref('')
const {
  holdings,
  views,
  loading,
  refreshing,
  quotesLoading,
  saving,
  adding,
  riskRefreshing,
  actionRunning,
  loaded,
  dirty,
  holdingsError,
  quotesError,
  saveError,
  addError,
  riskError,
  feedback,
  quotedCount,
  quoteAsOf,
  quoteSources,
  hasStaleQuotes,
  aggregate,
  updateHolding,
  removeHolding,
  refreshQuotes,
  refreshRiskStates,
  refresh,
  save,
  addHolding,
  resetPlan,
  confirmAdjustment,
} = useHoldings()

const pageRefreshing = computed(() => refreshing.value || quotesLoading.value || saving.value || adding.value || riskRefreshing.value || actionRunning.value)
const replanHolding = computed(() => holdings.value.find((row) => row.clientKey === replanClientKey.value) || null)
const dataStatus = computed(() => {
  if (holdingsError.value) return '账本暂不可用'
  if (loading.value) return '加载中'
  if (!holdings.value.length) return '真实空数组'
  if (quotesError.value || quotedCount.value < holdings.value.length) return '报价局部降级'
  if (hasStaleQuotes.value) return '陈旧报价'
  if (dirty.value) return '等待自动保存'
  return '账本与报价可用'
})
const quoteStamp = computed(() => quoteAsOf.value || '—')
const sourceLabel = computed(() => quoteSources.value.length ? quoteSources.value.join(' / ') : '—')

watch(pageRefreshing, (value) => emit('refreshState', value), { immediate: true })

function openEditor() {
  showEditor.value = true
  void nextTick(() => document.querySelector<HTMLElement>('#holding-editor-title')?.scrollIntoView({ block: 'start' }))
}

async function handleAdd(input: NewHoldingInput) {
  const existing = holdings.value.find((row) => row.code === input.code)
  if (existing) {
    const oldCost = existing.cost
    const oldShares = existing.shares
    let supplement = input
    if (oldCost != null && oldShares != null) {
      const totalShares = oldShares + input.shares
      const supplementFees = input.fees == null ? 0 : input.fees
      const aggregateCost = (oldCost * oldShares + input.cost * input.shares + supplementFees) / totalShares
      supplement = {
        ...input,
        cost: aggregateCost,
        shares: totalShares,
        note: input.note || existing.note,
      }
    }
    if (await resetPlan(existing.code, supplement, 'supplement')) showEditor.value = false
    return
  }
  const added = await addHolding(input)
  if (added) showEditor.value = false
}

function openReplan(clientKey: string) {
  replanClientKey.value = clientKey
  void nextTick(() => document.querySelector<HTMLElement>('#holding-editor-title')?.scrollIntoView({ block: 'start' }))
}

async function handleReplan(input: NewHoldingInput) {
  const holding = replanHolding.value
  if (!holding) return
  if (await resetPlan(holding.code, input, 'manual_replan')) {
    replanClientKey.value = ''
  }
}

async function handleRemove(clientKey: string) {
  const holding = holdings.value.find((row) => row.clientKey === clientKey)
  if (!holding) return
  const label = holding.name ? `${holding.name}（${holding.code}）` : holding.code
  if (!window.confirm(`确认取消关注 ${label}？确认后将立即更新持仓账本。`)) return
  await removeHolding(clientKey)
}

onBeforeUnmount(() => emit('refreshState', false))
defineExpose({ refresh })
</script>

<template>
  <section class="holdings-page" aria-labelledby="holdings-page-title" :aria-busy="pageRefreshing">
    <header class="holdings-intro">
      <div class="page-heading">
        <h1 id="holdings-page-title">我的持仓</h1>
        <p class="page-subtitle">管理超短、短线和波段计划；服务端按K线、Wilder ATR14、结构位与时间规则给出累计仓位降低比例。</p>
      </div>
      <div class="holdings-intro__actions">
        <button type="button" class="primary-button" :disabled="loading || !!holdingsError || saving || adding || actionRunning" @click="openEditor">添加持仓</button>
        <button type="button" class="secondary-button" :disabled="riskRefreshing || actionRunning || saving || adding || !holdings.length" @click="refreshRiskStates(true)">{{ riskRefreshing ? '评估中' : '刷新仓位评估' }}</button>
      </div>
    </header>

    <div class="sr-only" aria-live="polite">
      {{ pageRefreshing ? '持仓页面正在处理请求' : feedback || saveError || quotesError || holdingsError || `持仓状态：${dataStatus}` }}
    </div>

    <div v-if="loading" class="holdings-loading" role="status" aria-live="polite">
      <span class="sr-only">持仓账本正在加载</span>
      <section class="panel holdings-skeleton holdings-skeleton--boundary"></section>
      <section class="panel holdings-skeleton holdings-skeleton--detail"></section>
    </div>

    <template v-else>
      <section v-if="holdingsError" class="panel holdings-fatal" role="alert" aria-labelledby="holdings-fatal-title">
        <p class="eyebrow">账本读取失败 · 保存已锁定</p>
        <h2 id="holdings-fatal-title">持仓账本暂不可用</h2>
        <p>{{ holdingsError }}</p>
        <p>PUT 是整体覆盖保存；在真实账本未加载前，页面不会启用保存或构造空持仓。</p>
        <button type="button" class="primary-button" :disabled="refreshing" @click="refresh">重试加载</button>
      </section>

      <template v-else>
        <div v-if="quotesError" class="holdings-alert" role="alert">
          <div>
            <strong>报价局部失败，持仓账本仍可编辑</strong>
            <span>{{ quotesError }}；缺价位置保持“—”，持仓总市值和总盈亏不汇总。</span>
          </div>
          <button type="button" class="secondary-button" :disabled="quotesLoading" @click="refreshQuotes">重试报价</button>
        </div>

        <div v-else-if="hasStaleQuotes" class="holdings-alert is-warning" role="status">
          <div><strong>当前含陈旧报价</strong><span>非交易时段或数据龄超限的报价已标记，不会声称为实时价格。</span></div>
        </div>

        <div v-if="feedback || saveError" class="holdings-feedback" :class="{ 'is-error': saveError }" :role="saveError ? 'alert' : 'status'">
          <strong>{{ saveError ? '操作未完成' : '账本状态' }}</strong><span>{{ saveError || feedback }}</span>
        </div>

        <div v-if="riskError" class="holdings-alert" role="alert">
          <div><strong>仓位管理状态读取失败</strong><span>{{ riskError }}；账本和报价仍可使用，页面不会用旧数据冒充新评估。</span></div>
          <button type="button" class="secondary-button" :disabled="riskRefreshing" @click="refreshRiskStates()">重试状态</button>
        </div>

        <HoldingEditor v-if="showEditor" :adding="adding || saving || actionRunning" :server-error="addError || saveError" @submit="handleAdd" @cancel="showEditor = false" />
        <HoldingEditor v-if="replanHolding" mode="replan" :initial="replanHolding" :adding="actionRunning" :server-error="saveError" @submit="handleReplan" @cancel="replanClientKey = ''" />

        <section class="panel holdings-boundary" aria-labelledby="holdings-boundary-title">
          <div>
            <p class="eyebrow">自动汇总口径</p>
            <h2 id="holdings-boundary-title">持仓成本与市值</h2>
            <p>投入成本仍按成本价 × 股数计算，市值按真实现价 × 股数计算；仓位管理只输出累计降低比例，不换算具体股数，也不连接券商下单。</p>
          </div>
          <dl>
            <div><dt>投入成本</dt><dd class="mono">{{ formatMoney(aggregate.costAmount) }}</dd></div>
            <div><dt>持仓总市值</dt><dd class="mono">{{ formatMoney(aggregate.marketValue) }}</dd></div>
            <div><dt>浮动盈亏</dt><dd class="mono" :class="`is-${pnlTone(aggregate.pnl)}`">{{ formatMoney(aggregate.pnl) }}</dd></div>
          </dl>
        </section>

        <section v-if="!holdings.length" class="panel holdings-empty" aria-labelledby="holdings-empty-title">
          <p class="eyebrow">真实空数组</p>
          <h2 id="holdings-empty-title">当前未记录持仓</h2>
          <p>接口成功返回空持仓。页面不会插入示例股票、零成本或零股数；“核验并加入”成功后会立即写入账本。</p>
          <button type="button" class="secondary-button" @click="openEditor">添加第一笔持仓</button>
        </section>

        <HoldingsRiskCards
          v-if="holdings.length"
          :rows="views"
          :action-running="actionRunning || saving || adding"
          @replan="openReplan"
          @confirm="confirmAdjustment"
          @remove="handleRemove"
        />

        <HoldingsTable
          v-if="holdings.length"
          :rows="views"
          :busy="saving || adding || actionRunning"
          @update="updateHolding"
          @commit="save"
          @remove="handleRemove"
        />

        <footer class="page-footer holdings-footer">
          <span>GET 持仓成功空数组、报价缺失、陈旧报价、接口失败分别展示；缺失值不补 0。</span>
          <span>仓位目标按计划版本只升不降；重置计划会重置1R、最高价、风险线和累计目标。数据失败保留原始时点并明确标记。</span>
        </footer>
      </template>
    </template>

    <dl class="holdings-stamp data-footnote-stamp" :class="{ 'is-degraded': quotesError || hasStaleQuotes || holdingsError }" aria-label="持仓数据日期、关键时点与状态">
      <div><dt>账本状态</dt><dd>{{ loaded ? '已加载' : loading ? '加载中' : '未加载' }}</dd></div>
      <div><dt>报价时点</dt><dd class="mono">{{ quoteStamp }}<span v-if="!quoteAsOf" class="sr-only">时点缺失</span></dd></div>
      <div><dt>报价来源</dt><dd>{{ sourceLabel }}<span v-if="!quoteSources.length" class="sr-only">来源缺失</span></dd></div>
      <div><dt>数据状态</dt><dd>{{ dataStatus }}</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.holdings-page { display: grid; min-width: 0; gap: 12px; }
.holdings-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.holdings-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.holdings-intro > div > p:last-child { max-width: 800px; margin: 7px 0 0; color: var(--text-2); }
.holdings-intro__actions { display: flex; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.holdings-stamp { display: grid; min-width: 520px; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px 14px; margin: 0; padding: 9px 11px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.holdings-stamp.is-degraded { border-color: var(--warning); }
.holdings-stamp div { min-width: 0; }
.holdings-stamp dt { color: var(--text-3); font-size: 12px; }
.holdings-stamp dd { margin: 1px 0 0; font-size: 13px; font-weight: 720; overflow-wrap: anywhere; }
.holdings-loading { display: grid; gap: 12px; }
.holdings-skeleton { position: relative; overflow: hidden; background: var(--surface); }
.holdings-skeleton::after { position: absolute; inset: 0; background: var(--surface-2); content: ""; animation: holdings-pulse 1.1s ease-in-out infinite alternate; }
.holdings-skeleton--boundary { min-height: 108px; }
.holdings-skeleton--detail { min-height: 460px; }
.holdings-alert, .holdings-feedback { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 11px 14px; border: 1px solid var(--danger); border-radius: var(--radius); background: var(--surface); }
.holdings-alert.is-warning { border-color: var(--warning); }
.holdings-alert > div, .holdings-feedback { display: grid; }
.holdings-alert.is-warning > div { display: flex; min-width: 0; gap: 8px; overflow-x: auto; white-space: nowrap; }
.holdings-alert span, .holdings-feedback span { color: var(--text-2); }
.holdings-feedback { border-color: var(--info); }
.holdings-feedback.is-error { border-color: var(--danger); }
.holdings-fatal, .holdings-empty { display: flex; min-height: 320px; flex-direction: column; align-items: center; justify-content: center; padding: 24px; text-align: center; }
.holdings-fatal h2, .holdings-empty h2 { margin: 0; font-size: 22px; }
.holdings-fatal > p:not(.eyebrow), .holdings-empty > p:not(.eyebrow) { max-width: 680px; margin: 8px 0 0; color: var(--text-2); }
.holdings-fatal .primary-button, .holdings-empty .primary-button { margin-top: 18px; }
.holdings-boundary { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(0, 1.6fr); align-items: center; gap: 16px; padding: 14px; }
.holdings-boundary h2 { margin: 0; font-size: 18px; }
.holdings-boundary > div > p:last-child { margin: 5px 0 0; color: var(--text-2); font-size: 13px; }
.holdings-boundary dl { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; margin: 0; }
.holdings-boundary dl div { min-width: 0; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.holdings-boundary dt { color: var(--text-3); font-size: 12px; }
.holdings-boundary dd { margin: 3px 0 0; font-size: 15px; font-weight: 720; overflow-wrap: anywhere; }
.holdings-boundary .is-up { color: var(--up); }
.holdings-boundary .is-down { color: var(--down); }
.holdings-boundary .is-flat { color: var(--text-2); }
.holdings-boundary .is-missing { color: var(--text-3); }
.holdings-footer { padding-top: 3px; }
@keyframes holdings-pulse { from { opacity: .45; } to { opacity: .8; } }
@media (max-width: 1100px) {
  .holdings-intro { align-items: stretch; flex-direction: column; gap: 12px; }
  .holdings-intro__actions { justify-content: flex-start; }
  .holdings-stamp { min-width: 0; }
  .holdings-boundary { grid-template-columns: minmax(0, 1fr); }
  .holdings-boundary dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
  .holdings-page { gap: 8px; font-size: 18px; }
  .holdings-intro__actions, .holdings-intro__actions button { width: 100%; }
  .holdings-stamp, .holdings-boundary { grid-template-columns: minmax(0, 1fr); }
  .holdings-boundary dl { grid-template-columns: minmax(0, 1fr); }
  .holdings-alert { align-items: stretch; flex-direction: column; }
  .holdings-alert .secondary-button { width: 100%; }
  .holdings-fatal, .holdings-empty { min-height: 280px; padding: 18px 14px; }
  .holdings-empty .secondary-button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .holdings-skeleton::after { animation: none; }
}
</style>
