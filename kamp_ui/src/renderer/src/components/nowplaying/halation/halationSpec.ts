// KAMP-774: "Halation", a Now Playing background of film grain, projector dust
// and warm light leaks, built on the shared WebGL base (KAMP-780).
//
// Motion type is "twinkle and drift". Nothing here may strobe, so:
//   * grain is per-pixel hash noise locked to the backing-store grid (gate weave
//     never moves it, or sub-pixel weave would shimmer it every frame). Its seed
//     changes every 3-4 s with a 0.5 s variance-normalised crossfade
//     (shared/halationTiming.ts, mirrored in grain() below);
//   * dust glints live in a sparse hashed-cell field (one cell per pixel, no
//     loops) on slow sin^6 envelopes, and glow brighter near the cursor. The
//     field drifts slowly upward and each mote meanders inside its cell; motes
//     are smooth Gaussians at least 2 px wide, so sub-pixel motion can't crawl;
//   * light leaks, the track-change burn and the hair are smooth gradients whose
//     intensities come from CPU envelopes with bounded slopes.
// Every colour layer is a mix() toward a target, not an add, so a light theme
// doesn't clip.
//
// Time: float32 uTime loses precision over hours. Leaks, weave and every
// envelope are computed on the CPU in float64; the only shader-side clock,
// uGlintT, wraps at GLINT_PERIOD and every glint frequency is an integer
// multiple of 1/GLINT_PERIOD, so the wrap is seamless.

import { BURN_EDGES, BurnEnvelope, RareEvents, seedAt } from '../../../../../shared/halationTiming'
import { GLSL_DITHER, GLSL_HEADER, GLSL_NOISE } from '../webgl/glsl'
import type { ShaderSpec } from '../webgl/shaderEngine'

// --- tunables (see TUNING.md) ----------------------------------------------------
const GLINT_PERIOD_S = 600
const STILL_SEED = 13 // the reduced-motion still's grain
const STILL_GLINT_T = 37 // a glint phase with a few motes lit
const STILL_LEAK = 0.7
const WEAVE = 0.0011 // gate weave amplitude, fraction of pane height
const JOLT = 0.006 // peak gate-jolt offset, fraction of pane height
const LEAK_PERIODS_S = [23, 31, 37] // incommensurate, so the leaks never pulse together
const CELL = 0.055 // dust cell, pane-height units (mirrored in the shader)
const DUST_TILE = 64 // the dust field repeats every 64 cells, so its drift can wrap
// Dust drift, pane heights per second: a slow rise (warm air over the projector)
// with a lazy sideways sway. About 2 minutes to cross the pane.
const DUST_RISE = 0.008
const DUST_SLIDE = 0.003
const DUST_SWAY = 0.025 // sideways sway amplitude, pane heights
const DUST_SWAY_S = 41
const TAU = Math.PI * 2

