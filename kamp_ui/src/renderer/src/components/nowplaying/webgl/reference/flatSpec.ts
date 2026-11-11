// KAMP-780: the flat reference background. Diagnostics-only (`__kampGL.reference()`).
//
// Two jobs:
//   * wiring check: it reads every standard uniform, so theme bg, palette
//     crossfade, pointer and level are all visibly exercised;
//   * measurement subject: per-pixel work is trivial, so its GPU cost is the cost
//     of the base itself (context, quarter-res backing, 30fps draws). It should sit
//     within ~2 GPU-busy points of "None" (powermetrics + summarize_powermetrics.py).

import { GLSL_DITHER, GLSL_HEADER, GLSL_NOISE } from '../glsl'
import type { ShaderSpec } from '../shaderEngine'

const FRAG = `${GLSL_HEADER}${GLSL_NOISE}${GLSL_DITHER}
void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  // A faint wash of the lead swatch, offset by the pointer and swelling with the
  // level: enough to see each input arrive, too little to cost anything.
  vec2 c = vec2(0.5) + vec2(uPointer.x, -uPointer.y) * 0.05;
  float wash = (1.0 - smoothstep(0.0, 0.75, distance(uv, c))) * (0.10 + 0.08 * uLevel);
  vec3 col = mix(uBg, uPalette[0], wash);
  gl_FragColor = vec4(dither(col, uTime), 1.0);
}
`

export const FLAT_REFERENCE_SPEC: ShaderSpec = {
  name: 'webgl-reference',
  frag: FRAG
}
