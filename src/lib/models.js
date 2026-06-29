import * as THREE from 'three'
import { sideHex, parseDate, clamp01, lerp } from './geo.js'

/* ============================================================
   Lively procedural 3D assets in a minimalist holographic style:
   dark faces + bright faction-coloured wireframe edges, so only
   the edges bloom. Walled base compounds with comms towers &
   command tents, border checkpoints, city clusters tinted by who
   holds them, and raid trucks that convoy along offensive routes.
   Everything attaches to the terrain via yAt and animates from
   the (deterministic) ms/t passed into update().
   ============================================================ */

const DAY = 86400000
const DRAW_DAYS = 22
const NEAR_DIST = 150       // closer than this -> show fine detail (tents, wheels)
const C_ISIS = new THREE.Color(0xff4d3d)
const C_PESH = new THREE.Color(0x1fe3c6)
const C_NEU = new THREE.Color(0x8a98a6)
const _scratch = new THREE.Color()

// dark-faced + glowing-edge primitive (the core holographic look)
function holo(geo, color, faceOp = 0.4, edgeOp = 0.85) {
  const g = new THREE.Group()
  const fill = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x060a10, transparent: true, opacity: faceOp, depthWrite: false }))
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color, transparent: true, opacity: edgeOp }))
  g.add(fill); g.add(edges)
  g.userData.edgeMat = edges.material
  return g
}

export class Models {
  constructor(projector, terrain, data, territory = null) {
    this.projector = projector
    this.terrain = terrain
    this.territory = territory
    this.group = new THREE.Group()
    this.visible = true
    this.bases = []
    this.cities = []
    this.convoys = []
    this.beacons = []
    this.detail = []          // fine-detail nodes toggled by camera distance
    this._buildBases(data.bases || [])
    this._buildCities(data.cities || [])
    this._buildCheckpoints()
    this._buildConvoys(data.offensives || [])
  }
  addTo(scene) { scene.add(this.group) }

  _surf(lat, lon, lift = 0) {
    const p = this.projector
    return new THREE.Vector3(p.x(lon), this.terrain.yAt(lat, lon, 0) + lift, p.z(lat))
  }

  _commsTower(hex, s) {
    const g = new THREE.Group()
    const mast = holo(new THREE.CylinderGeometry(0.06 * s, 0.16 * s, 3.2 * s, 5), hex, 0.3, 0.8)
    mast.position.y = 1.6 * s
    g.add(mast)
    // blinking beacon at the top
    const beacon = new THREE.Mesh(
      new THREE.SphereGeometry(0.16 * s, 8, 8),
      new THREE.MeshBasicMaterial({ color: hex, transparent: true, opacity: 0.9, blending: THREE.AdditiveBlending, depthWrite: false })
    )
    beacon.position.y = 3.3 * s
    g.add(beacon)
    this.beacons.push(beacon)
    return g
  }

  _tent(hex, s) {
    const t = holo(new THREE.ConeGeometry(0.7 * s, 1.0 * s, 4), hex, 0.35, 0.8)
    t.rotation.y = Math.PI / 4
    t.position.y = 0.5 * s
    return t
  }

