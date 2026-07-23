<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import CloseAdviceReview from './CloseAdviceReview.vue'
import CloseRecommendationReview from './CloseRecommendationReview.vue'
import CloseReviewDecision from './CloseReviewDecision.vue'
import ReportDocument from './ReportDocument.vue'
import { useCloseReview } from '../composables/useCloseReview'
import type { ReportOutlineItem } from '../utils/reportMarkdown'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  report, adviceReview, stats, recommendations,
  loading, refreshing, generating, pushing, verifying,
  reportError, adviceError, statsError, recommendationError, actionError,
  actionNotice, generationStage, generationPreview, hasReport,
  refresh, generate, stopGeneration, pushReport, verifyRecommendations,
} = useCloseReview()
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
  if (!target) return
  target.focus({ preventScroll: true })
  target.scrollIntoView({
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    block: 'start',
  })
}

function printReport() {
  window.print()
}

defineExpose({ refresh })
</script>

<template>
  <section class="close-review-page" aria-labelledby="close-review-page-title">
    <header class="premarket-page__intro close-review-page__intro">
      <div>
        <p class="eyebrow">交易日 15:30 收盘复盘</p>
        <h1 id="close-review-page-title">今日复盘</h1>
        <p>先确认收盘结论和风险边界，再核对昨日建议与推荐回验，最后阅读正式复盘。历史判断只追加、不回写。</p>
      </div>
      <div class="premarket-page__stamp" aria-label="复盘日期与生成时点">
        <span>{{ reportTime.date }}</span>
        <strong class="mono">{{ reportTime.time }}</strong>
      </div>
    </header>

    <div v-if="loading" class="close-review-loading" role="status" aria-live="polite">
      <span class="sr-only">今日复盘正在加载</span>
      <section class="panel skeleton-block close-loading__decision"></section>
      <section class="close-evidence-grid"><span class="panel skeleton-block close-loading__evidence"></span><span class="panel skeleton-block close-loading__evidence"></span></section>
      <section class="panel skeleton-block close-loading__report"></section>
    </div>

    <template v-else>
      <div v-if="reportError && hasReport" class="premarket-inline-alert" role="alert">
        <strong>最新复盘刷新失败，当前保留已加载定稿</strong><span>{{ reportError }}</span>
      </div>
      <div v-if="actionError" class="premarket-inline-alert" role="alert"><strong>操作未完成</strong><span>{{ actionError }}</span></div>
      <p v-if="actionNotice" class="report-action-notice close-page-notice" role="status">{{ actionNotice }}</p>

      <CloseReviewDecision :snapshot="report?.cockpit" />

      <section id="review-evidence" class="close-evidence-section" aria-labelledby="review-evidence-title">
        <div class="tail-section-heading">
          <div><p class="eyebrow">结构化复盘证据</p><h2 id="review-evidence-title">判断是否兑现，先让真实记录说话</h2></div>
          <p>持仓建议与推荐历史均不可改写；缺失、空记录和传输失败分别展示。</p>
        </div>
        <div class="close-evidence-grid">
          <CloseAdviceReview :data="adviceReview" :error="adviceError" @retry="refresh" />
          <CloseRecommendationReview
            :stats="stats"
            :recommendations="recommendations?.recommendations || []"
            :stats-error="statsError"
            :recommendation-error="recommendationError"
            :verifying="verifying"
            @verify="verifyRecommendations"
            @retry="refresh"
          />
        </div>
      </section>

      <div v-if="hasReport" class="report-layout close-report-layout">
        <details class="panel report-outline" open>
          <summary><span>复盘目录</span><span class="mono">{{ outline.length }} 节</span></summary>
          <nav aria-label="今日复盘报告章节">
            <button v-for="item in outline" :key="item.id" type="button" :class="{ 'is-subsection': item.level > 1 }" @click="scrollToSection(item.id)"><span aria-hidden="true"></span>{{ item.label }}</button>
          </nav>
          <div class="report-outline__foot">
            <strong>{{ governanceLabel }}</strong>
            <span>复盘用于验证与修正，不是实盘下单指令；风险、价格与回验以服务端规则和真实证据为准。</span>
          </div>
        </details>

        <article class="panel report-document" aria-labelledby="close-report-document-title">
          <header class="report-document__header">
            <div>
              <p class="eyebrow">唯一正式收盘复盘</p>
              <h2 id="close-report-document-title">{{ report?.title || '今日复盘定稿' }}</h2>
              <div class="report-document__meta">
                <span class="mono">{{ reportVersion }}</span>
                <span v-if="report?.pushed" class="report-state-badge">已提交推送</span>
                <span v-else class="report-state-badge is-neutral">待推送</span>
              </div>
            </div>
            <div class="report-document__actions">
              <button type="button" class="secondary-button" @click="printReport"><AppIcon name="print" :size="18" />打印 / 导出 PDF</button>
              <button type="button" class="primary-button" :disabled="pushing" @click="pushReport"><AppIcon name="send" :size="18" />{{ pushing ? '提交中' : report?.pushed ? '再次提交推送' : '提交推送' }}</button>
            </div>
          </header>
          <ReportDocument :content="report?.content_md || ''" @outline="outline = $event" />
        </article>
      </div>

      <section v-else class="panel report-empty close-report-empty" aria-labelledby="close-report-empty-title">
        <span class="report-empty__mark" aria-hidden="true"><AppIcon name="report" :size="28" /></span>
        <p class="eyebrow">正式定稿状态</p>
        <h2 id="close-report-empty-title">今日复盘尚未定稿</h2>
        <p>{{ reportError || '计划在北京时间 15:30 后形成唯一正式复盘；缺失时不使用示例内容补位。' }}</p>
        <div v-if="generating" class="report-generation" aria-live="polite">
          <strong>{{ generationStage || '今日复盘生成中…' }}</strong>
          <span>停止查看会取消当前数据流；未完成内容不会保存为正式复盘。</span>
          <div v-if="generationPreview" class="generation-preview">{{ generationPreview.slice(-800) }}</div>
          <button type="button" class="danger-button" @click="stopGeneration">停止查看</button>
        </div>
        <div v-else class="report-empty__actions">
          <button type="button" class="primary-button" @click="generate"><AppIcon name="spark" :size="18" />生成正式复盘</button>
          <button type="button" class="secondary-button" @click="refresh"><AppIcon name="refresh" :size="18" />刷新状态</button>
          <a class="secondary-button" href="#/history?tab=reports">查看历史报告</a>
        </div>
      </section>

      <footer class="page-footer close-review-footer">
        <span>数据治理：缺失保持缺失，任一接口失败仅局部降级。</span>
        <span>版本治理：正式报告、建议与回验采用 append-only 语义。</span>
      </footer>
    </template>
  </section>
</template>
