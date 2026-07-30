<script setup lang="ts">
import { computed, ref } from 'vue'
import type { WatchlistItem, WatchlistZone } from '../types/watchlist'
import {
  compactTime,
  finiteNumber,
  formatBand,
  formatPercent,
  formatPrice,
  quoteTone,
  riskLabel,
  targetSourceLabel,
  zonePositionLabel,
  zoneStateLabel,
} from '../utils/watchlistFormat'

const props = defineProps<{
  item: WatchlistItem
  busy: boolean
  deleting: boolean
}>()

const emit = defineEmits<{
  edit: [item: WatchlistItem]
  delete: [item: WatchlistItem]
}>()

const confirmingDelete = ref(false)
const expanded = ref(false)
const code = computed(() => String(props.item.code || '').trim())
const itemId = computed(() => String(props.item.id || '').trim())
const displayName = computed(() => String(props.item.name || props.item.quote?.name || code.value || '名称缺失'))
const diagnosisHash = computed(() => `#/research?tab=diagnosis&code=${encodeURIComponent(code.value)}`)
const detailsId = computed(() => `watchlist-details-${itemId.value || code.value}`)
const quotePrice = computed(() => finiteNumber(props.item.quote?.price))
const changeTone = computed(() => quoteTone(props.item.quote?.change_pct))
const riskClass = computed(() => {
  const risk = finiteNumber(props.item.risk_level)
  return risk != null && Number.isInteger(risk) && risk >= 1 && risk <= 5 ? `risk-${risk}` : 'risk-missing'
})
const zones = computed(() => props.item.ideal_buy_zones || {})
const missingReasons = computed(() => {
  const values = [
    ...(Array.isArray(props.item.missing) ? props.item.missing : []),
    ...(Array.isArray(zones.value.missing) ? zones.value.missing : []),
  ].map(String).filter(Boolean)
  return [...new Set(values)]
})
const itemState = computed(() => {
  if (missingReasons.value.length) return `缺失 ${missingReasons.value.length} 项`
  if (zones.value.ultra?.state === 'degraded' || zones.value.swing?.state === 'degraded') return '含降级快照'
  if (props.item.quote?.stale) return '报价陈旧'
  if (quotePrice.value == null) return '报价缺失'
  return '决策字段完整'
})
const itemStateClass = computed(() => {
  if (missingReasons.value.length || quotePrice.value == null) return 'is-missing'
  if (itemState.value !== '决策字段完整') return 'is-warning'
  return 'is-complete'
})
const detailRows = computed(() => [
  ['加入理由', props.item.add_reason],
  ['买入前提', props.item.trigger_condition],
  ['不买条件', props.item.no_buy_condition],
  ['放弃条件', props.item.abandon_condition],
  ['备注', props.item.note],
] as Array<[string, string | null | undefined]>)

function zoneReasons(zone: WatchlistZone | null | undefined): string[] {
  return Array.isArray(zone?.missing) ? zone.missing.map(String).filter(Boolean) : []
}
</script>

