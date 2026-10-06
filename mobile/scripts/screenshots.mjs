// Store screenshots from the real app, rendered by the web export in a
// phone-sized headless Chrome at Apple's required pixel sizes.
//
//   npx expo export --platform web --output-dir dist-web
//   SERVE_DIR=dist-web node scripts/screenshots.mjs <session-file> [base-url] [out-dir]
//
// The session file holds a signed Baseline session for a test account (the
// App Store reviewer account — premium only); it is read from disk and never
// logged. The web export must be served at an origin the backend's CORS list
// allows (http://localhost:5173). With SERVE_DIR set, this script serves that
// export itself for the length of the run and stops — no separate server.
// Puppeteer is resolved from PUPPETEER_DIR (default: this directory) so it can
// live outside the repo.
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { createRequire } from 'node:module'

const [, , sessionFile, base = 'http://localhost:5173', out = 'screenshots'] = process.argv
if (!sessionFile) { console.error('usage: node scripts/screenshots.mjs <session-file> [base-url] [out-dir]'); process.exit(2) }
const session = fs.readFileSync(sessionFile, 'utf8').trim()
const require = createRequire(path.join(process.env.PUPPETEER_DIR || process.cwd(), 'package.json'))
const puppeteer = require('puppeteer')

// The export, served from this process: files as they are, any other path
// falls back to index.html (the export is a single-page app).
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css',
                '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml',
                '.ttf': 'font/ttf', '.otf': 'font/otf', '.woff': 'font/woff', '.woff2': 'font/woff2',
                '.ico': 'image/x-icon', '.map': 'application/json' }
async function serve(dir) {
  const root = path.resolve(dir)
  const server = http.createServer((req, res) => {
    let file = path.join(root, decodeURIComponent(new URL(req.url, base).pathname))
    if (!file.startsWith(root)) { res.writeHead(403); res.end(); return }
    if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html')
    if (!fs.existsSync(file)) file = fs.existsSync(`${file}.html`) ? `${file}.html` : path.join(root, 'index.html')
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream' })
    fs.createReadStream(file).pipe(res)
  })
  await new Promise((ok, fail) => { server.once('error', fail); server.listen(Number(new URL(base).port || 80), 'localhost', ok) })
  console.log(`serving ${root} at ${base}`)
  return server
}

