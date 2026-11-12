// KAMP-768: offscreen canvas helper shared by the baked-sprite backgrounds
// (Sparks' glow sprites, Wintermute's flakes).

export type SpriteContext = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D

/** A square offscreen canvas; OffscreenCanvas where available. */
export function makeCanvas(side: number): {
  canvas: CanvasImageSource
  ctx: SpriteContext | null
} {
  if (typeof OffscreenCanvas !== 'undefined') {
    const c = new OffscreenCanvas(side, side)
    return { canvas: c, ctx: c.getContext('2d') }
  }
  const c = document.createElement('canvas')
  c.width = c.height = side
  return { canvas: c, ctx: c.getContext('2d') }
}
