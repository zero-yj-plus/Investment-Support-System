<script setup lang="ts">
import type { DeliveryReceipt, ReportRecord } from '../types/reports'

defineProps<{
  report: ReportRecord
  receipt: DeliveryReceipt | null
}>()

function versionLabel(report: ReportRecord): string {
  const version = report.plan_version ?? report.report_version
  return version == null ? '未提供' : `v${version}`
}

function fingerprint(report: ReportRecord): string {
  return report.content_hash ? report.content_hash.slice(0, 12) : '未提供'
}
</script>

<template>
  <section class="panel report-governance" aria-labelledby="report-governance-title">
    <header>
      <div>
        <p class="eyebrow">数据健康与治理</p>
        <h2 id="report-governance-title">版本、证据与交付留痕</h2>
      </div>
      <span class="mono">{{ report.schema_version || 'schema 未提供' }}</span>
    </header>
    <dl>
      <div><dt>报告版本</dt><dd class="mono">{{ versionLabel(report) }}</dd></div>
      <div><dt>内容指纹</dt><dd class="mono">{{ fingerprint(report) }}</dd></div>
      <div><dt>内容身份</dt><dd class="mono">{{ report.content_id || '未提供' }}</dd></div>
      <div><dt>价格校验</dt><dd>{{ report.claim_validation?.status || '未提供' }}</dd></div>
      <div><dt>建议治理</dt><dd>{{ report.advice_governance?.status || '未提供' }}</dd></div>
      <div><dt>数字质检</dt><dd>{{ report.eval?.status || (report.eval ? '已记录' : '未提供') }}</dd></div>
      <div><dt>交付渠道</dt><dd>{{ receipt?.channel || '未提供' }}</dd></div>
      <div><dt>交付尝试</dt><dd class="mono">{{ receipt?.attempt ?? '未提供' }}</dd></div>
    </dl>
    <p>报告、版本和交付回执按追加式事实展示；页面不覆盖历史定稿，也不把平台接受写成最终送达或已读。</p>
  </section>
</template>

<style scoped>
.report-governance { min-width: 0; overflow: hidden; }
.report-governance > header { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.report-governance h2 { margin: 0; font-size: 18px; }
.report-governance > header > .mono { color: var(--text-3); font-size: 12px; }
.report-governance dl { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; }
.report-governance dl div { min-width: 0; padding: 10px 12px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.report-governance dl div:nth-child(4n) { border-right: 0; }
.report-governance dt { color: var(--text-3); font-size: 12px; }
.report-governance dd { margin: 3px 0 0; color: var(--text-2); overflow-wrap: anywhere; }
.report-governance > p { margin: 0; padding: 10px 12px; color: var(--text-3); font-size: 12px; line-height: 1.6; }
@media (max-width: 900px) {
  .report-governance dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .report-governance dl div:nth-child(2n) { border-right: 0; }
}
@media (max-width: 520px) {
  .report-governance > header { align-items: flex-start; flex-direction: column; }
  .report-governance dl { grid-template-columns: minmax(0, 1fr); }
  .report-governance dl div { border-right: 0; }
}
</style>

