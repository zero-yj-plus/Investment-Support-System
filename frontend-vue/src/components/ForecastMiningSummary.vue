<script setup lang="ts">
import { computed } from 'vue'
import type { ForecastMiningResponse } from '../types/forecastMining'
import { formatCompactDate, formatCount } from '../utils/forecastFormat'

const props = defineProps<{
  data: ForecastMiningResponse | null
  itemCount: number
  trapCount: number
  loading: boolean
  unavailable: boolean
}>()

const metrics = computed(() => [
  { label: '报告期', value: formatCompactDate(props.data?.period), detail: '服务端自动选择的中报或年报期' },
  { label: '窗口截止', value: props.data?.deadline || '—', detail: '披露时点分层的规则边界' },
  { label: '预告总数', value: formatCount(props.data?.stats?.['总数']), detail: '公开源去重后的预告总量' },
  { label: '今日新增', value: formatCount(props.data?.stats?.['今日新增']), detail: '公告日等于服务端北京时间今日' },
  { label: '掘金池', value: formatCount(props.data?.stats?.['掘金池']), detail: '达到服务端正向规则且未落入陷阱池' },
  { label: '陷阱沉底', value: formatCount(props.data?.stats?.['陷阱池']), detail: '低基数或异常高同比，透明保留' },
])

const conclusion = computed(() => {
  if (props.loading) return '正在读取服务端确定性榜单，暂不生成结论。'
  if (props.unavailable) return '当前榜单不可用，停止使用本页产生新的判断。'
  if (!props.itemCount && !props.trapCount) return '当前报告期在既定规则下没有正向候选或陷阱记录。'
  return `服务端规则给出 ${props.itemCount} 只掘金候选，并将 ${props.trapCount} 只基数风险标的沉入陷阱池。`
})
</script>

<template>
  <section class="forecast-summary" aria-labelledby="forecast-conclusion-title">
    <article class="panel forecast-conclusion">
      <div class="forecast-conclusion__copy">
        <p class="eyebrow">当前确定性结论</p>
        <h2 id="forecast-conclusion-title">{{ conclusion }}</h2>
        <p>下一步只做证据复核：先核披露时点、基数与扣非疑点，再进入个股诊断补齐行情和风险证据。候选不等于推荐。</p>
      </div>
      <dl class="forecast-boundaries" aria-label="风险、仓位和禁止条件">
        <div><dt>风险边界</dt><dd>预告不是正式报告；扣非待核与基数陷阱必须单独验证。</dd></div>
        <div><dt>仓位边界</dt><dd>本接口未提供仓位上限或价格区间，本页不得推断。</dd></div>
        <div><dt>禁止条件</dt><dd>数据不可用、字段缺失或只有异常同比时，不据此建立交易动作。</dd></div>
      </dl>
    </article>

    <section class="forecast-metrics" aria-label="榜单统计">
      <article v-for="metric in metrics" :key="metric.label" class="forecast-metric">
        <span>{{ metric.label }}</span>
        <strong class="mono">{{ metric.value }}</strong>
        <small>{{ metric.detail }}</small>
      </article>
    </section>
  </section>
</template>

<style scoped>
.forecast-summary { display: grid; min-width: 0; gap: 12px; }
.forecast-conclusion { display: grid; min-width: 0; grid-template-columns: minmax(0, 1.15fr) minmax(360px, .85fr); overflow: hidden; }
.forecast-conclusion__copy { padding: 16px; border-right: 1px solid var(--border); }
.forecast-conclusion h2 { max-width: 860px; margin: 2px 0 7px; font-size: clamp(20px, 2vw, 26px); line-height: 1.35; }
.forecast-conclusion__copy > p:last-child { max-width: 800px; margin: 0; color: var(--text-2); }
.forecast-boundaries { display: grid; margin: 0; background: var(--surface-2); }
.forecast-boundaries div { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 10px; align-items: baseline; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.forecast-boundaries div:last-child { border-bottom: 0; }
.forecast-boundaries dt { color: var(--warning); font-size: 12px; font-weight: 760; }
.forecast-boundaries dd { margin: 0; color: var(--text-2); font-size: 13px; line-height: 1.55; }
.forecast-metrics { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
.forecast-metric { min-width: 0; min-height: 104px; padding: 10px 11px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.forecast-metric > span { color: var(--text-3); font-size: 12px; }
.forecast-metric strong { display: block; margin-top: 3px; color: var(--text); font-size: 20px; overflow-wrap: anywhere; }
.forecast-metric small { display: block; margin-top: 4px; color: var(--text-3); font-size: 12px; line-height: 1.35; }
@media (max-width: 1100px) {
  .forecast-conclusion { grid-template-columns: 1fr; }
  .forecast-conclusion__copy { border-right: 0; border-bottom: 1px solid var(--border); }
  .forecast-metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
  .forecast-conclusion__copy { padding: 14px; }
  .forecast-boundaries div { grid-template-columns: 1fr; gap: 2px; }
  .forecast-boundaries dd { font-size: 15px; }
  .forecast-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .forecast-metric { min-height: 112px; }
}
</style>
