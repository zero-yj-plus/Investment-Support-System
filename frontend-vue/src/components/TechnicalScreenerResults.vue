<script setup lang="ts">
import type { TechnicalScreenResponse, TechnicalStock } from '../types/technicalScreener'
import {
  formatTechnicalInteger,
  formatTechnicalMetrics,
  formatTechnicalMoney,
  formatTechnicalPercent,
  formatTechnicalPrice,
  technicalDirectionClass,
} from '../utils/technicalScreenerFormat'

defineProps<{
  response: TechnicalScreenResponse | null
  stocks: TechnicalStock[]
  statusStocks: number | null | undefined
  statusError: string
  loading: boolean
  hasSubmitted: boolean
  error: string
  stale: boolean
}>()

const emit = defineEmits<{
  retry: []
  stop: []
  refreshCache: []
}>()

function diagnosisHash(code: string): string {
  return `#/research?tab=diagnosis&code=${encodeURIComponent(code)}`
}
</script>

<template>
  <section class="panel technical-results" aria-labelledby="technical-results-title" :aria-busy="loading">
    <header class="technical-results__header">
      <div>
        <p class="eyebrow">结构化证据</p>
        <h2 id="technical-results-title">筛选结果</h2>
      </div>
      <span v-if="response" class="mono">{{ formatTechnicalInteger(response.matched) }} 只命中 · {{ formatTechnicalInteger(response.returned) }} 只展示</span>
      <span v-else>尚未筛选</span>
    </header>

    <div v-if="error" class="technical-result-alert" role="alert">
      <strong>{{ response?.stocks?.length ? '新一轮筛选局部失败' : '筛选暂不可用' }}</strong>
      <span>{{ error }}</span>
      <button class="secondary-button" type="button" :disabled="loading" @click="emit('retry')">重试筛选</button>
    </div>
    <p v-if="stale && response" class="technical-result-stale" role="status">规则已经修改；以下仍是上一次成功结果，尚未按新规则重算。</p>

    <div v-if="loading" class="technical-result-state technical-screener-query-loading" aria-live="polite">
      <span class="technical-loading-bar" aria-hidden="true"></span>
      <strong>正在读取本地技术信号缓存</strong>
      <p>自定义参数只会重算本地前复权 K 线，不会触发逐股网络请求。</p>
      <button class="secondary-button" type="button" @click="emit('stop')">停止本次筛选</button>
    </div>

    <div v-else-if="statusError && statusStocks == null" class="technical-result-state is-unavailable">
      <strong>缓存状态暂不可用</strong>
      <p>{{ statusError }}。尚不能判断缓存是否存在，请使用顶部刷新重读状态。</p>
    </div>

    <div v-else-if="statusStocks == null" class="technical-result-state is-unavailable">
      <strong>缓存股票数缺失</strong>
      <p>接口没有返回可核验的缓存股票数量，因此不会执行筛选。</p>
    </div>

    <div v-else-if="statusStocks === 0" class="technical-result-state is-missing">
      <strong>尚未生成全市场技术信号缓存</strong>
      <p>普通筛选不会触发逐股历史行情请求。请显式提交后台刷新。</p>
      <button class="primary-button" type="button" @click="emit('refreshCache')">刷新技术信号数据</button>
    </div>

    <div v-else-if="!hasSubmitted || !response" class="technical-result-state">
      <strong>等待筛选</strong>
      <p>使用页面上方“开始筛选”后，才会调用真实筛选接口；页面不预载示例候选。</p>
    </div>

    <div v-else-if="response.ok === false" class="technical-result-state is-missing">
      <strong>{{ response.state === 'missing' ? '缓存缺失' : '当前规则无法执行' }}</strong>
      <p>{{ response.error || response.errors?.map((item) => item.message).filter(Boolean).join('；') || response.missing?.join('；') || '服务端未返回可用结果。' }}</p>
    </div>

    <div v-else-if="stocks.length === 0" class="technical-result-state is-empty">
      <strong>当前规则无命中</strong>
      <p>筛选结果为真实空数组，不会使用示例股票填充。</p>
      <p v-if="response.strongest_condition">限制最强：{{ response.strongest_condition }}。可核对后再决定是否放宽。</p>
    </div>

    <template v-else>
      <div class="technical-result-summary">
        <div><span>数据日</span><strong class="mono">{{ response.data_date || '—' }}</strong></div>
        <div><span>缓存覆盖</span><strong class="mono">{{ formatTechnicalInteger(response.coverage?.cached) }} 只</strong></div>
        <div><span>入池范围</span><strong class="mono">{{ formatTechnicalInteger(response.coverage?.eligible) }} 只</strong></div>
        <div><span>复权口径</span><strong>{{ response.adjustment === 'qfq' ? '前复权' : (response.adjustment || '—') }}</strong></div>
      </div>

      <div class="technical-table-wrap" tabindex="0" aria-label="技术面选股结果表，可在容器内横向滚动">
        <table class="technical-table">
          <thead><tr><th>代码 / 名称</th><th>最新价</th><th>涨跌幅</th><th>成交额</th><th>行业</th><th>信号分</th><th>主要命中原因</th></tr></thead>
          <tbody>
            <tr v-for="row in stocks" :key="row.code">
              <td><a class="technical-stock-link" :href="diagnosisHash(row.code)">{{ row.code }}<span>{{ row.name || '名称缺失' }}</span></a></td>
              <td class="mono">{{ formatTechnicalPrice(row.last_price) }}</td>
              <td class="mono" :class="technicalDirectionClass(row.change_pct)">{{ formatTechnicalPercent(row.change_pct) }}</td>
              <td class="mono">{{ formatTechnicalMoney(row.last_amount) }}</td>
              <td>{{ row.industry || '行业缺失' }}</td>
              <td class="mono technical-score">{{ formatTechnicalInteger(row.signal_score) }}</td>
              <td>
                <details>
                  <summary>{{ row.matched_reasons?.[0]?.signal_name || '查看命中原因' }}</summary>
                  <ul>
                    <li v-for="reason in (row.matched_reasons || [])" :key="`${reason.signal_code}-${reason.timeframe}-${reason.date || ''}`">
                      <strong>{{ reason.timeframe }} · {{ reason.signal_name }}{{ reason.is_confirmed === false ? ' · 未确认' : '' }}</strong>
                      <span>{{ reason.date || '信号日期缺失' }} · {{ formatTechnicalMetrics(reason.metrics) }}</span>
                    </li>
                  </ul>
                </details>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="technical-result-cards">
        <article v-for="row in stocks" :key="row.code">
          <header><a class="technical-stock-link" :href="diagnosisHash(row.code)">{{ row.code }} · {{ row.name || '名称缺失' }}</a><strong class="mono" :class="technicalDirectionClass(row.change_pct)">{{ formatTechnicalPercent(row.change_pct) }}</strong></header>
          <dl>
            <div><dt>最新价</dt><dd class="mono">{{ formatTechnicalPrice(row.last_price) }}</dd></div>
            <div><dt>成交额</dt><dd class="mono">{{ formatTechnicalMoney(row.last_amount) }}</dd></div>
            <div><dt>信号分</dt><dd class="mono">{{ formatTechnicalInteger(row.signal_score) }}</dd></div>
            <div><dt>行业</dt><dd>{{ row.industry || '行业缺失' }}</dd></div>
          </dl>
          <ul><li v-for="reason in (row.matched_reasons || [])" :key="`${reason.signal_code}-${reason.timeframe}`">{{ reason.timeframe }} · {{ reason.signal_name }}{{ reason.is_confirmed === false ? ' · 未确认' : '' }}</li></ul>
        </article>
      </div>
    </template>
  </section>
