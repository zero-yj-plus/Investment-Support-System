<script setup lang="ts">
import { computed, watch } from 'vue'
import StockAskConversation from './StockAskConversation.vue'
import StockAskEvidence from './StockAskEvidence.vue'
import StockAskHistory from './StockAskHistory.vue'
import { useStockAsk } from '../composables/useStockAsk'
import { formatTime } from '../utils/stockAskFormat'

const emit = defineEmits<{ 'refresh-state': [value: boolean] }>()

const {
  codeInput, selectedCode, prompt, core, enrichment, stockName,
  coreLoading, enrichmentLoading, stockLoading, coreError, enrichmentError, codeError,
  composerError, chatError, saveWarning, conversations, conversationsLoading,
  conversationError, activeConversationId, messages, pendingImages, streaming,
  stageText, stageSeconds, refreshing, canAsk, loadStock, loadConversations,
  newConversation, switchConversation, deleteConversation, addPendingImages,
  removePendingImage, send, stop, refresh,
} = useStockAsk()

const dataStamp = computed(() => {
  if (!selectedCode.value) return '尚未选择标的'
  if (stockLoading.value) return '正在更新标的证据'
  return formatTime(core.value?.quote?.as_of || core.value?.ts || enrichment.value?.ts)
})

const healthLabel = computed(() => {
  if (!selectedCode.value) return '等待查询'
  if (core.value?.recognized === false || core.value?.state === 'not_found') return '代码未通过核验'
  if (coreError.value && !core.value) return '首屏暂不可用'
  if (core.value?.state === 'partial' || enrichment.value?.state === 'partial' || enrichmentError.value) return '部分覆盖'
  if (core.value?.state === 'complete') return '证据已就绪'
  return stockLoading.value ? '读取中' : '状态待确认'
})

async function submitCode() {
  await loadStock(codeInput.value)
}

watch(refreshing, (value) => emit('refresh-state', value), { immediate: true })
defineExpose({ refresh })
</script>

<template>
  <section class="stock-ask-page" aria-labelledby="stock-ask-page-title" :aria-busy="refreshing || streaming">
    <header class="stock-ask-page__header">
      <div>
        <p class="eyebrow">研究中心 · 服务端治理问答</p>
        <h1 id="stock-ask-page-title">问股</h1>
        <p>专属盘中问股：先锁定单只 A 股，再围绕当前动作、风险边界、失效条件和证据完整性提问。</p>
      </div>
      <dl>
        <div><dt>当前标的</dt><dd class="mono">{{ selectedCode || '—' }}</dd></div>
        <div><dt>数据时点</dt><dd class="mono">{{ dataStamp }}</dd></div>
        <div><dt>证据状态</dt><dd>{{ healthLabel }}</dd></div>
      </dl>
    </header>

    <form class="stock-ask-selector panel" novalidate @submit.prevent="submitCode">
      <div class="stock-ask-selector__copy">
        <label for="stock-ask-code">六位 A 股代码</label>
        <span>查询只会读取当前标的首屏行情与必要增强证据；问股动作和价位继续由服务端决定。</span>
      </div>
      <div class="stock-ask-selector__controls">
        <input
          id="stock-ask-code"
          v-model="codeInput"
          type="text"
          inputmode="numeric"
          autocomplete="off"
          maxlength="6"
          pattern="[0-9]{6}"
          placeholder="例如 600519"
          :aria-invalid="Boolean(codeError)"
          aria-describedby="stock-ask-code-help"
          :disabled="streaming"
        >
        <button type="submit" :disabled="stockLoading || streaming">{{ stockLoading ? '查询中' : '查询标的' }}</button>
      </div>
      <p id="stock-ask-code-help" :class="{ 'is-error': codeError }" :role="codeError ? 'alert' : undefined">
        {{ codeError || (selectedCode ? `当前锁定 ${stockName || '名称待返回'} ${selectedCode}` : '只接受六位数字；报价缺失不会被补成 0。') }}
      </p>
    </form>

    <div class="stock-ask-workbench">
      <StockAskHistory
        class="stock-ask-workbench__history"
        :conversations="conversations"
        :loading="conversationsLoading"
        :error="conversationError"
        :active-id="activeConversationId"
        :disabled="streaming"
        @new="newConversation"
        @select="switchConversation"
        @delete="deleteConversation"
        @retry="loadConversations"
      />
      <StockAskConversation
        class="stock-ask-workbench__conversation"
        :messages="messages"
        :prompt="prompt"
        :pending-images="pendingImages"
        :selected-code="selectedCode"
        :stock-name="stockName"
        :streaming="streaming"
        :stage-text="stageText"
        :stage-seconds="stageSeconds"
        :composer-error="composerError"
        :chat-error="chatError"
        :save-warning="saveWarning"
        :can-ask="canAsk"
        @update:prompt="prompt = $event"
        @add-images="addPendingImages"
        @remove-image="removePendingImage"
        @send="send()"
        @stop="stop"
      />
      <StockAskEvidence
        class="stock-ask-workbench__evidence"
        :code="selectedCode"
        :core="core"
        :enrichment="enrichment"
        :core-loading="coreLoading"
        :enrichment-loading="enrichmentLoading"
        :core-error="coreError"
        :enrichment-error="enrichmentError"
      />
    </div>

    <footer class="stock-ask-page__governance">
      <span>本页不提供自由问答、持仓判断或产业链挖掘。</span>
      <span>未完整回答不保存；历史消息只追加，不覆盖既有事实。</span>
    </footer>
  </section>
