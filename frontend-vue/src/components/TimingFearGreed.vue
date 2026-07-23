<script setup lang="ts">
import { computed } from 'vue'
import type { FearGreedReading } from '../types/timing'
import { finite, formatMoneyYuan, formatNumber, formatRatio, scoreWidth, uniqueText } from '../utils/timingFormat'

const props = defineProps<{ reading?: FearGreedReading | null }>()
const factorOrder = ['涨跌家数比', '涨停温度', '成交量比', '资金', '波动反向'] as const
const available = computed(() => props.reading?.ok !== false && finite(props.reading?.score))
const missing = computed(() => uniqueText(props.reading?.missing || []))
</script>

<template>
  <section class="panel timing-fear-panel" aria-labelledby="timing-fear-title">
    <header class="section-heading">
      <div><p class="eyebrow">MARKET TEMPERATURE</p><h2 id="timing-fear-title">贪恐情绪温度</h2></div>
      <span class="timing-fear-date mono">截至 {{ reading?.data_date || '—' }} 收盘</span>
    </header>

    <div v-if="available" class="timing-fear-body">
      <div class="timing-fear-score">
        <span>五因子等权结果</span>
        <strong class="mono">{{ formatNumber(reading?.score, 1) }}<small> / 100</small></strong>
        <b>{{ reading?.label || '标签缺失' }}</b>
        <div role="progressbar" aria-label="贪恐情绪温度" :aria-valuenow="reading?.score as number" aria-valuemin="0" aria-valuemax="100">
          <i :style="{ width: scoreWidth(reading?.score) }"></i>
        </div>
      </div>

      <dl class="timing-fear-factors">
        <div v-for="factor in factorOrder" :key="factor">
          <dt>{{ factor }}</dt>
          <dd>
            <span class="timing-factor-track" aria-hidden="true"><i :style="{ width: scoreWidth(reading?.factors?.[factor]) }"></i></span>
            <strong class="mono">{{ formatNumber(reading?.factors?.[factor], 1) }}<small v-if="finite(reading?.factors?.[factor])"> / 100</small></strong>
          </dd>
        </div>
      </dl>

      <dl class="timing-fear-meta">
        <div><dt>上涨</dt><dd class="mono is-up">{{ formatNumber(reading?.meta?.up, 0) }}<span v-if="finite(reading?.meta?.up)"> 家</span></dd></div>
        <div><dt>下跌</dt><dd class="mono is-down">{{ formatNumber(reading?.meta?.down, 0) }}<span v-if="finite(reading?.meta?.down)"> 家</span></dd></div>
        <div><dt>涨停</dt><dd class="mono is-up">{{ formatNumber(reading?.meta?.limit_up, 0) }}<span v-if="finite(reading?.meta?.limit_up)"> 家</span></dd></div>
        <div><dt>跌停</dt><dd class="mono is-down">{{ formatNumber(reading?.meta?.limit_down, 0) }}<span v-if="finite(reading?.meta?.limit_down)"> 家</span></dd></div>
        <div><dt>上证量能比</dt><dd class="mono">{{ formatRatio(reading?.meta?.vol_ratio) }}</dd></div>
        <div><dt>两市主力净额</dt><dd class="mono">{{ formatMoneyYuan(reading?.meta?.main_flow_total) }}</dd></div>
      </dl>
    </div>

    <div v-else class="timing-local-empty">
      <strong>贪恐指标暂不可计算</strong>
      <span>{{ missing[0] || '服务端未返回可用的综合分数' }}</span>
    </div>

    <p v-if="reading?.stale" class="timing-stale-note">陈旧数据：这是截至 {{ reading.data_date || '最近交易日' }} 收盘的日线温度，不代表今日盘中实时状态。</p>
    <details v-if="missing.length" class="timing-panel-missing">
      <summary>数据缺失与口径说明（{{ missing.length }}）</summary>
      <ul><li v-for="item in missing" :key="item">{{ item }}</li></ul>
    </details>
  </section>
