<script setup lang="ts">
import StockAskComposer from './StockAskComposer.vue'
import StockAskMessage from './StockAskMessage.vue'
import type { PendingStockImage, StockAskMessage as Message } from '../types/stockAsk'

defineProps<{
  messages: Message[]
  prompt: string
  pendingImages: PendingStockImage[]
  selectedCode: string
  stockName: string
  streaming: boolean
  stageText: string
  stageSeconds: number
  composerError: string
  chatError: string
  saveWarning: string
  canAsk: boolean
}>()

const emit = defineEmits<{
  'update:prompt': [value: string]
  addImages: [images: PendingStockImage[]]
  removeImage: [id: string]
  send: []
  stop: []
}>()
</script>

<template>
  <section class="stock-ask-conversation panel" aria-labelledby="stock-ask-conversation-title">
    <header>
      <div>
        <p class="eyebrow">GOVERNED STOCK Q&amp;A</p>
        <h2 id="stock-ask-conversation-title">盘中问股</h2>
      </div>
      <div class="stock-ask-conversation__target">
        <span>当前标的</span>
        <strong v-if="selectedCode">{{ stockName || '名称待返回' }} <b class="mono">{{ selectedCode }}</b></strong>
        <strong v-else>未选择</strong>
      </div>
    </header>

    <div class="stock-ask-conversation__messages" role="log" aria-live="polite" aria-label="盘中问股对话记录">
      <div v-if="messages.length === 0" class="stock-ask-conversation__empty">
        <span class="stock-ask-conversation__mark" aria-hidden="true">问</span>
        <h3>{{ selectedCode ? '围绕当前标的提出一个明确问题' : '先选择一只股票' }}</h3>
        <p v-if="selectedCode">服务端先核验实时行情、确定性诊断、风险闸和建议留痕，再输出当前动作、失效条件与依据。</p>
        <p v-else>本页只做单只 A 股的盘中问股，不提供自由问答、持仓判断或产业链挖掘。</p>
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
.stock-ask-conversation { min-width: 0; overflow: visible; }
.stock-ask-conversation > header { display: flex; min-height: 72px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.stock-ask-conversation h2 { margin: 0; font-size: 18px; }
.stock-ask-conversation__target { display: flex; min-width: 0; flex-direction: column; align-items: flex-end; text-align: right; }
.stock-ask-conversation__target span { color: var(--text-3); font-size: 12px; }
.stock-ask-conversation__target strong { overflow-wrap: anywhere; font-size: 14px; }
.stock-ask-conversation__target b { color: var(--accent); }
.stock-ask-conversation__messages { min-height: 380px; }
.stock-ask-conversation__empty { display: grid; min-height: 380px; place-content: center; justify-items: center; padding: 24px; text-align: center; }
.stock-ask-conversation__mark { display: grid; width: 52px; height: 52px; place-items: center; margin-bottom: 10px; border: 1px solid var(--accent); border-radius: 8px; background: var(--accent-dim); color: var(--accent); font: 760 20px/1 "SFMono-Regular", Consolas, monospace; }
.stock-ask-conversation__empty h3 { margin: 0 0 6px; font-size: 19px; }
.stock-ask-conversation__empty p { max-width: 580px; margin: 0; color: var(--text-3); line-height: 1.7; }
.stock-ask-conversation__feedback { margin: 0 12px 8px; padding: 9px 10px; border: 1px solid var(--danger); border-radius: 6px; color: var(--danger); font-size: 13px; }
.stock-ask-conversation__feedback.is-warning { border-color: var(--warning); color: var(--warning); }

@media (max-width: 720px) {
  .stock-ask-conversation > header { align-items: flex-start; }
  .stock-ask-conversation__target { max-width: 54%; }
  .stock-ask-conversation__messages, .stock-ask-conversation__empty { min-height: 300px; }
}
</style>
