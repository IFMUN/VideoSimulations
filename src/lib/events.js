import * as THREE from 'three'
import { sideHex, parseDate, clamp01, smoothstep } from './geo.js'

/* ============================================================
   Event pings + battle FX. As the clock sweeps past an event it
   flares: an expanding shockwave + warm fireball + ballistic
   debris, and — where a sourced offensive gives an attack axis —
   tracer "flying shots" streaming in with a muzzle flash. All
   motion is a deterministic function of |now - eventDate| and a
   per-instance hash (no Math.random), so it reads correctly and
   identically whether playing, scrubbing or frame-capturing.
   ============================================================ */

const PING_DAYS = 16
const DAY = 86400000
const COMBAT = new Set(['battle', 'capture', 'offensive', 'siege', 'airstrike', 'liberation'])
const SHARDS = (mag) => (mag >= 4 ? 14 : mag >= 3 ? 9 : 5)
const TRACERS = 9

const _dummy = new THREE.Object3D()
const _col = new THREE.Color()

// deterministic per-(i,k) hash -> [0,1)
function srnd(i, k) {
  let h = (Math.imul(i, 374761393) + Math.imul(k, 668265263)) >>> 0
  h = Math.imul(h ^ (h >>> 13), 1274126177) >>> 0
  h = (h ^ (h >>> 16)) >>> 0
  return (h % 100000) / 100000
}

let FLASH_TEX = null
function flashTex() {
  if (FLASH_TEX) return FLASH_TEX
  const s = 128, c = document.createElement('canvas'); c.width = c.height = s
  const ctx = c.getContext('2d')
  const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2)
  g.addColorStop(0, 'rgba(255,255,255,1)')
  g.addColorStop(0.4, 'rgba(255,255,255,0.4)')
  g.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = g; ctx.fillRect(0, 0, s, s)
  FLASH_TEX = new THREE.CanvasTexture(c)
  return FLASH_TEX
}

export class Events {
  constructor(projector, terrain, events, offensives = null) {
    this.projector = projector
    this.terrain = terrain
    this.offensives = offensives || []
    this.group = new THREE.Group()
    this.items = []
    let nShards = 0, nTracers = 0
    for (let i = 0; i < (events || []).length; i++) {
      const it = this._build(events[i], i)
      it.shardBase = nShards; nShards += it.shardCount
      it.tracerBase = nTracers; nTracers += it.tracerCount
    }
    this._buildInstanced(Math.max(1, nShards), Math.max(1, nTracers))
  }
  addTo(scene) { scene.add(this.group) }

  // nearest sourced offensive (in time + space) gives a battle its attack axis
  _attackDir(e) {
    const ems = parseDate(e.date)
    let best = null, bestScore = Infinity
    for (const o of this.offensives) {
      if (!o.from || !o.to) continue
      const od = parseDate(o.date), oe = o.endDate ? parseDate(o.endDate) : od + 22 * DAY
      const tGap = ems < od ? (od - ems) : ems > oe ? (ems - oe) : 0
      const tDays = tGap / DAY
      if (tDays > 45) continue
      const dLat = e.lat - o.to[0], dLon = (e.lon - o.to[1]) * 0.8
      const dKm = Math.sqrt(dLat * dLat + dLon * dLon) * 111
      if (dKm > 90) continue
      const score = tDays + dKm * 0.4
      if (score < bestScore) { bestScore = score; best = o }
    }
    if (!best) return null
    const p = this.projector
    const dx = p.x(best.to[1]) - p.x(best.from[1])
    const dz = p.z(best.to[0]) - p.z(best.from[0])
    const L = Math.hypot(dx, dz) || 1
    return { dx: dx / L, dz: dz / L }
  }

