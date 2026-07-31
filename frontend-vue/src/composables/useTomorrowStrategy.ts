import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, postEventStream, postJson } from '../services/api'
import type {
  TomorrowActionResponse,
  TomorrowReportResponse,
  TomorrowStatusResponse,
  TomorrowWatchlistResponse,
} from '../types/tomorrow'

function todayInChina(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date())
}

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : '数据读取失败'
}

export function useTomorrowStrategy() {
  const report = ref<TomorrowReportResponse | null>(null)
  const watchlist = ref<TomorrowWatchlistResponse | null>(null)
  const loading = ref(true)
  const refreshing = ref(false)
  const generating = ref(false)
  const pushing = ref(false)
  const reportError = ref('')
  const watchlistError = ref('')
  const actionError = ref('')
  const actionNotice = ref('')
  const generationStage = ref('')
  const generationPreview = ref('')
  const reportDate = ref(todayInChina())
  let reportController: AbortController | null = null
  let watchlistController: AbortController | null = null
  let generateController: AbortController | null = null
  let pushController: AbortController | null = null
  let watchlistTimer: ReturnType<typeof setInterval> | null = null
  let alive = false

  const hasReport = computed(() => Boolean(report.value?.ok !== false && report.value?.content_md))

  async function loadReport() {
    reportController?.abort()
    const controller = new AbortController()
    reportController = controller
    try {
      const payload = await getJson<TomorrowReportResponse>(
        `/reports/evening?date=${encodeURIComponent(reportDate.value)}`,
        controller.signal,
      )
      if (!alive || controller !== reportController) return
      report.value = payload
      reportError.value = payload.ok === false || !payload.content_md
        ? payload.error || '该日期暂无次日策略定稿'
        : ''
    } catch (caught) {
      if (!alive || controller !== reportController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      reportError.value = errorMessage(caught)
    }
  }

  async function loadWatchlist() {
    watchlistController?.abort()
    const controller = new AbortController()
    watchlistController = controller
    try {
      const payload = await getJson<TomorrowWatchlistResponse>('/watchlist', controller.signal)
      if (!alive || controller !== watchlistController) return
      if (payload.ok === false) {
        watchlistError.value = payload.error || '明日观察池暂不可用'
        return
      }
      watchlist.value = payload
      watchlistError.value = ''
    } catch (caught) {
      if (!alive || controller !== watchlistController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      watchlistError.value = errorMessage(caught)
    }
  }

  async function refresh() {
    if (refreshing.value || generating.value) return
    refreshing.value = true
    actionError.value = ''
    await Promise.allSettled([loadReport(), loadWatchlist()])
    if (!alive) return
    loading.value = false
    refreshing.value = false
  }

  async function generate() {
    if (generating.value || pushing.value) return
    generateController?.abort()
    const controller = new AbortController()
    generateController = controller
    generating.value = true
    generationStage.value = '检查模型连接…'
    generationPreview.value = ''
    actionError.value = ''
    actionNotice.value = ''
    try {
      const status = await getJson<TomorrowStatusResponse>('/status', controller.signal)
      if (!alive || controller !== generateController) return
      const model = status.model_connection || {}
      const capability = status.hermes?.agent_capability || {}
      if (!model.configured) throw new Error('AI 增强未配置，请先到系统设置保存可用连接')
      if (model.connected === false || capability.ready === false) {
        throw new Error(capability.reason || '当前模型连接或 Agent 权限预检未通过')
      }
      await postEventStream('/reports/evening/generate', {}, {
        signal: controller.signal,
        onStage: (stage) => { if (alive && controller === generateController) generationStage.value = stage },
        onDelta: (delta) => {
          if (!alive || controller !== generateController) return
          generationStage.value = '次日策略生成中…'
          generationPreview.value += delta
        },
        onDone: (payload) => {
          if (!alive || controller !== generateController) return
          reportDate.value = typeof payload.date === 'string' ? payload.date : reportDate.value
          actionNotice.value = '次日策略已生成并保存为正式定稿'
        },
      })
      if (alive && controller === generateController) await loadReport()
    } catch (caught) {
      if (!alive || controller !== generateController) return
      if (caught instanceof DOMException && caught.name === 'AbortError') {
        actionNotice.value = '已停止查看生成流；未完成内容不会冒充正式定稿'
      } else actionError.value = errorMessage(caught)
    } finally {
      if (alive && controller === generateController) {
        generating.value = false
        generationStage.value = ''
        generationPreview.value = ''
        generateController = null
      }
    }
  }

  function stopGeneration() {
    generateController?.abort()
  }

  async function pushReport() {
    if (!hasReport.value || pushing.value || generating.value) return
    pushController?.abort()
    const controller = new AbortController()
    pushController = controller
    pushing.value = true
    actionError.value = ''
    actionNotice.value = ''
    try {
      const payload = await postJson<TomorrowActionResponse>(
        '/reports/evening/push',
        {
          date: report.value?.date || reportDate.value,
          ...(report.value?.pushed ? { force_resend: true, resend_id: crypto.randomUUID() } : {}),
        },
        controller.signal,
      )
      if (!alive || controller !== pushController) return
      if (payload.ok === false) throw new Error(payload.error || '推送提交失败')
      actionNotice.value = payload.msg
        ? `已提交推送：${payload.msg}；平台送达状态仍需通过交付回执核验`
        : '次日策略已提交推送；当前未确认平台送达'
      if (report.value) report.value = { ...report.value, pushed: true }
    } catch (caught) {
      if (!alive || controller !== pushController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      actionError.value = errorMessage(caught)
    } finally {
      if (alive && controller === pushController) {
        pushing.value = false
        pushController = null
      }
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void Promise.allSettled([loadReport(), loadWatchlist()])
  }

  onMounted(() => {
    alive = true
    void refresh()
    watchlistTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void loadWatchlist()
    }, 30_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    alive = false
    reportController?.abort()
    watchlistController?.abort()
    generateController?.abort()
    pushController?.abort()
    if (watchlistTimer) clearInterval(watchlistTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    report, watchlist, loading, refreshing, generating, pushing, reportError, watchlistError,
    actionError, actionNotice, generationStage, generationPreview, hasReport, refresh,
    loadWatchlist, generate, stopGeneration, pushReport,
  }
}
