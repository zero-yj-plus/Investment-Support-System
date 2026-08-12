import { createRequire } from 'node:module'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'
import { createServer } from 'vite'

const require = createRequire(import.meta.url)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const outputDir = resolve(root, 'audit-output')
const axeSource = await readFile(require.resolve('axe-core/axe.min.js'), 'utf8')
const chromePath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const viewports = [
  { name: 'wide-1440-dark', width: 1440, height: 1000, theme: 'dark' },
  { name: 'phone-375-dark', width: 375, height: 812, theme: 'dark' },
  { name: 'wide-1440-light', width: 1440, height: 1000, theme: 'light' },
]

await mkdir(outputDir, { recursive: true })
const server = await createServer({ root, server: { host: '127.0.0.1', port: 4177, strictPort: true }, logLevel: 'error' })
await server.listen()
const browser = await chromium.launch({ executablePath: chromePath, headless: true })
const report = []

function initialOrder() {
  return {
    order_id: 'sim-audit-001', status: 'proposed', symbol: '600519', side: 'buy', order_type: 'limit',
    quantity: 100, limit_price: 1420, reference_price: 1418.5, risk_level: 2,
    data_as_of: '2026-08-11T10:30:00+08:00', data_stale: false, data_missing: [],
    price_mode: 'live', trade_date: '2026-08-11', market_price: null, slippage_bps: null,
    total_fee: null, rejection_reason: null, simulation_only: true,
    events: [{ event_id: 1, event_type: 'proposed', from_status: null, to_status: 'proposed', created_at: '2026-08-11T10:30:00+08:00' }],
  }
}

function initialAccount() {
  return {
    schema_version: 'simulation-account.v1', initialized: true, simulation_only: true,
    broker_connected: false, initial_cash: 1000000, cash: 1000000, market_value: 0,
    total_assets: 1000000, realized_pnl: 0, unrealized_pnl: 0, total_pnl: 0,
    return_pct: 0, total_fees: 0, valuation_state: 'exact', missing_symbols: [],
    trade_date: '2026-08-11', positions: [], fee_settings: {
      commission_rate_pct: 0.03, minimum_commission: 5, stamp_duty_rate_pct: 0.05,
      transfer_fee_rate_pct: 0.002, slippage_bps: 0,
    },
    equity_curve: [{
      snapshot_id: 'eq-audit-001', reason: 'account_initialized', order_id: null,
      trade_date: null, cash: 1000000, market_value: 0, total_assets: 1000000,
      realized_pnl: 0, unrealized_pnl: 0, total_pnl: 0, total_fees: 0,
      valuation_state: 'exact', missing_symbols: [], created_at: '2026-08-11T09:30:00+08:00',
    }],
  }
}

