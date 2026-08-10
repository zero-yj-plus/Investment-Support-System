import type { SentimentEvent, SentimentEventFilter, SentimentMarketObservation } from '../types/sentimentEvents'

export function compactTime(value?: string | null) {
  const text = String(value || '')
  return {
    date: text.match(/\d{4}-\d{1,2}-\d{1,2}/)?.[0] || '',
    time: text.match(/\d{1,2}:\d{2}/)?.[0] || '',
  }
}

export function displayTime(value?: string | null) {
  if (!value) return '未提供'
  return String(value).replace('T', ' ').replace(/\+08:00$/, '') || '未提供'
}

export function safeHttpUrl(value?: string | null) {
  try {
    const parsed = new URL(String(value || ''))
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : ''
  } catch {
    return ''
  }
}

export function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function formatPercent(value: unknown, digits = 2) {
  const number = finiteNumber(value)
  if (number == null) return '—'
  return `${number > 0 ? '+' : ''}${number.toFixed(digits)}%`
}

export function eventMatchesFilter(event: SentimentEvent, filter: SentimentEventFilter) {
  const mapping = event.a_share
  if (filter === 'related') return mapping?.state === 'mapped'
  if (filter === 'direct') return mapping?.state === 'mapped' && mapping.level === 'direct_stock'
  if (filter === 'clues') return mapping?.state === 'candidate'
    || (mapping?.state === 'mapped' && mapping.level !== 'direct_stock')
  return true
}

export function eventTitle(event: SentimentEvent) {
  return event.title?.trim() || event.content?.trim().slice(0, 80) || '未提供事件标题'
}

export function eventArchiveKey(event: SentimentEvent) {
  return String(
    event.event_id
    || event.content_hash
    || `${event.published_at || event.time || 'time-missing'}:${eventTitle(event)}`,
  )
}

export function eventDescription(event: SentimentEvent) {
  const title = eventTitle(event)
  let content = String(event.content || '').trim()
  if (!content || content === title) return ''
  if (content.startsWith(title)) content = content.slice(title.length).replace(/^[\s，,。；;：:]+/, '')
  return content
}

export function observationLabel(observation?: SentimentMarketObservation, fallback?: string) {
  const complete = finiteNumber(observation?.progress?.complete)
  const total = finiteNumber(observation?.progress?.total)
  if (!observation?.status) return { label: fallback || '暂无盘面观察', note: '缺少观察状态', tone: 'neutral' }
  if (observation.status === 'waiting') {
    return { label: '待盘面核对', note: observation.target_trade_date ? `${observation.target_trade_date} 目标时点` : '目标日期未提供', tone: 'waiting' }
  }
  if (observation.status === 'observing') {
    return { label: '核对中', note: complete != null && total != null ? `已记录 ${complete}/${total} 个时点` : '检查进度未提供', tone: 'info' }
  }
  if (observation.status === 'observed') return { label: '已留痕', note: '目标交易日盘面记录已完成', tone: 'complete' }
  return { label: observation.label || '暂不可比', note: observation.reason_code || '当前观察不可比较', tone: 'neutral' }
}

export function evidenceLevelLabel(level?: string) {
  return ({ tier_1: 'T1 授权通道', tier_2: 'T2 公开采集', tier_3: 'T3 仅作线索' } as Record<string, string>)[level || ''] || '证据等级未标注'
}

export function sourceRelationLabel(relation?: string) {
  return ({
    customer_authorized_feed: '客户授权数据通道',
    original_publisher: '原始发布方',
    public_aggregator: '公开聚合端（非官方 SLA）',
    republication: '转载端',
    unknown: '上游关系未确认',
  } as Record<string, string>)[relation || ''] || '上游关系未确认'
}

export function degradedReasonLabel(reason: string) {
  return ({
    original_url_unavailable: '原文链接未提供',
    published_at_unavailable: '来源时间未提供',
    published_date_inferred: '来源未给日期，按抓取日补全',
  } as Record<string, string>)[reason] || reason
}
