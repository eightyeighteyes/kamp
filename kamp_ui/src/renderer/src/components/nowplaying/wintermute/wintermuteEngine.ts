// KAMP-768: framework-free Canvas 2D renderer for the "Wintermute" Now Playing
// background: a subdued, multihued snowfall in album colors.
//
// Every rule lives in shared/wintermuteModel.ts (node-tested). This file only
// draws what the model hands it. Its public surface mirrors SparksEngine.
//
// Draw order: 1. the --bg fill; 2. a faint haze from the top, in the palette's
// first color (one cached gradient); 3. the flakes, far layer first, each a
// drawImage of a pre-baked sprite; 4. the close flake, its soft focus level at
// full strength with the sharp level over it at the focus amount. Two levels and
// no crossfade: a crossfade at m and 1-m reaches only about 75% opacity at its
// midpoint, a visible dip (the KAMP-774 trap). No CSS filter anywhere.

import {
  KIND_CLOSE,
  KIND_FLAKE,
  LAYER_RADIUS,
  LAYERS,
  POOL_SIZE,
  SnowField,
  stillFrame,
  type PaneRect
} from '../../../../../shared/wintermuteModel'
import { PaletteCrossfade } from '../../../../../shared/paletteCrossfade'
import { titleQuiet } from '../../../../../shared/particleUtil'
import { accentPalette, hexToRgb, type Palette, type RGB } from '../palette'
import type { GLDevTarget } from '../webgl/devHooks'
import type { ContentRect } from '../webgl/shaderEngine'
import {
  bakeCrystalMasks,
  bakeFlakeSheet,
  crystalHalf,
  tintCrystal,
  type CrystalPair,
  type FlakeSheet
} from './flakeSprites'

// --- tunables ------------------------------------------------------------------------
// Backing store fraction of the (capped) DPR. Flakes are soft discs, so half
// resolution reads fine and quarters the fill (as Sparks).
const RES_SCALE = 0.5
const MAX_DPR = 2
const DEFAULT_FPS = 30
// rAF timestamps jitter; without slack a frame cap skips an extra vsync (see shaderEngine).
const FRAME_SLACK_MS = 1.5
// Baked depth-of-field blur per layer, in CSS px, far to near. Part of the
// animation's definition, not a user setting. Far flakes are nearly in focus; near
// ones are well out of it, which is what gives the snowfall its depth.
const LAYER_BLUR_CSS = [1, 2.5, 6]
// Re-bake only when the backing pane height moves this much (as Sparks).
const REBAKE_TOLERANCE = 0.05
const CROSSFADE_MS = 1500 // the haze's recolor; the flakes recolor by turnover and dips
const HAZE_H = 0.35 // fraction of pane height
const HAZE_ALPHA = 0.07
const TITLE_QUIET = 0.25 // flake alpha behind the title text
const TITLE_FEATHER = 0.03 // of pane height
const MIN_ALPHA = 0.01
const DEFAULT_BG: RGB = { r: 0x14, g: 0x14, b: 0x14 }

export interface WintermuteEngineOptions {
  /** prefers-reduced-motion: show the designed still frame. */
  reducedMotion: boolean
}

const rgba = (c: RGB, a: number): string =>
  `rgba(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)},${a})`

export class WintermuteEngine {
  private ctx: CanvasRenderingContext2D | null
  private readonly field = new SnowField(Math.random)
  private readonly reducedMotion: boolean
  private cssW = 0
  private cssH = 0
  private scale = 1
  private resScale = RES_SCALE
  private frameMs = 1000 / DEFAULT_FPS
  private blurK = 1 // multiplier on LAYER_BLUR_CSS (dev tune)
  private layers = LAYERS // draw only the first N layers (dev tune)
  private bg: RGB = DEFAULT_BG
  private fallback: RGB[]
  private readonly haze: PaletteCrossfade