</template>

<style scoped>
.technical-results { min-width: 0; overflow: hidden; }
.technical-results__header { min-height: 62px; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 14px; border-bottom: 1px solid var(--border); }
.technical-results__header h2 { margin: 0; font-size: 18px; }
.technical-results__header > span { color: var(--text-3); font-size: 13px; }
.technical-result-alert, .technical-result-stale { display: flex; align-items: center; gap: 10px; margin: 10px; padding: 9px 10px; border-left: 3px solid var(--danger); background: color-mix(in srgb, var(--danger) 8%, var(--surface)); }
.technical-result-alert span { min-width: 0; flex: 1; color: var(--text-2); }
.technical-result-stale { border-left-color: var(--warning); background: color-mix(in srgb, var(--warning) 8%, var(--surface)); color: var(--text-2); }
.technical-result-state { min-height: 280px; display: grid; place-content: center; justify-items: center; gap: 8px; padding: 24px; text-align: center; }
.technical-result-state p { max-width: 580px; margin: 0; color: var(--text-3); line-height: 1.55; }
.technical-loading-bar { width: min(280px, 70vw); height: 5px; border-radius: 999px; background: linear-gradient(90deg, var(--border) 0 30%, var(--info) 45% 55%, var(--border) 70% 100%); background-size: 200% 100%; animation: technical-loading 1.4s linear infinite; }
@keyframes technical-loading { to { background-position: -200% 0; } }
.technical-result-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid var(--border); }
.technical-result-summary div { min-width: 0; padding: 10px 12px; border-right: 1px solid var(--border); }
.technical-result-summary div:last-child { border-right: 0; }
.technical-result-summary span, .technical-result-summary strong { display: block; }
.technical-result-summary span { color: var(--text-3); font-size: 12px; }
.technical-result-summary strong { margin-top: 3px; font-size: 16px; }
.technical-table-wrap { max-width: 100%; overflow-x: auto; }
.technical-table { width: 100%; min-width: 940px; border-collapse: collapse; }
.technical-table th, .technical-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
.technical-table th { color: var(--text-3); font-size: 12px; }
.technical-table td { font-size: 14px; }
.technical-stock-link { min-height: 44px; display: inline-flex; flex-direction: column; justify-content: center; color: var(--info); font-weight: 760; text-decoration: none; }
.technical-stock-link span { color: var(--text-3); font-size: 12px; font-weight: 500; }
.technical-score { font-size: 18px !important; font-weight: 760; }
.technical-table details summary { min-height: 44px; display: flex; align-items: center; cursor: pointer; }
.technical-table details ul { min-width: 320px; margin: 6px 0 0; padding-left: 18px; }
.technical-table details li { margin: 7px 0; }
.technical-table details li span { display: block; margin-top: 2px; color: var(--text-3); font-size: 12px; }
.technical-result-cards { display: none; }
.is-up { color: var(--up) !important; }
.is-down { color: var(--down) !important; }
@media (max-width: 720px) {
  .technical-results__header, .technical-result-alert { align-items: stretch; flex-direction: column; }
  .technical-result-summary { grid-template-columns: 1fr 1fr; }
  .technical-result-summary div:nth-child(2) { border-right: 0; }
  .technical-table-wrap { display: none; }
  .technical-result-cards { display: grid; gap: 8px; padding: 8px; }
  .technical-result-cards article { padding: 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); }
  .technical-result-cards header { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .technical-result-cards dl { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin: 8px 0; }
  .technical-result-cards dl div { min-width: 0; }
  .technical-result-cards dt { color: var(--text-3); font-size: 12px; }
  .technical-result-cards dd { margin: 2px 0 0; overflow-wrap: anywhere; }
  .technical-result-cards ul { margin: 8px 0 0; padding-left: 18px; color: var(--text-2); }
}
@media (prefers-reduced-motion: reduce) { .technical-loading-bar { animation: none; } }
</style>
