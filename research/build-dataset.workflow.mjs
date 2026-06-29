export const meta = {
  name: 'isis-peshmerga-dataset',
  description: 'Compile + adversarially verify a comprehensive ISIS-vs-Peshmerga war dataset (timeline, control, geo, stats)',
  phases: [
    { title: 'Geography' },
    { title: 'Phases', detail: '4 war-period research agents in parallel' },
    { title: 'Verify', detail: 'adversarial fact-check of critical dates & headline numbers' },
    { title: 'Curate', detail: 'monthly stat time-series + war phases' },
    { title: 'Critic', detail: 'completeness & consistency review' },
  ],
}

const ANCHORS = `Known coordinates (lat,lon) — use these exactly; geocode others via WebSearch:
Mosul 36.345,43.140 | Erbil/Hewler 36.191,44.009 | Kirkuk 35.468,44.392 | Dohuk 36.867,42.988 |
Sulaymaniyah 35.561,45.435 | Sinjar town 36.322,41.866 | Mount Sinjar peak 36.390,41.650 |
Mosul Dam 36.630,42.823 | Tal Afar 36.377,42.449 | Makhmur 35.730,43.531 | Gwer/Gwair 36.052,43.490 |
Qaraqosh(Bakhdida) 36.270,43.378 | Bartella 36.351,43.377 | Bashiqa 36.460,43.367 | Khazir/Kalak 36.40,43.49 |
Zumar 36.793,42.380 | Rabia 36.812,42.099 | Wana 36.69,42.62 | Tel Keppe 36.49,43.12 | Batnaya 36.53,43.13 |
Qayyarah 35.793,43.291 | Tikrit 34.607,43.677 | Hamdaniya 36.27,43.38 | Snune/Sinune 36.55,41.78 |
Tal Afar airbase 36.30,42.40 | Baghdad 33.31,44.36 | Qandil Mtns 36.62,45.10 | Halabja 35.18,45.99 |
Kobani(Syria) 36.89,38.35 | Raqqa(Syria) 35.95,38.99`

const SLICE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['events', 'offensives', 'controlSnapshots', 'statsPoints', 'bases', 'sources'],
  properties: {
    events: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['date','title','lat','lon','side','kind','magnitude','blurb'],
      properties: {
        date: { type: 'string', description: 'YYYY-MM-DD' },
        title: { type: 'string' }, lat: { type: 'number' }, lon: { type: 'number' },
        side: { type: 'string', description: 'isis|peshmerga|coalition|iraqi|civilian' },
        kind: { type: 'string', description: 'capture|battle|offensive|airstrike|massacre|siege|liberation|political|relief' },
        magnitude: { type: 'number', description: '1 minor .. 5 pivotal' },
        blurb: { type: 'string', description: '1-2 sentence factual summary' },
        casualties: { type: 'string' }, source: { type: 'string' } } } },
    offensives: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['date','name','side','fromLat','fromLon','toLat','toLon'],
      properties: { date: { type: 'string' }, endDate: { type: 'string' }, name: { type: 'string' },
        side: { type: 'string' }, fromLat: { type: 'number' }, fromLon: { type: 'number' },
        toLat: { type: 'number' }, toLon: { type: 'number' }, label: { type: 'string' }, outcome: { type: 'string' } } } },
    controlSnapshots: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['date','towns'], properties: { date: { type: 'string' },
        towns: { type: 'array', items: { type: 'object', additionalProperties: false,
          required: ['town','side','lat','lon'],
          properties: { town: { type: 'string' }, side: { type: 'string', description: 'isis|peshmerga|iraqi|contested' },
            lat: { type: 'number' }, lon: { type: 'number' } } } } } } },
    statsPoints: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['date'], properties: { date: { type: 'string' },
        isisKm2: { type: 'number', description: 'ISIS-controlled area in IRAQ, km2 (approx)' },
        peshFrontlineKm: { type: 'number' }, isisFighters: { type: 'number' }, peshFighters: { type: 'number' },
        coalitionStrikes: { type: 'number', description: 'cumulative coalition airstrikes in Iraq' },
        yazidiRefugees: { type: 'number' }, civiliansDisplaced: { type: 'number' } } } },
    bases: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['name','side','kind','lat','lon'],
      properties: { name: { type: 'string' }, side: { type: 'string' },
        kind: { type: 'string', description: 'command|forward_base|airbase|garrison|hq' },
        lat: { type: 'number' }, lon: { type: 'number' }, activeFrom: { type: 'string' }, activeTo: { type: 'string' } } } },
    sources: { type: 'array', items: { type: 'string' } },
  },
}

