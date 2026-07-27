<script setup lang="ts">
import { computed } from 'vue'
import type { CrowdednessResponse } from '../types/mainlineResearch'
import { coverageLabel, featureQuality, finite, formatPlainPercent, formatPercent, formatScore, kindLabel, uniqueText } from '../utils/mainlineFormat'
import MainlineFeatureHeader from './MainlineFeatureHeader.vue'

const props = defineProps<{ data: CrowdednessResponse | null; loading: boolean; error?: string }>()
defineEmits<{ retry: [] }>()
const rows = computed(() => Array.isArray(props.data?.sectors) ? props.data.sectors.filter((item) => item?.name).slice(0, 14) : [])
const fieldPresent = computed(() => Array.isArray(props.data?.sectors))
const quality = computed(() => featureQuality(props.data, rows.value.length > 0, props.error))
const statusLabel = computed(() => ({ complete: '拥挤度可用', degraded: '简化或部分口径', partial: '读取中', missing: '拥挤度不可用' }[quality.value]))
const notes = computed(() => uniqueText([props.data?.degraded_factors, props.data?.missing_fields, props.data?.missing, props.data?.notes]))
function scoreWidth(value: unknown) { return finite(value) ? `${Math.max(0, Math.min(100, value))}%` : '0%' }
function levelClass(level?: string) { return level === '过热拥挤' ? 'is-hot' : level === '偏拥挤' ? 'is-warm' : level === '冷清' ? 'is-cold' : 'is-normal' }
</script>

<template>
  <section class="panel crowd-panel" aria-labelledby="crowdedness-title">
    <MainlineFeatureHeader title-id="crowdedness-title" eyebrow="CROWDEDNESS" title="交易拥挤度" description="高分表示当前横截面更拥挤，不等同买卖建议；分数、等级与因子覆盖均来自服务端。" :status="quality" :status-label="statusLabel" :stamp="data?.data_date || data?.ts" />
    <div v-if="error" class="crowd-alert" role="alert"><span><strong>{{ data ? '最新刷新失败，保留已加载记录' : '交易拥挤度暂不可用' }}</strong>{{ error }}</span><button class="secondary-button" type="button" :disabled="loading" @click="$emit('retry')">重试</button></div>
    <div v-if="loading && !data" class="crowd-loading" role="status" aria-live="polite"><span class="sr-only">交易拥挤度正在加载</span><i v-for="item in 6" :key="item"></i></div>
    <ol v-else-if="rows.length" class="crowd-list">
      <li v-for="(item, index) in rows" :key="`${item.kind}-${item.name}`">
        <div class="crowd-name"><span class="mono">{{ String(index + 1).padStart(2, '0') }}</span><strong>{{ item.name }}</strong><small>{{ kindLabel(item.kind) }} · {{ coverageLabel(item.score_coverage) }}</small></div>
        <div v-if="finite(item.crowd_score)" class="crowd-score">
          <div class="crowd-track" role="progressbar" :aria-label="`${item.name}拥挤分`" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="finite(item.crowd_score) ? item.crowd_score : undefined"><i :style="{ width: scoreWidth(item.crowd_score) }"></i></div>
          <strong class="mono">{{ formatScore(item.crowd_score, 0) }}</strong>
          <span class="crowd-level" :class="levelClass(item.level)">{{ item.level || '等级未提供' }}</span>
        </div>
        <div v-else class="crowd-score-missing"><strong>拥挤分未提供</strong><span>{{ item.level || '等级未提供' }}</span></div>
        <dl><div><dt>成交占比</dt><dd class="mono">{{ formatPlainPercent(item.amt_share) }}</dd></div><div><dt>涨跌幅</dt><dd class="mono" :class="item.change_pct == null ? 'is-flat' : item.change_pct >= 0 ? 'is-up' : 'is-down'">{{ formatPercent(item.change_pct) }}</dd></div><div><dt>行业涨停</dt><dd class="mono">{{ item.zt_count == null ? '—' : item.zt_count }}</dd></div></dl>
      </li>
    </ol>
    <div v-else-if="data" class="crowd-empty"><strong>{{ fieldPresent ? '本次未计算出拥挤度记录' : 'sectors 字段缺失' }}</strong><span>{{ fieldPresent ? '接口成功返回真实空数组，页面未补入示例题材。' : '接口未返回板块数组，与真实空数组分开处理。' }}</span></div>
    <details v-if="notes.length" class="crowd-notes"><summary>查看 {{ notes.length }} 条缺失与口径说明</summary><ul><li v-for="note in notes" :key="note">{{ note }}</li></ul></details>
  </section>
