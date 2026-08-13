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
const dates = [
  '2026-08-13', '2026-08-12', '2026-08-11', '2026-08-10', '2026-08-07',
  '2026-08-06', '2026-08-05', '2026-08-04', '2026-08-03', '2026-07-31',
  '2026-07-30', '2026-07-29', '2026-07-28', '2026-07-27', '2026-07-24',
  '2026-07-23', '2026-07-22', '2026-07-21', '2026-07-20', '2026-07-17',
]

const metric = (state, value = null, sample = 0, total = 6) => ({
  value,
  state,
  label: {
    pending: '待收盘', intraday: '盘中快照／未定稿', final: '收盘定稿',
    missing: '数据暂缺', unavailable: '暂不可用', not_applicable: '无样本',
  }[state],
  sample_size: sample,
  total_size: total,
  coverage: total ? sample / total : null,
  source: state === 'final' ? '腾讯财经·前复权日K' : state === 'intraday' ? '批量实时行情' : null,
  frozen_at: state === 'final' ? '2026-08-13T15:20:00+08:00' : null,
})

const rowResult = (date, state, value = null) => ({
  date,
  change_pct: value,
  state,
  label: { pending: '待追踪', intraday: '盘中', final: '收盘定稿', missing: '数据暂缺', unavailable: '暂不可用' }[state],
  source: value == null ? null : state === 'intraday' ? '批量实时行情' : '腾讯财经·前复权日K',
  market_time: value == null ? null : date,
})

const snapshot = (date) => {
  const modes = {
    '2026-08-13': { t1: 'pending', t2: 'pending', tag: '最新批次', t1v: null, t2v: null },
    '2026-08-12': { t1: 'intraday', t2: 'pending', tag: '盘中批次', t1v: 1.25, t2v: null },
    '2026-08-11': { t1: 'final', t2: 'missing', tag: '定稿批次', t1v: 2.5, t2v: null },
    '2026-08-10': { t1: 'unavailable', t2: 'unavailable', tag: '不可用批次', t1v: null, t2v: null },
  }
  const mode = modes[date] || { t1: 'missing', t2: 'missing', tag: '缺失批次', t1v: null, t2v: null }
  const index = dates.indexOf(date)
  const t1Date = dates[Math.max(0, index - 1)] || null
  const t2Date = dates[Math.max(0, index - 2)] || null
  const t1Sample = mode.t1 === 'final' || mode.t1 === 'intraday' ? 6 : 0
  const t2Sample = mode.t2 === 'final' || mode.t2 === 'intraday' ? 6 : 0
  const stocks = Array.from({ length: 6 }, (_, position) => {
    const change = position % 3 === 0 ? -1.2 : position % 3 === 1 ? 0 : 3.4
    return {
      position: position + 1,
      code: `00000${position + 1}`,
      name: `${mode.tag}${String.fromCharCode(30002 + position)}`,
      industry: position === 4 ? null : ['软件开发', '通信设备', '电网设备'][position % 3],
      lianban: position < 2 ? 3 - position : 1,
      lianban_label: position < 2 ? `${3 - position}连板` : '首板',
      baseline_change_pct: position === 4 ? null : 10.01,
      first_limit_time: position === 5 ? null : `09:${31 + position}:00`,
      source: '公开涨跌停池',
      t1: rowResult(t1Date, mode.t1, mode.t1v == null ? null : change),
      t2: rowResult(t2Date, mode.t2, mode.t2v == null ? null : change / 2),
    }
  })
  return {
    ok: true,
    selected_date: date,
    baseline_date: date,
    baseline_count: 6,
    tracking_status: mode.t1 === 'intraday' ? '盘中追踪／未定稿' : mode.t1 === 'final' ? '部分数据暂缺' : mode.t1 === 'pending' ? '等待后续交易日' : '部分数据暂缺',
    t1_date: t1Date,
    t2_date: t2Date,
    metrics: {
      t1_average: metric(mode.t1, mode.t1v, t1Sample),
      t1_red_rate: metric(mode.t1, mode.t1v == null ? null : 50, t1Sample),
      t1_promotion_rate: metric(mode.t1, mode.t1v == null ? null : 33.3, t1Sample),
      t2_average: metric(mode.t2, mode.t2v, t2Sample),
      t2_red_rate: metric(mode.t2, mode.t2v == null ? null : 50, t2Sample),
    },
    stocks,
    data_date: date,
    generated_at: '2026-08-13T15:21:00+08:00',
    market_time: mode.t1 === 'intraday' ? '2026-08-13T10:30:00+08:00' : date,
    sources: ['公开涨跌停池', mode.t1 === 'intraday' ? '批量实时行情' : '腾讯财经·前复权日K'],
    is_intraday: mode.t1 === 'intraday',
    is_final: mode.t1 === 'final' && mode.t2 === 'final',
    missing: mode.t2 === 'missing' ? ['T+2 历史行情覆盖 0/6'] : [],
    degraded_reasons: [],
    coverage: { baseline: { available: 6, total: 6 }, t1: { available: t1Sample, total: 6 }, t2: { available: t2Sample, total: 6 } },
  }
}

