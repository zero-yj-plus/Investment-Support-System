import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'

const require = createRequire(import.meta.url)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const outputDir = process.env.HOLDINGS_RISK_AUDIT_DIR || resolve(root, 'audit-output')
const axeSource = await readFile(require.resolve('axe-core/axe.min.js'), 'utf8')
const chromePath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'

const rows = {
  '300750': {
    code: '300750', name: '宁德时代', rule_version: 'holding-risk-v1-shadow-r2',
    shadow_mode: true, push_eligible: false, state: 'red', action: '退出当前可卖持仓',
    signal_type: 'risk_exit', signal_type_label: '风险退出', confidence: 'high',
    summary: '价格结构已经确认失效', reasons: ['有效跌破算法风险线 386.66'],
    horizon: 'short', horizon_label: '短线', entry_type: 'general', entry_type_label: '未指定',
    as_of: '2026-08-26', price_basis: '实时价', coverage: 1,
    levels: { initial_stop: 376.20, initial_stop_basis: 'MA20支撑下方', algorithm_stop: 386.66, trailing_stop: 386.66, support: 388.10, risk_unit: 14.95 },
    metrics: { ma5: 391.20, ma20: 389.70, ma60: 372.80, atr14: 5.62 },
    sector: { name: '电池', change_pct: -0.12, stock_change_pct: 0.84, relative_today_pct: 0.96, data_date: '2026-08-26', source: '公开行业行情' },
    missing: [], fresh: true, stale: false, evaluated_at: '2026-08-26T09:27:53+08:00',
  },
  '510300': {
    code: '510300', name: '沪深300ETF华泰柏瑞', rule_version: 'holding-risk-v1-shadow-r2',
    shadow_mode: true, push_eligible: false, state: 'red', action: '退出当前可卖持仓',
    signal_type: 'risk_exit', signal_type_label: '风险退出', confidence: 'high',
    summary: '价格结构已经确认失效', reasons: ['有效跌破算法风险线 4.62'],
    horizon: 'overnight', horizon_label: '隔日', entry_type: 'general', entry_type_label: '未指定',
    as_of: '2026-08-26', price_basis: '实时价', coverage: 1,
    levels: { initial_stop: 4.50, initial_stop_basis: 'ATR备用风险线', algorithm_stop: 4.617, trailing_stop: 4.617, support: 4.60, risk_unit: 0.18 },
    metrics: { ma5: 4.64, ma20: 4.58, ma60: 4.42, atr14: 0.06 },
    sector: { name: '沪深300', change_pct: -0.06, stock_change_pct: -0.02, relative_today_pct: 0.04, data_date: '2026-08-26', source: '指数行情' },
    missing: [], fresh: true, stale: false, evaluated_at: '2026-08-26T09:27:53+08:00',
  },
  '600519': {
    code: '600519', name: '贵州茅台', rule_version: 'holding-risk-v1-shadow-r2',
    shadow_mode: true, push_eligible: false, state: 'yellow', action: '继续持有，观察突破',
    signal_type: 'warning', signal_type_label: '风险观察', confidence: 'medium',
    summary: '进入收益观察区', reasons: ['已进入第一收益观察区，尚未形成滞涨确认'],
    horizon: 'swing', horizon_label: '波段', entry_type: 'pullback', entry_type_label: '回踩',
    as_of: '2026-08-26', price_basis: '实时价', coverage: 1,
    levels: { initial_stop: 1253.715, initial_stop_basis: '回踩低点下方', algorithm_stop: 1253.715, trailing_stop: null, support: 1270.50, risk_unit: 19.115 },
    metrics: { ma5: 1288.30, ma20: 1267.20, ma60: 1238.60, atr14: 18.40 },
    sector: { name: '白酒', change_pct: -0.78, stock_change_pct: -0.31, relative_today_pct: 0.47, data_date: '2026-08-26', source: '公开行业行情' },
    missing: [], fresh: true, stale: false, evaluated_at: '2026-08-26T09:27:53+08:00',
  },
}

