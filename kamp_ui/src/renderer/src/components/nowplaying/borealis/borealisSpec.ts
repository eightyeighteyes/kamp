// KAMP-765: "Borealis", a Now Playing background of slow aurora curtains in album
// colours, built on the shared WebGL base (KAMP-780).
//
// Motion type is "flow". Three curtains, each a wavy bright lower edge with soft
// rays fading upward. The rays are striated in x only, so they read as hanging
// light rather than texture.
//   * Flow: every noise lookup is offset along two circles in noise space whose
//     angles come from the CPU (shared/borealisTiming.ts flowPhase). The motion is
//     bounded, the wrap is seamless, and float32 never sees a large coordinate.
//   * Cursor: the sampling x is pushed away from the lean anchor, with a slow
//     outward ripple. The anchor is the last real cursor position (pointerAt), so
//     a fading lean stays put instead of sliding toward the centre.
//   * Track change: up to two brightness bands roll across the curtains.
//   * Rare: one curtain's lower edge brightens locally and settles.
// Every layer is a mix() toward a target, never an add, so a light theme doesn't
// clip. The title mask is applied to the composited aurora, so it bounds every
// moment at once.

import {
  CURTAINS,
  FlickerEvents,
  LeanFollower,
  SurgeEnvelope,
  flowPhase
} from '../../../../../shared/borealisTiming'
import { GLSL_DITHER, GLSL_HEADER, GLSL_NOISE } from '../webgl/glsl'
import type { ShaderSpec } from '../webgl/shaderEngine'

// --- tunables (see TUNING.md) ----------------------------------------------------
const STILL_PHASE: [number, number] = [1.9, 4.4] // the reduced-motion still's curtains
const RIPPLE_S = 2.6 // one lean ripple per this many seconds
const TAU = Math.PI * 2

