<script setup lang="ts">
import CloseReviewDecision from './CloseReviewDecision.vue'
import type { TomorrowCockpitSnapshot } from '../types/tomorrow'

defineProps<{ snapshot?: TomorrowCockpitSnapshot | null }>()
</script>

<template>
  <CloseReviewDecision
    :snapshot="snapshot || undefined"
    primary-action-eyebrow="唯一主动作"
    primary-action-label="核对明日触发条件"
    primary-action-href="#tomorrow-watchlist"
    footnote="这是收盘生成时的服务端确定性结论，不等于明日开盘后的最终风险闸；执行前仍需重新校验实时状态。"
  />
</template>
