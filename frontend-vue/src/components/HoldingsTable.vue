<script setup lang="ts">
import type { HoldingView } from '../types/holdings'
import { formatMoney, formatPercent, formatPrice, pnlTone } from '../utils/holdingsFormat'

defineProps<{ rows: HoldingView[] }>()

const emit = defineEmits<{
  update: [clientKey: string, field: 'code' | 'cost' | 'shares' | 'note', value: string]
  remove: [clientKey: string]
}>()

function inputValue(event: Event): string {
  return (event.target as HTMLInputElement).value
}
</script>

<template>
  <section class="panel holdings-detail" aria-labelledby="holdings-detail-title">
    <header class="holdings-detail__header">
      <div>
        <p class="eyebrow">结构化明细</p>
        <h2 id="holdings-detail-title">持仓明细</h2>
      </div>
      <span>{{ rows.length }} 条账本记录</span>
    </header>

    <div class="holdings-table-wrap" tabindex="0" role="region" aria-label="持仓明细宽表，可横向滚动">
      <table class="holdings-table">
        <thead>
          <tr>
            <th scope="col">证券</th><th scope="col">行业</th><th scope="col">成本价</th><th scope="col">股数</th>
            <th scope="col">现价</th><th scope="col">今日涨跌</th><th scope="col">浮动盈亏</th><th scope="col">市值</th>
            <th scope="col">持仓构成</th><th scope="col">备注</th><th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.clientKey">
            <th scope="row">
              <input :aria-label="`${row.name || row.code || '未命名持仓'}股票代码`" :value="row.code" inputmode="numeric" maxlength="6" @input="emit('update', row.clientKey, 'code', inputValue($event))" />
              <strong>{{ row.name || '名称缺失' }}</strong>
            </th>
            <td><span>{{ row.industry || '待服务端核验' }}</span></td>
            <td><input :aria-label="`${row.name || row.code}成本价，单位元每股`" :value="row.cost == null ? '' : row.cost" inputmode="decimal" @input="emit('update', row.clientKey, 'cost', inputValue($event))" /></td>
            <td><input :aria-label="`${row.name || row.code}持有股数`" :value="row.shares == null ? '' : row.shares" inputmode="numeric" @input="emit('update', row.clientKey, 'shares', inputValue($event))" /></td>
            <td class="mono">{{ formatPrice(row.price) }}<small>{{ row.quote?.stale ? '陈旧报价' : row.quote?.source || '报价缺失' }}</small></td>
            <td class="mono" :class="`is-${pnlTone(row.changePct)}`">{{ formatPercent(row.changePct, true) }}</td>
            <td class="mono" :class="`is-${pnlTone(row.pnl)}`">{{ formatMoney(row.pnl) }}<small>{{ formatPercent(row.pnlPct, true) }}</small></td>
            <td class="mono">{{ formatMoney(row.marketValue) }}</td>
            <td class="mono">{{ formatPercent(row.holdingSharePct) }}<small>{{ row.holdingSharePct == null ? '报价不完整' : '持仓市值占比' }}</small></td>
            <td><input :aria-label="`${row.name || row.code}备注`" :value="row.note" maxlength="200" @input="emit('update', row.clientKey, 'note', inputValue($event))" /></td>
            <td><button type="button" class="danger-button" @click="emit('remove', row.clientKey)">移除</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="holdings-card-list">
      <article v-for="row in rows" :key="`mobile-${row.clientKey}`" class="holding-card">
        <header>
          <div><strong>{{ row.name || '名称缺失' }}</strong><span class="mono">{{ row.code || '代码缺失' }}</span></div>
          <button type="button" class="danger-button" @click="emit('remove', row.clientKey)">移除</button>
        </header>
        <p class="holding-card__industry">行业：{{ row.industry || '待服务端核验' }}</p>
        <dl>
          <div><dt>现价</dt><dd class="mono">{{ formatPrice(row.price) }}</dd></div>
          <div><dt>今日涨跌</dt><dd class="mono" :class="`is-${pnlTone(row.changePct)}`">{{ formatPercent(row.changePct, true) }}</dd></div>
          <div><dt>浮动盈亏</dt><dd class="mono" :class="`is-${pnlTone(row.pnl)}`">{{ formatMoney(row.pnl) }}</dd></div>
          <div><dt>持仓构成</dt><dd class="mono">{{ formatPercent(row.holdingSharePct) }}</dd></div>
        </dl>
        <p v-if="row.quote?.stale || row.price == null" class="holding-card__quote-note">{{ row.quote?.quote_note || '当前报价缺失，市值与盈亏不计算。' }}</p>
        <div class="holding-card__fields">
          <label><span>代码</span><input :value="row.code" inputmode="numeric" maxlength="6" @input="emit('update', row.clientKey, 'code', inputValue($event))" /></label>
          <label><span>成本价（元/股）</span><input :value="row.cost == null ? '' : row.cost" inputmode="decimal" @input="emit('update', row.clientKey, 'cost', inputValue($event))" /></label>
          <label><span>股数（股）</span><input :value="row.shares == null ? '' : row.shares" inputmode="numeric" @input="emit('update', row.clientKey, 'shares', inputValue($event))" /></label>
          <label><span>备注</span><input :value="row.note" maxlength="200" @input="emit('update', row.clientKey, 'note', inputValue($event))" /></label>
        </div>
      </article>
    </div>

    <footer>单位：价格为元/股，数量为股，市值与盈亏为元，涨跌与持仓构成为百分数值。持仓构成是单只股票市值占全部持仓市值的比例，不代表账户仓位；缺失值统一显示为“—”。</footer>
  </section>
