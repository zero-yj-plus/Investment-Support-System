<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import WatchlistEditor from './WatchlistEditor.vue'
import WatchlistItemCard from './WatchlistItemCard.vue'
import { useWatchlist } from '../composables/useWatchlist'
import type { WatchlistInput, WatchlistItem } from '../types/watchlist'
import { compactTime } from '../utils/watchlistFormat'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const showEditor = ref(false)
const editingItem = ref<WatchlistItem | null>(null)
let editorOpener: HTMLElement | null = null
const {
  items,
  loading,
  refreshing,
  verifying,
  saving,
  deletingId,
  recalculating,
  loaded,
  timestamp,
  responseMissing,
  loadError,
  verifyError,
  mutationError,
  feedback,
  actionBusy,
  refresh,
  saveItem,
  deleteItem,
  recalculate,
} = useWatchlist()

const pageRefreshing = computed(() => refreshing.value || actionBusy.value)
const gradeACount = computed(() => items.value.filter((item) => item.grade === 'A').length)
const missingQuoteCount = computed(() => items.value.filter((item) => item.quote?.price == null).length)
const degradedZoneCount = computed(() => items.value.filter((item) => (
  item.ideal_buy_zones?.ultra?.state === 'degraded'
  || item.ideal_buy_zones?.swing?.state === 'degraded'
)).length)
const staleQuoteCount = computed(() => items.value.filter((item) => item.quote?.stale === true).length)
const itemMissingCount = computed(() => items.value.filter((item) => (
  Array.isArray(item.missing) && item.missing.length > 0
)).length)
const zoneDates = computed(() => [...new Set(items.value.flatMap((item) => [
  item.ideal_buy_zones?.ultra?.data_date,
  item.ideal_buy_zones?.swing?.data_date,
]).filter((value): value is string => Boolean(value)))].sort().reverse())
const algorithmVersions = computed(() => [...new Set(items.value.map((item) => (
  String(item.ideal_buy_zones?.algorithm_version || '').trim()
)).filter(Boolean))])
const dataStatus = computed(() => {
  if (loadError.value && !loaded.value) return '接口失败'
  if (loading.value) return '加载中'
  if (loadError.value) return '刷新失败，保留已加载结果'
  if (!items.value.length) return '真实空数组'
  if (
    responseMissing.value.length
    || missingQuoteCount.value
    || degradedZoneCount.value
    || staleQuoteCount.value
    || itemMissingCount.value
  ) return '局部降级'
  return '数据完整'
})
const primaryConclusion = computed(() => {
  if (!items.value.length) return '当前没有观察项，可添加“想买但尚未到价”的标的。'
  if (gradeACount.value) return `${gradeACount.value} 项由服务端评为 A 级；先逐条核对买入前提与不买条件。`
  return '当前没有服务端 A 级项；继续等待区间与触发条件，不以页面推算替代。'
})

watch(pageRefreshing, (value) => emit('refreshState', value), { immediate: true })

function openCreate() {
  editorOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  editingItem.value = null
  showEditor.value = true
}

function openEdit(item: WatchlistItem) {
  editorOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  editingItem.value = item
  showEditor.value = true
}

function closeEditor() {
  showEditor.value = false
  editingItem.value = null
  const opener = editorOpener
  editorOpener = null
  void nextTick(() => opener?.focus())
}

async function handleSave(input: WatchlistInput) {
  const saved = await saveItem(input, String(editingItem.value?.id || ''))
  if (saved) closeEditor()
}

async function handleDelete(item: WatchlistItem) {
  const deleted = await deleteItem(item)
  if (deleted && editingItem.value?.id === item.id) closeEditor()
}

onBeforeUnmount(() => emit('refreshState', false))
defineExpose({ refresh })
</script>

