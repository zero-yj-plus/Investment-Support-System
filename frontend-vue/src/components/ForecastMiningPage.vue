<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ForecastMiningSummary from './ForecastMiningSummary.vue'
import ForecastMiningTable from './ForecastMiningTable.vue'
import { useForecastMining } from '../composables/useForecastMining'
import type { ForecastPool } from '../types/forecastMining'
import { formatCompactDate } from '../utils/forecastFormat'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const { data, items, traps, loading, refreshing, error, refresh } = useForecastMining()
const pool = ref<ForecastPool>('items')
const selectedType = ref('')
const codeQuery = ref('')

watch(refreshing, (value) => emit('refreshState', value), { immediate: true })

const rows = computed(() => pool.value === 'items' ? items.value : traps.value)
const filteredRows = computed(() => rows.value.filter((row) => {
  if (selectedType.value && row.type !== selectedType.value) return false
  if (codeQuery.value && !String(row.code || '').includes(codeQuery.value)) return false
  return true
}))
const trueEmpty = computed(() => !loading.value && !error.value && data.value?.ok !== false && !items.value.length && !traps.value.length)
const unavailable = computed(() => Boolean(error.value && !items.value.length && !traps.value.length))
const partialNotes = computed(() => [...new Set((data.value?.missing || []).filter(Boolean))])
const statusLabel = computed(() => {
  if (loading.value) return '读取中'
  if (unavailable.value) return '暂不可用'
  if (data.value?.used_last_good) return '降级 · 上次成功快照'
  if (error.value) return '已加载 · 刷新失败'
  if (partialNotes.value.length) return '部分覆盖'
  if (trueEmpty.value) return '真实空结果'
  if (items.value.length || traps.value.length) return '数据可用'
  return '状态确认中'
})
const freshnessLabel = computed(() => {
  if (data.value?.used_last_good) {
    const age = data.value.snapshot_age_seconds
    const ageText = age == null ? '陈旧时长未知' : `距成功采集约 ${age} 秒`
    return `上次成功采集 ${data.value.data_ts || '时点缺失'} · ${ageText}`
  }
  if (data.value?.data_ts) return `公开源采集时点 ${data.value.data_ts}；服务端计算时点不等于公告数据日期。`
  return '接口未提供独立数据日期或陈旧标记；计算时点不等于公告数据日期。'
})
const typeDistribution = computed(() => Object.entries(data.value?.stats?.['按类型'] || {})
  .filter(([, count]) => count != null && Number.isFinite(Number(count))))

function selectType(label: string) {
  selectedType.value = selectedType.value === label ? '' : label
}

function updateCodeQuery(value: string) {
  codeQuery.value = value.replace(/\D/g, '').slice(0, 6)
}

defineExpose({ refresh })
</script>

