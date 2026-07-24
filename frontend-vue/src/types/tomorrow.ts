import type { ApiResponseState, DecisionCommand } from './dashboard'

export interface TomorrowCommand extends DecisionCommand {
  allow?: string[]
  forbid?: string[]
  blackswan?: string[]
}

export interface TomorrowCockpitSnapshot {
  command?: TomorrowCommand
  as_of?: string
}

export interface ReportLineageSource {
  rtype?: string
  trade_day?: string
  content_id?: string
  version?: number | string
  content_hash?: string
  created_at?: string
}

export interface ReportInputLineage {
  schema_version?: string
  trade_day?: string
  sources?: ReportLineageSource[]
  evidence?: Record<string, unknown>[]
  lineage_hash?: string
}

export interface TomorrowReportResponse extends ApiResponseState {
  rtype?: string
  date?: string
  title?: string
  content_md?: string
  created_at?: string
  pushed?: boolean
  stale?: boolean
  content_hash?: string
  report_version?: number
  plan_version?: number
  cockpit?: TomorrowCockpitSnapshot
  claim_validation?: { status?: string; ok?: boolean }
  advice_governance?: { status?: string; proposal_only?: boolean }
  input_lineage?: ReportInputLineage
}

export interface TomorrowWatchQuote {
  price?: number | null
  change_pct?: number | null
  as_of?: string
  stale?: boolean
  source?: string
}

export interface TomorrowWatchItem {
  code?: string
  name?: string
  grade?: string
  status?: string
  target_low?: number | null
  target_high?: number | null
  target_source?: string
  quote?: TomorrowWatchQuote | null
  missing?: string[]
}

export interface TomorrowWatchlistResponse extends ApiResponseState {
  ts?: string
  items?: TomorrowWatchItem[]
  missing?: string[]
}

export interface TomorrowActionResponse extends ApiResponseState {
  ok?: boolean
  msg?: string
}
