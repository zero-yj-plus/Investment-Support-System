<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AppIcon from './components/AppIcon.vue'
import CapitalTrackingPage from './components/CapitalTrackingPage.vue'
import CloseReviewPage from './components/CloseReviewPage.vue'
import DataStatus from './components/DataStatus.vue'
import DecisionCommand from './components/DecisionCommand.vue'
import DiagnosisPage from './components/DiagnosisPage.vue'
import HotboardPage from './components/HotboardPage.vue'
import HoldingsPage from './components/HoldingsPage.vue'
import MainlineOverview from './components/MainlineOverview.vue'
import MainlineResearchPage from './components/MainlineResearchPage.vue'
import MarketEvidence from './components/MarketEvidence.vue'
import MarketOverview from './components/MarketOverview.vue'
import PremarketReportPage from './components/PremarketReportPage.vue'
import RiskOverview from './components/RiskOverview.vue'
import ScreenerPage from './components/ScreenerPage.vue'
import SentimentEventsPage from './components/SentimentEventsPage.vue'
import SidebarNavigation from './components/SidebarNavigation.vue'
import StockAskPage from './components/StockAskPage.vue'
import TailReportPage from './components/TailReportPage.vue'
import TechnicalScreenerPage from './components/TechnicalScreenerPage.vue'
import TimingPage from './components/TimingPage.vue'
import TomorrowStrategyPage from './components/TomorrowStrategyPage.vue'
import WatchlistPage from './components/WatchlistPage.vue'
import { useDecisionOverview } from './composables/useDecisionOverview'
import { findActiveNavigation } from './navigation'

type Theme = 'dark' | 'light'
const { data, errors, loading, refreshing, lastSuccessAt, quality, connectionLabel, refresh } = useDecisionOverview()
const menuOpen = ref(false)
const menuButton = ref<HTMLButtonElement | null>(null)
const sidebar = ref<InstanceType<typeof SidebarNavigation> | null>(null)
const premarketPage = ref<InstanceType<typeof PremarketReportPage> | null>(null)
const tailPage = ref<InstanceType<typeof TailReportPage> | null>(null)
const closeReviewPage = ref<InstanceType<typeof CloseReviewPage> | null>(null)
const capitalTrackingPage = ref<InstanceType<typeof CapitalTrackingPage> | null>(null)
const hotboardPage = ref<InstanceType<typeof HotboardPage> | null>(null)
const holdingsPage = ref<InstanceType<typeof HoldingsPage> | null>(null)
const timingPage = ref<InstanceType<typeof TimingPage> | null>(null)
const mainlineResearchPage = ref<InstanceType<typeof MainlineResearchPage> | null>(null)
const sentimentEventsPage = ref<InstanceType<typeof SentimentEventsPage> | null>(null)
const stockAskPage = ref<InstanceType<typeof StockAskPage> | null>(null)
const diagnosisPage = ref<InstanceType<typeof DiagnosisPage> | null>(null)
const screenerPage = ref<InstanceType<typeof ScreenerPage> | null>(null)
const technicalScreenerPage = ref<InstanceType<typeof TechnicalScreenerPage> | null>(null)
const tomorrowPage = ref<InstanceType<typeof TomorrowStrategyPage> | null>(null)
const watchlistPage = ref<InstanceType<typeof WatchlistPage> | null>(null)
const routePath = ref('today')
const routeTab = ref('decision')
const sidebarCollapsed = ref(false)
const reportRefreshing = ref(false)
const now = ref(new Date())
const theme = ref<Theme>('dark')
let clockTimer: ReturnType<typeof setInterval> | null = null

