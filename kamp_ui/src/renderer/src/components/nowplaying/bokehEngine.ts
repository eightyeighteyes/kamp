// KAMP-561: framework-free bokeh renderer for the Now Playing background.
//
// Soft blurred colored orbs (from the album palette) drifting slowly. Deliberately
// calm (not beat-reactive) and never louder than the album art. Owns a single rAF
// handle; start()/stop() are idempotent. The backing store is rendered at reduced
// resolution and CSS-upscaled.
//
// KAMP-777: each orb (and the firefly) is drawn from a sprite with its blur baked
// in once (shared/bokehSprites.ts). The original CSS `filter: blur(28px)` over the
// whole canvas was re-run by the compositor every frame: 66% GPU busy on an Intel
// iGPU, against 8% for the sprites at RES_SCALE 0.25 (numbers in TUNING.md).

import type { Palette, RGB } from './palette'
import type { GLDevTarget } from './webgl/devHooks'
import { makeCanvas } from './sprites'
import { seededRng } from '../../../../shared/particleUtil'
import {
  BOKEH_BLUR_CSS,
  alphaScale,
  bakeKey,
  drawHalf,
  orbSprite,
  type OrbSprite
} from '../../../../shared/bokehSprites'
import { PaletteCrossfade } from '../../../../shared/paletteCrossfade'
import type { PaneRect } from '../../../../shared/particleUtil'
import { measureContent } from './contentRects'
import {
  BLOOM_MAX_DELAY,
  ORB_PLAN,
  SpriteBakeGate,
  VISIBLE_SHARE,
  advanceField,
  makeField,
  orbCenter,
  visibleOrbCount,
  visibleShare,
  type Orb,
  type Rng
} from '../../../../shared/bokehField'
import { MAX_SHARP, RackFocus, focusAlphas } from '../../../../shared/bokehFocus'

// --- tunables (the "5 knobs") ----------------------------------------------
// Backing store fraction of (clamped) devicePixelRatio. Everything drawn is a smooth
// blurred gradient, so bilinear upscaling hides the low resolution. Cost is linear in
// pixels: 0.5 measured 15.7% GPU busy, 0.25 8.3%, 0.125 5.4% (KAMP-777). 0.25 leaves
// headroom for KAMP-782's sharper orbs, which a lower scale would smear.
const RES_SCALE = 0.25
const MAX_DPR = 2
const FRAME_MS = 33 // ~30fps cap — slow drift needs no more
// Breath (+6%) and bloom (+20%) on top of an orb's base alpha: the brightest it
// ever gets, which is what a baked sprite holds (globalAlpha can only dim).
const ORB_ALPHA_PEAK = 1 + 0.06 + 0.2
const CROSSFADE_MS = 1000 // palette recolor duration on track change
const PARALLAX_TAU = 0.25 // seconds — heavy exponential damping of cursor parallax
// Firefly motes (KAMP-627): a rare, single transient spark. Poisson inter-arrival
// (floor + exponential(mean)) so the cadence never feels scheduled; idle-gated so it
// only appears in still moments; warm-dim so it reads as atmosphere, not a UI dot.
const FIREFLY_GAP_FLOOR = 30 // seconds — minimum gap between motes
const FIREFLY_GAP_MEAN = 60 // seconds — exponential mean added to the floor (avg ~1min)
const FIREFLY_GAP_MAX = 120 // seconds — soft cap on the heavy tail
const FIREFLY_IDLE = 8 // seconds of no pane pointer activity before a due mote appears
const FIREFLY_LIFE: [number, number] = [10, 15] // seconds, fade-in -> drift -> fade-out
const FIREFLY_SIZE: [number, number] = [0.08, 0.12] // core radius, fraction of S
const FIREFLY_TRAVEL = 0.07 // eased drift distance over life, fraction of S
const FIREFLY_BOW = 0.012 // perpendicular path bow, fraction of S
const FIREFLY_ALPHA = 0.8 // peak core alpha (kept modest; the baked blur softens it)
const FIREFLY_PEAK = FIREFLY_ALPHA * 1.15 // brightest with its breath: what the sprite holds
const FIREFLY_CORE: RGB = { r: 255, g: 246, b: 230 } // warm off-white centre
// Track-change bloom (KAMP-628): a soft swell on each palette change. Radius-led so it
// reads as the field "inhaling" rather than flashing; the tail outlasts the 1s crossfade
// so the light settles *after* the new colors land.
const BLOOM_DURATION = 1.5 // seconds — total bloom lifetime
const BLOOM_ATTACK = 0.3 // fraction of duration spent rising to peak (~0.45s)
const BLOOM_RADIUS = 0.16 // peak orb-radius boost (fraction of base)
const BLOOM_ALPHA = 0.2 // peak orb-alpha boost (kept low — brightness spikes read as a flash)
const BLOOM_DONE = BLOOM_DURATION + BLOOM_MAX_DELAY // sentinel: no bloom active
// Depth of field (KAMP-782). An in-focus accent is drawn as a second, sharp
// sprite over its soft one (soft under sharp, shared/bokehFocus.ts): a flat disc
// with a brighter lens-like rim.
// Edge blur of the sharp disc, in *backing* px. A floor, not a look: at RES_SCALE
// 0.25 one backing px is 2–4 CSS px, so anything under ~1.5 aliases into a
// crawling edge as the disc drifts (the stairs of KAMP-774). "Defined", not razor.
const SHARP_SIGMA = 1.5
const SHARP_RADIUS = 0.85 // disc radius, fraction of the orb's radius: focus gathers light
const SHARP_GAIN = 1.25 // disc interior alpha over the orb's alpha (must cover the soft centre)
const RIM_GAIN = 1.25 // rim alpha over the interior: the bright ring of real lens bokeh
const RIM_STOPS: [number, number, number] = [0.8, 0.93, 1] // interior ends, rim peak, edge
// The accents: the only orbs that ever come into focus.
const FOCUSABLE = ORB_PLAN.flatMap((p, i) => (p.tier === 'accent' ? [i] : []))

