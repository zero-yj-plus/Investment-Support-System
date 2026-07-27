import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ApiError, postJson } from '../services/api'
import type {
  ScreenerFilterForm,
  ScreenerFilters,
  ScreenerRequest,
  ScreenerResponse,
  ScreenerStock,
  ScreenerWatchlistResponse,
  WatchlistActionState,
} from '../types/screener'

const BOARDS = ['主板', '创业板', '科创板', '北交所']
const SESSION_KEY = 'wudi.vue.screener.state'

function initialFilters(): ScreenerFilterForm {
  return {
    peMin: '', peMax: '', pbMin: '', pbMax: '', turnoverMin: '', turnoverMax: '',
    volumeRatioMin: '', circMvMin: '', circMvMax: '', changeMin: '', changeMax: '',
    amountMin: '', industry: '', boards: [...BOARDS], excludeSt: true, excludeLoss: false,
  }
}

function readableError(error: unknown, fallback: string): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : fallback
}

function numeric(value: string, label: string, errors: Record<string, string>): number | undefined {
  const text = value.trim()
  if (!text) return undefined
  const parsed = Number(text)
  if (!Number.isFinite(parsed)) {
    errors[label] = '请输入有效数字，或留空表示不限制'
    return undefined
  }
  return parsed
}

function validateRange(
  minimum: number | undefined,
  maximum: number | undefined,
  minimumKey: string,
  maximumKey: string,
  errors: Record<string, string>,
) {
  if (minimum != null && maximum != null && minimum > maximum) {
    errors[minimumKey] = '最小值不能大于最大值'
    errors[maximumKey] = '最大值不能小于最小值'
  }
}

