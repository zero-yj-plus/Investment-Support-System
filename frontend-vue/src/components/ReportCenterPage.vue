<script setup lang="ts">
import { ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import ReportCenterGovernance from './ReportCenterGovernance.vue'
import ReportCenterSummary from './ReportCenterSummary.vue'
import ReportDocument from './ReportDocument.vue'
import { REPORT_TYPES, useReportCenter } from '../composables/useReportCenter'
import type { ReportType } from '../types/reports'
import type { ReportOutlineItem } from '../utils/reportMarkdown'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  selectedType, selectedDate, selectedOption, report, visibleDates, matchingReceipt,
  loading, refreshing, generating, pushing, reportError, historyError, receiptError,
  actionError, actionNotice, generationStage, generationPreview, generationIncomplete,
  generationSeconds, isToday, hasReport, refresh, selectReport, selectDate, generate,
  stopGeneration, pushReport,
} = useReportCenter()
const outline = ref<ReportOutlineItem[]>([])

watch([refreshing, generating, pushing], ([isRefreshing, isGenerating, isPushing]) => {
  emit('refreshState', isRefreshing || isGenerating || isPushing)
}, { immediate: true })

function onDateChange(event: Event) {
  const value = (event.target as HTMLInputElement).value
  if (value) void selectDate(value)
}

function onTypeChange(type: ReportType) {
  void selectReport(type)
}

function focusSection(event: MouseEvent, id: string) {
  event.preventDefault()
  const target = document.getElementById(id)
  target?.focus({ preventScroll: false })
}

function printReport() {
  window.print()
}

defineExpose({ refresh })
</script>

