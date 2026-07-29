<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import type { JobsResponse, ScheduleSettings, SettingsView, StatusResponse, SystemJob } from '../types/settings'
import { formatDuration, jobId, jobLabel, jobStatus, textValue } from '../utils/settingsFormat'

const props = defineProps<{
  jobs: JobsResponse | null
  settings: SettingsView | null
  status: StatusResponse | null
  loading: boolean
  error: string
  writable: boolean
  busy: boolean
}>()
const emit = defineEmits<{
  save: [payload: Record<string, unknown>]
  runJob: [jobId: string]
  retry: []
}>()

const scheduleLabels: Array<[keyof ScheduleSettings, string]> = [
  ['morning', '早盘内参'], ['midday', '尾盘草案'], ['tail', '尾盘定稿'],
  ['close', '收盘复盘'], ['evening', '次日策略'], ['archive', '每日存档'],
]
const schedule = reactive<Record<string, string>>({})
watch(() => props.settings?.schedule, (value) => {
  scheduleLabels.forEach(([key]) => { schedule[key] = typeof value?.[key] === 'string' ? value[key] || '' : '' })
}, { immediate: true, deep: true })

const rows = computed(() => Array.isArray(props.jobs?.jobs) ? props.jobs.jobs : null)
const currentFailures = computed(() => rows.value?.filter((job) => jobStatus(job, props.status?.date).tone === 'danger') || [])
const needsAttention = computed(() => rows.value?.filter((job) => jobStatus(job, props.status?.date).tone === 'warning') || [])

function outputs(job: SystemJob) {
  const nested = job.last_result?.output_objects
  if (Array.isArray(nested)) return nested
  return Array.isArray(job.last_output_objects) ? job.last_output_objects : []
}

function saveSchedule() {
  const payload: Record<string, string> = {}
  scheduleLabels.forEach(([key]) => { if (schedule[key]) payload[key] = schedule[key] })
  emit('save', { schedule: payload })
}
</script>

