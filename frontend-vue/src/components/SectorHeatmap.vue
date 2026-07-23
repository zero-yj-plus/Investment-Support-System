<script setup lang="ts">
import { computed } from 'vue'
import type { MarketSector, MarketSectorsResponse } from '../types/dashboard'

const props = defineProps<{
  data: MarketSectorsResponse | null
  loading: boolean
  error?: string
}>()

interface HeatItem {
  row: MarketSector
  weight: number
  weightKind: 'amount' | 'change'
}

interface HeatRect extends HeatItem {
  x: number
  y: number
  width: number
  height: number
}

function finite(value?: number | null): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

const industries = computed<HeatItem[]>(() => (props.data?.sectors || [])
  .filter((row) => row.kind === 'industry' && (finite(row.amount) && row.amount > 0 || finite(row.change_pct)))
  .map((row) => {
    if (finite(row.amount) && row.amount > 0) return { row, weight: row.amount, weightKind: 'amount' as const }
    // 与源版 treemapOption 保持一致：成交额缺失时只用真实涨跌幅生成展示权重。
    // 该权重仅用于矩形布局，不能作为金额展示或进入任何决策计算。
    return { row, weight: Math.max(1, Math.abs(row.change_pct as number) * 1e9), weightKind: 'change' as const }
  })
  .sort((a, b) => b.weight - a.weight)
  .slice(0, 40))

const fallbackCount = computed(() => industries.value.filter((item) => item.weightKind === 'change').length)
const usesChangeFallback = computed(() => fallbackCount.value > 0)

function binaryLayout(
  items: HeatItem[],
  x: number,
  y: number,
  width: number,
  height: number,
  output: HeatRect[],
) {
  if (!items.length || width <= 0 || height <= 0) return
  if (items.length === 1) {
    output.push({ ...items[0], x, y, width, height })
    return
  }
  const total = items.reduce((sum, item) => sum + item.weight, 0)
  if (!total) return
  let split = 1
  let leftTotal = items[0].weight
  let bestDistance = Math.abs(total / 2 - leftTotal)
  for (let index = 1; index < items.length - 1; index += 1) {
    leftTotal += items[index].weight
    const distance = Math.abs(total / 2 - leftTotal)
    if (distance <= bestDistance) {
      bestDistance = distance
      split = index + 1
    } else break
  }
  const first = items.slice(0, split)
  const second = items.slice(split)
  const firstTotal = first.reduce((sum, item) => sum + item.weight, 0)
  const ratio = firstTotal / total
  if (width >= height) {
    const firstWidth = width * ratio
    binaryLayout(first, x, y, firstWidth, height, output)
    binaryLayout(second, x + firstWidth, y, width - firstWidth, height, output)
  } else {
    const firstHeight = height * ratio
    binaryLayout(first, x, y, width, firstHeight, output)
    binaryLayout(second, x, y + firstHeight, width, height - firstHeight, output)
  }
}

const layout = computed(() => {
  const output: HeatRect[] = []
  binaryLayout(industries.value, 0, 0, 1000, 280, output)
  return output
})

function tone(value?: number | null) {
  if (!finite(value)) return 'is-flat'
  return value >= 0 ? 'is-up' : 'is-down'
}

function heatTone(value?: number | null) {
  if (!finite(value)) return 'heat-flat'
  return value >= 0 ? 'heat-up' : 'heat-down'
}

function percent(value?: number | null) {
  return finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '涨跌幅暂缺'
}

