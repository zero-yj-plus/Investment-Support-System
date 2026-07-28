<script setup lang="ts">
import { computed } from 'vue'
import type { KnowledgeAction, KnowledgeItem } from '../types/knowledge'

const props = defineProps<{
  item: KnowledgeItem
  busy: boolean
}>()

const emit = defineEmits<{
  action: [type: KnowledgeAction, item: KnowledgeItem]
}>()

const statusMeta = computed(() => {
  if (props.item.status === 'approved') return { label: '已批准 · 会进入 AI', tone: 'approved' }
  if (props.item.status === 'draft') return { label: '待人工审批', tone: 'pending' }
  if (props.item.status === 'legacy_unverified') return { label: '历史条目 · 待补证', tone: 'pending' }
  if (props.item.status === 'rejected') return { label: '已拒绝 · 不进入 AI', tone: 'rejected' }
  if (props.item.status === 'superseded') return { label: '已被新版替代', tone: 'superseded' }
  return { label: '状态未提供', tone: 'missing' }
})

const isPending = computed(() => props.item.status === 'draft' || props.item.status === 'legacy_unverified')
const canSupersede = computed(() => props.item.status === 'approved')
const hasUsableId = computed(() => typeof props.item.id === 'string' && props.item.id.length > 0)

const sourceState = computed(() => {
  const reportsAvailable = Array.isArray(props.item.source_report_ids)
  const evidenceAvailable = Array.isArray(props.item.evidence_ids)
  if (!reportsAvailable && !evidenceAvailable) {
    return { label: '来源字段未提供', detail: [] as string[], partial: true }
  }
  const reports = reportsAvailable ? props.item.source_report_ids as string[] : []
  const evidence = evidenceAvailable ? props.item.evidence_ids as string[] : []
  const detail = [...reports, ...evidence]
  if (!reportsAvailable || !evidenceAvailable) {
    return { label: detail.length ? `已绑定 ${detail.length} 项 · 另一来源字段缺失` : '来源字段局部缺失', detail, partial: true }
  }
  return { label: detail.length ? `已绑定 ${detail.length} 项来源` : '未绑定来源', detail, partial: false }
})

