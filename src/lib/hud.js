import { SIDE, sideColor, parseDate, fmtDate, fmtMonth, lerp, clamp01 } from './geo.js'

/* ============================================================
   DOM HUD: ticking counters, legend, layer toggles, the
   event-tick timeline (phase bands + seek), live ops feed,
   phase brief and transport controls.
   ============================================================ */

const COUNTERS = [
  { key: 'isisKm2',         label: 'IS TERRITORY',  unit: 'km²', side: 'isis' },
  { key: 'peshFrontlineKm', label: 'FRONTLINE',     unit: 'km',  side: 'peshmerga' },
  { key: 'coalitionStrikes',label: 'COALITION AIR', unit: '',    side: 'coalition' },
  { key: 'yazidiRefugees',  label: 'YAZIDI DISPLACED', unit: '', side: 'civilian', opt: true },
  { key: 'townsIsis',       label: 'KEY TOWNS · IS', unit: '',   side: 'isis', opt: true },
  { key: 'townsPesh',       label: 'KEY TOWNS · PESH', unit: '', side: 'peshmerga' },
]

const fmtNum = (v) => Math.round(v).toLocaleString('en-US')

export class HUD {
  constructor(data, handlers) {
    this.data = data
    this.h = handlers
    this.startMs = parseDate(data.meta.startDate)
    this.endMs = parseDate(data.meta.endDate)
    this.span = this.endMs - this.startMs
    this.display = {}     // tweened counter values
    this.lastFeedKey = ''
    this._buildCounters()
    this._buildLegend()
    this._buildLayers()
    this._buildTimeline()
    this._buildTransport()
    this._bindSeek()
  }

  _el(id) { return document.getElementById(id) }

  _buildCounters() {
    const wrap = this._el('counters')
    wrap.innerHTML = ''
    this.counterEls = {}
    for (const c of COUNTERS) {
      const cell = document.createElement('div')
      cell.className = 'cell' + (c.opt ? ' opt' : '')
      cell.style.setProperty('--cell-accent', sideColor(c.side).css)
      cell.innerHTML = `<div class="c-label"><span>${c.label}</span><span class="trend flat" data-tr></span></div>
        <div class="c-val"><span data-v>0</span><span class="c-unit">${c.unit}</span></div>`
      wrap.appendChild(cell)
      this.counterEls[c.key] = { v: cell.querySelector('[data-v]'), tr: cell.querySelector('[data-tr]') }
      this.display[c.key] = 0
    }
  }

  _buildLegend() {
    const body = this._el('legendBody')
    const sides = [
      ['isis', 'box'], ['peshmerga', 'box'], ['coalition', 'box'], ['iraqi', 'box'], ['civilian', 'box'],
    ]
    let html = '<div class="lg-cap">FACTIONS</div>'
    for (const [s] of sides) {
      html += `<div class="legend-row"><span class="legend-swatch"><i style="width:12px;height:12px;background:${SIDE[s].css};box-shadow:0 0 8px ${SIDE[s].glow}"></i></span>${SIDE[s].label}</div>`
    }
    html += '<div class="lg-sep"></div><div class="lg-cap">MARKERS</div>'
    html += `<div class="legend-row"><span class="legend-swatch"><i style="width:8px;height:8px;border-radius:50%;background:#e7f2f8"></i></span>CITY / TOWN</div>`
    html += `<div class="legend-row"><span class="legend-swatch"><i style="width:12px;height:12px;border:1px solid #1fe3c6"></i></span>COMMAND CENTRE</div>`
    html += `<div class="legend-row"><span class="legend-swatch"><i style="width:0;height:0;border-left:6px solid transparent;border-right:6px solid transparent;border-bottom:11px solid #ff4d3d"></i></span>OFFENSIVE AXIS</div>`
    html += `<div class="legend-row"><span class="legend-swatch"><i style="width:12px;height:12px;border-radius:50%;border:1px solid #ffe39a"></i></span>STRIKE / EVENT</div>`
    body.innerHTML = html
  }

  _buildLayers() {
    const body = this._el('layerBody')
    this.layers = { territory: true, arrows: true, events: true, labels: true, contours: true }
    const defs = [
      ['territory', 'TERRITORY FIELD'], ['arrows', 'OFFENSIVE AXES'], ['events', 'EVENT PINGS'],
      ['labels', 'PLACE LABELS'], ['contours', 'CONTOURS'],
    ]
    body.innerHTML = ''
    for (const [key, label] of defs) {
      const row = document.createElement('div')
      row.className = 'layer-row interactive'
      row.innerHTML = `<span>${label}</span><span class="sw"></span>`
      row.addEventListener('click', () => {
        this.layers[key] = !this.layers[key]
        row.classList.toggle('off', !this.layers[key])
        this.h.onLayer?.(key, this.layers[key])
      })
      body.appendChild(row)
    }
  }

