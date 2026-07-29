<script setup lang="ts">
import { computed } from 'vue'
import type { DatasourcesResponse } from '../types/settings'
import { formatDuration, sourceStatus, textValue } from '../utils/settingsFormat'

const props = defineProps<{ response: DatasourcesResponse | null; loading: boolean; error: string }>()
const emit = defineEmits<{ retry: [] }>()
const rows = computed(() => Array.isArray(props.response?.sources) ? props.response.sources : null)
const fields = computed(() => props.response?.field_contract?.fields && typeof props.response.field_contract.fields === 'object'
  ? Object.entries(props.response.field_contract.fields)
  : [])
const coreKeys = new Set(['tdx_api', 'tencent', 'universe'])
const coreDown = computed(() => rows.value?.filter((row) => row.status === 'down' && coreKeys.has(String(row.key || ''))) || [])
const degraded = computed(() => rows.value?.filter((row) => ['down', 'degraded'].includes(String(row.status || ''))) || [])
const idle = computed(() => rows.value?.filter((row) => row.status === 'idle') || [])
</script>

<template>
  <section id="settings-data" class="settings-data panel" aria-labelledby="settings-data-title">
    <header class="settings-data__heading">
      <div><p class="eyebrow">DATA GOVERNANCE</p><h2 id="settings-data-title">数据健康与部署</h2><p>状态来自被动埋点；查看本页不会把未采集冒充为失败。</p></div>
      <span v-if="response" class="mono">{{ textValue(response.ts, '检查时点未提供') }}</span>
    </header>
    <div v-if="!rows && loading" class="settings-local-state" role="status">正在读取数据源健康…</div>
    <div v-else-if="!rows" class="settings-local-state is-error" role="alert"><strong>数据健康暂不可用</strong><p>{{ error || '接口没有返回可验证的 sources 数组。' }}</p><button type="button" @click="emit('retry')">重新读取</button></div>
    <template v-else-if="response">
      <div class="settings-data__summary">
        <article :class="coreDown.length ? 'is-danger' : 'is-success'"><span>基础链路</span><strong>{{ coreDown.length ? `${coreDown.length} 个最近失败` : (rows.length ? '未发现已知失败' : '真实空来源列表') }}</strong><small>TDX API / 腾讯 / 股票池</small></article>
        <article :class="degraded.length ? 'is-warning' : 'is-success'"><span>降级与失败</span><strong>{{ degraded.length }} 个</strong><small>只影响对应字段或模块</small></article>
        <article class="is-neutral"><span>尚未采集</span><strong>{{ idle.length }} 个</strong><small>idle 不代表不可用</small></article>
        <article :class="response.universe?.stale || response.universe?.refresh_required ? 'is-warning' : 'is-neutral'"><span>全 A 股股票池</span><strong>{{ response.universe?.count == null ? '未提供' : `${response.universe.count.toLocaleString('zh-CN')} 只` }}</strong><small>{{ response.universe?.stale ? '陈旧快照' : textValue(response.universe?.as_of, '数据日期未提供') }}</small></article>
      </div>
      <div v-if="response.deployment" class="settings-deployment">
        <div><span>配置拓扑</span><strong>{{ textValue(response.deployment.configured) }}</strong></div>
        <div><span>实际部署</span><strong>{{ textValue(response.deployment.profile) }}</strong></div>
        <div><span>公开源路由</span><strong>{{ textValue(response.deployment.china_public_route) }}</strong></div>
        <p>{{ textValue(response.deployment.public_note, '未提供部署路由说明') }}</p>
      </div>
      <div v-if="rows.length" class="settings-table-wrap" tabindex="0" aria-label="数据源健康表，可横向滚动">
        <table><thead><tr><th>字段链路</th><th>供应商</th><th>用途</th><th>角色</th><th>状态</th><th>最近耗时</th><th>最近记录</th></tr></thead><tbody>
          <tr v-for="(row, index) in rows" :key="row.key || `${row.name}-${index}`">
            <td><strong>{{ textValue(row.name) }}</strong><small class="mono">{{ textValue(row.key, '标识未提供') }}</small></td>
            <td>{{ textValue(row.provider) }}</td><td>{{ textValue(row.feeds) }}</td><td>{{ textValue(row.role) }}</td>
            <td><span class="settings-state" :class="`is-${sourceStatus(row.status).tone}`">{{ sourceStatus(row.status).label }}</span><small>{{ textValue(row.note, '无补充说明') }}</small></td>
            <td class="mono">{{ formatDuration(row.latency_ms) }}</td><td class="mono">{{ textValue(row.last_ts) }}</td>
          </tr>
        </tbody></table>
      </div>
      <p v-else class="settings-empty">服务端返回真实空数组：当前没有数据源登记；未用示例链路补位。</p>
      <details v-if="fields.length" class="settings-field-contract">
        <summary>标准字段契约 · {{ textValue(response.field_contract?.schema_version) }} · {{ fields.length }} 个字段</summary>
        <div class="settings-table-wrap" tabindex="0" aria-label="字段单位与路由契约表，可横向滚动">
          <table><thead><tr><th>字段</th><th>业务含义</th><th>单位 / 值语义</th><th>主备路由</th><th>新鲜度门槛</th><th>批次范围</th></tr></thead><tbody>
            <tr v-for="([field, meta]) in fields" :key="field"><td><code>{{ field }}</code></td><td>{{ textValue(meta.meaning) }}</td><td>{{ textValue(meta.unit) }}<small>{{ textValue(meta.value_semantics) }}</small></td><td>{{ Array.isArray(meta.route) && meta.route.length ? meta.route.join(' → ') : '未提供' }}</td><td><template v-if="meta.live_max_age_seconds != null">实时 ≤ {{ meta.live_max_age_seconds }}s</template><template v-else>未提供</template><small v-if="meta.delayed_max_age_seconds != null">延迟 ≤ {{ meta.delayed_max_age_seconds }}s</small></td><td>{{ textValue(meta.batch_scope) }}</td></tr>
          </tbody></table>
        </div>
      </details>
    </template>
  </section>
