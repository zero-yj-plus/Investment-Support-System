<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { CapitalFlowRow } from '../types/capitalTracking'
import { capitalDirection, finiteNumber, formatCapitalPoint, isRawCapital } from '../utils/capitalFormat'

const props = defineProps<{ sectors: CapitalFlowRow[]; hotStocks: CapitalFlowRow[] }>()
const mode = ref<'sector' | 'stock'>('sector')
const selectedKey = ref('')

const rows = computed(() => mode.value === 'sector' ? props.sectors : props.hotStocks)
function rowKey(row: CapitalFlowRow): string { return mode.value === 'sector' ? String(row.name || '') : String(row.code || '') }
function rowLabel(row: CapitalFlowRow): string { return mode.value === 'sector' ? String(row.name || '名称缺失') : `${row.name || '名称缺失'} ${row.code || ''}`.trim() }

watch([rows, mode], () => {
  if (!rows.value.some((row) => rowKey(row) === selectedKey.value)) selectedKey.value = rows.value.length ? rowKey(rows.value[0]) : ''
}, { immediate: true })

const selected = computed(() => rows.value.find((row) => rowKey(row) === selectedKey.value) || null)
const points = computed(() => (Array.isArray(selected.value?.series) ? selected.value.series : [])
  .map((point) => ({ time: String(point?.t || ''), value: finiteNumber(point?.v) }))
  .filter((point): point is { time: string; value: number } => Boolean(point.time) && point.value != null))
const visiblePoints = computed(() => points.value.slice(-12))
const rawUnit = computed(() => selected.value ? isRawCapital(selected.value) : false)

const plot = computed(() => {
  const values = visiblePoints.value.map((point) => point.value)
  if (!values.length) return { polyline: '', baseline: 90, dots: [] as { x: number; y: number }[] }
  let min = Math.min(0, ...values)
  let max = Math.max(0, ...values)
  if (min === max) { min -= 1; max += 1 }
  const top = 16
  const bottom = 164
  const left = 24
  const right = 616
  const y = (value: number) => bottom - ((value - min) / (max - min)) * (bottom - top)
  const dots = values.map((value, index) => ({
    x: values.length === 1 ? (left + right) / 2 : left + (index / (values.length - 1)) * (right - left),
    y: y(value),
  }))
  return { polyline: dots.map((point) => `${point.x},${point.y}`).join(' '), baseline: y(0), dots }
})

const direction = computed(() => capitalDirection(selected.value?.main_net))
const directionLabel = computed(() => direction.value === 'in' ? '最新为净流入' : direction.value === 'out' ? '最新为净流出' : direction.value === 'flat' ? '最新持平' : '最新方向缺失')
const lineClass = computed(() => direction.value === 'in' ? 'is-up' : direction.value === 'out' ? 'is-down' : 'is-neutral')
</script>

<template>
  <section class="panel capital-trend" aria-labelledby="capital-trend-title">
    <header class="capital-trend__heading">
      <div><p class="eyebrow">历史采样 · 同口径连续片段</p><h2 id="capital-trend-title">日内资金趋势</h2></div>
      <div class="capital-mode" aria-label="趋势类型">
        <button type="button" :class="{ 'is-active': mode === 'sector' }" :aria-pressed="mode === 'sector'" @click="mode = 'sector'">行业</button>
        <button type="button" :class="{ 'is-active': mode === 'stock' }" :aria-pressed="mode === 'stock'" @click="mode = 'stock'">热门股</button>
      </div>
    </header>
    <div v-if="!rows.length" class="capital-trend__empty" role="status">
      <strong>当前口径暂无可选趋势</strong>
      <span>等待真实快照累积后显示；不会生成示例曲线。</span>
    </div>
    <template v-else>
      <div class="capital-trend__controls">
        <label for="capital-trend-select">观察对象</label>
        <select id="capital-trend-select" v-model="selectedKey">
          <option v-for="row in rows" :key="rowKey(row)" :value="rowKey(row)">{{ rowLabel(row) }}</option>
        </select>
        <span class="capital-trend__direction" :class="lineClass">{{ directionLabel }}</span>
        <span v-if="rawUnit" class="capital-trend__warning">单位尚未核验，仅比较方向与排名</span>
      </div>
      <div v-if="!visiblePoints.length" class="capital-trend__empty" role="status">
        <strong>该对象暂无有效采样点</strong><span>空值不会按 0 画入趋势。</span>
      </div>
      <div v-else class="capital-trend__body">
        <figure class="capital-plot">
          <svg viewBox="0 0 640 180" role="img" :aria-label="`${rowLabel(selected!)}日内资金趋势，共 ${visiblePoints.length} 个有效采样点，${directionLabel}`">
            <line x1="24" :y1="plot.baseline" x2="616" :y2="plot.baseline" class="capital-plot__zero" />
            <polyline v-if="plot.polyline" :points="plot.polyline" class="capital-plot__line" :class="lineClass" />
            <circle v-for="(dot, index) in plot.dots" :key="index" :cx="dot.x" :cy="dot.y" r="4" class="capital-plot__dot" :class="lineClass" />
          </svg>
          <figcaption>最近 {{ visiblePoints.length }} 个有效采样点；源切换导致单位变化时，服务端只返回最新同口径连续片段。</figcaption>
        </figure>
        <div class="capital-point-table" tabindex="0" aria-label="日内资金采样明细，可横向滚动">
          <table><thead><tr><th scope="col">采样时点</th><th scope="col">资金值</th></tr></thead><tbody><tr v-for="point in visiblePoints" :key="`${point.time}-${point.value}`"><td class="mono">{{ point.time }}</td><td class="mono" :class="point.value > 0 ? 'is-up' : point.value < 0 ? 'is-down' : ''">{{ formatCapitalPoint(point.value, rawUnit) }}</td></tr></tbody></table>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.capital-trend { min-width: 0; overflow: hidden; }
