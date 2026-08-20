<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from 'vue'
import type { HoldingView } from '../types/holdings'
import { formatPercent, formatPrice } from '../utils/holdingsFormat'

defineProps<{ rows: HoldingView[]; actionRunning: boolean }>()

const emit = defineEmits<{
  replan: [clientKey: string]
  confirm: [code: string, reductionPct: number]
  remove: [clientKey: string]
}>()

const evidenceDialog = ref<HTMLDialogElement | null>(null)
const selectedEvidence = ref<HoldingView | null>(null)
let evidenceOpener: HTMLElement | null = null

function reductionTarget(row: HoldingView): number {
  return Number(row.riskState?.target_reduction_pct || 0)
}

function statusLabel(row: HoldingView): string {
  if (!row.riskPlan) return '待建立计划'
  if (row.riskState?.state === 'unsupported') return '暂不支持'
  if (row.riskState?.state === 'data_insufficient') return '数据不足'
  if (row.riskState?.state === 'pending_adjustment') return '待确认仓位调整'
  if (row.riskState?.state === 'completed_adjustment') return '已完成当前调整'
  return '监控中'
}

function triggerLabels(row: HoldingView): string[] {
  const state = row.riskState
  if (!state) return []
  return [...(state.decisive_triggers || []), ...(state.ordinary_triggers || [])]
    .map((item) => String(item.label || '').trim()).filter(Boolean)
}

async function openEvidence(row: HoldingView, event: MouseEvent) {
  selectedEvidence.value = row
  evidenceOpener = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  await nextTick()
  if (evidenceDialog.value && !evidenceDialog.value.open) evidenceDialog.value.showModal()
  evidenceDialog.value?.querySelector<HTMLElement>('.holding-evidence-dialog__close')?.focus()
}

function closeEvidence() {
  if (evidenceDialog.value?.open) evidenceDialog.value.close()
}

function handleEvidenceClosed() {
  selectedEvidence.value = null
  evidenceOpener?.focus()
  evidenceOpener = null
}

onBeforeUnmount(closeEvidence)
</script>

