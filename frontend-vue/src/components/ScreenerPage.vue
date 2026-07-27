<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import type { QualityState } from '../types/dashboard'
import { formatDateTime, formatInteger, formatRatioPercent } from '../utils/screenerFormat'
import { useScreener } from '../composables/useScreener'
import DataStatus from './DataStatus.vue'
import ScreenerDataHealth from './ScreenerDataHealth.vue'
import ScreenerFilters from './ScreenerFilters.vue'
import ScreenerResults from './ScreenerResults.vue'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  filters,
  response,
  stocks,
  loading,
  showLoading,
  elapsedSeconds,
  transportError,
  formErrors,
  resultStale,
  hasSubmitted,
  watchlistStates,
  watchlistErrors,
  busy,
  runScreen,
  stopScreen,
  markDirty,
  resetFilters,
  addToWatchlist,
  refresh,
} = useScreener()

watch(busy, (value) => emit('refreshState', value), { immediate: true })
onBeforeUnmount(() => emit('refreshState', false))

const quality = computed<QualityState>(() => {
  if (!response.value) return 'partial'
  if (response.value.ok === false || response.value.coverage?.sufficient === false) return 'missing'
  if (response.value.missing?.length || response.value.universe?.stale) return 'partial'
  return 'complete'
})
const qualityLabel = computed(() => !response.value ? '等待筛选' : quality.value === 'complete' ? '依据完整' : quality.value === 'partial' ? '部分覆盖' : '依据不足')
const conclusion = computed(() => {
  if (!response.value) return { eyebrow: '尚未形成结论', title: '先设置条件，再执行一次全市场筛选', detail: '页面不会预载候选或使用示例数据。' }
  if (response.value.ok === false) return { eyebrow: '本批次不可用', title: '行情或股票池覆盖未达到规则门槛', detail: '不展示候选，不把局部数据冒充全市场结果。' }
  if (!stocks.value.length) return { eyebrow: '本批次无命中', title: '返回真实空数组', detail: response.value.missing?.length ? '同时存在局部缺失，不能断言全市场没有候选。' : '可调整条件后重新执行。' }
  return { eyebrow: '确定性候选已形成', title: `命中 ${formatInteger(response.value.matched)} 只，当前展示 ${formatInteger(response.value.returned)} 只`, detail: '下一步是逐只进入个股诊断；筛选结果本身不是推荐。' }
})

async function submit() {
  await runScreen()
}

defineExpose({ refresh })
</script>

<template>
  <section class="screener-page" aria-labelledby="screener-page-title" :aria-busy="busy">
    <header class="screener-intro">
      <div>
        <p class="eyebrow">研究中心 · 全市场确定性筛选</p>
        <h1 id="screener-page-title">条件选股</h1>
        <p>用价格、量能、估值、规模和行业条件缩小研究范围；覆盖不足时不产生候选。</p>
      </div>
      <dl class="screener-stamp" aria-label="条件选股数据日期、生成时点与依据状态">
        <div><dt>数据日期</dt><dd class="mono">{{ response?.trade_date || '—' }}</dd></div>
        <div><dt>生成时点</dt><dd class="mono">{{ response?.ts ? formatDateTime(response.ts) : '—' }}</dd></div>
        <div><dt>依据状态</dt><dd><DataStatus :state="quality" :label="qualityLabel" /></dd></div>
      </dl>
    </header>

    <section class="panel screener-decision" aria-labelledby="screener-decision-title">
      <div class="screener-decision-copy">
        <p class="eyebrow">{{ conclusion.eyebrow }}</p>
        <h2 id="screener-decision-title">{{ conclusion.title }}</h2>
        <p>{{ conclusion.detail }}</p>
      </div>
      <dl class="screener-decision-stats" aria-label="本次筛选命中与覆盖摘要">
        <div><dt>命中</dt><dd class="mono">{{ formatInteger(response?.matched) }}<small v-if="response?.matched != null"> 只</small></dd></div>
        <div><dt>快照覆盖</dt><dd class="mono">{{ formatRatioPercent(response?.coverage?.percent) }}</dd></div>
        <div><dt>规则门槛</dt><dd class="mono">{{ response?.coverage?.minimum_ratio == null ? '80.00%' : formatRatioPercent(response.coverage.minimum_ratio * 100) }}</dd></div>
      </dl>
      <div class="screener-primary-action">
        <button class="primary-button" type="submit" form="screener-filter-form" :disabled="loading" :aria-busy="loading">
          {{ loading ? '筛选进行中' : response ? '按当前条件重新筛选' : '开始筛选' }}
        </button>
        <small>普通筛选最多返回前 300 只；接口最长等待 30 秒。</small>
      </div>
    </section>

    <ScreenerFilters v-model="filters" :errors="formErrors" :stale="resultStale" @submit="submit" @reset="resetFilters" @dirty="markDirty" />

    <ScreenerResults
      :response="response"
      :stocks="stocks"
      :has-submitted="hasSubmitted"
      :loading="loading"
      :show-loading="showLoading"
      :elapsed-seconds="elapsedSeconds"
      :transport-error="transportError"
      :stale="resultStale"
      :watchlist-states="watchlistStates"
      :watchlist-errors="watchlistErrors"
      @retry="submit"
      @stop="stopScreen"
      @add-watchlist="addToWatchlist"
    />

    <ScreenerDataHealth v-if="response" :response="response" />

    <footer class="page-footer screener-footer">
      <span>价格、流通市值与成交额响应单位为元；表单市值与成交额输入单位为亿元；涨跌幅与换手率为百分数。</span>
      <span>缺失、降级、陈旧、真实空数组和接口失败分别展示；任何缺失值都不补 0。</span>
    </footer>
  </section>
