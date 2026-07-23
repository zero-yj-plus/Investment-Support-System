export type QualityState = 'complete' | 'degraded' | 'partial' | 'missing'

export interface ApiResponseState {
  ok?: boolean
  error?: string
  missing?: string[]
  missing_fields?: string[]
  degraded_factors?: string[]
  notes?: string[]
  state?: string
  quality_status?: string
  data_state?: string
  state_label?: string
  stale?: boolean
  note?: string
  ts?: string
  data_date?: string
}

export interface DecisionCommand {
  risk_level?: number | null
  level_name?: string
  trade_state?: string
  regime?: string
  today_action?: string
  level_text?: string
  position_cap?: string
  first_principle?: string
  action_label?: string
  decision_tradeable?: boolean
  baseline_data_date?: string
  change?: string
  '定调叙述'?: string
  '定格时点'?: string
}

export interface PulseResponse extends ApiResponseState {
  ok?: boolean
  command?: DecisionCommand
  regime?: string
  risk_level?: number | null
  risk_label?: string
  senti?: number | null
  senti_label?: string
  change?: string
  '定格时点'?: string
}

export interface ShellStatusResponse extends ApiResponseState {
  ok?: boolean
  is_trade_day?: boolean
  market_session?: 'non_trading' | 'premarket' | 'intraday' | 'tail' | 'review' | string
  market_context?: {
    now?: string
    is_trade_day?: boolean
    market_session?: string
    recommended_hash?: string
  }
}

export interface MarketIndex {
  name?: string
  code?: string
  price?: number | null
  change_pct?: number | null
}

export interface MarketOverviewResponse extends ApiResponseState {
  indexes?: MarketIndex[]
  up_count?: number | null
  down_count?: number | null
  flat_count?: number | null
  total_amount?: number | null
  limit_up?: number | null
  limit_down?: number | null
  zhaban_count?: number | null
  zhaban_rate?: number | null
  promote_rate?: number | null
  lianban_height?: number | null
  limit_up_amount?: number | null
  zhaban_stale_date?: string | null
  zhaban_rate_stale_date?: string | null
  promote_stale_date?: string | null
  lianban_stale_date?: string | null
  struct_stale_date?: string | null
}

export interface RiskDimension {
  dim?: string
  contrib?: string
  reading?: string
}

export interface SystemRiskResponse extends ApiResponseState {
  level?: number | null
  level_name?: string
  regime?: string
  summary?: string
  level_text?: string
  risk_score?: number | null
  change?: string
  dims?: RiskDimension[]
  '定格时点'?: string
}

export interface MainlineRole {
  name?: string
  label?: string
  status?: string
  status_label?: string
  change_pct?: number | null
  structure_score?: number | null
  score?: number | null
  breadth_ratio?: number | null
  amount_rank_pct?: number | null
  limit_up_count?: number | null
  max_lianban?: number | null
  role_reason?: string
  evidence?: string
  consecutive_hits?: number | null
}

export interface LimitDownStock {
  code?: string
  name?: string
  change_pct?: number | null
  industry?: string | null
  first_time?: string | null
  seal_amount?: number | null
  open_times?: number | null
}

export interface MainlineResponse extends ApiResponseState {
  roles?: MainlineRole[]
  sectors?: MainlineRole[]
  mainlines?: MainlineRole[]
  market_state?: {
    state?: string
    up_ratio?: number | null
  }
  limit_down_pool?: LimitDownStock[]
  limit_down_missing?: string[]
}

export interface SentimentMetrics {
  yesterday_limit_premium?: number | null
  up_down_ratio?: number | null
}

export interface SentimentTodayResponse extends ApiResponseState {
  metrics?: SentimentMetrics
  confidence?: string
}

export interface SectorFlowRow {
  name?: string
  main_net?: number | null
  change_pct?: number | null
}

export interface MarketFlowResponse extends ApiResponseState {
  main_flow_sectors?: SectorFlowRow[]
  main_flow_total?: number | null
  main_flow_total_kind?: string | null
  source?: string
}

export interface StockFlowRow {
  code?: string
  name?: string
  net?: number | null
  change_pct?: number | null
}

export interface StockFlowResponse extends ApiResponseState {
  inflow?: StockFlowRow[]
  outflow?: StockFlowRow[]
}

export interface MarketSector {
  kind?: 'industry' | 'concept' | string
  name?: string
  change_pct?: number | null
  amount?: number | null
  leader_name?: string
  leader_change_pct?: number | null
  up_count?: number | null
  down_count?: number | null
}

export interface MarketSectorsResponse extends ApiResponseState {
  sectors?: MarketSector[]
  coverage?: Record<string, unknown>
}

export interface MultiperiodStock {
  code?: string
  name?: string
  theme?: string
  d1?: number | null
  d3?: number | null
  d5?: number | null
  d20?: number | null
}

export interface MultiperiodResponse extends ApiResponseState {
  stocks?: MultiperiodStock[]
  coverage?: {
    period_rows?: { complete?: number; total?: number }
  }
}

export interface DecisionOverviewData {
  shell: ShellStatusResponse | null
  pulse: PulseResponse | null
  market: MarketOverviewResponse | null
  risk: SystemRiskResponse | null
  mainlines: MainlineResponse | null
}

export type DashboardEndpoint = keyof DecisionOverviewData
