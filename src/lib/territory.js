import * as THREE from 'three'
import { parseDate, clamp01 } from './geo.js'

/* ============================================================
   Territory influence field.
   Per-town control is interpolated over time from the dataset's
   control snapshots, then splatted into a low-res RGBA texture
   (R = ISIS influence, G = anti-ISIS/Peshmerga influence) that
   the terrain shader samples to tint ground and draw the
   morphing frontline. Per-cell town weights are precomputed
   once so the per-frame update is a few thousand multiplies.
   ============================================================ */

const FIELD = 104          // texture resolution per axis
const INFLUENCE_KM = 44    // town control radius

const VALUE = { isis: 1.0, contested: 0.0, peshmerga: -1.0, iraqi: -0.62, coalition: -0.5, civilian: -0.3 }

export class Territory {
  constructor(projector, control) {
    this.projector = projector
    this.FIELD = FIELD
    this._buildTowns(control)
    this._precomputeWeights()
    this._buildTexture()
  }

  _buildTowns(control) {
    const byName = new Map()
    const snaps = (control || []).slice().sort((a, b) => parseDate(a.date) - parseDate(b.date))
    for (const snap of snaps) {
      const d = parseDate(snap.date)
      for (const tw of (snap.towns || [])) {
        const key = tw.name.toLowerCase().trim()
        if (!byName.has(key)) byName.set(key, { name: tw.name, lat: tw.lat, lon: tw.lon, pts: [] })
        const rec = byName.get(key)
        if (tw.lat != null) { rec.lat = tw.lat; rec.lon = tw.lon }
        const v = VALUE[tw.side] != null ? VALUE[tw.side] : 0
        rec.pts.push({ d, v })
      }
    }
    this.towns = [...byName.values()].filter(t => t.lat != null && t.pts.length)
    for (const t of this.towns) t.pts.sort((a, b) => a.d - b.d)
  }

  // value of a town in [-1,1] at time ms (piecewise-linear over its own history)
  _townValue(t, ms) {
    const p = t.pts
    if (ms <= p[0].d) return p[0].v
    if (ms >= p[p.length - 1].d) return p[p.length - 1].v
    for (let i = 0; i < p.length - 1; i++) {
      if (ms >= p[i].d && ms <= p[i + 1].d) {
        const f = (ms - p[i].d) / (p[i + 1].d - p[i].d || 1)
        return p[i].v + (p[i + 1].v - p[i].v) * f
      }
    }
    return p[p.length - 1].v
  }

  _precomputeWeights() {
    const p = this.projector, b = p.bounds
    const cosLat = Math.cos(p.centerLat * Math.PI / 180)
    // cell list of {ti, w} per texel
    this.cellTowns = new Array(FIELD * FIELD)
    for (let yy = 0; yy < FIELD; yy++) {
      const lat = b.south + ((yy + 0.5) / FIELD) * (b.north - b.south)
      for (let xx = 0; xx < FIELD; xx++) {
        const lon = b.west + ((xx + 0.5) / FIELD) * (b.east - b.west)
        const list = []
        for (let ti = 0; ti < this.towns.length; ti++) {
          const t = this.towns[ti]
          const dx = (lon - t.lon) * cosLat, dy = lat - t.lat
          const dKm = Math.sqrt(dx * dx + dy * dy) * 111.0
          const w = Math.exp(-Math.pow(dKm / INFLUENCE_KM, 2))
          if (w > 0.02) list.push({ ti, w })
        }
        this.cellTowns[yy * FIELD + xx] = list
      }
    }
  }

  _buildTexture() {
    this.data = new Uint8Array(FIELD * FIELD * 4)
    this.tex = new THREE.DataTexture(this.data, FIELD, FIELD, THREE.RGBAFormat)
    this.tex.minFilter = THREE.LinearFilter
    this.tex.magFilter = THREE.LinearFilter
    this.tex.wrapS = this.tex.wrapT = THREE.ClampToEdgeWrapping
    this.tex.needsUpdate = true
  }

  update(ms) {
    const vals = this.towns.map(t => this._townValue(t, ms))
    const data = this.data
    for (let c = 0; c < FIELD * FIELD; c++) {
      let isis = 0, pesh = 0
      const list = this.cellTowns[c]
      for (let k = 0; k < list.length; k++) {
        const v = vals[list[k].ti], w = list[k].w
        if (v > 0) isis += w * v
        else pesh += w * (-v)
      }
      data[c * 4] = clamp01(isis) * 255
      data[c * 4 + 1] = clamp01(pesh) * 255
      data[c * 4 + 2] = 0
      data[c * 4 + 3] = 255
    }
    this.tex.needsUpdate = true
  }
}
