export const KNOWLEDGE_CATEGORIES = [
  '交易原则',
  '亏损禁区',
  '擅长模式',
  '不擅长模式',
  '仓位规则',
  '经验教训',
  '选股偏好',
  '复盘笔记',
  '舆情留档',
] as const

export type KnowledgeCategory = (typeof KNOWLEDGE_CATEGORIES)[number]
export type KnowledgeStatus = 'draft' | 'approved' | 'rejected' | 'superseded' | 'legacy_unverified'

export interface KnowledgeItem {
  schema?: string | null
  id?: string | null
  rule_id?: string | null
  category?: string | null
  title?: string | null
  content?: string | null
  status?: string | null
  version?: number | null
  origin?: string | null
  source_report_ids?: string[] | null
  evidence_ids?: string[] | null
  sample_count?: number | null
  created_at?: string | null
  created_by?: string | null
  approved_at?: string | null
  approved_by?: string | null
  rejected_at?: string | null
  rejected_by?: string | null
  rejection_reason?: string | null
  superseded_at?: string | null
  superseded_by?: string | null
  superseded_reason?: string | null
  supersedes_id?: string | null
  replacement_id?: string | null
}

export interface KnowledgeResponse {
  ok?: boolean
  error?: string
  schema_version?: string | null
  summary?: Partial<Record<KnowledgeStatus, number>> | null
  review_queue_count?: number | null
  items?: KnowledgeItem[] | null
}

export interface KnowledgeMutationResponse {
  ok?: boolean
  error?: string
  id?: string | null
  item?: KnowledgeItem | null
}

export type KnowledgeAction = 'create' | 'approve' | 'reject' | 'supersede' | 'delete'

export interface KnowledgeCreatePayload {
  category: string
  title: string
  content: string
}

export interface KnowledgeApprovePayload {
  source_report_ids: string[]
  evidence_ids: string[]
  sample_count: number
}

export interface KnowledgeRejectPayload {
  reason: string
}

export interface KnowledgeSupersedePayload {
  category: string
  title: string
  content: string
  reason: string
}
