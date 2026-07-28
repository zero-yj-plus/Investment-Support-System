<script setup lang="ts">
import { computed } from 'vue'
import type {
  CompanyDisclosureResponse,
  CompanyInteractionResponse,
  CompanyResearchEnrichment,
  CompanyResourceState,
} from '../types/companyResearch'
import {
  disclosureSourceLabel,
  disclosureWindowLabel,
  formatCompanyDate,
  formatCompanyDateTime,
  formatCompanyNumber,
  safeOfficialDisclosureUrl,
} from '../utils/companyResearchFormat'

const props = defineProps<{
  code: string
  enrichment: CompanyResourceState<CompanyResearchEnrichment>
  disclosures: CompanyResourceState<CompanyDisclosureResponse>
  interactions: CompanyResourceState<CompanyInteractionResponse>
}>()

const disclosureItems = computed(() => Array.isArray(props.disclosures.data?.items) ? props.disclosures.data.items : [])
const interactionItems = computed(() => Array.isArray(props.interactions.data?.items) ? props.interactions.data.items : [])
const disclosureReason = computed(() => String(
  props.disclosures.data?.evidence?.degraded_reason
  || props.disclosures.data?.degraded_reasons?.[0]
  || '',
).trim())
const interactionReason = computed(() => String(
  props.interactions.data?.evidence?.degraded_reason
  || props.interactions.data?.degraded_reasons?.[0]
  || '',
).trim())

function pointSeries(points: Array<{ year?: number | null; value?: number | null }> | undefined, suffix: string) {
  if (!Array.isArray(points) || !points.length) return '—'
  return points.map((point) => `${point.year ?? '年份缺失'}E ${formatCompanyNumber(point.value)}${suffix}`).join(' / ')
}

function disclosureStateLabel() {
  const data = props.disclosures.data
  if (props.disclosures.loading) return '核对中'
  if (props.disclosures.error || data?.state === 'missing') return '当前无法核验'
  if (data?.state === 'complete' && data.verification_state === 'empty_confirmed') return '双目录空结果已核对'
  if (data?.state === 'complete') return '官方双目录已核对'
  if (data?.used_last_good) return '上次成功快照'
  return '部分官方链路可用'
}

function interactionStateLabel() {
  const state = props.interactions.data?.state
  if (props.interactions.loading) return '读取中'
  if (props.interactions.error || state === 'missing') return '当前不可用'
  if (state === 'not_supported') return '当前未覆盖'
  if (state === 'complete_empty') return '已查询 · 无记录'
  if (state === 'last_good') return '上次成功快照'
  if (state === 'degraded') return '部分字段待核验'
  if (state === 'complete') return '互动易已返回'
  return '等待查询'
}

function interactionTime(value: unknown, fallback: string) {
  const formatted = formatCompanyDateTime(value)
  return formatted === '—' ? fallback : formatted
}
</script>

