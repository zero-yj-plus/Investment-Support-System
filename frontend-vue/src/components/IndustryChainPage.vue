<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useIndustryChain } from '../composables/useIndustryChain'
import IndustryChainHistory from './IndustryChainHistory.vue'
import IndustryChainResearchForm from './IndustryChainResearchForm.vue'
import IndustryChainSummary from './IndustryChainSummary.vue'
import ReportDocument from './ReportDocument.vue'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const inputRef = ref<InstanceType<typeof IndustryChainResearchForm> | null>(null)
const {
  industryInput, currentIndustry, content, runState, stageText, stageSeconds,
  runError, fieldError, history, activeHistoryId, saving, saveError, saveMessage,
  running, summary, canSave, refreshing, startResearch, stopResearch,
  selectHistory, saveToKnowledge, refresh: rerun,
} = useIndustryChain()

watch(refreshing, (value) => emit('refreshState', value), { immediate: true })
onBeforeUnmount(() => emit('refreshState', false))

const resultTitle = computed(() => currentIndustry.value ? `${currentIndustry.value} · 产业链研究` : '产业链研究正文')
const resultDescription = computed(() => {
  if (runState.value === 'loading') return '正在接收服务端冻结证据与受约束解释。'
  if (runState.value === 'partial') return '本次输出未完整结束，正文已隔离，仅供检查。'
  if (summary.value.trueEmpty) return '接口成功返回真实空映射；没有用产业常识补公司。'
  return '完整正文按服务端顺序呈现，表格只在自身容器内横向滚动。'
})

async function refresh() {
  if (currentIndustry.value) {
    await rerun()
    return
  }
  document.querySelector<HTMLInputElement>('#industry-chain-input')?.focus()
}

defineExpose({ refresh })
</script>