.capital-trend__heading { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.capital-trend__heading h2 { margin: 0; font-size: 18px; }
.capital-mode { display: inline-flex; gap: 8px; }
.capital-mode button { min-width: 76px; min-height: 44px; padding: 8px 12px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text-2); font: inherit; font-weight: 720; cursor: pointer; }
.capital-mode button.is-active { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); }
.capital-trend__controls { display: flex; min-width: 0; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 10px 16px; border-bottom: 1px solid var(--border); background: var(--surface-2); }
.capital-trend__controls label { color: var(--text-3); font-size: 12px; font-weight: 750; }
.capital-trend__controls select { min-width: min(280px, 100%); min-height: 44px; padding: 8px 36px 8px 10px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--surface); color: var(--text); font: inherit; }
.capital-trend__direction, .capital-trend__warning { font-size: 13px; font-weight: 750; }
.capital-trend__warning { color: var(--warning); }
.capital-trend__body { display: grid; min-width: 0; grid-template-columns: minmax(0, 1fr) minmax(220px, 300px); }
.capital-plot { min-width: 0; margin: 0; padding: 14px 16px; border-right: 1px solid var(--border); }
.capital-plot svg { display: block; width: 100%; min-height: 180px; }
.capital-plot__zero { stroke: var(--border-strong); stroke-width: 1; stroke-dasharray: 5 5; }
.capital-plot__line { fill: none; stroke: var(--info); stroke-width: 3; vector-effect: non-scaling-stroke; }
.capital-plot__dot { fill: var(--surface); stroke: var(--info); stroke-width: 3; vector-effect: non-scaling-stroke; }
.capital-plot__line.is-up, .capital-plot__dot.is-up { stroke: var(--up); }
.capital-plot__line.is-down, .capital-plot__dot.is-down { stroke: var(--down); }
.capital-plot figcaption { color: var(--text-3); font-size: 12px; line-height: 1.5; }
.capital-point-table { max-width: 100%; overflow-x: auto; }
.capital-point-table table { width: 100%; min-width: 220px; border-collapse: collapse; font-size: 13px; }
.capital-point-table th, .capital-point-table td { padding: 8px 12px; border-bottom: 1px solid var(--border); text-align: right; }
.capital-point-table th { color: var(--text-3); font-size: 12px; }
.capital-point-table th:first-child, .capital-point-table td:first-child { text-align: left; }
.capital-trend__empty { display: flex; min-height: 180px; flex-direction: column; align-items: center; justify-content: center; gap: 4px; padding: 24px; text-align: center; }
.capital-trend__empty span { color: var(--text-3); }
.is-up { color: var(--up); }
.is-down { color: var(--down); }
.is-neutral { color: var(--text-2); }
@media (max-width: 900px) {
  .capital-trend__body { grid-template-columns: minmax(0, 1fr); }
  .capital-plot { border-right: 0; border-bottom: 1px solid var(--border); }
}
@media (max-width: 720px) {
  .capital-trend__heading { align-items: stretch; flex-direction: column; }
  .capital-mode, .capital-mode button { width: 100%; }
  .capital-trend__controls { align-items: stretch; flex-direction: column; }
  .capital-trend__controls select { width: 100%; min-width: 0; }
  .capital-plot { padding: 10px; }
}
</style>