const isPremarket = computed(() => routePath.value === 'premarket' && routeTab.value === 'report')
const isTailDecision = computed(() => routePath.value === 'tail' && routeTab.value === 'decision')
const isCloseReview = computed(() => routePath.value === 'review' && routeTab.value === 'close')
const isDecisionOverview = computed(() => routePath.value === 'today' && routeTab.value === 'decision')
const isCapitalTracking = computed(() => routePath.value === 'today' && routeTab.value === 'capital')
const isHotboard = computed(() => routePath.value === 'today' && routeTab.value === 'hotboard')
const isHoldings = computed(() => routePath.value === 'risk' && routeTab.value === 'holdings')
const isTiming = computed(() => routePath.value === 'today' && routeTab.value === 'timing')
const isMainlineResearch = computed(() => routePath.value === 'today' && routeTab.value === 'mainline')
const isSentimentEvents = computed(() => routePath.value === 'today' && routeTab.value === 'news')
const isTomorrowStrategy = computed(() => routePath.value === 'review' && routeTab.value === 'tomorrow')
const isWatchlist = computed(() => routePath.value === 'risk' && routeTab.value === 'watchlist')
const isStockAsk = computed(() => routePath.value === 'research' && routeTab.value === 'ask')
const isDiagnosis = computed(() => routePath.value === 'research' && routeTab.value === 'diagnosis')
const isScreener = computed(() => routePath.value === 'research' && routeTab.value === 'screener')
const isTechnicalScreener = computed(() => routePath.value === 'research' && routeTab.value === 'technical-screener')
const activeNavigation = computed(() => findActiveNavigation(routePath.value, routeTab.value))
const moduleTitle = computed(() => activeNavigation.value?.group.label || (routePath.value === 'settings' ? '系统设置' : '交易工作台'))
const pageTitle = computed(() => activeNavigation.value?.child.label || (routePath.value === 'settings' ? '系统设置' : '页面迁移中'))
const pageRefreshing = computed(() => isHotboard.value || isHoldings.value || isWatchlist.value || isTiming.value || isMainlineResearch.value || isSentimentEvents.value || isTomorrowStrategy.value || isCapitalTracking.value || isStockAsk.value || isDiagnosis.value || isScreener.value || isTechnicalScreener.value
  ? reportRefreshing.value
  : refreshing.value || reportRefreshing.value)
const pageBusy = computed(() => isHotboard.value || isHoldings.value || isWatchlist.value || isTiming.value || isMainlineResearch.value || isSentimentEvents.value || isTomorrowStrategy.value || isCapitalTracking.value || isStockAsk.value || isDiagnosis.value || isScreener.value || isTechnicalScreener.value
  ? reportRefreshing.value
  : loading.value || pageRefreshing.value)

const beijingTime = computed(() => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
}).format(now.value))

const beijingClockTime = computed(() => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
}).format(now.value))

const successTime = computed(() => lastSuccessAt.value
  ? new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(lastSuccessAt.value)
  : '尚未成功取数')

const marketState = computed(() => {
  const context = data.shell?.market_context
  const session = context?.market_session || data.shell?.market_session || ''
  const isTradeDay = context?.is_trade_day ?? data.shell?.is_trade_day
  if (isTradeDay === false || session === 'non_trading') {
    return { label: '休市', tone: 'closed', title: '当前为非交易日，页面展示最近交易日数据' }
  }
  if (session === 'premarket') return { label: '盘前', tone: 'waiting', title: '当前为盘前阶段' }
  if (session === 'intraday') return { label: '交易中', tone: 'open', title: '当前处于 A 股连续交易时段' }
  if (session === 'tail') return { label: '尾盘', tone: 'open', title: '当前为尾盘决策阶段，市场仍在交易' }
  if (session === 'review') return { label: '已收盘', tone: 'closed', title: '当前交易所已收盘，进入复盘阶段' }
  return { label: '状态确认中', tone: 'waiting', title: '正在从服务端确认交易阶段' }
})

function applyTheme(value: Theme) {
  theme.value = value
  document.documentElement.dataset.theme = value
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute(
    'content',
    value === 'light' ? '#F3F5F8' : '#111A27',
  )
  localStorage.setItem('wudi-vue-theme', value)
}

function toggleTheme() {
  applyTheme(theme.value === 'dark' ? 'light' : 'dark')
}

function closeMenu(restoreFocus = false) {
  menuOpen.value = false
  if (restoreFocus) void nextTick(() => menuButton.value?.focus())
}

function toggleMenu() {
  if (menuOpen.value) {
    closeMenu(true)
    return
  }
  expandSidebar()
  menuOpen.value = true
  void nextTick(() => sidebar.value?.focusFirstItem())
}

function updateRoute() {
  const raw = (window.location.hash || '#/today?tab=decision').replace(/^#\/?/, '')
  const [pathPart, query = ''] = raw.split('?')
  const path = pathPart.split('/')[0] || 'today'
  const defaults: Record<string, string> = {
    today: 'decision', premarket: 'report', tail: 'decision', review: 'close',
    risk: 'holdings', research: 'ask', history: 'reports', settings: 'system',
  }
  const requestedTab = new URLSearchParams(query).get('tab') || defaults[path] || ''
  if (path === 'risk' && requestedTab === 'simulation') {
    window.history.replaceState(null, '', '#/risk?tab=holdings')
    routePath.value = 'risk'
    routeTab.value = 'holdings'
    return
  }
  routePath.value = path
  routeTab.value = requestedTab
}

function toggleSidebarCollapsed() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem('wudi-vue-sidebar-collapsed', String(sidebarCollapsed.value))
}

function expandSidebar() {
  sidebarCollapsed.value = false
  localStorage.setItem('wudi-vue-sidebar-collapsed', 'false')
}

