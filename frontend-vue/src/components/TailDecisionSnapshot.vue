<script setup lang="ts">
import { computed } from 'vue'
import type { TailCockpitSnapshot } from '../types/tail'

const props = defineProps<{ snapshot?: TailCockpitSnapshot }>()

const command = computed(() => props.snapshot?.command)
const riskLevel = computed(() => {
  const level = Number(command.value?.risk_level)
  return level >= 1 && level <= 5 ? level : null
})
const mode = computed(() => riskLevel.value == null ? 'missing' : riskLevel.value >= 4 ? 'veto' : riskLevel.value === 3 ? 'wait' : 'allow')
const verdict = computed(() => mode.value === 'veto' ? '今日不做' : mode.value === 'wait' ? '仅看不抢' : mode.value === 'allow' ? '可做尾盘' : '等待定稿')
const headline = computed(() => {
  if (riskLevel.value == null) return '尾盘定稿后显示能否参与'
  const levelName = command.value?.level_name || `${riskLevel.value} 级风险`
  if (mode.value === 'veto') return `${levelName}，不建议做尾盘，以防守为主`
  if (mode.value === 'wait') return `${levelName}，只观察到价机会，不主动抢筹`
  return `${levelName}，可按计划参与，但必须严守条件`
})
const rules = computed(() => mode.value === 'allow' ? command.value?.allow || [] : command.value?.forbid || [])
const rulesTitle = computed(() => mode.value === 'allow' ? '今日允许' : '今日禁止')
</script>

<template>
  <section class="panel tail-gate" :class="`is-${mode}`" aria-labelledby="tail-gate-title">
    <div class="tail-gate__verdict">
      <div>
        <p class="eyebrow">14:52 正式定稿 · 先回答能不能做</p>
        <div class="tail-gate__title-row">
          <span class="tail-verdict-badge">{{ verdict }}</span>
          <span v-if="riskLevel != null" class="risk-level-badge" :class="`risk-${riskLevel}`">风险 {{ riskLevel }} 级</span>
          <span v-else class="risk-level-badge risk-missing">风险暂不可用</span>
          <h2 id="tail-gate-title">{{ headline }}</h2>
        </div>
      </div>
      <div class="tail-gate__stamp">
        <span>定格时点</span>
        <strong class="mono">{{ snapshot?.as_of || '待确认' }}</strong>
      </div>
    </div>

    <p class="tail-gate__action">{{ command?.today_action || '当前不依据缺失数据新增尾盘交易动作。' }}</p>

    <dl class="tail-gate__guardrails">
      <div>
        <dt>仓位上限</dt>
        <dd class="mono">{{ command?.position_cap || '待确认' }}</dd>
      </div>
      <div>
        <dt>第一原则</dt>
        <dd>{{ command?.first_principle || '尾盘决策未定稿前不新增风险暴露。' }}</dd>
      </div>
    </dl>

    <div v-if="rules.length" class="tail-gate__rules">
      <strong>{{ rulesTitle }}</strong>
      <span v-for="item in rules" :key="item">{{ item }}</span>
    </div>
    <p v-else class="tail-gate__missing">结构化允许/禁止项暂不可用，不据此扩大仓位。</p>

    <div v-if="command?.blackswan?.length" class="tail-gate__black-swan" role="status">
      <strong>黑天鹅信号</strong>
      <span>{{ command.blackswan.join('；') }}</span>
    </div>

    <p class="tail-gate__foot">本风险闸按尾盘正式报告生成时点定格，当日不随实时波动回写；具体价格条件以正式报告和服务端证据为准。</p>
  </section>
</template>
