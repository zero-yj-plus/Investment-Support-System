<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import type { PulseResponse, QualityState } from '../types/dashboard'
import { useTiming } from '../composables/useTiming'
import { finite, formatNumber, uniqueText } from '../utils/timingFormat'
import DataStatus from './DataStatus.vue'
import TimingFearGreed from './TimingFearGreed.vue'
import TimingRegionCard from './TimingRegionCard.vue'
import TimingStockLookup from './TimingStockLookup.vue'

const props = defineProps<{
  pulse: PulseResponse | null
  pulseError?: string
  marketSession?: string
  isTradeDay?: boolean
}>()
const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  data,
  loading,
  refreshing,
  error,
  stock,
  stockLoading,
  stockError,
  stockRequested,
  stockQueryCode,
  refresh,
  queryStock,
} = useTiming(() => props.isTradeDay !== false && ['intraday', 'tail'].includes(props.marketSession || ''))

watch(refreshing, (value) => emit('refreshState', value), { immediate: true })
onBeforeUnmount(() => emit('refreshState', false))

const indexes = computed(() => Array.isArray(data.value?.indexes) ? data.value.indexes.filter(Boolean) : [])
const indexesFieldPresent = computed(() => Array.isArray(data.value?.indexes))
const primaryIndex = computed(() => indexes.value.find((item) => item.code === 'sh000001' && item.ok !== false && item.zone)
  || indexes.value.find((item) => item.ok !== false && item.zone))
const command = computed(() => props.pulse?.ok === false ? undefined : props.pulse?.command)
const riskLevel = computed(() => props.pulse?.ok === false ? null : props.pulse?.risk_level ?? command.value?.risk_level ?? null)
const riskLabel = computed(() => props.pulse?.ok === false ? '风险待确认' : props.pulse?.risk_label || command.value?.level_name || '风险待确认')
const hasDecision = computed(() => Boolean(command.value && (
  command.value.today_action || command.value.position_cap || command.value.trade_state || command.value['定调叙述']
)))
const coverageNotes = computed(() => uniqueText([
  ...(data.value?.missing || []),
  ...(data.value?.fear_greed?.missing || []),
  ...indexes.value.flatMap((item) => item.missing || []),
]))
const partialIndexes = computed(() => indexes.value.some((item) => item.ok === false || !finite(item.score)))
const quality = computed<QualityState>(() => {
  if (!data.value) return error.value ? 'missing' : 'partial'
  if (data.value.stale || data.value.fear_greed?.stale || coverageNotes.value.length) return 'degraded'
  if (!Array.isArray(data.value.indexes) || !data.value.fear_greed || partialIndexes.value || data.value.fear_greed.ok === false) return 'partial'
  return 'complete'
})
const statusLabel = computed(() => ({
  complete: '择时数据可用',
  degraded: data.value?.stale || data.value?.fear_greed?.stale ? '陈旧或部分覆盖' : '部分覆盖',
  partial: '关键项缺失',
  missing: '择时暂不可用',
}[quality.value]))

defineExpose({ refresh })
</script>

