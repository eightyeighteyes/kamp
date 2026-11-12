// KAMP-780: the shared WebGL engine for Now Playing backgrounds. A background
// supplies only a ShaderSpec (its fragment shader, its GPU budget, any GL
// resources it owns, and its per-frame uniforms); everything else lives here.
//
// Public surface mirrors BokehEngine (setPalette, setBg, setPointer, start/stop,
// resize, destroy, renderStatic) plus pushLevel/setAccent. Differences WebGL
// forces (KAMP-776 findings):
//   * construction can FAIL (no context, blocklisted or software-only GPU, shader
//     error), hence `create` returning null;
//   * the context can be LOST at any time; restore rebuilds every GL object, and
//     no restore within RESTORE_TIMEOUT_MS is fatal (Chromium can block WebGL for
//     good after repeated GPU crashes);
//   * the canvas is opaque (alpha:false, so no KAMP-631 compositor seam), so the
//     shader paints the themed bg itself, and every path that clears the buffer
//     (resize, restore) must redraw immediately or the pane shows black.

import { LevelFollower } from '../../../../../shared/levelFollower'
import { PaletteCrossfade } from '../../../../../shared/paletteCrossfade'
import { PALETTE_SLOTS, packPalette } from '../../../../../shared/paletteUniforms'
import { accentPalette, hexToRgb, type Palette, type RGB } from '../palette'
import { createQuad, type GlQuad } from './glQuad'
import type { GLDevTarget } from './devHooks'

export interface FrameInfo {
  /** Seconds of animation since this engine's previous draw. */
  dt: number
  /** Animation clock in seconds (the value uploaded as uTime). */
  elapsed: number
  /** Delayed, smoothed audio level, 0..1. */
  level: number
  /** A one-off static frame (stopped, or reduced-motion), not a loop tick. */
  still: boolean
  /**
   * KAMP-774: the reduced-motion still, the background's designed rest frame.
   * Narrower than `still`, which is also true for a palette or theme repaint
   * while the pane is merely paused (those should show the paused state).
   */
  atRest: boolean
  /** First loop frame after start(): drop anything in flight from before the stop. */
  resumed: boolean
  /**
   * Increments once per real track change while the loop runs. Never on mount,
   * a null transition, or while stopped, so a delight moment keyed to it can't
   * fire on opening the pane or replay late on resume.
   */
  trackSeq: number
  /** The art (and title) are shown (KAMP-769), eased 0..1 over ~0.6 s. */
  artShown: number
  /** The cursor is over the pane, eased 0..1. uPointer alone can't say: it recentres on leave. */
  pointerActive: number
  /** Seconds since the last pointer movement or leave, for idle-gated moments. */
  pointerIdle: number
  /**
   * KAMP-765: the last real cursor position over the pane, -1..1 (DOM y-down),
   * un-eased and never recentred on leave. uPointer slides back to the centre
   * faster than pointerActive fades, so a cursor effect anchored on it would
   * drift toward the middle as it fades out; anchor such effects here instead.
   */
  pointerAt: { x: number; y: number }
  /**
   * The title's text box in the canvas, normalised 0..1 with y down (DOM), or
   * null when hidden. For keeping busy detail out from behind the title. It is
   * the text extent, not the full-width title element.
   */
  titleRect: ContentRect | null
}

export interface ContentRect {
  x0: number
  y0: number
  x1: number
  y1: number
}

/**
 * One shader background. `R` is whatever GL resources the background owns
 * (textures, extra buffers, extension handles).
 *
 * GL resources die with the context, so they are created in `onContext`, which
 * runs at construction AND after every context restore. Keep any CPU-side source
 * data (a decoded bitmap, a lookup table) on the spec side so it can be uploaded
 * again. Returning null from `onContext` is fatal, same as a shader that fails to
 * compile: the wrapper swaps to the static fallback.
 */
