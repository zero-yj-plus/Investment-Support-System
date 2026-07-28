<script setup lang="ts">
const props = defineProps<{
  modelValue: string
  running: boolean
  disabled?: boolean
  error?: string
}>()
const emit = defineEmits<{
  'update:modelValue': [value: string]
  submit: []
  stop: []
}>()
</script>

<template>
  <form class="chain-form" aria-labelledby="chain-form-title" @submit.prevent="emit('submit')">
    <div class="chain-form__copy">
      <p class="eyebrow">唯一主动作</p>
      <h2 id="chain-form-title">选择要核验的产业链</h2>
      <p>先验证板块与真实成员，再拆需求、利润池、卡点和兑现证据。产业逻辑不会自动变成买点。</p>
    </div>
    <div class="chain-form__controls">
      <label for="industry-chain-input">行业或产业链名称</label>
      <div>
        <input
          id="industry-chain-input"
          :value="props.modelValue"
          type="text"
          maxlength="120"
          autocomplete="off"
          placeholder="例如：光模块"
          :aria-invalid="Boolean(error)"
          :aria-describedby="error ? 'industry-chain-input-error' : 'industry-chain-input-help'"
          :disabled="running || disabled"
          @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
        />
        <button v-if="!running" class="primary-button" type="submit" :disabled="disabled">开始研究</button>
        <button v-else class="danger-button" type="button" @click="emit('stop')">停止研究</button>
      </div>
      <small id="industry-chain-input-help">仅提交用户输入的产业主题，不自动触发其他研究页面接口。</small>
      <p v-if="error" id="industry-chain-input-error" class="chain-form__error" role="alert">{{ error }}</p>
    </div>
  </form>
</template>

<style scoped>
.chain-form { display: grid; grid-template-columns: minmax(260px, .72fr) minmax(360px, 1.28fr); gap: 20px; padding: 16px; }
.chain-form__copy { min-width: 0; }.chain-form__copy h2 { margin: 0; font-size: 20px; }.chain-form__copy > p:last-child { margin: 7px 0 0; color: var(--text-2); line-height: 1.65; }
.chain-form__controls { display: grid; align-content: start; gap: 6px; min-width: 0; }.chain-form__controls label { color: var(--text-2); font-size: 13px; font-weight: 720; }.chain-form__controls > div { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }.chain-form__controls input { min-width: 0; min-height: 44px; padding: 8px 11px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); }.chain-form__controls input::placeholder { color: var(--text-3); }.chain-form__controls input[aria-invalid="true"] { border-color: var(--danger); }.chain-form__controls button { min-width: 116px; }.chain-form__controls small { color: var(--text-3); font-size: 12px; }.chain-form__error { margin: 0; color: var(--danger); font-size: 13px; }
.chain-form__controls .primary-button:hover { background: var(--accent); }
@media (max-width: 900px) { .chain-form { grid-template-columns: minmax(0, 1fr); gap: 14px; } }
@media (max-width: 560px) { .chain-form { padding: 13px; }.chain-form__controls > div { grid-template-columns: minmax(0, 1fr); }.chain-form__controls button { width: 100%; } }
</style>
