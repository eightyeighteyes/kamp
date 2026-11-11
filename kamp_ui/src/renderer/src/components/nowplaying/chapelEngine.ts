// KAMP-775: framework-free Canvas 2D renderer for the "Chapel" Now Playing
// background: three stacked fields of album color, close to rectangular with
// feathered edges, that swell and dim over 20-40 seconds.
//
// The layout, timing and every "what happens while stopped" rule live in
// shared/chapelLayout.ts (node-tested); this file only draws. Public surface
// mirrors BokehEngine (setPalette, setBg, setPointer, start/stop, resize,
// destroy, renderStatic) plus retarget/pointerLeave/setArtVisible/setAccent.
//
// No CSS filter: Bokeh's blur(28px) was ~50 of its 65 GPU points (KAMP-776/777).
// Edges are feathered with gradient stops instead, in a pinned draw order:
//   1. --bg fill
//   2. each field as a vertical gradient (transparent -> color -> transparent)
//   3. seam glows and the rare bloom, in 'screen'
//   4. --bg overlays across the side margins, fading inward. Because they come
//      after 2 and 3, every field and glow gets the same separable mask
//      a(y) * (1 - b(x)): soft corners that still read as rectangles, and no
//      glow ever shows a hard end at the canvas edge
//   5. the --bg band behind the title, only while the art (and title) show

import {
  ALPHA_BREATH,
  FIELD_ALPHA,
  FIELD_COUNT,
  ProportionTween,
  SeamBloom,
  SeamWarmth,
  TITLE_DIM,
  breathAt,
  breathedWeights,
  fieldColors,
  layoutFields,
  makeBreaths,
  randomProportions,
  type Breath,
  type FieldLayout
} from '../../../../shared/chapelLayout'
import { PaletteCrossfade } from '../../../../shared/paletteCrossfade'
import { accentPalette, hexToRgb, type Palette, type RGB } from './palette'

// --- tunables ------------------------------------------------------------------------
// Backing store fraction of the (capped) DPR. 0.5, not Bokeh-WebGL's 0.25: there is
// no blur to hide 8-bit steps on dark gradients once they are upscaled.
const RES_SCALE = 0.5
const MAX_DPR = 2
const DEFAULT_FPS = 15 // motion periods are 20-40 s; more frames buy nothing visible
// rAF timestamps jitter; without slack a frame cap skips an extra vsync (see shaderEngine).
const FRAME_SLACK_MS = 1.5
const CROSSFADE_MS = 4000 // slow, so the new palette arrives as the fields re-proportion
const RETARGET_MS = 7000 // track-change re-proportion
const POINTER_REACH = 0.12 // fraction of pane height within which a seam warms
const GLOW_ALPHA = 0.32 // peak cursor-glow opacity
const BLOOM_ALPHA = 0.42 // peak opacity of the rare seam warming
const GLOW_HALF_H = 1.4 // glow band half-height, in feathers
const GLOW_MIN = 0.003 // below this a glow isn't drawn (and costs nothing)
const WARM: RGB = { r: 255, g: 222, b: 184 } // glows lean toward warm white
const TITLE_DIM_FEATHER = 48 // CSS px over which the title band fades in
const DEFAULT_BG: RGB = { r: 0x14, g: 0x14, b: 0x14 }

export interface ChapelEngineOptions {
  /** prefers-reduced-motion: still frames are the composition at rest. */
  reducedMotion: boolean
  /** Frame cap; cost is linear in pixels x frames. */
  fps?: number
}

const rgba = (c: RGB, a: number): string =>
  `rgba(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)},${a})`

const mix = (a: RGB, b: RGB, t: number): RGB => ({
  r: a.r + (b.r - a.r) * t,
  g: a.g + (b.g - a.g) * t,
  b: a.b + (b.b - a.b) * t
})

