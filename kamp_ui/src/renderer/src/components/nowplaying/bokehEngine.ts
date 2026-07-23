// KAMP-561: framework-free bokeh renderer for the Now Playing background.
//
// Soft blurred colored orbs (from the album palette) drifting slowly. Deliberately
// calm (not beat-reactive) and never louder than the album art. Owns a single rAF
// handle; start()/stop() are idempotent. The backing store is rendered at reduced
// resolution and CSS-upscaled — the canvas-wide blur hides the upscale and makes
// the per-frame fill + blur ~4-16x cheaper (see plan perf notes).

import type { Palette, RGB } from './palette'

// --- tunables (the "5 knobs") ----------------------------------------------
const RES_SCALE = 0.5 // backing store fraction of (clamped) devicePixelRatio
const MAX_DPR = 2
const FRAME_MS = 33 // ~30fps cap — slow drift needs no more
const CROSSFADE_MS = 1000 // palette recolor duration on track change
const EDGE_MARGIN = 0.45 // wrap distance beyond [0,1] before respawning an orb
const PARALLAX_TAU = 0.35 // seconds — heavy exponential damping of cursor parallax

type Tier = 'hero' | 'mid' | 'accent'

interface Orb {
  bx: number // drifting base position, normalized [0,1] of width/height
  by: number
  vx: number // drift velocity, normalized units / second
  vy: number
  sizePct: number // diameter as a fraction of S = min(cssW, cssH)
  ax: number // wobble amplitude, fraction of S
  ay: number
  px1: number // incommensurate wobble periods (seconds) — never a closed loop
  px2: number
  py1: number
  py2: number
  breathP: number // breathing period (seconds)
  breathPhase: number
  baseAlpha: number
  colorIndex: number // stable palette slot (so recolor never flickers)
  parallax: number // cursor-parallax amplitude, fraction of S
}

const rand = (min: number, max: number): number => min + Math.random() * (max - min)

// Tier -> [count, sizePct range, alpha range, cross-screen seconds range].
// `parallax` is the per-tier cursor-parallax amplitude. Note the mapping is
// intentionally *inverted* from physical depth: hero (big) orbs move least and the
// small accents move most, so the calm backdrop stays still while the sparkle
// dances. It reads as atmosphere, not a literal 3D depth cue.
const TIERS: Record<
  Tier,
  {
    size: [number, number]
    alpha: [number, number]
    cross: [number, number]
    parallax: number
  }
> = {
  hero: { size: [0.55, 0.75], alpha: [0.32, 0.4], cross: [90, 150], parallax: 0.01 },
  mid: { size: [0.3, 0.45], alpha: [0.4, 0.5], cross: [60, 110], parallax: 0.02 },
  accent: { size: [0.12, 0.22], alpha: [0.5, 0.56], cross: [45, 70], parallax: 0.028 }
}

// 7 orbs, most-dominant palette slots to the big hero orbs.
const ORB_PLAN: { tier: Tier; colorIndex: number }[] = [
  { tier: 'hero', colorIndex: 0 },
  { tier: 'hero', colorIndex: 1 },
  { tier: 'mid', colorIndex: 2 },
  { tier: 'mid', colorIndex: 3 },
  { tier: 'mid', colorIndex: 0 },
  { tier: 'accent', colorIndex: 4 },
  { tier: 'accent', colorIndex: 2 }
]

function makeOrb(plan: { tier: Tier; colorIndex: number }): Orb {
  const t = TIERS[plan.tier]
  const cross = rand(t.cross[0], t.cross[1])
  const angle = rand(0, Math.PI * 2)
  const speed = 1 / cross // ~1.0 normalized traversal over `cross` seconds
  return {
    bx: rand(0, 1),
    by: rand(0, 1),
    vx: Math.cos(angle) * speed,
    vy: Math.sin(angle) * speed,
    sizePct: rand(t.size[0], t.size[1]),
    ax: rand(0.05, 0.07),
    ay: rand(0.05, 0.07),
    px1: rand(15, 19),
    px2: rand(27, 31),
    py1: rand(18, 22),
    py2: rand(29, 33),
    breathP: rand(12, 20),
    breathPhase: rand(0, Math.PI * 2),
    baseAlpha: rand(t.alpha[0], t.alpha[1]),
    colorIndex: plan.colorIndex,
    parallax: t.parallax
  }
}

const lerp = (a: number, b: number, t: number): number => a + (b - a) * t
const smoothstep = (t: number): number => t * t * (3 - 2 * t)
const lerpRgb = (a: RGB, b: RGB, t: number): RGB => ({
  r: Math.round(lerp(a.r, b.r, t)),
  g: Math.round(lerp(a.g, b.g, t)),
  b: Math.round(lerp(a.b, b.b, t))
})

export class BokehEngine {
  private ctx: CanvasRenderingContext2D | null
  private orbs: Orb[] = ORB_PLAN.map(makeOrb)
  private cssW = 0
  private cssH = 0
  private scale = 1 // effective backing scale (reduced dpr)

  // Palette crossfade state.
  private from: RGB[]
  private to: RGB[]
  private paletteT = 1 // 0..1

