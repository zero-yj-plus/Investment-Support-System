<script setup lang="ts">
import { nextTick, ref } from 'vue'
import type { TimingReading } from '../types/timing'
import TimingRegionCard from './TimingRegionCard.vue'

defineProps<{
  reading: TimingReading | null
  loading: boolean
  error: string
  requested: boolean
  queryCode: string
}>()

const emit = defineEmits<{ query: [code: string] }>()
const code = ref('')
const validationError = ref('')
const input = ref<HTMLInputElement | null>(null)

function submit() {
  const normalized = code.value.trim()
  if (!/^\d{6}$/.test(normalized)) {
    validationError.value = '请输入 6 位数字股票代码'
    void nextTick(() => input.value?.focus())
    return
  }
  validationError.value = ''
  emit('query', normalized)
}
</script>

<template>
  <section class="panel timing-stock-panel" aria-labelledby="timing-stock-title" :aria-busy="loading">
    <header class="timing-stock-heading">
      <div>
        <p class="eyebrow">STOCK CHECK</p>
        <h2 id="timing-stock-title">个股择时核对</h2>
        <p>输入代码后读取该股前复权日线与服务端四区域结论；不会据此替代账户风险闸。</p>
      </div>
      <form class="timing-stock-form" novalidate @submit.prevent="submit">
        <label for="timing-stock-code">股票代码</label>
        <div>
          <input
            id="timing-stock-code"
            ref="input"
            v-model="code"
            name="stock-code"
            type="text"
            inputmode="numeric"
            autocomplete="off"
            maxlength="6"
            pattern="[0-9]{6}"
            placeholder="6 位股票代码"
            :aria-invalid="Boolean(validationError)"
            aria-describedby="timing-stock-help timing-stock-error"
            @input="validationError = ''"
          />
          <button class="secondary-button" type="submit" :disabled="loading">{{ loading ? '查询中' : '查询' }}</button>
        </div>
        <span id="timing-stock-help">仅支持 6 位 A 股代码</span>
        <strong v-if="validationError" id="timing-stock-error" role="alert">{{ validationError }}</strong>
      </form>
    </header>

    <div v-if="loading" class="timing-stock-loading" role="status" aria-live="polite">
      <span class="sr-only">个股择时正在加载</span>
      <span class="timing-stock-skeleton"></span>
    </div>

    <div v-else-if="error" class="timing-stock-error" role="alert">
      <strong>个股择时暂不可用</strong>
      <span>{{ error }}</span>
      <small v-if="queryCode" class="mono">查询代码 {{ queryCode }}</small>
    </div>

    <div v-else-if="!requested" class="timing-stock-idle">
      <strong>尚未查询个股</strong>
      <span>市场结论与个股结论可能不同，查询结果只适用于对应代码。</span>
    </div>

    <TimingRegionCard v-else-if="reading" class="timing-stock-result" :reading="reading" kind="stock" />

    <div v-else class="timing-stock-error" role="status">
      <strong>个股结果缺失</strong>
      <span>接口未返回该代码的个股对象，页面未使用其他标的或示例数据补位。</span>
    </div>
  </section>
</template>

<style scoped>
.timing-stock-panel { min-width: 0; overflow: hidden; }
.timing-stock-heading { display: flex; min-height: 96px; align-items: center; justify-content: space-between; gap: 20px; padding: 12px 14px; border-bottom: 1px solid var(--border); }
.timing-stock-heading > div { min-width: 0; }
.timing-stock-heading h2 { margin: 0; font-size: 18px; }
.timing-stock-heading > div > p:last-child { max-width: 680px; margin: 4px 0 0; color: var(--text-3); font-size: 13px; }
.timing-stock-form { display: grid; min-width: 310px; gap: 3px; }
.timing-stock-form > label { color: var(--text-2); font-size: 13px; font-weight: 700; }
.timing-stock-form > div { display: grid; grid-template-columns: minmax(150px, 1fr) auto; gap: 8px; }
.timing-stock-form input { width: 100%; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); font-family: "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }
.timing-stock-form input[aria-invalid="true"] { border-color: var(--danger); }
.timing-stock-form > span { color: var(--text-3); font-size: 12px; }
.timing-stock-form > strong { color: var(--danger); font-size: 12px; }
.timing-stock-loading { padding: 14px; }
.timing-stock-skeleton { position: relative; display: block; min-height: 270px; overflow: hidden; border-radius: var(--radius); background: var(--surface-2); }
.timing-stock-skeleton::after { position: absolute; inset: 0; background: color-mix(in srgb, var(--surface-3) 70%, transparent); content: ""; animation: timing-pulse 1.2s ease-in-out infinite alternate; }
.timing-stock-idle, .timing-stock-error { display: grid; min-height: 152px; align-content: center; gap: 3px; padding: 22px; color: var(--text-3); text-align: center; }
.timing-stock-idle strong, .timing-stock-error strong { color: var(--text-2); }
.timing-stock-error { color: var(--danger); }
.timing-stock-error span, .timing-stock-error small { color: var(--text-3); }
.timing-stock-result { margin: 12px; }
@keyframes timing-pulse { from { opacity: .42; } to { opacity: .78; } }
@media (max-width: 720px) {
  .timing-stock-heading { align-items: stretch; flex-direction: column; gap: 12px; }
  .timing-stock-form { min-width: 0; }
}
@media (max-width: 420px) {
  .timing-stock-form > div { grid-template-columns: minmax(0, 1fr); }
  .timing-stock-form .secondary-button { width: 100%; }
}
@media (prefers-reduced-motion: reduce) {
  .timing-stock-skeleton::after { animation: none; }
}
</style>
