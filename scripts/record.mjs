/* Frame-exact recorder: drives the app in deterministic record mode, captures
   every frame as a PNG, then encodes a WebM (VP8) with ffmpeg. Smooth output
   regardless of (software) WebGL render speed.
   Usage: node scripts/record.mjs  [URL]
   Env: W,H,FPS,TAIL,OUT,FRAMES,FFMPEG */
import { chromium } from 'playwright'
import { mkdirSync, rmSync, existsSync, readdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'

const URL = process.argv[2] || process.env.URL || 'http://localhost:4173/'
const W = +(process.env.W || 1600)
const H = +(process.env.H || 900)
const FPS = +(process.env.FPS || 24)
const DUR = +(process.env.DUR || 165)
const TAIL = +(process.env.TAIL || 2.2)
const FRAMES = process.env.FRAMES || '/tmp/claude-0/-home-user-VideoSimulations/9a692233-2af7-590f-848c-4e352b60128e/scratchpad/frames'
const OUT = process.env.OUT || '/home/user/VideoSimulations/media/isis-peshmerga-war-map.webm'
const FFMPEG = process.env.FFMPEG || '/opt/pw-browsers/ffmpeg-1011/ffmpeg-linux'
const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'

if (existsSync(FRAMES)) rmSync(FRAMES, { recursive: true })
mkdirSync(FRAMES, { recursive: true })
mkdirSync(OUT.replace(/\/[^/]+$/, ''), { recursive: true })

const browser = await chromium.launch({
  executablePath: CHROME, headless: true,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    '--ignore-gpu-blocklist', '--enable-webgl', '--disable-dev-shm-usage', '--no-sandbox',
    '--hide-scrollbars', '--force-device-scale-factor=1'],
})
const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 })
page.on('pageerror', e => console.log('[pageerror]', e.message))

const recUrl = `${URL}?record=1&fps=${FPS}&dur=${DUR}`
console.log('opening', recUrl)
await page.goto(recUrl, { waitUntil: 'load', timeout: 45000 })
await page.waitForFunction(() => window.__recordReady === true, null, { timeout: 45000 })
await page.waitForTimeout(800) // let fonts/textures settle

const { INTRO, DURATION } = await page.evaluate(() => ({ INTRO: window.__app.INTRO, DURATION: window.__app.DURATION }))
const total = Math.ceil((INTRO + DURATION + TAIL) * FPS)
console.log(`recording ${total} frames @ ${FPS}fps  (intro ${INTRO}s + run ${DURATION}s + tail ${TAIL}s)  ${W}x${H}`)

const t0 = Date.now()
for (let i = 0; i < total; i++) {
  await page.evaluate(([f, fps]) => window.__captureFrame(f, fps), [i, FPS])
  await page.screenshot({ path: `${FRAMES}/f_${String(i).padStart(5, '0')}.jpg`, type: 'jpeg', quality: 92 })
  if (i % 60 === 0 || i === total - 1) {
    const pct = ((i + 1) / total * 100).toFixed(0)
    const eta = ((Date.now() - t0) / (i + 1) * (total - i - 1) / 1000).toFixed(0)
    console.log(`  frame ${i + 1}/${total} (${pct}%)  eta ${eta}s`)
  }
}
await browser.close()

const nframes = readdirSync(FRAMES).filter(f => f.endsWith('.jpg')).length
console.log(`captured ${nframes} frames; encoding -> ${OUT}`)
// This ffmpeg build only decodes mjpeg via image2pipe, so concat the JPEGs in.
const cmd = `cat ${FRAMES}/f_*.jpg | '${FFMPEG}' -y -f image2pipe -vcodec mjpeg -framerate ${FPS} -i pipe:0 ` +
  `-c:v libvpx -b:v 4M -crf 12 -deadline good -cpu-used 2 -auto-alt-ref 0 -pix_fmt yuv420p '${OUT}'`
const r = spawnSync('bash', ['-c', cmd], { stdio: 'inherit' })
if (r.status !== 0) { console.error('ffmpeg failed', r.status); process.exit(1) }
console.log('done:', OUT)
