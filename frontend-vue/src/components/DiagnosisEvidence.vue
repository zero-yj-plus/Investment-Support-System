<script setup lang="ts">
import { computed } from 'vue'
import type { DiagnosisEvidencePoint, DiagnosisQuote, DiagnosisResponse } from '../types/diagnosis'
import { evidenceFieldLabel, formatEvidenceValue } from '../utils/diagnosisFormat'

const props = defineProps<{ card: DiagnosisResponse; quote?: DiagnosisQuote | null }>()

const stages = computed(() => Array.isArray(props.card.analysis_pipeline?.stages) ? props.card.analysis_pipeline!.stages! : [])
const evidence = computed(() => Array.isArray(props.card.evidence) ? props.card.evidence : [])
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
</script>

<template>
  <section class="diagnosis-evidence-grid" aria-label="诊断结构化证据">
    <section class="panel diagnosis-analysis" aria-labelledby="diagnosis-analysis-title">
      <header class="section-heading">
        <div><h2 id="diagnosis-analysis-title">六维诊断依据</h2><small>趋势、动量、量价、资金、行业与财务</small></div>
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

    <section class="panel diagnosis-market" aria-labelledby="diagnosis-market-title">
      <header class="section-heading">
        <div><h2 id="diagnosis-market-title">当前快速行情</h2><small><template v-if="quote?.source">{{ quote.source }}</template><template v-if="quote?.as_of"> · {{ quote.as_of }}</template></small></div>
        <span>仅供核对 · 未绑定证据 ID · {{ quotePoints.length }}/8 项有值</span>
      </header>
      <section v-if="quotePoints.length" class="diagnosis-quote-fallback" aria-label="当前快速行情字段">
        <div class="diagnosis-market__grid">
          <article v-for="point in quotePoints" :key="`quote-${point.field}`" class="is-unbound">
            <div><span>{{ evidenceFieldLabel(point.field) }}</span></div>
            <strong class="mono">{{ formatEvidenceValue(point) }}</strong>
          </article>
        </div>
      </section>
      <div v-else class="diagnosis-evidence-empty"><strong>快速行情暂不可用</strong><span>页面不会用示例数字或历史值补位。</span></div>
    </section>

    <section class="panel diagnosis-chain" aria-labelledby="diagnosis-chain-title">
      <header class="section-heading">
        <div><h2 id="diagnosis-chain-title">可追溯结论证据</h2></div>
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
.diagnosis-evidence-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 12px; min-width: 0; }
.diagnosis-market, .diagnosis-analysis, .diagnosis-chain { min-width: 0; overflow: hidden; }
.diagnosis-analysis { order: 1; }
.diagnosis-market { order: 2; }
.diagnosis-chain { order: 3; }
.section-heading > div > small { display: block; margin-top: 2px; color: var(--text-3); font-size: 11px; }
.section-heading > span { color: var(--text-3); font-size: 12px; text-align: right; }
.diagnosis-market__grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; padding: 10px; }
.diagnosis-market__grid article { min-width: 0; padding: 8px 10px; border: 1px solid var(--border); border-left: 3px solid currentColor; border-radius: var(--radius-sm); background: var(--surface-2); }
.diagnosis-market__grid article.is-unbound { color: var(--text-2); border-left-color: var(--border-strong); }
.diagnosis-market__grid article > div { display: flex; justify-content: space-between; gap: 8px; }
.diagnosis-market__grid span { color: var(--text-3); font-size: 12px; }
.diagnosis-market__grid em { font-size: 11px; font-style: normal; }
.diagnosis-market__grid strong { display: block; margin: 3px 0 1px; color: var(--text); font-size: 17px; }
.diagnosis-market__grid small { display: block; color: var(--text-3); font-size: 11px; overflow-wrap: anywhere; }
.diagnosis-stage-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; padding: 10px; }
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
.diagnosis-evidence-empty { display: grid; min-height: 72px; align-content: center; gap: 3px; padding: 14px 20px; color: var(--text-3); text-align: center; }
.diagnosis-evidence-empty strong { color: var(--text-2); }
@media (max-width: 1120px) {
  .diagnosis-evidence-grid { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-chain { grid-column: auto; }
  .diagnosis-stage-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 660px) {
  .section-heading { align-items: flex-start; }
  .section-heading > span { text-align: left; }
  .diagnosis-market__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .diagnosis-stage-list, .diagnosis-chain__list { grid-template-columns: minmax(0, 1fr); }
}
</style>
