<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import PremarketDecisionSnapshot from './PremarketDecisionSnapshot.vue'
import ReportDocument from './ReportDocument.vue'
import { usePremarketReport } from '../composables/usePremarketReport'
import type { ReportOutlineItem } from '../utils/reportMarkdown'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  report,
  liveFlow,
  loading,
  refreshing,
  generating,
  pushing,
  error,
  flowError,
  generationStage,
  generationPreview,
  actionNotice,
  hasReport,
  refresh,
  generate,
  stopGeneration,
  pushReport,
} = usePremarketReport()

const outline = ref<ReportOutlineItem[]>([])

watch(refreshing, (value) => emit('refreshState', value), { immediate: true })

const reportVersion = computed(() => {
  const version = report.value?.plan_version || report.value?.report_version || 1
  const fingerprint = String(report.value?.content_hash || '').slice(0, 12)
  return fingerprint ? `定稿 v${version} · ${fingerprint}` : `定稿 v${version} · 指纹未记录`
})

const reportTime = computed(() => {
  const value = report.value?.created_at || ''
  const parts = value.split(' ')
  return { date: report.value?.date || parts[0] || '日期待确认', time: parts[1] || '时点待确认' }
})

const governanceLabel = computed(() => {
  const advice = report.value?.advice_governance
  const claim = report.value?.claim_validation
  if (advice?.status === 'passed' && claim?.status === 'passed') return '治理校验通过'
  if (advice?.proposal_only) return '候选提案 · 非下单指令'
  return '报告留痕可追溯'
})

function scrollToSection(id: string) {
  const target = document.getElementById(id)
  target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  window.setTimeout(() => target?.focus({ preventScroll: true }), 220)
}

function printReport() {
  window.print()
}

defineExpose({ refresh })
</script>

<template>
  <section class="premarket-page" aria-labelledby="premarket-page-title">
    <header class="premarket-page__intro">
      <div class="page-heading">
        <h1 id="premarket-page-title">盘前报告</h1>
        <p class="page-subtitle">先确认能否参与与风险边界，再按章节核对依据。盘中验证只提示偏差，不改写盘前事实。</p>
      </div>
    </header>

    <div v-if="loading" class="premarket-loading" aria-label="盘前报告正在加载">
      <section class="panel skeleton-block premarket-loading__decision"></section>
      <section class="panel skeleton-block premarket-loading__report"></section>
    </div>

    <template v-else-if="hasReport">
      <div v-if="error" class="premarket-inline-alert" role="alert">
        <strong>最新刷新失败，当前保留已加载报告</strong>
        <span>{{ error }}</span>
      </div>

      <PremarketDecisionSnapshot
        :snapshot="report?.cockpit"
        :live-flow="liveFlow"
        :flow-error="flowError"
      />

      <div class="report-layout">
        <details class="panel report-outline" open>
          <summary>
            <span>报告目录</span>
            <span class="mono">{{ outline.length }} 节</span>
          </summary>
          <nav aria-label="盘前报告章节">
            <button
              v-for="item in outline"
              :key="item.id"
              type="button"
              :class="{ 'is-subsection': item.level > 1 }"
              @click="scrollToSection(item.id)"
            >
              <span aria-hidden="true"></span>{{ item.label }}
            </button>
          </nav>
          <div class="report-outline__foot">
            <strong>{{ governanceLabel }}</strong>
            <span>报告只作受约束解释，风险与执行边界以服务端规则为准。</span>
          </div>
        </details>

        <article class="panel report-document" aria-labelledby="report-document-title">
          <header class="report-document__header">
            <div>
              <p class="eyebrow">正式定稿</p>
              <h2 id="report-document-title">{{ report?.title || '早盘内参' }}</h2>
              <div class="report-document__meta">
                <span class="mono">{{ reportVersion }}</span>
                <span v-if="report?.pushed" class="report-state-badge">已推送</span>
                <span v-else class="report-state-badge is-neutral">待推送</span>
              </div>
            </div>
            <div class="report-document__actions">
              <button type="button" class="secondary-button" @click="printReport">
                <AppIcon name="print" :size="18" />打印 / 导出 PDF
              </button>
              <button type="button" class="primary-button" :disabled="pushing" @click="pushReport">
                <AppIcon name="send" :size="18" />{{ pushing ? '推送中' : report?.pushed ? '再次推送' : '推送' }}
              </button>
            </div>
          </header>

          <p v-if="actionNotice" class="report-action-notice" role="status">{{ actionNotice }}</p>
          <ReportDocument :content="report?.content_md || ''" @outline="outline = $event" />
        </article>
      </div>
    </template>

    <section v-else class="panel report-empty" aria-labelledby="report-empty-title">
      <span class="report-empty__mark" aria-hidden="true"><AppIcon name="report" :size="28" /></span>
      <p class="eyebrow">报告状态</p>
      <h2 id="report-empty-title">今日盘前报告尚未生成</h2>
      <p>{{ error || '计划在北京时间 08:50 后生成；缺失时不使用示例数据补位。' }}</p>

      <div v-if="generating" class="report-generation" aria-live="polite">
        <strong>{{ generationStage || '报告生成中…' }}</strong>
        <span>生成由后台托管；停止查看不会把未完成内容保存为正式报告。</span>
        <div v-if="generationPreview" class="generation-preview">{{ generationPreview.slice(-800) }}</div>
        <button type="button" class="danger-button" @click="stopGeneration">停止生成</button>
      </div>
      <div v-else class="report-empty__actions">
        <button type="button" class="primary-button" @click="generate">
          <AppIcon name="spark" :size="18" />立即生成
        </button>
        <button type="button" class="secondary-button" @click="refresh">
          <AppIcon name="refresh" :size="18" />刷新状态
        </button>
        <a class="secondary-button" href="#/history?tab=reports">查看历史报告</a>
      </div>
    </section>

    <div class="premarket-page__stamp" aria-label="报告时点">
      <span>{{ reportTime.date }}</span>
      <strong class="mono">{{ reportTime.time }}</strong>
    </div>
  </section>
</template>
