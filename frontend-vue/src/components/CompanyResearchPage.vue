<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { useCompanyResearch } from '../composables/useCompanyResearch'
import CompanyResearchEvidence from './CompanyResearchEvidence.vue'
import CompanyResearchForm from './CompanyResearchForm.vue'
import CompanyResearchHistory from './CompanyResearchHistory.vue'
import CompanyResearchSummary from './CompanyResearchSummary.vue'
import ReportDocument from './ReportDocument.vue'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  codeInput, currentCode, content, runState, runError, fieldError, stageText,
  stageSeconds, completedAt, statusLoading, modelReady, modelReason,
  enrichment, disclosures, interactions, history, activeHistoryId, saving,
  saveError, saveMessage, running, evidenceLoading, canSave, refreshing,
  startResearch, stopResearch, selectHistory, saveToKnowledge, refresh,
} = useCompanyResearch()

watch(refreshing, (value) => emit('refreshState', value), { immediate: true })
onBeforeUnmount(() => emit('refreshState', false))

const dataAsOf = computed(() => (
  disclosures.value.data?.data_as_of
  || disclosures.value.data?.evidence?.data_as_of
  || enrichment.value.data?.ts
  || interactions.value.data?.data_as_of
  || '尚未核验'
))
const generatedAt = computed(() => {
  if (!completedAt.value) return '尚未生成'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(completedAt.value))
})
const reportTitle = computed(() => currentCode.value ? `${currentCode.value} · 单公司深度研究` : '公司研究正文')
const reportDescription = computed(() => {
  if (runState.value === 'loading') return '正在接收服务端治理后的报告；完成标记返回前不视为完整。'
  if (runState.value === 'partial') return '不完整输出已隔离，仅供检查，不能保存。'
  if (runState.value === 'complete') return '报告按服务端顺序展示；交易段已经过后端治理。'
  return '尚未生成正文；结构化证据与模型输出保持分离。'
})

defineExpose({ refresh })
</script>

