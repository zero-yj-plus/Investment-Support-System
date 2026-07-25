<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import ReportDocument from './ReportDocument.vue'
import TomorrowDataHealth from './TomorrowDataHealth.vue'
import TomorrowDecisionSummary from './TomorrowDecisionSummary.vue'
import TomorrowWatchlist from './TomorrowWatchlist.vue'
import { useTomorrowStrategy } from '../composables/useTomorrowStrategy'
import type { ReportOutlineItem } from '../utils/reportMarkdown'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  report, watchlist, loading, refreshing, generating, pushing, reportError, watchlistError,
  actionError, actionNotice, generationStage, generationPreview, hasReport, refresh,
  loadWatchlist, generate, stopGeneration, pushReport,
} = useTomorrowStrategy()
const outline = ref<ReportOutlineItem[]>([])
watch(refreshing, (value) => emit('refreshState', value), { immediate: true })
onBeforeUnmount(() => emit('refreshState', false))

const reportTime = computed(() => {
  const value = report.value?.created_at || ''
  const normalized = value.replace('T', ' ')
  const [date = '', time = ''] = normalized.split(' ')
  return { date: report.value?.date || date || '日期待确认', time: time.slice(0, 8) || '时点待确认' }
})
const reportVersion = computed(() => {
  const version = report.value?.plan_version ?? report.value?.report_version
  const fingerprint = String(report.value?.content_hash || '').slice(0, 12)
  return `定稿 v${version ?? '缺失'} · ${fingerprint || '指纹缺失'}`
})
const governanceLabel = computed(() => {
  if (report.value?.advice_governance?.status === 'passed' && report.value?.claim_validation?.status === 'passed') return '治理校验通过'
  if (report.value?.advice_governance?.proposal_only) return '候选提案 · 非下单指令'
  return '治理状态不完整'
})

async function scrollToSection(id: string) {
  await nextTick()
  const target = document.getElementById(id)
  if (!target) return
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
  target.focus({ preventScroll: true })
}
function printReport() { window.print() }
defineExpose({ refresh })
</script>

<template>
  <section class="tomorrow-page" aria-labelledby="tomorrow-page-title">
    <header class="premarket-page__intro">
      <div>
        <p class="eyebrow">交易日 19:30 · 次日行动边界</p>
        <h1 id="tomorrow-page-title">次日策略</h1>
        <p class="tomorrow-page__description">今日尾盘与收盘定稿只作上游事实，不被晚间信息回写。先确认风险与禁止条件，再核对观察池和正式报告。</p>
      </div>
      <div class="premarket-page__stamp" aria-label="次日策略数据日期与生成时点">
        <span>{{ reportTime.date }}</span><strong class="mono">{{ reportTime.time }}</strong>
      </div>
    </header>

    <div v-if="loading" class="tomorrow-loading" role="status" aria-live="polite">
      <span class="sr-only">次日策略正在加载</span>
      <section class="panel skeleton-block tomorrow-loading__summary"></section>
      <div class="tomorrow-loading__evidence"><section class="panel skeleton-block"></section><section class="panel skeleton-block"></section></div>
      <section class="panel skeleton-block tomorrow-loading__report"></section>
    </div>

    <template v-else>
      <div v-if="reportError && hasReport" class="tomorrow-inline-alert" role="alert">
        <strong>最新刷新失败，保留当前已加载定稿</strong><span>{{ reportError }}</span>
      </div>
      <div v-if="actionError" class="tomorrow-inline-alert is-error" role="alert"><strong>操作未完成</strong><span>{{ actionError }}</span></div>
      <div v-if="actionNotice" class="tomorrow-inline-alert is-notice" role="status"><strong>操作反馈</strong><span>{{ actionNotice }}</span></div>

      <TomorrowDecisionSummary :snapshot="report?.cockpit" />

      <section class="tomorrow-evidence-section" aria-labelledby="tomorrow-evidence-title">
        <header class="tomorrow-section-heading">
          <div><p class="eyebrow">STRUCTURED EVIDENCE</p><h2 id="tomorrow-evidence-title">触发条件与依据健康</h2></div>
          <p>两个接口独立请求；任一失败只降级对应模块。</p>
        </header>
        <div class="tomorrow-evidence-grid">
          <TomorrowWatchlist :items="watchlist?.items" :error="watchlistError" :refreshing="refreshing" @retry="loadWatchlist" />
          <TomorrowDataHealth :report="report" :watchlist="watchlist" :report-error="reportError" :watchlist-error="watchlistError" />
        </div>
      </section>

      <div v-if="hasReport" class="report-layout tomorrow-report-layout">
        <details class="panel report-outline" open>
          <summary><span>报告目录</span><span class="mono">{{ outline.length }} 节</span></summary>
          <nav aria-label="次日策略报告章节">
            <button v-for="item in outline" :key="item.id" type="button" :class="{ 'is-subsection': item.level > 1 }" @click="scrollToSection(item.id)">
              <span aria-hidden="true"></span>{{ item.label }}
            </button>
          </nav>
          <div class="report-outline__foot"><strong>{{ governanceLabel }}</strong><span>报告仅作受约束解释；风险、评级和价格区间以服务端定稿为准。</span></div>
        </details>

        <article class="panel report-document" aria-labelledby="tomorrow-report-title">
          <header class="report-document__header">
            <div>
              <p class="eyebrow">唯一正式次日计划</p><h2 id="tomorrow-report-title">{{ report?.title || '次日策略' }}</h2>
              <div class="report-document__meta">
                <span class="mono">{{ reportVersion }}</span>
                <span v-if="report?.pushed" class="report-state-badge">已提交推送 · 未确认送达</span>
                <span v-else class="report-state-badge is-neutral">尚未提交推送</span>
              </div>
            </div>
            <div class="report-document__actions">
              <button type="button" class="secondary-button" @click="printReport"><AppIcon name="print" :size="18" />打印 / 导出 PDF</button>
              <button type="button" class="primary-button" :disabled="pushing || generating" @click="pushReport"><AppIcon name="send" :size="18" />{{ pushing ? '提交中' : report?.pushed ? '再次提交' : '提交推送' }}</button>
            </div>
          </header>
          <ReportDocument :content="report?.content_md || ''" @outline="outline = $event" />
        </article>
      </div>

      <section v-else class="panel report-empty tomorrow-report-empty" aria-labelledby="tomorrow-empty-title">
        <div class="report-empty__mark"><AppIcon name="report" :size="28" /></div>
        <p class="eyebrow">NO FORMAL DRAFT</p><h2 id="tomorrow-empty-title">次日策略尚未定稿</h2>
        <p>{{ reportError || '接口返回真实空内容。页面不会用示例数字或推测结论补位。' }}</p>
        <div class="report-empty__actions">
          <button type="button" class="primary-button" :disabled="generating || refreshing" @click="generate"><AppIcon name="spark" :size="18" />{{ generating ? '生成中' : '生成次日策略' }}</button>
          <button type="button" class="secondary-button" :disabled="refreshing || generating" @click="refresh"><AppIcon name="refresh" :size="18" />{{ refreshing ? '刷新中' : '重新读取' }}</button>
          <a class="secondary-button" href="#/history?tab=reports"><AppIcon name="history" :size="18" />查看历史定稿</a>
        </div>
        <div v-if="generating" class="tomorrow-generation" aria-live="polite">
          <strong>{{ generationStage || '生成中…' }}</strong><p v-if="generationPreview">{{ generationPreview }}</p>
          <button type="button" class="secondary-button" @click="stopGeneration">停止查看生成流</button>
        </div>
      </section>

      <footer class="page-footer tomorrow-footer">
        <span>缺失值保持缺失，不补 0；真实空数组与接口失败分别呈现。</span>
        <span>历史报告采用追加式留痕；本页不连接券商真实下单。</span>
      </footer>
    </template>
  </section>
