# Now Playing bokeh — tuning cheat sheet

The "Bokeh" Now Playing animation (KAMP-561; one entry in `registry.ts` since
KAMP-766) is built from tunable constants at
the top of two files, plus one CSS line. Nothing here needs a rebuild to try —
Vite hot-reloads edits, so you can slide values with the app running. All knobs
are at conservative midpoints; this is the map for adjusting the character by eye.

Grouped by what you actually _see_.

## Color / mood — `palette.ts`

| Knob                      | Default     | Raise it →                                                                                      |
| ------------------------- | ----------- | ----------------------------------------------------------------------------------------------- |
| `SAT_FLOOR`               | 0.45        | More vivid, punchier colors (lower → muted / pastel)                                            |
| `LIGHT_MIN` / `LIGHT_MAX` | 0.45 / 0.68 | Brighter glow; widen the gap for more tonal variety                                             |
| `MONO_SAT_THRESHOLD`      | 0.12        | More covers judged "grey" and fall back to the theme accent (lower → trusts faint cover colors) |
| `HUE_BINS`                | 16          | Finer hue separation — more distinct swatches from busy covers                                  |

## Softness / blur — `assets/track-list.css`, `.now-playing-bokeh`

- `filter: blur(28px)` — **the single biggest aesthetic lever.** Higher = dreamier
  and more diffuse; lower = defined, more distinct "orbs." Useful range ~20–40px.
- Positioning and the KAMP-631 opaque backing live on the shared
  `.now-playing-backdrop` class; `.now-playing-bokeh` holds only the blur.

## Motion feel — `bokehEngine.ts`

| Knob                       | Default      | Effect                                                                                       |
| -------------------------- | ------------ | -------------------------------------------------------------------------------------------- |
| `TIERS[*].cross`           | 45–150 s     | Seconds to cross the screen. **Raise for slower, calmer drift**; lower for livelier.         |
| `CROSSFADE_MS`             | 1000         | Track-change recolor duration. Raise for a lazier color morph.                               |
| `ax` / `ay` (in `makeOrb`) | 0.05–0.07    | Wobble amplitude — higher = more meandering, less linear travel.                             |
| `breathP`                  | 12–20 s      | Breathing period. Depth is set by the `0.08` (scale) and `0.06` (alpha) factors in `draw()`. |
| `FRAME_MS`                 | 33 (~30 fps) | Frame cap. Raise (~50 → ~20 fps) for an even lazier, lighter feel.                           |

## Cursor parallax — `bokehEngine.ts` (KAMP-626)

As the cursor moves over the Now Playing pane the orb field eases a few pixels
_opposite_ the cursor, then settles. Subtle and heavily damped by design; off under
`prefers-reduced-motion` and when the pane is inactive/hidden.

| Knob                    | Default                             | Effect                                                                                                                                                                                                                   |
| ----------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `TIERS[*].parallax`     | hero 0.010, mid 0.020, accent 0.028 | Per-tier shift amplitude (fraction of the short edge). Raise for a stronger effect. The mapping is intentionally inverted from physical depth — big orbs move least, so the backdrop stays calm while the accents dance. |
| `PARALLAX_TAU`          | 0.35 s                              | Damping time constant. Higher = slower, heavier follow; lower = snappier.                                                                                                                                                |
| offset sign in `draw()` | subtract                            | Orbs move opposite the cursor (depth read). Flip to add for a "drag the field" feel.                                                                                                                                     |

## Fireflies — `bokehEngine.ts` (KAMP-627)

Very rarely, a single warm mote fades in near a corner, bows outward, breathes, and
fades. Rare and irregular by design (Poisson timing), only appears when the pointer has
been still, one at a time; off under `prefers-reduced-motion` and when inactive/hidden.

