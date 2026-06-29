/* Normalises the research-workflow output (scratch_wf/research.json) into the
   engine's conflict.json schema: faction/kind normalisation, bounds filtering,
   deduplication, a synthesised pre-war baseline snapshot, and a Kirkuk-reversal
   phase. Run: node scripts/build-data.mjs */
import fs from 'node:fs'

const R = JSON.parse(fs.readFileSync('research/research-output.json', 'utf8'))
const B = R.geo.bounds
const inB = (lat, lon, m = 0.18) =>
  lat >= B.south - m && lat <= B.north + m && lon >= B.west - m && lon <= B.east + m

const SIDES = { isis: 1, peshmerga: 1, coalition: 1, iraqi: 1, civilian: 1, contested: 1 }
function side(s) {
  const x = String(s || '').toLowerCase()
  if (/isis|isil|islamic state|daesh/.test(x)) return 'isis'
  if (/pesh|kurd|kdp|puk|ypg|krg/.test(x)) return 'peshmerga'
  if (/coalition|u\.?s\.?|cjtf|oir/.test(x) && !/iraqi/.test(x)) return 'coalition'
  if (/coalition/.test(x)) return 'coalition'
  if (/iraqi|isf|pmf|cts|federal|hashd/.test(x)) return 'iraqi'
  if (/civil|yazidi|idp|refugee/.test(x)) return 'civilian'
  if (/contest/.test(x)) return 'contested'
  return SIDES[x] ? x : 'contested'
}
function baseKind(k) {
  const x = String(k || '').toLowerCase()
  if (/ghq|hq|capital|command|joint ops|sector/.test(x)) return 'command'
  if (/airbase|airfield|air /.test(x)) return 'airbase'
  if (/stronghold|garrison/.test(x)) return 'garrison'
  return 'forward_base'
}
const stripParen = (s) => String(s).replace(/\s*\(.*?\)\s*/g, ' ').replace(/\s+/g, ' ').trim()
const r2 = (v) => Math.round(v * 100) / 100

/* ---- cities ---- */
const cities = (R.geo.cities || [])
  .filter(c => inB(c.lat, c.lon))
  .map(c => ({ name: stripParen(c.name), lat: c.lat, lon: c.lon, kind: c.kind || 'town', elevM: c.elevM || 250, note: c.note || '' }))

/* ---- mountains / rivers ---- */
const mountains = R.geo.mountains || []
const rivers = R.geo.rivers || []

/* ---- bases (commandCenters already folded into R.bases) ---- */
const seenBase = new Set()
const bases = []
for (const b of (R.bases || [])) {
  if (!inB(b.lat, b.lon)) continue
  const key = `${r2(b.lat)},${r2(b.lon)},${side(b.side)}`
  if (seenBase.has(key)) continue
  seenBase.add(key)
  bases.push({
    name: stripParen(b.name), side: side(b.side), kind: baseKind(b.kind),
    lat: b.lat, lon: b.lon,
    activeFrom: b.activeFrom || '2014-06-01', activeTo: b.activeTo || undefined,
    note: b.note || '',
  })
}

/* ---- events (dedupe + bounds + clamp magnitude) ---- */
const seenEv = new Set()
const events = []
for (const e of (R.events || [])) {
  if (!inB(e.lat, e.lon)) continue
  const key = e.date + '|' + String(e.title).toLowerCase().slice(0, 24)
  if (seenEv.has(key)) continue
  seenEv.add(key)
  events.push({
    date: e.date, title: e.title, lat: e.lat, lon: e.lon,
    side: side(e.side), kind: e.kind || 'battle',
    magnitude: Math.max(1, Math.min(5, e.magnitude || 3)),
    blurb: e.blurb || '', casualties: e.casualties || '', source: e.source || '',
  })
}
events.sort((a, b) => a.date.localeCompare(b.date))

