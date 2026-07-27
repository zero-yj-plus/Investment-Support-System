<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ScreenerResponse, ScreenerStock, WatchlistActionState } from '../types/screener'
import { formatInteger, formatMoney, formatNumber, formatPercent } from '../utils/screenerFormat'

const props = defineProps<{
  response: ScreenerResponse | null
  stocks: ScreenerStock[]
  hasSubmitted: boolean
  loading: boolean
  showLoading: boolean
  elapsedSeconds: number
  transportError: string
  stale: boolean
  watchlistStates: Record<string, WatchlistActionState>
  watchlistErrors: Record<string, string>
}>()

const emit = defineEmits<{ retry: []; stop: []; addWatchlist: [stock: ScreenerStock] }>()

type ResultSortKey = 'price' | 'change_pct' | 'turnover_rate' | 'volume_ratio' | 'pe_ttm' | 'pb' | 'circ_mv' | 'amount'

const sortKey = ref<ResultSortKey | null>(null)
const sortDirection = ref<'ascending' | 'descending'>('descending')

function sortValue(stock: ScreenerStock, key: ResultSortKey): number | null {
  const raw = key === 'pe_ttm' ? stock.pe_ttm ?? stock.pe : stock[key]
  if (raw == null) return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : null
}

const visibleStocks = computed(() => [...props.stocks].sort((left, right) => {
  if (!sortKey.value) return 0
  const leftValue = sortValue(left, sortKey.value)
  const rightValue = sortValue(right, sortKey.value)
  if (leftValue == null && rightValue == null) return 0
  if (leftValue == null) return 1
  if (rightValue == null) return -1
  return (sortDirection.value === 'descending' ? -1 : 1) * (leftValue - rightValue)
}))

function sortBy(key: ResultSortKey) {
  if (sortKey.value === key) sortDirection.value = sortDirection.value === 'descending' ? 'ascending' : 'descending'
  else {
    sortKey.value = key
    sortDirection.value = 'descending'
  }
}

function ariaSort(key: ResultSortKey): 'none' | 'ascending' | 'descending' {
  if (sortKey.value !== key) return 'none'
  return sortDirection.value
}

function sortLabel(key: ResultSortKey, label: string): string {
  if (sortKey.value !== key) return `${label}排序，当前未排序；首次点击按从高到低`
  return `${label}排序，当前${sortDirection.value === 'descending' ? '降序' : '升序'}`
}

function diagnosisHref(code: string | null | undefined): string {
  return `#/research?tab=diagnosis&code=${encodeURIComponent(code || '')}`
}

function actionLabel(state: WatchlistActionState | undefined): string {
  if (state === 'adding') return '提交中'
  if (state === 'queued') return '已提交'
  if (state === 'added') return '已加入'
  if (state === 'error') return '重试加入'
  return '加入观察池'
}

function isActionDisabled(state: WatchlistActionState | undefined): boolean {
  return state === 'adding' || state === 'queued' || state === 'added'
}
</script>

