# WebGL Now Playing backgrounds (KAMP-780)

A shader background is a `ShaderSpec` (`shaderEngine.ts`) wrapped in `ShaderCanvas`. The base handles everything else:

- **Context:** creation and the attributes (`webgl`, opaque, low-power, no software rendering).
- **Context loss:** the background is rebuilt on restore. If no restore arrives within 2 s, it falls back to `StillBackdrop`.
- **Opaque `--bg`:** the shader paints the theme background itself, and the base redraws after every resize or restore.
- **Reduced motion:** a still frame instead of the loop.
- **Palette:** the crossfade and the `vec3[5]` packing.
- **Audio:** the level follower.
- **Pointer:** parallax.
- **Visibility:** the loop runs only while the Now Playing pane is visible.

Background research is in `docs/spikes/kamp-776-webgl.md`.

## Adding a background

1. Write the spec, with its fragment shader built from `glsl.ts`:

   ```ts
   const FRAG = `${GLSL_HEADER}${GLSL_NOISE}${GLSL_DITHER}
   void main() {
     vec3 col = /* ... */;
     gl_FragColor = vec4(dither(col, uTime), 1.0);
   }`
   export const MY_SPEC: ShaderSpec<MyRes> = {
     name: 'my-background',
     frag: FRAG,
     resScale: 0.25, // default; slow, soft backgrounds should stay here
     fps: 30,        // default
     onContext: (gl, quad) => /* textures etc., or null to fall back */,
     dispose: (gl, res) => /* free them */,
     setUniforms: (gl, quad, res, frame) => /* extra uniforms, delight moments */
   }
   ```

2. Add a one-line component, `(props) => <ShaderCanvas spec={MY_SPEC} {...props} />`.
3. Register it in `../registry.ts` and add its id to `shared/nowPlayingAnimation.ts`.

The standard uniforms are set every frame:
- `uRes`, `uTime`, `uBg`
- `uPalette[5]`: crossfaded on the CPU
- `uPointer`: -1..1, DOM y-down
- `uLevel`: 0..1, delayed about 300 ms to match the speakers

`uPointerActive` (0..1, eased) says whether the cursor is over the pane at all. `uPointer` recentres on leave, so anything that glows near the cursor should scale by it.

`setUniforms` also receives a `FrameInfo` (KAMP-774). Besides `dt`, `elapsed`, `level` and `still`, it has:
- `atRest`: the reduced-motion still. Draw the designed rest frame here. `still` alone is also true for a paused palette or theme repaint.
- `resumed`: the first loop frame after `start()`. Drop any moment in flight from before the stop.
- `trackSeq`: increments once per real track change while running. It never increments on mount, on a null transition or while stopped. Compare it with the last value you saw.
- `artShown`: eased 0..1 art visibility (KAMP-769). It lands directly while stopped.
- `pointerActive` and `pointerIdle` (seconds), for cursor effects and idle-gated rare moments.
- `pointerAt`: the last real cursor position (KAMP-765). Unlike `uPointer` it is not eased and never recentres on leave. Anchor a cursor effect that fades out here, or it slides toward the centre as it fades.
- `pointerEpoch` (KAMP-772): bumped each time the cursor enters the pane. Connect two `pointerAt` samples into one stroke only when their epochs match, or a quick leave and re-entry draws a line across the pane.
- `titleRect`: the title's text box, normalised and DOM y-down, or null. Keep busy detail out from behind the title. It is the text extent: the title element itself is full-width, so its box would cover the whole pane.

**Per-mount state goes in `createState()`, not in `R` or the module.** It is created once per engine, survives a context restore (so a moment in flight carries on), and is passed to `setUniforms` as its last argument.

**GL resources die with the context.** Create them in `onContext`, which runs again after every restore. Keep the CPU-side source data, such as a decoded bitmap, on the spec side so it can be uploaded again.

**Always `dither()` last.** Dark, slow gradients band visibly without it.

**The spec must be a module-level constant.** The engine is built once per mount.

## Budget

Cost is linear in pixels × frames, with no measurable fixed overhead (KAMP-776). Each background states its `resScale` and `fps` and is measured against a GPU-busy target on the Intel integrated GPU. The target starts at about 20% or less and will be refined once KAMP-778 brings in the Windows numbers.

**The base itself is free.** The flat reference background, at the default ¼ resolution and 30 fps, measured on the Intel Mac on 2026-11-11 (n = 60 each):

| Variant | GPU busy | Avg cores active |
|---|---|---|
| None | 2.25% ± 0.77 | 1.64 ± 0.31 |
| Reference | 2.73% ± 2.02 | 2.17 ± 0.90 |

That is +0.5 GPU-busy points, inside noise. So a background's cost is its shader's per-pixel work multiplied by its draw rate. The CPU rise is within noise too, but check it first if a background shows main-thread cost.

## Measuring

Launch with `KAMP_DIAGNOSTICS=1 npm start`. Keep music playing and the display pinned at 60 Hz. Then, in DevTools:

| Hook | What it does |
|---|---|
| `__kampGL.reference()` / `.clear()` | Show the flat reference background, or return to your choice. This is never persisted. |
| `__kampGL.tune({ scale, fps })` | Try a different budget on the mounted background. |
| `__kampGL.stats()` | Actual draw rate since the last call, plus the backing size. |
| `__kampGL.lose()` / `.restore()` / `.cycle(ms)` | Exercise context loss. `lose()` alone falls back after 2 s. |

Record each variant with `sudo powermetrics --samplers cpu_power,gpu_power -i 1000 -n 60 -o <file>` and average the files with `scripts/summarize_powermetrics.py`. Read **GPU busy**, not package power.

To exercise the no-GPU fallback, launch with `KAMP_DISABLE_GPU=1`.