<template>
  <section class="timing-page" aria-labelledby="timing-page-title" :aria-busy="loading || refreshing">
    <header class="timing-intro">
      <div>
        <p class="eyebrow">市场结构 · 确定性规则</p>
        <h1 id="timing-page-title">资金择时</h1>
        <p>先看服务端今日动作与风险边界，再用贪恐五因子、指数四区域和个股查询核验证据。</p>
      </div>
      <dl class="timing-stamp" :class="{ 'is-stale': data?.stale || data?.fear_greed?.stale }" aria-label="资金择时数据日期、取数时点与质量状态">
        <div><dt>数据日期</dt><dd class="mono">{{ data?.data_date || data?.fear_greed?.data_date || '—' }}</dd></div>
        <div><dt>取数时点</dt><dd class="mono">{{ data?.ts || '—' }}</dd></div>
        <div><dt>数据状态</dt><dd><DataStatus :state="quality" :label="statusLabel" /></dd></div>
      </dl>
    </header>

    <section class="panel timing-decision" aria-labelledby="timing-decision-title" aria-live="polite">
      <header class="timing-decision__header">
        <div>
          <p class="eyebrow">SERVER DECISION</p>
          <h2 id="timing-decision-title">当前结论与执行边界</h2>
        </div>
        <div class="timing-decision__meta">
          <span class="mono">决策定格 {{ pulse?.['定格时点'] || command?.['定格时点'] || '—' }}</span>
          <span class="tag" :class="`risk-${riskLevel ?? 'missing'}`">风险 {{ riskLevel == null ? '—' : `${riskLevel} 级` }} · {{ riskLabel }}</span>
        </div>
      </header>

      <div class="timing-decision__grid" :class="{ 'is-fallback': !hasDecision }">
        <article>
          <span>01</span>
          <div><p>当前状态</p><strong>{{ hasDecision ? command?.['定调叙述'] || command?.trade_state || command?.regime || '状态待确认' : '今日决策数据暂不可用，本页不生成替代结论。' }}</strong></div>
        </article>
        <article>
          <span>02</span>
          <div><p>{{ command?.action_label || '今日唯一主动作' }}</p><strong>{{ hasDecision ? command?.today_action || command?.level_text || '等待服务端确认动作' : '先恢复风险与行情数据，再决定是否行动。' }}</strong></div>
        </article>
        <article>
          <span>03</span>
          <div><p>仓位边界</p><strong>{{ hasDecision ? command?.position_cap || '—' : '不依据缺失数据新增或补仓' }}</strong></div>
        </article>
        <article>
          <span>04</span>
          <div><p>执行原则</p><strong>{{ hasDecision ? command?.first_principle || '按服务端风险边界执行' : '数据不完整时不强行给方向' }}</strong></div>
        </article>
      </div>

      <div class="timing-decision__evidence">
        <span>择时主参考</span>
        <strong>{{ primaryIndex ? `${primaryIndex.name || primaryIndex.code} · ${primaryIndex.zone}` : '指数四区域暂不可用' }}</strong>
        <p>{{ primaryIndex?.advice || '没有服务端指数建议时，不从贪恐分数反推仓位或交易动作。' }}</p>
      </div>
      <p v-if="pulseError" class="timing-decision__warning" role="alert">决策边界最新刷新失败：{{ pulseError }}<span v-if="pulse">；当前保留已加载服务端值。</span></p>
    </section>

    <div v-if="loading && !data" class="timing-loading" role="status" aria-live="polite">
      <span class="sr-only">资金择时正在加载</span>
      <section class="panel timing-skeleton timing-skeleton--fear"></section>
      <section class="panel timing-skeleton timing-skeleton--indexes"></section>
    </div>

    <template v-else>
      <div v-if="error" class="timing-alert" role="alert">
        <div><strong>{{ data ? '最新择时刷新失败，保留已加载数据' : '择时证据暂不可用' }}</strong><span>{{ error }}</span></div>
        <button class="secondary-button" type="button" :disabled="refreshing" @click="refresh">重试</button>
      </div>

      <div v-if="data" class="timing-evidence-grid">
        <TimingFearGreed :reading="data.fear_greed" />

        <section class="panel timing-index-panel" aria-labelledby="timing-index-title">
          <header class="section-heading">
            <div><p class="eyebrow">REGION EVIDENCE</p><h2 id="timing-index-title">指数四区域</h2></div>
            <span>区域与评分均由服务端规则返回</span>
          </header>
          <div v-if="indexes.length" class="timing-index-grid">
            <TimingRegionCard v-for="item in indexes" :key="item.code || item.name" :reading="item" kind="index" />
          </div>
          <div v-else-if="indexesFieldPresent" class="timing-index-empty">
            <strong>指数择时返回真实空数组</strong>
            <span>当前没有可展示记录；页面未使用历史指数或示例数值补位。</span>
          </div>
          <div v-else class="timing-index-empty">
            <strong>指数择时字段缺失</strong>
            <span>接口未返回 indexes 数组，与真实空数组分开处理。</span>
          </div>
        </section>
      </div>

      <TimingStockLookup
        :reading="stock"
        :loading="stockLoading"
        :error="stockError"
        :requested="stockRequested"
        :query-code="stockQueryCode"
        @query="queryStock"
      />

      <section class="panel timing-health" aria-labelledby="timing-health-title">
        <header class="section-heading">
          <div><p class="eyebrow">DATA GOVERNANCE</p><h2 id="timing-health-title">数据健康与口径</h2></div>
          <span>{{ coverageNotes.length ? `${coverageNotes.length} 条缺失或降级说明` : '接口未声明缺口' }}</span>
        </header>
        <div class="timing-health__grid">
          <article><strong>来源</strong><span>指数报价与日线、市场活跃度、后台资金快照；以服务端实际返回为准。</span></article>
          <article><strong>时效</strong><span>贪恐为最近收盘日线温度；指数价格可能为盘中点位，二者不可混称实时。</span></article>
          <article><strong>治理</strong><span>风险、主动作、仓位边界与四区域结论均来自服务端；前端不重算。</span></article>
        </div>
        <details v-if="coverageNotes.length" class="timing-health__details">
          <summary>查看全部缺失与降级说明</summary>
          <ul><li v-for="item in coverageNotes" :key="item">{{ item }}</li></ul>
        </details>
      </section>

      <footer class="page-footer timing-footer">
        <span>价格：个股为元、指数为点；涨跌幅与换手为百分数，资金净额为元。</span>
        <span>本页不消费 Markdown，也不注入原始 HTML；缺失、空结果、陈旧和请求失败分别展示。</span>
      </footer>
    </template>
  </section>
