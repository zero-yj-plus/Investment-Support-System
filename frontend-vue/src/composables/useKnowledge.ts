import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { deleteJson, getJson, postJson } from '../services/api'
import type {
  KnowledgeAction,
  KnowledgeApprovePayload,
  KnowledgeCreatePayload,
  KnowledgeMutationResponse,
  KnowledgeRejectPayload,
  KnowledgeResponse,
  KnowledgeSupersedePayload,
} from '../types/knowledge'

const REQUEST_TIMEOUT_MS = 15_000

function messageFrom(error: unknown, fallback: string) {
  if (error instanceof Error && error.message) return error.message
  return fallback
}

export function useKnowledge() {
  const response = ref<KnowledgeResponse | null>(null)
  const loading = ref(true)
  const refreshing = ref(false)
  const listError = ref('')
  const actionError = ref('')
  const actionMessage = ref('')
  const activeAction = ref<{ type: KnowledgeAction; id: string | null } | null>(null)

  let alive = false
  let listController: AbortController | null = null
  let createController: AbortController | null = null
  let approveController: AbortController | null = null
  let rejectController: AbortController | null = null
  let supersedeController: AbortController | null = null
  let deleteController: AbortController | null = null
  const generations = { list: 0, create: 0, approve: 0, reject: 0, supersede: 0, delete: 0 }

  const items = computed(() => Array.isArray(response.value?.items) ? response.value.items : [])
  const hasValidList = computed(() => Array.isArray(response.value?.items))

  function withTimeout(controller: AbortController) {
    const timer = window.setTimeout(() => controller.abort('timeout'), REQUEST_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }

  async function loadKnowledge() {
    if (refreshing.value) return
    listController?.abort()
    const controller = new AbortController()
    listController = controller
    const generation = ++generations.list
    const clearTimeout = withTimeout(controller)
    if (response.value) refreshing.value = true
    else loading.value = true
    listError.value = ''
    try {
      const payload = await getJson<KnowledgeResponse>('/knowledge', controller.signal)
      if (payload.ok === false) throw new Error(payload.error || '知识库接口返回失败')
      if (!Array.isArray(payload.items)) {
        throw new Error('知识库接口的 items 缺失或不是数组，不能当作空知识库。')
      }
      if (!alive || controller !== listController || generation !== generations.list) return
      response.value = payload
    } catch (error) {
      if (!alive || controller !== listController || generation !== generations.list) return
      if (controller.signal.aborted && controller.signal.reason !== 'timeout') return
      listError.value = controller.signal.reason === 'timeout'
        ? '知识库请求超过 15 秒，已停止等待。'
        : messageFrom(error, '知识库暂不可用，请稍后重试。')
    } finally {
      clearTimeout()
      if (alive && controller === listController && generation === generations.list) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  function actionController(type: KnowledgeAction) {
    if (type === 'create') return createController
    if (type === 'approve') return approveController
    if (type === 'reject') return rejectController
    if (type === 'supersede') return supersedeController
    return deleteController
  }

  function setActionController(type: KnowledgeAction, controller: AbortController | null) {
    if (type === 'create') createController = controller
    else if (type === 'approve') approveController = controller
    else if (type === 'reject') rejectController = controller
    else if (type === 'supersede') supersedeController = controller
    else deleteController = controller
  }

  async function mutate(
    type: KnowledgeAction,
    id: string | null,
    request: (signal: AbortSignal) => Promise<KnowledgeMutationResponse>,
    successMessage: string,
  ) {
    if (activeAction.value) return false
    actionController(type)?.abort()
    const controller = new AbortController()
    setActionController(type, controller)
    const generation = ++generations[type]
    const clearTimeout = withTimeout(controller)
    activeAction.value = { type, id }
    actionError.value = ''
    actionMessage.value = ''
    try {
      const payload = await request(controller.signal)
      if (payload.ok === false) throw new Error(payload.error || '知识治理接口返回失败')
      if (!alive || actionController(type) !== controller || generation !== generations[type]) return false
      actionMessage.value = successMessage
      await loadKnowledge()
      return true
    } catch (error) {
      if (!alive || actionController(type) !== controller || generation !== generations[type]) return false
      if (controller.signal.aborted && controller.signal.reason !== 'timeout') return false
      actionError.value = controller.signal.reason === 'timeout'
        ? '知识治理请求超过 15 秒，已停止等待；结果未知，请刷新核对后再操作。'
        : messageFrom(error, '知识治理动作失败，请稍后重试。')
      return false
    } finally {
      clearTimeout()
      if (alive && actionController(type) === controller && generation === generations[type]) {
        activeAction.value = null
      }
    }
  }

  function createKnowledge(payload: KnowledgeCreatePayload) {
    return mutate(
      'create',
      null,
      (signal) => postJson<KnowledgeMutationResponse>('/knowledge', payload, signal),
      '已保存；服务端将本次明确录入记为已批准规则。',
    )
  }

  function approveKnowledge(id: string, payload: KnowledgeApprovePayload) {
    return mutate(
      'approve',
      id,
      (signal) => postJson<KnowledgeMutationResponse>(`/knowledge/${encodeURIComponent(id)}/approve`, payload, signal),
      '服务端已批准；该规则会进入后续知识摘要。',
    )
  }

  function rejectKnowledge(id: string, payload: KnowledgeRejectPayload) {
    return mutate(
      'reject',
      id,
      (signal) => postJson<KnowledgeMutationResponse>(`/knowledge/${encodeURIComponent(id)}/reject`, payload, signal),
      '服务端已拒绝；该条目不会进入知识摘要。',
    )
  }

  function supersedeKnowledge(id: string, payload: KnowledgeSupersedePayload) {
    return mutate(
      'supersede',
      id,
      (signal) => postJson<KnowledgeMutationResponse>(`/knowledge/${encodeURIComponent(id)}/supersede`, payload, signal),
      '服务端已创建批准的新版本，并保留旧版本的替代关系。',
    )
  }

  function deleteKnowledge(id: string) {
    return mutate(
      'delete',
      id,
      (signal) => deleteJson<KnowledgeMutationResponse>(`/knowledge/${encodeURIComponent(id)}`, signal),
      '服务端已删除条目，并追加删除审计事件。',
    )
  }

  function clearActionFeedback() {
    actionError.value = ''
    actionMessage.value = ''
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible' && !loading.value && !refreshing.value && !activeAction.value) {
      void loadKnowledge()
    }
  }

  onMounted(() => {
    alive = true
    document.addEventListener('visibilitychange', onVisibilityChange)
    void loadKnowledge()
  })

  onBeforeUnmount(() => {
    alive = false
    generations.list += 1
    generations.create += 1
    generations.approve += 1
    generations.reject += 1
    generations.supersede += 1
    generations.delete += 1
    listController?.abort()
    createController?.abort()
    approveController?.abort()
    rejectController?.abort()
    supersedeController?.abort()
    deleteController?.abort()
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    response,
    items,
    hasValidList,
    loading,
    refreshing,
    listError,
    actionError,
    actionMessage,
    activeAction,
    loadKnowledge,
    createKnowledge,
    approveKnowledge,
    rejectKnowledge,
    supersedeKnowledge,
    deleteKnowledge,
    clearActionFeedback,
  }
}
