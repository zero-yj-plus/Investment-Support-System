<script setup lang="ts">
import { computed } from 'vue'
import type { CompanyResearchRunState } from '../types/companyResearch'

const props = defineProps<{
  state: CompanyResearchRunState
  code: string
  runError: string
  modelReady: boolean
  evidenceLoading: boolean
}>()

const conclusion = computed(() => {
  if (props.state === 'loading') return '证据与服务端报告正在核验，暂不形成新结论'
  if (props.state === 'complete') return '服务端治理后的公司研究已完整返回'
  if (props.state === 'partial') return '研究流不完整，正文已隔离，不作为完整结论'
  if (props.state === 'error' && props.code) return '研究正文未形成；结构化证据仍可独立查看'
  return '尚未选择公司，当前没有研究结论'
})

const action = computed(() => {
  if (!props.code) return '输入 6 位代码，先核验公司证据'
  if (props.state === 'loading') return '等待证据完成；必要时可停止生成'
  if (props.state === 'complete') return '先阅读交易边界，再到个股诊断复核'
  if (props.state === 'partial') return '重新生成完整报告，不保存隔离正文'
  return props.modelReady ? '重新生成，或仅查看已返回的官方证据' : '先检查模型连接；官方证据仍可刷新'
})
</script>

<template>
  <section class="company-summary panel" aria-labelledby="company-summary-title">
    <div class="company-summary__verdict" role="status" aria-live="polite">
      <p class="eyebrow">当前结论</p>
      <h2 id="company-summary-title">{{ conclusion }}</h2>
      <p v-if="runError">{{ runError }}</p>
      <p v-else>{{ evidenceLoading ? '证据分区正在独立加载。' : '结论状态与证据状态分开呈现。' }}</p>
    </div>
    <dl>
      <div><dt>现在应该做什么</dt><dd>{{ action }}</dd></div>
      <div><dt>风险等级</dt><dd>本页接口未独立提供，不从报告文本推导</dd></div>
      <div><dt>仓位上限</dt><dd>未取得结构化字段，保持缺失，不补成 0%</dd></div>
    </dl>
    <a v-if="code" class="company-diagnosis-link" :href="`#/research?tab=diagnosis&code=${encodeURIComponent(code)}`">
      前往个股诊断复核 <span class="mono">{{ code }}</span>
    </a>
  </section>
</template>

<style scoped>
.company-summary { display: grid; grid-template-columns: minmax(320px, 1.2fr) minmax(420px, 1fr) auto; align-items: stretch; overflow: hidden; }.company-summary__verdict { min-width: 0; padding: 14px 16px; border-right: 1px solid var(--border); }.company-summary__verdict h2 { margin: 0; font-size: clamp(19px, 1.65vw, 24px); line-height: 1.45; }.company-summary__verdict > p:last-child { margin: 7px 0 0; color: var(--text-2); font-size: 13px; }.company-summary dl { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0; }.company-summary dl div { min-width: 0; padding: 13px 12px; border-right: 1px solid var(--border); }.company-summary dt { color: var(--text-3); font-size: 12px; font-weight: 700; }.company-summary dd { margin: 5px 0 0; color: var(--text); font-size: 14px; font-weight: 680; line-height: 1.55; }.company-diagnosis-link { display: inline-flex; min-width: 168px; min-height: 44px; align-items: center; justify-content: center; gap: 6px; align-self: center; margin: 12px; padding: 8px 12px; border: 1px solid var(--accent); border-radius: var(--radius-sm); color: var(--accent); font-size: 13px; font-weight: 720; text-decoration: none; }.company-diagnosis-link:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); }
@media (max-width: 1220px) { .company-summary { grid-template-columns: minmax(0, 1fr) auto; }.company-summary dl { grid-column: 1 / -1; grid-row: 2; border-top: 1px solid var(--border); }.company-summary__verdict { border-right: 1px solid var(--border); } }
@media (max-width: 720px) { .company-summary { grid-template-columns: minmax(0, 1fr); }.company-summary__verdict { border-right: 0; }.company-summary dl { grid-column: 1; grid-template-columns: minmax(0, 1fr); }.company-summary dl div { border-right: 0; border-bottom: 1px solid var(--border); }.company-diagnosis-link { width: calc(100% - 24px); } }
</style>
