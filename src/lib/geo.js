import * as THREE from 'three'

/* ============================================================
   Geographic projection: lat/lon -> world XZ, elevation -> Y.
   A single Projector instance is shared by every layer so the
   terrain, markers, arrows and territory field all register.
   ============================================================ */

const DEG = Math.PI / 180
const TARGET_SPAN = 240          // world units across the map's larger axis
const VERTICAL_EXAGGERATION = 14  // makes real elevation legible at this scale

export class Projector {
  constructor(bounds) {
    this.bounds = bounds
    this.centerLat = (bounds.north + bounds.south) / 2
    this.centerLon = (bounds.east + bounds.west) / 2
    const cosLat = Math.cos(this.centerLat * DEG)

    // degree spans in "latitude-equivalent" degrees (lon corrected by cos lat)
    const latSpan = bounds.north - bounds.south
    const lonSpanEq = (bounds.east - bounds.west) * cosLat
    const maxSpan = Math.max(latSpan, lonSpanEq)

    this.scale = TARGET_SPAN / maxSpan          // world units per degree latitude
    this.kx = this.scale * cosLat               // units per degree longitude
    this.kz = this.scale
    this.unitsPerKm = this.scale / 111.0
    this.heightUnitsPerMeter = (this.unitsPerKm / 1000) * VERTICAL_EXAGGERATION

    this.worldWidth = (bounds.east - bounds.west) * this.kx
    this.worldDepth = (bounds.north - bounds.south) * this.kz
  }

  // North maps to -Z (so a top-down camera looking toward +Z shows north up).
  x(lon) { return (lon - this.centerLon) * this.kx }
  z(lat) { return -(lat - this.centerLat) * this.kz }
  y(elevM) { return (elevM || 0) * this.heightUnitsPerMeter }

  // Normalised [0,1] UV across the map plane (u: west->east, v: south->north)
  u(lon) { return (lon - this.bounds.west) / (this.bounds.east - this.bounds.west) }
  v(lat) { return (lat - this.bounds.south) / (this.bounds.north - this.bounds.south) }

  vec(lat, lon, elevM = 0) { return new THREE.Vector3(this.x(lon), this.y(elevM), this.z(lat)) }
}

/* ---------- Faction palette (shared by WebGL + DOM) ---------- */
export const SIDE = {
  isis:       { hex: 0xff4d3d, css: '#ff4d3d', glow: '#ff6a52', label: 'ISLAMIC STATE' },
  peshmerga:  { hex: 0x1fe3c6, css: '#1fe3c6', glow: '#5dffe6', label: 'PESHMERGA' },
  coalition:  { hex: 0xffd166, css: '#ffd166', glow: '#ffe39a', label: 'COALITION / US AIR' },
  iraqi:      { hex: 0xc7b25a, css: '#c7b25a', glow: '#e6d27e', label: 'IRAQI FORCES' },
  civilian:   { hex: 0xb98cff, css: '#b98cff', glow: '#d4b6ff', label: 'CIVILIAN / YAZIDI' },
  contested:  { hex: 0x9aa6b2, css: '#9aa6b2', glow: '#c2ccd6', label: 'CONTESTED' },
  neutral:    { hex: 0x9aa6b2, css: '#9aa6b2', glow: '#c2ccd6', label: 'NEUTRAL' },
}
export const sideColor = (s) => (SIDE[s] || SIDE.neutral)
export const sideHex = (s) => (SIDE[s] || SIDE.neutral).hex

/* ---------- Date helpers (app/browser context: Date is allowed) ---------- */
export const parseDate = (s) => {
  if (s instanceof Date) return s.getTime()
  return Date.parse(typeof s === 'string' && s.length === 7 ? s + '-01' : s)
}
export const fmtDate = (ms) => {
  const d = new Date(ms)
  const M = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
  return `${String(d.getUTCDate()).padStart(2,'0')} ${M[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}
export const fmtMonth = (ms) => {
  const d = new Date(ms)
  const M = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
  return `${M[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(2)}`
}
export const lerp = (a, b, t) => a + (b - a) * t
export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))
export const clamp01 = (v) => Math.min(1, Math.max(0, v))
export const smoothstep = (e0, e1, x) => { const t = clamp01((x - e0) / (e1 - e0)); return t * t * (3 - 2 * t) }
export const easeInOut = (t) => t < 0.5 ? 2*t*t : 1 - Math.pow(-2*t+2, 2)/2

/* Deterministic value-noise (no Math.random — stable across runs/records). */
function hash2(x, y) {
  let h = x * 374761393 + y * 668265263
  h = (h ^ (h >> 13)) * 1274126177
  h = h ^ (h >> 16)
  return ((h >>> 0) % 100000) / 100000
}
function valueNoise(x, y) {
  const xi = Math.floor(x), yi = Math.floor(y)
  const xf = x - xi, yf = y - yi
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf)
  const a = hash2(xi, yi), b = hash2(xi + 1, yi)
  const c = hash2(xi, yi + 1), d = hash2(xi + 1, yi + 1)
  return lerp(lerp(a, b, u), lerp(c, d, u), v)
}
export function fbm(x, y, octaves = 5) {
  let amp = 0.5, freq = 1, sum = 0, norm = 0
  for (let i = 0; i < octaves; i++) {
    sum += amp * valueNoise(x * freq, y * freq)
    norm += amp; amp *= 0.5; freq *= 2.03
  }
  return sum / norm
}

// distance (km) from point P to segment A-B, all in lat/lon (approx planar w/ cos correction)
export function distToSegmentKm(plat, plon, alat, alon, blat, blon, cosLat) {
  const px = plon * cosLat, py = plat
  const ax = alon * cosLat, ay = alat
  const bx = blon * cosLat, by = blat
  const dx = bx - ax, dy = by - ay
  const len2 = dx * dx + dy * dy || 1e-9
  let t = ((px - ax) * dx + (py - ay) * dy) / len2
  t = clamp01(t)
  const cx = ax + t * dx, cy = ay + t * dy
  const ddx = px - cx, ddy = py - cy
  return Math.sqrt(ddx * ddx + ddy * ddy) * 111.0
}