<template>
  <section class="company-evidence" aria-labelledby="company-evidence-title">
    <header class="company-evidence__heading">
      <div><p class="eyebrow">结构化证据</p><h2 id="company-evidence-title">依据是否完整、可靠、及时？</h2></div>
      <p>三个接口独立降级；公告、互动问答与模型正文不能互相替代。</p>
    </header>

    <div v-if="!code" class="company-evidence-initial panel">
      <strong>等待公司代码</strong><span>提交后才请求该公司的真实证据，不预载其他研究接口。</span>
    </div>

    <div v-else class="company-evidence-grid">
      <section class="company-evidence-card panel" aria-labelledby="company-enrichment-title">
        <header>
          <div><h3 id="company-enrichment-title">盘后研究底座</h3><p>TDX 低频源 · 日内评分权重 0</p></div>
          <span class="company-state" :class="`is-${enrichment.data?.data_state || (enrichment.error ? 'missing' : 'waiting')}`">
            {{ enrichment.loading ? '读取中' : enrichment.error ? '暂不可用' : enrichment.data?.data_state || '状态待核验' }}
          </span>
        </header>
        <div v-if="enrichment.loading" class="company-evidence-loading" role="status">正在读取机构一致预期与股东报告期…</div>
        <div v-else-if="enrichment.error" class="company-local-error" role="alert"><strong>盘后研究底座暂不可用</strong><span>{{ enrichment.error }}</span></div>
        <div v-else-if="enrichment.data" class="company-enrichment-body">
          <dl class="company-metric-grid">
            <div><dt>机构目标价</dt><dd class="mono">{{ formatCompanyNumber(enrichment.data.consensus?.target_price_cny) }} <small>元</small></dd><dd class="company-metric-note">一致预期，仅作盘后辅助</dd></div>
            <div><dt>EPS 一致预期</dt><dd class="mono is-series">{{ pointSeries(enrichment.data.consensus?.eps_cny, ' 元/股') }}</dd><dd class="company-metric-note">年份或数值缺失时保持 `—`</dd></div>
            <div><dt>前十大股东合计</dt><dd class="mono">{{ formatCompanyNumber(enrichment.data.holders_top10?.top10_total_ratio_pct) }} <small>%</small></dd><dd class="company-metric-note">报告期 {{ formatCompanyDate(enrichment.data.holders_top10?.report_date) }}</dd></div>
          </dl>
          <dl class="company-enrichment-detail">
            <div><dt>第一大股东</dt><dd>{{ enrichment.data.holders_top10?.largest_holder?.name || '—' }}<span class="mono">{{ formatCompanyNumber(enrichment.data.holders_top10?.largest_holder?.ratio_pct) }}%</span></dd></div>
            <div><dt>GPJY 交易面</dt><dd>{{ enrichment.data.gpjy?.status === 'blocked' ? '已封锁' : '待验证' }}<span>{{ enrichment.data.gpjy?.reason || '字段语义与单位待核验' }}</span></dd></div>
            <div><dt>数据时点</dt><dd class="mono">{{ formatCompanyDateTime(enrichment.data.ts) }}<span>接口未独立暴露陈旧判定</span></dd></div>
          </dl>
          <p v-if="enrichment.data.missing?.length" class="company-missing-list" role="status">数据缺口：{{ enrichment.data.missing.join('；') }}</p>
        </div>
        <div v-else class="company-local-empty"><strong>研究底座未返回数据</strong><span>这不是数值为 0。</span></div>
      </section>

      <section class="company-evidence-card panel" aria-labelledby="company-disclosure-title">
        <header>
          <div><h3 id="company-disclosure-title">官方公告证据</h3><p>{{ disclosureWindowLabel(disclosures.data?.requested_window) }} · 不参与日内买卖评分</p></div>
          <span class="company-state" :class="`is-${disclosures.data?.state || (disclosures.error ? 'missing' : 'waiting')}`">{{ disclosureStateLabel() }}</span>
        </header>
        <div v-if="disclosures.loading" class="company-evidence-loading" role="status">正在核对巨潮与公司所属交易所…</div>
        <div v-else-if="disclosures.error" class="company-local-error" role="alert"><strong>当前无法读取官方公告</strong><span>{{ disclosures.error }}；不能据此判断查询窗口内“无公告”。</span></div>
        <div v-else-if="disclosures.data?.state === 'missing'" class="company-local-error" role="alert"><strong>当前无法读取官方公告</strong><span>{{ disclosureReason || '两条官方链路当前不可用，不能据此判断查询窗口内“无公告”。' }}</span></div>
        <div v-else-if="!disclosureItems.length && disclosures.data?.state === 'complete' && disclosures.data?.verification_state === 'empty_confirmed'" class="company-local-empty is-verified" role="status"><strong>查询窗口内未发现公告</strong><span>巨潮与{{ disclosures.data?.exchange === 'sse' ? '上交所' : '深交所' }}均已返回空列表。</span></div>
        <div v-else-if="!disclosureItems.length" class="company-local-empty is-degraded" role="status"><strong>当前没有可展示的公告</strong><span>{{ disclosureReason || '官方链路未完成双源核对，现有结果不足以证明查询窗口内确实无公告。' }}</span></div>
        <div v-else class="company-disclosure-list">
          <article v-for="(item, index) in disclosureItems" :key="item.announcement_id || `${item.title}-${index}`">
            <time class="mono">{{ item.announcement_date || item.published_at || '日期待核验' }}</time>
            <div><strong>{{ item.title || '未命名公告' }}</strong><span>{{ disclosureSourceLabel(item.source_confirmations, disclosures.data?.exchange, disclosures.data?.verification_state, disclosures.data?.used_last_good) }}</span></div>
            <a v-if="safeOfficialDisclosureUrl(item.original_url)" :href="safeOfficialDisclosureUrl(item.original_url)" target="_blank" rel="noopener noreferrer">查看官方原文</a>
            <span v-else class="company-link-missing">原文链接不可用</span>
          </article>
          <p>“双目录均有记录”只表示出现同题公告，不代表内容得到独立信源互证。</p>
        </div>
        <p v-if="disclosures.data?.used_last_good" class="company-stale-note" role="status">当前展示上次成功快照；不得称为实时公告。</p>
        <p v-if="disclosureReason && disclosures.data?.state === 'degraded'" class="company-stale-note">降级说明：{{ disclosureReason }}</p>
      </section>

      <section class="company-evidence-card panel" aria-labelledby="company-interaction-title">
        <header>
          <div><h3 id="company-interaction-title">管理层回复 · 互动问答</h3><p>深交所互动易 · Tier 3 · 非正式公告 · 不参与日内评分</p></div>
          <span class="company-state" :class="`is-${interactions.data?.state || (interactions.error ? 'missing' : 'waiting')}`">{{ interactionStateLabel() }}</span>
        </header>
        <div v-if="interactions.loading" class="company-evidence-loading" role="status">正在读取深交所互动易…</div>
        <div v-else-if="interactions.error" class="company-local-error" role="alert"><strong>当前无法读取互动问答</strong><span>{{ interactions.error }}</span></div>
        <div v-else-if="interactions.data?.state === 'not_supported'" class="company-local-empty" role="status"><strong>当前仅覆盖深市互动易</strong><span>沪市/北交所公司暂不适用；这是覆盖范围说明，不代表数据读取失败。</span></div>
        <div v-else-if="interactions.data?.state === 'complete_empty'" class="company-local-empty is-verified" role="status"><strong>查询窗口内暂无公开互动问答</strong><span>互动易明确返回空结果；不等同于公司没有其他投资者沟通。</span></div>
        <div v-else-if="interactions.data?.state === 'missing'" class="company-local-error" role="alert"><strong>当前无法读取互动问答</strong><span>{{ interactionReason || '官方互动问答链路当前不可用；这不是“无记录”结论。' }}</span></div>
        <div v-else-if="!interactionItems.length" class="company-local-empty is-degraded" role="status"><strong>{{ interactions.data?.state === 'last_good' ? '上次成功快照未含问答' : '当前没有可确认的互动问答' }}</strong><span>{{ interactions.data?.state === 'last_good' ? '旧快照的空结果不能证明现在仍无互动问答。' : interactionReason || '返回状态或字段未通过完整校验。' }}</span></div>
        <div v-else class="company-interaction-list">
          <article v-for="(item, index) in interactionItems" :key="`${item.question_pub_date}-${index}`">
            <div class="company-interaction-times"><span>提问 {{ interactionTime(item.question_pub_date, '时间待核验') }}</span><span>{{ item.answered && item.answer ? `回复 ${interactionTime(item.answer_pub_date, '时间待核验')}` : '尚未回复' }}</span></div>
            <dl><div><dt>投资者提问</dt><dd>{{ item.question || '提问内容待核验' }}</dd></div><div v-if="item.answered && item.answer"><dt>管理层回复</dt><dd>{{ item.answer }}</dd></div></dl>
            <p v-if="!item.answered || !item.answer">该问题尚未收到公开回复，不标记为“管理层回复”。</p>
          </article>
        </div>
        <p v-if="interactions.data?.state === 'last_good'" class="company-stale-note" role="status">当前展示上次成功快照；旧数据不会标成实时。</p>
        <p v-if="interactionReason && ['degraded', 'last_good'].includes(interactions.data?.state || '')" class="company-stale-note">{{ interactions.data?.state === 'last_good' ? '快照' : '降级' }}说明：{{ interactionReason }}</p>
      </section>
    </div>
  </section>
