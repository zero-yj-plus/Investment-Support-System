<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { WatchlistInput, WatchlistItem } from '../types/watchlist'
import type { StockSearchItem } from '../types/stockSearch'
import { finiteNumber } from '../utils/watchlistFormat'
import StockAutocomplete from './StockAutocomplete.vue'

const props = defineProps<{
  item: WatchlistItem | null
  busy: boolean
  error: string
}>()

const emit = defineEmits<{
  submit: [input: WatchlistInput]
  cancel: []
}>()

function initialManual(field: 'manual_target_low' | 'manual_target_high', legacy: 'target_low' | 'target_high') {
  if (!props.item) return ''
  const source = Object.prototype.hasOwnProperty.call(props.item, field)
    ? props.item[field]
    : props.item[legacy]
  return source == null ? '' : String(source)
}

const code = ref(String(props.item?.code || ''))
const name = ref(String(props.item?.name || ''))
const sector = ref(String(props.item?.sector || ''))
const reason = ref(String(props.item?.add_reason || ''))
const targetLow = ref(initialManual('manual_target_low', 'target_low'))
const targetHigh = ref(initialManual('manual_target_high', 'target_high'))
const triggerCondition = ref(String(props.item?.trigger_condition || ''))
const noBuyCondition = ref(String(props.item?.no_buy_condition || ''))
const abandonCondition = ref(String(props.item?.abandon_condition || ''))
const abandonPrice = ref(props.item?.abandon_price == null ? '' : String(props.item.abandon_price))
const note = ref(String(props.item?.note || ''))
const localError = ref('')
const codeInput = ref<InstanceType<typeof StockAutocomplete> | null>(null)
const dialog = ref<HTMLDialogElement | null>(null)

function optionalPositive(raw: string, label: string): { value: number | null; error: string } {
  if (!raw.trim()) return { value: null, error: '' }
  const value = finiteNumber(raw)
  if (value == null || value <= 0) return { value: null, error: `${label}必须是大于 0 的有效金额，或留空。` }
  return { value, error: '' }
}

function submit() {
  localError.value = ''
  const normalizedCode = code.value.replace(/\D/g, '')
  if (!/^\d{6}$/.test(normalizedCode)) {
    localError.value = '股票代码必须是 6 位数字。'
    void nextTick(() => codeInput.value?.focus())
    return
  }
  const low = optionalPositive(targetLow.value, '买入区间下沿')
  const high = optionalPositive(targetHigh.value, '买入区间上沿')
  const abandon = optionalPositive(abandonPrice.value, '放弃价')
  const validation = low.error || high.error || abandon.error
  if (validation) {
    localError.value = validation
    return
  }
  if (low.value != null && high.value != null && low.value > high.value) {
    localError.value = '买入区间下沿不能高于上沿。'
    return
  }
  emit('submit', {
    code: normalizedCode,
    name: name.value.trim(),
    sector: sector.value.trim(),
    add_reason: reason.value.trim(),
    target_low: low.value,
    target_high: high.value,
    trigger_condition: triggerCondition.value.trim(),
    no_buy_condition: noBuyCondition.value.trim(),
    abandon_condition: abandonCondition.value.trim(),
    abandon_price: abandon.value,
    note: note.value.trim(),
  })
}

function requestClose() {
  if (props.busy) return
  dialog.value?.close()
  emit('cancel')
}

function selectStock(item: StockSearchItem) {
  if (!props.item?.id) name.value = item.name
}

function handleBackdrop(event: MouseEvent) {
  if (event.target === dialog.value) requestClose()
}

onMounted(() => {
  dialog.value?.showModal()
  void nextTick(() => codeInput.value?.focus())
})

onBeforeUnmount(() => {
  if (dialog.value?.open) dialog.value.close()
})
</script>

