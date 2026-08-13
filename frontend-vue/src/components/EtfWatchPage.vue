<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { useEtfWatch } from '../composables/useEtfWatch'
import type { EtfWatchItem } from '../types/etfWatch'

const emit = defineEmits<{ refreshState: [value: boolean] }>()
const {
  items, loading, refreshing, mutating, loaded, timestamp, loadError, mutationError,
  feedback, degradedCount, refresh, addCodes, refreshProfiles, deleteItem,
} = useEtfWatch()

type KindFilter = 'all' | 'cross' | 'equity' | 'bond' | 'commodity'
type SortMode = 'default' | 'amount' | 'change' | 'turnover'
const search = ref('')
const kindFilter = ref<KindFilter>('all')
const sortMode = ref<SortMode>('default')
const addDialog = ref<HTMLDialogElement | null>(null)
const addText = ref('')
const addError = ref('')
const addButton = ref<HTMLButtonElement | null>(null)
const codeInput = ref<HTMLTextAreaElement | null>(null)
const liveMessage = ref('')
const pageBusy = computed(() => refreshing.value || mutating.value)

watch(pageBusy, (value) => emit('refreshState', value), { immediate: true })
onBeforeUnmount(() => emit('refreshState', false))

const counts = computed(() => {
  const result = { all: items.value.length, cross: 0, equity: 0, bond: 0, commodity: 0 }
  for (const item of items.value) {
    const kind = item.profile?.kind
    if (kind && kind in result && kind !== 'all') result[kind as Exclude<KindFilter, 'all'>] += 1
  }
  return result
})

const visibleItems = computed(() => {
  const query = search.value.trim().toLowerCase()
  let rows = items.value.filter((item) => {
    const kindMatch = kindFilter.value === 'all' || item.profile?.kind === kindFilter.value
    const text = `${item.code || ''}${item.name || ''}${item.profile?.tracking_target || ''}`.toLowerCase()
    return kindMatch && (!query || text.includes(query))
  })
  if (sortMode.value === 'amount') rows = [...rows].sort((a, b) => finite(b.quote?.amount) - finite(a.quote?.amount))
  if (sortMode.value === 'change') rows = [...rows].sort((a, b) => finite(b.quote?.change_pct) - finite(a.quote?.change_pct))
  if (sortMode.value === 'turnover') rows = [...rows].sort((a, b) => finite(b.quote?.turnover_rate) - finite(a.quote?.turnover_rate))
  return rows
})

function finite(value: unknown): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY
}

function has(value: unknown): boolean {
  return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value))
}

function number(value: unknown, digits = 2): string {
  return has(value) ? Number(value).toFixed(digits) : '—'
}