<template>
  <section class="report-center-page" aria-labelledby="report-center-page-title">
    <header class="report-center-intro">
      <div class="page-heading">
        <h1 id="report-center-page-title">报告中心</h1>
        <p class="page-subtitle">先核对服务端定稿结论与风险边界，再阅读正文和交付回执；前端不重算交易结论。</p>
      </div>
    </header>

    <section class="panel report-center-controls" aria-label="选择报告">
      <div class="report-type-tabs" tabindex="0" aria-label="报告类型，可横向滚动">
        <button
          v-for="option in REPORT_TYPES"
          :key="option.id"
          type="button"
          :aria-pressed="selectedType === option.id"
          :disabled="generating || pushing || refreshing"
          @click="onTypeChange(option.id)"
        >
          <span>{{ option.label }}</span><small class="mono">{{ option.scheduledAt }}</small>
        </button>
      </div>
      <label class="report-date-field" for="report-center-date">
        <span>报告日期</span>
        <input
          id="report-center-date"
          :value="selectedDate"
          type="date"
          :max="visibleDates[0]"
          :disabled="generating || pushing || refreshing"
          @change="onDateChange"
        />
      </label>
    </section>

    <div class="report-center-shell">
      <aside class="panel report-history" aria-labelledby="report-history-title">
        <header>
          <div><p class="eyebrow">历史日期</p><h2 id="report-history-title">最近 30 个日期</h2></div>
          <span class="mono">{{ visibleDates.length }}</span>
        </header>
        <p v-if="historyError" class="report-history__error" role="status">历史日期读取失败：{{ historyError }}</p>
        <nav aria-label="报告历史日期">
          <button
            v-for="date in visibleDates"
            :key="date"
            type="button"
            :aria-current="selectedDate === date ? 'date' : undefined"
            :disabled="generating || pushing || refreshing"
            @click="selectDate(date)"
          >
            <span class="mono">{{ date }}</span><small>{{ date === visibleDates[0] ? '今天' : '查看' }}</small>
          </button>
        </nav>
      </aside>

      <div class="report-center-work">
        <div v-if="loading" class="report-center-loading" role="status" aria-label="报告中心正在加载">
          <section class="panel skeleton-block report-center-loading__summary"></section>
          <section class="panel skeleton-block report-center-loading__document"></section>
        </div>

        <template v-else-if="hasReport && report">
          <div v-if="reportError" class="report-center-alert" role="alert">
            <strong>最新刷新失败，保留已加载的正式报告</strong><span>{{ reportError }}</span>
          </div>

          <ReportCenterSummary :report="report" :receipt="matchingReceipt" :receipt-error="receiptError" />

          <div class="report-center-layout">
            <details class="panel report-center-outline" open>
              <summary><span>报告目录</span><span class="mono">{{ outline.length }} 节</span></summary>
              <nav aria-label="报告正文章节">
                <a
                  v-for="item in outline"
                  :key="item.id"
                  :href="`#/history?tab=reports&section=${encodeURIComponent(item.id)}`"
                  :class="{ 'is-subsection': item.level > 1 }"
                  @click="focusSection($event, item.id)"
                >{{ item.label }}</a>
              </nav>
              <p>目录只负责定位正文，不改变当前报告或历史事实。</p>
            </details>

            <article class="panel report-center-document" aria-labelledby="report-center-document-title">
              <header>
                <div>
                  <p class="eyebrow">正式定稿</p>
                  <h2 id="report-center-document-title">{{ report.title || selectedOption.label }}</h2>
                  <div class="report-center-document__meta">
                    <span class="mono">{{ report.created_at || '生成时点未提供' }}</span>
                    <span class="mono">{{ report.content_hash ? report.content_hash.slice(0, 12) : '指纹未提供' }}</span>
                  </div>
                </div>
                <div class="report-center-document__actions">
                  <button type="button" class="secondary-button" @click="printReport">
                    <AppIcon name="print" :size="18" />打印 / 导出 PDF
                  </button>
                  <button
                    v-if="selectedOption.pushable"
                    type="button"
                    class="primary-button"
                    :disabled="pushing || generating"
                    @click="pushReport"
                  >
                    <AppIcon name="send" :size="18" />{{ pushing ? '提交中' : report.pushed ? '再次推送' : '提交推送' }}
                  </button>
                  <button v-else type="button" class="secondary-button" disabled title="尾盘草案仅供定稿复核，不能单独推送">
                    草案不可推送
                  </button>
                </div>
              </header>
              <div class="report-action-feedback" aria-live="polite">
                <p v-if="actionNotice" class="is-notice">{{ actionNotice }}</p>
                <p v-if="actionError" class="is-error" role="alert">{{ actionError }}</p>
              </div>
              <ReportDocument :content="report.content_md || ''" @outline="outline = $event" />
            </article>
          </div>

          <ReportCenterGovernance :report="report" :receipt="matchingReceipt" />
        </template>

        <section v-else class="panel report-center-empty" aria-labelledby="report-center-empty-title">
          <span class="report-center-empty__mark" aria-hidden="true"><AppIcon name="report" :size="28" /></span>
          <p class="eyebrow">报告状态</p>
          <h2 id="report-center-empty-title">{{ selectedDate }} 暂无「{{ selectedOption.label }}」</h2>
          <p>{{ reportError || '接口成功返回真实空状态；没有使用示例正文或数字补位。' }}</p>

          <div v-if="generating" class="report-generation-state" aria-live="polite">
            <strong>{{ generationStage || '报告生成中…' }}</strong>
            <span class="mono">已等待 {{ generationSeconds }} 秒</span>
            <p>当前内容只是流式预览，收到保存完成回执前不视为正式报告。</p>
            <pre v-if="generationPreview">{{ generationPreview.slice(-1200) }}</pre>
            <button type="button" class="danger-button" @click="stopGeneration">
              <AppIcon name="stop" :size="18" />停止旁观
            </button>
          </div>

          <div v-else class="report-center-empty__actions">
            <button v-if="isToday" type="button" class="primary-button" :disabled="refreshing" @click="generate">
              <AppIcon name="spark" :size="18" />生成今日报告
            </button>
            <button v-else type="button" class="primary-button" :disabled="refreshing" @click="selectDate(visibleDates[0])">
              返回今天
            </button>
            <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh({ clear: true })">
              <AppIcon name="refresh" :size="18" />{{ refreshing ? '刷新中' : '刷新状态' }}
            </button>
          </div>

          <div v-if="generationIncomplete && generationPreview" class="report-generation-incomplete" role="alert">
            <strong>不完整输出 · 已隔离</strong>
            <p>{{ actionError || '生成流未完成，以下内容不作为定稿、风险结论或推送依据。' }}</p>
            <pre>{{ generationPreview.slice(-1200) }}</pre>
          </div>
          <p v-if="actionNotice" class="report-center-empty__notice" role="status">{{ actionNotice }}</p>
          <p v-if="actionError && !generationIncomplete" class="report-center-empty__error" role="alert">{{ actionError }}</p>
        </section>
      </div>
    </div>
    <div class="report-center-intro__stamp" aria-label="当前报告日期与计划时点">
      <span class="mono">{{ selectedDate }}</span>
      <strong>{{ selectedOption.label }} · {{ selectedOption.scheduledAt }}</strong>
    </div>
  </section>
