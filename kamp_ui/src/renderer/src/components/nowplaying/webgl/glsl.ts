// KAMP-780: the shared GLSL chunk every shader background starts from. GLSL 100
// (WebGL 1) for the widest driver support. Build a fragment shader as
//
//   GLSL_HEADER + GLSL_NOISE + GLSL_DITHER + `void main() { ... }`
//
// The engine sets every uniform in GLSL_HEADER each frame; a shader that never
// reads one simply gets it optimised away (glQuad's u() then returns null and the
// upload is a no-op).

export const GLSL_HEADER = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes;        // backing-store size in pixels
uniform float uTime;      // seconds of animation (frozen while stopped)
uniform vec3 uPalette[5]; // album palette, 0..1, crossfaded on the CPU
uniform vec3 uBg;         // themed --bg, 0..1: the canvas is opaque, so paint it
uniform vec2 uPointer;    // eased cursor over the pane, -1..1 (DOM y-down)
uniform float uPointerActive; // eased 0..1: the cursor is over the pane (uPointer recentres on leave)
uniform float uLevel;     // delayed, smoothed audio level, 0..1
`

export const GLSL_NOISE = `
float hash1(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }
vec2 hash2(vec2 p) {
  p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
  return fract(sin(p) * 43758.5453);
}
// 2D value noise, 0..1, smooth (Hermite-interpolated lattice).
float valueNoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = hash1(i);
  float b = hash1(i + vec2(1.0, 0.0));
  float c = hash1(i + vec2(0.0, 1.0));
  float d = hash1(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}
`

// Dark, slow gradients band visibly in 8-bit output without dither. Chromium
// dithers Canvas 2D gradients, which is why Bokeh never showed this (KAMP-776).
// Needs GLSL_NOISE. Call last: col = dither(col, uTime);
export const GLSL_DITHER = `
vec3 dither(vec3 col, float t) {
  return col + (hash1(gl_FragCoord.xy + fract(t) * 61.0) - 0.5) / 255.0;
}
`
