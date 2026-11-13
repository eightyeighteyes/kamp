// KAMP-779: Bokeh's orb field as a pure model, so `node --test` can measure what
// the eye sees: how many orbs are actually on screen, and for how long.
// Framework-free under strip-only TypeScript (KAMP-765): no enums, no parameter
// properties, `.ts` sibling specifiers.
//
// The full drawn position (drift + wobble + parallax) lives here, not in the
// engine, so the density test and the renderer can never disagree about where an
// orb is.

import type { PaneRect } from './particleUtil.ts'

export type Tier = 'hero' | 'mid' | 'accent'

export interface TierSpec {
  size: [number, number] // diameter, fraction of S = min(w, h)
  alpha: [number, number]
  cross: [number, number] // seconds to drift one pane width
  parallax: number // cursor-parallax amplitude, fraction of S
}

// `parallax` is intentionally *inverted* from physical depth: hero (big) orbs move
// least and the small accents move most, so the calm backdrop stays still while
// the sparkle dances. It reads as atmosphere, not a literal 3D depth cue.
export const TIERS: Record<Tier, TierSpec> = {
  hero: { size: [0.55, 0.75], alpha: [0.32, 0.4], cross: [90, 150], parallax: 0.02 },
  mid: { size: [0.3, 0.45], alpha: [0.4, 0.5], cross: [60, 110], parallax: 0.04 },
  accent: { size: [0.12, 0.22], alpha: [0.5, 0.56], cross: [45, 70], parallax: 0.056 }
}

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

const EDGE_MARGIN = 0.45 // wrap distance beyond [0,1] before respawning an orb
export const BLOOM_MAX_DELAY = 0.12 // seconds — per-orb bloom onset jitter

export interface Orb {
  tier: Tier
  bx: number // drifting base position, normalized [0,1] of width/height
  by: number
  vx: number // drift velocity, normalized units / second
  vy: number
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
  const cross = rand(rng, t.cross[0], t.cross[1])
  const angle = rand(rng, 0, Math.PI * 2)
  const speed = 1 / cross // ~1.0 normalized traversal over `cross` seconds
  return {
    tier: plan.tier,
    bx: rand(rng, 0, 1),
    by: rand(rng, 0, 1),
    vx: Math.cos(angle) * speed,
    vy: Math.sin(angle) * speed,
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
}

export function makeField(rng: Rng): Orb[] {
  return ORB_PLAN.map((p) => makeOrb(p, rng))
}

/** Advance every orb's drift by `dt` seconds. */
export function advanceField(orbs: Orb[], dt: number): void {
  for (const o of orbs) {
    o.bx += o.vx * dt
    o.by += o.vy * dt
    if (o.bx < -EDGE_MARGIN) o.bx += 1 + 2 * EDGE_MARGIN
    else if (o.bx > 1 + EDGE_MARGIN) o.bx -= 1 + 2 * EDGE_MARGIN
    if (o.by < -EDGE_MARGIN) o.by += 1 + 2 * EDGE_MARGIN
    else if (o.by > 1 + EDGE_MARGIN) o.by -= 1 + 2 * EDGE_MARGIN
  }
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
