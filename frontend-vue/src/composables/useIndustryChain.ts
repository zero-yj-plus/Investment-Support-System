import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { postEventStream, postJson } from '../services/api'
import type {
  IndustryChainHistoryEntry,
  IndustryChainKnowledgeResponse,
  IndustryChainRequestKey,
  IndustryChainRunState,
} from '../types/industryChain'
import {
  buildIndustryChainRequest,
  INDUSTRY_CHAIN_HISTORY_KEY,
  INDUSTRY_CHAIN_HISTORY_LIMIT,
  normalizeIndustryHistory,
  summarizeIndustryChain,
} from '../utils/industryChainFormat'

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}

export function useIndustryChain() {
  const industryInput = ref('')
  const currentIndustry = ref('')
  const content = ref('')
  const runState = ref<IndustryChainRunState>('idle')
  const stageText = ref('')
  const stageSeconds = ref(0)
  const runError = ref('')
  const fieldError = ref('')
  const history = ref<IndustryChainHistoryEntry[]>([])
  const activeHistoryId = ref('')
  const saving = ref(false)
  const saveError = ref('')
  const saveMessage = ref('')
  const controllers: Partial<Record<IndustryChainRequestKey, AbortController>> = {}
  const generations: Record<IndustryChainRequestKey, number> = { research: 0, knowledge: 0 }
  let alive = false
  let stopRequested = false
  let stageTimer: ReturnType<typeof setInterval> | null = null

  const running = computed(() => runState.value === 'loading')
  const summary = computed(() => summarizeIndustryChain(content.value))
  const canSave = computed(() => runState.value === 'complete' && Boolean(content.value.trim()) && !saving.value)
  const refreshing = computed(() => running.value || saving.value)

  function persistHistory() {
    localStorage.setItem(INDUSTRY_CHAIN_HISTORY_KEY, JSON.stringify(history.value))
  }

  function clearStageTimer() {
    if (stageTimer) clearInterval(stageTimer)
    stageTimer = null
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

  function appendHistory(incomplete: boolean) {
    if (!content.value.trim()) return
    const entry: IndustryChainHistoryEntry = {
      id: `chain-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      industry: currentIndustry.value,
      content: content.value.slice(0, 60_000),
      ts: Date.now(),
      incomplete,
    }
    history.value = [entry, ...history.value].slice(0, INDUSTRY_CHAIN_HISTORY_LIMIT)
    activeHistoryId.value = entry.id
    persistHistory()
  }

  async function startResearch(rawIndustry = industryInput.value) {
    if (running.value || saving.value) return
    const industry = String(rawIndustry || '').trim().slice(0, 120)
    fieldError.value = ''
    runError.value = ''
    saveError.value = ''
    saveMessage.value = ''
    if (!industry) {
      fieldError.value = '请输入行业或产业链名称'
      return
    }

    industryInput.value = industry
    currentIndustry.value = industry
    content.value = ''
    runState.value = 'loading'
    stageText.value = '正在匹配 TDX 板块目录、成员关系与当前行情…'
    stageSeconds.value = 0
    activeHistoryId.value = ''
    stopRequested = false
    clearStageTimer()
    stageTimer = setInterval(() => { stageSeconds.value += 1 }, 1000)
    const guard = requestGuard('research', 105_000)
    let completed = false

    try {
      await postEventStream('/chat', buildIndustryChainRequest(industry), {
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
          : '研究流未完整结束；部分正文已隔离，不能作为完整研究结论。'
        appendHistory(true)
      } else {
        runState.value = 'error'
        runError.value = guard.timedOut()
          ? '研究请求超过 105 秒，已停止等待；本轮没有形成结果。'
          : aborted && stopRequested
            ? '已停止本次产业链研究。'
            : errorMessage(error, '产业链研究暂不可用，请稍后重试')
      }
    } finally {
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

  function selectHistory(entry: IndustryChainHistoryEntry) {
    if (running.value || saving.value) return
    industryInput.value = entry.industry
    currentIndustry.value = entry.industry
    content.value = entry.content
    runState.value = entry.incomplete ? 'partial' : 'complete'
    runError.value = entry.incomplete ? '该记录是不完整输出，仅供查看，不能存入知识库。' : ''
    saveError.value = ''
    saveMessage.value = ''
    activeHistoryId.value = entry.id
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
    if (currentIndustry.value) await startResearch(currentIndustry.value)
  }

  onMounted(() => {
    alive = true
    try {
      history.value = normalizeIndustryHistory(JSON.parse(localStorage.getItem(INDUSTRY_CHAIN_HISTORY_KEY) || '[]'))
    } catch {
      history.value = []
    }
  })

  onBeforeUnmount(() => {
    alive = false
    clearStageTimer()
    abortRequest('research')
    abortRequest('knowledge')
  })

  return {
    industryInput, currentIndustry, content, runState, stageText, stageSeconds,
    runError, fieldError, history, activeHistoryId, saving, saveError, saveMessage,
    running, summary, canSave, refreshing, startResearch, stopResearch,
    selectHistory, saveToKnowledge, refresh,
  }
}
