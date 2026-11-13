// KAMP-779: Bokeh's orb field as a pure model, so `node --test` can measure what
// the eye sees: how many orbs are actually on screen, and for how long.
// Framework-free under strip-only TypeScript (KAMP-765): no enums, no parameter
// properties, `.ts` sibling specifiers.
//
// The full drawn position (drift + wobble + parallax) lives here, not in the
// engine, so the density test and the renderer can never disagree about where an
// orb is.

// Motion (KAMP-779). The orbs used to drift in straight lines and wrap 0.45 of a
// pane beyond each edge, so each spent much of its life off screen: the field was
// blank about 10% of the time (19% once the art and the vignette band are taken
// into account) and showed at most one orb for long stretches. Now each orb's base
// position wanders on a bounded, mean-reverting walk around the pane centre, one
// per axis (shared DrunkWalk, as Chapel's hues). No orb ever wraps, so nothing can
// pop in or out at an edge either.
//
// `reach` is how far an orb's base may roam from the centre, as a fraction of half
// the pane: heroes stay inside it and keep a wash behind the art, while mids and
// accents (reach > 1) can leave and come back, so the count on screen still varies.

import { DrunkWalk } from './drunkWalk.ts'
import type { PaneRect } from './particleUtil.ts'

export type Tier = 'hero' | 'mid' | 'accent'

export interface TierSpec {
  size: [number, number] // diameter, fraction of S = min(w, h)
  alpha: [number, number]
  reach: number // base roam from centre, fraction of half the pane, per axis
  rate: number // walk clock multiplier: speed scales linearly with it
  parallax: number // cursor-parallax amplitude, fraction of S
}

// `parallax` is intentionally *inverted* from physical depth: hero (big) orbs move
// least and the small accents move most, so the calm backdrop stays still while
// the sparkle dances. It reads as atmosphere, not a literal 3D depth cue.
export const TIERS: Record<Tier, TierSpec> = {
  hero: { size: [0.55, 0.75], alpha: [0.32, 0.4], reach: 0.8, rate: 0.35, parallax: 0.02 },
  mid: { size: [0.3, 0.45], alpha: [0.4, 0.5], reach: 1.1, rate: 0.35, parallax: 0.04 },
  accent: { size: [0.12, 0.22], alpha: [0.5, 0.56], reach: 1.5, rate: 0.35, parallax: 0.056 }
}

// DrunkWalk reflects hard at its bound (v -> -0.5v), which would read as an orb
// turning abruptly mid-pane. Mapping the walk through tanh puts that bound where
// the curve is nearly flat (slope ~0.07 of the centre's), so a reflection is a
// gentle slowdown. tanh(SOFT·u)/tanh(SOFT) still lands exactly on ±1 at u = ±1.
// Measured over 6 h: the worst frame-to-frame velocity change is ~5x smaller than
// with a linear map (bokehField.test.ts pins it).
const SOFT = 2
const softBound = (u: number): number => Math.tanh(SOFT * u) / Math.tanh(SOFT)

// Seconds of walk time simulated at creation, so orbs start spread out the way
// they will be later rather than all at the centre (DrunkWalk starts at 0).
export const PREWARM_S = 600
const STEP_S = 0.1 // DrunkWalk clamps its dt here; prewarm in steps no larger

// 7 orbs, most-dominant palette slots to the big hero orbs.
export const ORB_PLAN: readonly { tier: Tier; colorIndex: number }[] = [
  { tier: 'hero', colorIndex: 0 },
  { tier: 'hero', colorIndex: 1 },
  { tier: 'mid', colorIndex: 2 },
  { tier: 'mid', colorIndex: 3 },
  { tier: 'mid', colorIndex: 0 },
  { tier: 'accent', colorIndex: 4 },
  { tier: 'accent', colorIndex: 2 }
]

export const BLOOM_MAX_DELAY = 0.12 // seconds — per-orb bloom onset jitter

export interface Orb {
  tier: Tier
  bx: number // base position, normalized [0,1] of width/height (derived from the walks)
  by: number
  walkX: DrunkWalk // ±1, mapped through softBound and scaled by reach
  walkY: DrunkWalk
  reach: number
  rate: number
  sizePct: number // diameter as a fraction of S
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
  parallax: number
  bloomDelay: number // per-orb bloom onset jitter (seconds), breaks lockstep
}

export type Rng = () => number

const rand = (rng: Rng, min: number, max: number): number => min + rng() * (max - min)

export function makeOrb(plan: { tier: Tier; colorIndex: number }, rng: Rng): Orb {
  const t = TIERS[plan.tier]
  const orb: Orb = {
    tier: plan.tier,
    bx: 0.5,
    by: 0.5,
    walkX: new DrunkWalk(rng, 1),
    walkY: new DrunkWalk(rng, 1),
    reach: t.reach,
    rate: t.rate,
    sizePct: rand(rng, t.size[0], t.size[1]),
    ax: rand(rng, 0.05, 0.07),
    ay: rand(rng, 0.05, 0.07),
    px1: rand(rng, 15, 19),
    px2: rand(rng, 27, 31),
    py1: rand(rng, 18, 22),
    py2: rand(rng, 29, 33),
    breathP: rand(rng, 12, 20),
    breathPhase: rand(rng, 0, Math.PI * 2),
    baseAlpha: rand(rng, t.alpha[0], t.alpha[1]),
    colorIndex: plan.colorIndex,
    parallax: t.parallax,
    bloomDelay: rand(rng, 0, BLOOM_MAX_DELAY)
  }
  // Prewarm in walk time (independent of rate), the walk's own mixing clock.
  for (let s = 0; s < PREWARM_S; s += STEP_S) advanceOrb(orb, STEP_S / orb.rate)
  return orb
}

