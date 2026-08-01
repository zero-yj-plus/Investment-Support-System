import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ApiError, deleteJson, getJson, postJson, putJson } from '../services/api'
import type {
  VerifiedWatchlistIdentity,
  WatchlistImportItem,
  WatchlistImportResponse,
  WatchlistInput,
  WatchlistItem,
  WatchlistMutationResponse,
  WatchlistQuoteResponse,
  WatchlistResponse,
} from '../types/watchlist'

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : fallback
}

function importCount(value: unknown, label: string): number {
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed < 0) throw new ApiError(`批量导入响应缺少有效的${label}`)
  return parsed
}

export function useWatchlist() {
  const items = ref<WatchlistItem[]>([])
  const loading = ref(true)
  const refreshing = ref(false)
  const verifying = ref(false)
  const saving = ref(false)
  const importing = ref(false)
  const deletingId = ref('')
  const recalculating = ref(false)
  const loaded = ref(false)
  const timestamp = ref('')
  const responseMissing = ref<string[]>([])
  const loadError = ref('')
  const verifyError = ref('')
  const mutationError = ref('')
  const feedback = ref('')

  let listController: AbortController | null = null
  let verifyController: AbortController | null = null
  let saveController: AbortController | null = null
  let importController: AbortController | null = null
  let deleteController: AbortController | null = null
  let recalculateController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  let alive = false

  const actionBusy = computed(() => (
    verifying.value || saving.value || importing.value || Boolean(deletingId.value) || recalculating.value
  ))

  async function loadItems(background = false) {
    if (refreshing.value) return
    listController?.abort()
    const controller = new AbortController()
    listController = controller
    refreshing.value = true
    if (!loaded.value) loading.value = true
    try {
      const payload = await getJson<WatchlistResponse>('/watchlist', controller.signal)
      if (!alive || controller !== listController) return
      if (payload.ok === false) throw new ApiError(payload.error || '观察池返回不可用状态')
      if (!Array.isArray(payload.items)) throw new ApiError('观察池未返回可识别的 items 数组')
      items.value = payload.items
      timestamp.value = String(payload.ts || '')
      responseMissing.value = Array.isArray(payload.missing) ? payload.missing.map(String) : []
      loadError.value = ''
      loaded.value = true
    } catch (caught) {
      if (!alive || controller !== listController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      loadError.value = errorMessage(caught, '观察池读取失败')
    } finally {
      if (controller === listController) listController = null
      if (alive) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  async function verifyCode(code: string): Promise<VerifiedWatchlistIdentity | null> {
    if (verifying.value) return null
    verifyController?.abort()
    const controller = new AbortController()
    verifyController = controller
    verifying.value = true
    verifyError.value = ''
    try {
      const payload = await getJson<WatchlistQuoteResponse>(`/quote/${encodeURIComponent(code)}`, controller.signal)
      if (!alive || controller !== verifyController) return null
      if (payload.recognized === false || payload.state === 'not_found') {
        verifyError.value = payload.error || '该代码不在当前完整 A 股名录中。'
        return null
      }
      if (payload.recognized !== true) {
        verifyError.value = payload.error || '当前名录与报价均不足，暂不能确认该股票代码。'
        return null
      }
      return {
        name: String(payload.quote?.name || payload.identity?.name || ''),
        quote: payload.quote || null,
        quoteAvailable: payload.quote?.price != null,
      }
    } catch (caught) {
      if (!alive || controller !== verifyController || (caught instanceof DOMException && caught.name === 'AbortError')) return null
      verifyError.value = `${errorMessage(caught, '股票代码核验失败')}；当前未写入观察池。`
      return null
    } finally {
      if (controller === verifyController) verifyController = null
      if (alive) verifying.value = false
    }
  }

  async function saveItem(input: WatchlistInput, existingId = ''): Promise<boolean> {
    if (saving.value || verifying.value) return false
    verifyError.value = ''
    mutationError.value = ''
    feedback.value = ''
    let payload = input
    let quoteAvailable = true
    if (!existingId) {
      const verified = await verifyCode(input.code)
      if (!verified || !alive) return false
      payload = { ...input, name: verified.name || input.name }
      quoteAvailable = verified.quoteAvailable
    }
    saveController?.abort()
    const controller = new AbortController()
    saveController = controller
    saving.value = true
    try {
      const result = existingId
        ? await putJson<WatchlistMutationResponse>(`/watchlist/${encodeURIComponent(existingId)}`, payload, controller.signal)
        : await postJson<WatchlistMutationResponse>('/watchlist', payload, controller.signal)
      if (!alive || controller !== saveController) return false
      if (result.ok === false) throw new ApiError(result.error || '服务端拒绝保存观察项')
      feedback.value = result.zone_status === 'queued'
        ? `${existingId ? '观察项已更新' : '观察项已加入'}；双周期区间已排队计算，尚未完成。`
        : `${existingId ? '观察项已更新' : '观察项已加入'}。`
      if (!quoteAvailable) feedback.value += ' 股票已识别，但当前报价缺失。'
      await loadItems(true)
      return true
    } catch (caught) {
      if (!alive || controller !== saveController || (caught instanceof DOMException && caught.name === 'AbortError')) return false
      mutationError.value = errorMessage(caught, '观察项保存失败')
      return false
    } finally {
      if (controller === saveController) saveController = null
      if (alive) saving.value = false
    }
  }

  async function deleteItem(item: WatchlistItem): Promise<boolean> {
    const id = String(item.id || '').trim()
    if (!id || deletingId.value) return false
    deleteController?.abort()
    const controller = new AbortController()
    deleteController = controller
    deletingId.value = id
    mutationError.value = ''
    feedback.value = ''
    try {
      const result = await deleteJson<WatchlistMutationResponse>(`/watchlist/${encodeURIComponent(id)}`, controller.signal)
      if (!alive || controller !== deleteController) return false
      if (result.ok === false) throw new ApiError(result.error || '服务端未删除该观察项')
      items.value = items.value.filter((row) => String(row.id || '') !== id)
      feedback.value = '观察项已移出；这是当前账本变更，不改写历史报告或推荐记录。'
      return true
    } catch (caught) {
      if (!alive || controller !== deleteController || (caught instanceof DOMException && caught.name === 'AbortError')) return false
      mutationError.value = errorMessage(caught, '观察项删除失败')
      return false
    } finally {
      if (controller === deleteController) deleteController = null
      if (alive) deletingId.value = ''
    }
  }

  async function importItems(candidates: WatchlistImportItem[]): Promise<boolean> {
    if (importing.value || !candidates.length) return false
    importController?.abort()
    const controller = new AbortController()
    importController = controller
    importing.value = true
    mutationError.value = ''
    feedback.value = ''
    try {
      const result = await postJson<WatchlistImportResponse>('/watchlist/import', { items: candidates }, controller.signal)
      if (!alive || controller !== importController) return false
      if (result.ok === false) throw new ApiError(result.error || '服务端拒绝批量导入')
      const added = importCount(result.added_count, '新增计数')
      const duplicates = importCount(result.duplicate_count, '重复计数')
      const invalid = importCount(result.invalid_count, '无效计数')
      feedback.value = `批量导入完成：新增 ${added} 条`
      if (duplicates || invalid) feedback.value += `，服务端另跳过重复 ${duplicates} 条、无效 ${invalid} 条`
      feedback.value += result.zone_status === 'queued'
        ? '；双周期区间已排队计算，尚未完成。'
        : '。'
      await loadItems(true)
      return true
    } catch (caught) {
      if (!alive || controller !== importController || (caught instanceof DOMException && caught.name === 'AbortError')) return false
      mutationError.value = errorMessage(caught, '观察池批量导入失败')
      return false
    } finally {
      if (controller === importController) importController = null
      if (alive) importing.value = false
    }
  }

  async function recalculate() {
    if (recalculating.value) return
    recalculateController?.abort()
    const controller = new AbortController()
    recalculateController = controller
    recalculating.value = true
    mutationError.value = ''
    feedback.value = ''
    try {
      const result = await postJson<WatchlistMutationResponse>('/watchlist/recalculate', {}, controller.signal)
      if (!alive || controller !== recalculateController) return
      if (result.ok === false) throw new ApiError(result.error || '服务端未接受区间重算请求')
      feedback.value = result.zone_status === 'queued'
        ? '双周期区间重算已排队，尚未完成；请稍后刷新查看结果。'
        : '区间重算请求已提交。'
    } catch (caught) {
      if (!alive || controller !== recalculateController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      mutationError.value = errorMessage(caught, '区间重算提交失败')
    } finally {
      if (controller === recalculateController) recalculateController = null
      if (alive) recalculating.value = false
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void loadItems(true)
  }

  onMounted(() => {
    alive = true
    void loadItems()
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void loadItems(true)
    }, 30_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    alive = false
    listController?.abort()
    verifyController?.abort()
    saveController?.abort()
    importController?.abort()
    deleteController?.abort()
    recalculateController?.abort()
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    items,
    loading,
    refreshing,
    verifying,
    saving,
    importing,
    deletingId,
    recalculating,
    loaded,
    timestamp,
    responseMissing,
    loadError,
    verifyError,
    mutationError,
    feedback,
    actionBusy,
    clearMutationError: () => { mutationError.value = '' },
    refresh: () => loadItems(false),
    saveItem,
    importItems,
    deleteItem,
    recalculate,
  }
}
