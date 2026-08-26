import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ApiError, getJson, postJson, putJson } from '../services/api'
import type {
  HoldingDraft,
  HoldingRecord,
  HoldingQuote,
  HoldingRiskCard,
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
    stopLossPrice: finiteNumber(row.stop_loss_price),
    takeProfitPrice: finiteNumber(row.take_profit_price),
    horizon: ['general', 'overnight', 'ultra', 'short', 'swing', 'trend'].includes(String(row.horizon))
      ? row.horizon as HoldingDraft['horizon'] : 'general',
    entryType: ['general', 'breakout', 'pullback', 'event'].includes(String(row.entry_type))
      ? row.entry_type as HoldingDraft['entryType'] : 'general',
    entryDate: String(row.entry_date || ''),
    note: String(row.note == null ? '' : row.note),
  }
}

export function useHoldings() {
  const holdings = ref<HoldingDraft[]>([])
  const quotes = ref<Record<string, HoldingQuote>>({})
  const riskCards = ref<Record<string, HoldingRiskCard>>({})
  const missingQuoteCodes = ref<string[]>([])
  const loading = ref(true)
  const refreshing = ref(false)
  const quotesLoading = ref(false)
  const riskLoading = ref(false)
  const saving = ref(false)
  const adding = ref(false)
  const loaded = ref(false)
  const dirty = ref(false)
  const holdingsError = ref('')
  const quotesError = ref('')
  const riskError = ref('')
  const saveError = ref('')
  const addError = ref('')
  const feedback = ref('')
  let holdingsController: AbortController | null = null
  let quotesController: AbortController | null = null
  let riskController: AbortController | null = null
  let saveController: AbortController | null = null
  let verifyController: AbortController | null = null
  let quoteTimer: ReturnType<typeof setInterval> | null = null
  let riskTimer: ReturnType<typeof setInterval> | null = null
  let monitorSaveTimer: ReturnType<typeof setTimeout> | null = null
  let monitorAutoSavePending = false
  let alive = false

  const baseViews = computed(() => holdings.value.map((row) => holdingView(
    row,
    quotes.value[row.code] || null,
    riskCards.value[row.code] || null,
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

  function updateHolding(
    clientKey: string,
    field: 'code' | 'cost' | 'shares' | 'stopLossPrice' | 'takeProfitPrice' | 'horizon' | 'entryType' | 'entryDate' | 'note',
    value: string,
  ) {
    const row = holdings.value.find((item) => item.clientKey === clientKey)
    if (!row) return
    if (field === 'code') {
      row.code = value.replace(/\D/g, '').slice(0, 6)
      row.name = ''
      row.industry = ''
    } else if (field === 'cost' || field === 'shares' || field === 'stopLossPrice' || field === 'takeProfitPrice') {
      row[field] = finiteNumber(value)
    } else if (field === 'horizon') {
      row.horizon = value as HoldingDraft['horizon']
    } else if (field === 'entryType') {
      row.entryType = value as HoldingDraft['entryType']
    } else if (field === 'entryDate') {
      row.entryDate = value.slice(0, 10)
    } else {
      row.note = value.slice(0, 200)
    }
    markDirty()
    if (field === 'horizon' || field === 'entryType' || field === 'entryDate') {
      scheduleMonitorSave()
    }
  }

  function scheduleMonitorSave() {
    if (monitorSaveTimer) clearTimeout(monitorSaveTimer)
    monitorAutoSavePending = true
    feedback.value = '监控方案已修改，正在自动保存并重新计算…'
    monitorSaveTimer = setTimeout(() => {
      monitorSaveTimer = null
      if (alive && dirty.value) void save()
    }, 650)
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

  async function refreshRisk(force = false) {
    if (!holdings.value.length || riskLoading.value) {
      if (!holdings.value.length) riskCards.value = {}
      return
    }
    riskController?.abort()
    const controller = new AbortController()
    riskController = controller
    riskLoading.value = true
    try {
      const payload = force
        ? await postJson<HoldingsRiskResponse>('/holdings/risk/refresh', {}, controller.signal)
        : await getJson<HoldingsRiskResponse>('/holdings/risk', controller.signal)
      if (!alive || controller !== riskController) return
      if (payload.ok === false) throw new ApiError(payload.error || '持仓算法风险卡暂不可用')
      riskCards.value = payload.rows && typeof payload.rows === 'object' ? payload.rows : {}
      riskError.value = ''
    } catch (caught) {
      if (!alive || controller !== riskController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      riskError.value = errorMessage(caught, '持仓算法风险卡读取失败')
    } finally {
      if (controller === riskController) riskController = null
      if (alive) riskLoading.value = false
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
      quotes.value = {}
      riskCards.value = {}
      missingQuoteCodes.value = []
      holdingsError.value = ''
      quotesError.value = ''
      riskError.value = ''
      saveError.value = ''
      feedback.value = ''
      loaded.value = true
      dirty.value = false
      await Promise.all([refreshQuotes(), refreshRisk()])
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
    if (holdings.value.length > 50) return '持仓最多 50 条，请先删除一条后再保存。'
    const seen = new Set<string>()
    for (const [index, row] of holdings.value.entries()) {
      const position = index + 1
      if (!/^\d{6}$/.test(row.code)) return `第 ${position} 条持仓的股票代码必须是 6 位数字。`
      if (seen.has(row.code)) return `股票代码 ${row.code} 重复，请合并后再保存。`
      if (row.cost == null || row.cost <= 0) return `第 ${position} 条持仓的成本价必须大于 0。`
      if (row.shares == null || row.shares <= 0 || !Number.isInteger(row.shares)) return `第 ${position} 条持仓的股数必须是大于 0 的整数。`
      if (row.stopLossPrice != null && row.stopLossPrice <= 0) return `第 ${position} 条持仓的止损价必须大于 0。`
      if (row.takeProfitPrice != null && row.takeProfitPrice <= 0) return `第 ${position} 条持仓的止盈价必须大于 0。`
      if (
        row.stopLossPrice != null
        && row.takeProfitPrice != null
        && row.stopLossPrice >= row.takeProfitPrice
      ) return `第 ${position} 条持仓的止损价必须低于止盈价。`
      seen.add(row.code)
    }
    return ''
  }

  async function save(): Promise<boolean> {
    if (saving.value) return false
    const automaticMonitorSave = monitorAutoSavePending
    monitorAutoSavePending = false
    if (monitorSaveTimer) {
      clearTimeout(monitorSaveTimer)
      monitorSaveTimer = null
    }
    const validationError = validateForSave()
    if (validationError) {
      saveError.value = validationError
      return false
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
        stop_loss_price: row.stopLossPrice,
        take_profit_price: row.takeProfitPrice,
        horizon: row.horizon,
        entry_type: row.entryType,
        entry_date: row.entryDate || null,
      }
      const currentMarketValue = holdingView(row, quotes.value[row.code] || null).marketValue
      const preservedMarketValue = currentMarketValue != null ? currentMarketValue : row.storedMarketValue
      if (preservedMarketValue != null && preservedMarketValue > 0) item.market_value = preservedMarketValue
      return item
    })
    try {
      const payload = await putJson<HoldingsResponse>('/holdings', {
        holdings: payloadRows,
      }, controller.signal)
      if (!alive || controller !== saveController) return false
      if (payload.ok === false) throw new ApiError(payload.error || '服务端拒绝保存持仓')
      const returned = Array.isArray(payload.holdings) ? payload.holdings : payloadRows
      holdings.value = returned.map(toDraft)
      dirty.value = false
      feedback.value = automaticMonitorSave
        ? '监控方案已生效，算法风险卡已经重新计算。'
        : '持仓已保存，页面汇总与算法风险卡已经更新。'
      await refreshRisk(true)
      return true
    } catch (caught) {
      if (!alive || controller !== saveController || (caught instanceof DOMException && caught.name === 'AbortError')) return false
      saveError.value = errorMessage(caught, '持仓保存失败')
      return false
    } finally {
      if (controller === saveController) saveController = null
      if (alive) saving.value = false
    }
  }

  async function addHolding(input: NewHoldingInput): Promise<boolean> {
    if (adding.value) return false
    if (holdings.value.length >= 50) {
      addError.value = '持仓最多 50 条，请先删除一条后再添加。'
      return false
    }
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
    const previousHoldings = holdings.value
    const previousQuotes = quotes.value
    const previousDirty = dirty.value
    holdings.value = [...holdings.value, toDraft({
      code: input.code,
      name,
      cost: input.cost,
      shares: input.shares,
      stop_loss_price: input.stopLossPrice,
      take_profit_price: input.takeProfitPrice,
      horizon: input.horizon,
      entry_type: input.entryType,
      entry_date: input.entryDate || null,
      note: input.note,
    })]
    if (quote) quotes.value = { ...quotes.value, [input.code]: quote }
    markDirty()
    feedback.value = quote ? '正在保存持仓并计算风险卡…' : '正在保存持仓；当前报价缺失，不计算盈亏。'
    const saved = await save()
    if (!saved) {
      holdings.value = previousHoldings
      quotes.value = previousQuotes
      dirty.value = previousDirty
      return false
    }
    return true
  }

  async function editHolding(code: string, input: NewHoldingInput): Promise<boolean> {
    const row = holdings.value.find((item) => item.code === code)
    if (!row || input.code !== code) {
      saveError.value = '未找到需要编辑的持仓，股票代码不能在编辑时更换。'
      return false
    }
    const previous = { ...row }
    const previousDirty = dirty.value
    row.cost = input.cost
    row.shares = input.shares
    row.stopLossPrice = input.stopLossPrice
    row.takeProfitPrice = input.takeProfitPrice
    row.horizon = input.horizon
    row.entryType = input.entryType
    row.entryDate = input.entryDate.slice(0, 10)
    row.note = input.note.slice(0, 200)
    markDirty()
    feedback.value = '正在保存修改并重新计算风险卡…'
    const saved = await save()
    if (!saved) {
      holdings.value = holdings.value.map((item) => item.code === code ? previous : item)
      dirty.value = previousDirty
      return false
    }
    return true
  }

  async function deleteHolding(code: string): Promise<boolean> {
    const previousHoldings = holdings.value
    const previousDirty = dirty.value
    if (!previousHoldings.some((row) => row.code === code)) {
      saveError.value = '未找到需要删除的持仓。'
      return false
    }
    holdings.value = previousHoldings.filter((row) => row.code !== code)
    markDirty()
    feedback.value = '正在删除持仓并更新组合统计…'
    const saved = await save()
    if (!saved) {
      holdings.value = previousHoldings
      dirty.value = previousDirty
      return false
    }
    quotes.value = Object.fromEntries(Object.entries(quotes.value).filter(([quoteCode]) => quoteCode !== code))
    return true
  }

  function beforeUnload(event: BeforeUnloadEvent) {
    if (!dirty.value) return
    event.preventDefault()
    event.returnValue = ''
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') {
      void refreshQuotes()
      void refreshRisk()
    }
  }

  onMounted(() => {
    alive = true
    void loadHoldings()
    quoteTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void refreshQuotes()
    }, 30_000)
    riskTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void refreshRisk()
    }, 60_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('beforeunload', beforeUnload)
  })

  onBeforeUnmount(() => {
    alive = false
    holdingsController?.abort()
    quotesController?.abort()
    riskController?.abort()
    saveController?.abort()
    verifyController?.abort()
    if (quoteTimer) clearInterval(quoteTimer)
    if (riskTimer) clearInterval(riskTimer)
    if (monitorSaveTimer) clearTimeout(monitorSaveTimer)
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
    riskLoading,
    saving,
    adding,
    loaded,
    dirty,
    holdingsError,
    quotesError,
    riskError,
    saveError,
    addError,
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
    refreshRisk,
    refresh,
    save,
    addHolding,
    editHolding,
    deleteHolding,
  }
}
