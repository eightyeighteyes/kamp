// KAMP-782: Bokeh's depth of field. Every so often focus is "racked" to one or two
// of the small accent orbs, which sharpen into defined discs while the rest stay
// soft. A pure model so `node --test` covers it. Framework-free under strip-only
// TypeScript (KAMP-765): no enums, no parameter properties, `.ts` specifiers.
//
// Each orb's static softness (its baked blur) lives in bokehField's TIERS; this
// module only decides how far toward its sharp layer each orb is drawn, and how
// the two layers are weighted.

import type { Rng } from './bokehField.ts'

/** Seconds between racks once running: long enough that focus feels like a drift. */
export const RACK_GAP: [number, number] = [20, 40]
/** Seconds to the first rack, so a fresh field shows its depth soon. */
export const FIRST_RACK: [number, number] = [4, 10]
/** Seconds a focus pull takes (smootherstep). Shorter than any rack gap. */
export const PULL_S = 3
/** A target hidden (behind the art, off the pane) this long loses focus. */
export const HIDDEN_RERACK_S = 5
/** Seconds before trying again when no accent was visible to pick. */
export const RETRY_S = 2
/** At most this many orbs sharp at once: "a visible minority". */
export const MAX_SHARP = 2
/** How far the soft layer dims at full focus, at most (see focusAlphas). */
export const SOFT_DIM = 0.5

// Zero velocity and acceleration at both ends, so a pull never starts or stops
// with a visible snap (the bloom's envelope uses it for the same reason).
const smootherstep = (t: number): number => t * t * t * (t * (t * 6 - 15) + 10)

const rand = (rng: Rng, [min, max]: [number, number]): number => min + rng() * (max - min)

export class RackFocus {
  /** Per orb, 0 = soft only, 1 = fully in focus. Lands exactly on 0 and 1. */
  readonly focus: number[]
  /** The orbs focus is being pulled toward. */
  readonly targets = new Set<number>()

  private from: number[]
  private to: number[]
  private progress: number[] // 0..1 through the current pull; 1 = settled
  private untilRack: number
  private hiddenFor = 0 // seconds the longest-hidden current target has been hidden
  private eligible: readonly number[]
  private rng: Rng

  /**
   * @param count number of orbs in the field
   * @param eligible indices of the orbs that may come into focus (the accents)
   */
  constructor(count: number, eligible: readonly number[], rng: Rng) {
    this.eligible = eligible
    this.rng = rng
    this.focus = new Array(count).fill(0)
    this.from = new Array(count).fill(0)
    this.to = new Array(count).fill(0)
    this.progress = new Array(count).fill(1)
    this.untilRack = rand(rng, FIRST_RACK)
  }

  /**
   * Advance by `dt` seconds. `visible(i)` says whether orb i can be seen now
   * (in the pane, not behind the art); it is asked about eligible orbs only.
   */
  tick(dt: number, visible: (i: number) => boolean): void {
    for (let i = 0; i < this.focus.length; i++) {
      if (this.progress[i] >= 1) continue
      this.progress[i] = Math.min(1, this.progress[i] + dt / PULL_S)
      // Snap on arrival: an ease that only approaches its target leaves an orb
      // a hair off forever (KAMP-772).
      this.focus[i] =
        this.progress[i] >= 1
          ? this.to[i]
          : this.from[i] + (this.to[i] - this.from[i]) * smootherstep(this.progress[i])
    }

    let anyHidden = false
    for (const i of this.targets) if (!visible(i)) anyHidden = true
    this.hiddenFor = anyHidden ? this.hiddenFor + dt : 0

    this.untilRack -= dt
    if (this.untilRack <= 0 || this.hiddenFor >= HIDDEN_RERACK_S) this.rack(visible)
  }

  /**
   * Pick new targets now: 1 or 2 visible eligible orbs, preferring ones not
   * already in focus so focus moves around the field. Every pull starts from
   * the orb's current focus, so a rack mid-pull never jumps (KAMP-774).
   */
  rack(visible: (i: number) => boolean): void {
    this.hiddenFor = 0
    const candidates = this.eligible.filter(visible)
    const next = new Set<number>()
    if (candidates.length === 0) {
      this.untilRack = RETRY_S
    } else {
      const want = Math.min(this.rng() < 0.5 ? 1 : 2, MAX_SHARP, candidates.length)
      const fresh = candidates.filter((i) => !this.targets.has(i))
      const stale = candidates.filter((i) => this.targets.has(i))
      while (next.size < want) {
        const pool = fresh.length > 0 ? fresh : stale
        const [pick] = pool.splice(Math.floor(this.rng() * pool.length), 1)
        next.add(pick)
      }
      this.untilRack = rand(this.rng, RACK_GAP)
    }
    this.targets.clear()
    for (const i of next) this.targets.add(i)
    for (let i = 0; i < this.focus.length; i++) {
      const to = next.has(i) ? 1 : 0
      if (to === this.to[i]) continue
      this.from[i] = this.focus[i]
      this.to[i] = to
      this.progress[i] = 0
    }
  }
}

/**
 * Layer weights for one orb at `focus`, soft under sharp (KAMP-768): the soft
 * sprite is drawn at `soft` × its alpha, then the sharp sprite over it at
 * `sharp` × its alpha. Never a crossfade (m / 1−m), which dips at its midpoint.
 *
 * The soft layer dims as focus rises, or an in-focus orb reads as a brighter
 * blob rather than a disc. Under 'screen' the centre's coverage is
 * 1 − (1 − as·(1 − k·f))·(1 − ak·f), which never falls with f only while
 * k ≤ ak·(1 − as)/as. So k is SOFT_DIM, capped by that bound for the alphas
 * actually drawn: a bright (bloomed) orb dims its halo less rather than letting
 * its centre darken mid-pull.
 *
 * @param focus 0..1
 * @param softCentre the soft layer's centre alpha as drawn (0..1)
 * @param sharpCentre the sharp layer's interior alpha at full focus (0..1)
 */
export function focusAlphas(
  focus: number,
  softCentre: number,
  sharpCentre: number
): { soft: number; sharp: number } {
  const k =
    softCentre <= 0
      ? SOFT_DIM
      : softCentre >= 1
        ? 0
        : Math.min(SOFT_DIM, (sharpCentre * (1 - softCentre)) / softCentre)
  return { soft: 1 - k * focus, sharp: focus }
}