  // Cursor parallax. Target is set by pointer events (normalized [-1,1], 0 =
  // pane center); the rendered value eases toward it in advance(). Both start
  // centered and are reset to center on start() so a resume never inherits a
  // stale off-center offset.
  private pointerTargetX = 0
  private pointerTargetY = 0
  private pointerX = 0
  private pointerY = 0

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
    this.from = initial.colors
    this.to = initial.colors
    this.refreshBg()
  }

  // Theme --bg for the vignette (all themes are dark; near-black).
  private refreshBg(): void {
    const v = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()
    if (v) this.bg = v
  }

  private currentColors(): RGB[] {
    if (this.paletteT >= 1) return this.to
    const t = smoothstep(this.paletteT)
    return this.to.map((c, i) => lerpRgb(this.from[i] ?? c, c, t))
  }

  setPalette(next: Palette): void {
    // Snapshot the currently-interpolated colors so a track change mid-crossfade
    // continues smoothly rather than snapping.
    this.from = this.currentColors()
    this.to = next.colors
    this.paletteT = 0
    if (!this.rafId) this.renderStatic() // reduced-motion path repaints on change
  }

  // Set the parallax target from a normalized cursor position (0 = pane center).
  // Clamped so an out-of-pane coordinate can never push the field past its amplitude.
  setPointer(nx: number, ny: number): void {
    this.pointerTargetX = Math.max(-1, Math.min(1, nx))
    this.pointerTargetY = Math.max(-1, Math.min(1, ny))
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
    this.scale = dpr * RES_SCALE
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
    this.rafId = requestAnimationFrame(this.tick)
  }

  stop(): void {
    if (this.rafId != null) {
      cancelAnimationFrame(this.rafId)
      this.rafId = null
    }
  }

  destroy(): void {
    this.stop()
    this.ctx = null
  }

  private tick = (ts: number): void => {
    this.rafId = requestAnimationFrame(this.tick)
    if (this.lastTs === 0) this.lastTs = ts
    const dt = Math.min(0.1, (ts - this.lastTs) / 1000) // clamp long gaps
    this.lastTs = ts
    // 30fps cap: advance the clock every rAF but only repaint every ~FRAME_MS.
    this.advance(dt)
    if (ts - this.lastDraw < FRAME_MS) return
    this.lastDraw = ts
    this.draw()
  }

  private advance(dt: number): void {
    if (document.hidden) return // belt-and-suspenders; the component also gates
    this.elapsed += dt
    if (this.paletteT < 1) this.paletteT = Math.min(1, this.paletteT + (dt * 1000) / CROSSFADE_MS)
    // Framerate-independent exponential lag toward the cursor target.
    const k = 1 - Math.exp(-dt / PARALLAX_TAU)
    this.pointerX += (this.pointerTargetX - this.pointerX) * k
    this.pointerY += (this.pointerTargetY - this.pointerY) * k
    for (const o of this.orbs) {
      o.bx += o.vx * dt
      o.by += o.vy * dt
      if (o.bx < -EDGE_MARGIN) o.bx += 1 + 2 * EDGE_MARGIN
      else if (o.bx > 1 + EDGE_MARGIN) o.bx -= 1 + 2 * EDGE_MARGIN
      if (o.by < -EDGE_MARGIN) o.by += 1 + 2 * EDGE_MARGIN
      else if (o.by > 1 + EDGE_MARGIN) o.by -= 1 + 2 * EDGE_MARGIN
    }
  }

  private draw(): void {
    const ctx = this.ctx
    if (!ctx || this.cssW === 0) return
    const { cssW: w, cssH: h } = this
    const s = Math.min(w, h)
    const colors = this.currentColors()

    ctx.clearRect(0, 0, w, h)
    ctx.globalCompositeOperation = 'screen'
    for (const o of this.orbs) {
      const ox = o.ax * (Math.sin(this.elapsed / o.px1) + Math.sin(this.elapsed / o.px2))
      const oy = o.ay * (Math.sin(this.elapsed / o.py1) + Math.sin(this.elapsed / o.py2))
      const breath = Math.sin(this.elapsed / o.breathP + o.breathPhase)
      // Subtract the eased pointer offset so orbs drift *opposite* the cursor —
      // the "peering through a deeper field" read, not dragging the field along.
      const x = o.bx * w + ox * s - this.pointerX * o.parallax * s
      const y = o.by * h + oy * s - this.pointerY * o.parallax * s
      const radius = o.sizePct * s * 0.5 * (1 + 0.08 * breath)
      if (radius <= 0) continue
      const alpha = o.baseAlpha * (1 + 0.06 * breath)
      const c = colors[o.colorIndex] ?? colors[0]
      const grad = ctx.createRadialGradient(x, y, 0, x, y, radius)
      grad.addColorStop(0, `rgba(${c.r},${c.g},${c.b},${alpha})`)
      grad.addColorStop(0.4, `rgba(${c.r},${c.g},${c.b},${alpha * 0.55})`)
      grad.addColorStop(1, `rgba(${c.r},${c.g},${c.b},0)`)
      ctx.fillStyle = grad
      ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2)
    }
    ctx.globalCompositeOperation = 'source-over'
    this.drawVignette(ctx, w, h)
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
