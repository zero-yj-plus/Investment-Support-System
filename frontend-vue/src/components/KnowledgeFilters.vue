<script setup lang="ts">
import { computed } from 'vue'
import { KNOWLEDGE_CATEGORIES, type KnowledgeItem } from '../types/knowledge'

const props = defineProps<{
  items: KnowledgeItem[]
  keyword: string
  category: string
  status: string
}>()

const emit = defineEmits<{
  'update:keyword': [value: string]
  'update:category': [value: string]
  'update:status': [value: string]
}>()

const statusOptions = [
  { value: 'all', label: '全部状态' },
  { value: 'approved', label: '已批准' },
  { value: 'draft', label: '待审批' },
  { value: 'legacy_unverified', label: '历史待补证' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'superseded', label: '已被替代' },
]

const categoryOptions = computed(() => [
  { value: 'all', label: '全部分类', count: props.items.length },
  ...KNOWLEDGE_CATEGORIES.map((value) => ({
    value,
    label: value,
    count: props.items.filter((item) => item.category === value).length,
  })),
])

const statusCounts = computed(() => statusOptions.map((option) => ({
  ...option,
  count: option.value === 'all'
    ? props.items.length
    : props.items.filter((item) => item.status === option.value).length,
})))
</script>

<template>
  <aside class="knowledge-filters panel" aria-labelledby="knowledge-filter-title">
    <div class="knowledge-filters__heading">
      <h2 id="knowledge-filter-title">检索与筛选</h2>
      <span>{{ items.length }} 条真实记录</span>
    </div>

    <div class="knowledge-field">
      <label for="knowledge-search">搜索标题或正文</label>
      <input
        id="knowledge-search"
        type="search"
        :value="keyword"
        autocomplete="off"
        placeholder="输入关键词"
        @input="emit('update:keyword', ($event.target as HTMLInputElement).value)"
      >
    </div>

    <div class="knowledge-field">
      <label for="knowledge-category">分类</label>
      <select id="knowledge-category" :value="category" @change="emit('update:category', ($event.target as HTMLSelectElement).value)">
        <option v-for="option in categoryOptions" :key="option.value" :value="option.value">
          {{ option.label }}（{{ option.count }}）
        </option>
      </select>
    </div>

    <fieldset>
      <legend>治理状态</legend>
      <button
        v-for="option in statusCounts"
        :key="option.value"
        type="button"
        :class="{ active: status === option.value }"
        :aria-pressed="status === option.value"
        @click="emit('update:status', option.value)"
      >
        <span>{{ option.label }}</span><b class="mono">{{ option.count }}</b>
      </button>
    </fieldset>
  </aside>
</template>

<style scoped>
.knowledge-filters { min-width: 0; align-self: start; overflow: hidden; }
.knowledge-filters__heading { display: flex; min-height: 58px; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.knowledge-filters__heading h2 { margin: 0; font-size: 17px; }
.knowledge-filters__heading span { color: var(--text-3); font-size: 12px; }
.knowledge-field { display: grid; gap: 5px; padding: 10px 12px 0; }
.knowledge-field label, fieldset legend { color: var(--text-2); font-size: 13px; font-weight: 700; }
.knowledge-field input, .knowledge-field select { width: 100%; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); color: var(--text); }
fieldset { display: grid; gap: 4px; margin: 10px 0 0; padding: 10px 12px 12px; border: 0; border-top: 1px solid var(--border); }
fieldset legend { padding: 0 4px; }
fieldset button { display: flex; min-height: 44px; align-items: center; justify-content: space-between; gap: 8px; padding: 7px 9px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: var(--text-2); text-align: left; cursor: pointer; }
fieldset button:hover { border-color: var(--border); background: var(--surface-2); }
fieldset button.active { border-color: color-mix(in srgb, var(--accent) 48%, var(--border)); background: var(--accent-dim); color: var(--accent); }
fieldset b { font-size: 13px; }

@media (max-width: 900px) {
  .knowledge-filters { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, .8fr); gap: 0 8px; }
  .knowledge-filters__heading, fieldset { grid-column: 1 / -1; }
  fieldset { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}

@media (max-width: 620px) {
  .knowledge-filters { display: block; }
  fieldset { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 380px) {
  fieldset { grid-template-columns: 1fr; }
}
</style>
