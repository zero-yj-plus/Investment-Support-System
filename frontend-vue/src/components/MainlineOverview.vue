<script setup lang="ts">
import { computed } from 'vue'
import type { MainlineResponse, MainlineRole } from '../types/dashboard'

const props = defineProps<{
  data: MainlineResponse | null
  loading: boolean
  error?: string
}>()

const dataAvailable = computed(() => props.data != null && props.data.ok !== false)
const roles = computed(() => dataAvailable.value ? (props.data?.roles || props.data?.sectors || props.data?.mainlines || []).slice(0, 5) : [])

function scoreOf(item: MainlineRole) {
  const raw = item.structure_score ?? item.score
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : null
}

function scoreWidth(item: MainlineRole) {
  const score = scoreOf(item)
  return score == null ? undefined : `${Math.max(0, Math.min(100, score))}%`
}

function ratio(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(0)}%` : '—'
}

function rank(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value) ? `前 ${ratio(value)}` : '—'
}

function count(value?: number | null, unit = '家') {
  return typeof value === 'number' && Number.isFinite(value) ? `${value} ${unit}` : '—'
}

function change(value?: number | null) {
  if (value == null) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

function isFormed(item: MainlineRole) {
  return item.status === 'formed'
}
</script>

<template>
  <section class="panel mainline-panel" aria-labelledby="mainline-title">
    <header class="section-heading">
      <div>
        <p class="eyebrow">MARKET STRUCTURE</p>
        <h2 id="mainline-title">今日行业主线</h2>
      </div>
      <div class="section-heading__meta">
        <span>{{ dataAvailable ? data?.market_state?.state || '环境待确认' : '环境待确认' }}</span>
        <span v-if="dataAvailable && data?.market_state?.up_ratio != null">上涨宽度 {{ ratio(data.market_state.up_ratio) }}</span>
      </div>
    </header>

    <div v-if="loading && !data" class="mainline-grid" aria-label="行业主线加载中">
      <div v-for="item in 4" :key="item" class="mainline-card skeleton-card"></div>
    </div>
    <div v-else-if="roles.length" class="mainline-grid" role="list">
      <article v-for="item in roles" :key="`${item.label}-${item.name}`" class="mainline-card" :class="{ 'is-formed': isFormed(item) }" role="listitem">
        <div class="mainline-card__top">
          <span class="role-label" :class="{ 'is-formed': isFormed(item) }">
            {{ item.label || item.status_label || '结构观察' }}
            <small v-if="isFormed(item)">已形成</small>
          </span>
          <span class="mainline-change" :class="item.change_pct == null ? 'is-flat' : item.change_pct >= 0 ? 'is-up' : 'is-down'">
            {{ change(item.change_pct) }}
          </span>
        </div>
        <h3>{{ item.name || '尚未形成' }}</h3>
        <div class="structure-score">
          <span>结构强度</span>
          <strong>{{ scoreOf(item) == null ? '—' : scoreOf(item)?.toFixed(0) }}</strong>
          <div v-if="scoreOf(item) != null" class="score-track" role="img" :aria-label="`结构强度 ${scoreOf(item)?.toFixed(0)}`">
            <i :style="{ width: scoreWidth(item) }"></i>
          </div>
          <span v-else class="score-missing">结构强度暂缺</span>
        </div>
        <dl>
          <div><dt>容量排名</dt><dd>{{ rank(item.amount_rank_pct) }}</dd></div>
          <div><dt>上涨宽度</dt><dd>{{ ratio(item.breadth_ratio) }}</dd></div>
          <div><dt>涨停</dt><dd>{{ count(item.limit_up_count) }}</dd></div>
          <div><dt>高度</dt><dd>{{ count(item.max_lianban, '板') }}</dd></div>
        </dl>
        <p class="mainline-summary" :class="{ 'is-ladder-summary': item.label === '情绪链' && isFormed(item) }">
          {{ item.role_reason || item.evidence || '结构证据待确认' }}
        </p>
      </article>
    </div>
    <div v-else class="empty-state" role="status">
      <strong>主线数据不足</strong>
      <span>{{ error || data?.error || data?.missing?.join('；') || '容量、相对强度、宽度或涨停梯队暂不可用。' }}</span>
    </div>
  </section>
</template>
