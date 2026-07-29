<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import HotboardStockTable from './HotboardStockTable.vue'
import HotboardThemeFilter from './HotboardThemeFilter.vue'
import { useHotboard } from '../composables/useHotboard'

const emit = defineEmits<{ refreshState: [refreshing: boolean] }>()
const { data, stocks, loading, refreshing, error, refresh } = useHotboard()
const selectedTheme = ref('')

watch(refreshing, (value) => emit('refreshState', value), { immediate: true })
watch(stocks, () => {
  if (selectedTheme.value && !stocks.value.some((stock) => String(stock.reason_type || '').split('+').map((item) => item.trim()).includes(selectedTheme.value))) {
    selectedTheme.value = ''
  }
})

const isEmpty = computed(() => !loading.value && !error.value && stocks.value.length === 0)
const isDegraded = computed(() => Boolean(data.value?.stale || data.value?.missing?.length || data.value?.data_state === 'degraded'))
const statusLabel = computed(() => {
  if (error.value && !stocks.value.length) return '暂不可用'
  if (data.value?.stale) return '陈旧数据'
  if (isDegraded.value) return '部分覆盖'
  if (isEmpty.value) return '真实空结果'
  if (stocks.value.length) return '数据可用'
  return '状态确认中'
})
const noteList = computed(() => [...new Set([...(data.value?.missing || []), ...(data.value?.notes || [])].filter(Boolean))])

defineExpose({ refresh })
</script>

<template>
  <section class="hotboard-page" aria-labelledby="hotboard-page-title" :aria-busy="loading || refreshing">
    <header class="hotboard-intro">
      <div class="page-heading">
        <h1 id="hotboard-page-title">涨停题材</h1>
        <p class="page-subtitle">先看涨停集中方向，再复核几天几板、板型、封板率与封单证据。页面只呈现事实，不生成买入评分。</p>
      </div>
    </header>

    <div v-if="loading" class="hotboard-loading" role="status" aria-live="polite">
      <span class="sr-only">涨停题材正在加载</span>
      <section class="panel hotboard-skeleton hotboard-skeleton--themes"></section>
      <section class="panel hotboard-skeleton hotboard-skeleton--table"></section>
    </div>

    <template v-else>
      <div v-if="error" class="hotboard-alert" role="alert">
        <div><strong>{{ stocks.length ? '最新刷新失败，保留已加载数据' : '涨停题材暂不可用' }}</strong><span>{{ error }}</span></div>
        <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh">重试</button>
      </div>

      <section v-if="isEmpty" class="panel hotboard-empty" aria-labelledby="hotboard-empty-title">
        <p class="eyebrow">真实空结果</p>
        <h2 id="hotboard-empty-title">当前无涨停题材数据</h2>
        <p>接口已成功返回空数组。可能处于非交易日、尚未开盘或当日确无涨停；页面不会用历史或示例数据补位。</p>
        <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh">重新读取</button>
      </section>

      <template v-if="stocks.length">
        <HotboardThemeFilter v-model="selectedTheme" :stocks="stocks" />
        <HotboardStockTable :stocks="stocks" :theme="selectedTheme" />
      </template>

      <footer class="page-footer hotboard-footer">
        <span>数据来源：同花顺公开涨停池；沪深、创业板、科创板，不含北交所。</span>
        <span>缺失保持缺失；本页不与主涨停池混算，不在浏览器生成交易建议。</span>
      </footer>
    </template>

    <dl class="hotboard-stamp" :class="{ 'is-stale': data?.stale, 'is-degraded': isDegraded }" aria-label="涨停题材数据日期、取数时点、状态与覆盖说明">
      <div><dt>数据日期</dt><dd class="mono">{{ data?.data_date || '—' }}<span v-if="!data?.data_date" class="sr-only">数据缺失</span></dd></div>
      <div><dt>取数时点</dt><dd class="mono">{{ data?.ts || '—' }}<span v-if="!data?.ts" class="sr-only">数据缺失</span></dd></div>
      <div><dt>数据状态</dt><dd>{{ statusLabel }}</dd></div>
      <div class="hotboard-stamp__coverage">
        <dt>数据覆盖说明</dt>
        <dd v-if="noteList.length"><span v-for="note in noteList" :key="note">{{ note }}</span></dd>
        <dd v-else>{{ data ? '接口未声明覆盖缺口' : '—' }}</dd>
      </div>
    </dl>
  </section>
</template>

<style scoped>
.hotboard-page { display: grid; min-width: 0; gap: 12px; }
.hotboard-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding: 4px 2px 2px; }
.hotboard-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); line-height: 1.25; }
.hotboard-intro > div > p:last-child { max-width: 760px; margin: 7px 0 0; color: var(--text-2); }
.hotboard-stamp { display: grid; min-width: 480px; grid-template-columns: minmax(92px, .72fr) minmax(164px, 1.25fr) minmax(88px, .7fr); gap: 6px 14px; margin: 0; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.hotboard-stamp.is-degraded { border-color: var(--warning); }
.hotboard-stamp.is-stale { border-color: var(--danger); }
.hotboard-stamp div { min-width: 0; }
.hotboard-stamp dt { color: var(--text-3); font-size: 12px; }
.hotboard-stamp dd { margin: 1px 0 0; font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }
.hotboard-stamp__coverage { display: grid; grid-column: 1 / -1; grid-template-columns: auto minmax(0, 1fr); align-items: baseline; gap: 8px; padding-top: 5px; border-top: 1px solid var(--border); }
.hotboard-stamp__coverage dd { display: flex; min-width: 0; flex-wrap: wrap; gap: 2px 10px; color: var(--text-2); font-weight: 500; }
.hotboard-stamp__coverage dd span:not(:last-child)::after { color: var(--text-3); content: "；"; }
.hotboard-loading { display: grid; gap: 12px; }
.hotboard-skeleton { position: relative; overflow: hidden; }
.hotboard-skeleton::after { position: absolute; inset: 0; background: color-mix(in srgb, var(--surface-3) 68%, transparent); content: ""; animation: hotboard-pulse 1.2s ease-in-out infinite alternate; }
.hotboard-skeleton--themes { min-height: 176px; }
.hotboard-skeleton--table { min-height: 420px; }
.hotboard-alert { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 12px 14px; border: 1px solid var(--danger); border-radius: var(--radius); background: color-mix(in srgb, var(--danger) 7%, var(--surface)); }
.hotboard-alert > div { display: grid; gap: 2px; }
.hotboard-alert span { color: var(--text-2); }
.hotboard-empty { display: flex; min-height: 360px; flex-direction: column; align-items: center; justify-content: center; padding: 24px; text-align: center; }
.hotboard-empty h2 { margin: 0; font-size: 22px; }
.hotboard-empty > p:not(.eyebrow) { max-width: 620px; margin: 8px 0 18px; color: var(--text-3); }
.hotboard-footer { padding-top: 3px; }
@keyframes hotboard-pulse { from { opacity: .42; } to { opacity: .78; } }
@media (max-width: 1024px) {
  .hotboard-intro { align-items: stretch; flex-direction: column; gap: 12px; }
  .hotboard-stamp { min-width: 0; }
}
@media (max-width: 720px) {
  .hotboard-page { gap: 8px; }
  .hotboard-intro { gap: 10px; }
  .hotboard-stamp { min-width: 0; grid-template-columns: 1fr; }
  .hotboard-stamp__coverage { grid-column: auto; grid-template-columns: 1fr; gap: 1px; }
  .hotboard-alert { align-items: stretch; flex-direction: column; }
  .hotboard-alert .secondary-button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .hotboard-skeleton::after { animation: none; }
}
</style>
