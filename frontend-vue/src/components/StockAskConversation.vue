<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import StockAskComposer from './StockAskComposer.vue'
import StockAskMessage from './StockAskMessage.vue'
import StockAskPerspectives from './StockAskPerspectives.vue'
import type { PendingStockImage, StockAskMessage as Message, StockPerspective } from '../types/stockAsk'

const props = defineProps<{
  messages: Message[]
  prompt: string
  pendingImages: PendingStockImage[]
  codeInput: string
  selectedCode: string
  stockName: string
  stockLoading: boolean
  codeError: string
  streaming: boolean
  stageText: string
  stageSeconds: number
  composerError: string
  chatError: string
  saveWarning: string
  canAsk: boolean
  perspectives: StockPerspective[]
  selectedPerspectiveIds: string[]
  maxSelectedPerspectives: number
  perspectivesLoading: boolean
  perspectiveError: string
}>()

const emit = defineEmits<{
  codeInput: [value: string]
  submitCode: []
  'update:prompt': [value: string]
  addImages: [images: PendingStockImage[]]
  removeImage: [id: string]
  send: []
  stop: []
  togglePerspective: [id: string]
  clearPerspectives: []
  retryPerspectives: []
}>()

const messageList = ref<HTMLElement | null>(null)
const followLatestMessage = ref(true)

function updateMessageFollowState() {
  const element = messageList.value
  if (!element) return
  followLatestMessage.value = element.scrollHeight - element.scrollTop - element.clientHeight <= 64
}

async function scrollToLatestMessage(force = false) {
  await nextTick()
  const element = messageList.value
  if (!element || (!force && !followLatestMessage.value)) return
  element.scrollTop = element.scrollHeight
}

watch(
  () => props.messages.map((message) => `${message.id}:${message.content.length}:${message.state}`).join('|'),
  () => void scrollToLatestMessage(),
)

watch(
  () => props.messages[0]?.id,
  (current, previous) => {
    if (current === previous) return
    followLatestMessage.value = true
    void scrollToLatestMessage(true)
  },
)

