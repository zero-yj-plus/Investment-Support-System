<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import HoldingEditor from './HoldingEditor.vue'
import HoldingsTable from './HoldingsTable.vue'
import { useHoldings } from '../composables/useHoldings'
import type { NewHoldingInput } from '../types/holdings'
import { formatMoney, formatPercent, pnlTone } from '../utils/holdingsFormat'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const editorOpen = ref(false)
const editorMode = ref<'add' | 'edit'>('add')
const selectedCode = ref('')
const {
  holdings,
  views,
  loading,
  refreshing,
  quotesLoading,
  riskLoading,
  saving,
  adding,
  loaded,
  dirty,
  holdingsError,
  quotesError,
  riskError,
  saveError,
  addError,
  feedback,
  quotedCount,
  quoteAsOf,
  quoteSources,
  hasStaleQuotes,
  aggregate,
  refreshQuotes,
  refreshRisk,
  refresh,
  addHolding,
  editHolding,
  deleteHolding,
} = useHoldings()

const pageRefreshing = computed(() => refreshing.value || quotesLoading.value || riskLoading.value || saving.value || adding.value)
const dataStatus = computed(() => {
  if (holdingsError.value) return '账本暂不可用'
  if (loading.value) return '加载中'
  if (!holdings.value.length) return '暂无持仓'
  if (quotesError.value || quotedCount.value < holdings.value.length) return '部分报价暂不可用'
  if (hasStaleQuotes.value) return '陈旧报价'
  if (holdings.value.length && riskCoverageCount.value < holdings.value.length) return '风险评估待生成'
  if (dirty.value) return '有未保存变更'
  return '账本与报价可用'
})
const quoteStamp = computed(() => quoteAsOf.value || '—')
const sourceLabel = computed(() => quoteSources.value.length ? quoteSources.value.join(' / ') : '—')
const hasStaleRisk = computed(() => views.value.some((row) => row.algorithmRisk?.stale === true))
const riskCoverageCount = computed(() => views.value.filter((row) => row.algorithmRisk != null).length)
const riskCoverageComplete = computed(() => (
  holdings.value.length > 0 && riskCoverageCount.value === holdings.value.length
))
const triggeredRows = computed(() => views.value.filter((row) => (
  row.riskState === 'red' || row.riskState === 'orange' || row.riskState === 'stop_triggered' || row.riskState === 'target_triggered'
)))
const approachingRows = computed(() => views.value.filter((row) => (
  row.riskState === 'yellow' || row.riskState === 'near_stop' || row.riskState === 'near_target'
)))
const riskCounts = computed(() => ({
  red: views.value.filter((row) => row.riskState === 'red' || row.riskState === 'stop_triggered').length,
  orange: views.value.filter((row) => row.riskState === 'orange' || row.riskState === 'target_triggered').length,
  yellow: views.value.filter((row) => row.riskState === 'yellow' || row.riskState === 'near_stop' || row.riskState === 'near_target').length,
  blue: views.value.filter((row) => row.riskState === 'blue' || row.riskState === 'normal').length,
  gray: views.value.filter((row) => ['gray', 'quote_missing', 'quote_stale', 'unconfigured'].includes(row.riskState)).length,
}))
const portfolioSummary = computed(() => {
  const validRows = views.value.filter((row) => row.marketValue != null && row.marketValue >= 0)
  const largest = [...validRows].sort((left, right) => (
    (right.marketValue as number) - (left.marketValue as number)
  ))[0]
  const sectors = new Map<string, number>()
  for (const row of validRows) {
    const sector = String(row.algorithmRisk?.sector?.name || row.industry || '').trim()
    if (!sector) continue
    const current = sectors.get(sector)
    sectors.set(sector, (current == null ? 0 : current) + (row.marketValue as number))
  }
  const largestSector = [...sectors.entries()].sort((left, right) => right[1] - left[1])[0]
  const total = aggregate.value.marketValue
  return {
    largestName: largest?.name || largest?.code || '—',
    largestPct: largest?.holdingSharePct ?? null,
    sectorName: largestSector?.[0] || '数据暂缺',
    sectorPct: total != null && total > 0 && largestSector ? largestSector[1] / total * 100 : null,
    urgentCount: riskCounts.value.red + riskCounts.value.orange,
  }
})
const portfolioTodayPnl = computed(() => {
  if (!views.value.length) return { amount: null, pct: null, covered: 0 }
  let currentTotal = 0
  let previousTotal = 0
  let covered = 0
  for (const row of views.value) {
    if (row.marketValue == null || row.changePct == null) continue
    const rate = row.changePct / 100
    if (1 + rate <= 0) continue
    currentTotal += row.marketValue
    previousTotal += row.marketValue / (1 + rate)
    covered += 1
  }
  if (!covered || previousTotal <= 0) return { amount: null, pct: null, covered }
  const amount = currentTotal - previousTotal
  return {
    amount,
    pct: amount / previousTotal * 100,
    covered,
  }
})
const monitorStatus = computed(() => {
  if (pageRefreshing.value) return '正在更新'
  if (hasStaleRisk.value) return '含陈旧风险结论'
  if (holdingsError.value || quotesError.value || riskError.value || saveError.value) return '数据需检查'
  if (holdings.value.length && !riskCoverageComplete.value) return '风险评估待生成'
  if (dirty.value) return '等待保存'
  return '监控已生效'
})
const selectedHolding = computed(() => holdings.value.find((row) => row.code === selectedCode.value) || null)
const editorBusy = computed(() => saving.value || adding.value)
const editorError = computed(() => editorMode.value === 'add' ? addError.value || saveError.value : saveError.value)

