<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import KnowledgeActionDialog from './KnowledgeActionDialog.vue'
import KnowledgeCard from './KnowledgeCard.vue'
import KnowledgeFilters from './KnowledgeFilters.vue'
import KnowledgeSummary from './KnowledgeSummary.vue'
import { useKnowledge } from '../composables/useKnowledge'
import type {
  KnowledgeAction,
  KnowledgeApprovePayload,
  KnowledgeCreatePayload,
  KnowledgeItem,
  KnowledgeRejectPayload,
  KnowledgeSupersedePayload,
} from '../types/knowledge'

const emit = defineEmits<{ refreshState: [value: boolean] }>()
const {
  response,
  items,
  hasValidList,
  loading,
  refreshing,
  listError,
  actionError,
  actionMessage,
  activeAction,
  loadKnowledge,
  createKnowledge,
  approveKnowledge,
  rejectKnowledge,
  supersedeKnowledge,
  deleteKnowledge,
  clearActionFeedback,
} = useKnowledge()

const keyword = ref('')
const category = ref('all')
const status = ref('all')
const dialogOpen = ref(false)
const dialogMode = ref<KnowledgeAction>('create')
const dialogItem = ref<KnowledgeItem | null>(null)
const showLoadingSkeleton = ref(false)
let loadingDelayTimer: ReturnType<typeof setTimeout> | null = null

const filteredItems = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase('zh-CN')
  return items.value.filter((item) => {
    if (category.value !== 'all' && item.category !== category.value) return false
    if (status.value !== 'all' && item.status !== status.value) return false
    if (!query) return true
    const title = typeof item.title === 'string' ? item.title.toLocaleLowerCase('zh-CN') : ''
    const content = typeof item.content === 'string' ? item.content.toLocaleLowerCase('zh-CN') : ''
    return title.includes(query) || content.includes(query)
  })
})

const filterActive = computed(() => Boolean(keyword.value.trim()) || category.value !== 'all' || status.value !== 'all')

function openAction(type: KnowledgeAction, item: KnowledgeItem | null = null) {
  clearActionFeedback()
  dialogMode.value = type
  dialogItem.value = item
  dialogOpen.value = true
}

function closeDialog() {
  if (!activeAction.value) dialogOpen.value = false
}

async function submitDialog(payload: object) {
  const itemId = typeof dialogItem.value?.id === 'string' ? dialogItem.value.id : ''
  let success = false
  if (dialogMode.value === 'create') {
    success = await createKnowledge(payload as KnowledgeCreatePayload)
  } else if (dialogMode.value === 'approve' && itemId) {
    success = await approveKnowledge(itemId, payload as KnowledgeApprovePayload)
  } else if (dialogMode.value === 'reject' && itemId) {
    success = await rejectKnowledge(itemId, payload as KnowledgeRejectPayload)
  } else if (dialogMode.value === 'supersede' && itemId) {
    success = await supersedeKnowledge(itemId, payload as KnowledgeSupersedePayload)
  } else if (dialogMode.value === 'delete' && itemId) {
    success = await deleteKnowledge(itemId)
  }
  if (success) dialogOpen.value = false
}

function resetFilters() {
  keyword.value = ''
  category.value = 'all'
  status.value = 'all'
}

watch([loading, refreshing], ([isLoading, isRefreshing]) => {
  emit('refreshState', isLoading || isRefreshing)
}, { immediate: true })

watch(loading, (value) => {
  if (loadingDelayTimer) clearTimeout(loadingDelayTimer)
  showLoadingSkeleton.value = false
  if (value) {
    loadingDelayTimer = setTimeout(() => { showLoadingSkeleton.value = true }, 320)
  }
}, { immediate: true })

onBeforeUnmount(() => {
  if (loadingDelayTimer) clearTimeout(loadingDelayTimer)
  emit('refreshState', false)
})

defineExpose({ refresh: loadKnowledge })
</script>