  _build(e, idx) {
    const p = this.projector
    const hex = sideHex(e.side)
    const combat = COMBAT.has(e.kind)
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

    // core flash — warm fireball for combat, faction-tinted otherwise
    const flash = new THREE.Sprite(new THREE.SpriteMaterial({
      map: flashTex(), color: combat ? 0xffb066 : hex, transparent: true, opacity: 0,
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

    // attack axis for tracer fire (only combat events that map to an offensive)
    const dir = combat ? this._attackDir(e) : null
    let muzzle = null
    const LANE = 24
    if (dir) {
      muzzle = new THREE.Sprite(new THREE.SpriteMaterial({
        map: flashTex(), color: 0xffd9a0, transparent: true, opacity: 0,
        blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false,
      }))
      muzzle.position.set(-dir.dx * LANE, 1.4, -dir.dz * LANE)
      muzzle.scale.setScalar(3 + mag * 0.4)
      g.add(muzzle)
    }

    this.group.add(g)
    const item = {
      e, g, dot, flash, ring, streak, muzzle, dir, lane: LANE,
      start: parseDate(e.date), hex, mag, baseY: y, base, combat,
      shardCount: combat ? SHARDS(mag) : 0,
      tracerCount: dir ? TRACERS : 0,
    }
    this.items.push(item)
    return item
  }

  _buildInstanced(nShards, nTracers) {
    // shared ballistic debris (one draw call for every event's shards)
    this.debris = new THREE.InstancedMesh(
      new THREE.TetrahedronGeometry(0.36),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false }),
      nShards)
    this.debris.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
    this.debris.frustumCulled = false
    // shared tracer rounds (elongated, stream along the attack axis)
    this.tracers = new THREE.InstancedMesh(
      new THREE.BoxGeometry(0.16, 0.16, 1.3),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false }),
      nTracers)
    this.tracers.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
    this.tracers.frustumCulled = false