<template>
  <section class="watchlist-page" aria-labelledby="watchlist-page-title" :aria-busy="pageRefreshing">
    <header class="watchlist-intro">
      <div class="page-heading">
        <h1 id="watchlist-page-title">观察池</h1>
        <p class="page-subtitle">管理“想买但尚未买”的标的。评级、状态、价格区间与位置只展示服务端确定性结果，页面不生成买卖结论。</p>
      </div>
    </header>

    <div class="sr-only" aria-live="polite">
      {{ pageRefreshing ? '观察池正在处理请求' : feedback || mutationError || loadError || `观察池状态：${dataStatus}` }}
    </div>

    <div v-if="loading" class="watchlist-loading" role="status" aria-live="polite">
      <span class="sr-only">观察池正在加载</span>
      <section class="panel watchlist-skeleton watchlist-skeleton--summary"></section>
      <section class="panel watchlist-skeleton watchlist-skeleton--item"></section>
    </div>

    <template v-else>
      <section v-if="loadError && !loaded" class="panel watchlist-fatal" role="alert" aria-labelledby="watchlist-fatal-title">
        <p class="eyebrow">读取失败 · 未构造空观察池</p>
        <h2 id="watchlist-fatal-title">观察池暂不可用</h2>
        <p>{{ loadError }}</p>
        <p>页面不会把接口失败解释为真实空数组，也不会显示示例股票或零值。</p>
        <button type="button" class="primary-button" :disabled="refreshing" @click="refresh">重试加载</button>
      </section>

      <template v-else>
        <section class="panel watchlist-summary" aria-labelledby="watchlist-summary-title">
          <div class="watchlist-summary__conclusion">
            <p class="eyebrow">当前确定性结论</p>
            <h2 id="watchlist-summary-title">{{ primaryConclusion }}</h2>
            <p>逐项查看服务端风险等级、生效区间、放弃价、买入前提与不买条件。本接口不提供仓位上限，页面不推断仓位；观察池不是下单指令。</p>
          </div>
          <dl class="watchlist-summary__metrics">
            <div><dt>观察项</dt><dd class="mono">{{ items.length }}</dd></div>
            <div><dt>服务端 A 级</dt><dd class="mono">{{ gradeACount }}</dd></div>
            <div><dt>报价缺失</dt><dd class="mono">{{ missingQuoteCount }}</dd></div>
            <div><dt>降级区间</dt><dd class="mono">{{ degradedZoneCount }}</dd></div>
          </dl>
          <div class="watchlist-summary__actions">
            <button type="button" class="primary-button" :disabled="actionBusy" @click="openCreate">添加观察</button>
            <button type="button" class="secondary-button" :disabled="pageRefreshing" @click="recalculate">
              {{ recalculating ? '提交中' : '重算双周期区间' }}
            </button>
          </div>
        </section>

        <div v-if="loadError" class="watchlist-alert" role="alert">
          <div><strong>自动刷新失败，保留最近已加载内容</strong><span>{{ loadError }}</span></div>
          <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh">重试刷新</button>
        </div>
        <div v-else-if="responseMissing.length" class="watchlist-alert is-warning" role="status">
          <div><strong>接口返回局部缺失</strong><span>{{ responseMissing.join('；') }}</span></div>
        </div>
        <div v-else-if="degradedZoneCount || staleQuoteCount || itemMissingCount" class="watchlist-alert is-warning" role="status">
          <div>
            <strong>观察池含降级、陈旧或缺失字段</strong>
            <span>降级区间 {{ degradedZoneCount }} 项；陈旧报价 {{ staleQuoteCount }} 项；条目缺失 {{ itemMissingCount }} 项。各项原因在卡片中展开。</span>
          </div>
        </div>

        <div v-if="feedback || mutationError" class="watchlist-feedback" :class="{ 'is-error': mutationError }" :role="mutationError ? 'alert' : 'status'">
          <strong>{{ mutationError ? '操作未完成' : '操作状态' }}</strong><span>{{ mutationError || feedback }}</span>
        </div>

        <Teleport to="body">
          <WatchlistEditor
            v-if="showEditor"
            :key="editingItem?.id || 'new'"
            :item="editingItem"
            :busy="verifying || saving"
            :error="verifyError || mutationError"
            @submit="handleSave"
            @cancel="closeEditor"
          />
        </Teleport>

        <section v-if="!items.length" class="panel watchlist-empty" aria-labelledby="watchlist-empty-title">
          <p class="eyebrow">真实空数组</p>
          <h2 id="watchlist-empty-title">当前观察池为空</h2>
          <p>接口成功返回空数组。页面不插入固定股票、示例价格或猜测区间；新增代码会先通过真实单票接口核验。</p>
          <button type="button" class="secondary-button" :disabled="showEditor" @click="openCreate">添加第一只观察标的</button>
        </section>

        <div v-else class="watchlist-grid" aria-label="观察项明细">
          <WatchlistItemCard
            v-for="item in items"
            :key="String(item.id || item.code)"
            :item="item"
            :busy="actionBusy"
            :deleting="deletingId === String(item.id || '')"
            @edit="openEdit"
            @delete="handleDelete"
          />
        </div>

        <footer class="page-footer watchlist-footer">
          <span>读取成功空数组、HTTP 失败、字段缺失、降级快照和陈旧报价分别展示；任何缺失值都不补 0。</span>
          <span>新增/编辑成功只代表账本写入和区间重算已排队；AI 不参与评级、位置、区间或风险边界计算。</span>
        </footer>
      </template>
    </template>

    <dl class="watchlist-stamp" :class="{ 'is-degraded': dataStatus !== '数据完整' }" aria-label="观察池数据日期、关键时点与状态">
      <div><dt>列表时点</dt><dd class="mono">{{ compactTime(timestamp) }}</dd></div>
      <div><dt>区间数据日期</dt><dd class="mono">{{ zoneDates.length ? zoneDates.join(' / ') : '—' }}</dd></div>
      <div><dt>算法版本</dt><dd class="mono">{{ algorithmVersions.length ? algorithmVersions.join(' / ') : '—' }}</dd></div>
      <div><dt>数据状态</dt><dd>{{ dataStatus }}</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.watchlist-page { display: grid; min-width: 0; gap: 12px; }