<template>
  <section class="industry-chain-page" aria-labelledby="industry-chain-page-title" :aria-busy="refreshing">
    <header class="chain-page-header">
      <div class="page-heading">
        <h1 id="industry-chain-page-title">产业链研究</h1>
        <p class="page-subtitle">从需求、产业地图和利润池开始，收敛到可核验公司与失效条件；产业结论和交易时点分开。</p>
      </div>
    </header>

    <IndustryChainResearchForm
      ref="inputRef"
      v-model="industryInput"
      class="panel"
      :running="running"
      :disabled="saving"
      :error="fieldError"
      @submit="startResearch()"
      @stop="stopResearch"
    />

    <div v-if="running" class="chain-progress panel" role="status" aria-live="polite">
      <div><strong>{{ stageText || '正在核验产业链证据…' }}</strong><span>完成前不形成交易结论；可随时停止。</span></div>
      <span class="mono">{{ stageSeconds }}s</span>
    </div>
    <p v-if="runError" class="chain-alert" role="alert">{{ runError }}</p>

    <IndustryChainSummary :state="runState" :industry="currentIndustry" :summary="summary" />

    <div class="chain-workbench">
      <section class="chain-document panel" aria-labelledby="chain-document-title">
        <header>
          <div><p class="eyebrow">SAFE MARKDOWN</p><h2 id="chain-document-title">{{ resultTitle }}</h2><p>{{ resultDescription }}</p></div>
          <div v-if="runState === 'complete'" class="chain-document__actions">
            <button class="secondary-button" type="button" :disabled="running || saving" @click="startResearch(currentIndustry)">重新研究</button>
            <button class="primary-button" type="button" :disabled="!canSave" @click="saveToKnowledge">{{ saving ? '写入中' : '存入知识库' }}</button>
          </div>
        </header>

        <div v-if="runState === 'idle' || (runState === 'error' && !content)" class="chain-document__empty">
          <strong>{{ runState === 'error' ? '本轮没有形成结果' : '等待产业链主题' }}</strong>
          <span>不会显示示例公司、示例数字或用 0 填补缺失数据。</span>
        </div>
        <div v-else class="chain-document__body" :class="{ 'is-partial': runState === 'partial' }">
          <p v-if="runState === 'partial'" class="chain-quarantine" role="alert">不完整输出 · 已隔离 · 不可存入知识库</p>
          <p v-if="summary.trueEmpty" class="chain-true-empty" role="status">真实空结果：TDX 板块或成分关系未匹配；公司映射保持空，不使用推测值补齐。</p>
          <p v-if="summary.deterministicFallback" class="chain-fallback" role="status">AI 解释未形成完整增强；当前展示确定性证据骨架，不影响已冻结的真实成员与缺口。</p>
          <nav v-if="summary.companies.length" class="chain-company-links" aria-label="公司映射快速进入个股诊断">
            <span>核验候选</span>
            <a v-for="company in summary.companies" :key="company.code" :href="`#/research?tab=diagnosis&code=${encodeURIComponent(company.code)}`">
              <strong>{{ company.name }}</strong><span class="mono">{{ company.code }}</span><small>P{{ company.priority }}</small>
            </a>
          </nav>
          <ReportDocument :content="content" />
        </div>

        <div class="chain-save-feedback" aria-live="polite">
          <p v-if="saveMessage" class="is-success">{{ saveMessage }}</p>
          <p v-if="saveError" class="is-error" role="alert">{{ saveError }}</p>
        </div>
      </section>

      <aside class="chain-sidebar" aria-label="产业链研究历史与数据治理">
        <IndustryChainHistory :entries="history" :active-id="activeHistoryId" :disabled="running || saving" @select="selectHistory" />
        <section class="chain-governance panel" aria-labelledby="chain-governance-title">
          <header><p class="eyebrow">DATA GOVERNANCE</p><h2 id="chain-governance-title">口径与限制</h2></header>
          <dl>
            <div><dt>真实接口</dt><dd><code>POST /api/chat</code>，仅在主动研究时请求。</dd></div>
            <div><dt>单位</dt><dd>现价为元；涨跌幅为 %；PE/PB 为倍数；数据可研分无量纲。</dd></div>
            <div><dt>确定性边界</dt><dd>TDX 成员仅证明板块归属；产业角色、兑现、价格、仓位与风险均不得由浏览器推断。</dd></div>
            <div><dt>缺失语义</dt><dd>传输失败、不完整流、后端缺失、真实空映射和模型降级分别展示；缺失值不补 0。</dd></div>
          </dl>
        </section>
      </aside>
    </div>

    <footer class="page-footer">
      <span>Markdown 经安全 Vue 节点解析；不使用 v-html 或运行时 HTML 注入。</span>
      <span>写入知识库不等于已审批，也不等于已进入模型知识。</span>
    </footer>

    <dl class="chain-data-stamp" aria-label="产业链研究数据与契约状态">
      <div><dt>数据日期 / 时点</dt><dd class="mono">{{ runState === 'complete' || runState === 'partial' ? summary.dataAsOf : '尚未研究' }}</dd></div>
      <div><dt>输出契约</dt><dd class="mono">chain-research-v2</dd></div>
      <div><dt>陈旧判定</dt><dd>接口未独立暴露</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.industry-chain-page { display: grid; min-width: 0; gap: 12px; }.chain-page-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }.chain-page-header h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }.chain-page-header > div > p:last-child { max-width: 760px; margin: 7px 0 0; color: var(--text-2); }.chain-page-header dl { display: grid; min-width: 520px; grid-template-columns: 1.5fr .9fr .8fr; gap: 10px; margin: 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }.chain-page-header dl div { min-width: 0; }.chain-page-header dt { color: var(--text-3); font-size: 12px; }.chain-page-header dd { margin: 3px 0 0; color: var(--text-2); font-size: 13px; font-weight: 720; overflow-wrap: anywhere; }
