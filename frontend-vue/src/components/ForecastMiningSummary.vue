<script setup lang="ts">
import { computed } from 'vue'
import type { ForecastMiningResponse } from '../types/forecastMining'
import { formatCompactDate, formatCount } from '../utils/forecastFormat'

const props = defineProps<{
  data: ForecastMiningResponse | null
}>()

const metrics = computed(() => [
  { label: '报告期', value: formatCompactDate(props.data?.period), detail: '服务端自动选择的中报或年报期' },
  { label: '窗口截止', value: props.data?.deadline || '—', detail: '披露时点分层的规则边界' },
  { label: '预告总数', value: formatCount(props.data?.stats?.['总数']), detail: '公开源去重后的预告总量' },
  { label: '今日新增', value: formatCount(props.data?.stats?.['今日新增']), detail: '公告日等于服务端北京时间今日' },
  { label: '掘金池', value: formatCount(props.data?.stats?.['掘金池']), detail: '达到服务端正向规则且未落入陷阱池' },
  { label: '陷阱沉底', value: formatCount(props.data?.stats?.['陷阱池']), detail: '低基数或异常高同比，透明保留' },
])

</script>

<template>
  <section class="forecast-metrics" aria-label="榜单统计">
    <article v-for="metric in metrics" :key="metric.label" class="forecast-metric">
      <span>{{ metric.label }}</span>
      <strong class="mono">{{ metric.value }}</strong>
      <small>{{ metric.detail }}</small>
    </article>
  </section>
</template>

<style scoped>
.forecast-metrics { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
.forecast-metric { min-width: 0; min-height: 104px; padding: 10px 11px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.forecast-metric > span { color: var(--text-3); font-size: 12px; }
.forecast-metric strong { display: block; margin-top: 3px; color: var(--text); font-size: 20px; overflow-wrap: anywhere; }
.forecast-metric small { display: block; margin-top: 4px; color: var(--text-3); font-size: 12px; line-height: 1.35; }
@media (max-width: 1100px) {
  .forecast-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
  .forecast-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .forecast-metric { min-height: 112px; }
}
</style>
