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
const code = computed(() => String(props.item.code || '').trim())
const itemId = computed(() => String(props.item.id || '').trim())
const displayName = computed(() => String(props.item.name || props.item.quote?.name || code.value || '名称缺失'))
const diagnosisHash = computed(() => `#/research?tab=diagnosis&code=${encodeURIComponent(code.value)}`)
const quotePrice = computed(() => finiteNumber(props.item.quote?.price))
const changeTone = computed(() => quoteTone(props.item.quote?.change_pct))
const riskClass = computed(() => {
  const risk = finiteNumber(props.item.risk_level)
  return risk != null && Number.isInteger(risk) && risk >= 1 && risk <= 5 ? `risk-${risk}` : 'risk-missing'
})
const zones = computed(() => props.item.ideal_buy_zones || {})
const zoneEntries = computed(() => [
  { key: 'ultra', label: '超短线 · 1—3日', zone: zones.value.ultra },
  { key: 'swing', label: '波段 · 3—10日', zone: zones.value.swing },
])
const missingReasons = computed(() => {
  const values = [
    ...(Array.isArray(props.item.missing) ? props.item.missing : []),
    ...(Array.isArray(zones.value.missing) ? zones.value.missing : []),
  ].map(String).filter(Boolean)
  return [...new Set(values)]
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
  <article class="panel watchlist-card" :aria-labelledby="`watchlist-item-${itemId || code}`">
    <header class="watchlist-card__header">
      <div
        class="watchlist-grade"
        :class="item.grade ? `grade-${String(item.grade).toLowerCase()}` : 'grade-missing'"
        :aria-label="item.grade ? `评级 ${item.grade}` : '评级缺失'"
      >
        <strong>{{ item.grade || '—' }}</strong>
      </div>
      <div class="watchlist-card__identity">
        <a :id="`watchlist-item-${itemId || code}`" :href="diagnosisHash">
          <strong>{{ displayName }}</strong>
          <span class="mono">{{ code || '代码缺失' }}</span>
        </a>
        <p>{{ item.status || '服务端状态缺失' }}</p>
        <span v-if="item.sector">{{ item.sector }}</span>
      </div>
      <div class="watchlist-card__actions">
        <template v-if="confirmingDelete">
          <button type="button" class="danger-button" :disabled="busy || !itemId" @click="emit('delete', item)">
            {{ deleting ? '移出中' : '确认移出' }}
          </button>
          <button type="button" class="secondary-button" :disabled="deleting" @click="confirmingDelete = false">取消</button>
        </template>
        <template v-else>
          <button type="button" class="secondary-button" :disabled="busy || !itemId" @click="emit('edit', item)">编辑</button>
          <button type="button" class="secondary-button" :disabled="busy || !itemId" @click="confirmingDelete = true">移出</button>
        </template>
      </div>
    </header>

    <div class="watchlist-card__decision">
      <div>
        <span>当前价格</span>
        <strong class="mono">{{ formatPrice(item.quote?.price) }}</strong>
        <em class="mono" :class="`is-${changeTone}`">{{ formatPercent(item.quote?.change_pct) }}</em>
      </div>
      <div>
        <span>生效理想区间 · {{ targetSourceLabel(item.target_source) }}</span>
        <strong class="mono">{{ formatBand(item.effective_target_low ?? item.target_low, item.effective_target_high ?? item.target_high) }}</strong>
        <em>单位：元</em>
      </div>
      <div>
        <span>风险边界</span>
        <strong class="watchlist-risk" :class="riskClass">{{ riskLabel(item.risk_level) }}</strong>
        <em class="mono">放弃价 {{ formatPrice(item.abandon_price) }} 元</em>
      </div>
    </div>

    <div class="watchlist-card__zones">
      <section v-for="entry in zoneEntries" :key="entry.key">
        <header>
          <strong>{{ entry.label }}</strong>
          <span :class="`is-${entry.zone?.state || 'missing'}`">{{ zoneStateLabel(entry.zone) }}</span>
        </header>
        <p class="mono">{{ formatBand(entry.zone?.low, entry.zone?.high) }} <small>元</small></p>
        <dl>
          <div><dt>位置</dt><dd>{{ zonePositionLabel(entry.zone?.position) }}</dd></div>
          <div><dt>数据日期</dt><dd class="mono">{{ entry.zone?.data_date || '—' }}</dd></div>
        </dl>
        <p v-if="entry.zone?.stale" class="watchlist-zone-warning">陈旧：同交易日最近成功结果</p>
        <ul v-if="zoneReasons(entry.zone).length" class="watchlist-zone-missing">
          <li v-for="reason in zoneReasons(entry.zone)" :key="reason">{{ reason }}</li>
        </ul>
      </section>
    </div>

    <details class="watchlist-card__details">
      <summary>条件与数据依据</summary>
      <dl class="watchlist-card__facts">
        <div v-for="row in detailRows" :key="row[0]"><dt>{{ row[0] }}</dt><dd>{{ row[1] || '—' }}</dd></div>
      </dl>

      <footer class="watchlist-card__footer">
        <span>报价：<b>{{ quotePrice == null ? '缺失' : item.quote?.stale ? '陈旧' : '可用' }}</b></span>
        <span>来源：{{ item.quote?.source || '—' }}</span>
        <span class="mono">报价时点：{{ compactTime(item.quote?.as_of) }}</span>
        <span class="mono">加入时点：{{ compactTime(item.created_at) }}</span>
        <span v-if="!itemId" class="is-warning">条目 ID 缺失，编辑与删除已禁用</span>
      </footer>

      <section v-if="missingReasons.length" class="watchlist-card__missing" aria-label="缺失与降级原因">
        <strong>缺失与降级原因（{{ missingReasons.length }}）</strong>
        <ul><li v-for="reason in missingReasons" :key="reason">{{ reason }}</li></ul>
      </section>
    </details>
  </article>
</template>

<style scoped>
.watchlist-card { display: grid; min-width: 0; overflow: hidden; }
.watchlist-card__header { display: grid; grid-template-columns: auto minmax(70px, 1fr) auto; align-items: center; gap: 6px; padding: 8px; border-bottom: 1px solid var(--border); }
.watchlist-grade { display: grid; width: 52px; min-height: 52px; place-items: center; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); text-align: center; }
.watchlist-grade strong { display: grid; width: 100%; height: 100%; place-items: center; font: 800 27px/1 "SFMono-Regular", Consolas, monospace; }
.watchlist-grade.grade-a { border-color: var(--info); color: var(--info); }
.watchlist-grade.grade-b { border-color: var(--warning); color: var(--warning); }
.watchlist-grade.grade-c, .watchlist-grade.grade-d, .watchlist-grade.grade-e, .watchlist-grade.grade-missing { color: var(--text-2); }
.watchlist-card__identity { min-width: 0; }
.watchlist-card__identity a { display: inline-flex; min-height: 44px; flex-wrap: wrap; align-items: baseline; gap: 8px; color: var(--text); text-decoration: none; }
.watchlist-card__identity a:hover { color: var(--accent); }
.watchlist-card__identity a strong { font-size: 16px; }
.watchlist-card__identity a span { color: var(--text-3); font-size: 13px; }
.watchlist-card__identity p { margin: -2px 0 0; color: var(--text-2); font-weight: 680; }
.watchlist-card__identity > span { color: var(--text-3); font-size: 12px; }
.watchlist-card__actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.watchlist-card__actions button { min-width: 44px; padding-inline: 8px; }
.watchlist-card__decision { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-bottom: 1px solid var(--border); }
.watchlist-card__decision > div { display: grid; min-width: 0; align-content: center; min-height: 78px; padding: 8px 10px; border-right: 1px solid var(--border); }
.watchlist-card__decision > div:last-child { border-right: 0; }
.watchlist-card__decision span { color: var(--text-3); font-size: 12px; }
.watchlist-card__decision strong { margin-top: 2px; color: var(--text); font-size: 18px; overflow-wrap: anywhere; }
.watchlist-card__decision em { color: var(--text-3); font-size: 12px; font-style: normal; }
.watchlist-risk { display: inline-flex; width: max-content; align-items: center; padding: 2px 7px; border: 1px solid var(--border); border-radius: 4px; background: var(--surface-2); color: var(--text-2) !important; font-size: 16px !important; }
.watchlist-risk.risk-1, .watchlist-risk.risk-2, .watchlist-risk.risk-3, .watchlist-risk.risk-4, .watchlist-risk.risk-5 { border-color: var(--risk-color); background: var(--risk-color); color: #fff !important; }
.watchlist-card__zones { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; padding: 8px 10px; border-bottom: 1px solid var(--border); }
.watchlist-card__zones section { min-width: 0; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.watchlist-card__zones header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.watchlist-card__zones header strong { font-size: 13px; }
.watchlist-card__zones header span { font-size: 12px; font-weight: 700; }
.watchlist-card__zones header .is-complete { color: var(--info); }
.watchlist-card__zones header .is-degraded { color: var(--warning); }
.watchlist-card__zones header .is-missing { color: var(--danger); }
.watchlist-card__zones section > p { margin: 3px 0; color: var(--text); font-size: 16px; font-weight: 720; }
.watchlist-card__zones section > p small { color: var(--text-3); font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; font-size: 12px; }
.watchlist-card__zones dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; margin: 0; }
.watchlist-card__zones dl div { min-width: 0; }
.watchlist-card__zones dt { color: var(--text-3); font-size: 12px; }
.watchlist-card__zones dd { margin: 1px 0 0; color: var(--text-2); font-size: 13px; overflow-wrap: anywhere; }
.watchlist-zone-warning { color: var(--warning) !important; font-family: inherit !important; font-size: 12px !important; }
.watchlist-zone-missing { margin: 5px 0 0; padding-left: 18px; color: var(--text-3); font-size: 12px; }
.watchlist-card__details > summary { min-height: 44px; padding: 11px 10px; color: var(--text-2); font-weight: 680; cursor: pointer; }
.watchlist-card__details[open] > summary { border-bottom: 1px solid var(--border); }
.watchlist-card__facts { display: grid; grid-template-columns: minmax(0, 1fr); margin: 0; padding: 4px 10px; }
.watchlist-card__facts div { display: grid; grid-template-columns: 78px minmax(0, 1fr); gap: 8px; min-width: 0; padding: 7px 0; border-bottom: 1px solid var(--border); }
.watchlist-card__facts div:last-child { border-bottom: 0; }
.watchlist-card__facts dt { color: var(--text-3); font-size: 12px; }
.watchlist-card__facts dd { margin: 0; color: var(--text-2); font-size: 13px; white-space: pre-wrap; }
.watchlist-card__footer { display: flex; flex-wrap: wrap; gap: 5px 12px; padding: 8px 10px; border-top: 1px solid var(--border); background: var(--surface-2); color: var(--text-3); font-size: 12px; }
.watchlist-card__footer b { color: var(--text-2); }
.watchlist-card__missing { padding: 8px 10px; border-top: 1px solid var(--border); color: var(--warning); }
.watchlist-card__missing > strong { font-size: 12px; }
.watchlist-card__missing ul { margin: 4px 0 0; padding-left: 18px; color: var(--text-3); font-size: 12px; }
@media (max-width: 820px) {
  .watchlist-card__header { grid-template-columns: auto minmax(0, 1fr); }
  .watchlist-card__actions { grid-column: 1 / -1; justify-content: flex-start; }
  .watchlist-card__decision { grid-template-columns: minmax(0, 1fr); }
  .watchlist-card__decision > div { min-height: 82px; border-right: 0; border-bottom: 1px solid var(--border); }
  .watchlist-card__decision > div:last-child { border-bottom: 0; }
}
@media (max-width: 720px) {
  .watchlist-card { font-size: 18px; }
  .watchlist-card__zones, .watchlist-card__facts { grid-template-columns: minmax(0, 1fr); }
  .watchlist-card__facts div:nth-last-child(2) { border-bottom: 1px solid var(--border); }
}
@media (max-width: 520px) {
  .watchlist-card__header { grid-template-columns: minmax(0, 1fr); }
  .watchlist-grade { width: 100%; min-height: 44px; grid-auto-flow: column; gap: 8px; align-items: center; }
  .watchlist-card__identity a { width: 100%; }
  .watchlist-card__actions, .watchlist-card__actions button { width: 100%; }
  .watchlist-card__zones dl { grid-template-columns: minmax(0, 1fr); }
  .watchlist-card__facts div { grid-template-columns: minmax(0, 1fr); gap: 2px; }
}
</style>
