export interface StockSearchItem {
  code: string
  name: string
}

export interface StockSearchUniverse {
  count: number
  complete: boolean
  stale: boolean
  refresh_required: boolean
  as_of?: string | null
  source?: string | null
  authority?: string | null
}

export interface StockSearchResponse {
  ok: boolean
  state: 'ready' | 'degraded' | 'unavailable'
  query: string
  results: StockSearchItem[]
  pinyin_available: boolean
  universe: StockSearchUniverse
  missing: string[]
}