| Knob                                     | Default      | Effect                                                                                                                                 |
| ---------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| `FIREFLY_GAP_FLOOR` / `FIREFLY_GAP_MEAN` | 45 s / 110 s | Inter-arrival = floor + exponential(mean); avg ~2.5 min. Raise the mean to make it rarer/more special.                                 |
| `FIREFLY_GAP_MAX`                        | 300 s        | Soft cap on the heavy tail (longest dry spell).                                                                                        |
| `FIREFLY_IDLE`                           | 8 s          | Pointer-idle required before a _due_ mote appears — keeps it to still moments.                                                         |
| `FIREFLY_LIFE`                           | 6–10 s       | Total lifetime (slow emerge, long fade-out).                                                                                           |
| `FIREFLY_ALPHA`                          | 0.6          | Peak core brightness. **Raise if the 28px blur washes it out; lower if it's too loud.** Never let it be the brightest thing on screen. |
| `FIREFLY_SIZE`                           | 0.016–0.024  | Core radius (fraction of the short edge). Larger survives the blur better.                                                             |
| `FIREFLY_TRAVEL` / `FIREFLY_BOW`         | 0.07 / 0.012 | Eased outward drift distance and the perpendicular path bow.                                                                           |

## Track-change bloom — `bokehEngine.ts` (KAMP-628)

On each track change the orbs briefly swell (mostly radius) and settle, peaking as the
new colors are ~half arrived. A soft "inhale," never a flash; off under
`prefers-reduced-motion` and while inactive/hidden.

| Knob              | Default | Effect                                                                                                             |
| ----------------- | ------- | ------------------------------------------------------------------------------------------------------------------ |
| `BLOOM_DURATION`  | 1.5 s   | Total swell lifetime. Kept longer than the 1 s crossfade so the light settles _after_ the colors land.             |
| `BLOOM_ATTACK`    | 0.3     | Fraction of the duration spent rising (~0.45 s). Lower = snappier onset (risks a "pop"); higher = a slower emerge. |
| `BLOOM_RADIUS`    | 0.16    | Peak radius boost. **This carries the effect** — radius reads as "inhaling" and never clips.                       |
| `BLOOM_ALPHA`     | 0.20    | Peak brightness boost. Keep low — brightness spikes read as a flash, especially where orbs overlap.                |
| `BLOOM_MAX_DELAY` | 0.12 s  | Per-orb onset jitter so the field doesn't bloom in lockstep. 0 = perfectly synchronized.                           |

## Density / size / intensity — `bokehEngine.ts`

| Knob             | Default                                         | Effect                                                                                           |
| ---------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `TIERS[*].size`  | hero 0.55–0.75, mid 0.30–0.45, accent 0.12–0.22 | Orb diameter as a fraction of the short screen edge. Bigger hero orbs = more color wash.         |
| `TIERS[*].alpha` | 0.32–0.56                                       | Per-orb opacity. **Lower the whole set to let the art pop more**; raise for a bolder background. |
| `ORB_PLAN`       | 7 entries                                       | Orb _count_ and which palette slot each orb wears. Add/remove rows to change density.            |

## Focus on the art (vignette) — `bokehEngine.ts`, `drawVignette()`

- Inner radius `r * 0.45` and outer stop alpha `0.35` control the "clearing" behind
  the cover. Raise `0.35` to darken the edges harder (art stands out more); lower it
  to let orbs reach the corners.

## Performance — `bokehEngine.ts` (only if it ever feels heavy)

- `RES_SCALE` (0.5) — backing-store resolution as a fraction of clamped DPR. Drop to
  0.4 for cheaper and blurrier.
- `MAX_DPR` (2) — caps the device-pixel-ratio the backing store honors.
- The loop already pauses when the pane is hidden, the window is occluded, or
  reduced-motion is set, and the canvas is not mounted at all when another animation
  (or "None") is selected — so idle cost is zero in those states.

## Fastest path

Start with three knobs — they change the character the most:

1. CSS `blur` (`.now-playing-bokeh`)
2. `TIERS[*].cross` (drift speed)
3. `TIERS[*].alpha` (how loud vs. how much the art pops)

