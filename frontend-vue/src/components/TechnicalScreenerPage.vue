<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useTechnicalScreener } from '../composables/useTechnicalScreener'
import type { QualityState } from '../types/dashboard'
import type { TechnicalTimeframe } from '../types/technicalScreener'
import { formatTechnicalInteger } from '../utils/technicalScreenerFormat'
import DataStatus from './DataStatus.vue'
import TechnicalScreenerGovernance from './TechnicalScreenerGovernance.vue'
import TechnicalScreenerResults from './TechnicalScreenerResults.vue'
import TechnicalScreenerRules from './TechnicalScreenerRules.vue'
import TechnicalSignalDialog from './TechnicalSignalDialog.vue'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  catalog,
  status,
  schemes,
  presets,
  result,
  stocks,
  bootstrapLoading,
  catalogError,
  statusError,
  schemesError,
  queryError,
  queryLoading,
  hasSubmitted,
  resultStale,
  refreshSubmitting,
  refreshQueued,
  refreshFeedback,
  schemeBusy,
  schemeFeedback,
  selectedSchemeId,
  selectedPresetId,
  schemeName,
  presetReadOnly,
  legacyReadOnly,
  legacyReason,
  ruleConfig,
  universe,
  minimumAmountWan,
  defaultTimeframe,
  historyCode,
  historyEvents,
  historyLoading,
  historyRequested,
  historyError,
  definitions,
  availableSignalCount,
  conflicts,
  busy,
  markDirty,
  setConditionSignal,
  addGroup,
  removeGroup,
  addSignals,
  removeCondition,
  restoreConditionDefaults,
  loadScheme,
  copyToDraft,
  saveScheme,
  deleteScheme,
  runScreen,
  stopQuery,
  submitRefresh,
  loadHistory,
  refresh,
} = useTechnicalScreener()

const libraryOpen = ref(false)
const libraryKind = ref<'satisfy' | 'exclude'>('satisfy')
const libraryGroupIndex = ref(0)
const libraryTrigger = ref<HTMLButtonElement | null>(null)

watch(busy, (value) => emit('refreshState', value), { immediate: true })
onBeforeUnmount(() => emit('refreshState', false))

const quality = computed<QualityState>(() => {
  if (bootstrapLoading.value || !status.value || statusError.value) return 'partial'
  if (status.value.state === 'missing') return 'missing'
  if (status.value.state === 'ready' && !catalogError.value) return 'complete'
  return 'partial'
})

const cacheLabel = computed(() => {
  if (bootstrapLoading.value || !status.value) return '状态确认中'
  if (statusError.value) return '状态暂不可用'
  if (status.value.state === 'missing') return '缓存缺失'
  if (status.value.state === 'ready') return '缓存可用'
  if (status.value.state === 'partial') return '部分覆盖'
  if (status.value.state === 'queued') return '刷新已排队'
  return '正在刷新'
})

const conclusion = computed(() => {
  if (bootstrapLoading.value) return { eyebrow: '读取真实缓存', title: '正在确认信号目录与缓存状态', detail: '三个初始化接口独立请求；局部失败不会拖垮整页。' }
  if (!status.value?.stocks) return { eyebrow: '当前不可筛', title: '尚无全市场技术信号缓存', detail: '先显式提交后台刷新；平台接受不等于数据已经更新。' }
  if (!result.value) return { eyebrow: '缓存已可用', title: `可在 ${formatTechnicalInteger(status.value.stocks)} 只缓存股票中执行规则`, detail: '当前唯一主动作是按所选技术规则开始筛选。' }
  if (result.value.ok === false) return { eyebrow: '本批次不可用', title: '服务端未形成可用筛选结果', detail: result.value.missing?.join('；') || result.value.error || '请核对缓存与规则。' }
  if (!stocks.value.length) return { eyebrow: '本批次无命中', title: '服务端返回真实空数组', detail: result.value.missing?.length ? '同时存在局部缺失，不能外推为全市场绝对无机会。' : '可核对限制最强的条件后再决定是否调整。' }
  return { eyebrow: '确定性候选已形成', title: `命中 ${formatTechnicalInteger(result.value.matched)} 只，展示 ${formatTechnicalInteger(result.value.returned)} 只`, detail: '候选只说明技术规则命中；下一步进入个股诊断核对风险与价格条件。' }
})

const canRun = computed(() => !bootstrapLoading.value && !queryLoading.value && Boolean(status.value?.stocks) && conflicts.value.length === 0)
const libraryTargetLabel = computed(() => `${libraryKind.value === 'satisfy' ? '满足规则' : '排除规则'} ${libraryGroupIndex.value + 1}`)

function openLibrary(kind: 'satisfy' | 'exclude', index: number, trigger: HTMLButtonElement) {
  libraryKind.value = kind
  libraryGroupIndex.value = index
  libraryTrigger.value = trigger
  libraryOpen.value = true
}

