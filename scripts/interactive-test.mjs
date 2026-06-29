import { chromium } from 'playwright'
const URL = process.env.URL || 'http://localhost:4173/'
const OUT = '/tmp/claude-0/-home-user-VideoSimulations/9a692233-2af7-590f-848c-4e352b60128e/scratchpad'

const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', headless: true,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    '--ignore-gpu-blocklist', '--enable-webgl', '--disable-dev-shm-usage', '--no-sandbox'],
})
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } })
const errors = []
page.on('pageerror', e => errors.push('PAGEERROR ' + e.message))
page.on('console', m => { if (m.type() === 'error' && !/fonts|favicon|net::ERR/.test(m.text())) errors.push('CONSOLE ' + m.text()) })

await page.goto(URL, { waitUntil: 'load' })
await page.waitForFunction(() => window.__app != null, null, { timeout: 30000 })
await page.waitForTimeout(3500)

const results = {}
// 1) pause/play
await page.click('#playBtn'); await page.waitForTimeout(300)
results.afterPause = await page.evaluate(() => window.__app.progress)
await page.waitForTimeout(600)
results.stillPaused = await page.evaluate(() => window.__app.progress) // should equal afterPause
await page.click('#playBtn') // resume

// 2) speed 4x
await page.click('#speedCtl .sp:nth-child(4)'); await page.waitForTimeout(50)
results.speedActive = await page.$eval('#speedCtl .sp:nth-child(4)', el => el.classList.contains('active'))

// 3) seek via timeline click (mid)
const box = await page.$eval('#tlTrack', el => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height } })
await page.mouse.click(box.x + box.w * 0.7, box.y + box.h / 2)
await page.waitForTimeout(200)
results.seekProgress = await page.evaluate(() => window.__app.progress) // ~0.7

// 4) toggle layers (territory off then on)
const layerRow = await page.$$('#layerBody .layer-row')
await layerRow[0].click(); await page.waitForTimeout(150)
results.layerToggledOff = await page.$eval('#layerBody .layer-row:first-child', el => el.classList.contains('off'))
await layerRow[0].click() // back on

// 5) auto-cam toggle
await page.click('#orbitBtn'); await page.waitForTimeout(100)
results.autoCamToggled = await page.$eval('#orbitBtn', el => !el.classList.contains('active'))
await page.click('#orbitBtn') // back on

// 6) restart
await page.click('#restartBtn'); await page.waitForTimeout(200)
results.restartProgress = await page.evaluate(() => window.__app.progress) // ~0

// 7) HUD live values present
results.hud = await page.evaluate(() => ({
  date: document.getElementById('dateReadout').textContent,
  phase: document.getElementById('phaseTag').textContent,
  counters: document.querySelectorAll('#counters .cell').length,
  feedItems: document.querySelectorAll('#feedBody .feed-item').length,
  tlTicks: document.querySelectorAll('#tlTicks .tl-tick').length,
}))

await page.screenshot({ path: `${OUT}/interactive_check.png` })
await browser.close()

console.log('RESULTS', JSON.stringify(results, null, 2))
console.log('ERRORS', errors.length ? errors : 'none')
process.exit(errors.length ? 1 : 0)
