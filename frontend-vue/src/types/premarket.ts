import type { ApiResponseState, DecisionCommand } from './dashboard'

export interface PremarketCommand extends DecisionCommand {
  allow?: string[]
  forbid?: string[]
  blackswan?: string[]
}

export interface PremarketDirection {
  name?: string
  change_pct?: number | null
  main_flow?: number | null
}

export interface PremarketCockpitSnapshot {
  command?: PremarketCommand
  dir?: PremarketDirection[]
  as_of?: string
}

export interface PremarketReportResponse extends ApiResponseState {
  rtype?: string
  date?: string
  title?: string
  content_md?: string
  created_at?: string
  pushed?: boolean
  content_hash?: string
  report_version?: number
  plan_version?: number
  cockpit?: PremarketCockpitSnapshot
  claim_validation?: {
    status?: string
  }
  advice_governance?: {
    status?: string
    proposal_only?: boolean
  }
}

export interface PremarketStatusResponse extends ApiResponseState {
  model_connection?: {
    configured?: boolean
    connected?: boolean
  }
  hermes?: {
    agent_capability?: {
      ready?: boolean
      reason?: string
    }
  }
}

export interface PremarketActionResponse extends ApiResponseState {
  ok?: boolean
  msg?: string
}
