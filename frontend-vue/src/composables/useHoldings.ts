import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ApiError, getJson, postJson, putJson } from '../services/api'
import type {
  HoldingDraft,
  HoldingRecord,
  HoldingQuote,
  HoldingActionResponse,
  HoldingRiskPlan,
  HoldingRiskState,
  HoldingsResponse,
  HoldingsRiskResponse,
  NewHoldingInput,
  QuoteResponse,
  QuotesResponse,
  SaveHoldingPayload,
} from '../types/holdings'
import { finiteNumber, holdingView } from '../utils/holdingsFormat'

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : fallback
}

let clientKeySeed = 0

function toDraft(row: HoldingRecord): HoldingDraft {
  clientKeySeed += 1
  return {
    clientKey: `holding-${clientKeySeed}`,
    code: String(row.code == null ? '' : row.code).trim(),
    name: String(row.name == null ? '' : row.name),
    cost: finiteNumber(row.cost),
    shares: finiteNumber(row.shares),
    industry: String(row.industry == null ? '' : row.industry),
    storedMarketValue: finiteNumber(row.market_value),
    storedWeightPct: finiteNumber(row.weight_pct),
    note: String(row.note == null ? '' : row.note),
    riskPlan: row.risk_plan && typeof row.risk_plan === 'object' ? { ...row.risk_plan } as HoldingRiskPlan : null,
  }
}

