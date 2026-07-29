<script setup lang="ts">
import { computed } from 'vue'
import type {
  DatasourcesResponse, DeliveryReadinessResponse, JobsResponse, ModelConnectionsResponse,
  SettingsRequestKey, SettingsResponse, StatusResponse,
} from '../types/settings'
import { jobStatus } from '../utils/settingsFormat'

const props = defineProps<{
  settings: SettingsResponse | null
  status: StatusResponse | null
  jobs: JobsResponse | null
  datasources: DatasourcesResponse | null
  readiness: DeliveryReadinessResponse | null
  models: ModelConnectionsResponse | null
  errors: Record<SettingsRequestKey, string>
  loading: boolean
}>()
const emit = defineEmits<{ primary: [target: string] }>()

const jobRows = computed(() => Array.isArray(props.jobs?.jobs) ? props.jobs.jobs : null)
const sourceRows = computed(() => Array.isArray(props.datasources?.sources) ? props.datasources.sources : null)
const modelRows = computed(() => Array.isArray(props.models?.connections) ? props.models.connections : null)
const failedJobs = computed(() => jobRows.value?.filter((job) => jobStatus(job, props.status?.date).tone === 'danger') || [])
const attentionJobs = computed(() => jobRows.value?.filter((job) => jobStatus(job, props.status?.date).tone === 'warning') || [])
const coreSourceKeys = new Set(['tdx_api', 'tencent', 'universe'])
const coreDown = computed(() => sourceRows.value?.filter((row) => row.status === 'down' && coreSourceKeys.has(String(row.key || ''))) || [])
const degradedSources = computed(() => sourceRows.value?.filter((row) => ['down', 'degraded'].includes(String(row.status || ''))) || [])
const deliveryEnabled = computed(() => props.settings?.settings?.push_enabled === true)

const conclusion = computed(() => {
  if (!props.settings?.settings && props.errors.settings) return { tone: 'danger', label: '设置不可写', detail: '读取失败时已锁定覆盖保存，避免误写空配置。', action: '重新检查', target: 'refresh' }
  if (coreDown.value.length) return { tone: 'danger', label: '基础数据链路存在失败', detail: `${coreDown.value.length} 个主链路最近调用失败；只影响对应数据能力。`, action: '查看数据健康', target: 'settings-data' }
  if (failedJobs.value.length) return { tone: 'warning', label: '系统部分就绪', detail: `${failedJobs.value.length} 个当前任务失败，需要先核对错误与产物。`, action: '处理任务异常', target: 'settings-tasks' }
  if (deliveryEnabled.value && props.readiness && props.readiness.launch_ready !== true) return { tone: 'warning', label: '核心可用，自动发送未就绪', detail: '报告仍可保留在系统内；请完善渠道或人工核验项。', action: '处理发送设置', target: 'settings-delivery' }
  const importantErrors = ['jobs', 'datasources', 'readiness'].filter((key) => props.errors[key as SettingsRequestKey])
  if (importantErrors.length) return { tone: 'warning', label: '部分状态暂不可核验', detail: '已成功分组仍可使用；失败分组没有被显示为正常或空数据。', action: '重新检查', target: 'refresh' }
  if (props.loading && !props.settings?.settings) return { tone: 'neutral', label: '正在核对系统状态', detail: '各接口独立读取，慢接口不会抹掉已成功结果。', action: '检查中', target: '' }
  return { tone: 'success', label: '未发现当前阻断项', detail: deliveryEnabled.value ? '核心链路与自动发送状态已通过当前检查。' : '核心链路可用；自动发送处于明确关闭状态。', action: '查看任务计划', target: 'settings-tasks' }
})

