<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type {
  DeliveryOutboxItem, DeliveryOutboxResponse, DeliveryReadinessResponse,
  DeliveryReceiptsResponse, SettingsView,
} from '../types/settings'
import { receiptStatus, textValue } from '../utils/settingsFormat'

const props = defineProps<{
  settings: SettingsView | null
  readiness: DeliveryReadinessResponse | null
  receipts: DeliveryReceiptsResponse | null
  outbox: DeliveryOutboxResponse | null
  loading: { settings: boolean; readiness: boolean; receipts: boolean; outbox: boolean }
  errors: { settings: string; readiness: string; receipts: string; outbox: string }
  writable: boolean
  busy: boolean
  pushTesting: boolean
  deliveryBusy: boolean
}>()
const emit = defineEmits<{
  save: [payload: Record<string, unknown>]
  testPush: []
  acknowledge: [deliveryId: string, note: string]
  retryDelivery: [deliveryId: string, ambiguous: boolean, note: string]
  retry: []
}>()

const webhook = ref('')
const secret = ref('')
const pushEnabled = ref(false)
const reviewNotes = reactive<Record<string, string>>({})
watch(() => props.settings, (value) => {
  webhook.value = ''
  secret.value = ''
  pushEnabled.value = value?.push_enabled === true
}, { immediate: true, deep: true })

const pushVia = computed(() => String(props.settings?.push_via || 'feishu').toLowerCase())
const channels = computed(() => Array.isArray(props.readiness?.channels) ? props.readiness.channels : null)
const receiptRows = computed(() => Array.isArray(props.receipts?.receipts) ? props.receipts.receipts : null)
const outboxRows = computed(() => Array.isArray(props.outbox?.items) ? props.outbox.items : null)
const manualRows = computed(() => outboxRows.value?.filter((row) => ['sending', 'unknown', 'partial', 'failed_terminal'].includes(String(row.status || ''))) || [])

const blockerLabels: Record<string, string> = {
  scheduled_push_disabled: '自动发送尚未开启',
  push_channel_unconfigured: '还没有设置报告接收渠道',
  push_target_unconfigured: '还没有选择报告接收目标',
  delivery_manual_review_required: '有发送记录需要人工确认',
  preflight_failed: '发送渠道检查失败',
}

function readinessLabel() {
  if (!pushEnabled.value) return '自动发送已关闭'
  if (props.readiness?.launch_ready === true) return '自动发送已就绪'
  if (props.readiness) return '自动发送需要配置或复核'
  return '发送状态暂不可核验'
}

function channelState(channel: NonNullable<DeliveryReadinessResponse['channels']>[number]) {
  if (channel.configured !== true) return '未配置'
  if (channel.enabled === false) return '已配置 · 未开启'
  if (channel.state === 'ready' || channel.status === 'ready') return channel.last_success_at || channel.last_success ? '最近成功' : '能力可用 · 待首次发送'
  if (channel.state === 'needs_attention') return '需要检查配置'
  return '已配置'
}

function saveDelivery() {
  const payload: Record<string, unknown> = { push_enabled: pushEnabled.value }
  if (webhook.value.trim()) payload.feishu_webhook = webhook.value.trim()
  if (secret.value.trim()) payload.feishu_secret = secret.value.trim()
  emit('save', payload)
}

function noteFor(item: DeliveryOutboxItem) {
  return reviewNotes[String(item.delivery_id || '')] || ''
}

function submitAcknowledgement(item: DeliveryOutboxItem) {
  const id = String(item.delivery_id || '')
  const note = noteFor(item).trim()
  if (id && note.length >= 3) emit('acknowledge', id, note)
}

function submitRetry(item: DeliveryOutboxItem) {
  const id = String(item.delivery_id || '')
  const note = noteFor(item).trim()
  const ambiguous = ['sending', 'unknown', 'partial'].includes(String(item.status || ''))
  if (id && note.length >= 3) emit('retryDelivery', id, ambiguous, note)
}
</script>

