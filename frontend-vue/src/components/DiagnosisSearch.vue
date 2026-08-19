<script setup lang="ts">
import { computed } from 'vue'
import StockAutocomplete from './StockAutocomplete.vue'

const props = defineProps<{
  code: string
  busy: boolean
  error?: string
  recentCodes: string[]
}>()

const emit = defineEmits<{
  'update:code': [value: string]
  submit: [code: string]
}>()

const codeModel = computed({
  get: () => props.code,
  set: (value: string) => emit('update:code', value),
})
</script>

<template>
  <section class="panel diagnosis-search" aria-labelledby="diagnosis-search-title">
    <form @submit.prevent="emit('submit', code)">
      <div class="diagnosis-search__field">
        <label id="diagnosis-search-title" for="diagnosis-code">A 股代码</label>
        <div class="diagnosis-search__controls">
          <StockAutocomplete
            v-model="codeModel"
            input-id="diagnosis-code"
            name="stock-code"
            placeholder="代码 / 中文 / 拼音 / 首字母"
            aria-describedby="diagnosis-code-hint diagnosis-code-error"
            :aria-invalid="Boolean(error)"
          />
          <button class="primary-button diagnosis-search__submit" type="submit" :disabled="busy">
            {{ busy ? '诊断中…' : '开始诊断' }}
          </button>
        </div>
      </div>
      <p id="diagnosis-code-hint">从本地名录选择；完整六位代码可直接核验，动作只认服务端诊断卡。</p>
      <p v-if="error" id="diagnosis-code-error" class="diagnosis-search__error" aria-live="polite">{{ error }}</p>
    </form>

    <div v-if="recentCodes.length" class="diagnosis-recent" aria-label="最近诊断股票">
      <span>最近诊断</span>
      <button
        v-for="recent in recentCodes"
        :key="recent"
        type="button"
        class="diagnosis-recent__code mono"
        :disabled="busy"
        @click="emit('submit', recent)"
      >{{ recent }}</button>
    </div>
  </section>
</template>

<style scoped>
.diagnosis-search { display: grid; grid-template-columns: minmax(360px, 1fr) auto; align-items: end; gap: 12px 18px; padding: 10px 12px; }
.diagnosis-search form { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: 3px 12px; }
.diagnosis-search__field { display: grid; gap: 4px; }
.diagnosis-search__controls { display: grid; grid-template-columns: minmax(180px, 250px) auto; gap: 8px; }
.diagnosis-search label { font-size: 13px; font-weight: 750; }
.diagnosis-search__submit { min-width: 126px; }
.diagnosis-search form > p { align-self: end; margin: 0 0 2px; color: var(--text-3); font-size: 12px; }
.diagnosis-search__error { grid-column: 1 / -1; color: var(--danger) !important; }
.diagnosis-recent { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding-left: 16px; border-left: 1px solid var(--border); }
.diagnosis-recent > span { color: var(--text-3); font-size: 12px; font-weight: 700; }
.diagnosis-recent__code { min-width: 74px; min-height: 44px; padding: 7px 9px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text-2); cursor: pointer; }
.diagnosis-recent__code:hover { border-color: var(--border-strong); color: var(--text); }
.diagnosis-recent__code:disabled { cursor: wait; opacity: .58; }
@media (max-width: 900px) {
  .diagnosis-search { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-recent { padding: 8px 0 0; border-top: 1px solid var(--border); border-left: 0; }
}
@media (max-width: 560px) {
  .diagnosis-search { padding: 12px; }
  .diagnosis-search form { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-search form > p { grid-column: 1 / -1; }
  .diagnosis-search__controls { grid-template-columns: minmax(0, 1fr) 112px; }
  .diagnosis-search__submit { min-width: 0; }
  .diagnosis-recent__code { flex: 1 1 92px; }
}
</style>
