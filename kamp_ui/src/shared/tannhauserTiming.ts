// KAMP-772: the pure model behind the "Tannhäuser" Now Playing background
// (condensation on a rainy-night window). Framework-free so `node --test` covers
// it; components/nowplaying/tannhauser/tannhauserSpec.ts uploads the fog mask
// and reads the other values as uniforms.
//
// The fog mask is a Uint8Array, not an offscreen Canvas 2D (the ticket's first
// design), for three reasons:
//   * a low-alpha source-over fill on 8-bit storage stalls short of full: once
//     (255 - v) * alpha rounds to zero the refill stops, leaving a permanent
//     ghost of every wipe. An integer saturating add lands exactly on 255;
//   * uploading a canvas can cost a GPU-to-CPU readback per frame;
//   * the bytes here are exactly what the GPU sees, so the tests check real
//     pixels rather than a mock of a drawing API.
//
// Same rules as Halation and Borealis otherwise: every envelope clamps dt and
// moves on a bounded slope, and every clock the shader sees is wrapped here in
// float64. Node's strip-only TS mode: no parameter properties or enums.

import { clampDt, poissonGap, smoothstep } from './rareSchedule.ts'

// --- fog mask -------------------------------------------------------------------------

/** 255 = fully fogged, 0 = wiped clear. */
export const FOG_FULL = 255
/**
 * Mask height in texels. Fixed, so a brush radius in pane heights is the same
 * number of texels at any pane size or DPR; the width follows the aspect. The
 * fog is soft, so about 1/8 of the pane survives LINEAR upscaling.
 */
export const MASK_H = 96
/** A stamp's soft edge is never narrower than this, or bilinear filtering shows diamonds. */
export const MIN_FALLOFF = 3

const MIN_W = 16
const MAX_W = 512

/** Mask width for a pane of this aspect (width / height). */
export function maskWidth(aspect: number): number {
  if (!Number.isFinite(aspect) || aspect <= 0) return MIN_W
  return Math.max(MIN_W, Math.min(MAX_W, Math.round(MASK_H * aspect)))
}

export class FogMask {
  /** Seconds for a fully wiped texel to fog over again. Linear, so doodles fade evenly. */
  static readonly REFILL_S = 10
  static readonly MAX_DT = 0.1
  /** Per stroke. A very fast swipe spaces its stamps out rather than costing more. */
  static readonly MAX_STAMPS = 64

  w: number
  h: number
  data: Uint8Array
  /** Changed since the last upload. */
  dirty = true
  // Every texel is FOG_FULL, so refill has nothing to do and nothing to upload.
  private full = true
  // Fractional refill levels carried between frames, so a high frame rate's
  // sub-level steps add up instead of being floored away.
  private acc = 0

  constructor(w: number, h: number) {
    this.w = w
    this.h = h
    this.data = new Uint8Array(w * h).fill(FOG_FULL)
  }

  markUploaded(): void {
    this.dirty = false
  }

  /** Fog the whole glass at once (the track-change breath, once it has covered the pane). */
  fill(): void {
    this.data.fill(FOG_FULL)
    this.full = true
    this.dirty = true
  }

  /**
   * Wipe the whole glass (tuning, and tests). Write through this, never into
   * `data` directly: `full` caches "nothing to refill", which is what keeps an
   * idle mask from being re-uploaded every frame.
   */
  clear(): void {
    this.data.fill(0)
    this.full = false
    this.dirty = true
  }

  /** Advance the refill by dt seconds. */
  refill(dt: number): void {
    this.acc += (clampDt(dt, FogMask.MAX_DT) * FOG_FULL) / FogMask.REFILL_S
    const n = Math.floor(this.acc)
    if (n < 1) return
    this.acc -= n
    if (this.full) return
    const d = this.data
    let full = true
    for (let i = 0; i < d.length; i++) {
      const v = d[i] + n
      if (v >= FOG_FULL) d[i] = FOG_FULL
      else {
        d[i] = v
        full = false
      }
    }
    this.full = full
    this.dirty = true
  }

