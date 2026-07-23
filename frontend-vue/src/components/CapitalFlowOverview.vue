<script setup lang="ts">
import { computed } from 'vue'
import type { MarketFlowResponse, SectorFlowRow, StockFlowResponse, StockFlowRow } from '../types/dashboard'

const props = defineProps<{
  flow: MarketFlowResponse | null
  stockflow: StockFlowResponse | null
  flowLoading: boolean
  stockflowLoading: boolean
  flowError?: string
  stockflowError?: string
}>()

function finite(value?: number | null): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function money(value?: number | null) {
  if (!finite(value)) return '—'
  const sign = value > 0 ? '+' : ''
  if (Math.abs(value) >= 1e8) return `${sign}${(value / 1e8).toFixed(1)} 亿`
  if (Math.abs(value) >= 1e4) return `${sign}${(value / 1e4).toFixed(1)} 万`
  return `${sign}${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(value)}`
}

function percent(value?: number | null) {
  return finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—'
}

function tone(value?: number | null) {
  if (!finite(value)) return 'is-flat'
  return value >= 0 ? 'is-up' : 'is-down'
}

const sectorRows = computed(() => (props.flow?.ok === false ? [] : props.flow?.main_flow_sectors || [])
  .filter((item): item is SectorFlowRow & { main_net: number } => finite(item.main_net)))
const inflow = computed(() => sectorRows.value.filter((item) => item.main_net > 0).sort((a, b) => b.main_net - a.main_net).slice(0, 10))
const outflow = computed(() => sectorRows.value.filter((item) => item.main_net < 0).sort((a, b) => a.main_net - b.main_net).slice(0, 8))
const maxAbs = computed(() => sectorRows.value.length ? Math.max(...sectorRows.value.map((item) => Math.abs(item.main_net))) : null)

function barWidth(row: SectorFlowRow) {
  return finite(row.main_net) && maxAbs.value ? `${Math.max(2, Math.abs(row.main_net) / maxAbs.value * 100)}%` : undefined
}

function usableStocks(rows?: StockFlowRow[]) {
  return (rows || [])
    .filter((item): item is StockFlowRow & { net: number } => finite(item.net))
    .slice(0, 10)
}

const stockInflow = computed(() => props.stockflow?.ok === false ? [] : usableStocks(props.stockflow?.inflow))
const stockOutflow = computed(() => props.stockflow?.ok === false ? [] : usableStocks(props.stockflow?.outflow))
const stockMaxAbs = computed(() => {
  const values = [...stockInflow.value, ...stockOutflow.value].map((item) => Math.abs(item.net))
  return values.length ? Math.max(...values) : null
})

function stockBarWidth(row: StockFlowRow) {
  return finite(row.net) && stockMaxAbs.value
    ? `${Math.max(3, Math.abs(row.net) / stockMaxAbs.value * 100)}%`
    : undefined
}

function stateNote(payload: MarketFlowResponse | StockFlowResponse | null, fallback: string) {
  return payload?.note
    || payload?.notes?.filter(Boolean).join('；')
    || payload?.missing?.filter(Boolean).join('；')
    || fallback
}
</script>