const SLICE_INTRO = `You are a military-history researcher building a precise dataset for an interactive war map of the Islamic State (ISIS/ISIL) vs the Kurdish Peshmerga in northern Iraq (2014-2017). Use ToolSearch with query "select:WebSearch,WebFetch" to load web tools, then verify dates, coordinates, place control, and numbers against reputable sources (Wikipedia campaign articles, HRW, ISW, BBC, Reuters, Rudaw). Only include well-documented facts. Return REAL latitude/longitude for every point. Be comprehensive but accurate — do not invent events or numbers. ${ANCHORS}\n\nReturn data ONLY for the period assigned below. Use YYYY-MM-DD dates. side is one of isis|peshmerga|coalition|iraqi|civilian. For controlSnapshots, give 1-2 snapshots in your period listing who controls each of the key towns (Mosul, Tal Afar, Sinjar, Sinjar Mountain, Mosul Dam, Zumar, Rabia, Qaraqosh, Bartella, Bashiqa, Tel Keppe, Gwer, Makhmur, Qayyarah, Kirkuk, Erbil, Dohuk, Tikrit) as of that date. For statsPoints give 1-3 data points for your period (approx is fine; note units). `

const slices = [
  { label: 'p1-blitz', prompt: SLICE_INTRO + `\n\nPERIOD: 1-10 June 2014 to 31 July 2014 — the ISIS blitz. Cover: fall of Mosul (10 Jun 2014), ISIS sweep down the Tigris (Tikrit), collapse of the Iraqi Army, Peshmerga moving into Kirkuk and disputed territories (~12 Jun 2014) as the Iraqi army fled, early ISIS probes toward Kirkuk and its oilfields (mid-late June), the new ~1000km Peshmerga-ISIS frontline forming. Offensives: ISIS thrust into Mosul; Peshmerga move into Kirkuk.` },
  { label: 'p2-erbil', prompt: SLICE_INTRO + `\n\nPERIOD: 1 August 2014 to 30 September 2014 — the August crisis. Cover: ISIS offensive into Kurdish-held Nineveh (Zumar, Sinjar town fall 3 Aug, Wana, Tel Keppe, Batnaya, Qaraqosh/Hamdaniya plains), the Sinjar massacre / Yazidi genocide and ~40,000-50,000 Yazidis stranded on Mount Sinjar, ISIS capture of Mosul Dam (~7 Aug) and advance to within ~40km of Erbil (Gwer, Makhmur), start of US airstrikes (8 Aug 2014), Peshmerga retaking Gwer & Makhmur (~10 Aug), recapture of Mosul Dam by Peshmerga+Iraqi SOF+US air (17-18 Aug). Offensives as arrows: ISIS thrust Sinjar; ISIS thrust toward Erbil via Gwer/Makhmur; ISIS thrust Mosul Dam; Peshmerga counter to Mosul Dam.` },
  { label: 'p3-counter', prompt: SLICE_INTRO + `\n\nPERIOD: 1 October 2014 to 31 December 2015 — the Peshmerga counteroffensives. Cover: Battle of Kirkuk continuing (to Nov 2014), Peshmerga help relieve Kobani (sent fighters via Turkey, late Oct/Nov 2014), December 2014 Sinjar offensive (17 Dec, broke siege of Mount Sinjar, ~700km2 retaken, opened corridor), Jan 2015 Peshmerga offensives north & west of Mosul and around Gwer/Makhmur, recapture of Rabia, and the Operation Free Sinjar / liberation of Sinjar town 12-13 Nov 2015 (with YPG + US air, cutting Highway 47 between Mosul and Raqqa). Offensives as arrows for each counteroffensive.` },
  { label: 'p4-mosul', prompt: SLICE_INTRO + `\n\nPERIOD: 1 January 2016 to 31 October 2017 — Mosul and the aftermath. Cover: 2016 shaping operations (Gwer/Qayyarah axis, Khazir), the Battle of Mosul launch 17 Oct 2016 with Peshmerga clearing villages east & north of Mosul (Khazir, Bashiqa taken ~7 Nov 2016, Nawaran), Iraqi CTS entering and the liberation of Mosul (declared 9-10 July 2017), the Kurdistan independence referendum (25 Sep 2017), and the Iraqi federal + PMF operation that retook Kirkuk and the disputed territories from the Peshmerga (16 Oct 2017) — a major reversal. Offensives as arrows: Peshmerga clearing east/north of Mosul; Iraqi advance into Kirkuk Oct 2017.` },
]