/* ---- offensives (convert + bounds) ---- */
const offensives = (R.offensives || [])
  .filter(o => inB(o.fromLat, o.fromLon) && inB(o.toLat, o.toLon))
  .map(o => ({
    date: o.date, endDate: o.endDate || undefined, name: o.name, side: side(o.side),
    from: [o.fromLat, o.fromLon], to: [o.toLat, o.toLon],
    label: o.label || o.name, outcome: o.outcome || '',
  }))

/* ---- control snapshots (rename town->name, normalise side) ---- */
const control = (R.controlSnapshots || [])
  .slice().sort((a, b) => a.date.localeCompare(b.date))
  .map(s => ({
    date: s.date,
    towns: (s.towns || []).map(t => ({ name: t.town || t.name, side: side(t.side), lat: t.lat, lon: t.lon })),
  }))

/* synthesise a pre-war baseline (2014-06-01) so the opening blitz visibly flips */
const KURD = new Set(['erbil', 'dohuk', 'sulaymaniyah', 'hewler'])
if (control.length) {
  const first = control[0]
  const baseline = {
    date: '2014-06-01',
    towns: first.towns.map(t => ({
      name: t.name, lat: t.lat, lon: t.lon,
      side: KURD.has(t.name.toLowerCase()) ? 'peshmerga' : 'iraqi',
    })),
  }
  control.unshift(baseline)
}

/* ---- stats (already monthly, engine keys) ---- */
const stats = (R.stats || []).map(s => ({
  date: s.date,
  isisKm2: s.isisKm2, peshFrontlineKm: s.peshFrontlineKm,
  isisFighters: s.isisFighters, peshFighters: s.peshFighters,
  coalitionStrikes: s.coalitionStrikes, yazidiRefugees: s.yazidiRefugees,
  civiliansDisplaced: s.civiliansDisplaced,
  townsIsis: s.townsIsis, townsPesh: s.townsPesh,
}))

/* ---- phases (uppercase, add Kirkuk reversal beat) ---- */
const accentOf = (a) => (['isis', 'peshmerga', 'neutral'].includes(a) ? a : 'neutral')
let phases = (R.phases || []).map(p => ({
  name: p.name.toUpperCase(), start: p.start, end: p.end, accent: accentOf(p.accent), summary: p.summary,
}))
if (phases.length) phases[phases.length - 1].end = '2017-09-30'
phases.push({
  name: 'KIRKUK REVERSAL', start: '2017-10-01', end: '2017-10-31', accent: 'neutral',
  summary: 'After the independence referendum, Iraqi federal forces and the PMF retake Kirkuk and the disputed territories from the Peshmerga.',
})

const out = {
  meta: {
    title: 'NORTHERN IRAQ — ISIS vs PESHMERGA',
    subtitle: 'Operational war map, 2014–2017',
    startDate: '2014-06-01',
    endDate: '2017-10-31',
    theater: 'Nineveh · Kurdistan Region · disputed territories',
    note: 'Reconstructed from open sources and adversarially fact-checked. Territory is an interpolated influence field, not a survey boundary.',
  },
  bounds: B,
  cities, mountains, rivers, bases, events, offensives, control, stats, phases,
}

fs.writeFileSync('src/data/conflict.json', JSON.stringify(out, null, 2))
console.log('conflict.json written:')
console.log(`  cities ${cities.length}  mountains ${mountains.length}  rivers ${rivers.length}  bases ${bases.length}`)
console.log(`  events ${events.length}  offensives ${offensives.length}  control ${control.length}  stats ${stats.length}  phases ${phases.length}`)
console.log(`  date range ${events[0]?.date} .. ${events[events.length - 1]?.date}`)
console.log(`  event sides ${[...new Set(events.map(e => e.side))].join(',')}`)
console.log(`  base kinds ${[...new Set(bases.map(b => b.kind))].join(',')} | sides ${[...new Set(bases.map(b => b.side))].join(',')}`)