</template>

<style scoped>
.holdings-detail { min-width: 0; overflow: hidden; }
.holdings-detail__header { display: flex; min-height: 68px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.holdings-detail__header h2 { margin: 0; font-size: 18px; }
.holdings-detail__header > span, .holdings-detail > footer { color: var(--text-3); font-size: 12px; }
.holdings-table-wrap { max-width: 100%; overflow-x: auto; }
.holdings-table { width: 100%; min-width: 1320px; border-collapse: collapse; font-size: 13px; }
.holdings-table th, .holdings-table td { padding: 9px 10px; border-bottom: 1px solid var(--border); text-align: right; vertical-align: middle; }
.holdings-table thead th { background: var(--surface-2); color: var(--text-3); font-size: 12px; }
.holdings-table th:first-child, .holdings-table td:nth-child(2), .holdings-table td:nth-child(10) { text-align: left; }
.holdings-table tbody th { min-width: 130px; text-align: left; }
.holdings-table tbody th strong { display: block; margin-top: 3px; }
.holdings-table input { width: 100%; min-width: 90px; min-height: 44px; padding: 7px 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); }
.holdings-table td:nth-child(10) input { min-width: 150px; }
.holdings-table small { display: block; margin-top: 2px; color: var(--text-3); font-size: 12px; white-space: normal; }
.holdings-table .danger-button { min-width: 64px; }
.holdings-card-list { display: none; }
.holdings-detail > footer { padding: 10px 14px; border-top: 1px solid var(--border); }
.is-up { color: var(--up); }
.is-down { color: var(--down); }
.is-flat { color: var(--text-2); }
.is-missing { color: var(--text-3); }
@media (max-width: 720px) {
  .holdings-table-wrap { display: none; }
  .holdings-card-list { display: grid; gap: 8px; padding: 8px; }
  .holding-card { min-width: 0; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-2); }
  .holding-card > header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .holding-card > header div { display: grid; }
  .holding-card > header span, .holding-card__industry, .holding-card__quote-note { color: var(--text-3); font-size: 13px; }
  .holding-card__industry { margin: 6px 0 10px; }
  .holding-card dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; margin: 0; }
  .holding-card dl div { min-width: 0; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface); }
  .holding-card dt { color: var(--text-3); font-size: 12px; }
  .holding-card dd { margin: 2px 0 0; font-size: 16px; font-weight: 720; overflow-wrap: anywhere; }
  .holding-card__quote-note { margin: 8px 0 0; color: var(--warning); }
  .holding-card__fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }
  .holding-card__fields label { display: grid; gap: 4px; color: var(--text-2); font-size: 13px; }
  .holding-card__fields input { width: 100%; min-width: 0; min-height: 44px; padding: 7px 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface); color: var(--text); }
}
@media (max-width: 420px) {
  .holding-card dl, .holding-card__fields { grid-template-columns: minmax(0, 1fr); }
}
</style>
