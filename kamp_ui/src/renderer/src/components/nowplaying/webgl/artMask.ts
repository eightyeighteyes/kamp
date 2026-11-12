// KAMP-772: skip the shader's work behind the album art. The art is opaque, so
// every pixel the shader draws inside its box is thrown away, yet at the default
// layout it covers a large share of the pane. Append GLSL_ART_SKIP to a fragment
// shader, call setArtUniform from setUniforms, and start main() with
//
//   if (behindArt(uv)) { gl_FragColor = vec4(uBg, 1.0); return; }
//
// Fragments run in blocks, and the art is one large convex box, so nearly every
// block is wholly inside or wholly outside it and the branch costs nothing.

import type { GlQuad } from './glQuad'
import type { FrameInfo } from './shaderEngine'

export const GLSL_ART_SKIP = `
uniform vec4 uArt;      // opaque art box in uv (y up), inset past its corners; x1 <= x0 means none
bool behindArt(vec2 uv) {
  if (uArt.z <= uArt.x) return false;
  // The canvas is upscaled with bilinear filtering, so a visible pixel at the
  // art's edge can read one backing pixel inside it. Keep a margin drawn.
  vec2 m = 1.5 / uRes;
  return all(greaterThan(uv, uArt.xy + m)) && all(lessThan(uv, uArt.zw - m));
}
`

/** Upload the art's box, or "none" while the art is hidden. */
export function setArtUniform(gl: WebGLRenderingContext, quad: GlQuad, frame: FrameInfo): void {
  const a = frame.artRect
  // Not on still frames (paused repaints, the reduced-motion still): they are
  // one-offs, so there is nothing to save, and drawing them whole means a rect
  // that is momentarily stale can never leave a flat box on screen.
  if (a && !frame.still) {
    // DOM y-down to GL y-up.
    gl.uniform4f(quad.u('uArt'), a.x0, 1 - a.y1, a.x1, 1 - a.y0)
  } else {
    gl.uniform4f(quad.u('uArt'), 0, 0, 0, 0)
  }
}