export interface ShaderSpec<R = void, S = void> {
  /** Used in log lines. */
  name: string
  /** Full fragment source; start from glsl.ts (GLSL_HEADER + ...). */
  frag: string
  /** Backing-store scale as a fraction of the (capped) DPR. Slow, soft
   * backgrounds should stay at the default quarter resolution (KAMP-776 budget). */
  resScale?: number
  /** Frame cap. Cost is linear in pixels x frames, so this is a budget knob. */
  fps?: number
  onContext?(gl: WebGLRenderingContext, quad: GlQuad): R | null
  /** Free what onContext made. Not called on context loss (those objects are already dead). */
  dispose?(gl: WebGLRenderingContext, res: R): void
  /**
   * KAMP-774: per-engine CPU state (envelopes, schedulers). Created once per
   * mount, so it survives a context restore (a burn in flight carries on), and is
   * never shared between mounts even though the spec itself is a module constant.
   */
  createState?(): S
  /** Upload background-specific uniforms; the program is already bound. Put
   * delight-moment logic here, driven by `frame`. */
  setUniforms?(gl: WebGLRenderingContext, quad: GlQuad, res: R, frame: FrameInfo, state: S): void
}

export interface ShaderEngineOptions {
  /** prefers-reduced-motion: static frames use a fixed, pleasant clock value. */
  reducedMotion: boolean
  /** Called once when the engine can no longer render (wrapper falls back). */
  onFatal: (reason: string) => void
}

// --- tunables ----------------------------------------------------------------
const DEFAULT_RES_SCALE = 0.25
const DEFAULT_FPS = 30
const MAX_DPR = 2
// rAF timestamps jitter by a fraction of a millisecond. Without slack, a 30fps
// cap (33.3ms) against a 60Hz display's 33.2ms two-frame gap skips a third frame
// and actually runs at 20fps, which would also skew every measurement.
const FRAME_SLACK_MS = 1.5
const CROSSFADE_MS = 1000
const PARALLAX_TAU = 0.25 // seconds
const PRESENCE_TAU = 0.6 // seconds; pointer presence and art visibility ease this slowly
const STILL_TIME = 37.0 // clock for the reduced-motion still frame
const RESTORE_TIMEOUT_MS = 2000

const CONTEXT_ATTRS: WebGLContextAttributes = {
  alpha: false,
  antialias: false,
  depth: false,
  stencil: false,
  preserveDrawingBuffer: false,
  powerPreference: 'low-power',
  // Refuse SwiftShader/blocklisted GPUs: a software-rendered fullscreen shader
  // would cost far more than the static fallback.
  failIfMajorPerformanceCaveat: true
}

const DEFAULT_BG: [number, number, number] = [0x14 / 255, 0x14 / 255, 0x14 / 255]

export class ShaderEngine<R = void, S = void> {
  private quad: GlQuad | null = null
  private readonly state: S
  // Boxed so an R of `undefined` (no onContext) still reads as "built".
  private res: { value: R } | null = null
  private loseExt: WEBGL_lose_context | null = null
  private destroyed = false

  private readonly crossfade: PaletteCrossfade
  private fallback: RGB[]
  private readonly paletteBuf = new Float32Array(PALETTE_SLOTS * 3)
  private bg = DEFAULT_BG

  private pointerTargetX = 0
  private pointerTargetY = 0
  private pointerX = 0
  private pointerY = 0
  private pointerTargetActive = 0
  private pointerActive = 0
  private lastPointerAt = 0 // elapsed at the last pointer event
  // KAMP-765: the last real cursor position, never recentred (see FrameInfo.pointerAt).
  private pointerAtX = 0
  private pointerAtY = 0

  private trackSeq = 0
  private artVisible = true
  private artShown = 1
  private titleRect: ContentRect | null = null
  private resumed = false

  private readonly level = new LevelFollower()

  private resScale: number
  private frameMs: number

  private elapsed = 0
  private lastDrawElapsed = 0
  private rafId: number | null = null
  private wantRunning = false
  private lastTs = 0
  private lastDraw = 0
  private restoreTimer: number | null = null

