export type ResearchKind = 'chain' | 'company'
export type ResearchRunStatus = 'queued' | 'running' | 'canceling' | 'completed' | 'failed' | 'canceled' | 'interrupted'

export interface ResearchRun {
  schema_version: 'research-run.v1' | string
  run_id: string
  kind: ResearchKind
  subject: string
  focus?: string
  status: ResearchRunStatus
  stage?: string
  content?: string
  error?: string | null
  error_code?: string | null
  truncated?: boolean
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  updated_at?: string | null
}

export interface ResearchRunResponse {
  ok?: boolean
  reused?: boolean
  run: ResearchRun
  error?: string
}

export interface ResearchRunLatestResponse {
  ok?: boolean
  kind: ResearchKind
  active?: ResearchRun | null
  latest?: ResearchRun | null
  latest_completed?: ResearchRun | null
  error?: string
}

export const RESEARCH_ACTIVE_STATUSES: ResearchRunStatus[] = ['queued', 'running', 'canceling']
