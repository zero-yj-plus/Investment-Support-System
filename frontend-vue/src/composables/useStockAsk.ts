import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { deleteJson, getJson, postEventStream, postJson } from '../services/api'
import type {
  ConversationCreateResponse,
  ConversationDetailResponse,
  ConversationListResponse,
  ConversationMeta,
  DataMaterialCatalogResponse,
  DataMaterialDefinition,
  DataMaterialPreselectResponse,
  DataMaterialReport,
  PendingStockImage,
  StockAskMessage,
  StockAskRequestKey,
  StockCoreResponse,
  StockEnrichmentResponse,
  StockPerspective,
  StockPerspectiveListResponse,
  StockPerspectiveSummary,
  StockAskMode,
  RequiredMaterialDefinition,
} from '../types/stockAsk'

const CODE_RE = /^\d{6}$/
const CONVERSATION_CODE_KEY = 'wudi.stockAsk.conversationCodes'
const PERSPECTIVE_SELECTION_KEY = 'wudi.stockAsk.perspectiveIds'
const DEFAULT_MAX_PERSPECTIVES = 4
const DEFAULT_MAX_MATERIALS = 4

function nowLabel() {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date())
}

function messageId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function errorText(error: unknown, fallback: string) {
  if (error instanceof Error && error.message) return error.message
  return fallback
}

function loadConversationCodes(): Record<string, string> {
  try {
    const value = JSON.parse(localStorage.getItem(CONVERSATION_CODE_KEY) || '{}') as Record<string, unknown>
    return Object.fromEntries(
      Object.entries(value).filter(([, code]) => CODE_RE.test(String(code))),
    ) as Record<string, string>
  } catch {
    return {}
  }
}

