import * as THREE from 'three'
import { sideHex, sideColor, parseDate, clamp01 } from './geo.js'
import { makeLabel } from './labels.js'

/* ============================================================
   Site markers: cities, forward bases, command centers, airbases.
   Each = faction-coded core shape + soft (faction/control tinted)
   halo + pulse/control ring + dark contrast pad + optional label.
   Cities recolour to whoever controls them at the current time,
   so ownership reads at a glance. Command centers keep a rotating
   bracket + beam to read as HQ.
   ============================================================ */

let HALO_TEX = null
function haloTexture() {
  if (HALO_TEX) return HALO_TEX
  const s = 128
  const c = document.createElement('canvas'); c.width = c.height = s
  const ctx = c.getContext('2d')
  const g = ctx.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2)
  // softer + lower-alpha than before: a faction-tinted aura, not a white blob
  g.addColorStop(0, 'rgba(255,255,255,0.70)')
  g.addColorStop(0.28, 'rgba(255,255,255,0.30)')
  g.addColorStop(0.62, 'rgba(255,255,255,0.07)')
  g.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = g; ctx.fillRect(0, 0, s, s)
  HALO_TEX = new THREE.CanvasTexture(c)
  return HALO_TEX
}

// --- control colour: tint cities by whoever holds them right now ---
const C_ISIS = new THREE.Color(0xff4d3d)
const C_PESH = new THREE.Color(0x1fe3c6)
const C_NEU = new THREE.Color(0x8a98a6)
const _scratch = new THREE.Color()
function controlColorInto(out, isis, pesh) {
  const ctrl = Math.max(isis, pesh)
  const margin = isis - pesh
  const fac = margin >= 0 ? C_ISIS : C_PESH
  const conf = Math.min(1, Math.abs(margin) / 0.45) * Math.min(1, ctrl / 0.18)
  out.copy(C_NEU).lerp(fac, conf)
  return out
}

// faction-coded core geometry: city=octahedron, base=pyramid, command=icosahedron, airbase=disc
function coreGeometry(type, r) {
  if (type === 'command') return new THREE.IcosahedronGeometry(r * 0.55, 0)
  if (type === 'base') return new THREE.ConeGeometry(r * 0.58, r * 1.25, 4)
  if (type === 'airbase') return new THREE.CylinderGeometry(r * 0.5, r * 0.5, r * 0.2, 18)
  return new THREE.OctahedronGeometry(r * 0.52, 0)
}

export class Markers {
  constructor(projector, terrain, territory = null) {
    this.projector = projector
    this.terrain = terrain
    this.territory = territory
    this.group = new THREE.Group()
    this.items = []
    this.pickables = []
    this.showLabels = true
  }
  addTo(scene) { scene.add(this.group) }

  _surface(lat, lon, lift = 0) {
    const p = this.projector
    return new THREE.Vector3(p.x(lon), this.terrain.yAt(lat, lon, 0) + lift, p.z(lat))
  }

  addCity(c) {
    const big = c.kind === 'metro'
    const strat = c.kind === 'strategic' || c.kind === 'dam'
    const r = big ? 1.5 : strat ? 1.15 : 0.85
    const item = this._buildMarker({
      lat: c.lat, lon: c.lon, hex: 0xc6d6e2, ringHex: 0x8a98a6, coreR: r,
      stem: true, beam: false, ring: big || strat, controlRing: true,
      label: c.name.toUpperCase(), sub: '', labelSize: big ? 30 : 22,
      type: 'city', alwaysOn: true,
      info: { title: c.name, meta: (c.kind || 'town').toUpperCase() + (c.elevM ? ` · ${Math.round(c.elevM)} m` : ''), blurb: c.note || '', side: 'contested' },
    })
    item.kind = c.kind
    return item
  }

  addBase(bse) {
    const isCmd = bse.kind === 'command' || bse.kind === 'hq'
    const isAir = bse.kind === 'airbase'
    const type = isCmd ? 'command' : isAir ? 'airbase' : 'base'
    const hex = sideHex(bse.side)
    const item = this._buildMarker({
      lat: bse.lat, lon: bse.lon, hex, ringHex: hex,
      coreR: isCmd ? 1.7 : isAir ? 1.3 : 1.1, stem: true, beam: isCmd, ring: true,
      bracket: isCmd, crosshair: isAir,
      label: isCmd ? bse.name.toUpperCase() : '', sub: isCmd ? (sideColor(bse.side).label) : '',
      labelSize: 22, type, side: bse.side,
      activeFrom: bse.activeFrom ? parseDate(bse.activeFrom) : null,
      activeTo: bse.activeTo ? parseDate(bse.activeTo) : null,
      info: { title: bse.name, meta: (bse.kind || 'base').toUpperCase() + ' · ' + sideColor(bse.side).label, blurb: bse.note || '', side: bse.side },
    })
    return item
  }

