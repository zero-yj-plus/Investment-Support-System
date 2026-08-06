export type CompanyResearchRunState = 'idle' | 'loading' | 'complete' | 'partial' | 'error'
export type CompanyRequestKey = 'status' | 'disclosures' | 'interactions' | 'research' | 'knowledge' | 'delete'

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
