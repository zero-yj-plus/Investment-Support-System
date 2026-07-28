export function finiteNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function formatCompactDate(value: string | null | undefined): string {
  const text = String(value || '').trim()
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
  return text || '—'
}

export function formatCount(value: unknown): string {
  const number = finiteNumber(value)
  return number == null ? '—' : Math.trunc(number).toLocaleString('zh-CN')
}

export function formatPercentRange(minimum: unknown, maximum: unknown): string {
  const low = finiteNumber(minimum)
  const high = finiteNumber(maximum)
  if (low == null && high == null) return '—'
  if (low != null && high != null && low !== high) return `${signed(low)} ~ ${signed(high)}`
  return signed(low ?? high as number)
}

export function formatMoney(value: unknown): string {
  const number = finiteNumber(value)
  if (number == null) return '—'
  const absolute = Math.abs(number)
  if (absolute >= 100_000_000) return `${(number / 100_000_000).toFixed(2)} 亿`
  if (absolute >= 10_000) return `${(number / 10_000).toFixed(1)} 万`
  return `${number.toFixed(0)} 元`
}

export function formatMoneyRange(minimum: unknown, maximum: unknown): string {
  const low = finiteNumber(minimum)
  const high = finiteNumber(maximum)
  if (low == null && high == null) return '—'
  if (low != null && high != null && low !== high) return `${formatMoney(low)} ~ ${formatMoney(high)}`
  return formatMoney(low ?? high)
}

function signed(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(0)}%`
}
