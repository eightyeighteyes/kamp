// KAMP-768: small pure helpers shared by the Canvas 2D particle backgrounds
// (Sparks, Wintermute). Framework-free so `node --test` covers them under
// strip-only TypeScript: no enums, no parameter properties.

import { smoothstep } from './rareSchedule.ts'

/** A rect over the pane, normalised 0..1 (DOM y-down), as ContentRect. */
export interface PaneRect {
  x0: number
  y0: number
  x1: number
  y1: number
}

export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t

export const clampAbs = (v: number, max: number): number => Math.max(-max, Math.min(max, v))

/** Index into `weights` for a uniform sample `u` (weights should sum to ~1). */
export function pick(weights: readonly number[], u: number): number {
  let acc = 0
  for (let i = 0; i < weights.length; i++) {
    acc += weights[i]
    if (u < acc) return i
  }
  return weights.length - 1
}

/** mulberry32: a deterministic RNG, for still frames that must not reshuffle. */
export function seededRng(seed: number): () => number {
  let a = seed
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * Alpha multiplier that quiets particles behind the title text so it stays
 * legible: 1 away from the rect, `quiet` inside it, feathered over `feather`
 * pane heights. nx, ny are normalised pane coordinates.
 */
export function titleQuiet(
  rect: PaneRect | null,
  aspect: number,
  nx: number,
  ny: number,
  quiet: number,
  feather: number
): number {
  if (!rect) return 1
  // Distance outside the rect, in pane heights.
  const dx = Math.max(rect.x0 - nx, 0, nx - rect.x1) * aspect
  const dy = Math.max(rect.y0 - ny, 0, ny - rect.y1)
  const inside = 1 - smoothstep(Math.hypot(dx, dy) / feather)
  return 1 - (1 - quiet) * inside
}
