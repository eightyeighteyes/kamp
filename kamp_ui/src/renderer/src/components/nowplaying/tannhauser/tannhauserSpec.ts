// KAMP-772: "Tannhäuser", a Now Playing background of condensation on a
// rainy-night window, built on the shared WebGL base (KAMP-780). The file name
// avoids the umlaut; the picker label keeps it.
//
// Motion is a steady trickle over beaded glass:
//   * Behind the glass: five soft lights in album colours, drifting slowly. The
//     field is evaluated ONCE per pixel, accumulating a sharp and a diffuse
//     falloff in the same loop: fogged glass shows the diffuse one, clear glass
//     the sharp one, and a drop's lens just moves the sample point. (A separate
//     refraction pass would have been a third evaluation.)
//   * Fog density comes from a CPU-side byte mask (shared/tannhauserTiming.ts
//     FogMask) uploaded as a LUMINANCE texture. The cursor wipes it; it refills
//     linearly. It is the first texture any background owns; see the README.
//   * Beads: two hashed-cell fields, one cell lookup each. Rows are shifted by a
//     per-row hash and most beads are small enough to roam their whole cell, so
//     the field never reads as a lattice. A fine bead beside a swelling coarse
//     one fades into it, which reads as merging. Beads exist only on fog.
//   * Runs: a few drops always slide in stick-slip jerks, grow, merge and leave
//     clear trails (SliderField). Their trails are wiped into the mask, so they
//     refog on their own and swallow the beads along the way.
//   * Every drop is a bottom-heavy lens: it sags, tapers at the top, and shows
//     an inverted, brighter view of the lights, brightest in its thick base.
//   * Track change: fresh fog breathes outward from the centre (BreathEnvelope).
// Every layer is a mix() toward a target, so a light theme doesn't clip.

