import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, postJson } from '../services/api'
import type {
  CompanyDisclosureResponse,
  CompanyHistoryEntry,
  CompanyInteractionResponse,
  CompanyKnowledgeResponse,
  CompanyModelStatus,
  CompanyRequestKey,
  CompanyResearchRunState,
  CompanyResourceState,
} from '../types/companyResearch'
import type { ResearchRun, ResearchRunLatestResponse, ResearchRunResponse } from '../types/researchRun'
import { RESEARCH_ACTIVE_STATUSES } from '../types/researchRun'
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

function runTimestamp(run: ResearchRun): number {
  const value = Date.parse(run.finished_at || run.updated_at || run.created_at)
  return Number.isFinite(value) ? value : Date.now()
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
  const disclosures = ref<CompanyResourceState<CompanyDisclosureResponse>>(emptyResource())
  const interactions = ref<CompanyResourceState<CompanyInteractionResponse>>(emptyResource())
  const history = ref<CompanyHistoryEntry[]>([])
  const activeHistoryId = ref('')
  const activeRunId = ref('')
  const saving = ref(false)
  const saveError = ref('')
  const saveMessage = ref('')
  const controllers: Partial<Record<CompanyRequestKey, AbortController>> = {}
  const generations: Record<CompanyRequestKey, number> = {
    status: 0,
    disclosures: 0,
    interactions: 0,
    research: 0,
    knowledge: 0,
  }
  let alive = false
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let stageTimer: ReturnType<typeof setInterval> | null = null
  let stageStartedAt = 0

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
  const evidenceLoading = computed(() => disclosures.value.loading || interactions.value.loading)
  const canSave = computed(() => runState.value === 'complete' && Boolean(content.value.trim()) && !saving.value)
  const refreshing = computed(() => running.value || saving.value || statusLoading.value || evidenceLoading.value)

  function persistHistory() {
    localStorage.setItem(COMPANY_HISTORY_KEY, JSON.stringify(history.value))
  }

  function clearStageTimer() {
    if (stageTimer) clearInterval(stageTimer)
    stageTimer = null
  }

  function clearPollTimer() {
    if (pollTimer) clearTimeout(pollTimer)
    pollTimer = null
  }

  function startElapsed(run: ResearchRun) {
    clearStageTimer()
    stageStartedAt = Date.parse(run.started_at || run.created_at)
    if (!Number.isFinite(stageStartedAt)) stageStartedAt = Date.now()
    const update = () => { stageSeconds.value = Math.max(0, Math.floor((Date.now() - stageStartedAt) / 1000)) }
    update()
    stageTimer = setInterval(update, 1000)
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

  function appendCompletedRun(run: ResearchRun) {
    const report = String(run.content || '').trim()
    if (!report || !COMPANY_CODE_RE.test(run.subject) || history.value.some((entry) => entry.id === run.run_id)) return
    const ts = runTimestamp(run)
    const entry: CompanyHistoryEntry = {
      id: run.run_id,
      code: run.subject,
      content: report.slice(0, 60_000),
      ts,
      incomplete: false,
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
      loadDisclosures(code),
      loadInteractions(code),
    ])
  }

  function showCompleted(run: ResearchRun) {
    clearPollTimer()
    clearStageTimer()
    activeRunId.value = ''
    codeInput.value = run.subject
    currentCode.value = run.subject
    content.value = String(run.content || '')
    runState.value = content.value.trim() ? 'complete' : 'error'
    stageText.value = ''
    stageSeconds.value = 0
    completedAt.value = runTimestamp(run)
    activeHistoryId.value = run.run_id
    appendCompletedRun(run)
    void loadEvidence(run.subject)
  }

  function showActive(run: ResearchRun) {
    activeRunId.value = run.run_id
    codeInput.value = run.subject
    currentCode.value = run.subject
    content.value = String(run.content || '')
    runState.value = 'loading'
    stageText.value = run.stage || '后台正在生成公司研究…'
    completedAt.value = null
    activeHistoryId.value = ''
    startElapsed(run)
  }

  function schedulePoll(runId: string) {
    clearPollTimer()
    if (!alive || !runId) return
    pollTimer = setTimeout(() => { void pollRun(runId) }, 2_000)
  }

  async function restoreLatest() {
    const guard = requestGuard('research', 15_000)
    try {
      const response = await getJson<ResearchRunLatestResponse>('/research/runs/latest?kind=company', guard.controller.signal)
      if (!guard.current()) return
      if (response.active) {
        runError.value = ''
        showActive(response.active)
        void loadEvidence(response.active.subject)
        schedulePoll(response.active.run_id)
        return
      }
      if (response.latest_completed) {
        showCompleted(response.latest_completed)
        if (response.latest && response.latest.run_id !== response.latest_completed.run_id && response.latest.status !== 'completed') {
          runError.value = `${response.latest.error || '最近一次后台研究未完成'}；当前展示上一次成功结果。`
        } else {
          runError.value = ''
        }
        return
      }
      if (response.latest) {
        clearPollTimer()
        clearStageTimer()
        activeRunId.value = ''
        codeInput.value = response.latest.subject
        currentCode.value = response.latest.subject
        runState.value = 'error'
        stageText.value = ''
        stageSeconds.value = 0
        runError.value = response.latest.error || '最近一次后台研究未完成。'
        void loadEvidence(response.latest.subject)
      }
    } catch (error) {
      if (!guard.current()) return
      runError.value = guard.timedOut()
        ? '后台任务状态查询超过 15 秒；生成任务不会因此停止。'
        : errorMessage(error, '暂时无法读取公司研究后台任务状态。')
    } finally {
      guard.finish()
    }
  }

  async function pollRun(runId: string) {
    if (!alive || !runId) return
    const guard = requestGuard('research', 15_000)
    let restoreAfterFailure = false
    try {
      const response = await getJson<ResearchRunResponse>(`/research/runs/${encodeURIComponent(runId)}`, guard.controller.signal)
      if (!guard.current() || response.run.run_id !== activeRunId.value) return
      const run = response.run
      if (RESEARCH_ACTIVE_STATUSES.includes(run.status)) {
        showActive(run)
        schedulePoll(run.run_id)
      } else if (run.status === 'completed') {
        runError.value = ''
        showCompleted(run)
      } else {
        restoreAfterFailure = true
        runError.value = run.error || '本次后台公司研究未完成。'
      }
    } catch (error) {
      if (!guard.current()) return
      runError.value = guard.timedOut()
        ? '本次状态查询超时；后台生成仍在继续，将自动重试。'
        : errorMessage(error, '后台任务状态暂不可用，将自动重试。')
      schedulePoll(runId)
    } finally {
      guard.finish()
    }
    if (restoreAfterFailure && alive) await restoreLatest()
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
    stageText.value = '正在提交公司研究后台任务…'
    stageSeconds.value = 0
    completedAt.value = null
    activeHistoryId.value = ''
    clearPollTimer()
    const evidenceTask = loadEvidence(code)
    const ready = await refreshStatus()
    if (!alive || code !== currentCode.value) return
    if (!ready) {
      runState.value = 'error'
      runError.value = `${modelReason.value} 本轮未创建后台研究任务。`
      stageText.value = ''
      await evidenceTask
      return
    }

    const guard = requestGuard('research', 15_000)
    try {
      const response = await postJson<ResearchRunResponse>('/research/runs', {
        kind: 'company', code,
      }, guard.controller.signal)
      if (!guard.current()) return
      showActive(response.run)
      schedulePoll(response.run.run_id)
    } catch (error) {
      if (!guard.current()) return
      runState.value = 'error'
      runError.value = guard.timedOut()
        ? '任务提交超过 15 秒；正在查询服务端是否已受理。'
        : errorMessage(error, '公司研究后台任务创建失败。')
    } finally {
      guard.finish()
      await evidenceTask
    }
    if (runState.value === 'error' && alive) await restoreLatest()
  }

  async function stopResearch() {
    const runId = activeRunId.value
    if (!running.value || !runId) return
    clearPollTimer()
    stageText.value = '正在停止后台任务…'
    const guard = requestGuard('research', 15_000)
    try {
      await postJson<ResearchRunResponse>(`/research/runs/${encodeURIComponent(runId)}/cancel`, {}, guard.controller.signal)
    } catch (error) {
      if (guard.current()) runError.value = errorMessage(error, '停止后台任务失败；将继续查询状态。')
    } finally {
      guard.finish()
    }
    if (alive) await restoreLatest()
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
    await restoreLatest()
  }

  onMounted(() => {
    alive = true
    try {
      history.value = normalizeCompanyHistory(JSON.parse(localStorage.getItem(COMPANY_HISTORY_KEY) || '[]'))
    } catch {
      history.value = []
    }
    void refreshStatus()
    void restoreLatest()
  })

  onBeforeUnmount(() => {
    alive = false
    clearPollTimer()
    clearStageTimer()
    // 这里只中止状态、证据和保存请求；服务端后台研究任务继续运行。
    ;(['status', 'disclosures', 'interactions', 'research', 'knowledge'] as CompanyRequestKey[])
      .forEach(abortRequest)
  })

  return {
    codeInput, currentCode, content, runState, runError, fieldError, stageText,
    stageSeconds, completedAt, statusLoading, statusError, modelReady, modelReason,
    disclosures, interactions, history, activeHistoryId, saving,
    saveError, saveMessage, running, evidenceLoading, canSave, refreshing,
    startResearch, stopResearch, selectHistory, saveToKnowledge, refresh,
  }
}