</template>

<style scoped>
.screener-page { display: grid; min-width: 0; gap: 12px; }
.screener-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.screener-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.screener-intro > div > p:last-child { max-width: 820px; margin: 7px 0 0; color: var(--text-2); }
.screener-stamp { display: grid; min-width: 510px; grid-template-columns: minmax(110px, .8fr) minmax(170px, 1fr) minmax(150px, 1fr); gap: 12px; margin: 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.screener-stamp div { min-width: 0; }
.screener-stamp dt { color: var(--text-3); font-size: 12px; }
.screener-stamp dd { margin: 2px 0 0; font-size: 13px; font-weight: 720; overflow-wrap: anywhere; }
.screener-stamp :deep(.data-status) { min-height: 26px; white-space: normal; }
.screener-decision { display: grid; grid-template-columns: minmax(0, 1fr) minmax(300px, .55fr) minmax(210px, .34fr); align-items: stretch; overflow: hidden; }
.screener-decision-copy { min-width: 0; padding: 14px 16px; }
.screener-decision-copy h2 { margin: 0; font-size: clamp(18px, 1.5vw, 23px); line-height: 1.4; }
.screener-decision-copy > p:last-child { margin: 5px 0 0; color: var(--text-2); }
.screener-decision-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0; border-left: 1px solid var(--border); }
.screener-decision-stats div { display: grid; min-width: 0; align-content: center; padding: 10px; border-right: 1px solid var(--border); }
.screener-decision-stats dt { color: var(--text-3); font-size: 12px; }
.screener-decision-stats dd { margin: 3px 0 0; color: var(--text); font-size: 20px; font-weight: 760; }
.screener-decision-stats small { font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; font-size: 12px; }
.screener-primary-action { display: grid; align-content: center; gap: 5px; padding: 12px; background: var(--surface-2); }
.screener-primary-action .primary-button { width: 100%; }
.screener-primary-action small { color: var(--text-3); font-size: 12px; line-height: 1.45; }
.screener-footer { padding-top: 0; }
@media (max-width: 1240px) {
  .screener-intro { align-items: stretch; flex-direction: column; gap: 12px; }
  .screener-stamp { min-width: 0; }
  .screener-decision { grid-template-columns: minmax(0, 1fr) minmax(310px, .62fr); }
  .screener-primary-action { grid-column: 1 / -1; grid-template-columns: minmax(210px, 320px) minmax(0, 1fr); align-items: center; }
}
@media (max-width: 860px) {
  .screener-decision { grid-template-columns: minmax(0, 1fr); }
  .screener-decision-stats { border-top: 1px solid var(--border); border-left: 0; }
}
@media (max-width: 720px) {
  .screener-page { gap: 8px; }
  .screener-stamp { grid-template-columns: minmax(0, 1fr); }
  .screener-decision-stats { grid-template-columns: minmax(0, 1fr); }
  .screener-decision-stats div { min-height: 70px; border-right: 0; border-bottom: 1px solid var(--border); }
  .screener-decision-stats div:last-child { border-bottom: 0; }
  .screener-primary-action { grid-template-columns: minmax(0, 1fr); }
}
</style>
