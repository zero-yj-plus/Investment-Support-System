export interface HoldingRecord {
  code?: string | null
  name?: string | null
  cost?: number | null
  shares?: number | null
  industry?: string | null
  weight_pct?: number | null
  market_value?: number | null
  note?: string | null
}

export interface HoldingsResponse {
  ok: boolean
  error?: string
  portfolio_total_asset?: number | null
  holdings?: HoldingRecord[] | null
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
}

export interface NewHoldingInput {
  code: string
  cost: number
  shares: number
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
}
