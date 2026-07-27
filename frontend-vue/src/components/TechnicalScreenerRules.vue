<script setup lang="ts">
import { computed } from 'vue'
import type {
  MatchMode,
  TechnicalCondition,
  TechnicalRuleConfig,
  TechnicalScheme,
  TechnicalSignalDefinition,
  TechnicalTimeframe,
  TechnicalUniverse,
} from '../types/technicalScreener'

const props = defineProps<{
  ruleConfig: TechnicalRuleConfig
  universe: TechnicalUniverse
  minimumAmountWan: string
  defaultTimeframe: TechnicalTimeframe
  sortField: string
  sortDirection: 'asc' | 'desc'
  schemes: TechnicalScheme[]
  selectedSchemeId: string
  schemeName: string
  presetReadOnly: boolean
  legacyReadOnly: boolean
  legacyReason: string
  schemeBusy: boolean
  schemeFeedback: string
  schemesError: string
  conflicts: string[]
  definitions: TechnicalSignalDefinition[]
}>()

const emit = defineEmits<{
  dirty: []
  loadScheme: [id: string]
  updateSchemeName: [value: string]
  copyDraft: []
  saveScheme: []
  deleteScheme: []
  addGroup: [kind: 'satisfy' | 'exclude']
  removeGroup: [kind: 'satisfy' | 'exclude', index: number]
  openLibrary: [kind: 'satisfy' | 'exclude', index: number, trigger: HTMLButtonElement]
  removeCondition: [kind: 'satisfy' | 'exclude', groupIndex: number, conditionIndex: number]
  restoreDefaults: [condition: TechnicalCondition]
  setSignal: [condition: TechnicalCondition, code: string]
  updateMinimumAmount: [value: string]
  updateDefaultTimeframe: [value: TechnicalTimeframe]
  updateSortField: [value: string]
  updateSortDirection: [value: 'asc' | 'desc']
  run: []
}>()

const MARKET_OPTIONS = [
  ['MAIN', '主板'], ['CHINEXT', '创业板'], ['STAR', '科创板'], ['BSE', '北交所'],
]

const naturalPreview = computed(() => {
  const phrase = (conditions: TechnicalCondition[], mode: MatchMode) => conditions.length
    ? conditions.map((item) => definitionName(item.signal_code)).join(mode === 'ANY' ? '，或者' : '，并且')
    : '尚未添加技术信号'
  const satisfy = props.ruleConfig.satisfy.groups.map((group) => phrase(group.conditions, group.conditionMatch))
  const exclude = props.ruleConfig.exclude.groups.map((group) => phrase(group.conditions, group.conditionMatch))
  return {
    satisfy: satisfy.length ? satisfy.join(props.ruleConfig.satisfy.groupMatch === 'ANY' ? '；或者 ' : '；并且 ') : '尚未添加满足规则',
    exclude: exclude.length ? exclude.join('；或者 ') : '当前没有排除规则',
  }
})

function definition(code: string) {
  return props.definitions.find((item) => item.signal_code === code)
}

function definitionName(code: string) {
  return definition(code)?.signal_name || code
}

function timeframeName(value: string) {
  return ({ D: '日线', W: '周线', M: '月线' } as Record<string, string>)[value] || value
}

function markEdited() {
  emit('dirty')
}
</script>