<template>
  <section class="holdings-risk-list" aria-labelledby="holdings-risk-title">
    <header class="holdings-risk-list__header">
      <div><p class="eyebrow">服务端确定性仓位管理</p><h2 id="holdings-risk-title">当前计划与调整目标</h2></div>
      <span>{{ rows.length }} 个持仓计划</span>
    </header>

    <article v-for="row in rows" :key="`risk-${row.clientKey}`" class="panel holding-risk-card">
      <header>
        <div class="holding-risk-card__identity">
          <strong>{{ row.name || '名称缺失' }}</strong>
          <span class="mono">{{ row.code }}</span>
          <span>{{ statusLabel(row) }}</span>
        </div>
      </header>

      <div v-if="row.riskPlan" class="holding-risk-card__body">
        <dl class="holding-risk-card__metrics">
          <div class="holding-risk-card__metric--period"><dt>计划周期</dt><dd>{{ row.riskState?.period_label || row.riskPlan.period }}<small>{{ row.riskState?.period_window || '—' }}</small></dd></div>
          <div><dt>已持有交易日</dt><dd class="mono">{{ row.riskState?.time?.trading_days_held ?? '—' }}<small>{{ row.riskState?.time?.expired ? '已到期，持续显示' : `上限 ${row.riskState?.time?.max_days ?? '—'} 日` }}</small></dd></div>
          <div><dt>当前价</dt><dd class="mono">{{ formatPrice(row.price) }}<small>{{ row.quote?.stale ? '陈旧报价' : row.quote?.source || '报价缺失' }}</small></dd></div>
          <div><dt>算法风险线</dt><dd class="mono">{{ formatPrice(row.riskState?.current_risk_line ?? null) }}<small>距风险线 {{ formatPercent(row.riskState?.risk_distance_pct ?? null, true) }}</small></dd></div>
          <div><dt>客户风险线</dt><dd class="mono">{{ formatPrice(row.riskState?.customer_risk_price ?? row.riskPlan.customer_risk_price ?? null) }}<small>触及则降低100%</small></dd></div>
          <div><dt>客户目标价</dt><dd class="mono">{{ formatPrice(row.riskState?.customer_target_price ?? row.riskPlan.customer_target_price ?? null) }}<small>{{ row.riskPlan.target_mode === 'ordinary_trigger' ? '计入一次普通触发' : '仅提醒' }}</small></dd></div>
          <div><dt>1R / 当前R</dt><dd class="mono">{{ formatPrice(row.riskState?.initial_r ?? null) }}<small>{{ row.riskState?.r_multiple == null ? '—' : `${row.riskState.r_multiple}R` }}</small></dd></div>
          <div><dt>已确认降低</dt><dd class="mono">{{ row.riskPlan.confirmed_reduction_pct || 0 }}%<small>目标只升不降</small></dd></div>
          <div class="holding-risk-card__evidence-summary">
            <dt>当前依据</dt>
            <dd>
              <span>{{ row.riskState?.action || '等待首次评估' }}</span>
              <small v-if="row.riskState?.target_reduction_pct != null">
                累计降低目标 {{ row.riskState.target_reduction_pct }}% · 保留 {{ row.riskState.retain_pct }}%
              </small>
              <small v-else>尚无累计调整目标</small>
              <button
                type="button"
                class="secondary-button holding-risk-card__evidence-trigger"
                :aria-label="`查看${row.name || row.code}的当前依据详细`"
                @click="openEvidence(row, $event)"
              >查看详细</button>
            </dd>
          </div>
        </dl>
      </div>

      <div v-else class="holding-risk-card__missing" role="status">
        这是一条升级前持仓。系统不会猜测买入日期和周期，请建立计划后再启用仓位管理。
      </div>

      <footer>
        <button type="button" class="secondary-button" :disabled="actionRunning" @click="emit('replan', row.clientKey)">{{ row.riskPlan ? '重新规划' : '建立计划' }}</button>
        <button
          v-if="reductionTarget(row) > Number(row.riskPlan?.confirmed_reduction_pct || 0)"
          type="button" class="primary-button" :disabled="actionRunning"
          @click="emit('confirm', row.code, reductionTarget(row))"
        >{{ reductionTarget(row) === 100 ? '已降低100%，结束关注' : '已调整至目标仓位' }}</button>
        <button type="button" class="secondary-button" :disabled="actionRunning" @click="emit('remove', row.clientKey)">取消关注</button>
      </footer>
    </article>
  </section>

  <Teleport to="body">
    <dialog
      ref="evidenceDialog"
      class="holding-evidence-dialog"
      aria-modal="true"
      aria-labelledby="holding-evidence-dialog-title"
      @cancel.prevent="closeEvidence"
      @close="handleEvidenceClosed"
    >
      <template v-if="selectedEvidence">
        <header>
          <div>
            <p class="eyebrow">仓位管理详细依据</p>
            <h2 id="holding-evidence-dialog-title">{{ selectedEvidence.name || '名称缺失' }} <span class="mono">{{ selectedEvidence.code }}</span></h2>
          </div>
          <button type="button" class="secondary-button holding-evidence-dialog__close" @click="closeEvidence">关闭</button>
        </header>
        <div class="holding-evidence-dialog__body">
          <section aria-labelledby="holding-evidence-decision-title">
            <h3 id="holding-evidence-decision-title">仓位建议</h3>
            <strong>{{ selectedEvidence.riskState?.action || '等待首次评估' }}</strong>
            <p v-if="selectedEvidence.riskState?.target_reduction_pct != null">
              累计降低目标 {{ selectedEvidence.riskState.target_reduction_pct }}% · 保留 {{ selectedEvidence.riskState.retain_pct }}%
            </p>
          </section>
          <section aria-labelledby="holding-evidence-detail-title">
            <h3 id="holding-evidence-detail-title">触发依据</h3>
            <ul v-if="triggerLabels(selectedEvidence).length">
              <li v-for="label in triggerLabels(selectedEvidence)" :key="label">{{ label }}</li>
            </ul>
            <p v-else-if="selectedEvidence.riskState?.near_risk_line">已接近算法风险线，暂停扩大仓位并等待确认；不计入普通触发。</p>
            <p v-else>{{ selectedEvidence.riskState?.target_note || selectedEvidence.riskState?.data_note || selectedEvidence.riskState?.missing?.join('；') || '当前没有仓位调整触发。' }}</p>
            <p v-if="selectedEvidence.riskState?.execution_constraints?.length">执行限制：{{ selectedEvidence.riskState.execution_constraints.join('；') }}</p>
          </section>
          <small>
            评估：{{ selectedEvidence.riskState?.evaluated_at || '尚未评估' }} · 数据：{{ selectedEvidence.riskState?.data_as_of || '—' }}<template v-if="selectedEvidence.riskState?.stale"> · 状态已陈旧</template>
          </small>
        </div>
      </template>
    </dialog>
  </Teleport>
