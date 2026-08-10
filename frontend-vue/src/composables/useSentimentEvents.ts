import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getJson, postJson } from '../services/api'
import type {
  SentimentArchiveResponse,
  SentimentArchiveState,
  SentimentEvent,
  SentimentEventsResponse,
} from '../types/sentimentEvents'
import { eventArchiveKey } from '../utils/sentimentFormat'

let sentimentSessionSnapshot: SentimentEventsResponse | null = null
const sentimentSessionArchived = new Set<string>()

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
  const archiveStates = ref<Record<string, SentimentArchiveState>>({})
  let eventController: AbortController | null = null
  const archiveControllers = new Map<string, AbortController>()
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

  async function archiveEvent(event: SentimentEvent) {
    const key = eventArchiveKey(event)
    const current = archiveStates.value[key]
    if (current?.status === 'archiving' || current?.status === 'archived') return
    archiveControllers.get(key)?.abort()
    const controller = new AbortController()
    archiveControllers.set(key, controller)
    archiveStates.value = {
      ...archiveStates.value,
      [key]: { status: 'archiving', message: '正在留档…' },
    }
    try {
      const payload = await postJson<SentimentArchiveResponse>(
        '/news/a-share/archive',
        { event },
        controller.signal,
      )
      if (!alive || archiveControllers.get(key) !== controller) return
      if (payload.ok === false) throw new Error(payload.error || '留档失败')
      sentimentSessionArchived.add(key)
      archiveStates.value = {
        ...archiveStates.value,
        [key]: {
          status: 'archived',
          message: payload.created === false
            ? '这条事件此前已留档，可在“历史与知识 → 知识库”查看。'
            : '已留档，可在“历史与知识 → 知识库”审核与查看。',
        },
      }
    } catch (caught) {
      if (!alive || archiveControllers.get(key) !== controller || (caught instanceof DOMException && caught.name === 'AbortError')) return
      archiveStates.value = {
        ...archiveStates.value,
        [key]: { status: 'error', message: errorMessage(caught) },
      }
    } finally {
      if (archiveControllers.get(key) === controller) archiveControllers.delete(key)
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void refresh({ background: true })
  }

  onMounted(() => {
    alive = true
    archiveStates.value = Object.fromEntries(
      [...sentimentSessionArchived].map((key) => [key, {
        status: 'archived' as const,
        message: '本次会话已留档，可在“历史与知识 → 知识库”查看。',
      }]),
    )
    void refresh({ background: Boolean(sentimentSessionSnapshot) })
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh({ background: true })
    }, 30_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    alive = false
    eventController?.abort()
    archiveControllers.forEach((controller) => controller.abort())
    archiveControllers.clear()
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return { data, events, loading, refreshing, error, refreshNotice, archiveStates, refresh, archiveEvent }
}