async function refreshCurrentPage() {
  if (isTechnicalScreener.value && technicalScreenerPage.value) {
    await Promise.resolve(technicalScreenerPage.value.refresh())
    return
  }
  if (isScreener.value && screenerPage.value) {
    await Promise.resolve(screenerPage.value.refresh())
    return
  }
  if (isDiagnosis.value && diagnosisPage.value) {
    await Promise.resolve(diagnosisPage.value.refresh())
    return
  }
  if (isStockAsk.value && stockAskPage.value) {
    await Promise.resolve(stockAskPage.value.refresh())
    return
  }
  if (isWatchlist.value && watchlistPage.value) {
    await Promise.resolve(watchlistPage.value.refresh())
    return
  }
  if (isHoldings.value && holdingsPage.value) {
    await Promise.resolve(holdingsPage.value.refresh())
    return
  }
  if (isCapitalTracking.value && capitalTrackingPage.value) {
    await Promise.resolve(capitalTrackingPage.value.refresh())
    return
  }
  if (isHotboard.value && hotboardPage.value) {
    await Promise.resolve(hotboardPage.value.refresh())
    return
  }
  if (isTiming.value && timingPage.value) {
    await Promise.resolve(timingPage.value.refresh())
    return
  }
  if (isMainlineResearch.value && mainlineResearchPage.value) {
    await Promise.resolve(mainlineResearchPage.value.refresh())
    return
  }
  if (isSentimentEvents.value && sentimentEventsPage.value) {
    await Promise.resolve(sentimentEventsPage.value.refresh())
    return
  }
  if (isTomorrowStrategy.value && tomorrowPage.value) {
    await Promise.resolve(tomorrowPage.value.refresh())
    return
  }

  const tasks: Promise<unknown>[] = [refresh()]
  if (isPremarket.value && premarketPage.value) tasks.push(Promise.resolve(premarketPage.value.refresh()))
  if (isTailDecision.value && tailPage.value) tasks.push(Promise.resolve(tailPage.value.refresh()))
  if (isCloseReview.value && closeReviewPage.value) tasks.push(Promise.resolve(closeReviewPage.value.refresh()))
  await Promise.allSettled(tasks)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && menuOpen.value) closeMenu(true)
}

onMounted(() => {
  const stored = localStorage.getItem('wudi-vue-theme')
  const preferred = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  applyTheme(stored === 'light' || stored === 'dark' ? stored : preferred)
  sidebarCollapsed.value = localStorage.getItem('wudi-vue-sidebar-collapsed') === 'true'
  clockTimer = setInterval(() => { now.value = new Date() }, 1000)
  updateRoute()
  window.addEventListener('hashchange', updateRoute)
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  window.removeEventListener('hashchange', updateRoute)
  document.removeEventListener('keydown', onKeydown)
})

watch(pageTitle, (value) => { document.title = `${value} · W.U.D.I.` }, { immediate: true })
</script>

