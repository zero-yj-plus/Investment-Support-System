export type CompanyResearchRunState = 'idle' | 'loading' | 'complete' | 'partial' | 'error'
export type CompanyRequestKey = 'status' | 'enrichment' | 'disclosures' | 'interactions' | 'research' | 'knowledge'

export interface CompanyHistoryEntry {
  id: string
  code: string
  content: string
  ts: number
  incomplete: boolean
}

export interface CompanyModelStatus {
  model_connection?: {
    configured?: boolean
    connected?: boolean | null
    status?: string
  }
  hermes?: {
    agent_capability?: {
      ready?: boolean
      status?: string
      reason?: string
    }
  }
}

export interface CompanyForecastPoint {
  year?: number | null
  value?: number | null
  as_of?: string | null
}

export interface CompanyResearchEnrichment {
  ok?: boolean
  ts?: string | null
  code?: string | null
  scope?: string
  intraday_score_weight?: number | null
  data_state?: 'complete' | 'degraded' | 'missing' | string
  missing?: string[]
  consensus?: {
    status?: string
    as_of?: string | null
    target_price_cny?: number | null
    eps_cny?: CompanyForecastPoint[]
    net_profit_yi_cny?: CompanyForecastPoint[]
    revenue_yi_cny?: CompanyForecastPoint[]
    roe_pct?: CompanyForecastPoint[]
    coverage?: { complete?: number; total?: number }
    note?: string
  }
  holders_top10?: {
    status?: string
    report_date?: string | null
    top10_total_ratio_pct?: number | null
    hk_clearing_ratio_pct?: number | null
    largest_holder?: {
      rank?: number | null
      name?: string | null
      shares?: number | null
      ratio_pct?: number | null
    } | null
    note?: string
  }
  gpjy?: {
    status?: string
    reason?: string
    replacement?: string[]
  }
}

export interface CompanyDisclosureItem {
  announcement_id?: string
  announcement_date?: string | null
  published_at?: string | null
  title?: string | null
  original_url?: string | null
  source_confirmations?: string[]
}

export interface CompanyDisclosureResponse {
  ok?: boolean
  code?: string
  exchange?: 'sse' | 'szse' | string
  state?: 'complete' | 'degraded' | 'missing' | string
  verification_state?: string
  used_last_good?: boolean
  data_as_of?: string | null
  requested_window?: { start?: string | null; end?: string | null }
  items?: CompanyDisclosureItem[]
  degraded_reasons?: string[]
  evidence?: { degraded_reason?: string | null; data_as_of?: string | null; fetched_at?: string | null }
}

export interface CompanyInteractionItem {
  question?: string | null
  question_pub_date?: string | null
  answered?: boolean
  answer?: string | null
  answer_pub_date?: string | null
}

export interface CompanyInteractionResponse {
  ok?: boolean
  code?: string
  state?: 'complete' | 'complete_empty' | 'degraded' | 'last_good' | 'missing' | 'not_supported' | string
  data_as_of?: string | null
  is_formal_disclosure?: boolean
  items?: CompanyInteractionItem[]
  degraded_reasons?: string[]
  evidence?: { degraded_reason?: string | null; fetched_at?: string | null }
}

export interface CompanyKnowledgeResponse {
  ok?: boolean
  id?: string
  error?: string
}

export interface CompanyResourceState<T> {
  loading: boolean
  data: T | null
  error: string
}
