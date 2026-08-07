<script setup lang="ts">
import { computed, ref } from 'vue'
import type { StockPerspective } from '../types/stockAsk'

const props = defineProps<{
  items: StockPerspective[]
  selectedIds: string[]
  maxSelected: number
  loading: boolean
  error: string
  disabled: boolean
}>()

const emit = defineEmits<{
  toggle: [id: string]
  clear: []
  retry: []
}>()

const expanded = ref(false)
const selectedSet = computed(() => new Set(props.selectedIds))
const selectedItems = computed(() => props.items.filter((item) => selectedSet.value.has(item.id)))
const atLimit = computed(() => props.selectedIds.length >= props.maxSelected)
const groups = computed(() => {
  const grouped = new Map<string, StockPerspective[]>()
  props.items.forEach((item) => {
    const rows = grouped.get(item.category) || []
    rows.push(item)
    grouped.set(item.category, rows)
  })
  return Array.from(grouped, ([name, rows]) => ({ name, rows }))
})

function isDisabled(id: string) {
  return props.disabled || (atLimit.value && !selectedSet.value.has(id))
}
</script>

<template>
  <section class="stock-ask-perspectives" aria-labelledby="stock-ask-perspectives-title" @keydown.esc="expanded = false">
    <div class="stock-ask-perspectives__bar">
      <button
        type="button"
        class="stock-ask-perspectives__toggle"
        :aria-expanded="expanded"
        aria-controls="stock-ask-perspectives-panel"
        @click="expanded = !expanded"
      >
        <span>
          <strong id="stock-ask-perspectives-title">分析视角</strong>
          <small>{{ selectedIds.length ? `已选 ${selectedIds.length}/${maxSelected}` : `可选 ${items.length} 个，最多 ${maxSelected} 个` }}</small>
        </span>
        <span class="stock-ask-perspectives__chevron" :class="{ 'is-open': expanded }" aria-hidden="true"></span>
      </button>
      <button v-if="selectedIds.length" type="button" class="stock-ask-perspectives__clear" :disabled="disabled" @click="emit('clear')">清空</button>
    </div>

    <div v-if="selectedItems.length" class="stock-ask-perspectives__selected" aria-label="本轮已选择的分析视角">
      <span v-for="item in selectedItems" :key="item.id">{{ item.name }}</span>
    </div>

    <div v-if="expanded" id="stock-ask-perspectives-panel" class="stock-ask-perspectives__panel">
      <p class="stock-ask-perspectives__help" :class="{ 'is-limit': atLimit }" aria-live="polite">
        {{ atLimit ? `已达到 ${maxSelected} 个上限；取消一个后可改选。` : '每个视角独立分析，最后汇总共识与分歧；不会改写服务端风险结论。' }}
      </p>
      <p v-if="loading" class="stock-ask-perspectives__state" role="status">正在读取分析视角…</p>
      <div v-else-if="items.length" class="stock-ask-perspectives__groups">
        <fieldset v-for="group in groups" :key="group.name">
          <legend>{{ group.name }}</legend>
          <div class="stock-ask-perspectives__grid">
            <label
              v-for="item in group.rows"
              :key="item.id"
              :class="{ 'is-selected': selectedSet.has(item.id), 'is-disabled': isDisabled(item.id) }"
            >
              <input
                type="checkbox"
                :checked="selectedSet.has(item.id)"
                :disabled="isDisabled(item.id)"
                @change="emit('toggle', item.id)"
              >
              <strong>{{ item.name }}</strong>
            </label>
          </div>
        </fieldset>
      </div>
      <div v-if="error" class="stock-ask-perspectives__error" role="alert">
        <span>{{ error }}</span>
        <button v-if="!items.length" type="button" @click="emit('retry')">重试</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.stock-ask-perspectives { border-top: 1px solid var(--border); background: color-mix(in srgb, var(--surface-2) 50%, var(--surface)); }
