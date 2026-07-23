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
const PARALLAX_TAU = 0.25 // seconds — heavy exponential damping of cursor parallax
// Firefly motes (KAMP-627): a rare, single transient spark. Poisson inter-arrival
// (floor + exponential(mean)) so the cadence never feels scheduled; idle-gated so it
// only appears in still moments; warm-dim so it reads as atmosphere, not a UI dot.
const FIREFLY_GAP_FLOOR = 45 // seconds — minimum gap between motes
const FIREFLY_GAP_MEAN = 110 // seconds — exponential mean added to the floor (avg ~2.5min)
const FIREFLY_GAP_MAX = 300 // seconds — soft cap on the heavy tail
const FIREFLY_IDLE = 8 // seconds of no pane pointer activity before a due mote appears
const FIREFLY_LIFE: [number, number] = [6, 10] // seconds, fade-in -> drift -> fade-out
const FIREFLY_SIZE: [number, number] = [0.016, 0.024] // core radius, fraction of S
const FIREFLY_TRAVEL = 0.07 // eased drift distance over life, fraction of S
const FIREFLY_BOW = 0.012 // perpendicular path bow, fraction of S
const FIREFLY_ALPHA = 0.6 // peak core alpha (kept modest; the canvas blur softens it)

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
  hero: { size: [0.55, 0.75], alpha: [0.32, 0.4], cross: [90, 150], parallax: 0.02 },
  mid: { size: [0.3, 0.45], alpha: [0.4, 0.5], cross: [60, 110], parallax: 0.04 },
  accent: { size: [0.12, 0.22], alpha: [0.5, 0.56], cross: [45, 70], parallax: 0.056 }
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
}

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

  // Firefly state. `nextFireflyAt` and `lastPointerAt` are in `elapsed` seconds, so
  // the cadence and idle gate freeze cleanly while the loop is stopped.
  private firefly: Firefly | null = null
  private nextFireflyAt = 0 // set in the constructor (needs fireflyGap())
  private lastPointerAt = 0 // elapsed at the last pane pointer activity
  private lastFireflyCorner = -1 // avoid spawning in the same corner twice running

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
    this.nextFireflyAt = this.fireflyGap()
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
    this.drawFirefly(ctx, w, h, s) // after the vignette so a corner mote still glows
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
    const u = Math.max(1e-6, Math.random())
    return Math.min(FIREFLY_GAP_MAX, FIREFLY_GAP_FLOOR - FIREFLY_GAP_MEAN * Math.log(u))
  }

  private spawnFirefly(): void {
    const colors = this.currentColors()
    if (!colors.length) return
    const src = colors[Math.floor(Math.random() * colors.length)]
    const color = { ...src } // snapshot so a mid-flight track change never recolors it
    // Pick a corner, not the same as last time.
    let corner = Math.floor(Math.random() * 4)
    if (corner === this.lastFireflyCorner) corner = (corner + 1) % 4
    this.lastFireflyCorner = corner
    const left = corner === 0 || corner === 3
    const top = corner < 2
    const x0 = left ? rand(0.08, 0.24) : rand(0.76, 0.92)
    const y0 = top ? rand(0.08, 0.24) : rand(0.76, 0.92)
    // Drift outward (away from center) so the mote never walks the eye to the art.
    const outAngle = Math.atan2(y0 < 0.5 ? -1 : 1, x0 < 0.5 ? -1 : 1)
    const angle = outAngle + rand(-0.6, 0.6)
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
      life: rand(FIREFLY_LIFE[0], FIREFLY_LIFE[1]),
      size: rand(FIREFLY_SIZE[0], FIREFLY_SIZE[1]),
      color,
      breathPhase: rand(0, Math.PI * 2)
    }
  }

  // A single warm mote drawn over the vignette with additive blend. The alpha
  // envelope (slow emerge, long fade-out, gentle breath) is baked into the gradient
  // stops — never globalAlpha, which the orb/vignette passes assume stays 1.
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
    const c = f.color
    const grad = ctx.createRadialGradient(x, y, 0, x, y, radius)
    grad.addColorStop(0, `rgba(255,246,230,${alpha})`) // warm off-white core
    grad.addColorStop(0.35, `rgba(${c.r},${c.g},${c.b},${alpha * 0.7})`)
    grad.addColorStop(1, `rgba(${c.r},${c.g},${c.b},0)`)
    ctx.globalCompositeOperation = 'lighter'
    ctx.fillStyle = grad
    ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2)
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
