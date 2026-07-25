import type { ApiResponseState } from './dashboard'

export interface CapitalSeriesPoint {
  t?: string
  v?: number | null
}

export interface CapitalFlowRow {
  code?: string
  name?: string
  main_net?: number | null
  change_pct?: number | null
  rnk?: number | null
  source?: string
  value_unit?: string
  unit_verified?: boolean
  series?: CapitalSeriesPoint[]
}

export interface CapitalTrendsResponse extends ApiResponseState {
  days?: number
  snap_ts?: string
  today?: string
  sector_source?: string
  hot_source?: string
  sector_date?: string
  hot_date?: string
  stale_sector?: boolean
  stale_hot?: boolean
  last_good_sector?: boolean
  last_good_hot?: boolean
  view_state?: 'complete' | 'empty' | 'last_good' | string
  msg?: string
  sectors?: CapitalFlowRow[]
  hot_stocks?: CapitalFlowRow[]
}

export interface CapitalSnapshotResponse extends ApiResponseState {
  complete?: boolean
  sectors?: number | null
  hot_stocks?: number | null
  sector_source?: string
  hot_source?: string
  sector_date?: string
  hot_date?: string
  msg?: string
}

export interface CapitalActionFeedback {
  tone: 'success' | 'warning' | 'danger'
  message: string
}
