<script setup lang="ts">
import { computed } from 'vue'
import type { PulseResponse } from '../types/dashboard'

const props = defineProps<{
  pulse: PulseResponse | null
  loading: boolean
  error?: string
  lastSuccessLabel: string
}>()

const command = computed(() => props.pulse?.ok === false ? undefined : props.pulse?.command)
const riskLevel = computed(() => props.pulse?.ok === false ? null : props.pulse?.risk_level ?? command.value?.risk_level ?? null)
const riskName = computed(() => props.pulse?.ok === false ? '等级待确认' : props.pulse?.risk_label || command.value?.level_name || '等级待确认')
const hasDecision = computed(() => Boolean(command.value && (
  command.value.risk_level != null || command.value.today_action || command.value.trade_state
)))

const cells = computed(() => {
  if (!hasDecision.value) {
    return [
      ['当前状态', '关键决策数据未连接，本卡暂停生成新结论。'],
      ['现在怎么做', '先恢复行情与风险数据，再依据最新时点重新确认动作。'],
      ['仓位边界', '不依据缺失数据新增或补仓'],
      ['执行原则', '数据质量不足时不强行给方向'],
    ]
  }
  const value = command.value!
  return [
    ['当前状态', value['定调叙述'] || value.trade_state || value.regime || '状态待确认'],
    [value.action_label || '今日唯一主动作', value.today_action || value.level_text || '等待可验证条件'],
    ['仓位边界', value.position_cap || '—'],
    ['执行原则', value.first_principle || '按价格条件与风险边界执行'],
  ]
})
</script>

<template>
  <section class="decision-command panel" aria-labelledby="decision-title" aria-live="polite">
    <div class="decision-command__heading">
      <div>
        <h2 id="decision-title">今日决策</h2>
      </div>
      <div class="decision-command__meta">
        <span class="data-stamp">最近成功连接 {{ lastSuccessLabel }}</span>
        <span v-if="pulse?.ok !== false && (pulse?.regime || command?.regime)" class="tag tag--neutral">
          {{ pulse?.regime || command?.regime }}
        </span>
        <span class="tag" :class="`risk-${riskLevel ?? 'missing'}`">
          风险 {{ riskLevel == null ? '—' : `${riskLevel} 级` }} · {{ riskName }}
        </span>
      </div>
    </div>

    <div v-if="loading && !pulse" class="command-skeleton" aria-label="决策数据加载中">
      <span v-for="item in 4" :key="item" class="skeleton-line"></span>
    </div>
    <div v-else class="decision-command__grid" :class="{ 'is-fallback': !hasDecision }">
      <article v-for="([label, value], index) in cells" :key="label" class="decision-cell">
        <span class="decision-cell__index" aria-hidden="true">0{{ index + 1 }}</span>
        <div>
          <p>{{ label }}</p>
          <strong>{{ value }}</strong>
        </div>
      </article>
    </div>
    <p v-if="error" class="inline-notice" role="status">决策接口暂不可用：{{ error }}</p>
  </section>
</template>
