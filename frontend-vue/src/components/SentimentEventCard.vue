<script setup lang="ts">
import { computed, ref } from 'vue'
import type { SentimentArchiveState, SentimentEvent, SentimentMappingMatch } from '../types/sentimentEvents'
import {
  compactTime,
  degradedReasonLabel,
  displayTime,
  eventDescription,
  eventTitle,
  evidenceLevelLabel,
  finiteNumber,
  formatPercent,
  observationLabel,
  safeHttpUrl,
  sourceRelationLabel,
} from '../utils/sentimentFormat'

const props = defineProps<{
  event: SentimentEvent
  index: number
  archiveState?: SentimentArchiveState
}>()
const emit = defineEmits<{ archive: [] }>()
const expanded = ref(false)
const detailsId = computed(() => `sentiment-event-details-${props.index}`)
const contentTitleId = computed(() => `sentiment-event-content-title-${props.index}`)
const mappingTitleId = computed(() => `sentiment-event-mapping-title-${props.index}`)
const evidenceTitleId = computed(() => `sentiment-event-evidence-title-${props.index}`)
const observations = computed(() => Array.isArray(props.event.observations) ? props.event.observations.filter(Boolean) : [])
const mapping = computed(() => props.event.a_share || {})
const matches = computed(() => Array.isArray(mapping.value.matches) ? mapping.value.matches.filter(Boolean) : [])
const stocks = computed(() => matches.value.filter((item) => item.kind === 'stock'))
const industries = computed(() => matches.value.filter((item) => item.kind === 'industry'))
const concepts = computed(() => matches.value.filter((item) => item.kind === 'concept'))
const candidates = computed(() => Array.isArray(mapping.value.topic_candidates) ? mapping.value.topic_candidates.filter(Boolean) : [])
const published = computed(() => compactTime(props.event.published_at || props.event.time))
const primarySource = computed(() => observations.value[0]?.collection_platform_name || props.event.source || '公开采集')
const observation = computed(() => mapping.value.observation)
const observationState = computed(() => observationLabel(observation.value, mapping.value.market_check?.label))
const observationProgress = computed(() => {
  const complete = finiteNumber(observation.value?.progress?.complete)
  const total = finiteNumber(observation.value?.progress?.total)
  return complete == null || total == null ? '检查进度未提供' : `${complete}/${total} 个时点`
})
const observationSnapshot = computed(() => observation.value?.result || observation.value?.latest)
const endpointCount = computed(() => finiteNumber(props.event.corroboration?.collection_endpoint_count) ?? finiteNumber(props.event.confirms))
const kind = computed(() => {
  if (mapping.value.state === 'mapped' && mapping.value.level === 'direct_stock') return { label: '公司点名', tone: 'direct' }
  if (mapping.value.state === 'mapped') return { label: 'A 股相关', tone: 'related' }
  if (mapping.value.state === 'candidate') return { label: '主题线索', tone: 'candidate' }
  if (mapping.value.state === 'unavailable') return { label: '映射不可用', tone: 'unavailable' }
  return { label: '市场资讯', tone: 'neutral' }
})
const archiveStatus = computed(() => props.archiveState?.status || 'idle')
const archiveMessageId = computed(() => `sentiment-event-archive-message-${props.index}`)

function names(rows: SentimentMappingMatch[], withCode = false) {
  return rows.map((item) => {
    const label = item.name || item.code || '名称未提供'
    return withCode && item.code ? `${label}（${String(item.code).slice(0, 6)}）` : label
  }).join('、')
}

function toggleDetails() {
  expanded.value = !expanded.value
}
</script>