import {
  BreathEnvelope,
  FogMask,
  MASK_H,
  SliderField,
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
const DRIFT_PERIODS_S: [number, number] = [131, 197] // the lights' slow drift
const STILL_DROPS: [number, number] = [0.37, 0.61] // the reduced-motion still's beads
const STILL_DRIFT: [number, number] = [1.3, 4.1]
const SLOTS = SliderField.SLOTS
const TAU = Math.PI * 2

const FRAG = `${GLSL_HEADER}${GLSL_NOISE}${GLSL_DITHER}${GLSL_TITLE_MASK}
uniform sampler2D uFog;  // fog mask, row 0 = top of the pane; 1 = fogged
uniform float uFogOn;    // 1 = use the mask; 0 = the designed still (all fogged)
uniform vec2 uBreath;    // front progress 0..1, strength 0..1
uniform vec2 uDrops;     // fine and coarse bead phases, 0..1
uniform vec4 uSliders[${SLOTS}]; // x, y (0..1, DOM y-down), radius (pane heights), alpha
uniform float uStretch[${SLOTS}]; // vertical elongation, 1 at rest
uniform vec2 uDrift;     // light drift angles, 0..2pi
uniform float uArtShown;

const int SLOTS = ${SLOTS};
const float FINE_CELL = 0.03;      // pane heights
const float COARSE_CELL = 0.075;
const float FINE_DENSITY = 0.55;   // fraction of cells holding a bead
const float COARSE_DENSITY = 0.35;
const float FINE_R = 0.22;         // largest bead, fraction of the cell
const float COARSE_R = 0.2;
const float REFRACT = 2.2;         // lens offset, radii
const float BREATH_W = 0.18;       // soft width of the breath front, pane heights
const float MIST = 0.2;            // mist veil over the backdrop
const float GLOW_FOG = 0.55;       // light scattered through fog
const float GLOW_CLEAR = 0.75;     // light seen through clear glass
const float LENS = 0.9;            // how much a drop shows its lens over the glass
const float FRESH = 0.12;          // extra veil while a breath is fresh
const float TITLE_QUIET = 0.75;

float lum(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

// A drop on a window sags: its bulk sits low and its top tapers. Returns the lens
// coordinates (about -1..1, y up) and the coverage, for a drop of radius r (pane
// heights) at offset d from its anchor. The edge is floored in backing pixels.
vec3 dropShape(vec2 d, float r, float stretch) {
  vec2 p = d / r;
  p.y /= stretch;
  p.y += 0.15;                                 // centre of mass below the anchor
  p.x *= 1.0 + 0.32 * clamp(p.y, 0.0, 1.2);    // taper toward the crown
  p.y *= p.y < 0.0 ? 1.12 : 0.92;              // flatter base, taller crown
  float aa = 1.5 / (uRes.y * r);
  return vec3(p, 1.0 - smoothstep(1.0 - aa, 1.0, length(p)));
}

// The bead in q's cell: xy = lens coords, z = radius (pane heights), w = coverage.
// The jitter is bounded by the bead's own size, so it never crosses its cell and
// one lookup per field suffices; small beads roam the whole cell.
vec4 dropIn(vec2 q, float cell, float density, float maxR, float phase, float seed) {
  // Shift each row by its own amount, so the cells never line up in columns.
  float row = floor(q.y / cell);
  vec2 sq = vec2(q.x + hash1(vec2(row, seed)) * cell, q.y);
  vec2 id = floor(sq / cell);
  vec2 h = hash2(id + seed);
  float exists = step(hash1(id + seed * 1.37), density);
  float life = fract(phase + h.x * 0.73 + h.y * 0.29);
  // Forms and swells over most of its life, then is drawn away. Zero at both
  // ends, so the life wrap never shows.
  float grow = smoothstep(0.0, 0.6, life) * (1.0 - smoothstep(0.88, 1.0, life));
  float size = 0.25 + 0.75 * h.y * h.y;        // mostly small, a few large
  float r = cell * maxR * size * grow;
  vec2 j = hash2(id + seed + 17.0) - 0.5;
  // 1.3: the sagging shape reaches about 1.1 radii from its anchor.
  vec2 c = (id + 0.5 + j * (1.0 - 2.6 * maxR * size)) * cell;
  vec3 s = dropShape(sq - c, max(r, 1e-4), 1.0);
  float aa = 1.5 / uRes.y;
  return vec4(s.xy, r, exists * s.z * smoothstep(0.0, 2.0 * aa, r));
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

  // Beads, strongest wins. They form only on fog: a wiped patch or a run's trail
  // is clear glass until it mists over again.
  float onFog = smoothstep(0.3, 0.8, dens);
  vec4 fine = dropIn(q, FINE_CELL, FINE_DENSITY, FINE_R, uDrops.x, 3.1);
  vec4 coarse = dropIn(q, COARSE_CELL, COARSE_DENSITY, COARSE_R, uDrops.y, 7.7);
  float swell = coarse.z / (COARSE_CELL * COARSE_R);
  fine.w *= 1.0 - (1.0 - smoothstep(1.0, 2.2, length(coarse.xy))) * swell;
  fine.w *= onFog;
  coarse.w *= onFog;
  vec4 drop = coarse.w > fine.w ? coarse : fine;

  // Running drops: never gated on fog (each has just cleared its own trail).
  for (int i = 0; i < SLOTS; i++) {
    vec4 s = uSliders[i];
    if (s.w <= 0.0) continue;
    vec3 sh = dropShape(q - vec2(s.x * aspect, 1.0 - s.y), max(s.z, 1e-4), uStretch[i]);
    float c = sh.z * s.w;
    if (c > drop.w) drop = vec4(sh.xy, s.z, c);
  }

  vec2 p = drop.xy;
  float pl = length(p);
  float cov = drop.w;

  // One evaluation of the lights behind the glass. Through a drop the view is
  // inverted and magnified: the sample point moves against the lens coordinate.
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
  vec3 col = mix(clear, fogged, dens);

  // The lens: a drop gathers light, so it shows a brighter view than the glass
  // around it, brightest in its thick base and dimmest at the thin crown.
  float base = 1.0 - smoothstep(-0.9, 0.6, p.y);
  vec3 lens = mix(uBg, colS, clamp((aS * (1.3 + 0.5 * base) + 0.06) * glowK, 0.0, 1.0));
  col = mix(col, lens, cov * LENS);

  // A thin dark meniscus, strongest at the tapered crown, and a glint where light
  // refracts through the base. Widths floored in backing pixels (KAMP-765).
  float px = aa / max(drop.z, 1e-4);
  float rimW = max(0.18, 3.0 * px);
  float rim = smoothstep(1.0 - rimW, 1.0, pl) * cov * (0.35 + 0.65 * smoothstep(-0.4, 0.8, p.y));
  col = mix(col, uBg * mix(0.45, 0.8, light), rim * 0.55);
  float gw = max(0.16, 1.5 * px);
  vec2 g = (p - vec2(0.22, -0.58)) / gw;
  float glint = exp(-dot(g, g)) * cov;
  col = mix(col, mix(vec3(1.0), colS, 0.35), glint * 0.6 * (1.0 - 0.4 * light));

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
  sliders: SliderField
  lastTrackSeq: number
  // Reused every frame, so the render loop allocates nothing for the sliders.
  sliderBuf: Float32Array
  stretchBuf: Float32Array
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
  // Drops need crisper edges than Borealis's curtains, so above the ¼-res default.
  // Tune with __kampGL.tune and re-measure.
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
    sliders: new SliderField(Math.random),
    lastTrackSeq: 0,
    sliderBuf: new Float32Array(SLOTS * 4),
    stretchBuf: new Float32Array(SLOTS)
  }),
  setUniforms(gl, quad, res, frame, state) {
    const { mask, stroke, breath, sliders, sliderBuf, stretchBuf } = state
    const t = frame.elapsed
    const aspect = gl.drawingBufferWidth / gl.drawingBufferHeight
    // Follow the pane's aspect. A resample, not a reset, so wipes stay put.
    mask.resize(maskWidth(aspect), MASK_H)

    if (!frame.still) {
      if (frame.resumed) {
        // Nothing ticked while stopped; a half-finished breath must not replay.
        // The mask and the running drops are kept: they are places, not moments.
        breath.cancel()
        stroke.reset()
        state.lastTrackSeq = frame.trackSeq
      }
      if (frame.trackSeq !== state.lastTrackSeq) {
        state.lastTrackSeq = frame.trackSeq
        breath.trigger()
      }
      // The breath fills first, so a wipe or trail from this same frame lands on top.
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
      // Trails are 0..1 (DOM y-down), the same mapping as the shader's drops.
      for (const tr of sliders.tick(frame.dt, t, frame.pointerIdle, frame.artShown, aspect)) {
        mask.stroke(
          tr.ax * mask.w - 0.5,
          tr.ay * mask.h - 0.5,
          tr.bx * mask.w - 0.5,
          tr.by * mask.h - 0.5,
          tr.r * mask.h
        )
      }
      mask.refill(frame.dt)
    }
    syncFog(gl, quad, res, mask)

    sliderBuf.fill(0)
    stretchBuf.fill(1)
    if (frame.atRest) {
      // The designed still: fully fogged, a fixed scatter of beads, nothing running.
      gl.uniform1f(quad.u('uFogOn'), 0)
      gl.uniform2f(quad.u('uBreath'), 0, 0)
      gl.uniform2f(quad.u('uDrops'), STILL_DROPS[0], STILL_DROPS[1])
      gl.uniform2f(quad.u('uDrift'), STILL_DRIFT[0], STILL_DRIFT[1])
    } else {
      gl.uniform1f(quad.u('uFogOn'), 1)
      gl.uniform2f(quad.u('uBreath'), breath.progress, breath.amount)
      const [fine, coarse] = dropPhase(t)
      gl.uniform2f(quad.u('uDrops'), fine, coarse)
      const list = sliders.sliders
      for (let i = 0; i < Math.min(list.length, SLOTS); i++) {
        const s = list[i]
        sliderBuf[i * 4] = s.x
        sliderBuf[i * 4 + 1] = s.y
        sliderBuf[i * 4 + 2] = s.r
        sliderBuf[i * 4 + 3] = s.alpha
        stretchBuf[i] = s.stretch
      }
      // Wrapped in float64, so the shader's sin() only ever sees 0..2π.
      const angle = (p: number): number => (TAU * (((t % p) + p) % p)) / p
      gl.uniform2f(quad.u('uDrift'), angle(DRIFT_PERIODS_S[0]), angle(DRIFT_PERIODS_S[1]))
    }
    gl.uniform4fv(quad.u('uSliders[0]'), sliderBuf)
    gl.uniform1fv(quad.u('uStretch[0]'), stretchBuf)

    gl.uniform1f(quad.u('uArtShown'), frame.artShown)
    setTitleUniform(gl, quad, frame)
  }
}
