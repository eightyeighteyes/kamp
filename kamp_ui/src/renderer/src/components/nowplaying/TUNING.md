# Now Playing bokeh — tuning cheat sheet

The ambient glow behind Now Playing (KAMP-561) is built from tunable constants at
the top of two files, plus one CSS line. Nothing here needs a rebuild to try —
Vite hot-reloads edits, so you can slide values with the app running. All knobs
are at conservative midpoints; this is the map for adjusting the character by eye.

Grouped by what you actually *see*.

## Color / mood — `palette.ts`

| Knob | Default | Raise it → |
|------|---------|-----------|
| `SAT_FLOOR` | 0.45 | More vivid, punchier colors (lower → muted / pastel) |
| `LIGHT_MIN` / `LIGHT_MAX` | 0.45 / 0.68 | Brighter glow; widen the gap for more tonal variety |
| `MONO_SAT_THRESHOLD` | 0.12 | More covers judged "grey" and fall back to the theme accent (lower → trusts faint cover colors) |
| `HUE_BINS` | 16 | Finer hue separation — more distinct swatches from busy covers |

## Softness / blur — `assets/track-list.css`, `.now-playing-bokeh`

- `filter: blur(28px)` — **the single biggest aesthetic lever.** Higher = dreamier
  and more diffuse; lower = defined, more distinct "orbs." Useful range ~20–40px.

## Motion feel — `bokehEngine.ts`

| Knob | Default | Effect |
|------|---------|--------|
| `TIERS[*].cross` | 45–150 s | Seconds to cross the screen. **Raise for slower, calmer drift**; lower for livelier. |
| `CROSSFADE_MS` | 1000 | Track-change recolor duration. Raise for a lazier color morph. |
| `ax` / `ay` (in `makeOrb`) | 0.05–0.07 | Wobble amplitude — higher = more meandering, less linear travel. |
| `breathP` | 12–20 s | Breathing period. Depth is set by the `0.08` (scale) and `0.06` (alpha) factors in `draw()`. |
| `FRAME_MS` | 33 (~30 fps) | Frame cap. Raise (~50 → ~20 fps) for an even lazier, lighter feel. |

## Density / size / intensity — `bokehEngine.ts`

| Knob | Default | Effect |
|------|---------|--------|
| `TIERS[*].size` | hero 0.55–0.75, mid 0.30–0.45, accent 0.12–0.22 | Orb diameter as a fraction of the short screen edge. Bigger hero orbs = more color wash. |
| `TIERS[*].alpha` | 0.32–0.56 | Per-orb opacity. **Lower the whole set to let the art pop more**; raise for a bolder background. |
| `ORB_PLAN` | 7 entries | Orb *count* and which palette slot each orb wears. Add/remove rows to change density. |

## Focus on the art (vignette) — `bokehEngine.ts`, `drawVignette()`

- Inner radius `r * 0.45` and outer stop alpha `0.35` control the "clearing" behind
  the cover. Raise `0.35` to darken the edges harder (art stands out more); lower it
  to let orbs reach the corners.

## Performance — `bokehEngine.ts` (only if it ever feels heavy)

- `RES_SCALE` (0.5) — backing-store resolution as a fraction of clamped DPR. Drop to
  0.4 for cheaper and blurrier.
- `MAX_DPR` (2) — caps the device-pixel-ratio the backing store honors.
- The loop already pauses when the pane is hidden, the window is occluded, the toggle
  is off, or reduced-motion is set — so idle cost is zero in those states.

## Fastest path

Start with three knobs — they change the character the most:

1. CSS `blur` (`.now-playing-bokeh`)
2. `TIERS[*].cross` (drift speed)
3. `TIERS[*].alpha` (how loud vs. how much the art pops)

## Deferred ideas (filed as follow-up tickets)

Cursor parallax, occasional "firefly" motes, a track-change "bloom" pulse, per-theme
palette tinting, and a dev-only swatch overlay to visualize the extracted palette
while tuning. See the KAMP tickets linked from KAMP-561.
