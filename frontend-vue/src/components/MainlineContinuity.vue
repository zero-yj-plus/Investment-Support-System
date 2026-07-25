<script setup lang="ts">
import { computed } from 'vue'
import type { MainlineResponse } from '../types/mainlineResearch'
import { featureQuality, formatAmount, formatScore, kindLabel, uniqueText } from '../utils/mainlineFormat'
import MainlineFeatureHeader from './MainlineFeatureHeader.vue'

const props = defineProps<{ data: MainlineResponse | null; loading: boolean; error?: string }>()
defineEmits<{ retry: [] }>()
const mainlines = computed(() => Array.isArray(props.data?.mainlines) ? props.data.mainlines.filter((item) => item?.name).slice(0, 10) : [])
const todayTop = computed(() => Array.isArray(props.data?.today_top) ? props.data.today_top.filter((item) => item?.name).slice(0, 8) : [])
const fieldsPresent = computed(() => Array.isArray(props.data?.mainlines) || Array.isArray(props.data?.today_top))
const hasRows = computed(() => mainlines.value.length > 0 || todayTop.value.length > 0)
const quality = computed(() => featureQuality(props.data, hasRows.value, props.error))
const statusLabel = computed(() => ({ complete: '持续性可用', degraded: '持续性部分覆盖', partial: '样本积累中', missing: '持续性不可用' }[quality.value]))
const notes = computed(() => uniqueText([props.data?.degraded_factors, props.data?.missing_fields, props.data?.missing, props.data?.notes]))
</script>

<template>
  <section class="panel continuity-panel" aria-labelledby="continuity-title">
    <MainlineFeatureHeader title-id="continuity-title" eyebrow="CONTINUITY" title="跨日主线持续性" description="强度仅由上榜天数与平均赚钱效应构成；累计资金流只作证据展示，不参与排序。" :status="quality" :status-label="statusLabel" :stamp="data?.data_date || data?.ts" />
    <div v-if="error" class="continuity-alert" role="alert"><span><strong>{{ data ? '最新刷新失败，保留已加载记录' : '跨日持续性暂不可用' }}</strong>{{ error }}</span><button class="secondary-button" type="button" :disabled="loading" @click="$emit('retry')">重试</button></div>
    <p v-if="data?.switch_signal" class="switch-signal"><strong>切换观察</strong>{{ data.switch_signal }}</p>
    <p v-if="data?.note" class="accumulating-note"><strong>{{ data.state_label || '样本积累中' }}</strong>{{ data.note }}</p>
    <div v-if="loading && !data" class="continuity-loading" role="status" aria-live="polite"><span class="sr-only">跨日主线正在加载</span><i v-for="item in 5" :key="item"></i></div>
    <ol v-else-if="mainlines.length" class="continuity-list">
      <li v-for="(item, index) in mainlines" :key="`${item.kind}-${item.name}`">
        <span class="continuity-rank mono">{{ String(index + 1).padStart(2, '0') }}</span><div class="continuity-name"><strong>{{ item.name }}</strong><small>{{ kindLabel(item.kind) }}</small></div>
        <dl><div><dt>主线强度</dt><dd class="mono">{{ formatScore(item.strength) }}</dd></div><div><dt>上榜天数</dt><dd class="mono">{{ item.days_on_board == null ? '—' : `${item.days_on_board}日` }}</dd></div><div><dt>平均赚钱分</dt><dd class="mono">{{ formatScore(item.avg_money_score) }}</dd></div><div><dt>累计资金流</dt><dd class="mono">{{ formatAmount(item.cum_main_flow) }}</dd></div></dl>
        <p>{{ item.flow_note || '资金字段仅展示，不参与主线强度。' }}</p>
      </li>
    </ol>
    <div v-else-if="todayTop.length" class="today-top"><h3>今日赚钱效应榜首（非跨日主线）</h3><ol><li v-for="(item, index) in todayTop" :key="`${item.kind}-${item.name}`"><span class="mono">{{ index + 1 }}</span><strong>{{ item.name }}</strong><small>{{ kindLabel(item.kind) }}</small><b class="mono">{{ formatScore(item.money_score) }}</b></li></ol></div>
    <div v-else-if="data" class="continuity-empty"><strong>{{ fieldsPresent ? '当前没有形成可展示的跨日主线' : '主线列表字段缺失' }}</strong><span>{{ fieldsPresent ? '接口成功返回真实空数组，页面未把今日题材或历史记录冒充跨日主线。' : '接口未返回 mainlines / today_top 数组，与真实空数组分开处理。' }}</span></div>
    <details v-if="notes.length" class="continuity-notes"><summary>查看 {{ notes.length }} 条缺失与口径说明</summary><ul><li v-for="note in notes" :key="note">{{ note }}</li></ul></details>
  </section>
