<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import type { CloseRecommendationItem, CloseReviewStatsResponse } from '../types/closeReview'
import type { RecommendationReview } from '../types/tail'

const props = defineProps<{
  stats: CloseReviewStatsResponse | null
  recommendations: CloseRecommendationItem[]
  statsError?: string
  recommendationError?: string
  verifying: boolean
}>()
const emit = defineEmits<{ verify: []; retry: [] }>()
const windows = ['T0', 'T1', 'T2', 'T5'] as const
const recent = computed(() => props.recommendations.slice(0, 8))

function formatNumber(value?: number | null, digits = 0): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return Number(value).toFixed(digits)
}

function formatPercent(value?: number | null, signed = false): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const number = Number(value)
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(1)}%`
}

function formatRate(value?: number | null): string {
  if (props.stats?.win_rate_unit !== 'percent') return '单位异常'
  return formatPercent(value)
}

function reviewFor(item: CloseRecommendationItem, windowName: typeof windows[number]): RecommendationReview | undefined {
  return item.review_windows?.[windowName]
    || item.reviews?.filter((review) => review.window === windowName).at(-1)
}

function latestReview(item: CloseRecommendationItem): RecommendationReview | undefined {
  if (item.review) return item.review
  const reviews = item.reviews || []
  if (reviews.length) return reviews[reviews.length - 1]
  for (const windowName of [...windows].reverse()) {
    const review = reviewFor(item, windowName)
    if (review) return review
  }
  return undefined
}

function reviewMeta(review?: RecommendationReview) {
  const state = review?.evaluation_state || ''
  const verdict = review?.verdict || ''
  if (!review || state === 'waiting') return { label: '待到期', tone: 'is-waiting' }
  if (state === 'untriggered' || verdict === 'not_triggered') return { label: '未触发', tone: 'is-neutral' }
  if (state === 'incomparable') return { label: '不可比', tone: 'is-warning' }
  if (verdict === 'insufficient_evidence') return { label: '证据不足', tone: 'is-warning' }
  if (verdict === 'hit' || review.hit_status === 'hit') return { label: '方向命中', tone: 'is-positive' }
  if (verdict === 'partial_hit') return { label: '部分命中', tone: 'is-neutral' }
  if (verdict === 'invalidated' || review.hit_status === 'miss') return { label: '方向失效', tone: 'is-negative' }
  return { label: '待核验', tone: 'is-warning' }
}

function rememberDiagnosis(code?: string) {
  if (code) sessionStorage.setItem('hermes.diagnosis.code', code)
}
</script>

<template>
  <section class="panel close-evidence-card" aria-labelledby="close-track-title">
    <header class="close-evidence-card__header">
      <div><p class="eyebrow">APPEND-ONLY TRACK RECORD</p><h2 id="close-track-title">推荐留痕回验</h2></div>
      <button type="button" class="primary-button compact-button" :disabled="verifying" @click="emit('verify')">
        <AppIcon name="refresh" :size="16" :class="{ spinning: verifying }" />{{ verifying ? '回验中' : '执行回验' }}
      </button>
    </header>

    <div v-if="statsError" class="close-local-state is-error" role="alert">
      <div><strong>回验统计暂不可用</strong><span>{{ statsError }}；推荐明细仍可独立查看。</span></div>
      <button type="button" class="secondary-button compact-button" @click="emit('retry')">重试</button>
    </div>
    <template v-else-if="stats">
      <p v-if="stats.win_rate_unit !== 'percent'" class="close-evidence-alert" role="status">胜率单位不是 percent，前端已拒绝猜测换算。</p>
      <p v-else-if="stats.enough_sample === false" class="close-evidence-note">可评分窗口 {{ formatNumber(stats.sample) }} 个，未达稳定胜率门槛 {{ formatNumber(stats.minimum_stable_sample) }}；稳定胜率保持缺失。</p>
      <dl class="close-track-stats">
        <div><dt>稳定胜率</dt><dd class="mono">{{ formatRate(stats.win_rate) }}</dd></div>
        <div><dt>原始观察</dt><dd class="mono">{{ formatRate(stats.observed_win_rate) }}</dd></div>
        <div><dt>可评分窗口</dt><dd class="mono">{{ formatNumber(stats.sample) }}</dd></div>
        <div><dt>盈亏比</dt><dd class="mono">{{ formatNumber(stats.profit_factor, 2) }}</dd></div>
      </dl>
      <div class="close-window-stats" aria-label="T0 T1 T2 T5 分窗口统计">
        <div v-for="windowName in windows" :key="windowName"><strong class="mono">{{ windowName }}</strong><span class="mono">{{ formatNumber(stats.per_window?.[windowName]?.sample) }} 样本</span><span>原始 {{ formatRate(stats.per_window?.[windowName]?.observed_win_rate) }}</span></div>
      </div>
      <p class="close-track-counts">待到期 {{ formatNumber(stats.waiting) }} · 未触发 {{ formatNumber(stats.untriggered) }} · 不可比 {{ formatNumber(stats.incomparable) }} · 已排除推荐 {{ formatNumber(stats.excluded) }}</p>
      <details v-if="stats.missing?.length" class="close-missing-details"><summary>查看 {{ stats.missing.length }} 条统计缺口</summary><ul><li v-for="item in stats.missing" :key="item">{{ item }}</li></ul></details>
    </template>

    <div v-if="recommendationError" class="close-local-state is-error" role="alert">
      <div><strong>推荐明细暂不可用</strong><span>{{ recommendationError }}；统计区不会因此清空。</span></div>
      <button type="button" class="secondary-button compact-button" @click="emit('retry')">重试</button>
    </div>
    <div v-else-if="recent.length" class="close-record-list">
      <article v-for="item in recent" :key="item.rec_id || item.code" class="close-record-item">
        <header>
          <a href="#/research?tab=diagnosis" @click="rememberDiagnosis(item.code)"><strong>{{ item.name || item.code || '未命名标的' }}</strong><small class="mono">{{ item.code || '—' }}</small></a>
          <span class="mono">荐价 {{ item.rec_price == null ? '—' : Number(item.rec_price).toFixed(2) }} 元</span>
          <span class="close-state-badge" :class="reviewMeta(latestReview(item)).tone">{{ reviewMeta(latestReview(item)).label }}</span>
        </header>
        <div class="close-window-timeline">
          <span v-for="windowName in windows" :key="windowName" :class="reviewMeta(reviewFor(item, windowName)).tone"><b class="mono">{{ windowName }}</b>{{ reviewMeta(reviewFor(item, windowName)).label }}</span>
        </div>
        <dl class="close-return-grid">
          <div><dt>标的涨跌</dt><dd class="mono">{{ formatPercent(latestReview(item)?.final_return, true) }}</dd></div>
          <div><dt>方向结果</dt><dd class="mono">{{ formatPercent(latestReview(item)?.directional_return, true) }}</dd></div>
          <div><dt>MFE</dt><dd class="mono">{{ formatPercent(latestReview(item)?.max_return, true) }}</dd></div>
          <div><dt>MAE</dt><dd class="mono">{{ formatPercent(latestReview(item)?.max_drawdown, true) }}</dd></div>
          <div><dt>目标日</dt><dd class="mono">{{ latestReview(item)?.target_trade_day || '—' }}</dd></div>
        </dl>
      </article>
    </div>
    <div v-else class="close-local-state is-empty"><strong>暂无推荐留痕</strong><span>没有真实记录时不展示模拟胜率；正式推荐产生后再按目标交易日回验。</span></div>
  </section>
</template>
