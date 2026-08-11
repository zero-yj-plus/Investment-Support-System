<script setup lang="ts">
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import type {
  DataMaterialDefinition,
  DataMaterialReport,
  RequiredMaterialDefinition,
} from '../types/stockAsk'

const props = defineProps<{
  items: DataMaterialDefinition[]
  requiredItems: RequiredMaterialDefinition[]
  selectedIds: string[]
  preselectedIds: string[]
  maxSelected: number
  adjusted: boolean
  report: DataMaterialReport | null
  loading: boolean
  error: string
  disabled: boolean
}>()

const emit = defineEmits<{
  toggle: [id: string]
  reset: []
  retry: []
}>()

const selectedSet = computed(() => new Set(props.selectedIds))
const preselectedSet = computed(() => new Set(props.preselectedIds))
const atLimit = computed(() => props.selectedIds.length >= props.maxSelected)
const resultById = computed(() => new Map(
  (props.report?.optional || []).map((item) => [item.id, item]),
))
const requiredResultById = computed(() => new Map(
  (props.report?.required || []).map((item) => [item.id, item]),
))

function disabledFor(item: DataMaterialDefinition) {
  return props.disabled
    || Boolean(props.report)
    || item.availability === 'unavailable'
    || (atLimit.value && !selectedSet.value.has(item.id))
}

function requiredAfterState(item: RequiredMaterialDefinition) {
  const result = requiredResultById.value.get(item.id)
  if (!result) return '缺失'
  if (result.state === 'missing') return '缺失'
  if (result.state === 'degraded') return '降级'
  return result.injected ? '已注入' : '未注入'
}

function beforeState(item: DataMaterialDefinition) {
  if (item.availability === 'unavailable') return '暂不可选'
  if (selectedSet.value.has(item.id)) {
    return preselectedSet.value.has(item.id) ? '系统预选' : '用户选择'
  }
  return item.slow ? '可能较慢' : '系统支持'
}

function afterState(item: DataMaterialDefinition) {
  const result = resultById.value.get(item.id)
  if (!result || result.state === 'not_selected') return '未注入'
  if (result.state === 'missing') return '缺失'
  if (result.state === 'degraded') return '降级'
  return result.injected ? '已注入' : '未注入'
}

function resultDetail(item: DataMaterialDefinition) {
  const result = resultById.value.get(item.id)
  if (!result) return item.description
  const details = [
    result.data_date ? `日期 ${result.data_date}` : '',
    result.source ? `来源 ${result.source}` : '',
    result.stale === true ? '陈旧数据' : '',
    result.reason || '',
  ].filter(Boolean)
  return details.join(' · ') || item.description
}
</script>

<template>
  <aside class="stock-ask-materials panel" aria-labelledby="stock-ask-materials-title">
    <header>
      <h2 id="stock-ask-materials-title">本轮数据材料</h2>
      <span class="stock-ask-materials__count" aria-live="polite">
        {{ report ? '本轮结果' : `已选 ${selectedIds.length}/${maxSelected}` }}
      </span>
    </header>
    <p class="stock-ask-materials__intro">
      系统根据问题预选，发送前可以调整。最终选择同时控制数据采集和模型注入。
    </p>

    <section aria-labelledby="stock-ask-required-title">
      <div class="stock-ask-materials__heading">
        <h3 id="stock-ask-required-title">系统必需</h3><span>不可取消</span>
      </div>
      <div class="stock-ask-materials__required-list">
        <div v-for="item in requiredItems" :key="item.id" class="stock-ask-materials__required">
          <AppIcon name="lock" :size="17" />
          <span><strong>{{ item.name }}</strong><small v-if="requiredResultById.get(item.id)?.reason">{{ requiredResultById.get(item.id)?.reason }}</small></span>
          <em>{{ report ? requiredAfterState(item) : '系统必需' }}</em>
        </div>
      </div>
    </section>

    <section aria-labelledby="stock-ask-optional-title">
      <div class="stock-ask-materials__heading">
        <h3 id="stock-ask-optional-title">可选材料</h3><span>最多选择 {{ maxSelected }} 项</span>
      </div>
      <p v-if="loading" class="stock-ask-materials__state" role="status">正在读取材料目录…</p>
      <fieldset v-else>
        <legend class="sr-only">选择本轮需要采集并注入模型的数据材料</legend>
        <label
          v-for="item in items"
          :key="item.id"
          :class="{ 'is-selected': selectedSet.has(item.id), 'is-disabled': disabledFor(item) }"
        >
          <input
            type="checkbox"
            :checked="selectedSet.has(item.id)"
            :disabled="disabledFor(item) || Boolean(report)"
            @change="emit('toggle', item.id)"
          >
          <span class="stock-ask-materials__copy">
            <strong>{{ item.name }}</strong>
            <small>{{ report ? resultDetail(item) : item.description }}</small>
          </span>
          <em>{{ report ? afterState(item) : beforeState(item) }}</em>
        </label>
      </fieldset>
    </section>

    <footer>
      <button type="button" :disabled="disabled || Boolean(report)" @click="emit('reset')">恢复系统预选</button>
      <p v-if="error" class="is-error" role="alert">{{ error }} <button v-if="!items.length" type="button" @click="emit('retry')">重试</button></p>
      <p v-else-if="report && !report.model_used">本轮使用确定性答复，未调用模型；已采集材料不会标成“已注入”。</p>
      <p v-else-if="atLimit">已达到 {{ maxSelected }} 项上限；如需更换，请先取消一个已选材料。</p>
      <p v-else-if="selectedIds.length">发送时将采集并注入：{{ items.filter((item) => selectedSet.has(item.id)).map((item) => item.name).join('、') }}。</p>
      <p v-else>当前未选择可选材料；发送时只使用四个系统必需底座。</p>
    </footer>
  </aside>
