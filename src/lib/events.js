import * as THREE from 'three'
import { sideHex, parseDate, clamp01, smoothstep } from './geo.js'

/* ============================================================
   Event pings. As the clock sweeps past an event it flares:
   an expanding shockwave ring + a core flash (+ a descending
   streak for airstrikes), then leaves a faint history dot.
   Intensity is a function of |now - eventDate| in days, so it
   reads correctly whether playing or scrubbing.
   ============================================================ */

const PING_DAYS = 16
const DAY = 86400000

let FLASH_TEX = null
function flashTex() {
  if (FLASH_TEX) return FLASH_TEX
  const s = 128, c = document.createElement('canvas'); c.width = c.height = s
  const ctx = c.getContext('2d')
  const g = ctx.createRadialGradient(s/2, s/2, 0, s/2, s/2, s/2)
  g.addColorStop(0, 'rgba(255,255,255,1)')
  g.addColorStop(0.4, 'rgba(255,255,255,0.4)')
  g.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = g; ctx.fillRect(0, 0, s, s)
  FLASH_TEX = new THREE.CanvasTexture(c)
  return FLASH_TEX
}

export class Events {
  constructor(projector, terrain, events) {
    this.projector = projector
    this.terrain = terrain
    this.group = new THREE.Group()
    this.items = []
    for (const e of (events || [])) this._build(e)
  }
  addTo(scene) { scene.add(this.group) }

  _build(e) {
    const p = this.projector
    const hex = sideHex(e.side)
    const y = this.terrain.yAt(e.lat, e.lon, 0)
    const base = new THREE.Vector3(p.x(e.lon), y, p.z(e.lat))
    const mag = e.magnitude || 2
    const g = new THREE.Group()
    g.position.copy(base)

    // history dot (persists after the event)
    const dot = new THREE.Sprite(new THREE.SpriteMaterial({
      map: flashTex(), color: hex, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false,
    }))
    dot.scale.setScalar(2 + mag * 0.5)
    dot.position.y = 0.6
    g.add(dot)

    // core flash
    const flash = new THREE.Sprite(new THREE.SpriteMaterial({
      map: flashTex(), color: hex, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false,
    }))
    flash.position.y = 1.2
    g.add(flash)

    // shockwave ring on the ground
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.9, 1.25, 48),
      new THREE.MeshBasicMaterial({ color: hex, transparent: true, opacity: 0, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false })
    )
    ring.rotation.x = -Math.PI / 2
    ring.position.y = 0.25
    g.add(ring)

    // airstrike descending streak
    let streak = null
    if (e.kind === 'airstrike') {
      streak = new THREE.Mesh(
        new THREE.CylinderGeometry(0.12, 0.0, 24, 6),
        new THREE.MeshBasicMaterial({ color: 0xffe39a, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false })
      )
      streak.position.y = 12
      g.add(streak)
    }

    this.group.add(g)
    this.items.push({ e, g, dot, flash, ring, streak, start: parseDate(e.date), hex, mag, baseY: y })
  }

  update(t, ms) {
    for (const it of this.items) {
      const dDays = (ms - it.start) / DAY
      // history presence
      const passed = smoothstep(-2, 2, dDays)
      it.dot.material.opacity = passed * 0.4
      it.g.visible = ms >= it.start - PING_DAYS * DAY

      // ping intensity around the event date
      const ping = Math.max(0, 1 - Math.abs(dDays) / PING_DAYS)
      const pe = ping * ping
      const ringT = clamp01((dDays + PING_DAYS * 0.3) / (PING_DAYS * 1.2))

      // shockwave expands and fades
      const s = 1 + ringT * (5 + it.mag * 2.2)
      it.ring.scale.setScalar(s)
      it.ring.material.opacity = pe * (1 - ringT) * 0.9

      // flash pulse
      const fl = pe * (0.6 + 0.4 * Math.sin(t * 9))
      it.flash.material.opacity = fl * 0.7
      it.flash.scale.setScalar((2 + it.mag * 0.7) * (0.55 + 0.5 * pe))

      if (it.streak) {
        it.streak.material.opacity = Math.max(0, pe - 0.2) * 0.9
        it.streak.position.y = 12 - pe * 4
      }
    }
  }

  // events whose date is within `windowDays` of `ms` (for the live ops feed)
  activeNear(ms, windowDays = 30) {
    return this.items
      .filter(it => Math.abs((ms - it.start) / DAY) <= windowDays)
      .sort((a, b) => Math.abs(ms - a.start) - Math.abs(ms - b.start))
      .map(it => it.e)
  }
}
