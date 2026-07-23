import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { getJson } from '../services/api'
import type {
  ApiResponseState,
  DashboardEndpoint,
  DecisionOverviewData,
  MainlineResponse,
  MarketOverviewResponse,
  PulseResponse,
  QualityState,
  ShellStatusResponse,
  SystemRiskResponse,
} from '../types/dashboard'

const ENDPOINTS = {
  shell: '/shell-status',
  pulse: '/pulse',
  market: '/market/overview_cached',
  risk: '/sysrisk',
  mainlines: '/market/mainlines',
} as const satisfies Record<DashboardEndpoint, string>

function messageOf(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : '数据读取失败'
}

function isUsable(payload: ApiResponseState | null): boolean {
  return payload != null && payload.ok !== false
}

function isDegraded(payload: ApiResponseState | null): boolean {
  if (!payload) return false
  const quality = `${payload.quality_status || payload.data_state || payload.state || ''}`.toLowerCase()
  return Boolean(
    payload.stale
    || payload.missing?.length
    || payload.missing_fields?.length
    || payload.degraded_factors?.length,
  ) || /degraded|partial|missing|stale|delayed/.test(quality)
}

function serviceError(payload: ApiResponseState, fallback: string): string | null {
  if (payload.ok !== false) return null
  return payload.error
    || payload.missing_fields?.filter(Boolean).join('；')
    || payload.missing?.filter(Boolean).join('；')
    || fallback
}

export function useDecisionOverview() {
  const data = reactive<DecisionOverviewData>({
    shell: null,
    pulse: null,
    market: null,
    risk: null,
    mainlines: null,
  })
  const errors = reactive<Partial<Record<DashboardEndpoint, string>>>({})
  const loading = ref(true)
  const refreshing = ref(false)
  const lastSuccessAt = ref<Date | null>(null)
  let controller: AbortController | null = null
  let refreshTimer: ReturnType<typeof setInterval> | null = null

  async function refresh() {
    if (refreshing.value) return
    refreshing.value = true
    controller?.abort()
    controller = new AbortController()
    let timedOut = false
    let receivedUsableData = false
    const timeout = setTimeout(() => {
      timedOut = true
      controller?.abort()
    }, 20_000)

    const tasks = (Object.keys(ENDPOINTS) as DashboardEndpoint[]).map(async (key) => {
      try {
        let payload: ApiResponseState
        if (key === 'shell') payload = data.shell = await getJson<ShellStatusResponse>(ENDPOINTS[key], controller!.signal)
        else if (key === 'pulse') payload = data.pulse = await getJson<PulseResponse>(ENDPOINTS[key], controller!.signal)
        else if (key === 'market') payload = data.market = await getJson<MarketOverviewResponse>(ENDPOINTS[key], controller!.signal)
        else if (key === 'risk') payload = data.risk = await getJson<SystemRiskResponse>(ENDPOINTS[key], controller!.signal)
        else payload = data.mainlines = await getJson<MainlineResponse>(ENDPOINTS[key], controller!.signal)

        const unavailable = serviceError(payload, `${key} 接口暂不可用`)
        if (unavailable) errors[key] = unavailable
        else {
          delete errors[key]
          receivedUsableData = true
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          if (timedOut) errors[key] = '请求超时，已保留此前取得的数据'
          return
        }
        errors[key] = messageOf(error)
      }
    })

    await Promise.allSettled(tasks)
    clearTimeout(timeout)
    if (receivedUsableData) lastSuccessAt.value = new Date()
    loading.value = false
    refreshing.value = false
  }

  const quality = computed<QualityState>(() => {
    const core = [data.pulse, data.market, data.risk, data.mainlines]
    const available = core.filter(isUsable).length
    const coreErrors = (['pulse', 'market', 'risk', 'mainlines'] as DashboardEndpoint[]).filter((key) => errors[key]).length
    const degradedPayload = core.some(isDegraded)
    if (available === 4 && isUsable(data.shell) && coreErrors === 0 && !errors.shell && !degradedPayload && !isDegraded(data.shell)) return 'complete'
    if (available >= 3) return 'degraded'
    if (available > 0) return 'partial'
    return 'missing'
  })

  const connectionLabel = computed(() => ({
    complete: '决策可用',
    degraded: '增强项缺失',
    partial: '部分覆盖',
    missing: loading.value ? '正在连接' : '暂不可用',
  })[quality.value])

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void refresh()
  }

  onMounted(() => {
    void refresh()
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, 30_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    controller?.abort()
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return { data, errors, loading, refreshing, lastSuccessAt, quality, connectionLabel, refresh }
}