  /**
   * Wipe a soft disc centred on texel (cx, cy) with radius r texels. Min-blended,
   * so overlapping stamps never add fog back.
   */
  stamp(cx: number, cy: number, r: number): void {
    const R = Math.max(r, MIN_FALLOFF)
    const inner = Math.max(0, R - Math.max(MIN_FALLOFF, R * 0.5))
    const x0 = Math.max(0, Math.floor(cx - R))
    const x1 = Math.min(this.w - 1, Math.ceil(cx + R))
    const y0 = Math.max(0, Math.floor(cy - R))
    const y1 = Math.min(this.h - 1, Math.ceil(cy + R))
    const d = this.data
    let changed = false
    for (let y = y0; y <= y1; y++) {
      const dy = y - cy
      for (let x = x0; x <= x1; x++) {
        const dx = x - cx
        const dist = Math.sqrt(dx * dx + dy * dy)
        if (dist >= R) continue
        const v = Math.round(FOG_FULL * smoothstep((dist - inner) / (R - inner)))
        const i = y * this.w + x
        if (v < d[i]) {
          d[i] = v
          changed = true
        }
      }
    }
    if (changed) {
      this.full = false
      this.dirty = true
    }
  }

  /** Wipe a capsule from (ax, ay) to (bx, by), in texels. Returns the stamp count. */
  stroke(ax: number, ay: number, bx: number, by: number, r: number): number {
    const len = Math.hypot(bx - ax, by - ay)
    const spacing = Math.max(0.5, Math.max(r, MIN_FALLOFF) / 3)
    const n = Math.min(FogMask.MAX_STAMPS, Math.ceil(len / spacing) + 1)
    for (let i = 0; i < n; i++) {
      const t = n === 1 ? 1 : i / (n - 1)
      this.stamp(ax + (bx - ax) * t, ay + (by - ay) * t, r)
    }
    return n
  }

  /**
   * Resample to a new size (bilinear), so a pane resize keeps every wipe where
   * it was instead of resetting the glass.
   */
  resize(w: number, h: number): void {
    if (w === this.w && h === this.h) return
    const src = this.data
    const sw = this.w
    const sh = this.h
    const out = new Uint8Array(w * h)
    for (let y = 0; y < h; y++) {
      const sy = Math.max(0, Math.min(sh - 1, ((y + 0.5) * sh) / h - 0.5))
      const y0 = Math.floor(sy)
      const y1 = Math.min(sh - 1, y0 + 1)
      const fy = sy - y0
      for (let x = 0; x < w; x++) {
        const sx = Math.max(0, Math.min(sw - 1, ((x + 0.5) * sw) / w - 0.5))
        const x0 = Math.floor(sx)
        const x1 = Math.min(sw - 1, x0 + 1)
        const fx = sx - x0
        const top = src[y0 * sw + x0] * (1 - fx) + src[y0 * sw + x1] * fx
        const bot = src[y1 * sw + x0] * (1 - fx) + src[y1 * sw + x1] * fx
        out[y * w + x] = Math.round(top * (1 - fy) + bot * fy)
      }
    }
    this.w = w
    this.h = h
    this.data = out
    this.dirty = true
  }
}

// --- cursor stroke ----------------------------------------------------------------------

export interface PointerPos {
  x: number
  y: number
}

export interface Segment {
  ax: number
  ay: number
  bx: number
  by: number
}

/**
 * Turns per-frame cursor samples into wipe segments. Two samples are joined only
 * within one pointer epoch (the base bumps it on every entry into the pane), so
 * leaving by one edge and re-entering by another never draws a line between them.
 */
export class StrokeTracker {
  private last: (PointerPos & { epoch: number }) | null = null

  next(at: PointerPos, epoch: number): Segment | null {
    const prev = this.last
    this.last = { x: at.x, y: at.y, epoch }
    if (!prev || prev.epoch !== epoch) return null
    if (prev.x === at.x && prev.y === at.y) return null
    return { ax: prev.x, ay: prev.y, bx: at.x, by: at.y }
  }

  reset(): void {
    this.last = null
  }
}

// --- track-change breath ----------------------------------------------------------------

type BreathPhase = 'idle' | 'grow' | 'rise' | 'fade'

