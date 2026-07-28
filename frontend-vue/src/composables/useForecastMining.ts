import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson } from '../services/api'
import type { ForecastItem, ForecastMiningResponse } from '../types/forecastMining'

const VISIBILITY_REFRESH_AFTER_MS = 15 * 60 * 1000
const REQUEST_TIMEOUT_MS = 35_000

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : '业绩预告榜单读取失败'
}

function validRows(value: unknown): ForecastItem[] {
  return Array.isArray(value)
    ? value.filter((row): row is ForecastItem => Boolean(row && typeof row === 'object'))
    : []
}

export function useForecastMining() {
  const data = ref<ForecastMiningResponse | null>(null)
  const loading = ref(true)
  const refreshing = ref(false)
  const error = ref('')
  let miningController: AbortController | null = null
  let requestTimeout: ReturnType<typeof setTimeout> | null = null
  let lastLoadedAt = 0
  let alive = false

  const items = computed(() => validRows(data.value?.items))
  const traps = computed(() => validRows(data.value?.traps))

  function clearRequestTimeout() {
    if (requestTimeout) clearTimeout(requestTimeout)
    requestTimeout = null
  }

  async function refresh() {
    if (refreshing.value) return
    miningController?.abort()
    clearRequestTimeout()
    const controller = new AbortController()
    let timedOut = false
    miningController = controller
    refreshing.value = true
    requestTimeout = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, REQUEST_TIMEOUT_MS)
    try {
      const payload = await getJson<ForecastMiningResponse>('/forecast/mining', controller.signal)
      if (!alive || controller !== miningController) return
      data.value = payload
      lastLoadedAt = Date.now()
      error.value = payload.ok === false
        ? payload.error || payload.missing?.[0] || '业绩预告公开源暂不可用'
        : ''
    } catch (caught) {
      if (!alive || controller !== miningController) return
      error.value = timedOut ? '业绩预告榜单请求超时' : errorMessage(caught)
    } finally {
      if (controller === miningController) {
        miningController = null
        clearRequestTimeout()
      }
      if (alive) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible' && Date.now() - lastLoadedAt >= VISIBILITY_REFRESH_AFTER_MS) void refresh()
  }

  onMounted(() => {
    alive = true
    document.addEventListener('visibilitychange', onVisibilityChange)
    void refresh()
  })

  onBeforeUnmount(() => {
    alive = false
    miningController?.abort()
    clearRequestTimeout()
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return { data, items, traps, loading, refreshing, error, refresh }
}