.chain-progress { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-style: dashed; }.chain-progress > div { display: grid; gap: 3px; }.chain-progress strong { color: var(--text); }.chain-progress span { color: var(--text-3); font-size: 13px; }.chain-progress > .mono { color: var(--accent); font-size: 18px; font-weight: 760; }.chain-alert { margin: 0; padding: 10px 12px; border: 1px solid var(--warning); border-radius: var(--radius-sm); background: color-mix(in srgb, var(--warning) 7%, var(--surface)); color: var(--warning); }
.chain-workbench { display: grid; grid-template-columns: minmax(0, 1fr) 264px; align-items: start; gap: 12px; min-width: 0; }.chain-document { min-width: 0; overflow: hidden; }.chain-document > header { display: flex; min-height: 72px; align-items: center; justify-content: space-between; gap: 14px; padding: 11px 14px; border-bottom: 1px solid var(--border); }.chain-document > header h2 { margin: 0; font-size: 19px; }.chain-document > header p:last-child { margin: 4px 0 0; color: var(--text-3); font-size: 13px; }.chain-document__actions { display: flex; flex: 0 0 auto; gap: 8px; }.chain-document__empty { display: grid; min-height: 360px; place-content: center; gap: 5px; padding: 24px; color: var(--text-3); text-align: center; }.chain-document__empty strong { color: var(--text-2); font-size: 18px; }.chain-document__body { min-width: 0; padding: 8px 14px 18px; }.chain-document__body.is-partial { border-left: 3px solid var(--warning); }.chain-document__body :deep(.report-markdown) { max-width: none; padding: 4px 0 18px; }.chain-document__body :deep(.report-table-wrap) { outline-offset: 3px; }.chain-quarantine, .chain-true-empty, .chain-fallback { margin: 8px 0; padding: 9px 10px; border: 1px solid var(--warning); border-radius: var(--radius-sm); color: var(--warning); font-size: 13px; font-weight: 700; }.chain-true-empty { border-color: var(--info); color: var(--info); }.chain-fallback { border-color: var(--border-strong); color: var(--text-2); }.chain-company-links { display: flex; max-width: 100%; align-items: stretch; gap: 6px; margin: 10px 0; padding: 8px; overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }.chain-company-links > span { display: flex; flex: 0 0 auto; align-items: center; padding: 0 4px; color: var(--text-3); font-size: 12px; }.chain-company-links a { display: grid; grid-template-columns: auto auto; min-width: 148px; min-height: 44px; align-content: center; gap: 1px 7px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); text-decoration: none; }.chain-company-links a:hover { border-color: var(--accent); }.chain-company-links a strong { max-width: 112px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.chain-company-links a span, .chain-company-links a small { color: var(--text-3); font-size: 11px; }.chain-company-links a small { grid-column: 1 / -1; }.chain-save-feedback { min-height: 1px; padding: 0 14px; }.chain-save-feedback p { margin: 0 0 12px; padding: 9px 10px; border: 1px solid currentColor; border-radius: var(--radius-sm); font-size: 13px; }.chain-save-feedback .is-success { color: var(--info); }.chain-save-feedback .is-error { color: var(--danger); }
.chain-sidebar { display: grid; gap: 12px; min-width: 0; }.chain-governance { overflow: hidden; }.chain-governance > header { padding: 10px 12px; border-bottom: 1px solid var(--border); }.chain-governance h2 { margin: 0; font-size: 17px; }.chain-governance dl { margin: 0; }.chain-governance dl div { padding: 10px 12px; border-bottom: 1px solid var(--border); }.chain-governance dl div:last-child { border-bottom: 0; }.chain-governance dt { color: var(--text-3); font-size: 12px; font-weight: 700; }.chain-governance dd { margin: 3px 0 0; color: var(--text-2); font-size: 13px; line-height: 1.55; }.chain-governance code { color: var(--accent); font-family: var(--font-num); }
@media (max-width: 1180px) { .chain-page-header { align-items: stretch; flex-direction: column; gap: 12px; }.chain-page-header dl { min-width: 0; } }
@media (max-width: 1039px) { .chain-workbench { grid-template-columns: minmax(0, 1fr); }.chain-sidebar { order: -1; }.chain-governance dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }.chain-governance dl div { border-right: 1px solid var(--border); }.chain-governance dl div:nth-child(even) { border-right: 0; } }
@media (max-width: 720px) { .industry-chain-page { gap: 8px; }.chain-page-header dl { grid-template-columns: minmax(0, 1fr); }.chain-document > header { align-items: flex-start; flex-direction: column; }.chain-document__actions { width: 100%; }.chain-document__actions button { flex: 1 1 0; }.chain-document__body { padding-inline: 10px; }.chain-governance dl { grid-template-columns: minmax(0, 1fr); }.chain-governance dl div { border-right: 0; }.chain-company-links { margin-inline: 0; } }
@media (max-width: 420px) { .chain-document__actions { flex-direction: column; }.chain-document__actions button { width: 100%; } }
.chain-data-stamp { display: grid; min-width: 520px; grid-template-columns: 1.5fr .9fr .8fr; gap: 10px; margin: 0; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.chain-data-stamp div { min-width: 0; }.chain-data-stamp dt { color: var(--text-3); font-size: 12px; }.chain-data-stamp dd { margin: 3px 0 0; color: var(--text-2); font-size: 13px; font-weight: 720; overflow-wrap: anywhere; }
@media (max-width: 1180px) { .chain-data-stamp { min-width: 0; } }
@media (max-width: 720px) { .chain-data-stamp { grid-template-columns: minmax(0, 1fr); } }
</style>