  _buildMarker(o) {
    const g = new THREE.Group()
    const pos = this._surface(o.lat, o.lon, 0.4)
    g.position.copy(pos)

    // dark contrast pad so a bright core reads against bright terrain tint
    const pad = new THREE.Mesh(
      new THREE.CircleGeometry(o.coreR * 1.5, 28),
      new THREE.MeshBasicMaterial({ color: 0x05080c, transparent: true, opacity: 0.5, depthWrite: false, depthTest: false })
    )
    pad.rotation.x = -Math.PI / 2
    pad.position.y = -0.34
    pad.renderOrder = 10
    g.add(pad)

    // soft halo (tint via material.color; texture only carries the falloff shape)
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({
      map: haloTexture(), color: o.hex, transparent: true, blending: THREE.AdditiveBlending,
      depthWrite: false, depthTest: false,
    }))
    const hs = o.coreR * 4.5
    halo.scale.set(hs, hs, 1)
    halo.renderOrder = 12
    g.add(halo)

    // faction-coded core
    const core = new THREE.Mesh(
      coreGeometry(o.type, o.coreR),
      new THREE.MeshBasicMaterial({ color: o.hex })
    )
    core.renderOrder = 14
    if (o.type === 'base') core.position.y = o.coreR * 0.2 // lift pyramid base off the pad
    g.add(core)

    // static control ring (cities): radius fixed, colour set per-frame by ownership
    let controlRing = null
    if (o.controlRing) {
      controlRing = new THREE.Mesh(
        new THREE.RingGeometry(o.coreR * 1.05, o.coreR * 1.28, 36),
        new THREE.MeshBasicMaterial({ color: o.ringHex, transparent: true, opacity: 0.7, side: THREE.DoubleSide, depthWrite: false, depthTest: false })
      )
      controlRing.rotation.x = -Math.PI / 2
      controlRing.position.y = -0.28
      controlRing.renderOrder = 13
      g.add(controlRing)
    }

    // airbase runway crosshair
    if (o.crosshair) {
      const ch = new THREE.LineSegments(
        crosshairGeometry(o.coreR * 1.9),
        new THREE.LineBasicMaterial({ color: o.ringHex, transparent: true, opacity: 0.7 })
      )
      ch.rotation.x = -Math.PI / 2
      ch.position.y = -0.26
      g.add(ch)
    }

    // vertical stem to ground (elevation legibility)
    if (o.stem) {
      const stem = new THREE.Mesh(
        new THREE.CylinderGeometry(0.05, 0.05, 4, 6),
        new THREE.MeshBasicMaterial({ color: o.ringHex, transparent: true, opacity: 0.3 })
      )
      stem.position.y = -2
      g.add(stem)
    }

    // expanding pulse ring (flat on surface)
    let ring = null
    if (o.ring) {
      ring = new THREE.Mesh(
        new THREE.RingGeometry(o.coreR * 1.4, o.coreR * 1.7, 40),
        new THREE.MeshBasicMaterial({ color: o.ringHex, transparent: true, opacity: 0.6, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false })
      )
      ring.rotation.x = -Math.PI / 2
      ring.position.y = -0.3
      g.add(ring)
    }

    // command beam + bracket
    let beam = null, bracket = null
    if (o.beam) {
      beam = new THREE.Mesh(
        new THREE.CylinderGeometry(0.12, 0.5, 16, 8, 1, true),
        new THREE.MeshBasicMaterial({ color: o.hex, transparent: true, opacity: 0.18, side: THREE.DoubleSide, blending: THREE.AdditiveBlending, depthWrite: false })
      )
      beam.position.y = 8
      g.add(beam)
    }
    if (o.bracket) {
      bracket = new THREE.LineSegments(
        bracketGeometry(o.coreR * 2.6),
        new THREE.LineBasicMaterial({ color: o.ringHex, transparent: true, opacity: 0.85 })
      )
      bracket.rotation.x = -Math.PI / 2
      bracket.position.y = -0.2
      g.add(bracket)
    }

