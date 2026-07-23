<script setup lang="ts">
import { computed, ref } from 'vue'
import type { HotboardStock } from '../types/hotboard'

type SortKey = 'price' | 'boardCount' | 'suc_rate_pct' | 'seal_amount'

const props = defineProps<{ stocks: HotboardStock[]; theme: string }>()
const sortKey = ref<SortKey | null>(null)
const sortDirection = ref<'ascending' | 'descending'>('descending')

function splitThemes(value: unknown): string[] {
  return String(value || '').split('+').map((item) => item.trim()).filter(Boolean)
}

function finite(value: unknown): number | null {
  if (value == null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function boardCount(stock: HotboardStock): number | null {
  const label = String(stock.high_days || '')
  if (label.includes('首板')) return 1
  const match = /(\d+)板/.exec(label)
  return match ? Number(match[1]) : null
}

function boardTone(stock: HotboardStock): string {
  const count = boardCount(stock)
  if (count == null) return 'is-missing'
  if (count >= 3) return 'is-high'
  if (count === 2) return 'is-mid'
  return 'is-first'
}

function numericValue(stock: HotboardStock, key: SortKey): number | null {
  return key === 'boardCount' ? boardCount(stock) : finite(stock[key])
}

function fallbackOrder(left: HotboardStock, right: HotboardStock): number {
  return (boardCount(right) ?? -Infinity) - (boardCount(left) ?? -Infinity)
    || (finite(right.seal_amount) ?? -Infinity) - (finite(left.seal_amount) ?? -Infinity)
}

const visibleStocks = computed(() => {
  const rows = props.stocks.filter((stock) => !props.theme || splitThemes(stock.reason_type).includes(props.theme))
  return [...rows].sort((left, right) => {
    if (!sortKey.value) return fallbackOrder(left, right)
    const leftValue = numericValue(left, sortKey.value)
    const rightValue = numericValue(right, sortKey.value)
    if (leftValue == null && rightValue == null) return fallbackOrder(left, right)
    if (leftValue == null) return 1
    if (rightValue == null) return -1
    const direction = sortDirection.value === 'ascending' ? 1 : -1
    return direction * (leftValue - rightValue) || fallbackOrder(left, right)
  })
})

function sortBy(key: SortKey) {
  if (sortKey.value === key) sortDirection.value = sortDirection.value === 'descending' ? 'ascending' : 'descending'
  else {
    sortKey.value = key
    sortDirection.value = 'descending'
  }
}

function ariaSort(key: SortKey): 'none' | 'ascending' | 'descending' {
  return sortKey.value === key ? sortDirection.value : 'none'
}

function sortLabel(key: SortKey, label: string): string {
  if (sortKey.value !== key) return `${label}排序，当前未排序；首次点击按从高到低`
  return `${label}排序，当前${sortDirection.value === 'descending' ? '降序' : '升序'}`
}

function number(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits)
}

function percent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function plainPercent(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(1)}%`
}

function amount(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  const absolute = Math.abs(value)
  if (absolute >= 100_000_000) return `${(value / 100_000_000).toFixed(2)} 亿`
  if (absolute >= 10_000) return `${(value / 10_000).toFixed(1)} 万`
  return `${value.toFixed(0)} 元`
}

function diagnosisHref(code: string | undefined): string {
  return `#/research?tab=diagnosis&code=${encodeURIComponent(code || '')}`
}
</script>

