<script setup lang="ts">
import { computed } from 'vue'
import type { HotboardStock, HotboardTheme } from '../types/hotboard'

const props = defineProps<{
  stocks: HotboardStock[]
  modelValue: string
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

function splitThemes(value: unknown): string[] {
  return String(value || '').split('+').map((item) => item.trim()).filter(Boolean)
}

const themes = computed<HotboardTheme[]>(() => {
  const counts = new Map<string, number>()
  for (const stock of props.stocks) {
    for (const theme of splitThemes(stock.reason_type)) counts.set(theme, (counts.get(theme) || 0) + 1)
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((left, right) => right.count - left.count || left.name.localeCompare(right.name, 'zh-CN'))
    .slice(0, 18)
})

const maxCount = computed(() => Math.max(1, ...themes.value.map((item) => item.count)))
</script>

<template>
  <section class="panel hotboard-themes" aria-labelledby="hotboard-theme-title">
    <header class="hotboard-section-heading">
      <div>
        <p class="eyebrow">结构化证据 01</p>
        <h2 id="hotboard-theme-title">题材集中分布</h2>
      </div>
      <span>{{ themes.length }} 个可识别题材</span>
    </header>
    <div class="hotboard-theme-list" aria-label="按题材筛选涨停股">
      <button
        type="button"
        class="hotboard-theme-button"
        :class="{ 'is-active': modelValue === '' }"
        :aria-pressed="modelValue === ''"
        @click="emit('update:modelValue', '')"
      ><span>全部</span><strong class="mono">{{ stocks.length }}</strong></button>
      <button
        v-for="theme in themes"
        :key="theme.name"
        type="button"
        class="hotboard-theme-button"
        :class="{ 'is-active': modelValue === theme.name }"
        :aria-pressed="modelValue === theme.name"
        :style="{ '--theme-weight': `${Math.round(theme.count / maxCount * 100)}%` }"
        @click="emit('update:modelValue', theme.name)"
      ><span>{{ theme.name }}</span><strong class="mono">{{ theme.count }}</strong></button>
    </div>
  </section>
</template>

<style scoped>
.hotboard-themes { overflow: hidden; }
.hotboard-section-heading { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.hotboard-section-heading h2 { margin: 0; font-size: 18px; line-height: 1.35; }
.hotboard-section-heading > span { color: var(--text-3); font-size: 13px; }
.hotboard-theme-list { display: flex; flex-wrap: wrap; gap: 8px; padding: 14px 16px; }
.hotboard-theme-button { position: relative; display: inline-flex; min-height: 44px; align-items: center; gap: 8px; overflow: hidden; padding: 7px 12px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-2); color: var(--text-2); cursor: pointer; }
.hotboard-theme-button::before { position: absolute; inset: auto auto 0 0; width: var(--theme-weight, 0%); height: 2px; background: var(--up); content: ""; }
.hotboard-theme-button:hover { border-color: var(--border-strong); color: var(--text); }
.hotboard-theme-button.is-active { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); }
.hotboard-theme-button strong { color: inherit; }
@media (max-width: 720px) {
  .hotboard-section-heading { align-items: flex-start; }
  .hotboard-theme-list { padding-inline: 12px; }
}
</style>