phase('Geography')
const GEO_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['bounds','cities','mountains','rivers','commandCenters','headlineNumbers'],
  properties: {
    bounds: { type: 'object', additionalProperties: false, required: ['west','east','south','north'],
      properties: { west: { type: 'number' }, east: { type: 'number' }, south: { type: 'number' }, north: { type: 'number' } } },
    cities: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['name','lat','lon','kind'],
      properties: { name: { type: 'string' }, lat: { type: 'number' }, lon: { type: 'number' },
        kind: { type: 'string', description: 'metro|city|town|strategic|dam|airbase' },
        elevM: { type: 'number', description: 'approx ground elevation in meters' }, note: { type: 'string' } } } },
    mountains: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['name','peakLat','peakLon','peakElevM'],
      properties: { name: { type: 'string' }, peakLat: { type: 'number' }, peakLon: { type: 'number' },
        peakElevM: { type: 'number' }, radiusKm: { type: 'number' },
        ridge: { type: 'array', items: { type: 'array', items: { type: 'number' }, description: 'lat,lon' } } } } },
    rivers: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['name','path'], properties: { name: { type: 'string' },
        path: { type: 'array', items: { type: 'array', items: { type: 'number' } } } } } },
    commandCenters: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['name','side','kind','lat','lon'],
      properties: { name: { type: 'string' }, side: { type: 'string' }, kind: { type: 'string' },
        lat: { type: 'number' }, lon: { type: 'number' }, note: { type: 'string' } } } },
    headlineNumbers: { type: 'object', additionalProperties: true,
      description: 'Key figures with brief source notes' },
  },
}
const geo = await agent(
  `You are a geographer/OSINT analyst. Build the geographic base layer for an interactive 3D war map of northern Iraq (the ISIS-vs-Peshmerga theater, 2014-2017). Use ToolSearch "select:WebSearch,WebFetch" then verify. ${ANCHORS}
Provide:
- bounds: a bounding box comfortably containing Sinjar (west), Sulaymaniyah (east), Tikrit (south), Dohuk/Zakho (north). Roughly west~41.0 east~46.0 south~34.3 north~37.4.
- cities: ~22 key places (Mosul, Erbil, Kirkuk, Dohuk, Sulaymaniyah, Sinjar, Tal Afar, Mosul Dam, Makhmur, Gwer, Qaraqosh, Bartella, Bashiqa, Tel Keppe, Zumar, Rabia, Qayyarah, Tikrit, Halabja, Zakho, Snune, Wana) with REAL lat/lon, kind, and approx ground elevation in meters (Mosul ~223m, Erbil ~390m, Sulaymaniyah ~880m, Kirkuk ~350m).
- mountains: the major ranges with peak coords + elevation + an approximate ridge polyline: Sinjar Mountains (E-W ridge ~36.35-36.40N, 41.5-42.1E, peak ~1463m), the Zagros / Kurdistan highlands along the NE (peaks 2000-3600m, incl. Qandil ~3000m+ near 36.6,45.1), Mateen/Gara mountains near Dohuk (~36.9,43.3, ~2000m), Hamrin hills SW of Kirkuk. Give each a ridge of 4-8 points.
- rivers: Tigris (Mosul->Qayyarah->Tikrit) and Great Zab (NE highlands -> joins near Kalak/Gwer).
- commandCenters: Peshmerga GHQ / Ministry of Peshmerga (Erbil), KDP & PUK sector commands, ISIS de-facto capital/HQ (Mosul) and ISIS Tal Afar stronghold, Coalition/Iraqi joint ops & airbases (Erbil airbase). Mark side and kind.
- headlineNumbers: peak ISIS area in Iraq and total Iraq+Syria, Peshmerga strength (~150,000-200,000), Kurdish-ISIS frontline length (~1000-1050 km), Yazidis stranded on Mt Sinjar (~40,000-50,000), approximate total US/coalition airstrikes in Iraq, IDPs in northern Iraq, Mosul civilian population. Add short source notes.`,
  { label: 'geo-base', phase: 'Geography', schema: GEO_SCHEMA })

