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
const DAY = 86400000
const EVENT_RAD_KM = INFLUENCE_KM * 0.5   // local ripple radius for a battle
const EVENT_RAMP = 12 * DAY               // front flexes in over ~12 days...
const EVENT_DECAY = 40 * DAY              // ...then settles back over ~40 days

const VALUE = { isis: 1.0, contested: 0.0, peshmerga: -1.0, iraqi: -0.62, coalition: -0.5, civilian: -0.3 }

// A sourced event implies a local, transient pressure on the front. Sign +1 pushes
// ISIS influence, -1 pushes anti-ISIS; amplitude scales with magnitude. The bump is
// transient (ramps in, decays to zero) so it adds motion/texture WITHOUT inventing a
// lasting territorial outcome beyond the verified control snapshots. Returns null for
// events with no territorial meaning (political, relief, massacre in already-held ground).
function eventNudge(e) {
  const k = e.kind, s = e.side
  const mag = e.magnitude || 2
  const amp = 0.12 + 0.06 * (mag - 2)       // ~0.12 .. 0.30
  const isisGain = (k === 'capture' || k === 'offensive' || k === 'battle' || k === 'siege')
  const antiGain = (k === 'liberation' || k === 'capture' || k === 'offensive' || k === 'battle')
  if (s === 'isis' && isisGain) return { sign: 1, amp }
  if ((s === 'peshmerga' || s === 'iraqi' || s === 'coalition') && antiGain) return { sign: -1, amp }
  if (k === 'airstrike') return { sign: -1, amp: amp * 0.6 }
  return null
}

export class Territory {
  constructor(projector, control, events = null) {
    this.projector = projector
    this.FIELD = FIELD
    this._buildTowns(control)
    this._precomputeWeights()
    this._buildEventSources(events)
    this._buildTexture()
  }

  // Precompute per-event affected cells (Gaussian, small radius) so the per-frame
  // bump is a few hundred multiplies for the handful of currently-active events.
  _buildEventSources(events) {
    this.eventSources = []
    if (!events) return
    const p = this.projector, b = p.bounds
    const cosLat = Math.cos(p.centerLat * Math.PI / 180)
    for (const e of events) {
      const nd = eventNudge(e)
      if (!nd || e.lat == null) continue
      const ems = parseDate(e.date)
      const cells = []
      for (let yy = 0; yy < FIELD; yy++) {
        const lat = b.south + ((yy + 0.5) / FIELD) * (b.north - b.south)
        for (let xx = 0; xx < FIELD; xx++) {
          const lon = b.west + ((xx + 0.5) / FIELD) * (b.east - b.west)
          const dx = (lon - e.lon) * cosLat, dy = lat - e.lat
          const dKm = Math.sqrt(dx * dx + dy * dy) * 111.0
          const w = Math.exp(-Math.pow(dKm / EVENT_RAD_KM, 2))
          if (w > 0.05) cells.push({ c: yy * FIELD + xx, w })
        }
      }
      this.eventSources.push({ ems, sign: nd.sign, amp: nd.amp, cells })
    }
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
    this._isisArr = new Float32Array(FIELD * FIELD)
    this._peshArr = new Float32Array(FIELD * FIELD)
    this.tex = new THREE.DataTexture(this.data, FIELD, FIELD, THREE.RGBAFormat)
    this.tex.minFilter = THREE.LinearFilter
    this.tex.magFilter = THREE.LinearFilter
    this.tex.wrapS = this.tex.wrapT = THREE.ClampToEdgeWrapping
    this.tex.needsUpdate = true
  }

  update(ms) {
    // The field changes at most on a daily granularity (control snapshots are daily at
    // finest; event ripples ramp over 12-40 days), so skip the full 104x104 recompute +
    // 43KB texture re-upload on frames that stay within the same in-sim day. Pure
    // function of ms -> deterministic and frame-exact for capture.
    const dayKey = Math.floor(ms / DAY)
    if (dayKey === this._lastDayKey) return
    this._lastDayKey = dayKey

    const vals = this.towns.map(t => this._townValue(t, ms))
    const data = this.data
    const isisArr = this._isisArr, peshArr = this._peshArr
    const N = FIELD * FIELD

    // base influence field from interpolated town control
    for (let c = 0; c < N; c++) {
      let isis = 0, pesh = 0
      const list = this.cellTowns[c]
      for (let k = 0; k < list.length; k++) {
        const v = vals[list[k].ti], w = list[k].w
        if (v > 0) isis += w * v
        else pesh += w * (-v)
      }
      isisArr[c] = isis
      peshArr[c] = pesh
    }

    // transient local ripples from currently-active sourced events
    for (const ev of this.eventSources) {
      const d = ms - ev.ems
      let env = 0
      if (d >= -EVENT_RAMP && d <= EVENT_DECAY) env = d < 0 ? (d + EVENT_RAMP) / EVENT_RAMP : 1 - d / EVENT_DECAY
      if (env <= 0.001) continue
      const a = env * ev.amp
      const cells = ev.cells
      if (ev.sign > 0) { for (let i = 0; i < cells.length; i++) isisArr[cells[i].c] += a * cells[i].w } else { for (let i = 0; i < cells.length; i++) peshArr[cells[i].c] += a * cells[i].w }
    }

    // write the RGBA field
    for (let c = 0; c < N; c++) {
      data[c * 4] = clamp01(isisArr[c]) * 255
      data[c * 4 + 1] = clamp01(peshArr[c]) * 255
      data[c * 4 + 2] = 0
      data[c * 4 + 3] = 255
    }
    this.tex.needsUpdate = true
  }

  // Current control at a lat/lon -> { isis, pesh } in [0,1]. Call after update(ms);
  // markers use this to recolour cities by whoever holds them right now.
  sampleControl(lat, lon) {
    const b = this.projector.bounds
    const fx = clamp01((lon - b.west) / (b.east - b.west))
    const fy = clamp01((lat - b.south) / (b.north - b.south))
    const xx = Math.min(FIELD - 1, Math.floor(fx * FIELD))
    const yy = Math.min(FIELD - 1, Math.floor(fy * FIELD))
    const c = (yy * FIELD + xx) * 4
    return { isis: this.data[c] / 255, pesh: this.data[c + 1] / 255 }
  }
}