</template>

<style scoped>
.timing-page { display: grid; min-width: 0; gap: 12px; }
.timing-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.timing-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.timing-intro > div > p:last-child { max-width: 800px; margin: 7px 0 0; color: var(--text-2); }
.timing-stamp { display: grid; min-width: 470px; grid-template-columns: minmax(100px, .8fr) minmax(160px, 1.2fr) minmax(160px, 1fr); gap: 12px; margin: 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.timing-stamp.is-stale { border-color: var(--warning); }
.timing-stamp div { min-width: 0; }
.timing-stamp dt { color: var(--text-3); font-size: 12px; }
.timing-stamp dd { margin: 2px 0 0; font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }
.timing-stamp :deep(.data-status) { min-height: 26px; white-space: normal; }
.timing-decision { --decision-color: var(--accent); overflow: hidden; border-color: color-mix(in srgb, var(--decision-color) 48%, var(--border)); }
.timing-decision__header { display: flex; min-height: 66px; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 14px; border-bottom: 1px solid var(--border); }
.timing-decision__header h2 { margin: 0; font-size: 19px; }
.timing-decision__meta { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; }
.timing-decision__meta > .mono { color: var(--text-3); font-size: 12px; }
.timing-decision__grid { display: grid; grid-template-columns: .9fr 1.45fr .75fr 1fr; }
.timing-decision__grid article { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 7px; min-width: 0; padding: 13px 12px 15px; border-right: 1px solid var(--border); }
.timing-decision__grid article:last-child { border-right: 0; }
.timing-decision__grid article > span { color: var(--text-3); font: 500 12px/1.5 "SFMono-Regular", Consolas, monospace; }
.timing-decision__grid p { margin: 0 0 3px; color: var(--text-3); font-size: 13px; font-weight: 700; }
.timing-decision__grid strong { display: block; font-size: 16px; line-height: 1.55; }
.timing-decision__grid.is-fallback strong { color: var(--text-2); font-weight: 560; }
.timing-decision__evidence { display: grid; grid-template-columns: 110px minmax(160px, auto) minmax(0, 1fr); align-items: center; gap: 10px; padding: 10px 14px; border-top: 1px solid var(--border); background: var(--surface-2); }
.timing-decision__evidence > span { color: var(--text-3); font-size: 12px; font-weight: 700; }
.timing-decision__evidence strong { color: var(--decision-color); }
.timing-decision__evidence p { margin: 0; color: var(--text-2); font-size: 13px; }
.timing-decision__warning { margin: 0; padding: 9px 14px; border-top: 1px solid var(--warning); color: var(--warning); font-size: 13px; }
.timing-decision__warning span { color: var(--text-2); }
.timing-loading { display: grid; gap: 12px; }
.timing-skeleton { position: relative; overflow: hidden; }
.timing-skeleton::after { position: absolute; inset: 0; background: color-mix(in srgb, var(--surface-3) 70%, transparent); content: ""; animation: timing-page-pulse 1.2s ease-in-out infinite alternate; }
.timing-skeleton--fear { min-height: 340px; }
.timing-skeleton--indexes { min-height: 520px; }
.timing-alert { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 12px 14px; border: 1px solid var(--danger); border-radius: var(--radius); background: color-mix(in srgb, var(--danger) 7%, var(--surface)); }
.timing-alert > div { display: grid; gap: 2px; }
.timing-alert span { color: var(--text-2); }
.timing-evidence-grid { display: grid; grid-template-columns: minmax(0, .88fr) minmax(0, 1.12fr); gap: 12px; align-items: start; }
.timing-index-panel { min-width: 0; overflow: hidden; }
.timing-index-panel .section-heading > span { color: var(--text-3); font-size: 12px; text-align: right; }
.timing-index-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; padding: 10px; }
.timing-index-empty { display: grid; min-height: 300px; align-content: center; gap: 4px; padding: 24px; color: var(--text-3); text-align: center; }
.timing-index-empty strong { color: var(--text-2); }
.timing-health { min-width: 0; overflow: hidden; }
.timing-health .section-heading > span { color: var(--text-3); font-size: 12px; }
.timing-health__grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.timing-health__grid article { display: grid; min-width: 0; gap: 3px; padding: 12px 14px; border-right: 1px solid var(--border); }
.timing-health__grid article:last-child { border-right: 0; }
.timing-health__grid strong { font-size: 13px; }
.timing-health__grid span { color: var(--text-3); font-size: 13px; }
.timing-health__details summary { min-height: 44px; padding: 11px 14px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 13px; cursor: pointer; }
.timing-health__details ul { margin: 0; padding: 0 30px 14px 36px; color: var(--text-3); font-size: 12px; }
.timing-footer { padding-top: 0; }
@keyframes timing-page-pulse { from { opacity: .42; } to { opacity: .78; } }
@media (max-width: 1180px) {
  .timing-intro { align-items: stretch; flex-direction: column; gap: 12px; }
  .timing-stamp { min-width: 0; }
  .timing-evidence-grid { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 900px) {
  .timing-decision__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .timing-decision__grid article:nth-child(2) { border-right: 0; }
  .timing-decision__grid article:nth-child(-n + 2) { border-bottom: 1px solid var(--border); }
  .timing-index-grid { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 720px) {
  .timing-page { gap: 8px; }
  .timing-stamp { grid-template-columns: minmax(0, 1fr); }
  .timing-decision__header { align-items: flex-start; flex-direction: column; }
  .timing-decision__meta { justify-content: flex-start; }
  .timing-decision__grid { grid-template-columns: minmax(0, 1fr); }
  .timing-decision__grid article { border-right: 0; border-bottom: 1px solid var(--border); }
  .timing-decision__grid article:last-child { border-bottom: 0; }
  .timing-decision__evidence { grid-template-columns: minmax(0, 1fr); gap: 3px; }
  .timing-health__grid { grid-template-columns: minmax(0, 1fr); }
  .timing-health__grid article { border-right: 0; border-bottom: 1px solid var(--border); }
  .timing-health__grid article:last-child { border-bottom: 0; }
  .timing-alert { align-items: stretch; flex-direction: column; }
  .timing-alert .secondary-button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .timing-skeleton::after { animation: none; }
}
</style>