    // label
    let label = null
    if (o.label) {
      label = makeLabel(o.label, {
        color: o.type === 'command' ? sideColor(o.side).css : '#dbe8f1',
        sub: o.sub, size: o.labelSize, glow: 'rgba(120,210,235,0.45)', align: 'left',
      })
      label.position.set(o.coreR * 1.6, o.coreR * 1.1, 0)
      g.add(label)
    }

    // invisible hover-pick target
    if (o.info) {
      const pick = new THREE.Mesh(
        new THREE.SphereGeometry(Math.max(2.4, o.coreR * 2.6), 8, 8),
        new THREE.MeshBasicMaterial({ visible: false })
      )
      pick.position.copy(pos)
      pick.userData.info = o.info
      this.group.add(pick)
      this.pickables.push(pick)
    }

    this.group.add(g)
    const item = {
      group: g, halo, core, ring, controlRing, beam, bracket, label,
      lat: o.lat, lon: o.lon, hex: o.hex, type: o.type, side: o.side,
      coreR: o.coreR, alwaysOn: !!o.alwaysOn,
      activeFrom: o.activeFrom, activeTo: o.activeTo,
      phase: (o.lat * 7.3 + o.lon * 3.1) % (Math.PI * 2), vis: o.alwaysOn ? 1 : 0,
    }
    this.items.push(item)
    return item
  }

  setLabelsVisible(on) {
    this.showLabels = on
    for (const it of this.items) if (it.label) it.label.visible = on && it.vis > 0.5
  }

  update(t, ms, camDist) {
    const labelScale = Math.max(1.0, Math.min(3.0, camDist / 150))
    for (const it of this.items) {
      // visibility by active window (bases/command centers)
      let target = 1
      if (!it.alwaysOn) {
        target = 1
        if (it.activeFrom != null && ms < it.activeFrom) target = 0
        if (it.activeTo != null && ms > it.activeTo) target = 0.25
      }
      it.vis += (target - it.vis) * 0.08
      it.group.visible = it.vis > 0.02
      const v = it.vis

      // cities flip toward the controlling faction's colour
      if (it.type === 'city' && this.territory) {
        const ctl = this.territory.sampleControl(it.lat, it.lon)
        controlColorInto(_scratch, ctl.isis, ctl.pesh)
        it.halo.material.color.lerp(_scratch, 0.10)
        it.core.material.color.lerp(_scratch, 0.10)
        if (it.controlRing) it.controlRing.material.color.lerp(_scratch, 0.12)
      }

      const pulse = 0.6 + 0.4 * Math.sin(t * 2.2 + it.phase)
      it.halo.material.opacity = (0.10 + 0.12 * pulse) * v
      it.core.scale.setScalar(0.85 + 0.18 * pulse)
      if (it.controlRing) it.controlRing.material.opacity = 0.7 * v
      if (it.ring) {
        const rp = (t * 0.5 + it.phase) % 1
        const s = 1 + rp * 1.7
        it.ring.scale.setScalar(s)
        it.ring.material.opacity = (1 - rp) * 0.38 * v
      }
      if (it.beam) it.beam.material.opacity = (0.1 + 0.1 * pulse) * v
      if (it.bracket) { it.bracket.rotation.z = t * 0.4; it.bracket.material.opacity = 0.85 * v }
      if (it.label) {
        it.label.visible = this.showLabels && v > 0.45
        const wh = it.label.userData.worldH * labelScale
        it.label.scale.set(wh * it.label.userData.aspect, wh, 1)
        it.label.material.opacity = v
      }
    }
  }
}

function bracketGeometry(s) {
  const h = s / 2, arm = s * 0.32
  const pts = []
  const corners = [[-h, -h], [h, -h], [h, h], [-h, h]]
  const dirs = [[[1, 0], [0, 1]], [[-1, 0], [0, 1]], [[-1, 0], [0, -1]], [[1, 0], [0, -1]]]
  for (let i = 0; i < 4; i++) {
    const [cx, cy] = corners[i]
    for (const [dx, dy] of dirs[i]) {
      pts.push(cx, 0, cy, cx + dx * arm, 0, cy + dy * arm)
    }
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
  return g
}

function crosshairGeometry(s) {
  const h = s / 2
  const pts = [-h, 0, 0, h, 0, 0, 0, 0, -h, 0, 0, h]
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
  return g
}