<template>
  <section class="forecast-page" aria-labelledby="forecast-page-title" :aria-busy="loading || refreshing">
    <header class="forecast-intro">
      <div class="page-heading">
        <h1 id="forecast-page-title">业绩预告掘金</h1>
        <p class="page-subtitle">回答“哪些预告达到既定正向规则、哪些被基数陷阱沉底”。AI 不参与筛选，页面不生成推荐、价格区间或仓位结论。</p>
      </div>
    </header>

    <div v-if="loading" class="forecast-loading" role="status" aria-live="polite">
      <span class="sr-only">业绩预告榜单正在加载</span>
      <section class="panel forecast-skeleton forecast-skeleton--stats"></section>
      <section class="panel forecast-skeleton forecast-skeleton--table"></section>
    </div>

    <template v-else>
      <div v-if="error" class="forecast-alert" role="alert">
        <div>
          <strong>{{ items.length || traps.length ? '最新刷新失败，保留已加载榜单' : '业绩预告榜单暂不可用' }}</strong>
          <span>{{ error }}</span>
        </div>
        <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh">重试</button>
      </div>

      <div v-if="partialNotes.length && !unavailable" class="forecast-partial" role="status" aria-live="polite">
        <strong>{{ data?.used_last_good ? '当前使用上次成功快照' : '当前为部分覆盖' }}</strong>
        <span v-for="note in partialNotes" :key="note">{{ note }}</span>
      </div>

      <ForecastMiningSummary
        :data="data"
      />

      <details class="panel forecast-terms" open>
        <summary>规则名词与使用边界</summary>
        <dl>
          <div><dt>主动交卷 / 提前披露</dt><dd>服务端按公告日期与法定窗口分层，只是披露时点信号，不等于业绩质量已确认。</dd></div>
          <div><dt>自愿披露</dt><dd>双创或北交所公司不在同一强制窗口内，必须与主板强制披露分开理解。</dd></div>
          <div><dt>扣非待核</dt><dd>变动原因命中非经常性损益词，正式报告仍需核对扣非净利润；页面不推断金额。</dd></div>
          <div><dt>基数陷阱</dt><dd>低基数、负基数或异常高同比被服务端透明沉底，不从历史事实中删除。</dd></div>
        </dl>
      </details>

      <section v-if="typeDistribution.length" class="panel forecast-types" aria-labelledby="forecast-types-title">
        <div><p class="eyebrow">覆盖构成</p><h2 id="forecast-types-title">预告类型分布</h2></div>
        <div class="forecast-type-filters" aria-label="按预告类型筛选明细">
          <button type="button" class="forecast-type-filter" :class="{ active: !selectedType }" :aria-pressed="!selectedType" @click="selectedType = ''">
            <span>全部类型</span><strong class="mono">{{ data?.stats?.['总数'] ?? '—' }}</strong>
          </button>
          <button v-for="([label, count]) in typeDistribution" :key="label" type="button" class="forecast-type-filter" :class="{ active: selectedType === label }" :aria-pressed="selectedType === label" @click="selectType(label)">
            <span>{{ label }}</span><strong class="mono">{{ count }}</strong>
          </button>
        </div>
      </section>

      <section v-if="trueEmpty" class="panel forecast-empty" aria-labelledby="forecast-empty-title">
        <p class="eyebrow">真实空结果</p>
        <h2 id="forecast-empty-title">当前规则下没有候选</h2>
        <p>接口成功返回 `items=[]` 与 `traps=[]`。页面不会用历史、猜测值或示例标的补位。</p>
        <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh">重新读取</button>
      </section>

      <ForecastMiningTable
        v-else-if="items.length || traps.length"
        :rows="filteredRows"
        :source-count="rows.length"
        :pool="pool"
        :selected-type="selectedType"
        :search-code="codeQuery"
        @change-pool="pool = $event"
        @update-search-code="updateCodeQuery"
      />

      <footer class="page-footer forecast-footer">
        <span>来源：公开业绩预告汇总；同比单位为 %，利润与基数原始单位为人民币元。</span>
        <span>缺失保持缺失；服务端排序与陷阱分池不在浏览器重算。历史预告事实不被覆盖。</span>
      </footer>
    </template>

    <dl class="forecast-stamp" :class="{ 'is-degraded': partialNotes.length, 'is-unavailable': unavailable }" aria-label="业绩预告报告期、服务端时点、数据状态与新鲜度说明">
      <div><dt>报告期</dt><dd class="mono">{{ formatCompactDate(data?.period) }}<span v-if="!data?.period" class="sr-only">报告期缺失</span></dd></div>
      <div><dt>服务端计算时点</dt><dd class="mono">{{ data?.ts || '—' }}<span v-if="!data?.ts" class="sr-only">时点缺失</span></dd></div>
      <div><dt>数据状态</dt><dd>{{ statusLabel }}</dd></div>
      <div class="forecast-stamp__freshness"><dt>新鲜度</dt><dd>{{ freshnessLabel }}</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.forecast-page { display: grid; min-width: 0; gap: 12px; }
