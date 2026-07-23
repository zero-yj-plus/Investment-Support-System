export type InlineTokenType = 'text' | 'strong' | 'code'

export interface InlineToken {
  type: InlineTokenType
  text: string
}

export type ReportBlock =
  | { type: 'heading'; level: number; id: string; text: string; inlines: InlineToken[] }
  | { type: 'paragraph'; inlines: InlineToken[] }
  | { type: 'quote'; inlines: InlineToken[] }
  | { type: 'list'; ordered: boolean; items: Array<{ key: string; inlines: InlineToken[] }> }
  | { type: 'table'; headers: InlineToken[][]; rows: InlineToken[][][] }
  | { type: 'rule' }

export interface ReportOutlineItem {
  id: string
  label: string
  level: number
}

function cleanInline(value: string): string {
  return value
    .replace(/^\s+|\s+$/g, '')
    .replace(/\\([*_`|])/g, '$1')
}

export function parseInline(value: string): InlineToken[] {
  const source = cleanInline(value)
  const tokens: InlineToken[] = []
  const pattern = /(\*\*([^*]+)\*\*|`([^`]+)`)/g
  let cursor = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(source))) {
    if (match.index > cursor) tokens.push({ type: 'text', text: source.slice(cursor, match.index) })
    if (match[2] != null) tokens.push({ type: 'strong', text: match[2] })
    else tokens.push({ type: 'code', text: match[3] || '' })
    cursor = match.index + match[0].length
  }
  if (cursor < source.length) tokens.push({ type: 'text', text: source.slice(cursor) })
  return tokens.length ? tokens : [{ type: 'text', text: source }]
}

function isTableDivider(line: string): boolean {
  const cells = line.trim().replace(/^\||\|$/g, '').split('|')
  return cells.length > 0 && cells.every((cell) => /^\s*:?-{3,}:?\s*$/.test(cell))
}

function tableCells(line: string): InlineToken[][] {
  return line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => parseInline(cell))
}

function isSpecial(line: string, nextLine = ''): boolean {
  const trimmed = line.trim()
  return !trimmed
    || /^#{1,4}\s+/.test(trimmed)
    || /^>\s?/.test(trimmed)
    || /^([-+*]|\d+\.)\s+/.test(trimmed)
    || /^([-*_])\1{2,}$/.test(trimmed.replace(/\s/g, ''))
    || (trimmed.includes('|') && isTableDivider(nextLine))
}

export function parseReportMarkdown(source: string): { blocks: ReportBlock[]; outline: ReportOutlineItem[] } {
  const lines = String(source || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: ReportBlock[] = []
  const outline: ReportOutlineItem[] = []
  let headingIndex = 0
  let index = 0

  while (index < lines.length) {
    const line = lines[index]
    const trimmed = line.trim()
    if (!trimmed) {
      index += 1
      continue
    }

    const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed)
    if (heading) {
      headingIndex += 1
      const level = Math.min(4, heading[1].length + 1)
      const text = cleanInline(heading[2].replace(/^▮\s*/, ''))
      const id = `report-section-${headingIndex}`
      blocks.push({ type: 'heading', level, id, text, inlines: parseInline(text) })
      if (heading[1].length <= 2) outline.push({ id, label: text, level: heading[1].length })
      index += 1
      continue
    }

    if (trimmed.includes('|') && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
      const headers = tableCells(trimmed)
      const rows: InlineToken[][][] = []
      index += 2
      while (index < lines.length && lines[index].trim().includes('|')) {
        rows.push(tableCells(lines[index]))
        index += 1
      }
      blocks.push({ type: 'table', headers, rows })
      continue
    }

    if (/^>\s?/.test(trimmed)) {
      const quote: string[] = []
      while (index < lines.length && /^>\s?/.test(lines[index].trim())) {
        quote.push(lines[index].trim().replace(/^>\s?/, ''))
        index += 1
      }
      blocks.push({ type: 'quote', inlines: parseInline(quote.join(' ')) })
      continue
    }

    const listMatch = /^([-+*]|\d+\.)\s+(.+)$/.exec(trimmed)
    if (listMatch) {
      const ordered = /^\d/.test(listMatch[1])
      const items: Array<{ key: string; inlines: InlineToken[] }> = []
      while (index < lines.length) {
        const item = /^\s*([-+*]|\d+\.)\s+(.+)$/.exec(lines[index])
        if (!item || /^\d/.test(item[1]) !== ordered) break
        const text = cleanInline(item[2])
        items.push({ key: `${index}-${text.slice(0, 24)}`, inlines: parseInline(text) })
        index += 1
      }
      blocks.push({ type: 'list', ordered, items })
      continue
    }

    if (/^([-*_])\1{2,}$/.test(trimmed.replace(/\s/g, ''))) {
      blocks.push({ type: 'rule' })
      index += 1
      continue
    }

    const paragraph = [trimmed]
    index += 1
    while (index < lines.length && !isSpecial(lines[index], lines[index + 1] || '')) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    blocks.push({ type: 'paragraph', inlines: parseInline(paragraph.join(' ')) })
  }

  return { blocks, outline }
}
