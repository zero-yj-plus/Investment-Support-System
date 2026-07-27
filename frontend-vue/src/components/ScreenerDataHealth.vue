<script setup lang="ts">
import { computed } from 'vue'
import type { QualityState } from '../types/dashboard'
import type { ScreenerResponse } from '../types/screener'
import { formatDateTime, formatInteger, formatRatioPercent } from '../utils/screenerFormat'
import DataStatus from './DataStatus.vue'

const props = defineProps<{ response: ScreenerResponse }>()

const missing = computed(() => Array.isArray(props.response.missing) ? props.response.missing : [])
const missingExchanges = computed(() => Array.isArray(props.response.universe?.missing_exchanges) ? props.response.universe?.missing_exchanges || [] : [])
const state = computed<QualityState>(() => {
  if (props.response.ok === false || props.response.coverage?.sufficient === false) return 'missing'
  if (missing.value.length || props.response.universe?.stale || props.response.analysis_contract?.data_status === 'partial') return 'partial'
  return 'complete'
})
const label = computed(() => state.value === 'complete' ? '依据完整' : state.value === 'partial' ? '部分覆盖' : '依据不足')
</script>

<template>
  <section class="panel screener-health" aria-labelledby="screener-health-title">
    <header class="screener-health-heading">
      <div><p class="eyebrow">数据治理</p><h2 id="screener-health-title">覆盖率、版本与缺失说明</h2></div>
      <DataStatus :state="state" :label="label" />
    </header>
    <dl class="screener-health-grid">
      <div><dt>数据日期</dt><dd class="mono">{{ response.trade_date || '—' }}</dd></div>
      <div><dt>生成时点</dt><dd class="mono">{{ formatDateTime(response.ts) }}</dd></div>
      <div><dt>快照覆盖</dt><dd class="mono">{{ formatInteger(response.coverage?.received) }} / {{ formatInteger(response.coverage?.expected) }}<small>{{ formatRatioPercent(response.coverage?.percent) }}</small></dd></div>
      <div><dt>最低门槛</dt><dd class="mono">{{ response.coverage?.minimum_ratio == null ? '—' : formatRatioPercent(response.coverage.minimum_ratio * 100) }}<small>{{ response.coverage?.sufficient === true ? '已达到' : response.coverage?.sufficient === false ? '未达到' : '状态缺失' }}</small></dd></div>
      <div><dt>行业补全</dt><dd class="mono">{{ formatInteger(response.industry_coverage?.filled) }} / {{ formatInteger(response.industry_coverage?.returned) }}<small>只对返回候选低频补全</small></dd></div>
      <div><dt>筛选规则</dt><dd class="mono">{{ response.analysis_contract?.deterministic_rule_version || '版本缺失' }}<small>{{ response.analysis_contract?.decision_authority === 'candidate_only' ? '仅候选，不是推荐' : response.analysis_contract?.decision_authority || '权限语义缺失' }}</small></dd></div>
    </dl>
    <div class="screener-health-notes">
      <div>
        <h3>股票池状态</h3>
        <p v-if="response.universe?.stale">陈旧快照<template v-if="response.universe.as_of">，截至 {{ response.universe.as_of }}</template>；不得标记为实时。</p>
        <p v-else-if="missingExchanges.length">缺少交易所：{{ missingExchanges.map((item) => item.toUpperCase()).join(' / ') }}</p>
        <p v-else-if="response.universe?.complete === true">股票池完整状态已确认。</p>
        <p v-else>股票池完整性字段缺失或未确认。</p>
      </div>
      <div>
        <h3>缺失与降级原因</h3>
        <ul v-if="missing.length"><li v-for="item in missing" :key="item">{{ item }}</li></ul>
        <p v-else>接口未报告缺失项；空数组按真实无缺失展示。</p>
      </div>
      <div>
        <h3>结论边界</h3>
        <p>筛选与覆盖门槛由确定性规则执行；表格排序只重排当前返回结果。AI 状态：{{ response.analysis_contract?.ai?.status || '未提供' }}；页面不生成买入、风险或仓位结论。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.screener-health { min-width: 0; overflow: hidden; }
.screener-health-heading { display: flex; min-height: 68px; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.screener-health-heading h2 { margin: 0; font-size: 18px; }
.screener-health-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); margin: 0; border-bottom: 1px solid var(--border); }
.screener-health-grid > div { min-width: 0; padding: 11px 12px; border-right: 1px solid var(--border); }
.screener-health-grid > div:last-child { border-right: 0; }
.screener-health-grid dt { color: var(--text-3); font-size: 12px; }
.screener-health-grid dd { margin: 3px 0 0; color: var(--text); font-size: 15px; font-weight: 750; overflow-wrap: anywhere; }
.screener-health-grid small { display: block; margin-top: 3px; color: var(--text-3); font-size: 12px; }
.screener-health-notes { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
.screener-health-notes > div { min-width: 0; padding: 12px 14px; border-right: 1px solid var(--border); }
.screener-health-notes > div:last-child { border-right: 0; }
.screener-health-notes h3 { margin: 0 0 5px; font-size: 14px; }
.screener-health-notes p, .screener-health-notes ul { margin: 0; color: var(--text-3); font-size: 13px; line-height: 1.55; }
.screener-health-notes ul { padding-left: 20px; }
@media (max-width: 1100px) { .screener-health-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } .screener-health-grid > div:nth-child(3) { border-right: 0; } .screener-health-grid > div:nth-child(-n + 3) { border-bottom: 1px solid var(--border); } }
@media (max-width: 720px) {
  .screener-health-heading { align-items: flex-start; flex-direction: column; }
  .screener-health-grid, .screener-health-notes { grid-template-columns: minmax(0, 1fr); }
  .screener-health-grid > div, .screener-health-grid > div:nth-child(3), .screener-health-notes > div { border-right: 0; border-bottom: 1px solid var(--border); }
  .screener-health-grid > div:last-child, .screener-health-notes > div:last-child { border-bottom: 0; }
}
</style>
