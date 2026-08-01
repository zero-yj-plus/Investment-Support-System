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
const outputDir = resolve(process.argv[2] || resolve(tmpdir(), 'wudi-watchlist-import-audit'))
const axeSource = await readFile(require.resolve('axe-core/axe.min.js'), 'utf8')
const chromePath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const server = await createServer({
  root,
  configFile: false,
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 4176 },
})
await server.listen()

let browser
try {
  await mkdir(outputDir, { recursive: true })
  browser = await chromium.launch({ executablePath: chromePath, headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname
    const body = path === '/api/watchlist'
      ? { ok: true, ts: '2026-08-01 13:30:00', items: [], missing: [] }
      : { ok: true }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })

  await page.goto('http://127.0.0.1:4176/#/risk?tab=watchlist', { waitUntil: 'networkidle' })
  await page.getByRole('button', { name: '批量导入' }).click()
  await page.locator('dialog.watchlist-import input[type="file"]').setInputFiles({
    name: 'Table_5680.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from([
      '初始    代码      名称        所属行业',
      '1       600519    贵州茅台    白酒',
      '2       09868     小鹏集团-W  汽车',
      '3       300750    宁德时代    电池',
    ].join('\n'), 'utf8'),
  })
  await page.waitForFunction(() => document.querySelectorAll('.watchlist-import__rows li').length === 3)
  await page.addScriptTag({ content: axeSource })
  const axe = await page.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
  }))
  const desktop = await page.evaluate(() => {
    const dialog = document.querySelector('dialog.watchlist-import')
    const text = dialog?.textContent || ''
    return {
      nativeModal: dialog instanceof HTMLDialogElement && dialog.open,
      sourceDetected: text.includes('东方财富'),
      validRows: text.includes('600519') && text.includes('300750'),
      skippedRows: text.includes('09868') && text.includes('不是可识别的 6 位 A 股代码'),
      appendOnlyCopy: text.includes('不会清空或覆盖'),
      filePicker: Boolean(dialog?.querySelector('input[type="file"][accept*=".txt"]')),
      noHorizontalOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    }
  })
  await page.screenshot({ path: resolve(outputDir, 'watchlist-import-preview-1440.png'), fullPage: true })

  await page.setViewportSize({ width: 375, height: 812 })
  const phone = await page.evaluate(() => {
    const dialog = document.querySelector('dialog.watchlist-import')
    const rect = dialog?.getBoundingClientRect()
    return {
      dialogFits: Boolean(rect && rect.left >= 0 && rect.right <= window.innerWidth),
      noHorizontalOverflow: document.documentElement.scrollWidth <= window.innerWidth,
      confirmReachable: Boolean(dialog?.querySelector('button[type="submit"]')),
    }
  })
  await page.screenshot({ path: resolve(outputDir, 'watchlist-import-preview-375.png'), fullPage: true })

  const report = {
    ok: Object.values(desktop).every(Boolean)
      && Object.values(phone).every(Boolean)
      && pageErrors.length === 0
      && axe.violations.length === 0,
    desktop,
    phone,
    pageErrors,
    violations: axe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help })),
  }
  await writeFile(resolve(outputDir, 'watchlist-import-audit.json'), JSON.stringify(report, null, 2), 'utf8')
  console.log(JSON.stringify(report, null, 2))
  console.log(`Audit output: ${outputDir}`)
  if (!report.ok) process.exitCode = 1
} finally {
  if (browser) await browser.close()
  await server.close()
}
