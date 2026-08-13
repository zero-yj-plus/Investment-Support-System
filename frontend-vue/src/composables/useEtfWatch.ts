import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ApiError, deleteJson, getJson, postJson } from '../services/api'
import type { EtfWatchItem, EtfWatchMutationResponse, EtfWatchResponse } from '../types/etfWatch'

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : fallback
}

export function useEtfWatch() {
  const items = ref<EtfWatchItem[]>([])
  const loading = ref(true)
  const refreshing = ref(false)
  const mutating = ref(false)
  const loaded = ref(false)
  const timestamp = ref('')
  const loadError = ref('')
  const mutationError = ref('')
  const feedback = ref('')
  let listController: AbortController | null = null
  let mutationController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  let profilePollTimers: ReturnType<typeof setTimeout>[] = []
  let alive = false

  const degradedCount = computed(() => items.value.filter((item) => item.state !== 'complete' || item.quote?.stale).length)

  function scheduleProfilePolls() {
    profilePollTimers.forEach((timer) => clearTimeout(timer))
    profilePollTimers = [5_000, 15_000, 35_000].map((delay) => setTimeout(() => {
      if (alive && document.visibilityState === 'visible') void loadItems()
    }, delay))
  }

  async function loadItems() {
    if (refreshing.value) return
    listController?.abort()
    const controller = new AbortController()
    listController = controller
    refreshing.value = true
    if (!loaded.value) loading.value = true
    try {
      const payload = await getJson<EtfWatchResponse>('/etf-watch', controller.signal)
      if (!alive || controller !== listController) return
      if (payload.ok === false) throw new ApiError(payload.error || 'ETF观察返回不可用状态')
      if (!Array.isArray(payload.items)) throw new ApiError('ETF观察未返回可识别的items数组')
      items.value = payload.items
      timestamp.value = String(payload.ts || '')
      loadError.value = ''
      loaded.value = true
    } catch (caught) {
      if (!alive || controller !== listController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      loadError.value = errorMessage(caught, 'ETF观察读取失败')
    } finally {
      if (controller === listController) listController = null
      if (alive) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  async function addCodes(codes: string[]): Promise<boolean> {
    if (mutating.value || !codes.length) return false
    mutationController?.abort()
    const controller = new AbortController()
    mutationController = controller
    mutating.value = true
    mutationError.value = ''
    feedback.value = ''
    try {
      const result = await postJson<EtfWatchMutationResponse>('/etf-watch/import', { codes }, controller.signal)
      if (!alive || controller !== mutationController) return false
      if (result.ok === false) throw new ApiError(result.error || '服务端拒绝添加ETF')
      const added = result.added_codes?.length || 0
      const duplicates = result.duplicate_codes?.length || 0
      const rejected = result.rejected_codes?.length || 0
      feedback.value = `ETF添加完成：新增 ${added} 只`
      if (duplicates || rejected) feedback.value += `，重复 ${duplicates} 只、行情未核验 ${rejected} 只`
      if (result.profile_status === 'queued') feedback.value += '；低频资料已排队刷新。'
      if (result.profile_status === 'queued') scheduleProfilePolls()
      await loadItems()
      return true
    } catch (caught) {
      if (!alive || controller !== mutationController || (caught instanceof DOMException && caught.name === 'AbortError')) return false
      mutationError.value = errorMessage(caught, 'ETF添加失败')
      return false
    } finally {
      if (controller === mutationController) mutationController = null
      if (alive) mutating.value = false
    }
  }

  async function refreshProfiles() {
    if (mutating.value) return
    mutationController?.abort()
    const controller = new AbortController()
    mutationController = controller
    mutating.value = true
    mutationError.value = ''
    feedback.value = ''
    try {
      const result = await postJson<EtfWatchMutationResponse>('/etf-watch/refresh', {}, controller.signal)
      if (!alive || controller !== mutationController) return
      if (result.ok === false) throw new ApiError(result.error || '服务端未接受资料刷新')
      feedback.value = 'ETF低频资料已排队刷新；当前继续展示最近成功结果。'
      scheduleProfilePolls()
    } catch (caught) {
      if (!alive || controller !== mutationController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      mutationError.value = errorMessage(caught, 'ETF资料刷新提交失败')
    } finally {
      if (controller === mutationController) mutationController = null
      if (alive) mutating.value = false
    }
  }

  async function deleteItem(item: EtfWatchItem): Promise<boolean> {
    const id = String(item.id || '')
    if (!id || mutating.value) return false
    mutationController?.abort()
    const controller = new AbortController()
    mutationController = controller
    mutating.value = true
    mutationError.value = ''
    try {
      const result = await deleteJson<EtfWatchMutationResponse>(`/etf-watch/${encodeURIComponent(id)}`, controller.signal)
      if (!alive || controller !== mutationController) return false
      if (result.ok === false) throw new ApiError(result.error || '服务端未删除该ETF')
      items.value = items.value.filter((row) => row.id !== id)
      feedback.value = 'ETF已移出观察列表。'
      return true
    } catch (caught) {
      if (!alive || controller !== mutationController || (caught instanceof DOMException && caught.name === 'AbortError')) return false
      mutationError.value = errorMessage(caught, 'ETF删除失败')
      return false
    } finally {
      if (controller === mutationController) mutationController = null
      if (alive) mutating.value = false
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void loadItems()
  }

  onMounted(() => {
    alive = true
    void loadItems()
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void loadItems()
    }, 30_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    alive = false
    listController?.abort()
    mutationController?.abort()
    if (refreshTimer) clearInterval(refreshTimer)
    profilePollTimers.forEach((timer) => clearTimeout(timer))
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    items, loading, refreshing, mutating, loaded, timestamp, loadError, mutationError,
    feedback, degradedCount, refresh: loadItems, addCodes, refreshProfiles, deleteItem,
  }
}
