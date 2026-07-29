<script setup lang="ts">
import { computed } from 'vue'
import type { DeliveryReceipt, ReportRecord } from '../types/reports'

const props = defineProps<{
  report: ReportRecord
  receipt: DeliveryReceipt | null
  receiptError?: string
}>()

const command = computed(() => props.report.cockpit?.command)
const riskLevel = computed(() => {
  const value = Number(command.value?.risk_level)
  return Number.isInteger(value) && value >= 1 && value <= 5 ? value : null
})
const forbidden = computed(() => command.value?.forbid?.filter(Boolean) || [])
const delivery = computed(() => {
  const status = props.receipt?.status
  const labels: Record<string, { label: string; detail: string; tone: string }> = {
    queued: { label: '已提交', detail: '已进入交付队列，尚未确认平台接受。', tone: 'neutral' },
    sending: { label: '提交中', detail: '平台响应尚未确认，请勿重复提交。', tone: 'warning' },
    accepted: { label: '平台接受', detail: '平台已接受请求，但这不等于最终送达或已读。', tone: 'info' },
    delivered: { label: '已送达', detail: '交付回执明确记录为 delivered。', tone: 'positive' },
    partial: { label: '部分接受', detail: '部分目标成功，仍有目标失败或状态不明。', tone: 'warning' },
    unknown: { label: '状态未知', detail: '可能已发送，必须人工核验后再决定是否重试。', tone: 'warning' },
    failed_retryable: { label: '可复核重试', detail: '当前交付失败，需按服务端治理流程复核后重试。', tone: 'negative' },
    failed_terminal: { label: '交付失败', detail: '平台未接受或通道不可用。', tone: 'negative' },
    acknowledged: { label: '人工已核验接收', detail: '外部平台已由人工核验；不等同于收件人已读。', tone: 'positive' },
  }
  if (status && labels[status]) return labels[status]
  if (props.report.pushed) {
    return { label: '历史推送记录', detail: '旧记录仅标记 pushed，未提供平台接受或送达层级。', tone: 'neutral' }
  }
  return { label: '未提交推送', detail: '当前报告没有匹配的追加式交付回执。', tone: 'neutral' }
})
const claimPassed = computed(() => props.report.claim_validation?.status === 'passed' && props.report.claim_validation?.ok !== false)
const advicePassed = computed(() => props.report.advice_governance?.status === 'passed' && props.report.advice_governance?.ok !== false)
</script>

<template>
  <section class="panel report-center-summary" aria-labelledby="report-summary-title">
    <div class="report-center-summary__lead">
      <p class="eyebrow">当前定稿结论</p>
      <div class="report-center-summary__title">
        <span v-if="riskLevel != null" class="risk-level-badge" :class="`risk-${riskLevel}`">风险 {{ riskLevel }} 级</span>
        <span v-else class="risk-level-badge risk-missing">风险未提供</span>
        <h2 id="report-summary-title">{{ command?.level_name || command?.action_label || '服务端未提供结构化结论' }}</h2>
      </div>
      <p class="report-center-summary__action">{{ command?.today_action || '请阅读正式报告正文；前端不从 Markdown 重新推导交易结论。' }}</p>
    </div>

    <dl class="report-center-summary__guardrails">
      <div>
        <dt>仓位边界</dt>
        <dd class="mono">{{ command?.position_cap || '未提供' }}</dd>
      </div>
      <div>
        <dt>第一原则</dt>
        <dd>{{ command?.first_principle || '服务端未提供；不依据缺失字段扩大风险暴露。' }}</dd>
      </div>
      <div>
        <dt>定格时点</dt>
        <dd class="mono">{{ report.cockpit?.as_of || report.created_at || '未提供' }}</dd>
      </div>
    </dl>

    <div class="report-center-summary__lower">
      <div>
        <strong>禁止条件</strong>
        <div v-if="forbidden.length" class="report-center-summary__rules">
          <span v-for="item in forbidden" :key="item">{{ item }}</span>
        </div>
        <p v-else>结构化禁止条件未提供；不能据此视为“无禁止条件”。</p>
      </div>
      <div class="report-center-delivery" :class="`is-${delivery.tone}`">
        <span>交付状态</span>
        <strong>{{ delivery.label }}</strong>
        <p>{{ delivery.detail }}</p>
        <p v-if="receiptError" class="report-center-delivery__error" role="status">
          回执读取失败：{{ receiptError }}；以上仅保留报告自身已有的推送标记，不能补充平台回执结论。
        </p>
      </div>
    </div>

    <div class="report-center-summary__checks" aria-label="报告治理状态">
      <span :class="claimPassed ? 'is-positive' : 'is-warning'">价格校验：{{ claimPassed ? '通过' : '未通过或未提供' }}</span>
      <span :class="advicePassed ? 'is-positive' : 'is-warning'">建议治理：{{ advicePassed ? '通过' : '未通过或未提供' }}</span>
      <span>输入谱系：{{ report.input_lineage || report.input_context_hash ? '已记录' : '未提供' }}</span>
      <span v-if="report.stale">陈旧数据：是</span>
      <span v-else-if="report.degraded">数据状态：降级</span>
      <span v-else>陈旧标记：接口未提供</span>
    </div>
  </section>
