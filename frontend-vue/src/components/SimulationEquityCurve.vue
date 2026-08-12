<script setup lang="ts">
import { computed } from 'vue'
import type { SimulationEquityPoint } from '../types/simulation'

const props = defineProps<{ points: SimulationEquityPoint[] }>()
const chartWidth = 800
const chartHeight = 220
const padX = 30
const padY = 22

const valid = computed(() => props.points.filter((point) => Number.isFinite(point.total_assets)))
const plotted = computed(() => {
  if (!valid.value.length) return []
  const values = valid.value.map((point) => Number(point.total_assets))
  const min = Math.min(...values)
  const max = Math.max(...values)
  const spread = max - min || Math.max(Math.abs(max) * 0.01, 1)
  return valid.value.map((point, index) => ({
    ...point,
    x: padX + (valid.value.length === 1 ? 0.5 : index / (valid.value.length - 1)) * (chartWidth - padX * 2),
    y: chartHeight - padY - ((Number(point.total_assets) - min) / spread) * (chartHeight - padY * 2),
  }))
})
const polyline = computed(() => plotted.value.map((point) => `${point.x},${point.y}`).join(' '))
const missingCount = computed(() => props.points.length - valid.value.length)

function money(value: number | null | undefined): string {
  return Number.isFinite(value) ? `${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元` : '估值缺失'
}

function time(value: string): string {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="equity-chart">
    <div v-if="!plotted.length" class="equity-chart__empty">尚无可绘制的完整资产点。</div>
    <svg v-else viewBox="0 0 800 220" role="img" aria-labelledby="equity-chart-title equity-chart-desc">
      <title id="equity-chart-title">模拟账户总资产曲线</title>
      <desc id="equity-chart-desc">共 {{ plotted.length }} 个完整估值点；{{ missingCount }} 个缺少持仓行情的点未连线。</desc>
      <line x1="30" y1="198" x2="770" y2="198" class="axis" />
      <line x1="30" y1="22" x2="30" y2="198" class="axis" />
      <polyline v-if="plotted.length > 1" :points="polyline" class="equity-line" />
      <circle v-for="point in plotted" :key="point.snapshot_id" :cx="point.x" :cy="point.y" r="4" class="equity-point">
        <title>{{ time(point.created_at) }}：{{ money(point.total_assets) }}，累计盈亏 {{ money(point.total_pnl) }}</title>
      </circle>
    </svg>
    <p v-if="missingCount" class="equity-chart__warning">{{ missingCount }} 个快照因持仓行情缺失未连入曲线，原始点仍保留在账本。</p>
    <ol class="sr-only">
      <li v-for="point in points" :key="point.snapshot_id">{{ time(point.created_at) }}，总资产 {{ money(point.total_assets) }}，累计盈亏 {{ money(point.total_pnl) }}</li>
    </ol>
  </div>
</template>

<style scoped>
.equity-chart { min-width: 0; }
.equity-chart svg { display: block; width: 100%; height: auto; min-height: 180px; }
.axis { stroke: var(--border-strong); stroke-width: 1; }
.equity-line { fill: none; stroke: var(--accent); stroke-linecap: round; stroke-linejoin: round; stroke-width: 3; vector-effect: non-scaling-stroke; }
.equity-point { fill: var(--surface); stroke: var(--accent); stroke-width: 3; vector-effect: non-scaling-stroke; }
.equity-chart__empty { display: grid; min-height: 180px; place-content: center; color: var(--text-3); }
.equity-chart__warning { margin: 6px 0 0; color: var(--warning); font-size: 12px; }
</style>
