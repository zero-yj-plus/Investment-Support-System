<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import type { TomorrowWatchItem } from '../types/tomorrow'

const props = defineProps<{ items: TomorrowWatchItem[]; error?: string; refreshing?: boolean }>()
const emit = defineEmits<{ retry: [] }>()
const gradeOrder: Record<string, number> = { A: 0, B: 1, C: 2, D: 3, E: 4 }
const sortedItems = computed(() => [...props.items]
  .sort((a, b) => (gradeOrder[a.grade || ''] ?? 9) - (gradeOrder[b.grade || ''] ?? 9)))

function formatPrice(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return `${Number(value).toFixed(2)} 元`
}

function formatPercent(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return '涨跌缺失'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function zone(item: TomorrowWatchItem): string {
  if (item.target_low == null || item.target_high == null) return '—'
  return `${Number(item.target_low).toFixed(2)}–${Number(item.target_high).toFixed(2)} 元`
}

function rememberDiagnosis(code?: string) {
  if (code) sessionStorage.setItem('hermes.diagnosis.code', code)
}
</script>

<template>
  <section id="tomorrow-watchlist" class="panel tomorrow-watch" aria-labelledby="tomorrow-watch-title">
    <header class="tomorrow-card-head">
      <div><p class="eyebrow">明日结构化证据</p><h2 id="tomorrow-watch-title">观察池触发条件</h2></div>
      <a class="secondary-button compact-button" href="#/risk?tab=watchlist">管理观察池</a>
    </header>
    <p class="tomorrow-watch__note">按服务端 A→E 评级排序；价格与区间单位均为元。评级只表示接近买点，不替代明日风险闸。</p>

    <div v-if="error" class="tomorrow-local-state is-error" role="status">
      <div><strong>明日观察池暂不可用</strong><span>{{ error }}</span></div>
      <button type="button" class="secondary-button" :disabled="refreshing" @click="emit('retry')">
        <AppIcon name="refresh" :size="17" :class="{ spinning: refreshing }" />{{ refreshing ? '重试中' : '重试' }}
      </button>
    </div>

    <div v-else-if="sortedItems.length" class="tomorrow-watch__list" aria-label="明日观察池排序">
      <a
        v-for="item in sortedItems"
        :key="item.code || item.name"
        class="tomorrow-watch__row"
        href="#/research?tab=diagnosis"
        @click="rememberDiagnosis(item.code)"
      >
        <span class="tomorrow-watch__identity">
          <b class="grade-badge" :class="`grade-${item.grade || 'missing'}`">{{ item.grade || '—' }}</b>
          <span><strong>{{ item.name || item.code || '未命名标的' }}</strong><small class="mono">{{ item.code || '代码缺失' }}</small></span>
        </span>
        <span><small>现价</small><strong class="mono">{{ formatPrice(item.quote?.price) }}</strong><em class="mono" :class="item.quote?.change_pct == null ? 'is-flat' : item.quote.change_pct >= 0 ? 'is-up' : 'is-down'">{{ formatPercent(item.quote?.change_pct) }}</em></span>
        <span><small>理想买入区间</small><strong class="mono">{{ zone(item) }}</strong><em>{{ item.target_source === 'missing' ? '区间缺失' : item.quote?.stale ? '陈旧报价' : '服务端区间' }}</em></span>
        <span><small>当前状态</small><strong>{{ item.status || '状态待确认' }}</strong><em v-if="item.missing?.length">缺失：{{ item.missing.join('、') }}</em></span>
      </a>
    </div>

    <div v-else class="tomorrow-local-state is-empty">
      <strong>观察池返回真实空数组</strong>
      <span>当前没有可排序的计划标的，页面不会用示例股票或零值补位。</span>
      <a class="secondary-button" href="#/risk?tab=watchlist">添加观察标的</a>
    </div>
  </section>
</template>

<style scoped>
.tomorrow-watch { min-width: 0; overflow: hidden; scroll-margin-top: 88px; }
.tomorrow-card-head { display: flex; min-height: 68px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.tomorrow-card-head h2 { margin: 0; font-size: 18px; }
.tomorrow-watch__note { margin: 0; padding: 9px 12px; border-bottom: 1px solid var(--border); background: var(--surface-2); color: var(--text-3); font-size: 12px; }
.tomorrow-watch__list { display: grid; }
.tomorrow-watch__row { display: grid; grid-template-columns: minmax(170px, 1.1fr) minmax(130px, .72fr) minmax(168px, .9fr) minmax(150px, 1fr); min-height: 72px; align-items: center; gap: 12px; padding: 8px 12px; border-bottom: 1px solid var(--border); color: var(--text-2); text-decoration: none; }
.tomorrow-watch__row:last-child { border-bottom: 0; }
.tomorrow-watch__row:hover { background: var(--surface-2); }
.tomorrow-watch__row > span { display: grid; min-width: 0; gap: 2px; }
.tomorrow-watch__row small, .tomorrow-watch__row em { color: var(--text-3); font-size: 12px; font-style: normal; }
.tomorrow-watch__row strong { overflow-wrap: anywhere; }
.tomorrow-watch__identity { display: flex !important; align-items: center; gap: 8px; }
.tomorrow-watch__identity > span { display: grid; min-width: 0; }
.tomorrow-watch__identity strong { color: var(--text); }
.tomorrow-local-state { display: flex; min-height: 176px; align-items: center; justify-content: center; gap: 14px; padding: 20px; color: var(--text-3); text-align: center; }
.tomorrow-local-state > div { display: grid; gap: 3px; text-align: left; }
.tomorrow-local-state strong { color: var(--text-2); }
.tomorrow-local-state.is-error { min-height: 144px; color: var(--warning); }
.tomorrow-local-state.is-error span { color: var(--text-3); }
.tomorrow-local-state.is-empty { flex-direction: column; }
@media (max-width: 720px) {
  .tomorrow-card-head { align-items: flex-start; }
  .tomorrow-watch__row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px 14px; min-height: 0; padding: 12px; }
  .tomorrow-watch__identity { grid-column: 1 / -1; }
  .tomorrow-watch__row > span:last-child { grid-column: 1 / -1; }
  .tomorrow-local-state.is-error { flex-direction: column; }
}
@media (max-width: 520px) {
  .tomorrow-watch__row { grid-template-columns: minmax(0, 1fr); }
  .tomorrow-watch__identity, .tomorrow-watch__row > span:last-child { grid-column: auto; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
