// KAMP-767: pre-rendered glow sprites for the Sparks background.
//
// Each spark is a drawImage of a sprite baked once per palette, size bucket and
// backing scale. The blur is baked in here, with the offscreen context's filter,
// instead of a CSS `filter` on the canvas: Bokeh's `blur(28px)` re-processed the
// whole pane every frame and was about 50 of its 65 GPU points (KAMP-776/777).
// Building a radial gradient per spark per frame would cost far more again.
//
// Sprites are baked at BACKING resolution. A sprite baked at CSS size and shrunk
// by drawImage would shrink its blur too, so the engine re-bakes whenever the
// backing scale or the pane height changes.

import type { RGB } from '../palette'
import { makeCanvas } from '../sprites'

export interface GlowSheet {
  /** [palette slot][size bucket] */
  sprites: CanvasImageSource[][]
  /** Half the sprite's side in BACKING px, per size bucket. */
  half: number[]
}

// The core leans toward warm white, so even a cool palette reads as fire.
const CORE: RGB = { r: 255, g: 238, b: 210 }
const CORE_STOP = 0.3
// The halo's reach (before blur) as a multiple of the core radius, and its opacity.
const HALO_SCALE = 2.5
const HALO_ALPHA = 0.35

/**
 * Bake one sheet.
 * @param colors palette swatches
 * @param radiusPx core radius per size bucket, in backing px
 * @param blurPx baked blur (CSS `blur()` radius = Gaussian sigma) in backing px; 0 = none
 */
export function bakeSheet(colors: RGB[], radiusPx: number[], blurPx: number): GlowSheet {
  // 3 sigma of padding, so the blurred halo is never clipped at the sprite's edge.
  const pad = Math.ceil(3 * blurPx)
  const half = radiusPx.map((r) => Math.max(1, Math.ceil(r * HALO_SCALE) + pad))
  const sprites = colors.map((c) =>
    radiusPx.map((r, b) => {
      const side = half[b] * 2
      const { canvas, ctx } = makeCanvas(side)
      if (!ctx) return canvas
      const cx = half[b]
      const radius = Math.max(0.5, r)
      // Two passes. Blurring the whole spark spreads a 3 px core so thin it all
      // but vanishes, which is exactly what Bokeh's blur did to small particles.
      // So only the halo is blurred, and a crisp core is drawn over it.
      if (blurPx > 0) ctx.filter = `blur(${blurPx}px)`
      ctx.fillStyle = `rgba(${c.r},${c.g},${c.b},${HALO_ALPHA})`
      ctx.beginPath()
      ctx.arc(cx, cx, radius * HALO_SCALE * 0.6, 0, Math.PI * 2)
      ctx.fill()
      ctx.filter = 'none'
      const g = ctx.createRadialGradient(cx, cx, 0, cx, cx, radius)
      g.addColorStop(0, `rgba(${CORE.r},${CORE.g},${CORE.b},1)`)
      g.addColorStop(CORE_STOP, `rgba(${c.r},${c.g},${c.b},0.9)`)
      g.addColorStop(1, `rgba(${c.r},${c.g},${c.b},0)`)
      ctx.fillStyle = g
      ctx.beginPath()
      ctx.arc(cx, cx, radius, 0, Math.PI * 2)
      ctx.fill()
      return canvas
    })
  )
  return { sprites, half }
}
