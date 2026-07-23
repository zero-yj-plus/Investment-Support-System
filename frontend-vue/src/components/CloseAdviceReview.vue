<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import type { HoldingsAdviceReviewResponse, HoldingsAdviceReviewRow } from '../types/closeReview'

const props = defineProps<{ data: HoldingsAdviceReviewResponse | null; error?: string }>()
const emit = defineEmits<{ retry: [] }>()
const rows = computed(() => props.data?.rows || [])

function formatPrice(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return Number(value).toFixed(2)
}

function formatPercent(value?: number | null, signed = true): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const number = Number(value)
  return `${signed && number > 0 ? '+' : ''}${number.toFixed(1)}%`
}

function verdictMeta(row: HoldingsAdviceReviewRow) {
  if (row.verdict === '兑现') return { label: '方向兑现', tone: 'is-positive' }
  if (row.verdict === '未兑现') return { label: '方向未兑现', tone: 'is-negative' }
  if (row.verdict === '中性') return { label: '中性 · 不计分', tone: 'is-neutral' }
  return { label: '暂不可比', tone: 'is-warning' }
}

function rememberDiagnosis(code?: string) {
  if (code) sessionStorage.setItem('hermes.diagnosis.code', code)
}
</script>

<template>
  <section class="panel close-evidence-card" aria-labelledby="close-advice-title">
    <header class="close-evidence-card__header">
      <div><p class="eyebrow">RULE-BASED REVIEW</p><h2 id="close-advice-title">昨日建议 vs 今日实际</h2></div>
      <span class="close-evidence-count mono">对照日 {{ data?.base_date || '—' }}</span>
    </header>

    <div v-if="error" class="close-local-state is-error" role="alert">
      <div><strong>策略自证暂不可用</strong><span>{{ error }}；不会展示推断数字。</span></div>
      <button type="button" class="secondary-button compact-button" @click="emit('retry')"><AppIcon name="refresh" :size="16" />重试</button>
    </div>

    <template v-else-if="rows.length">
      <dl class="close-advice-stats">
        <div><dt>兑现</dt><dd class="mono">{{ data?.stats?.hit ?? '—' }}</dd></div>
        <div><dt>未兑现</dt><dd class="mono">{{ data?.stats?.miss ?? '—' }}</dd></div>
        <div><dt>中性 / 不可比</dt><dd class="mono">{{ data?.stats?.neutral ?? '—' }}</dd></div>
        <div><dt>命中率</dt><dd class="mono">{{ formatPercent(data?.stats?.win_rate, false) }}</dd></div>
      </dl>
      <p class="close-evidence-note">价格单位：元；涨跌与命中率单位：百分数。±0.5% 内为中性，不进入命中分母。</p>
      <div class="close-advice-table-wrap" tabindex="0" aria-label="昨日建议回检明细，可横向滚动">
        <table class="close-advice-table">
          <thead><tr><th scope="col">持仓</th><th scope="col">昨日收盘</th><th scope="col">今日价</th><th scope="col">今日涨跌</th><th scope="col">昨日建议</th><th scope="col">结果</th></tr></thead>
          <tbody>
            <tr v-for="row in rows" :key="row.code || row.name">
              <th scope="row"><a href="#/research?tab=diagnosis" @click="rememberDiagnosis(row.code)"><strong>{{ row.name || row.code || '未命名标的' }}</strong><small class="mono">{{ row.code || '—' }}</small></a></th>
              <td data-label="昨日收盘" class="mono">{{ formatPrice(row.base_price) }}</td>
              <td data-label="今日价" class="mono">{{ row.today_price == null ? '未取得' : formatPrice(row.today_price) }}</td>
              <td data-label="今日涨跌" class="mono" :class="row.change_pct == null ? 'is-flat' : row.change_pct >= 0 ? 'is-up' : 'is-down'">{{ formatPercent(row.change_pct) }}</td>
              <td data-label="昨日建议">{{ row.tendency || '建议缺失' }}</td>
              <td data-label="结果"><span class="close-state-badge" :class="verdictMeta(row).tone">{{ verdictMeta(row).label }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <div v-else class="close-local-state is-empty">
      <strong>暂无可回检的昨日建议</strong>
      <span>{{ data?.missing?.join('；') || '真实记录为空；生成复盘后，次日起可用真实价格对账。' }}</span>
    </div>
  </section>
</template>