<template>
  <section class="company-research-page" aria-labelledby="company-page-title" :aria-busy="refreshing">
    <header class="company-page-header">
      <div class="page-heading">
        <h1 id="company-page-title">公司研究</h1>
        <p class="page-subtitle">公告、管理层互动与低频研究底座独立核验；模型只生成受治理的解释正文。</p>
      </div>
    </header>

    <CompanyResearchForm
      v-model="codeInput"
      class="panel"
      :running="running"
      :busy="refreshing"
      :status-loading="statusLoading"
      :model-ready="modelReady"
      :model-reason="modelReason"
      :error="fieldError"
      @submit="startResearch()"
      @stop="stopResearch"
    />

    <div v-if="running" class="company-progress panel" role="status" aria-live="polite">
      <div><strong>{{ stageText || '正在生成公司研究…' }}</strong><span>完成前不形成完整结论；官方证据分区会独立更新。</span></div>
      <span class="mono">{{ stageSeconds }}s</span>
    </div>

    <CompanyResearchSummary
      :state="runState"
      :code="currentCode"
      :run-error="runError"
      :model-ready="modelReady"
      :evidence-loading="evidenceLoading"
    />

    <div class="company-workbench">
      <div class="company-main-column">
        <CompanyResearchEvidence
          :code="currentCode"
          :enrichment="enrichment"
          :disclosures="disclosures"
          :interactions="interactions"
        />

        <section class="company-report panel" aria-labelledby="company-report-title">
          <header>
            <div><p class="eyebrow">SAFE MARKDOWN</p><h2 id="company-report-title">{{ reportTitle }}</h2><p>{{ reportDescription }}</p></div>
            <div v-if="currentCode && !running" class="company-report__actions">
              <button class="secondary-button" type="button" :disabled="saving" @click="startResearch(currentCode)">重新生成</button>
              <button v-if="runState === 'complete'" class="primary-button" type="button" :disabled="!canSave" @click="saveToKnowledge">{{ saving ? '写入中' : '存入知识库' }}</button>
            </div>
          </header>

          <div v-if="runState === 'idle' || (runState === 'error' && !content)" class="company-report__empty">
            <strong>{{ runState === 'error' ? '本轮没有形成研究正文' : '等待公司代码' }}</strong>
            <span>{{ runError || '不会显示示例公司、示例数字，也不会把缺失值补成 0。' }}</span>
          </div>
          <div v-else-if="running && !content" class="company-report__loading" role="status">
            <strong>正文尚未返回</strong><span>正在等待服务端首段内容；结构化证据可先独立完成。</span>
          </div>
          <div v-else class="company-report__body" :class="{ 'is-partial': runState === 'partial' }">
            <p v-if="runState === 'partial'" class="company-quarantine" role="alert">不完整输出 · 已隔离 · 不可存入知识库</p>
            <ReportDocument :content="content" />
          </div>

          <div class="company-save-feedback" aria-live="polite">
            <p v-if="saveMessage" class="is-success">{{ saveMessage }}</p>
            <p v-if="saveError" class="is-error" role="alert">{{ saveError }}</p>
          </div>
        </section>
      </div>

      <aside class="company-sidebar" aria-label="公司研究历史与数据治理">
        <CompanyResearchHistory :entries="history" :active-id="activeHistoryId" :disabled="running || saving" @select="selectHistory" />
        <section class="company-governance panel" aria-labelledby="company-governance-title">
          <header><p class="eyebrow">DATA GOVERNANCE</p><h2 id="company-governance-title">口径与限制</h2></header>
          <dl>
            <div><dt>真实接口</dt><dd><code>GET /api/status</code>、研究底座、公告、互动问答与主动 <code>POST /api/chat</code>。</dd></div>
            <div><dt>单位</dt><dd>目标价为元；EPS 为元/股；收入/利润预测为亿元；ROE 与股东比例为 %。</dd></div>
            <div><dt>确定性边界</dt><dd>本页接口没有独立风险等级和仓位上限；浏览器不从报告文本推导。</dd></div>
            <div><dt>缺失语义</dt><dd><code>missing</code>、<code>degraded</code>、真实空结果、旧快照、请求失败和不完整流分别展示。</dd></div>
            <div><dt>历史语义</dt><dd>本地研究记录只追加；不完整输出带隔离标记，不覆盖完整历史。</dd></div>
          </dl>
        </section>
      </aside>
    </div>

    <footer class="page-footer">
      <span>Markdown 经安全 Vue 节点解析，不使用 v-html 或运行时 HTML 注入。</span>
      <span>写入知识库只表示已接受保存，不等于已审批或已送达。</span>
    </footer>

    <dl class="company-data-stamp" aria-label="公司研究数据与生成时点">
      <div><dt>当前公司</dt><dd class="mono">{{ currentCode || '尚未选择' }}</dd></div>
      <div><dt>数据日期 / 时点</dt><dd class="mono">{{ dataAsOf }}</dd></div>
      <div><dt>生成时点</dt><dd class="mono">{{ generatedAt }}</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.company-research-page { display: grid; min-width: 0; gap: 12px; }.company-page-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }.company-page-header h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }.company-page-header > div > p:last-child { max-width: 760px; margin: 7px 0 0; color: var(--text-2); }.company-page-header dl { display: grid; min-width: 540px; grid-template-columns: .8fr 1.35fr 1fr; gap: 10px; margin: 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }.company-page-header dl div { min-width: 0; }.company-page-header dt { color: var(--text-3); font-size: 12px; }.company-page-header dd { margin: 3px 0 0; color: var(--text-2); font-size: 13px; font-weight: 720; overflow-wrap: anywhere; }.company-progress { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-style: dashed; }.company-progress > div { display: grid; gap: 3px; }.company-progress strong { color: var(--text); }.company-progress span { color: var(--text-3); font-size: 13px; }.company-progress > .mono { color: var(--accent); font-size: 18px; font-weight: 760; }.company-workbench { display: grid; grid-template-columns: minmax(0, 1fr) 280px; align-items: start; gap: 12px; min-width: 0; }.company-main-column, .company-sidebar { display: grid; min-width: 0; gap: 12px; }.company-report { min-width: 0; overflow: hidden; }.company-report > header { display: flex; min-height: 72px; align-items: center; justify-content: space-between; gap: 14px; padding: 11px 14px; border-bottom: 1px solid var(--border); }.company-report > header h2 { margin: 0; font-size: 19px; }.company-report > header p:last-child { margin: 4px 0 0; color: var(--text-3); font-size: 13px; }.company-report__actions { display: flex; flex: 0 0 auto; gap: 8px; }.company-report__empty, .company-report__loading { display: grid; min-height: 320px; place-content: center; gap: 5px; padding: 24px; color: var(--text-3); text-align: center; }.company-report__empty strong, .company-report__loading strong { color: var(--text-2); font-size: 18px; }.company-report__empty span, .company-report__loading span { max-width: 620px; line-height: 1.6; }.company-report__body { min-width: 0; padding: 8px 14px 18px; }.company-report__body.is-partial { border-left: 3px solid var(--warning); }.company-report__body :deep(.report-markdown) { max-width: none; padding: 4px 0 18px; }.company-report__body :deep(.report-table-wrap) { outline-offset: 3px; }.company-quarantine { margin: 8px 0; padding: 9px 10px; border: 1px solid var(--warning); border-radius: var(--radius-sm); color: var(--warning); font-size: 13px; font-weight: 700; }.company-save-feedback { min-height: 1px; padding: 0 14px; }.company-save-feedback p { margin: 0 0 12px; padding: 9px 10px; border: 1px solid currentColor; border-radius: var(--radius-sm); font-size: 13px; }.company-save-feedback .is-success { color: var(--info); }.company-save-feedback .is-error { color: var(--danger); }.company-governance { overflow: hidden; }.company-governance > header { padding: 10px 12px; border-bottom: 1px solid var(--border); }.company-governance h2 { margin: 0; font-size: 17px; }.company-governance dl { margin: 0; }.company-governance dl div { padding: 10px 12px; border-bottom: 1px solid var(--border); }.company-governance dl div:last-child { border-bottom: 0; }.company-governance dt { color: var(--text-3); font-size: 12px; font-weight: 700; }.company-governance dd { margin: 3px 0 0; color: var(--text-2); font-size: 13px; line-height: 1.55; }.company-governance code { color: var(--accent); font-family: var(--font-num); overflow-wrap: anywhere; }