async function closeLibrary() {
  libraryOpen.value = false
  await nextTick()
  libraryTrigger.value?.focus()
}

async function commitSignals(codes: string[]) {
  addSignals(libraryKind.value, libraryGroupIndex.value, codes)
  await closeLibrary()
}

defineExpose({ refresh })
</script>

<template>
  <section class="technical-screener-page" aria-labelledby="technical-screener-page-title" :aria-busy="busy">
    <header class="technical-screener-intro">
      <div class="page-heading">
        <h1 id="technical-screener-page-title">技术面选股</h1>
        <p class="page-subtitle">全市场前复权技术信号缓存；事件与状态分离，普通筛选不触发逐股网络采集。</p>
      </div>
    </header>

    <section class="panel technical-decision" aria-labelledby="technical-decision-title">
      <div class="technical-decision__copy">
        <p class="eyebrow">{{ conclusion.eyebrow }}</p>
        <h2 id="technical-decision-title">{{ conclusion.title }}</h2>
        <p>{{ conclusion.detail }}</p>
      </div>
      <dl class="technical-decision__stats" aria-label="技术缓存与筛选摘要">
        <div><dt>缓存股票</dt><dd class="mono">{{ formatTechnicalInteger(status?.stocks) }}<small v-if="status?.stocks != null"> 只</small></dd></div>
        <div><dt>可用信号</dt><dd class="mono">{{ catalog ? `${availableSignalCount}/${catalog.signals.length}` : '—' }}</dd></div>
        <div><dt>当前命中</dt><dd class="mono">{{ formatTechnicalInteger(result?.matched) }}<small v-if="result?.matched != null"> 只</small></dd></div>
      </dl>
      <div class="technical-decision__action">
        <button class="primary-button" type="submit" form="technical-screener-form" :disabled="!canRun" :aria-busy="queryLoading">
          {{ queryLoading ? '筛选进行中' : result ? '按当前规则重新筛选' : '开始筛选' }}
        </button>
        <button class="secondary-button" type="button" :disabled="refreshSubmitting || refreshQueued || status?.state === 'running'" @click="submitRefresh">
          {{ refreshSubmitting ? '提交中' : refreshQueued || status?.state === 'running' ? '后台刷新中' : '刷新信号缓存' }}
        </button>
        <small>刷新是显式重型动作；提交后在后台执行。</small>
      </div>
    </section>

    <p v-if="refreshFeedback" class="technical-refresh-feedback" :class="{ 'is-error': refreshFeedback.includes('失败') }" aria-live="polite">{{ refreshFeedback }}</p>

    <div v-if="bootstrapLoading" class="panel technical-screener-loading" aria-live="polite">
      <strong>正在加载技术信号目录、缓存状态与保存方案</strong>
      <span>各接口独立失败；不会用示例数字填充。</span>
    </div>

    <div v-else class="technical-screener-layout">
      <TechnicalScreenerRules
        :rule-config="ruleConfig"
        :universe="universe"
        :minimum-amount-wan="minimumAmountWan"
        :default-timeframe="defaultTimeframe"
        :presets="presets"
        :schemes="schemes"
        :selected-scheme-id="selectedSchemeId"
        :selected-preset-id="selectedPresetId"
        :scheme-name="schemeName"
        :preset-read-only="presetReadOnly"
        :legacy-read-only="legacyReadOnly"
        :legacy-reason="legacyReason"
        :scheme-busy="schemeBusy"
        :scheme-feedback="schemeFeedback"
        :schemes-error="schemesError"
        :conflicts="conflicts"
        :definitions="definitions"
        @dirty="markDirty"
        @load-scheme="loadScheme"
        @update-scheme-name="schemeName = $event; selectedPresetId = ''; presetReadOnly = false; markDirty()"
        @copy-draft="copyToDraft"
        @save-scheme="saveScheme"
        @delete-scheme="deleteScheme"
        @add-group="addGroup"
        @remove-group="removeGroup"
        @open-library="openLibrary"
        @remove-condition="removeCondition"
        @restore-defaults="restoreConditionDefaults"
        @set-signal="setConditionSignal"
        @update-minimum-amount="minimumAmountWan = $event"
        @update-default-timeframe="defaultTimeframe = $event as TechnicalTimeframe"
        @run="runScreen"
      />

      <TechnicalScreenerResults
        :response="result"
        :stocks="stocks"
        :status-stocks="status?.stocks"
        :status-error="statusError"
        :loading="queryLoading"
        :has-submitted="hasSubmitted"
        :error="queryError"
        :stale="resultStale"
        @retry="runScreen"
        @stop="stopQuery"
        @refresh-cache="submitRefresh"
      />
    </div>

    <TechnicalScreenerGovernance
      v-if="!bootstrapLoading"
      :catalog="catalog"
      :status="status"
      :catalog-error="catalogError"
      :status-error="statusError"
      :history-code="historyCode"
      :history-events="historyEvents"
      :history-loading="historyLoading"
      :history-requested="historyRequested"
      :history-error="historyError"
      @update-history-code="historyCode = $event"
      @load-history="loadHistory"
    />

    <footer class="page-footer technical-screener-footer">
      <span>价格单位为元，涨跌幅为百分数，成交额为元；输入的 20 日均成交额按万元换算后提交。</span>
      <span>历史只记录事件命中，不计算收益、胜率或策略回测。</span>
    </footer>

    <TechnicalSignalDialog
      :open="libraryOpen"
      :signals="definitions"
      :target-label="libraryTargetLabel"
      @close="closeLibrary"
      @commit="commitSignals"
    />
    <dl class="technical-screener-stamp" aria-label="技术面选股数据日期与缓存状态">
      <div><dt>数据日期</dt><dd class="mono">{{ status?.data_date || '—' }}</dd></div>
      <div><dt>缓存状态</dt><dd><DataStatus :state="quality" :label="cacheLabel" /></dd></div>
      <div><dt>算法版本</dt><dd class="mono">{{ status?.algorithm_ver || catalog?.algorithm_ver || '—' }}</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.technical-screener-page { min-width: 0; display: grid; gap: 12px; }
