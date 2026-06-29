import * as THREE from 'three'
import { fbm, distToSegmentKm, clamp01, smoothstep } from './geo.js'

/* ============================================================
   Procedural elevation terrain, grounded in real geography.
   Heightmap = regional trend (low Tigris valley -> high NE
   Zagros) + data-driven mountain ridges + fractal detail.
   Rendered with a custom shader: hillshade, topographic
   contours, a faint ops graticule, faction territory tint,
   and an animated glowing frontline seam.
   ============================================================ */

const RES = 360 // grid nodes per axis

export class Terrain {
  constructor(projector, geo) {
    this.projector = projector
    this.geo = geo
    this.RES = RES
    this.maxElevM = 1
    this._buildHeightmap()
    this._buildMesh()
  }

  _buildHeightmap() {
    const p = this.projector, b = p.bounds
    const cosLat = Math.cos(p.centerLat * Math.PI / 180)
    const mtns = (this.geo.mountains || []).map(m => {
      // build ridge segments; fall back to a single peak point
      let ridge = (m.ridge && m.ridge.length >= 2) ? m.ridge.slice() : [[m.peakLat, m.peakLon]]
      if (ridge.length === 1) ridge = [ridge[0], ridge[0]]
      return { ridge, peak: m.peakElevM || 1000, r: (m.radiusKm || 26) }
    })

    const N = RES + 1
    const elev = new Float32Array(N * N)
    let maxE = 1
    for (let i = 0; i < N; i++) {
      const lat = b.south + (i / RES) * (b.north - b.south)
      for (let j = 0; j < N; j++) {
        const lon = b.west + (j / RES) * (b.east - b.west)

        // Regional base trend: low in SW (Tigris/Jazira ~170m), rising to NE foothills.
        const ne = clamp01(((lat - b.south) / (b.north - b.south)) * 0.55 +
                           ((lon - b.west) / (b.east - b.west)) * 0.45)
        let base = 150 + ne * 520

        // Data-driven mountain ridges (use max so isolated ranges stay isolated).
        let mountain = 0
        for (const m of mtns) {
          let dmin = 1e9
          for (let k = 0; k < m.ridge.length - 1; k++) {
            const a = m.ridge[k], c = m.ridge[k + 1]
            const d = distToSegmentKm(lat, lon, a[0], a[1], c[0], c[1], cosLat)
            if (d < dmin) dmin = d
          }
          const falloff = Math.exp(-Math.pow(dmin / (m.r * 0.7), 2))
          mountain = Math.max(mountain, m.peak * falloff)
        }
        let h = Math.max(base, mountain * 0.96 + base * (1 - clamp01(mountain / 600)) * 0.0)

        // Fractal ruggedness — heavier in the mountains, gentle on the plains.
        const rough = clamp01(h / 1400)
        const n = fbm(lon * 9.0 + 11.3, lat * 9.0 + 4.1, 5) - 0.5
        const n2 = fbm(lon * 26.0, lat * 26.0, 3) - 0.5
        h += n * (120 + rough * 520) + n2 * (40 + rough * 160)
        h = Math.max(60, h)

        elev[i * N + j] = h
        if (h > maxE) maxE = h
      }
    }
    this.elev = elev
    this.N = N
    this.maxElevM = maxE
  }

  // Bilinear elevation (metres) at a lat/lon.
  elevMAt(lat, lon) {
    const b = this.projector.bounds, N = this.N
    const fx = clamp01((lon - b.west) / (b.east - b.west)) * RES
    const fy = clamp01((lat - b.south) / (b.north - b.south)) * RES
    const x0 = Math.floor(fx), y0 = Math.floor(fy)
    const x1 = Math.min(RES, x0 + 1), y1 = Math.min(RES, y0 + 1)
    const tx = fx - x0, ty = fy - y0
    const e = this.elev
    const a = e[y0 * N + x0], c = e[y0 * N + x1]
    const d = e[y1 * N + x0], f = e[y1 * N + x1]
    return (a * (1 - tx) + c * tx) * (1 - ty) + (d * (1 - tx) + f * tx) * ty
  }

  // World-space Y on the terrain surface at lat/lon (+ optional metres lift).
  yAt(lat, lon, liftM = 0) { return this.projector.y(this.elevMAt(lat, lon) + liftM) }

