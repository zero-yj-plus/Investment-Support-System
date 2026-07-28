<script setup lang="ts">
import type { ForecastItem, ForecastPool } from '../types/forecastMining'
import { finiteNumber, formatCompactDate, formatMoney, formatMoneyRange, formatPercentRange } from '../utils/forecastFormat'

defineProps<{
  rows: ForecastItem[]
  sourceCount: number
  pool: ForecastPool
  selectedType: string
  searchCode: string
}>()

const emit = defineEmits<{
  changePool: [pool: ForecastPool]
  updateSearchCode: [value: string]
}>()

function onSearchInput(event: Event) {
  emit('updateSearchCode', (event.target as HTMLInputElement).value)
}

function diagnosisHref(code: string | undefined): string {
  return `#/research?tab=diagnosis&code=${encodeURIComponent(code || '')}`
}

function percentTone(row: ForecastItem): string {
  const value = finiteNumber(row.p_chg_min) ?? finiteNumber(row.p_chg_max)
  if (value == null) return ''
  return value >= 0 ? 'is-up' : 'is-down'
}

function layerTone(layer: string | undefined): string {
  if (layer === '主动交卷' || layer === '提前披露') return 'is-positive'
  if (layer === '压哨' || layer === '超期/更正') return 'is-warning'
  return 'is-neutral'
}

function reason(row: ForecastItem): string {
  return String(row.reason || row.summary || '').trim() || '—'
}
</script>

