/* Build src/data/borders.geo.json from Natural Earth (public domain).
   Pulls ne_10m_admin_0_boundary_lines_land, keeps the international borders that
   touch the Northern-Iraq theatre bbox (Iraq–Turkey / Iraq–Iran / Iraq–Syria),
   clips them to the bbox, simplifies (Douglas–Peucker) and writes [lat,lon] paths
   matching the rivers/mountains convention used elsewhere in conflict.json.

   Also appends an explicitly APPROXIMATE, disputed KRG "Green Line" (no settled
   survey exists; rendered dashed + labelled as approximate) and indicative country
   label anchors. International border geometry is authoritative; the Green Line and
   label anchors are schematic — see research/SOURCES.md.

   Usage: node scripts/build-borders.mjs
   Net: shells out to `curl` so the environment's HTTPS proxy is honoured. */
import { writeFileSync, mkdtempSync, readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const SRC = process.env.NE_URL ||
  'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_boundary_lines_land.geojson'
const OUT = process.env.OUT || new URL('../src/data/borders.geo.json', import.meta.url).pathname
const BBOX = { w: 41, e: 46, s: 34.3, n: 37.4 }   // matches conflict.json bounds

// --- fetch via curl (uses HTTPS proxy) ---
const tmp = join(mkdtempSync(join(tmpdir(), 'ne-')), 'ne.geojson')
console.log('downloading', SRC)
const r = spawnSync('curl', ['-sS', '--max-time', '120', '-o', tmp, SRC], { stdio: 'inherit' })
if (r.status !== 0) { console.error('curl failed', r.status); process.exit(1) }
const gj = JSON.parse(readFileSync(tmp, 'utf8'))

// --- bbox segment clipping (Liang–Barsky per segment, accumulate contiguous runs) ---
function clipToBox(line) {
  const out = []
  let cur = []
  const push = (lon, lat) => { const last = cur[cur.length - 1]; if (!last || last[0] !== lon || last[1] !== lat) cur.push([lon, lat]) }
  for (let i = 0; i < line.length - 1; i++) {
    const a = line[i], b = line[i + 1]
    const seg = clipSeg(a[0], a[1], b[0], b[1])
    if (!seg) { if (cur.length > 1) out.push(cur); cur = []; continue }
    push(seg[0], seg[1]); push(seg[2], seg[3])
    if (seg[4]) { if (cur.length > 1) out.push(cur); cur = [] } // segment left the box at b
  }
  if (cur.length > 1) out.push(cur)
  return out
}
function clipSeg(x0, y0, x1, y1) {
  let t0 = 0, t1 = 1
  const dx = x1 - x0, dy = y1 - y0
  const p = [-dx, dx, -dy, dy]
  const q = [x0 - BBOX.w, BBOX.e - x0, y0 - BBOX.s, BBOX.n - y0]
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) { if (q[i] < 0) return null }
    else { const t = q[i] / p[i]; if (p[i] < 0) { if (t > t1) return null; if (t > t0) t0 = t } else { if (t < t0) return null; if (t < t1) t1 = t } }
  }
  return [x0 + t0 * dx, y0 + t0 * dy, x0 + t1 * dx, y0 + t1 * dy, t1 < 1]
}

// --- Douglas–Peucker on [lon,lat] (planar; fine at this scale) ---
function simplify(pts, tol = 0.008) {
  if (pts.length < 3) return pts
  const keep = new Array(pts.length).fill(false)
  keep[0] = keep[pts.length - 1] = true
  const stack = [[0, pts.length - 1]]
  while (stack.length) {
    const [s, e] = stack.pop()
    let dmax = 0, idx = -1
    for (let i = s + 1; i < e; i++) {
      const d = perp(pts[i], pts[s], pts[e])
      if (d > dmax) { dmax = d; idx = i }
    }
    if (dmax > tol && idx > 0) { keep[idx] = true; stack.push([s, idx], [idx, e]) }
  }
  return pts.filter((_, i) => keep[i])
}
function perp(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1]
  const L = Math.hypot(dx, dy)
  // degenerate baseline (e.g. a closed ring whose endpoints coincide): fall back to
  // point distance so DP keeps the farthest vertex instead of collapsing the ring.
  if (L < 1e-12) return Math.hypot(p[0] - a[0], p[1] - a[1])
  return Math.abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / L
}

