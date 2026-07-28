import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, postEventStream, postJson } from '../services/api'
import type {
  CompanyDisclosureResponse,
  CompanyHistoryEntry,
  CompanyInteractionResponse,
  CompanyKnowledgeResponse,
  CompanyModelStatus,
  CompanyRequestKey,
  CompanyResearchEnrichment,
  CompanyResearchRunState,
  CompanyResourceState,
} from '../types/companyResearch'
import {
  COMPANY_CODE_RE,
  COMPANY_HISTORY_KEY,
  COMPANY_HISTORY_LIMIT,
  normalizeCompanyCode,
  normalizeCompanyHistory,
} from '../utils/companyResearchFormat'

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function emptyResource<T>(): CompanyResourceState<T> {
  return { loading: false, data: null, error: '' }
}

export function useCompanyResearch() {
  const codeInput = ref('')
  const currentCode = ref('')
  const content = ref('')
  const runState = ref<CompanyResearchRunState>('idle')
  const runError = ref('')
  const fieldError = ref('')
  const stageText = ref('')
  const stageSeconds = ref(0)
  const completedAt = ref<number | null>(null)
  const status = ref<CompanyModelStatus | null>(null)
  const statusLoading = ref(false)
  const statusError = ref('')
  const enrichment = ref<CompanyResourceState<CompanyResearchEnrichment>>(emptyResource())
  const disclosures = ref<CompanyResourceState<CompanyDisclosureResponse>>(emptyResource())
  const interactions = ref<CompanyResourceState<CompanyInteractionResponse>>(emptyResource())
  const history = ref<CompanyHistoryEntry[]>([])
  const activeHistoryId = ref('')
  const saving = ref(false)
  const saveError = ref('')
  const saveMessage = ref('')
  const controllers: Partial<Record<CompanyRequestKey, AbortController>> = {}
  const generations: Record<CompanyRequestKey, number> = {
    status: 0,
    enrichment: 0,
    disclosures: 0,
    interactions: 0,
    research: 0,
    knowledge: 0,
  }
  let alive = false
  let stopRequested = false
  let stageTimer: ReturnType<typeof setInterval> | null = null

  const running = computed(() => runState.value === 'loading')
  const modelReady = computed(() => {
    const model = status.value?.model_connection
    const capability = status.value?.hermes?.agent_capability
    return model?.configured === true && model.connected !== false && capability?.ready !== false
  })
  const modelReason = computed(() => {
    if (statusError.value) return statusError.value
    const model = status.value?.model_connection
    const capability = status.value?.hermes?.agent_capability
    if (!model?.configured) return 'AI 增强未配置；结构化证据仍可核验。'
    if (model.connected === false || capability?.ready === false) {
      return capability?.reason || '模型连接或 Agent 权限预检未通过；结构化证据仍可核验。'
    }
    return modelReady.value ? 'AI 研究连接已就绪。' : 'AI 研究连接状态待确认。'
  })
  const evidenceLoading = computed(() => enrichment.value.loading || disclosures.value.loading || interactions.value.loading)
  const canSave = computed(() => runState.value === 'complete' && Boolean(content.value.trim()) && !saving.value)
  const refreshing = computed(() => running.value || saving.value || statusLoading.value || evidenceLoading.value)

  function persistHistory() {
    localStorage.setItem(COMPANY_HISTORY_KEY, JSON.stringify(history.value))
  }

  function clearStageTimer() {
    if (stageTimer) clearInterval(stageTimer)
    stageTimer = null
  }

  function abortRequest(key: CompanyRequestKey) {
    generations[key] += 1
    controllers[key]?.abort()
    delete controllers[key]
  }

  function requestGuard(key: CompanyRequestKey, timeoutMs: number) {
    abortRequest(key)
    const controller = new AbortController()
    const generation = ++generations[key]
    let timedOut = false
    const timeout = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, timeoutMs)
    controllers[key] = controller
    return {
      controller,
      timedOut: () => timedOut,
      current: () => alive && controllers[key] === controller && generations[key] === generation,
      finish: () => {
        clearTimeout(timeout)
        if (controllers[key] === controller) delete controllers[key]
      },
    }
  }

  function appendHistory(incomplete: boolean) {
    if (!content.value.trim() || !COMPANY_CODE_RE.test(currentCode.value)) return
    const ts = Date.now()
    const entry: CompanyHistoryEntry = {
      id: `company-${ts}-${currentCode.value}-${Math.random().toString(16).slice(2)}`,
      code: currentCode.value,
      content: content.value.slice(0, 60_000),
      ts,
      incomplete,
    }
    history.value = [entry, ...history.value].slice(0, COMPANY_HISTORY_LIMIT)
    activeHistoryId.value = entry.id
    completedAt.value = ts
    persistHistory()
  }

  async function refreshStatus() {
    const guard = requestGuard('status', 10_000)
    statusLoading.value = true
    statusError.value = ''
    try {
      const response = await getJson<CompanyModelStatus>('/status', guard.controller.signal)
      if (!guard.current()) return false
      status.value = response
      return response.model_connection?.configured === true
        && response.model_connection.connected !== false
        && response.hermes?.agent_capability?.ready !== false
    } catch (error) {
      if (!guard.current()) return false
      status.value = null
      statusError.value = guard.timedOut()
        ? 'AI 状态检查超过 10 秒，无法确认连接；结构化证据仍可核验。'
        : errorMessage(error, 'AI 状态接口暂不可用；结构化证据仍可核验。')
      return false
    } finally {
      guard.finish()
      if (alive) statusLoading.value = false
    }
  }

  async function loadEnrichment(code: string) {
    const guard = requestGuard('enrichment', 20_000)
    enrichment.value = { loading: true, data: null, error: '' }
    try {
      const response = await getJson<CompanyResearchEnrichment>(`/stock/${encodeURIComponent(code)}/research-enrichment`, guard.controller.signal)
      if (!guard.current() || code !== currentCode.value) return
      enrichment.value = { loading: false, data: response, error: '' }
    } catch (error) {
      if (!guard.current() || code !== currentCode.value) return
      enrichment.value = {
        loading: false,
        data: null,
        error: guard.timedOut() ? '盘后研究底座请求超时。' : errorMessage(error, '盘后研究底座暂不可用。'),
      }
    } finally {
      guard.finish()
    }
  }

  async function loadDisclosures(code: string) {
    const guard = requestGuard('disclosures', 20_000)
    disclosures.value = { loading: true, data: null, error: '' }
    try {
      const response = await getJson<CompanyDisclosureResponse>(`/disclosures/announcements?code=${encodeURIComponent(code)}&limit=6`, guard.controller.signal)
      if (!guard.current() || code !== currentCode.value) return
      disclosures.value = { loading: false, data: response, error: '' }
    } catch (error) {
      if (!guard.current() || code !== currentCode.value) return
      disclosures.value = {
        loading: false,
        data: null,
        error: guard.timedOut() ? '官方公告请求超时；不能据此判断“无公告”。' : errorMessage(error, '官方公告接口失败；不能据此判断“无公告”。'),
      }
    } finally {
      guard.finish()
    }
  }

  async function loadInteractions(code: string) {
    const guard = requestGuard('interactions', 20_000)
    interactions.value = { loading: true, data: null, error: '' }
    try {
      const response = await getJson<CompanyInteractionResponse>(`/interactions/management?code=${encodeURIComponent(code)}&limit=6`, guard.controller.signal)
      if (!guard.current() || code !== currentCode.value) return
      interactions.value = { loading: false, data: response, error: '' }
    } catch (error) {
      if (!guard.current() || code !== currentCode.value) return
      interactions.value = {
        loading: false,
        data: null,
        error: guard.timedOut() ? '互动问答请求超时；这不是“无记录”。' : errorMessage(error, '互动问答接口失败；这不是“无记录”。'),
      }
    } finally {
      guard.finish()
    }
  }

  async function loadEvidence(code: string) {
    await Promise.allSettled([
      loadEnrichment(code),
      loadDisclosures(code),
      loadInteractions(code),
    ])
  }

  async function startResearch(rawCode = codeInput.value) {
    if (running.value || saving.value) return
    const code = normalizeCompanyCode(rawCode)
    codeInput.value = code
    fieldError.value = ''
    runError.value = ''
    saveError.value = ''
    saveMessage.value = ''
    if (!COMPANY_CODE_RE.test(code)) {
      fieldError.value = '请输入 6 位股票代码'
      return
    }

    currentCode.value = code
    content.value = ''
    runState.value = 'loading'
    stageText.value = '正在核验官方公告、互动问答与盘后研究底座…'
    stageSeconds.value = 0
    completedAt.value = null
    activeHistoryId.value = ''
    stopRequested = false
    clearStageTimer()
    stageTimer = setInterval(() => { stageSeconds.value += 1 }, 1000)
    const evidenceTask = loadEvidence(code)
    const ready = await refreshStatus()
    if (!alive || code !== currentCode.value) return
    if (!ready) {
      clearStageTimer()
      runState.value = 'error'
      runError.value = `${modelReason.value} 本轮未生成研究正文。`
      stageText.value = ''
      stageSeconds.value = 0
      await evidenceTask
      return
    }

    const guard = requestGuard('research', 120_000)
    let completed = false
    try {
      await postEventStream('/chat', {
        mode: 'company',
        message: '生成单公司深度研究报告',
        code,
        industry: '',
        history: [],
        images: [],
      }, {
        signal: guard.controller.signal,
        onStage(stage) {
          if (guard.current()) stageText.value = stage
        },
        onDelta(delta) {
          if (guard.current()) content.value += delta
        },
        onDone() {
          if (guard.current()) completed = true
        },
      })
      if (!guard.current()) return
      if (!completed || !content.value.trim()) throw new Error('研究流未返回完整正文，已按不完整输出隔离')
      runState.value = 'complete'
      appendHistory(false)
    } catch (error) {
      if (!guard.current()) return
      const aborted = error instanceof DOMException && error.name === 'AbortError'
      if (content.value.trim()) {
        runState.value = 'partial'
        runError.value = stopRequested
          ? '已停止生成；部分正文已隔离，不能存入知识库。'
          : '研究流未完整结束；部分正文已隔离，不能作为完整公司研究结论。'
        appendHistory(true)
      } else {
        runState.value = 'error'
        runError.value = guard.timedOut()
          ? '研究请求超过 120 秒，已停止等待；本轮没有形成正文。'
          : aborted && stopRequested
            ? '已停止本次公司研究。'
            : errorMessage(error, '公司研究暂不可用；结构化证据仍可查看。')
      }
    } finally {
      await evidenceTask
      clearStageTimer()
      guard.finish()
      if (alive) {
        stageText.value = ''
        stageSeconds.value = 0
      }
    }
  }

  function stopResearch() {
    if (!running.value) return
    stopRequested = true
    controllers.research?.abort()
  }

  function selectHistory(entry: CompanyHistoryEntry) {
    if (running.value || saving.value) return
    codeInput.value = entry.code
    currentCode.value = entry.code
    content.value = entry.content
    runState.value = entry.incomplete ? 'partial' : 'complete'
    runError.value = entry.incomplete ? '该历史记录是不完整输出，仅供查看，不能存入知识库。' : ''
    completedAt.value = entry.ts
    activeHistoryId.value = entry.id
    saveError.value = ''
    saveMessage.value = ''
    void loadEvidence(entry.code)
  }

  async function saveToKnowledge() {
    if (!canSave.value) return
    const guard = requestGuard('knowledge', 15_000)
    saving.value = true
    saveError.value = ''
    saveMessage.value = ''
    try {
      const response = await postJson<CompanyKnowledgeResponse>('/knowledge', {
        category: '复盘笔记',
        title: `单公司深度研究：${currentCode.value}`,
        content: content.value,
      }, guard.controller.signal)
      if (!guard.current()) return
      if (response.ok === false) throw new Error(response.error || '知识库接口返回保存失败')
      saveMessage.value = '已写入知识库（复盘笔记）；不等于已审批，也不等于已进入模型知识。'
    } catch (error) {
      if (!guard.current()) return
      saveError.value = guard.timedOut()
        ? '保存请求超过 15 秒，已停止等待。'
        : errorMessage(error, '写入知识库失败，请稍后重试。')
    } finally {
      guard.finish()
      if (alive) saving.value = false
    }
  }

  async function refresh() {
    await refreshStatus()
    if (currentCode.value) await loadEvidence(currentCode.value)
  }

  onMounted(() => {
    alive = true
    try {
      history.value = normalizeCompanyHistory(JSON.parse(localStorage.getItem(COMPANY_HISTORY_KEY) || '[]'))
    } catch {
      history.value = []
    }
    void refreshStatus()
  })

  onBeforeUnmount(() => {
    alive = false
    clearStageTimer()
    ;(['status', 'enrichment', 'disclosures', 'interactions', 'research', 'knowledge'] as CompanyRequestKey[])
      .forEach(abortRequest)
  })

  return {
    codeInput, currentCode, content, runState, runError, fieldError, stageText,
    stageSeconds, completedAt, statusLoading, statusError, modelReady, modelReason,
    enrichment, disclosures, interactions, history, activeHistoryId, saving,
    saveError, saveMessage, running, evidenceLoading, canSave, refreshing,
    startResearch, stopResearch, selectHistory, saveToKnowledge, refresh,
  }
}
