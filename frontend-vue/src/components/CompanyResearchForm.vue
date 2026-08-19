<script setup lang="ts">
import StockAutocomplete from './StockAutocomplete.vue'

const model = defineModel<string>({ required: true })
defineProps<{
  running: boolean
  busy: boolean
  statusLoading: boolean
  modelReady: boolean
  modelReason: string
  error: string
}>()
const emit = defineEmits<{ submit: []; stop: [] }>()
</script>

<template>
  <form class="company-form" aria-labelledby="company-form-title" @submit.prevent="emit('submit')">
    <div class="company-form__copy">
      <p class="eyebrow">唯一主动作</p>
      <h2 id="company-form-title">核验公司并生成研究</h2>
      <p>可按代码、中文简称、全拼或拼音首字母选择股票。官方证据与模型正文独立请求，任一接口失败不会拖垮其他区域。</p>
    </div>
    <div class="company-form__controls">
      <label for="company-research-code">股票代码</label>
      <div>
        <StockAutocomplete
          v-model="model"
          class="company-code-input mono"
          input-id="company-research-code"
          name="code"
          placeholder="代码 / 中文 / 拼音 / 首字母"
          :aria-invalid="Boolean(error)"
          aria-describedby="company-code-help company-code-error"
          :disabled="running"
        />
        <button v-if="running" class="danger-button" type="button" @click="emit('stop')">停止生成</button>
        <button v-else class="primary-button" type="submit" :disabled="busy">
          {{ busy ? '正在核验' : '生成公司研究' }}
        </button>
      </div>
      <p id="company-code-help">中文或拼音需明确选择候选；完整六位代码可直接进入真实接口核验。</p>
      <p v-if="error" id="company-code-error" class="company-field-error" role="alert">{{ error }}</p>
      <p class="company-model-state" :class="{ 'is-ready': modelReady, 'is-warning': !modelReady }" role="status" aria-live="polite">
        <span aria-hidden="true"></span>{{ statusLoading ? '正在检查 AI 研究连接…' : modelReason }}
      </p>
    </div>
  </form>
</template>

<style scoped>
.company-form { display: grid; grid-template-columns: minmax(260px, .72fr) minmax(420px, 1.28fr); gap: 20px; padding: 14px 16px; }.company-form__copy h2 { margin: 0; font-size: 20px; }.company-form__copy > p:last-child { max-width: 620px; margin: 6px 0 0; color: var(--text-2); font-size: 14px; line-height: 1.6; }.company-form__controls { min-width: 0; }.company-form__controls > label { display: block; margin-bottom: 5px; color: var(--text-2); font-size: 13px; font-weight: 720; }.company-form__controls > div { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 8px; }.company-code-input { min-width: 0; }.company-code-input :deep(input) { border-color: var(--border-strong); font-size: 16px; }.company-form__controls > p { margin: 5px 0 0; color: var(--text-3); font-size: 12px; }.company-field-error { color: var(--danger) !important; font-weight: 700; }.company-model-state { display: flex; align-items: center; gap: 7px; min-height: 24px; }.company-model-state > span { width: 8px; height: 8px; flex: 0 0 auto; border-radius: 50%; background: currentColor; }.company-model-state.is-ready { color: var(--info); }.company-model-state.is-warning { color: var(--warning); }
.company-form__controls .primary-button:hover { background: var(--accent); }
@media (max-width: 920px) { .company-form { grid-template-columns: minmax(0, 1fr); gap: 12px; } }
@media (max-width: 560px) { .company-form { padding: 12px; }.company-form__controls > div { grid-template-columns: minmax(0, 1fr); }.company-form__controls button { width: 100%; } }
</style>
