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

const reportMarkdown = `# 审计早盘内参

## 一、当前结论

服务端定稿结论：中等风险，只做主线内有承接的确认机会，不追一致加速。

## 二、风险与仓位

- 总仓不超过 50%。
- 结构化价格条件缺失时保持等待，不以 0 补位。

## 三、证据完整性

|证据项|状态|数据时点|
|---|---|---|
|风险快照|完整|2026-07-28 08:50:00|
|结构化价格条件|缺失|—|

> 本报告正文只作受约束解释，确定性风险与仓位边界来自服务端定格快照。`

const shellFixture = {
  ok: true,
  is_trade_day: true,
  market_session: 'intraday',
  market_context: { now: '2026-07-28T10:31:00+08:00', is_trade_day: true, market_session: 'intraday' },
}
const reportFixture = {
  ok: true,
  schema_version: 'daily-report-v2',
  rtype: 'morning',
  date: '2026-07-28',
  title: '审计早盘内参',
  content_md: reportMarkdown,
  content_id: 'report:morning:2026-07-28',
  content_hash: 'a82be94d0a932b467dac5289f8a1c77b047191787599574b62e1294c0f8dc321',
  report_version: 1,
  created_at: '2026-07-28 08:51:12',
  pushed: true,
  cockpit: {
    as_of: '2026-07-28 08:50:00',
    command: {
      risk_level: 3,
      level_name: '中等风险 · 条件参与',
      today_action: '只做主线内有承接的确认机会，不追一致加速',
      position_cap: '总仓不超过 50%',
      first_principle: '先确认风险边界，再验证价格条件',
      forbid: ['不追一致加速', '价格条件缺失时不新增风险'],
    },
  },
  claim_validation: { ok: true, status: 'passed', errors: [] },
  advice_governance: { ok: true, status: 'passed', proposal_only: true, errors: [] },
  input_lineage: { evidence_as_of: '2026-07-28 08:50:00' },
}
const receiptFixture = {
  ok: true,
  schema_version: 'delivery-receipt.v1',
  receipts: [{
    receipt_id: 'dr_audit_report',
    content_id: 'report:morning:2026-07-28',
    content_hash: reportFixture.content_hash,
    channel: 'gateway',
    attempt: 1,
    status: 'accepted',
    attempted_at: '2026-07-28T00:52:00Z',
    sent_at: '2026-07-28T00:52:01Z',
    ack_at: null,
    target_results: [{ target: 'feishu', status: 'accepted' }],
  }],
  count: 1,
  total: 1,
}
const commonFixtures = {
  '/api/shell-status': shellFixture,
  '/api/archive/dates': { ok: true, dates: ['2026-07-28', '2026-07-27', '2026-07-24'] },
  '/api/reports/morning': reportFixture,
  '/api/delivery/receipts': receiptFixture,
}

const scenarios = [
  { name: 'reports-375-dark', width: 375, height: 812, theme: 'dark', kind: 'complete' },
  { name: 'reports-1440-dark', width: 1440, height: 1000, theme: 'dark', kind: 'complete' },
  { name: 'reports-1440-light', width: 1440, height: 1000, theme: 'light', kind: 'complete' },
  { name: 'reports-loading-375-dark', width: 375, height: 812, theme: 'dark', kind: 'loading' },
  { name: 'reports-empty-1440-dark', width: 1440, height: 1000, theme: 'dark', kind: 'empty' },
  { name: 'reports-partial-failure-1440-dark', width: 1440, height: 1000, theme: 'dark', kind: 'partial-failure' },
]

await mkdir(outputDir, { recursive: true })
const server = await createServer({ root, server: { host: '127.0.0.1', port: 4176, strictPort: true }, logLevel: 'error' })
await server.listen()
const browser = await chromium.launch({ executablePath: chromePath, headless: true })
const report = []

try {
  for (const scenario of scenarios) {
    const page = await browser.newPage({ viewport: { width: scenario.width, height: scenario.height }, deviceScaleFactor: 1 })
    const pageErrors = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.addInitScript((theme) => localStorage.setItem('wudi-vue-theme', theme), scenario.theme)
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      if (scenario.kind === 'loading' && ['/api/archive/dates', '/api/reports/morning', '/api/delivery/receipts'].includes(path)) {
        await new Promise((resolveDelay) => setTimeout(resolveDelay, 1800))
      }
      if (scenario.kind === 'empty' && path === '/api/reports/morning') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: false, error: '该日期无此报告' }) })
      }
      if (scenario.kind === 'partial-failure' && ['/api/archive/dates', '/api/delivery/receipts'].includes(path)) {
        return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '审计场景：局部链路暂不可用' }) })
      }
      const body = commonFixtures[path] || { ok: false, missing: ['报告中心不消费此接口'] }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    })
    await page.goto('http://127.0.0.1:4176/#/history?tab=reports', {
      waitUntil: scenario.kind === 'loading' ? 'domcontentloaded' : 'networkidle',
    })
    if (scenario.kind === 'loading') await page.waitForSelector('.report-center-loading')
    else if (scenario.kind === 'empty') await page.waitForSelector('.report-center-empty')
    else await page.waitForSelector(scenario.kind === 'partial-failure' ? '.report-center-summary' : '.report-center-document')
    await page.addScriptTag({ content: axeSource })
    const layout = await page.evaluate((kind) => {
      const targets = [...document.querySelectorAll('button, a, input')]
        .map((node) => {
          const rect = node.getBoundingClientRect()
          const style = getComputedStyle(node)
          return {
            label: node.getAttribute('aria-label') || node.textContent?.trim() || node.tagName,
            width: rect.width,
            height: rect.height,
            visible: style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0,
          }
        })
        .filter((item) => item.visible)
      const text = document.body.innerText
      return {
        pageTitle: Boolean(document.querySelector('#report-center-page-title')),
        overflow: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
        undersizedTargets: targets.filter((item) => item.width < 44 || item.height < 44),
        stateComplete: kind === 'loading'
          ? document.querySelectorAll('.report-center-loading .skeleton-block').length === 2
          : kind === 'empty'
            ? text.includes('暂无「早盘内参」') && text.includes('该日期无此报告') && !text.includes('审计早盘内参')
            : kind === 'partial-failure'
              ? text.includes('审计早盘内参') && text.includes('历史日期读取失败') && text.includes('回执读取失败') && text.includes('历史推送记录')
              : text.includes('中等风险 · 条件参与') && text.includes('平台接受') && text.includes('不等于最终送达或已读'),
      }
    }, scenario.kind)
    const axe = await page.evaluate(async () => window.axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    }))
    await page.screenshot({ path: resolve(outputDir, `${scenario.name}.png`), fullPage: true })
    report.push({
      scenario,
      layout,
      pageErrors,
      violations: axe.violations.map((item) => ({
        id: item.id,
        impact: item.impact,
        help: item.help,
        nodes: item.nodes.map((node) => ({ target: node.target, html: node.html, summary: node.failureSummary })),
      })),
    })
    await page.close()
  }
} finally {
  await browser.close()
  await server.close()
}

const failed = report.some((item) =>
  !item.layout.pageTitle
  || item.layout.overflow > 0
  || item.layout.undersizedTargets.length > 0
  || !item.layout.stateComplete
  || item.pageErrors.length > 0
  || item.violations.length > 0
)
await writeFile(resolve(outputDir, 'reports-audit-report.json'), JSON.stringify({ failed, report }, null, 2), 'utf8')
console.log(JSON.stringify({ failed, report }, null, 2))
if (failed) process.exitCode = 1

