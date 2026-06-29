import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { lerp, clamp } from './geo.js'

/* ============================================================
   Camera rig: OrbitControls for manual drag/zoom + a cinematic
   auto-cam that slowly orbits and eases its look-at toward the
   current hotspot. User interaction pauses the auto-cam, which
   resumes after a few idle seconds.
   ============================================================ */

const RESUME_MS = 5000

export class CameraRig {
  constructor(camera, domElement, worldRadius) {
    this.camera = camera
    this.worldRadius = worldRadius
    this.controls = new OrbitControls(camera, domElement)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.06
    this.controls.minDistance = worldRadius * 0.35
    this.controls.maxDistance = worldRadius * 2.4
    this.controls.maxPolarAngle = Math.PI * 0.49
    this.controls.minPolarAngle = Math.PI * 0.12
    this.controls.rotateSpeed = 0.6
    this.controls.zoomSpeed = 0.8
    this.controls.target.set(0, 4, 0)

    this.auto = true
    this.userActive = false
    this.lastInteract = -1e9
    this.theta = -0.7
    this.radius = worldRadius * 1.62
    this.focus = new THREE.Vector3(0, 4, 0)
    this.target = new THREE.Vector3(0, 4, 0)

    // initial cinematic framing
    this._place(this.theta, 0.8, this.radius)

    const mark = () => { this.userActive = true; this.lastInteract = performance.now() }
    this.controls.addEventListener('start', mark)
  }

  _place(theta, phi, radius) {
    const t = this.controls.target
    const x = t.x + radius * Math.sin(phi) * Math.cos(theta)
    const z = t.z + radius * Math.sin(phi) * Math.sin(theta)
    const y = t.y + radius * Math.cos(phi)
    this.camera.position.set(x, y, z)
  }

  setAuto(on) { this.auto = on; if (on) this.lastInteract = -1e9 }

  // focus: world-space hotspot to drift toward (or null); progress 0..1 across timeline
  update(dt, now, focus, progress) {
    const idle = (performance.now() - this.lastInteract) > RESUME_MS
    if (this.userActive && idle) this.userActive = false

    if (this.auto && !this.userActive) {
      // gentle orbit + a slow elevation/zoom breath
      this.theta += dt * 0.045
      const phi = 0.78 + Math.sin(now * 0.13) * 0.05
      const rad = this.worldRadius * (1.62 - 0.1 * Math.sin(now * 0.07) - progress * 0.07)

      // ease look-at toward hotspot (kept subtle so it never lurches)
      const fx = focus || new THREE.Vector3(0, 4, 0)
      this.focus.lerp(fx, 0.012)
      this.target.set(
        lerp(0, this.focus.x, 0.5),
        4 + this.focus.y * 0.25,
        lerp(0, this.focus.z, 0.5),
      )
      this.controls.target.lerp(this.target, 0.04)
      this.radius = lerp(this.radius, rad, 0.04)
      this._place(this.theta, phi, this.radius)
    }
    this.controls.update()
  }
}