<template>
  <section id="settings-tasks" class="settings-section panel" aria-labelledby="settings-tasks-title">
    <header class="settings-section__heading">
      <div><p class="eyebrow">AUTOMATION</p><h2 id="settings-tasks-title">任务与运行计划</h2><p>先核对当前失败，再调整服务端热生效时点。</p></div>
      <span v-if="rows" class="settings-count mono">{{ rows.length }} 个真实任务</span>
    </header>

    <div class="settings-task-grid">
      <form class="settings-subpanel" @submit.prevent="saveSchedule">
        <div class="settings-subpanel__title"><h3>定时任务时间</h3><span>北京时间</span></div>
        <div v-if="!settings && loading" class="settings-local-state" role="status">正在读取任务时点…</div>
        <div v-else-if="!settings" class="settings-local-state is-error" role="alert">设置未加载，保存已锁定。</div>
        <div v-else class="settings-schedule-grid">
          <label v-for="([key, label]) in scheduleLabels" :key="key" :for="`settings-${key}`">
            <span>{{ label }}</span>
            <input :id="`settings-${key}`" v-model="schedule[key]" type="time" :disabled="!writable || busy" />
            <small v-if="!schedule[key]">未配置</small>
          </label>
        </div>
        <p class="settings-help">保存后由服务端重新应用计划；空值不会被猜成默认时间。</p>
        <button class="primary-button" type="submit" :disabled="!writable || busy">{{ busy ? '保存中…' : '保存任务时间' }}</button>
      </form>

      <article class="settings-subpanel settings-task-result">
        <div class="settings-subpanel__title"><h3>当前任务结论</h3><span>非行情结论</span></div>
        <template v-if="rows">
          <dl>
            <div><dt>当前失败</dt><dd :class="{ 'is-danger': currentFailures.length }">{{ currentFailures.length }} 个</dd></div>
            <div><dt>需要关注</dt><dd :class="{ 'is-warning': needsAttention.length }">{{ needsAttention.length }} 个</dd></div>
            <div><dt>已登记</dt><dd>{{ rows.length }} 个</dd></div>
            <div><dt>生成时点</dt><dd class="mono">{{ textValue(jobs?.generated_at) }}</dd></div>
          </dl>
          <p v-if="rows.length === 0" class="settings-empty">服务端返回真实空数组：暂无已登记任务。</p>
          <p v-else-if="!currentFailures.length && !needsAttention.length" class="settings-ok">未发现当前失败或阻断；历史运行仍以明细为准。</p>
        </template>
        <div v-else-if="loading" class="settings-local-state" role="status">正在读取任务状态…</div>
        <div v-else class="settings-local-state is-error" role="alert">{{ error || '任务接口未返回可验证的 jobs 数组。' }}<button type="button" @click="emit('retry')">重新读取</button></div>
      </article>
    </div>

    <div v-if="rows?.length" class="settings-table-wrap" tabindex="0" aria-label="任务运行状态表，可横向滚动">
      <table>
        <thead><tr><th>任务</th><th>状态</th><th>下次运行</th><th>最近成功</th><th>耗时</th><th>错误</th><th>实际产物</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="job in rows" :key="jobId(job) || jobLabel(job)">
            <td><strong>{{ jobLabel(job) }}</strong><small class="mono">{{ jobId(job) || '标识未提供' }}</small></td>
            <td><span class="settings-state" :class="`is-${jobStatus(job, status?.date).tone}`">{{ jobStatus(job, status?.date).label }}</span></td>
            <td class="mono">{{ textValue(job.next_run_at || job.next_run) }}</td>
            <td class="mono">{{ textValue(job.last_ok) }}</td>
            <td class="mono">{{ formatDuration(job.last_result?.duration_ms ?? job.last_duration_ms) }}</td>
            <td>{{ textValue(job.last_result?.error || job.last_result?.error_code || job.last_error || job.last_error_code, '无已知错误') }}</td>
            <td>
              <span v-if="!outputs(job).length">尚无产物</span>
              <ul v-else class="settings-output-list">
                <li v-for="(output, index) in outputs(job)" :key="`${output.content_id}-${index}`">
                  <strong>{{ textValue(output.kind || output.report_type, '任务产物') }}</strong>
                  <code>{{ textValue(output.content_id) }}</code>
                  <small>{{ output.trade_day ? `交易日 ${output.trade_day}` : '交易日未提供' }}{{ output.version != null ? ` · v${output.version}` : '' }}</small>
                </li>
              </ul>
            </td>
            <td><button type="button" :disabled="busy || job.enabled === false || job.manual_run?.enabled === false || !jobId(job)" @click="emit('runJob', jobId(job))">{{ job.retry?.eligible ? '重试任务' : '手动执行' }}</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="rows?.length" class="settings-mobile-list" aria-label="任务运行状态">
      <article v-for="job in rows" :key="`mobile-${jobId(job) || jobLabel(job)}`">
        <div><h3>{{ jobLabel(job) }}</h3><span class="settings-state" :class="`is-${jobStatus(job, status?.date).tone}`">{{ jobStatus(job, status?.date).label }}</span></div>
        <dl><div><dt>下次运行</dt><dd>{{ textValue(job.next_run_at || job.next_run) }}</dd></div><div><dt>最近成功</dt><dd>{{ textValue(job.last_ok) }}</dd></div><div><dt>耗时</dt><dd>{{ formatDuration(job.last_result?.duration_ms ?? job.last_duration_ms) }}</dd></div><div><dt>错误</dt><dd>{{ textValue(job.last_result?.error || job.last_result?.error_code || job.last_error || job.last_error_code, '无已知错误') }}</dd></div></dl>
        <button type="button" :disabled="busy || job.enabled === false || job.manual_run?.enabled === false || !jobId(job)" @click="emit('runJob', jobId(job))">{{ job.retry?.eligible ? '重试任务' : '手动执行' }}</button>
      </article>
    </div>
  </section>
</template>

