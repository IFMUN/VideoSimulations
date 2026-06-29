import * as THREE from 'three'
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js'

import { Projector, parseDate, clamp01, lerp } from './lib/geo.js'
import { Terrain } from './lib/terrain.js'
import { Territory } from './lib/territory.js'
import { Markers } from './lib/markers.js'
import { Arrows } from './lib/arrows.js'
import { Events } from './lib/events.js'
import { CameraRig } from './lib/camera.js'
import { HUD } from './lib/hud.js'

const params = new URLSearchParams(location.search)
const RECORD = params.has('record')
const FPS = parseInt(params.get('fps') || '30', 10)
const DURATION = parseFloat(params.get('dur') || '82')   // seconds for full timeline at 1x
const INTRO = RECORD ? 2.6 : 2.2                          // seconds of reveal before the clock runs
const DAY = 86400000

async function boot() {
  const data = await fetch(new URL('./data/conflict.json', import.meta.url)).then(r => r.json())
  prepStats(data)

  const canvas = document.getElementById('scene')
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' })
  renderer.setPixelRatio(RECORD ? 1 : Math.min(devicePixelRatio, 1.5))
  renderer.setSize(innerWidth, innerHeight)
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 0.96

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x05080d)
  scene.fog = new THREE.FogExp2(0x06090f, 0.00085)

  const projector = new Projector(data.bounds)
  const worldRadius = Math.hypot(projector.worldWidth, projector.worldDepth) * 0.5

  const camera = new THREE.PerspectiveCamera(42, innerWidth / innerHeight, 0.5, 4000)
  const rig = new CameraRig(camera, canvas, worldRadius)

  // ---- layers ----
  const terrain = new Terrain(projector, data); terrain.addTo(scene)
  const territory = new Territory(projector, data.control)
  terrain.setTerritoryTexture(territory.tex)
  const markers = new Markers(projector, terrain); markers.addTo(scene)
  for (const c of (data.cities || [])) markers.addCity(c)
  for (const b of (data.bases || [])) markers.addBase(b)
  const arrows = new Arrows(projector, terrain, data.offensives); arrows.addTo(scene)
  const events = new Events(projector, terrain, data.events); events.addTo(scene)

  buildRivers(scene, projector, terrain, data.rivers)
  atmosphere(scene, projector, worldRadius)

  // ---- post fx ----
  const composer = new EffectComposer(renderer)
  composer.addPass(new RenderPass(scene, camera))
  const bloom = new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.38, 0.36, 0.82)
  composer.addPass(bloom)
  composer.addPass(new OutputPass())

  // ---- clock / state ----
  const startMs = parseDate(data.meta.startDate)
  const endMs = parseDate(data.meta.endDate)
  const span = endMs - startMs
  let progress = 0, playing = true, speed = 1, animClock = 0, introT = 0
  let lastReal = performance.now(), extra = 0

  // ---- HUD ----
  const hud = new HUD(data, {
    onPlayToggle: () => { playing = !playing; hud.setPlaying(playing) },
    onRestart: () => { progress = 0; introT = 0; animClock = 0; playing = true; hud.setPlaying(true) },
    onSeek: (f) => { progress = clamp01(f); if (introT < INTRO) introT = INTRO },
    onSpeed: (s) => { speed = s },
    onAutoCam: (on) => rig.setAuto(on),
    onLayer: (name, on) => {
      if (name === 'territory') terrain.setLayer('territory', on)
      if (name === 'contours') terrain.setLayer('contours', on)
      if (name === 'arrows') arrows.setVisible(on)
      if (name === 'events') events.group.visible = on
      if (name === 'labels') markers.setLabelsVisible(on)
    },
  })
  hud.setPlaying(playing)

  // ---- hover tooltip ----
  setupHover(canvas, camera, markers, hud)

  // ---- resize ----
  addEventListener('resize', () => {
    camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix()
    renderer.setSize(innerWidth, innerHeight); composer.setSize(innerWidth, innerHeight)
    bloom.setSize(innerWidth, innerHeight)
  })

  document.getElementById('loader').classList.add('hidden')

  // Render the current state (animClock / introT / progress) and present a frame.
  function renderFrame(dt) {
    const reveal = Math.min(1.25, (introT / INTRO) * 1.25)
    const ms = startMs + progress * span

    territory.update(ms)
    terrain.update(animClock, reveal)
    const camDist = camera.position.distanceTo(rig.controls.target)
    markers.update(animClock, ms, camDist)
    arrows.update(animClock, ms)
    events.update(animClock, ms)

    const focus = hotspot(events, projector, terrain, ms)
    rig.update(dt, animClock, focus, progress)

    const stats = interpStats(data, ms)
    const phase = phaseAt(data, ms)
    const feed = events.activeNear(ms, 42)
    hud.update(ms, stats, phase, progress, feed)

    composer.render()
  }

  function loop() {
    const now = performance.now()
    const dt = Math.min(0.05, (now - lastReal) / 1000)
    lastReal = now
    animClock += dt
    if (introT < INTRO) introT += dt
    else if (playing) progress = clamp01(progress + (dt * speed) / DURATION)
    renderFrame(dt)
    requestAnimationFrame(loop)
  }

  if (RECORD) {
    // Deterministic, frame-exact capture driven by the recorder script.
    window.__captureFrame = (frame, fps) => {
      const t = frame / fps
      animClock = t
      introT = Math.min(INTRO, t)
      progress = clamp01(Math.max(0, t - INTRO) / DURATION)
      renderFrame(1 / fps)
    }
    window.__captureFrame(0, FPS)
    window.__recordReady = true
  } else {
    requestAnimationFrame(loop)
  }

  // expose for debugging / recorder
  window.__app = { data, scene, camera, INTRO, DURATION, get progress() { return progress }, set progress(v) { progress = v } }
}

