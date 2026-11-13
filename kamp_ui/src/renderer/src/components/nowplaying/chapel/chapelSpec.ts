// KAMP-781: "Chapel" on the shared WebGL base (KAMP-780), ported from its Canvas 2D
// engine (KAMP-775) to give the fields surface: layered washes, dry-brush density
// and worked, irregular edges.
//
// The model (layout, offsets, breath, warmth, bloom, retarget) lives in
// shared/chapelLayout.ts and is node-tested; this file turns it into uniforms and
// draws. Rules the shader keeps (KAMP-774):
//   * Texture coordinates are pane-height units, translated only by each field's x
//     offset. The breath changes a field's height but never scales its texture, so
//     no pixel re-samples noise frame to frame. Nothing but dither() reads uTime.
//   * The texture only thins the paint (density <= TEXTURE_PEAK = 1), so the title
//     band's bound (chapelLayout fieldAlpha) holds whatever the noise does.
//   * Every feather is floored in backing pixels, so nothing stairs when upscaled.
// Draw order matches the Canvas engine: --bg, the fields (source-over, in order),
// the seam glows and the rare bloom (screen), then the --bg band behind the title.

import {
  ALPHA_BREATH,
  FIELD_ALPHA,
  FIELD_COUNT,
  FIELD_WARM_GAIN,
  FieldWarmth,
  ProportionTween,
  SeamBloom,
  SeamWarmth,
  TITLE_DIM,
  breathAt,
  breathedWeights,
  fieldColors,
  layoutFields,
  makeBreaths,
  randomOffsets,
  randomProportions,
  retargetDecision,
  type Breath
} from '../../../../../shared/chapelLayout'
import { PaletteCrossfade } from '../../../../../shared/paletteCrossfade'
import type { Rgb } from '../../../../../shared/paletteUniforms'
import { GLSL_ART_SKIP, setArtUniform } from '../webgl/artMask'
import { GLSL_DITHER, GLSL_HEADER, GLSL_NOISE } from '../webgl/glsl'
import type { ShaderSpec } from '../webgl/shaderEngine'

// --- tunables (see TUNING.md) ----------------------------------------------------
const CROSSFADE_MS = 4000 // slow, so the new palette arrives as the fields re-proportion
const RETARGET_MS = 7000 // track-change re-proportion and re-stagger
const POINTER_REACH = 0.12 // fraction of pane height within which a seam warms
const GLOW_ALPHA = 0.32 // peak seam-glow opacity
const BLOOM_ALPHA = 0.42 // peak opacity of the rare seam warming
const GLOW_HALF_H = 1.4 // glow band half-height, in feathers
const TITLE_FEATHER = 0.06 // of pane height: the band behind the title fades in over this
const WARM: Rgb = { r: 255, g: 222, b: 184 } // glows lean toward warm white

const f = (n: number): string => n.toFixed(4)

