import { onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson } from '../services/api'
import type { TimingReading, TimingResponse } from '../types/timing'

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : fallback
}

export function useTiming(shouldAutoRefresh: () => boolean) {
  const data = ref<TimingResponse | null>(null)
  const loading = ref(true)
  const refreshing = ref(false)
  const error = ref('')
  const stock = ref<TimingReading | null>(null)
  const stockLoading = ref(false)
  const stockError = ref('')
  const stockRequested = ref(false)
  const stockQueryCode = ref('')
  let marketController: AbortController | null = null
  let stockController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  let alive = false
  let marketGeneration = 0
  let stockGeneration = 0

  async function refresh() {
    if (marketController) return
    const controller = new AbortController()
    const generation = ++marketGeneration
    marketController = controller
    if (data.value) refreshing.value = true
    else loading.value = true
    try {
      const payload = await getJson<TimingResponse>('/timing', controller.signal)
      if (!alive || controller !== marketController || generation !== marketGeneration) return
      if (payload.ok === false) throw new Error(payload.error || payload.missing?.[0] || '资金择时接口暂不可用')
      data.value = payload
      error.value = ''
    } catch (caught) {
      if (!alive || controller !== marketController || generation !== marketGeneration) return
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      error.value = errorMessage(caught, '资金择时数据读取失败')
    } finally {
      if (controller === marketController) marketController = null
      if (alive && generation === marketGeneration) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  async function queryStock(code: string) {
    stockController?.abort()
    const controller = new AbortController()
    const generation = ++stockGeneration
    stockController = controller
    stockRequested.value = true
    stockQueryCode.value = code
    stock.value = null
    stockError.value = ''
    stockLoading.value = true
    try {
      const payload = await getJson<TimingResponse>(`/timing?code=${encodeURIComponent(code)}`, controller.signal)
      if (!alive || controller !== stockController || generation !== stockGeneration) return
      if (payload.ok === false) throw new Error(payload.error || payload.missing?.[0] || '个股择时接口暂不可用')
      stock.value = payload.stock ?? null
      if (!payload.stock) stockError.value = '接口成功返回，但未包含该个股的择时对象'
    } catch (caught) {
      if (!alive || controller !== stockController || generation !== stockGeneration) return
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      stockError.value = errorMessage(caught, '个股择时数据读取失败')
    } finally {
      if (controller === stockController) stockController = null
      if (alive && generation === stockGeneration) stockLoading.value = false
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible' && shouldAutoRefresh()) void refresh()
  }

  onMounted(() => {
    alive = true
    void refresh()
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible' && shouldAutoRefresh()) void refresh()
    }, 60_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    alive = false
    marketGeneration += 1
    stockGeneration += 1
    marketController?.abort()
    stockController?.abort()
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    data,
    loading,
    refreshing,
    error,
    stock,
    stockLoading,
    stockError,
    stockRequested,
    stockQueryCode,
    refresh,
    queryStock,
  }
}
