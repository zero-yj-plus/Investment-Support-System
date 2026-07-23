<script setup lang="ts">
import { computed } from 'vue'
import type { WatchlistItem } from '../types/tail'

const props = defineProps<{ items: WatchlistItem[]; error?: string }>()
const gradeOrder: Record<string, number> = { A: 0, B: 1, C: 2, D: 3, E: 4 }
const sortedItems = computed(() => [...props.items]
  .sort((a, b) => (gradeOrder[a.grade || ''] ?? 9) - (gradeOrder[b.grade || ''] ?? 9))
  .slice(0, 8))

function formatPrice(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return Number(value).toFixed(2)
}

function formatPercent(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function zone(item: WatchlistItem): string {
  if (item.target_low == null || item.target_high == null) return '—'
  return `${formatPrice(item.target_low)}–${formatPrice(item.target_high)}`
}

function rememberDiagnosis(code?: string) {
  if (!code) return
  sessionStorage.setItem('hermes.diagnosis.code', code)
}
</script>

<template>
  <section class="panel tail-evidence-card" aria-labelledby="tail-watchlist-title">
    <header class="tail-evidence-card__header">
      <div>
        <p class="eyebrow">WATCHLIST</p>
        <h2 id="tail-watchlist-title">观察池就绪机会</h2>
      </div>
      <span class="tail-evidence-count mono">{{ sortedItems.length }} 个可见</span>
    </header>

    <p v-if="error" class="tail-evidence-alert" role="status">观察池暂不可用：{{ error }}</p>
    <p class="tail-watchlist-note">按 A→E 接近买点排序；只展示服务端真实区间，缺失保持“—”。</p>

    <div v-if="sortedItems.length" class="tail-watchlist-table" aria-label="尾盘观察池机会">
      <div class="tail-watchlist-row is-head" aria-hidden="true">
        <span>评级 / 标的</span><span>现价</span><span>理想区间</span><span>状态</span>
      </div>
      <a v-for="item in sortedItems" :key="item.code" class="tail-watchlist-row" href="#/research?tab=diagnosis" @click="rememberDiagnosis(item.code)">
        <span class="tail-watchlist-name"><b class="grade-badge" :class="`grade-${item.grade || 'missing'}`">{{ item.grade || '—' }}</b><span><strong>{{ item.name || item.code || '未命名标的' }}</strong><small class="mono">{{ item.code || '—' }}</small></span></span>
        <span><strong class="mono">{{ formatPrice(item.quote?.price) }}</strong><small class="mono" :class="item.quote?.change_pct == null ? 'is-flat' : item.quote.change_pct >= 0 ? 'is-up' : 'is-down'">{{ formatPercent(item.quote?.change_pct) }}</small></span>
        <span class="mono">{{ zone(item) }}</span>
        <span>{{ item.status || '状态待确认' }}</span>
      </a>
    </div>
    <div v-else class="tail-evidence-empty">
      <strong>观察池还没有可展示标的</strong>
      <span>尾盘只从已有观察计划中筛选到价机会，不临时用示例标的补位。</span>
    </div>
  </section>
</template>
