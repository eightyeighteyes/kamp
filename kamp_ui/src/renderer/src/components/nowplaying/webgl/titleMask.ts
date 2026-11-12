// KAMP-772: the title-legibility mask shared by the shader backgrounds. Halation
// and Borealis each carried an identical copy; Tannhäuser would have been the
// third. Append GLSL_TITLE_MASK to a fragment shader and call setTitleUniform
// from setUniforms; then `titleMask(uv)` is 1 behind the title text (soft-edged)
// and 0 elsewhere. Pull busy detail toward uBg by it, scaled by uArtShown.

import type { GlQuad } from './glQuad'
import type { FrameInfo } from './shaderEngine'

export const GLSL_TITLE_MASK = `
uniform vec4 uTitle;    // title text box in uv (y up); x1 <= x0 means none
float titleMask(vec2 uv) {
  if (uTitle.z <= uTitle.x) return 0.0;
  float m = 0.03;
  vec2 a = smoothstep(uTitle.xy - m, uTitle.xy, uv)
         * (1.0 - smoothstep(uTitle.zw, uTitle.zw + m, uv));
  return a.x * a.y;
}
`

/** Upload the title's text box, or "none" while it (and the art) are hidden. */
export function setTitleUniform(gl: WebGLRenderingContext, quad: GlQuad, frame: FrameInfo): void {
  const c = frame.titleRect
  if (c && frame.artShown > 0) {
    // DOM y-down to GL y-up.
    gl.uniform4f(quad.u('uTitle'), c.x0, 1 - c.y1, c.x1, 1 - c.y0)
  } else {
    gl.uniform4f(quad.u('uTitle'), 0, 0, 0, 0)
  }
}