type FocusMode = 'auto' | 'on' | 'off'

const rand = (rng: Rng, min: number, max: number): number => min + rng() * (max - min)

const smoothstep = (t: number): number => t * t * (3 - 2 * t)
// Quintic ease with zero acceleration (not just velocity) at the ends — kills the faint
// onset "snap" that betrays a triggered animation. Used for the bloom envelope.
const smootherstep = (t: number): number => t * t * t * (t * (t * 6 - 15) + 10)

// A transient firefly mote (KAMP-627). Position is derived from `age` each frame
// (not integrated), so a stop/resume never desyncs it. Color is snapshotted at spawn.
interface Firefly {
  x0: number // spawn position, normalized [0,1]
  y0: number
  dirX: number // eased-drift direction (unit), biased away from center
  dirY: number
  perpX: number // unit perpendicular to dir, for the path bow
  perpY: number
  age: number // seconds since spawn
  life: number // total seconds
  size: number // core radius, fraction of S
  color: RGB
  breathPhase: number
  sprite: CanvasImageSource | null // null until baked
  spriteGeom: OrbSprite | null
  spriteKey: string // bakeKey the sprite was baked for
}

export class BokehEngine {
  private ctx: CanvasRenderingContext2D | null
  private rng: Rng = Math.random
  private orbs: Orb[] = makeField(this.rng)
  private cssW = 0
  private cssH = 0
  private scale = 1 // effective backing scale (reduced dpr)

  // Measurement knobs (__kampGL.tune, KAMP-777). Defaults are the shipped values.
  private resScale = RES_SCALE
  private frameMs = FRAME_MS
  private blurCss = BOKEH_BLUR_CSS
  private frozen = false // A/B screenshots: hold the clock still
  private focusMode: FocusMode = 'auto'
  private sharpSigma = SHARP_SIGMA
  private draws = 0
  private drawsSince = 0

  // Rack focus (KAMP-782). Visibility asks where the art is; that's a DOM read,
  // so the rect is refreshed once a second rather than every frame.
  private rack = new RackFocus(ORB_PLAN.length, FOCUSABLE, this.rng)
  private focusArt: PaneRect | null = null
  private nextArtAt = 0
  private sharpSprites: (CanvasImageSource | null)[] = []
  private sharpGeom: (OrbSprite | null)[] = []

  // Baked orb sprites, one per orb (each has its own radius and palette slot).
  // Re-baked when the geometry key changes, or the colours have moved since the
  // last bake (only during a crossfade; SpriteBakeGate has the rule and its tests).
  private sprites: (CanvasImageSource | null)[] = []
  private spriteGeom: (OrbSprite | null)[] = []
  private bakeGate = new SpriteBakeGate()

  // Palette crossfade (shared with the WebGL backgrounds). KAMP-779: a palette set
  // while the loop is stopped lands at once, so the reduced-motion still frame
  // shows the current track, not the previous one.
  private palette: PaletteCrossfade