</template>

<style scoped>
.settings-data { min-width: 0; overflow: hidden; }
.settings-data__heading { display: flex; min-height: 72px; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--border); }
.settings-data__heading h2 { margin: 0; font-size: 19px; }.settings-data__heading p:last-child { margin: 2px 0 0; color: var(--text-3); font-size: 13px; }.settings-data__heading > span { color: var(--text-3); font-size: 12px; }
.settings-local-state { min-height: 110px; padding: 24px 16px; color: var(--text-2); }.settings-local-state.is-error { color: var(--danger); }.settings-local-state p { margin: 4px 0 12px; }.settings-local-state button { min-height: 44px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); color: var(--text); cursor: pointer; }
.settings-data__summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; padding: 12px; }.settings-data__summary article { min-width: 0; padding: 10px; border: 1px solid var(--border); border-left: 3px solid var(--border-strong); border-radius: 6px; background: var(--surface-2); }.settings-data__summary article.is-success { border-left-color: var(--info); }.settings-data__summary article.is-warning { border-left-color: var(--warning); }.settings-data__summary article.is-danger { border-left-color: var(--danger); }.settings-data__summary span, .settings-data__summary small { display: block; color: var(--text-3); font-size: 12px; }.settings-data__summary strong { display: block; margin: 3px 0; font-size: 16px; }
.settings-deployment { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 0 12px 12px; padding: 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }.settings-deployment div { min-width: 0; }.settings-deployment span { display: block; color: var(--text-3); font-size: 12px; }.settings-deployment strong { overflow-wrap: anywhere; }.settings-deployment p { grid-column: 1/-1; margin: 0; color: var(--text-2); font-size: 13px; }
.settings-table-wrap { max-width: 100%; overflow-x: auto; margin: 0 12px 12px; border: 1px solid var(--border); border-radius: 6px; }.settings-field-contract .settings-table-wrap { margin: 8px 0 0; }table { width: 100%; min-width: 980px; border-collapse: collapse; }th, td { padding: 9px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; font-size: 13px; }th { background: var(--surface-2); color: var(--text-3); font-size: 12px; }td strong, td small { display: block; }td small { color: var(--text-3); }
.settings-state { display: inline-flex; min-height: 26px; align-items: center; padding: 2px 7px; border: 1px solid currentColor; border-radius: 99px; font-size: 12px; font-weight: 700; }.settings-state.is-success { color: var(--info); }.settings-state.is-warning { color: var(--warning); }.settings-state.is-danger { color: var(--danger); }.settings-state.is-neutral { color: var(--text-2); }
.settings-field-contract { margin: 0 12px 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 6px; }.settings-field-contract summary { min-height: 44px; cursor: pointer; font-weight: 700; line-height: 44px; }.settings-empty { margin: 0 12px 12px; padding: 14px; border: 1px solid var(--border); border-radius: 6px; color: var(--text-2); }
.mono, code { font-family: "SFMono-Regular", Consolas, monospace; font-variant-numeric: tabular-nums; }
@media (max-width: 1020px) { .settings-data__summary { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 720px) { .settings-data__heading { align-items: flex-start; flex-direction: column; }.settings-data__summary, .settings-deployment { grid-template-columns: 1fr; }.settings-deployment p { grid-column: auto; } }
</style>
