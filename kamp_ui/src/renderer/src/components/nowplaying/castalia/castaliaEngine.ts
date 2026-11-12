// KAMP-773: framework-free Canvas 2D renderer for the "Castalia" Now Playing
// background: fine grains in album colors settling into a Chladni figure.
//
// Every rule lives in shared/castaliaModel.ts (node-tested). This file only
// draws what the model hands it. Its public surface mirrors WintermuteEngine,
// plus setIdentity, which the shared lifecycle calls before every trackChanged.
//
// Draw order: 1. the --bg fill (the dark plate); 2. the grains, each a drawImage
// of a pre-baked sprite. No CSS filter anywhere: the ~2px blur is baked into the
// sprite (KAMP-776/777).

import { CastaliaPlate, GRAINS, MODES } from '../../../../../shared/castaliaModel'
import { titleQuiet } from '../../../../../shared/particleUtil'
import { accentPalette, hexToRgb, type Palette, type RGB } from '../palette'
import type { GLDevTarget } from '../webgl/devHooks'
import type { ContentRect } from '../webgl/shaderEngine'
import { bakeGrainSheet, type GrainSheet } from './grainSprites'

// --- tunables ------------------------------------------------------------------------
// Backing store fraction of the (capped) DPR. Fine grains looked bad at the half
// resolution Sparks and Wintermute use; 0.75 reads fine and, with 20 fps, measured
// 19.9% GPU busy against full resolution's 47% (KAMP-773, TUNING.md).
const RES_SCALE = 0.75
const MAX_DPR = 2
// The motion is slow settling, so 20 fps reads fine and was signed off in review.
// It is every third vsync at 60 Hz; full resolution makes frames the lever (KAMP-776).
const DEFAULT_FPS = 20
// rAF timestamps jitter; without slack a frame cap skips an extra vsync (see shaderEngine).
const FRAME_SLACK_MS = 1.5
// The grain's baked halo, CSS px. Part of the animation's definition, not a user setting.
const BLUR_CSS = 2
// The grain's radius. 0.5 (a one-pixel grain) nearly vanished on the dark plate
// in review; 0.75 is a 1.5 px grain, still fine sand.
const CORE_CSS = 0.75
const TITLE_QUIET = 0.3 // grain alpha behind the title text
const TITLE_FEATHER = 0.03 // of pane height
const MIN_ALPHA = 0.01
const DEFAULT_BG: RGB = { r: 0x14, g: 0x14, b: 0x14 }

const rgba = (c: RGB, a: number): string =>
  `rgba(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)},${a})`

export class CastaliaEngine {
  private ctx: CanvasRenderingContext2D | null
  private readonly plate = new CastaliaPlate(Math.random)
  private cssW = 0
  private cssH = 0
  private scale = 1
  private resScale = RES_SCALE
  private frameMs = 1000 / DEFAULT_FPS
  private blurCss = BLUR_CSS
  private bg: RGB = DEFAULT_BG
  private fallback: RGB[]

  // Palette generation -> its colors and its grain sheet.
  private readonly colorsByGen = new Map<number, RGB[]>()
  private readonly sheets = new Map<number, GrainSheet>()
  private bakedScale = 0 // backing scale the sheets were baked at

  private title: ContentRect | null = null
  private art: ContentRect | null = null
  private warmed = false

  private rafId: number | null = null
  private lastTs = 0
  private lastDraw = 0
  private draws = 0
  private drawsSince = performance.now()

  constructor(
    private readonly canvas: HTMLCanvasElement,
    initial: Palette
  ) {
    this.ctx = canvas.getContext('2d')
    this.fallback = initial.colors.length > 0 ? initial.colors : accentPalette('#7c86e1').colors
    this.colorsByGen.set(this.plate.setPalette(false), this.fallback)
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
    this.fallback = accentPalette(hex).colors
  }

  setPalette(next: Palette): void {
    const colors = next.colors.length > 0 ? next.colors : this.fallback
    // KAMP-779: while stopped the model moves every grain to the new generation
    // at once, so the still frame lands on the new colors.
    const gen = this.plate.setPalette(this.running)
    this.colorsByGen.set(gen, colors)
    this.pruneGenerations()
    this.bake(gen)
    if (!this.running) this.renderStatic()
  }

  /** The album and track keys; always arrives before trackChanged. */
  setIdentity(albumKey: string, trackKey: string): void {
    this.plate.setIdentity(albumKey, trackKey, this.running)
    if (!this.running) this.renderStatic()
  }

  /** id to a different id; the model ignores it while stopped. */
  trackChanged(): void {
    this.plate.trackChanged(this.running)
  }

  /** Normalized pane position, -1..1 (as BokehEngine). */
  setPointer(nx: number, ny: number): void {
    if (this.cssH === 0) return
    const aspect = this.cssW / this.cssH
    this.plate.pointerMove(((nx + 1) / 2) * aspect, (ny + 1) / 2)
  }

