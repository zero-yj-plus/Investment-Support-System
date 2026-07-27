<script setup lang="ts">
import { computed } from 'vue'
import type { LeadersResponse } from '../types/mainlineResearch'
import { featureQuality, formatAmount, formatPlainPercent, uniqueText } from '../utils/mainlineFormat'
import MainlineFeatureHeader from './MainlineFeatureHeader.vue'

const props = defineProps<{ data: LeadersResponse | null; loading: boolean; error?: string }>()
defineEmits<{ retry: [] }>()
const ladder = computed(() => Array.isArray(props.data?.ladder) ? props.data.ladder.filter((item) => Array.isArray(item?.stocks) && item.stocks.length) : [])
const popularity = computed(() => Array.isArray(props.data?.popularity) ? props.data.popularity.filter((item) => item?.name).slice(0, 10) : [])
const fieldsPresent = computed(() => Array.isArray(props.data?.ladder) || Array.isArray(props.data?.popularity))
const hasRows = computed(() => ladder.value.length > 0 || popularity.value.length > 0)
const quality = computed(() => featureQuality(props.data, hasRows.value, props.error))
const statusLabel = computed(() => ({ complete: '龙头梯队可用', degraded: '龙头证据部分覆盖', partial: '读取中', missing: '龙头梯队不可用' }[quality.value]))
const notes = computed(() => uniqueText([props.data?.degraded_factors, props.data?.missing_fields, props.data?.missing, props.data?.notes]))
</script>

<template>
  <section class="panel leaders-panel" aria-labelledby="leaders-title">
    <MainlineFeatureHeader title-id="leaders-title" eyebrow="LEADER LADDER" title="情绪龙头梯队" description="最高板只在连板梯队中呈现一次；封单额、换手率和首封时点保持接口原始缺失语义。" :status="quality" :status-label="statusLabel" :stamp="data?.data_date || data?.ts" />
    <div v-if="error" class="leaders-alert" role="alert"><span><strong>{{ data ? '最新刷新失败，保留已加载记录' : '龙头梯队暂不可用' }}</strong>{{ error }}</span><button class="secondary-button" type="button" :disabled="loading" @click="$emit('retry')">重试</button></div>
    <div v-if="loading && !data" class="leaders-loading" role="status" aria-live="polite"><span class="sr-only">情绪龙头梯队正在加载</span><i v-for="item in 5" :key="item"></i></div>
    <div v-else-if="hasRows" class="leaders-grid">
      <div class="ladder-list">
        <article v-for="item in ladder" :key="`${item.lianban}-${item.label}`">
          <header><strong>{{ item.label || (item.lianban == null ? '板位待补' : `${item.lianban}连板`) }}</strong><span class="mono">{{ item.count == null ? item.stocks?.length ?? '—' : item.count }}只</span></header>
          <ul><li v-for="stock in item.stocks" :key="stock.code || stock.name" :class="{ 'is-leader': stock.is_leader }"><div><strong>{{ stock.name || '名称未提供' }}</strong><span class="mono">{{ stock.code || '代码未提供' }}</span></div><p>{{ stock.reason || '涨停原因未提供' }}</p><dl><div><dt>封单额</dt><dd class="mono">{{ formatAmount(stock.seal_amount) }}</dd></div><div><dt>换手率</dt><dd class="mono">{{ formatPlainPercent(stock.turnover_rate) }}</dd></div><div><dt>首封</dt><dd class="mono">{{ stock.first_time || '—' }}</dd></div></dl><b v-if="stock.is_leader">最高板</b></li></ul>
        </article>
      </div>
      <aside v-if="popularity.length" aria-labelledby="popularity-title"><h3 id="popularity-title">人气承接</h3><ol><li v-for="(stock, index) in popularity" :key="stock.code || stock.name"><span class="mono">{{ String(index + 1).padStart(2, '0') }}</span><div><strong>{{ stock.name || '名称未提供' }}</strong><small class="mono">{{ stock.code || '代码未提供' }}</small></div><dl><div><dt>板位</dt><dd class="mono">{{ stock.lianban == null ? '—' : `${stock.lianban}板` }}</dd></div><div><dt>封单额</dt><dd class="mono">{{ formatAmount(stock.seal_amount) }}</dd></div><div><dt>封单比</dt><dd class="mono">{{ formatPlainPercent(stock.seal_ratio) }}</dd></div></dl></li></ol></aside>
    </div>
    <div v-else-if="data" class="leaders-empty"><strong>{{ fieldsPresent ? '当前批次未形成龙头梯队' : '梯队字段缺失' }}</strong><span>{{ fieldsPresent ? '接口成功返回真实空数组，页面未使用历史龙头或示例股票补位。' : '接口未返回 ladder / popularity 数组，与真实空数组分开处理。' }}</span></div>
    <details v-if="notes.length" class="leaders-notes"><summary>查看 {{ notes.length }} 条缺失与口径说明</summary><ul><li v-for="note in notes" :key="note">{{ note }}</li></ul></details>
  </section>
