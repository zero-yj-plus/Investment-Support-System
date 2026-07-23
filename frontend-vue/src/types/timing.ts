import type { ApiResponseState } from './dashboard'

export interface TimingFactors {
  '趋势'?: number | null
  '动量'?: number | null
  '量能'?: number | null
  '位置'?: number | null
  [key: string]: number | null | undefined
}

export interface FearGreedFactors {
  '涨跌家数比'?: number | null
  '涨停温度'?: number | null
  '成交量比'?: number | null
  '资金'?: number | null
  '波动反向'?: number | null
  [key: string]: number | null | undefined
}

export interface TimingMovingAverages {
  price?: number | null
  ma5?: number | null
  ma20?: number | null
  ma60?: number | null
}

export interface TimingReading {
  code?: string
  name?: string
  ok?: boolean
  price?: number | null
  change_pct?: number | null
  score?: number | null
  zone?: string
  advice?: string
  factors?: TimingFactors
  ma?: TimingMovingAverages
  vol_ratio?: number | null
  turnover_rate?: number | null
  bars?: number | null
  missing?: string[]
}

export interface FearGreedMeta {
  up?: number | null
  down?: number | null
  flat?: number | null
  limit_up?: number | null
  limit_down?: number | null
  vol_ratio?: number | null
  vol_pctl?: number | null
  main_flow_total?: number | null
}

export interface FearGreedReading {
  ok?: boolean
  score?: number | null
  label?: string
  factors?: FearGreedFactors
  meta?: FearGreedMeta
  data_date?: string
  stale?: boolean
  missing?: string[]
}

export interface TimingResponse extends ApiResponseState {
  fear_greed?: FearGreedReading | null
  indexes?: TimingReading[] | null
  stock?: TimingReading | null
}