<template>
  <section id="settings-delivery" class="settings-delivery panel" aria-labelledby="settings-delivery-title">
    <header class="settings-delivery__heading">
      <div><p class="eyebrow">DELIVERY</p><h2 id="settings-delivery-title">交付与推送</h2><p>设置接收渠道，并用追加式回执核对真实发送结果。</p></div>
      <span class="settings-state" :class="pushEnabled && readiness?.launch_ready === true ? 'is-success' : (pushEnabled ? 'is-warning' : 'is-neutral')">{{ readinessLabel() }}</span>
    </header>

    <div class="settings-delivery__grid">
      <form class="settings-subpanel" @submit.prevent="saveDelivery">
        <div class="settings-subpanel__title"><h3>接收设置</h3><span>{{ pushVia === 'gateway' ? '可选集成网关' : '飞书 Webhook' }}</span></div>
        <div v-if="!settings && loading.settings" class="settings-local-state" role="status">正在读取发送设置…</div>
        <div v-else-if="!settings" class="settings-local-state is-error" role="alert">设置读取失败，保存已锁定。</div>
        <template v-else>
          <p v-if="pushVia === 'gateway'" class="settings-note">渠道与目标由集成网关单独管理；本页只控制是否自动发送并读取服务端就绪状态。</p>
          <template v-else>
            <label for="settings-webhook"><span>Webhook 地址</span><input id="settings-webhook" v-model="webhook" type="url" autocomplete="off" :placeholder="settings.feishu_webhook_set ? '已安全保存；留空保持不变' : 'https://open.feishu.cn/open-apis/bot/v2/hook/…'" :disabled="busy" /><small>{{ settings.feishu_webhook_set ? '当前已配置，明文不会回显。' : '未配置；仅允许飞书/Lark 官方 HTTPS 机器人地址。' }}</small></label>
            <label for="settings-secret"><span>签名密钥（可选）</span><input id="settings-secret" v-model="secret" type="password" autocomplete="new-password" :placeholder="settings.feishu_secret_set ? '已安全保存；留空保持不变' : '未配置'" :disabled="busy" /><small>{{ settings.feishu_secret_set ? '当前已配置，输入新值才会覆盖。' : '未配置签名密钥。' }}</small></label>
          </template>
          <label class="settings-switch" for="settings-push-enabled"><input id="settings-push-enabled" v-model="pushEnabled" type="checkbox" :disabled="busy" /><span><strong>自动发送报告</strong><small>{{ pushEnabled ? '开启后仍需通过渠道、目标与待复核事务检查。' : '关闭时报告只保留在系统内。' }}</small></span></label>
          <div class="settings-actions"><button class="primary-button" type="submit" :disabled="!writable || busy">{{ busy ? '保存中…' : '保存发送设置' }}</button><button type="button" :disabled="pushTesting || busy" @click="emit('testPush')">{{ pushTesting ? '提交中…' : '发送测试消息' }}</button></div>
        </template>
      </form>

      <article class="settings-subpanel">
        <div class="settings-subpanel__title"><h3>自动发送状态</h3><span>配置状态 ≠ 已送达</span></div>
        <div v-if="!readiness && loading.readiness" class="settings-local-state" role="status">正在执行只读就绪检查…</div>
        <div v-else-if="!readiness" class="settings-local-state is-error" role="alert">{{ errors.readiness || '就绪接口未返回。' }}<button type="button" @click="emit('retry')">重新检查</button></div>
        <template v-else>
          <dl class="settings-readiness-list"><div><dt>接收渠道</dt><dd>{{ textValue(readiness.channel, '尚未设置') }}</dd></div><div><dt>接收目标</dt><dd>{{ Array.isArray(readiness.targets) && readiness.targets.length ? readiness.targets.join('、') : '尚未选择' }}</dd></div><div><dt>待人工确认</dt><dd>{{ readiness.manual_review_count == null ? '未提供' : `${readiness.manual_review_count} 条` }}</dd></div></dl>
          <ul v-if="Array.isArray(readiness.blockers) && readiness.blockers.length" class="settings-blockers"><li v-for="blocker in readiness.blockers" :key="blocker">{{ blockerLabels[blocker] || '自动发送配置还需要完善' }} <code>{{ blocker }}</code></li></ul>
          <p v-else-if="readiness.launch_ready === true" class="settings-ok">渠道、目标与待复核事务检查通过。</p>
          <p v-else class="settings-note">接口未提供阻断码，不能据此推断自动发送已就绪。</p>
        </template>
      </article>
    </div>

    <section class="settings-delivery-block" aria-labelledby="settings-channel-title">
      <h3 id="settings-channel-title">分渠道状态</h3>
      <div v-if="channels?.length" class="settings-channel-grid">
        <article v-for="channel in channels" :key="String(channel.key || channel.label || 'channel')"><span>{{ textValue(channel.label, textValue(channel.key)) }}</span><strong>{{ channelState(channel) }}</strong><small>{{ textValue(channel.last_success_at || channel.last_success, '尚无成功记录') }}</small></article>
      </div>
      <p v-else-if="channels && channels.length === 0" class="settings-empty">服务端返回真实空渠道列表；未推断 Web、PDF 或外部消息能力。</p>
      <p v-else class="settings-note">服务端未返回可验证的 channels 数组，分渠道状态不可用。</p>
    </section>

    <section v-if="!outboxRows || manualRows.length" class="settings-delivery-block" aria-labelledby="settings-review-title">
      <h3 id="settings-review-title">待人工核验事务</h3>
      <div v-if="!outboxRows && loading.outbox" class="settings-local-state" role="status">正在读取交付事务…</div>
      <div v-else-if="!outboxRows" class="settings-local-state is-error" role="alert">{{ errors.outbox || '交付事务接口未返回可验证的 items 数组。' }}</div>
      <template v-else>
        <p class="settings-note">`sending / unknown / partial` 可能已部分送达。填写核验依据后再确认或重试，避免重复消息。</p>
        <div class="settings-review-grid">
          <article v-for="item in manualRows" :key="String(item.delivery_id || item.content_id || 'delivery')">
            <div><strong>{{ textValue(item.content_id, textValue(item.delivery_id)) }}</strong><span class="settings-state is-warning">{{ textValue(item.status) }}</span></div>
            <label :for="`review-${item.delivery_id}`">人工核验说明<input :id="`review-${item.delivery_id}`" v-model="reviewNotes[String(item.delivery_id || '')]" type="text" minlength="3" :disabled="deliveryBusy" placeholder="填写外部平台核验依据（至少 3 个字符）" /></label>
            <div class="settings-actions"><button v-if="['sending', 'unknown', 'partial'].includes(String(item.status || ''))" type="button" :disabled="deliveryBusy || noteFor(item).trim().length < 3" @click="submitAcknowledgement(item)">确认外部已接收</button><button type="button" class="danger-button" :disabled="deliveryBusy || noteFor(item).trim().length < 3" @click="submitRetry(item)">核验后重试</button></div>
          </article>
        </div>
      </template>
    </section>

    <section class="settings-delivery-block" aria-labelledby="settings-receipts-title">
      <h3 id="settings-receipts-title">交付回执</h3>
      <div v-if="!receiptRows && loading.receipts" class="settings-local-state" role="status">正在读取追加式回执…</div>
      <div v-else-if="!receiptRows" class="settings-local-state is-error" role="alert">{{ errors.receipts || '回执接口未返回可验证的 receipts 数组。' }}</div>
      <p v-else-if="receiptRows.length === 0" class="settings-empty">服务端返回真实空数组：暂无交付尝试记录。</p>
      <div v-else class="settings-table-wrap" tabindex="0" aria-label="交付回执表，可横向滚动"><table><thead><tr><th>内容</th><th>渠道</th><th>尝试</th><th>状态</th><th>发送时点</th><th>错误</th></tr></thead><tbody><tr v-for="(receipt, index) in receiptRows" :key="receipt.delivery_id || `${receipt.content_id}-${index}`"><td><strong>{{ textValue(receipt.content_id) }}</strong></td><td>{{ textValue(receipt.channel) }}</td><td class="mono">{{ receipt.attempt == null ? '未提供' : `#${receipt.attempt}` }}</td><td><span class="settings-state" :class="`is-${receiptStatus(receipt).tone}`">{{ receiptStatus(receipt).label }}</span></td><td class="mono">{{ textValue(receipt.sent_at || receipt.attempted_at || receipt.updated_at) }}</td><td>{{ textValue(receipt.error || receipt.error_code, '无已知错误') }}</td></tr></tbody></table></div>
    </section>
  </section>
