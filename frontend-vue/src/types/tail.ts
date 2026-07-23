import type { ApiResponseState, DecisionCommand } from './dashboard'

export interface TailCommand extends DecisionCommand {
  allow?: string[]
  forbid?: string[]
  blackswan?: string[]
}

export interface TailCockpitSnapshot {
  command?: TailCommand
  as_of?: string
}

export interface TailReportResponse extends ApiResponseState {
  rtype?: string
  date?: string
  title?: string
  content_md?: string
  created_at?: string
  pushed?: boolean
  content_hash?: string
  report_version?: number
  plan_version?: number
  cockpit?: TailCockpitSnapshot
  claim_validation?: { status?: string }
  advice_governance?: { status?: string; proposal_only?: boolean }
}

export interface RecommendationReview {
  window?: string
  evaluation_state?: string
  verdict?: string
  pending_reason?: string
  target_trade_day?: string
  directional_return?: number | null
  final_return?: number | null
  max_return?: number | null
  max_drawdown?: number | null
  hit_status?: string
  error_reason?: string
}

export interface RecommendationItem {
  rec_id?: string
  code?: string
  name?: string
  source?: string
  rec_type?: string
  rec_price?: number | null
  review?: RecommendationReview
  review_windows?: Record<string, RecommendationReview | undefined>
  reviews?: RecommendationReview[]
}

export interface RecommendationListResponse extends ApiResponseState {
  recommendations?: RecommendationItem[]
}

export interface RecommendationWindowStats {
  sample?: number | null
  observed_win_rate?: number | null
  win_rate?: number | null
}

export interface RecommendationStatsResponse extends ApiResponseState {
  win_rate_unit?: string
  enough_sample?: boolean
  minimum_stable_sample?: number | null
  sample?: number | null
  win_rate?: number | null
  observed_win_rate?: number | null
  profit_factor?: number | null
  waiting?: number | null
  untriggered?: number | null
  incomparable?: number | null
  excluded?: number | null
  per_window?: Record<string, RecommendationWindowStats | undefined>
  missing?: string[]
}

export interface WatchlistQuote {
  price?: number | null
  change_pct?: number | null
}

export interface WatchlistItem {
  code?: string
  name?: string
  grade?: string
  status?: string
  target_low?: number | null
  target_high?: number | null
  quote?: WatchlistQuote
}

export interface WatchlistResponse extends ApiResponseState {
  items?: WatchlistItem[]
}

export interface TailActionResponse extends ApiResponseState {
  ok?: boolean
  msg?: string
  verified?: number
  closed?: number
}
