<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import StockAskComposer from './StockAskComposer.vue'
import StockAskMessage from './StockAskMessage.vue'
import StockAskProfileForm from './StockAskProfileForm.vue'
import StockAutocomplete from './StockAutocomplete.vue'
import type { PendingStockImage, StockAskMessage as Message, StockAskMode } from '../types/stockAsk'
import type { StockAskProfile } from '../types/stockAskProfile'

const props = defineProps<{
  messages: Message[]
  mode: StockAskMode
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
  stockAskProfile: StockAskProfile
  quotePrice?: number | null
}>()

const emit = defineEmits<{
  codeInput: [value: string]
  submitCode: []
  'update:prompt': [value: string]
  addImages: [images: PendingStockImage[]]
  removeImage: [id: string]
  send: []
  stop: []
  'update:stockAskProfile': [value: StockAskProfile]
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

const codeModel = computed({
  get: () => props.codeInput,
  set: (value: string) => emit('codeInput', value),
})
</script>

<template>
  <section class="stock-ask-conversation panel" :class="{ 'is-free': mode === 'free' }" aria-labelledby="stock-ask-conversation-title">
    <header>
      <div class="stock-ask-conversation__heading">
        <p v-if="mode === 'stock'" class="eyebrow">GOVERNED STOCK Q&amp;A</p>
        <h2 id="stock-ask-conversation-title">{{ mode === 'free' ? '自由问答' : '盘中问股' }}</h2>
        <p v-if="mode === 'free'" class="stock-ask-conversation__subtitle">不必预先选择股票；系统按问题决定是否注入市场或个股证据。</p>
      </div>
      <div v-if="mode === 'free'" class="stock-ask-conversation__model">
        <span>发送时核验模型</span><small>使用当前系统模型连接</small>
      </div>
      <form v-else class="stock-ask-conversation__selector" novalidate @submit.prevent="emit('submitCode')">
        <label for="stock-ask-code">六位 A 股代码</label>
        <div class="stock-ask-conversation__selector-controls">
          <StockAutocomplete
            v-model="codeModel"
            input-id="stock-ask-code"
            placeholder="代码 / 中文 / 拼音 / 首字母"
            :aria-invalid="Boolean(codeError)"
            :aria-describedby="codeError || selectedCode ? 'stock-ask-code-help' : ''"
            :display-name="stockName"
            :disabled="streaming"
          />
          <button type="submit" :disabled="stockLoading || streaming">{{ stockLoading ? '查询中' : '查询标的' }}</button>
        </div>
        <p v-if="codeError || selectedCode" id="stock-ask-code-help" :class="{ 'is-error': codeError }" :role="codeError ? 'alert' : undefined">
          {{ codeError || `当前锁定 ${stockName || '名称待返回'} ${selectedCode}` }}
        </p>
      </form>
    </header>

    <StockAskProfileForm
      v-if="mode === 'stock'"
      :model-value="stockAskProfile"
      :quote-price="quotePrice"
      :disabled="streaming"
      @update:model-value="emit('update:stockAskProfile', $event)"
    />

    <div
      ref="messageList"
      class="stock-ask-conversation__messages"
      role="log"
      aria-live="polite"
      :aria-label="mode === 'free' ? '自由问答对话记录' : '盘中问股对话记录'"
      tabindex="0"
      @scroll="updateMessageFollowState"
    >
      <div v-if="messages.length === 0" class="stock-ask-conversation__empty">
        <span class="stock-ask-conversation__mark" aria-hidden="true">问</span>
        <h3>{{ mode === 'free' ? '直接提出一个问题' : selectedCode ? '围绕当前标的提出一个明确问题' : '先选择一只股票' }}</h3>
        <p v-if="mode === 'free'">可询问市场状态、研究方法，或输入一只股票的六位代码/完整名称。</p>
        <p v-else-if="selectedCode">服务端先采集五类真实证据，再执行六桶评分、A—E 状态机、RiskGate 和建议留痕。</p>
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
      :mode="mode"
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
.stock-ask-conversation.is-free { grid-template-rows: auto minmax(96px, 1fr) auto auto; }
.stock-ask-conversation > header { display: flex; min-height: 72px; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.stock-ask-conversation__heading { flex: 0 0 auto; }
.stock-ask-conversation__subtitle { margin: 2px 0 0; color: var(--text-3); font-size: 12px; }
.stock-ask-conversation__model { display: grid; justify-items: end; gap: 3px; }
.stock-ask-conversation__model span { padding: 3px 8px; border: 1px solid var(--border); border-radius: 999px; color: var(--text-2); font-size: 12px; font-weight: 700; }
.stock-ask-conversation__model small { color: var(--text-3); font-size: 11px; }
.stock-ask-conversation h2 { margin: 0; font-size: 18px; }
.stock-ask-conversation__selector { display: grid; min-width: min(100%, 430px); grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 3px 8px; }
.stock-ask-conversation__selector label { color: var(--text-2); font-size: 12px; font-weight: 700; }
.stock-ask-conversation__selector-controls { display: grid; grid-template-columns: minmax(210px, 260px) auto; align-items: start; gap: 8px; }
.stock-ask-conversation__selector button { min-height: 44px; border: 1px solid var(--border-strong); border-radius: 8px; }
.stock-ask-conversation__selector button { padding: 0 14px; border-color: var(--accent); background: var(--accent); color: #111a27; font-weight: 760; cursor: pointer; white-space: nowrap; }
:global(:root[data-theme="light"] .stock-ask-conversation__selector button) { color: #fff; }
.stock-ask-conversation__selector button:disabled { cursor: wait; opacity: .55; }
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
  .stock-ask-conversation__selector button { min-height: 52px; }
  .stock-ask-conversation__selector > p { grid-column: 1; text-align: left; }
  .stock-ask-conversation__model { justify-items: start; }
}

@media (max-width: 430px) {
  .stock-ask-conversation__selector-controls { grid-template-columns: minmax(0, 1fr); }
}
</style>
