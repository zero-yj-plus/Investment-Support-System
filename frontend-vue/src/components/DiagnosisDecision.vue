<script setup lang="ts">
import { computed } from 'vue'
import type { DiagnosisResponse } from '../types/diagnosis'
import { finiteNumber, formatNumber, formatPercent } from '../utils/diagnosisFormat'

const props = defineProps<{ card: DiagnosisResponse }>()

const action = computed(() => props.card.action || '动作未返回')
const actionTone = computed(() => {
  if (['买入', '加仓'].includes(action.value)) return 'is-entry'
  if (['减仓', '止损', '清仓', '回避'].includes(action.value)) return 'is-defensive'
  return 'is-neutral'
})
const risk = computed(() => props.card.risk_decision)
const riskMeta = computed(() => ({
  allow: { label: '风险闸未额外阻断', tone: 'is-allow' },
  reduce: { label: '风险闸已收紧敞口', tone: 'is-reduce' },
  veto: { label: '风险闸禁止新增敞口', tone: 'is-veto' },
}[risk.value?.decision || ''] || { label: '风险闸状态未返回', tone: 'is-missing' }))
const cap = computed(() => finiteNumber(props.card.position_cap_pct ?? risk.value?.position_cap_pct))
const capText = computed(() => cap.value == null ? '新增仓位上限未提供' : `新增仓位上限 ${formatNumber(cap.value, 2)}%`)
const plan = computed(() => props.card.trade_plan)
const quote = computed(() => props.card.market_quote)
const entryText = computed(() => props.card.entry_zone || plan.value?.entry_zone || '未提供')
const stopText = computed(() => props.card.stop || plan.value?.stop || '未提供')
const structuralAction = computed(() => props.card.deep_signal?.action || props.card.deterministic_signal?.action || props.card.signal_action || '未返回')
const confidence = computed(() => finiteNumber(props.card.confidence ?? risk.value?.effective_confidence))
const coverage = computed(() => finiteNumber(props.card.deterministic_signal?.coverage))
const marketQuality = computed(() => props.card.decision_market_evidence_quality || props.card.market_evidence_quality)
const evidenceStatus = computed(() => {
  if (props.card.deterministic_signal?.complete === true) return '确定性依据完整'
  if (props.card.deterministic_signal?.complete === false) return '关键行情证据不完整'
  return '依据完整度未返回'
})
const insight = computed(() => {
  const reasons = props.card.deep_signal?.reasons || props.card.technical_analysis?.reasons || []
  return [props.card.technical_analysis?.structure, ...reasons.slice(0, 2)].filter(Boolean).join(' · ') || '服务端未返回结构摘要'
})

function prepareExplanation() {
  if (!props.card.code) return
  sessionStorage.setItem('hermes.chat.code', props.card.code)
  sessionStorage.setItem('hermes.chat.prompt', '请解释这张确定性诊断卡：先说明当前最应关注什么，再解释趋势、量价、风险闸和失效条件。不得改写冻结动作或另造价位。')
}
</script>

