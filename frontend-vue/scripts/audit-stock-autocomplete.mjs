import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'
import { createServer } from 'vite'
import vue from '@vitejs/plugin-vue'

const require = createRequire(import.meta.url)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const axeSource = await readFile(require.resolve('axe-core/axe.min.js'), 'utf8')
const chromePath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const viewports = [
  { name: 'phone-375', width: 375, height: 812 },
  { name: 'wide-1440', width: 1440, height: 960 },
]

const server = await createServer({
  configFile: false,
  root,
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 4177, strictPort: true },
  logLevel: 'error',
})
await server.listen()
const browser = await chromium.launch({ executablePath: chromePath, headless: true })
const report = []

try {
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport, deviceScaleFactor: 1 })
    const pageErrors = []
    const searchQueries = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' })
    await page.route('**/api/**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname === '/api/stocks/search') {
        searchQueries.push(url.searchParams.get('q'))
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ok: true,
            state: 'degraded',
            query: url.searchParams.get('q') || '',
            results: [
              { code: '600519', name: '贵州茅台' },
              { code: '605208', name: '永茂泰' },
            ],
            pinyin_available: true,
            universe: { count: 4904, complete: false, stale: true, refresh_required: true, as_of: '2026-08-01', source: '本地 last-good', authority: null },
            missing: ['股票名录为陈旧快照，候选仅供选择'],
          }),
        })
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) })
    })

    await page.goto('http://127.0.0.1:4177/#/research?tab=diagnosis', { waitUntil: 'networkidle' })
    const input = page.locator('#diagnosis-code')
    await input.fill('maotai')
    await page.getByRole('option', { name: /贵州茅台.*600519/ }).waitFor()

    await input.press('Enter')
    const unselectedValue = await input.inputValue()
    await input.press('ArrowDown')
    await input.press('Enter')
    const selectedValue = await input.inputValue()
    const selectedNameVisible = await page.getByText('贵州茅台', { exact: true }).last().isVisible()
    const staleBoundaryVisible = await page.getByText(/股票名录截至 2026-08-01，待刷新/).isVisible()
    const optionHeights = await page.locator('[role="option"]').evaluateAll((items) => items.map((item) => item.getBoundingClientRect().height))

    await page.addScriptTag({ content: axeSource })
    const axe = await page.evaluate(async () => window.axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    }))
    report.push({
      viewport,
      state: {
        debouncedCacheSearch: searchQueries.includes('maotai'),
        enterDoesNotAutoPickFirst: unselectedValue === 'maotai',
        keyboardSelectionWritesCode: selectedValue === '600519',
        selectedNameVisible,
        staleBoundaryVisible,
        touchTargets: optionHeights.every((height) => height >= 44),
        noHorizontalOverflow: await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      },
      pageErrors,
      violations: axe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help })),
    })
    await page.close()
  }
} finally {
  await browser.close()
  await server.close()
}

const failed = report.some((item) => (
  Object.values(item.state).some((value) => value !== true)
  || item.pageErrors.length > 0
  || item.violations.length > 0
))
console.log(JSON.stringify({ failed, report }, null, 2))
if (failed) process.exitCode = 1