## Deferred ideas (filed as follow-up tickets)

Occasional "firefly" motes (KAMP-627), a track-change "bloom" pulse (KAMP-628),
per-theme palette tinting (KAMP-629), and a dev-only swatch overlay to visualize the
extracted palette while tuning (KAMP-630). Cursor parallax shipped in KAMP-626 (above).

---

# Chapel (KAMP-775)

Three stacked, feathered fields of album color that breathe over 20–40 s. The geometry
and timing are in `src/shared/chapelLayout.ts` (covered by `npm test`), and the drawing
is in `chapelEngine.ts`. Chapel has no CSS filter: the feathering comes from gradient
stops.

## Composition — `chapelLayout.ts`

| Knob                                        | Default                                  | Effect                                                                                                                               |
| ------------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `MARGIN_X` / `MARGIN_TOP` / `MARGIN_BOTTOM` | 0.08 / 0.07 / 0.07                       | How far the fields float in from the edges.                                                                                          |
| `GAP`                                       | 0.045                                    | Seam height as a fraction of pane height. It is also the edge feather, so raising it softens every edge.                             |
| `BREATH_PERIOD_S`                           | 20–40                                    | Each field's swell period.                                                                                                           |
| `HEIGHT_BREATH` / `ALPHA_BREATH`            | 0.08 / 0.15                              | How much a field grows and brightens at the peak of its breath.                                                                      |
| `FIELD_ALPHA`                               | 0.5                                      | Loudness over `--bg`. The palette is already saturated, so above about 0.6 the fields upstage the art.                               |
| `TITLE_DIM`                                 | 0.6                                      | The `--bg` band behind the title, only while the art shows. A test holds the effective field alpha under the title at or below 0.25. |
| `PROPORTION_RANGE`                          | 0.75–1.35                                | Per-track spread of field heights.                                                                                                   |
| `SeamWarmth.ATTACK_S` / `RELEASE_S`         | 1.5 / 4                                  | How fast the cursor glow warms and cools.                                                                                            |
| `SeamBloom.GAP_*` / `LIFE_S`                | 90 + exp(120) s, capped at 300 / 12–16 s | Spacing and length of the rare seam warming. Lower `GAP_FLOOR_S` temporarily to see it while tuning.                                 |

## Feel and cost — `chapelEngine.ts`

- `CROSSFADE_MS` (4000) and `RETARGET_MS` (7000) set how slowly a track change lands.
- `GLOW_ALPHA`, `BLOOM_ALPHA` and `WARM` set the glow intensity and how warm it reads.
- `POINTER_REACH` (0.12 of pane height) is how close the cursor has to be before a seam warms.
- `DEFAULT_FPS` (15) and `RES_SCALE` (0.5) set the budget. If GPU busy is over target,
  drop to 10 fps first. Keep the scale at 0.5 or above: lower scales band on dark
  covers, because there is no blur to hide the steps.

## Measuring

Measure as described in `webgl/README.md`: `powermetrics` for 60 s per variant, display
pinned at 60 Hz, music playing. Run these variants:

1. **None**: the baseline.
2. **Chapel, cursor outside the pane**: no glows are drawn. This is the effect-off variant.
3. **Chapel, cursor resting on a seam**: the glow is drawn every frame.

The target is GPU busy at or below about 20% on the Intel Mac.

---

# Halation (KAMP-774)

Film grain, projector dust and warm light leaks, drawn by a WebGL shader on the shared base (`webgl/README.md`). The timing lives in `src/shared/halationTiming.ts` and is covered by `npm test`. The shader and its uniforms are in `halation/halationSpec.ts`.

## Timing — `halationTiming.ts`

