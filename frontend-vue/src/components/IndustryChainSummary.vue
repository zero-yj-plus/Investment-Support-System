<script setup lang="ts">
import type { IndustryChainRunState, IndustryChainSummary } from '../types/industryChain'
import DataStatus from './DataStatus.vue'

defineProps<{
  state: IndustryChainRunState
  industry: string
  summary: IndustryChainSummary
}>()

function quality(state: IndustryChainRunState, trueEmpty: boolean) {
  if (state === 'loading') return 'partial'
  if (state === 'complete' && !trueEmpty) return 'degraded'
  return 'missing'
}
</script>

<template>
  <section class="chain-summary panel" aria-labelledby="chain-summary-title">
    <header>
      <div>
        <p class="eyebrow">DETERMINISTIC BOUNDARY</p>
        <h2 id="chain-summary-title">当前结论与行动边界</h2>
      </div>
      <DataStatus
        :state="quality(state, summary.trueEmpty)"
        :label="state === 'loading' ? '证据核验中' : state === 'complete' ? (summary.trueEmpty ? '真实空结果' : '结构证据可用 · 存在缺口') : state === 'partial' ? '不完整输出 · 已隔离' : '尚无完整研究'"
      />
    </header>

    <div v-if="state === 'idle' || state === 'error'" class="chain-summary__empty">
      <strong>尚未形成产业链结论</strong>
      <span>输入主题后，服务端将先冻结可核验的 TDX 板块与成员证据。</span>
    </div>
    <div v-else class="chain-summary__grid">
      <article class="is-conclusion">
        <span>产业结论</span>
        <strong>{{ state === 'loading' ? '正在建立证据底座，暂不形成结论' : summary.conclusion }}</strong>
        <small>产业结构用于中期跟踪，不被单日涨跌改写。</small>
      </article>
      <article class="is-action">
        <span>现在应该做什么</span>
        <strong>{{ state === 'loading' ? '等待服务端完成证据核验' : summary.trueEmpty ? '先校正产业名称或补齐板块匹配证据' : '按证据核验队列补公告、财报与兑现材料' }}</strong>
        <small>研究优先级不是投资排名，也不是买入顺序。</small>
      </article>
      <article class="is-boundary">
        <span>风险与仓位边界</span>
        <strong>{{ state === 'loading' ? '尚未评估' : summary.tradeTiming }}</strong>
        <small>本接口不评估目标价、仓位或即时买卖动作，须另走实时行情与风险闸。</small>
      </article>
    </div>

    <dl class="chain-summary__evidence">
      <div><dt>研究主题</dt><dd>{{ industry || '—' }}</dd></div>
      <div><dt>证据时点</dt><dd class="mono">{{ state === 'complete' || state === 'partial' ? summary.dataAsOf : '—' }}</dd></div>
      <div><dt>来源覆盖</dt><dd>{{ state === 'complete' || state === 'partial' ? summary.sourceCoverage : '—' }}</dd></div>
      <div><dt>主要缺口</dt><dd>{{ state === 'complete' || state === 'partial' ? summary.gapSummary : '—' }}</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.chain-summary { overflow: hidden; }.chain-summary > header { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 14px; border-bottom: 1px solid var(--border); }.chain-summary h2 { margin: 0; font-size: 19px; }.chain-summary__empty { display: grid; min-height: 132px; place-content: center; gap: 4px; padding: 20px; color: var(--text-3); text-align: center; }.chain-summary__empty strong { color: var(--text-2); font-size: 17px; }
.chain-summary__grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }.chain-summary__grid article { display: grid; align-content: start; gap: 6px; min-width: 0; padding: 14px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }.chain-summary__grid article:last-child { border-right: 0; }.chain-summary__grid span, .chain-summary__evidence dt { color: var(--text-3); font-size: 12px; font-weight: 700; }.chain-summary__grid strong { color: var(--text); font-size: 17px; line-height: 1.55; }.chain-summary__grid small { color: var(--text-3); font-size: 12px; line-height: 1.5; }.chain-summary__grid .is-conclusion { border-top: 3px solid var(--accent); }.chain-summary__grid .is-action { border-top: 3px solid var(--info); }.chain-summary__grid .is-boundary { border-top: 3px solid var(--warning); }
.chain-summary__evidence { display: grid; grid-template-columns: minmax(110px, .6fr) repeat(3, minmax(0, 1fr)); margin: 0; }.chain-summary__evidence div { min-width: 0; padding: 10px 12px; border-right: 1px solid var(--border); }.chain-summary__evidence div:last-child { border-right: 0; }.chain-summary__evidence dd { margin: 3px 0 0; color: var(--text-2); font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
@media (max-width: 920px) { .chain-summary__grid { grid-template-columns: minmax(0, 1fr); }.chain-summary__grid article { border-right: 0; }.chain-summary__evidence { grid-template-columns: repeat(2, minmax(0, 1fr)); }.chain-summary__evidence div:nth-child(2) { border-right: 0; }.chain-summary__evidence div:nth-child(-n+2) { border-bottom: 1px solid var(--border); } }
@media (max-width: 560px) { .chain-summary > header { align-items: flex-start; flex-direction: column; }.chain-summary__evidence { grid-template-columns: minmax(0, 1fr); }.chain-summary__evidence div { border-right: 0; border-bottom: 1px solid var(--border); }.chain-summary__evidence div:last-child { border-bottom: 0; } }
</style>