<template>
  <section class="panel diagnosis-decision" aria-labelledby="diagnosis-decision-title">
    <header class="diagnosis-decision__header">
      <div class="diagnosis-identity">
        <h2 id="diagnosis-decision-title">{{ card.name || '名称缺失' }} <span class="mono">{{ card.code || '代码缺失' }}</span></h2>
        <div class="diagnosis-quote-line">
          <strong class="mono">{{ quote?.price == null ? '现价缺失' : `${formatNumber(quote.price, 2)} 元` }}</strong>
          <span class="mono" :class="quote?.change_pct == null ? 'is-flat' : quote.change_pct > 0 ? 'is-up' : quote.change_pct < 0 ? 'is-down' : 'is-flat'">{{ quote?.change_pct == null ? '涨跌幅缺失' : formatPercent(quote.change_pct) }}</span>
          <span>{{ quote?.stale ? '最近交易日行情' : '诊断行情快照' }}<template v-if="quote?.as_of"> · {{ quote.as_of }}</template></span>
        </div>
      </div>
      <div class="diagnosis-action" :class="actionTone">
        <span>服务端最终动作</span><strong>{{ action }}</strong><small>{{ capText }}</small>
      </div>
    </header>

    <section class="diagnosis-signal-strip" aria-label="信号、治理动作与依据完整度">
      <div class="diagnosis-signal-chain">
        <span>结构信号</span><strong>{{ structuralAction }}</strong><i aria-hidden="true">→</i><span>服务端治理后</span><strong>{{ action }}</strong>
      </div>
      <div class="diagnosis-signal-badges">
        <span>{{ confidence == null ? '置信度未返回' : `置信 ${formatNumber(confidence * 100, 0)}%` }}</span>
        <span>{{ coverage == null ? '规则覆盖未返回' : `规则覆盖 ${formatNumber(coverage * 100, 0)}%` }}</span>
        <span :class="{ 'is-warning': card.deterministic_signal?.complete === false }">{{ evidenceStatus }}</span>
        <span>{{ marketQuality?.captured ?? '—' }}/{{ marketQuality?.requested ?? '—' }} 项行情证据</span>
      </div>
    </section>

    <p class="diagnosis-insight">{{ insight }}</p>

    <div class="diagnosis-command">
      <article>
        <span>现在</span>
        <strong>{{ action }}；{{ capText }}</strong>
      </article>
      <article>
        <span>等待触发</span>
        <strong>{{ plan?.entry_trigger || '执行触发条件未返回' }}</strong>
      </article>
      <article>
        <span>结论失效</span>
        <strong>{{ stopText }}</strong>
      </article>
    </div>

    <div class="diagnosis-guardrails">
      <section class="diagnosis-price-map" aria-label="服务端结构价位">
        <article><span>支撑 / 关注区</span><strong class="mono">{{ plan?.support == null ? '—' : `${formatNumber(plan.support, 2)} 元` }}</strong><small>{{ entryText }}</small></article>
        <article class="is-current"><span>诊断行情价</span><strong class="mono">{{ quote?.price == null ? '—' : `${formatNumber(quote.price, 2)} 元` }}</strong><small>{{ quote?.as_of || '数据日期未返回' }}</small></article>
        <article><span>结构阻力</span><strong class="mono">{{ plan?.resistance == null ? '—' : `${formatNumber(plan.resistance, 2)} 元` }}</strong><small>{{ card.technical_analysis?.levels?.resistance_basis || '依据未返回' }}</small></article>
      </section>

      <article class="diagnosis-risk" :class="riskMeta.tone">
        <header><span>风险边界</span><strong>{{ riskMeta.label }}</strong></header>
        <ul v-if="risk?.reasons?.length"><li v-for="reason in risk.reasons" :key="reason">{{ reason }}</li></ul>
        <p v-else>仅表示当前动作未被额外阻断，不代表低风险或可以买入。</p>
      </article>
    </div>

    <footer class="diagnosis-decision__footer">
      <details>
        <summary>查看完整诊断说明与规则详情</summary>
        <p>{{ card.summary || '服务端未返回完整诊断说明' }}</p>
        <small class="mono">{{ card.decision_authority || '服务端规则 + 风险闸' }} · {{ risk?.rule_version || '规则版本未返回' }}<template v-if="risk?.input_snapshot_hash"> · input {{ risk.input_snapshot_hash.slice(0, 12) }}</template></small>
      </details>
      <a href="#/research?tab=ask" @click="prepareExplanation">让 AI 解释冻结诊断卡</a>
    </footer>
  </section>
</template>