</template>

<style scoped>
.tomorrow-page { display: grid; min-width: 0; gap: 12px; }
.tomorrow-page__description { max-width: none !important; }
.tomorrow-loading { display: grid; gap: 12px; }
.tomorrow-loading .skeleton-block { width: 100%; }
.tomorrow-loading__summary { min-height: 300px; }.tomorrow-loading__evidence { display: grid; grid-template-columns: 1.25fr .75fr; gap: 12px; }.tomorrow-loading__evidence > * { min-height: 310px; }.tomorrow-loading__report { min-height: 480px; }
.tomorrow-inline-alert { display: flex; flex-wrap: wrap; gap: 4px 12px; padding: 10px 12px; border: 1px solid var(--warning); border-radius: var(--radius); color: var(--warning); }
.tomorrow-inline-alert span { color: var(--text-2); }.tomorrow-inline-alert.is-error { border-color: var(--danger); color: var(--danger); }.tomorrow-inline-alert.is-notice { border-color: var(--info); color: var(--info); }
.tomorrow-section-heading { display: flex; align-items: end; justify-content: space-between; gap: 16px; padding: 4px 2px 0; }
.tomorrow-section-heading h2 { margin: 1px 0 0; font-size: 20px; }.tomorrow-section-heading > p { max-width: 440px; margin: 0; color: var(--text-3); font-size: 13px; text-align: right; }
.tomorrow-evidence-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(320px, .75fr); align-items: start; gap: 12px; margin-top: 8px; }
.tomorrow-report-layout { margin-top: 0; }.tomorrow-report-empty { min-height: 420px; }.report-empty__actions a { text-decoration: none; }
.tomorrow-generation { width: min(720px, 100%); margin-top: 16px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); text-align: left; }.tomorrow-generation p { max-height: 160px; overflow: auto; color: var(--text-3); white-space: pre-wrap; }
.tomorrow-footer { padding-bottom: 8px; }
@media (max-width: 1100px) { .tomorrow-evidence-grid, .tomorrow-loading__evidence { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 720px) { .tomorrow-section-heading { align-items: flex-start; flex-direction: column; }.tomorrow-section-heading > p { text-align: left; } }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; transition: none !important; } }
@media print { .tomorrow-evidence-section, .tomorrow-footer, .tomorrow-inline-alert { display: none !important; } }
</style>