const FRAG = `${GLSL_HEADER}${GLSL_NOISE}${GLSL_DITHER}${GLSL_ART_SKIP}
uniform vec4 uField[${FIELD_COUNT}];    // x0, y0, x1, y1 in backing px, GL y-up
uniform vec3 uFieldCol[${FIELD_COUNT}];
uniform vec3 uFieldA[${FIELD_COUNT}];   // breath -1..1, warmth 0..1, x offset (px)
uniform vec2 uFeather;                  // vertical, horizontal (px)
uniform vec4 uSeam[${FIELD_COUNT - 1}]; // y, x0, x1 (px), glow alpha
uniform vec3 uSeamCol[${FIELD_COUNT - 1}];
uniform vec4 uBloom;                    // cx, cy, half-width, alpha (px, alpha)
uniform float uBloomRy;
uniform vec3 uBloomCol;
uniform vec3 uBand;                     // title top (px, GL), feather (px), strength
uniform float uTexture;                 // 1 textured, 0 flat (measurement variant)

const float FIELD_ALPHA = ${f(FIELD_ALPHA)};
const float ALPHA_BREATH = ${f(ALPHA_BREATH)};
const float WARM_GAIN = ${f(FIELD_WARM_GAIN)};
const float TITLE_DIM = ${f(TITLE_DIM)};
const vec3 WARM = vec3(${f(WARM.r / 255)}, ${f(WARM.g / 255)}, ${f(WARM.b / 255)});

// Texture. Scales are per pane height. Rothko, not ribbons: the outline stays
// nearly rectangular (a small slow waver plus a fine dry-brush fray), and the life
// is inside the field, in large cloudy shifts of density and a soft inner light.
const float TEX_FLOOR = 0.4;       // the thinnest the paint gets (density)
const float WASH_SCALE = 1.3;      // large clouds: layered washes
const vec2 WASH2_SCALE = vec2(2.6, 3.4);
const vec2 STREAK_SCALE = vec2(1.6, 14.0); // dry-brush: long in x, fine in y
const vec2 EDGE_LO_SCALE = vec2(4.0, 6.0); // the outline's slow waver
const vec2 EDGE_HI_SCALE = vec2(40.0, 60.0); // the fray: fine, or it reads as scallops
const float EDGE_LO = 0.15;        // waver, in feathers
const float EDGE_HI = 0.1;         // fray, in feathers
const float SCUMBLE = 0.75;        // how far the streaks break up the feathered edge
const float LIFT = 0.12;           // inner light: the dense middle lifts toward white
// The rim's shade. Darkening it put a band below --bg around every field (an
// outline, not an under-layer), so it is 0 until a lighter-hue rim is tried.
const float RIM = 0.0;
const float SEAM_STREAK = 0.6;     // how far brush streaks break up a seam's glow

// Smooth 0..1 across [e - w/2, e + w/2], width floored in backing pixels.
float ramp(float d, float w) {
  float ww = max(w, 3.0);
  return smoothstep(-0.5 * ww, 0.5 * ww, d);
}

vec3 screen(vec3 dst, vec3 src, float a) {
  return mix(dst, src + dst - src * dst, clamp(a, 0.0, 1.0));
}

void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  if (behindArt(uv)) { gl_FragColor = vec4(uBg, 1.0); return; }
  vec2 p = gl_FragCoord.xy;
  float H = uRes.y;

  // The title band (GL y-up: it covers everything below the title's top).
  float band = (1.0 - smoothstep(uBand.x, uBand.x + max(uBand.y, 3.0), p.y)) * uBand.z;

  vec3 col = uBg;
  float fv = uFeather.x;
  float fh = uFeather.y;
  // Each field's edge waver here, in vertical px, so a seam's glow can follow the
  // edges either side of it instead of running ruler-straight between them.
  float wvs[${FIELD_COUNT}];
  for (int i = 0; i < ${FIELD_COUNT}; i++) {
    wvs[i] = 0.0;
    vec4 r = uField[i];
    // Out as far as a seam glow reaches, so wvs is never 0 under a glow's fringe.
    float reach = max(fv, fh) * (1.0 + EDGE_LO + EDGE_HI) + fv * ${f(GLOW_HALF_H)};
    if (p.x < r.x - reach || p.x > r.z + reach || p.y < r.y - reach || p.y > r.w + reach) continue;
    float fi = float(i);
    // Pane-height units, moved with the field's x offset only (see header).
    vec2 q = vec2(p.x - uFieldA[i].z, p.y) / H + vec2(fi * 7.31, fi * 3.17);

    // A worked edge: a small slow waver and a fine fray, on every side.
    float waver = (valueNoise(q * EDGE_LO_SCALE) - 0.5) * 2.0 * EDGE_LO
                + (valueNoise(q * EDGE_HI_SCALE + 11.0) - 0.5) * 2.0 * EDGE_HI;
    float wv = waver * fv * uTexture;
    float wh = waver * fh * uTexture;
    wvs[i] = wv;
    float cov = ramp(p.y - r.y + wv, fv) * ramp(r.w - p.y + wv, fv)
              * ramp(p.x - r.x + wh, fh) * ramp(r.z - p.x + wh, fh);
    if (cov <= 0.0) continue;

    // Large cloudy washes and dry-brush streaks; they only thin the paint.
    float wash = valueNoise(q * WASH_SCALE);
    float wash2 = valueNoise(q * WASH2_SCALE + 37.0);
    float streak = valueNoise(q * STREAK_SCALE + 5.0);
    float tex = smoothstep(0.2, 0.8, 0.45 * wash + 0.3 * wash2 + 0.25 * streak);
    float density = mix(1.0, mix(TEX_FLOOR, 1.0, tex), uTexture);
    // Scumbled edge: the streaks break up the feather, more toward the outside.
    cov *= mix(1.0, mix(1.0 - SCUMBLE, 1.0, streak), (1.0 - cov) * uTexture);

    // Colour, not alpha, so the title bound is untouched: the dense middle lifts
    // toward white (light from within), the rim deepens (an under-layer). The lift
    // stays out of the title band.
    float inner = smoothstep(0.55, 1.0, cov) * tex * (1.0 - band);
    float rim = 4.0 * cov * (1.0 - cov);
    vec3 c = mix(uFieldCol[i], vec3(1.0), LIFT * inner * uTexture);
    c *= 1.0 - RIM * rim * uTexture;

    // chapelLayout.fieldAlpha: warmth fades out inside the title band.
    float breath = uFieldA[i].x;
    float warmth = uFieldA[i].y;
    float a = FIELD_ALPHA * (1.0 + ALPHA_BREATH * breath)
            * (1.0 + WARM_GAIN * warmth * (1.0 - band)) * density;
    col = mix(col, c, clamp(a * cov, 0.0, 1.0));
  }

  // Seam glows: a band across the union of the two fields, feathered at both ends
  // so a stagger never shows a hard end. Its centre follows the midpoint of the
  // two wavering edges, and brush streaks break it up like the fields' edges.
  float halfH = max(fv * ${f(GLOW_HALF_H)}, 3.0);
  for (int j = 0; j < ${FIELD_COUNT - 1}; j++) {
    vec4 s = uSeam[j];
    if (s.w <= 0.0) continue;
    // GL y-up: field j's lower edge sits at r.y - wv, field j+1's upper at r.w + wv.
    float y = s.x + 0.5 * (wvs[j + 1] - wvs[j]);
    float v = max(0.0, 1.0 - abs(p.y - y) / halfH);
    if (v <= 0.0) continue;
    float h = ramp(p.x - s.y, fh) * ramp(s.z - p.x, fh);
    float streak = valueNoise(p / H * STREAK_SCALE + 71.0);
    float tx = mix(1.0, mix(1.0 - SEAM_STREAK, 1.0, streak), uTexture);
    col = screen(col, uSeamCol[j], s.w * v * h * tx);
  }

  // The rare bloom: an ellipse along its seam, warm white at the core.
  if (uBloom.w > 0.0) {
    vec2 d = (p - uBloom.xy) / vec2(max(uBloom.z, 1.0), max(uBloomRy, 1.0));
    float rr = length(d);
    if (rr < 1.0) {
      vec3 c = rr < 0.45 ? mix(WARM, uBloomCol, rr / 0.45) : uBloomCol;
      float a = rr < 0.45 ? mix(1.0, 0.6, rr / 0.45) : mix(0.6, 0.0, (rr - 0.45) / 0.55);
      col = screen(col, c, uBloom.w * a);
    }
  }

  col = mix(col, uBg, TITLE_DIM * band);
  gl_FragColor = vec4(dither(clamp(col, 0.0, 1.0), uTime), 1.0);
}
`

