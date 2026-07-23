import type { ApiResponseState, DecisionCommand } from './dashboard'
import type { RecommendationItem, RecommendationStatsResponse } from './tail'

export interface CloseReviewCommand extends DecisionCommand {
  allow?: string[]
  forbid?: string[]
  blackswan?: string[]
}

export interface CloseReviewCockpitSnapshot {
  command?: CloseReviewCommand
  as_of?: string
}

export interface CloseReviewReportResponse extends ApiResponseState {
  rtype?: string
  date?: string
  title?: string
  content_md?: string
  created_at?: string
  pushed?: boolean
  content_hash?: string
  report_version?: number
  plan_version?: number
  cockpit?: CloseReviewCockpitSnapshot
  claim_validation?: { status?: string }
  advice_governance?: { status?: string; proposal_only?: boolean }
}

export interface HoldingsAdviceReviewRow {
  code?: string
  name?: string
  tendency?: string
  direction?: string
  base_price?: number | null
  today_price?: number | null
  change_pct?: number | null
  verdict?: string
}

export interface HoldingsAdviceReviewResponse extends ApiResponseState {
  base_date?: string | null
  rows?: HoldingsAdviceReviewRow[]
  stats?: {
    hit?: number | null
    miss?: number | null
    neutral?: number | null
    scored?: number | null
    win_rate?: number | null
  }
  missing?: string[]
}

export interface CloseRecommendationItem extends RecommendationItem {
  rec_time?: string
  directional_return?: number | null
  final_return?: number | null
}

export interface CloseRecommendationListResponse extends ApiResponseState {
  recommendations?: CloseRecommendationItem[]
  review_windows?: string[]
  ts?: string
}

export interface CloseReviewActionResponse extends ApiResponseState {
  ok?: boolean
  msg?: string
  verified?: number | null
  closed?: number | null
}

export type CloseReviewStatsResponse = RecommendationStatsResponse