.stock-ask-perspectives__bar { display: flex; min-height: 48px; align-items: center; gap: 8px; padding: 4px 12px; }
.stock-ask-perspectives__toggle { display: flex; min-width: 0; min-height: 44px; flex: 1; align-items: center; justify-content: space-between; gap: 12px; padding: 5px 8px; border: 0; border-radius: 7px; background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.stock-ask-perspectives__toggle:hover { background: var(--surface-2); }
.stock-ask-perspectives__toggle > span:first-child { display: flex; min-width: 0; align-items: baseline; gap: 8px; }
.stock-ask-perspectives__toggle strong { font-size: 13px; }
.stock-ask-perspectives__toggle small { color: var(--text-3); font-size: 12px; }
.stock-ask-perspectives__chevron { width: 9px; height: 9px; flex: 0 0 auto; border-right: 2px solid var(--text-3); border-bottom: 2px solid var(--text-3); transform: rotate(45deg) translateY(-2px); transition: transform 180ms ease-out; }
.stock-ask-perspectives__chevron.is-open { transform: rotate(225deg) translate(-2px, -2px); }
.stock-ask-perspectives__clear { min-height: 44px; padding: 0 10px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); color: var(--text-2); cursor: pointer; }
.stock-ask-perspectives__clear:disabled { cursor: wait; opacity: .5; }
.stock-ask-perspectives__selected { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 20px 8px; }
.stock-ask-perspectives__selected span { padding: 3px 7px; border: 1px solid color-mix(in srgb, var(--accent) 48%, var(--border)); border-radius: 999px; background: var(--accent-dim); color: var(--accent); font-size: 11px; font-weight: 700; }
.stock-ask-perspectives__panel { max-height: min(18dvh, 180px); overflow-y: auto; padding: 4px 12px 10px; border-top: 1px dashed var(--border); overscroll-behavior: contain; scrollbar-width: thin; }
.stock-ask-perspectives__help, .stock-ask-perspectives__state { margin: 8px 0; color: var(--text-3); font-size: 12px; line-height: 1.6; }
.stock-ask-perspectives__help.is-limit { color: var(--warning); font-weight: 700; }
.stock-ask-perspectives__groups { display: grid; gap: 12px; }
.stock-ask-perspectives fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
.stock-ask-perspectives legend { margin-bottom: 6px; color: var(--text-2); font-size: 12px; font-weight: 760; }
.stock-ask-perspectives__grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 6px; }
.stock-ask-perspectives__grid label { display: grid; min-width: 0; min-height: 44px; grid-template-columns: 20px minmax(0, 1fr); align-items: center; gap: 7px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); cursor: pointer; }
.stock-ask-perspectives__grid label:hover { border-color: var(--border-strong); }
.stock-ask-perspectives__grid label.is-selected { border-color: color-mix(in srgb, var(--accent) 65%, var(--border)); background: var(--accent-dim); }
.stock-ask-perspectives__grid label.is-disabled { cursor: not-allowed; opacity: .48; }
.stock-ask-perspectives__grid input { width: 18px; height: 18px; margin: 1px 0 0; accent-color: var(--accent); }
.stock-ask-perspectives__grid strong { min-width: 0; color: var(--text); font-size: 13px; line-height: 1.35; }
.stock-ask-perspectives__error { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 8px; color: var(--danger); font-size: 12px; }
.stock-ask-perspectives__error button { min-height: 44px; padding: 0 12px; border: 1px solid var(--danger); border-radius: 7px; background: transparent; color: var(--danger); cursor: pointer; }

@media (max-width: 800px) {
  .stock-ask-perspectives__panel { max-height: none; overflow: visible; }
}

@media (max-width: 720px) {
  .stock-ask-perspectives__grid { grid-template-columns: minmax(0, 1fr); }
  .stock-ask-perspectives__toggle > span:first-child { align-items: flex-start; flex-direction: column; gap: 1px; }
  .stock-ask-perspectives__selected { padding-inline: 12px; }
}

@media (prefers-reduced-motion: reduce) {
  .stock-ask-perspectives__chevron { transition: none; }
}
</style>
