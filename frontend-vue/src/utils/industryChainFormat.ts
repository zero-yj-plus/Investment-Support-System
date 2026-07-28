import type {
  IndustryChainCompanyLink,
  IndustryChainHistoryEntry,
  IndustryChainSummary,
} from '../types/industryChain'

export const INDUSTRY_CHAIN_HISTORY_KEY = 'hermes.chain.history'
export const INDUSTRY_CHAIN_CONTRACT = 'chain-research-v2'
export const INDUSTRY_CHAIN_HISTORY_LIMIT = 20

const trimSentence = (value: string) => value.trim().replace(/[。；]+$/, '')

function labelledBoldLine(content: string, label: string): string {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = new RegExp(`\\*\\*${escaped}[：:]\\s*([^\\n*]+)`, 'i').exec(content)
  return trimSentence(match?.[1] || '')
}

function quoteLine(content: string, label: string): string {
  const line = String(content || '').split(/\r?\n/).find((item) => item.trim().startsWith(`> ${label}：`))
  return trimSentence(line?.trim().replace(/^>\s*/, '').slice(label.length + 1) || '')
}

export function extractIndustryCompanies(content: string): IndustryChainCompanyLink[] {
  const lines = String(content || '').split(/\r?\n/)
  const headerIndex = lines.findIndex((line) => /^\|\s*核验序\s*\|\s*代码\s*\|\s*名称\s*\|/.test(line))
  if (headerIndex < 0) return []
  const rows: IndustryChainCompanyLink[] = []
  for (const line of lines.slice(headerIndex + 2)) {
    if (!line.trim().startsWith('|')) break
    const cells = line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim())
    const code = cells[1] || ''
    if (!/^\d{6}$/.test(code)) continue
    rows.push({ priority: cells[0] || '—', code, name: cells[2] || code })
  }
  return rows
}

export function summarizeIndustryChain(content: string): IndustryChainSummary {
  const source = String(content || '')
  return {
    conclusion: labelledBoldLine(source, '产业结论') || '服务端未单列产业结论，不能从正文猜测确定性判断',
    tradeTiming: labelledBoldLine(source, '当前交易时点') || '未评估；价格、仓位与风险须另走实时行情和风险闸',
    dataAsOf: quoteLine(source, '数据时点') || '未报告',
    sourceCoverage: quoteLine(source, '来源覆盖') || '未报告',
    gapSummary: quoteLine(source, '页首缺口') || '接口未单列页首缺口；不代表证据完整',
    trueEmpty: /TDX\s*目录未匹配|未取得可验证的\s*TDX\s*成分关系/.test(source),
    deterministicFallback: /当前模型不可用|诚实骨架|模型深度研究未通过证据边界/.test(source),
    companies: extractIndustryCompanies(source),
  }
}

export function buildIndustryChainRequest(industry: string) {
  const subject = String(industry || '').trim().slice(0, 120)
  return {
    mode: 'chain',
    code: '',
    codes: [],
    industry: subject,
    history: [],
    images: [],
    research_contract: INDUSTRY_CHAIN_CONTRACT,
    message: [
      `请按研究中心统一的「产业链深度研究」契约分析「${subject}」。`,
      '本轮重点：完成整套产业链研究，并给出后续需要核验的证据清单。',
      '必须沿同一条推导链完成：需求本质 → 上中下游产业地图 → 价值捕获/利润池 → 核心卡点 → 国产替代 → 已验证 A 股公司映射 → 兑现节奏与估值 → 研究优先级和失效条件。',
      '逐项标明数据截至时间/交易日、证据来源或验证等级；未取得、过期、仅有产业常识的内容必须列入证据缺口。',
      '产业研究与交易时点必须分开；当前是否可交易、价格与仓位必须另按实时行情和风险闸复核。',
    ].join('\n'),
  }
}

export function normalizeIndustryHistory(value: unknown): IndustryChainHistoryEntry[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item, index) => {
    if (!item || typeof item !== 'object') return []
    const row = item as Record<string, unknown>
    const industry = String(row.industry || '').trim()
    const content = String(row.content || '')
    const ts = Number(row.ts)
    if (!industry || !content || !Number.isFinite(ts)) return []
    return [{
      id: String(row.id || `legacy-${ts}-${index}`),
      industry,
      content,
      ts,
      incomplete: Boolean(row.incomplete),
    }]
  }).slice(0, INDUSTRY_CHAIN_HISTORY_LIMIT)
}

export function formatIndustryHistoryTime(ts: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(ts))
}
