// Render the Short: deterministic frames from scene.html, muxed with the
// narration build.py already produced.
//
// The duration comes from plan.json, which came from the MEASURED length of the
// synthesised speech — so the picture is exactly as long as the voiceover and
// neither has to be trimmed to fit the other.
const { chromium } = require('playwright')
const { execFileSync } = require('child_process')
const ffmpeg = require('ffmpeg-static')
const path = require('path')
const fs = require('fs')

const HERE = __dirname
const OUT = path.join(HERE, 'out')
const W = 1080, H = 1920, FPS = 30

;(async () => {
  const plan = JSON.parse(fs.readFileSync(path.join(OUT, 'plan.json'), 'utf8'))
  const narration = path.join(OUT, 'narration.wav')
  if (!fs.existsSync(narration)) throw new Error('run build.py first — no narration.wav')

  const FRAMES = path.join(OUT, 'frames')
  fs.rmSync(FRAMES, { recursive: true, force: true }); fs.mkdirSync(FRAMES, { recursive: true })

  const browser = await chromium.launch({ args: ['--hide-scrollbars', '--force-color-profile=srgb'] })
  const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 })
  await page.goto('file:///' + path.join(HERE, 'scene.html').replace(/\\/g, '/'),
                  { waitUntil: 'networkidle', timeout: 60000 })
  // Fonts and the logo must be decoded before frame 0 or the open renders in a
  // fallback face and the type jumps when Barlow lands.
  await page.evaluate(() => document.fonts.ready)
  await page.evaluate(p => window.__init(p), plan)
  await page.evaluate(() => Promise.all([...document.images]
    .filter(i => !i.complete).map(i => new Promise(r => { i.onload = i.onerror = r }))))
  await page.waitForTimeout(400)

  const total = Math.round(plan.duration * FPS)
  console.log(`rendering ${total} frames (${plan.duration}s) at ${W}x${H}`)
  for (let i = 0; i < total; i++) {
    await page.evaluate(t => window.__setT(t), i / FPS)
    await page.screenshot({ path: path.join(FRAMES, String(i).padStart(4, '0') + '.png') })
    if (i % 90 === 0) process.stdout.write(`  ${i}/${total}\n`)
  }
  await browser.close()

  const mp4 = path.join(OUT, `baseline-short-${plan.date}.mp4`)
  execFileSync(ffmpeg, ['-y', '-framerate', String(FPS),
    '-i', path.join(FRAMES, '%04d.png'), '-i', narration,
    // 48kHz AAC-LC with no edit list — the same combination that fixed silent
    // playback on mobile for the promo.
    '-af', 'loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000:first_pts=0',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '19',
    '-profile:v', 'high', '-level', '4.2', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-profile:a', 'aac_low', '-b:a', '192k', '-ar', '48000', '-ac', '2',
    '-muxpreload', '0', '-muxdelay', '0', '-movflags', '+faststart', '-shortest',
    mp4], { stdio: 'pipe' })

  console.log(`OK ${mp4} (${(fs.statSync(mp4).size / 1024 / 1024).toFixed(2)} MB)`)

  const picks = plan.segments.map(s => Math.round((s.start + s.dur * 0.55) * FPS))
  picks.forEach((n, k) => {
    const src = path.join(FRAMES, String(Math.min(n, total - 1)).padStart(4, '0') + '.png')
    if (fs.existsSync(src)) fs.copyFileSync(src, path.join(FRAMES, `pick${k}.png`))
  })
  execFileSync(ffmpeg, ['-y', '-i', path.join(FRAMES, 'pick%d.png'),
    '-filter_complex', `scale=300:-2,tile=${picks.length}x1:padding=6:color=0x333333`,
    path.join(OUT, 'sheet.png')], { stdio: 'pipe' })
  console.log('sheet ->', path.join(OUT, 'sheet.png'))
})().catch(e => { console.error('FAILED:', e.message.split('\n')[0]); process.exit(1) })
