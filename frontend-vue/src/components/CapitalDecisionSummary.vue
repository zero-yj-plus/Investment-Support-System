<script setup lang="ts">
import { computed } from 'vue'
import type { CapitalFlowRow, CapitalTrendsResponse } from '../types/capitalTracking'
import { isRawCapital } from '../utils/capitalFormat'

const props = defineProps<{
  data: CapitalTrendsResponse | null
  sectors: CapitalFlowRow[]
  hotStocks: CapitalFlowRow[]
  error: string
  snapshotting: boolean
}>()
const emit = defineEmits<{ snapshot: [] }>()

const hasRows = computed(() => props.sectors.length > 0 || props.hotStocks.length > 0)
const stale = computed(() => Boolean(props.data?.stale_sector || props.data?.stale_hot))
const rawUnit = computed(() => props.sectors.some(isRawCapital) || props.hotStocks.some(isRawCapital))

const conclusion = computed(() => {
  if (props.error && !props.data) return '资金追踪暂不可用'
  if (props.data?.ok === false) return '资金追踪尚未启用'
  if (!hasRows.value) return '当前暂无资金快照'
  if (stale.value) return '当前展示最近已发布交易日快照'
  if (rawUnit.value) return '资金方向可观察，金额口径不完整'
  return '资金快照可用，先复核多时点持续性'
})

const action = computed(() => {
  if (props.error && !props.data) return '先重试读取；数据恢复前不据此增加风险暴露。'
  if (props.data?.ok === false) return '如需使用，请先由部署配置启用资金追踪。'
  if (!hasRows.value) return '可采集一次真实快照；源为空时继续保持空数据。'
  if (stale.value) return '不要与今日实时涨跌混用；等待当日新快照后再复核。'
  if (rawUnit.value) return '只比较同口径下的方向与排名，不做金额合计。'
  return '至少对照两个采样时点，再结合服务端风险与仓位结论。'
})
</script>

<template>
  <section class="panel capital-decision" aria-labelledby="capital-decision-title">
    <div class="capital-decision__main">
      <p class="eyebrow">当前结论 · 数据边界优先</p>
      <h2 id="capital-decision-title">{{ conclusion }}</h2>
      <p>{{ action }}</p>
      <button
        type="button"
        class="primary-button capital-snapshot-button"
        :disabled="snapshotting"
        @click="emit('snapshot')"
      >{{ snapshotting ? '采集中…' : '采集真实快照' }}</button>
    </div>
    <dl class="capital-boundaries" aria-label="风险、仓位及禁止条件">
      <div><dt>仓位边界</dt><dd>本页不产生仓位上限；以服务端风险闸与正式策略为准。</dd></div>
      <div><dt>禁止条件</dt><dd>禁止把单点资金流、陈旧快照或未核验单位直接解释为买卖信号。</dd></div>
      <div><dt>缺失纪律</dt><dd>缺失保持“—”；不补 0，不用历史或示例值冒充当日数据。</dd></div>
    </dl>
  </section>
</template>

<style scoped>
.capital-decision { display: grid; min-width: 0; grid-template-columns: minmax(0, 1.25fr) minmax(320px, .75fr); overflow: hidden; }
.capital-decision__main { display: flex; min-width: 0; flex-direction: column; align-items: flex-start; padding: 20px; border-right: 1px solid var(--border); }
.capital-decision h2 { margin: 2px 0 0; font-size: clamp(22px, 2.2vw, 30px); line-height: 1.25; }
.capital-decision__main > p:not(.eyebrow) { max-width: 780px; margin: 8px 0 16px; color: var(--text-2); }
.capital-snapshot-button { min-width: 156px; }
.capital-boundaries { display: grid; min-width: 0; margin: 0; padding: 12px 16px; background: var(--surface-2); }
.capital-boundaries div { display: grid; grid-template-columns: 80px minmax(0, 1fr); align-items: start; gap: 10px; padding: 9px 0; border-bottom: 1px solid var(--border); }
.capital-boundaries div:last-child { border-bottom: 0; }
.capital-boundaries dt { color: var(--text-3); font-size: 12px; font-weight: 750; }
.capital-boundaries dd { margin: 0; color: var(--text-2); font-size: 14px; font-weight: 620; line-height: 1.55; }
@media (max-width: 900px) {
  .capital-decision { grid-template-columns: minmax(0, 1fr); }
  .capital-decision__main { border-right: 0; border-bottom: 1px solid var(--border); }
}
@media (max-width: 720px) {
  .capital-decision__main { padding: 16px; }
  .capital-snapshot-button { width: 100%; }
  .capital-boundaries div { grid-template-columns: 1fr; gap: 2px; }
  .capital-boundaries dd { font-size: 16px; }
}
</style>
