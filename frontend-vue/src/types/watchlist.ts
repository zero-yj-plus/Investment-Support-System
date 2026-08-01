export type WatchlistZoneState = 'complete' | 'degraded' | 'missing' | string

export interface WatchlistQuote {
  name?: string | null
  price?: number | null
  change_pct?: number | null
  as_of?: string | null
  stale?: boolean | null
  source?: string | null
}

export interface WatchlistZone {
  state?: WatchlistZoneState | null
  label?: string | null
  low?: number | null
  high?: number | null
  position?: 'ABOVE_ZONE' | 'IN_ZONE' | 'BELOW_ZONE' | string | null
  data_date?: string | null
  stale?: boolean | null
  missing?: string[] | null
  metrics?: Record<string, unknown> | null
}

export interface WatchlistZones {
  schema_version?: string | null
  algorithm_version?: string | null
  calculated_at?: string | null
  quote_date?: string | null
  ok?: boolean | null
  missing?: string[] | null
  ultra?: WatchlistZone | null
  swing?: WatchlistZone | null
}

export interface WatchlistItem {
  id?: string | null
  code?: string | null
  name?: string | null
  market?: string | null
  sector?: string | null
  add_reason?: string | null
  manual_target_low?: number | null
  manual_target_high?: number | null
  target_low?: number | null
  target_high?: number | null
  effective_target_low?: number | null
  effective_target_high?: number | null
  target_source?: 'manual_override' | 'auto_ultra' | 'missing' | string | null
  trigger_condition?: string | null
  no_buy_condition?: string | null
  abandon_condition?: string | null
  abandon_price?: number | null
  risk_level?: number | null
  note?: string | null
  created_at?: string | null
  quote?: WatchlistQuote | null
  grade?: 'A' | 'B' | 'C' | 'D' | 'E' | string | null
  status?: string | null
  missing?: string[] | null
  ideal_buy_zones?: WatchlistZones | null
}

export interface WatchlistResponse {
  ok: boolean
  ts?: string | null
  items?: WatchlistItem[] | null
  missing?: string[] | null
  error?: string
}

export interface WatchlistQuoteResponse {
  ok: boolean
  state?: 'complete' | 'not_found' | 'quote_unavailable' | string
  recognized?: boolean | null
  error?: string
  identity?: { name?: string | null } | null
  quote?: WatchlistQuote | null
}

export interface WatchlistMutationResponse {
  ok: boolean
  id?: string | null
  zone_status?: 'queued' | string | null
  error?: string
}

export interface WatchlistImportItem {
  code: string
  name: string
  sector: string
}

export interface WatchlistImportSkippedRow {
  line: number
  code: string
  name: string
  reason: string
}

export interface ParsedWatchlistImport {
  source: 'eastmoney' | 'ths'
  totalRows: number
  items: WatchlistImportItem[]
  skipped: WatchlistImportSkippedRow[]
}

export interface WatchlistImportResponse {
  ok: boolean
  added_count?: number
  duplicate_count?: number
  invalid_count?: number
  added_codes?: string[]
  duplicate_codes?: string[]
  invalid?: Array<{ index?: number; code?: string; reason?: string }>
  zone_status?: 'queued' | string | null
  error?: string
}

export interface WatchlistInput {
  code: string
  name: string
  sector: string
  add_reason: string
  target_low: number | null
  target_high: number | null
  trigger_condition: string
  no_buy_condition: string
  abandon_condition: string
  abandon_price: number | null
  note: string
}

export interface VerifiedWatchlistIdentity {
  name: string
  quote: WatchlistQuote | null
  quoteAvailable: boolean
}