const FRAG = `${GLSL_HEADER}${GLSL_NOISE}${GLSL_DITHER}
uniform vec3 uGrain;    // seedA, seedB, mix
uniform vec2 uWeave;    // gate weave, pane-height units, y up
uniform vec3 uLeak;     // per-leak intensity, 0..1
uniform vec2 uBurn;     // envelope 0..1, edge (0 left, 1 right, 2 top)
uniform vec4 uHair;     // progress 0..1, alpha 0..1, seed, unused
uniform float uGlintT;  // seconds, wrapped at GLINT_PERIOD
uniform vec2 uDust;     // dust field drift, pane-height units, y up, wrapped
uniform float uArtShown;
uniform vec4 uTitle;    // title text box in uv (y up); x1 <= x0 means none

const float GLINT_PERIOD = ${GLINT_PERIOD_S.toFixed(1)};
const float GRAIN = 0.06;        // grain amplitude
const float LEAK = 0.42;         // peak leak opacity
const float BURN = 0.6;          // peak burn opacity
const float CELL = ${CELL.toFixed(3)};        // dust cell, pane-height units
const float DUST_DENSITY = 0.09; // fraction of cells holding a mote
const float DUST_TILE = ${DUST_TILE.toFixed(1)}; // dust field period, cells
// Per-mote meander, cell units. With motes placed in 0.4..0.6 of the cell and a
// 0.3-cell cutoff radius, 0.1 is the most that never clips at the cell edge.
const float MEANDER = 0.1;
const vec3 AMBER = vec3(1.0, 0.56, 0.20);
const vec3 WARM_WHITE = vec3(1.0, 0.92, 0.80);

// Sine-free hash: stays well-distributed at pixel coordinates in the thousands,
// where hash1's sin(large) degrades and stripes on some GPUs.
float hashNS(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

float lum(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

// Mirrors grainMix() in shared/halationTiming.ts: constant contrast mid-fade.
float grain(vec2 px) {
  float a = hashNS(px + uGrain.x * vec2(37.0, 17.0));
  float b = hashNS(px + uGrain.y * vec2(37.0, 17.0));
  float m = uGrain.z;
  float wa = 1.0 - m;
  return (a * wa + b * m - 0.5) / sqrt(wa * wa + m * m) + 0.5;
}

// 1 behind the title text (soft-edged), 0 elsewhere. Only the title: the art is
// opaque, and the side strips beside it are where this background has to read.
float titleMask(vec2 uv) {
  if (uTitle.z <= uTitle.x) return 0.0;
  float m = 0.03;
  vec2 a = smoothstep(uTitle.xy - m, uTitle.xy, uv)
         * (1.0 - smoothstep(uTitle.zw, uTitle.zw + m, uv));
  return a.x * a.y;
}

void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  float aspect = uRes.x / uRes.y;
  vec2 q = vec2(uv.x * aspect, uv.y);   // pane-height units
  vec2 qw = q + uWeave;                 // the gate weaves the light, not the grain
  vec2 uvw = vec2(qw.x / aspect, qw.y);
  float hidden = 1.0 - uArtShown;
  float quiet = titleMask(uv) * uArtShown;

  vec3 col = uBg;

  // Art hidden: a softly lit projection screen.
  vec2 sd = abs(uvw - 0.5);
  float screen = (1.0 - smoothstep(0.36, 0.45, sd.x)) * (1.0 - smoothstep(0.33, 0.43, sd.y));
  col = mix(col, mix(WARM_WHITE, uPalette[0], 0.25), screen * hidden * 0.10);
  float vig = smoothstep(0.45, 0.95, length(uv - 0.5) * 1.414);
  col *= 1.0 - vig * (0.10 + 0.20 * hidden);

  // Light leaks breathing in from the edges, in album colours warmed toward amber.
  float quietL = 1.0 - 0.8 * quiet;
  // (GLSL pow() is undefined for a negative base, so square by hand.)
  float y0 = (uvw.y - 0.62) / 0.30;
  float y1 = (uvw.y - 0.35) / 0.34;
  float a0 = (1.0 - smoothstep(0.0, 0.30, uvw.x)) * exp(-y0 * y0);
  float a1 = (1.0 - smoothstep(0.0, 0.26, 1.0 - uvw.x)) * exp(-y1 * y1);
  float a2 = 1.0 - smoothstep(0.0, 0.55, length((1.0 - uvw) * vec2(aspect, 1.0)));
  col = mix(col, mix(uPalette[0], AMBER, 0.35), a0 * a0 * uLeak.x * LEAK * quietL);
  col = mix(col, mix(uPalette[1], AMBER, 0.35), a1 * a1 * uLeak.y * LEAK * quietL);
  col = mix(col, mix(uPalette[2], AMBER, 0.45), a2 * a2 * uLeak.z * LEAK * quietL);

  // Track-change burn: amber spreading in from one edge as the envelope rises.
  if (uBurn.x > 0.0) {
    float d = uBurn.y < 0.5 ? uvw.x * aspect : (uBurn.y < 1.5 ? (1.0 - uvw.x) * aspect : 1.0 - uvw.y);
    float reach = 0.10 + 0.45 * uBurn.x;
    vec3 bc = mix(mix(AMBER, uPalette[0], 0.2), WARM_WHITE, exp(-d / (reach * 0.35)) * 0.5);
    col = mix(col, bc, uBurn.x * exp(-d / reach) * BURN);
  }

  // Grain, pixel-locked. Scaled with the local light so it reads on light themes too.
  float g = grain(floor(gl_FragCoord.xy)) - 0.5;
  col += g * GRAIN * (1.0 - 0.5 * quiet) * (0.6 + 0.8 * lum(col));

  // Dust glints: one mote at most per cell, kept clear of the cell edge so the
  // single-cell lookup never clips it. The whole field drifts (uDust), and each
  // mote meanders a little inside its cell, like dust in a quiet theatre's beam.
  // The field tiles every DUST_TILE cells, so uDust wraps at DUST_TILE * CELL
  // with no visible jump.
  vec2 cq = (qw - uDust) / CELL;
  vec2 cell = floor(cq);
  vec2 ch = mod(cell, DUST_TILE);
  float h = hashNS(ch + 7.31);
  if (h > 1.0 - DUST_DENSITY) {
    // Integer kw: the meander's period divides GLINT_PERIOD, like the envelope's.
    float kw = 40.0 + floor(hashNS(ch + 61.9) * 35.0);
    float wt = 6.2831853 * uGlintT / GLINT_PERIOD;
    vec2 wander = MEANDER * vec2(sin(kw * wt + h * 40.0), sin((kw - 13.0) * wt + h * 97.0));
    vec2 pos = 0.4 + 0.2 * vec2(hashNS(ch + 19.7), hashNS(ch + 41.3)) + wander;
    float dist = length(cq - cell - pos) * CELL;
    float rad = max(0.0035, 2.0 / uRes.y); // never sub-pixel, or it would crawl
    float spot = exp(-(dist * dist) / (rad * rad)) + 0.25 * exp(-dist / (rad * 2.0));
    spot *= 1.0 - smoothstep(0.7 * 0.3 * CELL, 0.3 * CELL, dist);
    // Integer k: the envelope's period divides GLINT_PERIOD, so the clock wrap is seamless.
    float k = 40.0 + floor(hashNS(ch + 3.7) * 50.0);
    float s = sin(3.14159265 * k * uGlintT / GLINT_PERIOD + h * 6.2831853);
    float env = pow(s * s, 3.0);
    vec2 pq = vec2((uPointer.x * 0.5 + 0.5) * aspect, 0.5 - uPointer.y * 0.5);
    float near = uPointerActive * (1.0 - smoothstep(0.0, 0.28, length(qw - pq)));
    float glint = spot * (env * 0.55 + near * (0.3 + 0.7 * env));
    col = mix(col, mix(WARM_WHITE, uPalette[1], 0.2), clamp(glint * (1.0 - 0.85 * quiet), 0.0, 0.9));
  }

  // A hair caught in the gate, drifting across. At least 1.5 backing px wide so it
  // doesn't crawl; light on dark, dark on light, so it reads on every theme.
  if (uHair.y > 0.001) {
    float seed = uHair.z;
    float x = qw.x - mix(-0.45, aspect + 0.05, uHair.x);
    float len = 0.25 + 0.15 * fract(seed * 0.37);
    float amp = 0.03 + 0.03 * fract(seed * 0.13);
    float f = 8.0 + 6.0 * fract(seed * 0.53);
    float y = 0.25 + 0.5 * fract(seed * 0.71) + amp * sin(x * f + seed);
    float slope = amp * f * cos(x * f + seed);
    float d = abs(qw.y - y) / sqrt(1.0 + slope * slope);
    float w = 1.5 / uRes.y;
    float along = smoothstep(0.0, 0.03, x) * (1.0 - smoothstep(len - 0.03, len, x));
    float hair = (1.0 - smoothstep(w * 0.5, w * 1.5, d)) * along * uHair.y;
    vec3 ink = mix(col + vec3(0.10, 0.085, 0.07), col * 0.35, smoothstep(0.25, 0.5, lum(col)));
    col = mix(col, ink, hair * 0.8);
  }

  gl_FragColor = vec4(dither(clamp(col, 0.0, 1.0), uTime), 1.0);
}
`

