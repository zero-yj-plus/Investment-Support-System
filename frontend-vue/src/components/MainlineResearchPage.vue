<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { useMainlineResearch } from '../composables/useMainlineResearch'
import type { MainlineEndpoint } from '../types/mainlineResearch'
import type { QualityState } from '../types/dashboard'
import { featureQuality, uniqueText } from '../utils/mainlineFormat'
import DataStatus from './DataStatus.vue'
import MainlineContinuity from './MainlineContinuity.vue'
import MainlineCrowdedness from './MainlineCrowdedness.vue'
import MainlineLeaders from './MainlineLeaders.vue'
import MainlineMoneyEffect from './MainlineMoneyEffect.vue'

const props = defineProps<{ marketSession?: string; isTradeDay?: boolean }>()
const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const { data, loading, errors, refreshing, refresh, retry } = useMainlineResearch(
  () => props.isTradeDay !== false && ['intraday', 'tail'].includes(props.marketSession || ''),
)

watch(refreshing, (value) => emit('refreshState', value), { immediate: true })
onBeforeUnmount(() => emit('refreshState', false))

const moneyRows = computed(() => Array.isArray(data.moneyEffect?.themes) ? data.moneyEffect.themes.filter((item) => item?.name) : [])
const crowdRows = computed(() => Array.isArray(data.crowdedness?.sectors) ? data.crowdedness.sectors.filter((item) => item?.name) : [])
const mainlineRows = computed(() => Array.isArray(data.mainline?.mainlines) ? data.mainline.mainlines.filter((item) => item?.name) : [])
const todayTop = computed(() => Array.isArray(data.mainline?.today_top) ? data.mainline.today_top.filter((item) => item?.name) : [])
const leaderRows = computed(() => [
  ...(Array.isArray(data.leaders?.ladder) ? data.leaders.ladder.flatMap((item) => item?.stocks || []) : []),
  ...(Array.isArray(data.leaders?.popularity) ? data.leaders.popularity : []),
].filter((item) => item?.name))

const qualities = computed<Record<MainlineEndpoint, QualityState>>(() => ({
  moneyEffect: featureQuality(data.moneyEffect, moneyRows.value.length > 0, errors.moneyEffect),
  crowdedness: featureQuality(data.crowdedness, crowdRows.value.length > 0, errors.crowdedness),
  mainline: featureQuality(data.mainline, mainlineRows.value.length > 0 || todayTop.value.length > 0, errors.mainline),
  leaders: featureQuality(data.leaders, leaderRows.value.length > 0, errors.leaders),
}))
const quality = computed<QualityState>(() => {
  const values = Object.values(qualities.value)
  if (values.every((item) => item === 'missing')) return 'missing'
  if (values.some((item) => item === 'missing' || item === 'degraded')) return 'degraded'
  if (values.some((item) => item === 'partial')) return 'partial'
  return 'complete'
})
const statusLabel = computed(() => ({ complete: '四项证据可用', degraded: '部分证据降级', partial: '证据读取中', missing: '主线证据不可用' }[quality.value]))
const dataDate = computed(() => data.mainline?.data_date || data.moneyEffect?.data_date || data.crowdedness?.data_date || data.leaders?.data_date || '—')
const evidenceTime = computed(() => data.mainline?.ts || data.moneyEffect?.ts || data.crowdedness?.ts || data.leaders?.ts || '—')
const governanceNotes = computed(() => uniqueText([
  data.moneyEffect?.missing, data.moneyEffect?.missing_fields, data.moneyEffect?.degraded_factors,
  data.crowdedness?.missing, data.crowdedness?.missing_fields, data.crowdedness?.degraded_factors,
  data.mainline?.missing, data.mainline?.missing_fields, data.mainline?.degraded_factors,
  data.leaders?.missing, data.leaders?.missing_fields, data.leaders?.degraded_factors,
]))

function retryOne(key: MainlineEndpoint) { void retry(key) }
defineExpose({ refresh })
</script>

