export interface HoldingRecord {
  code?: string | null
  name?: string | null
  cost?: number | null
  shares?: number | null
  industry?: string | null
  weight_pct?: number | null
  market_value?: number | null
  note?: string | null
  risk_plan?: HoldingRiskPlan | null
}

export type HoldingPeriod = 'ultra' | 'short' | 'swing'
export type TargetMode = 'reminder' | 'ordinary_trigger'

export interface HoldingRiskPlan {
  schema_version?: string
  version?: number
  period: HoldingPeriod
  buy_date: string
  customer_risk_price?: number | null
  customer_target_price?: number | null
  target_mode: TargetMode
  confirmed_reduction_pct?: number
  created_at?: string
  updated_at?: string
}

export interface RiskTrigger {
  code?: string
  label?: string
}

export interface HoldingRiskState {
  code?: string
  name?: string
  plan_version?: number
  period?: HoldingPeriod
  period_label?: string
  period_window?: string
  state?: 'monitoring' | 'pending_adjustment' | 'completed_adjustment' | 'data_insufficient' | 'unsupported' | string
  action?: string
  target_reduction_pct?: number
  retain_pct?: number
  confirmed_reduction_pct?: number
  ordinary_trigger_count?: number
  ordinary_triggers?: RiskTrigger[]
  decisive_triggers?: RiskTrigger[]
  near_risk_line?: boolean
  target_touched?: boolean
  target_note?: string | null
  time?: { trading_days_held?: number; max_days?: number; expired?: boolean }
  price?: number | null
  initial_risk_line?: number | null
  current_risk_line?: number | null
  customer_risk_price?: number | null
  customer_target_price?: number | null
  risk_distance_pct?: number | null
  initial_r?: number | null
  r_multiple?: number | null
  highest_price?: number | null
  atr?: { daily_14?: number | null; primary_14?: number | null; method?: string }
  structure?: { support?: number | null; pressure?: number | null; support_basis?: string; pressure_basis?: string }
  execution_constraints?: string[]
  fresh?: boolean
  stale?: boolean
  missing?: string[]
  evaluated_at?: string
  data_as_of?: string
  data_note?: string
}

export interface HoldingsResponse {
  ok: boolean
  error?: string
  portfolio_total_asset?: number | null
  holdings?: HoldingRecord[] | null
  risk_states?: Record<string, HoldingRiskState> | null
}

export interface HoldingsRiskResponse {
  ok: boolean
  error?: string
  states?: Record<string, HoldingRiskState> | null
}

export interface HoldingQuote {
  code?: string | null
  name?: string | null
  price?: number | null
  change_pct?: number | null
  source?: string | null
  stale?: boolean | null
  as_of?: string | null
  quote_note?: string | null
}

export interface QuotesResponse {
  ok: boolean
  quotes?: Record<string, HoldingQuote> | null
  missing?: string[] | null
  error?: string
}

export interface QuoteResponse {
  ok: boolean
  state?: 'complete' | 'not_found' | 'quote_unavailable' | string
  recognized?: boolean | null
  error?: string
  identity?: { name?: string | null } | null
  quote?: HoldingQuote | null
}

export interface HoldingDraft {
  clientKey: string
  code: string
  name: string
  cost: number | null
  shares: number | null
  industry: string
  storedMarketValue: number | null
  storedWeightPct: number | null
  note: string
  riskPlan: HoldingRiskPlan | null
}

export interface HoldingView extends HoldingDraft {
  quote: HoldingQuote | null
  price: number | null
  changePct: number | null
  costAmount: number | null
  marketValue: number | null
  pnl: number | null
  pnlPct: number | null
  holdingSharePct: number | null
  riskState: HoldingRiskState | null
}

export interface NewHoldingInput {
  code: string
  cost: number
  shares: number
  buyDate: string
  period: HoldingPeriod
  customerRiskPrice: number | null
  customerTargetPrice: number | null
  targetMode: TargetMode
  fees: number | null
  note: string
}

export interface SaveHoldingPayload {
  code: string
  name: string
  cost: number
  shares: number
  note: string
  weight_pct: null
  market_value?: number
  risk_plan?: HoldingRiskPlan
}

export interface HoldingActionResponse extends HoldingsResponse {
  states?: Record<string, HoldingRiskState> | null
  ended?: boolean
}
