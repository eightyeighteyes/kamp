// KAMP-774: the pure timing behind the "Halation" Now Playing background (film
// grain, projector dust, light leaks). Framework-free so `node --test` covers it;
// the shader in components/nowplaying/halation/halationSpec.ts only reads values.
//
// The ticket's main risk is strobing, so every value here moves on a slope with a
// known bound:
//   * the grain re-seeds every 3-4 s (never per frame) with a 0.5 s crossfade,
//     and the crossfade is variance-normalised so the grain never softens mid-fade;
//   * the track-change burn rises over ~2 s from zero slope and never steps faster
//     than MAX_RATE per second, however fast tracks are skipped;
//   * every tick clamps dt, because a frame cap of 1 fps (__kampGL.tune) or a
//     stalled renderer would otherwise hand an envelope a whole second at once.
// Node's strip-only TS mode: no parameter properties or enums in this file.

const smoothstep = (t: number): number => {
  const x = Math.max(0, Math.min(1, t))
  return x * x * (3 - 2 * x)
}

/** Deterministic 0..1 from an integer (the epoch index), stable across sessions. */
function hashUnit(i: number): number {
  let h = Math.imul(i | 0, 0x9e3779b1) ^ 0x85ebca6b
  h = Math.imul(h ^ (h >>> 16), 0x7feb352d)
  h = Math.imul(h ^ (h >>> 15), 0x846ca68b)
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296
}

// --- grain seed schedule ------------------------------------------------------------

export const SEED_PERIOD_S: [number, number] = [3, 4]
export const SEED_FADE_S = 0.5
// Seeds are offsets fed to a sin-based hash in GLSL. Large arguments lose float32
// precision and stripe on Intel/ANGLE, so they stay small.
export const SEED_WRAP = 97

const SEED_MID_S = (SEED_PERIOD_S[0] + SEED_PERIOD_S[1]) / 2
const SEED_JITTER_S = (SEED_PERIOD_S[1] - SEED_PERIOD_S[0]) / 4 // ±, so a period spans lo..hi

/**
 * Start time of grain epoch k. Boundaries sit on a fixed grid with a per-epoch
 * jitter, so the period varies within 3-4 s and the epoch of any time is found in
 * O(1): the schedule is a pure function of the clock, which survives a context
 * restore and needs no state.
 */
export function seedBoundary(k: number): number {
  return k * SEED_MID_S + (hashUnit(k) * 2 - 1) * SEED_JITTER_S
}

function seedOf(k: number): number {
  return hashUnit(k + 0x5bd1e995) * SEED_WRAP
}

export interface GrainSeeds {
  seedA: number
  seedB: number
  /** 0 shows seedA, 1 shows seedB. Outside a crossfade both seeds are equal. */
  mix: number
}

export function seedAt(elapsed: number): GrainSeeds {
  let k = Math.floor(elapsed / SEED_MID_S)
  // The jitter can put `elapsed` just either side of the grid line.
  if (elapsed < seedBoundary(k)) k -= 1
  else if (elapsed >= seedBoundary(k + 1)) k += 1
  const into = elapsed - seedBoundary(k)
  const current = seedOf(k)
  if (into >= SEED_FADE_S) return { seedA: current, seedB: current, mix: 0 }
  // Fading from the previous epoch's grain into this one's. When the fade ends,
  // (prev, current, 1) and (current, current, 0) render identically.
  return { seedA: seedOf(k - 1), seedB: current, mix: smoothstep(into / SEED_FADE_S) }
}

/**
 * Crossfade two independent 0..1 noise samples without losing contrast. A plain
 * lerp of independent fields drops the variance to half at the midpoint, so the
 * grain would visibly soften every few seconds; dividing the deviation by
 * sqrt((1-m)^2 + m^2) holds it constant. Mirrored exactly in the GLSL.
 */
export function grainMix(a: number, b: number, m: number): number {
  const wa = 1 - m
  const dev = a * wa + b * m - 0.5
  return dev / Math.sqrt(wa * wa + m * m) + 0.5
}

// --- track-change burn ------------------------------------------------------------------

/** Edges a burn can come from. Bottom is excluded: the title sits there. */
export const BURN_EDGES = 3 // 0 left, 1 right, 2 top

export class BurnEnvelope {
  static readonly RISE_S = 2
  static readonly HOLD_S = 0.3
  static readonly DECAY_S = 3.5
  static readonly PEAK = 1
  /** smoothstep's steepest slope is 1.5 / duration; the rise is the steepest leg. */
  static readonly MAX_RATE = (1.5 * BurnEnvelope.PEAK) / BurnEnvelope.RISE_S
  static readonly MAX_DT = 0.1

  value = 0
  edge = 0
  private from = 0
  private age = Infinity

