<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { useDiagnosis } from '../composables/useDiagnosis'
import { formatBeijingTime, formatCompactMoney, formatNumber, formatPercent, qualityLabel, qualityState } from '../utils/diagnosisFormat'
import DataStatus from './DataStatus.vue'
import DiagnosisDataHealth from './DiagnosisDataHealth.vue'
import DiagnosisDecision from './DiagnosisDecision.vue'
import DiagnosisEvidence from './DiagnosisEvidence.vue'
import DiagnosisSearch from './DiagnosisSearch.vue'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  inputCode,
  requestedCode,
  recentCodes,
  requested,
  inputError,
  quote,
  diagnosis,
  quoteLoading,
  diagnosisLoading,
  quoteError,
  diagnosisError,
  busy,
  diagnose,
  refresh,
} = useDiagnosis()

watch(busy, (value) => emit('refreshState', value), { immediate: true })
onBeforeUnmount(() => emit('refreshState', false))

const displayedQuote = computed(() => quote.value?.quote || diagnosis.value?.market_quote || null)
const quality = computed(() => qualityState(diagnosis.value))
const statusLabel = computed(() => qualityLabel(diagnosis.value))
const dataDate = computed(() => diagnosis.value?.analysis_pipeline?.data_as_of || diagnosis.value?.analysis_contract?.data_as_of || displayedQuote.value?.as_of || '')
const quoteTone = computed(() => {
  const change = displayedQuote.value?.change_pct
  if (change == null) return 'is-flat'
  return change > 0 ? 'is-up' : change < 0 ? 'is-down' : 'is-flat'
})

defineExpose({ refresh })
</script>

<template>
  <section class="diagnosis-page" aria-labelledby="diagnosis-page-title" :aria-busy="busy">
    <header class="diagnosis-intro">
      <div>
        <p class="eyebrow">研究中心 · 确定性诊断</p>
        <h1 id="diagnosis-page-title">个股诊断</h1>
        <p>先看服务端动作与风险边界，再核对行情、趋势、量价、资金、行业和财务证据。</p>
      </div>
      <dl class="diagnosis-stamp" :class="{ 'is-stale': diagnosis?.analysis_pipeline?.quote_stale }" aria-label="诊断标的、数据时点与质量">
        <div><dt>诊断标的</dt><dd class="mono">{{ requestedCode || '尚未选择' }}</dd></div>
        <div><dt>关键时点</dt><dd class="mono">{{ dataDate ? formatBeijingTime(dataDate) : '—' }}</dd></div>
        <div><dt>依据状态</dt><dd><DataStatus :state="diagnosis ? quality : 'partial'" :label="diagnosis ? statusLabel : '等待诊断'" /></dd></div>
      </dl>
    </header>

    <DiagnosisSearch
      v-model:code="inputCode"
      :busy="busy"
      :error="inputError"
      :recent-codes="recentCodes"
      @submit="diagnose"
    />

    <div v-if="quoteError" class="diagnosis-local-alert" role="alert">
      <div><strong>快速报价局部失败</strong><span>{{ quoteError }}<template v-if="diagnosis">；正式诊断卡仍可使用。</template></span></div>
      <button v-if="!busy && requestedCode" type="button" class="secondary-button" @click="refresh">重试本次诊断</button>
    </div>

    <section v-if="diagnosisLoading && !diagnosis" class="panel diagnosis-loading" role="status" aria-live="polite">
      <div v-if="displayedQuote" class="diagnosis-quote-preview">
        <div><strong>{{ displayedQuote.name || requestedCode }}</strong><span class="mono">{{ displayedQuote.code || requestedCode }}</span></div>
        <strong class="mono" :class="quoteTone">{{ displayedQuote.price == null ? '现价缺失' : `${formatNumber(displayedQuote.price, 2)} 元` }}</strong>
        <span class="mono" :class="quoteTone">{{ displayedQuote.change_pct == null ? '涨跌幅缺失' : formatPercent(displayedQuote.change_pct) }}</span>
        <small>量比 {{ displayedQuote.volume_ratio == null ? '—' : formatNumber(displayedQuote.volume_ratio, 2) }} · 换手 {{ displayedQuote.turnover_rate == null ? '—' : `${formatNumber(displayedQuote.turnover_rate, 2)}%` }} · 成交额 {{ formatCompactMoney(displayedQuote.amount) }}</small>
      </div>
      <div class="diagnosis-loading__message"><span aria-hidden="true"></span><strong>正在生成服务端确定性诊断</strong><small>并行核对日 K、动量、量价、波动、结构位、行业、资金与财务；缺失项不会补 0。</small></div>
      <div class="diagnosis-loading__skeleton" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></div>
    </section>

    <div v-if="diagnosisError" class="diagnosis-local-alert is-error" role="alert">
      <div>
        <strong>{{ displayedQuote ? '正式诊断局部失败，快速行情仍保留' : '诊断服务暂不可用' }}</strong>
        <span>{{ diagnosisError }}；本次不输出新的交易动作。</span>
      </div>
      <button v-if="!busy && requestedCode" type="button" class="secondary-button" @click="refresh">重试</button>
    </div>

    <section v-if="!requested && !diagnosisLoading" class="panel diagnosis-empty" aria-labelledby="diagnosis-empty-title">
      <p class="eyebrow">WAITING FOR SYMBOL</p>
      <h2 id="diagnosis-empty-title">输入股票代码开始诊断</h2>
      <p>当前没有诊断数据。页面不会自动请求其他业务接口，也不会用示例股票、演示数字或历史值填充。</p>
      <ol>
        <li>快速报价只负责确认标的与当前行情。</li>
        <li>正式诊断完成后才展示动作、仓位和价格条件。</li>
        <li>证据不足、降级、陈旧与接口失败会分别标注。</li>
      </ol>
    </section>

    <template v-if="diagnosis">
      <DiagnosisDecision :card="diagnosis" />
      <DiagnosisEvidence :card="diagnosis" :quote="displayedQuote" />
      <DiagnosisDataHealth :card="diagnosis" />
    </template>

    <footer class="page-footer diagnosis-footer">
      <span>价格单位为元；涨跌幅与换手率为百分数；成交额与资金净额为元。</span>
      <span>风险、动作、仓位上限与价格条件均由服务端返回，前端不重算。</span>
    </footer>
  </section>
