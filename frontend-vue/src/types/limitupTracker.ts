export type TrackerMetricState = 'pending' | 'intraday' | 'final' | 'missing' | 'unavailable' | 'not_applicable'
export type TrackerRowState = 'pending' | 'intraday' | 'final' | 'missing' | 'unavailable'

export interface LimitUpTrackerDateItem {
  date: string
  snapshot_state: 'missing' | 'intraday' | 'tracking' | 'complete'
  has_snapshot: boolean
  missing_label?: string | null
}

export interface LimitUpTrackerDatesResponse {
  ok: boolean
  dates: LimitUpTrackerDateItem[]
  default_date?: string | null
  calendar?: { state?: string; source?: string }
  generated_at?: string
  missing?: string[]
}

export interface LimitUpTrackerMetric {
  value: number | null
  state: TrackerMetricState
  label: string
  sample_size: number
  total_size: number
  coverage: number | null
  source?: string | null
  frozen_at?: string | null
}

export interface LimitUpTrackerRowResult {
  date: string | null
  change_pct: number | null
  state: TrackerRowState
  label: string
  source?: string | null
  market_time?: string | null
}

export interface LimitUpTrackerStock {
  code: string
  name: string
  industry?: string | null
  lianban?: number | null
  lianban_label: string
  baseline_change_pct?: number | null
  first_limit_time?: string | null
  source?: string | null
  position: number
  t1: LimitUpTrackerRowResult
  t2: LimitUpTrackerRowResult
}

export interface LimitUpTrackerSnapshot {
  ok: boolean
  selected_date: string
  baseline_date: string
  baseline_count: number | null
  tracking_status: string
  t1_date: string | null
  t2_date: string | null
  metrics: {
    t1_average: LimitUpTrackerMetric
    t1_red_rate: LimitUpTrackerMetric
    t1_promotion_rate: LimitUpTrackerMetric
    t2_average: LimitUpTrackerMetric
    t2_red_rate: LimitUpTrackerMetric
  }
  stocks: LimitUpTrackerStock[]
  generated_at?: string | null
  market_time?: string | null
  data_date?: string | null
  sources?: string[]
  is_intraday: boolean
  is_final: boolean
  missing?: string[]
  degraded_reasons?: string[]
  coverage?: Record<string, { available: number; total: number } | null>
  error?: string
}

export type LimitUpBackfillStatus = 'running' | 'complete' | 'partial' | 'failed' | 'noop'

export interface LimitUpBackfillResponse {
  ok: boolean
  job_id: string
  status: LimitUpBackfillStatus
  already_running?: boolean
  message?: string
  reason?: string | null
  started_at?: string
  finished_at?: string
  summary?: { completed: number; partial: number; failed: number; noop: number }
}
