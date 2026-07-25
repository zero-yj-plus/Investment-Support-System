import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { getJson } from '../services/api'
import type {
  CrowdednessResponse,
  LeadersResponse,
  MainlineEndpoint,
  MainlineResearchData,
  MainlineResponse,
  MoneyEffectResponse,
} from '../types/mainlineResearch'

const ENDPOINTS: Record<MainlineEndpoint, string> = {
  moneyEffect: '/features/money-effect',
  crowdedness: '/features/crowdedness',
  mainline: '/features/mainline',
  leaders: '/features/leaders',
}

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : '主线证据读取失败'
}

export function useMainlineResearch(shouldAutoRefresh: () => boolean) {
  const data = reactive<MainlineResearchData>({
    moneyEffect: null,
    crowdedness: null,
    mainline: null,
    leaders: null,
  })
  const loading = reactive<Record<MainlineEndpoint, boolean>>({
    moneyEffect: true,
    crowdedness: true,
    mainline: true,
    leaders: true,
  })
  const errors = reactive<Record<MainlineEndpoint, string>>({
    moneyEffect: '',
    crowdedness: '',
    mainline: '',
    leaders: '',
  })
  const refreshing = ref(false)
  const lastSuccessAt = ref<Date | null>(null)
  const controllers: Partial<Record<MainlineEndpoint, AbortController>> = {}
  const generations: Record<MainlineEndpoint, number> = { moneyEffect: 0, crowdedness: 0, mainline: 0, leaders: 0 }
  let alive = false
  let refreshTimer: ReturnType<typeof setInterval> | null = null

  async function loadOne(key: MainlineEndpoint) {
    if (controllers[key]) return
    const controller = new AbortController()
    const generation = ++generations[key]
    let timedOut = false
    const timeout = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, 20_000)
    controllers[key] = controller
    loading[key] = true
    try {
      let payload: MainlineResearchData[MainlineEndpoint]
      if (key === 'moneyEffect') payload = await getJson<MoneyEffectResponse>(ENDPOINTS[key], controller.signal)
      else if (key === 'crowdedness') payload = await getJson<CrowdednessResponse>(ENDPOINTS[key], controller.signal)
      else if (key === 'mainline') payload = await getJson<MainlineResponse>(ENDPOINTS[key], controller.signal)
      else payload = await getJson<LeadersResponse>(ENDPOINTS[key], controller.signal)
      if (!alive || controllers[key] !== controller || generations[key] !== generation) return
      data[key] = payload as never
      errors[key] = payload?.ok === false ? payload.error || payload.missing?.[0] || '接口返回不可用状态' : ''
      if (!errors[key]) lastSuccessAt.value = new Date()
    } catch (caught) {
      if (!alive || controllers[key] !== controller || generations[key] !== generation) return
      if (caught instanceof DOMException && caught.name === 'AbortError' && !timedOut) return
      errors[key] = timedOut ? '请求超过 20 秒，已停止本模块取数' : errorMessage(caught)
    } finally {
      clearTimeout(timeout)
      if (controllers[key] === controller) delete controllers[key]
      if (alive && generations[key] === generation) loading[key] = false
    }
  }

  async function refresh() {
    if (refreshing.value || Object.keys(controllers).length) return
    refreshing.value = true
    try {
      await loadOne('moneyEffect')
      await Promise.allSettled([
        loadOne('crowdedness'),
        loadOne('mainline'),
        loadOne('leaders'),
      ])
    } finally {
      if (alive) refreshing.value = false
    }
  }

  function retry(key: MainlineEndpoint) {
    return loadOne(key)
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible' && shouldAutoRefresh()) void refresh()
  }

  onMounted(() => {
    alive = true
    void refresh()
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible' && shouldAutoRefresh()) void refresh()
    }, 25_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    alive = false
    ;(Object.keys(generations) as MainlineEndpoint[]).forEach((key) => {
      generations[key] += 1
      controllers[key]?.abort()
    })
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return { data, loading, errors, refreshing, lastSuccessAt, refresh, retry }
}
