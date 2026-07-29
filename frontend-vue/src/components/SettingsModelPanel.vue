<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { ModelConnection, ModelConnectionPayload, ModelConnectionsResponse, SettingsView } from '../types/settings'
import { textValue } from '../utils/settingsFormat'

const props = defineProps<{
  response: ModelConnectionsResponse | null
  settings: SettingsView | null
  loading: boolean
  error: string
  busy: boolean
}>()
const emit = defineEmits<{
  test: [payload: ModelConnectionPayload & { connection_id?: string }]
  save: [connectionId: string | null, payload: ModelConnectionPayload]
  activate: [connectionId: string]
  delete: [connectionId: string]
  retry: []
}>()

const selectedId = ref<string | null>(null)
const touched = ref(false)
const form = reactive({ name: '', provider: 'custom', base_url: '', model: '', api_key: '', clear_api_key: false, activate: true })
const rows = computed(() => Array.isArray(props.response?.connections) ? props.response.connections : null)
const activeId = computed(() => props.response?.active_connection_id ?? props.settings?.active_model_connection_id ?? null)
const selected = computed(() => rows.value?.find((row) => row.id === selectedId.value) || null)

function fill(connection: ModelConnection | null) {
  form.name = typeof connection?.name === 'string' ? connection.name : ''
  form.provider = typeof connection?.provider === 'string' && connection.provider ? connection.provider : 'custom'
  form.base_url = typeof connection?.base_url === 'string' ? connection.base_url : ''
  form.model = typeof connection?.model === 'string' ? connection.model : ''
  form.api_key = ''
  form.clear_api_key = false
  form.activate = connection ? connection.active === true : true
  touched.value = false
}

watch(rows, (value) => {
  if (!value) return
  if (selectedId.value && value.some((row) => row.id === selectedId.value)) return
  const nextId = typeof activeId.value === 'string' ? activeId.value : null
  selectedId.value = nextId
  fill(value.find((row) => row.id === nextId) || null)
}, { immediate: true })

const formError = computed(() => {
  if (!form.name.trim()) return '请填写连接名称。'
  if (!form.provider.trim()) return '请填写 Provider。'
  if (!form.base_url.trim()) return '请填写接口地址。'
  if (!form.model.trim()) return '请填写模型名。'
  return ''
})