| Knob                                    | Default                       | Effect                                                                                                      |
| --------------------------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `SEED_PERIOD_S` / `SEED_FADE_S`         | 3–4 s / 0.5 s                 | How often the grain re-seeds, and the crossfade between seeds. Keep it well above per-frame, or it strobes. |
| `BurnEnvelope.RISE_S` / `DECAY_S`       | 2 / 3.5 s                     | The track-change burn's spread-in and fade. A shorter rise means a steeper slope, and that risks a flash.   |
| `RareEvents.GAP_FLOOR_S` / `GAP_MEAN_S` | 60 + exp(90) s, capped at 240 | Spacing of the rare hair or jolt. Call `forceNext()` from a breakpoint to see one while tuning.             |
| `RareEvents.HAIR_CHANCE`                | 0.6                           | Share of rare events that are a hair rather than a gate jolt.                                               |
| `RareEvents.IDLE_S`                     | 6 s                           | How long the cursor has to be still before a rare event can start.                                          |

## Look — `halationSpec.ts`

The GLSL constants are at the top of `FRAG`:

- `GRAIN` (0.06): grain strength.
- `LEAK` (0.42) and `BURN` (0.6): peak opacity of the leaks and the burn.
- `CELL` (0.055 of pane height, defined in TypeScript and passed to the shader) and `DUST_DENSITY` (0.09): mote spacing and how many cells hold a mote.
- `MEANDER` (0.1 of a cell): how far each mote wanders inside its cell. 0.1 is the ceiling; above it a mote clips at the cell edge.

The TypeScript constants are:

- `WEAVE` and `JOLT`: the gate offsets, as a fraction of pane height. Keep `WEAVE` tiny, because it moves the dust and the light but never the grain.
- `LEAK_PERIODS_S`: the breathing period of each leak.
- `DUST_RISE` (0.008) and `DUST_SLIDE` (0.003): the dust field's drift, in pane heights per second. At these values a mote takes about 2 minutes to cross the pane.
- `DUST_SWAY` (0.025) and `DUST_SWAY_S` (41): the field's lazy sideways sway.
- `DUST_TILE` (64): the dust field repeats every 64 cells, so the drift can wrap without a jump. Keep it larger than the pane in cells.

Behind the title text, the leaks, glints and half the grain are attenuated, so the title stays legible. The mask is the title's text box, never the full-width title element.

## Cost

`resScale` is 0.5 and the frame cap is 20 fps. At 0.25 the grain upscales into blocks. If GPU busy is over the target, try `__kampGL.tune({ fps: 15 })` first, then `scale: 0.4`.

Measured on the Intel Mac on 2026-11-11, with dust drift: about 22% GPU busy (24% GPU active). That is just over the ~20% target and was accepted as is. Re-measure if a change adds per-pixel work.

Measure these variants as described above:

1. None
2. Halation with the art visible
3. Halation with the art hidden
4. Halation with the cursor resting over the dust

---

# Borealis (KAMP-765)

Three slow aurora curtains in album colours, drawn by a WebGL shader on the shared base (`webgl/README.md`). The timing lives in `src/shared/borealisTiming.ts` and is covered by `npm test`. The shader and its uniforms are in `borealis/borealisSpec.ts`. The Poisson gap and dt clamp are shared with Halation in `src/shared/rareSchedule.ts`.

## Timing — `borealisTiming.ts`