function updateCodeInput(event: Event) {
  emit('codeInput', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <section class="stock-ask-conversation panel" aria-labelledby="stock-ask-conversation-title">
    <header>
      <div class="stock-ask-conversation__heading">
        <p class="eyebrow">GOVERNED STOCK Q&amp;A</p>
        <h2 id="stock-ask-conversation-title">盘中问股</h2>
      </div>
      <form class="stock-ask-conversation__selector" novalidate @submit.prevent="emit('submitCode')">
        <label for="stock-ask-code">六位 A 股代码</label>
        <div class="stock-ask-conversation__selector-controls">
          <input
            id="stock-ask-code"
            :value="props.codeInput"
            type="text"
            inputmode="numeric"
            autocomplete="off"
            maxlength="6"
            pattern="[0-9]{6}"
            placeholder="例如 600519"
            :aria-invalid="Boolean(codeError)"
            :aria-describedby="codeError || selectedCode ? 'stock-ask-code-help' : undefined"
            :disabled="streaming"
            @input="updateCodeInput"
          >
          <button type="submit" :disabled="stockLoading || streaming">{{ stockLoading ? '查询中' : '查询标的' }}</button>
        </div>
        <p v-if="codeError || selectedCode" id="stock-ask-code-help" :class="{ 'is-error': codeError }" :role="codeError ? 'alert' : undefined">
          {{ codeError || `当前锁定 ${stockName || '名称待返回'} ${selectedCode}` }}
        </p>
      </form>
    </header>

    <StockAskPerspectives
      :items="perspectives"
      :selected-ids="selectedPerspectiveIds"
      :max-selected="maxSelectedPerspectives"
      :loading="perspectivesLoading"
      :error="perspectiveError"
      :disabled="streaming"
      @toggle="emit('togglePerspective', $event)"
      @clear="emit('clearPerspectives')"
      @retry="emit('retryPerspectives')"
    />

    <div
      ref="messageList"
      class="stock-ask-conversation__messages"
      role="log"
      aria-live="polite"
      aria-label="盘中问股对话记录"
      tabindex="0"
      @scroll="updateMessageFollowState"
    >
      <div v-if="messages.length === 0" class="stock-ask-conversation__empty">
        <span class="stock-ask-conversation__mark" aria-hidden="true">问</span>
        <h3>{{ selectedCode ? '围绕当前标的提出一个明确问题' : '先选择一只股票' }}</h3>
        <p v-if="selectedCode">服务端先核验实时行情、确定性诊断、风险闸和建议留痕，再输出当前动作、失效条件与依据。</p>
      </div>
      <StockAskMessage
        v-for="message in messages"
        :key="message.id"
        :message="message"
        :stage-text="stageText"
        :stage-seconds="stageSeconds"
      />
    </div>

    <div v-if="chatError || saveWarning" class="stock-ask-conversation__feedback" :class="{ 'is-warning': saveWarning && !chatError }" role="alert">
      {{ chatError || saveWarning }}
    </div>
    <StockAskComposer
      :model-value="prompt"
      :pending-images="pendingImages"
      :streaming="streaming"
      :disabled="!canAsk"
      :error="composerError"
      @update:model-value="emit('update:prompt', $event)"
      @add-images="emit('addImages', $event)"
      @remove-image="emit('removeImage', $event)"
      @send="emit('send')"
      @stop="emit('stop')"
    />
  </section>
</template>

<style scoped>
.stock-ask-conversation { display: grid; min-width: 0; height: calc(100dvh - 148px); min-height: 620px; grid-template-rows: auto auto minmax(96px, 1fr) auto auto; overflow: hidden; }
.stock-ask-conversation > header { display: flex; min-height: 72px; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.stock-ask-conversation__heading { flex: 0 0 auto; }
.stock-ask-conversation h2 { margin: 0; font-size: 18px; }
.stock-ask-conversation__selector { display: grid; min-width: min(100%, 350px); grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 3px 8px; }
.stock-ask-conversation__selector label { color: var(--text-2); font-size: 12px; font-weight: 700; }
.stock-ask-conversation__selector-controls { display: grid; grid-template-columns: minmax(130px, 180px) auto; gap: 8px; }
.stock-ask-conversation__selector input, .stock-ask-conversation__selector button { min-height: 44px; border: 1px solid var(--border-strong); border-radius: 8px; }
.stock-ask-conversation__selector input { min-width: 0; padding: 0 10px; background: var(--surface-2); color: var(--text); font: 700 15px/1 "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; letter-spacing: .06em; }
.stock-ask-conversation__selector button { padding: 0 14px; border-color: var(--accent); background: var(--accent); color: #111a27; font-weight: 760; cursor: pointer; white-space: nowrap; }
:global(:root[data-theme="light"] .stock-ask-conversation__selector button) { color: #fff; }
.stock-ask-conversation__selector button:disabled, .stock-ask-conversation__selector input:disabled { cursor: wait; opacity: .55; }
.stock-ask-conversation__selector > p { grid-column: 2; margin: 0; color: var(--text-3); font-size: 11px; text-align: right; }
.stock-ask-conversation__selector > p.is-error { color: var(--danger); }
.stock-ask-conversation__messages { min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; scrollbar-color: var(--border-strong) transparent; }
.stock-ask-conversation__empty { display: grid; min-height: 100%; place-content: center; justify-items: center; padding: 24px; text-align: center; }
.stock-ask-conversation__mark { display: grid; width: 52px; height: 52px; place-items: center; margin-bottom: 10px; border: 1px solid var(--accent); border-radius: 8px; background: var(--accent-dim); color: var(--accent); font: 760 20px/1 "SFMono-Regular", Consolas, monospace; }
.stock-ask-conversation__empty h3 { margin: 0 0 6px; font-size: 19px; }
.stock-ask-conversation__empty p { max-width: 580px; margin: 0; color: var(--text-3); line-height: 1.7; }
.stock-ask-conversation__feedback { margin: 0 12px 8px; padding: 9px 10px; border: 1px solid var(--danger); border-radius: 6px; color: var(--danger); font-size: 13px; }
.stock-ask-conversation__feedback.is-warning { border-color: var(--warning); color: var(--warning); }

@media (max-width: 800px) {
  .stock-ask-conversation { height: auto; min-height: 0; grid-template-rows: auto; overflow: visible; }
  .stock-ask-conversation__messages { min-height: 300px; overflow: visible; }
  .stock-ask-conversation__empty { min-height: 300px; }
}

@media (max-width: 720px) {
  .stock-ask-conversation > header { align-items: stretch; flex-direction: column; gap: 8px; }
  .stock-ask-conversation__selector { width: 100%; grid-template-columns: minmax(0, 1fr); }
  .stock-ask-conversation__selector-controls { grid-template-columns: minmax(0, 1fr) auto; }
  .stock-ask-conversation__selector input, .stock-ask-conversation__selector button { min-height: 52px; }
  .stock-ask-conversation__selector > p { grid-column: 1; text-align: left; }
}

@media (max-width: 430px) {
  .stock-ask-conversation__selector-controls { grid-template-columns: minmax(0, 1fr); }
}
</style>