export function useHoldings() {
  const holdings = ref<HoldingDraft[]>([])
  const quotes = ref<Record<string, HoldingQuote>>({})
  const riskStates = ref<Record<string, HoldingRiskState>>({})
  const missingQuoteCodes = ref<string[]>([])
  const loading = ref(true)
  const refreshing = ref(false)
  const quotesLoading = ref(false)
  const saving = ref(false)
  const adding = ref(false)
  const riskRefreshing = ref(false)
  const actionRunning = ref(false)
  const loaded = ref(false)
  const dirty = ref(false)
  const holdingsError = ref('')
  const quotesError = ref('')
  const saveError = ref('')
  const addError = ref('')
  const riskError = ref('')
  const feedback = ref('')
  let holdingsController: AbortController | null = null
  let quotesController: AbortController | null = null
  let saveController: AbortController | null = null
  let verifyController: AbortController | null = null
  let riskController: AbortController | null = null
  let actionController: AbortController | null = null
  let quoteTimer: ReturnType<typeof setInterval> | null = null
  let alive = false

  const baseViews = computed(() => holdings.value.map((row) => holdingView(
    row,
    quotes.value[row.code] || null,
    riskStates.value[row.code] || null,
  )))
  const quotedCount = computed(() => baseViews.value.filter((row) => row.price != null).length)
  const quoteCoverageComplete = computed(() => holdings.value.length > 0 && quotedCount.value === holdings.value.length)
  const quoteAsOf = computed(() => {
    const values = Object.values(quotes.value).map((quote) => String(quote.as_of || '').trim()).filter(Boolean)
    return values.sort().at(-1) || ''
  })
  const quoteSources = computed(() => [...new Set(
    Object.values(quotes.value).map((quote) => String(quote.source || '').trim()).filter(Boolean),
  )])
  const hasStaleQuotes = computed(() => Object.values(quotes.value).some((quote) => quote.stale === true))
  const aggregate = computed(() => {
    const costAmounts = baseViews.value.map((row) => row.costAmount)
    const costAmount = costAmounts.length > 0 && costAmounts.every((value) => value != null)
      ? costAmounts.reduce<number>((sum, value) => sum + (value as number), 0)
      : null
    if (!quoteCoverageComplete.value) {
      return { marketValue: null, costAmount, pnl: null, pnlPct: null }
    }
    const marketValues = baseViews.value.map((row) => row.marketValue)
    if (marketValues.some((value) => value == null) || costAmount == null) {
      return { marketValue: null, costAmount, pnl: null, pnlPct: null }
    }
    const marketValue = marketValues.reduce<number>((sum, value) => sum + (value as number), 0)
    const pnl = marketValue - costAmount
    const pnlPct = costAmount > 0 ? pnl / costAmount * 100 : null
    return { marketValue, costAmount, pnl, pnlPct }
  })
  const views = computed(() => baseViews.value.map((row) => ({
    ...row,
    holdingSharePct: row.marketValue != null && aggregate.value.marketValue != null && aggregate.value.marketValue > 0
      ? row.marketValue / aggregate.value.marketValue * 100
      : null,
  })))

  function markDirty() {
    dirty.value = true
    feedback.value = ''
    saveError.value = ''
  }

  function updateHolding(clientKey: string, field: 'code' | 'cost' | 'shares' | 'note', value: string) {
    const row = holdings.value.find((item) => item.clientKey === clientKey)
    if (!row) return
    if (field === 'code') {
      row.code = value.replace(/\D/g, '').slice(0, 6)
      row.name = ''
      row.industry = ''
    } else if (field === 'cost' || field === 'shares') {
      row[field] = finiteNumber(value)
    } else {
      row.note = value.slice(0, 200)
    }
    markDirty()
  }

  function removeHolding(clientKey: string) {
    holdings.value = holdings.value.filter((row) => row.clientKey !== clientKey)
    markDirty()
    feedback.value = '已从待保存列表移除；保存前可通过重新载入恢复。'
  }

  async function refreshQuotes() {
    const codes = [...new Set(holdings.value.map((row) => row.code).filter((code) => /^\d{6}$/.test(code)))]
    if (!codes.length || quotesLoading.value) return
    quotesController?.abort()
    const controller = new AbortController()
    quotesController = controller
    quotesLoading.value = true
    try {
      const payload = await getJson<QuotesResponse>(`/quotes?codes=${encodeURIComponent(codes.join(','))}`, controller.signal)
      if (!alive || controller !== quotesController) return
      const nextQuotes = payload.quotes && typeof payload.quotes === 'object' ? payload.quotes : {}
      quotes.value = { ...quotes.value, ...nextQuotes }
      missingQuoteCodes.value = Array.isArray(payload.missing)
        ? payload.missing.map(String)
        : codes.filter((code) => nextQuotes[code] == null)
      quotesError.value = payload.ok === false && Object.keys(nextQuotes).length === 0
        ? payload.error || '批量报价当前没有返回可用价格'
        : ''
      for (const row of holdings.value) {
        const quote = nextQuotes[row.code]
        if (quote?.name && !row.name) row.name = String(quote.name)
      }
    } catch (caught) {
      if (!alive || controller !== quotesController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      missingQuoteCodes.value = codes
      quotesError.value = errorMessage(caught, '批量报价读取失败')
    } finally {
      if (controller === quotesController) quotesController = null
      if (alive) quotesLoading.value = false
    }
  }

  async function refreshRiskStates(force = false) {
    if (riskRefreshing.value) return
    riskController?.abort()
    const controller = new AbortController()
    riskController = controller
    riskRefreshing.value = true
    riskError.value = ''
    try {
      const payload = force
        ? await postJson<HoldingsRiskResponse>('/holdings/risk/refresh', {}, controller.signal)
        : await getJson<HoldingsRiskResponse>('/holdings/risk', controller.signal)
      if (!alive || controller !== riskController) return
      if (payload.ok === false) throw new ApiError(payload.error || '仓位管理状态不可用')
      riskStates.value = payload.states && typeof payload.states === 'object' ? payload.states : {}
      if (force) feedback.value = '仓位管理状态已按最新可用数据刷新。'
    } catch (caught) {
      if (!alive || controller !== riskController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      riskError.value = errorMessage(caught, '仓位管理状态读取失败')
    } finally {
      if (controller === riskController) riskController = null
      if (alive) riskRefreshing.value = false
    }
  }

  async function loadHoldings() {
    if (refreshing.value) return
    holdingsController?.abort()
    const controller = new AbortController()
    holdingsController = controller
    refreshing.value = true
    if (!loaded.value) loading.value = true
    try {
      const payload = await getJson<HoldingsResponse>('/holdings', controller.signal)
      if (!alive || controller !== holdingsController) return
      if (payload.ok === false) throw new ApiError(payload.error || '持仓账本返回不可用状态')
      holdings.value = Array.isArray(payload.holdings) ? payload.holdings.map(toDraft) : []
      riskStates.value = payload.risk_states && typeof payload.risk_states === 'object' ? payload.risk_states : {}
      quotes.value = {}
      missingQuoteCodes.value = []
      holdingsError.value = ''
      quotesError.value = ''
      saveError.value = ''
      feedback.value = ''
      loaded.value = true
      dirty.value = false
      await refreshQuotes()
      await refreshRiskStates()
    } catch (caught) {
      if (!alive || controller !== holdingsController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      holdingsError.value = errorMessage(caught, '持仓账本读取失败')
      loaded.value = false
    } finally {
      if (controller === holdingsController) holdingsController = null
      if (alive) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  async function refresh() {
    if (dirty.value) {
      feedback.value = '检测到未保存编辑，本次只刷新报价，没有重新载入持仓账本。'
      await refreshQuotes()
      return
    }
    await loadHoldings()
  }

  function validateForSave(): string {
    if (!loaded.value) return '持仓账本尚未成功加载，保存已锁定。'
    const seen = new Set<string>()
    for (const [index, row] of holdings.value.entries()) {
      const position = index + 1
      if (!/^\d{6}$/.test(row.code)) return `第 ${position} 条持仓的股票代码必须是 6 位数字。`
      if (seen.has(row.code)) return `股票代码 ${row.code} 重复，请合并后再保存。`
      if (row.cost == null || row.cost <= 0) return `第 ${position} 条持仓的成本价必须大于 0。`
      if (row.shares == null || row.shares <= 0 || !Number.isInteger(row.shares)) return `第 ${position} 条持仓的股数必须是大于 0 的整数。`
      seen.add(row.code)
    }
    return ''
  }

  async function save() {
    if (saving.value) return
    const validationError = validateForSave()
    if (validationError) {
      saveError.value = validationError
      return
    }
    saveController?.abort()
    const controller = new AbortController()
    saveController = controller
    saving.value = true
    saveError.value = ''
    feedback.value = ''
    const payloadRows: SaveHoldingPayload[] = holdings.value.map((row) => {
      const item: SaveHoldingPayload = {
        code: row.code,
        name: row.name || quotes.value[row.code]?.name || '',
        cost: row.cost as number,
        shares: row.shares as number,
        note: row.note,
        weight_pct: null,
      }
      if (row.riskPlan) item.risk_plan = { ...row.riskPlan }
      const currentMarketValue = holdingView(row, quotes.value[row.code] || null).marketValue
      const preservedMarketValue = currentMarketValue != null ? currentMarketValue : row.storedMarketValue
      if (preservedMarketValue != null && preservedMarketValue > 0) item.market_value = preservedMarketValue
      return item
    })
    try {
      const payload = await putJson<HoldingsResponse>('/holdings', {
        holdings: payloadRows,
      }, controller.signal)
      if (!alive || controller !== saveController) return
      if (payload.ok === false) throw new ApiError(payload.error || '服务端拒绝保存持仓')
      const returned = Array.isArray(payload.holdings) ? payload.holdings : payloadRows
      holdings.value = returned.map(toDraft)
      riskStates.value = payload.risk_states && typeof payload.risk_states === 'object' ? payload.risk_states : riskStates.value
      dirty.value = false
      feedback.value = '持仓已保存；行业以服务端核验结果为准，页面汇总已自动更新。'
    } catch (caught) {
      if (!alive || controller !== saveController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      saveError.value = errorMessage(caught, '持仓保存失败')
    } finally {
      if (controller === saveController) saveController = null
      if (alive) saving.value = false
    }
  }

  async function addHolding(input: NewHoldingInput): Promise<boolean> {
    if (adding.value) return false
    if (holdings.value.some((row) => row.code === input.code)) {
      addError.value = '该股票已经在持仓中，请编辑原记录。'
      return false
    }
    verifyController?.abort()
    const controller = new AbortController()
    verifyController = controller
    adding.value = true
    addError.value = ''
    let quote: HoldingQuote | null = null
    let name = ''
    try {
      const payload = await getJson<QuoteResponse>(`/quote/${input.code}`, controller.signal)
      if (!alive || controller !== verifyController) return false
      if (payload.recognized === false || payload.state === 'not_found') {
        addError.value = payload.error || '该代码不在当前完整 A 股名录中。'
        return false
      }
      quote = payload.quote || null
      name = String(payload.quote?.name || payload.identity?.name || '')
    } catch (caught) {
      if (!alive || controller !== verifyController || (caught instanceof DOMException && caught.name === 'AbortError')) return false
      addError.value = `${errorMessage(caught, '股票代码核验失败')}；当前未加入列表，请重试。`
      return false
    } finally {
      if (controller === verifyController) verifyController = null
      if (alive) adding.value = false
    }
    holdings.value = [...holdings.value, toDraft({
      code: input.code,
      name,
      cost: input.cost,
      shares: input.shares,
      note: input.note,
      risk_plan: {
        period: input.period,
        buy_date: input.buyDate,
        customer_risk_price: input.customerRiskPrice,
        customer_target_price: input.customerTargetPrice,
        target_mode: input.targetMode,
        confirmed_reduction_pct: 0,
      },
    })]
    if (quote) quotes.value = { ...quotes.value, [input.code]: quote }
    markDirty()
    feedback.value = quote ? '已加入待保存持仓，并取得当前报价。' : '已加入待保存持仓；当前报价缺失，不计算盈亏。'
    return true
  }

  async function resetPlan(code: string, input: NewHoldingInput, reason = 'manual_replan'): Promise<boolean> {
    if (actionRunning.value) return false
    actionController?.abort()
    const controller = new AbortController()
    actionController = controller
    actionRunning.value = true
    saveError.value = ''
    try {
      const payload = await postJson<HoldingActionResponse>(`/holdings/${encodeURIComponent(code)}/risk-plan/reset`, {
        cost: input.cost,
        shares: input.shares,
        buy_date: input.buyDate,
        period: input.period,
        customer_risk_price: input.customerRiskPrice,
        customer_target_price: input.customerTargetPrice,
        target_mode: input.targetMode,
        reason,
      }, controller.signal)
      if (!alive || controller !== actionController) return false
      if (payload.ok === false) throw new ApiError(payload.error || '重置计划失败')
      await loadHoldings()
      feedback.value = '新计划已建立；成本、日期、1R、最高价和累计降低目标均已重置。'
      return true
    } catch (caught) {
      if (!alive || controller !== actionController || (caught instanceof DOMException && caught.name === 'AbortError')) return false
      saveError.value = errorMessage(caught, '重置计划失败')
      return false
    } finally {
      if (controller === actionController) actionController = null
      if (alive) actionRunning.value = false
    }
  }

  async function confirmAdjustment(code: string, reductionPct: number): Promise<boolean> {
    if (actionRunning.value) return false
    actionController?.abort()
    const controller = new AbortController()
    actionController = controller
    actionRunning.value = true
    saveError.value = ''
    try {
      const payload = await postJson<HoldingActionResponse>(`/holdings/${encodeURIComponent(code)}/adjustment`, {
        reduction_pct: reductionPct,
      }, controller.signal)
      if (!alive || controller !== actionController) return false
      if (payload.ok === false) throw new ApiError(payload.error || '确认仓位调整失败')
      holdings.value = Array.isArray(payload.holdings) ? payload.holdings.map(toDraft) : holdings.value
      riskStates.value = payload.states && typeof payload.states === 'object' ? payload.states : riskStates.value
      dirty.value = false
      feedback.value = payload.ended
        ? '已确认累计降低100%，该持仓已结束关注并保留历史事件。'
        : `已确认累计降低${reductionPct}%，继续监控；历史目标不会回退。`
      await refreshQuotes()
      return true
    } catch (caught) {
      if (!alive || controller !== actionController || (caught instanceof DOMException && caught.name === 'AbortError')) return false
      saveError.value = errorMessage(caught, '确认仓位调整失败')
      return false
    } finally {
      if (controller === actionController) actionController = null
      if (alive) actionRunning.value = false
    }
  }

  function beforeUnload(event: BeforeUnloadEvent) {
    if (!dirty.value) return
    event.preventDefault()
    event.returnValue = ''
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') {
      void refreshQuotes()
      void refreshRiskStates()
    }
  }

  onMounted(() => {
    alive = true
    void loadHoldings()
    quoteTimer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void refreshQuotes()
        void refreshRiskStates()
      }
    }, 30_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('beforeunload', beforeUnload)
  })

  onBeforeUnmount(() => {
    alive = false
    holdingsController?.abort()
    quotesController?.abort()
    saveController?.abort()
    verifyController?.abort()
    riskController?.abort()
    actionController?.abort()
    if (quoteTimer) clearInterval(quoteTimer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
    window.removeEventListener('beforeunload', beforeUnload)
  })

  return {
    holdings,
    views,
    missingQuoteCodes,
    loading,
    refreshing,
    quotesLoading,
    saving,
    adding,
    riskRefreshing,
    actionRunning,
    loaded,
    dirty,
    holdingsError,
    quotesError,
    saveError,
    addError,
    riskError,
    feedback,
    quotedCount,
    quoteCoverageComplete,
    quoteAsOf,
    quoteSources,
    hasStaleQuotes,
    aggregate,
    updateHolding,
    removeHolding,
    refreshQuotes,
    refreshRiskStates,
    refresh,
    save,
    addHolding,
    resetPlan,
    confirmAdjustment,
  }
}
