// KAMP-781: a bounded, smooth "drunkard's walk" for slow wandering values (Chapel's
// per-field hue). Framework-free so `node --test` covers it (KAMP-765 rules).
//
// A plain random walk drifts away forever, and a first-order one (each step a
// random kick to the value) jitters frame to frame. This one kicks the *velocity*,
// damps it, and pulls the value gently back toward the centre: a damped,
// mean-reverting second-order walk. The path is smooth, never periodic, and
// stays near the centre more than chance would (see its test); a hard reflection at the
// bound catches excursions. PULL_S 20 is a loose pull, tuned by eye: the value
// roams most of its range, at about 0.05 of the bound per second.

import { clampDt } from './rareSchedule.ts'

const VEL_TAU_S = 4 // how long a drift keeps its direction
const PULL_S = 20 // how strongly it is drawn back to the centre
const SIGMA = 0.032 // velocity kick, in bounds per s^1.5
const DT_MAX = 0.1 // a stalled frame is clamped, never one giant step

/** A standard normal sample from two uniform ones (Box-Muller). */
function gauss(rng: () => number): number {
  const u = Math.max(1e-9, rng())
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rng())
}

export class DrunkWalk {
  private u = 0 // value / max
  private v = 0 // velocity, in bounds per second
  private readonly rng: () => number
  private readonly max: number

  constructor(rng: () => number, max: number) {
    this.rng = rng
    this.max = max
  }

  /** The current value, within ±max. */
  get value(): number {
    return this.u * this.max
  }

  tick(rawDt: number): void {
    const dt = clampDt(rawDt, DT_MAX)
    if (dt <= 0) return
    const pull = this.u / (PULL_S * PULL_S)
    this.v += (-this.v / VEL_TAU_S - pull) * dt + SIGMA * Math.sqrt(dt) * gauss(this.rng)
    this.u += this.v * dt
    if (Math.abs(this.u) > 1) {
      this.u = Math.sign(this.u)
      this.v = -0.5 * this.v
    }
  }
}