const fixtures = {
  '/api/holdings': {
    ok: true,
    holdings: [
      { code: '300750', name: '宁德时代', cost: 391.15, shares: 100, industry: '电池', market_value: 39115, weight_pct: null, stop_loss_price: 386.66, take_profit_price: null, horizon: 'short', entry_type: 'general', entry_date: '2026-08-01', note: '' },
      { code: '510300', name: '沪深300ETF华泰柏瑞', cost: 4.68, shares: 1000, industry: '', market_value: 4680, weight_pct: null, stop_loss_price: null, take_profit_price: null, horizon: 'overnight', entry_type: 'general', entry_date: '2026-08-25', note: '' },
      { code: '600519', name: '贵州茅台', cost: 1272.83, shares: 100, industry: '白酒', market_value: 127283, weight_pct: null, stop_loss_price: null, take_profit_price: 1360, horizon: 'swing', entry_type: 'pullback', entry_date: '2026-07-28', note: '' },
    ],
    portfolio_total_asset: null,
  },
  '/api/holdings/risk': {
    ok: true, shadow_mode: true, push_enabled: false,
    rule_version: 'holding-risk-v1-shadow-r2',
    market_context: { benchmark: '沪深300', data_date: '2026-08-26' },
    rows,
  },
  '/api/holdings/risk/refresh': {
    ok: true, shadow_mode: true, push_enabled: false,
    rule_version: 'holding-risk-v1-shadow-r2',
    market_context: { benchmark: '沪深300', data_date: '2026-08-26' },
    rows,
  },
  '/api/quotes': {
    ok: true,
    quotes: {
      '300750': { code: '300750', name: '宁德时代', price: 379.90, change_pct: 0.84, source: '腾讯财经', stale: false, as_of: '2026-08-26 09:27:53' },
      '510300': { code: '510300', name: '沪深300ETF华泰柏瑞', price: 4.615, change_pct: -0.02, source: '腾讯财经', stale: false, as_of: '2026-08-26 09:27:53' },
      '600519': { code: '600519', name: '贵州茅台', price: 1300, change_pct: -0.31, source: '腾讯财经', stale: false, as_of: '2026-08-26 09:27:53' },
    },
    missing: [],
  },
  '/api/shell-status': { ok: true, now_cn: '2026-08-26 09:27:53', market_phase: '交易中', client: { brand: '审计目标' } },
}

function summarizeViolations(violations) {
  return violations.map((item) => ({
    id: item.id,
    impact: item.impact,
    help: item.help,
    nodes: item.nodes.map((node) => ({
      target: node.target,
      html: node.html,
      failureSummary: node.failureSummary,
    })),
  }))
}

await mkdir(outputDir, { recursive: true })
const server = await createServer({
  root,
  configFile: false,
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 4176, strictPort: true },
  logLevel: 'error',
})
await server.listen()
const browser = await chromium.launch({ executablePath: chromePath, headless: true })
const results = []