/**
 * Fresh fog blooming outward from the centre on a track change.
 *
 * `progress` is the front's radius, 0..1 of the way to the farthest corner, and
 * `amount` its strength. The shader shows max(mask, cover * amount), which only
 * ever adds fog. Once the front has covered the pane, `tick` returns true and the
 * caller fills the mask solid. That is invisible, because cover * amount is
 * already 1 everywhere. Then `amount` fades, so wipes made after the fill emerge
 * gradually instead of popping in.
 *
 * Re-triggers never move the front. A skip while it grows changes nothing (the
 * bloom carries on). A skip while it fades raises `amount` back from where it is,
 * and then commits again.
 */
export class BreathEnvelope {
  static readonly GROW_S = 1.8
  static readonly RISE_S = 0.6
  static readonly FADE_S = 2.5
  static readonly MAX_DT = 0.1
  /** smoothstep's steepest slope is 1.5 / duration; the shortest leg bounds every step. */
  static readonly MAX_STEP =
    (1.5 * BreathEnvelope.MAX_DT) /
    Math.min(BreathEnvelope.GROW_S, BreathEnvelope.RISE_S, BreathEnvelope.FADE_S)

  progress = 0
  amount = 0
  private phase: BreathPhase = 'idle'
  private age = 0
  private from = 0

  get active(): boolean {
    return this.phase !== 'idle'
  }

  trigger(): void {
    if (this.phase === 'idle') {
      this.phase = 'grow'
      this.age = 0
      this.progress = 0
      // The front starts at radius 0, so full strength shows nothing yet.
      this.amount = 1
    } else if (this.phase === 'fade') {
      this.phase = 'rise'
      this.age = 0
      this.from = this.amount
    }
    // grow or rise: already on its way to a commit.
  }

  /** Advance by dt. Returns true on the frame the caller must fill the mask. */
  tick(dt: number): boolean {
    if (this.phase === 'idle') return false
    this.age += clampDt(dt, BreathEnvelope.MAX_DT)
    switch (this.phase) {
      case 'grow':
        if (this.age >= BreathEnvelope.GROW_S) return this.commit()
        this.progress = smoothstep(this.age / BreathEnvelope.GROW_S)
        return false
      case 'rise':
        if (this.age >= BreathEnvelope.RISE_S) return this.commit()
        this.amount = this.from + (1 - this.from) * smoothstep(this.age / BreathEnvelope.RISE_S)
        return false
      case 'fade':
        if (this.age >= BreathEnvelope.FADE_S) {
          this.cancel()
          return false
        }
        this.amount = 1 - smoothstep(this.age / BreathEnvelope.FADE_S)
        return false
    }
  }

  cancel(): void {
    this.phase = 'idle'
    this.age = 0
    this.progress = 0
    this.amount = 0
  }

  private commit(): boolean {
    this.phase = 'fade'
    this.age = 0
    this.progress = 1
    this.amount = 1
    return true
  }
}

// --- rare sliding drop -----------------------------------------------------------------

export interface Slide {
  age: number
  life: number
  /** Normalised pane position (0..1, DOM y-down) where the drop breaks loose. */
  x: number
  y: number
  /** How far it slides, in pane heights. */
  dist: number
  /** Head radius, in pane heights. */
  r: number
}

/** Side strips beside the art (normalised x). A slide behind the art would be wasted. */
export const SLIDE_STRIPS: [number, number][] = [
  [0.05, 0.2],
  [0.8, 0.95]
]

/**
 * A heavy drop that swells in place, breaks loose, slides a short way and
 * fades. The caller strokes each returned segment into the fog mask as a clear
 * trail, which then refogs on its own. Head and trail both come from `pos`, so
 * they can't disagree.
 */
export class SlideEvents {
  static readonly GAP_FLOOR_S = 60
  static readonly GAP_MEAN_S = 100
  static readonly GAP_MAX_S = 240
  /** Seconds the cursor must have been still: rare moments belong to quiet ones. */
  static readonly IDLE_S = 6
  static readonly SWELL_S = 0.8
  static readonly SLIDE_S: [number, number] = [1.6, 2.4]
  static readonly LINGER_S = 1.2
  static readonly DIST: [number, number] = [0.12, 0.22]
  static readonly R: [number, number] = [0.018, 0.026]
  static readonly MAX_DT = 0.1