<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <div class="app-shell" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
    <button
      v-if="menuOpen"
      class="nav-scrim"
      type="button"
      aria-label="关闭导航"
      @click="closeMenu(true)"
    ></button>

    <SidebarNavigation
      ref="sidebar"
      :current-path="routePath"
      :current-tab="routeTab"
      :menu-open="menuOpen"
      :collapsed="sidebarCollapsed"
      :quality="quality"
      :connection-label="connectionLabel"
      :theme="theme"
      @close-menu="closeMenu()"
      @toggle-theme="toggleTheme"
      @toggle-collapsed="toggleSidebarCollapsed"
      @expand-sidebar="expandSidebar"
    />

    <div class="workspace">
      <header class="topbar">
        <div class="topbar__title">
          <button
            ref="menuButton"
            class="icon-button menu-button"
            type="button"
            :aria-expanded="menuOpen"
            aria-controls="primary-navigation"
            :aria-label="menuOpen ? '关闭主导航' : '打开主导航'"
            @click="toggleMenu"
          ><AppIcon name="menu" :size="21" /></button>
          <div><span>{{ moduleTitle }}</span><strong>{{ pageTitle }}</strong></div>
        </div>
        <div class="topbar__center">
          <time class="clock" :datetime="data.shell?.market_context?.now" aria-label="北京时间">
            <span>北京时间</span>
            <strong class="clock-desktop">{{ beijingTime }}</strong>
            <strong class="clock-mobile">{{ beijingClockTime }}</strong>
          </time>
          <span class="market-session" :class="`is-${marketState.tone}`" :title="marketState.title">
            <i aria-hidden="true"></i>{{ marketState.label }}
          </span>
        </div>
        <div class="topbar__actions">
          <DataStatus :state="quality" :label="connectionLabel" :title="`最近成功连接：${successTime}`" />
          <button class="icon-button theme-button" type="button" :aria-label="theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'" @click="toggleTheme">
            <AppIcon :name="theme === 'dark' ? 'sun' : 'moon'" :size="19" />
          </button>
          <button class="refresh-button" type="button" :disabled="pageRefreshing" @click="refreshCurrentPage">
            <AppIcon name="refresh" :size="17" :class="{ spinning: pageRefreshing }" />
            <span>{{ pageRefreshing ? '刷新中' : '刷新' }}</span>
          </button>
        </div>
      </header>

      <main id="main-content" tabindex="-1" :aria-busy="pageBusy">
        <PremarketReportPage
          v-if="isPremarket"
          ref="premarketPage"
          @refresh-state="reportRefreshing = $event"
        />

        <TailReportPage
          v-else-if="isTailDecision"
          ref="tailPage"
          @refresh-state="reportRefreshing = $event"
        />

        <CloseReviewPage
          v-else-if="isCloseReview"
          ref="closeReviewPage"
          @refresh-state="reportRefreshing = $event"
        />

        <TomorrowStrategyPage
          v-else-if="isTomorrowStrategy"
          ref="tomorrowPage"
          @refresh-state="reportRefreshing = $event"
        />

        <HotboardPage
          v-else-if="isHotboard"
          ref="hotboardPage"
          @refresh-state="reportRefreshing = $event"
        />

        <CapitalTrackingPage
          v-else-if="isCapitalTracking"
          ref="capitalTrackingPage"
          @refresh-state="reportRefreshing = $event"
        />

        <HoldingsPage
          v-else-if="isHoldings"
          ref="holdingsPage"
          @refresh-state="reportRefreshing = $event"
        />

        <WatchlistPage
          v-else-if="isWatchlist"
          ref="watchlistPage"
          @refresh-state="reportRefreshing = $event"
        />

        <TimingPage
          v-else-if="isTiming"
          ref="timingPage"
          :pulse="data.pulse"
          :pulse-error="errors.pulse"
          :market-session="data.shell?.market_context?.market_session || data.shell?.market_session"
          :is-trade-day="data.shell?.market_context?.is_trade_day ?? data.shell?.is_trade_day"
          @refresh-state="reportRefreshing = $event"
        />

        <MainlineResearchPage
          v-else-if="isMainlineResearch"
          ref="mainlineResearchPage"
          :market-session="data.shell?.market_context?.market_session || data.shell?.market_session"
          :is-trade-day="data.shell?.market_context?.is_trade_day ?? data.shell?.is_trade_day"
          @refresh-state="reportRefreshing = $event"
        />

        <SentimentEventsPage
          v-else-if="isSentimentEvents"
          ref="sentimentEventsPage"
          @refresh-state="reportRefreshing = $event"
        />

        <StockAskPage
          v-else-if="isStockAsk"
          ref="stockAskPage"
          @refresh-state="reportRefreshing = $event"
        />

        <DiagnosisPage
          v-else-if="isDiagnosis"
          ref="diagnosisPage"
          @refresh-state="reportRefreshing = $event"
        />

        <ScreenerPage
          v-else-if="isScreener"
          ref="screenerPage"
          @refresh-state="reportRefreshing = $event"
        />

        <TechnicalScreenerPage
          v-else-if="isTechnicalScreener"
          ref="technicalScreenerPage"
          @refresh-state="reportRefreshing = $event"
        />

        <template v-else-if="isDecisionOverview">
          <h1 id="page-title" class="sr-only">W.U.D.I. 决策总览</h1>

          <DecisionCommand :pulse="data.pulse" :loading="loading" :error="errors.pulse" :last-success-label="successTime" />

          <div class="overview-grid">
            <RiskOverview :risk="data.risk" :pulse="data.pulse" :loading="loading" :error="errors.risk" />
            <MarketOverview :market="data.market" :loading="loading" :error="errors.market" />
          </div>

          <MainlineOverview :data="data.mainlines" :loading="loading" :error="errors.mainlines" />

          <MarketEvidence
            :market="data.market"
            :mainlines="data.mainlines"
            :core-loading="loading"
            :market-error="errors.market"
            :mainline-error="errors.mainlines"
          />

          <footer class="page-footer">
            <span>数据缺失时保持空值，不以 0 或推测值补齐。</span>
            <span>风险、筛选与建议由服务端确定性规则产生。</span>
          </footer>
        </template>

        <section v-else class="panel migration-placeholder" aria-labelledby="migration-title">
          <p class="eyebrow">VUE REFACTOR</p>
          <h1 id="migration-title">{{ pageTitle }}</h1>
          <p>左侧模块入口已完成重构；此业务页面尚未迁移到 Vue 版本。</p>
          <a href="#/today?tab=decision">返回决策总览</a>
        </section>
      </main>
    </div>
  </div>
</template>