<template>
  <div class="capital-evidence-stack">
    <section class="panel evidence-card" aria-labelledby="sector-flow-title">
      <header class="section-heading evidence-heading">
        <div>
          <p class="eyebrow">SECTOR CAPITAL</p>
          <h3 id="sector-flow-title">行业资金 · 净流入 / 净流出</h3>
        </div>
        <div class="section-heading__meta">
          <span>{{ flow?.source || '公开资金源' }}</span>
          <span>{{ flow?.ts || '时点待确认' }}</span>
        </div>
      </header>

      <div v-if="flowLoading && !flow" class="flow-board" role="status" aria-label="行业资金加载中">
        <span v-for="item in 12" :key="item" class="skeleton-line"></span>
      </div>
      <template v-else-if="sectorRows.length">
        <p v-if="flow?.stale || flow?.note" class="evidence-note is-warning" role="status">{{ stateNote(flow, '') }}</p>
        <div class="flow-board" tabindex="0" aria-label="行业资金净流入和净流出排名，可横向滚动">
          <div class="flow-columns-label" aria-hidden="true">
            <span>板块</span><span>相对净额</span><span>主力净额</span><span>涨跌幅</span>
          </div>
          <div class="flow-section">
            <h4><span class="is-up">净流入</span><b>{{ inflow.length }} 个行业</b></h4>
            <div v-if="inflow.length" class="flow-rows" role="list" aria-label="行业资金净流入">
              <div v-for="item in inflow" :key="item.name" class="flow-row" role="listitem">
                <strong>{{ item.name || '未命名行业' }}</strong>
                <span class="flow-track" aria-hidden="true"><i class="is-inflow" :style="{ width: barWidth(item) }"></i></span>
                <b class="is-up mono">{{ money(item.main_net) }}</b>
                <span :class="tone(item.change_pct)" class="mono">{{ percent(item.change_pct) }}</span>
              </div>
            </div>
            <p v-else class="flow-empty">当前数据中没有正净额行业。</p>
          </div>
          <div class="flow-section">
            <h4><span class="is-down">净流出</span><b>{{ outflow.length }} 个行业</b></h4>
            <div v-if="outflow.length" class="flow-rows" role="list" aria-label="行业资金净流出">
              <div v-for="item in outflow" :key="item.name" class="flow-row" role="listitem">
                <strong>{{ item.name || '未命名行业' }}</strong>
                <span class="flow-track" aria-hidden="true"><i class="is-outflow" :style="{ width: barWidth(item) }"></i></span>
                <b class="is-down mono">{{ money(item.main_net) }}</b>
                <span :class="tone(item.change_pct)" class="mono">{{ percent(item.change_pct) }}</span>
              </div>
            </div>
            <p v-else class="flow-empty">当前数据中没有负净额行业。</p>
          </div>
        </div>
      </template>
      <div v-else class="empty-state compact-empty" role="status">
        <strong>行业资金暂不可用</strong>
        <span>{{ flowError || stateNote(flow, '公开资金源尚未返回可用的行业排名。') }}</span>
      </div>
    </section>

    <section class="panel evidence-card" aria-labelledby="stock-flow-title">
      <header class="section-heading evidence-heading">
        <div>
          <p class="eyebrow">STOCK CAPITAL</p>
          <h3 id="stock-flow-title">个股资金 · 净流入 / 净流出</h3>
        </div>
        <div class="section-heading__meta">
          <span>{{ stockflow?.state_label || '公开排名口径' }}</span>
          <span>{{ stockflow?.data_date || stockflow?.ts || '时点待确认' }}</span>
        </div>
      </header>

      <div v-if="stockflowLoading && !stockflow" class="stock-flow-grid" role="status" aria-label="个股资金加载中">
        <span v-for="item in 12" :key="item" class="skeleton-line"></span>
      </div>
      <template v-else-if="stockInflow.length || stockOutflow.length">
        <p v-if="stockflow?.data_state === 'delayed' || stockflow?.missing?.length" class="evidence-note is-warning" role="status">{{ stateNote(stockflow, '') }}</p>
        <div class="stock-flow-grid">
          <div class="stock-flow-column">
            <h4 class="is-up">净流入 TOP</h4>
            <div v-for="item in stockInflow" :key="item.code || item.name" class="stock-flow-row">
              <span><strong>{{ item.name || item.code || '未知标的' }}</strong><small>{{ item.code || '代码缺失' }}</small></span>
              <span class="stock-flow-track" aria-hidden="true"><i class="is-inflow" :style="{ width: stockBarWidth(item) }"></i></span>
              <b class="is-up mono">{{ money(item.net) }}</b>
              <em :class="tone(item.change_pct)" class="mono">{{ percent(item.change_pct) }}</em>
            </div>
            <p v-if="!stockInflow.length" class="flow-empty">净流入榜暂缺。</p>
          </div>
          <div class="stock-flow-column">
            <h4 class="is-down">净流出 TOP</h4>
            <div v-for="item in stockOutflow" :key="item.code || item.name" class="stock-flow-row">
              <span><strong>{{ item.name || item.code || '未知标的' }}</strong><small>{{ item.code || '代码缺失' }}</small></span>
              <span class="stock-flow-track" aria-hidden="true"><i class="is-outflow" :style="{ width: stockBarWidth(item) }"></i></span>
              <b class="is-down mono">{{ money(item.net) }}</b>
              <em :class="tone(item.change_pct)" class="mono">{{ percent(item.change_pct) }}</em>
            </div>
            <p v-if="!stockOutflow.length" class="flow-empty">净流出榜暂缺。</p>
          </div>
        </div>
      </template>
      <div v-else class="empty-state compact-empty" role="status">
        <strong>个股资金流暂不可用</strong>
        <span>{{ stockflowError || stateNote(stockflow, '免费公开资金源尚未更新或暂不可达；不以成交额或零值冒充净流入。') }}</span>
      </div>
    </section>
  </div>
</template>
