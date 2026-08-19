<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { getJson } from '../services/api'
import type { StockSearchItem, StockSearchResponse } from '../types/stockSearch'

const props = withDefaults(defineProps<{
  inputId: string
  label?: string
  name?: string
  placeholder?: string
  disabled?: boolean
  readonly?: boolean
  required?: boolean
  ariaDescribedby?: string
  ariaInvalid?: boolean
  displayName?: string
}>(), {
  label: '',
  name: 'stock-code',
  placeholder: '输入代码、中文、全拼或首字母',
  disabled: false,
  readonly: false,
  required: false,
  ariaDescribedby: '',
  ariaInvalid: false,
  displayName: '',
})

const model = defineModel<string>({ required: true })
const emit = defineEmits<{ selected: [item: StockSearchItem] }>()

const root = ref<HTMLElement | null>(null)
const input = ref<HTMLInputElement | null>(null)
const query = ref(String(model.value || ''))
const results = ref<StockSearchItem[]>([])
const response = ref<StockSearchResponse | null>(null)
const requestState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const requestError = ref('')
const open = ref(false)
const activeIndex = ref(-1)
const composing = ref(false)
const focused = ref(false)
const localSelection = ref<StockSearchItem | null>(null)
const exactCodeName = ref('')
const selectionHint = ref('')
let committedCode = /^\d{6}$/.test(model.value) ? model.value : ''
let debounceTimer: ReturnType<typeof setTimeout> | null = null
let blurTimer: ReturnType<typeof setTimeout> | null = null
let controller: AbortController | null = null
let alive = true

const listboxId = computed(() => `${props.inputId}-stock-options`)
const statusId = computed(() => `${props.inputId}-stock-search-status`)
const describedBy = computed(() => [props.ariaDescribedby, statusId.value].filter(Boolean).join(' ') || undefined)
const dropdownVisible = computed(() => (
  open.value
  && Boolean(query.value.trim())
  && !props.disabled
  && !props.readonly
  && (requestState.value !== 'idle' || Boolean(results.value.length))
))
const selectedName = computed(() => {
  if (!/^\d{6}$/.test(query.value) || query.value !== model.value) return ''
  return localSelection.value?.name || props.displayName || exactCodeName.value
})
const catalogNotice = computed(() => {
  const payload = response.value
  if (!payload) return ''
  if (payload.state === 'unavailable') return '本地股票名录暂不可用；仍可输入完整六位代码继续核验。'
  if (payload.universe.stale || payload.universe.refresh_required || !payload.universe.complete) {
    const date = payload.universe.as_of ? `截至 ${payload.universe.as_of}` : '日期未提供'
    return `股票名录${date}，待刷新；候选仅供选择，提交仍会真实核验。`
  }
  if (!payload.pinyin_available) return '拼音索引暂不可用；代码和中文名称搜索仍可使用。'
  return ''
})
const liveMessage = computed(() => {
  if (selectionHint.value) return selectionHint.value
  if (requestState.value === 'loading') return '正在搜索本地股票名录'
  if (requestState.value === 'error') return requestError.value
  if (requestState.value === 'ready' && results.value.length) return `找到 ${results.value.length} 个候选，请明确选择股票`
  if (requestState.value === 'ready' && query.value.trim()) {
    return /^\d{6}$/.test(query.value)
      ? '名录中没有匹配项；该六位代码仍可提交真实接口核验'
      : '没有匹配股票；请修改中文或拼音后重试'
  }
  return ''
})

function clearTimers() {
  if (debounceTimer) clearTimeout(debounceTimer)
  if (blurTimer) clearTimeout(blurTimer)
  debounceTimer = null
  blurTimer = null
}

function abortSearch() {
  controller?.abort()
  controller = null
}

function resetCandidates() {
  results.value = []
  response.value = null
  exactCodeName.value = ''
  activeIndex.value = -1
  requestError.value = ''
  requestState.value = 'idle'
}

function scheduleSearch() {
  if (debounceTimer) clearTimeout(debounceTimer)
  abortSearch()
  selectionHint.value = ''
  const term = query.value.trim()
  if (!term || props.disabled || props.readonly || composing.value) {
    resetCandidates()
    return
  }
  results.value = []
  activeIndex.value = -1
  requestState.value = 'idle'
  debounceTimer = setTimeout(() => void search(term), 160)
}