function edit(connection: ModelConnection) {
  selectedId.value = typeof connection.id === 'string' ? connection.id : null
  fill(connection)
  document.querySelector('#settings-model-form')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function createNew() {
  selectedId.value = null
  fill(null)
}

function payload(): ModelConnectionPayload {
  return {
    name: form.name.trim(), provider: form.provider.trim(), base_url: form.base_url.trim(), model: form.model.trim(),
    ...(form.api_key.trim() ? { api_key: form.api_key.trim() } : {}),
    ...(form.clear_api_key ? { clear_api_key: true } : {}),
    activate: form.activate,
  }
}

function testConnection() {
  touched.value = true
  if (!formError.value) emit('test', { ...payload(), ...(selectedId.value ? { connection_id: selectedId.value } : {}) })
}

function saveConnection() {
  touched.value = true
  if (!formError.value) emit('save', selectedId.value, payload())
}

function deleteConnection(connection: ModelConnection) {
  const id = typeof connection.id === 'string' ? connection.id : ''
  if (id && window.confirm(`删除模型连接“${textValue(connection.name, id)}”？核心行情与确定性规则不会受影响。`)) emit('delete', id)
}
</script>

<template>
  <section id="settings-models" class="settings-models panel" aria-labelledby="settings-models-title">
    <header class="settings-models__heading">
      <div><p class="eyebrow">OPTIONAL MODEL</p><h2 id="settings-models-title">自定义模型连接</h2><p>模型只作可选解释增强；未配置不影响行情、风险与确定性规则。</p></div>
      <button type="button" :disabled="busy" @click="createNew">新建连接</button>
    </header>
    <div v-if="!rows && loading" class="settings-local-state" role="status">正在读取模型连接列表…</div>
    <div v-else-if="!rows" class="settings-local-state is-error" role="alert"><strong>连接状态暂不可读</strong><p>{{ error || '接口未返回可验证的 connections 数组。' }}</p><button type="button" @click="emit('retry')">重新读取</button></div>
    <template v-else>
      <div v-if="rows.length" class="settings-model-list">
        <article v-for="connection in rows" :key="String(connection.id || connection.name || 'model-connection')" :class="{ 'is-active': connection.active }">
          <div><h3>{{ textValue(connection.name, '未命名连接') }}</h3><span class="settings-state" :class="connection.active ? 'is-success' : 'is-neutral'">{{ connection.active ? '当前使用' : '已保存' }}</span></div>
          <dl><div><dt>Provider / 模型</dt><dd>{{ textValue(connection.provider) }} / {{ textValue(connection.model) }}</dd></div><div><dt>接口地址</dt><dd class="mono">{{ textValue(connection.base_url) }}</dd></div><div><dt>API Key</dt><dd>{{ connection.api_key_set === true ? '已安全保存' : (connection.api_key_set === false ? '未保存' : '状态未提供') }}</dd></div></dl>
          <div class="settings-actions"><button type="button" :disabled="busy" @click="edit(connection)">编辑</button><button v-if="!connection.active && connection.id" type="button" :disabled="busy" @click="emit('activate', connection.id)">设为当前</button><button type="button" class="danger-button" :disabled="busy || !connection.id" @click="deleteConnection(connection)">删除连接</button></div>
        </article>
      </div>
      <p v-else class="settings-empty">服务端返回真实空数组：尚未保存自定义模型连接。这是正常的可选未配置状态。</p>

      <form id="settings-model-form" class="settings-model-form" @submit.prevent="saveConnection">
        <div class="settings-model-form__heading"><div><h3>{{ selected ? `编辑：${textValue(selected.name)}` : '新建连接' }}</h3><p>密钥不回显；留空保持已保存密钥，清除必须显式勾选。</p></div><span class="settings-state is-neutral">核心决策不依赖模型</span></div>
        <div class="settings-form-grid">
          <label for="settings-model-name"><span>连接名称</span><input id="settings-model-name" v-model="form.name" type="text" maxlength="120" :disabled="busy" @blur="touched = true" /></label>
          <label for="settings-model-provider"><span>Provider</span><input id="settings-model-provider" v-model="form.provider" type="text" maxlength="80" :disabled="busy" @blur="touched = true" /></label>
          <label class="is-wide" for="settings-model-base"><span>接口地址</span><input id="settings-model-base" v-model="form.base_url" type="url" maxlength="2048" placeholder="公网 HTTPS /v1，或显式 localhost" :disabled="busy" @blur="touched = true" /><small>服务端会校验 URL 结构与 DNS，拒绝私网、凭据、查询参数和任意子路径。</small></label>
          <label for="settings-model-key"><span>API Key</span><input id="settings-model-key" v-model="form.api_key" type="password" maxlength="4096" autocomplete="new-password" :placeholder="selected?.api_key_set ? '已安全保存；留空保持不变' : '公网模型服务需要 API Key'" :disabled="busy || form.clear_api_key" /></label>
          <label for="settings-model-name-field"><span>模型名</span><input id="settings-model-name-field" v-model="form.model" type="text" maxlength="200" :disabled="busy" @blur="touched = true" /></label>
        </div>
        <label v-if="selected?.api_key_set" class="settings-checkbox" for="settings-model-clear-key"><input id="settings-model-clear-key" v-model="form.clear_api_key" type="checkbox" :disabled="busy" /><span><strong>明确清除已保存 API Key</strong><small>公网连接清除密钥时服务端会拒绝无效配置。</small></span></label>
        <label class="settings-checkbox" for="settings-model-activate"><input id="settings-model-activate" v-model="form.activate" type="checkbox" :disabled="busy" /><span><strong>保存后设为当前连接</strong><small>切换模型不改变核心行情和确定性决策规则。</small></span></label>
        <p v-if="touched && formError" class="settings-form-error" role="alert">{{ formError }}</p>
        <div class="settings-actions"><button type="button" :disabled="busy || Boolean(formError)" @click="testConnection">{{ busy ? '处理中…' : '测试当前填写' }}</button><button class="primary-button" type="submit" :disabled="busy || Boolean(formError)">{{ busy ? '保存中…' : (selected ? '保存更换' : '保存连接') }}</button></div>
      </form>
    </template>
  </section>
</template>

<style scoped>
.settings-models { min-width: 0; overflow: hidden; }.settings-models__heading { display: flex; min-height: 72px; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); }.settings-models__heading h2, .settings-model-form__heading h3 { margin: 0; }.settings-models__heading h2 { font-size: 19px; }.settings-models__heading p:last-child, .settings-model-form__heading p { margin: 2px 0 0; color: var(--text-3); font-size: 13px; }.settings-models__heading > button, .settings-local-state button { min-height: 44px; padding: 8px 13px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); color: var(--text); cursor: pointer; }
.settings-local-state { min-height: 110px; padding: 24px 16px; color: var(--text-2); }.settings-local-state.is-error { color: var(--danger); }.settings-local-state p { margin: 4px 0 12px; }
.settings-model-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; padding: 12px; }.settings-model-list article { min-width: 0; padding: 11px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }.settings-model-list article.is-active { border-color: color-mix(in srgb, var(--info) 55%, var(--border)); }.settings-model-list article > div:first-child, .settings-model-form__heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }.settings-model-list h3 { margin: 0; font-size: 16px; }.settings-model-list dl { display: grid; gap: 7px; margin: 10px 0; }.settings-model-list dt { color: var(--text-3); font-size: 12px; }.settings-model-list dd { margin: 1px 0 0; overflow-wrap: anywhere; font-size: 13px; }
.settings-model-form { margin: 0 12px 12px; padding: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); scroll-margin-top: 84px; }.settings-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }.settings-form-grid label { display: grid; gap: 5px; color: var(--text-2); font-size: 13px; }.settings-form-grid label.is-wide { grid-column: 1/-1; }.settings-form-grid input { width: 100%; min-height: 44px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); }.settings-form-grid small { color: var(--text-3); font-size: 12px; }
.settings-checkbox { display: grid; grid-template-columns: 44px minmax(0, 1fr); align-items: center; gap: 8px; min-height: 52px; margin-top: 8px; cursor: pointer; }.settings-checkbox input { width: 20px; height: 20px; justify-self: center; }.settings-checkbox span, .settings-checkbox small { display: block; }.settings-checkbox small { color: var(--text-3); }
.settings-actions { display: flex; flex-wrap: wrap; gap: 8px; }.settings-actions button { min-height: 44px; padding: 7px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); cursor: pointer; }.settings-actions .primary-button { border-color: var(--accent); background: var(--accent); color: #111; }.settings-actions .danger-button { color: var(--danger); }.settings-actions button:disabled { cursor: wait; opacity: .55; }.settings-form-error { margin: 8px 0; color: var(--danger); }
.settings-empty { margin: 12px; padding: 14px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); color: var(--text-2); }.settings-state { display: inline-flex; min-height: 28px; align-items: center; padding: 2px 8px; border: 1px solid currentColor; border-radius: 99px; font-size: 12px; font-weight: 700; white-space: nowrap; }.settings-state.is-success { color: var(--info); }.settings-state.is-neutral { color: var(--text-2); }.mono { font-family: "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }
@media (max-width: 1100px) { .settings-model-list { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 720px) { .settings-models__heading, .settings-model-form__heading { align-items: stretch; flex-direction: column; }.settings-models__heading > button { width: 100%; }.settings-model-list, .settings-form-grid { grid-template-columns: 1fr; }.settings-form-grid label.is-wide { grid-column: auto; }.settings-actions button { flex: 1 1 150px; } }
</style>
