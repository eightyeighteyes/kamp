// KAMP-767: where the title and the art sit over a Now Playing canvas. Moved
// unchanged from webgl/ShaderCanvas.tsx so the Canvas 2D Sparks background can
// share it; each caller hands the result to its own engine.

import type { ContentRect } from './webgl/shaderEngine'

export interface ContentRects {
  title: ContentRect | null
  art: ContentRect | null
}

/** `t` relative to the canvas box `c`, normalised (DOM y-down), shrunk by `inset` px. */
function normalise(c: DOMRect, t: DOMRect, inset = 0): ContentRect | null {
  if (t.width <= 2 * inset || t.height <= 2 * inset) return null
  return {
    x0: (t.left + inset - c.left) / c.width,
    x1: (t.right - inset - c.left) / c.width,
    y0: (t.top + inset - c.top) / c.height,
    y1: (t.bottom - inset - c.top) / c.height
  }
}

/**
 * Measure the title and the art relative to the canvas. Each is null while
 * hidden (they hide together, KAMP-769).
 *
 * The title is its TEXT box, not the element: `.now-playing-title` and
 * `.now-playing-meta` are full-width, so their boxes span the whole pane and
 * anything muted "behind the title" would be muted everywhere.
 *
 * The art is the element, inset by its corner radius so the rounded corners,
 * where the canvas shows through, stay outside it. Shaders skip the work inside
 * it (KAMP-772): the art is opaque, so those pixels are never seen.
 */
export function measureContent(canvas: HTMLCanvasElement): ContentRects {
  const pane = canvas.closest('.now-playing')
  const c = canvas.getBoundingClientRect()
  if (!pane || c.width === 0 || c.height === 0) return { title: null, art: null }
  let title: ContentRect | null = null
  const titleEl = pane.querySelector('.now-playing-title')
  if (titleEl) {
    const range = document.createRange()
    range.selectNodeContents(titleEl)
    title = normalise(c, range.getBoundingClientRect())
  }
  let art: ContentRect | null = null
  const artEl = pane.querySelector('.now-playing-art')
  if (artEl) {
    const radius = parseFloat(getComputedStyle(artEl).borderTopLeftRadius) || 0
    art = normalise(c, artEl.getBoundingClientRect(), radius)
  }
  return { title, art }
}
