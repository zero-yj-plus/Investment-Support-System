import type { ApiResponseState } from './dashboard'

export type ReportType = 'morning' | 'midday' | 'tail' | 'close' | 'evening' | 'trend'

export interface ReportTypeOption {
  id: ReportType
  label: string
  scheduledAt: string
  pushable: boolean
}

export interface ReportCommand {
  risk_level?: number | null
  level_name?: string
  action_label?: string
  today_action?: string
  position_cap?: string
  first_principle?: string
  allow?: string[]
  forbid?: string[]
  blackswan?: string[]
}

export interface ReportRecord extends ApiResponseState {
  schema_version?: string
  rtype?: ReportType | string
  date?: string
  title?: string
  content_md?: string
  content_id?: string
  content_hash?: string
  report_version?: number | string
  plan_version?: number | string
  created_at?: string
  pushed?: boolean
  stale?: boolean
  degraded?: boolean
  missing?: string[]
  cockpit?: {
    as_of?: string
    command?: ReportCommand
  }
  claim_validation?: {
    ok?: boolean
    status?: string
    errors?: string[]
  }
  advice_governance?: {
    ok?: boolean
    status?: string
    proposal_only?: boolean
    errors?: string[]
  }
  eval?: {
    status?: string
    suspect_count?: number | null
  }
  input_lineage?: Record<string, unknown>
  input_context_hash?: string
}

export interface ArchiveDatesResponse extends ApiResponseState {
  dates?: string[]
}

export type DeliveryStatus =
  | 'queued'
  | 'sending'
  | 'accepted'
  | 'delivered'
  | 'partial'
  | 'unknown'
  | 'failed_retryable'
  | 'failed_terminal'
  | 'acknowledged'

export interface DeliveryTargetResult {
  target?: string
  status?: DeliveryStatus | string
  error?: string | null
}

export interface DeliveryReceipt {
  receipt_id?: string
  delivery_id?: string
  content_id?: string
  content_hash?: string
  channel?: string
  attempt?: number
  status?: DeliveryStatus | string
  attempted_at?: string
  sent_at?: string | null
  ack_at?: string | null
  error?: string | null
  error_code?: string | null
  target_results?: DeliveryTargetResult[]
}

export interface DeliveryReceiptsResponse extends ApiResponseState {
  schema_version?: string
  receipts?: DeliveryReceipt[]
  count?: number
  total?: number
}

export interface ReportActionResponse extends ApiResponseState {
  msg?: string
}

