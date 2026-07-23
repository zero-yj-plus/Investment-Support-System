<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import type { MainlineResponse, MarketOverviewResponse } from '../types/dashboard'
import { useMarketEvidence } from '../composables/useMarketEvidence'
import CapitalFlowOverview from './CapitalFlowOverview.vue'
import LimitEvidence from './LimitEvidence.vue'
import MultiperiodTable from './MultiperiodTable.vue'
import SectorHeatmap from './SectorHeatmap.vue'

defineProps<{
  market: MarketOverviewResponse | null
  mainlines: MainlineResponse | null
  coreLoading: boolean
  marketError?: string
  mainlineError?: string
}>()

const { data, errors, loading, loaded, ensure } = useMarketEvidence()
const evidenceRoot = ref<HTMLElement | null>(null)
const deepRoot = ref<HTMLElement | null>(null)
let observer: IntersectionObserver | null = null
let setupTimer: ReturnType<typeof setTimeout> | null = null

onMounted(() => {
  setupTimer = setTimeout(() => {
    setupTimer = null
    if (!('IntersectionObserver' in window)) {
      void ensure(['sentiment', 'flow', 'stockflow', 'sectors', 'multiperiod'])
      return
    }
    observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return
        if (entry.target === evidenceRoot.value) {
          void ensure(['sentiment', 'flow', 'stockflow'])
          observer?.unobserve(entry.target)
        }
        if (entry.target === deepRoot.value) {
          void ensure(['sectors', 'multiperiod'])
          observer?.unobserve(entry.target)
        }
      })
    }, { rootMargin: '520px 0px', threshold: 0.01 })
    if (evidenceRoot.value) observer.observe(evidenceRoot.value)
    if (deepRoot.value) observer.observe(deepRoot.value)
  }, 800)
})

onBeforeUnmount(() => {
  if (setupTimer) clearTimeout(setupTimer)
  observer?.disconnect()
})
</script>

<template>
  <section ref="evidenceRoot" class="dashboard-section" aria-labelledby="evidence-section-title">
    <header class="dashboard-section__heading">
      <div>
        <p class="eyebrow">MARKET EVIDENCE</p>
        <h2 id="evidence-section-title">盘面证据</h2>
      </div>
      <p>报价快更；风险与指令仍按服务端定格时点保持稳定。</p>
    </header>

    <LimitEvidence
      :market="market"
      :mainlines="mainlines"
      :sentiment="data.sentiment"
      :market-loading="coreLoading"
      :mainline-loading="coreLoading"
      :sentiment-loading="loading.sentiment || !loaded.sentiment"
      :market-error="marketError"
      :mainline-error="mainlineError"
    />
    <CapitalFlowOverview
      :flow="data.flow"
      :stockflow="data.stockflow"
      :flow-loading="loading.flow || !loaded.flow"
      :stockflow-loading="loading.stockflow || !loaded.stockflow"
      :flow-error="errors.flow"
      :stockflow-error="errors.stockflow"
    />
  </section>

  <section ref="deepRoot" class="dashboard-section" aria-labelledby="deep-section-title">
    <header class="dashboard-section__heading">
      <div>
        <p class="eyebrow">DEEP STRUCTURE</p>
        <h2 id="deep-section-title">深度结构</h2>
      </div>
      <p>用于复核行业热度与多周期持续性，不直接替代主线判定。</p>
    </header>

    <SectorHeatmap :data="data.sectors" :loading="loading.sectors || !loaded.sectors" :error="errors.sectors" />
    <MultiperiodTable :data="data.multiperiod" :loading="loading.multiperiod || !loaded.multiperiod" :error="errors.multiperiod" />
  </section>
</template>
