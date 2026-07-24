<script setup lang="ts">
import { computed } from 'vue'
import type { TomorrowReportResponse, TomorrowWatchlistResponse } from '../types/tomorrow'

const props = defineProps<{
  report: TomorrowReportResponse | null
  watchlist: TomorrowWatchlistResponse | null
  reportError?: string
  watchlistError?: string
}>()

const sourceRows = computed(() => {
  const sources = props.report?.input_lineage?.sources || []
  return [
    { type: 'tail', label: '尾盘定稿', source: sources.find((item) => item.rtype === 'tail') },
    { type: 'close', label: '收盘复盘', source: sources.find((item) => item.rtype === 'close') },
  ]
})
const sourceComplete = computed(() => sourceRows.value.every((item) => Boolean(item.source)))
const reportState = computed(() => {
  if (!props.report?.content_md) return { label: props.reportError ? '暂不可用' : '未定稿', tone: 'is-error' }
  if (props.report.stale) return { label: '陈旧定稿', tone: 'is-warning' }
  if (!sourceComplete.value) return { label: '部分覆盖', tone: 'is-warning' }
  return { label: '来源完整', tone: 'is-ok' }
})
const watchState = computed(() => {
  if (props.watchlistError) return { label: '请求失败', tone: 'is-error' }
  const items = props.watchlist?.items
  if (!Array.isArray(items)) return { label: '尚未读取', tone: 'is-neutral' }
  if (!items.length) return { label: '真实空结果', tone: 'is-neutral' }
  const hasMissing = items.some((item) => item.missing?.length || item.quote?.price == null || item.target_low == null || item.target_high == null)
  return hasMissing ? { label: '部分覆盖', tone: 'is-warning' } : { label: '数据完整', tone: 'is-ok' }
})

function shortHash(value?: string): string {
  return value ? value.slice(0, 12) : '未记录'
}
</script>

<template>
  <section class="panel tomorrow-health" aria-labelledby="tomorrow-health-title">
    <header class="tomorrow-health__head">
      <div><p class="eyebrow">DATA HEALTH</p><h2 id="tomorrow-health-title">依据完整性与治理</h2></div>
      <span class="tomorrow-health__state" :class="reportState.tone">{{ reportState.label }}</span>
    </header>

    <dl class="tomorrow-health__summary">
      <div><dt>策略来源</dt><dd>{{ sourceComplete ? '尾盘 + 收盘同日定稿' : '上游定稿不完整' }}</dd></div>
      <div><dt>观察池</dt><dd><span :class="watchState.tone">{{ watchState.label }}</span></dd></div>
      <div><dt>策略日期</dt><dd class="mono">{{ report?.date || '待确认' }}</dd></div>
    </dl>

    <div class="tomorrow-health__sources" aria-label="次日策略上游报告">
      <div v-for="row in sourceRows" :key="row.type">
        <span aria-hidden="true" :class="row.source ? 'is-present' : 'is-missing'"></span>
        <p><strong>{{ row.label }}</strong><small v-if="row.source" class="mono">{{ row.source.trade_day || '日期缺失' }} · v{{ row.source.version ?? '—' }}</small><small v-else>缺失：本次策略未记录该上游定稿</small></p>
      </div>
    </div>

    <details class="tomorrow-health__details">
      <summary>版本与推送状态</summary>
      <dl>
        <div><dt>报告指纹</dt><dd class="mono">{{ shortHash(report?.content_hash) }}</dd></div>
        <div><dt>来源锁</dt><dd class="mono">{{ shortHash(report?.input_lineage?.lineage_hash) }}</dd></div>
        <div><dt>价格校验</dt><dd>{{ report?.claim_validation?.status || '未记录' }}</dd></div>
        <div><dt>建议治理</dt><dd>{{ report?.advice_governance?.status || '未记录' }}</dd></div>
        <div><dt>推送</dt><dd>{{ report?.pushed ? '已提交推送，未确认送达' : '尚未提交推送' }}</dd></div>
      </dl>
    </details>
    <p v-if="reportError || watchlistError" class="tomorrow-health__warning" role="status">局部降级：{{ [reportError, watchlistError].filter(Boolean).join('；') }}</p>
  </section>
</template>

<style scoped>
.tomorrow-health { min-width: 0; overflow: hidden; }
.tomorrow-health__head { display: flex; min-height: 68px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.tomorrow-health__head h2 { margin: 0; font-size: 18px; }
.tomorrow-health__state { padding: 3px 8px; border: 1px solid currentColor; border-radius: 999px; font-size: 12px; font-weight: 750; white-space: nowrap; }
.is-ok { color: var(--info); }
.is-warning { color: var(--warning); }
.is-error { color: var(--danger); }
.is-neutral { color: var(--text-3); }
.tomorrow-health__summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0; border-bottom: 1px solid var(--border); }
.tomorrow-health__summary div { min-width: 0; padding: 10px 12px; border-right: 1px solid var(--border); }
.tomorrow-health__summary div:last-child { border-right: 0; }
.tomorrow-health__summary dt { color: var(--text-3); font-size: 12px; }
.tomorrow-health__summary dd { margin: 3px 0 0; color: var(--text-2); font-size: 13px; overflow-wrap: anywhere; }
.tomorrow-health__sources { display: grid; }
.tomorrow-health__sources > div { display: grid; grid-template-columns: 12px minmax(0, 1fr); align-items: center; gap: 8px; min-height: 58px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
.tomorrow-health__sources > div > span { width: 8px; height: 8px; border: 2px solid currentColor; border-radius: 50%; color: var(--danger); }
.tomorrow-health__sources > div > span.is-present { color: var(--info); }
.tomorrow-health__sources p { display: grid; gap: 2px; margin: 0; }
.tomorrow-health__sources small { color: var(--text-3); font-size: 12px; }
.tomorrow-health__details summary { min-height: 44px; padding: 11px 12px; color: var(--text-2); font-weight: 700; cursor: pointer; }
.tomorrow-health__details dl { display: grid; gap: 0; margin: 0; border-top: 1px solid var(--border); }
.tomorrow-health__details dl div { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 8px; padding: 7px 12px; border-bottom: 1px solid var(--border); }
.tomorrow-health__details dt { color: var(--text-3); font-size: 12px; }
.tomorrow-health__details dd { margin: 0; color: var(--text-2); font-size: 12px; overflow-wrap: anywhere; }
.tomorrow-health__warning { margin: 10px 12px 12px; padding: 8px 10px; border: 1px solid var(--warning); border-radius: var(--radius-sm); color: var(--warning); font-size: 13px; }
@media (max-width: 520px) {
  .tomorrow-health__summary { grid-template-columns: minmax(0, 1fr); }
  .tomorrow-health__summary div { border-right: 0; border-bottom: 1px solid var(--border); }
  .tomorrow-health__summary div:last-child { border-bottom: 0; }
}
</style>
