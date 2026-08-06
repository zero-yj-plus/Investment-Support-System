import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ApiError, deleteJson, getJson, postJson } from '../services/api'
import type {
  IndustryChainHistoryEntry,
  IndustryChainKnowledgeResponse,
  IndustryChainRequestKey,
  IndustryChainRunState,
} from '../types/industryChain'
import type { ResearchRun, ResearchRunDeleteResponse, ResearchRunLatestResponse, ResearchRunResponse } from '../types/researchRun'
import { RESEARCH_ACTIVE_STATUSES } from '../types/researchRun'
import {
  INDUSTRY_CHAIN_HISTORY_KEY,
  INDUSTRY_CHAIN_HISTORY_LIMIT,
  normalizeIndustryHistory,
  summarizeIndustryChain,
} from '../utils/industryChainFormat'

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

function runTimestamp(run: ResearchRun): number {
  const value = Date.parse(run.finished_at || run.updated_at || run.created_at)
  return Number.isFinite(value) ? value : Date.now()
}

export function useIndustryChain() {
  const industryInput = ref('')
  const focusInput = ref('')
  const currentIndustry = ref('')
  const content = ref('')
  const runState = ref<IndustryChainRunState>('idle')
  const stageText = ref('')
  const stageSeconds = ref(0)
  const runError = ref('')
  const fieldError = ref('')
  const history = ref<IndustryChainHistoryEntry[]>([])
  const activeHistoryId = ref('')
  const activeRunId = ref('')
  const saving = ref(false)
  const saveError = ref('')
  const saveMessage = ref('')
  const deletingHistoryId = ref('')
  const historyError = ref('')
  const historyMessage = ref('')
  const controllers: Partial<Record<IndustryChainRequestKey, AbortController>> = {}
  const generations: Record<IndustryChainRequestKey, number> = { research: 0, knowledge: 0, delete: 0 }
  let alive = false
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let stageTimer: ReturnType<typeof setInterval> | null = null
  let stageStartedAt = 0

  const running = computed(() => runState.value === 'loading')
  const summary = computed(() => summarizeIndustryChain(content.value))
  const canSave = computed(() => runState.value === 'complete' && Boolean(content.value.trim()) && !saving.value && !deletingHistoryId.value)
  const refreshing = computed(() => running.value || saving.value || Boolean(deletingHistoryId.value))

  function persistHistory() {
    localStorage.setItem(INDUSTRY_CHAIN_HISTORY_KEY, JSON.stringify(history.value))
  }

  function clearPollTimer() {
    if (pollTimer) clearTimeout(pollTimer)
    pollTimer = null
  }

  function clearStageTimer() {
    if (stageTimer) clearInterval(stageTimer)
    stageTimer = null
  }

  function startElapsed(run: ResearchRun) {
    clearStageTimer()
    stageStartedAt = Date.parse(run.started_at || run.created_at)
    if (!Number.isFinite(stageStartedAt)) stageStartedAt = Date.now()
    const update = () => { stageSeconds.value = Math.max(0, Math.floor((Date.now() - stageStartedAt) / 1000)) }
    update()
    stageTimer = setInterval(update, 1000)
  }

  function abortRequest(key: IndustryChainRequestKey) {
    generations[key] += 1
    controllers[key]?.abort()
    delete controllers[key]
  }

  function requestGuard(key: IndustryChainRequestKey, timeoutMs: number) {
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
    if (!report || history.value.some((entry) => entry.id === run.run_id)) return
    const entry: IndustryChainHistoryEntry = {
      id: run.run_id,
      industry: run.subject,
      focus: run.focus || '',
      content: report.slice(0, 60_000),
      ts: runTimestamp(run),
      incomplete: false,
    }
    history.value = [entry, ...history.value].slice(0, INDUSTRY_CHAIN_HISTORY_LIMIT)
    persistHistory()
  }

  function showCompleted(run: ResearchRun) {
    clearPollTimer()
    clearStageTimer()
    activeRunId.value = ''
    industryInput.value = run.subject
    currentIndustry.value = run.subject
    focusInput.value = run.focus || ''
    content.value = String(run.content || '')
    runState.value = content.value.trim() ? 'complete' : 'error'
    stageText.value = ''
    stageSeconds.value = 0
    activeHistoryId.value = run.run_id
    appendCompletedRun(run)
  }

  function showActive(run: ResearchRun) {
    activeRunId.value = run.run_id
    industryInput.value = run.subject
    currentIndustry.value = run.subject
    focusInput.value = run.focus || ''
    content.value = String(run.content || '')
    runState.value = 'loading'
    stageText.value = run.stage || '后台正在生成产业链研究…'
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
      const response = await getJson<ResearchRunLatestResponse>('/research/runs/latest?kind=chain', guard.controller.signal)
      if (!guard.current()) return
      if (response.active) {
        runError.value = ''
        showActive(response.active)
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
        currentIndustry.value = response.latest.subject
        industryInput.value = response.latest.subject
        focusInput.value = response.latest.focus || ''
        runState.value = 'error'
        stageText.value = ''
        stageSeconds.value = 0
        runError.value = response.latest.error || '最近一次后台研究未完成。'
      }
    } catch (error) {
      if (!guard.current()) return
      runError.value = guard.timedOut()
        ? '后台任务状态查询超过 15 秒；生成任务不会因此停止。'
        : errorMessage(error, '暂时无法读取后台研究任务状态。')
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
        runError.value = run.error || '本次后台研究未完成。'
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

  async function startResearch(rawIndustry = industryInput.value, rawFocus = focusInput.value) {
    if (running.value || saving.value) return
    const industry = String(rawIndustry || '').trim().slice(0, 120)
    const focus = String(rawFocus || '').trim().slice(0, 1000)
    fieldError.value = ''
    runError.value = ''
    saveError.value = ''
    saveMessage.value = ''
    if (!industry) {
      fieldError.value = '请输入行业或产业链名称'
      return
    }

    industryInput.value = industry
    focusInput.value = focus
    currentIndustry.value = industry
    content.value = ''
    runState.value = 'loading'
    stageText.value = '正在提交后台研究任务…'
    stageSeconds.value = 0
    activeHistoryId.value = ''
    clearPollTimer()
    const guard = requestGuard('research', 15_000)
    try {
      const response = await postJson<ResearchRunResponse>('/research/runs', {
        kind: 'chain', industry, focus,
      }, guard.controller.signal)
      if (!guard.current()) return
      showActive(response.run)
      schedulePoll(response.run.run_id)
    } catch (error) {
      if (!guard.current()) return
      runState.value = 'error'
      runError.value = guard.timedOut()
        ? '任务提交超过 15 秒；正在查询服务端是否已受理。'
        : errorMessage(error, '产业链后台任务创建失败。')
    } finally {
      guard.finish()
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

  function selectHistory(entry: IndustryChainHistoryEntry) {
    if (running.value || saving.value || deletingHistoryId.value) return
    industryInput.value = entry.industry
    focusInput.value = entry.focus || ''
    currentIndustry.value = entry.industry
    content.value = entry.content
    runState.value = entry.incomplete ? 'partial' : 'complete'
    runError.value = entry.incomplete ? '该记录是不完整输出，仅供查看，不能存入知识库。' : ''
    saveError.value = ''
    saveMessage.value = ''
    activeHistoryId.value = entry.id
  }

  function resetHistorySelection() {
    industryInput.value = ''
    focusInput.value = ''
    currentIndustry.value = ''
    content.value = ''
    runState.value = 'idle'
    runError.value = ''
    activeHistoryId.value = ''
    saveError.value = ''
    saveMessage.value = ''
  }

  async function deleteHistory(entry: IndustryChainHistoryEntry) {
    if (running.value || saving.value || deletingHistoryId.value || !entry.id) return false
    deletingHistoryId.value = entry.id
    historyError.value = ''
    historyMessage.value = ''
    const serverBacked = /^rr_[0-9a-f]{32}$/.test(entry.id)
    const wasActive = activeHistoryId.value === entry.id
    try {
      if (serverBacked) {
        const guard = requestGuard('delete', 15_000)
        try {
          await deleteJson<ResearchRunDeleteResponse>(`/research/runs/${encodeURIComponent(entry.id)}`, guard.controller.signal)
          if (!guard.current()) return false
        } catch (error) {
          if (!guard.current()) return false
          if (!(error instanceof ApiError && error.status === 404)) {
            historyError.value = guard.timedOut()
              ? '删除请求超过 15 秒，记录仍保留。'
              : errorMessage(error, '删除产业链研究记录失败。')
            return false
          }
        } finally {
          guard.finish()
        }
      }
      if (!alive) return false
      history.value = history.value.filter((item) => item.id !== entry.id)
      persistHistory()
      if (wasActive) {
        resetHistorySelection()
        if (serverBacked) await restoreLatest()
        else if (history.value[0]) {
          deletingHistoryId.value = ''
          selectHistory(history.value[0])
        }
      }
      if (!alive) return false
      historyMessage.value = `已删除产业链研究记录：${entry.industry}`
      return true
    } finally {
      if (alive) deletingHistoryId.value = ''
    }
  }

  async function saveToKnowledge() {
    if (!canSave.value) return
    const guard = requestGuard('knowledge', 15_000)
    saving.value = true
    saveError.value = ''
    saveMessage.value = ''
    try {
      const response = await postJson<IndustryChainKnowledgeResponse>('/knowledge', {
        category: '复盘笔记',
        title: `产业链研究：${currentIndustry.value}`,
        content: content.value,
      }, guard.controller.signal)
      if (!guard.current()) return
      if (response.ok === false) throw new Error(response.error || '知识库接口返回保存失败')
      saveMessage.value = '已写入知识库（复盘笔记）；是否进入模型知识仍以审批状态为准。'
    } catch (error) {
      if (!guard.current()) return
      saveError.value = guard.timedOut()
        ? '保存请求超过 15 秒，已停止等待。'
        : errorMessage(error, '写入知识库失败，请稍后重试')
    } finally {
      guard.finish()
      if (alive) saving.value = false
    }
  }

  async function refresh() {
    await restoreLatest()
  }

  onMounted(() => {
    alive = true
    try {
      history.value = normalizeIndustryHistory(JSON.parse(localStorage.getItem(INDUSTRY_CHAIN_HISTORY_KEY) || '[]'))
    } catch {
      history.value = []
    }
    void restoreLatest()
  })

  onBeforeUnmount(() => {
    alive = false
    clearPollTimer()
    clearStageTimer()
    // 这里只中止短轮询；服务端后台研究任务继续运行。
    abortRequest('research')
    abortRequest('knowledge')
    abortRequest('delete')
  })

  return {
    industryInput, focusInput, currentIndustry, content, runState, stageText, stageSeconds,
    runError, fieldError, history, activeHistoryId, saving, saveError, saveMessage,
    deletingHistoryId, historyError, historyMessage,
    running, summary, canSave, refreshing, startResearch, stopResearch,
    selectHistory, deleteHistory, saveToKnowledge, refresh,
  }
}
