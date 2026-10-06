// App Store marketing frames: headline, the real app inside a phone, tagline,
// brand mark — composed from raw captures and rendered at Apple's sizes.
//
//   RAW=1 SERVE_DIR=dist-web node scripts/screenshots.mjs <session> http://localhost:5173 store/raw   (captures)
//   node scripts/store_frames.mjs [raw-dir=store/raw/raw] [out-dir=store/screenshots]
//
// Every phone shows an unretouched capture of the running app with real data;
// only the frame around it is drawn. Copy is ours and describes what the app
// does — no review counts, ratings, testimonials or results claims.
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'

const [, , rawDir = 'store/raw/raw', out = 'store/screenshots'] = process.argv
const require = createRequire(path.join(process.env.PUPPETEER_DIR || process.cwd(), 'package.json'))
const puppeteer = require('puppeteer')

const b64 = (f) => fs.readFileSync(f).toString('base64')
const FONT_DIR = 'node_modules/@expo-google-fonts'
const face = (fam, file, weight) =>
  `@font-face{font-family:'${fam}';font-weight:${weight};src:url(data:font/ttf;base64,${b64(path.join(FONT_DIR, file))}) format('truetype')}`
const FONTS = [
  face('BC', 'barlow-condensed/900Black/BarlowCondensed_900Black.ttf', 900),
  face('BC', 'barlow-condensed/800ExtraBold/BarlowCondensed_800ExtraBold.ttf', 800),
  face('BC', 'barlow-condensed/700Bold/BarlowCondensed_700Bold.ttf', 700),
  face('B', 'barlow/600SemiBold/Barlow_600SemiBold.ttf', 600),
  face('B', 'barlow/500Medium/Barlow_500Medium.ttf', 500),
].join('\n')
const ICON = `data:image/png;base64,${b64('assets/images/icon.png')}`
const shot = (name) => `data:image/png;base64,${b64(path.join(rawDir, name))}`

const SIZES = [
  { name: '6.9in-1320x2868', w: 1320, h: 2868 },
  { name: '6.5in-1242x2688', w: 1242, h: 2688 },
]

// Headline: green line, white line. Tagline: green line, white line.
const FRAMES = [
  { name: '01-hero', hero: true, shot: '01-board.png', top: ['Player props', 'projected.'],
    sub: 'Tennis, NFL and NBA lines, priced by the Baseline model.' },
  { name: '02-board', shot: '01-board.png', top: ['Every line', 'priced live'], foot: ['The live board', 'Top plays first'] },
  { name: '03-pick-sheet', shot: '03-pick-sheet.png', top: ['Our number', 'vs. the line'], foot: ['Edge and confidence', 'on every prop'] },
  { name: '04-nfl-board', shot: '06-nfl-board.png', top: ['Every prop', 'one tap deep'], foot: ['NFL lines', 'all week long'] },
  { name: '05-research', shot: '05-research.png', top: ['Know', 'the form'], foot: ['Last 5, last 10', 'prop by prop'] },
  { name: '06-project', shot: '04-project.png', top: ['Price any', 'matchup'], foot: ['Pick the players', 'get the number'] },
]

// iOS-style status bar glyphs, white.
const SIGNAL = `<svg viewBox="0 0 34 22"><rect x="0" y="14" width="6" height="8" rx="1.6"/><rect x="9" y="10" width="6" height="12" rx="1.6"/><rect x="18" y="5" width="6" height="17" rx="1.6"/><rect x="27" y="0" width="6" height="22" rx="1.6"/></svg>`
const WIFI = `<svg viewBox="0 0 32 23"><path d="M16 23l-5.2-6.2a7.6 7.6 0 0110.4 0z"/><path d="M5.6 11.6a14.8 14.8 0 0120.8 0l-2.9 3.5a10.3 10.3 0 00-15 0z" /><path d="M0.4 5.4a22.6 22.6 0 0131.2 0l-2.9 3.4a18 18 0 00-25.4 0z"/></svg>`
const BATTERY = `<svg viewBox="0 0 54 24"><rect x="1" y="1" width="46" height="22" rx="6.5" fill="none" stroke="#fff" stroke-opacity=".45" stroke-width="2"/><rect x="4.5" y="4.5" width="34" height="15" rx="3.8"/><path d="M50 8.5v7c1.9-.6 3-2 3-3.5s-1.1-2.9-3-3.5z" fill-opacity=".45"/></svg>`

function phone(src) {
  return `
  <div class="device">
    <i class="btn act"></i><i class="btn vup"></i><i class="btn vdn"></i><i class="btn pwr"></i>
    <div class="bezel"><div class="screen">
      <div class="status"><span class="time">9:41</span><span class="icons">${SIGNAL}${WIFI}${BATTERY}</span></div>
      <img class="app" src="${src}">
      <div class="island"></div>
    </div></div>
  </div>`
}

