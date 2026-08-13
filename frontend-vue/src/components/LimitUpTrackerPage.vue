<script setup lang="ts">
import { computed, watch } from 'vue'
import LimitUpSnapshotNav from './LimitUpSnapshotNav.vue'
import LimitUpTrackerStats from './LimitUpTrackerStats.vue'
import LimitUpTrackerTable from './LimitUpTrackerTable.vue'
import { useLimitUpTracker } from '../composables/useLimitUpTracker'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  dateData,
  snapshot,
  selectedDate,
  pendingDate,
  loading,
  refreshing,
  switching,
  error,
  backfill,
  backfilling,
  busy,
  selectDate,
  startBackfill,
  refresh,
} = useLimitUpTracker()

watch(busy, (value) => emit('refreshState', value), { immediate: true })

const notes = computed(() => [...new Set([
  ...(dateData.value?.missing || []),
  ...(snapshot.value?.missing || []),
  ...(snapshot.value?.degraded_reasons || []),
].filter(Boolean))])

const liveMessage = computed(() => {
  if (switching.value && pendingDate.value) return `正在切换到 ${pendingDate.value} 的完整快照`
  if (backfilling.value) return '正在补齐最近20个交易日的真实快照和到期结果'
  if (error.value) return error.value
  if (snapshot.value) return `已显示 ${snapshot.value.baseline_date} 涨停追踪，共 ${snapshot.value.baseline_count ?? '未知'} 只`
  return '涨停追踪正在加载'
})

defineExpose({ refresh })
</script>

<template>
  <section class="limitup-tracker-page" aria-labelledby="limitup-tracker-title" :aria-busy="busy">
    <header class="tracker-intro">
      <div class="page-heading">
        <h1 id="limitup-tracker-title">涨停追踪</h1>
        <p class="page-subtitle">按涨停基准日追踪次日 T+1 与第三日 T+2；盘中值明确标记，收盘结果由服务端冻结。</p>
      </div>
    </header>

    <LimitUpSnapshotNav
      :dates="dateData?.dates || []"
      :selected-date="selectedDate"
      :pending-date="pendingDate"
      :disabled="loading"
      :backfill="backfill"
      @select="selectDate"
      @backfill="startBackfill"
    />

    <p class="sr-only" aria-live="polite">{{ liveMessage }}</p>

    <div v-if="error" class="tracker-alert" role="alert">
      <div><strong>{{ snapshot ? '最新请求失败，保留上一批完整快照' : '涨停追踪暂不可用' }}</strong><span>{{ error }}</span></div>
      <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh">重试</button>
    </div>

    <div v-if="loading && !snapshot" class="tracker-loading" role="status" aria-live="polite">
      <span class="sr-only">涨停追踪正在加载</span>
      <section class="panel tracker-skeleton tracker-skeleton--summary"></section>
      <section class="tracker-skeleton-grid"><i v-for="item in 5" :key="item" class="panel"></i></section>
      <section class="panel tracker-skeleton tracker-skeleton--table"></section>
    </div>

    <div v-else-if="snapshot" class="tracker-batch" :class="{ 'is-switching': switching }">
      <div v-if="switching" class="tracker-switch-overlay" role="status">
        <strong>正在加载 {{ pendingDate }} 完整快照</strong>
        <span>日期、五项统计与明细表将在同一响应完成后一起切换</span>
      </div>

      <section class="panel tracker-summary" aria-labelledby="tracker-summary-title">
        <div>
          <span>涨停基准日</span>
          <h2 id="tracker-summary-title" class="mono">{{ snapshot.baseline_date }}</h2>
        </div>
        <dl>
          <div><dt>涨停股票</dt><dd class="mono">{{ snapshot.baseline_count == null ? '数据暂缺' : `${snapshot.baseline_count} 只` }}</dd></div>
          <div><dt>当前追踪状态</dt><dd>{{ snapshot.tracking_status }}</dd></div>
          <div><dt>快照性质</dt><dd>{{ snapshot.is_intraday ? '盘中快照／未定稿' : snapshot.is_final ? '收盘定稿／全部追踪完成' : '基准池已定稿' }}</dd></div>
        </dl>
      </section>

      <LimitUpTrackerStats :snapshot="snapshot" />

      <LimitUpTrackerTable :stocks="snapshot.stocks" :t1-date="snapshot.t1_date" :t2-date="snapshot.t2_date" />

      <dl class="tracker-provenance panel" aria-label="数据来源、日期、时点、缺失与降级说明">
        <div><dt>数据日期</dt><dd class="mono">{{ snapshot.data_date || '—' }}</dd></div>
        <div><dt>生成时间</dt><dd class="mono">{{ snapshot.generated_at || '—' }}</dd></div>
        <div><dt>行情时点</dt><dd class="mono">{{ snapshot.market_time || '—' }}</dd></div>
        <div><dt>数据来源</dt><dd>{{ snapshot.sources?.length ? snapshot.sources.join('；') : '数据来源暂缺' }}</dd></div>
        <div class="tracker-provenance__notes">
          <dt>缺失、降级与覆盖</dt>
          <dd v-if="notes.length"><span v-for="note in notes" :key="note">{{ note }}</span></dd>
          <dd v-else>服务端未声明缺失或降级；T+1/T+2 仍以各字段状态为准。</dd>
        </div>
      </dl>
    </div>

    <section v-else-if="!loading" class="panel tracker-empty">
      <strong>没有可展示的真实快照</strong>
      <span>服务端交易日历或涨停池暂不可用；页面不会生成示例数据。</span>
    </section>

    <footer class="page-footer tracker-footer">
      <span>次日晋级由服务端按同源涨停池计算；前端不推导排名或业务结论。</span>
      <span>缺失保持缺失，盘中动态不会覆盖已经冻结的历史收盘结果。</span>
    </footer>
  </section>