export function useStockAsk() {
  const mode = ref<StockAskMode>('stock')
  const codeInput = ref('')
  const selectedCode = ref('')
  const prompt = ref('')
  const core = ref<StockCoreResponse | null>(null)
  const enrichment = ref<StockEnrichmentResponse | null>(null)
  const coreLoading = ref(false)
  const enrichmentLoading = ref(false)
  const coreError = ref('')
  const enrichmentError = ref('')
  const codeError = ref('')
  const composerError = ref('')
  const chatError = ref('')
  const saveWarning = ref('')
  const conversations = ref<ConversationMeta[]>([])
  const perspectives = ref<StockPerspective[]>([])
  const selectedPerspectiveIds = ref<string[]>([])
  const maxSelectedPerspectives = ref(DEFAULT_MAX_PERSPECTIVES)
  const perspectivesLoading = ref(true)
  const perspectiveError = ref('')
  const dataMaterials = ref<DataMaterialDefinition[]>([])
  const requiredMaterials = ref<RequiredMaterialDefinition[]>([])
  const selectedDataMaterialIds = ref<string[]>([])
  const preselectedDataMaterialIds = ref<string[]>([])
  const maxSelectedDataMaterials = ref(DEFAULT_MAX_MATERIALS)
  const materialsAdjusted = ref(false)
  const materialReport = ref<DataMaterialReport | null>(null)
  const materialsLoading = ref(true)
  const materialError = ref('')
  const conversationsLoading = ref(true)
  const conversationError = ref('')
  const activeConversationId = ref('')
  const messages = ref<StockAskMessage[]>([])
  const pendingImages = ref<PendingStockImage[]>([])
  const streaming = ref(false)
  const stageText = ref('')
  const stageSeconds = ref(0)
  const controllers: Partial<Record<StockAskRequestKey, AbortController>> = {}
  const generations: Record<StockAskRequestKey, number> = {
    core: 0,
    enrichment: 0,
    perspectiveCatalog: 0,
    materialCatalog: 0,
    materialPreselect: 0,
    conversationList: 0,
    conversationDetail: 0,
    conversationCreate: 0,
    conversationDelete: 0,
    conversationSave: 0,
    chat: 0,
  }
  let alive = false
  let stageTimer: ReturnType<typeof setInterval> | null = null
  let materialPreselectTimer: ReturnType<typeof setTimeout> | null = null
  let stopRequested = false
  let conversationCodes: Record<string, string> = {}

  const quote = computed(() => core.value?.quote || null)
  const stockName = computed(() => quote.value?.name || core.value?.recognition?.name || '')
  const stockLoading = computed(() => coreLoading.value || enrichmentLoading.value)
  const refreshing = computed(() => stockLoading.value || conversationsLoading.value || perspectivesLoading.value || materialsLoading.value)
  const canAsk = computed(() => mode.value === 'free' || (CODE_RE.test(selectedCode.value) && core.value?.recognized !== false))
  const selectedPerspectives = computed<StockPerspectiveSummary[]>(() => selectedPerspectiveIds.value
    .map((id) => perspectives.value.find((item) => item.id === id))
    .filter((item): item is StockPerspective => Boolean(item))
    .map(({ id, name, version }) => ({ id, name, version })))

  function persistPerspectiveSelection() {
    localStorage.setItem(PERSPECTIVE_SELECTION_KEY, JSON.stringify(selectedPerspectiveIds.value))
  }

  function restorePerspectiveSelection() {
    let stored: unknown = []
    try {
      stored = JSON.parse(localStorage.getItem(PERSPECTIVE_SELECTION_KEY) || '[]')
    } catch {
      stored = []
    }
    const allowed = new Set(perspectives.value.map((item) => item.id))
    selectedPerspectiveIds.value = (Array.isArray(stored) ? stored : [])
      .map((item) => String(item || ''))
      .filter((item, index, rows) => allowed.has(item) && rows.indexOf(item) === index)
      .slice(0, maxSelectedPerspectives.value)
    persistPerspectiveSelection()
  }

  async function loadPerspectives() {
    const guard = requestGuard('perspectiveCatalog', 12_000)
    perspectivesLoading.value = true
    perspectiveError.value = ''
    try {
      const payload = await getJson<StockPerspectiveListResponse>('/chat/perspectives', guard.controller.signal)
      if (!guard.current()) return
      if (payload.ok === false || !Array.isArray(payload.perspectives)) {
        throw new Error(payload.error || '视角列表返回不可用状态')
      }
      perspectives.value = payload.perspectives.filter((item) => item?.id && item?.name)
      const serverLimit = Number(payload.max_selected)
      maxSelectedPerspectives.value = Number.isInteger(serverLimit)
        ? Math.min(DEFAULT_MAX_PERSPECTIVES, Math.max(1, serverLimit))
        : DEFAULT_MAX_PERSPECTIVES
      restorePerspectiveSelection()
    } catch (error) {
      if (!guard.current()) return
      perspectiveError.value = guard.timedOut()
        ? '分析视角读取超过 12 秒；默认问股仍可使用'
        : errorText(error, '分析视角暂不可用；默认问股仍可使用')
    } finally {
      guard.finish()
      if (alive) perspectivesLoading.value = false
    }
  }

  function togglePerspective(id: string) {
    if (streaming.value) return
    perspectiveError.value = ''
    if (!perspectives.value.some((item) => item.id === id)) return
    if (selectedPerspectiveIds.value.includes(id)) {
      selectedPerspectiveIds.value = selectedPerspectiveIds.value.filter((item) => item !== id)
    } else if (selectedPerspectiveIds.value.length >= maxSelectedPerspectives.value) {
      perspectiveError.value = `每轮最多选择 ${maxSelectedPerspectives.value} 个分析视角；请先取消一个`
      return
    } else {
      selectedPerspectiveIds.value = [...selectedPerspectiveIds.value, id]
    }
    persistPerspectiveSelection()
  }

  function clearPerspectives() {
    if (streaming.value) return
    perspectiveError.value = ''
    selectedPerspectiveIds.value = []
    persistPerspectiveSelection()
  }

  async function loadDataMaterials() {
    const guard = requestGuard('materialCatalog', 12_000)
    materialsLoading.value = true
    materialError.value = ''
    try {
      const payload = await getJson<DataMaterialCatalogResponse>(
        '/chat/data-materials', guard.controller.signal,
      )
      if (!guard.current()) return
      if (payload.ok === false || !Array.isArray(payload.materials)) {
        throw new Error(payload.error || '数据材料目录返回不可用状态')
      }
      dataMaterials.value = payload.materials.filter((item) => item?.id && item?.name)
      requiredMaterials.value = Array.isArray(payload.required) ? payload.required : []
      const serverLimit = Number(payload.max_selected)
      maxSelectedDataMaterials.value = Number.isInteger(serverLimit)
        ? Math.min(DEFAULT_MAX_MATERIALS, Math.max(1, serverLimit))
        : DEFAULT_MAX_MATERIALS
      await requestMaterialPreselection(prompt.value, true)
    } catch (error) {
      if (!guard.current()) return
      materialError.value = guard.timedOut()
        ? '数据材料目录读取超过 12 秒；本轮仍可只使用系统必需底座'
        : errorText(error, '数据材料目录暂不可用')
    } finally {
      guard.finish()
      if (alive) materialsLoading.value = false
    }
  }

  async function requestMaterialPreselection(question: string, force = false) {
    if (mode.value !== 'free' || (!force && materialsAdjusted.value)) return
    const guard = requestGuard('materialPreselect', 8_000)
    try {
      const payload = await postJson<DataMaterialPreselectResponse>(
        '/chat/data-materials/preselect',
        { mode: 'free', message: String(question || '').trim() },
        guard.controller.signal,
      )
      if (!guard.current() || mode.value !== 'free') return
      if (payload.ok === false || !Array.isArray(payload.preselected_ids)) {
        throw new Error(payload.error || '系统预选返回不可用状态')
      }
      const allowed = new Set(dataMaterials.value.map((item) => item.id))
      preselectedDataMaterialIds.value = payload.preselected_ids
        .map((item) => String(item || ''))
        .filter((item, index, rows) => allowed.has(item) && rows.indexOf(item) === index)
        .slice(0, 3)
      if (!materialsAdjusted.value || force) {
        selectedDataMaterialIds.value = [...preselectedDataMaterialIds.value]
        if (force) materialsAdjusted.value = false
      }
    } catch (error) {
      if (guard.current()) materialError.value = errorText(error, '系统预选暂不可用')
    } finally {
      guard.finish()
    }
  }

  function scheduleMaterialPreselection() {
    if (materialPreselectTimer) clearTimeout(materialPreselectTimer)
    materialPreselectTimer = setTimeout(() => {
      materialPreselectTimer = null
      void requestMaterialPreselection(prompt.value)
    }, 280)
  }

  function toggleDataMaterial(id: string) {
    if (streaming.value || mode.value !== 'free') return
    materialError.value = ''
    if (!dataMaterials.value.some((item) => item.id === id && item.availability !== 'unavailable')) return
    materialsAdjusted.value = true
    materialReport.value = null
    if (selectedDataMaterialIds.value.includes(id)) {
      selectedDataMaterialIds.value = selectedDataMaterialIds.value.filter((item) => item !== id)
    } else if (selectedDataMaterialIds.value.length >= maxSelectedDataMaterials.value) {
      materialError.value = `每轮最多选择 ${maxSelectedDataMaterials.value} 个可选材料；请先取消一个`
    } else {
      selectedDataMaterialIds.value = [...selectedDataMaterialIds.value, id]
    }
  }

  async function resetDataMaterials() {
    if (streaming.value || mode.value !== 'free') return
    materialReport.value = null
    materialsAdjusted.value = false
    await requestMaterialPreselection(prompt.value, true)
  }

  function persistConversationCodes() {
    localStorage.setItem(CONVERSATION_CODE_KEY, JSON.stringify(conversationCodes))
  }

  function bindConversationCode(conversationId: string, code: string) {
    if (!conversationId || !CODE_RE.test(code)) return
    conversationCodes[conversationId] = code
    persistConversationCodes()
  }

  function abortRequest(key: StockAskRequestKey) {
    generations[key] += 1
    controllers[key]?.abort()
    delete controllers[key]
  }

  function requestGuard(key: StockAskRequestKey, timeoutMs: number) {
    abortRequest(key)
    const controller = new AbortController()
    const generation = ++generations[key]
    let timedOut = false
    const timeout = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, timeoutMs)
    controllers[key] = controller
    return {
      controller,
      generation,
      timedOut: () => timedOut,
      current: () => alive && controllers[key] === controller && generations[key] === generation,
      finish: () => {
        clearTimeout(timeout)
        if (controllers[key] === controller) delete controllers[key]
      },
    }
  }

  async function loadStockPart(part: 'core' | 'enrichment', code: string) {
    const guard = requestGuard(part, 15_000)
    if (part === 'core') {
      coreLoading.value = true
      coreError.value = ''
    } else {
      enrichmentLoading.value = true
      enrichmentError.value = ''
    }
    try {
      if (part === 'core') {
        const payload = await getJson<StockCoreResponse>(`/stock/${encodeURIComponent(code)}/core`, guard.controller.signal)
        if (!guard.current() || selectedCode.value !== code) return
        core.value = payload
        if (payload.recognized === false || payload.state === 'not_found') {
          coreError.value = payload.error || '该代码不在当前完整 A 股名录中'
        } else if (payload.ok === false || payload.state === 'missing') {
          coreError.value = payload.error || payload.missing?.[0] || '股票已识别，但首屏行情暂未返回'
        }
      } else {
        const payload = await getJson<StockEnrichmentResponse>(`/stock/${encodeURIComponent(code)}/enrichment`, guard.controller.signal)
        if (!guard.current() || selectedCode.value !== code) return
        enrichment.value = payload
        if (payload.ok === false || payload.state === 'missing') {
          enrichmentError.value = payload.error || payload.missing?.[0] || '增强证据暂未返回'
        }
      }
    } catch (error) {
      if (!guard.current()) return
      const timeoutText = guard.timedOut() ? '请求超过 15 秒，已停止本模块取数' : ''
      if (part === 'core') coreError.value = timeoutText || errorText(error, '首屏行情读取失败')
      else enrichmentError.value = timeoutText || errorText(error, '增强证据读取失败')
    } finally {
      guard.finish()
      if (alive && selectedCode.value === code) {
        if (part === 'core') coreLoading.value = false
        else enrichmentLoading.value = false
      }
    }
  }

  async function loadStock(rawCode = codeInput.value) {
    const code = String(rawCode || '').trim()
    codeError.value = ''
    if (!CODE_RE.test(code)) {
      codeError.value = '请输入 6 位 A 股股票代码'
      return false
    }
    if (streaming.value) {
      codeError.value = '请先停止当前回答，再切换股票'
      return false
    }
    codeInput.value = code
    selectedCode.value = code
    core.value = null
    enrichment.value = null
    coreError.value = ''
    enrichmentError.value = ''
    chatError.value = ''
    if (activeConversationId.value) bindConversationCode(activeConversationId.value, code)
    await Promise.allSettled([loadStockPart('core', code), loadStockPart('enrichment', code)])
    const loadedCore = core.value as StockCoreResponse | null
    return loadedCore?.recognized !== false
  }

  async function loadConversations() {
    const guard = requestGuard('conversationList', 12_000)
    conversationsLoading.value = true
    conversationError.value = ''
    try {
      const payload = await getJson<ConversationListResponse>(
        `/chat/conversations?mode=${encodeURIComponent(mode.value)}`,
        guard.controller.signal,
      )
      if (!guard.current()) return
      if (payload.ok === false) throw new Error(payload.error || '历史接口返回不可用状态')
      conversations.value = (payload.conversations || []).filter((item) => item.mode === mode.value)
    } catch (error) {
      if (!guard.current()) return
      conversationError.value = guard.timedOut()
        ? '历史请求超过 12 秒，已停止等待'
        : errorText(error, '问股历史暂不可用')
    } finally {
      guard.finish()
      if (alive) conversationsLoading.value = false
    }
  }

  async function createConversation(resetMessages = true) {
    const guard = requestGuard('conversationCreate', 12_000)
    try {
      const payload = await postJson<ConversationCreateResponse>(
        '/chat/conversations', { mode: mode.value }, guard.controller.signal,
      )
      if (!guard.current() || !payload.conversation?.id) return ''
      activeConversationId.value = payload.conversation.id
      if (resetMessages) messages.value = []
      if (mode.value === 'stock' && selectedCode.value) bindConversationCode(payload.conversation.id, selectedCode.value)
      conversations.value = [payload.conversation, ...conversations.value.filter((item) => item.id !== payload.conversation?.id)]
      return payload.conversation.id
    } catch (error) {
      if (guard.current()) conversationError.value = errorText(error, '新建问股会话失败')
      return ''
    } finally {
      guard.finish()
    }
  }

  async function newConversation() {
    if (streaming.value) {
      abortRequest('chat')
      streaming.value = false
      clearStageTimer()
    }
    abortRequest('conversationSave')
    materialReport.value = null
    await createConversation(true)
  }

  async function switchMode(nextMode: StockAskMode) {
    if (nextMode === mode.value) return
    abortRequest('chat')
    abortRequest('conversationDetail')
    abortRequest('conversationCreate')
    abortRequest('conversationSave')
    abortRequest('core')
    abortRequest('enrichment')
    streaming.value = false
    clearStageTimer()
    mode.value = nextMode
    activeConversationId.value = ''
    messages.value = []
    pendingImages.value = []
    chatError.value = ''
    saveWarning.value = ''
    materialReport.value = null
    if (nextMode === 'free') {
      codeError.value = ''
      await requestMaterialPreselection(prompt.value, true)
    }
    await loadConversations()
  }

  async function switchConversation(id: string) {
    if (!id || id === activeConversationId.value) return
    if (streaming.value) {
      abortRequest('chat')
      streaming.value = false
      clearStageTimer()
    }
    abortRequest('conversationSave')
    const guard = requestGuard('conversationDetail', 12_000)
    conversationError.value = ''
    try {
      const payload = await getJson<ConversationDetailResponse>(
        `/chat/conversations/${encodeURIComponent(id)}`, guard.controller.signal,
      )
      if (!guard.current()) return
      if (payload.ok === false || payload.meta?.mode !== mode.value) throw new Error(payload.error || '这不是当前模式的会话')
      activeConversationId.value = id
      messages.value = (payload.messages || []).map((item, index) => ({
        id: `history-${id}-${index}`,
        role: item.role === 'assistant' ? 'assistant' : 'user',
        content: String(item.content || ''),
        ts: String(item.ts || '').slice(11, 19),
        imageCount: item.has_image ? 1 : 0,
        perspectives: Array.isArray(item.perspectives)
          ? item.perspectives.slice(0, DEFAULT_MAX_PERSPECTIVES)
          : [],
        dataMaterials: item.data_materials,
        state: 'complete',
      }))
      materialReport.value = [...messages.value].reverse().find((item) => item.dataMaterials)?.dataMaterials || null
      const boundCode = conversationCodes[id]
      if (mode.value === 'stock' && CODE_RE.test(boundCode || '')) {
        await loadStock(boundCode)
      } else if (mode.value === 'stock') {
        abortRequest('core')
        abortRequest('enrichment')
        selectedCode.value = ''
        codeInput.value = ''
        core.value = null
        enrichment.value = null
        codeError.value = '此旧会话未留存可复核的股票代码，请重新输入标的后继续提问'
      }
    } catch (error) {
      if (guard.current()) conversationError.value = errorText(error, '加载问股会话失败')
    } finally {
      guard.finish()
    }
  }

  async function deleteConversation(id: string) {
    if (!id || streaming.value) return
    const guard = requestGuard('conversationDelete', 12_000)
    try {
      const payload = await deleteJson<{ ok?: boolean; error?: string }>(
        `/chat/conversations/${encodeURIComponent(id)}`, guard.controller.signal,
      )
      if (!guard.current()) return
      if (payload.ok === false) throw new Error(payload.error || '删除失败')
      conversations.value = conversations.value.filter((item) => item.id !== id)
      delete conversationCodes[id]
      persistConversationCodes()
      if (activeConversationId.value === id) {
        activeConversationId.value = ''
        messages.value = []
      }
    } catch (error) {
      if (guard.current()) conversationError.value = errorText(error, '删除问股会话失败')
    } finally {
      guard.finish()
    }
  }

  async function saveExchange(
    userText: string,
    imageCount: number,
    assistantText: string,
    code = '',
    perspectiveIds: string[] = [],
    dataMaterialReport: DataMaterialReport | null = null,
  ) {
    saveWarning.value = ''
    let conversationId = activeConversationId.value
    if (!conversationId) conversationId = await createConversation(false)
    if (!conversationId) {
      saveWarning.value = '回答已完成，但本轮未能写入历史会话'
      return
    }
    if (mode.value === 'stock') bindConversationCode(conversationId, code)
    const guard = requestGuard('conversationSave', 12_000)
    try {
      await postJson<{ ok?: boolean }>(
        `/chat/conversations/${encodeURIComponent(conversationId)}/messages`,
        {
          messages: [
            { role: 'user', content: userText, has_image: imageCount > 0 },
            {
              role: 'assistant', content: assistantText,
              perspective_ids: perspectiveIds,
              data_materials: dataMaterialReport || undefined,
            },
          ],
        },
        guard.controller.signal,
      )
      if (guard.current()) await loadConversations()
    } catch (error) {
      if (guard.current()) saveWarning.value = `回答已完成，但历史保存失败：${errorText(error, '请稍后重试')}`
    } finally {
      guard.finish()
    }
  }

  function addPendingImages(images: PendingStockImage[]) {
    composerError.value = ''
    const room = Math.max(0, 4 - pendingImages.value.length)
    pendingImages.value.push(...images.slice(0, room))
    if (images.length > room) composerError.value = '每轮最多上传 4 张图片'
  }

  function removePendingImage(id: string) {
    pendingImages.value = pendingImages.value.filter((item) => item.id !== id)
  }

  function clearStageTimer() {
    if (stageTimer) clearInterval(stageTimer)
    stageTimer = null
  }

  async function send(question = prompt.value) {
    if (streaming.value) return
    const text = String(question || '').trim()
    composerError.value = ''
    chatError.value = ''
    saveWarning.value = ''
    if (mode.value === 'free') materialReport.value = null
    if (mode.value === 'stock' && !canAsk.value) {
      codeError.value = core.value?.recognized === false
        ? '当前代码未通过 A 股名录核验，不能生成问股动作'
        : '请先输入并查询一只 6 位 A 股代码'
      return
    }
    if (!text && pendingImages.value.length === 0) {
      composerError.value = '请输入问题，或上传一张需要解读的图片'
      return
    }

    const requestMode = mode.value
    const code = requestMode === 'stock' ? selectedCode.value : ''
    const images = pendingImages.value.map((item) => item.dataUrl)
    const perspectiveIds = requestMode === 'stock' ? [...selectedPerspectiveIds.value] : []
    const perspectiveMeta = requestMode === 'stock'
      ? selectedPerspectives.value.map((item) => ({ ...item })) : []
    const dataMaterialIds = requestMode === 'free' ? [...selectedDataMaterialIds.value] : []
    const history = messages.value
      .filter((item) => item.state === 'complete' && (item.role === 'user' || item.role === 'assistant'))
      .slice(-10)
      .map((item) => ({ role: item.role, content: item.content }))
    const userText = text || (requestMode === 'stock'
      ? '请结合已选择的股票和上传图片做盘中分析。'
      : '请结合上传图片与本轮选定材料回答。')
    messages.value.push({
      id: messageId('user'), role: 'user', content: userText, code: code || undefined,
      imageCount: images.length, ts: nowLabel(), state: 'complete',
    })
    const assistant: StockAskMessage = {
      id: messageId('assistant'), role: 'assistant', content: '', code: code || undefined,
      ts: nowLabel(), perspectives: perspectiveMeta, state: 'streaming',
    }
    messages.value.push(assistant)
    prompt.value = ''
    pendingImages.value = []
    stageText.value = requestMode === 'free'
      ? '正在判断问题类型并准备本轮上下文…'
      : (perspectiveIds.length
        ? `正在核验行情、证据与风险闸，并准备 ${perspectiveIds.length} 个分析视角…`
        : '正在核验行情、证据与风险闸…')
    stageSeconds.value = 0
    clearStageTimer()
    stageTimer = setInterval(() => { stageSeconds.value += 1 }, 1000)
    stopRequested = false
    streaming.value = true
    const guard = requestGuard('chat', 100_000)
    let completed = false
    let completedMaterialReport: DataMaterialReport | null = null

    try {
      await postEventStream('/chat', {
        mode: requestMode, message: userText, code, industry: '', history, images,
        perspective_ids: perspectiveIds,
        ...(requestMode === 'free' ? { data_material_ids: dataMaterialIds } : {}),
      }, {
        signal: guard.controller.signal,
        onStage(stage) {
          if (guard.current() && assistant.state === 'streaming') stageText.value = stage
        },
        onDelta(delta) {
          if (!guard.current()) return
          assistant.content += delta
        },
        onDone(payload) {
          if (!guard.current()) return
          completed = true
          if (requestMode === 'free' && payload.data_materials && typeof payload.data_materials === 'object') {
            completedMaterialReport = payload.data_materials as unknown as DataMaterialReport
            materialReport.value = completedMaterialReport
            assistant.dataMaterials = completedMaterialReport
          }
          if (payload.code) assistant.code = String(payload.code)
        },
        onError(payload) {
          if (!guard.current() || requestMode !== 'free') return
          if (payload.data_materials && typeof payload.data_materials === 'object') {
            materialReport.value = payload.data_materials as unknown as DataMaterialReport
          }
        },
      })
      if (!guard.current()) return
      if (!completed || !assistant.content.trim()) throw new Error('回答流未返回完整内容，已拒绝保存')
      assistant.state = 'complete'
      await saveExchange(
        userText, images.length, assistant.content, code, perspectiveIds,
        completedMaterialReport,
      )
    } catch (error) {
      if (!guard.current()) return
      const aborted = error instanceof DOMException && error.name === 'AbortError'
      if (assistant.content.trim()) {
        assistant.state = 'partial'
        chatError.value = stopRequested
          ? '已停止生成；部分内容未保存，也不会进入下一轮上下文。'
          : '回答未完整结束；部分内容已隔离，不作为交易或后续对话依据。'
      } else {
        messages.value = messages.value.filter((item) => item !== assistant)
        chatError.value = aborted && stopRequested
          ? `已停止本次${requestMode === 'free' ? '自由问答' : '问股'}。`
          : errorText(error, requestMode === 'free' ? '自由问答暂不可用，请稍后重试' : '盘中问股暂不可用，请稍后重试')
      }
    } finally {
      const isCurrentChat = guard.current()
      guard.finish()
      if (alive && isCurrentChat) {
        clearStageTimer()
        streaming.value = false
        stageText.value = ''
        stageSeconds.value = 0
      }
    }
  }

  function stop() {
    if (!streaming.value) return
    stopRequested = true
    controllers.chat?.abort()
  }

  async function refresh() {
    if (streaming.value) {
      abortRequest('chat')
      streaming.value = false
      clearStageTimer()
    }
    const tasks: Promise<unknown>[] = [loadConversations(), loadPerspectives(), loadDataMaterials()]
    if (selectedCode.value) tasks.push(loadStock(selectedCode.value))
    await Promise.allSettled(tasks)
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible' && !streaming.value) void loadConversations()
  }

  watch(prompt, () => {
    if (mode.value !== 'free' || streaming.value) return
    if (materialReport.value) {
      materialReport.value = null
      materialsAdjusted.value = false
    }
    if (!materialsAdjusted.value) scheduleMaterialPreselection()
  })

  onMounted(async () => {
    alive = true
    conversationCodes = loadConversationCodes()
    document.addEventListener('visibilitychange', onVisibilityChange)
    const pendingCode = sessionStorage.getItem('hermes.chat.code') || ''
    const pendingPrompt = sessionStorage.getItem('hermes.chat.prompt') || ''
    sessionStorage.removeItem('hermes.chat.code')
    sessionStorage.removeItem('hermes.chat.prompt')
    if (CODE_RE.test(pendingCode)) mode.value = 'stock'
    await Promise.all([loadConversations(), loadPerspectives(), loadDataMaterials()])
    if (CODE_RE.test(pendingCode)) {
      await loadStock(pendingCode)
      if (pendingPrompt && core.value?.recognized !== false) {
        prompt.value = pendingPrompt
        await send()
      }
    }
  })

  onBeforeUnmount(() => {
    alive = false
    clearStageTimer()
    if (materialPreselectTimer) clearTimeout(materialPreselectTimer)
    materialPreselectTimer = null
    document.removeEventListener('visibilitychange', onVisibilityChange)
    ;(Object.keys(controllers) as StockAskRequestKey[]).forEach(abortRequest)
    pendingImages.value = []
  })

  return {
    mode, codeInput, selectedCode, prompt, core, enrichment, quote, stockName,
    coreLoading, enrichmentLoading, stockLoading, coreError, enrichmentError, codeError,
    composerError, chatError, saveWarning, conversations, conversationsLoading,
    conversationError, activeConversationId, messages, pendingImages, streaming,
    stageText, stageSeconds, refreshing, canAsk, perspectives, selectedPerspectiveIds,
    selectedPerspectives, maxSelectedPerspectives, perspectivesLoading, perspectiveError,
    dataMaterials, requiredMaterials, selectedDataMaterialIds, preselectedDataMaterialIds,
    maxSelectedDataMaterials, materialsAdjusted, materialReport, materialsLoading, materialError,
    loadStock, loadConversations, loadPerspectives, togglePerspective, clearPerspectives,
    loadDataMaterials, toggleDataMaterial, resetDataMaterials, switchMode,
    newConversation, switchConversation, deleteConversation, addPendingImages,
    removePendingImage, send, stop, refresh,
  }
}
