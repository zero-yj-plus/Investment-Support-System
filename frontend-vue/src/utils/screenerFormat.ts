export function finiteNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function formatNumber(value: unknown, digits = 2): string {
  const parsed = finiteNumber(value)
  return parsed == null ? '—' : parsed.toFixed(digits)
}

export function formatInteger(value: unknown): string {
  const parsed = finiteNumber(value)
  return parsed == null ? '—' : Math.round(parsed).toLocaleString('zh-CN')
}

export function formatPercent(value: unknown, digits = 2, signed = false): string {
  const parsed = finiteNumber(value)
  if (parsed == null) return '—'
  return `${signed && parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}%`
}

export function formatRatioPercent(value: unknown): string {
  const parsed = finiteNumber(value)
  return parsed == null ? '—' : `${parsed.toFixed(2)}%`
}

export function formatMoney(value: unknown): string {
  const parsed = finiteNumber(value)
  if (parsed == null) return '—'
  const absolute = Math.abs(parsed)
  if (absolute >= 100_000_000) return `${(parsed / 100_000_000).toFixed(2)} 亿`
  if (absolute >= 10_000) return `${(parsed / 10_000).toFixed(1)} 万`
  return `${parsed.toFixed(0)} 元`
}

export function formatDateTime(value: unknown): string {
  if (value == null || value === '') return '—'
  const text = String(value)
  const date = new Date(text.includes('T') ? text : text.replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) return text
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

