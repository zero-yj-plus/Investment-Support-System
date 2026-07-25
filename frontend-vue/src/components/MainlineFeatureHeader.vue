<script setup lang="ts">
import type { QualityState } from '../types/dashboard'
import DataStatus from './DataStatus.vue'

defineProps<{
  titleId: string
  eyebrow: string
  title: string
  description: string
  status: QualityState
  statusLabel: string
  stamp?: string
}>()
</script>

<template>
  <header class="feature-heading">
    <div>
      <p class="eyebrow">{{ eyebrow }}</p>
      <h2 :id="titleId">{{ title }}</h2>
      <p>{{ description }}</p>
    </div>
    <div class="feature-heading__meta">
      <DataStatus :state="status" :label="statusLabel" />
      <span class="mono">{{ stamp || '时点未提供' }}</span>
    </div>
  </header>
</template>

<style scoped>
.feature-heading { display: flex; min-height: 76px; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 12px 14px; border-bottom: 1px solid var(--border); }
.feature-heading h2 { margin: 0; font-size: 18px; line-height: 1.35; }
.feature-heading > div > p:last-child { max-width: 680px; margin: 4px 0 0; color: var(--text-3); font-size: 13px; }
.feature-heading__meta { display: flex; flex: 0 0 auto; align-items: flex-end; flex-direction: column; gap: 5px; }
.feature-heading__meta .mono { max-width: 220px; color: var(--text-3); font-size: 12px; overflow-wrap: anywhere; text-align: right; }
@media (max-width: 720px) {
  .feature-heading { flex-direction: column; }
  .feature-heading__meta { align-items: flex-start; }
  .feature-heading__meta .mono { text-align: left; }
}
</style>

