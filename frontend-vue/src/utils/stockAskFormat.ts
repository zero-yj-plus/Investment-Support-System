export function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function formatPrice(value: unknown): string {
  return finite(value) ? value.toFixed(2) : '—'
}

export function formatPercent(value: unknown, signed = true): string {
  if (!finite(value)) return '—'
  const sign = signed && value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

export function formatRatio(value: unknown): string {
  return finite(value) ? value.toFixed(2) : '—'
}

export function formatMoney(value: unknown): string {
  if (!finite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 100_000_000) return `${(value / 100_000_000).toFixed(2)} 亿`
  if (abs >= 10_000) return `${(value / 10_000).toFixed(2)} 万`
  return `${value.toFixed(2)} 元`
}

export function trendClass(value: unknown): 'is-up' | 'is-down' | 'is-flat' {
  if (!finite(value) || value === 0) return 'is-flat'
  return value > 0 ? 'is-up' : 'is-down'
}

export function formatTime(value: unknown): string {
  const text = String(value || '').trim()
  if (!text) return '时点未提供'
  return text.replace('T', ' ').replace(/\+08:00$/, '')
}
