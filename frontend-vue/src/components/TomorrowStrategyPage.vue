<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import ReportDocument from './ReportDocument.vue'
import TomorrowDataHealth from './TomorrowDataHealth.vue'
import TomorrowDecisionSummary from './TomorrowDecisionSummary.vue'
import TomorrowWatchlist from './TomorrowWatchlist.vue'
import { useTomorrowStrategy } from '../composables/useTomorrowStrategy'
import type { ReportOutlineItem } from '../utils/reportMarkdown'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  report, watchlist, loading, refreshing, generating, pushing,
  reportError, watchlistError, actionError, actionNotice,
  generationStage, generationPreview, hasReport,
  refresh, loadWatchlist, generate, stopGeneration, pushReport,
} = useTomorrowStrategy()
const outline = ref<ReportOutlineItem[]>([])
watch(refreshing, (value) => emit('refreshState', value), { immediate: true })
onBeforeUnmount(() => emit('refreshState', false))

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
  return '治理回执未完整记录'
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
  <section class="tomorrow-page" aria-labelledby="tomorrow-page-title">
    <header class="premarket-page__intro tomorrow-page__intro">
      <div>
        <p class="eyebrow">交易日 19:30 次日作战计划</p>
        <h1 id="tomorrow-page-title">次日策略</h1>
        <p>先看服务端风险底座与仓位边界，再核对观察池触发条件，最后阅读正式计划。今日尾盘与收盘定稿只作上游事实，不被晚间信息回写。</p>
      </div>
      <div class="premarket-page__stamp" aria-label="策略数据日期与生成时点">
        <span>{{ reportTime.date }}</span>
        <strong class="mono">{{ reportTime.time }}</strong>
      </div>
    </header>

    <div v-if="loading" class="tomorrow-loading" role="status" aria-live="polite">
      <span class="sr-only">次日策略正在加载</span>
      <section class="panel skeleton-block tomorrow-loading__summary"></section>
      <section class="tomorrow-evidence-grid"><span class="panel skeleton-block tomorrow-loading__evidence"></span><span class="panel skeleton-block tomorrow-loading__evidence"></span></section>
      <section class="panel skeleton-block tomorrow-loading__report"></section>
    </div>

    <template v-else>
      <div v-if="reportError && hasReport" class="premarket-inline-alert" role="alert">
        <strong>最新策略刷新失败，当前保留已加载定稿</strong><span>{{ reportError }}</span>
      </div>
      <div v-if="actionError" class="premarket-inline-alert" role="alert"><strong>操作未完成</strong><span>{{ actionError }}</span></div>
      <p v-if="actionNotice" class="report-action-notice tomorrow-page__notice" role="status">{{ actionNotice }}</p>

      <TomorrowDecisionSummary :snapshot="report?.cockpit" />

      <section class="tomorrow-evidence" aria-labelledby="tomorrow-evidence-title">
        <div class="tail-section-heading">
          <div><p class="eyebrow">执行前证据</p><h2 id="tomorrow-evidence-title">计划必须能追溯，也必须等到价格条件</h2></div>
          <p>上游定稿、观察池与治理回执独立展示；失败只降级对应区域。</p>
        </div>
        <div class="tomorrow-evidence-grid">
          <TomorrowWatchlist
            :items="watchlist?.items || []"
            :error="watchlistError"
            :refreshing="refreshing"
            @retry="loadWatchlist"
          />
          <TomorrowDataHealth
            :report="report"
            :watchlist="watchlist"
            :report-error="reportError"
            :watchlist-error="watchlistError"
          />
        </div>
      </section>

      <div v-if="hasReport" class="report-layout tomorrow-report-layout">
        <details class="panel report-outline" open>
          <summary><span>策略目录</span><span class="mono">{{ outline.length }} 节</span></summary>
          <nav aria-label="次日策略报告章节">
            <button v-for="item in outline" :key="item.id" type="button" :class="{ 'is-subsection': item.level > 1 }" @click="scrollToSection(item.id)"><span aria-hidden="true"></span>{{ item.label }}</button>
          </nav>
          <div class="report-outline__foot">
            <strong>{{ governanceLabel }}</strong>
            <span>正文中的标的均是候选提案，不计胜率、不构成下单；明日动作必须重新通过价格条件和服务端风险闸。</span>
          </div>
        </details>

        <article class="panel report-document" aria-labelledby="tomorrow-report-document-title">
          <header class="report-document__header">
            <div>
              <p class="eyebrow">唯一正式次日计划</p>
              <h2 id="tomorrow-report-document-title">{{ report?.title || '次日策略定稿' }}</h2>
              <div class="report-document__meta">
                <span class="mono">{{ reportVersion }}</span>
                <span v-if="report?.pushed" class="report-state-badge">已提交推送 · 未确认送达</span>
                <span v-else class="report-state-badge is-neutral">尚未提交推送</span>
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

      <section v-else class="panel report-empty tomorrow-report-empty" aria-labelledby="tomorrow-report-empty-title">
        <span class="report-empty__mark" aria-hidden="true"><AppIcon name="report" :size="28" /></span>
        <p class="eyebrow">正式定稿状态</p>
        <h2 id="tomorrow-report-empty-title">次日策略尚未定稿</h2>
        <p>{{ reportError || '计划在北京时间 19:30 后形成正式次日策略；缺失时不使用示例内容、猜测值或零值补位。' }}</p>
        <div v-if="generating" class="report-generation" aria-live="polite">
          <strong>{{ generationStage || '次日策略生成中…' }}</strong>
          <span>生成由后台托管；停止查看只取消当前数据流，未完整内容不会冒充正式定稿。</span>
          <div v-if="generationPreview" class="generation-preview">{{ generationPreview.slice(-800) }}</div>
          <button type="button" class="danger-button" @click="stopGeneration">停止查看</button>
        </div>
        <div v-else class="report-empty__actions">
          <button type="button" class="primary-button" @click="generate"><AppIcon name="spark" :size="18" />生成正式策略</button>
          <button type="button" class="secondary-button" @click="refresh"><AppIcon name="refresh" :size="18" />刷新状态</button>
          <a class="secondary-button" href="#/history?tab=reports">查看历史报告</a>
        </div>
      </section>

      <footer class="page-footer tomorrow-footer">
        <span>数据治理：缺失、真实空数组、陈旧与传输失败分别展示。</span>
        <span>业务治理：风险、筛选、评分和价格区间继续以服务端为准。</span>
      </footer>
    </template>
  </section>