/* ---------- helpers ---------- */
function prepStats(data) {
  data._stats = (data.stats || []).map(s => ({ ms: parseDate(s.date), ...s })).sort((a, b) => a.ms - b.ms)
}
function interpStats(data, ms) {
  const a = data._stats
  if (!a.length) return {}
  if (ms <= a[0].ms) return a[0]
  if (ms >= a[a.length - 1].ms) return a[a.length - 1]
  for (let i = 0; i < a.length - 1; i++) {
    if (ms >= a[i].ms && ms <= a[i + 1].ms) {
      const f = (ms - a[i].ms) / (a[i + 1].ms - a[i].ms || 1)
      const out = {}
      for (const k of Object.keys(a[i])) {
        if (k === 'ms' || k === 'date') continue
        const va = a[i][k], vb = a[i + 1][k]
        out[k] = (typeof va === 'number' && typeof vb === 'number') ? lerp(va, vb, f) : va
      }
      return out
    }
  }
  return a[a.length - 1]
}
function phaseAt(data, ms) {
  const ps = data.phases || []
  for (const p of ps) if (ms >= parseDate(p.start) && ms <= parseDate(p.end)) return p
  // nearest
  let best = ps[0], bd = 1e18
  for (const p of ps) { const d = Math.min(Math.abs(ms - parseDate(p.start)), Math.abs(ms - parseDate(p.end))); if (d < bd) { bd = d; best = p } }
  return best
}
function hotspot(events, projector, terrain, ms) {
  let wx = 0, wz = 0, wy = 0, w = 0
  for (const it of events.items) {
    const d = Math.abs(ms - it.start) / DAY
    if (d > 26) continue
    const weight = (1 - d / 26) * (it.mag || 2)
    wx += projector.x(it.e.lon) * weight
    wz += projector.z(it.e.lat) * weight
    wy += it.baseY * weight
    w += weight
  }
  if (w < 0.01) return new THREE.Vector3(0, 4, 0)
  return new THREE.Vector3(wx / w, wy / w, wz / w)
}

