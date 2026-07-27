import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ApiError, getJson } from '../services/api'
import type { DiagnosisResponse, QuoteResponse } from '../types/diagnosis'

const RECENT_KEY = 'wudi.diagnosis.recent'
const LEGACY_RECENT_KEY = 'hermes.diagnosis.recent'

function readableError(error: unknown, fallback: string): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  if (error instanceof ApiError && error.status === 404) return '未找到该 A 股代码'
  return error instanceof Error ? error.message : fallback
}

function readRecentCodes(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY) || localStorage.getItem(LEGACY_RECENT_KEY) || '[]'
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.map(String).filter((code) => /^\d{6}$/.test(code)).slice(0, 8) : []
  } catch {
    return []
  }
}

function codeFromHash(): string {
  const query = (window.location.hash.split('?', 2)[1] || '').split('#', 1)[0]
  const code = new URLSearchParams(query).get('code') || ''
  return /^\d{6}$/.test(code) ? code : ''
}

export function useDiagnosis() {
  const inputCode = ref('')
  const requestedCode = ref('')
  const recentCodes = ref<string[]>([])
  const requested = ref(false)
  const inputError = ref('')
  const quote = ref<QuoteResponse | null>(null)
  const diagnosis = ref<DiagnosisResponse | null>(null)
  const quoteLoading = ref(false)
  const diagnosisLoading = ref(false)
  const quoteError = ref('')
  const diagnosisError = ref('')
  let quoteController: AbortController | null = null
  let diagnosisController: AbortController | null = null
  let quoteGeneration = 0
  let diagnosisGeneration = 0
  let alive = false

  const busy = computed(() => quoteLoading.value || diagnosisLoading.value)

  function remember(code: string) {
    recentCodes.value = [code, ...recentCodes.value.filter((item) => item !== code)].slice(0, 8)
    try {
      localStorage.setItem(RECENT_KEY, JSON.stringify(recentCodes.value))
    } catch {
      // Storage can be unavailable in privacy mode; diagnosis remains usable.
    }
  }

  async function loadQuote(code: string) {
    quoteController?.abort()
    const controller = new AbortController()
    const generation = ++quoteGeneration
    quoteController = controller
    quoteLoading.value = true
    quoteError.value = ''
    try {
      const payload = await getJson<QuoteResponse>(`/quote/${encodeURIComponent(code)}`, controller.signal)
      if (!alive || controller !== quoteController || generation !== quoteGeneration) return
      quote.value = payload
      if (payload.ok === false) quoteError.value = payload.error || '快速报价暂不可用'
    } catch (caught) {
      if (!alive || controller !== quoteController || generation !== quoteGeneration) return
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      quoteError.value = readableError(caught, '快速报价读取失败')
    } finally {
      if (controller === quoteController) quoteController = null
      if (alive && generation === quoteGeneration) quoteLoading.value = false
    }
  }

  async function loadDiagnosis(code: string) {
    diagnosisController?.abort()
    const controller = new AbortController()
    const generation = ++diagnosisGeneration
    diagnosisController = controller
    diagnosisLoading.value = true
    diagnosisError.value = ''
    try {
      const payload = await getJson<DiagnosisResponse>(`/diagnose?code=${encodeURIComponent(code)}`, controller.signal)
      if (!alive || controller !== diagnosisController || generation !== diagnosisGeneration) return
      if (payload.ok === false) throw new Error(payload.error || payload.missing?.[0] || '服务端未生成诊断卡')
      diagnosis.value = payload
      remember(code)
    } catch (caught) {
      if (!alive || controller !== diagnosisController || generation !== diagnosisGeneration) return
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      diagnosisError.value = readableError(caught, '诊断服务读取失败')
    } finally {
      if (controller === diagnosisController) diagnosisController = null
      if (alive && generation === diagnosisGeneration) diagnosisLoading.value = false
    }
  }

  async function diagnose(rawCode = inputCode.value) {
    const code = String(rawCode || '').replace(/\D/g, '').slice(0, 6)
    inputCode.value = code
    if (!/^\d{6}$/.test(code)) {
      inputError.value = '请输入 6 位 A 股代码'
      return false
    }
    if (diagnosisLoading.value && requestedCode.value === code) return false
    const keepCurrent = requestedCode.value === code && diagnosis.value != null
    inputError.value = ''
    requested.value = true
    requestedCode.value = code
    if (!keepCurrent) {
      quote.value = null
      diagnosis.value = null
    }
    quoteError.value = ''
    diagnosisError.value = ''
    await Promise.allSettled([loadQuote(code), loadDiagnosis(code)])
    return true
  }

  async function refresh() {
    if (!requestedCode.value) {
      inputError.value = '请先输入 6 位 A 股代码再刷新'
      return
    }
    if (busy.value) return
    await diagnose(requestedCode.value)
  }

  function syncIncomingCode() {
    const code = codeFromHash()
    if (code && code !== requestedCode.value) void diagnose(code)
  }

  onMounted(() => {
    alive = true
    recentCodes.value = readRecentCodes()
    const incoming = codeFromHash() || sessionStorage.getItem('hermes.diagnosis.code') || ''
    if (/^\d{6}$/.test(incoming)) {
      sessionStorage.removeItem('hermes.diagnosis.code')
      void diagnose(incoming)
    }
    window.addEventListener('hashchange', syncIncomingCode)
  })

  onBeforeUnmount(() => {
    alive = false
    quoteGeneration += 1
    diagnosisGeneration += 1
    quoteController?.abort()
    diagnosisController?.abort()
    window.removeEventListener('hashchange', syncIncomingCode)
  })

  return {
    inputCode,
    requestedCode,
    recentCodes,
    requested,
    inputError,
    quote,
    diagnosis,
    quoteLoading,
    diagnosisLoading,
    quoteError,
    diagnosisError,
    busy,
    diagnose,
    refresh,
  }
}
