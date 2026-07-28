<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  KNOWLEDGE_CATEGORIES,
  type KnowledgeAction,
  type KnowledgeApprovePayload,
  type KnowledgeCreatePayload,
  type KnowledgeItem,
  type KnowledgeRejectPayload,
  type KnowledgeSupersedePayload,
} from '../types/knowledge'

type DialogPayload = KnowledgeCreatePayload | KnowledgeApprovePayload | KnowledgeRejectPayload | KnowledgeSupersedePayload | Record<string, never>

const props = defineProps<{
  open: boolean
  mode: KnowledgeAction
  item: KnowledgeItem | null
  busy: boolean
  serverError: string
}>()

const emit = defineEmits<{
  close: []
  submit: [payload: DialogPayload]
}>()

const dialog = ref<HTMLDialogElement | null>(null)
const category = ref('复盘笔记')
const title = ref('')
const content = ref('')
const reportIds = ref('')
const evidenceIds = ref('')
const sampleCount = ref('')
const reason = ref('')
const clientError = ref('')
let opener: HTMLElement | null = null

const dialogTitle = computed(() => {
  if (props.mode === 'create') return '新增知识条目'
  if (props.mode === 'approve') return '核对来源并批准'
  if (props.mode === 'reject') return '拒绝待审知识'
  if (props.mode === 'supersede') return '创建替代版本'
  return '删除知识条目'
})

const primaryLabel = computed(() => {
  if (props.busy) return '提交中'
  if (props.mode === 'create') return '保存为已批准规则'
  if (props.mode === 'approve') return '确认批准'
  if (props.mode === 'reject') return '确认拒绝'
  if (props.mode === 'supersede') return '创建新版本并替代'
  return '确认删除'
})

function resetForm() {
  category.value = typeof props.item?.category === 'string' && KNOWLEDGE_CATEGORIES.includes(props.item.category as never)
    ? props.item.category
    : '复盘笔记'
  title.value = typeof props.item?.title === 'string' ? props.item.title : ''
  content.value = typeof props.item?.content === 'string' ? props.item.content : ''
  reportIds.value = Array.isArray(props.item?.source_report_ids) ? props.item.source_report_ids.join('\n') : ''
  evidenceIds.value = Array.isArray(props.item?.evidence_ids) ? props.item.evidence_ids.join('\n') : ''
  sampleCount.value = typeof props.item?.sample_count === 'number' ? String(props.item.sample_count) : ''
  reason.value = ''
  clientError.value = ''
}

function lines(value: string) {
  return [...new Set(value.split(/\r?\n/).map((row) => row.trim()).filter(Boolean))]
}

function submit() {
  clientError.value = ''
  if (props.mode === 'create') {
    if (!title.value.trim() || !content.value.trim()) {
      clientError.value = '标题与正文都必须填写。'
      return
    }
    emit('submit', { category: category.value, title: title.value.trim(), content: content.value.trim() })
    return
  }
  if (props.mode === 'approve') {
    const sample = Number(sampleCount.value)
    const sources = lines(reportIds.value)
    const evidence = lines(evidenceIds.value)
    if (!Number.isInteger(sample) || sample < 1) {
      clientError.value = '样本数必须是大于等于 1 的整数。'
      return
    }
    if (props.item?.origin !== 'explicit_user_input' && !sources.length && !evidence.length) {
      clientError.value = '自动或历史条目必须至少绑定一个真实报告 ID 或证据 ID。'
      return
    }
    emit('submit', { source_report_ids: sources, evidence_ids: evidence, sample_count: sample })
    return
  }
  if (props.mode === 'reject') {
    if (!reason.value.trim()) {
      clientError.value = '拒绝原因必须填写。'
      return
    }
    emit('submit', { reason: reason.value.trim() })
    return
  }
  if (props.mode === 'supersede') {
    if (!title.value.trim() || !content.value.trim() || !reason.value.trim()) {
      clientError.value = '标题、正文和替代理由都必须填写。'
      return
    }
    emit('submit', {
      category: category.value,
      title: title.value.trim(),
      content: content.value.trim(),
      reason: reason.value.trim(),
    })
    return
  }
  emit('submit', {})
}

