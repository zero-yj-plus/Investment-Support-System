<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import type { RecommendationItem, RecommendationReview, RecommendationStatsResponse } from '../types/tail'

const props = defineProps<{
  stats: RecommendationStatsResponse | null
  recommendations: RecommendationItem[]
  statsError?: string
  recommendationError?: string
  verifying: boolean
}>()
const emit = defineEmits<{ verify: [] }>()
const windows = ['T0', 'T1', 'T2', 'T5'] as const

const hasStats = computed(() => Boolean(props.stats))
const recent = computed(() => props.recommendations.slice(0, 5))

function formatNumber(value?: number | null, digits = 0): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return Number(value).toFixed(digits)
}

function formatRate(value?: number | null): string {
  if (props.stats?.win_rate_unit !== 'percent' || value == null || !Number.isFinite(Number(value))) return '—'
  return `${Number(value).toFixed(1)}%`
}

function latestReview(item: RecommendationItem): RecommendationReview | undefined {
  if (item.review) return item.review
  const reviews = item.reviews || []
  if (reviews.length) return reviews[reviews.length - 1]
  for (const windowName of [...windows].reverse()) {
    const review = item.review_windows?.[windowName]
    if (review) return review
  }
  return undefined
}

function reviewMeta(review?: RecommendationReview) {
  const state = review?.evaluation_state || ''
  const verdict = review?.verdict || ''
  if (!review || state === 'waiting') return { label: '待到期', tone: 'is-waiting' }
  if (state === 'untriggered' || verdict === 'not_triggered') return { label: '未触发', tone: 'is-neutral' }
  if (state === 'incomparable' || verdict === 'insufficient_evidence') return { label: '证据不足', tone: 'is-warning' }
  if (verdict === 'hit') return { label: '方向命中', tone: 'is-positive' }
  if (verdict === 'partial_hit') return { label: '部分命中', tone: 'is-neutral' }
  if (verdict === 'invalidated') return { label: '方向失效', tone: 'is-negative' }
  if (review.hit_status === 'hit') return { label: '方向命中', tone: 'is-positive' }
  if (review.hit_status === 'miss') return { label: '方向失效', tone: 'is-negative' }
  return { label: '待核验', tone: 'is-warning' }
}

function rememberDiagnosis(code?: string) {
  if (!code) return
  sessionStorage.setItem('hermes.diagnosis.code', code)
}
</script>

<template>
  <section class="panel tail-evidence-card" aria-labelledby="tail-track-title">
    <header class="tail-evidence-card__header">
      <div>
        <p class="eyebrow">TRACK RECORD</p>
        <h2 id="tail-track-title">推荐留痕与回验</h2>
      </div>
      <button class="secondary-button compact-button" type="button" :disabled="verifying" @click="emit('verify')">
        <AppIcon name="refresh" :size="16" :class="{ spinning: verifying }" />{{ verifying ? '回验中' : '执行回验' }}
      </button>
    </header>

    <p v-if="statsError || recommendationError" class="tail-evidence-alert" role="status">
      {{ [statsError, recommendationError].filter(Boolean).join('；') }}
    </p>

    <template v-if="hasStats">
      <p v-if="stats?.enough_sample === false" class="tail-sample-note">
        当前 {{ formatNumber(stats?.sample) }} 个可评分窗口，未达稳定胜率门槛 {{ formatNumber(stats?.minimum_stable_sample) }}；只展示原始比例供审计。
      </p>
      <dl class="tail-stats-grid">
        <div><dt>稳定胜率</dt><dd class="mono">{{ formatRate(stats?.win_rate) }}</dd></div>
        <div><dt>原始观察</dt><dd class="mono">{{ formatRate(stats?.observed_win_rate) }}</dd></div>
        <div><dt>可评分窗口</dt><dd class="mono">{{ formatNumber(stats?.sample) }}</dd></div>
        <div><dt>盈亏比</dt><dd class="mono">{{ formatNumber(stats?.profit_factor, 2) }}</dd></div>
      </dl>
      <div class="tail-window-grid" aria-label="分窗口回验统计">
        <div v-for="windowName in windows" :key="windowName">
          <strong class="mono">{{ windowName }}</strong>
          <span class="mono">{{ formatNumber(stats?.per_window?.[windowName]?.sample) }} 样本</span>
          <span>原始 {{ formatRate(stats?.per_window?.[windowName]?.observed_win_rate) }}</span>
        </div>
      </div>
      <p class="tail-stats-foot">
        待到期 {{ formatNumber(stats?.waiting) }} · 未触发 {{ formatNumber(stats?.untriggered) }} · 不可比 {{ formatNumber(stats?.incomparable) }} · 已排除 {{ formatNumber(stats?.excluded) }}
      </p>
    </template>

    <div v-if="recent.length" class="tail-record-list">
      <a v-for="item in recent" :key="item.rec_id || item.code" href="#/research?tab=diagnosis" @click="rememberDiagnosis(item.code)">
        <span><strong>{{ item.name || item.code || '未命名标的' }}</strong><small class="mono">{{ item.code || '—' }}</small></span>
        <span class="mono">荐价 {{ item.rec_price == null ? '—' : Number(item.rec_price).toFixed(2) }}</span>
        <span class="tail-review-badge" :class="reviewMeta(latestReview(item)).tone">{{ reviewMeta(latestReview(item)).label }}</span>
      </a>
    </div>
    <div v-else class="tail-evidence-empty">
      <strong>还没有推荐留痕</strong>
      <span>没有真实记录时不展示模拟胜率；正式推荐产生后再按真实价格回验。</span>
    </div>
  </section>
</template>
