// KAMP-765: the pure timing behind the "Borealis" Now Playing background (slow
// aurora curtains in album colours). Framework-free so `node --test` covers it;
// the shader in components/nowplaying/borealis/borealisSpec.ts only reads values.
//
// Same rules as Halation (KAMP-774): nothing may jump or strobe, so every value
// moves on a slope with a known bound, every tick clamps dt, and every clock the
// shader sees is computed here in float64 and wrapped seamlessly.
// Node's strip-only TS mode: no parameter properties or enums in this file.

import { clampDt, poissonGap, smoothstep } from './rareSchedule.ts'

const TAU = Math.PI * 2

// --- flow -------------------------------------------------------------------------

/**
 * The curtains' noise is sampled along two circular paths in noise space, one per
 * period. A circle is bounded (float32 never sees a large coordinate) and closes
 * on itself (the wrap is seamless). Two coprime periods put the combined repeat at
 * about 3.8 h, so no loop is ever visible.
 */
export const FLOW_PERIODS_S: [number, number] = [97, 143]

/** Both flow angles at time t, each in 0..2π. */
export function flowPhase(t: number): [number, number] {
  const angle = (p: number): number => {
    const r = ((t % p) + p) % p // float64 modulo first: exact even at t ≈ 10 h
    return (TAU * r) / p
  }
  return [angle(FLOW_PERIODS_S[0]), angle(FLOW_PERIODS_S[1])]
}

// --- track-change surge -------------------------------------------------------------

export interface SurgeBand {
  /** 0..1 along the curtains, in the band's direction of travel. */
  position: number
  /** 0..1 brightness. */
  value: number
  /** 0 travels left to right, 1 right to left. */
  dir: number
}

interface BandState extends SurgeBand {
  /** Envelope age; Infinity when the band is idle. */
  envAge: number
  /** Value at the last (re)trigger, so a boost rises from where it is. */
  from: number
}

/**
 * A brighter band rolling along the curtains while the new palette crossfades in
 * (the base crossfade is 1 s, which is also RISE_S, so the band peaks as the new
 * colours land).
 *
 * Two slots, so a skip never moves a visible band: a skip while the newest band is
 * early on brightens it in place; a skip once it is past halfway rolls a second
 * band in from zero brightness while the first finishes. Position only ever
 * advances at 1/SWEEP_S per second.
 */
export class SurgeEnvelope {
  static readonly RISE_S = 1
  static readonly HOLD_S = 0.4
  static readonly DECAY_S = 3
  /** The band crosses exactly once over a full envelope. */
  static readonly SWEEP_S = SurgeEnvelope.RISE_S + SurgeEnvelope.HOLD_S + SurgeEnvelope.DECAY_S
  /** smoothstep's steepest slope is 1.5 / duration; the rise is the steepest leg. */
  static readonly MAX_RATE = 1.5 / SurgeEnvelope.RISE_S
  static readonly MAX_DT = 0.1
  /** Past this, a skip launches a second band instead of boosting this one. */
  static readonly BOOST_UNTIL = 0.5

  readonly bands: [BandState, BandState] = [SurgeEnvelope.idle(), SurgeEnvelope.idle()]
  private newest = 0

  private static idle(): BandState {
    return { position: 0, value: 0, dir: 0, envAge: Infinity, from: 0 }
  }

  get active(): boolean {
    return this.bands.some((b) => b.envAge !== Infinity)
  }

  trigger(dir: number): void {
    const n = this.bands[this.newest]
    if (n.envAge !== Infinity && n.position < SurgeEnvelope.BOOST_UNTIL) {
      this.boost(n)
      return
    }
    const free = this.bands.findIndex((b) => b.envAge === Infinity)
    if (free < 0) {
      // Both bands busy: brighten the newest where it is rather than move anything.
      this.boost(n)
      return
    }
    const b = this.bands[free]
    b.position = 0
    b.value = 0
    b.dir = dir
    b.from = 0
    b.envAge = 0
    this.newest = free
  }

  tick(dt: number): void {
    const step = clampDt(dt, SurgeEnvelope.MAX_DT)
    for (const b of this.bands) {
      if (b.envAge === Infinity) continue
      b.envAge += step
      b.position = Math.min(1, b.position + step / SurgeEnvelope.SWEEP_S)
      b.value = SurgeEnvelope.at(b.envAge, b.from)
      if (b.value === 0 && b.envAge > SurgeEnvelope.RISE_S) b.envAge = Infinity
    }
  }

  /** Drop every band in flight (a resume after the loop was stopped). */
  cancel(): void {
    for (const b of this.bands) Object.assign(b, SurgeEnvelope.idle())
  }

  private boost(b: BandState): void {
    b.from = b.value
    b.envAge = 0
  }

  private static at(age: number, from: number): number {
    const { RISE_S, HOLD_S, DECAY_S } = SurgeEnvelope
    if (age < RISE_S) return from + (1 - from) * smoothstep(age / RISE_S)
    if (age < RISE_S + HOLD_S) return 1
    if (age < RISE_S + HOLD_S + DECAY_S) return 1 - smoothstep((age - RISE_S - HOLD_S) / DECAY_S)
    return 0
  }
}

// --- cursor lean --------------------------------------------------------------------

export interface PointerPos {
  x: number
  y: number
}

