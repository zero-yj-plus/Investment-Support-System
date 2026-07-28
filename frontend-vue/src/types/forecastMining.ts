import type { ApiResponseState } from './dashboard'

export interface ForecastStats {
  '总数'?: number | null
  '今日新增'?: number | null
  '按类型'?: Record<string, number | null>
  '掘金池'?: number | null
  '陷阱池'?: number | null
}

export interface ForecastItem {
  code?: string
  name?: string
  ann_date?: string
  type?: string
  p_chg_min?: number | null
  p_chg_max?: number | null
  net_min?: number | null
  net_max?: number | null
  last_net?: number | null
  summary?: string
  reason?: string
  source?: string
  board?: string
  mandatory?: boolean | null
  layer?: string
  trap?: boolean | null
  trap_reason?: string
  nonrec_flag?: boolean | null
  is_new_today?: boolean | null
}

export interface ForecastMiningResponse extends ApiResponseState {
  data_ts?: string | null
  period?: string
  deadline?: string
  degraded?: boolean
  used_last_good?: boolean
  snapshot_age_seconds?: number | null
  source_error?: string
  stats?: ForecastStats
  items?: ForecastItem[]
  traps?: ForecastItem[]
}

export type ForecastPool = 'items' | 'traps'