</template>

<style scoped>
.limitup-tracker-page { display: grid; min-width: 0; gap: 10px; }
.tracker-intro { padding: 4px 2px 2px; }
.tracker-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); }
.tracker-batch { position: relative; display: grid; min-height: 560px; gap: 10px; }
.tracker-batch.is-switching > :not(.tracker-switch-overlay) { opacity: .48; }
.tracker-switch-overlay { position: absolute; inset: 0; z-index: 9; display: grid; place-content: start center; gap: 4px; padding-top: 110px; color: var(--text); text-align: center; }
.tracker-switch-overlay strong { font-size: 16px; }
.tracker-switch-overlay span { color: var(--text-2); font-size: 13px; }
.tracker-summary { display: grid; grid-template-columns: minmax(190px, .6fr) minmax(0, 1.8fr); align-items: stretch; }
.tracker-summary > div { display: grid; align-content: center; padding: 12px 14px; border-right: 1px solid var(--border); }
.tracker-summary > div > span, .tracker-summary dt { color: var(--text-3); font-size: 12px; }
.tracker-summary h2 { margin: 3px 0 0; font-size: 22px; }
.tracker-summary dl { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0; }
.tracker-summary dl div { display: grid; align-content: center; min-height: 76px; padding: 10px 14px; border-right: 1px solid var(--border); }
.tracker-summary dl div:last-child { border-right: 0; }
.tracker-summary dd { margin: 4px 0 0; font-size: 15px; font-weight: 750; }
.tracker-provenance { display: grid; grid-template-columns: minmax(120px, .7fr) minmax(180px, 1fr) minmax(180px, 1fr) minmax(260px, 2fr); gap: 0; }
.tracker-provenance > div { min-width: 0; padding: 9px 11px; border-right: 1px solid var(--border); }
.tracker-provenance > div:nth-child(4) { border-right: 0; }
.tracker-provenance dt { color: var(--text-3); font-size: 11px; }
.tracker-provenance dd { margin: 3px 0 0; font-size: 12px; font-weight: 650; overflow-wrap: anywhere; }
.tracker-provenance__notes { display: grid; grid-column: 1 / -1; grid-template-columns: auto minmax(0, 1fr); gap: 12px; border-top: 1px solid var(--border); border-right: 0 !important; }
.tracker-provenance__notes dd { display: flex; flex-wrap: wrap; gap: 2px 10px; color: var(--text-2); font-weight: 500; }
.tracker-provenance__notes dd span:not(:last-child)::after { color: var(--text-3); content: "；"; }
.tracker-alert { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 11px 13px; border: 1px solid var(--danger); border-radius: var(--radius); background: color-mix(in srgb, var(--danger) 7%, var(--surface)); }
.tracker-alert > div { display: grid; gap: 2px; }
.tracker-alert span { color: var(--text-2); font-size: 13px; }
.tracker-loading { display: grid; gap: 10px; }
.tracker-skeleton, .tracker-skeleton-grid i { position: relative; overflow: hidden; }
.tracker-skeleton::after, .tracker-skeleton-grid i::after { position: absolute; inset: 0; background: color-mix(in srgb, var(--surface-3) 66%, transparent); content: ""; animation: tracker-pulse 1.1s ease-in-out infinite alternate; }
.tracker-skeleton--summary { min-height: 78px; }
.tracker-skeleton--table { min-height: 390px; }
.tracker-skeleton-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
.tracker-skeleton-grid i { min-height: 116px; }
.tracker-empty { display: grid; min-height: 320px; place-content: center; gap: 5px; color: var(--text-2); text-align: center; }
.tracker-empty span { color: var(--text-3); font-size: 13px; }
.tracker-footer { padding-top: 2px; }
@keyframes tracker-pulse { from { opacity: .4; } to { opacity: .78; } }
@media (max-width: 980px) {
  .tracker-summary { grid-template-columns: 1fr; }
  .tracker-summary > div { border-right: 0; border-bottom: 1px solid var(--border); }
  .tracker-provenance { grid-template-columns: 1fr 1fr; }
  .tracker-provenance > div { border-bottom: 1px solid var(--border); }
  .tracker-provenance > div:nth-child(2n) { border-right: 0; }
  .tracker-skeleton-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 680px) {
  .tracker-summary dl { grid-template-columns: 1fr; }
  .tracker-summary dl div { min-height: 60px; border-right: 0; border-bottom: 1px solid var(--border); }
  .tracker-summary dl div:last-child { border-bottom: 0; }
  .tracker-provenance { grid-template-columns: 1fr; }
  .tracker-provenance > div { border-right: 0; }
  .tracker-provenance__notes { grid-template-columns: 1fr; gap: 2px; }
  .tracker-skeleton-grid { grid-template-columns: 1fr 1fr; }
  .tracker-alert { align-items: stretch; flex-direction: column; }
}
@media (prefers-reduced-motion: reduce) {
  .tracker-skeleton::after, .tracker-skeleton-grid i::after { animation: none; }
  .tracker-batch.is-switching > :not(.tracker-switch-overlay) { opacity: .7; }
}
</style>
