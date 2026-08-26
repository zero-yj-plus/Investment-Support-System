<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import type { HoldingView } from '../types/holdings'
import { formatMoney, formatPercent, formatPrice, pnlTone } from '../utils/holdingsFormat'

const props = defineProps<{ rows: HoldingView[] }>()
const emit = defineEmits<{ edit: [code: string] }>()

const riskRank: Record<string, number> = {
  red: 5, stop_triggered: 5, orange: 4, target_triggered: 4,
  yellow: 3, near_stop: 3, near_target: 3,
  gray: 2, quote_missing: 2, quote_stale: 2, unconfigured: 2,
  blue: 1, normal: 1,
}
function rankedState(state: string): number {
  return Object.prototype.hasOwnProperty.call(riskRank, state) ? riskRank[state] : 0
}
const sortedRows = computed(() => [...props.rows].sort((left, right) => (
  rankedState(right.riskState) - rankedState(left.riskState)
)))

function metric(row: HoldingView, key: string): number | null {
  const value = Number(row.algorithmRisk?.metrics?.[key])
  return Number.isFinite(value) ? value : null
}
function sectorName(row: HoldingView) {
  return row.algorithmRisk?.sector?.name || row.industry || '行业数据暂缺'
}
function riskDistance(row: HoldingView): number | null {
  const line = row.algorithmRisk?.levels?.algorithm_stop
  if (row.price == null || line == null || row.price <= 0) return null
  return (row.price - line) / row.price * 100
}
function riskIcon(state: string) {
  if (state === 'red' || state === 'stop_triggered') return '急'
  if (state === 'orange' || state === 'target_triggered') return '减'
  if (state === 'yellow' || state === 'near_stop' || state === 'near_target') return '观'
  if (state === 'blue' || state === 'normal') return '持'
  return '缺'
}
function actionTitle(row: HoldingView) { return row.algorithmRisk?.action || row.riskLabel }
function actionGuide(row: HoldingView) {
  const type = row.algorithmRisk?.signal_type
  if (type === 'profit_take') return '分批兑现利润，剩余仓位继续跟踪风险线。'
  if (row.riskState === 'red' || row.riskState === 'stop_triggered') return '先处理当前风险，不等待其他指标转好。'
  if (row.riskState === 'orange' || row.riskState === 'target_triggered') return '先降低部分仓位，剩余仓位继续观察。'
  if (row.riskState === 'yellow' || row.riskState === 'near_stop' || row.riskState === 'near_target') return '暂时不要加仓，等待风险条件解除。'
  if (row.riskState === 'blue' || row.riskState === 'normal') return '当前持有条件仍然成立，继续跟踪即可。'
  return '当前证据不足，暂不根据算法调整仓位。'
}

function closeEvidenceOnOutsideClick(event: MouseEvent) {
  const target = event.target
  if (!(target instanceof Node)) return
  document.querySelectorAll<HTMLDetailsElement>('.evidence-popover[open]').forEach((details) => {
    if (!details.contains(target)) details.open = false
  })
}

onMounted(() => document.addEventListener('click', closeEvidenceOnOutsideClick))
onBeforeUnmount(() => document.removeEventListener('click', closeEvidenceOnOutsideClick))
</script>