phase('Phases')
const sliceResults = await parallel(slices.map(s => () =>
  agent(s.prompt, { label: s.label, phase: 'Phases', schema: SLICE_SCHEMA })))

const merged = { events: [], offensives: [], controlSnapshots: [], statsPoints: [], bases: [], sources: [] }
for (const r of sliceResults.filter(Boolean)) {
  for (const k of Object.keys(merged)) if (Array.isArray(r[k])) merged[k].push(...r[k])
}
for (const c of (geo?.commandCenters || [])) merged.bases.push({ ...c, kind: c.kind || 'command' })
log(`Merged: ${merged.events.length} events, ${merged.offensives.length} offensives, ${merged.controlSnapshots.length} control snapshots, ${merged.bases.length} bases`)

phase('Verify')
const sortedEvents = [...merged.events].sort((a,b) => (b.magnitude||0)-(a.magnitude||0))
const criticalEvents = sortedEvents.slice(0, 8)
const numberClaims = Object.entries(geo?.headlineNumbers || {}).slice(0, 6)
  .map(([k,v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
const claims = [
  ...criticalEvents.map(e => `EVENT ${e.date}: "${e.title}" at (${e.lat},${e.lon}) — ${e.blurb}`),
  ...numberClaims.map(c => `NUMBER ${c}`),
]
const VERIFY_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['claim','verdict','note'],
  properties: { claim: { type: 'string' },
    verdict: { type: 'string', description: 'confirmed|corrected|unverified' },
    correctedDate: { type: 'string' }, correctedValue: { type: 'string' },
    note: { type: 'string' }, source: { type: 'string' } },
}
const verifications = (await parallel(claims.map((c, i) => () =>
  agent(`You are an adversarial fact-checker. Independently verify this claim about the 2014-2017 ISIS-vs-Peshmerga war using ToolSearch "select:WebSearch,WebFetch" and reputable sources. Default to skepticism: if the date, place, or number is off, mark "corrected" and give the right value; if you cannot confirm, mark "unverified". Be concise.\n\nCLAIM: ${c}`,
  { label: `verify-${i+1}`, phase: 'Verify', schema: VERIFY_SCHEMA })))).filter(Boolean)
const corrections = verifications.filter(v => v.verdict !== 'confirmed')
log(`Verification: ${verifications.filter(v=>v.verdict==='confirmed').length}/${verifications.length} confirmed, ${corrections.length} flagged`)

phase('Curate')
const CURATE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['stats','phases'],
  properties: {
    stats: { type: 'array', description: 'monthly time series 2014-06 .. 2017-10',
      items: { type: 'object', additionalProperties: false,
        required: ['date','isisKm2','peshFrontlineKm','isisFighters','coalitionStrikes','yazidiRefugees','townsIsis','townsPesh'],
        properties: { date: { type: 'string', description: 'YYYY-MM-01' },
          isisKm2: { type: 'number' }, peshFrontlineKm: { type: 'number' },
          isisFighters: { type: 'number' }, peshFighters: { type: 'number' },
          coalitionStrikes: { type: 'number', description: 'cumulative' },
          yazidiRefugees: { type: 'number' }, civiliansDisplaced: { type: 'number' },
          townsIsis: { type: 'number' }, townsPesh: { type: 'number' } } } },
    phases: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['name','start','end','summary'],
      properties: { name: { type: 'string' }, start: { type: 'string' }, end: { type: 'string' },
        accent: { type: 'string', description: 'isis|peshmerga|neutral' },
        summary: { type: 'string' } } } },
  },
}
const curated = await agent(
  `You are a data editor. Produce a clean MONTHLY time-series (2014-06-01 through 2017-10-01, one point per month) of headline war metrics for the HUD counters of an ISIS-vs-Peshmerga war map, plus a list of named war phases. Base it on these researched data points and headline numbers; interpolate smoothly and keep the narrative arc correct:
- ISIS area in Iraq rises sharply Jun-Aug 2014 to a peak (~40,000 km2 in Iraq), plateaus, then declines through 2015-2017 toward near-zero by mid-2017 (Mosul fell Jul 2017).
- Peshmerga-ISIS frontline ~1000-1050 km once established (Aug 2014), slowly shrinking.
- Cumulative coalition airstrikes in Iraq climb from 0 (Aug 2014) into the many thousands by 2017.
- Yazidi refugees on/from Mt Sinjar spike Aug 2014 (~40,000-50,000), ease after the Dec 2014 corridor and Nov 2015 liberation.
- townsIsis / townsPesh: approximate count (of ~18 tracked key towns) held by each side per month.
RAW STAT POINTS: ${JSON.stringify(merged.statsPoints).slice(0, 3500)}
HEADLINE NUMBERS: ${JSON.stringify(geo?.headlineNumbers).slice(0, 1500)}
VERIFICATION CORRECTIONS: ${JSON.stringify(corrections).slice(0, 1500)}
Also define 4-6 named phases with start/end YYYY-MM-DD, accent, and a one-sentence summary.`,
  { label: 'curate-stats', phase: 'Curate', schema: CURATE_SCHEMA })