| Knob                                       | Default                        | Effect                                                                                                                                       |
| ------------------------------------------ | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `FLOW_PERIODS_S`                           | 97 / 143 s                     | The two noise loops. Keep them coprime so the combined repeat stays in hours. Lower both for a livelier sky.                                 |
| `SurgeEnvelope.RISE_S` / `DECAY_S`         | 1 / 3 s                        | The track-change surge. `RISE_S` matches the base's 1 s palette crossfade, so the band peaks as the new colours land. Shorter risks a flash. |
| `SurgeEnvelope.BOOST_UNTIL`                | 0.5                            | Before this point a skip brightens the travelling band; after it, a second band rolls in from zero.                                          |
| `LeanFollower.ATTACK_S` / `RELEASE_S`      | 0.35 / 0.8 s                   | How quickly the lean builds and eases back.                                                                                                  |
| `LeanFollower.REST_S`                      | 1.5 s                          | How long the cursor rests before the lean eases back.                                                                                        |
| `FlickerEvents.GAP_FLOOR_S` / `GAP_MEAN_S` | 75 + exp(120) s, capped at 300 | Spacing of the rare edge flicker. Call `forceNext()` from a breakpoint to see one while tuning.                                              |
| `FlickerEvents.FLUTTER_HZ` / `SWING`       | 1 Hz / 0.15                    | Soft pulses inside a flicker. A test holds `FLUTTER_HZ` at or below 1.2, well under the three-flashes-a-second limit.                        |
| `FLICKER_STRIPS`                           | 0.04–0.22, 0.78–0.96           | Where a flicker may land while the art is shown, so it isn't hidden behind the art.                                                          |

## Look — `borealisSpec.ts`

The GLSL constants are at the top of `FRAG`:

- `ALPHA` (0.45): peak curtain opacity. It leaves headroom for the surge, so keep it at or below about 0.5.
- `SURGE` (0.55) and `FLICKER` (0.6): brightness gains for the two moments.
- `LEAN` (0.06 pane heights) and `LEAN_R` (0.32): how far the curtains lean from the cursor, and over what radius.
- `WAVE` (0.075): how much the lower edges undulate. `EDGE_W` and `RAY_H` set the edge glow's thickness and how high the rays reach.
- `FLOW_R` (1.6): radius of the flow circles in noise space. Larger means more change over one loop.

Curtain heights are in the loop: `0.50 + 0.10 * i` with the art shown, so the curtains sit above the title and the side strips carry them, and `0.24 + 0.15 * i` when they have the pane to themselves.

On a light `--bg`, the curtains use a deeper tone of the palette at 75% opacity. A lightened tone over a light background reads as grey haze. Behind the title text, the composited aurora is pulled 80% back toward `--bg`, so every moment is bounded at once.

## Cost

`resScale` is 0.25 and the frame cap is 30 fps. Each pixel does 15 value-noise lookups (five per curtain). If GPU busy is over target, try `__kampGL.tune({ fps: 20 })` first. The curtains are soft, so `scale` can go lower than Halation's.

Measured on the Intel Mac on 2026-11-11 (n = 60 each, 60 Hz, music playing):

| Variant              | GPU busy      | GPU active    |
| -------------------- | ------------- | ------------- |
| None                 | 3.43% ± 2.16  | 4.13% ± 2.23  |
| Borealis, art hidden | 23.55% ± 0.66 | 25.83% ± 0.73 |
| Borealis, art shown  | 24.78% ± 1.35 | 26.98% ± 1.84 |

That is about 20 points over None, just over the ~20% target, and was accepted as is. Art shown and hidden cost the same, as expected, because the shader draws every pixel either way. The art-shown run's package power (24.9 W against 8.6 W) came from unrelated system activity. Read GPU busy, not package power (KAMP-776).

No surge variant was measured. A surge lasts about 4 s, too short for a 60 s run, and adds only a couple of exp() calls per pixel while a band is showing.

To re-measure, run these variants as described above:

1. None
2. Borealis with the art visible
3. Borealis with the art hidden

---

# Tannhäuser (KAMP-772)

Condensation on a rainy-night window, drawn by a WebGL shader on the shared base (`webgl/README.md`). The model lives in `src/shared/tannhauserTiming.ts` and is covered by `npm test`. The shader and its uniforms are in `tannhauser/tannhauserSpec.ts`.

The fog density is a byte mask (`FogMask`, 256 texels high, width by aspect), uploaded as a texture whenever it changes. The cursor wipes it, and it refills linearly. A fully fogged mask with the cursor still uploads nothing.

## Timing — `tannhauserTiming.ts`

