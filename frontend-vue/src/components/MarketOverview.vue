<script setup lang="ts">
import { computed } from 'vue'
import type { MarketOverviewResponse } from '../types/dashboard'

const props = defineProps<{
  market: MarketOverviewResponse | null
  loading: boolean
  error?: string
}>()

const marketAvailable = computed(() => props.market != null && props.market.ok !== false)
const indexes = computed(() => marketAvailable.value ? (props.market?.indexes || []).slice(0, 6) : [])
const totalBreadth = computed(() => {
  const counts = [props.market?.up_count, props.market?.down_count, props.market?.flat_count]
  if (!counts.every((value) => typeof value === 'number' && Number.isFinite(value))) return null
  return (counts as number[]).reduce((sum, value) => sum + value, 0)
})
const upWidth = computed(() => totalBreadth.value && props.market?.up_count != null ? (props.market.up_count / totalBreadth.value) * 100 : null)
const downWidth = computed(() => totalBreadth.value && props.market?.down_count != null ? (props.market.down_count / totalBreadth.value) * 100 : null)

function formatNumber(value?: number | null, digits = 0) {
  return value == null ? '—' : new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(value)
}

function formatAmount(value?: number | null) {
  if (value == null) return '—'
  // 当前 market overview 返回的两市成交额实际口径为“万元”。
  return `${(value / 1e8).toFixed(2)} 万亿`
}

function changeLabel(value?: number | null) {
  if (value == null) return '涨跌幅暂缺'
  return `${value >= 0 ? '上涨' : '下跌'} ${Math.abs(value).toFixed(2)}%`
}
</script>

<template>
  <section class="panel market-panel" aria-labelledby="market-title">
    <header class="section-heading">
      <div>
        <p class="eyebrow">MARKET PULSE</p>
        <h2 id="market-title">市场环境</h2>
      </div>
      <div class="section-heading__meta">
        <span>成交额 {{ formatAmount(market?.total_amount) }}</span>
        <span>{{ market?.data_date || market?.ts || '时点待确认' }}</span>
      </div>
    </header>

    <div v-if="loading && !market" class="metric-grid" aria-label="市场数据加载中">
      <div v-for="item in 6" :key="item" class="metric-card skeleton-card"></div>
    </div>
    <template v-else-if="marketAvailable && market">
      <div class="metric-grid" role="list" aria-label="核心指数">
        <article v-for="item in indexes" :key="item.code || item.name" class="metric-card" role="listitem">
          <span>{{ item.name || item.code || '指数' }}</span>
          <strong :class="item.change_pct == null ? 'is-flat' : item.change_pct >= 0 ? 'is-up' : 'is-down'">{{ formatNumber(item.price, 2) }}</strong>
          <em :class="item.change_pct == null ? 'is-flat' : item.change_pct >= 0 ? 'is-up' : 'is-down'">
            {{ item.change_pct == null ? '—' : `${item.change_pct >= 0 ? '+' : ''}${item.change_pct.toFixed(2)}%` }}
            <span class="sr-only">{{ changeLabel(item.change_pct) }}</span>
          </em>
        </article>
      </div>

      <div class="breadth" aria-labelledby="breadth-label">
        <div class="breadth__labels" id="breadth-label">
          <span><b class="is-up">{{ formatNumber(market.up_count) }}</b> 家上涨</span>
          <span><b>{{ formatNumber(market.flat_count) }}</b> 家平盘</span>
          <span><b class="is-down">{{ formatNumber(market.down_count) }}</b> 家下跌</span>
        </div>
        <div v-if="upWidth != null && downWidth != null" class="breadth__track" role="img" :aria-label="`上涨 ${formatNumber(market.up_count)} 家，平盘 ${formatNumber(market.flat_count)} 家，下跌 ${formatNumber(market.down_count)} 家`">
          <span class="breadth__up" :style="{ width: `${upWidth}%` }"></span>
          <span class="breadth__flat" :style="{ width: `${Math.max(0, 100 - upWidth - downWidth)}%` }"></span>
          <span class="breadth__down" :style="{ width: `${downWidth}%` }"></span>
        </div>
        <p v-else class="breadth__missing">市场宽度比例暂不可用</p>
        <div class="breadth__facts">
          <span>涨停 <b>{{ formatNumber(market.limit_up) }}</b></span>
          <span>跌停 <b>{{ formatNumber(market.limit_down) }}</b></span>
          <span>上涨宽度 <b>{{ upWidth == null ? '—' : `${upWidth.toFixed(0)}%` }}</b></span>
        </div>
      </div>
    </template>
    <div v-else class="empty-state" role="status">
      <strong>市场数据暂不可用</strong>
      <span>{{ error || market?.error || market?.missing?.join('；') || '尚未取得指数和涨跌家数，当前不生成市场方向判断。' }}</span>
    </div>
  </section>
</template>
