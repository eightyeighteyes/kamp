// KAMP-773: Castalia's plate. Instead of a flat --bg, a radial gradient from the
// top-left corner carries up to three bands of album colour, faded to --bg at the
// far corner, and breathes slowly.
//
// Framework-free so `node --test` covers it. The engine draws it as ONE opaque
// fill: every stop is pre-blended with --bg, so the gradient replaces the solid
// fill rather than adding a second full-pane pass. A Canvas 2D background pays
// per pixel filled per frame (KAMP-773 measurements), so a second pass would
// cost about as much as the grains.

import type { Rgb } from './paletteUniforms'

export const MAX_BANDS = 3
// Band centres along the corner-to-far-corner radius. The last stop is --bg.
const BAND_OFFSETS = [0, 0.3, 0.6]
// Each band is mixed into --bg less than the one before, so colour fades outward.
const BAND_FALLOFF = [1, 0.75, 0.5]
// The breath: strength swings between these, as a share of the album colour over --bg.
// Kept low so the grains and the title stay the brightest things on the plate.
export const STRENGTH_MIN = 0.12
export const STRENGTH_MAX = 0.22
export const PULSE_PERIOD_S = 10

export interface BackdropStop {
  offset: number
  color: Rgb
}

/**
 * Exactly MAX_BANDS colours: the palette's first three, or the ones it has
 * repeated. A fixed count means a crossfade between a long palette and a short
 * one never drops a band in a single frame.
 */
export function bandColors(colors: readonly Rgb[], fallback: Rgb): Rgb[] {
  const src = colors.length > 0 ? colors : [fallback]
  return Array.from({ length: MAX_BANDS }, (_, i) => src[Math.min(i, src.length - 1)])
}

/**
 * The breath at `t` seconds: a raised cosine, so it eases at both extremes with no
 * corner. Reduced motion passes null and gets the middle of the range.
 */
export function pulseStrength(t: number | null): number {
  if (t == null) return (STRENGTH_MIN + STRENGTH_MAX) / 2
  const k = (1 - Math.cos((2 * Math.PI * t) / PULSE_PERIOD_S)) / 2
  return STRENGTH_MIN + (STRENGTH_MAX - STRENGTH_MIN) * k
}

const mix = (a: Rgb, b: Rgb, k: number): Rgb => ({
  r: a.r + (b.r - a.r) * k,
  g: a.g + (b.g - a.g) * k,
  b: a.b + (b.b - a.b) * k
})

/** The gradient's stops, each already blended over --bg, ending on --bg itself. */
export function backdropStops(bg: Rgb, bands: readonly Rgb[], strength: number): BackdropStop[] {
  const stops = bands.slice(0, MAX_BANDS).map((c, i) => ({
    offset: BAND_OFFSETS[i],
    color: mix(bg, c, strength * BAND_FALLOFF[i])
  }))
  stops.push({ offset: 1, color: bg })
  return stops
}