export function makeField(rng: Rng): Orb[] {
  return ORB_PLAN.map((p) => makeOrb(p, rng))
}

function advanceOrb(o: Orb, dt: number): void {
  // Ticking the walk on a scaled clock scales its speed without changing its
  // spread; the shared DrunkWalk keeps its own constants (Chapel uses them).
  o.walkX.tick(dt * o.rate)
  o.walkY.tick(dt * o.rate)
  o.bx = 0.5 + 0.5 * o.reach * softBound(o.walkX.value)
  o.by = 0.5 + 0.5 * o.reach * softBound(o.walkY.value)
}

/** Advance every orb's drift by `dt` seconds. */
export function advanceField(orbs: Orb[], dt: number): void {
  for (const o of orbs) advanceOrb(o, dt)
}

/**
 * Where an orb is drawn, in CSS px: drift + wobble, minus the eased pointer
 * offset (orbs drift *opposite* the cursor — "peering through a deeper field").
 */
export function orbCenter(
  o: Orb,
  elapsed: number,
  w: number,
  h: number,
  pointerX: number,
  pointerY: number
): { x: number; y: number } {
  const s = Math.min(w, h)
  const ox = o.ax * (Math.sin(elapsed / o.px1) + Math.sin(elapsed / o.px2))
  const oy = o.ay * (Math.sin(elapsed / o.py1) + Math.sin(elapsed / o.py2))
  return {
    x: o.bx * w + ox * s - pointerX * o.parallax * s,
    y: o.by * h + oy * s - pointerY * o.parallax * s
  }
}

// --- the density metric ------------------------------------------------------

// The vignette darkens a band at the pane's edge; colour there reads as nothing.
export const EDGE_BAND = 0.06
// An orb counts as visible when at least this share of its disc is in the pane,
// outside the edge band and outside the (opaque) album art.
export const VISIBLE_SHARE = 0.25

// Sample points over a unit disc: three rings of 24, area-weighted by radius so
// the outer ring (most of the disc's area) counts most.
const RINGS = [0.3, 0.6, 0.9]
const SPOKES = 24
const SAMPLES: { dx: number; dy: number; w: number }[] = []
for (const r of RINGS) {
  for (let i = 0; i < SPOKES; i++) {
    const a = (i / SPOKES) * Math.PI * 2
    SAMPLES.push({ dx: Math.cos(a) * r, dy: Math.sin(a) * r, w: r })
  }
}
const SAMPLE_WEIGHT = SAMPLES.reduce((acc, p) => acc + p.w, 0)

/**
 * Share of a disc (centre x, y and radius in px) that the viewer can see: in the
 * pane, outside the edge band, outside `art` (normalised pane rect, or null).
 */
export function visibleShare(
  x: number,
  y: number,
  radius: number,
  w: number,
  h: number,
  art: PaneRect | null
): number {
  let seen = 0
  for (const p of SAMPLES) {
    const nx = (x + p.dx * radius) / w
    const ny = (y + p.dy * radius) / h
    if (nx < EDGE_BAND || nx > 1 - EDGE_BAND || ny < EDGE_BAND || ny > 1 - EDGE_BAND) continue
    if (art && nx > art.x0 && nx < art.x1 && ny > art.y0 && ny < art.y1) continue
    seen += p.w
  }
  return seen / SAMPLE_WEIGHT
}

/** How many orbs are meaningfully on screen right now (at base radius). */
export function visibleOrbCount(
  orbs: readonly Orb[],
  elapsed: number,
  w: number,
  h: number,
  art: PaneRect | null
): number {
  const s = Math.min(w, h)
  let n = 0
  for (const o of orbs) {
    const { x, y } = orbCenter(o, elapsed, w, h, 0, 0)
    if (visibleShare(x, y, o.sizePct * s * 0.5, w, h, art) >= VISIBLE_SHARE) n++
  }
  return n
}

// --- sprite re-bake gate -------------------------------------------------------

/**
 * Decides when the orb sprites need baking again (KAMP-777's baked blur is the
 * expensive part: 7 blurred canvases). A bake is due when the geometry key
 * changes or the colours do. Colours are compared by array *reference*, which is
 * PaletteCrossfade's contract: once settled `current()` returns the same target
 * array every call, and mid-fade a fresh blend each call. So a settled field never
 * re-bakes, a fade re-bakes on every drawn frame (as before), and a palette that
 * lands while the loop is stopped re-bakes once.
 */
export class SpriteBakeGate {
  private key = ''
  private colors: unknown = null

  shouldBake(key: string, colors: readonly unknown[]): boolean {
    if (key === this.key && colors === this.colors) return false
    this.key = key
    this.colors = colors
    return true
  }

  /** Force the next call to bake (a tune() changed the scale or blur). */
  invalidate(): void {
    this.key = ''
  }
}