function requestClose() {
  if (!props.busy) emit('close')
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
      resetForm()
      await nextTick()
      if (dialog.value && !dialog.value.open) dialog.value.showModal()
      await nextTick()
      dialog.value?.querySelector<HTMLElement>('input, select, textarea, button')?.focus()
      return
    }
    if (dialog.value?.open) dialog.value.close()
    await nextTick()
    opener?.focus()
  },
)
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="knowledge-dialog" :aria-labelledby="'knowledge-dialog-title'" @cancel.prevent="requestClose" @close="emit('close')">
      <form method="dialog" @submit.prevent="submit">
        <header>
          <div>
            <p class="eyebrow">KNOWLEDGE GOVERNANCE</p>
            <h2 id="knowledge-dialog-title">{{ dialogTitle }}</h2>
          </div>
          <button type="button" aria-label="关闭对话框" :disabled="busy" @click="requestClose">关闭</button>
        </header>

        <div class="knowledge-dialog__body">
          <template v-if="mode === 'create' || mode === 'supersede'">
            <label>
              <span>分类</span>
              <select v-model="category" required>
                <option v-for="option in KNOWLEDGE_CATEGORIES" :key="option" :value="option">{{ option }}</option>
              </select>
            </label>
            <label>
              <span>标题</span>
              <input v-model="title" type="text" maxlength="120" required autocomplete="off">
            </label>
            <label>
              <span>正文</span>
              <textarea v-model="content" rows="8" maxlength="8000" required></textarea>
            </label>
            <label v-if="mode === 'supersede'">
              <span>替代理由</span>
              <textarea v-model="reason" rows="3" maxlength="500" required></textarea>
            </label>
            <p v-if="mode === 'create'" class="knowledge-dialog__hint">
              当前后端契约会把用户明确录入直接记为 approved；保存不是草稿动作。
            </p>
            <p v-else class="knowledge-dialog__hint">
              服务端会新增一个已批准版本，并把旧版标为 superseded；旧版内容与版本号仍保留。
            </p>
          </template>

          <template v-else-if="mode === 'approve'">
            <p class="knowledge-dialog__hint">
              服务端会核验报告/证据 ID 是否真实存在。自动条目的样本数不能超过已核验来源数。
            </p>
            <label>
              <span>来源报告 ID（每行一个）</span>
              <textarea v-model="reportIds" rows="4" autocomplete="off"></textarea>
            </label>
            <label>
              <span>证据 ID（每行一个）</span>
              <textarea v-model="evidenceIds" rows="4" autocomplete="off"></textarea>
            </label>
            <label>
              <span>真实样本数</span>
              <input v-model="sampleCount" type="number" min="1" step="1" inputmode="numeric" required>
            </label>
          </template>

          <label v-else-if="mode === 'reject'">
            <span>拒绝原因</span>
            <textarea v-model="reason" rows="5" maxlength="500" required></textarea>
          </label>

          <div v-else class="knowledge-dialog__danger" role="note">
            <strong>删除后条目不会继续出现在知识库列表。</strong>
            <span>服务端会追加删除审计事件，但本页面无法恢复被删条目。</span>
            <span>{{ item?.title || '标题未提供' }}</span>
          </div>

          <p v-if="clientError" class="knowledge-dialog__error" role="alert">{{ clientError }}</p>
          <p v-if="serverError" class="knowledge-dialog__error" role="alert">{{ serverError }}</p>
        </div>

        <footer>
          <button type="button" :disabled="busy" @click="requestClose">取消</button>
          <button class="primary-button" :class="{ 'is-danger': mode === 'delete' }" type="submit" :disabled="busy">
            {{ primaryLabel }}
          </button>
        </footer>
      </form>
    </dialog>
  </Teleport>
</template>

<style scoped>
.knowledge-dialog { width: min(680px, calc(100vw - 32px)); max-height: min(820px, calc(100dvh - 32px)); padding: 0; overflow: hidden; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--surface); color: var(--text); }
.knowledge-dialog::backdrop { background: rgb(5 11 20 / 72%); }
.knowledge-dialog form { display: grid; max-height: min(820px, calc(100dvh - 32px)); grid-template-rows: auto minmax(0, 1fr) auto; }
.knowledge-dialog header, .knowledge-dialog footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; }
.knowledge-dialog header { border-bottom: 1px solid var(--border); }
.knowledge-dialog footer { border-top: 1px solid var(--border); background: var(--surface); }
.knowledge-dialog h2 { margin: 0; font-size: 19px; }
.knowledge-dialog button { min-height: 44px; padding: 8px 13px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); cursor: pointer; }
.knowledge-dialog button:disabled { cursor: wait; opacity: .58; }
.knowledge-dialog .primary-button { border-color: var(--accent); background: var(--accent); color: #111; font-weight: 750; }
:global(:root[data-theme="light"] .knowledge-dialog .primary-button) { color: #fff; }
.knowledge-dialog .primary-button.is-danger { border-color: var(--danger); background: var(--danger); color: #fff; }
.knowledge-dialog__body { display: grid; gap: 12px; min-height: 0; overflow-y: auto; padding: 16px; }
.knowledge-dialog label { display: grid; gap: 5px; }
.knowledge-dialog label > span { color: var(--text-2); font-size: 13px; font-weight: 700; }
.knowledge-dialog input, .knowledge-dialog select, .knowledge-dialog textarea { width: 100%; min-height: 44px; padding: 9px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); color: var(--text); }
.knowledge-dialog textarea { resize: vertical; line-height: 1.6; }
.knowledge-dialog__hint { margin: 0; padding: 9px 10px; border-left: 3px solid var(--info); background: var(--surface-2); color: var(--text-2); font-size: 13px; }
.knowledge-dialog__danger { display: grid; gap: 6px; padding: 12px; border: 1px solid color-mix(in srgb, var(--danger) 55%, var(--border)); border-radius: 6px; color: var(--text-2); }
.knowledge-dialog__danger strong, .knowledge-dialog__error { color: var(--danger); }
.knowledge-dialog__error { margin: 0; font-weight: 650; }

@media (max-width: 520px) {
  .knowledge-dialog { width: calc(100vw - 16px); max-height: calc(100dvh - 16px); }
  .knowledge-dialog form { max-height: calc(100dvh - 16px); }
  .knowledge-dialog footer { display: grid; grid-template-columns: 1fr 1fr; }
}

@media (prefers-reduced-motion: reduce) {
  .knowledge-dialog, .knowledge-dialog * { scroll-behavior: auto !important; }
}
</style>