</template>

<style scoped>
.stock-ask-page { display: grid; min-width: 0; gap: 12px; }
.stock-ask-page__header { display: flex; min-width: 0; align-items: flex-start; justify-content: space-between; gap: 24px; }
.stock-ask-page__header h1 { margin: 0; font-size: clamp(24px, 2.1vw, 30px); }
.stock-ask-page__header > div > p:last-child { max-width: 780px; margin: 5px 0 0; color: var(--text-2); }
.stock-ask-page__header dl { display: grid; grid-template-columns: repeat(3, minmax(100px, 1fr)); min-width: min(100%, 440px); margin: 0; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.stock-ask-page__header dl div { min-width: 0; padding: 9px 10px; border-right: 1px solid var(--border); }
.stock-ask-page__header dl div:last-child { border-right: 0; }
.stock-ask-page__header dt { color: var(--text-3); font-size: 12px; }
.stock-ask-page__header dd { margin: 2px 0 0; overflow-wrap: anywhere; color: var(--text); font-size: 14px; font-weight: 720; }
.stock-ask-selector { display: grid; grid-template-columns: minmax(230px, 1fr) auto; align-items: center; gap: 8px 18px; padding: 12px 14px; }
.stock-ask-selector__copy { display: flex; min-width: 0; flex-direction: column; }
.stock-ask-selector__copy label { color: var(--text); font-weight: 750; }
.stock-ask-selector__copy span { color: var(--text-3); font-size: 13px; }
.stock-ask-selector__controls { display: grid; grid-template-columns: minmax(150px, 190px) auto; gap: 8px; }
.stock-ask-selector input, .stock-ask-selector button { min-height: 48px; border: 1px solid var(--border-strong); border-radius: 8px; }
.stock-ask-selector input { min-width: 0; padding: 0 12px; background: var(--surface-2); color: var(--text); font: 700 17px/1 "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; letter-spacing: .08em; }
.stock-ask-selector button { padding: 0 16px; border-color: var(--accent); background: var(--accent); color: #111a27; font-weight: 760; cursor: pointer; }
:global(:root[data-theme="light"] .stock-ask-selector button) { color: #fff; }
.stock-ask-selector button:disabled, .stock-ask-selector input:disabled { cursor: wait; opacity: .55; }
.stock-ask-selector > p { grid-column: 1 / -1; margin: 0; color: var(--text-3); font-size: 13px; }
.stock-ask-selector > p.is-error { color: var(--danger); }
.stock-ask-workbench { display: grid; grid-template-columns: minmax(190px, 220px) minmax(460px, 1fr) minmax(260px, 320px); grid-template-areas: "history conversation evidence"; align-items: start; gap: 12px; min-width: 0; }
.stock-ask-workbench > * { min-width: 0; }
.stock-ask-workbench__history { grid-area: history; }
.stock-ask-workbench__conversation { grid-area: conversation; }
.stock-ask-workbench__evidence { grid-area: evidence; }
.stock-ask-page__governance { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 16px; padding: 4px 2px; color: var(--text-3); font-size: 12px; }

@media (max-width: 1180px) {
  .stock-ask-page__header { flex-direction: column; gap: 10px; }
  .stock-ask-page__header dl { width: 100%; }
  .stock-ask-workbench { grid-template-columns: minmax(190px, 220px) minmax(0, 1fr); grid-template-areas: "history conversation" "evidence evidence"; }
}

@media (max-width: 800px) {
  .stock-ask-selector { grid-template-columns: minmax(0, 1fr); }
  .stock-ask-selector__controls { grid-template-columns: minmax(0, 1fr) auto; }
  .stock-ask-workbench { grid-template-columns: minmax(0, 1fr); grid-template-areas: "conversation" "evidence" "history"; }
}

@media (max-width: 720px) {
  .stock-ask-page { gap: 8px; font-size: 18px; }
  .stock-ask-page__header dl { grid-template-columns: minmax(0, 1fr); }
  .stock-ask-page__header dl div { border-right: 0; border-bottom: 1px solid var(--border); }
  .stock-ask-page__header dl div:last-child { border-bottom: 0; }
  .stock-ask-selector { padding: 10px; }
  .stock-ask-selector__controls { grid-template-columns: minmax(0, 1fr); }
  .stock-ask-selector input, .stock-ask-selector button { min-height: 52px; }
  .stock-ask-selector__copy span, .stock-ask-selector > p { font-size: 14px; }
}
</style>
