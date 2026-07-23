const API_BASE = '/api'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: 'application/json' },
    signal,
  })
  const body = (await response.json().catch(() => null)) as T | { error?: string } | null
  if (!response.ok) {
    const detail = body && typeof body === 'object' && 'error' in body ? body.error : undefined
    throw new ApiError(detail || `接口返回 HTTP ${response.status}`, response.status)
  }
  if (body == null) throw new ApiError('接口没有返回可读取的数据', response.status)
  return body as T
}

export async function postJson<T>(path: string, payload: unknown, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload ?? {}),
    signal,
  })
  const body = (await response.json().catch(() => null)) as T | { error?: string } | null
  if (!response.ok) {
    const detail = body && typeof body === 'object' && 'error' in body ? body.error : undefined
    throw new ApiError(detail || `接口返回 HTTP ${response.status}`, response.status)
  }
  if (body == null) throw new ApiError('接口没有返回可读取的数据', response.status)
  return body as T
}

export interface EventStreamHandlers {
  signal: AbortSignal
  onStage?: (stage: string) => void
  onDelta?: (delta: string) => void
  onDone?: (payload: Record<string, unknown>) => void
}

export async function postEventStream(
  path: string,
  payload: unknown,
  handlers: EventStreamHandlers,
): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload ?? {}),
    signal: handlers.signal,
  })
  if (!response.ok || !response.body) throw new ApiError(`服务返回 HTTP ${response.status}`, response.status)

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finished = false

  const readWithWatchdog = async () => {
    let timer: ReturnType<typeof setTimeout> | null = null
    try {
      return await Promise.race([
        reader.read(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new ApiError('长时间无响应，已停止本次生成')), 90_000)
        }),
      ])
    } finally {
      if (timer) clearTimeout(timer)
    }
  }

  const consume = (raw: string) => {
    for (const line of raw.split('\n')) {
      const match = /^data:\s?(.*)$/.exec(line.replace(/\r$/, ''))
      if (!match) continue
      let event: Record<string, unknown>
      try {
        event = JSON.parse(match[1]) as Record<string, unknown>
      } catch {
        continue
      }
      if (event.error != null) throw new ApiError(String(event.error))
      if (event.done) {
        finished = true
        handlers.onDone?.(event)
        return
      }
      if (event.stage != null) handlers.onStage?.(String(event.stage))
      if (event.delta != null) handlers.onDelta?.(String(event.delta))
    }
  }

  try {
    for (;;) {
      const { done, value } = await readWithWatchdog()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const chunk = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        consume(chunk)
        if (finished) return
        boundary = buffer.indexOf('\n\n')
      }
    }
    if (buffer.trim()) consume(buffer)
    if (!finished) throw new ApiError('数据流未返回完成标记，已按不完整输出隔离')
  } finally {
    await reader.cancel().catch(() => undefined)
  }
}
