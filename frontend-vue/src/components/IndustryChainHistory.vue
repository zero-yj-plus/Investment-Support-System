<script setup lang="ts">
import type { IndustryChainHistoryEntry } from '../types/industryChain'
import { formatIndustryHistoryTime } from '../utils/industryChainFormat'

defineProps<{
  entries: IndustryChainHistoryEntry[]
  activeId: string
  disabled?: boolean
  deletingId?: string
  error?: string
  message?: string
}>()
const emit = defineEmits<{
  select: [entry: IndustryChainHistoryEntry]
  delete: [entry: IndustryChainHistoryEntry]
}>()

function requestDelete(entry: IndustryChainHistoryEntry) {
  if (window.confirm(`删除产业链研究记录“${entry.industry}”？此操作不可撤销。`)) emit('delete', entry)
}
</script>

<template>
  <section class="chain-history panel" aria-labelledby="chain-history-title">
    <header><div><p class="eyebrow">LOCAL HISTORY</p><h2 id="chain-history-title">本地研究历史</h2></div><span class="mono">{{ entries.length }}/20</span></header>
    <p v-if="error" class="chain-history__feedback is-error" role="alert">{{ error }}</p>
    <p v-else-if="message" class="chain-history__feedback is-success" role="status">{{ message }}</p>
    <p v-if="!entries.length" class="chain-history__empty">暂无研究记录。完整和不完整输出会保持不同状态。</p>
    <div v-else class="chain-history__list">
      <div
        v-for="entry in entries"
        :key="entry.id"
        class="chain-history__item"
        :class="{ 'is-active': entry.id === activeId }"
      >
        <button
          class="chain-history__open"
          type="button"
          :aria-pressed="entry.id === activeId"
          :disabled="disabled || deletingId === entry.id"
          @click="emit('select', entry)"
        >
          <strong>{{ entry.industry }}</strong>
          <span>{{ entry.incomplete ? '不完整 · 已隔离' : '完整研究' }}</span>
          <time class="mono" :datetime="new Date(entry.ts).toISOString()">{{ formatIndustryHistoryTime(entry.ts) }}</time>
        </button>
        <button
          class="chain-history__delete"
          type="button"
          :disabled="disabled || Boolean(deletingId)"
          :aria-label="`删除产业链研究记录：${entry.industry}`"
          @click="requestDelete(entry)"
        >{{ deletingId === entry.id ? '删除中' : '删除' }}</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.chain-history { overflow: hidden; }.chain-history > header { display: flex; min-height: 58px; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border); }.chain-history h2 { margin: 0; font-size: 17px; }.chain-history > header > span { color: var(--text-3); font-size: 12px; }.chain-history__feedback { margin: 0; padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 12px; }.chain-history__feedback.is-error { color: var(--danger); }.chain-history__feedback.is-success { color: var(--info); }.chain-history__empty { margin: 0; padding: 18px 12px; color: var(--text-3); font-size: 13px; line-height: 1.6; }.chain-history__list { display: grid; max-height: 520px; overflow-y: auto; }.chain-history__item { display: grid; grid-template-columns: minmax(0, 1fr) 58px; align-items: center; border-bottom: 1px solid var(--border); }.chain-history__item:last-child { border-bottom: 0; }.chain-history__item.is-active { box-shadow: inset 3px 0 0 var(--accent); background: color-mix(in srgb, var(--accent) 7%, var(--surface)); }.chain-history__open { display: grid; grid-template-columns: minmax(0, 1fr) auto; min-height: 64px; gap: 2px 8px; padding: 9px 8px 9px 11px; border: 0; background: transparent; color: var(--text-2); text-align: left; cursor: pointer; }.chain-history__open:hover { background: var(--surface-2); }.chain-history__delete { min-width: 52px; min-height: 44px; margin-right: 6px; padding: 4px; border: 0; background: transparent; color: var(--danger); cursor: pointer; }.chain-history__open:disabled, .chain-history__delete:disabled { cursor: not-allowed; opacity: .62; }.chain-history__list strong { min-width: 0; overflow: hidden; color: var(--text); text-overflow: ellipsis; white-space: nowrap; }.chain-history__list span { grid-column: 1; color: var(--text-3); font-size: 12px; }.chain-history__list time { grid-column: 2; grid-row: 1 / 3; align-self: center; color: var(--text-3); font-size: 11px; }
@media (max-width: 1039px) { .chain-history__list { display: flex; max-height: none; overflow-x: auto; overflow-y: hidden; }.chain-history__item { min-width: 250px; border-right: 1px solid var(--border); border-bottom: 0; } }
</style>