  // Cursor parallax. Target is set by pointer events (normalized [-1,1], 0 =
  // pane center); the rendered value eases toward it in advance(). Both start
  // centered and are reset to center on start() so a resume never inherits a
  // stale off-center offset.
  private pointerTargetX = 0
  private pointerTargetY = 0
  private pointerX = 0
  private pointerY = 0

  // Firefly state. `nextFireflyAt` and `lastPointerAt` are in `elapsed` seconds, so
  // the cadence and idle gate freeze cleanly while the loop is stopped.
  private firefly: Firefly | null = null
  private nextFireflyAt = 0 // set in the constructor (needs fireflyGap())
  private lastPointerAt = 0 // elapsed at the last pane pointer activity
  private lastFireflyCorner = -1 // avoid spawning in the same corner twice running

  // Density census for __kampGL.stats() (KAMP-779): one-second samples of how many
  // orbs are visible, by count, since the last stats() call. Null — and free —
  // until the first stats() call, so it never runs outside a measurement session.
  // A single sample would miss a blank that shows up 10% of the time.
  private census: number[] | null = null
  private censusArt: PaneRect | null = null
  private nextCensusAt = 0
  private sharpCensus = { seen: 0, all: 0 } // samples of in-focus (> 0.5) orbs, and visible ones

  // Track-change bloom. Seconds since the last palette change; BLOOM_DONE = inactive.
  private bloomAge = BLOOM_DONE

  private elapsed = 0 // animation clock (seconds), advances only while running
  private rafId: number | null = null
  private lastTs = 0
  private lastDraw = 0
  private bg = '#141414'

  constructor(
    private canvas: HTMLCanvasElement,
    initial: Palette
  ) {
    this.ctx = canvas.getContext('2d')
    this.palette = new PaletteCrossfade(initial.colors, CROSSFADE_MS)
    this.refreshBg()
    this.nextFireflyAt = this.fireflyGap()
  }

  /** The __kampGL handle (KAMP-780), for measuring and tuning the softness. */
  devTarget(): GLDevTarget {
    return {
      tune: ({ scale, fps, blur, seed, freeze, focus, sharpSigma }) => {
        if (scale != null && scale > 0) this.resScale = scale
        if (fps != null && fps > 0) this.frameMs = 1000 / fps
        if (blur != null && blur >= 0) this.blurCss = blur
        if (freeze != null) this.frozen = freeze
        if (sharpSigma != null && sharpSigma >= 0) this.sharpSigma = sharpSigma
        if (seed != null) this.reseed(seed)
        if (focus != null) {
          this.focusMode = focus
          // 'auto' racks at once, so a tuning session needn't wait 20–40 s.
          if (focus === 'auto') this.rack.rack((i) => this.orbVisible(i))
        }
        this.bakeGate.invalidate() // force a re-bake at the new scale or blur
        this.resize()
      },
      stats: () => {
        const now = performance.now()
        const fps = (this.draws * 1000) / Math.max(1, now - this.drawsSince)
        this.draws = 0
        this.drawsSince = now
        return {
          fps: Math.round(fps * 10) / 10,
          backing: `${this.canvas.width}x${this.canvas.height}`,
          // What's applied right now: tune() persists until a reload, so a run
          // records its own settings rather than trusting what was typed earlier.
          settings: `scale ${this.resScale} blur ${this.blurCss} fps ${Math.round(1000 / this.frameMs)} focus ${this.focusMode} sharpSigma ${this.sharpSigma}${this.frozen ? ' frozen' : ''}`,
          density: this.takeCensus()
        }
      },
      forcePop: () => {
        this.firefly = null
        this.spawnFirefly()
      }
    }
  }

  // Report and reset the density census; the first call starts it. The art rect
  // is re-measured on each call (a DOM read, so not per sample): the art is
  // opaque, and an orb behind it counts for nothing.
  private takeCensus(): string {
    this.censusArt = measureContent(this.canvas).art
    const c = this.census
    const sharp = this.sharpCensus
    this.census = new Array(ORB_PLAN.length + 1).fill(0)
    this.sharpCensus = { seen: 0, all: 0 }
    this.nextCensusAt = this.elapsed
    const n = c ? c.reduce((a, b) => a + b, 0) : 0
    if (!c || n === 0) return 'visible orbs: sampling started, call stats() again later'
    const pct = (v: number, of: number): string => `${((100 * v) / of).toFixed(1)}%`
    const twoPlus = c.slice(2).reduce((a, b) => a + b, 0)
    const mean = c.reduce((a, v, i) => a + v * i, 0) / n
    // KAMP-782: an in-focus orb behind the art is depth of field nobody sees.
    const focus = sharp.all > 0 ? pct(sharp.seen, sharp.all) : 'n/a'
    return `visible orbs over ${n} s: 0 ${pct(c[0], n)} · 1 ${pct(c[1], n)} · 2+ ${pct(twoPlus, n)} · mean ${mean.toFixed(1)} · in-focus orbs visible ${focus}`
  }

