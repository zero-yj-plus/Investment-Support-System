import type { HoldingDraft, HoldingQuote, HoldingRiskCard, HoldingRiskState, HoldingView } from '../types/holdings'

const NEAR_THRESHOLD_PCT = 2

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
  algorithmRisk: HoldingRiskCard | null = null,
): HoldingView {
  const price = finiteNumber(quote?.price)
  const changePct = finiteNumber(quote?.change_pct)
  const costAmount = row.cost != null && row.shares != null ? row.cost * row.shares : null
  const marketValue = price != null && row.shares != null ? price * row.shares : null
  const pnl = marketValue != null && costAmount != null ? marketValue - costAmount : null
  const pnlPct = pnl != null && costAmount != null && costAmount > 0 ? pnl / costAmount * 100 : null
  const stopDistancePct = price != null && row.stopLossPrice != null
    ? (price - row.stopLossPrice) / price * 100
    : null
  const targetDistancePct = price != null && row.takeProfitPrice != null
    ? (row.takeProfitPrice - price) / price * 100
    : null
  let riskState: HoldingRiskState = algorithmRisk?.state || 'normal'
  let riskLabel = algorithmRisk?.action || '区间内'
  if (algorithmRisk) {
    // 服务端卡已统一客户风险线、结构、ATR、均线与市场覆盖层；前端不得
    // 用一套较简单的现价判断覆盖确定性服务端结论。
  } else if (row.stopLossPrice == null && row.takeProfitPrice == null) {
    riskState = 'unconfigured'
    riskLabel = '未设置风险线'
  } else if (price == null) {
    riskState = 'quote_missing'
    riskLabel = '报价缺失，无法判断'
  } else if (quote?.stale) {
    riskState = 'quote_stale'
    riskLabel = '报价陈旧，暂停触发判断'
  } else if (row.stopLossPrice != null && price <= row.stopLossPrice) {
    riskState = 'stop_triggered'
    riskLabel = '已触及止损线'
  } else if (row.takeProfitPrice != null && price >= row.takeProfitPrice) {
    riskState = 'target_triggered'
    riskLabel = '已触及止盈线'
  } else if (stopDistancePct != null && stopDistancePct <= NEAR_THRESHOLD_PCT) {
    riskState = 'near_stop'
    riskLabel = `距止损线 ${Math.max(0, stopDistancePct).toFixed(1)}%`
  } else if (targetDistancePct != null && targetDistancePct <= NEAR_THRESHOLD_PCT) {
    riskState = 'near_target'
    riskLabel = `距止盈线 ${Math.max(0, targetDistancePct).toFixed(1)}%`
  }
  return {
    ...row,
    quote,
    price,
    changePct,
    costAmount,
    marketValue,
    pnl,
    pnlPct,
    holdingSharePct: null,
    riskState,
    riskLabel,
    stopDistancePct,
    targetDistancePct,
    algorithmRisk,
  }
}

export function pnlTone(value: number | null): 'up' | 'down' | 'flat' | 'missing' {
  if (value == null) return 'missing'
  if (value > 0) return 'up'
  if (value < 0) return 'down'
  return 'flat'
}
