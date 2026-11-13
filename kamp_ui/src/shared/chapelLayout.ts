// KAMP-775: the pure layout and timing logic behind the Chapel Now Playing
// background (stacked, feathered fields of album color that breathe slowly).
//
// Framework-free with type-only imports so `node --test` can exercise it under
// strip-only TypeScript (no parameter properties, no enums). Everything here that
// could go wrong without being visible frame to frame (fields overlapping, a
// still frame on the previous track's layout, a glow that snaps off) is decided
// in this module, so the canvas engine only draws what it is handed.

import type { Rgb } from './paletteUniforms'

export const FIELD_COUNT = 3

// --- tunables ----------------------------------------------------------------------
// Fractions of the pane. Rothko-like insets: the fields float on --bg rather than
// bleeding off the edges, which is what keeps them reading as rectangles.
const MARGIN_X = 0.06 // of width, outside the widest possible stagger
// KAMP-781: the most a field is offset sideways, as a fraction of width. The
// field width is derived from it (w · (1 - 2 · (MARGIN_X + OFFSET_MAX))), so the
// whole range is reachable and an offset field never needs clamping.
export const OFFSET_MAX = 0.08
const MARGIN_TOP = 0.07 // of height
const MARGIN_BOTTOM = 0.07 // of height
// Seam gap, of height. The vertical feather equals the gap: each edge fades over
// one feather centred on the edge, so neighbouring fades just meet in the middle
// of the seam instead of overlapping into a muddy band.
const GAP = 0.045
// Below this the pane is a sliver (collapsing transition, display:none); draw nothing.
const MIN_PANE_PX = 16

// Breathing: each field swells and brightens together on its own slow period.
const BREATH_PERIOD_S: [number, number] = [20, 40]
const HEIGHT_BREATH = 0.08 // ± fraction of the field's weight
export const ALPHA_BREATH = 0.15 // ± fraction of FIELD_ALPHA

// Field opacity over --bg. Palette swatches are clamped to L 0.45-0.68 and S >=
// 0.45 (palette.ts), so at full opacity they would be louder than the art.
export const FIELD_ALPHA = 0.5
// Opacity of the --bg band behind the title while the art (and so the title) shows.
export const TITLE_DIM = 0.6

// Track-change proportions: a field's weight relative to the others.
const PROPORTION_RANGE: [number, number] = [0.75, 1.35]

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface FieldLayout {
  fields: Rect[]
  /** y of each seam's centre, between field i and i + 1. */
  seams: number[]
  /** Each seam's horizontal extent: the union of the two fields it divides. */
  seamX: Array<{ x0: number; x1: number }>
  /** Vertical feather width in px; equals the seam gap. */
  feather: number
  /** Horizontal feather width in px, at the side margins. */
  featherX: number
}

const EMPTY: FieldLayout = { fields: [], seams: [], seamX: [], feather: 0, featherX: 0 }

/**
 * Stack one field per weight, top to bottom. The total field height is fixed by
 * the pane, so weights only redistribute it: breathing and re-proportioning can
 * never push a field into its neighbour or off the pane.
 *
 * `offsets` (KAMP-781) shift each field sideways by a fraction of the width,
 * held to ±OFFSET_MAX. Fields are narrow enough that the extreme offset still
 * leaves MARGIN_X for the side feather.
 */