  get active(): boolean {
    return this.value > 0 || this.age < BurnEnvelope.RISE_S + BurnEnvelope.HOLD_S
  }

  /**
   * A track changed. The rise starts from wherever the burn is now, so a rapid
   * skip never resets it to zero or past the peak. A burn already showing keeps
   * its edge: moving the gradient across the frame would read as a flash.
   */
  trigger(edge: number): void {
    if (this.value <= 0.02) this.edge = edge
    this.from = this.value
    this.age = 0
  }

  tick(dt: number): void {
    if (this.age === Infinity) return
    this.age += Math.min(Math.max(0, dt), BurnEnvelope.MAX_DT)
    this.value = this.at(this.age)
    if (this.value === 0 && this.age > BurnEnvelope.RISE_S) this.age = Infinity
  }

  /** Drop a burn in flight (a resume after the loop was stopped). */
  cancel(): void {
    this.value = 0
    this.from = 0
    this.age = Infinity
  }

  private at(age: number): number {
    const { RISE_S, HOLD_S, DECAY_S, PEAK } = BurnEnvelope
    if (age < RISE_S) return this.from + (PEAK - this.from) * smoothstep(age / RISE_S)
    if (age < RISE_S + HOLD_S) return PEAK
    if (age < RISE_S + HOLD_S + DECAY_S) {
      return PEAK * (1 - smoothstep((age - RISE_S - HOLD_S) / DECAY_S))
    }
    return 0
  }
}

// --- rare hair / gate jolt ----------------------------------------------------------------

export type RareKind = 'hair' | 'jolt'

export interface RareEvent {
  kind: RareKind
  age: number
  life: number
  /** 0..SEED_WRAP: the hair's shape and path. */
  seed: number
}

export class RareEvents {
  static readonly GAP_FLOOR_S = 60
  static readonly GAP_MEAN_S = 90
  static readonly GAP_MAX_S = 240
  /** Seconds the cursor must have been still: rare moments belong to quiet ones. */
  static readonly IDLE_S = 6
  static readonly HAIR_CHANCE = 0.6
  static readonly HAIR_LIFE_S: [number, number] = [7, 10]
  static readonly JOLT_LIFE_S = 0.6
  static readonly JOLT_HZ = 6
  static readonly MAX_DT = 0.1

  active: RareEvent | null = null
  nextAt: number
  private readonly rng: () => number

  constructor(rng: () => number) {
    this.rng = rng
    this.nextAt = this.gap()
  }

  private gap(): number {
    const u = Math.max(1e-6, this.rng())
    return Math.min(
      RareEvents.GAP_MAX_S,
      RareEvents.GAP_FLOOR_S - RareEvents.GAP_MEAN_S * Math.log(u)
    )
  }

  /** Make the next event due now (tuning, and tests). Still waits for idle. */
  forceNext(): void {
    this.nextAt = -Infinity
  }

  /** `elapsed` is the animation clock; `idle` is seconds since pointer activity. */
  tick(dt: number, elapsed: number, idle: number, burning: boolean): void {
    const step = Math.min(Math.max(0, dt), RareEvents.MAX_DT)
    if (this.active) {
      this.active.age += step
      if (this.active.age >= this.active.life) {
        this.active = null
        this.nextAt = elapsed + this.gap()
      }
      return
    }
    if (elapsed < this.nextAt || idle < RareEvents.IDLE_S || burning) return
    const hair = this.rng() < RareEvents.HAIR_CHANCE
    const [lo, hi] = RareEvents.HAIR_LIFE_S
    this.active = {
      kind: hair ? 'hair' : 'jolt',
      age: 0,
      life: hair ? lo + (hi - lo) * this.rng() : RareEvents.JOLT_LIFE_S,
      seed: this.rng() * SEED_WRAP
    }
  }

  /** Drop an in-flight event (resume after a stop) and schedule the next. */
  cancel(elapsed: number): void {
    this.active = null
    this.nextAt = elapsed + this.gap()
  }

  /** 0..1 how far the hair has drifted across the frame, or -1 with no hair. */
  hairProgress(): number {
    const a = this.active
    return a && a.kind === 'hair' ? a.age / a.life : -1
  }

  /** 0..1 hair opacity: zero slope at both ends, so it never pops in or out. */
  hairAlpha(): number {
    const a = this.active
    if (!a || a.kind !== 'hair') return 0
    const s = Math.sin(Math.PI * Math.min(1, a.age / a.life))
    return s * s
  }

  /** -1..1 vertical gate offset: a damped shake that is zero at both ends. */
  joltOffset(): number {
    const a = this.active
    if (!a || a.kind !== 'jolt') return 0
    const t = a.age
    const decay = Math.exp(-t / (a.life / 4)) * (1 - smoothstep(t / a.life))
    return Math.sin(2 * Math.PI * RareEvents.JOLT_HZ * t) * decay
  }
}