<template>
  <section class="panel hotboard-stocks" aria-labelledby="hotboard-stocks-title">
    <header class="hotboard-stock-heading">
      <div><p class="eyebrow">结构化证据 02</p><h2 id="hotboard-stocks-title">涨停股明细</h2></div>
      <span class="mono">{{ visibleStocks.length }} 只<span v-if="theme"> · {{ theme }}</span></span>
    </header>
    <div class="hotboard-table-wrap" tabindex="0" aria-label="涨停股明细，可在窄屏下按卡片逐项阅读">
      <table class="hotboard-table">
        <thead>
          <tr>
            <th scope="col">股票</th>
            <th scope="col" :aria-sort="ariaSort('price')"><button type="button" class="hotboard-sort-button" :class="{ 'is-active': sortKey === 'price' }" :aria-label="sortLabel('price', '现价')" @click="sortBy('price')">现价 <span class="hotboard-sort-icon" aria-hidden="true">{{ sortKey === 'price' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col">涨幅</th>
            <th scope="col" :aria-sort="ariaSort('boardCount')"><button type="button" class="hotboard-sort-button" :class="{ 'is-active': sortKey === 'boardCount' }" :aria-label="sortLabel('boardCount', '几天几板')" @click="sortBy('boardCount')">几天几板 <span class="hotboard-sort-icon" aria-hidden="true">{{ sortKey === 'boardCount' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col">板型</th>
            <th scope="col" :aria-sort="ariaSort('suc_rate_pct')"><button type="button" class="hotboard-sort-button" :class="{ 'is-active': sortKey === 'suc_rate_pct' }" :aria-label="sortLabel('suc_rate_pct', '封板率')" @click="sortBy('suc_rate_pct')">封板率 <span class="hotboard-sort-icon" aria-hidden="true">{{ sortKey === 'suc_rate_pct' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col">首封</th>
            <th scope="col" :aria-sort="ariaSort('seal_amount')"><button type="button" class="hotboard-sort-button" :class="{ 'is-active': sortKey === 'seal_amount' }" :aria-label="sortLabel('seal_amount', '封单额')" @click="sortBy('seal_amount')">封单额 <span class="hotboard-sort-icon" aria-hidden="true">{{ sortKey === 'seal_amount' ? sortDirection === 'descending' ? '↓' : '↑' : '↕' }}</span></button></th>
            <th scope="col">题材</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="stock in visibleStocks" :key="stock.code">
            <td data-label="股票"><div class="hotboard-stock-identity"><a class="hotboard-stock-link" :href="diagnosisHref(stock.code)"><strong>{{ stock.name || '名称缺失' }}</strong><span class="mono">{{ stock.code }}</span></a><span v-if="stock.is_again" class="hotboard-again">回封</span></div></td>
            <td data-label="现价" class="mono" :aria-label="stock.price == null ? '现价：数据缺失' : undefined">{{ number(stock.price) }}<span v-if="stock.price != null"> 元</span></td>
            <td data-label="涨幅" class="mono" :class="stock.change_pct == null ? '' : stock.change_pct >= 0 ? 'is-up' : 'is-down'" :aria-label="stock.change_pct == null ? '涨幅：数据缺失' : undefined">{{ percent(stock.change_pct) }}</td>
            <td data-label="几天几板" :aria-label="!stock.high_days ? '几天几板：数据缺失' : undefined"><span class="hotboard-board" :class="boardTone(stock)">{{ stock.high_days || '—' }}</span></td>
            <td data-label="板型" :aria-label="!stock.limit_up_type ? '板型：数据缺失' : undefined">{{ stock.limit_up_type || '—' }}</td>
            <td data-label="封板率" class="mono" :aria-label="stock.suc_rate_pct == null ? '封板率：数据缺失' : undefined">{{ plainPercent(stock.suc_rate_pct) }}</td>
            <td data-label="首封" class="mono" :aria-label="!stock.first_time ? '首封：数据缺失' : undefined">{{ stock.first_time || '—' }}</td>
            <td data-label="封单额" class="mono" :aria-label="stock.seal_amount == null ? '封单额：数据缺失' : undefined">{{ amount(stock.seal_amount) }}</td>
            <td data-label="题材" class="hotboard-reason" :aria-label="!stock.reason_type ? '题材：数据缺失' : undefined">{{ stock.reason_type || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.hotboard-stocks { min-width: 0; overflow: hidden; }
.hotboard-stock-heading { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.hotboard-stock-heading h2 { margin: 0; font-size: 18px; }
.hotboard-stock-heading > span { color: var(--text-3); font-size: 13px; }
.hotboard-table-wrap { max-width: 100%; overflow-x: auto; }
.hotboard-table { width: 100%; min-width: 940px; table-layout: fixed; border-collapse: collapse; font-size: 14px; line-height: 1.45; }
.hotboard-table th, .hotboard-table td { min-width: 0; padding: 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: middle; }
.hotboard-table td { min-height: 58px; color: var(--text-2); font-weight: 570; }
.hotboard-table td.mono { color: var(--text); font-size: 14px; font-weight: 730; letter-spacing: .015em; }
.hotboard-table th:nth-child(1) { width: 176px; }
.hotboard-table th:nth-child(2) { width: 90px; }
.hotboard-table th:nth-child(3) { width: 78px; }
.hotboard-table th:nth-child(4) { width: 108px; }
.hotboard-table th:nth-child(5) { width: 76px; }
.hotboard-table th:nth-child(6) { width: 94px; }
.hotboard-table th:nth-child(7) { width: 76px; }
.hotboard-table th:nth-child(8) { width: 108px; }
.hotboard-table th { background: var(--surface-2); color: var(--text-3); font-weight: 700; white-space: nowrap; }
.hotboard-sort-button { display: inline-flex; min-width: 100%; min-height: 44px; align-items: center; justify-content: space-between; gap: 4px; margin: -5px 0; padding: 5px 7px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--surface-3); color: var(--text); font-weight: 750; cursor: pointer; }
.hotboard-sort-button:hover { border-color: var(--accent); color: var(--accent); }
.hotboard-sort-button.is-active { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); }
.hotboard-sort-icon { display: inline-grid; width: 20px; height: 20px; flex: 0 0 auto; place-items: center; border: 1px solid currentColor; border-radius: 4px; font-family: "SFMono-Regular", Consolas, monospace; }
.hotboard-table tbody tr:last-child td { border-bottom: 0; }
.hotboard-table tbody tr { transition: background var(--ease); }
.hotboard-table tbody tr:hover { background: var(--surface-2); }
.hotboard-stock-identity { display: flex; min-width: 0; align-items: center; gap: 7px; white-space: nowrap; }
.hotboard-stock-link { display: inline-flex; min-width: 0; min-height: 44px; align-items: center; gap: 7px; color: var(--text); text-decoration: none; white-space: nowrap; }
.hotboard-stock-link strong { color: var(--text); font-size: 15px; font-weight: 760; }
.hotboard-stock-link span { color: var(--accent); font-size: 12px; font-weight: 750; }
.hotboard-again { display: inline-flex; min-height: 24px; align-items: center; justify-content: center; padding: 1px 5px; border: 1px solid var(--warning); border-radius: 4px; background: color-mix(in srgb, var(--warning) 8%, var(--surface)); color: var(--warning); font-size: 12px; font-weight: 750; }
.hotboard-board { display: inline-flex; min-height: 32px; align-items: center; padding: 3px 8px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface-2); font-weight: 720; white-space: nowrap; }
.hotboard-board.is-first { border-color: color-mix(in srgb, var(--info) 58%, var(--border)); background: color-mix(in srgb, var(--info) 9%, var(--surface)); color: var(--info); }
.hotboard-board.is-mid { border-color: color-mix(in srgb, var(--warning) 68%, var(--border)); background: color-mix(in srgb, var(--warning) 10%, var(--surface)); color: var(--warning); }
.hotboard-board.is-high { border-color: color-mix(in srgb, var(--up) 68%, var(--border)); background: color-mix(in srgb, var(--up) 10%, var(--surface)); color: var(--hotboard-high-text, var(--up)); }
:global(:root[data-theme="light"]) { --hotboard-high-text: #b70f32; }
.hotboard-board.is-missing { color: var(--text-3); }
.hotboard-reason { max-width: 320px; color: var(--text-2); font-weight: 600; }
.is-up { color: var(--up); }
.is-down { color: var(--down); }
@media (max-width: 720px) {
  .hotboard-table-wrap { overflow: visible; }
  .hotboard-table { min-width: 0; }
  .hotboard-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .hotboard-table, .hotboard-table tbody, .hotboard-table tr, .hotboard-table td { display: block; width: 100%; }
  .hotboard-table tbody { display: grid; gap: 8px; padding: 8px; }
  .hotboard-table tbody tr { overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
  .hotboard-table tbody tr:hover { background: var(--surface-2); }
  .hotboard-table { font-size: 15px; }
  .hotboard-table td { display: grid; min-height: 48px; grid-template-columns: minmax(88px, .42fr) minmax(0, 1fr); align-items: center; gap: 10px; padding: 9px 11px; }
  .hotboard-table td::before { color: var(--text-3); font-size: 14px; font-weight: 680; content: attr(data-label); }
  .hotboard-table td:first-child { grid-template-columns: minmax(88px, .42fr) minmax(0, 1fr); }
  .hotboard-stock-link { min-width: 0; }
  .hotboard-reason { max-width: none; }
}
</style>