</template>

<style scoped>
.holdings-risk-list { display: grid; gap: 6px; }
.holdings-risk-list__header { display: flex; min-height: 52px; align-items: center; justify-content: space-between; gap: 10px; padding: 6px 12px; }
.holdings-risk-list__header h2 { margin: 0; font-size: 18px; }
.holdings-risk-list__header > span { color: var(--text-3); font-size: 12px; }
.holding-risk-card { overflow: hidden; }
.holding-risk-card > header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 9px 12px; border-bottom: 1px solid var(--border); }
.holding-risk-card__identity { display: flex; min-width: 0; align-items: baseline; gap: 8px; }
.holding-risk-card__identity > span:last-child { padding-left: 8px; border-left: 1px solid var(--border); color: var(--text-2); font-size: 13px; }
.holding-risk-card__body { padding: 8px 12px; }
.holding-risk-card__metrics { display: grid; grid-template-columns: repeat(8, minmax(90px, 1fr)) minmax(260px, 2fr); align-items: stretch; gap: 6px; margin: 0; }
.holding-risk-card__metrics > div { min-width: 0; padding: 7px 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.holding-risk-card__metrics dt { color: var(--text-3); font-size: 12px; }
.holding-risk-card__metrics dd { margin: 2px 0 0; font-weight: 720; overflow-wrap: anywhere; }
.holding-risk-card__metrics small { display: block; margin-top: 2px; color: var(--text-3); font-size: 11px; font-weight: 500; }
.holding-risk-card__metrics > .holding-risk-card__evidence-summary { display: grid; align-content: center; }
.holding-risk-card__evidence-summary dd { display: grid; grid-template: "value action" auto "meta action" auto / minmax(0, 1fr) auto; align-items: center; column-gap: 8px; }
.holding-risk-card__evidence-summary dd > span { grid-area: value; }
.holding-risk-card__evidence-summary small { grid-area: meta; }
.holding-risk-card__evidence-summary button { grid-area: action; min-width: 84px; min-height: 44px; align-self: center; }
.holding-risk-card__missing { padding: 16px 14px; color: var(--text-2); }
.holding-risk-card > footer { display: flex; justify-content: flex-end; gap: 8px; padding: 7px 12px; border-top: 1px solid var(--border); }
.holding-evidence-dialog { width: min(680px, calc(100vw - 32px)); max-height: min(720px, calc(100dvh - 32px)); padding: 0; overflow: hidden; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--surface); color: var(--text); }
.holding-evidence-dialog::backdrop { background: rgb(5 11 20 / 60%); }
.holding-evidence-dialog > header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 14px; border-bottom: 1px solid var(--border); }
.holding-evidence-dialog h2, .holding-evidence-dialog h3, .holding-evidence-dialog p { margin: 0; }
.holding-evidence-dialog h2 { font-size: 18px; }
.holding-evidence-dialog h2 span { font-size: 14px; }
.holding-evidence-dialog h3 { margin-bottom: 6px; font-size: 15px; }
.holding-evidence-dialog__body { display: grid; max-height: calc(100dvh - 106px); gap: 8px; padding: 12px 14px; overflow-y: auto; }
.holding-evidence-dialog__body section { padding: 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); }
.holding-evidence-dialog__body section > p, .holding-evidence-dialog__body ul { margin: 5px 0 0; color: var(--text-2); }
.holding-evidence-dialog__body ul { padding-left: 20px; }
.holding-evidence-dialog__body > small { color: var(--text-3); }
@media (max-width: 1280px) {
  .holding-risk-card__metrics { grid-template-columns: repeat(4, minmax(118px, 1fr)); }
  .holding-risk-card__evidence-summary { grid-column: 1 / -1; }
}
@media (max-width: 760px) {
  .holding-risk-card__metrics { grid-template-columns: repeat(2, minmax(118px, 1fr)); }
}
@media (max-width: 720px) {
  .holding-risk-card > header { align-items: flex-start; flex-direction: column; }
  .holding-risk-card__metrics { grid-template-columns: minmax(0, 1fr); }
  .holding-risk-card > footer { align-items: stretch; flex-direction: column; }
  .holding-risk-card > footer button { width: 100%; min-height: 44px; }
}
@media (max-width: 520px) {
  .holding-evidence-dialog { width: calc(100vw - 16px); max-height: calc(100dvh - 16px); }
}
</style>
