<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import WatchlistEditor from './WatchlistEditor.vue'
import WatchlistImportDialog from './WatchlistImportDialog.vue'
import WatchlistItemRow from './WatchlistItemRow.vue'
import { useWatchlist } from '../composables/useWatchlist'
import type { WatchlistImportItem, WatchlistInput, WatchlistItem } from '../types/watchlist'
import { compactTime } from '../utils/watchlistFormat'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const showEditor = ref(false)
const showImporter = ref(false)
const editingItem = ref<WatchlistItem | null>(null)
let editorOpener: HTMLElement | null = null
let importerOpener: HTMLElement | null = null
const {
  items,
  loading,
  refreshing,
  verifying,
  saving,
  importing,
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
  clearMutationError,
  refresh,
  saveItem,
  importItems,
  deleteItem,
  recalculate,
} = useWatchlist()

const pageRefreshing = computed(() => refreshing.value || actionBusy.value)
const missingQuoteCount = computed(() => items.value.filter((item) => item.quote?.price == null).length)
const degradedZoneCount = computed(() => items.value.filter((item) => (
  item.ideal_buy_zones?.ultra?.state === 'degraded'
  || item.ideal_buy_zones?.swing?.state === 'degraded'
)).length)
const staleQuoteCount = computed(() => items.value.filter((item) => item.quote?.stale === true).length)
const existingCodes = computed(() => items.value.map((item) => String(item.code || '').trim()).filter(Boolean))
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

function openImporter() {
  importerOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  clearMutationError()
  showImporter.value = true
}

function closeEditor() {
  showEditor.value = false
  editingItem.value = null
  const opener = editorOpener
  editorOpener = null
  void nextTick(() => opener?.focus())
}

function closeImporter() {
  showImporter.value = false
  const opener = importerOpener
  importerOpener = null
  void nextTick(() => opener?.focus())
}

async function handleSave(input: WatchlistInput) {
  const saved = await saveItem(input, String(editingItem.value?.id || ''))
  if (saved) closeEditor()
}

