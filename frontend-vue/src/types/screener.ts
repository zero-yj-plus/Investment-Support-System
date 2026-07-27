export type ScreenerSortField = 'amount' | 'turnover_rate' | 'volume_ratio' | 'change_pct' | 'circ_mv' | 'pe_ttm' | 'pb'

export interface ScreenerFilterForm {
  peMin: string
  peMax: string
  pbMin: string
  pbMax: string
  turnoverMin: string
  turnoverMax: string
  volumeRatioMin: string
  circMvMin: string
  circMvMax: string
  changeMin: string
  changeMax: string
  amountMin: string
  industry: string
  boards: string[]
  excludeSt: boolean
  excludeLoss: boolean
}

export interface ScreenerFilters {
  pe_min?: number
  pe_max?: number
  pb_min?: number
  pb_max?: number
  turnover_min?: number
  turnover_max?: number
  volume_ratio_min?: number
  circ_mv_min?: number
  circ_mv_max?: number
  chg_min?: number
  chg_max?: number
  amount_min?: number
  industry_kw?: string
  boards?: string[]
  exclude_st: boolean
  exclude_loss: boolean
}

export interface ScreenerCoverage {
  received?: number | null
  expected?: number | null
  ratio?: number | null
  percent?: number | null
  minimum_ratio?: number | null
  sufficient?: boolean | null
}

export interface ScreenerUniverse {
  count?: number | null
  complete?: boolean | null
  stale?: boolean | null
  as_of?: string | null
  refresh_required?: boolean | null
  missing_exchanges?: string[] | null
}

export interface ScreenerIndustryCoverage {
  filled?: number | null
  returned?: number | null
  queried?: number | null
  query_limit?: number | null
  percent?: number | null
}

export interface ScreenerAnalysisContract {
  version?: string | null
  current_stage?: string | null
  deterministic_rule_version?: string | null
  decision_authority?: string | null
  data_status?: string | null
  data_as_of?: string | null
  analysis_executed?: boolean | null
  ai?: {
    role?: string | null
    status?: string | null
    may_change_deterministic_action?: boolean | null
  } | null
}

export interface ScreenerStock {
  code?: string | null
  name?: string | null
  industry?: string | null
  board?: string | null
  price?: number | null
  change_pct?: number | null
  turnover_rate?: number | null
  volume_ratio?: number | null
  pe?: number | null
  pe_ttm?: number | null
  pb?: number | null
  total_mv?: number | null
  circ_mv?: number | null
  amount?: number | null
}

export interface ScreenerResponse {
  ok: boolean
  ts?: string | null
  trade_date?: string | null
  elapsed_ms?: number | null
  total_market?: number | null
  matched?: number | null
  returned?: number | null
  sort_by?: string | null
  coverage?: ScreenerCoverage | null
  universe?: ScreenerUniverse | null
  industry_coverage?: ScreenerIndustryCoverage | null
  analysis_contract?: ScreenerAnalysisContract | null
  missing?: string[] | null
  stocks?: ScreenerStock[] | null
  error?: string | null
}

export interface ScreenerRequest {
  filters: ScreenerFilters
  sort_by: ScreenerSortField
  sort_desc: boolean
  result_limit: number
}

export interface ScreenerWatchlistResponse {
  ok: boolean
  id?: string | null
  zone_status?: string | null
  error?: string | null
}

export type WatchlistActionState = 'adding' | 'queued' | 'added' | 'error'
