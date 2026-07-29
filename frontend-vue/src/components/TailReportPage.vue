<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import ReportDocument from './ReportDocument.vue'
import TailDecisionSnapshot from './TailDecisionSnapshot.vue'
import TailTrackRecord from './TailTrackRecord.vue'
import TailWatchlist from './TailWatchlist.vue'
import { useTailDecision } from '../composables/useTailDecision'
import type { ReportOutlineItem } from '../utils/reportMarkdown'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const {
  report, stats, recommendations, watchlist,
  loading, refreshing, generating, pushing, verifying,
  reportError, statsError, recommendationError, watchlistError,
  generationStage, generationPreview, actionNotice, hasReport,
  refresh, generate, stopGeneration, pushReport, verifyRecommendations,
} = useTailDecision()

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
  <section class="tail-page" aria-labelledby="tail-page-title">
    <header class="premarket-page__intro tail-page__intro">
      <div class="page-heading">
        <h1 id="tail-page-title">尾盘决策</h1>
        <p class="page-subtitle">先判断今天能不能做，再核对观察池与历史回验，最后阅读正式结论。定格事实不随盘后波动回写。</p>
      </div>
    </header>

    <div v-if="loading" class="premarket-loading" aria-label="尾盘决策正在加载">
      <section class="panel skeleton-block tail-loading__gate"></section>
      <section class="tail-evidence-grid"><span class="panel skeleton-block tail-loading__evidence"></span><span class="panel skeleton-block tail-loading__evidence"></span></section>
      <section class="panel skeleton-block premarket-loading__report"></section>
    </div>

    <template v-else>
      <div v-if="reportError && hasReport" class="premarket-inline-alert" role="alert">
        <strong>最新报告刷新失败，当前保留已加载定稿</strong><span>{{ reportError }}</span>
      </div>

      <TailDecisionSnapshot :snapshot="report?.cockpit" />

      <section class="tail-evidence-section" aria-labelledby="tail-evidence-title">
        <div class="tail-section-heading">
          <div><p class="eyebrow">执行前证据</p><h2 id="tail-evidence-title">只在真实计划与真实回验上做判断</h2></div>
          <p>推荐历史不可改写；观察池区间由服务端确定性规则计算。</p>
        </div>
        <div class="tail-evidence-grid">
          <TailTrackRecord
            :stats="stats"
            :recommendations="recommendations?.recommendations || []"
            :stats-error="statsError"
            :recommendation-error="recommendationError"
            :verifying="verifying"
            @verify="verifyRecommendations"
          />
          <TailWatchlist :items="watchlist?.items || []" :error="watchlistError" />
        </div>
      </section>

      <div v-if="hasReport" class="report-layout tail-report-layout">
        <details class="panel report-outline" open>
          <summary><span>报告目录</span><span class="mono">{{ outline.length }} 节</span></summary>
          <nav aria-label="尾盘决策报告章节">
            <button v-for="item in outline" :key="item.id" type="button" :class="{ 'is-subsection': item.level > 1 }" @click="scrollToSection(item.id)">
              <span aria-hidden="true"></span>{{ item.label }}
            </button>
          </nav>
          <div class="report-outline__foot">
            <strong>{{ governanceLabel }}</strong>
            <span>候选与区间不等于下单；风险与执行边界以服务端规则和正式证据为准。</span>
          </div>
        </details>

        <article class="panel report-document" aria-labelledby="tail-report-document-title">
          <header class="report-document__header">
            <div>
              <p class="eyebrow">唯一正式尾盘结论</p>
              <h2 id="tail-report-document-title">{{ report?.title || '尾盘决策定稿' }}</h2>
              <div class="report-document__meta">
                <span class="mono">{{ reportVersion }}</span>
                <span v-if="report?.pushed" class="report-state-badge">已提交推送</span>
                <span v-else class="report-state-badge is-neutral">待推送</span>
              </div>
            </div>
            <div class="report-document__actions">
              <button type="button" class="secondary-button" @click="printReport"><AppIcon name="print" :size="18" />打印 / 导出 PDF</button>
              <button type="button" class="primary-button" :disabled="pushing" @click="pushReport"><AppIcon name="send" :size="18" />{{ pushing ? '推送中' : report?.pushed ? '再次推送' : '推送' }}</button>
            </div>
          </header>
          <p v-if="actionNotice" class="report-action-notice" role="status">{{ actionNotice }}</p>
          <ReportDocument :content="report?.content_md || ''" @outline="outline = $event" />
        </article>
      </div>

      <section v-else class="panel report-empty tail-report-empty" aria-labelledby="tail-report-empty-title">
        <span class="report-empty__mark" aria-hidden="true"><AppIcon name="report" :size="28" /></span>
        <p class="eyebrow">正式定稿状态</p>
        <h2 id="tail-report-empty-title">今日尾盘决策尚未定稿</h2>
        <p>{{ reportError || '计划在北京时间 14:52 后形成唯一正式结论；缺失时不使用示例内容补位。' }}</p>
        <div v-if="generating" class="report-generation" aria-live="polite">
          <strong>{{ generationStage || '尾盘定稿生成中…' }}</strong>
          <span>生成由后台托管；停止查看不会把未完成内容保存为正式报告。</span>
          <div v-if="generationPreview" class="generation-preview">{{ generationPreview.slice(-800) }}</div>
          <button type="button" class="danger-button" @click="stopGeneration">停止查看</button>
        </div>
        <div v-else class="report-empty__actions">
          <button type="button" class="primary-button" @click="generate"><AppIcon name="spark" :size="18" />生成正式定稿</button>
          <button type="button" class="secondary-button" @click="refresh"><AppIcon name="refresh" :size="18" />刷新状态</button>
          <a class="secondary-button" href="#/history?tab=reports">查看历史报告</a>
        </div>
        <p v-if="actionNotice" class="report-action-notice" role="status">{{ actionNotice }}</p>
      </section>
    </template>
    <div class="premarket-page__stamp" aria-label="报告时点">
      <span>{{ reportTime.date }}</span>
      <strong class="mono">{{ reportTime.time }}</strong>
    </div>
  </section>
</template>