// Logical points × scale = Apple's required pixel sizes. RAW=1 instead renders
// one 440×902 capture per screen — a 6.9" screen less its 54pt status bar —
// for scripts/store_frames.mjs to place inside a phone frame.
const DEVICES = process.env.RAW ? [{ name: 'raw', width: 440, height: 902, scale: 3 }] : [
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

// The Board's player cards (build 6): a card with several props carries an
// "N props" pill inside its header button. Clicking the header drops it down.
const PILLS = () => [...document.querySelectorAll('div,span')]
  .filter(e => e.children.length === 0 && /^\d+ props$/i.test((e.textContent || '').trim()))
async function dropDown(page, index, visibleOnly) {
  return page.evaluate((src, index, visibleOnly) => {
    const pills = new Function(`return (${src})()`)()
    const list = visibleOnly ? pills.filter(p => { const r = p.getBoundingClientRect(); return r.top > 120 && r.bottom < innerHeight - 120 }) : pills
    const p = list[index]
    if (!p) return false
    let t = p
    for (let i = 0; i < 10 && t && t.getAttribute('role') !== 'button'; i++) t = t.parentElement
    if (!t) return false
    t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
    return true
  }, PILLS.toString(), index, visibleOnly)
}
// Open the sheet of a PRICED prop from a dropped-down card: its line reads
// "Baseline 24.6 · edge +1.6"; a row not priced yet reads "Pricing…".
async function openPricedProp(page) {
  const n = await page.evaluate((src) => new Function(`return (${src})()`)().length, PILLS.toString())
  for (let i = 0; i < Math.min(n, 10); i++) {
    await dropDown(page, i, false)
    await sleep(900)
    if (await page.evaluate(() => {
      const el = [...document.querySelectorAll('div,span')]
        .filter(e => /^Baseline \d/.test((e.textContent || '').trim()) && e.children.length < 6)
        .sort((a, b) => a.textContent.length - b.textContent.length)[0]
      if (!el) return false
      let t = el
      for (let k = 0; k < 10 && t && t.getAttribute('role') !== 'button'; k++) t = t.parentElement
      ;(t || el).dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
      return true
    })) return
  }
  throw new Error('no priced prop to open on the board')
}
// The board's strongest play: the first Top plays card (they are the highest-
// confidence priced plays, one per player). Found from its own "vs <opponent>"
// line, climbing to the focusable card; a player card's header is a role=button
// and is skipped. (Matching on the card's whole text does not work: text nodes
// run together, "Total Gamesvs Alana Smith", so there is no word boundary.)
async function openTopPlay(page) {
  return page.evaluate(() => {
    const leaves = [...document.querySelectorAll('div,span')]
      .filter(e => e.children.length === 0 && /^vs\s/.test((e.textContent || '').trim()))
    for (const l of leaves) {
      let t = l
      for (let i = 0; i < 8 && t && t.getAttribute('tabindex') !== '0'; i++) t = t.parentElement
      if (!t || t.getAttribute('tabindex') !== '0' || t.getAttribute('role') === 'button') continue
      t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
      return true
    }
    return false
  })
}
// Photos arrive through a redirect to Wikimedia and can land seconds after the
// text: wait for the network to go quiet and every image to finish (loaded or
// failed), so a face is never caught mid-load as initials.
async function settle(page) {
  try { await page.waitForNetworkIdle({ idleTime: 1500, timeout: 25_000 }) } catch {}
  try { await page.waitForFunction(() => [...document.images].every(i => i.complete), { timeout: 15_000 }) } catch {}
  // A sheet focuses its Done button on the web and the browser rings it; the
  // iOS app draws no such ring, so it never belongs in a store frame.
  await page.addStyleTag({ content: '*:focus,*:focus-visible{outline:none!important}' })
  await page.evaluate(() => { const a = document.activeElement; if (a && a !== document.body && a.blur) a.blur() })
  await sleep(800)
}

const SHOTS = [
  // The Board once every row is priced (it re-polls each minute while any is
  // pending), with the first on-screen multi-prop card dropped down.
  { name: '01-board', path: '/', ready: 'Top plays|Full board|No .* lines', then: async (page) => {
      try {
        await page.waitForFunction(() => !/pricing the rest of the board/i.test(document.body.innerText),
                                   { timeout: 8 * 60_000, polling: 5000 })
      } catch { console.log('01-board: board still pricing after 8 min, capturing as is') }
      if (await dropDown(page, 0, true)) await sleep(1500) } },
  // Picks as it stands: a starred card only when the latest board has a real
  // Pick of the Day — the star is a flag, never the #1 play.
  { name: '02-picks', path: '/picks', ready: 'Pick of the Day|No .* picks yet' },
  // The strongest play's sheet; a dropped-down card's priced line if the
  // board has no Top plays strip.
  { name: '03-pick-sheet', path: '/', ready: 'Full board', then: async (page) => {
      if (!(await openTopPlay(page))) await openPricedProp(page)
      await hasText(page, 'Recent form'); await sleep(6000) } },
  // Project, opened the way a pick sheet opens it: both players, prop and line
  // in the URL, so it runs on its own (a matchup the board pricer has priced;
  // swap in a current one when this goes stale). Scrolled so the verdict card
  // is in view under a little of the form.
  { name: '04-project', path: '/project?t=1&sport=tennis&player=Coco%20Gauff&playerId=264983&opponent=Elise%20Mertens&opponentId=78551&tour=WTA&surface=Hard&prop=Break%20Points%20Won&line=4.5',
    ready: 'Price any matchup', then: async (page) => {
      await hasText(page, 'edge [+−-]?\\d', 180_000); await sleep(3000)
      await page.evaluate(() => {
        // The subtitle is nested text ("vs " + name + " · Hard"), so match on
        // any element and keep the tightest one.
        const leaf = [...document.querySelectorAll('div,span')]
          .filter(e => /^vs .+ · (Hard|Clay|Grass)$/.test((e.textContent || '').trim()))
          .sort((a, b) => a.textContent.length - b.textContent.length)[0]
        if (!leaf) return
        // Scroll the screen's own scroller by the subtitle's offset, leaving
        // ~260pt above it (the verdict's title and the Run button).
        let sc = leaf.parentElement
        while (sc && !(/(auto|scroll)/.test(getComputedStyle(sc).overflowY) && sc.scrollHeight > sc.clientHeight)) sc = sc.parentElement
        if (!sc) return
        const delta = leaf.getBoundingClientRect().top - sc.getBoundingClientRect().top - 260
        sc.scrollTop = Math.max(0, Math.min(sc.scrollHeight - sc.clientHeight, sc.scrollTop + delta))
      })
      await sleep(1500) } },
  { name: '05-research', path: '/research', ready: 'Research', then: async (page) => {
      const input = await page.waitForSelector('input[placeholder*="Search"]', { timeout: 30_000 })
      // An active player: a page carrying the "may be inactive" warning (Sinner,
      // 85 days since his last match on 2026-10-06) is not the product at its best.
      await input.click(); await page.keyboard.type('Alcaraz', { delay: 40 })
      await hasText(page, 'Carlos Alcaraz', 60_000); await clickText(page, 'Carlos Alcaraz')
      await hasText(page, 'streak', 120_000)
      await hasText(page, 'Last 10 matches', 180_000); await sleep(5000) } },
  // The NFL board (every upcoming game day, build 6) with the first player who
  // has several props dropped down and scrolled to the middle of the screen.
  // The sport switch is in-memory, so the next shot's reload starts on tennis.
  { name: '06-nfl-board', path: '/', ready: 'Full board', then: async (page) => {
      await clickText(page, '^NFL$')
      await hasText(page, '· live lines', 90_000)
      await hasText(page, '\\d+ props', 90_000)
      if (!(await dropDown(page, 0, false))) throw new Error('no NFL player with several props')
      await sleep(1200)
      await page.evaluate((src) => {
        const p = new Function(`return (${src})()`)()[0]
        let t = p
        for (let i = 0; i < 10 && t && t.getAttribute('role') !== 'button'; i++) t = t.parentElement
        ;(t?.parentElement || p).scrollIntoView({ block: 'center' })
      }, PILLS.toString())
      await sleep(1500) } },
]

const server = process.env.SERVE_DIR ? await serve(process.env.SERVE_DIR) : null
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
    // ONLY=01-board re-renders just the named shot(s), comma-separated.
    const only = (process.env.ONLY || '').split(',').map(s => s.trim()).filter(Boolean)
    for (const shot of SHOTS.filter(s => !only.length || only.includes(s.name))) {
      try {
        await page.goto(base + shot.path, { waitUntil: 'networkidle2', timeout: 120_000 })
        await hasText(page, shot.ready)
        await sleep(1500)
        if (shot.then) await shot.then(page)
        await settle(page)
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
  server?.close()
}