export function layoutFields(
  w: number,
  h: number,
  weights: number[],
  offsets: readonly number[] = []
): FieldLayout {
  const n = weights.length
  if (n === 0 || w < MIN_PANE_PX || h < MIN_PANE_PX) return EMPTY
  const gap = GAP * h
  const avail = h * (1 - MARGIN_TOP - MARGIN_BOTTOM) - gap * (n - 1)
  const total = weights.reduce((a, b) => a + b, 0)
  if (avail <= 0 || !(total > 0)) return EMPTY
  const marginX = MARGIN_X * w
  const fw = w * (1 - 2 * (MARGIN_X + OFFSET_MAX))
  const centredX = (w - fw) / 2
  const fields: Rect[] = []
  const seams: number[] = []
  let y = MARGIN_TOP * h
  for (let i = 0; i < n; i++) {
    const fh = (avail * weights[i]) / total
    const off = Math.max(-OFFSET_MAX, Math.min(OFFSET_MAX, offsets[i] ?? 0))
    fields.push({ x: centredX + off * w, y, w: fw, h: fh })
    y += fh
    if (i < n - 1) {
      seams.push(y + gap / 2)
      y += gap
    }
  }
  const seamX = seams.map((_, i) => {
    const a = fields[i]
    const b = fields[i + 1]
    return { x0: Math.min(a.x, b.x), x1: Math.max(a.x + a.w, b.x + b.w) }
  })
  // The side feather has to fit inside the margin, or it would eat the canvas edge.
  return { fields, seams, seamX, feather: gap, featherX: Math.min(2 * gap, marginX) }
}

// --- breathing -----------------------------------------------------------------------

export interface Breath {
  period: number // seconds
  phase: number // radians
}

export function makeBreaths(rng: () => number, n = FIELD_COUNT): Breath[] {
  const [lo, hi] = BREATH_PERIOD_S
  return Array.from({ length: n }, () => ({
    period: lo + (hi - lo) * rng(),
    phase: rng() * Math.PI * 2
  }))
}

/** -1..1: where a field is in its breath at time `t`. */
export function breathAt(b: Breath, t: number): number {
  return Math.sin((t / b.period) * Math.PI * 2 + b.phase)
}

/**
 * Weights with each field's breath applied. `breaths` null is the composition at
 * rest: the reduced-motion still frame.
 */
export function breathedWeights(base: number[], breaths: Breath[] | null, t: number): number[] {
  if (!breaths) return base.slice()
  return base.map((w, i) => w * (1 + HEIGHT_BREATH * (breaths[i] ? breathAt(breaths[i], t) : 0)))
}

// --- proportions on track change ---------------------------------------------------------

export function randomProportions(rng: () => number, n = FIELD_COUNT): number[] {
  const [lo, hi] = PROPORTION_RANGE
  return Array.from({ length: n }, () => lo + (hi - lo) * rng())
}

/**
 * KAMP-781: a fresh sideways offset per field, for tension. Each is at least 40%
 * of OFFSET_MAX, and never all to one side, so the stack always visibly staggers
 * instead of landing nearly aligned. Eased in with a ProportionTween.
 */
export function randomOffsets(rng: () => number, n = FIELD_COUNT): number[] {
  const o = Array.from({ length: n }, () => {
    const m = (0.4 + 0.6 * rng()) * OFFSET_MAX
    return rng() < 0.5 ? -m : m
  })
  if (n > 1 && (o.every((x) => x > 0) || o.every((x) => x < 0))) o[1] = -o[1]
  return o
}

const smoothstep = (t: number): number => t * t * (3 - 2 * t)

/**
 * Eases field weights toward a new target, the proportions counterpart of
 * PaletteCrossfade. Carries the same KAMP-779 rule: with the loop stopped,
 * nothing ticks the tween, so a retarget lands immediately.
 */
export class ProportionTween {
  private from: number[]
  private to: number[]
  private t = 1
  private readonly durationMs: number

  constructor(initial: number[], durationMs: number) {
    this.durationMs = durationMs
    this.from = initial
    this.to = initial
  }

  set(next: number[], animate: boolean): void {
    this.from = this.current()
    this.to = next
    this.t = animate && this.durationMs > 0 ? 0 : 1
  }

  tick(dtMs: number): void {
    if (this.t >= 1) return
    this.t = Math.min(1, this.t + dtMs / this.durationMs)
  }

  current(): number[] {
    if (this.t >= 1) return this.to
    const k = smoothstep(this.t)
    return this.to.map((v, i) => {
      const f = this.from[i] ?? v
      return f + (v - f) * k
    })
  }
}

