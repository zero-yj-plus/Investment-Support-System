export type IndustryChainRunState = 'idle' | 'loading' | 'complete' | 'partial' | 'error'

export interface IndustryChainHistoryEntry {
  id: string
  industry: string
  focus?: string
  content: string
  ts: number
  incomplete: boolean
}

export interface IndustryChainCompanyLink {
  priority: string
  code: string
  name: string
}

export interface IndustryChainSummary {
  conclusion: string
  tradeTiming: string
  dataAsOf: string
  sourceCoverage: string
  gapSummary: string
  trueEmpty: boolean
  deterministicFallback: boolean
  companies: IndustryChainCompanyLink[]
}

export interface IndustryChainKnowledgeResponse {
  ok?: boolean
  id?: string
  error?: string
}

export type IndustryChainRequestKey = 'research' | 'knowledge' | 'delete'
