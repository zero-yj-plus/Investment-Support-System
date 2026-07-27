<script setup lang="ts">
import { computed } from 'vue'
import type { StockCoreResponse, StockEnrichmentResponse } from '../types/stockAsk'
import { finite, formatMoney, formatPercent, formatPrice, formatRatio, formatTime, trendClass } from '../utils/stockAskFormat'

const props = defineProps<{
  code: string
  core: StockCoreResponse | null
  enrichment: StockEnrichmentResponse | null
  coreLoading: boolean
  enrichmentLoading: boolean
  coreError: string
  enrichmentError: string
}>()

const quote = computed(() => props.core?.quote || null)
const missing = computed(() => Array.from(new Set([
  ...(props.core?.missing || []),
  ...(props.enrichment?.missing || []),
])))
const evidenceState = computed(() => {
  if (props.coreLoading || props.enrichmentLoading) return '读取中'
  if (props.core?.recognized === false || props.core?.state === 'not_found') return '代码未通过核验'
  if (props.coreError && !props.core) return '首屏暂不可用'
  if (props.core?.state === 'partial' || props.enrichment?.state === 'partial' || props.enrichmentError) return '部分覆盖'
  if (props.core?.state === 'complete') return '行情可用'
  return '等待选择标的'
})
</script>

<template>
  <aside class="stock-evidence panel" aria-labelledby="stock-evidence-title">
    <header>
      <div><p class="eyebrow">REAL DATA</p><h2 id="stock-evidence-title">标的证据</h2></div>
      <span class="stock-evidence__state">{{ evidenceState }}</span>
    </header>

    <div v-if="!code" class="stock-evidence__empty">
      <strong>尚未选择股票</strong>
      <span>输入六位代码后，这里只展示该标的真实行情与证据状态。</span>
    </div>
    <div v-else-if="coreLoading && !core" class="stock-evidence__loading" role="status">
      <span v-for="index in 6" :key="index"></span><p>正在读取 {{ code }} 首屏行情…</p>
    </div>
    <div v-else class="stock-evidence__content">
      <div v-if="coreError" class="stock-evidence__notice is-error" role="alert">
        <strong>{{ core?.recognized === false ? '代码未通过 A 股名录核验' : '首屏行情局部不可用' }}</strong>
        <span>{{ coreError }}</span>
      </div>

      <section v-if="quote" class="stock-evidence__quote" aria-label="实时行情">
        <div class="stock-evidence__identity">
          <div><strong>{{ quote.name || '名称未提供' }}</strong><span class="mono">{{ quote.code || code }}</span></div>
          <span :class="trendClass(quote.change_pct)">{{ formatPercent(quote.change_pct) }}</span>
        </div>
        <div class="stock-evidence__price" :class="trendClass(quote.change_pct)">{{ formatPrice(quote.price) }} <small>元</small></div>
        <dl>
          <div><dt>今开</dt><dd>{{ formatPrice(quote.open) }}</dd></div>
          <div><dt>最高</dt><dd>{{ formatPrice(quote.high) }}</dd></div>
          <div><dt>最低</dt><dd>{{ formatPrice(quote.low) }}</dd></div>
          <div><dt>昨收</dt><dd>{{ formatPrice(quote.prev_close) }}</dd></div>
          <div><dt>换手率</dt><dd>{{ formatPercent(quote.turnover_rate, false) }}</dd></div>
          <div><dt>量比</dt><dd>{{ formatRatio(quote.volume_ratio) }}</dd></div>
          <div><dt>成交额</dt><dd>{{ formatMoney(quote.amount) }}</dd></div>
          <div><dt>流通市值</dt><dd>{{ formatMoney(quote.float_mv) }}</dd></div>
        </dl>
        <p class="stock-evidence__asof">
          <span>{{ quote.stale ? '陈旧快照' : '行情时点' }}：{{ formatTime(quote.as_of || core?.ts) }}</span>
          <span v-if="quote.source">来源：{{ quote.source }}</span>
          <span v-if="quote.quote_note">{{ quote.quote_note }}</span>
        </p>
      </section>

      <section class="stock-evidence__section" aria-labelledby="stock-evidence-structure">
        <h3 id="stock-evidence-structure">行业与概念</h3>
        <p v-if="enrichmentLoading && !enrichment" role="status">正在读取增强证据…</p>
        <template v-else-if="enrichment?.board?.industry || enrichment?.board?.concepts?.length">
          <div class="stock-evidence__tags">
            <span v-if="enrichment.board?.industry">行业 · {{ enrichment.board.industry }}</span>
            <span v-for="concept in enrichment.board?.concepts || []" :key="concept">{{ concept }}</span>
          </div>
          <small v-if="enrichment.board?.source">来源：{{ enrichment.board.source }}</small>
        </template>
        <p v-else>行业/概念归属暂未返回。</p>
      </section>

      <section class="stock-evidence__section" aria-labelledby="stock-evidence-flow">
        <h3 id="stock-evidence-flow">资金与财务</h3>
        <div v-if="enrichment?.flow" class="stock-evidence__flow">
          <span>主力净额</span>
          <strong v-if="finite(enrichment.flow.main_net)" class="mono" :class="trendClass(enrichment.flow.main_net)">{{ formatMoney(enrichment.flow.main_net) }}</strong>
          <strong v-else>未提供</strong>
          <small>{{ enrichment.flow_date || '数据日期未提供' }} · {{ enrichment.flow.source || '来源未标注' }}</small>
        </div>
        <p v-else>个股资金流暂未返回，不参与页面结论。</p>
        <dl v-if="enrichment?.financial" class="stock-evidence__financial">
          <div><dt>ROE</dt><dd>{{ formatPercent(enrichment.financial.roe, false) }}</dd></div>
          <div><dt>毛利率</dt><dd>{{ formatPercent(enrichment.financial.grossprofit_margin, false) }}</dd></div>
          <div><dt>净利同比</dt><dd>{{ formatPercent(enrichment.financial.netprofit_yoy) }}</dd></div>
          <div><dt>资产负债率</dt><dd>{{ formatPercent(enrichment.financial.debt_to_assets, false) }}</dd></div>
        </dl>
      </section>

      <div v-if="enrichmentError" class="stock-evidence__notice" role="alert">
        <strong>增强证据局部失败</strong><span>{{ enrichmentError }}</span>
      </div>
      <details v-if="missing.length" class="stock-evidence__missing">
        <summary>查看缺失与降级项（{{ missing.length }}）</summary>
        <ul><li v-for="item in missing" :key="item">{{ item }}</li></ul>
      </details>
    </div>
  </aside>
