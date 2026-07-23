<script setup lang="ts">
import { computed } from 'vue'
import type { TimingReading } from '../types/timing'
import { changeTone, finite, formatNumber, formatPercent, formatRatio, scoreWidth, uniqueText } from '../utils/timingFormat'

const props = defineProps<{
  reading: TimingReading
  kind: 'index' | 'stock'
}>()

const factorOrder = ['趋势', '动量', '量能', '位置'] as const
const priceUnit = computed(() => props.kind === 'index' ? '点' : '元')
const available = computed(() => props.reading.ok !== false && finite(props.reading.score) && Boolean(props.reading.zone))
const missing = computed(() => uniqueText(props.reading.missing || []))
const zoneClass = computed(() => ({
  '进攻': 'is-offense',
  '出击': 'is-strike',
  '震荡': 'is-shock',
  '防守': 'is-defense',
}[props.reading.zone || ''] || 'is-missing'))
</script>

<template>
  <article class="timing-region-card" :class="zoneClass">
    <header class="timing-region-card__header">
      <div>
        <h3>{{ reading.name || reading.code || '名称缺失' }}</h3>
        <span class="mono">{{ reading.code || '代码缺失' }}</span>
      </div>
      <span class="timing-zone-badge">{{ available ? reading.zone : '不可计算' }}</span>
    </header>

    <div class="timing-region-card__quote">
      <div>
        <span>最新{{ kind === 'index' ? '点位' : '价格' }}</span>
        <strong class="mono">{{ formatNumber(reading.price, 2) }}<small v-if="finite(reading.price)"> {{ priceUnit }}</small></strong>
      </div>
      <div>
        <span>涨跌幅</span>
        <strong class="mono" :class="changeTone(reading.change_pct)">{{ formatPercent(reading.change_pct) }}</strong>
      </div>
      <div>
        <span>择时评分</span>
        <strong class="mono">{{ formatNumber(reading.score, 1) }}<small v-if="finite(reading.score)"> / 100</small></strong>
      </div>
    </div>

    <template v-if="available">
      <div
        class="timing-score-track"
        role="progressbar"
        :aria-label="`${reading.name || reading.code || '标的'}择时评分`"
        :aria-valuenow="reading.score as number"
        aria-valuemin="0"
        aria-valuemax="100"
      ><i :style="{ width: scoreWidth(reading.score) }"></i></div>

      <p class="timing-region-card__advice"><strong>服务端动作边界</strong>{{ reading.advice || '服务端未返回操作建议' }}</p>

      <dl class="timing-factor-list">
        <div v-for="factor in factorOrder" :key="factor">
          <dt>{{ factor }}</dt>
          <dd class="mono">{{ formatNumber(reading.factors?.[factor], 1) }}<span v-if="finite(reading.factors?.[factor])"> / 100</span></dd>
        </div>
      </dl>

      <dl class="timing-ma-list">
        <div><dt>当前</dt><dd class="mono">{{ formatNumber(reading.ma?.price, 2) }}<span v-if="finite(reading.ma?.price)"> {{ priceUnit }}</span></dd></div>
        <div><dt>MA5</dt><dd class="mono">{{ formatNumber(reading.ma?.ma5, 2) }}</dd></div>
        <div><dt>MA20</dt><dd class="mono">{{ formatNumber(reading.ma?.ma20, 2) }}</dd></div>
        <div><dt>MA60</dt><dd class="mono">{{ formatNumber(reading.ma?.ma60, 2) }}</dd></div>
        <div><dt>量能比</dt><dd class="mono">{{ formatRatio(reading.vol_ratio) }}</dd></div>
        <div><dt>日线样本</dt><dd class="mono">{{ finite(reading.bars) ? `${formatNumber(reading.bars, 0)} 根` : '—' }}</dd></div>
      </dl>
    </template>

    <div v-else class="timing-region-card__empty">
      <strong>该标的择时数据不可计算</strong>
      <span>{{ missing[0] || '服务端未返回完整的日线、区域或评分字段' }}</span>
    </div>

    <details v-if="missing.length" class="timing-card-missing">
      <summary>查看缺失与降级说明（{{ missing.length }}）</summary>
      <ul><li v-for="item in missing" :key="item">{{ item }}</li></ul>
    </details>
  </article>
