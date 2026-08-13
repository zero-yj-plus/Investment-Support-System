import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'

const require = createRequire(import.meta.url)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const outputDir = resolve(process.argv[2] || resolve(tmpdir(), 'wudi-etf-watch-audit'))
const axeSource = await readFile(require.resolve('axe-core/axe.min.js'), 'utf8')
const chromePath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'

const shellFixtures = {
  '/api/shell-status': {
    ok: true,
    client: { name: 'ETF审计实例', id: 'audit', brand: 'W.U.D.I.', brand_sub: 'A股决策支持' },
    version: '1.4.1-audit', is_trade_day: true, market_session: 'intraday',
    market_context: { now: '2026-08-13T10:31:00+08:00', is_trade_day: true, market_session: 'intraday' },
  },
  '/api/pulse': { ok: true, regime: '结构轮动', risk_level: 3, risk_label: '中等风险', senti: 52, senti_label: '中性', '定格时点': '2026-08-13 10:30' },
  '/api/market/overview_cached': { ok: true, data_date: '2026-08-13 10:31', indexes: [{ name: '上证指数', price: 3934.09, change_pct: -0.82 }] },
  '/api/sysrisk': { ok: true, level: 3, level_name: '中等风险', summary: '审计数据' },
  '/api/market/mainlines': { ok: true, data_date: '2026-08-13', roles: [], mainlines: [], missing: [] },
}

const profiles = {
  cross: { kind: 'cross', kind_label: '跨境', fund_type: 'QDII-股票型', trade_rule: 'T+0', tracking_target: '纳斯达克100指数', manager: '国泰基金管理有限公司', management_fee_pct: 0.60, custody_fee_pct: 0.20, net_assets_billion: 198.42, asset_date: '2026-06-30', latest_nav: 2.2134, nav_date: '2026-08-12', subscribe_status: '开放申购', redeem_status: '开放赎回', ret5_pct: -1.24, ret20_pct: 3.86, ret60_pct: 9.44, volatility20_pct: 21.18, drawdown60_pct: -6.42, trend: 'MA20上方 · MA60上方', history_date: '2026-08-12', profile_source: '天天基金公开档案', refreshed_at: '2026-08-13T09:00:00+08:00', missing: [] },
  equity: { kind: 'equity', kind_label: '股票', fund_type: '指数型-股票', trade_rule: 'T+1', tracking_target: '沪深300指数', manager: '华泰柏瑞基金管理有限公司', management_fee_pct: 0.15, custody_fee_pct: 0.05, net_assets_billion: 948.72, asset_date: '2026-06-30', latest_nav: 4.7068, nav_date: '2026-08-12', subscribe_status: '开放申购', redeem_status: '开放赎回', ret5_pct: 0.62, ret20_pct: 2.17, ret60_pct: 7.83, volatility20_pct: 16.31, drawdown60_pct: -4.22, trend: 'MA20上方 · MA60上方', history_date: '2026-08-12', profile_source: '天天基金公开档案', refreshed_at: '2026-08-13T09:00:00+08:00', missing: [] },
  bond: { kind: 'bond', kind_label: '债券', fund_type: '债券型', trade_rule: 'T+0', tracking_target: '中债10年期国债指数', manager: '国泰基金管理有限公司', management_fee_pct: 0.20, custody_fee_pct: 0.05, net_assets_billion: 31.42, asset_date: '2026-06-30', latest_nav: 135.6122, nav_date: '2026-08-12', subscribe_status: '开放申购', redeem_status: '开放赎回', ret5_pct: 0.08, ret20_pct: -0.12, ret60_pct: 0.53, volatility20_pct: 2.08, drawdown60_pct: -0.72, trend: 'MA20下方 · MA60上方', history_date: '2026-08-12', profile_source: '天天基金公开档案', refreshed_at: '2026-08-13T09:00:00+08:00', missing: [] },
}

function item(code, name, price, change, profile, index, state = 'complete') {
  const prev = price / (1 + change / 100)
  return {
    id: `audit-${code}`, code, name, group: '', note: '', state,
    missing: state === 'complete' ? [] : ['基金档案'],
    quote: {
      name, price, change_pct: change, prev_close: prev, open: prev * 1.002,
      high: price * 1.006, low: prev * 0.995, amplitude_pct: 1.1,
      volume: 128000000 + index * 1000000, amount: 2210000000 + index * 210000000,
      turnover_rate: 5.31 - index * 0.24, volume_ratio: 1.12 + index * 0.03,
      total_mv: 19200000000 + index * 1800000000, source: index % 2 ? '腾讯财经' : 'TDX API',
      as_of: '2026-08-13T10:31:00+08:00', stale: false,
    },
    profile,
  }
}

const etfFixture = {
  ok: true, schema_version: 'etf-watch-v1', ts: '2026-08-13 10:31:00', dual_cycle_zones: false,
  low_frequency_refresh: 'explicit_background', missing: [],
  items: [
    item('513100', '纳指ETF国泰', 2.215, -2.03, profiles.cross, 0),
    item('159941', '纳指ETF广发', 1.659, -1.48, { ...profiles.cross, manager: '广发基金管理有限公司' }, 1),
    item('510300', '沪深300ETF华泰柏瑞', 4.728, -0.65, profiles.equity, 2),
    item('511260', '十年国债ETF国泰', 135.526, -0.03, profiles.bond, 3),
    item('518880', '黄金ETF华安', 9.009, 0.04, { ...profiles.cross, kind: 'commodity', kind_label: '商品', fund_type: '商品型', tracking_target: '黄金现货', manager: '华安基金管理有限公司' }, 4),
    item('159915', '创业板ETF易方达', 3.565, 0.17, null, 5, 'degraded'),
  ],
}