function signed(value: unknown, digits = 2): string {
  if (!has(value)) return '—'
  const parsed = Number(value)
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}%`
}

function percent(value: unknown, digits = 2): string {
  return has(value) ? `${Number(value).toFixed(digits)}%` : '—'
}

function tone(value: unknown): string {
  if (!has(value) || Number(value) === 0) return 'is-flat'
  return Number(value) > 0 ? 'is-up' : 'is-down'
}

function amount(value: unknown): string {
  if (!has(value)) return '—'
  const parsed = Number(value)
  return parsed >= 1e8 ? `${(parsed / 1e8).toFixed(2)}亿` : `${(parsed / 1e4).toFixed(0)}万`
}

function volume(value: unknown): string {
  const formatted = amount(value)
  return formatted === '—' ? formatted : `${formatted}份`
}

function scaleProfileReady(item: EtfWatchItem): boolean {
  return has(item.profile?.net_assets_billion) || has(item.profile?.latest_nav)
}

function trendReady(item: EtfWatchItem): boolean {
  return Boolean(item.profile?.trend) || has(item.profile?.ret5_pct) || has(item.profile?.ret20_pct) || has(item.profile?.ret60_pct)
}

function feeReady(item: EtfWatchItem): boolean {
  return Boolean(item.profile?.manager || item.profile?.subscribe_status || item.profile?.redeem_status)
    || has(item.profile?.management_fee_pct) || has(item.profile?.custody_fee_pct)
}

function fee(item: EtfWatchItem): string {
  const management = item.profile?.management_fee_pct
  const custody = item.profile?.custody_fee_pct
  if (!has(management) || !has(custody)) return '—'
  return `${number(management)}% + ${number(custody)}%`
}

function openAddDialog() {
  addError.value = ''
  addText.value = ''
  addDialog.value?.showModal()
  void nextTick(() => codeInput.value?.focus())
}

function closeAddDialog() {
  if (mutating.value) return
  addDialog.value?.close()
  void nextTick(() => addButton.value?.focus())
}

function parseCodes(): string[] {
  return [...new Set((addText.value.match(/\d{6}/g) || []).filter((code) => /^(?:5\d{5}|1[56]\d{4})$/.test(code)))]
}

async function submitAdd() {
  const codes = parseCodes()
  if (!codes.length) {
    addError.value = '请输入至少一个可识别的沪深ETF代码；可用空格、逗号或换行分隔。'
    return
  }
  addError.value = ''
  if (await addCodes(codes)) closeAddDialog()
}

async function removeItem(item: EtfWatchItem) {
  if (!window.confirm(`确认将 ${item.name || item.code || '该ETF'} 移出观察列表？`)) return
  await deleteItem(item)
}

async function refreshAll() {
  await refresh()
  liveMessage.value = 'ETF行情已刷新'
}

defineExpose({ refresh: refreshAll })
</script>

<template>
  <section class="etf-watch-page" aria-labelledby="etf-watch-page-title" :aria-busy="pageBusy">
    <header class="etf-watch-intro">
      <div class="page-heading">
        <h1 id="etf-watch-page-title">ETF观察</h1>
        <p class="page-subtitle">集中观察行情、流动性、规模净值、趋势波动与申赎状态；不生成双周期区间，也不长期展示高缺失字段。</p>
      </div>
      <div class="etf-watch-actions">
        <button type="button" class="secondary-button" :disabled="pageBusy || !items.length" @click="refreshProfiles">刷新低频资料</button>
        <button ref="addButton" type="button" class="primary-button" :disabled="pageBusy" @click="openAddDialog">
          <AppIcon name="plus" :size="17" />添加ETF
        </button>
      </div>
    </header>

    <p v-if="feedback || mutationError" class="etf-feedback" :class="{ 'is-error': mutationError }" :role="mutationError ? 'alert' : 'status'" aria-live="polite">
      {{ mutationError || feedback }}
    </p>

    <div v-if="loading" class="etf-loading" role="status" aria-live="polite">
      <span class="sr-only">ETF观察正在加载</span>
      <div v-for="index in 4" :key="index" class="panel etf-skeleton"></div>
    </div>

    <template v-else>
      <section v-if="loadError && !loaded" class="panel etf-fatal" role="alert" aria-labelledby="etf-fatal-title">
        <h2 id="etf-fatal-title">ETF观察暂不可用</h2>
        <p>{{ loadError }}；未构造空列表，也未用演示数据补位。</p>
        <button type="button" class="secondary-button" :disabled="refreshing" @click="refresh">重试读取</button>
      </section>

      <template v-else>
        <p v-if="loadError" class="etf-alert" role="alert">{{ loadError }}；当前保留上次成功列表，缺失值不补0。</p>
        <p v-else-if="degradedCount" class="etf-alert is-warning" role="status">
          {{ degradedCount }}只ETF存在局部缺失或资料补充中；已取得的字段继续显示，未取得的字段不参与排序。
        </p>

        <section class="etf-toolbar" aria-label="ETF观察筛选">
          <label class="etf-search">
            <span class="sr-only">搜索ETF代码、名称或跟踪标的</span>
            <AppIcon name="research" :size="17" />
            <input v-model="search" type="search" placeholder="搜索代码 / 名称 / 跟踪标的" autocomplete="off" />
          </label>
          <div class="etf-filters" role="group" aria-label="按ETF类型筛选">
            <button v-for="filter in ([['all', '全部'], ['cross', '跨境'], ['equity', '股票'], ['bond', '债券'], ['commodity', '商品']] as const)" :key="filter[0]" type="button" :class="{ active: kindFilter === filter[0] }" @click="kindFilter = filter[0]">
              {{ filter[1] }} {{ counts[filter[0]] }}
            </button>
          </div>
          <label class="etf-sort">
            <span class="sr-only">ETF排序方式</span>
            <select v-model="sortMode">
              <option value="default">默认排序</option>
              <option value="amount">成交额从高到低</option>
              <option value="change">涨幅从高到低</option>
              <option value="turnover">换手率从高到低</option>
            </select>
          </label>
        </section>

        <div class="etf-summary">
          <div><span>当前显示 <strong>{{ visibleItems.length }}</strong>只</span><span>T+0 <strong>{{ items.filter(item => item.profile?.trade_rule === 'T+0').length }}</strong>只</span><span>T+1 <strong>{{ items.filter(item => item.profile?.trade_rule === 'T+1').length }}</strong>只</span></div>
          <span>行情批量读取 · 低频资料last-good · 更新时间 {{ timestamp || '未提供' }}</span>
        </div>

        <section v-if="!items.length" class="panel etf-empty" aria-labelledby="etf-empty-title">
          <h2 id="etf-empty-title">当前ETF观察列表为空</h2>
          <p>这是服务端返回的真实空数组。添加代码后，行情先显示，基金资料与历史指标在后台低频补充。</p>
          <button type="button" class="primary-button" @click="openAddDialog">添加第一只ETF</button>
        </section>

        <section v-else class="etf-list" aria-label="ETF观察明细">
          <div class="etf-list-head" aria-hidden="true"><span>标的 / 跟踪</span><span>行情</span><span>日内区间</span><span>成交 / 流动性</span><span>规模 / 净值</span><span>趋势 / 波动</span><span>费率 / 申赎</span><span>来源状态</span></div>
          <article v-for="(item, index) in visibleItems" :key="String(item.id || item.code || index)" class="etf-row-item">
            <div class="etf-row">
              <div class="etf-cell etf-identity">
                <div><strong>{{ item.name || item.code || '名称缺失' }}</strong><span v-if="item.profile?.trade_rule" class="etf-tag">{{ item.profile.trade_rule }}</span></div>
                <small class="mono">{{ item.code || '代码缺失' }} · {{ item.profile?.kind_label || item.profile?.fund_type || '类型补充中' }}</small>
                <span :title="item.profile?.tracking_target || undefined">跟踪 · {{ item.profile?.tracking_target || '资料补充中' }}</span>
              </div>

              <div class="etf-cell etf-quote">
                <strong class="mono">{{ number(item.quote?.price, 3) }}</strong>
                <span class="mono" :class="tone(item.quote?.change_pct)">{{ signed(item.quote?.change_pct) }} · {{ has(item.quote?.price) && has(item.quote?.prev_close) ? `${Number(item.quote?.price) - Number(item.quote?.prev_close) >= 0 ? '+' : ''}${(Number(item.quote?.price) - Number(item.quote?.prev_close)).toFixed(3)}` : '—' }}</span>
              </div>

              <div class="etf-cell etf-metrics">
                <div><span>开 / 高</span><b class="mono">{{ number(item.quote?.open, 3) }} / {{ number(item.quote?.high, 3) }}</b></div>
                <div><span>低 / 昨</span><b class="mono">{{ number(item.quote?.low, 3) }} / {{ number(item.quote?.prev_close, 3) }}</b></div>
                <div><span>振幅</span><b class="mono">{{ has(item.quote?.amplitude_pct) ? `${number(item.quote?.amplitude_pct)}%` : '—' }}</b></div>
              </div>

              <div class="etf-cell etf-metrics">
                <div><span>成交额</span><b class="mono">{{ amount(item.quote?.amount) }}</b></div>
                <div><span>换手 / 量比</span><b class="mono">{{ has(item.quote?.turnover_rate) ? `${number(item.quote?.turnover_rate)}%` : '—' }} / {{ number(item.quote?.volume_ratio) }}</b></div>
                <div><span>成交量</span><b class="mono">{{ volume(item.quote?.volume) }}</b></div>
              </div>

              <div class="etf-cell etf-metrics">
                <div><span>场内市值</span><b class="mono">{{ amount(item.quote?.total_mv) }}</b></div>
                <template v-if="scaleProfileReady(item)">
                  <div><span>净资产</span><b class="mono">{{ has(item.profile?.net_assets_billion) ? `${number(item.profile?.net_assets_billion)}亿` : '—' }}</b></div>
                  <div><span>最近净值</span><b class="mono">{{ number(item.profile?.latest_nav, 4) }} · {{ item.profile?.nav_date || '日期缺失' }}</b></div>
                </template>
                <div v-else class="etf-inline-pending"><span>规模 / 净值</span><b>基金资料补充中</b></div>
              </div>

              <div v-if="trendReady(item)" class="etf-cell etf-trend">
                <div class="etf-returns"><span>5日<b class="mono" :class="tone(item.profile?.ret5_pct)">{{ signed(item.profile?.ret5_pct) }}</b></span><span>20日<b class="mono" :class="tone(item.profile?.ret20_pct)">{{ signed(item.profile?.ret20_pct) }}</b></span><span>60日<b class="mono" :class="tone(item.profile?.ret60_pct)">{{ signed(item.profile?.ret60_pct) }}</b></span></div>
                <strong>{{ item.profile?.trend || '趋势缺失' }}</strong>
                <small>20日波动 / 60日回撤：{{ percent(item.profile?.volatility20_pct) }} / {{ signed(item.profile?.drawdown60_pct) }}</small>
              </div>
              <div v-else class="etf-cell etf-profile-pending"><strong>历史指标补充中</strong><span>失败时保留最近成功结果</span></div>

              <div v-if="feeReady(item)" class="etf-cell etf-metrics">
                <div><span>管理人</span><b>{{ item.profile?.manager || '—' }}</b></div>
                <div><span>管理 + 托管</span><b class="mono">{{ fee(item) }}</b></div>
                <div><span>申赎</span><b>{{ item.profile?.subscribe_status || '—' }} · {{ item.profile?.redeem_status || '—' }}</b></div>
              </div>
              <div v-else class="etf-cell etf-profile-pending"><strong>费率申赎补充中</strong><span>不使用行情字段推断</span></div>

              <div class="etf-cell etf-quality">
                <span class="etf-quality-badge" :class="{ 'is-degraded': item.state !== 'complete' || item.quote?.stale }">{{ item.quote?.stale ? '盘后快照' : item.state === 'complete' ? '数据可用' : item.quote ? '部分覆盖' : '行情缺失' }}</span>
                <small :title="item.quote?.quote_note || undefined">{{ item.quote?.source || '行情源缺失' }}<br>{{ item.profile?.profile_source || '基金档案补充中' }}<br>日线 {{ item.profile?.history_date || '补充中' }}</small>
                <button type="button" class="etf-remove" :disabled="mutating" :aria-label="`移出${item.name || item.code || 'ETF'}`" @click="removeItem(item)">移出</button>
              </div>
            </div>
          </article>
          <div v-if="!visibleItems.length" class="etf-filter-empty" role="status">没有匹配的ETF，请调整搜索或分类。</div>
        </section>

        <dl class="etf-footnote" aria-label="ETF观察数据说明">
          <div><dt>行情快照</dt><dd>TDX API主、腾讯备 · 批量读取</dd></div>
          <div><dt>基金档案</dt><dd>公开基金档案 · 低频last-good</dd></div>
          <div><dt>历史指标</dt><dd>日线本地计算 · 明示数据日期</dd></div>
          <div><dt>未纳入主行</dt><dd>PE/PB、五档盘口、IOPV、资金分单</dd></div>
        </dl>
        <footer class="page-footer"><span>低频资料失败时保留最近成功结果；从未成功的整组标注补充中。</span><span>红涨绿跌只表达行情，不表达风险或买卖建议。</span></footer>
      </template>
    </template>

    <Teleport to="body">
      <dialog ref="addDialog" class="etf-dialog" aria-labelledby="etf-dialog-title" @cancel.prevent="closeAddDialog">
        <header><div><h2 id="etf-dialog-title">添加ETF观察</h2><p>先通过真实批量行情核验代码；低频基金资料随后在后台补充。</p></div><button type="button" class="icon-button" aria-label="关闭添加ETF窗口" :disabled="mutating" @click="closeAddDialog"><AppIcon name="close" :size="18" /></button></header>
        <form @submit.prevent="submitAdd">
          <label for="etf-code-input">ETF代码</label>
          <textarea id="etf-code-input" ref="codeInput" v-model="addText" required inputmode="numeric" placeholder="输入6位ETF代码，每行一个"></textarea>
          <small>可一次输入1—50个代码，用空格、逗号或换行分隔；无真实行情的代码不会写入。</small>
          <p v-if="addError || mutationError" role="alert">{{ addError || mutationError }}</p>
          <footer><button type="button" class="secondary-button" :disabled="mutating" @click="closeAddDialog">取消</button><button type="submit" class="primary-button" :disabled="mutating">{{ mutating ? '核验并添加中' : '核验并添加' }}</button></footer>
        </form>
      </dialog>
    </Teleport>
    <span class="sr-only" aria-live="polite">{{ liveMessage }}</span>
  </section>
</template>

<style scoped>
.etf-watch-page { display: grid; min-width: 0; gap: 12px; }
.etf-watch-intro { display: flex; align-items: flex-end; justify-content: space-between; gap: 22px; padding: 4px 2px 2px; }
.etf-watch-intro h1 { margin: 0; font-size: clamp(24px, 2.2vw, 30px); }
.etf-watch-actions { display: flex; flex: 0 0 auto; gap: 8px; }
.etf-watch-actions button, .etf-empty button { min-height: 44px; }
.etf-feedback, .etf-alert { margin: 0; padding: 10px 12px; border: 1px solid var(--info); border-radius: var(--radius); background: var(--surface); color: var(--text-2); }
.etf-feedback.is-error, .etf-alert { border-color: var(--danger); }
.etf-alert.is-warning { border-color: var(--warning); }
.etf-loading { display: grid; gap: 8px; }
.etf-skeleton { min-height: 102px; background: var(--surface-2); animation: etf-pulse 1.1s ease-in-out infinite alternate; }
.etf-fatal, .etf-empty { display: flex; min-height: 320px; flex-direction: column; align-items: center; justify-content: center; padding: 24px; text-align: center; }
.etf-fatal h2, .etf-empty h2 { margin: 0; }
.etf-fatal p, .etf-empty p { max-width: 700px; color: var(--text-2); }
.etf-toolbar { display: grid; grid-template-columns: minmax(220px, 290px) minmax(0, 1fr) minmax(158px, 180px); align-items: center; gap: 8px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.etf-search { position: relative; min-width: 0; }
.etf-search .app-icon { position: absolute; top: 50%; left: 11px; color: var(--text-3); transform: translateY(-50%); pointer-events: none; }
.etf-search input, .etf-sort select { width: 100%; min-height: 44px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); }
.etf-search input { padding: 7px 10px 7px 36px; }
.etf-sort select { padding: 7px 10px; }
.etf-filters { display: flex; min-width: 0; flex-wrap: wrap; gap: 5px; }
.etf-filters button { min-height: 44px; padding: 5px 10px; border: 1px solid transparent; border-radius: var(--radius-sm); background: transparent; color: var(--text-2); cursor: pointer; }
.etf-filters button:hover { border-color: var(--border); background: var(--surface-2); }
.etf-filters button.active { border-color: var(--accent); background: var(--accent-dim); color: var(--accent); font-weight: 760; }
.etf-summary { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 7px 16px; padding: 0 3px; color: var(--text-3); font-size: 12px; }
.etf-summary div { display: flex; flex-wrap: wrap; gap: 12px; }
.etf-summary strong { color: var(--text-2); }
.etf-list { min-width: 0; overflow: clip; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.etf-list-head, .etf-row { display: grid; grid-template-columns: minmax(174px,1.35fr) minmax(98px,.72fr) minmax(136px,.98fr) minmax(140px,1fr) minmax(146px,1.05fr) minmax(170px,1.2fr) minmax(134px,.95fr) minmax(106px,.72fr); }
.etf-list-head { min-height: 40px; background: var(--surface-3); color: var(--text-2); font-size: 12px; font-weight: 760; }
.etf-list-head span, .etf-cell { display: flex; min-width: 0; flex-direction: column; justify-content: center; padding: 7px 9px; }
.etf-list-head span + span, .etf-cell + .etf-cell { border-left: 1px solid var(--border); }
.etf-row-item + .etf-row-item { border-top: 1px solid var(--border); }
.etf-row { min-height: 102px; }
.etf-row:hover { background: color-mix(in srgb, var(--surface-2) 64%, transparent); }
.etf-identity { gap: 4px; }
.etf-identity > div { display: flex; min-width: 0; align-items: center; gap: 7px; }
.etf-identity strong { min-width: 0; overflow: hidden; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.etf-identity small { color: var(--text-3); font-size: 11px; }
.etf-identity > span:last-child { overflow: hidden; color: var(--text-2); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.etf-tag { flex: 0 0 auto; padding: 1px 6px; border: 1px solid var(--info); border-radius: 999px; color: var(--info); font-size: 10px; font-weight: 740; }
.etf-quote > strong { font-size: 19px; line-height: 1.15; }
.etf-quote > span { margin-top: 4px; font-size: 12px; font-weight: 730; }
.etf-metrics { display: grid; gap: 3px; }
.etf-metrics div { display: flex; min-width: 0; align-items: baseline; justify-content: space-between; gap: 6px; font-size: 11px; }
.etf-metrics span { color: var(--text-3); }
.etf-metrics b { min-width: 0; overflow: hidden; color: var(--text-2); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.etf-metrics .etf-inline-pending { align-items: flex-start; flex-direction: column; gap: 2px; }
.etf-metrics .etf-inline-pending b { color: var(--text-3); font-size: 10px; }
.etf-profile-pending { gap: 4px; color: var(--text-2); }
.etf-profile-pending strong { font-size: 12px; }
.etf-profile-pending span { color: var(--text-3); font-size: 10px; }
.etf-returns { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 4px; }
.etf-returns span { padding: 3px 4px; border-radius: 3px; background: var(--surface-2); color: var(--text-3); font-size: 9px; text-align: center; }
.etf-returns b { display: block; margin-top: 1px; font-size: 10px; }
.etf-trend > strong { margin-top: 4px; overflow: hidden; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.etf-trend > small { color: var(--text-3); font-size: 10px; }
.etf-quality { align-items: flex-start; gap: 4px; }
.etf-quality-badge { padding: 2px 7px; border: 1px solid var(--down); border-radius: 999px; color: var(--down); font-size: 10px; font-weight: 740; }
.etf-quality-badge.is-degraded { border-color: var(--warning); color: var(--warning); }
.etf-quality small { color: var(--text-3); font-size: 10px; line-height: 1.35; }
.etf-remove { min-width: 44px; min-height: 44px; margin-top: auto; padding: 0 8px; border: 1px solid transparent; background: transparent; color: var(--text-3); cursor: pointer; }
.etf-remove:hover { border-color: var(--border); color: var(--danger); }
.etf-filter-empty { padding: 28px; color: var(--text-3); text-align: center; }
.etf-footnote { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 8px 16px; margin: 0; padding: 9px 11px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.etf-footnote div { min-width: 0; }
.etf-footnote dt { color: var(--text-3); font-size: 11px; }
.etf-footnote dd { margin: 2px 0 0; color: var(--text-2); font-size: 12px; font-weight: 680; overflow-wrap: anywhere; }
.etf-dialog { width: min(560px,calc(100vw - 28px)); padding: 0; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); color: var(--text); }
.etf-dialog::backdrop { background: rgb(7 13 22 / 58%); }
.etf-dialog > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--border); }
.etf-dialog h2 { margin: 0; font-size: 20px; }
.etf-dialog header p { margin: 4px 0 0; color: var(--text-2); font-size: 13px; }
.etf-dialog form { display: grid; gap: 8px; padding: 16px; }
.etf-dialog label { color: var(--text-2); font-size: 13px; font-weight: 720; }
.etf-dialog textarea { min-height: 112px; padding: 9px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); color: var(--text); resize: vertical; }
.etf-dialog form > small { color: var(--text-3); }
.etf-dialog form > p { margin: 0; color: var(--danger); }
.etf-dialog form footer { display: flex; justify-content: flex-end; gap: 8px; margin: 8px -16px -16px; padding: 12px 16px; border-top: 1px solid var(--border); }
.etf-dialog button { min-height: 44px; }
@keyframes etf-pulse { from { opacity: .5; } to { opacity: .82; } }
@media (max-width: 1100px) {
  .etf-watch-intro { align-items: stretch; flex-direction: column; gap: 10px; }
  .etf-watch-actions { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); }
  .etf-toolbar { grid-template-columns: minmax(210px,1fr) minmax(150px,180px); }
  .etf-filters { grid-column: 1 / -1; order: 3; }
  .etf-row { grid-template-columns: repeat(12,minmax(0,1fr)); }
  .etf-cell { min-height: 84px; border-left: 0 !important; }
  .etf-cell:nth-child(1) { grid-column: 1 / 4; grid-row: 1; }
  .etf-cell:nth-child(2) { grid-column: 4 / 6; grid-row: 1; border-left: 1px solid var(--border) !important; }
  .etf-cell:nth-child(7) { grid-column: 6 / 9; grid-row: 1; border-left: 1px solid var(--border) !important; }
  .etf-cell:nth-child(8) { grid-column: 9 / 13; grid-row: 1; border-left: 1px solid var(--border) !important; }
  .etf-cell:nth-child(3) { grid-column: 1 / 4; grid-row: 2; border-top: 1px solid var(--border); }
  .etf-cell:nth-child(4) { grid-column: 4 / 7; grid-row: 2; border-top: 1px solid var(--border); border-left: 1px solid var(--border) !important; }
  .etf-cell:nth-child(5) { grid-column: 7 / 10; grid-row: 2; border-top: 1px solid var(--border); border-left: 1px solid var(--border) !important; }
  .etf-cell:nth-child(6) { grid-column: 10 / 13; grid-row: 2; border-top: 1px solid var(--border); border-left: 1px solid var(--border) !important; }
  .etf-list-head { display: none; }
}
@media (max-width: 680px) {
  .etf-watch-page { gap: 9px; font-size: 18px; }
  .etf-watch-actions, .etf-toolbar { grid-template-columns: minmax(0,1fr); }
  .etf-toolbar { padding: 8px; }
  .etf-sort, .etf-filters { grid-column: 1; }
  .etf-filters button { flex: 1 1 80px; font-size: 16px; }
  .etf-summary { align-items: flex-start; flex-direction: column; font-size: 13px; }
  .etf-row { grid-template-columns: repeat(6,minmax(0,1fr)); }
  .etf-cell { min-height: 0; padding: 9px 10px; }
  .etf-cell:nth-child(1) { grid-column: 1 / 5; grid-row: 1; min-height: 82px; }
  .etf-cell:nth-child(2) { grid-column: 5 / 7; grid-row: 1; min-height: 82px; }
  .etf-cell:nth-child(3), .etf-cell:nth-child(4), .etf-cell:nth-child(5), .etf-cell:nth-child(6), .etf-cell:nth-child(7), .etf-cell:nth-child(8) { grid-column: 1 / 7; border-left: 0 !important; border-top: 1px solid var(--border); }
  .etf-cell:nth-child(3) { grid-row: 2; }
  .etf-cell:nth-child(4) { grid-row: 3; }
  .etf-cell:nth-child(5) { grid-row: 4; }
  .etf-cell:nth-child(6) { grid-row: 5; }
  .etf-cell:nth-child(7) { grid-row: 6; }
  .etf-cell:nth-child(8) { grid-row: 7; }
  .etf-footnote { grid-template-columns: minmax(0,1fr); }
  .etf-dialog { width: calc(100vw - 12px); }
  .etf-dialog form footer { align-items: stretch; flex-direction: column; }
}
@media (prefers-reduced-motion: reduce) { .etf-skeleton { animation: none; } }
</style>