</template>

<style scoped>
.settings-delivery { min-width: 0; overflow: hidden; }.settings-delivery__heading { display: flex; min-height: 72px; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); }.settings-delivery__heading h2, .settings-subpanel__title h3, .settings-delivery-block h3 { margin: 0; }.settings-delivery__heading h2 { font-size: 19px; }.settings-delivery__heading p:last-child { margin: 2px 0 0; color: var(--text-3); font-size: 13px; }
.settings-delivery__grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 12px; }.settings-subpanel { min-width: 0; padding: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }.settings-subpanel__title { display: flex; align-items: center; justify-content: space-between; gap: 8px; }.settings-subpanel__title span { color: var(--text-3); font-size: 12px; }.settings-subpanel > label:not(.settings-switch), .settings-review-grid label { display: grid; gap: 5px; margin-top: 10px; color: var(--text-2); font-size: 13px; }.settings-subpanel input[type="url"], .settings-subpanel input[type="password"], .settings-review-grid input { width: 100%; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); }.settings-subpanel label small { color: var(--text-3); font-size: 12px; }
.settings-switch { display: grid; grid-template-columns: 44px minmax(0, 1fr); align-items: center; gap: 8px; min-height: 52px; margin-top: 10px; cursor: pointer; }.settings-switch input { width: 20px; height: 20px; justify-self: center; }.settings-switch span, .settings-switch small { display: block; }.settings-switch small { color: var(--text-3); }
.settings-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }.settings-actions button, .settings-local-state button { min-height: 44px; padding: 7px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); cursor: pointer; }.settings-actions .primary-button { border-color: var(--accent); background: var(--accent); color: #111; }.settings-actions .danger-button { color: var(--danger); }.settings-actions button:disabled { cursor: wait; opacity: .55; }
.settings-local-state { min-height: 82px; padding: 16px 0; color: var(--text-2); }.settings-local-state.is-error { color: var(--danger); }.settings-local-state button { display: block; margin-top: 8px; }.settings-note, .settings-empty, .settings-ok { margin: 10px 0 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text-2); font-size: 13px; }.settings-ok { color: var(--info); }
.settings-readiness-list { display: grid; gap: 7px; margin: 10px 0; }.settings-readiness-list div { display: grid; grid-template-columns: 118px minmax(0, 1fr); gap: 8px; padding-bottom: 7px; border-bottom: 1px solid var(--border); }.settings-readiness-list dt { color: var(--text-3); }.settings-readiness-list dd { margin: 0; overflow-wrap: anywhere; }.settings-blockers { display: grid; gap: 5px; margin: 8px 0 0; padding-left: 20px; color: var(--warning); }.settings-blockers code { color: var(--text-3); }
.settings-delivery-block { margin: 0 12px 12px; padding: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }.settings-channel-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }.settings-channel-grid article { min-width: 0; padding: 9px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); }.settings-channel-grid span, .settings-channel-grid small { display: block; color: var(--text-3); font-size: 12px; }.settings-channel-grid strong { display: block; margin: 3px 0; }
.settings-review-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }.settings-review-grid article { min-width: 0; padding: 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); }.settings-review-grid article > div:first-child { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.settings-table-wrap { max-width: 100%; overflow-x: auto; margin-top: 10px; border: 1px solid var(--border); border-radius: 6px; }table { width: 100%; min-width: 840px; border-collapse: collapse; }th, td { padding: 9px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; font-size: 13px; }th { background: var(--surface); color: var(--text-3); font-size: 12px; }
.settings-state { display: inline-flex; min-height: 28px; align-items: center; padding: 2px 8px; border: 1px solid currentColor; border-radius: 99px; font-size: 12px; font-weight: 700; }.settings-state.is-success { color: var(--info); }.settings-state.is-warning { color: var(--warning); }.settings-state.is-danger { color: var(--danger); }.settings-state.is-neutral { color: var(--text-2); }.mono, code { font-family: "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }
@media (max-width: 1050px) { .settings-channel-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 900px) { .settings-delivery__grid { grid-template-columns: 1fr; } }
@media (max-width: 720px) { .settings-delivery__heading { align-items: flex-start; flex-direction: column; }.settings-review-grid, .settings-channel-grid { grid-template-columns: 1fr; }.settings-actions button { flex: 1 1 150px; } }
</style>