</template>

<style scoped>
.diagnosis-page { display: grid; min-width: 0; gap: 12px; }
.diagnosis-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.diagnosis-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.diagnosis-intro > div > p:last-child { max-width: 800px; margin: 7px 0 0; color: var(--text-2); }
.diagnosis-stamp { display: grid; min-width: 520px; grid-template-columns: minmax(110px, .8fr) minmax(210px, 1.25fr) minmax(150px, 1fr); gap: 12px; margin: 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.diagnosis-stamp.is-stale { border-color: var(--warning); }
.diagnosis-stamp div { min-width: 0; }
.diagnosis-stamp dt { color: var(--text-3); font-size: 12px; }
.diagnosis-stamp dd { margin: 2px 0 0; font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }
.diagnosis-stamp :deep(.data-status) { min-height: 26px; white-space: normal; }
.diagnosis-local-alert { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 10px 12px; border: 1px solid var(--warning); border-radius: var(--radius); background: color-mix(in srgb, var(--warning) 7%, var(--surface)); }
.diagnosis-local-alert.is-error { border-color: var(--danger); background: color-mix(in srgb, var(--danger) 7%, var(--surface)); }
.diagnosis-local-alert > div { display: grid; gap: 2px; }
.diagnosis-local-alert span { color: var(--text-2); font-size: 13px; }
.diagnosis-loading { min-height: 490px; overflow: hidden; }
.diagnosis-quote-preview { display: flex; min-height: 68px; flex-wrap: wrap; align-items: center; gap: 8px 18px; padding: 11px 14px; border-bottom: 1px solid var(--border); }
.diagnosis-quote-preview > div { display: flex; align-items: baseline; gap: 8px; }
.diagnosis-quote-preview > div span { color: var(--text-3); }
.diagnosis-quote-preview > strong { font-size: 20px; }
.diagnosis-quote-preview small { flex: 1 1 280px; color: var(--text-3); font-size: 12px; }
.diagnosis-loading__message { display: grid; grid-template-columns: 12px minmax(0, 1fr); gap: 2px 8px; padding: 14px; }
.diagnosis-loading__message > span { width: 10px; height: 10px; margin-top: 7px; border: 2px solid var(--accent); border-top-color: transparent; border-radius: 50%; animation: diagnosis-spin .8s linear infinite; }
.diagnosis-loading__message small { grid-column: 2; color: var(--text-3); }
.diagnosis-loading__skeleton { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 0 14px 14px; }
.diagnosis-loading__skeleton i { min-height: 82px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); animation: diagnosis-pulse 1.2s ease-in-out infinite alternate; }
.diagnosis-loading__skeleton i:first-child, .diagnosis-loading__skeleton i:last-child { grid-column: 1 / -1; }
.diagnosis-empty { display: grid; min-height: 360px; align-content: center; justify-items: center; padding: 28px; text-align: center; }
.diagnosis-empty h2 { margin: 0; font-size: 22px; }
.diagnosis-empty > p:not(.eyebrow) { max-width: 680px; margin: 7px 0 14px; color: var(--text-2); }
.diagnosis-empty ol { display: grid; gap: 6px; max-width: 620px; margin: 0; padding-left: 24px; color: var(--text-3); text-align: left; }
.diagnosis-footer { padding-top: 0; }
@keyframes diagnosis-spin { to { transform: rotate(360deg); } }
@keyframes diagnosis-pulse { from { opacity: .45; } to { opacity: .82; } }
@media (max-width: 1180px) {
  .diagnosis-intro { align-items: stretch; flex-direction: column; gap: 12px; }
  .diagnosis-stamp { min-width: 0; }
}
@media (max-width: 720px) {
  .diagnosis-page { gap: 8px; }
  .diagnosis-stamp { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-local-alert { align-items: stretch; flex-direction: column; }
  .diagnosis-local-alert button { width: 100%; }
  .diagnosis-loading__skeleton { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-loading__skeleton i:first-child, .diagnosis-loading__skeleton i:last-child { grid-column: auto; }
  .diagnosis-empty { min-height: 420px; padding: 22px 14px; }
}
@media (prefers-reduced-motion: reduce) {
  .diagnosis-loading__message > span, .diagnosis-loading__skeleton i { animation: none; }
}
</style>
