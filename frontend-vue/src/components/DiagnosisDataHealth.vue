<script setup lang="ts">
import { computed } from 'vue'
import type { DiagnosisResponse } from '../types/diagnosis'
import { uniqueText } from '../utils/diagnosisFormat'

const props = defineProps<{ card: DiagnosisResponse }>()

const missing = computed(() => uniqueText([props.card.missing, props.card.market_evidence_quality?.missing_fields, props.card.decision_market_evidence_quality?.missing_fields]))
const adviceState = computed(() => {
  const record = props.card.advice_record
  if (!record) return '建议留痕状态未返回'
  if (record.ok === false) return `建议留痕失败${record.error_code ? `：${record.error_code}` : ''}`
  if (record.blocked) return '建议已留痕，但新增风险被阻断'
  if (record.recommendation_created) return '正式推荐事件已创建（不是推送送达状态）'
  return '建议记录已提交，未创建正式推荐事件'
})
const modelState = computed(() => {
  const model = props.card.model_connection
  if (!model) return '模型状态未返回'
  if (model.used_for_diagnosis) return '模型参与解释；确定性动作仍以服务端规则为准'
  if (model.status === 'not_configured') return '模型未配置；本卡使用确定性规则'
  if (model.status === 'not_requested') return '本次未请求模型'
  return `模型状态：${model.status || '未标注'}`
})
</script>

<template>
  <section class="panel diagnosis-health" aria-labelledby="diagnosis-health-title">
    <header class="section-heading">
      <div><h2 id="diagnosis-health-title">数据质量与审计</h2></div>
      <span>{{ missing.length ? `${missing.length} 条缺口或降级说明` : '接口未声明数据缺口' }}</span>
    </header>
    <details class="diagnosis-health__audit">
      <summary>展开规则、模型、留痕与全部缺口</summary>
      <div class="diagnosis-health__grid">
        <article><strong>数据时点</strong><span>{{ card.analysis_pipeline?.data_as_of || card.decision_generated_at || '未返回' }}<template v-if="card.analysis_pipeline?.quote_stale"> · 已标记陈旧</template></span></article>
        <article><strong>规则权威</strong><span>{{ card.deterministic_signal?.rule_version || card.analysis_pipeline?.rule_version || '版本未返回' }}；浏览器不重算动作、仓位或价位。</span></article>
        <article><strong>模型边界</strong><span>{{ modelState }}</span></article>
        <article><strong>建议留痕</strong><span>{{ adviceState }}</span></article>
      </div>
      <section v-if="missing.length" class="diagnosis-health__details" aria-label="全部缺失与降级说明">
        <strong>全部缺失与降级说明</strong>
        <ul><li v-for="item in missing" :key="item">{{ item }}</li></ul>
      </section>
      <footer>
        <span>本页仅提供决策支持和本地模拟语义，不连接券商真实下单。</span>
        <span>服务端文本按普通 Vue 文本节点展示。</span>
      </footer>
    </details>
  </section>
</template>

<style scoped>
.diagnosis-health { min-width: 0; overflow: hidden; }
.section-heading > span { color: var(--text-3); font-size: 12px; }
.diagnosis-health__audit > summary { min-height: 44px; padding: 11px 14px; border-top: 1px solid var(--border); color: var(--text-2); font-size: 13px; font-weight: 700; cursor: pointer; }
.diagnosis-health__grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.diagnosis-health__grid article { display: grid; min-width: 0; gap: 3px; padding: 12px 14px; border-right: 1px solid var(--border); }
.diagnosis-health__grid article:last-child { border-right: 0; }
.diagnosis-health__grid strong { font-size: 13px; }
.diagnosis-health__grid span { color: var(--text-3); font-size: 12px; }
.diagnosis-health__details { padding: 10px 14px 0; border-top: 1px solid var(--border); color: var(--warning); font-size: 12px; }
.diagnosis-health__details ul { margin: 6px 0 0; padding: 0 16px 12px 22px; color: var(--text-3); }
.diagnosis-health footer { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 5px 16px; padding: 10px 14px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 12px; }
@media (max-width: 1000px) {
  .diagnosis-health__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .diagnosis-health__grid article:nth-child(2) { border-right: 0; }
  .diagnosis-health__grid article:nth-child(-n + 2) { border-bottom: 1px solid var(--border); }
}
@media (max-width: 620px) {
  .diagnosis-health__grid { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-health__grid article { border-right: 0; border-bottom: 1px solid var(--border); }
  .diagnosis-health__grid article:last-child { border-bottom: 0; }
}
</style>