<template>
  <div class="knowledge-page">
    <header class="knowledge-page__header">
      <div>
        <p class="eyebrow">PERSONAL DECISION MEMORY</p>
        <h1 id="knowledge-page-title">知识库</h1>
        <p>沉淀个人交易规则，核对来源，并明确哪些内容会进入后续 AI 上下文。</p>
      </div>
      <button class="knowledge-primary-button" type="button" :disabled="Boolean(activeAction)" @click="openAction('create')">
        新增知识
      </button>
    </header>

    <p v-if="actionMessage" class="knowledge-feedback is-success" aria-live="polite">{{ actionMessage }}</p>
    <p v-if="actionError && !dialogOpen" class="knowledge-feedback is-error" role="alert">{{ actionError }}</p>

    <section v-if="loading && !showLoadingSkeleton" class="knowledge-connect panel" role="status" aria-live="polite">
      正在读取知识库治理状态…
    </section>

    <section v-else-if="loading" class="knowledge-loading" role="status" aria-label="正在加载知识库">
      <div class="knowledge-loading__summary panel"></div>
      <div class="knowledge-loading__workspace">
        <div class="knowledge-loading__filters panel"></div>
        <div class="knowledge-loading__cards">
          <div class="panel"></div><div class="panel"></div><div class="panel"></div><div class="panel"></div>
        </div>
      </div>
    </section>

    <section v-else-if="!hasValidList" class="knowledge-failure panel" role="alert" aria-labelledby="knowledge-failure-title">
      <p class="eyebrow">DATA UNAVAILABLE</p>
      <h2 id="knowledge-failure-title">知识库暂不可用</h2>
      <p>{{ listError || '接口没有返回可验证的 items 数组，不能把它当作空知识库。' }}</p>
      <button type="button" :disabled="refreshing" @click="loadKnowledge">重新读取</button>
    </section>

    <template v-else>
      <KnowledgeSummary :response="response" :items="items" />

      <p v-if="listError" class="knowledge-feedback is-warning" role="alert">
        本次刷新失败：{{ listError }} 已保留上次成功列表，不能据此判断条目已删除或为空。
      </p>

      <div class="knowledge-workspace">
        <KnowledgeFilters
          :items="items"
          :keyword="keyword"
          :category="category"
          :status="status"
          @update:keyword="keyword = $event"
          @update:category="category = $event"
          @update:status="status = $event"
        />

        <section class="knowledge-list" aria-labelledby="knowledge-list-title">
          <div class="knowledge-list__heading panel">
            <div>
              <h2 id="knowledge-list-title">知识明细</h2>
              <p aria-live="polite">当前显示 {{ filteredItems.length }} / {{ items.length }} 条真实记录</p>
            </div>
            <button v-if="filterActive" type="button" @click="resetFilters">清除筛选</button>
          </div>

          <div v-if="items.length === 0" class="knowledge-empty panel" role="status">
            <h3>知识库暂无条目</h3>
            <p>服务端返回了真实空数组；没有使用示例规则、数字或默认内容补位。</p>
            <button class="knowledge-primary-button" type="button" @click="openAction('create')">新增第一条知识</button>
          </div>

          <div v-else-if="filteredItems.length === 0" class="knowledge-empty panel" role="status">
            <h3>没有匹配的条目</h3>
            <p>当前筛选结果为空，但知识库本身不是空库。</p>
            <button type="button" @click="resetFilters">清除筛选</button>
          </div>

          <div v-else class="knowledge-card-grid">
            <KnowledgeCard
              v-for="item in filteredItems"
              :key="item.id || `${item.rule_id}-${item.version}-${item.created_at}`"
              :item="item"
              :busy="Boolean(activeAction)"
              @action="openAction"
            />
          </div>
        </section>
      </div>

      <footer class="knowledge-governance panel">
        <span>状态、审批与版本关系均以服务端为准。</span>
        <span>条目字段缺失保持“未提供”，不补 0、不猜测。</span>
        <span>本页面只请求知识库接口；共享 App 壳的市场状态请求不属于本页业务数据。</span>
      </footer>
    </template>

    <KnowledgeActionDialog
      :open="dialogOpen"
      :mode="dialogMode"
      :item="dialogItem"
      :busy="Boolean(activeAction)"
      :server-error="actionError"
      @close="closeDialog"
      @submit="submitDialog"
    />
  </div>
</template>