// --- seam warmth (cursor) ----------------------------------------------------------------

/**
 * A set of glows that warm toward per-slot targets and cool when released.
 * Exponential approach, so the curve is the same at any frame rate; cooling is
 * slower than warming so a glow lingers after the cursor moves on. Subclasses
 * only decide the targets (KAMP-781: shared by the seam and field warmth).
 */
export class Warmth {
  static readonly ATTACK_S = 1.5
  static readonly RELEASE_S = 4

  protected readonly v: number[]
  protected readonly target: number[]

  constructor(n: number) {
    this.v = new Array<number>(n).fill(0)
    this.target = new Array<number>(n).fill(0)
  }

  tick(dt: number): void {
    for (let i = 0; i < this.v.length; i++) {
      const tau = this.target[i] > this.v[i] ? Warmth.ATTACK_S : Warmth.RELEASE_S
      this.v[i] += (this.target[i] - this.v[i]) * (1 - Math.exp(-dt / tau))
    }
  }

  values(): number[] {
    return this.v
  }

  /** Drop all warmth and aim, so a resumed loop never replays a stale glow. */
  clear(): void {
    this.v.fill(0)
    this.target.fill(0)
  }
}

/**
 * Per-seam glow that follows the cursor: the nearest seam within reach warms in
 * proportion to closeness, everything else cools.
 */
export class SeamWarmth extends Warmth {
  /** Aim at the cursor's y (same units as `seams`), or null once it has left. */
  aim(y: number | null, seams: number[], reach: number): void {
    this.target.fill(0)
    if (y === null || reach <= 0) return
    let best = -1
    let bestD = Infinity
    for (let i = 0; i < seams.length && i < this.target.length; i++) {
      const d = Math.abs(y - seams[i])
      if (d < bestD) {
        bestD = d
        best = i
      }
    }
    if (best >= 0 && bestD < reach) this.target[best] = 1 - bestD / reach
  }
}

/**
 * KAMP-781: the whole field under the cursor warms (that field only, a design
 * decision), on the same attack and release as the seams. The values are per
 * field, not positions, so a release cools the field the cursor was on rather
 * than drifting with a recentring pointer (KAMP-765). Nothing warms inside the
 * title band, so the brightening can't undo the title's dimming.
 */
export class FieldWarmth extends Warmth {
  /** Aim at the cursor (same units as `fields`), or nulls once it has left. */
  aim(x: number | null, y: number | null, fields: Rect[], titleTop: number | null): void {
    this.target.fill(0)
    if (x === null || y === null) return
    if (titleTop !== null && y >= titleTop) return
    for (let i = 0; i < fields.length && i < this.target.length; i++) {
      const f = fields[i]
      if (x >= f.x && x <= f.x + f.w && y >= f.y && y <= f.y + f.h) {
        this.target[i] = 1
        return
      }
    }
  }
}

// --- rare seam bloom -----------------------------------------------------------------------

export interface ActiveBloom {
  seam: number
  /** Where along the seam the warmth starts, 0..1 of the field width. */
  x: number
  age: number
  life: number
}

/**
 * The rare "luminous warming along one seam". Poisson inter-arrival (a floor plus
 * an exponential draw, soft-capped) so it never feels scheduled, and gated on the
 * pointer being idle so it never competes with a cursor glow.
 */
export class SeamBloom {
  static readonly GAP_FLOOR_S = 90
  static readonly GAP_MEAN_S = 120
  static readonly GAP_MAX_S = 300
  static readonly IDLE_S = 8
  static readonly LIFE_S: [number, number] = [12, 16]

  active: ActiveBloom | null = null
  nextAt: number
  private readonly rng: () => number

  constructor(rng: () => number) {
    this.rng = rng
    this.nextAt = this.gap()
  }

  private gap(): number {
    const u = Math.max(1e-6, this.rng())
    return Math.min(SeamBloom.GAP_MAX_S, SeamBloom.GAP_FLOOR_S - SeamBloom.GAP_MEAN_S * Math.log(u))
  }

