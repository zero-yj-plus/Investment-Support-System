import type { HoldingDraft, HoldingQuote, HoldingView } from '../types/holdings'

export function finiteNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function formatPrice(value: number | null): string {
  return value == null ? '—' : `${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 3 })} 元`
}

export function formatMoney(value: number | null): string {
  return value == null ? '—' : `${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元`
}

export function formatPercent(value: number | null, signed = false): string {
  if (value == null) return '—'
  const prefix = signed && value > 0 ? '+' : ''
  return `${prefix}${value.toLocaleString('zh-CN', { minimumFractionDigits: 1, maximumFractionDigits: 2 })}%`
}

export function formatShares(value: number | null): string {
  return value == null ? '—' : `${value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })} 股`
}

export function holdingView(
  row: HoldingDraft,
  quote: HoldingQuote | null,
): HoldingView {
  const price = finiteNumber(quote?.price)
  const changePct = finiteNumber(quote?.change_pct)
  const costAmount = row.cost != null && row.shares != null ? row.cost * row.shares : null
  const marketValue = price != null && row.shares != null ? price * row.shares : null
  const pnl = marketValue != null && costAmount != null ? marketValue - costAmount : null
  const pnlPct = pnl != null && costAmount != null && costAmount > 0 ? pnl / costAmount * 100 : null
  return { ...row, quote, price, changePct, costAmount, marketValue, pnl, pnlPct, holdingSharePct: null }
}

export function pnlTone(value: number | null): 'up' | 'down' | 'flat' | 'missing' {
  if (value == null) return 'missing'
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}
