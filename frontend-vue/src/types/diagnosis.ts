import type { QualityState } from './dashboard'

export interface DiagnosisQuote {
  code?: string
  name?: string
  price?: number | null
  prev_close?: number | null
  change_pct?: number | null
  amount?: number | null
  turnover_rate?: number | null
  volume_ratio?: number | null
  pe?: number | null
  pb?: number | null
  open?: number | null
  high?: number | null
  low?: number | null
  as_of?: string | null
  source?: string | null
  stale?: boolean
}

export interface QuoteResponse {
  ok?: boolean
  state?: 'complete' | 'not_found' | 'quote_unavailable' | string
  recognized?: boolean | null
  error_code?: string
  error?: string
  identity?: { name?: string; universe_as_of?: string; universe_complete?: boolean }
  quote?: DiagnosisQuote | null
}

export interface DiagnosisEvidencePoint {
  field?: string
  value?: number | string | null
  unit?: 'CNY' | 'percent' | 'ratio' | 'share' | string
  source_id?: string
  source?: string
  quality_status?: 'ok' | 'degraded' | 'conflict' | 'missing' | string
  freshness?: 'live' | 'delayed' | 'stale' | 'missing' | string
  event_time?: string | null
  fetched_at?: string | null
  trading_day?: string | null
  evidence_id?: string | null
  missing_reason?: string | null
}

export interface DiagnosisEvidenceQuality {
  status?: 'ok' | 'degraded' | 'conflict' | 'missing' | 'unavailable' | 'not_applicable' | string
  captured?: number
  requested?: number
  missing_fields?: string[]
  reason?: string | null
}

export interface DiagnosisEvidence {
  finding?: string
  direction?: '利多' | '利空' | '中性' | string
  source?: string
  evidence_ref?: string
  evidence_refs?: string[]
  rule_version?: string
}

export interface DiagnosisStage {
  id?: string
  label?: string
  status?: 'complete' | 'partial' | 'missing' | string
  score?: number | null
  summary?: string
  source?: string
  data_date?: string | null
}

export interface DiagnosisPipeline {
  rule_version?: string
  completed?: number
  total?: number
  elapsed_ms?: number
  data_as_of?: string | null
  quote_stale?: boolean
  quote_note?: string | null
  stages?: DiagnosisStage[]
}

export interface DiagnosisTechnical {
  structure?: string
  complete?: boolean
  score?: number | null
  reasons?: string[]
  dimensions?: DiagnosisStage[]
  levels?: {
    support?: number | null
    resistance?: number | null
    support_basis?: string
    resistance_basis?: string
  }
  metrics?: Record<string, number | string | null | undefined>
}

export interface DiagnosisRiskDecision {
  decision?: 'allow' | 'reduce' | 'veto' | string
  final_action?: string
  position_cap_pct?: number | null
  effective_confidence?: number | null
  reasons?: string[]
  reason_codes?: string[]
  rule_version?: string
  input_snapshot_hash?: string
  event_scope?: string
}

export interface DiagnosisTradePlan {
  entry_zone?: string | null
  entry_trigger?: string | null
  hold_trigger?: string | null
  stop?: string | null
  support?: number | null
  resistance?: number | null
  invalidation?: number | null
  basis?: string[]
  rule_version?: string
}

export interface DiagnosisResponse {
  ok?: boolean
  code?: string
  name?: string
  recognized?: boolean | null
  error_code?: string
  error?: string
  action?: string | null
  signal_action?: string | null
  original_action?: string | null
  conviction?: string | null
  confidence?: number | null
  position_cap_pct?: number | null
  entry_zone?: string | null
  stop?: string | null
  summary?: string
  missing?: string[]
  analysis_mode?: string
  analysis_status?: string
  decision_authority?: string
  decision_generated_at?: string | null
  analysis_pipeline?: DiagnosisPipeline
  technical_analysis?: DiagnosisTechnical
  risk_decision?: DiagnosisRiskDecision
  trade_plan?: DiagnosisTradePlan
  market_quote?: DiagnosisQuote
  market_evidence?: DiagnosisEvidencePoint[]
  decision_market_evidence?: DiagnosisEvidencePoint[]
  market_evidence_quality?: DiagnosisEvidenceQuality
  decision_market_evidence_quality?: DiagnosisEvidenceQuality
  evidence?: DiagnosisEvidence[]
  deterministic_signal?: {
    signal_score?: number | null
    coverage?: number | null
    complete?: boolean
    reasons?: string[]
    rule_version?: string
  }
  model_connection?: {
    configured?: boolean
    status?: string
    used_for_diagnosis?: boolean
    fallback?: string | null
  }
  model_explanation_status?: string
  analysis_contract?: {
    version?: string
    data_status?: string
    data_as_of?: string | null
    decision_authority?: string
  }
  advice_record?: {
    ok?: boolean
    blocked?: boolean
    recommendation_created?: boolean
    error_code?: string
  }
}

export interface DiagnosisPageQuality {
  state: QualityState
  label: string
}