function numberLabel(value: number | null | undefined, suffix: string) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value}${suffix}` : '未提供'
}

function textLabel(value: string | null | undefined, missing: string) {
  return typeof value === 'string' && value.trim() ? value : missing
}
</script>

<template>
  <article class="knowledge-card panel" :aria-labelledby="`knowledge-title-${item.id || 'missing'}`">
    <header>
      <div class="knowledge-card__tags">
        <span class="knowledge-card__category">{{ textLabel(item.category, '分类未提供') }}</span>
        <span class="knowledge-card__status" :class="`is-${statusMeta.tone}`">{{ statusMeta.label }}</span>
      </div>
      <span class="mono knowledge-card__version">{{ numberLabel(item.version, ' 版') }}</span>
    </header>

    <div class="knowledge-card__body">
      <h3 :id="`knowledge-title-${item.id || 'missing'}`">{{ textLabel(item.title, '标题未提供') }}</h3>
      <p class="knowledge-card__content">{{ textLabel(item.content, '正文未提供') }}</p>
      <details v-if="typeof item.content === 'string' && item.content.length > 220" class="knowledge-card__full-content">
        <summary>展开完整正文</summary>
        <p>{{ item.content }}</p>
      </details>
    </div>

    <dl class="knowledge-card__facts">
      <div><dt>记录时点</dt><dd class="mono">{{ textLabel(item.created_at, '未提供') }}</dd></div>
    </dl>

    <details v-if="sourceState.detail.length || item.rejection_reason || item.replacement_id || item.supersedes_id" class="knowledge-card__details">
      <summary>查看治理依据与版本关系</summary>
      <div>
        <section v-if="sourceState.detail.length" aria-label="已绑定来源">
          <strong>已绑定来源</strong>
          <ul><li v-for="source in sourceState.detail" :key="source" class="mono">{{ source }}</li></ul>
        </section>
        <p v-if="item.rejection_reason"><strong>拒绝原因：</strong>{{ item.rejection_reason }}</p>
        <p v-if="item.replacement_id"><strong>替代版本 ID：</strong><span class="mono">{{ item.replacement_id }}</span></p>
        <p v-if="item.supersedes_id"><strong>被替代版本 ID：</strong><span class="mono">{{ item.supersedes_id }}</span></p>
      </div>
    </details>

    <footer class="knowledge-card__footer">
      <p v-if="!hasUsableId" role="status">条目 ID 缺失，治理动作已锁定。</p>
      <div class="knowledge-card__actions">
        <button v-if="isPending" type="button" :disabled="busy || !hasUsableId" @click="emit('action', 'approve', item)">审批</button>
        <button v-if="isPending" type="button" :disabled="busy || !hasUsableId" @click="emit('action', 'reject', item)">拒绝</button>
        <button v-if="canSupersede" type="button" :disabled="busy || !hasUsableId" @click="emit('action', 'supersede', item)">新版本替代</button>
        <button class="danger-button" type="button" :disabled="busy || !hasUsableId" @click="emit('action', 'delete', item)">删除</button>
      </div>
    </footer>
  </article>
</template>

<style scoped>
.knowledge-card { display: grid; min-width: 0; overflow: hidden; }
.knowledge-card > header { display: flex; min-height: 44px; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 10px; border-bottom: 1px solid var(--border); }
.knowledge-card__tags { display: flex; min-width: 0; flex-wrap: wrap; gap: 6px; }
.knowledge-card__category, .knowledge-card__status { display: inline-flex; min-height: 28px; align-items: center; padding: 3px 7px; border: 1px solid var(--border); border-radius: 4px; font-size: 12px; font-weight: 700; }
.knowledge-card__category { color: var(--accent); }
.knowledge-card__status.is-approved { border-color: color-mix(in srgb, var(--info) 60%, var(--border)); color: var(--info); }
.knowledge-card__status.is-pending { border-color: color-mix(in srgb, var(--warning) 60%, var(--border)); color: var(--warning); }
.knowledge-card__status.is-rejected { border-color: color-mix(in srgb, var(--danger) 60%, var(--border)); color: var(--danger); }
.knowledge-card__status.is-superseded, .knowledge-card__status.is-missing { color: var(--text-2); }
.knowledge-card__version { flex: 0 0 auto; color: var(--text-3); font-size: 12px; }
.knowledge-card__body { padding: 10px; }
.knowledge-card__body h3 { margin: 0 0 6px; font-size: 17px; }
.knowledge-card__content { display: -webkit-box; margin: 0; overflow: hidden; color: var(--text-2); white-space: pre-wrap; -webkit-box-orient: vertical; -webkit-line-clamp: 4; }
.knowledge-card__full-content { margin-top: 8px; border-top: 1px solid var(--border); }
.knowledge-card__full-content summary { display: flex; min-height: 44px; align-items: center; color: var(--info); cursor: pointer; }
.knowledge-card__full-content p { margin: 0 0 4px; color: var(--text-2); white-space: pre-wrap; }
.knowledge-card__facts { display: grid; margin: 0; padding: 0 10px 10px; }
.knowledge-card__facts > div { min-width: 0; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }
.knowledge-card__facts dt { color: var(--text-3); font-size: 12px; }
.knowledge-card__facts dd { margin: 2px 0 0; overflow-wrap: anywhere; font-size: 13px; }
.knowledge-card__details { margin: 0 10px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }
.knowledge-card__details summary { display: flex; min-height: 44px; align-items: center; padding: 8px 10px; color: var(--text-2); cursor: pointer; }
.knowledge-card__details > div { padding: 0 10px 10px; border-top: 1px solid var(--border); color: var(--text-2); font-size: 13px; }
.knowledge-card__details strong { color: var(--text); }
.knowledge-card__details ul { margin: 5px 0 8px; padding-left: 20px; }
.knowledge-card__details li, .knowledge-card__details p { overflow-wrap: anywhere; }
.knowledge-card__footer { display: flex; min-height: 52px; align-items: center; justify-content: flex-end; gap: 8px; padding: 4px 10px; border-top: 1px solid var(--border); }
.knowledge-card__footer p { margin: 0 auto 0 0; color: var(--warning); font-size: 13px; }
.knowledge-card__actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.knowledge-card__actions button { min-height: 44px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); cursor: pointer; }
.knowledge-card__actions button:hover { border-color: var(--border-strong); background: var(--surface-3); }
.knowledge-card__actions button:disabled { cursor: wait; opacity: .58; }
.knowledge-card__actions .danger-button { border-color: color-mix(in srgb, var(--danger) 50%, var(--border)); color: var(--danger); }

@media (max-width: 520px) {
  .knowledge-card__footer { align-items: stretch; flex-direction: column; }
  .knowledge-card__actions { display: grid; grid-template-columns: 1fr; }
  .knowledge-card__actions button { width: 100%; }
}
</style>