    // assign per-instance colours up front (warm-hot debris, faction-bright tracers)
    for (const it of this.items) {
      for (let s = 0; s < it.shardCount; s++) {
        _col.setHex(it.e.kind === 'airstrike' ? 0xffe39a : 0xff8a3d).lerp(new THREE.Color(it.hex), 0.35)
        this.debris.setColorAt(it.shardBase + s, _col)
      }
      _col.setHex(it.hex).multiplyScalar(1.35)
      for (let k = 0; k < it.tracerCount; k++) this.tracers.setColorAt(it.tracerBase + k, _col)
    }
    if (this.debris.instanceColor) this.debris.instanceColor.needsUpdate = true
    if (this.tracers.instanceColor) this.tracers.instanceColor.needsUpdate = true
    // start hidden
    _dummy.scale.setScalar(0); _dummy.position.set(0, -9999, 0); _dummy.updateMatrix()
    for (let i = 0; i < nShards; i++) this.debris.setMatrixAt(i, _dummy.matrix)
    for (let i = 0; i < nTracers; i++) this.tracers.setMatrixAt(i, _dummy.matrix)
    this.debris.instanceMatrix.needsUpdate = true
    this.tracers.instanceMatrix.needsUpdate = true
    this.group.add(this.debris)
    this.group.add(this.tracers)
  }

  update(t, ms) {
    let debrisDirty = false, tracersDirty = false
    for (let gi = 0; gi < this.items.length; gi++) {
      const it = this.items[gi]
      const dDays = (ms - it.start) / DAY
      const passed = smoothstep(-2, 2, dDays)
      it.dot.material.opacity = passed * 0.4
      it.g.visible = ms >= it.start - PING_DAYS * DAY

      const ping = Math.max(0, 1 - Math.abs(dDays) / PING_DAYS)
      const pe = ping * ping
      const ringT = clamp01((dDays + PING_DAYS * 0.3) / (PING_DAYS * 1.2))

      it.ring.scale.setScalar(1 + ringT * (5 + it.mag * 2.2))
      it.ring.material.opacity = pe * (1 - ringT) * 0.9

      const fl = pe * (0.6 + 0.4 * Math.sin(t * 9))
      it.flash.material.opacity = fl * (it.combat ? 0.85 : 0.6)
      it.flash.scale.setScalar((2 + it.mag * 0.7) * (0.55 + 0.5 * pe))

      if (it.streak) {
        it.streak.material.opacity = Math.max(0, pe - 0.2) * 0.9
        it.streak.position.y = 12 - pe * 4
      }
      if (it.muzzle) it.muzzle.material.opacity = pe * (0.4 + 0.6 * Math.abs(Math.sin(t * 26 + gi)))

      // ballistic debris burst (front-loaded around the event date). Only write the
      // instance matrices while active, plus once on the active->inactive transition,
      // so idle frames don't re-zero & re-upload hundreds of instances.
      if (it.shardCount) {
        const bp = (dDays + 0.5) / (PING_DAYS * 0.5)   // 0..1 over the burst
        const active = bp > 0 && bp < 1
        if (active) {
          for (let s = 0; s < it.shardCount; s++) {
            const az = srnd(gi * 53 + s, 1) * Math.PI * 2
            const elev = 0.55 + srnd(gi * 53 + s, 2) * 0.85
            const spd = 7 + srnd(gi * 53 + s, 3) * 11 + it.mag * 1.6
            const ch = Math.cos(elev), sh = Math.sin(elev)
            const dist = spd * bp
            _dummy.position.set(
              it.base.x + Math.cos(az) * ch * dist,
              it.base.y + 0.4 + sh * spd * bp - 15 * bp * bp,
              it.base.z + Math.sin(az) * ch * dist)
            const sc = Math.max(0, (1 - bp)) * (0.55 + it.mag * 0.12)
            _dummy.rotation.set(t * 3 + s, t * 2.3 + az, 0)
            _dummy.scale.setScalar(sc)
            _dummy.updateMatrix()
            this.debris.setMatrixAt(it.shardBase + s, _dummy.matrix)
          }
          debrisDirty = true
        } else if (it.wasActive) {
          _dummy.scale.setScalar(0); _dummy.updateMatrix()
          for (let s = 0; s < it.shardCount; s++) this.debris.setMatrixAt(it.shardBase + s, _dummy.matrix)
          debrisDirty = true
        }
        it.wasActive = active
      }

      // tracer "flying shots" streaming along the attack axis while the battle is hot
      if (it.tracerCount && it.dir) {
        const hot = pe > 0.02
        if (hot) {
          for (let k = 0; k < it.tracerCount; k++) {
            const seed = srnd(gi * 71 + k, 5)
            const tr = (t * (0.55 + seed * 0.4) + seed) % 1            // 0..1 along the lane
            const lat = (srnd(gi * 71 + k, 6) - 0.5) * 3.5             // lateral spread
            const along = (tr - 1) * it.lane                          // start behind, fly to target
            const px = it.base.x + it.dir.dx * along - it.dir.dz * lat
            const pz = it.base.z + it.dir.dz * along + it.dir.dx * lat
            const py = it.base.y + 1.6 + Math.sin(tr * Math.PI) * 2.2  // shallow arc
            _dummy.position.set(px, py, pz)
            _dummy.lookAt(px + it.dir.dx, py, pz + it.dir.dz)
            const fade = Math.sin(tr * Math.PI)                        // bright mid-flight
            _dummy.scale.set(1, 1, 1.6 + it.mag * 0.3).multiplyScalar(0.6 + 0.6 * fade * pe)
            _dummy.updateMatrix()
            this.tracers.setMatrixAt(it.tracerBase + k, _dummy.matrix)
          }
          tracersDirty = true
        } else if (it.wasHot) {
          _dummy.scale.setScalar(0); _dummy.updateMatrix()
          for (let k = 0; k < it.tracerCount; k++) this.tracers.setMatrixAt(it.tracerBase + k, _dummy.matrix)
          tracersDirty = true
        }
        it.wasHot = hot
      }
    }
    if (debrisDirty) this.debris.instanceMatrix.needsUpdate = true
    if (tracersDirty) this.tracers.instanceMatrix.needsUpdate = true
  }

  // events whose date is within `windowDays` of `ms` (for the live ops feed)
  activeNear(ms, windowDays = 30) {
    return this.items
      .filter(it => Math.abs((ms - it.start) / DAY) <= windowDays)
      .sort((a, b) => Math.abs(ms - a.start) - Math.abs(ms - b.start))
      .map(it => it.e)
  }
}