  private draws = 0
  private drawsSince = performance.now()

  /**
   * Returns null when WebGL is unavailable or the background can't be built,
   * after reporting the reason through `opts.onFatal`.
   */
  static create<R, S>(
    canvas: HTMLCanvasElement,
    spec: ShaderSpec<R, S>,
    initial: Palette,
    opts: ShaderEngineOptions
  ): ShaderEngine<R, S> | null {
    const gl = canvas.getContext('webgl', CONTEXT_ATTRS)
    if (!gl) {
      opts.onFatal('getContext(webgl) returned null (blocklist, HW accel off, or software-only)')
      return null
    }
    const engine = new ShaderEngine(canvas, gl, spec, initial, opts)
    if (!engine.build()) {
      engine.destroy()
      opts.onFatal('shader build failed')
      return null
    }
    return engine
  }

  private constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly gl: WebGLRenderingContext,
    private readonly spec: ShaderSpec<R, S>,
    initial: Palette,
    private readonly opts: ShaderEngineOptions
  ) {
    this.state = spec.createState ? spec.createState() : (undefined as S)
    this.crossfade = new PaletteCrossfade(initial.colors, CROSSFADE_MS)
    this.fallback = initial.colors
    this.resScale = spec.resScale ?? DEFAULT_RES_SCALE
    this.frameMs = 1000 / (spec.fps ?? DEFAULT_FPS)
    canvas.addEventListener('webglcontextlost', this.onLost)
    canvas.addEventListener('webglcontextrestored', this.onRestored)
  }

  // --- GL object lifecycle ---------------------------------------------------------

  /** (Re)build every GL object for the current context. False = unusable. */
  private build(): boolean {
    const quad = createQuad(this.gl, this.spec.frag)
    if (!quad) return false
    let value: R
    if (this.spec.onContext) {
      const r = this.spec.onContext(this.gl, quad)
      if (r === null) {
        quad.dispose()
        return false
      }
      value = r
    } else {
      value = undefined as R
    }
    this.quad = quad
    this.res = { value }
    // Extension objects belong to a context generation; re-fetch after a restore.
    this.loseExt = this.gl.getExtension('WEBGL_lose_context')
    return true
  }

  private onLost = (e: Event): void => {
    // Without preventDefault Chromium never fires webglcontextrestored.
    e.preventDefault()
    if (this.destroyed) return
    console.warn(`[KAMP-780] ${this.spec.name}: webglcontextlost`)
    if (this.rafId != null) cancelAnimationFrame(this.rafId)
    this.rafId = null // wantRunning is kept, so restore resumes the loop
    // Every GL object from the old context is already dead; just drop them.
    this.quad = null
    this.res = null
    this.restoreTimer = window.setTimeout(() => {
      this.restoreTimer = null
      this.opts.onFatal(`context not restored within ${RESTORE_TIMEOUT_MS}ms`)
    }, RESTORE_TIMEOUT_MS)
  }

  private onRestored = (): void => {
    if (this.destroyed) return
    console.warn(`[KAMP-780] ${this.spec.name}: webglcontextrestored`)
    if (this.restoreTimer != null) window.clearTimeout(this.restoreTimer)
    this.restoreTimer = null
    if (!this.build()) {
      this.opts.onFatal('shader rebuild failed after context restore')
      return
    }
    // The restored buffer is black; force a repaint even though the size is unchanged.
    this.resize(true)
    if (this.wantRunning) this.scheduleLoop()
  }

  // --- contract ----------------------------------------------------------------------

  setBg(hex: string): void {
    const c = hexToRgb(hex)
    if (c) this.bg = [c.r / 255, c.g / 255, c.b / 255]
    if (!this.running) this.renderStatic()
  }

  /** Theme accent, for the fallback palette when a cover yields no colors. */
  setAccent(hex: string): void {
    this.fallback = accentPalette(hex).colors
  }

  setPalette(next: Palette): void {
    // While stopped nothing advances the fade, so land on the new colors directly.
    this.crossfade.set(next.colors, this.running)
    if (!this.running) this.renderStatic()
  }

  setPointer(nx: number, ny: number): void {
    this.pointerTargetX = Math.max(-1, Math.min(1, nx))
    this.pointerTargetY = Math.max(-1, Math.min(1, ny))
    this.pointerAtX = this.pointerTargetX
    this.pointerAtY = this.pointerTargetY
    this.pointerTargetActive = 1
    this.lastPointerAt = this.elapsed
  }

  /** The cursor left the pane: recentre the parallax and let presence fade out. */
  pointerLeave(): void {
    this.pointerTargetX = this.pointerTargetY = 0
    this.pointerTargetActive = 0
    this.lastPointerAt = this.elapsed
  }

  /**
   * The current track changed (id to a different id). Counted only while the
   * loop runs: a change seen while stopped must not replay as a late moment.
   */
  trackChanged(): void {
    if (this.running) this.trackSeq++
  }

  /** KAMP-769 art toggle. Eased while running; lands directly while stopped. */
  setArtVisible(visible: boolean): void {
    this.artVisible = visible
    if (!this.running) {
      this.artShown = visible ? 1 : 0
      this.renderStatic()
    }
  }

  /** The title's text box (normalised, DOM y-down), or null when hidden. */
  setTitleRect(rect: ContentRect | null): void {
    this.titleRect = rect
    if (!this.running) this.renderStatic()
  }

  /** Feed one audio.level sample, stamped at arrival (not draw) time. */
  pushLevel(db: number | null, nowMs: number): void {
    this.level.push(db, nowMs)
  }

  /**
   * Size the backing store to the element. Assigning width/height clears the
   * buffer to opaque black, so it is skipped when nothing changed (every
   * ResizeObserver tick and activation would otherwise clear and redraw) and
   * always followed by a repaint when it does happen. `force` repaints anyway.
   */
  resize(force = false): void {
    const rect = this.canvas.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return // display:none; RO fires again later
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR)
    const scale = dpr * this.resScale
    const w = Math.max(1, Math.round(rect.width * scale))
    const h = Math.max(1, Math.round(rect.height * scale))
    if (!force && w === this.canvas.width && h === this.canvas.height) return
    this.canvas.width = w
    this.canvas.height = h
    if (this.running) this.drawFrame(this.elapsed, this.levelNow(), false)
    else this.renderStatic()
  }

  start(): void {
    this.wantRunning = true
    if (this.running || !this.quad) return // lost: onRestored resumes
    this.pointerTargetX = this.pointerTargetY = this.pointerX = this.pointerY = 0
    // Presence restarts from "away": the cursor is only over the pane once it moves.
    this.pointerTargetActive = this.pointerActive = 0
    this.lastPointerAt = this.elapsed
    this.artShown = this.artVisible ? 1 : 0
    this.resumed = true
    this.scheduleLoop()
  }

  stop(): void {
    this.wantRunning = false
    if (this.rafId != null) cancelAnimationFrame(this.rafId)
    this.rafId = null
  }

  destroy(): void {
    this.destroyed = true
    this.stop()
    if (this.restoreTimer != null) window.clearTimeout(this.restoreTimer)
    this.restoreTimer = null
    this.canvas.removeEventListener('webglcontextlost', this.onLost)
    this.canvas.removeEventListener('webglcontextrestored', this.onRestored)
    if (this.res && this.spec.dispose) this.spec.dispose(this.gl, this.res.value)
    this.quad?.dispose()
    this.quad = null
    this.res = null
    // Deliberately NOT loseContext(): StrictMode re-runs the mount effect on the
    // same <canvas>, and getContext would hand back the lost context. One engine is
    // mounted at a time, so letting GC reclaim the context is fine.
  }

  /**
   * Paint one frame without the loop. Reduced-motion gets a fixed clock and a
   * centred pointer; otherwise the current clock is kept, so a palette or bg
   * change while hidden doesn't make the animation jump when it resumes.
   */
  renderStatic(): void {
    const time = this.opts.reducedMotion ? STILL_TIME : this.elapsed
    this.drawFrame(time, 0, true)
  }

  /** The handle window.__kampGL drives (see devHooks.ts). */
  devTarget(): GLDevTarget {
    return {
      lose: () => this.loseExt?.loseContext(),
      restore: () => this.loseExt?.restoreContext(),
      tune: ({ scale, fps }) => {
        if (scale != null && scale > 0) this.resScale = scale
        if (fps != null && fps > 0) this.frameMs = 1000 / fps
        this.resize(true)
        console.info(
          `[KAMP-780] ${this.spec.name} tune: scale=${this.resScale} fps=${(1000 / this.frameMs).toFixed(0)} backing=${this.canvas.width}x${this.canvas.height}`
        )
      },
      stats: () => {
        const now = performance.now()
        const fps = (this.draws * 1000) / Math.max(1, now - this.drawsSince)
        this.draws = 0
        this.drawsSince = now
        return {
          fps: Math.round(fps * 10) / 10,
          backing: `${this.canvas.width}x${this.canvas.height}`
        }
      }
    }
  }

  // --- loop ------------------------------------------------------------------------

  private get running(): boolean {
    return this.rafId != null
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
    this.crossfade.tick(dt * 1000)
    const k = 1 - Math.exp(-dt / PARALLAX_TAU)
    this.pointerX += (this.pointerTargetX - this.pointerX) * k
    this.pointerY += (this.pointerTargetY - this.pointerY) * k
    const kp = 1 - Math.exp(-dt / PRESENCE_TAU)
    this.pointerActive += (this.pointerTargetActive - this.pointerActive) * kp
    this.artShown += ((this.artVisible ? 1 : 0) - this.artShown) * kp
    if (ts - this.lastDraw < this.frameMs - FRAME_SLACK_MS) return
    this.lastDraw = ts
    this.drawFrame(this.elapsed, this.levelNow(), false)
  }

  private drawFrame(time: number, level: number, still: boolean): void {
    const gl = this.gl
    const q = this.quad
    const res = this.res
    if (!q || !res || gl.isContextLost()) return
    const centred = still && this.opts.reducedMotion
    gl.viewport(0, 0, this.canvas.width, this.canvas.height)
    gl.useProgram(q.program)
    gl.uniform2f(q.u('uRes'), this.canvas.width, this.canvas.height)
    gl.uniform1f(q.u('uTime'), time)
    gl.uniform3fv(
      q.u('uPalette[0]'),
      packPalette(this.crossfade.current(), this.fallback, this.paletteBuf)
    )
    gl.uniform3f(q.u('uBg'), this.bg[0], this.bg[1], this.bg[2])
    gl.uniform2f(q.u('uPointer'), centred ? 0 : this.pointerX, centred ? 0 : this.pointerY)
    const pointerActive = centred ? 0 : this.pointerActive
    gl.uniform1f(q.u('uPointerActive'), pointerActive)
    gl.uniform1f(q.u('uLevel'), level)
    // A still frame is not a step of the animation (its clock may even be the
    // fixed STILL_TIME), so it reports no elapsed time to delight-moment logic.
    const frame: FrameInfo = {
      dt: still ? 0 : time - this.lastDrawElapsed,
      elapsed: time,
      level,
      still,
      atRest: centred,
      resumed: !still && this.resumed,
      trackSeq: this.trackSeq,
      artShown: this.artShown,
      pointerActive,
      pointerIdle: this.elapsed - this.lastPointerAt,
      pointerAt: { x: this.pointerAtX, y: this.pointerAtY },
      titleRect: this.titleRect
    }
    if (!still) {
      this.lastDrawElapsed = time
      this.resumed = false
    }
    this.spec.setUniforms?.(gl, q, res.value, frame, this.state)
    q.draw()
    if (!still) this.draws++
  }
}