<template>
  <section class="holdings-board" aria-labelledby="holdings-board-title">
    <header class="holdings-board__header">
      <h2 id="holdings-board-title">逐笔持仓</h2>
      <span>需要调整成本、数量或监控方案时，点击对应持仓的“编辑持仓”</span>
    </header>

    <div class="position-list">
      <article v-for="row in sortedRows" :key="row.clientKey" class="position-card" :class="`is-${row.riskState}`">
        <header class="position-card__header">
          <div class="stock-title">
            <h3>{{ row.name || '名称缺失' }}</h3>
            <code>{{ row.code || '代码缺失' }}</code>
            <div class="stock-tags">
              <span class="sector-chip">{{ sectorName(row) }}<b v-if="row.algorithmRisk?.sector?.change_pct != null" :class="`is-${pnlTone(row.algorithmRisk.sector.change_pct)}`">{{ formatPercent(row.algorithmRisk.sector.change_pct, true) }}</b></span>
              <span>{{ row.algorithmRisk?.horizon_label || '通用' }}</span>
              <span v-if="row.algorithmRisk?.sector?.relative_today_pct != null" :class="`is-${pnlTone(row.algorithmRisk.sector.relative_today_pct)}`">今日相对板块 {{ formatPercent(row.algorithmRisk.sector.relative_today_pct, true) }}</span>
              <span v-if="row.algorithmRisk?.stale" class="is-stale">风险结论已陈旧</span>
            </div>
          </div>
          <div class="position-card__header-side">
            <details class="evidence-popover">
              <summary>为什么这样判断</summary>
              <div class="evidence-popover__content">
                <div class="evidence-grid">
                  <div><span>信号类型</span><strong>{{ row.algorithmRisk?.signal_type_label || '—' }}</strong></div>
                  <div><span>初始风险依据</span><strong>{{ row.algorithmRisk?.levels?.initial_stop_basis || '—' }}</strong></div>
                  <div><span>结构支撑</span><strong>{{ formatPrice(row.algorithmRisk?.levels?.support ?? null) }}</strong></div>
                  <div><span>吊灯保护线</span><strong>{{ formatPrice(row.algorithmRisk?.levels?.trailing_stop ?? null) }}</strong></div>
                  <div><span>MA5 / MA20 / MA60</span><strong>{{ formatPrice(metric(row, 'ma5')) }} / {{ formatPrice(metric(row, 'ma20')) }} / {{ formatPrice(metric(row, 'ma60')) }}</strong></div>
                  <div><span>ATR14</span><strong>{{ formatPrice(metric(row, 'atr14')) }}</strong></div>
                </div>
                <p v-if="row.algorithmRisk?.stale" class="evidence-stale" role="status">
                  本次刷新失败，保留原评估 {{ row.algorithmRisk.evaluated_at || row.algorithmRisk.as_of || '时点缺失' }} 的结论；{{ row.algorithmRisk.refresh_error || '失败原因未返回' }}
                </p>
                <ul v-if="row.algorithmRisk?.reasons?.length"><li v-for="reason in row.algorithmRisk.reasons" :key="reason">{{ reason }}</li></ul>
              </div>
            </details>
            <div class="position-card__actions">
              <div class="action-badge" :class="`is-${row.riskState}`"><i aria-hidden="true">{{ riskIcon(row.riskState) }}</i><span>{{ actionTitle(row) }}</span></div>
              <button type="button" class="edit-button" :aria-label="`编辑${row.name || row.code}持仓`" @click="emit('edit', row.code)">编辑持仓</button>
            </div>
          </div>
        </header>

        <div class="position-card__main">
          <section class="position-snapshot" :aria-label="`${row.name || row.code}持仓数据`">
            <div><span>现价</span><p><strong>{{ formatPrice(row.price) }}</strong><small :class="`is-${pnlTone(row.changePct)}`">涨跌 {{ formatPercent(row.changePct, true) }}</small></p></div>
            <div><span>浮动盈亏</span><p><strong :class="`is-${pnlTone(row.pnl)}`">{{ formatMoney(row.pnl) }}</strong><small><b :class="`is-${pnlTone(row.pnlPct)}`">累计 {{ formatPercent(row.pnlPct, true) }}</b></small></p></div>
            <div><span>持仓市值</span><p><strong>{{ formatMoney(row.marketValue) }}</strong><small>仓位 {{ formatPercent(row.holdingSharePct) }}</small></p></div>
            <div><span>持仓成本</span><p><strong>{{ formatPrice(row.cost) }}</strong><small>{{ row.shares == null ? '数量缺失' : `${row.shares.toLocaleString('zh-CN')} 股` }}</small></p></div>
          </section>

          <section class="decision-panel">
            <div class="decision-panel__heading"><p class="decision-panel__eyebrow">现在怎么做</p><h4>{{ actionTitle(row) }}</h4></div>
            <p class="decision-panel__guide">{{ actionGuide(row) }}</p>
            <div v-if="row.algorithmRisk" class="decision-reason"><span>主要原因</span><strong>{{ row.algorithmRisk.reasons?.[0] || row.algorithmRisk.summary }}</strong></div>
            <div class="risk-line-row">
              <span>算法风险线 <b>{{ formatPrice(row.algorithmRisk?.levels?.algorithm_stop ?? null) }}</b></span>
              <span v-if="riskDistance(row) != null">距离 <b>{{ formatPercent(riskDistance(row)) }}</b></span>
              <span v-if="row.stopLossPrice != null">客户风险线 <b>{{ formatPrice(row.stopLossPrice) }}</b></span>
            </div>
          </section>
        </div>

      </article>
    </div>
  </section>