const FRAG = `${GLSL_HEADER}${GLSL_NOISE}${GLSL_DITHER}
uniform vec4 uFlow;     // cos/sin of the two flow angles
uniform vec4 uLean;     // amount 0..1, anchor x, anchor y (-1..1, DOM y-down), ripple phase
uniform vec3 uSurgeA;   // position 0..1, value 0..1, dir (0 L->R, 1 R->L)
uniform vec3 uSurgeB;
uniform vec4 uFlicker;  // value 0..1, curtain index, x 0..1, unused
uniform float uArtShown;
uniform vec4 uTitle;    // title text box in uv (y up); x1 <= x0 means none

const int CURTAINS = ${CURTAINS};
const float ALPHA = 0.45;      // peak curtain opacity; leaves headroom for the surge
const float SURGE = 0.55;      // surge brightness gain
const float FLICKER = 0.6;     // edge flicker brightness gain
const float LEAN = 0.06;       // peak lean, pane heights
const float LEAN_R = 0.32;     // lean falloff radius, pane heights
const float EDGE_W = 0.018;    // lower-edge glow thickness
const float RAY_H = 0.30;      // ray fade height above the edge
const float WAVE = 0.075;      // lower-edge wave amplitude
const float FLOW_R = 1.6;      // flow circle radius, noise units

float lum(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

// 1 behind the title text (soft-edged), 0 elsewhere.
float titleMask(vec2 uv) {
  if (uTitle.z <= uTitle.x) return 0.0;
  float m = 0.03;
  vec2 a = smoothstep(uTitle.xy - m, uTitle.xy, uv)
         * (1.0 - smoothstep(uTitle.zw, uTitle.zw + m, uv));
  return a.x * a.y;
}

float surgeBand(vec3 s, float x) {
  if (s.y <= 0.0) return 0.0;
  float bx = s.z < 0.5 ? mix(-0.25, 1.25, s.x) : mix(1.25, -0.25, s.x);
  float d = (x - bx) / 0.18;
  return s.y * exp(-d * d);
}

void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  float aspect = uRes.x / uRes.y;
  vec2 q = vec2(uv.x * aspect, uv.y);   // pane-height units
  float shown = uArtShown;
  float hidden = 1.0 - shown;

  // Cursor lean: sample from nearer the cursor, so the curtains move away from it.
  // A smooth sign (not sign()) keeps the column under the cursor from tearing.
  vec2 anchor = vec2((uLean.y * 0.5 + 0.5) * aspect, 0.5 - uLean.z * 0.5);
  vec2 d = q - anchor;
  float dist = length(d);
  float fall = exp(-(dist * dist) / (LEAN_R * LEAN_R));
  float ripple = 1.0 + 0.35 * sin(dist * 18.0 - uLean.w);
  float push = LEAN * uLean.x * fall * ripple * (d.x / (abs(d.x) + 0.05));
  float X = q.x - push;

  vec2 c0 = uFlow.xy * FLOW_R;
  vec2 c1 = uFlow.zw * FLOW_R;
  float surge = surgeBand(uSurgeA, uv.x) + surgeBand(uSurgeB, uv.x);

  // Light theme: a deeper tone at lower opacity reads as coloured light; a raised
  // tone on a light background would read as grey haze.
  float light = smoothstep(0.35, 0.65, lum(uBg));

  vec3 col = uBg;
  // A faint wash of the deepest palette colour toward the top: night sky, not flat.
  col = mix(col, uPalette[4] * mix(0.45, 1.0, light), 0.06 * uv.y * (1.0 - 0.5 * light));

  for (int i = 0; i < CURTAINS; i++) {
    float fi = float(i);
    // Higher with the art shown, so the curtains sit above the title and the side
    // strips carry them; lower and taller when they have the pane to themselves.
    float base = mix(0.50 + 0.10 * fi, 0.24 + 0.15 * fi, hidden);
    float rayH = RAY_H * mix(1.0, 1.5, hidden) * (0.8 + 0.2 * fi);

    float e1 = valueNoise(vec2(X * 1.3 + fi * 7.1, fi * 2.3) + c0) - 0.5;
    float e2 = valueNoise(vec2(X * 3.1 + fi * 3.7, fi * 5.9) + c1 * 1.3) - 0.5;
    float edge = base + WAVE * 2.0 * e1 + 0.35 * WAVE * 2.0 * e2;
    float h = uv.y - edge;

    // Lower edge: a thin bright glow; above it, rays fading upward.
    float ew = max(EDGE_W, 2.0 / uRes.y); // never sub-pixel, or it stairs too
    float below = h < 0.0 ? h / ew : 0.0;
    float glow = exp(-below * below) * (h < 0.0 ? 1.0 : exp(-h / (ew * 2.5)));
    // The rays fade in across the edge rather than switching on at h = 0: a hard
    // step there is a one-pixel stair that the ¼-res upscale turns into visible
    // blocks. The ramp is never narrower than a few backing pixels.
    float ramp = max(EDGE_W, 3.0 / uRes.y);
    float rays = smoothstep(-ramp, ramp, h) * exp(-max(h, 0.0) / rayH);
    float stri = 0.45 + 0.35 * valueNoise(vec2(X * 6.0 + fi * 11.0, 0.0) + c1)
                      + 0.20 * valueNoise(vec2(X * 14.0 + fi * 5.0, 3.0) + c0 * 1.7);
    // Curtains come and go along their length, never the full width at once.
    float span = 0.35 + 0.65 * smoothstep(0.25, 0.65,
                   valueNoise(vec2(X * 0.9 + fi * 13.0, 0.5) + c0 * 0.7));

    float inten = (0.75 * glow + 0.6 * rays * stri) * span;

    // Rare flicker: this curtain's lower edge, locally.
    float fd = (uv.x - uFlicker.z) / 0.07;
    float isMine = 1.0 - step(0.5, abs(uFlicker.y - fi));
    inten += FLICKER * uFlicker.x * isMine * exp(-fd * fd) * glow;

    inten *= 1.0 + SURGE * surge;

    // Colour runs from this curtain's palette entry at the edge to the next one at
    // the ray tips; the edge is lifted toward white on dark themes, like a real
    // aurora's bright fringe.
    vec3 hue = mix(uPalette[i], uPalette[i + 1], smoothstep(0.0, rayH, h));
    vec3 darkTint = mix(hue, vec3(1.0), 0.12 + 0.30 * glow + 0.15 * surge);
    vec3 lightTint = hue * 0.72;
    vec3 tint = mix(darkTint, lightTint, light);
    float a = clamp(inten * ALPHA * mix(1.0, 0.75, light), 0.0, 0.85);
    col = mix(col, tint, a);
  }

  // Keep the title legible: pull everything above back toward the background
  // behind the title text, after every moment is composited.
  float quiet = titleMask(uv) * shown;
  col = mix(col, uBg, 0.8 * quiet);

  gl_FragColor = vec4(dither(clamp(col, 0.0, 1.0), uTime), 1.0);
}
`