</template>

<style scoped>
.timing-fear-panel { min-width: 0; overflow: hidden; }
.timing-fear-date { color: var(--text-3); font-size: 12px; }
.timing-fear-body { display: grid; grid-template-columns: minmax(180px, .65fr) minmax(280px, 1.35fr); }
.timing-fear-score { display: flex; min-width: 0; min-height: 196px; flex-direction: column; align-items: center; justify-content: center; padding: 16px; border-right: 1px solid var(--border); background: var(--surface-2); text-align: center; }
.timing-fear-score > span { color: var(--text-3); font-size: 12px; }
.timing-fear-score > strong { margin-top: 4px; font-size: 38px; line-height: 1.2; }
.timing-fear-score small { color: var(--text-3); font-size: 13px; }
.timing-fear-score b { color: var(--accent); font-size: 17px; }
.timing-fear-score > div { width: min(180px, 100%); height: 7px; overflow: hidden; margin-top: 12px; border-radius: 99px; background: var(--surface-3); }
.timing-fear-score > div i { display: block; height: 100%; border-radius: inherit; background: var(--accent); }
.timing-fear-factors { display: grid; align-content: center; gap: 8px; margin: 0; padding: 14px; }
.timing-fear-factors > div { display: grid; grid-template-columns: 92px minmax(0, 1fr); align-items: center; gap: 10px; }
.timing-fear-factors dt { color: var(--text-2); font-size: 13px; }
.timing-fear-factors dd { display: grid; grid-template-columns: minmax(80px, 1fr) 66px; align-items: center; gap: 8px; margin: 0; }
.timing-factor-track { height: 7px; overflow: hidden; border-radius: 99px; background: var(--surface-3); }
.timing-factor-track i { display: block; height: 100%; border-radius: inherit; background: var(--info); }
.timing-fear-factors strong { font-size: 13px; text-align: right; }
.timing-fear-factors small { color: var(--text-3); font-size: 12px; }
.timing-fear-meta { display: grid; grid-column: 1 / -1; grid-template-columns: repeat(6, minmax(0, 1fr)); margin: 0; border-top: 1px solid var(--border); }
.timing-fear-meta div { min-width: 0; padding: 9px 10px; border-right: 1px solid var(--border); }
.timing-fear-meta div:last-child { border-right: 0; }
.timing-fear-meta dt { color: var(--text-3); font-size: 12px; }
.timing-fear-meta dd { margin: 2px 0 0; color: var(--text-2); font-size: 13px; overflow-wrap: anywhere; }
.timing-fear-meta dd span { color: inherit; font-size: 12px; }
.timing-local-empty { display: grid; min-height: 220px; align-content: center; gap: 4px; padding: 24px; color: var(--text-3); text-align: center; }
.timing-local-empty strong { color: var(--text-2); }
.timing-stale-note { margin: 0; padding: 9px 12px; border-top: 1px solid var(--warning); background: color-mix(in srgb, var(--warning) 7%, var(--surface)); color: var(--warning); font-size: 13px; }
.timing-panel-missing summary { min-height: 44px; padding: 11px 12px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 13px; cursor: pointer; }
.timing-panel-missing ul { margin: 0; padding: 0 28px 12px 34px; color: var(--text-3); font-size: 12px; }
@media (max-width: 720px) {
  .timing-fear-body { grid-template-columns: minmax(0, 1fr); }
  .timing-fear-score { min-height: 168px; border-right: 0; border-bottom: 1px solid var(--border); }
  .timing-fear-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .timing-fear-meta div { border-bottom: 1px solid var(--border); }
  .timing-fear-meta div:nth-child(2n) { border-right: 0; }
}
@media (max-width: 420px) {
  .timing-fear-factors > div { grid-template-columns: 84px minmax(0, 1fr); gap: 7px; }
  .timing-fear-factors dd { grid-template-columns: minmax(50px, 1fr) 58px; gap: 5px; }
}
</style>