  _buildMesh() {
    const p = this.projector, b = p.bounds, N = this.N
    const geom = new THREE.BufferGeometry()
    const pos = new Float32Array(N * N * 3)
    const uv = new Float32Array(N * N * 2)
    const aElev = new Float32Array(N * N)
    for (let i = 0; i < N; i++) {
      const lat = b.south + (i / RES) * (b.north - b.south)
      for (let j = 0; j < N; j++) {
        const lon = b.west + (j / RES) * (b.east - b.west)
        const idx = i * N + j
        const eM = this.elev[idx]
        pos[idx * 3] = p.x(lon)
        pos[idx * 3 + 1] = p.y(eM)
        pos[idx * 3 + 2] = p.z(lat)
        uv[idx * 2] = j / RES
        uv[idx * 2 + 1] = i / RES
        aElev[idx] = eM
      }
    }
    const indices = new Uint32Array(RES * RES * 6)
    let o = 0
    for (let i = 0; i < RES; i++) {
      for (let j = 0; j < RES; j++) {
        const a = i * N + j, c = a + 1, d = a + N, f = d + 1
        // wound CCW from above so vertex normals point up (lit, not culled)
        indices[o++] = a; indices[o++] = c; indices[o++] = d
        indices[o++] = c; indices[o++] = f; indices[o++] = d
      }
    }
    geom.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    geom.setAttribute('uv', new THREE.BufferAttribute(uv, 2))
    geom.setAttribute('aElev', new THREE.BufferAttribute(aElev, 1))
    geom.setIndex(new THREE.BufferAttribute(indices, 1))
    geom.computeVertexNormals()

    this.uniforms = {
      uTime: { value: 0 },
      uTerritory: { value: null },
      uShowTerritory: { value: 1 },
      uShowContours: { value: 1 },
      uShowGrid: { value: 1 },
      uReveal: { value: 0 },
      uMaxElev: { value: this.maxElevM },
      uContourInterval: { value: 240 },
      uIsis: { value: new THREE.Color(0xff4d3d) },
      uPesh: { value: new THREE.Color(0x1fe3c6) },
      uLow: { value: new THREE.Color(0x35536d) },
      uMid: { value: new THREE.Color(0x547f9b) },
      uHigh: { value: new THREE.Color(0x8aafc5) },
      uSnow: { value: new THREE.Color(0xe8f1f7) },
      uLine: { value: new THREE.Color(0x46cfe6) },
      uSun: { value: new THREE.Vector3(-0.5, 0.82, 0.28).normalize() },
      uGridCount: { value: 26 },
    }

    const mat = new THREE.ShaderMaterial({
      uniforms: this.uniforms,
      vertexShader: TERRAIN_VERT,
      fragmentShader: TERRAIN_FRAG,
      transparent: false,
    })
    this.mesh = new THREE.Mesh(geom, mat)
    this.mesh.frustumCulled = false
    this.mesh.renderOrder = 0

    // a thin dark "sea level" base slab + edge skirt for a clean floating-relief look
    const skirtGeom = new THREE.BoxGeometry(p.worldWidth * 1.001, 3, p.worldDepth * 1.001)
    const skirtMat = new THREE.MeshBasicMaterial({ color: 0x05080c })
    this.skirt = new THREE.Mesh(skirtGeom, skirtMat)
    this.skirt.position.y = -1.6
  }

  addTo(scene) { scene.add(this.mesh); scene.add(this.skirt) }

  update(t, reveal) {
    this.uniforms.uTime.value = t
    this.uniforms.uReveal.value = reveal
  }
  setTerritoryTexture(tex) { this.uniforms.uTerritory.value = tex }
  setLayer(name, on) {
    if (name === 'territory') this.uniforms.uShowTerritory.value = on ? 1 : 0
    if (name === 'contours') this.uniforms.uShowContours.value = on ? 1 : 0
    if (name === 'grid') this.uniforms.uShowGrid.value = on ? 1 : 0
  }
}

const TERRAIN_VERT = /* glsl */`
  attribute float aElev;
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vWorld;
  varying float vElev;
  void main() {
    vUv = uv;
    vElev = aElev;
    vNormal = normalize(normalMatrix * normal);
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vWorld = wp.xyz;
    gl_Position = projectionMatrix * viewMatrix * wp;
  }
`