export interface ChapelState {
  crossfade: PaletteCrossfade | null // built on the first frame, from its palette
  colors: Rgb[] // the crossfade's target
  lastPalette: readonly Rgb[] | null
  lastChanges: number | null
  proportions: ProportionTween
  offsets: ProportionTween // the same tween, over sideways offsets
  breaths: Breath[]
  seamWarmth: SeamWarmth
  fieldWarmth: FieldWarmth
  bloom: SeamBloom
  forceBloom: boolean
  texture: boolean
}

const mixRgb = (a: Rgb, b: Rgb, t: number): Rgb => ({
  r: a.r + (b.r - a.r) * t,
  g: a.g + (b.g - a.g) * t,
  b: a.b + (b.b - a.b) * t
})

// Scratch buffers, reused every frame.
const fieldBuf = new Float32Array(FIELD_COUNT * 4)
const fieldColBuf = new Float32Array(FIELD_COUNT * 3)
const fieldABuf = new Float32Array(FIELD_COUNT * 3)
const seamBuf = new Float32Array((FIELD_COUNT - 1) * 4)
const seamColBuf = new Float32Array((FIELD_COUNT - 1) * 3)

export const CHAPEL_SPEC: ShaderSpec<void, ChapelState> = {
  name: 'chapel',
  frag: FRAG,
  // Texture wants more pixels than a soft shader; measured in TUNING.md.
  resScale: 0.4,
  // Motion periods are 20-40 s; more frames buy nothing visible.
  fps: 15,
  createState: () => ({
    crossfade: null,
    colors: [],
    lastPalette: null,
    lastChanges: null,
    proportions: new ProportionTween(randomProportions(Math.random), RETARGET_MS),
    offsets: new ProportionTween(randomOffsets(Math.random), RETARGET_MS),
    breaths: makeBreaths(Math.random),
    seamWarmth: new SeamWarmth(FIELD_COUNT - 1),
    fieldWarmth: new FieldWarmth(FIELD_COUNT),
    bloom: new SeamBloom(Math.random),
    forceBloom: false,
    texture: true
  }),
  tune(opts, state) {
    if (opts.texture != null) state.texture = opts.texture === 'on'
  },
  forcePop(state) {
    state.forceBloom = true
  },
  setUniforms(gl, quad, _res, frame, state) {
    const W = gl.drawingBufferWidth
    const H = gl.drawingBufferHeight
    const t = frame.elapsed
    const live = !frame.still

    // Palette: Chapel's own fade, luminance-sorted per field. A still frame lands
    // it, so nothing freezes mid-blend (KAMP-779).
    if (frame.palette !== state.lastPalette) {
      state.lastPalette = frame.palette
      state.colors = fieldColors([...frame.palette])
      if (!state.crossfade) state.crossfade = new PaletteCrossfade(state.colors, CROSSFADE_MS)
      else state.crossfade.set(state.colors, live)
    } else if (!live && state.crossfade && !state.crossfade.settled) {
      state.crossfade.set(state.colors, false)
    }
    const fade = state.crossfade

    // Track change: re-proportion and re-stagger; eased while running, landed on a
    // still frame, nothing on mount or resume.
    const decision = retargetDecision(state.lastChanges, frame.trackChanges, frame.still)
    state.lastChanges = frame.trackChanges
    if (decision !== 'none') {
      const animate = decision === 'animate'
      state.proportions.set(randomProportions(Math.random), animate)
      state.offsets.set(randomOffsets(Math.random), animate)
    }

    if (live) {
      if (frame.resumed) {
        // Nothing ticked while stopped; a stale glow or bloom must not replay.
        state.seamWarmth.clear()
        state.fieldWarmth.clear()
        state.bloom.cancel(t)
      }
      fade?.tick(frame.dt * 1000)
      state.proportions.tick(frame.dt * 1000)
      state.offsets.tick(frame.dt * 1000)
    }

    const breaths = frame.atRest ? null : state.breaths
    const layout = layoutFields(
      W,
      H,
      breathedWeights(state.proportions.current(), breaths, t),
      state.offsets.current()
    )
    const { fields, seams, seamX, feather, featherX } = layout

    // The title band's top in DOM px (y-down), only while the art and title show.
    const title = frame.titleRect
    const titleTop = title ? title.y0 * H : null
    const bandOn = titleTop != null && frame.artShown > 0.5

    if (live && !frame.atRest) {
      // Anchor on the last real cursor position (KAMP-765); release once it leaves.
      const present = frame.pointerActive > 0.5
      const px = present ? ((frame.pointerAt.x + 1) / 2) * W : null
      const py = present ? ((frame.pointerAt.y + 1) / 2) * H : null
      state.seamWarmth.aim(py, seams, POINTER_REACH * H)
      state.fieldWarmth.aim(px, py, fields, bandOn ? titleTop : null)
      state.seamWarmth.tick(frame.dt)
      state.fieldWarmth.tick(frame.dt)
      if (state.forceBloom) {
        state.forceBloom = false
        state.bloom.tick(0, Math.max(t, state.bloom.nextAt), Infinity, seams.length)
      } else {
        state.bloom.tick(frame.dt, t, frame.pointerIdle, seams.length)
      }
    }

    const colors = fade?.current() ?? []
    const colorAt = (i: number): Rgb => colors[i % Math.max(1, colors.length)] ?? WARM
    const seamWarm = state.seamWarmth.values()
    const fieldWarm = state.fieldWarmth.values()
    const glows = !frame.atRest

    for (let i = 0; i < FIELD_COUNT; i++) {
      const r = fields[i]
      if (r) {
        // DOM y-down to GL y-up.
        fieldBuf.set([r.x, H - (r.y + r.h), r.x + r.w, H - r.y], i * 4)
      } else fieldBuf.set([0, 0, 0, 0], i * 4)
      const c = colorAt(i)
      fieldColBuf.set([c.r / 255, c.g / 255, c.b / 255], i * 3)
      const b = breaths?.[i] ? breathAt(breaths[i], t) : 0
      const off = r ? r.x - (W - r.w) / 2 : 0
      fieldABuf.set([b, glows ? (fieldWarm[i] ?? 0) : 0, off], i * 3)
    }
    for (let j = 0; j < FIELD_COUNT - 1; j++) {
      const c = mixRgb(mixRgb(colorAt(j), colorAt(j + 1), 0.5), WARM, 0.55)
      const span = seamX[j]
      const a = glows ? GLOW_ALPHA * (seamWarm[j] ?? 0) : 0
      seamBuf.set(span ? [H - seams[j], span.x0, span.x1, a] : [0, 0, 0, 0], j * 4)
      seamColBuf.set([c.r / 255, c.g / 255, c.b / 255], j * 3)
    }
    gl.uniform4fv(quad.u('uField[0]'), fieldBuf)
    gl.uniform3fv(quad.u('uFieldCol[0]'), fieldColBuf)
    gl.uniform3fv(quad.u('uFieldA[0]'), fieldABuf)
    gl.uniform2f(quad.u('uFeather'), feather, featherX)
    gl.uniform4fv(quad.u('uSeam[0]'), seamBuf)
    gl.uniform3fv(quad.u('uSeamCol[0]'), seamColBuf)

    // The bloom, across its seam's whole span.
    const bloom = state.bloom.active
    const span = bloom ? seamX[bloom.seam] : undefined
    if (glows && bloom && span) {
      const width = span.x1 - span.x0
      const c = mixRgb(mixRgb(colorAt(bloom.seam), colorAt(bloom.seam + 1), 0.5), WARM, 0.55)
      gl.uniform4f(
        quad.u('uBloom'),
        span.x0 + bloom.x * width,
        H - seams[bloom.seam],
        Math.max(1, state.bloom.spread() * width * 0.75),
        BLOOM_ALPHA * state.bloom.envelope()
      )
      gl.uniform1f(quad.u('uBloomRy'), feather * GLOW_HALF_H * 1.3)
      gl.uniform3f(quad.u('uBloomCol'), c.r / 255, c.g / 255, c.b / 255)
    } else {
      gl.uniform4f(quad.u('uBloom'), 0, 0, 0, 0)
    }

    // The band's strength follows artShown; its position never moves with it.
    gl.uniform3f(
      quad.u('uBand'),
      titleTop != null ? H - titleTop : -1,
      TITLE_FEATHER * H,
      titleTop != null ? frame.artShown : 0
    )
    gl.uniform1f(quad.u('uTexture'), state.texture ? 1 : 0)
    setArtUniform(gl, quad, frame)
  }
}
