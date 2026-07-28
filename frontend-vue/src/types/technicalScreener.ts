export type TechnicalCacheState = 'missing' | 'queued' | 'running' | 'partial' | 'ready'
export type TechnicalTimeframe = 'D' | 'W' | 'M'
export type TechnicalOccurrence = 'TODAY' | 'RECENT' | 'CURRENT'
export type MatchMode = 'ALL' | 'ANY'

export interface TechnicalSignalDefinition {
  signal_code: string
  signal_name: string
  category: string
  signal_kind: 'event' | 'state'
  direction: 'bullish' | 'bearish' | string
  default_params?: Record<string, number | string | boolean>
  aliases?: string[]
  available: boolean
  unavailable_reason?: string | null
}

export interface TechnicalCatalogResponse {
  ok: boolean
  algorithm_ver: string
  config_schema_version: number
  timeframes: Array<{ code: TechnicalTimeframe; name: string }>
  occurrences: Array<{ code: TechnicalOccurrence; name: string }>
  signals: TechnicalSignalDefinition[]
}

export interface TechnicalRefreshProgress {
  state?: TechnicalCacheState
  processed?: number | null
  total?: number | null
  succeeded?: number | null
  failed?: number | null
  started_at?: string | null
  finished_at?: string | null
}

export interface TechnicalStatusResponse {
  ok: boolean
  state: TechnicalCacheState
  schema_version?: string
  config_schema_version: number
  algorithm_ver: string
  stocks: number | null
  bars: number | null
  signal_matches: number | null
  data_date: string | null
  adjustment: 'qfq' | string
  history_days?: number | null
  benchmark?: string | null
  benchmark_bars?: number | null
  level2_available?: boolean | null
  level2_reason?: string | null
  refresh?: TechnicalRefreshProgress | null
}

export interface TechnicalCondition {
  id: string
  signal_code: string
  timeframe: TechnicalTimeframe
  occurrence: TechnicalOccurrence
  recent_periods?: number
  include_partial_period?: boolean
  params: Record<string, number | string | boolean>
}

export interface TechnicalRuleGroup {
  id: string
  conditionMatch: MatchMode
  conditions: TechnicalCondition[]
}

export interface TechnicalRuleConfig {
  schemaVersion: 2
  satisfy: { groupMatch: MatchMode; groups: TechnicalRuleGroup[] }
  exclude: { groups: TechnicalRuleGroup[] }
}

export interface TechnicalPreset {
  id: string
  name: string
  summary: string
  config: TechnicalRuleConfig
}

export interface TechnicalUniverse {
  markets: string[]
  exclude_st: boolean
  exclude_suspended: boolean
  min_listing_days: number
  min_amount_ma20: number
}

export interface TechnicalQueryRequest extends TechnicalRuleConfig {
  universe: TechnicalUniverse
  default_timeframe: TechnicalTimeframe
  sort: Array<{ field: string; direction: 'asc' | 'desc' }>
  algorithm_ver: string
  adjustment: 'qfq'
  benchmark: '000300.SH'
  limit: number
  offset: number
}

export interface TechnicalMatchedReason {
  signal_code: string
  signal_name: string
  signal_kind?: 'event' | 'state'
  timeframe: TechnicalTimeframe
  date?: string | null
  metrics?: Record<string, unknown> | null
  is_confirmed?: boolean | null
}

export interface TechnicalStock {
  code: string
  name?: string | null
  industry?: string | null
  last_price?: number | null
  change_pct?: number | null
  last_amount?: number | null
  avg_amount20?: number | null
  signal_score?: number | null
  matched_reasons?: TechnicalMatchedReason[] | null
}

export interface TechnicalScreenResponse {
  ok: boolean
  state: 'invalid' | TechnicalCacheState
  error?: string
  errors?: Array<{ code?: string; message?: string; group?: string }>
  error_code?: string
  schema_version?: string
  config_schema_version?: number
  algorithm_ver?: string
  data_date?: string | null
  adjustment?: string
  matched?: number | null
  returned?: number | null
  offset?: number
  limit?: number
  stocks: TechnicalStock[]
  coverage?: { cached?: number | null; eligible?: number | null }
  strongest_condition?: string | null
  missing?: string[]
}

export interface TechnicalScheme extends Partial<TechnicalQueryRequest> {
  id: string
  name: string
  algorithm_ver?: string
  editable?: boolean
  migration_state?: string
  migration_reason?: string
  created_at?: string
  updated_at?: string
  must_all?: unknown[]
  any_of?: unknown[]
}

export interface TechnicalSchemesResponse {
  ok: boolean
  schemes: TechnicalScheme[]
}

export interface TechnicalHistoryEvent {
  code: string
  signal_code: string
  signal_name: string
  timeframe: TechnicalTimeframe
  event_date: string
  metrics?: Record<string, unknown> | null
  is_confirmed?: boolean
}

export interface TechnicalHistoryResponse {
  ok: boolean
  events: TechnicalHistoryEvent[]
  returned: number
  algorithm_ver: string
}
