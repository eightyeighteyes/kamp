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

export interface StampOpts {
  /**
   * Fog left at the centre, 0..255. A run's trail is not wiped dry: some
   * moisture stays, so its edges refog first and it reads as tapering from the
   * start rather than as a clean-cut tube.
   */
  floor?: number
  /** A cone with no flat clear core, for the same reason. */
  soft?: boolean
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
   * so overlapping stamps never add fog back. See StampOpts for trails.
   */
  stamp(cx: number, cy: number, r: number, opts: StampOpts = {}): void {
    const R = Math.max(r, MIN_FALLOFF)
    const inner = opts.soft ? 0 : Math.max(0, R - Math.max(MIN_FALLOFF, R * 0.5))
    const floor = opts.floor ?? 0
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
        const v = Math.round(floor + (FOG_FULL - floor) * smoothstep((dist - inner) / (R - inner)))
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
  stroke(ax: number, ay: number, bx: number, by: number, r: number, opts: StampOpts = {}): number {
    const len = Math.hypot(bx - ax, by - ay)
    const spacing = Math.max(0.5, Math.max(r, MIN_FALLOFF) / 3)
    const n = Math.min(FogMask.MAX_STAMPS, Math.ceil(len / spacing) + 1)
    for (let i = 0; i < n; i++) {
      const t = n === 1 ? 1 : i / (n - 1)
      this.stamp(ax + (bx - ax) * t, ay + (by - ay) * t, r, opts)
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

// --- sliding drops ------------------------------------------------------------------------

/** Side strips beside the art (normalised x). A drop behind the art would be wasted. */
export const SLIDE_STRIPS: [number, number][] = [
  [0.04, 0.2],
  [0.8, 0.96]
]

/** One sliding drop, as the shader sees it. Positions are 0..1 of the pane, DOM y-down. */
export interface Slider {
  id: number
  x: number
  y: number
  /** Where it formed. Its x meanders a little from here as it runs. */
  x0: number
  /** Radius, pane heights. */
  r: number
  alpha: number
  /** Vertical elongation, 1 at rest; a fast drop stretches a little. */
  stretch: number
  heavy: boolean
  /** Being drawn into a larger drop; it shrinks away into that drop's centre. */
  merging: boolean
}

/** A stretch of glass a drop ran over this tick, normalised. `r` is the trail's radius in pane heights. */
export interface Trail extends Segment {
  id: number
  r: number
}

type SliderPhase = 'form' | 'stuck' | 'slip' | 'merge'

interface SliderState extends Slider {
  phase: SliderPhase
  /** Seconds into the current phase, and how long it lasts. */
  age: number
  dur: number
  v: number
  rTarget: number
  travel: number
  wobble: number
  into: SliderState | null
}

/**
 * The trickle: a few drops always on the move (KAMP-772, after a first cut with
 * only a rare single slide read as static). Each forms in place, then runs in
 * stick-slip jerks: stuck for a moment, then slipping down a short way. It
 * gathers the beads in its path (its radius grows with travel) and merges with
 * any drop it meets (the larger keeps their combined area). A running drop never
 * fades out: water doesn't evaporate mid-run, so it leaves only off the bottom
 * of the pane or by being drawn into a larger drop.
 *
 * The caller wipes each returned Trail into the fog mask, so a run leaves clear
 * glass behind it that refogs on its own, and the static beads along it vanish
 * (the shader beads only on fog) as if the drop had swallowed them. A trail
 * starts narrow where the drop broke loose and widens over TAPER_LEN, and it is
 * stamped soft with some fog left in (TRAIL_FLOOR), so it tapers from the first
 * frame instead of reading as a clean-cut tube until the refill rounds it off.
 *
 * Motion is ambient, so it runs whatever the cursor does. The rare moment is the
 * heavy drop: bigger, longer runs, in its own slot, and only once the cursor has
 * been still a while.
 */
export class SliderField {
  /** Ordinary drops at once. Plus one slot for the heavy drop. */
  static readonly MAX = 6
  static readonly SLOTS = SliderField.MAX + 1
  static readonly SPAWN_FLOOR_S = 0.8
  static readonly SPAWN_MEAN_S = 2
  static readonly SPAWN_MAX_S = 8
  static readonly FORM_S = 0.6
  static readonly STUCK_S: [number, number] = [0.4, 2.2]
  static readonly SLIP_S: [number, number] = [0.25, 0.9]
  static readonly R: [number, number] = [0.007, 0.014]
  static readonly R_MAX = 0.03
  /** Radius gained per pane height run: the beads it swallows on the way. */
  static readonly GROW = 0.008
  /** Slip speed is V_BASE + V_PER_R * r pane heights a second: heavier drops run faster. */
  static readonly V_BASE = 0.08
  static readonly V_PER_R = 4
  /** No drop ever moves faster than this, so no step can jump. */
  static readonly V_CAP = 0.3
  static readonly V_TAU = 0.12
  static readonly R_TAU = 0.15
  static readonly MERGE_TAU = 0.08
  /** Drops merge once their centres are this fraction of their summed radii apart. */
  static readonly MERGE_K = 0.8
  /** A trail's full width, as a fraction of its drop's radius. */
  static readonly TRAIL_R = 0.7
  /** Width at the very start of a run, as a fraction of the full trail width. */
  static readonly TRAIL_START = 0.3
  /** Pane heights a drop runs before its trail reaches full width. */
  static readonly TAPER_LEN = 0.1
  /** Fog left in the middle of a trail, 0..255: a run doesn't wipe the glass dry. */
  static readonly TRAIL_FLOOR = 60
  static readonly HEAVY_GAP_FLOOR_S = 60
  static readonly HEAVY_GAP_MEAN_S = 100
  static readonly HEAVY_GAP_MAX_S = 240
  static readonly HEAVY_IDLE_S = 6
  static readonly HEAVY_R: [number, number] = [0.022, 0.028]
  static readonly MAX_DT = 0.1
  /** smoothstep's steepest slope is 1.5 / duration; forming is the only alpha change. */
  static readonly MAX_ALPHA_STEP = (1.5 * SliderField.MAX_DT) / SliderField.FORM_S
  static readonly MAX_R_STEP =
    SliderField.R_MAX * (1 - Math.exp(-SliderField.MAX_DT / SliderField.R_TAU))

  private readonly list: SliderState[] = []
  private nextId = 1
  nextSpawnAt: number
  nextHeavyAt: number
  private readonly rng: () => number

  constructor(rng: () => number) {
    this.rng = rng
    this.nextSpawnAt = this.spawnGap()
    this.nextHeavyAt = this.heavyGap()
  }

  get sliders(): readonly Slider[] {
    return this.list
  }

  /** Stop ordinary spawns (tests, and tuning). */
  pauseSpawning(): void {
    this.nextSpawnAt = Infinity
  }

  /** Make the heavy drop due now (tuning, and tests). Still waits for idle. */
  forceHeavy(): void {
    this.nextHeavyAt = -Infinity
  }

  /** Form an ordinary drop at a given place (tests, and tuning). */
  spawnAt(x: number, y: number, r: number, heavy = false): Slider {
    const s: SliderState = {
      id: this.nextId++,
      x,
      y,
      x0: x,
      r,
      alpha: 0,
      stretch: 1,
      heavy,
      merging: false,
      phase: 'form',
      age: 0,
      dur: heavy ? SliderField.FORM_S * 1.5 : SliderField.FORM_S,
      v: 0,
      rTarget: r,
      travel: 0,
      wobble: this.rng() * Math.PI * 2,
      into: null
    }
    this.list.push(s)
    return s
  }

  /**
   * `elapsed` is the animation clock, `idle` seconds since pointer activity,
   * `artShown` 0..1, `aspect` the pane's width / height. Returns the trails run
   * this tick.
   */
  tick(dt: number, elapsed: number, idle: number, artShown: number, aspect: number): Trail[] {
    const step = clampDt(dt, SliderField.MAX_DT)
    const trails: Trail[] = []
    for (const s of this.list) this.advance(s, step, aspect, trails)
    this.mergeTouching(aspect)
    this.removeFinished()
    this.spawn(elapsed, idle, artShown)
    return trails
  }

  private advance(s: SliderState, step: number, aspect: number, trails: Trail[]): void {
    s.age += step
    if (s.phase === 'merge') {
      // Drawn into the larger drop: slide to its centre while shrinking away.
      const into = s.into
      const k = 1 - Math.exp(-step / SliderField.MERGE_TAU)
      if (into) {
        s.x += (into.x - s.x) * k
        s.y += (into.y - s.y) * k
      }
      s.r *= Math.exp(-step / SliderField.MERGE_TAU)
      return
    }

    s.r += (s.rTarget - s.r) * (1 - Math.exp(-step / SliderField.R_TAU))
    switch (s.phase) {
      case 'form':
        s.alpha = smoothstep(s.age / s.dur)
        if (s.age >= s.dur) {
          // Land on exactly 1: the last form tick can stop just short of it.
          s.alpha = 1
          this.enter(s, 'stuck')
        }
        break
      case 'stuck':
        if (s.age >= s.dur) this.enter(s, 'slip')
        break
      case 'slip':
        if (s.age >= s.dur) this.enter(s, 'stuck')
        break
    }

    const target = s.phase === 'slip' ? this.speed(s) : 0
    s.v += (target - s.v) * (1 - Math.exp(-step / SliderField.V_TAU))
    // A decaying velocity never reaches zero; snap it, so a stuck drop is still.
    if (target === 0 && s.v < 0.002) s.v = 0
    s.v = Math.min(s.v, SliderField.V_CAP)
    if (s.v <= 0) {
      s.stretch = 1
      return
    }
    const ax = s.x
    const ay = s.y
    const dy = s.v * step
    s.y += dy
    s.travel += dy
    // Runs meander: x is a function of distance run, so it never jumps.
    s.x = s.x0 + (0.01 * (Math.sin(s.wobble + s.travel * 12) - Math.sin(s.wobble))) / aspect
    s.rTarget = Math.min(SliderField.R_MAX, s.rTarget + SliderField.GROW * dy)
    s.stretch = 1 + 0.5 * (s.v / SliderField.V_CAP)
    const taper = Math.min(
      1,
      SliderField.TRAIL_START + ((1 - SliderField.TRAIL_START) * s.travel) / SliderField.TAPER_LEN
    )
    trails.push({ id: s.id, ax, ay, bx: s.x, by: s.y, r: s.r * SliderField.TRAIL_R * taper })
  }

  private enter(s: SliderState, phase: SliderPhase): void {
    s.phase = phase
    s.age = 0
    const heavy = s.heavy
    switch (phase) {
      case 'stuck':
        s.dur = this.lerp(heavy ? [0.2, 0.8] : SliderField.STUCK_S)
        break
      case 'slip':
        s.dur = this.lerp(heavy ? [0.6, 1.4] : SliderField.SLIP_S)
        break
      default:
        break
    }
  }

  private speed(s: SliderState): number {
    return Math.min(SliderField.V_CAP, SliderField.V_BASE + SliderField.V_PER_R * s.r)
  }

  private mergeTouching(aspect: number): void {
    const live = this.list.filter((s) => s.phase !== 'merge')
    for (let i = 0; i < live.length; i++) {
      for (let j = i + 1; j < live.length; j++) {
        const a = live[i]
        const b = live[j]
        if (a.phase === 'merge' || b.phase === 'merge') continue
        const dist = Math.hypot((a.x - b.x) * aspect, a.y - b.y)
        if (dist >= (a.r + b.r) * SliderField.MERGE_K) continue
        const [big, small] = a.r >= b.r ? [a, b] : [b, a]
        // The larger keeps their combined area (capped), eased in by R_TAU.
        big.rTarget = Math.min(SliderField.R_MAX, Math.hypot(big.rTarget, small.rTarget))
        small.phase = 'merge'
        small.merging = true
        small.into = big
        small.age = 0
      }
    }
  }

  private removeFinished(): void {
    for (let i = this.list.length - 1; i >= 0; i--) {
      const s = this.list[i]
      const gone = (s.phase === 'merge' && s.r < 0.0004) || s.y - s.r > 1.02
      if (gone) this.list.splice(i, 1)
    }
    // A drop being drawn into one that has gone just finishes shrinking in place.
    for (const s of this.list) if (s.into && !this.list.includes(s.into)) s.into = null
  }

  private spawn(elapsed: number, idle: number, artShown: number): void {
    const ordinary = this.list.filter((s) => !s.heavy).length
    if (elapsed >= this.nextSpawnAt && ordinary < SliderField.MAX) {
      this.spawnAt(this.pickX(artShown), this.lerp([0.02, 0.55]), this.lerp(SliderField.R))
      this.nextSpawnAt = elapsed + this.spawnGap()
    }
    const heavyLive = this.list.some((s) => s.heavy)
    if (elapsed >= this.nextHeavyAt && idle >= SliderField.HEAVY_IDLE_S && !heavyLive) {
      this.spawnAt(
        this.pickX(artShown),
        this.lerp([0.05, 0.3]),
        this.lerp(SliderField.HEAVY_R),
        true
      )
      this.nextHeavyAt = elapsed + this.heavyGap()
    }
  }

  private pickX(artShown: number): number {
    if (artShown < 0.5) return 0.05 + 0.9 * this.rng()
    return this.lerp(SLIDE_STRIPS[this.rng() < 0.5 ? 0 : 1])
  }

  private lerp([lo, hi]: [number, number]): number {
    return lo + (hi - lo) * this.rng()
  }

  private spawnGap(): number {
    return poissonGap(
      this.rng(),
      SliderField.SPAWN_FLOOR_S,
      SliderField.SPAWN_MEAN_S,
      SliderField.SPAWN_MAX_S
    )
  }

  private heavyGap(): number {
    return poissonGap(
      this.rng(),
      SliderField.HEAVY_GAP_FLOOR_S,
      SliderField.HEAVY_GAP_MEAN_S,
      SliderField.HEAVY_GAP_MAX_S
    )
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

// --- lights behind the glass ----------------------------------------------------------

/** How many soft lights sit behind the glass: one per palette colour. */
export const LIGHTS = 5
/** How far each light drifts from its home, pane heights. */
export const LIGHT_DRIFT = 0.07
/** The diffuse (through-fog) falloff's radius, as a multiple of the sharp one. */
export const LIGHT_DIFFUSE = 2.6

const frac = (x: number): number => x - Math.floor(x)

/**
 * Where the lights are this frame, as LIGHTS × (x, y, 1/rs², 1/rd²): centre in
 * pane-height units (y up, as the shader's q), then the inverse squared radii of
 * the sharp and diffuse falloffs, so the shader multiplies instead of dividing.
 *
 * Every pixel used to work this out for itself, with two trig calls per light,
 * though it is the same for the whole frame. `drift` is the pair of drift angles
 * (0..2π), `shown` the eased art visibility: with the art shown, the lights
 * gather in the side strips beside it. Fills and returns `out`.
 */
export function lightField(
  drift: [number, number],
  shown: number,
  aspect: number,
  out: Float32Array
): Float32Array {
  const [left, right] = SLIDE_STRIPS
  for (let i = 0; i < LIGHTS; i++) {
    const bx = frac(0.17 + i * 0.381966) // golden-ratio spread across the width
    const by = frac(0.62 + i * 0.618034)
    const strip =
      bx < 0.5
        ? left[0] + (left[1] - left[0]) * bx * 2
        : right[0] + (right[1] - right[0]) * (bx * 2 - 1)
    const rs = 0.07 + 0.035 * frac(i * 0.7548)
    const rd = rs * LIGHT_DIFFUSE
    out[i * 4] = (bx + (strip - bx) * shown) * aspect + LIGHT_DRIFT * Math.sin(drift[0] + i * 1.9)
    out[i * 4 + 1] = by * 0.8 + 0.1 + LIGHT_DRIFT * Math.cos(drift[1] + i * 2.7)
    out[i * 4 + 2] = 1 / (rs * rs)
    out[i * 4 + 3] = 1 / (rd * rd)
  }
  return out
}