export class ChapelEngine {
  private ctx: CanvasRenderingContext2D | null
  private cssW = 0
  private cssH = 0
  private bg: RGB = DEFAULT_BG
  private fallback: RGB[]
  private readonly crossfade: PaletteCrossfade
  private readonly proportions = new ProportionTween(randomProportions(Math.random), RETARGET_MS)
  private readonly breaths: Breath[] = makeBreaths(Math.random)
  private readonly warmth = new SeamWarmth(FIELD_COUNT - 1)
  private readonly bloom = new SeamBloom(Math.random)
  private seams: number[] = [] // from the last draw, for aiming the warmth

  private pointerY: number | null = null // CSS px, null = not over the pane
  private lastPointerAt = 0 // elapsed at the last pointer activity
  private artVisible = true
  private titleTop: number | null = null // CSS px from the canvas top

  private elapsed = 0
  private rafId: number | null = null
  private lastTs = 0
  private lastDraw = 0
  private readonly frameMs: number
  private readonly reducedMotion: boolean

  constructor(
    private readonly canvas: HTMLCanvasElement,
    initial: Palette,
    opts: ChapelEngineOptions
  ) {
    this.ctx = canvas.getContext('2d')
    this.reducedMotion = opts.reducedMotion
    this.frameMs = 1000 / (opts.fps ?? DEFAULT_FPS)
    this.fallback = fieldColors(initial.colors)
    this.crossfade = new PaletteCrossfade(this.fallback, CROSSFADE_MS)
  }

  private get running(): boolean {
    return this.rafId != null
  }

  // --- contract ------------------------------------------------------------------------

  setBg(hex: string): void {
    this.bg = hexToRgb(hex) ?? DEFAULT_BG
    if (!this.running) this.renderStatic()
  }

  /** Theme accent, for the fallback when a palette has no colors. */
  setAccent(hex: string): void {
    this.fallback = fieldColors(accentPalette(hex).colors)
  }

  setPalette(next: Palette): void {
    const colors = fieldColors(next.colors)
    // KAMP-779: while stopped nothing advances the fade, so land on the new colors.
    this.crossfade.set(colors.length > 0 ? colors : this.fallback, this.running)
    if (!this.running) this.renderStatic()
  }

  /** New track: the fields re-proportion (landing directly while stopped). */
  retarget(): void {
    this.proportions.set(randomProportions(Math.random), this.running)
    if (!this.running) this.renderStatic()
  }

  /** Normalized pane position, -1..1 (as BokehEngine). Only y matters here. */
  setPointer(_nx: number, ny: number): void {
    this.pointerY = ((Math.max(-1, Math.min(1, ny)) + 1) / 2) * this.cssH
    this.lastPointerAt = this.elapsed
  }

  /** The cursor left the pane: let the glow cool on its release curve. */
  pointerLeave(): void {
    this.pointerY = null
    this.lastPointerAt = this.elapsed
  }

  /** The title exists only while the art shows (KAMP-769), and so does its dim band. */
  setArtVisible(visible: boolean): void {
    this.artVisible = visible
    this.measureTitle()
    if (!this.running) this.renderStatic()
  }

  /**
   * Size the backing store to the element. Assigning width/height clears the
   * canvas, so this always repaints synchronously, running or not; at 15 fps a
   * window drag would otherwise flash the bare backdrop between frames.
   */
  resize(): void {
    const rect = this.canvas.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return // display:none; RO fires again later
    const scale = Math.min(window.devicePixelRatio || 1, MAX_DPR) * RES_SCALE
    const w = Math.max(1, Math.round(rect.width * scale))
    const h = Math.max(1, Math.round(rect.height * scale))
    this.cssW = rect.width
    this.cssH = rect.height
    if (w !== this.canvas.width || h !== this.canvas.height) {
      this.canvas.width = w
      this.canvas.height = h
    }
    this.ctx?.setTransform(scale, 0, 0, scale, 0, 0) // draw in CSS-pixel space
    this.measureTitle()
    if (this.running) this.draw(this.elapsed, false)
    else this.renderStatic()
  }