</template>

<style scoped>
.holdings-board { display: grid; gap: 9px; }
.holdings-board__header { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding: 0 2px; }
.holdings-board__header h2 { margin: 0; font-size: 20px; }
.holdings-board__header > span { color: var(--text-3); font-size: 12px; }
.position-list { display: grid; grid-template-columns: minmax(0, 1fr); align-items: start; gap: 10px; }
.position-card { --risk-color: var(--text-3); --holding-columns: minmax(520px, 1.15fr) minmax(430px, .85fr); position: relative; overflow: visible; border: 1px solid var(--border); border-top: 2px solid var(--risk-color); border-radius: 8px; background: var(--surface); }
.position-card:has(.evidence-popover[open]) { z-index: 20; }
.position-card.is-red, .position-card.is-stop_triggered { --risk-color: var(--danger); }
.position-card.is-orange, .position-card.is-target_triggered { --risk-color: #d97706; }
.position-card.is-yellow, .position-card.is-near_stop, .position-card.is-near_target { --risk-color: var(--warning); }
.position-card.is-blue, .position-card.is-normal { --risk-color: var(--info); }
.position-card__header { position: relative; display: grid; min-height: 43px; grid-template-columns: var(--holding-columns); align-items: center; padding: 6px 12px; border-bottom: 1px solid var(--border); border-radius: 8px 8px 0 0; background: var(--surface); }
.stock-title { display: flex; min-width: 0; align-items: center; flex-wrap: wrap; gap: 5px 8px; padding-right: 12px; }
.stock-title h3 { margin: 0; font-size: 17px; line-height: 1.2; }
.stock-title code { color: var(--text-3); font-size: 11px; }
.stock-tags { display: inline-flex; flex-wrap: wrap; gap: 5px; }
.stock-tags > span { display: inline-flex; align-items: center; gap: 4px; padding: 2px 7px; border-radius: 999px; background: var(--surface-2); color: var(--text-2); font-size: 10.5px; }
.stock-tags .sector-chip { border: 1px solid var(--border); background: var(--surface); }
.stock-tags .is-stale { border: 1px solid color-mix(in srgb, var(--warning) 45%, var(--border)); color: color-mix(in srgb, var(--warning) 78%, var(--text)); }
.evidence-popover summary { display: inline-flex; min-height: 44px; align-items: center; cursor: pointer; color: var(--text-2); font-size: 13px; font-weight: 800; line-height: 1.25; white-space: nowrap; }
.evidence-popover summary:hover { color: var(--text); }
.evidence-popover__content { position: absolute; z-index: 30; top: calc(100% + 6px); left: 12px; width: min(720px, calc(100% - 24px)); max-height: calc(100dvh - 24px); overflow-y: auto; padding: 12px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--surface); }
.position-card__header-side { display: flex; min-width: 0; align-items: center; justify-content: space-between; gap: 10px; padding-left: 16px; }
.position-card__actions { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; }
.action-badge { display: inline-flex; min-height: 44px; align-items: center; gap: 6px; padding: 6px 10px; border: 1px solid color-mix(in srgb, var(--risk-color) 45%, var(--border)); border-radius: 8px; background: color-mix(in srgb, var(--risk-color) 7%, var(--surface)); color: var(--risk-color); font-size: 12px; font-weight: 800; white-space: nowrap; }
.action-badge i { display: grid; width: 24px; height: 24px; place-items: center; border-radius: 50%; background: var(--risk-color); color: white; font-style: normal; font-size: 11px; }
.edit-button { display: inline-flex; min-height: 44px; align-items: center; justify-content: center; gap: 5px; padding: 6px 12px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--surface); color: var(--text-2); font-size: 12px; font-weight: 700; cursor: pointer; }
.edit-button:hover { border-color: var(--focus); color: var(--text); }
.edit-button:focus-visible, .evidence-popover summary:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.position-card__main { display: grid; grid-template-columns: var(--holding-columns); overflow: hidden; padding: 0 12px; border-radius: 0 0 9px 9px; }
.position-snapshot { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); padding: 6px 0; }
.position-snapshot > div { display: grid; min-height: 39px; align-content: center; padding: 5px 12px; }
.position-snapshot > div:nth-child(even) { border-left: 1px solid var(--border); }
.position-snapshot > div:nth-child(n + 3) { border-top: 1px solid var(--border); }
.position-snapshot span, .position-snapshot small { color: var(--text-3); font-size: 11px; line-height: 1.25; }
.position-snapshot p { display: flex; min-width: 0; align-items: baseline; flex-wrap: wrap; gap: 3px 18px; margin: 2px 0 0; }
.position-snapshot strong { font-size: 14px; line-height: 1.25; }
.position-snapshot small { display: inline-flex; align-items: baseline; gap: 5px; }
.position-snapshot small b { font-weight: 600; }
.position-snapshot small i { color: var(--text-3); font-style: normal; }
.decision-panel { position: relative; display: flex; min-width: 0; flex-direction: column; justify-content: center; padding: 8px 16px; border-left: 1px solid var(--border); background: color-mix(in srgb, var(--risk-color) 5%, var(--surface)); }
.decision-panel__heading { display: flex; min-width: 0; align-items: baseline; flex-wrap: wrap; gap: 5px 10px; }
.decision-panel__eyebrow { margin: 0; color: var(--risk-color); font-size: 10px; font-weight: 800; line-height: 1.2; }
.decision-panel h4 { margin: 0; color: var(--risk-color); font-size: 19px; line-height: 1.2; }
.decision-panel__guide { margin: 2px 0 0; color: var(--text-2); font-size: 12px; line-height: 1.35; }
.decision-reason { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 7px; margin-top: 6px; padding-top: 6px; border-top: 1px solid color-mix(in srgb, var(--risk-color) 15%, var(--border)); font-size: 11px; line-height: 1.3; }
.decision-reason span { color: var(--text-3); }
.risk-line-row { display: flex; flex-wrap: wrap; gap: 3px 12px; margin-top: 4px; color: var(--text-2); font-size: 11px; line-height: 1.3; }
.evidence-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
.evidence-grid > div { display: grid; gap: 3px; padding: 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.evidence-grid span { color: var(--text-3); font-size: 11px; }
.evidence-grid strong { font-size: 12px; overflow-wrap: anywhere; }
.evidence-popover__content ul { margin: 9px 0 0; padding-left: 20px; color: var(--text-2); font-size: 12px; }
.evidence-stale { margin: 9px 0 0; padding: 8px; border-left: 3px solid var(--warning); background: color-mix(in srgb, var(--warning) 7%, var(--surface)); color: var(--text-2); font-size: 12px; }
.is-up { color: var(--up) !important; }.is-down { color: var(--down) !important; }.is-flat { color: var(--text-2) !important; }.is-missing { color: var(--text-3) !important; }
@media (max-width: 1080px) {
  .position-card { --holding-columns: minmax(420px, 1.05fr) minmax(390px, .95fr); }
}
@media (max-width: 820px) {
  .holdings-board__header { align-items: flex-start; flex-direction: column; }
  .holdings-board__header > span { display: none; }
  .position-card__header { grid-template-columns: minmax(0, 1fr); gap: 8px; }
  .position-card__header-side { width: 100%; padding-left: 0; }
  .position-card__actions { width: auto; margin-left: auto; }
  .action-badge { flex: 1; justify-content: center; }
  .position-card__main { grid-template-columns: minmax(0, 1fr); padding: 0 12px 10px; }
  .decision-panel { border-top: 1px solid var(--border); border-left: 0; }
  .evidence-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .evidence-popover__content { position: fixed; top: 50%; left: 50%; width: calc(100vw - 16px); max-height: calc(100dvh - 16px); transform: translate(-50%, -50%); }
}
@media (max-width: 430px) {
  .position-card__header-side { align-items: stretch; flex-direction: column; }
  .position-card__actions { width: 100%; align-items: stretch; flex-direction: column; margin-left: 0; }
  .edit-button { justify-content: center; }
  .position-snapshot, .evidence-grid { grid-template-columns: minmax(0, 1fr); }
  .position-snapshot > div:nth-child(even) { border-left: 0; }
  .position-snapshot > div + div { border-top: 1px solid var(--border); }
}
</style>