</template>

<style scoped>
.company-evidence { display: grid; min-width: 0; gap: 10px; }.company-evidence__heading { display: flex; align-items: end; justify-content: space-between; gap: 16px; padding: 3px 2px; }.company-evidence__heading h2 { margin: 0; font-size: 20px; }.company-evidence__heading > p { max-width: 520px; margin: 0; color: var(--text-3); font-size: 13px; text-align: right; }.company-evidence-initial { display: grid; min-height: 112px; place-content: center; gap: 4px; padding: 18px; color: var(--text-3); text-align: center; }.company-evidence-initial strong { color: var(--text-2); }.company-evidence-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; min-width: 0; }.company-evidence-card { min-width: 0; overflow: hidden; }.company-evidence-card:first-child { grid-column: 1 / -1; }.company-evidence-card > header { display: flex; min-height: 66px; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 12px; border-bottom: 1px solid var(--border); }.company-evidence-card h3 { margin: 0; font-size: 17px; }.company-evidence-card header p { margin: 3px 0 0; color: var(--text-3); font-size: 12px; }.company-state { flex: 0 0 auto; padding: 4px 7px; border: 1px solid currentColor; border-radius: 999px; color: var(--text-2); font-size: 12px; font-weight: 720; }.company-state.is-complete, .company-state.is-complete_empty { color: var(--info); }.company-state.is-degraded, .company-state.is-last_good, .company-state.is-waiting { color: var(--warning); }.company-state.is-missing { color: var(--danger); }.company-evidence-loading, .company-local-error, .company-local-empty { display: grid; min-height: 116px; place-content: center; gap: 5px; padding: 16px; color: var(--text-3); text-align: center; }.company-local-error { color: var(--danger); }.company-local-empty.is-degraded { color: var(--warning); }.company-local-empty.is-verified { color: var(--info); }.company-local-error span, .company-local-empty span { max-width: 640px; color: var(--text-2); font-size: 13px; line-height: 1.55; }.company-enrichment-body { padding: 10px 12px 12px; }.company-metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 0; }.company-metric-grid > div { min-width: 0; padding: 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }.company-metric-grid dt, .company-enrichment-detail dt { color: var(--text-3); font-size: 12px; }.company-metric-grid dd { margin: 4px 0; color: var(--text); font-size: 21px; font-weight: 760; overflow-wrap: anywhere; }.company-metric-grid dd.is-series { font-size: 15px; line-height: 1.5; }.company-metric-grid dd small { font: 650 12px/1.4 Inter, "PingFang SC", "Microsoft YaHei", sans-serif; }.company-metric-grid .company-metric-note { color: var(--text-3); font: 500 11px/1.45 Inter, "PingFang SC", "Microsoft YaHei", sans-serif; }.company-enrichment-detail { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 10px 0 0; border: 1px solid var(--border); border-radius: 6px; }.company-enrichment-detail > div { min-width: 0; padding: 9px 10px; }.company-enrichment-detail > div + div { border-left: 1px solid var(--border); }.company-enrichment-detail dd { display: grid; gap: 2px; margin: 3px 0 0; color: var(--text); font-size: 13px; font-weight: 680; }.company-enrichment-detail dd span { color: var(--text-3); font-size: 11px; font-weight: 500; }.company-missing-list, .company-stale-note { margin: 9px 0 0; padding: 8px 10px; border: 1px solid var(--warning); border-radius: 6px; color: var(--warning); font-size: 12px; line-height: 1.5; }.company-disclosure-list, .company-interaction-list { display: grid; padding: 0 12px 10px; }.company-disclosure-list article { display: grid; grid-template-columns: 86px minmax(0, 1fr) auto; align-items: center; gap: 8px; min-height: 60px; border-bottom: 1px solid var(--border); }.company-disclosure-list time { color: var(--text-3); font-size: 12px; }.company-disclosure-list article > div { display: grid; min-width: 0; gap: 2px; }.company-disclosure-list strong { overflow-wrap: anywhere; font-size: 13px; }.company-disclosure-list article span { color: var(--text-3); font-size: 11px; }.company-disclosure-list a { display: inline-flex; min-width: 108px; min-height: 44px; align-items: center; justify-content: center; padding: 6px 8px; border: 1px solid var(--accent); border-radius: 6px; color: var(--accent); font-size: 12px; font-weight: 720; text-decoration: none; }.company-link-missing { padding: 6px; text-align: center; }.company-disclosure-list > p { margin: 9px 0 0; color: var(--text-3); font-size: 12px; line-height: 1.5; }.company-evidence-card > .company-stale-note { margin: 0 12px 10px; }.company-interaction-list { gap: 8px; padding-top: 10px; }.company-interaction-list article { padding: 9px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-2); }.company-interaction-times { display: flex; flex-wrap: wrap; gap: 5px 12px; color: var(--text-3); font: 500 11px/1.45 var(--font-num); }.company-interaction-list dl { display: grid; gap: 7px; margin: 8px 0 0; }.company-interaction-list dl > div { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 8px; }.company-interaction-list dt { color: var(--text-3); font-size: 12px; }.company-interaction-list dd { margin: 0; color: var(--text); font-size: 13px; line-height: 1.55; }.company-interaction-list article > p { margin: 7px 0 0; color: var(--warning); font-size: 12px; }
@media (max-width: 980px) { .company-evidence-grid { grid-template-columns: minmax(0, 1fr); }.company-evidence-card:first-child { grid-column: 1; } }
@media (max-width: 720px) { .company-evidence__heading { align-items: flex-start; flex-direction: column; }.company-evidence__heading > p { text-align: left; }.company-evidence-card > header { align-items: flex-start; flex-direction: column; }.company-metric-grid, .company-enrichment-detail { grid-template-columns: minmax(0, 1fr); }.company-enrichment-detail > div + div { border-top: 1px solid var(--border); border-left: 0; }.company-disclosure-list article { grid-template-columns: minmax(0, 1fr); padding-block: 8px; }.company-disclosure-list a { width: 100%; }.company-link-missing { text-align: left; }.company-interaction-list dl > div { grid-template-columns: minmax(0, 1fr); gap: 2px; } }
</style>