function buildRivers(scene, projector, terrain, rivers) {
  for (const r of (rivers || [])) {
    if (!r.path || r.path.length < 2) continue
    const pts = r.path.map(([lat, lon]) => new THREE.Vector3(projector.x(lon), terrain.yAt(lat, lon, 0) + 0.5, projector.z(lat)))
    const curve = new THREE.CatmullRomCurve3(pts)
    const geom = new THREE.TubeGeometry(curve, Math.max(24, pts.length * 8), 0.55, 6, false)
    const mat = new THREE.MeshBasicMaterial({ color: 0x2f6f95, transparent: true, opacity: 0.34, blending: THREE.AdditiveBlending, depthWrite: false })
    const mesh = new THREE.Mesh(geom, mat)
    mesh.renderOrder = 2
    scene.add(mesh)
  }
}

function atmosphere(scene, projector, worldRadius) {
  // faint dust / particulate above the theatre for cinematic depth
  const N = 900
  const pos = new Float32Array(N * 3)
  for (let i = 0; i < N; i++) {
    const r = worldRadius * (0.4 + Math.random() * 1.6)
    const a = Math.random() * Math.PI * 2
    pos[i * 3] = Math.cos(a) * r
    pos[i * 3 + 1] = 6 + Math.random() * worldRadius * 0.8
    pos[i * 3 + 2] = Math.sin(a) * r
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3))
  const m = new THREE.PointsMaterial({ color: 0x4d7f97, size: 0.5, transparent: true, opacity: 0.5, depthWrite: false, blending: THREE.AdditiveBlending })
  scene.add(new THREE.Points(g, m))

  // perimeter base ring (ops-table edge)
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(worldRadius * 1.02, worldRadius * 1.05, 96),
    new THREE.MeshBasicMaterial({ color: 0x16313d, transparent: true, opacity: 0.4, side: THREE.DoubleSide })
  )
  ring.rotation.x = -Math.PI / 2; ring.position.y = -1.4
  scene.add(ring)
}

function setupHover(canvas, camera, markers, hud) {
  const ray = new THREE.Raycaster()
  const tip = document.getElementById('tooltip')
  const mouse = new THREE.Vector2()
  let down = false
  canvas.addEventListener('pointerdown', () => { down = true })
  canvas.addEventListener('pointerup', () => { down = false })
  canvas.addEventListener('pointermove', (e) => {
    if (down) { tip.classList.remove('show'); return }
    mouse.x = (e.clientX / innerWidth) * 2 - 1
    mouse.y = -(e.clientY / innerHeight) * 2 + 1
    ray.setFromCamera(mouse, camera)
    const hits = ray.intersectObjects(markers.pickables, false)
    if (hits.length) {
      const info = hits[0].object.userData.info
      tip.style.setProperty('--side', sideCss(info.side))
      tip.innerHTML = `<div class="tt-title">${info.title}</div><div class="tt-meta">${info.meta || ''}</div>${info.blurb ? `<div class="tt-blurb">${info.blurb}</div>` : ''}`
      tip.style.left = Math.min(innerWidth - 250, e.clientX + 14) + 'px'
      tip.style.top = (e.clientY + 14) + 'px'
      tip.classList.add('show')
    } else tip.classList.remove('show')
  })
}
function sideCss(s) {
  const m = { isis: '#ff4d3d', peshmerga: '#1fe3c6', coalition: '#ffd166', iraqi: '#c7b25a', civilian: '#b98cff', contested: '#9aa6b2' }
  return m[s] || '#9aa6b2'
}

boot().catch(err => {
  console.error(err)
  const l = document.getElementById('loader')
  if (l) l.innerHTML = `<div class="loader-text" style="color:#ff6a52">BOOT ERROR · ${String(err.message || err)}</div>`
})