| Knob                                         | Default                              | Effect                                                                                                                  |
| -------------------------------------------- | ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `FogMask.REFILL_S`                           | 10 s                                 | How long a wiped patch takes to mist over again. The refill is linear, so a doodle fades evenly.                        |
| `MASK_H` / `MIN_FALLOFF`                     | 256 / 2 texels                       | Mask resolution and the narrowest soft edge. Any narrower trail is clamped up, so lower MASK_H and trails stop scaling. |
| `BreathEnvelope.GROW_S` / `FADE_S`           | 1.8 / 2.5 s                          | The track-change fog: the front's spread from the centre, then how fast the fresh veil thins and older wipes re-emerge. |
| `BreathEnvelope.RISE_S`                      | 0.6 s                                | A skip while the breath fades raises it back from where it is over this long. A skip while it grows changes nothing.    |
| `SliderField.MAX`                            | 6                                    | Running drops at once, plus one slot for the heavy drop. Raise it for a busier window; it nears Wintermute above ~10.   |
| `SliderField.SPAWN_FLOOR_S` / `SPAWN_MEAN_S` | 0.8 + exp(2) s                       | How often a new drop forms. With the run lengths below, this keeps about 3–5 on the move.                               |
| `SliderField.STUCK_S` / `SLIP_S`             | 0.4–2.2 s / 0.25–0.9 s               | The stick-slip rhythm: how long a drop clings, then how long it runs. Longer slips read as streaming, not condensation. |
| `SliderField.V_BASE` / `V_PER_R`             | 0.08 + 4·r pane heights/s            | Slip speed. Heavier drops run faster. `V_CAP` (0.3) bounds every step.                                                  |
| `SliderField.GROW`                           | 0.008                                | Radius gained per pane height run, from the beads it swallows. A running drop never fades; it leaves off the bottom.    |
| `SliderField.TRAIL_START` / `TAPER_LEN`      | 0.3 / 0.1                            | A trail starts at this fraction of its full width and widens over this many pane heights, so it never reads as a tube.  |
| `SliderField.TRAIL_FLOOR`                    | 60 / 255                             | Fog left in the middle of a trail. Higher reads as a damp streak; 0 wipes it dry and the trail looks cut out.           |
| `SliderField.MERGE_K` / `MERGE_TAU`          | 0.8 / 0.08 s                         | How close two drops get before merging, and how fast the smaller is drawn into the larger. The larger keeps their area. |
| `SliderField.HEAVY_GAP_*` / `HEAVY_IDLE_S`   | 60 + exp(100) s, capped at 240 / 6 s | The rare heavy drop: bigger, longer runs, only once the cursor has been still. Call `forceHeavy()` to see one.          |
| `SliderField.REST_MAX` / `REST_R`            | 10 / 0.005–0.011                     | Resting drops on the glass, and their size when they form. They are what a run visibly collides with.                   |
| `SliderField.REST_GROW` / `R_BREAK`          | 0.00015/s / 0.016                    | How fast a resting drop swells, and the size at which it breaks loose and runs (about a minute from forming).           |
| `SliderField.CAPTURE_K` / `CAPTURE_AHEAD`    | 2.5 / 0.2                            | How far to the side (in summed radii) and how far below a run is drawn toward a drop. Higher means more merges.         |
| `SliderField.LEAN`                           | 0.5                                  | Sideways lean per pane height run. Higher swerves harder into a neighbour; it is also what keeps x from jumping.        |
| `DROP_PERIODS_S`                             | 29 s                                 | Life cycle of the fine beads. The second period (47 s) belonged to the removed hashed coarse beads and is unused.       |

## Look — `tannhauserSpec.ts`

The TypeScript constants are:

- `WIPE_R` (0.075 pane heights): the cursor's wipe radius.
- `DRIFT_PERIODS_S` (131 / 197 s): how slowly the lights behind the glass drift.

The trail width is `SliderField.TRAIL_R` in the model: the trail stamp's radius is 1.4 times the drop's. The stamp is a soft cone, so that clears about 70% of the drop's width and fades out at the edges.