<template>
  <dialog
    ref="dialog"
    class="watchlist-editor"
    aria-labelledby="watchlist-editor-title"
    aria-describedby="watchlist-editor-description"
    @cancel.prevent="requestClose"
    @click="handleBackdrop"
  >
    <header class="watchlist-editor__header">
      <div>
        <p class="eyebrow">{{ item ? '编辑用户条件' : '真实代码核验' }}</p>
        <h2 id="watchlist-editor-title">{{ item ? '编辑观察项' : '添加观察项' }}</h2>
      </div>
      <button type="button" class="secondary-button" :disabled="busy" @click="requestClose">取消</button>
    </header>

    <form @submit.prevent="submit">
      <p id="watchlist-editor-description" class="watchlist-editor__description">
        风险边界等级由服务端计算，本表单不采集、不覆盖该字段。
      </p>
      <div class="watchlist-editor__grid">
        <div class="watchlist-editor__stock-field">
          <StockAutocomplete
            ref="codeInput"
            v-model="code"
            input-id="watchlist-stock-code"
            label="股票代码"
            placeholder="代码 / 中文 / 拼音 / 首字母"
            required
            :readonly="Boolean(item?.id)"
            :display-name="name"
            :aria-invalid="Boolean(localError && !/^\d{6}$/.test(code))"
            aria-describedby="watchlist-code-hint"
            @selected="selectStock"
          />
          <small id="watchlist-code-hint">新增时通过真实单票接口核验；编辑时不改变代码。</small>
        </div>
        <label>
          <span>名称（可选）</span>
          <input v-model="name" maxlength="30" autocomplete="off" />
          <small>新增核验取得名称时，以真实核验结果优先。</small>
        </label>
        <label>
          <span>所属方向 / 板块</span>
          <input v-model="sector" maxlength="30" autocomplete="off" />
          <small>仅保存用户录入，不推断行业归属。</small>
        </label>
        <label class="watchlist-editor__half">
          <span>加入理由</span>
          <textarea v-model="reason" maxlength="500" rows="2"></textarea>
        </label>
        <label class="watchlist-editor__half">
          <span>备注（可选）</span>
          <textarea v-model="note" maxlength="300" rows="2"></textarea>
        </label>

        <fieldset class="watchlist-editor__two-thirds watchlist-editor__band">
          <legend>手工理想买入区间（元，可选）</legend>
          <label>
            <span>下沿</span>
            <input v-model="targetLow" inputmode="decimal" autocomplete="off" />
          </label>
          <span aria-hidden="true">—</span>
          <label>
            <span>上沿</span>
            <input v-model="targetHigh" inputmode="decimal" autocomplete="off" />
          </label>
          <small>两端均留空时采用服务端自动超短线区间；填写任一端即启用手工覆盖。</small>
        </fieldset>

        <label class="watchlist-editor__half">
          <span>买入触发条件</span>
          <textarea v-model="triggerCondition" maxlength="500" rows="2"></textarea>
        </label>
        <label class="watchlist-editor__half">
          <span>不买条件</span>
          <textarea v-model="noBuyCondition" maxlength="500" rows="2"></textarea>
        </label>
        <label>
          <span>放弃价（元，可选）</span>
          <input v-model="abandonPrice" inputmode="decimal" autocomplete="off" />
          <small>缺失保持为空，不以区间端点替代。</small>
        </label>
        <label>
          <span>放弃条件（可选）</span>
          <input v-model="abandonCondition" maxlength="300" autocomplete="off" />
        </label>
      </div>

      <p v-if="localError || error" class="watchlist-editor__error" role="alert">{{ localError || error }}</p>
      <footer>
        <p>{{ item ? '保存后服务端重新排队计算区间。' : '核验成功后才写入观察池；暂缺报价不会被补成 0。' }}</p>
        <button type="submit" class="primary-button" :disabled="busy">
          {{ busy ? (item ? '保存中' : '核验并保存中') : (item ? '保存修改' : '核验并加入') }}
        </button>
      </footer>
    </form>
  </dialog>
</template>

<style scoped>
.watchlist-editor { width: min(960px, calc(100vw - 32px)); max-width: none; max-height: calc(100dvh - 32px); margin: auto; padding: 0; overflow: auto; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); color: var(--text); }
.watchlist-editor::backdrop { background: rgb(0 0 0 / 56%); }
.watchlist-editor__header { position: sticky; z-index: 1; top: 0; display: flex; min-height: 68px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--border); background: var(--surface); }
.watchlist-editor h2 { margin: 0; font-size: 18px; }
.watchlist-editor form { padding: 14px; }
.watchlist-editor__description { margin: 0 0 12px; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text-2); font-size: 13px; }
.watchlist-editor__grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px 12px; }
.watchlist-editor__grid > label, .watchlist-editor__stock-field { grid-column: span 2; }
.watchlist-editor label, .watchlist-editor__stock-field { display: grid; min-width: 0; align-content: start; gap: 5px; color: var(--text-2); font-size: 13px; font-weight: 680; }
.watchlist-editor label b { color: var(--danger); }
.watchlist-editor input, .watchlist-editor textarea { width: 100%; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); }
.watchlist-editor textarea { min-height: 48px; resize: vertical; }
.watchlist-editor input:focus, .watchlist-editor textarea:focus { border-color: var(--focus); }
.watchlist-editor input[readonly] { color: var(--text-3); cursor: not-allowed; }
.watchlist-editor small { color: var(--text-3); font-size: 12px; font-weight: 500; }
.watchlist-editor__wide { grid-column: 1 / -1; }
.watchlist-editor__half { grid-column: span 3 !important; }
.watchlist-editor__two-thirds { grid-column: span 4; }
.watchlist-editor__band { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: end; gap: 8px; min-width: 0; margin: 0; padding: 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); }
.watchlist-editor__band legend { padding: 0 5px; color: var(--text-2); font-size: 13px; font-weight: 680; }
.watchlist-editor__band > small { grid-column: 1 / -1; }
.watchlist-editor footer { position: sticky; z-index: 1; bottom: -14px; display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 12px -14px -14px; padding: 10px 14px; border-top: 1px solid var(--border); background: var(--surface); }
.watchlist-editor footer p { margin: 0; color: var(--text-3); font-size: 13px; }
.watchlist-editor__error { margin: 10px 0 0; color: var(--danger); }
@media (max-width: 720px) {
  .watchlist-editor { width: calc(100vw - 16px); max-height: calc(100dvh - 16px); font-size: 18px; }
  .watchlist-editor__grid { grid-template-columns: minmax(0, 1fr); }
  .watchlist-editor__grid > label, .watchlist-editor__stock-field, .watchlist-editor__wide, .watchlist-editor__half, .watchlist-editor__two-thirds { grid-column: auto !important; }
}
@media (max-width: 520px) {
  .watchlist-editor__band { grid-template-columns: minmax(0, 1fr); align-items: stretch; }
  .watchlist-editor__band > span { display: none; }
  .watchlist-editor footer { align-items: stretch; flex-direction: column; }
  .watchlist-editor footer .primary-button { width: 100%; }
}
</style>
