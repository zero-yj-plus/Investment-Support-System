<script setup lang="ts">
import { computed } from 'vue'
import type { CloseReviewCockpitSnapshot } from '../types/closeReview'

const props = withDefaults(defineProps<{
  snapshot?: CloseReviewCockpitSnapshot
  primaryActionEyebrow?: string
  primaryActionLabel?: string
  primaryActionHref?: string
  footnote?: string
}>(), {
  primaryActionEyebrow: '复盘唯一主动作',
  primaryActionLabel: '核对执行偏差',
  primaryActionHref: '#review-evidence',
  footnote: '本结论按今日复盘生成时点定格；历史报告不可覆盖，下一交易日必须重新通过服务端风险闸。',
})
const command = computed(() => props.snapshot?.command)
const riskLevel = computed(() => {
  const value = Number(command.value?.risk_level)
  return value >= 1 && value <= 5 ? value : null
})
const mode = computed(() => riskLevel.value == null ? 'missing' : riskLevel.value >= 4 ? 'veto' : riskLevel.value === 3 ? 'wait' : 'allow')
const headline = computed(() => {
  if (riskLevel.value == null) return '收盘风险结论暂不可用，不据此预判下一交易日'
  const levelName = command.value?.level_name || `${riskLevel.value} 级风险`
  if (mode.value === 'veto') return `${levelName}，复盘以暴露风险和收缩边界为先`
  if (mode.value === 'wait') return `${levelName}，先核对偏差，不急于形成次日交易动作`
  return `${levelName}，可保留计划，但下一交易日仍需重新验证`
})
const rules = computed(() => mode.value === 'allow' ? command.value?.allow || [] : command.value?.forbid || [])
</script>

<template>
  <section class="panel close-decision" :class="`is-${mode}`" aria-labelledby="close-decision-title">
    <div class="close-decision__main">
      <div>
        <p class="eyebrow">收盘确定性结论 · 先看风险与动作边界</p>
        <div class="close-decision__title-row">
          <span v-if="riskLevel != null" class="risk-level-badge" :class="`risk-${riskLevel}`">风险 {{ riskLevel }} 级</span>
          <span v-else class="risk-level-badge risk-missing">风险缺失</span>
          <h2 id="close-decision-title">{{ headline }}</h2>
        </div>
        <p class="close-decision__action">{{ command?.today_action || '当前只保留已知事实，不依据缺失数据新增、补仓或修改交易计划。' }}</p>
      </div>
      <div class="close-decision__primary-action">
        <span>{{ primaryActionEyebrow }}</span>
        <a class="primary-button" :href="primaryActionHref">{{ primaryActionLabel }}</a>
      </div>
    </div>

    <dl class="close-decision__guardrails">
      <div><dt>仓位边界</dt><dd class="mono">{{ command?.position_cap || '待确认' }}</dd></div>
      <div><dt>第一原则</dt><dd>{{ command?.first_principle || '收盘事实不等于次日买入结论；关键依据缺失时不扩大风险。' }}</dd></div>
      <div><dt>定格时点</dt><dd class="mono">{{ snapshot?.as_of || '待确认' }}</dd></div>
    </dl>

    <div v-if="rules.length" class="close-decision__rules">
      <strong>{{ mode === 'allow' ? '保留条件' : '禁止条件' }}</strong>
      <span v-for="item in rules" :key="item">{{ item }}</span>
    </div>
    <p v-else class="close-decision__missing">结构化允许/禁止项暂不可用；不从报告散文中自行推导交易条件。</p>

    <div v-if="command?.blackswan?.length" class="close-decision__black-swan" role="status">
      <strong>黑天鹅信号</strong><span>{{ command.blackswan.join('；') }}</span>
    </div>
    <p class="close-decision__foot">{{ footnote }}</p>
  </section>
</template>