</template>

<style scoped>
.leaders-panel { min-width: 0; overflow: hidden; }
.leaders-alert { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--danger); color: var(--text-2); }.leaders-alert span { display: grid; gap: 2px; }.leaders-alert strong { color: var(--danger); }
.leaders-loading { display: grid; gap: 8px; padding: 12px; }.leaders-loading i { min-height: 70px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); animation: leaders-pulse 1.2s ease-in-out infinite alternate; }
.leaders-grid { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(300px, .8fr); align-items: start; }.ladder-list { min-width: 0; padding: 12px; border-right: 1px solid var(--border); }.ladder-list article + article { margin-top: 10px; }.ladder-list article > header { display: flex; align-items: center; justify-content: space-between; min-height: 34px; padding: 0 9px; border: 1px solid var(--border); border-bottom: 0; border-radius: 6px 6px 0 0; background: var(--surface-2); }.ladder-list article > header strong { font-size: 14px; }.ladder-list article > header span { color: var(--text-2); font-size: var(--mainline-metric-compact-size, 16px); font-weight: 720; }.ladder-list ul { display: grid; gap: 0; margin: 0; padding: 0; border: 1px solid var(--border); border-radius: 0 0 6px 6px; list-style: none; }.ladder-list li { position: relative; display: grid; grid-template-columns: minmax(130px, .75fr) minmax(160px, 1fr) minmax(250px, 1.1fr); align-items: center; gap: 8px; min-width: 0; padding: 8px 10px; border-bottom: 1px solid var(--border); }.ladder-list li:last-child { border-bottom: 0; }.ladder-list li.is-leader { border-left: 3px solid var(--accent); }.ladder-list li > div { display: grid; }.ladder-list li > div span,.ladder-list li p { color: var(--text-3); font-size: 12px; }.ladder-list li p { margin: 0; }.ladder-list li dl { display: grid; grid-template-columns: repeat(3, minmax(68px, 1fr)); gap: 6px; margin: 0; }.ladder-list li dl div { padding-left: 7px; border-left: 1px solid var(--border); }.ladder-list dt { color: var(--text-3); font-size: 12px; }.ladder-list dd { margin: 2px 0 0; font-size: var(--mainline-metric-compact-size, 16px); font-weight: 740; line-height: 1.25; overflow-wrap: anywhere; }.ladder-list li > b { position: absolute; top: 3px; right: 4px; color: var(--accent); font-size: 11px; }
.leaders-grid aside { min-width: 0; padding: 12px; }.leaders-grid aside h3 { margin: 0 0 8px; font-size: 14px; }.leaders-grid aside ol { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }.leaders-grid aside li { display: grid; grid-template-columns: 28px minmax(90px, .8fr) minmax(180px, 1.2fr); align-items: center; gap: 6px; padding: 8px; border: 1px solid var(--border); border-radius: 6px; }.leaders-grid aside li > span,.leaders-grid aside small { color: var(--text-3); font-size: 12px; }.leaders-grid aside li > div { display: grid; }.leaders-grid aside dl { display: grid; grid-template-columns: repeat(3, minmax(54px, 1fr)); gap: 4px; margin: 0; }.leaders-grid aside dt { color: var(--text-3); font-size: 11px; }.leaders-grid aside dd { margin: 1px 0 0; font-size: var(--mainline-metric-compact-size, 16px); font-weight: 720; line-height: 1.25; overflow-wrap: anywhere; }
.leaders-empty { display: grid; min-height: 210px; place-content: center; gap: 4px; padding: 24px; color: var(--text-3); text-align: center; }.leaders-empty strong { color: var(--text-2); }
.leaders-notes summary { min-height: 44px; padding: 11px 14px; border-top: 1px solid var(--border); color: var(--text-3); font-size: 13px; cursor: pointer; }.leaders-notes ul { margin: 0; padding: 0 28px 14px 34px; color: var(--text-3); font-size: 12px; }
@keyframes leaders-pulse { from { opacity: .45; } to { opacity: .82; } }
@media (max-width: 1120px) { .leaders-grid { grid-template-columns: minmax(0, 1fr); }.ladder-list { border-right: 0; border-bottom: 1px solid var(--border); } }
@media (max-width: 760px) { .ladder-list li { grid-template-columns: minmax(0, 1fr); }.ladder-list li dl { grid-template-columns: repeat(3, minmax(0, 1fr)); }.leaders-grid aside li { grid-template-columns: 28px minmax(0, .8fr) minmax(0, 1.2fr); }.leaders-alert { align-items: stretch; flex-direction: column; }.leaders-alert button { width: 100%; } }
@media (max-width: 520px) { .leaders-grid aside li { grid-template-columns: 28px minmax(0, 1fr); }.leaders-grid aside dl { grid-column: 2; } }
@media (prefers-reduced-motion: reduce) { .leaders-loading i { animation: none; } }
</style>
