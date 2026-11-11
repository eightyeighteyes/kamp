// KAMP-776 spike (not for merge): "Thalassa" — pool-floor caustics as a WebGL
// fragment shader, to test whether a GPU engine fits the bokehEngine contract.
//
// Public surface deliberately mirrors BokehEngine (setPalette, setBg, setPointer,
// start/stop, resize, destroy, renderStatic) plus pushLevel for the audio feed.
// Differences forced by WebGL, each a spike finding:
//   * construction can FAIL (no context / blocklisted / software-only / shader
//     error) — hence the static `create` returning null;
//   * the context can be LOST at any time; recovery rebuilds every GL object;
//   * the canvas is opaque (alpha:false), so the shader paints the themed bg
//     itself, and every path that clears the buffer (resize, restore) must
//     redraw immediately or the pane shows black.

import { LevelFollower } from '../../../../shared/levelFollower'
import { packPalette } from '../../../../shared/paletteUniforms'
import { createQuad, type GlQuad } from './glQuad'
import { hexToRgb, type Palette, type RGB } from './palette'

// --- tunables ----------------------------------------------------------------
const RES_SCALE = 0.5 // same reduced backing store as bokeh; caustics are soft enough
const MAX_DPR = 2
const FRAME_MS = 33 // ~30fps cap, same as bokeh, so the cost comparison is fair
const CROSSFADE_MS = 1000
const PARALLAX_TAU = 0.25 // seconds
const STILL_TIME = 37.0 // shader clock used for the reduced-motion still frame
const RESTORE_TIMEOUT_MS = 2000 // Chromium may never restore after repeated GPU crashes
const SUBMIT_LOG_EVERY_MS = 10_000

const FRAG = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes;
uniform float uTime;
uniform vec3 uPalette[5];
uniform vec3 uBg;
uniform vec2 uPointer;
uniform float uLevel;

vec2 hash2(vec2 p) {
  p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
  return fract(sin(p) * 43758.5453);
}
float hash1(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

// Distance to the nearest cell border (F2 - F1) of a Voronoi field whose seeds
// orbit slowly: thin bright borders read as light focused by a rippled surface.
float cellEdge(vec2 p, float t) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  float d1 = 8.0;
  float d2 = 8.0;
  for (int y = -1; y <= 1; y++) {
    for (int x = -1; x <= 1; x++) {
      vec2 g = vec2(float(x), float(y));
      vec2 o = hash2(i + g);
      o = 0.5 + 0.45 * sin(t + 6.2831 * o);
      float d = length(g + o - f);
      if (d < d1) { d2 = d1; d1 = d; } else if (d < d2) { d2 = d; }
    }
  }
  return d2 - d1;
}

