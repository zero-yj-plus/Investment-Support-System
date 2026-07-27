<script setup lang="ts">
defineProps<{
  code: string
  busy: boolean
  error?: string
  recentCodes: string[]
}>()

const emit = defineEmits<{
  'update:code': [value: string]
  submit: [code: string]
}>()

function clean(value: string) {
  emit('update:code', value.replace(/\D/g, '').slice(0, 6))
}
</script>

<template>
  <section class="panel diagnosis-search" aria-labelledby="diagnosis-search-title">
    <form @submit.prevent="emit('submit', code)">
      <div class="diagnosis-search__field">
        <label id="diagnosis-search-title" for="diagnosis-code">A 股代码</label>
        <input
          id="diagnosis-code"
          :value="code"
          name="stock-code"
          inputmode="numeric"
          autocomplete="off"
          maxlength="6"
          pattern="[0-9]{6}"
          aria-describedby="diagnosis-code-hint diagnosis-code-error"
          :aria-invalid="Boolean(error)"
          placeholder="输入 6 位代码"
          @input="clean(($event.target as HTMLInputElement).value)"
        />
      </div>
      <button class="primary-button diagnosis-search__submit" type="submit" :disabled="busy">
        {{ busy ? '诊断中…' : '开始诊断' }}
      </button>
      <p id="diagnosis-code-hint">快速报价与正式诊断并行读取；正式动作只认服务端诊断卡。</p>
      <p id="diagnosis-code-error" class="diagnosis-search__error" aria-live="polite">{{ error || '' }}</p>
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
.diagnosis-search { padding: 12px 14px; }
.diagnosis-search form { display: grid; grid-template-columns: minmax(180px, 250px) auto minmax(260px, 1fr); align-items: end; gap: 8px 12px; }
.diagnosis-search__field { display: grid; gap: 4px; }
.diagnosis-search label { font-size: 13px; font-weight: 750; }
.diagnosis-search input { width: 100%; min-height: 44px; padding: 8px 11px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); font: 700 17px/1.4 "SFMono-Regular", Consolas, monospace; letter-spacing: .12em; }
.diagnosis-search input[aria-invalid="true"] { border-color: var(--danger); }
.diagnosis-search__submit { min-width: 126px; }
.diagnosis-search form > p { align-self: center; margin: 0; color: var(--text-3); font-size: 13px; }
.diagnosis-search__error { grid-column: 1 / -1; min-height: 20px; color: var(--danger) !important; }
.diagnosis-recent { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding-top: 8px; border-top: 1px solid var(--border); }
.diagnosis-recent > span { color: var(--text-3); font-size: 12px; font-weight: 700; }
.diagnosis-recent__code { min-width: 80px; min-height: 44px; padding: 7px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text-2); cursor: pointer; }
.diagnosis-recent__code:hover { border-color: var(--border-strong); color: var(--text); }
.diagnosis-recent__code:disabled { cursor: wait; opacity: .58; }
@media (max-width: 900px) {
  .diagnosis-search form { grid-template-columns: minmax(0, 1fr) auto; }
  .diagnosis-search form > p { grid-column: 1 / -1; }
}
@media (max-width: 560px) {
  .diagnosis-search { padding: 12px; }
  .diagnosis-search form { grid-template-columns: minmax(0, 1fr); }
  .diagnosis-search__submit { width: 100%; }
  .diagnosis-recent__code { flex: 1 1 92px; }
}
</style>
