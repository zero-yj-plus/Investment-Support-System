<script setup lang="ts">
import { computed } from 'vue'
import type { TomorrowReportResponse, TomorrowWatchlistResponse } from '../types/tomorrow'

const props = defineProps<{
  report?: TomorrowReportResponse | null
  watchlist?: TomorrowWatchlistResponse | null
  reportError?: string
  watchlistError?: string
}>()
const sources = computed(() => props.report?.input_lineage?.sources || [])
const sourceRows = computed(() => ['tail', 'close'].map((rtype) => ({ rtype, source: sources.value.find((item) => item.rtype === rtype) })))
const sourceComplete = computed(() => sourceRows.value.every((row) => Boolean(row.source?.content_hash && row.source?.trade_day)))
const reportState = computed(() => {
  if (!props.report?.content_md) return { label: '不可用', tone: 'danger' }
  if (props.report.stale) return { label: '陈旧', tone: 'warning' }
  if (!sourceComplete.value || props.report.missing?.length) return { label: '部分覆盖', tone: 'warning' }
  return { label: '完整', tone: 'positive' }
})
const watchState = computed(() => {
  if (props.watchlistError) return { label: '局部失败', tone: 'danger' }
  if (!props.watchlist) return { label: '未读取', tone: 'neutral' }
  if (Array.isArray(props.watchlist.items) && props.watchlist.items.length === 0) return { label: '真实空数组', tone: 'neutral' }
  if (props.watchlist.missing?.length) return { label: '部分覆盖', tone: 'warning' }
  return { label: '可用', tone: 'positive' }
})
</script>

<template>
  <section class="panel tomorrow-health" aria-labelledby="tomorrow-health-title">
    <header class="tomorrow-card-header">
      <div><p class="eyebrow">DATA HEALTH</p><h3 id="tomorrow-health-title">依据完整性与治理</h3></div>
      <span class="tomorrow-health-badge" :class="`is-${reportState.tone}`">报告 {{ reportState.label }}</span>
    </header>
    <div class="tomorrow-health__summary">
      <div><span>上游定稿</span><strong>{{ sourceComplete ? '尾盘 + 收盘齐备' : '部分覆盖' }}</strong></div>
      <div><span>观察池</span><strong :class="`is-${watchState.tone}`">{{ watchState.label }}</strong></div>
      <div><span>数据日期</span><strong class="mono">{{ report?.date || '缺失' }}</strong></div>
    </div>
    <div class="tomorrow-source-list" aria-label="上游定稿来源">
      <article v-for="row in sourceRows" :key="row.rtype">
        <div><strong>{{ row.rtype === 'tail' ? '尾盘决策定稿' : '收盘复盘定稿' }}</strong><span class="mono">{{ row.source?.trade_day || '日期缺失' }}</span></div>
        <span class="tomorrow-health-badge" :class="row.source ? 'is-positive' : 'is-danger'">{{ row.source ? `v${row.source.version ?? '缺失'}` : '来源缺失' }}</span>
        <code>{{ row.source?.content_hash ? row.source.content_hash.slice(0, 12) : '指纹缺失' }}</code>
      </article>
    </div>
    <dl class="tomorrow-governance">
      <div><dt>报告版本</dt><dd class="mono">v{{ report?.plan_version ?? report?.report_version ?? '缺失' }}</dd></div>
      <div><dt>推送状态</dt><dd>{{ report?.pushed ? '已提交推送，未确认送达' : '尚未提交推送' }}</dd></div>
      <div><dt>内容指纹</dt><dd class="mono">{{ report?.content_hash?.slice(0, 12) || '缺失' }}</dd></div>
      <div><dt>Lineage 指纹</dt><dd class="mono">{{ report?.input_lineage?.lineage_hash?.slice(0, 12) || '缺失' }}</dd></div>
      <div><dt>声明校验</dt><dd>{{ report?.claim_validation?.status || '缺失' }}</dd></div>
      <div><dt>建议治理</dt><dd>{{ report?.advice_governance?.status || '缺失' }}</dd></div>
    </dl>
    <p v-if="reportError || watchlistError" class="tomorrow-health__warning" role="status">局部降级：{{ [reportError, watchlistError].filter(Boolean).join('；') }}</p>
  </section>
</template>

<style scoped>
.tomorrow-health { min-width: 0; overflow: hidden; }
.tomorrow-card-header { display: flex; min-height: 68px; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.tomorrow-card-header h3 { margin: 1px 0 0; font-size: 18px; }
.tomorrow-health-badge { display: inline-flex; min-height: 28px; align-items: center; padding: 3px 7px; border: 1px solid currentColor; border-radius: 999px; font-size: 12px; font-weight: 700; white-space: nowrap; }
.is-positive { color: var(--info); }.is-warning { color: var(--warning); }.is-danger { color: var(--danger); }.is-neutral { color: var(--text-3); }
.tomorrow-health__summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-bottom: 1px solid var(--border); }
.tomorrow-health__summary div { display: grid; gap: 2px; min-width: 0; padding: 10px 12px; border-right: 1px solid var(--border); }
.tomorrow-health__summary div:last-child { border-right: 0; }
.tomorrow-health__summary span, dt { color: var(--text-3); font-size: 12px; }
.tomorrow-source-list article { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px 10px; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.tomorrow-source-list article > div { display: grid; }.tomorrow-source-list article > div span { color: var(--text-3); font-size: 12px; }
.tomorrow-source-list code { grid-column: 1 / -1; color: var(--text-3); font-size: 12px; overflow-wrap: anywhere; }
.tomorrow-governance { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; }
.tomorrow-governance div { min-width: 0; padding: 8px 12px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.tomorrow-governance div:nth-child(2n) { border-right: 0; }
dd { margin: 2px 0 0; overflow-wrap: anywhere; }
.tomorrow-health__warning { margin: 0; padding: 10px 12px; color: var(--warning); font-size: 12px; }
@media (max-width: 720px) { .tomorrow-health__summary, .tomorrow-governance { grid-template-columns: minmax(0, 1fr); } .tomorrow-health__summary div, .tomorrow-governance div { border-right: 0; } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
