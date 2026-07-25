<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import SentimentEventCard from './SentimentEventCard.vue'
import type { SentimentEvent, SentimentEventFilter } from '../types/sentimentEvents'
import { eventMatchesFilter, eventTitle } from '../utils/sentimentFormat'

const props = defineProps<{ events: SentimentEvent[] }>()
const selectedFilter = ref<SentimentEventFilter>('related')
const query = ref('')
const shown = ref(24)
const pageSize = 24

const filters: Array<{ key: SentimentEventFilter; label: string }> = [
  { key: 'related', label: 'A 股相关' },
  { key: 'direct', label: '公司点名' },
  { key: 'clues', label: '行业 / 主题线索' },
  { key: 'all', label: '全部事件' },
]

function countFor(filter: SentimentEventFilter) {
  return props.events.filter((event) => eventMatchesFilter(event, filter)).length
}

const filtered = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase('zh-CN')
  return props.events.filter((event) => {
    if (!eventMatchesFilter(event, selectedFilter.value)) return false
    if (!keyword) return true
    const mappingText = [
      ...(event.a_share?.matches || []),
      ...(event.a_share?.topic_candidates || []),
    ].map((item) => `${item.name || ''} ${item.code || ''}`).join(' ')
    return `${eventTitle(event)} ${event.content || ''} ${mappingText}`.toLocaleLowerCase('zh-CN').includes(keyword)
  })
})
const visibleEvents = computed(() => filtered.value.slice(0, shown.value))

watch([selectedFilter, query], () => { shown.value = pageSize })
watch(() => props.events, () => {
  if (selectedFilter.value !== 'all' && countFor(selectedFilter.value) === 0 && props.events.length) selectedFilter.value = 'all'
})

function chooseFilter(filter: SentimentEventFilter) {
  selectedFilter.value = filter
}
</script>

<template>
  <section class="sentiment-feed" aria-labelledby="sentiment-feed-title">
    <header class="panel sentiment-feed__toolbar">
      <div>
        <p class="eyebrow">结构化证据</p>
        <h2 id="sentiment-feed-title">事件流</h2>
        <p>先读事件，再看关联 A 股，最后看盘面是否已留痕。来源证据收在每条事件详情中。</p>
      </div>
      <label class="sentiment-search">
        <span>搜索事件或映射实体</span>
        <input v-model="query" type="search" autocomplete="off" placeholder="输入公司、行业、主题或事件词" />
      </label>
      <div class="sentiment-filters" role="group" aria-label="新闻范围筛选">
        <button
          v-for="filter in filters"
          :key="filter.key"
          type="button"
          :aria-pressed="selectedFilter === filter.key"
          :class="{ 'is-active': selectedFilter === filter.key }"
          @click="chooseFilter(filter.key)"
        >{{ filter.label }} <span class="mono">{{ countFor(filter.key) }}</span></button>
      </div>
    </header>

    <div v-if="visibleEvents.length" class="sentiment-feed__list">
      <SentimentEventCard
        v-for="(event, index) in visibleEvents"
        :key="event.event_id || `${event.published_at || event.time || 'event'}-${index}`"
        :event="event"
        :index="index"
      />
    </div>

    <section v-else class="panel sentiment-filter-empty" aria-labelledby="sentiment-filter-empty-title">
      <p class="eyebrow">当前筛选无结果</p>
      <h3 id="sentiment-filter-empty-title">没有匹配的事件</h3>
      <p>这只表示当前筛选或搜索词没有命中，不代表接口失败，也不代表市场没有事件。</p>
      <button v-if="query || selectedFilter !== 'all'" type="button" class="secondary-button" @click="query = ''; selectedFilter = 'all'">查看全部事件</button>
    </section>

    <button
      v-if="filtered.length > shown"
      type="button"
      class="secondary-button sentiment-load-more"
      @click="shown += pageSize"
    >加载更多（剩 {{ filtered.length - shown }} 条）</button>
  </section>
</template>

<style scoped>
.sentiment-feed { display: grid; min-width: 0; gap: 8px; }
.sentiment-feed__toolbar { display: grid; grid-template-columns: minmax(280px, 1fr) minmax(260px, 380px); gap: 10px 18px; padding: 14px 16px; }
.sentiment-feed__toolbar h2 { margin: 0; font-size: 20px; }
.sentiment-feed__toolbar > div:first-child > p:last-child { margin: 5px 0 0; color: var(--text-3); font-size: 13px; }
.sentiment-search { display: grid; gap: 4px; align-self: center; color: var(--text-3); font-size: 12px; }
.sentiment-search input { width: 100%; min-height: 44px; padding: 0 11px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); }
.sentiment-filters { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 6px; padding-top: 10px; border-top: 1px solid var(--border); }
.sentiment-filters button { min-height: 44px; padding: 0 11px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text-2); cursor: pointer; }
.sentiment-filters button:hover { border-color: var(--border-strong); color: var(--text); }
.sentiment-filters button.is-active { border-color: var(--accent); color: var(--accent); font-weight: 740; }
.sentiment-filters button span { margin-left: 3px; }
.sentiment-feed__list { display: grid; min-width: 0; gap: 7px; }
.sentiment-filter-empty { display: flex; min-height: 230px; flex-direction: column; align-items: center; justify-content: center; padding: 22px; text-align: center; }
.sentiment-filter-empty h3 { margin: 0; font-size: 20px; }
.sentiment-filter-empty > p:not(.eyebrow) { max-width: 620px; margin: 7px 0 16px; color: var(--text-3); }
.sentiment-load-more { width: 100%; justify-content: center; }
@media (max-width: 900px) {
  .sentiment-feed__toolbar { grid-template-columns: minmax(0, 1fr); padding: 12px; }
  .sentiment-filters { grid-column: 1; }
  .sentiment-filters button { flex: 1 1 150px; }
}
</style>
