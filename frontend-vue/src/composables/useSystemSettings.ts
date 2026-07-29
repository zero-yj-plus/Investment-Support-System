import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { deleteJson, getJson, postJson, putJson } from '../services/api'
import type {
  AgentRunsResponse,
  DatasourcesResponse,
  DeliveryOutboxResponse,
  DeliveryReadinessResponse,
  DeliveryReceiptsResponse,
  JobsResponse,
  ModelConnectionPayload,
  ModelConnectionsResponse,
  MutationResponse,
  SettingsActionKey,
  SettingsRequestKey,
  SettingsResponse,
  StatusResponse,
} from '../types/settings'

const REQUEST_TIMEOUT_MS = 15_000

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

export function useSystemSettings() {
  const settings = ref<SettingsResponse | null>(null)
  const status = ref<StatusResponse | null>(null)
  const jobs = ref<JobsResponse | null>(null)
  const datasources = ref<DatasourcesResponse | null>(null)
  const readiness = ref<DeliveryReadinessResponse | null>(null)
  const receipts = ref<DeliveryReceiptsResponse | null>(null)
  const outbox = ref<DeliveryOutboxResponse | null>(null)
  const agentRuns = ref<AgentRunsResponse | null>(null)
  const models = ref<ModelConnectionsResponse | null>(null)
  const errors = reactive<Record<SettingsRequestKey, string>>({
    settings: '', status: '', jobs: '', datasources: '', readiness: '',
    receipts: '', outbox: '', agentRuns: '', models: '',
  })
  const pending = reactive<Record<SettingsRequestKey, boolean>>({
    settings: false, status: false, jobs: false, datasources: false, readiness: false,
    receipts: false, outbox: false, agentRuns: false, models: false,
  })
  const actionMessage = ref('')
  const actionError = ref('')
  const activeAction = ref<SettingsActionKey | null>(null)
  const lastCheckedAt = ref<Date | null>(null)

  let alive = false
  let lastRefreshAt = 0
  let settingsController: AbortController | null = null
  let statusController: AbortController | null = null
  let jobsController: AbortController | null = null
  let datasourcesController: AbortController | null = null
  let readinessController: AbortController | null = null
  let receiptsController: AbortController | null = null
  let outboxController: AbortController | null = null
  let agentRunsController: AbortController | null = null
  let modelsController: AbortController | null = null
  let saveController: AbortController | null = null
  let pushTestController: AbortController | null = null
  let modelController: AbortController | null = null
  let jobController: AbortController | null = null
  let deliveryController: AbortController | null = null
  const generations: Record<SettingsRequestKey, number> = {
    settings: 0, status: 0, jobs: 0, datasources: 0, readiness: 0,
    receipts: 0, outbox: 0, agentRuns: 0, models: 0,
  }
  const actionGenerations: Record<SettingsActionKey, number> = {
    save: 0, pushTest: 0, model: 0, job: 0, delivery: 0,
  }

  const loading = computed(() => Object.values(pending).some(Boolean))
  const settingsWritable = computed(() => Boolean(settings.value?.settings) && !errors.settings && !pending.settings)

  function requestController(key: SettingsRequestKey) {
    if (key === 'settings') return settingsController
    if (key === 'status') return statusController
    if (key === 'jobs') return jobsController
    if (key === 'datasources') return datasourcesController
    if (key === 'readiness') return readinessController
    if (key === 'receipts') return receiptsController
    if (key === 'outbox') return outboxController
    if (key === 'agentRuns') return agentRunsController
    return modelsController
  }

  function setRequestController(key: SettingsRequestKey, controller: AbortController | null) {
    if (key === 'settings') settingsController = controller
    else if (key === 'status') statusController = controller
    else if (key === 'jobs') jobsController = controller
    else if (key === 'datasources') datasourcesController = controller
    else if (key === 'readiness') readinessController = controller
    else if (key === 'receipts') receiptsController = controller
    else if (key === 'outbox') outboxController = controller
    else if (key === 'agentRuns') agentRunsController = controller
    else modelsController = controller
  }

  function actionController(key: SettingsActionKey) {
    if (key === 'save') return saveController
    if (key === 'pushTest') return pushTestController
    if (key === 'model') return modelController
    if (key === 'job') return jobController
    return deliveryController
  }

  function setActionController(key: SettingsActionKey, controller: AbortController | null) {
    if (key === 'save') saveController = controller
    else if (key === 'pushTest') pushTestController = controller
    else if (key === 'model') modelController = controller
    else if (key === 'job') jobController = controller
    else deliveryController = controller
  }

  function timeout(controller: AbortController) {
    const timer = window.setTimeout(() => controller.abort('timeout'), REQUEST_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }

  async function loadRequest<T extends { ok?: boolean; error?: string }>(
    key: SettingsRequestKey,
    request: (signal: AbortSignal) => Promise<T>,
    assign: (payload: T) => void,
    invalid: (payload: T) => string | null = (payload) => payload.ok === false ? (payload.error || '接口返回失败') : null,
  ) {
    requestController(key)?.abort()
    const controller = new AbortController()
    setRequestController(key, controller)
    const generation = ++generations[key]
    const clear = timeout(controller)
    pending[key] = true
    errors[key] = ''
    try {
      const payload = await request(controller.signal)
      const problem = invalid(payload)
      if (problem) throw new Error(problem)
      if (!alive || controller !== requestController(key) || generation !== generations[key]) return
      assign(payload)
    } catch (error) {
      if (!alive || controller !== requestController(key) || generation !== generations[key]) return
      if (controller.signal.aborted && controller.signal.reason !== 'timeout') return
      errors[key] = controller.signal.reason === 'timeout'
        ? '请求超过 15 秒，已停止等待。'
        : errorMessage(error, '接口暂不可用。')
    } finally {
      clear()
      if (alive && controller === requestController(key) && generation === generations[key]) pending[key] = false
    }
  }

  const loadSettings = () => loadRequest(
    'settings',
    (signal) => getJson<SettingsResponse>('/settings', signal),
    (payload) => { settings.value = payload },
    (payload) => payload.ok === false ? (payload.error || '设置接口返回失败') : (!payload.settings ? 'settings 字段缺失，不能执行覆盖保存。' : null),
  )
  const loadStatus = () => loadRequest('status', (signal) => getJson<StatusResponse>('/status', signal), (payload) => { status.value = payload })
  const loadJobs = () => loadRequest(
    'jobs',
    (signal) => getJson<JobsResponse>('/jobs', signal),
    (payload) => { jobs.value = payload },
    (payload) => payload.ok === false ? (payload.error || '任务接口返回失败') : (!Array.isArray(payload.jobs) ? 'jobs 缺失或不是数组，不能当作暂无任务。' : null),
  )
  const loadDatasources = () => loadRequest(
    'datasources',
    (signal) => getJson<DatasourcesResponse>('/datasources', signal),
    (payload) => { datasources.value = payload },
    (payload) => payload.ok === false ? (payload.error || '数据源接口返回失败') : (!Array.isArray(payload.sources) ? 'sources 缺失或不是数组，不能当作暂无来源。' : null),
  )
  const loadReadiness = () => loadRequest('readiness', (signal) => getJson<DeliveryReadinessResponse>('/delivery/readiness', signal), (payload) => { readiness.value = payload })
  const loadReceipts = () => loadRequest(
    'receipts',
    (signal) => getJson<DeliveryReceiptsResponse>('/delivery/receipts?limit=30', signal),
    (payload) => { receipts.value = payload },
    (payload) => payload.ok === false ? (payload.error || '回执接口返回失败') : (!Array.isArray(payload.receipts) ? 'receipts 缺失或不是数组，不能当作暂无回执。' : null),
  )
  const loadOutbox = () => loadRequest(
    'outbox',
    (signal) => getJson<DeliveryOutboxResponse>('/delivery/outbox?limit=30', signal),
    (payload) => { outbox.value = payload },
    (payload) => payload.ok === false ? (payload.error || '交付事务接口返回失败') : (!Array.isArray(payload.items) ? 'items 缺失或不是数组，不能当作暂无事务。' : null),
  )
  const loadAgentRuns = () => loadRequest(
    'agentRuns',
    (signal) => getJson<AgentRunsResponse>('/agent/runs?limit=12', signal),
    (payload) => { agentRuns.value = payload },
    (payload) => payload.ok === false ? (payload.error || 'Agent 账本接口返回失败') : (!Array.isArray(payload.items) ? 'items 缺失或不是数组，不能当作暂无运行记录。' : null),
  )
  const loadModels = () => loadRequest(
    'models',
    (signal) => getJson<ModelConnectionsResponse>('/settings/model-connections', signal),
    (payload) => { models.value = payload },
    (payload) => payload.ok === false ? (payload.error || '模型连接接口返回失败') : (!Array.isArray(payload.connections) ? 'connections 缺失或不是数组，不能当作暂无连接。' : null),
  )

  async function refreshAll() {
    if (loading.value) return
    await Promise.allSettled([
      loadSettings(), loadStatus(), loadJobs(), loadDatasources(), loadReadiness(),
      loadReceipts(), loadOutbox(), loadAgentRuns(), loadModels(),
    ])
    if (!alive) return
    lastRefreshAt = Date.now()
    lastCheckedAt.value = new Date()
  }

  async function mutate(
    key: SettingsActionKey,
    request: (signal: AbortSignal) => Promise<MutationResponse>,
    successMessage: string,
    reload: Array<() => Promise<void>>,
  ) {
    if (activeAction.value) return false
    actionController(key)?.abort()
    const controller = new AbortController()
    setActionController(key, controller)
    const generation = ++actionGenerations[key]
    const clear = timeout(controller)
    activeAction.value = key
    actionError.value = ''
    actionMessage.value = ''
    try {
      const payload = await request(controller.signal)
      if (payload.ok === false) throw new Error(payload.error || '操作未被服务端接受')
      if (!alive || actionController(key) !== controller || generation !== actionGenerations[key]) return false
      actionMessage.value = successMessage
      await Promise.allSettled(reload.map((fn) => fn()))
      return true
    } catch (error) {
      if (!alive || actionController(key) !== controller || generation !== actionGenerations[key]) return false
      if (controller.signal.aborted && controller.signal.reason !== 'timeout') return false
      actionError.value = controller.signal.reason === 'timeout'
        ? '操作超过 15 秒，结果未知。请先刷新核对，避免重复提交。'
        : errorMessage(error, '操作失败，请稍后重试。')
      return false
    } finally {
      clear()
      if (alive && actionController(key) === controller && generation === actionGenerations[key]) activeAction.value = null
    }
  }

  const saveSettings = (payload: Record<string, unknown>) => mutate(
    'save',
    (signal) => putJson<MutationResponse>('/settings', payload, signal),
    '设置已由服务端保存并热生效。',
    [loadSettings, loadReadiness, loadStatus],
  )
  const testPush = () => mutate(
    'pushTest',
    (signal) => postJson<MutationResponse>('/push/test', {}, signal),
    '测试消息已提交；请以外部平台回执判断是否最终送达。',
    [loadReadiness, loadReceipts, loadOutbox],
  )
  const testModel = (payload: ModelConnectionPayload & { connection_id?: string }) => mutate(
    'model',
    (signal) => postJson<MutationResponse>('/settings/model/test', payload, signal),
    '当前填写已通过连接测试；测试成功不等于已保存。',
    [],
  )
  const saveModel = (connectionId: string | null, payload: ModelConnectionPayload) => mutate(
    'model',
    (signal) => connectionId
      ? putJson<MutationResponse>(`/settings/model-connections/${encodeURIComponent(connectionId)}`, payload, signal)
      : postJson<MutationResponse>('/settings/model-connections', payload, signal),
    connectionId ? '模型连接已更新；密钥仍不会回显。' : '模型连接已保存；密钥仍不会回显。',
    [loadModels, loadSettings, loadStatus],
  )
  const activateModel = (connectionId: string) => mutate(
    'model',
    (signal) => postJson<MutationResponse>(`/settings/model-connections/${encodeURIComponent(connectionId)}/activate`, {}, signal),
    '当前模型连接已切换。',
    [loadModels, loadSettings, loadStatus],
  )
  const deleteModel = (connectionId: string) => mutate(
    'model',
    (signal) => deleteJson<MutationResponse>(`/settings/model-connections/${encodeURIComponent(connectionId)}`, signal),
    '模型连接已删除；核心行情与确定性规则不受影响。',
    [loadModels, loadSettings, loadStatus],
  )
  const runJob = (jobId: string) => mutate(
    'job',
    (signal) => postJson<MutationResponse>(`/jobs/${encodeURIComponent(jobId)}/run`, {}, signal),
    '任务已进入服务端队列；这不表示任务已经完成。',
    [loadJobs],
  )
  const acknowledgeDelivery = (deliveryId: string, reviewNote: string) => mutate(
    'delivery',
    (signal) => postJson<MutationResponse>(`/delivery/${encodeURIComponent(deliveryId)}/acknowledge`, { review_note: reviewNote }, signal),
    '已追加人工送达确认；原交付事实未被覆盖。',
    [loadReadiness, loadReceipts, loadOutbox],
  )
  const retryDelivery = (deliveryId: string, ambiguous: boolean, reviewNote: string) => mutate(
    'delivery',
    (signal) => postJson<MutationResponse>(`/delivery/${encodeURIComponent(deliveryId)}/retry`, {
      confirm_possible_duplicate: ambiguous,
      review_note: reviewNote,
    }, signal),
    '交付重试已提交；请等待新回执，不把提交状态当作已送达。',
    [loadReadiness, loadReceipts, loadOutbox],
  )

  function clearFeedback() {
    actionMessage.value = ''
    actionError.value = ''
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible' && Date.now() - lastRefreshAt > 60_000 && !loading.value && !activeAction.value) {
      void refreshAll()
    }
  }

  onMounted(() => {
    alive = true
    document.addEventListener('visibilitychange', onVisibilityChange)
    void refreshAll()
  })

  onBeforeUnmount(() => {
    alive = false
    ;(Object.keys(generations) as SettingsRequestKey[]).forEach((key) => { generations[key] += 1 })
    ;(Object.keys(actionGenerations) as SettingsActionKey[]).forEach((key) => { actionGenerations[key] += 1 })
    settingsController?.abort(); statusController?.abort(); jobsController?.abort(); datasourcesController?.abort()
    readinessController?.abort(); receiptsController?.abort(); outboxController?.abort(); agentRunsController?.abort(); modelsController?.abort()
    saveController?.abort(); pushTestController?.abort(); modelController?.abort(); jobController?.abort(); deliveryController?.abort()
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    settings, status, jobs, datasources, readiness, receipts, outbox, agentRuns, models,
    errors, pending, loading, settingsWritable, actionMessage, actionError, activeAction,
    lastCheckedAt, refreshAll, saveSettings, testPush, testModel, saveModel, activateModel,
    deleteModel, runJob, acknowledgeDelivery, retryDelivery, clearFeedback,
  }
}
