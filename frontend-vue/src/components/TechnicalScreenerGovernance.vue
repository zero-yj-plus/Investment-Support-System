<script setup lang="ts">
import { computed } from 'vue'
import type { TechnicalCatalogResponse, TechnicalHistoryEvent, TechnicalStatusResponse } from '../types/technicalScreener'
import { formatTechnicalInteger, formatTechnicalMetrics } from '../utils/technicalScreenerFormat'
import DataStatus from './DataStatus.vue'
import StockAutocomplete from './StockAutocomplete.vue'

const props = defineProps<{
  catalog: TechnicalCatalogResponse | null
  status: TechnicalStatusResponse | null
  catalogError: string
  statusError: string
  historyCode: string
  historyEvents: TechnicalHistoryEvent[]
  historyLoading: boolean
  historyRequested: boolean
  historyError: string
}>()

const emit = defineEmits<{
  updateHistoryCode: [value: string]
  loadHistory: []
}>()

const historyCodeModel = computed({
  get: () => props.historyCode,
  set: (value: string) => emit('updateHistoryCode', value),
})

function statusState(status: TechnicalStatusResponse | null): 'complete' | 'partial' | 'missing' {
  if (!status || status.state === 'missing') return 'missing'
  return status.state === 'ready' ? 'complete' : 'partial'
}
</script>

<template>
  <div class="technical-governance-grid">
    <section class="panel technical-history" aria-labelledby="technical-history-title">
      <header><div><p class="eyebrow">追加式事件</p><h2 id="technical-history-title">历史信号命中</h2></div><span>不计算收益或胜率</span></header>
      <form @submit.prevent="emit('loadHistory')">
        <StockAutocomplete
          v-model="historyCodeModel"
          class="technical-history-autocomplete"
          input-id="technical-history-code"
          label="股票代码"
          placeholder="代码 / 中文 / 拼音 / 首字母"
          :aria-invalid="Boolean(historyError)"
        />
        <button class="secondary-button" type="submit" :disabled="historyLoading">{{ historyLoading ? '查询中' : '查询历史命中' }}</button>
      </form>
      <p v-if="historyError" class="technical-history-error" role="alert">{{ historyError }}</p>
      <ol v-else-if="historyEvents.length" class="technical-history-list">
        <li v-for="item in historyEvents" :key="`${item.code}-${item.signal_code}-${item.timeframe}-${item.event_date}`">
          <time class="mono">{{ item.event_date }}</time>
          <strong>{{ item.timeframe }} · {{ item.signal_name }}</strong>
          <span>{{ formatTechnicalMetrics(item.metrics) }}</span>
        </li>
      </ol>
      <p v-else-if="historyRequested && !historyLoading" class="technical-history-empty">缓存范围内没有该股票的事件命中；这是接口返回的真实空数组。</p>
      <p v-else class="technical-history-empty">输入代码后读取真实事件缓存。</p>
    </section>

    <section class="panel technical-health" aria-labelledby="technical-health-title">
      <header><div><p class="eyebrow">版本与治理</p><h2 id="technical-health-title">数据健康</h2></div><DataStatus :state="statusState(status)" :label="status?.state === 'ready' ? '缓存完整' : status?.state === 'partial' ? '部分覆盖' : status?.state === 'running' || status?.state === 'queued' ? '刷新处理中' : '缓存缺失'" /></header>
      <dl>
        <div><dt>缓存股票</dt><dd class="mono">{{ formatTechnicalInteger(status?.stocks) }} 只</dd></div>
        <div><dt>K 线记录</dt><dd class="mono">{{ formatTechnicalInteger(status?.bars) }} 条</dd></div>
        <div><dt>信号命中</dt><dd class="mono">{{ formatTechnicalInteger(status?.signal_matches) }} 条</dd></div>
        <div><dt>数据日期</dt><dd class="mono">{{ status?.data_date || '—' }}</dd></div>
        <div><dt>复权</dt><dd>{{ status?.adjustment === 'qfq' ? '前复权' : (status?.adjustment || '—') }}</dd></div>
        <div><dt>算法版本</dt><dd class="mono">{{ status?.algorithm_ver || catalog?.algorithm_ver || '—' }}</dd></div>
        <div><dt>规则结构</dt><dd class="mono">v{{ status?.config_schema_version ?? catalog?.config_schema_version ?? '—' }}</dd></div>
        <div><dt>可用信号</dt><dd class="mono">{{ catalog ? `${catalog.signals.filter((item) => item.available).length}/${catalog.signals.length}` : '—' }}</dd></div>
      </dl>
      <p v-if="status?.level2_available === false" class="technical-health-note">Level-2：{{ status.level2_reason || '当前无可核验数据源' }}。相关条件不会用成交量推断。</p>
      <p v-if="catalogError" class="technical-health-error" role="alert">信号目录局部失败：{{ catalogError }}</p>
      <p v-if="statusError" class="technical-health-error" role="alert">缓存状态局部失败：{{ statusError }}</p>
    </section>
  </div>
</template>

<style scoped>
.technical-governance-grid { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(320px, .85fr); gap: 12px; }
.technical-history, .technical-health { min-width: 0; padding: 12px; }
.technical-history > header, .technical-health > header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.technical-history h2, .technical-health h2 { margin: 0; font-size: 18px; }
.technical-history > header > span { color: var(--text-3); font-size: 12px; }
.technical-history form { display: grid; grid-template-columns: minmax(220px, 1fr) auto; align-items: end; gap: 8px; margin-top: 12px; }
.technical-history-list { display: grid; gap: 0; margin: 12px 0 0; padding: 0; list-style: none; }
.technical-history-list li { display: grid; grid-template-columns: 110px minmax(140px, 210px) minmax(0, 1fr); gap: 8px; padding: 8px 4px; border-bottom: 1px solid var(--border); }
.technical-history-list span, .technical-history-empty { color: var(--text-3); }
.technical-history-error, .technical-health-error, .technical-health-note { padding: 8px 10px; border-left: 3px solid var(--danger); background: color-mix(in srgb, var(--danger) 8%, var(--surface)); }
.technical-health-note { border-left-color: var(--warning); background: color-mix(in srgb, var(--warning) 8%, var(--surface)); color: var(--text-2); }
.technical-health dl { display: grid; grid-template-columns: 1fr 1fr; margin: 12px 0 0; border: 1px solid var(--border); border-radius: 7px; overflow: hidden; }
.technical-health dl div { min-width: 0; padding: 8px 10px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.technical-health dl div:nth-child(2n) { border-right: 0; }
.technical-health dt { color: var(--text-3); font-size: 12px; }
.technical-health dd { margin: 3px 0 0; overflow-wrap: anywhere; font-weight: 700; }
@media (max-width: 900px) { .technical-governance-grid { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 720px) {
  .technical-history form { grid-template-columns: minmax(0, 1fr); }
  .technical-history-list li { grid-template-columns: minmax(0, 1fr); }
}
</style>