</template>

<style scoped>
.continuity-panel { min-width: 0; overflow: hidden; }
.continuity-alert { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--danger); color: var(--text-2); }.continuity-alert span { display: grid; gap: 2px; }.continuity-alert strong { color: var(--danger); }
.switch-signal,.accumulating-note { display: grid; grid-template-columns: 100px minmax(0, 1fr); gap: 10px; margin: 0; padding: 10px 14px; border-bottom: 1px solid var(--warning); color: var(--text-2); font-size: 13px; }.switch-signal strong { color: var(--warning); }.accumulating-note { border-bottom-color: var(--info); }.accumulating-note strong { color: var(--info); }
.continuity-loading { display: grid; gap: 8px; padding: 12px; }.continuity-loading i { min-height: 72px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); animation: continuity-pulse 1.2s ease-in-out infinite alternate; }
.continuity-list { margin: 0; padding: 0; list-style: none; }.continuity-list li { display: grid; grid-template-columns: 28px minmax(120px, .7fr) minmax(390px, 1.6fr) minmax(170px, .8fr); align-items: center; gap: 8px; padding: 9px 12px; border-bottom: 1px solid var(--border); }.continuity-rank { color: var(--text-3); font-size: 12px; }.continuity-name { display: grid; }.continuity-name small { color: var(--text-3); font-size: 12px; }.continuity-list dl { display: grid; grid-template-columns: repeat(4, minmax(76px, 1fr)); gap: 6px; margin: 0; }.continuity-list dl div { padding-left: 7px; border-left: 1px solid var(--border); }.continuity-list dt { color: var(--text-3); font-size: 12px; }.continuity-list dd { margin: 2px 0 0; font-size: var(--mainline-metric-size, 17px); font-weight: 760; line-height: 1.25; overflow-wrap: anywhere; }.continuity-list p { margin: 0; color: var(--text-3); font-size: 12px; }
.today-top { padding: 12px; }.today-top h3 { margin: 0 0 8px; font-size: 14px; }.today-top ol { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }.today-top li { display: grid; grid-template-columns: 28px minmax(0, 1fr) 56px 68px; align-items: center; gap: 6px; padding: 8px; border: 1px solid var(--border); border-radius: 6px; }.today-top small { color: var(--text-3); }.today-top b { font-size: var(--mainline-metric-size, 17px); line-height: 1.25; text-align: right; }
.continuity-empty { display: grid; min-height: 190px; place-content: center; gap: 4px; padding: 24px; color: var(--text-3); text-align: center; }.continuity-empty strong { color: var(--text-2); }
.continuity-notes summary { min-height: 44px; padding: 11px 14px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 13px; cursor: pointer; }.continuity-notes ul { margin: 0; padding: 0 28px 14px 34px; color: var(--text-3); font-size: 12px; }
@keyframes continuity-pulse { from { opacity: .45; } to { opacity: .82; } }
@media (max-width: 980px) { .continuity-list li { grid-template-columns: 28px minmax(120px, .7fr) minmax(0, 1.6fr); }.continuity-list p { grid-column: 2 / -1; } }
@media (max-width: 720px) { .continuity-alert { align-items: stretch; flex-direction: column; }.continuity-list li { grid-template-columns: 28px minmax(0, 1fr); }.continuity-list dl,.continuity-list p { grid-column: 2; }.continuity-list dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }.switch-signal,.accumulating-note { grid-template-columns: minmax(0, 1fr); gap: 3px; } }
@media (prefers-reduced-motion: reduce) { .continuity-loading i { animation: none; } }
</style>
