<script setup lang="ts">
import { ref } from 'vue'
import type { ScreenerFilterForm } from '../types/screener'

const model = defineModel<ScreenerFilterForm>({ required: true })
defineProps<{ errors: Record<string, string>; stale: boolean }>()
const emit = defineEmits<{ submit: []; reset: []; dirty: [] }>()
const activePreset = ref('')

const boards = ['主板', '创业板', '科创板', '北交所']
const presets: Array<{ id: string; label: string; description: string; values: Partial<ScreenerFilterForm> }> = [
  { id: 'active-volume', label: '活跃放量', description: '放量且成交活跃，排除极端换手', values: { turnoverMin: '3', turnoverMax: '18', volumeRatioMin: '1.5', amountMin: '5', changeMin: '-2', changeMax: '7' } },
  { id: 'value-capacity', label: '低估值容量', description: '正盈利、低 PE / PB 与中大市值', values: { peMin: '0', peMax: '25', pbMin: '0', pbMax: '3', circMvMin: '100', amountMin: '3', excludeLoss: true } },
  { id: 'small-cap', label: '小盘弹性', description: '20–150 亿流通市值，兼顾量能', values: { turnoverMin: '4', turnoverMax: '20', volumeRatioMin: '1.2', circMvMin: '20', circMvMax: '150', amountMin: '2' } },
  { id: 'trend-not-limit', label: '趋势强势', description: '上涨但不追板，量价同步确认', values: { turnoverMin: '2', turnoverMax: '15', volumeRatioMin: '1.2', changeMin: '2', changeMax: '8', amountMin: '5' } },
  { id: 'pullback', label: '回调承接', description: '温和回撤且保持成交与换手', values: { turnoverMin: '1', turnoverMax: '10', volumeRatioMin: '1', changeMin: '-4', changeMax: '0', amountMin: '5' } },
]

function blankValues(): ScreenerFilterForm {
  return {
    peMin: '', peMax: '', pbMin: '', pbMax: '', turnoverMin: '', turnoverMax: '', volumeRatioMin: '',
    circMvMin: '', circMvMax: '', changeMin: '', changeMax: '', amountMin: '', industry: '', boards: [...boards],
    excludeSt: true, excludeLoss: false,
  }
}

function applyPreset(preset: typeof presets[number]) {
  Object.assign(model.value, blankValues(), preset.values)
  activePreset.value = preset.id
  emit('dirty')
}

function markChanged() {
  activePreset.value = ''
  emit('dirty')
}

function reset() {
  activePreset.value = ''
  emit('reset')
}
</script>