const summaries = computed(() => [
  {
    label: '任务运行',
    tone: !jobRows.value ? (props.errors.jobs ? 'danger' : 'neutral') : (failedJobs.value.length ? 'danger' : (attentionJobs.value.length ? 'warning' : 'success')),
    value: !jobRows.value ? (props.errors.jobs ? '暂不可用' : '检查中') : (failedJobs.value.length ? `${failedJobs.value.length} 个当前失败` : (attentionJobs.value.length ? `${attentionJobs.value.length} 个需关注` : (jobRows.value.length ? '未发现当前异常' : '暂无已登记任务'))),
    detail: jobRows.value ? `真实登记 ${jobRows.value.length} 个任务` : '没有把接口失败当作空任务中心',
  },
  {
    label: '数据健康',
    tone: !sourceRows.value ? (props.errors.datasources ? 'danger' : 'neutral') : (coreDown.value.length ? 'danger' : (degradedSources.value.length ? 'warning' : 'success')),
    value: !sourceRows.value ? (props.errors.datasources ? '暂不可用' : '检查中') : (coreDown.value.length ? `${coreDown.value.length} 个基础链路失败` : (degradedSources.value.length ? `${degradedSources.value.length} 个链路降级` : (sourceRows.value.length ? '未发现已知失败' : '真实空来源列表'))),
    detail: sourceRows.value ? `${sourceRows.value.length} 条被动健康记录` : 'idle 与 down 将严格分开',
  },
  {
    label: '自动发送',
    tone: props.errors.readiness ? 'danger' : (!deliveryEnabled.value ? 'neutral' : (props.readiness?.launch_ready === true ? 'success' : 'warning')),
    value: props.errors.readiness ? '状态暂不可读' : (!deliveryEnabled.value ? '已关闭' : (props.readiness?.launch_ready === true ? '已就绪' : (props.readiness ? '需要配置' : '检查中'))),
    detail: !deliveryEnabled.value ? '报告留在系统内，不自动外发' : '提交、平台接受与送达分别核验',
  },
  {
    label: '模型连接',
    tone: props.errors.models ? 'warning' : 'neutral',
    value: props.errors.models ? '状态暂不可读' : (!modelRows.value ? '检查中' : (modelRows.value.length ? `${modelRows.value.length} 个已保存连接` : '未配置（可选）')),
    detail: '未配置不阻断行情与确定性规则',
  },
])

function primaryAction() {
  if (conclusion.value.target) emit('primary', conclusion.value.target)
}
</script>

<template>
  <section class="settings-readiness panel" :class="`is-${conclusion.tone}`" aria-labelledby="settings-conclusion-title">
    <div class="settings-readiness__main">
      <p class="eyebrow">CURRENT SYSTEM CONCLUSION</p>
      <h2 id="settings-conclusion-title">{{ conclusion.label }}</h2>
      <p>{{ conclusion.detail }}</p>
      <button type="button" class="primary-button" :disabled="!conclusion.target" @click="primaryAction">{{ conclusion.action }}</button>
    </div>
    <div class="settings-readiness__grid" aria-label="系统设置摘要">
      <article v-for="item in summaries" :key="item.label" :class="`is-${item.tone}`">
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
        <small>{{ item.detail }}</small>
      </article>
    </div>
  </section>
</template>

<style scoped>
.settings-readiness { position: relative; display: grid; grid-template-columns: minmax(280px, .86fr) minmax(0, 1.6fr); min-width: 0; overflow: hidden; border-left: 3px solid var(--border-strong); }
.settings-readiness.is-success { border-left-color: var(--info); }
.settings-readiness.is-warning { border-left-color: var(--warning); }
.settings-readiness.is-danger { border-left-color: var(--danger); }
.settings-readiness__main { padding: 16px; border-right: 1px solid var(--border); }
.settings-readiness__main h2 { margin: 0; font-size: 22px; }
.settings-readiness__main > p:not(.eyebrow) { margin: 5px 0 14px; color: var(--text-2); }
.settings-readiness__main button { min-height: 44px; padding: 8px 14px; border-radius: 6px; cursor: pointer; }
.settings-readiness__main button:disabled { cursor: wait; opacity: .58; }
.settings-readiness__grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; padding: 12px; }
.settings-readiness__grid article { min-width: 0; padding: 10px 11px; border: 1px solid var(--border); border-left: 3px solid var(--border-strong); border-radius: 6px; background: var(--surface-2); }
.settings-readiness__grid article.is-success { border-left-color: var(--info); }
.settings-readiness__grid article.is-warning { border-left-color: var(--warning); }
.settings-readiness__grid article.is-danger { border-left-color: var(--danger); }
.settings-readiness__grid span, .settings-readiness__grid small { display: block; color: var(--text-3); font-size: 12px; }
.settings-readiness__grid strong { display: block; margin: 3px 0; font-size: 16px; }
@media (max-width: 960px) { .settings-readiness { grid-template-columns: 1fr; } .settings-readiness__main { border-right: 0; border-bottom: 1px solid var(--border); } }
@media (max-width: 560px) { .settings-readiness__grid { grid-template-columns: 1fr; } .settings-readiness__main button { width: 100%; } }
</style>
