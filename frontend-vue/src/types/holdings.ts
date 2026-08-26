export type HoldingRiskState =
  | 'blue'
  | 'yellow'
  | 'orange'
  | 'red'
  | 'gray'
  | 'unconfigured'
  | 'quote_missing'
  | 'quote_stale'
  | 'normal'
  | 'near_stop'
  | 'near_target'
  | 'stop_triggered'
  | 'target_triggered'

export interface HoldingRecord {
  code?: string | number
  name?: string
  cost?: number | null
  shares?: number | null
  industry?: string
  market_value?: number | null
  weight_pct?: number | null
  stop_loss_price?: number | null
  take_profit_price?: number | null
  horizon?: HoldingHorizon
  entry_type?: HoldingEntryType
  entry_date?: string | null
  note?: string
}

export type HoldingHorizon = 'general' | 'overnight' | 'ultra' | 'short' | 'swing' | 'trend'
export type HoldingEntryType = 'general' | 'breakout' | 'pullback' | 'event'

export interface HoldingDraft {
  clientKey: string
  code: string
  name: string
  cost: number | null
  shares: number | null
  industry: string
  storedMarketValue: number | null
  storedWeightPct: number | null
  stopLossPrice: number | null
  takeProfitPrice: number | null
  horizon: HoldingHorizon
  entryType: HoldingEntryType
  entryDate: string
  note: string
}

export interface HoldingQuote {
  code?: string
  name?: string
  price?: number | null
  change_pct?: number | null
  as_of?: string
  source?: string
  stale?: boolean
  quote_note?: string
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
  riskState: HoldingRiskState
  riskLabel: string
  stopDistancePct: number | null
  targetDistancePct: number | null
  algorithmRisk: HoldingRiskCard | null
}

export interface NewHoldingInput {
  code: string
  cost: number
  shares: number
  stopLossPrice: number | null
  takeProfitPrice: number | null
  horizon: HoldingHorizon
  entryType: HoldingEntryType
  entryDate: string
  note: string
}

export interface SaveHoldingPayload {
  code: string
  name: string
  cost: number
  shares: number
  note: string
  weight_pct: number | null
  stop_loss_price: number | null
  take_profit_price: number | null
  horizon: HoldingHorizon
  entry_type: HoldingEntryType
  entry_date: string | null
  market_value?: number
}

export interface HoldingRiskCard {
  code: string
  name?: string
  rule_version: string
  shadow_mode: boolean
  push_eligible: boolean
  state: 'blue' | 'yellow' | 'orange' | 'red' | 'gray'
  action: string
  signal_type?: 'data' | 'hold' | 'warning' | 'customer_line' | 'risk_reduce' | 'risk_exit' | 'trend_reduce' | 'profit_take' | 'event_review' | 'event_exit' | 'time_risk'
  signal_type_label?: string
  confidence?: 'low' | 'medium' | 'high'
  sector?: {
    name?: string | null
    change_pct?: number | null
    stock_change_pct?: number | null
    relative_today_pct?: number | null
    data_date?: string | null
    source?: string | null
  }
  summary: string
  reasons: string[]
  horizon: HoldingHorizon
  horizon_label: string
  entry_type?: HoldingEntryType
  entry_type_label?: string
  as_of?: string | null
  evaluated_at?: string | null
  price_basis?: string
  coverage?: number
  fresh?: boolean
  stale?: boolean
  refresh_failed_at?: string | null
  refresh_error?: string | null
  metrics?: Record<string, number | string | null>
  levels?: {
    manual_stop?: number | null
    manual_target?: number | null
    support?: number | null
    support_basis?: string | null
    resistance?: number | null
    resistance_basis?: string | null
    initial_stop?: number | null
    initial_stop_basis?: string | null
    current_structure_stop?: number | null
    trailing_stop?: number | null
    algorithm_stop?: number | null
    target1?: number | null
    target2?: number | null
    risk_unit?: number | null
  }
  missing?: string[]
}

export interface HoldingsRiskResponse {
  ok?: boolean
  error?: string
  shadow_mode?: boolean
  push_enabled?: boolean
  rule_version?: string
  market_context?: Record<string, unknown>
  rows?: Record<string, HoldingRiskCard>
}

export interface HoldingsResponse {
  ok?: boolean
  error?: string
  holdings?: HoldingRecord[]
  portfolio_total_asset?: number | null
}

export interface QuotesResponse {
  ok?: boolean
  error?: string
  quotes?: Record<string, HoldingQuote>
  missing?: Array<string | number>
}

export interface QuoteResponse {
  recognized?: boolean
  state?: string
  error?: string
  quote?: HoldingQuote | null
  identity?: { name?: string } | null
}
