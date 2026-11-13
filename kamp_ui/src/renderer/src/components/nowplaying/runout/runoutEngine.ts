// KAMP-771: framework-free Canvas 2D renderer for the "Runout" Now Playing
// background: a macro view of a spinning record's grooves under a lamp.
//
// Every rule lives in shared/runoutModel.ts and shared/runoutGrooves.ts
// (node-tested). This file only draws what they hand it. Its public surface is
// the shared particle lifecycle's (useParticleEngine), plus setPlayback.
//
// Geometry: the record's centre sits below and left of the pane, so the view
// shows the top of the record and the grooves cross it as long, shallow arcs,
// carried left to right. Draw order: 1. the plate, --bg with the lamp's sheen,
// painted on a small canvas and stretched (Castalia, KAMP-773); 2. the grooves,
// batched by alpha into a few strokes; 3. the gap band's gloss; 4. the lock
// groove and matrix number; 5. dust. No CSS filter anywhere (KAMP-776/777).

import { PaletteCrossfade } from '../../../../../shared/paletteCrossfade'
import { titleQuiet } from '../../../../../shared/particleUtil'
import { cutAlpha, grooveLevel, lockS, spiralS } from '../../../../../shared/runoutGrooves'
import { GAP_W, GROOVES, RunoutPlatter } from '../../../../../shared/runoutModel'
import { matrixNumber, type RunoutPlayback } from '../../../../../shared/runoutPlayback'
import { accentPalette, hexToRgb, type Palette, type RGB } from '../palette'
import type { GLDevTarget } from '../webgl/devHooks'
import type { ContentRect } from '../webgl/shaderEngine'

// --- tunables ------------------------------------------------------------------------
// Backing store fraction of the (capped) DPR, and the frame cap. The ticket's
// starting point; measured and tuned in TUNING.md.
const RES_SCALE = 0.5
const MAX_DPR = 2
const DEFAULT_FPS = 30
// rAF timestamps jitter; without slack a frame cap skips an extra vsync (see shaderEngine).
const FRAME_SLACK_MS = 1.5
// The record's centre, as fractions of the pane: across from the left, and below
// the bottom edge in pane heights.
const CENTRE_X = 0.3
const CENTRE_BELOW = 1.1
// Groove stroke, CSS px, floored at one backing pixel so it can't alias away.
const GROOVE_W_CSS = 1.1
// Groove alpha: the floor, and the most the pattern and the lamp add. Low on
// purpose: high-contrast fine lines moiré against the backing grid.
const GROOVE_ALPHA_MIN = 0.06
const GROOVE_ALPHA_SPAN = 0.22
const GROOVE_ALPHA_MAX = GROOVE_ALPHA_MIN + GROOVE_ALPHA_SPAN
// Segments are batched into this many strokes, binned over 0..GROOVE_ALPHA_MAX.
// One bin is ~0.03 alpha, below what the eye separates at this contrast.
const ALPHA_LEVELS = 10
const SEGMENTS = 28 // per turn across the view
const TITLE_QUIET = 0.35
const TITLE_FEATHER = 0.04 // of pane height
// The lamp: where its highlight crosses the record (fraction across the view),
// its angular width, and how much it lifts the grooves it lights.
const LAMP_AT = 0.62
const LAMP_SIGMA = 0.22 // rad
const LAMP_LIFT = 0.6
// The sheen: strength of the album colour over --bg at the lamp's core, and the
// highlight's shape (CSS px along the radius vs across it, in pane heights).
const SHEEN_CORE = 0.2
const SHEEN_HALO = 0.1
const SHEEN_LONG = 1.1
const SHEEN_WIDE = 0.3
const SHEEN_W = 64 // width of the small canvas the sheen is painted on
const GROOVE_TINT = 0.55 // groove colour, the album colour mixed this far toward white
// The gap band's gloss: alpha of its wide wash and its bright centre line.
const GLOSS_WASH = 0.08
const GLOSS_LINE = 0.2
const LOCK_ALPHA = 0.32
const MATRIX_ALPHA = 0.32
const DUST_ALPHA = 0.85
const DUST_CSS = 1.3 // radius
const DEFAULT_BG: RGB = { r: 0x14, g: 0x14, b: 0x14 }
const COLOR_CROSSFADE_MS = 1500
const WHITE: RGB = { r: 255, g: 255, b: 255 }

const rgba = (c: RGB, a: number): string =>
  `rgba(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)},${a})`

