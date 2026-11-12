// KAMP-768: pre-rendered sprites for the Wintermute background.
//
// Flakes: one soft disc per palette slot and depth layer, with that layer's
// depth-of-field blur baked in by the offscreen context's filter. There is no
// CSS `filter` on the canvas: Bokeh's `blur(28px)` re-processed the whole pane
// every frame and was about 50 of its 65 GPU points (KAMP-776/777). Sheets are
// baked at BACKING resolution, so drawImage never shrinks the blur (KAMP-767).
//
// The close flake: a six-armed crystal, baked ONCE as a white alpha mask in two
// focus levels (sharp and soft) at a fixed high resolution, independent of the
// pane, so its arms stay crisp when drawn down. A palette only tints the masks
// (a `source-in` fill, no blur), so nothing heavy runs on the main thread at the
// moment a track-change gust is playing.

import type { RGB } from '../palette'
import { makeCanvas, type SpriteContext } from '../sprites'

export interface FlakeSheet {
  /** [palette slot][layer] */
  sprites: CanvasImageSource[][]
  /** Half the sprite's side in BACKING px, per layer. */
  half: number[]
}

export interface CrystalPair {
  sharp: CanvasImageSource
  soft: CanvasImageSource
}

// How far each swatch is pulled toward white, so it reads as snow and not confetti.
const WHITE_MIX = 0.45
// The disc is a radial falloff: solid to CORE_STOP of its radius, then fading out.
const CORE_STOP = 0.35

/** Side of the crystal masks, in px. Drawn down to at most ~120 backing px. */
export const CRYSTAL_SIDE = 256
const ARM_W = CRYSTAL_SIDE * 0.022 // stays >= 2 backing px once drawn down
const CRYSTAL_SOFT_BLUR = 9 // px at CRYSTAL_SIDE
const CRYSTAL_PAD = 0.1 // of the side, so the soft level's blur isn't clipped

function snowTint(c: RGB): RGB {
  return {
    r: Math.round(c.r + (255 - c.r) * WHITE_MIX),
    g: Math.round(c.g + (255 - c.g) * WHITE_MIX),
    b: Math.round(c.b + (255 - c.b) * WHITE_MIX)
  }
}

/**
 * Bake one flake sheet.
 * @param colors palette swatches
 * @param radiusPx disc radius per layer, in backing px
 * @param blurPx baked blur (Gaussian sigma) per layer in backing px; 0 = none
 */
export function bakeFlakeSheet(colors: RGB[], radiusPx: number[], blurPx: number[]): FlakeSheet {
  // 3 sigma of padding, so the blur is never clipped at the sprite's edge.
  const half = radiusPx.map((r, l) => Math.max(1, Math.ceil(r + 3 * blurPx[l])))
  const sprites = colors.map((raw) => {
    const c = snowTint(raw)
    return radiusPx.map((r, l) => {
      const side = half[l] * 2
      const { canvas, ctx } = makeCanvas(side)
      if (!ctx) return canvas
      const cx = half[l]
      const radius = Math.max(0.75, r)
      if (blurPx[l] > 0) ctx.filter = `blur(${blurPx[l]}px)`
      const g = ctx.createRadialGradient(cx, cx, 0, cx, cx, radius)
      g.addColorStop(0, `rgba(${c.r},${c.g},${c.b},1)`)
      g.addColorStop(CORE_STOP, `rgba(${c.r},${c.g},${c.b},0.85)`)
      g.addColorStop(1, `rgba(${c.r},${c.g},${c.b},0)`)
      ctx.fillStyle = g
      ctx.beginPath()
      ctx.arc(cx, cx, radius, 0, Math.PI * 2)
      ctx.fill()
      return canvas
    })
  })
  return { sprites, half }
}

