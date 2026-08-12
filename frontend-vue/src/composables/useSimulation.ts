import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ApiError, getJson, postJson } from '../services/api'
import type {
  SimulationAccount,
  SimulationAccountPayload,
  SimulationMutationResponse,
  SimulationOrder,
  SimulationOrderIntent,
  SimulationOrdersResponse,
  SimulationStatus,
} from '../types/simulation'

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消'
  return error instanceof Error ? error.message : fallback
}

function requestId(scope: string): { key: string; value: string } {
  const safeScope = scope.replace(/[^A-Za-z0-9._:-]/g, '-').slice(0, 100)
  const key = `wudi.simulation.request.${safeScope}`
  let value = sessionStorage.getItem(key)
  if (!value) {
    const token = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`
    value = `web:${safeScope}:${token}`.slice(0, 160)
    sessionStorage.setItem(key, value)
  }
  return { key, value }
}

function clearRequest(entry: { key: string }) {
  sessionStorage.removeItem(entry.key)
}

function assertSimulationBoundary(payload: SimulationStatus | SimulationOrdersResponse | SimulationMutationResponse | SimulationAccount) {
  if (payload.simulation_only !== true) throw new ApiError('服务端未确认本地模拟模式，页面已停止操作')
  if ('broker_connected' in payload && payload.broker_connected !== false) {
    throw new ApiError('服务端返回了非预期的券商连接状态，页面已停止操作')
  }
}

export function useSimulation() {
  const status = ref<SimulationStatus | null>(null)
  const account = ref<SimulationAccount | null>(null)
  const orders = ref<SimulationOrder[]>([])
  const loading = ref(true)
  const refreshing = ref(false)
  const loaded = ref(false)
  const loadError = ref('')
  const actionError = ref('')
  const feedback = ref('')
  const actionBusy = ref('')
  let loadController: AbortController | null = null
  let actionController: AbortController | null = null
  let alive = false

  const pageBusy = computed(() => refreshing.value || Boolean(actionBusy.value))

  async function load() {
    loadController?.abort()
    const controller = new AbortController()
    loadController = controller
    refreshing.value = true
    if (!loaded.value) loading.value = true
    try {
      const [nextStatus, nextAccount, nextOrders] = await Promise.all([
        getJson<SimulationStatus>('/simulation/status', controller.signal),
        getJson<SimulationAccount>('/simulation/account?curve_limit=365', controller.signal),
        getJson<SimulationOrdersResponse>('/simulation/orders?limit=100', controller.signal),
      ])
      if (!alive || controller !== loadController) return
      assertSimulationBoundary(nextStatus)
      assertSimulationBoundary(nextAccount)
      assertSimulationBoundary(nextOrders)
      if (!Array.isArray(nextOrders.items)) throw new ApiError('模拟订单接口未返回可识别的 items 数组')
      status.value = nextStatus
      account.value = nextAccount
      orders.value = nextOrders.items
      loadError.value = ''
      loaded.value = true
    } catch (caught) {
      if (!alive || controller !== loadController || (caught instanceof DOMException && caught.name === 'AbortError')) return
      loadError.value = errorMessage(caught, '模拟执行账本读取失败')
      if (!loaded.value) {
        status.value = null
        account.value = null
        orders.value = []
      }
    } finally {
      if (controller === loadController) loadController = null
      if (alive) {
        loading.value = false
        refreshing.value = false
      }
    }
  }

  async function perform(
    scope: string,
    path: string,
    payload: Record<string, unknown>,
    successMessage: (response: SimulationMutationResponse) => string,
  ): Promise<boolean> {
    if (actionBusy.value) return false
    actionController?.abort()
    const controller = new AbortController()
    const request = requestId(scope)
    actionController = controller
    actionBusy.value = scope
    actionError.value = ''
    feedback.value = ''
    try {
      const response = await postJson<SimulationMutationResponse>(path, {
        client_request_id: request.value,
        ...payload,
      }, controller.signal)
      if (!alive || controller !== actionController) return false
      assertSimulationBoundary(response)
      clearRequest(request)
      await load()
      if (!alive || controller !== actionController) return false
      feedback.value = successMessage(response)
      return true
    } catch (caught) {
      if (!alive || controller !== actionController || (caught instanceof DOMException && caught.name === 'AbortError')) return false
      if (caught instanceof ApiError && caught.status === 409) {
        clearRequest(request)
        await load()
      }
      actionError.value = errorMessage(caught, '模拟执行操作失败')
      return false
    } finally {
      if (controller === actionController) actionController = null
      if (alive) actionBusy.value = ''
    }
  }

  function propose(intent: SimulationOrderIntent): Promise<boolean> {
    const scope = `create:${intent.symbol}:${intent.side}:${intent.order_type}:${intent.quantity}:${intent.limit_price ?? 'market'}`
    return perform(scope, '/simulation/orders', intent as unknown as Record<string, unknown>, (response) => (
      response.order?.status === 'rejected'
        ? `订单意图已拒绝：${response.order.rejection_reason || '服务端未返回具体原因'}。`
        : '订单意图已进入待人工批准状态。'
    ))
  }

  function approve(order: SimulationOrder, approvedBy: string): Promise<boolean> {
    return perform(`approve:${order.order_id}`, `/simulation/orders/${encodeURIComponent(order.order_id)}/approve`, {
      approved_by: approvedBy,
    }, () => '模拟订单已由人工批准，尚未成交。')
  }

  function fill(order: SimulationOrder): Promise<boolean> {
    return perform(`fill:${order.order_id}`, `/simulation/orders/${encodeURIComponent(order.order_id)}/fill`, {}, (response) => (
      response.order?.status === 'rejected'
        ? `模拟成交已拒绝：${response.order.rejection_reason || '服务端未返回具体原因'}。`
        : '订单已按服务端重新读取的行情完成模拟成交。'
    ))
  }

  function cancel(order: SimulationOrder): Promise<boolean> {
    return perform(`cancel:${order.order_id}`, `/simulation/orders/${encodeURIComponent(order.order_id)}/cancel`, {
      reason: 'operator_cancelled_from_vue',
    }, () => '模拟订单已取消。')
  }

  function emergencyStop(reason: string): Promise<boolean> {
    return perform('emergency-stop', '/simulation/emergency-stop', { reason }, () => (
      '紧急停止已开启，所有待处理模拟订单已取消。'
    ))
  }

  function resetEmergencyStop(approvedBy: string, reason: string): Promise<boolean> {
    return perform('emergency-reset', '/simulation/emergency-stop/reset', {
      approved_by: approvedBy,
      reason,
    }, () => '紧急停止已由人工复核解除；此前取消的订单不会恢复。')
  }

  function initializeAccount(payload: SimulationAccountPayload): Promise<boolean> {
    return perform('account-initialize', '/simulation/account', payload as unknown as Record<string, unknown>, () => (
      '模拟账户已创建，初始资金已经锁定并写入资产曲线。'
    ))
  }

  function updateAccountSettings(payload: SimulationAccountPayload): Promise<boolean> {
    return perform('account-settings', '/simulation/account/settings', payload as unknown as Record<string, unknown>, () => (
      '费用与滑点设置已更新，只影响后续模拟成交。'
    ))
  }

  function recordValuation(): Promise<boolean> {
    return perform('account-valuation', '/simulation/account/valuation', {}, () => (
      '已按服务端当前可用行情追加一个资产曲线点。'
    ))
  }

  function clearActionMessages() {
    actionError.value = ''
    feedback.value = ''
  }

  onMounted(() => {
    alive = true
    void load()
  })

  onBeforeUnmount(() => {
    alive = false
    loadController?.abort()
    actionController?.abort()
  })

  return {
    status,
    account,
    orders,
    loading,
    refreshing,
    loaded,
    loadError,
    actionError,
    feedback,
    actionBusy,
    pageBusy,
    load,
    propose,
    approve,
    fill,
    cancel,
    emergencyStop,
    resetEmergencyStop,
    initializeAccount,
    updateAccountSettings,
    recordValuation,
    clearActionMessages,
  }
}