const datesPayload = {
  ok: true,
  default_date: dates[0],
  calendar: { state: 'available', source: '服务端A股交易日历' },
  dates: dates.map((date, index) => ({
    date,
    snapshot_state: index === 0 ? 'intraday' : index < 3 ? 'tracking' : 'missing',
    has_snapshot: index < 3,
    missing_label: index < 3 ? null : '快照缺失',
  })),
  generated_at: '2026-08-13T15:21:00+08:00',
  missing: [],
}

async function installRoutes(page) {
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    let body
    if (path === '/api/market/limitup-tracker/dates') body = datesPayload
    else if (path === '/api/market/limitup-tracker') {
      const selected = url.searchParams.get('date') || dates[0]
      if (selected === '2026-08-12') await new Promise((resolveDelay) => setTimeout(resolveDelay, 450))
      if (selected === '2026-08-11') await new Promise((resolveDelay) => setTimeout(resolveDelay, 50))
      body = snapshot(selected)
    } else if (path === '/api/market/limitup-tracker/backfill') {
      body = { ok: true, job_id: 'audit-job', status: 'running', message: '正在补数据' }
    } else if (path === '/api/market/limitup-tracker/backfill/audit-job') {
      body = { ok: true, job_id: 'audit-job', status: 'complete', message: '补数据完成', summary: { completed: 5, partial: 0, failed: 0, noop: 2 } }
    } else if (path === '/api/shell-status') {
      body = { ok: true, client: { brand: 'W.U.D.I.' }, market_context: { now: '2026-08-13T15:21:00+08:00', is_trade_day: true, market_session: 'review' }, missing: [] }
    } else body = { ok: true, missing: [] }
    await route.fulfill({ status: request.method() === 'POST' ? 202 : 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
}

async function axeViolations(page) {
  await page.addScriptTag({ content: axeSource })
  const result = await page.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  return result.violations.map((item) => ({ id: item.id, impact: item.impact, targets: item.nodes.map((node) => node.target) }))
}

await mkdir(outputDir, { recursive: true })
const server = await createServer({ root, server: { host: '127.0.0.1', port: 4178, strictPort: true }, logLevel: 'error' })
await server.listen()
const browser = await chromium.launch({ executablePath: chromePath, headless: true })
const report = { desktop: {}, mobile: {}, light: {}, checks: {}, pageErrors: [] }

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  page.on('pageerror', (error) => report.pageErrors.push(error.message))
  await page.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await installRoutes(page)
  await page.goto('http://127.0.0.1:4178/#/today?tab=limitup', { waitUntil: 'networkidle' })
  await page.waitForSelector('#limitup-tracker-title')
  await page.waitForSelector('.tracker-summary')

  report.checks.deepLink = page.url().endsWith('#/today?tab=limitup')
  report.checks.activeNavigation = (await page.locator('.nav-child.active').textContent())?.trim() === '涨停追踪'
  report.checks.defaultDate = (await page.locator('.tracker-summary h2').textContent())?.trim() === '2026-08-13'
  await page.reload({ waitUntil: 'networkidle' })
  report.checks.reloadKeepsRoute = page.url().endsWith('#/today?tab=limitup') && (await page.locator('.tracker-summary h2').textContent())?.trim() === '2026-08-13'

  const beforeTop = (await page.locator('.tracker-summary').boundingBox())?.y
  const recentButton = page.getByRole('button', { name: '最近20日' })
  await recentButton.click()
  const afterTop = (await page.locator('.tracker-summary').boundingBox())?.y
  report.checks.popoverNoPush = beforeTop === afterTop
  report.checks.ariaExpanded = await recentButton.getAttribute('aria-expanded') === 'true'
  report.checks.twentyDates = await page.locator('.tracker-history__grid button').count() === 20
  report.checks.currentSelected = await page.locator('.tracker-history__grid button[aria-pressed="true"]').count() === 1
  await page.keyboard.press('Escape')
  report.checks.escapeCloses = await recentButton.getAttribute('aria-expanded') === 'false'
  await recentButton.click()
  await page.locator('#limitup-tracker-title').click()
  report.checks.outsideClickCloses = await recentButton.getAttribute('aria-expanded') === 'false'
  await recentButton.click()
  await page.locator('.tracker-history__grid button').nth(3).click()
  await page.waitForFunction(() => document.querySelector('.tracker-summary h2')?.textContent?.trim() === '2026-08-10')
  report.checks.twentyDaySelection = (await page.locator('.tracker-summary h2').textContent())?.trim() === '2026-08-10' && (await page.locator('.tracker-metric strong').first().textContent())?.trim() === '暂不可用'

  await page.locator('.tracker-date-button').nth(0).click()
  await page.waitForFunction(() => document.querySelector('.tracker-summary h2')?.textContent?.trim() === '2026-08-13')
  report.checks.pendingState = (await page.locator('.tracker-metric strong').first().textContent())?.trim() === '待收盘'
  await page.locator('.tracker-date-button').nth(1).click()
  await page.locator('.tracker-date-button').nth(2).click()
  await page.waitForFunction(() => document.querySelector('.tracker-summary h2')?.textContent?.trim() === '2026-08-11')
  await page.waitForTimeout(550)
  report.checks.raceSafe = (await page.locator('.tracker-summary h2').textContent())?.trim() === '2026-08-11' && (await page.locator('tbody tr').first().textContent()).includes('定稿批次')
  report.checks.finalAndMissingStates = (await page.locator('.tracker-metrics').textContent()).includes('收盘定稿') && (await page.locator('.tracker-metrics').textContent()).includes('数据暂缺')

  await page.locator('.tracker-date-button').nth(1).click()
  await page.waitForFunction(() => document.querySelector('.tracker-summary h2')?.textContent?.trim() === '2026-08-12')
  report.checks.intradayState = (await page.locator('.tracker-metrics').textContent()).includes('盘中快照／未定稿')
  const headers = await page.locator('.tracker-table th').evaluateAll((nodes) => nodes.map((node) => node.childNodes[0]?.textContent?.trim()))
  report.checks.exactColumns = JSON.stringify(headers) === JSON.stringify(['代码', '名称', '行业', '连板', '涨停日涨幅', '首封', '次日 T+1', '第三日 T+2'])
  report.checks.serverOrder = (await page.locator('tbody tr').first().textContent()).includes('000001') && (await page.locator('tbody tr').nth(1).textContent()).includes('000002')

  await page.getByRole('button', { name: '补数据', exact: true }).click()
  await page.waitForFunction(() => document.body.innerText.includes('补数据完成'), null, { timeout: 5_000 })
  report.checks.backfillLifecycle = (await page.locator('.tracker-backfill-state').textContent()).includes('补数据完成')

  const desktopLayout = await page.evaluate(() => {
    const root = document.documentElement
    const wrapper = document.querySelector('.tracker-table-wrap')
    const targets = [...document.querySelectorAll('.limitup-tracker-page button, .limitup-tracker-page a')].map((node) => {
      const rect = node.getBoundingClientRect()
      return { label: node.getAttribute('aria-label') || node.textContent?.trim(), width: rect.width, height: rect.height }
    }).filter((item) => item.width > 0 && item.height > 0)
    return {
      documentOverflow: Math.max(0, root.scrollWidth - innerWidth),
      tableOverflow: wrapper ? Math.max(0, wrapper.scrollWidth - wrapper.clientWidth) : null,
      undersized: targets.filter((item) => item.width < 44 || item.height < 44),
    }
  })
  report.desktop = { layout: desktopLayout, violations: await axeViolations(page) }
  await page.screenshot({ path: resolve(outputDir, 'limitup-tracker-desktop-dark.png'), fullPage: true })
  await page.close()

  const mobile = await browser.newPage({ viewport: { width: 375, height: 812 }, deviceScaleFactor: 1 })
  mobile.on('pageerror', (error) => report.pageErrors.push(error.message))
  await mobile.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await installRoutes(mobile)
  await mobile.goto('http://127.0.0.1:4178/#/today?tab=limitup', { waitUntil: 'networkidle' })
  await mobile.waitForSelector('.tracker-table')
  report.mobile.layout = await mobile.evaluate(() => {
    const root = document.documentElement
    const wrapper = document.querySelector('.tracker-table-wrap')
    return {
      documentOverflow: Math.max(0, root.scrollWidth - innerWidth),
      tableOwnScroll: wrapper ? wrapper.scrollWidth > wrapper.clientWidth : false,
      toolbarVisible: Boolean(document.querySelector('.tracker-nav')),
    }
  })
  report.mobile.violations = await axeViolations(mobile)
  await mobile.screenshot({ path: resolve(outputDir, 'limitup-tracker-mobile-dark.png'), fullPage: true })
  await mobile.close()

  const light = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  light.on('pageerror', (error) => report.pageErrors.push(error.message))
  await light.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'light'))
  await installRoutes(light)
  await light.goto('http://127.0.0.1:4178/#/today?tab=limitup', { waitUntil: 'networkidle' })
  await light.waitForSelector('.tracker-table')
  report.light.theme = await light.evaluate(() => document.documentElement.dataset.theme)
  report.light.violations = await axeViolations(light)
  await light.screenshot({ path: resolve(outputDir, 'limitup-tracker-desktop-light.png'), fullPage: true })
  await light.close()

  const failures = [
    ...Object.entries(report.checks).filter(([, value]) => value !== true).map(([key]) => `check:${key}`),
    ...(report.desktop.layout.documentOverflow > 1 ? ['desktop:document-overflow'] : []),
    ...(report.desktop.layout.tableOverflow > 1 ? ['desktop:table-overflow'] : []),
    ...(report.desktop.layout.undersized.length ? ['desktop:undersized-targets'] : []),
    ...(!report.mobile.layout.tableOwnScroll ? ['mobile:table-not-scoped-scroll'] : []),
    ...(report.mobile.layout.documentOverflow > 1 ? ['mobile:document-overflow'] : []),
    ...(report.desktop.violations.length ? ['desktop:axe'] : []),
    ...(report.mobile.violations.length ? ['mobile:axe'] : []),
    ...(report.light.violations.length ? ['light:axe'] : []),
    ...(report.light.theme !== 'light' ? ['light:theme'] : []),
    ...(report.pageErrors.length ? ['page-errors'] : []),
  ]
  report.failures = failures
  await writeFile(resolve(outputDir, 'limitup-tracker-report.json'), JSON.stringify(report, null, 2), 'utf8')
  if (failures.length) throw new Error(`涨停追踪浏览器验收失败: ${failures.join(', ')}`)
  console.log(JSON.stringify({ ok: true, checks: Object.keys(report.checks).length, screenshots: 3, report: resolve(outputDir, 'limitup-tracker-report.json') }, null, 2))
} finally {
  await browser.close()
  await server.close()
}
