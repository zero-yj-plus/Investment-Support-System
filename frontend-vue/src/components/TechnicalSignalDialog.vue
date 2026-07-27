<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { TechnicalSignalDefinition } from '../types/technicalScreener'

const props = defineProps<{
  open: boolean
  signals: TechnicalSignalDefinition[]
  targetLabel: string
}>()
const emit = defineEmits<{
  close: []
  commit: [codes: string[]]
}>()

const dialog = ref<HTMLElement | null>(null)
const search = ref<HTMLInputElement | null>(null)
const query = ref('')
const category = ref('全部')
const filter = ref<'all' | 'available' | 'selected'>('all')
const selected = ref<string[]>([])

const categories = computed(() => ['全部', ...new Set(props.signals.map((item) => item.category))])
const rows = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return props.signals.filter((item) => {
    if (category.value !== '全部' && item.category !== category.value) return false
    if (filter.value === 'available' && !item.available) return false
    if (filter.value === 'selected' && !selected.value.includes(item.signal_code)) return false
    const haystack = [item.signal_code, item.signal_name, item.category, ...(item.aliases || [])].join(' ').toLowerCase()
    return !needle || haystack.includes(needle)
  })
})

watch(() => props.open, async (open) => {
  if (!open) return
  query.value = ''
  category.value = '全部'
  filter.value = 'all'
  selected.value = []
  await nextTick()
  search.value?.focus()
})

function toggle(code: string, checked: boolean) {
  selected.value = checked
    ? [...new Set([...selected.value, code])]
    : selected.value.filter((item) => item !== code)
}

function onDialogKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key !== 'Tab' || !dialog.value) return
  const focusable = [...dialog.value.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled])')]
  if (!focusable.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="technical-library-backdrop" @mousedown.self="emit('close')">
      <section
        ref="dialog"
        class="technical-library"
        role="dialog"
        aria-modal="true"
        aria-labelledby="technical-library-title"
        @keydown="onDialogKeydown"
      >
        <header class="technical-library__header">
          <div>
            <p class="eyebrow">信号目录</p>
            <h2 id="technical-library-title">技术信号库</h2>
            <p>加入到：{{ targetLabel }}</p>
          </div>
          <button class="secondary-button" type="button" @click="emit('close')">关闭</button>
        </header>

        <div class="technical-library__tools">
          <label for="technical-library-search">搜索信号</label>
          <input id="technical-library-search" ref="search" v-model="query" type="search" autocomplete="off" placeholder="中文名、指标名、编码或别名">
          <div class="technical-library__filters" aria-label="信号过滤">
            <button type="button" :aria-pressed="filter === 'all'" @click="filter = 'all'">全部</button>
            <button type="button" :aria-pressed="filter === 'available'" @click="filter = 'available'">仅看可用</button>
            <button type="button" :aria-pressed="filter === 'selected'" @click="filter = 'selected'">已选 {{ selected.length }}</button>
          </div>
        </div>

        <div class="technical-library__body">
          <nav class="technical-library__categories" aria-label="技术信号分类">
            <button
              v-for="item in categories"
              :key="item"
              type="button"
              :aria-pressed="category === item"
              @click="category = item"
            >{{ item }}</button>
          </nav>
          <div class="technical-library__list" role="group" aria-label="可选技术信号">
            <label v-for="item in rows" :key="item.signal_code" class="technical-library__row" :class="{ 'is-unavailable': !item.available }">
              <input
                type="checkbox"
                :checked="selected.includes(item.signal_code)"
                :disabled="!item.available"
                @change="toggle(item.signal_code, ($event.target as HTMLInputElement).checked)"
              >
              <span>
                <strong>{{ item.signal_name }}</strong>
                <small>{{ item.signal_kind === 'state' ? '状态型' : '事件型' }} · {{ item.signal_code }}</small>
                <span>{{ item.available ? (item.signal_kind === 'state' ? '当前周期持续成立' : '当前周期新发生') : (item.unavailable_reason || '数据源不可用') }}</span>
              </span>
              <em>{{ item.available ? item.category : '不可用' }}</em>
            </label>
            <div v-if="!rows.length" class="technical-library__empty">
              <strong>没有符合当前过滤条件的信号</strong>
              <span>可切换分类或清除搜索词。</span>
            </div>
          </div>
        </div>

        <footer class="technical-library__footer">
          <span aria-live="polite">已选择 {{ selected.length }} 项</span>
          <div>
            <button class="secondary-button" type="button" @click="emit('close')">取消</button>
            <button class="primary-button" type="button" :disabled="!selected.length" @click="emit('commit', selected)">加入当前条件组</button>
          </div>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.technical-library-backdrop { position: fixed; z-index: 1000; inset: 0; display: flex; justify-content: flex-end; background: rgb(8 15 25 / 70%); }
.technical-library { width: min(760px, 58vw); min-width: 0; height: 100%; display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto; border-left: 1px solid var(--border-strong); background: var(--surface); color: var(--text); }
.technical-library__header, .technical-library__footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 18px; border-bottom: 1px solid var(--border); }
.technical-library__header h2 { margin: 0; font-size: 20px; }
.technical-library__header p:last-child { margin: 4px 0 0; color: var(--text-3); }
.technical-library__tools { padding: 12px 18px; border-bottom: 1px solid var(--border); }
.technical-library__tools > label { display: block; margin-bottom: 6px; color: var(--text-2); font-size: 13px; }
.technical-library__tools input { width: 100%; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); color: var(--text); }
.technical-library__filters { display: flex; gap: 8px; margin-top: 8px; overflow-x: auto; }
.technical-library__filters button, .technical-library__categories button { min-height: 44px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--text-2); cursor: pointer; }
.technical-library__filters button { padding: 0 12px; white-space: nowrap; background: var(--surface-2); }
.technical-library__filters button[aria-pressed="true"], .technical-library__categories button[aria-pressed="true"] { border-color: var(--accent); color: var(--text); background: var(--accent-dim); }
.technical-library__body { min-height: 0; display: grid; grid-template-columns: 150px minmax(0, 1fr); overflow: hidden; }
.technical-library__categories { min-height: 0; display: grid; align-content: start; gap: 4px; padding: 10px; overflow-y: auto; border-right: 1px solid var(--border); }
.technical-library__categories button { width: 100%; padding: 0 10px; text-align: left; }
.technical-library__list { min-height: 0; overflow-y: auto; padding: 8px 14px 16px; }
.technical-library__row { min-height: 76px; display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: start; gap: 10px; padding: 12px 7px; border-bottom: 1px solid var(--border); cursor: pointer; }
.technical-library__row > input { width: 20px; height: 20px; margin-top: 2px; accent-color: var(--accent); }
.technical-library__row strong, .technical-library__row small, .technical-library__row span > span { display: block; }
.technical-library__row small, .technical-library__row span > span { margin-top: 3px; color: var(--text-3); font-size: 12px; }
.technical-library__row > em { color: var(--text-3); font-size: 12px; font-style: normal; }
.technical-library__row.is-unavailable { cursor: not-allowed; opacity: .72; }
.technical-library__empty { min-height: 220px; display: grid; place-content: center; gap: 5px; color: var(--text-2); text-align: center; }
.technical-library__empty span { color: var(--text-3); }
.technical-library__footer { border-top: 1px solid var(--border); border-bottom: 0; }
.technical-library__footer > div { display: flex; gap: 8px; }
@media (max-width: 720px) {
  .technical-library { width: 100vw; }
  .technical-library__header { padding-top: max(12px, env(safe-area-inset-top)); }
  .technical-library__body { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); }
  .technical-library__categories { display: flex; overflow-x: auto; overflow-y: hidden; border-right: 0; border-bottom: 1px solid var(--border); }
  .technical-library__categories button { width: auto; min-width: max-content; }
  .technical-library__footer { align-items: stretch; flex-direction: column; padding-bottom: max(12px, env(safe-area-inset-bottom)); }
  .technical-library__footer > div { display: grid; grid-template-columns: 1fr 1fr; }
}
</style>
