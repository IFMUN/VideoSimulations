import { chromium } from 'playwright'

const URL = process.env.URL || 'http://localhost:4173/'
const OUT = process.env.OUT || '/tmp/claude-0/-home-user-VideoSimulations/9a692233-2af7-590f-848c-4e352b60128e/scratchpad'

const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  headless: true,
  args: [
    '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
    '--ignore-gpu-blocklist', '--enable-webgl', '--disable-dev-shm-usage', '--no-sandbox',
  ],
})
const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 })

const logs = []
page.on('console', (m) => logs.push(`[${m.type()}] ${m.text()}`))
page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`))

await page.goto(URL, { waitUntil: 'load', timeout: 30000 })
await page.waitForTimeout(4000)

const status = await page.evaluate(() => {
  const loaderHidden = document.getElementById('loader')?.classList.contains('hidden')
  const canvas = document.getElementById('scene')
  const gl = canvas?.getContext('webgl2') || canvas?.getContext('webgl')
  return {
    loaderHidden,
    hasApp: !!window.__app,
    cities: window.__app?.data?.cities?.length,
    events: window.__app?.data?.events?.length,
    canvasW: canvas?.width, canvasH: canvas?.height,
    webgl: !!gl,
  }
})
console.log('STATUS', JSON.stringify(status, null, 2))

// screenshot at several timeline positions
const shots = [0.0, 0.12, 0.28, 0.55, 0.82, 1.0]
for (const p of shots) {
  await page.evaluate((pp) => { if (window.__app) window.__app.progress = pp }, p)
  await page.waitForTimeout(900)
  const f = `${OUT}/shot_${String(Math.round(p * 100)).padStart(3, '0')}.png`
  await page.screenshot({ path: f })
  console.log('shot', f)
}

console.log('--- console logs ---')
console.log(logs.slice(-40).join('\n'))

await browser.close()
