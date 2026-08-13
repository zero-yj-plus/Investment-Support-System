import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import {
  getLimitUpTrackerBackfill,
  getLimitUpTrackerDates,
  getLimitUpTrackerSnapshot,
  startLimitUpTrackerBackfill,
} from '../services/api'
import type {
  LimitUpBackfillResponse,
  LimitUpTrackerDatesResponse,
  LimitUpTrackerSnapshot,
} from '../types/limitupTracker'

function message(error: unknown, fallback: string): string {
  if (error instanceof DOMException && error.name === 'AbortError') return ''
  return error instanceof Error ? error.message : fallback
}

export function useLimitUpTracker() {
  const dateData = shallowRef<LimitUpTrackerDatesResponse | null>(null)
  const snapshot = shallowRef<LimitUpTrackerSnapshot | null>(null)
  const selectedDate = ref('')
  const pendingDate = ref('')
  const loading = ref(true)
  const refreshing = ref(false)
  const switching = ref(false)
  const error = ref('')
  const backfill = shallowRef<LimitUpBackfillResponse | null>(null)
  const backfilling = computed(() => backfill.value?.status === 'running')
  const busy = computed(() => loading.value || refreshing.value || switching.value || backfilling.value)
  let alive = false
  let requestVersion = 0
  let datesController: AbortController | null = null
  let snapshotController: AbortController | null = null
  let backfillController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  let pollTimer: ReturnType<typeof setTimeout> | null = null

  async function loadDates(): Promise<LimitUpTrackerDatesResponse | null> {
    datesController?.abort()
    const controller = new AbortController()
    datesController = controller
    try {
      const payload = await getLimitUpTrackerDates(controller.signal, 20)
      if (!alive || controller !== datesController) return null
      dateData.value = payload
      return payload
    } catch (caught) {
      if (alive && controller === datesController) error.value = message(caught, '交易日列表读取失败')
      return null
    } finally {
      if (controller === datesController) datesController = null
    }
  }

  async function selectDate(date: string, force = false): Promise<void> {
    if (!date || (!force && date === selectedDate.value && snapshot.value)) return
    const version = ++requestVersion
    snapshotController?.abort()
    const controller = new AbortController()
    snapshotController = controller
    pendingDate.value = date
    switching.value = Boolean(snapshot.value)
    if (!snapshot.value) loading.value = true
    try {
      const payload = await getLimitUpTrackerSnapshot(date, controller.signal)
      if (!alive || version !== requestVersion || controller !== snapshotController) return
      if (payload.ok === false) throw new Error(payload.error || '涨停追踪快照暂不可用')
      // 日期、统计和长表只在同一响应完整返回后一次性提交，杜绝新标题配旧正文。
      snapshot.value = payload
      selectedDate.value = payload.selected_date
      error.value = ''
    } catch (caught) {
      if (!alive || version !== requestVersion || controller !== snapshotController) return
      const detail = message(caught, '涨停追踪快照读取失败')
      if (detail) error.value = detail
    } finally {
      if (version === requestVersion) {
        pendingDate.value = ''
        switching.value = false
        loading.value = false
      }
      if (controller === snapshotController) snapshotController = null
    }
  }

  async function refresh(): Promise<void> {
    if (refreshing.value || switching.value) return
    refreshing.value = true
    try {
      const dates = await loadDates()
      const target = selectedDate.value || dates?.default_date || ''
      if (target) await selectDate(target, true)
    } finally {
      if (alive) refreshing.value = false
    }
  }

  function finishBackfill(payload: LimitUpBackfillResponse) {
    backfill.value = payload
    const labels: Record<string, string> = {
      complete: '补数据完成，已重新读取日期与当前快照',
      partial: '补数据部分完成；未取得的数据仍保持缺失',
      failed: '补数据失败',
      noop: '没有已到期且可补的数据',
    }
    backfill.value = { ...payload, message: payload.reason || labels[payload.status] || payload.message }
    void refresh()
  }

  async function pollBackfill(jobId: string): Promise<void> {
    backfillController?.abort()
    const controller = new AbortController()
    backfillController = controller
    try {
      const payload = await getLimitUpTrackerBackfill(jobId, controller.signal)
      if (!alive || controller !== backfillController) return
      backfill.value = payload
      if (payload.status === 'running') {
        pollTimer = setTimeout(() => { void pollBackfill(jobId) }, 1_500)
      } else {
        finishBackfill(payload)
      }
    } catch (caught) {
      if (!alive || controller !== backfillController) return
      backfill.value = {
        ok: false,
        job_id: jobId,
        status: 'failed',
        reason: message(caught, '补数据状态读取失败'),
      }
    } finally {
      if (controller === backfillController) backfillController = null
    }
  }

  async function startBackfill(): Promise<void> {
    if (backfilling.value) return
    if (pollTimer) clearTimeout(pollTimer)
    backfillController?.abort()
    const controller = new AbortController()
    backfillController = controller
    backfill.value = { ok: true, job_id: '', status: 'running', message: '正在启动补数据任务' }
    try {
      const payload = await startLimitUpTrackerBackfill(controller.signal)
      if (!alive || controller !== backfillController) return
      backfill.value = payload
      if (payload.status === 'running') void pollBackfill(payload.job_id)
      else finishBackfill(payload)
    } catch (caught) {
      if (!alive || controller !== backfillController) return
      backfill.value = {
        ok: false,
        job_id: '',
        status: 'failed',
        reason: message(caught, '补数据任务启动失败'),
      }
    } finally {
      if (controller === backfillController) backfillController = null
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible' && selectedDate.value) void selectDate(selectedDate.value, true)
  }

  onMounted(async () => {
    alive = true
    const dates = await loadDates()
    const target = dates?.default_date || dates?.dates?.[0]?.date || ''
    if (target) await selectDate(target)
    else loading.value = false
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible' && selectedDate.value) void selectDate(selectedDate.value, true)
    }, 60_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    alive = false
    requestVersion += 1
    datesController?.abort()
    snapshotController?.abort()
    backfillController?.abort()
    if (refreshTimer) clearInterval(refreshTimer)
    if (pollTimer) clearTimeout(pollTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    dateData,
    snapshot,
    selectedDate,
    pendingDate,
    loading,
    refreshing,
    switching,
    error,
    backfill,
    backfilling,
    busy,
    selectDate,
    startBackfill,
    refresh,
  }
}