function apiBody(path) {
  if (path === '/api/etf-watch') return etfFixture
  return shellFixtures[path] || { ok: true }
}

const server = await createServer({
  root, configFile: false, plugins: [vue()], server: { host: '127.0.0.1', port: 4177 },
})
await server.listen()

let browser
try {
  await mkdir(outputDir, { recursive: true })
  browser = await chromium.launch({ executablePath: chromePath, headless: true })
  const audits = []
  for (const target of [
    { name: 'wide-1440-dark', width: 1440, height: 1000, theme: 'dark' },
    { name: 'landscape-900-dark', width: 900, height: 600, theme: 'dark' },
    { name: 'phone-375-dark', width: 375, height: 812, theme: 'dark' },
    { name: 'wide-1440-light', width: 1440, height: 1000, theme: 'light' },
  ]) {
    const page = await browser.newPage({ viewport: { width: target.width, height: target.height }, deviceScaleFactor: 1 })
    const pageErrors = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.addInitScript((theme) => localStorage.setItem('wudi-vue-theme', theme), target.theme)
    if (target.name === 'phone-375-dark') await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' })
    await page.route('**/api/**', async (route) => {
      const body = apiBody(new URL(route.request().url()).pathname)
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    })
    await page.goto('http://127.0.0.1:4177/#/risk?tab=etf-watch', { waitUntil: 'networkidle' })
    await page.waitForSelector('.etf-row-item')
    await page.addScriptTag({ content: axeSource })
    const axe = await page.evaluate(async () => window.axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    }))
    const layout = await page.evaluate(() => {
      const root = document.documentElement
      const pageRoot = document.querySelector('.etf-watch-page')
      const firstRow = document.querySelector('.etf-row')
      const targets = [...pageRoot.querySelectorAll('button, input, select, textarea')]
        .filter((node) => {
          const style = getComputedStyle(node)
          const rect = node.getBoundingClientRect()
          return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
        })
        .map((node) => {
          const rect = node.getBoundingClientRect()
          return { label: node.getAttribute('aria-label') || node.textContent?.trim() || node.tagName, width: rect.width, height: rect.height }
        })
      const text = pageRoot.textContent || ''
      return {
        noHorizontalOverflow: root.scrollWidth <= window.innerWidth && pageRoot.scrollWidth <= pageRoot.clientWidth,
        rowCount: document.querySelectorAll('.etf-row-item').length,
        firstRowHeight: Math.round(firstRow.getBoundingClientRect().height),
        titleExact: document.querySelector('#etf-watch-page-title')?.textContent === 'ETF观察',
        noDualCycleColumns: !text.includes('超短线区间') && !text.includes('波段区间'),
        groupedPending: text.includes('基金资料补充中') && text.includes('历史指标补充中'),
        excludedFieldsExplained: text.includes('PE/PB、五档盘口、IOPV、资金分单'),
        undersizedTargets: targets.filter((item) => item.width < 44 || item.height < 44),
      }
    })
    await page.screenshot({ path: resolve(outputDir, `${target.name}.png`), fullPage: true })
    audits.push({
      target, layout, pageErrors,
      violations: axe.violations.map((entry) => ({ id: entry.id, impact: entry.impact, help: entry.help })),
    })
    await page.close()
  }

  const interactionPage = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  await interactionPage.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await interactionPage.route('**/api/**', async (route) => {
    const body = apiBody(new URL(route.request().url()).pathname)
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
  await interactionPage.goto('http://127.0.0.1:4177/#/risk?tab=etf-watch', { waitUntil: 'networkidle' })
  await interactionPage.getByRole('button', { name: '跨境 2' }).click()
  const filterCount = await interactionPage.locator('.etf-row-item').count()
  await interactionPage.getByRole('button', { name: '添加ETF' }).click()
  const modal = await interactionPage.evaluate(() => {
    const dialog = document.querySelector('dialog.etf-dialog')
    return {
      open: dialog instanceof HTMLDialogElement && dialog.open,
      nativeDialog: dialog instanceof HTMLDialogElement,
      focusedInput: document.activeElement?.id === 'etf-code-input',
    }
  })
  await interactionPage.getByRole('button', { name: '关闭添加ETF窗口' }).click()
  const focusRestored = await interactionPage.getByRole('button', { name: '添加ETF' }).evaluate((node) => document.activeElement === node)
  await interactionPage.close()

  const report = {
    ok: audits.every((audit) => audit.pageErrors.length === 0
      && audit.violations.length === 0
      && audit.layout.noHorizontalOverflow
      && audit.layout.rowCount === 6
      && audit.layout.titleExact
      && audit.layout.noDualCycleColumns
      && audit.layout.groupedPending
      && audit.layout.excludedFieldsExplained
      && audit.layout.undersizedTargets.length === 0)
      && filterCount === 2 && Object.values(modal).every(Boolean) && focusRestored,
    audits,
    interaction: { filterCount, modal, focusRestored },
  }
  await writeFile(resolve(outputDir, 'etf-watch-audit.json'), JSON.stringify(report, null, 2), 'utf8')
  console.log(JSON.stringify(report, null, 2))
  console.log(`Audit output: ${outputDir}`)
  if (!report.ok) process.exitCode = 1
} finally {
  if (browser) await browser.close()
  await server.close()
}
