<script setup lang="ts">
import { computed } from 'vue'
import type { KnowledgeItem, KnowledgeResponse } from '../types/knowledge'

const props = defineProps<{
  response: KnowledgeResponse | null
  items: KnowledgeItem[]
}>()

function countLabel(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value} 条` : '未提供'
}

const latestCreatedAt = computed(() => {
  const timestamps = props.items
    .map((item) => typeof item.created_at === 'string' ? item.created_at.trim() : '')
    .filter(Boolean)
    .sort((left, right) => right.localeCompare(left))
  return timestamps[0] || '未提供'
})
</script>

<template>
  <section class="knowledge-summary panel" aria-label="知识库概览">
    <dl class="knowledge-summary__metrics">
      <div>
        <dt>当前条目</dt>
        <dd>{{ items.length }} <small>条</small></dd>
        <dd class="knowledge-summary__hint">来自本次真实返回数组</dd>
      </div>
      <div>
        <dt>已批准</dt>
        <dd>{{ countLabel(response?.summary?.approved) }}</dd>
        <dd class="knowledge-summary__hint">由服务端 summary 提供</dd>
      </div>
      <div>
        <dt>待人工治理</dt>
        <dd>{{ countLabel(response?.review_queue_count) }}</dd>
        <dd class="knowledge-summary__hint">draft + legacy_unverified</dd>
      </div>
      <div>
        <dt>最近记录时点</dt>
        <dd class="knowledge-summary__time">{{ latestCreatedAt }}</dd>
        <dd class="knowledge-summary__hint">不是独立数据日期或实时性证明</dd>
      </div>
    </dl>

    <div class="knowledge-summary__boundary" role="note">
      <strong>治理边界</strong>
      <span>本接口不提供风险等级、仓位上限或交易结论，本页不作推断。</span>
      <span>接口也未提供新鲜度或 degraded 字段，因此不会把记录时点称为实时数据。</span>
    </div>
  </section>
</template>

<style scoped>
.knowledge-summary { overflow: hidden; }
.knowledge-summary__metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; padding: 12px; gap: 8px; }
.knowledge-summary__metrics > div { min-width: 0; padding: 10px 11px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); }
.knowledge-summary__metrics dt { color: var(--text-3); font-size: 13px; font-weight: 650; }
.knowledge-summary__metrics dd { margin: 3px 0 1px; font: 760 22px/1.35 "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.knowledge-summary__metrics dd small { color: var(--text-2); font-size: 13px; font-weight: 650; }
.knowledge-summary__metrics .knowledge-summary__hint { display: block; margin: 0; color: var(--text-3); font: inherit; font-size: 12px; }
.knowledge-summary__metrics .knowledge-summary__time { font-size: 16px; }
.knowledge-summary__boundary { display: flex; flex-wrap: wrap; gap: 4px 16px; padding: 10px 16px; border-top: 1px solid var(--border); color: var(--text-2); font-size: 13px; }
.knowledge-summary__boundary strong { color: var(--warning); }

@media (max-width: 900px) {
  .knowledge-summary__metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 520px) {
  .knowledge-summary__metrics { grid-template-columns: 1fr; }
}
</style>