  pointerLeave(): void {
    this.plate.pointerLeave()
  }

  /** The title's text box and the art's box (normalized), each null while hidden. */
  setContentRects(title: ContentRect | null, art: ContentRect | null): void {
    this.title = title
    this.art = art
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
    this.plate.setBounds(rect.width / rect.height)
    if (this.scale !== this.bakedScale) this.bakeAll()
    if (!this.warmed) {
      // The pane opens on the settled figure; nothing forms on mount.
      this.warmed = true
      this.plate.prewarm()
    }
    this.draw()
  }

  start(): void {
    if (this.running) return
    // Nothing ticks while stopped, so a resume must not replay a ring or a sweep.
    this.plate.resume()
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

  /**
   * One frame without the loop. The model never ticks under reduced motion, so the
   * prewarmed grains are the settled figure: that is the still frame.
   */
  renderStatic(): void {
    this.draw()
  }

  /** The __kampGL handle (KAMP-780), used to measure the blur variants and review modes. */
  devTarget(): GLDevTarget {
    return {
      tune: ({ scale, fps, blur, mode }) => {
        if (scale != null && scale > 0) this.resScale = scale
        if (fps != null && fps > 0) this.frameMs = 1000 / fps
        if (blur != null && blur >= 0) this.blurCss = blur
        if (mode != null) {
          this.plate.ringTo(Math.floor(mode))
          const m = MODES[this.plate.mode]
          console.info(`[castalia] mode ${this.plate.mode}: (${m.m}, ${m.n}, ${m.sign})`)
        }
        this.bakedScale = 0 // force a re-bake at the new scale or blur
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
      forcePop: () => this.plate.forceShimmer()
    }
  }

  // --- loop -------------------------------------------------------------------------------

  private tick = (ts: number): void => {
    this.rafId = requestAnimationFrame(this.tick)
    if (this.lastTs === 0) this.lastTs = ts
    const dt = (ts - this.lastTs) / 1000
    this.lastTs = ts
    if (document.hidden) return
    this.plate.tick(dt) // clamps dt itself
    if (ts - this.lastDraw < this.frameMs - FRAME_SLACK_MS) return
    this.lastDraw = ts
    this.draw()
  }

  // --- sprites ----------------------------------------------------------------------------

  private bake(gen: number): void {
    const colors = this.colorsByGen.get(gen)
    if (!colors || this.scale === 0) return
    this.sheets.set(gen, bakeGrainSheet(colors, CORE_CSS * this.scale, this.blurCss * this.scale))
  }

  private bakeAll(): void {
    this.bakedScale = this.scale
    for (const gen of this.colorsByGen.keys()) this.bake(gen)
  }

  /** Drop generations no grain shows any more. */
  private pruneGenerations(): void {
    const oldest = this.plate.oldestGenerationInUse()
    for (const gen of [...this.colorsByGen.keys()]) {
      if (gen < oldest) {
        this.colorsByGen.delete(gen)
        this.sheets.delete(gen)
      }
    }
  }

  // --- drawing ------------------------------------------------------------------------------

  private draw(): void {
    const ctx = this.ctx
    if (!ctx || this.cssW === 0) return
    const w = this.cssW
    const h = this.cssH
    ctx.globalCompositeOperation = 'source-over'
    ctx.globalAlpha = 1
    ctx.fillStyle = rgba(this.bg, 1)
    ctx.fillRect(0, 0, w, h)
    const p = this.plate
    const fallbackSheet = this.sheets.get(p.generation)
    const a = this.art
    // Every sprite shares one size, so the cull box is computed once.
    const half = (fallbackSheet?.half ?? 1) / this.scale // CSS px
    const ax0 = a ? a.x0 * w + half : 0
    const ax1 = a ? a.x1 * w - half : 0
    const ay0 = a ? a.y0 * h + half : 0
    const ay1 = a ? a.y1 * h - half : 0
    for (let i = 0; i < GRAINS; i++) {
      const px = p.u[i] * w
      const py = p.v[i] * h
      // Skip a grain only when its whole sprite is behind the art.
      if (a && px > ax0 && px < ax1 && py > ay0 && py < ay1) continue
      const k = p.alpha(i) * this.titleQuiet(px / w, py / h)
      if (k < MIN_ALPHA) continue
      const sheet = this.sheets.get(p.gen[i]) ?? fallbackSheet
      const sprite = sheet?.sprites[p.slot[i] % sheet.sprites.length]
      if (!sprite) continue
      ctx.globalAlpha = Math.min(1, k)
      ctx.drawImage(sprite, px - half, py - half, half * 2, half * 2)
    }
    ctx.globalAlpha = 1
    this.draws++
  }

  /** 1 away from the title text, TITLE_QUIET inside it, feathered between. */
  private titleQuiet(nx: number, ny: number): number {
    return titleQuiet(this.title, this.cssW / this.cssH, nx, ny, TITLE_QUIET, TITLE_FEATHER)
  }
}