  _buildBases(bases) {
    for (const b of bases) {
      const hex = sideHex(b.side)
      const isCmd = b.kind === 'command' || b.kind === 'hq'
      const isAir = b.kind === 'airbase'
      const s = isCmd ? 1.35 : 1.0
      const g = new THREE.Group()
      g.position.copy(this._surf(b.lat, b.lon, 0.1))

      // perimeter wall (low wireframe enclosure)
      const wall = holo(new THREE.BoxGeometry(3.6 * s, 0.9 * s, 3.6 * s, 1, 1, 1), hex, 0.18, 0.55)
      wall.position.y = 0.45 * s
      g.add(wall)

      // comms tower
      const tower = this._commsTower(hex, s)
      tower.position.set(1.1 * s, 0, 1.1 * s)
      g.add(tower)

      // command tent(s) — fine detail
      const tent = this._tent(hex, s)
      tent.position.set(-0.9 * s, 0, -0.7 * s)
      g.add(tent); this.detail.push(tent)
      if (isCmd) {
        const tent2 = this._tent(hex, s * 0.8); tent2.position.set(0.6 * s, 0, -0.9 * s)
        g.add(tent2); this.detail.push(tent2)
      }

      // airbase: a runway strip across the compound
      if (isAir) {
        const strip = holo(new THREE.BoxGeometry(4.6 * s, 0.06, 0.7 * s), hex, 0.12, 0.6)
        strip.position.y = 0.05
        g.add(strip)
      }

      this.group.add(g)
      this.bases.push({
        b, g,
        activeFrom: b.activeFrom ? parseDate(b.activeFrom) : null,
        activeTo: b.activeTo ? parseDate(b.activeTo) : null,
        phase: (b.lat * 5.7 + b.lon * 2.3) % (Math.PI * 2), vis: 0,
      })
    }
  }

  _buildCities(cities) {
    for (const c of cities) {
      if (c.kind !== 'metro' && c.kind !== 'strategic') continue
      const big = c.kind === 'metro'
      const g = new THREE.Group()
      g.position.copy(this._surf(c.lat, c.lon, 0.05))
      const n = big ? 6 : 4
      const edgeMats = []
      for (let i = 0; i < n; i++) {
        const hb = 0.35 * (i * 7.3 % 1 + 0.4)
        const w = (0.4 + (i * 3.1 % 1) * 0.5) * (big ? 1.1 : 0.85)
        const h = (0.8 + (i * 5.7 % 1) * (big ? 2.4 : 1.4))
        const b = holo(new THREE.BoxGeometry(w, h, w), 0xc6d6e2, 0.35, 0.8)
        const ang = (i / n) * Math.PI * 2
        const rad = big ? 0.9 : 0.6
        b.position.set(Math.cos(ang) * rad, h / 2, Math.sin(ang) * rad)
        g.add(b)
        edgeMats.push(b.userData.edgeMat)
      }
      this.group.add(g)
      this.cities.push({ c, g, edgeMats })
    }
  }

  // Border checkpoints at real, sourced crossings on the bbox borders.
  _buildCheckpoints() {
    const crossings = [
      { name: 'Rabia (Syria)', lat: 36.812, lon: 42.099, side: 'contested' },
      { name: 'Ibrahim Khalil / Fishkhabur (Turkey)', lat: 37.045, lon: 42.358, side: 'peshmerga' },
      { name: 'Haji Omaran (Iran)', lat: 36.633, lon: 44.905, side: 'peshmerga' },
    ]
    for (const c of crossings) {
      const hex = sideHex(c.side)
      const g = new THREE.Group()
      g.position.copy(this._surf(c.lat, c.lon, 0.1))
      // two gate posts + a barrier bar
      for (const x of [-0.7, 0.7]) {
        const post = holo(new THREE.BoxGeometry(0.2, 1.2, 0.2), hex, 0.3, 0.85)
        post.position.set(x, 0.6, 0); g.add(post)
      }
      const bar = holo(new THREE.BoxGeometry(1.7, 0.12, 0.12), hex, 0.3, 0.85)
      bar.position.set(0, 1.0, 0); g.add(bar)
      const booth = holo(new THREE.BoxGeometry(0.6, 0.7, 0.6), hex, 0.25, 0.8)
      booth.position.set(1.2, 0.35, 0); g.add(booth); this.detail.push(booth)
      this.group.add(g)
    }
  }

  _buildConvoys(offensives) {
    for (const o of offensives) {
      if (!o.from || !o.to) continue
      const hex = sideHex(o.side)
      const a = this._surf(o.from[0], o.from[1], 0)
      const b = this._surf(o.to[0], o.to[1], 0)
      const start = parseDate(o.date)
      const end = Math.max(o.endDate ? parseDate(o.endDate) : start + DRAW_DAYS * DAY, start + 4 * DAY)
      const trucks = []
      const N = o.side === 'isis' ? 4 : 3
      for (let k = 0; k < N; k++) {
        const truck = this._truck(hex)
        truck.visible = false
        this.group.add(truck)
        trucks.push(truck)
      }
      this.convoys.push({ o, a, b, start, end, trucks, hex })
    }
  }

