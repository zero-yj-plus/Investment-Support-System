<script setup lang="ts">
import { computed } from 'vue'
import type { TomorrowCockpitSnapshot } from '../types/tomorrow'

const props = defineProps<{ snapshot?: TomorrowCockpitSnapshot }>()
const command = computed(() => props.snapshot?.command)
const riskLevel = computed(() => {
  const value = Number(command.value?.risk_level)
  return value >= 1 && value <= 5 ? value : null
})
const mode = computed(() => riskLevel.value == null ? 'missing' : riskLevel.value >= 4 ? 'veto' : riskLevel.value === 3 ? 'wait' : 'allow')
const headline = computed(() => {
  if (riskLevel.value == null) return '19:30 风险底座缺失，不据此形成次日动作'
  const label = command.value?.level_name || `${riskLevel.value} 级风险`
  if (mode.value === 'veto') return `${label}，明日先等待确认并收缩风险暴露`
  if (mode.value === 'wait') return `${label}，明日只执行已经验证的价格条件`
  return `${label}，明日可保留计划，但开盘后仍须重新验证`
})
const rules = computed(() => mode.value === 'allow' ? command.value?.allow || [] : command.value?.forbid || [])
</script>

<template>
  <section class="panel tomorrow-summary" :class="`is-${mode}`" aria-labelledby="tomorrow-summary-title">
    <div class="tomorrow-summary__main">
      <div>
        <p class="eyebrow">19:30 生成时风险底座 · 服务端确定性快照</p>
        <div class="tomorrow-summary__title-row">
          <span v-if="riskLevel != null" class="risk-level-badge" :class="`risk-${riskLevel}`">风险 {{ riskLevel }} 级</span>
          <span v-else class="risk-level-badge risk-missing">风险缺失</span>
          <h2 id="tomorrow-summary-title">{{ headline }}</h2>
        </div>
        <p class="tomorrow-summary__action">{{ command?.today_action || '当前只保留已有计划，不依据缺失数据新增标的、仓位或价格条件。' }}</p>
      </div>
      <div class="tomorrow-summary__primary-action">
        <span>现在唯一主动作</span>
        <a class="primary-button" href="#tomorrow-watchlist">核对明日触发条件</a>
      </div>
    </div>

    <dl class="tomorrow-summary__guardrails">
      <div><dt>仓位边界</dt><dd class="mono">{{ command?.position_cap || '待确认' }}</dd></div>
      <div><dt>第一原则</dt><dd>{{ command?.first_principle || '开盘事实形成前不把晚间计划直接当作可执行指令。' }}</dd></div>
      <div><dt>风险定格时点</dt><dd class="mono">{{ snapshot?.as_of || '待确认' }}</dd></div>
    </dl>

    <div v-if="rules.length" class="tomorrow-summary__rules">
      <strong>{{ mode === 'allow' ? '允许条件' : '禁止条件' }}</strong>
      <span v-for="item in rules" :key="item">{{ item }}</span>
    </div>
    <p v-else class="tomorrow-summary__missing">结构化允许/禁止项暂不可用；不从报告散文中自行推导交易条件。</p>

    <div v-if="command?.blackswan?.length" class="tomorrow-summary__black-swan" role="status">
      <strong>黑天鹅信号</strong><span>{{ command.blackswan.join('；') }}</span>
    </div>
    <p class="tomorrow-summary__foot">风险快照是次日计划的服务端底座，不等于明日开盘后的最终风险闸；价格区间、筛选和是否可执行仍以服务端当时结论为准。</p>
  </section>
</template>

<style scoped>
.tomorrow-summary { --summary-color: var(--border-strong); min-width: 0; margin-bottom: 16px; overflow: hidden; border-top: 3px solid var(--summary-color); }
.tomorrow-summary.is-allow { --summary-color: var(--info); }
.tomorrow-summary.is-wait { --summary-color: var(--warning); }
.tomorrow-summary.is-veto { --summary-color: var(--danger); }
.tomorrow-summary__main { display: grid; grid-template-columns: minmax(0, 1fr) 206px; align-items: center; gap: 18px; padding: 16px 18px 10px; }
.tomorrow-summary__title-row { display: flex; min-width: 0; flex-wrap: wrap; align-items: center; gap: 8px; }
.tomorrow-summary__title-row h2 { flex: 1 1 420px; margin: 0; font-size: clamp(18px, 1.45vw, 23px); line-height: 1.4; }
.risk-missing { background: var(--surface-2); color: var(--text-2); }
.tomorrow-summary__action { margin: 10px 0 0; color: var(--text); font-size: 18px; font-weight: 720; line-height: 1.65; }
.tomorrow-summary__primary-action { display: grid; gap: 6px; padding-left: 18px; border-left: 1px solid var(--border); color: var(--text-3); font-size: 12px; }
.tomorrow-summary__primary-action .primary-button { width: 100%; }
.tomorrow-summary__guardrails { display: grid; grid-template-columns: minmax(150px, .28fr) minmax(0, 1fr) minmax(160px, .32fr); margin: 0 18px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.tomorrow-summary__guardrails div { min-width: 0; padding: 10px 12px; }
.tomorrow-summary__guardrails div + div { border-left: 1px solid var(--border); }
.tomorrow-summary__guardrails dt { color: var(--text-3); font-size: 12px; font-weight: 700; }
.tomorrow-summary__guardrails dd { margin: 2px 0 0; color: var(--text-2); font-size: 14px; }
.tomorrow-summary__rules { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 12px 18px 0; }
.tomorrow-summary__rules strong { color: var(--summary-color); font-size: 13px; }
.tomorrow-summary__rules span { padding: 4px 9px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-2); color: var(--text-2); font-size: 13px; }
.tomorrow-summary__missing { margin: 0; padding: 12px 18px 0; color: var(--warning); font-size: 13px; }
.tomorrow-summary__black-swan { display: flex; gap: 8px; margin: 12px 18px 0; padding: 8px 10px; border: 1px solid var(--danger); border-radius: var(--radius-sm); color: var(--text-2); font-size: 13px; }
.tomorrow-summary__black-swan strong { flex: 0 0 auto; color: var(--danger); }
.tomorrow-summary__foot { margin: 12px 18px 0; padding: 10px 0 12px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 12px; line-height: 1.55; }
@media (max-width: 720px) {
  .tomorrow-summary__main { grid-template-columns: minmax(0, 1fr); padding: 14px 12px 8px; }
  .tomorrow-summary__primary-action { padding: 10px 0 0; border-top: 1px solid var(--border); border-left: 0; }
  .tomorrow-summary__guardrails { grid-template-columns: minmax(0, 1fr); margin: 0 12px; }
  .tomorrow-summary__guardrails div + div { border-top: 1px solid var(--border); border-left: 0; }
  .tomorrow-summary__rules { padding-right: 12px; padding-left: 12px; }
  .tomorrow-summary__foot { margin-right: 12px; margin-left: 12px; }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; transition: none !important; } }
</style>