</template>

<style scoped>
.timing-region-card { --zone-color: var(--border-strong); min-width: 0; overflow: hidden; border: 1px solid var(--border); border-left: 3px solid var(--zone-color); border-radius: var(--radius); background: var(--surface-2); }
.timing-region-card.is-offense { --zone-color: var(--info); }
.timing-region-card.is-strike { --zone-color: var(--accent); }
.timing-region-card.is-shock { --zone-color: var(--warning); }
.timing-region-card.is-defense { --zone-color: var(--danger); }
.timing-region-card__header { display: flex; min-height: 62px; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.timing-region-card__header > div { min-width: 0; }
.timing-region-card__header h3 { margin: 0; font-size: 16px; line-height: 1.4; }
.timing-region-card__header .mono { color: var(--text-3); font-size: 12px; }
.timing-zone-badge { flex: 0 0 auto; padding: 3px 8px; border: 1px solid var(--zone-color); border-radius: 4px; color: var(--zone-color); font-size: 12px; font-weight: 800; }
.timing-region-card__quote { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border-bottom: 1px solid var(--border); }
.timing-region-card__quote > div { min-width: 0; padding: 9px 10px; border-right: 1px solid var(--border); }
.timing-region-card__quote > div:last-child { border-right: 0; }
.timing-region-card__quote span { display: block; color: var(--text-3); font-size: 12px; }
.timing-region-card__quote strong { display: block; margin-top: 2px; font-size: 16px; overflow-wrap: anywhere; }
.timing-region-card__quote small { color: var(--text-3); font-size: 12px; }
.timing-score-track { height: 6px; margin: 10px 12px 0; overflow: hidden; border-radius: 99px; background: var(--surface-3); }
.timing-score-track i { display: block; height: 100%; border-radius: inherit; background: var(--zone-color); }
.timing-region-card__advice { margin: 10px 12px; color: var(--text-2); font-size: 14px; line-height: 1.6; }
.timing-region-card__advice strong { display: block; color: var(--zone-color); font-size: 12px; }
.timing-factor-list, .timing-ma-list { display: grid; margin: 0; border-top: 1px solid var(--border); }
.timing-factor-list { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.timing-ma-list { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.timing-factor-list div, .timing-ma-list div { min-width: 0; padding: 8px 10px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.timing-factor-list div:last-child, .timing-ma-list div:nth-child(3n) { border-right: 0; }
.timing-factor-list dt, .timing-ma-list dt { color: var(--text-3); font-size: 12px; }
.timing-factor-list dd, .timing-ma-list dd { margin: 2px 0 0; color: var(--text-2); font-size: 13px; overflow-wrap: anywhere; }
.timing-factor-list dd span { color: var(--text-3); font-size: 12px; }
.timing-region-card__empty { display: grid; min-height: 132px; align-content: center; gap: 3px; padding: 18px; color: var(--text-3); text-align: center; }
.timing-region-card__empty strong { color: var(--text-2); }
.timing-card-missing summary { min-height: 44px; padding: 11px 12px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 13px; cursor: pointer; }
.timing-card-missing ul { margin: 0; padding: 0 28px 12px 34px; color: var(--text-3); font-size: 12px; }
@media (max-width: 520px) {
  .timing-region-card__quote { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .timing-region-card__quote > div:nth-child(2) { border-right: 0; }
  .timing-region-card__quote > div:nth-child(3) { grid-column: 1 / -1; border-top: 1px solid var(--border); }
  .timing-factor-list, .timing-ma-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .timing-factor-list div:nth-child(2n), .timing-ma-list div:nth-child(2n) { border-right: 0; }
  .timing-ma-list div:nth-child(3n) { border-right: 1px solid var(--border); }
}
</style>
