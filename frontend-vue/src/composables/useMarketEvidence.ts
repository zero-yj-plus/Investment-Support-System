import { onBeforeUnmount, onMounted, reactive } from 'vue'
import { getJson } from '../services/api'
import type {
  ApiResponseState,
  MarketFlowResponse,
  MarketSectorsResponse,
  MultiperiodResponse,
  SentimentTodayResponse,
  StockFlowResponse,
} from '../types/dashboard'

export type EvidenceKey = 'sentiment' | 'flow' | 'stockflow' | 'sectors' | 'multiperiod'

interface EvidenceData {
  sentiment: SentimentTodayResponse | null
  flow: MarketFlowResponse | null
  stockflow: StockFlowResponse | null
  sectors: MarketSectorsResponse | null
  multiperiod: MultiperiodResponse | null
}

const ENDPOINTS: Record<EvidenceKey, string> = {
  sentiment: '/sentiment/today',
  flow: '/market/flow',
  stockflow: '/market/stockflow?n=10',
  sectors: '/market/sectors',
  multiperiod: '/market/multiperiod?n=12',
}

const TIMEOUTS: Record<EvidenceKey, number> = {
  sentiment: 10_000,
  flow: 16_000,
  stockflow: 16_000,
  sectors: 16_000,
  multiperiod: 20_000,
}

function unavailableMessage(payload: ApiResponseState, fallback: string): string | null {
  if (payload.ok !== false) return null
  return payload.error
    || payload.missing_fields?.filter(Boolean).join('；')
    || payload.missing?.filter(Boolean).join('；')
    || fallback
}

export function useMarketEvidence() {
  const data = reactive<EvidenceData>({
    sentiment: null,
    flow: null,
    stockflow: null,
    sectors: null,
    multiperiod: null,
  })
  const errors = reactive<Partial<Record<EvidenceKey, string>>>({})
  const loading = reactive<Record<EvidenceKey, boolean>>({
    sentiment: false,
    flow: false,
    stockflow: false,
    sectors: false,
    multiperiod: false,
  })
  const loaded = reactive<Record<EvidenceKey, boolean>>({
    sentiment: false,
    flow: false,
    stockflow: false,
    sectors: false,
    multiperiod: false,
  })
  const controllers = new Map<EvidenceKey, AbortController>()
  let refreshTimer: ReturnType<typeof setInterval> | null = null
  let disposed = false

  async function load(key: EvidenceKey, force = false) {
    if (disposed || loading[key] || (loaded[key] && !force)) return
    loading[key] = true
    const controller = new AbortController()
    controllers.set(key, controller)
    let timedOut = false
    const timeout = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, TIMEOUTS[key])

    try {
      let payload: ApiResponseState
      if (key === 'sentiment') payload = data.sentiment = await getJson<SentimentTodayResponse>(ENDPOINTS[key], controller.signal)
      else if (key === 'flow') payload = data.flow = await getJson<MarketFlowResponse>(ENDPOINTS[key], controller.signal)
      else if (key === 'stockflow') payload = data.stockflow = await getJson<StockFlowResponse>(ENDPOINTS[key], controller.signal)
      else if (key === 'sectors') payload = data.sectors = await getJson<MarketSectorsResponse>(ENDPOINTS[key], controller.signal)
      else payload = data.multiperiod = await getJson<MultiperiodResponse>(ENDPOINTS[key], controller.signal)

      const unavailable = unavailableMessage(payload, `${key} 接口暂不可用`)
      if (unavailable) errors[key] = unavailable
      else delete errors[key]
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        if (timedOut) errors[key] = '请求超时，保留此前成功数据'
      } else {
        errors[key] = error instanceof Error ? error.message : '数据读取失败'
      }
    } finally {
      clearTimeout(timeout)
      if (controllers.get(key) === controller) controllers.delete(key)
      loaded[key] = true
      loading[key] = false
    }
  }

  async function ensure(keys: EvidenceKey[]) {
    await Promise.allSettled(keys.map((key) => load(key)))
  }

  async function refreshLoaded(includeDeep = false) {
    const keys = (Object.keys(loaded) as EvidenceKey[]).filter((key) => (
      loaded[key] && (includeDeep || (key !== 'sectors' && key !== 'multiperiod'))
    ))
    await Promise.allSettled(keys.map((key) => load(key, true)))
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') void refreshLoaded(true)
  }

  onMounted(() => {
    refreshTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void refreshLoaded(false)
    }, 60_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    disposed = true
    controllers.forEach((controller) => controller.abort())
    controllers.clear()
    if (refreshTimer) clearInterval(refreshTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return { data, errors, loading, loaded, ensure, refreshLoaded }
}