.forecast-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.forecast-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.forecast-intro > div > p:last-child { max-width: 790px; margin: 7px 0 0; color: var(--text-2); }
.forecast-stamp { display: grid; min-width: 520px; grid-template-columns: minmax(112px, .75fr) minmax(190px, 1.25fr) minmax(92px, .65fr); gap: 6px 14px; margin: 0; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.forecast-stamp.is-degraded { border-color: var(--warning); }.forecast-stamp.is-unavailable { border-color: var(--danger); }
.forecast-stamp div { min-width: 0; }.forecast-stamp dt { color: var(--text-3); font-size: 12px; }.forecast-stamp dd { margin: 1px 0 0; font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }
.forecast-stamp__freshness { display: grid; grid-column: 1 / -1; grid-template-columns: auto minmax(0, 1fr); gap: 8px; padding-top: 5px; border-top: 1px solid var(--border); }
.forecast-stamp__freshness dd { color: var(--text-2); font-weight: 500; }
.forecast-loading { display: grid; gap: 12px; }
.forecast-skeleton { position: relative; overflow: hidden; }
.forecast-skeleton::after { position: absolute; inset: 0; background: color-mix(in srgb, var(--surface-3) 68%, transparent); content: ""; animation: forecast-pulse 1.2s ease-in-out infinite alternate; }
.forecast-skeleton--stats { min-height: 104px; }.forecast-skeleton--table { min-height: 420px; }
.forecast-alert { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 12px 14px; border: 1px solid var(--danger); border-radius: var(--radius); background: color-mix(in srgb, var(--danger) 7%, var(--surface)); }
.forecast-alert > div { display: grid; gap: 2px; }.forecast-alert span { color: var(--text-2); }
.forecast-partial { display: flex; flex-wrap: wrap; gap: 4px 12px; padding: 10px 14px; border: 1px solid var(--warning); border-radius: var(--radius); background: color-mix(in srgb, var(--warning) 7%, var(--surface)); color: var(--text-2); }
.forecast-partial strong { color: var(--warning); }.forecast-partial span:not(:last-child)::after { color: var(--text-3); content: "；"; }
.forecast-terms summary { display: flex; min-height: 52px; align-items: center; padding: 8px 14px; color: var(--text); font-weight: 760; cursor: pointer; }
.forecast-terms dl { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; border-top: 1px solid var(--border); }
.forecast-terms dl div { min-width: 0; padding: 10px 12px; border-right: 1px solid var(--border); }.forecast-terms dl div:last-child { border-right: 0; }
.forecast-terms dt { color: var(--warning); font-size: 13px; font-weight: 750; }.forecast-terms dd { margin: 4px 0 0; color: var(--text-2); font-size: 13px; line-height: 1.55; }
.forecast-types { display: flex; min-width: 0; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 16px; }
.forecast-types h2 { margin: 0; font-size: 17px; }
.forecast-type-filters { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.forecast-type-filter { display: grid; min-width: 92px; min-height: 44px; grid-template-columns: auto auto; align-items: center; justify-content: space-between; gap: 10px; padding: 6px 9px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text-2); cursor: pointer; }
.forecast-type-filter span { font-size: 12px; }.forecast-type-filter strong { color: var(--text); font-size: 16px; }
.forecast-type-filter:hover { border-color: var(--border-strong); color: var(--text); }
.forecast-type-filter.active { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); }
.forecast-type-filter.active strong { color: var(--accent); }
.forecast-empty { display: flex; min-height: 340px; flex-direction: column; align-items: center; justify-content: center; padding: 24px; text-align: center; }
.forecast-empty h2 { margin: 0; font-size: 22px; }.forecast-empty > p:not(.eyebrow) { max-width: 640px; margin: 8px 0 18px; color: var(--text-3); }
.forecast-footer { padding-top: 3px; }
@keyframes forecast-pulse { from { opacity: .42; } to { opacity: .78; } }
@media (max-width: 1100px) {
  .forecast-intro { align-items: stretch; flex-direction: column; gap: 12px; }
  .forecast-stamp { min-width: 0; }
  .forecast-terms dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .forecast-skeleton--stats { min-height: 216px; }
  .forecast-terms dl div:nth-child(2) { border-right: 0; }.forecast-terms dl div:nth-child(-n + 2) { border-bottom: 1px solid var(--border); }
}
@media (max-width: 720px) {
  .forecast-page { gap: 8px; }
  .forecast-stamp { grid-template-columns: 1fr; }.forecast-stamp__freshness { grid-column: auto; grid-template-columns: 1fr; gap: 1px; }
  .forecast-alert { align-items: stretch; flex-direction: column; }.forecast-alert .secondary-button { width: 100%; }
  .forecast-partial { flex-direction: column; }.forecast-partial span:not(:last-child)::after { content: ""; }
  .forecast-terms dl { grid-template-columns: 1fr; }.forecast-terms dl div { border-right: 0; border-bottom: 1px solid var(--border); }.forecast-terms dl div:last-child { border-bottom: 0; }
  .forecast-terms dl div:nth-child(-n + 2) { border-bottom: 1px solid var(--border); }
  .forecast-types { align-items: stretch; flex-direction: column; }.forecast-type-filters { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); justify-content: stretch; }
  .forecast-type-filter { width: 100%; }
  .forecast-skeleton--stats { min-height: 352px; }
}
@media (prefers-reduced-motion: reduce) {
  .forecast-skeleton::after { animation: none; }
}
</style>
