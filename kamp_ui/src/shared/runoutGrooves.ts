// KAMP-771: the groove geometry and pattern behind the Runout background.
// Framework-free so `node --test` covers it (KAMP-765 rules: `.ts` specifiers,
// no enums or parameter properties).
//
// The record is one spiral, written in a continuous turn coordinate x: x = k + φ/2π
// for turn k at record angle φ. Integer x are the grooves the model counts
// (GROOVES of them across the view). Because x runs on smoothly from one turn to
// the next, anything that is a function of x alone (spacing, the pattern) has no
// seam where φ wraps. Radius is rOut - pitch · spiralS(x, runout).

import { GROOVES } from './runoutModel.ts'
import { lerp, seededRng } from './particleUtil.ts'
import { smoothstep } from './rareSchedule.ts'

// Where the lead-out starts, as a fraction of GROOVES; how much wider its turns
// get once fully in the runout; and where the lock groove sits (fraction of
// GROOVES, in spacing units) once locked.
export const LEAD_AT = 0.45
export const WIDEN = 4
export const LOCK_AT = 0.8
// How far past the cut a groove takes to fade out, in spacing units.
const CUT_FEATHER = 1.2

/** Spacing coordinate of turn coordinate x: even before LEAD_AT, widened past it by `e`. */
export function spiralS(x: number, e: number): number {
  const lead = LEAD_AT * GROOVES
  if (x <= lead) return x
  return lead + (x - lead) * (1 + e * (WIDEN - 1))
}

/** The lock groove's spacing coordinate. Off the record until the runout begins. */
export function lockS(e: number): number {
  return lerp(GROOVES + 2 * CUT_FEATHER, LOCK_AT * GROOVES, e)
}

/** Alpha for a groove at spacing coordinate s: 1 up to the lock, fading to 0 past it. */
export function cutAlpha(s: number, e: number): number {
  return 1 - smoothstep((s - lockS(e) + CUT_FEATHER / 2) / CUT_FEATHER)
}

// The pattern: a few sines in x with non-integer frequencies, so the same angle on
// adjacent turns differs and the grooves never line up into radial stripes. The
// slow term reads as quiet and loud passages; the faster ones as texture.
const HARMONICS = 3
const FREQ_MIN = [1.3, 5.2, 11.7] // cycles per turn
const FREQ_SPAN = [1.1, 3.3, 7.9]
const AMP = [0.5, 0.3, 0.2]

interface Harmonic {
  f: number
  p: number
  a: number
}

const cache = new Map<number, Harmonic[]>()
const CACHE_MAX = 8

function harmonics(seed: number): Harmonic[] {
  let h = cache.get(seed)
  if (h) return h
  const rng = seededRng(seed)
  h = []
  for (let j = 0; j < HARMONICS; j++) {
    h.push({ f: FREQ_MIN[j] + FREQ_SPAN[j] * rng(), p: rng() * 2 * Math.PI, a: AMP[j] })
  }
  if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as number)
  cache.set(seed, h)
  return h
}

/** The groove's brightness at turn coordinate x, 0..1, for an album's seed. */
export function grooveLevel(seed: number, x: number): number {
  let v = 0
  for (const { f, p, a } of harmonics(seed)) v += a * Math.sin(2 * Math.PI * f * x + p)
  return 0.5 + 0.5 * v // the amplitudes sum to 1
}
