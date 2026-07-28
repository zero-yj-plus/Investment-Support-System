import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ApiError, deleteJson, getJson, postJson, putJson } from '../services/api'
import type {
  MatchMode,
  TechnicalCacheState,
  TechnicalCatalogResponse,
  TechnicalCondition,
  TechnicalHistoryEvent,
  TechnicalHistoryResponse,
  TechnicalOccurrence,
  TechnicalPreset,
  TechnicalQueryRequest,
  TechnicalRuleConfig,
  TechnicalRuleGroup,
  TechnicalScheme,
  TechnicalSchemesResponse,
  TechnicalScreenResponse,
  TechnicalSignalDefinition,
  TechnicalStatusResponse,
  TechnicalTimeframe,
  TechnicalUniverse,
} from '../types/technicalScreener'

const DEFAULT_UNIVERSE: TechnicalUniverse = {
  markets: ['MAIN', 'CHINEXT', 'STAR'],
  exclude_st: true,
  exclude_suspended: true,
  min_listing_days: 120,
  min_amount_ma20: 50_000_000,
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function makeId(prefix: string): string {
  return `${prefix}_${globalThis.crypto?.randomUUID?.() || `${Date.now()}_${Math.random().toString(16).slice(2)}`}`
}

function makeCondition(signalCode = 'MACD_GOLDEN_CROSS', extra: Partial<TechnicalCondition> = {}): TechnicalCondition {
  return {
    id: makeId('condition'),
    signal_code: signalCode,
    timeframe: 'D',
    occurrence: 'TODAY',
    params: signalCode === 'MACD_GOLDEN_CROSS' ? { fast: 12, slow: 26, signal: 9 } : {},
    ...extra,
  }
}

function makeGroup(kind: 'satisfy' | 'exclude', conditions: TechnicalCondition[] = [], conditionMatch?: MatchMode): TechnicalRuleGroup {
  return {
    id: makeId(kind === 'satisfy' ? 'satisfy' : 'exclude'),
    conditionMatch: conditionMatch || (kind === 'exclude' ? 'ANY' : 'ALL'),
    conditions,
  }
}

function makePreset(id: string, name: string, summary: string, conditionMatch: MatchMode, conditions: TechnicalCondition[]): TechnicalPreset {
  return {
    id,
    name,
    summary,
    config: {
      schemaVersion: 2,
      satisfy: { groupMatch: 'ALL', groups: [makeGroup('satisfy', conditions, conditionMatch)] },
      exclude: { groups: [] },
    },
  }
}

const TECHNICAL_PRESETS: TechnicalPreset[] = [
  makePreset('strong_macd', '强势MACD金叉', '日线MACD今日金叉、价格位于MA60上方且ADX不低于20。', 'ALL', [
    makeCondition(),
    makeCondition('CLOSE_ABOVE_MA', { occurrence: 'CURRENT', params: { period: 60 } }),
    makeCondition('ADX_STRONG', { occurrence: 'CURRENT', params: { threshold: 20 } }),
  ]),
  makePreset('ma_start', '均线多头启动', '刚形成均线多头排列，且成交量不低于20日均量。', 'ALL', [
    makeCondition('LONG_MA_ARRAY_START'),
    makeCondition('VOLUME_AT_LEAST_AVERAGE', { occurrence: 'CURRENT', params: { period: 20 } }),
  ]),
  makePreset('volume_breakout', '放量突破', '突破此前20日高点，成交量达到20日均量1.5倍。', 'ALL', [
    makeCondition('VOLUME_BREAKOUT', { params: { period: 20, volume_period: 20, multiplier: 1.5 } }),
  ]),
  makePreset('oversold', '超卖修复', 'RSI向上穿越30，并重新站上MA5。', 'ALL', [
    makeCondition('RSI_OVERSOLD_RECOVERY', { params: { period: 14, threshold: 30 } }),
    makeCondition('CLOSE_ABOVE_MA', { occurrence: 'CURRENT', params: { period: 5 } }),
  ]),
  makePreset('weekly_turn', '周线趋势转强', '确认完成的周线出现MACD金叉或Supertrend转多。', 'ANY', [
    makeCondition('MACD_GOLDEN_CROSS', { timeframe: 'W', params: { fast: 12, slow: 26, signal: 9 } }),
    makeCondition('SUPERTREND_BULLISH_FLIP', { timeframe: 'W', params: { period: 14, multiplier: 3 } }),
  ]),
  makePreset('resonance', '日周共振', '日线MACD刚转强，同时周线DIF仍位于DEA上方。', 'ALL', [
    makeCondition(),
    makeCondition('MACD_BULLISH_STATE', { timeframe: 'W', occurrence: 'CURRENT', params: { fast: 12, slow: 26, signal: 9 } }),
  ]),
]

function defaultRules(): TechnicalRuleConfig {
  return clone(TECHNICAL_PRESETS[0].config)
}

function readableError(error: unknown, fallback: string): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : fallback
}

