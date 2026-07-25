import type { WatchlistZone } from '../types/watchlist'

export function finiteNumber(value: unknown): number | null {
  if (value == null || value === '' || typeof value === 'boolean') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function formatPrice(value: unknown): string {
  const parsed = finiteNumber(value)
  return parsed == null ? '—' : parsed.toFixed(2)
}

export function formatPercent(value: unknown): string {
  const parsed = finiteNumber(value)
  if (parsed == null) return '—'
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(2)}%`
}

export function formatBand(low: unknown, high: unknown): string {
  const parsedLow = finiteNumber(low)
  const parsedHigh = finiteNumber(high)
  if (parsedLow != null && parsedHigh != null) return `${parsedLow.toFixed(2)}—${parsedHigh.toFixed(2)}`
  if (parsedLow != null) return `≥ ${parsedLow.toFixed(2)}（上沿缺失）`
  if (parsedHigh != null) return `≤ ${parsedHigh.toFixed(2)}（下沿缺失）`
  return '—'
}

export function quoteTone(value: unknown): 'up' | 'down' | 'flat' | 'missing' {
  const parsed = finiteNumber(value)
  if (parsed == null) return 'missing'
  if (parsed > 0) return 'up'
  if (parsed < 0) return 'down'
  return 'flat'
}

export function zoneStateLabel(zone: WatchlistZone | null | undefined): string {
  if (zone?.state === 'complete') return '完整'
  if (zone?.state === 'degraded') return '降级快照'
  if (zone?.state === 'missing') return '缺失'
  return '状态未提供'
}

export function zonePositionLabel(position: string | null | undefined): string {
  if (position === 'IN_ZONE') return '区间内'
  if (position === 'ABOVE_ZONE') return '区间上方'
  if (position === 'BELOW_ZONE') return '区间下方'
  return '位置缺失'
}

export function riskLabel(value: unknown): string {
  const parsed = finiteNumber(value)
  return parsed != null && Number.isInteger(parsed) && parsed >= 1 && parsed <= 5
    ? `${parsed} 级`
    : '风险等级缺失'
}

export function targetSourceLabel(value: string | null | undefined): string {
  if (value === 'manual_override') return '手工覆盖'
  if (value === 'auto_ultra') return '自动超短线'
  if (value === 'missing') return '来源缺失'
  return '来源未提供'
}

export function compactTime(value: string | null | undefined): string {
  const text = String(value || '').trim()
  return text ? text.replace('T', ' ').slice(0, 19) : '—'
}