try {
  for (const scenario of [
    { name: 'phone-375-dark', width: 375, height: 812, theme: 'dark' },
    { name: 'phone-landscape-dark', width: 812, height: 375, theme: 'dark' },
    { name: 'wide-1280-dark', width: 1280, height: 900, theme: 'dark' },
    { name: 'wide-1440-light', width: 1440, height: 1000, theme: 'light' },
    { name: 'wide-1920-light', width: 1920, height: 1080, theme: 'light' },
  ]) {
    const page = await browser.newPage({
      viewport: { width: scenario.width, height: scenario.height },
      deviceScaleFactor: 1,
    })
    const pageErrors = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.addInitScript((theme) => localStorage.setItem('wudi-vue-theme', theme), scenario.theme)
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      const body = fixtures[path] || { ok: true }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })
    })
    await page.goto('http://127.0.0.1:4176/#/risk?tab=holdings', { waitUntil: 'networkidle' })
    await page.waitForSelector('.position-card')
    await page.addScriptTag({ content: axeSource })
    const violations = await page.evaluate(async () => (
      await window.axe.run(document, { rules: { 'color-contrast': { enabled: true } } })
    ).violations)
    const pageHeightBeforeDialog = await page.evaluate(() => document.documentElement.scrollHeight)
    const layout = await page.evaluate(() => {
      const cards = [...document.querySelectorAll('.position-card')]
      const list = document.querySelector('.position-list')?.getBoundingClientRect()
      const firstSnapshot = document.querySelector('.position-snapshot')?.getBoundingClientRect()
      const firstDecision = document.querySelector('.decision-panel')?.getBoundingClientRect()
      const targets = [...document.querySelectorAll('.holdings-page button, .holdings-page summary')]
        .filter((node) => node.getClientRects().length)
        .map((node) => node.getBoundingClientRect())
      return {
        noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
        cardCount: cards.length === 3,
        cardsUseFullWidth: Boolean(list && cards.every((card) => Math.abs(card.getBoundingClientRect().width - list.width) <= 2)),
        portfolioMetricCount: document.querySelectorAll('.portfolio-metrics > div').length === 5,
        portfolioActionVisible: document.body.textContent.includes('今日需处理 2只'),
        actionsVisible: document.body.textContent.includes('退出当前可卖持仓') && document.body.textContent.includes('继续持有，观察突破'),
        touchTargets: targets.every((rect) => rect.height >= 43.5),
        desktopTwoColumns: window.innerWidth <= 820 || Boolean(firstSnapshot && firstDecision && Math.abs(firstSnapshot.top - firstDecision.top) <= 2),
        mobileStacked: window.innerWidth > 820 || Boolean(firstSnapshot && firstDecision && firstDecision.top >= firstSnapshot.bottom - 2),
      }
    })
    await page.screenshot({
      path: resolve(outputDir, 'holdings-risk-' + scenario.name + '.png'),
      fullPage: true,
    })

    await page.locator('.evidence-popover summary').first().click()
    await page.waitForSelector('.evidence-popover[open]')
    const evidenceViolations = await page.evaluate(async () => (
      await window.axe.run(document, { rules: { 'color-contrast': { enabled: true } } })
    ).violations)
    const evidenceLayout = await page.evaluate(() => {
      const popover = document.querySelector('.evidence-popover[open] .evidence-popover__content')
      const rect = popover?.getBoundingClientRect()
      return {
        detailsVisible: document.body.textContent.includes('有效跌破算法风险线 386.66'),
        noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
        withinViewport: Boolean(rect && rect.left >= 0 && rect.top >= 0 && rect.right <= window.innerWidth && rect.bottom <= window.innerHeight),
      }
    })
    await page.screenshot({ path: resolve(outputDir, 'holdings-evidence-' + scenario.name + '.png') })
    await page.locator('.holdings-board__header h2').click()
    await page.waitForFunction(() => !document.querySelector('.evidence-popover[open]'))
    const evidenceClosedOnOutsideClick = await page.evaluate(() => !document.querySelector('.evidence-popover[open]'))

    await page.getByRole('button', { name: '添加持仓', exact: true }).click()
    await page.waitForSelector('.holding-dialog[open]')
    const dialogViolations = await page.evaluate(async () => (
      await window.axe.run(document, { rules: { 'color-contrast': { enabled: true } } })
    ).violations)
    const dialogLayout = await page.evaluate((heightBefore) => {
      const dialog = document.querySelector('.holding-dialog')
      const rect = dialog?.getBoundingClientRect()
      const targets = [...document.querySelectorAll('.holding-dialog button, .holding-dialog input, .holding-dialog select, .holding-dialog textarea')]
        .filter((node) => node.getClientRects().length)
        .map((node) => node.getBoundingClientRect())
      return {
        modalTopLayer: dialog instanceof HTMLDialogElement && dialog.matches(':modal'),
        pageFlowStable: document.documentElement.scrollHeight === heightBefore,
        noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
        focusInsideDialog: dialog instanceof HTMLDialogElement && dialog.contains(document.activeElement),
        withinViewport: Boolean(rect && rect.top >= 0 && rect.left >= 0 && rect.right <= window.innerWidth && rect.bottom <= window.innerHeight),
        touchTargets: targets.every((rect) => rect.height >= 43.5),
      }
    }, pageHeightBeforeDialog)
    await page.mouse.click(2, 2)
    const editorStayedOpenAfterBackdropClick = await page.locator('.holding-dialog[open]').count() === 1
    await page.screenshot({ path: resolve(outputDir, 'holdings-add-dialog-' + scenario.name + '.png') })
    await page.keyboard.press('Escape')
    await page.waitForSelector('.holding-dialog[open]', { state: 'detached' })
    await page.waitForFunction(() => document.activeElement?.textContent?.trim() === '添加持仓')
    const focusReturnedToTrigger = await page.evaluate(() => document.activeElement?.textContent?.trim() === '添加持仓')

    results.push({
      scenario: scenario.name,
      pageErrors,
      violations: summarizeViolations(violations),
      evidenceViolations: summarizeViolations(evidenceViolations),
      dialogViolations: summarizeViolations(dialogViolations),
      layout,
      evidenceLayout,
      dialogLayout,
      evidenceClosedOnOutsideClick,
      editorStayedOpenAfterBackdropClick,
      focusReturnedToTrigger,
    })
    await page.close()
  }
} finally {
  await browser.close()
  await server.close()
}

const failed = results.some((item) => (
  item.pageErrors.length
  || item.violations.length
  || item.evidenceViolations.length
  || item.dialogViolations.length
  || !Object.values(item.layout).every(Boolean)
  || !Object.values(item.evidenceLayout).every(Boolean)
  || !Object.values(item.dialogLayout).every(Boolean)
  || !item.evidenceClosedOnOutsideClick
  || !item.editorStayedOpenAfterBackdropClick
  || !item.focusReturnedToTrigger
))
await writeFile(
  resolve(outputDir, 'holdings-risk-audit.json'),
  JSON.stringify({ failed, results }, null, 2),
  'utf8',
)
console.log(JSON.stringify({ failed, results }, null, 2))
if (failed) process.exitCode = 1