.technical-screener-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.technical-screener-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.technical-screener-intro > div > p:last-child { max-width: 820px; margin: 7px 0 0; color: var(--text-2); }
.technical-screener-stamp { min-width: 520px; display: grid; grid-template-columns: minmax(110px, .7fr) minmax(150px, 1fr) minmax(160px, 1fr); gap: 10px; margin: 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.technical-screener-stamp div { min-width: 0; }
.technical-screener-stamp dt { color: var(--text-3); font-size: 12px; }
.technical-screener-stamp dd { margin: 3px 0 0; overflow-wrap: anywhere; font-size: 13px; font-weight: 720; }
.technical-decision { display: grid; grid-template-columns: minmax(0, 1fr) minmax(300px, .55fr) minmax(220px, .35fr); overflow: hidden; }
.technical-decision__copy { padding: 14px 16px; }
.technical-decision__copy h2 { margin: 0; font-size: clamp(18px, 1.5vw, 23px); line-height: 1.4; }
.technical-decision__copy > p:last-child { margin: 5px 0 0; color: var(--text-2); }
.technical-decision__stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0; border-left: 1px solid var(--border); }
.technical-decision__stats div { min-width: 0; display: grid; align-content: center; padding: 10px; border-right: 1px solid var(--border); }
.technical-decision__stats dt { color: var(--text-3); font-size: 12px; }
.technical-decision__stats dd { margin: 3px 0 0; font-size: 20px; font-weight: 760; }
.technical-decision__stats small { font-family: Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; font-size: 12px; }
.technical-decision__action { display: grid; align-content: center; gap: 6px; padding: 12px; background: var(--surface-2); }
.technical-decision__action button { width: 100%; }
.technical-decision__action small { color: var(--text-3); font-size: 12px; line-height: 1.4; }
.technical-refresh-feedback { margin: 0; padding: 9px 11px; border-left: 3px solid var(--info); background: color-mix(in srgb, var(--info) 8%, var(--surface)); }
.technical-refresh-feedback.is-error { border-left-color: var(--danger); }
.technical-screener-loading { min-height: 320px; display: grid; place-content: center; justify-items: center; gap: 8px; text-align: center; }
.technical-screener-loading span { color: var(--text-3); }
.technical-screener-layout { min-width: 0; display: grid; grid-template-columns: minmax(360px, .78fr) minmax(0, 1.22fr); gap: 12px; align-items: start; }
.technical-screener-footer { padding-top: 0; }
@media (max-width: 1240px) {
  .technical-screener-intro { align-items: stretch; flex-direction: column; gap: 12px; }
  .technical-screener-stamp { min-width: 0; }
  .technical-decision { grid-template-columns: minmax(0, 1fr) minmax(320px, .58fr); }
  .technical-decision__action { grid-column: 1 / -1; grid-template-columns: minmax(210px, 320px) minmax(210px, 320px) minmax(0, 1fr); align-items: center; }
}
@media (max-width: 1024px) { .technical-screener-layout { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 860px) {
  .technical-decision { grid-template-columns: minmax(0, 1fr); }
  .technical-decision__stats { border-top: 1px solid var(--border); border-left: 0; }
}
@media (max-width: 720px) {
  .technical-screener-page { gap: 8px; }
  .technical-screener-stamp { grid-template-columns: minmax(0, 1fr); }
  .technical-decision__stats { grid-template-columns: minmax(0, 1fr); }
  .technical-decision__stats div { min-height: 68px; border-right: 0; border-bottom: 1px solid var(--border); }
  .technical-decision__action { grid-template-columns: minmax(0, 1fr); }
}
</style>