  // Can orb i be seen right now (in the pane, outside the edge band and the art)?
  // Drawn position, pointer offset included: that's where the eye finds it.
  private orbVisible(i: number): boolean {
    const o = this.orbs[i]
    if (!o || this.cssW === 0) return false
    const { x, y } = orbCenter(o, this.elapsed, this.cssW, this.cssH, this.pointerX, this.pointerY)
    const r = o.sizePct * Math.min(this.cssW, this.cssH) * 0.5
    return visibleShare(x, y, r, this.cssW, this.cssH, this.focusArt) >= VISIBLE_SHARE
  }

  // How far orb i is drawn toward its sharp layer, under the current tune() mode.
  private focusOf(i: number): number {
    if (this.focusMode === 'off') return 0
    if (this.focusMode === 'on')
      return FOCUSABLE.indexOf(i) >= 0 && FOCUSABLE.indexOf(i) < MAX_SHARP ? 1 : 0
    return this.rack.focus[i] ?? 0
  }

  // Rebuild the field from a fixed seed with the clock at zero, so two variants
  // can be screenshotted at the same moment of the same layout.
  private reseed(seed: number): void {
    this.rng = seededRng(seed)
    this.orbs = makeField(this.rng)
    this.rack = new RackFocus(ORB_PLAN.length, FOCUSABLE, this.rng)
    this.elapsed = 0
    this.firefly = null
    this.nextFireflyAt = this.fireflyGap()
    this.lastPointerAt = 0
    this.resetPointer()
  }

  // Theme --bg for the vignette (all themes are dark; near-black).
  private refreshBg(): void {
    const v = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
    if (v) this.bg = v
  }

  // Push the themed --bg from React (KAMP-631). refreshBg only runs on
  // construction/resize, so a palette switch — which changes --bg without
  // resizing the canvas — would otherwise leave the vignette darkening the
  // edges with the PREVIOUS theme's bg (a neutral seam on the exposed sides
  // when the pane is wide). The caller reads --bg post-commit, where the value
  // is reliable, and re-applies it on every theme change.
  setBg(v: string): void {
    if (v) this.bg = v
  }

  private currentColors(): RGB[] {
    return this.palette.current()
  }

  setPalette(next: Palette): void {
    // Running: fade from whatever is on screen (a mid-fade skip never snaps).
    // Stopped: land at once — nothing would advance the fade, and a resume after
    // a hidden track change has nothing on screen to fade from.
    this.palette.set(next.colors, this.rafId != null)
    // Trigger the bloom. If one is still in flight, re-arm from the peak (not 0) so a
    // rapid skip re-excites the field smoothly instead of stuttering back through the attack.
    this.bloomAge = this.bloomAge < BLOOM_DONE ? BLOOM_ATTACK * BLOOM_DURATION : 0
    if (!this.rafId) this.renderStatic() // reduced-motion path repaints on change
  }

  // Set the parallax target from a normalized cursor position (0 = pane center).
  // Clamped so an out-of-pane coordinate can never push the field past its amplitude.
  setPointer(nx: number, ny: number): void {
    this.pointerTargetX = Math.max(-1, Math.min(1, nx))
    this.pointerTargetY = Math.max(-1, Math.min(1, ny))
    // Pointer activity resets the firefly idle gate — motes only appear when still.
    this.lastPointerAt = this.elapsed
  }

  // Snap both the target and the eased value back to center. Called on start() so a
  // resume after a stop never inherits a frozen off-center offset (nothing eases it
  // back while the loop is stopped).
  private resetPointer(): void {
    this.pointerTargetX = 0
    this.pointerTargetY = 0
    this.pointerX = 0
    this.pointerY = 0
  }

