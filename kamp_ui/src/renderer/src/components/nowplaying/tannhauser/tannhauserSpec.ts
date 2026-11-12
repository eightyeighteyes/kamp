// KAMP-772: "Tannhäuser", a Now Playing background of condensation on a
// rainy-night window, built on the shared WebGL base (KAMP-780). The file name
// avoids the umlaut; the picker label keeps it.
//
// Motion type is "condense and bead": drops form, swell and merge in place.
//   * Behind the glass: five soft lights in album colours, drifting slowly. The
//     field is evaluated ONCE per pixel, accumulating a sharp and a diffuse
//     falloff in the same loop: fogged glass shows the diffuse one, clear glass
//     the sharp one, and a drop's refraction just moves the sample point. (A
//     separate refraction pass would have been a third evaluation, KAMP-772.)
//   * Fog density comes from a CPU-side byte mask (shared/tannhauserTiming.ts
//     FogMask) uploaded as a LUMINANCE texture. The cursor wipes it; it refills
//     linearly. It is the first texture any background owns; see the README.
//   * Drops are two hashed-cell fields, one cell lookup per field, sized by a
//     life clock wrapped in float64 on the CPU. A fine drop beside a swelling
//     coarse one fades into it, which reads as merging. Drops exist only on fog.
//   * Track change: fresh fog breathes outward from the centre (BreathEnvelope).
//   * Rare: a heavy drop slides a short way; its trail is wiped into the mask,
//     so it refogs on its own (SlideEvents).
// Every layer is a mix() toward a target, so a light theme doesn't clip.

import {
  BreathEnvelope,
  FogMask,
  MASK_H,
  SlideEvents,
  StrokeTracker,
  dropPhase,
  maskWidth
} from '../../../../../shared/tannhauserTiming'
import type { GlQuad } from '../webgl/glQuad'
import { GLSL_DITHER, GLSL_HEADER, GLSL_NOISE } from '../webgl/glsl'
import type { ShaderSpec } from '../webgl/shaderEngine'
import { GLSL_TITLE_MASK, setTitleUniform } from '../webgl/titleMask'

// --- tunables (see TUNING.md) ----------------------------------------------------
const WIPE_R = 0.075 // cursor wipe radius, pane heights
const TRAIL_R = 0.7 // slide trail radius, fraction of the head's radius
const DRIFT_PERIODS_S: [number, number] = [131, 197] // the lights' slow drift
const STILL_DROPS: [number, number] = [0.37, 0.61] // the reduced-motion still's drops
const STILL_DRIFT: [number, number] = [1.3, 4.1]
const TAU = Math.PI * 2