const TERRAIN_FRAG = /* glsl */`
  precision highp float;
  uniform float uTime, uShowTerritory, uShowContours, uShowGrid, uReveal;
  uniform float uMaxElev, uContourInterval, uGridCount;
  uniform vec3 uIsis, uPesh, uLow, uMid, uHigh, uSnow, uLine, uSun;
  uniform sampler2D uTerritory;
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vWorld;
  varying float vElev;

  void main() {
    float h = clamp(vElev / uMaxElev, 0.0, 1.0);

    // --- base elevation palette (plains lifted so the valley floor reads) ---
    vec3 col = mix(uLow, uMid, smoothstep(-0.04, 0.26, h));
    col = mix(col, uHigh, smoothstep(0.28, 0.70, h));
    col = mix(col, uSnow, smoothstep(0.80, 1.0, h));

    // --- hillshade ---
    vec3 n = normalize(vNormal);
    float diff = clamp(dot(n, normalize(uSun)) * 0.5 + 0.5, 0.0, 1.0);
    col *= mix(0.86, 1.32, diff);
    // gentle valley darkening / peak lift
    col *= mix(0.94, 1.08, smoothstep(0.0, 0.5, h));
    // self-lit ambient floor so the relief always reads against the void
    col += col * 0.1;

    // --- topographic contour lines ---
    if (uShowContours > 0.5) {
      float e = vElev / uContourInterval;
      float f = abs(fract(e - 0.5) - 0.5) / fwidth(e);
      float line = 1.0 - clamp(f - 0.6, 0.0, 1.0);
      // emphasise index contours (every 5th)
      float e5 = vElev / (uContourInterval * 5.0);
      float f5 = abs(fract(e5 - 0.5) - 0.5) / fwidth(e5);
      float line5 = 1.0 - clamp(f5 - 0.6, 0.0, 1.0);
      col += uLine * (line * 0.10 + line5 * 0.16) * (0.4 + 0.6 * h);
    }

    // --- ops graticule ---
    if (uShowGrid > 0.5) {
      vec2 g = vUv * uGridCount;
      vec2 gf = abs(fract(g - 0.5) - 0.5) / fwidth(g);
      float grid = 1.0 - min(min(gf.x, gf.y), 1.0);
      col += uLine * grid * 0.05;
    }

    // --- faction territory tint + frontline seam ---
    if (uShowTerritory > 0.5) {
      vec4 terr = texture2D(uTerritory, vUv);
      float isis = terr.r;     // ISIS influence 0..1
      float pesh = terr.g;     // anti-ISIS / Peshmerga influence 0..1
      float ctrl = max(isis, pesh);
      float margin = isis - pesh;
      // tint toward the DOMINANT side so claimed ground reads solid up to the
      // seam (translucent, so relief & contours still show through)
      vec3 fac = margin > 0.0 ? uIsis : uPesh;
      float fill = smoothstep(0.05, 0.20, ctrl);
      col = mix(col, mix(col, fac, 0.55), fill * 0.6);
      col += fac * fill * 0.045;

      // crisp glowing frontline exactly where control crosses over
      float seamW = fwidth(margin) * 1.5 + 0.012;
      float seam = (1.0 - smoothstep(0.0, seamW, abs(margin))) * smoothstep(0.12, 0.3, ctrl);
      float shimmer = 0.6 + 0.4 * sin(uTime * 2.0 + vWorld.x * 0.6 + vWorld.z * 0.6);
      vec3 seamCol = mix(uIsis, uPesh, 0.5) * 1.25 + vec3(0.16);
      col += seamCol * seam * (0.5 + 0.5 * shimmer);
    }

    // --- fresnel rim for depth (subtle; avoids a bright grazing-edge glow) ---
    vec3 viewDir = normalize(cameraPosition - vWorld);
    float fres = pow(1.0 - clamp(dot(n, viewDir), 0.0, 1.0), 4.0);
    col += uLine * fres * 0.05;

    // --- reveal wipe (south -> north) on intro ---
    float rv = smoothstep(uReveal - 0.12, uReveal + 0.02, vUv.y);
    col *= (1.0 - rv);
    float edge = smoothstep(uReveal - 0.03, uReveal, vUv.y) * (1.0 - smoothstep(uReveal, uReveal + 0.03, vUv.y));
    col += uLine * edge * 1.2;

    gl_FragColor = vec4(col, 1.0);
  }
`