phase('Critic')
const CRITIC_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['missing','fixes','assessment'],
  properties: {
    missing: { type: 'array', items: { type: 'string' } },
    fixes: { type: 'array', items: { type: 'object', additionalProperties: false,
      required: ['issue','suggestion'], properties: { issue: { type: 'string' }, suggestion: { type: 'string' } } } },
    assessment: { type: 'string' },
  },
}
const critic = await agent(
  `You are a completeness & consistency critic for a war-map dataset (ISIS vs Peshmerga, 2014-2017). Review the assembled dataset summary below. Flag: missing pivotal events, mislabeled sides, coordinates that look wrong, control snapshots that contradict the timeline, or stat trends that defy the known arc. Keep it actionable and short.
EVENTS (${merged.events.length}): ${JSON.stringify(merged.events.map(e=>({d:e.date,t:e.title,s:e.side,k:e.kind}))).slice(0,3500)}
OFFENSIVES (${merged.offensives.length}): ${JSON.stringify(merged.offensives.map(o=>({d:o.date,n:o.name,s:o.side}))).slice(0,1500)}
CONTROL SNAPSHOTS: ${JSON.stringify(merged.controlSnapshots.map(c=>c.date))}
STATS MONTHS: ${curated?.stats?.length}
PHASES: ${JSON.stringify(curated?.phases?.map(p=>p.name))}`,
  { label: 'critic', phase: 'Critic', schema: CRITIC_SCHEMA })

return {
  geo,
  events: merged.events,
  offensives: merged.offensives,
  controlSnapshots: merged.controlSnapshots,
  bases: merged.bases,
  statsRaw: merged.statsPoints,
  stats: curated?.stats || [],
  phases: curated?.phases || [],
  sources: Array.from(new Set(merged.sources)).slice(0, 60),
  verifications,
  critic,
}