const mix = (a: RGB, b: RGB, t: number): RGB => ({
  r: a.r + (b.r - a.r) * t,
  g: a.g + (b.g - a.g) * t,
  b: a.b + (b.b - a.b) * t
})

/** [sheen core, sheen halo, groove tint] from an album palette. */
function lampColors(colors: readonly RGB[]): RGB[] {
  const core = colors[0]
  const halo = colors[1] ?? core
  return [core, halo, mix(core, WHITE, GROOVE_TINT)]
}

export class RunoutEngine {
  private ctx: CanvasRenderingContext2D | null
  private readonly platter = new RunoutPlatter(Math.random)
  private readonly lamp: PaletteCrossfade
  private cssW = 0
  private cssH = 0
  private scale = 1
  private resScale = RES_SCALE
  // Dev-only measurement knobs (see devTarget); the constants ship.
  private alphaLevels = ALPHA_LEVELS
  private segments = SEGMENTS
  private groovesOn = true
  private frameMs = 1000 / DEFAULT_FPS
  private bg: RGB = DEFAULT_BG
  private fallback: RGB[]

  // Geometry, CSS px, set on resize.
  private cx = 0
  private cy = 0
  private rOut = 0
  private pitch = 1
  private view0 = -2
  private view1 = -1

  private sheenCanvas: OffscreenCanvas | null = null
  private sheenCtx: OffscreenCanvasRenderingContext2D | null = null
  private sheenDirty = true

  private title: ContentRect | null = null
  private art: ContentRect | null = null
  private albumKey = ''
  private disc = 1
  private matrix = 'A-1'

  private rafId: number | null = null
  private lastTs = 0
  private lastDraw = 0
  // Something outside the model changed while running and idle: draw once more.
  private dirty = true
  private draws = 0
  private drawsSince = performance.now()

  // One path per alpha level, reused every frame.
  private readonly levels: Path2D[] = []

  // No options: reduced motion needs no branch here. The loop never starts, so the
  // model never ticks, and the landed snapshot is the still frame (as Castalia).
  constructor(
    private readonly canvas: HTMLCanvasElement,
    initial: Palette
  ) {
    this.ctx = canvas.getContext('2d')
    this.fallback = initial.colors.length > 0 ? initial.colors : accentPalette('#7c86e1').colors
    this.lamp = new PaletteCrossfade(lampColors(this.fallback), COLOR_CROSSFADE_MS)
  }

  private get running(): boolean {
    return this.rafId != null
  }

  /** Repaint now while stopped; draw once more while running even if idle. */
  private changed(): void {
    if (this.running) this.dirty = true
    else this.renderStatic()
  }

  // --- contract ------------------------------------------------------------------------

  setBg(hex: string): void {
    this.bg = hexToRgb(hex) ?? DEFAULT_BG
    this.sheenDirty = true
    this.changed()
  }

  setAccent(hex: string): void {
    this.fallback = accentPalette(hex).colors
  }

  setPalette(next: Palette): void {
    const colors = next.colors.length > 0 ? next.colors : this.fallback
    // KAMP-779: while stopped nothing advances the fade, so land on the new colours.
    this.lamp.set(lampColors(colors), this.running)
    this.sheenDirty = true
    this.changed()
  }

  setIdentity(albumKey: string, trackKey: string): void {
    this.albumKey = albumKey
    this.matrix = matrixNumber(albumKey, this.disc)
    this.platter.setIdentity(albumKey, trackKey, this.running)
    this.changed()
  }

  trackChanged(): void {
    this.platter.trackChanged(this.running)
  }

  setPlayback(p: RunoutPlayback): void {
    if (p.disc !== this.disc) {
      this.disc = p.disc
      this.matrix = matrixNumber(this.albumKey, p.disc)
    }
    this.platter.setPlayback(p, this.running)
    if (!this.running) this.renderStatic()
  }

  /** Normalized pane position, -1..1 (as BokehEngine). Only sideways travel nudges, so y is unused. */
  setPointer(nx: number): void {
    if (this.cssH === 0) return
    this.platter.pointerMove(((nx + 1) / 2) * (this.cssW / this.cssH))
  }

  pointerLeave(): void {
    this.platter.pointerLeave()
  }

  setContentRects(title: ContentRect | null, art: ContentRect | null): void {
    this.title = title
    this.art = art
    this.changed()
  }

