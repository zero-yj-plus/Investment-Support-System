import type {
  ParsedWatchlistImport,
  WatchlistImportItem,
  WatchlistImportSkippedRow,
} from '../types/watchlist'

const MAX_IMPORT_ROWS = 200
const MAX_IMPORT_BYTES = 2 * 1024 * 1024
const A_SHARE_CODE_RE = /^(?:00[0123]\d{3}|30[01]\d{3}|60[0135]\d{3}|68[89]\d{3}|[48]\d{5}|92\d{4})$/

function decodeBytes(bytes: ArrayBuffer): string {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    try {
      return new TextDecoder('gb18030', { fatal: true }).decode(bytes)
    } catch {
      throw new Error('文件不是可识别的 UTF-8、GBK 或 GB18030 文本。请直接导入软件导出的 TXT。')
    }
  }
}

export async function readWatchlistTextFile(file: File): Promise<string> {
  if (!file.name.toLowerCase().endsWith('.txt')) throw new Error('请选择 .txt 文件。')
  if (!file.size) throw new Error('所选 TXT 是空文件。')
  if (file.size > MAX_IMPORT_BYTES) throw new Error('TXT 文件不能超过 2 MB。')
  return decodeBytes(await file.arrayBuffer()).replace(/^\uFEFF/, '').replaceAll('\u0000', '')
}

function safeText(value: string, limit: number): string {
  return value.trim().replace(/\s+/g, ' ').slice(0, limit)
}

function thsSector(line: string): string {
  const columns = line.split(/\t+/).map((value) => value.trim())
  const candidate = columns[columns.length - 28] || ''
  return /[\u3400-\u9fff]/.test(candidate) && !/[\d%]/.test(candidate)
    ? safeText(candidate, 30)
    : ''
}

export function parseWatchlistText(
  text: string,
  existingCodes: Iterable<string> = [],
): ParsedWatchlistImport {
  const lines = text.split(/\r?\n/)
  const sample = lines.slice(0, 8).join('\n')
  const isThs = /(?:^|\t)(?:SH|SZ|BJ)\d{6}(?:\t|$)/im.test(text)
  const isEastmoney = /代码\s+名称/.test(sample) && /(?:^|\n)\s*\d+\s+\d{5,6}\s+\S+/m.test(text)
  if (!isThs && !isEastmoney) {
    throw new Error('未识别到东方财富或同花顺自选股表格格式。请使用软件直接导出的 TXT。')
  }

  const source: ParsedWatchlistImport['source'] = isThs ? 'ths' : 'eastmoney'
  const known = new Set([...existingCodes].map((code) => String(code).trim().padStart(6, '0')))
  const seen = new Set<string>()
  const items: WatchlistImportItem[] = []
  const skipped: WatchlistImportSkippedRow[] = []
  let totalRows = 0

  const eastHeader = lines.find((line) => /代码\s+名称/.test(line)) || ''
  const eastHeaderColumns = eastHeader.trim().split(/\s{2,}/)
  const eastSectorIndex = eastHeaderColumns.indexOf('所属行业')

  const register = (line: number, code: string, name: string, sector: string, market = '') => {
    totalRows += 1
    const normalizedCode = code.trim()
    const normalizedName = safeText(name, 30)
    let reason = ''
    if (market && !['SH', 'SZ', 'BJ'].includes(market.toUpperCase())) reason = `不支持 ${market.toUpperCase()} 市场`
    else if (!A_SHARE_CODE_RE.test(normalizedCode)) reason = '不是可识别的 6 位 A 股代码'
    else if (known.has(normalizedCode)) reason = '观察池已存在'
    else if (seen.has(normalizedCode)) reason = '文件内代码重复'
    else if (items.length >= MAX_IMPORT_ROWS) reason = `超过单次 ${MAX_IMPORT_ROWS} 条上限`

    if (reason) {
      skipped.push({ line, code: normalizedCode, name: normalizedName, reason })
      return
    }
    seen.add(normalizedCode)
    items.push({ code: normalizedCode, name: normalizedName, sector: safeText(sector, 30) })
  }

  lines.forEach((line, index) => {
    if (!line.trim()) return
    if (source === 'ths') {
      const match = /(?:^|\t)([A-Z]{2})(\d{5,6})\t+([^\t\r\n]+)/i.exec(line)
      if (!match) return
      register(index + 1, match[2], match[3], thsSector(line), match[1])
      return
    }
    const match = /^\s*\d+\s+(\d{5,6})\s+(\S+)/.exec(line)
    if (!match) return
    const columns = line.trim().split(/\s{2,}/)
    const sector = eastSectorIndex >= 0 ? String(columns[eastSectorIndex] || '') : ''
    register(index + 1, match[1], match[2], sector)
  })

  if (!totalRows) throw new Error('文件中没有找到可识别的证券记录。')
  return { source, totalRows, items, skipped }
}