function page(f, W, H) {
  const u = W / 1320
  const body = f.hero ? `
    <div class="frame hero">
      <div class="glow g1"></div><div class="glow g2"></div>
      <div class="top">
        <div class="brand"><img src="${ICON}"><span>Baseline</span></div>
        <div class="h hx g">${f.top[0]}</div><div class="h hx w">${f.top[1]}</div>
        <div class="sub">${f.sub}</div>
        <div class="chips"><span>Tennis</span><span>NFL</span><span>NBA</span></div>
      </div>
      <div class="tilt">${phone(shot(f.shot))}</div>
    </div>` : `
    <div class="frame">
      <div class="glow g1"></div><div class="glow g2"></div>
      <div class="top"><div class="h g">${f.top[0]}</div><div class="h w">${f.top[1]}</div></div>
      <div class="stage">${phone(shot(f.shot))}</div>
      <div class="foot">
        <div><div class="t g">${f.foot[0]}</div><div class="t w">${f.foot[1]}</div></div>
        <img class="mark" src="${ICON}">
      </div>
    </div>`
  return `<!doctype html><html><head><meta charset="utf-8"><style>
${FONTS}
:root { --u: ${u}px; --green: #00E676; }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: ${W}px; height: ${H}px; background: #050505; overflow: hidden; }
.frame { position: relative; width: 100%; height: 100%; overflow: hidden; display: flex; flex-direction: column;
  padding: calc(150 * var(--u)) calc(96 * var(--u)) calc(118 * var(--u));
  background: linear-gradient(180deg, #070807 0%, #040404 55%, #060807 100%); }
.glow { position: absolute; pointer-events: none; border-radius: 50%; filter: blur(calc(40 * var(--u))); }
.g1 { width: calc(1300 * var(--u)); height: calc(1300 * var(--u)); left: calc(10 * var(--u)); top: 34%;
  background: radial-gradient(closest-side, rgba(0,230,118,.20), rgba(0,230,118,.06) 55%, transparent); }
.g2 { width: calc(900 * var(--u)); height: calc(700 * var(--u)); left: calc(-380 * var(--u)); top: calc(-260 * var(--u));
  background: radial-gradient(closest-side, rgba(0,230,118,.16), transparent); }
.top, .foot, .stage { position: relative; }
.h { font-family: BC; font-weight: 900; text-transform: uppercase; font-size: calc(172 * var(--u));
  line-height: .88; letter-spacing: calc(.5 * var(--u)); white-space: nowrap; }
.g { color: var(--green); } .w { color: #fff; }
.stage { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center;
  padding: calc(72 * var(--u)) 0 calc(64 * var(--u)); }
.foot { display: flex; align-items: flex-end; justify-content: space-between; gap: calc(30 * var(--u)); }
.t { font-family: BC; font-weight: 900; text-transform: uppercase; font-size: calc(94 * var(--u));
  line-height: .93; white-space: nowrap; }
.mark { width: calc(150 * var(--u)); height: calc(150 * var(--u)); border-radius: calc(34 * var(--u));
  box-shadow: 0 0 0 calc(2 * var(--u)) rgba(255,255,255,.08), 0 calc(14 * var(--u)) calc(40 * var(--u)) rgba(0,0,0,.6); }

/* the phone — sized in script from the space the stage leaves */
.device { position: relative; border-radius: var(--r0); padding: var(--ring);
  background: linear-gradient(140deg, #6b6d73 0%, #2b2c30 18%, #17181b 50%, #3a3b40 82%, #75777d 100%);
  box-shadow: 0 calc(70 * var(--u)) calc(140 * var(--u)) rgba(0,0,0,.75), 0 0 calc(160 * var(--u)) rgba(0,230,118,.10); }
.bezel { border-radius: var(--r1); padding: var(--bez); background: #000; height: 100%; }
.screen { position: relative; border-radius: var(--r2); overflow: hidden; background: #0a0a0a; height: 100%;
  display: flex; flex-direction: column; }
.status { flex: 0 0 auto; height: var(--sb); display: flex; align-items: center; justify-content: space-between;
  padding: 0 var(--sbpad); color: #fff; background: #0a0a0a; }
.time { font-family: B; font-weight: 600; font-size: var(--tf); letter-spacing: calc(.2 * var(--u)); }
.icons { display: flex; align-items: center; gap: var(--ig); }
.icons svg { height: var(--ih); width: auto; fill: #fff; }
.icons svg:last-child { height: calc(var(--ih) * 1.05); }
.app { display: block; width: 100%; height: auto; }
.island { position: absolute; left: 50%; transform: translateX(-50%); top: var(--it); width: var(--iw); height: var(--ihh);
  border-radius: 999px; background: #000; }
.btn { position: absolute; width: var(--bw); border-radius: var(--bw);
  background: linear-gradient(90deg, #2b2c30, #55575c 50%, #2b2c30); }
.act { left: calc(var(--bw) * -0.8); top: 17%; height: 4.2%; }
.vup { left: calc(var(--bw) * -0.8); top: 24%; height: 7.5%; }
.vdn { left: calc(var(--bw) * -0.8); top: 33%; height: 7.5%; }
.pwr { right: calc(var(--bw) * -0.8); top: 27%; height: 11%; }

/* hero */
.hero .hx { font-size: calc(190 * var(--u)); }
.sub { font-family: B; font-weight: 500; color: #c9c9c9; font-size: calc(54 * var(--u)); line-height: 1.3;
  margin-top: calc(42 * var(--u)); max-width: calc(1000 * var(--u)); }
.chips { display: flex; gap: calc(22 * var(--u)); margin-top: calc(48 * var(--u)); }
.chips span { font-family: BC; font-weight: 800; text-transform: uppercase; color: var(--green);
  font-size: calc(46 * var(--u)); letter-spacing: calc(2 * var(--u)); padding: calc(12 * var(--u)) calc(34 * var(--u));
  border: calc(3 * var(--u)) solid rgba(0,230,118,.55); border-radius: 999px; background: rgba(0,230,118,.08); }
.brand { display: flex; align-items: center; gap: calc(26 * var(--u)); margin-bottom: calc(70 * var(--u)); }
.brand img { width: calc(104 * var(--u)); height: calc(104 * var(--u)); border-radius: calc(24 * var(--u));
  box-shadow: 0 0 0 calc(2 * var(--u)) rgba(255,255,255,.08); }
.brand span { font-family: BC; font-weight: 800; text-transform: uppercase; color: #fff;
  font-size: calc(64 * var(--u)); letter-spacing: calc(6 * var(--u)); }
.tilt { position: absolute; left: calc(250 * var(--u)); top: calc(1130 * var(--u));
  transform: perspective(calc(3400 * var(--u))) rotateX(16deg) rotateY(-26deg) rotateZ(9deg); transform-origin: 30% 0; }
</style></head><body>${body}
<script>
  // Fit the phone: the stage's height (or the hero's fixed width) decides the
  // screen; bezel, corners, status bar and island all scale from it, using the
  // iPhone 6.9" proportions (440 x 956 pt screen, 54 pt status bar).
  window.__layout = () => {
    const u = ${u}
    const ring = 9 * u, bez = 15 * u, pad = ring + bez
    const dev = document.querySelector('.device')
    let sw
    if (document.querySelector('.hero')) {
      sw = 1060 * u
    } else {
      const st = document.querySelector('.stage'), cs = getComputedStyle(st)
      const avail = st.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom)
      sw = (avail - 2 * pad) * 440 / 956
    }
    const k = sw / 440
    const sh = 956 * k
    dev.style.width = (sw + 2 * pad) + 'px'
    dev.style.height = (sh + 2 * pad) + 'px'
    const set = (n, v) => dev.style.setProperty(n, v + 'px')
    set('--ring', ring); set('--bez', bez)
    set('--r2', 55 * k); set('--r1', 55 * k + bez); set('--r0', 55 * k + pad)
    set('--sb', 54 * k); set('--sbpad', 30 * k); set('--tf', 17 * k); set('--ih', 12.5 * k); set('--ig', 6 * k)
    set('--it', 11 * k); set('--iw', 126 * k); set('--ihh', 37 * k); set('--bw', 7 * u)
  }
</script></body></html>`
}

const browser = await puppeteer.launch({ headless: true, args: ['--no-sandbox', '--disable-gpu'] })
try {
  const pg = await browser.newPage()
  for (const size of SIZES) {
    const dir = path.join(out, size.name)
    fs.mkdirSync(dir, { recursive: true })
    for (const f of FRAMES) {
      await pg.setViewport({ width: size.w, height: size.h, deviceScaleFactor: 1 })
      await pg.setContent(page(f, size.w, size.h), { waitUntil: 'load' })
      await pg.evaluate(async () => { await document.fonts.ready; window.__layout() })
      await pg.evaluate(() => Promise.all([...document.images].map(i => i.decode().catch(() => {}))))
      const file = path.join(dir, `${f.name}.png`)
      await pg.screenshot({ path: file, type: 'png' })
      console.log('wrote', file)
    }
  }
} finally {
  await browser.close()
}