<style scoped>
.settings-section { min-width: 0; overflow: hidden; }
.settings-section__heading, .settings-subpanel__title { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.settings-section__heading { min-height: 72px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.settings-section__heading h2, .settings-subpanel__title h3 { margin: 0; }
.settings-section__heading h2 { font-size: 19px; }
.settings-section__heading p:last-child { margin: 2px 0 0; color: var(--text-3); font-size: 13px; }
.settings-count { color: var(--text-3); font-size: 13px; }
.settings-task-grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(280px, .65fr); gap: 10px; padding: 12px; }
.settings-subpanel { min-width: 0; padding: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }
.settings-subpanel__title span { color: var(--text-3); font-size: 12px; }
.settings-schedule-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }
.settings-schedule-grid label { display: grid; gap: 5px; min-width: 0; color: var(--text-2); font-size: 13px; }
.settings-schedule-grid input { width: 100%; min-height: 44px; padding: 7px 9px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); }
.settings-schedule-grid small, .settings-help { color: var(--text-3); font-size: 12px; }
.settings-help { margin: 10px 0; }
.settings-subpanel .primary-button, .settings-local-state button, .settings-table-wrap button, .settings-mobile-list button { min-height: 44px; padding: 7px 12px; border: 1px solid var(--border); border-radius: 6px; cursor: pointer; }
.settings-subpanel .primary-button:disabled, .settings-table-wrap button:disabled, .settings-mobile-list button:disabled { cursor: wait; opacity: .58; }
.settings-task-result dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin: 10px 0; }
.settings-task-result dl div { padding: 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); }
.settings-task-result dt { color: var(--text-3); font-size: 12px; }
.settings-task-result dd { margin: 2px 0 0; font-size: 16px; font-weight: 750; }
.settings-empty, .settings-ok { margin: 8px 0 0; color: var(--text-2); font-size: 13px; }
.settings-ok { color: var(--info); }
.settings-local-state { min-height: 76px; margin-top: 10px; padding: 12px; color: var(--text-2); }
.settings-local-state.is-error { color: var(--danger); }
.settings-local-state button { display: block; margin-top: 8px; background: var(--surface); color: var(--text); }
.settings-table-wrap { max-width: 100%; overflow-x: auto; margin: 0 12px 12px; border: 1px solid var(--border); border-radius: 6px; }
table { width: 100%; min-width: 1180px; border-collapse: collapse; }
th, td { padding: 9px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; font-size: 13px; }
th { background: var(--surface-2); color: var(--text-3); font-size: 12px; }
td strong, td small { display: block; }
td small { color: var(--text-3); }
.settings-state { display: inline-flex; min-height: 26px; align-items: center; padding: 2px 7px; border: 1px solid currentColor; border-radius: 99px; font-size: 12px; font-weight: 700; }
.settings-state.is-success { color: var(--info); }.settings-state.is-warning, .is-warning { color: var(--warning); }.settings-state.is-danger, .is-danger { color: var(--danger); }.settings-state.is-neutral { color: var(--text-2); }
.settings-output-list { display: grid; gap: 5px; min-width: 200px; margin: 0; padding: 0; list-style: none; }
.settings-output-list li { padding: 6px; border: 1px solid var(--border); border-radius: 4px; background: var(--surface-2); }
.settings-output-list code { display: block; overflow-wrap: anywhere; }
.settings-mobile-list { display: none; }
.mono { font-family: "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }
@media (max-width: 960px) { .settings-task-grid { grid-template-columns: 1fr; } }
@media (max-width: 760px) { .settings-table-wrap { display: none; } .settings-mobile-list { display: grid; gap: 8px; padding: 0 12px 12px; } .settings-mobile-list article { padding: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); } .settings-mobile-list article > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; } .settings-mobile-list h3 { margin: 0; font-size: 16px; } .settings-mobile-list dl { display: grid; gap: 6px; margin: 10px 0; } .settings-mobile-list dl div { display: grid; grid-template-columns: 104px minmax(0, 1fr); gap: 8px; } .settings-mobile-list dt { color: var(--text-3); font-size: 12px; } .settings-mobile-list dd { margin: 0; overflow-wrap: anywhere; } .settings-mobile-list button { width: 100%; background: var(--surface); } }
@media (max-width: 620px) { .settings-section__heading { align-items: flex-start; flex-direction: column; } .settings-schedule-grid { grid-template-columns: 1fr; } .settings-subpanel .primary-button { width: 100%; } }
</style>