  /** `elapsed` is the animation clock; `idle` is seconds since pointer activity. */
  tick(dt: number, elapsed: number, idle: number, seamCount: number): void {
    if (this.active) {
      this.active.age += dt
      if (this.active.age >= this.active.life) {
        this.active = null
        this.nextAt = elapsed + this.gap()
      }
      return
    }
    if (seamCount <= 0 || elapsed < this.nextAt || idle < SeamBloom.IDLE_S) return
    const [lo, hi] = SeamBloom.LIFE_S
    this.active = {
      seam: Math.min(seamCount - 1, Math.floor(this.rng() * seamCount)),
      x: 0.25 + 0.5 * this.rng(),
      age: 0,
      life: lo + (hi - lo) * this.rng()
    }
  }

  /** 0..1 intensity: a slow rise and recession with no onset snap. */
  envelope(): number {
    const a = this.active
    if (!a) return 0
    const s = Math.sin(Math.PI * Math.min(1, a.age / a.life))
    return s * s
  }

  /** 0..1 how far the warmth has spread along the seam. */
  spread(): number {
    const a = this.active
    if (!a) return 0
    const t = Math.min(1, a.age / (a.life * 0.6))
    return smoothstep(t)
  }

  /** Drop an in-flight bloom (resume after a stop) and schedule the next. */
  cancel(elapsed: number): void {
    if (!this.active) return
    this.active = null
    this.nextAt = elapsed + this.gap()
  }
}

// --- colors -----------------------------------------------------------------------------------

const luminance = (c: Rgb): number => 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b

/**
 * One color per field: the dominant swatches, lightest at the top so the darkest
 * field sits under the title. Sorted once per palette (not per frame), so the
 * crossfade runs per field and never sweeps a field through an unrelated hue.
 */
export function fieldColors(colors: Rgb[], n = FIELD_COUNT): Rgb[] {
  if (colors.length === 0) return []
  const picked = Array.from({ length: n }, (_, i) => colors[i % Math.min(n, colors.length)])
  return picked.sort((a, b) => luminance(b) - luminance(a))
}

// --- legibility --------------------------------------------------------------------------------

// KAMP-781: how much a fully warmed field brightens (its alpha × 1 + this), and
// the texture density's peak. The texture only thins the paint (scumble, dry
// brush), never thickens it past FIELD_ALPHA, so it can't break the title bound.
export const FIELD_WARM_GAIN = 0.45
export const TEXTURE_PEAK = 1

/**
 * A field's opacity at one pixel: breath (-1..1), field warmth (0..1), texture
 * density (0..TEXTURE_PEAK), and how far into the title band it is (0..1). The
 * warmth fades out inside the band, so hovering the bottom field can't undo the
 * title's dimming. The shader mirrors this formula.
 */
export function fieldAlpha(breath: number, warmth: number, density: number, band: number): number {
  return (
    FIELD_ALPHA *
    (1 + ALPHA_BREATH * breath) *
    (1 + FIELD_WARM_GAIN * warmth * (1 - band)) *
    density
  )
}

/** Worst-case field opacity left under the title once the dim band is applied. */
export function titleBandAlpha(): number {
  return fieldAlpha(1, 1, TEXTURE_PEAK, 1) * (1 - TITLE_DIM)
}

// --- track change -------------------------------------------------------------------------

/**
 * KAMP-781: what a frame does with the base's real-track-change count. The first
 * frame (mount, a restore) only records it; an unchanged count (a resume, a
 * repaint) does nothing; a change eases in while running and lands on a still
 * frame, so a skip while the pane was hidden is already in place on return.
 */
export function retargetDecision(
  prev: number | null,
  now: number,
  still: boolean
): 'none' | 'animate' | 'land' {
  if (prev === null || prev === now) return 'none'
  return still ? 'land' : 'animate'
}