/**
 * How far the curtains lean away from the cursor, and where from.
 *
 * The amount follows pointerActive, gated off once the cursor has rested REST_S,
 * through an attack/release follower, so it never steps. The anchor follows the
 * base's pointerAt, which is the last real cursor position: unlike uPointer it is
 * not recentred on leave, so a fading lean stays where it was instead of sliding
 * toward the middle of the pane.
 */
export class LeanFollower {
  static readonly ATTACK_S = 0.35
  static readonly RELEASE_S = 0.8
  /** Seconds of stillness before the lean eases back. */
  static readonly REST_S = 1.5
  static readonly ANCHOR_S = 0.15
  static readonly MAX_DT = 0.1

  amount = 0
  x = 0
  y = 0
  private anchored = false

  /** `active` is the eased pointer presence; `idle` is seconds since pointer activity. */
  tick(dt: number, active: number, idle: number, at: PointerPos): void {
    const step = clampDt(dt, LeanFollower.MAX_DT)
    if (!this.anchored) {
      // Nothing to ease from yet: the amount is 0, so the first anchor can't show.
      this.x = at.x
      this.y = at.y
      this.anchored = true
    } else {
      const k = 1 - Math.exp(-step / LeanFollower.ANCHOR_S)
      this.x = LeanFollower.approach(this.x, at.x, k)
      this.y = LeanFollower.approach(this.y, at.y, k)
    }
    const target = idle < LeanFollower.REST_S ? Math.max(0, Math.min(1, active)) : 0
    const tau = target > this.amount ? LeanFollower.ATTACK_S : LeanFollower.RELEASE_S
    this.amount += (target - this.amount) * (1 - Math.exp(-step / tau))
  }

  cancel(): void {
    this.amount = 0
  }

  private static approach(v: number, target: number, k: number): number {
    const next = v + (target - v) * k
    return Math.abs(target - next) < 1e-6 ? target : next
  }
}

// --- rare edge flicker ----------------------------------------------------------------

export interface Flicker {
  age: number
  life: number
  /** Which curtain's lower edge, 0..CURTAINS-1. */
  curtain: number
  /** 0..1 across the pane. */
  x: number
}

export const CURTAINS = 3

/** Side strips beside the art (normalised x). A flicker behind the art is wasted. */
export const FLICKER_STRIPS: [number, number][] = [
  [0.04, 0.22],
  [0.78, 0.96]
]

export class FlickerEvents {
  static readonly GAP_FLOOR_S = 75
  static readonly GAP_MEAN_S = 120
  static readonly GAP_MAX_S = 300
  /** Seconds the cursor must have been still: rare moments belong to quiet ones. */
  static readonly IDLE_S = 6
  static readonly LIFE_S: [number, number] = [2, 3]
  /**
   * Soft pulses per second inside the flicker. Well under the WCAG limit of
   * three flashes a second, and the swing is small: it modulates, never blinks.
   */
  static readonly FLUTTER_HZ = 1
  static readonly SWING = 0.15
  static readonly MAX_DT = 0.1

  active: Flicker | null = null
  nextAt: number
  private readonly rng: () => number

  constructor(rng: () => number) {
    this.rng = rng
    this.nextAt = this.gap()
  }

  /** 0..1 brightness at `age` of a flicker lasting `life`: zero value and slope at both ends. */
  static envelope(age: number, life: number): number {
    const a = Math.max(0, Math.min(life, age))
    const s = Math.sin((Math.PI * a) / life)
    const flutter = 0.5 - 0.5 * Math.cos(TAU * FlickerEvents.FLUTTER_HZ * a)
    return s * s * (1 - FlickerEvents.SWING * flutter)
  }

  private gap(): number {
    return poissonGap(
      this.rng(),
      FlickerEvents.GAP_FLOOR_S,
      FlickerEvents.GAP_MEAN_S,
      FlickerEvents.GAP_MAX_S
    )
  }

  /** Make the next flicker due now (tuning, and tests). Still waits for idle. */
  forceNext(): void {
    this.nextAt = -Infinity
  }

  /**
   * `elapsed` is the animation clock, `idle` seconds since pointer activity,
   * `surging` whether a track-change surge is showing, `artShown` 0..1.
   */
  tick(dt: number, elapsed: number, idle: number, surging: boolean, artShown: number): void {
    const step = clampDt(dt, FlickerEvents.MAX_DT)
    if (this.active) {
      this.active.age += step
      if (this.active.age >= this.active.life) {
        this.active = null
        this.nextAt = elapsed + this.gap()
      }
      return
    }
    if (elapsed < this.nextAt || idle < FlickerEvents.IDLE_S || surging) return
    const [lo, hi] = FlickerEvents.LIFE_S
    this.active = {
      age: 0,
      life: lo + (hi - lo) * this.rng(),
      curtain: Math.min(CURTAINS - 1, Math.floor(this.rng() * CURTAINS)),
      x: this.pickX(artShown)
    }
  }

  value(): number {
    const a = this.active
    return a ? FlickerEvents.envelope(a.age, a.life) : 0
  }

  /** Drop a flicker in flight (resume after a stop) and schedule the next. */
  cancel(elapsed: number): void {
    this.active = null
    this.nextAt = elapsed + this.gap()
  }

  private pickX(artShown: number): number {
    if (artShown < 0.5) return 0.1 + 0.8 * this.rng()
    const [lo, hi] = FLICKER_STRIPS[this.rng() < 0.5 ? 0 : 1]
    return lo + (hi - lo) * this.rng()
  }
}
