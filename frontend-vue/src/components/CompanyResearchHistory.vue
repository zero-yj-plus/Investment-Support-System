<script setup lang="ts">
import type { CompanyHistoryEntry } from '../types/companyResearch'

defineProps<{ entries: CompanyHistoryEntry[]; activeId: string; disabled: boolean }>()
const emit = defineEmits<{ select: [entry: CompanyHistoryEntry] }>()

function timeLabel(value: number) {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}
</script>

<template>
  <section class="company-history panel" aria-labelledby="company-history-title">
    <header><p class="eyebrow">APPEND-ONLY</p><h2 id="company-history-title">本地研究历史</h2></header>
    <p v-if="!entries.length" class="company-history__empty">暂无研究记录。新结果只追加，不覆盖旧记录。</p>
    <ol v-else>
      <li v-for="entry in entries" :key="entry.id">
        <button
          type="button"
          :class="{ 'is-active': entry.id === activeId }"
          :disabled="disabled"
          @click="emit('select', entry)"
        >
          <span><strong class="mono">{{ entry.code }}</strong><small>{{ entry.incomplete ? '不完整输出 · 已隔离' : '完整研究' }}</small></span>
          <time class="mono" :datetime="new Date(entry.ts).toISOString()">{{ timeLabel(entry.ts) }}</time>
        </button>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.company-history { overflow: hidden; }.company-history > header { padding: 10px 12px; border-bottom: 1px solid var(--border); }.company-history h2 { margin: 0; font-size: 17px; }.company-history__empty { margin: 0; padding: 18px 12px; color: var(--text-3); font-size: 13px; line-height: 1.6; }.company-history ol { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }.company-history li + li { border-top: 1px solid var(--border); }.company-history button { display: grid; width: 100%; min-height: 64px; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px; padding: 9px 12px; border: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; }.company-history button:hover, .company-history button.is-active { background: var(--surface-2); }.company-history button.is-active { box-shadow: inset 3px 0 0 var(--accent); }.company-history button > span { display: grid; min-width: 0; gap: 3px; }.company-history strong { font-size: 15px; }.company-history small, .company-history time { color: var(--text-3); font-size: 11px; }.company-history button:disabled { cursor: wait; opacity: .62; }
</style>