<template>
  <form id="technical-screener-form" class="technical-rules" @submit.prevent="emit('run')">
    <section class="panel technical-scheme-panel" aria-labelledby="technical-scheme-title">
      <header>
        <div><p class="eyebrow">方案</p><h2 id="technical-scheme-title">规则方案</h2></div>
        <span v-if="presetReadOnly">预置只读</span>
        <span v-else-if="legacyReadOnly">旧方案只读</span>
      </header>
      <div class="technical-scheme-grid">
        <label>选择方案
          <select :value="selectedSchemeId" @change="emit('loadScheme', ($event.target as HTMLSelectElement).value)">
            <option value="">预置：强势 MACD 金叉</option>
            <option v-for="item in schemes" :key="item.id" :value="item.id">{{ item.name }}{{ item.editable === false ? '（旧方案只读）' : '' }}</option>
          </select>
        </label>
        <label>方案名称
          <input :value="schemeName" maxlength="80" :disabled="legacyReadOnly" @input="emit('updateSchemeName', ($event.target as HTMLInputElement).value)" @change="markEdited">
        </label>
      </div>
      <div class="technical-scheme-actions">
        <button class="secondary-button" type="button" :disabled="schemeBusy" @click="emit('copyDraft')">{{ legacyReadOnly ? '创建重建草稿' : '复制为自定义' }}</button>
        <button class="secondary-button" type="button" :disabled="schemeBusy || presetReadOnly || legacyReadOnly || conflicts.length > 0" @click="emit('saveScheme')">{{ selectedSchemeId ? '更新方案' : '保存方案' }}</button>
        <button class="danger-button" type="button" :disabled="schemeBusy || !selectedSchemeId" @click="emit('deleteScheme')">删除方案</button>
      </div>
      <p v-if="legacyReadOnly" class="technical-notice" role="status">{{ legacyReason }}。原筛选逻辑仍可执行，但不能在当前编辑器中改写。</p>
      <p v-if="schemeFeedback" class="technical-feedback" aria-live="polite">{{ schemeFeedback }}</p>
      <p v-if="schemesError" class="technical-inline-error" role="alert">方案列表暂不可用：{{ schemesError }}</p>
    </section>

    <fieldset class="panel technical-universe" :disabled="presetReadOnly || legacyReadOnly">
      <legend>股票池边界</legend>
      <p>先限定市场与流动性；所有金额提交为元。</p>
      <div class="technical-market-grid">
        <label v-for="([code, label]) in MARKET_OPTIONS" :key="code">
          <input v-model="universe.markets" type="checkbox" :value="code" @change="markEdited">{{ label }}
        </label>
        <label><input v-model="universe.exclude_st" type="checkbox" @change="markEdited">排除 ST</label>
        <label><input v-model="universe.exclude_suspended" type="checkbox" @change="markEdited">排除停牌</label>
      </div>
      <div class="technical-universe-fields">
        <label for="technical-listing-days">排除上市不足（天）
          <input id="technical-listing-days" v-model.number="universe.min_listing_days" type="number" inputmode="numeric" min="0" max="1000" @input="markEdited">
        </label>
        <label for="technical-min-amount">20 日均成交额不低于（万元）
          <input id="technical-min-amount" :value="minimumAmountWan" type="number" inputmode="decimal" min="0" @input="emit('updateMinimumAmount', ($event.target as HTMLInputElement).value); markEdited()">
        </label>
      </div>
    </fieldset>

    <section class="panel technical-period" aria-labelledby="technical-period-title">
      <div>
        <p class="eyebrow">周期口径</p>
        <h2 id="technical-period-title">新增条件默认周期</h2>
      </div>
      <label for="technical-default-timeframe">默认周期
        <select id="technical-default-timeframe" :value="defaultTimeframe" :disabled="presetReadOnly || legacyReadOnly" @change="emit('updateDefaultTimeframe', ($event.target as HTMLSelectElement).value as TechnicalTimeframe); markEdited()">
          <option value="D">日线</option><option value="W">周线</option><option value="M">月线</option>
        </select>
      </label>
      <details><summary>未完成周期说明</summary><p>未完成周线/月线信号可能在周期结束前消失；勾选后命中原因必须显示“未确认”。</p></details>
    </section>

    <section v-if="legacyReadOnly" class="panel technical-legacy" aria-labelledby="technical-legacy-title">
      <p class="eyebrow">兼容模式</p>
      <h2 id="technical-legacy-title">原方案逻辑只读保留</h2>
      <p>{{ legacyReason }}。如需修改，请先创建重建草稿；原方案不会被覆盖。</p>
    </section>

    <template v-else>
      <section
        v-for="kind in (['satisfy', 'exclude'] as const)"
        :key="kind"
        class="panel technical-rule-section"
        :aria-labelledby="`technical-${kind}-title`"
      >
        <header>
          <div>
            <p class="eyebrow">{{ kind === 'satisfy' ? '入选条件' : '禁止条件' }}</p>
            <h2 :id="`technical-${kind}-title`">{{ kind === 'satisfy' ? '满足规则' : '排除规则' }}</h2>
            <p>{{ kind === 'satisfy' ? '决定股票需要满足什么条件' : '命中任意一个排除组即剔除' }}</p>
          </div>
          <fieldset v-if="kind === 'satisfy' && ruleConfig.satisfy.groups.length > 1" class="technical-segmented" :disabled="presetReadOnly">
            <legend>规则组之间</legend>
            <label><input v-model="ruleConfig.satisfy.groupMatch" type="radio" value="ALL" @change="markEdited"><span>所有组成立</span></label>
            <label><input v-model="ruleConfig.satisfy.groupMatch" type="radio" value="ANY" @change="markEdited"><span>任意组成立</span></label>
          </fieldset>
        </header>

        <div class="technical-groups">
          <article v-for="(group, groupIndex) in ruleConfig[kind].groups" :key="group.id" class="technical-group">
            <header>
              <div><small>{{ kind === 'satisfy' ? '满足规则' : '排除规则' }} {{ groupIndex + 1 }}</small><strong>{{ kind === 'satisfy' ? '股票需要满足本组' : '出现本组情况时剔除' }}</strong></div>
              <button class="secondary-button" type="button" :disabled="presetReadOnly" @click="emit('removeGroup', kind, groupIndex)">删除本组</button>
            </header>
            <fieldset class="technical-segmented" :disabled="presetReadOnly">
              <legend>本组条件</legend>
              <label><input v-model="group.conditionMatch" type="radio" value="ALL" @change="markEdited"><span>全部成立</span></label>
              <label><input v-model="group.conditionMatch" type="radio" value="ANY" @change="markEdited"><span>{{ kind === 'exclude' ? '任意出现' : '任意成立' }}</span></label>
            </fieldset>
            <button
              class="secondary-button technical-add-signal"
              type="button"
              :disabled="presetReadOnly"
              aria-haspopup="dialog"
              @click="emit('openLibrary', kind, groupIndex, $event.currentTarget as HTMLButtonElement)"
            >添加技术信号</button>

            <div class="technical-conditions">
              <article v-for="(condition, conditionIndex) in group.conditions" :key="condition.id" class="technical-condition">
                <header>
                  <div><small>{{ definition(condition.signal_code)?.signal_kind === 'state' ? '状态型' : '事件型' }} · {{ timeframeName(condition.timeframe) }}</small><strong>{{ definitionName(condition.signal_code) }}</strong></div>
                  <button class="secondary-button" type="button" :disabled="presetReadOnly" @click="emit('removeCondition', kind, groupIndex, conditionIndex)">删除条件</button>
                </header>
                <div class="technical-condition-fields">
                  <label>技术信号
                    <select :value="condition.signal_code" :disabled="presetReadOnly" @change="emit('setSignal', condition, ($event.target as HTMLSelectElement).value)">
                      <option v-for="item in definitions.filter((row) => row.available)" :key="item.signal_code" :value="item.signal_code">{{ item.signal_name }}</option>
                    </select>
                  </label>
                  <label>周期
                    <select v-model="condition.timeframe" :disabled="presetReadOnly" @change="markEdited">
                      <option value="D">日线</option><option value="W">周线</option><option value="M">月线</option>
                    </select>
                  </label>
                  <label>时间语义
                    <select v-model="condition.occurrence" :disabled="presetReadOnly || definition(condition.signal_code)?.signal_kind === 'state'" @change="markEdited">
                      <option v-if="definition(condition.signal_code)?.signal_kind !== 'state'" value="TODAY">今日刚发生</option>
                      <option v-if="definition(condition.signal_code)?.signal_kind !== 'state'" value="RECENT">近 N 周期发生</option>
                      <option value="CURRENT">当前成立</option>
                    </select>
                  </label>
                  <label v-if="condition.occurrence === 'RECENT'">近几个周期
                    <input v-model.number="condition.recent_periods" :disabled="presetReadOnly" type="number" inputmode="numeric" min="1" max="60" @input="markEdited">
                  </label>
                  <label v-for="(value, key) in condition.params" :key="key">参数 {{ key }}
                    <input v-model.number="condition.params[key]" :disabled="presetReadOnly" type="number" inputmode="decimal" step="any" @input="markEdited">
                  </label>
                </div>
                <label v-if="condition.timeframe !== 'D'" class="technical-partial-period"><input v-model="condition.include_partial_period" :disabled="presetReadOnly" type="checkbox" @change="markEdited">使用未完成周期（命中将标为未确认）</label>
                <button class="secondary-button" type="button" :disabled="presetReadOnly" @click="emit('restoreDefaults', condition)">恢复默认参数</button>
              </article>
              <p v-if="!group.conditions.length" class="technical-empty-group">尚未添加技术信号。</p>
            </div>
          </article>
        </div>
        <button class="secondary-button technical-add-group" type="button" :disabled="presetReadOnly" @click="emit('addGroup', kind)">添加一组{{ kind === 'satisfy' ? '满足' : '排除' }}规则</button>
      </section>

      <section class="panel technical-preview" aria-labelledby="technical-preview-title">
        <p class="eyebrow">自然语言校验</p>
        <h2 id="technical-preview-title">当前规则含义</h2>
        <p><strong>满足：</strong>{{ naturalPreview.satisfy }}。</p>
        <p><strong>排除：</strong>{{ naturalPreview.exclude }}。</p>
      </section>
    </template>

    <section class="panel technical-sort" aria-labelledby="technical-sort-title">
      <div><p class="eyebrow">服务端排序</p><h2 id="technical-sort-title">结果顺序</h2></div>
      <label>排序字段
        <select :value="sortField" :disabled="presetReadOnly || legacyReadOnly" @change="emit('updateSortField', ($event.target as HTMLSelectElement).value); markEdited()">
          <option value="signal_score">综合信号分</option><option value="change_pct">涨跌幅</option><option value="last_amount">成交额</option><option value="avg_amount20">20 日均成交额</option><option value="last_price">最新价</option>
        </select>
      </label>
      <label>排序方向
        <select :value="sortDirection" :disabled="presetReadOnly || legacyReadOnly" @change="emit('updateSortDirection', ($event.target as HTMLSelectElement).value as 'asc' | 'desc'); markEdited()">
          <option value="desc">从高到低</option><option value="asc">从低到高</option>
        </select>
      </label>
    </section>

    <div v-if="conflicts.length" class="technical-conflicts" role="alert">
      <strong>规则尚不能执行</strong>
      <span v-for="item in conflicts" :key="item">{{ item }}</span>
    </div>
  </form>