</template>

<style scoped>
.tomorrow-page { min-width: 0; }
.tomorrow-page__notice { margin: 0 0 12px; }
.tomorrow-loading { display: grid; gap: 12px; }
.tomorrow-loading .skeleton-block { width: 100%; }
.tomorrow-loading__summary { height: 300px; }
.tomorrow-loading__evidence { display: block; height: 430px; }
.tomorrow-loading__report { height: 620px; }
.tomorrow-evidence { margin-bottom: 16px; }
.tomorrow-evidence-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(320px, .75fr); gap: 12px; align-items: start; }
.tomorrow-report-layout { margin-top: 0; }
.tomorrow-report-empty { min-height: 380px; }
.tomorrow-footer { padding-bottom: 8px; }
@media (max-width: 1100px) { .tomorrow-evidence-grid { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 900px) { .tomorrow-report-layout { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 720px) {
  .tomorrow-page__intro { align-items: flex-start; }
  .tomorrow-loading__summary { height: 420px; }
  .tomorrow-loading__evidence { height: 360px; }
  .tomorrow-report-empty { min-height: 430px; padding: 24px 14px; }
}
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; transition: none !important; animation: none !important; } }
@media print {
  .tomorrow-evidence, .report-outline, .report-document__actions, .tomorrow-footer { display: none !important; }
  .tomorrow-report-layout { display: block; }
}
</style>