export function useTechnicalScreener() {
  const catalog = ref<TechnicalCatalogResponse | null>(null)
  const status = ref<TechnicalStatusResponse | null>(null)
  const schemes = ref<TechnicalScheme[]>([])
  const result = ref<TechnicalScreenResponse | null>(null)
  const bootstrapLoading = ref(true)
  const catalogError = ref('')
  const statusError = ref('')
  const schemesError = ref('')
  const queryError = ref('')
  const queryLoading = ref(false)
  const hasSubmitted = ref(false)
  const resultStale = ref(false)
  const refreshSubmitting = ref(false)
  const refreshQueued = ref(false)
  const refreshFeedback = ref('')
  const schemeBusy = ref(false)
  const schemeFeedback = ref('')
  const selectedSchemeId = ref('')
  const selectedPresetId = ref(TECHNICAL_PRESETS[0].id)
  const schemeName = ref(TECHNICAL_PRESETS[0].name)
  const presetReadOnly = ref(true)
  const legacyReadOnly = ref(false)
  const legacyReason = ref('')
  const legacyPayload = ref<Record<string, unknown> | null>(null)
  const ruleConfig = reactive<TechnicalRuleConfig>(defaultRules())
  const universe = reactive<TechnicalUniverse>(clone(DEFAULT_UNIVERSE))
  const minimumAmountWan = ref(String(DEFAULT_UNIVERSE.min_amount_ma20 / 10_000))
  const defaultTimeframe = ref<TechnicalTimeframe>('D')
  const sortField = ref('signal_score')
  const sortDirection = ref<'asc' | 'desc'>('desc')
  const historyCode = ref('')
  const historyEvents = ref<TechnicalHistoryEvent[]>([])
  const historyLoading = ref(false)
  const historyRequested = ref(false)
  const historyError = ref('')

  let alive = false
  let lifecycleGeneration = 0
  let catalogController: AbortController | null = null
  let statusController: AbortController | null = null
  let schemesController: AbortController | null = null
  let queryController: AbortController | null = null
  let refreshController: AbortController | null = null
  let schemeController: AbortController | null = null
  let historyController: AbortController | null = null
  let statusPollTimer: ReturnType<typeof setInterval> | null = null

  const stocks = computed(() => Array.isArray(result.value?.stocks) ? result.value.stocks : [])
  const busy = computed(() => bootstrapLoading.value || queryLoading.value || refreshSubmitting.value || schemeBusy.value || historyLoading.value)
  const definitions = computed(() => Array.isArray(catalog.value?.signals) ? catalog.value!.signals : [])
  const availableSignalCount = computed(() => definitions.value.filter((item) => item.available).length)
  const conflicts = computed(() => {
    const errors: string[] = []
    if (!universe.markets.length) errors.push('股票池至少保留一个市场')
    if (!Number.isFinite(Number(universe.min_listing_days)) || Number(universe.min_listing_days) < 0) errors.push('上市天数必须是非负数')
    if (!Number.isFinite(Number(minimumAmountWan.value)) || Number(minimumAmountWan.value) < 0) errors.push('20 日均成交额必须是非负数')
    if (!legacyReadOnly.value) {
      if (!ruleConfig.satisfy.groups.length) errors.push('请至少添加一组满足规则')
      for (const [kind, groups] of [['满足', ruleConfig.satisfy.groups], ['排除', ruleConfig.exclude.groups]] as const) {
        groups.forEach((group, index) => {
          if (!group.conditions.length) errors.push(`${kind}规则 ${index + 1} 尚未添加技术信号`)
        })
      }
      const required = new Set(ruleConfig.satisfy.groups.flatMap((group) => group.conditions.map((item) => `${item.signal_code}:${item.timeframe}`)))
      const excluded = new Set(ruleConfig.exclude.groups.flatMap((group) => group.conditions.map((item) => `${item.signal_code}:${item.timeframe}`)))
      required.forEach((key) => { if (excluded.has(key)) errors.push(`同一周期的 ${key.split(':')[0]} 同时出现在满足和排除规则`) })
    }
    return errors
  })

  function definition(code: string): TechnicalSignalDefinition | undefined {
    return definitions.value.find((item) => item.signal_code === code)
  }

  function replaceRules(next: TechnicalRuleConfig) {
    ruleConfig.schemaVersion = 2
    ruleConfig.satisfy = clone(next.satisfy)
    ruleConfig.exclude = clone(next.exclude)
  }

  function markDirty() {
    if (result.value) resultStale.value = true
    schemeFeedback.value = ''
  }

  function setConditionSignal(condition: TechnicalCondition, code: string) {
    const item = definition(code)
    condition.signal_code = code
    condition.occurrence = item?.signal_kind === 'state' ? 'CURRENT' : 'TODAY'
    condition.params = clone(item?.default_params || {})
    delete condition.recent_periods
    markDirty()
  }

  function addGroup(kind: 'satisfy' | 'exclude') {
    ruleConfig[kind].groups.push(makeGroup(kind))
    presetReadOnly.value = false
    markDirty()
  }

  function removeGroup(kind: 'satisfy' | 'exclude', index: number) {
    ruleConfig[kind].groups.splice(index, 1)
    presetReadOnly.value = false
    markDirty()
  }

  function addSignals(kind: 'satisfy' | 'exclude', groupIndex: number, codes: string[]) {
    const group = ruleConfig[kind].groups[groupIndex]
    if (!group) return
    for (const code of codes) {
      const item = definition(code)
      if (!item?.available || group.conditions.some((condition) => condition.signal_code === code && condition.timeframe === defaultTimeframe.value)) continue
      group.conditions.push({
        id: makeId('condition'),
        signal_code: code,
        timeframe: defaultTimeframe.value,
        occurrence: item.signal_kind === 'state' ? 'CURRENT' : 'TODAY',
        params: clone(item.default_params || {}),
      })
    }
    presetReadOnly.value = false
    markDirty()
  }

  function removeCondition(kind: 'satisfy' | 'exclude', groupIndex: number, conditionIndex: number) {
    ruleConfig[kind].groups[groupIndex]?.conditions.splice(conditionIndex, 1)
    presetReadOnly.value = false
    markDirty()
  }

  function restoreConditionDefaults(condition: TechnicalCondition) {
    condition.params = clone(definition(condition.signal_code)?.default_params || {})
    markDirty()
  }

  function setGroupMatch(kind: 'satisfy' | 'exclude', groupIndex: number, mode: MatchMode) {
    const group = ruleConfig[kind].groups[groupIndex]
    if (group) group.conditionMatch = mode
    markDirty()
  }

  function setOccurrence(condition: TechnicalCondition, occurrence: TechnicalOccurrence) {
    condition.occurrence = occurrence
    if (occurrence !== 'RECENT') delete condition.recent_periods
    markDirty()
  }

  function normalizeUniverse() {
    universe.min_listing_days = Math.max(0, Number(universe.min_listing_days) || 0)
    universe.min_amount_ma20 = Math.max(0, Number(minimumAmountWan.value) * 10_000)
  }

  function legacyRules(config: TechnicalRuleConfig) {
    const node = (group: TechnicalRuleGroup) => ({
      operator: group.conditionMatch === 'ANY' ? 'OR' : 'AND',
      conditions: group.conditions.map(({ id: _id, ...condition }) => clone(condition)),
    })
    return {
      must_all: config.satisfy.groupMatch === 'ALL' ? config.satisfy.groups.map(node) : [],
      any_of: config.satisfy.groupMatch === 'ANY' ? config.satisfy.groups.map(node) : [],
      exclude: config.exclude.groups.map(node),
    }
  }

  function buildPayload(forSave = false): Record<string, unknown> {
    normalizeUniverse()
    const common = {
      universe: clone(universe),
      default_timeframe: defaultTimeframe.value,
      sort: [{ field: sortField.value, direction: sortDirection.value }],
      algorithm_ver: catalog.value?.algorithm_ver || status.value?.algorithm_ver || 'local-signal-v1',
      adjustment: 'qfq',
      benchmark: '000300.SH',
      limit: 50,
      offset: 0,
    }
    if (legacyReadOnly.value && legacyPayload.value) return { ...clone(legacyPayload.value), ...common }
    const config = clone(ruleConfig)
    if ((status.value?.config_schema_version || catalog.value?.config_schema_version || 2) >= 2) return { ...common, ...config }
    const compatibility = legacyRules(config)
    return { ...common, ...compatibility, ...(forSave ? { editor_config: config } : {}) }
  }

  async function loadCatalog() {
    catalogController?.abort()
    const controller = new AbortController()
    catalogController = controller
    const generation = lifecycleGeneration
    try {
      const payload = await getJson<TechnicalCatalogResponse>('/technical-screen/catalog', controller.signal)
      if (!alive || controller !== catalogController || generation !== lifecycleGeneration) return false
      catalog.value = payload
      catalogError.value = ''
      const current = ruleConfig.satisfy.groups[0]?.conditions[0]
      if (current?.signal_code === 'MACD_GOLDEN_CROSS') current.params = clone(definition('MACD_GOLDEN_CROSS')?.default_params || current.params)
      return true
    } catch (error) {
      if (!alive || controller !== catalogController || generation !== lifecycleGeneration || (error instanceof DOMException && error.name === 'AbortError')) return false
      catalogError.value = readableError(error, '技术信号目录读取失败')
      return false
    } finally {
      if (controller === catalogController) catalogController = null
    }
  }

  function stopStatusPolling() {
    if (statusPollTimer) clearInterval(statusPollTimer)
    statusPollTimer = null
  }

  function ensureStatusPolling() {
    if (statusPollTimer) return
    statusPollTimer = setInterval(() => {
      if (document.visibilityState === 'visible') void loadStatus()
    }, 5_000)
  }

  async function loadStatus() {
    statusController?.abort()
    const controller = new AbortController()
    statusController = controller
    const generation = lifecycleGeneration
    try {
      const payload = await getJson<TechnicalStatusResponse>('/technical-screen/status', controller.signal)
      if (!alive || controller !== statusController || generation !== lifecycleGeneration) return false
      status.value = payload
      statusError.value = ''
      if (payload.state === 'running' || payload.state === 'queued') ensureStatusPolling()
      if (refreshQueued.value && (payload.state === 'ready' || payload.state === 'partial')) {
        refreshQueued.value = false
        refreshFeedback.value = payload.state === 'ready' ? '后台刷新完成，缓存已可用。' : '后台刷新完成，但缓存只有部分覆盖。'
        stopStatusPolling()
      }
      return true
    } catch (error) {
      if (!alive || controller !== statusController || generation !== lifecycleGeneration || (error instanceof DOMException && error.name === 'AbortError')) return false
      statusError.value = readableError(error, '缓存状态读取失败')
      return false
    } finally {
      if (controller === statusController) statusController = null
    }
  }

  async function loadSchemes() {
    schemesController?.abort()
    const controller = new AbortController()
    schemesController = controller
    const generation = lifecycleGeneration
    try {
      const payload = await getJson<TechnicalSchemesResponse>('/technical-screen/schemes', controller.signal)
      if (!alive || controller !== schemesController || generation !== lifecycleGeneration) return false
      schemes.value = Array.isArray(payload.schemes) ? payload.schemes : []
      schemesError.value = ''
      return true
    } catch (error) {
      if (!alive || controller !== schemesController || generation !== lifecycleGeneration || (error instanceof DOMException && error.name === 'AbortError')) return false
      schemesError.value = readableError(error, '保存方案读取失败')
      return false
    } finally {
      if (controller === schemesController) schemesController = null
    }
  }

  async function refresh() {
    if (bootstrapLoading.value) return
    bootstrapLoading.value = true
    await Promise.allSettled([loadCatalog(), loadStatus(), loadSchemes()])
    if (alive) bootstrapLoading.value = false
  }

  async function runScreen() {
    if (queryLoading.value || conflicts.value.length || !status.value?.stocks) return false
    queryController?.abort()
    const controller = new AbortController()
    queryController = controller
    const generation = lifecycleGeneration
    queryLoading.value = true
    queryError.value = ''
    hasSubmitted.value = true
    try {
      const payload = await postJson<TechnicalScreenResponse>('/technical-screen/query', buildPayload(), controller.signal)
      if (!alive || controller !== queryController || generation !== lifecycleGeneration) return false
      if (!Array.isArray(payload.stocks)) throw new Error('技术选股接口未返回可识别的 stocks 数组')
      result.value = payload
      resultStale.value = false
      if (payload.ok === false) queryError.value = payload.error || payload.errors?.map((item) => item.message).filter(Boolean).join('；') || payload.missing?.join('；') || '本次筛选不可用'
      return payload.ok !== false
    } catch (error) {
      if (!alive || controller !== queryController || generation !== lifecycleGeneration || (error instanceof DOMException && error.name === 'AbortError')) return false
      queryError.value = `${readableError(error, '技术选股请求失败')}；已有成功结果仍保留。`
      return false
    } finally {
      if (controller === queryController) queryController = null
      if (alive && generation === lifecycleGeneration) queryLoading.value = false
    }
  }

  function stopQuery() {
    queryController?.abort()
    queryError.value = '本次筛选已停止；已有成功结果仍保留。'
  }

  async function submitRefresh() {
    if (refreshSubmitting.value || refreshQueued.value || status.value?.state === 'running') return false
    refreshController?.abort()
    const controller = new AbortController()
    refreshController = controller
    const generation = lifecycleGeneration
    refreshSubmitting.value = true
    refreshFeedback.value = ''
    try {
      const payload = await postJson<{ ok: boolean; state: TechnicalCacheState; message?: string }>('/technical-screen/refresh', {}, controller.signal)
      if (!alive || controller !== refreshController || generation !== lifecycleGeneration) return false
      refreshQueued.value = payload.state === 'queued' || payload.state === 'running'
      refreshFeedback.value = payload.state === 'queued' ? '刷新已提交，平台已接受并进入后台队列；数据尚未更新。' : (payload.message || '刷新状态已更新。')
      if (status.value) status.value = { ...status.value, state: payload.state }
      ensureStatusPolling()
      return payload.ok !== false
    } catch (error) {
      if (!alive || controller !== refreshController || generation !== lifecycleGeneration || (error instanceof DOMException && error.name === 'AbortError')) return false
      refreshFeedback.value = readableError(error, '刷新任务提交失败')
      return false
    } finally {
      if (controller === refreshController) refreshController = null
      if (alive && generation === lifecycleGeneration) refreshSubmitting.value = false
    }
  }

  function loadPreset(id = TECHNICAL_PRESETS[0].id) {
    const preset = TECHNICAL_PRESETS.find((item) => item.id === id) || TECHNICAL_PRESETS[0]
    selectedSchemeId.value = ''
    selectedPresetId.value = preset.id
    schemeName.value = preset.name
    presetReadOnly.value = true
    legacyReadOnly.value = false
    legacyReason.value = ''
    legacyPayload.value = null
    replaceRules(preset.config)
    Object.assign(universe, clone(DEFAULT_UNIVERSE))
    minimumAmountWan.value = String(DEFAULT_UNIVERSE.min_amount_ma20 / 10_000)
    defaultTimeframe.value = 'D'
    sortField.value = 'signal_score'
    sortDirection.value = 'desc'
    markDirty()
  }

  function loadScheme(id: string) {
    if (id.startsWith('preset:')) {
      loadPreset(id.slice('preset:'.length))
      return
    }
    const schemeId = id.startsWith('scheme:') ? id.slice('scheme:'.length) : id
    const item = schemes.value.find((scheme) => scheme.id === schemeId)
    if (!item) return
    selectedSchemeId.value = item.id
    selectedPresetId.value = ''
    schemeName.value = item.name
    presetReadOnly.value = false
    legacyReadOnly.value = item.editable === false || item.schemaVersion !== 2 || !item.satisfy || !item.exclude
    legacyReason.value = legacyReadOnly.value ? (item.migration_reason || '旧方案无法无损转换为当前两层规则编辑器') : ''
    legacyPayload.value = legacyReadOnly.value ? clone(item as unknown as Record<string, unknown>) : null
    if (!legacyReadOnly.value) replaceRules({ schemaVersion: 2, satisfy: clone(item.satisfy!), exclude: clone(item.exclude!) })
    Object.assign(universe, clone(DEFAULT_UNIVERSE), clone(item.universe || {}))
    minimumAmountWan.value = String(Number(universe.min_amount_ma20 || 0) / 10_000)
    defaultTimeframe.value = item.default_timeframe || 'D'
    sortField.value = item.sort?.[0]?.field || 'signal_score'
    sortDirection.value = item.sort?.[0]?.direction || 'desc'
    schemeFeedback.value = item.algorithm_ver && catalog.value?.algorithm_ver && item.algorithm_ver !== catalog.value.algorithm_ver
      ? `该方案使用旧算法版本 ${item.algorithm_ver}，页面未静默改写。`
      : ''
    markDirty()
  }

  function copyToDraft() {
    selectedSchemeId.value = ''
    selectedPresetId.value = ''
    schemeName.value = `${schemeName.value || '技术选股方案'} 副本`
    presetReadOnly.value = false
    if (legacyReadOnly.value) {
      replaceRules({ schemaVersion: 2, satisfy: { groupMatch: 'ALL', groups: [makeGroup('satisfy')] }, exclude: { groups: [] } })
      legacyReadOnly.value = false
      legacyPayload.value = null
      legacyReason.value = ''
      schemeFeedback.value = '已创建空白重建草稿；原旧方案和原筛选逻辑未被修改。'
    } else {
      replaceRules(clone(ruleConfig))
      schemeFeedback.value = '已创建可编辑副本；保存后才会写入方案列表。'
    }
    markDirty()
  }

  async function saveScheme() {
    if (schemeBusy.value || presetReadOnly.value || legacyReadOnly.value || conflicts.value.length) return false
    const name = schemeName.value.trim()
    if (!name) {
      schemeFeedback.value = '请填写方案名称。'
      return false
    }
    schemeController?.abort()
    const controller = new AbortController()
    schemeController = controller
    const generation = lifecycleGeneration
    schemeBusy.value = true
    schemeFeedback.value = ''
    const payload = { ...buildPayload(true), name }
    try {
      const saved = selectedSchemeId.value
        ? await putJson<TechnicalScheme & { ok: boolean }>(`/technical-screen/schemes/${encodeURIComponent(selectedSchemeId.value)}`, payload, controller.signal)
        : await postJson<TechnicalScheme & { ok: boolean }>('/technical-screen/schemes', payload, controller.signal)
      if (!alive || controller !== schemeController || generation !== lifecycleGeneration) return false
      selectedSchemeId.value = saved.id
      selectedPresetId.value = ''
      schemeName.value = saved.name || name
      schemeFeedback.value = '方案已保存。'
      await loadSchemes()
      return true
    } catch (error) {
      if (!alive || controller !== schemeController || generation !== lifecycleGeneration || (error instanceof DOMException && error.name === 'AbortError')) return false
      schemeFeedback.value = readableError(error, '方案保存失败')
      return false
    } finally {
      if (controller === schemeController) schemeController = null
      if (alive && generation === lifecycleGeneration) schemeBusy.value = false
    }
  }

  async function deleteScheme() {
    if (!selectedSchemeId.value || schemeBusy.value || !window.confirm('确认删除当前技术选股方案？')) return false
    schemeController?.abort()
    const controller = new AbortController()
    schemeController = controller
    const generation = lifecycleGeneration
    schemeBusy.value = true
    try {
      await deleteJson<{ ok: boolean }>(`/technical-screen/schemes/${encodeURIComponent(selectedSchemeId.value)}`, controller.signal)
      if (!alive || controller !== schemeController || generation !== lifecycleGeneration) return false
      await loadSchemes()
      loadPreset()
      schemeFeedback.value = '方案已删除。'
      return true
    } catch (error) {
      if (!alive || controller !== schemeController || generation !== lifecycleGeneration || (error instanceof DOMException && error.name === 'AbortError')) return false
      schemeFeedback.value = readableError(error, '方案删除失败')
      return false
    } finally {
      if (controller === schemeController) schemeController = null
      if (alive && generation === lifecycleGeneration) schemeBusy.value = false
    }
  }

  async function loadHistory() {
    const code = historyCode.value.trim()
    historyRequested.value = true
    if (!/^\d{6}$/.test(code)) {
      historyError.value = '请输入 6 位股票代码。'
      historyEvents.value = []
      return false
    }
    historyController?.abort()
    const controller = new AbortController()
    historyController = controller
    const generation = lifecycleGeneration
    historyLoading.value = true
    historyError.value = ''
    try {
      const payload = await getJson<TechnicalHistoryResponse>(`/technical-screen/history?code=${encodeURIComponent(code)}&limit=200`, controller.signal)
      if (!alive || controller !== historyController || generation !== lifecycleGeneration) return false
      historyEvents.value = Array.isArray(payload.events) ? payload.events : []
      return true
    } catch (error) {
      if (!alive || controller !== historyController || generation !== lifecycleGeneration || (error instanceof DOMException && error.name === 'AbortError')) return false
      historyError.value = readableError(error, '历史命中读取失败')
      historyEvents.value = []
      return false
    } finally {
      if (controller === historyController) historyController = null
      if (alive && generation === lifecycleGeneration) historyLoading.value = false
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible' && (refreshQueued.value || status.value?.state === 'running')) void loadStatus()
  }

  onMounted(async () => {
    alive = true
    lifecycleGeneration += 1
    document.addEventListener('visibilitychange', onVisibilityChange)
    await Promise.allSettled([loadCatalog(), loadStatus(), loadSchemes()])
    if (alive) bootstrapLoading.value = false
    if (status.value?.state === 'running') ensureStatusPolling()
  })

  onBeforeUnmount(() => {
    alive = false
    lifecycleGeneration += 1
    catalogController?.abort()
    statusController?.abort()
    schemesController?.abort()
    queryController?.abort()
    refreshController?.abort()
    schemeController?.abort()
    historyController?.abort()
    stopStatusPolling()
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return {
    catalog,
    status,
    schemes,
    presets: TECHNICAL_PRESETS,
    result,
    stocks,
    bootstrapLoading,
    catalogError,
    statusError,
    schemesError,
    queryError,
    queryLoading,
    hasSubmitted,
    resultStale,
    refreshSubmitting,
    refreshQueued,
    refreshFeedback,
    schemeBusy,
    schemeFeedback,
    selectedSchemeId,
    selectedPresetId,
    schemeName,
    presetReadOnly,
    legacyReadOnly,
    legacyReason,
    ruleConfig,
    universe,
    minimumAmountWan,
    defaultTimeframe,
    sortField,
    sortDirection,
    historyCode,
    historyEvents,
    historyLoading,
    historyRequested,
    historyError,
    definitions,
    availableSignalCount,
    conflicts,
    busy,
    definition,
    markDirty,
    setConditionSignal,
    addGroup,
    removeGroup,
    addSignals,
    removeCondition,
    restoreConditionDefaults,
    setGroupMatch,
    setOccurrence,
    loadScheme,
    copyToDraft,
    saveScheme,
    deleteScheme,
    runScreen,
    stopQuery,
    submitRefresh,
    loadHistory,
    refresh,
  }
}
