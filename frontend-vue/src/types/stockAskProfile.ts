export type StockAskHoldingStatus = 'unheld' | 'held' | 'unspecified'
export type StockAskScenario = 'intraday' | 'after_close'
export type StockAskHorizon = 'NEXT_DAY' | 'SHORT_TERM' | 'SWING'

export interface StockAskProfile {
  holding_status: StockAskHoldingStatus
  current_price: number | null
  holding_cost: number | null
  position_pct: number | null
  scenario: StockAskScenario
  horizon: StockAskHorizon
  risk_budget_pct: number
  risk_budget_explicit: boolean
  max_position_pct: number
  allow_add: boolean
}

export const DEFAULT_STOCK_ASK_PROFILE: StockAskProfile = {
  holding_status: 'unspecified',
  current_price: null,
  holding_cost: null,
  position_pct: null,
  scenario: 'intraday',
  horizon: 'SHORT_TERM',
  risk_budget_pct: 1.5,
  risk_budget_explicit: false,
  max_position_pct: 70,
  allow_add: false,
}

export function createDefaultStockAskProfile(): StockAskProfile {
  return { ...DEFAULT_STOCK_ASK_PROFILE }
}
