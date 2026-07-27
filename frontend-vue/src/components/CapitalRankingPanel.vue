<script setup lang="ts">
import type { CapitalFlowRow } from '../types/capitalTracking'
import {
  capitalDirection,
  finiteNumber,
  formatCapitalValue,
  formatPercent,
  isRawCapital,
} from '../utils/capitalFormat'

defineProps<{
  id: string
  title: string
  kind: 'sector' | 'stock'
  rows: CapitalFlowRow[]
  source?: string
  dataDate?: string
  stale?: boolean
}>()

function directionLabel(value: unknown): string {
  const direction = capitalDirection(value)
  if (direction === 'in') return '净流入'
  if (direction === 'out') return '净流出'
  if (direction === 'flat') return '持平'
  return '方向缺失'
}

function directionClass(value: unknown): string {
  const direction = capitalDirection(value)
  return direction === 'in' ? 'is-up' : direction === 'out' ? 'is-down' : ''
}

function rank(value: unknown): string {
  const parsed = finiteNumber(value)
  return parsed == null ? '—' : String(parsed)
}

function diagnosisHref(code: string | undefined): string {
  return `#/research?tab=diagnosis&code=${encodeURIComponent(code || '')}`
}
</script>

<template>
  <section class="panel capital-ranking" :aria-labelledby="`${id}-title`">
    <header class="capital-ranking__heading">
      <div><p class="eyebrow">结构化证据</p><h2 :id="`${id}-title`">{{ title }}</h2></div>
      <span class="mono">{{ rows.length }} 项</span>
    </header>
    <div class="capital-ranking__meta">
      <span>数据日期 <strong class="mono">{{ dataDate || '—' }}</strong></span>
      <span>来源 <strong>{{ source || '—' }}</strong></span>
      <span v-if="stale" class="capital-stale-label">非今日 · 最近已发布交易日</span>
    </div>

    <div v-if="!rows.length" class="capital-ranking__empty" role="status">
      <strong>{{ kind === 'stock' ? '当前无热门股资金记录' : '当前无行业资金记录' }}</strong>
      <span>接口已返回真实空数组，本模块不使用其他榜单或示例值补位。</span>
    </div>

    <div v-else class="capital-table-wrap" tabindex="0" :aria-label="`${title}，窄屏下按卡片逐项阅读`">
      <table class="capital-table">
        <thead><tr><th scope="col">排名</th><th scope="col">{{ kind === 'stock' ? '股票' : '行业' }}</th><th scope="col">快照涨跌</th><th scope="col">资金方向</th><th scope="col">最新值</th><th scope="col">单位状态</th></tr></thead>
        <tbody>
          <tr v-for="(row, index) in rows" :key="`${row.code || row.name || 'missing'}-${index}`">
            <td data-label="排名" class="mono" :aria-label="finiteNumber(row.rnk) == null ? '排名：数据缺失' : undefined">{{ rank(row.rnk) }}</td>
            <td :data-label="kind === 'stock' ? '股票' : '行业'">
              <a v-if="kind === 'stock' && row.code" class="capital-stock-link" :href="diagnosisHref(row.code)">
                <strong>{{ row.name || '名称缺失' }}</strong><span class="mono">{{ row.code }}</span>
              </a>
              <strong v-else>{{ row.name || '名称缺失' }}</strong>
            </td>
            <td data-label="快照涨跌" class="mono" :class="directionClass(row.change_pct)" :aria-label="finiteNumber(row.change_pct) == null ? '快照涨跌：数据缺失' : undefined">{{ formatPercent(row.change_pct) }}</td>
            <td data-label="资金方向"><span class="capital-direction" :class="directionClass(row.main_net)">{{ directionLabel(row.main_net) }}</span></td>
            <td data-label="最新值" class="capital-value mono" :class="directionClass(row.main_net)" :aria-label="finiteNumber(row.main_net) == null ? '资金值：数据缺失' : undefined">{{ formatCapitalValue(row) }}</td>
            <td data-label="单位状态"><span class="capital-unit" :class="{ 'is-unverified': isRawCapital(row) }">{{ isRawCapital(row) ? '原始值 · 未核验' : row.value_unit === 'CNY' || row.unit_verified ? '人民币 · 元' : '单位缺失' }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.capital-ranking { min-width: 0; overflow: hidden; }
.capital-ranking__heading { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.capital-ranking__heading h2 { margin: 0; font-size: 18px; }
.capital-ranking__heading > span { color: var(--text-3); font-size: 13px; }
.capital-ranking__meta { display: flex; min-width: 0; flex-wrap: wrap; gap: 4px 16px; padding: 8px 16px; border-bottom: 1px solid var(--border); background: var(--surface-2); color: var(--text-3); font-size: 12px; }
.capital-ranking__meta strong { color: var(--text-2); overflow-wrap: anywhere; }
.capital-stale-label { color: var(--warning); font-weight: 750; }
.capital-ranking__empty { display: flex; min-height: 220px; flex-direction: column; align-items: center; justify-content: center; gap: 4px; padding: 24px; text-align: center; }
.capital-ranking__empty span { max-width: 480px; color: var(--text-3); }
.capital-table-wrap { max-width: 100%; overflow-x: auto; }
.capital-table { width: 100%; min-width: 740px; table-layout: fixed; border-collapse: collapse; font-size: 14px; }
.capital-table th, .capital-table td { min-width: 0; padding: 9px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: middle; }
.capital-table th { background: var(--surface-2); color: var(--text-3); font-size: 12px; font-weight: 750; white-space: nowrap; }
.capital-table th:nth-child(1) { width: 58px; }
.capital-table th:nth-child(2) { width: 170px; }
.capital-table th:nth-child(3) { width: 108px; }
.capital-table th:nth-child(4) { width: 104px; }
.capital-table th:nth-child(5) { width: 130px; }
.capital-table th:nth-child(6) { width: 132px; }
.capital-table tbody tr:last-child td { border-bottom: 0; }
.capital-table td { color: var(--text-2); font-weight: 600; }
.capital-table td.mono { color: var(--text); font-weight: 720; }
.capital-stock-link { display: inline-flex; min-width: 44px; min-height: 44px; flex-direction: column; justify-content: center; color: var(--text); text-decoration: none; }
.capital-stock-link span { color: var(--accent); font-size: 12px; }
.capital-direction { font-weight: 750; }
.capital-value { font-size: 15px; }
.capital-unit { display: inline-flex; min-height: 28px; align-items: center; padding: 2px 7px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface-2); color: var(--text-2); font-size: 12px; font-weight: 700; }
.capital-unit.is-unverified { border-color: var(--warning); color: var(--warning); }
.is-up { color: var(--up) !important; }
.is-down { color: var(--down) !important; }
@media (max-width: 720px) {
  .capital-table-wrap { overflow: visible; }
  .capital-table { min-width: 0; font-size: 16px; }
  .capital-table thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .capital-table, .capital-table tbody, .capital-table tr, .capital-table td { display: block; width: 100%; }
  .capital-table tbody { display: grid; gap: 8px; padding: 8px; }
  .capital-table tbody tr { overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
  .capital-table td { display: grid; min-height: 48px; grid-template-columns: minmax(104px, .44fr) minmax(0, 1fr); align-items: center; gap: 10px; padding: 9px 11px; }
  .capital-table td::before { color: var(--text-3); font-size: 14px; font-weight: 680; content: attr(data-label); }
  .capital-stock-link { min-width: 0; }
}
</style>