<template>
  <section class="panel screener-filters" aria-labelledby="screener-filter-title">
    <header class="screener-section-heading">
      <div><p class="eyebrow">筛选输入</p><h2 id="screener-filter-title">设置筛选条件</h2></div>
      <span>空值表示不限制 · 数字不会自动补 0</span>
    </header>

    <form id="screener-filter-form" @submit.prevent="emit('submit')" @input="markChanged" @change="markChanged">
      <div v-if="Object.keys(errors).length" class="screener-form-alert" role="alert">
        <strong>部分条件需要修正</strong><span>请按字段提示调整后重新筛选。</span>
      </div>

      <fieldset class="screener-fieldset screener-presets-fieldset">
        <legend>策略预设 · 选择后会回填真实条件，可继续调整</legend>
        <div class="screener-presets" role="group" aria-label="筛选策略预设">
          <button
            v-for="preset in presets"
            :key="preset.id"
            type="button"
            class="screener-preset"
            :class="{ 'is-active': activePreset === preset.id }"
            :aria-pressed="activePreset === preset.id"
            @click="applyPreset(preset)"
          ><strong>{{ preset.label }}</strong><span>{{ preset.description }}</span></button>
        </div>
      </fieldset>

      <fieldset class="screener-fieldset">
        <legend>常用行情条件</legend>
        <div class="screener-filter-grid">
          <div class="screener-field screener-range-field">
            <span class="screener-field-label">换手率 <small>%</small></span>
            <label for="screener-turnover-min">最小值</label>
            <input id="screener-turnover-min" v-model="model.turnoverMin" type="number" inputmode="decimal" step="any" placeholder="不限" :aria-invalid="Boolean(errors.turnoverMin)" :aria-describedby="errors.turnoverMin ? 'screener-turnover-error' : undefined" />
            <span aria-hidden="true">至</span>
            <label for="screener-turnover-max">最大值</label>
            <input id="screener-turnover-max" v-model="model.turnoverMax" type="number" inputmode="decimal" step="any" placeholder="不限" :aria-invalid="Boolean(errors.turnoverMax)" :aria-describedby="errors.turnoverMax ? 'screener-turnover-error' : undefined" />
            <strong v-if="errors.turnoverMin || errors.turnoverMax" id="screener-turnover-error" role="alert">{{ errors.turnoverMin || errors.turnoverMax }}</strong>
          </div>

          <div class="screener-field">
            <label class="screener-field-label" for="screener-volume-ratio">量比下限 <small>倍</small></label>
            <input id="screener-volume-ratio" v-model="model.volumeRatioMin" type="number" inputmode="decimal" min="0" step="any" placeholder="不限" :aria-invalid="Boolean(errors.volumeRatioMin)" :aria-describedby="errors.volumeRatioMin ? 'screener-volume-ratio-error' : undefined" />
            <strong v-if="errors.volumeRatioMin" id="screener-volume-ratio-error" role="alert">{{ errors.volumeRatioMin }}</strong>
          </div>

          <div class="screener-field screener-range-field">
            <span class="screener-field-label">流通市值 <small>亿元</small></span>
            <label for="screener-market-cap-min">最小值</label>
            <input id="screener-market-cap-min" v-model="model.circMvMin" type="number" inputmode="decimal" min="0" step="any" placeholder="不限" :aria-invalid="Boolean(errors.circMvMin)" :aria-describedby="errors.circMvMin ? 'screener-market-cap-error' : undefined" />
            <span aria-hidden="true">至</span>
            <label for="screener-market-cap-max">最大值</label>
            <input id="screener-market-cap-max" v-model="model.circMvMax" type="number" inputmode="decimal" min="0" step="any" placeholder="不限" :aria-invalid="Boolean(errors.circMvMax)" :aria-describedby="errors.circMvMax ? 'screener-market-cap-error' : undefined" />
            <strong v-if="errors.circMvMin || errors.circMvMax" id="screener-market-cap-error" role="alert">{{ errors.circMvMin || errors.circMvMax }}</strong>
          </div>

          <div class="screener-field screener-range-field">
            <span class="screener-field-label">当日涨跌幅 <small>%</small></span>
            <label for="screener-change-min">最小值</label>
            <input id="screener-change-min" v-model="model.changeMin" type="number" inputmode="decimal" step="any" placeholder="不限" :aria-invalid="Boolean(errors.changeMin)" :aria-describedby="errors.changeMin ? 'screener-change-error' : undefined" />
            <span aria-hidden="true">至</span>
            <label for="screener-change-max">最大值</label>
            <input id="screener-change-max" v-model="model.changeMax" type="number" inputmode="decimal" step="any" placeholder="不限" :aria-invalid="Boolean(errors.changeMax)" :aria-describedby="errors.changeMax ? 'screener-change-error' : undefined" />
            <strong v-if="errors.changeMin || errors.changeMax" id="screener-change-error" role="alert">{{ errors.changeMin || errors.changeMax }}</strong>
          </div>

          <div class="screener-field">
            <label class="screener-field-label" for="screener-amount-min">成交额下限 <small>亿元</small></label>
            <input id="screener-amount-min" v-model="model.amountMin" type="number" inputmode="decimal" min="0" step="any" placeholder="不限" :aria-invalid="Boolean(errors.amountMin)" :aria-describedby="errors.amountMin ? 'screener-amount-error' : undefined" />
            <strong v-if="errors.amountMin" id="screener-amount-error" role="alert">{{ errors.amountMin }}</strong>
          </div>
        </div>
      </fieldset>

      <details class="screener-advanced">
        <summary>高级条件 <span>估值、行业、板块与排除项</span></summary>
        <div class="screener-advanced-body">
          <fieldset class="screener-fieldset">
            <legend>估值与行业</legend>
            <div class="screener-filter-grid">
              <div class="screener-field screener-range-field">
                <span class="screener-field-label">市盈率 TTM <small>倍</small></span>
                <label for="screener-pe-min">最小值</label>
                <input id="screener-pe-min" v-model="model.peMin" type="number" inputmode="decimal" step="any" placeholder="不限" :aria-invalid="Boolean(errors.peMin)" :aria-describedby="errors.peMin ? 'screener-pe-error' : undefined" />
                <span aria-hidden="true">至</span>
                <label for="screener-pe-max">最大值</label>
                <input id="screener-pe-max" v-model="model.peMax" type="number" inputmode="decimal" step="any" placeholder="不限" :aria-invalid="Boolean(errors.peMax)" :aria-describedby="errors.peMax ? 'screener-pe-error' : undefined" />
                <strong v-if="errors.peMin || errors.peMax" id="screener-pe-error" role="alert">{{ errors.peMin || errors.peMax }}</strong>
              </div>

              <div class="screener-field screener-range-field">
                <span class="screener-field-label">市净率 PB <small>倍</small></span>
                <label for="screener-pb-min">最小值</label>
                <input id="screener-pb-min" v-model="model.pbMin" type="number" inputmode="decimal" step="any" placeholder="不限" :aria-invalid="Boolean(errors.pbMin)" :aria-describedby="errors.pbMin ? 'screener-pb-error' : undefined" />
                <span aria-hidden="true">至</span>
                <label for="screener-pb-max">最大值</label>
                <input id="screener-pb-max" v-model="model.pbMax" type="number" inputmode="decimal" step="any" placeholder="不限" :aria-invalid="Boolean(errors.pbMax)" :aria-describedby="errors.pbMax ? 'screener-pb-error' : undefined" />
                <strong v-if="errors.pbMin || errors.pbMax" id="screener-pb-error" role="alert">{{ errors.pbMin || errors.pbMax }}</strong>
              </div>

              <div class="screener-field">
                <label class="screener-field-label" for="screener-industry">所属行业关键词</label>
                <input id="screener-industry" v-model="model.industry" type="text" autocomplete="off" placeholder="留空表示不限行业" />
              </div>
            </div>
          </fieldset>

          <fieldset class="screener-fieldset screener-board-fieldset">
            <legend>板块与排除项</legend>
            <div class="screener-checkbox-grid">
              <label v-for="board in boards" :key="board"><input v-model="model.boards" type="checkbox" :value="board" /> <span>{{ board }}</span></label>
              <label><input v-model="model.excludeSt" type="checkbox" /> <span>剔除 ST / 退市</span></label>
              <label><input v-model="model.excludeLoss" type="checkbox" /> <span>剔除亏损股（PE≤0 或缺失）</span></label>
            </div>
            <strong v-if="errors.boards" class="screener-board-error" role="alert">{{ errors.boards }}</strong>
          </fieldset>
        </div>
      </details>

      <div class="screener-form-actions">
        <button type="button" class="secondary-button" @click="reset">重置条件</button>
      </div>
      <p v-if="stale" class="screener-stale-note" role="status">筛选条件已变化，下方仍保留上次结果；请重新执行筛选。</p>
    </form>
  </section>
