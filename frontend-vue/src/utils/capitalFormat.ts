import type { CapitalFlowRow } from '../types/capitalTracking'

export function finiteNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function isRawCapital(row: CapitalFlowRow): boolean {
  return row.unit_verified === false || row.value_unit === 'tdx_raw_unverified'
}

export function capitalDirection(value: unknown): 'in' | 'out' | 'flat' | 'missing' {
  const parsed = finiteNumber(value)
  if (parsed == null) return 'missing'
  if (parsed > 0) return 'in'
  if (parsed < 0) return 'out'
  return 'flat'
}

export function formatCapitalValue(row: CapitalFlowRow, digits = 2): string {
  const value = finiteNumber(row.main_net)
  if (value == null) return '—'
  if (isRawCapital(row)) {
    return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  }
  const absolute = Math.abs(value)
  if (absolute >= 100_000_000) return `${(value / 100_000_000).toFixed(digits)} 亿`
  if (absolute >= 10_000) return `${(value / 10_000).toFixed(1)} 万`
  return `${value.toFixed(0)} 元`
}

export function formatCapitalPoint(value: unknown, raw: boolean): string {
  const parsed = finiteNumber(value)
  if (parsed == null) return '—'
  if (raw) return parsed.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  const absolute = Math.abs(parsed)
  if (absolute >= 100_000_000) return `${(parsed / 100_000_000).toFixed(2)} 亿`
  if (absolute >= 10_000) return `${(parsed / 10_000).toFixed(1)} 万`
  return `${parsed.toFixed(0)} 元`
}

export function formatPercent(value: unknown): string {
  const parsed = finiteNumber(value)
  if (parsed == null) return '—'
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(2)}%`
}
