<script setup lang="ts">
import type { IndustryChainRunState, IndustryChainSummary } from '../types/industryChain'
import DataStatus from './DataStatus.vue'

defineProps<{
  state: IndustryChainRunState
  summary: IndustryChainSummary
}>()

function quality(state: IndustryChainRunState) {
  if (state === 'loading') return 'partial'
  if (state === 'complete') return 'complete'
  return 'missing'
}
</script>

<template>
  <section class="chain-summary panel" aria-labelledby="chain-summary-title">
    <header>
      <div>
        <p class="eyebrow">INDUSTRY LOGIC</p>
        <h2 id="chain-summary-title">产业逻辑速览</h2>
      </div>
      <DataStatus
        :state="quality(state)"
        :label="state === 'loading' ? '产业链梳理中' : state === 'complete' ? '上下游报告已完成' : state === 'partial' ? '不完整输出 · 已隔离' : '尚无完整研究'"
      />
    </header>

    <div v-if="state === 'idle' || state === 'error'" class="chain-summary__empty">
      <strong>尚未形成产业链结论</strong>
      <span>输入主题后，将沿需求、上下游、利润池、卡点和替代路径展开。</span>
    </div>
    <div v-else class="chain-summary__grid">
      <article class="is-conclusion">
        <span>产业结论</span>
        <strong>{{ state === 'loading' ? '正在梳理需求与上下游传导' : summary.conclusion }}</strong>
        <small>产业结构用于中期跟踪，不被单日涨跌改写。</small>
      </article>
    </div>
  </section>
</template>

<style scoped>
.chain-summary { overflow: hidden; }.chain-summary > header { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 14px; border-bottom: 1px solid var(--border); }.chain-summary h2 { margin: 0; font-size: 19px; }.chain-summary__empty { display: grid; min-height: 132px; place-content: center; gap: 4px; padding: 20px; color: var(--text-3); text-align: center; }.chain-summary__empty strong { color: var(--text-2); font-size: 17px; }
.chain-summary__grid { display: grid; }.chain-summary__grid article { display: grid; align-content: start; gap: 6px; min-width: 0; padding: 14px; }.chain-summary__grid span { color: var(--text-3); font-size: 12px; font-weight: 700; }.chain-summary__grid strong { color: var(--text); font-size: 17px; line-height: 1.55; }.chain-summary__grid small { color: var(--text-3); font-size: 12px; line-height: 1.5; }.chain-summary__grid .is-conclusion { border-top: 3px solid var(--accent); }
@media (max-width: 560px) { .chain-summary > header { align-items: flex-start; flex-direction: column; } }
</style>
