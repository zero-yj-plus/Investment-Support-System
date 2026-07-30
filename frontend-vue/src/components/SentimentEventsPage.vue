<script setup lang="ts">
import { computed, watch } from 'vue'
import SentimentEventFeed from './SentimentEventFeed.vue'
import { useSentimentEvents } from '../composables/useSentimentEvents'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const { data, events, loading, refreshing, error, refreshNotice, refresh } = useSentimentEvents()

watch(refreshing, (value) => emit('refreshState', value), { immediate: true })

const pipelineStates = computed(() => Object.values(data.value?.pipeline || {}).filter(Boolean))
const notes = computed(() => {
  const derived = []
  if (data.value?.pipeline?.source_state === 'unavailable') derived.push('新闻采集链暂不可用')
  if (data.value?.pipeline?.entity_mapping_state === 'unavailable') derived.push('A 股映射底座暂不可用')
  if (data.value?.observation?.enabled === false) derived.push('盘面观察闭环未启用')
  if (data.value?.pipeline?.market_observation_state === 'unavailable') derived.push('盘面观察链暂不可用')
  return [...new Set([
    ...(data.value?.missing || []),
    ...(data.value?.mapping?.missing || []),
    ...(data.value?.observation?.missing || []),
    ...derived,
  ].filter(Boolean))]
})
const unavailable = computed(() => data.value?.pipeline?.source_state === 'unavailable')
const degraded = computed(() => notes.value.length > 0 || pipelineStates.value.some((state) => ['degraded', 'unavailable'].includes(String(state))))
const stale = computed(() => notes.value.some((note) => /沿用|last.?good|快照|陈旧/i.test(note)) || Boolean(refreshNotice.value))
const trueEmpty = computed(() => !loading.value && !error.value && !unavailable.value && events.value.length === 0)
const statusLabel = computed(() => {
  if (error.value && !events.value.length) return '暂不可用'
  if (unavailable.value && !events.value.length) return '采集链不可用'
  if (stale.value) return '沿用数据'
  if (degraded.value) return '部分覆盖'
  if (trueEmpty.value) return '真实空结果'
  if (events.value.length) return '数据可用'
  return '状态确认中'
})

defineExpose({ refresh })
</script>

<template>
  <section class="sentiment-page" aria-labelledby="sentiment-page-title" :aria-busy="loading || refreshing">
    <header class="sentiment-page__intro">
      <div class="page-heading">
        <h1 id="sentiment-page-title">舆情事件</h1>
        <p class="page-subtitle">展示真实采集事件、确定性 A 股实体映射与追加式盘面留痕；不判断利多利空，不生成交易结论。</p>
      </div>
    </header>

    <div v-if="loading" class="sentiment-loading" role="status" aria-live="polite">
      <span class="sr-only">舆情事件正在加载</span>
      <section class="panel sentiment-skeleton sentiment-skeleton--feed"></section>
    </div>

    <template v-else>
      <div v-if="error || refreshNotice" class="sentiment-alert" role="alert">
        <div>
          <strong>{{ events.length ? '最新刷新失败，保留已加载事件' : '舆情事件暂不可用' }}</strong>
          <span>{{ refreshNotice || error }}</span>
        </div>
        <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh()">重试</button>
      </div>

      <section v-if="trueEmpty" class="panel sentiment-empty" aria-labelledby="sentiment-empty-title">
        <p class="eyebrow">真实空结果</p>
        <h2 id="sentiment-empty-title">当前暂无可展示的舆情事件</h2>
        <p>接口成功返回了真实空数组。页面不会用历史事件、示例内容或数字 0 补位。</p>
        <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh()">重新读取</button>
      </section>

      <section v-else-if="unavailable && !events.length" class="panel sentiment-unavailable" aria-labelledby="sentiment-unavailable-title">
        <p class="eyebrow">局部接口不可用</p>
        <h2 id="sentiment-unavailable-title">新闻采集链暂未返回可核验事件</h2>
        <p>这不是“没有事件”。请根据覆盖说明检查数据源；A 股映射与盘面观察不会被补成空结果或示例值。</p>
        <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh()">重试采集链</button>
      </section>

      <SentimentEventFeed v-if="events.length" :events="events" />

      <footer class="page-footer sentiment-page__footer">
        <span>事件时间与抓取时间分别展示；多采集端命中不等于独立信源确认。</span>
        <span>后续观察采用追加式留痕；“已留痕”不代表新闻造成涨跌。</span>
      </footer>
    </template>

    <dl class="sentiment-page__stamp" :class="{ 'is-stale': stale, 'is-degraded': degraded }" aria-label="舆情事件数据日期、抓取时点、状态与覆盖说明">
      <div><dt>最新事件</dt><dd class="mono">{{ data?.latest_event_time || '—' }}<span v-if="!data?.latest_event_time" class="sr-only">数据缺失</span></dd></div>
      <div><dt>最近抓取</dt><dd class="mono">{{ data?.fetched_at || data?.cn_fetched_at || '—' }}<span v-if="!data?.fetched_at && !data?.cn_fetched_at" class="sr-only">数据缺失</span></dd></div>
      <div><dt>数据状态</dt><dd>{{ statusLabel }}</dd></div>
      <div class="sentiment-page__coverage"><dt>覆盖说明</dt><dd>{{ notes.length ? notes.join('；') : (data ? '接口未声明覆盖缺口' : '—') }}</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.sentiment-page { display: grid; min-width: 0; gap: 12px; }