<style scoped>
.knowledge-page { display: grid; min-width: 0; gap: 12px; }
.knowledge-page__header { display: flex; min-height: 76px; align-items: center; justify-content: space-between; gap: 16px; }
.knowledge-page__header h1 { margin: 0; font-size: 24px; }
.knowledge-page__header p:last-child { margin: 3px 0 0; color: var(--text-2); }
.knowledge-primary-button, .knowledge-failure button, .knowledge-empty button, .knowledge-list__heading button { min-height: 44px; padding: 8px 14px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); color: var(--text); cursor: pointer; }
.knowledge-primary-button { border-color: var(--accent); background: var(--accent); color: #111; font-weight: 760; }
:global(:root[data-theme="light"] .knowledge-primary-button) { color: #fff; }
.knowledge-primary-button:disabled, .knowledge-failure button:disabled { cursor: wait; opacity: .58; }
.knowledge-feedback { margin: 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.knowledge-feedback.is-success { border-color: color-mix(in srgb, var(--info) 52%, var(--border)); color: var(--info); }
.knowledge-feedback.is-error { border-color: color-mix(in srgb, var(--danger) 52%, var(--border)); color: var(--danger); }
.knowledge-feedback.is-warning { border-color: color-mix(in srgb, var(--warning) 52%, var(--border)); color: var(--warning); }
.knowledge-connect { min-height: 92px; padding: 24px 16px; color: var(--text-2); }
.knowledge-loading { display: grid; gap: 12px; }
.knowledge-loading__summary, .knowledge-loading__filters, .knowledge-loading__cards > div { position: relative; overflow: hidden; background: var(--surface-2); }
.knowledge-loading__summary::after, .knowledge-loading__filters::after, .knowledge-loading__cards > div::after { position: absolute; inset: 0; background: color-mix(in srgb, var(--text-3) 11%, transparent); content: ""; animation: knowledge-pulse 1.2s ease-in-out infinite alternate; }
.knowledge-loading__summary { min-height: 250px; }
.knowledge-loading__workspace { display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 12px; }
.knowledge-loading__filters { min-height: 420px; }
.knowledge-loading__cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.knowledge-loading__cards > div { min-height: 230px; }
.knowledge-failure { padding: 24px; }
.knowledge-failure h2 { margin: 0; }
.knowledge-failure p:not(.eyebrow) { margin: 6px 0 16px; color: var(--text-2); }
.knowledge-workspace { display: grid; grid-template-columns: 240px minmax(0, 1fr); align-items: start; gap: 12px; min-width: 0; }
.knowledge-list { display: grid; min-width: 0; gap: 12px; }
.knowledge-list__heading { display: flex; min-height: 62px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 12px; }
.knowledge-list__heading h2 { margin: 0; font-size: 18px; }
.knowledge-list__heading p { margin: 2px 0 0; color: var(--text-3); font-size: 13px; }
.knowledge-card-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: start; gap: 10px; min-width: 0; }
.knowledge-empty { display: grid; min-height: 250px; place-items: center; align-content: center; padding: 24px; text-align: center; }
.knowledge-empty h3 { margin: 0; }
.knowledge-empty p { max-width: 620px; margin: 6px 0 16px; color: var(--text-2); }
.knowledge-governance { display: flex; flex-wrap: wrap; gap: 4px 16px; padding: 10px 12px; color: var(--text-3); font-size: 12px; }

@keyframes knowledge-pulse { from { opacity: .3; } to { opacity: .75; } }

@media (max-width: 1180px) {
  .knowledge-card-grid, .knowledge-loading__cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 900px) {
  .knowledge-workspace, .knowledge-loading__workspace { grid-template-columns: 1fr; }
  .knowledge-loading__filters { min-height: 260px; }
}

@media (max-width: 720px) {
  .knowledge-page__header { align-items: stretch; flex-direction: column; }
  .knowledge-page__header .knowledge-primary-button { width: 100%; }
  .knowledge-card-grid, .knowledge-loading__cards { grid-template-columns: 1fr; }
}

@media (max-width: 520px) {
  .knowledge-list__heading { align-items: stretch; flex-direction: column; }
  .knowledge-list__heading button { width: 100%; }
}

@media (prefers-reduced-motion: reduce) {
  .knowledge-loading__summary::after, .knowledge-loading__filters::after, .knowledge-loading__cards > div::after { animation: none; }
}
</style>