export interface HalationState {
  burn: BurnEnvelope
  events: RareEvents
  lastTrackSeq: number
}

export const HALATION_SPEC: ShaderSpec<void, HalationState> = {
  name: 'halation',
  frag: FRAG,
  // Grain needs more resolution than a soft background (at 0.25 it upscales into
  // blocks). Per-pixel cost is a few hashes, so the budget is fit with the frame
  // cap: between re-seeds only the glints and leaks move.
  resScale: 0.5,
  fps: 20,
  createState: () => ({
    burn: new BurnEnvelope(),
    events: new RareEvents(Math.random),
    lastTrackSeq: 0
  }),
  setUniforms(gl, quad, _res, frame, state) {
    const { burn, events } = state
    const t = frame.elapsed

    if (frame.atRest) {
      // The designed still: fixed grain, a few motes lit, no moments.
      gl.uniform3f(quad.u('uGrain'), STILL_SEED, STILL_SEED, 0)
      gl.uniform2f(quad.u('uWeave'), 0, 0)
      gl.uniform3f(quad.u('uLeak'), STILL_LEAK, STILL_LEAK, STILL_LEAK)
      gl.uniform2f(quad.u('uBurn'), 0, 0)
      gl.uniform4f(quad.u('uHair'), 0, 0, 0, 0)
      gl.uniform1f(quad.u('uGlintT'), STILL_GLINT_T)
      gl.uniform2f(quad.u('uDust'), 0, 0)
    } else {
      if (!frame.still) {
        if (frame.resumed) {
          // Nothing ticked while stopped; a half-finished moment must not replay.
          burn.cancel()
          events.cancel(t)
          state.lastTrackSeq = frame.trackSeq
        }
        if (frame.trackSeq !== state.lastTrackSeq) {
          state.lastTrackSeq = frame.trackSeq
          burn.trigger(Math.floor(Math.random() * BURN_EDGES))
        }
        burn.tick(frame.dt)
        events.tick(frame.dt, t, frame.pointerIdle, burn.active)
      }
      const seeds = seedAt(t)
      gl.uniform3f(quad.u('uGrain'), seeds.seedA, seeds.seedB, seeds.mix)
      const wx = WEAVE * (0.6 * Math.sin((TAU * t) / 7.3) + 0.4 * Math.sin((TAU * t) / 3.1))
      const wy =
        WEAVE * (0.6 * Math.sin((TAU * t) / 5.9 + 1.3) + 0.4 * Math.sin((TAU * t) / 2.7)) +
        JOLT * events.joltOffset()
      gl.uniform2f(quad.u('uWeave'), wx, wy)
      const leak = (i: number): number =>
        0.55 + 0.45 * Math.sin((TAU * t) / LEAK_PERIODS_S[i] + i * 2.1)
      gl.uniform3f(quad.u('uLeak'), leak(0), leak(1), leak(2))
      gl.uniform2f(quad.u('uBurn'), burn.value, burn.edge)
      const hair = events.hairProgress()
      gl.uniform4f(
        quad.u('uHair'),
        Math.max(0, hair),
        events.hairAlpha(),
        events.active?.seed ?? 0,
        0
      )
      gl.uniform1f(quad.u('uGlintT'), t % GLINT_PERIOD_S)
      // Computed in float64 and wrapped at the field's period, so the shader only
      // ever sees small offsets and the wrap lands on an identical field.
      const span = DUST_TILE * CELL
      const wrap = (v: number): number => ((v % span) + span) % span
      const dx = DUST_SLIDE * t + DUST_SWAY * Math.sin((TAU * t) / DUST_SWAY_S)
      gl.uniform2f(quad.u('uDust'), wrap(dx), wrap(DUST_RISE * t))
    }

    gl.uniform1f(quad.u('uArtShown'), frame.artShown)
    const c = frame.titleRect
    if (c && frame.artShown > 0) {
      // DOM y-down to GL y-up.
      gl.uniform4f(quad.u('uTitle'), c.x0, 1 - c.y1, c.x1, 1 - c.y0)
    } else {
      gl.uniform4f(quad.u('uTitle'), 0, 0, 0, 0)
    }
  }
}
