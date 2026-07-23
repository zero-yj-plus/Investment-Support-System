<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MainlineResponse, MarketOverviewResponse, SentimentTodayResponse } from '../types/dashboard'

const props = defineProps<{
  market: MarketOverviewResponse | null
  mainlines: MainlineResponse | null
  sentiment: SentimentTodayResponse | null
  marketLoading: boolean
  mainlineLoading: boolean
  sentimentLoading: boolean
  marketError?: string
  mainlineError?: string
}>()

const expanded = ref(false)
const marketAvailable = computed(() => props.market != null && props.market.ok !== false)
const downPool = computed(() => props.mainlines?.ok === false ? [] : props.mainlines?.limit_down_pool || [])
const visibleDownPool = computed(() => expanded.value ? downPool.value : downPool.value.slice(0, 5))
const downPoolMissing = computed(() => props.mainlines?.limit_down_missing?.filter(Boolean) || [])

function finite(value?: number | null): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function number(value?: number | null, digits = 0) {
  return finite(value) ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(value) : '—'
}

function percent(value?: number | null, digits = 1) {
  return finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}%` : '—'
}

function amount(value?: number | null) {
  if (!finite(value)) return '—'
  if (Math.abs(value) >= 1e12) return `${(value / 1e12).toFixed(2)} 万亿`
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(1)} 亿`
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(1)} 万`
  return number(value)
}

function marketTurnover(value?: number | null) {
  if (!finite(value)) return '—'
  // 当前 market overview 返回的两市成交额实际口径为“万元”。
  return `${(value / 1e8).toFixed(2)} 万亿`
}

function tone(value?: number | null) {
  if (!finite(value)) return 'is-flat'
  return value >= 0 ? 'is-up' : 'is-down'
}

const stats = computed(() => {
  const market = props.market
  const premium = props.sentiment?.ok === false ? null : props.sentiment?.metrics?.yesterday_limit_premium
  const ratio = props.sentiment?.ok === false ? null : props.sentiment?.metrics?.up_down_ratio
  return [
    { label: '正式涨停', value: number(market?.limit_up), tone: 'is-up' },
    { label: '炸板数', value: number(market?.zhaban_count), tone: 'is-warning', staleDate: market?.zhaban_stale_date },
    { label: '跌停', value: number(market?.limit_down), tone: 'is-down' },
    { label: '炸板率', value: finite(market?.zhaban_rate) ? `${number(market?.zhaban_rate, 1)}%` : '—', tone: 'is-warning', staleDate: market?.zhaban_rate_stale_date },
    { label: '晋级率', value: finite(market?.promote_rate) ? `${number(market?.promote_rate, 1)}%` : '—', tone: 'is-flat', staleDate: market?.promote_stale_date },
    { label: '最高连板', value: finite(market?.lianban_height) ? `${number(market?.lianban_height)} 板` : '—', tone: 'is-up', staleDate: market?.lianban_stale_date },
    { label: '封板成交', value: amount(market?.limit_up_amount), tone: 'is-flat', staleDate: market?.struct_stale_date },
    { label: '昨涨停溢价', value: percent(premium), tone: tone(premium), loading: props.sentimentLoading && !props.sentiment },
    { label: '涨跌比', value: finite(ratio) ? number(ratio, 2) : '—', tone: 'is-flat', loading: props.sentimentLoading && !props.sentiment },
  ]
})
</script>

<template>
  <div class="evidence-pair">
    <section class="panel evidence-card limit-stats-panel" aria-labelledby="limit-stats-title">
      <header class="section-heading evidence-heading">
        <div>
          <p class="eyebrow">LIMIT STRUCTURE</p>
          <h3 id="limit-stats-title">涨跌停情况</h3>
        </div>
        <span class="evidence-stamp">两市成交 {{ marketTurnover(market?.total_amount) }}</span>
      </header>

      <div v-if="marketLoading && !market" class="evidence-metric-grid" aria-label="涨跌停数据加载中">
        <span v-for="item in 9" :key="item" class="evidence-metric skeleton-card"></span>
      </div>
      <div v-else-if="marketAvailable" class="evidence-metric-grid" role="list">
        <div v-for="item in stats" :key="item.label" class="evidence-metric" role="listitem">
          <strong :class="[item.tone, { 'skeleton-text': item.loading }]">{{ item.loading ? '' : item.value }}</strong>
          <span>{{ item.label }}</span>
          <small v-if="item.staleDate">最近 {{ String(item.staleDate).slice(5) }}</small>
        </div>
      </div>
      <div v-else class="empty-state compact-empty" role="status">
        <strong>涨跌停统计暂不可用</strong>
        <span>{{ marketError || market?.missing?.join('；') || '市场概览未返回可验证的涨跌停结构。' }}</span>
      </div>
    </section>

    <section class="panel evidence-card down-pool-panel" aria-labelledby="down-pool-title">
      <header class="section-heading evidence-heading">
        <div>
          <p class="eyebrow">LIMIT-DOWN WATCH</p>
          <h3 id="down-pool-title">跌停风险池</h3>
        </div>
        <span class="evidence-stamp">{{ mainlines?.data_date || mainlines?.ts || '时点待确认' }}</span>
      </header>

      <div v-if="mainlineLoading && !mainlines" class="risk-pool-list" aria-label="跌停风险池加载中">
        <span v-for="item in 5" :key="item" class="skeleton-line"></span>
      </div>
      <template v-else-if="downPool.length">
        <p v-if="downPoolMissing.length" class="evidence-note is-warning" role="status">{{ downPoolMissing.join('；') }}</p>
        <div id="limit-down-details" class="risk-pool-list" role="list">
          <article v-for="item in visibleDownPool" :key="item.code || item.name" class="risk-pool-row" role="listitem">
            <div>
              <strong>{{ item.name || item.code || '未知标的' }}</strong>
              <span v-if="item.code" class="mono">{{ item.code }}</span>
            </div>
            <b :class="tone(item.change_pct)">{{ percent(item.change_pct, 2) }}</b>
            <p>
              <span>{{ item.industry || '行业未标注' }}</span>
              <span>{{ item.first_time ? `首封 ${item.first_time}` : '首封时间 —' }}{{ item.open_times == null ? '' : ` · 开板 ${number(item.open_times)} 次` }}</span>
            </p>
          </article>
        </div>
        <button
          v-if="downPool.length > 5"
          class="disclosure-button evidence-disclosure"
          type="button"
          :aria-expanded="expanded"
          aria-controls="limit-down-details"
          @click="expanded = !expanded"
        >{{ expanded ? '收起跌停明细' : `展开全部 ${number(downPool.length)} 只` }}</button>
      </template>
      <div v-else class="empty-state compact-empty" role="status">
        <strong>{{ downPoolMissing.length || mainlineError || mainlines?.ok === false ? '跌停明细暂不可用' : '今日暂无跌停风险' }}</strong>
        <span>{{ mainlineError || downPoolMissing.join('；') || '当前数据口径未发现跌停个股。' }}</span>
      </div>
    </section>
  </div>
</template>