  // HiDPI + reduced-resolution sizing. Bails when the pane is display:none (0x0).
  resize(): void {
    const rect = this.canvas.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return
    this.refreshBg()
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR)
    this.scale = dpr * this.resScale
    this.cssW = rect.width
    this.cssH = rect.height
    this.canvas.width = Math.max(1, Math.round(rect.width * this.scale))
    this.canvas.height = Math.max(1, Math.round(rect.height * this.scale))
    const ctx = this.ctx
    if (ctx) {
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.scale(this.scale, this.scale) // draw in CSS-pixel space
    }
    if (!this.rafId) this.renderStatic()
  }

  start(): void {
    if (this.rafId != null) return
    this.lastTs = 0
    this.lastDraw = 0
    this.resetPointer() // resume centered; no stale offset from a prior activation
    // Suppress a stale bloom on resume: the palette effect can fire setPalette while the
    // loop is stopped (it's keyed on the art URL, not visibility), so a track change during
    // hidden/inactive would otherwise replay a late bloom here. The bloom is a discrete
    // "this just happened" event, so it should not fire late; the colors already landed
    // when setPalette ran while stopped (KAMP-779).
    this.bloomAge = BLOOM_DONE
    this.rafId = requestAnimationFrame(this.tick)
  }

  // Bloom envelope for an orb at `age` seconds into the bloom (its own onset delay already
  // subtracted): a smootherstep rise to peak, then a longer smootherstep settle. 0 outside.
  private bloomEnvelope(age: number): number {
    const x = age / BLOOM_DURATION
    if (x <= 0 || x >= 1) return 0
    if (x < BLOOM_ATTACK) return smootherstep(x / BLOOM_ATTACK)
    return 1 - smootherstep((x - BLOOM_ATTACK) / (1 - BLOOM_ATTACK))
  }

  stop(): void {
    if (this.rafId != null) {
      cancelAnimationFrame(this.rafId)
      this.rafId = null
    }
  }

  destroy(): void {
    this.stop()
    this.sprites = []
    this.spriteGeom = []
    this.sharpSprites = []
    this.sharpGeom = []
    this.firefly = null
    this.ctx = null
  }

  private tick = (ts: number): void => {
    this.rafId = requestAnimationFrame(this.tick)
    if (this.lastTs === 0) this.lastTs = ts
    const dt = Math.min(0.1, (ts - this.lastTs) / 1000) // clamp long gaps
    this.lastTs = ts
    // 30fps cap: advance the clock every rAF but only repaint every ~FRAME_MS.
    this.advance(dt)
    if (ts - this.lastDraw < this.frameMs) return
    this.lastDraw = ts
    this.draws++
    this.draw()
  }

  private advance(dt: number): void {
    if (document.hidden) return // belt-and-suspenders; the component also gates
    if (this.frozen) return
    this.elapsed += dt
    this.palette.tick(dt * 1000)
    this.bloomAge = Math.min(BLOOM_DONE, this.bloomAge + dt)
    // Framerate-independent exponential lag toward the cursor target.
    const k = 1 - Math.exp(-dt / PARALLAX_TAU)
    this.pointerX += (this.pointerTargetX - this.pointerX) * k
    this.pointerY += (this.pointerTargetY - this.pointerY) * k
    // Firefly: age the active one; else spawn when due AND the pointer has been idle.
    if (this.firefly) {
      this.firefly.age += dt
      if (this.firefly.age >= this.firefly.life) {
        this.firefly = null
        this.nextFireflyAt = this.elapsed + this.fireflyGap()
      }
    } else if (
      this.elapsed >= this.nextFireflyAt &&
      this.elapsed - this.lastPointerAt >= FIREFLY_IDLE
    ) {
      this.spawnFirefly()
    }
    advanceField(this.orbs, dt)
    // Rack focus runs on the animation clock (after the `frozen` check), so a
    // freeze holds focus too and a hidden pane never racks.
    if (this.cssW > 0 && this.elapsed >= this.nextArtAt) {
      this.nextArtAt = this.elapsed + 1
      this.focusArt = measureContent(this.canvas).art
    }
    this.rack.tick(dt, (i) => this.orbVisible(i))
    if (this.census && this.cssW > 0 && this.elapsed >= this.nextCensusAt) {
      this.nextCensusAt = this.elapsed + 1
      this.census[visibleOrbCount(this.orbs, this.elapsed, this.cssW, this.cssH, this.censusArt)]++
      for (let i = 0; i < this.orbs.length; i++) {
        if (this.focusOf(i) <= 0.5) continue
        this.sharpCensus.all++
        if (this.orbVisible(i)) this.sharpCensus.seen++
      }
    }
  }

  private draw(): void {
    const ctx = this.ctx
    if (!ctx || this.cssW === 0) return
    const { cssW: w, cssH: h } = this
    const s = Math.min(w, h)
    const colors = this.currentColors()

    ctx.clearRect(0, 0, w, h)
    this.bakeSprites(s, colors)
    ctx.globalCompositeOperation = 'screen'
    for (let i = 0; i < this.orbs.length; i++) {
      const o = this.orbs[i]
      const breath = Math.sin(this.elapsed / o.breathP + o.breathPhase)
      // Track-change bloom: a brief per-orb swell (radius-led) on top of the breath.
      const bloom = this.bloomEnvelope(this.bloomAge - o.bloomDelay)
      const { x, y } = orbCenter(o, this.elapsed, w, h, this.pointerX, this.pointerY)
      const radius = o.sizePct * s * 0.5 * (1 + 0.08 * breath + BLOOM_RADIUS * bloom)
      if (radius <= 0) continue
      const alpha = o.baseAlpha * (1 + 0.06 * breath + BLOOM_ALPHA * bloom)
      const sprite = this.sprites[i]
      const geom = this.spriteGeom[i]
      if (!sprite || !geom) continue
      // Drawn from its pre-blurred sprite: scaled for breath and bloom, dimmed
      // from its baked peak alpha.
      const peak = o.baseAlpha * ORB_ALPHA_PEAK
      const dim = alphaScale(alpha, peak)
      const focus = this.focusOf(i)
      const sharp = focus > 0 ? this.sharpSprites[i] : null
      const sharpGeom = this.sharpGeom[i]
      // Soft under sharp (KAMP-782): the soft layer dims as the disc sharpens,
      // by no more than keeps the centre from darkening mid-pull. The centres
      // are the baked (clamped) alphas times `dim`: what actually lands.
      const layers = focusAlphas(
        focus,
        Math.min(1, peak) * dim,
        Math.min(1, peak * SHARP_GAIN) * dim
      )
      const half = drawHalf(geom, radius, this.scale)
      ctx.globalAlpha = dim * (sharp ? layers.soft : 1)
      ctx.drawImage(sprite, x - half, y - half, half * 2, half * 2)
      if (sharp && sharpGeom) {
        const sh = drawHalf(sharpGeom, radius * SHARP_RADIUS, this.scale)
        ctx.globalAlpha = dim * layers.sharp
        ctx.drawImage(sharp, x - sh, y - sh, sh * 2, sh * 2)
      }
      ctx.globalAlpha = 1
    }
    ctx.globalCompositeOperation = 'source-over'
    this.drawVignette(ctx, w, h)
    this.drawFirefly(ctx, w, h, s) // after the vignette so a corner mote still glows
  }

  // The orb's 3-stop gradient, drawn at `peakAlpha` into a centred sprite with
  // the blur applied by the offscreen context's filter. Used for orbs and the
  // firefly alike; `core` replaces the centre stop's colour (the firefly's warm
  // white). Returns null when no 2D context is available (sprite path skipped).
  private bakeOrb(
    geom: OrbSprite,
    c: RGB,
    peakAlpha: number,
    stops: [number, number],
    core?: RGB
  ): CanvasImageSource | null {
    const side = geom.half * 2
    const { canvas, ctx } = makeCanvas(side)
    if (!ctx) return null
    const cx = geom.half
    const r = Math.max(0.5, geom.radius)
    if (geom.sigma > 0) ctx.filter = `blur(${geom.sigma}px)`
    const k = core ?? c
    const grad = ctx.createRadialGradient(cx, cx, 0, cx, cx, r)
    grad.addColorStop(0, `rgba(${k.r},${k.g},${k.b},${peakAlpha})`)
    grad.addColorStop(stops[0], `rgba(${c.r},${c.g},${c.b},${peakAlpha * stops[1]})`)
    grad.addColorStop(1, `rgba(${c.r},${c.g},${c.b},0)`)
    ctx.fillStyle = grad
    ctx.fillRect(cx - r, cx - r, r * 2, r * 2)
    return canvas
  }

  // An in-focus accent (KAMP-782): a flat disc with a brighter rim, like a point
  // of light through a real lens, with only a small blur on its edge. Baked at
  // the orb's peak alpha (bloom + breath) scaled by the gains, since globalAlpha
  // can only dim (KAMP-777); drawn at the same `dim` as the soft layer, the
  // interior then lands at SHARP_GAIN × the orb's alpha, which focusAlphas() is
  // told. Alphas clamp at 1: on a bright orb the rim can't outshine the disc.
  private bakeSharp(geom: OrbSprite, c: RGB, peakAlpha: number): CanvasImageSource | null {
    const side = geom.half * 2
    const { canvas, ctx } = makeCanvas(side)
    if (!ctx) return null
    const cx = geom.half
    const r = Math.max(0.5, geom.radius)
    if (geom.sigma > 0) ctx.filter = `blur(${geom.sigma}px)`
    const inner = Math.min(1, peakAlpha * SHARP_GAIN)
    const rim = Math.min(1, inner * RIM_GAIN)
    const rgba = (a: number): string => `rgba(${c.r},${c.g},${c.b},${a})`
    const grad = ctx.createRadialGradient(cx, cx, 0, cx, cx, r)
    grad.addColorStop(0, rgba(inner))
    grad.addColorStop(RIM_STOPS[0], rgba(inner))
    grad.addColorStop(RIM_STOPS[1], rgba(rim))
    grad.addColorStop(RIM_STOPS[2], rgba(0))
    ctx.fillStyle = grad
    ctx.beginPath()
    ctx.arc(cx, cx, r, 0, Math.PI * 2)
    ctx.fill()
    return canvas
  }

  // Bake the orb sprites if the geometry or the colours changed since last time.
  // In steady state this does nothing: colours only move during the 1 s crossfade.
  private bakeSprites(s: number, colors: RGB[]): void {
    const key = `${bakeKey(s, this.scale, this.blurCss)}|${this.sharpSigma}`
    if (!this.bakeGate.shouldBake(key, colors)) return
    // Every accent's sharp disc is baked here, eagerly, so a rack never stalls a
    // frame baking one; the extra cost is a few small, barely blurred sprites.
    this.sharpSprites = this.orbs.map((o, i) => {
      const c = colors[o.colorIndex] ?? colors[0]
      if (!FOCUSABLE.includes(i) || !c) return null
      const geom = orbSprite(
        o.sizePct * s * 0.5 * SHARP_RADIUS,
        this.sharpSigma / this.scale,
        this.scale
      )
      this.sharpGeom[i] = geom
      return this.bakeSharp(geom, c, o.baseAlpha * ORB_ALPHA_PEAK)
    })
    this.sprites = this.orbs.map((o, i) => {
      // Each orb's own depth of field (KAMP-782): `blurCss` is the reference
      // softness (and the tune() knob); the orb scales it by its tier's range.
      const geom = orbSprite(o.sizePct * s * 0.5, this.blurCss * o.blurMul, this.scale)
      this.spriteGeom[i] = geom
      const c = colors[o.colorIndex] ?? colors[0]
      return c
        ? this.bakeOrb(geom, c, Math.min(1, o.baseAlpha * ORB_ALPHA_PEAK), [0.4, 0.55])
        : null
    })
  }

  // Darken toward the edges with the theme bg so the corners stay calm and the
  // centered album art reads as the hero.
  private drawVignette(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    const cx = w / 2
    const cy = h / 2
    const r = Math.hypot(w, h) / 2
    const grad = ctx.createRadialGradient(cx, cy, r * 0.45, cx, cy, r)
    grad.addColorStop(0, this.rgbaBg(0))
    grad.addColorStop(1, this.rgbaBg(0.35))
    ctx.fillStyle = grad
    ctx.fillRect(0, 0, w, h)
  }

  private rgbaBg(a: number): string {
    // --bg is a hex string on all themes; fall back to near-black.
    const m = /^#?([0-9a-f]{6})$/i.exec(this.bg)
    const n = m ? parseInt(m[1], 16) : 0x141414
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
  }

  // Poisson inter-arrival: a floor plus an exponential draw, so the cadence is
  // irregular (never a steady beat) with an occasional long dry spell. Soft-capped.
  private fireflyGap(): number {
    const u = Math.max(1e-6, this.rng())
    return Math.min(FIREFLY_GAP_MAX, FIREFLY_GAP_FLOOR - FIREFLY_GAP_MEAN * Math.log(u))
  }

  private spawnFirefly(): void {
    const colors = this.currentColors()
    if (!colors.length) return
    const src = colors[Math.floor(this.rng() * colors.length)]
    const color = { ...src } // snapshot so a mid-flight track change never recolors it
    // Pick a corner, not the same as last time.
    let corner = Math.floor(this.rng() * 4)
    if (corner === this.lastFireflyCorner) corner = (corner + 1) % 4
    this.lastFireflyCorner = corner
    const left = corner === 0 || corner === 3
    const top = corner < 2
    const x0 = left ? rand(this.rng, 0.08, 0.24) : rand(this.rng, 0.76, 0.92)
    const y0 = top ? rand(this.rng, 0.08, 0.24) : rand(this.rng, 0.76, 0.92)
    // Drift outward (away from center) so the mote never walks the eye to the art.
    const outAngle = Math.atan2(y0 < 0.5 ? -1 : 1, x0 < 0.5 ? -1 : 1)
    const angle = outAngle + rand(this.rng, -0.6, 0.6)
    const dirX = Math.cos(angle)
    const dirY = Math.sin(angle)
    this.firefly = {
      x0,
      y0,
      dirX,
      dirY,
      perpX: -dirY,
      perpY: dirX,
      age: 0,
      life: rand(this.rng, FIREFLY_LIFE[0], FIREFLY_LIFE[1]),
      size: rand(this.rng, FIREFLY_SIZE[0], FIREFLY_SIZE[1]),
      color,
      breathPhase: rand(this.rng, 0, Math.PI * 2),
      sprite: null,
      spriteGeom: null,
      spriteKey: ''
    }
  }

  // A single warm mote drawn over the vignette with additive blend. The alpha
  // envelope (slow emerge, long fade-out, gentle breath) dims its sprite through
  // globalAlpha, restored to 1 after, which the orb/vignette passes assume.
  private drawFirefly(ctx: CanvasRenderingContext2D, w: number, h: number, s: number): void {
    const f = this.firefly
    if (!f) return
    const t = f.age / f.life
    const fadeIn = smoothstep(Math.min(1, t / 0.3))
    const fadeOut = smoothstep(Math.min(1, (1 - t) / 0.5))
    const breath = 1 + 0.15 * Math.sin(t * Math.PI * 3 + f.breathPhase)
    const env = fadeIn * fadeOut * breath
    if (env <= 0) return
    const alpha = FIREFLY_ALPHA * env
    // Eased outward travel + a perpendicular bow so the path is never ruler-straight.
    const travel = FIREFLY_TRAVEL * smoothstep(t)
    const bow = FIREFLY_BOW * Math.sin(t * Math.PI)
    const x = f.x0 * w + (f.dirX * travel + f.perpX * bow) * s
    const y = f.y0 * h + (f.dirY * travel + f.perpY * bow) * s
    const radius = f.size * s
    // Baked once per spawn (and per geometry change): one blur a minute. The warm
    // off-white core is the centre stop, so the colour reads as a glow, not a dot.
    const key = bakeKey(s, this.scale, this.blurCss)
    if (f.spriteKey !== key) {
      f.spriteGeom = orbSprite(radius, this.blurCss, this.scale)
      f.sprite = this.bakeOrb(f.spriteGeom, f.color, FIREFLY_PEAK, [0.35, 0.7], FIREFLY_CORE)
      f.spriteKey = key
    }
    if (!f.sprite || !f.spriteGeom) return
    const half = drawHalf(f.spriteGeom, radius, this.scale)
    ctx.globalCompositeOperation = 'lighter'
    ctx.globalAlpha = alphaScale(alpha, FIREFLY_PEAK)
    ctx.drawImage(f.sprite, x - half, y - half, half * 2, half * 2)
    ctx.globalAlpha = 1
    ctx.globalCompositeOperation = 'source-over'
  }

  // One-shot static paint for the off/reduced-motion path: a soft gradient of the
  // two dominant swatches, plus the vignette. No animation.
  renderStatic(): void {
    const ctx = this.ctx
    if (!ctx || this.cssW === 0) return
    const { cssW: w, cssH: h } = this
    const colors = this.currentColors()
    ctx.clearRect(0, 0, w, h)
    ctx.globalCompositeOperation = 'screen'
    const grad = ctx.createLinearGradient(0, 0, w, h)
    const a = colors[0] ?? { r: 40, g: 40, b: 48 }
    const b = colors[1] ?? a
    grad.addColorStop(0, `rgba(${a.r},${a.g},${a.b},0.22)`)
    grad.addColorStop(1, `rgba(${b.r},${b.g},${b.b},0.22)`)
    ctx.fillStyle = grad
    ctx.fillRect(0, 0, w, h)
    ctx.globalCompositeOperation = 'source-over'
    this.drawVignette(ctx, w, h)
  }
}
