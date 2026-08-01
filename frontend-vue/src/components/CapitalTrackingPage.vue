<script setup lang="ts">
import { computed, watch } from 'vue'
import CapitalRankingPanel from './CapitalRankingPanel.vue'
import CapitalTrendPanel from './CapitalTrendPanel.vue'
import { useCapitalTracking } from '../composables/useCapitalTracking'
import { isRawCapital } from '../utils/capitalFormat'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  data,
  sectors,
  hotStocks,
  loading,
  refreshing,
  snapshotting,
  error,
  actionFeedback,
  refresh,
  takeSnapshot,
} = useCapitalTracking()

watch(refreshing, (value) => emit('refreshState', value), { immediate: true })

const hasRows = computed(() => sectors.value.length > 0 || hotStocks.value.length > 0)
const isEmpty = computed(() => !loading.value && !error.value && data.value?.ok !== false && !hasRows.value)
const isRaw = computed(() => sectors.value.some(isRawCapital) || hotStocks.value.some(isRawCapital))
const isStale = computed(() => Boolean(data.value?.stale_sector || data.value?.stale_hot))
const statusLabel = computed(() => {
  if (error.value && !data.value) return '暂不可用'
  if (data.value?.ok === false) return '未启用'
  if (isStale.value) return '陈旧快照'
  if (isRaw.value) return '单位部分未核验'
  if (isEmpty.value) return '真实空结果'
  if (hasRows.value) return '数据可用'
  return '状态确认中'
})

defineExpose({ refresh })
</script>

<template>
  <section class="capital-page" aria-labelledby="capital-page-title" :aria-busy="loading || refreshing || snapshotting">
    <header class="capital-intro">
      <div class="page-heading">
        <h1 id="capital-page-title">资金追踪</h1>
        <p class="page-subtitle">追踪行业与热门股的主力资金日内流向；先辨认数据日期、单位与连续性，再复核资金聚焦和分歧。</p>
      </div>
      <button
        type="button"
        class="primary-button capital-snapshot-button"
        :disabled="snapshotting"
        @click="takeSnapshot"
      >{{ snapshotting ? '采集中…' : '采集真实快照' }}</button>
    </header>

    <div class="sr-only" aria-live="polite">{{ refreshing ? '资金追踪正在刷新' : actionFeedback?.message || (error ? `资金追踪读取异常：${error}` : `资金追踪状态：${statusLabel}`) }}</div>

    <div v-if="loading" class="capital-loading" role="status" aria-live="polite">
      <span class="sr-only">资金追踪正在加载</span>
      <section class="panel capital-skeleton capital-skeleton--ranking"></section>
      <section class="panel capital-skeleton capital-skeleton--trend"></section>
    </div>

    <template v-else>
      <div v-if="error" class="capital-alert" role="alert">
        <div><strong>{{ data ? '最新刷新失败，保留已加载资金快照' : '资金追踪暂不可用' }}</strong><span>{{ error }}</span></div>
        <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh">重试读取</button>
      </div>

      <div v-if="actionFeedback" class="capital-action-feedback" :class="`is-${actionFeedback.tone}`" role="status">
        <strong>{{ actionFeedback.tone === 'success' ? '采集完成' : actionFeedback.tone === 'warning' ? '采集部分完成' : '采集失败' }}</strong>
        <span>{{ actionFeedback.message }}</span>
      </div>

      <section v-if="data?.ok === false" class="panel capital-disabled" aria-labelledby="capital-disabled-title">
        <p class="eyebrow">功能状态</p><h2 id="capital-disabled-title">资金追踪未启用</h2>
        <p>{{ data.msg || '当前部署未初始化资金追踪时序库。页面不会把未启用误报为“今日无资金”。' }}</p>
      </section>

      <section v-else-if="isEmpty" class="panel capital-empty" aria-labelledby="capital-empty-title">
        <p class="eyebrow">真实空结果</p><h2 id="capital-empty-title">当前无资金快照</h2>
        <p>接口成功返回行业与热门股空数组。可能尚未采集或公开源当前为空；页面不使用历史或示例数字补位。</p>
      </section>

      <template v-else-if="hasRows">
        <div class="capital-ranking-grid">
          <CapitalRankingPanel id="capital-sector-ranking" title="行业资金榜" kind="sector" :rows="sectors" :source="data?.sector_source" :data-date="data?.sector_date" :stale="data?.stale_sector" />
          <CapitalRankingPanel id="capital-stock-ranking" title="热门股资金榜" kind="stock" :rows="hotStocks" :source="data?.hot_source" :data-date="data?.hot_date" :stale="data?.stale_hot" />
        </div>
        <CapitalTrendPanel :sectors="sectors" :hot-stocks="hotStocks" />
      </template>

      <footer class="page-footer capital-footer">
        <span>金额字段：已核验 `CNY` 按元展示；`tdx_raw_unverified` 只展示原始公式值，不换算亿元。</span>
        <span>本页只展示服务端资金快照，不生成买卖、仓位或价格区间结论。</span>
      </footer>
    </template>

    <dl class="capital-stamp data-footnote-stamp" :class="{ 'is-stale': isStale, 'is-degraded': isRaw || data?.ok === false }" aria-label="资金追踪数据日期、采样时点与状态">
      <div><dt>行业日期</dt><dd class="mono">{{ data?.sector_date || '—' }}<span v-if="!data?.sector_date" class="sr-only">数据缺失</span></dd></div>
      <div><dt>热门股日期</dt><dd class="mono">{{ data?.hot_date || '—' }}<span v-if="!data?.hot_date" class="sr-only">数据缺失</span></dd></div>
      <div><dt>最近采样</dt><dd class="mono">{{ data?.snap_ts || '—' }}<span v-if="!data?.snap_ts" class="sr-only">数据缺失</span></dd></div>
      <div><dt>数据状态</dt><dd>{{ statusLabel }}</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.capital-page { display: grid; min-width: 0; gap: 12px; }
