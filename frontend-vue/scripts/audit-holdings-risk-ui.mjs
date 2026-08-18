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

const plans = {
  '601899': { version: 1, period: 'short', buy_date: '2026-08-11', customer_risk_price: 17.60, customer_target_price: 19.20, target_mode: 'reminder', confirmed_reduction_pct: 0 },
  '300750': { version: 1, period: 'swing', buy_date: '2026-08-07', customer_risk_price: null, customer_target_price: 295.00, target_mode: 'ordinary_trigger', confirmed_reduction_pct: 25 },
  '002594': { version: 1, period: 'ultra', buy_date: '2026-08-17', customer_risk_price: 109.80, customer_target_price: null, target_mode: 'reminder', confirmed_reduction_pct: 0 },
}
const riskStates = {
  '601899': { code: '601899', name: '紫金矿业', plan_version: 1, period: 'short', period_label: '短线', period_window: '3–5个交易日', state: 'pending_adjustment', action: '建议将该持仓降低33%', target_reduction_pct: 33, retain_pct: 67, confirmed_reduction_pct: 0, ordinary_trigger_count: 2, ordinary_triggers: [{ code: 'time_expired', label: '已超出短线区间（3–5个交易日）' }, { code: 'stagnation_group', label: '收益观察区内出现2项滞涨特征' }], decisive_triggers: [], near_risk_line: false, time: { trading_days_held: 5, max_days: 5, expired: true }, initial_risk_line: 16.88, current_risk_line: 17.75, customer_risk_price: 17.60, customer_target_price: 19.20, risk_distance_pct: 4.98, initial_r: 0.54, r_multiple: 2.33, fresh: true, stale: false, evaluated_at: '2026-08-17T10:30:00+08:00', data_as_of: '2026-08-17 10:00:00' },
  '300750': { code: '300750', name: '宁德时代', plan_version: 1, period: 'swing', period_label: '波段', period_window: '5–10个交易日', state: 'completed_adjustment', action: '已完成当前调整，继续监控', target_reduction_pct: 25, retain_pct: 75, confirmed_reduction_pct: 25, ordinary_trigger_count: 0, ordinary_triggers: [], decisive_triggers: [], near_risk_line: false, target_touched: true, target_note: '达到客户目标价，仅提醒', time: { trading_days_held: 7, max_days: 10, expired: false }, initial_risk_line: 260.10, current_risk_line: 278.40, customer_risk_price: null, customer_target_price: 295.00, risk_distance_pct: 3.07, initial_r: 14.70, r_multiple: 0.84, fresh: true, stale: false, evaluated_at: '2026-08-17T10:30:00+08:00', data_as_of: '2026-08-14' },
  '002594': { code: '002594', name: '比亚迪', plan_version: 1, period: 'ultra', period_label: '超短', period_window: '1–3个交易日', state: 'monitoring', action: '当前无需调整仓位', target_reduction_pct: 0, retain_pct: 100, confirmed_reduction_pct: 0, ordinary_trigger_count: 0, ordinary_triggers: [], decisive_triggers: [], near_risk_line: true, time: { trading_days_held: 1, max_days: 3, expired: false }, initial_risk_line: 112.20, current_risk_line: 112.40, customer_risk_price: 109.80, customer_target_price: null, risk_distance_pct: 0.57, initial_r: 4.00, r_multiple: -0.79, execution_constraints: ['买入当日受T+1限制，可调整数量可能为0'], fresh: true, stale: false, evaluated_at: '2026-08-17T10:30:00+08:00', data_as_of: '2026-08-17 10:25:00' },
}
const fixtures = {
  '/api/holdings': { ok: true, holdings: [
    { code: '601899', name: '紫金矿业', cost: 17.42, shares: 6000, industry: '工业金属', market_value: 112080, note: '核心观察仓', risk_plan: plans['601899'] },
    { code: '300750', name: '宁德时代', cost: 274.80, shares: 300, industry: '电池', market_value: 86166, note: '只按计划处理', risk_plan: plans['300750'] },
    { code: '002594', name: '比亚迪', cost: 116.20, shares: 500, industry: '汽车整车', market_value: 56525, note: '', risk_plan: plans['002594'] },
  ], risk_states: riskStates },
  '/api/holdings/risk': { ok: true, schema_version: 'holding-risk-evaluation-v1', algorithm_version: '1.0', states: riskStates },
  '/api/quotes': { ok: true, quotes: {
    '601899': { code: '601899', name: '紫金矿业', price: 18.68, change_pct: 2.30, source: 'TDX API', stale: false, as_of: '2026-08-17 10:31:18' },
    '300750': { code: '300750', name: '宁德时代', price: 287.22, change_pct: 1.45, source: '腾讯财经', stale: false, as_of: '2026-08-17 10:31:19' },
    '002594': { code: '002594', name: '比亚迪', price: 113.05, change_pct: -1.17, source: 'TDX API', stale: false, as_of: '2026-08-17 10:31:18' },
  }, missing: [] },
  '/api/shell-status': { ok: true, now_cn: '2026-08-17 10:31:20', market_phase: '交易中', client: { brand: '审计目标' } },
}

await mkdir(outputDir, { recursive: true })
const server = await createServer({ root, configFile: false, plugins: [vue()], server: { host: '127.0.0.1', port: 4176, strictPort: true }, logLevel: 'error' })
await server.listen()
const browser = await chromium.launch({ executablePath: chromePath, headless: true })
const results = []

try {
  for (const scenario of [
    { name: 'phone-375-dark', width: 375, height: 812, theme: 'dark' },
    { name: 'phone-landscape-dark', width: 812, height: 375, theme: 'dark' },
    { name: 'wide-1440-dark', width: 1440, height: 1000, theme: 'dark' },
    { name: 'wide-1440-light', width: 1440, height: 1000, theme: 'light' },
  ]) {
    const page = await browser.newPage({ viewport: { width: scenario.width, height: scenario.height }, deviceScaleFactor: 1 })
    const pageErrors = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.addInitScript((theme) => localStorage.setItem('wudi-vue-theme', theme), scenario.theme)
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      const body = fixtures[path] || { ok: true }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    })
    await page.goto('http://127.0.0.1:4176/#/risk?tab=holdings', { waitUntil: 'networkidle' })
    await page.waitForSelector('.holding-risk-card')
    await page.addScriptTag({ content: axeSource })
    const violations = await page.evaluate(async () => (await window.axe.run(document, { rules: { 'color-contrast': { enabled: true } } })).violations)
    const layout = await page.evaluate(() => ({
      noPageOverflow: document.documentElement.scrollWidth <= window.innerWidth,
      cardCount: document.querySelectorAll('.holding-risk-card').length,
      hasPercentAction: document.body.textContent.includes('建议将该持仓降低33%'),
      hasExecutionLimit: document.body.textContent.includes('T+1限制'),
    }))
    await page.screenshot({ path: resolve(outputDir, `holdings-risk-${scenario.name}.png`), fullPage: true })
    results.push({ scenario: scenario.name, pageErrors, violations: violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help })), layout })
    await page.close()
  }
} finally {
  await browser.close()
  await server.close()
}

const failed = results.some((item) => item.pageErrors.length || item.violations.length || !Object.values(item.layout).every(Boolean))
await writeFile(resolve(outputDir, 'holdings-risk-audit.json'), JSON.stringify({ failed, results }, null, 2), 'utf8')
console.log(JSON.stringify({ failed, results }, null, 2))
if (failed) process.exitCode = 1
