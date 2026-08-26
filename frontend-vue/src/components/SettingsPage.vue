<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import SettingsDataPanel from './SettingsDataPanel.vue'
import SettingsDeliveryPanel from './SettingsDeliveryPanel.vue'
import SettingsGovernance from './SettingsGovernance.vue'
import SettingsModelPanel from './SettingsModelPanel.vue'
import SettingsReadiness from './SettingsReadiness.vue'
import SettingsTaskPanel from './SettingsTaskPanel.vue'
import { useSystemSettings } from '../composables/useSystemSettings'

type SettingsSection = 'tasks' | 'delivery' | 'models' | 'data' | 'governance'
const props = defineProps<{ section: SettingsSection }>()
const emit = defineEmits<{ refreshState: [value: boolean] }>()
const {
  settings, status, jobs, datasources, readiness, receipts, outbox, agentRuns, models,
  errors, pending, loading, settingsWritable, actionMessage, actionError, activeAction,
  lastCheckedAt, refreshAll, saveSettings, saveScheduledPush, testPush, testModel, saveModel, activateModel,
  deleteModel, runJob, acknowledgeDelivery, retryDelivery, clearFeedback,
} = useSystemSettings()

const showLoadingSkeleton = ref(false)
let loadingDelayTimer: ReturnType<typeof setTimeout> | null = null
const hasAnyResponse = computed(() => Boolean(settings.value || status.value || jobs.value || datasources.value || readiness.value || receipts.value || outbox.value || agentRuns.value || models.value))
const initialLoading = computed(() => loading.value && !hasAnyResponse.value)
const checkedLabel = computed(() => lastCheckedAt.value
  ? new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(lastCheckedAt.value)
  : '尚未完成检查')

watch([loading, activeAction], ([isLoading, action]) => emit('refreshState', isLoading || Boolean(action)), { immediate: true })
watch(initialLoading, (value) => {
  if (loadingDelayTimer) clearTimeout(loadingDelayTimer)
  showLoadingSkeleton.value = false
  if (value) loadingDelayTimer = setTimeout(() => { showLoadingSkeleton.value = true }, 320)
}, { immediate: true })

function primaryAction(target: string) {
  if (target === 'refresh') {
    void refreshAll()
    return
  }
  if (target.startsWith('settings-')) window.location.hash = `#${target}`
}

function retryAll() {
  clearFeedback()
  void refreshAll()
}

function updateScheduledPush(key: Parameters<typeof saveScheduledPush>[0], enabled: boolean, complete: (saved: boolean) => void) {
  void saveScheduledPush(key, enabled).then(complete)
}

onBeforeUnmount(() => {
  if (loadingDelayTimer) clearTimeout(loadingDelayTimer)
  emit('refreshState', false)
})

defineExpose({ refresh: refreshAll })
</script>

