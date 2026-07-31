import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, postEventStream, postJson } from '../services/api'
import type { PremarketStatusResponse } from '../types/premarket'
import type {
  RecommendationListResponse,
  RecommendationStatsResponse,
  TailActionResponse,
  TailReportResponse,
  WatchlistResponse,
} from '../types/tail'

function todayInChina(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date())
}

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : '数据读取失败'
}

export function useTailDecision() {
  const report = ref<TailReportResponse | null>(null)
  const stats = ref<RecommendationStatsResponse | null>(null)
  const recommendations = ref<RecommendationListResponse | null>(null)
  const watchlist = ref<WatchlistResponse | null>(null)
  const loading = ref(true)
  const refreshing = ref(false)
  const generating = ref(false)
  const pushing = ref(false)
  const verifying = ref(false)
  const reportError = ref('')
  const statsError = ref('')
  const recommendationError = ref('')
  const watchlistError = ref('')
  const generationStage = ref('')
  const generationPreview = ref('')
  const actionNotice = ref('')
  const reportDate = ref(todayInChina())
  let reportController: AbortController | null = null
  let statsController: AbortController | null = null
  let recommendationController: AbortController | null = null
  let watchlistController: AbortController | null = null
  let actionController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null

  const hasReport = computed(() => Boolean(report.value?.ok !== false && report.value?.content_md))

  async function loadReport() {
    reportController?.abort()
    reportController = new AbortController()
    try {
      const payload = await getJson<TailReportResponse>(
        `/reports/tail?date=${encodeURIComponent(reportDate.value)}`,
        reportController.signal,
      )
      report.value = payload
      reportError.value = payload.ok === false || !payload.content_md
        ? payload.error || '该日期暂无尾盘决策定稿'
        : ''
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      reportError.value = errorMessage(caught)
    }
  }

  async function loadStats() {
    statsController?.abort()
    statsController = new AbortController()
    try {
      const payload = await getJson<RecommendationStatsResponse>('/recommendations/stats', statsController.signal)
      if (payload.ok === false) throw new Error(payload.error || '推荐统计暂不可用')
      stats.value = payload
      statsError.value = ''
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      statsError.value = errorMessage(caught)
    }
  }

  async function loadRecommendations() {
    recommendationController?.abort()
    recommendationController = new AbortController()
    try {
      const payload = await getJson<RecommendationListResponse>('/recommendations', recommendationController.signal)
      if (payload.ok === false) throw new Error(payload.error || '推荐明细暂不可用')
      recommendations.value = payload
      recommendationError.value = ''
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      recommendationError.value = errorMessage(caught)
    }
  }

  async function loadWatchlist() {
    watchlistController?.abort()
    watchlistController = new AbortController()
    try {
      const payload = await getJson<WatchlistResponse>('/watchlist', watchlistController.signal)
      if (payload.ok === false) throw new Error(payload.error || '观察池暂不可用')
      watchlist.value = payload
      watchlistError.value = ''
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      watchlistError.value = errorMessage(caught)
    }
  }

  async function refreshEvidence() {
    await Promise.allSettled([loadStats(), loadRecommendations(), loadWatchlist()])
  }

  async function refresh() {
    if (refreshing.value || generating.value) return
    refreshing.value = true
    actionNotice.value = ''
    await Promise.allSettled([loadReport(), refreshEvidence()])
    loading.value = false
    refreshing.value = false
  }

  async function generate() {
    if (generating.value || pushing.value || verifying.value) return
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
      await postEventStream('/reports/tail/generate', {}, {
        signal: actionController.signal,
        onStage: (stage) => { generationStage.value = stage },
        onDelta: (delta) => {
          generationStage.value = '尾盘定稿生成中…'
          generationPreview.value += delta
        },
        onDone: (payload) => {
          reportDate.value = typeof payload.date === 'string' ? payload.date : reportDate.value
          actionNotice.value = '尾盘决策已定稿并保存'
        },
      })
      await loadReport()
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') actionNotice.value = '已停止查看；未完整内容不会冒充正式定稿'
      else reportError.value = errorMessage(caught)
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
    if (!hasReport.value || pushing.value || generating.value || verifying.value) return
    actionController?.abort()
    actionController = new AbortController()
    pushing.value = true
    actionNotice.value = ''
    try {
      const payload = await postJson<TailActionResponse>(
        '/reports/tail/push',
        {
          date: report.value?.date || reportDate.value,
          ...(report.value?.pushed ? { force_resend: true, resend_id: crypto.randomUUID() } : {}),
        },
        actionController.signal,
      )
      if (payload.ok === false) throw new Error(payload.error || '推送失败')
      actionNotice.value = payload.msg ? `已提交推送：${payload.msg}` : '尾盘决策已提交推送'
      if (report.value) report.value = { ...report.value, pushed: true }
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === 'AbortError')) reportError.value = errorMessage(caught)
    } finally {
      pushing.value = false
      actionController = null
    }
  }

  async function verifyRecommendations() {
    if (verifying.value || generating.value || pushing.value) return
    actionController?.abort()
    actionController = new AbortController()
    verifying.value = true
    actionNotice.value = ''
    try {
      const payload = await postJson<TailActionResponse>('/recommendations/verify', {}, actionController.signal)
      if (payload.ok === false) throw new Error(payload.error || '推荐回验失败')
      actionNotice.value = `回验完成：核验 ${payload.verified ?? 0} 条，结算 ${payload.closed ?? 0} 条`
      await Promise.allSettled([loadStats(), loadRecommendations()])
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === 'AbortError')) recommendationError.value = errorMessage(caught)
    } finally {
      verifying.value = false
      actionController = null
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void refreshEvidence()
  }

  onMounted(() => {
    void refresh()
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void refreshEvidence()
    }, 30_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    reportController?.abort()
    statsController?.abort()
    recommendationController?.abort()
    watchlistController?.abort()
    actionController?.abort()
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    report, stats, recommendations, watchlist,
    loading, refreshing, generating, pushing, verifying,
    reportError, statsError, recommendationError, watchlistError,
    generationStage, generationPreview, actionNotice, hasReport,
    refresh, generate, stopGeneration, pushReport, verifyRecommendations,
  }
}