watch(pageRefreshing, (value) => emit('refreshState', value), { immediate: true })

function openAddEditor() {
  editorMode.value = 'add'
  selectedCode.value = ''
  addError.value = ''
  saveError.value = ''
  editorOpen.value = true
}

function openEditEditor(code: string) {
  if (!holdings.value.some((row) => row.code === code)) return
  editorMode.value = 'edit'
  selectedCode.value = code
  addError.value = ''
  saveError.value = ''
  editorOpen.value = true
}

function closeEditor() {
  if (editorBusy.value) return
  editorOpen.value = false
}

async function handleEditorSubmit(input: NewHoldingInput) {
  const saved = editorMode.value === 'add'
    ? await addHolding(input)
    : await editHolding(selectedCode.value, input)
  if (saved) editorOpen.value = false
}

async function handleEditorRemove() {
  if (editorMode.value !== 'edit' || !selectedCode.value) return
  const removed = await deleteHolding(selectedCode.value)
  if (removed) editorOpen.value = false
}

onBeforeUnmount(() => emit('refreshState', false))
defineExpose({ refresh })
</script>

<template>
  <section class="holdings-page" aria-labelledby="holdings-page-title" :aria-busy="pageRefreshing">
    <header class="holdings-intro">
      <div class="page-heading">
        <div class="page-heading__line">
          <h1 id="holdings-page-title">我的持仓</h1>
          <span class="monitor-status" :class="{ 'is-warning': dirty || holdingsError || quotesError || riskError || saveError || hasStaleRisk || (holdings.length && !riskCoverageComplete) }">● {{ monitorStatus }}</span>
        </div>
        <p class="page-subtitle">每只持仓先告诉你现在该做什么，再说明为什么。</p>
      </div>
      <div class="holdings-intro__actions">
        <button type="button" class="primary-button" :disabled="loading || !!holdingsError || saving || adding" @click="openAddEditor">添加持仓</button>
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

        <div v-if="saveError" class="holdings-feedback is-error" role="alert">
          <strong>操作未完成</strong><span>{{ saveError }}</span>
        </div>

        <div v-if="riskError" class="holdings-alert is-warning" role="status">
          <div><strong>算法风险卡暂不可用</strong><span>{{ riskError }}；客户手填风险线仍按页面基础规则显示。</span></div>
          <button type="button" class="secondary-button" :disabled="riskLoading" @click="refreshRisk(true)">重试风险评估</button>
        </div>

        <div v-else-if="hasStaleRisk" class="holdings-alert is-warning" role="status">
          <div><strong>部分风险结论刷新失败</strong><span>页面保留最近有效结论及原评估时点，并明确标为陈旧；本轮没有用旧价生成新动作。</span></div>
          <button type="button" class="secondary-button" :disabled="riskLoading" @click="refreshRisk(true)">重新评估</button>
        </div>

        <div v-else-if="holdings.length && !riskCoverageComplete" class="holdings-alert is-warning" role="status">
          <div><strong>风险评估尚未覆盖全部持仓</strong><span>当前已生成 {{ riskCoverageCount }}/{{ holdings.length }} 只；缺失持仓不会补造风险线或动作。</span></div>
          <button type="button" class="secondary-button" :disabled="riskLoading" @click="refreshRisk(true)">生成风险卡</button>
        </div>

        <section v-if="holdings.length" class="panel portfolio-overview" aria-labelledby="portfolio-overview-title" aria-live="polite">
          <div class="portfolio-attention" :class="{ 'is-clear': !triggeredRows.length }">
            <div class="portfolio-attention__title">
              <span aria-hidden="true">{{ triggeredRows.length ? '急' : '稳' }}</span>
              <div class="portfolio-attention__heading"><small>组合行动</small><h2 id="portfolio-overview-title">{{ triggeredRows.length ? `今日需处理 ${triggeredRows.length}只` : '当前无需处理' }}</h2></div>
            </div>
            <div v-if="triggeredRows.length" class="portfolio-attention__rows">
              <button v-for="row in triggeredRows.slice(0, 3)" :key="row.code" type="button" :aria-label="`编辑${row.name || row.code}持仓`" @click="openEditEditor(row.code)">
                <strong>{{ row.name || row.code }}</strong><span>{{ row.algorithmRisk?.action || row.riskLabel }}</span><i aria-hidden="true">编辑</i>
              </button>
            </div>
            <p v-else>{{ approachingRows.length ? `${approachingRows.length}只持仓进入观察区，暂不需要调整仓位。` : '全部持仓均处于正常监控状态。' }}</p>
          </div>
          <dl class="portfolio-metrics">
            <div><dt>持仓总市值</dt><dd class="mono">{{ formatMoney(aggregate.marketValue) }}</dd><dd class="portfolio-metric__sub" :class="`is-${pnlTone(aggregate.pnl)}`">{{ aggregate.pnl == null ? '累计盈亏 —' : aggregate.pnl < 0 ? `累计亏损 ${formatMoney(aggregate.pnl)}` : `累计盈利 ${formatMoney(aggregate.pnl)}` }}</dd></div>
            <div><dt>今日总盈亏率</dt><dd class="mono" :class="`is-${pnlTone(portfolioTodayPnl.pct)}`">{{ formatPercent(portfolioTodayPnl.pct, true) }}</dd><dd class="portfolio-metric__sub" :class="`is-${pnlTone(portfolioTodayPnl.amount)}`">今日盈亏 {{ formatMoney(portfolioTodayPnl.amount) }}<small v-if="portfolioTodayPnl.covered < views.length">覆盖 {{ portfolioTodayPnl.covered }}/{{ views.length }} 只</small></dd></div>
            <div><dt>总浮动盈亏率</dt><dd class="mono" :class="`is-${pnlTone(aggregate.pnlPct)}`">{{ formatPercent(aggregate.pnlPct, true) }}</dd></div>
            <div><dt>最大单只持仓</dt><dd>{{ formatPercent(portfolioSummary.largestPct) }} <small>{{ portfolioSummary.largestName }}</small></dd></div>
            <div><dt>最大行业集中度</dt><dd>{{ formatPercent(portfolioSummary.sectorPct) }} <small>{{ portfolioSummary.sectorName }}</small></dd></div>
          </dl>
        </section>

        <HoldingEditor
          :open="editorOpen"
          :mode="editorMode"
          :holding="selectedHolding"
          :busy="editorBusy"
          :server-error="editorError"
          @submit="handleEditorSubmit"
          @close="closeEditor"
          @remove="handleEditorRemove"
        />

        <section v-if="!holdings.length" class="panel holdings-empty" aria-labelledby="holdings-empty-title">
          <p class="eyebrow">开始监控</p>
          <h2 id="holdings-empty-title">当前未记录持仓</h2>
          <p>添加股票、成本和数量后，系统会开始跟踪风险线和持仓行动。</p>
          <button type="button" class="secondary-button" @click="openAddEditor">添加第一笔持仓</button>
        </section>

        <HoldingsTable
          v-else
          :rows="views"
          @edit="openEditEditor"
        />

      </template>
    </template>

    <div class="holdings-data-line" :class="{ 'is-degraded': quotesError || hasStaleQuotes || hasStaleRisk || (holdings.length && !riskCoverageComplete) || holdingsError }" aria-label="持仓数据状态">
      <span><b>数据更新</b> {{ quoteStamp }}</span>
      <span>{{ dataStatus }}</span>
      <span v-if="quoteSources.length">{{ sourceLabel }}</span>
    </div>
  </section>