  active: Slide | null = null
  nextAt: number
  private readonly rng: () => number

  constructor(rng: () => number) {
    this.rng = rng
    this.nextAt = this.gap()
  }

  /** The head's position at `age`: still while it swells, then down, easing in and out. */
  static pos(e: Slide, age: number): PointerPos {
    const slideS = e.life - SlideEvents.SWELL_S - SlideEvents.LINGER_S
    const k = smoothstep((age - SlideEvents.SWELL_S) / slideS)
    return { x: e.x, y: e.y + e.dist * k }
  }

  /** The head's visibility: swells in, holds through the slide, then fades. Zero at both ends. */
  static alpha(e: Slide, age: number): number {
    if (age <= 0) return 0
    if (age < SlideEvents.SWELL_S) return smoothstep(age / SlideEvents.SWELL_S)
    const fadeFrom = e.life - SlideEvents.LINGER_S
    if (age < fadeFrom) return 1
    return 1 - smoothstep((age - fadeFrom) / SlideEvents.LINGER_S)
  }

  private gap(): number {
    return poissonGap(
      this.rng(),
      SlideEvents.GAP_FLOOR_S,
      SlideEvents.GAP_MEAN_S,
      SlideEvents.GAP_MAX_S
    )
  }

  /** Make the next slide due now (tuning, and tests). Still waits for idle. */
  forceNext(): void {
    this.nextAt = -Infinity
  }

  /**
   * `elapsed` is the animation clock, `idle` seconds since pointer activity,
   * `breathing` whether a track-change breath is showing, `artShown` 0..1.
   * Returns the trail segment the head covered this tick (normalised, DOM
   * y-down), or null when it didn't move.
   */
  tick(
    dt: number,
    elapsed: number,
    idle: number,
    breathing: boolean,
    artShown: number
  ): Segment | null {
    const step = clampDt(dt, SlideEvents.MAX_DT)
    const e = this.active
    if (e) {
      const a = SlideEvents.pos(e, e.age)
      e.age = Math.min(e.life, e.age + step)
      const b = SlideEvents.pos(e, e.age)
      if (e.age >= e.life) {
        this.active = null
        this.nextAt = elapsed + this.gap()
      }
      return b.y > a.y ? { ax: a.x, ay: a.y, bx: b.x, by: b.y } : null
    }
    if (elapsed < this.nextAt || idle < SlideEvents.IDLE_S || breathing) return null
    const lerp = ([lo, hi]: [number, number]): number => lo + (hi - lo) * this.rng()
    this.active = {
      age: 0,
      life: SlideEvents.SWELL_S + lerp(SlideEvents.SLIDE_S) + SlideEvents.LINGER_S,
      x: this.pickX(artShown),
      y: lerp([0.12, 0.45]),
      dist: lerp(SlideEvents.DIST),
      r: lerp(SlideEvents.R)
    }
    return null
  }

  /** Drop a slide in flight (resume after a stop) and schedule the next. */
  cancel(elapsed: number): void {
    this.active = null
    this.nextAt = elapsed + this.gap()
  }

  private pickX(artShown: number): number {
    if (artShown < 0.5) return 0.1 + 0.8 * this.rng()
    const [lo, hi] = SLIDE_STRIPS[this.rng() < 0.5 ? 0 : 1]
    return lo + (hi - lo) * this.rng()
  }
}

// --- drop clock -----------------------------------------------------------------------

/**
 * Lifecycle periods of the fine and coarse drop fields. Each drop's life is
 * fract(phase + its own hash), and a drop is invisible at both ends of its life,
 * so the wrap never shows.
 */
export const DROP_PERIODS_S: [number, number] = [29, 47]

/** Both drop phases at time t, each in 0..1, wrapped in float64. */
export function dropPhase(t: number): [number, number] {
  const phase = (p: number): number => (((t % p) + p) % p) / p
  return [phase(DROP_PERIODS_S[0]), phase(DROP_PERIODS_S[1])]
}