const FRAG = `${GLSL_HEADER}${GLSL_NOISE}${GLSL_DITHER}${GLSL_TITLE_MASK}
uniform sampler2D uFog;  // fog mask, row 0 = top of the pane; 1 = fogged
uniform float uFogOn;    // 1 = use the mask; 0 = the designed still (all fogged)
uniform vec2 uBreath;    // front progress 0..1, strength 0..1
uniform vec2 uDrops;     // fine and coarse drop phases, 0..1
uniform vec4 uSlide;     // head x, y (0..1, DOM y-down), radius (pane heights), alpha
uniform vec2 uDrift;     // light drift angles, 0..2pi
uniform float uArtShown;

const float FINE_CELL = 0.034;     // pane heights
const float COARSE_CELL = 0.085;
const float FINE_DENSITY = 0.6;    // fraction of cells holding a drop
const float COARSE_DENSITY = 0.3;
const float FINE_R = 0.34;         // max radius, fraction of the cell
const float COARSE_R = 0.3;
const float REFRACT = 2.2;         // drop lens offset, radii
const float BREATH_W = 0.18;       // soft width of the breath front, pane heights
const float MIST = 0.2;            // mist veil over the backdrop
const float GLOW_FOG = 0.55;       // light scattered through fog
const float GLOW_CLEAR = 0.75;     // light seen through clear glass
const float FRESH = 0.12;          // extra veil while a breath is fresh
const float TITLE_QUIET = 0.75;

float lum(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

// The drop in q's cell: xy = offset from its centre in radii, z = radius (pane
// heights), w = coverage 0..1. The jitter is bounded so a drop never crosses
// its cell, which is what lets one lookup per field suffice.
vec4 dropIn(vec2 q, float cell, float density, float maxR, float phase, float seed) {
  vec2 id = floor(q / cell);
  vec2 h = hash2(id + seed);
  float exists = step(hash1(id + seed * 1.37), density);
  float life = fract(phase + h.x * 0.73 + h.y * 0.29);
  // Forms and swells over most of its life, then is drawn away quickly. Zero at
  // both ends, so the life wrap never shows.
  float grow = smoothstep(0.0, 0.6, life) * (1.0 - smoothstep(0.88, 1.0, life));
  float r = cell * maxR * (0.55 + 0.45 * h.y) * grow;
  vec2 c = (id + 0.5 + (h - 0.5) * (1.0 - 2.0 * maxR - 0.1)) * cell;
  vec2 d = q - c;
  float aa = 1.5 / uRes.y;           // edge width floored in backing pixels
  float cov = exists * (1.0 - smoothstep(r - aa, r, length(d)));
  cov *= smoothstep(0.0, 2.0 * aa, r); // a forming drop fades in, never a 1-px pop
  return vec4(d / max(r, 1e-4), r, cov);
}

void main() {
  vec2 uv = gl_FragCoord.xy / uRes;
  float aspect = uRes.x / uRes.y;
  vec2 q = vec2(uv.x * aspect, uv.y);   // pane-height units
  float shown = uArtShown;
  float light = smoothstep(0.35, 0.65, lum(uBg));
  float aa = 1.5 / uRes.y;

  // Fog density: the wipe mask, then the breath front, which only ever adds fog.
  float dens = mix(1.0, texture2D(uFog, vec2(uv.x, 1.0 - uv.y)).r, uFogOn);
  float maxD = length(vec2(aspect, 1.0) * 0.5);
  float front = uBreath.x * (maxD + BREATH_W);
  float cover = (1.0 - smoothstep(front - BREATH_W, front, length(q - vec2(aspect * 0.5, 0.5))))
              * uBreath.y;
  dens = max(dens, cover);

  // Drops, strongest wins. They bead only on fog: a wiped patch is clear glass
  // until it mists over again.
  float onFog = smoothstep(0.3, 0.8, dens);
  vec4 fine = dropIn(q, FINE_CELL, FINE_DENSITY, FINE_R, uDrops.x, 3.1);
  vec4 coarse = dropIn(q, COARSE_CELL, COARSE_DENSITY, COARSE_R, uDrops.y, 7.7);
  float swell = coarse.z / (COARSE_CELL * COARSE_R);
  fine.w *= 1.0 - (1.0 - smoothstep(1.0, 2.2, length(coarse.xy))) * swell;
  fine.w *= onFog;
  coarse.w *= onFog;
  vec4 drop = coarse.w > fine.w ? coarse : fine;

  // The rare sliding drop: heavier, a little taller than wide, never gated on fog
  // (its own trail has just cleared the glass behind it).
  vec2 sc = vec2(uSlide.x * aspect, 1.0 - uSlide.y);
  float sr = max(uSlide.z, 1e-4);
  vec2 sp = (q - sc) / sr;
  sp.y *= 0.85;
  float scov = (1.0 - smoothstep(1.0 - aa / sr, 1.0, length(sp))) * uSlide.w;
  if (scov > drop.w) drop = vec4(sp, sr, scov);

  vec2 p = drop.xy;
  float pl = length(p);
  float cov = drop.w;

  // One evaluation of the lights behind the glass, sampled through the drop.
  vec2 lq = q - p * drop.z * REFRACT * cov;
  vec3 sumS = vec3(0.0);
  vec3 sumD = vec3(0.0);
  float wS = 0.0;
  float wD = 0.0;
  for (int i = 0; i < 5; i++) {
    float fi = float(i);
    float bx = fract(0.17 + fi * 0.381966);   // golden-ratio spread across the width
    float by = fract(0.62 + fi * 0.618034);
    // With the art shown, the lights gather in the side strips beside it.
    float strip = bx < 0.5 ? mix(0.04, 0.2, bx * 2.0) : mix(0.8, 0.96, bx * 2.0 - 1.0);
    vec2 c = vec2(mix(bx, strip, shown) * aspect, by * 0.8 + 0.1)
           + 0.07 * vec2(sin(uDrift.x + fi * 1.9), cos(uDrift.y + fi * 2.7));
    float rs = 0.07 + 0.035 * fract(fi * 0.7548);
    float rd = rs * 2.6;
    vec2 d = lq - c;
    float dd = dot(d, d);
    float ws = exp(-dd / (rs * rs));
    float wd = exp(-dd / (rd * rd));
    sumS += uPalette[i] * ws;
    sumD += uPalette[i] * wd;
    wS += ws;
    wD += wd;
  }
  float pulse = 0.85 + 0.25 * uLevel;  // the lights breathe faintly with the music
  vec3 colS = sumS / max(wS, 1e-4);
  vec3 colD = sumD / max(wD, 1e-4);
  float aS = clamp(wS * pulse, 0.0, 1.0);
  float aD = clamp(wD * 0.8 * pulse, 0.0, 1.0);
  // Light theme: deeper tones at lower opacity read as light; raised ones as haze.
  colS = mix(colS, colS * 0.72, light);
  colD = mix(colD, colD * 0.72, light);
  float glowK = mix(1.0, 0.75, light);

  vec3 avg = (uPalette[0] + uPalette[1] + uPalette[2] + uPalette[3] + uPalette[4]) / 5.0;
  vec3 veil = mix(mix(vec3(0.55, 0.6, 0.68), avg, 0.35), vec3(1.0), light);
  vec3 mist = mix(uBg, veil, MIST * mix(1.0, 1.6, light));
  vec3 fogged = mix(mist, colD, aD * GLOW_FOG * glowK);
  fogged = mix(fogged, veil, FRESH * cover);
  // Frost: a faint static grain in the fog only.
  fogged += (valueNoise(q * 90.0) - 0.5) * 0.024 * (1.0 - 0.5 * light);
  vec3 clear = mix(uBg, colS, aS * GLOW_CLEAR * glowK);

  // A drop is a clear lens through the fog.
  vec3 col = mix(clear, fogged, dens * (1.0 - 0.85 * cov));

  // Drop shading: a darker rim, a caught highlight upper-left, and a faint
  // light-coloured crescent along the bottom. Widths floored in backing pixels.
  float px = aa / max(drop.z, 1e-4);
  float rim = smoothstep(max(0.55, 1.0 - 4.0 * px), 1.0, pl) * cov;
  col = mix(col, mix(uBg * 0.6, colS * 0.55, light), rim * 0.35);
  float hw = max(0.2, 1.5 * px);
  vec2 hl = p - vec2(-0.35, 0.42);
  float spec = exp(-dot(hl, hl) / (hw * hw)) * cov;
  col = mix(col, vec3(1.0), spec * 0.55 * (1.0 - 0.5 * light));
  float cw = max(0.15, 1.5 * px);
  float cy = (p.y + 0.62) / cw;   // squared by hand: pow() is undefined for x < 0
  float crescent = exp(-cy * cy) * (1.0 - smoothstep(0.3, 0.8, abs(p.x))) * cov;
  col = mix(col, colS, crescent * 0.25);

  // Keep the title legible: the mist (and everything on it) fades out behind it.
  col = mix(col, uBg, TITLE_QUIET * titleMask(uv) * shown);

  gl_FragColor = vec4(dither(clamp(col, 0.0, 1.0), uTime), 1.0);
}
`