<template>
  <article class="watchlist-row-item" :aria-labelledby="`watchlist-item-${itemId || code}`">
    <div class="watchlist-row">
      <div
        class="watchlist-grade"
        :class="item.grade ? `grade-${String(item.grade).toLowerCase()}` : 'grade-missing'"
        :aria-label="item.grade ? `评级 ${item.grade}` : '评级缺失'"
      >
        <strong>{{ item.grade || '—' }}</strong>
      </div>

      <div class="watchlist-identity">
        <a :id="`watchlist-item-${itemId || code}`" :href="diagnosisHash">
          <strong>{{ displayName }}</strong>
          <span class="mono">{{ code || '代码缺失' }}</span>
        </a>
        <span v-if="item.sector">{{ item.sector }}</span>
      </div>

      <div class="watchlist-status">
        <span class="cell-label">状态</span>
        <strong>{{ item.status || '状态未提供' }}</strong>
        <em :class="itemStateClass">{{ itemState }}</em>
      </div>

      <div class="watchlist-price">
        <span class="cell-label">当前价</span>
        <strong class="mono">{{ formatPrice(item.quote?.price) }}</strong>
        <em class="mono" :class="`is-${changeTone}`">{{ formatPercent(item.quote?.change_pct) }}</em>
      </div>

      <div class="watchlist-target">
        <span class="cell-label">生效区间 · {{ targetSourceLabel(item.target_source) }}</span>
        <strong class="mono">{{ formatBand(item.effective_target_low ?? item.target_low, item.effective_target_high ?? item.target_high) }}</strong>
        <em>单位：元</em>
      </div>

      <section class="watchlist-zone" aria-label="超短线 1—3日区间">
        <span class="cell-label">超短线 · 1—3日</span>
        <strong class="mono">{{ formatBand(zones.ultra?.low, zones.ultra?.high) }} <small>元</small></strong>
        <em :class="`is-${zones.ultra?.state || 'missing'}`">{{ zoneStateLabel(zones.ultra) }} · {{ zonePositionLabel(zones.ultra?.position) }}</em>
      </section>

      <section class="watchlist-zone" aria-label="波段 3—10日区间">
        <span class="cell-label">波段 · 3—10日</span>
        <strong class="mono">{{ formatBand(zones.swing?.low, zones.swing?.high) }} <small>元</small></strong>
        <em :class="`is-${zones.swing?.state || 'missing'}`">{{ zoneStateLabel(zones.swing) }} · {{ zonePositionLabel(zones.swing?.position) }}</em>
      </section>

      <div class="watchlist-risk-cell">
        <span class="cell-label">风险边界</span>
        <strong class="watchlist-risk" :class="riskClass">{{ riskLabel(item.risk_level) }}</strong>
        <em class="mono">放弃价 {{ formatPrice(item.abandon_price) }} 元</em>
      </div>

      <div class="watchlist-row__actions">
        <template v-if="confirmingDelete">
          <button type="button" class="danger-button" :disabled="busy || !itemId" @click="emit('delete', item)">
            {{ deleting ? '移出中' : '确认' }}
          </button>
          <button type="button" class="secondary-button" :disabled="deleting" @click="confirmingDelete = false">取消</button>
        </template>
        <template v-else>
          <button type="button" class="secondary-button" :disabled="busy || !itemId" @click="emit('edit', item)">编辑</button>
          <button type="button" class="secondary-button" :disabled="busy || !itemId" @click="confirmingDelete = true">移出</button>
        </template>
        <button
          type="button"
          class="secondary-button"
          :aria-expanded="expanded"
          :aria-controls="detailsId"
          @click="expanded = !expanded"
        >
          {{ expanded ? '收起' : '详情' }}
        </button>
      </div>
    </div>

    <section :id="detailsId" class="watchlist-row__details" :hidden="!expanded" aria-label="条件与数据依据">
      <dl class="watchlist-row__facts">
        <div v-for="row in detailRows" :key="row[0]"><dt>{{ row[0] }}</dt><dd>{{ row[1] || '—' }}</dd></div>
      </dl>

      <div class="watchlist-row__zone-meta">
        <section>
          <strong>超短线数据</strong>
          <span class="mono">{{ zones.ultra?.data_date || '日期缺失' }}</span>
          <span v-if="zones.ultra?.stale" class="is-warning">陈旧：同交易日最近成功结果</span>
          <ul v-if="zoneReasons(zones.ultra).length"><li v-for="reason in zoneReasons(zones.ultra)" :key="reason">{{ reason }}</li></ul>
        </section>
        <section>
          <strong>波段数据</strong>
          <span class="mono">{{ zones.swing?.data_date || '日期缺失' }}</span>
          <span v-if="zones.swing?.stale" class="is-warning">陈旧：同交易日最近成功结果</span>
          <ul v-if="zoneReasons(zones.swing).length"><li v-for="reason in zoneReasons(zones.swing)" :key="reason">{{ reason }}</li></ul>
        </section>
      </div>

      <footer class="watchlist-row__footer">
        <span>报价：<b>{{ quotePrice == null ? '缺失' : item.quote?.stale ? '陈旧' : '可用' }}</b></span>
        <span>来源：{{ item.quote?.source || '—' }}</span>
        <span class="mono">报价时点：{{ compactTime(item.quote?.as_of) }}</span>
        <span class="mono">加入时点：{{ compactTime(item.created_at) }}</span>
        <span class="mono">算法：{{ zones.algorithm_version || '—' }}</span>
        <span v-if="!itemId" class="is-warning">条目 ID 缺失，编辑与删除已禁用</span>
      </footer>

      <section v-if="missingReasons.length" class="watchlist-row__missing" aria-label="缺失与降级原因">
        <strong>缺失与降级原因（{{ missingReasons.length }}）</strong>
        <ul><li v-for="reason in missingReasons" :key="reason">{{ reason }}</li></ul>
      </section>
    </section>
  </article>
