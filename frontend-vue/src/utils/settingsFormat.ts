import type { DeliveryReceipt, SystemJob } from '../types/settings'

export const JOB_LABELS: Record<string, string> = {
  morning: '早盘内参', midday: '尾盘决策草案', tail: '尾盘决策定稿',
  close: '收盘复盘', evening: '次日策略与趋势票', archive: '晚间自动存档',
  capital_snapshot: '资金快照', daily_plan_finalize: '盘前计划定稿',
  data_sentinel_am: '开盘前数据体检', data_sentinel_pm: '收盘后数据体检',
  pdf_postmarket: '盘后 PDF', pdf_premarket: '盘前 PDF', pm_brief_gen: '盘前简报草案',
  quant_pick_daily: '每日量化选股', quant_pick_fill: '开盘价回填', prewarm_tick: '数据预热',
}

export function textValue(value: unknown, missing = '未提供') {
  return typeof value === 'string' && value.trim() ? value.trim() : missing
}

export function formatDuration(value: number | null | undefined) {
  if (value == null || !Number.isFinite(Number(value))) return '未提供'
  const milliseconds = Math.max(0, Number(value))
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(milliseconds < 10_000 ? 1 : 0)} 秒`
  return `${(milliseconds / 60_000).toFixed(1)} 分`
}

export function jobId(job: SystemJob) {
  return textValue(job.id, '')
}

export function jobLabel(job: SystemJob) {
  const id = jobId(job).replace(/^ext_/, '')
  if (JOB_LABELS[id]) return JOB_LABELS[id]
  const name = textValue(job.name, '')
  if (name && !/<locals>|build_scheduler|register\./i.test(name)) return name
  return id || '未命名任务'
}

export function jobStatus(job: SystemJob, today?: string | null) {
  if (job.enabled === false) return { label: '未启用', tone: 'neutral' }
  const status = String(job.last_result?.status || job.last_status || '')
  const runDate = String(job.last_run || job.last_result?.started_at || '').slice(0, 10)
  const historical = Boolean(runDate && today && runDate !== today)
  if (!status || status === 'never') return { label: '尚未运行', tone: 'neutral' }
  if (historical && ['error', 'interrupted', 'blocked', 'warn'].includes(status)) return { label: '上次异常（历史）', tone: 'neutral' }
  if (['error', 'interrupted'].includes(status)) return { label: '当前失败', tone: 'danger' }
  if (['blocked', 'warn'].includes(status)) return { label: '需要关注', tone: 'warning' }
  if (status === 'running') return { label: '运行中', tone: 'warning' }
  if (status === 'ok') return { label: historical ? '上次成功（历史）' : '最近成功', tone: 'success' }
  return { label: status, tone: 'neutral' }
}

export function receiptStatus(receipt: DeliveryReceipt) {
  const status = String(receipt.status || '')
  if (status === 'accepted') return { label: '平台已接受', tone: 'success' }
  if (status === 'delivered') return { label: '已送达', tone: 'success' }
  if (status === 'acknowledged') return { label: '人工确认已接收', tone: 'success' }
  if (status === 'partial') return { label: '部分目标成功', tone: 'warning' }
  if (['sending', 'unknown'].includes(status)) return { label: '结果待核验', tone: 'warning' }
  if (['failed', 'failed_retryable', 'failed_terminal'].includes(status)) return { label: '发送失败', tone: 'danger' }
  return { label: status || '状态未提供', tone: 'neutral' }
}

export function sourceStatus(status: string | null | undefined) {
  if (status === 'ok') return { label: '最近调用成功', tone: 'success' }
  if (status === 'degraded') return { label: '部分可用', tone: 'warning' }
  if (status === 'down') return { label: '最近调用失败', tone: 'danger' }
  if (status === 'idle') return { label: '尚未采集', tone: 'neutral' }
  return { label: '状态未提供', tone: 'neutral' }
}
