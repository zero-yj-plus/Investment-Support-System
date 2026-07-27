import type { FeatureBaseResponse, FeatureCoverage } from '../types/mainlineResearch'
import type { QualityState } from '../types/dashboard'

export function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}
export function formatScore(value: unknown, digits = 1): string {
  return finite(value) ? value.toFixed(digits) : '—'
}

export function formatPercent(value: unknown, digits = 1): string {
  return finite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(digits)}%` : '—'
}

export function formatPlainPercent(value: unknown, digits = 1): string {
  return finite(value) ? `${value.toFixed(digits)}%` : '—'
}

export function formatAmount(value: unknown): string {
  if (!finite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 100_000_000) return `${(value / 100_000_000).toFixed(1)}亿`
  if (abs >= 10_000) return `${(value / 10_000).toFixed(1)}万`
  return `${value.toFixed(0)}元`
}

export function kindLabel(kind: unknown): string {
  if (kind === 'industry') return '行业'
  if (kind === 'concept') return '概念'
  return '类型未提供'
}

export function coverageLabel(coverage?: FeatureCoverage): string {
  if (!coverage) return '覆盖未提供'
  if (coverage.state === 'complete') return '因子完整'
  if (finite(coverage.complete) && finite(coverage.total)) return `部分因子 ${coverage.complete}/${coverage.total}`
  return '部分因子'
}

export function uniqueText(values: unknown[]): string[] {
  return [...new Set(values.flatMap((value) => Array.isArray(value) ? value : []).filter((item): item is string => typeof item === 'string' && Boolean(item.trim())).map((item) => item.trim()))]
}

export function featureQuality(response: FeatureBaseResponse | null, hasRows: boolean, error = ''): QualityState {
  if (!response) return error ? 'missing' : 'partial'
  if (response.ok === false || response.data_state === 'missing') return 'missing'
  if (response.data_state === 'degraded' || response.stale || (response.degraded_factors?.length || 0) > 0) return 'degraded'
  if (response.data_state === 'accumulating') return 'partial'
  if (!hasRows && ((response.missing_fields?.length || 0) > 0 || (response.missing?.length || 0) > 0)) return 'missing'
  if ((response.missing_fields?.length || 0) > 0 || (response.missing?.length || 0) > 0) return 'degraded'
  return 'complete'
}