</template>

<style scoped>
.technical-rules { min-width: 0; display: grid; gap: 10px; }
.technical-rules h2 { margin: 0; font-size: 17px; }
.technical-rules input:not([type="checkbox"]):not([type="radio"]), .technical-rules select { width: 100%; min-height: 44px; margin-top: 5px; padding: 7px 9px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); color: var(--text); }
.technical-rules label { min-width: 0; color: var(--text-2); font-size: 13px; }
.technical-scheme-panel, .technical-universe, .technical-period, .technical-rule-section, .technical-preview, .technical-sort, .technical-legacy { padding: 12px; }
.technical-scheme-panel > header, .technical-rule-section > header, .technical-group > header, .technical-condition > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.technical-scheme-panel > header > span { padding: 3px 7px; border-radius: 999px; background: color-mix(in srgb, var(--info) 9%, var(--surface)); color: var(--info); font-size: 12px; }
.technical-scheme-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px; }
.technical-scheme-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.technical-feedback, .technical-notice, .technical-inline-error { margin: 8px 0 0; padding: 8px 10px; border-left: 3px solid var(--info); background: color-mix(in srgb, var(--info) 8%, var(--surface)); color: var(--text-2); }
.technical-notice { border-left-color: var(--warning); background: color-mix(in srgb, var(--warning) 8%, var(--surface)); }
.technical-inline-error { border-left-color: var(--danger); background: color-mix(in srgb, var(--danger) 8%, var(--surface)); }
.technical-universe { min-width: 0; margin: 0; }
.technical-universe legend { padding: 0 4px; font-weight: 760; }
.technical-universe > p { margin: 2px 0 10px; color: var(--text-3); }
.technical-market-grid { display: flex; flex-wrap: wrap; gap: 5px 16px; }
.technical-market-grid label, .technical-partial-period { min-height: 44px; display: inline-flex; align-items: center; gap: 7px; }
.technical-market-grid input, .technical-partial-period input { width: 18px; height: 18px; accent-color: var(--accent); }
.technical-universe-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
.technical-period, .technical-sort { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: center; gap: 8px; }
.technical-period > div, .technical-sort > div { grid-column: 1 / -1; }
.technical-period details { grid-column: 1 / -1; color: var(--text-2); }
.technical-period details p { margin: 6px 0 0; color: var(--text-3); line-height: 1.5; }
.technical-rule-section > header p:last-child { margin: 4px 0 0; color: var(--text-3); }
.technical-groups, .technical-conditions { display: grid; gap: 8px; margin-top: 10px; }
.technical-group { padding: 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-2); }
.technical-group header small, .technical-condition header small { display: block; color: var(--text-3); }
.technical-group header strong, .technical-condition header strong { display: block; margin-top: 2px; }
.technical-segmented { min-width: 0; display: flex; gap: 4px; margin: 9px 0; padding: 4px; border: 1px solid var(--border); border-radius: 7px; }
.technical-segmented legend { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.technical-segmented label { flex: 1; cursor: pointer; }
.technical-segmented input { position: absolute; opacity: 0; }
.technical-segmented span { min-height: 44px; display: grid; place-items: center; padding: 5px; border-radius: 5px; text-align: center; }
.technical-segmented input:checked + span { box-shadow: inset 0 0 0 1px var(--info); background: color-mix(in srgb, var(--info) 9%, var(--surface)); color: var(--text); }
.technical-segmented input:focus-visible + span { outline: 3px solid var(--focus); outline-offset: 2px; }
.technical-add-signal, .technical-add-group { width: 100%; border-style: dashed; }
.technical-condition { padding: 10px; border-left: 3px solid var(--info); border-radius: 6px; background: var(--surface); }
.technical-condition-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; margin: 8px 0; }
.technical-empty-group { margin: 0; padding: 10px; color: var(--text-3); text-align: center; }
.technical-preview p { margin: 7px 0 0; color: var(--text-2); line-height: 1.55; }
.technical-conflicts { display: grid; gap: 4px; padding: 10px; border-left: 3px solid var(--danger); background: color-mix(in srgb, var(--danger) 8%, var(--surface)); }
@media (max-width: 720px) {
  .technical-scheme-grid, .technical-universe-fields, .technical-condition-fields { grid-template-columns: minmax(0, 1fr); }
  .technical-period, .technical-sort { grid-template-columns: minmax(0, 1fr); }
  .technical-scheme-actions { display: grid; grid-template-columns: minmax(0, 1fr); }
  .technical-rule-section > header { flex-direction: column; }
}
</style>