</template>

<style scoped>
.report-center-summary { min-width: 0; overflow: hidden; }
.report-center-summary__lead { padding: 16px 18px 12px; }
.report-center-summary__title { display: flex; min-width: 0; flex-wrap: wrap; align-items: center; gap: 8px; }
.report-center-summary__title h2 { flex: 1 1 420px; margin: 0; font-size: clamp(19px, 1.6vw, 24px); line-height: 1.45; }
.report-center-summary__action { margin: 10px 0 0; color: var(--text); font-size: 18px; font-weight: 720; line-height: 1.65; }
.report-center-summary__guardrails { display: grid; grid-template-columns: .65fr 1.5fr .85fr; margin: 0 18px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.report-center-summary__guardrails div { min-width: 0; padding: 10px 12px; }
.report-center-summary__guardrails div + div { border-left: 1px solid var(--border); }
.report-center-summary__guardrails dt { color: var(--text-3); font-size: 12px; font-weight: 700; }
.report-center-summary__guardrails dd { margin: 3px 0 0; color: var(--text-2); overflow-wrap: anywhere; }
.report-center-summary__lower { display: grid; grid-template-columns: minmax(0, 1fr) minmax(240px, .52fr); gap: 12px; padding: 12px 18px; }
.report-center-summary__lower > div { min-width: 0; }
.report-center-summary__lower strong { font-size: 13px; }
.report-center-summary__lower p { margin: 5px 0 0; color: var(--text-3); font-size: 13px; line-height: 1.55; }
.report-center-summary__rules { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.report-center-summary__rules span { padding: 4px 8px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-2); color: var(--text-2); font-size: 13px; }
.report-center-delivery { padding-left: 12px; border-left: 3px solid var(--border-strong); }
.report-center-delivery > span { display: block; color: var(--text-3); font-size: 12px; }
.report-center-delivery.is-info { border-color: var(--info); }
.report-center-delivery.is-positive { border-color: var(--risk-2); }
.report-center-delivery.is-warning { border-color: var(--warning); }
.report-center-delivery.is-negative { border-color: var(--danger); }
.report-center-delivery__error { color: var(--warning) !important; }
.report-center-summary__checks { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 18px 14px; border-top: 1px solid var(--border); }
.report-center-summary__checks span { padding: 3px 7px; border: 1px solid var(--border); border-radius: 4px; color: var(--text-3); font-size: 12px; }
.report-center-summary__checks .is-positive { color: var(--accent); font-weight: 700; }
.report-center-summary__checks .is-warning { color: var(--warning); }
@media (max-width: 820px) {
  .report-center-summary__guardrails { grid-template-columns: minmax(0, 1fr); margin-inline: 12px; }
  .report-center-summary__guardrails div + div { border-top: 1px solid var(--border); border-left: 0; }
  .report-center-summary__lower { grid-template-columns: minmax(0, 1fr); padding-inline: 12px; }
  .report-center-delivery { padding-top: 8px; padding-left: 0; border-top: 3px solid var(--border-strong); border-left: 0; }
  .report-center-summary__checks { padding-inline: 12px; }
}
@media (max-width: 520px) {
  .report-center-summary__lead { padding-inline: 12px; }
  .report-center-summary__title { align-items: flex-start; flex-direction: column; }
  .report-center-summary__title h2 { flex: none; }
  .report-center-summary__action { font-size: 18px; }
}
</style>
