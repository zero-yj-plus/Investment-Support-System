<script setup lang="ts">
import { computed } from 'vue'
import type { DiagnosisEvidencePoint, DiagnosisQuote, DiagnosisResponse } from '../types/diagnosis'
import { evidenceFieldLabel, evidenceQualityLabel, evidenceUnavailableReason, formatEvidenceValue, qualityCoverage } from '../utils/diagnosisFormat'

const props = defineProps<{ card: DiagnosisResponse; quote?: DiagnosisQuote | null }>()

const hasDecisionSnapshot = computed(() => Array.isArray(props.card.decision_market_evidence))
const decisionPoints = computed(() => Array.isArray(props.card.decision_market_evidence) ? props.card.decision_market_evidence : [])
const currentPoints = computed(() => Array.isArray(props.card.market_evidence) ? props.card.market_evidence : [])
const stages = computed(() => Array.isArray(props.card.analysis_pipeline?.stages) ? props.card.analysis_pipeline!.stages! : [])
const evidence = computed(() => Array.isArray(props.card.evidence) ? props.card.evidence : [])
const sameSnapshot = computed(() => {
  const ids = (rows: DiagnosisEvidencePoint[]) => rows.map((row) => row.evidence_id).filter(Boolean).sort().join('|')
  return Boolean(ids(currentPoints.value)) && ids(currentPoints.value) === ids(decisionPoints.value)
})
const primaryPoints = computed(() => hasDecisionSnapshot.value ? decisionPoints.value : currentPoints.value)
const primaryQuality = computed(() => hasDecisionSnapshot.value ? props.card.decision_market_evidence_quality : props.card.market_evidence_quality)
const missingFields = computed(() => primaryQuality.value?.missing_fields || [])
const emptyReason = computed(() => evidenceUnavailableReason(primaryQuality.value?.reason))
const quotePoints = computed<DiagnosisEvidencePoint[]>(() => {
  const quote = props.quote
  if (!quote) return []
  const definitions: Array<[string, keyof DiagnosisQuote, string]> = [
    ['last_price', 'price', 'CNY'], ['prev_close', 'prev_close', 'CNY'],
    ['change_pct', 'change_pct', 'percent'], ['amount', 'amount', 'CNY'],
    ['turnover_rate', 'turnover_rate', 'percent'], ['volume_ratio', 'volume_ratio', 'ratio'],
    ['pe', 'pe', 'ratio'], ['pb', 'pb', 'ratio'],
  ]
  return definitions.flatMap(([field, key, unit]) => quote[key] == null ? [] : [{ field, value: quote[key] as number, unit }])
})
const directionTone = (direction: string | undefined) => direction === '利多' ? 'is-positive' : direction === '利空' ? 'is-negative' : 'is-neutral'
const pointTone = (point: DiagnosisEvidencePoint) => point.quality_status === 'ok' ? 'is-ok' : point.quality_status === 'conflict' ? 'is-conflict' : point.quality_status === 'missing' ? 'is-missing' : 'is-degraded'
</script>