/** Six arms with paired side branches and a small hexagonal hub. */
function drawCrystal(ctx: SpriteContext, side: number): void {
  const c = side / 2
  const arm = side * (0.5 - CRYSTAL_PAD)
  ctx.strokeStyle = '#fff'
  ctx.fillStyle = '#fff'
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.translate(c, c)
  for (let k = 0; k < 6; k++) {
    ctx.save()
    ctx.rotate((k * Math.PI) / 3)
    ctx.lineWidth = ARM_W
    ctx.beginPath()
    ctx.moveTo(0, 0)
    ctx.lineTo(arm, 0)
    ctx.stroke()
    // Branches shorten toward the tip, at 60 degrees off the arm.
    ctx.lineWidth = ARM_W * 0.8
    for (const [at, len] of [
      [0.32, 0.3],
      [0.55, 0.24],
      [0.76, 0.15]
    ]) {
      const bx = arm * at
      const bl = arm * len
      ctx.beginPath()
      ctx.moveTo(bx + bl * 0.5, -bl * 0.866)
      ctx.lineTo(bx, 0)
      ctx.lineTo(bx + bl * 0.5, bl * 0.866)
      ctx.stroke()
    }
    ctx.restore()
  }
  ctx.beginPath()
  for (let k = 0; k < 6; k++) {
    const a = (k * Math.PI) / 3 + Math.PI / 6
    const r = arm * 0.12
    if (k === 0) ctx.moveTo(r * Math.cos(a), r * Math.sin(a))
    else ctx.lineTo(r * Math.cos(a), r * Math.sin(a))
  }
  ctx.closePath()
  ctx.fill()
  ctx.setTransform(1, 0, 0, 1, 0, 0)
}

/** The white crystal masks, baked once per engine. */
export function bakeCrystalMasks(): CrystalPair {
  const sharp = makeCanvas(CRYSTAL_SIDE)
  if (sharp.ctx) drawCrystal(sharp.ctx, CRYSTAL_SIDE)
  const soft = makeCanvas(CRYSTAL_SIDE)
  if (soft.ctx) {
    soft.ctx.filter = `blur(${CRYSTAL_SOFT_BLUR}px)`
    soft.ctx.drawImage(sharp.canvas, 0, 0)
    soft.ctx.filter = 'none'
    // A faint disc under the blur, so the out-of-focus flake reads as a soft
    // round blob, the way a defocused highlight does, rather than a smudged star.
    soft.ctx.globalAlpha = 0.35
    soft.ctx.beginPath()
    soft.ctx.arc(CRYSTAL_SIDE / 2, CRYSTAL_SIDE / 2, CRYSTAL_SIDE * 0.3, 0, Math.PI * 2)
    soft.ctx.fillStyle = '#fff'
    soft.ctx.filter = `blur(${CRYSTAL_SOFT_BLUR * 2}px)`
    soft.ctx.fill()
    soft.ctx.filter = 'none'
    soft.ctx.globalAlpha = 1
  }
  return { sharp: sharp.canvas, soft: soft.canvas }
}

/** Tint both masks with one swatch. Cheap: two fills, no blur. */
export function tintCrystal(masks: CrystalPair, color: RGB): CrystalPair {
  const c = snowTint(color)
  const tint = (mask: CanvasImageSource): CanvasImageSource => {
    const { canvas, ctx } = makeCanvas(CRYSTAL_SIDE)
    if (!ctx) return mask
    ctx.drawImage(mask, 0, 0)
    ctx.globalCompositeOperation = 'source-in'
    ctx.fillStyle = `rgb(${c.r},${c.g},${c.b})`
    ctx.fillRect(0, 0, CRYSTAL_SIDE, CRYSTAL_SIDE)
    return canvas
  }
  return { sharp: tint(masks.sharp), soft: tint(masks.soft) }
}

/** Half of the crystal sprite's drawn side for a flake of radius r (any unit). */
export function crystalHalf(r: number): number {
  return r / (1 - 2 * CRYSTAL_PAD)
}
