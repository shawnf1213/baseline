// Store screenshots from the real app, rendered by the web export in a
// phone-sized headless Chrome at Apple's required pixel sizes.
//
//   node scripts/screenshots.mjs <session-file> [base-url] [out-dir]
//
// The session file holds a signed Baseline session for a test account (the
// App Store reviewer account — premium only); it is read from disk and never
// logged. The web export must be served at an origin the backend's CORS list
// allows (http://localhost:5173). Puppeteer is resolved from PUPPETEER_DIR
// (default: this directory) so it can live outside the repo.
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'

const [, , sessionFile, base = 'http://localhost:5173', out = 'screenshots'] = process.argv
if (!sessionFile) { console.error('usage: node scripts/screenshots.mjs <session-file> [base-url] [out-dir]'); process.exit(2) }
const session = fs.readFileSync(sessionFile, 'utf8').trim()
const require = createRequire(path.join(process.env.PUPPETEER_DIR || process.cwd(), 'package.json'))
const puppeteer = require('puppeteer')

// Logical points × scale = Apple's required pixel sizes.
const DEVICES = [
  { name: '6.9in-1320x2868', width: 440, height: 956, scale: 3 },
  { name: '6.5in-1242x2688', width: 414, height: 896, scale: 3 },
]

const sleep = (ms) => new Promise(r => setTimeout(r, ms))
const hasText = (page, re, timeout = 90_000) =>
  page.waitForFunction((src) => new RegExp(src, 'i').test(document.body.innerText), { timeout }, re)
async function clickText(page, re) {
  const ok = await page.evaluate((src) => {
    const rx = new RegExp(src, 'i')
    const all = [...document.querySelectorAll('div,span')].filter(e => rx.test(e.textContent || '') && e.children.length < 6)
    const el = all.sort((a, b) => (a.textContent || '').length - (b.textContent || '').length)[0]
    if (!el) return false
    let t = el
    for (let i = 0; i < 8 && t; i++) { if (t.getAttribute && (t.getAttribute('role') === 'button' || t.getAttribute('tabindex') === '0')) break; t = t.parentElement }
    ;(t || el).dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
    return true
  }, re)
  if (!ok) throw new Error(`nothing to click for ${re}`)
}

const SHOTS = [
  { name: '01-picks', path: '/', ready: 'Pick of the Day|No .* picks yet' },
  { name: '02-board', path: '/board', ready: 'priced|lines|No .* lines' },
  { name: '03-pick-sheet', path: '/', ready: 'Pick of the Day', then: async (page) => {
      await clickText(page, 'Pick of the Day'); await hasText(page, 'Confidence|Recent form|Result'); await sleep(2500) } },
  { name: '04-project', path: '/project', ready: 'Price any matchup' },
  { name: '05-research', path: '/research', ready: 'Research', then: async (page) => {
      const input = await page.waitForSelector('input[placeholder*="Search"]', { timeout: 30_000 })
      await input.click(); await page.keyboard.type('Sinner', { delay: 40 })
      await hasText(page, 'Jannik Sinner', 60_000); await clickText(page, 'Jannik Sinner')
      await hasText(page, 'Recent form|Prop history', 120_000); await sleep(4000) } },
]

const browser = await puppeteer.launch({ headless: true, args: ['--no-sandbox', '--disable-gpu'] })
try {
  for (const dev of DEVICES) {
    const dir = path.join(out, dev.name)
    fs.mkdirSync(dir, { recursive: true })
    const page = await browser.newPage()
    await page.setViewport({ width: dev.width, height: dev.height, deviceScaleFactor: dev.scale, isMobile: true, hasTouch: true })
    await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: 'dark' }])
    await page.goto(base + '/', { waitUntil: 'networkidle2', timeout: 120_000 })
    await page.evaluate((tok) => { localStorage.setItem('baseline.session', tok) }, session)
    for (const shot of SHOTS) {
      try {
        await page.goto(base + shot.path, { waitUntil: 'networkidle2', timeout: 120_000 })
        await hasText(page, shot.ready)
        await sleep(1500)
        if (shot.then) await shot.then(page)
        const file = path.join(dir, `${shot.name}.png`)
        await page.screenshot({ path: file, type: 'png' })
        console.log('wrote', file)
      } catch (e) {
        console.error(`FAILED ${dev.name} ${shot.name}: ${e.message}`)
        try { await page.screenshot({ path: path.join(dir, `${shot.name}-FAILED.png`) }) } catch {}
      }
    }
    await page.close()
  }
} finally {
  await browser.close()
}