void main() {
  float s = min(uRes.x, uRes.y);
  vec2 uv = (gl_FragCoord.xy - 0.5 * uRes) / s;
  // Parallax: shift the sample point WITH the cursor so the pattern moves
  // opposite it (bokeh's "deeper field" read). DOM y is down, GL y is up.
  vec2 q = uv + vec2(uPointer.x, -uPointer.y) * 0.03;
  float t = uTime;
  // Slow sinusoidal warp: the water surface bending the light.
  vec2 w = q + 0.06 * vec2(sin(q.y * 3.1 + t * 0.21), sin(q.x * 2.7 - t * 0.17));
  float e1 = cellEdge(w * 3.2, t * 0.35);
  float e2 = cellEdge(w * 5.3 + 7.0, t * 0.47);
  float width = 0.07 + 0.05 * uLevel;
  float l1 = 1.0 - smoothstep(0.0, width, e1);
  float l2 = 1.0 - smoothstep(0.0, width * 0.8, e2);
  float light = (l1 * 0.6 + l2 * 0.4 + l1 * l2 * 0.6) * (0.75 + 0.5 * uLevel);

  float m = 0.5 + 0.5 * sin(uv.x * 1.3 + uv.y * 0.9 + t * 0.05);
  vec3 water = mix(uPalette[0], uPalette[1], m) * 0.30;
  vec3 tint = mix(uPalette[2], uPalette[4], 0.5 + 0.5 * sin(t * 0.07 + uv.y));
  vec3 caustic = mix(tint, vec3(1.0), 0.35) * light * 0.45;
  vec3 layer = clamp(water + caustic, 0.0, 1.0);
  // 'screen' over the themed bg, matching bokehEngine's composite.
  vec3 col = 1.0 - (1.0 - uBg) * (1.0 - layer);
  // Vignette toward bg: same 0.45..1 radius and 0.35 strength as bokeh.
  float r = length(gl_FragCoord.xy - 0.5 * uRes) / (0.5 * length(uRes));
  col = mix(col, uBg, smoothstep(0.45, 1.0, r) * 0.35);
  // Dither: 8-bit output of dark, slow gradients bands visibly without it
  // (Chromium dithers Canvas 2D gradients, so bokeh never showed this).
  col += (hash1(gl_FragCoord.xy + fract(t) * 61.0) - 0.5) / 255.0;
  gl_FragColor = vec4(col, 1.0);
}
`

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t
const smoothstep = (t: number): number => t * t * (3 - 2 * t)

interface DevHook {
  lose(): void
  restore(): void
  /** Lose, then restore after `ms` — inside the fallback timeout. */
  cycle(ms?: number): void
  /** Measurement knobs: backing-store scale (fraction of DPR) and frame cap. */
  tune(opts: { scale?: number; fps?: number }): void
}

declare global {
  interface Window {
    __kampGL?: DevHook
  }
}

const CONTEXT_ATTRS: WebGLContextAttributes = {
  alpha: false, // opaque: no transparent pixels, so no KAMP-631 base-color seam
  antialias: false,
  depth: false,
  stencil: false,
  preserveDrawingBuffer: false,
  powerPreference: 'low-power',
  // Refuse SwiftShader/blocklisted GPUs: a software-rendered fullscreen shader
  // would be far more expensive than the static fallback.
  failIfMajorPerformanceCaveat: true
}

export class ThalassaEngine {
  private quad: GlQuad | null
  private cssW = 0

  private from: RGB[]
  private to: RGB[]
  private paletteT = 1
  private readonly fallback: RGB[]
  private bg: [number, number, number] = [0x14 / 255, 0x14 / 255, 0x14 / 255]

  private pointerTargetX = 0
  private pointerTargetY = 0
  private pointerX = 0
  private pointerY = 0

  private readonly level = new LevelFollower()

  private resScale = RES_SCALE
  private frameMs = FRAME_MS

  private elapsed = 0
  private rafId: number | null = null
  private wantRunning = false
  private lastTs = 0
  private lastDraw = 0
  private restoreTimer: number | null = null

  private submitMs = 0
  private submitFrames = 0
  private submitSince = 0

  private readonly loseExt: WEBGL_lose_context | null

  /** Returns null when WebGL is unavailable or the shader fails, so the caller falls back. */
  static create(
    canvas: HTMLCanvasElement,
    initial: Palette,
    onFatal: (reason: string) => void
  ): ThalassaEngine | null {
    const gl = canvas.getContext('webgl', CONTEXT_ATTRS)
    if (!gl) {
      onFatal('getContext(webgl) returned null (blocklist, HW accel off, or software-only)')
      return null
    }
    const quad = createQuad(gl, FRAG)
    if (!quad) {
      onFatal('shader compile/link failed')
      return null
    }
    return new ThalassaEngine(canvas, gl, quad, initial, onFatal)
  }

  private constructor(
    private canvas: HTMLCanvasElement,
    private gl: WebGLRenderingContext,
    quad: GlQuad,
    initial: Palette,
    private onFatal: (reason: string) => void
  ) {
    this.quad = quad
    this.from = initial.colors
    this.to = initial.colors
    this.fallback = initial.colors
    this.loseExt = gl.getExtension('WEBGL_lose_context')
    canvas.addEventListener('webglcontextlost', this.onLost)
    canvas.addEventListener('webglcontextrestored', this.onRestored)
    window.__kampGL = {
      lose: () => this.loseExt?.loseContext(),
      restore: () => this.loseExt?.restoreContext(),
      cycle: (ms = 500) => {
        this.loseExt?.loseContext()
        window.setTimeout(() => this.loseExt?.restoreContext(), ms)
      },
      tune: ({ scale, fps }) => {
        if (scale != null && scale > 0) this.resScale = scale
        if (fps != null && fps > 0) this.frameMs = 1000 / fps
        this.resize()
        console.info(
          `[KAMP-776] tune: scale=${this.resScale} fps=${(1000 / this.frameMs).toFixed(0)} backing=${this.canvas.width}x${this.canvas.height}`
        )
      }
    }
  }

  // --- context loss ------------------------------------------------------------

  private onLost = (e: Event): void => {
    // Without preventDefault the browser never fires webglcontextrestored.
    e.preventDefault()
    console.warn('[KAMP-776] webglcontextlost')
    if (this.rafId != null) cancelAnimationFrame(this.rafId)
    this.rafId = null
    this.quad = null // every GL object from the old context is dead
    this.restoreTimer = window.setTimeout(() => {
      this.restoreTimer = null
      this.onFatal(`context not restored within ${RESTORE_TIMEOUT_MS}ms`)
    }, RESTORE_TIMEOUT_MS)
  }

  private onRestored = (): void => {
    console.warn('[KAMP-776] webglcontextrestored')
    if (this.restoreTimer != null) window.clearTimeout(this.restoreTimer)
    this.restoreTimer = null
    this.quad = createQuad(this.gl, FRAG)
    if (!this.quad) {
      this.onFatal('shader rebuild failed after context restore')
      return
    }
    this.resize() // redraws immediately, covering the reduced-motion still too
    if (this.wantRunning) this.scheduleLoop()
  }

  // --- contract ------------------------------------------------------------------

  setBg(v: string): void {
    const c = hexToRgb(v)
    if (c) this.bg = [c.r / 255, c.g / 255, c.b / 255]
    if (!this.rafId) this.renderStatic()
  }

  private currentColors(): RGB[] {
    if (this.paletteT >= 1) return this.to
    const t = smoothstep(this.paletteT)
    return this.to.map((c, i) => {
      const f = this.from[i] ?? c
      return { r: lerp(f.r, c.r, t), g: lerp(f.g, c.g, t), b: lerp(f.b, c.b, t) }
    })
  }

  setPalette(next: Palette): void {
    this.from = this.currentColors()
    this.to = next.colors
    // While stopped there is no loop to run the crossfade, so land on the new
    // colors directly. (bokehEngine leaves paletteT at 0 here, which makes its
    // reduced-motion still show the PREVIOUS track's colors — a spike finding.)
    this.paletteT = this.rafId ? 0 : 1
    if (!this.rafId) this.renderStatic()
  }

  setPointer(nx: number, ny: number): void {
    this.pointerTargetX = Math.max(-1, Math.min(1, nx))
    this.pointerTargetY = Math.max(-1, Math.min(1, ny))
  }

  /** Feed one audio.level sample, stamped at arrival (not draw) time. */
  pushLevel(db: number | null, nowMs: number): void {
    this.level.push(db, nowMs)
  }

  resize(): void {
    const rect = this.canvas.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR)
    const scale = dpr * this.resScale
    this.cssW = rect.width
    // Assigning width/height clears the drawing buffer (to opaque BLACK with
    // alpha:false), so always repaint straight after — running or not.
    this.canvas.width = Math.max(1, Math.round(rect.width * scale))
    this.canvas.height = Math.max(1, Math.round(rect.height * scale))
    if (this.rafId) this.drawFrame(this.elapsed, this.levelNow())
    else this.renderStatic()
  }

  start(): void {
    this.wantRunning = true
    if (this.rafId != null || !this.quad) return // lost: onRestored resumes
    this.resetPointer()
    this.scheduleLoop()
  }

  stop(): void {
    this.wantRunning = false
    if (this.rafId != null) cancelAnimationFrame(this.rafId)
    this.rafId = null
  }

  destroy(): void {
    this.stop()
    if (this.restoreTimer != null) window.clearTimeout(this.restoreTimer)
    this.canvas.removeEventListener('webglcontextlost', this.onLost)
    this.canvas.removeEventListener('webglcontextrestored', this.onRestored)
    this.quad?.dispose()
    this.quad = null
    // Deliberately NOT loseContext() here: StrictMode (dev) re-runs the mount
    // effect on the same <canvas>, and getContext would hand back the lost
    // context. One engine is mounted at a time, so GC reclaiming it is fine.
    if (window.__kampGL) delete window.__kampGL
  }

  renderStatic(): void {
    this.drawFrame(STILL_TIME, 0, true)
  }

  // --- loop ------------------------------------------------------------------------

  private resetPointer(): void {
    this.pointerTargetX = this.pointerTargetY = this.pointerX = this.pointerY = 0
  }

  private scheduleLoop(): void {
    this.lastTs = 0
    this.lastDraw = 0
    this.rafId = requestAnimationFrame(this.tick)
  }

  private levelNow(): number {
    return this.level.valueAt(performance.now())
  }

  private tick = (ts: number): void => {
    this.rafId = requestAnimationFrame(this.tick)
    if (this.lastTs === 0) this.lastTs = ts
    const dt = Math.min(0.1, (ts - this.lastTs) / 1000)
    this.lastTs = ts
    if (document.hidden) return
    this.elapsed += dt
    if (this.paletteT < 1) this.paletteT = Math.min(1, this.paletteT + (dt * 1000) / CROSSFADE_MS)
    const k = 1 - Math.exp(-dt / PARALLAX_TAU)
    this.pointerX += (this.pointerTargetX - this.pointerX) * k
    this.pointerY += (this.pointerTargetY - this.pointerY) * k
    if (ts - this.lastDraw < this.frameMs) return
    this.lastDraw = ts
    this.drawFrame(this.elapsed, this.levelNow())
  }

  private drawFrame(time: number, level: number, still = false): void {
    const gl = this.gl
    const q = this.quad
    if (!q || this.cssW === 0 || gl.isContextLost()) return
    const t0 = performance.now()
    gl.viewport(0, 0, this.canvas.width, this.canvas.height)
    gl.useProgram(q.program)
    gl.uniform2f(q.u('uRes'), this.canvas.width, this.canvas.height)
    gl.uniform1f(q.u('uTime'), time)
    gl.uniform3fv(q.u('uPalette[0]'), packPalette(this.currentColors(), this.fallback))
    gl.uniform3f(q.u('uBg'), this.bg[0], this.bg[1], this.bg[2])
    gl.uniform2f(q.u('uPointer'), still ? 0 : this.pointerX, still ? 0 : this.pointerY)
    gl.uniform1f(q.u('uLevel'), level)
    q.draw()
    if (!still) this.logSubmit(performance.now() - t0)
  }

  // Main-thread SUBMIT time only. WebGL calls are queued for the GPU process, so
  // this says nothing about GPU cost — use powermetrics / Task Manager for that.
  private logSubmit(ms: number): void {
    const now = performance.now()
    if (this.submitSince === 0) this.submitSince = now
    this.submitMs += ms
    this.submitFrames++
    if (now - this.submitSince < SUBMIT_LOG_EVERY_MS) return
    console.info(
      `[KAMP-776] thalassa main-thread submit: ${(this.submitMs / this.submitFrames).toFixed(3)}ms avg over ${this.submitFrames} frames (${this.canvas.width}x${this.canvas.height} backing)`
    )
    this.submitMs = 0
    this.submitFrames = 0
    this.submitSince = now
  }
}