  _buildTimeline() {
    // phase bands
    const ph = this._el('tlPhases'); ph.innerHTML = ''
    for (const p of (this.data.phases || [])) {
      const s = clamp01((parseDate(p.start) - this.startMs) / this.span)
      const e = clamp01((parseDate(p.end) - this.startMs) / this.span)
      const div = document.createElement('div')
      div.className = 'tl-phase ' + (p.accent || 'neutral')
      div.style.width = ((e - s) * 100) + '%'
      div.innerHTML = `<span>${p.name}</span>`
      ph.appendChild(div)
    }
    // event ticks
    const ticks = this._el('tlTicks'); ticks.innerHTML = ''
    for (const ev of (this.data.events || [])) {
      const f = clamp01((parseDate(ev.date) - this.startMs) / this.span)
      const t = document.createElement('div')
      const mag = ev.magnitude || 2
      t.className = 'tl-tick' + (mag >= 5 ? ' mag5' : mag >= 4 ? ' mag4' : '')
      t.style.left = (f * 100) + '%'
      t.style.setProperty('--tk', sideColor(ev.side).css)
      ticks.appendChild(t)
    }
    // axis year labels
    const axis = this._el('tlAxis'); axis.innerHTML = ''
    const y0 = new Date(this.startMs).getUTCFullYear(), y1 = new Date(this.endMs).getUTCFullYear()
    for (let y = y0; y <= y1; y++) {
      const span = document.createElement('span'); span.textContent = y
      axis.appendChild(span)
    }
  }

  _buildTransport() {
    this.playBtn = this._el('playBtn')
    this.playBtn.addEventListener('click', () => this.h.onPlayToggle?.())
    this._el('restartBtn').addEventListener('click', () => this.h.onRestart?.())
    this.orbitBtn = this._el('orbitBtn')
    this.orbitBtn.classList.add('active')
    this.orbitBtn.addEventListener('click', () => {
      const on = !this.orbitBtn.classList.contains('active')
      this.orbitBtn.classList.toggle('active', on)
      this.h.onAutoCam?.(on)
    })
    const sc = this._el('speedCtl'); sc.innerHTML = ''
    this.speedEls = []
    for (const sp of [0.5, 1, 2, 4]) {
      const b = document.createElement('div')
      b.className = 'sp interactive' + (sp === 1 ? ' active' : '')
      b.textContent = sp + '×'
      b.addEventListener('click', () => {
        this.speedEls.forEach(x => x.classList.remove('active'))
        b.classList.add('active')
        this.h.onSpeed?.(sp)
      })
      sc.appendChild(b); this.speedEls.push(b)
    }
  }

  _bindSeek() {
    const track = this._el('tlTrack')
    const seek = (clientX) => {
      const r = track.getBoundingClientRect()
      this.h.onSeek?.(clamp01((clientX - r.left) / r.width))
    }
    let dragging = false
    track.style.pointerEvents = 'auto'
    track.addEventListener('pointerdown', (e) => { dragging = true; track.setPointerCapture(e.pointerId); seek(e.clientX) })
    track.addEventListener('pointermove', (e) => { if (dragging) seek(e.clientX) })
    track.addEventListener('pointerup', () => { dragging = false })
  }

  setPlaying(on) { this.playBtn.textContent = on ? '❚❚' : '▶' }

  update(ms, stats, phase, progress, feedEvents) {
    // date + phase
    this._el('dateReadout').textContent = fmtDate(ms)
    this._el('phaseTag').textContent = phase ? phase.name : '—'
    this._el('phaseSummary').textContent = phase ? phase.summary : '—'

    // counters (tween + trend)
    for (const c of COUNTERS) {
      const target = stats[c.key] != null ? stats[c.key] : 0
      const prev = this.display[c.key]
      this.display[c.key] = lerp(prev, target, 0.18)
      const el = this.counterEls[c.key]
      el.v.textContent = fmtNum(this.display[c.key])
      const dv = target - prev
      el.tr.className = 'trend ' + (dv > 0.5 ? 'up' : dv < -0.5 ? 'down' : 'flat')
      el.tr.textContent = dv > 0.5 ? '▲' : dv < -0.5 ? '▼' : '■'
    }

    // playhead + fill
    this._el('tlHead').style.left = (progress * 100) + '%'
    this._el('tlFill').style.width = (progress * 100) + '%'

    // feed
    const key = feedEvents.map(e => e.date + e.title).join('|')
    if (key !== this.lastFeedKey) {
      this.lastFeedKey = key
      const body = this._el('feedBody')
      body.innerHTML = ''
      for (const e of feedEvents.slice(0, 7)) {
        const item = document.createElement('div')
        item.className = 'feed-item'
        item.style.setProperty('--side', sideColor(e.side).css)
        item.innerHTML = `<div class="fi-date">${fmtDate(parseDate(e.date))} · <span class="fi-tag">${(e.kind||'').toUpperCase()}</span></div>
          <div class="fi-title">${e.title}</div>
          <div class="fi-blurb">${e.blurb || ''}</div>`
        body.appendChild(item)
      }
      if (!feedEvents.length) body.innerHTML = '<div class="fi-blurb" style="color:var(--ink-faint)">// no active operations in window</div>'
    }
  }
}