.capital-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.page-heading { min-width: 0; flex: 1; }
.capital-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.capital-intro > div > p:last-child { max-width: 780px; margin: 7px 0 0; color: var(--text-2); }
.capital-snapshot-button { min-width: 156px; flex: 0 0 auto; }
.capital-stamp { display: grid; min-width: 520px; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px 14px; margin: 0; padding: 9px 11px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.capital-stamp.is-degraded { border-color: var(--warning); }
.capital-stamp.is-stale { border-color: var(--danger); }
.capital-stamp div { min-width: 0; }
.capital-stamp dt { color: var(--text-3); font-size: 12px; }
.capital-stamp dd { margin: 1px 0 0; font-size: 13px; font-weight: 720; overflow-wrap: anywhere; }
.capital-loading { display: grid; gap: 12px; }
.capital-skeleton { position: relative; overflow: hidden; }
.capital-skeleton::after { position: absolute; inset: 0; background: color-mix(in srgb, var(--surface-3) 68%, transparent); content: ""; animation: capital-pulse 1.2s ease-in-out infinite alternate; }
.capital-skeleton--ranking { min-height: 420px; }
.capital-skeleton--trend { min-height: 310px; }
.capital-alert { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 12px 14px; border: 1px solid var(--danger); border-radius: var(--radius); background: color-mix(in srgb, var(--danger) 7%, var(--surface)); }
.capital-alert > div, .capital-action-feedback { display: grid; gap: 2px; }
.capital-alert span, .capital-action-feedback span { color: var(--text-2); }
.capital-action-feedback { padding: 10px 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.capital-action-feedback.is-success { border-color: var(--info); }
.capital-action-feedback.is-warning { border-color: var(--warning); }
.capital-action-feedback.is-danger { border-color: var(--danger); }
.capital-disabled, .capital-empty { display: flex; min-height: 280px; flex-direction: column; align-items: center; justify-content: center; padding: 24px; text-align: center; }
.capital-disabled h2, .capital-empty h2 { margin: 0; font-size: 22px; }
.capital-disabled > p:not(.eyebrow), .capital-empty > p:not(.eyebrow) { max-width: 620px; margin: 8px 0 0; color: var(--text-3); }
.capital-ranking-grid { display: grid; min-width: 0; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; align-items: start; }
.capital-footer { padding-top: 3px; }
@keyframes capital-pulse { from { opacity: .42; } to { opacity: .78; } }
@media (max-width: 1024px) {
  .capital-stamp { min-width: 0; }
  .capital-ranking-grid { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 720px) {
  .capital-page { gap: 8px; }
  .capital-intro { align-items: stretch; flex-direction: column; gap: 12px; }
  .capital-snapshot-button { width: 100%; }
  .capital-stamp { grid-template-columns: minmax(0, 1fr); }
  .capital-alert { align-items: stretch; flex-direction: column; }
  .capital-alert .secondary-button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .capital-skeleton::after { animation: none; }
}
</style>
