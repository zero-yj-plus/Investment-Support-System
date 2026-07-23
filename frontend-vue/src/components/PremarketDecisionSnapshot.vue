<script setup lang="ts">
import { computed } from 'vue'
import type { MarketFlowResponse } from '../types/dashboard'
import type { PremarketCockpitSnapshot, PremarketDirection } from '../types/premarket'

const props = defineProps<{
  snapshot?: PremarketCockpitSnapshot
  liveFlow: MarketFlowResponse | null
  flowError?: string
}>()

const command = computed(() => props.snapshot?.command)
const riskLevel = computed(() => {
  const level = Number(command.value?.risk_level)
  return level >= 1 && level <= 5 ? level : null
})
const actions = computed(() => {
  if (riskLevel.value != null && riskLevel.value >= 3) return command.value?.forbid || []
  return command.value?.allow || []
})
const actionTitle = computed(() => riskLevel.value != null && riskLevel.value >= 3 ? '今日禁止' : '今日允许')

const liveFlowByName = computed(() => new Map(
  (props.liveFlow?.main_flow_sectors || [])
    .filter((item) => item.name && item.main_net != null)
    .map((item) => [item.name as string, item.main_net as number]),
))

function formatPercent(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const number = Number(value)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function formatMoney(value?: number | null): string {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const number = Number(value)
  const absolute = Math.abs(number)
  if (absolute >= 100_000_000) return `${number < 0 ? '-' : ''}${(absolute / 100_000_000).toFixed(1)}亿`
  if (absolute >= 10_000) return `${number < 0 ? '-' : ''}${(absolute / 10_000).toFixed(0)}万`
  return number.toFixed(0)
}

function directionVerification(direction: PremarketDirection): { label: string; tone: string } {
  if (!direction.name || !liveFlowByName.value.has(direction.name)) return { label: '验证待补', tone: 'is-muted' }
  const value = liveFlowByName.value.get(direction.name)!
  return value > 0
    ? { label: '资金续流', tone: 'is-confirmed' }
    : { label: '已转流出', tone: 'is-warning' }
}
</script>

<template>
  <section class="panel premarket-decision" aria-labelledby="premarket-decision-title">
    <div class="premarket-decision__primary">
      <div class="premarket-decision__status">
        <p class="eyebrow">08:50 定格决策</p>
        <div class="risk-lockup">
          <span v-if="riskLevel != null" class="risk-level-badge" :class="`risk-${riskLevel}`">风险 {{ riskLevel }} 级</span>
          <span v-else class="risk-level-badge risk-missing">风险暂不可用</span>
          <h2 id="premarket-decision-title">{{ command?.level_name || command?.action_label || '等待盘前风险快照' }}</h2>
        </div>
        <p class="premarket-decision__action">{{ command?.today_action || '当前不依据缺失数据新增交易动作。' }}</p>
      </div>

      <dl class="premarket-guardrails">
        <div>
          <dt>仓位边界</dt>
          <dd class="mono">{{ command?.position_cap || '待确认' }}</dd>
        </div>
        <div>
          <dt>第一原则</dt>
          <dd>{{ command?.first_principle || '先确认当日数据与集合竞价，不用昨日结论直接下单。' }}</dd>
        </div>
      </dl>

      <div v-if="actions.length" class="premarket-action-list">
        <strong>{{ actionTitle }}</strong>
        <span v-for="item in actions" :key="item">{{ item }}</span>
      </div>
      <p v-else class="premarket-missing-note">结构化允许/禁止项暂不可用，不据此扩大风险暴露。</p>
    </div>

    <div class="premarket-directions">
      <div class="premarket-directions__heading">
        <div>
          <p class="eyebrow">今日重点方向</p>
          <h3>盘前预判与盘中验证</h3>
        </div>
        <span class="mono">{{ snapshot?.as_of || '时点待确认' }}</span>
      </div>
      <div v-if="snapshot?.dir?.length" class="direction-list">
        <article v-for="(direction, directionIndex) in snapshot.dir" :key="direction.name || directionIndex" class="direction-row">
          <span class="direction-rank mono">0{{ directionIndex + 1 }}</span>
          <div><strong>{{ direction.name || '未命名方向' }}</strong><span>盘前资金排序</span></div>
          <span class="mono" :class="direction.change_pct == null ? 'is-flat' : direction.change_pct >= 0 ? 'is-up' : 'is-down'">{{ formatPercent(direction.change_pct) }}</span>
          <span class="mono">{{ formatMoney(direction.main_flow) }}</span>
          <span class="verification-badge" :class="directionVerification(direction).tone">{{ directionVerification(direction).label }}</span>
        </article>
      </div>
      <div v-else class="direction-empty">
        <strong>今日无可确认的资金主线</strong>
        <span>方向快照缺失或两市资金普遍净流出，盘前以防守和等待确认为主。</span>
      </div>
      <p v-if="flowError" class="flow-verification-note" role="status">盘中验证暂不可用：{{ flowError }}</p>
      <p v-else class="flow-verification-note">方向主体按盘前时点锁定；盘中资金只作验证，不改写原预判。</p>
    </div>
  </section>
</template>