<template>
  <section class="diagnosis-evidence-grid" aria-label="诊断结构化证据">
    <section class="panel diagnosis-market" aria-labelledby="diagnosis-market-title">
      <header class="section-heading">
        <div><p class="eyebrow">PRICE EVIDENCE</p><h2 id="diagnosis-market-title">结论绑定的行情证据</h2></div>
        <span>{{ qualityCoverage(primaryQuality) }} · {{ evidenceQualityLabel(primaryQuality?.status) }}</span>
      </header>
      <div v-if="primaryPoints.length" class="diagnosis-market__grid">
        <article v-for="point in primaryPoints" :key="`${point.field}-${point.evidence_id || 'missing'}`" :class="pointTone(point)">
          <div><span>{{ evidenceFieldLabel(point.field) }}</span><em>{{ evidenceQualityLabel(point.quality_status) }}</em></div>
          <strong class="mono">{{ formatEvidenceValue(point) }}</strong>
          <small>{{ point.source_id || point.source || '来源未标注' }}<template v-if="point.trading_day"> · {{ point.trading_day }}</template></small>
        </article>
      </div>
      <div v-else class="diagnosis-evidence-empty is-unavailable" role="status">
        <strong>正式行情证据采集未完成</strong>
        <span>{{ emptyReason }} 快速行情只作当前值核对，不会被写成结论证据。</span>
      </div>
      <p v-if="missingFields.length" class="diagnosis-market__missing">
        {{ primaryPoints.length ? '缺失字段' : '未绑定正式证据' }}：{{ missingFields.map(evidenceFieldLabel).join('、') }}
      </p>
      <details v-if="hasDecisionSnapshot && !sameSnapshot && currentPoints.length" class="diagnosis-current-snapshot">
        <summary>查看当前字段级证据（未参与已冻结结论）</summary>
        <div class="diagnosis-market__grid">
          <article v-for="point in currentPoints" :key="`current-${point.field}-${point.evidence_id || 'missing'}`" :class="pointTone(point)">
            <div><span>{{ evidenceFieldLabel(point.field) }}</span><em>{{ evidenceQualityLabel(point.quality_status) }}</em></div>
            <strong class="mono">{{ formatEvidenceValue(point) }}</strong>
            <small>{{ point.source_id || point.source || '来源未标注' }}</small>
          </article>
        </div>
      </details>
      <section v-if="!primaryPoints.length && !currentPoints.length && quotePoints.length" class="diagnosis-quote-fallback" aria-labelledby="diagnosis-quote-fallback-title">
        <header>
          <div><strong id="diagnosis-quote-fallback-title">当前快速行情</strong><span>仅供核对 · 未绑定证据 ID</span></div>
          <small>{{ quotePoints.length }}/8 项有值</small>
        </header>
        <div class="diagnosis-market__grid">
          <article v-for="point in quotePoints" :key="`quote-${point.field}`" class="is-unbound">
            <div><span>{{ evidenceFieldLabel(point.field) }}</span><em>未绑定</em></div>
            <strong class="mono">{{ formatEvidenceValue(point) }}</strong>
            <small>{{ quote?.source || '快速行情源' }}<template v-if="quote?.as_of"> · {{ quote.as_of }}</template></small>
          </article>
        </div>
      </section>
    </section>

    <section class="panel diagnosis-analysis" aria-labelledby="diagnosis-analysis-title">
      <header class="section-heading">
        <div><p class="eyebrow">ANALYSIS PIPELINE</p><h2 id="diagnosis-analysis-title">多维分析完成度</h2></div>
        <span class="mono">{{ card.analysis_pipeline?.completed ?? '—' }}/{{ card.analysis_pipeline?.total ?? '—' }} 维 · {{ card.analysis_pipeline?.elapsed_ms == null ? '耗时未返回' : `${(card.analysis_pipeline.elapsed_ms / 1000).toFixed(2)} 秒` }}</span>
      </header>
      <div v-if="stages.length" class="diagnosis-stage-list">
        <article v-for="stage in stages" :key="stage.id || stage.label">
          <header><strong>{{ stage.label || stage.id || '分析维度' }}</strong><span :class="`is-${stage.status || 'missing'}`">{{ stage.status === 'complete' ? '已分析' : stage.status === 'partial' ? '部分覆盖' : '缺失' }}</span></header>
          <p>{{ stage.summary || '该维度未返回摘要' }}</p>
          <small>{{ stage.source || '来源未标注' }}<template v-if="stage.data_date"> · {{ stage.data_date }}</template><template v-if="stage.score != null"> · 规则分 {{ stage.score > 0 ? '+' : '' }}{{ stage.score }}</template></small>
        </article>
      </div>
      <div v-else class="diagnosis-evidence-empty"><strong>分析维度未返回</strong><span>stages 字段缺失或为空，不能解释为全部分析完成。</span></div>
    </section>

    <section class="panel diagnosis-chain" aria-labelledby="diagnosis-chain-title">
      <header class="section-heading">
        <div><p class="eyebrow">TRACEABLE FINDINGS</p><h2 id="diagnosis-chain-title">可追溯结论证据</h2></div>
        <span>{{ evidence.length }} 条服务端证据</span>
      </header>
      <div v-if="evidence.length" class="diagnosis-chain__list">
        <article v-for="(item, index) in evidence" :key="`${item.evidence_ref || item.finding}-${index}`">
          <span class="diagnosis-direction" :class="directionTone(item.direction)">{{ item.direction || '中性' }}</span>
          <div><strong>{{ item.finding || '证据描述缺失' }}</strong><small>{{ item.source || '来源未标注' }}<template v-if="item.rule_version"> · {{ item.rule_version }}</template></small></div>
        </article>
      </div>
      <div v-else class="diagnosis-evidence-empty"><strong>证据链真实空数组</strong><span>服务端没有返回可追溯 finding；页面不生成替代论据。</span></div>
    </section>
  </section>
