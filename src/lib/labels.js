import * as THREE from 'three'

/* Canvas-texture sprite labels — kept in WebGL so they record cleanly
   (no DOM overlay). Crisp on hiDPI; uppercase monospace military style. */

export function makeLabel(text, opts = {}) {
  const {
    color = '#dcebf3', sub = '', size = 26, pad = 10,
    weight = 600, glow = 'rgba(120,210,235,0.0)', align = 'left',
  } = opts
  const dpr = 2
  const font = `${weight} ${size}px 'JetBrains Mono', monospace`
  const subFont = `500 ${Math.round(size * 0.62)}px 'JetBrains Mono', monospace`
  const measure = document.createElement('canvas').getContext('2d')
  measure.font = font
  const w1 = measure.measureText(text).width
  measure.font = subFont
  const w2 = sub ? measure.measureText(sub).width : 0
  const tw = Math.max(w1, w2)
  const cw = Math.ceil(tw + pad * 2)
  const ch = Math.ceil(size * (sub ? 2.05 : 1.35) + pad)

  const canvas = document.createElement('canvas')
  canvas.width = cw * dpr
  canvas.height = ch * dpr
  const ctx = canvas.getContext('2d')
  ctx.scale(dpr, dpr)
  ctx.textBaseline = 'top'
  ctx.textAlign = 'left'

  // main label
  ctx.font = font
  ctx.fillStyle = color
  ctx.shadowColor = glow
  ctx.shadowBlur = 8
  ctx.fillText(text, pad, pad * 0.4)
  ctx.shadowBlur = 0

  if (sub) {
    ctx.font = subFont
    ctx.fillStyle = 'rgba(150,175,190,0.85)'
    ctx.fillText(sub, pad, pad * 0.4 + size * 1.02)
  }

  const tex = new THREE.CanvasTexture(canvas)
  tex.minFilter = THREE.LinearFilter
  tex.magFilter = THREE.LinearFilter
  tex.anisotropy = 4
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false, depthWrite: false })
  const spr = new THREE.Sprite(mat)
  const worldH = size * 0.06
  spr.scale.set(worldH * (cw / ch), worldH, 1)
  spr.userData.aspect = cw / ch
  spr.userData.worldH = worldH
  spr.renderOrder = 30
  spr.center.set(align === 'left' ? 0.0 : 0.5, 0.5)
  return spr
}
