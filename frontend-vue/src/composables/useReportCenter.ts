import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, postEventStream, postJson } from '../services/api'
import type {
  ArchiveDatesResponse,
  DeliveryReceipt,
  DeliveryReceiptsResponse,
  ReportActionResponse,
  ReportRecord,
  ReportType,
  ReportTypeOption,
} from '../types/reports'

export const REPORT_TYPES: ReportTypeOption[] = [
  { id: 'morning', label: '早盘内参', scheduledAt: '08:50', pushable: true },
  { id: 'midday', label: '尾盘草案', scheduledAt: '14:45', pushable: false },
  { id: 'tail', label: '尾盘定稿', scheduledAt: '14:52', pushable: true },
  { id: 'close', label: '收盘复盘', scheduledAt: '15:30', pushable: true },
  { id: 'evening', label: '次日策略', scheduledAt: '19:30', pushable: true },
  { id: 'trend', label: '趋势票', scheduledAt: '19:30', pushable: true },
]

function todayInChina(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date())
}

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : '数据读取失败'
}

export function useReportCenter() {
  const selectedType = ref<ReportType>('morning')
  const selectedDate = ref(todayInChina())
  const report = ref<ReportRecord | null>(null)
  const historyDates = ref<string[]>([])
  const receipts = ref<DeliveryReceipt[]>([])
  const loading = ref(true)
  const refreshing = ref(false)
  const generating = ref(false)
  const pushing = ref(false)
  const reportError = ref('')
  const historyError = ref('')
  const receiptError = ref('')
  const actionError = ref('')
  const actionNotice = ref('')
  const generationStage = ref('')
  const generationPreview = ref('')
  const generationIncomplete = ref(false)
  let alive = true
  let reportController: AbortController | null = null
  let historyController: AbortController | null = null
  let receiptController: AbortController | null = null
  let generationController: AbortController | null = null
  let pushController: AbortController | null = null
  let generationTimer: ReturnType<typeof setInterval> | null = null
  const generationSeconds = ref(0)
  const generations = { report: 0, history: 0, receipts: 0, generate: 0, push: 0 }

  const selectedOption = computed(() => REPORT_TYPES.find((item) => item.id === selectedType.value) || REPORT_TYPES[0])
  const isToday = computed(() => selectedDate.value === todayInChina())
  const hasReport = computed(() => Boolean(report.value?.ok !== false && report.value?.content_md))
  const visibleDates = computed(() => [...new Set([todayInChina(), selectedDate.value, ...historyDates.value])]
    .filter((item) => /^\d{4}-\d{2}-\d{2}$/.test(item))
    .sort()
    .reverse()
    .slice(0, 30))
  const matchingReceipt = computed(() => {
    if (!hasReport.value) return null
    const ids = new Set([
      report.value?.content_id,
      `report:${selectedType.value}:${selectedDate.value}`,
    ].filter(Boolean))
    return receipts.value.find((item) => ids.has(item.content_id)) || null
  })

  function clearGenerationTimer() {
    if (generationTimer) clearInterval(generationTimer)
    generationTimer = null
  }

  async function loadReport({ clear = false } = {}) {
    reportController?.abort()
    const controller = new AbortController()
    const generation = ++generations.report
    reportController = controller
    if (clear) report.value = null
    try {
      const payload = await getJson<ReportRecord>(
        `/reports/${selectedType.value}?date=${encodeURIComponent(selectedDate.value)}`,
        controller.signal,
      )
      if (!alive || controller !== reportController || generation !== generations.report) return
      report.value = payload
      reportError.value = payload.ok === false || !payload.content_md
        ? payload.error || '该日期暂无已保存报告'
        : ''
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      if (!alive || controller !== reportController || generation !== generations.report) return
      reportError.value = errorMessage(caught)
    }
  }

  async function loadHistory() {
    historyController?.abort()
    const controller = new AbortController()
    const generation = ++generations.history
    historyController = controller
    try {
      const payload = await getJson<ArchiveDatesResponse>('/archive/dates', controller.signal)
      if (!alive || controller !== historyController || generation !== generations.history) return
      if (payload.ok === false) {
        historyError.value = payload.error || '历史日期暂不可用'
        return
      }
      historyDates.value = Array.isArray(payload.dates) ? payload.dates : []
      historyError.value = ''
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      if (!alive || controller !== historyController || generation !== generations.history) return
      historyError.value = errorMessage(caught)
    }
  }

  async function loadReceipts() {
    receiptController?.abort()
    const controller = new AbortController()
    const generation = ++generations.receipts
    receiptController = controller
    try {
      const payload = await getJson<DeliveryReceiptsResponse>('/delivery/receipts?limit=100', controller.signal)
      if (!alive || controller !== receiptController || generation !== generations.receipts) return
      if (payload.ok === false) {
        receiptError.value = payload.error || '交付回执暂不可用'
        return
      }
      receipts.value = Array.isArray(payload.receipts) ? payload.receipts : []
      receiptError.value = ''
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      if (!alive || controller !== receiptController || generation !== generations.receipts) return
      receiptError.value = errorMessage(caught)
    }
  }

  async function refresh({ clear = false } = {}) {
    if (refreshing.value || generating.value || pushing.value) return
    refreshing.value = true
    actionError.value = ''
    actionNotice.value = ''
    await Promise.allSettled([loadReport({ clear }), loadHistory(), loadReceipts()])
    if (alive) {
      loading.value = false
      refreshing.value = false
    }
  }

  async function selectReport(type: ReportType, date = selectedDate.value) {
    if (generating.value || pushing.value || refreshing.value) return
    selectedType.value = type
    selectedDate.value = /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : todayInChina()
    loading.value = true
    await refresh({ clear: true })
  }

  async function selectDate(date: string) {
    await selectReport(selectedType.value, date)
  }

  async function generate() {
    if (generating.value || pushing.value || refreshing.value) return
    if (!isToday.value) {
      await selectDate(todayInChina())
    }
    generationController?.abort()
    const controller = new AbortController()
    const generation = ++generations.generate
    generationController = controller
    generating.value = true
    generationStage.value = '正在准备真实数据…'
    generationPreview.value = ''
    generationIncomplete.value = false
    generationSeconds.value = 0
    actionError.value = ''
    actionNotice.value = ''
    clearGenerationTimer()
    generationTimer = setInterval(() => { generationSeconds.value += 1 }, 1000)
    try {
      await postEventStream(`/reports/${selectedType.value}/generate`, {}, {
        signal: controller.signal,
        onStage: (stage) => {
          if (alive && controller === generationController && generation === generations.generate) generationStage.value = stage
        },
        onDelta: (delta) => {
          if (!alive || controller !== generationController || generation !== generations.generate) return
          generationStage.value = '报告生成中…'
          generationPreview.value += delta
        },
        onDone: (payload) => {
          if (!alive || controller !== generationController || generation !== generations.generate) return
          if (typeof payload.date === 'string') selectedDate.value = payload.date
          generationIncomplete.value = Boolean(payload.truncated)
          actionNotice.value = payload.saved ? '报告已生成并保存，正在读取正式定稿' : '生成已完成，正在核对保存状态'
        },
      })
      await Promise.allSettled([loadReport({ clear: true }), loadHistory(), loadReceipts()])
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') {
        actionNotice.value = '已停止旁观生成；后台托管任务可能继续，稍后可刷新查看定稿'
      } else {
        generationIncomplete.value = Boolean(generationPreview.value)
        actionError.value = errorMessage(caught)
      }
    } finally {
      clearGenerationTimer()
      if (alive && controller === generationController && generation === generations.generate) {
        generating.value = false
        generationStage.value = ''
        generationController = null
      }
    }
  }

  function stopGeneration() {
    generationController?.abort()
  }

  async function pushReport() {
    if (!hasReport.value || !selectedOption.value.pushable || pushing.value || generating.value) return
    pushController?.abort()
    const controller = new AbortController()
    const generation = ++generations.push
    pushController = controller
    pushing.value = true
    actionError.value = ''
    actionNotice.value = '推送请求已提交，等待服务端回执…'
    try {
      const payload = await postJson<ReportActionResponse>(
        `/reports/${selectedType.value}/push`,
        {
          date: selectedDate.value,
          ...(report.value?.pushed ? { force_resend: true, resend_id: crypto.randomUUID() } : {}),
        },
        controller.signal,
      )
      if (!alive || controller !== pushController || generation !== generations.push) return
      if (payload.ok === false) throw new Error(payload.error || '推送失败')
      actionNotice.value = '服务端已完成推送请求，正在读取平台回执…'
      await loadReceipts()
      if (alive && controller === pushController && generation === generations.push && !matchingReceipt.value) {
        actionNotice.value = '推送请求已提交；交付回执暂未返回，不能确认平台接受或送达'
      }
      await loadReport()
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === 'AbortError')) actionError.value = errorMessage(caught)
    } finally {
      if (alive && controller === pushController && generation === generations.push) {
        pushing.value = false
        pushController = null
      }
    }
  }

  onMounted(() => { void refresh({ clear: true }) })

  onBeforeUnmount(() => {
    alive = false
    generations.report += 1
    generations.history += 1
    generations.receipts += 1
    generations.generate += 1
    generations.push += 1
    reportController?.abort()
    historyController?.abort()
    receiptController?.abort()
    generationController?.abort()
    pushController?.abort()
    clearGenerationTimer()
  })

  return {
    selectedType, selectedDate, selectedOption, report, visibleDates, matchingReceipt,
    loading, refreshing, generating, pushing, reportError, historyError, receiptError,
    actionError, actionNotice, generationStage, generationPreview, generationIncomplete,
    generationSeconds, isToday, hasReport, refresh, selectReport, selectDate, generate,
    stopGeneration, pushReport,
  }
}
