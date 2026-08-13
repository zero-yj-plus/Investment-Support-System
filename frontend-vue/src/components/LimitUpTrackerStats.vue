<script setup lang="ts">
import { computed } from 'vue'
import type { LimitUpTrackerSnapshot } from '../types/limitupTracker'

const props = defineProps<{ snapshot: LimitUpTrackerSnapshot }>()

const cards = computed(() => [
  { key: 't1_average', title: '次日均涨', metric: props.snapshot.metrics.t1_average, date: props.snapshot.t1_date },
  { key: 't1_red_rate', title: '次日红盘', metric: props.snapshot.metrics.t1_red_rate, date: props.snapshot.t1_date },
  { key: 't1_promotion_rate', title: '次日晋级', metric: props.snapshot.metrics.t1_promotion_rate, date: props.snapshot.t1_date },
  { key: 't2_average', title: '第三日均涨', metric: props.snapshot.metrics.t2_average, date: props.snapshot.t2_date },
  { key: 't2_red_rate', title: '第三日红盘', metric: props.snapshot.metrics.t2_red_rate, date: props.snapshot.t2_date },
])

function percent(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
}
</script>

<template>
  <section class="tracker-metrics" aria-labelledby="tracker-metrics-title">
    <h2 id="tracker-metrics-title" class="sr-only">五项追踪统计</h2>
    <article v-for="card in cards" :key="card.key" class="tracker-metric panel" :class="`is-${card.metric.state}`">
      <header><span>{{ card.title }}</span><time v-if="card.date" class="mono" :datetime="card.date">{{ card.date }}</time></header>
      <strong
        class="mono"
        :class="card.metric.value == null ? '' : card.metric.value > 0 ? 'is-up' : card.metric.value < 0 ? 'is-down' : 'is-flat'"
      >{{ card.metric.value == null ? card.metric.label : percent(card.metric.value) }}</strong>
      <footer>
        <span v-if="card.metric.value != null" class="tracker-metric__state">{{ card.metric.label }}</span>
        <span v-if="card.metric.total_size" class="mono">{{ card.metric.sample_size }}/{{ card.metric.total_size }} 只</span>
      </footer>
    </article>
  </section>
</template>

<style scoped>
.tracker-metrics { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
.tracker-metric { min-height: 116px; padding: 10px 12px; }
.tracker-metric header, .tracker-metric footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tracker-metric header { color: var(--text-2); font-size: 13px; font-weight: 700; }
.tracker-metric time { color: var(--text-3); font-size: 11px; font-weight: 500; }
.tracker-metric > strong { display: block; margin: 12px 0 10px; font-size: clamp(18px, 1.7vw, 24px); line-height: 1.2; }
.tracker-metric footer { color: var(--text-3); font-size: 11px; }
.tracker-metric__state { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tracker-metric.is-intraday { border-color: var(--warning); }
.tracker-metric.is-missing, .tracker-metric.is-unavailable { border-style: dashed; }
@media (max-width: 1080px) { .tracker-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 680px) { .tracker-metrics { grid-template-columns: 1fr 1fr; } }
</style>
