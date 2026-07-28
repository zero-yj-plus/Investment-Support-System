import type { CompanyHistoryEntry } from '../types/companyResearch'

export const COMPANY_CODE_RE = /^\d{6}$/
export const COMPANY_HISTORY_KEY = 'wudi.vue.company-research.history.v1'
export const COMPANY_HISTORY_LIMIT = 20

export function normalizeCompanyCode(value: string) {
  return String(value || '').replace(/\D/g, '').slice(0, 6)
}

export function normalizeCompanyHistory(value: unknown): CompanyHistoryEntry[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item): CompanyHistoryEntry[] => {
    if (!item || typeof item !== 'object') return []
    const row = item as Partial<CompanyHistoryEntry>
    const code = normalizeCompanyCode(String(row.code || ''))
    const content = typeof row.content === 'string' ? row.content.slice(0, 60_000) : ''
    const ts = Number(row.ts)
    if (!COMPANY_CODE_RE.test(code) || !content.trim() || !Number.isFinite(ts)) return []
    return [{
      id: typeof row.id === 'string' && row.id ? row.id : `company-${ts}-${code}`,
      code,
      content,
      ts,
      incomplete: row.incomplete === true,
    }]
  }).slice(0, COMPANY_HISTORY_LIMIT)
}

export function formatCompanyNumber(value: unknown, digits = 2) {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(digits) : '—'
}

export function formatCompanyDate(value: unknown) {
  const raw = String(value || '').trim()
  if (/^\d{8}$/.test(raw)) return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`
  return raw || '—'
}

export function formatCompanyDateTime(value: unknown) {
  const raw = String(value || '').trim()
  if (!raw) return '—'
  return raw.replace('T', ' ').replace(/\+08:00$/, '').replace(/Z$/, ' UTC')
}

export function safeOfficialDisclosureUrl(value: unknown) {
  try {
    const parsed = new URL(String(value || ''))
    const officialHosts = new Set([
      'static.cninfo.com.cn',
      'static.sse.com.cn',
      'disc.static.szse.cn',
    ])
    if (parsed.protocol !== 'https:' || !officialHosts.has(parsed.hostname.toLowerCase())) return ''
    if ((parsed.port && parsed.port !== '443') || parsed.username || parsed.password) return ''
    return parsed.href
  } catch {
    return ''
  }
}

export function disclosureWindowLabel(window?: { start?: string | null; end?: string | null }) {
  const start = String(window?.start || '').trim()
  const end = String(window?.end || '').trim()
  const isoDate = /^\d{4}-\d{2}-\d{2}$/
  if (isoDate.test(start) && isoDate.test(end)) return `${start} 至 ${end}`
  if (isoDate.test(start)) return `${start} 起`
  if (isoDate.test(end)) return `截至 ${end}`
  return '查询窗口以上游返回为准'
}

export function disclosureSourceLabel(sources: string[] | undefined, exchange: string | undefined, verification: string | undefined, lastGood = false) {
  const values = Array.isArray(sources) ? sources : []
  const hasCninfo = values.includes('cninfo')
  const exchangeKey = exchange === 'sse' ? 'sse' : 'szse'
  const exchangeName = exchange === 'sse' ? '上交所' : '深交所'
  const hasExchange = values.includes(exchangeKey)
  const crossConfirmed = ['cross_confirmed', 'partially_cross_confirmed'].includes(String(verification || ''))
  if (hasCninfo && hasExchange && crossConfirmed) return `巨潮 + ${exchangeName}均有同题记录`
  if (hasCninfo && hasExchange && lastGood) return `上次成功快照 · 巨潮 + ${exchangeName}均有记录`
  if (hasCninfo) return '仅巨潮官网记录'
  if (hasExchange) return `仅${exchangeName}官网记录`
  return '来源确认待补充'
}
