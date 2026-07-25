<script setup lang="ts">
import { computed } from 'vue'
import type { TomorrowWatchItem } from '../types/tomorrow'

const props = defineProps<{ items?: TomorrowWatchItem[] | null; error?: string; refreshing?: boolean }>()
defineEmits<{ retry: [] }>()
const sortedItems = computed(() => [...(props.items || [])].sort((a, b) => String(a.grade || 'Z').localeCompare(String(b.grade || 'Z'))))
function formatPrice(value?: number | null) { return value == null ? '—' : `${value.toFixed(2)} 元` }
function formatChange(change_pct?: number | null) { return change_pct == null ? '涨跌缺失' : `${change_pct > 0 ? '+' : ''}${change_pct.toFixed(2)}%` }
function formatZone(item: TomorrowWatchItem) {
  return item.target_low == null || item.target_high == null ? '区间缺失' : `${item.target_low.toFixed(2)}–${item.target_high.toFixed(2)} 元`
}
function rememberStock(item: TomorrowWatchItem) {
  if (item.code) sessionStorage.setItem('wudi-diagnosis-code', item.code)
}
</script>

<template>
  <section id="tomorrow-watchlist" class="panel tomorrow-evidence-card" aria-labelledby="tomorrow-watchlist-title" tabindex="-1">
    <header class="tomorrow-card-header">
      <div><p class="eyebrow">SERVER WATCHLIST</p><h3 id="tomorrow-watchlist-title">明日观察池与价格条件</h3></div>
      <a href="#/risk?tab=watchlist">管理观察池</a>
    </header>
    <p class="tomorrow-card-note">报价与目标区间单位均为人民币元；涨跌幅单位为百分比。评级、状态与区间沿用服务端结论。</p>

    <div v-if="error" class="tomorrow-local-state is-error" role="alert">
      <div><strong>明日观察池暂不可用</strong><span>{{ error }}</span></div>
      <button type="button" class="secondary-button" :disabled="refreshing" @click="$emit('retry')">{{ refreshing ? '重试中' : '重试此模块' }}</button>
    </div>
    <div v-else-if="sortedItems.length" class="tomorrow-watchlist-list">
      <article v-for="item in sortedItems" :key="item.code || item.name" class="tomorrow-watchlist-item">
        <div class="tomorrow-watchlist-item__identity">
          <a href="#/research?tab=diagnosis" @click="rememberStock(item)">
            <strong>{{ item.name || '名称缺失' }}</strong><span class="mono">{{ item.code || '代码缺失' }}</span>
          </a>
          <span class="tomorrow-grade">{{ item.grade || '评级缺失' }}</span>
        </div>
        <dl>
          <div><dt>最新价</dt><dd class="mono">{{ formatPrice(item.quote?.price) }}</dd></div>
          <div><dt>涨跌</dt><dd class="mono">{{ formatChange(item.quote?.change_pct) }}</dd></div>
          <div><dt>理想区间</dt><dd class="mono">{{ formatZone(item) }}</dd></div>
          <div><dt>服务端状态</dt><dd>{{ item.status || '状态缺失' }}</dd></div>
        </dl>
        <p v-if="item.quote?.stale" class="tomorrow-stale">陈旧报价 · {{ item.quote.as_of || '时点缺失' }}</p>
        <p v-if="item.target_source" class="tomorrow-source">区间来源：{{ item.target_source }}</p>
        <p v-if="item.missing?.length" class="tomorrow-missing">缺失：{{ item.missing.join('；') }}</p>
      </article>
    </div>
    <div v-else class="tomorrow-local-state is-empty">
      <strong>观察池返回真实空数组</strong><span>页面不会用示例股票或零值补位。</span>
    </div>
  </section>
</template>

<style scoped>
.tomorrow-evidence-card { min-width: 0; overflow: hidden; }
.tomorrow-card-header { display: flex; min-height: 68px; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.tomorrow-card-header h3 { margin: 1px 0 0; font-size: 18px; }
.tomorrow-card-header a { display: inline-flex; min-height: 44px; align-items: center; color: var(--accent); }
.tomorrow-card-note { margin: 0; padding: 8px 16px; border-bottom: 1px solid var(--border); color: var(--text-3); font-size: 12px; }
.tomorrow-watchlist-list { display: grid; }
.tomorrow-watchlist-item { min-width: 0; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.tomorrow-watchlist-item:last-child { border-bottom: 0; }
.tomorrow-watchlist-item__identity { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tomorrow-watchlist-item__identity a { display: flex; min-width: 0; min-height: 44px; flex-direction: column; justify-content: center; color: var(--text); text-decoration: none; }
.tomorrow-watchlist-item__identity a:hover { color: var(--accent); }
.tomorrow-watchlist-item__identity a span { color: var(--text-3); font-size: 12px; }
.tomorrow-grade { display: inline-flex; min-height: 28px; align-items: center; padding: 3px 8px; border: 1px solid var(--border-strong); border-radius: 999px; font-size: 12px; font-weight: 700; }
dl { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; margin: 8px 0 0; }
dl div { min-width: 0; padding: 7px 8px; background: var(--surface-2); }
dt { color: var(--text-3); font-size: 12px; }
dd { margin: 2px 0 0; overflow-wrap: anywhere; }
.tomorrow-stale, .tomorrow-source, .tomorrow-missing { margin: 7px 0 0; color: var(--text-3); font-size: 12px; }
.tomorrow-stale { color: var(--warning); }
.tomorrow-missing { color: var(--danger); }
.tomorrow-local-state { display: flex; min-height: 164px; align-items: center; justify-content: center; gap: 16px; padding: 20px; text-align: center; }
.tomorrow-local-state > div, .tomorrow-local-state.is-empty { flex-direction: column; }
.tomorrow-local-state strong, .tomorrow-local-state span { display: block; }
.tomorrow-local-state span { margin-top: 4px; color: var(--text-3); }
@media (max-width: 720px) { dl { grid-template-columns: repeat(2, minmax(0, 1fr)); } .tomorrow-card-header { align-items: flex-start; } }
@media (max-width: 520px) { dl { grid-template-columns: minmax(0, 1fr); } .tomorrow-local-state.is-error { align-items: stretch; flex-direction: column; } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
