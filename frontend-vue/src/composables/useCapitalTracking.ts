import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, postJson } from '../services/api'
import type {
  CapitalActionFeedback,
  CapitalSnapshotResponse,
  CapitalTrendsResponse,
} from '../types/capitalTracking'

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : fallback
}

export function useCapitalTracking() {
  const data = ref<CapitalTrendsResponse | null>(null)
  const loading = ref(true)
  const refreshing = ref(false)
  const snapshotting = ref(false)
  const error = ref('')
  const actionFeedback = ref<CapitalActionFeedback | null>(null)
  let trendsController: AbortController | null = null
  let snapshotController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  let alive = false

  const sectors = computed(() => Array.isArray(data.value?.sectors)
    ? data.value.sectors.filter((row) => row && typeof row === 'object')
    : [])
  const hotStocks = computed(() => Array.isArray(data.value?.hot_stocks)
    ? data.value.hot_stocks.filter((row) => row && typeof row === 'object')
    : [])

  async function refresh() {
    if (refreshing.value) return
    trendsController?.abort()
    const controller = new AbortController()
    trendsController = controller
    refreshing.value = true
    try {
      const payload = await getJson<CapitalTrendsResponse>('/capital/trends?days=1', controller.signal)
      if (!alive || controller !== trendsController) return
      data.value = payload
      error.value = ''
    } catch (caught) {
      if (!alive || controller !== trendsController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      error.value = errorMessage(caught, '资金追踪数据读取失败')
    } finally {
      if (controller === trendsController) trendsController = null
      if (alive) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  async function takeSnapshot() {
    if (snapshotting.value) return
    snapshotController?.abort()
    const controller = new AbortController()
    snapshotController = controller
    snapshotting.value = true
    actionFeedback.value = null
    try {
      const payload = await postJson<CapitalSnapshotResponse>('/capital/snapshot', {}, controller.signal)
      if (!alive || controller !== snapshotController) return
      if (payload.ok === false) {
        actionFeedback.value = {
          tone: 'danger',
          message: `采集未完成：${payload.msg || payload.missing?.join('；') || '数据源暂不可用'}`,
        }
        return
      }
      const sectorCount = payload.sectors == null ? '行业数量缺失' : `行业 ${payload.sectors}`
      const stockCount = payload.hot_stocks == null ? '热门股数量缺失' : `热门股 ${payload.hot_stocks}`
      actionFeedback.value = {
        tone: payload.complete === false ? 'warning' : 'success',
        message: `资金快照已写入：${sectorCount}，${stockCount}${payload.complete === false ? '；部分口径缺失' : ''}`,
      }
      await refresh()
    } catch (caught) {
      if (!alive || controller !== snapshotController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      actionFeedback.value = { tone: 'danger', message: errorMessage(caught, '资金快照采集失败') }
    } finally {
      if (controller === snapshotController) snapshotController = null
      if (alive) snapshotting.value = false
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void refresh()
  }

  onMounted(() => {
    alive = true
    void refresh()
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, 60_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    alive = false
    trendsController?.abort()
    snapshotController?.abort()
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    data,
    sectors,
    hotStocks,
    loading,
    refreshing,
    snapshotting,
    error,
    actionFeedback,
    refresh,
    takeSnapshot,
  }
}
