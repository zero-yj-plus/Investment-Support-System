<script setup lang="ts">
import type { LimitUpTrackerStock, TrackerRowState } from '../types/limitupTracker'

defineProps<{
  stocks: LimitUpTrackerStock[]
  t1Date: string | null
  t2Date: string | null
}>()

function percent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function tone(value: number | null | undefined): string {
  if (value == null) return ''
  return value > 0 ? 'is-up' : value < 0 ? 'is-down' : 'is-flat'
}

function stateClass(state: TrackerRowState): string {
  return `is-${state}`
}

function diagnosisHref(code: string): string {
  return `#/research?tab=diagnosis&code=${encodeURIComponent(code)}`
}
</script>

<template>
  <section class="panel tracker-table-panel" aria-labelledby="tracker-table-title">
    <header class="tracker-table-heading">
      <div><h2 id="tracker-table-title">涨停股票明细</h2><p>顺序由服务端冻结，不在浏览器重新排名。</p></div>
      <strong class="mono">{{ stocks.length }} 只</strong>
    </header>
    <div v-if="stocks.length" class="tracker-table-wrap" tabindex="0" aria-label="涨停股票 T+1 与 T+2 追踪明细；窄屏时表格区域可横向滚动">
      <table class="tracker-table">
        <colgroup>
          <col class="col-code"><col class="col-name"><col class="col-industry"><col class="col-board">
          <col class="col-change"><col class="col-time"><col class="col-window"><col class="col-window">
        </colgroup>
        <thead>
          <tr>
            <th scope="col">代码</th>
            <th scope="col">名称</th>
            <th scope="col">行业</th>
            <th scope="col">连板</th>
            <th scope="col">涨停日涨幅</th>
            <th scope="col">首封</th>
            <th scope="col">次日 T+1<span class="mono">{{ t1Date || '日期暂缺' }}</span></th>
            <th scope="col">第三日 T+2<span class="mono">{{ t2Date || '日期暂缺' }}</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="stock in stocks" :key="stock.code">
            <td class="mono"><a :href="diagnosisHref(stock.code)">{{ stock.code }}</a></td>
            <td><strong>{{ stock.name || '名称缺失' }}</strong></td>
            <td :aria-label="stock.industry ? undefined : '行业：数据缺失'">{{ stock.industry || '—' }}</td>
            <td><span class="board-label" :class="stock.lianban && stock.lianban >= 2 ? 'is-multi' : ''">{{ stock.lianban_label || '板位待补' }}</span></td>
            <td class="mono" :class="tone(stock.baseline_change_pct)" :aria-label="stock.baseline_change_pct == null ? '涨停日涨幅：数据缺失' : undefined">{{ percent(stock.baseline_change_pct) }}</td>
            <td class="mono" :aria-label="stock.first_limit_time ? undefined : '首封时间：数据缺失'">{{ stock.first_limit_time || '—' }}</td>
            <td class="tracker-window-cell" :class="stateClass(stock.t1.state)">
              <strong v-if="stock.t1.change_pct != null" class="mono" :class="tone(stock.t1.change_pct)">{{ percent(stock.t1.change_pct) }}</strong>
              <strong v-else>{{ stock.t1.label }}</strong>
              <span>{{ stock.t1.change_pct != null ? stock.t1.label : stock.t1.date || '日期暂缺' }}</span>
            </td>
            <td class="tracker-window-cell" :class="stateClass(stock.t2.state)">
              <strong v-if="stock.t2.change_pct != null" class="mono" :class="tone(stock.t2.change_pct)">{{ percent(stock.t2.change_pct) }}</strong>
              <strong v-else>{{ stock.t2.label }}</strong>
              <span>{{ stock.t2.change_pct != null ? stock.t2.label : stock.t2.date || '日期暂缺' }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="tracker-table-empty">
      <strong>当前快照没有可展示的股票明细</strong>
      <span>若快照缺失，请使用“补数据”；真实空涨停池也会保持空表。</span>
    </div>
  </section>
</template>

<style scoped>
.tracker-table-panel { overflow: hidden; }
.tracker-table-heading { display: flex; min-height: 58px; align-items: center; justify-content: space-between; gap: 16px; padding: 9px 12px; border-bottom: 1px solid var(--border); }
.tracker-table-heading h2 { margin: 0; font-size: 17px; }
.tracker-table-heading p { margin: 2px 0 0; color: var(--text-3); font-size: 12px; }
.tracker-table-heading > strong { color: var(--text-2); font-size: 13px; }
.tracker-table-wrap { max-width: 100%; overflow-x: visible; }
.tracker-table { width: 100%; table-layout: fixed; border-collapse: collapse; font-size: 13px; }
.tracker-table .col-code { width: 8%; }
.tracker-table .col-name { width: 10%; }
.tracker-table .col-industry { width: 12%; }
.tracker-table .col-board { width: 9%; }
.tracker-table .col-change { width: 11%; }
.tracker-table .col-time { width: 10%; }
.tracker-table .col-window { width: 20%; }
.tracker-table th, .tracker-table td { min-width: 0; padding: 9px 8px; border-bottom: 1px solid var(--border); vertical-align: middle; text-align: left; }
.tracker-table th { background: var(--surface-2); color: var(--text-2); font-size: 12px; font-weight: 750; }
.tracker-table th > span { display: block; margin-top: 2px; color: var(--text-3); font-size: 10px; font-weight: 500; }
.tracker-table tbody tr:last-child td { border-bottom: 0; }
.tracker-table tbody tr:hover { background: var(--surface-2); }
.tracker-table td { overflow-wrap: anywhere; }
.tracker-table a { display: inline-flex; min-width: 44px; min-height: 44px; align-items: center; color: var(--accent); text-decoration: none; }
.tracker-table a:hover { text-decoration: underline; }
.board-label { display: inline-flex; min-height: 26px; align-items: center; padding: 2px 7px; border: 1px solid var(--border-strong); border-radius: 4px; color: var(--text-2); font-size: 12px; font-weight: 750; white-space: nowrap; }
.board-label.is-multi { border-color: var(--up); color: var(--up); }
.tracker-window-cell { display: table-cell; }
.tracker-window-cell strong, .tracker-window-cell span { display: block; }
.tracker-window-cell strong { font-size: 13px; }
.tracker-window-cell span { margin-top: 2px; color: var(--text-3); font-size: 10px; }
.tracker-window-cell.is-intraday { box-shadow: inset 2px 0 0 var(--warning); }
.tracker-window-cell.is-missing strong, .tracker-window-cell.is-unavailable strong { color: var(--text-2); font-weight: 650; }
.tracker-window-cell.is-pending strong { color: var(--warning); font-weight: 650; }
.tracker-table-empty { display: grid; min-height: 220px; place-content: center; gap: 5px; padding: 24px; color: var(--text-2); text-align: center; }
.tracker-table-empty span { color: var(--text-3); font-size: 13px; }
@media (max-width: 820px) {
  .tracker-table-wrap { overflow-x: auto; overscroll-behavior-inline: contain; }
  .tracker-table { min-width: 880px; }
}
</style>
