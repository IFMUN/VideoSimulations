import * as THREE from 'three'
import { sideHex, sideColor, parseDate } from './geo.js'
import { makeLabel } from './labels.js'

/* ============================================================
   Site markers: cities, forward bases, command centers.
   Each = glowing core + soft halo + pulse ring(s) + vertical
   stem/beam, plus an optional canvas label. Command centers
   get a rotating bracket + bright beam to read as HQ.
   ============================================================ */

let HALO_TEX = null
function haloTexture() {
  if (HALO_TEX) return HALO_TEX
  const s = 128
  const c = document.createElement('canvas'); c.width = c.height = s
  const ctx = c.getContext('2d')
  const g = ctx.createRadialGradient(s/2, s/2, 0, s/2, s/2, s/2)
  g.addColorStop(0, 'rgba(255,255,255,1)')
  g.addColorStop(0.25, 'rgba(255,255,255,0.55)')
  g.addColorStop(0.6, 'rgba(255,255,255,0.12)')
  g.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = g; ctx.fillRect(0, 0, s, s)
  HALO_TEX = new THREE.CanvasTexture(c)
  return HALO_TEX
}

export class Markers {
  constructor(projector, terrain) {
    this.projector = projector
    this.terrain = terrain
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
      lat: c.lat, lon: c.lon, hex: 0xe7f2f8, ringHex: 0x6fd9ef, coreR: r,
      stem: true, beam: false, ring: big || strat,
      label: c.name.toUpperCase(), sub: '', labelSize: big ? 30 : 22,
      type: 'city', alwaysOn: true,
      info: { title: c.name, meta: (c.kind || 'town').toUpperCase() + (c.elevM ? ` · ${Math.round(c.elevM)} m` : ''), blurb: c.note || '', side: 'contested' },
    })
    item.kind = c.kind
    return item
  }

  addBase(bse) {
    const isCmd = bse.kind === 'command' || bse.kind === 'hq'
    const hex = sideHex(bse.side)
    const item = this._buildMarker({
      lat: bse.lat, lon: bse.lon, hex, ringHex: hex,
      coreR: isCmd ? 1.7 : 1.1, stem: true, beam: isCmd, ring: true, bracket: isCmd,
      label: isCmd ? bse.name.toUpperCase() : '', sub: isCmd ? (sideColor(bse.side).label) : '',
      labelSize: 22, type: isCmd ? 'command' : 'base', side: bse.side,
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

    // soft halo
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({
      map: haloTexture(), color: o.hex, transparent: true, blending: THREE.AdditiveBlending,
      depthWrite: false, depthTest: false,
    }))
    const hs = o.coreR * 3.0
    halo.scale.set(hs, hs, 1)
    halo.renderOrder = 12
    g.add(halo)

    // glowing core
    const core = new THREE.Mesh(
      new THREE.SphereGeometry(o.coreR * 0.5, 16, 16),
      new THREE.MeshBasicMaterial({ color: o.hex })
    )
    core.renderOrder = 14
    g.add(core)

    // vertical stem to ground (elevation legibility)
    if (o.stem) {
      const stemH = Math.max(1.2, pos.y - this.terrain.yAt(o.lat, o.lon, 0) + 1.6)
      const stem = new THREE.Mesh(
        new THREE.CylinderGeometry(0.05, 0.05, 4, 6),
        new THREE.MeshBasicMaterial({ color: o.ringHex, transparent: true, opacity: 0.35 })
      )
      stem.position.y = -2
      g.add(stem)
    }

    // pulse ring (flat on surface)
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
        new THREE.LineBasicMaterial({ color: o.ringHex, transparent: true, opacity: 0.8 })
      )
      bracket.rotation.x = -Math.PI / 2
      bracket.position.y = -0.2
      g.add(bracket)
    }

    // label
    let label = null
    if (o.label) {
      label = makeLabel(o.label, {
        color: o.type === 'command' ? sideColor(o.side).css : '#e7f2f8',
        sub: o.sub, size: o.labelSize, glow: 'rgba(120,210,235,0.5)', align: 'left',
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
      group: g, halo, core, ring, beam, bracket, label,
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

      const pulse = 0.6 + 0.4 * Math.sin(t * 2.2 + it.phase)
      it.halo.material.opacity = (0.15 + 0.18 * pulse) * v
      it.core.scale.setScalar(0.8 + 0.22 * pulse)
      if (it.ring) {
        const rp = (t * 0.5 + it.phase) % 1
        const s = 1 + rp * 1.7
        it.ring.scale.setScalar(s)
        it.ring.material.opacity = (1 - rp) * 0.42 * v
      }
      if (it.beam) it.beam.material.opacity = (0.1 + 0.1 * pulse) * v
      if (it.bracket) { it.bracket.rotation.z = t * 0.4; it.bracket.material.opacity = 0.8 * v }
      if (it.label) {
        it.label.visible = this.showLabels && v > 0.45
        const wh = it.label.userData.worldH * labelScale
        it.label.scale.set(wh * it.label.userData.aspect, wh, 1)
        it.label.material.opacity = v
      }
      // keep halo a constant screen-ish size-ish
      const hs = it.coreR * 7
      it.halo.scale.set(hs, hs, 1)
    }
  }
}

function bracketGeometry(s) {
  const h = s / 2, arm = s * 0.32
  const pts = []
  const corners = [[-h,-h],[h,-h],[h,h],[-h,h]]
  const dirs = [[[1,0],[0,1]],[[-1,0],[0,1]],[[-1,0],[0,-1]],[[1,0],[0,-1]]]
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
