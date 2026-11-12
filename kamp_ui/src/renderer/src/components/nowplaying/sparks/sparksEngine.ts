// KAMP-767: framework-free Canvas 2D renderer for the "Sparks" Now Playing
// background: campfire sparks in album colors rising, curling and winking out.
//
// Every rule lives in shared/sparksModel.ts (node-tested). This file only draws
// what the model hands it. Its public surface mirrors ChapelEngine (setPalette,
// setBg, setAccent, setPointer, pointerLeave, start/stop, resize, destroy,
// renderStatic), plus trackChanged, setContentRects and devTarget.
//
// Draw order: 1. the --bg fill; 2. a low ember glow along the bottom edge (one
// cached gradient); 3. the sparks, each a drawImage of a pre-baked glow sprite in
// 'lighter'. No CSS filter: the blur is baked into the sprites (glowSprites.ts).

import {
  SIZE_RADIUS,
  SparkField,
  KIND_DEAD,
  POOL_SIZE,
  stillFrame,
  type PaneRect
} from '../../../../../shared/sparksModel'
import { PaletteCrossfade } from '../../../../../shared/paletteCrossfade'
import { titleQuiet } from '../../../../../shared/particleUtil'
import { accentPalette, hexToRgb, type Palette, type RGB } from '../palette'
import type { GLDevTarget } from '../webgl/devHooks'
import type { ContentRect } from '../webgl/shaderEngine'
import { bakeSheet, type GlowSheet } from './glowSprites'

// --- tunables ------------------------------------------------------------------------
// Backing store fraction of the (capped) DPR. The sprites carry their own soft
// edges, so half resolution reads fine and quarters the fill.
const RES_SCALE = 0.5
const MAX_DPR = 2
const DEFAULT_FPS = 30 // sparks move in pixels per frame; 15 fps judders
// rAF timestamps jitter; without slack a frame cap skips an extra vsync (see shaderEngine).
const FRAME_SLACK_MS = 1.5
// Baked halo blur, in CSS px. Part of the animation's definition, not a user setting.
const SPARK_BLUR_CSS = 6
// Re-bake the sprites only when the backing pane height moves this much, so a
// window drag doesn't re-bake on every ResizeObserver tick.
const REBAKE_TOLERANCE = 0.05
const CROSSFADE_MS = 1500 // the ember glow's recolor; the sparks recolor by turnover
const EMBER_H = 0.22 // fraction of pane height
const EMBER_ALPHA = 0.16
const TITLE_QUIET = 0.25 // spark alpha behind the title text
const TITLE_FEATHER = 0.03 // of pane height, so a spark dims into the title, not at it
const MIN_ALPHA = 0.01
const PREWARM_S = 6 // a fresh pane opens on a lit fire, never an empty one
const DEFAULT_BG: RGB = { r: 0x14, g: 0x14, b: 0x14 }

export interface SparksEngineOptions {
  /** prefers-reduced-motion: show the designed still frame. */
  reducedMotion: boolean
}

const rgba = (c: RGB, a: number): string =>
  `rgba(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)},${a})`

export class SparksEngine {
  private ctx: CanvasRenderingContext2D | null
  private readonly field = new SparkField(Math.random)
  private readonly reducedMotion: boolean
  private cssW = 0
  private cssH = 0
  private scale = 1
  private resScale = RES_SCALE
  private frameMs = 1000 / DEFAULT_FPS
  private blurCss = SPARK_BLUR_CSS
  private bg: RGB = DEFAULT_BG
  private fallback: RGB[]
  private readonly ember: PaletteCrossfade

  // Palette generation -> its colors and (once the size is known) its sprites.
  private readonly colorsByGen = new Map<number, RGB[]>()
  private readonly sheets = new Map<number, GlowSheet>()
  private bakedFor = 0 // backing pane height the sheets were baked at
  private emberGrad: CanvasGradient | null = null

  private title: ContentRect | null = null
  private art: ContentRect | null = null
  private still: ReturnType<typeof stillFrame> | null = null
  private warmed = false

  private rafId: number | null = null
  private lastTs = 0
  private lastDraw = 0
  private draws = 0
  private drawsSince = performance.now()

  constructor(
    private readonly canvas: HTMLCanvasElement,
    initial: Palette,
    opts: SparksEngineOptions
  ) {
    this.ctx = canvas.getContext('2d')
    this.reducedMotion = opts.reducedMotion
    this.fallback = initial.colors.length > 0 ? initial.colors : accentPalette('#7c86e1').colors
    this.ember = new PaletteCrossfade(this.fallback, CROSSFADE_MS)
    this.colorsByGen.set(this.field.setPalette(false), this.fallback)
  }

