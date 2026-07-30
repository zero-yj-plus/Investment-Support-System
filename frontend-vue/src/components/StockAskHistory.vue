<script setup lang="ts">
import type { ConversationMeta } from '../types/stockAsk'

defineProps<{
  conversations: ConversationMeta[]
  loading: boolean
  error: string
  activeId: string
  disabled: boolean
  expanded: boolean
}>()

const emit = defineEmits<{
  toggle: []
  new: []
  select: [id: string]
  delete: [id: string]
  retry: []
}>()

function requestDelete(item: ConversationMeta) {
  if (window.confirm(`删除问股记录“${item.title || '新对话'}”？此操作不可撤销。`)) emit('delete', item.id)
}
</script>

<template>
  <aside class="stock-ask-history panel" :class="{ 'is-expanded': expanded }" aria-label="问股记录">
    <header>
      <button
        type="button"
        class="stock-ask-history__toggle"
        :aria-expanded="expanded"
        aria-controls="stock-ask-history-content"
        :aria-label="expanded ? '收起问股记录' : '展开问股记录'"
        @click="emit('toggle')"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6" /></svg>
        <span class="stock-ask-history__toggle-label">问股记录</span>
      </button>
      <button v-if="expanded" type="button" class="stock-ask-history__new" :disabled="disabled" @click="emit('new')">新建</button>
    </header>

    <div v-if="expanded" id="stock-ask-history-content">
      <p v-if="loading" class="stock-ask-history__state" role="status">正在读取问股记录…</p>
      <div v-else-if="error" class="stock-ask-history__state is-error" role="alert">
        <strong>历史暂不可用</strong><span>{{ error }}</span>
        <button type="button" @click="emit('retry')">重试</button>
      </div>
      <p v-else-if="conversations.length === 0" class="stock-ask-history__state">暂无问股记录。选择股票并提问后会自动建立会话。</p>
      <ol v-else>
        <li v-for="item in conversations" :key="item.id" :class="{ active: item.id === activeId }">
          <button
            type="button"
            class="stock-ask-history__open"
            :disabled="disabled"
            :aria-current="item.id === activeId ? 'page' : undefined"
            @click="emit('select', item.id)"
          >
            <strong>{{ item.title || '新对话' }}</strong>
            <span class="mono">{{ item.updated_at || '更新时间未提供' }}</span>
          </button>
          <button
            type="button"
            class="stock-ask-history__delete"
            :disabled="disabled"
            :aria-label="`删除问股记录：${item.title || '新对话'}`"
            @click="requestDelete(item)"
          >删除</button>
        </li>
      </ol>
    </div>
  </aside>
</template>

<style scoped>
.stock-ask-history { min-width: 0; align-self: start; overflow: hidden; }
.stock-ask-history > header { display: flex; min-height: 64px; align-items: center; justify-content: center; gap: 8px; padding: 10px 6px; }
.stock-ask-history:not(.is-expanded) > header { min-height: 132px; }
.stock-ask-history.is-expanded > header { justify-content: space-between; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.stock-ask-history__toggle { display: flex; min-width: 44px; min-height: 44px; align-items: center; justify-content: center; flex-direction: column; gap: 7px; padding: 4px 0; border: 0; border-radius: 6px; background: transparent; color: var(--text); cursor: pointer; }
.stock-ask-history.is-expanded .stock-ask-history__toggle { min-width: 0; justify-content: flex-start; flex-direction: row; padding: 0 4px; text-align: left; }
.stock-ask-history__toggle:hover { background: var(--surface-2); }
.stock-ask-history__toggle svg { width: 20px; height: 20px; flex: 0 0 20px; fill: none; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 2; }
.stock-ask-history.is-expanded .stock-ask-history__toggle svg { transform: rotate(90deg); }
.stock-ask-history__toggle-label { font-size: 17px; font-weight: 750; }
.stock-ask-history:not(.is-expanded) .stock-ask-history__toggle-label { font-size: 14px; letter-spacing: .12em; writing-mode: vertical-rl; }
.stock-ask-history__new, .stock-ask-history__state button { min-width: 60px; min-height: 44px; padding: 0 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); cursor: pointer; }
.stock-ask-history__new:hover, .stock-ask-history__state button:hover { border-color: var(--border-strong); }
.stock-ask-history button:disabled { cursor: wait; opacity: .55; }
.stock-ask-history__state { display: grid; min-height: 140px; place-content: center; gap: 6px; margin: 0; padding: 18px 12px; color: var(--text-3); text-align: center; }
.stock-ask-history__state strong { color: var(--danger); }
.stock-ask-history__state span { font-size: 13px; }
.stock-ask-history__state button { justify-self: center; margin-top: 6px; }
.stock-ask-history ol { display: grid; gap: 2px; margin: 0; padding: 6px; list-style: none; }
.stock-ask-history li { display: grid; grid-template-columns: minmax(0, 1fr) 52px; gap: 4px; border: 1px solid transparent; border-radius: 6px; }
.stock-ask-history li.active { border-color: color-mix(in srgb, var(--accent) 42%, var(--border)); background: var(--accent-dim); }
.stock-ask-history__open, .stock-ask-history__delete { min-height: 52px; border: 0; background: transparent; cursor: pointer; }
.stock-ask-history__open { display: flex; min-width: 0; flex-direction: column; justify-content: center; padding: 6px 8px; text-align: left; }
.stock-ask-history__open strong { overflow: hidden; color: var(--text); font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.stock-ask-history__open span { overflow: hidden; color: var(--text-3); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.stock-ask-history__delete { padding: 0 6px; color: var(--danger); font-size: 12px; }
.stock-ask-history li:hover { background: var(--surface-2); }

@media (max-width: 800px) {
  .stock-ask-history > header, .stock-ask-history:not(.is-expanded) > header { min-height: 64px; justify-content: flex-start; padding: 10px 12px; }
  .stock-ask-history:not(.is-expanded) .stock-ask-history__toggle { flex-direction: row; padding: 0 4px; }
  .stock-ask-history:not(.is-expanded) .stock-ask-history__toggle-label { font-size: 17px; letter-spacing: normal; writing-mode: horizontal-tb; }
  .stock-ask-history ol { grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr)); }
}
</style>
