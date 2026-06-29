import * as THREE from 'three'
import { sideHex, parseDate, clamp01 } from './geo.js'

/* ============================================================
   Offensive arrows = lifted bezier "attack vectors" rendered as
   glowing tubes that draw progressively as the timeline reaches
   the operation, with a flowing energy pattern and a moving
   arrowhead. Old arrows fade to faint ghosts.
   ============================================================ */

const DRAW_DAYS = 22      // default time (days) an arrow takes to draw if no endDate
const GHOST_DAYS = 95     // after this many days past completion, fade to ghost
const DAY = 86400000

export class Arrows {
  constructor(projector, terrain, offensives) {
    this.projector = projector
    this.terrain = terrain
    this.group = new THREE.Group()
    this.arrows = []
    this.visible = true
    for (const o of (offensives || [])) this._build(o)
  }
  addTo(scene) { scene.add(this.group) }

  _surf(lat, lon, lift) {
    const p = this.projector
    return new THREE.Vector3(p.x(lon), this.terrain.yAt(lat, lon, 0) + lift, p.z(lat))
  }

  _build(o) {
    const hex = sideHex(o.side)
    const a = this._surf(o.from[0], o.from[1], 1.0)
    const b = this._surf(o.to[0], o.to[1], 1.0)
    const dist = a.distanceTo(b)
    const lift = Math.min(26, 7 + dist * 0.28)
    const mid = a.clone().add(b).multiplyScalar(0.5)
    mid.y = Math.max(a.y, b.y) + lift
    // slight lateral bow so parallel arrows separate
    const side = new THREE.Vector3().subVectors(b, a).normalize().cross(new THREE.Vector3(0, 1, 0))
    mid.addScaledVector(side, ((o.from[1] + o.to[0]) % 1 - 0.5) * dist * 0.12)

    const curve = new THREE.QuadraticBezierCurve3(a, mid, b)
    const geom = new THREE.TubeGeometry(curve, 72, 0.42, 10, false)
    const uniforms = {
      uColor: { value: new THREE.Color(hex) },
      uProgress: { value: 0 },
      uTime: { value: 0 },
      uOpacity: { value: 0 },
    }
    const mat = new THREE.ShaderMaterial({
      uniforms, transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending,
      vertexShader: ARROW_VERT, fragmentShader: ARROW_FRAG,
    })
    const tube = new THREE.Mesh(geom, mat)
    tube.frustumCulled = false
    tube.renderOrder = 20
    this.group.add(tube)

    // arrowhead
    const head = new THREE.Mesh(
      new THREE.ConeGeometry(1.5, 4.2, 18),
      new THREE.MeshBasicMaterial({ color: hex, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending })
    )
    head.renderOrder = 21
    this.group.add(head)

    // origin pip
    const pip = new THREE.Mesh(
      new THREE.SphereGeometry(0.7, 12, 12),
      new THREE.MeshBasicMaterial({ color: hex, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending })
    )
    pip.position.copy(a)
    this.group.add(pip)

    const start = parseDate(o.date)
    const end = o.endDate ? parseDate(o.endDate) : start + DRAW_DAYS * DAY
    this.arrows.push({ o, curve, tube, head, pip, uniforms, start, end: Math.max(end, start + 4 * DAY), hex })
  }

  setVisible(on) { this.visible = on; this.group.visible = on }

  update(t, ms) {
    if (!this.visible) return
    for (const ar of this.arrows) {
      const progress = clamp01((ms - ar.start) / (ar.end - ar.start))
      ar.uniforms.uProgress.value = progress
      ar.uniforms.uTime.value = t

      // opacity envelope: fade in as it starts, ghost out long after completion
      let op = 0
      if (ms >= ar.start - 3 * DAY) {
        const ageDays = (ms - ar.end) / DAY
        op = 1
        if (ms < ar.start) op = clamp01((ms - (ar.start - 3 * DAY)) / (3 * DAY))
        else if (ageDays > 0) op = 1 - 0.78 * clamp01(ageDays / GHOST_DAYS)
      }
      ar.uniforms.uOpacity.value = op
      ar.tube.visible = op > 0.01

      // arrowhead rides the drawing tip
      if (progress > 0.02 && op > 0.01) {
        const p = ar.curve.getPointAt(progress)
        const tan = ar.curve.getTangentAt(progress)
        ar.head.position.copy(p)
        ar.head.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), tan)
        ar.head.material.opacity = op * (0.6 + 0.4 * Math.sin(t * 6))
        ar.head.scale.setScalar(0.7 + 0.3 * progress)
        ar.head.visible = true
      } else ar.head.visible = false

      ar.pip.material.opacity = op * 0.7
      ar.pip.visible = op > 0.01
    }
  }
}

const ARROW_VERT = /* glsl */`
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`
const ARROW_FRAG = /* glsl */`
  precision highp float;
  uniform vec3 uColor;
  uniform float uProgress, uTime, uOpacity;
  varying vec2 vUv;
  void main() {
    if (vUv.x > uProgress) discard;
    // cross-tube falloff -> bright core, soft edges
    float edge = 1.0 - abs(vUv.y - 0.5) * 2.0;
    edge = pow(clamp(edge, 0.0, 1.0), 1.5);
    // flowing energy bands along the length
    float flow = 0.55 + 0.45 * sin(vUv.x * 46.0 - uTime * 7.0);
    // bright head near the drawing tip
    float head = smoothstep(uProgress - 0.06, uProgress, vUv.x);
    float b = (0.45 + 0.55 * flow) * edge + head * 0.9;
    vec3 col = uColor * (0.7 + 1.1 * b) + vec3(head * 0.6);
    gl_FragColor = vec4(col, clamp(b, 0.0, 1.0) * uOpacity);
  }
`
