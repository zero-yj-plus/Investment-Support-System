<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import StockAskConversation from './StockAskConversation.vue'
import StockAskEvidence from './StockAskEvidence.vue'
import StockAskHistory from './StockAskHistory.vue'
import { useStockAsk } from '../composables/useStockAsk'
import { formatTime } from '../utils/stockAskFormat'

const emit = defineEmits<{ 'refresh-state': [value: boolean] }>()
const historyExpanded = ref(false)

const {
  codeInput, selectedCode, prompt, core, enrichment, stockName,
  coreLoading, enrichmentLoading, stockLoading, coreError, enrichmentError, codeError,
  composerError, chatError, saveWarning, conversations, conversationsLoading,
  conversationError, activeConversationId, messages, pendingImages, streaming,
  stageText, stageSeconds, refreshing, canAsk, loadStock, loadConversations,
  perspectives, selectedPerspectiveIds, maxSelectedPerspectives,
  perspectivesLoading, perspectiveError, loadPerspectives, togglePerspective, clearPerspectives,
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
      <div class="page-heading">
        <h1 id="stock-ask-page-title">问股</h1>
        <p class="page-subtitle">专属盘中问股：先锁定单只 A 股，再围绕当前动作、风险边界、失效条件和证据完整性提问。</p>
      </div>
    </header>

    <div class="stock-ask-workbench" :class="{ 'is-history-expanded': historyExpanded }">
      <StockAskHistory
        class="stock-ask-workbench__history"
        :conversations="conversations"
        :loading="conversationsLoading"
        :error="conversationError"
        :active-id="activeConversationId"
        :disabled="streaming"
        :expanded="historyExpanded"
        @toggle="historyExpanded = !historyExpanded"
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
        :code-input="codeInput"
        :selected-code="selectedCode"
        :stock-name="stockName"
        :stock-loading="stockLoading"
        :code-error="codeError"
        :streaming="streaming"
        :stage-text="stageText"
        :stage-seconds="stageSeconds"
        :composer-error="composerError"
        :chat-error="chatError"
        :save-warning="saveWarning"
        :can-ask="canAsk"
        :perspectives="perspectives"
        :selected-perspective-ids="selectedPerspectiveIds"
        :max-selected-perspectives="maxSelectedPerspectives"
        :perspectives-loading="perspectivesLoading"
        :perspective-error="perspectiveError"
        @code-input="codeInput = $event"
        @submit-code="submitCode"
        @update:prompt="prompt = $event"
        @add-images="addPendingImages"
        @remove-image="removePendingImage"
        @send="send()"
        @stop="stop"
        @toggle-perspective="togglePerspective"
        @clear-perspectives="clearPerspectives"
        @retry-perspectives="loadPerspectives"
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
    <dl class="stock-ask-page__stamp data-footnote-stamp" aria-label="问股标的、数据时点与证据状态">
      <div><dt>当前标的</dt><dd class="mono">{{ selectedCode || '—' }}</dd></div>
      <div><dt>数据时点</dt><dd class="mono">{{ dataStamp }}</dd></div>
      <div><dt>证据状态</dt><dd>{{ healthLabel }}</dd></div>
    </dl>
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
.stock-ask-page__stamp { display: grid; grid-template-columns: repeat(3, minmax(100px, 1fr)); min-width: min(100%, 440px); margin: 0; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.stock-ask-page__stamp div { min-width: 0; padding: 9px 10px; border-right: 1px solid var(--border); }.stock-ask-page__stamp div:last-child { border-right: 0; }
.stock-ask-page__stamp dt { color: var(--text-3); font-size: 12px; }.stock-ask-page__stamp dd { margin: 2px 0 0; overflow-wrap: anywhere; color: var(--text); font-size: 14px; font-weight: 720; }
.stock-ask-workbench { display: grid; grid-template-columns: 56px minmax(460px, 1fr) minmax(260px, 320px); grid-template-areas: "history conversation evidence"; align-items: start; gap: 12px; min-width: 0; }
.stock-ask-workbench.is-history-expanded { grid-template-columns: minmax(190px, 220px) minmax(460px, 1fr) minmax(260px, 320px); }
.stock-ask-workbench > * { min-width: 0; }
.stock-ask-workbench__history { grid-area: history; }
.stock-ask-workbench__conversation { grid-area: conversation; }
.stock-ask-workbench__evidence { grid-area: evidence; }
.stock-ask-page__governance { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 6px 16px; padding: 4px 2px; color: var(--text-3); font-size: 12px; }

@media (max-width: 1180px) {
  .stock-ask-page__header { flex-direction: column; gap: 10px; }
  .stock-ask-page__header dl { width: 100%; }
  .stock-ask-page__stamp { width: 100%; }
  .stock-ask-workbench { grid-template-columns: 56px minmax(0, 1fr); grid-template-areas: "history conversation" "evidence evidence"; }
  .stock-ask-workbench.is-history-expanded { grid-template-columns: minmax(190px, 220px) minmax(0, 1fr); }
}

@media (max-width: 800px) {
  .stock-ask-workbench, .stock-ask-workbench.is-history-expanded { grid-template-columns: minmax(0, 1fr); grid-template-areas: "conversation" "evidence" "history"; }
}

@media (max-width: 720px) {
  .stock-ask-page { gap: 8px; font-size: 18px; }
  .stock-ask-page__header dl { grid-template-columns: minmax(0, 1fr); }
  .stock-ask-page__header dl div { border-right: 0; border-bottom: 1px solid var(--border); }
  .stock-ask-page__header dl div:last-child { border-bottom: 0; }
  .stock-ask-page__stamp { grid-template-columns: minmax(0, 1fr); }
  .stock-ask-page__stamp div { border-right: 0; border-bottom: 1px solid var(--border); }
  .stock-ask-page__stamp div:last-child { border-bottom: 0; }
}
</style>
