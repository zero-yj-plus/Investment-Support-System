export type StockAskRequestKey =
  | 'core'
  | 'enrichment'
  | 'conversationList'
  | 'conversationDetail'
  | 'conversationCreate'
  | 'conversationDelete'
  | 'conversationSave'
  | 'chat'

export interface StockSectionState {
  state?: 'complete' | 'partial' | 'pending' | 'missing' | 'not_found' | 'unavailable' | string
  reason?: string
}

export interface StockQuote {
  code?: string | null
  name?: string | null
  price?: number | null
  change_pct?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  prev_close?: number | null
  amount?: number | null
  turnover_rate?: number | null
  volume_ratio?: number | null
  pe?: number | null
  total_mv?: number | null
  float_mv?: number | null
  high_52w?: number | null
  low_52w?: number | null
  source?: string | null
  stale?: boolean
  as_of?: string | null
  quote_note?: string | null
}

export interface StockCoreResponse {
  ok?: boolean
  state?: 'complete' | 'partial' | 'missing' | 'not_found' | string
  ts?: string | null
  quote?: StockQuote | null
  recent_daily?: Array<Record<string, unknown>>
  sections?: Record<string, StockSectionState>
  pending?: string[]
  missing?: string[]
  recognized?: boolean | null
  recognition?: { name?: string | null; known?: boolean; universe_complete?: boolean }
  error_code?: string | null
  error?: string | null
}

export interface StockBoard {
  industry?: string | null
  concepts?: string[]
  source?: string | null
}

export interface StockFlow {
  trade_date?: string | null
  main_net?: number | null
  super_net?: number | null
  large_net?: number | null
  medium_net?: number | null
  small_net?: number | null
  net_all?: number | null
  source?: string | null
  derived?: boolean
}

export interface StockFinancial {
  end_date?: string | null
  roe?: number | null
  grossprofit_margin?: number | null
  netprofit_yoy?: number | null
  or_yoy?: number | null
  debt_to_assets?: number | null
  source?: string | null
}

export interface StockEnrichmentResponse {
  ok?: boolean
  state?: 'complete' | 'partial' | 'missing' | string
  ts?: string | null
  board?: StockBoard | null
  flow?: StockFlow | null
  flow_date?: string | null
  financial?: StockFinancial | null
  sections?: Record<string, StockSectionState>
  pending?: string[]
  missing?: string[]
  error?: string | null
}

export interface ConversationMeta {
  id: string
  title?: string
  group?: string
  mode?: string
  created_at?: string
  updated_at?: string
  msg_count?: number
}

export interface ConversationMessagePayload {
  role?: 'user' | 'assistant' | string
  content?: string
  ts?: string
  has_image?: boolean
}

export interface ConversationListResponse {
  ok?: boolean
  conversations?: ConversationMeta[]
  error?: string
}

export interface ConversationDetailResponse {
  ok?: boolean
  meta?: ConversationMeta
  messages?: ConversationMessagePayload[]
  error?: string
}

export interface ConversationCreateResponse {
  ok?: boolean
  conversation?: ConversationMeta
  error?: string
}

export type StockAskMessageState = 'complete' | 'streaming' | 'partial' | 'error'

export interface StockAskMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  ts?: string
  code?: string
  imageCount?: number
  state: StockAskMessageState
}

export interface PendingStockImage {
  id: string
  name: string
  dataUrl: string
}
