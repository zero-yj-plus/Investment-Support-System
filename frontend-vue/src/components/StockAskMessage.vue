<script setup lang="ts">
import ReportDocument from './ReportDocument.vue'
import type { StockAskMessage } from '../types/stockAsk'

defineProps<{
  message: StockAskMessage
  stageText?: string
  stageSeconds?: number
}>()
</script>

<template>
  <article class="stock-ask-message" :class="[`is-${message.role}`, `is-${message.state}`]">
    <div class="stock-ask-message__avatar" aria-hidden="true">{{ message.role === 'user' ? '我' : message.role === 'system' ? '!' : '研' }}</div>
    <div class="stock-ask-message__body">
      <div v-if="message.role === 'user'" class="stock-ask-message__plain">
        <span v-if="message.imageCount" class="stock-ask-message__attachment">附带 {{ message.imageCount }} 张图片</span>
        <p>{{ message.content }}</p>
      </div>
      <div v-else-if="message.state === 'streaming' && !message.content" class="stock-ask-message__waiting" role="status">
        <span>{{ stageText || '正在核验行情、证据与风险闸…' }}</span>
        <strong class="mono">{{ stageSeconds || 0 }}s</strong>
      </div>
      <div v-else class="stock-ask-message__markdown">
        <p v-if="message.state === 'partial'" class="stock-ask-message__quarantine" role="alert">未完整输出 · 已隔离，不保存、不进入后续对话</p>
        <ReportDocument :content="message.content" />
      </div>
      <footer>
        <span>{{ message.role === 'user' ? '你' : '研究助手' }}</span>
        <span v-if="message.code" class="mono">{{ message.code }}</span>
        <time v-if="message.ts">{{ message.ts }}</time>
        <span v-if="message.state === 'streaming'">生成中</span>
        <span v-else-if="message.state === 'partial'">部分输出</span>
      </footer>
    </div>
  </article>
</template>

<style scoped>
.stock-ask-message { display: grid; grid-template-columns: 36px minmax(0, 1fr); gap: 10px; min-width: 0; padding: 14px 16px; }
.stock-ask-message + .stock-ask-message { border-top: 1px solid var(--border); }
.stock-ask-message.is-user { background: color-mix(in srgb, var(--accent) 5%, transparent); }
.stock-ask-message__avatar { display: grid; width: 36px; height: 36px; place-items: center; border: 1px solid var(--border); border-radius: 50%; background: var(--surface-2); color: var(--text-2); font-size: 13px; font-weight: 760; }
.is-assistant .stock-ask-message__avatar { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); color: var(--accent); }
.stock-ask-message__body { min-width: 0; }
.stock-ask-message__plain { width: fit-content; max-width: min(680px, 100%); padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); }
.stock-ask-message__plain p { margin: 0; white-space: pre-wrap; }
.stock-ask-message__attachment { display: inline-block; margin-bottom: 6px; color: var(--info); font-size: 13px; }
.stock-ask-message__waiting { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 12px; padding: 12px; border: 1px dashed var(--border-strong); border-radius: 8px; color: var(--text-2); }
.stock-ask-message__waiting strong { color: var(--accent); font-size: 17px; }
.stock-ask-message__quarantine { margin: 0 0 8px; padding: 9px 10px; border: 1px solid var(--warning); border-radius: 6px; background: color-mix(in srgb, var(--warning) 9%, var(--surface)); color: var(--warning); font-weight: 700; }
.stock-ask-message footer { display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 6px; color: var(--text-3); font-size: 12px; }
.stock-ask-message__markdown :deep(.report-markdown) { max-width: none; padding: 0; }
.stock-ask-message__markdown :deep(.report-markdown h2) { margin-top: 4px; font-size: 20px; }
.stock-ask-message__markdown :deep(.report-markdown h3) { margin-top: 18px; font-size: 18px; }
.stock-ask-message__markdown :deep(.report-markdown h4) { margin-top: 14px; font-size: 16px; }
.stock-ask-message__markdown :deep(.report-markdown p), .stock-ask-message__markdown :deep(.report-markdown li) { line-height: 1.7; }

@media (max-width: 720px) {
  .stock-ask-message { grid-template-columns: 32px minmax(0, 1fr); gap: 8px; padding: 12px 10px; }
  .stock-ask-message__avatar { width: 32px; height: 32px; }
}
</style>
