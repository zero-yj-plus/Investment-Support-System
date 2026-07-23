<script setup lang="ts">
import { computed } from 'vue'
import type { MultiperiodResponse } from '../types/dashboard'

const props = defineProps<{
  data: MultiperiodResponse | null
  loading: boolean
  error?: string
}>()

const rows = computed(() => props.data?.ok === false ? [] : (props.data?.stocks || []).slice(0, 12))

function finite(value?: number | null): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function percent(value?: number | null) {
  return finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—'
}

function tone(value?: number | null) {
  if (!finite(value)) return 'is-flat'
  return value >= 0 ? 'is-up' : 'is-down'
}

const coverage = computed(() => props.data?.coverage?.period_rows)
</script>

<template>
  <section class="panel evidence-card multiperiod-panel" aria-labelledby="multiperiod-title">
    <header class="section-heading evidence-heading">
      <div>
        <p class="eyebrow">MULTI-PERIOD LEADERS</p>
        <h3 id="multiperiod-title">多周期涨幅榜 TOP</h3>
      </div>
      <div class="section-heading__meta">
        <span v-if="coverage">历史完整 {{ coverage.complete ?? '—' }} / {{ coverage.total ?? '—' }}</span>
        <span>{{ data?.data_date || data?.ts || '时点待确认' }}</span>
      </div>
    </header>

    <div v-if="loading && !data" class="table-skeleton" role="status" aria-label="多周期涨幅榜加载中">
      <span v-for="item in 8" :key="item" class="skeleton-line"></span>
    </div>
    <template v-else-if="rows.length">
      <p v-if="data?.data_state === 'degraded' || data?.missing?.length" class="evidence-note is-warning" role="status">
        {{ data?.state_label || data?.missing?.join('；') }}
      </p>
      <div class="evidence-table-wrap" tabindex="0" aria-label="多周期涨幅榜，可横向或纵向滚动">
        <table class="evidence-table">
          <thead>
            <tr><th scope="col">名称</th><th scope="col">题材 / 行业</th><th scope="col">当日</th><th scope="col">3 日</th><th scope="col">5 日</th><th scope="col">20 日</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in rows" :key="item.code || item.name">
              <th scope="row"><strong>{{ item.name || item.code || '未知标的' }}</strong><span>{{ item.code || '代码缺失' }}</span></th>
              <td>{{ item.theme || '—' }}</td>
              <td :class="tone(item.d1)" class="mono">{{ percent(item.d1) }}</td>
              <td :class="tone(item.d3)" class="mono">{{ percent(item.d3) }}</td>
              <td :class="tone(item.d5)" class="mono">{{ percent(item.d5) }}</td>
              <td :class="tone(item.d20)" class="mono">{{ percent(item.d20) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
    <div v-else class="empty-state compact-empty" role="status">
      <strong>多周期涨幅暂不可用</strong>
      <span>{{ error || data?.missing_fields?.join('；') || data?.missing?.join('；') || '涨幅榜标的池或免费日线样本暂不可用。' }}</span>
    </div>
  </section>
</template>
