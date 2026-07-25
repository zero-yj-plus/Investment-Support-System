<script setup lang="ts">
import { computed } from 'vue'
import type { SentimentEvent, SentimentEventsResponse } from '../types/sentimentEvents'
import { finiteNumber } from '../utils/sentimentFormat'

const props = defineProps<{
  data: SentimentEventsResponse | null
  events: SentimentEvent[]
  unavailable: boolean
  degraded: boolean
}>()

const mappedCount = computed(() => props.events.filter((event) => event.a_share?.state === 'mapped').length)
const pendingCount = computed(() => props.events.filter((event) => ['waiting', 'observing'].includes(event.a_share?.observation?.status || '')).length)
const hasEventCountBasis = computed(() => Boolean(props.data) && !(props.unavailable && !props.events.length))
const sourcesLive = computed(() => finiteNumber(props.data?.sources_live))
const sourcesTried = computed(() => finiteNumber(props.data?.sources_tried))
const conclusion = computed(() => {
  if (props.unavailable && !props.events.length) return '采集链当前不可用，不能据此判断最新舆情。'
  if (!props.events.length) return '接口已返回，但当前没有可展示事件。'
  if (props.degraded) return '已有事件可读，但来源、映射或观察链只完成部分覆盖。'
  return '事件、A 股映射与后续观察链已返回，可逐条复核。'
})
</script>

<template>
  <section class="panel sentiment-summary" aria-labelledby="sentiment-summary-title">
    <div class="sentiment-summary__lead">
      <p class="eyebrow">当前结论</p>
      <h2 id="sentiment-summary-title">{{ conclusion }}</h2>
      <p><strong>现在应该做：</strong>先核对事件原文与映射类型，再看盘面是否已经留痕；不要依据单条消息追涨。</p>
    </div>

    <dl class="sentiment-summary__metrics" aria-label="事件覆盖摘要">
      <div><dt>当前事件</dt><dd class="mono">{{ hasEventCountBasis ? events.length : '—' }}</dd></div>
      <div><dt>A 股相关</dt><dd class="mono">{{ hasEventCountBasis ? mappedCount : '—' }}</dd></div>
      <div><dt>待盘面核对</dt><dd class="mono">{{ hasEventCountBasis ? pendingCount : '—' }}</dd></div>
      <div>
        <dt>有效采集端</dt>
        <dd class="mono">{{ sourcesLive == null ? '—' : sourcesLive }} / {{ sourcesTried == null ? '—' : sourcesTried }}</dd>
      </div>
    </dl>

    <div class="sentiment-summary__guardrails" aria-label="风险与行动边界">
      <div><span>风险边界</span><strong>新闻映射不等于利多、利空或因果</strong></div>
      <div><span>仓位边界</span><strong>本页不生成仓位结论，沿用服务端风险决策</strong></div>
      <div><span>禁止条件</span><strong>来源、事件时点或盘面证据不完整时禁止据此下结论</strong></div>
    </div>
  </section>
</template>

<style scoped>
.sentiment-summary { overflow: hidden; }
.sentiment-summary__lead { padding: 16px 18px 14px; border-bottom: 1px solid var(--border); }
.sentiment-summary__lead h2 { max-width: 920px; margin: 0; font-size: clamp(19px, 1.7vw, 25px); line-height: 1.45; }
.sentiment-summary__lead > p:last-child { margin: 7px 0 0; color: var(--text-2); }
.sentiment-summary__lead strong { color: var(--text); }
.sentiment-summary__metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; border-bottom: 1px solid var(--border); }
.sentiment-summary__metrics div { min-width: 0; padding: 12px 14px; border-right: 1px solid var(--border); }
.sentiment-summary__metrics div:last-child { border-right: 0; }
.sentiment-summary__metrics dt { color: var(--text-3); font-size: 12px; }
.sentiment-summary__metrics dd { margin: 3px 0 0; font-size: clamp(20px, 2vw, 27px); font-weight: 760; }
.sentiment-summary__guardrails { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.sentiment-summary__guardrails div { min-width: 0; padding: 12px 14px; border-right: 1px solid var(--border); }
.sentiment-summary__guardrails div:last-child { border-right: 0; }
.sentiment-summary__guardrails span { display: block; color: var(--text-3); font-size: 12px; }
.sentiment-summary__guardrails strong { display: block; margin-top: 3px; font-size: 14px; line-height: 1.55; }
@media (max-width: 900px) {
  .sentiment-summary__metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .sentiment-summary__metrics div:nth-child(2) { border-right: 0; }
  .sentiment-summary__metrics div:nth-child(-n + 2) { border-bottom: 1px solid var(--border); }
  .sentiment-summary__guardrails { grid-template-columns: minmax(0, 1fr); }
  .sentiment-summary__guardrails div { border-right: 0; border-bottom: 1px solid var(--border); }
  .sentiment-summary__guardrails div:last-child { border-bottom: 0; }
}
@media (max-width: 520px) {
  .sentiment-summary__lead { padding: 14px 12px; }
  .sentiment-summary__metrics div, .sentiment-summary__guardrails div { padding-inline: 12px; }
}
</style>