The GLSL constants are at the top of `FRAG`:

- `FINE_CELL` / `COARSE_CELL` (0.03 / 0.075 pane heights), `*_DENSITY` and `*_R`: bead spacing, how many cells hold a bead, and the largest bead as a fraction of its cell. Each row of cells is shifted by its own hash, and a bead's jitter scales with its size, so small beads roam the whole cell and the field never reads as a grid. A bead never crosses its cell, so `*_R` must stay below about 0.35.
- `dropShape()`: the sag. The `0.15` offset sets how low the bulk sits, `0.32` how much the crown tapers, and `1.12` / `0.92` how flat the base and how tall the crown are. Running drops share it, stretched with their speed.
- `REFRACT` (2.2 radii): how far a drop's lens shifts the lights it shows. `LENS` (0.9) is how strongly the lens shows over the glass; it is brightest in the drop's base.
- `MIST` (0.2), `GLOW_FOG` (0.55) and `GLOW_CLEAR` (0.75): the veil's strength, and how much light shows through fogged and through clear glass. The gap between the two `GLOW_` values is what makes a wipe read as clear glass.
- `BREATH_W` (0.18) and `FRESH` (0.12): the soft width of the breath's front, and how much thicker fresh fog looks.
- `TITLE_QUIET` (0.75): how far the scene is pulled back to `--bg` behind the title text.

With the art shown, the five lights gather in the side strips and new drops form there. The shader draws only the fine beads, which are the mist's texture and only form on fog, so a wiped patch or a run's trail is clear glass until it mists over. Every larger drop, resting or running, is in `SliderField`: a drop drawn by the shader alone can't collide with anything, because the model can't see it. A cursor wipe takes the drops under it.

## Cost

`resScale` is 0.33 and the frame cap is 20 fps. Drops need crisper edges than Borealis's curtains. Each pixel evaluates the five lights once, plus one bead cell, the drops near it (17 slots, each rejected by a bounding box) and one texture fetch. The measurements below predate the resting drops, which swapped a hashed bead field for ten more slots. Pixels behind the art are skipped (`GLSL_ART_SKIP`). Running drops keep the mask dirty, so expect a texture upload on most frames.

Measured on the Intel Mac on 2026-11-12 (n = 60 each, 60 Hz, music playing):

| Variant                        | GPU busy      |
| ------------------------------ | ------------- |
| None                           | 3.01% ± 0.74  |
| First build, 0.4, art shown    | 34.87% ± 1.08 |
| 0.4, art shown (art skipped)   | 23.36% ± 0.90 |
| 0.4, art hidden                | 33.28% ± 0.95 |
| 0.4, art hidden, 15 fps        | 24.37% ± 0.64 |
| **0.33, art hidden** (shipped) | 23.58% ± 0.59 |
| 0.1, art hidden (diagnostic)   | 6.28% ± 1.13  |

At 0.33 with the art shown, projected from the above, it is about 17%. Art hidden is a few points over the ~20% target and was accepted: 15 fps looked visibly less smooth, and 0.3 (about 20%) softens the smallest beads.

What the numbers say:

- **The cost is per pixel.** It fell in proportion to pixel count from 0.4 to 0.33 to 0.1. The 0.1 run is only about 3 points over None, so the fog upload (about 100 KB a frame at 16:10) and the rest of the per-frame work cost at most a couple of points.
- **Skipping the art saved about 11 points**, roughly its share of the pane.
- **Sine-free hashes and CPU-placed lights saved nothing measurable.** Cut pixels or frames, not arithmetic.
- **Cursor wiping costs nothing extra**: wiping continuously measured the same as the trickle alone.

To measure, run these variants as described above. `tune()` is lost on a reload, so set it after the page loads and don't refresh before measuring:

1. None
2. Tannhäuser, art shown, cursor outside the pane (the trickle alone)
3. Tannhäuser, art hidden
4. Tannhäuser, wiping continuously
