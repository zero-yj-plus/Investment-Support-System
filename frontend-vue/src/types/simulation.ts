export type SimulationOrderStatus = 'proposed' | 'approved' | 'filled' | 'rejected' | 'cancelled'
export type SimulationOrderSide = 'buy' | 'sell'
export type SimulationOrderType = 'limit' | 'market'

export interface SimulationOrderEvent {
  event_id: number
  event_type: string
  from_status?: string | null
  to_status?: string | null
  detail?: Record<string, unknown>
  created_at?: string | null
}

export interface SimulationOrder {
  order_id: string
  status: SimulationOrderStatus
  symbol: string
  side: SimulationOrderSide
  order_type: SimulationOrderType
  quantity: number
  limit_price?: number | null
  reference_price?: number | null
  risk_level?: number | null
  data_as_of?: string | null
  data_stale?: boolean
  data_missing?: string[]
  approved_by?: string | null
  approved_at?: string | null
  market_price?: number | null
  raw_quote_price?: number | null
  market_as_of?: string | null
  market_source?: string | null
  price_mode?: 'live' | 'latest_close' | null
  trade_date?: string | null
  slippage_bps?: number | null
  gross_amount?: number | null
  commission_fee?: number | null
  stamp_duty_fee?: number | null
  transfer_fee?: number | null
  total_fee?: number | null
  net_cash_change?: number | null
  realized_pnl?: number | null
  account_applied?: boolean
  rejection_reason?: string | null
  cancelled_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  events?: SimulationOrderEvent[]
  simulation_only: true
  broker_order_id?: null
}

export interface SimulationEmergencyStop {
  active: boolean
  reason?: string | null
  updated_at?: string | null
}

export interface SimulationStatus {
  schema_version?: string
  mode: 'simulation_only'
  simulation_only: true
  broker_connected: false
  broker_interface_present?: false
  manual_approval_required: true
  account_initialized?: boolean
  settlement_rule?: 'A_SHARE_T_PLUS_1'
  emergency_stop: SimulationEmergencyStop
  last_emergency_reset?: {
    approved_by?: string | null
    reason?: string | null
    at?: string | null
  } | null
  counts: Record<SimulationOrderStatus, number>
  latest_event?: SimulationOrderEvent | null
  disclaimer?: string
}

export interface SimulationOrdersResponse {
  ok: boolean
  simulation_only: true
  items: SimulationOrder[]
  count: number
  error?: string
}

export interface SimulationMutationResponse {
  ok: boolean
  simulation_only: true
  idempotent_replay?: boolean
  order?: SimulationOrder
  status?: SimulationStatus
  account?: SimulationAccount
  error?: string
}

export interface SimulationOrderIntent {
  symbol: string
  side: SimulationOrderSide
  order_type: SimulationOrderType
  quantity: number
  limit_price: number | null
}

export type SimulationDialogMode =
  | 'account-create'
  | 'account-settings'
  | 'create'
  | 'approve'
  | 'fill'
  | 'cancel'
  | 'emergency-stop'
  | 'emergency-reset'

export interface SimulationDialogPayload {
  initial_cash?: number
  commission_rate_pct?: number
  minimum_commission?: number
  stamp_duty_rate_pct?: number
  transfer_fee_rate_pct?: number
  slippage_bps?: number
  symbol?: string
  side?: SimulationOrderSide
  order_type?: SimulationOrderType
  quantity?: number
  limit_price?: number | null
  approved_by?: string
  reason?: string
}

export interface SimulationFeeSettings {
  commission_rate_pct: number
  minimum_commission: number
  stamp_duty_rate_pct: number
  transfer_fee_rate_pct: number
  slippage_bps: number
}

export interface SimulationPosition {
  symbol: string
  quantity: number
  available_quantity: number
  t1_locked_quantity: number
  next_sellable_date?: string | null
  average_cost?: number | null
  cost_value?: number | null
  market_price?: number | null
  market_value?: number | null
  unrealized_pnl?: number | null
  price_mode?: 'live' | 'latest_close' | null
  price_as_of?: string | null
  price_source?: string | null
  valuation_state: 'exact' | 'missing'
}

export interface SimulationEquityPoint {
  snapshot_id: string
  reason: string
  order_id?: string | null
  trade_date?: string | null
  cash: number
  market_value?: number | null
  total_assets?: number | null
  realized_pnl: number
  unrealized_pnl?: number | null
  total_pnl?: number | null
  total_fees: number
  valuation_state: 'exact' | 'missing'
  missing_symbols: string[]
  created_at: string
}

export interface SimulationAccount {
  ok?: boolean
  schema_version: 'simulation-account.v1'
  initialized: boolean
  simulation_only: true
  broker_connected: false
  initial_cash?: number
  cash?: number
  market_value?: number | null
  total_assets?: number | null
  realized_pnl?: number
  unrealized_pnl?: number | null
  total_pnl?: number | null
  return_pct?: number | null
  total_fees?: number
  valuation_state?: 'exact' | 'missing'
  missing_symbols?: string[]
  trade_date?: string | null
  fee_settings?: SimulationFeeSettings
  positions: SimulationPosition[]
  equity_curve: SimulationEquityPoint[]
  created_at?: string | null
  updated_at?: string | null
  error?: string
}

export interface SimulationAccountPayload extends SimulationFeeSettings {
  initial_cash?: number
}