.watchlist-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.watchlist-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.watchlist-intro > div > p:last-child { max-width: 800px; margin: 7px 0 0; color: var(--text-2); }
.watchlist-stamp { display: grid; min-width: 520px; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px 14px; margin: 0; padding: 9px 11px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.watchlist-stamp.is-degraded { border-color: var(--warning); }
.watchlist-stamp div { min-width: 0; }
.watchlist-stamp dt { color: var(--text-3); font-size: 12px; }
.watchlist-stamp dd { margin: 1px 0 0; font-size: 13px; font-weight: 720; overflow-wrap: anywhere; }
.watchlist-loading { display: grid; gap: 12px; }
.watchlist-skeleton { position: relative; overflow: hidden; background: var(--surface-2); animation: watchlist-pulse 1.1s ease-in-out infinite alternate; }
.watchlist-skeleton--summary { min-height: 220px; }
.watchlist-skeleton--item { min-height: 300px; }
.watchlist-summary { display: grid; grid-template-columns: minmax(280px, 1.5fr) minmax(360px, 1fr) auto; align-items: center; gap: 14px; padding: 14px; border-color: color-mix(in srgb, var(--accent) 38%, var(--border)); }
.watchlist-summary h2 { margin: 0; font-size: 20px; line-height: 1.4; }
.watchlist-summary__conclusion > p:last-child { margin: 6px 0 0; color: var(--text-2); font-size: 13px; }
.watchlist-summary__metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 7px; margin: 0; }
.watchlist-summary__metrics div { min-width: 0; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); text-align: center; }
.watchlist-summary__metrics dt { color: var(--text-3); font-size: 12px; }
.watchlist-summary__metrics dd { margin: 2px 0 0; color: var(--text); font-size: 22px; font-weight: 760; }
.watchlist-summary__actions { display: grid; gap: 8px; }
.watchlist-alert, .watchlist-feedback { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 11px 14px; border: 1px solid var(--danger); border-radius: var(--radius); background: var(--surface); }
.watchlist-alert.is-warning { border-color: var(--warning); }
.watchlist-alert > div, .watchlist-feedback { display: grid; }
.watchlist-alert span, .watchlist-feedback span { color: var(--text-2); }
.watchlist-feedback { border-color: var(--info); }
.watchlist-feedback.is-error { border-color: var(--danger); }
.watchlist-fatal, .watchlist-empty { display: flex; min-height: 320px; flex-direction: column; align-items: center; justify-content: center; padding: 24px; text-align: center; }
.watchlist-fatal h2, .watchlist-empty h2 { margin: 0; font-size: 22px; }
.watchlist-fatal > p:not(.eyebrow), .watchlist-empty > p:not(.eyebrow) { max-width: 680px; margin: 8px 0 0; color: var(--text-2); }
.watchlist-fatal button, .watchlist-empty button { margin-top: 18px; }
.watchlist-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); align-items: start; gap: 10px; }
.watchlist-footer { padding-top: 3px; }
@keyframes watchlist-pulse { from { opacity: .5; } to { opacity: .82; } }
@media (max-width: 1280px) {
  .watchlist-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .watchlist-summary { grid-template-columns: minmax(0, 1fr) minmax(360px, .8fr); }
  .watchlist-summary__actions { grid-column: 1 / -1; grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 1100px) {
  .watchlist-intro { align-items: stretch; flex-direction: column; gap: 12px; }
  .watchlist-stamp { min-width: 0; }
  .watchlist-summary { grid-template-columns: minmax(0, 1fr); }
  .watchlist-summary__metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 1040px) {
  .watchlist-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .watchlist-grid { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 720px) {
  .watchlist-page { gap: 8px; font-size: 18px; }
  .watchlist-stamp, .watchlist-summary__metrics, .watchlist-summary__actions { grid-template-columns: minmax(0, 1fr); }
  .watchlist-alert { align-items: stretch; flex-direction: column; }
  .watchlist-alert .secondary-button, .watchlist-summary__actions button { width: 100%; }
  .watchlist-fatal, .watchlist-empty { min-height: 280px; padding: 18px 14px; }
  .watchlist-empty .secondary-button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .watchlist-skeleton { animation: none; }
}
</style>