export interface BorealisState {
  surge: SurgeEnvelope
  lean: LeanFollower
  flicker: FlickerEvents
  lastTrackSeq: number
}

export const BOREALIS_SPEC: ShaderSpec<void, BorealisState> = {
  name: 'borealis',
  frag: FRAG,
  // Soft curtains survive the ¼-resolution upscale (KAMP-776's starting point).
  resScale: 0.25,
  fps: 30,
  createState: () => ({
    surge: new SurgeEnvelope(),
    lean: new LeanFollower(),
    flicker: new FlickerEvents(Math.random),
    lastTrackSeq: 0
  }),
  setUniforms(gl, quad, _res, frame, state) {
    const { surge, lean, flicker } = state
    const t = frame.elapsed

    if (frame.atRest) {
      // The designed still: a fixed curtain shape, no moments.
      const [p0, p1] = STILL_PHASE
      gl.uniform4f(quad.u('uFlow'), Math.cos(p0), Math.sin(p0), Math.cos(p1), Math.sin(p1))
      gl.uniform4f(quad.u('uLean'), 0, 0, 0, 0)
      gl.uniform3f(quad.u('uSurgeA'), 0, 0, 0)
      gl.uniform3f(quad.u('uSurgeB'), 0, 0, 0)
      gl.uniform4f(quad.u('uFlicker'), 0, 0, 0, 0)
    } else {
      if (!frame.still) {
        if (frame.resumed) {
          // Nothing ticked while stopped; a half-finished moment must not replay.
          surge.cancel()
          lean.cancel()
          flicker.cancel(t)
          state.lastTrackSeq = frame.trackSeq
        }
        if (frame.trackSeq !== state.lastTrackSeq) {
          state.lastTrackSeq = frame.trackSeq
          surge.trigger(Math.random() < 0.5 ? 0 : 1)
        }
        surge.tick(frame.dt)
        lean.tick(frame.dt, frame.pointerActive, frame.pointerIdle, frame.pointerAt)
        flicker.tick(frame.dt, t, frame.pointerIdle, surge.active, frame.artShown)
      }
      const [p0, p1] = flowPhase(t)
      gl.uniform4f(quad.u('uFlow'), Math.cos(p0), Math.sin(p0), Math.cos(p1), Math.sin(p1))
      // Wrapped in float64 so the shader's sin() only ever sees 0..2π.
      const ripple = (TAU * (t % RIPPLE_S)) / RIPPLE_S
      gl.uniform4f(quad.u('uLean'), lean.amount, lean.x, lean.y, ripple)
      const [a, b] = surge.bands
      gl.uniform3f(quad.u('uSurgeA'), a.position, a.value, a.dir)
      gl.uniform3f(quad.u('uSurgeB'), b.position, b.value, b.dir)
      const f = flicker.active
      gl.uniform4f(quad.u('uFlicker'), flicker.value(), f?.curtain ?? 0, f?.x ?? 0, 0)
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