  start(): void {
    if (this.running) return
    // Nothing ticks these while stopped, so a resume must not replay them late.
    this.warmth.clear()
    this.bloom.cancel(this.elapsed)
    this.pointerY = null
    this.lastTs = 0
    this.lastDraw = 0
    this.rafId = requestAnimationFrame(this.tick)
  }

  stop(): void {
    if (this.rafId != null) cancelAnimationFrame(this.rafId)
    this.rafId = null
  }

  destroy(): void {
    this.stop()
    this.ctx = null
  }

  /** One frame without the loop. Reduced-motion gets the composition at rest. */
  renderStatic(): void {
    this.draw(this.reducedMotion ? 0 : this.elapsed, true)
  }

  // --- loop -------------------------------------------------------------------------------

  private tick = (ts: number): void => {
    this.rafId = requestAnimationFrame(this.tick)
    if (this.lastTs === 0) this.lastTs = ts
    const dt = Math.min(0.1, (ts - this.lastTs) / 1000)
    this.lastTs = ts
    if (document.hidden) return
    this.advance(dt)
    if (ts - this.lastDraw < this.frameMs - FRAME_SLACK_MS) return
    this.lastDraw = ts
    this.draw(this.elapsed, false)
  }

  private advance(dt: number): void {
    this.elapsed += dt
    this.crossfade.tick(dt * 1000)
    this.proportions.tick(dt * 1000)
    this.warmth.aim(this.pointerY, this.seams, POINTER_REACH * this.cssH)
    this.warmth.tick(dt)
    this.bloom.tick(dt, this.elapsed, this.elapsed - this.lastPointerAt, this.seams.length)
  }

  // --- drawing ------------------------------------------------------------------------------

  private measureTitle(): void {
    this.titleTop = null
    if (!this.artVisible) return
    const meta = this.canvas.closest('.now-playing')?.querySelector('.now-playing-meta')
    if (!meta) return
    const top = meta.getBoundingClientRect().top - this.canvas.getBoundingClientRect().top
    if (top > 0 && top < this.cssH) this.titleTop = top
  }

  private draw(time: number, still: boolean): void {
    const ctx = this.ctx
    if (!ctx || this.cssW === 0) return
    const w = this.cssW
    const h = this.cssH
    const atRest = still && this.reducedMotion
    const breaths = atRest ? null : this.breaths

    ctx.globalCompositeOperation = 'source-over'
    ctx.fillStyle = rgba(this.bg, 1)
    ctx.fillRect(0, 0, w, h)

    const layout = layoutFields(w, h, breathedWeights(this.proportions.current(), breaths, time))
    this.seams = layout.seams
    if (layout.fields.length === 0) return
    const colors = this.crossfade.current()
    const colorAt = (i: number): RGB => colors[i % colors.length] ?? this.bg

    this.drawFields(ctx, layout, colorAt, breaths, time)
    if (!atRest) this.drawGlows(ctx, layout, colorAt)
    this.drawSideFeather(ctx, layout, w, h)
    this.drawTitleBand(ctx, w, h)
  }

  private drawFields(
    ctx: CanvasRenderingContext2D,
    { fields, feather }: FieldLayout,
    colorAt: (i: number) => RGB,
    breaths: Breath[] | null,
    time: number
  ): void {
    for (let i = 0; i < fields.length; i++) {
      const f = fields[i]
      const b = breaths?.[i] ? breathAt(breaths[i], time) : 0
      const a = FIELD_ALPHA * (1 + ALPHA_BREATH * b)
      const c = colorAt(i)
      // Each edge fades over one feather centred on the edge.
      const y0 = f.y - feather / 2
      const y1 = f.y + f.h + feather / 2
      const s = Math.min(0.5, feather / (y1 - y0))
      const grad = ctx.createLinearGradient(0, y0, 0, y1)
      grad.addColorStop(0, rgba(c, 0))
      grad.addColorStop(s, rgba(c, a))
      grad.addColorStop(1 - s, rgba(c, a))
      grad.addColorStop(1, rgba(c, 0))
      ctx.fillStyle = grad
      // Full width: the side overlays supply the horizontal feather.
      ctx.fillRect(0, y0, this.cssW, y1 - y0)
    }
  }