</template>

<style scoped>
.watchlist-row-item { min-width: 0; border-top: 1px solid var(--border); background: var(--surface); }
.watchlist-row { display: grid; min-width: 0; min-height: 78px; grid-template-columns: var(--watchlist-columns); align-items: stretch; }
.watchlist-row > * { display: grid; min-width: 0; align-content: center; padding: 8px 10px; border-right: 1px solid var(--border); }
.watchlist-row > *:last-child { border-right: 0; }
.cell-label { display: none; color: var(--text-3); font-size: 12px; font-weight: 650; }
.watchlist-grade { place-items: center; padding: 6px; text-align: center; }
.watchlist-grade strong { display: grid; width: 42px; height: 42px; place-items: center; border: 1px solid var(--border); border-radius: var(--radius-sm); font: 800 24px/1 "SFMono-Regular", Consolas, monospace; }
.watchlist-grade.grade-a strong { border-color: var(--info); color: var(--info); }
.watchlist-grade.grade-b strong { border-color: var(--warning); color: var(--warning); }
.watchlist-grade.grade-c strong, .watchlist-grade.grade-d strong, .watchlist-grade.grade-e strong, .watchlist-grade.grade-missing strong { color: var(--text-2); }
.watchlist-identity a { display: flex; min-width: 0; min-height: 44px; flex-direction: column; justify-content: center; color: var(--text); text-decoration: none; }
.watchlist-identity a:hover { color: var(--accent); }
.watchlist-identity a strong { overflow: hidden; font-size: 15px; text-overflow: ellipsis; white-space: nowrap; }
.watchlist-identity a span, .watchlist-identity > span { color: var(--text-3); font-size: 12px; }
.watchlist-status strong, .watchlist-price strong, .watchlist-target strong, .watchlist-zone strong { color: var(--text); overflow-wrap: anywhere; }
.watchlist-status em, .watchlist-price em, .watchlist-target em, .watchlist-zone em, .watchlist-risk-cell em { margin-top: 3px; color: var(--text-3); font-size: 12px; font-style: normal; overflow-wrap: anywhere; }
.watchlist-status em.is-complete, .watchlist-zone em.is-complete { color: var(--info); }
.watchlist-status em.is-warning, .watchlist-zone em.is-degraded { color: var(--warning); }
.watchlist-status em.is-missing, .watchlist-zone em.is-missing { color: var(--danger); }
.watchlist-price strong { font-size: 17px; }
.watchlist-zone small { color: var(--text-3); font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; font-size: 12px; }
.watchlist-risk { display: inline-flex; width: max-content; align-items: center; padding: 3px 7px; border: 1px solid var(--border); border-radius: 4px; background: var(--surface-2); color: var(--text-2); font-size: 14px; }
.watchlist-risk.risk-1, .watchlist-risk.risk-2, .watchlist-risk.risk-3, .watchlist-risk.risk-4, .watchlist-risk.risk-5 { border-color: var(--risk-color); background: var(--risk-color); color: #fff; }
.watchlist-row__actions { display: flex; flex-wrap: nowrap; align-items: center; justify-content: flex-end; gap: 8px; }
.watchlist-row__actions button { min-width: 44px; min-height: 44px; padding-inline: 7px; }
.watchlist-row__details { border-top: 1px solid var(--border); background: var(--surface-2); }
.watchlist-row__details[hidden] { display: none; }
.watchlist-row__facts { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); margin: 0; }
.watchlist-row__facts div { min-width: 0; padding: 10px 12px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.watchlist-row__facts div:last-child { border-right: 0; }
.watchlist-row__facts dt { color: var(--text-3); font-size: 12px; }
.watchlist-row__facts dd { margin: 3px 0 0; color: var(--text-2); font-size: 13px; white-space: pre-wrap; }
.watchlist-row__zone-meta { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.watchlist-row__zone-meta section { display: flex; min-width: 0; flex-wrap: wrap; gap: 4px 12px; padding: 9px 12px; border-right: 1px solid var(--border); }
.watchlist-row__zone-meta section:last-child { border-right: 0; }
.watchlist-row__zone-meta span, .watchlist-row__zone-meta ul { color: var(--text-3); font-size: 12px; }
.watchlist-row__zone-meta ul { width: 100%; margin: 0; padding-left: 18px; }
.watchlist-row__footer { display: flex; flex-wrap: wrap; gap: 5px 14px; padding: 8px 12px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 12px; }
.watchlist-row__footer b { color: var(--text-2); }
.watchlist-row__missing { padding: 9px 12px; border-top: 1px solid var(--border); color: var(--warning); }
.watchlist-row__missing > strong { font-size: 12px; }
.watchlist-row__missing ul { margin: 4px 0 0; padding-left: 18px; color: var(--text-3); font-size: 12px; }
@media (max-width: 900px) {
  .watchlist-row { min-height: 158px; grid-template-columns: repeat(12, minmax(0, 1fr)); grid-template-rows: repeat(2, minmax(78px, auto)); }
  .watchlist-row > * { border-bottom: 1px solid var(--border); }
  .watchlist-row > :nth-child(n + 5) { border-bottom: 0; }
  .watchlist-row > :nth-child(1) { grid-column: 1 / 2; grid-row: 1; }
  .watchlist-row > :nth-child(2) { grid-column: 2 / 4; grid-row: 1; }
  .watchlist-row > :nth-child(3) { grid-column: 4 / 6; grid-row: 1; }
  .watchlist-row > :nth-child(4) { grid-column: 6 / 8; grid-row: 1; }
  .watchlist-row > :nth-child(8) { grid-column: 8 / 10; grid-row: 1; border-bottom: 1px solid var(--border); }
  .watchlist-row > :nth-child(9) { grid-column: 10 / 13; grid-row: 1; border-bottom: 1px solid var(--border); }
  .watchlist-row > :nth-child(5) { grid-column: 1 / 5; grid-row: 2; }
  .watchlist-row > :nth-child(6) { grid-column: 5 / 9; grid-row: 2; }
  .watchlist-row > :nth-child(7) { grid-column: 9 / 13; grid-row: 2; border-right: 0; }
  .cell-label { display: block; }
  .watchlist-row__facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .watchlist-row__facts div:nth-child(2n) { border-right: 0; }
}
@media (max-width: 680px) {
  .watchlist-row { min-height: 372px; grid-template-columns: repeat(7, minmax(0, 1fr)); grid-template-rows: repeat(5, minmax(68px, auto)); font-size: 16px; }
  .watchlist-row > :nth-child(1) { grid-column: 1 / 2; grid-row: 1; }
  .watchlist-row > :nth-child(2) { grid-column: 2 / 4; grid-row: 1; }
  .watchlist-row > :nth-child(9) { grid-column: 4 / 8; grid-row: 1; }
  .watchlist-row > :nth-child(3) { grid-column: 1 / 8; grid-row: 2; border-right: 0; }
  .watchlist-row > :nth-child(4) { grid-column: 1 / 3; grid-row: 3; }
  .watchlist-row > :nth-child(5) { grid-column: 3 / 8; grid-row: 3; border-right: 0; }
  .watchlist-row > :nth-child(6) { grid-column: 1 / 4; grid-row: 4; }
  .watchlist-row > :nth-child(7) { grid-column: 4 / 8; grid-row: 4; border-right: 0; }
  .watchlist-row > :nth-child(8) { grid-column: 1 / 8; grid-row: 5; border-right: 0; border-bottom: 0; }
  .watchlist-row > :nth-child(n + 3):not(:nth-child(9)) { border-bottom: 1px solid var(--border); }
  .watchlist-row > :nth-child(8) { border-bottom: 0; }
  .watchlist-row__actions { justify-content: flex-end; }
  .watchlist-row__actions button { flex: 1 1 44px; padding-inline: 4px; }
  .watchlist-row__facts, .watchlist-row__zone-meta { grid-template-columns: minmax(0, 1fr); }
  .watchlist-row__facts div, .watchlist-row__zone-meta section { border-right: 0; }
  .watchlist-row__zone-meta section:first-child { border-bottom: 1px solid var(--border); }
}
</style>
