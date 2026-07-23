import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, postEventStream, postJson } from '../services/api'
import type { PremarketStatusResponse } from '../types/premarket'
import type {
  CloseRecommendationListResponse,
  CloseReviewActionResponse,
  CloseReviewReportResponse,
  CloseReviewStatsResponse,
  HoldingsAdviceReviewResponse,
} from '../types/closeReview'

function todayInChina(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date())
}

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : '数据读取失败'
}

export function useCloseReview() {
  const report = ref<CloseReviewReportResponse | null>(null)
  const adviceReview = ref<HoldingsAdviceReviewResponse | null>(null)
  const stats = ref<CloseReviewStatsResponse | null>(null)
  const recommendations = ref<CloseRecommendationListResponse | null>(null)
  const loading = ref(true)
  const refreshing = ref(false)
  const generating = ref(false)
  const pushing = ref(false)
  const verifying = ref(false)
  const reportError = ref('')
  const adviceError = ref('')
  const statsError = ref('')
  const recommendationError = ref('')
  const actionError = ref('')
  const actionNotice = ref('')
  const generationStage = ref('')
  const generationPreview = ref('')
  const reportDate = ref(todayInChina())
  let reportController: AbortController | null = null
  let adviceController: AbortController | null = null
  let statsController: AbortController | null = null
  let recommendationController: AbortController | null = null
  let generateController: AbortController | null = null
  let pushController: AbortController | null = null
  let verifyController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  let alive = false

  const hasReport = computed(() => Boolean(report.value?.ok !== false && report.value?.content_md))

  async function loadReport() {
    reportController?.abort()
    const controller = new AbortController()
    reportController = controller
    try {
      const payload = await getJson<CloseReviewReportResponse>(
        `/reports/close?date=${encodeURIComponent(reportDate.value)}`,
        controller.signal,
      )
      if (!alive || controller !== reportController) return
      report.value = payload
      reportError.value = payload.ok === false || !payload.content_md
        ? payload.error || '该日期暂无今日复盘定稿'
        : ''
    } catch (caught) {
      if (!alive || controller !== reportController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      reportError.value = errorMessage(caught)
    }
  }

  async function loadAdviceReview() {
    adviceController?.abort()
    const controller = new AbortController()
    adviceController = controller
    try {
      const payload = await getJson<HoldingsAdviceReviewResponse>('/holdings/advice-review', controller.signal)
      if (!alive || controller !== adviceController) return
      if (payload.ok === false) throw new Error(payload.error || '策略自证暂不可用')
      adviceReview.value = payload
      adviceError.value = ''
    } catch (caught) {
      if (!alive || controller !== adviceController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      adviceError.value = errorMessage(caught)
    }
  }

  async function loadStats() {
    statsController?.abort()
    const controller = new AbortController()
    statsController = controller
    try {
      const payload = await getJson<CloseReviewStatsResponse>('/recommendations/stats', controller.signal)
      if (!alive || controller !== statsController) return
      if (payload.ok === false) throw new Error(payload.error || '推荐统计暂不可用')
      stats.value = payload
      statsError.value = ''
    } catch (caught) {
      if (!alive || controller !== statsController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      statsError.value = errorMessage(caught)
    }
  }

  async function loadRecommendations() {
    recommendationController?.abort()
    const controller = new AbortController()
    recommendationController = controller
    try {
      const payload = await getJson<CloseRecommendationListResponse>('/recommendations', controller.signal)
      if (!alive || controller !== recommendationController) return
      if (payload.ok === false) throw new Error(payload.error || '推荐明细暂不可用')
      recommendations.value = payload
      recommendationError.value = ''
    } catch (caught) {
      if (!alive || controller !== recommendationController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      recommendationError.value = errorMessage(caught)
    }
  }

  async function refreshEvidence() {
    await Promise.allSettled([loadAdviceReview(), loadStats(), loadRecommendations()])
  }

  async function refresh() {
    if (refreshing.value || generating.value) return
    refreshing.value = true
    actionNotice.value = ''
    actionError.value = ''
    await Promise.allSettled([loadReport(), refreshEvidence()])
    if (!alive) return
    loading.value = false
    refreshing.value = false
  }

  async function generate() {
    if (generating.value || pushing.value || verifying.value) return
    generateController?.abort()
    const controller = new AbortController()
    generateController = controller
    generating.value = true
    generationStage.value = '检查模型连接…'
    generationPreview.value = ''
    actionNotice.value = ''
    actionError.value = ''
    try {
      const status = await getJson<PremarketStatusResponse>('/status', controller.signal)
      if (!alive || controller !== generateController) return
      const model = status.model_connection || {}
      const capability = status.hermes?.agent_capability || {}
      if (!model.configured) throw new Error('AI 增强未配置，请先到系统设置保存可用连接')
      if (model.connected === false || capability.ready === false) {
        throw new Error(capability.reason || '当前模型连接或 Agent 权限预检未通过')
      }
      await postEventStream('/reports/close/generate', {}, {
        signal: controller.signal,
        onStage: (stage) => { if (alive && controller === generateController) generationStage.value = stage },
        onDelta: (delta) => {
          if (!alive || controller !== generateController) return
          generationStage.value = '今日复盘生成中…'
          generationPreview.value += delta
        },
        onDone: (payload) => {
          if (!alive || controller !== generateController) return
          reportDate.value = typeof payload.date === 'string' ? payload.date : reportDate.value
          actionNotice.value = '今日复盘已定稿并保存'
        },
      })
      if (alive && controller === generateController) await loadReport()
    } catch (caught) {
      if (!alive || controller !== generateController) return
      if (caught instanceof DOMException && caught.name === 'AbortError') actionNotice.value = '已停止查看；未完整内容不会冒充正式复盘'
      else actionError.value = errorMessage(caught)
    } finally {
      if (controller === generateController) generateController = null
      if (alive) {
        generating.value = false
        generationStage.value = ''
        generationPreview.value = ''
      }
    }
  }

  function stopGeneration() {
    generateController?.abort()
  }

  async function pushReport() {
    if (!hasReport.value || pushing.value || generating.value || verifying.value) return
    pushController?.abort()
    const controller = new AbortController()
    pushController = controller
    pushing.value = true
    actionNotice.value = ''
    actionError.value = ''
    try {
      const payload = await postJson<CloseReviewActionResponse>(
        '/reports/close/push',
        { date: report.value?.date || reportDate.value },
        controller.signal,
      )
      if (!alive || controller !== pushController) return
      if (payload.ok === false) throw new Error(payload.error || '推送提交失败')
      actionNotice.value = payload.msg ? `已提交推送：${payload.msg}` : '今日复盘已提交推送'
      if (report.value) report.value = { ...report.value, pushed: true }
    } catch (caught) {
      if (!alive || controller !== pushController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      actionError.value = errorMessage(caught)
    } finally {
      if (controller === pushController) pushController = null
      if (alive) pushing.value = false
    }
  }

  async function verifyRecommendations() {
    if (verifying.value || generating.value || pushing.value) return
    verifyController?.abort()
    const controller = new AbortController()
    verifyController = controller
    verifying.value = true
    actionNotice.value = ''
    actionError.value = ''
    try {
      const payload = await postJson<CloseReviewActionResponse>('/recommendations/verify', {}, controller.signal)
      if (!alive || controller !== verifyController) return
      if (payload.ok === false) throw new Error(payload.error || '推荐回验失败')
      const facts = [
        payload.verified == null ? '' : `核验 ${payload.verified} 条`,
        payload.closed == null ? '' : `结算 ${payload.closed} 条`,
      ].filter(Boolean)
      actionNotice.value = facts.length ? `回验完成：${facts.join('，')}` : '回验请求已完成'
      await Promise.allSettled([loadStats(), loadRecommendations()])
    } catch (caught) {
      if (!alive || controller !== verifyController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      actionError.value = errorMessage(caught)
    } finally {
      if (controller === verifyController) verifyController = null
      if (alive) verifying.value = false
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void refreshEvidence()
  }

  onMounted(() => {
    alive = true
    void refresh()
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void refreshEvidence()
    }, 30_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    alive = false
    reportController?.abort()
    adviceController?.abort()
    statsController?.abort()
    recommendationController?.abort()
    generateController?.abort()
    pushController?.abort()
    verifyController?.abort()
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    report, adviceReview, stats, recommendations,
    loading, refreshing, generating, pushing, verifying,
    reportError, adviceError, statsError, recommendationError, actionError,
    actionNotice, generationStage, generationPreview, hasReport,
    refresh, generate, stopGeneration, pushReport, verifyRecommendations,
  }
}