</template>

<style scoped>
.crowd-panel { min-width: 0; overflow: hidden; }
.crowd-alert { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--danger); color: var(--text-2); }
.crowd-alert span { display: grid; gap: 2px; }.crowd-alert strong { color: var(--danger); }
.crowd-loading { display: grid; gap: 8px; padding: 12px; }.crowd-loading i { min-height: 64px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); animation: crowd-pulse 1.2s ease-in-out infinite alternate; }
.crowd-list { margin: 0; padding: 0; list-style: none; }
.crowd-list li { display: grid; grid-template-columns: minmax(180px, .9fr) minmax(220px, 1.2fr) minmax(220px, .9fr); align-items: center; gap: 10px; min-width: 0; padding: 9px 12px; border-bottom: 1px solid var(--border); }
.crowd-name { display: grid; grid-template-columns: 24px minmax(0, 1fr); align-items: center; gap: 0 7px; min-width: 0; }.crowd-name > span { grid-row: 1 / 3; color: var(--text-3); font-size: 12px; }.crowd-name strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.crowd-name small { color: var(--text-3); font-size: 12px; }
.crowd-score { display: grid; grid-template-columns: minmax(80px, 1fr) 42px auto; align-items: center; gap: 7px; }.crowd-track { height: 10px; overflow: hidden; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-3); }.crowd-track i { display: block; height: 100%; background: var(--info); }.crowd-score > strong { font-size: var(--mainline-metric-size, 17px); font-weight: 760; line-height: 1.25; text-align: right; }
.crowd-score-missing { display: grid; gap: 2px; color: var(--text-3); font-size: 12px; }.crowd-score-missing strong { color: var(--text-2); font-size: 13px; }
.crowd-level { padding: 2px 6px; border: 1px solid currentColor; border-radius: 4px; font-size: 12px; font-weight: 700; white-space: nowrap; }.crowd-level.is-hot { color: var(--danger); }.crowd-level.is-warm { color: var(--warning); }.crowd-level.is-cold { color: var(--info); }.crowd-level.is-normal { color: var(--text-2); }
.crowd-list dl { display: grid; grid-template-columns: repeat(3, minmax(58px, 1fr)); gap: 6px; margin: 0; }.crowd-list dl div { padding-left: 7px; border-left: 1px solid var(--border); }.crowd-list dt { color: var(--text-3); font-size: 12px; }.crowd-list dd { margin: 2px 0 0; font-size: var(--mainline-metric-size, 17px); font-weight: 760; line-height: 1.25; overflow-wrap: anywhere; }
.crowd-empty { display: grid; min-height: 190px; place-content: center; gap: 4px; padding: 24px; color: var(--text-3); text-align: center; }.crowd-empty strong { color: var(--text-2); }
.crowd-notes summary { min-height: 44px; padding: 11px 14px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 13px; cursor: pointer; }.crowd-notes ul { margin: 0; padding: 0 28px 14px 34px; color: var(--text-3); font-size: 12px; }
@keyframes crowd-pulse { from { opacity: .45; } to { opacity: .82; } }
@media (max-width: 800px) { .crowd-list li { grid-template-columns: minmax(0, 1fr); }.crowd-list dl { padding-left: 31px; }.crowd-score { padding-left: 31px; } }
@media (max-width: 720px) { .crowd-alert { align-items: stretch; flex-direction: column; }.crowd-alert button { width: 100%; } }
@media (prefers-reduced-motion: reduce) { .crowd-loading i { animation: none; } }
</style>