async function search(term: string) {
  const nextController = new AbortController()
  controller = nextController
  requestState.value = 'loading'
  requestError.value = ''
  open.value = focused.value
  try {
    const payload = await getJson<StockSearchResponse>(
      `/stocks/search?q=${encodeURIComponent(term)}&limit=8`,
      nextController.signal,
    )
    if (!alive || controller !== nextController || query.value.trim() !== term) return
    response.value = payload
    results.value = Array.isArray(payload.results) ? payload.results.slice(0, 8) : []
    exactCodeName.value = results.value.find((item) => item.code === term)?.name || ''
    activeIndex.value = -1
    requestState.value = 'ready'
  } catch (caught) {
    if (!alive || controller !== nextController || (caught instanceof DOMException && caught.name === 'AbortError')) return
    results.value = []
    response.value = null
    requestError.value = caught instanceof Error
      ? `${caught.message}；可输入完整六位代码继续核验。`
      : '股票名录搜索暂不可用；可输入完整六位代码继续核验。'
    requestState.value = 'error'
  } finally {
    if (controller === nextController) controller = null
  }
}

function updateQuery(raw: string) {
  const value = raw.slice(0, 64)
  query.value = value
  localSelection.value = null
  exactCodeName.value = ''
  committedCode = /^\d{6}$/.test(value) ? value : ''
  model.value = committedCode
  open.value = Boolean(value.trim())
  if (!composing.value) scheduleSearch()
}

function choose(item: StockSearchItem) {
  abortSearch()
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = null
  query.value = item.code
  committedCode = item.code
  model.value = item.code
  localSelection.value = item
  exactCodeName.value = item.name
  results.value = []
  activeIndex.value = -1
  requestState.value = 'idle'
  open.value = false
  selectionHint.value = `已选择 ${item.name} ${item.code}`
  emit('selected', item)
  void nextTick(() => input.value?.focus())
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') {
    if (!results.value.length) return
    event.preventDefault()
    open.value = true
    activeIndex.value = activeIndex.value < results.value.length - 1 ? activeIndex.value + 1 : 0
    return
  }
  if (event.key === 'ArrowUp') {
    if (!results.value.length) return
    event.preventDefault()
    open.value = true
    activeIndex.value = activeIndex.value > 0 ? activeIndex.value - 1 : results.value.length - 1
    return
  }
  if (event.key === 'Escape') {
    open.value = false
    activeIndex.value = -1
    return
  }
  if (event.key === 'Enter') {
    if (open.value && activeIndex.value >= 0 && results.value[activeIndex.value]) {
      event.preventDefault()
      choose(results.value[activeIndex.value])
      return
    }
    if (!/^\d{6}$/.test(query.value)) {
      event.preventDefault()
      selectionHint.value = results.value.length
        ? '请点击候选，或先用方向键高亮后按回车选择'
        : '中文或拼音输入必须先选择候选股票'
    }
  }
}

function onFocus() {
  focused.value = true
  if (blurTimer) clearTimeout(blurTimer)
  blurTimer = null
  if (query.value.trim() && !props.readonly) {
    open.value = true
    if (requestState.value === 'idle') scheduleSearch()
  }
}

function onBlur() {
  focused.value = false
  blurTimer = setTimeout(() => {
    open.value = false
    activeIndex.value = -1
  }, 120)
}

function onCompositionStart() {
  composing.value = true
}

function onCompositionEnd(event: CompositionEvent) {
  composing.value = false
  updateQuery((event.target as HTMLInputElement).value)
}

function focus() {
  input.value?.focus()
}

watch(model, (value) => {
  const next = String(value || '')
  if (next === committedCode) return
  committedCode = /^\d{6}$/.test(next) ? next : ''
  query.value = next
  localSelection.value = null
  exactCodeName.value = ''
  if (next) scheduleSearch()
  else resetCandidates()
})

watch(() => [props.disabled, props.readonly], ([disabled, readonly]) => {
  if (disabled || readonly) {
    open.value = false
    abortSearch()
  }
})

onBeforeUnmount(() => {
  alive = false
  clearTimers()
  abortSearch()
})

defineExpose({ focus })
</script>

