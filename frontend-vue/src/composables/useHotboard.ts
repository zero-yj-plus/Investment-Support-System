import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson } from '../services/api'
import type { HotboardResponse } from '../types/hotboard'

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : '涨停题材数据读取失败'
}

export function useHotboard() {
  const data = ref<HotboardResponse | null>(null)
  const loading = ref(true)
  const refreshing = ref(false)
  const error = ref('')
  let listController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  let alive = false

  const stocks = computed(() => Array.isArray(data.value?.stocks)
    ? data.value.stocks.filter((item) => typeof item?.code === 'string' && item.code.trim())
    : [])

  async function refresh() {
    if (refreshing.value) return
    listController?.abort()
    const controller = new AbortController()
    listController = controller
    refreshing.value = true
    try {
      const payload = await getJson<HotboardResponse>('/market/limitup/themes', controller.signal)
      if (!alive || controller !== listController) return
      if (payload.ok === false) {
        throw new Error(payload.error || payload.missing?.[0] || '涨停题材接口暂不可用')
      }
      data.value = payload
      error.value = ''
    } catch (caught) {
      if (!alive || controller !== listController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      error.value = errorMessage(caught)
    } finally {
      if (controller === listController) listController = null
      if (alive) {
        loading.value = false
        refreshing.value = false
      }
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
    listController?.abort()
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return { data, stocks, loading, refreshing, error, refresh }
}
