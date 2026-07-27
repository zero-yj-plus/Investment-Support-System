export function formatTechnicalInteger(value: number | null | undefined): string {
  return value == null || !Number.isFinite(Number(value)) ? '—' : Math.trunc(Number(value)).toLocaleString('zh-CN')
}

export function formatTechnicalDecimal(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toFixed(digits)
}

export function formatTechnicalPrice(value: number | null | undefined): string {
  const number = Number(value)
  return value == null || !Number.isFinite(number) ? '—' : `${number.toFixed(2)} 元`
}

export function formatTechnicalPercent(value: number | null | undefined): string {
  const number = Number(value)
  return value == null || !Number.isFinite(number) ? '—' : `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

export function formatTechnicalMoney(value: number | null | undefined): string {
  const number = Number(value)
  if (value == null || !Number.isFinite(number)) return '—'
  if (Math.abs(number) >= 100_000_000) return `${(number / 100_000_000).toFixed(2)} 亿元`
  if (Math.abs(number) >= 10_000) return `${(number / 10_000).toFixed(2)} 万元`
  return `${number.toFixed(2)} 元`
}

export function formatTechnicalMetrics(metrics: Record<string, unknown> | null | undefined): string {
  if (!metrics) return '无额外指标值'
  const entries = Object.entries(metrics).slice(0, 4)
  if (!entries.length) return '无额外指标值'
  return entries.map(([key, value]) => {
    if (value == null || value === '') return `${key} —`
    const number = Number(value)
    return `${key} ${Number.isFinite(number) ? number.toFixed(2) : String(value)}`
  }).join(' · ')
}

export function technicalDirectionClass(value: number | null | undefined): string {
  const number = Number(value)
  if (value == null || !Number.isFinite(number) || number === 0) return ''
  return number > 0 ? 'is-up' : 'is-down'
}
