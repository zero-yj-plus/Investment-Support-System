import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { chromium } from 'playwright-core'

const root = resolve(import.meta.dirname, '..')
const htmlPath = resolve(import.meta.dirname, 'watchlist-compact-list.html')
const url = pathToFileURL(htmlPath).href
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
    path: resolve(import.meta.dirname, `watchlist-compact-${viewport.name}.png`),
    fullPage: true,
  })
  results.push(await page.evaluate((name) => {
    const shell = document.querySelector('.list-shell')
    const row = document.querySelector('.watch-row')
    return {
      viewport: name,
      bodyScrollWidth: document.body.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      listHeight: Math.round(shell?.getBoundingClientRect().height || 0),
      firstRowHeight: Math.round(row?.getBoundingClientRect().height || 0),
      visibleButtonsBelow44: [...document.querySelectorAll('button')].filter((button) => {
        const rect = button.getBoundingClientRect()
        return rect.width < 44 || rect.height < 44
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
await page.locator('#theme-toggle').click()
const lightAxe = await page.evaluate(async () => window.axe.run(document, {
  runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
}))
await page.locator('.expand-button').first().click()
const interaction = await page.evaluate(() => {
  const button = document.querySelector('.expand-button')
  const panel = document.getElementById(button?.getAttribute('aria-controls') || '')
  return {
    expanded: button?.getAttribute('aria-expanded') === 'true',
    panelVisible: panel?.hidden === false,
  }
})
await page.close()
await browser.close()

const compactViolations = (axe) => axe.violations.map(({ id, impact, nodes }) => ({
  id,
  impact,
  nodes: nodes.map(({ target, failureSummary }) => ({ target, failureSummary })),
}))
console.log(JSON.stringify({
  results,
  interaction,
  darkAxeViolations: compactViolations(darkAxe),
  lightAxeViolations: compactViolations(lightAxe),
}, null, 2))
