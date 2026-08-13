export type EtfWatchState = 'complete' | 'degraded' | 'missing' | string

export interface EtfQuote {
  name?: string | null
  price?: number | null
  change_pct?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  prev_close?: number | null
  volume?: number | null
  amount?: number | null
  turnover_rate?: number | null
  volume_ratio?: number | null
  total_mv?: number | null
  amplitude_pct?: number | null
  source?: string | null
  as_of?: string | null
  stale?: boolean | null
  quote_note?: string | null
}

export interface EtfProfile {
  schema_version?: string | null
  official_name?: string | null
  fund_type?: string | null
  kind?: 'cross' | 'equity' | 'bond' | 'commodity' | string | null
  kind_label?: string | null
  tracking_target?: string | null
  manager?: string | null
  custodian?: string | null
  management_fee_pct?: number | null
  custody_fee_pct?: number | null
  net_assets_billion?: number | null
  asset_date?: string | null
  trade_rule?: 'T+0' | 'T+1' | string | null
  latest_nav?: number | null
  nav_date?: string | null
  current_nav?: number | null
  current_nav_date?: string | null
  subscribe_status?: string | null
  redeem_status?: string | null
  ret5_pct?: number | null
  ret20_pct?: number | null
  ret60_pct?: number | null
  volatility20_pct?: number | null
  drawdown60_pct?: number | null
  trend?: string | null
  history_date?: string | null
  history_source?: string | null
  history_adjustment?: string | null
  profile_source?: string | null
  nav_source?: string | null
  refreshed_at?: string | null
  state?: EtfWatchState | null
  missing?: string[] | null
}

export interface EtfWatchItem {
  id?: string | null
  code?: string | null
  name?: string | null
  group?: string | null
  note?: string | null
  created_at?: string | null
  quote?: EtfQuote | null
  profile?: EtfProfile | null
  state?: EtfWatchState | null
  missing?: string[] | null
}

export interface EtfWatchResponse {
  ok: boolean
  schema_version?: string | null
  ts?: string | null
  items?: EtfWatchItem[] | null
  missing?: string[] | null
  low_frequency_refresh?: string | null
  dual_cycle_zones?: boolean | null
  error?: string
}

export interface EtfWatchMutationResponse {
  ok: boolean
  id?: string | null
  profile_status?: 'queued' | string | null
  added_codes?: string[]
  duplicate_codes?: string[]
  rejected_codes?: string[]
  error?: string
}