.sentiment-page__intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.sentiment-page__intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.sentiment-page__intro > div > p:last-child { max-width: 760px; margin: 7px 0 0; color: var(--text-2); }
.sentiment-page__stamp { display: grid; min-width: min(620px, 48vw); grid-template-columns: minmax(180px, 1fr) minmax(210px, 1.2fr) minmax(94px, .65fr); gap: 6px 14px; margin: 0; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.sentiment-page__stamp.is-degraded { border-color: var(--warning); }
.sentiment-page__stamp.is-stale { border-color: var(--danger); }
.sentiment-page__stamp div { min-width: 0; }
.sentiment-page__stamp dt { color: var(--text-3); font-size: 12px; }
.sentiment-page__stamp dd { margin: 1px 0 0; font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }
.sentiment-page__coverage { display: grid; grid-column: 1 / -1; grid-template-columns: auto minmax(0, 1fr); gap: 8px; padding-top: 5px; border-top: 1px solid var(--border); }
.sentiment-page__coverage dd { color: var(--text-2); font-weight: 500; }
.sentiment-loading { display: grid; gap: 12px; }
.sentiment-skeleton { position: relative; overflow: hidden; }
.sentiment-skeleton::after { position: absolute; inset: 0; background: color-mix(in srgb, var(--surface-3) 68%, transparent); content: ""; animation: sentiment-pulse 1.2s ease-in-out infinite alternate; }
.sentiment-skeleton--feed { min-height: 480px; }
.sentiment-alert { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 12px 14px; border: 1px solid var(--danger); border-radius: var(--radius); background: color-mix(in srgb, var(--danger) 7%, var(--surface)); }
.sentiment-alert > div { display: grid; gap: 2px; }
.sentiment-alert span { color: var(--text-2); }
.sentiment-empty, .sentiment-unavailable { display: flex; min-height: 320px; flex-direction: column; align-items: center; justify-content: center; padding: 24px; text-align: center; }
.sentiment-empty h2, .sentiment-unavailable h2 { margin: 0; font-size: 22px; }
.sentiment-empty > p:not(.eyebrow), .sentiment-unavailable > p:not(.eyebrow) { max-width: 650px; margin: 8px 0 18px; color: var(--text-3); }
.sentiment-page__footer { padding-bottom: 6px; }
@keyframes sentiment-pulse { from { opacity: .42; } to { opacity: .78; } }
@media (max-width: 1100px) {
  .sentiment-page__intro { align-items: stretch; flex-direction: column; gap: 12px; }
  .sentiment-page__stamp { min-width: 0; width: 100%; }
}
@media (max-width: 900px) {
  .sentiment-page__stamp { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 720px) {
  .sentiment-page { gap: 8px; }
  .sentiment-page__intro { gap: 10px; }
  .sentiment-page__stamp { grid-template-columns: minmax(0, 1fr); }
  .sentiment-page__coverage { grid-column: 1; grid-template-columns: minmax(0, 1fr); gap: 1px; }
  .sentiment-alert { align-items: stretch; flex-direction: column; }
  .sentiment-alert .secondary-button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .sentiment-skeleton::after { animation: none; }
}
</style>
