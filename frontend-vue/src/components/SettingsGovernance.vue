<script setup lang="ts">
import { computed } from 'vue'
import type { AgentRunsResponse, SettingsView, StatusResponse } from '../types/settings'
import { textValue } from '../utils/settingsFormat'

const props = defineProps<{
  status: StatusResponse | null
  settings: SettingsView | null
  runs: AgentRunsResponse | null
  loading: { status: boolean; runs: boolean }
  errors: { status: string; runs: string }
}>()
const emit = defineEmits<{ retry: [] }>()
const rows = computed(() => Array.isArray(props.runs?.items) ? props.runs.items : null)
const capability = computed(() => props.status?.hermes?.agent_capability)
const modelConfigured = computed(() => props.status?.model_connection?.configured === true)

function runState(run: NonNullable<AgentRunsResponse['items']>[number]) {
  if (run.safe_to_use === true) return { label: '完整可用', tone: 'success' }
  if (run.partial_output === true || run.status === 'partial') return { label: '部分输出 · 不可用', tone: 'danger' }
  if (run.status === 'failed' || run.status === 'blocked') return { label: '运行失败 / 阻断', tone: 'danger' }
  if (run.status === 'cancelled') return { label: '已取消 · 不可用', tone: 'warning' }
  return { label: textValue(run.status), tone: 'neutral' }
}
</script>

<template>
  <section id="settings-governance" class="settings-governance panel" aria-labelledby="settings-governance-title">
    <header class="settings-governance__heading"><div><p class="eyebrow">VERSION & GOVERNANCE</p><h2 id="settings-governance-title">版本、安全与运行边界</h2><p>只展示脱敏配置状态、权限证明和运行终态，不返回 prompt、模型原文或凭据。</p></div></header>
    <div class="settings-governance__grid">
      <article class="settings-subpanel">
        <h3>系统信息</h3>
        <div v-if="!status && loading.status" class="settings-local-state" role="status">正在读取系统信息…</div>
        <div v-else-if="!status" class="settings-local-state is-error" role="alert">{{ errors.status || '系统状态接口未返回。' }}<button type="button" @click="emit('retry')">重新读取</button></div>
        <dl v-else><div v-if="status.ui?.hide_client_info !== true"><dt>客户</dt><dd>{{ textValue(status.client?.name) }}</dd></div><div><dt>版本</dt><dd class="mono">{{ textValue(status.version) }}</dd></div><div><dt>系统交易日期</dt><dd class="mono">{{ textValue(status.date) }}</dd></div><div><dt>数据策略</dt><dd>{{ textValue(status.data_source) }}</dd></div><div><dt>自定义模型</dt><dd>{{ modelConfigured ? '已配置（可选）' : '未配置（可选）' }}</dd></div></dl>
      </article>
      <article class="settings-subpanel">
        <h3>安全约束</h3>
        <dl><div><dt>模型密钥</dt><dd>仅显示“已配置 / 未配置”，明文不回显</dd></div><div><dt>受限工具集</dt><dd>{{ settings?.agent_require_restricted_toolsets === true ? '服务端要求收敛' : (settings?.agent_require_restricted_toolsets === false ? '服务端未要求' : '状态未提供') }}</dd></div><div><dt>Profile 证明</dt><dd>{{ settings?.agent_require_profile_attestation === true ? '服务端要求' : (settings?.agent_require_profile_attestation === false ? '服务端未要求' : '状态未提供') }}</dd></div><div><dt>产品边界</dt><dd>仅作决策支持和本地模拟，不连接券商真实下单</dd></div></dl>
      </article>
    </div>
    <details class="settings-agent-details">
      <summary>可选集成 / Agent 高级诊断</summary>
      <div class="settings-agent-details__body">
        <p class="settings-note">Hermes/模型是可选增强。未配置是正常状态；一旦调用 Agent，权限预检仍必须 fail closed。</p>
        <dl class="settings-agent-summary"><div><dt>运行模式</dt><dd>{{ textValue(capability?.runtime_mode) }}</dd></div><div><dt>权限结论</dt><dd>{{ textValue(capability?.reason) }}</dd></div><div><dt>Profile</dt><dd>{{ textValue(capability?.profile_attestation) }}</dd></div><div><dt>进程证明</dt><dd>{{ textValue(capability?.process_attestation) }}</dd></div></dl>
        <div v-if="!rows && loading.runs" class="settings-local-state" role="status">正在读取脱敏运行账本…</div>
        <div v-else-if="!rows" class="settings-local-state is-error" role="alert">{{ errors.runs || '运行账本未返回可验证的 items 数组。' }}</div>
        <p v-else-if="rows.length === 0" class="settings-empty">服务端返回真实空数组：尚无 Agent 运行记录。</p>
        <div v-else class="settings-table-wrap" tabindex="0" aria-label="Agent 运行账本表，可横向滚动"><table><thead><tr><th>运行 ID</th><th>开始时间</th><th>用途</th><th>模型</th><th>权限快照</th><th>尝试</th><th>工具事件</th><th>终态</th></tr></thead><tbody><tr v-for="run in rows" :key="String(run.run_id || run.started_at || 'agent-run')"><td><code>{{ textValue(run.run_id).slice(0, 12) }}</code></td><td class="mono">{{ textValue(run.started_at) }}</td><td>{{ textValue(run.purpose) }}</td><td>{{ textValue(run.model) }}</td><td>{{ textValue(run.capability_status) }}</td><td>{{ run.attempts == null ? '未提供' : run.attempts }}</td><td>{{ run.tool_events == null ? '未提供' : run.tool_events }}</td><td><span class="settings-state" :class="`is-${runState(run).tone}`">{{ runState(run).label }}</span><small>{{ textValue(run.error_code || run.finish_reason) }}</small></td></tr></tbody></table></div>
      </div>
    </details>
  </section>