<template>
  <section class="mainline-research-page" aria-labelledby="mainline-research-title" :aria-busy="refreshing">
    <header class="mainline-intro">
      <div class="page-heading"><h1 id="mainline-research-title">主线深研</h1><p class="page-subtitle">交叉验证题材赚钱效应、交易拥挤度、跨日持续性与龙头梯队，识别主线强弱和分歧风险。</p></div>
    </header>

    <div class="mainline-workbench">
      <MainlineMoneyEffect :data="data.moneyEffect" :loading="loading.moneyEffect" :error="errors.moneyEffect" @retry="retryOne('moneyEffect')" />
      <MainlineCrowdedness :data="data.crowdedness" :loading="loading.crowdedness" :error="errors.crowdedness" @retry="retryOne('crowdedness')" />
      <MainlineContinuity :data="data.mainline" :loading="loading.mainline" :error="errors.mainline" @retry="retryOne('mainline')" />
      <MainlineLeaders :data="data.leaders" :loading="loading.leaders" :error="errors.leaders" @retry="retryOne('leaders')" />
    </div>

    <section class="panel mainline-governance" aria-labelledby="mainline-governance-title"><header><div><p class="eyebrow">DATA GOVERNANCE</p><h2 id="mainline-governance-title">数据健康与口径</h2></div><span>{{ governanceNotes.length ? `${governanceNotes.length} 条缺失或降级说明` : '接口未声明缺口' }}</span></header><div><article><strong>真实接口</strong><span>money-effect / crowdedness / mainline / leaders 四路独立请求；失败局部隔离。</span></article><article><strong>单位</strong><span>涨跌、换手、成交占比为百分比；金额为元；赚钱分、拥挤分和主线强度不是同一量纲。</span></article><article><strong>确定性边界</strong><span>排名、分数、等级、梯队和切换信号由服务端返回；前端不重算风险或仓位。</span></article></div><details v-if="governanceNotes.length"><summary>查看全部缺失与降级说明</summary><ul><li v-for="note in governanceNotes" :key="note">{{ note }}</li></ul></details></section>

    <footer class="page-footer"><span>本页不消费 Markdown，也不注入原始 HTML；所有接口文本均作为 Vue 文本节点渲染。</span><span>空数组、字段缺失、降级状态和请求失败分别展示，缺失数字保持“—”。</span></footer>
    <dl class="mainline-stamp data-footnote-stamp" aria-label="主线深研数据日期、取数时点与质量状态"><div><dt>数据日期</dt><dd class="mono">{{ dataDate }}</dd></div><div><dt>证据时点</dt><dd class="mono">{{ evidenceTime }}</dd></div><div><dt>数据状态</dt><dd><DataStatus :state="quality" :label="statusLabel" /></dd></div></dl>
  </section>
</template>

<style scoped>
.mainline-research-page { display: grid; min-width: 0; --mainline-metric-size: clamp(16px, 1.15vw, 18px); --mainline-metric-compact-size: clamp(15px, 1vw, 16px); gap: 12px; }
.mainline-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }.mainline-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }.mainline-intro > div > p:last-child { max-width: 820px; margin: 7px 0 0; color: var(--text-2); }
.mainline-stamp { display: grid; min-width: 480px; grid-template-columns: minmax(100px, .8fr) minmax(170px, 1.2fr) minmax(160px, 1fr); gap: 12px; margin: 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }.mainline-stamp div { min-width: 0; }.mainline-stamp dt { color: var(--text-3); font-size: 12px; }.mainline-stamp dd { margin: 2px 0 0; font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }.mainline-stamp :deep(.data-status) { min-height: 26px; white-space: normal; }
.mainline-workbench { display: grid; gap: 12px; min-width: 0; }.mainline-governance { overflow: hidden; }.mainline-governance > header { display: flex; min-height: 58px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--border); }.mainline-governance h2 { margin: 0; font-size: 18px; }.mainline-governance > header > span { color: var(--text-3); font-size: 12px; }.mainline-governance > div { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }.mainline-governance article { display: grid; gap: 3px; padding: 12px 14px; border-right: 1px solid var(--border); }.mainline-governance article:last-child { border-right: 0; }.mainline-governance article strong { font-size: 13px; }.mainline-governance article span { color: var(--text-3); font-size: 13px; }.mainline-governance details summary { min-height: 44px; padding: 11px 14px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 13px; cursor: pointer; }.mainline-governance details ul { margin: 0; padding: 0 28px 14px 34px; color: var(--text-3); font-size: 12px; }
@media (max-width: 1180px) { .mainline-intro { align-items: stretch; flex-direction: column; gap: 12px; }.mainline-stamp { min-width: 0; } }
@media (max-width: 720px) { .mainline-research-page { gap: 8px; }.mainline-stamp { grid-template-columns: minmax(0, 1fr); }.mainline-governance > header { align-items: flex-start; flex-direction: column; }.mainline-governance > div { grid-template-columns: minmax(0, 1fr); }.mainline-governance article { border-right: 0; border-bottom: 1px solid var(--border); }.mainline-governance article:last-child { border-bottom: 0; } }
</style>
