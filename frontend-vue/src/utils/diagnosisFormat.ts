import type { DiagnosisEvidencePoint, DiagnosisEvidenceQuality, DiagnosisResponse } from '../types/diagnosis'
import type { QualityState } from '../types/dashboard'

export function finiteNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function formatNumber(value: unknown, digits = 2): string {
  const parsed = finiteNumber(value)
  return parsed == null ? '—' : parsed.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits })
}

export function formatCompactMoney(value: unknown): string {
  const parsed = finiteNumber(value)
  if (parsed == null) return '—'
  const abs = Math.abs(parsed)
  if (abs >= 100_000_000) return `${formatNumber(parsed / 100_000_000, 2)} 亿元`
  if (abs >= 10_000) return `${formatNumber(parsed / 10_000, 2)} 万元`
  return `${formatNumber(parsed, 2)} 元`
}

export function formatEvidenceValue(point: DiagnosisEvidencePoint): string {
  const value = finiteNumber(point.value)
  if (value == null) return '—'
  if (point.unit === 'percent') return `${formatNumber(value, 2)}%`
  if (point.unit === 'ratio') return `${formatNumber(value, 2)} 倍`
  if (point.unit === 'share') return `${formatNumber(value, 0)} 股`
  if (point.unit === 'CNY') {
    if (['amount', 'total_mv', 'float_mv', 'main_net'].includes(point.field || '')) return formatCompactMoney(value)
    return `${formatNumber(value, 2)} 元`
  }
  return formatNumber(value, 2)
}

export function formatPercent(value: unknown): string {
  const parsed = finiteNumber(value)
  return parsed == null ? '—' : `${parsed > 0 ? '+' : ''}${formatNumber(parsed, 2)}%`
}

export function formatBeijingTime(value: unknown): string {
  if (value == null || value === '') return '—'
  const text = String(value).trim()
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?$/.test(text)) {
    return `${text.replace('T', ' ')} 北京时间`
  }
  const date = new Date(text)
  if (Number.isNaN(date.getTime())) return text
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date)
}

export function evidenceFieldLabel(field: string | undefined): string {
  return ({
    last_price: '现价', prev_close: '昨收', change_pct: '涨跌幅', amount: '成交额',
    turnover_rate: '换手率', volume_ratio: '量比', pe: '市盈率', pb: '市净率',
    total_mv: '总市值', float_mv: '流通市值',
  } as Record<string, string>)[field || ''] || field || '未命名字段'
}

export function evidenceQualityLabel(status: string | undefined): string {
  return ({
    ok: '可核验', degraded: '降级', conflict: '跨源冲突', missing: '缺失',
    unavailable: '暂不可用', not_applicable: '不适用',
  } as Record<string, string>)[status || ''] || '状态未标注'
}

export function evidenceUnavailableReason(reason: string | null | undefined): string {
  return ({
    market_evidence_timeout: '字段级证据采集超过 4 秒交互预算。',
    diagnosis_data_capacity_busy: '当前证据采集并发容量已满。',
    market_evidence_failed: '字段级证据采集失败。',
  } as Record<string, string>)[reason || ''] || '已配置行情源本次未返回可登记的字段值。'
}

export function qualityState(card: DiagnosisResponse | null): QualityState {
  if (!card) return 'missing'
  const market = card.market_evidence_quality?.status
  if (market === 'missing' || market === 'unavailable' || market === 'not_applicable') return 'missing'
  if (market === 'conflict' || market === 'degraded' || card.analysis_status === 'partial' || card.analysis_pipeline?.quote_stale || (card.missing?.length || 0) > 0) return 'degraded'
  if (market !== 'ok' || card.analysis_status !== 'complete') return 'partial'
  return 'complete'
}

export function qualityLabel(card: DiagnosisResponse | null): string {
  const state = qualityState(card)
  if (state === 'complete') return '诊断证据完整'
  if (state === 'degraded') return card?.analysis_pipeline?.quote_stale ? '陈旧或部分覆盖' : '降级或部分覆盖'
  if (state === 'partial') return '关键项待确认'
  return '诊断证据缺失'
}

export function qualityCoverage(quality: DiagnosisEvidenceQuality | undefined): string {
  if (quality?.captured == null || quality.requested == null) return '覆盖度未返回'
  return `${quality.captured}/${quality.requested} 项`
}

export function uniqueText(values: unknown[]): string[] {
  return [...new Set(values.flatMap((value) => Array.isArray(value) ? value : []).map(String).map((item) => item.trim()).filter(Boolean))]
}
