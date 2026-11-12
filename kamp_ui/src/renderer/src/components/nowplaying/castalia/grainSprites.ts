// KAMP-773: pre-rendered grain sprites for the Castalia background.
//
// One sprite per palette slot: a crisp one-pixel core drawn over a soft halo that
// carries the ~2px blur. The blur is part of the animation's definition and is
// baked here with the offscreen context's filter, never a CSS `filter` on the
// canvas (Bokeh's re-processed the whole pane every frame, KAMP-776/777). Only
// the halo is blurred: a blurred one-pixel grain spreads so thin it vanishes, the
// way Bokeh's blur swallowed its small particles (KAMP-767).
//
// Sprites are baked at BACKING resolution, so drawImage never shrinks the blur;
// the engine re-bakes when the backing scale changes.

import type { RGB } from '../palette'
import { makeCanvas } from '../sprites'

export interface GrainSheet {
  /** One sprite per palette slot. */
  sprites: CanvasImageSource[]
  /** Half the sprite's side, in BACKING px. */
  half: number
}

// Pulled toward white so a dark swatch still reads as sand on a dark plate.
// 0.3 left dark album colors muddy against the plate in review.
const LIGHT_MIX = 0.5
// The halo sits under the core at this strength; the core is full strength.
const HALO_ALPHA = 0.8

function lighten(c: RGB): RGB {
  return {
    r: Math.round(c.r + (255 - c.r) * LIGHT_MIX),
    g: Math.round(c.g + (255 - c.g) * LIGHT_MIX),
    b: Math.round(c.b + (255 - c.b) * LIGHT_MIX)
  }
}

/**
 * Bake one grain sheet.
 * @param colors palette swatches
 * @param corePx the grain's radius in backing px (half a CSS pixel times the scale)
 * @param blurPx the halo's Gaussian sigma in backing px; 0 = no halo (the no-blur variant)
 */
export function bakeGrainSheet(colors: RGB[], corePx: number, blurPx: number): GrainSheet {
  const core = Math.max(0.5, corePx)
  // 3 sigma of padding so the halo is never clipped at the sprite's edge.
  const half = Math.max(1, Math.ceil(core + 3 * blurPx + 1))
  const sprites = colors.map((raw) => {
    const c = lighten(raw)
    const { canvas, ctx } = makeCanvas(half * 2)
    if (!ctx) return canvas
    const fill = (a: number): string => `rgba(${c.r},${c.g},${c.b},${a})`
    if (blurPx > 0) {
      ctx.filter = `blur(${blurPx}px)`
      ctx.fillStyle = fill(HALO_ALPHA)
      ctx.beginPath()
      ctx.arc(half, half, Math.max(1, core * 1.5), 0, Math.PI * 2)
      ctx.fill()
      ctx.filter = 'none'
    }
    ctx.fillStyle = fill(1)
    ctx.beginPath()
    ctx.arc(half, half, core, 0, Math.PI * 2)
    ctx.fill()
    return canvas
  })
  return { sprites, half }
}