// stitch sub-segments that share endpoints into continuous polylines
function stitch(pieces) {
  const eps = 1e-6, eq = (a, b) => Math.abs(a[0] - b[0]) < eps && Math.abs(a[1] - b[1]) < eps
  const segs = pieces.map(p => p.slice()), used = new Array(pieces.length).fill(false), result = []
  for (let i = 0; i < segs.length; i++) {
    if (used[i]) continue
    used[i] = true
    let line = segs[i].slice(), extended = true
    while (extended) {
      extended = false
      for (let j = 0; j < segs.length; j++) {
        if (used[j]) continue
        const s = segs[j], head = line[0], tail = line[line.length - 1]
        if (eq(tail, s[0])) { line = line.concat(s.slice(1)); used[j] = true; extended = true }
        else if (eq(tail, s[s.length - 1])) { line = line.concat(s.slice().reverse().slice(1)); used[j] = true; extended = true }
        else if (eq(head, s[s.length - 1])) { line = s.slice(0, -1).concat(line); used[j] = true; extended = true }
        else if (eq(head, s[0])) { line = s.slice().reverse().slice(0, -1).concat(line); used[j] = true; extended = true }
      }
    }
    result.push(line)
  }
  return result
}

const borders = []
const want = new Set(['Turkey', 'Iran', 'Syria'])
const byNeighbor = new Map()
for (const f of gj.features) {
  const P = f.properties
  if (P.ADM0_LEFT !== 'Iraq' && P.ADM0_RIGHT !== 'Iraq') continue
  const other = P.ADM0_LEFT === 'Iraq' ? P.ADM0_RIGHT : P.ADM0_LEFT
  if (!want.has(other)) continue
  const lines = f.geometry.type === 'LineString' ? [f.geometry.coordinates] : f.geometry.coordinates
  if (!byNeighbor.has(other)) byNeighbor.set(other, [])
  for (const line of lines) for (const piece of clipToBox(line)) byNeighbor.get(other).push(piece)
}
for (const [other, pieces] of byNeighbor) {
  for (const line of stitch(pieces)) {
    const simp = simplify(line)
    if (simp.length < 2) continue
    borders.push({
      name: `Iraq–${other}`, kind: 'international', status: 'official',
      path: simp.map(([lon, lat]) => [+lat.toFixed(4), +lon.toFixed(4)]),
    })
  }
}

// Approximate, DISPUTED KRG "Green Line" (2003 admin boundary of the Kurdistan
// Region vs disputed territories). No settled survey line exists; this is a coarse
// schematic from open depictions (ISW / International Crisis Group) — see SOURCES.md.
borders.push({
  name: 'Green Line', kind: 'disputed', status: 'approximate',
  path: [
    [37.10, 42.36], [36.85, 42.55], [36.70, 42.95], [36.50, 43.35],
    [36.30, 43.55], [35.95, 43.62], [35.62, 43.78], [35.45, 44.05],
    [35.20, 44.45], [34.95, 44.85], [34.55, 45.25], [34.30, 45.50],
  ],
})

const out = {
  meta: {
    source: 'Natural Earth 10m admin-0 boundary lines (public domain, naturalearthdata.com)',
    note: 'International borders clipped to the theatre bbox & simplified. Green Line is an APPROXIMATE, disputed schematic (not a survey boundary). Country label anchors are indicative.',
    bbox: BBOX,
  },
  borders,
  countries: [
    { name: 'IRAQ', lat: 35.4, lon: 43.2 },
    { name: 'SYRIA', lat: 36.6, lon: 41.25 },
    { name: 'TURKEY', lat: 37.32, lon: 43.4 },
    { name: 'IRAN', lat: 35.2, lon: 45.85 },
  ],
}
writeFileSync(OUT, JSON.stringify(out, null, 2))
console.log(`wrote ${OUT}: ${borders.length} border paths (` +
  borders.map(b => `${b.name}:${b.path.length}`).join(', ') + ')')
