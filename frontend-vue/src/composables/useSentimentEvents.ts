import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson } from '../services/api'
import type { SentimentEvent, SentimentEventsResponse } from '../types/sentimentEvents'

let sentimentSessionSnapshot: SentimentEventsResponse | null = null

function errorMessage(error: unknown) {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : '舆情事件读取失败'
}

export function useSentimentEvents() {
  const data = ref<SentimentEventsResponse | null>(sentimentSessionSnapshot)
  const loading = ref(!sentimentSessionSnapshot)
  const refreshing = ref(false)
  const error = ref('')
  const refreshNotice = ref('')
  let eventController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  let alive = false

  const events = computed<SentimentEvent[]>(() => Array.isArray(data.value?.items)
    ? data.value.items.filter((item) => item && Boolean(item.title?.trim() || item.content?.trim()))
    : [])

  async function refresh(options: { background?: boolean } = {}) {
    if (refreshing.value) return
    eventController?.abort()
    const controller = new AbortController()
    eventController = controller
    refreshing.value = true
    try {
      const payload = await getJson<SentimentEventsResponse>('/news/a-share', controller.signal)
      if (!alive || controller !== eventController) return
      if (payload.ok === false) throw new Error(payload.error || payload.missing?.[0] || '舆情事件接口暂不可用')
      data.value = payload
      sentimentSessionSnapshot = payload
      error.value = ''
      refreshNotice.value = ''
    } catch (caught) {
      if (!alive || controller !== eventController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      const message = errorMessage(caught)
      error.value = message
      refreshNotice.value = options.background && events.value.length
        ? '后台刷新失败，当前保留上次成功数据。'
        : ''
    } finally {
      if (controller === eventController) eventController = null
      if (alive) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void refresh({ background: true })
  }

  onMounted(() => {
    alive = true
    void refresh({ background: Boolean(sentimentSessionSnapshot) })
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh({ background: true })
    }, 30_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    alive = false
    eventController?.abort()
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return { data, events, loading, refreshing, error, refreshNotice, refresh }
}
