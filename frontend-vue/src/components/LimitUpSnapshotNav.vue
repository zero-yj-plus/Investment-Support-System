<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { LimitUpBackfillResponse, LimitUpTrackerDateItem } from '../types/limitupTracker'

const props = defineProps<{
  dates: LimitUpTrackerDateItem[]
  selectedDate: string
  pendingDate: string
  disabled: boolean
  backfill: LimitUpBackfillResponse | null
}>()
const emit = defineEmits<{ select: [date: string]; backfill: [] }>()
const root = ref<HTMLElement | null>(null)
const recentButton = ref<HTMLButtonElement | null>(null)
const open = ref(false)
const topDates = computed(() => props.dates.slice(0, 3))

function dateLabel(value: string): string {
  const parsed = new Date(`${value}T12:00:00+08:00`)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', weekday: 'short' }).format(parsed)
}

function stateLabel(item: LimitUpTrackerDateItem): string {
  if (item.missing_label) return item.missing_label
  const labels: Record<string, string> = {
    intraday: '盘中', tracking: '追踪中', complete: '已冻结', missing: '快照缺失',
  }
  return labels[item.snapshot_state] || item.snapshot_state
}

function choose(date: string) {
  open.value = false
  emit('select', date)
}

function close(restoreFocus = false) {
  if (!open.value) return
  open.value = false
  if (restoreFocus) void nextTick(() => recentButton.value?.focus())
}

function onPointerDown(event: PointerEvent) {
  if (open.value && root.value && !root.value.contains(event.target as Node)) close()
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && open.value) close(true)
}

onMounted(() => {
  document.addEventListener('pointerdown', onPointerDown)
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onPointerDown)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <section ref="root" class="tracker-nav panel" aria-label="涨停基准日快照导航">
    <div class="tracker-nav__dates" aria-label="最近三个交易日">
      <button
        v-for="(item, index) in topDates"
        :key="item.date"
        type="button"
        class="tracker-date-button"
        :class="{ 'is-selected': item.date === selectedDate, 'is-pending': item.date === pendingDate, 'is-missing': Boolean(item.missing_label) }"
        :aria-pressed="item.date === selectedDate"
        :disabled="disabled"
        @click="choose(item.date)"
      >
        <span>最近第{{ index + 1 }}个交易日</span>
        <strong class="mono">{{ dateLabel(item.date) }}</strong>
        <small>{{ item.date === pendingDate ? '切换中' : stateLabel(item) }}</small>
      </button>
    </div>

    <div class="tracker-nav__actions">
      <div class="tracker-history">
        <button
          ref="recentButton"
          type="button"
          class="tracker-action-button"
          aria-controls="limitup-recent-20-panel"
          :aria-expanded="open"
          :disabled="disabled"
          @click="open = !open"
        >最近20日</button>
        <div v-if="open" id="limitup-recent-20-panel" class="tracker-history__popover" role="dialog" aria-label="最近20个交易日">
          <header><strong>选择涨停基准日</strong><span>日期来自服务端交易日历</span></header>
          <div class="tracker-history__grid">
            <button
              v-for="item in dates"
              :key="item.date"
              type="button"
              :class="{ 'is-selected': item.date === selectedDate, 'is-missing': Boolean(item.missing_label) }"
              :aria-pressed="item.date === selectedDate"
              @click="choose(item.date)"
            >
              <strong class="mono">{{ item.date }}</strong>
              <span>{{ stateLabel(item) }}</span>
            </button>
          </div>
        </div>
      </div>
      <button
        type="button"
        class="tracker-action-button tracker-backfill-button"
        :disabled="disabled || backfill?.status === 'running'"
        @click="emit('backfill')"
      >{{ backfill?.status === 'running' ? '补数处理中' : '补数据' }}</button>
    </div>
    <p v-if="backfill?.message || backfill?.reason" class="tracker-backfill-state" :class="`is-${backfill.status}`" aria-live="polite">
      {{ backfill.message || backfill.reason }}
    </p>
  </section>
</template>

<style scoped>
.tracker-nav { position: sticky; top: 64px; z-index: 12; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: stretch; gap: 8px; padding: 8px; background: color-mix(in srgb, var(--surface) 96%, transparent); }
.tracker-nav__dates { display: grid; min-width: 0; grid-template-columns: repeat(3, minmax(148px, 1fr)); gap: 8px; }
.tracker-date-button, .tracker-action-button { min-height: 44px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); cursor: pointer; transition: border-color var(--ease), background var(--ease), color var(--ease); }
.tracker-date-button { display: grid; min-width: 0; grid-template-columns: minmax(0, 1fr) auto; align-content: center; gap: 1px 8px; padding: 7px 10px; text-align: left; }
.tracker-date-button > span { grid-column: 1 / -1; color: var(--text-3); font-size: 11px; }
.tracker-date-button strong { overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.tracker-date-button small { color: var(--text-3); font-size: 11px; white-space: nowrap; }
.tracker-date-button:hover, .tracker-action-button:hover { border-color: var(--border-strong); background: var(--surface-3); }
.tracker-date-button.is-selected { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); }
.tracker-date-button.is-selected small { color: currentColor; }
.tracker-date-button.is-missing:not(.is-selected) { border-style: dashed; }
.tracker-date-button.is-pending { border-color: var(--warning); }
.tracker-date-button:disabled, .tracker-action-button:disabled { cursor: wait; opacity: .62; }
.tracker-nav__actions { display: grid; grid-template-columns: repeat(2, minmax(104px, auto)); gap: 8px; }
.tracker-action-button { padding: 0 14px; font-weight: 700; white-space: nowrap; }
.tracker-backfill-button { border-color: color-mix(in srgb, var(--accent) 54%, var(--border)); }
.tracker-history { position: relative; }
.tracker-history > .tracker-action-button { width: 100%; }
.tracker-history__popover { position: absolute; top: calc(100% + 7px); right: 0; z-index: 30; width: min(540px, calc(100vw - 32px)); padding: 10px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--surface); }
.tracker-history__popover header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; padding: 2px 2px 9px; border-bottom: 1px solid var(--border); }
.tracker-history__popover header span { color: var(--text-3); font-size: 12px; }
.tracker-history__grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; padding-top: 8px; }
.tracker-history__grid button { display: grid; min-height: 52px; align-content: center; gap: 1px; padding: 5px 7px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); text-align: left; cursor: pointer; }
.tracker-history__grid button:hover { border-color: var(--border-strong); background: var(--surface-3); }
.tracker-history__grid button.is-selected { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); }
.tracker-history__grid button.is-missing:not(.is-selected) { border-style: dashed; }
.tracker-history__grid strong { font-size: 12px; }
.tracker-history__grid span { color: var(--text-3); font-size: 11px; }
.tracker-history__grid .is-selected span { color: currentColor; }
.tracker-backfill-state { grid-column: 1 / -1; margin: 0; padding: 5px 8px 0; border-top: 1px solid var(--border); color: var(--text-2); font-size: 12px; }
.tracker-backfill-state.is-failed { color: var(--danger); }
.tracker-backfill-state.is-partial { color: var(--warning); }
@media (max-width: 940px) {
  .tracker-nav { position: relative; top: auto; grid-template-columns: 1fr; }
  .tracker-nav__actions { grid-template-columns: 1fr 1fr; }
  .tracker-history__popover { right: auto; left: 0; }
}
@media (max-width: 680px) {
  .tracker-nav__dates { grid-template-columns: 1fr; }
  .tracker-history__grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (prefers-reduced-motion: reduce) {
  .tracker-date-button, .tracker-action-button { transition: none; }
}
</style>