</template>

<style scoped>
.settings-governance { min-width: 0; overflow: hidden; }.settings-governance__heading { min-height: 72px; padding: 12px 16px; border-bottom: 1px solid var(--border); }.settings-governance__heading h2, .settings-subpanel h3 { margin: 0; }.settings-governance__heading h2 { font-size: 19px; }.settings-governance__heading p:last-child { margin: 2px 0 0; color: var(--text-3); font-size: 13px; }.settings-governance__grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 12px; }.settings-subpanel { min-width: 0; padding: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }.settings-subpanel dl, .settings-agent-summary { display: grid; gap: 7px; margin: 10px 0 0; }.settings-subpanel dl div, .settings-agent-summary div { display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 8px; padding-bottom: 7px; border-bottom: 1px solid var(--border); }.settings-subpanel dt, .settings-agent-summary dt { color: var(--text-3); }.settings-subpanel dd, .settings-agent-summary dd { margin: 0; overflow-wrap: anywhere; }
.settings-agent-details { margin: 0 12px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }.settings-agent-details summary { min-height: 44px; padding: 0 12px; cursor: pointer; font-weight: 700; line-height: 44px; }.settings-agent-details[open] summary { border-bottom: 1px solid var(--border); }.settings-agent-details__body { padding: 12px; }.settings-note, .settings-empty { margin: 0 0 10px; padding: 9px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text-2); font-size: 13px; }.settings-agent-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }.settings-agent-summary div { display: block; padding: 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); }.settings-agent-summary dd { margin-top: 2px; }
.settings-local-state { min-height: 82px; padding: 16px 0; color: var(--text-2); }.settings-local-state.is-error { color: var(--danger); }.settings-local-state button { display: block; min-height: 44px; margin-top: 8px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); cursor: pointer; }.settings-table-wrap { max-width: 100%; overflow-x: auto; border: 1px solid var(--border); border-radius: 6px; }table { width: 100%; min-width: 980px; border-collapse: collapse; }th, td { padding: 9px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; font-size: 13px; }th { background: var(--surface); color: var(--text-3); font-size: 12px; }td small { display: block; color: var(--text-3); }
.settings-state { display: inline-flex; min-height: 28px; align-items: center; padding: 2px 8px; border: 1px solid currentColor; border-radius: 99px; font-size: 12px; font-weight: 700; }.settings-state.is-success { color: var(--info); }.settings-state.is-warning { color: var(--warning); }.settings-state.is-danger { color: var(--danger); }.settings-state.is-neutral { color: var(--text-2); }.mono, code { font-family: "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }
@media (max-width: 820px) { .settings-governance__grid, .settings-agent-summary { grid-template-columns: 1fr; } }
</style>