@media (max-width: 1180px) { .company-page-header { align-items: stretch; flex-direction: column; gap: 12px; }.company-page-header dl { min-width: 0; } }
@media (max-width: 1039px) { .company-workbench { grid-template-columns: minmax(0, 1fr); }.company-sidebar { order: -1; grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 720px) { .company-research-page { gap: 8px; }.company-page-header dl { grid-template-columns: minmax(0, 1fr); }.company-sidebar { grid-template-columns: minmax(0, 1fr); }.company-report > header { align-items: flex-start; flex-direction: column; }.company-report__actions { width: 100%; }.company-report__actions button { flex: 1 1 0; }.company-report__body { padding-inline: 10px; } }
@media (max-width: 420px) { .company-report__actions { flex-direction: column; }.company-report__actions button { width: 100%; } }
.company-data-stamp { display: grid; min-width: 540px; grid-template-columns: .8fr 1.35fr 1fr; gap: 10px; margin: 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.company-data-stamp div { min-width: 0; }.company-data-stamp dt { color: var(--text-3); font-size: 12px; }.company-data-stamp dd { margin: 3px 0 0; color: var(--text-2); font-size: 13px; font-weight: 720; overflow-wrap: anywhere; }
@media (max-width: 1180px) { .company-data-stamp { min-width: 0; } }
@media (max-width: 720px) { .company-data-stamp { grid-template-columns: minmax(0, 1fr); } }
</style>