  private drawGlows(
    ctx: CanvasRenderingContext2D,
    { fields, seams, feather }: FieldLayout,
    colorAt: (i: number) => RGB
  ): void {
    const warmth = this.warmth.values()
    const bloom = this.bloom.active
    const halfH = feather * GLOW_HALF_H
    ctx.globalCompositeOperation = 'screen'
    for (let j = 0; j < seams.length; j++) {
      // The seam takes the warmth of both fields it divides, pushed toward warm white.
      const c = mix(mix(colorAt(j), colorAt(j + 1), 0.5), WARM, 0.55)
      const a = GLOW_ALPHA * (warmth[j] ?? 0)
      if (a > GLOW_MIN) {
        // A full-width band, so it shows in the side strips beside the art.
        const grad = ctx.createLinearGradient(0, seams[j] - halfH, 0, seams[j] + halfH)
        grad.addColorStop(0, rgba(c, 0))
        grad.addColorStop(0.5, rgba(c, a))
        grad.addColorStop(1, rgba(c, 0))
        ctx.fillStyle = grad
        ctx.fillRect(0, seams[j] - halfH, this.cssW, halfH * 2)
      }
      if (bloom && bloom.seam === j) {
        const ba = BLOOM_ALPHA * this.bloom.envelope()
        const f = fields[0]
        const halfW = Math.max(1, this.bloom.spread() * f.w * 0.75)
        if (ba > GLOW_MIN) {
          // An ellipse that widens along the seam from where it started.
          const cx = f.x + bloom.x * f.w
          const ry = halfH * 1.3
          ctx.save()
          ctx.translate(cx, seams[j])
          ctx.scale(halfW / ry, 1)
          const grad = ctx.createRadialGradient(0, 0, 0, 0, 0, ry)
          grad.addColorStop(0, rgba(WARM, ba))
          grad.addColorStop(0.45, rgba(c, ba * 0.6))
          grad.addColorStop(1, rgba(c, 0))
          ctx.fillStyle = grad
          ctx.fillRect(-ry, -ry, ry * 2, ry * 2)
          ctx.restore()
        }
      }
    }
    ctx.globalCompositeOperation = 'source-over'
  }

  private drawSideFeather(
    ctx: CanvasRenderingContext2D,
    { fields, featherX }: FieldLayout,
    w: number,
    h: number
  ): void {
    const left = fields[0].x
    const right = fields[0].x + fields[0].w
    const half = featherX / 2
    const solid = rgba(this.bg, 1)
    const clear = rgba(this.bg, 0)
    const l = ctx.createLinearGradient(left - half, 0, left + half, 0)
    l.addColorStop(0, solid)
    l.addColorStop(1, clear)
    ctx.fillStyle = solid
    ctx.fillRect(0, 0, Math.max(0, left - half), h)
    ctx.fillStyle = l
    ctx.fillRect(left - half, 0, featherX, h)
    const r = ctx.createLinearGradient(right - half, 0, right + half, 0)
    r.addColorStop(0, clear)
    r.addColorStop(1, solid)
    ctx.fillStyle = r
    ctx.fillRect(right - half, 0, featherX, h)
    ctx.fillStyle = solid
    ctx.fillRect(right + half, 0, Math.max(0, w - right - half), h)
  }

  private drawTitleBand(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    const top = this.titleTop
    if (top == null) return
    const y0 = Math.max(0, top - TITLE_DIM_FEATHER)
    const grad = ctx.createLinearGradient(0, y0, 0, top)
    grad.addColorStop(0, rgba(this.bg, 0))
    grad.addColorStop(1, rgba(this.bg, TITLE_DIM))
    ctx.fillStyle = grad
    ctx.fillRect(0, y0, w, top - y0)
    ctx.fillStyle = rgba(this.bg, TITLE_DIM)
    ctx.fillRect(0, top, w, h - top)
  }
}