<template>
  <div ref="root" class="stock-autocomplete">
    <label v-if="label" :for="inputId">
      {{ label }}<b v-if="required" aria-hidden="true"> *</b>
    </label>
    <div class="stock-autocomplete__control">
      <input
        :id="inputId"
        ref="input"
        :name="name"
        :value="query"
        type="text"
        inputmode="search"
        autocomplete="off"
        autocapitalize="none"
        :spellcheck="false"
        maxlength="64"
        :placeholder="placeholder"
        :disabled="disabled"
        :readonly="readonly"
        :required="required"
        role="combobox"
        aria-autocomplete="list"
        :aria-controls="listboxId"
        :aria-expanded="dropdownVisible"
        :aria-activedescendant="activeIndex >= 0 ? `${listboxId}-${activeIndex}` : undefined"
        :aria-describedby="describedBy"
        :aria-invalid="ariaInvalid"
        :class="{ 'has-identity': Boolean(selectedName) }"
        @input="updateQuery(($event.target as HTMLInputElement).value)"
        @keydown="onKeydown"
        @focus="onFocus"
        @blur="onBlur"
        @compositionstart="onCompositionStart"
        @compositionend="onCompositionEnd"
      />
      <span v-if="selectedName" class="stock-autocomplete__identity" :title="selectedName">{{ selectedName }}</span>
      <ul
        v-if="dropdownVisible"
        :id="listboxId"
        class="stock-autocomplete__options"
        role="listbox"
        :aria-label="`${label || '股票'}候选`"
      >
        <li v-if="requestState === 'loading'" class="stock-autocomplete__state" role="presentation">正在搜索本地名录…</li>
        <li v-else-if="requestState === 'error'" class="stock-autocomplete__state is-error" role="presentation">{{ requestError }}</li>
        <li
          v-for="(item, index) in results"
          v-else
          :id="`${listboxId}-${index}`"
          :key="item.code"
          class="stock-autocomplete__option"
          :class="{ 'is-active': activeIndex === index }"
          role="option"
          :aria-selected="activeIndex === index"
          @mouseenter="activeIndex = index"
          @pointerdown.prevent
          @click="choose(item)"
        >
          <strong>{{ item.name }}</strong><span class="mono">{{ item.code }}</span>
        </li>
        <li v-if="requestState === 'ready' && !results.length" class="stock-autocomplete__state" role="presentation">
          {{ /^\d{6}$/.test(query) ? '名录未命中；仍可直接提交该代码核验' : '没有匹配股票，请修改输入' }}
        </li>
      </ul>
    </div>
    <small v-if="catalogNotice" class="stock-autocomplete__notice">{{ catalogNotice }}</small>
    <span :id="statusId" class="sr-only" aria-live="polite">{{ liveMessage }}</span>
  </div>
</template>

<style scoped>
.stock-autocomplete { position: relative; display: grid; min-width: 0; gap: 5px; color: var(--text-2); font-size: 13px; font-weight: 680; }
.stock-autocomplete > label { color: var(--text-2); font-size: 13px; font-weight: 700; }
.stock-autocomplete > label b { color: var(--danger); }
.stock-autocomplete__control { position: relative; min-width: 0; }
.stock-autocomplete__control > input { width: 100%; min-width: 0; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); font: inherit; font-variant-numeric: tabular-nums; }
.stock-autocomplete__control > input:focus-visible { border-color: var(--focus); outline: 3px solid color-mix(in srgb, var(--focus) 32%, transparent); outline-offset: 1px; }
.stock-autocomplete__control > input[aria-invalid="true"] { border-color: var(--danger); }
.stock-autocomplete__control > input:read-only { color: var(--text-3); cursor: not-allowed; }
.stock-autocomplete__control > input:disabled { cursor: wait; opacity: .5; }
.stock-autocomplete__control > input.has-identity { padding-right: min(46%, 150px); }
.stock-autocomplete__identity { position: absolute; top: 50%; right: 9px; max-width: 43%; overflow: hidden; padding: 3px 6px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface); color: var(--text-2); font-size: 12px; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; pointer-events: none; transform: translateY(-50%); }
.stock-autocomplete__options { position: absolute; z-index: 40; top: calc(100% + 4px); right: 0; left: 0; display: grid; max-height: min(352px, 46dvh); margin: 0; padding: 4px; overflow-y: auto; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--surface); box-shadow: 0 10px 24px rgb(0 0 0 / 18%); list-style: none; }
.stock-autocomplete__option { display: flex; min-height: 44px; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 9px; border-radius: 5px; color: var(--text); cursor: pointer; }
.stock-autocomplete__option:hover, .stock-autocomplete__option.is-active { background: var(--surface-3); }
.stock-autocomplete__option strong { min-width: 0; overflow-wrap: anywhere; }
.stock-autocomplete__option span { flex: 0 0 auto; color: var(--text-3); font-size: 13px; }
.stock-autocomplete__state { min-height: 44px; padding: 11px 9px; color: var(--text-3); font-weight: 500; }
.stock-autocomplete__state.is-error { color: var(--danger); }
.stock-autocomplete__notice { color: var(--warning); font-size: 12px; font-weight: 500; line-height: 1.45; }
@media (max-width: 560px) {
  .stock-autocomplete__control > input { font-size: 16px; }
  .stock-autocomplete__options { max-height: min(308px, 42dvh); }
}
</style>
