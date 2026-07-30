<script setup lang="ts">
import type { IndustryChainRunState, IndustryChainSummary } from '../types/industryChain'
import DataStatus from './DataStatus.vue'

defineProps<{
  state: IndustryChainRunState
  industry: string
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
      <article class="is-action">
        <span>阅读主线</span>
        <strong>{{ state === 'loading' ? '正在形成传导链' : '需求驱动 → 上游卡点 → 中游制造 → 下游兑现' }}</strong>
        <small>先理解价值如何传导，再进入公司研究。</small>
      </article>
      <article class="is-boundary">
        <span>报告定位</span>
        <strong>{{ state === 'loading' ? '产业思维整理中' : '定性产业研究，不是数据审计或交易指令' }}</strong>
        <small>价格、仓位和即时买卖动作在个股诊断中另行判断。</small>
      </article>
    </div>

    <dl class="chain-summary__context">
      <div><dt>研究主题</dt><dd>{{ industry || '—' }}</dd></div>
      <div><dt>核心框架</dt><dd>需求、产业地图、利润池、卡点、替代路径</dd></div>
      <div><dt>公司映射</dt><dd>只作为后续研究入口，不作为排名</dd></div>
      <div><dt>交易判断</dt><dd>与产业研究分开</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.chain-summary { overflow: hidden; }.chain-summary > header { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 14px; border-bottom: 1px solid var(--border); }.chain-summary h2 { margin: 0; font-size: 19px; }.chain-summary__empty { display: grid; min-height: 132px; place-content: center; gap: 4px; padding: 20px; color: var(--text-3); text-align: center; }.chain-summary__empty strong { color: var(--text-2); font-size: 17px; }
.chain-summary__grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }.chain-summary__grid article { display: grid; align-content: start; gap: 6px; min-width: 0; padding: 14px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }.chain-summary__grid article:last-child { border-right: 0; }.chain-summary__grid span, .chain-summary__context dt { color: var(--text-3); font-size: 12px; font-weight: 700; }.chain-summary__grid strong { color: var(--text); font-size: 17px; line-height: 1.55; }.chain-summary__grid small { color: var(--text-3); font-size: 12px; line-height: 1.5; }.chain-summary__grid .is-conclusion { border-top: 3px solid var(--accent); }.chain-summary__grid .is-action { border-top: 3px solid var(--info); }.chain-summary__grid .is-boundary { border-top: 3px solid var(--warning); }
.chain-summary__context { display: grid; grid-template-columns: minmax(110px, .6fr) repeat(3, minmax(0, 1fr)); margin: 0; }.chain-summary__context div { min-width: 0; padding: 10px 12px; border-right: 1px solid var(--border); }.chain-summary__context div:last-child { border-right: 0; }.chain-summary__context dd { margin: 3px 0 0; color: var(--text-2); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
@media (max-width: 920px) { .chain-summary__grid { grid-template-columns: minmax(0, 1fr); }.chain-summary__grid article { border-right: 0; }.chain-summary__context { grid-template-columns: repeat(2, minmax(0, 1fr)); }.chain-summary__context div:nth-child(2) { border-right: 0; }.chain-summary__context div:nth-child(-n+2) { border-bottom: 1px solid var(--border); } }
@media (max-width: 560px) { .chain-summary > header { align-items: flex-start; flex-direction: column; }.chain-summary__context { grid-template-columns: minmax(0, 1fr); }.chain-summary__context div { border-right: 0; border-bottom: 1px solid var(--border); }.chain-summary__context div:last-child { border-bottom: 0; } }
</style>