  _truck(hex) {
    const g = new THREE.Group()
    const bed = holo(new THREE.BoxGeometry(0.5, 0.28, 1.15), hex, 0.4, 0.9)
    bed.position.y = 0.32; g.add(bed)
    const cab = holo(new THREE.BoxGeometry(0.46, 0.34, 0.4), hex, 0.4, 0.9)
    cab.position.set(0, 0.42, 0.5); g.add(cab)
    // axles (fine detail)
    for (const z of [-0.35, 0.35]) {
      const axle = holo(new THREE.CylinderGeometry(0.1, 0.1, 0.56, 6), hex, 0.3, 0.7)
      axle.rotation.z = Math.PI / 2; axle.position.set(0, 0.12, z)
      g.add(axle); this.detail.push(axle)
    }
    return g
  }

  setVisible(on) { this.visible = on; this.group.visible = on }

  update(t, ms, camDist) {
    if (!this.visible) return
    const near = camDist < NEAR_DIST
    for (const d of this.detail) if (d.visible !== near) d.visible = near

    // bases fade in/out by their active window
    for (const it of this.bases) {
      let target = 1
      if (it.activeFrom != null && ms < it.activeFrom) target = 0
      if (it.activeTo != null && ms > it.activeTo) target = 0.15
      it.vis += (target - it.vis) * 0.08
      it.g.visible = it.vis > 0.02
      it.g.scale.setScalar(0.6 + 0.4 * it.vis)
    }

    // beacons blink
    for (let i = 0; i < this.beacons.length; i++) {
      this.beacons[i].material.opacity = 0.35 + 0.55 * Math.abs(Math.sin(t * 2.0 + i * 1.7))
    }

    // city clusters tint to whoever holds them
    if (this.territory) {
      for (const it of this.cities) {
        const ctl = this.territory.sampleControl(it.c.lat, it.c.lon)
        const ctrl = Math.max(ctl.isis, ctl.pesh), margin = ctl.isis - ctl.pesh
        const fac = margin >= 0 ? C_ISIS : C_PESH
        const conf = Math.min(1, Math.abs(margin) / 0.45) * Math.min(1, ctrl / 0.18)
        _scratch.copy(C_NEU).lerp(fac, conf)
        for (const m of it.edgeMats) m.color.lerp(_scratch, 0.08)
      }
    }

    // raid convoys crawl from origin to objective while the offensive runs
    for (const cv of this.convoys) {
      const p = (ms - cv.start) / (cv.end - cv.start)
      const live = p > -0.08 && p < 1.15
      for (let k = 0; k < cv.trucks.length; k++) {
        const tr = cv.trucks[k]
        if (!live) { if (tr.visible) tr.visible = false; continue }
        const param = clamp01(p - k * 0.09)
        tr.visible = true
        const x = lerp(cv.a.x, cv.b.x, param)
        const z = lerp(cv.a.z, cv.b.z, param)
        // sample terrain under the truck so it rides the relief
        const lat = this._latOf(z), lon = this._lonOf(x)
        tr.position.set(x, this.terrain.yAt(lat, lon, 0) + 0.18, z)
        tr.lookAt(cv.b.x, tr.position.y, cv.b.z)
        const op = Math.min(1, Math.min(param, 1 - param) * 6 + 0.2)
        tr.scale.setScalar(0.9 + 0.1 * Math.sin(t * 8 + k))
        tr.traverse(o => { if (o.material && o.material.opacity != null && o.isLineSegments) o.material.opacity = 0.9 * op })
      }
    }
  }

  // inverse of projector x/z (only used to sample terrain height under a truck)
  _lonOf(x) { const p = this.projector; return p.centerLon + x / p.kx }
  _latOf(z) { const p = this.projector; return p.centerLat - z / p.kz }
}