  private get running(): boolean {
    return this.rafId != null
  }

  // --- contract ------------------------------------------------------------------------

  setBg(hex: string): void {
    this.bg = hexToRgb(hex) ?? DEFAULT_BG
    this.emberGrad = null
    if (!this.running) this.renderStatic()
  }

  /** Theme accent, for the fallback when a palette has no colors. */
  setAccent(hex: string): void {
    this.fallback = accentPalette(hex).colors
  }

  setPalette(next: Palette): void {
    const colors = next.colors.length > 0 ? next.colors : this.fallback
    const gen = this.field.setPalette(this.running)
    this.colorsByGen.set(gen, colors)
    this.pruneGenerations()
    this.bake(gen)
    // KAMP-779: while stopped nothing advances a fade, so land on the new colors.
    this.ember.set(colors, this.running)
    this.emberGrad = null
    if (!this.running) this.renderStatic()
  }

  /** id to a different id; the field ignores it while stopped. */
  trackChanged(): void {
    this.field.trackChanged(this.running)
  }

  /** Normalized pane position, -1..1 (as BokehEngine). */
  setPointer(nx: number, ny: number): void {
    if (this.cssH === 0) return
    const aspect = this.cssW / this.cssH
    this.field.pointerMove(((nx + 1) / 2) * aspect, (ny + 1) / 2)
  }

  pointerLeave(): void {
    this.field.pointerLeave()
  }

  /** The title's text box and the art's box (normalized), each null while hidden. */
  setContentRects(title: ContentRect | null, art: ContentRect | null): void {
    this.title = title
    this.art = art
    this.still = null
    this.applyBounds()
    if (!this.running) this.renderStatic()
  }

  /**
   * Size the backing store to the element. Assigning width/height clears the
   * canvas, so this always repaints synchronously, running or not.
   */
  resize(): void {
    const rect = this.canvas.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return // display:none; RO fires again later
    this.scale = Math.min(window.devicePixelRatio || 1, MAX_DPR) * this.resScale
    const w = Math.max(1, Math.round(rect.width * this.scale))
    const h = Math.max(1, Math.round(rect.height * this.scale))
    this.cssW = rect.width
    this.cssH = rect.height
    if (w !== this.canvas.width || h !== this.canvas.height) {
      this.canvas.width = w
      this.canvas.height = h
    }
    this.ctx?.setTransform(this.scale, 0, 0, this.scale, 0, 0) // draw in CSS-pixel space
    this.emberGrad = null
    this.still = null
    this.applyBounds()
    const backingH = rect.height * this.scale
    if (Math.abs(backingH - this.bakedFor) > this.bakedFor * REBAKE_TOLERANCE) this.bakeAll()
    if (!this.warmed) {
      this.warmed = true
      // Run the field forward so the pane opens on a lit fire. Bounds are known now.
      for (let t = 0; t < PREWARM_S; t += 1 / 30) this.field.tick(1 / 30)
    }
    if (this.running) this.draw()
    else this.renderStatic()
  }

  start(): void {
    if (this.running) return
    // Nothing ticks while stopped, so a resume must not replay a burst or a stale gust.
    this.field.resume()
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
    this.sheets.clear()
    this.ctx = null
  }

  /** One frame without the loop. Reduced motion gets the designed still frame. */
  renderStatic(): void {
    if (this.reducedMotion) this.drawStill()
    else this.draw()
  }

  /** The __kampGL handle (KAMP-780), used to measure with and without the blur. */
  devTarget(): GLDevTarget {
    return {
      tune: ({ scale, fps, blur }) => {
        if (scale != null && scale > 0) this.resScale = scale
        if (fps != null && fps > 0) this.frameMs = 1000 / fps
        if (blur != null && blur >= 0) this.blurCss = blur
        this.bakedFor = 0 // force a re-bake at the new scale or blur
        this.resize()
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
      },
      forcePop: () => this.field.forcePop()
    }
  }

  // --- loop -------------------------------------------------------------------------------

  private tick = (ts: number): void => {
    this.rafId = requestAnimationFrame(this.tick)
    if (this.lastTs === 0) this.lastTs = ts
    const dt = (ts - this.lastTs) / 1000
    this.lastTs = ts
    if (document.hidden) return
    this.field.tick(dt) // clamps dt itself
    if (!this.ember.settled) {
      this.ember.tick(Math.min(dt, 0.1) * 1000)
      this.emberGrad = null
    }
    if (ts - this.lastDraw < this.frameMs - FRAME_SLACK_MS) return
    this.lastDraw = ts
    this.draw()
  }

  // --- sprites ----------------------------------------------------------------------------