</template>

<style scoped>
.holdings-page { display: grid; min-width: 0; gap: 9px; }
.holdings-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.holdings-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.page-heading__line { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.monitor-status { display: inline-flex; align-items: center; padding: 4px 8px; border: 1px solid color-mix(in srgb, var(--up) 28%, var(--border)); border-radius: 8px; background: color-mix(in srgb, var(--up) 8%, var(--surface)); color: var(--up); font-size: 12px; font-weight: 750; }
.monitor-status.is-warning { border-color: color-mix(in srgb, var(--warning) 38%, var(--border)); background: color-mix(in srgb, var(--warning) 10%, var(--surface)); color: color-mix(in srgb, var(--warning) 80%, var(--text)); }
.holdings-intro > div > p:last-child { max-width: 800px; margin: 3px 0 0; color: var(--text-2); font-size: 12px; }
.holdings-intro__actions { display: flex; flex: 0 0 auto; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.holdings-intro__actions button { min-height: 44px; }
.holdings-data-line { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 5px 14px; padding: 1px 3px; color: var(--text-3); font-size: 12px; }
.holdings-data-line b { color: var(--text-2); font-weight: 700; }
.holdings-data-line.is-degraded { color: color-mix(in srgb, var(--warning) 78%, var(--text)); }
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
.portfolio-overview { display: grid; grid-template-columns: minmax(320px, .76fr) minmax(0, 1.54fr); min-height: 112px; overflow: hidden; padding: 0; }
.portfolio-attention { display: grid; align-content: center; gap: 7px; padding: 12px 16px; border-right: 1px solid var(--border); background: color-mix(in srgb, var(--warning) 6%, var(--surface)); }
.portfolio-attention.is-clear { background: color-mix(in srgb, var(--up) 5%, var(--surface)); }
.portfolio-attention__title { display: flex; align-items: center; gap: 10px; }
.portfolio-attention__title > span { display: grid; width: 32px; height: 32px; flex: 0 0 auto; place-items: center; border: 1px solid color-mix(in srgb, var(--warning) 50%, var(--border)); border-radius: 8px; background: var(--surface); color: var(--warning); font-size: 17px; font-weight: 900; }
.portfolio-attention.is-clear .portfolio-attention__title > span { border-color: color-mix(in srgb, var(--up) 45%, var(--border)); color: var(--up); }
.portfolio-attention__heading { display: flex; min-width: 0; align-items: baseline; flex-wrap: wrap; gap: 5px 9px; }
.portfolio-attention__title small { color: var(--text-3); font-size: 10px; }
.portfolio-attention__title h2 { margin: 0; color: var(--warning); font-size: 18px; line-height: 1.2; }
.portfolio-attention.is-clear .portfolio-attention__title h2 { color: var(--up); }
.portfolio-attention__rows { display: grid; gap: 5px; }
.portfolio-attention__rows button { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 8px; min-height: 44px; padding: 6px 10px; border: 1px solid color-mix(in srgb, var(--warning) 25%, var(--border)); border-radius: 8px; background: var(--surface); color: var(--text); text-align: left; cursor: pointer; }
.portfolio-attention__rows button:hover { border-color: var(--warning); }
.portfolio-attention__rows strong { overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.portfolio-attention__rows span { color: var(--warning); font-size: 12px; font-weight: 750; white-space: nowrap; }
.portfolio-attention__rows i { color: var(--text-3); font-size: 18px; font-style: normal; }
.portfolio-attention__rows button:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.portfolio-attention > p { margin: 0; color: var(--text-2); font-size: 12px; }
.portfolio-metrics { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); align-items: center; margin: 0; padding: 14px 10px; }
.portfolio-metrics > div { min-width: 0; padding: 6px 12px; text-align: center; }
.portfolio-metrics > div + div { border-left: 1px solid var(--border); }
.portfolio-metrics dt { color: var(--text-3); font-size: 11px; }
.portfolio-metrics dd { margin: 5px 0 0; color: var(--text); font-size: 17px; font-weight: 800; overflow-wrap: anywhere; }
.portfolio-metrics dd small { display: block; margin-top: 2px; color: var(--text-2); font-size: 10px; font-weight: 650; }
.portfolio-metric__sub { display: block; margin-top: 3px; color: var(--text-2); font-size: 10px; font-weight: 700; }
.portfolio-metric__sub small { display: block; margin-top: 2px; color: var(--text-3); font-size: 10px; font-weight: 600; }
.holdings-overview-grid { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(360px, .85fr); align-items: stretch; gap: 10px; }
.holdings-risk-summary { display: grid; grid-template-columns: minmax(180px, .7fr) minmax(0, 1.4fr); align-items: center; gap: 12px 18px; padding: 12px 14px; border-left: 3px solid var(--warning); }
.holdings-risk-summary.has-triggered { border-left-color: var(--danger); }
.holdings-risk-summary h2 { margin: 0; font-size: 18px; }
.holdings-risk-summary ul { display: flex; min-width: 0; flex-wrap: wrap; gap: 7px; margin: 0; padding: 0; list-style: none; }
.holdings-risk-summary li { display: inline-flex; gap: 6px; padding: 6px 9px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-2); font-size: 13px; }
.holdings-risk-summary li span, .holdings-risk-summary > p { color: var(--text-2); }
.risk-counts li { border-left-width: 3px; }
.risk-counts li.is-red { border-left-color: var(--danger); }
.risk-counts li.is-orange { border-left-color: #d97706; }
.risk-counts li.is-yellow { border-left-color: var(--warning); }
.risk-counts li.is-blue { border-left-color: var(--info); }
.risk-actions { grid-column: 1 / -1; padding-top: 8px !important; border-top: 1px solid var(--border); }
.holdings-risk-summary > p { grid-column: 1 / -1; margin: 0; font-size: 12px; }
.holdings-fatal, .holdings-empty { display: flex; min-height: 320px; flex-direction: column; align-items: center; justify-content: center; padding: 24px; text-align: center; }
.holdings-fatal h2, .holdings-empty h2 { margin: 0; font-size: 22px; }
.holdings-fatal > p:not(.eyebrow), .holdings-empty > p:not(.eyebrow) { max-width: 680px; margin: 8px 0 0; color: var(--text-2); }
.holdings-fatal .primary-button, .holdings-empty .primary-button { margin-top: 18px; }
.holdings-boundary { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(0, 1.6fr); align-items: center; gap: 16px; padding: 14px; }
.holdings-overview-grid .holdings-boundary { grid-template-columns: minmax(0, 1fr); align-content: center; gap: 8px; padding: 11px 12px; }
.holdings-overview-grid .holdings-boundary > div > p:last-child { display: none; }
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
  .holdings-boundary { grid-template-columns: minmax(0, 1fr); }
  .holdings-overview-grid { grid-template-columns: minmax(0, 1fr); }
  .holdings-risk-summary { grid-template-columns: minmax(0, 1fr); }
  .holdings-boundary dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .portfolio-overview { grid-template-columns: minmax(0, 1fr); }
  .portfolio-attention { border-right: 0; border-bottom: 1px solid var(--border); }
  .portfolio-attention__rows { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .portfolio-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px 0; }
  .portfolio-metrics > div:nth-child(4) { border-left: 0; }
}
@media (max-width: 720px) {
  .holdings-page { gap: 8px; font-size: 18px; }
  .holdings-intro__actions, .holdings-intro__actions button { width: 100%; }
  .holdings-data-line { justify-content: flex-start; }
  .portfolio-metrics { grid-template-columns: minmax(0, 1fr); }
  .portfolio-metrics > div + div { border-top: 1px solid var(--border); border-left: 0; }
  .portfolio-attention__rows, .portfolio-metrics { grid-template-columns: minmax(0, 1fr); }
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
