<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PulseResponse, SystemRiskResponse } from '../types/dashboard'
import AppIcon from './AppIcon.vue'

const props = defineProps<{
  risk: SystemRiskResponse | null
  pulse: PulseResponse | null
  loading: boolean
  error?: string
}>()

const expanded = ref(false)
const riskAvailable = computed(() => props.risk != null && props.risk.ok !== false)
const level = computed(() => {
  const value = props.risk?.level
  return riskAvailable.value && Number.isInteger(value) && value != null && value >= 1 && value <= 5 ? value : null
})
const sentiment = computed(() => {
  const value = props.pulse?.ok === false ? null : props.pulse?.senti
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100 ? value : null
})
const sentimentTone = computed(() => {
  const value = sentiment.value
  if (value == null) return 'neutral'
  if (value >= 75) return 'hot'
  if (value >= 58) return 'warm'
  if (value >= 42) return 'neutral'
  if (value >= 25) return 'cool'
  return 'cold'
})
</script>

<template>
  <aside class="panel risk-panel" aria-labelledby="risk-title">
    <header class="section-heading">
      <div>
        <p class="eyebrow">MARKET STATE</p>
        <h2 id="risk-title">市场状态</h2>
      </div>
      <span v-if="risk?.['定格时点']" class="lock-time">定格 {{ risk['定格时点'] }}</span>
    </header>

    <div v-if="loading && !risk && !pulse" class="risk-skeleton" aria-label="市场状态加载中">
      <span class="skeleton-block"></span><span class="skeleton-line"></span><span class="skeleton-line"></span>
    </div>
    <template v-else>
      <div class="state-score-grid">
        <div v-if="riskAvailable && risk && level != null" class="risk-hero" :class="`risk-${level}`">
          <span class="state-score-label">系统风险</span>
          <div class="risk-score"><strong>{{ level }}</strong><span>/ 5 级</span></div>
          <div class="state-score-caption">
            <b>{{ risk.level_name || '等级待确认' }}</b>
            <span>{{ risk.regime || '市场状态待确认' }}</span>
          </div>
        </div>
        <div v-else class="state-score-empty"><strong>系统风险</strong><span>暂不可用</span></div>

        <div v-if="sentiment != null" class="emotion-hero" :class="`is-${sentimentTone}`">
          <span class="state-score-label">情绪温度</span>
          <div class="emotion-score"><strong>{{ sentiment }}</strong><span>/ 100</span></div>
          <div class="state-score-caption"><b>{{ pulse?.senti_label || '分档待确认' }}</b></div>
          <div
            class="emotion-meter"
            role="progressbar"
            aria-label="情绪温度"
            aria-valuemin="0"
            aria-valuemax="100"
            :aria-valuenow="sentiment"
          ><i :style="{ width: `${sentiment}%` }"></i></div>
        </div>
        <div v-else class="state-score-empty"><strong>情绪温度</strong><span>暂不可用</span></div>
      </div>

      <template v-if="riskAvailable && risk && level != null">
        <div class="risk-scale" :aria-label="`当前系统风险 ${level} 级，共 5 级`" role="img">
          <span v-for="step in 5" :key="step" :class="[`risk-step-${step}`, { active: level != null && step <= level }]"><i>{{ step }}</i></span>
        </div>
        <p class="risk-summary">{{ risk.summary || risk.level_text || '风险摘要待确认' }}</p>

        <button
          class="disclosure-button"
          type="button"
          :aria-expanded="expanded"
          aria-controls="risk-evidence"
          @click="expanded = !expanded"
        >
          <span>{{ expanded ? '收起五项风险依据' : '展开五项风险依据' }}</span>
          <AppIcon name="chevron" :class="{ rotated: expanded }" />
        </button>
        <div v-show="expanded" id="risk-evidence" class="risk-evidence">
          <article v-for="item in risk.dims || []" :key="item.dim">
            <div><strong>{{ item.dim || '维度' }}</strong><span>{{ item.contrib || '待确认' }}</span></div>
            <p>{{ item.reading || '该维度数据暂不可用' }}</p>
          </article>
          <p v-if="!risk.dims?.length" class="inline-notice">五项风险依据暂未返回。</p>
        </div>
      </template>
      <p v-else class="inline-notice" role="status">系统性风险暂不可用：{{ error || risk?.error || risk?.missing?.join('；') || '风险等级缺失，当前不据此新增风险暴露。' }}</p>
    </template>
  </aside>
</template>