</template>

<style scoped>
.diagnosis-evidence-grid { display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(360px, .92fr); gap: 12px; min-width: 0; }
.diagnosis-market, .diagnosis-analysis, .diagnosis-chain { min-width: 0; overflow: hidden; }
.section-heading > span { color: var(--text-3); font-size: 12px; text-align: right; }
.diagnosis-market__grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; padding: 10px; }
.diagnosis-market__grid article { min-width: 0; padding: 9px 10px; border: 1px solid var(--border); border-left: 3px solid currentColor; border-radius: var(--radius-sm); background: var(--surface-2); }
.diagnosis-market__grid article.is-ok { color: var(--info); }
.diagnosis-market__grid article.is-degraded { color: var(--warning); }
.diagnosis-market__grid article.is-conflict, .diagnosis-market__grid article.is-missing { color: var(--danger); }
.diagnosis-market__grid article.is-unbound { color: var(--text-2); border-left-color: var(--border-strong); }
.diagnosis-market__grid article > div { display: flex; justify-content: space-between; gap: 8px; }
.diagnosis-market__grid span { color: var(--text-3); font-size: 12px; }
.diagnosis-market__grid em { font-size: 11px; font-style: normal; }
.diagnosis-market__grid strong { display: block; margin: 3px 0 1px; color: var(--text); font-size: 18px; }
.diagnosis-market__grid small { display: block; color: var(--text-3); font-size: 11px; overflow-wrap: anywhere; }
.diagnosis-market__missing { margin: 0; padding: 9px 12px; border-top: 1px solid var(--border); color: var(--warning); font-size: 12px; }
.diagnosis-evidence-empty.is-unavailable { min-height: 112px; padding-bottom: 14px; color: var(--text-2); background: color-mix(in srgb, var(--warning) 6%, var(--surface)); }
.diagnosis-evidence-empty.is-unavailable strong { color: var(--warning); }
.diagnosis-quote-fallback { border-top: 1px solid var(--border); }
.diagnosis-quote-fallback > header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 12px 0; }
.diagnosis-quote-fallback > header > div { display: grid; gap: 2px; }
.diagnosis-quote-fallback > header span, .diagnosis-quote-fallback > header small { color: var(--text-3); font-size: 12px; }
.diagnosis-current-snapshot summary { min-height: 44px; padding: 11px 12px; border-top: 1px solid var(--border); color: var(--text-2); font-size: 13px; cursor: pointer; }
.diagnosis-stage-list { display: grid; gap: 8px; padding: 10px; }
.diagnosis-stage-list article { min-width: 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.diagnosis-stage-list header { display: flex; justify-content: space-between; gap: 8px; }
.diagnosis-stage-list header > span { font-size: 12px; }
.diagnosis-stage-list .is-complete { color: var(--info); }
.diagnosis-stage-list .is-partial { color: var(--warning); }
.diagnosis-stage-list .is-missing { color: var(--danger); }
.diagnosis-stage-list p { margin: 5px 0 3px; color: var(--text-2); font-size: 13px; }
.diagnosis-stage-list small { color: var(--text-3); font-size: 11px; }
.diagnosis-chain { grid-column: 1 / -1; }
.diagnosis-chain__list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; padding: 10px; }
.diagnosis-chain__list article { display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: start; gap: 9px; min-width: 0; padding: 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.diagnosis-chain__list strong { display: block; font-size: 14px; }
.diagnosis-chain__list small { display: block; margin-top: 3px; color: var(--text-3); font-size: 11px; }
.diagnosis-direction { min-width: 48px; padding: 2px 5px; border: 1px solid currentColor; border-radius: 4px; font-size: 12px; text-align: center; }
.diagnosis-direction.is-positive { color: var(--up); }
.diagnosis-direction.is-negative { color: var(--down); }
.diagnosis-direction.is-neutral { color: var(--text-2); }
.diagnosis-evidence-empty { display: grid; min-height: 150px; align-content: center; gap: 3px; padding: 20px; color: var(--text-3); text-align: center; }
.diagnosis-evidence-empty strong { color: var(--text-2); }
@media (max-width: 1120px) {
  .diagnosis-evidence-grid { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-chain { grid-column: auto; }
}
@media (max-width: 660px) {
  .section-heading { align-items: flex-start; }
  .section-heading > span { text-align: left; }
  .diagnosis-market__grid, .diagnosis-chain__list { grid-template-columns: minmax(0, 1fr); }
}
</style>