try {
  for (const viewport of viewports) {
    let order = initialOrder()
    let account = initialAccount()
    let emergencyActive = false
    let emergencyReason = null
    let eventSeed = 1
    const page = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } })
    await page.addInitScript((theme) => localStorage.setItem('wudi-vue-theme', theme), viewport.theme)
    await page.route('**/api/**', async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      const method = request.method()
      let body = { ok: false, error: '定向审计未提供该接口' }
      let statusCode = 200

      if (path === '/api/simulation/status') {
        body = {
          schema_version: 'simulation-status.v2', mode: 'simulation_only', simulation_only: true,
          broker_connected: false, broker_interface_present: false, manual_approval_required: true,
          account_initialized: true, settlement_rule: 'A_SHARE_T_PLUS_1',
          emergency_stop: { active: emergencyActive, reason: emergencyReason, updated_at: '2026-08-11T10:31:00+08:00' },
          counts: { proposed: order.status === 'proposed' ? 1 : 0, approved: order.status === 'approved' ? 1 : 0, filled: order.status === 'filled' ? 1 : 0, rejected: 0, cancelled: order.status === 'cancelled' ? 1 : 0 },
          latest_event: order.events.at(-1), disclaimer: '仅模拟记录，不连接券商，不会产生真实委托或成交。',
        }
      } else if (path === '/api/simulation/account' && method === 'GET') {
        body = { ok: true, ...account }
      } else if (path === '/api/simulation/account/settings' && method === 'POST') {
        account = { ...account, fee_settings: { ...request.postDataJSON() } }
        delete account.fee_settings.client_request_id
        body = { ok: true, simulation_only: true, account }
      } else if (path === '/api/simulation/account/valuation' && method === 'POST') {
        account = { ...account, equity_curve: [...account.equity_curve, { ...account.equity_curve.at(-1), snapshot_id: `eq-audit-${account.equity_curve.length + 1}`, reason: 'manual_valuation', created_at: '2026-08-11T10:35:00+08:00' }] }
        body = { ok: true, simulation_only: true, account }
      } else if (path === '/api/simulation/orders' && method === 'GET') {
        body = { ok: true, simulation_only: true, items: [order], count: 1 }
      } else if (path === '/api/simulation/orders' && method === 'POST') {
        const payload = request.postDataJSON()
        order = {
          ...initialOrder(), order_id: 'sim-audit-002', symbol: payload.symbol, side: payload.side,
          order_type: payload.order_type, quantity: payload.quantity, limit_price: payload.limit_price,
        }
        body = { ok: true, simulation_only: true, order }
      } else if (path.endsWith('/approve') && method === 'POST') {
        eventSeed += 1
        order = { ...order, status: 'approved', approved_by: request.postDataJSON().approved_by, events: [...order.events, { event_id: eventSeed, event_type: 'approved', from_status: 'proposed', to_status: 'approved', created_at: '2026-08-11T10:31:00+08:00' }] }
        body = { ok: true, simulation_only: true, order }
      } else if (path.endsWith('/fill') && method === 'POST') {
        eventSeed += 1
        order = { ...order, status: 'filled', market_price: 1419, slippage_bps: 3.52, total_fee: 47.58, events: [...order.events, { event_id: eventSeed, event_type: 'filled', from_status: 'approved', to_status: 'filled', created_at: '2026-08-11T10:32:00+08:00' }] }
        account = {
          ...account, cash: 858052.42, market_value: 141900, total_assets: 999952.42,
          unrealized_pnl: -47.58, total_pnl: -47.58, return_pct: -0.0048, total_fees: 47.58,
          positions: [{ symbol: '600519', quantity: 100, available_quantity: 0, t1_locked_quantity: 100, next_sellable_date: '2026-08-12', average_cost: 1419.4758, market_price: 1419, market_value: 141900, unrealized_pnl: -47.58, price_mode: 'live', valuation_state: 'exact' }],
          equity_curve: [...account.equity_curve, { snapshot_id: 'eq-audit-fill', reason: 'order_filled', order_id: order.order_id, trade_date: '2026-08-11', cash: 858052.42, market_value: 141900, total_assets: 999952.42, realized_pnl: 0, unrealized_pnl: -47.58, total_pnl: -47.58, total_fees: 47.58, valuation_state: 'exact', missing_symbols: [], created_at: '2026-08-11T10:32:00+08:00' }],
        }
        body = { ok: true, simulation_only: true, order }
      } else if (path.endsWith('/cancel') && method === 'POST') {
        order = { ...order, status: 'cancelled', rejection_reason: 'operator_cancelled_from_vue' }
        body = { ok: true, simulation_only: true, order }
      } else if (path === '/api/simulation/emergency-stop' && method === 'POST') {
        emergencyActive = true
        emergencyReason = request.postDataJSON().reason
        body = { ok: true, simulation_only: true }
      } else if (path === '/api/simulation/emergency-stop/reset' && method === 'POST') {
        emergencyActive = false
        emergencyReason = null
        body = { ok: true, simulation_only: true }
      } else if (path === '/api/status') {
        body = { ok: true, client: { brand: 'W.U.D.I.' }, market_context: { now: '2026-08-11T10:30:00+08:00', market_session: 'intraday', is_trade_day: true } }
      } else {
        statusCode = 503
      }
      await route.fulfill({ status: statusCode, contentType: 'application/json', body: JSON.stringify(body) })
    })

    await page.goto('http://127.0.0.1:4177/#/risk?tab=simulation', { waitUntil: 'networkidle' })
    await page.locator('#simulation-page-title').waitFor()
    await page.addScriptTag({ content: axeSource })

    if (viewport.width >= 1000 && viewport.theme === 'dark') {
      await page.getByRole('button', { name: '人工批准' }).click()
      await page.getByLabel('人工批准人').fill('审计操作员')
      await page.getByRole('button', { name: '确认人工批准' }).click()
      await page.getByText('已批准', { exact: true }).first().waitFor()
      await page.getByRole('button', { name: '模拟成交' }).click()
      await page.getByRole('button', { name: '按服务端行情模拟成交' }).click()
      await page.getByText('已模拟成交', { exact: true }).first().waitFor()
      await page.getByRole('button', { name: '费用设置' }).click()
      await page.getByRole('button', { name: '保存后续成交设置' }).click()
      await page.getByRole('button', { name: '记录当前估值' }).click()
      await page.getByRole('button', { name: '紧急停止', exact: true }).click()
      await page.getByLabel('停止原因').fill('定向 UI 审计')
      await page.getByRole('button', { name: '立即紧急停止' }).click()
      await page.getByRole('heading', { name: '紧急停止已开启' }).waitFor()
      await page.getByRole('button', { name: '人工复核后恢复' }).click()
      await page.getByLabel('人工复核人').fill('审计复核员')
      await page.getByLabel('恢复原因').fill('已核验定向审计状态')
      await page.getByRole('button', { name: '确认人工恢复' }).click()
      await page.getByRole('heading', { name: '紧急停止未开启' }).waitFor()
    }

    const checks = await page.evaluate(async () => {
      const targets = [...document.querySelectorAll('button')].map((element) => {
        const rect = element.getBoundingClientRect()
        return { text: element.textContent?.trim(), width: rect.width, height: rect.height, visible: rect.width > 0 && rect.height > 0 }
      }).filter((item) => item.visible)
      const axe = await globalThis.axe.run(document)
      return {
        overflow: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
        title: document.querySelector('#simulation-page-title')?.textContent?.trim(),
        boundaryVisible: document.body.textContent?.includes('不产生真实委托') === true,
        brokerBoundaryVisible: document.body.textContent?.includes('无券商连接') === true,
        accountVisible: document.body.textContent?.includes('累计盈亏') === true,
        t1Visible: document.body.textContent?.includes('A 股 T+1') === true,
        equityVisible: document.body.textContent?.includes('资产曲线') === true,
        navigationPresent: document.querySelector('#primary-navigation')?.textContent?.includes('模拟执行') === true,
        desktopTableVisible: getComputedStyle(document.querySelector('.simulation-table-wrap')).display !== 'none',
        mobileCardsVisible: getComputedStyle(document.querySelector('.simulation-cards')).display !== 'none',
        undersizedTargets: targets.filter((item) => item.height < 44),
        violations: axe.violations.map((item) => ({
          id: item.id,
          impact: item.impact,
          help: item.help,
          nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })),
        })),
      }
    })
    await page.screenshot({ path: resolve(outputDir, `simulation-${viewport.name}.png`), fullPage: true })
    report.push({ viewport, checks })
    await page.close()
  }
} finally {
  await browser.close()
  await server.close()
}

const failed = report.some(({ viewport, checks }) => (
  checks.overflow > 0
  || checks.title !== '模拟执行'
  || !checks.boundaryVisible
  || !checks.brokerBoundaryVisible
  || !checks.accountVisible
  || !checks.t1Visible
  || !checks.equityVisible
  || (viewport.width >= 1000 && (!checks.navigationPresent || !checks.desktopTableVisible))
  || (viewport.width < 1000 && !checks.mobileCardsVisible)
  || checks.undersizedTargets.length > 0
  || checks.violations.length > 0
))
await writeFile(resolve(outputDir, 'simulation-report.json'), JSON.stringify({ failed, report }, null, 2), 'utf8')
if (failed) {
  process.stderr.write(JSON.stringify({ failed, report }, null, 2))
  process.exitCode = 1
} else {
  process.stdout.write(JSON.stringify({ failed, viewports: report.map((item) => item.viewport.name) }))
}