</template>

<style scoped>
.screener-filters { min-width: 0; overflow: hidden; }
.screener-section-heading { display: flex; min-height: 68px; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.screener-section-heading h2 { margin: 0; font-size: 18px; }
.screener-section-heading > span { color: var(--text-3); font-size: 12px; text-align: right; }
#screener-filter-form { display: grid; gap: 14px; padding: 14px; }
.screener-form-alert { display: grid; gap: 2px; padding: 9px 11px; border: 1px solid var(--danger); border-radius: var(--radius-sm); background: color-mix(in srgb, var(--danger) 7%, var(--surface)); }
.screener-form-alert span { color: var(--text-2); font-size: 13px; }
.screener-fieldset { min-width: 0; padding: 0; border: 0; }
.screener-fieldset legend { width: 100%; margin-bottom: 8px; color: var(--text-3); font-size: 12px; font-weight: 750; letter-spacing: .08em; }
.screener-presets { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
.screener-preset { display: grid; min-height: 68px; align-content: center; gap: 3px; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); text-align: left; cursor: pointer; }
.screener-preset:hover { border-color: var(--border-strong); background: var(--surface-3); }
.screener-preset.is-active { border-color: var(--accent); background: var(--accent-dim); }
.screener-preset strong { font-size: 14px; }
.screener-preset span { color: var(--text-3); font-size: 12px; line-height: 1.45; }
.screener-filter-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
.screener-field { display: grid; min-width: 0; grid-template-columns: minmax(0, 1fr); gap: 5px; }
.screener-field-label { color: var(--text-2); font-size: 13px; font-weight: 720; }
.screener-field-label small { color: var(--text-3); font-size: 12px; font-weight: 550; }
.screener-field input { width: 100%; min-width: 0; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); font-family: "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }
.screener-field input[aria-invalid="true"] { border-color: var(--danger); }
.screener-field > strong, .screener-board-error { color: var(--danger); font-size: 12px; }
.screener-range-field { grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); align-items: center; }
.screener-range-field .screener-field-label, .screener-range-field > strong { grid-column: 1 / -1; }
.screener-range-field > label:not(.screener-field-label) { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.screener-range-field > span[aria-hidden="true"] { color: var(--text-3); font-size: 12px; }
.screener-advanced { overflow: hidden; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.screener-advanced summary { display: flex; min-height: 44px; align-items: center; gap: 8px; padding: 8px 12px; color: var(--text); font-weight: 720; cursor: pointer; }
.screener-advanced summary span { color: var(--text-3); font-size: 13px; font-weight: 500; }
.screener-advanced-body { display: grid; gap: 14px; padding: 12px; border-top: 1px solid var(--border); }
.screener-checkbox-grid { display: flex; flex-wrap: wrap; gap: 8px; }
.screener-checkbox-grid label { display: inline-flex; min-height: 44px; align-items: center; gap: 7px; padding: 6px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface); color: var(--text-2); cursor: pointer; }
.screener-checkbox-grid input { width: 18px; height: 18px; accent-color: var(--accent); }
.screener-form-actions { display: flex; justify-content: flex-end; padding-top: 2px; }
.screener-stale-note { margin: 0; padding: 8px 10px; border-left: 3px solid var(--warning); background: color-mix(in srgb, var(--warning) 7%, var(--surface)); color: var(--text-2); font-size: 13px; }
@media (max-width: 1180px) { .screener-filter-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } .screener-presets { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 860px) { .screener-filter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .screener-presets { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 720px) {
  .screener-section-heading, .screener-form-actions { align-items: stretch; flex-direction: column; }
  .screener-section-heading > span { text-align: left; }
  .screener-filter-grid { grid-template-columns: minmax(0, 1fr); }
  .screener-presets { grid-template-columns: minmax(0, 1fr); }
  .screener-form-actions .secondary-button { width: 100%; }
  .screener-checkbox-grid { display: grid; grid-template-columns: minmax(0, 1fr); }
}
</style>
