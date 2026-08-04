export type SentimentMappingState = 'mapped' | 'candidate' | 'none' | 'unavailable' | string
export type SentimentObservationState = 'waiting' | 'observing' | 'observed' | 'incomparable' | string

export interface SentimentSourceObservation {
  source_id?: string
  publisher_name?: string | null
  collection_platform_name?: string
  source_relation?: string
  access_path?: string
  evidence_level?: string
  published_at?: string | null
  fetched_at?: string | null
  source_url?: string | null
  content_hash?: string
  degraded_reasons?: string[]
}

export interface SentimentMappingMatch {
  kind?: 'stock' | 'industry' | 'concept' | string
  code?: string
  name?: string
  match_by?: string
  name_consistency?: string
}

export interface SentimentObservationSnapshotStock {
  code?: string
  name?: string
  since_baseline_pct?: number | null
  change_pct?: number | null
}

export interface SentimentObservationSnapshotSector {
  code?: string
  name?: string
  median_change_pct?: number | null
  up_ratio_pct?: number | null
}

export interface SentimentObservationSnapshot {
  stocks?: SentimentObservationSnapshotStock[]
  sectors?: SentimentObservationSnapshotSector[]
}

export interface SentimentMarketObservation {
  status?: SentimentObservationState
  label?: string
  reason_code?: string
  target_trade_date?: string | null
  progress?: { complete?: number | null; total?: number | null }
  checkpoints?: Array<{ key?: string; name?: string; status?: string; reason_code?: string }>
  latest?: SentimentObservationSnapshot | null
  result?: SentimentObservationSnapshot | null
  causality?: string
}

export interface SentimentAShareMapping {
  state?: SentimentMappingState
  level?: string
  matches?: SentimentMappingMatch[]
  topic_candidates?: SentimentMappingMatch[]
  coverage?: { missing?: string[] }
  market_check?: {
    state?: string
    label?: string
    stocks?: SentimentObservationSnapshotStock[]
    sectors?: SentimentObservationSnapshotSector[]
  }
  observation?: SentimentMarketObservation
}

export interface SentimentEvent {
  event_id?: string
  source?: string
  title?: string
  content?: string
  time?: string
  published_at?: string | null
  fetched_at?: string | null
  evidence_level?: string
  content_hash?: string
  confirms?: number | null
  multi?: boolean
  observations?: SentimentSourceObservation[]
  corroboration?: {
    collection_endpoint_count?: number | null
    independent_origin_count?: number | null
    independent_origin_confirmed?: boolean
    origin_unknown?: boolean
  }
  a_share?: SentimentAShareMapping
}

export interface SentimentEventsResponse {
  ok?: boolean
  error?: string
  ts?: string
  cn_fetched_at?: string | null
  fetched_at?: string | null
  latest_event_time?: string | null
  refresh_seconds?: number | null
  sources_tried?: number | null
  sources_live?: number | null
  missing?: string[]
  items?: SentimentEvent[]
  snapshot?: {
    served_from_snapshot?: boolean
    status?: 'live' | 'fresh' | 'stale' | string
    saved_at?: string | null
    age_seconds?: number | null
    refreshing?: boolean
  }
  mapping?: {
    state?: string
    mapped?: number | null
    candidates?: number | null
    total?: number | null
    missing?: string[]
  }
  observation?: {
    enabled?: boolean
    state?: string
    tracked?: number | null
    waiting?: number | null
    observing?: number | null
    observed?: number | null
    missing?: string[]
  }
  pipeline?: {
    source_state?: string
    entity_mapping_state?: string
    market_observation_state?: string
    validation_state?: string
  }
}

export type SentimentEventFilter = 'related' | 'direct' | 'clues' | 'all'