  /** Size the backing store to the element. Assigning width/height clears it, so repaint. */
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
    this.layout()
    this.sheenDirty = true
    this.draw()
  }

  start(): void {
    if (this.running) return
    this.platter.resume()
    this.lastTs = 0
    this.lastDraw = 0
    this.dirty = true
    this.rafId = requestAnimationFrame(this.tick)
  }

  stop(): void {
    if (this.rafId != null) cancelAnimationFrame(this.rafId)
    this.rafId = null
  }

  destroy(): void {
    this.stop()
    this.ctx = null
    this.sheenCanvas = null
    this.sheenCtx = null
  }

  renderStatic(): void {
    this.draw()
  }

  devTarget(): GLDevTarget {
    return {
      tune: ({ scale, fps, grooves, segments, levels }) => {
        if (grooves != null) this.groovesOn = grooves === 'on'
        if (segments != null && segments >= 1) this.segments = Math.floor(segments)
        if (levels != null && levels >= 1) this.alphaLevels = Math.floor(levels)
        if (scale != null && scale > 0) this.resScale = scale
        if (fps != null && fps > 0) this.frameMs = 1000 / fps
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
      forcePop: () => this.platter.forceDust()
    }
  }

  // --- loop -------------------------------------------------------------------------------

  private tick = (ts: number): void => {
    this.rafId = requestAnimationFrame(this.tick)
    if (this.lastTs === 0) this.lastTs = ts
    const dt = (ts - this.lastTs) / 1000
    this.lastTs = ts
    if (document.hidden) return
    this.platter.tick(dt) // clamps dt itself
    if (!this.lamp.settled) {
      this.lamp.tick(Math.min(dt, 0.1) * 1000)
      this.sheenDirty = true
    }
    // A paused, settled record changes nothing: skip the draw (and its GPU cost).
    if (this.platter.idle && this.lamp.settled && !this.dirty) return
    if (ts - this.lastDraw < this.frameMs - FRAME_SLACK_MS) return
    this.lastDraw = ts
    this.dirty = false
    this.draw()
  }

  // --- geometry ---------------------------------------------------------------------------

  /** Place the record under the pane and tell the model which angles are in view. */
  private layout(): void {
    const w = this.cssW
    const h = this.cssH
    this.cx = CENTRE_X * w
    this.cy = h + CENTRE_BELOW * h
    const corners: Array<[number, number]> = [
      [0, 0],
      [w, 0],
      [0, h],
      [w, h]
    ]
    const angles = corners.map(([x, y]) => Math.atan2(y - this.cy, x - this.cx))
    const radii = corners.map(([x, y]) => Math.hypot(x - this.cx, y - this.cy))
    // The centre is below the pane, so every angle is in (-π, 0): no wrap.
    this.view0 = Math.min(...angles)
    this.view1 = Math.max(...angles)
    // The nearest point of the pane is straight above the centre.
    const rNear = this.cy - h
    const rFar = Math.max(...radii)
    this.rOut = rFar
    this.pitch = (rFar - rNear) / GROOVES
    this.platter.setView(this.view0, this.view1)
  }

  private radius(x: number, e: number): number {
    return this.rOut - this.pitch * spiralS(x, e)
  }

  // --- drawing ------------------------------------------------------------------------------

  private draw(): void {
    const ctx = this.ctx
    if (!ctx || this.cssW === 0) return
    ctx.globalCompositeOperation = 'source-over'
    ctx.globalAlpha = 1
    this.drawSheen(ctx)
    const tint = this.lamp.current()[2]
    if (this.groovesOn) this.drawGrooves(ctx, tint)
    this.drawBand(ctx, tint)
    this.drawRunout(ctx, tint)
    this.drawDust(ctx)
    ctx.globalAlpha = 1
    this.draws++
  }

  /**
   * The plate: --bg with the lamp's highlight, a soft streak along the radius
   * through the lamp (a record's highlight runs across its grooves). Painted
   * opaque on a small canvas, stretched over the pane, repainted only when the
   * colours, --bg or the size change.
   */
  private drawSheen(ctx: CanvasRenderingContext2D): void {
    const w = this.cssW
    const h = this.cssH
    const sw = SHEEN_W
    const sh = Math.max(1, Math.round((SHEEN_W * h) / w))
    if (!this.sheenCanvas || this.sheenCanvas.height !== sh) {
      this.sheenCanvas = new OffscreenCanvas(sw, sh)
      this.sheenCtx = this.sheenCanvas.getContext('2d')
      this.sheenDirty = true
    }
    const sc = this.sheenCtx
    if (!sc) return
    if (this.sheenDirty) {
      this.sheenDirty = false
      const [core, halo] = this.lamp.current()
      const k = sw / w // small-canvas px per CSS px
      sc.setTransform(1, 0, 0, 1, 0, 0)
      sc.fillStyle = rgba(this.bg, 1)
      sc.fillRect(0, 0, sw, sh)
      const a = this.lampAngle()
      // The streak's middle: halfway across the record's radii in view.
      const r = this.rOut - (this.pitch * GROOVES) / 2
      sc.setTransform(k, 0, 0, k, 0, 0)
      sc.translate(this.cx + r * Math.cos(a), this.cy + r * Math.sin(a))
      sc.rotate(a) // x now runs along the radius
      sc.scale(SHEEN_LONG / SHEEN_WIDE, 1)
      const rr = (SHEEN_WIDE * h) / 2
      for (const [c, s, reach] of [
        [halo, SHEEN_HALO, 2],
        [core, SHEEN_CORE, 1]
      ] as Array<[RGB, number, number]>) {
        const g = sc.createRadialGradient(0, 0, 0, 0, 0, rr * reach)
        g.addColorStop(0, rgba(mix(this.bg, c, s), 1))
        g.addColorStop(1, rgba(mix(this.bg, c, s), 0))
        sc.fillStyle = g
        sc.fillRect(-rr * reach, -rr * reach, rr * reach * 2, rr * reach * 2)
      }
    }
    ctx.imageSmoothingEnabled = true
    ctx.drawImage(this.sheenCanvas, 0, 0, w, h)
  }

  private lampAngle(): number {
    return this.view0 + LAMP_AT * (this.view1 - this.view0)
  }

  /**
   * Every visible turn of the spiral as short segments, each binned by alpha into
   * one of ALPHA_LEVELS paths, so the whole record is a handful of strokes.
   */
  private drawGrooves(ctx: CanvasRenderingContext2D, tint: RGB): void {
    const p = this.platter
    const e = p.runout
    const band = p.gapX
    const lamp = this.lampAngle()
    const aspect = this.cssW / this.cssH
    const w = this.cssW
    const h = this.cssH
    const art = this.art
    for (let i = 0; i < this.alphaLevels; i++) this.levels[i] = new Path2D()
    const t0 = this.view0
    const dt = (this.view1 - this.view0) / this.segments
    // Turns whose spacing coordinate can land in view: x <= s, so up to the far edge.
    for (let k = -1; k <= GROOVES + 1; k++) {
      let prevLevel = -1
      let px = 0
      let py = 0
      let pPhi = 0
      for (let j = 0; j <= this.segments; j++) {
        const theta = t0 + j * dt
        const phi = (((theta - p.angle) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI)
        const x = k + phi / (2 * Math.PI)
        const s = spiralS(x, e)
        const r = this.radius(x, e)
        const qx = this.cx + r * Math.cos(theta)
        const qy = this.cy + r * Math.sin(theta)
        // A segment ends here. Skip it across the spiral's seam (phi wrapped).
        if (j > 0 && phi > pPhi) {
          const mx = (px + qx) / 2
          const my = (py + qy) / 2
          const hidden =
            art != null && mx > art.x0 * w && mx < art.x1 * w && my > art.y0 * h && my < art.y1 * h
          // Behind the art a segment is never seen: skip its fill entirely.
          const a = hidden
            ? 0
            : this.segmentAlpha(p.seedAt(x), x, s, e, band, theta, lamp) *
              titleQuiet(this.title, aspect, mx / w, my / h, TITLE_QUIET, TITLE_FEATHER)
          const level = Math.min(
            this.alphaLevels - 1,
            Math.floor((a / GROOVE_ALPHA_MAX) * this.alphaLevels)
          )
          // Below half the first bin rounds to nothing: a fade reaches zero, not the floor.
          if (a >= GROOVE_ALPHA_MAX / this.alphaLevels / 2) {
            const path = this.levels[level]
            if (level !== prevLevel) path.moveTo(px, py)
            path.lineTo(qx, qy)
            prevLevel = level
          } else prevLevel = -1
        } else prevLevel = -1
        px = qx
        py = qy
        pPhi = phi
      }
    }
    ctx.lineCap = 'butt'
    ctx.lineWidth = Math.max(GROOVE_W_CSS, 1 / this.scale)
    ctx.strokeStyle = rgba(tint, 1)
    for (let i = 0; i < this.alphaLevels; i++) {
      ctx.globalAlpha = GROOVE_ALPHA_MAX * ((i + 0.5) / this.alphaLevels) // the middle of its bin
      ctx.stroke(this.levels[i])
    }
    ctx.globalAlpha = 1
  }

  /**
   * A segment's alpha, 0..GROOVE_ALPHA_MAX: the album's pattern lit by the lamp,
   * then faded (multiplied, so a fade reaches zero) past the lock and across the
   * band.
   */
  private segmentAlpha(
    seed: number,
    x: number,
    s: number,
    e: number,
    band: number | null,
    theta: number,
    lamp: number
  ): number {
    const lit = Math.exp(-(((theta - lamp) / LAMP_SIGMA) ** 2))
    const bright = grooveLevel(seed, x) * (1 - LAMP_LIFT + LAMP_LIFT * lit)
    let a = GROOVE_ALPHA_MIN + GROOVE_ALPHA_SPAN * bright
    a *= cutAlpha(s, e)
    if (band != null) {
      // The gap between tracks: no grooves across the band, feathered.
      const d = Math.abs(x - band) / GAP_W
      a *= Math.min(1, Math.max(0, (d - 0.55) / 0.45))
    }
    return a
  }

  /** The gap between tracks: a smooth, glossy ring where the band is. */
  private drawBand(ctx: CanvasRenderingContext2D, tint: RGB): void {
    const band = this.platter.gapX
    if (band == null) return
    const r = this.radius(band, this.platter.runout)
    if (r <= 0) return
    ctx.strokeStyle = rgba(tint, 1)
    ctx.beginPath()
    ctx.arc(this.cx, this.cy, r, this.view0, this.view1)
    ctx.globalAlpha = GLOSS_WASH
    ctx.lineWidth = this.pitch * GAP_W * 0.8
    ctx.stroke()
    ctx.globalAlpha = GLOSS_LINE
    ctx.lineWidth = Math.max(1.5, 1 / this.scale)
    ctx.stroke()
    ctx.globalAlpha = 1
  }

  /** The lock groove, and the matrix number scratched into the blank runout past it. */
  private drawRunout(ctx: CanvasRenderingContext2D, tint: RGB): void {
    const p = this.platter
    const e = p.runout
    if (e <= 0) return
    const rLock = this.rOut - this.pitch * lockS(e)
    if (rLock <= 0) return
    ctx.strokeStyle = rgba(tint, 1)
    ctx.lineWidth = Math.max(GROOVE_W_CSS * 1.4, 1 / this.scale)
    ctx.globalAlpha = LOCK_ALPHA * e
    ctx.beginPath()
    ctx.arc(this.cx, this.cy, rLock, this.view0, this.view1)
    ctx.stroke()
    // Scratched along the groove, upright at the top of the record.
    const rText = rLock - this.pitch * 2.2
    const a = p.matrixAngle
    ctx.save()
    ctx.translate(this.cx + rText * Math.cos(a), this.cy + rText * Math.sin(a))
    ctx.rotate(a + Math.PI / 2)
    ctx.font = `italic ${Math.max(9, this.pitch * 0.9)}px ui-monospace, Menlo, monospace`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.lineWidth = Math.max(0.6, 1 / this.scale)
    ctx.globalAlpha = MATRIX_ALPHA * e
    ctx.strokeText(this.matrix, 0, 0)
    ctx.restore()
    ctx.globalAlpha = 1
  }

  /** A dust speck riding its groove: a soft halo with a bright core. */
  private drawDust(ctx: CanvasRenderingContext2D): void {
    const p = this.platter
    const d = p.dust
    if (!d) return
    const r = this.radius(d.groove, p.runout)
    const x = this.cx + r * Math.cos(d.angle)
    const y = this.cy + r * Math.sin(d.angle)
    const k = DUST_ALPHA * p.dustAlpha
    ctx.fillStyle = rgba(WHITE, 1)
    ctx.globalAlpha = k * 0.25
    ctx.beginPath()
    ctx.arc(x, y, DUST_CSS * 2.5, 0, 2 * Math.PI)
    ctx.fill()
    ctx.globalAlpha = k
    ctx.beginPath()
    ctx.arc(x, y, Math.max(DUST_CSS, 0.75 / this.scale), 0, 2 * Math.PI)
    ctx.fill()
    ctx.globalAlpha = 1
  }
}
