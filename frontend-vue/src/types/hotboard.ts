import type { ApiResponseState } from './dashboard'

export interface HotboardStock {
  code?: string
  name?: string
  price?: number | null
  change_pct?: number | null
  reason_type?: string
  limit_up_type?: string
  high_days?: string
  is_again?: boolean
  suc_rate_pct?: number | null
  open_num_ths?: number | null
  first_time?: string
  last_time?: string
  seal_amount?: number | null
  seal_volume?: number | null
  turnover_rate?: number | null
  circ_mv?: number | null
  amount?: number | null
  amount_as_of?: string | null
  amount_source?: string | null
}

export interface HotboardResponse extends ApiResponseState {
  date?: string
  stocks?: HotboardStock[]
}

export interface HotboardTheme {
  name: string
  count: number
}