interface TannhauserRes {
  tex: WebGLTexture
  // Size of the texture's storage in this context. 0 after a context restore, so
  // the next frame (loop or still) does a full upload from the surviving mask.
  uploadedW: number
  uploadedH: number
}

export interface TannhauserState {
  mask: FogMask
  stroke: StrokeTracker
  breath: BreathEnvelope
  slide: SlideEvents
  lastTrackSeq: number
}

/**
 * Bind the fog texture and bring it up to date. Runs on still frames too: a
 * still is the first frame after a context restore while paused, and a texture
 * that was never uploaded samples black, which reads as fully wiped glass.
 */
function syncFog(gl: WebGLRenderingContext, quad: GlQuad, res: TannhauserRes, mask: FogMask): void {
  gl.activeTexture(gl.TEXTURE0)
  gl.bindTexture(gl.TEXTURE_2D, res.tex)
  gl.uniform1i(quad.u('uFog'), 0)
  const resized = res.uploadedW !== mask.w || res.uploadedH !== mask.h
  if (!resized && !mask.dirty) return
  // Explicit every time: unpack state is per context and another caller could
  // leave it set. An odd mask width needs alignment 1 or the rows skew.
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1)
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false)
  gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false)
  if (resized) {
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.LUMINANCE,
      mask.w,
      mask.h,
      0,
      gl.LUMINANCE,
      gl.UNSIGNED_BYTE,
      mask.data
    )
    res.uploadedW = mask.w
    res.uploadedH = mask.h
  } else {
    gl.texSubImage2D(
      gl.TEXTURE_2D,
      0,
      0,
      0,
      mask.w,
      mask.h,
      gl.LUMINANCE,
      gl.UNSIGNED_BYTE,
      mask.data
    )
  }
  mask.markUploaded()
}

/** -1..1 pointer space (DOM y-down) to mask texel coordinates. */
const texX = (mask: FogMask, nx: number): number => (nx * 0.5 + 0.5) * mask.w - 0.5
const texY = (mask: FogMask, ny: number): number => (ny * 0.5 + 0.5) * mask.h - 0.5