<template>
  <section class="panel screener-results" aria-labelledby="screener-results-title" :aria-busy="loading">
    <header class="screener-results-heading">
      <div><p class="eyebrow">结构化证据</p><h2 id="screener-results-title">筛选结果</h2></div>
      <div class="screener-result-tools">
        <span v-if="response" class="mono">命中 {{ formatInteger(response.matched) }} · 返回 {{ formatInteger(response.returned) }}</span>
        <span v-else>尚无筛选批次</span>
        <small>点击数值列表头排序 · 仅重排当前返回结果</small>
      </div>
    </header>

    <div v-if="transportError" class="screener-result-alert" role="alert">
      <div><strong>{{ response ? '新一轮筛选局部失败' : '筛选接口暂不可用' }}</strong><span>{{ transportError }}</span></div>
      <button v-if="!loading" type="button" class="secondary-button" @click="emit('retry')">重新筛选</button>
    </div>

    <div v-if="loading && showLoading" class="screener-loading" role="status" aria-live="polite">
      <div>
        <span class="screener-loading-dot" aria-hidden="true"></span>
        <div><strong>正在读取全市场快照并执行确定性筛选</strong><small>已等待 {{ elapsedSeconds }} 秒 · 最长 30 秒；缺失指标不会补 0。</small></div>
        <button type="button" class="secondary-button" @click="emit('stop')">停止本次</button>
      </div>
      <div v-if="!response" class="screener-loading-grid" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></div>
    </div>
    <div v-else-if="loading && !response" class="screener-loading-pending" role="status"><span class="sr-only">条件选股请求已开始</span></div>

    <div v-if="stale && response" class="screener-result-stale" role="status">当前条件与下方结果批次不一致；结果仅作为上次筛选留存。</div>

    <div v-if="!hasSubmitted && !response" class="screener-result-state is-idle">
      <p class="eyebrow">WAITING FOR FILTERS</p>
      <h3>尚未运行条件筛选</h3>
      <p>填写条件后点击页面上方“开始筛选”。页面不会预载全市场结果，也不会展示示例股票或演示数字。</p>
    </div>

    <div v-else-if="response?.ok === false" class="screener-result-state is-unavailable" role="status">
      <h3>全市场筛选不可用</h3>
      <p>{{ response.error || '股票池或行情覆盖未达到规则门槛，本批次没有可执行结果。' }}</p>
      <ul v-if="response.missing?.length"><li v-for="item in response.missing" :key="item">{{ item }}</li></ul>
      <button v-if="!loading" type="button" class="secondary-button" @click="emit('retry')">重新筛选</button>
    </div>

    <div v-else-if="response && stocks.length === 0" class="screener-result-state is-empty" role="status">
      <h3>筛选结果为真实空数组</h3>
      <p v-if="response.missing?.length">本批次没有命中，同时存在局部缺失；不能据此断言全市场没有候选。</p>
      <p v-else>可用股票池达到规则门槛，但当前条件没有命中。可调整条件后重新执行。</p>
      <ul v-if="response.missing?.length"><li v-for="item in response.missing" :key="item">{{ item }}</li></ul>
    </div>

    <div v-else-if="response && stocks.length" class="screener-table-wrap" tabindex="0" aria-label="条件选股结果表；桌面端可在容器内横向滚动，移动端按卡片阅读">
      <table class="screener-table">
        <caption class="sr-only">条件选股返回明细</caption>
        <thead>
          <tr>
            <th scope="col">股票</th>
            <th scope="col" :aria-sort="ariaSort('price')"><button type="button" class="screener-sort-button" :class="{ 'is-active': sortKey === 'price' }" :aria-label="sortLabel('price', '现价')" @click="sortBy('price')">现价 <span aria-hidden="true">{{ sortKey === 'price' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col" :aria-sort="ariaSort('change_pct')"><button type="button" class="screener-sort-button" :class="{ 'is-active': sortKey === 'change_pct' }" :aria-label="sortLabel('change_pct', '涨跌幅')" @click="sortBy('change_pct')">涨跌幅 <span aria-hidden="true">{{ sortKey === 'change_pct' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col" :aria-sort="ariaSort('turnover_rate')"><button type="button" class="screener-sort-button" :class="{ 'is-active': sortKey === 'turnover_rate' }" :aria-label="sortLabel('turnover_rate', '换手率')" @click="sortBy('turnover_rate')">换手率 <span aria-hidden="true">{{ sortKey === 'turnover_rate' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col" :aria-sort="ariaSort('volume_ratio')"><button type="button" class="screener-sort-button" :class="{ 'is-active': sortKey === 'volume_ratio' }" :aria-label="sortLabel('volume_ratio', '量比')" @click="sortBy('volume_ratio')">量比 <span aria-hidden="true">{{ sortKey === 'volume_ratio' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col" :aria-sort="ariaSort('pe_ttm')"><button type="button" class="screener-sort-button" :class="{ 'is-active': sortKey === 'pe_ttm' }" :aria-label="sortLabel('pe_ttm', 'PE TTM')" @click="sortBy('pe_ttm')">PE TTM <span aria-hidden="true">{{ sortKey === 'pe_ttm' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col" :aria-sort="ariaSort('pb')"><button type="button" class="screener-sort-button" :class="{ 'is-active': sortKey === 'pb' }" :aria-label="sortLabel('pb', 'PB')" @click="sortBy('pb')">PB <span aria-hidden="true">{{ sortKey === 'pb' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col" :aria-sort="ariaSort('circ_mv')"><button type="button" class="screener-sort-button" :class="{ 'is-active': sortKey === 'circ_mv' }" :aria-label="sortLabel('circ_mv', '流通市值')" @click="sortBy('circ_mv')">流通市值 <span aria-hidden="true">{{ sortKey === 'circ_mv' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col" :aria-sort="ariaSort('amount')"><button type="button" class="screener-sort-button" :class="{ 'is-active': sortKey === 'amount' }" :aria-label="sortLabel('amount', '成交额')" @click="sortBy('amount')">成交额 <span aria-hidden="true">{{ sortKey === 'amount' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col">行业 / 板块</th><th scope="col">观察池</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="stock in visibleStocks" :key="stock.code || `${stock.name}-${stock.price}`">
            <td data-label="股票">
              <a v-if="stock.code" class="screener-stock-link" :href="diagnosisHref(stock.code)">
                <strong>{{ stock.name || '名称缺失' }}</strong><span class="mono">{{ stock.code }}</span>
              </a>
              <span v-else>代码缺失</span>
            </td>
            <td data-label="现价" class="mono" :aria-label="stock.price == null ? '现价：数据缺失' : undefined">{{ formatNumber(stock.price) }}<span v-if="stock.price != null"> 元</span></td>
            <td data-label="涨跌幅" class="mono" :class="stock.change_pct == null ? '' : stock.change_pct > 0 ? 'is-up' : stock.change_pct < 0 ? 'is-down' : 'is-flat'" :aria-label="stock.change_pct == null ? '涨跌幅：数据缺失' : undefined">{{ formatPercent(stock.change_pct, 2, true) }}</td>
            <td data-label="换手率" class="mono" :aria-label="stock.turnover_rate == null ? '换手率：数据缺失' : undefined">{{ formatPercent(stock.turnover_rate) }}</td>
            <td data-label="量比" class="mono" :aria-label="stock.volume_ratio == null ? '量比：数据缺失' : undefined">{{ formatNumber(stock.volume_ratio) }}</td>
            <td data-label="PE TTM" class="mono" :aria-label="stock.pe_ttm == null && stock.pe == null ? '市盈率：数据缺失' : undefined">{{ formatNumber(stock.pe_ttm ?? stock.pe, 1) }}</td>
            <td data-label="PB" class="mono" :aria-label="stock.pb == null ? '市净率：数据缺失' : undefined">{{ formatNumber(stock.pb) }}</td>
            <td data-label="流通市值" class="mono" :aria-label="stock.circ_mv == null ? '流通市值：数据缺失' : undefined">{{ formatMoney(stock.circ_mv) }}</td>
            <td data-label="成交额" class="mono" :aria-label="stock.amount == null ? '成交额：数据缺失' : undefined">{{ formatMoney(stock.amount) }}</td>
            <td data-label="行业 / 板块"><strong>{{ stock.industry || '行业缺失' }}</strong><small>{{ stock.board || '板块缺失' }}</small></td>
            <td data-label="观察池" class="screener-watch-action">
              <button type="button" class="secondary-button" :disabled="!stock.code || isActionDisabled(watchlistStates[String(stock.code || '')])" @click="emit('addWatchlist', stock)">{{ actionLabel(watchlistStates[String(stock.code || '')]) }}</button>
              <small v-if="watchlistStates[String(stock.code || '')] === 'queued'" role="status">平台已接受；区间计算已排队，尚未完成。</small>
              <small v-else-if="watchlistErrors[String(stock.code || '')]" class="is-error" role="alert">{{ watchlistErrors[String(stock.code || '')] }}</small>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.screener-results { min-width: 0; overflow: hidden; }
.screener-results-heading { display: flex; min-height: 68px; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.screener-results-heading h2 { margin: 0; font-size: 18px; }
.screener-result-tools { display: grid; min-width: 0; justify-items: end; gap: 2px; }
.screener-result-tools > span { color: var(--text-3); font-size: 13px; white-space: nowrap; }
.screener-result-tools > small { color: var(--text-3); font-size: 12px; }
.screener-result-alert, .screener-loading > div:first-child { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin: 12px; padding: 10px 12px; border: 1px solid var(--danger); border-radius: var(--radius-sm); background: color-mix(in srgb, var(--danger) 7%, var(--surface)); }
.screener-result-alert > div { display: grid; gap: 2px; }
.screener-result-alert span { color: var(--text-2); font-size: 13px; }
.screener-loading > div:first-child { border-color: var(--warning); background: color-mix(in srgb, var(--warning) 7%, var(--surface)); }
.screener-loading > div:first-child > div { display: grid; flex: 1; gap: 2px; }
.screener-loading small { color: var(--text-3); }
.screener-loading-dot { width: 12px; height: 12px; flex: 0 0 auto; border: 2px solid var(--warning); border-top-color: transparent; border-radius: 50%; animation: screener-spin .8s linear infinite; }
.screener-loading-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; padding: 0 12px 12px; }
.screener-loading-grid i { min-height: 78px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); animation: screener-pulse 1.1s ease-in-out infinite alternate; }
.screener-loading-pending { min-height: 260px; }
.screener-result-stale { margin: 12px; padding: 8px 10px; border-left: 3px solid var(--warning); background: color-mix(in srgb, var(--warning) 7%, var(--surface)); color: var(--text-2); font-size: 13px; }
.screener-result-state { display: grid; min-height: 260px; align-content: center; justify-items: center; gap: 7px; padding: 24px; text-align: center; }
.screener-result-state h3 { margin: 0; font-size: 20px; }
.screener-result-state p { max-width: 700px; margin: 0; color: var(--text-2); }
.screener-result-state ul { max-width: 720px; margin: 4px 0; padding-left: 22px; color: var(--text-3); text-align: left; }
.screener-result-state.is-unavailable h3 { color: var(--danger); }
.screener-table-wrap { max-width: 100%; overflow-x: auto; overscroll-behavior-x: contain; }
.screener-table { width: 100%; min-width: 1280px; table-layout: fixed; border-collapse: collapse; font-size: 13px; }
.screener-table th, .screener-table td { min-width: 0; padding: 9px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: middle; }
.screener-table th { background: var(--surface-2); color: var(--text-3); font-size: 12px; font-weight: 750; white-space: nowrap; }
.screener-sort-button { display: inline-flex; width: 100%; min-height: 44px; align-items: center; justify-content: space-between; gap: 4px; margin: -5px 0; padding: 5px 7px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--surface-3); color: var(--text); font-size: 12px; font-weight: 750; cursor: pointer; }
.screener-sort-button:hover { border-color: var(--accent); color: var(--accent); }
.screener-sort-button.is-active { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); }
.screener-sort-button span { display: inline-grid; width: 20px; height: 20px; flex: 0 0 auto; place-items: center; border: 1px solid currentColor; border-radius: 4px; font-family: "SFMono-Regular", Consolas, monospace; }
.screener-table th:first-child { width: 150px; }
.screener-table th:nth-last-child(2) { width: 132px; }
.screener-table th:last-child { width: 160px; }
.screener-table tbody tr:last-child td { border-bottom: 0; }
.screener-table td { color: var(--text-2); }
.screener-table td.mono { color: var(--text); font-size: 14px; font-weight: 720; }
.screener-stock-link { display: inline-flex; min-height: 44px; flex-direction: column; justify-content: center; color: var(--text); text-decoration: none; }
.screener-stock-link:hover { color: var(--accent); }
.screener-stock-link span { color: var(--accent); font-size: 12px; }
.screener-table td[data-label="行业 / 板块"] { display: table-cell; }
.screener-table td[data-label="行业 / 板块"] strong, .screener-table td[data-label="行业 / 板块"] small { display: block; }
.screener-table td[data-label="行业 / 板块"] small { margin-top: 2px; color: var(--text-3); }
.screener-watch-action { display: grid; gap: 4px; }
.screener-watch-action .secondary-button { width: 100%; padding-inline: 8px; font-size: 12px; }
.screener-watch-action small { color: var(--text-3); font-size: 12px; }
.screener-watch-action small.is-error { color: var(--danger); }
@keyframes screener-spin { to { transform: rotate(360deg); } }
@keyframes screener-pulse { from { opacity: .45; } to { opacity: .82; } }
@media (max-width: 720px) {
  .screener-results-heading { align-items: flex-start; flex-direction: column; gap: 6px; }
  .screener-result-tools { width: 100%; justify-items: start; }
  .screener-result-alert, .screener-loading > div:first-child { align-items: stretch; flex-direction: column; }
  .screener-result-alert .secondary-button, .screener-loading .secondary-button { width: 100%; }
  .screener-loading-grid { grid-template-columns: minmax(0, 1fr); }
  .screener-table-wrap { overflow: visible; }
  .screener-table { min-width: 0; }
  .screener-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .screener-table, .screener-table tbody, .screener-table tr, .screener-table td { display: block; width: 100%; }
  .screener-table tbody { display: grid; gap: 8px; padding: 8px; }
  .screener-table tbody tr { overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
  .screener-table td, .screener-table td[data-label="行业 / 板块"] { display: grid; min-height: 48px; grid-template-columns: minmax(108px, .42fr) minmax(0, 1fr); align-items: center; gap: 10px; padding: 9px 11px; background: transparent; }
  .screener-table td::before { color: var(--text-3); font-size: 14px; font-weight: 680; content: attr(data-label); }
  .screener-table td[data-label="行业 / 板块"] strong, .screener-table td[data-label="行业 / 板块"] small { grid-column: 2; }
  .screener-table td[data-label="行业 / 板块"] small { margin-top: -8px; }
  .screener-watch-action .secondary-button, .screener-watch-action small { grid-column: 2; }
}
@media (prefers-reduced-motion: reduce) { .screener-loading-dot, .screener-loading-grid i { animation: none; } }
</style>