</template>

<style scoped>
.stock-evidence { min-width: 0; align-self: start; overflow: hidden; }
.stock-evidence > header { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.stock-evidence h2 { margin: 0; font-size: 17px; }
.stock-evidence__state { padding: 3px 7px; border: 1px solid var(--border); border-radius: 4px; color: var(--text-2); font-size: 12px; font-weight: 700; }
.stock-evidence__empty { display: grid; min-height: 220px; place-content: center; gap: 6px; padding: 20px; text-align: center; }
.stock-evidence__empty span { color: var(--text-3); }
.stock-evidence__loading { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; padding: 16px; }
.stock-evidence__loading span { height: 48px; border-radius: 6px; background: var(--surface-2); animation: stock-pulse 1.2s ease-in-out infinite alternate; }
.stock-evidence__loading p { grid-column: 1 / -1; color: var(--text-3); text-align: center; }
.stock-evidence__content { display: grid; gap: 0; }
.stock-evidence__quote, .stock-evidence__section, .stock-evidence__notice, .stock-evidence__missing { padding: 12px; border-bottom: 1px solid var(--border); }
.stock-evidence__identity { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.stock-evidence__identity > div { display: flex; min-width: 0; flex-direction: column; }
.stock-evidence__identity strong { font-size: 17px; }
.stock-evidence__identity span { font-size: 13px; }
.stock-evidence__price { margin: 8px 0; font: 760 28px/1.2 "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }
.stock-evidence__price small { color: var(--text-3); font: 500 13px/1 Inter, sans-serif; }
.stock-evidence dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; margin: 0; }
.stock-evidence dl div { min-width: 0; padding: 7px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }
.stock-evidence dt { color: var(--text-3); font-size: 12px; }
.stock-evidence dd { margin: 2px 0 0; overflow-wrap: anywhere; color: var(--text); font: 700 15px/1.35 "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }
.stock-evidence__asof { display: grid; gap: 2px; margin: 9px 0 0; color: var(--text-3); font-size: 12px; }
.stock-evidence__section h3 { margin: 0 0 8px; font-size: 15px; }
.stock-evidence__section p { margin: 0; color: var(--text-3); font-size: 13px; }
.stock-evidence__section small { display: block; margin-top: 6px; color: var(--text-3); }
.stock-evidence__tags { display: flex; flex-wrap: wrap; gap: 6px; }
.stock-evidence__tags span { padding: 3px 7px; border: 1px solid var(--border); border-radius: 4px; background: var(--surface-2); font-size: 12px; }
.stock-evidence__flow { display: grid; grid-template-columns: 1fr auto; gap: 2px 8px; margin-bottom: 8px; }
.stock-evidence__flow span { color: var(--text-3); font-size: 13px; }
.stock-evidence__flow strong { font-size: 17px; }
.stock-evidence__flow small { grid-column: 1 / -1; color: var(--text-3); }
.stock-evidence__financial { margin-top: 8px !important; }
.stock-evidence__notice { display: grid; gap: 3px; background: color-mix(in srgb, var(--warning) 7%, transparent); }
.stock-evidence__notice strong { color: var(--warning); }
.stock-evidence__notice.is-error strong { color: var(--danger); }
.stock-evidence__notice span { color: var(--text-2); font-size: 13px; }
.stock-evidence__missing summary { min-height: 44px; cursor: pointer; color: var(--text-2); font-weight: 700; }
.stock-evidence__missing ul { margin: 0; padding-left: 20px; color: var(--text-3); font-size: 13px; }
@keyframes stock-pulse { to { opacity: .48; } }
@media (prefers-reduced-motion: reduce) { .stock-evidence__loading span { animation: none; } }
</style>