<template>
  <article class="sentiment-event-card" :class="`is-${kind.tone}`">
    <div class="sentiment-event-card__time">
      <time class="mono" :datetime="event.published_at || event.time || undefined">{{ published.time || '—' }}</time>
      <span v-if="published.date" class="mono">{{ published.date }}</span>
      <span>{{ primarySource }}</span>
    </div>

    <div class="sentiment-event-card__event">
      <div class="sentiment-event-card__title">
        <span class="sentiment-kind" :class="`is-${kind.tone}`">{{ kind.label }}</span>
        <h3>{{ eventTitle(event) }}</h3>
      </div>
      <p v-if="eventDescription(event)" class="sentiment-event-card__summary">{{ eventDescription(event) }}</p>
    </div>

    <div class="sentiment-event-card__mapping" aria-label="A 股映射">
      <span v-if="stocks.length" class="mapping-chip"><small>公司</small><strong>{{ names(stocks, true) }}</strong></span>
      <span v-if="industries.length" class="mapping-chip"><small>行业</small><strong>{{ names(industries) }}</strong></span>
      <span v-if="concepts.length" class="mapping-chip"><small>概念</small><strong>{{ names(concepts) }}</strong></span>
      <span v-if="candidates.length" class="mapping-chip"><small>主题候选</small><strong>{{ names(candidates) }}</strong></span>
      <span v-if="!stocks.length && !industries.length && !concepts.length && !candidates.length" class="sentiment-missing">
        {{ mapping.state === 'unavailable' ? 'A 股映射底座暂不可用' : '暂无明确 A 股关联' }}
      </span>
    </div>

    <div class="sentiment-event-card__state">
      <span class="observation-badge" :class="`is-${observationState.tone}`">{{ observationState.label }}</span>
      <small>{{ observationState.note }}</small>
    </div>

    <div class="sentiment-event-card__actions">
      <button
        type="button"
        class="sentiment-event-toggle"
        :aria-expanded="expanded"
        :aria-controls="detailsId"
        @click="toggleDetails"
      >{{ expanded ? '收起事件详情' : '展开事件详情与来源' }}</button>
      <button
        type="button"
        class="sentiment-event-archive"
        :disabled="archiveStatus === 'archiving' || archiveStatus === 'archived'"
        :aria-describedby="archiveMessageId"
        @click="emit('archive')"
      >{{ archiveStatus === 'archiving' ? '留档中…' : archiveStatus === 'archived' ? '已留档' : '留档到知识库' }}</button>
      <span
        :id="archiveMessageId"
        class="sentiment-event-archive__message"
        :class="{ 'is-error': archiveStatus === 'error' }"
        :role="archiveStatus === 'error' ? 'alert' : 'status'"
        aria-live="polite"
      >{{ archiveState?.message || '' }}</span>
    </div>

    <div v-if="expanded" :id="detailsId" class="sentiment-event-details">
      <section :aria-labelledby="contentTitleId">
        <h4 :id="contentTitleId">事件原文</h4>
        <p class="sentiment-event-content">{{ event.content || event.title || '上游未提供正文' }}</p>
      </section>

      <section :aria-labelledby="mappingTitleId">
        <h4 :id="mappingTitleId">A 股映射与盘面留痕</h4>
        <dl class="sentiment-detail-list">
          <div><dt>映射状态</dt><dd>{{ mapping.state || '未提供' }}</dd></div>
          <div><dt>映射层级</dt><dd>{{ mapping.level || '未提供' }}</dd></div>
          <div><dt>观察状态</dt><dd>{{ observationState.label }}；{{ observationState.note }}</dd></div>
          <div><dt>目标日期</dt><dd class="mono">{{ observation?.target_trade_date || '—' }}</dd></div>
          <div><dt>检查进度</dt><dd class="mono">{{ observationProgress }}</dd></div>
        </dl>
        <div v-if="observationSnapshot" class="sentiment-observation-facts">
          <p>盘面记录（非因果）</p>
          <span v-for="stock in observationSnapshot.stocks || []" :key="`stock-${stock.code || stock.name}`">
            {{ stock.name || stock.code || '股票' }} {{ formatPercent(stock.since_baseline_pct ?? stock.change_pct) }}
          </span>
          <template v-for="sector in observationSnapshot.sectors || []" :key="`sector-${sector.code || sector.name}`">
            <span>{{ sector.name || sector.code || '板块' }}中位 {{ formatPercent(sector.median_change_pct) }}</span>
            <span>上涨占比 {{ finiteNumber(sector.up_ratio_pct) == null ? '—' : `${finiteNumber(sector.up_ratio_pct)?.toFixed(1)}%` }}</span>
          </template>
        </div>
        <p v-if="mapping.coverage?.missing?.length" class="sentiment-coverage-note">
          覆盖说明：{{ mapping.coverage.missing.join('；') }}
        </p>
        <ul v-if="observation?.checkpoints?.length" class="sentiment-checkpoints" aria-label="观察检查点">
          <li v-for="checkpoint in observation.checkpoints" :key="checkpoint.key || checkpoint.name">
            <span>{{ checkpoint.name || checkpoint.key || '未命名检查点' }}</span>
            <strong>{{ checkpoint.status === 'complete' ? '已记录' : (checkpoint.status === 'missing' ? '缺失' : '待记录') }}</strong>
          </li>
        </ul>
        <p class="sentiment-causality-note">“已留痕”只表示目标交易日盘面已经记录；不判断利多利空，也不代表新闻造成涨跌。</p>
      </section>

      <section :aria-labelledby="evidenceTitleId">
        <h4 :id="evidenceTitleId">采集证据</h4>
        <p class="sentiment-evidence-summary">
          采集端命中 {{ endpointCount == null ? '—' : endpointCount }} 个 · 独立信源{{ event.corroboration?.independent_origin_confirmed ? '已确认' : '待确认' }}
        </p>
        <div v-if="observations.length" class="sentiment-source-list">
          <div v-for="(source, sourceIndex) in observations" :key="`${source.source_id || 'source'}-${sourceIndex}`" class="sentiment-source-item">
            <header><strong>{{ source.collection_platform_name || source.source_id || '未知采集端' }}</strong><span>{{ evidenceLevelLabel(source.evidence_level) }}</span></header>
            <dl>
              <div><dt>上游发布方</dt><dd>{{ source.publisher_name || '未披露' }}</dd></div>
              <div><dt>来源关系</dt><dd>{{ sourceRelationLabel(source.source_relation) }}</dd></div>
              <div><dt>来源时间</dt><dd class="mono">{{ displayTime(source.published_at) }}</dd></div>
              <div><dt>抓取时间</dt><dd class="mono">{{ displayTime(source.fetched_at) }}</dd></div>
            </dl>
            <a v-if="safeHttpUrl(source.source_url)" :href="safeHttpUrl(source.source_url)" target="_blank" rel="noopener noreferrer">查看原文</a>
            <span v-else class="sentiment-source-missing">原文链接未提供</span>
            <p v-if="source.degraded_reasons?.length">覆盖说明：{{ source.degraded_reasons.map(degradedReasonLabel).join('；') }}</p>
          </div>
        </div>
        <p v-else class="sentiment-coverage-note">该条为兼容数据，未携带可核验的采集观测。</p>
      </section>
    </div>
  </article>
