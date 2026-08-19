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
  { name: 'phone-landscape', width: 812, height: 375 },
  { name: 'wide-1440', width: 1440, height: 1000 },
]

const server = await createServer({
  configFile: false,
  root,
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 4176, strictPort: true },
  logLevel: 'error',
})
await server.listen()
const browser = await chromium.launch({ executablePath: chromePath, headless: true })
const report = []

try {
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport, deviceScaleFactor: 1 })
    const pageErrors = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.addInitScript(() => localStorage.setItem('wudi-vue-theme', 'dark'))
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      if (path === '/api/chat/conversations') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, conversations: [] }) })
      }
      if (path === '/api/chat/data-materials') {
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, materials: [], required: [], max_selected: 8 }) })
      }
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'focused audit fixture missing' }) })
    })
    await page.goto('http://127.0.0.1:4176/#/research?tab=ask', { waitUntil: 'networkidle' })
    await page.waitForSelector('#stock-ask-page-title')
    await page.addScriptTag({ content: axeSource })
    const state = await page.evaluate(() => {
      const profileTargets = [
        ...document.querySelectorAll('.stock-ask-profile__toggle, .stock-ask-profile__grid input, .stock-ask-profile__grid select, .stock-ask-profile__foot button'),
      ].filter((element) => element.getClientRects().length > 0)
      return {
        deterministicBoundary: document.body.innerText.includes('个股主输出不调用模型'),
        profileFields: document.querySelectorAll('.stock-ask-profile input, .stock-ask-profile select').length >= 8,
        noHorizontalOverflow: document.documentElement.scrollWidth <= window.innerWidth,
        touchTargets: window.innerWidth > 720 || profileTargets.every((element) => element.getBoundingClientRect().height >= 44),
      }
    })
    const axe = await page.evaluate(async () => window.axe.run(document, {
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    }))
    report.push({
      viewport,
      state,
      pageErrors,
      violations: axe.violations.map((item) => ({ id: item.id, impact: item.impact, help: item.help })),
    })
    await page.close()
  }
} finally {
  await browser.close()
  await server.close()
}

const failed = report.some((item) =>
  Object.values(item.state).some((value) => value !== true)
  || item.pageErrors.length > 0
  || item.violations.length > 0
)
console.log(JSON.stringify({ failed, report }, null, 2))
if (failed) process.exitCode = 1