</template>

<style scoped>
.report-center-page { display: grid; min-width: 0; gap: 12px; }
.report-center-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; padding: 4px 2px; }
.report-center-intro h1 { margin: 0; font-size: clamp(24px, 2.5vw, 32px); }
.report-center-intro p:last-child { max-width: 760px; margin: 6px 0 0; color: var(--text-2); }
.report-center-intro__stamp { display: grid; min-width: 210px; justify-items: end; gap: 2px; }
.report-center-intro__stamp span { color: var(--text-3); font-size: 13px; }
.report-center-intro__stamp strong { font-size: 16px; }
.report-center-controls { display: flex; min-width: 0; align-items: stretch; justify-content: space-between; gap: 12px; padding: 8px; }
.report-type-tabs { display: flex; min-width: 0; flex: 1 1 auto; gap: 4px; overflow-x: auto; }
.report-type-tabs button { display: flex; min-width: 116px; min-height: 44px; align-items: center; justify-content: center; gap: 6px; padding: 7px 10px; border: 1px solid transparent; border-radius: var(--radius-sm); background: transparent; color: var(--text-2); cursor: pointer; }
.report-type-tabs button:hover { background: var(--surface-2); color: var(--text); }
.report-type-tabs button[aria-pressed="true"] { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.report-type-tabs small { color: var(--text-3); font-size: 12px; }
.report-date-field { display: flex; flex: 0 0 auto; align-items: center; gap: 8px; color: var(--text-3); font-size: 12px; }
.report-date-field input { min-width: 150px; min-height: 44px; padding: 7px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); font: inherit; color-scheme: dark; }
:global([data-theme="light"]) .report-date-field input { color-scheme: light; }
.report-center-shell { display: grid; grid-template-columns: 216px minmax(0, 1fr); gap: 12px; align-items: start; min-width: 0; }
.report-history { position: sticky; top: 76px; min-width: 0; overflow: hidden; }
.report-history > header { display: flex; min-height: 60px; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--border); }
.report-history h2 { margin: 0; font-size: 16px; }
.report-history > header > .mono { color: var(--text-3); font-size: 12px; }
.report-history__error { margin: 0; padding: 8px 10px; border-bottom: 1px solid var(--warning); color: var(--warning); font-size: 12px; }
.report-history nav { display: grid; padding: 5px; }
.report-history button { display: flex; min-height: 44px; align-items: center; justify-content: space-between; gap: 8px; padding: 7px 8px; border: 1px solid transparent; border-radius: var(--radius-sm); background: transparent; color: var(--text-2); cursor: pointer; }
.report-history button:hover { background: var(--surface-2); }
.report-history button[aria-current="date"] { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.report-history button small { color: var(--text-3); font-size: 12px; }
.report-center-work { display: grid; min-width: 0; gap: 12px; }
.report-center-loading { display: grid; gap: 12px; }
.report-center-loading .skeleton-block { width: 100%; }
.report-center-loading__summary { height: 280px; }
.report-center-loading__document { height: 680px; }
.report-center-alert { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 10px 12px; border: 1px solid var(--warning); border-radius: var(--radius-sm); color: var(--warning); }
.report-center-alert span { color: var(--text-2); }
.report-center-layout { display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 12px; align-items: start; min-width: 0; }
.report-center-outline { position: sticky; top: 76px; grid-column: 2; grid-row: 1; min-width: 0; overflow: hidden; }
.report-center-outline summary { display: flex; min-height: 48px; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--border); cursor: pointer; font-weight: 700; }
.report-center-outline nav { display: grid; padding: 5px; }
.report-center-outline a { display: flex; min-height: 44px; align-items: center; padding: 7px 8px; border-radius: var(--radius-sm); color: var(--text-2); text-decoration: none; }
.report-center-outline a:hover { background: var(--surface-2); color: var(--accent); }
.report-center-outline a.is-subsection { padding-left: 18px; color: var(--text-3); }
.report-center-outline > p { margin: 0; padding: 9px 12px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 12px; line-height: 1.55; }
.report-center-document { grid-column: 1; grid-row: 1; min-width: 0; overflow: hidden; }
.report-center-document > header { display: flex; min-height: 78px; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.report-center-document h2 { margin: 0; font-size: 20px; }
.report-center-document__meta { display: flex; flex-wrap: wrap; gap: 6px 12px; margin-top: 4px; color: var(--text-3); font-size: 12px; }
.report-center-document__actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.report-center-document__actions button { min-height: 44px; }
.report-action-feedback:empty { display: none; }
.report-action-feedback p { margin: 0; padding: 8px 14px; border-bottom: 1px solid var(--border); }
.report-action-feedback .is-notice { color: var(--info); }
.report-action-feedback .is-error { color: var(--danger); }
.report-center-empty { display: flex; min-height: 480px; flex-direction: column; align-items: center; justify-content: center; padding: 32px 20px; text-align: center; }
.report-center-empty__mark { display: grid; width: 52px; height: 52px; place-items: center; margin-bottom: 10px; border: 1px solid var(--border); border-radius: 50%; color: var(--text-3); }
.report-center-empty h2 { margin: 0; font-size: 22px; }
.report-center-empty > p { max-width: 680px; color: var(--text-2); }
.report-center-empty__actions { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 12px; }
.report-center-empty__actions > * { min-height: 44px; }
.report-generation-state, .report-generation-incomplete { display: grid; width: min(100%, 760px); gap: 8px; margin-top: 14px; padding: 14px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); text-align: left; }
.report-generation-state > span { color: var(--text-3); }
.report-generation-state p, .report-generation-incomplete p { margin: 0; color: var(--text-2); }
.report-generation-state pre, .report-generation-incomplete pre { max-height: 240px; margin: 0; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--text-2); font: 13px/1.6 "SFMono-Regular", Consolas, monospace; }
.report-generation-state button { justify-self: start; min-height: 44px; }
.report-generation-incomplete { border-color: var(--warning); }
.report-generation-incomplete strong { color: var(--warning); }
.report-center-empty__notice { color: var(--info) !important; }
.report-center-empty__error { color: var(--danger) !important; }
@media (max-width: 1100px) {
  .report-center-layout { grid-template-columns: minmax(0, 1fr); }
  .report-center-outline, .report-center-document { position: static; grid-column: 1; grid-row: auto; }
  .report-center-outline nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 900px) {
  .report-center-controls { align-items: stretch; flex-direction: column; }
  .report-date-field { justify-content: space-between; }
  .report-center-shell { grid-template-columns: minmax(0, 1fr); }
  .report-history { position: static; }
  .report-history nav { display: flex; max-width: 100%; overflow-x: auto; }
  .report-history button { min-width: 150px; }
}
@media (max-width: 720px) {
  .report-center-intro { align-items: flex-start; flex-direction: column; gap: 8px; }
  .report-center-intro__stamp { min-width: 0; justify-items: start; }
  .report-center-document > header { align-items: flex-start; flex-direction: column; }
  .report-center-document__actions { width: 100%; justify-content: flex-start; }
  .report-center-document__actions button { flex: 1 1 170px; }
  .report-center-outline nav { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 520px) {
  .report-center-page { gap: 8px; }
  .report-center-controls { padding: 6px; }
  .report-type-tabs button { min-width: 124px; }
  .report-date-field { align-items: stretch; flex-direction: column; }
  .report-date-field input { width: 100%; min-width: 0; }
  .report-center-empty { min-height: 520px; padding-inline: 12px; }
  .report-center-empty__actions, .report-center-empty__actions > * { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .report-center-page *, .report-center-page *::before, .report-center-page *::after { scroll-behavior: auto !important; transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; }
}
@media print {
  .report-center-intro, .report-center-controls, .report-history, .report-center-summary, .report-center-outline, .report-governance, .report-center-document__actions, .topbar, .sidebar { display: none !important; }
  .report-center-shell, .report-center-layout { display: block; }
  .report-center-document { border: 0; }
}
</style>