</template>

<style scoped>
.stock-ask-materials { min-width: 0; align-self: start; overflow: hidden; }
.stock-ask-materials > header { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border); }
.stock-ask-materials h2 { margin: 0; font-size: 17px; }
.stock-ask-materials__count { padding: 3px 7px; border: 1px solid var(--border); border-radius: 4px; color: var(--accent); font-size: 12px; font-weight: 750; white-space: nowrap; }
.stock-ask-materials__intro { margin: 0; padding: 9px 12px; border-bottom: 1px solid var(--border); color: var(--text-3); font-size: 12px; line-height: 1.55; }
.stock-ask-materials section { padding: 12px; border-bottom: 1px solid var(--border); }
.stock-ask-materials__heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.stock-ask-materials__heading h3 { margin: 0; font-size: 14px; }
.stock-ask-materials__heading span { color: var(--text-3); font-size: 11px; }
.stock-ask-materials__required-list, .stock-ask-materials fieldset { display: grid; gap: 6px; min-width: 0; margin: 0; padding: 0; border: 0; }
.stock-ask-materials__required, .stock-ask-materials fieldset label { display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; align-items: center; gap: 8px; min-height: 44px; padding: 7px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }
.stock-ask-materials__required > span, .stock-ask-materials__copy { display: grid; min-width: 0; gap: 1px; }
.stock-ask-materials strong { overflow-wrap: anywhere; font-size: 13px; line-height: 1.35; }
.stock-ask-materials small { color: var(--text-3); font-size: 11px; line-height: 1.35; }
.stock-ask-materials em { align-self: start; padding-top: 2px; color: var(--text-3); font-size: 11px; font-style: normal; font-weight: 720; white-space: nowrap; }
.stock-ask-materials__required em { color: var(--info); }
.stock-ask-materials fieldset label { min-height: 56px; cursor: pointer; transition: border-color 180ms ease, background 180ms ease; }
.stock-ask-materials fieldset label:hover { border-color: var(--border-strong); background: var(--surface-3); }
.stock-ask-materials fieldset label.is-selected { border-color: color-mix(in srgb, var(--accent) 46%, var(--border)); background: var(--accent-dim); }
.stock-ask-materials fieldset label.is-selected em { color: var(--accent); }
.stock-ask-materials fieldset label.is-disabled { cursor: not-allowed; opacity: .55; }
.stock-ask-materials input { width: 18px; height: 18px; margin: 0; accent-color: var(--accent); cursor: inherit; }
.stock-ask-materials__state { min-height: 80px; margin: 0; color: var(--text-3); }
.stock-ask-materials footer { display: grid; gap: 7px; padding: 10px 12px 12px; }
.stock-ask-materials footer > button { min-height: 44px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); color: var(--text-2); font-size: 13px; font-weight: 700; cursor: pointer; }
.stock-ask-materials footer > button:hover { border-color: var(--border-strong); color: var(--text); }
.stock-ask-materials footer button:disabled { cursor: wait; opacity: .55; }
.stock-ask-materials footer p { min-height: 34px; margin: 0; color: var(--text-3); font-size: 11px; line-height: 1.5; }
.stock-ask-materials footer p.is-error { color: var(--danger); }
.stock-ask-materials footer p button { min-height: 44px; margin-left: 6px; border: 1px solid var(--danger); border-radius: 6px; background: transparent; color: var(--danger); }
@media (prefers-reduced-motion: reduce) { .stock-ask-materials fieldset label { transition: none; } }
</style>