<template>
  <div class="settings-page">
    <header class="settings-page__header">
      <div class="page-heading"><h1 id="settings-page-title">系统设置</h1><p class="page-subtitle">核对任务、数据、交付、模型与治理状态；设置页不生成交易结论。</p></div>
    </header>

    <nav class="settings-section-nav panel" aria-label="系统设置分区">
      <a href="#settings-tasks" :class="{ active: props.section === 'tasks' }" :aria-current="props.section === 'tasks' ? 'page' : undefined">任务计划</a>
      <a href="#settings-delivery" :class="{ active: props.section === 'delivery' }" :aria-current="props.section === 'delivery' ? 'page' : undefined">交付推送</a>
      <a href="#settings-models" :class="{ active: props.section === 'models' }" :aria-current="props.section === 'models' ? 'page' : undefined">模型连接</a>
      <a href="#settings-data" :class="{ active: props.section === 'data' }" :aria-current="props.section === 'data' ? 'page' : undefined">数据健康</a>
      <a href="#settings-governance" :class="{ active: props.section === 'governance' }" :aria-current="props.section === 'governance' ? 'page' : undefined">版本治理</a>
    </nav>

    <p v-if="actionMessage" class="settings-feedback is-success" aria-live="polite">{{ actionMessage }}</p>
    <p v-if="actionError" class="settings-feedback is-error" role="alert">{{ actionError }}</p>

    <section v-if="initialLoading && !showLoadingSkeleton" class="settings-connect panel" role="status" aria-live="polite">正在并行读取系统设置与运行状态…</section>
    <section v-else-if="initialLoading" class="settings-loading" role="status" aria-label="正在加载系统设置">
      <div class="panel settings-loading__hero"></div><div class="panel"></div><div class="panel"></div><div class="panel"></div>
    </section>

    <template v-else>
      <SettingsReadiness :settings="settings" :status="status" :jobs="jobs" :datasources="datasources" :readiness="readiness" :models="models" :errors="errors" :loading="loading" @primary="primaryAction" />

      <SettingsTaskPanel v-if="props.section === 'tasks'" :jobs="jobs" :settings="settings?.settings || null" :status="status" :loading="pending.jobs || pending.settings" :error="errors.jobs" :writable="settingsWritable" :busy="activeAction === 'save' || activeAction === 'job'" @save="saveSettings" @save-scheduled-push="updateScheduledPush" @run-job="runJob" @retry="retryAll" />

      <SettingsDeliveryPanel
        v-else-if="props.section === 'delivery'"
        :settings="settings?.settings || null" :readiness="readiness" :receipts="receipts" :outbox="outbox"
        :loading="{ settings: pending.settings, readiness: pending.readiness, receipts: pending.receipts, outbox: pending.outbox }"
        :errors="{ settings: errors.settings, readiness: errors.readiness, receipts: errors.receipts, outbox: errors.outbox }"
        :writable="settingsWritable" :busy="activeAction === 'save'" :push-testing="activeAction === 'pushTest'" :delivery-busy="activeAction === 'delivery'"
        @save="saveSettings" @test-push="testPush" @acknowledge="acknowledgeDelivery" @retry-delivery="retryDelivery" @retry="retryAll"
      />

      <SettingsModelPanel v-else-if="props.section === 'models'" :response="models" :settings="settings?.settings || null" :loading="pending.models" :error="errors.models" :busy="activeAction === 'model'" @test="testModel" @save="saveModel" @activate="activateModel" @delete="deleteModel" @retry="retryAll" />

      <SettingsDataPanel v-else-if="props.section === 'data'" :response="datasources" :loading="pending.datasources" :error="errors.datasources" @retry="retryAll" />

      <SettingsGovernance v-else :status="status" :settings="settings?.settings || null" :runs="agentRuns" :loading="{ status: pending.status, runs: pending.agentRuns }" :errors="{ status: errors.status, runs: errors.agentRuns }" @retry="retryAll" />

      <footer class="settings-footer panel"><span>缺失、真实空数组、降级和接口失败保持独立语义。</span><span>敏感信息仅显示配置状态，明文不进入浏览器。</span><span>系统只提供决策支持与本地模拟，不连接券商下单。</span></footer>
    </template>
    <div class="settings-page__stamp"><span>最近完成检查</span><strong class="mono">{{ checkedLabel }}</strong></div>
  </div>
</template>

<style scoped>
.settings-page { display: grid; min-width: 0; gap: 12px; scroll-behavior: smooth; }.settings-page__header { display: flex; min-height: 0; align-items: center; justify-content: space-between; gap: 16px; padding-block: 2px; }.settings-page__header h1 { margin: 0; font-size: 24px; }.settings-page__header p:last-child { margin: 0; color: var(--text-2); }.settings-page__stamp { display: grid; gap: 2px; justify-items: end; color: var(--text-3); font-size: 12px; }.settings-page__stamp strong { color: var(--text); font-size: 14px; }
.settings-section-nav { display: flex; flex-wrap: wrap; gap: 4px; padding: 4px; }.settings-section-nav a { display: inline-flex; min-height: 44px; align-items: center; padding: 7px 12px; border: 1px solid transparent; border-radius: 6px; color: var(--text-2); text-decoration: none; }.settings-section-nav a:hover { border-color: var(--border); background: var(--surface-2); color: var(--text); }.settings-section-nav a.active { border-color: var(--border-strong); background: var(--surface-2); color: var(--text); font-weight: 700; }
.settings-feedback { margin: 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }.settings-feedback.is-success { border-color: color-mix(in srgb, var(--info) 52%, var(--border)); color: var(--info); }.settings-feedback.is-error { border-color: color-mix(in srgb, var(--danger) 52%, var(--border)); color: var(--danger); }
.settings-connect { min-height: 96px; padding: 26px 16px; color: var(--text-2); }.settings-loading { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.settings-loading > div { position: relative; min-height: 260px; overflow: hidden; background: var(--surface-2); }.settings-loading__hero { grid-column: 1/-1; min-height: 190px !important; }.settings-loading > div::after { position: absolute; inset: 0; background: color-mix(in srgb, var(--text-3) 11%, transparent); content: ""; animation: settings-pulse 1.2s ease-in-out infinite alternate; }
.settings-page :deep(.panel[id]) { scroll-margin-top: 84px; }.settings-footer { display: flex; flex-wrap: wrap; gap: 4px 16px; padding: 10px 12px; color: var(--text-3); font-size: 12px; }.mono { font-family: "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }
@keyframes settings-pulse { from { opacity: .3; } to { opacity: .75; } }
@media (max-width: 720px) { .settings-page { font-size: 18px; }.settings-page__header { align-items: flex-start; flex-direction: column; }.settings-page__stamp { justify-items: start; }.settings-section-nav { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }.settings-section-nav a { justify-content: center; }.settings-loading { grid-template-columns: 1fr; }.settings-loading__hero { grid-column: auto; } }
@media (max-width: 420px) { .settings-section-nav { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) { .settings-loading > div::after { animation: none; }.settings-page { scroll-behavior: auto; } }
</style>
