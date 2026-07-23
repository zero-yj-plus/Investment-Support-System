import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, postEventStream, postJson } from '../services/api'
import type { MarketFlowResponse } from '../types/dashboard'
import type {
  PremarketActionResponse,
  PremarketReportResponse,
  PremarketStatusResponse,
} from '../types/premarket'

function todayInChina(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date())
}

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : '数据读取失败'
}

export function usePremarketReport() {
  const report = ref<PremarketReportResponse | null>(null)
  const liveFlow = ref<MarketFlowResponse | null>(null)
  const loading = ref(true)
  const refreshing = ref(false)
  const generating = ref(false)
  const pushing = ref(false)
  const error = ref('')
  const flowError = ref('')
  const generationStage = ref('')
  const generationPreview = ref('')
  const actionNotice = ref('')
  const reportDate = ref(todayInChina())
  let loadController: AbortController | null = null
  let flowController: AbortController | null = null
  let actionController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null

  const hasReport = computed(() => Boolean(report.value?.ok !== false && report.value?.content_md))

  async function loadReport() {
    loadController?.abort()
    loadController = new AbortController()
    try {
      const payload = await getJson<PremarketReportResponse>(
        `/reports/morning?date=${encodeURIComponent(reportDate.value)}`,
        loadController.signal,
      )
      if (payload.ok === false || !payload.content_md) {
        report.value = payload
        error.value = payload.error || '该日期暂无盘前报告'
      } else {
        report.value = payload
        error.value = ''
      }
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      error.value = errorMessage(caught)
    }
  }

  async function refreshFlow() {
    flowController?.abort()
    flowController = new AbortController()
    try {
      const payload = await getJson<MarketFlowResponse>('/market/flow', flowController.signal)
      if (payload.ok === false) {
        flowError.value = payload.error || '盘中资金验证暂不可用'
        return
      }
      liveFlow.value = payload
      flowError.value = ''
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      flowError.value = errorMessage(caught)
    }
  }

  async function refresh() {
    if (refreshing.value || generating.value) return
    refreshing.value = true
    actionNotice.value = ''
    await Promise.allSettled([loadReport(), refreshFlow()])
    loading.value = false
    refreshing.value = false
  }

  async function generate() {
    if (generating.value || pushing.value) return
    actionController?.abort()
    actionController = new AbortController()
    generating.value = true
    generationStage.value = '检查模型连接…'
    generationPreview.value = ''
    actionNotice.value = ''
    try {
      const status = await getJson<PremarketStatusResponse>('/status', actionController.signal)
      const model = status.model_connection || {}
      const capability = status.hermes?.agent_capability || {}
      if (!model.configured) throw new Error('AI 增强未配置，请先到系统设置保存可用连接')
      if (model.connected === false || capability.ready === false) {
        throw new Error(capability.reason || '当前模型连接或 Agent 权限预检未通过')
      }
      await postEventStream('/reports/morning/generate', {}, {
        signal: actionController.signal,
        onStage: (stage) => { generationStage.value = stage },
        onDelta: (delta) => {
          generationStage.value = '报告生成中…'
          generationPreview.value += delta
        },
        onDone: (payload) => {
          reportDate.value = typeof payload.date === 'string' ? payload.date : reportDate.value
          actionNotice.value = '盘前报告已生成并保存'
        },
      })
      await loadReport()
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') actionNotice.value = '已停止生成；未完整内容不会保存'
      else error.value = errorMessage(caught)
    } finally {
      generating.value = false
      generationStage.value = ''
      generationPreview.value = ''
      actionController = null
    }
  }

  function stopGeneration() {
    actionController?.abort()
  }

  async function pushReport() {
    if (!hasReport.value || pushing.value || generating.value) return
    actionController?.abort()
    actionController = new AbortController()
    pushing.value = true
    actionNotice.value = ''
    try {
      const payload = await postJson<PremarketActionResponse>(
        '/reports/morning/push',
        { date: report.value?.date || reportDate.value },
        actionController.signal,
      )
      if (payload.ok === false) throw new Error(payload.error || '推送失败')
      actionNotice.value = payload.msg ? `已推送：${payload.msg}` : '盘前报告已推送'
      if (report.value) report.value = { ...report.value, pushed: true }
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === 'AbortError')) error.value = errorMessage(caught)
    } finally {
      pushing.value = false
      actionController = null
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void refreshFlow()
  }

  onMounted(() => {
    void refresh()
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void refreshFlow()
    }, 30_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    loadController?.abort()
    flowController?.abort()
    actionController?.abort()
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    report,
    liveFlow,
    loading,
    refreshing,
    generating,
    pushing,
    error,
    flowError,
    generationStage,
    generationPreview,
    actionNotice,
    hasReport,
    refresh,
    generate,
    stopGeneration,
    pushReport,
  }
}
