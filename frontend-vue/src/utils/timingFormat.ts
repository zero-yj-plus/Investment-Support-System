export function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function formatNumber(value: unknown, digits = 1): string {
  if (!finite(value)) return '—'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatPercent(value: unknown): string {
  if (!finite(value)) return '—'
  const sign = value > 0 ? '+' : ''
  return `${sign}${formatNumber(value, 2)}%`
}

export function formatRatio(value: unknown): string {
  return finite(value) ? `${formatNumber(value, 2)} 倍` : '—'
}

export function formatMoneyYuan(value: unknown): string {
  if (!finite(value)) return '—'
  const absolute = Math.abs(value)
  if (absolute >= 100_000_000) return `${formatNumber(value / 100_000_000, 2)} 亿元`
  if (absolute >= 10_000) return `${formatNumber(value / 10_000, 2)} 万元`
  return `${formatNumber(value, 0)} 元`
}

export function scoreWidth(value: unknown): string {
  if (!finite(value)) return '0%'
  return `${Math.min(100, Math.max(0, value))}%`
}

export function changeTone(value: unknown): 'is-up' | 'is-down' | 'is-flat' {
  if (!finite(value) || value === 0) return 'is-flat'
  return value > 0 ? 'is-up' : 'is-down'
}

export function uniqueText(values: Array<string | null | undefined>): string[] {
  return [...new Set(values.map((value) => String(value || '').trim()).filter(Boolean))]
}