export function useScreener() {
  const filters = reactive<ScreenerFilterForm>(initialFilters())
  const response = ref<ScreenerResponse | null>(null)
  const loading = ref(false)
  const showLoading = ref(false)
  const elapsedSeconds = ref(0)
  const transportError = ref('')
  const formErrors = ref<Record<string, string>>({})
  const resultStale = ref(false)
  const hasSubmitted = ref(false)
  const watchlistStates = reactive<Record<string, WatchlistActionState>>({})
  const watchlistErrors = reactive<Record<string, string>>({})
  let screenController: AbortController | null = null
  const watchlistControllers = new Map<string, AbortController>()
  const watchlistTimeouts = new Map<string, ReturnType<typeof setTimeout>>()
  let screenGeneration = 0
  let alive = false
  let loadingDelay: ReturnType<typeof setTimeout> | null = null
  let progressTimer: ReturnType<typeof setInterval> | null = null
  let screenTimeout: ReturnType<typeof setTimeout> | null = null
  let lastRequest: ScreenerRequest | null = null
  let screenTimedOut = false

  const stocks = computed(() => Array.isArray(response.value?.stocks) ? response.value.stocks : [])
  const busy = computed(() => loading.value || Object.values(watchlistStates).some((state) => state === 'adding'))

  function clearScreenTimers() {
    if (loadingDelay) clearTimeout(loadingDelay)
    if (progressTimer) clearInterval(progressTimer)
    if (screenTimeout) clearTimeout(screenTimeout)
    loadingDelay = null
    progressTimer = null
    screenTimeout = null
  }

  function buildRequest(): ScreenerRequest | null {
    const errors: Record<string, string> = {}
    const peMin = numeric(filters.peMin, 'peMin', errors)
    const peMax = numeric(filters.peMax, 'peMax', errors)
    const pbMin = numeric(filters.pbMin, 'pbMin', errors)
    const pbMax = numeric(filters.pbMax, 'pbMax', errors)
    const turnoverMin = numeric(filters.turnoverMin, 'turnoverMin', errors)
    const turnoverMax = numeric(filters.turnoverMax, 'turnoverMax', errors)
    const circMvMin = numeric(filters.circMvMin, 'circMvMin', errors)
    const circMvMax = numeric(filters.circMvMax, 'circMvMax', errors)
    const changeMin = numeric(filters.changeMin, 'changeMin', errors)
    const changeMax = numeric(filters.changeMax, 'changeMax', errors)
    validateRange(peMin, peMax, 'peMin', 'peMax', errors)
    validateRange(pbMin, pbMax, 'pbMin', 'pbMax', errors)
    validateRange(turnoverMin, turnoverMax, 'turnoverMin', 'turnoverMax', errors)
    validateRange(circMvMin, circMvMax, 'circMvMin', 'circMvMax', errors)
    validateRange(changeMin, changeMax, 'changeMin', 'changeMax', errors)
    if (!filters.boards.length) errors.boards = '至少保留一个板块；全选表示不限板块'
    formErrors.value = errors
    if (Object.keys(errors).length) return null

    const payload: ScreenerFilters = {
      exclude_st: filters.excludeSt,
      exclude_loss: filters.excludeLoss,
    }
    const values: Array<[keyof ScreenerFilters, number | undefined]> = [
      ['pe_min', peMin], ['pe_max', peMax], ['pb_min', pbMin], ['pb_max', pbMax],
      ['turnover_min', turnoverMin], ['turnover_max', turnoverMax],
      ['volume_ratio_min', numeric(filters.volumeRatioMin, 'volumeRatioMin', errors)],
      ['circ_mv_min', circMvMin], ['circ_mv_max', circMvMax],
      ['chg_min', changeMin], ['chg_max', changeMax],
      ['amount_min', numeric(filters.amountMin, 'amountMin', errors)],
    ]
    for (const [key, value] of values) if (value != null) Object.assign(payload, { [key]: value })
    const industry = filters.industry.trim()
    if (industry) payload.industry_kw = industry
    if (filters.boards.length < BOARDS.length) payload.boards = [...filters.boards]
    formErrors.value = errors
    if (Object.keys(errors).length) return null
    return { filters: payload, sort_by: 'amount', sort_desc: true, result_limit: 300 }
  }

  function persist(request: ScreenerRequest, payload: ScreenerResponse) {
    try {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify({ request, response: payload }))
    } catch {
      // 筛选结果仍留在当前 Vue 状态；禁用存储或配额不足不影响页面使用。
    }
  }

  function restore() {
    try {
      const parsed = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null') as { request?: ScreenerRequest; response?: ScreenerResponse } | null
      if (!parsed?.request || !parsed.response || !Array.isArray(parsed.response.stocks)) return
      lastRequest = parsed.request
      response.value = parsed.response
      hasSubmitted.value = true
      const source = parsed.request.filters || {}
      Object.assign(filters, {
        peMin: source.pe_min == null ? '' : String(source.pe_min),
        peMax: source.pe_max == null ? '' : String(source.pe_max),
        pbMin: source.pb_min == null ? '' : String(source.pb_min),
        pbMax: source.pb_max == null ? '' : String(source.pb_max),
        turnoverMin: source.turnover_min == null ? '' : String(source.turnover_min),
        turnoverMax: source.turnover_max == null ? '' : String(source.turnover_max),
        volumeRatioMin: source.volume_ratio_min == null ? '' : String(source.volume_ratio_min),
        circMvMin: source.circ_mv_min == null ? '' : String(source.circ_mv_min),
        circMvMax: source.circ_mv_max == null ? '' : String(source.circ_mv_max),
        changeMin: source.chg_min == null ? '' : String(source.chg_min),
        changeMax: source.chg_max == null ? '' : String(source.chg_max),
        amountMin: source.amount_min == null ? '' : String(source.amount_min),
        industry: source.industry_kw || '',
        boards: Array.isArray(source.boards) ? [...source.boards] : [...BOARDS],
        excludeSt: source.exclude_st !== false,
        excludeLoss: source.exclude_loss === true,
      })
    } catch {
      sessionStorage.removeItem(SESSION_KEY)
    }
  }

  async function runScreen() {
    if (loading.value) return false
    const request = buildRequest()
    if (!request) return false
    screenController?.abort()
    clearScreenTimers()
    const controller = new AbortController()
    const generation = ++screenGeneration
    screenController = controller
    screenTimedOut = false
    loading.value = true
    showLoading.value = false
    elapsedSeconds.value = 0
    transportError.value = ''
    hasSubmitted.value = true
    loadingDelay = setTimeout(() => { if (alive && loading.value) showLoading.value = true }, 300)
    progressTimer = setInterval(() => { if (alive && loading.value) elapsedSeconds.value += 1 }, 1000)
    screenTimeout = setTimeout(() => { screenTimedOut = true; controller.abort() }, 30_000)
    try {
      const payload = await postJson<ScreenerResponse>('/screen', request, controller.signal)
      if (!alive || controller !== screenController || generation !== screenGeneration) return false
      if (!Array.isArray(payload.stocks)) throw new Error('筛选接口未返回可识别的 stocks 数组')
      response.value = payload
      lastRequest = request
      resultStale.value = false
      persist(request, payload)
      return payload.ok !== false
    } catch (caught) {
      if (!alive || controller !== screenController || generation !== screenGeneration) return false
      if (caught instanceof DOMException && caught.name === 'AbortError' && !screenTimedOut) {
        transportError.value = '本次筛选已停止；已有成功结果仍保留。'
      } else {
        transportError.value = screenTimedOut
          ? '全市场筛选超过 30 秒，已停止本次请求；已有成功结果仍保留。'
          : `${readableError(caught, '条件选股请求失败')}；已有成功结果仍保留。`
      }
      return false
    } finally {
      if (controller === screenController) screenController = null
      if (alive && generation === screenGeneration) {
        loading.value = false
        showLoading.value = false
      }
      clearScreenTimers()
    }
  }

  function stopScreen() {
    if (!loading.value) return
    screenTimedOut = false
    screenController?.abort()
  }

  function markDirty() {
    formErrors.value = {}
    if (response.value) resultStale.value = true
  }

  function resetFilters() {
    Object.assign(filters, initialFilters())
    formErrors.value = {}
    if (response.value) resultStale.value = true
  }

  async function addToWatchlist(stock: ScreenerStock) {
    const code = String(stock.code || '')
    if (!/^\d{6}$/.test(code) || watchlistStates[code] === 'adding' || watchlistStates[code] === 'queued' || watchlistStates[code] === 'added') return false
    watchlistControllers.get(code)?.abort()
    const controller = new AbortController()
    watchlistControllers.set(code, controller)
    watchlistStates[code] = 'adding'
    delete watchlistErrors[code]
    const timeout = setTimeout(() => controller.abort(), 10_000)
    watchlistTimeouts.set(code, timeout)
    try {
      const payload = await postJson<ScreenerWatchlistResponse>('/watchlist', {
        code,
        name: stock.name || '',
        sector: stock.industry || '',
        add_reason: '条件选股命中',
      }, controller.signal)
      if (!alive || controller !== watchlistControllers.get(code)) return false
      if (payload.ok === false) throw new Error(payload.error || '观察池未接受本次提交')
      watchlistStates[code] = payload.zone_status === 'queued' ? 'queued' : 'added'
      return true
    } catch (caught) {
      if (!alive || controller !== watchlistControllers.get(code)) return false
      watchlistStates[code] = 'error'
      watchlistErrors[code] = caught instanceof DOMException && caught.name === 'AbortError'
        ? '提交超时，平台是否接受尚不明确，请到观察池核对后再重试。'
        : readableError(caught, '加入观察池失败')
      return false
    } finally {
      const activeTimeout = watchlistTimeouts.get(code)
      if (activeTimeout) clearTimeout(activeTimeout)
      watchlistTimeouts.delete(code)
      if (controller === watchlistControllers.get(code)) watchlistControllers.delete(code)
    }
  }

  async function refresh() {
    if (loading.value) return
    await runScreen()
  }

  onMounted(() => {
    alive = true
    restore()
  })

  onBeforeUnmount(() => {
    alive = false
    screenGeneration += 1
    screenController?.abort()
    watchlistControllers.forEach((controller) => controller.abort())
    watchlistTimeouts.forEach((timeout) => clearTimeout(timeout))
    watchlistControllers.clear()
    watchlistTimeouts.clear()
    clearScreenTimers()
  })

  return {
    filters,
    response,
    stocks,
    loading,
    showLoading,
    elapsedSeconds,
    transportError,
    formErrors,
    resultStale,
    hasSubmitted,
    watchlistStates,
    watchlistErrors,
    busy,
    runScreen,
    stopScreen,
    markDirty,
    resetFilters,
    addToWatchlist,
    refresh,
  }
}