<style scoped>
.diagnosis-decision { overflow: hidden; border-color: color-mix(in srgb, var(--accent) 42%, var(--border)); }
.diagnosis-decision__header { display: flex; min-height: 92px; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 16px; border-bottom: 1px solid var(--border); }
.diagnosis-identity { min-width: 0; }
.diagnosis-identity h2 { margin: 0; font-size: 22px; }
.diagnosis-identity h2 span { margin-left: 6px; color: var(--text-3); font-size: 15px; font-weight: 550; }
.diagnosis-identity > p:last-child { margin: 4px 0 0; color: var(--text-2); }
.diagnosis-action { display: grid; min-width: 150px; min-height: 74px; place-items: center; align-content: center; padding: 7px 14px; border: 1px solid currentColor; border-radius: var(--radius); text-align: center; }
.diagnosis-action span, .diagnosis-action small { font-size: 12px; }
.diagnosis-action strong { font-size: 24px; line-height: 1.25; }
.diagnosis-action.is-entry { color: var(--accent); background: var(--accent-dim); }
.diagnosis-action.is-defensive { color: var(--danger); background: color-mix(in srgb, var(--danger) 8%, var(--surface)); }
.diagnosis-action.is-neutral { color: var(--info); background: color-mix(in srgb, var(--info) 8%, var(--surface)); }
.diagnosis-command { display: grid; grid-template-columns: 1.05fr 1.2fr .85fr; }
.diagnosis-command article { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 7px; min-width: 0; padding: 13px 14px; border-right: 1px solid var(--border); }
.diagnosis-command article:last-child { border-right: 0; }
.diagnosis-command article > span { color: var(--text-3); font: 500 12px/1.5 "SFMono-Regular", Consolas, monospace; }
.diagnosis-command p { margin: 0 0 3px; color: var(--text-3); font-size: 12px; font-weight: 700; }
.diagnosis-command strong { font-size: 15px; line-height: 1.55; }
.diagnosis-guardrails { display: grid; grid-template-columns: minmax(280px, .72fr) minmax(0, 1.28fr); border-top: 1px solid var(--border); }
.diagnosis-risk { min-width: 0; padding: 13px 14px; border-right: 1px solid var(--border); border-left: 3px solid currentColor; }
.diagnosis-risk.is-allow { color: var(--info); }
.diagnosis-risk.is-reduce { color: var(--warning); }
.diagnosis-risk.is-veto { color: var(--danger); }
.diagnosis-risk.is-missing { color: var(--text-2); }
.diagnosis-risk header { display: grid; gap: 2px; }
.diagnosis-risk header span { color: var(--text-3); font-size: 12px; }
.diagnosis-risk ul { margin: 8px 0; padding-left: 20px; color: var(--text-2); font-size: 13px; }
.diagnosis-risk p { margin: 8px 0; color: var(--text-2); font-size: 13px; }
.diagnosis-risk small { color: var(--text-3); font-size: 11px; overflow-wrap: anywhere; }
.diagnosis-levels { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.diagnosis-levels article { display: grid; min-width: 0; align-content: start; gap: 3px; padding: 12px 14px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); background: var(--surface-2); }
.diagnosis-levels article:nth-child(2n) { border-right: 0; }
.diagnosis-levels article:nth-last-child(-n + 2) { border-bottom: 0; }
.diagnosis-levels span, .diagnosis-levels small { color: var(--text-3); font-size: 12px; }
.diagnosis-levels strong { font-size: 17px; overflow-wrap: anywhere; }
.diagnosis-decision__footer { display: flex; min-height: 52px; align-items: center; justify-content: space-between; gap: 12px; padding: 7px 14px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 12px; }
.diagnosis-decision__footer a { display: inline-flex; min-height: 44px; align-items: center; padding: 7px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); color: var(--text); font-weight: 700; text-decoration: none; }
.diagnosis-decision__footer a:hover { border-color: var(--border-strong); background: var(--surface-2); }
.diagnosis-decision__header { min-height: 84px; padding: 12px 14px; }
.diagnosis-identity > p:last-child { margin: 0; }
.diagnosis-quote-line { display: flex; flex-wrap: wrap; align-items: baseline; gap: 5px 12px; margin-top: 5px; }
.diagnosis-quote-line > strong { font-size: 20px; }
.diagnosis-quote-line > span:last-child { color: var(--text-3); font-size: 12px; }
.diagnosis-action { min-width: 184px; min-height: 68px; padding: 6px 12px; }
.diagnosis-signal-strip { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 14px; padding: 9px 14px; border-bottom: 1px solid var(--border); background: var(--surface-2); }
.diagnosis-signal-chain, .diagnosis-signal-badges { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.diagnosis-signal-chain span { color: var(--text-3); font-size: 12px; }
.diagnosis-signal-chain strong { font-size: 14px; }
.diagnosis-signal-chain i { color: var(--text-3); font-style: normal; }
.diagnosis-signal-badges span { padding: 3px 6px; border: 1px solid var(--border); border-radius: 4px; color: var(--text-2); font-size: 11px; }
.diagnosis-signal-badges .is-warning { border-color: var(--warning); color: var(--warning); }
.diagnosis-insight { margin: 0; padding: 8px 14px; border-bottom: 1px solid var(--border); color: var(--text-2); font-size: 13px; font-weight: 650; }
.diagnosis-command { grid-template-columns: .78fr 1.2fr 1.02fr; }
.diagnosis-command article { display: grid; grid-template-columns: minmax(0, 1fr); align-content: start; gap: 4px; padding: 11px 14px; }
.diagnosis-command article > span { font-family: inherit; font-weight: 700; }
.diagnosis-command strong { font-size: 14px; line-height: 1.5; }
.diagnosis-guardrails { grid-template-columns: minmax(0, 1.45fr) minmax(240px, .55fr); }
.diagnosis-price-map { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); min-width: 0; }
.diagnosis-price-map article { display: grid; min-width: 0; align-content: start; gap: 3px; padding: 10px 12px; border-right: 1px solid var(--border); background: var(--surface-2); }
.diagnosis-price-map article.is-current { background: color-mix(in srgb, var(--accent) 7%, var(--surface-2)); }
.diagnosis-price-map span, .diagnosis-price-map small { color: var(--text-3); font-size: 11px; }
.diagnosis-price-map strong { font-size: 16px; overflow-wrap: anywhere; }
.diagnosis-risk { padding: 10px 12px; border-right: 0; }
.diagnosis-risk header span { font-size: 11px; }
.diagnosis-risk ul { margin: 6px 0 0; padding-left: 18px; font-size: 12px; }
.diagnosis-risk p { margin: 6px 0 0; font-size: 12px; }
.diagnosis-decision__footer { align-items: flex-start; padding: 7px 12px; }
.diagnosis-decision__footer details { min-width: 0; flex: 1; }
.diagnosis-decision__footer summary { display: inline-flex; min-height: 36px; align-items: center; color: var(--text-2); font-weight: 700; cursor: pointer; }
.diagnosis-decision__footer details p { max-width: 900px; margin: 6px 0; color: var(--text-2); line-height: 1.6; }
.diagnosis-decision__footer details small { display: block; overflow-wrap: anywhere; }
.diagnosis-decision__footer a { flex: 0 0 auto; }
@media (max-width: 980px) {
  .diagnosis-command { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-command article { border-right: 0; border-bottom: 1px solid var(--border); }
  .diagnosis-command article:last-child { border-bottom: 0; }
  .diagnosis-guardrails { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-risk { border-top: 1px solid var(--border); border-right: 0; border-bottom: 0; }
}
@media (max-width: 620px) {
  .diagnosis-decision__header { align-items: stretch; flex-direction: column; padding: 12px; }
  .diagnosis-action { min-width: 0; }
  .diagnosis-signal-strip { align-items: flex-start; flex-direction: column; padding: 9px 12px; }
  .diagnosis-price-map { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-price-map article { border-right: 0; border-bottom: 1px solid var(--border); }
  .diagnosis-price-map article:last-child { border-bottom: 0; }
  .diagnosis-levels { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-levels article { border-right: 0; }
  .diagnosis-levels article:nth-last-child(2) { border-bottom: 1px solid var(--border); }
  .diagnosis-decision__footer { align-items: stretch; flex-direction: column; }
  .diagnosis-decision__footer a { justify-content: center; }
}
</style>