</template>

<style scoped>
.sentiment-event-card { display: grid; min-width: 0; grid-template-columns: 112px minmax(260px, 1.4fr) minmax(230px, 1fr) 150px; gap: 10px 14px; padding: 12px 14px; border: 1px solid var(--border); border-left: 3px solid var(--border-strong); border-radius: var(--radius); background: var(--surface); }
.sentiment-event-card.is-related, .sentiment-event-card.is-direct { border-left-color: var(--info); }
.sentiment-event-card.is-candidate { border-left-color: var(--warning); }
.sentiment-event-card.is-unavailable { border-left-color: var(--danger); }
.sentiment-event-card__time { display: flex; min-width: 0; flex-direction: column; gap: 4px; color: var(--text-3); font-size: 12px; overflow-wrap: anywhere; }
.sentiment-event-card__time time { color: var(--accent); font-size: 16px; font-weight: 760; }
.sentiment-event-card__event, .sentiment-event-card__mapping { min-width: 0; }
.sentiment-event-card__title { display: flex; min-width: 0; align-items: flex-start; gap: 7px; }
.sentiment-event-card__title h3 { min-width: 0; margin: 0; font-size: 16px; line-height: 1.5; }
.sentiment-kind { flex: 0 0 auto; padding: 2px 6px; border: 1px solid currentColor; border-radius: 4px; color: var(--text-3); font-size: 12px; font-weight: 720; }
.sentiment-kind.is-direct, .sentiment-kind.is-related { color: var(--info); }
.sentiment-kind.is-candidate { color: var(--warning); }
.sentiment-kind.is-unavailable { color: var(--danger); }
.sentiment-event-card__summary { display: -webkit-box; margin: 6px 0 0; overflow: hidden; color: var(--text-2); font-size: 14px; line-height: 1.55; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.sentiment-event-card__mapping { display: flex; align-content: flex-start; flex-wrap: wrap; gap: 5px; }
.mapping-chip { display: inline-flex; max-width: 100%; align-items: baseline; gap: 5px; padding: 4px 6px; border: 1px solid var(--border); border-radius: 4px; background: var(--surface-2); font-size: 12px; overflow-wrap: anywhere; }
.mapping-chip small { color: var(--text-3); font-size: 12px; }
.mapping-chip strong { color: var(--text); }
.sentiment-missing { color: var(--text-3); font-size: 13px; line-height: 1.5; }
.sentiment-event-card__state { display: flex; min-width: 0; flex-direction: column; align-items: flex-start; gap: 4px; }
.observation-badge { padding: 3px 7px; border: 1px solid currentColor; border-radius: 4px; color: var(--text-2); font-size: 12px; font-weight: 740; }
.observation-badge.is-waiting { color: var(--warning); }
.observation-badge.is-info { color: var(--info); }
.observation-badge.is-complete { color: var(--info); }
.sentiment-event-card__state small { color: var(--text-3); font-size: 12px; line-height: 1.45; }
.sentiment-event-card__actions { grid-column: 2 / -1; display: flex; min-width: 0; align-items: center; flex-wrap: wrap; gap: 8px; }
.sentiment-event-toggle, .sentiment-event-archive { min-height: 44px; padding: 0 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text-2); cursor: pointer; font-weight: 680; }
.sentiment-event-toggle:hover, .sentiment-event-archive:hover { border-color: var(--border-strong); color: var(--text); }
.sentiment-event-archive { border-color: color-mix(in srgb, var(--accent) 48%, var(--border)); color: var(--accent); }
.sentiment-event-archive:disabled { cursor: default; opacity: .68; }
.sentiment-event-archive__message { min-height: 20px; flex: 1 1 260px; color: var(--info); font-size: 12px; line-height: 1.5; }
.sentiment-event-archive__message.is-error { color: var(--danger); }
.sentiment-event-details { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, .85fr); gap: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
.sentiment-event-details > section { min-width: 0; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.sentiment-event-details > section:first-child { grid-column: 1 / -1; }
.sentiment-event-details h4 { margin: 0 0 8px; font-size: 15px; }
.sentiment-event-content { margin: 0; color: var(--text-2); line-height: 1.75; white-space: pre-wrap; }
.sentiment-detail-list, .sentiment-source-item dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; margin: 0; }
.sentiment-detail-list div, .sentiment-source-item dl div { min-width: 0; }
.sentiment-detail-list dt, .sentiment-source-list dt { color: var(--text-3); font-size: 12px; }
.sentiment-detail-list dd, .sentiment-source-list dd { margin: 2px 0 0; overflow-wrap: anywhere; font-size: 13px; font-weight: 650; }
.sentiment-observation-facts { display: flex; flex-wrap: wrap; gap: 5px 10px; margin-top: 10px; padding: 8px; border: 1px solid var(--border); border-radius: 4px; }
.sentiment-observation-facts p { width: 100%; margin: 0; color: var(--text-3); font-size: 12px; }
.sentiment-observation-facts span { font-size: 13px; font-variant-numeric: tabular-nums; }
.sentiment-coverage-note, .sentiment-causality-note, .sentiment-evidence-summary { margin: 9px 0 0; color: var(--warning); font-size: 12px; line-height: 1.55; }
.sentiment-causality-note, .sentiment-evidence-summary { color: var(--text-3); }
.sentiment-checkpoints { display: grid; gap: 4px; margin: 9px 0 0; padding: 0; list-style: none; }
.sentiment-checkpoints li { display: flex; justify-content: space-between; gap: 8px; padding: 5px 7px; border: 1px solid var(--border); border-radius: 4px; font-size: 12px; }
.sentiment-checkpoints span { color: var(--text-3); }
.sentiment-source-list { display: grid; gap: 7px; margin-top: 8px; }
.sentiment-source-item { min-width: 0; padding: 9px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface); }
.sentiment-source-list header { display: flex; justify-content: space-between; gap: 8px; }
.sentiment-source-list header span { color: var(--info); font-size: 12px; }
.sentiment-source-item dl { margin-top: 7px; }
.sentiment-source-list a { display: inline-flex; min-height: 44px; align-items: center; margin-top: 5px; color: var(--info); font-weight: 700; }
.sentiment-source-missing { display: block; margin-top: 8px; color: var(--text-3); font-size: 12px; }
.sentiment-source-item > p { margin: 5px 0 0; color: var(--warning); font-size: 12px; }
@media (max-width: 1180px) {
  .sentiment-event-card { grid-template-columns: 96px minmax(0, 1fr) minmax(220px, .8fr); }
  .sentiment-event-card__state { grid-column: 3; }
  .sentiment-event-card__actions { grid-column: 2 / -1; }
}
@media (max-width: 900px) {
  .sentiment-event-card { grid-template-columns: minmax(0, 1fr); padding: 12px; }
  .sentiment-event-card__time { flex-direction: row; flex-wrap: wrap; align-items: baseline; gap: 5px 9px; }
  .sentiment-event-card__state, .sentiment-event-card__actions, .sentiment-event-details { grid-column: 1; }
  .sentiment-event-details { grid-template-columns: minmax(0, 1fr); }
  .sentiment-event-details > section:first-child { grid-column: 1; }
  .sentiment-detail-list, .sentiment-source-item dl { grid-template-columns: minmax(0, 1fr); }
  .sentiment-event-card__actions { align-items: stretch; flex-direction: column; }
  .sentiment-event-toggle, .sentiment-event-archive { width: 100%; }
  .sentiment-event-archive__message { min-height: 0; flex-basis: auto; }
}
</style>
