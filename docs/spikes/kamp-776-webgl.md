# KAMP-776: WebGL rendering for Now Playing backgrounds

**Decision:** add a WebGL rendering path for Now Playing backgrounds, with a GPU budget per background.

**Prototype:** the "Thalassa" pool-caustics shader on branch `kamp-776-webgl-spike`. It is reference only and is not merged. The branch also carries the dev hooks used below.

**Follow-ups:**
- KAMP-777: cut the GPU cost of Bokeh's blur.
- KAMP-778: repeat these measurements on Windows (ANGLE/D3D11).
- KAMP-779: keep Bokeh consistently populated. It sometimes read as blank during testing, most visibly with art missing.

## Measurements

All runs used:
- An Intel Mac, integrated GPU only. Chromium stayed on the integrated GPU with `powerPreference: 'low-power'`.
- `sudo powermetrics --samplers cpu_power,gpu_power -i 1000 -n 60`, averaged with `scripts/summarize_powermetrics.py`.
- A 60 Hz display, the same maximized window, and music playing in every run.

**GPU busy is the signal to use.** Its spread is about ±1–3 points. Package power is too noisy to rank variants: the "None" run on battery read 18.7 W ± 12, which is higher than Bokeh's.

| Variant | GPU busy, AC | GPU busy, battery |
|---|---|---|
| None | 1.9% | 2.1% |
| Bokeh without `filter: blur(28px)` | 15.3% | 14.3% |
| Thalassa, ¼ resolution (scale 0.25), 30 fps | 16.3% | (not run) |
| Thalassa, ½ resolution, 15 fps | 27.7% | (not run) |
| Thalassa, ½ resolution, 30 fps | 57.9% | 59.7% |
| Bokeh as shipped | 65.4% | 66.0% |

**Findings:**
- **The shader's cost is linear in pixels × frames, with no measurable fixed overhead.** Net of the 1.9% baseline:
  - A quarter of the pixels cost 14.4 points against 56.0. Linear scaling predicts 14.0.
  - Half the frames cost 25.8 points. Linear scaling predicts 28.0.
  - So WebGL itself isn't expensive. A shader costs exactly what its per-pixel work multiplied by its draw rate costs, which makes it budgetable.
- **Thalassa at quarter resolution looks acceptable.** The user checked it by eye. At that setting it costs about what Bokeh's orbs cost without the blur, and roughly 4× less than Bokeh as shipped.
- **Bokeh's cost is almost all the CSS blur**: about 50 of its 65 points. This is separate from WebGL and is what KAMP-777 targets.

## Answers to the ticket's questions

**Fit with the engine contract: yes.** `ThalassaEngine` has the same public surface as `BokehEngine` (`setPalette`, `setBg`, `setPointer`, `start`/`stop`, `resize`, `destroy`, `renderStatic`), plus `pushLevel`. Its React wrapper is a hook-for-hook copy of `BokehCanvas`. A WebGL engine needs two things the contract doesn't have yet:
- Construction can fail. There may be no context, the GPU may be blocklisted, the context may be software-only, or the shader may not compile. `create()` returns null for all of these.
- An engine can fail later, when a lost context is never restored. The wrapper needs a way to swap in a fallback.

**Robustness:**
- **Context loss:** call `preventDefault()` on `webglcontextlost`, otherwise Chromium never fires `webglcontextrestored`. Every GL object dies with the context, so restore rebuilds them all. If no restore arrives within 2 s, swap to the fallback, because Chromium can block WebGL for good after repeated GPU crashes. Verified with `WEBGL_lose_context` via `__kampGL.cycle()` and `__kampGL.lose()`.
- **Hardware acceleration off or GPU blocklisted:** request the context with `failIfMajorPerformanceCaveat: true`, which refuses SwiftShader. The fallback is a **static CSS gradient, not Bokeh**: with hardware acceleration off, Bokeh's CSS blur is composited in software, which is likely the most expensive option of all. Verified by launching with `KAMP_DISABLE_GPU=1`.
- **KAMP-631 seam:** an opaque context (`alpha: false`) cannot show the compositor base-color seam. But it covers `.now-playing-backdrop`'s themed background, so:
  - The shader paints `--bg` itself. The engine receives it through the same `selectedTheme` effect Bokeh uses.
  - Any path that clears the buffer must redraw immediately. Assigning `canvas.width` clears it to opaque **black**, and a context restore or the reduced-motion still frame does the same. Verified on several non-default themes.

**Architecture:**
- **One context per engine.** `NowPlayingBackground` mounts one engine at a time, keyed by id, so sharing a context buys nothing.
- **No library.** `glQuad.ts` is about 80 lines (compile and link, one fullscreen triangle, a cache of uniform locations) and covers everything a fullscreen background needs. regl or twgl would only earn their place with multi-pass effects, such as feedback buffers for fluid.
- **Don't call `loseContext()` in `destroy()`.** React StrictMode re-runs the mount effect on the same `<canvas>`, and `getContext` would then return the context that was just lost.

**Palette:** upload the 5 `palette.ts` colors as a `uniform vec3[5]`. Crossfade them on the CPU, as Bokeh does. `padPalette` returns `[]` when a cover yields no colors, so packing falls back to the accent palette and then to grey. Otherwise the shader receives NaN.

**Audio: yes.**
- Feed the `leftDb`/`rightDb` samples into a follower from `useStore.subscribe`, timestamped on arrival. Don't poll them from the render tick.
- The follower delays the level by 300 ms, eases it with a 150 ms time constant, and decays to zero when samples stop (pause or stop).
- The swell lagged the music rather than leading it.
- The meter runs about `audio-buffer` (0.2 s) plus the output device's latency ahead of the speakers. Bluetooth will need more, so a per-output setting is a likely future need.
- Audio reactivity is a product choice per background. Bokeh is deliberately calm.

## Recommendation

1. **Adopt WebGL for per-pixel backgrounds**: Solaris, Thalassa, Arrakis, Ubik, and Borealis as a shader.
2. **Make the budget part of the contract.** Each shader background states its resolution scale and frame cap, and is measured with the method above against a GPU-busy target on the Intel integrated GPU. Start at ≤ ~20% (the quarter-resolution Thalassa result) and refine it once KAMP-778 brings in the Windows numbers.
   - Because cost is linear, the budget can be reached with resolution, frame rate or shader complexity.
   - Slow, soft backgrounds should default to ¼ resolution.
3. **Extract the spike's pieces into a shared WebGL engine base**: `glQuad`, context-loss handling with a timed fallback, the opaque-bg repaint rules, palette packing and the level follower. Each background then supplies only a shader and its uniforms.

## Other findings

- **Bokeh's reduced-motion still frame shows the previous track's colors.**
  - `setPalette` sets `paletteT = 0` and then calls `renderStatic()`.
  - While stopped there is no loop to advance the crossfade.
  - So the still frame paints the `from` palette, always one track behind.
  - Fix: land directly on the new colors when the loop isn't running, as `ThalassaEngine.setPalette` does. Noted on KAMP-779.
- **Spike selections carry over into the installed app.** The preview build (`npm start`) shares localStorage with the installed app. An id the installed build doesn't know falls back through the legacy toggle to **Bokeh**, even if you had chosen None. Reset the animation after testing a spike build.
- **`npm run build` strips `kamp_ui/node_modules`.** Run `npm install` afterwards, before any typecheck or test.