<template>
  <section class="panel forecast-results" aria-label="候选与陷阱明细">
    <header class="forecast-results__head">
      <form class="forecast-code-search" role="search" @submit.prevent>
        <label for="forecast-code-search">股票代码搜索</label>
        <div>
          <input
            id="forecast-code-search"
            :value="searchCode"
            type="search"
            inputmode="numeric"
            pattern="[0-9]*"
            maxlength="6"
            autocomplete="off"
            placeholder="输入股票代码"
            @input="onSearchInput"
          />
          <button v-if="searchCode" type="button" aria-label="清空股票代码搜索" @click="emit('updateSearchCode', '')">清空</button>
        </div>
      </form>
      <div class="forecast-pool-switch" aria-label="榜单分池">
        <button type="button" :aria-pressed="pool === 'items'" :class="{ active: pool === 'items' }" @click="emit('changePool', 'items')">掘金池</button>
        <button type="button" :aria-pressed="pool === 'traps'" :class="{ active: pool === 'traps' }" @click="emit('changePool', 'traps')">陷阱池</button>
      </div>
    </header>

    <div v-if="!rows.length" class="forecast-pool-empty" role="status">
      <strong>{{ sourceCount ? '当前筛选没有匹配标的' : pool === 'items' ? '当前掘金池为空' : '当前陷阱池为空' }}</strong>
      <span v-if="sourceCount">{{ selectedType ? `预告类型：${selectedType}` : '全部预告类型' }}{{ searchCode ? ` · 股票代码包含：${searchCode}` : '' }}。清除筛选可恢复当前分池结果。</span>
      <span v-else>接口成功返回了真实空数组；页面不会补入历史标的或示例数据。</span>
    </div>

    <p v-else class="forecast-filter-status" aria-live="polite">当前显示 {{ rows.length }} / {{ sourceCount }} 只{{ selectedType ? ` · ${selectedType}` : '' }}{{ searchCode ? ` · 代码包含 ${searchCode}` : '' }}</p>

    <div v-if="rows.length" class="forecast-table-wrap" tabindex="0" aria-label="业绩预告候选明细表；窄屏自动转换为逐项卡片">
      <table class="forecast-table">
        <caption class="sr-only">服务端业绩预告规则榜单，保持服务端返回顺序</caption>
        <thead>
          <tr>
            <th scope="col">标的</th><th scope="col">板块 / 类型</th><th scope="col">净利同比</th>
            <th scope="col">预告净利</th><th scope="col">上年基数</th><th scope="col">披露 / 分层</th>
            <th scope="col">核验标记</th><th scope="col">变动原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="`${pool}-${row.code}-${row.ann_date}`">
            <td data-label="标的">
              <a class="forecast-stock-link" :href="diagnosisHref(row.code)">
                <strong>{{ row.name || '名称缺失' }}</strong><span class="mono">{{ row.code || '代码缺失' }}</span>
              </a>
            </td>
            <td data-label="板块 / 类型"><strong>{{ row.type || '类型缺失' }}</strong><span>{{ row.board || '板块缺失' }}</span></td>
            <td data-label="净利同比" class="mono forecast-number" :class="percentTone(row)" :aria-label="row.p_chg_min == null && row.p_chg_max == null ? '净利同比：幅度未披露' : undefined">{{ formatPercentRange(row.p_chg_min, row.p_chg_max) }}</td>
            <td data-label="预告净利" class="mono forecast-number" :aria-label="row.net_min == null && row.net_max == null ? '预告净利：未披露' : undefined">{{ formatMoneyRange(row.net_min, row.net_max) }}</td>
            <td data-label="上年基数" class="mono forecast-number" :aria-label="row.last_net == null ? '上年同期基数：未披露' : undefined">{{ formatMoney(row.last_net) }}</td>
            <td data-label="披露 / 分层"><time class="mono">{{ formatCompactDate(row.ann_date) }}</time><span class="forecast-layer" :class="layerTone(row.layer)">{{ row.layer || '时点未知' }}</span></td>
            <td data-label="核验标记">
              <div class="forecast-flags">
                <span v-if="row.is_new_today" class="is-info">今日新增</span>
                <span v-if="row.mandatory === false" class="is-info">自愿披露</span>
                <span v-if="row.nonrec_flag" class="is-warning">扣非待核</span>
                <span v-if="row.trap" class="is-danger">基数陷阱</span>
                <span v-if="!row.is_new_today && row.mandatory !== false && !row.nonrec_flag && !row.trap" class="is-neutral">无额外标记</span>
              </div>
              <small v-if="row.trap_reason">{{ row.trap_reason }}</small>
            </td>
            <td data-label="变动原因" class="forecast-reason">{{ reason(row) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.forecast-results { min-width: 0; overflow: hidden; }
.forecast-results__head { display: flex; min-height: 72px; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.forecast-code-search { display: grid; min-width: min(100%, 360px); gap: 4px; }
.forecast-code-search label { color: var(--text-3); font-size: 12px; font-weight: 700; }
.forecast-code-search > div { display: flex; min-width: 0; gap: 8px; }
.forecast-code-search input { width: min(100%, 280px); min-height: 44px; padding: 8px 11px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); font: 15px/1.4 "SFMono-Regular", Consolas, monospace; }
.forecast-code-search input::placeholder { color: var(--text-3); font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif; }
.forecast-code-search button { min-width: 64px; min-height: 44px; padding: 6px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text-2); cursor: pointer; }
.forecast-code-search button:hover { border-color: var(--border-strong); color: var(--text); }
.forecast-pool-switch { display: flex; gap: 8px; }
.forecast-pool-switch button { min-width: 104px; min-height: 44px; padding: 6px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text-2); font-weight: 730; cursor: pointer; }
.forecast-pool-switch button:hover { border-color: var(--border-strong); color: var(--text); }
.forecast-pool-switch button.active { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); }
.forecast-pool-empty { display: flex; min-height: 300px; flex-direction: column; align-items: center; justify-content: center; gap: 5px; padding: 24px; text-align: center; }
.forecast-pool-empty span { max-width: 620px; color: var(--text-3); }
.forecast-filter-status { margin: 0; padding: 7px 16px; border-bottom: 1px solid var(--border); color: var(--text-3); font-size: 12px; }
.forecast-table-wrap { max-width: 100%; overflow-x: auto; }
.forecast-table { width: 100%; min-width: 1260px; table-layout: fixed; border-collapse: collapse; font-size: 14px; line-height: 1.45; }
.forecast-table th, .forecast-table td { min-width: 0; padding: 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
.forecast-table th { background: var(--surface-2); color: var(--text-3); font-size: 12px; font-weight: 750; white-space: nowrap; }
.forecast-table th:nth-child(1) { width: 160px; }.forecast-table th:nth-child(2) { width: 126px; }.forecast-table th:nth-child(3) { width: 112px; }.forecast-table th:nth-child(4) { width: 148px; }.forecast-table th:nth-child(5) { width: 116px; }.forecast-table th:nth-child(6) { width: 142px; }.forecast-table th:nth-child(7) { width: 174px; }
.forecast-table tbody tr:last-child td { border-bottom: 0; }
.forecast-table tbody tr:hover { background: var(--surface-2); }
.forecast-table td > span:not(.forecast-layer), .forecast-table td > time { display: block; margin-top: 3px; color: var(--text-3); font-size: 12px; }
.forecast-stock-link { display: inline-flex; min-height: 44px; flex-direction: column; justify-content: center; color: var(--text); text-decoration: none; }
.forecast-stock-link strong { font-size: 15px; }.forecast-stock-link span { color: var(--accent); font-size: 12px; }
.forecast-number { color: var(--text); font-size: 15px; font-weight: 760; }
.forecast-layer { display: inline-flex; min-height: 28px; align-items: center; margin-top: 5px; padding: 2px 7px; border: 1px solid currentColor; border-radius: 5px; font-size: 12px; font-weight: 740; }
.forecast-layer.is-positive { color: var(--info); }.forecast-layer.is-warning { color: var(--warning); }.forecast-layer.is-neutral { color: var(--text-3); }
.forecast-flags { display: flex; flex-wrap: wrap; gap: 4px; }
.forecast-flags span { padding: 2px 6px; border: 1px solid currentColor; border-radius: 4px; font-size: 12px; font-weight: 720; }
.forecast-flags .is-info { color: var(--info); }.forecast-flags .is-warning { color: var(--warning); }.forecast-flags .is-danger { color: var(--danger); }.forecast-flags .is-neutral { color: var(--text-3); }
.forecast-table td small { display: block; margin-top: 5px; color: var(--danger); font-size: 12px; line-height: 1.45; }
.forecast-reason { color: var(--text-2); overflow-wrap: anywhere; }
.is-up { color: var(--up); }.is-down { color: var(--down); }
@media (max-width: 720px) {
  .forecast-results__head { align-items: stretch; flex-direction: column; }
  .forecast-code-search { min-width: 0; }.forecast-code-search input { width: 100%; }
  .forecast-pool-switch button { flex: 1 1 0; min-width: 0; }
  .forecast-table-wrap { overflow: visible; }
  .forecast-table { min-width: 0; font-size: 15px; }
  .forecast-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .forecast-table, .forecast-table tbody, .forecast-table tr, .forecast-table td { display: block; width: 100%; }
  .forecast-table tbody { display: grid; gap: 8px; padding: 8px; }
  .forecast-table tbody tr { overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
  .forecast-table td { display: grid; min-height: 52px; grid-template-columns: minmax(104px, .38fr) minmax(0, 1fr); align-items: start; gap: 10px; padding: 10px 11px; }
  .forecast-table td::before { color: var(--text-3); font-size: 14px; font-weight: 680; content: attr(data-label); }
  .forecast-stock-link { min-width: 0; }
  .forecast-reason { overflow-wrap: anywhere; }
}
@media (prefers-reduced-motion: reduce) {
  .forecast-table tbody tr { transition: none; }
}
</style>
