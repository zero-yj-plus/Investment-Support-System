<script setup lang="ts">
import { computed } from 'vue'
import type { MoneyEffectResponse } from '../types/mainlineResearch'
import { coverageLabel, featureQuality, formatAmount, formatPercent, formatScore, kindLabel, uniqueText } from '../utils/mainlineFormat'
import MainlineFeatureHeader from './MainlineFeatureHeader.vue'

const props = defineProps<{ data: MoneyEffectResponse | null; loading: boolean; error?: string }>()
defineEmits<{ retry: [] }>()

const rows = computed(() => Array.isArray(props.data?.themes) ? props.data.themes.filter((item) => item?.name).slice(0, 12) : [])
const losers = computed(() => Array.isArray(props.data?.losers) ? props.data.losers.filter((item) => item?.name).slice(0, 6) : [])
const fieldPresent = computed(() => Array.isArray(props.data?.themes))
const quality = computed(() => featureQuality(props.data, rows.value.length > 0, props.error))
const statusLabel = computed(() => ({ complete: '赚钱效应可用', degraded: '部分因子结果', partial: '读取中', missing: '赚钱效应不可用' }[quality.value]))
const notes = computed(() => uniqueText([props.data?.degraded_factors, props.data?.missing_fields, props.data?.missing, props.data?.notes]))
</script>

<template>
  <section class="panel feature-panel" aria-labelledby="money-effect-title">
    <MainlineFeatureHeader
      title-id="money-effect-title"
      eyebrow="MONEY EFFECT"
      title="题材赚钱效应"
      description="带符号赚钱分与涨跌、宽度、涨停聚集分开呈现；部分因子分不与完整因子分等置比较。"
      :status="quality"
      :status-label="statusLabel"
      :stamp="data?.data_date || data?.ts"
    />
    <div v-if="error" class="feature-alert" role="alert">
      <span><strong>{{ data ? '最新刷新失败，保留已加载记录' : '赚钱效应暂不可用' }}</strong>{{ error }}</span>
      <button class="secondary-button" type="button" :disabled="loading" @click="$emit('retry')">重试</button>
    </div>
    <div v-if="loading && !data" class="feature-loading" role="status" aria-live="polite">
      <span class="sr-only">题材赚钱效应正在加载</span><i v-for="item in 6" :key="item"></i>
    </div>
    <ol v-else-if="rows.length" class="money-list">
      <li v-for="(item, index) in rows" :key="`${item.kind}-${item.name}`">
        <span class="rank mono">{{ String(index + 1).padStart(2, '0') }}</span>
        <div class="money-name"><strong>{{ item.name }}</strong><span>{{ kindLabel(item.kind) }} · {{ coverageLabel(item.score_coverage) }}</span></div>
        <dl>
          <div><dt>赚钱分</dt><dd class="mono">{{ formatScore(item.money_score) }}</dd></div>
          <div><dt>涨跌幅</dt><dd class="mono" :class="item.change_pct == null ? 'is-flat' : item.change_pct >= 0 ? 'is-up' : 'is-down'">{{ formatPercent(item.change_pct) }}</dd></div>
          <div><dt>涨停 / 最高板</dt><dd class="mono">{{ item.zt_count == null ? '—' : item.zt_count }} / {{ item.max_lianban == null ? '—' : item.max_lianban }}</dd></div>
          <div><dt>成交额</dt><dd class="mono">{{ formatAmount(item.amount) }}</dd></div>
        </dl>
        <p>领涨：{{ item.leader_name || '未提供' }}<span v-if="item.leader_name" class="mono" :class="item.leader_change_pct == null ? 'is-flat' : item.leader_change_pct >= 0 ? 'is-up' : 'is-down'"> {{ formatPercent(item.leader_change_pct) }}</span></p>
      </li>
    </ol>
    <div v-else-if="data" class="feature-empty">
      <strong>{{ fieldPresent ? '本次未计算出赚钱效应记录' : 'themes 字段缺失' }}</strong>
      <span>{{ fieldPresent ? '接口成功返回真实空数组，页面未用历史题材或示例数字补位。' : '接口未返回题材数组，与真实空数组分开处理。' }}</span>
    </div>
    <div v-if="losers.length" class="loser-strip"><strong>亏钱效应</strong><span v-for="item in losers" :key="`${item.kind}-${item.name}`">{{ item.name }} <b class="mono is-down">{{ formatPercent(item.change_pct) }}</b></span></div>
    <details v-if="notes.length" class="feature-notes"><summary>查看 {{ notes.length }} 条缺失与口径说明</summary><ul><li v-for="note in notes" :key="note">{{ note }}</li></ul></details>
  </section>
</template>

<style scoped>
.feature-panel { min-width: 0; overflow: hidden; }
.feature-alert { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--danger); color: var(--text-2); }
.feature-alert span { display: grid; gap: 2px; }
.feature-alert strong { color: var(--danger); }
.feature-loading { display: grid; gap: 8px; padding: 12px; }
.feature-loading i { min-height: 76px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); animation: feature-pulse 1.2s ease-in-out infinite alternate; }
.money-list { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }
.money-list li { display: grid; grid-template-columns: 28px minmax(120px, .85fr) minmax(360px, 1.7fr) minmax(150px, .8fr); align-items: center; gap: 8px; min-width: 0; padding: 9px 12px; border-bottom: 1px solid var(--border); }
.rank { color: var(--text-3); font-size: 12px; }
.money-name { display: grid; min-width: 0; }
.money-name strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.money-name span, .money-list p { margin: 0; color: var(--text-3); font-size: 12px; }
.money-list dl { display: grid; grid-template-columns: repeat(4, minmax(72px, 1fr)); gap: 6px; margin: 0; }
.money-list dl div { min-width: 0; padding: 5px 7px; border-left: 1px solid var(--border); }
.money-list dt { color: var(--text-3); font-size: 12px; }
.money-list dd { margin: 2px 0 0; font-size: var(--mainline-metric-size, 17px); font-weight: 760; line-height: 1.25; overflow-wrap: anywhere; }
.money-list p .mono { font-size: var(--mainline-metric-compact-size, 16px); font-weight: 720; }
.loser-strip { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; padding: 10px 12px; background: var(--surface-2); color: var(--text-2); font-size: 13px; }
.feature-empty { display: grid; min-height: 190px; place-content: center; gap: 4px; padding: 24px; color: var(--text-3); text-align: center; }
.feature-empty strong { color: var(--text-2); }
.feature-notes summary { min-height: 44px; padding: 11px 14px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 13px; cursor: pointer; }
.feature-notes ul { margin: 0; padding: 0 28px 14px 34px; color: var(--text-3); font-size: 12px; }
@keyframes feature-pulse { from { opacity: .45; } to { opacity: .82; } }
@media (max-width: 920px) { .money-list li { grid-template-columns: 28px minmax(120px, .8fr) minmax(0, 1.7fr); } .money-list p { grid-column: 2 / -1; } }
@media (max-width: 720px) {
  .money-list li { grid-template-columns: 28px minmax(0, 1fr); align-items: start; }
  .money-list dl, .money-list p { grid-column: 2; }
  .money-list dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .feature-alert { align-items: stretch; flex-direction: column; }
  .feature-alert button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) { .feature-loading i { animation: none; } }
</style>
