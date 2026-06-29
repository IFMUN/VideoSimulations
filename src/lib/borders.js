import * as THREE from 'three'
import { Line2 } from 'three/examples/jsm/lines/Line2.js'
import { LineGeometry } from 'three/examples/jsm/lines/LineGeometry.js'
import { LineMaterial } from 'three/examples/jsm/lines/LineMaterial.js'
import { makeLabel } from './labels.js'

/* ============================================================
   Geographic basemap: real international borders (Iraq–Syria,
   Iraq–Turkey, Iraq–Iran from Natural Earth) draped over the
   terrain as crisp screen-space lines, plus an approximate,
   clearly-flagged KRG "Green Line", and country name labels —
   so the viewer knows exactly where this theatre sits.
   Data: src/data/borders.geo.json (see scripts/build-borders.mjs).
   ============================================================ */

export class Borders {
  constructor(projector, terrain, data) {
    this.projector = projector
    this.terrain = terrain
    this.group = new THREE.Group()
    this.materials = []
    this.labels = []
    this.showLabels = true
    this.visible = true
    this._build(data)
  }

  addTo(scene) { scene.add(this.group) }

  // densify a [lat,lon] polyline and sample terrain so the line hugs the relief
  _drape(path, lift) {
    const p = this.projector, t = this.terrain
    const out = []
    const push = (la, lo) => out.push(p.x(lo), t.yAt(la, lo, 0) + lift, p.z(la))
    for (let i = 0; i < path.length - 1; i++) {
      const [la0, lo0] = path[i], [la1, lo1] = path[i + 1]
      const n = Math.max(1, Math.ceil(Math.hypot(la1 - la0, lo1 - lo0) / 0.035))
      for (let k = 0; k < n; k++) { const f = k / n; push(la0 + (la1 - la0) * f, lo0 + (lo1 - lo0) * f) }
    }
    const last = path[path.length - 1]; push(last[0], last[1])
    return out
  }

  _build(data) {
    for (const b of (data.borders || [])) {
      if (!b.path || b.path.length < 2) continue
      const intl = b.kind === 'international'
      const positions = this._drape(b.path, intl ? 0.6 : 0.95)
      const geom = new LineGeometry()
      geom.setPositions(positions)
      const mat = new LineMaterial({
        color: intl ? 0xb6c4cf : 0xe0b15a,
        linewidth: intl ? 2.0 : 1.7,                 // screen-space px
        transparent: true, opacity: intl ? 0.6 : 0.5,
        dashed: !intl, dashSize: 1.6, gapSize: 1.1,
        depthTest: true, depthWrite: false,
      })
      mat.resolution.set(window.innerWidth, window.innerHeight)
      const line = new Line2(geom, mat)
      if (!intl) line.computeLineDistances()
      line.renderOrder = 3
      line.frustumCulled = false
      this.group.add(line)
      this.materials.push(mat)
    }

    for (const c of (data.countries || [])) {
      const lab = makeLabel(c.name, { color: '#aebccb', size: 34, weight: 600, glow: 'rgba(120,170,200,0.2)', align: 'center' })
      const y = this.terrain.yAt(c.lat, c.lon, 0) + 9
      lab.position.set(this.projector.x(c.lon), y, this.projector.z(c.lat))
      this.group.add(lab)
      this.labels.push(lab)
    }
  }

  setResolution(w, h) { for (const m of this.materials) m.resolution.set(w, h) }

  setVisible(on) { this.visible = on; this.group.visible = on }

  setLabelsVisible(on) { this.showLabels = on }

  update(camDist) {
    // country labels: keep readable size, fade gently with distance, stay subtle
    const scale = Math.max(1.6, Math.min(4.5, camDist / 110))
    const op = this.showLabels ? Math.min(0.82, Math.max(0.14, camDist / 240)) : 0
    for (const l of this.labels) {
      const wh = l.userData.worldH * scale
      l.scale.set(wh * l.userData.aspect, wh, 1)
      l.material.opacity = op
      l.visible = this.showLabels
    }
  }
}
