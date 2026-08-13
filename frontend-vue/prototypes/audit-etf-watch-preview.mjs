import { mkdir, readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { chromium } from 'playwright-core'

const root = resolve(import.meta.dirname, '..')
const htmlPath = resolve(import.meta.dirname, 'etf-watch-preview.html')
const outputDir = resolve(process.argv[2] || import.meta.dirname)
const url = pathToFileURL(htmlPath).href
await mkdir(outputDir, { recursive: true })
const browser = await chromium.launch({
  executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  headless: true,
})

const results = []
for (const viewport of [
  { name: '1440', width: 1440, height: 1000 },
  { name: '900', width: 900, height: 1000 },
  { name: '375', width: 375, height: 812 },
]) {
  const page = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } })
  await page.goto(url)
  await page.screenshot({
    path: resolve(outputDir, `etf-watch-preview-${viewport.name}.png`),
    fullPage: false,
  })
  results.push(await page.evaluate((name) => {
    const firstRow = document.querySelector('.etf-row')
    return {
      viewport: name,
      bodyScrollWidth: document.body.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      firstRowHeight: Math.round(firstRow?.getBoundingClientRect().height || 0),
      renderedRows: document.querySelectorAll('.etf-item').length,
      visibleButtonsBelow44: [...document.querySelectorAll('button')].filter((button) => {
        const rect = button.getBoundingClientRect()
        return rect.width > 0 && rect.height > 0 && (rect.width < 44 || rect.height < 44)
      }).length,
    }
  }, viewport.name))
  await page.close()
}

const axeSource = await readFile(resolve(root, 'node_modules/axe-core/axe.min.js'), 'utf8')
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
await page.goto(url)
await page.addScriptTag({ content: axeSource })
const darkAxe = await page.evaluate(async () => window.axe.run(document, {
  runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
}))
await page.locator('#themeToggle').click()
const lightAxe = await page.evaluate(async () => window.axe.run(document, {
  runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
}))
await page.locator('[data-filter="cross"]').click()
const filteredRows = await page.locator('.etf-item').count()
await page.locator('[data-filter="all"]').click()
await page.locator('#searchInput').fill('黄金')
const searchedRows = await page.locator('.etf-item').count()
await page.locator('#searchInput').fill('')
await page.locator('#addButton').click()
const dialogVisible = await page.locator('#addDialog').isVisible()
await page.locator('#dialogCancel').click()
await page.close()
await browser.close()

const compactViolations = (axe) => axe.violations.map(({ id, impact, nodes }) => ({
  id,
  impact,
  nodes: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })),
}))

console.log(JSON.stringify({
  results,
  interaction: { filteredRows, searchedRows, dialogVisible },
  darkAxeViolations: compactViolations(darkAxe),
  lightAxeViolations: compactViolations(lightAxe),
}, null, 2))
