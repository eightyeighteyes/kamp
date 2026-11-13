// KAMP-777: geometry for Bokeh's baked orb sprites.
//
// Bokeh used to soften its orbs with a CSS `filter: blur(28px)` over the whole
// canvas, re-run every frame: about 50 of its 65 GPU points (KAMP-776). Each orb
// is now a sprite with that same blur baked in once, then drawn with drawImage.
//
// Why bake a real blur instead of widening the gradient: the blur's sigma is fixed
// in CSS px while the orbs scale with the pane, so the blurred profile differs per
// orb and per pane size. No single hand-tuned falloff reproduces it; baking the
// blur at the actual radius/sigma ratio does, by construction.
//
// Pure and framework-free so `node --test` covers it (strip-only TypeScript).

import { blurPad } from './particleUtil.ts'

/** The softness the CSS filter used to apply: Gaussian sigma, in CSS px. */
export const BOKEH_BLUR_CSS = 28

export interface OrbSprite {
  /** Orb radius as baked, in backing px. */
  radius: number
  /** Baked blur sigma, in backing px. */
  sigma: number
  /** Half the sprite's side, in backing px: the radius plus the blur's tail. */
  half: number
}

/**
 * Size one sprite.
 * @param radiusCss the orb's base radius (no breath or bloom), CSS px
 * @param blurCss the blur sigma, CSS px; 0 = none
 * @param scale backing px per CSS px
 */
export function orbSprite(radiusCss: number, blurCss: number, scale: number): OrbSprite {
  // Both in backing px: a sprite drawn at a different size than it was baked
  // scales its blur with it (KAMP-767), so sigma has to follow the scale too.
  const radius = radiusCss * scale
  const sigma = Math.max(0, blurCss) * scale
  return { radius, sigma, half: Math.max(1, Math.ceil(radius + blurPad(sigma))) }
}

/**
 * Half-side to draw the sprite at, in CSS px, for an orb whose radius is now
 * `radiusCss`. Breath (±8%) and bloom (+16%) scale the baked sprite rather than
 * re-baking it; at those amounts the blur's growth with it is invisible.
 */
export function drawHalf(sp: OrbSprite, radiusCss: number, scale: number): number {
  const baseCss = sp.radius / scale
  return (sp.half / scale) * (baseCss > 0 ? radiusCss / baseCss : 1)
}

/**
 * globalAlpha for drawing a sprite baked at `peak` alpha so it shows `alpha`.
 * Sprites are baked at the brightest the orb ever gets (breath + bloom), because
 * globalAlpha can only dim, never brighten.
 */
export function alphaScale(alpha: number, peak: number): number {
  if (peak <= 0) return 0
  return Math.max(0, Math.min(1, alpha / peak))
}

/**
 * Identity of a sprite set's geometry: re-bake when it changes. The pane's short
 * side is rounded to backing px so layout jitter below a pixel doesn't re-bake.
 */
export function bakeKey(paneShortCss: number, scale: number, blurCss: number): string {
  return `${Math.round(paneShortCss * scale)}|${scale}|${blurCss}`
}
