<script setup lang="ts">
import { computed } from 'vue'
import type { DiagnosisResponse } from '../types/diagnosis'
import { finiteNumber, formatNumber } from '../utils/diagnosisFormat'

const props = defineProps<{ card: DiagnosisResponse }>()

const action = computed(() => props.card.action || '动作未返回')
const actionTone = computed(() => {
  if (['买入', '加仓'].includes(action.value)) return 'is-entry'
  if (['减仓', '止损', '清仓', '回避'].includes(action.value)) return 'is-defensive'
  return 'is-neutral'
})
const risk = computed(() => props.card.risk_decision)
const riskMeta = computed(() => ({
  allow: { label: '风险闸通过', tone: 'is-allow' },
  reduce: { label: '风险闸缩减敞口', tone: 'is-reduce' },
  veto: { label: '风险闸禁止新增敞口', tone: 'is-veto' },
}[risk.value?.decision || ''] || { label: '风险闸状态未返回', tone: 'is-missing' }))
const isEntrySignal = computed(() => ['买入', '加仓'].includes(props.card.signal_action || props.card.action || ''))
const cap = computed(() => finiteNumber(props.card.position_cap_pct ?? risk.value?.position_cap_pct))
const plan = computed(() => props.card.trade_plan)
const entryText = computed(() => props.card.entry_zone || plan.value?.entry_zone || '未提供')
const stopText = computed(() => props.card.stop || plan.value?.stop || '未提供')

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
        <p class="eyebrow">SERVER DECISION</p>
        <h2 id="diagnosis-decision-title">{{ card.name || '名称缺失' }} <span class="mono">{{ card.code || '代码缺失' }}</span></h2>
        <p>{{ card.technical_analysis?.structure || '技术结构未返回' }}</p>
      </div>
      <div class="diagnosis-action" :class="actionTone">
        <span>当前动作</span><strong>{{ action }}</strong><small>置信 {{ card.conviction || '未返回' }}</small>
      </div>
    </header>

    <div class="diagnosis-command">
      <article>
        <span>01</span>
        <div><p>当前结论</p><strong>{{ card.summary || '服务端未返回一句话结论' }}</strong></div>
      </article>
      <article>
        <span>02</span>
        <div><p>现在应做什么</p><strong>{{ action }}；{{ plan?.entry_trigger || '执行触发条件未返回' }}</strong></div>
      </article>
      <article>
        <span>03</span>
        <div><p>仓位边界</p><strong>{{ isEntrySignal && cap != null ? `新增仓位上限 ${formatNumber(cap, 2)}%` : isEntrySignal ? '服务端未提供新增仓位上限' : '当前动作不增加新风险；未提供值不可按 0% 解读' }}</strong></div>
      </article>
    </div>

    <div class="diagnosis-guardrails">
      <article class="diagnosis-risk" :class="riskMeta.tone">
        <header><span>风险边界</span><strong>{{ riskMeta.label }}</strong></header>
        <ul v-if="risk?.reasons?.length">
          <li v-for="reason in risk.reasons" :key="reason">{{ reason }}</li>
        </ul>
        <p v-else>接口未返回额外风险原因；不代表风险为零。</p>
        <small class="mono">{{ risk?.rule_version || '规则版本未返回' }}<template v-if="risk?.input_snapshot_hash"> · input {{ risk.input_snapshot_hash.slice(0, 12) }}</template></small>
      </article>

      <div class="diagnosis-levels" aria-label="服务端价格与失效条件">
        <article><span>关注 / 执行区</span><strong class="mono">{{ entryText }}</strong><small>由服务端 trade_plan 返回</small></article>
        <article><span>失效 / 离场条件</span><strong class="mono">{{ stopText }}</strong><small>缺失时不推断价位</small></article>
        <article><span>结构支撑</span><strong class="mono">{{ plan?.support == null ? '—' : `${formatNumber(plan.support, 2)} 元` }}</strong><small>{{ card.technical_analysis?.levels?.support_basis || '依据未返回' }}</small></article>
        <article><span>结构阻力</span><strong class="mono">{{ plan?.resistance == null ? '—' : `${formatNumber(plan.resistance, 2)} 元` }}</strong><small>{{ card.technical_analysis?.levels?.resistance_basis || '依据未返回' }}</small></article>
      </div>
    </div>

    <footer class="diagnosis-decision__footer">
      <span>确定性结论权威：{{ card.decision_authority || '服务端规则 + 风险闸' }}</span>
      <a href="#/research?tab=ask" @click="prepareExplanation">让 AI 解释已冻结诊断卡</a>
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
@media (max-width: 980px) {
  .diagnosis-command { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-command article { border-right: 0; border-bottom: 1px solid var(--border); }
  .diagnosis-command article:last-child { border-bottom: 0; }
  .diagnosis-guardrails { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-risk { border-right: 0; border-bottom: 1px solid var(--border); }
}
@media (max-width: 620px) {
  .diagnosis-decision__header { align-items: stretch; flex-direction: column; padding: 12px; }
  .diagnosis-action { min-width: 0; }
  .diagnosis-levels { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-levels article { border-right: 0; }
  .diagnosis-levels article:nth-last-child(2) { border-bottom: 1px solid var(--border); }
  .diagnosis-decision__footer { align-items: stretch; flex-direction: column; }
  .diagnosis-decision__footer a { justify-content: center; }
}
</style>
