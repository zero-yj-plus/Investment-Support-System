import type { ApiResponseState } from './dashboard'

export interface FeatureCoverage {
  complete?: number | null
  total?: number | null
  state?: 'complete' | 'partial' | string
  missing?: string[]
}

export interface FeatureBaseResponse extends ApiResponseState {
  coverage?: Record<string, FeatureCoverage>
  score_semantics?: {
    name?: string
    comparable_when?: string
    warning?: string
  }
}

export interface MoneyEffectRow {
  name?: string
  kind?: 'industry' | 'concept' | string
  change_pct?: number | null
  money_score?: number | null
  zt_count?: number | null
  max_lianban?: number | null
  up_count?: number | null
  down_count?: number | null
  leader_name?: string | null
  leader_change_pct?: number | null
  main_flow?: number | null
  amount?: number | null
  score_coverage?: FeatureCoverage
}

export interface MoneyEffectResponse extends FeatureBaseResponse {
  themes?: MoneyEffectRow[]
  losers?: MoneyEffectRow[]
}

export interface CrowdednessRow {
  name?: string
  kind?: 'industry' | 'concept' | string
  change_pct?: number | null
  amount?: number | null
  amt_share?: number | null
  zt_count?: number | null
  crowd_score?: number | null
  level?: '过热拥挤' | '偏拥挤' | '正常' | '冷清' | string
  score_coverage?: FeatureCoverage
}

export interface CrowdednessResponse extends FeatureBaseResponse {
  sectors?: CrowdednessRow[]
}

export interface MainlineHistoryRow {
  name?: string
  kind?: 'industry' | 'concept' | string
  days_on_board?: number | null
  avg_money_score?: number | null
  cum_main_flow?: number | null
  strength?: number | null
  recent_score?: number | null
  flow_note?: string
}

export interface MainlineResponse extends FeatureBaseResponse {
  history_days?: number | null
  mainlines?: MainlineHistoryRow[]
  switch_signal?: string | null
  today_top?: MoneyEffectRow[]
}

export interface LeaderStock {
  code?: string
  name?: string
  lianban?: number | null
  reason?: string | null
  seal_amount?: number | null
  seal_ratio?: number | null
  turnover_rate?: number | null
  first_time?: string | null
  is_leader?: boolean
}

export interface LeaderLadder {
  lianban?: number | null
  label?: string
  count?: number | null
  stocks?: LeaderStock[]
}

export interface LeadersResponse extends FeatureBaseResponse {
  kongban?: { height?: number | null; stocks?: LeaderStock[] } | null
  ladder?: LeaderLadder[]
  popularity?: LeaderStock[]
}

export interface MainlineResearchData {
  moneyEffect: MoneyEffectResponse | null
  crowdedness: CrowdednessResponse | null
  mainline: MainlineResponse | null
  leaders: LeadersResponse | null
}

export type MainlineEndpoint = keyof MainlineResearchData

