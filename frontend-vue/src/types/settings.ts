export interface ApiResult {
  ok?: boolean
  error?: string
}

export interface ScheduleSettings {
  morning?: string | null
  midday?: string | null
  tail?: string | null
  close?: string | null
  evening?: string | null
  archive?: string | null
}

export interface ModelConnection {
  id?: string | null
  name?: string | null
  provider?: string | null
  base_url?: string | null
  model?: string | null
  api_key_set?: boolean | null
  active?: boolean | null
}

export interface SettingsView {
  feishu_webhook?: string | null
  feishu_webhook_set?: boolean | null
  feishu_secret?: string | null
  feishu_secret_set?: boolean | null
  push_enabled?: boolean | null
  push_via?: string | null
  schedule?: ScheduleSettings | null
  active_model_connection_id?: string | null
  model_connections?: ModelConnection[] | null
  hermes_integration?: {
    optional?: boolean | null
    advanced_diagnostics?: boolean | null
    active_as_model_gateway?: boolean | null
  } | null
  agent_require_restricted_toolsets?: boolean | null
  agent_allowed_toolsets?: string[] | null
  agent_require_profile_attestation?: boolean | null
}

export interface SettingsResponse extends ApiResult {
  settings?: SettingsView | null
}

export interface JobOutputObject {
  kind?: string | null
  report_type?: string | null
  content_id?: string | null
  trade_day?: string | null
  version?: number | string | null
  content_hash?: string | null
  report_types?: string[] | null
}

export interface SystemJob {
  id?: string | null
  name?: string | null
  kind?: string | null
  enabled?: boolean | null
  next_run?: string | null
  next_run_at?: string | null
  last_run?: string | null
  last_ok?: string | null
  last_status?: string | null
  last_error?: string | null
  last_error_code?: string | null
  last_duration_ms?: number | null
  last_output_objects?: JobOutputObject[] | null
  last_result?: {
    status?: string | null
    started_at?: string | null
    finished_at?: string | null
    duration_ms?: number | null
    error?: string | null
    error_code?: string | null
    output_objects?: JobOutputObject[] | null
  } | null
  manual_run?: { enabled?: boolean | null; endpoint?: string | null } | null
  retry?: { eligible?: boolean | null; endpoint?: string | null } | null
}

export interface JobsResponse extends ApiResult {
  schema_version?: string | null
  generated_at?: string | null
  summary?: Record<string, unknown> | null
  jobs?: SystemJob[] | null
}

export interface AgentCapability {
  ready?: boolean | null
  status?: string | null
  reason?: string | null
  runtime_mode?: string | null
  tool_execution?: string | null
  unexpected_toolsets?: string[] | null
  profile_attestation?: string | null
  process_attestation?: string | null
  checked_at?: number | null
}

export interface StatusResponse extends ApiResult {
  client?: { name?: string | null; id?: string | null } | null
  version?: string | null
  date?: string | null
  data_source?: string | null
  ui?: { hide_client_info?: boolean | null } | null
  hermes?: {
    connected?: boolean | null
    optional?: boolean | null
    required_for_core?: boolean | null
    configured?: boolean | null
    custom_connection?: boolean | null
    agent_capability?: AgentCapability | null
  } | null
  model_connection?: {
    configured?: boolean | null
    connected?: boolean | null
    status?: string | null
    required_for_core?: boolean | null
  } | null
}

export interface DataSourceRow {
  key?: string | null
  name?: string | null
  provider?: string | null
  feeds?: string | null
  role?: string | null
  status?: string | null
  latency_ms?: number | null
  last_ts?: string | null
  channel?: string | null
  note?: string | null
}

export interface DatasourcesResponse extends ApiResult {
  source_contract?: string | null
  active_policy?: string | null
  ts?: string | null
  health_refresh?: {
    status?: 'pending' | 'ready' | 'degraded' | null
    request_mode?: 'passive_snapshot' | null
    last_started_at?: string | null
    last_finished_at?: string | null
    duration_ms?: number | null
    error_codes?: string[] | null
  } | null
  sources?: DataSourceRow[] | null
  deployment?: {
    configured?: string | null
    profile?: string | null
    china_public_route?: string | null
    public_note?: string | null
  } | null
  universe?: {
    count?: number | null
    source?: string | null
    as_of?: string | null
    stale?: boolean | null
    refresh_required?: boolean | null
    missing_exchanges?: string[] | null
  } | null
  field_contract?: {
    schema_version?: string | null
    fields?: Record<string, {
      meaning?: string | null
      unit?: string | null
      value_semantics?: string | null
      route?: string[] | null
      live_max_age_seconds?: number | null
      delayed_max_age_seconds?: number | null
      batch_scope?: string | null
    }> | null
  } | null
}

export interface DeliveryChannel {
  key?: string | null
  label?: string | null
  role?: string | null
  kind?: string | null
  state?: string | null
  status?: string | null
  configured?: boolean | null
  enabled?: boolean | null
  last_success_at?: string | null
  last_success?: string | null
  latest_receipt?: { error_code?: string | null } | null
}

export interface DeliveryReadinessResponse extends ApiResult {
  launch_ready?: boolean | null
  configured?: boolean | null
  channel?: string | null
  targets?: string[] | null
  blockers?: string[] | null
  manual_review_count?: number | null
  channels?: DeliveryChannel[] | null
}

export interface DeliveryReceipt {
  delivery_id?: string | null
  content_id?: string | null
  channel?: string | null
  attempt?: number | null
  status?: string | null
  attempted_at?: string | null
  sent_at?: string | null
  updated_at?: string | null
  error?: string | null
  error_code?: string | null
  target_results?: Array<{ target?: string | null; status?: string | null; error?: string | null }> | null
}

export interface DeliveryReceiptsResponse extends ApiResult {
  receipts?: DeliveryReceipt[] | null
  count?: number | null
  total?: number | null
}

export interface DeliveryOutboxItem {
  delivery_id?: string | null
  content_id?: string | null
  channel?: string | null
  status?: string | null
  attempts?: number | null
  updated_at?: string | null
}

export interface DeliveryOutboxResponse extends ApiResult {
  items?: DeliveryOutboxItem[] | null
  count?: number | null
}

export interface AgentRun {
  run_id?: string | null
  purpose?: string | null
  model?: string | null
  capability_status?: string | null
  status?: string | null
  attempts?: number | null
  tool_events?: number | null
  finish_reason?: string | null
  error_code?: string | null
  partial_output?: boolean | null
  safe_to_use?: boolean | null
  started_at?: string | null
  completed_at?: string | null
}

export interface AgentRunsResponse extends ApiResult {
  items?: AgentRun[] | null
}

export interface ModelConnectionsResponse extends ApiResult {
  active_connection_id?: string | null
  connections?: ModelConnection[] | null
}

export interface ModelConnectionPayload {
  name: string
  provider: string
  base_url: string
  model: string
  api_key?: string
  clear_api_key?: boolean
  activate?: boolean
}

export interface MutationResponse extends ApiResult {
  msg?: string | null
  status?: string | null
  run_id?: string | null
  active_connection_id?: string | null
  connection?: ModelConnection | null
}

export type SettingsRequestKey =
  | 'settings' | 'status' | 'jobs' | 'datasources' | 'readiness'
  | 'receipts' | 'outbox' | 'agentRuns' | 'models'

export type SettingsActionKey = 'save' | 'pushTest' | 'model' | 'job' | 'delivery'