async function handleImport(candidates: WatchlistImportItem[]) {
  const imported = await importItems(candidates)
  if (imported) closeImporter()
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
      <div class="watchlist-intro__actions">
        <button type="button" class="secondary-button" :disabled="!loaded || pageRefreshing" @click="recalculate">
          {{ recalculating ? '提交中' : '重算双周期区间' }}
        </button>
        <button type="button" class="secondary-button" :disabled="!loaded || pageRefreshing" @click="openImporter">
          {{ importing ? '导入中…' : '批量导入' }}
        </button>
        <button type="button" class="primary-button" :disabled="!loaded || actionBusy" @click="openCreate">添加观察</button>
      </div>
    </header>

    <div class="sr-only" aria-live="polite">
      {{ pageRefreshing ? '观察池正在处理请求' : feedback || mutationError || loadError || `观察池状态：${dataStatus}` }}
    </div>

    <div v-if="loading" class="watchlist-loading" role="status" aria-live="polite">
      <span class="sr-only">观察池正在加载</span>
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
            <span>降级区间 {{ degradedZoneCount }} 项；陈旧报价 {{ staleQuoteCount }} 项；条目缺失 {{ itemMissingCount }} 项。各项原因可在对应行展开。</span>
          </div>
        </div>

        <div v-if="feedback || mutationError" class="watchlist-feedback" :class="{ 'is-error': mutationError }" :role="mutationError ? 'alert' : 'status'">
          <strong>{{ mutationError ? '操作未完成' : '操作状态' }}</strong><span>{{ mutationError || feedback }}</span>
        </div>

        <Teleport to="body">
          <WatchlistImportDialog
            v-if="showImporter"
            :existing-codes="existingCodes"
            :busy="importing"
            :error="mutationError"
            @submit="handleImport"
            @cancel="closeImporter"
          />
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

        <div v-else class="watchlist-list" aria-label="观察项明细">
          <div class="watchlist-list__head" aria-hidden="true">
            <span>评级</span>
            <span>标的</span>
            <span>状态</span>
            <span>当前价</span>
            <span>生效理想区间</span>
            <span>超短线 · 1—3日</span>
            <span>波段 · 3—10日</span>
            <span>风险边界</span>
            <span>操作</span>
          </div>
          <WatchlistItemRow
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

    <dl class="watchlist-stamp data-footnote-stamp" :class="{ 'is-degraded': dataStatus !== '数据完整' }" aria-label="观察池数据日期、关键时点与状态">
      <div><dt>列表时点</dt><dd class="mono">{{ compactTime(timestamp) }}</dd></div>
      <div><dt>区间数据日期</dt><dd class="mono">{{ zoneDates.length ? zoneDates.join(' / ') : '—' }}</dd></div>
      <div><dt>算法版本</dt><dd class="mono">{{ algorithmVersions.length ? algorithmVersions.join(' / ') : '—' }}</dd></div>
      <div><dt>数据状态</dt><dd>{{ dataStatus }}</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.watchlist-page { --watchlist-head-bg: #e7ecf2; --watchlist-head-text: #111827; --watchlist-head-border: #c5ced8; display: grid; min-width: 0; gap: 12px; }
.watchlist-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.watchlist-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.watchlist-intro > div > p:last-child { max-width: 800px; margin: 7px 0 0; color: var(--text-2); }
.watchlist-intro__actions { display: grid; flex: 0 0 auto; grid-template-columns: repeat(3, max-content); gap: 8px; }
.watchlist-stamp { display: grid; min-width: 520px; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px 14px; margin: 0; padding: 9px 11px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.watchlist-stamp.is-degraded { border-color: var(--warning); }
.watchlist-stamp div { min-width: 0; }
.watchlist-stamp dt { color: var(--text-3); font-size: 12px; }
.watchlist-stamp dd { margin: 1px 0 0; font-size: 13px; font-weight: 720; overflow-wrap: anywhere; }
.watchlist-loading { display: grid; gap: 12px; }
.watchlist-skeleton { position: relative; overflow: hidden; background: var(--surface-2); animation: watchlist-pulse 1.1s ease-in-out infinite alternate; }
.watchlist-skeleton--item { min-height: 220px; }
.watchlist-alert, .watchlist-feedback { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 11px 14px; border: 1px solid var(--danger); border-radius: var(--radius); background: var(--surface); }
.watchlist-alert.is-warning { border-color: var(--warning); }
.watchlist-alert > div, .watchlist-feedback { display: grid; }
.watchlist-alert.is-warning > div { display: flex; min-width: 0; gap: 8px; overflow-x: auto; white-space: nowrap; }
.watchlist-alert span, .watchlist-feedback span { color: var(--text-2); }
.watchlist-feedback { border-color: var(--info); }
.watchlist-feedback.is-error { border-color: var(--danger); }
.watchlist-fatal, .watchlist-empty { display: flex; min-height: 320px; flex-direction: column; align-items: center; justify-content: center; padding: 24px; text-align: center; }
.watchlist-fatal h2, .watchlist-empty h2 { margin: 0; font-size: 22px; }
.watchlist-fatal > p:not(.eyebrow), .watchlist-empty > p:not(.eyebrow) { max-width: 680px; margin: 8px 0 0; color: var(--text-2); }
.watchlist-fatal button, .watchlist-empty button { margin-top: 18px; }
.watchlist-list { --watchlist-columns: 52px minmax(132px, 1.05fr) minmax(132px, 1fr) minmax(82px, .62fr) minmax(122px, .95fr) minmax(122px, .95fr) minmax(122px, .95fr) minmax(104px, .75fr) 168px; min-width: 0; overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.watchlist-list__head { display: grid; min-height: 40px; grid-template-columns: var(--watchlist-columns); align-items: stretch; background: var(--watchlist-head-bg); color: var(--watchlist-head-text); font-size: 12px; font-weight: 760; }
.watchlist-list__head span { display: flex; min-width: 0; align-items: center; padding: 7px 10px; border-right: 1px solid var(--watchlist-head-border); overflow-wrap: anywhere; }
.watchlist-list__head span:last-child { justify-content: flex-end; border-right: 0; }
.watchlist-footer { padding-top: 3px; }
@keyframes watchlist-pulse { from { opacity: .5; } to { opacity: .82; } }
@media (max-width: 1100px) {
  .watchlist-intro { align-items: stretch; flex-direction: column; gap: 12px; }
  .watchlist-intro__actions { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .watchlist-stamp { min-width: 0; }
}
@media (max-width: 900px) {
  .watchlist-list__head { display: none; }
}
@media (max-width: 720px) {
  .watchlist-page { gap: 8px; font-size: 18px; }
  .watchlist-stamp { grid-template-columns: minmax(0, 1fr); }
  .watchlist-alert { align-items: stretch; flex-direction: column; }
  .watchlist-alert .secondary-button, .watchlist-intro__actions button { width: 100%; }
  .watchlist-fatal, .watchlist-empty { min-height: 280px; padding: 18px 14px; }
  .watchlist-empty .secondary-button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .watchlist-skeleton { animation: none; }
}
</style>