  private bake(gen: number): void {
    const colors = this.colorsByGen.get(gen)
    if (!colors || this.cssH === 0) return
    const paneH = this.cssH * this.scale
    const radii = SIZE_RADIUS.map((r) => r * paneH)
    this.sheets.set(gen, bakeSheet(colors, radii, this.blurCss * this.scale))
  }

  private bakeAll(): void {
    if (this.cssH === 0) return
    this.bakedFor = this.cssH * this.scale
    for (const gen of this.colorsByGen.keys()) this.bake(gen)
  }

  /** Drop generations no spark can use any more (the model caps how many live). */
  private pruneGenerations(): void {
    const oldest = this.field.oldestGeneration()
    for (const gen of [...this.colorsByGen.keys()]) {
      if (gen < oldest) {
        this.colorsByGen.delete(gen)
        this.sheets.delete(gen)
      }
    }
  }

  private sheetFor(gen: number): GlowSheet | undefined {
    return this.sheets.get(gen) ?? this.sheets.get(this.field.generation)
  }

  // --- drawing ------------------------------------------------------------------------------

  private applyBounds(): void {
    if (this.cssH === 0) return
    this.field.setBounds(this.cssW / this.cssH, this.art as PaneRect | null)
  }

  private drawBase(ctx: CanvasRenderingContext2D, w: number, h: number): void {
    ctx.globalCompositeOperation = 'source-over'
    ctx.globalAlpha = 1
    ctx.fillStyle = rgba(this.bg, 1)
    ctx.fillRect(0, 0, w, h)
    if (!this.emberGrad) {
      const colors = this.ember.current()
      const c = colors[0] ?? this.bg
      const g = ctx.createLinearGradient(0, h * (1 - EMBER_H), 0, h)
      g.addColorStop(0, rgba(c, 0))
      g.addColorStop(1, rgba(c, EMBER_ALPHA))
      this.emberGrad = g
    }
    ctx.fillStyle = this.emberGrad
    ctx.fillRect(0, h * (1 - EMBER_H), w, h * EMBER_H)
  }

  private draw(): void {
    const ctx = this.ctx
    if (!ctx || this.cssW === 0) return
    const w = this.cssW
    const h = this.cssH
    this.drawBase(ctx, w, h)
    ctx.globalCompositeOperation = 'lighter'
    const f = this.field
    for (let i = 0; i < POOL_SIZE; i++) {
      if (f.kind[i] === KIND_DEAD) continue
      const sheet = this.sheetFor(f.gen[i])
      if (!sheet) continue
      const px = f.x[i] * h // pane heights -> CSS px
      const py = f.y[i] * h
      this.drawSprite(ctx, sheet, f.slot[i], f.size[i], px, py, f.alpha(i), w, h)
    }
    ctx.globalCompositeOperation = 'source-over'
    ctx.globalAlpha = 1
    this.draws++
  }

  private drawStill(): void {
    const ctx = this.ctx
    if (!ctx || this.cssW === 0) return
    const w = this.cssW
    const h = this.cssH
    this.drawBase(ctx, w, h)
    this.still ??= stillFrame(w / h, this.art)
    const sheet = this.sheetFor(this.field.generation)
    if (sheet) {
      ctx.globalCompositeOperation = 'lighter'
      for (const s of this.still) {
        this.drawSprite(ctx, sheet, s.slot, s.size, s.x * h, s.y * h, s.alpha, w, h)
      }
    }
    ctx.globalCompositeOperation = 'source-over'
    ctx.globalAlpha = 1
  }

  private drawSprite(
    ctx: CanvasRenderingContext2D,
    sheet: GlowSheet,
    slot: number,
    size: number,
    px: number,
    py: number,
    alpha: number,
    w: number,
    h: number
  ): void {
    const half = sheet.half[size] / this.scale // CSS px
    // Cull only when the whole sprite is hidden behind the art, so a halo near
    // the art's edge never pops in and out.
    const a = this.art
    if (
      a &&
      px - half > a.x0 * w &&
      px + half < a.x1 * w &&
      py - half > a.y0 * h &&
      py + half < a.y1 * h
    ) {
      return
    }
    const k = alpha * this.titleQuiet(px / w, py / h)
    if (k < MIN_ALPHA) return
    const row = sheet.sprites[slot % sheet.sprites.length]
    const sprite = row?.[size]
    if (!sprite) return
    ctx.globalAlpha = Math.min(1, k)
    ctx.drawImage(sprite, px - half, py - half, half * 2, half * 2)
  }

  /** 1 away from the title text, TITLE_QUIET inside it, feathered between. */
  private titleQuiet(nx: number, ny: number): number {
    return titleQuiet(this.title, this.cssW / this.cssH, nx, ny, TITLE_QUIET, TITLE_FEATHER)
  }
}