export const TANNHAUSER_SPEC: ShaderSpec<TannhauserRes, TannhauserState> = {
  name: 'tannhauser',
  frag: FRAG,
  // Drops need crisper edges than Borealis's curtains, so above the ¼-res default;
  // they barely move, so 20 fps. Tune with __kampGL.tune and re-measure.
  resScale: 0.4,
  fps: 20,
  onContext(gl) {
    const tex = gl.createTexture()
    if (!tex) return null
    gl.bindTexture(gl.TEXTURE_2D, tex)
    // NPOT in WebGL1: clamp, linear, no mipmaps.
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    return { tex, uploadedW: 0, uploadedH: 0 }
  },
  dispose(gl, res) {
    gl.deleteTexture(res.tex)
  },
  createState: () => ({
    mask: new FogMask(maskWidth(16 / 10), MASK_H),
    stroke: new StrokeTracker(),
    breath: new BreathEnvelope(),
    slide: new SlideEvents(Math.random),
    lastTrackSeq: 0
  }),
  setUniforms(gl, quad, res, frame, state) {
    const { mask, stroke, breath, slide } = state
    const t = frame.elapsed
    // Follow the pane's aspect. A resample, not a reset, so wipes stay put.
    mask.resize(maskWidth(gl.drawingBufferWidth / gl.drawingBufferHeight), MASK_H)

    if (!frame.still) {
      if (frame.resumed) {
        // Nothing ticked while stopped; a half-finished moment must not replay.
        // The mask itself is kept: wiped glass stays wiped across a pause.
        breath.cancel()
        slide.cancel(t)
        stroke.reset()
        state.lastTrackSeq = frame.trackSeq
      }
      if (frame.trackSeq !== state.lastTrackSeq) {
        state.lastTrackSeq = frame.trackSeq
        breath.trigger()
      }
      // The breath fills first, so a wipe made this same frame lands on top of it.
      if (breath.tick(frame.dt)) mask.fill()
      const seg = stroke.next(frame.pointerAt, frame.pointerEpoch)
      if (seg) {
        mask.stroke(
          texX(mask, seg.ax),
          texY(mask, seg.ay),
          texX(mask, seg.bx),
          texY(mask, seg.by),
          WIPE_R * mask.h
        )
      }
      const trail = slide.tick(frame.dt, t, frame.pointerIdle, breath.active, frame.artShown)
      const head = slide.active
      if (trail && head) {
        // Trail segments are 0..1 (DOM y-down); same mapping as the shader's head.
        mask.stroke(
          trail.ax * mask.w - 0.5,
          trail.ay * mask.h - 0.5,
          trail.bx * mask.w - 0.5,
          trail.by * mask.h - 0.5,
          head.r * TRAIL_R * mask.h
        )
      }
      mask.refill(frame.dt)
    }
    syncFog(gl, quad, res, mask)

    if (frame.atRest) {
      // The designed still: fully fogged, a fixed scatter of drops, no moments.
      gl.uniform1f(quad.u('uFogOn'), 0)
      gl.uniform2f(quad.u('uBreath'), 0, 0)
      gl.uniform2f(quad.u('uDrops'), STILL_DROPS[0], STILL_DROPS[1])
      gl.uniform4f(quad.u('uSlide'), 0, 0, 0, 0)
      gl.uniform2f(quad.u('uDrift'), STILL_DRIFT[0], STILL_DRIFT[1])
    } else {
      gl.uniform1f(quad.u('uFogOn'), 1)
      gl.uniform2f(quad.u('uBreath'), breath.progress, breath.amount)
      const [fine, coarse] = dropPhase(t)
      gl.uniform2f(quad.u('uDrops'), fine, coarse)
      const s = slide.active
      if (s) {
        const pos = SlideEvents.pos(s, s.age)
        gl.uniform4f(quad.u('uSlide'), pos.x, pos.y, s.r, SlideEvents.alpha(s, s.age))
      } else {
        gl.uniform4f(quad.u('uSlide'), 0, 0, 0, 0)
      }
      // Wrapped in float64, so the shader's sin() only ever sees 0..2π.
      const angle = (p: number): number => (TAU * (((t % p) + p) % p)) / p
      gl.uniform2f(quad.u('uDrift'), angle(DRIFT_PERIODS_S[0]), angle(DRIFT_PERIODS_S[1]))
    }

    gl.uniform1f(quad.u('uArtShown'), frame.artShown)
    setTitleUniform(gl, quad, frame)
  }
}