function amount(value?: number | null) {
  if (!finite(value)) return '—'
  if (Math.abs(value) >= 1e12) return `${(value / 1e12).toFixed(2)} 万亿`
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(1)} 亿`
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(value)
}

function layoutMetric(item: HeatItem) {
  return item.weightKind === 'amount' ? amount(item.row.amount) : `|涨跌幅| ${Math.abs(item.row.change_pct as number).toFixed(2)}%`
}

function isLargeTile(item: HeatRect) {
  // 窄高型矩形也可能是成交额大板块，不能仅按宽度判断层级。
  return item.width >= 88 && item.height >= 56 && item.width * item.height >= 10_000
}

function showsTileName(item: HeatRect) {
  return item.width >= 42 && item.height >= 22
}

function tileName(item: HeatRect) {
  const name = item.row.name || '未命名行业'
  if (isLargeTile(item)) return name
  const maxChars = Math.max(2, Math.floor((item.width - 12) / 12))
  return name.length > maxChars ? `${name.slice(0, Math.max(1, maxChars - 1))}…` : name
}

function tileNameY(item: HeatRect) {
  return item.y + item.height / 2 - (isLargeTile(item) && finite(item.row.change_pct) ? 12 : 0)
}
</script>

<template>
  <section class="panel evidence-card heatmap-panel" aria-labelledby="sector-heat-title">
    <header class="section-heading evidence-heading">
      <div>
        <p class="eyebrow">SECTOR HEATMAP</p>
        <h3 id="sector-heat-title">题材热力图</h3>
      </div>
      <div class="section-heading__meta">
        <span>面积 = 成交额</span>
        <span v-if="usesChangeFallback">降级展示：{{ fallbackCount }} 项成交额缺失，面积改用 |涨跌幅|</span>
        <span>文字 = 涨跌幅</span>
      </div>
    </header>

    <div v-if="loading && !data" class="heatmap-loading" role="status" aria-label="题材热力加载中">
      <span class="skeleton-block"></span>
    </div>
    <div v-else-if="layout.length" class="heatmap-layout">
      <svg class="sector-treemap" viewBox="0 0 1000 280" role="img" aria-labelledby="sector-heat-title sector-heat-desc">
        <desc id="sector-heat-desc">矩形面积优先表示行业成交额；成交额缺失项按涨跌幅绝对值形成展示权重。红色背景表示上涨，绿色背景表示下跌，白色文字显示行业与涨跌幅；右侧列表提供精确数值。</desc>
        <defs>
          <linearGradient id="heat-up-gradient" class="heat-gradient heat-gradient--up" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop class="heat-stop heat-stop--base" offset="0%" />
            <stop class="heat-stop heat-stop--light" offset="100%" />
          </linearGradient>
          <linearGradient id="heat-down-gradient" class="heat-gradient heat-gradient--down" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop class="heat-stop heat-stop--base" offset="0%" />
            <stop class="heat-stop heat-stop--light" offset="100%" />
          </linearGradient>
        </defs>
        <g v-for="item in layout" :key="item.row.name">
          <rect
            :x="item.x + 1"
            :y="item.y + 1"
            :width="Math.max(0, item.width - 2)"
            :height="Math.max(0, item.height - 2)"
            rx="3"
            :class="heatTone(item.row.change_pct)"
          ><title>{{ item.row.name }} · {{ layoutMetric(item) }} · {{ percent(item.row.change_pct) }}</title></rect>
          <template v-if="showsTileName(item)">
            <text
              :x="item.x + item.width / 2"
              :y="tileNameY(item)"
              :class="['heat-name', isLargeTile(item) ? 'heat-name--large' : 'heat-name--compact']"
              dominant-baseline="middle"
              text-anchor="middle"
            >{{ tileName(item) }}</text>
            <text
              v-if="isLargeTile(item) && finite(item.row.change_pct)"
              :x="item.x + item.width / 2"
              :y="item.y + item.height / 2 + 15"
              class="heat-value heat-value--large"
              dominant-baseline="middle"
              text-anchor="middle"
            >{{ percent(item.row.change_pct) }}</text>
          </template>
        </g>
      </svg>

      <div class="heat-ranking" :aria-label="usesChangeFallback ? '行业布局权重精确值' : '行业成交额精确值'">
        <div class="heat-ranking__head"><span>{{ usesChangeFallback ? '布局权重 TOP' : '成交额 TOP' }}</span><span>{{ data?.data_date || data?.ts || '时点待确认' }}</span></div>
        <ol>
          <li v-for="item in industries.slice(0, 8)" :key="item.row.name">
            <span><strong>{{ item.row.name || '未命名行业' }}</strong><small>{{ layoutMetric(item) }}</small></span>
            <b :class="tone(item.row.change_pct)" class="mono">{{ percent(item.row.change_pct) }}</b>
          </li>
        </ol>
      </div>
    </div>
    <div v-else class="empty-state compact-empty" role="status">
      <strong>题材热力暂不可用</strong>
      <span>{{ error || data?.missing_fields?.join('；') || data?.missing?.join('；') || '行业成交额及涨跌幅均缺失，无法生成面积矩阵。' }}</span>
    </div>
  </section>
</template>