  // Palette generation -> its colors, its flake sheet and its tinted crystal.
  private readonly colorsByGen = new Map<number, RGB[]>()
  private readonly sheets = new Map<number, FlakeSheet>()
  private readonly crystals = new Map<number, CrystalPair>()
  private masks: CrystalPair | null = null
  private bakedFor = 0 // backing pane height the sheets were baked at
  private hazeGrad: CanvasGradient | null = null

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
    opts: WintermuteEngineOptions
  ) {
    this.ctx = canvas.getContext('2d')
    this.reducedMotion = opts.reducedMotion
    this.fallback = initial.colors.length > 0 ? initial.colors : accentPalette('#7c86e1').colors
    this.haze = new PaletteCrossfade(this.fallback, CROSSFADE_MS)
    this.colorsByGen.set(this.field.setPalette(false), this.fallback)
  }

  private get running(): boolean {
    return this.rafId != null
  }

  // --- contract ------------------------------------------------------------------------

  setBg(hex: string): void {
    this.bg = hexToRgb(hex) ?? DEFAULT_BG
    this.hazeGrad = null
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
    this.haze.set(colors, this.running)
    this.hazeGrad = null
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
    this.hazeGrad = null
    this.still = null
    this.applyBounds()
    const backingH = rect.height * this.scale
    if (Math.abs(backingH - this.bakedFor) > this.bakedFor * REBAKE_TOLERANCE) this.bakeAll()
    if (!this.warmed) {
      // The pane opens on a snowfall already under way. Bounds are known now.
      this.warmed = true
      this.field.prewarm()
    }
    if (this.running) this.draw()
    else this.renderStatic()
  }

  start(): void {
    if (this.running) return
    // Nothing ticks while stopped, so a resume must not replay a gust or a sweep.
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
    this.crystals.clear()
    this.masks = null
    this.ctx = null
  }

  /** One frame without the loop. Reduced motion gets the designed still frame. */
  renderStatic(): void {
    if (this.reducedMotion) this.drawStill()
    else this.draw()
  }

  /** The __kampGL handle (KAMP-780), used to measure the blur and layer variants. */
  devTarget(): GLDevTarget {
    return {
      tune: ({ scale, fps, blur, layers }) => {
        if (scale != null && scale > 0) this.resScale = scale
        if (fps != null && fps > 0) this.frameMs = 1000 / fps
        if (blur != null && blur >= 0) this.blurK = blur / LAYER_BLUR_CSS[LAYERS - 1]
        if (layers != null && layers >= 0) this.layers = Math.min(LAYERS, Math.floor(layers))
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
      forcePop: () => this.field.forceClose()
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
    if (!this.haze.settled) {
      this.haze.tick(Math.min(dt, 0.1) * 1000)
      this.hazeGrad = null
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
    const radii = LAYER_RADIUS.map((r) => r * paneH)
    const blurs = LAYER_BLUR_CSS.map((b) => b * this.blurK * this.scale)
    this.sheets.set(gen, bakeFlakeSheet(colors, radii, blurs))
  }

  private bakeAll(): void {
    if (this.cssH === 0) return
    this.bakedFor = this.cssH * this.scale
    for (const gen of this.colorsByGen.keys()) this.bake(gen)
  }

  /** Drop generations no flake shows any more. */
  private pruneGenerations(): void {
    const oldest = this.field.oldestGenerationInUse()
    for (const gen of [...this.colorsByGen.keys()]) {
      if (gen < oldest) {
        this.colorsByGen.delete(gen)
        this.sheets.delete(gen)
        this.crystals.delete(gen)
      }
    }
  }

  private sheetFor(gen: number): FlakeSheet | undefined {
    return this.sheets.get(gen) ?? this.sheets.get(this.field.generation)
  }

  /** The close flake's crystal in a generation's first color, tinted on first use. */
  private crystalFor(gen: number): CrystalPair | undefined {
    const cached = this.crystals.get(gen)
    if (cached) return cached
    const colors = this.colorsByGen.get(gen) ?? this.colorsByGen.get(this.field.generation)
    if (!colors || colors.length === 0) return undefined
    this.masks ??= bakeCrystalMasks()
    const pair = tintCrystal(this.masks, colors[0])
    this.crystals.set(gen, pair)
    return pair
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
    if (!this.hazeGrad) {
      const c = this.haze.current()[0] ?? this.bg
      const g = ctx.createLinearGradient(0, 0, 0, h * HAZE_H)
      g.addColorStop(0, rgba(c, HAZE_ALPHA))
      g.addColorStop(1, rgba(c, 0))
      this.hazeGrad = g
    }
    ctx.fillStyle = this.hazeGrad
    ctx.fillRect(0, 0, w, h * HAZE_H)
  }

  private draw(): void {
    const ctx = this.ctx
    if (!ctx || this.cssW === 0) return
    const w = this.cssW
    const h = this.cssH
    this.drawBase(ctx, w, h)
    const f = this.field
    // Far to near, so nearer flakes pass in front.
    for (let layer = 0; layer < this.layers; layer++) {
      for (let i = 0; i < POOL_SIZE; i++) {
        if (f.kind[i] !== KIND_FLAKE || f.layer[i] !== layer) continue
        const sheet = this.sheetFor(f.gen[i])
        if (!sheet) continue
        this.drawSprite(ctx, sheet, f.slot[i], layer, f.x[i] * h, f.y[i] * h, f.alpha(i), w, h)
      }
    }
    const c = f.closeIndex
    if (c >= 0 && f.kind[c] === KIND_CLOSE) this.drawClose(ctx, c, w, h)
    ctx.globalAlpha = 1
    this.draws++
  }

  private drawStill(): void {
    const ctx = this.ctx
    if (!ctx || this.cssW === 0) return
    const w = this.cssW
    const h = this.cssH
    this.drawBase(ctx, w, h)
    this.still ??= stillFrame(w / h, this.art, this.title)
    const sheet = this.sheetFor(this.field.generation)
    if (sheet) {
      for (let layer = 0; layer < this.layers; layer++) {
        for (const s of this.still) {
          if (s.layer !== layer) continue
          this.drawSprite(ctx, sheet, s.slot, layer, s.x * h, s.y * h, s.alpha, w, h)
        }
      }
    }
    ctx.globalAlpha = 1
  }

  private drawSprite(
    ctx: CanvasRenderingContext2D,
    sheet: FlakeSheet,
    slot: number,
    layer: number,
    px: number,
    py: number,
    alpha: number,
    w: number,
    h: number
  ): void {
    const half = sheet.half[layer] / this.scale // CSS px
    // Cull only when the whole sprite is hidden behind the art, so a flake near
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
    const sprite = sheet.sprites[slot % sheet.sprites.length]?.[layer]
    if (!sprite) return
    ctx.globalAlpha = Math.min(1, k)
    ctx.drawImage(sprite, px - half, py - half, half * 2, half * 2)
  }

  private drawClose(ctx: CanvasRenderingContext2D, i: number, w: number, h: number): void {
    const f = this.field
    const crystal = this.crystalFor(f.gen[i])
    if (!crystal) return
    const px = f.x[i] * h
    const py = f.y[i] * h
    const alpha = f.alpha(i) * this.titleQuiet(px / w, py / h)
    if (alpha < MIN_ALPHA) return
    const half = crystalHalf(f.closeRadius(i) * h)
    const focus = f.focus(i)
    ctx.save()
    ctx.translate(px, py)
    ctx.rotate(f.angle(i))
    // Soft at full strength underneath, sharp over it at the focus amount: the
    // total never dips the way an m / 1-m crossfade does.
    ctx.globalAlpha = Math.min(1, alpha)
    ctx.drawImage(crystal.soft, -half, -half, half * 2, half * 2)
    if (focus > 0) {
      ctx.globalAlpha = Math.min(1, alpha * focus)
      ctx.drawImage(crystal.sharp, -half, -half, half * 2, half * 2)
    }
    ctx.restore()
  }

  /** 1 away from the title text, TITLE_QUIET inside it, feathered between. */
  private titleQuiet(nx: number, ny: number): number {
    return titleQuiet(this.title, this.cssW / this.cssH, nx, ny, TITLE_QUIET, TITLE_FEATHER)
  }
}
